export type VoiceMode = "system" | "clone" | "instruction";

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
