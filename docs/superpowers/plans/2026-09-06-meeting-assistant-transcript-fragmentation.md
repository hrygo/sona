# Meeting Assistant Transcript Fragmentation Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox "- [ ]" syntax for tracking.

**Goal:** 修复会议助手把连续自然发言切成大量独立“嗯/呃”小卡片的问题，同时保留确认转录、时间戳、说话人和原始片段的可追溯性。

**Architecture:** 将问题拆成两层：会议专用 endpoint/VAD 配置负责减少过度切分；阅读视图只对同一说话人、同一转录 epoch、时间相近的独立语气词做展示层折叠。PostgreSQL 中的 confirmed 正文与原始时间线不修改，时序视图继续提供逐片段证据。

**Tech Stack:** Python 3.12、uv、pytest、SpeechRail OpenAI Realtime WebSocket、PostgreSQL、React 19、TypeScript、Vitest、Vite。

**Spec:** docs/architecture/实时语音交互与字幕-方案与最佳实践.md、docs/architecture/speaker-diarization-e2e-design.md、docs/manuals/会议助手后端运行与前后端联调.md，以及用户提供的会议助手截图与问题描述。

## Global Constraints

- 本计划只描述执行路径；当前请求不执行业务代码修改、不提交、不重启服务。
- 当前工作树已经存在上一轮最小修复及其他用户未提交改动。执行时先记录 git status --short，只认领本计划列出的文件，不使用 git reset --hard、git checkout --、批量清理或覆盖其他改动。
- 第一阶段只修改 sona 仓库；不修改 /Users/hrygo/Documents/SpeechRail，不安装、下载或启动本地 ASR/TTS/diarization 模型。
- confirmed 转录正文、开始/结束时间和原始片段 ID 不可变；阅读视图折叠只能生成派生展示块，不能删除、合并或回写数据库记录。
- 分人仍通过既有 apply_speaker_patches 路径处理；任何展示折叠不得跨 speaker_key 或 source_epoch。
- 会议 VAD 配置必须与字幕/语音助手配置隔离；不改变非会议模式的默认断句窗口。
- 不新增数据库迁移、不保存音频、不把原始转录文本写入新的日志或诊断文件；需要观测时只记录聚合计数和耗时。
- 测试数据库必须使用 SONA_TEST_DATABASE_URL=postgresql:///knowledge 的独立临时 schema，并由测试清理；禁止指向生产数据。
- 不新增依赖。若后续需要语音增强或 semantic VAD，必须另立方案并先用实测数据证明必要性。
- 只有在没有活动会议时才允许进行运行时重启；活动会议期间只做只读状态检查。

---

## Feasibility Audit（只读核验，2026-09-06）

| 方案环节 | 当前证据 | 结论 |
|---|---|---|
| 会议专用 VAD 注入 | SpeechRailStreamingTranscriber.connect() 已统一经过 _turn_detection_config()；SpeechRailRealtimeClient 已接受 turn_detection 并放入 session.update | 可执行，且不需要改 Realtime 协议 |
| 会议/字幕配置隔离 | transcriber.py 按 context.purpose == "meeting" 选择 profile，其他模式仍使用 DEFAULT_SERVER_VAD* | 可执行，回归边界明确 |
| UI 折叠不破坏事实源 | deriveReadingBlocks() 是纯函数；TranscriptViewBlock.segment_ids 可保留原始片段；MeetingSession._on_window() 仍通过 persistence append confirmed item | 可执行，不需要数据库迁移 |
| 阅读/时序双视图 | MeetingRecordingView 已同时保留 timeline 与 reading 分支，可默认进入阅读视图再切回原始时序 | 可执行，回退路径已存在 |
| 自动化质量门禁 | 已实测：后端 1097 passed、覆盖率 83.24%；前端 294 passed；mypy 108 个源文件无错误；ruff、前端生产构建、git diff --check 均通过 | 可执行，当前基线没有已知门禁阻塞 |
| 运行时依赖 | 当前 sona-ui、SpeechRail ASR/TTS、LM Studio 均健康；当前服务未为本计划重启 | 可执行，但真实 UI 冒烟应安排在无活动会议窗口 |
| 真实声学效果 | 目前只有截图和事件/单测证据，没有脱敏音频回放集，尚未证明不同噪声、语速下的误切率/漏切率 | 最小修复可先执行；声学阈值的长期优化仍需独立采样和 A/B |

## Research-derived Decisions

- endpointing 是延迟与误切分的权衡：Realtime 文档明确指出更短的 silence_duration_ms 响应更快，但更容易在短停顿时提前结束；semantic VAD 可能减少误切分，但要单独评估延迟。[OpenAI Realtime API](https://platform.openai.com/docs/api-reference/realtime?lang=javascript)
- 不直接删除所有“嗯/呃”：backchannel 可能承载确认或互动意义，因此只在阅读视图中折叠，并保留可展开的原始片段。[Backchannel research](https://onlinelibrary.wiley.com/doi/full/10.4218/etrij.2023-0358)
- partial 与 confirmed 必须分开对待；增量 ASR 的中间结果不稳定，任何去重/折叠都不能改变 confirmed 事实。[AWS partial results](https://docs.aws.amazon.com/transcribe/latest/dg/streaming-partial-results.html)
- 若后续需要更严格的声学门控，应先评估噪声、回声和漏检数据；WebRTC VAD 的 aggressiveness 本身就是漏检/误检权衡，不能凭经验盲调。[WebRTC VAD API](https://webrtc.googlesource.com/src/+/main/common_audio/vad/include/webrtc_vad.h)

## File Map

- src/sona/speechrail/transport.py：保留通用 DEFAULT_SERVER_VAD*，增加会议专用 VAD profile。
- src/sona/speechrail/transcriber.py：按 meeting purpose 选择会议 profile，其他模式保持原 profile。
- tests/asr/test_speechrail_realtime.py：验证会议 legacy/extensions 两条配置路径和非会议回归。
- ui/src/contracts/meetingContract.ts：声明语气词折叠的时间窗口选项。
- ui/src/components/meeting/transcriptViewModel.ts：实现纯展示层 block 派生、边界判断和原始 ID 保留。
- ui/src/components/meeting/MeetingRecordingView.tsx：默认阅读视图，保留时序视图切换。
- ui/src/components/meeting/transcriptViewModel.test.ts：验证折叠、说话人/epoch/间隔/时长边界。
- ui/src/components/meeting/MeetingComponents.test.tsx：验证默认阅读视图和切换控件。
- src/sona/meeting/session.py：执行期间只读核对 _on_window() 的 confirmed persistence，不改其事实源职责。

## Execution Plan

### Task 1: 建立安全基线与可复现回归

Files: 只读检查 git status、src/sona/meeting/session.py；测试文件为 tests/asr/test_speechrail_realtime.py、ui/src/components/meeting/transcriptViewModel.test.ts、ui/src/components/meeting/MeetingComponents.test.tsx。

- [ ] 记录 git status --short，确认其他用户改动不被纳入本任务；不要执行 reset、checkout 或 stash 覆盖操作。
- [ ] 在干净实现分支中先加入三个回归断言：会议 VAD 使用 900ms；重复独立语气词折叠为一个展示块且保留全部 segment_ids；默认渲染阅读卡片。
- [ ] 先运行后端单测和两个前端定向测试，历史基线应分别在对应新断言处失败；当前工作树若已经含有这些断言，则直接记录其绿色结果，不重复添加测试。

~~~bash
uv run pytest tests/asr/test_speechrail_realtime.py -q --no-cov
cd ui && npm test -- --run src/components/meeting/transcriptViewModel.test.ts src/components/meeting/MeetingComponents.test.tsx
~~~

Expected: 干净旧基线出现“400 != 900”、重复语气词仍为多个 block、默认阅读卡片不存在中的相应失败；当前已应用修复的工作树应全部通过。

### Task 2: 实施会议专用 endpoint/VAD profile

Files: src/sona/speechrail/transport.py 的 VAD 常量；src/sona/speechrail/transcriber.py 的 _turn_detection_config() 与 connect()；tests/asr/test_speechrail_realtime.py。

- [ ] 保持通用配置不变：DEFAULT_SERVER_VAD.silence_duration_ms == 400，DEFAULT_SERVER_VAD_EXTENSIONS.silence_duration_ms == 600。
- [ ] 增加会议 profile，精确使用：

~~~python
MEETING_SERVER_VAD = {
    **DEFAULT_SERVER_VAD,
    "silence_duration_ms": 900,
}
MEETING_SERVER_VAD_EXTENSIONS = {
    **DEFAULT_SERVER_VAD_EXTENSIONS,
    "silence_duration_ms": 1_000,
}
~~~

- [ ] 在 _turn_detection_config() 中只对 context.purpose == "meeting" 选择上述 profile；extensions 请求时选择 MEETING_SERVER_VAD_EXTENSIONS，否则选择 MEETING_SERVER_VAD。
- [ ] 补齐四类断言：meeting/legacy、meeting/extensions、subtitle/legacy、subtitle/extensions；断言最终 session.update.turn_detection payload，而不是只断言本地常量。

Acceptance: 会议短停顿不再按通用 400/600ms 结束；字幕和语音助手仍为 400/600ms；没有协议字段名、音频格式或模型生命周期变化。

### Task 3: 在阅读视图折叠重复独立语气词

Files: ui/src/contracts/meetingContract.ts、ui/src/components/meeting/transcriptViewModel.ts、ui/src/components/meeting/transcriptViewModel.test.ts。

- [ ] 为 ReadingBlockOptions 增加 maxFillerGapMs 默认 5000 和 maxFillerDurationMs 默认 30000，与普通正文的 maxGapMs=1200、maxDurationMs=15000 分开。
- [ ] 只将独立的 嗯、呃、啊、唔、额、诶（允许末尾中英文标点）识别为 filler；含有其他实质文字的片段不得进入 filler 折叠。
- [ ] 仅当以下条件全部满足时折叠：filler token 相同、speaker_key 相同、source_epoch 相同、相邻 gap <= 5000ms、折叠后总时长 <= 30000ms。
- [ ] 展示文本使用 嗯 × N 形式，但 segment_ids 必须追加每一条原始片段；点击/展开仍通过既有 getSegmentsForBlock() 找回原始片段。
- [ ] filler block 不与正常正文合并；不跨说话人、不跨 epoch；超出 gap 或总时长后从新 block 开始。

Required test matrix:

- [ ] 同说话人、同 epoch、间隔 2000ms/5000ms：折叠。
- [ ] 间隔 5001ms、总时长 30001ms：不折叠。
- [ ] 说话人不同、epoch 不同、文本为“嗯嗯/嗯我同意”、filler token 不同：不折叠。
- [ ] 折叠结果包含全部原始 ID，starred 任一片段后 block 仍为 starred。
- [ ] 普通正文的原有 gap、时长、字数和中英文空格规则不变。

### Task 4: 默认阅读呈现并保留原始证据入口

Files: ui/src/components/meeting/MeetingRecordingView.tsx、ui/src/components/meeting/MeetingComponents.test.tsx。

- [ ] 将初始 viewMode 设为 "reading"，但保留 "timeline" 控件、aria-pressed 状态和现有逐片段渲染分支。
- [ ] 默认状态断言阅读卡片存在；点击“时序视图”后断言原始 segment 卡片存在；切回阅读视图后断言聚合 block 存在。
- [ ] 核对 speaker rename、star、expand/collapse、auto-scroll 和结束会议按钮不依赖旧的默认视图值。
- [ ] 不改 API、WebSocket、数据库 schema 或 confirmed 文本 payload。

Acceptance: 首屏不再被大量短 filler 卡片撑满；用户仍可一键查看未经折叠的时序证据。

### Task 5: 完成质量门禁与受控运行时冒烟

Files: 无新增业务文件；只读检查服务状态和测试输出。

- [ ] 在没有活动会议时执行状态检查；活动会议中禁止重启：

~~~bash
scripts/sona-ctl.sh status
~~~

- [ ] 运行后端全量门禁：

~~~bash
SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/
uv run mypy src/
uv run ruff check src/ tests/
~~~

- [ ] 运行前端全量门禁：

~~~bash
cd ui && npm test -- --run
cd ui && npm run build
~~~

- [ ] 运行 git diff --check，确认无空白错误；确认 diff 只包含本计划范围或已明确存在的用户改动。
- [ ] 获得执行授权且确认没有活动会议后，创建一场短测试会议：连续说 3 次带 1–2 秒停顿的“嗯”，再说一整句正常内容；在阅读视图验证 嗯 × 3 与完整句子，在时序视图验证原始片段仍逐条存在；结束会议后验证正常封存，无新增 30 秒等待。

Current baseline expected: 后端 1097 passed、覆盖率约 83.24%；前端 294 passed；mypy、ruff、构建和 diff check 退出码为 0。测试数量变化时以全量通过和覆盖率门禁为准，不以固定数量替代验收。

### Task 6: 回滚与发布边界

- [ ] 将后端 VAD 和前端阅读聚合拆成两个独立提交，提交信息分别使用 fix(meeting): 调整会议专用断句窗口 与 fix(meeting-ui): 折叠重复语气词展示。
- [ ] 若实测出现漏识别，优先回滚后端那一个精确提交；若只是展示偏好不符，保留后端修复并让用户切换时序视图，不删除数据库事实。
- [ ] 回滚只能使用已确认提交的 git revert <commit>；不得用宽范围 reset/checkout 覆盖工作树。
- [ ] 发布前记录测试结果、SpeechRail/LM Studio 健康状态和人工冒烟结论；不把原始会议音频或隐私文本写入报告。

## Deferred Evidence Gate

本次最小修复不继续盲调阈值。若真实会议中仍出现高频误切或出现明显漏切，下一份独立方案必须先补充不含原始音频的事件级回放/聚合指标：空 final 数量、confirmed 片段时长、相邻 gap、endpoint 等待时延、说话人/epoch 边界。只有拿到分噪声与语速的对照数据后，才评估 semantic VAD、WebRTC APM/NS 或 SpeechRail 上游 profile；不得在没有证据时增加本地模型或新依赖。

## Definition of Done

- [ ] 会议专用 VAD profile 只影响 meeting purpose，字幕和语音助手 profile 无回归。
- [ ] 阅读视图对安全范围内的重复 filler 做展示聚合，原始 segment ID、speaker、epoch、star 和时序视图完整保留。
- [ ] confirmed persistence、分人修订、EOF 屏障和数据库 schema 未改变。
- [ ] 后端/前端全量质量门禁通过，且完成无活动会议前提下的运行时冒烟。
- [ ] 形成可回滚的独立提交和验收记录；本计划执行前不进行任何业务实施。
