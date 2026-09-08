"""Shared subtitle/meeting adapter for SpeechRail OpenAI Realtime ASR."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Literal
from uuid import uuid4

from sona.asr.contracts import ASRCapabilities, ASREvent, ASRSessionContext
from sona.asr.diagnostics import ASRDiagnostics
from sona.asr.models import (
    ASRAttributionUnitSpan,
    ASRCompletedItem,
    ASRSegment,
    ASRWindow,
)
from sona.speechrail.transcription_events import (
    DiarizationDoneEvent,
    DiarizationStatusEvent,
    DiarizationUpdate,
    DiarizationUpdatedEvent,
    Noop,
    SpeechRailTranscriptionError,
    TranscriptionCompleted,
    TranscriptionDelta,
    TranscriptionSegment,
    decode_transcription_event,
)
from sona.speechrail.transport import (
    DEFAULT_SERVER_VAD,
    MEETING_SERVER_VAD,
    ConnectionFactory,
    SpeechRailProtocolError,
    SpeechRailRealtimeClient,
)

logger = logging.getLogger(__name__)

__all__ = ["ConnectionFactory", "SpeechRailRealtimeClient", "SpeechRailStreamingTranscriber"]

_BYTES_PER_MS = 32
_SAMPLES_PER_MS = 16
UNKNOWN_SPEAKER_KEY = "unknown"
DIARIZATION_DISPLAY_STATUS = Literal["off", "active", "degraded"]
_MAX_TRACKED_UNITS = 8_192
_MAX_TRACKED_ITEM_IDS = 64


class SpeechRailStreamingTranscriber:
    """Project one SpeechRail session into the neutral ASR contract."""

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
        diagnostics: ASRDiagnostics | None = None,
        turn_detection: Mapping[str, object] | None = None,
    ) -> None:
        if finish_timeout_secs <= 0:
            raise ValueError("finish_timeout_secs must be positive")
        self._client = client
        self._context = context
        self._language = language
        self._finish_timeout_secs = finish_timeout_secs
        self._turn_detection = dict(turn_detection) if turn_detection is not None else None
        self._ready = False
        self._events_active = False
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
        self._finish_lock = asyncio.Lock()
        self._finish_requested = False
        self._commit_sent = False
        self._clear_sent = False
        self._finish_sent = False
        self._finish_event_id = f"sona-finish-{uuid4().hex}"
        self._final_ready = asyncio.Event()
        self._done_ready = asyncio.Event()
        self._terminal_error: tuple[str, str] | None = None
        self._diarization_requested = context.diarization_enabled
        self._diarization_enabled = False
        self._diarization_unavailable_reason: str | None = None
        self._diarization_degraded_reason: str | None = None
        self._diarization_done: DiarizationDoneEvent | None = None
        self._diarization_done_received = False
        self._completed_item_ids: list[str] = []
        self._unit_updates: dict[str, tuple[int, tuple[object, ...], int]] = {}
        self._last_update_sequence = 0
        self._diagnostics = diagnostics if diagnostics is not None else ASRDiagnostics()

    @property
    def uri(self) -> str:
        return self._client.uri

    @property
    def diagnostics(self) -> ASRDiagnostics:
        return self._diagnostics

    @property
    def session_id(self) -> str:
        return self._client.session_id or ""

    @property
    def diarization_enabled(self) -> bool:
        return self._diarization_enabled

    @property
    def diarization_requested(self) -> bool:
        return self._diarization_requested

    @property
    def diarization_unavailable_reason(self) -> str | None:
        return self._diarization_unavailable_reason

    @property
    def diarization_degraded_reason(self) -> str | None:
        return self._diarization_degraded_reason

    @property
    def diarization_done(self) -> DiarizationDoneEvent | None:
        return self._diarization_done

    @property
    def diarization_status(self) -> DIARIZATION_DISPLAY_STATUS:
        if self._diarization_degraded_reason or self._diarization_unavailable_reason:
            return "degraded"
        return "active" if self._diarization_enabled else "off"

    async def connect(self) -> None:
        await self._client.connect(
            language=self._language,
            turn_detection=self._turn_detection_config(),
            diarization_enabled=self._diarization_requested,
        )
        self._diarization_enabled = self._client.diarization_enabled
        self._diarization_unavailable_reason = self._client.diarization_unavailable_reason
        self._ready = True

    def _turn_detection_config(self) -> dict[str, object]:
        if self._turn_detection is not None:
            return dict(self._turn_detection)
        config = MEETING_SERVER_VAD if self._context.purpose == "meeting" else DEFAULT_SERVER_VAD
        return dict(config)

    async def send_audio(self, chunk: bytes) -> None:
        await self._client.append_pcm(chunk)
        self._audio_ms += len(chunk) // _BYTES_PER_MS
        self._diagnostics.record_audio_samples(len(chunk) // 2)

    async def events(self) -> AsyncIterator[ASREvent]:
        if self._events_active:
            raise RuntimeError("SPEECHRAIL_EVENTS_ALREADY_CONSUMED")
        self._events_active = True
        if self._ready:
            self._ready = False
            ready_metadata: dict[str, object] = {
                "diarization_status": self.diarization_status,
            }
            if self._diarization_unavailable_reason:
                ready_metadata["diarization_reason"] = self._diarization_unavailable_reason
            yield ASREvent(kind="ready", metadata=ready_metadata)
        try:
            while True:
                event = await self._client.receive()
                try:
                    decoded = decode_transcription_event(
                        event, diarization_enabled=self._diarization_enabled
                    )
                except SpeechRailProtocolError:
                    self._diagnostics.record_protocol_error()
                    if self._diarization_enabled and _is_diarization_related(event):
                        yield self._mark_diarization_degraded("protocol_error")
                        continue
                    yield self._set_terminal_error(*_terminal_error_for(event))
                    return

                if isinstance(decoded, Noop):
                    if (
                        decoded.reason == "input_audio_buffer.cleared"
                        and self._finish_requested
                        and not self._diarization_enabled
                    ):
                        self._final_ready.set()
                        return
                    elif decoded.reason == "input_audio_buffer.committed":
                        self._diagnostics.record_committed()
                    elif decoded.reason == "input_audio_buffer.speech_started":
                        self._active_item_id = decoded.item_id
                        self._active_item_start_ms = decoded.audio_start_ms
                        self._active_item_end_ms = None
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
                        source_epoch=self._context.source_epoch,
                        partial=self._partial_text,
                        diarization_status=self.diarization_status,
                        diarization_reason=self._diarization_reason(),
                    )
                    yield ASREvent(kind="snapshot", window=self._last_window)
                    continue

                if isinstance(decoded, TranscriptionSegment):
                    try:
                        self._pending_segments.append(
                            _segment(
                                decoded,
                                self._context,
                                item_offset_ms=self._segment_offset_ms(decoded.item_id),
                            )
                        )
                    except RuntimeError:
                        yield self._set_terminal_error(
                            "SPEECHRAIL_PROTOCOL_ERROR",
                            "SpeechRail returned an invalid transcription segment",
                        )
                        return
                    continue

                if isinstance(decoded, TranscriptionCompleted):
                    if decoded.transcript.strip():
                        self._diagnostics.record_nonempty_completed()
                    else:
                        self._diagnostics.record_empty_completed()
                    if self._diarization_enabled:
                        try:
                            final_window = self._extension_completed_window(decoded)
                        except RuntimeError:
                            self._diagnostics.record_protocol_error()
                            yield self._mark_diarization_degraded("protocol_error")
                            continue
                    else:
                        final_window = self._plain_completed_window(decoded)
                    yield ASREvent(kind="final", window=final_window)
                    continue

                if isinstance(decoded, DiarizationUpdatedEvent):
                    if self._diarization_done_received:
                        yield self._mark_diarization_degraded("update_after_done")
                        continue
                    if decoded.session_id != self.session_id:
                        yield self._mark_diarization_degraded("session_mismatch")
                        continue
                    valid_update, changed = self._consume_diarization_update(decoded)
                    if not valid_update:
                        yield self._mark_diarization_degraded(
                            self._diarization_degraded_reason or "protocol_error"
                        )
                        continue
                    self._last_update_sequence = max(
                        self._last_update_sequence, decoded.sequence
                    )
                    if changed:
                        yield ASREvent(kind="diarization", metadata={"event": decoded})
                    continue

                if isinstance(decoded, DiarizationStatusEvent):
                    if self._diarization_done_received:
                        yield self._mark_diarization_degraded("status_after_done")
                        continue
                    if decoded.session_id != self.session_id:
                        yield self._mark_diarization_degraded("session_mismatch")
                        continue
                    yield self._mark_diarization_degraded(decoded.reason, event=decoded)
                    continue

                if isinstance(decoded, DiarizationDoneEvent):
                    if not self._finish_requested:
                        yield self._mark_diarization_degraded("unexpected_done", event=decoded)
                        continue
                    self._diarization_done_received = True
                    self._diarization_done = decoded
                    self._last_update_sequence = max(
                        self._last_update_sequence, decoded.last_update_sequence
                    )
                    if decoded.session_id != self.session_id:
                        self._done_ready.set()
                        yield self._mark_diarization_degraded(
                            "session_mismatch", event=decoded
                        )
                        continue
                    if decoded.finalization_id != self._finish_event_id:
                        self._done_ready.set()
                        yield self._mark_diarization_degraded(
                            "finalization_id_mismatch", event=decoded
                        )
                        continue
                    if decoded.status == "degraded":
                        self._diarization_degraded_reason = decoded.reason or "degraded"
                    self._done_ready.set()
                    yield ASREvent(kind="diarization", metadata={"event": decoded})
                    continue

                if isinstance(decoded, SpeechRailTranscriptionError):
                    yield self._set_terminal_error(
                        "SPEECHRAIL_REQUEST_FAILED",
                        decoded.message or decoded.code,
                    )
                    return
        finally:
            self._events_active = False

    async def finish(self) -> ASRWindow:
        """Flush committed ASR and, when enabled, wait for the v2 done barrier."""

        async with self._finish_lock:
            if not self._finish_requested:
                self._finish_requested = True
                self._final_ready.clear()
                self._done_ready.clear()
                if not self._commit_sent:
                    await self._client.commit()
                    self._commit_sent = True
                if self._diarization_enabled:
                    if not self._finish_sent:
                        await self._client.send_diarization_finish(self._finish_event_id)
                        self._finish_sent = True
                elif not self._clear_sent:
                    await self._client.clear()
                    self._clear_sent = True
        if self._diarization_enabled:
            try:
                await asyncio.wait_for(self._done_ready.wait(), self._finish_timeout_secs)
            except TimeoutError:
                self._diarization_degraded_reason = "finalization_timeout"
                raise TimeoutError(
                    "SPEECHRAIL_FINAL_TIMEOUT: diarization done was not received"
                ) from None
            if self._diarization_done is not None and (
                self._diarization_done.finalization_id != self._finish_event_id
            ):
                raise RuntimeError("SPEECHRAIL_FINALIZATION_ID_MISMATCH")
            if self._diarization_done is not None and (
                self._diarization_done.session_id != self.session_id
            ):
                raise RuntimeError("SPEECHRAIL_SESSION_ID_MISMATCH")
        else:
            try:
                await asyncio.wait_for(self._final_ready.wait(), self._finish_timeout_secs)
            except TimeoutError:
                raise TimeoutError(
                    "SPEECHRAIL_FINAL_TIMEOUT: final result was not received"
                ) from None
        if self._terminal_error is not None:
            code, message = self._terminal_error
            raise RuntimeError(f"{code}: {message}")
        return self._last_confirmed_window

    async def close(self) -> None:
        if self._terminal_error is None:
            self._final_ready.set()
            self._done_ready.set()
        await self._client.close()

    def _plain_completed_window(self, decoded: TranscriptionCompleted) -> ASRWindow:
        try:
            if self._pending_segments:
                segments = tuple(self._pending_segments)
            elif decoded.transcript.strip():
                segments = (
                    _synthesized_segment(
                        decoded.transcript,
                        self._context,
                        self._audio_ms,
                        speech_start_ms=self._active_item_start_ms,
                        speech_end_ms=self._active_item_end_ms,
                        previous_confirmed_end_ms=self._last_confirmed_end_ms,
                    ),
                )
            else:
                segments = ()
        except RuntimeError:
            raise
        self._pending_segments.clear()
        self._reset_active_item(decoded.item_id)
        final_window = ASRWindow(
            source_epoch=self._context.source_epoch,
            segments=segments,
            diarization_status=self.diarization_status,
            diarization_reason=self._diarization_reason(),
        )
        if segments:
            self._last_window = final_window
            self._last_confirmed_window = final_window
            self._last_confirmed_end_ms = max(segment.end_ms for segment in segments)
        return final_window

    def _extension_completed_window(self, decoded: TranscriptionCompleted) -> ASRWindow:
        item_id = decoded.item_id
        if item_id is None or item_id in self._completed_item_ids:
            raise RuntimeError("SPEECHRAIL_PROTOCOL_ERROR")
        self._completed_item_ids.append(item_id)
        if len(self._completed_item_ids) > _MAX_TRACKED_ITEM_IDS:
            self._completed_item_ids.pop(0)
        if len(self._unit_updates) + len(decoded.attribution_units) > _MAX_TRACKED_UNITS:
            raise RuntimeError("SPEECHRAIL_PROTOCOL_ERROR")
        for unit in decoded.attribution_units:
            if unit.segment_uid in self._unit_updates:
                raise RuntimeError("SPEECHRAIL_PROTOCOL_ERROR")
            self._unit_updates[unit.segment_uid] = (0, (), unit.audio_end_sample)
        segments = self._segments_from_units(decoded)
        completed = self._completed_item(decoded)
        final_window = ASRWindow(
            source_epoch=self._context.source_epoch,
            segments=segments,
            source_session_id=self.session_id,
            completed_items=(completed,),
            offset_ms=self._context.offset_ms,
            diarization_status=self.diarization_status,
            diarization_reason=self._diarization_reason(),
        )
        self._last_window = final_window
        if segments:
            self._last_confirmed_window = final_window
            self._last_confirmed_end_ms = max(segment.end_ms for segment in segments)
        self._reset_active_item(item_id)
        return final_window

    def _reset_active_item(self, item_id: str | None) -> None:
        self._partial_text = ""
        self._partial_item_id = item_id
        self._active_item_id = None
        self._active_item_start_ms = None
        self._active_item_end_ms = None
        self._active_item_offset_ms = None

    def _completed_item(self, decoded: TranscriptionCompleted) -> ASRCompletedItem:
        assert decoded.audio_start_sample is not None
        assert decoded.audio_end_sample is not None
        assert decoded.event_id is not None
        assert decoded.sequence is not None
        return ASRCompletedItem(
            item_id=decoded.item_id or "item-unknown",
            event_id=decoded.event_id,
            sequence=decoded.sequence,
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
        assert completed.audio_start_sample is not None
        segments: list[ASRSegment] = []
        for unit in completed.attribution_units:
            text = completed.transcript[unit.text_start : unit.text_end]
            if not text.strip():
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
                    source_session_id=self.session_id,
                )
            )
        return tuple(segments)

    def _consume_diarization_update(
        self, event: DiarizationUpdatedEvent
    ) -> tuple[bool, bool]:
        if event.stable_through_sample < 0:
            self._diarization_degraded_reason = "protocol_error"
            return False, False
        changed = False
        for update in event.updates:
            tracked = self._unit_updates.get(update.segment_uid)
            if tracked is None:
                self._diarization_degraded_reason = "unknown_segment_uid"
                return False, False
            last_revision, last_content, unit_end = tracked
            content = _update_content(update)
            if update.revision == last_revision:
                if content != last_content:
                    self._diarization_degraded_reason = "revision_conflict"
                    return False, False
                continue
            if update.revision != last_revision + 1:
                self._diarization_degraded_reason = "revision_gap"
                return False, False
            self._unit_updates[update.segment_uid] = (update.revision, content, unit_end)
            changed = True
        for segment_uid, (_, _, unit_end) in tuple(self._unit_updates.items()):
            if unit_end <= event.stable_through_sample:
                del self._unit_updates[segment_uid]
        return bool(event.updates), changed

    def _mark_diarization_degraded(
        self,
        reason: str,
        *,
        event: DiarizationStatusEvent | DiarizationDoneEvent | None = None,
    ) -> ASREvent:
        if self._diarization_degraded_reason is None:
            self._diarization_degraded_reason = reason
        metadata: dict[str, object] = {"status": "degraded", "reason": reason}
        if event is not None:
            metadata["event"] = event
        else:
            metadata["event"] = DiarizationStatusEvent(
                event_id=f"sona-status-{uuid4().hex}",
                session_id=self.session_id or "unknown-session",
                sequence=self._last_update_sequence,
                status="degraded",
                reason=reason,
                since_sample=0,
            )
        return ASREvent(kind="diarization", metadata=metadata)

    def _diarization_reason(self) -> str | None:
        return self._diarization_degraded_reason or self._diarization_unavailable_reason

    def _set_terminal_error(self, code: str, message: str) -> ASREvent:
        self._terminal_error = (code, message)
        self._final_ready.set()
        self._done_ready.set()
        return ASREvent(kind="error", error_code=code, error_message=message)

    def _segment_offset_ms(self, item_id: str | None) -> int:
        if self._active_item_offset_ms is None:
            return 0
        if self._active_item_id is None or item_id is None or item_id == self._active_item_id:
            return self._active_item_offset_ms
        return 0


def _segment(
    value: TranscriptionSegment,
    context: ASRSessionContext,
    *,
    item_offset_ms: int = 0,
) -> ASRSegment:
    speaker_key = value.speaker or "0"
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
    if has_vad_bounds:
        start_ms = speech_start_ms or 0
        end_ms = max(start_ms, speech_end_ms or start_ms)
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
    return int(min(max(len(text) * 120, 400), 10_000))


def _speaker_key(context: ASRSessionContext, speaker: str) -> str:
    if context.diarization_enabled:
        return UNKNOWN_SPEAKER_KEY
    return f"epoch:{context.source_epoch}:speaker:{speaker}"


def _update_content(update: DiarizationUpdate) -> tuple[object, ...]:
    return (
        update.status,
        update.speaker,
        update.coverage_ratio,
        update.overlap_ratio,
        tuple((candidate.speaker, candidate.support_ratio) for candidate in update.candidates),
    )


def _is_diarization_related(event: Mapping[str, object]) -> bool:
    event_type = event.get("type")
    return isinstance(event_type, str) and (
        event_type.startswith("speechrail.diarization.")
        or event_type == "conversation.item.input_audio_transcription.segment"
        or "attribution_units" in event
    )


def _terminal_error_for(event: Mapping[str, object]) -> tuple[str, str]:
    event_type = event.get("type")
    if event_type == "conversation.item.input_audio_transcription.delta":
        return "SPEECHRAIL_PROTOCOL_ERROR", "SpeechRail returned an invalid transcription delta"
    if event_type == "conversation.item.input_audio_transcription.completed":
        return "SPEECHRAIL_PROTOCOL_ERROR", "SpeechRail returned an invalid completed transcript"
    if event_type == "conversation.item.input_audio_transcription.segment":
        return (
            "SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR",
            "SpeechRail returned an invalid transcription segment",
        )
    return "SPEECHRAIL_PROTOCOL_ERROR", "SpeechRail returned an invalid transcription event"
