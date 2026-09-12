import { expect, it } from "vitest";
import { VoiceServiceError } from "./voiceService";
import { registrationMayHaveCompleted } from "./voiceWorkflow";
import { hasAcceptedSynthesis, type VoiceCatalogItem } from "../contracts/voiceContract";
it.each(["transcription_unavailable", "backend_not_ready", "output_invalid", "backend_error", "dependency_missing"])(
  "allows correcting a definitively rejected registration: %s", (code) => {
    expect(registrationMayHaveCompleted(new VoiceServiceError("not published", 503, code))).toBe(false);
  },
);
it.each([0, 200, 201, 409, 500, 502, 504])("retains the same attempt for uncertain HTTP %s", (status) => {
  expect(registrationMayHaveCompleted(new VoiceServiceError("uncertain", status, "unknown"))).toBe(true);
});
it("does not freeze editing for a definite mode conflict or invalid input", () => {
  expect(registrationMayHaveCompleted(new VoiceServiceError("meeting", 409, "mode_conflict"))).toBe(false);
  expect(registrationMayHaveCompleted(new VoiceServiceError("bad", 400, "invalid_payload"))).toBe(false);
});
it.each([Number.NaN, Infinity, -0.1, 1.1])("rejects impossible ASR evidence %s without inventing thresholds", (score) => {
  const voice: VoiceCatalogItem = { id: "test", name: "test", is_system: false, quality: {
    policy_version: "v1", run_id: "run", tested_at: "2026-09-12", status: "pass", failure_codes: [],
    synthesis: { probe_count: 18, successful_probe_count: 18, deterministic: true, transcript_match: score },
  } };
  expect(hasAcceptedSynthesis(voice)).toBe(false);
});
