import { describe, it, expect, vi } from "vitest";
import {
  displayBlocksToExportSegments,
  exportMeetingData,
  generateJsonContent,
  generateMarkdownContent,
  generatePlainTextContent,
  generateSrtContent,
  msToReadableTime,
  msToSrtTimestamp,
} from "./exportUtils";
import {
  mockMeetingDetailCompleted,
  mockMinutesCompleted,
  mockSegments,
} from "../test/fixtures/meetingFixtures";

const readableBlocks = [
  {
    block_id: "block-1",
    item_ids: ["item-1"],
    source_ids: ["item-1"],
    order: 0,
    speaker_key: null,
    speaker_name: "正在确认",
    speaker_status: "pending" as const,
    speaker_color_token: "speaker-neutral",
    start_ms: 1_000,
    end_ms: 2_000,
    text: "实时字幕。",
    timing_quality: "aligned" as const,
    is_partial: false,
  },
];

describe("exportUtils", () => {
  it("converts milliseconds to standard SRT timestamp format", () => {
    expect(msToSrtTimestamp(0)).toBe("00:00:00,000");
    expect(msToSrtTimestamp(15420)).toBe("00:00:15,420");
    expect(msToSrtTimestamp(3665123)).toBe("01:01:05,123");
  });

  it("converts milliseconds to readable time MM:SS", () => {
    expect(msToReadableTime(0)).toBe("00:00");
    expect(msToReadableTime(65000)).toBe("01:05");
  });

  it("generates standard SRT content", () => {
    const srt = generateSrtContent(mockSegments);
    expect(srt).toContain("1\n00:00:00,000 --> 00:00:12,450\n[张三 (架构师)] 大家好");
    expect(srt).toContain("2\n00:00:13,000 --> 00:00:24,800\n[李四 (前端负责人)] 前端部分");
  });

  it("exports readable blocks instead of legacy character segments", () => {
    const segments = displayBlocksToExportSegments([
      {
        ...readableBlocks[0],
        text: "实时字幕。",
      },
      {
        ...readableBlocks[0],
        block_id: "partial",
        item_ids: [],
        source_ids: [],
        text: "未完成",
        is_partial: true,
      },
    ]);

    expect(segments).toEqual([
      {
        id: "item-1",
        speaker_name: "正在确认",
        start_ms: 1_000,
        end_ms: 2_000,
        text: "实时字幕。",
      },
    ]);
    expect(generateSrtContent(segments)).toContain("实时字幕。\n");
    expect(generateSrtContent(segments)).not.toContain("\n实\n");
  });

  it("generates structured plain text content", () => {
    const txt = generatePlainTextContent(
      mockMeetingDetailCompleted,
      mockSegments,
      mockMinutesCompleted,
    );
    expect(txt).toContain("会议主题：实时语音与字幕产品评审");
    expect(txt).toContain("【AI 会议纪要】");
    expect(txt).toContain("概要：");
    expect(txt).toContain("核心议题：");
    expect(txt).toContain("【会议转录记录】");
  });

  it("generates markdown content with tables and checklist", () => {
    const md = generateMarkdownContent(
      mockMeetingDetailCompleted,
      mockSegments,
      { ...mockMinutesCompleted, content_markdown: null },
    );
    expect(md).toContain("# 会议纪要：实时语音与字幕产品评审");
    expect(md).toContain("## 1. 会议概要");
    expect(md).toContain("## 2. 核心议题");
    expect(md).toContain("## 3. 决策事项");
    expect(md).toContain("## 4. 待办行动项");
    expect(md).toContain("| 时间 | 说话人 | 重点 | 转录内容 |");
  });

  it("generates markdown and plain text with highlighted starred segments", () => {
    const starredSet = new Set([mockSegments[0].id]);
    const md = generateMarkdownContent(
      mockMeetingDetailCompleted,
      mockSegments,
      { ...mockMinutesCompleted, content_markdown: null },
      starredSet,
    );
    expect(md).toContain("| ⭐ | **大家好");

    const txt = generatePlainTextContent(
      mockMeetingDetailCompleted,
      mockSegments,
      mockMinutesCompleted,
      starredSet,
    );
    expect(txt).toContain("[⭐ 重点]:");
  });

  it("generates json export content", () => {
    const jsonStr = generateJsonContent(
      mockMeetingDetailCompleted,
      mockSegments,
      mockMinutesCompleted,
    );
    const parsed = JSON.parse(jsonStr);
    expect(parsed.meeting.id).toBe(mockMeetingDetailCompleted.id);
    expect(parsed.segments).toHaveLength(4);
    expect(parsed.minutes.id).toBe(mockMinutesCompleted.id);
  });

  it("keeps display blocks in JSON export while providing block-level compatibility segments", () => {
    const parsed = JSON.parse(
      generateJsonContent(
        mockMeetingDetailCompleted,
        mockSegments,
        mockMinutesCompleted,
        readableBlocks,
      ),
    );
    expect(parsed.segments).toHaveLength(1);
    expect(parsed.segments[0].text).toBe("实时字幕。");
    expect(parsed.display_blocks[0].text).toBe("实时字幕。");
  });

  it("aggregates legacy character segments before meeting export", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:meeting-export");
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLAnchorElement.prototype, "click", {
      configurable: true,
      value: vi.fn(),
    });
    const characterSegments = ["实", "时", "字幕。"].map((text, index) => ({
      ...mockSegments[0],
      id: `legacy-${index}`,
      text,
      start_ms: 1_000 + index * 100,
      end_ms: 1_100 + index * 100,
    }));

    exportMeetingData(mockMeetingDetailCompleted, characterSegments, null, "srt");
    const blob = createObjectURL.mock.calls[0]?.[0] as Blob;
    const content = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsText(blob);
    });

    expect(content).toContain("实时字幕。");
    expect(content).not.toContain("\n实\n");
  });
});
