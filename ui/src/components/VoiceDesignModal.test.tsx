import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoiceDesignModal } from "./VoiceDesignModal";
import type { VoiceModelCapabilities } from "../contracts/voiceContract";

vi.mock("../utils/audioPlayback", () => ({
  playAudioBlob: vi.fn().mockResolvedValue(undefined),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let container: HTMLDivElement;
let onCancel: () => void;
let onCreated: ReturnType<typeof vi.fn>;

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  onCancel = vi.fn();
  onCreated = vi.fn();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

function renderModal(modelCapabilities?: VoiceModelCapabilities) {
  act(() => {
    root.render(
      <VoiceDesignModal
        modelCapabilities={modelCapabilities}
        onCancel={onCancel}
        onCreated={onCreated}
      />,
    );
  });
}

it("renders modal with header and inspiration chips", () => {
  renderModal();
  expect(container.textContent).toContain("自然语言音色设计");
  expect(container.textContent).toContain("灵感预设库");
  expect(container.textContent).toContain("🌸 温柔知性");
  expect(container.textContent).toContain("🍵 磁性男声");
});

it("fills name and instruction when inspiration chip is clicked", () => {
  renderModal();
  const chips = Array.from(container.querySelectorAll<HTMLButtonElement>(".voice-inspiration-chip"));
  const warmChip = chips.find((c) => c.textContent?.includes("温柔知性"));
  expect(warmChip).toBeDefined();

  act(() => {
    warmChip!.click();
  });

  const nameInput = container.querySelector<HTMLInputElement>("#voice-design-name");
  const descInput = container.querySelector<HTMLTextAreaElement>("#voice-design-desc");
  expect(nameInput?.value).toBe("知性女声");
  expect(descInput?.value).toContain("温柔轻快");
});

it("sends legacy design preview to the dedicated preview extension", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    blob: async () => new Blob(["audio"], { type: "audio/wav" }),
  });
  vi.stubGlobal("fetch", fetchMock);

  renderModal();
  const warmChip = Array.from(container.querySelectorAll<HTMLButtonElement>(".voice-inspiration-chip")).find((b) =>
    b.textContent?.includes("温柔知性"),
  )!;
  act(() => warmChip.click());
  const previewButton = container.querySelector<HTMLButtonElement>(".btn-voice-design-preview")!;
  await act(async () => {
    previewButton.click();
    await Promise.resolve();
  });

  const previewCall = fetchMock.mock.calls.find(([url]) => String(url).includes("/v1/voices/previews"));
  expect(previewCall).toBeDefined();
  const payload = JSON.parse((previewCall?.[1] as RequestInit).body as string) as Record<string, unknown>;
  expect(payload.instruction).toContain("温柔轻快");
  expect(payload.voice).toBeUndefined();
});

it("submits new voice and calls onCreated on success", async () => {
  const mockVoice = {
    id: "custom_abc123",
    name: "知性女声",
    instruction: "温柔轻快",
    is_system: false,
    created_at: 1788583000,
    available: true,
  };

  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => mockVoice,
  });
  vi.stubGlobal("fetch", fetchMock);

  renderModal();

  const chips = Array.from(container.querySelectorAll<HTMLButtonElement>(".voice-inspiration-chip"));
  const warmChip = chips.find((c) => c.textContent?.includes("温柔知性"))!;
  act(() => {
    warmChip.click();
  });

  const saveBtn = container.querySelector<HTMLButtonElement>(".btn-save-voice")!;
  await act(async () => {
    saveBtn.click();
  });

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/v1/voices"),
    expect.objectContaining({
      method: "POST",
      headers: { "Content-Type": "application/json" },
    }),
  );

  const sentBody = JSON.parse(fetchMock.mock.calls[0][1].body);
  expect(sentBody.name).toBe("知性女声");
  expect(sentBody.instruction).toContain("温柔轻快");

  expect(onCreated).toHaveBeenCalledWith(mockVoice);
});

it("calls onCancel when close or cancel button is clicked", () => {
  renderModal();
  const cancelBtn = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.trim() === "取消",
  );
  expect(cancelBtn).toBeDefined();

  act(() => {
    cancelBtn!.click();
  });
  expect(onCancel).toHaveBeenCalled();
});

it("disables design actions when the active model does not support instruction design", () => {
  renderModal({
    supports_preview: false,
    supports_clone: false,
    supports_instruction: false,
  });

  expect(container.textContent).toContain("当前 TTS 模型不支持自然语言设计");
  expect(container.querySelector<HTMLButtonElement>(".btn-voice-design-preview")?.disabled).toBe(true);
  expect(container.querySelector<HTMLButtonElement>(".btn-save-voice")?.disabled).toBe(true);
});
