---
title: "Sona × SpeechRail OpenAI Realtime 讲话人分离联合验收报告"
status: completed
type: acceptance_report
category: meeting
version: "1.2.0"
date: 2026-09-08
last_updated: 2026-09-09
owners: [sona-core]
tags: [speechrail, openai, realtime, diarization, subtitles, acceptance]
---

# Sona × SpeechRail OpenAI Realtime 讲话人分离联合验收报告

> 验收时间：2026-09-09（Asia/Shanghai）<br>
> 规范依据：[端到端设计](../architecture/speaker-diarization-e2e-design.md)、[实施计划](../superpowers/plans/2026-09-08-speechrail-openai-diarization-integration.md) 与已发布的 [SpeechRail v2.0.0](https://github.com/hrygo/SpeechRail/releases/tag/v2.0.0)。

## 结论

Sona 已完成 v2 OpenAI Realtime 接线、会议 speaker-only 事务、字幕/SRT 投影、`done` 水位屏障、人工更正优先、旧协议拒绝和前后端契约迁移。本地 fake 协议、事务、UI 与质量门全部通过。

SpeechRail 当前运行进程的 `/health` 与 `/readyz` 均为 HTTP 200 且 `ready=true`，ASR、TTS、diarization profile 与 `realtime_vad` 均报告 ready。此前 v2.0.1 的 `onnxruntime` preflight 阻塞已由维护者提交修复并部署到当前 v2.0.2 进程；直接使用 Sona 默认 `server_vad` 的 meeting handshake、真实 meeting capture 和真实字幕 stop 均已通过。本轮完整短语音字幕事件未再出现非法的 `tentative/stable + speaker=null`、`protocol_error`、`revision_gap` 或 `unknown_segment_uid`；合法的 `unknown + speaker=null` 保留为未知归属。Sona 不修改 SpeechRail，也不放宽客户端的协议校验。

因此，本报告状态为 **`completed`**：Sona 质量门、真实会议/字幕联合 smoke、字幕 clean tail 和最终 SRT 归档均已记录；SpeechRail 的 DER/cpCER、长文件、两小时资源行为和 RTTM/UEM 仍不在本任务的验收范围内。

## 1. 验收边界与证据原则

- Sona 只消费 `/v1/realtime` 的标准 OpenAI ASR 事件和 `speechrail.diarization.updated/status/done`；不加载或安装 ASR、TTS、diarization 模型。
- 会议正文是不可变 `completed` item；分人更新只修改 speaker 元数据，不修改正文、时间、顺序或 item identity。
- A–D 仅是 session/epoch 内匿名标签；Sona 不持久化 `speaker_links`、候选声学身份、embedding 或音频。
- 真实 smoke 只记录事件种类、计数、状态、序号、水位和哈希摘要，不记录 API key、PCM 或完整转写。外部音频使用脱敏短样本，未复制到 Sona 仓库。
- SpeechRail v2.0.0 是协议基线；本次复验的本机联调服务为兼容的 v2.0.2。此前 v2.0.1 的运行时限制作为历史证据保留，不改写 v2.0.0 合约结论。

## 2. Sona 质量门禁

| 门禁 | 实际结果 | 判定 |
|---|---|---|
| `SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/` | `1134 passed`；分支覆盖率 `83.09%`（门槛 `80%`） | PASS |
| `uv run mypy src/` | `Success: no issues found in 108 source files` | PASS |
| `uv run ruff check src/ tests/` | `All checks passed!` | PASS |
| `cd ui && npm test -- --run` | `46` 个测试文件、`353` 个测试通过；仅有既有 jsdom/React 警告 | PASS |
| `cd ui && npm run build` | `tsc --noEmit` 与 Vite production build 通过；仅有 chunk size 提示 | PASS |
| `git diff --check` | 无空白错误 | PASS |

测试数据库使用隔离临时 schema 并在测试后清理；没有把测试 DSN 指向生产数据。

## 3. 本地协议、事务和 UI 验收

已通过的 fake/隔离验收覆盖：

1. 所有连接先完成标准 `session.created`、标准 ASR `session.update`/`session.updated`；启用的会议或字幕只发送一次命名空间 opt-in，并校验精确 `{enabled: true, version: 1, max_speakers: 4}` 回显。
2. `completed` 的 Unicode `attribution_units` 无缝覆盖 canonical text；更新只引用同 session、已完成的 unit，并严格校验 revision、sequence、比例和 terminal `done`。
3. 旧 `speechrail.diarization.v1`、旧 `update/finalized`、旧字段、非 opted-in 分人事件、late opt-in 和 `.segment` 双写均确定性拒绝。
4. 会议 repository 的 speaker-only transaction 保持正文、时间、顺序、UUID 和 source identity 不变；重复 event 幂等，同 event ID 的不同 payload 拒绝；人工 override 始终优先。
5. 两个 session 即使都返回 `A`，也会得到不同的匿名 session/epoch 标签和 Sona opaque speaker key。
6. 字幕 `full_update.lines[].speaker` 始终为 scoped `str`，`diarization.status` 始终可见；numeric speaker payload 被拒绝，speaker patch 原位替换而不追加重复行。
7. meeting `done.last_update_sequence` 水位屏障、journal 回放、失联/错误/降级的有界清理，以及 disabled mode 无 `speechrail` opt-in 均有测试覆盖。

## 4. SpeechRail 运行时基线

最近一次健康探测（2026-09-09，Asia/Shanghai）结果：

- `/health`: HTTP 200，`version=2.0.2`，`asr_ready=true`，`tts_ready=true`，`diarization_ready=true`，`ready=true`。
- `/readyz`: HTTP 200，`ready=true`，diarization profile 为 `coreml-sortformer-fp16`。
- health payload 的 `realtime_vad` 显示配置为 `auto`、解析引擎为 `silero`，`ready=true`，消息为 `Silero VAD runtime and model are ready`。
- 直接使用 Sona `Settings()` 发起默认 `server_vad` 握手：成功建立 session，并收到精确的 diarization opt-in echo；未输出或持久化 API key。

补充历史：v2.0.1 曾因运行环境缺少 `onnxruntime` 被默认 `server_vad` preflight 拒绝；维护者修复提交为 [`6fed764`](https://github.com/hrygo/SpeechRail/commit/6fed764223fd34fae766fb47488928f1383c0f5e)，当前 v2.0.2 已恢复。结论：SpeechRail 核心服务与默认 `server_vad` meeting 会话当前正常，但这不替代字幕 clean tail 验收。

## 5. 真实 loopback smoke

### 5.1 会议（默认 server VAD）

使用真实 v2.0.2 Realtime 服务和 Sona meeting adapter，以默认 `server_vad`、20 秒脱敏短样本和实时节奏完成一次无转写内容落盘的聚合 smoke：

| 指标 | 结果 |
|---|---:|
| `ready` 事件 | 1 |
| snapshot 事件 | 8 |
| final 事件 | 5 |
| diarization 事件（updated + done） | 6 |
| 终态事件顺序 | `ready → snapshot/final/diarization → done` |
| `done.status` | `complete` |
| `done.last_update_sequence` | 40 |
| adapter status | `active` |
| consumer error | 无 |

该结果证明 Sona 对 v2 opt-in、默认 `server_vad`、`updated`、`done` 和会议终态路径接线正确；会议默认 VAD smoke 已通过。

### 5.2 实时字幕停止链路

最近一次使用真实服务、opt-in、默认字幕 VAD、完整约 7.3 秒语音和受控 stop 的 smoke 结果如下；输入在句尾自然结束，未人为截断：

| 指标 | 结果 |
|---|---:|
| 音频时长 | 7.301s |
| `full_update` 数量 | 5 |
| diarization status 计数 | `off=4, active=1` |
| 原始事件 | `completed=1, updated=1, done=1` |
| `updated` 载荷 | `unknown=1, speaker=null`（合法未知归属） |
| finish/done | `finish=1`；`done.status=complete`；`done.sequence=14`；`last_update_sequence=13` |
| 降级原因计数 | 无 |
| SRT archive | 1 个，133 bytes，SHA-256 前 12 位 `8777653104dd` |
| proxy state / last error | `stopped` / `None` |

原始追踪确认 `completed → updated → done` 顺序、`updated` UID 先由 `completed` 固定、revision 从 `1` 开始且 `stable_through_sample` 在 `done` 时与 `through_sample` 对齐；代理排空队列后只发出一次 `input_audio_buffer.commit` 和一次 `speechrail.diarization.finish`，写出最终归档并完成 stopped 清理。

补充的当前 v2.0.2 两源短 meeting capture（3.153s、同一 Realtime session）也完成 `completed=1 → updated=1 → done=1`，无 `worker_inference_error` 或连接错误；该 smoke 只证明两源输入的协议/终止接线，不宣称分人质量、DER 或 speaker 正确率。

本轮另有一次更长的多轮试样触发 SpeechRail `worker_inference_error`；该结果未作为本任务 AC 的通过依据，归入 SpeechRail backend 的多轮/长时质量范围。它不影响上面的完整短语音 clean-tail 证据，也不改变 Sona 的 fail-closed 行为。

## 6. Acceptance Criteria 矩阵

| AC | 判定 | 证据/说明 |
|---|---|---|
| AC-S1 | PASS | fake WebSocket + meeting/subtitle opt-in 测试：一次、pre-PCM、精确 echo |
| AC-S2 | PASS | 标准 ASR baseline 与 disabled control tests |
| AC-S3 | PASS | attribution tiling、immutable item 和 speaker-only transaction tests |
| AC-S4 | PASS | session/epoch 隔离、opaque key、无 speaker_links/embedding/audio 持久化 |
| AC-S5 | PASS | manual override precedence tests |
| AC-S5a | PASS（本地） | scoped string speaker、reconnect 隔离、原位替换和 SRT presenter tests |
| AC-S5b | PASS（本地） | `off/active/degraded` schema、UI 展示和 numeric speaker rejection tests |
| AC-S6 | PASS | 旧协议负面测试、无 batch overlay wiring；历史行仅保留读取兼容 |
| AC-S7 | PASS（本地 + 真实默认 VAD meeting） | finish/done、水位屏障、会议 finalization tests，以及 v2.0.2 默认 `server_vad` meeting smoke |
| AC-S7a | PASS（真实 v2.0.2） | 真实字幕 `completed → updated → done` clean tail；队列排空、`finish=1`、`last_update_sequence=13`、最终 SRT 归档和 stopped 清理 |
| AC-S8 | PASS（本地 + 真实降级清理） | malformed/lost done/degraded bounded outcome 与 stopped cleanup |
| AC-S8a | PASS（本地） | unavailable/unsupported negotiation 保留 baseline ASR 并只记一次 degraded |
| AC-S9 | PASS | Sona 全量门禁通过；已保存真实 v2.0.2 meeting capture 与 realtime subtitle 的脱敏事件聚合记录 |

## 7. 外部待办与重新验收条件

SpeechRail 问题已反馈给其维护者，由 SpeechRail 自己修复；VAD preflight 修复已部署到 v2.0.2。本次 Sona 变更不安装外部依赖、不修改 SpeechRail 工作树、不放宽协议校验。

本轮重新验收结果：

1. 实际 v2.0.2 `/health`、`/readyz`、`realtime_vad` 与默认 `server_vad` 均 ready；meeting 与 realtime subtitle session 均完成。
2. meeting 满足 `updated → done`、`last_update_sequence` 水位对齐和完整正文；subtitle stop 满足 queued PCM drain、单次 finish、尾部 update、`done`、最终 SRT archive 和 stopped 清理。
3. 当前真实字幕 session 未出现 `SPEECHRAIL_DIARIZATION_PROTOCOL_ERROR`、`revision_gap`、`unknown_segment_uid` 或 `finalization_timeout`；`unknown + speaker=null` 按协议保留为未知归属。
4. 本报告与任务卡已同步更新为 `completed`；不将服务 health 200 外推为 SpeechRail 独立质量（DER/cpCER、长时资源、RTTM/UEM）通过。
