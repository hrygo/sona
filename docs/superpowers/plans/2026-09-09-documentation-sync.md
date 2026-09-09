# Cross-Project Documentation Synchronization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkboxes for tracking.

**Goal:** Synchronize the active Sona and SpeechRail documentation with the verified 2026-09-09 architecture, runtime, VAD ownership, diarization protocol, and clone-TTS stability behavior.

**Architecture:** Keep Sona authoritative for mode arbitration, PCM ownership, assistant-local turn detection, meeting persistence, and UI-facing semantics. Keep SpeechRail authoritative for ASR/TTS inference, server-side Realtime VAD, speech admission, continuous diarization, audio alignment, and managed runtime deployment. Historical documents remain historical; current indexes point to the active sources of truth.

**Tech Stack:** Markdown with YAML frontmatter, OpenAI Realtime `/v1/realtime`, SpeechRail 2.0.3 managed wheel, Silero VAD, bounded `SpeechAdmission`, CoreML Sortformer diarization, Qwen3-TTS ICL clone path.

**Spec:** Current source implementation and the verified 2026-09-09 Sona × SpeechRail runtime behavior.

## Global Constraints

- Do not change application source code or runtime configuration in this documentation-only task.
- Preserve existing user modifications and historical documents; update only active/current references and add superseding ADRs where the decision is architectural.
- Treat current source, tests, health output, and live smoke evidence as higher authority than stale plan text.
- Document that SpeechRail deployments must be built and installed from the SpeechRail source repository; never document direct edits under `runtime/current`.
- Do not expose secrets, private model paths, or full environment values.

---

### Task 1: Synchronize Sona current architecture and VAD ownership

**Files:**
- Modify: `docs/README.md`
- Modify: `docs/architecture/系统总体架构与详细设计方案.md`
- Modify: `docs/architecture/实时语音交互与字幕-方案与最佳实践.md`
- Modify: `docs/architecture/speaker-diarization-e2e-design.md`
- Modify: `docs/manuals/会议助手后端运行与前后端联调.md`
- Create: `docs/decisions/0013-vad-ownership-and-mode-boundaries.md`

**Interfaces:**
- Consumes: Sona `transport.py`, `transcriber.py`, `stt_processor.py`, `interaction/pipeline.py`, and current SpeechRail Realtime contract.
- Produces: one current Sona documentation path explaining `assistant` local VAD, subtitle `400ms` server VAD, meeting `900ms` server VAD, and the no-duplicate ownership rule.

- [x] Add the current v2.0.3 integration baseline, effective VAD values, 32ms frame quantization note, and source-built SpeechRail boundary to the Sona index and active architecture pages.
- [x] Replace stale “planned” diarization wording with the implemented strict revision, `unknown` status, EOF `done`/watermark barrier, epoch reset, zero-duration alignment handling, and speaker-patch precedence behavior.
- [x] Add ADR-0013 explaining why Sona keeps local VAD only for the assistant while subtitle/meeting turn detection belongs to SpeechRail.
- [x] Update the meeting runbook with the current source-to-managed-runtime deployment and live verification expectations.

### Task 2: Synchronize SpeechRail current VAD, diarization, clone, and deployment documents

**Files:**
- Modify: `docs/README.md`
- Modify: `docs/architecture/README.md`
- Modify: `docs/architecture/current-boundaries.md`
- Modify: `docs/architecture/realtime-vad-2026-best-practices.md`
- Modify: `docs/architecture/voice-cloning-design-and-handoff.md`
- Modify: `docs/operations/realtime-vad-runtime-acceptance-2026-09-08.md`
- Modify: `docs/operations/runtime-deployment.md`
- Create: `docs/decisions/0013-realtime-vad-and-diarization-boundary.md`

**Interfaces:**
- Consumes: SpeechRail `realtime_openai.py`, `speech_admission.py`, VAD backends, diarization session, Qwen3 TTS worker, clone audio validator, current health output, and source-built managed installer flow.
- Produces: one current SpeechRail documentation path distinguishing generic API defaults from Sona mode policies and recording the v2.0.3 verified runtime.

- [x] Record that `auto` currently resolves to Silero on the managed quality profile and that `SpeechAdmission` is the boundary state machine, not a second independent endpoint detector.
- [x] Document generic SpeechRail `server_vad` defaults separately from Sona overrides: subtitle `400ms`, meeting `900ms`, threshold `0.65`, prefix `300ms`, with 32ms frame quantization.
- [x] Document that diarization activity consumes the continuous PCM stream independently of turn endpointing and only patches speaker metadata.
- [x] Update clone documentation with deterministic request-local seed, low-temperature/top-p settings, gain freeze after calibration, peak ceiling, input signal validation, clean capture constraints, and the explicit non-1.0 clone speed limitation.
- [x] Update deployment documentation and ADR-0013/ADR-0014 to require building the wheel from the SpeechRail source repository and deploying through `tools.install_macos.install_managed`.

### Task 3: Cross-project documentation verification

**Files:**
- Verify: all files modified in Tasks 1–2

- [x] Search both documentation trees for stale claims that conflict with the current baseline (`v2.0.0`/`v2.0.2` as current runtime, Sona-local meeting VAD, duplicate VAD ownership, old diarization fallback, or unrestricted clone speed).
- [x] Run `git diff --check` in both repositories.
- [x] Validate YAML frontmatter is still parseable enough for the existing documentation convention and confirm cross-repository links point to source paths or stable contract documents.
- [x] Report the exact modified files, preserved unrelated worktree changes, and any intentionally historical documents left unchanged.
