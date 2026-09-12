import { VoiceServiceError } from "./voiceService";

/** These server codes reject before publishing a voice; transport/commit failures are uncertain. */
const PREPUBLICATION_FAILURES = new Set([
  "transcription_unavailable", "backend_not_ready", "output_invalid", "backend_error",
  "dependency_missing", "voice_design_registration_unsupported", "voice_cloning_unsupported",
]);
export function registrationMayHaveCompleted(error: unknown): boolean {
  if (!(error instanceof VoiceServiceError)) return true;
  if (PREPUBLICATION_FAILURES.has(error.code)) return false;
  return error.status === 0 || error.status >= 500 || (error.status >= 200 && error.status < 300)
    || (error.status === 409 && error.code !== "mode_conflict");
}
