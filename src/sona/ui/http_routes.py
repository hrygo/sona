"""UI HTTP 路由：health/services/runtime 与 SpeechRail voices/speech proxy。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import is_dataclass
from typing import Any, Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from sona.lm_studio import lm_studio_auth_headers, lm_studio_openai_models_url
from sona.network import local_async_client
from sona.ui.app_context import UIAppContext

logger = logging.getLogger(__name__)

NetworkScope = Literal["local", "network"]
_RUNTIME_DIAGNOSTIC_KEYS = ("audio_hub", "interaction", "subtitles", "tts", "last_transition")
_ASR_WORKLOAD_KEYS = ("workload", "ws_state", "reconnect_count", "last_event_age_ms")
_VOICE_CLONE_MAX_BODY_BYTES = 15 * 1024 * 1024
_VOICE_QUALITY_RUN_MAX_BODY_BYTES = 64 * 1024
_VOICE_PREVIEW_MAX_BODY_BYTES = 64 * 1024
_VOICE_WORKSHOP_BLOCKED_MODES = frozenset({"meeting", "subtitles"})
_PROXY_RESPONSE_HEADERS = (
    "content-type",
    "content-disposition",
    "x-request-id",
    "retry-after",
    "www-authenticate",
    "cache-control",
)


def _speechrail_health_url(rest_url: str) -> str:
    parsed = urlsplit(rest_url)
    scheme = "https" if parsed.scheme == "https" else "http"
    return f"{scheme}://{parsed.netloc}/health"


def _speechrail_rest_path(rest_url: str, path: str) -> str:
    return f"{rest_url.rstrip('/')}/{path.lstrip('/')}"


def _speechrail_auth_headers(api_key: str | None) -> dict[str, str] | None:
    return {"Authorization": f"Bearer {api_key}"} if api_key else None


def _request_id(request: Request) -> str:
    """读取或生成短 request id，错误响应可被前端和日志关联。"""
    value = request.headers.get("x-request-id", "").strip()
    if 0 < len(value) <= 64 and value.isprintable():
        return value
    return uuid4().hex


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "type": "server_error"
                if status_code >= 500 or retryable
                else "invalid_request_error",
                "request_id": _request_id(request),
                "retryable": retryable,
            }
        },
    )


def _proxy_response(
    response: httpx.Response,
    *,
    default_content_type: str = "application/json",
) -> Response:
    """保留上游状态、正文和安全响应头，避免错误被代理层改写。"""
    headers: dict[str, str] = {}
    for name in _PROXY_RESPONSE_HEADERS:
        value = response.headers.get(name)
        if value:
            headers[name] = value
    headers.setdefault(
        "content-type",
        "application/json" if response.status_code >= 400 else default_content_type,
    )
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=headers,
    )


def _proxy_request_headers(
    request: Request,
    *,
    content_type: str | None = None,
    api_key: str | None,
    preserve_content_length: bool = False,
) -> dict[str, str] | None:
    """只向 SpeechRail 转发必要的请求头，避免泄漏浏览器连接元数据。"""
    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type
    content_length = request.headers.get("content-length")
    if preserve_content_length and content_length:
        headers["Content-Length"] = content_length
    request_id = request.headers.get("x-request-id")
    if request_id:
        headers["X-Request-ID"] = request_id
    auth_headers = _speechrail_auth_headers(api_key)
    if auth_headers is not None:
        headers.update(auth_headers)
    return headers or None


def _speechrail_transport_error(
    request: Request,
    exc: httpx.HTTPError,
    *,
    message: str,
) -> JSONResponse:
    """将 Sona 代理自身的网络异常映射为稳定的公共错误。"""
    if isinstance(exc, httpx.TimeoutException):
        return _error_response(
            request,
            status_code=503,
            code="backend_timeout",
            message="SpeechRail 请求超时",
            retryable=True,
        )
    if isinstance(exc, httpx.NetworkError):
        return _error_response(
            request,
            status_code=503,
            code="speechrail_unavailable",
            message=message,
            retryable=True,
        )
    return _error_response(
        request,
        status_code=502,
        code="speechrail_bad_gateway",
        message="SpeechRail 返回无效响应",
        retryable=True,
    )


def _declared_body_size(request: Request, *, limit: int) -> Response | None:
    raw_length = request.headers.get("content-length")
    if raw_length is None:
        return None
    try:
        content_length = int(raw_length)
    except ValueError:
        return _error_response(
            request,
            status_code=400,
            code="invalid_content_length",
            message="请求体长度无效",
            retryable=False,
        )
    if content_length < 0:
        return _error_response(
            request,
            status_code=400,
            code="invalid_content_length",
            message="请求体长度无效",
            retryable=False,
        )
    if content_length > limit:
        return _error_response(
            request,
            status_code=413,
            code="payload_too_large",
            message=f"请求体过大（上限 {limit // (1024 * 1024)} MiB）",
            retryable=False,
        )
    return None


async def _bounded_request_stream(request: Request, *, limit: int) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _PayloadTooLargeError
        yield chunk


class _PayloadTooLargeError(Exception):
    """请求声明长度未知但实际流超出代理上限。"""


def _voice_workshop_guard(context: UIAppContext, request: Request) -> Response | None:
    """会议/字幕独占音频时禁止工坊请求，assistant 的静音录音流程仍可用。"""
    runtime = context.runtime
    if runtime is None:
        return _error_response(
            request,
            status_code=503,
            code="runtime_unavailable",
            message="运行时尚未就绪",
            retryable=True,
        )
    try:
        snapshot = runtime.snapshot()
        mode = getattr(getattr(snapshot, "mode", None), "value", snapshot.mode)
        owner = getattr(getattr(snapshot, "pcm_owner", None), "value", snapshot.pcm_owner)
        meeting_state = getattr(
            getattr(snapshot, "meeting_state", None), "value", snapshot.meeting_state
        )
    except Exception:
        return _error_response(
            request,
            status_code=503,
            code="runtime_unavailable",
            message="运行时状态不可用",
            retryable=True,
        )
    if (
        mode in _VOICE_WORKSHOP_BLOCKED_MODES
        or owner in _VOICE_WORKSHOP_BLOCKED_MODES
        or meeting_state in {"recording", "finalizing"}
    ):
        return _error_response(
            request,
            status_code=409,
            code="mode_conflict",
            message="会议或字幕正在占用音频资源，请结束当前模式后再使用声音工坊",
            retryable=False,
        )
    return None


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

    @router.get("/v1/models")
    async def models(request: Request) -> Response:
        """代理 SpeechRail 模型清单，供前端读取实际 TTS 能力。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/models"
        )
        try:
            async with local_async_client(timeout=settings.ui.api_timeout) as client:
                resp = await client.get(
                    url,
                    headers=_proxy_request_headers(
                        request,
                        api_key=settings.interaction.speechrail_api_key,
                    ),
                )
                return _proxy_response(resp)
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail /v1/models 请求失败: %s", exc)
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 模型能力不可用",
            )

    @router.get("/v1/voices")
    async def voices(request: Request) -> Response:
        """代理 SpeechRail 音色列表，供前端音色下拉。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/voices"
        )
        try:
            async with local_async_client(timeout=settings.ui.api_timeout) as client:
                resp = await client.get(
                    url,
                    headers=_proxy_request_headers(
                        request,
                        api_key=settings.interaction.speechrail_api_key,
                    ),
                )
                return _proxy_response(resp)
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail /v1/voices 请求失败: %s", exc)
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 音色列表不可用",
            )

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

    @router.post("/v1/voices/previews")
    async def preview_voice(request: Request) -> Response:
        """代理 SpeechRail 短生命周期自然语言声音设计试听。"""
        guard = _voice_workshop_guard(context, request)
        if guard is not None:
            return guard

        declared_size_error = _declared_body_size(
            request, limit=_VOICE_PREVIEW_MAX_BODY_BYTES
        )
        if declared_size_error is not None:
            return declared_size_error

        content_type = request.headers.get("content-type", "application/json")
        if not content_type.lower().startswith("application/json"):
            return _error_response(
                request,
                status_code=415,
                code="unsupported_media_type",
                message="声音试听请求必须使用 application/json",
                retryable=False,
            )

        try:
            body = await request.body()
            if len(body) > _VOICE_PREVIEW_MAX_BODY_BYTES:
                return _error_response(
                    request,
                    status_code=413,
                    code="payload_too_large",
                    message="声音试听请求体过大",
                    retryable=False,
                )
            settings = context.settings
            url = _speechrail_rest_path(
                settings.interaction.speechrail_tts_rest_url, "/voices/previews"
            )
            headers = _proxy_request_headers(
                request,
                content_type=content_type,
                api_key=settings.interaction.speechrail_api_key,
            )
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.post(url, content=body, headers=headers)
                return _proxy_response(resp, default_content_type="audio/wav")
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail /v1/voices/previews 请求失败: %s", exc)
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 声音试听服务不可用",
            )
        except Exception as exc:
            logger.error(
                "Sona: 处理声音试听请求异常: %s",
                type(exc).__name__,
            )
            return _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="声音试听请求处理失败",
                retryable=False,
            )

    @router.post("/v1/voices/designs")
    async def register_voice_design(request: Request) -> Response:
        """代理显式生成参考注册；不回退旧创建接口、不接触音频或模型。"""
        guard = _voice_workshop_guard(context, request)
        if guard is not None:
            return guard
        content_type = request.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            return _error_response(
                request,
                status_code=415,
                code="unsupported_media_type",
                message="音色设计注册必须使用 application/json",
                retryable=False,
            )
        size_error = _declared_body_size(request, limit=_VOICE_PREVIEW_MAX_BODY_BYTES)
        if size_error is not None:
            return size_error
        settings = context.settings
        try:
            # Count actual streamed bytes too; Content-Length is only an early hint.
            body = bytearray()
            async for chunk in _bounded_request_stream(
                request, limit=_VOICE_PREVIEW_MAX_BODY_BYTES
            ):
                body.extend(chunk)
            headers = _proxy_request_headers(
                request,
                content_type=content_type,
                api_key=settings.interaction.speechrail_api_key,
            )
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.post(
                    _speechrail_rest_path(
                        settings.interaction.speechrail_tts_rest_url, "/voices/designs"
                    ),
                    content=bytes(body),
                    headers=headers,
                )
                return _proxy_response(resp)
        except _PayloadTooLargeError:
            return _error_response(
                request,
                status_code=413,
                code="payload_too_large",
                message="音色设计注册请求体过大（上限 64 KiB）",
                retryable=False,
            )
        except httpx.HTTPError as exc:
            # Never log descriptions, vendor error bodies, credentials or paths.
            logger.warning("Sona: 音色设计注册传输失败 (%s)", type(exc).__name__)
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 音色设计注册服务不可用",
            )

    @router.post("/v1/voices/clone")
    async def clone_voice(request: Request) -> Response:
        """透明代理 SpeechRail 录音克隆音色，不在 sona 解析或持久化音频。"""
        guard = _voice_workshop_guard(context, request)
        if guard is not None:
            return guard

        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            return _error_response(
                request,
                status_code=415,
                code="unsupported_media_type",
                message="音色克隆请求必须使用 multipart/form-data",
                retryable=False,
            )
        declared_size_error = _declared_body_size(
            request, limit=_VOICE_CLONE_MAX_BODY_BYTES
        )
        if declared_size_error is not None:
            return declared_size_error

        idempotency_key = request.headers.get("idempotency-key")
        if idempotency_key is not None and (
            not 1 <= len(idempotency_key) <= 128
            or not all(character.isascii() and (character.isalnum() or character in "_-.")
                       for character in idempotency_key)
        ):
            return _error_response(
                request, status_code=400, code="invalid_idempotency_key",
                message="注册重试标识无效", retryable=False,
            )
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/voices/clone"
        )
        try:
            headers = _proxy_request_headers(
                request,
                content_type=content_type,
                api_key=settings.interaction.speechrail_api_key,
                preserve_content_length=True,
            )
            if idempotency_key is not None:
                headers = {**(headers or {}), "Idempotency-Key": idempotency_key}
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.post(
                    url,
                    content=_bounded_request_stream(
                        request, limit=_VOICE_CLONE_MAX_BODY_BYTES
                    ),
                    headers=headers,
                )
                return _proxy_response(resp)
        except _PayloadTooLargeError:
            return _error_response(
                request,
                status_code=413,
                code="payload_too_large",
                message="录音音频过大（上限 15 MiB）",
                retryable=False,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Sona: SpeechRail POST /v1/voices/clone 请求失败 (%s)", type(exc).__name__
            )
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 音色克隆服务不可用",
            )
        except Exception as exc:
            logger.error("Sona: 处理音色克隆上传异常: %s", type(exc).__name__)
            return _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="音色克隆请求处理失败",
                retryable=False,
            )

    @router.post("/v1/voices/clone/validate")
    async def validate_voice_clone(request: Request) -> Response:
        """代理 SpeechRail 仅校验、不注册的克隆质量报告。"""
        guard = _voice_workshop_guard(context, request)
        if guard is not None:
            return guard

        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("multipart/form-data"):
            return _error_response(
                request,
                status_code=415,
                code="unsupported_media_type",
                message="音色质量校验请求必须使用 multipart/form-data",
                retryable=False,
            )
        declared_size_error = _declared_body_size(
            request, limit=_VOICE_CLONE_MAX_BODY_BYTES
        )
        if declared_size_error is not None:
            return declared_size_error

        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/voices/clone/validate"
        )
        try:
            headers = _proxy_request_headers(
                request,
                content_type=content_type,
                api_key=settings.interaction.speechrail_api_key,
                preserve_content_length=True,
            )
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.post(
                    url,
                    content=_bounded_request_stream(
                        request, limit=_VOICE_CLONE_MAX_BODY_BYTES
                    ),
                    headers=headers,
                )
                return _proxy_response(resp)
        except _PayloadTooLargeError:
            return _error_response(
                request,
                status_code=413,
                code="payload_too_large",
                message="录音音频过大（上限 15 MiB）",
                retryable=False,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "Sona: SpeechRail POST /v1/voices/clone/validate 请求失败 (%s)",
                type(exc).__name__,
            )
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 音色质量校验服务不可用",
            )
        except Exception as exc:
            logger.error("Sona: 处理音色质量校验上传异常: %s", type(exc).__name__)
            return _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="音色质量校验请求处理失败",
                retryable=False,
            )

    @router.post("/v1/voices/{voice_id}/quality-runs")
    async def run_voice_quality(request: Request, voice_id: str) -> Response:
        """代理 SpeechRail 对已注册音色执行固定质量探针。"""
        guard = _voice_workshop_guard(context, request)
        if guard is not None:
            return guard

        content_type = request.headers.get("content-type", "application/json")
        if not content_type.lower().startswith("application/json"):
            return _error_response(
                request,
                status_code=415,
                code="unsupported_media_type",
                message="音色质量探针请求必须使用 application/json",
                retryable=False,
            )
        declared_size_error = _declared_body_size(
            request, limit=_VOICE_QUALITY_RUN_MAX_BODY_BYTES
        )
        if declared_size_error is not None:
            return declared_size_error

        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url,
            f"/voices/{voice_id}/quality-runs",
        )
        try:
            body = await request.body()
            if len(body) > _VOICE_QUALITY_RUN_MAX_BODY_BYTES:
                return _error_response(
                    request,
                    status_code=413,
                    code="payload_too_large",
                    message="音色质量探针请求体过大",
                    retryable=False,
                )
            headers = _proxy_request_headers(
                request,
                content_type=content_type,
                api_key=settings.interaction.speechrail_api_key,
            )
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.post(url, content=body, headers=headers)
                return _proxy_response(resp)
        except httpx.HTTPError as exc:
            logger.warning(
                "Sona: SpeechRail POST /v1/voices/%s/quality-runs 请求失败: %s",
                voice_id,
                exc,
            )
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 音色质量探针不可用",
            )

    @router.post("/v1/voices")
    async def create_voice(request: Request) -> Response:
        """代理 SpeechRail 创建自定义音色。"""
        guard = _voice_workshop_guard(context, request)
        if guard is not None:
            return guard
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
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.post(url, content=body, headers=headers)
                return _proxy_response(resp)
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail POST /v1/voices 请求失败: %s", exc)
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 创建音色服务不可用",
            )

    @router.delete("/v1/voices/{voice_id}")
    async def delete_voice(request: Request, voice_id: str) -> Response:
        """代理 SpeechRail 删除自定义音色。"""
        guard = _voice_workshop_guard(context, request)
        if guard is not None:
            return guard
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, f"/voices/{voice_id}"
        )
        try:
            auth_headers = _speechrail_auth_headers(settings.interaction.speechrail_api_key)
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.delete(url, headers=auth_headers)
                return _proxy_response(resp)
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail DELETE /v1/voices/%s 请求失败: %s", voice_id, exc)
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 删除音色服务不可用",
            )

    @router.post("/v1/audio/speech")
    async def proxy_speech(request: Request) -> Response:
        """代理 SpeechRail 音频合成，供前端音色试听。"""
        settings = context.settings
        url = _speechrail_rest_path(
            settings.interaction.speechrail_tts_rest_url, "/audio/speech"
        )
        try:
            body = await request.body()
            headers = _proxy_request_headers(
                request,
                content_type=request.headers.get("content-type", "application/json"),
                api_key=settings.interaction.speechrail_api_key,
            )
            async with local_async_client(
                timeout=settings.interaction.speechrail_tts_request_timeout_secs
            ) as client:
                resp = await client.post(url, content=body, headers=headers)
                return _proxy_response(resp, default_content_type="audio/wav")
        except httpx.HTTPError as exc:
            logger.warning("Sona: SpeechRail /v1/audio/speech 试听请求失败: %s", exc)
            return _speechrail_transport_error(
                request,
                exc,
                message="SpeechRail 语音合成不可用",
            )

    return router
