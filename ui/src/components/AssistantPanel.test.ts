import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantErrorNotice } from "./AssistantErrorNotice";
import type { CommandSocketApi } from "../hooks/useCommandSocket";
import type { RuntimeStateSnapshot } from "../protocol";
import AssistantPanel, {
  DUPLEX_MODE_PRESENTATION,
  canRequestDuplexModeChange,
  getAssistantInputPresentation,
  getAssistantSessionControlPresentation,
  getTelemetryBadge,
  TELEMETRY_HELP_STEPS,
  getAssistantPhaseTransitionDelay,
  getDuplexModeFeedback,
  getDuplexToggleMode,
  getVoiceDisplayName,
} from "./AssistantPanel";
import { createAssistantSnapshot, useAssistantStore } from "../stores/assistantStore";
import { useUISettingsStore } from "../stores/uiSettingsStore";

const assistantEventHarness = vi.hoisted(() => ({
  onMessage: null as ((message: MessageEvent) => void) | null,
}));

const toastHarness = vi.hoisted(() => ({
  showToast: vi.fn(),
}));

vi.mock("../hooks/useEventSocket", () => ({
  useEventSocket: (_url: string, onMessage: (message: MessageEvent) => void) => {
    assistantEventHarness.onMessage = onMessage;
    return { state: "open" };
  },
}));

vi.mock("./AssistantWaveform", () => ({
  AssistantWaveform: () => createElement("div", { "data-testid": "assistant-waveform" }),
}));

vi.mock("./VoiceStudioModal", () => ({
  VoiceStudioModal: (props: {
    isOpen?: boolean;
    onClose?: () => void;
    onStartRecordingVoice?: () => void | Promise<void>;
    onStopRecordingVoice?: () => void | Promise<void>;
  }) => createElement("div", { "data-testid": "voice-studio-modal" }, [
    createElement(
      "button",
      {
        key: "close",
        className: "test-studio-close",
        onClick: () => props.onClose?.(),
      },
      "close",
    ),
    createElement(
      "button",
      {
        key: "start",
        className: "test-record-start",
        onClick: () => void props.onStartRecordingVoice?.(),
      },
      "start",
    ),
    createElement(
      "button",
      {
        key: "stop",
        className: "test-record-stop",
        onClick: () => void Promise.resolve(props.onStopRecordingVoice?.()).catch(() => undefined),
      },
      "stop",
    ),
  ]),
}));

vi.mock("./Toast", () => ({
  showToast: toastHarness.showToast,
}));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let container: HTMLDivElement;

function runtimeSnapshot(overrides: Partial<RuntimeStateSnapshot> = {}): RuntimeStateSnapshot {
  return {
    mode: "assistant",
    pcm_owner: "assistant",
    pipeline: "running",
    subtitle: "idle",
    mic_muted: false,
    runtime_revision: 1,
    ...overrides,
  };
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  assistantEventHarness.onMessage = null;
  toastHarness.showToast.mockClear();
  useAssistantStore.setState({ ...createAssistantSnapshot(), connected: false });
  useUISettingsStore.setState({ micMuted: false });
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("test fetch skipped")));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  useAssistantStore.setState({ ...createAssistantSnapshot(), connected: false });
  useUISettingsStore.setState({ micMuted: false });
  vi.unstubAllGlobals();
});

describe("assistant phase presentation", () => {
  it("keeps the listening animation visible before advancing to thinking", () => {
    expect(getAssistantPhaseTransitionDelay("listening", "thinking", 1_000, 1_100)).toBe(140);
  });

  it("does not delay entering listening when speech starts", () => {
    expect(getAssistantPhaseTransitionDelay("idle", "listening", 1_000, 1_100)).toBe(0);
  });

  it("describes muted microphone ownership without claiming active listening", () => {
    const presentation = getAssistantInputPresentation("listening", true, "assistant");

    expect(presentation.label).toBe("麦克风已静音");
    expect(presentation.detail).not.toContain("正在接收麦克风语音");
  });

  it("keeps the muted label while the assistant is finishing another phase", () => {
    const presentation = getAssistantInputPresentation("thinking", true, "assistant");

    expect(presentation.label).toBe("麦克风已静音");
  });

  it("explains when another workload owns the audio input", () => {
    const presentation = getAssistantInputPresentation("listening", false, "subtitles");

    expect(presentation.label).toBe("实时字幕占用音频");
    expect(presentation.detail).toContain("助手未接收麦克风语音");
  });
});

describe("voice audition presentation", () => {
  it("uses readable names for system and clone voices", () => {
    expect(getVoiceDisplayName("warm", {
      id: "warm",
      name: "warm",
      is_system: true,
      mode: "system",
    })).toBe("温暖磁性");

    expect(getVoiceDisplayName("clone_7f3c", {
      id: "clone_7f3c",
      name: "温柔中文女声",
      is_system: false,
      mode: "clone",
    })).toBe("温柔中文女声");

    expect(getVoiceDisplayName("clone_7f3c", {
      id: "clone_7f3c",
      name: "clone_7f3c",
      is_system: false,
      mode: "clone",
    })).toBe("克隆音色");

    expect(getVoiceDisplayName("vendor_system", {
      id: "vendor_system",
      name: "vendor_system",
      is_system: true,
      mode: "system",
    })).toBe("系统预设音色");
  });

});

describe("assistant session control state", () => {
  it("offers resume after the server confirms that the assistant session stopped", () => {
    const presentation = getAssistantSessionControlPresentation(runtimeSnapshot({
      mode: "idle",
      pcm_owner: "none",
      pipeline: "stopped",
    }));

    expect(presentation.command).toBe("start_assistant");
    expect(presentation.label).toBe("恢复会话");
    expect(presentation.title).toContain("恢复");
  });

  it("closes the stop and resume loop from the rendered control", async () => {
    const sendCommand = vi
      .fn()
      .mockResolvedValueOnce(runtimeSnapshot({
        mode: "idle",
        pcm_owner: "none",
        pipeline: "stopped",
        runtime_revision: 2,
      }))
      .mockResolvedValueOnce(runtimeSnapshot({ runtime_revision: 3 }));
    let commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: runtimeSnapshot(),
      highestRuntimeRevision: 1,
      sendCommand,
      reconcileRuntime: vi.fn().mockResolvedValue(runtimeSnapshot()),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    const stopButton = container.querySelector<HTMLButtonElement>("[data-testid='assistant-session-toggle']");
    expect(stopButton?.textContent).toContain("停止会话");
    await act(async () => {
      stopButton?.click();
      await Promise.resolve();
    });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: "stop_session" });

    commandSocket = {
      ...commandSocket,
      snapshot: runtimeSnapshot({ mode: "idle", pcm_owner: "none", pipeline: "stopped", runtime_revision: 2 }),
      highestRuntimeRevision: 2,
    };
    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    const resumeButton = container.querySelector<HTMLButtonElement>("[data-testid='assistant-session-toggle']");
    expect(resumeButton?.textContent).toContain("恢复会话");
    await act(async () => {
      resumeButton?.click();
      await Promise.resolve();
    });
    expect(sendCommand).toHaveBeenCalledWith({ cmd: "start_assistant" });
  });

  it("does not expose an assistant stop command while a meeting owns the microphone", () => {
    const sendCommand = vi.fn().mockResolvedValue(runtimeSnapshot({ mode: "meeting", pcm_owner: "meeting" }));
    const commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: runtimeSnapshot({ mode: "meeting", pcm_owner: "meeting", pipeline: "stopped" }),
      highestRuntimeRevision: 2,
      sendCommand,
      reconcileRuntime: vi.fn().mockResolvedValue(runtimeSnapshot()),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket, isMeetingRecording: true }));
    });

    const meetingButton = container.querySelector<HTMLButtonElement>("[data-testid='assistant-session-toggle']");
    expect(meetingButton?.textContent).toContain("会议录制中");
    expect(meetingButton?.disabled).toBe(true);
    expect(sendCommand).not.toHaveBeenCalled();
  });
});

describe("voice switching presentation", () => {
  it("shows the readable voice name instead of its id after switching", async () => {
    const sendCommand = vi.fn().mockResolvedValue({});
    const commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: null,
      highestRuntimeRevision: null,
      sendCommand,
      reconcileRuntime: vi.fn().mockResolvedValue({}),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    const voiceSelect = container.querySelector<HTMLSelectElement>("#assistant-voice-select");
    expect(voiceSelect).not.toBeNull();

    await act(async () => {
      voiceSelect!.value = "warm";
      voiceSelect!.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    expect(sendCommand).toHaveBeenCalledWith({ cmd: "set_voice", voice: "warm" });
    expect(toastHarness.showToast).toHaveBeenCalledWith("音色已切换为: 温暖磁性", "success");
  });
});

describe("voice workshop microphone lease", () => {
  it("retries assistant unmute after the control connection recovers", async () => {
    const sendCommand = vi.fn().mockResolvedValue({});
    const connectedSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: null,
      highestRuntimeRevision: null,
      sendCommand,
      reconcileRuntime: vi.fn().mockResolvedValue({}),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket: connectedSocket }));
    });
    act(() => container.querySelector<HTMLButtonElement>(".btn-voice-studio-btn")?.click());

    expect(sendCommand).toHaveBeenCalledWith({ cmd: "set_mic_muted", muted: true });

    const disconnectedSocket: CommandSocketApi = { ...connectedSocket, ready: false };
    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket: disconnectedSocket }));
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>(".test-studio-close")?.click();
      await Promise.resolve();
    });

    expect(sendCommand).not.toHaveBeenCalledWith({ cmd: "set_mic_muted", muted: false });

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket: connectedSocket }));
    });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(sendCommand).toHaveBeenCalledWith({ cmd: "set_mic_muted", muted: false });
  });
});

describe("duplex mode presentation", () => {
  it("names speaker mode by its output device and interaction consequence", () => {
    expect(DUPLEX_MODE_PRESENTATION.speaker_focus.interruptionEnabled).toBe(false);
    expect(DUPLEX_MODE_PRESENTATION.speaker_focus.label).toBe("扬声器");
    expect(DUPLEX_MODE_PRESENTATION.speaker_focus.summary).toContain("不接收插话");
    expect(DUPLEX_MODE_PRESENTATION.speaker_focus.detail).toContain("扬声器");
  });

  it("names headphone mode by its output device and interaction consequence", () => {
    expect(DUPLEX_MODE_PRESENTATION.headphone_duplex.interruptionEnabled).toBe(true);
    expect(DUPLEX_MODE_PRESENTATION.headphone_duplex.label).toBe("耳机");
    expect(DUPLEX_MODE_PRESENTATION.headphone_duplex.summary).toContain("可随时插话");
    expect(DUPLEX_MODE_PRESENTATION.headphone_duplex.detail).toContain("需佩戴耳机");
  });
});

describe("duplex mode feedback", () => {
  it("explains that the system is applying the requested mode while awaiting acknowledgement", () => {
    const feedback = getDuplexModeFeedback("speaker_focus", "headphone_duplex", true);

    expect(feedback.tone).toBe("switching");
    expect(feedback.title).toContain("耳机");
    expect(feedback.detail).toContain("正在应用");
  });

  it("keeps the active mode visible when a switch fails", () => {
    const feedback = getDuplexModeFeedback("speaker_focus", null, true, "切换失败");

    expect(feedback.tone).toBe("error");
    expect(feedback.detail).toContain("扬声器");
  });

  it("does not submit a duplicate request for the active or pending mode", () => {
    expect(canRequestDuplexModeChange("speaker_focus", "speaker_focus", null)).toBe(false);
    expect(canRequestDuplexModeChange("speaker_focus", "headphone_duplex", "headphone_duplex")).toBe(false);
    expect(canRequestDuplexModeChange("speaker_focus", "headphone_duplex", null)).toBe(true);
  });
});

describe("duplex toggle position", () => {
  it("shows the committed mode when no switch is pending", () => {
    expect(getDuplexToggleMode("speaker_focus", null)).toBe("speaker_focus");
  });

  it("moves the visual thumb to the requested mode before acknowledgement", () => {
    expect(getDuplexToggleMode("speaker_focus", "headphone_duplex")).toBe("headphone_duplex");
  });

  it("returns to the committed mode when a pending switch is cleared", () => {
    expect(getDuplexToggleMode("headphone_duplex", null)).toBe("headphone_duplex");
  });
});

describe("telemetry badge", () => {
  it("shows the grade and total for complete metrics", () => {
    expect(getTelemetryBadge({
      sttMs: 150,
      llmTtftMs: 700,
      ttsTtfbMs: 250,
      e2eMs: 1100,
    })).toEqual({ className: "fast", label: "首包极速", value: 1100 });
  });

  it("shows data insufficiency for partial metrics", () => {
    expect(getTelemetryBadge({
      sttMs: 150,
      llmTtftMs: 700,
      ttsTtfbMs: 250,
      e2eMs: null,
    })).toEqual({ className: "idle", label: "数据不足", value: null });
  });

  it("shows standby before the first metrics event", () => {
    expect(getTelemetryBadge(null)).toEqual({ className: "idle", label: "待命中", value: null });
  });
});

describe("telemetry explanation", () => {
  it("documents the anchor and all three measured stages", () => {
    expect(TELEMETRY_HELP_STEPS.map((step) => step.title)).toEqual([
      "转写等待",
      "LLM 首字",
      "TTS 首包",
    ]);
    expect(TELEMETRY_HELP_STEPS[0].formula).toContain("STT final");
    expect(TELEMETRY_HELP_STEPS[0].description).toContain("0ms");
    expect(TELEMETRY_HELP_STEPS[0].description).toContain("不代表模型识别耗时为 0");
    expect(TELEMETRY_HELP_STEPS[1].formula).toContain("max");
    expect(TELEMETRY_HELP_STEPS[2].formula).toContain("TTS 首帧");
  });

  it("makes the frame-level meaning explicit for users", () => {
    const explanation = TELEMETRY_HELP_STEPS.map((step) => `${step.event} ${step.description}`).join(" ");
    expect(explanation).toContain("UserStoppedSpeakingFrame");
    expect(explanation).toContain("TranscriptionFrame");
    expect(explanation).toContain("LLMTextFrame");
    expect(explanation).toContain("TTSAudioRawFrame");
  });

  it("renders the first-packet scope and device playback boundary in the help panel", () => {
    useAssistantStore.setState({
      latestMetrics: {
        turnId: 1,
        sttMs: 0,
        llmTtftMs: 700,
        ttsTtfbMs: 250,
        e2eMs: 950,
      },
    });
    const commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: null,
      highestRuntimeRevision: null,
      sendCommand: vi.fn().mockResolvedValue({}),
      reconcileRuntime: vi.fn().mockResolvedValue({}),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    expect(container.querySelector(".telemetry-grade-pill")?.textContent)
      .toContain("首包极速");
    expect(container.querySelector(".telemetry-compact-flow")?.textContent)
      .toContain("转写等待");

    const helpButton = container.querySelector<HTMLButtonElement>(".telemetry-help-trigger");
    expect(helpButton).not.toBeNull();
    act(() => helpButton?.click());

    const helpPanel = container.querySelector("#telemetry-help-panel");
    expect(helpPanel?.textContent).toContain("断句后首个语音包");
    expect(helpPanel?.textContent).toContain("0ms 不代表模型识别耗时为 0");
    expect(helpPanel?.textContent).toContain("设备扬声器播放延迟");
    expect(helpPanel?.textContent).toContain("缺少任一关键帧则显示“数据不足”");
  });

  it("renders data insufficiency when the total first-packet metric is missing", () => {
    useAssistantStore.setState({
      latestMetrics: {
        turnId: 1,
        sttMs: 0,
        llmTtftMs: 700,
        ttsTtfbMs: 250,
        e2eMs: null,
      },
    });
    const commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: null,
      highestRuntimeRevision: null,
      sendCommand: vi.fn().mockResolvedValue({}),
      reconcileRuntime: vi.fn().mockResolvedValue({}),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    const badge = container.querySelector(".telemetry-grade-pill");
    expect(badge?.textContent).toContain("数据不足");
    expect(badge?.textContent).not.toContain("首包极速");
  });

  it("renders high latency grade (slow / 首包偏高) and symmetric flow steps when speech quality or latency degrades", () => {
    useAssistantStore.setState({
      latestMetrics: {
        turnId: 2,
        sttMs: 3450.5,
        llmTtftMs: 2800.0,
        ttsTtfbMs: 1250.2,
        e2eMs: 7500.7,
      },
    });
    const commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: null,
      highestRuntimeRevision: null,
      sendCommand: vi.fn().mockResolvedValue({}),
      reconcileRuntime: vi.fn().mockResolvedValue({}),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    const badge = container.querySelector(".telemetry-grade-pill");
    expect(badge?.textContent).toContain("首包偏高");
    expect(badge?.textContent).toContain("7500.7ms");
    expect(badge?.classList.contains("slow")).toBe(true);

    const steps = container.querySelectorAll(".telemetry-compact-flow .flow-step");
    expect(steps.length).toBe(3);
    expect(steps[0]?.textContent).toContain("3450.5ms");
    expect(steps[1]?.textContent).toContain("2800ms");
    expect(steps[2]?.textContent).toContain("1250.2ms");

    const separators = container.querySelectorAll(".telemetry-compact-flow .flow-sep");
    expect(separators.length).toBe(2);
  });
});

describe("assistant error notice", () => {
  it("keeps the pipeline reason visible and retries only after an explicit click", () => {
    const onRetry = vi.fn();

    act(() => {
      root.render(createElement(
        AssistantErrorNotice,
        {
          message: "LM Studio 连接失败",
          retryText: "请再试一次",
          onRetry,
        },
      ));
    });

    expect(container.querySelector("[role='alert']")?.textContent)
      .toContain("LM Studio 连接失败");
    const retryButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("重新编辑输入"));
    expect(retryButton).not.toBeUndefined();

    act(() => retryButton?.click());

    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("keeps the error visible without offering a retry when no input is available", () => {
    act(() => {
      root.render(createElement(AssistantErrorNotice, { message: "语音服务暂不可用" }));
    });

    expect(container.querySelector("[role='alert']")?.textContent)
      .toContain("语音服务暂不可用");
    expect(container.querySelector("button")).toBeNull();
  });

  it("keeps the panel failure visible through late closing events and only refills retry text", async () => {
    const sendCommand = vi.fn().mockResolvedValue({});
    const commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: null,
      highestRuntimeRevision: null,
      sendCommand,
      reconcileRuntime: vi.fn().mockResolvedValue({}),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    const emit = (payload: unknown) => {
      act(() => {
        assistantEventHarness.onMessage?.(
          new MessageEvent("message", { data: JSON.stringify(payload) }),
        );
      });
    };

    emit({ type: "stt", state: "final", text: "请再试一次" });
    emit({ type: "system", state: "pipeline_error", message: "LM Studio 连接失败" });

    expect(container.querySelector("[role='alert']")?.textContent)
      .toContain("LM Studio 连接失败");

    emit({ type: "llm", state: "final", text: "已经生成了一半", turn_id: 0 });
    emit({ type: "tts", state: "stopped" });
    expect(container.querySelector("[role='alert']")).not.toBeNull();

    const editButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent?.includes("重新编辑输入"));
    expect(editButton).not.toBeUndefined();
    act(() => editButton?.click());

    expect((container.querySelector(".assistant-text-input") as HTMLInputElement).value)
      .toBe("请再试一次");
    expect(sendCommand).not.toHaveBeenCalled();
    expect(container.querySelector("[role='alert']")).not.toBeNull();

    const sendButton = container.querySelector(".assistant-send-btn") as HTMLButtonElement;
    await act(async () => sendButton.click());
    expect(sendCommand).toHaveBeenCalledWith({ cmd: "send_text", text: "请再试一次" });
    expect(container.querySelector("[role='alert']")).toBeNull();
  });

  it("renders muted microphone ownership without a listening label", () => {
    useUISettingsStore.setState({ micMuted: true });
    const commandSocket: CommandSocketApi = {
      state: "open",
      ready: true,
      snapshot: {
        mode: "assistant",
        pcm_owner: "assistant",
        pipeline: "listening",
        subtitle: "idle",
        mic_muted: true,
        runtime_revision: 1,
      },
      highestRuntimeRevision: 1,
      sendCommand: vi.fn().mockResolvedValue({}),
      reconcileRuntime: vi.fn().mockResolvedValue({}),
    };

    act(() => {
      root.render(createElement(AssistantPanel, { commandSocket }));
    });

    const phaseBar = container.querySelector(".assistant-phase-bar");
    expect(phaseBar?.textContent).toContain("麦克风已静音");
    expect(phaseBar?.textContent).not.toContain("聆听麦克风");

    const phaseBadge = container.querySelector(".assistant-phase-badge");
    expect(phaseBadge?.textContent).toContain("麦克风已静音");
    expect(phaseBadge?.getAttribute("title")).not.toContain("正在接收麦克风语音");
  });
});
