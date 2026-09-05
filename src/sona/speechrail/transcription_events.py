"""Event-specific semantic decoding for SpeechRail OpenAI Realtime transcription.

Transport-level envelope concerns (JSON, generic envelope, strict sequence,
session identity) stay unique in ``speechrail.transport``.  This module only
validates event-specific fields and produces a narrow typed union for ASR
adapters to pattern-match against the OpenAI Realtime ``/v1/realtime`` events.

SPK-E2E-1 分人扩展（``speechrail.diarization.v1``）只在显式协商成功后解码：
``decode_transcription_event(..., diarization_extensions=True)`` 才接受三个
登记的扩展 type 与带 ``attribution_units`` 的 completed；未协商连接收到这些
事件按协议错误拒绝，legacy ``.segment`` 在扩展模式下视为双写协议错误。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sona.speechrail.transport import SpeechRailProtocolError

__all__ = [
    "AttributionUnit",
    "DiarizationCandidate",
    "DiarizationFinalizedEvent",
    "DiarizationSpeakerLink",
    "DiarizationStatusEvent",
    "DiarizationUpdate",
    "DiarizationUpdateEvent",
    "Noop",
    "SpeechRailTranscriptionError",
    "SpeechRailTranscriptionEvent",
    "TranscriptionCompleted",
    "TranscriptionDelta",
    "TranscriptionSegment",
    "decode_transcription_event",
]

# SPK-E2E-1 登记的扩展 type（Rail 规格 §5）。
DIARIZATION_UPDATE_TYPE = "speechrail.diarization.update"
DIARIZATION_STATUS_TYPE = "speechrail.diarization.status"
DIARIZATION_FINALIZED_TYPE = "speechrail.diarization.finalized"
DIARIZATION_EXTENSION_TYPES = frozenset(
    {DIARIZATION_UPDATE_TYPE, DIARIZATION_STATUS_TYPE, DIARIZATION_FINALIZED_TYPE}
)

# 归属单元/样本的协议上限（Rail 规格 §5.2）。
_MAX_UNITS_PER_ITEM = 4096
_MAX_SEGMENT_UID_LENGTH = 128
_TIMING_QUALITIES = frozenset({"aligned", "unavailable"})
_PATCH_STATUSES = frozenset({"unknown", "tentative", "stable"})
_FINALIZED_STATUSES = frozenset({"complete", "degraded"})

# Server->client events that carry no transcription payload and are safely
# ignored by the ASR adapters.
_SESSION_NOOPS = frozenset(
    {
        "session.created",
        "session.updated",
        "conversation.created",
        "conversation.item.created",
        "input_audio_buffer.committed",
        "input_audio_buffer.cleared",
        "response.created",
        "response.output_item.added",
        "response.content_part.added",
    }
)


@dataclass(frozen=True, slots=True)
class Noop:
    """A semantically-inert server event (session/ack/parent envelope)."""

    reason: str
    item_id: str | None = None
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionDelta:
    text: str
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    """One immutable transcription segment; ``speaker`` is anonymous or null."""

    text: str
    speaker: str | None
    start_ms: int
    end_ms: int
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionCompleted:
    transcript: str
    item_id: str | None = None
    # SPK-E2E-1 扩展模式字段（legacy 模式恒为空/None）。
    audio_start_sample: int | None = None
    audio_end_sample: int | None = None
    attribution_units: tuple[AttributionUnit, ...] = ()


@dataclass(frozen=True, slots=True)
class AttributionUnit:
    """一个不可变归属单元：canonical text 的 code point 切片（左闭右开）。"""

    segment_uid: str
    text_start: int
    text_end: int
    audio_start_sample: int
    audio_end_sample: int
    timing_quality: str


@dataclass(frozen=True, slots=True)
class DiarizationUpdate:
    """单个 segment 的归属修订（revision 从 1 严格递增）。"""

    segment_uid: str
    revision: int
    status: str
    speaker: str | None
    coverage_ratio: float
    overlap_ratio: float
    candidates: tuple[DiarizationCandidate, ...]


@dataclass(frozen=True, slots=True)
class DiarizationCandidate:
    speaker: str
    support_ratio: float


@dataclass(frozen=True, slots=True)
class DiarizationSpeakerLink:
    """跨 session 声学关联建议（不可传递放大）。"""

    link_id: str
    from_session_id: str
    from_speaker: str
    to_session_id: str
    to_speaker: str
    relation: str
    similarity: float


@dataclass(frozen=True, slots=True)
class DiarizationUpdateEvent:
    group_generation: str | None
    stable_through_sample: int
    updates: tuple[DiarizationUpdate, ...]
    speaker_links: tuple[DiarizationSpeakerLink, ...]


@dataclass(frozen=True, slots=True)
class DiarizationStatusEvent:
    """扩展模式内的分人状态；本期只允许 active→degraded 一次。"""

    status: str
    reason: str | None
    since_sample: int


@dataclass(frozen=True, slots=True)
class DiarizationFinalizedEvent:
    """分人终态；complete 时 through/stable 两个 sample 相等。"""

    finalization_id: str
    through_sample: int
    stable_through_sample: int
    status: str
    reason: str | None
    last_update_sequence: int


@dataclass(frozen=True, slots=True)
class SpeechRailTranscriptionError:
    code: str
    message: str


type SpeechRailTranscriptionEvent = (
    Noop
    | TranscriptionDelta
    | TranscriptionSegment
    | TranscriptionCompleted
    | DiarizationUpdateEvent
    | DiarizationStatusEvent
    | DiarizationFinalizedEvent
    | SpeechRailTranscriptionError
)


def decode_transcription_event(
    raw: Mapping[str, object], *, diarization_extensions: bool = False
) -> SpeechRailTranscriptionEvent:
    """Decode a transport-validated OpenAI Realtime event into a typed form.

    Raises :class:`SpeechRailProtocolError` with ``SPEECHRAIL_PROTOCOL_ERROR``
    for shape violations and ``SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR`` for
    speaker/timestamp violations.  ``diarization_extensions=True`` 才接受
    SPK-E2E-1 扩展事件；未协商连接收到扩展事件同样是协议错误。
    """
    event_type = raw.get("type")
    if not isinstance(event_type, str):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if event_type in DIARIZATION_EXTENSION_TYPES:
        if not diarization_extensions:
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        if event_type == DIARIZATION_UPDATE_TYPE:
            return _decode_diarization_update(raw)
        if event_type == DIARIZATION_STATUS_TYPE:
            return _decode_diarization_status(raw)
        return _decode_diarization_finalized(raw)
    if event_type == "input_audio_buffer.speech_started":
        return _decode_speech_boundary(raw, "audio_start_ms")
    if event_type == "input_audio_buffer.speech_stopped":
        return _decode_speech_boundary(raw, "audio_end_ms")
    if event_type in _SESSION_NOOPS:
        return Noop(reason=event_type, item_id=_optional_item_id(raw.get("item_id")))
    if event_type == "conversation.item.input_audio_transcription.delta":
        return TranscriptionDelta(
            text=_require_text(raw.get("delta")),
            item_id=_optional_item_id(raw.get("item_id")),
        )
    if event_type == "conversation.item.input_audio_transcription.completed":
        if diarization_extensions:
            return _decode_completed_with_units(raw)
        return TranscriptionCompleted(
            transcript=_require_text(raw.get("transcript")),
            item_id=_optional_item_id(raw.get("item_id")),
        )
    if event_type == "conversation.item.input_audio_transcription.segment":
        if diarization_extensions:
            # 扩展模式不再下发 legacy .segment；收到即双写协议错误。
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
        return _decode_segment(raw)
    if event_type == "conversation.item.input_audio_transcription.failed":
        return _decode_error(raw.get("error"))
    if event_type == "error":
        return _decode_error(raw.get("error"))
    raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")


def _require_text(value: object) -> str:
    if not isinstance(value, str):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return value


def _optional_item_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return value


def _decode_speech_boundary(raw: Mapping[str, object], field: str) -> Noop:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return Noop(
        reason=(
            "input_audio_buffer.speech_started"
            if field == "audio_start_ms"
            else "input_audio_buffer.speech_stopped"
        ),
        item_id=_optional_item_id(raw.get("item_id")),
        audio_start_ms=value if field == "audio_start_ms" else None,
        audio_end_ms=value if field == "audio_end_ms" else None,
    )


def _decode_segment(raw: Mapping[str, object]) -> TranscriptionSegment:
    text = raw.get("text")
    start = raw.get("start")
    end = raw.get("end")
    if (
        not isinstance(text, str)
        or not text.strip()
        or isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or start < 0
        or end < start
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return TranscriptionSegment(
        text=text,
        speaker=_decode_speaker(raw.get("speaker")),
        start_ms=round(start * 1000),
        end_ms=round(end * 1000),
        item_id=_optional_item_id(raw.get("item_id")),
    )


def _decode_speaker(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    if not value.startswith("spk_") or len(value) > 64:
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    return value


def _decode_error(value: object) -> SpeechRailTranscriptionError:
    if not isinstance(value, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    code = value.get("code")
    if not isinstance(code, str) or not code:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    message = value.get("message")
    if message is None:
        message = ""
    elif not isinstance(message, str):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return SpeechRailTranscriptionError(code=code, message=message)


# ---------------------------------------------------------------------------
# SPK-E2E-1 扩展事件严格解码
# ---------------------------------------------------------------------------


def _decode_completed_with_units(raw: Mapping[str, object]) -> TranscriptionCompleted:
    transcript = _require_text(raw.get("transcript"))
    start = _require_sample(raw.get("audio_start_sample"))
    end = _require_sample(raw.get("audio_end_sample"))
    if end < start:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    units = _decode_units(raw.get("attribution_units"), transcript)
    return TranscriptionCompleted(
        transcript=transcript,
        item_id=_optional_item_id(raw.get("item_id")),
        audio_start_sample=start,
        audio_end_sample=end,
        attribution_units=units,
    )


def _require_sample(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return value


def _require_unit_sample(value: object) -> int:
    return _require_sample(value)


def _decode_units(value: object, transcript: str) -> tuple[AttributionUnit, ...]:
    if not isinstance(value, list):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if len(value) > _MAX_UNITS_PER_ITEM:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    units: list[AttributionUnit] = []
    expected_start = 0
    for raw_unit in value:
        if not isinstance(raw_unit, dict):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        segment_uid = raw_unit.get("segment_uid")
        if (
            not isinstance(segment_uid, str)
            or not segment_uid
            or len(segment_uid) > _MAX_SEGMENT_UID_LENGTH
            or not segment_uid.isascii()
        ):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        text_start = _require_sample(raw_unit.get("text_start"))
        text_end = _require_sample(raw_unit.get("text_end"))
        audio_start = _require_unit_sample(raw_unit.get("audio_start_sample"))
        audio_end = _require_unit_sample(raw_unit.get("audio_end_sample"))
        timing = raw_unit.get("timing_quality")
        if (
            text_end <= text_start
            or text_start != expected_start
            or text_end > len(transcript)
            or audio_end < audio_start
            or timing not in _TIMING_QUALITIES
        ):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        expected_start = text_end
        units.append(
            AttributionUnit(
                segment_uid=segment_uid,
                text_start=text_start,
                text_end=text_end,
                audio_start_sample=audio_start,
                audio_end_sample=audio_end,
                timing_quality=timing,
            )
        )
    # 空单元必须对应空 transcript；非空 transcript 必须被完整无缝切分。
    if expected_start != len(transcript):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return tuple(units)


def _decode_diarization_update(raw: Mapping[str, object]) -> DiarizationUpdateEvent:
    group_generation = _optional_group_generation(raw.get("group_generation"))
    stable_through = _require_sample(raw.get("stable_through_sample"))
    raw_updates = raw.get("updates")
    if not isinstance(raw_updates, list) or len(raw_updates) > 256:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    updates = tuple(_decode_update(item) for item in raw_updates)
    links = _decode_speaker_links(raw.get("speaker_links"))
    return DiarizationUpdateEvent(
        group_generation=group_generation,
        stable_through_sample=stable_through,
        updates=updates,
        speaker_links=links,
    )


def _optional_group_generation(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 128:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return value


def _decode_update(raw: object) -> DiarizationUpdate:
    if not isinstance(raw, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    segment_uid = raw.get("segment_uid")
    revision = raw.get("revision")
    status = raw.get("status")
    if (
        not isinstance(segment_uid, str)
        or not segment_uid
        or len(segment_uid) > _MAX_SEGMENT_UID_LENGTH
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or status not in _PATCH_STATUSES
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    speaker = raw.get("speaker")
    if speaker is not None:
        # unknown 主 speaker 必须 null；有名 speaker 必须是匿名 spk_* 标签。
        if isinstance(speaker, bool) or not isinstance(speaker, str):
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
        if not speaker.startswith("spk_") or len(speaker) > 64:
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    coverage = _require_ratio(raw.get("coverage_ratio"))
    overlap = _require_ratio(raw.get("overlap_ratio"))
    candidates = _decode_candidates(raw.get("candidates"))
    return DiarizationUpdate(
        segment_uid=segment_uid,
        revision=revision,
        status=status,
        speaker=speaker,
        coverage_ratio=coverage,
        overlap_ratio=overlap,
        candidates=candidates,
    )


def _require_ratio(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    ratio = float(value)
    if ratio < 0.0 or ratio > 1.0:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return ratio


def _decode_candidates(value: object) -> tuple[DiarizationCandidate, ...]:
    if not isinstance(value, list):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if len(value) > 4:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    candidates: list[DiarizationCandidate] = []
    for raw_candidate in value:
        if not isinstance(raw_candidate, dict):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        speaker = raw_candidate.get("speaker")
        if not isinstance(speaker, str) or not speaker.startswith("spk_") or len(speaker) > 64:
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
        candidates.append(
            DiarizationCandidate(
                speaker=speaker,
                support_ratio=_require_ratio(raw_candidate.get("support_ratio")),
            )
        )
    return tuple(candidates)


def _decode_speaker_links(value: object) -> tuple[DiarizationSpeakerLink, ...]:
    if not isinstance(value, list) or len(value) > 16:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    links: list[DiarizationSpeakerLink] = []
    for raw_link in value:
        if not isinstance(raw_link, dict):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        link_id = raw_link.get("link_id")
        relation = raw_link.get("relation")
        from_session = raw_link.get("from_session_id")
        to_session = raw_link.get("to_session_id")
        from_speaker = raw_link.get("from_speaker")
        to_speaker = raw_link.get("to_speaker")
        if (
            not isinstance(link_id, str)
            or not link_id
            or len(link_id) > 128
            or relation != "same_speaker"
            or not isinstance(from_session, str)
            or not from_session
            or not isinstance(to_session, str)
            or not to_session
            or not isinstance(from_speaker, str)
            or not from_speaker.startswith("spk_")
            or not isinstance(to_speaker, str)
            or not to_speaker.startswith("spk_")
        ):
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
        similarity = _require_ratio(raw_link.get("similarity"))
        links.append(
            DiarizationSpeakerLink(
                link_id=link_id,
                from_session_id=from_session,
                from_speaker=from_speaker,
                to_session_id=to_session,
                to_speaker=to_speaker,
                relation=relation,
                similarity=similarity,
            )
        )
    return tuple(links)


def _decode_diarization_status(raw: Mapping[str, object]) -> DiarizationStatusEvent:
    status = raw.get("status")
    since_sample = _require_sample(raw.get("since_sample"))
    reason = raw.get("reason")
    if status != "degraded":
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return DiarizationStatusEvent(
        status="degraded",
        reason=reason,
        since_sample=since_sample,
    )


def _decode_diarization_finalized(raw: Mapping[str, object]) -> DiarizationFinalizedEvent:
    finalization_id = raw.get("finalization_id")
    through = _require_sample(raw.get("through_sample"))
    stable_through = _require_sample(raw.get("stable_through_sample"))
    status = raw.get("status")
    reason = raw.get("reason")
    last_update_sequence = raw.get("last_update_sequence")
    if not isinstance(finalization_id, str) or not finalization_id or len(finalization_id) > 128:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if status not in _FINALIZED_STATUSES:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if status == "complete" and through != stable_through:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if (
        isinstance(last_update_sequence, bool)
        or not isinstance(last_update_sequence, int)
        or last_update_sequence < 0
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return DiarizationFinalizedEvent(
        finalization_id=finalization_id,
        through_sample=through,
        stable_through_sample=stable_through,
        status=status,
        reason=reason,
        last_update_sequence=last_update_sequence,
    )
