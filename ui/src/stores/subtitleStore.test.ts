import { describe, expect, it } from "vitest";

import {
  formatSpeaker,
  deriveSubtitleDisplayBlocks,
  isStandaloneFiller,
  reduceSubtitleSnapshot,
  toSRT,
  type SubtitleReducerState,
  type SubtitleSnapshot,
} from "./subtitleStore";

describe("formatSpeaker", () => {
  it("preserves scoped anonymous speaker labels", () => {
    expect(formatSpeaker("会话 1 · A")).toBe("会话 1 · A");
    expect(formatSpeaker("会话 1 · B")).toBe("会话 1 · B");
  });

  it("uses a reserved label for an empty speaker", () => {
    expect(formatSpeaker("")).toBe("未识别说话人");
  });
});

describe("subtitle snapshot reducer", () => {
  it("replaces confirmed lines and accepts an empty partial", () => {
    const previous: SubtitleReducerState = {
      lines: [{ speaker: "会话 1 · A", text: "旧", start: "00:00:00", end: "00:00:01" }],
      partial: "处理中",
      diarization: { status: "active", reason: null },
    };

    const next = reduceSubtitleSnapshot(previous, { lines: [], buffer_transcription: "" });

    expect(next.lines).toEqual([]);
    expect(next.partial).toBe("");
  });

  it("prefers backend display blocks over legacy flat lines", () => {
    const block = {
      block_id: "block-1",
      item_ids: ["item-1"],
      source_ids: ["item-1#segment-1"],
      order: 0,
      speaker_key: null,
      speaker_name: null,
      speaker_status: "pending" as const,
      speaker_color_token: "speaker-pending",
      start_ms: 0,
      end_ms: 1000,
      text: "完整正文",
      timing_quality: "aligned" as const,
      is_partial: false,
    };
    const next = reduceSubtitleSnapshot(
      { lines: [], partial: "", diarization: { status: "off", reason: null } },
      { lines: [{ speaker: "旧", text: "逐字", start: "00:00:00", end: "00:00:01" }], display_blocks: [block] },
    );

    expect(next.displayBlocks).toEqual([block]);
  });

  it("aggregates legacy subtitle characters and preserves degraded speaker state", () => {
    const blocks = deriveSubtitleDisplayBlocks(
      [
        { speaker: "会话 1 · A", text: "实", start: "00:00:01.000", end: "00:00:01.100" },
        { speaker: "会话 1 · A", text: "时", start: "00:00:01.100", end: "00:00:01.200" },
        { speaker: "会话 1 · A", text: "字幕。", start: "00:00:01.200", end: "00:00:02.000" },
      ],
      "degraded",
    );

    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.text).toBe("实时字幕。");
    expect(blocks[0]?.speaker_status).toBe("degraded");
    expect(blocks[0]?.speaker_key).toBeNull();
  });

  it("filters out old raw lines when clearedOffset is set", () => {
    const previous: SubtitleReducerState = {
      rawLines: [
        { speaker: "会话 1 · A", text: "第一句", start: "00:00:00", end: "00:00:01" },
        { speaker: "会话 1 · B", text: "第二句", start: "00:00:01", end: "00:00:02" },
      ],
      lines: [],
      partial: "",
      diarization: { status: "active", reason: null },
      clearedOffset: 2,
    };

    const snapshotWithNewLines: Partial<SubtitleSnapshot> = {
      lines: [
        { speaker: "会话 1 · A", text: "第一句", start: "00:00:00", end: "00:00:01" },
        { speaker: "会话 1 · B", text: "第二句", start: "00:00:01", end: "00:00:02" },
        { speaker: "会话 1 · A", text: "第三句 (新)", start: "00:00:02", end: "00:00:03" },
      ],
      buffer_transcription: "正在说话",
      diarization: { status: "active", reason: null },
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
      { speaker: "会话 1 · A", text: "第一句", start: "0:00:03.500", end: "0:00:04,125" },
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
    const previous: SubtitleReducerState = {
      lines: [],
      partial: "",
      diarization: { status: "off", reason: null },
    };

    const snapshotWithFillers: Partial<SubtitleSnapshot> = {
      lines: [
        { speaker: "会话 1 · A", text: "嗯。", start: "00:00:01", end: "00:00:02" },
        { speaker: "会话 1 · A", text: "啊。", start: "00:00:02", end: "00:00:03" },
        { speaker: "会话 1 · A", text: "大家好，现在开会。", start: "00:00:03", end: "00:00:05" },
      ],
      buffer_transcription: "嗯。",
      diarization: { status: "off", reason: null },
    };

    const next = reduceSubtitleSnapshot(previous, snapshotWithFillers);

    // 孤立 filler 行被过滤，仅保留有真实语义内容的发言行
    expect(next.lines).toHaveLength(1);
    expect(next.lines[0]?.text).toBe("大家好，现在开会。");
    // 孤立草稿 "嗯。" 也被抑制为空串
    expect(next.partial).toBe("");
  });

  it("preserves real partial text", () => {
    const previous: SubtitleReducerState = {
      lines: [],
      partial: "",
      diarization: { status: "off", reason: null },
    };

    const snapshot = {
      lines: [],
      buffer_transcription: "正在发言中...",
    };

    const next = reduceSubtitleSnapshot(previous, snapshot);
    expect(next.partial).toBe("正在发言中...");
  });
});
