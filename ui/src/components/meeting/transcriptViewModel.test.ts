import { describe, expect, it } from "vitest";
import type { TranscriptSegment } from "../../contracts/meetingContract";
import {
  deriveReadingBlocks,
  getSegmentsForBlock,
} from "./transcriptViewModel";

describe("transcriptViewModel (阅读视图派生模型 §5.1, §6.2)", () => {
  it("returns empty array for empty segments", () => {
    expect(deriveReadingBlocks([])).toEqual([]);
  });

  it("merges consecutive short-gap segments from the same speaker and epoch", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 3000,
        text: "各位同事好，",
        source_epoch: 1,
      },
      {
        id: "seg-2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 3500, // gap = 500ms <= 1200ms
        end_ms: 7000,
        text: "今天讨论技术架构方案。",
        source_epoch: 1,
      },
    ];

    const blocks = deriveReadingBlocks(segments);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.segment_ids).toEqual(["seg-1", "seg-2"]);
    expect(blocks[0]?.start_ms).toBe(0);
    expect(blocks[0]?.end_ms).toBe(7000);
    expect(blocks[0]?.text).toBe("各位同事好，今天讨论技术架构方案。");
  });

  it("collapses repeated standalone filler segments while retaining raw segment ids", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "filler-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 300,
        text: "嗯",
        source_epoch: 1,
      },
      {
        id: "filler-2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 2000,
        end_ms: 2300,
        text: "嗯。",
        source_epoch: 1,
      },
      {
        id: "filler-3",
        order: 3,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 7000,
        end_ms: 7300,
        text: "嗯",
        source_epoch: 1,
      },
    ];

    const blocks = deriveReadingBlocks(segments, new Set(["filler-2"]));
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.text).toBe("嗯 × 3");
    expect(blocks[0]?.segment_ids).toEqual(["filler-1", "filler-2", "filler-3"]);
    expect(blocks[0]?.isStarred).toBe(true);
    expect(getSegmentsForBlock(blocks[0]!, segments)).toHaveLength(3);
  });

  it("does not collapse fillers across speaker, epoch, token, or substantive-text boundaries", () => {
    const first: TranscriptSegment = {
      id: "filler-first",
      order: 1,
      speaker_key: "spk_0",
      speaker_name: "说话人 1",
      start_ms: 0,
      end_ms: 300,
      text: "嗯",
      source_epoch: 1,
    };
    const cases: Array<[string, TranscriptSegment]> = [
      [
        "different speaker",
        { ...first, id: "filler-speaker", speaker_key: "spk_1", speaker_name: "说话人 2", start_ms: 1000, end_ms: 1300 },
      ],
      [
        "different source epoch",
        { ...first, id: "filler-epoch", source_epoch: 2, start_ms: 1000, end_ms: 1300 },
      ],
      [
        "different filler token",
        { ...first, id: "filler-token", text: "呃", start_ms: 1000, end_ms: 1300 },
      ],
      [
        "repeated characters are substantive text",
        { ...first, id: "filler-repeated", text: "嗯嗯", start_ms: 1000, end_ms: 1300 },
      ],
      [
        "filler followed by substantive text",
        { ...first, id: "filler-substantive", text: "嗯我同意", start_ms: 1000, end_ms: 1300 },
      ],
    ];

    for (const [label, second] of cases) {
      expect(deriveReadingBlocks([first, second]), label).toHaveLength(2);
    }
  });

  it("does not collapse fillers beyond the dedicated gap or duration limits", () => {
    const makeFiller = (
      id: string,
      start_ms: number,
      end_ms: number,
    ): TranscriptSegment => ({
      id,
      order: start_ms + 1,
      speaker_key: "spk_0",
      speaker_name: "说话人 1",
      start_ms,
      end_ms,
      text: "嗯",
      source_epoch: 1,
    });

    expect(
      deriveReadingBlocks([
        makeFiller("gap-first", 0, 300),
        makeFiller("gap-second", 5_601, 5_901),
      ]),
    ).toHaveLength(2);

    expect(
      deriveReadingBlocks([
        makeFiller("duration-first", 0, 300),
        makeFiller("duration-second", 2_500, 30_301),
      ]),
    ).toHaveLength(2);
  });

  it("inserts space between ASCII/English words when merging", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-e1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 2000,
        text: "Hello",
      },
      {
        id: "seg-e2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 2200,
        end_ms: 4000,
        text: "World",
      },
    ];

    const blocks = deriveReadingBlocks(segments);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.text).toBe("Hello World");
  });

  it("never merges across different speakers", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 3000,
        text: "请问第一阶段什么时候发布？",
      },
      {
        id: "seg-2",
        order: 2,
        speaker_key: "spk_1",
        speaker_name: "说话人 2",
        start_ms: 3200, // very short gap
        end_ms: 6000,
        text: "预计下周二发布。",
      },
    ];

    const blocks = deriveReadingBlocks(segments);
    expect(blocks).toHaveLength(2);
    expect(blocks[0]?.speaker_key).toBe("spk_0");
    expect(blocks[1]?.speaker_key).toBe("spk_1");
  });

  it("never merges across different source epochs", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 3000,
        text: "断线前的内容",
        source_epoch: 1,
      },
      {
        id: "seg-2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 3200,
        end_ms: 6000,
        text: "重连后的内容",
        source_epoch: 2,
      },
    ];

    const blocks = deriveReadingBlocks(segments);
    expect(blocks).toHaveLength(2);
  });

  it("splits blocks when gap exceeds maxGapMs threshold", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 3000,
        text: "第一句话",
      },
      {
        id: "seg-2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 5000, // gap = 2000ms > default 1200ms
        end_ms: 8000,
        text: "长停顿后的第二句话",
      },
    ];

    const blocks = deriveReadingBlocks(segments);
    expect(blocks).toHaveLength(2);
  });

  it("splits blocks when total duration exceeds maxDurationMs", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 10000,
        text: "第一长段",
      },
      {
        id: "seg-2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 10500,
        end_ms: 22000, // 22000 - 0 = 22s > 15s max
        text: "第二长段",
      },
    ];

    const blocks = deriveReadingBlocks(segments);
    expect(blocks).toHaveLength(2);
  });

  it("propagates starredIds into block.isStarred", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 2000,
        text: "普通段落",
      },
      {
        id: "seg-2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 2200,
        end_ms: 4000,
        text: "重点结论段落",
      },
    ];

    const starred = new Set(["seg-2"]);
    const blocks = deriveReadingBlocks(segments, starred);
    expect(blocks).toHaveLength(1);
    expect(blocks[0]?.isStarred).toBe(true);
  });

  it("allows tracing block back to original segments via getSegmentsForBlock", () => {
    const segments: TranscriptSegment[] = [
      {
        id: "seg-1",
        order: 1,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 0,
        end_ms: 2000,
        text: "片段 1",
      },
      {
        id: "seg-2",
        order: 2,
        speaker_key: "spk_0",
        speaker_name: "说话人 1",
        start_ms: 2200,
        end_ms: 4000,
        text: "片段 2",
      },
    ];

    const blocks = deriveReadingBlocks(segments);
    const sourceSegments = getSegmentsForBlock(blocks[0]!, segments);
    expect(sourceSegments).toHaveLength(2);
    expect(sourceSegments[0]?.id).toBe("seg-1");
    expect(sourceSegments[1]?.id).toBe("seg-2");
  });
});
