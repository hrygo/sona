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
});
