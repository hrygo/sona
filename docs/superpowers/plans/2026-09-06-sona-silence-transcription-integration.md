# Sona 字幕与会议无声误转录接入实施方案

> 执行约定：按本计划逐项实施，每项完成测试与审查后再推进；执行阶段使用可用的 executing-plans 工作流。未经用户明确要求，不启动子代理。
>
> 状态：planned。2026-09-06 已完成源码与契约可行性审查；尚未实施，尚未完成真实联合验收。
>
> 跟踪 issue：[Sona #8](https://github.com/hrygo/sona/issues/8)。上游依赖：[SpeechRail #10](https://github.com/hrygo/SpeechRail/issues/10)。

**Goal:** 接入 SpeechRail 的无声准入治理，保证字幕和会议不会因空协议事件产生文本，同时保留真实短答、重连历史、不可变正文、分人水位与结束闭环。

**Architecture:** Sona 继续使用现有 SpeechRailStreamingTranscriber，不新增字幕/会议本地 VAD 或文本黑名单。消费端先建立协议回放、诊断与行为回归；仅在测试证明当前实现违约时作最小修复。声学治理由 SpeechRail 配套计划交付。

**Tech Stack:** Python 3.12、uv、pytest、现有 SpeechRail Realtime adapter、PostgreSQL 临时测试 schema、React/TypeScript/Vitest。

**Spec:** 本文为此次接入规格；同时遵守 `docs/architecture/speaker-diarization-e2e-design.md`、`docs/manuals/会议助手后端运行与前后端联调.md`、`AGENTS.md`。上游规格：SpeechRail `docs/superpowers/plans/2026-09-06-speechrail-silence-transcription-admission.md`。

## 全局约束

- 当前任务仅创建方案和跟踪 issue，不实施产品代码、不启动测试会议、不重启或部署服务。
- 执行前记录 `git status --short` 和 `git log -5 --oneline`，不得覆盖已有声音工坊等并行改动。
- 不安装、不下载、不启动 ASR/TTS/diarization 模型；字幕和会议不另行加载 Silero，不复用 Pipecat 状态对象。
- AudioHub 单源采集和模式互斥不变；原始 PCM 不因 UI 短词策略被过滤、重采样或持久化。
- 已确认正文、时间与 ID 不可变；分人原位修订及人工更正优先规则不变。
- PostgreSQL 为会议事实源，恢复 journal 仅按原职责记录 confirmed/patch；不得增加会议音频或调试转录日志。
- 会议不写字幕 SRT；普通字幕维持已有归档行为，空完成不新增 SRT 条目。
- 不新增 `no_speech`、confidence、vad_profile 等未经上游契约支持的字段；缺失值不得默认成低质量。
- 不按“嗯／对／好”或片段长度拒收真实转录，不按文字相同合并数据库事实。
- 不调整现有会议 900/1000 ms、字幕 400/600 ms 窗口作为本计划首期默认；调整必须另有联合评测证据。
- 新建测试数据库对象只能位于独立临时 schema；测试清理目标必须是该 schema，严禁清理生产 schema。
- 本计划不要求修改 UI 业务布局、数据库 schema、交互助手 echo 防线或模型所有权。

## 现状与证据边界

核验日期：2026-09-06；本地 HEAD 为 `f8b158f`。前序 `1da6fa8` 调整会议端点，`f8b158f` 折叠重复语气词展示；两者均不是声学无声治理完成证据。

| 路径/符号 | 当前事实 |
|---|---|
| `src/sona/speechrail/transcriber.py:SpeechRailStreamingTranscriber` | 字幕/会议共用转录 adapter，按 purpose 选择端点参数 |
| 同文件 `finish` | legacy commit→clear ack；扩展 commit→finalize→等 finalized，持久化后释放 clear |
| `src/sona/asr/models.py` | 没有 no_speech、ASR confidence 或有效发声时长字段 |
| `tests/asr/test_speechrail_realtime.py` | 已覆盖空 completed、clear ack 竞态、重连与模式参数；应扩展而非重复实现 |
| `tests/test_subtitle_components.py` | 已覆盖同文字不同时间、历史累积、重连、SRT 生命周期 |
| `docs/superpowers/plans/2026-09-06-meeting-assistant-transcript-fragmentation.md` | 历史碎片化/展示方案；不改写为本次无声治理的验收结果 |

源码证明需要客户端协议回归；是否必须修改每个生产文件应由新测试决定。当前没有真实语音回放结果可证明“所有嗯都来自 VAD 误启动”。

## 两项目接口合同 SR-SILENCE-1

以下与 SpeechRail 计划一致；未完成上游验收前仅为待交付行为：

1. server_vad 未接纳语音，不发送非空 delta/segment/completed；客户端不需要猜测无声质量。
2. 显式 commit 即使无声也完成 committed/created/空 completed；空正文不生成会议或字幕正文条目，但生命周期事件继续处理。
3. legacy 保持旧事件集合；协商扩展才处理新归属单元与水位，扩展不混入 legacy segment。
4. 上游连续采样时钟包含静音，ASR 区间映射回 session samples；Sona 再按 epoch offset 换算一次，禁止二次偏移。
5. legacy EOF commit→clear/cleared；扩展 EOF commit→finalize/finalized→归属持久化→clear。空会议也必须完成。
6. manual/null 的交互助手回合语义不变；Sona 不增加第二个 server VAD 裁决者。
7. 首期没有新增公共质量或 profile 字段；上游引擎版本与配置记录在联合验收报告，不由客户端猜测。

## 文件与职责

| 类型 | 路径 | 职责 |
|---|---|---|
| 修改测试 | `tests/asr/test_speechrail_realtime.py` | 复用 FakeConnection、envelope helper，新增空流、短答、旧事件与时钟回归 |
| 新建 | `tests/asr/test_silence_contract.py` | SR-SILENCE-1 参数化回放，人工文本/事件，不包含音频 |
| 修改测试 | `tests/test_subtitle_components.py` | 无声快照、同词不同时间、SRT 与重连历史 |
| 修改测试 | `tests/test_meeting_finalization.py` | 空会议、正常尾句、finalize/degraded/超时、重复结束 |
| 新建 | `tests/test_meeting_silence_contract.py` | completed/patch 人工 fake 与临时 schema 验收 |
| 按失败证据修改 | `src/sona/speechrail/transcriber.py`、`transcription_events.py` | 空终态、事件身份、时钟与重连；不引入文本拒收 |
| 按失败证据修改 | `src/sona/subtitles/sessions.py`、`src/sona/meeting/session.py`、`src/sona/meeting/finalization.py` | 业务消费与结束顺序的最小修复 |
| 新建 | `src/sona/asr/diagnostics.py` | 无文本的会话内有界计数摘要 |
| 新建 | `tests/asr/test_diagnostics.py` | 计数、重置、脱敏断言 |
| 修改测试 | `ui/src/components/meeting/transcriptViewModel.test.ts`、`ui/src/components/meeting/MeetingComponents.test.tsx` | 真实短答仍可追溯，空流不增加卡片 |
| 新建 | `docs/operations/silence-transcription-integration-acceptance.md` | 联合验收与回退报告 |

上述“按失败证据修改”文件若测试已通过，交付仅增加回归测试，不为完成清单制造代码改动。

## S0：准备协议回放与诊断

**输入：**现有 FakeConnection 与 ASREvent/ASRWindow。

**输出：**确定性、无音频的协议回放与诊断摘要，不依赖上游实现完工。

- [ ] 复用 `tests/asr/test_speechrail_realtime.py` 的 `_session_events`、`_envelope` 和 fake socket 模式，为新测试建立最小 fake；避免把该测试文件作为生产依赖。
- [ ] 回放集包含：纯空终态、真实单字短答、重复 event_id、相同文本不同 item/time、旧 session 晚到事件、重连后空完成、finalized 延迟、degraded、缺失 finalized。
- [ ] fixture 使用人工短词和匿名 speaker；扩展 fixture 带合法 item/segment_uid、sequence、采样范围与 attribution_units，不虚构 confidence。
- [ ] diagnostics 使用下面的固定字段，只累积元数据。单个连接结束时输出一条调试摘要或暴露给现有诊断入口，不每帧写日志。

```python
from dataclasses import dataclass

@dataclass
class ASRDiagnostics:
    sent_samples: int = 0
    partial_events: int = 0
    empty_completed: int = 0
    nonempty_completed: int = 0
    committed_events: int = 0
    reconnects: int = 0
    protocol_errors: int = 0
```

- [ ] 会话内现有 event/session/item ID 用于回放与关联，不保存正文，也不作为高基数指标标签。上游诊断缺失不阻断转录。
- [ ] 添加 diagnostics 序列化白名单测试，断言没有 transcript/text/audio/prompt/姓名字段；只记录计数、epoch 和耗时，结束释放内存。

```bash
uv run pytest tests/asr/test_diagnostics.py tests/asr/test_silence_contract.py -q --no-cov
```

**通过条件：**无需麦克风/真实模型/生产数据库即可复现协议边界；诊断不能被用于自动拒收文字。

## S1：验证空结果与真实短答

**输入：**SR-SILENCE-1 回放；现有 decoder/transcriber。

**输出：**所有模式一致的空正文与真实正文消费规则。

- [ ] `test_empty_completed_produces_no_new_body`：空 completed 不合成新 segment，不删除先前 confirmed 历史；协议 reader 保持工作。
- [ ] `test_empty_commit_does_not_leave_stale_partial`：当前 item 空结束后清除其 partial；不清空旧 confirmed。
- [ ] `test_real_short_answers_are_preserved`：参数“嗯”“对”“好”“嗯，我同意”，每个合法 completed 原文保留；字符数和 filler 类别不影响准入。
- [ ] `test_same_text_in_distinct_turns_is_not_deduplicated`：相同短词、不同合法事件与时间应成为不同事实。legacy 可能复用 item_id，不能单凭 item_id 或文字去重。
- [ ] `test_old_session_event_cannot_pollute_new_epoch`：重连后旧事件不进入新窗口；新 session 时间加 epoch offset 一次。
- [ ] `test_server_profiles_and_manual_path_stay_compatible`：会议/字幕既有参数不变；交互路径仍由原有客户端 VAD/manual 提交控制。
- [ ] 若失败，分别在 decoder 身份校验、transcriber 空窗口组装或生命周期状态处最小修改；禁止添加通用 TranscriptAdmissionPolicy 文本黑名单。

关键行为示例（使用当前 decoder）：

```python
import pytest
from sona.speechrail.transcription_events import (
    TranscriptionCompleted,
    decode_transcription_event,
)


@pytest.mark.parametrize("text", ["嗯", "对", "好", "嗯，我同意"])
def test_decoder_preserves_short_answer(text):
    event = {
        "type": "conversation.item.input_audio_transcription.completed",
        "event_id": "evt-test", "session_id": "sess-test",
        "sequence": 5, "item_id": "item-test",
        "content_index": 0, "transcript": text,
    }
    decoded = decode_transcription_event(event)
    assert isinstance(decoded, TranscriptionCompleted)
    assert decoded.transcript == text
```

已核验当前 TranscriptionCompleted 的正文字段为 transcript。此例仅是 decoder 单元用例，不能替代端到端入库断言。

```bash
uv run pytest tests/asr/test_speechrail_realtime.py tests/asr/test_silence_contract.py -q --no-cov
```

**通过条件：**空事件无新正文，真实短答零规则误删，既有历史与事件隔离保持。

## S2：字幕、会议持久化与结束屏障

**输入：**S1 合法窗口/终态；fake repository 或独立临时 schema。

**输出：**普通字幕、会议、分人各自状态与数据守恒证据。

- [ ] 普通字幕持续空流不生成正文行、不新增 SRT 条目；上游真实短答能显示且正常归档。
- [ ] 会议纯静音期间 completed 正文追加次数为 0；结束时仍能封存会议元数据，不能将其视为连接故障或挂起。
- [ ] 真实短答写入 immutable 正文一次；随后同词不同时间写入另一条；使用真实 repository 的隔离 schema 验证，不只断言 mock 次数。
- [ ] legacy 结束等待 clear ack 并保留结束前最后一条正文；覆盖 ack 早于 send 返回、重复 finish、迟到空 completed。
- [ ] 扩展模式等待 finalized 与持久化水位，然后 clear；空会议、只有静音尾部、degraded 与超时分别有测试。没有协商扩展时不启用分人屏障。
- [ ] 分人 patch 不改正文或时间，不覆盖 manually_corrected；空正文不制造虚假 attribution unit，但水位按合法 finalized 推进。
- [ ] 重连期间 PCM 活跃快照不被“静音过滤”裁剪；旧历史保留，新 session/epoch 隔离。不得引入按相同文字去重以掩盖重放问题。
- [ ] UI 空流卡片数不增长；真实短答即使阅读视图折叠，原始 segment_ids 与时序视图仍完整。
- [ ] 结束失败保持现有 typed error 与 last window 语义，不通过吞掉异常伪装成功。

```bash
uv run pytest tests/test_subtitle_components.py tests/test_meeting_finalization.py tests/test_meeting_silence_contract.py -q --no-cov
npm --prefix ui test -- --run src/components/meeting/transcriptViewModel.test.ts src/components/meeting/MeetingComponents.test.tsx
```

**通过条件：**正文、时间、人工归属、journal 与 EOF 均守恒，空流协议成功不等于新增正文。

## S3：与 SpeechRail 联合验收

**依赖：**SpeechRail R2 完成、R3 候选模型及独立声学评测有记录；S0–S2 可提前完成。

**输出：**消费端真实验收报告，避免将 fake 测试解释为声学治理已完成。

- [ ] 在明确允许的无活动会议窗口测试；记录两个项目代码 SHA、实际服务制品版本、上游 VAD 引擎与参数、是否协商分人扩展。
- [ ] 使用同一批非敏感仓库外样本，分别驱动字幕与会议的公开消费入口；禁止读取或录制生产会议作回放集。
- [ ] 依次验证数字静音 10 分钟、非语音环境噪声 10 分钟、100 条标注中文短答/轻声/正常句、30 分钟混合会话；样本复用 SpeechRail R3 验收集，冻结参数后评分。
- [ ] 静音/非语音场景要求可见 partial、确认字幕、会议正文新增均为 0；真实短答集无整句丢失。电视/扬声器真人语音不属于静音门槛。
- [ ] 报告相对基线的语句检出率下降 ≤1 个百分点、CER 恶化 ≤1 个百分点、相同端点设置下 p95 首字/确认延迟增量分别 ≤200 ms；这些为提议门槛，未实测不得填“通过”。
- [ ] 在讲话中结束、长静音后发言、跨 8 秒连续讲话、重连和重复停止场景确认无首尾字遗漏、无双写、无时间回退和无无谓屏障超时。
- [ ] manual 交互模式完成真实短答与正常回声防线回归；不强制使用字幕/会议引擎。
- [ ] 仅保存聚合数量、耗时、匿名场景编号、配置与失败类别，拒绝在 issue/报告上传原始音频与真实会议正文。

## S4：门禁、文档与回退

- [ ] 后端完整测试必须启用独立临时 schema 的数据库测试；执行前确认 conftest 的 schema 创建/清理路径，DSN 通过环境提供：

```bash
: "${SONA_TEST_DATABASE_URL:?请先配置隔离测试数据库连接}"
uv run pytest tests/
uv run mypy src/
uv run ruff check src/ tests/
npm --prefix ui test -- --run
npm --prefix ui run build
git diff --check
```

- [ ] 不以历史固定测试数量作为门禁；覆盖率满足项目配置，测试不得因未配数据库悄悄跳过关键验收。
- [ ] 更新运行手册，注明静音无文字属于正常运行，空 EOF 仍结束成功，manual 与 server_vad 的所有权不同。
- [ ] 补充联合报告并将两项目 issue 互链；只有 S3 完成后才能关闭“声学问题已解决”的跟踪。
- [ ] Sona 首期保持 wire payload 不变，可以连接旧 SpeechRail；旧服务仍可能产生噪声文本，不能靠客户端隐藏来宣布修复。
- [ ] 需要回退上游时按 SpeechRail 已审查服务流程恢复 legacy/上一制品；Sona 测试与诊断可保留。若客户端确有业务修改，按其独立提交回退，不回写或删除历史正文。
- [ ] 当前历史噪声条目不自动清理；历史数据治理需用户另行明确范围。

## 完成定义与可执行性结论

- [ ] S0–S2 可在 fake backend 下独立执行，S3 明确依赖上游 R2/R3，S4 记录完整门禁。
- [ ] 客户端不依赖不存在的质量字段，不实现第二套声学准入，不误删真实“嗯”。
- [ ] 两类 EOF、空会议、分人修订、人工更正、重连与长发言都经过验证。
- [ ] 方案与 issue 创建不代表产品修复；真实声学效果和性能仍需 S3 实测。

## 编写阶段验证记录

2026-09-06 已核验计划引用的现有文件与 TranscriptionCompleted.transcript。实际执行现有 adapter 的空 completed、旧空 EOF 冲刷、短词回退及字幕参数相关定向测试，共 5 项通过；文中 decoder 示例的 4 个短答全部原样保留。文档空白检查通过。这些结果仅验证既有基线与示例，不代表上游治理、新增集成测试或真实声学验收已完成。

## 依据

- [OpenAI Realtime VAD](https://developers.openai.com/api/docs/guides/realtime-vad)：语音活动与端点行为背景；Sona 实际支持以 SpeechRail 已实现契约为准。
- SpeechRail 配套计划中的 SR-SILENCE-1 是本项目消费端联合验收合同；新质量协议不在首期范围。
