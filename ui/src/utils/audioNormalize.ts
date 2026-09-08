/**
 * 录音响度标准化：克隆的是音色而不是音量。
 *
 * 声音工坊录音在提交克隆前统一到固定有效响度（目标 RMS -20 dBFS），
 * 消除"有人声音就是小"带来的电平差异；同时把 MediaRecorder 的
 * 有损 opus/webm 容器转换为无损 16-bit PCM WAV，保留克隆所需音色细节。
 */

/** 录音几乎无声（低于静音阈值）时抛出，组件层应引导用户重新录制。 */
export class SilentRecordingError extends Error {
  constructor(message = "录音几乎无声，请靠近麦克风（15~20cm）后重新录制") {
    super(message);
    this.name = "SilentRecordingError";
  }
}

export interface LoudnessMetrics {
  /** 标准化前有效响度（dBFS）。 */
  readonly rmsBeforeDb: number;
  /** 标准化后有效响度（dBFS）。 */
  readonly rmsAfterDb: number;
  /** 实际施加的总增益（dB，含峰值护栏回缩）。 */
  readonly gainDb: number;
  /** 标准化后峰值（dBFS）。 */
  readonly peakAfterDb: number;
  /** 标准化后时长（秒）。 */
  readonly durationSeconds: number;
  /** 标准化后采样率（Hz）。 */
  readonly sampleRate: number;
}

export interface NormalizedRecording {
  readonly blob: Blob;
  readonly metrics: LoudnessMetrics;
}

export interface NormalizeTuning {
  /** 目标有效响度（线性 RMS），默认 0.1（-20 dBFS）。 */
  readonly targetRms?: number;
  /** 放大增益上限（线性），默认 10^(24/20)（+24 dB），防止把底噪放大成"人声"。 */
  readonly maxGain?: number;
  /** 衰减增益下限（线性），默认 10^(-12/20)（-12 dB）。 */
  readonly minGain?: number;
  /** 峰值上限（线性），默认 10^(-1/20)（-1 dBFS），留出 WAV 量化余量。 */
  readonly peakCeiling?: number;
  /** 静音判定阈值（线性 RMS），默认 0.001（-60 dBFS）。 */
  readonly silenceRms?: number;
}

const DEFAULT_TARGET_RMS = 0.1;
const DEFAULT_MAX_GAIN = 10 ** (24 / 20);
const DEFAULT_MIN_GAIN = 10 ** (-12 / 20);
const DEFAULT_PEAK_CEILING = 10 ** (-1 / 20);
const DEFAULT_SILENCE_RMS = 0.001;

const toDb = (linear: number): number => 20 * Math.log10(Math.max(linear, Number.EPSILON));

interface Loudness {
  readonly rms: number;
  readonly peak: number;
  readonly dcOffset: number;
}

/** 计算去直流后的 RMS 与峰值；RMS/峰值均基于去偏置后的样本。 */
export function analyzeLoudness(samples: Float32Array): Loudness {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    sum += samples[i];
  }
  const dcOffset = samples.length > 0 ? sum / samples.length : 0;

  let squared = 0;
  let peak = 0;
  for (let i = 0; i < samples.length; i++) {
    const value = samples[i] - dcOffset;
    squared += value * value;
    const magnitude = Math.abs(value);
    if (magnitude > peak) {
      peak = magnitude;
    }
  }
  return {
    rms: samples.length > 0 ? Math.sqrt(squared / samples.length) : 0,
    peak,
    dcOffset,
  };
}

export function resolveGain(rms: number, tuning: NormalizeTuning = {}): number {
  const targetRms = tuning.targetRms ?? DEFAULT_TARGET_RMS;
  const maxGain = tuning.maxGain ?? DEFAULT_MAX_GAIN;
  const minGain = tuning.minGain ?? DEFAULT_MIN_GAIN;
  if (rms <= 0) return 1;
  return Math.min(maxGain, Math.max(minGain, targetRms / rms));
}

export interface NormalizedSamples {
  readonly samples: Float32Array;
  readonly gain: number;
  readonly rmsBefore: number;
  readonly peakBefore: number;
  readonly peakAfter: number;
}

/**
 * 纯样本域标准化：去直流 → 增益归一（clamp）→ 峰值护栏整体回缩。
 * 不做逐样本限幅/压扩，避免非线性处理损伤克隆音色。
 */
export function normalizeSamples(
  samples: Float32Array,
  tuning: NormalizeTuning = {},
): NormalizedSamples {
  const peakCeiling = tuning.peakCeiling ?? DEFAULT_PEAK_CEILING;
  const silenceRms = tuning.silenceRms ?? DEFAULT_SILENCE_RMS;
  const { rms, peak, dcOffset } = analyzeLoudness(samples);
  if (rms < silenceRms) {
    throw new SilentRecordingError();
  }

  const gain = resolveGain(rms, tuning);
  const out = new Float32Array(samples.length);
  let peakAfter = 0;
  for (let i = 0; i < samples.length; i++) {
    const value = (samples[i] - dcOffset) * gain;
    const magnitude = Math.abs(value);
    if (magnitude > peakAfter) {
      peakAfter = magnitude;
    }
    out[i] = value;
  }

  let totalGain = gain;
  if (peakAfter > peakCeiling) {
    const shrink = peakCeiling / peakAfter;
    totalGain *= shrink;
    for (let i = 0; i < out.length; i++) {
      out[i] *= shrink;
    }
    peakAfter *= shrink;
  }

  return { samples: out, gain: totalGain, rmsBefore: rms, peakBefore: peak, peakAfter };
}

/** 编码单声道 16-bit PCM WAV（44 字节标准头）。 */
export function encodeWav16Mono(samples: Float32Array, sampleRate: number): Blob {
  const dataSize = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeAscii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeAscii(36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const quantized = Math.round(clamped * (clamped < 0 ? 0x8000 : 0x7fff));
    view.setInt16(offset, quantized, true);
    offset += 2;
  }
  return new Blob([buffer], { type: "audio/wav" });
}

function mixToMono(buffer: AudioBuffer): Float32Array {
  if (buffer.numberOfChannels === 1) {
    return buffer.getChannelData(0).slice();
  }
  const mixed = new Float32Array(buffer.length);
  for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
    const data = buffer.getChannelData(channel);
    for (let i = 0; i < mixed.length; i++) {
      mixed[i] += data[i] / buffer.numberOfChannels;
    }
  }
  return mixed;
}

/** 读取 Blob 二进制数据；对缺少 Blob.arrayBuffer 的旧运行时回退 FileReader。 */
export function readBlobAsArrayBuffer(blob: Blob): Promise<ArrayBuffer> {
  if (typeof blob.arrayBuffer === "function") {
    return blob.arrayBuffer();
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () => reject(reader.error ?? new Error("无法读取录音数据"));
    reader.readAsArrayBuffer(blob);
  });
}

/**
 * 解码录音 Blob 并标准化为 WAV。解码失败抛出原始错误（调用方可降级提交原始录音），
 * 近静音录音抛出 {@link SilentRecordingError}。
 */
export async function normalizeRecordingForClone(
  blob: Blob,
  tuning: NormalizeTuning = {},
): Promise<NormalizedRecording> {
  const AudioContextClass = window.AudioContext
    ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextClass) {
    throw new Error("当前浏览器不支持音频解码");
  }

  const audioCtx = new AudioContextClass();
  try {
    const decoded = await audioCtx.decodeAudioData(await readBlobAsArrayBuffer(blob));
    const { samples, gain, rmsBefore, peakAfter } = normalizeSamples(mixToMono(decoded), tuning);
    return {
      blob: encodeWav16Mono(samples, decoded.sampleRate),
      metrics: {
        rmsBeforeDb: toDb(rmsBefore),
        rmsAfterDb: toDb(rmsBefore * gain),
        gainDb: toDb(gain),
        peakAfterDb: toDb(peakAfter),
        durationSeconds: samples.length / decoded.sampleRate,
        sampleRate: decoded.sampleRate,
      },
    };
  } finally {
    void audioCtx.close().catch(() => undefined);
  }
}
