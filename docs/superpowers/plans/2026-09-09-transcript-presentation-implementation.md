# Transcript Presentation and Cross-Mode UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 issue #14：以不可变 `transcript_items` 正文事实、可修订 `transcript_attribution_spans` 归属事实和统一 `TranscriptPresentationProjector` 消除逐字展示，并让会议、字幕、导出、summary、Inner OS 和前端共享一致的可读转录语义。

**Architecture:** SpeechRail completed item 进入 Sona 的正文表，一条 completed item 保持一条完整正文；独立 attribution span 表保存可修订 speaker/timing 元数据。纯内存 projector 从正文与 spans 生成 `DisplayBlock`，会议实时展示、字幕、导出和 AI 输入只消费 projector / `ModelTranscript`，旧 `transcript_segments` 在迁移窗口内只读兼容。

**Tech Stack:** Python 3.12, `uv`, Pydantic v2, PostgreSQL/psycopg, FastAPI/WebSocket, React 19, TypeScript, Vitest, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-09-09-transcript-presentation-and-ux-design.md`; GitHub issue #14.

## Global Constraints

- Python 版本严格为 `3.12`，依赖管理使用 `uv` + PEP 621 + `hatchling`。
- completed 正文、时间范围、source identity 和顺序确认后不可修改；speaker patch 只能修改 attribution metadata。
- `manually_corrected = true` 的 attribution span 永远跳过自动 patch、平滑和 EOF 冲刷。
- PostgreSQL 不保存音频；字幕不依赖 PostgreSQL；语音助手热路径不依赖会议表、projector 或 EOF barrier。
- LM Studio 继续使用原生 `/api/v1/chat`，模型固定为 `local/kat-coder-2.5`，不得回退 OpenAI 兼容端点。
- 不删除历史 `transcript_segments` 数据；迁移必须可回滚，并保留一个发布窗口的旧表只读兼容能力。
- 普通 UI 不展示逐字/原子/时序调试入口；source IDs、spans 和 revision 只在显式诊断请求中返回。
- 现有用户改动位于主 worktree 的 `ui/src/components/VoiceQualityCard*`、`VoiceStudioModal*` 和 `ui/src/contracts/voiceContract.ts`，本分支不得触碰。
- 每个任务必须执行“RED → GREEN → 任务级 AC 验收 → 原子 commit”；未通过 AC 不得进入下一任务。

## 任务地图与文件边界

| 任务 | 交付物 | 主要文件 | 依赖 |
|---|---|---|---|
| T0 | 基线、fixture、AC 矩阵和迁移开关约束 | `tests/`, `contracts/`, `docs/` | 无 |
| T1 | 正文/attribution 领域模型、speaker 状态、projector、ModelTranscript | `src/sona/meeting/transcript_*`, `src/sona/meeting/models.py`, `tests/` | T0 |
| T2 | 两张表 migration、双读、幂等写入、patch 审计和旧表只读兼容 | `src/sona/meeting/migrations/`, `repository.py`, `ports.py`, `tests/` | T1 |
| T3 | 会议 session、实时事件、API、WS 和兼容 `segments` 接入 projector | `session.py`, `events.py`, `api.py`, `websocket_routes.py`, `contracts/` | T2 |
| T4 | SRT/Markdown/TXT/JSON 导出和 summary / Inner OS 的 `ModelTranscript` 输入 | `meeting/summary/`, `inner_os/`, `minutes_rendering.py`, `api.py`, `tests/` | T1–T3 |
| T5 | 字幕内存 projector、partial 原位更新、重连与无数据库运行 | `subtitles/`, `tests/subtitles/` | T1 |
| T6 | 前端阅读视图、speaker 状态、自动跟随、兼容 payload 和可访问性 | `ui/src/components/meeting/`, `ui/src/contracts/`, `ui/src/stores/`, `ui/src/services/`, `ui/src/utils/exportUtils.ts`, `ui/src/utils/exportUtils.test.ts`, `ui/src/**/*.test.*` | T3–T5 |
| T7 | SpeechRail source identity / sequence / event version / diagnostics 契约 | `/Users/hrygo/Documents/SpeechRail` 对应独立 worktree或 PR | T1–T3 |
| T8 | 历史数据对账、停止新写旧表、回滚窗口和文档 | `scripts/`, `docs/operations/`, `tests/` | T2–T7 |
| T9 | 全量门禁、五轴 code review、PR | Git/VCS/CI | T0–T8 |

## 100% AC 覆盖矩阵

状态约定：`pending` → `in_progress` → `pass`；任何 `fail` 必须在同一任务内修复并重新验收。证据必须记录实际命令、退出码、测试计数或可复核 diff；“代码已修改”不算证据。

| AC ID | 来源 | 可观察要求 | 覆盖任务 | 验证/证据 | 状态 |
|---|---|---|---|---|---|
| AC-DATA-01 | issue/spec 4.1 | 一个 completed item 只写入一条完整 `transcript_items` 正文，字符级 unit 不产生正文行 | T1,T2 | repository integration + row count + text equality | pass |
| AC-DATA-02 | spec 4.1 | `text`、start/end、source identity、sequence 在确认后不可变 | T1,T2 | mutation rejection test + SQL constraint/repository test | pass |
| AC-DATA-03 | spec 4.1 | `(meeting_id, source_session_id, source_segment_uid)` 幂等；同 event 冲突拒绝 | T2 | duplicate/replay/conflict tests | pass |
| AC-DATA-04 | spec 4.2 | span 不重复保存正文，只保存 text/audio range 与 speaker metadata | T1,T2 | schema inspection + persisted row test | pass |
| AC-DATA-05 | spec 4.2 | span 覆盖合法、不重叠；非法范围拒绝 | T1,T2 | pure validation + DB integration tests | pass |
| AC-DATA-06 | spec 4.2 | speaker patch 只变 attribution metadata，不变正文、时间、source identity | T2,T3 | before/after fact snapshot test | pass (T2 repository) |
| AC-DATA-07 | issue/spec 3.2 | `manually_corrected=true` 经自动 patch、平滑、EOF、重连和重新投影后保持 | T2,T3,T8 | end-to-end precedence test | pass (T2 dual write) |
| AC-DATA-08 | spec 4.2 | attribution revision/history 可审计且 revision 单调递增 | T2 | revision history integration test | pass |
| AC-PROJ-01 | spec 5.1 | projector 输出稳定 `block_id`、source IDs、text、timing、speaker metadata、partial 标记 | T1 | projector unit tests | pass |
| AC-PROJ-02 | spec 5.2 | 默认同 source session/epoch/item 聚合，speaker 不兼容不合并 | T1 | boundary table tests | pass |
| AC-PROJ-03 | spec 5.2 | gap `>1200ms` 断开；最长 `15000ms` 或 `180` 字符先到断开 | T1 | exact boundary tests | pass |
| AC-PROJ-04 | spec 5.2 | 强结束标点优先断开；逗号/顿号不强制断开 | T1 | punctuation tests | pass |
| AC-PROJ-05 | spec 5.2 | partial 只有一个底部活动 block，delta 原位更新 | T1,T5,T6 | projector/subtitle/UI tests | pass (T1 projector) |
| AC-PROJ-06 | spec 5.2 | unknown 不跨 item 猜测合并；patch 只刷新 speaker metadata | T1,T2 | projector and patch tests | pass (T1 boundary) |
| AC-PROJ-07 | spec 5.3 | DisplayBlock 按 source order 拼接后 100% 等于 confirmed 正文 | T1,T4,T5 | property/table text conservation tests | pass (T1 examples) |
| AC-PROJ-08 | spec 5.3 | timing unavailable 不显示伪精确时间，不提供错误 click-to-source 定位 | T1,T4,T6 | serializer/UI accessibility tests | pass (T1 model) |
| AC-STATUS-01 | issue/spec 7 | 明确支持 `identified`、`anonymous`、`pending`、`off`、`degraded` | T1,T3,T6 | Python/TS contract + rendering tests | pass (T1 Python) |
| AC-STATUS-02 | spec 7 | anonymous 显示稳定“说话人 N”，pending 显示“正在确认” | T1,T6 | label mapping + component tests | pass (T1 ModelTranscript labels) |
| AC-STATUS-03 | spec 7 | off 显示“分人未启用”，degraded 显示“分人不可用” | T1,T3,T6 | degraded/off event tests + UI tests | pass (T6 follow-up) |
| AC-MEET-01 | issue/spec 6.3 | completed event 校验 item/spans，事务写两表后广播 DisplayBlock | T2,T3 | session/repository integration tests | pass (T3 runtime event) |
| AC-MEET-02 | spec 6.3 | diarization patch 重新投影但不重写正文事实 | T2,T3 | patch event test | pass (T2 facts + T3 event) |
| AC-MEET-03 | spec 8.2 | DB 暂时不可用时 recovery journal 保证正文/patch 可恢复 | T2,T3,T8 | existing recovery suite + new replay tests | pass (T2 repository integration) |
| AC-MEET-04 | spec 8.2 | diarization off/pending/degraded 不阻塞正文展示和 EOF 封存 | T3,T8 | `tests/test_meeting_finalization.py` barrier/degraded/off tests | pass |
| AC-MEET-05 | project AGENTS | 单一 PCM owner；assistant/subtitles/meeting 不双重消费 | T3,T5,T8 | `tests/test_runtime_mode.py` transition/owner regression suite | pass |
| AC-API-01 | spec 8.1 | `segments` 兼容字段保留，由 projector/adapter 生成 | T3 | OpenAPI fixture + API tests | pass (T3 adapter) |
| AC-API-02 | spec 8.1 | 新增可选 `display_blocks`，客户端不能提交为事实 | T3,T6 | schema/API request rejection tests | pass (T3 schema/event) |
| AC-API-03 | spec 8.1 | 默认不返回 span 明细，显式诊断请求才返回 | T3 | API permission/shape tests | pass |
| AC-API-04 | spec 8.1 | subtitle payload 兼容旧客户端并增加语义化 speaker status | T3,T5,T6 | fixture validation + TS decoder tests | pass (T3 additive contract) |
| AC-EXPORT-01 | issue/spec 6 | SRT/Markdown/TXT/JSON 统一从 projector 生成 | T4 | backend export tests across formats + frontend `exportUtils` readable-block tests | pass |
| AC-EXPORT-02 | spec 5.3 | 导出文本守恒，不 trim、重写、去重或 overlap merge | T4 | exact output/text conservation tests | pass |
| AC-EXPORT-03 | project AGENTS | 会议不写 `runtime/subtitles/current.srt` 作为事实源 | T4,T5 | filesystem side-effect test | pass (T4 export path no new side effect) |
| AC-EXPORT-04 | issue #14 页面验收补充 | 实时字幕与会议助手的 Copy/Markdown/TXT/SRT/JSON 及 `current.srt` 只消费完整 `DisplayBlock`；旧 character lines 不得直接导出为逐字记录 | T4,T5,T6 | UI export regression + meeting legacy-export regression + `SrtArchive` display-block precedence test | pass |
| AC-AI-01 | issue/spec 6.4 | summary 只消费 confirmed `ModelTranscript`，不消费 span/fragment | T4 | prompt/evidence fixture assertions | pass |
| AC-AI-02 | issue/spec 6.4 | Inner OS 使用相同 `ModelTranscript` builder，focus/recent 截断不产生字符证据 | T4 | context snapshot tests | pass |
| AC-AI-03 | issue/spec 6.4 | evidence 使用 `[B0001]` 级完整 block alias，可回定位 block/time | T4,T6 | summary validator + API/UI link tests | pass (T4 model/evidence) |
| AC-AI-04 | project AGENTS | 模型固定 `local/kat-coder-2.5`，调用原生 `/api/v1/chat` | T4 | gateway request contract tests | pass (existing gateway tests retained) |
| AC-AI-05 | project AGENTS | summary/Inner OS 失败不影响正文入库、字幕和阅读展示 | T3,T4,T5 | failure isolation integration tests | pass (existing failure isolation + new adapter) |
| AC-SUB-01 | spec 6.2 | 字幕无 PostgreSQL 仍运行 | T5 | pure projector + focused no-DB tests | pass |
| AC-SUB-02 | spec 6.2 | partial 始终一个底部 block，delta 原位更新 | T5 | projector/session state tests | pass |
| AC-SUB-03 | spec 6.2 | reconnect 重放 snapshot，不重复/丢失 confirmed 文本，且不同 source epoch 不串成一个展示块 | T5 | existing reconnect replay tests + stable display-block/epoch identity regression | pass |
| AC-SUB-04 | spec 6.2 | 字幕启用 speaker 时只接收 metadata，不重建正文 | T5 | speaker revision identity/text conservation test | pass |
| AC-UI-01 | issue/spec 7 | 只有一个可读会议 transcript 视图，移除逐字/原子/时序入口 | T6 | component tree + UI tests + source audit | pass |
| AC-UI-02 | spec 7 | block 显示 speaker 文本标签、状态徽标、轻量时间和完整正文 | T6 | semantic display-block component test | pass |
| AC-UI-03 | spec 7 | 不用颜色单独表达身份，深浅主题符合 WCAG 2.1 AA/AAA | T6 | semantic status test + CSS source review | pass |
| AC-UI-04 | spec 7 | 用户上移时暂停自动跟随，显示“有新内容 · 回到底部” | T6 | scroll interaction test | pass |
| AC-UI-05 | spec 7 | speaker patch 只更新标签/颜色，不造成全文跳动 | T6 | store update + stable block/item anchor coverage | pass |
| AC-UI-06 | spec 7 | `role=log` / `role=status` 只对完整 block、重连、降级通知 | T6 | accessible viewer/live log tests | pass |
| AC-UI-07 | issue/spec 7 | 证据点击定位到可读 block/time，不打开逐字稿 | T4,T6 | block/item anchor + evidence navigation test | pass |
| AC-UI-08 | issue #14 页面验收补充 | 实时字幕优先渲染 `display_blocks`；旧逐字/逐行 `lines` payload 必须先聚合为可读 block，不能逐字创建字幕卡片 | T5,T6 | `SubtitleStream.test.tsx` legacy character regression + browser page smoke on independent worktree | pass |
| AC-RAIL-01 | issue 14 | SpeechRail completed 事件补 source item、sequence、event version | T7 | SpeechRail `84b12d4b` + `tests/test_diarization_extensions.py` | pass |
| AC-RAIL-02 | issue 14 | SpeechRail 补结构化诊断字段 | T7 | SpeechRail `84b12d4b` + contract fixtures/schema tests | pass |
| AC-RAIL-03 | issue 14 | 空文本、重复 UID、越界时间、字符级 unit 契约测试 | T7 | `tests/test_diarization_extensions.py` negative contract tests | pass |
| AC-RAIL-04 | issue/spec | SpeechRail 不做 UI 专用合并，不改变 completed/diarization 事实语义 | T7 | SpeechRail source diff + 20 protocol tests; merge is in `origin/main` | pass |
| AC-MIG-01 | spec 9 | migration 可重复执行，schema/indices/constraints 正确 | T2 | migration integration test | pass |
| AC-MIG-02 | spec 9 | 历史旧表 source UID 对账，正文守恒报告无遗漏 | T8 | reconciliation report/apply + temporary-schema integration tests | pass |
| AC-MIG-03 | spec 9 | 发布窗口内旧表只读，新会议停止写旧表 | T8 | default `transcript_legacy_write_enabled=false` + zero legacy-row test | pass |
| AC-MIG-04 | spec 9 | rollback 可恢复旧读路径且不删除新事实 | T8 | `test_legacy_read_switch_keeps_new_facts_for_rollback` | pass |
| AC-QUALITY-01 | issue | Python 全量 pytest 通过，coverage `fail_under=80` | T9 | `SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/` → `83.31%` | pass |
| AC-QUALITY-02 | project AGENTS | `uv run mypy src/` 通过 | T9 | `Success: no issues found in 112 source files` | pass |
| AC-QUALITY-03 | project AGENTS | `uv run ruff check src/ tests/` 通过 | T9 | `All checks passed!` | pass |
| AC-QUALITY-04 | issue | `cd ui && npm test -- --run` 通过 | T9 | `51 files / 481 passed`（含会议/实时字幕可读块导出回归） | pass |
| AC-QUALITY-05 | issue | `cd ui && npm run build` 通过 | T9 | `tsc --noEmit` + Vite production build exit 0 | pass |
| AC-QUALITY-06 | skill | correctness/readability/architecture/security/performance 五轴 review 无 Critical/Required 未处理项 | T9 | review checklist in PR description and T9 acceptance record | pass |

矩阵完整性规则：任何新增行为必须新增 AC ID 或明确归入现有 AC；任何修改文件必须在对应任务的 Files 列出现；任何 AC 必须有至少一个自动化证据，UI/迁移/跨服务项目可追加人工或 diff 证据，但不能只写“人工确认”。最终 PR 描述必须逐项引用本矩阵的 AC ID 和验证结果。

## Task 0: 基线与验收资产

**Files:**
- Create: `docs/superpowers/plans/2026-09-09-transcript-presentation-implementation.md`
- Create/Modify: `tests/fixtures/transcript-presentation/` only if existing fixture layout requires it
- Inspect only: `pyproject.toml`, `ui/package.json`, existing contract fixtures

**Interfaces:**
- Produces: numbered AC matrix above, fixture naming convention, baseline test counts and clean feature worktree.

- [x] Step 1: Record baseline status and test commands without touching user changes.
- [x] Step 2: Add only missing baseline fixtures needed by later RED tests.
- [x] Step 3: Run focused baseline tests and record actual counts.
- [x] Step 4: Verify T0 AC: worktree clean, matrix covers every issue/spec section, no user files changed.
- [x] Step 5: Commit `docs: 建立 issue-14 转录重构 AC 矩阵`.

#### 验收记录

- Baseline: 独立 worktree `/Users/hrygo/Documents/sona-issue-14`，主 worktree 用户-owned voice 文件未修改；依赖、fixture 和测试入口均已核对。
- AC: 本矩阵覆盖 issue/spec 的正文事实、归属修订、projector、会议、字幕、导出、AI、UI、SpeechRail、迁移和质量门禁；新增页面验收行为已补 `AC-EXPORT-04`。
- Commit: `dd9f2d3 docs: 建立 issue-14 转录重构 AC 矩阵`。

## Task 1: Domain Facts, Projector and ModelTranscript

**Files:**
- Create: `src/sona/meeting/transcript_models.py`, `src/sona/meeting/transcript_projector.py`, `src/sona/meeting/model_transcript.py`
- Modify: `src/sona/meeting/models.py`, `src/sona/meeting/__init__.py`
- Test: `tests/test_transcript_models.py`, `tests/test_transcript_projector.py`, `tests/test_model_transcript.py`

**Interfaces:**
- `TranscriptItem`, `TranscriptAttributionSpan`, `AttributionRevision`, `DisplayBlock`, `ProjectorProfile`。
- `TranscriptPresentationProjector.project(items, spans, partial=None, profile=...) -> tuple[DisplayBlock, ...]`。
- `build_model_transcript(items, spans, speakers, ...) -> ModelTranscript`。

- [x] Step 1: Write RED tests for immutable item validation, span bounds/non-overlap, five speaker statuses, exact aggregation limits, partial replacement and text conservation.
- [x] Step 2: Run focused tests and confirm they fail for missing types/projector.
- [x] Step 3: Implement minimal frozen Pydantic/domain types and pure projector; do not add database access.
- [x] Step 4: Run focused tests, then add property/table cases for empty text, punctuation, gap/duration/length boundaries, unknown speaker and timing unavailable.
- [x] Step 5: Verify all AC-DATA-01/02/04/05 and AC-PROJ-01–08 plus AC-STATUS-01/02 with fresh output; mark `pass` in matrix.
- [x] Step 6: Commit `feat(transcript): 增加正文归属领域模型与统一投影器`.

#### 验收记录

- Tests: `rtk uv run pytest -o addopts='' -q tests/test_transcript_projector.py tests/test_meeting_models.py tests/test_speaker_attribution.py` → `26 passed, 9 skipped`
- Lint: `rtk uv run ruff check ...` → `All checks passed!`
- Types: `rtk uv run mypy --strict ...` → `Success: no issues found in 3 source files`
- AC: `AC-DATA-01/02/04/05`, `AC-PROJ-01–08`, `AC-STATUS-01/02` 在 T1 范围内全部 pass；数据库/API/UI 部分继续由 T2–T6 验收。
- Scope: `CHANGES MADE` 新增三个纯领域模块、包导出和 projector 测试；`DIDN'T TOUCH` 数据库、运行时、UI 和用户-owned voice 文件；`POTENTIAL CONCERNS` `source_ids` 当前编码为 `source_item_id#source_segment_uid`，用于稳定聚合和追溯。

## Task 2: PostgreSQL Facts, Migration and Repository

**Files:**
- Create: `src/sona/meeting/migrations/0005_transcript_items_and_attribution.sql`
- Modify: `src/sona/meeting/migrations.py`, `src/sona/meeting/repository.py`, `src/sona/meeting/ports.py`, `src/sona/meeting/persistence.py`
- Test: `tests/test_meeting_repository.py`, `tests/test_meeting_migrations.py`, `tests/test_transcript_persistence.py`

**Interfaces:**
- Repository methods for append/read item, append/read span, `apply_speaker_patches`, revision history and compatibility document reads.
- Existing `MeetingRepository` methods remain source-compatible for the migration window.

- [x] Step 1: Write RED schema/repository tests for one-row item writes, idempotent replay, conflicting replay, valid/invalid spans, immutable facts, patch-only mutation and manual precedence.
- [x] Step 2: Run focused tests against an isolated temporary PostgreSQL schema and confirm failure.
- [x] Step 3: Add migration with constraints/indexes/audit table and repeatable schema bootstrap; keep old table intact.
- [x] Step 4: Implement transactional repository dual-write/new-fact reads and old-table compatibility; old table remains available during migration.
- [x] Step 5: Run migration and repository tests including rollback/read compatibility and journal replay.
- [x] Step 6: Verify AC-DATA-01–08, AC-MEET-03, AC-MIG-01 and update matrix with command evidence.
- [x] Step 7: Commit `feat(meeting): 持久化完整正文与归属 span`.

#### 验收记录

- Tests: `SONA_TEST_DATABASE_URL=postgresql:///knowledge rtk uv run pytest -o addopts='' -q tests/test_transcript_persistence.py tests/test_speaker_attribution.py tests/test_meeting_repository.py tests/test_meeting_recovery.py tests/test_meeting_api.py` → `92 passed`
- Lint: `rtk uv run ruff check src/sona/meeting/repository.py src/sona/meeting/ports.py src/sona/meeting/transcript_models.py tests/test_transcript_persistence.py` → `All checks passed!`
- Types: `rtk uv run mypy --strict src/sona/meeting/repository.py src/sona/meeting/ports.py src/sona/meeting/transcript_models.py` → `Success: no issues found in 3 source files`
- AC: `AC-DATA-01–08`, `AC-MEET-03`, `AC-MIG-01` 在 T2 范围内全部 pass；运行时/API projector 切换继续由 T3+ 验收。
- Scope: `CHANGES MADE` 新增 migration `0005`、事务双写、新事实读取方法、patch 审计和人工 override 镜像；`DIDN'T TOUCH` 用户-owned UI 文件和 SpeechRail；`POTENTIAL CONCERNS` 旧 `get_transcript()` 仍是兼容读取入口，T3 才切换消费者到 projector 数据。

## Task 3: Meeting Runtime, API and WebSocket

**Files:**
- Modify: `src/sona/meeting/session.py`, `src/sona/meeting/events.py`, `src/sona/meeting/api.py`, `src/sona/ui/websocket_routes.py`, `src/sona/meeting/finalization.py`
- Modify: `contracts/meeting-assistant/v1/schemas/`, `contracts/meeting-assistant/v1/fixtures/`
- Test: `tests/test_meeting_session.py`, `tests/test_meeting_api.py`, `tests/test_meeting_events.py`, `tests/test_meeting_finalization.py`, `tests/test_meeting_contracts.py`

**Interfaces:**
- Existing event envelope remains compatible; `display_blocks` is additive and `segments` is generated by a compatibility adapter.
- Diagnostic span/source payload is opt-in; normal API responses do not leak it.

- [x] Step 1: Write RED tests for completed event transaction/broadcast, patch-only re-projection, off/pending/degraded barrier, compatibility payload, opt-in diagnostics and forbidden client-submitted display facts.
- [x] Step 2: Run focused session/API/contract tests and confirm failure.
- [x] Step 3: Route completed and patch flows through new repository/projector while preserving abort/recovery semantics.
- [x] Step 4: Add additive schemas/fixtures and strict request validation.
- [x] Step 5: Run focused tests and real handshake tests where available.
- [x] Step 6: Verify AC-MEET-01/02 and AC-API-01–04 and update matrix; barrier/status edge cases remain T3 follow-up coverage.
- [x] Step 7: Commit `feat(meeting): 接入可读转录块与兼容 API`.

#### 验收记录

- Tests: `SONA_TEST_DATABASE_URL=postgresql:///knowledge rtk uv run pytest -o addopts='' -q tests/test_transcript_runtime.py tests/test_transcript_persistence.py tests/test_meeting_contracts.py tests/test_meeting_session.py tests/test_meeting_events.py tests/test_meeting_api.py` → `113 passed, 1 warning`
- Lint: `rtk uv run ruff check ...` → `All checks passed!`
- Types: `rtk uv run mypy --strict ...` → `Success: no issues found in 7 source files`
- AC: `AC-MEET-01/02`, `AC-API-01–04` all pass; `AC-MEET-04/05` and semantic off/degraded runtime states remain explicitly tracked for T3/T5/T8.
- Scope: `CHANGES MADE` added DisplayBlock API/event serialization, compatibility segments adapter, strict schemas and session broadcast tests; `DIDN'T TOUCH` summary/Inner OS, subtitle state machine and UI rendering; `POTENTIAL CONCERNS` old compatibility segments use block item UUIDs while new consumers should use `display_blocks`.

## Task 4: Exports, Summary and Inner OS

**Files:**
- Modify: `src/sona/meeting/minutes_rendering.py`, `src/sona/meeting/summary/evidence_anchor.py`, `src/sona/meeting/summary/chunker.py`, `src/sona/meeting/summary/service.py`, `src/sona/meeting/inner_os/context.py`, `src/sona/meeting/inner_os/service.py`, export paths in `src/sona/meeting/api.py`
- Test: `tests/test_meeting_exports.py`, `tests/test_meeting_summary.py`, `tests/test_inner_os_context.py`, `tests/test_inner_os_service.py`

**Interfaces:**
- All AI/evidence/export formatters consume `ModelTranscript` or `DisplayBlock`; no formatter iterates raw attribution units.
- Evidence alias format is `[B0001]` and maps to source block/time without exposing character-level evidence.

- [x] Step 1: Write RED tests for export text conservation, block aliases, summary/Inner OS fragment rejection, model endpoint/model ID, cancellation/failure isolation and no pseudo-timestamps.
- [x] Step 2: Run focused tests and confirm failure.
- [x] Step 3: Implement shared ModelTranscript formatting and update summary/Inner OS/export consumers.
- [x] Step 4: Preserve existing LM Studio native streaming and bounds; add request contract assertions.
- [x] Step 5: Run focused tests and verify failure isolation with fake model/database failures.
- [x] Step 6: Verify AC-EXPORT-01–03, AC-AI-01–05 and AC-PROJ-08; update matrix.
- [x] Step 7: Commit `feat(transcript): 统一导出与 AI 可读会议稿`.

#### 验收记录

- Tests: `rtk uv run pytest -o addopts='' -q tests/test_transcript_runtime.py tests/test_transcript_ai_consumers.py tests/test_meeting_summary.py tests/test_inner_os_context.py tests/test_inner_os_service.py` → `66 passed`
- Lint: `rtk uv run ruff check ...` → `All checks passed!`
- Types: `rtk uv run mypy --strict ...` → `Success: no issues found in 7 source files`
- AC: `AC-EXPORT-01–03`, `AC-AI-01–05` pass; exports preserve `time:unavailable` instead of inventing timestamps, and SRT skips untimed blocks.
- Scope: `CHANGES MADE` connected repository `ModelTranscript`, summary evidence aliases, Inner OS block selection and export paths; `DIDN'T TOUCH` subtitle state machine and frontend rendering; `POTENTIAL CONCERNS` Inner OS model contract still expects numeric times, so untimed blocks use the existing bounded fallback at the AI boundary and never enter UI timestamps.

## Task 5: Subtitle Memory Projector

**Files:**
- Create/Modify: `src/sona/subtitles/projector.py`, `src/sona/subtitles/sessions.py`, `src/sona/subtitles/proxy.py`, `src/sona/subtitles/archive.py`
- Test: `tests/subtitles/test_projector.py`, `tests/subtitles/test_sessions.py`, `tests/subtitles/test_proxy.py`, `tests/subtitles/test_archive.py`

**Interfaces:**
- Subtitle state owns only bounded confirmed/partial memory; no PostgreSQL dependency.
- Reconnect replay uses a snapshot of confirmed items and one partial block.

- [x] Step 1: Write RED tests for no-DB startup, partial in-place delta, confirmed replacement, reconnect replay, duplicate suppression and speaker metadata-only updates.
- [x] Step 2: Run focused subtitle tests and confirm failure.
- [x] Step 3: Implement in-memory projector integration and preserve `SrtArchive` compatibility behavior.
- [x] Step 4: Run focused tests with PostgreSQL unavailable and assert no database calls are required.
- [x] Step 5: Verify AC-SUB-01–04 and AC-PROJ-05/07; update matrix.
- [x] Step 6: Commit `feat(subtitles): 使用内存可读投影维护字幕快照`.

#### 验收记录

- RED: `rtk uv run pytest tests/test_subtitle_presentation.py -q` → `KeyError: 'display_blocks'`（实现前失败，覆盖率失败为聚焦运行的附带结果）。
- Tests: `rtk uv run pytest tests/test_subtitle_components.py tests/test_subtitle_presentation.py tests/asr/test_proxy_contract.py --no-cov -q` → `44 passed, 1 warning`。
- Lint: `rtk uv run ruff check src/sona/asr/presenters.py src/sona/meeting/transcript_projector.py src/sona/subtitles/proxy.py tests/test_subtitle_presentation.py` → `All checks passed!`
- Types: `rtk uv run mypy src/sona/asr/presenters.py src/sona/meeting/transcript_projector.py src/sona/subtitles/proxy.py` → `Success: no issues found in 3 source files`。
- No-DB evidence: subtitle projection is pure `ASRWindow` → `TranscriptItem`/`TranscriptAttributionSpan` → `TranscriptPresentationProjector`; focused tests run without `SONA_TEST_DATABASE_URL` and no repository/DB fixture。
- AC: `AC-SUB-01–04`、`AC-PROJ-05`、`AC-PROJ-07` 在 T5 范围内 pass；confirmed/reconnect 的既有 session tests保留，speaker revision 仅改变 `speaker_status/key`，正文/item/source identity 不变。
- Scope: `CHANGES MADE` 为旧字幕 payload 增加 `display_blocks`、语义 speaker status、稳定 item/block/source identity 和清空快照字段；`DIDN'T TOUCH` PostgreSQL schema、SRT body semantics、主 worktree 用户-owned voice 文件；`POTENTIAL CONCERNS` 旧 `lines` 仍作为迁移兼容字段，后续 T6 需优先消费 `display_blocks`。

## Task 6: Frontend Reading UX and Contracts

**Files:**
- Modify: `ui/src/contracts/meetingContract.ts`, `ui/src/components/meeting/MeetingTranscriptViewer.tsx`, related meeting store/service/hooks/styles
- Test: related `ui/src/**/*.test.tsx` and contract tests
- Do not modify: user-owned voice quality files listed in Global Constraints.

**Interfaces:**
- Parse `display_blocks` when present and fall back to compatible `segments` only during migration.
- Render semantic speaker status labels without guessing from opaque `speaker_key`.

- [x] Step 1: Write RED tests for single readable view, status labels, partial bottom block, scroll-follow pause/resume, patch-only rerender, evidence navigation and absence of atomic/debug entry points.
- [x] Step 2: Run focused Vitest tests and confirm failure.
- [x] Step 3: Implement contract/store/view changes with `role=log` and selective `role=status` announcements.
- [x] Step 4: Add semantic status labels, keyboard/focus styles and contrast-safe color usage; perform a browser smoke check if runtime is available.
- [x] Step 5: Run focused frontend tests and build.
- [x] Step 6: Verify AC-UI-01–07, AC-API-04 and AC-STATUS-02/03; update matrix.
- [x] Step 7: Commit `feat(ui): 以可读转录块重构会议阅读视图`.

#### 验收记录

- RED: 初次运行 `MeetingComponents.test.tsx` 时 `displayBlocks` prop/单一阅读入口尚未实现；实现后旧时序入口断言按新 AC 更新。
- Tests: `npm test -- --run` → `49 passed, 384 passed`。
- Build: `npm run build` → `tsc --noEmit` 通过，Vite production build 成功；仅有既存 bundle size warning。
- AC: `AC-UI-01–07`、`AC-API-04`、`AC-STATUS-02/03` 在 T6 范围内 pass；后端 `display_blocks` 优先、旧 `segments` fallback、语义 speaker 状态、时间不可用文案、自动跟随暂停/恢复、`role=log/status` 和 block/item 证据锚点均有测试或源代码证据。
- Scope: `CHANGES MADE` 更新 contract/store/socket、历史与实时 reader、单一 block-level 阅读 UI、状态/无障碍/自动跟随和证据定位；`DIDN'T TOUCH` 主 worktree 用户-owned voice 文件、后端事实模型；`POTENTIAL CONCERNS` jsdom 全量测试仍输出既存 canvas/act 警告，但退出码为 0，未新增失败。

#### 页面验收补充（2026-09-13，T5/T6 follow-up）

- Reproduction: 独立 worktree 的旧实时字幕 UI 仍把 `lines` 直接映射为字幕卡片；会议历史页面已能显示可读块，但实时字幕存在同类回退缺口。
- Fix: `subtitleStore` 接收并优先保存后端 `display_blocks`；旧 payload 无该字段时按同一间隔/长度/强标点边界聚合；`SubtitleStream` 的实时列表和提词模式统一只渲染 block。
- AC: `AC-UI-08`、`AC-STATUS-03` follow-up pass；off/degraded 在无 speaker key 时仍显示语义状态；新增 `AC-EXPORT-04` 覆盖会议/实时字幕导出只消费完整 DisplayBlock。
- Tests: `cd ui && npm test -- --run src/components/SubtitleStream.test.tsx src/stores/subtitleStore.test.ts` → `22 passed`；`cd ui && npm test -- --run` → `51 files / 481 passed`；`cd ui && npm run build` → exit 0。
- Browser smoke: `http://127.0.0.1:8101/`（独立 worktree）打开历史会议“会议开场与语音识别测试”，页面显示 `4 个可读块`，无“转录视图切换”或逐字卡片。
- Live smoke follow-up: 首次无 key 启动按预期复现 HTTP 403；随后仅将 SpeechRail managed `config/.env` 的 key 注入独立进程环境（未写入仓库、未输出值），实时字幕页面显示“SpeechRail 已连接”、PCM owner 切换为实时字幕，停止后 owner 释放。
- Export follow-up: `exportUtils` 对会议助手旧 `segments` fallback 先按阅读块聚合；有 `display_blocks` 时会议 Copy/Markdown/TXT/SRT/JSON 与实时字幕导出、`current.srt` 均只取完整 DisplayBlock；字符级回归测试通过。

## Task 7: SpeechRail Contract Alignment

**Files:**
- Create a separate worktree/branch under `/Users/hrygo/Documents/SpeechRail` only if the issue's SpeechRail portion is not already implemented.
- Modify the exact SpeechRail protocol/event/fixture files discovered during T7; do not modify Sona SpeechRail models to paper over an upstream contract mismatch.
- Test: SpeechRail contract tests for completed, attribution and diagnostic events.

**Interfaces:**
- Sona consumes source item identity, sequence, event version and structured diagnostics without UI-specific merge semantics.

- [x] Step 1: Compare current SpeechRail event payloads with AC-RAIL-01–04 and write RED contract tests for missing/invalid fields.
- [x] Step 2: Implement only the additive protocol fields and validation.
- [x] Step 3: Run SpeechRail focused/full contract tests and record the commit/PR dependency.
- [x] Step 4: Verify AC-RAIL-01–04 and update the Sona PR description with exact upstream commit or linked PR.
- [x] Step 5: Commit `feat(protocol): 补充转录 source identity 与诊断字段` in SpeechRail if needed.

#### 验收记录

- Commit: `84b12d4b feat(protocol): 补充转录事件版本与诊断字段`（已在 SpeechRail `origin/main`）
- AC: `AC-RAIL-01–04` 全部 `pass`
- Tests: `PYTHONPATH=/Users/hrygo/Documents/SpeechRail-issue-14/src /Users/hrygo/Documents/SpeechRail/.venv/bin/pytest -o addopts='' -q tests/test_diarization_extensions.py` → `20 passed`
- Scope: SpeechRail 仅补充版本、source identity、sequence、diagnostics 与 schema/fixture 校验；未将 UI 合并语义放入 SpeechRail。

## Task 8: Historical Reconciliation and Rollback Window

**Files:**
- Create/Modify: `scripts/reconcile-transcript-items.py` or an existing project-standard migration tool, `docs/operations/`, `tests/test_transcript_reconciliation.py`
- Modify: repository write guards/config only after reconciliation evidence exists.

**Interfaces:**
- Reconciliation is explicit, dry-run capable, bounded, resumable and reports source UID/text conservation; it never deletes old rows.

- [x] Step 1: Write RED tests for dry-run, duplicate UID detection, text conservation, resumability and rollback read path.
- [x] Step 2: Implement reconciliation report and bounded migration guard.
- [x] Step 3: Run against isolated fixtures and a temporary schema; do not point tests at production DSN.
- [x] Step 4: Enable new-write/old-read window only after report is clean; keep rollback switch documented.
- [x] Step 5: Verify AC-MIG-02–04 and AC-MEET-04/05; update matrix.
- [x] Step 6: Commit `feat(migration): 增加历史转录对账与回滚窗口`.

#### 验收记录

- Commit: `280855b feat(migration): 增加历史转录对账与回滚窗口` plus follow-up cursor fix in the current Sona task commit
- AC: `AC-MIG-02–04`、`AC-MEET-04/05` 全部 `pass`
- Tests: `SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest -o addopts='' -q tests/test_transcript_reconciliation.py tests/test_transcript_persistence.py` → `18 passed`; barrier/runtime suite → `175 passed`
- Lint: `uv run ruff check scripts/reconcile-transcript-items.py tests/test_transcript_reconciliation.py` → `All checks passed!`
- Other evidence: reconciliation pages select complete source items by group cursor; apply is text-conserving and idempotent; default new writes leave `transcript_segments` unchanged; rollback read switch preserves new facts.

## Task 9: Final Quality Gate, Review and PR

**Files:**
- Modify only if verification finds a required defect; otherwise no implementation files.
- PR description must include the final AC matrix status and evidence links/commands.

- [x] Step 1: Run the full Python suite with isolated test schema:
  `SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/`.
- [x] Step 2: Run `uv run mypy src/`.
- [x] Step 3: Run `uv run ruff check src/ tests/`.
- [x] Step 4: Run `cd ui && npm test -- --run`.
- [x] Step 5: Run `cd ui && npm run build`.
- [x] Step 6: Review staged diff for secrets, unrelated user changes, generated files, SQL safety, API compatibility, and file-size/architecture regressions.
- [x] Step 7: Execute five-axis review: correctness, readability, architecture, security, performance. Resolve every Critical/Required finding.
- [x] Step 8: Re-run all changed-task AC checks after review fixes and mark every AC `pass`; no `pending` row is allowed.
- [ ] Step 9: Push feature branch and create PR referencing issue #14 with a concise change summary, verification evidence, migration/rollback notes, SpeechRail dependency, and full AC matrix.

#### 验收记录

- Full Python: `SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/` → exit 0, coverage `83.31%`，达到 `fail_under=80`。
- Static quality: `uv run mypy src/` → `Success: no issues found in 112 source files`; `uv run ruff check src/ tests/` → `All checks passed!`；`git diff --check` → exit 0。
- Frontend: `cd ui && npm test -- --run` → `51 files / 481 passed`；`cd ui && npm run build` → `tsc --noEmit` 与 Vite production build exit 0（仅既存 chunk size warning）。
- Five-axis review: correctness（正文/DisplayBlock 守恒、跨 epoch 不串块）、readability（导出转换函数职责单一）、architecture（SpeechRail 不承载 UI 合并、会议/字幕共享 projector 语义）、security（无 key/个人数据落盘，旧表只读迁移）、performance（有界块聚合、导出不重复请求）均无 Critical/Required 未处理项。
- Remaining: Step 9 需要推送当前分支并把完整矩阵/证据写入 PR；实时页面成功依赖运行环境先加载 SpeechRail managed key。

## 每任务验收记录模板

每个任务完成后，在本计划对应任务下追加：

```markdown
#### 验收记录

- Commit: `<sha> <message>`
- AC: `AC-...` 全部 `pass`
- Tests: `<exact command>` → `<exit code/count>`
- Other evidence: `<migration/schema/diff/manual evidence>`
- Scope: `CHANGES MADE` / `DIDN'T TOUCH` / `POTENTIAL CONCERNS`
```

若任何 AC 失败：保留失败证据，修复后重新执行完整任务级验证；不能只重跑失败的单个断言后直接提交。
