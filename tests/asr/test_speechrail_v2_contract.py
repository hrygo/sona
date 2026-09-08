"""SpeechRail v2.0.0 OpenAI Realtime contract tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from sona.speechrail.transcription_events import (
    DiarizationDoneEvent,
    DiarizationStatusEvent,
    DiarizationUpdatedEvent,
    TranscriptionCompleted,
    decode_transcription_event,
)
from sona.speechrail.transport import SpeechRailProtocolError, SpeechRailRealtimeClient


def _envelope(event_type: str, sequence: int, **fields: object) -> str:
    return json.dumps(
        {
            "type": event_type,
            "event_id": f"evt-{sequence}",
            "session_id": "sess-v2",
            "sequence": sequence,
            **fields,
        }
    )


class _Connection:
    def __init__(self, incoming: list[str]) -> None:
        self.incoming = asyncio.Queue[str]()
        for item in incoming:
            self.incoming.put_nowait(item)
        self.sent: list[dict[str, object]] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        return await self.incoming.get()

    async def close(self) -> None:
        return None


def _client(connection: _Connection) -> SpeechRailRealtimeClient:
    async def factory(_url: str) -> _Connection:
        return connection

    return SpeechRailRealtimeClient(
        url="ws://speechrail.test/v1/realtime",
        connection_factory=factory,
    )


def _handshake(*, diarization: bool, unavailable: str | None = None) -> _Connection:
    incoming = [
        _envelope(
            "session.created",
            1,
            session={"id": "sess-v2", "capabilities": ["realtime"]},
        ),
        _envelope(
            "session.updated",
            2,
            session={"id": "sess-v2", "turn_detection": {"type": "manual"}},
        ),
    ]
    if diarization:
        if unavailable is None:
            incoming.append(
                _envelope(
                    "session.updated",
                    3,
                    session={
                        "id": "sess-v2",
                        "speechrail": {
                            "diarization": {
                                "enabled": True,
                                "version": 1,
                                "max_speakers": 4,
                            }
                        },
                    },
                )
            )
        else:
            incoming.append(
                _envelope(
                    "error",
                    3,
                    error={"code": unavailable, "message": unavailable},
                )
            )
    return _Connection(incoming)


@pytest.mark.asyncio
async def test_connect_confirms_baseline_before_one_namespaced_opt_in() -> None:
    connection = _handshake(diarization=True)
    client = _client(connection)

    await client.connect(
        language="Chinese",
        turn_detection={"type": "manual"},
        diarization_enabled=True,
    )

    assert connection.sent[0] == {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "manual"},
            "input_audio_transcription": {
                "model": "gpt-4o-transcribe",
                "language": "Chinese",
            },
        },
    }
    assert connection.sent[0]["session"].get("speechrail") is None
    assert connection.sent[1] == {
        "type": "session.update",
        "session": {"speechrail": {"diarization": {"enabled": True}}},
    }
    assert client.diarization_enabled is True


@pytest.mark.asyncio
async def test_opt_in_unavailable_keeps_baseline_asr_and_records_one_reason() -> None:
    connection = _handshake(diarization=True, unavailable="diarization_not_available")
    client = _client(connection)

    await client.connect(language="Chinese", diarization_enabled=True)

    assert client.diarization_enabled is False
    assert client.diarization_unavailable_reason == "diarization_not_available"
    assert len(connection.sent) == 2


@pytest.mark.asyncio
async def test_finish_is_one_shot_and_uses_v2_event_id() -> None:
    connection = _handshake(diarization=True)
    client = _client(connection)
    await client.connect(language="Chinese", diarization_enabled=True)

    await client.send_diarization_finish("finish-1")
    await client.send_diarization_finish("finish-1")

    assert connection.sent.count(
        {"type": "speechrail.diarization.finish", "event_id": "finish-1"}
    ) == 1
    with pytest.raises(SpeechRailProtocolError):
        await client.send_diarization_finish("finish-2")


def _completed() -> dict[str, object]:
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "event_id": "evt-10",
        "session_id": "sess-v2",
        "sequence": 10,
        "item_id": "item-1",
        "content_index": 0,
        "transcript": "同意🙂。",
        "audio_start_sample": 0,
        "audio_end_sample": 8000,
        "attribution_units": [
            {
                "segment_uid": "unit-1",
                "text_start": 0,
                "text_end": 2,
                "audio_start_sample": 0,
                "audio_end_sample": 4000,
                "timing_quality": "aligned",
            },
            {
                "segment_uid": "unit-2",
                "text_start": 2,
                "text_end": 4,
                "audio_start_sample": 4000,
                "audio_end_sample": 8000,
                "timing_quality": "aligned",
            },
        ],
    }


def _updated() -> dict[str, object]:
    return {
        "type": "speechrail.diarization.updated",
        "event_id": "evt-11",
        "session_id": "sess-v2",
        "sequence": 11,
        "stable_through_sample": 0,
        "updates": [
            {
                "segment_uid": "unit-1",
                "revision": 1,
                "status": "tentative",
                "speaker": "A",
                "coverage_ratio": 1.0,
                "overlap_ratio": 0.0,
                "candidates": [{"speaker": "A", "support_ratio": 1.0}],
            }
        ],
        "speaker_links": [],
    }


def test_v2_completed_and_updated_decode_with_session_metadata_and_code_points() -> None:
    completed = decode_transcription_event(_completed(), diarization_enabled=True)
    updated = decode_transcription_event(_updated(), diarization_enabled=True)

    assert isinstance(completed, TranscriptionCompleted)
    assert "".join(
        completed.transcript[unit.text_start : unit.text_end]
        for unit in completed.attribution_units
    ) == completed.transcript
    assert isinstance(updated, DiarizationUpdatedEvent)
    assert (updated.event_id, updated.session_id, updated.sequence) == (
        "evt-11",
        "sess-v2",
        11,
    )
    assert updated.updates[0].speaker == "A"


def test_v2_status_and_done_decode() -> None:
    status = decode_transcription_event(
        {
            "type": "speechrail.diarization.status",
            "event_id": "evt-12",
            "session_id": "sess-v2",
            "sequence": 12,
            "status": "degraded",
            "reason": "diarization_overloaded",
            "since_sample": 8000,
        },
        diarization_enabled=True,
    )
    done = decode_transcription_event(
        {
            "type": "speechrail.diarization.done",
            "event_id": "evt-13",
            "session_id": "sess-v2",
            "sequence": 13,
            "finalization_id": "finish-1",
            "through_sample": 8000,
            "stable_through_sample": 8000,
            "status": "degraded",
            "reason": "diarization_overloaded",
            "last_update_sequence": 11,
        },
        diarization_enabled=True,
    )

    assert isinstance(status, DiarizationStatusEvent)
    assert status.reason == "diarization_overloaded"
    assert isinstance(done, DiarizationDoneEvent)
    assert done.finalization_id == "finish-1"
    assert done.last_update_sequence == 11


@pytest.mark.parametrize(
    "event_type",
    [
        "speechrail.diarization.update",
        "speechrail.diarization.finalized",
    ],
)
def test_replaced_event_literals_are_rejected(event_type: str) -> None:
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(
            {
                "type": event_type,
                "event_id": "evt-old",
                "session_id": "sess-v2",
                "sequence": 1,
            },
            diarization_enabled=True,
        )


def test_missing_extension_envelope_metadata_is_rejected() -> None:
    raw = _updated()
    del raw["session_id"]
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(raw, diarization_enabled=True)


def test_duplicate_update_uid_in_one_event_is_rejected() -> None:
    raw = _updated()
    raw["updates"] = [raw["updates"][0], raw["updates"][0]]  # type: ignore[index]
    with pytest.raises(SpeechRailProtocolError):
        decode_transcription_event(raw, diarization_enabled=True)
