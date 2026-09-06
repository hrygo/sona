import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  MeetingSpeakerModal,
  formatFriendlySpeakerFallback,
} from "./MeetingSpeakerModal";

// Extend global for React 19 testing flag
declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("formatFriendlySpeakerFallback", () => {
  it("converts unknown or empty key safely", () => {
    expect(formatFriendlySpeakerFallback("")).toBe("未知发言人");
    expect(formatFriendlySpeakerFallback("unknown")).toBe("待识别发言人");
    expect(formatFriendlySpeakerFallback("UNKNOWN")).toBe("待识别发言人");
  });

  it("extracts speaker index from various speechrail technical identifiers", () => {
    expect(formatFriendlySpeakerFallback("spk_01")).toBe("说话人 1");
    expect(formatFriendlySpeakerFallback("spk_2")).toBe("说话人 2");
    expect(formatFriendlySpeakerFallback("s3")).toBe("说话人 3");
    expect(
      formatFriendlySpeakerFallback("speechrail:spk-e2e-1:speaker-source:s1:spk_01")
    ).toBe("说话人 1");
    expect(
      formatFriendlySpeakerFallback("speechrail:spk-e2e-1:speaker-source:s1:spk_05")
    ).toBe("说话人 5");
  });

  it("handles general identifiers gracefully", () => {
    expect(formatFriendlySpeakerFallback("guest_speaker")).toBe("参会发言人");
  });
});

describe("MeetingSpeakerModal Component", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("does not render when isOpen is false", () => {
    act(() => {
      root.render(
        <MeetingSpeakerModal
          isOpen={false}
          speakerKey="spk_01"
          currentDisplayName="说话人 1"
          onClose={vi.fn()}
          onSave={vi.fn()}
        />
      );
    });

    expect(container.querySelector(".modal-speaker-dialog")).toBeNull();
  });

  it("renders with friendly speaker name and hides raw technical ID by default", () => {
    act(() => {
      root.render(
        <MeetingSpeakerModal
          isOpen={true}
          speakerKey="speechrail:spk-e2e-1:speaker-source:s1:spk_01"
          currentDisplayName="张三"
          onClose={vi.fn()}
          onSave={vi.fn()}
        />
      );
    });

    expect(container.textContent).toContain("设置说话人称谓");
    expect(container.textContent).toContain("张三");
    expect(container.textContent).toContain("(说话人 1)");
    // Raw technical id is NOT visible initially
    expect(container.textContent).not.toContain("speechrail:spk-e2e-1");
    expect(container.textContent).toContain("查看技术通道 ID");

    // Click toggle to show technical ID
    const toggleBtn = container.querySelector(".speaker-modal-tech-id-toggle") as HTMLButtonElement;
    expect(toggleBtn).not.toBeNull();
    act(() => {
      toggleBtn.click();
    });
    expect(container.textContent).toContain("speechrail:spk-e2e-1:speaker-source:s1:spk_01");
    expect(container.textContent).toContain("收起通道标识");
  });

  it("allows selecting preset roles and saving", async () => {
    const handleSave = vi.fn().mockResolvedValue(undefined);
    const handleClose = vi.fn();

    act(() => {
      root.render(
        <MeetingSpeakerModal
          isOpen={true}
          speakerKey="spk_02"
          currentDisplayName="说话人 2"
          onClose={handleClose}
          onSave={handleSave}
        />
      );
    });

    const presetButtons = container.querySelectorAll(".speaker-preset-btn");
    expect(presetButtons.length).toBeGreaterThan(0);

    // Click "主持人" preset
    const hostBtn = Array.from(presetButtons).find((b) => b.textContent?.includes("主持人")) as HTMLButtonElement;
    expect(hostBtn).not.toBeNull();
    act(() => {
      hostBtn.click();
    });

    const input = container.querySelector(".speaker-modal-input") as HTMLInputElement;
    expect(input.value).toBe("主持人");

    // Click save
    const saveBtn = container.querySelector(".speaker-modal-btn-save") as HTMLButtonElement;
    await act(async () => {
      saveBtn.click();
    });

    expect(handleSave).toHaveBeenCalledTimes(1);
    expect(handleSave).toHaveBeenCalledWith("spk_02", "主持人");
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it("handles clearing input, typing, and Escape cancel", () => {
    const handleClose = vi.fn();

    act(() => {
      root.render(
        <MeetingSpeakerModal
          isOpen={true}
          speakerKey="spk_01"
          currentDisplayName="原始名字"
          onClose={handleClose}
          onSave={vi.fn()}
        />
      );
    });

    const input = container.querySelector(".speaker-modal-input") as HTMLInputElement;
    expect(input.value).toBe("原始名字");

    const clearBtn = container.querySelector(".speaker-modal-input-clear") as HTMLButtonElement;
    expect(clearBtn).not.toBeNull();
    act(() => {
      clearBtn.click();
    });
    expect(input.value).toBe("");

    // Escape should close modal
    act(() => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(handleClose).toHaveBeenCalledTimes(1);
  });
});
