# Sona Clone TTS Loudness Playback Stability Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 让 Sona 在消费 SpeechRail cloned-voice Realtime PCM 时稳定音量、避免突变和削波，并兼容尚未部署稳定响度能力的旧 SpeechRail。

**Architecture:** SpeechRail#34 是 clone 输入与生成响度的权威修复；Sona 在 Realtime TTS 客户端建立一次请求级的 PCM16 guard。收到 stable_loudness_v1 能力时只做 peak safety；旧服务或未声明能力时使用有界、慢速的 compatibility guard。guard 跨所有 80 ms delta 保持状态，绝不逐块独立归一化；现有 CoreAudio/PyAudio 采样率与 buffer 策略保持不变。

**Tech Stack:** Python 3.12、uv、Pipecat、websockets、PCM16、pytest、Ruff、mypy、React/Vite 现有质量门禁、macOS CoreAudio。

**Spec:** Sona#10；上游生成侧方案见 SpeechRail#34。

## Global Constraints

- Python 严格锁定 3.12，使用 uv、PEP 621；不新增音频第三方依赖。
- SpeechRail 独占 ASR/TTS/Diarization 模型生命周期；Sona 只处理收到的 PCM，不安装或加载模型。
- 24 kHz mono PCM16、Pipecat TTS frame、response cancel、interruption、context_id 和现有输出设备策略不变。
- 不按单个 80 ms chunk 独立归一化；guard 生命周期与一个 synthesize() 请求一致。
- 不把文本、PCM、Base64、API key、原始参考音频、完整 RMS 序列或设备 UID 写入日志。
- stable_loudness_v1 已声明时不得再执行大幅上拉；旧服务兼容模式必须有最大增益/衰减边界。
- 不修改现有麦克风采样率、VAD、回声抑制、会议 PCM owner 或 CoreAudio buffer 配置。
- 工作区已有改动归其所有者；实现时只修改本计划列出的文件。

---

### Task 1: 解析 SpeechRail 响度能力并选择 guard 模式

**Files:**
- Modify: src/sona/speechrail/tts.py:22-150
- Test: tests/test_speechrail_tts.py

**Interfaces:**
- Produces: SpeechRailTTSClient.audio_loudness_profile: str | None
- Produces: _resolve_loudness_mode(profile: str | None) -> Literal["safety", "compatibility"]
- Consumes: session.created.session.speech_capabilities.audio_loudness_profile

- [ ] Step 1: 写旧/新服务能力解析失败测试

    def test_tts_client_uses_safety_mode_for_stable_server() -> None:
        connection = FakeSpeechConnection(
            session_capabilities={"audio_loudness_profile": "stable_loudness_v1"}
        )
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="clone-test",
            language="zh",
            connection_factory=lambda _: _immediate(connection),
        )
        chunks = [chunk async for chunk in client.synthesize("你好")]
        assert chunks
        assert client.audio_loudness_profile == "stable_loudness_v1"

    def test_tts_client_falls_back_to_compatibility_without_capability() -> None:
        connection = FakeSpeechConnection(session_capabilities={})
        client = SpeechRailTTSClient(
            url=connection.uri,
            model="speechrail/qwen3-tts",
            voice="clone-test",
            language="zh",
            connection_factory=lambda _: _immediate(connection),
        )
        chunks = [chunk async for chunk in client.synthesize("你好")]
        assert chunks
        assert client.audio_loudness_profile is None

    测试 fake event 只放入能力字段，不放文本、PCM 或真实 voice metadata。

- [ ] Step 2: 运行测试确认失败

    Run:
    uv run pytest tests/test_speechrail_tts.py -q --no-cov

    Expected: FakeSpeechConnection 不支持 session capabilities，client 没有 profile 属性，测试 FAIL。

- [ ] Step 3: 只读取 session.created 的 namespaced 字段

    在 synthesize() 进入事件循环前把模式初始化为 compatibility；收到 session.created 时读取：

    session = event.get("session")
    capabilities = session.get("speech_capabilities") if isinstance(session, dict) else None
    profile = capabilities.get("audio_loudness_profile") if isinstance(capabilities, dict) else None
    if isinstance(profile, str) and profile == "stable_loudness_v1":
        self._audio_loudness_profile = profile

    未声明、未知或类型错误的 profile 都按 compatibility 处理；不得把未知字段转发给标准 OpenAI 事件，也不得因为能力缺失阻断旧服务连接。

- [ ] Step 4: 运行协议测试确认通过

    Run:
    uv run pytest tests/test_speechrail_tts.py -q --no-cov
    uv run ruff check src/sona/speechrail/tts.py tests/test_speechrail_tts.py
    uv run mypy src/sona/speechrail/tts.py

    Expected: 新旧 fake session 均 PASS，已有 response id、event order、cancel 和 API key header 测试不变。

- [ ] Step 5: 提交能力解析主题

    git add src/sona/speechrail/tts.py tests/test_speechrail_tts.py
    git commit -m "feat(tts): consume SpeechRail loudness capability"

### Task 2: 实现 Sona PCM16 流式 guard

**Files:**
- Create: src/sona/speechrail/tts_loudness.py
- Create: tests/test_speechrail_tts_loudness.py

**Interfaces:**
- Produces: Pcm16LoudnessConfig
- Produces: StreamingPcm16LoudnessGuard.process(pcm: bytes) -> bytes
- Produces: StreamingPcm16LoudnessGuard.set_mode(mode: Literal["safety", "compatibility"]) -> None
- Produces: StreamingPcm16LoudnessGuard.reset() -> None

- [ ] Step 1: 写失败测试，覆盖两个模式和 PCM 边界

    def test_guard_rejects_odd_pcm16() -> None:
        guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
        with pytest.raises(ValueError, match="PCM16 payload length must be even"):
            guard.process(b"\x00")

    def test_safety_mode_does_not_raise_quiet_audio() -> None:
        guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
        guard.set_mode("safety")
        chunk = _constant_pcm16(0.05, 1_920)
        assert guard.process(chunk) == chunk

    def test_compatibility_mode_smooths_alternating_levels() -> None:
        guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
        low = _constant_pcm16(0.05, 1_920)
        high = _constant_pcm16(0.40, 1_920)
        output = [guard.process(chunk) for chunk in (low, high, low, high)]
        assert _rms_jump_p95(output) < 8.0

    def test_guard_limits_peak_without_pcm_wraparound() -> None:
        guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
        output = guard.process(_constant_pcm16(0.99, 1_920))
        assert _peak_dbfs(output) <= -1.0 + 0.1
        assert max(abs(value) for value in _decode_pcm16(output)) <= 32767

    safety 模式的向上增益上限为 0 dB；它只允许 peak ceiling 的有界衰减。compatibility 模式沿用 target -20 dBFS、peak -1 dBFS、calibration 240 ms、最大上拉 +12 dB、最大衰减 -6 dB 和慢速 attack/release。

- [ ] Step 2: 运行测试确认失败

    Run:
    uv run pytest tests/test_speechrail_tts_loudness.py -q --no-cov

    Expected: collection FAIL，因为 sona.speechrail.tts_loudness 尚不存在。

- [ ] Step 3: 实现纯 PCM16 guard

    @dataclass(frozen=True, slots=True)
    class Pcm16LoudnessConfig:
        target_dbfs: float = -20.0
        peak_ceiling_dbfs: float = -1.0
        calibration_ms: int = 240
        attack_ms: int = 250
        release_ms: int = 800
        max_gain_db: float = 12.0
        max_attenuation_db: float = -6.0

    class StreamingPcm16LoudnessGuard:
        def __init__(
            self,
            *,
            sample_rate: int,
            config: Pcm16LoudnessConfig | None = None,
        ) -> None:
            raise NotImplementedError

        def process(self, pcm: bytes) -> bytes:
            raise NotImplementedError

        def set_mode(self, mode: Literal["safety", "compatibility"]) -> None:
            raise NotImplementedError

        def reset(self) -> None:
            raise NotImplementedError

    process() 校验偶数字节后按 little-endian PCM16 解码；compatibility 模式只在有限前导窗口和有效语音 RMS 上计算目标增益，后续以 attack/release 平滑；safety 模式不做向上拉升；两种模式都在 chunk 内插值增益并执行 peak ceiling。近静音块不更新目标，也不放大噪声。未知 mode 直接 ValueError。

- [ ] Step 4: 运行 guard 测试确认通过

    Run:
    uv run pytest tests/test_speechrail_tts_loudness.py -q --no-cov
    uv run ruff check src/sona/speechrail/tts_loudness.py tests/test_speechrail_tts_loudness.py
    uv run mypy src/sona/speechrail/tts_loudness.py

    Expected: 所有测试 PASS，输出不回绕、不产生奇数 PCM payload，reset 后下一次请求不会继承上一请求的 gain。

- [ ] Step 5: 提交纯逻辑主题

    git add src/sona/speechrail/tts_loudness.py tests/test_speechrail_tts_loudness.py
    git commit -m "fix(tts): add stateful PCM playback guard"

### Task 3: 将 guard 接入 SpeechRailTTSClient 生命周期

**Files:**
- Modify: src/sona/speechrail/tts.py:80-150
- Test: tests/test_speechrail_tts.py
- Test: tests/test_speechrail_tts_service.py

**Interfaces:**
- Consumes: StreamingPcm16LoudnessGuard、Task 1 的 profile selection
- Produces: 每个 synthesize() 请求独立且可清理的 guarded PCM stream

- [ ] Step 1: 写失败集成测试

    让 fake connection 返回四个 80 ms delta，幅度为 [0.05, 0.40, 0.05, 0.40]；分别放入 stable profile 和无 profile 两组 session.created。

    def test_tts_client_applies_one_guard_across_all_audio_deltas() -> None:
        connection = FakeSpeechConnection(
            session_capabilities={"audio_loudness_profile": "stable_loudness_v1"},
            audio_deltas=_alternating_pcm_chunks(),
        )
        client = _client(connection, voice="clone-test")
        chunks = [chunk async for chunk in client.synthesize("测试")]
        assert len(chunks) == 4
        assert _peak_dbfs(b"".join(chunks)) <= -1.0 + 0.1
        assert _rms_jump_p95(chunks) <= _builtin_jump_baseline + 2.0

    def test_tts_client_resets_guard_after_cancel() -> None:
        connection = BlockingSpeechConnection()
        client = _client(connection, voice="clone-test")
        task = asyncio.create_task(_consume(client))
        await connection.response_created.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.active_response_id is None

- [ ] Step 2: 运行测试确认失败

    Run:
    uv run pytest tests/test_speechrail_tts.py tests/test_speechrail_tts_service.py -q --no-cov

    Expected: guarded output 尚未接入，交替 chunk 的输出指标仍等于 raw baseline，测试 FAIL。

- [ ] Step 3: 在 synthesize() 建立一次 guard 并处理 delta

    连接建立后创建 guard，初始模式为 compatibility；事件循环收到 session.created 后调用 set_mode("safety") 或保持 compatibility；收到 response.audio.delta 时按以下顺序处理：

    raw_pcm = decode_pcm16(event.get("delta"))
    guarded_pcm = guard.process(raw_pcm)
    yield guarded_pcm

    在 finally 中调用 guard.reset()，并保留现有 active_response_id 清理、response.cancel、transport.close 和 error code 行为。一个 response 只创建一个 guard；不能在每个 delta、每个 sentence 或每个 TTSAudioRawFrame 创建新对象。

- [ ] Step 4: 验证 Sona Pipecat frame 不变

    保留 src/sona/interaction/tts.py 中 TTSAudioRawFrame 的 sample_rate=24_000、num_channels=1、context_id=context_id。补充测试断言 guard 只改变 audio bytes，不改变 frame 数量和 frame metadata；SpeechRailProtocolError、CancelledError 和 cleanup 行为与现有测试一致。

- [ ] Step 5: 运行 Python 交互门禁

    Run:
    uv run pytest tests/test_speechrail_tts.py tests/test_speechrail_tts_service.py tests/test_pipeline_dependencies.py -q --no-cov
    uv run ruff check src/sona/speechrail/tts.py src/sona/interaction/tts.py tests/test_speechrail_tts.py tests/test_speechrail_tts_service.py
    uv run mypy src/sona/speechrail/tts.py src/sona/interaction/tts.py

    Expected: 全部 PASS；稳定输出 transport 的采样率、buffer 和音频生命周期无回归。

- [ ] Step 6: 提交 client 接线主题

    git add src/sona/speechrail/tts.py src/sona/interaction/tts.py tests/test_speechrail_tts.py tests/test_speechrail_tts_service.py
    git commit -m "fix(tts): guard cloned voice playback stream"

### Task 4: 跨仓库能力契约和回退验证

**Files:**
- Modify: src/sona/speechrail/tts.py
- Modify: src/sona/speechrail/transport.py only if event helper needs a typed extraction boundary
- Test: tests/test_speechrail_tts.py
- Test: tests/test_speechrail_tts.py
- Modify: docs/superpowers/plans/2026-09-08-clone-tts-loudness-stability.md

**Interfaces:**
- Consumes: SpeechRail#34 的 speech_capabilities.audio_loudness_profile
- Produces: old SpeechRail compatibility mode and stable SpeechRail safety-only mode

- [ ] Step 1: 锁定未声明 capability 的兼容行为

    fake session.created 不带 speech_capabilities.audio_loudness_profile 时，Sona 必须继续建立 TTS response；只记录内部模式为 compatibility，不发送任何 proprietary request 字段，不改变标准 OpenAI Realtime outbound events。

- [ ] Step 2: 锁定未知 capability 的降级行为

    profile 为 stable_loudness_v0、空字符串、数字或嵌套错误结构时，按 compatibility 处理并保持 session 可用；不能抛出裸 KeyError/TypeError，也不能把未知 profile 直接当成 stable。

- [ ] Step 3: 运行双仓库契约验证

    SpeechRail:
    uv run --extra dev pytest tests/test_realtime_openai.py tests/test_tts_voice_clone.py -q --no-cov

    Sona:
    uv run pytest tests/test_speechrail_tts.py tests/test_speechrail_tts_service.py -q --no-cov

    Expected: SpeechRail 声明稳定能力时 Sona safety-only；旧服务无能力字段时仍能完成 response.done。

- [ ] Step 4: 回链 issue

    在 SpeechRail#34 评论 Sona commit hash 和验证结果；在 Sona#10 评论 SpeechRail commit hash、部署的 capability 名称和真实 smoke 结果。未部署上游时不得把 compatibility 结果写成最终验收。

### Task 5: 真实本机 Realtime 与扬声器验收

**Files:**
- Create: docs/operations/clone-tts-loudness-acceptance-2026-09-08.md
- Modify: docs/operations/README.md only when the acceptance entry point is added

- [ ] Step 1: 准备不落盘的统计 smoke

    运行授权的本机 realtime client，使用 3 段不同长度/标点结构的中文文本；每个 clone 和一个内置音色各串行 3 次。只在内存中计算 guarded PCM 的 chunk count、duration、active RMS p10/p50/p90、相邻跳变 P95、peak ceiling count 和首包延迟。

- [ ] Step 2: 验收数值

    clone active RMS 中位数在共享目标 -20 dBFS ±3 dB；相邻块跳变 P95 不高于内置音色基线 +2 dB；peak <= -1 dBFS；response.done=completed；首包延迟和总时长相对修复前没有异常回归。

- [ ] Step 3: 验收真实播放

    使用当前默认输出设备试听短句和长句，覆盖正常完成、连续两轮播报、cancel/interruption。确认无可闻 pumping、click、爆音、截断和明显音色变薄。只记录结论与统计，不保存录音。

- [ ] Step 4: 回退验证与文档

    Sona 仅回退自己的 release；SpeechRail 通过其 managed runtime 回退。验证 /health、/readyz、Sona status 和 TTS response。文档写入 issue 链接、版本、统计摘要、未验证项和回退路径，不写 API key、原始文本或音频。

- [ ] Step 5: 提交验收文档

    git add docs/operations/clone-tts-loudness-acceptance-2026-09-08.md docs/operations/README.md
    git commit -m "docs(tts): record clone loudness playback acceptance"

### Task 6: Sona 全量质量门禁

**Files:**
- No production file changes; verify all files from Tasks 1-5

- [ ] Step 1: 运行后端门禁

    SONA_TEST_DATABASE_URL=postgresql:///knowledge uv run pytest tests/
    uv run mypy src/
    uv run ruff check src/ tests/

- [ ] Step 2: 运行前端门禁

    cd ui && npm test -- --run
    cd ui && npm run build

- [ ] Step 3: 检查差异与敏感内容

    git diff --check
    git status --short

    Expected: 只有本 issue 目标文件进入提交；未出现 PCM、Base64、API key、绝对模型路径或用户参考音频。

- [ ] Step 4: 完成 issue 状态回报

    在 Sona#10 回报测试命令、SpeechRail#34 的 commit/deployment 状态和真实试听结论；只有双仓库都达到验收标准后关闭对应 issue。
