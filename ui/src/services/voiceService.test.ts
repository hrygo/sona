import { afterEach, describe, expect, it, vi } from "vitest";
import {
  VoiceServiceError,
  voiceService,
} from "./voiceService";

const MODEL = "speechrail/qwen3-tts";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("voiceService", () => {
  it("sends natural-language design prompts to the preview extension", async () => {
    const audio = new Blob(["RIFF-preview"], { type: "audio/wav" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => audio,
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await voiceService.preview({
      model: MODEL,
      input: "你好",
      instruction: "温柔知性、吐字清晰",
      response_format: "wav",
    });

    expect(result.type).toBe("audio/wav");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/voices/previews"),
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      model: MODEL,
      input: "你好",
      instruction: "温柔知性、吐字清晰",
      response_format: "wav",
    });
  });

  it("preserves FormData for clone uploads and lets the browser set the boundary", async () => {
    const formData = new FormData();
    formData.append("audio", new Blob(["audio"]), "recording.webm");
    formData.append("ref_text", "白日依山尽");
    formData.append("name", "测试分身");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: "clone-1", name: "测试分身", is_system: false }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await voiceService.clone(formData);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/voices/clone"),
      expect.objectContaining({ method: "POST", body: formData }),
    );
    expect(init.headers).toBeUndefined();
  });

  it("requires a voice for the standard OpenAI-compatible speech endpoint", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      voiceService.speech({ model: MODEL, input: "你好", voice: "" }),
    ).rejects.toMatchObject({
      code: "voice_required",
      status: 422,
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("exposes structured upstream errors without losing request metadata", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        error: {
          code: "voice_preview_unsupported",
          message: "preview is unavailable",
          request_id: "preview-req-1",
          retryable: false,
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const error = await voiceService.preview({
      model: MODEL,
      input: "你好",
      instruction: "温柔知性",
    }).catch((value: unknown) => value);

    expect(error).toBeInstanceOf(VoiceServiceError);
    expect(error).toMatchObject({
      code: "voice_preview_unsupported",
      message: "preview is unavailable",
      requestId: "preview-req-1",
      retryable: false,
      status: 422,
    });
  });

  it("normalizes the voice catalog at the service boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        object: "list",
        data: [
          { id: "warm", name: "温暖磁性", is_system: true, available: true },
          { id: "offline", name: "不可用", is_system: true, available: false },
          { name: "缺少 id", is_system: false },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(voiceService.list()).resolves.toEqual([
      { id: "warm", name: "温暖磁性", is_system: true, available: true },
    ]);
  });

  it("reads model-level TTS capabilities without inferring them from profile names", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        object: "list",
        data: [
          {
            id: "speechrail/qwen3-tts",
            object: "model",
            variant: "custom_voice",
            capabilities: {
              supports_preview: false,
              supports_clone: false,
              supports_instruction: false,
            },
          },
          {
            id: "tts-1",
            resolves_to: "speechrail/qwen3-tts",
            capabilities: {
              supports_preview: false,
              supports_clone: false,
              supports_instruction: false,
            },
          },
          { id: "speechrail/qwen3-asr-1.7b", variant: "asr" },
        ],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(voiceService.models()).resolves.toEqual([
      {
        id: "speechrail/qwen3-tts",
        object: "model",
        variant: "custom_voice",
        capabilities: {
          supports_preview: false,
          supports_clone: false,
          supports_instruction: false,
        },
      },
      {
        id: "tts-1",
        resolves_to: "speechrail/qwen3-tts",
        capabilities: {
          supports_preview: false,
          supports_clone: false,
          supports_instruction: false,
        },
      },
      { id: "speechrail/qwen3-asr-1.7b", variant: "asr" },
    ]);
  });

  it("parses an optional quality report on a cloned voice", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        id: "clone-1",
        name: "测试分身",
        is_system: false,
        mode: "clone",
        quality: {
          policy_version: "voice_quality_v1",
          status: "pass",
          run_id: "vqr-1",
          tested_at: "2026-09-09T10:00:00Z",
          reference: {
            duration_seconds: 8.4,
            estimated_snr_db: 28.4,
            clipping_ratio: 0,
          },
          synthesis: {
            probe_count: 3,
            successful_probe_count: 3,
            deterministic: true,
          },
          failure_codes: [],
        },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(voiceService.clone(new FormData())).resolves.toMatchObject({
      quality: {
        policy_version: "voice_quality_v1",
        status: "pass",
        run_id: "vqr-1",
        reference: { estimated_snr_db: 28.4 },
        synthesis: { probe_count: 3, deterministic: true },
        failure_codes: [],
      },
    });
  });

  it("maps an unknown quality status to unevaluated without trusting it as pass", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        policy_version: "voice_quality_v1",
        status: "future_status",
        run_id: "vqr-2",
        tested_at: "2026-09-09T10:00:00Z",
        failure_codes: ["unknown_status"],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(voiceService.qualityRun("clone-1")).resolves.toMatchObject({
      status: "unevaluated",
      run_id: "vqr-2",
      failure_codes: ["unknown_status"],
    });
  });

  it("validates clone FormData without setting a multipart content type", async () => {
    const formData = new FormData();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        policy_version: "voice_quality_v1",
        status: "warn",
        run_id: "vqr-3",
        tested_at: "2026-09-09T10:00:00Z",
        failure_codes: ["low_snr"],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await voiceService.validateClone(formData);

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/voices/clone/validate"),
      expect.objectContaining({ method: "POST", body: formData }),
    );
    expect((fetchMock.mock.calls[0][1] as RequestInit).headers).toBeUndefined();
  });

  it("starts a bounded quality run with a fixed probe set", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        policy_version: "voice_quality_v1",
        status: "pass",
        run_id: "vqr-4",
        tested_at: "2026-09-09T10:00:00Z",
        failure_codes: [],
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await voiceService.qualityRun("clone-1");

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/v1/voices/clone-1/quality-runs"),
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );
    expect(JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)).toEqual({
      probe_set: "voice_quality_v1_zh",
      runs: 3,
      include_audio: false,
    });
  });
});

const designRequest = {
  id: "design_one", name: "讲解声音", instruction: "温暖的成年男性普通话声音，清晰自然。",
  reference_text: "这是一段自然清晰的参考朗读内容，用于创建可复用的声音而非固定的输出。", seed: 42, language: "zh" as const,
};
const designVoice = {
  id: designRequest.id, name: designRequest.name, mode: "clone", is_system: false,
  ref_text: designRequest.reference_text,
  audio_path: "/private/should-not-leak.wav",
  creation: {
    origin: "generated", method: "voice_design_reference_v1", model_artifact: "qwen3-tts-voicedesign",
    model_revision: "a".repeat(40), seed: 42, instruction_sha256: "b".repeat(64),
    reference_text_sha256: "c".repeat(64), reference_audio_sha256: "d".repeat(64), preprocessing_version: "energy_v1",
    private_path: "/private/also-not-public",
  },
};

it("parses generated registration envelope and forwards only its explicit contract", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201,
    json: async () => ({ voice: designVoice, synthesis_validation: "unevaluated" }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const controller = new AbortController();
  const result = await voiceService.design(designRequest, controller.signal);
  const [url, init] = fetchMock.mock.calls[0]! as [string, RequestInit];
  expect(url).toMatch(/\/v1\/voices\/designs$/);
  expect(JSON.parse(init.body as string)).toEqual(designRequest);
  expect(init.signal).toBe(controller.signal);
  expect(result.voice.creation?.origin).toBe("generated"); expect(result.voice.mode).toBe("clone");
  expect(result.synthesis_validation).toBe("unevaluated");
  expect(JSON.stringify(result)).not.toContain("/private/");
});

it.each([
  { voice: designVoice, synthesis_validation: "pass" },
  { voice: { ...designVoice, id: "wrong" }, synthesis_validation: "unevaluated" },
  { voice: { ...designVoice, mode: "instruction" }, synthesis_validation: "unevaluated" },
  { voice: { ...designVoice, creation: undefined }, synthesis_validation: "unevaluated" },
  { voice: { ...designVoice, creation: { ...designVoice.creation, reference_audio_sha256: "bad" } }, synthesis_validation: "unevaluated" },
  designVoice,
])("fails closed on a malformed or legacy generated registration response", async (payload) => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 201, json: async () => payload });
  vi.stubGlobal("fetch", fetchMock);
  await expect(voiceService.design(designRequest)).rejects.toMatchObject({ code: "invalid_response" });
  expect(fetchMock).toHaveBeenCalledOnce();
});

it.each([404, 409, 429, 503])("preserves generated registration error %s without fallback", async (status) => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: false, status, json: async () => ({
    error: { code: "upstream", message: "not ready", request_id: "design-req", retryable: status >= 429 },
  }) });
  vi.stubGlobal("fetch", fetchMock);
  await expect(voiceService.design(designRequest)).rejects.toMatchObject({ status, code: "upstream", requestId: "design-req" });
  expect(fetchMock).toHaveBeenCalledOnce();
});

it("preserves intentional cancellation rather than reporting network unavailability", async () => {
  const controller = new AbortController(); controller.abort();
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("cancelled", "AbortError")));
  await expect(voiceService.design(designRequest, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
});
