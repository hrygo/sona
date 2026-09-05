---
title: "Sona × SpeechRail 会议讲话人分离端到端验收报告 (SPK-E2E-1)"
status: accepted
type: acceptance_report
category: meeting
version: "1.0.0"
date: 2026-09-06
owners: [sona-core, speechrail-core]
tags: [speechrail, diarization, e2e, acceptance, spk-e2e-1]
---

# Sona × SpeechRail 会议讲话人分离端到端验收报告 (SPK-E2E-1)

> 验收日期：2026-09-06<br>
> 责任人：Sona Core Team, SpeechRail Core Team<br>
> 规范依据：[Sona 端到端设计](../architecture/speaker-diarization-e2e-design.md) 与 [SpeechRail 端到端设计](file:///Users/hrygo/Documents/SpeechRail/docs/architecture/speaker-diarization-e2e-design.md)<br>
> 实施计划：[Sona S0–S4 实施计划](../superpowers/plans/2026-09-05-speaker-diarization-e2e.md) 与 [SpeechRail R0–R5 实施计划](file:///Users/hrygo/Documents/SpeechRail/docs/superpowers/plans/2026-09-05-speaker-diarization-e2e.md)

---

## 1. 验收范围与执行基准

本报告记录 Sona 与 SpeechRail 针对 SPK-E2E-1（会议多说话人分离与持续归属流）的端到端接线与联合验证结果。

### 1.1 代码与环境基准

| 维度 | SpeechRail | Sona |
|---|---|---|
| **代码分支 / 提交** | `master` @ `d9798cd` | `main` @ `18fb2a3` + 本轮 S4 联合验收提交 |
| **运行时引擎** | Python 3.12.14, uv, FastAPI | Python 3.12.14, uv, React 19, TypeScript, PostgreSQL |
| **协议扩展能力** | `speechrail:diarization_extensions:v1` | `speechrail:diarization_extensions:v1` (opt-in) |
| **测试数据库** | N/A (确定性 fake / 隔离回归) | PostgreSQL `knowledge` 临时 schema（`vr_test_e2e_*`，测试完毕级联清理） |
| **测试套件覆盖** | 52 passed, ruff & mypy 100% | 1092 passed (含全量集成测试), 分支覆盖率 83.14%, mypy 0 issues |

---

## 2. 核心功能与边界验收矩阵

联合端到端测试落盘于 [`tests/test_diarization_e2e.py`](file:///Users/hrygo/Documents/sona/tests/test_diarization_e2e.py)，共 7 项综合用例，全部在真实 PostgreSQL 事务与 WebSocket 协议栈上跑通。

| 序号 | 验证维度 | 对应测试用例 | 预期行为与验收事实 | 判定 |
|---|---|---|---|---|
| **E2E-1** | 多 Commit、修订推进与样本对齐 | `test_diarization_e2e_three_commits_and_speaker_revisions` | 1. 连续 3 次 ASR commit 创建不可变正文单元与时间戳。<br>2. 初始归属为 unknown，时间戳单调连续（0–7000ms）。<br>3. 逐次推送 `SpeakerPatchEvent`，`content_revision` 与 `transcript_revision` 严格递增。<br>4. 文本原位保留，不产生后缀截断或重复字。 | **PASSED** |
| **E2E-2** | 断线重连与 Source Epoch 幂等去重 | `test_diarization_e2e_reconnection_and_epoch_dedup` | 1. 同一 source epoch 的重放 event 自动去重，返回 None，不新增重复 segment。<br>2. 网络断线重连后进入新 source epoch（epoch 2），时钟连续推进，两轮文本完整保全。 | **PASSED** |
| **E2E-3** | 人工改名与手动更正优先（Override Precedence） | `test_diarization_e2e_manual_speaker_override_precedence` | 1. 说话人在 UI 改名后，后续模型自动 patch 不覆盖用户改名。<br>2. 用户设置 segment override 后，有效归属保持人工 override；底层 `model_speaker_key` 客观更新声学证据。<br>3. 撤销人工 override 后，平滑回退至模型最新声学归属。 | **PASSED** |
| **E2E-4** | 瞬态 DB 故障与 Recovery Journal 恢复 | `test_diarization_e2e_transient_db_failure_and_journal_recovery` | 1. 模拟数据库连接瞬态中断，持久化层安全捕获并降级，将 patch 写入 journal 文件。<br>2. Journal 文件目录权限严格为 `0700`，文件权限为 `0600`。<br>3. 数据库连接恢复后调用 `RecoveryJournal.replay`，成功落库并自动清理 journal 文件，转录事实零丢失。 | **PASSED** |
| **E2E-5** | Finalize 屏障与幂等性 | `test_diarization_e2e_finalize_barrier_and_idempotency` | 1. `RepositoryDiarizationGate` 等待分人持久化水位。<br>2. 水位达到时会议状态标记为 `complete`。<br>3. 重复调用 finalize 具有严格幂等性，不破坏会议终态。 | **PASSED** |
| **E2E-6** | Finalize 超时优雅降级 | `test_diarization_e2e_finalize_timeout_degrades_gracefully` | 1. 模拟网络抖动或分人延迟，超过 watermark 等待超时上限。<br>2. 系统安全标记分人状态为 `degraded`（原因 `finalization_timeout`），主会议转录正文完整封存，不引发服务崩溃。 | **PASSED** |
| **E2E-7** | 新旧 Rail × 新旧 Sona 四组合兼容矩阵 | `test_diarization_e2e_four_combinations_matrix` | 1. **New Rail + New Sona**: 双方成功协商 `speechrail:diarization_extensions:v1`，校验 `diarization_contract` 参数（16kHz, session_samples, 4 说话人）。<br>2. **New Rail + Legacy Sona**: Sona 不发 extensions，安全降级走 legacy 协议。<br>3. **Legacy Rail + New Sona**: Rail 缺少 capability，Sona 安全回退走 legacy 协议。<br>4. **Legacy Rail + Legacy Sona**: 纯旧协议平稳工作。 | **PASSED** |

---

## 3. 合约规范与静态验证

### 3.1 合约校验与 Fixtures

运行 `scripts/validate-meeting-contract.py`：
- 校验通过 21 组 JSON fixtures 与 25 组 JSON Schema。
- 覆盖标准会议详情、历史列表、说话人重命名、AI 纪要与 `speaker_details=1` opt-in 契约。

### 3.2 双仓代码质量门禁

#### Sona 门禁汇总
```bash
# 1. 后端单元与集成测试（需本地 PostgreSQL 运行）
SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/
# 结果：1092 passed, 4 warnings in 22.32s; 分支覆盖率: 83.14% (>= 80% 门禁达成)

# 2. Python Strict 类型检查
uv run mypy src/
# 结果：Success: no issues found in 108 source files

# 3. 代码风格与 Lint
uv run ruff check src/ tests/
# 结果：All checks passed!

# 4. 前端测试与构建
cd ui && npm test -- --run
# 结果：40 test files passed, 293 tests passed (100%)
cd ui && npm run build
# 结果：tsc 0 errors, vite build 成功输出静态资源
```

#### SpeechRail 门禁汇总
```bash
uv run --extra dev pytest tests/test_diarization_extensions.py tests/test_realtime.py -v
# 结果：52 passed in 1.48s
uv run --extra dev ruff check src tests
# 结果：All checks passed!
uv run --extra dev mypy src
# 结果：Success: no issues found in 27 source files
```

---

## 4. 架构约束与数据边界确认

1. **零本地重型模型**：Sona 严格不加载 Sortformer、CAM++、Whisper 或本地 ASR/TTS 模型，所有声学与分人推理完全由独立的 SpeechRail 服务（8201）负责。
2. **音频与声纹数据边界**：
   - PostgreSQL 数据库与 Recovery Journal 中绝对不存储原始音频 PCM、Base64 或声纹 embedding。
   - 说话人身份在会议作用域内为匿名 application key，不进行跨会议声纹聚类或实名绑定。
3. **Recovery Journal 隔离防护**：
   - 故障恢复日志仅记录 transient DB 错误期间的 segment 与 patch 事实。
   - 目录权限严格限制为 `0700`，文件权限限制为 `0600`，回放成功后立即原子删除。
4. **单向不可变正文**：
   - 文本内容在 `append_completed_item` 写入后全局不可变。
   - 说话人声学归属通过专用 `apply_speaker_patches` 事务就地修订，杜绝了历史基于窗口替换产生的尾部丢失问题。

---

## 5. 回退与应急预案

若生产环境遇到未知声学模型兼容性异常或极端网络抖动：
1. **协议层回退**：将 Sona 配置 `SONA_MEETING_DIARIZATION_EXTENSIONS_ENABLED` 置为 `false`，即刻回退至单人 legacy 转录与会后常规封存，无需重启数据库或改动 schema。
2. **数据库兼容性**：0003 数据库迁移采用完全加法迁移（`ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`），旧数据未做破坏性变更，回退无需执行 `DROP COLUMN`。
3. **数据一致性保护**：若回退发生，已记录的 `transcript_segments` 与 `meeting_speakers` 仍完全可读，不产生脏数据或悬挂外键。

---

## 6. 验收结论

Sona × SpeechRail 会议多说话人分离端到端链路（SPK-E2E-1）的协议协商、正文不可变追加、流式归属修订、人工优先仲裁、断线重连时钟推进、崩溃恢复 Journal 以及新旧双端兼容矩阵，各项关键指标均已严格达到设计与工程规范要求。

**综合评定：验收通过 (ACCEPTED)，准予转入正常运行态。**
