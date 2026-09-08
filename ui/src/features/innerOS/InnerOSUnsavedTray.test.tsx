import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { InnerOSUnsavedTray } from "./InnerOSUnsavedTray";
import type { InnerOSAnswer } from "./contracts";
import type { UnsavedExchangeItem } from "./innerOSStore";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe("InnerOSUnsavedTray", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  const answer: InnerOSAnswer = {
    intent: "fact",
    evidence: [],
    facts: [{ text: "已确认一个事实", evidence_segment_ids: [] }],
    judgements: [],
    draft: null,
    limitations: [],
  };

  const item: UnsavedExchangeItem = {
    queryId: "q-1",
    meetingId: "m-1",
    question: "刚才确认了什么？",
    intent: "fact",
    answer,
    createdAt: "2026-08-27T12:00:00.000Z",
    saved: false,
  };

  it("expands answer content without nesting a full answer card", () => {
    act(() => {
      root.render(
        <InnerOSUnsavedTray
          items={[item]}
          onSaveItem={vi.fn().mockResolvedValue(undefined)}
          onDismissItem={vi.fn()}
        />,
      );
    });

    const summaryButton = container.querySelector(".inner-os-tray-item-q") as HTMLButtonElement;
    expect(summaryButton).not.toBeNull();

    act(() => {
      summaryButton.click();
    });

    expect(container.querySelector(".inner-os-answer-content")).not.toBeNull();
    expect(container.querySelector(".inner-os-answer-card")).toBeNull();
    expect(container.querySelector(".inner-os-save-btn")).toBeNull();

    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("dismisses every staged exchange from the tray header", () => {
    const secondItem: UnsavedExchangeItem = { ...item, queryId: "q-2" };

    const TrayHarness = () => {
      const [visibleItems, setVisibleItems] = useState<readonly UnsavedExchangeItem[]>([item, secondItem]);
      return (
        <InnerOSUnsavedTray
          items={visibleItems}
          onSaveItem={vi.fn().mockResolvedValue(undefined)}
          onDismissItem={(queryId) => {
            setVisibleItems((currentItems) => currentItems.filter((currentItem) => currentItem.queryId !== queryId));
          }}
        />
      );
    };

    act(() => {
      root.render(<TrayHarness />);
    });

    const dismissAllButton = container.querySelector(
      '[data-testid="inner-os-tray-dismiss-all"]',
    ) as HTMLButtonElement | null;
    expect(dismissAllButton).not.toBeNull();
    if (!dismissAllButton) return;

    act(() => {
      dismissAllButton.click();
    });

    expect(container.querySelector('[data-testid="inner-os-unsaved-tray"]')).toBeNull();

    act(() => {
      root.unmount();
    });
    container.remove();
  });
});
