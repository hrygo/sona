---
title: "Sona 接入 SpeechRail OpenAI Realtime 讲话人分离（会议与实时字幕）"
status: ready
type: execution_plan
category: meeting
version: "1.0.0"
date: 2026-09-08
owners: [sona-core]
tags: [speechrail, openai, realtime, diarization, subtitles, migration]
---

# Sona 接入 SpeechRail OpenAI Realtime 讲话人分离（会议与实时字幕） Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Sona 的会议与实时字幕模式以 OpenAI Realtime 为主协议，按各自显式开关使用唯一的 SpeechRail opt-in 接收匿名、会话内的讲话人归属；未开启分人的调用不感知任何分人扩展。

**Architecture:** 固定正文继续来自 OpenAI 的 `conversation.item.input_audio_transcription.completed`。所有会话先完成原有的标准 OpenAI ASR `session.update`；`SONA_MEETING_DIARIZATION_ENABLED` 或 `SONA_SUBTITLE_DIARIZATION_ENABLED` 为真时，才在首个 PCM 前发送唯一一次 `session.speechrail.diarization.enabled=true` opt-in。会议把 `updated` 落为 speaker-only 事务并在 EOF 用 `finish/done` 建立持久化屏障；实时字幕把同一 patch 原位投影到内存字幕/SRT，并在停止时排空 `finish/done` 尾部。Sona 不加载声学模型、不维护声纹或跨会议身份；它仅呈现 A–D 等匿名标签，并把会议人工改名/更正作为本应用的优先事实。

**Tech Stack:** Python 3.12、OpenAI-compatible Realtime WebSocket、现有 `websockets` transport、Pydantic、PostgreSQL、pytest、Ruff、mypy；不引入 SpeechRail 专用 SDK。

**Authoritative upstream contract:** SpeechRail 当前 checkout 的 `contracts/realtime-openai.md` “Diarization 扩展”、`docs/users/api-contract.md` 与 `src/speechrail/compatibility/openai_realtime.py` 的实际渲染 payload。实现前必须以三者的当前内容重核字段；不得以 Sona 现有 `speechrail.diarization.v1` 文档、fixture 或代码推断新协议。2026-09-08 核查时 `contracts/diarization/v1/` 中仍保留旧 `update/finalized` schema/fixture 名称，不能作为新 wire literal 的依据；由 SpeechRail 在其契约变更中原子更新后，Sona 才可将新 schema fixture 镜像到自己的测试。

## Global Constraints

- Python 严格使用 `>=3.12,<3.13` 与 `uv`；不增加 ASR、diarization、声纹或模型下载依赖。
- SpeechRail 独占模型、PCM 的声学处理和 runtime 生命周期。Sona 不发送远程音频 URL，不持久化 PCM、embedding 或原始音频。
- 唯一 Realtime 分人开关是 `session.speechrail.diarization.enabled: true`。所有会话先确认不含 `speechrail` 的标准 ASR `session.update`；仅已开启的会议或字幕会话在首个 PCM 前追加一次该 opt-in，成功标准是 `session.updated.session.speechrail.diarization == {"enabled": true, "version": 1, "max_speakers": 4}`。
- 不支持、不保留、不降级到 `speechrail.diarization.v1`、`input_audio_transcription.diarization`、`speaker_count_hint`、`group_id`、`diarization.update` 或 `diarization.finalized`。旧字段/事件必须成为确定的协议错误，不能双读、双写或隐式回退；旧 batch overlay 及其音频缓冲实现一并删除。
- 未开启讲话人分离的字幕、助手和一般 ASR 会话继续按原 OpenAI SDK/Realtime 调用；它们不发送 `speechrail` 字段，也不接收或存储 `speechrail.diarization.*` 事件。opt-in 返回 `diarization_not_available` 或 `unsupported_operation` 时，会议保留已确认的标准 ASR text、显式记为 `degraded`；字幕保留其已确认的 text/SRT、公开 `degraded` 状态；两者均不尝试旧协议、batch 或第二个分人实现。
- 标签只在一个 WebSocket session 内匿名有效。Sona 可将匿名标签映射为会议内显示名；不得把 A–D、`speaker_links` 或候选声学标签当作真人身份，也不得跨会议自动合并。
- `completed` 的正文、时间、`item_id` 和 `attribution_units` 是不可变事实。自动 speaker patch 不能改写正文、重排后缀、覆盖人工更正或创建真人资料。
- 事件、日志、fixture 和验收报告不得记录 API key、Base64、PCM、完整转写、姓名、reference audio 或 embedding。
- 本卡只迁移 Sona 接线。SpeechRail 的模型质量、长音频、尾部和资源 AC 由 SpeechRail 的验收矩阵负责；它们不阻塞本地 fake 协议测试。

## ROI 与交付边界

此迁移的 ROI 为高：Sona 当前实现消费一套已被 SpeechRail 替换的 extension 类型和协商形态，继续保留会使“已打开分人”的会议收不到可用 patch 或在 EOF 卡住。新方案只改 transport、typed decoder 与已有 speaker-only 持久化/封存边界，不增加第二条 ASR 管道、模型进程或开发者 SDK，因此实现成本中等、持续维护成本低。

不在本卡范围内：修改 SpeechRail runtime、部署模型、批转写接口、跨会议实名识别、声纹库、自动人物合并，以及为旧 Sona / 旧 SpeechRail 维持兼容矩阵。

## 目标协议

| 阶段 | Sona 行为 | 必须验证的字段/语义 |
|---|---|---|
| 建连 | 所有模式按现有 OpenAI Realtime 地址和 ASR model 建连，并消费 `session.created` 作为 transport bootstrap | `session.created` 不用于 capability 协商；标准 ASR `session.update` / `session.updated` 先完成 |
| 开启分人 | 开关已启用的会议或字幕会话在首个 `input_audio_buffer.append` 前追加一次 `session.update` | `session.speechrail.diarization.enabled=true`；只接受该次 `session.updated` 的 `{enabled, version: 1, max_speakers: 4}` 回显 |
| 固定正文 | 消费 `conversation.item.input_audio_transcription.completed` | `item_id`、canonical `transcript`、session-global 16 kHz `audio_start_sample` / `audio_end_sample`、完整无缝的 `attribution_units` |
| 归属修订 | 消费 `speechrail.diarization.updated` | `event_id`、`session_id`、单调 `sequence`、`stable_through_sample`、每个 `segment_uid` 的连续 `revision`、`status`、`speaker`、coverage/overlap/candidates |
| 降级 | 消费一次 `speechrail.diarization.status` | `status=degraded` 后将未定单元置为 `unknown`，保留 ASR 正文并将会议状态记为 degraded |
| EOF | 所有 capture/commit 已排入 transport 后发送一次 `speechrail.diarization.finish` | 请求携带唯一 `event_id`；在 `done` 前继续接收尾部 `updated` |
| 封存 | 消费 `speechrail.diarization.done` | 与请求 `event_id` 相等的 `finalization_id`、`status=complete|degraded`、`through_sample`、`stable_through_sample`、`last_update_sequence`；持久化达到该 sequence 后才创建纪要 |

字幕不建立会议记录或 speaker 身份。它以 `(session_id, segment_uid)` 定位已固定的内存字幕单元，自动 patch 只替换匿名 speaker 显示值，正文和时间不变；重新连接后的同名 A–D 必须显示为新的 session/epoch 标签。停止字幕时，先停止接收新 PCM、排空已排队 PCM、发送 `finish`、继续消费尾部 update 直至 done、写入最终 SRT，再关闭 WebSocket。

`attribution_units` 的 `text_start` / `text_end` 是 Python Unicode code point 左闭右开区间，不是 UTF-16 index。`status=unknown` 必须有 `speaker=null`；`tentative` / `stable` 必须有匿名 `speaker`。`speaker_links` 只可作为诊断字段丢弃，不能驱动身份或跨会话合并。

## File Responsibility Map

| 文件 | 本卡后的责任 |
|---|---|
| `src/sona/speechrail/transport.py` | 标准 Realtime 建连、唯一 session opt-in、严格回显校验、finish 发送 |
| `src/sona/speechrail/transcription_events.py` | 新 completed/update/status/done wire 类型的严格解码；拒绝旧 extension literal |
| `src/sona/speechrail/transcriber.py` | 固定正文与 speaker patch 的顺序交付、一次 finish、done 等待和 protocol-failure 隔离 |
| `src/sona/subtitles/proxy.py`、`sessions.py` | 将会议/字幕两个独立开关传给正确会话；字幕 speaker-only patch、重连隔离和 finish/done drain |
| `ui/src/stores/subtitleStore.ts`、`ui/src/components/SubtitleStream.tsx` | 将字幕 `speaker` 统一为匿名字符串标签，渲染 `full_update.diarization` 的 active/degraded 状态 |
| `src/sona/config/meeting.py`、`src/sona/config/subtitles.py`、`src/sona/ui/runtime.py` | 会议与字幕的显式 `diarization_enabled` 配置和 runtime 装配；移除 legacy extension/overlay 状态 |
| `src/sona/asr/contracts.py`、`models.py`、`presenters.py` | 移除 `speaker_count_hint`/`group_id` 旧协议输入，提供匿名 session/epoch subtitle label 与不可变 patch 投影 |
| `src/sona/ui/app_context.py`、`src/sona/ui/protocol.py` | 删除 batch/overlay 的构造、注入与对外 capability 字段 |
| `src/sona/meeting/session.py`、`speaker_attribution.py` | 将新 patch 映射到不可变正文的归属事务，人工 override 优先，忽略跨会话声学身份建议 |
| `src/sona/meeting/finalization.py` | `done.last_update_sequence` 的持久化屏障；`complete` → complete、`degraded` → degraded |
| `src/sona/meeting/diarization_overlay.py`、`src/sona/speechrail/batch_transcriber.py` | 删除：旧 batch overlay 的第二分人生产者 |
| `tests/asr/`、`tests/test_subtitle_*.py`、`tests/test_meeting_*.py`、`tests/test_diarization_e2e.py` | fake WebSocket 契约、字幕/SRT patch、事务、EOF、人工覆盖和负面迁移测试 |
| `docs/manuals/`、`docs/architecture/`、`docs/operations/` | 实施完成时删除旧协议叙述，记录新开发者接入方式和真实联合验收结果 |

---

### Task 1: Replace negotiation with the one namespaced opt-in

**Files:**
- Modify: `src/sona/speechrail/transport.py`
- Modify: `src/sona/speechrail/transcriber.py`
- Modify: `src/sona/config/meeting.py`
- Modify: `src/sona/config/subtitles.py`
- Modify: `src/sona/subtitles/proxy.py`
- Modify: `src/sona/subtitles/sessions.py`
- Modify: `src/sona/asr/contracts.py`
- Modify: `src/sona/ui/runtime.py`
- Test: `tests/asr/test_speechrail_realtime.py`
- Test: `tests/asr/test_diarization_extension_contract.py`
- Test: `tests/asr/test_proxy_contract.py`
- Test: `tests/test_runtime.py`
- Test: `tests/test_subtitle_components.py`
- Test: `tests/test_subtitle_send_gaps.py`

**Interfaces:**
- Produces: `SpeechRailRealtimeClient.negotiate_diarization() -> None`, which sends exactly one post-baseline `session.update` with `session.speechrail.diarization.enabled`.
- Produces: `SpeechRailRealtimeClient.send_diarization_finish(event_id: str) -> None`.
- Produces: `SpeechRailStreamingTranscriber.diarization_unavailable_reason: str | None`, set only when the requested opt-in is rejected by `diarization_not_available` or `unsupported_operation` after baseline ASR setup.
- Consumes: `session.created` only to establish transport session identity, then the baseline OpenAI `session.updated`, then the diarization `session.updated` exact echo; no capability-string negotiation.

- [ ] Write a failing fake-WebSocket test that first delivers `session.created`, then asserts every session sends the same existing standard ASR configuration (`model`, `language`, and turn detection) and receives its normal `session.updated`. A meeting with `SONA_MEETING_DIARIZATION_ENABLED=true` and a subtitle stream with `SONA_SUBTITLE_DIARIZATION_ENABLED=true` each send exactly one additional opt-in before their first PCM; every disabled mode sends none.

```python
assert standard_connection.sent[0]["session"].get("speechrail") is None
assert meeting_connection.sent[1] == {
    "type": "session.update",
    "session": {"speechrail": {"diarization": {"enabled": True}}},
}
assert client.diarization_enabled is True
```

- [ ] Write failing negative tests for: legacy `input_audio_transcription.diarization`, `speechrail.diarization.v1`, a late opt-in after PCM, missing/changed `version` or `max_speakers`, and any `speechrail.diarization.*` event on a non-opted-in connection. Each must raise the existing stable protocol error and must not issue a second opt-in request.
- [ ] Add a failing negotiation-error test: after baseline ASR configuration is confirmed, an opt-in `error` with `diarization_not_available` or `unsupported_operation` preserves standard ASR delivery. A meeting persists `degraded` with that reason; a subtitle stream emits its explicit degraded state and keeps confirmed text/SRT. Neither creates a `finish/done` barrier or calls a legacy/batch implementation.
- [ ] Replace `diarization_extensions_enabled` with independent `SONA_MEETING_DIARIZATION_ENABLED` and `SONA_SUBTITLE_DIARIZATION_ENABLED` settings, both default `false`. Delete the legacy capability constant, `group_id`, `speaker_count_hint`, contract/timebase negotiation, and all fallback branches. Remove `speaker_count_hint` and `diarization_group_id` from `ASRSessionContext`, `SubtitleProxy.prepare_capture`, `MeetingCaptureSession`, their callers and contract tests. `SubtitleProxy` selects its matching explicit setting from `context.purpose`; `UIRuntime` wires both settings and removes all legacy overlay capability reporting. The standard OpenAI ASR model remains the connection model; the session opt-in alone requests Realtime diarization.
- [ ] Implement `send_diarization_finish` as an idempotent, one-shot client event:

```python
{"type": "speechrail.diarization.finish", "event_id": event_id}
```

- [ ] Run and pass:

```bash
uv run pytest tests/asr/test_speechrail_realtime.py tests/asr/test_diarization_extension_contract.py tests/asr/test_proxy_contract.py tests/test_runtime.py tests/test_subtitle_components.py tests/test_subtitle_send_gaps.py -q
```

### Task 2: Decode the new wire contract and reject the replaced one

**Files:**
- Modify: `src/sona/speechrail/transcription_events.py`
- Modify: `src/sona/speechrail/transcriber.py`
- Test: `tests/asr/test_speechrail_events.py`
- Test: `tests/asr/test_diarization_extension_contract.py`

**Interfaces:**
- Produces: `DiarizationUpdatedEvent`, `DiarizationStatusEvent`, `DiarizationDoneEvent` typed values.
- Consumes: `conversation.item.input_audio_transcription.completed`, `speechrail.diarization.updated`, `speechrail.diarization.status`, `speechrail.diarization.done`.

- [ ] Replace old fixture literals and decoder types. The accepted server literals are exactly `speechrail.diarization.updated`, `speechrail.diarization.status`, and `speechrail.diarization.done`; reject `speechrail.diarization.update` and `speechrail.diarization.finalized`.
- [ ] Add failing decoder tests using only sanitized JSON for a Chinese-plus-emoji transcript. Assert that `attribution_units` tile the canonical transcript exactly in code points, each patch revision starts at 1 and advances by one, `unknown` has `speaker is None`, and `tentative`/`stable` have a nonempty anonymous speaker.
- [ ] Decode and retain top-level `event_id`, `session_id`, and `sequence`. Require every patch target to refer to a completed item from the same session. Reject duplicate/missing `segment_uid`, noncontiguous revision, malformed ratios, foreign session, and an update after `done`.
- [ ] Decode `done` with `finalization_id`, `status`, `through_sample`, `stable_through_sample`, and `last_update_sequence`. Require `finalization_id` to match Sona's one outstanding finish `event_id`. Do not treat `status=degraded` as a missing terminal event.
- [ ] Preserve OpenAI `.segment` decoding for ordinary ASR, but in an opted-in diarization session reject every `.segment` event as a double-write protocol violation.
- [ ] Run and pass:

```bash
uv run pytest tests/asr/test_speechrail_events.py tests/asr/test_diarization_extension_contract.py -q
```

### Task 3: Project diarization patches into realtime subtitles and final SRT

**Files:**
- Modify: `src/sona/subtitles/sessions.py`
- Modify: `src/sona/subtitles/proxy.py`
- Modify: `src/sona/asr/models.py`
- Modify: `src/sona/asr/presenters.py`
- Modify: `src/sona/subtitles/archive.py`
- Modify: `ui/src/stores/subtitleStore.ts`
- Modify: `ui/src/components/SubtitleStream.tsx`
- Test: `tests/test_subtitle_components.py`
- Test: `tests/test_subtitle_send_gaps.py`
- Test: `tests/asr/test_proxy_contract.py`
- Test: `ui/src/stores/subtitleStore.test.ts`
- Test: `ui/src/components/SubtitleStream.test.tsx`

**Interfaces:**
- Consumes: validated `ASREvent(kind="diarization")` with same-session `segment_uid` revisions after the corresponding fixed subtitle unit.
- Produces: the existing `full_update` browser message and SRT with changed anonymous speaker display only; no meeting repository writes. Its clean current schema uses `lines[].speaker: str` for every subtitle line and adds `diarization: {"status": "off"|"active"|"degraded", "reason": str | null}`. The UI and producer change atomically; do not accept a number-or-string compatibility union or retain a legacy parser.

- [ ] Write a failing subtitle-session test: completed units A/B first render as unknown; a `tentative` then `stable` update changes only the matching unit's speaker display. Assert text, start/end, source UID and ordering are unchanged, and the emitted `full_update` replaces the prior line rather than appending a duplicate.
- [ ] Keep subtitle patch state keyed by `(source_session_id, segment_uid)` with contiguous revision validation. A patch for an unknown UID, a foreign session, a revision gap or a post-done update is a bounded protocol failure: retain already confirmed subtitle text, mark the subtitle stream degraded and do not infer a speaker.
- [ ] Render anonymous subtitle speakers as an explicit session/epoch-scoped display label (for example `会话 2 · A`) so a reconnect with another A cannot appear to be the same person. Convert every subtitle presenter speaker value to the one `str` wire type, update `subtitleStore` and `SubtitleStream` to render/hash the string safely, and use a reserved anonymous unknown label. Neither SRT nor UI may persist a human name, candidate or `speaker_links` value.
- [ ] Extend the existing `full_update` payload with `diarization.status` (`off`, `active`, or `degraded`) and a sanitized optional reason. Update the store/component to show the current state without creating a parallel browser event channel. Normal subtitles emit `off`; opt-in success emits `active`; unavailable, malformed-wire, lost-done and terminal degraded outcomes emit `degraded` while retaining the latest confirmed text.
- [ ] Replace `StandardSubtitleSession.close_stream()`'s direct cancellation with a graceful drain for an opted-in stream: stop acceptance, await the sent PCM queue, send exactly one finish, keep the receive loop alive through all tail updates and done, persist the final full snapshot/SRT, then clear and close. If done times out or the extension is degraded, persist the latest canonical subtitle/SRT and publish a bounded degraded state; do not invoke a second diarizer.
- [ ] Add a reconnect test where two subtitle sessions both return A. Their labels must be distinct by epoch/session, and neither session may patch the other session's lines. Add UI/store tests that reject numeric speaker payloads, render the scoped string label and visibly expose `degraded`. Add a disabled-subtitle control test proving no `speechrail` request or diarization event handling occurs.
- [ ] Run and pass:

```bash
uv run pytest tests/test_subtitle_components.py tests/test_subtitle_send_gaps.py tests/asr/test_proxy_contract.py -q
cd ui && npm test -- --run SubtitleStream.test.tsx
```

### Task 4: Apply speaker-only patches to meeting transcripts without changing canonical text

**Files:**
- Modify: `src/sona/speechrail/transcriber.py`
- Modify: `src/sona/meeting/session.py`
- Modify: `src/sona/meeting/speaker_attribution.py`
- Modify: `src/sona/meeting/asr_mapping.py`
- Modify: `src/sona/meeting/repository.py`
- Modify: `src/sona/ui/app_context.py`
- Modify: `src/sona/ui/protocol.py`
- Delete: `src/sona/meeting/diarization_overlay.py`
- Delete: `src/sona/speechrail/batch_transcriber.py`
- Test: `tests/test_speaker_attribution.py`
- Test: `tests/test_meeting_session.py`
- Test: `tests/test_ui_app_context.py`
- Test: `tests/test_runtime_events.py`
- Modify/Delete: `tests/test_diarization_overlay.py`
- Modify: `tests/test_meeting_finalization.py`

**Interfaces:**
- Consumes: completed item before all updates targeting its `segment_uid`.
- Produces: existing `SpeakerPatchEvent` / repository speaker-only transaction with the new wire event names and fields.

- [ ] Write a failing repository test that appends three completed items, applies an update to the middle item's unit, and verifies the id, order, text, timing and source identity of all three items are byte-for-byte unchanged. Replay of the same event is idempotent; same event id with a different payload is rejected.
- [ ] Write a failing meeting test where a user has manually named/corrected a segment. A later `tentative` then `stable` update must update model evidence only; the displayed/persisted manual speaker remains unchanged. An `unknown` patch must display the reserved unknown state and never manufacture a person.
- [ ] Convert the new wire event into the existing domain transaction only after session id, source item, unit tiling, and revision checks pass. Maintain a meeting-local mapping from the anonymous session label to an opaque Sona speaker key. Ignore `speaker_links` and candidates for identity resolution.
- [ ] Add a reconnect test with two SpeechRail session ids both using `A`. They must create distinct opaque Sona speaker keys and never inherit each other's automatic or manual label. A manual correction remains attached only to its immutable item identity.
- [ ] Delete `group_generation` and `speaker_links` semantics from Sona domain types, group remapping and every legacy batch overlay invocation. Delete `src/sona/meeting/diarization_overlay.py`, `src/sona/speechrail/batch_transcriber.py`, their unit tests, and their `app_context` construction; remove `diarization_overlay_enabled` from the runtime protocol. Retain no second diarization producer. Historical rows remain readable as historical data, but no running code may select their legacy contract path.
- [ ] Run and pass:

```bash
uv run pytest tests/test_speaker_attribution.py tests/test_meeting_session.py tests/test_ui_app_context.py tests/test_runtime_events.py -q
```

### Task 5: Make `done` the sole diarization finalization barrier

**Files:**
- Modify: `src/sona/speechrail/transcriber.py`
- Modify: `src/sona/meeting/session.py`
- Modify: `src/sona/meeting/finalization.py`
- Test: `tests/test_meeting_finalization.py`
- Test: `tests/asr/test_speechrail_realtime.py`

**Interfaces:**
- Consumes: `speechrail.diarization.done.last_update_sequence`.
- Produces: meeting diarization state `complete` for `done.status=complete`, `degraded` for `done.status=degraded` or the prior status event.

- [ ] Write a failing EOF ordering test: final capture/commit drains first, Sona emits exactly one finish, receives two tail `updated` events, then `done`; the finalizer must wait until the repository watermark reaches `last_update_sequence` before transcript finalization or minutes creation.
- [ ] Write failing negative tests for a lost `done`, mismatched `finalization_id`, `done` before an outstanding patch persists, and `status=degraded`. The first three must end in the documented bounded degraded outcome without leaving a task pending; the last must still finalize a readable meeting with anonymous `unknown` attribution where necessary.
- [ ] Replace all `finalized`/legacy clear-barrier checks with the new `done` barrier. Only a meeting that did not request `diarization_enabled`, or whose opt-in was explicitly persisted as unavailable/degraded before capture, has no diarization barrier; neither case may use old protocol or batch work.
- [ ] Make the unavailable path concrete: expose `diarization_unavailable_reason` on the stream, have `_diarization_barrier` persist `finalize_diarization(status="degraded", reason=...)` and return without sending finish, and leave standard capture cleanup unchanged. All other requested-and-enabled meetings send finish and require done.
- [ ] Ensure cleanup remains at-most-once. A failure of diarization must preserve completed ASR text and must not retrigger capture, batch diarization, or summary creation before the terminal decision.
- [ ] Run and pass:

```bash
uv run pytest tests/test_meeting_finalization.py tests/asr/test_speechrail_realtime.py -q
```

### Task 6: Remove stale developer guidance and perform joint protocol acceptance

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/README.md`
- Modify: `docs/manuals/SpeechRail-流式说话人分离对接手册.md`
- Modify: `docs/manuals/SpeechRail-Realtime-v2-语音转文字开发对接手册.md`
- Modify: `docs/architecture/speaker-diarization-e2e-design.md`
- Modify: `docs/architecture/系统总体架构与详细设计方案.md`
- Modify: `docs/architecture/实时语音交互与字幕-方案与最佳实践.md`
- Modify: `docs/manuals/会议助手后端运行与前后端联调.md`
- Modify: `docs/operations/speaker-diarization-e2e-acceptance-2026-09-06.md`
- Create: `docs/operations/speechrail-openai-diarization-integration-acceptance.md`
- Test: `tests/test_diarization_e2e.py`

- [ ] Replace all old `v1`, `update`, `finalized`, `group_id`, legacy fallback, four-combination compatibility, and post-recording batch-overlay instructions with the target protocol table in this card. Move the replaced implementation reports to the historical archive only if the repository's documentation convention requires retaining them; no active document may claim the old literals work against current SpeechRail.
- [ ] Add fake end-to-end coverage for both modes. Meeting sequence: opt-in → completed item 1 → completed item 2 → update item 1 → manual correction item 1 → stable update item 1 → finish → tail update item 2 → done. Subtitle sequence: opt-in → completed unit → update → reconnect with same A label → finish → tail update → done. Verify immutable text/timings, session separation, final SRT and, for meetings, persisted watermark and minutes input.
- [ ] Add normal-ASR control tests proving that a subtitle/assistant connection with its diarization setting disabled sends no `speechrail` session field and succeeds with the unchanged OpenAI event stream.
- [ ] With an authorized ready SpeechRail runtime, run one sanitized short multi-speaker meeting and one sanitized realtime subtitle session against `/v1/realtime`; record only commits/versions, contract fixture hashes, event ordering, terminal state and aggregate timings. Do not state DER, tail quality or long-run quality as passed unless the corresponding SpeechRail quality AC has evidence.
- [ ] Run the complete Sona gate and inspect the actual result:

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
git diff --check
```

## Acceptance Criteria

- [ ] **AC-S1:** Any enabled meeting or subtitle session sends exactly one pre-PCM `session.speechrail.diarization.enabled=true`; exact `session.updated` echo is required.
- [ ] **AC-S2:** Every session first confirms its existing standard OpenAI ASR configuration. Disabled Sona OpenAI SDK/Realtime consumers send no diarization extension and have no behavior change.
- [ ] **AC-S3:** Every accepted update targets one immutable, same-session completed attribution unit; canonical transcript and timings never change.
- [ ] **AC-S4:** A–D labels remain anonymous and meeting/session scoped. No automatic human identity, voiceprint, cross-meeting linkage or persistence of `speaker_links` occurs.
- [ ] **AC-S5:** Manual corrections always outrank any `tentative`, `stable`, `unknown` or degraded update.
- [ ] **AC-S5a:** Subtitle updates replace only the matching `(session_id, segment_uid)` anonymous speaker display; every `full_update.lines[].speaker` is a session/epoch-scoped string label, subtitle text, timing, order and SRT body never change, and reconnects do not share A–D labels.
- [ ] **AC-S5b:** Every subtitle `full_update` exposes `diarization.status=off|active|degraded`; the UI renders the state, and its parser rejects the replaced numeric speaker shape.
- [ ] **AC-S6:** Old fields and literals are rejected deterministically; source has no dual-protocol, batch-overlay module, configuration, runtime wiring or compatibility matrix.
- [ ] **AC-S7:** EOF sends one finish, accepts all updates before done, persists through `last_update_sequence`, and only then creates the final transcript/minutes.
- [ ] **AC-S7a:** Subtitle stop drains queued PCM, sends one finish, applies every tail update through done, then writes the final SRT and closes the stream.
- [ ] **AC-S8:** `status=degraded`, lost done and malformed wire data preserve canonical ASR text, reach a bounded visible terminal state, and leave no hanging meeting, subtitle or finalizer task.
- [ ] **AC-S8a:** An opt-in rejected as unavailable leaves baseline ASR active, records one explicit degraded reason for the relevant mode, and never issues legacy events, batch requests or a second diarization attempt.
- [ ] **AC-S9:** Fake protocol, transaction and full Sona quality gates pass; authorized joint smoke has a saved sanitized acceptance record.

## Handoff / External Preconditions

Development and fake acceptance can proceed immediately. The only external prerequisite for the joint smoke is a SpeechRail checkout/runtime that exposes the contract listed above and has a ready diarization profile. If that profile is unavailable, record `diarization_not_available` as the expected integration precondition failure; do not revive the old protocol or deploy a local model in Sona.

The joint smoke does not close SpeechRail's quality gates for tail speech, DER/cpCER, long files, two-hour resource behavior, or RTTM/UEM corpus scoring. Those remain owned by SpeechRail's current diarization acceptance matrix and its quality-evaluation handoff.
