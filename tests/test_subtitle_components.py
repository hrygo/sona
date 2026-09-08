"""SubtitleProxy 职责拆分前的行为保护测试。

这些测试锁定 façade 当前的可观察行为，供后续组件提取时逐项保持。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path

import pytest

from sona.asr.contracts import ASRCapabilities, ASREvent, ASRSessionContext
from sona.asr.models import ASRSegment, ASRWindow
from sona.asr.presenters import legacy_subtitle_payload
from sona.config import SubtitleSettings
from sona.meeting.models import PCMOwner
from sona.subtitles import (
    FinalizationTimeoutError,
    SubtitleProxy,
    SubtitleProxyState,
    TranscriptionGap,
)


class FakeTranscriber:
    backend_id = "fake"
    capabilities = ASRCapabilities(
        languages=frozenset({"Chinese"}),
        supports_partial=True,
        supports_segment_timestamps=True,
        supports_word_timestamps=True,
        supports_hotwords=False,
        supports_speaker_labels=True,
        supports_native_diarization=False,
        supports_eof_flush=True,
    )

    def __init__(self, *, source_epoch: int) -> None:
        self.source_epoch = source_epoch
        self.closed = False
        self.connected = False
        self.sent_audio: list[bytes] = []
        self.commits = 0
        self._events: asyncio.Queue[ASREvent] = asyncio.Queue()
        self._finish_gate = asyncio.Event()
        self._finish_result: ASRWindow | None = None
        self._end_stream = False
        self._send_gate: asyncio.Event | None = None

    @property
    def uri(self) -> str:
        return "ws://fake/asr"

    async def connect(self) -> None:
        self.connected = True
        self._events.put_nowait(ASREvent(kind="ready"))

    async def send_audio(self, chunk: bytes) -> None:
        self.sent_audio.append(chunk)
        if self._send_gate is not None:
            await self._send_gate.wait()

    async def events(self) -> AsyncIterator[ASREvent]:
        while not self.closed:
            try:
                yield await asyncio.wait_for(self._events.get(), timeout=0.05)
            except TimeoutError:
                if self._end_stream:
                    return

    async def finish(self) -> ASRWindow:
        self.commits += 1
        if self._finish_result is not None:
            return self._finish_result
        await self._finish_gate.wait()
        return self._finish_result or ASRWindow(source_epoch=self.source_epoch)

    async def close(self) -> None:
        self.closed = True

    def emit(self, event: ASREvent) -> None:
        self._events.put_nowait(event)


def _settings(tmp_path: Path) -> SubtitleSettings:
    return SubtitleSettings(
        model_dir=tmp_path,
        output_dir=tmp_path / "subtitles",
    )


class ShortLivedTranscriber(FakeTranscriber):
    """连接成功后立即结束流，模拟 worker 未就绪时的秒断握手。"""

    def __init__(self, *, source_epoch: int) -> None:
        super().__init__(source_epoch=source_epoch)
        self._end_stream = True


class GracefulDiarizationTranscriber(FakeTranscriber):
    """普通字幕 opt-in EOF drain 的最小可观测 fake。"""

    diarization_requested = True
    diarization_enabled = True
    diarization_status = "active"
    diarization_degraded_reason = None
    diarization_unavailable_reason = None

    def __init__(self, *, source_epoch: int) -> None:
        super().__init__(source_epoch=source_epoch)
        self.order: list[str] = []
        self._finish_result = ASRWindow(source_epoch=source_epoch)

    async def send_audio(self, chunk: bytes) -> None:
        await super().send_audio(chunk)
        self.order.append("send")

    async def finish(self) -> ASRWindow:
        self.order.append("finish")
        return await super().finish()


def _flapping_proxy(
    tmp_path: Path,
    *,
    probes: list[bool] | None = None,
    backoff: Sequence[float] = (0.01, 0.02),
    stable_reset_after_secs: float | None = None,
) -> SubtitleProxy:
    created: list[FakeTranscriber] = []

    def factory(_ctx: ASRSessionContext) -> FakeTranscriber:
        transcriber = ShortLivedTranscriber(source_epoch=1 + len(created))
        created.append(transcriber)
        return transcriber

    probe_results = list(probes) if probes is not None else []

    async def probe() -> bool:
        return probe_results.pop(0) if probe_results else True

    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=factory,
        backoff_delays=backoff,
        readiness_probe=probe,
        stable_reset_after_secs=stable_reset_after_secs,
    )
    proxy._created = created  # type: ignore[attr-defined]
    return proxy


def _proxy(tmp_path: Path) -> SubtitleProxy:
    return SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=lambda _ctx: FakeTranscriber(source_epoch=1),
    )


def test_subtitle_proxy_diagnostics_exposes_asr_counters(tmp_path: Path) -> None:
    proxy = _proxy(tmp_path)

    proxy._on_session_reconnect()

    diagnostics = proxy.diagnostics(PCMOwner.NONE)

    assert diagnostics.asr == {
        "sent_samples": 0,
        "partial_events": 0,
        "empty_completed": 0,
        "nonempty_completed": 0,
        "committed_events": 0,
        "reconnects": 1,
        "protocol_errors": 0,
    }


def _window(*, partial: str = "", with_segment: bool = False) -> ASRWindow:
    segments = (
        (
            ASRSegment(
                order=0,
                source_epoch=1,
                speaker_key="epoch:1:speaker:1",
                start_ms=0,
                end_ms=1000,
                text="你好世界",
            ),
        )
        if with_segment
        else ()
    )
    return ASRWindow(source_epoch=1, partial=partial, segments=segments)


def _segment(
    text: str,
    start_ms: int,
    end_ms: int,
    *,
    source_epoch: int = 1,
    speaker: str = "1",
) -> ASRSegment:
    return ASRSegment(
        order=0,
        source_epoch=source_epoch,
        speaker_key=f"epoch:{source_epoch}:speaker:{speaker}",
        start_ms=start_ms,
        end_ms=end_ms,
        text=text,
    )


def _window_with_segments(
    *segments: ASRSegment, partial: str = "", source_epoch: int = 1
) -> ASRWindow:
    return ASRWindow(source_epoch=source_epoch, partial=partial, segments=segments)


async def test_slow_client_only_suffers_its_own_bounded_queue(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    fast_calls: list[str] = []
    blocked = asyncio.Event()

    async def fast_sender(text: str) -> None:
        fast_calls.append(text)

    async def slow_sender(text: str) -> None:
        await blocked.wait()

    proxy.add_client(fast_sender)
    proxy.add_client(slow_sender)
    for index in range(12):
        await proxy._broadcast_untracked({"type": "tick", "i": index})

    await asyncio.sleep(0.05)
    assert len(fast_calls) == 12
    slow_channel = proxy._client_hub._clients[slow_sender]
    assert slow_channel.queue.maxsize == 8
    assert slow_channel.queue.qsize() == 8

    proxy.remove_client(slow_sender)
    await asyncio.sleep(0.02)
    assert slow_sender not in proxy._client_hub._clients
    assert proxy.has_clients
    await proxy.stop()


async def test_late_subscriber_immediately_receives_current_snapshot(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    payload = legacy_subtitle_payload(_window(partial="快照", with_segment=True))
    await proxy._broadcast_payload(payload)

    received: list[str] = []

    async def sender(text: str) -> None:
        received.append(text)

    proxy.add_client(sender)
    await asyncio.sleep(0.05)

    assert len(received) == 1
    assert json.loads(received[0]) == payload
    await proxy.stop()


async def test_standard_subtitles_accumulate_confirmed_history_and_keep_partial(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("第一句", 0, 1000)),
        )
    )
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="snapshot",
            window=ASRWindow(source_epoch=1, partial="第二句"),
        )
    )
    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == ["第一句"]
    assert proxy._last_payload["buffer_transcription"] == "第二句"

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("第二句", 1200, 2200)),
        )
    )
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="snapshot",
            window=ASRWindow(source_epoch=1, partial="第三句"),
        )
    )
    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == [
        "第一句",
        "第二句",
    ]
    assert proxy._last_payload["buffer_transcription"] == "第三句"

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("第三句", 2400, 3400)),
        )
    )
    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == [
        "第一句",
        "第二句",
        "第三句",
    ]
    assert proxy._last_payload["buffer_transcription"] == ""

    current = tmp_path / "subtitles" / "current.srt"
    srt = current.read_text(encoding="utf-8")
    assert all(text in srt for text in ("第一句", "第二句", "第三句"))
    assert srt.count("\n\n") == 2
    await proxy.stop()


async def test_standard_subtitles_replaces_and_deduplicates_revision_window(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()

    original = _segment("原始文本", 0, 1000)
    revised = _segment("修订文本", 200, 1200, speaker="2")
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(kind="final", window=_window_with_segments(original))
    )
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(kind="final", window=_window_with_segments(revised))
    )
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(kind="final", window=_window_with_segments(revised))
    )

    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == ["修订文本"]
    await proxy.stop()


async def test_standard_subtitle_revision_keeps_overlapping_parallel_segments(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(
                _segment("旧一", 0, 1000, speaker="1"),
                _segment("旧二", 0, 1000, speaker="2"),
            ),
        )
    )
    revised_window = _window_with_segments(
        _segment("新一", 0, 1000, speaker="3"),
        _segment("新二", 0, 1000, speaker="3"),
    )
    for _ in range(2):
        await proxy._subtitle_session._handle_stream_event(
            ASREvent(kind="final", window=revised_window)
        )

    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == ["新一", "新二"]
    await proxy.stop()


async def test_standard_subtitle_same_text_at_different_times_stays_distinct(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()

    repeated = "重复发言"
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment(repeated, 0, 500)),
        )
    )
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment(repeated, 600, 1000)),
        )
    )

    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == [repeated, repeated]
    await proxy.stop()


async def test_standard_subtitle_revision_window_can_split_and_merge_segments(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("整句", 0, 1000)),
        )
    )
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(
                _segment("前半", 0, 450),
                _segment("后半", 500, 1000),
            ),
        )
    )
    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == ["前半", "后半"]

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("合并修订", 0, 1000)),
        )
    )
    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == ["合并修订"]
    await proxy.stop()


async def test_standard_subtitle_reconnect_preserves_confirmed_history(
    tmp_path: Path,
) -> None:
    first = FakeTranscriber(source_epoch=1)
    second = FakeTranscriber(source_epoch=2)
    created = [first, second]

    def factory(context: ASRSessionContext) -> FakeTranscriber:
        return created[context.source_epoch - 1]

    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=factory,
        backoff_delays=(0.01,),
    )
    await proxy.start()
    preparation = await proxy.prepare_browser_capture(timeout_secs=1.0)
    proxy.commit_browser_capture(preparation)

    first.emit(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("断线前", 0, 1000)),
        )
    )
    for _ in range(20):
        await asyncio.sleep(0.01)
        if proxy._last_payload is not None and proxy._last_payload.get("lines"):
            break
    first._end_stream = True
    for _ in range(30):
        await asyncio.sleep(0.01)
        if proxy.subtitle_epoch >= 2:
            break

    second.emit(
        ASREvent(
            kind="final",
            window=_window_with_segments(
                _segment("断线后", 1200, 2200, source_epoch=2), source_epoch=2
            ),
        )
    )
    for _ in range(20):
        await asyncio.sleep(0.01)
        if proxy._last_payload is not None and len(proxy._last_payload["lines"]) >= 2:
            break

    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == [
        "断线前",
        "断线后",
    ]
    current = tmp_path / "subtitles" / "current.srt"
    srt = current.read_text(encoding="utf-8")
    assert "断线前" in srt and "断线后" in srt
    await proxy.stop()


async def test_opt_in_subtitle_close_drains_pcm_before_finish(tmp_path: Path) -> None:
    """停止时必须先排空已入队 PCM，再发送且只发送一次 EOF finish。"""
    transcriber = GracefulDiarizationTranscriber(source_epoch=1)
    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=lambda _ctx: transcriber,
    )
    await proxy.start()
    preparation = await proxy.prepare_browser_capture(timeout_secs=1.0)
    proxy.commit_browser_capture(preparation)

    pcm = b"\x00\x00" * 1600
    await proxy.push_audio(pcm)
    await proxy.stop()

    assert transcriber.order == ["send", "finish"]
    assert transcriber.sent_audio == [pcm]
    assert transcriber.commits == 1


async def test_standard_subtitle_reconnect_context_keeps_absolute_audio_time(
    tmp_path: Path,
) -> None:
    contexts: list[ASRSessionContext] = []
    streams: list[FakeTranscriber] = []

    def factory(context: ASRSessionContext) -> FakeTranscriber:
        contexts.append(context)
        stream = FakeTranscriber(source_epoch=context.source_epoch)
        streams.append(stream)
        return stream

    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=factory,
        backoff_delays=(0.1,),
    )
    await proxy.start()
    preparation = await proxy.prepare_browser_capture(timeout_secs=1.0)
    proxy.commit_browser_capture(preparation)

    gap_payloads: list[dict[str, object]] = []

    async def sender(text: str) -> None:
        payload = json.loads(text)
        if payload.get("type") == "gap":
            gap_payloads.append(payload)

    proxy.add_client(sender)
    first = streams[0]
    first.emit(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("断线前", 0, 500)),
        )
    )
    first.emit(
        ASREvent(
            kind="snapshot",
            window=ASRWindow(source_epoch=1, partial="未确认"),
        )
    )
    for _ in range(30):
        await asyncio.sleep(0.01)
        if (
            proxy._last_payload is not None
            and proxy._last_payload.get("buffer_transcription") == "未确认"
        ):
            break

    await proxy.push_audio(b"\x00\x00" * 16_000)
    for _ in range(30):
        await asyncio.sleep(0.01)
        if len(first.sent_audio) == 1:
            break
    assert first.sent_audio == [b"\x00\x00" * 16_000]

    first._end_stream = True
    for _ in range(40):
        await asyncio.sleep(0.01)
        if proxy.state == SubtitleProxyState.BACKOFF:
            break
    assert proxy.state == SubtitleProxyState.BACKOFF

    await proxy.push_audio(b"\x00\x00" * 3_200)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if len(contexts) >= 2:
            break
    assert len(contexts) >= 2
    assert contexts[0].offset_ms == 0
    assert contexts[1].offset_ms == 1_200

    second = streams[1]
    second.emit(
        ASREvent(
            kind="final",
            window=_window_with_segments(
                _segment(
                    "断线后",
                    contexts[1].offset_ms,
                    contexts[1].offset_ms + 500,
                    source_epoch=2,
                ),
                source_epoch=2,
            ),
        )
    )
    for _ in range(30):
        await asyncio.sleep(0.01)
        if proxy._last_payload is not None and len(proxy._last_payload["lines"]) >= 2:
            break

    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == [
        "断线前",
        "断线后",
    ]
    assert proxy._last_payload["buffer_transcription"] == ""
    for _ in range(30):
        await asyncio.sleep(0.01)
        if gap_payloads:
            break
    assert gap_payloads == [{"type": "gap", "dropped_ms": 200}]

    current = tmp_path / "subtitles" / "current.srt"
    srt = current.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:00,500" in srt
    assert "00:00:01,200 --> 00:00:01,700" in srt
    await proxy.stop()


async def test_standard_subtitle_history_resets_on_clear_and_new_epoch(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("旧 epoch", 0, 1000)),
        )
    )
    await proxy.clear_subtitles()
    assert proxy._last_payload is not None
    assert proxy._last_payload["lines"] == []
    assert proxy._last_payload["buffer_transcription"] == ""
    assert proxy._last_payload["diarization"] == {"status": "off", "reason": None}

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(_segment("清空后", 1200, 2200)),
        )
    )
    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == ["清空后"]

    await proxy._subtitle_session._open_epoch()
    await proxy._subtitle_session._handle_stream_event(
        ASREvent(
            kind="final",
            window=_window_with_segments(
                _segment("新 epoch", 0, 1000, source_epoch=2), source_epoch=2
            ),
        )
    )
    assert proxy._last_payload is not None
    assert [line["text"] for line in proxy._last_payload["lines"]] == ["新 epoch"]
    await proxy.stop()


async def test_browser_disconnect_does_not_close_meeting_capture(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    preparation = await proxy.prepare_capture("meeting-1", timeout_secs=1.0)
    proxy.commit_capture(preparation)
    assert proxy.capture_owner == "meeting-1"

    async def sender(text: str) -> None:
        return None

    proxy.add_client(sender)
    proxy.remove_client(sender)

    assert proxy.capture_owner == "meeting-1"
    assert proxy.is_paused is False
    await proxy.abort_capture()
    await proxy.stop()


async def test_browser_and_meeting_preparation_are_mutually_exclusive(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    preparation = await proxy.prepare_capture("meeting-1", timeout_secs=1.0)

    with pytest.raises(RuntimeError, match="会议采集租约"):
        await proxy.prepare_browser_capture(timeout_secs=1.0)

    proxy.commit_capture(preparation)
    with pytest.raises(RuntimeError, match="会议采集租约"):
        await proxy.prepare_browser_capture(timeout_secs=1.0)
    await proxy.abort_capture()

    browser_preparation = await proxy.prepare_browser_capture(timeout_secs=1.0)
    proxy.commit_browser_capture(browser_preparation)
    capture_preparation = await proxy.prepare_capture("meeting-2", timeout_secs=1.0)
    with pytest.raises(RuntimeError, match="普通字幕仍处于活动状态"):
        proxy.commit_capture(capture_preparation)
    await proxy.abort_prepared_capture(capture_preparation)
    await proxy.deactivate_browser_capture()
    await proxy.stop()


async def test_capture_reconnect_increments_epoch_and_reports_gap(
    tmp_path: Path,
) -> None:
    first = FakeTranscriber(source_epoch=1)
    second = FakeTranscriber(source_epoch=2)
    first._send_gate = asyncio.Event()
    created: list[ASRSessionContext] = []

    def factory(context: ASRSessionContext) -> FakeTranscriber:
        created.append(context)
        return first if context.source_epoch == 1 else second

    gaps: list[TranscriptionGap] = []
    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=factory,
        backoff_delays=(0.01,),
    )
    await proxy.start()
    proxy.add_gap_listener(gaps.append)
    preparation = await proxy.prepare_capture("meeting-1", timeout_secs=1.0)
    proxy.commit_capture(preparation)
    first._end_stream = True
    await proxy.push_audio(b"\x00\x00" * 1600)

    await asyncio.sleep(0.4)
    assert proxy.capture_epoch == 2
    assert gaps == [TranscriptionGap(source_epoch=2, start_ms=0, end_ms=100)]
    assert created[1].source_epoch == 2
    await proxy.stop()


async def test_finish_timeout_carries_last_window(tmp_path: Path) -> None:
    transcriber = FakeTranscriber(source_epoch=1)
    proxy = SubtitleProxy(_settings(tmp_path), transcriber_factory=lambda _ctx: transcriber)
    await proxy.start()
    preparation = await proxy.prepare_capture("meeting-1", timeout_secs=1.0)
    proxy.commit_capture(preparation)

    await proxy._capture_session._handle_event(
        ASREvent(kind="snapshot", window=_window(partial="最后一句", with_segment=True))
    )

    with pytest.raises(FinalizationTimeoutError) as excinfo:
        await proxy.finish_capture(timeout_secs=0.05)
    assert excinfo.value.last_window is not None
    assert excinfo.value.last_window.partial == "最后一句"
    await proxy.stop()


async def test_meeting_final_event_notifies_listeners(tmp_path: Path) -> None:
    """会议采集期间每个 server_vad 回合的 final 窗口必须转发监听器增量入库。"""
    transcriber = FakeTranscriber(source_epoch=1)
    proxy = SubtitleProxy(_settings(tmp_path), transcriber_factory=lambda _ctx: transcriber)
    await proxy.start()
    preparation = await proxy.prepare_capture("meeting-1", timeout_secs=1.0)
    proxy.commit_capture(preparation)

    received: list[ASRWindow] = []

    async def listener(window: ASRWindow) -> None:
        received.append(window)

    proxy.add_event_listener(listener)
    await proxy._capture_session._handle_event(
        ASREvent(kind="final", window=_window(partial="", with_segment=True))
    )

    assert len(received) == 1
    assert received[0].segments[0].text == "你好世界"
    await proxy.abort_capture()
    await proxy.stop()


async def test_stop_is_idempotent(tmp_path: Path) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy.stop()
    await proxy.stop()
    await proxy.start()
    await proxy.stop()


async def test_abort_capture_is_idempotent_without_lease(tmp_path: Path) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy.abort_capture()
    await proxy.abort_capture()
    await proxy.stop()


async def test_partial_only_does_not_write_srt(tmp_path: Path) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._broadcast_payload(
        legacy_subtitle_payload(_window(partial="识别中")), persist=True
    )

    assert not (tmp_path / "subtitles" / "current.srt").exists()
    await proxy.stop()


async def test_empty_final_does_not_add_subtitle_line_or_srt(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()

    await proxy._subtitle_session._handle_stream_event(
        ASREvent(kind="final", window=ASRWindow(source_epoch=1))
    )

    assert proxy._last_payload is not None
    assert proxy._last_payload["lines"] == []
    assert not (tmp_path / "subtitles" / "current.srt").exists()
    await proxy.stop()


async def test_duplicate_confirmed_snapshot_does_not_rewrite_srt(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    payload = legacy_subtitle_payload(_window(with_segment=True))
    await proxy._broadcast_payload(payload, persist=True)
    current = tmp_path / "subtitles" / "current.srt"
    first_ns = current.stat().st_mtime_ns

    await proxy._broadcast_payload(payload, persist=True)
    assert current.stat().st_mtime_ns == first_ns
    await proxy.stop()


async def test_srt_persist_uses_atomic_replace(tmp_path: Path) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._broadcast_payload(
        legacy_subtitle_payload(_window(with_segment=True)), persist=True
    )
    current = tmp_path / "subtitles" / "current.srt"

    assert current.is_file()
    assert not (tmp_path / "subtitles" / "current.srt.tmp").exists()
    assert "你好世界" in current.read_text(encoding="utf-8")
    await proxy.stop()


async def test_close_epoch_archives_srt_only_once(tmp_path: Path) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    await proxy._subtitle_session._open_epoch()
    await proxy._broadcast_payload(
        legacy_subtitle_payload(_window(with_segment=True)), persist=True
    )

    await proxy._subtitle_session._close_epoch()
    archives = sorted((tmp_path / "subtitles").glob("session-*.srt"))
    assert len(archives) == 1

    await proxy._subtitle_session._close_epoch()
    assert len(sorted((tmp_path / "subtitles").glob("session-*.srt"))) == 1
    await proxy.stop()


async def test_archive_filename_conflict_uses_numeric_suffix(tmp_path: Path) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    payload = legacy_subtitle_payload(_window(with_segment=True))
    await proxy._subtitle_session._open_epoch()
    await proxy._broadcast_payload(payload, persist=True)
    await proxy._subtitle_session._close_epoch()
    await proxy._subtitle_session._open_epoch()
    await proxy._broadcast_payload(payload, persist=True)
    await proxy._subtitle_session._close_epoch()

    archives = sorted((tmp_path / "subtitles").glob("session-*.srt"))
    assert len(archives) == 2
    assert archives[0].name != archives[1].name
    assert "-2" in archives[1].name
    await proxy.stop()


async def test_epoch_close_broadcasts_reset_with_source_epoch(
    tmp_path: Path,
) -> None:
    proxy = _proxy(tmp_path)
    await proxy.start()
    received: list[str] = []

    async def sender(text: str) -> None:
        received.append(text)

    proxy.add_client(sender)
    await proxy._subtitle_session._open_epoch()
    await proxy._subtitle_session._close_epoch()
    await asyncio.sleep(0.05)

    assert received == [json.dumps({"type": "reset", "source_epoch": 1}, ensure_ascii=False)]
    await proxy.stop()


async def test_push_audio_requires_committed_owner(tmp_path: Path) -> None:
    transcriber = FakeTranscriber(source_epoch=1)
    proxy = SubtitleProxy(_settings(tmp_path), transcriber_factory=lambda _ctx: transcriber)
    await proxy.start()
    await proxy.push_audio(b"\x00\x00" * 1600)

    assert transcriber.sent_audio == []

    preparation = await proxy.prepare_capture("meeting-1", timeout_secs=1.0)
    proxy.commit_capture(preparation)
    await proxy.push_audio(b"\x00\x00" * 1600)
    await asyncio.sleep(0.1)
    assert transcriber.sent_audio == [b"\x00\x00" * 1600]
    await proxy.stop()


async def test_subtitle_reconnect_waits_while_ready_probe_denies(
    tmp_path: Path,
) -> None:
    ready = {"value": False}
    proxy = _flapping_proxy(tmp_path, backoff=(0.01, 0.02))

    async def probe() -> bool:
        return ready["value"]

    proxy._subtitle_session._readiness_probe = probe
    await proxy.start()
    preparation = await proxy.prepare_browser_capture(timeout_secs=1.0)
    proxy.commit_browser_capture(preparation)

    await asyncio.sleep(0.2)
    assert len(proxy._created) == 1

    ready["value"] = True
    await asyncio.sleep(0.15)
    assert len(proxy._created) >= 2
    await proxy.stop()


async def test_subtitle_reconnect_uses_backoff_without_stable_window(
    tmp_path: Path,
) -> None:
    created_at: list[float] = []
    loop = asyncio.get_running_loop()

    def factory(_ctx: ASRSessionContext) -> FakeTranscriber:
        created_at.append(loop.time())
        return ShortLivedTranscriber(source_epoch=1 + len(created_at))

    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=factory,
        backoff_delays=(0.05, 0.2),
    )
    await proxy.start()
    preparation = await proxy.prepare_browser_capture(timeout_secs=1.0)
    proxy.commit_browser_capture(preparation)

    await asyncio.sleep(0.8)
    assert len(created_at) >= 3
    first_gap = created_at[1] - created_at[0]
    second_gap = created_at[2] - created_at[1]
    assert second_gap > first_gap * 1.5
    await proxy.stop()


async def test_stable_subtitle_connection_resets_backoff(tmp_path: Path) -> None:
    created: list[FakeTranscriber] = []

    def factory(_ctx: ASRSessionContext) -> FakeTranscriber:
        transcriber = FakeTranscriber(source_epoch=1 + len(created))
        created.append(transcriber)
        return transcriber

    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=factory,
        backoff_delays=(0.05, 0.2),
        stable_reset_after_secs=0.1,
    )
    await proxy.start()
    preparation = await proxy.prepare_browser_capture(timeout_secs=1.0)
    proxy.commit_browser_capture(preparation)

    await asyncio.sleep(0.15)
    assert len(created) == 1

    first = created[0]
    first._end_stream = True
    await asyncio.sleep(0.3)
    assert len(created) == 2
    await proxy.stop()


async def test_capture_reconnect_waits_while_ready_probe_denies(
    tmp_path: Path,
) -> None:
    ready = {"value": False}
    created: list[FakeTranscriber] = []

    def factory(_ctx: ASRSessionContext) -> FakeTranscriber:
        transcriber = ShortLivedTranscriber(source_epoch=1 + len(created))
        created.append(transcriber)
        return transcriber

    async def probe() -> bool:
        return ready["value"]

    proxy = SubtitleProxy(
        _settings(tmp_path),
        transcriber_factory=factory,
        backoff_delays=(0.01, 0.02),
        readiness_probe=probe,
    )
    await proxy.start()
    preparation = await proxy.prepare_capture("meeting-1", timeout_secs=1.0)
    proxy.commit_capture(preparation)

    await asyncio.sleep(0.2)
    assert len(created) == 1

    ready["value"] = True
    await asyncio.sleep(0.15)
    assert len(created) >= 2
    await proxy.abort_capture()
    await proxy.stop()


def test_is_standalone_filler_identifies_hallucinations_and_preserves_speech() -> None:
    from sona.subtitles.sessions import is_standalone_filler

    # 纯语气词/停顿/符号 -> 判定为孤立 filler
    assert is_standalone_filler("") is True
    assert is_standalone_filler("   ") is True
    assert is_standalone_filler("。！？…") is True
    assert is_standalone_filler("嗯") is True
    assert is_standalone_filler("嗯。") is True
    assert is_standalone_filler("啊！") is True
    assert is_standalone_filler("呃……") is True
    assert is_standalone_filler("嗯。哦。嗯。") is True
    assert is_standalone_filler("唔……额……") is True

    # 包含真实语义词汇 -> 判定为正常语音
    assert is_standalone_filler("嗯，好的") is False
    assert is_standalone_filler("嗯我知道了") is False
    assert is_standalone_filler("啊对对对") is False
    assert is_standalone_filler("hello") is False
    assert is_standalone_filler("开会讨论") is False


async def test_standard_subtitle_session_drops_standalone_filler_and_preserves_real_speech(
    tmp_path: Path,
) -> None:
    from sona.subtitles.sessions import StandardSubtitleSession

    transcriber = FakeTranscriber(source_epoch=1)
    queue: asyncio.Queue[bytes] = asyncio.Queue()
    stop_event = asyncio.Event()
    payloads: list[dict[str, object]] = []

    async def record_payload(p: dict[str, object]) -> None:
        payloads.append(p)

    async def noop_epoch_closed(_epoch: int) -> None:
        pass

    session = StandardSubtitleSession(
        audio_queue=queue,
        transcriber_factory=lambda _ctx: transcriber,
        backoff_delays=(0.01,),
        stop_event=stop_event,
        running=lambda: True,
        capture_active=lambda: False,
        on_payload=record_payload,
        on_state=lambda _s: None,
        on_epoch_opened=lambda: None,
        on_epoch_closed=noop_epoch_closed,
        on_reconnect=lambda: None,
        on_last_event=lambda: None,
        on_last_error=lambda _e: None,
        on_dropped_chunk=lambda: None,
        on_gap=lambda: None,
    )

    preparation = await session.prepare(timeout_secs=1.0)
    session.commit(preparation)

    # 1. 模拟收到底噪语气词幻觉 "嗯。"
    filler_window = ASRWindow(
        source_epoch=1,
        partial="嗯。",
        segments=(
            ASRSegment(
                order=0,
                source_epoch=1,
                speaker_key="epoch:1:speaker:0",
                start_ms=0,
                end_ms=1000,
                text="嗯。",
            ),
        ),
    )
    await session._handle_stream_event(ASREvent(kind="final", window=filler_window))

    # 断言：纯孤立 filler 被丢弃，未录入 confirmed_segments，lines 为空，partial 为空
    assert len(payloads) >= 1
    last_payload = payloads[-1]
    assert last_payload.get("type") == "full_update"
    assert last_payload.get("lines") == []
    assert last_payload.get("buffer_transcription") == ""

    # 2. 模拟收到真实发言 "大家好，开会了"
    real_window = ASRWindow(
        source_epoch=1,
        partial="",
        segments=(
            ASRSegment(
                order=1,
                source_epoch=1,
                speaker_key="epoch:1:speaker:0",
                start_ms=1500,
                end_ms=3000,
                text="大家好，开会了",
            ),
        ),
    )
    await session._handle_stream_event(ASREvent(kind="final", window=real_window))

    # 断言：真实语音被正常记录并广播
    last_payload = payloads[-1]
    assert last_payload.get("type") == "full_update"
    assert len(last_payload["lines"]) == 1  # type: ignore[arg-type]
    assert last_payload["lines"][0]["text"] == "大家好，开会了"  # type: ignore[index]

    await session.close_stream()


def test_build_server_vad_config_defaults_to_calibrated_threshold() -> None:
    from sona.speechrail.transport import (
        DEFAULT_SERVER_VAD,
        DEFAULT_SERVER_VAD_THRESHOLD,
        MEETING_SERVER_VAD,
        build_server_vad_config,
    )

    assert DEFAULT_SERVER_VAD_THRESHOLD == 0.65
    assert DEFAULT_SERVER_VAD["threshold"] == 0.65
    assert MEETING_SERVER_VAD["threshold"] == 0.65

    custom = build_server_vad_config(threshold=0.75, silence_duration_ms=800)
    assert custom["threshold"] == 0.75
    assert custom["silence_duration_ms"] == 800
    assert custom["prefix_padding_ms"] == 300
