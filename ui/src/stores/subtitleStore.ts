import { create } from "zustand";
import type { DisplayBlock, SpeakerStatus } from "../contracts/meetingContract";

/** SpeechRail 字幕快照的前端消费字段。 */
export interface SubtitleLine {
  speaker: string;
  text: string;
  start: string;
  end: string;
  translation?: string | null;
  detected_language?: string | null;
}

export type DiarizationStatus = "off" | "active" | "degraded";

export interface SubtitleDiarizationState {
  status: DiarizationStatus;
  reason: string | null;
}

export interface SubtitleSnapshot {
  lines: SubtitleLine[];
  display_blocks?: DisplayBlock[];
  buffer_transcription: string;
  diarization: SubtitleDiarizationState;
}

export interface SubtitleReducerState {
  readonly lines: SubtitleLine[];
  readonly rawLines?: SubtitleLine[];
  readonly displayBlocks?: DisplayBlock[];
  readonly partial: string;
  readonly diarization: SubtitleDiarizationState;
  readonly clearedOffset?: number;
}

export const STANDALONE_FILLER_CHARS = new Set([
  "嗯",
  "呃",
  "啊",
  "唔",
  "额",
  "诶",
  "哦",
  "呀",
  "吧",
  "哩",
  "哈",
  "呵",
  "咳",
]);

export function isStandaloneFiller(text: string | null | undefined): boolean {
  if (!text) return true;
  const cleaned = text.trim().replace(/[\s.,!?;:…~。！？，、；：—\-]+/gu, "");
  if (!cleaned) return true;
  for (const ch of cleaned) {
    if (!STANDALONE_FILLER_CHARS.has(ch)) return false;
  }
  return true;
}

export function reduceSubtitleSnapshot(
  state: SubtitleReducerState,
  snap: Partial<SubtitleSnapshot>,
): SubtitleReducerState {
  const rawLines = snap.lines ?? state.rawLines ?? state.lines;
  const rawCount = rawLines.length;
  const currentCleared = state.clearedOffset ?? 0;
  // 如果 SpeechRail 新 session 导致 rawLines 变短，重置 offset
  const clearedOffset = currentCleared > rawCount ? 0 : currentCleared;
  const visibleLines = rawLines
    .slice(clearedOffset)
    .filter((line) => !isStandaloneFiller(line.text));

  const incomingPartial = snap.buffer_transcription ?? state.partial;
  const partial = isStandaloneFiller(incomingPartial) ? "" : incomingPartial;
  const diarization = normalizeDiarization(snap.diarization ?? state.diarization);
  const displayBlocks = snap.display_blocks ?? deriveSubtitleDisplayBlocks(visibleLines, diarization.status);

  return {
    rawLines,
    lines: visibleLines,
    displayBlocks,
    partial,
    diarization,
    clearedOffset,
  };
}

/** 严格拒绝被替换协议的 numeric speaker payload。 */
export function isSubtitleSnapshotPayload(value: unknown): value is Partial<SubtitleSnapshot> {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  if (record.lines !== undefined) {
    if (!Array.isArray(record.lines)) return false;
    if (record.lines.some((line) => {
      if (!line || typeof line !== "object") return true;
      const speaker = (line as Record<string, unknown>).speaker;
      return typeof speaker !== "string" || !speaker.trim();
    })) return false;
  }
  if (record.diarization !== undefined && !isDiarizationState(record.diarization)) {
    return false;
  }
  if (record.display_blocks !== undefined) {
    if (!Array.isArray(record.display_blocks)) return false;
    if (record.display_blocks.some((block) => !isDisplayBlock(block))) return false;
  }
  return record.buffer_transcription === undefined || typeof record.buffer_transcription === "string";
}

function isDisplayBlock(value: unknown): value is DisplayBlock {
  if (!value || typeof value !== "object") return false;
  const block = value as Record<string, unknown>;
  const status = block.speaker_status;
  return (
    typeof block.block_id === "string" &&
    typeof block.text === "string" &&
    block.text.trim().length > 0 &&
    Array.isArray(block.item_ids) &&
    Array.isArray(block.source_ids) &&
    typeof block.speaker_color_token === "string" &&
    (status === "identified" ||
      status === "anonymous" ||
      status === "pending" ||
      status === "off" ||
      status === "degraded") &&
    (block.start_ms === null || typeof block.start_ms === "number") &&
    (block.end_ms === null || typeof block.end_ms === "number") &&
    (block.timing_quality === "aligned" || block.timing_quality === "unavailable") &&
    typeof block.is_partial === "boolean"
  );
}

function parseSubtitleTime(value: string): number | null {
  const match = value.trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$/u);
  if (!match) return null;
  const hours = Number(match[1] ?? 0);
  const minutes = Number(match[2]);
  const seconds = Number(match[3]);
  const milliseconds = Number((match[4] ?? "0").padEnd(3, "0"));
  if (minutes > 59 || seconds > 59) return null;
  return (((hours * 60 + minutes) * 60 + seconds) * 1000) + milliseconds;
}

/** 兼容旧字幕 payload：没有 display_blocks 时也按可读块聚合，绝不逐行展示。 */
export function deriveSubtitleDisplayBlocks(
  lines: readonly SubtitleLine[],
  diarizationStatus: DiarizationStatus = "active",
): DisplayBlock[] {
  const blocks: DisplayBlock[] = [];
  const statusForLine: SpeakerStatus = diarizationStatus === "off"
    ? "off"
    : diarizationStatus === "degraded"
      ? "degraded"
      : "anonymous";
  for (const [index, line] of lines.entries()) {
    const startMs = parseSubtitleTime(line.start);
    const endMs = parseSubtitleTime(line.end);
    const speakerKey = statusForLine === "anonymous" ? line.speaker || null : null;
    const previous = blocks.at(-1);
    const sameSpeaker = previous?.speaker_key === speakerKey;
    const gap = previous?.end_ms !== null && previous?.end_ms !== undefined && startMs !== null
      ? startMs - previous.end_ms
      : null;
    const duration = previous?.start_ms !== null && previous?.start_ms !== undefined && endMs !== null
      ? endMs - previous.start_ms
      : null;
    const canMerge =
      previous !== undefined &&
      !previous.is_partial &&
      sameSpeaker &&
      gap !== null &&
      gap >= 0 &&
      gap <= 1200 &&
      duration !== null &&
      duration <= 15000 &&
      previous.text.length + line.text.length <= 180 &&
      !/[。！？!?；;.．]$/u.test(previous.text);
    if (canMerge) {
      blocks[blocks.length - 1] = {
        ...previous,
        source_ids: [...previous.source_ids, `legacy-${index}`],
        text: `${previous.text}${line.text}`,
        end_ms: endMs,
      };
      continue;
    }
    const status: SpeakerStatus = statusForLine === "off" || statusForLine === "degraded"
      ? statusForLine
      : speakerKey
        ? statusForLine
        : "pending";
    blocks.push({
      block_id: `subtitle-block-${index}`,
      item_ids: [`legacy-${index}`],
      source_ids: [`legacy-${index}`],
      order: index,
      speaker_key: speakerKey,
      speaker_name: speakerKey,
      speaker_status: status,
      speaker_color_token: `legacy-${line.speaker || "pending"}`,
      start_ms: startMs,
      end_ms: endMs,
      text: line.text,
      timing_quality: startMs === null || endMs === null ? "unavailable" : "aligned",
      is_partial: false,
    });
  }
  return blocks;
}

/** 将可读块转换为兼容导出器使用的语义化字幕行。 */
export function displayBlocksToSubtitleLines(
  blocks: readonly DisplayBlock[],
): SubtitleLine[] {
  return blocks
    .filter((block) => !block.is_partial)
    .map((block) => ({
      speaker: displayBlockSpeakerLabel(block),
      text: block.text,
      start: formatSubtitleTime(block.start_ms),
      end: formatSubtitleTime(block.end_ms),
    }));
}

/** SRT 只接受有可靠时间范围的完整可读块，避免写入伪精确时间。 */
export function toSRTDisplayBlocks(blocks: readonly DisplayBlock[]): string {
  return toSRT(
    displayBlocksToSubtitleLines(blocks).filter((line) => line.start !== "" && line.end !== ""),
  );
}

/** 纯文本导出按可读块输出；无时间块显式保留“时间不可用”。 */
export function toPlainTextDisplayBlocks(blocks: readonly DisplayBlock[]): string {
  return displayBlocksToSubtitleLines(blocks)
    .map((line) => {
      const time = line.start && line.end ? `[${line.start} - ${line.end}]` : "[时间不可用]";
      return `${time} ${line.speaker}: ${line.text}`;
    })
    .join("\n");
}

function displayBlockSpeakerLabel(block: DisplayBlock): string {
  if (block.speaker_name) return block.speaker_name;
  if (block.speaker_key) return block.speaker_key;
  switch (block.speaker_status) {
    case "off":
      return "分人未启用";
    case "degraded":
      return "分人不可用";
    case "pending":
    case "unknown":
    case "tentative":
      return "正在确认";
    default:
      return "未识别说话人";
  }
}

function formatSubtitleTime(value: number | null): string {
  if (value === null || !Number.isFinite(value) || value < 0) return "";
  const totalSeconds = Math.floor(value / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const milliseconds = value % 1000;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function isDiarizationState(value: unknown): value is SubtitleDiarizationState {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return (record.status === "off" || record.status === "active" || record.status === "degraded")
    && (record.reason === null || typeof record.reason === "string");
}

function normalizeDiarization(
  value: SubtitleDiarizationState | undefined,
): SubtitleDiarizationState {
  if (!value || !isDiarizationState(value)) return { status: "off", reason: null };
  return {
    status: value.status,
    reason: value.reason?.trim() || null,
  };
}

interface SubtitleState {
  lines: SubtitleLine[];
  rawLines: SubtitleLine[];
  displayBlocks: DisplayBlock[];
  partial: string;
  diarization: SubtitleDiarizationState;
  connected: boolean;
  starredIndices: Set<number>;
  clearedOffset: number;
  applySnapshot: (snap: Partial<SubtitleSnapshot>) => void;
  setConnected: (v: boolean) => void;
  toggleStar: (index: number) => void;
  clear: () => void;
  /** SpeechRail epoch 重置（{"type":"reset"}）：新时间轴，旧行与星标全部作废。 */
  resetForReconnect: () => void;
}

export const useSubtitleStore = create<SubtitleState>((set) => ({
  lines: [],
  rawLines: [],
  displayBlocks: [],
  partial: "",
  diarization: { status: "off", reason: null },
  connected: false,
  starredIndices: new Set<number>(),
  clearedOffset: 0,
  applySnapshot: (snap) =>
    set((state) => {
      const reduced = reduceSubtitleSnapshot(state, snap);
      return {
        ...state,
        ...reduced,
      };
    }),
  setConnected: (v) => set({ connected: v }),
  toggleStar: (index) =>
    set((s) => {
      const next = new Set(s.starredIndices);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return { starredIndices: next };
    }),
  clear: () =>
    set((state) => {
      const totalRaw = state.rawLines.length > 0 ? state.rawLines.length : (state.lines.length + state.clearedOffset);
      return {
        clearedOffset: totalRaw,
        lines: [],
        displayBlocks: [],
        partial: "",
        diarization: state.diarization,
        starredIndices: new Set<number>(),
      };
    }),
  resetForReconnect: () =>
    set({
      lines: [],
      rawLines: [],
      displayBlocks: [],
      partial: "",
      diarization: { status: "off", reason: null },
      clearedOffset: 0,
      starredIndices: new Set<number>(),
    }),
}));

/** 格式化匿名字符串说话人；空值使用保留的未知标签。 */
export function formatSpeaker(speaker: string): string {
  const normalized = speaker.trim();
  return normalized || "未识别说话人";
}

/** 说话人配色：按匿名字符串稳定取色。 */
export function speakerColor(speaker: string): string {
  const palette = [
    "#2563eb",
    "#16a34a",
    "#ea580c",
    "#9333ea",
    "#0891b2",
    "#e11d48",
    "#65a30d",
    "#475569",
  ];
  let hash = 2166136261;
  for (const char of speaker) {
    hash ^= char.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619);
  }
  return palette[Math.abs(hash) % palette.length];
}

/** 生成 SRT 文件内容（索引 + 时间戳 + 文本 + 空行）。 */
export function toSRT(lines: SubtitleLine[]): string {
  return lines
    .map((line, i) => {
      const start = srtTime(line.start);
      const end = srtTime(line.end) || start;
      const text = line.translation && line.translation.trim()
        ? `${line.text}\n${line.translation}`
        : line.text;
      return `${i + 1}\n${start} --> ${end}\n${text}\n`;
    })
    .join("\n");
}

/** 生成结构化 Markdown 会议纪要与对话转写。 */
export function toMarkdownNotes(lines: SubtitleLine[], starred: Set<number>): string {
  const now = new Date();
  const dateStr = now.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  const timeStr = now.toLocaleTimeString("zh-CN", { hour12: false });

  const uniqueSpeakers = Array.from(new Set(lines.map((l) => formatSpeaker(l.speaker))));
  const totalDuration =
    lines.length > 0
      ? `${lines[0]?.start ?? "00:00:00"} ~ ${lines.at(-1)?.end ?? lines.at(-1)?.start ?? "00:00:00"}`
      : "00:00:00";

  let md = `# Sona 会议与语音对话纪要\n\n`;
  md += `> 自动生成于：${dateStr} ${timeStr} | 引擎：SpeechRail / Apple Silicon\n\n`;

  md += `## 📋 会议概要\n\n`;
  md += `- **记录时间**：${dateStr} ${timeStr}\n`;
  md += `- **有效时间段**：\`${totalDuration}\`\n`;
  md += `- **发言人数**：${uniqueSpeakers.length} 位 (${uniqueSpeakers.join(", ")})\n`;
  md += `- **总转写条目**：${lines.length} 条\n`;
  md += `- **重点星标标记**：${starred.size} 条\n\n`;

  // 重点星标部分
  if (starred.size > 0) {
    md += `## ⭐ 重点发言与结论速览\n\n`;
    lines.forEach((line, idx) => {
      if (starred.has(idx)) {
        const spk = formatSpeaker(line.speaker);
        md += `- **[${line.start}] ${spk}**：${line.text}\n`;
        if (line.translation) {
          md += `  > 译文：${line.translation}\n`;
        }
      }
    });
    md += `\n---\n\n`;
  }

  // 完整时序转写
  md += `## 📝 完整对话时序记录\n\n`;
  let currentSpeaker: string | null = null;

  lines.forEach((line, idx) => {
    const isStarred = starred.has(idx);
    const starTag = isStarred ? " ⭐" : "";
    const spk = formatSpeaker(line.speaker);

    if (spk !== currentSpeaker) {
      currentSpeaker = spk;
      md += `\n### 👤 ${spk} (\`${line.start}\`)\n\n`;
    }

    md += `- \`[${line.start} - ${line.end || line.start}]\` ${line.text}${starTag}\n`;
    if (line.translation && line.translation.trim()) {
      md += `  > 译文：${line.translation}\n`;
    }
  });

  md += `\n\n---\n*由 Sona 本地离线工作台导出*\n`;
  return md;
}

/** "0:00:03" / "0:00:03,500" → SRT "00:00:03,000"。 */
function srtTime(raw: string | undefined): string {
  if (!raw) return "";
  const normalized = raw.trim().replace(",", ".");
  const [clock = "", fraction = ""] = normalized.split(".", 2);
  const [h, m, s] = clock.split(":").map(Number);
  const millis = Number(fraction.padEnd(3, "0").slice(0, 3));
  if (Number.isNaN(h) || Number.isNaN(m) || Number.isNaN(s) || Number.isNaN(millis)) return "";
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(
    s,
  ).padStart(2, "0")},${String(millis).padStart(3, "0")}`;
}
