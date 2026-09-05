from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from sona.config.meeting import MeetingSettings
from sona.meeting.diarization_overlay import (
    MeetingDiarizationOverlay,
    SpeakerLabelledSpan,
    assign_speakers_by_overlap,
    meeting_diarization_group_id,
    meeting_range,
    meeting_speaker_key,
    overlay_gate_for_runtime,
)
from sona.meeting.models import NormalizedSegment
from sona.speechrail.batch_transcriber import (
    SpeechRailBatchTranscriber,
    SpeechRailDiarizeResult,
    SpeechRailDiarizeSegment,
    _http_base_url,
)
from sona.speechrail.transport import SpeechRailProtocolError


@pytest.mark.asyncio
async def test_batch_transcriber_parses_verbose_json_segments() -> None:
    payload = {
        "text": "今天讨论预算",
        "language": "zh",
        "segments": [
            {"text": "今天", "start": 0.0, "end": 0.4, "speaker": "spk_01"},
            {"text": "讨论预算", "start": 0.5, "end": 1.2, "speaker": "spk_02"},
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transcriber = SpeechRailBatchTranscriber(
        url="ws://127.0.0.1:8201/v1/realtime",
        http_client=client,
    )
    result = await transcriber.transcribe_diarize(b"\x00\x00" * 16_000)
    assert result.text == "今天讨论预算"
    assert result.language == "zh"
    assert len(result.segments) == 2
    assert result.segments[0].speaker == "spk_01"
    assert result.segments[0].start_ms == 0
    assert result.segments[0].end_ms == 400
    assert result.segments[1].speaker == "spk_02"


@pytest.mark.asyncio
async def test_batch_transcriber_drops_malformed_segments() -> None:
    payload = {
        "text": "hello",
        "segments": [
            {"text": "", "start": 0, "end": 1, "speaker": "spk_01"},
            {"text": "  ", "start": 0, "end": 1},
            {"text": "ok", "start": "bad", "end": 1.0, "speaker": "spk_01"},
            {"text": "good", "start": 1.0, "end": 2.0, "speaker": "spk_02"},
        ],
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transcriber = SpeechRailBatchTranscriber(
        url="ws://127.0.0.1:8201/v1/realtime", http_client=client
    )
    result = await transcriber.transcribe_diarize(b"\x00\x00" * 16_000)
    assert len(result.segments) == 1
    assert result.segments[0].text == "good"
    assert result.segments[0].speaker == "spk_02"


@pytest.mark.asyncio
async def test_batch_transcriber_wraps_non_200_as_protocol_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, content=b"boom")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transcriber = SpeechRailBatchTranscriber(
        url="ws://127.0.0.1:8201/v1/realtime", http_client=client
    )
    with pytest.raises(SpeechRailProtocolError):
        await transcriber.transcribe_diarize(b"\x00\x00" * 16_000)


def test_http_base_url_derives_http_from_ws() -> None:
    assert _http_base_url("ws://127.0.0.1:8201/v1/realtime") == "http://127.0.0.1:8201"
    assert _http_base_url("wss://speechrail.local/v1/realtime") == "https://speechrail.local"


def test_speaker_key_maps_to_group_namespace() -> None:
    assert meeting_speaker_key("abc123", "spk_01") == "group:abc123:speaker:spk_01"
    assert meeting_speaker_key("abc123", "") == "group:abc123:speaker:0"


def test_meeting_diarization_group_id_is_deterministic_sha256() -> None:
    import hashlib

    owner = "meeting:9670174c-1e6e-4a72-9a0b-5e0d3226b7a1"
    expected = hashlib.sha256(owner.encode("utf-8")).hexdigest()
    assert meeting_diarization_group_id(owner) == expected
    assert meeting_diarization_group_id(owner) == meeting_diarization_group_id(owner)


def test_meeting_diarization_group_id_requires_owner() -> None:
    with pytest.raises(ValueError, match="capture owner"):
        meeting_diarization_group_id(None)


def test_overlay_group_id_matches_streaming_namespace() -> None:
    meeting_id = uuid.uuid4()
    owner = f"meeting:{meeting_id}"
    overlay = MeetingDiarizationOverlay(group_id=meeting_diarization_group_id(owner))
    assert overlay.active is False
    overlay.start(group_id=meeting_diarization_group_id(owner))
    overlay.stop()


def _seg(id_: str, start: int, end: int, spk: str = "group:g:speaker:0") -> NormalizedSegment:
    return NormalizedSegment(
        id=uuid.uuid5(uuid.NAMESPACE_URL, id_),
        order=0,
        source_epoch=0,
        speaker_key=spk,
        start_ms=start,
        end_ms=end,
        text="x",
    )


def _span(key: str, start: int, end: int) -> SpeakerLabelledSpan:
    raw = key.rsplit(":", 1)[-1]
    return SpeakerLabelledSpan(
        speaker_key=key, raw_speaker=raw, start_ms=start, end_ms=end
    )


def test_assign_speakers_by_overlap_uses_max_overlap() -> None:
    segments = [_seg("a", 0, 400), _seg("b", 500, 900)]
    spans = [
        _span("group:g:speaker:spk_01", 0, 400),
        _span("group:g:speaker:spk_02", 500, 800),
    ]
    mapping = assign_speakers_by_overlap(segments, spans)
    assert mapping[str(segments[0].id)] == "group:g:speaker:spk_01"
    assert mapping[str(segments[1].id)] == "group:g:speaker:spk_02"


def test_assign_speakers_by_overlap_no_overlap_keeps_default() -> None:
    segments = [_seg("a", 0, 400)]
    spans = [_span("group:g:speaker:spk_02", 900, 1100)]
    mapping = assign_speakers_by_overlap(segments, spans)
    assert mapping == {}


class _FakeTranscriber:
    def __init__(self) -> None:
        self.calls = 0

    async def transcribe_diarize(self, pcm: bytes) -> SpeechRailDiarizeResult:
        self.calls += 1
        return SpeechRailDiarizeResult(
            text="hi",
            language="zh",
            segments=(SpeechRailDiarizeSegment("你好", 0, 500, "spk_01"),),
        )


def test_overlay_buffers_and_flushes() -> None:
    fake = _FakeTranscriber()
    overlay = MeetingDiarizationOverlay(transcriber=fake, group_id="g")

    async def run() -> None:
        overlay.start(group_id="g")
        overlay.push_pcm(b"\x00\x00" * 16_000 * 2)  # 2s audio > min flush
        result = await overlay.flush()
        assert len(result.spans) == 1
        assert result.spans[0].speaker_key == "group:g:speaker:spk_01"
        assert result.covered_start_ms == 0
        assert result.covered_end_ms == 2000
        assert overlay.buffered_pcm() == b""
        overlay.stop()

    asyncio.run(run())
    assert fake.calls == 1


class _NoopTranscriber:
    async def transcribe_diarize(self, pcm: bytes) -> SpeechRailDiarizeResult:
        return SpeechRailDiarizeResult(text="", language=None, segments=())


def test_overlay_does_not_flush_below_min_threshold() -> None:
    overlay = MeetingDiarizationOverlay(transcriber=_NoopTranscriber(), group_id="g")

    async def run() -> None:
        overlay.start(group_id="g")
        overlay.push_pcm(b"\x00\x00" * 1_000)  # too short
        result = await overlay.flush()
        assert result.spans == ()
        overlay.stop()

    asyncio.run(run())


def test_overlay_finish_flushes_and_stops() -> None:
    fake = _FakeTranscriber()
    overlay = MeetingDiarizationOverlay(transcriber=fake, group_id="g")

    async def run() -> None:
        overlay.start(group_id="g")
        overlay.push_pcm(b"\x00\x00" * 16_000 * 2)
        result = await overlay.finish()
        assert len(result.spans) == 1
        assert not overlay.active
        assert overlay.buffered_pcm() == b""

    asyncio.run(run())


# ---------------------------------------------------------------------------
# S0: 缓冲裁剪偏移、请求上限前置校验与默认关闭
# ---------------------------------------------------------------------------


def test_meeting_range_offsets_buffer_relative_spans() -> None:
    # 45 分钟输入只保留最后 30 分钟：缓冲起点是会议第 900 秒。
    # 缓冲内相对第 1~2 秒的结果必须落在会议第 901~902 秒。
    start, end = meeting_range(900 * 16000, 16000, 32000)
    assert (start, end) == (901 * 16000, 902 * 16000)


def test_meeting_range_without_trim_is_identity() -> None:
    assert meeting_range(0, 16000, 32000) == (16000, 32000)


def test_default_config_disables_overlay_and_extensions() -> None:
    settings = MeetingSettings(_env_file=None)
    assert settings.diarization_overlay_enabled is False
    assert settings.diarization_extensions_enabled is False


def test_overlay_gate_extensions_win_over_legacy_flag() -> None:
    # 默认：不启用诊断 overlay。
    assert overlay_gate_for_runtime(overlay_enabled=False, extensions_enabled=False) is False
    # 显式 legacy 诊断（无扩展）：允许。
    assert overlay_gate_for_runtime(overlay_enabled=True, extensions_enabled=False) is True
    # 扩展开启后，旧 overlay 开关残留 true 也必须不启用。
    assert overlay_gate_for_runtime(overlay_enabled=True, extensions_enabled=True) is False


class _CountingTranscriber:
    """记录调用次数与入参的 spy。"""

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    async def transcribe_diarize(self, pcm: bytes) -> SpeechRailDiarizeResult:
        self.calls.append(pcm)
        return SpeechRailDiarizeResult(
            text="hi",
            language="zh",
            segments=(SpeechRailDiarizeSegment("你好", 1000, 2000, "spk_01"),),
        )


def _push_minutes(overlay: MeetingDiarizationOverlay, minutes: int) -> None:
    # 每分钟 = 16_000 个 2 字节样本 = 32_000 字节。
    minute_bytes = b"\x00\x00" * 16_000 * 60
    for _ in range(minutes):
        overlay.push_pcm(minute_bytes)


def test_overlay_trims_to_tail_and_tracks_buffer_start_sample() -> None:
    overlay = MeetingDiarizationOverlay(
        transcriber=_CountingTranscriber(), group_id="g", max_buffer_seconds=1800
    )
    overlay.start(group_id="g")
    _push_minutes(overlay, 45)

    # 只保留最后 30 分钟，且样本起点可追溯：第 900 秒。
    assert overlay.buffer_start_sample == 900 * 16000
    buffered = overlay.buffered_pcm()
    assert len(buffered) == 1800 * 32_000


@pytest.mark.asyncio
async def test_overlay_flush_returns_meeting_time_spans_and_coverage() -> None:
    spy = _CountingTranscriber()
    overlay = MeetingDiarizationOverlay(
        transcriber=spy, group_id="g", max_buffer_seconds=1800
    )
    overlay.start(group_id="g")
    _push_minutes(overlay, 45)

    result = await overlay.flush()

    assert len(spy.calls) == 1
    # span 在缓冲内相对 1~2 秒，输出必须已加裁剪偏移到会议时间线。
    assert result.spans[0].start_ms == 901 * 1000
    assert result.spans[0].end_ms == 902 * 1000
    assert result.covered_start_ms == 900 * 1000
    assert result.covered_end_ms == 45 * 60 * 1000


@pytest.mark.asyncio
async def test_overlay_flush_without_trim_keeps_zero_offset() -> None:
    spy = _CountingTranscriber()
    overlay = MeetingDiarizationOverlay(transcriber=spy, group_id="g", max_buffer_seconds=60)
    overlay.start(group_id="g")
    overlay.push_pcm(b"\x00\x00" * 16_000 * 2)

    result = await overlay.flush()

    assert result.spans[0].start_ms == 1000
    assert result.covered_start_ms == 0
    assert result.covered_end_ms == 2000


@pytest.mark.asyncio
async def test_overlay_preflight_rejects_oversized_request_visibly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 7200 秒缓冲(230MB)超过 batch WAV 上限(120MB)：必须前置拒绝并可见告警，
    # 而不是把超大请求发出去后静默失败。
    spy = _CountingTranscriber()
    overlay = MeetingDiarizationOverlay(
        transcriber=spy, group_id="g", max_buffer_seconds=7200
    )
    overlay.start(group_id="g")
    overlay.push_pcm(b"\x00\x00" * (60_000_001))

    with caplog.at_level("WARNING"):
        result = await overlay.flush()

    assert spy.calls == []
    assert result.spans == ()
    assert any("长度上限" in record.message for record in caplog.records)
