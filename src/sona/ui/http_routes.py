"""UI HTTP 路由：health/services/runtime 与 SpeechRail voices/speech proxy。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Mapping
from dataclasses import is_dataclass
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder

from sona.lm_studio import lm_studio_auth_headers, lm_studio_openai_models_url
from sona.network import local_async_client
from sona.ui.app_context import UIAppContext

logger = logging.getLogger(__name__)

NetworkScope = Literal["local", "network"]
_RUNTIME_DIAGNOSTIC_KEYS = ("audio_hub", "interaction", "subtitles", "tts", "last_transition")
_ASR_WORKLOAD_KEYS = ("workload", "ws_state", "reconnect_count", "last_event_age_ms")


def _speechrail_health_url(rest_url: str) -> str:
    parsed = urlsplit(rest_url)
    scheme = "https" if parsed.scheme == "https" else "http"
    return f"{scheme}://{parsed.netloc}/health"


def _speechrail_rest_path(rest_url: str, path: str) -> str:
    return f"{rest_url.rstrip('/')}/{path.lstrip('/')}"


def _speechrail_auth_headers(api_key: str | None) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {api_key}"} if api_key else None


def _network_scope(host: str) -> NetworkScope:
    normalized = host.strip().lower()
    return "local" if normalized in {"127.0.0.1", "localhost", "::1", "[::1]"} else "network"


async def _do_probe_async(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    expected_model: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """并发异步探活单个服务。"""
    try:
        resp = await client.get(url, headers=headers)
        result: dict[str, Any] = {
            "name": name,
            "status": "ok" if resp.status_code < 400 else "error",
            "url": url,
        }
        if expected_model is not None and resp.status_code < 400:
            model_ids: set[str] = set()
            body: object = None
            with contextlib.suppress(ValueError, TypeError):
                body = resp.json()
                if isinstance(body, dict) and isinstance(body.get("data"), list):
                    model_ids = {
                        item["id"]
                        for item in body["data"]
                        if isinstance(item, dict) and isinstance(item.get("id"), str)
                    }
            result["target_model"] = expected_model
            result["model_present"] = (
                body.get("tts_ready") is True
                if name == "tts" and isinstance(body, dict) and not model_ids
                else expected_model in model_ids
            )
        return result
    except httpx.ConnectError:
        return {"name": name, "status": "unreachable", "url": url}
    except (httpx.ReadTimeout, httpx.TimeoutException):
        return {"name": name, "status": "timeout", "url": url}
    except Exception:
        return {"name": name, "status": "error", "url": url}


def _empty_runtime_diagnostics() -> dict[str, Any]:
    return {
        "audio_hub": {},
        "interaction": {},
        "subtitles": {},
        "tts": {},
        "last_transition": None,
    }


def _redact_diagnostic_binary(_value: object) -> None:
    """诊断接口只公开指标，不复制 PCM 或其他二进制载荷。"""


def _json_safe_mapping(value: object) -> dict[str, Any] | None:
    """把 mapping/frozen dataclass 转成已验证可 JSON 序列化的深副本。"""
    if not isinstance(value, Mapping) and not (
        is_dataclass(value) and not isinstance(value, type)
    ):
        return None
    encoded = jsonable_encoder(
        value,
        custom_encoder={
            bytes: _redact_diagnostic_binary,
            bytearray: _redact_diagnostic_binary,
            memoryview: _redact_diagnostic_binary,
            asyncio.Queue: _redact_diagnostic_binary,
        },
    )
    copied = cast(object, json.loads(json.dumps(encoded, allow_nan=False)))
    if not isinstance(copied, dict):
        return None
    return cast(dict[str, Any], copied)


def _runtime_diagnostics(runtime: Any) -> dict[str, Any]:
    fallback = _empty_runtime_diagnostics()
    if runtime is None:
        return fallback
    diagnostics = getattr(runtime, "diagnostics", None)
    if not callable(diagnostics):
        return fallback
    try:
        raw = _json_safe_mapping(diagnostics())
        if raw is None:
            return fallback
        return {
            key: raw.get(key, fallback[key]) for key in _RUNTIME_DIAGNOSTIC_KEYS
        }
    except Exception as exc:
        logger.warning(
            "Sona: runtime diagnostics unavailable: %s",
            type(exc).__name__,
        )
        return fallback


def _asr_workload_diagnostics(runtime: Any) -> dict[str, Any]:
    if runtime is None:
        return {}
    try:
        snapshot = getattr(runtime, "snapshot", None)
        subtitle_proxy = getattr(runtime, "subtitle_proxy", None)
        diagnostics = getattr(subtitle_proxy, "diagnostics", None)
        if not callable(snapshot) or not callable(diagnostics):
            return {}
        state = snapshot()
        raw = _json_safe_mapping(diagnostics(state.pcm_owner))
        if raw is None:
            return {}
        return {key: raw[key] for key in _ASR_WORKLOAD_KEYS if key in raw}
    except Exception as exc:
        logger.warning(
            "Sona: SpeechRail workload diagnostics unavailable: %s",
            type(exc).__name__,
        )
        return {}


def create_http_router(context: UIAppContext) -> APIRouter:
    """Build health/services/runtime and SpeechRail voices/speech proxy routes."""
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/api/services")
    async def services() -> dict[str, Any]:
        """三服务健康灯聚合（并发异步探活，单次总延时 <= timeout）。"""
        settings = context.settings
        timeout = min(settings.ui.api_timeout, 2.5)
        speechrail = settings.subtitles
        lm = settings.interaction
        paths = [
            (
                "speechrail",
                speechrail.speechrail_health_url,
                None,
                _speechrail_auth_headers(speechrail.speechrail_api_key),
            ),
            (
                "tts",
                _speechrail_health_url(lm.speechrail_tts_rest_url),
                lm.speechrail_tts_model,
                _speechrail_auth_headers(lm.speechrail_api_key),
            ),
            (
                "lm",
                lm_studio_openai_models_url(lm.llm_base_url),
                lm.llm_model,
                lm_studio_auth_headers(lm.llm_api_key),
            ),
        ]
        async with local_async_client(timeout=timeout) as client:
            tasks = [
                _do_probe_async(client, name, url, expected_model, headers=headers)
                for name, url, expected_model, headers in paths
            ]
            results = await asyncio.gather(*tasks)
        service_results = list(results)
        runtime = context.runtime
        workload_diagnostics = _asr_workload_diagnostics(runtime)
        if workload_diagnostics:
            speechrail_service = next(
                (item for item in service_results if item["name"] == "speechrail"),
                None,
            )
            if speechrail_service is not None:
                speechrail_service.update(workload_diagnostics)
        return {
            "network_scope": _network_scope(settings.ui.host),
            "services": service_results,
            "diagnostics": _runtime_diagnostics(runtime),
        }

    @router.get("/api/runtime")
    async def runtime_state() -> dict[str, Any]:
        runtime = context.runtime
        if runtime is None:
            raise HTTPException(status_code=503, detail="runtime 未就绪")
        return runtime.snapshot().model_dump(mode="json")

    @router.get("/v1/voices")
    async def voices() -> dict[str, Any]:
        """代理 SpeechRail 音色列表，供前端音色下拉。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/voices"
        )
        try:
            async with local_async_client(timeout=settings.ui.api_timeout) as client:
                resp = await client.get(
                    url,
                    headers=_speechrail_auth_headers(
                        settings.interaction.speechrail_api_key
                    ),
                )
                resp.raise_for_status()
                return dict(resp.json())
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail /v1/voices 请求失败: %s", exc)
            raise HTTPException(status_code=502, detail="SpeechRail 音色列表不可用") from exc

    @router.get("/v1/voices/clone/prompts")
    async def clone_prompts() -> dict[str, Any]:
        """获取音色克隆精选引导文案库（优先代理 SpeechRail，降级使用 Sona 精选库）。"""
        default_prompts = [
            {
                "id": "poetry_tang",
                "category": "classic",
                "title": "📜 盛唐气象 · 经典诗韵",
                "script": (
                    "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。"
                    "春江潮水连海平，海上明月共潮生。"
                ),
                "tips": "字正腔圆，声调平稳从容，注意句尾自然停顿。",
            },
            {
                "id": "prose_technology",
                "category": "tech",
                "title": "⚡ 科技浪潮 · 现代叙述",
                "script": (
                    "人工智能正在深刻改变我们的交互方式，让每一次人机对话都充满温度与智慧。"
                    "保持探索的热情，方能见证未来的无限可能。"
                ),
                "tips": "语速适中，吐字清脆明快，保持自然表达状态。",
            },
            {
                "id": "daily_dialogue",
                "category": "life",
                "title": "☕ 晨光午后 · 日常伴随",
                "script": (
                    "清晨的阳光透过窗棂洒在桌前，微风拂过绿植，带来清新怡人的气息。"
                    "今天也是从容充实的一天，随时为你提供帮助。"
                ),
                "tips": "语调温和亲切，如同与身旁好友促膝交谈。",
            },
            {
                "id": "philosophical_exploration",
                "category": "deep",
                "title": "🌌 星辰大海 · 哲思沉稳",
                "script": (
                    "浩瀚星空无垠深邃，人类对真理的探索永不止步。"
                    "唯有在宁静中沉淀思考，方能听见内心深处最真实的声音。"
                ),
                "tips": "低沉醇厚，字句饱满有力，略带思考的韵味。",
            },
        ]
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/voices/clone/prompts"
        )
        try:
            async with local_async_client(timeout=3.0) as client:
                resp = await client.get(
                    url,
                    headers=_speechrail_auth_headers(
                        settings.interaction.speechrail_api_key
                    ),
                )
                if resp.status_code == 200:
                    return dict(resp.json())
        except Exception:
            pass
        return {"object": "list", "data": default_prompts}

    @router.post("/v1/voices/clone")
    async def clone_voice(request: Request) -> Response:
        """代理 SpeechRail 录音克隆音色（上传参考音频与引导文本）。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/voices/clone"
        )
        try:
            form = await request.form()
            upload_file = form.get("audio")
            ref_text = form.get("ref_text")
            name = form.get("name")
            voice_id = form.get("id")

            if not upload_file or not hasattr(upload_file, "read"):
                raise HTTPException(status_code=400, detail="缺少录音音频文件 (audio)")
            if not ref_text or not str(ref_text).strip():
                raise HTTPException(status_code=400, detail="缺少朗读参考文本 (ref_text)")
            if not name or not str(name).strip():
                raise HTTPException(status_code=400, detail="缺少音色名称 (name)")

            audio_content = bytearray()
            max_limit = 15 * 1024 * 1024  # 15MB
            while chunk := await upload_file.read(64 * 1024):
                audio_content.extend(chunk)
                if len(audio_content) > max_limit:
                    raise HTTPException(status_code=413, detail="录音音频过大（上限 15MB）")

            if len(audio_content) < 1024:
                raise HTTPException(status_code=400, detail="录音音频内容过小或为空")

            filename = getattr(upload_file, "filename", "recording.wav") or "recording.wav"
            content_type = getattr(upload_file, "content_type", "audio/wav") or "audio/wav"

            data_fields: dict[str, str] = {
                "name": str(name).strip(),
                "ref_text": str(ref_text).strip(),
            }
            if voice_id and str(voice_id).strip():
                data_fields["id"] = str(voice_id).strip()

            auth_headers = _speechrail_auth_headers(settings.interaction.speechrail_api_key)
            async with local_async_client(timeout=settings.ui.api_timeout) as client:
                resp = await client.post(
                    url,
                    data=data_fields,
                    files={"audio": (filename, bytes(audio_content), content_type)},
                    headers=auth_headers,
                )
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
        except HTTPException:
            raise
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail POST /v1/voices/clone 请求失败: %s", exc)
            raise HTTPException(status_code=502, detail="SpeechRail 音色克隆服务响应异常") from exc
        except Exception as exc:
            logger.error("Sona: 处理音色克隆上传异常: %s", exc)
            raise HTTPException(status_code=500, detail="处理音色克隆上传失败") from exc

    @router.post("/v1/voices")
    async def create_voice(request: Request) -> Response:
        """代理 SpeechRail 创建自定义音色。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/voices"
        )
        try:
            body = await request.body()
            headers = {"Content-Type": "application/json"}
            auth_headers = _speechrail_auth_headers(settings.interaction.speechrail_api_key)
            if auth_headers is not None:
                headers.update(auth_headers)
            async with local_async_client(timeout=settings.ui.api_timeout) as client:
                resp = await client.post(url, content=body, headers=headers)
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail POST /v1/voices 请求失败: %s", exc)
            raise HTTPException(status_code=502, detail="SpeechRail 创建音色失败") from exc

    @router.delete("/v1/voices/{voice_id}")
    async def delete_voice(voice_id: str) -> Response:
        """代理 SpeechRail 删除自定义音色。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, f"/voices/{voice_id}"
        )
        try:
            auth_headers = _speechrail_auth_headers(settings.interaction.speechrail_api_key)
            async with local_async_client(timeout=settings.ui.api_timeout) as client:
                resp = await client.delete(url, headers=auth_headers)
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail DELETE /v1/voices/%s 请求失败: %s", voice_id, exc)
            raise HTTPException(status_code=502, detail="SpeechRail 删除音色失败") from exc

    @router.post("/v1/audio/speech")
    async def proxy_speech(request: Request) -> Response:
        """代理 SpeechRail 音频合成，供前端音色试听。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/audio/speech"
        )
        try:
            body = await request.body()
            headers = {"Content-Type": "application/json"}
            async with local_async_client(timeout=10.0) as client:
                auth_headers = _speechrail_auth_headers(
                    settings.interaction.speechrail_api_key
                )
                if auth_headers is not None:
                    headers.update(auth_headers)
                resp = await client.post(url, content=body, headers=headers)
                resp.raise_for_status()
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "audio/wav"),
                )
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail /v1/audio/speech 试听请求失败: %s", exc)
            raise HTTPException(status_code=502, detail="SpeechRail 语音合成不可用") from exc

    return router
