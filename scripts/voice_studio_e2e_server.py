"""Loopback-only UX harness: real Sona UI/proxy/control, synthetic external runtime.

Never import this module from production. No models, microphones or user DB are used.
The companion browser runner drives scenario switches through /__ux/config.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import struct
import threading
import wave
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from sona.config import Settings, SubtitleSettings
from sona.meeting.models import PCMOwner, RuntimeMode
from sona.subtitles import SubtitleProxy
from sona.ui.app_context import get_app_context
from sona.ui.assistant_bridge import StatusBridgeObserver
from sona.ui.protocol import DuplexMode, RuntimeStateSnapshot
from sona.ui.runtime import UIRuntime
from sona.ui.runtime_events import RuntimeStateBroadcaster
from sona.ui.server import create_app

CONFIG: dict[str, Any] = {}
CALLS: list[dict[str, Any]] = []
VOICES: dict[str, dict[str, Any]] = {}


def reference() -> dict[str, Any]:
    return {
        "policy_version": "voice_quality_v1",
        "run_id": "reference-fixture",
        "tested_at": "2026-09-12T00:00:00Z",
        "status": "pass",
        "failure_codes": [],
        "reference": {"duration_seconds": 8, "transcript_match": 1},
    }


def output() -> dict[str, Any]:
    result = reference()
    result.pop("reference")
    result["run_id"] = "output-fixture"
    result["synthesis"] = {
        "probe_count": 18,
        "successful_probe_count": 18,
        "deterministic": True,
        "transcript_match": 1,
    }
    mode = CONFIG.get("quality", "pass")
    if mode in {"warn", "reject", "unevaluated"}:
        result.update(status=mode, failure_codes=["transcript_mismatch"])
    elif mode == "incomplete":
        result["synthesis"]["successful_probe_count"] = 17
    return result


def wave_bytes(seconds: float = 0.4) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(
            b"".join(
                struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * i / 24000)))
                for i in range(int(seconds * 24000))
            )
        )
    return buf.getvalue()


def reset() -> None:
    CONFIG.clear()
    CALLS.clear()
    VOICES.clear()
    VOICES.update(
        {
            "default": {"id": "default", "name": "默认原声", "is_system": True, "mode": "system"},
            "warm": {"id": "warm", "name": "温暖磁性", "is_system": True, "mode": "system"},
            "my_clone": {
                "id": "my_clone",
                "name": "既有录音音色",
                "mode": "clone",
                "is_system": False,
                "ref_text": "这是已经存在的参考文字",
                "quality": reference(),
            },
        }
    )


class Runtime:
    def __init__(self) -> None:
        self.observer = StatusBridgeObserver()
        self.subtitle_proxy = SubtitleProxy(SubtitleSettings(_env_file=None))
        self._state = RuntimeStateSnapshot(
            pipeline="running",
            subtitle="disconnected",
            mic_muted=False,
            voice="default",
            persona=None,
            session_started_at=None,
            duplex_mode=DuplexMode.SPEAKER_FOCUS,
            mode=RuntimeMode.ASSISTANT,
            pcm_owner=PCMOwner.ASSISTANT,
            runtime_revision=1,
        )
        self.runtime_events = RuntimeStateBroadcaster(self.snapshot)

    def snapshot(self) -> RuntimeStateSnapshot:
        return self._state

    def update(self, **fields: Any) -> None:
        self._state = self._state.model_copy(
            update={**fields, "runtime_revision": self._state.runtime_revision + 1}
        )
        self.runtime_events.publish(self._state)

    async def set_mic_muted(self, muted: bool) -> None:
        CALLS.append({"op": "mute", "muted": muted})
        await asyncio.sleep(CONFIG.get("mute_delay", 0))
        if CONFIG.get("mute_fail") and muted:
            raise ValueError("fixture mute rejected")
        if not CONFIG.get("mute_mismatch"):
            self.update(mic_muted=muted)

    async def set_voice(self, voice: str) -> None:
        CALLS.append({"op": "activate", "id": voice})
        await asyncio.sleep(CONFIG.get("activate_delay", 0))
        if CONFIG.get("activate_fail"):
            raise ValueError("fixture activation rejected")
        if not CONFIG.get("activate_mismatch"):
            self.update(voice=voice)

    async def stop_active_mode(self) -> None:
        self.update(mode=RuntimeMode.IDLE, pcm_owner=PCMOwner.NONE)

    async def start_assistant(self) -> None:
        self.update(mode=RuntimeMode.ASSISTANT, pcm_owner=PCMOwner.ASSISTANT)


upstream = FastAPI()


def failure(code: str, status: int = 503) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": f"测试分支：{code}", "retryable": status >= 500}},
        status_code=status,
        headers={"x-request-id": "ux-fixture"},
    )


@upstream.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
async def external(request: Request, path: str) -> Response:
    body = await request.body()
    data: dict[str, Any] = {}
    if "application/json" in request.headers.get("content-type", "") and body:
        data = json.loads(body)
    elif "multipart/form-data" in request.headers.get("content-type", ""):
        message = BytesParser(policy=policy.default).parsebytes(
            (
                "Content-Type: " + request.headers["content-type"] + "\r\nMIME-Version: 1.0\r\n\r\n"
            ).encode()
            + body
        )
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            value = part.get_payload(decode=True)
            data[str(name)] = (
                {"sha256": hashlib.sha256(value).hexdigest(), "mime": part.get_content_type()}
                if name == "audio"
                else value.decode()
            )
    CALLS.append(
        {
            "op": request.method,
            "path": path,
            "data": data,
            "key": request.headers.get("idempotency-key"),
            "authorized": request.headers.get("authorization") == "Bearer fixture-only",
        }
    )
    if path == "v1/models":
        capabilities = {
            "supports_instruction": True,
            "supports_clone": True,
            "supports_preview": True,
        }
        if CONFIG.get("tier") == "light":
            capabilities = dict.fromkeys(capabilities, False)
        return JSONResponse(
            {"data": [{"id": "speechrail/qwen3-tts", "capabilities": capabilities}]}
        )
    if path == "v1/voices" and request.method == "GET":
        if CONFIG.get("list_fail"):
            return failure("catalog_unavailable")
        return JSONResponse({"data": list(VOICES.values())})
    if path == "v1/voices/clone/prompts":
        return JSONResponse({"data": []})
    if path.endswith("quality-runs"):
        await asyncio.sleep(CONFIG.get("quality_delay", 0))
        if CONFIG.get("quality") == "error":
            return failure("transcription_unavailable")
        return JSONResponse(output())
    if path in {"v1/audio/speech", "v1/voices/previews"}:
        await asyncio.sleep(CONFIG.get("audio_delay", 0))
        if CONFIG.get("audio_fail"):
            return failure("backend_error")
        return Response(wave_bytes(CONFIG.get("audio_seconds", 0.4)), media_type="audio/wav")
    if path == "v1/voices/clone/validate":
        await asyncio.sleep(CONFIG.get("preflight_delay", 0))
        if CONFIG.get("preflight") == "error":
            return failure("transcription_unavailable")
        report = reference()
        if CONFIG.get("preflight") in {"reject", "unevaluated"}:
            report.update(status=CONFIG["preflight"], failure_codes=["transcript_mismatch"])
        return JSONResponse(report)
    if path in {"v1/voices/designs", "v1/voices/clone"}:
        await asyncio.sleep(CONFIG.get("register_delay", 0))
        kind = "design" if path.endswith("designs") else "clone"
        scenario = CONFIG.get("registration", "success")
        if scenario == "unsupported":
            return failure("not_found", 404)
        if scenario == "reject":
            return failure("transcription_unavailable")
        ident = data["id"]
        # Fixtures replay recorded clones only with the stable Idempotency-Key.
        if ident in VOICES and (
            kind == "design" or request.headers.get("idempotency-key") != ident
        ):
            return failure("voice_id_conflict", 409)
        voice = {
            "id": ident,
            "name": data["name"],
            "is_system": False,
            "mode": "clone",
            "ref_text": data.get("reference_text", data.get("ref_text")),
            "quality": reference(),
        }
        if kind == "design":
            voice["creation"] = {
                "origin": "generated",
                "method": "voice_design_reference_v1",
                "preprocessing_version": "energy_v1",
                "model_artifact": "qwen3-voice-design",
                "model_revision": "a" * 40,
                "seed": data["seed"],
                "instruction_sha256": hashlib.sha256(data["instruction"].encode()).hexdigest(),
                "reference_text_sha256": hashlib.sha256(
                    data["reference_text"].encode()
                ).hexdigest(),
                "reference_audio_sha256": "c" * 64,
            }
        VOICES[ident] = voice
        if scenario == "lost":
            return failure("upstream_timeout", 504)
        if scenario == "malformed":
            return JSONResponse({"unexpected": True}, status_code=201)
        return JSONResponse(
            {"voice": voice, "synthesis_validation": "unevaluated"} if kind == "design" else voice,
            status_code=201,
        )
    if path.startswith("v1/voices/") and request.method == "DELETE":
        if CONFIG.get("delete_fail"):
            return failure("delete_unavailable")
        VOICES.pop(path.rsplit("/", 1)[-1], None)
        return Response(status_code=204)
    if path in {"health", "v1/health", "v1/system/status"}:
        return JSONResponse({"status": "ok"})
    return JSONResponse({"data": []})


def make_ui(upstream_port: int) -> FastAPI:
    settings = Settings(
        _env_file=None,
        ui={"static_dir": Path(__file__).resolve().parents[1] / "ui/dist"},
        interaction={
            "speechrail_tts_rest_url": f"http://127.0.0.1:{upstream_port}/v1",
            "speechrail_api_key": "fixture-only",
            "llm_base_url": f"http://127.0.0.1:{upstream_port}/v1",
        },
    )
    app = create_app(settings, initialize_meeting=False)
    runtime = Runtime()
    get_app_context(app).runtime = cast(UIRuntime, runtime)

    @app.post("/__ux/config")
    async def config(request: Request) -> dict[str, bool]:
        update = await request.json()
        if update.pop("reset", False):
            reset()
            runtime.update(
                voice="default",
                mic_muted=False,
                mode=RuntimeMode.ASSISTANT,
                pcm_owner=PCMOwner.ASSISTANT,
            )
        state = update.pop("runtime", {})
        if state:
            runtime.update(**state)
        CONFIG.update(update)
        return {"ok": True}

    @app.get("/__ux/state")
    async def state() -> dict[str, Any]:
        return {
            "calls": CALLS,
            "voices": VOICES,
            "runtime": runtime.snapshot().model_dump(mode="json"),
        }

    # Static root mount must stay last so fixture controls are not swallowed.
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != ""] + [
        r for r in app.router.routes if getattr(r, "path", None) == ""
    ]
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19830)
    args = parser.parse_args()
    reset()
    thread = threading.Thread(
        target=uvicorn.run,
        args=(upstream,),
        kwargs={"host": "127.0.0.1", "port": args.port + 1, "log_level": "warning"},
        daemon=True,
    )
    thread.start()
    uvicorn.run(
        make_ui(args.port + 1),
        host="127.0.0.1",
        port=args.port,
        lifespan="off",
        log_level="warning",
    )
