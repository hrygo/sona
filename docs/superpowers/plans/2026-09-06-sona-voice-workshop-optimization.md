# Sona Voice Workshop Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 sona 声音工坊的声音克隆上传与自然语言试听调用链，并建立与 SpeechRail OpenAI 兼容扩展的一致边界。

**Architecture:** sona 保留 `/v1/audio/speech` 的 OpenAI 兼容请求语义，新增 `/v1/voices/previews` 作为短生命周期声音设计预览代理。clone 请求不在 sona 解析 multipart，而是执行有界、带鉴权的原始流式转发；所有 SpeechRail 响应保留状态码、Content-Type 和结构化错误，只有网络故障才映射为稳定的本地错误。

**Tech Stack:** Python 3.12, FastAPI, httpx, pytest/pytest-asyncio, React 19, TypeScript, Vitest.

**Spec:** GitHub issue `hrygo/sona#7`; 跨仓库预览契约见 `hrygo/SpeechRail#8`。

## Global Constraints

- 不保存原始音频，不把 clone 文件写入 sona 数据库或 runtime 目录。
- 不安装、不下载、不启动本地 ASR/TTS 模型；SpeechRail 继续拥有语音模型生命周期。
- `/v1/audio/speech` 保持 `voice` 必填的 OpenAI 兼容语义，不发送单数 `instruction` 到标准端点。
- multipart 透传必须保留请求的 `Content-Type` boundary，并限制最大请求体为 15 MiB。
- meeting/finalizing 或会议/字幕独占 PCM owner 时，声音工坊请求返回 409 `mode_conflict`；assistant 模式由前端录音回调先静音，保留工坊可用性。
- 保留工作区已有未提交改动，不执行 reset、checkout、清理或无关重构。

---

### Task 1: 固化 HTTP 代理契约与失败回归测试

**Files:**
- Modify: `tests/test_ui_server.py`
- Modify: `src/sona/ui/http_routes.py`

**Interfaces:**
- Produces `POST /v1/voices/previews` as a JSON-to-SpeechRail proxy.
- Produces bounded raw-body forwarding for `POST /v1/voices/clone`.
- Preserves upstream response status, body, and content type for voice preview, clone, and speech proxy routes.

- [x] **Step 1: Write the failing tests**

  Add tests for: multipart clone requests reaching the upstream as an async content stream without calling `request.form()`, preview forwarding to `/v1/voices/previews`, standard speech 422 remaining 422 with its JSON body, and meeting/PCM ownership returning 409 before contacting SpeechRail.

- [x] **Step 2: Run the focused backend tests and verify the current failure**

  Run `uv run pytest tests/test_ui_server.py -k 'voice or speech or preview' -q`.
  Expected: the new tests fail because clone currently parses multipart locally, preview has no route, speech currently converts upstream 4xx to 502, and no voice-workshop mode guard exists.

- [x] **Step 3: Implement the smallest proxy helpers**

  Add private helpers for: copying only safe upstream headers, building a stable `mode_conflict` JSON error, and forwarding a bounded `Request.stream()` async iterator. Reject a declared `Content-Length` above 15 MiB before opening the upstream request; stop the iterator if the streamed total exceeds the same limit.

- [x] **Step 4: Implement the preview route and response pass-through**

  Add `POST /v1/voices/previews` mapped to SpeechRail `/voices/previews`, with JSON content type, configured bearer authentication, and the same response pass-through behavior as clone. Remove `raise_for_status()` from `/v1/audio/speech` and `/v1/voices` so upstream validation/capability errors remain observable.

- [x] **Step 5: Replace local multipart parsing with bounded streaming**

  Make `/v1/voices/clone` forward the original request body and multipart content type directly to SpeechRail. Do not add `python-multipart`; this keeps sona from buffering or persisting user audio and makes the route independent of Starlette multipart parsing.

- [x] **Step 6: Run the focused backend tests and verify the green result**

  Run `uv run pytest tests/test_ui_server.py -k 'voice or speech or preview' -q` and confirm all selected tests pass.

### Task 2: Add typed frontend voice service and migrate both design flows

**Files:**
- Create: `ui/src/services/voiceService.ts`
- Create: `ui/src/services/voiceService.test.ts`
- Modify: `ui/src/components/VoiceStudioModal.tsx`
- Modify: `ui/src/components/VoiceDesignModal.tsx`

**Interfaces:**
- `voiceService.preview(input: VoicePreviewRequest): Promise<Blob>` calls `/v1/voices/previews`.
- `voiceService.clone(formData: FormData): Promise<VoiceCatalogItem>` calls `/v1/voices/clone`.
- `voiceService.create(input: VoiceCreateRequest): Promise<VoiceCatalogItem>` calls `/v1/voices`.
- `voiceService.speech(input: SpeechRequest): Promise<Blob>` calls `/v1/audio/speech` and always includes `voice`.
- Service errors expose HTTP status and stable upstream `code`/`request_id` when present without leaking raw stack traces.

- [x] **Step 1: Write failing service tests**

  Cover preview payload fields (`model`, `input`, `instruction`, `response_format`), clone FormData preservation, required `voice` for standard speech, and structured error extraction from both `{error:{...}}` and `{detail:...}` responses.

- [x] **Step 2: Run the service tests and verify RED**

  Run `cd ui && npm test -- --run src/services/voiceService.test.ts`; expect failure because the service does not exist.

- [x] **Step 3: Implement the typed service**

  Use `apiUrl`, `fetch`, and a single response/error parser. Preview must send `instruction` to `/v1/voices/previews`; standard speech must reject an empty voice before making a request; clone must not set `Content-Type` manually so the browser can generate the multipart boundary.

- [x] **Step 4: Migrate the active VoiceStudioModal paths**

  Replace direct fetch calls for clone, standard audition, design preview, and design creation with `voiceService`. The design preview must no longer call `/v1/audio/speech` and must include no fake voice.

- [x] **Step 5: Migrate the legacy VoiceDesignModal preview**

  Use the same preview service and display the parsed error message. Keep its existing persistent create flow on `/v1/voices`.

- [x] **Step 6: Run frontend component and service tests**

  Run `cd ui && npm test -- --run src/services/voiceService.test.ts src/components/VoiceStudioModal.test.tsx src/components/VoiceDesignModal.test.tsx` and confirm the request shapes and existing UI behavior pass.

### Task 3: Verify integration boundaries and quality gates

**Files:**
- Modify: `tests/test_ui_server.py` only if a focused regression exposes a missing assertion.
- Modify: `ui/src/components/VoiceStudioModal.test.tsx` only if migrated request behavior needs a component-level assertion.
- Modify: `ui/src/components/VoiceDesignModal.test.tsx` only if migrated request behavior needs a component-level assertion.

- [x] **Step 1: Run the complete backend suite**

  Run `SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/` and inspect failures rather than skipping them.

- [x] **Step 2: Run Python type and lint checks**

  Run `uv run mypy src/` and `uv run ruff check src/ tests/`.

- [x] **Step 3: Run the complete frontend test and build gates**

  Run `cd ui && npm test -- --run` and `cd ui && npm run build`.

- [x] **Step 4: Inspect the final diff and verify scope**

  Run `git diff -- src/sona/ui/http_routes.py tests/test_ui_server.py ui/src/services/voiceService.ts ui/src/services/voiceService.test.ts ui/src/components/VoiceStudioModal.tsx ui/src/components/VoiceDesignModal.tsx ui/src/components/VoiceStudioModal.test.tsx ui/src/components/VoiceDesignModal.test.tsx` and confirm no existing unrelated worktree changes were staged or overwritten.

- [ ] **Step 5: Perform a manual contract check when SpeechRail #8 is available**

  Verify official TTS still returns 200, preview returns audio without creating a profile, clone returns the upstream 201/4xx contract, and meeting mode returns 409 before any upstream request. SpeechRail #8 is merged locally but deployment is still pending, so this remains the only unchecked item.

### Task 4: Align Sona with the merged SpeechRail #8 contract

**Files:**
- Modify: `src/sona/config/interaction.py`
- Modify: `src/sona/ui/http_routes.py`
- Modify: `ui/src/contracts/voiceContract.ts`
- Modify: `ui/src/services/voiceService.ts`
- Modify: `ui/src/components/AssistantPanel.tsx`
- Modify: `ui/src/components/VoiceStudioModal.tsx`
- Modify: `ui/src/components/VoiceDesignModal.tsx`
- Modify: related tests and the operations handoff

- [x] Add a Sona `/v1/models` proxy and forward safe response headers including `Retry-After` and `WWW-Authenticate`.
- [x] Consume model-level TTS capabilities while retaining compatibility when an older SpeechRail omits the fields.
- [x] Align Sona-generated connection/timeout/protocol errors with the structured SpeechRail envelope and use a configurable TTS REST request timeout.
- [x] Gate clone/design/preview UI actions from explicit model capabilities without inferring support from profile names.
- [x] Keep preview seed request-only; do not invent a seed response header absent from the merged SpeechRail contract.
