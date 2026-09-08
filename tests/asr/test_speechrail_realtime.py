from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator

import pytest

from sona.asr.contracts import ASREvent, ASRSessionContext
from sona.asr.diagnostics import ASRDiagnostics
from sona.speechrail import (
    SpeechRailRealtimeClient,
    SpeechRailStreamingTranscriber,
)
from sona.speechrail.transcription_events import (
    SpeechRailTranscriptionError,
    TranscriptionCompleted,
    TranscriptionDelta,
    decode_transcription_event,
)
from sona.speechrail.transport import SpeechRailOpenAITransport


class FakeConnection:
    uri = "ws://speechrail.test/v1/realtime"

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []
        self.commit_sent = asyncio.Event()
        self._messages_available = asyncio.Event()
        self._messages = [*_session_events(),
            _transcription_delta("你好", sequence=4),
            _transcription_completed("你好世界", sequence=5),
        ]
        self._messages_available.set()

    async def send(self, payload: str) -> None:
        event = json.loads(payload)
        self.sent.append(event)
        if event.get("type") == "input_audio_buffer.commit":
            self.commit_sent.set()

    async def recv(self) -> str:
        while not self._messages:
            self._messages_available.clear()
            await self._messages_available.wait()
        message = self._messages.pop(0)
        if self._messages:
            self._messages_available.set()
        return json.dumps(message)

    def add_messages(self, *messages: dict[str, object]) -> None:
        self._messages.extend(messages)
        self._messages_available.set()

    async def close(self) -> None:
        return None


class AckDuringClearConnection(FakeConnection):
    async def send(self, payload: str) -> None:
        await super().send(payload)
        event = json.loads(payload)
        if event.get("type") == "input_audio_buffer.clear":
            self.add_messages(_envelope("input_audio_buffer.cleared", 6))
            await asyncio.sleep(0)


class Websockets17StyleConnection:
    """Minimal websockets 17 connection shape: no public ``uri`` attribute."""

    async def send(self, payload: str) -> None:
        return None

    async def recv(self) -> str:
        raise AssertionError("recv should not be called by the transport URI test")

    async def close(self) -> None:
        return None


def _session_events() -> list[dict[str, object]]:
    return [
        _envelope("session.created", 1, session={"id": "sess-1"}),
        _envelope("session.updated", 2, session={"id": "sess-1"}),
        _envelope("conversation.created", 3, conversation={"id": "conv-1"}),
    ]


def _envelope(event_type: str, sequence: int, **payload: object) -> dict[str, object]:
    return {
        "type": event_type,
        "event_id": f"evt-{sequence}",
        "session_id": "sess-1",
        "sequence": sequence,
        **payload,
    }


def _transcription_delta(text: str, *, sequence: int) -> dict[str, object]:
    return _envelope(
        "conversation.item.input_audio_transcription.delta",
        sequence,
        item_id="item-1",
        content_index=0,
        delta=text,
    )


def _transcription_completed(transcript: str, *, sequence: int) -> dict[str, object]:
    return _envelope(
        "conversation.item.input_audio_transcription.completed",
        sequence,
        item_id="item-1",
        content_index=0,
        transcript=transcript,
    )


def _speech_started(audio_start_ms: int, *, sequence: int) -> dict[str, object]:
    return _envelope(
        "input_audio_buffer.speech_started",
        sequence,
        item_id="item-1",
        audio_start_ms=audio_start_ms,
    )


def _speech_stopped(audio_end_ms: int, *, sequence: int) -> dict[str, object]:
    return _envelope(
        "input_audio_buffer.speech_stopped",
        sequence,
        item_id="item-1",
        audio_end_ms=audio_end_ms,
    )


def _segment(
    text: str,
    *,
    speaker: str | None,
    sequence: int,
    start: float = 0.0,
    end: float = 1.0,
) -> dict[str, object]:
    return _envelope(
        "conversation.item.input_audio_transcription.segment",
        sequence,
        item_id="item-1",
        content_index=0,
        id=f"seg-{sequence}",
        text=text,
        speaker=speaker,
        start=start,
        end=end,
    )


def test_transport_uri_uses_configured_url_when_connection_has_no_uri() -> None:
    async def scenario() -> None:
        connection = Websockets17StyleConnection()
        transport = SpeechRailOpenAITransport(
            url="ws://speechrail.test/v1/realtime",
            connection_factory=lambda _: _immediate(connection),
        )

        await transport.connect()

        assert transport.uri == "ws://speechrail.test/v1/realtime"
        await transport.close()

    asyncio.run(scenario())


def test_meeting_adapter_uses_longer_vad_silence_window() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
            language="Chinese",
        )

        await adapter.connect()

        session_update = next(
            event for event in connection.sent if event.get("type") == "session.update"
        )
        session = session_update["session"]
        assert isinstance(session, dict)
        turn_detection = session["turn_detection"]
        assert isinstance(turn_detection, dict)
        assert turn_detection["silence_duration_ms"] == 900

    asyncio.run(scenario())


def test_meeting_extensions_adapter_uses_extensions_vad_silence_window() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = _extensions_session_events()
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(
                source_epoch=2,
                offset_ms=0,
                purpose="meeting",
                diarization_group_id="a" * 64,
            ),
            language="Chinese",
            diarization_extensions=True,
        )

        await adapter.connect()

        session_update = next(
            event for event in connection.sent if event.get("type") == "session.update"
        )
        session = session_update["session"]
        assert isinstance(session, dict)
        turn_detection = session["turn_detection"]
        assert isinstance(turn_detection, dict)
        assert turn_detection["silence_duration_ms"] == 1_000

    asyncio.run(scenario())


def test_subtitle_adapter_keeps_default_vad_silence_window() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="subtitles"),
            language="Chinese",
        )

        await adapter.connect()

        session_update = next(
            event for event in connection.sent if event.get("type") == "session.update"
        )
        session = session_update["session"]
        assert isinstance(session, dict)
        turn_detection = session["turn_detection"]
        assert isinstance(turn_detection, dict)
        assert turn_detection["silence_duration_ms"] == 400

    asyncio.run(scenario())


def test_subtitle_extensions_request_keeps_default_extensions_vad_window() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="subtitles"),
            language="Chinese",
            diarization_extensions=True,
        )

        await adapter.connect()

        session_update = next(
            event for event in connection.sent if event.get("type") == "session.update"
        )
        session = session_update["session"]
        assert isinstance(session, dict)
        turn_detection = session["turn_detection"]
        assert isinstance(turn_detection, dict)
        assert turn_detection["silence_duration_ms"] == 600

    asyncio.run(scenario())


def test_streaming_adapter_maps_openai_snapshot_and_pcm_append() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        client = SpeechRailRealtimeClient(
            url=connection.uri,
            connection_factory=lambda _: _immediate(connection),
        )
        adapter = SpeechRailStreamingTranscriber(
            client=client,
            context=ASRSessionContext(source_epoch=2, offset_ms=1_000, purpose="subtitles"),
            language="Chinese",
        )

        await adapter.connect()
        await adapter.send_audio(b"\x00\x00")
        await client.commit()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        snapshot = await anext(events)
        final = await anext(events)

        assert snapshot.kind == "snapshot"
        assert snapshot.window is not None
        assert snapshot.window.partial == "你好"
        assert final.kind == "final"
        assert final.window is not None
        assert final.window.segments[0].text == "你好世界"
        assert final.window.segments[0].speaker_key == "epoch:2:speaker:0"
        assert (
            final.window.segments[0].start_ms,
            final.window.segments[0].end_ms,
        ) == (1_000, 1_480)
        assert "input_audio_transcription" in connection.sent[0]["session"]
        assert connection.sent[1]["audio"] == base64.b64encode(b"\x00\x00").decode()
        assert connection.sent[2]["type"] == "input_audio_buffer.commit"

    asyncio.run(scenario())


def test_meeting_adapter_requests_diarization_and_preserves_anonymous_speaker_label() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [*_session_events(),
            _transcription_delta("你好", sequence=4),
            _segment("你好世界", speaker="spk_02", sequence=5),
            _transcription_completed("你好世界", sequence=6),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(
                source_epoch=2,
                offset_ms=1_000,
                purpose="meeting",
                speaker_count_hint=2,
                diarization_group_id="a" * 64,
            ),
            language="Chinese",
        )

        await adapter.connect()
        await adapter.send_audio(b"\x00\x00")
        await adapter._client.commit()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        await anext(events)
        final = await anext(events)

        transcription = connection.sent[0]["session"]["input_audio_transcription"]
        assert isinstance(transcription, dict)
        assert transcription["diarization"] == {
            "enabled": True,
            "finalize": True,
            "speaker_count_hint": 2,
            "group_id": "a" * 64,
        }
        assert final.window is not None
        assert final.window.segments[0].speaker_key == f"group:{'a' * 64}:speaker:spk_02"
        assert final.window.segments[0].text == "你好世界"

    asyncio.run(scenario())


def test_meeting_adapter_defaults_epoch_speaker_when_no_group_id() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [*_session_events(),
            _segment("你好世界", speaker="spk_01", sequence=4),
            _transcription_completed("你好世界", sequence=5),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=1_000, purpose="meeting"),
            language="Chinese",
        )

        await adapter.connect()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        final = await anext(events)

        assert final.window is not None
        assert final.window.segments[0].speaker_key == "epoch:2:speaker:spk_01"

    asyncio.run(scenario())


def test_finish_waits_for_the_confirmed_transcript_window() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [*_session_events(),
            _segment("你好世界", speaker="spk_01", sequence=4),
            _transcription_completed("你好世界", sequence=5),
            _envelope("input_audio_buffer.cleared", 6),
        ]
        client = SpeechRailRealtimeClient(
            url=connection.uri,
            connection_factory=lambda _: _immediate(connection),
        )
        adapter = SpeechRailStreamingTranscriber(
            client=client,
            context=ASRSessionContext(source_epoch=2, offset_ms=1_000, purpose="meeting"),
            language="Chinese",
        )
        await adapter.connect()
        await adapter.send_audio(b"\x00\x00")

        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        collected: list[ASREvent] = []

        async def collect_events() -> None:
            collected.extend([event async for event in events])

        event_task = asyncio.create_task(collect_events())
        completed = await adapter.finish()
        await event_task

        assert completed.segments[0].text == "你好世界"
        assert [event.kind for event in collected] == ["final"]

    asyncio.run(scenario())


def test_finish_drains_old_and_empty_eof_completed_before_clear_ack() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = _session_events()
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=1_000, purpose="meeting"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        collected: list[ASREvent] = []

        async def collect_tail() -> None:
            collected.extend([event async for event in events])

        event_task = asyncio.create_task(collect_tail())

        async def append_tail_after_commit() -> None:
            await connection.commit_sent.wait()
            connection.add_messages(
                _transcription_completed("旧轮", sequence=4),
                _transcription_completed("", sequence=5),
                _envelope("input_audio_buffer.cleared", 6),
            )

        producer = asyncio.create_task(append_tail_after_commit())
        completed = await adapter.finish()
        await producer
        await event_task

        assert completed.segments[0].text == "旧轮"
        assert [event.kind for event in collected] == ["final", "final"]
        assert collected[0].window is not None
        assert collected[0].window.segments[0].text == "旧轮"
        assert collected[1].window is not None
        assert collected[1].window.segments == ()
        assert connection._messages == []
        assert [event["type"] for event in connection.sent[-2:]] == [
            "input_audio_buffer.commit",
            "input_audio_buffer.clear",
        ]

    asyncio.run(scenario())


def test_finish_is_idempotent_after_clear_barrier() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_session_events(),
            _transcription_completed("最后一句", sequence=4),
            _envelope("input_audio_buffer.cleared", 5),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"

        event_task = asyncio.create_task(_drain_events(events))
        first = await adapter.finish()
        second = await adapter.finish()
        await event_task

        assert first == second
        assert [event["type"] for event in connection.sent].count("input_audio_buffer.commit") == 1
        assert [event["type"] for event in connection.sent].count("input_audio_buffer.clear") == 1

    asyncio.run(scenario())


def test_finish_accepts_clear_ack_arriving_before_clear_send_returns() -> None:
    async def scenario() -> None:
        connection = AckDuringClearConnection()
        connection._messages = [
            *_session_events(),
            _transcription_completed("尾句", sequence=4),
            _transcription_completed("", sequence=5),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        event_task = asyncio.create_task(_drain_events(events))

        completed = await adapter.finish()
        await event_task

        assert completed.segments[0].text == "尾句"
        assert connection._messages == []

    asyncio.run(scenario())


def test_events_yields_multiple_turns_without_terminating_until_finish() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_session_events(),
            # Turn 1
            _transcription_delta("第一句", sequence=4),
            _transcription_completed("第一句完成", sequence=5),
            # Turn 2
            _transcription_delta("第二句", sequence=6),
            _transcription_completed("第二句完成", sequence=7),
        ]
        client = SpeechRailRealtimeClient(
            url=connection.uri,
            connection_factory=lambda _: _immediate(connection),
        )
        adapter = SpeechRailStreamingTranscriber(
            client=client,
            context=ASRSessionContext(source_epoch=2, offset_ms=1_000, purpose="subtitles"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()

        assert (await anext(events)).kind == "ready"

        # Turn 1 snapshot & final
        e1_snap = await anext(events)
        assert e1_snap.kind == "snapshot" and e1_snap.window and e1_snap.window.partial == "第一句"
        e1_final = await anext(events)
        assert e1_final.kind == "final" and e1_final.window
        assert e1_final.window.segments[0].text == "第一句完成"

        # Turn 2: events() must still be alive and yield Turn 2!
        e2_snap = await anext(events)
        assert e2_snap.kind == "snapshot" and e2_snap.window and e2_snap.window.partial == "第二句"
        e2_final = await anext(events)
        assert e2_final.kind == "final" and e2_final.window
        assert e2_final.window.segments[0].text == "第二句完成"

        # Finish terminates the stream
        adapter._commit_sent = True
        # Once closed / finished, events terminates
        await adapter.close()

    asyncio.run(scenario())


def test_streaming_adapter_normalizes_each_segment_from_speech_start_clock() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_session_events(),
            # SpeechRail segment timestamps restart at zero for every committed item.
            _speech_started(1_024, sequence=4),
            _segment("第一句", speaker="spk_01", sequence=5, start=0.1, end=0.8),
            _speech_stopped(2_848, sequence=6),
            _transcription_completed("第一句", sequence=7),
            _speech_started(3_008, sequence=8),
            _segment("第二句", speaker="spk_01", sequence=9, start=0.0, end=0.6),
            _transcription_completed("第二句", sequence=10),
        ]
        client = SpeechRailRealtimeClient(
            url=connection.uri,
            connection_factory=lambda _: _immediate(connection),
        )
        adapter = SpeechRailStreamingTranscriber(
            client=client,
            context=ASRSessionContext(source_epoch=3, offset_ms=1_000, purpose="meeting"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()

        assert (await anext(events)).kind == "ready"
        first = await anext(events)
        second = await anext(events)

        assert first.kind == "final" and first.window is not None
        assert second.kind == "final" and second.window is not None
        first_segment = first.window.segments[0]
        second_segment = second.window.segments[0]
        assert (first_segment.start_ms, first_segment.end_ms) == (1_100, 1_800)
        assert (second_segment.start_ms, second_segment.end_ms) == (3_848, 4_448)

    asyncio.run(scenario())


def test_streaming_adapter_accumulates_incremental_deltas_until_completed() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_session_events(),
            _transcription_delta("第一", sequence=4),
            _transcription_delta("句", sequence=5),
            _transcription_completed("第一句", sequence=6),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=3, offset_ms=0, purpose="subtitles"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()

        assert (await anext(events)).kind == "ready"
        first_snapshot = await anext(events)
        second_snapshot = await anext(events)
        final = await anext(events)

        assert first_snapshot.window is not None
        assert second_snapshot.window is not None
        assert first_snapshot.window.partial == "第一"
        assert second_snapshot.window.partial == "第一句"
        assert final.kind == "final"

    asyncio.run(scenario())


def test_client_reconnects_with_a_new_session_sequence() -> None:
    async def scenario() -> None:
        first = FakeConnection()
        second = FakeConnection()
        connections = iter((first, second))
        client = SpeechRailRealtimeClient(
            url=first.uri,
            connection_factory=lambda _: _immediate(next(connections)),
        )

        await client.connect(language="Chinese")
        await client.close()
        await client.connect(language="Chinese")

        assert client.uri == second.uri

    asyncio.run(scenario())


def test_client_rejects_events_from_another_session() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = _session_events()
        connection._messages[1]["session_id"] = "sess-other"
        client = SpeechRailRealtimeClient(
            url=connection.uri,
            connection_factory=lambda _: _immediate(connection),
        )

        await client.connect(language="Chinese")
        assert (await client.receive())["type"] == "session.created"

        try:
            await client.receive()
        except RuntimeError as error:
            assert str(error) == "SPEECHRAIL_SESSION_MISMATCH"
        else:
            raise AssertionError("mismatched session event was accepted")

    asyncio.run(scenario())


def test_finish_times_out_when_no_event_reader_receives_a_final_result() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = _session_events()
        client = SpeechRailRealtimeClient(
            url=connection.uri,
            connection_factory=lambda _: _immediate(connection),
        )
        adapter = SpeechRailStreamingTranscriber(
            client=client,
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
            language="Chinese",
            finish_timeout_secs=0.01,
        )
        await adapter.connect()

        try:
            await adapter.finish()
        except TimeoutError as error:
            assert str(error) == "SPEECHRAIL_FINAL_TIMEOUT: final result was not received"
        else:
            raise AssertionError("finish unexpectedly completed without a final event")

    asyncio.run(scenario())


def _collect_stream_events(
    connection: FakeConnection,
    context: ASRSessionContext,
) -> list[ASREvent]:
    async def scenario() -> list[ASREvent]:
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=context,
            language="Chinese",
        )
        await adapter.connect()
        collected: list[ASREvent] = []
        async for event in adapter.events():
            collected.append(event)
            if event.kind in {"final", "error"}:
                break
        return collected

    return asyncio.run(scenario())


def test_streaming_adapter_matches_decoder_for_delta_and_completed() -> None:
    async def scenario() -> None:
        delta_raw = {
            "type": "conversation.item.input_audio_transcription.delta",
            "delta": "你好",
        }
        completed_raw = {
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": "你好世界",
        }
        decoded_delta = decode_transcription_event(delta_raw)
        decoded_completed = decode_transcription_event(completed_raw)
        assert isinstance(decoded_delta, TranscriptionDelta)
        assert isinstance(decoded_completed, TranscriptionCompleted)

        connection = FakeConnection()
        connection._messages = [*_session_events(),
            _transcription_delta(decoded_delta.text, sequence=4),
            _transcription_completed(decoded_completed.transcript, sequence=5),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="subtitles"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        snapshot = await anext(events)
        final = await anext(events)

        assert snapshot.kind == "snapshot"
        assert snapshot.window is not None
        assert snapshot.window.partial == decoded_delta.text
        assert final.kind == "final"
        assert final.window is not None
        assert final.window.segments[0].text == decoded_completed.transcript
        assert final.window.segments[0].speaker_key == "epoch:2:speaker:0"

    asyncio.run(scenario())


def test_streaming_adapter_matches_decoder_for_error_event() -> None:
    error_raw = {"type": "error", "error": {"code": "speechrail_error", "message": "boom"}}
    decoded = decode_transcription_event(error_raw)
    assert isinstance(decoded, SpeechRailTranscriptionError)
    assert decoded.code == "speechrail_error"

    connection = FakeConnection()
    connection._messages = [*_session_events(),
        _envelope("error", 4, error={"code": "speechrail_error", "message": "boom"})
    ]
    events = _collect_stream_events(
        connection,
        ASRSessionContext(source_epoch=2, offset_ms=0, purpose="subtitles"),
    )

    assert [event.kind for event in events] == ["ready", "error"]
    assert events[1].error_code == "SPEECHRAIL_REQUEST_FAILED"
    assert events[1].error_message == "SpeechRail rejected the transcription request"


def test_streaming_adapter_reports_protocol_error_for_delta_without_text() -> None:
    connection = FakeConnection()
    connection._messages = [*_session_events(),
        _envelope("conversation.item.input_audio_transcription.delta", 4)
    ]
    events = _collect_stream_events(
        connection,
        ASRSessionContext(source_epoch=2, offset_ms=0, purpose="subtitles"),
    )

    assert events[-1].kind == "error"
    assert events[-1].error_code == "SPEECHRAIL_PROTOCOL_ERROR"
    assert events[-1].error_message == "SpeechRail returned a transcription delta without text"


def test_streaming_adapter_accepts_empty_completed_transcript_as_no_new_text() -> None:
    connection = FakeConnection()
    connection._messages = [*_session_events(),
        _envelope("conversation.item.input_audio_transcription.completed", 4, transcript="")
    ]
    events = _collect_stream_events(
        connection,
        ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
    )

    assert [event.kind for event in events] == ["ready", "final"]
    assert events[-1].window is not None
    assert events[-1].window.segments == ()


def test_streaming_adapter_records_empty_completion_without_storing_text() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        diagnostics = ASRDiagnostics()
        connection._messages = [
            *_session_events(),
            _envelope("input_audio_buffer.committed", 4),
            _transcription_completed("", sequence=5),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
            language="Chinese",
            diagnostics=diagnostics,
        )
        await adapter.connect()
        await adapter.send_audio(b"\x00\x00" * 160)
        events = adapter.events()

        assert (await anext(events)).kind == "ready"
        assert (await anext(events)).kind == "final"
        await events.aclose()

        assert adapter.diagnostics is diagnostics
        assert diagnostics.snapshot() == {
            "sent_samples": 160,
            "partial_events": 0,
            "empty_completed": 1,
            "nonempty_completed": 0,
            "committed_events": 1,
            "reconnects": 0,
            "protocol_errors": 0,
        }

    asyncio.run(scenario())


@pytest.mark.parametrize("text", ["嗯", "对", "好", "嗯，我同意"])
def test_streaming_adapter_preserves_real_short_answers(text: str) -> None:
    connection = FakeConnection()
    connection._messages = [*_session_events(),
        _transcription_completed(text, sequence=4),
    ]
    events = _collect_stream_events(
        connection,
        ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
    )

    assert [event.kind for event in events] == ["ready", "final"]
    assert events[-1].window is not None
    assert events[-1].window.segments[0].text == text
    assert events[-1].window.segments[0].speaker_key == "epoch:2:speaker:0"


def test_streaming_adapter_uses_vad_bounds_for_completed_without_segments() -> None:
    connection = FakeConnection()
    connection._messages = [
        *_session_events(),
        _speech_started(1_200, sequence=4),
        _speech_stopped(1_900, sequence=5),
        _transcription_completed("无 segment", sequence=6),
    ]
    events = _collect_stream_events(
        connection,
        ASRSessionContext(source_epoch=2, offset_ms=3_000, purpose="meeting"),
    )

    final = events[-1]
    assert final.window is not None
    segment = final.window.segments[0]
    assert (segment.start_ms, segment.end_ms) == (4_200, 4_900)


def test_streaming_adapter_fallback_starts_after_previous_confirmed_segment() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_session_events(),
            _segment("已确认", speaker="spk_01", sequence=4, start=0.5, end=1.5),
            _transcription_completed("已确认", sequence=5),
            _transcription_completed("无边界", sequence=6),
        ]
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=1_000, purpose="meeting"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()

        assert (await anext(events)).kind == "ready"
        first = await anext(events)
        second = await anext(events)

        assert first.window is not None
        assert second.window is not None
        first_segment = first.window.segments[0]
        second_segment = second.window.segments[0]
        assert (first_segment.start_ms, first_segment.end_ms) == (1_500, 2_500)
        assert second_segment.start_ms >= first_segment.end_ms
        assert 0 < second_segment.end_ms - second_segment.start_ms <= 10_000

    asyncio.run(scenario())


def test_finish_keeps_last_confirmed_after_partial_and_empty_completed() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = _session_events()
        adapter = SpeechRailStreamingTranscriber(
            client=SpeechRailRealtimeClient(
                url=connection.uri,
                connection_factory=lambda _: _immediate(connection),
            ),
            context=ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
            language="Chinese",
        )
        await adapter.connect()
        events = adapter.events()
        assert (await anext(events)).kind == "ready"
        collected: list[ASREvent] = []

        async def collect_tail() -> None:
            collected.extend([event async for event in events])

        event_task = asyncio.create_task(collect_tail())

        async def append_tail_after_commit() -> None:
            await connection.commit_sent.wait()
            connection.add_messages(
                _transcription_completed("确认 A", sequence=4),
                _transcription_delta("partial B", sequence=5),
                _transcription_completed("", sequence=6),
                _envelope("input_audio_buffer.cleared", 7),
            )

        producer = asyncio.create_task(append_tail_after_commit())
        completed = await adapter.finish()
        await producer
        await event_task

        assert completed.partial == ""
        assert [segment.text for segment in completed.segments] == ["确认 A"]
        assert [event.kind for event in collected] == ["final", "snapshot", "final"]
        assert collected[1].window is not None
        assert collected[1].window.partial == "partial B"
        assert collected[2].window is not None
        assert collected[2].window.partial == ""
        assert collected[2].window.segments == ()

    asyncio.run(scenario())


def test_streaming_adapter_falls_back_for_diarized_segment_without_speaker() -> None:
    connection = FakeConnection()
    connection._messages = [*_session_events(),
        _segment("你好", speaker=None, sequence=4),
        _transcription_completed("你好", sequence=5),
    ]
    events = _collect_stream_events(
        connection,
        ASRSessionContext(source_epoch=2, offset_ms=0, purpose="meeting"),
    )

    assert [event.kind for event in events] == ["ready", "final"]
    assert events[-1].window is not None
    assert events[-1].window.segments[0].text == "你好"
    assert events[-1].window.segments[0].speaker_key == "epoch:2:speaker:0"


async def _next_final(events: AsyncIterator[ASREvent]) -> ASREvent:
    async for event in events:
        if event.kind == "final":
            return event
    raise AssertionError("adapter ended without a final event")


async def _drain_events(events: AsyncIterator[ASREvent]) -> list[ASREvent]:
    return [event async for event in events]


async def _immediate(connection: FakeConnection) -> FakeConnection:
    return connection


# ---------------------------------------------------------------------------
# S1: SPK-E2E-1 扩展模式（协商、样本时间线、修订连续性、断线）
# ---------------------------------------------------------------------------

_EXTENSIONS_CONTRACT = {
    "version": 1,
    "timebase": "session_samples",
    "sample_rate": 16000,
    "max_speakers": 4,
    "max_item_duration_ms": 8000,
    "max_revision_delay_ms": 3000,
    "group_generation": "generation_example",
}


def _extensions_session_events() -> list[dict[str, object]]:
    return [
        _envelope(
            "session.created",
            1,
            session={"id": "sess-1", "capabilities": ["speechrail.diarization.v1"]},
        ),
        _envelope(
            "session.updated",
            2,
            session={"id": "sess-1", "diarization_contract": _EXTENSIONS_CONTRACT},
        ),
    ]


def _extensions_completed(
    transcript: str,
    *,
    sequence: int,
    item_id: str,
    start_sample: int,
    end_sample: int,
    units: list[dict[str, object]],
) -> dict[str, object]:
    return _envelope(
        "conversation.item.input_audio_transcription.completed",
        sequence,
        item_id=item_id,
        content_index=0,
        transcript=transcript,
        audio_start_sample=start_sample,
        audio_end_sample=end_sample,
        attribution_units=units,
    )


def _unit(
    uid: str,
    *,
    text_start: int,
    text_end: int,
    start_sample: int,
    end_sample: int,
    timing: str = "aligned",
) -> dict[str, object]:
    return {
        "segment_uid": uid,
        "text_start": text_start,
        "text_end": text_end,
        "audio_start_sample": start_sample,
        "audio_end_sample": end_sample,
        "timing_quality": timing,
    }


def _diarization_update(
    *,
    sequence: int,
    stable_through: int,
    updates: list[dict[str, object]],
) -> dict[str, object]:
    return _envelope(
        "speechrail.diarization.update",
        sequence,
        group_generation="generation_example",
        stable_through_sample=stable_through,
        updates=updates,
        speaker_links=[],
    )


def _patch(
    uid: str,
    *,
    revision: int,
    status: str = "stable",
    speaker: str | None = "spk_01",
) -> dict[str, object]:
    return {
        "segment_uid": uid,
        "revision": revision,
        "status": status,
        "speaker": speaker,
        "coverage_ratio": 0.95,
        "overlap_ratio": 0.0,
        "candidates": ([] if speaker is None else [{"speaker": speaker, "support_ratio": 0.95}]),
    }


def _extensions_adapter(connection: FakeConnection, *, offset_ms: int = 0):
    client = SpeechRailRealtimeClient(
        url=connection.uri,
        connection_factory=lambda _: _immediate(connection),
    )
    adapter = SpeechRailStreamingTranscriber(
        client=client,
        context=ASRSessionContext(
            source_epoch=3,
            offset_ms=offset_ms,
            purpose="meeting",
            diarization_group_id="b" * 64,
        ),
        language="zh",
        diarization_extensions=True,
    )
    return adapter, client


async def _drain_ready(events: AsyncIterator[ASREvent]) -> None:
    first = await anext(events)
    assert first.kind == "ready"


def test_extensions_mode_negotiates_and_maps_units_with_unknown_speaker() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_extensions_session_events(),
            _extensions_completed(
                "同意。",
                sequence=4,
                item_id="item-1",
                start_sample=48000,
                end_sample=56000,
                units=[_unit("seg_1", text_start=0, text_end=3,
                             start_sample=49600, end_sample=52800)],
            ),
        ]
        adapter, client = _extensions_adapter(connection, offset_ms=1_000)

        await adapter.connect()
        assert adapter.extensions_negotiated is True
        assert client.diarization_contract is not None
        events = adapter.events()
        await _drain_ready(events)
        final = await anext(events)

        assert final.kind == "final"
        assert final.window is not None
        assert final.window.source_session_id == "sess-1"
        segment = final.window.segments[0]
        # 新模式不拼 group+label 当持久身份；样本域换算只加一次 epoch offset。
        assert segment.speaker_key == "unknown"
        assert segment.source_uid == "seg_1"
        assert segment.start_ms == 1_000 + 49_600 // 16
        assert segment.end_ms == 1_000 + 52_800 // 16
        assert segment.text == "同意。"
        # 正文单元拼接逐字等于 canonical text。
        assert segment.text == "同意。"

    asyncio.run(scenario())


def test_extensions_mode_second_commit_keeps_sample_timeline() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_extensions_session_events(),
            _extensions_completed(
                "第一句说完了",
                sequence=4,
                item_id="item-1",
                start_sample=48000,
                end_sample=56000,
                units=[
                    _unit("s1", text_start=0, text_end=3, start_sample=48000, end_sample=52000),
                    _unit("s2", text_start=3, text_end=6, start_sample=52000, end_sample=56000),
                ],
            ),
            _extensions_completed(
                "第二句",
                sequence=6,
                item_id="item-2",
                start_sample=104000,
                end_sample=120000,
                units=[_unit("s3", text_start=0, text_end=3,
                             start_sample=104000, end_sample=120000)],
            ),
        ]
        adapter, _ = _extensions_adapter(connection, offset_ms=2_000)
        await adapter.connect()
        events = adapter.events()
        await _drain_ready(events)
        first = await anext(events)
        second = await anext(events)

        assert first.window is not None and second.window is not None
        assert [s.end_ms for s in first.window.segments] == [
            2_000 + 52_000 // 16,
            2_000 + 56_000 // 16,
        ]
        # 第二个 commit 的时间不回零、不双加 offset。
        assert second.window.segments[0].start_ms == 2_000 + 104_000 // 16
        assert second.window.segments[0].start_ms > first.window.segments[-1].end_ms

    asyncio.run(scenario())


def test_extensions_mode_vad_long_silence_keeps_clock() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        # 静音 16 分钟：第二 item 的样本区间整体后移，不压缩不重置。
        silence_samples = 16 * 60 * 16_000
        second_start = 56_000 + silence_samples
        connection._messages = [
            *_extensions_session_events(),
            _extensions_completed(
                "静音前",
                sequence=4,
                item_id="item-1",
                start_sample=48000,
                end_sample=56000,
                units=[_unit("a", text_start=0, text_end=3,
                             start_sample=48000, end_sample=56000)],
            ),
            _extensions_completed(
                "静音后",
                sequence=5,
                item_id="item-2",
                start_sample=second_start,
                end_sample=second_start + 16_000,
                units=[_unit("b", text_start=0, text_end=3,
                             start_sample=second_start, end_sample=second_start + 16_000)],
            ),
        ]
        adapter, _ = _extensions_adapter(connection)
        await adapter.connect()
        events = adapter.events()
        await _drain_ready(events)
        first = await anext(events)
        second = await anext(events)

        gap_ms = second.window.segments[0].start_ms - first.window.segments[0].end_ms
        assert gap_ms >= 16 * 60 * 1000 - 1_000

    asyncio.run(scenario())


def test_extensions_mode_revision_continuity_and_conflict() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_extensions_session_events(),
            _extensions_completed(
                "你好",
                sequence=4,
                item_id="item-1",
                start_sample=0,
                end_sample=32_000,
                units=[_unit("u1", text_start=0, text_end=2,
                             start_sample=1000, end_sample=30_000)],
            ),
            _diarization_update(sequence=5, stable_through=1000,
                                updates=[_patch("u1", revision=1)]),
            _diarization_update(sequence=6, stable_through=1000,
                                updates=[_patch("u1", revision=2)]),
            # 同 revision 同内容：幂等忽略。
            _diarization_update(sequence=7, stable_through=1000,
                                updates=[_patch("u1", revision=2)]),
            _extensions_completed(
                "继续",
                sequence=8,
                item_id="item-2",
                start_sample=32_000,
                end_sample=48_000,
                units=[_unit("u2", text_start=0, text_end=2,
                             start_sample=32_000, end_sample=48_000)],
            ),
            # 跳号：协议错误——停止分人，保留正文。
            _diarization_update(sequence=9, stable_through=1000,
                                updates=[_patch("u1", revision=4)]),
            _extensions_completed(
                "还在",
                sequence=10,
                item_id="item-3",
                start_sample=48_000,
                end_sample=64_000,
                units=[_unit("u3", text_start=0, text_end=2,
                             start_sample=48_000, end_sample=64_000)],
            ),
        ]
        adapter, _ = _extensions_adapter(connection)
        await adapter.connect()
        events = adapter.events()
        await _drain_ready(events)

        final_1 = await anext(events)
        assert final_1.kind == "final"
        revision_1 = await anext(events)
        assert revision_1.kind == "diarization"
        revision_2 = await anext(events)
        assert revision_2.kind == "diarization"
        # 幂等重复不产生新事件，直接读到第二个 completed。
        final_2 = await anext(events)
        assert final_2.kind == "final"
        # 跳号后分人停止：后续 completed 的 final 仍然交付。
        final_3 = await anext(events)
        assert final_3.kind == "final"
        assert final_3.window.segments[0].text == "还在"
        assert adapter.diarization_finalized is None

    asyncio.run(scenario())


def test_extensions_mode_stable_watermark_evicts_tracked_units() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_extensions_session_events(),
            _extensions_completed(
                "早",
                sequence=4,
                item_id="item-1",
                start_sample=0,
                end_sample=16_000,
                units=[_unit("old", text_start=0, text_end=1,
                             start_sample=0, end_sample=16_000)],
            ),
            _extensions_completed(
                "晚",
                sequence=5,
                item_id="item-2",
                start_sample=32_000,
                end_sample=48_000,
                units=[_unit("new", text_start=0, text_end=1,
                             start_sample=32_000, end_sample=48_000)],
            ),
            # 水位推进到 32000：旧单元（end 16000 < 32000）被冻结出缓存。
            _diarization_update(sequence=6, stable_through=32_000,
                                updates=[_patch("new", revision=1)]),
        ]
        adapter, _ = _extensions_adapter(connection)
        await adapter.connect()
        events = adapter.events()
        await _drain_ready(events)
        await anext(events)
        await anext(events)
        revision = await anext(events)
        assert revision.kind == "diarization"
        assert "old" not in adapter._unit_updates
        assert "new" in adapter._unit_updates

    asyncio.run(scenario())


def test_extensions_mode_unknown_uid_update_stops_diarization_keeps_text() -> None:
    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_extensions_session_events(),
            _diarization_update(sequence=4, stable_through=0,
                                updates=[_patch("ghost", revision=1)]),
            _extensions_completed(
                "正文仍在",
                sequence=5,
                item_id="item-1",
                start_sample=0,
                end_sample=16_000,
                units=[_unit("u1", text_start=0, text_end=4,
                             start_sample=0, end_sample=16_000)],
            ),
        ]
        adapter, _ = _extensions_adapter(connection)
        await adapter.connect()
        events = adapter.events()
        await _drain_ready(events)

        final = await anext(events)
        assert final.kind == "final"
        assert final.window.segments[0].text == "正文仍在"

    asyncio.run(scenario())


def test_extensions_mode_ws_disconnect_terminates_events() -> None:
    import websockets

    async def scenario() -> None:
        connection = FakeConnection()
        adapter, _ = _extensions_adapter(connection)
        await adapter.connect()
        events = adapter.events()
        await _drain_ready(events)

        async def _explode() -> str:
            raise websockets.ConnectionClosed(None, None)

        connection.recv = _explode  # type: ignore[method-assign]
        with pytest.raises(websockets.ConnectionClosed):
            await anext(events)

    asyncio.run(scenario())


def test_extensions_mode_ignores_duplicate_completed_item() -> None:
    """服务端 commit 嵯套重放同 item completed 时不产生第二个 final。"""

    async def scenario() -> None:
        connection = FakeConnection()
        diagnostics = ASRDiagnostics()
        connection._messages = [
            *_extensions_session_events(),
            _extensions_completed(
                "你好",
                sequence=4,
                item_id="item-1",
                start_sample=48000,
                end_sample=56000,
                units=[
                    _unit("s1", text_start=0, text_end=2,
                          start_sample=48000, end_sample=56000)
                ],
            ),
            _extensions_completed(
                "你好",
                sequence=5,
                item_id="item-1",
                start_sample=48000,
                end_sample=56000,
                units=[
                    _unit("s1", text_start=0, text_end=2,
                          start_sample=48000, end_sample=56000)
                ],
            ),
        ]
        client = SpeechRailRealtimeClient(
            url=connection.uri,
            connection_factory=lambda _: _immediate(connection),
        )
        adapter = SpeechRailStreamingTranscriber(
            client=client,
            context=ASRSessionContext(
                source_epoch=3,
                offset_ms=0,
                purpose="meeting",
                diarization_group_id="b" * 64,
            ),
            language="zh",
            diarization_extensions=True,
            diagnostics=diagnostics,
        )

        await adapter.connect()
        assert adapter.extensions_negotiated is True
        events = adapter.events()
        await _drain_ready(events)

        final = await anext(events)
        assert final.kind == "final"
        assert final.window is not None
        assert [segment.text for segment in final.window.segments] == ["你好"]

        # 消息耗尽即断连，驱动消费队列中的重复 completed 事件。
        import websockets

        async def _recv_exhausting() -> str:
            if not connection._messages:
                raise websockets.ConnectionClosed(None, None)
            return json.dumps(connection._messages.pop(0))

        connection.recv = _recv_exhausting  # type: ignore[method-assign]
        with pytest.raises(websockets.ConnectionClosed):
            await anext(events)

        # 重复 completed 仅记协议异常，不推送第二个 final、不重复登记单元。
        assert diagnostics.protocol_errors == 1
        assert list(adapter._finalized_item_ids) == ["item-1"]  # type: ignore[attr-defined]

    asyncio.run(scenario())


def test_extensions_mode_empty_closed_loop_keeps_confirmed_window() -> None:
    """嵯套后的空闭环 completed 不把已确认窗口回退为空（与 legacy 分支对齐）。"""

    async def scenario() -> None:
        connection = FakeConnection()
        connection._messages = [
            *_extensions_session_events(),
            _extensions_completed(
                "你好",
                sequence=4,
                item_id="item-1",
                start_sample=48000,
                end_sample=56000,
                units=[
                    _unit("s1", text_start=0, text_end=2,
                          start_sample=48000, end_sample=56000)
                ],
            ),
            # SpeechRail 嵯套 commit 的外层空闭环：新 item_id、空 transcript、无单元。
            _extensions_completed(
                "",
                sequence=5,
                item_id="item-2",
                start_sample=56000,
                end_sample=56000,
                units=[],
            ),
        ]
        adapter, _ = _extensions_adapter(connection)

        await adapter.connect()
        events = adapter.events()
        await _drain_ready(events)

        first = await anext(events)
        assert first.kind == "final"
        assert first.window is not None
        assert [segment.text for segment in first.window.segments] == ["你好"]

        second = await anext(events)
        assert second.kind == "final"
        assert second.window is not None
        assert second.window.segments == ()

        # 空闭环只清空 partial 展示；confirmed 窗口保留最近真实正文。
        assert adapter._last_confirmed_window is not None  # type: ignore[attr-defined]
        assert [  # type: ignore[attr-defined]
            segment.text for segment in adapter._last_confirmed_window.segments  # type: ignore[attr-defined]
        ] == ["你好"]

    asyncio.run(scenario())
