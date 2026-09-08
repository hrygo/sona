import { describe, expect, it } from "vitest";

// The browser bundle does not include this file; Vitest executes it in Node.
// @ts-expect-error Vitest-only source inspection uses Node's filesystem API.
import { readFileSync } from "node:fs";

const subtitleStreamCss = readFileSync("src/components/SubtitleStream.css", "utf8");

function getRuleBody(selector: string): string {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\\]\\]/g, "\\$&");
  const match = subtitleStreamCss.match(new RegExp(`${escapedSelector}\\s*\\{([^}]*)\\}`));
  expect(match, `Missing CSS rule for ${selector}`).not.toBeNull();
  return match?.[1] ?? "";
}

describe("subtitle sidebar output actions", () => {
  it("uses a three-column grid with full-width action buttons", () => {
    const gridRule = getRuleBody(".subtitle-sidebar-actions");
    const buttonRule = getRuleBody(".subtitle-sidebar-action");

    expect(gridRule).toMatch(/display\s*:\s*grid/);
    expect(gridRule).toMatch(/grid-template-columns\s*:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/);
    expect(gridRule).toMatch(/gap\s*:\s*8px/);
    expect(buttonRule).toMatch(/width\s*:\s*100%/);
    expect(buttonRule).toMatch(/justify-content\s*:\s*center/);
  });

  it("falls back to two columns on very narrow sidebars", () => {
    expect(subtitleStreamCss).toMatch(
      /@media\s*\(max-width:\s*360px\)[\s\S]*?\.subtitle-sidebar-actions\s*\{[\s\S]*?grid-template-columns\s*:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/,
    );
  });
});
