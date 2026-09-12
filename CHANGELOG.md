# Changelog

## [Unreleased]

### Added

- 声音工坊新增六类可编辑示例卡片、描述与朗读文本分栏、模板覆盖确认和折叠 seed 设置。
- 代理并接入 SpeechRail `/v1/voices/designs`，生成参考注册到 Base；旧服务明确报错而不回退旧流程。
- 统一录音与生成音色的结果页：参考验收、18 段输出复核、实际音色试听与手动确认使用。
- 保存响应不确定时保留同一 ID 和正文，提供档案库核对与原 ID 重试；保留路径安全的生成来源信息。
- 增加声音工坊 UI/Python CI，后端数据库测试使用独立 PostgreSQL 服务与临时 schema。

### Changed

- 录音提交保留原始容器、字节和电平，移除该链路的浏览器整段 RMS 归一，由 SpeechRail 统一处理参考。
- 提词文本在录音开始时固定，完成后可按实际朗读修正；本地安全检查使用实际解码时长。
- 质量报告按 reference/synthesis 分开解释，旧版不完整 pass 不显示为输出通过，注册后不自动启用。
- 模态框增加窄窗口适配、焦点保护、忙碌操作互斥；试听和生成请求在卸载时取消，播放 URL 及时释放。

### Fixed

- 启用音色必须等待控制确认且返回目标音色；主界面克隆音色下拉选择先进入结果检查，不能绕过验收。
- 每次试听、复测或取消先撤销旧证据，失败后不沿用“已试听”；生成成功可返回编辑，保留已注册资产。
- 删除只确认一次，异步失败保留弹窗；删除当前音色先确认切换默认音色，失败时不发起删除。
- 注册结果不确定时保留固定 ID / 请求；录音克隆代理校验并转发同一 Idempotency-Key，重试不换录音。
- 等待助手静音确认后才启动声音操作；等待中关闭会先等静音结束再恢复，恢复失败可以在重连后重试。
- 补齐取消录音/请求、空录音与设备中断、实际 30 秒停止、晚到提词不污染已录文本、未提交草稿关闭确认。
- 模态内隔离全局快捷键，嵌套删除 Escape 不关闭整个工坊；刷新失败保留列表，能力变化不丢失描述草稿。
- 增加可重跑的浏览器分支矩阵，串联实际 React、Sona HTTP 代理和 WebSocket 控制；外部模型与输入音源为夹具。

### Compatibility and acceptance

- 新注册链路需要 SpeechRail #46（`0c403ab` 或后续兼容版本）及 Quality 档；首次仅支持中文。
- 不自动迁移旧 instruction/clone 音色；不宣称已接入 AudioWorklet 无损采集、神经 VAD 或降噪模型。
- 软件回归与夹具页面检查不替代真实麦克风、Safari/WebKit 和 Apple Silicon 声纹/噪声/听感验收。

## [1.6.0] - 2026-09-09

### Added

- 接入 SpeechRail `2.1.0` 克隆音色参考音频质量验收与质量复测接口。
- 增加声音工坊质量卡片、克隆前预检、失败原因展示与克隆验收诊断流程。

### Changed

- Sona 代理转发 `clone/validate` 与 `quality-runs`，统一复用 SpeechRail 认证和错误响应。
- 自我介绍验收流程在重启管道后等待运行时稳定，再发送固定测试文本，降低启动竞态误判。

### Verification

- Python：`1143 passed`，覆盖率 `83.23%`；`mypy` strict 与 `ruff` 全通过。
- Frontend：`381 passed`；TypeScript/Vite production build 通过。
- SpeechRail `2.1.0` 真实联调：参考音频校验、clone、3 次质量复测与 TTS 输出均通过；物理扬声器回录噪声仍需真实用户录音 A/B 验收。

## [1.5.1] - 2026-09-09

### Added

- 增加 assistant / subtitles / meeting 三种语音端点策略的清晰边界：assistant 使用本地 VAD，字幕与会议分别使用 SpeechRail `400ms` / `900ms` server VAD。
- 增加会议/字幕讲话人归属重入隔离、EOF speaker patch 保护与 SpeechRail v2.0.3 联合验收记录。

### Changed

- 音色工坊与 SpeechRail Realtime TTS 支持命名空间语速参数透传；克隆音色稳定性、参考音频校验和源码构建部署边界写入正式文档。
- 文档中心、架构方案、运行手册与验收记录同步至当前 SpeechRail 源码构建发布流程。

### Fixed

- 修复实时字幕重入后继承旧 diarization terminal/degraded 状态的问题。
- 修复字幕 EOF 最终窗口以 `unknown` 回写并覆盖已确认 speaker patch 的问题。
- 修复从语音助手或普通字幕进入会议工作区时未先停止当前 PCM owner 的问题。

### Verification

- Python：`1138 passed`，覆盖率 `83.36%`；`mypy` strict 与 `ruff` 全通过。
- Frontend：`361 passed`；TypeScript/Vite production build 通过。
- SpeechRail `2.0.3` health、字幕重入、会议 EOF 水位屏障与 clone 稳定性专项复验已记录；DER/cpCER、长时资源和克隆主观音质仍不在本版本门禁内。

## [1.5.0] - 2026-09-09

### Added

- 完成 SpeechRail v2 OpenAI Realtime 接入，会议与实时字幕支持独立启用的 namespaced diarization opt-in。
- 增加持续分人事件解析、说话人原位修订、EOF `done` 水位屏障、最终 SRT 归档及人工更正优先保护。
- 增加 SpeechRail v2.0.2 联合验收记录，覆盖协议、事务、前端、真实会议与字幕 smoke。

### Changed

- 统一升级产品版本号至 `1.5.0`（Python 包、前端控制台、后端 FastAPI 与 `uv.lock`）。
- 更新中英文 README，明确 SpeechRail v2 运行前置、独立分人开关、健康检查与验收边界。

### Fixed

- 修复旧版分人 overlay、字典事件解包、已确认正文丢失及 legacy EOF 屏障悬挂等联调缺陷。
- 完善实时音频采集、播放稳定性、静音环境转录和声音工坊链路的错误处理与用户反馈。

### Verification

- Python 全量测试、`mypy` strict、`ruff` 与前端测试/生产构建均通过；真实 SpeechRail v2.0.2 健康检查、会议及字幕 smoke 已完成。
- SpeechRail 的 DER/cpCER、长文件/长时资源行为及 RTTM/UEM 质量不属于本版本验收范围。

## [1.4.0] - 2026-08-28

### Added

- 增加会中 Inner OS 私密伴侣：支持证据上下文快照、严格答案契约、流式查询、取消指令与会后即焚的临时背景。
- 增加用户显式保存的 Inner OS 问答持久化、历史归档与 REST API；未保存的临时背景和模型推理过程不落库。
- 增加共享本地推理调度器，统一协调语音交互、会议纪要与 Inner OS 的 LM Studio 工作负载。
- 增加 Inner OS P0 价值评测数据集、盲评规则、指标聚合与可复现报告。
- 增加 Inner OS 会中侧面板、快捷 Prompt、答案卡片、未保存托盘及历史视图，并扩展 OpenAPI、AsyncAPI 与 JSON Schema 契约。

### Changed

- 统一升级项目版本号至 `1.4.0`（后端 FastAPI、TTS 桥与前端控制台）。
- 统一复用 LM Studio 原生传输层，并保持 `/api/v1/chat` 的响应链、推理开关与输出边界约束。
- 重构会议工作台、侧边栏、顶部操作栏与折叠交互，改善响应式布局、快捷键可达性和全站无障碍对比度。

### Fixed

- 修复 Inner OS 在空转录、路由注册时序和异常契约场景下查询或响应卡片卡住的问题。
- 修复会议中重命名说话人后再次发言时名称被恢复为默认值的问题。
- 修复会议详情双栏挤压、工具栏溢出、折叠导轨 Tooltip 偏移及 `Cmd+K` 焦点冲突。

### Verification

- Python：`1327 passed, 1 warning`，分支覆盖率 `82.89%`（$\ge 80.0\%$）。
- Frontend：`215 passed`（34 test files），TypeScript/Vite 生产构建成功。
- `mypy`（strict，108 源文件全绿）、`ruff`（全通过）。

## [1.3.0] - 2026-08-27

### Added

- 增加 CAM++ 192 维声纹嵌入特征提取（阿里 3D-Speaker ONNX ~27MB，CPU 单段仅 ~12ms，纯内存处理，绝不落盘原始音频）。
- 增加会议实时在线声纹质心池跟踪（`CentroidPool`），相似度 $\ge 0.75$ 时自动映射归并。
- 增加会后全局 AHC 层次凝聚聚类二次修正（`AHCClusterer`），余弦距离 $\le 0.35$ 且受 `max_speakers` 强约束。
- 增加 1:N 已知说话人声纹注册与自动命名匹配器（`VoiceprintProfileMatcher`）。
- 增加 Sortformer 迟滞双门限判定机制（onset=0.50, offset=0.35, silence=0.25）与防静音吸气通道抖动。
- 增加动态参会人数容量先验 `max_speakers`（1~4）全链路贯通（前端 UI 下拉选择器 ➔ 控制协议 ➔ 后端协调器 ➔ WLK Sortformer）。
- 增加 `PostgresMeetingRepository.apply_speaker_remapping` 单事务原子更新段落与合并说话人记录。
- 增加架构决策记录 [ADR-008](docs/decisions/0008-speaker-diarization-and-voiceprint-clustering.md)。

### Changed

- 统一升级项目版本号至 `1.3.0`（包含后端 FastAPI、前端控制台与契约层）。
- 完善 `DiarizationSmoother` 时序平滑器，支持跨 Epoch 相同声道自然平滑（间隙扩至 1000ms）。

### Fixed

- 彻底根除会议模式下“一人多号 / 说话人过度分裂 / 静音漂移”缺陷。
- 修复短片段杂音与多段连续短闪烁翻转（$A-B-A$ 及 $A-B-B-A$ 序列平滑纠偏）。
- 修复跨 Epoch 重连导致说话人自定义名称丢失的问题，支持跨 Epoch 继承与原子重命名同步。

### Verification

- Python：`1269 passed, 1 warning`，分支覆盖率 `83.17%`（$\ge 80.0\%$）。
- Frontend：`166 passed`（20 test files），TypeScript/Vite 生产构建成功。
- `mypy`（strict，91 源文件全绿）、`ruff`（全通过）。

## [1.2.0] - 2026-08-26

### Added

- 增加 Qwen3-TTS 四重纵深防御：输入强制终结标点归一化、自回归重复惩罚参数强化（`repetition_penalty=1.25`）、动态字符级 Token 熔断上限与音频 `nan_to_num` 极值清洗和 5ms 线性淡出。
- 增加全链路日志轮转、敏感凭据自动脱敏（`SanitizingFilter`）、排障指引与交互时延度量（TTFT / TTFA）。
- 增加运行时两阶段工作负载仲裁机制，支持 `assistant` / `meeting` / `idle` 模式安全互斥与 PCM 重连快照恢复。
- 增加语音助手默认聆听态（👂）基准流转与状态栏居中防抖动效。

### Changed

- 统一升级项目版本号至 `1.2.0`（包含后端 FastAPI、前端控制台与契约层）。
- 优化 Sona 控制台三大模块响应式布局与设计 Token。

### Fixed

- 彻底根治短词、叠词（如“好的好的”、“嗯嗯，那咱们随时聊。”）在 Qwen3-TTS 下引发的声学死循环与长蜂鸣问题。
- 修复语音助手前端在多轮交互中对重复输入（如连续回复“没有。”）的误去重丢泡缺陷。
- 修复长会议纪要触顶未闭合 JSON 的输出边界收敛与异常处理。

### Verification

- Python：`1233 passed, 1 warning`，分支覆盖率 `83.11%`（$\ge 80.0\%$）。
- Frontend：`166 passed`（20 test files），TypeScript/Vite 生产构建成功。
- `mypy`（89 源文件全绿）、`ruff`（全通过）。

## [1.1.0] - 2026-08-25

### Added

- 增加 Qwen3-ASR、SenseVoice 和 Fun-ASR 的统一适配与本地运行入口。
- 增加 ASR 公共代理语料冻结、metadata-only 预检、阶段执行器、证据链和序贯决策工具。
- 增加可复现的 ASR benchmark 报告、公共代理 v1/v2 结果与开发语料生成脚本。
- 增加 Sona 助手、会议、字幕和状态栏相关的控制台交互能力。

### Changed

- 保持当前产品后端分工：会议/字幕使用 `Qwen3-ASR-1.7B`，语音交互使用 `SenseVoiceSmall`。
- ASR 评测结果继续标记为 `Experimental / No decision`；公共代理证据不直接触发生产模型切换。

### Fixed

- 加固混合语言自动检测、MPS 超时收敛、资源锁释放和聚类 bootstrap 评估边界。
- 补充 benchmark、ASR 适配层、会议控制台和字幕代理的回归测试。

### Verification

- Python：`1011 passed, 10 skipped`，覆盖率 `80.46%`。
- Frontend：`64 tests` passed，TypeScript/Vite production build passed。
- `mypy`、`ruff`、`uv lock --check` 和 Python package build passed。
