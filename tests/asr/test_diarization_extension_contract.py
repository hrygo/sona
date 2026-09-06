"""SPK-E2E-1 分人扩展契约测试。

fixture 来源与哈希见 ``fixtures/diarization_extension/MANIFEST.json``；
Rail R2 golden fixtures 冻结后必须重新核对，禁止为通过测试改造字段名。
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from sona.asr.models import ASRCompletedItem, ASRWindow
from sona.meeting.asr_mapping import meeting_sample, to_transcript_window
from sona.speechrail.transcription_events import (
    DiarizationFinalizedEvent,
    DiarizationStatusEvent,
    DiarizationUpdateEvent,
    TranscriptionCompleted,
    decode_transcription_event,
)
from sona.speechrail.transport import SpeechRailProtocolError

FIXTURE_DIR = pathlib.Path(__file__).parent / "fixtures" / "diarization_extension"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_fixture_copies_match_recorded_hashes() -> None:
    manifest = json.loads((FIXTURE_DIR / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["spec_id"] == "SPK-E2E-1"
    for name, digest in manifest["sha256"].items():
        actual = hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest()
        assert actual == digest, name


# ---------------------------------------------------------------------------
# 会话样本时间与正文单元
# ---------------------------------------------------------------------------


def test_session_sample_is_added_exactly_once() -> None:
    assert meeting_sample(160000, 49600) == 209600


def test_unicode_units_reconstruct_the_final_text() -> None:
    text = "同意🙂。"
    ranges = [(0, 2), (2, 4)]
    assert "".join(text[start:end] for start, end in ranges) == text


def test_completed_fixture_decodes_with_tiled_units() -> None:
    raw = _fixture("completed.attribution_units.json")
    decoded = decode_transcription_event(raw, diarization_extensions=True)
    assert isinstance(decoded, TranscriptionCompleted)
    assert decoded.transcript == "同意。"
    assert decoded.audio_start_sample == 48000
    assert decoded.audio_end_sample == 56000
    assert len(decoded.attribution_units) == 1
    unit = decoded.attribution_units[0]
    assert unit.segment_uid == "seg_example_2_0"
    assert (unit.text_start, unit.text_end) == (0, 3)
    assert unit.timing_quality == "aligned"
    # 单元范围必须逐字重造 canonical text。
    assert decoded.transcript[unit.text_start : unit.text_end] == "同意。"


def test_completed_units_must_tile_transcript_exactly() -> None:
    base = _fixture("completed.attribution_units.json")
    # UTF-16 语义的错切：end 超出 code point 长度，必须拒绝。
    utf16_misplit = dict(base)
    utf16_misplit["transcript"] = "同意🙂。"
    utf16_misplit["attribution_units"] = [
        {"segment_uid": "u1", "text_start": 0, "text_end": 2,
         "audio_start_sample": 0, "audio_end_sample": 10, "timing_quality": "aligned"},
        {"segment_uid": "u2", "text_start": 2, "text_end": 4,
         "audio_start_sample": 10, "audio_end_sample": 20, "timing_quality": "aligned"},
        {"segment_uid": "u3", "text_start": 4, "text_end": 5,
         "audio_start_sample": 20, "audio_end_sample": 30, "timing_quality": "aligned"},
    ]
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(utf16_misplit, diarization_extensions=True)


def test_completed_units_with_gap_or_overlap_are_rejected() -> None:
    base = _fixture("completed.attribution_units.json")
    overlapped = dict(base)
    overlapped["attribution_units"] = [
        {"segment_uid": "u1", "text_start": 0, "text_end": 2,
         "audio_start_sample": 0, "audio_end_sample": 10, "timing_quality": "aligned"},
        {"segment_uid": "u2", "text_start": 1, "text_end": 3,
         "audio_start_sample": 10, "audio_end_sample": 20, "timing_quality": "aligned"},
    ]
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(overlapped, diarization_extensions=True)
    gapped = dict(base)
    gapped["attribution_units"] = [
        {"segment_uid": "u1", "text_start": 0, "text_end": 2,
         "audio_start_sample": 0, "audio_end_sample": 10, "timing_quality": "aligned"},
    ]
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(gapped, diarization_extensions=True)


def test_completed_without_samples_is_protocol_error_in_extensions_mode() -> None:
    raw = _fixture("completed.attribution_units.json")
    legacy_shape = {k: v for k, v in raw.items()
                    if k not in {"audio_start_sample", "audio_end_sample", "attribution_units"}}
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(legacy_shape, diarization_extensions=True)
    # legacy 模式下同样的 completed（无样本字段）保持可解码。
    decoded = decode_transcription_event(legacy_shape, diarization_extensions=False)
    assert isinstance(decoded, TranscriptionCompleted)
    assert decoded.attribution_units == ()


def test_empty_extension_completed_is_not_mapped_to_persisted_body() -> None:
    window = ASRWindow(
        source_epoch=1,
        source_session_id="sess-empty",
        completed_items=(
            ASRCompletedItem(
                item_id="item-empty",
                event_id="evt-empty",
                sequence=4,
                audio_start_sample=0,
                audio_end_sample=0,
                canonical_text="",
                units=(),
            ),
        ),
    )

    mapped = to_transcript_window(window)

    assert mapped.segments == ()
    assert mapped.completed == ()


# ---------------------------------------------------------------------------
# 协商与扩展事件
# ---------------------------------------------------------------------------


def test_update_fixture_decodes_strictly() -> None:
    raw = _fixture("diarization.update.json")
    decoded = decode_transcription_event(raw, diarization_extensions=True)
    assert isinstance(decoded, DiarizationUpdateEvent)
    assert decoded.group_generation == "generation_example"
    assert decoded.stable_through_sample == 52800
    assert decoded.updates[0].revision == 1
    assert decoded.updates[0].status == "stable"
    assert decoded.updates[0].speaker == "spk_01"
    assert decoded.updates[0].candidates[0].support_ratio == 0.95
    assert decoded.speaker_links == ()


def test_update_rejects_out_of_range_ratio_and_bad_speaker() -> None:
    raw = _fixture("diarization.update.json")
    bad_ratio = json.loads(json.dumps(raw))
    bad_ratio["updates"][0]["coverage_ratio"] = 1.5
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(bad_ratio, diarization_extensions=True)
    bad_speaker = json.loads(json.dumps(raw))
    bad_speaker["updates"][0]["speaker"] = "speaker_one"
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(bad_speaker, diarization_extensions=True)
    bool_ratio = json.loads(json.dumps(raw))
    bool_ratio["updates"][0]["overlap_ratio"] = True
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(bool_ratio, diarization_extensions=True)


def test_status_fixture_requires_degraded() -> None:
    raw = _fixture("diarization.status.degraded.json")
    decoded = decode_transcription_event(raw, diarization_extensions=True)
    assert isinstance(decoded, DiarizationStatusEvent)
    assert decoded.status == "degraded"
    assert decoded.reason == "diarization_overloaded"
    assert decoded.since_sample == 52000
    active = json.loads(json.dumps(raw))
    active["status"] = "active"
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(active, diarization_extensions=True)


def test_finalized_fixture_decodes_strictly() -> None:
    raw = _fixture("diarization.finalized.json")
    decoded = decode_transcription_event(raw, diarization_extensions=True)
    assert isinstance(decoded, DiarizationFinalizedEvent)
    assert decoded.status == "complete"
    assert decoded.through_sample == decoded.stable_through_sample
    assert decoded.last_update_sequence == 13
    bad_status = json.loads(json.dumps(raw))
    bad_status["status"] = "ok"
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(bad_status, diarization_extensions=True)


def test_unnegotiated_connection_rejects_extension_events() -> None:
    for name in ("diarization.update.json", "diarization.status.degraded.json",
                 "diarization.finalized.json"):
        with pytest.raises(SpeechRailProtocolError):
            decode_transcription_event(_fixture(name), diarization_extensions=False)


def test_extensions_mode_rejects_legacy_segment_double_write() -> None:
    raw = {
        "type": "conversation.item.input_audio_transcription.segment",
        "event_id": "evt-1",
        "session_id": "sess-1",
        "sequence": 9,
        "item_id": "item-1",
        "content_index": 0,
        "id": 0,
        "text": "你好",
        "speaker": "spk_01",
        "start": 0.0,
        "end": 1.0,
    }
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(raw, diarization_extensions=True)


# ---------------------------------------------------------------------------
# 映射层：新模式身份与会议时间
# ---------------------------------------------------------------------------


def test_new_mode_units_keep_unknown_identity_and_stable_uuid() -> None:
    from uuid import NAMESPACE_URL, uuid5

    from sona.asr.models import ASRSegment

    window = ASRWindow(
        source_epoch=1,
        source_session_id="sess_example",
        segments=(
            ASRSegment(
                order=0,
                source_epoch=1,
                speaker_key="unknown",
                start_ms=3100,
                end_ms=3300,
                text="同意。",
                source_uid="seg_example_2_0",
                timing_quality="aligned",
            ),
        ),
    )
    normalized = to_transcript_window(window)
    segment = normalized.segments[0]
    # 新模式不拼 group+label 当持久身份；UUID 由 source session + uid 决定。
    assert segment.speaker_key == "unknown"
    assert segment.id == uuid5(
        NAMESPACE_URL, "speechrail:spk-e2e-1:sess_example:seg_example_2_0"
    )
    # 同一 source UID 重放映射到同一 UUID。
    again = to_transcript_window(window)
    assert again.segments[0].id == segment.id


def test_different_source_sessions_with_same_uid_do_not_merge() -> None:
    from sona.asr.models import ASRSegment

    def _window(session_id: str) -> ASRWindow:
        return ASRWindow(
            source_epoch=1,
            source_session_id=session_id,
            segments=(
                ASRSegment(
                    order=0,
                    source_epoch=1,
                    speaker_key="unknown",
                    start_ms=0,
                    end_ms=1000,
                    text="你好",
                    source_uid="seg_same",
                ),
            ),
        )

    first = to_transcript_window(_window("sess_a")).segments[0].id
    second = to_transcript_window(_window("sess_b")).segments[0].id
    assert first != second


# ---------------------------------------------------------------------------
# 客户端协商：capability → session.updated 契约校验
# ---------------------------------------------------------------------------

_CAPABILITY_SESSION = {
    "type": "session.created",
    "event_id": "evt-1",
    "session_id": "sess-neg",
    "sequence": 1,
    "session": {"id": "sess-neg", "capabilities": ["speechrail.diarization.v1"]},
}

_PLAIN_SESSION = {
    "type": "session.created",
    "event_id": "evt-1",
    "session_id": "sess-neg",
    "sequence": 1,
    "session": {"id": "sess-neg", "capabilities": []},
}

_CONTRACT = {
    "version": 1,
    "timebase": "session_samples",
    "sample_rate": 16000,
    "max_speakers": 4,
    "max_item_duration_ms": 8000,
    "max_revision_delay_ms": 3000,
    "group_generation": "generation_example",
}


class _NegotiationConnection:
    """按能力回放 session.created/session.updated 的最小连接。"""

    uri = "ws://speechrail.test/v1/realtime"

    def __init__(self, created: dict[str, object], with_contract: bool) -> None:
        self.sent: list[dict[str, object]] = []
        self._created = created
        self._with_contract = with_contract

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        message = dict(self._created)
        self._created = {
            "type": "session.updated",
            "event_id": "evt-2",
            "session_id": "sess-neg",
            "sequence": 2,
            "session": (
                {"id": "sess-neg", "diarization_contract": _CONTRACT}
                if self._with_contract
                else {"id": "sess-neg"}
            ),
        }
        return json.dumps(message)

    async def close(self) -> None:
        return None


def _client(connection: _NegotiationConnection) -> object:
    from sona.speechrail.transport import SpeechRailRealtimeClient

    return SpeechRailRealtimeClient(
        url="ws://speechrail.test/v1/realtime",
        connection_factory=lambda _url: _resolved(connection),
    )


def _resolved(connection: _NegotiationConnection) -> object:
    async def _awaitable() -> _NegotiationConnection:
        return connection

    return _awaitable()


async def _negotiate(connection: _NegotiationConnection, **kwargs: object) -> object:
    client = _client(connection)
    await client.connect(
        language="zh",
        diarization=True,
        diarization_group_id="g" * 32,
        diarization_extensions=True,
        **kwargs,
    )
    return client


async def test_capability_missing_sends_no_extensions() -> None:
    connection = _NegotiationConnection(_PLAIN_SESSION, with_contract=False)
    client = await _negotiate(connection)

    update = connection.sent[0]
    diarization = update["session"]["input_audio_transcription"]["diarization"]
    assert "extensions" not in diarization
    assert client.diarization_contract is None


async def test_capability_present_negotiates_and_validates_contract() -> None:
    connection = _NegotiationConnection(_CAPABILITY_SESSION, with_contract=True)
    client = await _negotiate(connection)

    update = connection.sent[0]
    diarization = update["session"]["input_audio_transcription"]["diarization"]
    assert diarization["extensions"] == ["speechrail.diarization.v1"]
    assert client.diarization_contract is not None
    assert client.diarization_contract["timebase"] == "session_samples"
    assert client.diarization_contract["sample_rate"] == 16000


async def test_missing_contract_after_extension_request_is_protocol_error() -> None:
    import pytest as _pytest

    from sona.speechrail.transport import SpeechRailProtocolError

    connection = _NegotiationConnection(_CAPABILITY_SESSION, with_contract=False)
    with _pytest.raises(SpeechRailProtocolError):
        await _negotiate(connection)


async def test_speaker_count_hint_above_four_rejected_in_extensions_entry() -> None:
    import pytest as _pytest

    connection = _NegotiationConnection(_CAPABILITY_SESSION, with_contract=True)
    with _pytest.raises(ValueError, match="SPEECHRAIL_SPEAKER_LIMIT_EXCEEDED"):
        await _negotiate(connection, speaker_count_hint=5)
    # 未发送任何事件即拒绝。
    assert connection.sent == []
