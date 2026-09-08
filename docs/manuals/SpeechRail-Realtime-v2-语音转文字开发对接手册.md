---
title: "SpeechRail Realtime v2 语音转文字开发对接手册"
description: "指导客户端通过 SpeechRail Realtime v2 接入本地 ASR 与可选 diarization，并对接 REST 文件转写能力"
status: archived
type: manual
category: asr
version: "v2.0.0"
date: 2026-09-01
last_updated: 2026-09-08
author: "Voice Realtime Core Team"
owners:
  - "sona-subtitles"
tags:
  - speechrail
  - realtime-v2
  - websocket
  - rest-api
  - streaming-transcription
  - developer-guide
scope:
  - "sona.subtitles"
  - "sona.asr"
related_documents:
  - "docs/architecture/系统总体架构与详细设计方案.md"
  - "docs/architecture/实时语音交互与字幕-方案与最佳实践.md"
  - "SpeechRail repository: contracts/realtime-openai.md (external sibling project)"
---

# SpeechRail Realtime v2 语音转文字开发对接手册

> **历史归档（不作为当前实现依据）：** 本文记录发布前的旧对接草案。当前 Sona 以已发布的
> [SpeechRail v2.0.0](https://github.com/hrygo/SpeechRail/releases/tag/v2.0.0) 和
> [`SpeechRail-流式说话人分离对接手册.md`](SpeechRail-流式说话人分离对接手册.md)为准；当前协议为
> OpenAI `/v1/realtime` 标准握手加显式 `speechrail.diarization.*` 扩展。

> ⚠️ **本文档已归档（`archived`）**：本文只保留历史文件名和服务基础信息。Sona 当前以 SpeechRail OpenAI 兼容
> `WS /v1/realtime` 为准；流式 ASR、可选实时说话人分离与 EOF 采用当前标准事件。**当前对接基线见**
> [`SpeechRail-流式说话人分离对接手册`](SpeechRail-流式说话人分离对接手册.md)
> 与 [SpeechRail v2 联合验收报告](../operations/speechrail-openai-diarization-integration-acceptance.md)；本 v2 手册仅供参考，不再作为实现依据。
>
> 原 `Qwen3-ASR-实时语音转文字开发对接手册.md` 文件名保留为兼容入口，但其中的旧直连地址和
> 二进制 WebSocket 协议已废弃。

## 1. 服务基础信息

| 配置项 | 当前约定 |
|---|---|
| WebSocket | `ws://127.0.0.1:8201/v1/realtime` |
| 健康检查 | `GET http://127.0.0.1:8201/health`；就绪检查使用 `/readyz` |
| ASR model | `speechrail/qwen3-asr-1.7b` |
| 音频 | 16kHz、mono、signed 16-bit PCM（`s16le`） |
| 鉴权 | 可选 `Authorization: Bearer <key>`；key 不得放入 URL |
| 生命周期 | SpeechRail 独立管理模型、profile、worker 与健康状态；客户端只消费协议 |

SpeechRail 当前契约仍需通过实际部署完成后端 worker smoke/e2e 验收；服务返回
`backend_not_ready` 时，客户端应报告依赖未就绪，不得静默切换到本地模型。

## 2. 当前标准协议索引

连接后，客户端先接收 `session.created`，完成不含 `speechrail` 的标准 ASR `session.update` 与
`session.updated`。仅在业务开关显式开启时，首个 PCM 前再发送一次命名空间 opt-in：

```json
{
  "type": "session.update",
  "session": {
    "input_audio_transcription": {"model": "speechrail/qwen3-asr-1.7b", "language": "zh"},
    "turn_detection": {"type": "server_vad"}
  }
}
```

启用分人时追加的唯一请求为：

```json
{
  "type": "session.update",
  "session": {"speechrail": {"diarization": {"enabled": true}}}
}
```

服务必须回显 `{"enabled": true, "version": 1, "max_speakers": 4}`。opt-in 只能发送一次；拒绝、未回显或
profile/runtime 不可用时必须公开 `degraded`，不得试探旧协议或启动第二个分人器。

### 2.1 追加 PCM 与读取事件

实时音频不是 WebSocket 二进制帧，而是 JSON 中的 Base64 字段：

```json
{
  "type": "input_audio_buffer.append",
  "audio": "<base64-s16le-pcm>"
}
```

主要服务端事件如下：

| 事件 | 客户端处理 |
|---|---|
| `conversation.item.input_audio_transcription.completed` | 不可变的 confirmed item；启用分人时包含完整 `attribution_units` |
| `speechrail.diarization.updated` | 对已完成 `segment_uid` 的 speaker-only patch；revision 必须连续 |
| `speechrail.diarization.status` | 报告 `active`/`degraded` 及原因 |
| `speechrail.diarization.done` | EOF 终态；包含 `finalization_id`、水位和 `last_update_sequence` |
| `error` | 根据 `error.code` 与 `retryable` 决定报告或重试 |

所有时间戳相对当前 SpeechRail session 首个已接受 PCM 字节。应用如果有自己的 source epoch 或
窗口偏移，必须在 adapter 层转换；不能把不同 WebSocket 连接的 timestamp 直接混用。

### 2.2 flush、commit、cancel

- `input_audio_buffer.commit`：提交最后 PCM 并冲刷标准 ASR 结果；启用分人时随后发送一次
  `speechrail.diarization.finish`，继续消费 `updated/status/done`。
- `session.cancel`：丢弃未确认输入和 partial，终态为 `session.cancelled`。
- 断线不保证 terminal event。Realtime v2 不提供透明恢复；必须新建连接/session，并由应用记录
  source epoch 与可能的 gap。

会议结束必须等待 `speechrail.diarization.done`（如启用分人）及其持久化水位，再封存 confirmed 转录和
speaker patch；超时应标记 `finalization_timeout`。若 server-side VAD preflight 缺少 `onnxruntime`，
服务会返回 `backend_not_ready`，这是 SpeechRail 部署问题，不在 Sona 侧回退。

## 3. Python 对接骨架

以下示例展示协议顺序；生产代码还应加入超时、sequence 校验、背压和取消回收：

```python
import asyncio
import base64
import json

import websockets

WS_URL = "ws://127.0.0.1:8201/v1/realtime"


async def transcribe(
    pcm_chunks: list[bytes],
    api_key: str | None = None,
    diarization_enabled: bool = False,
) -> None:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    async with websockets.connect(WS_URL, additional_headers=headers) as ws:
        created = json.loads(await ws.recv())
        if created["type"] != "session.created":
            raise RuntimeError(created)

        await ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "input_audio_transcription": {
                    "model": "speechrail/qwen3-asr-1.7b",
                    "language": "zh",
                },
                "turn_detection": {"type": "manual"},
            },
        }))
        updated = json.loads(await ws.recv())
        if updated["type"] != "session.updated":
            raise RuntimeError(updated)
        if diarization_enabled:
            await ws.send(json.dumps({
                "type": "session.update",
                "session": {"speechrail": {"diarization": {"enabled": True}}},
            }))
            diarization_echo = json.loads(await ws.recv())
            if diarization_echo.get("session", {}).get("speechrail", {}).get("diarization") != {
                "enabled": True,
                "version": 1,
                "max_speakers": 4,
            }:
                raise RuntimeError(diarization_echo)

        for chunk in pcm_chunks:
            if not chunk or len(chunk) % 2:
                raise ValueError("PCM must be non-empty int16")
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(chunk).decode("ascii"),
            }))

        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        if diarization_enabled:
            await ws.send(json.dumps({
                "type": "speechrail.diarization.finish",
                "event_id": "client-finalize-1",
            }))
        else:
            await ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
        async for raw in ws:
            event = json.loads(raw)
            if event["type"] == "conversation.item.input_audio_transcription.completed":
                pass  # persist canonical item; do not print or log transcript data
            elif event["type"] == "error":
                raise RuntimeError(event["error"])
            elif event["type"] == "speechrail.diarization.done":
                break
            elif not diarization_enabled and event["type"] == "input_audio_buffer.cleared":
                break


if __name__ == "__main__":
    asyncio.run(transcribe([]))
```

`sona` 内部使用 `SpeechRailRealtimeClient` 与 `SpeechRailStreamingTranscriber` 统一校验 envelope、session、request 和 sequence；
优先复用对应 adapter，而不是在业务模块中重复实现协议解析。

## 4. REST 文件转写（非实时）

录音文件整段转写可使用 SpeechRail 的 REST 接口（具体 multipart 字段以 SpeechRail 当前 OpenAPI 为准）：

```bash
curl --fail --silent http://127.0.0.1:8201/v1/audio/transcriptions \
  -F "file=@meeting_record.wav" \
  -F "model=speechrail/qwen3-asr-1.7b" \
  -F "language=zh" \
  -F "response_format=json"
```

文件转写不是 `sona` 当前字幕/会议实时主链路；实时场景必须使用 `/v1/realtime`。

## 5. 排查清单

1. `backend_not_ready`：检查 SpeechRail worker、model snapshot 和 profile 就绪状态；不要联网隐式下载。
2. `invalid_event_order` / `sequence` 错误：确认首个客户端事件是 `session.update`，并且只消费当前 session 的递增事件。
3. 没有文字：确认是 JSON + Base64 PCM、16kHz mono int16，且在结束时发送 `commit`。
4. 没有 speaker：确认已收到精确 opt-in echo，并检查 SpeechRail profile readiness 和 `degraded` 原因。
5. 断线丢字：这是不可恢复 session；为新连接创建新 source epoch，并在应用层记录 gap/对账边界。
