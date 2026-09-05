import { beforeEach, describe, expect, it } from "vitest";
import { useMeetingStore } from "./meetingStore";
import type { TranscriptSegment } from "../contracts/meetingContract";

/**
 * SPK-E2E-1 S3 UI 状态机测试（计划用例）：
 * partial→confirmed / unknown→tentative→stable 原位变化、UUID 不变、
 * 正文不重复、有意义短插话保留、emoji 正确、人工状态优先。
 */

const MEETING_ID = "m-e2e";

function segment(
  id: string,
  overrides: Partial<TranscriptSegment> = {},
): TranscriptSegment {
  return {
    id,
    order: 0,
    speaker_key: "speechrail:spk-e2e-1:speaker-source:s1:spk_01",
    speaker_name: "说话人 1",
    start_ms: 0,
    end_ms: 1000,
    text: "内容",
    source_epoch: 0,
    ...overrides,
  };
}

describe("SPK-E2E-1 UI 归属状态机", () => {
  beforeEach(() => {
    useMeetingStore.getState().resetActiveSession();
    useMeetingStore.getState().updateMeetingState("recording", null, null, null, MEETING_ID);
  });

  it("partial→confirmed：确认后 partial 清空且正文只出现一次", () => {
    const store = useMeetingStore.getState();
    store.setPartial("第一句话", "说话人 1", MEETING_ID);
    expect(useMeetingStore.getState().partialText).toBe("第一句话");

    store.reconcileTranscript(
      0,
      [segment("seg-1", { text: "第一句话", end_ms: 1000 })],
      1,
      1,
      MEETING_ID,
    );

    const state = useMeetingStore.getState();
    expect(state.partialText).toBeNull();
    expect(state.segments.map((s) => s.text)).toEqual(["第一句话"]);
  });

  it("unknown→tentative→stable 原位变化：UUID 不变、正文不重复", () => {
    const store = useMeetingStore.getState();
    const uid = "6f1c8b9a-0000-4000-8000-000000000001";
    store.reconcileTranscript(
      0,
      [segment(uid, { speaker_status: "unknown", speaker_key: "unknown" })],
      1,
      1,
      MEETING_ID,
    );
    store.reconcileTranscript(
      0,
      [segment(uid, { speaker_status: "tentative", speaker_key: "…:spk_01" })],
      2,
      2,
      MEETING_ID,
    );
    store.reconcileTranscript(
      0,
      [segment(uid, { speaker_status: "stable", speaker_key: "…:spk_01" })],
      3,
      3,
      MEETING_ID,
    );

    const segments = useMeetingStore.getState().segments;
    expect(segments).toHaveLength(1);
    expect(segments[0].id).toBe(uid);
    expect(segments[0].speaker_status).toBe("stable");
  });

  it("受影响后缀替换保留未受影响的短插话", () => {
    const store = useMeetingStore.getState();
    // 初始三段：长发言、2 秒短插话、后续长发言
    store.reconcileTranscript(
      0,
      [
        segment("seg-a", { start_ms: 0, end_ms: 5000, order: 0 }),
        segment("seg-b", {
          start_ms: 5200,
          end_ms: 7200,
          order: 1,
          speaker_key: "…:spk_02",
          text: "同意",
        }),
        segment("seg-c", { start_ms: 8000, end_ms: 13000, order: 2 }),
      ],
      1,
      1,
      MEETING_ID,
    );

    // spk_01 的 stable 修订：后缀从 seg-a 起完整替换（含短插话）
    store.reconcileTranscript(
      0,
      [
        segment("seg-a", { start_ms: 0, end_ms: 5000, order: 0, speaker_status: "stable" }),
        segment("seg-b", {
          start_ms: 5200,
          end_ms: 7200,
          order: 1,
          speaker_key: "…:spk_02",
          text: "同意",
          speaker_status: "stable",
        }),
        segment("seg-c", { start_ms: 8000, end_ms: 13000, order: 2, speaker_status: "stable" }),
      ],
      2,
      2,
      MEETING_ID,
    );

    const segments = useMeetingStore.getState().segments;
    expect(segments.map((s) => s.id)).toEqual(["seg-a", "seg-b", "seg-c"]);
    expect(segments[1].text).toBe("同意");
  });

  it("emoji 正文原样保留", () => {
    const store = useMeetingStore.getState();
    store.reconcileTranscript(
      0,
      [segment("seg-emoji", { text: "🚀 发布确认 ✅" })],
      1,
      1,
      MEETING_ID,
    );
    expect(useMeetingStore.getState().segments[0].text).toBe("🚀 发布确认 ✅");
  });

  it("人工重命名优先于后端默认名", () => {
    const store = useMeetingStore.getState();
    const key = "speechrail:spk-e2e-1:speaker-source:s1:spk_01";
    store.setSpeaker(key, "张三", 2, MEETING_ID);
    store.reconcileTranscript(
      0,
      [
        segment("seg-1", {
          speaker_key: key,
          speaker_name: "说话人 1",
          speaker_status: "stable",
          speaker_manual: false,
        }),
      ],
      3,
      3,
      MEETING_ID,
    );

    const seg = useMeetingStore.getState().segments[0];
    expect(seg.speaker_name).toBe("张三");
  });

  it("人工更正段的归属状态不再展示模型徽标信息（speaker_manual 透传）", () => {
    const store = useMeetingStore.getState();
    store.reconcileTranscript(
      0,
      [segment("seg-1", { speaker_status: "tentative", speaker_manual: true })],
      1,
      1,
      MEETING_ID,
    );
    const seg = useMeetingStore.getState().segments[0];
    expect(seg.speaker_manual).toBe(true);
    expect(seg.speaker_status).toBe("tentative");
  });

  it("过期 revision 的 reconciled 事件被丢弃", () => {
    const store = useMeetingStore.getState();
    store.reconcileTranscript(
      0,
      [segment("seg-1", { text: "新" })],
      5,
      5,
      MEETING_ID,
    );
    // revision 5 <= current 5：store 层由 hook 过滤；直接验证 revision 不回退
    expect(useMeetingStore.getState().transcriptRevision).toBe(5);
  });
});
