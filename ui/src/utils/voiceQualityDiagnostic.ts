import type { VoiceQualityStatus } from "../contracts/voiceContract";

export type VoiceNoiseClassification =
  | "reference_noise"
  | "generated_output"
  | "playback_device"
  | "echo_risk"
  | "session_state"
  | "insufficient_evidence";

export interface VoiceNoiseEvidenceInput {
  readonly voiceQualityStatus?: VoiceQualityStatus;
  readonly speechrailOutputClean?: boolean;
  readonly speechrailOutputAbnormal?: boolean;
  readonly playbackOutputAbnormal?: boolean;
  readonly echoDetected?: boolean;
  readonly sessionContextPersisted?: boolean;
}

export function classifyVoiceNoiseEvidence(
  evidence: VoiceNoiseEvidenceInput,
): VoiceNoiseClassification {
  if (evidence.playbackOutputAbnormal && evidence.speechrailOutputClean) return "playback_device";
  if (evidence.speechrailOutputAbnormal) return "generated_output";
  if (evidence.echoDetected) return "echo_risk";
  if (evidence.voiceQualityStatus === "reject") return "reference_noise";
  if (evidence.sessionContextPersisted) return "session_state";
  return "insufficient_evidence";
}

export const NOISE_CLASSIFICATION_LABEL: Record<VoiceNoiseClassification, string> = {
  reference_noise: "参考录音疑似有噪声",
  generated_output: "生成输出异常",
  playback_device: "播放设备或 CoreAudio 异常",
  echo_risk: "检测到回声风险",
  session_state: "会话状态可能未完全清空",
  insufficient_evidence: "证据不足，需要对照音频指标",
};
