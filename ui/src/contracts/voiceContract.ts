export type VoiceMode = "system" | "clone" | "instruction";

export type VoiceQualityStatus = "unevaluated" | "pass" | "warn" | "reject";

export interface VoiceReferenceQuality {
  readonly duration_seconds?: number;
  readonly sample_rate?: number;
  readonly channels?: number;
  readonly speech_active_ratio?: number;
  readonly noise_floor_dbfs?: number;
  readonly estimated_snr_db?: number;
  readonly clipping_ratio?: number;
  readonly leading_silence_seconds?: number;
  readonly trailing_silence_seconds?: number;
  readonly transcript_match?: number;
}

export interface VoiceSynthesisQuality {
  readonly probe_count?: number;
  readonly successful_probe_count?: number;
  readonly active_rms_dbfs?: number;
  readonly peak_dbfs?: number;
  readonly chunk_jump_p95_db?: number;
  readonly clipping_ratio?: number;
  readonly deterministic?: boolean;
  readonly transcript_match?: number;
}

export interface VoiceQualityReport {
  readonly policy_version: string;
  readonly status: VoiceQualityStatus;
  readonly run_id: string;
  readonly tested_at: string;
  readonly reference?: VoiceReferenceQuality;
  readonly synthesis?: VoiceSynthesisQuality;
  readonly failure_codes: readonly string[];
}

export interface VoiceCapabilities {
  readonly supports_speaker?: boolean;
  readonly supports_clone?: boolean;
  readonly supports_preview?: boolean;
  readonly supports_instruction?: boolean;
}

export interface VoiceModelCapabilities {
  readonly supports_preview?: boolean;
  readonly supports_clone?: boolean;
  readonly supports_instruction?: boolean;
}

export type VoiceModelCapability = keyof VoiceModelCapabilities;

/** Missing capability metadata stays backward-compatible until SpeechRail is upgraded. */
export function supportsVoiceCapability(
  capabilities: VoiceModelCapabilities | undefined,
  capability: VoiceModelCapability,
): boolean {
  return capabilities?.[capability] !== false;
}

export interface VoiceCatalogItem {
  readonly id: string;
  readonly name: string;
  readonly instruction?: string;
  readonly is_system: boolean;
  readonly mode?: VoiceMode;
  readonly ref_text?: string;
  readonly duration_seconds?: number;
  readonly created_at?: number;
  readonly available?: boolean;
  readonly capabilities?: VoiceCapabilities;
  readonly quality?: VoiceQualityReport;
  readonly creation?: VoiceCreation;
}

export interface VoiceModelCatalogItem {
  readonly id: string;
  readonly object?: string;
  readonly owned_by?: string;
  readonly created?: number;
  readonly resolves_to?: string;
  readonly profile?: string | null;
  readonly artifact?: string;
  readonly source_model?: string;
  readonly family?: string;
  readonly variant?: string;
  readonly capabilities?: VoiceModelCapabilities;
}

export interface VoiceClonePrompt {
  readonly id: string;
  readonly category: string;
  readonly title: string;
  readonly script: string;
  readonly tips: string;
}

export interface VoicePreviewRequest {
  readonly model: string;
  readonly input: string;
  readonly instruction: string;
  readonly seed?: number;
  readonly speed?: number;
  readonly language?: string;
  readonly response_format?: string;
}

export interface SpeechRequest {
  readonly model: string;
  readonly input: string;
  readonly voice: string;
  readonly speed?: number;
  readonly language?: string;
  readonly response_format?: string;
  readonly instructions?: string;
}

export interface VoiceCreateRequest {
  readonly name: string;
  readonly instruction: string;
}

/** Provenance for prompt-generated references; mode remains clone for Base routing. */
export interface VoiceCreation {
  readonly origin: "generated";
  readonly method: "voice_design_reference_v1";
  readonly model_artifact: string;
  readonly model_revision: string;
  readonly seed: number;
  readonly instruction_sha256: string;
  readonly reference_text_sha256: string;
  readonly reference_audio_sha256: string;
  readonly preprocessing_version: "energy_v1";
}

export interface VoiceDesignRequest {
  readonly id: string;
  readonly name: string;
  readonly instruction: string;
  readonly reference_text: string;
  readonly seed: number;
  readonly language: "zh";
}

export interface VoiceDesignResponse {
  readonly voice: VoiceCatalogItem;
  readonly synthesis_validation: "unevaluated";
}

/** Reference-only pass must never be presented as accepted synthesized output. */
export function synthesisQuality(report?: VoiceQualityReport): VoiceQualityReport | undefined {
  return report?.synthesis && (report.synthesis.probe_count ?? 0) > 0 ? report : undefined;
}

export function hasAcceptedSynthesis(voice: VoiceCatalogItem): boolean {
  const report = synthesisQuality(voice.quality);
  return report?.status === "pass" && report.failure_codes.length === 0
    && Number.isInteger(report.synthesis?.probe_count)
    && (report.synthesis?.probe_count ?? 0) >= 18
    && report.synthesis?.deterministic === true
    && typeof report.synthesis.transcript_match === "number"
    && Number.isFinite(report.synthesis.transcript_match)
    && report.synthesis.transcript_match >= 0 && report.synthesis.transcript_match <= 1
    && report.synthesis.successful_probe_count === report.synthesis.probe_count;
}
