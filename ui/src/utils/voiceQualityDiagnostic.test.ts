import { describe, expect, it } from "vitest";
import { classifyVoiceNoiseEvidence } from "./voiceQualityDiagnostic";

describe("voiceQualityDiagnostic", () => {
  it("prioritizes a playback failure when SpeechRail output is clean", () => {
    expect(classifyVoiceNoiseEvidence({
      speechrailOutputClean: true,
      playbackOutputAbnormal: true,
    })).toBe("playback_device");
  });

  it("attributes a rejected clone reference to reference noise", () => {
    expect(classifyVoiceNoiseEvidence({
      voiceQualityStatus: "reject",
    })).toBe("reference_noise");
  });

  it("detects echo risk before falling back to insufficient evidence", () => {
    expect(classifyVoiceNoiseEvidence({ echoDetected: true })).toBe("echo_risk");
    expect(classifyVoiceNoiseEvidence({})).toBe("insufficient_evidence");
  });
});
