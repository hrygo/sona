import { apiUrl } from "../config/runtimeConfig";
import type {
  SpeechRequest,
  VoiceCreation,
  VoiceDesignRequest,
  VoiceDesignResponse,
  VoiceCatalogItem,
  VoiceClonePrompt,
  VoiceCreateRequest,
  VoiceModelCatalogItem,
  VoiceModelCapabilities,
  VoicePreviewRequest,
  VoiceQualityReport,
  VoiceReferenceQuality,
  VoiceSynthesisQuality,
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

function parseReferenceQuality(value: unknown): VoiceReferenceQuality | undefined {
  if (!isRecord(value)) return undefined;
  const durationSeconds = asNumber(value.duration_seconds);
  const sampleRate = asNumber(value.sample_rate);
  const channels = asNumber(value.channels);
  const speechActiveRatio = asNumber(value.speech_active_ratio);
  const noiseFloorDbfs = asNumber(value.noise_floor_dbfs);
  const estimatedSnrDb = asNumber(value.estimated_snr_db);
  const clippingRatio = asNumber(value.clipping_ratio);
  const leadingSilenceSeconds = asNumber(value.leading_silence_seconds);
  const trailingSilenceSeconds = asNumber(value.trailing_silence_seconds);
  const transcriptMatch = asNumber(value.transcript_match);
  const result: VoiceReferenceQuality = {
    ...(durationSeconds !== undefined ? { duration_seconds: durationSeconds } : {}),
    ...(sampleRate !== undefined ? { sample_rate: sampleRate } : {}),
    ...(channels !== undefined ? { channels } : {}),
    ...(speechActiveRatio !== undefined ? { speech_active_ratio: speechActiveRatio } : {}),
    ...(noiseFloorDbfs !== undefined ? { noise_floor_dbfs: noiseFloorDbfs } : {}),
    ...(estimatedSnrDb !== undefined ? { estimated_snr_db: estimatedSnrDb } : {}),
    ...(clippingRatio !== undefined ? { clipping_ratio: clippingRatio } : {}),
    ...(leadingSilenceSeconds !== undefined ? { leading_silence_seconds: leadingSilenceSeconds } : {}),
    ...(trailingSilenceSeconds !== undefined ? { trailing_silence_seconds: trailingSilenceSeconds } : {}),
    ...(transcriptMatch !== undefined ? { transcript_match: transcriptMatch } : {}),
  };
  return Object.keys(result).length > 0 ? result : undefined;
}

function parseSynthesisQuality(value: unknown): VoiceSynthesisQuality | undefined {
  if (!isRecord(value)) return undefined;
  const probeCount = asNumber(value.probe_count);
  const successfulProbeCount = asNumber(value.successful_probe_count);
  const activeRmsDbfs = asNumber(value.active_rms_dbfs);
  const peakDbfs = asNumber(value.peak_dbfs);
  const chunkJumpP95Db = asNumber(value.chunk_jump_p95_db);
  const clippingRatio = asNumber(value.clipping_ratio);
  const deterministic = asBoolean(value.deterministic);
  const transcriptMatch = asNumber(value.transcript_match);
  const result: VoiceSynthesisQuality = {
    ...(probeCount !== undefined ? { probe_count: probeCount } : {}),
    ...(successfulProbeCount !== undefined ? { successful_probe_count: successfulProbeCount } : {}),
    ...(activeRmsDbfs !== undefined ? { active_rms_dbfs: activeRmsDbfs } : {}),
    ...(peakDbfs !== undefined ? { peak_dbfs: peakDbfs } : {}),
    ...(chunkJumpP95Db !== undefined ? { chunk_jump_p95_db: chunkJumpP95Db } : {}),
    ...(clippingRatio !== undefined ? { clipping_ratio: clippingRatio } : {}),
    ...(deterministic !== undefined ? { deterministic } : {}),
    ...(transcriptMatch !== undefined ? { transcript_match: transcriptMatch } : {}),
  };
  return Object.keys(result).length > 0 ? result : undefined;
}

function parseQualityReport(value: unknown): VoiceQualityReport | undefined {
  if (!isRecord(value)) return undefined;
  const policyVersion = asString(value.policy_version);
  const runId = asString(value.run_id);
  const testedAt = asString(value.tested_at);
  if (!policyVersion || !runId || !testedAt) return undefined;
  const status = value.status === "pass" || value.status === "warn" || value.status === "reject"
    ? value.status
    : "unevaluated";
  // Missing/malformed failure evidence must not be silently repaired into a pass.
  if (!Array.isArray(value.failure_codes)
    || !value.failure_codes.every((item): item is string => typeof item === "string" && item.trim().length > 0)) return undefined;
  const failureCodes = value.failure_codes;
  const reference = parseReferenceQuality(value.reference);
  const synthesis = parseSynthesisQuality(value.synthesis);
  return {
    policy_version: policyVersion,
    status,
    run_id: runId,
    tested_at: testedAt,
    ...(reference ? { reference } : {}),
    ...(synthesis ? { synthesis } : {}),
    failure_codes: failureCodes,
  };
}

function parseCreation(value: unknown): VoiceCreation | undefined {
  if (!isRecord(value) || value.origin !== "generated"
    || value.method !== "voice_design_reference_v1" || value.preprocessing_version !== "energy_v1"
    || typeof value.seed !== "number" || !Number.isInteger(value.seed)
    || value.seed < 0 || value.seed > 2 ** 32 - 1
    || typeof value.model_artifact !== "string" || !/^[a-z0-9._-]{1,128}$/.test(value.model_artifact)
    || typeof value.model_revision !== "string" || !/^[0-9a-f]{40}$/.test(value.model_revision)) return undefined;
  for (const key of ["instruction_sha256", "reference_text_sha256", "reference_audio_sha256"]) {
    if (typeof value[key] !== "string" || !/^[0-9a-f]{64}$/.test(value[key])) return undefined;
  }
  // Copy known fields only: a vendor payload must not spread private paths into UI state.
  return {
    origin: "generated", method: "voice_design_reference_v1", preprocessing_version: "energy_v1",
    model_artifact: value.model_artifact, model_revision: value.model_revision, seed: value.seed,
    instruction_sha256: value.instruction_sha256 as string,
    reference_text_sha256: value.reference_text_sha256 as string,
    reference_audio_sha256: value.reference_audio_sha256 as string,
  };
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
  const quality = parseQualityReport(value.quality);
  const creation = parseCreation(value.creation);
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
    ...(quality ? { quality } : {}),
    ...(creation ? { creation } : {}),
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
    ?? asString(root.request_id)
    ?? asString(response.headers?.get?.("x-request-id"));
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
  } catch (error) {
    if (init.signal?.aborted) throw error;
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

  async list(signal?: AbortSignal): Promise<VoiceCatalogItem[]> {
    const payload = await requestJson<unknown>("/v1/voices", { method: "GET", signal });
    if (!isRecord(payload) || !Array.isArray(payload.data)) {
      throw new VoiceServiceError("档案库响应不完整，无法确认保存结果", 502, "invalid_response");
    }
    return parseVoiceList(payload);
  },

  async clonePrompts(): Promise<VoiceClonePrompt[]> {
    const payload = await requestJson<unknown>("/v1/voices/clone/prompts", { method: "GET" });
    return parseClonePrompts(payload);
  },

  async preview(request: VoicePreviewRequest, signal?: AbortSignal): Promise<Blob> {
    return requestBlob("/v1/voices/previews", {
      signal, method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  },

  async speech(request: SpeechRequest, signal?: AbortSignal): Promise<Blob> {
    assertVoiceRequest(request.voice);
    return requestBlob("/v1/audio/speech", {
      signal, method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  },

  async clone(formData: FormData, signal?: AbortSignal, idempotencyKey?: string): Promise<VoiceCatalogItem> {
    const payload = await requestJson<unknown>("/v1/voices/clone", {
      method: "POST", signal,
      ...(idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : {}),
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

  async validateClone(formData: FormData, signal?: AbortSignal): Promise<VoiceQualityReport> {
    const payload = await requestJson<unknown>("/v1/voices/clone/validate", {
      method: "POST", signal,
      body: formData,
    });
    const report = parseQualityReport(payload);
    if (!report) {
      throw new VoiceServiceError(
        "SpeechRail 返回的参考音频质量报告无效",
        502,
        "invalid_response",
      );
    }
    return report;
  },

  async qualityRun(
    voiceId: string,
    input: { probe_set?: string; runs?: number } = {},
    signal?: AbortSignal,
  ): Promise<VoiceQualityReport> {
    const payload = await requestJson<unknown>(
      `/v1/voices/${encodeURIComponent(voiceId)}/quality-runs`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal,
        body: JSON.stringify({
          probe_set: input.probe_set ?? "voice_quality_v1_zh",
          runs: Math.min(Math.max(input.runs ?? 3, 1), 3),
          include_audio: false,
        }),
      },
    );
    const report = parseQualityReport(payload);
    if (!report) {
      throw new VoiceServiceError(
        "SpeechRail 返回的音色质量报告无效",
        502,
        "invalid_response",
      );
    }
    return report;
  },

  async design(request: VoiceDesignRequest, signal?: AbortSignal): Promise<VoiceDesignResponse> {
    const payload = await requestJson<unknown>("/v1/voices/designs", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request), signal,
    });
    const voice = isRecord(payload) ? parseVoice(payload.voice) : null;
    if (!isRecord(payload) || payload.synthesis_validation !== "unevaluated"
      || !voice || voice.id !== request.id || voice.mode !== "clone" || voice.is_system
      || !voice.creation || !voice.ref_text) {
      throw new VoiceServiceError("注册响应无效，请先刷新档案库确认保存结果", 502, "invalid_response");
    }
    return { voice, synthesis_validation: "unevaluated" };
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
