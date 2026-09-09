---
title: "Sona × SpeechRail 统一转录事实、展示投影与跨模式 UX 设计规格"
description: "以正文表和独立 attribution span 表统一支撑语音助手、会议、内心 OS 与实时字幕，消除逐字展示并保持正文与分人修订不变量"
status: accepted
type: technical_spec
category: architecture
version: "1.0.0"
date: 2026-09-09
last_updated: 2026-09-09
owners:
  - "sona-core"
  - "speechrail"
scope:
  - "sona.interaction"
  - "sona.meeting"
  - "sona.subtitles"
  - "sona.ui"
  - "SpeechRail realtime"
contracts:
  - "contracts/meeting-assistant/v1/"
---

# Sona × SpeechRail 统一转录事实、展示投影与跨模式 UX 设计规格

> 状态：accepted。本文是当前跨模式转录展示、会议 AI 输入和持久化重构的唯一执行基线。
>
> 模型边界：会议纪要与 Inner OS 固定使用 `local/kat-coder-2.5`。本规格不引入模型路由、模型替换或模型加载配置变更。

## 1. 决策摘要

当前系统把 ASR / diarization 的内部原子单元直接暴露给用户。中文 ASR 可能按字符产生 token，SpeechRail 又将每个 token 转为一个 `AttributionUnit`，Sona 再将其逐个写入 `transcript_segments`、字幕 cue 和会议卡片，因而出现“一个字一段”。会议纪要和 Inner OS 也直接遍历这些碎片，导致 AI 消费不可读稿件。

最终方案如下：

1. SpeechRail 保留可追溯的原子对齐事实；字符级 unit 只能作为事件内的对齐元数据，不能成为数据库正文行、UI 项或 AI evidence 行。
2. Sona 采用两张核心表：`transcript_items` 保存不可变完整正文，`transcript_attribution_spans` 保存可修订的说话人/时间对齐范围。
3. Sona 提供无持久化副作用的 `TranscriptPresentationProjector`，统一生成可读 `DisplayBlock`。
4. 会议实时展示、实时字幕、SRT/Markdown/TXT 导出和 AI 输入均从 projector 生成；普通 UI 不提供逐字版、原子版或时序调试页。
5. 会议纪要和 Inner OS 统一消费 `ModelTranscript`，模型固定为 `local/kat-coder-2.5`。
6. 语音助手保留自己的低延迟 turn 链，不依赖会议数据库、会议 projector 或 EOF barrier。
7. `RuntimeModeCoordinator` 继续保证单一 PCM owner；模式之间共享类型和校验，不共享不适用的状态与存储。

该方案与 SPK-E2E-1 的“正文不可变、分人原位修订、人工更正绝对优先”保持一致。SpeechRail 只需补充 source item、sequence、event version 和结构化诊断字段，不承担 UI 合并逻辑。

## 2. 当前证据与行业依据

- 当前 `append_completed_item` 按 attribution unit 插入数据库，造成正文与对齐范围耦合；summary formatter 和 Inner OS context formatter 又按 segment 构造 evidence。
- 截至 2026-09-09，`sona.transcript_segments` 约 40% 为单字记录；当前表规模约 352 KB，主要问题是语义错误而非性能瓶颈。
- AWS Transcribe、Azure Speech 和 Google Speech-to-Text 都将完整 phrase/transcript 与 word-level timestamp 分开；Teams 的会议 transcript 也以可读发言和匿名说话人标签为用户层表达。[AWS Transcribe](https://docs.aws.amazon.com/transcribe/latest/dg/how-input.html)、[Azure Speech](https://learn.microsoft.com/en-us/rest/api/speechtotext/transcriptions/transcribe?tabs=HTTP&view=rest-speechtotext-2024-05-15-preview)、[Google Speech-to-Text](https://docs.cloud.google.com/speech-to-text/docs/v1/async-time-offsets)、[Teams transcript](https://support.microsoft.com/en-gb/office/view-live-transcription-in-microsoft-teams-meetings-dc1a8f23-2e20-4684-885e-2152e06a4a8b?wapp_id=236c8229-99fc-4702-9960-d79daf8ee38d)。
- NIST 将 STT、句边界、diarization 和 Speaker-Attributed STT 分开评估，支持将文字可读性和说话人可靠性作为不同质量维度。[NIST Rich Transcription](https://www.nist.gov/itl/iad/mltg/rich-transcription-evaluation)

## 3. 领域边界与不变量

### 3.1 目标

- 用户看到的是连续、可扫读的语义发言块，而非 ASR 内部 token。
- 字幕低延迟，但不会因每个 delta 产生新行、新卡片或屏幕阅读器播报。
- 会议转录、实时字幕、可读导出在相同事实下具有一致的分块和标签。
- 任一展示块都能定位回一个或多个不可变 source item，满足审计、修订和问题排查。
- 说话人状态可解释：待确认、未启用、匿名稳定或服务不可用必须区分。
- 正文、时间和 source identity 不变；人工说话人更正不会被后续自动 patch 覆盖。

### 3.2 非目标

- 不在 Sona 重装、下载或运行 ASR/TTS/diarization 模型。
- 不在 SpeechRail 端做面向 UI 的句子重写、摘要或跨事实单元合并。
- 不把匿名声纹 cluster 推断为真实姓名。
- 不用展示合并替代数据库正文；历史数据迁移必须保留 source UID。
- 不更换 `local/kat-coder-2.5`，不改变 LM Studio 原生 `/api/v1/chat` 约束。

### 3.3 既有硬不变量

- completed 正文、时间和 `segment_uid` 不可变。
- diarization patch 只更新 speaker metadata，且人工更正具有最高优先级。
- PostgreSQL 不保存音频；会议数据库是会议元数据、正文、分人元数据和纪要的事实源。
- 会议 EOF 继续遵守 commit、`speechrail.diarization.done` 和持久化水位屏障。
- AudioHub 只有一个 PCM owner；语音助手、字幕和会议不会并行录音。

## 4. 核心数据模型

```text
SpeechRail completed event
        │
        ▼
sona.transcript_items
  完整正文、时间、source identity（不可变）
        │
        ├── sona.transcript_attribution_spans
        │     文本范围、音频范围、speaker metadata（可修订）
        │
        ▼
TranscriptPresentationProjector
        ├── DisplayBlock / subtitle / SRT / export
        └── ModelTranscript / summary / Inner OS
```

### 4.1 `transcript_items` 正文表

每个 completed item 一行：

```text
item_id, meeting_id, source_session_id, source_epoch
source_segment_uid, source_item_id, sequence
start_ms, end_ms, text, language, status, created_at
```

约束：`text`、时间范围、source identity 和顺序确认后不可修改；字符级 unit 不得拆成多行。唯一性使用 `(meeting_id, source_session_id, source_segment_uid)`，并保留幂等 event identity。

### 4.2 `transcript_attribution_spans` 归属表

每个正文范围一行，不重复存文本：

```text
span_id, item_id, text_start, text_end
audio_start_ms, audio_end_ms, timing_quality
speaker_key, speaker_status, speaker_confidence
speaker_revision, manually_corrected, candidates
```

约束：span 覆盖 `item.text` 的合法范围且同一 item 内不重叠；只允许修改 speaker、confidence、timing quality 和 revision 相关字段；`manually_corrected = true` 的 span 永远跳过自动 patch、平滑和 EOF 冲刷。

说话人修订历史使用独立的 `transcript_attribution_revisions` 审计表或等价事件表，不污染正文表。

### 4.3 所有权

SpeechRail 保持原始 token/时间范围与 `AttributionUnit` 的可追溯性，发送 completed 文本、音频范围、attribution units 与 diarization patch，并补充 source item、sequence、event version 和结构化诊断字段。

Sona 负责两张表的事务、幂等、修订屏障、speaker 语义、展示投影、可读导出和 AI 输入构造。`DisplayBlock` 是派生对象，不进入 PostgreSQL，不参与事实修订，也不获得独立事实身份。

## 5. TranscriptPresentationProjector

### 5.1 输入与输出

输入至少包含：

- `item_id` / `source_segment_uid`、完整 `text`、`start_ms`、`end_ms`；
- `source_session_id`、`source_epoch`、`source_item_id`；
- attribution spans、`speaker_key`、`speaker_status`、`speaker_name`、`manually_corrected`。

输出 `DisplayBlock`：

```text
block_id                 # 派生稳定 ID，不是事实主键
item_ids / source_ids
text
start_ms / end_ms
speaker_name / speaker_status
speaker_color_token
timing_quality
is_partial
```

### 5.2 默认聚合规则

1. `transcript_item` 是最小事实输入；默认先在同一 `source_session`、`source_epoch`、`source_item_id` 内聚合。
2. `speaker_key` 和 `speaker_status` 必须兼容，不能跨 speaker 聚合。
3. 相邻片段间隔超过 `1200ms` 时断开。
4. 单个 block 最长 `15000ms` 或 `180` 个字符，先满足的条件生效。
5. `。！？!?；;` 等强结束标点后优先断开；逗号、顿号和短语停顿不强制断开。
6. partial 只允许存在一个位于列表底部的活动 block；delta 原位更新，不追加新 block。
7. `unknown` speaker 只在同一 source item 且时间连续时聚合，不跨 item 猜测合并。
8. speaker patch 不改文字、时间或 source IDs，只刷新 speaker 元数据并重新投影。
9. timing unavailable 时可展示文本块，但不显示伪精确时间，也不提供错误的点击定位。

这些值是展示 profile 的默认值，不是事实约束。字幕、会议阅读和导出可以使用不同的长度/延迟 profile，但必须复用同一套边界语义、speaker 状态和 text conservation fixtures。

### 5.3 文本守恒与追溯

按 source order 拼接 DisplayBlock 的文本，必须等于 `transcript_items` 的 confirmed 正文；不可通过 trim、重写、去重或 overlap merge 改变正文。DisplayBlock 保留 source IDs、revision 和 timing metadata，供对账、AI evidence、JSON 导出和开发诊断使用，但这些字段不在普通 UI 页面渲染。

## 6. 四种模式的边界

| 模式 | 事实/处理路径 | 数据库 | 用户输出 | AI 输入 |
|---|---|---:|---|---|
| 语音助手 | Pipecat + VAD + 回声抑制 + 当前 turn | 否 | 对话气泡 | 当前 user turn |
| 实时字幕 | SpeechRail ASR + 内存 projector | 否 | DisplayBlock + 一个 partial | 无会议 AI |
| 会议实时展示 | completed event + 两表事务 + projector | 是 | 会议阅读块 | 不直接消费 span |
| 会议纪要 | 封存后读取正文并构造 ModelTranscript | 是 | 结构化纪要 | `local/kat-coder-2.5` |
| 会议 Inner OS | 会中读取 confirmed 正文并构造 ModelTranscript | 可选保存 | 私密卡片 | `local/kat-coder-2.5` |

### 6.1 语音助手

保持现有 `AudioHub → EchoSuppressionProcessor → SpeechRail STT → SelfEchoFilter → VAD/turn aggregation → local/kat-coder-2.5 → SpeechRail TTS` 热路径。语音助手不得依赖 PostgreSQL、meeting repository、会议 EOF barrier 或 attribution span；可共享底层事件解析和文本校验类型，但不共享会议事实生命周期。

### 6.2 实时字幕

字幕只维护有界内存 confirmed/partial snapshot，重连时重放 snapshot；不依赖 PostgreSQL。默认不启用 diarization，启用时只接收 speaker metadata。partial 只有一个底部活动块，confirmed 到达时替换/追加可读 block，不逐字符创建 DOM 节点。

### 6.3 会议实时展示

```text
completed event
 → 校验完整 item 与 spans
 → 事务写 transcript_items + transcript_attribution_spans
 → projector 生成 DisplayBlock
 → WebSocket 广播
```

diarization patch 只更新 span 的 speaker 元数据，重新投影并广播；不能修改正文、时间或 source identity。

### 6.4 会议纪要与 Inner OS

两者只读取 confirmed `transcript_items`，通过同一 `ModelTranscript` builder 生成 `[B0001]` 级完整发言输入。partial 不进入正式纪要；Inner OS 请求取消不阻塞会议 EOF 和封存；会后即焚仍遵守现有浏览器内存生命周期，只有用户主动保存才持久化。

## 7. UX 规则

- UI 只有一个会议主 transcript：可读阅读视图；删除逐字版、原子版和时序调试入口。
- 每个 block 显示 speaker 文本标签、状态徽标、轻量时间戳和完整正文；不通过颜色单独表达身份。
- 服务端返回明确状态：`identified`、`anonymous`、`pending`、`off`、`degraded`；前端不得解析 opaque `speaker_key` 猜测“是否识别”。
- `anonymous` 显示稳定的“说话人 1/2”；`pending` 显示“正在确认”；`off` 显示“分人未启用”；`degraded` 显示“分人不可用”。不再使用无解释的“未识别说话人”。
- 用户上移阅读时暂停自动跟随，显示“有新内容 · 回到底部”；speaker patch 只更新标签和颜色，不使全文跳动。
- partial 固定在底部、delta 原位更新；只对完整 block 确认、重连恢复和分人降级发送 `role="status"` 通知，不对每个字符播报。
- 纪要/Inner OS 证据点击后定位到可读 block 或时间点，不打开逐字稿。
- 深浅主题、键盘焦点、`role="log"` / `role="status"` 和对比度遵守项目 WCAG 2.1 AA/AAA 约束。

## 8. 接口、兼容与故障隔离

### 8.1 API

- 会议 v1 既有 `segments` 字段在迁移窗口内保留，由兼容适配器生成；新客户端优先使用可选 `display_blocks`。
- `display_blocks` 是服务端派生输出，客户端不能提交回服务器作为事实。
- 默认 API 不暴露 span 明细；JSON/开发诊断接口显式请求后才返回 source IDs、spans 和 revision。
- 字幕 payload 保留旧客户端所需字段，同时补充语义化 speaker status；旧客户端不会因新增字段失败。

### 8.2 模式隔离

- `RuntimeModeCoordinator` 保证 `assistant`、`subtitles`、`meeting`、`idle` 的单一 PCM owner 和原子切换。
- 语音助手和字幕不因会议数据库不可用而失败；会议数据库失败时使用 recovery journal。
- AI 纪要或 Inner OS 失败不影响会议录音、正文入库和字幕/阅读展示。
- diarization 降级只改变 speaker status，不阻塞正文和 EOF 封存。
- projector 异常时最多退回完整 `transcript_item`，禁止退回逐字展示。
- SpeechRail 重连使用 snapshot、source UID 和幂等事务恢复，禁止重复正文。

### 8.3 LM Studio

会议纪要和 Inner OS 固定使用 `local/kat-coder-2.5`，继续调用原生 `/api/v1/chat`，保留 `reasoning`、`max_output_tokens`、`store: false`、token/TTFT 统计和既有调度约束。不得把历史 assistant 压成 user text，也不得改回 OpenAI 兼容端点。

## 9. 迁移与实施顺序

1. 增加领域类型、两张表 migration、projector、ModelTranscript builder 和纯单元测试；不改变语音助手热路径。
2. repository 增加新表双读/幂等写入，旧 `transcript_segments` 只读兼容；新会议开始写入新表。
3. 会议实时展示、导出和 API 增量接入 DisplayBlock；前端取消逐字/原子页面入口。
4. 会议 summary 与 Inner OS 改为读取 ModelTranscript，并增加“禁止单字 evidence”测试。
5. 字幕接入内存 projector，验证重连、partial 替换和无数据库运行。
6. 完成历史数据校验和可回滚切换后，停止新写入旧 `transcript_segments`；旧兼容读取在一个发布窗口后移除。
7. SpeechRail 仅补 source identity、event version、结构化日志和契约测试，不改变 diarization 事实语义。

每一步均可独立回退；禁止一次性删除旧数据库数据，历史数据迁移必须先完成 source UID 对账和 text conservation 校验。

## 10. 验收矩阵

### 数据与事实

- 一个 completed item 对应一条完整正文记录；字符级 attribution unit 不产生正文行。
- span 覆盖合法、不重叠，且不重复存正文。
- speaker patch 只改 span；正文、时间、source identity 不变。
- 人工修正经过自动 patch、EOF、重连和重新投影后 100% 保持。
- display block 文本守恒 100%，source UID 可双向定位。

### 模式回归

- 语音助手连续 20 轮保持既有 VAD、回声抑制、TTS 和上下文行为，不访问会议表。
- 字幕在无 PostgreSQL 时仍可运行；partial 不产生逐字列表；重连无重复/丢失。
- 会议实时展示在 diarization `off` / `pending` / `degraded` 下均能继续显示正文。
- 纪要和 Inner OS prompt 只出现完整 block alias，不出现字符级 evidence；模型 ID 始终为 `local/kat-coder-2.5`。
- 模式切换不产生双重 PCM consumer；会议 EOF barrier、journal 和封存语义保持。

### UX 与无障碍

- 普通连续中文不出现单字独立展示块，除非原文确实是单字/极短 utterance。
- UI 不存在逐字版入口；证据只定位到可读 block。
- `identified`、`anonymous`、`pending`、`off`、`degraded` 可区分。
- 用户上移阅读时不被新内容抢滚动位置；键盘、屏幕阅读器、深浅主题和对比度测试通过。

## 11. 取舍与被替代内容

| 方案 | 结论 | 原因 |
|---|---|---|
| SpeechRail 直接合并成 UI 句子 | 不采用 | 事实、协议和展示耦合，削弱 patch 对账 |
| 只在前端合并 | 不采用 | 字幕、导出和 AI 仍会碎片化，多端规则漂移 |
| `transcript_segments` 同时保存正文和 attribution span | 不采用 | 一个字一行，正文与 speaker 修订耦合 |
| `transcript_items` + `transcript_attribution_spans` + Sona projector | 采用 | 正文不可变、分人可修订、展示和 AI 可读、模式可隔离 |
| 会议纪要/Inner OS 直接读取 span | 不采用 | 模型消费噪声且无法形成稳定证据语义 |

本规格替代旧的 `docs/solutions/会议助手实时转录体验优化方案.md`；该旧方案已删除，不再作为执行依据。历史 ADR、验收记录和 SpeechRail 协议归档仍保留，仅用于溯源，不得覆盖本文的当前决策。

## 12. 关联资料

- [SPK-E2E-1 端到端分人设计](/Users/hrygo/Documents/sona/docs/architecture/speaker-diarization-e2e-design.md:102)
- [会议助手后端运行与前后端联调](/Users/hrygo/Documents/sona/docs/manuals/会议助手后端运行与前后端联调.md:1)
- [ADR-007：有界会议纪要生成](/Users/hrygo/Documents/sona/docs/decisions/0007-bounded-meeting-summary-generation.md:40)
- [ADR-009：共享本地推理平台](/Users/hrygo/Documents/sona/docs/decisions/0009-shared-local-inference-platform.md:41)
- [本机 LM Studio 最佳实践](/Users/hrygo/Documents/本机优化配置/LM-Studio最佳实践.md:1)
