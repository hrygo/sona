"""Shared subtitle/meeting adapter for SpeechRail OpenAI Realtime ASR.

Consumes SpeechRail ``WS /v1/realtime`` transcription events and projects them
onto the neutral :class:`ASREvent` / :class:`ASRWindow` domain contract.  The
meeting path enables the session-scoped ``diarization`` profile (via
``session.update``) and rewrites the anonymous ``spk_*`` labels into the
application's stable ``group:{id}`` speaker namespace so downstream mapping/
rename semantics are preserved.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace

from sona.asr.contracts import ASRCapabilities, ASREvent, ASRSessionContext
from sona.asr.diagnostics import ASRDiagnostics
from sona.asr.models import (
    ASRAttributionUnitSpan,
    ASRCompletedItem,
    ASRSegment,
    ASRWindow,
)
from sona.speechrail.transcription_events import (
    DiarizationFinalizedEvent,
    DiarizationStatusEvent,
    DiarizationUpdateEvent,
    Noop,
    SpeechRailTranscriptionError,
    TranscriptionCompleted,
    TranscriptionDelta,
    TranscriptionSegment,
    decode_transcription_event,
)
from sona.speechrail.transport import (
    DEFAULT_SERVER_VAD,
    DEFAULT_SERVER_VAD_EXTENSIONS,
    MEETING_SERVER_VAD,
    MEETING_SERVER_VAD_EXTENSIONS,
    ConnectionFactory,
    SpeechRailProtocolError,
    SpeechRailRealtimeClient,
)

__all__ = ["ConnectionFactory", "SpeechRailRealtimeClient", "SpeechRailStreamingTranscriber"]

_BYTES_PER_MS = 32_000 / 1_000  # 16 kHz mono s16le bytes per millisecond
_SAMPLES_PER_MS = 16  # 16 kHz：1 ms = 16 samples（样本域换算保持整数精确）

# SPK-E2E-1 扩展模式的无归属保留 key：不拼接 group+label 当持久身份。
UNKNOWN_SPEAKER_KEY = "unknown"

# 扩展模式逐连接跟踪的归属单元状态上限（协议要求的有界缓存）。
_MAX_TRACKED_UNITS = 8_192


class SpeechRailStreamingTranscriber:
    backend_id = "speechrail-openai-realtime"
    capabilities = ASRCapabilities(
        languages=frozenset({"Chinese", "English", "zh", "en"}),
        supports_partial=True,
        supports_segment_timestamps=False,
        supports_word_timestamps=False,
        supports_hotwords=False,
        supports_speaker_labels=True,
        supports_native_diarization=True,
        supports_eof_flush=True,
    )

    def __init__(
        self,
        *,
        client: SpeechRailRealtimeClient,
        context: ASRSessionContext,
        language: str,
        finish_timeout_secs: float = 10.0,
        diarization_extensions: bool = False,
        diagnostics: ASRDiagnostics | None = None,
        turn_detection: Mapping[str, object] | None = None,
    ) -> None:
        if finish_timeout_secs <= 0:
            raise ValueError("finish_timeout_secs must be positive")
        self._client = client
        self._context = context
        self._language = language
        self._finish_timeout_secs = finish_timeout_secs
        self._ready = False
        self._turn_detection = dict(turn_detection) if turn_detection is not None else None
        self._last_window = ASRWindow(source_epoch=context.source_epoch)
        self._last_confirmed_window = self._last_window
        self._pending_segments: list[ASRSegment] = []
        self._partial_text = ""
        self._partial_item_id: str | None = None
        self._active_item_id: str | None = None
        self._active_item_start_ms: int | None = None
        self._active_item_end_ms: int | None = None
        self._active_item_offset_ms: int | None = None
        self._last_audio_boundary_ms = 0
        self._audio_ms = 0
        self._last_confirmed_end_ms: int | None = None
        self._final_ready = asyncio.Event()
        self._finish_lock = asyncio.Lock()
        self._finish_requested = False
        self._commit_sent = False
        self._clear_sent = False
        self._events_active = False
        self._terminal_error: tuple[str, str] | None = None
        self._diarization_requested = context.purpose == "meeting"
        self._extensions_requested = bool(diarization_extensions)
        self._extensions_negotiated = False
        # 扩展模式协议状态：归属单元修订连续性与分人健康（停止分人保留正文）。
        self._unit_updates: dict[str, tuple[int, tuple[object, ...], int]] = {}
        self._diarization_broken = False
        self._diarization_degraded = False
        self._diarization_finalized: DiarizationFinalizedEvent | None = None
        self._finalize_sent = False
        self._finalized_ready = asyncio.Event()
        self._diagnostics = diagnostics if diagnostics is not None else ASRDiagnostics()

    @property
    def uri(self) -> str:
        return self._client.uri

    @property
    def diagnostics(self) -> ASRDiagnostics:
        """返回当前连接的有限计数诊断，不包含音频或转录内容。"""
        return self._diagnostics

    async def connect(self) -> None:
        await self._client.connect(
            language=self._language,
            diarization=self._diarization_requested,
            speaker_count_hint=self._context.speaker_count_hint,
            diarization_group_id=self._context.diarization_group_id,
            turn_detection=self._turn_detection_config(),
            diarization_extensions=self._extensions_requested,
        )
        self._extensions_negotiated = (
            self._extensions_requested and self._client.diarization_contract is not None
        )
        self._ready = True

    def _turn_detection_config(self) -> dict[str, object]:
        if self._turn_detection is not None:
            return dict(self._turn_detection)
        if self._context.purpose == "meeting":
            return (
                MEETING_SERVER_VAD_EXTENSIONS
                if self._extensions_requested
                else MEETING_SERVER_VAD
            )
        return (
            DEFAULT_SERVER_VAD_EXTENSIONS
            if self._extensions_requested
            else DEFAULT_SERVER_VAD
        )

    @property
    def session_id(self) -> str:
        """当前连接的 session id；未连接时为空串（分人修订依赖它）。"""
        return self._client.session_id or ""

    @property
    def extensions_negotiated(self) -> bool:
        """是否成功协商 SPK-E2E-1 扩展（capability 缺失时回退 legacy）。"""
        return self._extensions_negotiated

    @property
    def diarization_finalized(self) -> DiarizationFinalizedEvent | None:
        """最近一次接收的 ``speechrail.diarization.finalized``（S3 EOF 屏障消费）。"""
        return self._diarization_finalized

    async def send_audio(self, chunk: bytes) -> None:
        await self._client.append_pcm(chunk)
        self._audio_ms += int(len(chunk) / _BYTES_PER_MS)
        self._diagnostics.record_audio_samples(len(chunk) // 2)

    async def events(self) -> AsyncIterator[ASREvent]:
        if self._events_active:
            raise RuntimeError("SPEECHRAIL_EVENTS_ALREADY_CONSUMED")
        self._events_active = True
        if self._ready:
            self._ready = False
            yield ASREvent(kind="ready")
        try:
            while True:
                event = await self._client.receive()
                try:
                    decoded = decode_transcription_event(
                        event, diarization_extensions=self._extensions_negotiated
                    )
                except SpeechRailProtocolError:
                    self._diagnostics.record_protocol_error()
                    yield self._set_terminal_error(*_terminal_error_for(event))
                    return
                if isinstance(decoded, Noop):
                    if decoded.reason == "input_audio_buffer.cleared":
                        if self._finish_requested:
                            # SpeechRail handles client events serially.  The
                            # clear acknowledgement therefore arrives after
                            # the commit's complete/segment frames and is the
                            # EOF barrier; no session.completed event exists.
                            self._final_ready.set()
                            return
                        continue
                    if decoded.reason == "input_audio_buffer.committed":
                        self._diagnostics.record_committed()
                        continue
                    if decoded.reason == "input_audio_buffer.speech_started":
                        self._active_item_id = decoded.item_id
                        self._active_item_start_ms = decoded.audio_start_ms
                        self._active_item_end_ms = None
                        # The first SpeechRail item already includes the
                        # session's leading silence.  Later items restart
                        # segment timestamps at the previous commit boundary;
                        # ``audio_start_ms`` is the VAD onset inside that
                        # item, so adding it would double-count the first item
                        # and the inter-item silence.
                        self._active_item_offset_ms = self._last_audio_boundary_ms
                        self._partial_text = ""
                        self._partial_item_id = decoded.item_id
                    elif decoded.reason == "input_audio_buffer.speech_stopped":
                        if decoded.audio_end_ms is not None:
                            self._active_item_end_ms = decoded.audio_end_ms
                            self._last_audio_boundary_ms = decoded.audio_end_ms
                    continue
                if isinstance(decoded, TranscriptionDelta):
                    self._diagnostics.record_partial()
                    if (
                        decoded.item_id is not None
                        and self._partial_item_id is not None
                        and decoded.item_id != self._partial_item_id
                    ):
                        self._partial_text = ""
                    if decoded.item_id is not None:
                        self._partial_item_id = decoded.item_id
                    self._partial_text += decoded.text
                    self._last_window = ASRWindow(
                        source_epoch=self._context.source_epoch, partial=self._partial_text
                    )
                    yield ASREvent(kind="snapshot", window=self._last_window)
                elif isinstance(decoded, TranscriptionSegment):
                    try:
                        self._pending_segments.append(
                            _segment(
                                decoded,
                                self._context,
                                require_speaker=self._diarization_requested,
                                item_offset_ms=self._segment_offset_ms(decoded.item_id),
                            )
                        )
                    except RuntimeError:
                        yield self._set_terminal_error(
                            "SPEECHRAIL_PROTOCOL_ERROR",
                            "SpeechRail returned an invalid transcription segment",
                        )
                        return
                elif isinstance(decoded, TranscriptionCompleted):
                    if decoded.transcript.strip():
                        self._diagnostics.record_nonempty_completed()
                    else:
                        self._diagnostics.record_empty_completed()
                    if self._extensions_negotiated:
                        # 扩展模式：completed 携带 attribution_units，一个单元一个
                        # 不可变正文段；样本区间换算会议时间，不再叠加 VAD onset/
                        # item offset。不再接收 legacy .segment（解码层拒绝）。
                        self._register_unit_uids(decoded)
                        segments = self._segments_from_units(decoded)
                        completed = self._completed_item(
                            decoded,
                            event_id=str(event.get("event_id") or ""),
                            sequence=_event_sequence(event),
                        )
                        final_window = ASRWindow(
                            source_epoch=self._context.source_epoch,
                            partial="",
                            segments=segments,
                            source_session_id=self._client.session_id,
                            completed_items=(completed,),
                            offset_ms=self._context.offset_ms,
                        )
                        self._last_window = final_window
                        self._last_confirmed_window = final_window
                        if segments:
                            self._last_confirmed_end_ms = max(
                                segment.end_ms for segment in segments
                            )
                        self._partial_text = ""
                        self._partial_item_id = decoded.item_id
                        self._active_item_id = None
                        self._active_item_start_ms = None
                        self._active_item_end_ms = None
                        self._active_item_offset_ms = None
                        yield ASREvent(kind="final", window=final_window)
                        continue
                    # 分人会话也可能收到无 segment 事件的 completed（服务端对短促/
                    # 单人轮次只下发 completed）。此时用兜底单 segment 保留本轮转写；
                    # EOF 空音频 completed 表示没有新增文本，不应污染已确认窗口。
                    try:
                        if self._pending_segments:
                            segments = tuple(self._pending_segments)
                        elif decoded.transcript.strip():
                            segments = (_synthesized_segment(
                                decoded.transcript,
                                self._context,
                                self._audio_ms,
                                speech_start_ms=self._active_item_start_ms,
                                speech_end_ms=self._active_item_end_ms,
                                previous_confirmed_end_ms=self._last_confirmed_end_ms,
                            ),)
                        else:
                            segments = ()
                    except RuntimeError:
                        yield self._set_terminal_error(
                            "SPEECHRAIL_PROTOCOL_ERROR",
                            "SpeechRail returned an invalid completed transcript",
                        )
                        return
                    self._pending_segments.clear()
                    self._partial_text = ""
                    self._partial_item_id = decoded.item_id
                    self._active_item_id = None
                    self._active_item_start_ms = None
                    self._active_item_end_ms = None
                    self._active_item_offset_ms = None
                    final_window = ASRWindow(
                        source_epoch=self._context.source_epoch,
                        partial="",
                        segments=segments,
                    )
                    if segments:
                        self._last_window = final_window
                        self._last_confirmed_window = final_window
                        self._last_confirmed_end_ms = max(
                            segment.end_ms for segment in segments
                        )
                    yield ASREvent(kind="final", window=final_window)
                elif isinstance(decoded, DiarizationUpdateEvent):
                    event_out = self._consume_diarization_update(decoded)
                    if event_out is not None:
                        yield replace(
                            event_out,
                            metadata={
                                **event_out.metadata,
                                "session_id": self._client.session_id,
                                "event_id": str(event.get("event_id") or ""),
                                "sequence": _event_sequence(event),
                            },
                        )
                elif isinstance(decoded, DiarizationStatusEvent):
                    if not self._diarization_broken:
                        self._diarization_degraded = True
                        yield ASREvent(kind="diarization", metadata={"event": decoded})
                elif isinstance(decoded, DiarizationFinalizedEvent):
                    if not self._diarization_broken:
                        self._diarization_finalized = decoded
                        self._finalized_ready.set()
                        yield ASREvent(
                            kind="diarization",
                            metadata={
                                "event": decoded,
                                "session_id": self._client.session_id,
                            },
                        )
                elif isinstance(decoded, SpeechRailTranscriptionError):
                    yield self._set_terminal_error(
                        "SPEECHRAIL_REQUEST_FAILED",
                        "SpeechRail rejected the transcription request",
                    )
                    return
        finally:
            self._events_active = False

    async def finish(self) -> ASRWindow:
        """EOF 屏障：legacy=commit→clear ack；扩展=commit→finalize→等 finalized。

        扩展模式的 clear 延后到 :meth:`release_clear`——必须等上层把
        finalized 对应的归属修订持久化之后才能释放会话（Rail 规格 §5.4.7）。
        """
        async with self._finish_lock:
            if not self._finish_requested:
                self._finish_requested = True
                self._final_ready.clear()
                self._finalized_ready.clear()
            if not self._commit_sent:
                await self._client.commit()
                self._commit_sent = True
            if self._extensions_negotiated:
                if not self._finalize_sent:
                    await self._client.send_diarization_finalize(
                        f"fin-{self._context.source_epoch}"
                    )
                    self._finalize_sent = True
            elif not self._clear_sent:
                await self._client.clear()
                self._clear_sent = True
        if self._extensions_negotiated:
            try:
                await asyncio.wait_for(
                    self._finalized_ready.wait(), timeout=self._finish_timeout_secs
                )
            except TimeoutError:
                raise TimeoutError(
                    "SPEECHRAIL_FINAL_TIMEOUT: diarization finalized was not received"
                ) from None
            if self._terminal_error is not None:
                code, message = self._terminal_error
                raise RuntimeError(f"{code}: {message}")
            return self._last_confirmed_window
        try:
            await asyncio.wait_for(self._final_ready.wait(), timeout=self._finish_timeout_secs)
        except TimeoutError:
            raise TimeoutError("SPEECHRAIL_FINAL_TIMEOUT: final result was not received") from None
        if self._terminal_error is not None:
            code, message = self._terminal_error
            raise RuntimeError(f"{code}: {message}")
        return self._last_confirmed_window

    async def release_clear(self, *, timeout_secs: float | None = None) -> ASRWindow:
        """扩展模式：分人终态已持久化后发送 clear 并等待确认；幂等。"""
        async with self._finish_lock:
            if not self._clear_sent:
                await self._client.clear()
                self._clear_sent = True
        try:
            await asyncio.wait_for(
                self._final_ready.wait(),
                timeout=timeout_secs if timeout_secs is not None else self._finish_timeout_secs,
            )
        except TimeoutError:
            raise TimeoutError("SPEECHRAIL_FINAL_TIMEOUT: clear ack was not received") from None
        if self._terminal_error is not None:
            code, message = self._terminal_error
            raise RuntimeError(f"{code}: {message}")
        return self._last_confirmed_window

    async def close(self) -> None:
        if self._terminal_error is None and not self._final_ready.is_set():
            self._terminal_error = ("SPEECHRAIL_CLOSED", "SpeechRail connection closed")
            self._final_ready.set()
        await self._client.close()

    def _set_terminal_error(self, code: str, message: str) -> ASREvent:
        self._terminal_error = (code, message)
        self._final_ready.set()
        return ASREvent(kind="error", error_code=code, error_message=message)

    # ------------------------------------------------------------------
    # SPK-E2E-1 扩展模式
    # ------------------------------------------------------------------

    def _register_unit_uids(self, completed: TranscriptionCompleted) -> None:
        """登记 completed 交付的归属单元 UID（同连接可被后续修订引用）。"""
        for unit in completed.attribution_units:
            self._unit_updates.setdefault(unit.segment_uid, (0, (), unit.audio_end_sample))

    def _completed_item(
        self, decoded: TranscriptionCompleted, *, event_id: str, sequence: int
    ) -> ASRCompletedItem:
        """把扩展 completed 投影为中立 CompletedItem（session 样本域原样保留）。"""
        assert decoded.audio_start_sample is not None
        assert decoded.audio_end_sample is not None
        return ASRCompletedItem(
            item_id=decoded.item_id or "item-unknown",
            event_id=event_id,
            sequence=sequence,
            audio_start_sample=decoded.audio_start_sample,
            audio_end_sample=decoded.audio_end_sample,
            canonical_text=decoded.transcript,
            units=tuple(
                ASRAttributionUnitSpan(
                    segment_uid=unit.segment_uid,
                    text_start=unit.text_start,
                    text_end=unit.text_end,
                    audio_start_sample=unit.audio_start_sample,
                    audio_end_sample=unit.audio_end_sample,
                    timing_quality=unit.timing_quality,
                )
                for unit in decoded.attribution_units
            ),
        )

    def _segments_from_units(
        self, completed: TranscriptionCompleted
    ) -> tuple[ASRSegment, ...]:
        """把 completed 的归属单元投影为不可变正文段（speaker 初始 unknown）。"""
        transcript = completed.transcript
        segments: list[ASRSegment] = []
        for unit in completed.attribution_units:
            text = transcript[unit.text_start : unit.text_end]
            if not text.strip():
                # 空白单元不产生正文段（说话人修订也无正文可归属）。
                continue
            segments.append(
                ASRSegment(
                    order=0,
                    source_epoch=self._context.source_epoch,
                    speaker_key=UNKNOWN_SPEAKER_KEY,
                    start_ms=self._context.offset_ms + unit.audio_start_sample // _SAMPLES_PER_MS,
                    end_ms=self._context.offset_ms + unit.audio_end_sample // _SAMPLES_PER_MS,
                    text=text,
                    source_uid=unit.segment_uid,
                    timing_quality=unit.timing_quality,
                )
            )
        return tuple(segments)

    def _consume_diarization_update(self, event: DiarizationUpdateEvent) -> ASREvent | None:
        """校验并转发归属修订；协议违例停止分人但保留正文。"""
        if self._diarization_broken:
            return None
        # 稳定水位之前的单元已冻结：丢弃跟踪状态（有界缓存，Rail 不会再修订）。
        for uid in [
            uid
            for uid, (_, _, unit_end) in self._unit_updates.items()
            if unit_end < event.stable_through_sample
        ]:
            del self._unit_updates[uid]
        emitted = False
        for update in event.updates:
            tracked = self._unit_updates.get(update.segment_uid)
            if tracked is None:
                # 更新未知 UID：协议错误——停止分人，保留正文。
                self._diarization_broken = True
                return None
            last_revision, last_content, unit_end = tracked
            content = _update_content(update)
            if update.revision < last_revision:
                # revision 倒退忽略。
                continue
            if update.revision == last_revision:
                if content != last_content:
                    # 同 revision 不同内容：协议冲突。
                    self._diarization_broken = True
                    return None
                # 同 revision 同内容：幂等确认。
                continue
            if update.revision != last_revision + 1:
                # 跳号：协议错误。
                self._diarization_broken = True
                return None
            self._unit_updates[update.segment_uid] = (update.revision, content, unit_end)
            emitted = True
        if not emitted:
            return None
        return ASREvent(kind="diarization", metadata={"event": event})

    def _segment_offset_ms(self, item_id: str | None) -> int:
        """Return the current SpeechRail item start on the session timeline.

        SpeechRail emits a stable input ``item_id`` for every VAD turn, so the
        latest ``speech_started.audio_start_ms`` is the turn discriminator.
        Missing item ids remain accepted for compatibility with older frames.
        """
        if self._active_item_offset_ms is None:
            return 0
        if self._active_item_id is None or item_id is None or item_id == self._active_item_id:
            return self._active_item_offset_ms
        return 0


def _segment(
    value: TranscriptionSegment,
    context: ASRSessionContext,
    *,
    require_speaker: bool,
    item_offset_ms: int = 0,
) -> ASRSegment:
    # 无说话人标注的 segment 落到匿名说话人，不断连（短轮次常见）。
    speaker_key = "0" if value.speaker is None else value.speaker
    return ASRSegment(
        order=0,
        source_epoch=context.source_epoch,
        speaker_key=_speaker_key(context, speaker_key),
        start_ms=value.start_ms + context.offset_ms + item_offset_ms,
        end_ms=value.end_ms + context.offset_ms + item_offset_ms,
        text=value.text,
    )


def _synthesized_segment(
    transcript: str,
    context: ASRSessionContext,
    audio_ms: int,
    *,
    speech_start_ms: int | None = None,
    speech_end_ms: int | None = None,
    previous_confirmed_end_ms: int | None = None,
) -> ASRSegment:
    """Create a fallback segment when SpeechRail emits no segment events.

    VAD boundaries are authoritative when both are available. If they are
    absent, use a bounded nominal duration and place the segment after the
    latest confirmed end so a short/empty audio counter cannot overwrite it.
    """
    segment = transcript.strip()
    if not segment:
        raise RuntimeError("SPEECHRAIL_PROTOCOL_ERROR")
    nominal_ms = _nominal_duration_ms(segment)
    previous_end_ms = (
        max(0, previous_confirmed_end_ms - context.offset_ms)
        if previous_confirmed_end_ms is not None
        else None
    )
    has_vad_bounds = speech_start_ms is not None and speech_end_ms is not None
    if speech_start_ms is not None and speech_end_ms is not None:
        start_ms = speech_start_ms
        end_ms = max(speech_start_ms, speech_end_ms)
    elif speech_start_ms is not None:
        start_ms = speech_start_ms
        end_ms = speech_start_ms + nominal_ms
    elif speech_end_ms is not None:
        end_ms = speech_end_ms
        start_ms = max(0, speech_end_ms - nominal_ms)
    else:
        end_ms = max(audio_ms, nominal_ms)
        start_ms = max(0, end_ms - nominal_ms)
    if not has_vad_bounds and previous_end_ms is not None and start_ms < previous_end_ms:
        start_ms = previous_end_ms
        end_ms = max(end_ms, start_ms + nominal_ms)
    return ASRSegment(
        order=0,
        source_epoch=context.source_epoch,
        speaker_key=_speaker_key(context, "0"),
        start_ms=context.offset_ms + start_ms,
        end_ms=context.offset_ms + end_ms,
        text=segment,
    )


def _nominal_duration_ms(text: str) -> int:
    """Best-effort duration for a fallback subtitle segment."""
    return int(min(max(len(text) * 120, 400), 10_000))


def _speaker_key(context: ASRSessionContext, speaker: str) -> str:
    if context.purpose == "meeting" and context.diarization_group_id is not None:
        return f"group:{context.diarization_group_id}:speaker:{speaker}"
    return f"epoch:{context.source_epoch}:speaker:{speaker}"


def _event_sequence(event: dict[str, object]) -> int:
    """顶层 sequence（transport 已校验为严格递增 int）。"""
    value = event.get("sequence")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _update_content(update: object) -> tuple[object, ...]:
    """一个归属修订的内容指纹（同 revision 幂等/冲突判定用）。"""
    status = getattr(update, "status", None)
    speaker = getattr(update, "speaker", None)
    coverage = getattr(update, "coverage_ratio", None)
    overlap = getattr(update, "overlap_ratio", None)
    candidates = tuple(
        (candidate.speaker, candidate.support_ratio)
        for candidate in getattr(update, "candidates", ())
    )
    return (status, speaker, coverage, overlap, candidates)


def _terminal_error_for(event: dict[str, object]) -> tuple[str, str]:
    event_type = event.get("type")
    if event_type == "conversation.item.input_audio_transcription.delta":
        return "SPEECHRAIL_PROTOCOL_ERROR", "SpeechRail returned a transcription delta without text"
    if event_type == "conversation.item.input_audio_transcription.completed":
        if event.get("attribution_units") is not None or "audio_start_sample" in event:
            return (
                "SPEECHRAIL_PROTOCOL_ERROR",
                "SpeechRail returned an invalid attributed completed transcript",
            )
        return "SPEECHRAIL_PROTOCOL_ERROR", "SpeechRail returned an invalid completed transcript"
    if event_type == "conversation.item.input_audio_transcription.segment":
        return (
            "SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR",
            "SpeechRail returned an invalid transcription segment",
        )
    if event_type == "speechrail.diarization.update":
        return (
            "SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR",
            "SpeechRail returned an invalid diarization update",
        )
    if event_type == "speechrail.diarization.status":
        return (
            "SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR",
            "SpeechRail returned an invalid diarization status",
        )
    if event_type == "speechrail.diarization.finalized":
        return (
            "SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR",
            "SpeechRail returned an invalid diarization finalized event",
        )
    return "SPEECHRAIL_PROTOCOL_ERROR", "SpeechRail returned an invalid transcription event"
