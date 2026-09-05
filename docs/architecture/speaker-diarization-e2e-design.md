---
title: "Sona × SpeechRail 会议讲话人分离端到端设计"
description: "会议采集、时钟、归属修订、人工映射、持久化、封存与纪要的可执行设计"
status: under_review
type: technical_spec
category: meeting
version: "1.0.0"
date: 2026-09-05
last_updated: 2026-09-05
owners: [sona-core]
tags: [speechrail, diarization, meeting]
---

# Sona × SpeechRail 会议讲话人分离端到端设计

> 设计编号 `SPK-E2E-1`，状态为待实施。本文新增的数据字段、配置开关和事件不是当前能力；文档落盘不代表运行态操作或数据库迁移已获授权。

配套：[Sona 实施计划](../superpowers/plans/2026-09-05-speaker-diarization-e2e.md)。公共 SpeechRail 协议统一由 `SpeechRail/docs/architecture/speaker-diarization-e2e-design.md` 第 5 节定义；同级检出时打开 [SpeechRail 设计](../../../SpeechRail/docs/architecture/speaker-diarization-e2e-design.md)。实施时将其转成两仓各自的机器可读契约，禁止客户端自行补造服务端不存在的事件。

## 1. 目标与职责

产品默认覆盖中文优先、1–4 位讲话人、两小时会议。用户首先看到字幕，稍后看到讲话人归属；不确定处可见且可人工更正。重叠讲话不承诺拆分成独立录音，也不凭上下文猜测待办负责人。

Sona 只做音频采集、来源/时钟映射、会议状态、业务身份、持久化和 LLM 编排。ASR/TTS、Sortformer 和 CAM++ 继续由 SpeechRail 管理；不恢复 WhisperLiveKit、Sona 本地声纹库、跨会议身份识别或本地模型 fallback。PCM 仅在有界内存/IPC 中，数据库和 journal 不存音频/embedding。

| 方案 | 结论 |
|---|---|
| 流式正文 + 显式讲话人修订 + 会议内人工映射 | 采用：边界清晰、可局部重试、长会议状态有界 |
| 每句独立分人，直接复用同名 speaker 编号 | 不采用：跨 commit/重连会误合并身份 |
| 默认会末整场 batch overlay | 不采用：与无音频持久化、有界缓冲和 batch/stream 互斥相冲突 |

## 2. 当前基线和风险

2026-09-05 代码基线 Sona `54b34cf`、SpeechRail `eb66f86`。同日 22:05 CST 外部服务探针显示 SpeechRail 1.7.0/light、分人 readiness 为 true；本轮没有真人分人或会议数据库实测。源码与服务安装 wheel 的一致性未核验。

| 当前事实 | 依据 | 新链路处理 |
|---|---|---|
| 会议请求 diarization，strict decoder 遇未知事件报错 | `speechrail/transport.py`、`transcription_events.py` | 显式能力协商，不向 legacy 连接发送新事件 |
| EOF 使用 commit→clear acknowledgment | `speechrail/transcriber.py:finish/events` | 新模式在 clear 前等待分人 finalized 和持久化；旧模式原样保留 |
| segment 缺失时合成兜底正文，speaker key 为 0 | `speechrail/transcriber.py:_synthesized_segment` | 保留文字，但展示“讲话人未确定”，不得创建“Speaker 0 真人” |
| meeting group 中直接拼入服务 speaker label | `speechrail/transcriber.py:_speaker_key` | 新模式使用 session 级源键及稳定应用侧身份 |
| overlay 默认启用，1800 秒以上丢最旧 PCM；span 没有被丢音频 offset | `config/meeting.py`、`meeting/diarization_overlay.py` | 先隔离危险回写，增量模式禁用自动 overlay |
| `reconcile_window` 删除从窗口起点开始的历史后缀 | `meeting/repository.py` | 不用于 speaker-only patch；新增专用事务 |
| remap 逐项更新并删除旧 speaker | `meeting/repository.py:apply_speaker_remapping` | 新链路不复用该算法处理交换映射/人工冲突 |
| 平滑器可将短 A-B-A 的 B 改成 A，并合并段 | `meeting/diarization_smoother.py` | 分离事实层与阅读层，不破坏 source unit 和短插话 |

代码图 Tier 2 与关键源码核验、coverage metadata_match 支持以上正向发现，不代表所有路径无缺陷。流式接线手册关于“无需改动”“会话内编号稳定”和 commit 延迟估计，只能作为旧里程碑说明；不能作为本方案的发布验收证据。

## 3. 采集与时钟

继续使用既有 `AudioSourceRouter`/来源实现和统一模式协调，会议与助手互斥消费 PCM。支持 microphone-only、physical-output-only、dual；首期推荐双源配耳机。外放 AEC 属于单独声学验收，不通过音量门控承诺无回声，也不把系统输出重复识别成另一位真人。

新协议样本是 16 kHz mono s16le。应用累计样本再转换成毫秒，不逐包取整累加。每 source epoch 记录：

扩展会议按 Rail 回显 `max_item_duration_ms=8000` 验证上限，默认请求 server VAD（threshold=0.5、prefix_padding_ms=300、silence_duration_ms=600）；每包 20–100 ms，最大 500 ms。服务自动硬切连续讲话，Sona 不再叠加独立定时 commit 造成重复空 item。UI 将“字幕出现”“等待断句”“讲话人确认”分开呈现，不承诺每个词都在 4 秒内最终归属；完整延迟门见 Rail 规格。

```text
source_epoch: int
source_session_id: str
meeting_start_sample: int
replay_until_meeting_sample: int
last_committed_meeting_sample: int
group_generation: str | null
```

`meeting_sample = meeting_start_sample + rail_session_sample`。新协议使用服务的 `audio_start_sample/audio_end_sample`，禁止再加 VAD onset/item_offset；legacy 路径继续原算法。样本换算单位以服务回显 `sample_rate=16000` 验证。

暂停、设备切换导致实际时钟跳变、来源故障或 WebSocket 重连时，必须结束旧 source epoch，记录准确 gap 和新起点。正常静音保持源时钟，不在音频中直接删掉。mute 是否继续输入零值 PCM 由当前音频产品语义决定；若当前语义为零吞吐，则 resume 必须新 epoch，不能伪装连续录音。

### 3.1 重连重放

1. 只缓存未落库的完整 ASR item 后缀，最大 30 秒；已确认文本不重播也不重新归属。
2. 已持久化的 completed item 以 item 结尾更新 `last_committed_meeting_sample`，而不是按最后一个词的结尾更新，避免重复静音尾部。
3. 新 epoch 的 meeting_start_sample 为重放后缀真实起点。回放期间仍按原速/受控速度接受服务背压，不同时把新 PCM 插入旧序列中间。
4. replay 超过上限、旧 item 一部分已确认但源边界未知，或新结果跨越持久化 watermark：放弃该重叠部分并记录 gap，不做模糊文本去重。已有正文不能被覆盖。
5. 已落库正文但 speaker 修订未完成时，不重跑其音频；断线后保留当前归属并将未冻结归属标为 unknown/connection_lost。声学状态不可从 journal 恢复。
6. 新旧相同 `spk_01` 默认两位匿名人。只有服务显式 speaker_link、同 group_generation 且没有人工冲突，才进行身份关联。

## 4. 消费协议与事实模型

### 4.1 协商

新增配置 `SONA_MEETING_DIARIZATION_EXTENSIONS_ENABLED=false`，只影响后续新会议。先读取 `session.created.capabilities`；包含 `speechrail.diarization.v1` 且开关 true 时，才请求相应 extensions。校验 session.updated 中版本、sample_rate、timebase 和 group_generation。

无能力时进入明确标注的 legacy 模式，不发送试探扩展，不自动启用 batch overlay。profile 本身缺失时会议 prepare 失败；用户可显式选择“仅转写”。会议中分人故障则继续保存文字，页面显示“分人不可用”，不把服务 error 当成一个新 speaker。

### 4.2 两类事实独立

`completed.transcript` 是正文事实。`attribution_units` 是对该字符串的完整 code point 范围划分（Python slice 语义）；浏览器 JS UTF-16 不能直接按此索引切字符串，后端应交付已切好的 text。

Sona 使用 `UUIDv5(meeting_id, source_session_id + ":" + segment_uid)` 生成稳定 segment UUID。新的 completed 重送只幂等确认；同源 ID 不同正文/时间/分割为协议冲突，保留原事实并中断该 epoch。新链路不再接收旧 `.segment` 双写。

新增归属领域对象：

```python
@dataclass(frozen=True)
class SpeakerPatch:
    source_session_id: str
    segment_uid: str
    revision: int
    status: Literal["unknown", "tentative", "stable"]
    source_speaker: str | None
    coverage_ratio: float
    overlap_ratio: float
    candidates: tuple[tuple[str, float], ...]
```

这是计划新增类型，不导入 SpeechRail 代码。source 字段是声学来源；应用侧 `speaker_key` 为会议内不透明 UUID 字符串，由持久化层生成；UI 不解析 key。legacy `speaker:0` 历史记录按 unknown 呈现，但不强制重写历史库。

文本 confirmed 可以伴随 speaker tentative/unknown。算法 stable 不等于人工确认。`timing_quality=unavailable` 表示只有 item 粗时间范围，不能以词级精度驱动跳转或计算高精度说话时长。

### 4.3 新事件的严格处理

新模式只新增三个 type：`speechrail.diarization.update`、`speechrail.diarization.status`、`speechrail.diarization.finalized`。字段限制以 Rail 规格为准；未知其他 type 仍拒绝，不能用“忽略所有未知事件”隐藏拼写和协议错误。

- update 只能引用本连接已落库 completed 的单位；同 revision 同内容重复忽略，同 revision 不同内容报冲突，revision 倒退忽略。
- 同连接每 segment revision 必须连续；跳号/更新未知 UID 视为协议错误，停止分人、保留正文。WebSocket 正常保证顺序，因此不实现服务端事件无限补拉。
- 客户端 DB 写入暂时失败时，接收队列有界，按原顺序 journal；未持久化的 completed 在修订之前回放。
- stable watermark 单调；超过 watermark 的单元不再接受自动归属改写。人工更正不受声学 watermark 限制。
- status degraded 后所有新 unit 为 unknown，页面提示一次；不无限重连试图掩盖持续模型错误。

## 5. 持久化与人工身份

### 5.1 加法迁移

在本项目配置的 schema 内通过版本化 migration 执行；编号取实施时下一个空闲序号，不硬编码历史数据库名称/用户或复用其他项目 schema。测试使用专用临时 schema。无需 pgvector/AGE，无声纹落库。

| 对象 | 拟新增内容 |
|---|---|
| `transcript_segments` | nullable `source_session_id`/`source_segment_uid`/`source_item_id`；`speaker_revision` 默认 0；`model_speaker_key` nullable；`speaker_override_key` nullable；`speaker_status` 默认 unknown；`speaker_frozen` 默认 false；`timing_quality` 默认 unavailable；`coverage_ratio/overlap_ratio` 默认 0；`speaker_candidates` JSONB 默认空数组（最多 4 项） |
| `meetings` | `diarization_status`（legacy/active/complete/degraded）、`diarization_reason` nullable |
| 新表 `meeting_transcription_sources` | meeting_id、source_epoch、session_id、meeting_start_sample、last_committed_meeting_sample、stable_through_sample、last_update_sequence、group_generation；每会议 session 唯一，stable_through_sample 保持 Rail session 时间域 |
| 新表 `meeting_speaker_sources` | meeting_id、session_id、source_speaker、group_generation、application_speaker_key；源标签唯一 |
| 新表 `meeting_source_events` | meeting_id、session_id、event_id、canonical_payload_hash；唯一组合用于去重，不存音频/embedding/原始消息 |

为有 source UID 的 segment 建 `(meeting_id,source_session_id,source_segment_uid)` 唯一索引；旧行 nullable，保持旧查询可用。未知显示使用保留 key `unknown`，不得出现在实名候选列表；它不是 UUID 身份。模型主 speaker 有值时解析到应用 UUID；对 legacy 字段的兼容输出由 presenter 负责。

新增所有 SQL 通过 schema-safe 的现有 repository 连接与参数化机制，不把 scheme/表名当未校验用户输入。旧程序回退仍能读取旧必填列；新增列/表不做 destructive down migration。

### 5.2 专用事务，不重用历史后缀替换

新增 port：`append_completed_item(meeting_id, item: CompletedItem) -> TranscriptReconcileResult` 与 `apply_speaker_patches(meeting_id, source_event: SpeakerPatchEvent) -> SpeakerPatchResult`，由 repository 实现。

`CompletedItem` 包含 session/item ID、顶层 event_id/sequence、session sample 区间、canonical text、immutable units 和 source epoch。`SpeakerPatchEvent` 包含 session/event ID/sequence、group_generation、stable_through_sample、patches 和 links。`SpeakerPatchResult` 包含 changed_segment_ids、transcript_revision、content_revision、diarization_status。各类型放 `meeting/models.py` 或新增 `meeting/speaker_attribution.py`，transport 类型先经显式转换，不能把未校验 dict 送入 repository。

speaker-only 事务步骤：

1. 锁 meeting 行；只接受 RECORDING/FINALIZING（自动修订），验证 source session 属于该 meeting。
2. 检查 source event 唯一键和内容哈希；相同 event 重放不增加版本，不新增事件。
3. 按 source UID 定位全部目标，验证 revision、不可变字段以及 watermark；任何非法目标整批拒绝，不半批写入。
4. 更新模型归属、revision、status、coverage/overlap 和候选；以 source watermark 更新 `speaker_frozen`，并在同事务推进 source 的 last_update_sequence。没有人工 override 时更新既有 `speaker_key`；有 override 保留用户结果，模型值仅供可追溯查看。
5. 仅当有效结果变化时递增 meeting 的 transcript_revision 和 content_revision，写既有 meeting_events，并提交去重凭证；一次事务一次版本。
6. 对外广播完整一致的结果；失败则全部回滚。禁止 DELETE transcript 后缀、禁止改 text/start/end/id，也不全量重写会议。

### 5.3 名称与身份冲突

rename 是 display_name 修改，不证明声音身份，也不将所有词标为 manual。对某段人工改归属则写 speaker_override_key；“恢复自动”显式清空 override，重新使用最新 model 值。

跨 session link 到两个已命名且名称不同的应用身份时，不自动合并、不删除任一身份，记录 conflict 供人工处理。同样，存在相互矛盾的 link 时不做传递闭包。只有一端未命名时可在事务中关联其 source key 到已存在应用身份；已经存在人工 override 的段始终保持。

显式人工合并支持撤销：源身份标记 alias，不删除；记录操作 ID、受影响 segment ID 和原 key。交换 A↔B 的手动操作根据原始快照一次计算再写入，不能用循环 UPDATE 导致 A/B 都变成一个值。本期不承诺分人结束后自动重新聚类所有历史段。

### 5.4 恢复 journal

复用 `meeting/recovery.py`，新增白名单操作 `append_completed_item`、`apply_speaker_patches`、`finalize_diarization`。journal 只存恢复必需的文本/匿名归属和幂等键，延续目录 0700、文件 0600；不记录私有底牌、PCM、embedding、完整服务 raw event。

DB 恢复后严格按 completed→patch→finalize 回放，利用同一唯一键幂等。journal 有待处理内容时，会议不能标记为“已完整保存”，纪要排队暂停。容量沿用现有有界错误策略，达到限制时停止采集并报告保存失败，不能吞异常后宣称 completed。

## 6. UI 与会议封存

### 6.1 展示状态

| 状态 | 页面文案/行为 |
|---|---|
| partial text | 临时字幕，可变文本，不进入纪要 |
| confirmed + unknown | “讲话人未确定”；保留全文 |
| confirmed + tentative | “发言人 1 · 待确认”，收到 patch 原位更新 |
| confirmed + stable | “发言人 1”或用户名称；不用百分比冒充身份概率 |
| 手动更正 | 显示人工标识；后续自动 patch 不覆盖 |
| overlap | “重叠讲话”，允许展示候选，不强制一人 |
| degraded | “分人不可用，文字仍在记录”；影响区间可查 |

阅读层可合并相邻同人文字，但底层 immutable units 不合并、不改 UUID。虚拟列表 key 用 segment UUID。短词不因 350/500 ms 规则改人；speaker-only 更新不触发正文滚动重置。粗时间单元不显示伪精确词级播放定位。

后端/前端契约本期继续使用既有 `transcript_reconciled` 或 snapshot 通道传递**完整受影响后缀**，复用现有 replace_from_ms 语义；这只是 presenter 输出，不是数据库后缀删除。新增归属字段在服务器与 UI 的 schema/fixture 中同步定义；旧客户端通过现有 legacy presenter 看到原字段集合。新 UI 请求 `speaker_details=1` 的 query 参数后才接收新字段，服务不支持该参数时新 UI回到 legacy 呈现。

后端每批最多 256 个声学修订，但输出后缀可能较长；超过现有响应/队列边界时发送既有 resync-required，客户端分页重取 snapshot，不发送无限大 WS 消息。新字段不默认注入旧版本 additionalProperties=false 的 JSON schema。

### 6.2 停止会议状态机

```mermaid
stateDiagram-v2
    RECORDING --> FINALIZING: 停止新采集并提交最后 PCM
    FINALIZING --> DRAINING: ASR completed 已持久化
    DRAINING --> SAVING: 分人 finalized 或明确超时
    SAVING --> COMPLETED: 无缺口且归属正常封存
    SAVING --> DEGRADED: 分人失败但文字已保存
    SAVING --> INTERRUPTED: 音频缺口或 ASR 尾部失败
```

DRAINING/SAVING/DEGRADED 是 UI/应用内部阶段，不擅自增加旧 MeetingStatus 枚举。持久状态继续 COMPLETED/INTERRUPTED，分人单独用 diarization_status 区分。仅分人失败可 `MeetingStatus.COMPLETED + diarization_status=degraded`；ASR 尾部超时使用现有 interrupted/finalization_timeout。

扩展模式顺序：停止采集→commit→所有 completed 落库→发送分人 finalize→消费直至 last_update_sequence 对应更新均持久化→保存 complete/degraded→clear/关闭→封存会议→创建纪要任务。总体等待上限 30 秒，不能每个步骤单独重新获得 30 秒。

legacy 模式继续 commit→clear acknowledgment，不等待不存在的 session.completed。新模式 timeout 不伪造 finalized；保留已落库正文并记录原因。自动 overlay 全程关闭，不能用 batch 推理掩盖分人终态失败。

### 6.3 纪要和内心 OS

纪要读取固定的 transcript_revision/content_revision；提炼过程附 segment UUID 证据引用。只有 confirmed text 进入最终纪要；unknown speaker 可以总结内容，但负责人保持“待确认”，不得从称谓或第一人称推断实名。重叠且无法区分的承诺不得确定归给单人。

生成期间人工命名/归属改变会递增 content_revision。返回时若 source revision 过期，结果标为 stale，用户看到更新提示；不得将旧姓名摘要覆盖当前版本。内心 OS 可以读取临时信息，但必须携带不确定状态且保持既有会后即焚边界。本期沿用当前 LLM provider/model，不修改 LM Studio 配置。

## 7. Overlay 收敛和迁移

S0 先将自动 overlay 默认关闭，并阻断对已可靠分人的词盲目覆盖。保留旧开关只用于明确选择的 legacy 诊断：`diarization_overlay_enabled=true` 且无新扩展时才允许会末单次调用；streaming lease 必须先释放。

诊断模式若保留：buffer 改为样本索引 ring，维护起点；输出 span 加 start_sample offset，只允许修改被缓冲音频完整覆盖的 segment，跨裁剪边界段不改。必须先测试所有实际请求层限制，不能以 1800 秒缓冲参数推断服务能接收。裁剪、超限、batch 失败均 visible，不能 silent return [] 后显示分人完成；其 batch labels 使用独立 source_session_id，不与实时同名 label 自动合并。

扩展启用后，即使旧 overlay 开关残留 true 也不调用 batch。此项必须有 spy 测试。一个发布周期内保留诊断回退；删除诊断实现是独立清理任务，不属于本次方案必须范围。

## 8. 联合验收与发布

质量数据与目标统一采用 Rail 规格第 7 节；Sona 不维护第二套冲突阈值。额外硬门：

| 用例 | 必须成立 |
|---|---|
| 第二个 commit、静音后恢复 | 时间不回零、不双加 offset、不覆盖历史 |
| 45 分钟 legacy overlay 诊断 | 最后 30 分钟只影响对应区间，前 15 分钟完全不变 |
| replay/重复 event | 正文无重复、UUID 稳定、版本只加一次 |
| 迟到 speaker patch | 正文、时间、人工 override 保持，局部归属正确变化 |
| 同 source ID 内容变化/版本跳号 | 可见协议失败，不 silent overwrite |
| A/B 相似音色、A-B-A 短插话 | 不单凭时长抹掉真实插话 |
| 人工名称冲突与 A↔B 交换 | 不合并两个人，不丢姓名与原始身份 |
| DB 故障与 journal 回放 | 单次逻辑提交，文本和修订顺序正确，封存不提前 |
| finalize 丢失、延迟或 native timeout | 30 秒内可见降级/中断，不假 completed |
| 旧/新 Rail × 旧/新 Sona | 四组合明确结果，旧 decoder 不收到新事件 |
| 扩展会议 | batch transcriber 调用次数恒为 0 |
| 摘要过程中人工纠错 | 旧 revision 的纪要标 stale，负责人不被错误归属 |

实施顺序：S0 风险隔离→S1 协议适配（开关关）→S2 加法数据迁移/幂等事务→S3 UI/EOF/纪要→S4 联合验收。依赖 Rail R2/R3 的机器契约，不能靠 mock 猜未实现接口。

发布：先部署兼容双方的新 decoder/presenter 与加法 schema，再启用新能力；受控非敏感会议通过后改默认。每个运行态步骤遵循项目流程，本文不执行这些操作。回退时先结束录音→关闭扩展开关→保留新数据列及 journal→恢复旧兼容行为；不删除数据，不自动恢复批量 overlay，不把未知改成默认某个人。若要降级至旧 Sona 二进制，必须先验证其读取加法 schema 的兼容性。

## 9. 本次文档交付边界

本轮仅新增设计/实施计划和导航，并给旧对接手册增加基线更正说明。代码、数据库、模型、运行配置均未修改。源码风险还需失败测试和真实会议验收；文档中的性能门与阈值均为目标。实施计划中的文件若执行时已发生其他任务改动，先按符号复核、只改目标范围，不覆盖未知修改。
