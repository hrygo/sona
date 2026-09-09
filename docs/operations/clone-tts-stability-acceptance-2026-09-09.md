---
title: "Sona × SpeechRail 克隆音色稳定性验收"
description: "SpeechRail v2.0.3 clone TTS 的确定性采样、响度稳定、参考音频质量和速度能力边界验收"
status: completed
type: acceptance_report
category: tts
version: "1.0.0"
date: 2026-09-09
last_updated: 2026-09-09
owners: [sona-core]
tags: [speechrail, tts, clone, loudness, acceptance]
---

# Sona × SpeechRail 克隆音色稳定性验收

## 结论

SpeechRail v2.0.3 的 clone ICL 路径已完成代码、自动化测试和托管运行时复验。连续相同请求的输出保持确定性，响度控制在首个有效片段完成校准后冻结，峰值保护仍持续生效；参考音频的削波、有效语音比例和首尾静音会在写入音色前拒绝。

本报告验证的是“稳定性与交付边界”，不把固定 hash 等同于主观音色质量，也不把当前 clone backend 不支持的任意变速写成已支持能力。

## 当前实现

- clone 生成使用请求级稳定 seed、低温度 `0.1`、`top_p=0.95` 和至少 `1.3` 的 repetition penalty；seed 绑定音色与参考文本，不绑定目标文本，避免同一音色因内容变化而改变采样风格。
- clone PCM 使用请求级响度控制器：首个有效窗口完成校准，后续窗口复用增益；峰值 ceiling 仍独立保护。
- Sona Voice Studio 录制参考音频时关闭浏览器 `echoCancellation`、`noiseSuppression`、`autoGainControl`，降低把处理伪影写入参考音频的概率。
- 参考音频必须是目标采样率的 mono PCM16，并通过削波、有效语音占比以及首尾静音检查。

## 实测摘要

在当前 quality managed runtime 使用同一参考音频和同一文本连续生成 3 次：

| 指标 | clone 结果 |
|---|---:|
| 输出格式 | 24kHz mono PCM16 |
| 输出时长 | 9.840s × 3 |
| 输出 hash | 3 次一致 |
| 总体 RMS | -22.80 dBFS |
| 有效语音 RMS | -21.03 dBFS |
| 峰值 | -3.81 dBFS |
| chunk 边界 | 未发现边界点击型突变证据 |

SpeechRail `/health` 同时报告 `tts_ready=true`、`tts_warm=true`；metrics 可见 clone request、reference cache hit、calibration 与 peak ceiling 事件。

## 能力边界

- 当前 active clone backend 的非 `1.0` speed 不受支持，REST 端明确返回 `clone_speed_unsupported`；不会静默接收后忽略 speed。
- 预置音色的 Realtime speed 扩展已完成协议接入和严格校验，但模型是否对极短文本产生可观测时长变化仍需按目标语料评测，不能仅凭请求被接受宣称变速生效。
- 如果未来需要 clone 任意变速，应新增经过验证的 PCM rate/pitch 后处理阶段，并独立验收音质、音高、边界和实时延迟。

## 证据与回退

- SpeechRail 源码：[克隆 worker](../../../SpeechRail/src/speechrail/backends/qwen3_tts_worker.py)、[响度控制](../../../SpeechRail/src/speechrail/domain/tts_loudness.py)、[参考音频校验](../../../SpeechRail/src/speechrail/domain/tts.py)。
- SpeechRail 修改必须在源码库构建 wheel，并通过 managed installer 切换 release；回退使用上一 release，不直接编辑 `runtime/current`。
- 原有 2026-09-08 compatibility-mode 记录保留在 [历史验收记录](clone-tts-loudness-acceptance-2026-09-08.md)，不作为当前 v2.0.3 运行时证据。
