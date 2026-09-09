# Sona 实时转录展示与会议/字幕 UX 重构规格

- 日期：2026-09-09
- 状态：待评审
- 范围：Sona 与 SpeechRail 的实时字幕、会议转录、说话人展示、可读导出
- 前置结论：保留原子转录事实；在 Sona 侧统一生成可读展示投影

## 1. 摘要

当前系统把 ASR / diarization 的内部原子单元直接暴露给用户。中文 ASR 可能按字符产生 token，SpeechRail 又将每个 token 转为一个 `AttributionUnit`，Sona 再将其逐个转换为 `ASRSegment`、字幕 cue 和会议卡片，因而出现“一个字一段”。这不是人类阅读层的问题，而是事实层与展示层边界缺失。

本规格确定以下方案：

1. SpeechRail 继续输出可追溯、可修订的原子事实，不在协议层偷偷合并或重写正文。
2. Sona 新增统一的、无持久化副作用的 `TranscriptPresentationProjector`，将原子事实投影为可读 `DisplayBlock`。
3. 实时字幕、会议阅读视图、SRT/Markdown/TXT 导出统一使用展示投影；JSON、后台审计和开发诊断保留原子片段，普通 UI 不展示。
4. 会议 PostgreSQL 仍只保存 canonical completed item 与 speaker metadata；展示块每次按当前事实重新计算，收到说话人 patch 后原位刷新。
5. 说话人标签不再通过解析不透明 `speaker_key` 猜测数字。服务端返回稳定的匿名标签与明确状态，前端只消费 `speaker_status` / `speaker_name`。
6. UI 只提供可读阅读体验；原子/逐字事实不作为用户页面或页面内的“高级视图”，仅保留在后台事实、JSON 导出和开发诊断能力中。

该方案与项目已有的“正文不可变、分人原位修订、人工更正绝对优先”设计保持一致，且不要求 SpeechRail 更改现有 `speechrail.diarization.*` 协议。

## 2. 调研结论与设计依据

### 2.1 行业产品

Microsoft Teams 将 live transcript 作为带说话人和时间戳的会议记录展示，而不是将底层 token 暴露给用户；其实时字幕与 transcript 也是可独立选择的体验。[Teams live transcription](https://support.microsoft.com/en-gb/office/view-live-transcription-in-microsoft-teams-meetings-dc1a8f23-2e20-4684-885e-2152e06a4a8b?wapp_id=236c8229-99fc-4702-9960-d79daf8ee38d)

当无法将声音映射到真实身份时，Teams Rooms 使用 `Speaker 1`、`Speaker 2` 这类稳定匿名标签，并允许撤销识别；这比“Unknown”更能表达“系统区分出了不同声音，但不知道姓名”。[Teams intelligent speakers](https://support.microsoft.com/en-us/teams/calls-devices/use-microsoft-teams-intelligent-speakers-to-identify-in-room-participants-in-a-meeting-transcription)

### 2.2 评测与标准

NIST Rich Transcription 将 STT、句边界检测、说话人 diarization 与 Speaker-Attributed STT 分开评估。这说明“文字是否可读”和“说话人是否可靠”是不同质量维度，不能用 segment 数量或一个“识别成功/失败”字段代替。[NIST Rich Transcription Evaluation](https://www.nist.gov/itl/iad/mltg/rich-transcription-evaluation)

W3C 对动态状态消息的建议是：状态变化应以不抢焦点的方式暴露，live region 不应对每个微小变化都播报；`role="status"` 应保持礼貌通知，完整状态需要时使用原子更新。[WCAG 4.1.3 Status Messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)、[ARIA22](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22)

### 2.3 结合当前代码与日志的判断

- SpeechRail `alignment.py` 保留 Qwen3 ASR 的原始字符级范围；`realtime_openai.py::_build_units()` 每个 `TextUnit` 生成一个 `AttributionUnit`。
- Sona `transcriber.py::_segments_from_units()`、会议 repository、字幕 archive 和会议 SRT export 均沿用逐 unit 输出。
- 前端会议阅读视图已有局部 `deriveReadingBlocks()`，但字幕实时流和导出仍未复用，造成不同入口的阅读体验不一致。
- 当前 `speaker_display_label()` 与前端 `isRecognizedSpeakerKey()` 都对 opaque key 做数字格式猜测，A/B/UUID 等合法匿名 cluster key 被误显示为“未识别说话人”。
- 字幕设置默认关闭 diarization；因此“分人未启用”不能显示为“未识别”。
- 最新会议日志显示 EOF barrier、diarization done 与仓储水位对齐正常，没有证据表明“一个字一段”是断线或 EOF 故障。历史日志中的队列溢出、旧 worker 错误属于需补充可观测性验证的风险，不能直接归因到当前会议。

### 2.4 模型边界

模型选型不属于本次转录展示与 UX 改造范围。会议纪要和 Inner OS 均继续使用项目既定的 `local/kat-coder-2.5`，不新增模型路由、不更换模型、不修改模型加载配置，也不在 UI 暴露模型选择器。

行业调研只用于确认输出形态：会议纪要应结构化呈现概览、主题、决策、行动项、负责人、截止时间和待解决问题；Inner OS 应保留证据引用、明确不确定性并支持拒答。[Fireflies summary schema](https://docs.fireflies.ai/schema/summary)、[Fireflies meeting recap](https://fireflies.ai/blog/how-to-write-a-meeting-recap)

LM Studio 仍统一使用原生 `/api/v1/chat`，保留现有 `reasoning`、`max_output_tokens`、`store: false` 和 token/TTFT 统计约束；这些是接入方式，不构成模型更换。[LM Studio REST API](https://lmstudio.ai/docs/developer/rest)、[LM Studio chat](https://lmstudio.ai/docs/developer/rest/chat)

## 3. 产品目标与非目标

### 3.1 目标

- 用户看到的是连续、可扫读的语义发言块，而非 ASR 内部 token。
- 字幕低延迟，但不会因每个 delta 产生新行、新卡片或屏幕阅读器播报。
- 会议转录、实时字幕、可读导出在相同事实下具有一致的分块和标签。
- 任一展示块都能展开回原子 `segment_uid`，满足审计、修订和问题排查。
- 说话人状态可解释：用户能知道是待确认、未启用还是服务降级，而不是面对含义不明的“未识别”。
- 保持正文、时间和原子 ID 的不可变性，人工说话人更正不会被后续自动 patch 覆盖。

### 3.2 非目标

- 不在 Sona 重装、下载或运行 ASR/TTS/diarization 模型。
- 不在 SpeechRail 端做面向 UI 的句子重写、摘要或跨事实单元合并。
- 不把匿名声纹 cluster 推断为真实姓名。
- 不用展示合并替代数据库 canonical segments，也不删除已有原子数据。
- 不在本次方案中改变 OpenAI Realtime 或 `speechrail.diarization.v1` 的既有事件语义。

## 4. 核心架构决策

### 4.1 两层模型

```text
SpeechRail 原子事件
        │
        ▼
Sona canonical facts
  completed text/time/segment_uid
  speaker metadata + revision history
        │
        ▼
TranscriptPresentationProjector（纯函数、可重算）
        │
        ├── 实时字幕 payload / SRT cue
        ├── 会议阅读视图 display_blocks
        ├── 可读 SRT / Markdown / TXT
        └── 原子时序 / 审计视图的 source references
```

canonical facts 是唯一事实源；`DisplayBlock` 是派生对象，不进入 PostgreSQL，不参与 speaker patch，不获得独立事实身份。展示投影必须保留 `source_segment_uids`，并在任何一个源片段发生 speaker 修订后重新计算。

### 4.2 Sona 与 SpeechRail 的职责

SpeechRail：

- 保持原始 token/时间范围与 `AttributionUnit` 的可追溯性。
- 继续发送 completed 文本、音频范围、attribution units 与 diarization patch。
- 增加必要的结构化观测字段和测试，验证 unit 数量、source item 边界、序列号与 event version；不增加 UI 专用“句子合并”逻辑。

Sona：

- 在 `sona.asr` 或 `sona.meeting` 的展示层提供统一 projector。
- 用 projector 供字幕 session、会议 API/export 和前端阅读视图使用。
- 将 speaker 状态和匿名标签作为服务端语义输出给 UI。
- 保留 raw timeline / audit 入口，供调试与证据核对。

### 4.3 为什么不直接修改 SpeechRail 合并

在 SpeechRail 合并会把事实层、实时协议层和 UI 展示层耦合。它还可能掩盖 `segment_uid` 对应关系，使 Sona 无法安全应用 speaker-only patch。展示合并属于消费方语义：字幕、会议阅读、SRT 和审计视图的边界不同，应该由 Sona 统一但可配置地生成。

## 5. DisplayBlock 规则

### 5.1 输入与输出

输入至少包含：

- `segment_uid`
- `text`
- `start_ms`、`end_ms`（若 unavailable，不伪造精确定位）
- `speaker_key`、`speaker_status`、`speaker_name`
- `source_epoch` / `source_session`
- `source_item_id`（内部字段，优先于猜测 opaque `source_uid`）
- `manually_corrected`

输出 `DisplayBlock`：

- `block_id`：派生稳定 ID，不作为事实主键
- `text`
- `start_ms`、`end_ms`
- `speaker_name`
- `speaker_status`
- `speaker_color_token`
- `source_segment_uids`
- `is_partial`
- `timing_quality`

### 5.2 默认聚合算法

默认值沿用现有阅读视图的有效经验，并收敛为后端单一实现：

1. 仅在同一 `source_session`、`source_epoch`、`source_item_id` 范围内聚合；稳定 speaker 的连续 completed item 可在明确连续条件下跨 item 聚合。
2. `speaker_key` 和 `speaker_status` 必须兼容，不能跨 speaker 聚合。
3. 相邻片段间隔超过 `1200ms` 时断开。
4. 单个 block 最长 `15000ms` 或 `180` 个字符，先满足的条件生效。
5. `。！？!?；;` 等强结束标点后优先断开；逗号、顿号、短语停顿不强制断开。
6. partial 只允许存在一个位于列表底部的活动 block；delta 更新原 block，不追加新 block。
7. unknown speaker 只在同一 source item 且时间连续时聚合；不跨 item 猜测合并，避免把两个未知说话人拼成一段。
8. 收到 speaker patch 后不改动文字、时间或 source IDs，只刷新 block 的 speaker 元数据并重新计算相邻边界。
9. timing unavailable 时允许展示文本块，但不显示伪精确时间，也不提供错误的点击定位。

这些值不是事实约束，而是展示 profile 的默认值；后续可以为字幕、会议阅读和导出定义不同 profile，但必须复用同一套边界语义和测试 fixtures。

### 5.3 后台可追溯性

`DisplayBlock` 必须保留 `source_segment_uids`、revision 和 timing metadata，供服务端对账、AI 证据引用、JSON 导出和开发诊断使用；这些字段不在普通 UI 页面渲染，也不提供逐字/原子片段页面。

用户侧只提供“定位到这段可读发言”的能力：跳转到对应的阅读块或时间位置，不打开字符级拆分结果。展示块的文本仍必须满足守恒：按 source order 拼接 display blocks 的文本，等于 canonical completed segments 的文本；不可通过 trim、重写、去重或 overlap merge 改变正文。

## 6. 会议与字幕 UX 重构

### 6.1 信息架构

主界面分为三层：

1. 顶部状态栏：录音状态、麦克风、连接状态、分人状态和已记录时长。
2. 主阅读区：默认“阅读视图”，显示 DisplayBlock。
3. 辅助工具区：搜索、回到底部、导出和说话人管理；不提供逐字版、原子版或时序调试页面。

不再把“19 个 segments”作为主信息。用户更关心“已记录多久、当前有几位匿名说话人、是否仍在识别”。原子数量不展示在用户 UI，仅写入后台观测和开发诊断数据。

### 6.2 阅读视图

每个发言块包含：

- 左侧稳定 speaker color rail；颜色只作辅助，不是唯一身份线索。
- `speaker_name` 与状态徽标。
- 轻量时间戳；时间不可用时隐藏精确时间并显示“时间信息有限”。
- 大字号、舒适行高的正文；块间留白优先于密集卡片边框。
- hover/focus 后显示“重命名”“定位到此处”。

默认滚动策略：用户接近底部时自动跟随；用户向上阅读后暂停自动滚动，显示“有新内容 · 回到底部”，点击后恢复跟随。speaker patch 只更新现有块的标签和颜色，不导致全文跳动。

### 6.3 实时字幕

- partial 固定在底部一个“正在识别”区域，delta 只更新文字。
- completed 到达时平滑替换 partial，不闪烁、不重复、不产生一个字一行。
- 确认块以 1–2 行为优先目标，过长才换行/换块；字幕不能因追求块少而延迟过长。
- 连接重连时显示状态提示，但不把技术错误插入字幕正文。
- SRT 预览使用同一 DisplayBlock projector；用户下载“可读字幕”时获得语义块，下载 JSON 时获得原子事实。

### 6.4 会议阅读与后台证据定位

会议 UI 只有一个面向用户的主 transcript：阅读视图。它面向持续阅读和会后回看，按说话人和语义边界分块。partial、speaker pending、degraded、timing unavailable 等状态以用户语言显示在阅读块或顶部状态栏中，不把内部事件列表变成另一个页面。

后台仍需维护 raw canonical segments、source references 和 revision history，用于 AI 证据、JSON 导出、日志排查和自动化测试；这属于系统可验证性，不属于普通用户 UX。用户从纪要或 Inner OS 证据点击后，只定位到可读发言块/时间点，不进入逐字版。

阅读视图与后台事实必须保持：

- 正文完全一致；
- source segment 可由服务端双向定位；
- speaker patch 的结果一致；
- 阅读视图不重写事实，只改变分组和默认信息密度；原子事实不在 UI 展示。

### 6.5 说话人展示与操作

服务端语义优先级：

| 状态 | 默认显示 | 用户解释 |
|---|---|---|
| `stable` | `说话人 1` | 已形成稳定匿名声纹簇，尚未证明真实姓名 |
| `tentative` | `正在确认 · 说话人 1` | 当前归属可能被后续证据修订 |
| `unknown` | `说话人待确定` | 暂无可靠说话人归属 |
| `disabled` | `分人未启用` | 当前模式没有请求 diarization |
| `degraded` | `分人不可用` | 分人服务异常或已降级，但文字仍继续记录 |
| 用户命名 | 用户自定义名称 | 人工更正优先，自动 patch 不得覆盖 |

“说话人 1/2”是匿名区分，不代表系统知道真实姓名。详情提示明确写出：“系统只区分声音簇，不会自动推断真实身份；你可以手动重命名。”

前端禁止通过 `speaker_key` 格式判断是否识别；只能消费 `speaker_status`、`speaker_name` 和服务端提供的稳定颜色 token。所有 speaker 列表按首次出现顺序稳定编号，不能因重连或 patch 重新洗牌。

## 7. 无障碍与视觉规范

- 新增内容使用 `role="log"` 或等价的可读区域；状态变化使用 `role="status"`，不抢焦点。
- 每个字符 delta 不触发屏幕阅读器播报；只对“开始录音、连接恢复、分人降级、一个完整发言块确认”等有意义事件做节流通知。
- `aria-live="polite"` 为默认；焦点在用户上移阅读时不自动移动。
- speaker 不能只靠颜色区分，必须同时有文本标签和状态。
- 遵守项目现有 WCAG 2.1 AA/AAA 对比度要求；状态徽标、边框、按钮与浅色/深色主题均需复核。
- 阅读区使用明确的键盘焦点样式；“回到底部”“定位到此处”“重命名说话人”均可键盘操作。

## 8. 接口与兼容性策略

### 8.1 第一阶段：内部 projector，无破坏性协议变更

- 保持会议 v1 `segments` 为 canonical raw segments。
- 字幕实时 payload、字幕归档、会议 SRT/Markdown/TXT export 改用 projector。
- 会议前端在兼容窗口内继续支持本地 `deriveReadingBlocks()`，但其规则必须与后端 fixtures 对齐；用户界面不提供 raw/逐字切换。
- 增加 `source_item_id` 到内部模型链路，避免从 opaque ID 推断 item 边界。

### 8.2 第二阶段：会议 API 增量提供 display blocks

在 `transcript-response` 与 `event-transcript-reconciled` 增加可选 `display_blocks`，保持 `additionalProperties: false` 下的 schema、OpenAPI、AsyncAPI、fixtures 同步更新。旧客户端只读 `segments` 仍可工作；新客户端优先使用服务端 block，断线或旧服务时回退本地 projector。

`display_blocks` 不写数据库，不替代 `segments`，不允许客户端提交回服务器作为事实。服务端发出的 block 必须含 `source_segment_uids`，以便校验守恒与 speaker patch 后刷新。

### 8.3 SpeechRail 改动边界

SpeechRail 本次只补：

- realtime unit 构造的 source item / sequence / event version 观测字段；
- 字符级 unit、空文本、重复 UID、越界时间的契约测试；
- 对 Sona 可诊断的结构化日志。

不改变 completed 事件的正文和 attribution semantics，不引入展示 block 字段，不在 SpeechRail 端做跨 unit 的可读合并。

### 8.4 模型配置边界

会议纪要和 Inner OS 继续共用 `local/kat-coder-2.5`。本次不拆分模型字段，不新增 fallback，不修改模型加载或运行配置；仅保留现有的 reasoning、输出上限、`store: false`、prompt version 和 token stats 约束。

## 9. 可观测性与验收指标

### 9.1 必备指标

分别统计，禁止用一个“segment count”代表整体质量：

- `raw_segment_count`
- `display_block_count`
- `display_compression_ratio = raw_segment_count / display_block_count`
- `partial_to_completed_replacement_ms`
- `display_block_update_count`
- `speaker_status_counts`：stable / tentative / unknown / disabled / degraded
- `speaker_patch_latency_ms`
- `speaker_patch_overwrite_attempts`（manual correction 被跳过应计数）
- `text_conservation_failures`
- `source_uid_or_item_boundary_violations`
- `timing_unavailable_count`

### 9.2 质量目标

- 普通中文连续发言不出现单字独立展示块，除非原文确实是单字/极短 utterance。
- 相邻普通发言块达到可读密度；默认规则下不会因合并而跨说话人、跨 source epoch 或跨不安全 item 边界。
- 字幕实时更新不因每个字符产生 DOM 列表项或屏幕阅读器通知。
- display block 文本守恒 100%。
- 人工 speaker override 在自动 patch、EOF flush、重连恢复和重新投影后 100% 保持。
- `unknown`、`disabled`、`degraded` 在 UI 上可区分，且都不再显示笼统“未识别说话人”。
- 断线重连后不重复、不丢失已确认 display block；必要时通过 source UID 去重。

### 9.3 必测场景

1. 中文逐字 ASR unit 连续输入，最终显示为一个或少量语义块。
2. 两个说话人交替，绝不跨 speaker 合并。
3. 同一 speaker 长段发言，按时间/字符上限稳定断开。
4. unknown 连续片段与不同 source item 的边界。
5. stable → tentative → stable 的 speaker 更新只改标签，不改正文。
6. 人工更正后接收自动 patch、EOF flush、重连恢复。
7. subtitle diarization disabled / degraded / available 三种 UI。
8. 用户上移阅读时继续来字，不抢滚动位置；回到底部后恢复跟随。
9. 页面刷新/重连与 SRT、Markdown、TXT、JSON 导出的文本守恒。
10. 键盘操作、屏幕阅读器状态节流与深浅主题对比度。

## 10. 分阶段实施顺序

### P0：统一展示投影与标签语义

- 实现 projector 与单元/集成测试。
- 接入字幕实时展示和所有可读导出。
- 修复服务端 speaker label/status 语义，移除前端 opaque-key 猜测。
- 保持会议纪要与 Inner OS 使用 `local/kat-coder-2.5`，仅补充展示投影、UX 和文本守恒评测。
- 增加 display/raw 计数与文本守恒日志。

### P1：会议 API 与前端主体验

- 增加可选 `display_blocks` 契约字段。
- 会议阅读视图优先消费服务端 block，保留兼容回退。
- 重构滚动跟随、partial 替换、speaker 状态徽标和可读证据定位。
- 删除逐字/原子/时序页面入口；原子数据只保留在后台和 JSON/开发诊断路径。

### P2：联调与体验验收

- 用真实 SpeechRail realtime payload 覆盖字符级 token、patch、重连、EOF。
- 对最新发现的 unavailable unit 数量不一致问题增加 session/run/version 关联日志，确认是否存在旧进程、回退路径或 payload 版本差异。
- 完成前端无障碍与视觉验收、导出对账、性能回归。

## 11. 方案取舍

| 方案 | 结论 | 原因 |
|---|---|---|
| 直接在 SpeechRail 合并 token | 不采用 | 事实层与展示层耦合，可能破坏 patch 对账；字幕/会议/审计需求不同 |
| 只在前端合并 | 不采用 | 导出和字幕仍会碎片化；多端实现漂移；屏幕阅读器仍可能收到高频 delta |
| 只把 ASR window 调大 | 不采用 | 无法解决 speaker 边界、标点、导出一致性和 unknown 语义 |
| 激活现有 `diarization_smoother` | 不采用 | 当前逻辑会重建 normalized segment，可能丢失 SPK-E2E 字段并改变原始事实 |
| 后端 projector + 前端兼容回退 | 采用 | 统一跨入口体验，同时保持 v1 客户端与分阶段发布的可回退性 |

## 12. 评审结论

本规格建议先提交评审，再进入 implementation plan。评审重点不是是否允许“合并”，而是确认：

- `DisplayBlock` 作为派生展示对象的边界；
- unknown speaker 只在安全 source item 内聚合的保守规则；
- 会议 API 是否在 P1 增加可选 `display_blocks`；
- “UI 只提供阅读视图，原子事实不作为页面入口”的产品边界；模型固定为 `local/kat-coder-2.5`，不纳入本次改造讨论。

确认后再按 P0 → P1 → P2 编写实施计划与测试矩阵。

## 13. 参考资料

### 外部资料

1. Microsoft. [View live transcription in Microsoft Teams meetings](https://support.microsoft.com/en-gb/office/view-live-transcription-in-microsoft-teams-meetings-dc1a8f23-2e20-4684-885e-2152e06a4a8b?wapp_id=236c8229-99fc-4702-9960-d79daf8ee38d)。用于说话人、时间戳和 transcript 与 live captions 分层的产品依据。
2. Microsoft. [Use Microsoft Teams intelligent speakers to identify in-room participants](https://support.microsoft.com/en-us/teams/calls-devices/use-microsoft-teams-intelligent-speakers-to-identify-in-room-participants-in-a-meeting-transcription)。用于匿名 `Speaker 1/2` 与人工撤销识别的产品依据。
3. NIST. [Rich Transcription Evaluation](https://www.nist.gov/itl/iad/mltg/rich-transcription-evaluation)。用于将 STT、句边界、diarization 和 speaker-attributed STT 分开评估的依据。
4. W3C. [Understanding Success Criterion 4.1.3: Status Messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)；[ARIA22](https://www.w3.org/WAI/WCAG21/Techniques/aria/ARIA22)。用于 live region、状态播报和不抢焦点的无障碍依据。
5. Fireflies. [Summary schema](https://docs.fireflies.ai/schema/summary)；[How to Write a Meeting Recap](https://fireflies.ai/blog/how-to-write-a-meeting-recap)。用于会议纪要结构、行动项和证据定位的行业产品参考。
6. LM Studio. [REST API](https://lmstudio.ai/docs/developer/rest)；[Chat API](https://lmstudio.ai/docs/developer/rest/chat)。用于本地原生 `/api/v1/chat`、reasoning、输出上限和性能统计的接口依据。

### 项目内资料

- [SPK-E2E-1 端到端分人设计](/Users/hrygo/Documents/sona/docs/architecture/speaker-diarization-e2e-design.md:102)
- [会议助手实时转录体验优化方案](/Users/hrygo/Documents/sona/docs/solutions/会议助手实时转录体验优化方案.md:141)
- [ADR-007：有界会议纪要生成](/Users/hrygo/Documents/sona/docs/decisions/0007-bounded-meeting-summary-generation.md:40)
- [ADR-009：共享本地推理平台](/Users/hrygo/Documents/sona/docs/decisions/0009-shared-local-inference-platform.md:41)
- [本机 LM Studio 最佳实践](/Users/hrygo/Documents/本机优化配置/LM-Studio最佳实践.md:1)
