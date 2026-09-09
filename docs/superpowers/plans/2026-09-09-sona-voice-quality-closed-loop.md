# Sona 克隆音色质量闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Sona 中落地克隆音色的本地录音预检、SpeechRail 质量报告消费、克隆后验收展示，以及清空会话/记忆/重启管道后的干净会话诊断。

**Architecture:** Sona 只做浏览器侧即时预检、状态呈现、播放前诊断和会话闭环编排；SpeechRail 是输入与生成质量的权威方。新增字段和 API 均可选，旧版 SpeechRail 缺少质量能力时展示“未评估”，不把网络失败或能力缺失判为通过。

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Python 3.12, FastAPI, pytest.

**Spec:** `docs/architecture/voice-clone-quality-closed-loop.md`

## Global Constraints

- 不在 Sona 安装、下载或启动本地 ASR/TTS 模型。
- 不在 Sona 持久化原始参考音频、TTS PCM、完整参考文本或高精度音频特征。
- 不按单个 80 ms audio delta 独立归一化，不与 SpeechRail 叠加第二个快速 AGC。
- 保留现有 `voiceRecordingMicLease`、L1/L2 回声防线、PCM owner 仲裁和清空上下文语义。
- 保留工作区已有未提交改动，不执行 reset、checkout、清理或无关重构。
- 所有行为变更先写失败测试，再实现最小行为；旧服务端不返回 `quality` 时必须可用。

---

### Task 1: 扩展质量契约与服务层兼容解析

**Files:**
- Modify: `ui/src/contracts/voiceContract.ts`
- Modify: `ui/src/services/voiceService.ts`
- Test: `ui/src/services/voiceService.test.ts`

**Interfaces:**
- `VoiceQualityReport`：包含 `policy_version`、`status`、`run_id`、`tested_at`、可选 `reference`/`synthesis` 和 `failure_codes`。
- `voiceService.validateClone(formData): Promise<VoiceQualityReport>`：调用 `/v1/voices/clone/validate`。
- `voiceService.qualityRun(voiceId, input?): Promise<VoiceQualityReport>`：调用 `/v1/voices/{voiceId}/quality-runs`。
- `voiceService.clone()`：继续返回 `VoiceCatalogItem`，并透传可选 `quality`。

- [x] **Step 1: 写失败测试**

  覆盖：质量报告四种状态、嵌套 reference/synthesis 指标解析、未知状态降级为 `unevaluated`、旧 VoiceProfile 缺少 quality、validate 的 multipart 不设置 `Content-Type`、quality run 的 URL/JSON 请求形状。

- [x] **Step 2: 运行前端聚焦测试确认 RED**

  Run: `cd ui && npm test -- --run src/services/voiceService.test.ts`

  Expected: 新增导入或方法断言失败，现有服务测试保持可收集。

- [x] **Step 3: 添加类型和边界解析**

  `parseQualityReport(value)` 只接受有限字段和有限枚举；数值必须为 finite number，字符串必须非空。未知/缺失质量载荷返回 `undefined`，由 UI 映射为 `unevaluated`。

- [x] **Step 4: 添加可选 API 方法并保持错误 envelope**

  validate/qualityRun 复用现有 `requestJson`、`VoiceServiceError` 和结构化错误解析。validate 使用 `FormData` 原样提交；quality run 只提交 `probe_set`、`runs`、`include_audio:false`，不上传或返回原始音频。

- [x] **Step 5: 运行服务层测试确认 GREEN**

  Run: `cd ui && npm test -- --run src/services/voiceService.test.ts`

### Task 2: 添加浏览器侧录音预检纯函数

**Files:**
- Create: `ui/src/utils/voiceQuality.ts`
- Create: `ui/src/utils/voiceQuality.test.ts`

**Interfaces:**
- `VoiceQualityStatus = "unevaluated" | "pass" | "warn" | "reject"`。
- `VoiceSignalMetrics`：duration、speech active ratio、noise floor、estimated SNR、clipping ratio、leading/trailing silence。
- `evaluateVoiceSignal(metrics): LocalVoiceQualityResult`：返回状态、失败码和主修复建议。
- `summarizeAnalyserFrame(samples): { rms: number; peak: number }`：只处理当前内存帧，不持久化样本。

- [x] **Step 1: 写失败测试**

  使用内存数组覆盖纯静音、正常语音、低 SNR、过载削波、过短、首尾静音和边界值；断言返回状态、稳定失败码和修复建议，不写文件、不依赖浏览器音频设备。

- [x] **Step 2: 运行测试确认 RED**

  Run: `cd ui && npm test -- --run src/utils/voiceQuality.test.ts`

- [x] **Step 3: 实现有限指标计算与策略**

  采用文档中的 `voice_quality_v1` 初始阈值。浏览器侧不做 transcript match 和最终模型质量判断；缺失输入指标返回 `unevaluated`。`peak` 只用于预警，不对录音数据做 destructive normalize。

- [x] **Step 4: 运行测试确认 GREEN**

  Run: `cd ui && npm test -- --run src/utils/voiceQuality.test.ts`

### Task 3: 将质量状态接入 VoiceStudioModal

**Files:**
- Create: `ui/src/components/VoiceQualityCard.tsx`
- Create: `ui/src/components/VoiceQualityCard.test.tsx`
- Modify: `ui/src/components/VoiceStudioModal.tsx`
- Modify: `ui/src/components/VoiceStudioModal.css`
- Modify: `ui/src/components/VoiceStudioModal.test.tsx`

**Interfaces:**
- `VoiceQualityCardProps`：接收 `report`, `title`, `pending`, `onRetry`, `onRerecord`，以文字/图标/aria-live 展示状态。
- clone stage 扩展为 `local_check`、`server_checking`、`quality_running`、`ready_for_activation`、`needs_rerecord`，保留现有 `ready/recording/recorded/submitting/success` 兼容语义。

- [x] **Step 1: 写 UI RED 测试**

  覆盖 pass/warn/reject/unevaluated 文案、失败原因、重录按钮、质量运行中、旧 profile 无 quality 显示“未评估”，以及 clone 创建后不自动切换默认音色。

- [x] **Step 2: 运行 VoiceStudioModal 聚焦测试确认 RED**

  Run: `cd ui && npm test -- --run src/components/VoiceQualityCard.test.tsx src/components/VoiceStudioModal.test.tsx`

- [ ] **Step 3: 接入本地预检和服务端 validate**

  录音停止后先计算可用的浏览器指标；reject 阻止上传，warn 允许继续但标记风险。validate 能力不存在或返回网络/404 时，保留录音并展示“服务端质量检查不可用，尚未评估”，不得直接判 pass。

- [x] **Step 4: 接入 clone 后 quality run 和候选音色状态**

  clone 成功后调用 `qualityRun`；成功返回的 report 进入质量卡片。质量通过才显示“可激活”，warn/reject/未评估只允许“试听/保存候选/重新录音”。`onVoiceCreated` 仍只更新列表，不调用 `onSelectVoice`。

- [ ] **Step 5: 加入 A/B 试听和质量徽章**

  复用现有 `playAudioBlob`；固定短句分别试听当前音色和 clone，随机化展示顺序但保留可追踪的 `run_id`。音色库徽章使用文字和图标，不只依赖颜色。

- [x] **Step 6: 运行组件测试确认 GREEN**

  Run: `cd ui && npm test -- --run src/components/VoiceQualityCard.test.tsx src/components/VoiceStudioModal.test.tsx`

### Task 4: 接入清空/重启后的干净会话诊断

**Files:**
- Create: `ui/src/utils/voiceQualityDiagnostic.ts`
- Create: `ui/src/utils/voiceQualityDiagnostic.test.ts`
- Modify: `ui/src/components/AssistantPanel.tsx`
- Modify: `ui/src/components/AssistantPanel.test.ts`
- Modify: `ui/src/components/AssistantPanel.css`

**Interfaces:**
- `CleanSessionDiagnosticStep = "clear_context" | "clear_transcript" | "restart_pipeline" | "self_intro"`。
- `CleanSessionDiagnosticState`：记录步骤状态、开始/结束时间、失败 code、关联 `run_id`，不记录音频/完整文本。
- `classifyVoiceNoiseEvidence(input): VoiceNoiseClassification`：输出 `reference_noise | generated_output | playback_device | echo_risk | session_state | insufficient_evidence`。

- [x] **Step 1: 写诊断纯函数失败测试**

  用脱敏布尔证据覆盖“服务端 PCM 已异常”“播放前 PCM 异常”“仅物理输出异常”“TTS 期间检测到自回声”“仅旧上下文存在”“证据不足”。

- [x] **Step 2: 运行测试确认 RED**

  Run: `cd ui && npm test -- --run src/utils/voiceQualityDiagnostic.test.ts`

- [x] **Step 3: 实现诊断状态机**

  状态机通过现有 `sendCommand("clear_context")`、清屏动作、`sendCommand("restart")` 和固定文本发送串行推进；每一步等待对应 command ack 或超时，失败即停止并展示可重试步骤。固定文本只作为动作参数，不进入 telemetry。

- [x] **Step 4: 在 AssistantPanel 增加“克隆音色验收”入口**

  入口只在 assistant 模式且 command socket ready 时启用；执行中禁用冲突控制；使用 stepper、aria-live 和“重新执行/查看证据”动作。完成后展示分类结论和“清除诊断结果”，不持久化原始音频。

- [x] **Step 5: 运行组件与诊断测试确认 GREEN**

  Run: `cd ui && npm test -- --run src/utils/voiceQualityDiagnostic.test.ts src/components/AssistantPanel.test.ts`

### Task 5: 全量验证与文档/issue 回填

**Files:**
- Modify: `docs/architecture/voice-clone-quality-closed-loop.md`
- Modify: `docs/superpowers/plans/2026-09-09-sona-voice-quality-closed-loop.md`

- [x] **Step 1: 运行前端全量测试和构建**

  Run: `cd ui && npm test -- --run`；`cd ui && npm run build`。

- [x] **Step 2: 运行后端回归与静态检查**

  Run: `SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/`；`uv run mypy src/`；`uv run ruff check src/ tests/`。

- [x] **Step 3: 检查范围、空白和现有改动**

  Run: `git diff --check`；`git status --short`；确认既有 `AssistantPanel`、`voiceRecordingMicLease` 变更未被覆盖，且没有生成音频或构建产物进入 diff。

- [x] **Step 4: 更新文档与 issue 证据**

  回填实际实现范围、兼容的 SpeechRail 能力、测试结果和未完成的真实模型/扬声器验收；只有真实闭环通过后才将文档状态从 `under_review` 改为 `implemented`。

> 当前状态：Sona 前端闭环与文档已落地；SpeechRail 权威 validate、跨仓库音频证据和真实设备验收仍待 issue #36，故本计划和架构文档保持 `under_review`。
