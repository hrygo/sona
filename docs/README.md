---
title: "Sona 文档中心"
description: "全本地实时语音交互、会议助手与实时字幕系统的技术文档总览、架构索引、状态生命周期与研发导航矩阵"
status: active
type: guide
category: architecture
version: "v2.3.0"
date: 2026-09-01
last_updated: 2026-09-09
author: "Sona Core Team"
owners:
  - "sona-core"
tags:
  - documentation
  - index
  - architecture
  - sitemap
  - guide
---

# 📚 Sona 文档中心

> 💡 **语源寓意**：`Sona` 源自拉丁语 *sonāre*（意为「**发出声音、回响、共鸣**」）。  
> 欢迎来到 **Sona** 技术文档中心。本项目是一套面向 Apple Silicon 硬件定制的全本地离线、超低延迟实时语音交互（Voice Assistant）、结构化会议助手（Meeting Assistant，含 SpeechRail diarization / PostgreSQL 持久化 / 异步 AI 纪要 / 崩溃恢复 Journal）与实时语音字幕（Live Subtitles）系统。

## 当前实现基线（2026-09-09）

> 当前交付与验收方案：[Sona × SpeechRail 会议讲话人分离端到端设计](architecture/speaker-diarization-e2e-design.md)（🟣 `implemented`）与
> [SpeechRail v2 对接实施计划](superpowers/plans/2026-09-08-speechrail-openai-diarization-integration.md)（🟡 `completed`），
> 详见 [2026-09-09 联合验收报告](operations/speechrail-openai-diarization-integration-acceptance.md)。协议基线为已发布
> [SpeechRail v2.0.0](https://github.com/hrygo/SpeechRail/releases/tag/v2.0.0) 公共协议；当前受管质量档为
> SpeechRail `2.0.3`，已从源码仓库构建 wheel 部署，`realtime_vad` 解析为 Silero 且 speech admission ready。
> 真实会议/字幕联合 smoke 与克隆音色的确定性、响度和输入信号校验已通过；克隆主观音质及非 `1.0` 速度能力仍按专项验收边界记录，SpeechRail 独立 DER/长时质量仍以其自身验收为准。

以下规则优先于历史方案、评测记录和早期实现说明：

- ASR 与 TTS 的模型、profile、进程和健康状态均由独立 SpeechRail 服务管理，默认地址为 `127.0.0.1:8201`。
- `sona` 只通过 SpeechRail **OpenAI Realtime (`/v1/realtime`) / REST** 客户端消费能力：字幕与会议使用 ASR OpenAI Realtime，语音助手使用 SpeechRail STT/TTS 与 LM Studio；仓库内不再运行本地 ASR/TTS worker、WhisperLiveKit 或旧 TTS bridge。
- `scripts/run-all.sh` 只启动 `sona-ui`；SpeechRail 必须单独启动并准备所需 snapshot/profile。Realtime 当前为不可透明恢复的会话，断线后由应用创建新会话并执行 source epoch/窗口对账。
- 会议分人使用 SpeechRail diarization 的持续分人扩展（Sortformer），应用侧负责不可变正文入库、原子原位 patch 修订与人工更正保护；本仓库不运行第二个分人生产者，也不运行本地 CAM++/AHC 声纹运行时。
- VAD 按模式单一归属：语音助手由 Sona/Pipecat 本地 Silero VAD 负责回合并向 SpeechRail 发 `manual` commit；标准字幕向 SpeechRail 发送 `server_vad`（静音 `400ms`），会议发送 `server_vad`（静音 `900ms`）。字幕与会议路径不再叠加 Sona 本地 VAD。
- SpeechRail 的 VAD 参数是端点策略，不是分人窗口；分人 activity 独立消费连续 PCM，只通过 `speechrail.diarization.updated/status/done` 修订 speaker 元数据。
- SpeechRail 的所有代码修改必须落在独立源码库并由源码 wheel 重新安装到 managed release；禁止直接编辑 `runtime/current`。
- “当前已实现”与“外部 SpeechRail 部署/模型的真实端到端验收”分开记录；未完成外部服务验收的内容不得写成已验证基线。

---

## 🚦 文档生命周期状态对照表（Status Legend）

为了清晰标识每篇文档的权威性与工程效力，本项目所有技术文档均在 YAML Frontmatter 中显式声明 `status`：

| 状态徽标 | 状态代码 (`status`) | 适用场景与定义 | 权威效力 |
|---|---|---|---|
| 🟢 **Active** | `active` | 核心系统架构、接口对接手册、当前生效的专项设计 | **权威基线**：当前系统正在运行和遵循的唯一事实源 |
| 🔵 **Accepted** | `accepted` | 架构决策记录 (ADRs) | **决策定稿**：团队已正式评审并批准采纳的技术决议 |
| 🟣 **Implemented** | `implemented` | 故障排障方案、已完成上线的规格设计与研发执行计划 | **已落地**：方案在代码库中已完全实现并验证通过 |
| 🟡 **Completed** | `completed` | 科学评测报告、联调验证记录、交接清单 | **已完成**：测试/评测/验收动作已结束，结论已归档 |
| ⚪ **Template** | `template` | 联调记录模板、报告模板 | **通用模板**：供后续发布/联调流程复用的标准模板 |
| 📦 **Archived** | `archived` | 历史预检数据集、探索性实验记录、早期演进文档 | **历史归档**：供技术溯源参考，不作为当前执行基线 |
| 🟠 **Blocked external** | `blocked_external` | 本地实现已完成，但依赖外部服务修复、部署或重新联调 | **未闭合**：不得写成完整验收通过 |
| 🟠 **Draft / Review** | `draft` / `under_review` | 方案初稿、跨团队评审签署中的草案 | **非正式**：尚处于评审讨论阶段，尚未进入主线 |

---

## 🗺️ 文档目录结构分层体系

```text
docs/
├── README.md                              # 🧭 本文档：文档中心总览与索引导航矩阵
│
├── architecture/                          # 🏗️ 系统总体架构与核心子系统设计
│   ├── 系统总体架构与详细设计方案.md       # 系统总体逻辑/物理架构、时序流与模块规范 (v2.5)
│   ├── Sona-核心架构重构方案与实施路径.md   # 核心架构治理、纪要解耦、包治理与实施路线图 (v1.0)
│   ├── 全链路语音交互与会议助手-技术方案与实施方案.md # 全链路端到端总体方案、当前基线、前沿调研与实施路线图 (v1.1.0)
│   ├── 实时语音交互与字幕-方案与最佳实践.md # 实时语音交互/字幕架构与单 PCM owner 仲裁契约 (v2.3.0)
│   └── 声学防回声与全双工交互设计方案.md   # 已落地后端 L1/L2 防回声与外放免提全双工边界
│
├── solutions/                             # 💡 专项技术方案与深度设计
│   ├── 会议模式多说话人精准识别与声纹聚类技术方案.md # 历史本地声纹方案（已归档）；当前实现见 SpeechRail diarization
│   └── Fun-ASR与现有ASR后端科学对比测试方案.md # SpeechRail 迁移前的 ASR 序贯盲测历史报告 (v1.3)
│
├── manuals/                               # 📖 开发对接与运行手册
│   ├── SpeechRail-Realtime-v2-语音转文字开发对接手册.md # SpeechRail Realtime v2 对接手册（已归档；当前基线为 OpenAI `/v1/realtime`）
│   ├── Qwen3-ASR-实时语音转文字开发对接手册.md # 历史兼容入口（已归档）
│   ├── 会议助手后端运行与前后端联调.md     # PostgreSQL环境准备、后端启动与前后端联调规范
│   ├── Sona-UI-设计方案.md        # 前端控制台架构设计、组件状态机与交互契约
│   └── 物理输出音频采集验收手册.md         # Helper 自动化门禁、人工 capture 与设备矩阵验收
│
├── operations/                            # 📋 协作交接、联调记录与排障分析
│   ├── 会议助手前后端分离式开发准备方案.md # 契约优先前后端分离路线与开发准备方案
│   ├── 会议助手前后端分离工作交接清单.md   # C0/B1/D1/F1/Q1 五类工作包交接清单与验收基准
│   ├── 前后端接线验证记录-2026-08-26.md    # 2026-08-26 前后端联调接线验证记录
│   ├── 联调记录模板.md                    # 标准前后端联调验收记录模板
│   ├── SpeechRail-OpenAI标准协议功能需求交割单.md # sona→SpeechRail 的 OpenAI 标准协议功能需求交割单
│   └── 语音交互打断后推理挂起故障排查与修复方案.md # Barge-in 打断导致 LM Studio 挂起故障排障与修复
│
├── decisions/                             # 📝 架构决策记录 (ADR-001 ~ ADR-012)
│   ├── 0001-single-owner-interaction-runtime.md
│   ├── 0002-lm-studio-stateful-chat-context.md
│   ├── 0003-lm-studio-context-compaction.md
│   ├── 0004-asr-sequential-evaluation.md
│   ├── 0005-server-side-runtime-workload-arbitration.md
│   ├── 0006-contract-first-meeting-assistant-separation.md
│   ├── 0007-bounded-meeting-summary-generation.md
│   ├── 0008-speaker-diarization-and-voiceprint-clustering.md
│   ├── 0009-shared-local-inference-platform.md
│   ├── 0010-physical-output-audio-capture.md
│   ├── 0011-speechrail-only-asr.md
│   └── 0012-speechrail-realtime-tts.md
│
└── superpowers/                           # ⚡ 历史执行计划与规格 (Plans & Specs 归档)
    ├── plans/                             # 研发执行计划 (14 份，含当前草案与历史归档)
    └── specs/                             # 设计规格 (14 份，含当前接受规格与历史归档)
```

---

## 🧭 按角色快速导航路径

```mermaid
graph TD
    User([开发者 / 贡献者]) --> Role{你的角色 / 任务}
    
    Role -->|系统架构 / 全局审计| Arc[1. 系统总体架构<br/>2. 决策记录 ADRs<br/>3. 工作负载仲裁]
    Role -->|后端研发| Be[1. 会议助手运行手册<br/>2. 架构详细设计<br/>3. 契约规范 contracts/]
    Role -->|前端研发| Fe[1. Sona UI 方案<br/>2. 前后端联调手册<br/>3. 统一转录展示规格]
    Role -->|AI / 算法评测| Algo[1. 统一转录展示规格<br/>2. SpeechRail 对接契约<br/>3. 历史评测与基准]
    Role -->|QA / 发布联调| Qa[1. 接线验证记录<br/>2. 联调记录模板<br/>3. 交接清单]

    Arc --> ArcDocs[docs/architecture/ & docs/decisions/]
    Be --> BeDocs[docs/manuals/ & contracts/]
    Fe --> FeDocs[docs/manuals/ & docs/operations/]
    Algo --> AlgoDocs[docs/solutions/]
    Qa --> QaDocs[docs/operations/]
```

---

## 📑 全量文档索引矩阵

### 标题规范

文档的一级标题和 Frontmatter `title` 使用稳定的规范名称，不混入日期、版本号、生命周期状态或“重构版”“优化版”“历史文件名”等过程标签。日期、版本和状态应分别放入 Frontmatter、正文元数据或文件名；只有验收记录、基准报告等本身以版本或日期区分的记录型文档，才在标题中保留必要的识别信息。

### 1. 系统总体架构与核心子系统 (`docs/architecture/`)

| 文档名称 | 状态 | 类型 | 版本 | 核心内容与设计要点 |
|---|---|---|---|---|
| [系统总体架构与详细设计方案](architecture/系统总体架构与详细设计方案.md) | 🟢 `active` | `architecture` | `v2.5` | **权威总体架构**：SpeechRail ASR/TTS 拓扑、模式 VAD 所有权、分层架构、交互/字幕/会议/控制端到端时序 |
| [Sona × SpeechRail 会议讲话人分离端到端设计](architecture/speaker-diarization-e2e-design.md) | 🟣 `implemented` | `technical_spec` | `v1.3.0` | **SPK-E2E-1 端到端设计**：双通道解耦、不可变正文入库、原子分人修订、人工更正最高优先级保护与 EOF 水位屏障 |
| [Sona 核心架构重构方案与实施路径](architecture/Sona-核心架构重构方案与实施路径.md) | 🟢 `active` | `architecture` | `v1.0.0` | **架构重构规范**：核心架构治理、纪要解耦、包治理与三阶段渐进式重构实施路线图 |
| [全链路语音交互与会议助手-技术方案与实施方案](architecture/全链路语音交互与会议助手-技术方案与实施方案.md) | 🟢 `active` | `architecture` | `v1.1.0` | **完整技术方案与实施路径**：当前 v2.0.3 边界、400/900ms VAD、断句/分人/对账、前沿调研与历史路线 |
| [实时语音交互与字幕-方案与最佳实践](architecture/实时语音交互与字幕-方案与最佳实践.md) | 🟢 `active` | `architecture` | `v2.3.0` | SpeechRail OpenAI Realtime `/v1/realtime` 语音交互与字幕技术方案、单 PCM owner 仲裁契约及验收边界 |
| [声学防回声与全双工交互设计方案](architecture/声学防回声与全双工交互设计方案.md) | 🟣 `implemented` | `architecture` | `v1.1` | 后端 L1/L2 防回声与 SubtitleProxy 音频门控；UI 融合仍标注为后续设计项 |
| [Sona 克隆音色质量闭环与声音工坊自量保障设计](architecture/voice-clone-quality-closed-loop.md) | 🟠 `under_review` | `technical_spec` | `v1.0.0` | 录音预检、SpeechRail 权威质量报告、固定 probe、清空/重启闭环、UI/UX 与噪声归因 |

### 2. 专项技术方案与深度设计 (`docs/solutions/`)

| 文档名称 | 状态 | 类型 | 版本 | 核心内容与设计要点 |
|---|---|---|---|---|
| [会议模式多说话人精准识别与声纹聚类技术方案](solutions/会议模式多说话人精准识别与声纹聚类技术方案.md) | 📦 `archived` | `domain_solution` | `v1.0` | 历史本地 CAM++/AHC 方案；当前实现为 SpeechRail diarization + speaker-only 映射，详见总体架构与 ADR-0011 |
| [Fun-ASR与现有ASR后端科学对比测试方案](solutions/Fun-ASR与现有ASR后端科学对比测试方案.md) | 🟡 `completed` | `benchmark_report` | `v1.3` | SpeechRail 迁移前的 Qwen3-ASR / Fun-ASR / SenseVoiceSmall 序贯盲测历史报告（Core 已触发 futility） |

### 3. 开发对接与运行手册 (`docs/manuals/`)

| 文档名称 | 状态 | 类型 | 版本 | 核心内容与设计要点 |
|---|---|---|---|---|
| [SpeechRail Realtime v2 语音转文字开发对接手册](manuals/SpeechRail-Realtime-v2-语音转文字开发对接手册.md) | 📦 `archived` | `manual` | `v2.0` | **历史**：旧手册；当前基线见[SpeechRail v2 Realtime 分人手册](manuals/SpeechRail-流式说话人分离对接手册.md)与联合验收报告 |
| [会议助手后端运行与前后端联调手册](manuals/会议助手后端运行与前后端联调.md) | 🟢 `active` | `manual` | `v1.1` | SpeechRail 独立依赖、会议运行手册、PostgreSQL 数据库准备、接口定义与前后端联调规范 |
| [Sona UI 设计方案](manuals/Sona-UI-设计方案.md) | 🟢 `active` | `guide` | `v1.1` | 前端控制台架构设计、SpeechRail 事件展示、单源麦克风控制面、组件状态机与交互契约 |
| [Sona 会议助手『内心 OS』前端 UI/UX 设计方案](manuals/Sona-会议助手-内心OS-UI-UX-设计方案.md) | 🟢 `active` | `specification` | `v1.0` | **内心 OS 专属设计方案**：私密副驾驶信息架构、事实/判断/草稿三层卡片、证据定位与状态机 |
| [物理输出音频采集验收手册](manuals/物理输出音频采集验收手册.md) | 🟠 `under_review` | `manual` | `v1.0` | 物理输出 Helper 自动化门禁、显式 30 秒 capture、隐私边界与全设备矩阵 |

### 4. 协作交接、联调记录与排障 (`docs/operations/`)

| 文档名称 | 状态 | 类型 | 版本 | 核心内容与设计要点 |
|---|---|---|---|---|
| [会议助手前后端分离式开发准备方案](operations/会议助手前后端分离式开发准备方案.md) | 🟡 `completed` | `technical_spec` | `v1.1` | 契约优先前后端分离路线、SpeechRail 适配边界、接口版本化与团队开发边界 |
| [会议助手前后端分离工作交接清单](operations/会议助手前后端分离工作交接清单.md) | 🟡 `completed` | `guide` | `v1.1` | C0/B1/D1/F1/Q1 五类工作包交接资料、SpeechRail 依赖边界、验收物与交接清单 |
| [前后端接线验证记录 (2026-08-26)](operations/前后端接线验证记录-2026-08-26.md) | 🟡 `completed` | `test_record` | `v1.0` | 会议助手前后端分离接线联调验证记录、测试结果矩阵与验收结论 |
| [会议助手前后端分离联调记录模板](operations/联调记录模板.md) | ⚪ `template` | `template` | `v1.0` | 每次契约/后端/前端版本发布前执行联调验收的标准记录模板 |
| [SPK-E2E-1 端到端联合验收报告 (2026-09-06)](operations/speaker-diarization-e2e-acceptance-2026-09-06.md) | 📦 `archived` | `test_record` | `v1.1.0` | 历史报告：记录发布前旧协议；不作为当前 SpeechRail v2 验收依据 |
| [SpeechRail v2.0.3 对接联合验收报告 (2026-09-09)](operations/speechrail-openai-diarization-integration-acceptance.md) | 🟡 `completed` | `test_record` | `v1.3.0` | **当前验收证据**：v2.0.0 协议契约、v2.0.3 源码构建运行时、400/900ms 模式 VAD、连续分人修订、字幕重入与会议 EOF 水位屏障 |
| [克隆音色稳定性验收 (2026-09-09)](operations/clone-tts-stability-acceptance-2026-09-09.md) | 🟡 `completed` | `test_record` | `v1.0.0` | SpeechRail v2.0.3 clone 采样确定性、响度冻结、参考音频校验与速度能力边界 |
| [SpeechRail-OpenAI标准协议功能需求交割单](operations/SpeechRail-OpenAI标准协议功能需求交割单.md) | 📦 `archived` | `technical_spec` | `v1.0` | **历史交割单**：记录 2026-09-02 的需求与差距快照；不替代已发布 v2 contract |
| [语音交互打断后推理挂起故障排查与修复方案](operations/语音交互打断后推理挂起故障排查与修复方案.md) | 🟣 `implemented` | `postmortem` | `v1.1` | SpeechRail 迁移前发生的 Barge-in 故障记录；EchoState、取消与状态机修复仍适用于当前链路 |
| [语音助手 TTS 爆音排查与验收手册](operations/语音助手-TTS-爆音排查与验收.md) | 🟢 `active` | `manual` | `v1.0` | 语音助手 CoreAudio overload 与长播报爆音排查、设备原生采样率/40ms 显式缓冲验收规范与回退机制 |

### 5. 架构决策记录 (`docs/decisions/`)

| ADR 编号 | 决策标题 | 状态 | 日期 | 核心决策要点 |
|---|---|---|---|---|
| [ADR-001](decisions/0001-single-owner-interaction-runtime.md) | 交互管道采用单一所有者运行时 | 🔵 `accepted` | 2026-08-20 | `sona-ui` 为交互管道唯一所有者，`sona-interact` 为互斥 headless 替代入口 |
| [ADR-002](decisions/0002-lm-studio-stateful-chat-context.md) | LM Studio 交互上下文采用原生有状态会话链 | 🔵 `accepted` | 2026-08-21 | 废弃 OpenAI 兼容端点，改用原生 `/api/v1/chat` + `reasoning: "off"` |
| [ADR-003](decisions/0003-lm-studio-context-compaction.md) | LM Studio 长会话采用结构化记忆预热与原子换链 | 🔵 `accepted` | 2026-08-21 | 基于输入 token 与 TTFT 动态监控，结构化摘要预热新链并原子换链 |
| [ADR-004](decisions/0004-asr-sequential-evaluation.md) | ASR 选型采用两阶段序贯盲测与 Finalist-Only 验收 | 🔵 `accepted` | 2026-08-24 | SpeechRail 迁移前的评测流程决策；当前运行时选型以 ADR-0011 为准 |
| [ADR-005](decisions/0005-server-side-runtime-workload-arbitration.md) | 服务端状态机统一仲裁语音推理工作负载 | 🔵 `accepted` | 2026-08-25 | `RuntimeModeCoordinator` 四模式状态机与单 PCM 所有者仲裁 |
| [ADR-006](decisions/0006-contract-first-meeting-assistant-separation.md) | 以契约优先支持会议助手前后端团队分离 | 🔵 `accepted` | 2026-08-26 | 单仓架构下以 `contracts/` 目录为唯一事实源，分离生产与消费 |
| [ADR-007](decisions/0007-bounded-meeting-summary-generation.md) | AI 会议纪要采用有界分段生成与服务端事件收敛 | 🔵 `accepted` | 2026-08-26 | 建立多层超时、字符熔断与 `output_limit` 边界，防止无限生成 |
| [ADR-008](decisions/0008-speaker-diarization-and-voiceprint-clustering.md) | 会议模式多说话人精准识别与声纹聚类 | 🔵 `accepted` | 2026-08-27 | 历史本地 CAM++/AHC 方案；当前运行时由 ADR-0011 的 SpeechRail diarization 路径替代 |
| [ADR-009](decisions/0009-shared-local-inference-platform.md) | LM Studio 原生协议与本地推理准入采用跨业务公共层 | 🔵 `accepted` | 2026-08-27 | 统一 SSE 语义、配置所有权、优先级调度和 Inner OS 边界 |
| [ADR-010](decisions/0010-physical-output-audio-capture.md) | 本地物理输出音频采用设备绑定的 Core Audio Tap 原生采集 | 🔵 `accepted` | 2026-08-31 | 原生 Helper、设备级作用域、双源混音与单 PCM 推理所有者 |
| [ADR-011](decisions/0011-speechrail-only-asr.md) | ASR 运行时统一由 SpeechRail 提供 | 🔵 `accepted` | 2026-08-31 | 移除本地 ASR worker/WhisperLiveKit；字幕、会议与交互统一使用 SpeechRail OpenAI Realtime `/v1/realtime` |
| [ADR-012](decisions/0012-speechrail-realtime-tts.md) | TTS 运行时统一由 SpeechRail 提供 | 🔵 `accepted` | 2026-09-01 | 移除旧 TTS bridge 与本地 TTS 运行时，应用负责播放、取消和回声状态协调 |
| [ADR-013](decisions/0013-vad-ownership-and-mode-boundaries.md) | 按运行模式划分 VAD 所有权 | 🔵 `accepted` | 2026-09-09 | assistant 使用 Sona 本地 VAD；字幕/会议使用 SpeechRail server VAD，避免双重 endpointing |

### 6. 历史计划与设计规格归档 (`docs/superpowers/`)

| 目录 | 数量 | 状态 | 说明 |
|---|---|---|---|
| [superpowers/plans/](superpowers/plans/) | 33 份执行计划 | 🟠 `draft` / 🟣 `implemented` | 当前研发计划与历史功能迭代任务清单；P0 见[音频源基础设施实施计划](superpowers/plans/2026-08-31-audio-source-foundation.md)，当前进入[P1 物理输出 Helper 实施计划](superpowers/plans/2026-08-31-physical-output-helper.md) |
| [superpowers/specs/](superpowers/specs/) | 14 份设计规格 | 🔵 `accepted` / 🟠 `under_review` / 🟣 `implemented` | 当前接受规格与历史技术整改设计及验证标准；当前转录基线见[统一转录事实、展示投影与跨模式 UX 设计规格](superpowers/specs/2026-09-09-transcript-presentation-and-ux-design.md) |

物理输出采集当前处于 P1 原生 Helper 阶段：IPC v1 契约位于
[`contracts/audio-capture/v1/`](../contracts/audio-capture/v1/)，`.app` 构建与无权限静态/枚举检查入口为
`scripts/build-audio-capture-helper.sh` 和 `scripts/test-audio-capture-helper.sh`。该阶段不增加页面来源选择，
也不改变会议、字幕和 PostgreSQL 数据边界；产品仍使用麦克风作为唯一业务输入。

---

## 📋 文档撰写与元数据最佳实践规范

新增或修改文档时，请务必在文档第一行声明规范的 YAML Frontmatter：

```yaml
---
title: "文档中文标题"
description: "文档一句话核心功能与摘要说明"
status: active | draft | under_review | accepted | implemented | completed | template | archived | blocked_external
type: architecture | domain_solution | technical_spec | manual | guide | decision_record | benchmark_report | test_record | postmortem | template | execution_plan
category: architecture | meeting | interaction | subtitles | asr | tts | frontend | quality_assurance
version: "1.0.0"        # 语义化版本（若适用）
date: 2026-08-27        # 创建日期
last_updated: 2026-08-27# 最后维护日期
author: "Voice Realtime Core Team"
owners:
  - "sona-core"
tags:
  - sona
  - keyword1
scope:
  - "sona.module"
related_documents:
  - "docs/architecture/系统总体架构与详细设计方案.md"
contracts:              # 若涉及前后端或外部通信协议
  - "contracts/meeting-assistant/v1/"
---
```
