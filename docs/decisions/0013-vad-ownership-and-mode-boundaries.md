---
title: "ADR-0013：按运行模式划分 VAD 所有权"
description: "明确 Sona 交互 VAD 与 SpeechRail 字幕/会议 server VAD 的职责边界，避免双重 endpointing"
status: accepted
type: decision_record
category: architecture
date: 2026-09-09
last_updated: 2026-09-09
author: "Voice Realtime Core Team"
owners:
  - "sona-core"
tags:
  - adr
  - vad
  - speechrail
  - subtitles
  - meeting
related_documents:
  - "docs/architecture/实时语音交互与字幕-方案与最佳实践.md"
  - "docs/architecture/系统总体架构与详细设计方案.md"
  - "../../../SpeechRail/docs/decisions/0013-realtime-vad-and-diarization-boundary.md"
---

# ADR-0013：按运行模式划分 VAD 所有权

## 状态

Accepted — 2026-09-09

## 背景

Sona 同时包含 Pipecat 语音助手和 SpeechRail Realtime 字幕/会议链路。三条链路都需要“何时结束一段语音”的能力，但它们的下游所有者不同。若 Sona 和 SpeechRail 在同一字幕/会议 PCM 上各自做 endpointing，会产生提前 commit、重复空 item、尾部丢字和分人修订时序冲突。

## 决策

- `assistant` 模式由 Sona/Pipecat 本地 `SileroVADAnalyzer` 负责起止与回合提交；交互 SpeechRail session 使用 `manual`，不重复启用 `server_vad`。
- `subtitles` 模式由 SpeechRail `server_vad` 负责 endpointing，Sona 发送 threshold `0.65`、prefix `300ms`、silence `400ms`，只消费 SpeechRail 的 speech boundary、completed 和 diarization 事件。
- `meeting` 模式由 SpeechRail `server_vad` 负责 endpointing，Sona 发送相同 threshold/prefix、silence `900ms`；会议分人 activity 独立消费连续 PCM，不把 diarization 当作 VAD。
- SpeechRail 运行时使用一个 VAD 评分器和 `SpeechAdmission` 状态机；后者是边界决策层，不是第二个独立 VAD。
- 所有模式继续受 `RuntimeModeCoordinator` 单一 PCM owner 约束，禁止 assistant、subtitles、meeting 并行消费同一麦克风流。

## 后果

- 会议的 `900ms` 是 endpointing 目标，不是分人时间窗；16kHz/32ms 帧量化后实际停止边界约为 `928ms`。标准字幕相应约为 `416ms`。
- assistant 的 `silence_secs=0.45` 不会渗透到会议或字幕配置。
- VAD readiness、Silero 模型和 speech admission 故障由 SpeechRail health/preflight 报告；Sona 不安装或启动第二个 VAD runtime。
- 若未来调整阈值或静音窗口，必须分别验证 assistant、subtitle、meeting 三种模式，不得以一个全局静音值替换三种策略。
