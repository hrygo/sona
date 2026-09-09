import React, { useEffect, useMemo, useRef, useState } from "react";
import type {
  DisplayBlock,
  SpeakerStatus,
  TranscriptSegment,
} from "../../contracts/meetingContract";
import { formatTimeRange } from "./MeetingGapAlert";
import { showToast } from "../Toast";
import { deriveReadingBlocks } from "./transcriptViewModel";
import { copyTextToClipboard } from "../../utils/clipboard";

interface MeetingTranscriptViewerProps {
  /** Migration fallback; new payloads should provide displayBlocks. */
  segments: readonly TranscriptSegment[];
  displayBlocks?: readonly DisplayBlock[];
  highlightedSegmentId: string | null;
  onRenameSpeaker: (speakerKey: string, currentName: string) => void;
  starredIds?: ReadonlySet<string>;
  onToggleStarSegment?: (segmentId: string) => void;
}

type ReadableBlock = DisplayBlock & { readonly starId: string };
type NormalizedSpeakerStatus = Exclude<SpeakerStatus, "unknown" | "tentative" | "stable">;

const SPEAKER_COLORS = [
  "#2563eb",
  "#047857",
  "#b45309",
  "#be185d",
  "#0e7490",
  "#6d28d9",
];

const SPEAKER_STATUS_LABELS: Record<NormalizedSpeakerStatus, string> = {
  identified: "已识别",
  anonymous: "匿名说话人",
  pending: "正在确认",
  off: "分人未启用",
  degraded: "分人不可用",
};

function normalizeSpeakerStatus(status: SpeakerStatus | undefined): NormalizedSpeakerStatus {
  if (status === "stable") return "identified";
  if (status === "unknown" || status === "tentative" || !status) return "pending";
  return status;
}

function speakerLabel(block: Pick<DisplayBlock, "speaker_name" | "speaker_status">): string {
  const status = normalizeSpeakerStatus(block.speaker_status);
  if ((status === "identified" || status === "anonymous") && block.speaker_name) {
    return block.speaker_name;
  }
  return SPEAKER_STATUS_LABELS[status];
}

function fallbackDisplayBlocks(segments: readonly TranscriptSegment[]): DisplayBlock[] {
  return deriveReadingBlocks(segments).map((block, index) => {
    const status = normalizeSpeakerStatus(
      segments.find((segment) => segment.id === block.segment_ids[0])?.speaker_status || "stable",
    );
    return {
      block_id: block.block_id,
      item_ids: block.segment_ids,
      source_ids: block.segment_ids,
      order: index,
      speaker_key: block.speaker_key || null,
      speaker_name: block.speaker_name || null,
      speaker_status: status,
      speaker_color_token: `legacy-${index}`,
      start_ms: block.start_ms,
      end_ms: block.end_ms,
      text: block.text,
      timing_quality: "aligned",
      is_partial: false,
    };
  });
}

function highlightMatch(text: string, query: string) {
  if (!query.trim()) return text;
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));
  return (
    <>
      {parts.map((part, index) =>
        part.toLowerCase() === query.toLowerCase() ? (
          <mark key={index} className="transcript-search-highlight">
            {part}
          </mark>
        ) : (
          <React.Fragment key={index}>{part}</React.Fragment>
        ),
      )}
    </>
  );
}

function formatBlockTime(block: DisplayBlock): string {
  if (block.start_ms === null || block.end_ms === null) return "时间不可用";
  return formatTimeRange(block.start_ms, block.end_ms);
}

export function MeetingTranscriptViewer({
  segments,
  displayBlocks,
  highlightedSegmentId,
  onRenameSpeaker,
  starredIds: propStarredIds,
  onToggleStarSegment: propToggleStarSegment,
}: MeetingTranscriptViewerProps) {
  const [search, setSearch] = useState("");
  const [selectedSpeaker, setSelectedSpeaker] = useState("all");
  const [localStarredIds, setLocalStarredIds] = useState<Set<string>>(() => new Set());
  const starredIds = propStarredIds ?? localStarredIds;
  const [filterStarredOnly, setFilterStarredOnly] = useState(false);
  const [isFollowing, setIsFollowing] = useState(true);
  const [hasNewContent, setHasNewContent] = useState(false);
  const logRef = useRef<HTMLDivElement | null>(null);
  const previousBlockCount = useRef(0);

  const sourceBlocks = useMemo(
    () => (displayBlocks && displayBlocks.length > 0 ? [...displayBlocks] : fallbackDisplayBlocks(segments)),
    [displayBlocks, segments],
  );

  const speakerStats = useMemo(() => {
    const map = new Map<string, { key: string; name: string; count: number }>();
    for (const block of sourceBlocks) {
      const status = normalizeSpeakerStatus(block.speaker_status);
      const key = block.speaker_key || `status:${status}`;
      const current = map.get(key) || { key, name: speakerLabel(block), count: 0 };
      current.count += 1;
      map.set(key, current);
    }
    return Array.from(map.values()).map((speaker, index) => ({
      ...speaker,
      color: SPEAKER_COLORS[index % SPEAKER_COLORS.length],
      percent: sourceBlocks.length > 0 ? Math.round((speaker.count / sourceBlocks.length) * 100) : 0,
    }));
  }, [sourceBlocks]);

  const filtered = useMemo<ReadableBlock[]>(() => {
    return sourceBlocks
      .filter((block) => !block.is_partial)
      .map((block) => ({
        ...block,
        starId: block.item_ids[0] || block.block_id,
      }))
      .filter((block) => {
        const status = normalizeSpeakerStatus(block.speaker_status);
        const speakerFilter = block.speaker_key || `status:${status}`;
        const label = speakerLabel(block).toLowerCase();
        const matchesSpeaker = selectedSpeaker === "all" || selectedSpeaker === speakerFilter;
        const matchesSearch =
          !search.trim() ||
          block.text.toLowerCase().includes(search.toLowerCase()) ||
          label.includes(search.toLowerCase());
        const matchesStarred =
          !filterStarredOnly || starredIds.has(block.starId) || starredIds.has(block.block_id);
        return matchesSpeaker && matchesSearch && matchesStarred;
      });
  }, [filterStarredOnly, search, selectedSpeaker, sourceBlocks, starredIds]);

  const degraded = sourceBlocks.some(
    (block) => normalizeSpeakerStatus(block.speaker_status) === "degraded",
  );

  useEffect(() => {
    const count = sourceBlocks.filter((block) => !block.is_partial).length;
    if (count > previousBlockCount.current) {
      if (isFollowing) {
        const list = logRef.current;
        if (list) {
          if (typeof list.scrollTo === "function") {
            list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
          } else {
            list.scrollTop = list.scrollHeight;
          }
        }
      } else {
        setHasNewContent(true);
      }
    }
    previousBlockCount.current = count;
  }, [isFollowing, sourceBlocks]);

  const handleScroll = () => {
    const list = logRef.current;
    if (!list) return;
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight <= 32;
    setIsFollowing(atBottom);
    if (atBottom) setHasNewContent(false);
  };

  const scrollToBottom = () => {
    const list = logRef.current;
    if (!list) return;
    setIsFollowing(true);
    setHasNewContent(false);
    if (typeof list.scrollTo === "function") {
      list.scrollTo({ top: list.scrollHeight, behavior: "smooth" });
    } else {
      list.scrollTop = list.scrollHeight;
    }
  };

  const toggleStar = (starId: string) => {
    if (propToggleStarSegment) {
      const currentlyStarred = starredIds.has(starId);
      propToggleStarSegment(starId);
      showToast(
        currentlyStarred ? "已取消重点标记" : "⭐ 已标记此发言为重点",
        currentlyStarred ? "info" : "success",
      );
      return;
    }
    setLocalStarredIds((current) => {
      const next = new Set(current);
      if (next.has(starId)) {
        next.delete(starId);
        showToast("已取消重点标记", "info");
      } else {
        next.add(starId);
        showToast("⭐ 已标记此发言为重点", "success");
      }
      return next;
    });
  };

  const copyBlock = async (text: string) => {
    try {
      await copyTextToClipboard(text);
      showToast("转录文本已复制到剪贴板", "info");
    } catch {
      showToast("复制失败", "warning");
    }
  };

  return (
    <section className="transcript-pane transcript-reading-pane" aria-label="会议转录阅读">
      <div className="pane-header">
        <div className="pane-title-group">
          <span className="pane-icon" aria-hidden="true">📝</span>
          <span className="pane-title">会议转录</span>
          <span className="pane-count-badge">{filtered.length} 个可读块</span>
        </div>
        <div className="pane-actions-group">
          <span className={`transcript-follow-state ${isFollowing ? "is-following" : "is-paused"}`}>
            {isFollowing ? "跟随最新" : "已暂停跟随"}
          </span>
          {starredIds.size > 0 && (
            <button
              type="button"
              className={`pane-header-btn ${filterStarredOnly ? "primary" : ""}`}
              onClick={() => setFilterStarredOnly((current) => !current)}
              title={filterStarredOnly ? "查看全部转录" : `仅查看重点 (${starredIds.size})`}
            >
              <span aria-hidden="true">⭐</span>
              <span>{filterStarredOnly ? "查看全部" : `仅看重点 (${starredIds.size})`}</span>
            </button>
          )}
        </div>
      </div>

      {speakerStats.length > 0 && (
        <div className="speaker-distribution-container" aria-label="说话人分布">
          <div className="speaker-distribution-bar" aria-hidden="true">
            {speakerStats.map((speaker) => (
              <span
                key={speaker.key}
                className="distribution-segment"
                style={{ width: `${speaker.percent}%`, backgroundColor: speaker.color }}
              />
            ))}
          </div>
          <div className="speaker-chips-row">
            {speakerStats.map((speaker) => {
              const selected = selectedSpeaker === speaker.key;
              return (
                <button
                  key={speaker.key}
                  type="button"
                  className={`speaker-stat-chip ${selected ? "selected" : ""}`}
                  onClick={() => setSelectedSpeaker(selected ? "all" : speaker.key)}
                >
                  <span className="chip-dot" style={{ backgroundColor: speaker.color }} aria-hidden="true" />
                  <span className="chip-name">{speaker.name}</span>
                  <span className="chip-percent">{speaker.count} 块</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="pane-filter-bar">
        <div className="search-input-wrapper">
          <span className="search-icon" aria-hidden="true">🔍</span>
          <input
            className="search-input"
            placeholder="搜索转录内容或说话人..."
            aria-label="搜索转录内容或说话人"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {search && (
            <button
              type="button"
              className="search-clear-btn"
              onClick={() => setSearch("")}
              title="清空搜索"
            >
              ✕
            </button>
          )}
        </div>
        <select
          className="speaker-select"
          aria-label="按说话人筛选"
          value={selectedSpeaker}
          onChange={(event) => setSelectedSpeaker(event.target.value)}
        >
          <option value="all">全部说话人 ({speakerStats.length})</option>
          {speakerStats.map((speaker) => (
            <option key={speaker.key} value={speaker.key}>
              {speaker.name} ({speaker.count} 块)
            </option>
          ))}
        </select>
      </div>

      <div
        ref={logRef}
        className="segment-list transcript-log"
        role="log"
        aria-live="polite"
        aria-label="完整会议转录"
        onScroll={handleScroll}
      >
        {hasNewContent && (
          <button type="button" className="transcript-new-content" onClick={scrollToBottom}>
            有新内容 · 回到底部
          </button>
        )}
        <div className="transcript-live-status" role="status" aria-live="polite">
          {degraded
            ? "分人不可用，正文仍可继续阅读"
            : isFollowing
              ? "正在显示最新内容"
              : "已暂停自动跟随"}
        </div>
        {filtered.length === 0 && (
          <div className="history-empty">
            <span aria-hidden="true" style={{ fontSize: "1.5rem", marginBottom: "6px" }}>🔍</span>
            <span>{filterStarredOnly ? "暂无标记为重点的转录" : "未匹配到相关转录内容"}</span>
          </div>
        )}
        {filtered.map((block, index) => {
          const status = normalizeSpeakerStatus(block.speaker_status);
          const label = speakerLabel(block);
          const isStarred = starredIds.has(block.starId) || starredIds.has(block.block_id);
          const highlighted =
            highlightedSegmentId === block.block_id || block.item_ids.includes(highlightedSegmentId || "");
          const canRename = Boolean(block.speaker_key) && (status === "identified" || status === "anonymous");
          return (
            <article
              key={block.block_id}
              id={`segment-${block.block_id}`}
              className={`segment-card reading-block-card ${highlighted ? "highlighted" : ""} ${isStarred ? "is-starred" : ""}`}
              data-block-id={block.block_id}
              data-item-id={block.item_ids[0] || undefined}
              style={{
                borderLeftColor: isStarred
                  ? "var(--color-yellow)"
                  : SPEAKER_COLORS[index % SPEAKER_COLORS.length],
              }}
            >
              <div className="segment-top">
                <div className="transcript-speaker-gutter">
                  {canRename ? (
                    <button
                      type="button"
                      className="speaker-tag-btn"
                      onClick={() => onRenameSpeaker(block.speaker_key!, label)}
                      title="点击修改说话人名称"
                    >
                      <span className="speaker-avatar-circle" aria-hidden="true">👤</span>
                      <span className="speaker-name-text">{label}</span>
                      <span className="speaker-edit-badge" aria-hidden="true">✎</span>
                    </button>
                  ) : (
                    <span className="speaker-tag-btn speaker-tag-static">
                      <span className="speaker-avatar-circle" aria-hidden="true">◌</span>
                      <span className="speaker-name-text">{label}</span>
                    </span>
                  )}
                  <span
                    className={`speaker-status-badge is-${status}`}
                    title={`说话人状态：${SPEAKER_STATUS_LABELS[status]}`}
                  >
                    {SPEAKER_STATUS_LABELS[status]}
                  </span>
                </div>
                <div className="segment-actions-group">
                  {isStarred && <span className="segment-starred-badge">⭐ 重点</span>}
                  <span className={`segment-time ${block.timing_quality === "unavailable" ? "time-unavailable" : ""}`}>
                    {formatBlockTime(block)}
                  </span>
                  <button
                    type="button"
                    className={`segment-star-btn ${isStarred ? "active" : ""}`}
                    title={isStarred ? "取消重点标记" : "标记此块为重点"}
                    aria-label={isStarred ? "取消重点标记" : "标记此块为重点"}
                    aria-pressed={isStarred}
                    onClick={() => toggleStar(block.starId)}
                  >
                    <span aria-hidden="true">{isStarred ? "⭐" : "✩"}</span>
                  </button>
                  <button
                    type="button"
                    className="segment-copy-btn"
                    title="复制此块内容"
                    aria-label="复制此块内容"
                    onClick={() => void copyBlock(block.text)}
                  >
                    📋
                  </button>
                </div>
              </div>
              <p className="segment-text reading-block-text">{highlightMatch(block.text, search)}</p>
            </article>
          );
        })}
      </div>
    </section>
  );
}
