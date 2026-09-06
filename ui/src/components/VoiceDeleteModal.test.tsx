import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { VoiceDeleteModal } from "./VoiceDeleteModal";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("VoiceDeleteModal", () => {
  let container: HTMLDivElement;
  let root: Root;
  let onClose: ReturnType<typeof vi.fn>;
  let onConfirm: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    onClose = vi.fn();
    onConfirm = vi.fn();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it("does not render when isOpen is false", () => {
    act(() => {
      root.render(
        <VoiceDeleteModal
          isOpen={false}
          voiceName="知性姐姐"
          onClose={onClose}
          onConfirm={onConfirm}
        />
      );
    });
    expect(container.querySelector(".voice-delete-modal-dialog")).toBeNull();
  });

  it("renders with voice name and standard delete warnings", () => {
    act(() => {
      root.render(
        <VoiceDeleteModal
          isOpen={true}
          voiceName="知性姐姐"
          isCurrentActive={false}
          onClose={onClose}
          onConfirm={onConfirm}
        />
      );
    });

    const dialog = container.querySelector(".voice-delete-modal-dialog");
    expect(dialog).not.toBeNull();
    expect(dialog?.textContent).toContain("删除自定义音色");
    expect(dialog?.textContent).toContain("“知性姐姐”");
    expect(dialog?.textContent).toContain("删除后本地声学模型参数与提示词配置将无法恢复");
    expect(dialog?.textContent).not.toContain("正在使用中");
  });

  it("displays active fallback warning when voice is currently active", () => {
    act(() => {
      root.render(
        <VoiceDeleteModal
          isOpen={true}
          voiceName="当前克隆音色"
          isCurrentActive={true}
          onClose={onClose}
          onConfirm={onConfirm}
        />
      );
    });

    const dialog = container.querySelector(".voice-delete-modal-dialog");
    expect(dialog?.textContent).toContain("该音色当前正在使用中，删除后系统将自动切回官方默认原声音色");
  });

  it("calls onClose when cancel button is clicked", () => {
    act(() => {
      root.render(
        <VoiceDeleteModal
          isOpen={true}
          voiceName="知性姐姐"
          onClose={onClose}
          onConfirm={onConfirm}
        />
      );
    });

    const cancelBtn = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => b.textContent?.includes("取消")
    );
    expect(cancelBtn).toBeDefined();
    act(() => {
      cancelBtn!.click();
    });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("calls onConfirm and then closes on confirm button click", async () => {
    onConfirm.mockResolvedValue(undefined);
    act(() => {
      root.render(
        <VoiceDeleteModal
          isOpen={true}
          voiceName="知性姐姐"
          onClose={onClose}
          onConfirm={onConfirm}
        />
      );
    });

    const confirmBtn = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find(
      (b) => b.textContent?.includes("确认删除")
    );
    expect(confirmBtn).toBeDefined();

    await act(async () => {
      confirmBtn!.click();
    });

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape key press", () => {
    act(() => {
      root.render(
        <VoiceDeleteModal
          isOpen={true}
          voiceName="知性姐姐"
          onClose={onClose}
          onConfirm={onConfirm}
        />
      );
    });

    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });

    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
