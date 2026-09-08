"""SpeechRail v2.0.0 diarization wire contract tests."""

from __future__ import annotations

import pytest

from sona.speechrail.transcription_events import (
    DiarizationDoneEvent,
    DiarizationStatusEvent,
    DiarizationUpdatedEvent,
    TranscriptionCompleted,
    decode_transcription_event,
)
from sona.speechrail.transport import SpeechRailProtocolError


def _completed() -> dict[str, object]:
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "event_id": "evt-completed",
        "session_id": "session-a",
        "sequence": 2,
        "item_id": "item-a",
        "transcript": "同意🙂。",
        "audio_start_sample": 0,
        "audio_end_sample": 8000,
        "attribution_units": [
            {
                "segment_uid": "unit-a",
                "text_start": 0,
                "text_end": 2,
                "audio_start_sample": 0,
                "audio_end_sample": 4000,
                "timing_quality": "aligned",
            },
            {
                "segment_uid": "unit-b",
                "text_start": 2,
                "text_end": 4,
                "audio_start_sample": 4000,
                "audio_end_sample": 8000,
                "timing_quality": "aligned",
            },
        ],
    }


def _updated(uid: str = "unit-a", revision: int = 1) -> dict[str, object]:
    return {
        "type": "speechrail.diarization.updated",
        "event_id": f"evt-update-{revision}",
        "session_id": "session-a",
        "sequence": 3,
        "stable_through_sample": 4000,
        "updates": [
            {
                "segment_uid": uid,
                "revision": revision,
                "status": "tentative" if revision == 1 else "stable",
                "speaker": "A",
                "coverage_ratio": 1.0,
                "overlap_ratio": 0.0,
                "candidates": [{"speaker": "A", "support_ratio": 1.0}],
            }
        ],
        # SpeechRail may include this diagnostic field; Sona deliberately ignores it.
        "speaker_links": [],
    }


def test_completed_units_tile_unicode_code_points() -> None:
    decoded = decode_transcription_event(_completed(), diarization_enabled=True)
    assert isinstance(decoded, TranscriptionCompleted)
    assert "".join(
        decoded.transcript[unit.text_start : unit.text_end]
        for unit in decoded.attribution_units
    ) == decoded.transcript


def test_update_retains_anonymous_label_and_metadata() -> None:
    decoded = decode_transcription_event(_updated(), diarization_enabled=True)
    assert isinstance(decoded, DiarizationUpdatedEvent)
    assert (decoded.event_id, decoded.session_id, decoded.sequence) == (
        "evt-update-1",
        "session-a",
        3,
    )
    assert decoded.updates[0].speaker == "A"


def test_unknown_update_requires_null_speaker() -> None:
    raw = _updated()
    update = raw["updates"][0]  # type: ignore[index]
    assert isinstance(update, dict)
    update["status"] = "unknown"
    update["speaker"] = None
    decoded = decode_transcription_event(raw, diarization_enabled=True)
    assert isinstance(decoded, DiarizationUpdatedEvent)
    assert decoded.updates[0].speaker is None


def test_status_and_done_are_typed() -> None:
    status = decode_transcription_event(
        {
            "type": "speechrail.diarization.status",
            "event_id": "evt-status",
            "session_id": "session-a",
            "sequence": 4,
            "status": "degraded",
            "reason": "profile_unavailable",
            "since_sample": 8000,
        },
        diarization_enabled=True,
    )
    done = decode_transcription_event(
        {
            "type": "speechrail.diarization.done",
            "event_id": "evt-done",
            "session_id": "session-a",
            "sequence": 5,
            "finalization_id": "finish-a",
            "through_sample": 8000,
            "stable_through_sample": 8000,
            "status": "degraded",
            "reason": "profile_unavailable",
            "last_update_sequence": 3,
        },
        diarization_enabled=True,
    )
    assert isinstance(status, DiarizationStatusEvent)
    assert isinstance(done, DiarizationDoneEvent)
    assert done.last_update_sequence == 3


@pytest.mark.parametrize(
    "event_type",
    ["speechrail.diarization.update", "speechrail.diarization.finalized"],
)
def test_replaced_event_literals_are_rejected(event_type: str) -> None:
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(
            {
                "type": event_type,
                "event_id": "old",
                "session_id": "session-a",
                "sequence": 1,
            },
            diarization_enabled=True,
        )


def test_diarization_events_are_rejected_when_not_opted_in() -> None:
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(_updated(), diarization_enabled=False)


def test_completed_without_units_is_rejected_when_opted_in() -> None:
    raw = _completed()
    raw["attribution_units"] = []
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(raw, diarization_enabled=True)
