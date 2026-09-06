import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoiceStudioModal } from "./VoiceStudioModal";
import type { VoiceCatalogItem, VoiceModelCapabilities } from "./assistantPresentation";

vi.mock("../utils/audioPlayback", () => ({
  playAudioBlob: vi.fn().mockResolvedValue(undefined),
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let container: HTMLDivElement;
let onSelectVoice: ReturnType<typeof vi.fn>;
let onVoiceCreated: ReturnType<typeof vi.fn>;
let onVoiceDeleted: ReturnType<typeof vi.fn>;
let onClose: ReturnType<typeof vi.fn>;

const MOCK_VOICES: readonly VoiceCatalogItem[] = [
  { id: "default", name: "默认原声", is_system: true, mode: "system", instruction: "标准女声" },
  { id: "warm", name: "温暖磁性", is_system: true, mode: "system", instruction: "亲和男声" },
  { id: "my_clone", name: "我的声音分身", is_system: false, mode: "clone", ref_text: "白日依山尽" },
  { id: "custom_design", name: "知性姐姐", is_system: false, mode: "instruction", instruction: "温柔知性" },
];

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  onSelectVoice = vi.fn();
  onVoiceCreated = vi.fn();
  onVoiceDeleted = vi.fn();
  onClose = vi.fn();

  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ object: "list", data: [] }),
  }));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderStudio(
  currentId = "default",
  modelCapabilities?: VoiceModelCapabilities,
  callbacks: {
    onStartRecordingVoice?: () => void | Promise<void>;
    onStopRecordingVoice?: () => void | Promise<void>;
  } = {},
) {
  act(() => {
    root.render(
      <VoiceStudioModal
        currentVoiceId={currentId}
        availableVoices={MOCK_VOICES}
        modelCapabilities={modelCapabilities}
        onSelectVoice={onSelectVoice}
        onVoiceCreated={onVoiceCreated}
        onVoiceDeleted={onVoiceDeleted}
        onClose={onClose}
        onStartRecordingVoice={callbacks.onStartRecordingVoice}
        onStopRecordingVoice={callbacks.onStopRecordingVoice}
      />
    );
  });
}

function stubRecordingEnvironment(events: string[]) {
  const getUserMedia = vi.fn(async () => {
    events.push("getUserMedia");
    return {
      getTracks: () => [],
    } as unknown as MediaStream;
  });
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  vi.stubGlobal("URL", {
    createObjectURL: vi.fn(() => "blob:voice-recording"),
    revokeObjectURL: vi.fn(),
  });
  vi.stubGlobal(
    "MediaRecorder",
    class {
      state: RecordingState = "inactive";
      ondataavailable: ((event: BlobEvent) => void) | null = null;
      onstop: (() => void) | null = null;

      constructor(_stream: MediaStream, _options: MediaRecorderOptions) {}

      start() {
        events.push("recorder.start");
        this.state = "recording";
      }

      stop() {
        this.state = "inactive";
        this.onstop?.();
      }
    },
  );
  vi.stubGlobal(
    "AudioContext",
    class {
      state: AudioContextState = "running";

      createMediaStreamSource() {
        return { connect: vi.fn() };
      }

      createAnalyser() {
        return {
          fftSize: 256,
          frequencyBinCount: 2,
          getByteFrequencyData: (data: Uint8Array) => data.fill(0),
        };
      }

      close() {
        this.state = "closed";
        return Promise.resolve();
      }
    },
  );
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(() => null);

  return { getUserMedia };
}

it("renders voice atelier with voice deck and clone forge tabs", () => {
  renderStudio();
  expect(container.textContent).toContain("VOICE ATELIER · 声音工坊");
  expect(container.textContent).toContain("全本地专属声音创设与档案库");
  expect(container.textContent).toContain("音色档案库");
  expect(container.textContent).toContain("默认原声");
  expect(container.textContent).toContain("我的声音分身");
  expect(container.textContent).toContain("声音克隆 (ICL 录音克隆)");
  expect(container.textContent).toContain("自然语言设计 (Prompt 定制)");
  expect(container.textContent).toContain("提词器引导朗读");
});

it("filters voices by mode in the voice deck", () => {
  renderStudio();
  const filterChips = Array.from(container.querySelectorAll<HTMLButtonElement>(".deck-filter-chip"));
  const cloneFilter = filterChips.find((c) => c.textContent?.includes("克隆"))!;
  expect(cloneFilter).toBeDefined();

  act(() => {
    cloneFilter.click();
  });

  const cards = container.querySelectorAll(".voice-deck-card");
  expect(cards.length).toBe(1);
  expect(cards[0].textContent).toContain("我的声音分身");
});

it("switches teleprompter prompt script when clicking switch button", () => {
  renderStudio();
  const initialScript = container.querySelector(".teleprompter-script")?.textContent;
  expect(initialScript).toContain("白日依山尽");

  const switchBtn = container.querySelector<HTMLButtonElement>(".btn-switch-prompt")!;
  expect(switchBtn).toBeDefined();

  act(() => {
    switchBtn.click();
  });

  const nextScript = container.querySelector(".teleprompter-script")?.textContent;
  expect(nextScript).not.toBe(initialScript);
});

it("switches to design forge tab and applies inspiration prompt", () => {
  renderStudio();
  const designTabBtn = Array.from(container.querySelectorAll<HTMLButtonElement>(".forge-tab-btn")).find((b) =>
    b.textContent?.includes("自然语言设计")
  )!;

  act(() => {
    designTabBtn.click();
  });

  expect(container.textContent).toContain("音色提示词 Prompt");
  const inspirationChips = Array.from(container.querySelectorAll<HTMLButtonElement>(".design-inspiration-chip"));
  const warmChip = inspirationChips.find((c) => c.textContent?.includes("温柔知性"))!;

  act(() => {
    warmChip.click();
  });

  const nameInput = container.querySelector<HTMLInputElement>("#design-name-input")!;
  const descInput = container.querySelector<HTMLTextAreaElement>("#design-instruction-input")!;
  expect(nameInput.value).toBe("知性女声");
  expect(descInput.value).toContain("温柔轻快");
});

it("sends design forge preview to the dedicated preview extension", async () => {
  const fetchMock = vi.fn().mockImplementation(async (url: string) => {
    if (url.includes("/v1/voices/previews")) {
      return {
        ok: true,
        status: 200,
        blob: async () => new Blob(["audio"], { type: "audio/wav" }),
      };
    }
    return { ok: true, status: 200, json: async () => ({ object: "list", data: [] }) };
  });
  vi.stubGlobal("fetch", fetchMock);

  renderStudio();
  const designTabBtn = Array.from(container.querySelectorAll<HTMLButtonElement>(".forge-tab-btn")).find((b) =>
    b.textContent?.includes("自然语言设计"),
  )!;
  act(() => designTabBtn.click());
  const warmChip = Array.from(container.querySelectorAll<HTMLButtonElement>(".design-inspiration-chip")).find((b) =>
    b.textContent?.includes("温柔知性"),
  )!;
  act(() => warmChip.click());

  const previewButton = container.querySelector<HTMLButtonElement>(".btn-design-preview")!;
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

it("triggers voice selection when clicking apply button in voice deck", () => {
  renderStudio("default");
  const applyButtons = Array.from(container.querySelectorAll<HTMLButtonElement>(".btn-deck-apply"));
  expect(applyButtons.length).toBeGreaterThan(0);

  act(() => {
    applyButtons[0].click();
  });

  expect(onSelectVoice).toHaveBeenCalled();
});

it("calls onClose when close button is clicked", () => {
  renderStudio();
  const closeBtn = container.querySelector<HTMLButtonElement>(".voice-studio-close-btn")!;
  act(() => {
    closeBtn.click();
  });
  expect(onClose).toHaveBeenCalled();
});

it("renders consistently under both light and dark themes", () => {
  document.documentElement.dataset.theme = "light";
  renderStudio();
  expect(container.querySelector(".voice-studio-dialog")).not.toBeNull();
  expect(container.querySelector(".teleprompter-script")).not.toBeNull();
  expect(container.querySelector(".deck-filter-chip.active")).not.toBeNull();

  document.documentElement.dataset.theme = "dark";
  renderStudio();
  expect(container.querySelector(".voice-studio-dialog")).not.toBeNull();
  expect(container.querySelector(".teleprompter-script")).not.toBeNull();
  delete document.documentElement.dataset.theme;
});

it("hides unsupported forge tabs when the active model exposes no creation capabilities", () => {
  renderStudio("default", {
    supports_preview: false,
    supports_clone: false,
    supports_instruction: false,
  });

  expect(container.querySelectorAll(".forge-tab-btn")).toHaveLength(0);
  expect(container.textContent).toContain("当前 TTS 模型不支持声音创设");
});

it("mutes the assistant before opening the browser recording stream and releases it after stop", async () => {
  const events: string[] = [];
  let confirmMute!: () => void;
  const muteConfirmed = new Promise<void>((resolve) => {
    confirmMute = resolve;
  });
  stubRecordingEnvironment(events);

  renderStudio("default", undefined, {
    onStartRecordingVoice: async () => {
      events.push("assistant.mute.requested");
      await muteConfirmed;
      events.push("assistant.mute.confirmed");
    },
    onStopRecordingVoice: async () => {
      events.push("assistant.unmute");
    },
  });

  const startButton = container.querySelector<HTMLButtonElement>(".btn-record-primary")!;
  act(() => {
    startButton.click();
  });

  await Promise.resolve();
  expect(events).toEqual(["assistant.mute.requested"]);

  confirmMute();
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(events.slice(0, 3)).toEqual([
    "assistant.mute.requested",
    "assistant.mute.confirmed",
    "getUserMedia",
  ]);

  const unmuteCountBeforeRerender = events.filter((event) => event === "assistant.unmute").length;
  renderStudio("default", undefined, {
    onStartRecordingVoice: async () => {
      events.push("assistant.mute.replaced");
    },
    onStopRecordingVoice: async () => {
      events.push("assistant.unmute.replaced");
    },
  });
  expect(container.querySelector(".btn-record-stop")).not.toBeNull();
  expect(events.filter((event) => event === "assistant.unmute")).toHaveLength(unmuteCountBeforeRerender);

  const stopButton = container.querySelector<HTMLButtonElement>(".btn-record-stop")!;
  await act(async () => {
    stopButton.click();
    await Promise.resolve();
  });

  expect(events).toContain("assistant.unmute.replaced");
});

it("ignores a second start while assistant mute confirmation is pending", async () => {
  const events: string[] = [];
  let confirmMute!: () => void;
  const muteConfirmed = new Promise<void>((resolve) => {
    confirmMute = resolve;
  });
  const { getUserMedia } = stubRecordingEnvironment(events);
  const onStartRecordingVoice = vi.fn(async () => {
    events.push("assistant.mute.requested");
    await muteConfirmed;
  });

  renderStudio("default", undefined, {
    onStartRecordingVoice,
    onStopRecordingVoice: vi.fn(),
  });

  const startButton = container.querySelector<HTMLButtonElement>(".btn-record-primary")!;
  act(() => {
    startButton.click();
    startButton.click();
  });

  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(onStartRecordingVoice).toHaveBeenCalledTimes(1);
  expect(startButton.disabled).toBe(true);

  confirmMute();
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(getUserMedia).toHaveBeenCalledTimes(1);
});

it("keeps design creation available while disabling preview when the model forbids preview", () => {
  renderStudio("default", {
    supports_preview: false,
    supports_clone: false,
    supports_instruction: true,
  });

  expect(container.textContent).toContain("自然语言设计 (Prompt 定制)");
  expect(container.textContent).not.toContain("声音克隆 (ICL 录音克隆)");
  const previewButton = container.querySelector<HTMLButtonElement>(".btn-design-preview");
  expect(previewButton?.disabled).toBe(true);
  expect(container.textContent).toContain("当前模型不支持自然语言试听");
});
