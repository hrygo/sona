# Clone TTS 响度播放验收记录

> 状态：`partial`。本记录覆盖 Sona 播放边界与不落盘的实时 PCM 统计；SpeechRail
> 稳定响度能力和真实扬声器试听仍待完成。

## 范围与环境

- 关联：`hrygo/sona#10`、`hrygo/SpeechRail#34`。
- 日期：2026-09-08（Asia/Shanghai）。
- Sona：`fix/tts-clone-loudness-issue-10`，guard 与客户端接线已提交。
- SpeechRail：本机 `1.13.1`，`quality` profile；`/health`、`/readyz` 均返回 ready。
- 本次运行服务返回 `speech_capabilities.audio_loudness_profile` 缺失，因此 custom voice
  使用 bounded compatibility mode；已知内置 preset 保持 PCM passthrough，未发送任何
  proprietary Realtime 字段。

## 自动化验证

- TTS/guard/config/pipeline focused tests：75 passed。
- `mypy src/`：110 个源文件通过。
- `ruff check src/ tests/`：通过。
- 前端 `npm test -- --run`：353 passed。
- 前端 `npm run build`：通过。
- 全量后端：1159 passed，覆盖率 83.69%。新增回归覆盖 `AudioHub` 的阻塞式
  `PyAudio.open()` 超时，以及 `/api/services` 探针测试不触碰真实音频设备。

兼容模式参数已通过 `SONA_INTERACTION_TTS_LOUDNESS_*` 配置项注入生产 TTS client，且
target、peak ceiling、最大增益/衰减和时间常数均有边界校验。

## 实时 PCM 统计

使用 3 段不同长度/标点的中文文本，clone 与内置音色各串行运行 3 次。统计仅在内存中
完成，不保存文本、PCM、Base64、参考音频或完整响度序列。

| 音色类别 | 运行 | chunk 数 | 时长 (ms) | 首 chunk (ms) | active RMS P50 (dBFS) | 相邻块 P95 (dB) | peak (dBFS) |
|---|---:|---:|---:|---:|---:|---:|---:|
| clone | 1 | 47 | 3760 | 145.8 | -21.44 | 11.18 | -3.25 |
| clone | 2 | 122 | 9760 | 95.1 | -22.12 | 17.84 | -3.58 |
| clone | 3 | 54 | 4320 | 92.0 | -19.79 | 14.72 | -3.21 |
| builtin | 1 | 36 | 2880 | 75.6 | -18.66 | 14.20 | -7.92 |
| builtin | 2 | 94 | 7520 | 74.5 | -20.72 | 16.42 | -4.58 |
| builtin | 3 | 35 | 2800 | 79.6 | -17.85 | 24.23 | -7.06 |

数值结果：clone 三次 active RMS 均在共享目标 `-20±3 dBFS` 内；逐文本比较时，clone
相邻块跳变 P95 均不高于对应内置基线 `+2 dB`；6 次运行的 peak 均低于 `-1 dBFS`。
所有请求均正常收到 `response.done`，否则客户端不会结束 `synthesize()`。

## 未完成项与回退

- SpeechRail#34 尚未在本次运行实例声明 `stable_loudness_v1`，因此未能验证 Sona 的
  safety-only 双重归一化保护。
- 未通过真实扬声器完成主观试听，也未在真实设备上覆盖 cancel/interruption。
- 发生问题时仅回退 Sona 的对应 release；SpeechRail 通过其 managed runtime 回退。
