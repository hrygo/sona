# Audio Startup Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a transient macOS CoreAudio startup delay from leaving Sona running without a microphone, and make the existing “恢复会话” action able to recover a failed audio source.

**Architecture:** Keep `AudioHub` as the sole microphone owner. Move PyAudio resource cleanup to the capture thread that owns the stream, wait for an old capture attempt to finish before allowing a retry, and extend the cold-start deadline beyond the observed 14-second CoreAudio initialization. `UIRuntime` will retry the hub when an assistant session is resumed and will reject the session transition if the microphone is still unavailable, while preserving the existing degraded snapshot.

**Tech Stack:** Python 3.12, asyncio, PyAudio/CoreAudio, FastAPI runtime state, pytest/pytest-asyncio.

**Spec:** Runtime evidence from `runtime/logs/ui.log` on 2026-09-09 (`AUHAL err=-50`, followed by `音频流打开超时（5s）`) plus the existing Sona single-owner microphone contract in `docs/architecture/系统总体架构与详细设计方案.md`.

## Global Constraints

- Keep `AudioHub` as the only microphone capture owner and preserve bounded sink queues.
- Do not persist or log raw microphone PCM.
- Preserve `SONA_INTERACTION_INPUT_DEVICE_NAME=MacBook Pro` selection semantics and the fixed 16 kHz mono int16 pipeline.
- A failed microphone source must remain visible through `degraded_reason=audio_hub_unavailable`; do not silently report a healthy audio pipeline.
- Do not add dependencies or alter SpeechRail/LM Studio model ownership.
- Run focused tests during the red/green loop, then the documented backend, type, lint, frontend, and runtime checks before claiming completion.

### Task 1: Lock down the failing lifecycle and recovery behavior

**Files:**
- Modify: `tests/test_audio_hub.py`
- Modify: `tests/test_runtime.py`

**Interfaces:**
- `AudioHub.start()` must not terminate PyAudio while its capture thread is still inside `open()`.
- `UIRuntime.start_assistant()` must attempt to restore an unavailable hub before starting the assistant workload.

- [x] **Step 1: Write the failing tests**

  Add a blocked-open test that releases `open()` only after `AudioHub.start()` has timed out and asserts that `terminate()` is deferred until the capture thread finishes. Add a runtime test where the first `hub.start()` fails and the second succeeds; assert that the assistant transition succeeds and `snapshot().degraded_reason` clears. Add a persistent-failure test asserting that the transition returns an error and remains degraded.

- [x] **Step 2: Run the focused tests to verify they fail**

  Run:

  ```bash
  uv run pytest --no-cov tests/test_audio_hub.py tests/test_runtime.py -q
  ```

  Expected: the cleanup-order assertion and hub-recovery assertions fail against the current implementation.

- [x] **Step 3: Keep the tests deterministic**

  Use `threading.Event` for the mocked blocking `open()` and `AsyncMock` for runtime components. Do not record or assert microphone content; only assert lifecycle state, call counts, and the degraded snapshot.

### Task 2: Make `AudioHub` startup and teardown race-safe

**Files:**
- Modify: `src/sona/audio/hub.py`
- Modify: `tests/test_audio_hub.py`

**Interfaces:**
- Keep the existing `AudioHub(device_index, device_name, sample_rate, chunk_size, ...)` constructor compatible.
- Keep `AudioInputDeviceError` as the public failure type for device/open failures.

- [x] **Step 1: Increase the cold-start deadline**

  Change `STREAM_OPEN_TIMEOUT_SECS` from `5.0` to `20.0`, retaining constructor validation and the explicit timeout error. This covers the observed approximately 14-second CoreAudio cold initialization while preserving a bounded failure path.

- [x] **Step 2: Give the capture thread ownership of PyAudio**

  Construct, use, and terminate the `PyAudio` instance in `_worker_capture_loop`. Have the async side only signal `_running=False`, stop sink workers, and wait for the worker to finish. Do not call `terminate()` from the event-loop thread while the worker can still be inside `PyAudio.open()`.

- [x] **Step 3: Serialize retries after a timed-out open**

  Track the capture thread completion with a `threading.Event` (or equivalent existing state) and reject or wait for a new `start()` until the previous attempt has exited. Guard `_report_stream_open` with the attempt generation/future so a late result from an old attempt cannot complete a new startup future.

- [x] **Step 4: Run the focused audio tests**

  Run:

  ```bash
  uv run pytest --no-cov tests/test_audio_hub.py -q
  ```

  Expected: all audio lifecycle, timeout, named-device, fan-out, and backpressure tests pass, including the new cleanup-order regression.

### Task 3: Make session recovery reinitialize the audio source

**Files:**
- Modify: `src/sona/ui/runtime.py`
- Modify: `tests/test_runtime.py`

**Interfaces:**
- `UIRuntime.start_assistant()` remains the control command target used by the existing frontend “恢复会话” button.
- `RuntimeStateSnapshot.degraded_reason` remains `audio_hub_unavailable` until a real hub start succeeds.

- [x] **Step 1: Add a single hub recovery helper**

  Add a private `_ensure_hub()` helper that returns immediately when `_hub_active` is true, otherwise calls `_start_hub()`, publishes the recovered state, and raises a stable audio-unavailable error if the retry fails.

- [x] **Step 2: Call the helper before assistant recovery**

  Change `UIRuntime.start_assistant()` to await `_ensure_hub()` before delegating to `RuntimeModeCoordinator.start_assistant()`. This prevents a false “running” assistant session with no PCM source.

- [x] **Step 3: Preserve degraded behavior on failure**

  If recovery fails, leave the coordinator in its current idle/stopped state, retain `degraded_reason=audio_hub_unavailable`, and let `ControlBridge` return its existing command error envelope. Do not auto-start an assistant pipeline without a live `AudioHub`.

- [x] **Step 4: Run the runtime-focused tests**

  Run:

  ```bash
  uv run pytest --no-cov tests/test_runtime.py -q
  ```

  Expected: existing lifecycle tests and the new recovery tests pass.

### Task 4: Review, verify, and record the change

**Files:**
- Modify: `docs/superpowers/plans/2026-09-09-audio-startup-robustness.md`

- [x] **Step 1: Review the diff and verify scope**

  Run `git diff --check`, inspect the staged/unstaged diff, and confirm no `.env`, audio data, generated output, or unrelated files changed.

- [x] **Step 2: Run the project quality gates**

  Run the documented backend pytest command with `SONA_TEST_DATABASE_URL=postgresql:///knowledge`, `uv run mypy src/`, `uv run ruff check src/ tests/`, `cd ui && npm test -- --run`, and `cd ui && npm run build`.

- [x] **Step 3: Perform real local runtime verification**

  Stop and start Sona through `scripts/sona-ctl.sh`, wait for port 8100, query `/api/runtime`, and confirm `degraded_reason` is null, `mic_muted` is false, and the microphone level is updating. Stop the service after verification only if it was stopped before the task; otherwise preserve the user's running state.

- [x] **Step 4: Commit the atomic fix**

  ```bash
  git add src/sona/audio/hub.py src/sona/ui/runtime.py tests/test_audio_hub.py tests/test_runtime.py docs/superpowers/plans/2026-09-09-audio-startup-robustness.md
  git commit -m "fix(audio): 增强麦克风启动与会话恢复鲁棒性"
  ```
