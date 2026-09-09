import { describe, expect, it } from "vitest";
import {
  calculateVoiceSignalMetrics,
  evaluateVoiceSignal,
  summarizeAnalyserFrame,
  type VoiceSignalMetrics,
} from "./voiceQuality";

function metrics(overrides: Partial<VoiceSignalMetrics> = {}): VoiceSignalMetrics {
  return {
    duration_seconds: 8.4,
    speech_active_ratio: 0.78,
    noise_floor_dbfs: -52,
    estimated_snr_db: 28,
    clipping_ratio: 0,
    leading_silence_seconds: 0.2,
    trailing_silence_seconds: 0.3,
    ...overrides,
  };
}

describe("voiceQuality", () => {
  it("summarizes an analyser frame without retaining samples", () => {
    expect(summarizeAnalyserFrame(new Float32Array([0.5, -0.5, 0, 0]))).toEqual({
      rms: 0.3535533905932738,
      peak: 0.5,
    });
  });

  it("accepts a clean reference recording", () => {
    expect(evaluateVoiceSignal(metrics())).toMatchObject({
      status: "pass",
      failure_codes: [],
    });
  });

  it("rejects silence and very short recordings with actionable advice", () => {
    const result = evaluateVoiceSignal(metrics({
      duration_seconds: 1.4,
      speech_active_ratio: 0.02,
      estimated_snr_db: 0,
    }));

    expect(result.status).toBe("reject");
    expect(result.failure_codes).toEqual(expect.arrayContaining([
      "audio_too_short",
      "speech_not_detected",
      "low_snr",
    ]));
    expect(result.primary_action).toContain("重新录音");
  });

  it("warns on moderate noise and long leading silence", () => {
    const result = evaluateVoiceSignal(metrics({
      noise_floor_dbfs: -40,
      estimated_snr_db: 18,
      leading_silence_seconds: 1.1,
    }));

    expect(result.status).toBe("warn");
    expect(result.failure_codes).toEqual(expect.arrayContaining([
      "high_noise_floor",
      "low_snr",
      "leading_silence",
    ]));
  });

  it("rejects clipping and never treats missing measurements as a pass", () => {
    expect(evaluateVoiceSignal(metrics({ clipping_ratio: 0.002 })).status).toBe("reject");
    expect(evaluateVoiceSignal({}).status).toBe("unevaluated");
  });

  it("derives bounded signal metrics from in-memory PCM samples", () => {
    const samples = new Float32Array(48000);
    for (let i = 0; i < samples.length; i += 1) {
      const amplitude = i < 4800 ? 0.002 : 0.2;
      samples[i] = i % 2 === 0 ? amplitude : -amplitude;
    }
    const result = calculateVoiceSignalMetrics(samples, 48_000);

    expect(result.duration_seconds).toBe(1);
    expect(result.speech_active_ratio).toBeGreaterThan(0.9);
    expect(result.clipping_ratio).toBe(0);
    expect(result.estimated_snr_db).toBeGreaterThan(15);
  });
});
