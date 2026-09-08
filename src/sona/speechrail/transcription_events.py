"""Strict semantic decoding for SpeechRail OpenAI Realtime v2 events.

The transport validates the common OpenAI envelope. This module validates
event-specific payloads and exposes immutable values to the ASR adapters. The
only realtime attribution events accepted here are the v2 namespaced
``updated``, ``status`` and ``done`` events.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from sona.speechrail.transport import SpeechRailProtocolError

__all__ = [
    "AttributionUnit",
    "DiarizationCandidate",
    "DiarizationDoneEvent",
    "DiarizationStatusEvent",
    "DiarizationUpdate",
    "DiarizationUpdatedEvent",
    "Noop",
    "SpeechRailTranscriptionError",
    "SpeechRailTranscriptionEvent",
    "TranscriptionCompleted",
    "TranscriptionDelta",
    "TranscriptionSegment",
    "decode_transcription_event",
]

DIARIZATION_UPDATED_TYPE = "speechrail.diarization.updated"
DIARIZATION_STATUS_TYPE = "speechrail.diarization.status"
DIARIZATION_DONE_TYPE = "speechrail.diarization.done"
DIARIZATION_FINISH_TYPE = "speechrail.diarization.finish"
DIARIZATION_EXTENSION_TYPES = frozenset(
    {DIARIZATION_UPDATED_TYPE, DIARIZATION_STATUS_TYPE, DIARIZATION_DONE_TYPE}
)

_MAX_UNITS_PER_ITEM = 4096
_MAX_UPDATES_PER_EVENT = 256
_MAX_SEGMENT_UID_LENGTH = 128
_TIMING_QUALITIES = frozenset({"aligned", "unavailable"})
_PATCH_STATUSES = frozenset({"unknown", "tentative", "stable"})
_DONE_STATUSES = frozenset({"complete", "degraded"})
_ANONYMOUS_SPEAKERS = frozenset({"A", "B", "C", "D"})
AnonymousSpeaker = Literal["A", "B", "C", "D"]

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
        "response.output_item.done",
        "response.content_part.added",
        "response.content_part.done",
        "response.done",
        "response.audio.delta",
        "response.audio.done",
        "response.output_audio.delta",
        "response.output_audio.done",
        "response.audio_transcript.delta",
        "response.audio_transcript.done",
    }
)


@dataclass(frozen=True, slots=True)
class Noop:
    """A semantically inert session or buffer event."""

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
    """One ordinary OpenAI transcription segment."""

    text: str
    speaker: str | None
    start_ms: int
    end_ms: int
    item_id: str | None = None


@dataclass(frozen=True, slots=True)
class AttributionUnit:
    """An immutable code-point and session-sample attribution unit."""

    segment_uid: str
    text_start: int
    text_end: int
    audio_start_sample: int
    audio_end_sample: int
    timing_quality: Literal["aligned", "unavailable"]


@dataclass(frozen=True, slots=True)
class TranscriptionCompleted:
    transcript: str
    item_id: str | None = None
    audio_start_sample: int | None = None
    audio_end_sample: int | None = None
    attribution_units: tuple[AttributionUnit, ...] = ()
    event_id: str | None = None
    session_id: str | None = None
    sequence: int | None = None


@dataclass(frozen=True, slots=True)
class DiarizationCandidate:
    speaker: str
    support_ratio: float


@dataclass(frozen=True, slots=True)
class DiarizationUpdate:
    """One attribution revision; continuity is enforced by the adapter."""

    segment_uid: str
    revision: int
    status: Literal["unknown", "tentative", "stable"]
    speaker: AnonymousSpeaker | None
    coverage_ratio: float
    overlap_ratio: float
    candidates: tuple[DiarizationCandidate, ...]


@dataclass(frozen=True, slots=True)
class DiarizationUpdatedEvent:
    event_id: str
    session_id: str
    sequence: int
    stable_through_sample: int
    updates: tuple[DiarizationUpdate, ...]


@dataclass(frozen=True, slots=True)
class DiarizationStatusEvent:
    event_id: str
    session_id: str
    sequence: int
    status: Literal["degraded"]
    reason: str
    since_sample: int


@dataclass(frozen=True, slots=True)
class DiarizationDoneEvent:
    event_id: str
    session_id: str
    sequence: int
    finalization_id: str
    through_sample: int
    stable_through_sample: int
    status: Literal["complete", "degraded"]
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
    | DiarizationUpdatedEvent
    | DiarizationStatusEvent
    | DiarizationDoneEvent
    | SpeechRailTranscriptionError
)


def decode_transcription_event(
    raw: Mapping[str, object], *, diarization_enabled: bool = False
) -> SpeechRailTranscriptionEvent:
    """Decode one transport-validated event using the v2.0.0 wire contract."""

    event_type = raw.get("type")
    if not isinstance(event_type, str) or not event_type:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")

    if event_type in DIARIZATION_EXTENSION_TYPES:
        if not diarization_enabled:
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        event_id, session_id, sequence = _event_metadata(raw)
        if event_type == DIARIZATION_UPDATED_TYPE:
            return _decode_diarization_updated(raw, event_id, session_id, sequence)
        if event_type == DIARIZATION_STATUS_TYPE:
            return _decode_diarization_status(raw, event_id, session_id, sequence)
        return _decode_diarization_done(raw, event_id, session_id, sequence)

    # Replaced literals are deliberately not aliases. They must fail closed.
    if event_type in {
        "speechrail.diarization.update",
        "speechrail.diarization.finalized",
        DIARIZATION_FINISH_TYPE,
    }:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")

    if event_type in _SESSION_NOOPS:
        return Noop(reason=event_type, item_id=_optional_item_id(raw.get("item_id")))
    if event_type == "input_audio_buffer.speech_started":
        return _decode_speech_boundary(raw, "audio_start_ms")
    if event_type == "input_audio_buffer.speech_stopped":
        return _decode_speech_boundary(raw, "audio_end_ms")
    if event_type == "conversation.item.input_audio_transcription.delta":
        return TranscriptionDelta(
            text=_require_text(raw.get("delta")),
            item_id=_optional_item_id(raw.get("item_id")),
        )
    if event_type == "conversation.item.input_audio_transcription.completed":
        if diarization_enabled:
            event_id, session_id, sequence = _event_metadata(raw)
            return _decode_completed_with_units(raw, event_id, session_id, sequence)
        if any(
            field in raw
            for field in ("audio_start_sample", "audio_end_sample", "attribution_units")
        ):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        return TranscriptionCompleted(
            transcript=_require_text(raw.get("transcript")),
            item_id=_optional_item_id(raw.get("item_id")),
        )
    if event_type == "conversation.item.input_audio_transcription.segment":
        if diarization_enabled:
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
        return _decode_segment(raw)
    if event_type in {
        "conversation.item.input_audio_transcription.failed",
        "error",
    }:
        return _decode_error(raw.get("error"))
    raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")


def _event_metadata(raw: Mapping[str, object]) -> tuple[str, str, int]:
    event_id = raw.get("event_id")
    session_id = raw.get("session_id")
    sequence = raw.get("sequence")
    if (
        not isinstance(event_id, str)
        or not event_id.strip()
        or len(event_id) > 128
        or not isinstance(session_id, str)
        or not session_id.strip()
        or len(session_id) > 128
        or isinstance(sequence, bool)
        or not isinstance(sequence, int)
        or sequence < 1
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return event_id, session_id, sequence


def _require_text(value: object) -> str:
    if not isinstance(value, str):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return value


def _optional_item_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return value


def _required_item_id(value: object) -> str:
    item_id = _optional_item_id(value)
    if item_id is None:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return item_id


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
        speaker=_decode_standard_speaker(raw.get("speaker")),
        start_ms=round(start * 1000),
        end_ms=round(end * 1000),
        item_id=_optional_item_id(raw.get("item_id")),
    )


def _decode_standard_speaker(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    return value


def _decode_anonymous_speaker(value: object) -> AnonymousSpeaker:
    if not isinstance(value, str) or value not in _ANONYMOUS_SPEAKERS:
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    return cast(AnonymousSpeaker, value)


def _decode_error(value: object) -> SpeechRailTranscriptionError:
    if not isinstance(value, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    code = value.get("code")
    if not isinstance(code, str) or not code.strip() or len(code) > 128:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    message = value.get("message")
    if message is None:
        message = ""
    elif not isinstance(message, str):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return SpeechRailTranscriptionError(code=code, message=message)


def _decode_completed_with_units(
    raw: Mapping[str, object], event_id: str, session_id: str, sequence: int
) -> TranscriptionCompleted:
    transcript = _require_text(raw.get("transcript"))
    start = _require_sample(raw.get("audio_start_sample"))
    end = _require_sample(raw.get("audio_end_sample"))
    if end < start:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return TranscriptionCompleted(
        transcript=transcript,
        item_id=_required_item_id(raw.get("item_id")),
        audio_start_sample=start,
        audio_end_sample=end,
        attribution_units=_decode_units(raw.get("attribution_units"), transcript),
        event_id=event_id,
        session_id=session_id,
        sequence=sequence,
    )


def _require_sample(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return value


def _decode_units(value: object, transcript: str) -> tuple[AttributionUnit, ...]:
    if not isinstance(value, list) or len(value) > _MAX_UNITS_PER_ITEM:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    units: list[AttributionUnit] = []
    expected_start = 0
    seen_uids: set[str] = set()
    for raw_unit in value:
        if not isinstance(raw_unit, dict):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        uid = raw_unit.get("segment_uid")
        if (
            not isinstance(uid, str)
            or not uid
            or len(uid) > _MAX_SEGMENT_UID_LENGTH
            or not uid.isascii()
            or uid in seen_uids
        ):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        seen_uids.add(uid)
        text_start = _require_sample(raw_unit.get("text_start"))
        text_end = _require_sample(raw_unit.get("text_end"))
        audio_start = _require_sample(raw_unit.get("audio_start_sample"))
        audio_end = _require_sample(raw_unit.get("audio_end_sample"))
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
                segment_uid=uid,
                text_start=text_start,
                text_end=text_end,
                audio_start_sample=audio_start,
                audio_end_sample=audio_end,
                timing_quality=timing,
            )
        )
    if expected_start != len(transcript):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return tuple(units)


def _decode_diarization_updated(
    raw: Mapping[str, object], event_id: str, session_id: str, sequence: int
) -> DiarizationUpdatedEvent:
    stable_through = _require_sample(raw.get("stable_through_sample"))
    raw_updates = raw.get("updates")
    if not isinstance(raw_updates, list) or len(raw_updates) > _MAX_UPDATES_PER_EVENT:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    updates = tuple(_decode_update(item) for item in raw_updates)
    if len({update.segment_uid for update in updates}) != len(updates):
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    return DiarizationUpdatedEvent(
        event_id=event_id,
        session_id=session_id,
        sequence=sequence,
        stable_through_sample=stable_through,
        updates=updates,
    )


def _decode_update(raw: object) -> DiarizationUpdate:
    if not isinstance(raw, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    uid = raw.get("segment_uid")
    revision = raw.get("revision")
    status = raw.get("status")
    if (
        not isinstance(uid, str)
        or not uid
        or len(uid) > _MAX_SEGMENT_UID_LENGTH
        or not uid.isascii()
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
        or status not in _PATCH_STATUSES
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
    speaker: AnonymousSpeaker | None
    raw_speaker = raw.get("speaker")
    if status == "unknown":
        if raw_speaker is not None:
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
        speaker = None
    else:
        speaker = _decode_anonymous_speaker(raw_speaker)
    candidates = _decode_candidates(raw.get("candidates"))
    return DiarizationUpdate(
        segment_uid=uid,
        revision=revision,
        status=status,
        speaker=speaker,
        coverage_ratio=_require_ratio(raw.get("coverage_ratio")),
        overlap_ratio=_require_ratio(raw.get("overlap_ratio")),
        candidates=candidates,
    )


def _require_ratio(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    ratio = float(value)
    if ratio != ratio or ratio in (float("inf"), float("-inf")) or not 0.0 <= ratio <= 1.0:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return ratio


def _decode_candidates(value: object) -> tuple[DiarizationCandidate, ...]:
    if not isinstance(value, list) or len(value) > 4:
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    candidates: list[DiarizationCandidate] = []
    seen: set[str] = set()
    for raw_candidate in value:
        if not isinstance(raw_candidate, dict):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        speaker = _decode_anonymous_speaker(raw_candidate.get("speaker"))
        if speaker in seen:
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR")
        seen.add(speaker)
        candidates.append(
            DiarizationCandidate(
                speaker=speaker,
                support_ratio=_require_ratio(raw_candidate.get("support_ratio")),
            )
        )
    return tuple(candidates)


def _decode_diarization_status(
    raw: Mapping[str, object], event_id: str, session_id: str, sequence: int
) -> DiarizationStatusEvent:
    status = raw.get("status")
    since_sample = _require_sample(raw.get("since_sample"))
    reason = raw.get("reason")
    if status != "degraded" or not isinstance(reason, str) or not reason.strip():
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return DiarizationStatusEvent(
        event_id=event_id,
        session_id=session_id,
        sequence=sequence,
        status="degraded",
        reason=reason,
        since_sample=since_sample,
    )


def _decode_diarization_done(
    raw: Mapping[str, object], event_id: str, session_id: str, sequence: int
) -> DiarizationDoneEvent:
    finalization_id = raw.get("finalization_id")
    through = _require_sample(raw.get("through_sample"))
    stable_through = _require_sample(raw.get("stable_through_sample"))
    status = raw.get("status")
    reason = raw.get("reason")
    last_update_sequence = raw.get("last_update_sequence")
    if (
        not isinstance(finalization_id, str)
        or not finalization_id.strip()
        or len(finalization_id) > 128
        or status not in _DONE_STATUSES
        or (status == "complete" and through != stable_through)
        or (reason is not None and (not isinstance(reason, str) or not reason.strip()))
        or isinstance(last_update_sequence, bool)
        or not isinstance(last_update_sequence, int)
        or last_update_sequence < 0
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return DiarizationDoneEvent(
        event_id=event_id,
        session_id=session_id,
        sequence=sequence,
        finalization_id=finalization_id,
        through_sample=through,
        stable_through_sample=stable_through,
        status=cast(Literal["complete", "degraded"], status),
        reason=reason,
        last_update_sequence=last_update_sequence,
    )
