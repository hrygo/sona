"""SpeechRail v2.0.0 OpenAI-compatible Realtime transport.

This module owns the WebSocket envelope, the standard ASR bootstrap and the
single opt-in for the namespaced SpeechRail diarization extension. ASR event
semantics are decoded by :mod:`sona.speechrail.transcription_events`.
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
    """Validate the OpenAI Realtime envelope and sequence identity."""

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
        self._connection_factory = connection_factory or (
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
            isinstance(sequence, bool)
            or not isinstance(sequence, int)
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
        return self._session_id

    async def close(self) -> None:
        if self._connection is None:
            return
        try:
            await self._connection.close()
        finally:
            self._connection = None
            self._session_id = None
            self._sequence = 0


DEFAULT_SERVER_VAD_THRESHOLD = 0.65
_SESSION_UPDATED_TIMEOUT_SECS = 10.0
_SERVER_VAD_SILENCE_MS = {"subtitle": 400, "meeting": 900}


def build_server_vad_config(
    *,
    threshold: float = DEFAULT_SERVER_VAD_THRESHOLD,
    silence_duration_ms: int = 400,
    prefix_padding_ms: int = 300,
) -> dict[str, object]:
    """Build the OpenAI-standard server VAD configuration."""

    return {
        "type": "server_vad",
        "threshold": threshold,
        "prefix_padding_ms": prefix_padding_ms,
        "silence_duration_ms": silence_duration_ms,
    }


def resolve_server_vad_config(
    *,
    purpose: str,
    diarization_enabled: bool = False,
    threshold: float = DEFAULT_SERVER_VAD_THRESHOLD,
) -> dict[str, object]:
    """Resolve the bounded server VAD window for a subtitle or meeting stream."""

    del diarization_enabled
    key = "meeting" if purpose == "meeting" else "subtitle"
    return build_server_vad_config(
        threshold=threshold,
        silence_duration_ms=_SERVER_VAD_SILENCE_MS[key],
    )


DEFAULT_SERVER_VAD = resolve_server_vad_config(purpose="subtitle")
MEETING_SERVER_VAD = resolve_server_vad_config(purpose="meeting")
MANUAL_TURN_DETECTION: dict[str, object] = {"type": "manual"}


class SpeechRailRealtimeClient:
    """One OpenAI-standard Realtime session for ASR and optional diarization."""

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
        self._baseline_ready = False
        self._diarization_enabled = False
        self._diarization_attempted = False
        self._diarization_unavailable_reason: str | None = None
        self._pcm_sent = False
        self._finish_event_id: str | None = None

    @property
    def uri(self) -> str:
        return self._transport.uri

    @property
    def session_id(self) -> str | None:
        return self._transport.session_id

    @property
    def diarization_enabled(self) -> bool:
        return self._diarization_enabled

    @property
    def diarization_unavailable_reason(self) -> str | None:
        return self._diarization_unavailable_reason

    async def connect(
        self,
        *,
        language: str,
        turn_detection: Mapping[str, object] | None = None,
        diarization_enabled: bool = False,
    ) -> None:
        """Bootstrap standard ASR, then optionally perform the one v2 opt-in."""

        await self._transport.connect()
        self._baseline_ready = False
        self._diarization_enabled = False
        self._diarization_attempted = False
        self._diarization_unavailable_reason = None
        self._pcm_sent = False
        self._finish_event_id = None

        await self._transport.receive()  # session.created: bootstrap only
        turn_cfg = dict(turn_detection) if turn_detection is not None else {"type": "manual"}
        await self._transport.send_event(
            {
                "type": "session.update",
                "session": {
                    "turn_detection": turn_cfg,
                    "input_audio_transcription": {
                        "model": "gpt-4o-transcribe",
                        "language": language,
                    },
                },
            }
        )
        baseline = await self._receive_session_updated()
        self._validate_baseline_session(baseline)
        self._baseline_ready = True
        if diarization_enabled:
            await self.negotiate_diarization()

    async def _receive_session_updated(self) -> dict[str, object]:
        try:
            event = await asyncio.wait_for(
                self._transport.receive(), timeout=_SESSION_UPDATED_TIMEOUT_SECS
            )
        except TimeoutError:
            raise SpeechRailProtocolError("SPEECHRAIL_SESSION_UPDATE_TIMEOUT") from None
        if event.get("type") == "error":
            raise _error_from_event(event)
        if event.get("type") != "session.updated":
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
        return event

    @staticmethod
    def _validate_baseline_session(event: Mapping[str, object]) -> None:
        session = event.get("session")
        if not isinstance(session, dict) or "speechrail" in session:
            raise SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")

    async def negotiate_diarization(self) -> None:
        """Perform the single pre-PCM namespaced opt-in, idempotently."""

        if self._diarization_attempted:
            return
        if not self._baseline_ready:
            raise SpeechRailProtocolError("SPEECHRAIL_SESSION_NOT_READY")
        if self._pcm_sent:
            raise SpeechRailProtocolError("SPEECHRAIL_LATE_DIARIZATION_OPT_IN")
        self._diarization_attempted = True
        await self._transport.send_event(
            {
                "type": "session.update",
                "session": {"speechrail": {"diarization": {"enabled": True}}},
            }
        )
        try:
            event = await self._receive_session_updated()
        except SpeechRailProtocolError as exc:
            if exc.code in {"diarization_not_available", "unsupported_operation"}:
                self._diarization_unavailable_reason = exc.code
                return
            raise
        contract = _diarization_echo(event)
        if contract != {"enabled": True, "version": 1, "max_speakers": 4}:
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_CONTRACT_ERROR")
        self._diarization_enabled = True

    async def append_pcm(self, chunk: bytes) -> None:
        if not chunk or len(chunk) % 2:
            raise ValueError("PCM must be non-empty int16")
        if self._finish_event_id is not None:
            raise SpeechRailProtocolError("SPEECHRAIL_AUDIO_AFTER_FINISH")
        self._pcm_sent = True
        await self._transport.send_event(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }
        )

    async def commit(self) -> None:
        if self._finish_event_id is not None:
            raise SpeechRailProtocolError("SPEECHRAIL_COMMIT_AFTER_FINISH")
        await self._transport.send_event({"type": "input_audio_buffer.commit"})

    async def clear(self) -> None:
        await self._transport.send_event({"type": "input_audio_buffer.clear"})

    async def send_diarization_finish(self, event_id: str) -> None:
        """Send one idempotent v2 finish request for the active diarization session."""

        if not event_id or len(event_id) > 128:
            raise ValueError("event_id 长度必须在 1–128")
        if not self._diarization_enabled:
            raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_NOT_ENABLED")
        if self._finish_event_id is not None:
            if self._finish_event_id == event_id:
                return
            raise SpeechRailProtocolError("SPEECHRAIL_FINISH_ID_CONFLICT")
        self._finish_event_id = event_id
        await self._transport.send_event(
            {"type": "speechrail.diarization.finish", "event_id": event_id}
        )

    async def receive(self) -> dict[str, object]:
        return await self._transport.receive()

    async def close(self) -> None:
        await self._transport.close()


def _error_from_event(event: Mapping[str, object]) -> SpeechRailProtocolError:
    error = event.get("error")
    if not isinstance(error, dict):
        return SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    code = error.get("code")
    message = error.get("message")
    if not isinstance(code, str) or not code.strip():
        return SpeechRailProtocolError("SPEECHRAIL_PROTOCOL_ERROR")
    return SpeechRailProtocolError(
        code,
        message if isinstance(message, str) and message else None,
    )


def _diarization_echo(event: Mapping[str, object]) -> dict[str, object]:
    session = event.get("session")
    if not isinstance(session, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_CONTRACT_ERROR")
    speechrail = session.get("speechrail")
    if not isinstance(speechrail, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_CONTRACT_ERROR")
    diarization = speechrail.get("diarization")
    if not isinstance(diarization, dict):
        raise SpeechRailProtocolError("SPEECHRAIL_DIARIZATION_CONTRACT_ERROR")
    return {str(key): value for key, value in diarization.items()}


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


__all__ = [
    "DEFAULT_SERVER_VAD",
    "DEFAULT_SERVER_VAD_THRESHOLD",
    "MANUAL_TURN_DETECTION",
    "MEETING_SERVER_VAD",
    "ConnectionFactory",
    "SpeechRailConnection",
    "SpeechRailOpenAITransport",
    "SpeechRailProtocolError",
    "SpeechRailRealtimeClient",
    "build_server_vad_config",
    "decode_pcm16",
    "resolve_server_vad_config",
]
