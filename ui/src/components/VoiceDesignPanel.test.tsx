import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import type { VoiceCatalogItem, VoiceDesignRequest, VoiceQualityReport } from "../contracts/voiceContract";
import { VoiceServiceError, voiceService } from "../services/voiceService";
import { playAudioBlob } from "../utils/audioPlayback";
import { VoiceDesignPanel } from "./VoiceDesignPanel";
import { VoiceCandidateReview } from "./VoiceCandidateReview";
import { DESIGN_REFERENCE_TEXT, VOICE_DESIGN_EXAMPLES, validateDesignText } from "./voiceDesignExamples";

vi.mock("../utils/audioPlayback", () => ({ playAudioBlob: vi.fn().mockResolvedValue(undefined) }));
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let container: HTMLDivElement;
let root: Root;
let created: ReturnType<typeof vi.fn>;
let selected: ReturnType<typeof vi.fn>;
let busy: ReturnType<typeof vi.fn>;
const reference: VoiceQualityReport = {
  policy_version: "voice_quality_v1", status: "pass", run_id: "reference", tested_at: "2026-09-12",
  reference: { duration_seconds: 8, transcript_match: 1 }, failure_codes: [],
};
const output: VoiceQualityReport = {
  ...reference, reference: undefined, run_id: "output",
  synthesis: { probe_count: 18, successful_probe_count: 18, deterministic: true, transcript_match: 1 },
};
const voice: VoiceCatalogItem = {
  id: "design_test", name: "知性女声", mode: "clone", is_system: false, quality: reference,
  ref_text: DESIGN_REFERENCE_TEXT, creation: {
    origin: "generated", method: "voice_design_reference_v1", model_artifact: "qwen-voice-design",
    model_revision: "a".repeat(40), seed: 42, instruction_sha256: "a".repeat(64),
    reference_text_sha256: "b".repeat(64), reference_audio_sha256: "c".repeat(64), preprocessing_version: "energy_v1",
  },
};

beforeEach(() => {
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
  created = vi.fn(); selected = vi.fn(); busy = vi.fn();
  vi.mocked(playAudioBlob).mockResolvedValue(undefined);
  vi.spyOn(voiceService, "preview").mockResolvedValue(new Blob(["audio"]));
  vi.spyOn(voiceService, "speech").mockResolvedValue(new Blob(["audio"]));
  vi.spyOn(voiceService, "qualityRun").mockResolvedValue(output);
  vi.spyOn(voiceService, "design").mockImplementation(async (request) => ({
    voice: { ...voice, id: request.id, name: request.name }, synthesis_validation: "unevaluated",
  }));
});
afterEach(() => { act(() => root.unmount()); container.remove(); vi.restoreAllMocks(); });
function render(canRegister = true) {
  act(() => root.render(<VoiceDesignPanel canRegister={canRegister} canPreview onCreated={created} onSelect={selected} onBusyChange={busy} />));
}
function button(text: string) {
  const result = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes(text));
  expect(result, `button ${text}`).toBeDefined(); return result!;
}
async function click(text: string) { await act(async () => { button(text).click(); }); }
function change(id: string, value: string) {
  const input = container.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${id}`)!;
  act(() => {
    const proto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("provides six editable use-case examples with explicit selection and a valid default reference", async () => {
  render(); expect(container.querySelectorAll(".voice-example-card")).toHaveLength(6);
  expect(validateDesignText(VOICE_DESIGN_EXAMPLES[0]!.instruction, DESIGN_REFERENCE_TEXT, 42)).toBeNull();
  for (const example of VOICE_DESIGN_EXAMPLES) {
    await click(example.title);
    expect((container.querySelector("#design-instruction-input") as HTMLTextAreaElement).value).toBe(example.instruction);
    expect(button(example.title).getAttribute("aria-pressed")).toBe("true");
  }
  expect(voiceService.design).not.toHaveBeenCalled();
});

it("protects edited descriptions and preserves custom names and reference text on template replacement", async () => {
  render(); await click("温柔知性");
  change("design-name-input", "我的命名"); change("design-instruction-input", "我的自定义声音描述");
  const customReference = "这是一段我自己编写的参考文本，用来检查模板切换时不会覆盖实际的朗读内容。";
  change("design-preview-input", customReference);
  await click("技术讲解");
  expect(container.textContent).toContain("当前描述有修改");
  expect((container.querySelector("#design-instruction-input") as HTMLTextAreaElement).value).toBe("我的自定义声音描述");
  await click("保留当前描述"); await click("技术讲解"); await click("替换描述");
  expect((container.querySelector("#design-name-input") as HTMLInputElement).value).toBe("我的命名");
  expect((container.querySelector("#design-preview-input") as HTMLTextAreaElement).value).toBe(customReference);
  expect((container.querySelector("#design-instruction-input") as HTMLTextAreaElement).value).toBe(VOICE_DESIGN_EXAMPLES[1]!.instruction);
});

it("previews the same edited reference and seed without creating or selecting a voice", async () => {
  render(); await click("温柔知性"); change("design-seed", "21"); await click("试听草稿");
  expect(voiceService.preview).toHaveBeenCalledWith(expect.objectContaining({
    input: DESIGN_REFERENCE_TEXT, instruction: VOICE_DESIGN_EXAMPLES[0]!.instruction,
    language: "zh", seed: 21,
  }), expect.any(AbortSignal));
  expect(voiceService.design).not.toHaveBeenCalled(); expect(created).not.toHaveBeenCalled();
  expect(selected).not.toHaveBeenCalled(); expect(container.textContent).toContain("保存时会重新生成");
});

it("registers only through the explicit new endpoint and preserves output-unevaluated state", async () => {
  const legacy = vi.spyOn(voiceService, "create");
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  expect(voiceService.design).toHaveBeenCalledOnce();
  expect(voiceService.design).toHaveBeenCalledWith(expect.objectContaining({
    id: expect.stringMatching(/^design_[a-f0-9]{32}$/), name: "知性女声", reference_text: DESIGN_REFERENCE_TEXT,
    instruction: VOICE_DESIGN_EXAMPLES[0]!.instruction, seed: 42, language: "zh",
  }), expect.any(AbortSignal));
  expect(legacy).not.toHaveBeenCalled(); expect(created).toHaveBeenCalledOnce();
  expect(voiceService.qualityRun).not.toHaveBeenCalled(); expect(selected).not.toHaveBeenCalled();
  expect(container.textContent).toContain("参考已核验"); expect(container.textContent).toContain("尚未评估");
  expect(button("确认使用此音色").disabled).toBe(true);
});

it("requires both scoped output acceptance and completed actual audition before explicit selection", async () => {
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  await click("检查输出（18 段）");
  expect(voiceService.qualityRun).toHaveBeenCalledWith(expect.stringMatching(/^design_/), { runs: 3 }, expect.any(AbortSignal));
  expect(button("确认使用此音色").disabled).toBe(true);
  await click("试听实际音色");
  const synthesis = vi.mocked(voiceService.speech).mock.calls[0]![0];
  expect(synthesis.voice).toMatch(/^design_/); expect(synthesis.input).not.toBe(DESIGN_REFERENCE_TEXT);
  expect(selected).not.toHaveBeenCalled(); expect(button("确认使用此音色").disabled).toBe(false);
  await click("确认使用此音色"); expect(selected).toHaveBeenCalledWith(synthesis.voice);
});

it.each(["warn", "reject", "unevaluated"] as const)("does not activate with a %s output report", async (status) => {
  vi.mocked(voiceService.qualityRun).mockResolvedValue({ ...output, status });
  act(() => root.render(<VoiceCandidateReview voice={voice} onUpdated={created} onSelect={selected} />));
  await click("检查输出（18 段）"); await click("试听实际音色");
  expect(button("确认使用此音色").disabled).toBe(true); expect(selected).not.toHaveBeenCalled();
});

it("rejects a reference-only report returned by an old quality endpoint", async () => {
  vi.mocked(voiceService.qualityRun).mockResolvedValue(reference);
  act(() => root.render(<VoiceCandidateReview voice={voice} onUpdated={created} onSelect={selected} />));
  await click("检查输出（18 段）"); await click("试听实际音色");
  expect(container.textContent).toContain("不能把参考验收视为输出通过");
  expect(button("确认使用此音色").disabled).toBe(true);
});

it("retains a saved voice when an output check fails and permits retry rather than recreation", async () => {
  vi.mocked(voiceService.qualityRun).mockRejectedValueOnce(new Error("unavailable"));
  act(() => root.render(<VoiceCandidateReview voice={voice} onUpdated={created} onSelect={selected} />));
  await click("检查输出（18 段）");
  expect(container.textContent).toContain("音色仍已保存");
  expect(voiceService.design).not.toHaveBeenCalled();
  await click("检查输出（18 段）"); expect(container.textContent).toContain("输出检查通过");
});

it("clears previous acceptance and audition when rechecking output", async () => {
  act(() => root.render(<VoiceCandidateReview voice={{ ...voice, quality: output }} onUpdated={created} onSelect={selected} />));
  await click("试听实际音色"); expect(button("确认使用此音色").disabled).toBe(false);
  vi.mocked(voiceService.qualityRun).mockRejectedValueOnce(new Error("down"));
  await click("重新检查输出"); expect(button("确认使用此音色").disabled).toBe(true);
  expect(created).toHaveBeenLastCalledWith(expect.objectContaining({ quality: undefined }));
});

it("does not claim heard when saved-voice playback fails", async () => {
  vi.mocked(playAudioBlob).mockRejectedValueOnce(new Error("autoplay denied"));
  act(() => root.render(<VoiceCandidateReview voice={{ ...voice, quality: output }} onUpdated={created} onSelect={selected} />));
  await click("试听实际音色"); expect(button("确认使用此音色").disabled).toBe(true);
  expect(container.textContent).toContain("试听失败");
});

it.each([404, 405])("explains unsupported server %s and never falls back to legacy creation", async (status) => {
  const legacy = vi.spyOn(voiceService, "create");
  vi.mocked(voiceService.design).mockRejectedValue(new VoiceServiceError("old server", status, "not_found"));
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  expect(container.textContent).toContain("请升级"); expect(legacy).not.toHaveBeenCalled();
  expect(created).not.toHaveBeenCalled(); expect(selected).not.toHaveBeenCalled();
  expect((container.querySelector("fieldset") as HTMLFieldSetElement).disabled).toBe(false);
});

it("retains the exact attempt after an ambiguous transport failure and reconciles without recreating", async () => {
  let savedRequest: VoiceDesignRequest | undefined;
  vi.mocked(voiceService.design).mockImplementation(async (request) => {
    savedRequest = request; throw new VoiceServiceError("down", 0, "speechrail_unavailable");
  });
  const list = vi.spyOn(voiceService, "list").mockImplementation(async () => [{ ...voice, id: savedRequest!.id }]);
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  expect(container.textContent).toContain(savedRequest!.id);
  expect((container.querySelector("fieldset") as HTMLFieldSetElement).disabled).toBe(true);
  await click("使用同一 ID 重试");
  expect(vi.mocked(voiceService.design).mock.calls[1]![0]).toEqual(savedRequest);
  await click("检查保存结果"); expect(list).toHaveBeenCalledOnce();
  expect(created).toHaveBeenCalledOnce(); expect(voiceService.design).toHaveBeenCalledTimes(2);
});

it("blocks duplicate clicks and aborts outstanding registration on unmount without a late creation callback", async () => {
  let finish!: (value: { voice: VoiceCatalogItem; synthesis_validation: "unevaluated" }) => void;
  vi.mocked(voiceService.design).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(); await click("温柔知性");
  await act(async () => { const b = button("生成并保存可复用音色"); b.click(); b.click(); });
  expect(voiceService.design).toHaveBeenCalledOnce(); expect(busy).toHaveBeenLastCalledWith(true);
  const signal = vi.mocked(voiceService.design).mock.calls[0]![1]!;
  act(() => root.unmount()); expect(signal.aborted).toBe(true);
  await act(async () => { finish({ voice, synthesis_validation: "unevaluated" }); });
  expect(created).not.toHaveBeenCalled(); expect(selected).not.toHaveBeenCalled();
});

it("cancels actual playback and output verification when review unmounts", async () => {
  vi.mocked(voiceService.qualityRun).mockImplementation(() => new Promise(() => undefined));
  act(() => root.render(<VoiceCandidateReview voice={voice} onUpdated={created} onSelect={selected} />));
  await click("检查输出（18 段）");
  const signal = vi.mocked(voiceService.qualityRun).mock.calls[0]![2]!;
  act(() => root.unmount()); expect(signal.aborted).toBe(true);
});

it("keeps templates editable but cannot register when model metadata is incomplete", async () => {
  render(false); await click("技术讲解");
  expect(button("生成并保存可复用音色").disabled).toBe(true);
  expect(button("试听草稿").disabled).toBe(false);
  expect(voiceService.design).not.toHaveBeenCalled();
});

it.each(["短文本", "！".repeat(25), "文".repeat(241)])("rejects invalid reference locally: %s", (value) => {
  expect(validateDesignText("温柔明亮的普通话成年声音", value, 42)).not.toBeNull();
});
it.each([-1, 1.5, 4294967296, NaN])("rejects invalid seed %s", (seed) => {
  expect(validateDesignText("温柔明亮的普通话成年声音", DESIGN_REFERENCE_TEXT, seed)).not.toBeNull();
});

it.each([
  { ...output, synthesis: { probe_count: 3, successful_probe_count: 3, deterministic: true, transcript_match: 1 } },
  { ...output, synthesis: { probe_count: 18, successful_probe_count: 18, deterministic: true } },
  { ...output, synthesis: { probe_count: 18, successful_probe_count: 17, deterministic: true, transcript_match: 1 } },
])("does not activate with incomplete synthesis evidence despite a pass label", async (quality) => {
  act(() => root.render(<VoiceCandidateReview voice={{ ...voice, quality }} onUpdated={created} onSelect={selected} />));
  await click("试听实际音色"); expect(button("确认使用此音色").disabled).toBe(true);
});

it("treats audio-mode conflicts as a preflight rejection rather than a saved-ID collision", async () => {
  vi.mocked(voiceService.design).mockRejectedValue(new VoiceServiceError("busy", 409, "mode_conflict"));
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  expect(container.textContent).toContain("会议或字幕正在占用");
  expect((container.querySelector("fieldset") as HTMLFieldSetElement).disabled).toBe(false);
  expect(container.textContent).not.toContain("待确认注册 ID");
});

it("invalidates an earlier audition when the next attempt fails", async () => {
  act(() => root.render(<VoiceCandidateReview voice={{ ...voice, quality: output }} onUpdated={created} onSelect={selected} />));
  await click("试听实际音色");
  expect(button("确认使用此音色").disabled).toBe(false);
  vi.mocked(playAudioBlob).mockRejectedValueOnce(new Error("device lost"));
  await click("试听实际音色");
  expect(button("确认使用此音色").disabled).toBe(true);
});

it("waits for activation acknowledgement and keeps a failed candidate retryable", async () => {
  let acknowledge!: (value: boolean) => void;
  selected.mockImplementationOnce(() => new Promise<boolean>((resolve) => { acknowledge = resolve; }));
  act(() => root.render(<VoiceCandidateReview voice={{ ...voice, quality: output }} onUpdated={created} onSelect={selected} />));
  await click("试听实际音色");
  await act(async () => { const apply = button("确认使用此音色"); apply.click(); apply.click(); });
  expect(selected).toHaveBeenCalledOnce();
  expect(button("正在确认启用").disabled).toBe(true);
  await act(async () => { acknowledge(false); });
  expect(container.textContent).toContain("启用未获确认");
  expect(button("确认使用此音色").disabled).toBe(false);
});

it("lets a definite ASR rejection return to editing rather than trapping the draft", async () => {
  vi.mocked(voiceService.design).mockRejectedValueOnce(new VoiceServiceError("asr missing", 503, "transcription_unavailable"));
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  expect((container.querySelector("fieldset") as HTMLFieldSetElement).disabled).toBe(false);
  expect(container.textContent).not.toContain("待确认注册 ID");
});

it("returns from a saved candidate to its editable description using a new target ID", async () => {
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  const first = vi.mocked(voiceService.design).mock.calls[0]![0].id;
  await click("基于此描述再设计");
  expect((container.querySelector("#design-instruction-input") as HTMLTextAreaElement).value).toBe(VOICE_DESIGN_EXAMPLES[0]!.instruction);
  await click("生成并保存可复用音色");
  expect(vi.mocked(voiceService.design).mock.calls[1]![0].id).not.toBe(first);
});

it("cancels an output wait without retaining old acceptance or issuing activation", async () => {
  vi.mocked(voiceService.qualityRun).mockImplementation((_id, _request, signal) => new Promise((_, reject) => {
    signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }));
  act(() => root.render(<VoiceCandidateReview voice={{ ...voice, quality: output }} onUpdated={created} onSelect={selected} />));
  await click("试听实际音色"); expect(button("确认使用此音色").disabled).toBe(false);
  await click("重新检查输出"); await click("停止等待检查");
  expect(container.textContent).toContain("已停止等待输出检查");
  expect(button("确认使用此音色").disabled).toBe(true); expect(selected).not.toHaveBeenCalled();
});
it("supports cancelling a generation wait and checking the same uncertain attempt", async () => {
  vi.mocked(voiceService.design).mockImplementation((_request, signal) => new Promise((_, reject) => {
    signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  }));
  render(); await click("温柔知性"); await click("生成并保存可复用音色"); await click("停止等待");
  expect(container.textContent).toContain("检查保存结果"); expect(created).not.toHaveBeenCalled();
  const before = vi.mocked(voiceService.design).mock.calls[0]![0];
  vi.mocked(voiceService.design).mockResolvedValue({ voice: { ...voice, id: before.id }, synthesis_validation: "unevaluated" });
  await click("使用同一 ID 重试"); expect(vi.mocked(voiceService.design).mock.calls[1]![0]).toEqual(before);
});
it("clears a deleted generated candidate and returns to its editable description", async () => {
  render(); await click("温柔知性"); await click("生成并保存可复用音色");
  const id = vi.mocked(voiceService.design).mock.calls[0]![0].id;
  await act(async () => root.render(<VoiceDesignPanel canRegister canPreview onCreated={created} onSelect={selected} onBusyChange={busy} deletedVoiceId={id} />));
  expect(container.querySelector("#design-instruction-input")).not.toBeNull();
  expect(container.textContent).not.toContain("确认使用此音色");
});
