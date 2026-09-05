"""SpeechRail OpenAI-compatible Realtime transport adapter.

This is the single wire-protocol client for SpeechRail's ``WS /v1/realtime``
(OpenAI Realtime ASR/TTS subset).  It owns only the OpenAI envelope concerns
(JSON, ``type``/``session_id``/``sequence`` identity, strict monotonic
sequence) and the outbound client events; ASR/TTS semantics stay in the
adapters and ``speechrail.tts``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, cast

import websockets


class SpeechRailConnection(Protocol):
    async def send(self, payload: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


ConnectionFactory = Callable[[str], Awaitable[SpeechRailConnection]]


def _connect(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    open_timeout: float | None = None,
) -> Awaitable[SpeechRailConnection]:
    return cast(
        Awaitable[SpeechRailConnection],
        websockets.connect(
            url,
            additional_headers=headers or None,
            open_timeout=open_timeout,
        ),
    )


class SpeechRailProtocolError(RuntimeError):
    """Stable local error for malformed or rejected SpeechRail events."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class SpeechRailOpenAITransport:
    """Validate the OpenAI Realtime envelope while leaving semantics to adapters."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        connect_timeout_secs: float | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._url = url
        normalized_key = api_key.strip() if isinstance(api_key, str) else None
        if connection_factory is not None:
            self._connection_factory: ConnectionFactory = connection_factory
        else:
            self._connection_factory = (
                lambda target: _connect(
                    target,
                    headers=(
                        {"Authorization": f"Bearer {normalized_key}"}
                        if normalized_key
                        else None
                    ),
                    open_timeout=connect_timeout_secs,
                )
            )
        self._connection: SpeechRailConnection | None = None
        self._sequence = 0
        self._session_id: str | None = None

    @property
    def uri(self) -> str:
        return self._url

    async def connect(self) -> None:
        """Open the socket without sending a protocol-specific session update."""
        if self._connection is not None:
            raise RuntimeError("SPEECHRAIL_ALREADY_CONNECTED")
        self._sequence = 0
        self._session_id = None
        self._connection = await self._connection_factory(self._url)

    async def send_event(self, payload: Mapping[str, object]) -> None:
        if self._connection is None:
            raise RuntimeError("SPEECHRAIL_NOT_CONNECTED")
        await self._connection.send(json.dumps(dict(payload), separators=(",", ":")))

    async def receive(self) -> dict[str, object]:
        if self._connection is None:
            raise RuntimeError("SPEECHRAIL_NOT_CONNECTED")
        try:
            payload = json.loads(await self._connection.recv())
        except (json.JSONDecodeError, TypeError) as exc:
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR") from exc
        if not isinstance(payload, dict):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        sequence = payload.get("sequence")
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence <= self._sequence
        ):
            raise SpeechRailProtocolError("SPEECHRAIL_SEQUENCE_ERROR")
        event_type = payload.get("type")
        session_id = payload.get("session_id")
        if (
            not isinstance(event_type, str)
            or not event_type
            or not isinstance(session_id, str)
            or not session_id
        ):
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        if self._session_id is None:
            if event_type != "session.created":
                raise SpeechRailProtocolError("SPEECHRAIL_SESSION_CREATE_FAILED")
            self._session_id = session_id
        elif session_id != self._session_id:
            raise SpeechRailProtocolError("SPEECHRAIL_SESSION_MISMATCH")
        self._sequence = sequence
        return {str(key): value for key, value in payload.items()}

    @property
    def session_id(self) -> str | None:
        """已协商的 session id；连接关闭后回到 None。"""
        return self._session_id

    async def close(self) -> None:
        if self._connection is not None:
            try:
                await self._connection.close()
            finally:
                self._connection = None
                self._session_id = None
                self._sequence = 0


_ASR_ALIAS_DIARIZE = "gpt-4o-transcribe-diarize"
_ASR_ALIAS_PLAIN = "gpt-4o-transcribe"

# SPK-E2E-1 分人扩展：能力字符串与 session.updated 契约约束（Rail 规格 §5.1）。
DIARIZATION_EXTENSION_CAPABILITY = "speechrail.diarization.v1"
_DIARIZATION_CONTRACT_TIMEBASE = "session_samples"
_DIARIZATION_CONTRACT_SAMPLE_RATE = 16_000
_SESSION_UPDATED_TIMEOUT_SECS = 10.0

DEFAULT_SERVER_VAD: dict[str, object] = {
    "type": "server_vad",
    "threshold": 0.5,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 400,
}
# 扩展模式推荐的 server VAD（Rail 规格 §5.1/Sona 设计 §3：silence 600ms）。
DEFAULT_SERVER_VAD_EXTENSIONS: dict[str, object] = {
    "type": "server_vad",
    "threshold": 0.5,
    "prefix_padding_ms": 300,
    "silence_duration_ms": 600,
}
MANUAL_TURN_DETECTION: dict[str, object] = {"type": "manual"}


class SpeechRailRealtimeClient:
    """One OpenAI-standard Realtime ASR session used by subtitle/meeting adapters.

    ``connect`` opens the socket and sends a ``session.update`` that configures
    the ``input_audio_transcription`` model/language and (optionally) enables
    the session-scoped ``diarization`` profile.  Audio is streamed in 16 kHz
    mono PCM16 base64 chunks and each turn is finalized with ``commit``.
    """

    def __init__(
        self,
        *,
        url: str,
        api_key: str | None = None,
        connect_timeout_secs: float | None = None,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._transport = SpeechRailOpenAITransport(
            url=url,
            api_key=api_key,
            connect_timeout_secs=connect_timeout_secs,
            connection_factory=connection_factory,
        )
        self._diarization_contract: dict[str, object] | None = None

    @property
    def uri(self) -> str:
        return self._transport.uri

    @property
    def session_id(self) -> str | None:
        """当前连接的 SpeechRail session id（未连接时为 None）。"""
        return self._transport.session_id

    async def connect(
        self,
        *,
        language: str,
        diarization: bool = False,
        speaker_count_hint: int | None = None,
        diarization_group_id: str | None = None,
        turn_detection: Mapping[str, object] | None = None,
        diarization_extensions: bool = False,
    ) -> None:
        await self._transport.connect()
        negotiated_contract: dict[str, object] | None = None
        negotiated = False
        if diarization_extensions:
            if speaker_count_hint is not None and not 1 <= speaker_count_hint <= 4:
                await self._transport.close()
                raise ValueError("SPEECHRAIL_SPEAKER_LIMIT_EXCEEDED")
            created = await self._transport.receive()
            capabilities = _session_capabilities(created)
            if DIARIZATION_EXTENSION_CAPABILITY in capabilities:
                negotiated_contract = await self._negotiate_extensions(
                    language=language,
                    speaker_count_hint=speaker_count_hint,
                    diarization_group_id=diarization_group_id,
                    turn_detection=turn_detection,
                )
                negotiated = True
        if not negotiated:
            await self._send_legacy_session_update(
                language=language,
                diarization=diarization,
                speaker_count_hint=speaker_count_hint,
                diarization_group_id=diarization_group_id,
                turn_detection=turn_detection,
            )
        self._diarization_contract = negotiated_contract

    @property
    def diarization_contract(self) -> dict[str, object] | None:
        """协商成功后的 diarization_contract；legacy 连接为 None。"""
        return self._diarization_contract

    async def _negotiate_extensions(
        self,
        *,
        language: str,
        speaker_count_hint: int | None,
        diarization_group_id: str | None,
        turn_detection: Mapping[str, object] | None,
    ) -> dict[str, object]:
        transcription: dict[str, object] = {
            "model": _ASR_ALIAS_DIARIZE,
            "language": language,
        }
        diarization_config: dict[str, object] = {
            "enabled": True,
            "finalize": True,
            "extensions": [DIARIZATION_EXTENSION_CAPABILITY],
        }
        if speaker_count_hint is not None:
            diarization_config["speaker_count_hint"] = speaker_count_hint
        if diarization_group_id is not None:
            diarization_config["group_id"] = diarization_group_id
        transcription["diarization"] = diarization_config
        turn_cfg = dict(turn_detection) if turn_detection is not None else {
            "type": "manual"
        }
        await self._transport.send_event(
            {
                "type": "session.update",
                "session": {
                    "turn_detection": turn_cfg,
                    "input_audio_transcription": transcription,
                },
            }
        )
        updated = await asyncio.wait_for(
            self._transport.receive(), timeout=_SESSION_UPDATED_TIMEOUT_SECS
        )
        if updated.get("type") != "session.updated":
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        return _validate_diarization_contract(updated)

    async def _send_legacy_session_update(
        self,
        *,
        language: str,
        diarization: bool,
        speaker_count_hint: int | None,
        diarization_group_id: str | None,
        turn_detection: Mapping[str, object] | None,
    ) -> None:
        transcription: dict[str, object] = {
            "model": _ASR_ALIAS_DIARIZE if diarization else _ASR_ALIAS_PLAIN,
            "language": language,
        }
        if diarization:
            diarization_config: dict[str, object] = {"enabled": True, "finalize": True}
            if speaker_count_hint is not None:
                diarization_config["speaker_count_hint"] = speaker_count_hint
            if diarization_group_id is not None:
                diarization_config["group_id"] = diarization_group_id
            transcription["diarization"] = diarization_config
        turn_cfg = dict(turn_detection) if turn_detection is not None else {"type": "manual"}
        await self._transport.send_event(
            {
                "type": "session.update",
                "session": {
                    "turn_detection": turn_cfg,
                    "input_audio_transcription": transcription,
                },
            }
        )

    async def append_pcm(self, chunk: bytes) -> None:
        if not chunk or len(chunk) % 2:
            raise ValueError("PCM must be non-empty int16")
        await self._transport.send_event(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }
        )

    async def commit(self) -> None:
        await self._transport.send_event({"type": "input_audio_buffer.commit"})

    async def clear(self) -> None:
        await self._transport.send_event({"type": "input_audio_buffer.clear"})

    async def receive(self) -> dict[str, object]:
        return await self._transport.receive()

    async def close(self) -> None:
        await self._transport.close()


def decode_pcm16(value: object) -> bytes:
    """Decode a base64 PCM16 audio field at an outbound boundary."""

    if not isinstance(value, str) or not value:
        raise SpeechRailProtocolError("SPEECHRAIL_AUDIO_ERROR")
    try:
        audio = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise SpeechRailProtocolError("SPEECHRAIL_AUDIO_ERROR") from exc
    if not audio or len(audio) % 2:
        raise SpeechRailProtocolError("SPEECHRAIL_AUDIO_ERROR")
    return audio


def _session_capabilities(created: dict[str, object]) -> frozenset[str]:
    """从 session.created 提取服务能力集合（缺失视为空）。"""
    session = created.get("session")
    if not isinstance(session, dict):
        return frozenset()
    capabilities = session.get("capabilities")
    if not isinstance(capabilities, list):
        return frozenset()
    return frozenset(
        item for item in capabilities if isinstance(item, str) and item
    )


def _validate_diarization_contract(updated: dict[str, object]) -> dict[str, object]:
    """校验 session.updated 回显的 diarization_contract（Rail 规格 §5.1）。

    未成功回显即未启用：任何字段缺失/越界都按协议错误处理，绝不降级猜测。
    """
    session = updated.get("session")
    if not isinstance(session, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    contract = session.get("diarization_contract")
    if not isinstance(contract, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    version = contract.get("version")
    timebase = contract.get("timebase")
    sample_rate = contract.get("sample_rate")
    max_speakers = contract.get("max_speakers")
    max_item_ms = contract.get("max_item_duration_ms")
    max_revision_ms = contract.get("max_revision_delay_ms")
    group_generation = contract.get("group_generation")
    if (
        version != 1
        or timebase != _DIARIZATION_CONTRACT_TIMEBASE
        or sample_rate != _DIARIZATION_CONTRACT_SAMPLE_RATE
        or isinstance(max_speakers, bool)
        or not isinstance(max_speakers, int)
        or max_speakers < 1
        or isinstance(max_item_ms, bool)
        or not isinstance(max_item_ms, int)
        or max_item_ms <= 0
        or isinstance(max_revision_ms, bool)
        or not isinstance(max_revision_ms, int)
        or max_revision_ms <= 0
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    if group_generation is not None and (
        not isinstance(group_generation, str) or not group_generation
    ):
        raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return dict(contract)


__all__ = [
    "DEFAULT_SERVER_VAD",
    "DEFAULT_SERVER_VAD_EXTENSIONS",
    "DIARIZATION_EXTENSION_CAPABILITY",
    "MANUAL_TURN_DETECTION",
    "ConnectionFactory",
    "SpeechRailConnection",
    "SpeechRailOpenAITransport",
    "SpeechRailProtocolError",
    "SpeechRailRealtimeClient",
    "decode_pcm16",
]
