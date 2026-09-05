import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { VoiceStudioModal } from "./VoiceStudioModal";
import type { VoiceCatalogItem } from "./assistantPresentation";

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
  vi.unstubAllGlobals();
});

function renderStudio(currentId = "default") {
  act(() => {
    root.render(
      <VoiceStudioModal
        currentVoiceId={currentId}
        availableVoices={MOCK_VOICES}
        onSelectVoice={onSelectVoice}
        onVoiceCreated={onVoiceCreated}
        onVoiceDeleted={onVoiceDeleted}
        onClose={onClose}
      />
    );
  });
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
