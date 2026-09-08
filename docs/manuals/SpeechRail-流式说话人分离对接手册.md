---
title: "SpeechRail v2 Realtime 分人对接手册"
description: "Sona 消费已发布 SpeechRail v2.0.0 OpenAI Realtime 分人协议的运行、事件与验收规范"
status: active
audience: "sona 工程团队（会议与实时字幕消费方）"
version: "2.0.0"
date: 2026-09-08
last_updated: 2026-09-08
---

# SpeechRail v2 Realtime 分人对接手册

> 本文是当前生效的 Sona 对接手册。协议基线为已发布的
> [SpeechRail v2.0.0](https://github.com/hrygo/SpeechRail/releases/tag/v2.0.0)，
> 服务契约以 [`contracts/realtime-openai.md`](../../../SpeechRail/contracts/realtime-openai.md) 为准。
> Sona 不加载 ASR、TTS 或 diarization 模型，模型生命周期由独立 SpeechRail 服务负责。

## 1. 运行边界

- 会议和实时字幕通过 `ws://127.0.0.1:8201/v1/realtime` 消费 OpenAI Realtime ASR。
- 分人是显式会话能力，默认关闭；会议与字幕分别由自己的配置开关控制。
- Sona 只保存 confirmed 文本、样本范围、匿名来源和 speaker 元数据，不保存 PCM、embedding 或 raw event。
- SpeechRail Realtime 会话不可透明恢复。断线后创建新的 session/source epoch，应用侧按 session identity 对账。

## 2. 配置与启动

```bash
export SONA_MEETING_DIARIZATION_ENABLED='false'

export SONA_SUBTITLE_SPEECHRAIL_URL='ws://127.0.0.1:8201/v1/realtime'
export SONA_SUBTITLE_SPEECHRAIL_API_KEY=''
export SONA_SUBTITLE_DIARIZATION_ENABLED='false'
```

会议与字幕共用 `SONA_SUBTITLE_SPEECHRAIL_URL` / `SONA_SUBTITLE_SPEECHRAIL_API_KEY` 作为 Realtime 连接配置，
会议另用 `SONA_MEETING_DIARIZATION_ENABLED` 控制是否 opt-in。API key 通过进程环境或本机安全配置注入，不写入仓库、日志和验收产物。启动 `sona-ui` 前确认 SpeechRail
的 `/health` 与 `/readyz`，并确认所需 ASR/diarization profile 已由 SpeechRail 预加载。Sona 不隐式下载或启动模型。

## 3. 会话协商

每条连接都必须先完成标准 OpenAI Realtime 握手：

1. 接收首个 `session.created`，记录服务分配的 `session_id`。
2. 发送标准 `session.update`，配置 `input_audio_transcription`、语言和 turn detection。
3. 等待 `session.updated`；标准回显不得包含 `speechrail` 扩展。
4. 只有显式开启对应设置时，再发送一次以下命名空间 opt-in：

```json
{
  "type": "session.update",
  "session": {"speechrail": {"diarization": {"enabled": true}}}
}
```

服务必须回显：

```json
{"enabled": true, "version": 1, "max_speakers": 4}
```

opt-in 必须发生在任何 PCM 之前且只能发送一次。未回显、profile 不可用或服务拒绝时，连接进入明确的
`off`/`degraded` 状态；不得试探另一个协议或启动第二个分人器。

## 4. 事件与数据约束

### 4.1 confirmed 正文

标准 `conversation.item.input_audio_transcription.completed` 提供 `item_id`、canonical `transcript`，
以及以 session 为时间域的 `audio_start_sample`/`audio_end_sample`。开启分人时，`attribution_units` 必须完整覆盖
transcript，范围使用 Python Unicode code point 半开区间；后端先切出可展示文本，前端不自行按 UTF-16 索引切分。

completed item 一旦写入 PostgreSQL，文本、时间和 item identity 不得改变。重复 completed 只做幂等确认；同一
source identity 携带不同正文或范围是协议错误。

### 4.2 分人事件

当前只接受以下三个扩展事件：

| 事件 | 作用 |
|---|---|
| `speechrail.diarization.updated` | 按 `segment_uid` 和连续 `revision` 提供 speaker patch；包含稳定水位 |
| `speechrail.diarization.status` | 报告 `active`、`degraded` 等运行状态和原因 |
| `speechrail.diarization.done` | EOF 后给出 `finalization_id`、`through_sample`、稳定水位和最后 update sequence |

扩展事件必须带顶层 `event_id`、`session_id`、递增 `sequence`。speaker 只能是 `A`–`D` 或 `null`；不同 session
即使返回相同标签，也不能在 Sona 中自动合并为同一人。所有 patch 经 typed decoder 和 speaker-only transaction
后落库，不改正文。

## 5. 结束与故障处理

开启分人的会议停止时：

```text
停止接收 PCM → input_audio_buffer.commit → 落库全部 completed
→ 等待 updated/status → 等待 done 与 repository watermark 对齐
→ 保存 complete/degraded → clear/close → 封存会议并排队纪要
```

`done` 缺失、超时、session 不匹配或 status 为 `degraded` 时，保留已确认正文，分人状态和原因对外可见，
不得伪造成功终态。未开启分人时按标准 ASR 终态快速关闭，不轮询分人水位。

人工更正写入 `manually_corrected=true` 后，任何后续自动 patch、状态冲刷和 EOF 处理都只能更新模型证据，
不能覆盖用户结果。

## 6. 联调与排障

- 首包不是 `session.created`、标准 session 回显含扩展、事件 sequence 回退或 patch revision 跳号：视为协议错误，
  保留正文并停止分人消费。
- 没有 speaker 不代表需要伪造一个默认人；检查 opt-in 回显、SpeechRail profile readiness 和 `degraded` 原因。
- 当前服务若请求 server-side VAD 但运行环境缺少 `onnxruntime`，会在 SpeechRail preflight 失败；这是服务部署依赖，
  不应通过 Sona 本地模型或隐藏 fallback 掩盖。可用手工 turn detection 独立验证 Realtime 分人协议。
- 真实验收只记录版本、事件计数、状态、哈希和错误类别，不记录 API key、PCM、完整 transcript 或服务 raw event。

## 7. 参考实现与门禁

- 客户端：`src/sona/speechrail/transport.py`、`transcriber.py`、`transcription_events.py`
- 会议屏障：`src/sona/meeting/session.py`、`finalization.py`
- 字幕会话：`src/sona/subtitles/sessions.py`、`proxy.py`
- 契约测试：`tests/asr/test_speechrail_v2_contract.py`、`tests/asr/test_speechrail_realtime.py`
- 端到端任务卡：[`2026-09-08-speechrail-openai-diarization-integration.md`](../superpowers/plans/2026-09-08-speechrail-openai-diarization-integration.md)
- 脱敏验收记录：[`2026-09-08 联合验收报告`](../operations/speechrail-openai-diarization-integration-acceptance.md)

变更后至少执行后端测试、`mypy`、`ruff`、前端测试和生产构建；真实服务联调必须使用独立临时输出目录，
不得把音频或凭据写入仓库。
