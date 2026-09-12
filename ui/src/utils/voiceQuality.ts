import { readBlobAsArrayBuffer } from "./audioNormalize";

export type VoiceQualityStatus = "unevaluated" | "pass" | "warn" | "reject";

export interface VoiceSignalMetrics {
  readonly duration_seconds?: number;
  readonly rms_dbfs?: number;
  readonly speech_active_ratio?: number;
  readonly noise_floor_dbfs?: number;
  readonly estimated_snr_db?: number;
  readonly clipping_ratio?: number;
  readonly leading_silence_seconds?: number;
  readonly trailing_silence_seconds?: number;
}

export interface LocalVoiceQualityResult {
  readonly status: VoiceQualityStatus;
  readonly failure_codes: readonly string[];
  readonly primary_action: string;
}

export interface AnalyserFrameSummary {
  readonly rms: number;
  readonly peak: number;
}

const METRIC_FRAME_SIZE = 1024;

function dbfs(value: number): number {
  return 20 * Math.log10(Math.max(value, 1e-6));
}

const CORE_MEASUREMENTS: readonly (keyof VoiceSignalMetrics)[] = [
  "duration_seconds",
  "speech_active_ratio",
  "noise_floor_dbfs",
  "estimated_snr_db",
  "clipping_ratio",
];

function isFiniteNumber(value: number | undefined): value is number {
  return value !== undefined && Number.isFinite(value);
}

export function summarizeAnalyserFrame(samples: Float32Array): AnalyserFrameSummary {
  if (samples.length === 0) return { rms: 0, peak: 0 };
  let squaredSum = 0;
  let peak = 0;
  for (const sample of samples) {
    const magnitude = Math.abs(sample);
    squaredSum += sample * sample;
    peak = Math.max(peak, magnitude);
  }
  return {
    rms: Math.sqrt(squaredSum / samples.length),
    peak,
  };
}

export function calculateVoiceSignalMetrics(
  samples: Float32Array,
  sampleRate: number,
): VoiceSignalMetrics {
  if (samples.length === 0 || sampleRate <= 0) return {};
  const frameRms: number[] = [];
  for (let offset = 0; offset < samples.length; offset += METRIC_FRAME_SIZE) {
    const end = Math.min(offset + METRIC_FRAME_SIZE, samples.length);
    let squareSum = 0;
    for (let index = offset; index < end; index += 1) {
      const sample = samples[index] ?? 0;
      squareSum += sample * sample;
    }
    const length = end - offset;
    frameRms.push(Math.sqrt(squareSum / length));
  }

  const sortedRms = [...frameRms].sort((left, right) => left - right);
  const noiseFrameCount = Math.max(1, Math.ceil(sortedRms.length * 0.1));
  const noiseRms = sortedRms.slice(0, noiseFrameCount).reduce((sum, value) => sum + value, 0) / noiseFrameCount;
  const activeThreshold = Math.max(noiseRms * 2, 0.01);
  const activeFrames = frameRms.filter((value) => value > activeThreshold);
  const activeRms = activeFrames.length > 0
    ? Math.sqrt(activeFrames.reduce((sum, value) => sum + value * value, 0) / activeFrames.length)
    : 0;
  const firstActive = frameRms.findIndex((value) => value > activeThreshold);
  let lastActive = -1;
  for (let index = frameRms.length - 1; index >= 0; index -= 1) {
    if ((frameRms[index] ?? 0) > activeThreshold) {
      lastActive = index;
      break;
    }
  }
  const clippingSamples = samples.reduce((count, sample) => count + (Math.abs(sample) >= 0.999 ? 1 : 0), 0);
  return {
    rms_dbfs: dbfs(summarizeAnalyserFrame(samples).rms),
    duration_seconds: samples.length / sampleRate,
    speech_active_ratio: activeFrames.length / frameRms.length,
    noise_floor_dbfs: dbfs(noiseRms),
    estimated_snr_db: dbfs(activeRms) - dbfs(noiseRms),
    clipping_ratio: clippingSamples / samples.length,
    leading_silence_seconds: firstActive < 0 ? samples.length / sampleRate : firstActive * METRIC_FRAME_SIZE / sampleRate,
    trailing_silence_seconds: lastActive < 0
      ? samples.length / sampleRate
      : Math.max(0, (samples.length - (lastActive + 1) * METRIC_FRAME_SIZE) / sampleRate),
  };
}

export async function inspectVoiceRecording(blob: Blob): Promise<VoiceSignalMetrics> {
  const AudioContextClass = window.AudioContext
    ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextClass) throw new Error("当前浏览器不支持音频质量检查");
  const audioContext = new AudioContextClass();
  try {
    const decoded = await audioContext.decodeAudioData(await readBlobAsArrayBuffer(blob));
    const mono = new Float32Array(decoded.length);
    for (let channel = 0; channel < decoded.numberOfChannels; channel += 1) {
      const samples = decoded.getChannelData(channel);
      for (let index = 0; index < mono.length; index += 1) {
        mono[index] = (mono[index] ?? 0) + (samples[index] ?? 0) / decoded.numberOfChannels;
      }
    }
    return calculateVoiceSignalMetrics(mono, decoded.sampleRate);
  } finally {
    await audioContext.close().catch(() => undefined);
  }
}

export function evaluateVoiceSignal(metrics: VoiceSignalMetrics): LocalVoiceQualityResult {
  const measurable = CORE_MEASUREMENTS.some((key) => isFiniteNumber(metrics[key]));
  if (!measurable) {
    return {
      status: "unevaluated",
      failure_codes: [],
      primary_action: "继续录音以获取完整质量检查",
    };
  }

  const rejectCodes: string[] = [];
  const warnCodes: string[] = [];
  const duration = metrics.duration_seconds;
  if (isFiniteNumber(duration)) {
    if (duration < 2) rejectCodes.push("audio_too_short");
    else if (duration > 45) rejectCodes.push("audio_too_long");
    else if (duration < 4 || duration > 30) warnCodes.push("duration_boundary");
  }

  const speechActiveRatio = metrics.speech_active_ratio;
  if (isFiniteNumber(speechActiveRatio)) {
    if (speechActiveRatio < 0.35) rejectCodes.push("speech_not_detected");
    else if (speechActiveRatio < 0.55) warnCodes.push("low_speech_coverage");
  }

  const noiseFloor = metrics.noise_floor_dbfs;
  if (isFiniteNumber(noiseFloor)) {
    if (noiseFloor > -35) rejectCodes.push("high_noise_floor");
    else if (noiseFloor > -45) warnCodes.push("high_noise_floor");
  }

  const snr = metrics.estimated_snr_db;
  if (isFiniteNumber(snr)) {
    if (snr < 15) rejectCodes.push("low_snr");
    else if (snr < 20) warnCodes.push("low_snr");
  }

  const clippingRatio = metrics.clipping_ratio;
  if (isFiniteNumber(clippingRatio)) {
    if (clippingRatio > 0.001) rejectCodes.push("clipping");
    else if (clippingRatio > 0.0001) warnCodes.push("clipping");
  }

  const leadingSilence = metrics.leading_silence_seconds;
  if (isFiniteNumber(leadingSilence)) {
    if (leadingSilence > 1.5) rejectCodes.push("leading_silence");
    else if (leadingSilence > 0.8) warnCodes.push("leading_silence");
  }
  const trailingSilence = metrics.trailing_silence_seconds;
  if (isFiniteNumber(trailingSilence)) {
    if (trailingSilence > 1.5) rejectCodes.push("trailing_silence");
    else if (trailingSilence > 0.8) warnCodes.push("trailing_silence");
  }

  const failureCodes = [...new Set([...rejectCodes, ...warnCodes])];
  const status: VoiceQualityStatus = rejectCodes.length > 0
    ? "reject"
    : warnCodes.length > 0
      ? "warn"
      : "pass";
  const primaryAction = status === "reject"
    ? "请在安静环境重新录音，并让声音保持在正常电平"
    : status === "warn"
      ? "可以继续，但建议降低环境噪声后重新录音"
      : "录音环境良好，可以进行服务端质量检查";
  return { status, failure_codes: failureCodes, primary_action: primaryAction };
}

/** Capture safety only: energy heuristics are not a speech/noise classifier. */
export function evaluateCaptureSafety(metrics: VoiceSignalMetrics): LocalVoiceQualityResult {
  const codes: string[] = [];
  if (metrics.rms_dbfs !== undefined && metrics.rms_dbfs < -60) codes.push("capture_silent");
  if (metrics.duration_seconds !== undefined && metrics.duration_seconds < 2) codes.push("audio_too_short");
  if (metrics.duration_seconds !== undefined && metrics.duration_seconds > 45) codes.push("audio_too_long");
  if (metrics.clipping_ratio !== undefined && metrics.clipping_ratio > 0.001) codes.push("clipping");
  return {
    status: codes.length ? "reject" : "unevaluated", failure_codes: codes,
    primary_action: codes.includes("capture_silent") ? "录音几乎无声，请检查输入设备后重新录制"
      : codes.length ? "参考音频过短、过长或发生削波，请检查后重新录制"
        : "已检查时长与电平；人声、内容与噪声仍需服务端核验，未自动增强音频",
  };
}
