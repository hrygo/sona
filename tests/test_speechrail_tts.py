from __future__ import annotations

import asyncio
import base64
import json
import math

import pytest

import sona.speechrail.transport as transport_module
from sona.speechrail.transport import (
    SpeechRailOpenAITransport,
    SpeechRailProtocolError,
)
from sona.speechrail.tts import SpeechRailTTSClient
from sona.speechrail.tts_loudness import Pcm16LoudnessConfig


class FakeSpeechConnection:
    uri = "ws://speechrail.test/v1/realtime"

    def __init__(
        self,
        *,
        session_capabilities: object | None = None,
        audio_deltas: list[bytes] | None = None,
    ) -> None:
        self.sent: list[dict[str, object]] = []
        session: dict[str, object] = {"id": "sess-1"}
        if session_capabilities is not None:
            session["speech_capabilities"] = session_capabilities
        deltas = audio_deltas or [b"\x00\x00"]
        self.messages = [
            self._event("session.created", 1, session=session),
            self._event("session.updated", 2, session={"id": "sess-1"}),
            self._event("conversation.created", 3, conversation={"id": "conv-1"}),
            self._event("conversation.item.created", 4),
            self._event("response.created", 5, response={"id": "resp-1", "status": "in_progress"}),
        ]
        for sequence, delta in enumerate(deltas, start=6):
            self.messages.append(
                self._event(
                    "response.audio.delta",
                    sequence,
                    response_id="resp-1",
                    delta=base64.b64encode(delta).decode("ascii"),
                )
            )
        response_done_sequence = 6 + len(deltas)
        self.messages.extend(
            [
                self._event(
                    "response.audio.done", response_done_sequence, response_id="resp-1"
                ),
                self._event(
                    "response.done",
                    response_done_sequence + 1,
                    response={"id": "resp-1", "status": "completed"},
                ),
            ]
        )

    @staticmethod
    def _event(event_type: str, sequence: int, **payload: object) -> dict[str, object]:
        return {
            "type": event_type,
            "event_id": f"evt-{sequence}",
            "session_id": "sess-1",
            "sequence": sequence,
            **payload,
        }

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        return json.dumps(self.messages.pop(0))

    async def close(self) -> None:
        return None


def test_tts_client_sends_openai_session_and_yields_pcm() -> None:
    async def scenario() -> None:
        connection = FakeSpeechConnection()
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="warm",
            language="zh",
            connection_factory=lambda _: _immediate(connection),
        )

        chunks = [chunk async for chunk in client.synthesize("你好", speed=1.1)]

        assert chunks == [b"\x00\x00"]
        assert connection.sent == [
            {
                "type": "session.update",
                "session": {
                    "model": "speechrail/qwen3-tts",
                    "voice": "warm",
                    "language": "zh",
                    "output_audio_format": "pcm16",
                },
            },
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "你好"}],
                },
            },
            {
                "type": "response.create",
                "response": {
                    "voice": "warm",
                    "speechrail": {"tts": {"speed": 1.1}},
                },
            },
        ]

    asyncio.run(scenario())


def test_tts_client_reads_stable_loudness_capability_without_changing_outbound_events() -> None:
    async def scenario() -> None:
        connection = FakeSpeechConnection(
            session_capabilities={"audio_loudness_profile": "stable_loudness_v1"}
        )
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="clone-test",
            language="zh",
            connection_factory=lambda _: _immediate(connection),
        )

        chunks = [chunk async for chunk in client.synthesize("你好")]

        assert chunks
        assert client.audio_loudness_profile == "stable_loudness_v1"
        assert all("speech_capabilities" not in event for event in connection.sent)

    asyncio.run(scenario())


def test_tts_client_falls_back_for_missing_or_unknown_loudness_capability() -> None:
    async def scenario() -> None:
        for profile in (
            None,
            {},
            {"audio_loudness_profile": "stable_loudness_v0"},
            {"audio_loudness_profile": 1},
            {"audio_loudness_profile": {}},
            ["stable_loudness_v1"],
        ):
            connection = FakeSpeechConnection(session_capabilities=profile)
            client = SpeechRailTTSClient(
                url=connection.uri,
                model="speechrail/qwen3-tts",
                voice="clone-test",
                language="zh",
                connection_factory=lambda _, connection=connection: _immediate(connection),
            )

            chunks = [chunk async for chunk in client.synthesize("你好")]

            assert chunks
            assert client.audio_loudness_profile is None

    asyncio.run(scenario())


def test_tts_client_uses_peak_safety_for_stable_loudness_capability() -> None:
    async def scenario() -> None:
        audio = _constant_pcm16(0.99, 1_920)
        connection = FakeSpeechConnection(
            session_capabilities={"audio_loudness_profile": "stable_loudness_v1"},
            audio_deltas=[audio],
        )
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="clone-test",
            language="zh",
            connection_factory=lambda _: _immediate(connection),
        )

        chunks = [chunk async for chunk in client.synthesize("测试")]

        assert len(chunks) == 1
        assert _peak_dbfs(chunks[0]) <= -1.0 + 0.1

    asyncio.run(scenario())


def test_tts_client_applies_one_loudness_guard_across_audio_deltas() -> None:
    async def scenario() -> None:
        connection = FakeSpeechConnection(
            audio_deltas=[_constant_pcm16(0.05, 1_920), _constant_pcm16(0.40, 1_920)]
        )
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="clone-test",
            language="zh",
            connection_factory=lambda _: _immediate(connection),
        )

        chunks = [chunk async for chunk in client.synthesize("测试")]

        assert len(chunks) == 2
        assert chunks[0] != _constant_pcm16(0.05, 1_920)
        assert chunks[1] != _constant_pcm16(0.40, 1_920)

    asyncio.run(scenario())


def test_tts_client_keeps_unannounced_builtin_voice_pcm_unchanged() -> None:
    async def scenario() -> None:
        audio = _constant_pcm16(0.99, 1_920)
        connection = FakeSpeechConnection(audio_deltas=[audio])
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="warm",
            language="zh",
            connection_factory=lambda _: _immediate(connection),
        )

        chunks = [chunk async for chunk in client.synthesize("测试")]

        assert chunks == [audio]

    asyncio.run(scenario())


def test_tts_client_uses_injected_loudness_config() -> None:
    async def scenario() -> None:
        audio = _constant_pcm16(0.05, 1_920)
        connection = FakeSpeechConnection(audio_deltas=[audio])
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="clone-test",
            language="zh",
            loudness_config=Pcm16LoudnessConfig(max_gain_db=0.0),
            connection_factory=lambda _: _immediate(connection),
        )

        chunks = [chunk async for chunk in client.synthesize("测试")]

        assert chunks == [audio]

    asyncio.run(scenario())


def test_tts_client_cancels_the_active_response_when_consumer_stops() -> None:
    async def scenario() -> None:
        connection = BlockingSpeechConnection()
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="default",
            connection_factory=lambda _: _immediate(connection),
        )

        async def consume() -> None:
            async for _chunk in client.synthesize("请取消", speed=1.0):
                pass

        task = asyncio.create_task(consume())
        await connection.response_created.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert {"type": "response.cancel"} in connection.sent
        assert connection.closed is True

    asyncio.run(scenario())


def test_tts_client_exposes_and_cancels_active_response_for_pipecat() -> None:
    async def scenario() -> None:
        connection = BlockingSpeechConnection()
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="default",
            connection_factory=lambda _: _immediate(connection),
        )

        async def consume() -> None:
            async for _chunk in client.synthesize("请取消", speed=1.0):
                pass

        task = asyncio.create_task(consume())
        await connection.response_created.wait()
        assert client.active_response_id == "resp-cancel"
        await client.cancel("resp-cancel")
        assert client.active_response_id is None
        assert {"type": "response.cancel"} in connection.sent
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_tts_client_rejects_audio_with_mismatched_response_id() -> None:
    async def scenario() -> None:
        connection = FakeSpeechConnection()
        connection.messages[5]["response_id"] = "resp-other"
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="default",
            connection_factory=lambda _: _immediate(connection),
        )

        with pytest.raises(SpeechRailProtocolError) as error:
            _ = [chunk async for chunk in client.synthesize("你好")]

        assert error.value.code == "SPEECHRAIL_RESPONSE_ERROR"

    asyncio.run(scenario())


class BlockingSpeechConnection(FakeSpeechConnection):
    def __init__(self) -> None:
        super().__init__()
        self.sent = []
        self.messages = [
            self._event("session.created", 1, session={"id": "sess-1"}),
            self._event("session.updated", 2, session={"id": "sess-1"}),
            self._event("conversation.created", 3, conversation={"id": "conv-1"}),
            self._event("conversation.item.created", 4),
        ]
        self.response_created = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def send(self, payload: str) -> None:
        event = json.loads(payload)
        self.sent.append(event)
        if event.get("type") == "response.create":
            self.messages.append(
                self._event(
                    "response.created",
                    5,
                    response={"id": "resp-cancel", "status": "in_progress"},
                )
            )
            self.response_created.set()

    async def recv(self) -> str:
        while not self.messages:
            await self.release.wait()
        return json.dumps(self.messages.pop(0))

    async def close(self) -> None:
        self.closed = True


async def _immediate(connection: FakeSpeechConnection) -> FakeSpeechConnection:
    return connection


def _constant_pcm16(level: float, samples: int) -> bytes:
    value = int(level * 32767)
    return b"".join(value.to_bytes(2, "little", signed=True) for _ in range(samples))


def _peak_dbfs(pcm: bytes) -> float:
    values = [
        int.from_bytes(pcm[index : index + 2], "little", signed=True)
        for index in range(0, len(pcm), 2)
    ]
    peak = max(abs(value) for value in values) / 32767
    return 20 * math.log10(max(peak, 1e-12))


def test_transport_sends_api_key_as_websocket_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    async def connect(uri: str, **kwargs: object) -> FakeSpeechConnection:
        calls.append((uri, kwargs.get("additional_headers")))
        return FakeSpeechConnection()

    monkeypatch.setattr(transport_module.websockets, "connect", connect)

    async def scenario() -> None:
        client = SpeechRailOpenAITransport(
            url="wss://speechrail.test/v1/realtime",
            api_key="  secret-key  ",
        )
        await client.connect()
        await client.close()

    asyncio.run(scenario())

    assert calls == [
        (
            "wss://speechrail.test/v1/realtime",
            {"Authorization": "Bearer secret-key"},
        )
    ]
