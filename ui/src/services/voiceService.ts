import { apiUrl } from "../config/runtimeConfig";
import type {
  SpeechRequest,
  VoiceCatalogItem,
  VoiceClonePrompt,
  VoiceCreateRequest,
  VoiceModelCatalogItem,
  VoiceModelCapabilities,
  VoicePreviewRequest,
} from "../contracts/voiceContract";

export const SPEECHRAIL_TTS_MODEL = "speechrail/qwen3-tts";

export class VoiceServiceError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string,
    readonly details?: Record<string, unknown>,
    readonly retryable = false,
  ) {
    super(message);
    this.name = "VoiceServiceError";
  }
}

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function asBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function parseVoice(value: unknown): VoiceCatalogItem | null {
  if (!isRecord(value)) return null;
  const id = asString(value.id);
  const name = asString(value.name) ?? id;
  if (!id || !name) return null;

  const instruction = asString(value.instruction);
  const mode = value.mode === "system" || value.mode === "clone" || value.mode === "instruction"
    ? value.mode
    : undefined;
  const refText = asString(value.ref_text);
  const durationSeconds = asNumber(value.duration_seconds);
  const createdAt = asNumber(value.created_at);
  const available = asBoolean(value.available);
  const capabilities = isRecord(value.capabilities)
    ? {
      ...(typeof value.capabilities.supports_clone === "boolean"
        ? { supports_clone: value.capabilities.supports_clone }
        : {}),
      ...(typeof value.capabilities.supports_preview === "boolean"
        ? { supports_preview: value.capabilities.supports_preview }
        : {}),
      ...(typeof value.capabilities.supports_instruction === "boolean"
        ? { supports_instruction: value.capabilities.supports_instruction }
        : {}),
      ...(typeof value.capabilities.supports_speaker === "boolean"
        ? { supports_speaker: value.capabilities.supports_speaker }
        : {}),
    }
    : undefined;
  return {
    id,
    name,
    is_system: asBoolean(value.is_system) ?? false,
    ...(instruction ? { instruction } : {}),
    ...(mode ? { mode } : {}),
    ...(refText ? { ref_text: refText } : {}),
    ...(durationSeconds !== undefined ? { duration_seconds: durationSeconds } : {}),
    ...(createdAt !== undefined ? { created_at: createdAt } : {}),
    ...(available !== undefined ? { available } : {}),
    ...(capabilities ? { capabilities } : {}),
  };
}

function parseModelCapabilities(value: unknown): VoiceModelCapabilities | undefined {
  if (!isRecord(value)) return undefined;
  const capabilities = {
    ...(typeof value.supports_preview === "boolean"
      ? { supports_preview: value.supports_preview }
      : {}),
    ...(typeof value.supports_clone === "boolean"
      ? { supports_clone: value.supports_clone }
      : {}),
    ...(typeof value.supports_instruction === "boolean"
      ? { supports_instruction: value.supports_instruction }
      : {}),
  };
  return Object.keys(capabilities).length > 0 ? capabilities : undefined;
}

function parseModel(value: unknown): VoiceModelCatalogItem | null {
  if (!isRecord(value)) return null;
  const id = asString(value.id);
  if (!id) return null;

  const object = asString(value.object);
  const ownedBy = asString(value.owned_by);
  const created = asNumber(value.created);
  const resolvesTo = asString(value.resolves_to);
  const profile = typeof value.profile === "string" || value.profile === null
    ? value.profile
    : undefined;
  const artifact = asString(value.artifact);
  const sourceModel = asString(value.source_model);
  const family = asString(value.family);
  const variant = asString(value.variant);
  const capabilities = parseModelCapabilities(value.capabilities);

  return {
    id,
    ...(object ? { object } : {}),
    ...(ownedBy ? { owned_by: ownedBy } : {}),
    ...(created !== undefined ? { created } : {}),
    ...(resolvesTo ? { resolves_to: resolvesTo } : {}),
    ...(profile !== undefined ? { profile } : {}),
    ...(artifact ? { artifact } : {}),
    ...(sourceModel ? { source_model: sourceModel } : {}),
    ...(family ? { family } : {}),
    ...(variant ? { variant } : {}),
    ...(capabilities ? { capabilities } : {}),
  };
}

function parseModelList(value: unknown): VoiceModelCatalogItem[] {
  if (!isRecord(value) || !Array.isArray(value.data)) return [];
  return value.data
    .map(parseModel)
    .filter((model): model is VoiceModelCatalogItem => model !== null);
}

function parseVoiceList(value: unknown): VoiceCatalogItem[] {
  if (!isRecord(value) || !Array.isArray(value.data)) return [];
  return value.data
    .map(parseVoice)
    .filter((voice): voice is VoiceCatalogItem => voice !== null)
    .filter((voice) => voice.available !== false);
}

function parseClonePrompts(value: unknown): VoiceClonePrompt[] {
  if (!isRecord(value) || !Array.isArray(value.data)) return [];
  return value.data.filter((item): item is VoiceClonePrompt => {
    if (!isRecord(item)) return false;
    return ["id", "category", "title", "script", "tips"].every(
      (key) => typeof item[key] === "string" && item[key].trim().length > 0,
    );
  }) as VoiceClonePrompt[];
}

async function readError(response: Response): Promise<VoiceServiceError> {
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    payload = undefined;
  }

  const root = isRecord(payload) ? payload : {};
  const nested = isRecord(root.error) ? root.error : {};
  const detail = isRecord(root.detail) ? root.detail : {};
  const code =
    asString(nested.code)
    ?? asString(detail.code)
    ?? asString(root.code)
    ?? `http_${response.status}`;
  const message =
    asString(nested.message)
    ?? asString(detail.message)
    ?? asString(root.detail)
    ?? `请求失败 (HTTP ${response.status})`;
  const requestId =
    asString(nested.request_id)
    ?? asString(detail.request_id)
    ?? asString(root.request_id);
  const retryable =
    typeof nested.retryable === "boolean"
      ? nested.retryable
      : typeof detail.retryable === "boolean"
        ? detail.retryable
        : typeof root.retryable === "boolean"
          ? root.retryable
          : response.status >= 500;
  const details = isRecord(nested.details)
    ? nested.details
    : isRecord(detail.details)
      ? detail.details
      : undefined;
  return new VoiceServiceError(message, response.status, code, requestId, details, retryable);
}

async function fetchResponse(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(apiUrl(path), init);
  } catch {
    throw new VoiceServiceError(
      "SpeechRail 服务不可用，请检查服务状态",
      0,
      "speechrail_unavailable",
      undefined,
      undefined,
      true,
    );
  }
}

async function requestJson<T>(path: string, init: RequestInit): Promise<T> {
  const response = await fetchResponse(path, init);
  if (!response.ok) throw await readError(response);
  try {
    return (await response.json()) as T;
  } catch {
    throw new VoiceServiceError(
      "SpeechRail 返回的音色数据无效",
      response.status,
      "invalid_response",
    );
  }
}

async function requestBlob(path: string, init: RequestInit): Promise<Blob> {
  const response = await fetchResponse(path, init);
  if (!response.ok) throw await readError(response);
  try {
    return await response.blob();
  } catch {
    throw new VoiceServiceError(
      "SpeechRail 返回的音频数据无效",
      response.status,
      "invalid_response",
    );
  }
}

function assertVoiceRequest(value: string): void {
  if (!value.trim()) {
    throw new VoiceServiceError(
      "标准语音合成必须提供 voice",
      422,
      "voice_required",
    );
  }
}

export const voiceService = {
  async models(): Promise<VoiceModelCatalogItem[]> {
    const payload = await requestJson<unknown>("/v1/models", { method: "GET" });
    return parseModelList(payload);
  },

  async list(): Promise<VoiceCatalogItem[]> {
    const payload = await requestJson<unknown>("/v1/voices", { method: "GET" });
    return parseVoiceList(payload);
  },

  async clonePrompts(): Promise<VoiceClonePrompt[]> {
    const payload = await requestJson<unknown>("/v1/voices/clone/prompts", { method: "GET" });
    return parseClonePrompts(payload);
  },

  async preview(request: VoicePreviewRequest): Promise<Blob> {
    return requestBlob("/v1/voices/previews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  },

  async speech(request: SpeechRequest): Promise<Blob> {
    assertVoiceRequest(request.voice);
    return requestBlob("/v1/audio/speech", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  },

  async clone(formData: FormData): Promise<VoiceCatalogItem> {
    const payload = await requestJson<unknown>("/v1/voices/clone", {
      method: "POST",
      body: formData,
    });
    const voice = parseVoice(payload);
    if (!voice) {
      throw new VoiceServiceError(
        "SpeechRail 返回的克隆音色数据无效",
        502,
        "invalid_response",
      );
    }
    return voice;
  },

  async create(request: VoiceCreateRequest): Promise<VoiceCatalogItem> {
    const payload = await requestJson<unknown>("/v1/voices", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const voice = parseVoice(payload);
    if (!voice) {
      throw new VoiceServiceError(
        "SpeechRail 返回的设计音色数据无效",
        502,
        "invalid_response",
      );
    }
    return voice;
  },

  async delete(voiceId: string): Promise<void> {
    const response = await fetchResponse(`/v1/voices/${encodeURIComponent(voiceId)}`, {
      method: "DELETE",
    });
    if (!response.ok) throw await readError(response);
  },
};
