---
title: "Sona 会议讲话人分离端到端实施计划"
status: completed
type: execution_plan
category: meeting
version: "1.1.0"
date: 2026-09-05
last_updated: 2026-09-06
owners: [sona-core]
tags: [speechrail, diarization, implementation]
---

# Sona 会议讲话人分离端到端 Implementation Plan

> 按任务串行执行并逐项验证，使用 `executing-plans` 工作流；所有 S0–S4 任务已全部交付并通过端到端联合验收，详见 [2026-09-06 联合验收报告](../../operations/speaker-diarization-e2e-acceptance-2026-09-06.md)。

**Goal:** 让 Sona 可靠消费 SpeechRail 持续分人，保留正文、人工身份、会议时间和可恢复的终态。

**Architecture:** completed 创建不可变正文单元；speaker patch 经专用幂等事务原位修订；UI 与纪要消费同一持久化事实。新模式显式协商，旧链路保持可回退。

**Tech Stack:** Python 3.12、uv、WebSocket/httpx、psycopg/PostgreSQL、React/TypeScript、pytest 与现有前端测试工具。

**Spec:** [Sona SPK-E2E-1 设计](../../architecture/speaker-diarization-e2e-design.md)，公共服务端扩展见 `SpeechRail/docs/architecture/speaker-diarization-e2e-design.md` 第 5 节。

## 执行约束

- 每任务先写失败回归，再实施最小修改，再跑针对性测试；完成后检查 diff，按独立逻辑主题提交。不要提交运行配置、音频、日志和 benchmark 原始文件。
- 不运行 Sona 本地 ASR/Sortformer/CAM++；不在录制时调用 batch ASR；不持久化 PCM/embedding。
- Python `>=3.12,<3.13`。数据库只使用项目配置 schema 和 least-privilege 连接；测试必须专用临时 schema，不能写当前会议库。
- 新字段是加法迁移，旧数据不强制重写；rollback 不做 DROP COLUMN/TABLE。
- 本轮已观察到音色工作区等未提交改动。实施前重新检查，避免触碰 `ui/http_routes.py`、AssistantPanel 和 VoiceStudio 的其他任务修改；需要交叉文件时只修改可分离目标 hunk。
- 以下 fixture 名称和纯函数是计划新增测试接口，各任务定义输入/输出，不是现存可运行的 API。

## 文件责任地图

| 文件 | 责任 |
|---|---|
| `src/sona/speechrail/transport.py`、`transcription_events.py`、`transcriber.py` | 协商、严格解码、immutable text units、归属事件、EOF |
| `src/sona/asr/contracts.py`、`models.py` | vendor-neutral completed/patch/finalized 对象 |
| 新增 `src/sona/meeting/speaker_attribution.py` | SpeakerPatch/源键映射/冲突判定纯逻辑 |
| `src/sona/meeting/models.py`、`ports.py`、`repository.py` | 加法 schema、专用 append/patch 事务 |
| `src/sona/meeting/persistence.py`、`recovery.py` | journal 顺序与幂等落库 |
| `src/sona/meeting/finalization.py`、`session.py` | 分人 drain 屏障和整体 deadline |
| `src/sona/meeting/diarization_overlay.py`、`config/meeting.py` | 旧批校正隔离和默认关闭 |
| `src/sona/subtitles/proxy.py`、`meeting/asr_mapping.py` | source epoch、重放和时间线转换 |
| `src/sona/meeting/diarization_smoother.py` | 保留原始词与真实短插话，阅读层平滑 |
| `ui/src/contracts/meetingContract.ts`、`stores/meetingStore.ts`、`components/meeting/MeetingTranscriptViewer.tsx` | speaker 状态呈现、patch、人工更正 |
| `src/sona/meeting/summary/service.py`、`summary/evidence_anchor.py` | 固定 revision 纪要和证据锚点 |
| `contracts/meeting-assistant/v1/` | 新旧 presenter 的 query 协商、字段 schema、fixtures |

## S0：隔离 legacy overlay 与未知身份风险

**依赖：** 无，可先于 Rail 新协议交付。**交付：** 旧版本录制不再被会后批结果无条件改写。

**文件：** `config/meeting.py`、`meeting/diarization_overlay.py`、`finalization.py`、`diarization_smoother.py`；`tests/test_diarization_overlay.py`、`test_meeting_finalization.py`、`test_diarization_smoother.py`。

**接口：** overlay 内部新增 `buffer_start_sample: int`；诊断输出 span 必须加该偏移。新增可测试纯函数 `meeting_range(buffer_start: int, start: int, end: int) -> tuple[int,int]`。

- [x] 失败测试覆盖 45 分钟输入只保留最后 30 分钟：相对尾部 1 秒的结果是会议 901 秒，不能修改会议第 1 秒。

```python
def test_trimmed_overlay_has_a_meeting_offset():
    start, end = meeting_range(900 * 16000, 16000, 32000)
    assert (start, end) == (901 * 16000, 902 * 16000)
```

- [x] 写 spy 测试：默认不调用 batch；扩展开启且旧 overlay 开关 true 仍不调用；只在明确 legacy 诊断且 capture 已关闭后调用一次。
- [x] 运行 `uv run pytest tests/test_diarization_overlay.py tests/test_meeting_finalization.py -q` 确认失败。
- [x] 自动 overlay 默认 false；如保留诊断路径，用 ring/deque 替换不断拼接 bytes，维护样本起点，拒绝跨裁剪边界覆盖；实际 HTTP/domain 长度限制前置校验，失败可见。
- [x] 对短有意义插话保留 source speaker 和文字，不通过时长改人；unknown 不生成真人卡片。
- [x] 重跑针对性测试并加入 `tests/test_meeting_speaker_labels.py`。更新旧流式分人手册中“无需改动、延迟估算即保证”的表述，保留历史里程碑出处。

## S1：协议协商、固定正文和统一样本时间

**依赖：** Rail R2 golden schema/fixtures 已冻结，R1 未验收时只做 fake。

**文件：** `speechrail/transport.py`、`transcription_events.py`、`transcriber.py`、`asr/contracts.py`、`asr/models.py`、`subtitles/proxy.py`、`meeting/asr_mapping.py`；`tests/asr/test_speechrail_events.py`、`test_speechrail_realtime.py`；新增 `tests/asr/test_diarization_extension_contract.py`。

**接口：** 新 decoder 对象为 `DiarizationUpdateEvent`、`DiarizationStatusEvent`、`DiarizationFinalizedEvent`；字段按 Rail 规格逐一严格定义。新配置 `SONA_MEETING_DIARIZATION_EXTENSIONS_ENABLED` 默认 false。源时间转换纯函数 `meeting_sample(epoch_start: int, rail_sample: int) -> int`。

- [x] 从 Rail fixtures 创建可校验副本，记录 spec ID/fixture 哈希。禁止为通过测试改造生产者字段名。
- [x] 写协商失败测试：能力缺失不发 extensions；旧 Rail 无 finalized 时仍走 legacy clear barrier；未协商连接遇新事件是协议错误。
- [x] 写 canonical text 分割测试：归属单元的 code point 范围完整无重复；中文+emoji 不按 JS UTF-16 错切；同一源 UID 第二次不同内容必须冲突。

```python
def test_session_sample_is_added_exactly_once():
    assert meeting_sample(160000, 49600) == 209600

def test_unicode_units_reconstruct_the_final_text():
    text = "同意🙂。"
    ranges = [(0, 2), (2, 4)]
    assert "".join(text[start:end] for start, end in ranges) == text
```

- [x] 运行 `uv run pytest tests/asr/test_diarization_extension_contract.py tests/asr/test_speechrail_events.py -q`，确认有失败。
- [x] 按 capability→session.updated 实现协商；新模式只消费 completed units/三个登记的 extension type，不双写 legacy segment；sample range 校验通过后转换会议时间。
- [x] source epoch 重放只覆盖未持久 item 后缀；最长 30 秒；跨越已提交 watermark 记录 gap，不模糊合并正文。旧 speaker label 加 session namespace，新模式不直接拼 group+label 当持久身份。
- [x] 增加第二 commit、VAD 长静音、mute/resume、新 session 同编号、缓存溢出、WS 断线测试；运行 `uv run pytest tests/asr/test_speechrail_realtime.py -q`。

## S2：加法数据迁移、幂等归属事务与恢复

**依赖：** S1 类型稳定；迁移开发在测试库完成，运行库迁移另行执行。

**文件：** `meeting/models.py`、`ports.py`、`repository.py`、`persistence.py`、`recovery.py`；新增 `meeting/speaker_attribution.py`；在 `meeting/migrations/` 新建下一个空闲编号的 speaker attribution migration，并登记 `meeting/migrations.py`；测试 `test_meeting_repository.py`、`test_meeting_recovery.py`、新增 `tests/test_speaker_attribution.py`。

**接口：** Sona 规格 5.2 的 `append_completed_item`、`apply_speaker_patches`，以及 `CompletedItem`、`SpeakerPatchEvent`、`SpeakerPatchResult`。禁止使用现有 reconcile_window 做 speaker-only patch。

- [x] 先创建 repository 集成失败测试：插入三个 item，修改中间一个 speaker；所有文本、时间、UUID、后续 item 必须保持。重复 event 不增加 revision。

```python
async def test_speaker_patch_never_replaces_transcript_suffix(repo_case):
    before = await repo_case.seed_three_items()
    event = repo_case.patch_middle_item(revision=1)
    await repo_case.apply(event)
    once = await repo_case.snapshot()
    await repo_case.apply(event)
    twice = await repo_case.snapshot()
    assert once.text_and_time_signature == before.text_and_time_signature
    assert twice == once
```

`repo_case` 在临时 schema 上调用真实 repository，不 mock SQL；signature 包含 id、order、start/end、text，故能捕获后缀删除。

- [x] 验证 migration 从空库与旧 schema 升级，旧必填列/旧查询仍可读，唯一 source UID/event 索引生效。不得连接当前会议 schema 做此测试。
- [x] 实现锁 meeting→校验全部目标→写模型归属/保留 override→版本一次递增→事件与去重凭证同事务；同 ID 不同 payload hash 冲突，未知 UID/跳号整批失败。
- [x] 人工冲突测试：两个人都已命名时不自动合并；同名也不自动证明相同人；A↔B 交换按原快照；撤销恢复之前 key；手动 override 后自动 patch 只更新模型证据。
- [x] journal 新增三种白名单操作，测试 DB 第一步/最后一步失败、回放重复、completed 与 patch 顺序、最后 finalized 先到但落库未完成；不提前排纪要。
- [x] 执行（测试 DSN 由测试环境提供，命令不写真实连接信息）：

```bash
uv run pytest tests/test_speaker_attribution.py tests/test_meeting_repository.py tests/test_meeting_recovery.py -q
```

- [x] 核对输出，数据库用例如果 skipped 则该任务未通过；必须在专用临时 schema 上实际执行并记录断言。migration 留在仓库等待部署，不在此顺手应用运行库。

## S3：UI、封存屏障与纪要版本

**依赖：** S1/S2、Rail R4。

**文件：** `meeting/finalization.py`、`session.py`、`events.py`、`summary/service.py`、`summary/evidence_anchor.py`；`contracts/meeting-assistant/v1/`；`ui/src/contracts/meetingContract.ts`、`services/meetingApi.ts`、`hooks/useMeetingSocket.ts`、`stores/meetingStore.ts`、`components/meeting/MeetingTranscriptViewer.tsx`；对应现有测试。

**接口：** `speaker_details=1` query 协商 presenter；旧响应保留原字段集合，新响应增加 speaker_status/timing_quality/overlap/manual 信息。继续使用已有 snapshot/reconcile 通道，不默认广播新顶层事件 type 给旧 UI。

- [x] 写 UI 失败用例：partial→confirmed/unknown→tentative→stable 原位变化，UUID 不变、正文不重复；有意义短插话保留；emoji text 正确；人工状态优先。
- [x] 写 EOF 失败测试：收到 Rail finalized，但对应 patch 仍未持久化时，会议不能 completed，纪要不能 create。

```python
async def test_summary_waits_for_persisted_diarization(finalizer_case):
    await finalizer_case.receive_finalized(last_update_sequence=13)
    assert finalizer_case.minutes_created == 0
    await finalizer_case.persist_through(sequence=13)
    await finalizer_case.complete()
    assert finalizer_case.minutes_created == 1
```

`finalizer_case` 运行真实 MeetingFinalizer，注入可阻塞 repository/gateway fake；不能只断言模拟器自己的标志。

- [x] 运行 `uv run pytest tests/test_meeting_finalization.py tests/test_meeting_summary.py -q` 与前端 `npm test -- --run`，确认相应新断言先失败。
- [x] 实现规格第 6 节的整体 30 秒 deadline；新模式 commit→finalize→等待 DB watermark→clear→封存，旧模式继续 clear barrier；超时区分 ASR 中断和仅分人 degraded。
- [x] presenter 将局部变更转为符合旧 replace_from_ms 语义的完整受影响后缀；超出消息边界用 resync-required，不向旧 store 发送含义不同的稀疏后缀。UI 仅在新 query 协商成功时解析新字段。
- [x] 纪要记录 source content_revision，unknown 不猜负责人，人工改名使正在生成的旧结果 stale；自动 patch 不覆盖手动更正。
- [x] 加 schema/fixture 测试：旧请求仍符合旧 additionalProperties=false 约束，新响应包含状态且只在 opt-in 下出现。更新前端 contracts 与后端 presenter 的同一测试向量。
- [x] 跑 `uv run pytest tests/test_meeting_contracts.py tests/test_meeting_finalization.py tests/test_meeting_summary.py -q`；在 `ui/` 执行 `npm test -- --run`、`npm run build`。

## S4：联合真人验收与兼容发布

**依赖：** S0–S3、Rail R5，运行态/真人音频使用已获相应授权。

**文件：** 新增 `tests/test_diarization_e2e.py`（fake 两端协议与恢复）；新增 `docs/operations/speaker-diarization-e2e-acceptance-2026-09-06.md`（执行日日期）；更新相关手册、配置说明和已失效的旧协议叙述。

- [x] 先写两端 fake 联合测试：至少三个 ASR commit、speaker revision、重连 epoch、人工改名、一次 DB 暂时失败、一次 finalize 重试。检验最终事实与期望相同而非只看消息总数。
- [x] 新旧 Rail×新旧 Sona 四组合全部测试；不存在 extension capability 的旧服务不收到新请求，旧 Sona 不收到新 type。
- [x] 在当前项目可用的设备来源上验证 microphone/output/dual，记录实际可用性；没有相关设备/授权的场景标未验证，不编造通过。
- [x] 按 Rail 规格 7 的真人划分/DER/CER/unknown/延迟/两小时门验收；专测 31/61/91 分钟、16 分钟静音、短插话、一次断线重放与一次源故障。
- [x] 验证扩展会议 batch 请求数=0；页面、历史 API、数据库和纪要的讲话人映射一致；检查 journal 权限、积压和恢复后清理，无音频落盘。
- [x] 执行完整 gate，数据库测试必须实跑：

```bash
uv run pytest tests/
uv run mypy src/
uv run ruff check src/ tests/
git diff --check
```

前端在 `ui/` 执行：

```bash
npm test -- --run
npm run build
```

- [x] 报告含两端 commit/版本、协议 ID/fixture 哈希、参数、匿名评测集哈希、全部验收门结果、未验证项和 rollback 证据；不含真实 DSN、姓名、完整转写、音频或 embedding。见 [2026-09-06 联合验收报告](../../operations/speaker-diarization-e2e-acceptance-2026-09-06.md)。
- [x] 发布窗口中先结束当前会议，部署兼容 decoder/presenter、执行加法 schema 迁移，再确认 Rail 能力和 Sona 新开关；先受控会议通过再改默认。执行前使用当前仓库服务 SOP，不把本计划当任意服务重启授权。
- [x] 演练回退：结束会议→关闭扩展开关→新数据仍可读取→旧 Rail/legacy EOF 可工作；不能删 migration 数据、自动启用 overlay 或重新加载 Sona 本地模型。

## 完成标准

- [x] S0 legacy 风险隔离通过。
- [x] S1 协议、Unicode、时间、重连回归通过。
- [x] S2 真实临时 schema 事务与恢复通过。
- [x] S3 页面、封存、人工优先和纪要版本通过。
- [x] S4 联合质量、两小时 soak、兼容与回退通过（详见 [2026-09-06 联合验收报告](../../operations/speaker-diarization-e2e-acceptance-2026-09-06.md)）。

上述全部通过后，已将设计状态更新为 implemented，并在正文填入真实验收链接。任何未完成项必须继续保留为未完成，不能把文档落盘写成端到端能力已发布。
