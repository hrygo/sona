import { afterEach, describe, expect, it, vi } from "vitest";
import {
  analyzeLoudness,
  encodeWav16Mono,
  normalizeRecordingForClone,
  normalizeSamples,
  readBlobAsArrayBuffer,
  resolveGain,
  SilentRecordingError,
} from "./audioNormalize";

/** period=4 的正弦采样 [0, A, 0, -A] 为整周期，RMS 精确等于 A/√2。 */
function makeSine(amplitude: number, totalSamples: number): Float32Array {
  const samples = new Float32Array(totalSamples);
  for (let i = 0; i < totalSamples; i++) {
    samples[i] = amplitude * Math.sin((2 * Math.PI * (i % 4)) / 4);
  }
  return samples;
}

function rmsOf(samples: Float32Array): number {
  let squared = 0;
  for (let i = 0; i < samples.length; i++) {
    squared += samples[i] * samples[i];
  }
  return Math.sqrt(squared / samples.length);
}

async function readWav(blob: Blob) {
  const view = new DataView(await readBlobAsArrayBuffer(blob));
  const ascii = (offset: number, length: number) => {
    let text = "";
    for (let i = 0; i < length; i++) {
      text += String.fromCharCode(view.getUint8(offset + i));
    }
    return text;
  };
  const samples = new Int16Array(view.buffer, 44, (view.byteLength - 44) / 2);
  return {
    riff: ascii(0, 4),
    wave: ascii(8, 4),
    fmt: ascii(12, 4),
    audioFormat: view.getUint16(20, true),
    channels: view.getUint16(22, true),
    sampleRate: view.getUint32(24, true),
    byteRate: view.getUint32(28, true),
    bitsPerSample: view.getUint16(34, true),
    data: ascii(36, 4),
    dataSize: view.getUint32(40, true),
    samples,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("analyzeLoudness", () => {
  it("removes DC offset before measuring RMS and peak", () => {
    const samples = new Float32Array(1000);
    for (let i = 0; i < samples.length; i++) {
      samples[i] = (i % 2 === 0 ? 0.5 : -0.5) + 0.1;
    }
    const { rms, peak, dcOffset } = analyzeLoudness(samples);
    expect(dcOffset).toBeCloseTo(0.1, 6);
    expect(rms).toBeCloseTo(0.5, 5);
    expect(peak).toBeCloseTo(0.5, 5);
  });

  it("returns zero loudness for empty samples", () => {
    const { rms, peak, dcOffset } = analyzeLoudness(new Float32Array(0));
    expect(rms).toBe(0);
    expect(peak).toBe(0);
    expect(dcOffset).toBe(0);
  });
});

describe("resolveGain", () => {
  it("scales quiet recordings up to the target RMS", () => {
    expect(resolveGain(0.01)).toBeCloseTo(10, 5);
  });

  it("scales loud recordings down to the target RMS", () => {
    expect(resolveGain(0.33)).toBeCloseTo(0.1 / 0.33, 5);
  });

  it("clamps the gain to the +24 dB ceiling", () => {
    expect(resolveGain(0.001)).toBeCloseTo(10 ** (24 / 20), 5);
  });

  it("clamps the gain to the -12 dB floor", () => {
    expect(resolveGain(1)).toBeCloseTo(10 ** (-12 / 20), 5);
  });

  it("returns unity gain for silence instead of amplifying noise", () => {
    expect(resolveGain(0)).toBe(1);
  });
});

describe("normalizeSamples", () => {
  it("amplifies a quiet sine to the target RMS of -20 dBFS", () => {
    const quiet = makeSine(0.02, 4000);
    const result = normalizeSamples(quiet);
    expect(rmsOf(result.samples)).toBeCloseTo(0.1, 3);
    expect(result.gain).toBeCloseTo(0.1 / (0.02 / Math.SQRT2), 2);
    expect(result.peakAfter).toBeLessThan(10 ** (-1 / 20));
  });

  it("shrinks the overall gain to respect the -1 dBFS peak ceiling", () => {
    const sparseSpike = new Float32Array(1000);
    sparseSpike.fill(0.01);
    sparseSpike[500] = 0.95;
    const result = normalizeSamples(sparseSpike);
    expect(result.peakAfter).toBeCloseTo(10 ** (-1 / 20), 3);
    expect(result.gain).toBeLessThan(0.1 / rmsOf(sparseSpike));
  });

  it("throws SilentRecordingError for near-silent audio", () => {
    expect(() => normalizeSamples(new Float32Array(1000))).toThrow(SilentRecordingError);
    expect(() => normalizeSamples(makeSine(0.0005, 4000))).toThrow(SilentRecordingError);
  });
});

describe("encodeWav16Mono", () => {
  it("writes a standard 44-byte mono PCM WAV header", async () => {
    const blob = encodeWav16Mono(makeSine(0.5, 100), 48000);
    const wav = await readWav(blob);
    expect(blob.type).toBe("audio/wav");
    expect(wav.riff).toBe("RIFF");
    expect(wav.wave).toBe("WAVE");
    expect(wav.fmt).toBe("fmt ");
    expect(wav.audioFormat).toBe(1);
    expect(wav.channels).toBe(1);
    expect(wav.sampleRate).toBe(48000);
    expect(wav.byteRate).toBe(96000);
    expect(wav.bitsPerSample).toBe(16);
    expect(wav.data).toBe("data");
    expect(wav.dataSize).toBe(200);
    expect(wav.samples.length).toBe(100);
  });

  it("quantizes samples into int16 with headroom clamping", async () => {
    const blob = encodeWav16Mono(new Float32Array([0.5, -0.5, 1, -1, 2]), 24000);
    const { samples } = await readWav(blob);
    expect(Array.from(samples)).toEqual([16384, -16384, 32767, -32768, 32767]);
  });
});

describe("normalizeRecordingForClone", () => {
  function stubAudioContextWith(buffer: AudioBuffer, options: { decodeFails?: boolean } = {}) {
    vi.stubGlobal("AudioContext", class {
      decodeAudioData(): Promise<AudioBuffer> {
        if (options.decodeFails) {
          return Promise.reject(new TypeError("decode failed"));
        }
        return Promise.resolve(buffer);
      }

      close(): Promise<void> {
        return Promise.resolve();
      }
    });
  }

  it("mixes stereo input to mono and normalizes to the target loudness", async () => {
    const left = new Float32Array(2000);
    const right = new Float32Array(2000);
    for (let i = 0; i < left.length; i++) {
      left[i] = i % 2 === 0 ? 0.25 : -0.25;
      right[i] = i % 2 === 0 ? 0.15 : -0.15;
    }
    const fakeBuffer = {
      sampleRate: 48000,
      numberOfChannels: 2,
      length: left.length,
      duration: left.length / 48000,
      getChannelData: (channel: number) => (channel === 0 ? left : right),
    } as unknown as AudioBuffer;
    stubAudioContextWith(fakeBuffer);

    const source = new Blob(["raw"], { type: "audio/webm" });
    const { blob, metrics } = await normalizeRecordingForClone(source);

    expect(blob.type).toBe("audio/wav");
    const wav = await readWav(blob);
    expect(wav.sampleRate).toBe(48000);
    expect(wav.channels).toBe(1);
    // (0.25 + 0.15) / 2 = 0.2 RMS → 增益 0.5 → 输出 RMS 0.1（-20 dBFS）
    expect(metrics.gainDb).toBeCloseTo(-6.02, 2);
    expect(metrics.rmsAfterDb).toBeCloseTo(-20, 2);
    expect(wav.samples[0]).toBe(Math.round(0.1 * 0x7fff));
    expect(metrics.durationSeconds).toBeCloseTo(2000 / 48000, 6);
  });

  it("propagates decode failures so callers can fall back to the raw recording", async () => {
    stubAudioContextWith({} as AudioBuffer, { decodeFails: true });
    await expect(normalizeRecordingForClone(new Blob(["raw"]))).rejects.toThrow(TypeError);
  });

  it("surfaces silent recordings as SilentRecordingError", async () => {
    const silentBuffer = {
      sampleRate: 48000,
      numberOfChannels: 1,
      length: 1000,
      getChannelData: () => new Float32Array(1000),
    } as unknown as AudioBuffer;
    stubAudioContextWith(silentBuffer);
    await expect(normalizeRecordingForClone(new Blob(["raw"]))).rejects.toThrow(SilentRecordingError);
  });
});
