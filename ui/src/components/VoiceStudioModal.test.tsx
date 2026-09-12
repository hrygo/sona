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

interface RecordingStub {
  state: RecordingState;
  ondataavailable: ((event: BlobEvent) => void) | null;
  onstop: (() => void) | null;
  start(): void;
  stop(): void;
}

function stubRecordingEnvironment(events: string[]) {
  const recorders: RecordingStub[] = [];
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

      constructor(_stream: MediaStream, _options: MediaRecorderOptions) {
        recorders.push(this as unknown as RecordingStub);
      }

      start() {
        events.push("recorder.start");
        this.state = "recording";
      }

      stop() {
        this.state = "inactive";
        this.onstop?.();
      }
    } as unknown as typeof MediaRecorder,
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

  return { getUserMedia, recorders };
}

it("renders voice atelier with voice deck and clone forge tabs", () => {
  renderStudio();
  expect(container.textContent).toContain("VOICE ATELIER · 声音工坊");
  expect(container.textContent).toContain("创建声音 · 检查效果 · 确认使用");
  expect(container.textContent).toContain("音色档案库");
  expect(container.textContent).toContain("默认原声");
  expect(container.textContent).toContain("我的声音分身");
  expect(container.textContent).toContain("克隆声音 · 参考录音");
  expect(container.textContent).toContain("描述声音 · 自然语言设计");
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

  expect(container.textContent).toContain("声音描述（可直接修改示例）");
  const inspirationChips = Array.from(container.querySelectorAll<HTMLButtonElement>(".design-inspiration-chip"));
  const warmChip = inspirationChips.find((c) => c.textContent?.includes("温柔知性"))!;

  act(() => {
    warmChip.click();
  });

  const nameInput = container.querySelector<HTMLInputElement>("#design-name-input")!;
  const descInput = container.querySelector<HTMLTextAreaElement>("#design-instruction-input")!;
  expect(nameInput.value).toBe("知性女声");
  expect(descInput.value).toContain("温暖清澈");
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
  expect(payload.instruction).toContain("温暖清澈");
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

it("opens VoiceDeleteModal when clicking delete on custom voice and confirms deletion", async () => {
  renderStudio("default");
  // 查找自定义音色的删除按钮（.btn-deck-delete）
  const deleteButtons = Array.from(container.querySelectorAll<HTMLButtonElement>(".btn-deck-delete"));
  expect(deleteButtons.length).toBeGreaterThan(0);

  // 点击删除按钮，弹出确认弹窗
  act(() => {
    deleteButtons[0].click();
  });

  const deleteDialog = container.querySelector(".voice-delete-modal-dialog");
  expect(deleteDialog).not.toBeNull();
  expect(deleteDialog?.textContent).toContain("删除自定义音色");
  expect(deleteDialog?.textContent).toContain("确定要永久删除音色资产");

  // 点击取消按钮，弹窗关闭且不触发删除
  const cancelBtn = Array.from(deleteDialog!.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.includes("取消"),
  );
  expect(cancelBtn).toBeDefined();
  act(() => {
    cancelBtn!.click();
  });
  expect(container.querySelector(".voice-delete-modal-dialog")).toBeNull();
  expect(onVoiceDeleted).not.toHaveBeenCalled();

  // 再次打开并点击确认删除
  act(() => {
    deleteButtons[0].click();
  });
  const confirmBtn = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
    (b) => b.textContent?.includes("确认删除"),
  );
  expect(confirmBtn).toBeDefined();
  await act(async () => {
    confirmBtn!.click();
  });

  expect(onVoiceDeleted).toHaveBeenCalledWith("my_clone");
  expect(container.querySelector(".voice-delete-modal-dialog")).toBeNull();
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

it("mutes the assistant when the studio modal opens and releases it after closing", async () => {
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

  // 弹窗打开即触发静音申请，无需点击录音
  await Promise.resolve();
  expect(events).toEqual(["assistant.mute.requested"]);

  confirmMute();
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(events).toEqual([
    "assistant.mute.requested",
    "assistant.mute.confirmed",
  ]);

  // 关闭/卸载弹窗时恢复麦克风
  act(() => {
    root.unmount();
  });
  await act(async () => {
    await Promise.resolve();
  });

  expect(events).toContain("assistant.unmute");
});

it("ignores a second start click while recording setup is in flight", async () => {
  const events: string[] = [];
  let confirmStream!: (stream: MediaStream) => void;
  const streamPromise = new Promise<MediaStream>((resolve) => {
    confirmStream = resolve;
  });
  const getUserMedia = vi.fn(async () => {
    events.push("getUserMedia");
    return streamPromise;
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
      start() {
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

  renderStudio("default", undefined, {
    onStartRecordingVoice: vi.fn(),
    onStopRecordingVoice: vi.fn(),
  });

  await act(async () => { await Promise.resolve(); });
  const startButton = container.querySelector<HTMLButtonElement>(".btn-record-primary")!;
  act(() => {
    startButton.click();
    startButton.click();
  });

  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(startButton.disabled).toBe(true);
  expect(getUserMedia).toHaveBeenCalledTimes(1);

  confirmStream({
    getTracks: () => [],
  } as unknown as MediaStream);

  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(getUserMedia).toHaveBeenCalledTimes(1);
});

it("disables browser noise suppression for clone reference capture", async () => {
  const events: string[] = [];
  const { getUserMedia } = stubRecordingEnvironment(events);

  renderStudio("default", { supports_clone: true });
  const startButton = container.querySelector<HTMLButtonElement>(".btn-record-primary")!;
  await act(async () => {
    startButton.click();
    await Promise.resolve();
    await Promise.resolve();
  });

  expect(getUserMedia).toHaveBeenCalledWith({
    audio: {
      echoCancellation: false,
      noiseSuppression: false,
      autoGainControl: false,
      sampleRate: 24_000,
    },
  });
});

it("allows editing examples but forbids generated registration without Base capability", () => {
  renderStudio("default", {
    supports_preview: false,
    supports_clone: false,
    supports_instruction: true,
  });

  expect(container.textContent).toContain("描述声音 · 自然语言设计");
  expect(container.textContent).not.toContain("克隆声音 · 参考录音");
  const previewButton = container.querySelector<HTMLButtonElement>(".btn-design-preview");
  expect(previewButton?.disabled).toBe(true);
  expect(container.querySelector<HTMLButtonElement>(".btn-submit-design")?.disabled).toBe(true);
  expect(container.textContent).toContain("Quality 档的设计与克隆能力");
});

/* ====================== 克隆提交流程（保留原始录音） ====================== */

function setInputNativeValue(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

interface CloneFetchResult {
  cloneBodies: FormData[];
  fetchMock: ReturnType<typeof vi.fn>;
}

function stubCloneFetch(outputProbeCount = 3): CloneFetchResult {
  const cloneBodies: FormData[] = [];
  const fetchMock = vi.fn(async (url: unknown, init?: RequestInit) => {
    const path = String(url);
    if (path.includes("/v1/voices/clone/validate") && init?.method === "POST") {
      return {
        ok: true,
        json: async () => ({
          policy_version: "voice_quality_v1",
          status: "pass",
          run_id: "vqr-validation-test",
          tested_at: "2026-09-09T10:00:00Z",
          reference: {
            duration_seconds: 10,
            sample_rate: 24000,
            channels: 1,
            speech_active_ratio: 0.8,
            noise_floor_dbfs: -50,
            estimated_snr_db: 30,
            clipping_ratio: 0,
            leading_silence_seconds: 0.1,
            trailing_silence_seconds: 0.1,
            transcript_match: 1,
          },
          failure_codes: [],
        }),
      };
    }
    if (path.includes("/quality-runs") && init?.method === "POST") {
      return {
        ok: true,
        json: async () => ({
          policy_version: "voice_quality_v1",
          status: "pass",
          run_id: "vqr-test",
          tested_at: "2026-09-09T10:00:00Z",
          synthesis: {
            probe_count: outputProbeCount,
            successful_probe_count: outputProbeCount,
            deterministic: true,
            transcript_match: 1,
          },
          failure_codes: [],
        }),
      };
    }
    if (path.endsWith("/v1/voices/clone") && init?.method === "POST") {
      cloneBodies.push(init.body as FormData);
      return {
        ok: true,
        json: async () => ({ id: (init?.body as FormData).get("id"), name: "测试音色", is_system: false, mode: "clone" }),
      };
    }
    return { ok: true, json: async () => ({ object: "list", data: [] }) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return { cloneBodies, fetchMock };
}

/** 驱动录音流程至"已录制"阶段（recordingSeconds ≥ 3），随后恢复真实计时器。 */
async function recordSampleAudio(events: string[], recorders: RecordingStub[]) {
  vi.useFakeTimers();
  renderStudio("default", { supports_clone: true });
  const startButton = container.querySelector<HTMLButtonElement>(".btn-record-primary")!;
  await act(async () => {
    startButton.click();
    await vi.advanceTimersByTimeAsync(50);
  });
  expect(events).toContain("recorder.start");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  const recorder = recorders[0]!;
  await act(async () => {
    recorder.ondataavailable?.({
      data: new Blob(["fake-webm"], { type: "audio/webm" }),
    } as unknown as BlobEvent);
  });
  const stopButton = container.querySelector<HTMLButtonElement>(".btn-record-stop")!;
  await act(async () => {
    stopButton.click();
  });
  expect(container.querySelector(".btn-submit-clone")).not.toBeNull();

  // 提交流程依赖异步读取录音，切回真实计时器避免 jsdom FileReader 被 fake 定时器阻塞。
  vi.useRealTimers();
}

async function submitCloneWith(name: string) {
  const nameInput = container.querySelector<HTMLInputElement>(".clone-name-input")!;
  await act(async () => {
    setInputNativeValue(nameInput, name);
  });
  const submitButton = container.querySelector<HTMLButtonElement>(".btn-submit-clone")!;
  await act(async () => {
    submitButton.click();
    await Promise.resolve();
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function waitForCloneRequest(cloneBodies: FormData[]) {
  await vi.waitFor(() => {
    expect(cloneBodies).toHaveLength(1);
  });
}

async function waitForLocalQualityFailure(message: string) {
  await vi.waitFor(() => {
    expect(container.textContent).toContain(message);
  });
}

it("uploads the original recording unchanged and does not auto-run output checks or activate", async () => {
  const events: string[] = [];
  const { recorders } = stubRecordingEnvironment(events);
  // 三秒有效信号；计时器不能代替解码后的真实时长。
  const decoded = new Float32Array(48000 * 3);
  for (let i = 0; i < decoded.length; i++) {
    decoded[i] = i % 2 === 0 ? 0.5 : -0.5;
  }
  vi.stubGlobal("AudioContext", class {
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
    decodeAudioData() {
      return Promise.resolve({
        sampleRate: 48000,
        numberOfChannels: 1,
        length: decoded.length,
        getChannelData: () => decoded,
      } as unknown as AudioBuffer);
    }
    close() {
      this.state = "closed";
      return Promise.resolve();
    }
  } as unknown as typeof AudioContext);
  const { cloneBodies, fetchMock } = stubCloneFetch();

  try {
    await recordSampleAudio(events, recorders);
    await submitCloneWith("我的测试音色");
    await waitForCloneRequest(cloneBodies);
  } finally {
    vi.useRealTimers();
  }

  const audio = cloneBodies[0]!.get("audio") as File;
  expect(audio.name).toBe("recording.webm");
  expect(audio.type).toBe("audio/webm");
  const captured = await new Promise<string>((resolve) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(audio);
  });
  expect(captured).toBe("fake-webm");
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("quality-runs"))).toBe(false);
  expect(cloneBodies[0]!.get("name")).toBe("我的测试音色");
  expect(cloneBodies[0]!.get("ref_text")).toContain("白日依山尽");
  expect(onVoiceCreated).toHaveBeenCalledWith(
    expect.objectContaining({ id: cloneBodies[0]!.get("id"), name: "测试音色" }),
  );
  expect(container.textContent).toContain("合成输出检查");
  expect(container.textContent).toContain("尚未评估");
  expect(onSelectVoice).not.toHaveBeenCalled();
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/v1/voices/clone/validate"))).toBe(true);
});

it("retains clone output evidence when switching creation tabs after the check", async () => {
  const events: string[] = [];
  const { recorders } = stubRecordingEnvironment(events);
  const { cloneBodies } = stubCloneFetch(18);

  try {
    await recordSampleAudio(events, recorders);
    await submitCloneWith("我的测试音色");
    await waitForCloneRequest(cloneBodies);
    await vi.waitFor(() => expect(container.textContent).toContain("输出待检查"));

    await act(async () => {
      Array.from(container.querySelectorAll<HTMLButtonElement>("button"))
        .find((button) => button.textContent?.includes("检查输出（18 段）"))!.click();
      await Promise.resolve();
      await Promise.resolve();
    });
    await vi.waitFor(() => expect(container.textContent).toContain("输出检查通过"));

    const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>(".forge-tab-btn"));
    act(() => tabs.find((button) => button.textContent?.includes("自然语言设计"))!.click());
    act(() => tabs.find((button) => button.textContent?.includes("参考录音"))!.click());

    expect(container.textContent).toContain("输出检查通过");
  } finally {
    vi.useRealTimers();
  }
});

it("leaves browser-uninspectable audio for the server preflight without altering its bytes", async () => {
  const events: string[] = [];
  // 本地无法解码仍必须通过服务端预检，不进行伪增强。
  const { recorders } = stubRecordingEnvironment(events);
  const { cloneBodies } = stubCloneFetch();

  try {
    await recordSampleAudio(events, recorders);
    await submitCloneWith("我的测试音色");
    await waitForCloneRequest(cloneBodies);
  } finally {
    vi.useRealTimers();
  }

  const audio = cloneBodies[0]!.get("audio") as File;
  expect(audio.name).toBe("recording.webm");
  expect(onVoiceCreated).toHaveBeenCalledWith(
    expect.objectContaining({ id: cloneBodies[0]!.get("id") }),
  );
});

it("blocks the clone submission and asks for a re-record when the recording is silent", async () => {
  const events: string[] = [];
  const { recorders } = stubRecordingEnvironment(events);
  vi.stubGlobal("AudioContext", class {
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
    decodeAudioData() {
      return Promise.resolve({
        sampleRate: 48000,
        numberOfChannels: 1,
        length: 2000,
        getChannelData: () => new Float32Array(2000),
      } as unknown as AudioBuffer);
    }
    close() {
      this.state = "closed";
      return Promise.resolve();
    }
  } as unknown as typeof AudioContext);
  stubCloneFetch();

  try {
    await recordSampleAudio(events, recorders);
    await submitCloneWith("我的测试音色");
    await waitForLocalQualityFailure("录音几乎无声");
  } finally {
    vi.useRealTimers();
  }

  expect(container.textContent).toContain("录音几乎无声");
  expect(onVoiceCreated).not.toHaveBeenCalled();
});

it("preserves an edited design draft when switching between the two creation tabs", async () => {
  renderStudio("default", { supports_clone: true, supports_instruction: true, supports_preview: true });
  const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>(".forge-tab-btn"));
  act(() => tabs.find((b) => b.textContent?.includes("自然语言设计"))!.click());
  const example = Array.from(container.querySelectorAll<HTMLButtonElement>(".voice-example-card"))[1]!;
  act(() => example.click());
  const description = container.querySelector<HTMLTextAreaElement>("#design-instruction-input")!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(description, "我的已修改描述");
    description.dispatchEvent(new Event("input", { bubbles: true }));
  });
  act(() => tabs.find((b) => b.textContent?.includes("参考录音"))!.click());
  act(() => tabs.find((b) => b.textContent?.includes("自然语言设计"))!.click());
  expect(container.querySelector<HTMLTextAreaElement>("#design-instruction-input")!.value).toBe("我的已修改描述");
});

it("forwards the corrected actual reference text instead of the original teleprompter", async () => {
  const events: string[] = [];
  const { recorders } = stubRecordingEnvironment(events);
  const { cloneBodies } = stubCloneFetch();
  await recordSampleAudio(events, recorders);
  const editor = container.querySelector<HTMLTextAreaElement>("#clone-reference-text")!;
  const spoken = "这是我实际说出的内容，不是提词器上原先提供的内容。";
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(editor, spoken);
    editor.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(container.querySelector<HTMLButtonElement>(".btn-switch-prompt")!.disabled).toBe(true);
  await submitCloneWith("修正参考"); await waitForCloneRequest(cloneBodies);
  expect(cloneBodies[0]!.get("ref_text")).toBe(spoken);
});

it("does not open the microphone before assistant mute is acknowledged", async () => {
  const events: string[] = [];
  const { getUserMedia } = stubRecordingEnvironment(events);
  let finish!: () => void;
  const start = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  renderStudio("default", undefined, { onStartRecordingVoice: start });
  await act(async () => { container.querySelector<HTMLButtonElement>(".btn-record-primary")!.click(); });
  expect(getUserMedia).not.toHaveBeenCalled();
  await act(async () => { finish(); });
  await act(async () => { container.querySelector<HTMLButtonElement>(".btn-record-primary")!.click(); });
  expect(getUserMedia).toHaveBeenCalledOnce();
});

it("keeps deletion confirmation open until the actual deletion completes", async () => {
  let finish!: () => void;
  onVoiceDeleted.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  renderStudio();
  act(() => { container.querySelector<HTMLButtonElement>(".btn-deck-delete")!.click(); });
  await act(async () => { container.querySelector<HTMLButtonElement>(".btn-danger")!.click(); });
  expect(container.querySelector(".voice-delete-modal-dialog")).not.toBeNull();
  expect(container.textContent).toContain("删除中");
  await act(async () => { finish(); });
  expect(container.querySelector(".voice-delete-modal-dialog")).toBeNull();
});

it("Escape dismisses only the deletion confirmation, not the entire workshop", async () => {
  renderStudio();
  act(() => { container.querySelector<HTMLButtonElement>(".btn-deck-delete")!.click(); });
  act(() => { window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
  expect(onClose).not.toHaveBeenCalled();
  expect(container.querySelector(".voice-delete-modal-dialog")).toBeNull();
});

it("pins the shown script while a late prompt-catalog response arrives", async () => {
  const events: string[] = []; stubRecordingEnvironment(events);
  let resolve!: (value: unknown) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise((done) => { resolve = done; })));
  renderStudio();
  await act(async () => { container.querySelector<HTMLButtonElement>(".btn-record-primary")!.click(); });
  await act(async () => resolve({ ok: true, json: async () => ({ data: [{ id: "late", title: "Late prompt", script: "不可替换正在朗读的文本", tips: "test", category: "test" }] }) }));
  expect(container.querySelector(".teleprompter-script")!.textContent).toContain("白日依山尽");
  expect(container.querySelector(".teleprompter-script")!.textContent).not.toContain("不可替换");
});
it("returns an empty or interrupted recorder to a retryable ready state", async () => {
  const events: string[] = []; const { recorders } = stubRecordingEnvironment(events);
  renderStudio();
  await act(async () => { container.querySelector<HTMLButtonElement>(".btn-record-primary")!.click(); });
  await act(async () => recorders[0]!.stop());
  expect(container.textContent).toContain("没有收到录音数据");
  expect(container.querySelector<HTMLButtonElement>(".btn-record-primary")!.disabled).toBe(false);
  await act(async () => { container.querySelector<HTMLButtonElement>(".btn-record-primary")!.click(); });
  const recorder = recorders[1] as unknown as { onerror: () => void };
  await act(async () => recorder.onerror());
  expect(container.textContent).toContain("录音设备中断或录制失败");
  expect(container.querySelector(".btn-submit-clone")).toBeNull();
});
it("automatically stops at the actual thirty-second boundary instead of thirty-one seconds", async () => {
  vi.useFakeTimers();
  try {
    const events: string[] = []; const { recorders } = stubRecordingEnvironment(events);
    renderStudio();
    await act(async () => { container.querySelector<HTMLButtonElement>(".btn-record-primary")!.click(); });
    await act(async () => recorders[0]!.ondataavailable?.({ data: new Blob(["valid-fixture"]) } as unknown as BlobEvent));
    await act(async () => { await vi.advanceTimersByTimeAsync(30000); });
    expect(recorders[0]!.state).toBe("inactive");
    expect(container.querySelector(".recorded-audio-player")).not.toBeNull();
  } finally { vi.useRealTimers(); }
});
