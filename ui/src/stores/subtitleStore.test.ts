import { describe, expect, it } from "vitest";

import {
  formatSpeaker,
  isStandaloneFiller,
  reduceSubtitleSnapshot,
  toSRT,
} from "./subtitleStore";

describe("formatSpeaker", () => {
  it("formats positive and zero speaker IDs cleanly", () => {
    expect(formatSpeaker(0)).toBe("说话人 0");
    expect(formatSpeaker(1)).toBe("说话人 1");
    expect(formatSpeaker(2)).toBe("说话人 2");
  });

  it("normalizes negative or unassigned speaker IDs to 0 instead of 未知", () => {
    expect(formatSpeaker(-1)).toBe("说话人 0");
    expect(formatSpeaker(-2)).toBe("说话人 0");
  });
});

describe("subtitle snapshot reducer", () => {
  it("replaces confirmed lines and accepts an empty partial", () => {
    const previous = {
      lines: [{ speaker: 0, text: "旧", start: "00:00:00", end: "00:00:01" }],
      partial: "处理中",
    };

    const next = reduceSubtitleSnapshot(previous, { lines: [], buffer_transcription: "" });

    expect(next.lines).toEqual([]);
    expect(next.partial).toBe("");
  });

  it("filters out old raw lines when clearedOffset is set", () => {
    const previous = {
      rawLines: [
        { speaker: 0, text: "第一句", start: "00:00:00", end: "00:00:01" },
        { speaker: 1, text: "第二句", start: "00:00:01", end: "00:00:02" },
      ],
      lines: [],
      partial: "",
      clearedOffset: 2,
    };

    const snapshotWithNewLines = {
      lines: [
        { speaker: 0, text: "第一句", start: "00:00:00", end: "00:00:01" },
        { speaker: 1, text: "第二句", start: "00:00:01", end: "00:00:02" },
        { speaker: 0, text: "第三句 (新)", start: "00:00:02", end: "00:00:03" },
      ],
      buffer_transcription: "正在说话",
    };

    const next = reduceSubtitleSnapshot(previous, snapshotWithNewLines);

    expect(next.lines).toHaveLength(1);
    expect(next.lines[0]?.text).toBe("第三句 (新)");
    expect(next.partial).toBe("正在说话");
  });
});

describe("toSRT", () => {
  it("accepts dot and comma milliseconds and exports comma format", () => {
    const output = toSRT([
      { speaker: 0, text: "第一句", start: "0:00:03.500", end: "0:00:04,125" },
    ]);

    expect(output).toContain("00:00:03,500 --> 00:00:04,125");
  });
});

describe("isStandaloneFiller", () => {
  it("identifies noise hallucination fillers and whitespace/punctuation", () => {
    expect(isStandaloneFiller("")).toBe(true);
    expect(isStandaloneFiller("   ")).toBe(true);
    expect(isStandaloneFiller("。！？")).toBe(true);
    expect(isStandaloneFiller("嗯")).toBe(true);
    expect(isStandaloneFiller("嗯。")).toBe(true);
    expect(isStandaloneFiller("啊！")).toBe(true);
    expect(isStandaloneFiller("呃……")).toBe(true);
    expect(isStandaloneFiller("嗯。哦。嗯。")).toBe(true);
    expect(isStandaloneFiller("唔……额……")).toBe(true);
  });

  it("preserves real speech containing fillers as part of a sentence", () => {
    expect(isStandaloneFiller("嗯，好的")).toBe(false);
    expect(isStandaloneFiller("啊对对对")).toBe(false);
    expect(isStandaloneFiller("嗯我知道了")).toBe(false);
    expect(isStandaloneFiller("开会讨论")).toBe(false);
    expect(isStandaloneFiller("Hello world")).toBe(false);
  });
});

describe("reduceSubtitleSnapshot filler suppression", () => {
  it("filters out standalone filler lines and suppresses standalone filler partial", () => {
    const previous = {
      lines: [],
      partial: "",
    };

    const snapshotWithFillers = {
      lines: [
        { speaker: 0, text: "嗯。", start: "00:00:01", end: "00:00:02" },
        { speaker: 0, text: "啊。", start: "00:00:02", end: "00:00:03" },
        { speaker: 0, text: "大家好，现在开会。", start: "00:00:03", end: "00:00:05" },
      ],
      buffer_transcription: "嗯。",
    };

    const next = reduceSubtitleSnapshot(previous, snapshotWithFillers);

    // 孤立 filler 行被过滤，仅保留有真实语义内容的发言行
    expect(next.lines).toHaveLength(1);
    expect(next.lines[0]?.text).toBe("大家好，现在开会。");
    // 孤立草稿 "嗯。" 也被抑制为空串
    expect(next.partial).toBe("");
  });

  it("preserves real partial text", () => {
    const previous = {
      lines: [],
      partial: "",
    };

    const snapshot = {
      lines: [],
      buffer_transcription: "正在发言中...",
    };

    const next = reduceSubtitleSnapshot(previous, snapshot);
    expect(next.partial).toBe("正在发言中...");
  });
});
