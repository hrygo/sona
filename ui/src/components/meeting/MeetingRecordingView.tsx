import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import type { DisplayBlock, SpeakerStatus, TranscriptSegment } from "../../contracts/meetingContract";
import { formatTimeRange, MeetingGapAlert } from "./MeetingGapAlert";
import type { TranscriptionGap } from "../../stores/meetingStore";
import { showToast } from "../Toast";
import { MeetingWaveform } from "./MeetingWaveform";
import { deriveReadingBlocks } from "./transcriptViewModel";
import { copyTextToClipboard } from "../../utils/clipboard";
import { InnerOSPanel, useInnerOSStore } from "../../features/innerOS";
import { useUISettingsStore } from "../../stores/uiSettingsStore";
import {
  ClockIcon,
  CopyIcon,
  EditIcon,
  FileTextIcon,
  MaskIcon,
  SparklesIcon,
  SpeakerIcon,
  StopCircleIcon,
  UserIcon,
} from "../Icons";

export const SPEAKER_COLORS = [
  "#6366f1", // indigo
  "#10b981", // emerald
  "#f59e0b", // amber
  "#ec4899", // pink
  "#06b6d4", // cyan
  "#8b5cf6", // purple
];

export function formatElapsed(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const RECOGNIZED_SPEAKER_KEY = /^(?:spk_|s)?0*[1-9][0-9]*$/;

/** 与后端说话人标签口径一致：0 和非数字匿名 key 仍属于待识别分组。 */
export function isRecognizedSpeakerKey(speakerKey: string): boolean {
  const rawSpeaker = speakerKey.trim().split(":").pop() ?? "";
  return RECOGNIZED_SPEAKER_KEY.test(rawSpeaker);
}

type LiveReadingBlock = {
  readonly block_id: string;
  readonly segment_ids: readonly string[];
  readonly speaker_key: string;
  readonly speaker_name: string;
  readonly speaker_status: SpeakerStatus;
  readonly start_ms: number | null;
  readonly end_ms: number | null;
  readonly text: string;
  readonly isStarred: boolean;
};

function liveSpeakerLabel(block: DisplayBlock): string {
  if (block.speaker_name) return block.speaker_name;
  switch (block.speaker_status) {
    case "identified":
      return "已识别说话人";
    case "anonymous":
      return "匿名说话人";
    case "off":
      return "分人未启用";
    case "degraded":
      return "分人不可用";
    default:
      return "正在确认";
  }
}

function liveTimeLabel(startMs: number | null, endMs: number | null): string {
  return startMs === null || endMs === null
    ? "时间不可用"
    : formatTimeRange(startMs, endMs);
}

export interface MeetingRecordingViewProps {
  startedAt: string | null;
  segments: readonly TranscriptSegment[];
  displayBlocks?: readonly DisplayBlock[];
  partialText: string | null;
  partialSpeaker: string | null;
  gaps: readonly TranscriptionGap[];
  micMuted: boolean;
  onToggleMic: () => void;
  onEndMeeting: () => Promise<void>;
  onRenameSpeaker: (speakerKey: string, currentName: string) => void;
  isEnding: boolean;
  isCalibrating?: boolean;
  starredIds?: ReadonlySet<string>;
  onToggleStarSegment?: (segmentId: string) => void;
}

export function MeetingRecordingView({
  startedAt,
  segments,
  displayBlocks,
  partialText,
  partialSpeaker,
  gaps,
  micMuted,
  onToggleMic,
  onEndMeeting,
  onRenameSpeaker,
  isEnding,
  isCalibrating = false,
  starredIds: propStarredIds,
  onToggleStarSegment: propToggleStarSegment,
}: MeetingRecordingViewProps) {
  const [elapsed, setElapsed] = useState(0);
  const [localStarredIds, setLocalStarredIds] = useState<Set<string>>(() => new Set());
  const starredIds = propStarredIds ?? localStarredIds;
  const [filterStarredOnly, setFilterStarredOnly] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoScroll, setAutoScroll] = useState(true);

  // Inner OS state
  const isInnerOSOpen = useInnerOSStore((s) => s.isPanelOpen);
  const toggleInnerOS = useInnerOSStore((s) => s.togglePanel);
  const queryStatus = useInnerOSStore((s) => s.queryStatus);
  const isGenerating = queryStatus === "generating" || queryStatus === "accepted";
  // 服务端能力位：InnerOS 未启用时隐藏入口并拦截快捷键
  const innerOSEnabled = useUISettingsStore((s) => s.innerOSEnabled);

  // Timer
  useEffect(() => {
    if (!startedAt) return;
    const startMs = Date.parse(startedAt);
    const update = () => {
      const diffSec = Math.max(0, Math.floor((Date.now() - startMs) / 1000));
      setElapsed(diffSec);
    };
    update();
    const interval = setInterval(update, 1000);
    return () => clearInterval(interval);
  }, [startedAt]);

  // Unique speaker color map
  const speakerColorMap = useMemo(() => {
    const map = new Map<string, string>();
    let colorIdx = 0;
    for (const seg of segments) {
      if (!map.has(seg.speaker_key)) {
        map.set(seg.speaker_key, SPEAKER_COLORS[colorIdx % SPEAKER_COLORS.length]!);
        colorIdx++;
      }
    }
    return map;
  }, [segments]);

  const speakerGroupCounts = useMemo(() => {
    const recognized = new Set<string>();
    const unrecognized = new Set<string>();
    for (const segment of segments) {
      const target = isRecognizedSpeakerKey(segment.speaker_key) ? recognized : unrecognized;
      target.add(segment.speaker_key);
    }
    return {
      recognized: recognized.size,
      unrecognized: unrecognized.size,
    };
  }, [segments]);

  const toggleStarSegment = useCallback((segmentId: string) => {
    if (propToggleStarSegment) {
      propToggleStarSegment(segmentId);
    } else {
      setLocalStarredIds((prev) => {
        const next = new Set(prev);
        if (next.has(segmentId)) {
          next.delete(segmentId);
        } else {
          next.add(segmentId);
        }
        return next;
      });
    }
  }, [propToggleStarSegment]);

  const handleStarSelectedOrLatest = useCallback(() => {
    if (segments.length > 0) {
      const latest = segments[segments.length - 1];
      toggleStarSegment(latest.id);
      const isNowStarred = !starredIds.has(latest.id);
      showToast(isNowStarred ? "已将最新发言标记为重点 ⭐" : "已取消最新发言重点标记");
    }
  }, [segments, starredIds, toggleStarSegment]);

  // Keyboard shortcuts (Meta+K for InnerOS works everywhere, S/M only when not in input)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isCmdOrCtrl = e.metaKey || e.ctrlKey;
      const isK = e.key.toLowerCase() === "k" || e.code === "KeyK";

      // 1. Meta shortcut ⌘+K: Always toggles Inner OS, even if focus is inside an input/textarea!
      if (isCmdOrCtrl && !e.altKey && !e.shiftKey && isK) {
        e.preventDefault();
        if (innerOSEnabled) {
          toggleInnerOS();
        } else {
          showToast("内心 OS 未启用：设置 SONA_MEETING_INNER_OS_ENABLED=true 并重启 sona-ui", "warning");
        }
        return;
      }

      // 2. Single-character shortcuts (S, M) are only active when not typing in inputs
      const target = e.target as HTMLElement | null;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable)) {
        return;
      }

      if (!isCmdOrCtrl && !e.altKey && !e.shiftKey) {
        if (e.key === "s" || e.key === "S" || e.code === "KeyS") {
          e.preventDefault();
          handleStarSelectedOrLatest();
        } else if (e.key === "m" || e.key === "M" || e.code === "KeyM") {
          e.preventDefault();
          onToggleMic();
          showToast(micMuted ? "麦克风已解除静音 🎙️" : "麦克风已静音 🔇");
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleStarSelectedOrLatest, innerOSEnabled, onToggleMic, micMuted, toggleInnerOS]);

  // Auto-scroll when new segments arrive
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [segments, partialText, autoScroll]);

  const handleScroll = () => {
    if (!scrollRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
    const isNearBottom = scrollHeight - scrollTop - clientHeight < 60;
    setAutoScroll(isNearBottom);
  };

  const handleCopyText = async (text: string) => {
    await copyTextToClipboard(text);
    showToast("已复制发言内容到剪贴板 📋");
  };

  const displayedSegments = useMemo(() => {
    if (!filterStarredOnly) return segments;
    return segments.filter((s) => starredIds.has(s.id));
  }, [segments, starredIds, filterStarredOnly]);

  const readingBlocks = useMemo(() => {
    if (displayBlocks && displayBlocks.length > 0) {
      return displayBlocks
        .filter((block) => !block.is_partial)
        .map<LiveReadingBlock>((block) => ({
          block_id: block.block_id,
          segment_ids: block.item_ids,
          speaker_key: block.speaker_key || "",
          speaker_name: liveSpeakerLabel(block),
          speaker_status: block.speaker_status,
          start_ms: block.start_ms,
          end_ms: block.end_ms,
          text: block.text,
          isStarred: block.item_ids.some((id) => starredIds.has(id)) || starredIds.has(block.block_id),
        }));
    }
    return deriveReadingBlocks(displayedSegments, starredIds);
  }, [displayBlocks, displayedSegments, starredIds]);

  // Evidence smooth anchor and 3s annealing animation
  const handleSelectEvidence = useCallback((segmentId: string) => {
    const el = document.getElementById(`segment-${segmentId}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.remove("is-evidence-target-inneros");
      // Force reflow for re-triggering keyframe
      void el.offsetWidth;
      el.classList.add("is-evidence-target-inneros");
      setTimeout(() => {
        el.classList.remove("is-evidence-target-inneros");
      }, 3000);
    }
  }, []);

  return (
    <div className="recording-view">
      <MeetingGapAlert gaps={gaps} />

      {/* Accessible Polite Status Live Region */}
      <div className="sr-only" role="status" aria-live="polite">
        {isCalibrating
          ? "正在校准转录基线"
          : isEnding
            ? "正在冲刷并封存会议转录"
            : micMuted
              ? "麦克风已静音"
              : "会议录制中，麦克风正常监听"}
      </div>

      {/* Recording Toolbar: compact status metrics · views · actions */}
      <div className="recording-toolbar">
        {/* Cluster 1 (Left): 状态与声学 */}
        <div className="toolbar-cluster cluster-status">
          <div className="recording-timer" title="当前会议录制时长">
            <span className="recording-dot" />
            <span>{formatElapsed(elapsed)}</span>
          </div>

          <div
            className="recording-vu-meter"
            title={micMuted ? "麦克风已静音 (快捷键 M)" : "麦克风音频采集中 (快捷键 M)"}
          >
            <span
              className={`rec-vu-bar ${micMuted ? "muted" : "active"}`}
              style={{ height: micMuted ? "3px" : "12px" }}
            />
            <span
              className={`rec-vu-bar ${micMuted ? "muted" : "active"}`}
              style={{ height: micMuted ? "3px" : "16px" }}
            />
            <span
              className={`rec-vu-bar ${micMuted ? "muted" : "active"}`}
              style={{ height: micMuted ? "3px" : "10px" }}
            />
            <span
              className={`rec-vu-bar ${micMuted ? "muted" : "active"}`}
              style={{ height: micMuted ? "3px" : "14px" }}
            />
          </div>

          <div className="recording-metric" title="已确认的发言片段">
            <FileTextIcon size={14} aria-hidden="true" />
            <strong>{segments.length}</strong>
            <span className="recording-metric-label">片段</span>
          </div>
          <div
            className="recording-metric"
            title="已识别的说话分组数；可能合并或拆分，不等于真实参会人数"
          >
            <SpeakerIcon size={14} aria-hidden="true" />
            <strong>{speakerGroupCounts.recognized}</strong>
            <span className="recording-metric-label">识别分组</span>
          </div>
          {isCalibrating && <span className="recording-compact-state is-calibrating">校准中</span>}
          {speakerGroupCounts.unrecognized > 0 && (
            <span
              className="recording-compact-state is-diarization-hint"
              title={speakerGroupCounts.unrecognized > 0
                ? "存在未识别说话人分组；当前计数只统计已识别分组，可能合并或拆分，不等于真实参会人数"
                : "流式转写的说话人分组可能待校准；当前计数只统计已识别分组，不等于真实参会人数"}
            >
              含未识别分组，待识别
            </span>
          )}
          {starredIds.size > 0 && (
            <div className="recording-metric recording-metric-starred" title="重点发言数量">
              <SparklesIcon size={14} aria-hidden="true" />
              <strong>{starredIds.size}</strong>
            </div>
          )}
        </div>

        {/* Cluster 2 (Center): 阅读重点 */}
        <div className="toolbar-cluster cluster-views">
          <button
            type="button"
            className="btn-toolbar-action btn-star-action"
            onClick={handleStarSelectedOrLatest}
            aria-pressed={false}
            title="标记重点发言 (快捷键 S)"
          >
            <SparklesIcon size={13} />
            <span>标重点</span>
            <kbd className="toolbar-kbd">S</kbd>
          </button>

          {starredIds.size > 0 && (
            <button
              type="button"
              className={`btn-toolbar-pill filter-starred-toggle ${filterStarredOnly ? "active" : ""}`}
              onClick={() => setFilterStarredOnly((prev) => !prev)}
              title={filterStarredOnly ? "查看全部转录片段" : "仅查看重点片段"}
            >
              <SparklesIcon size={12} />
              <span>{filterStarredOnly ? "全部" : `重点 (${starredIds.size})`}</span>
            </button>
          )}
        </div>

        {/* Cluster 3 (Right): 结束 & 副驾驶 — 麦克风已由顶部 StatusBar VU 控件统一管控，此处不重复 */}
        <div className="toolbar-cluster cluster-actions">
          <button
            type="button"
            className={`btn-end-meeting ${isEnding ? "is-ending" : ""}`}
            onClick={() => void onEndMeeting()}
            disabled={isEnding}
            title="结束当前会议并冲刷转录生成 AI 纪要"
          >
            {isEnding ? <span className="btn-spinner-sm" /> : <StopCircleIcon size={14} />}
            <span>{isEnding ? "正在冲刷并封存..." : "结束会议"}</span>
          </button>

          <button
            type="button"
            className={`btn-inneros-toggle ${isInnerOSOpen ? "is-active" : ""} ${isGenerating ? "is-generating" : ""}`}
            onClick={toggleInnerOS}
            title="展开/收起内心 OS 私密副驾驶 (⌘+K)"
            aria-pressed={isInnerOSOpen}
          >
            <MaskIcon size={14} />
            <span>内心 OS</span>
            {isGenerating && <span className="btn-inneros-pulse" />}
          </button>
        </div>
      </div>

      {/* Main Workspace Body Split with Inner OS */}
      <div className="recording-workspace-body">
        <div className="recording-transcript-pane">
          {/* 会议录制高保真拾音与声纹分轨拟真波形 */}
          <div className="meeting-waveform-container">
            <MeetingWaveform
              isRecording={true}
              hasPartial={Boolean(partialText)}
              isMuted={micMuted}
              activeTextTrigger={partialText || displayBlocks?.length || segments.length}
            />
          </div>

          <div
            className="live-transcript-container"
            ref={scrollRef}
            onScroll={handleScroll}
            role="log"
            aria-live="polite"
            aria-label="实时会议转录"
          >
            {displayedSegments.length === 0 && !partialText && (
              <div className="history-empty">
                {filterStarredOnly ? (
                  <span><SparklesIcon size={14} /> 暂无标记为重点的发言片段，点击片段右侧星号可随时标记</span>
                ) : (
                  <span><ClockIcon size={14} /> 正在倾听发言... 请保持讲话，实时转录将在此展示</span>
                )}
              </div>
            )}

            {/* One block-level reading surface for confirmed transcript facts. */}
            {readingBlocks.map((block) => {
                const speakerColor = speakerColorMap.get(block.speaker_key) || "var(--color-accent)";
                const status = "speaker_status" in block ? block.speaker_status : "identified";
                const canRename = Boolean(block.speaker_key) && (status === "identified" || status === "anonymous" || status === "stable");
                return (
                  <div
                    key={block.block_id}
                    id={`segment-${block.block_id}`}
                    className={`segment-card reading-block-card ${block.isStarred ? "is-starred" : ""}`}
                    data-item-id={block.segment_ids[0] || undefined}
                    style={{ borderLeftColor: block.isStarred ? "var(--color-yellow)" : speakerColor }}
                  >
                    <div className="segment-top">
                      {canRename ? (
                        <button
                          type="button"
                          className="speaker-tag-btn"
                          title="点击修改此说话人名称"
                          onClick={(e) => {
                            e.stopPropagation();
                            onRenameSpeaker(block.speaker_key, block.speaker_name);
                          }}
                        >
                          <span className="speaker-avatar-circle" style={{ backgroundColor: speakerColor }}>
                            <UserIcon size={11} />
                          </span>
                          <span className="speaker-name-text">{block.speaker_name}</span>
                          <span className="speaker-edit-badge" title="可重命名"><EditIcon size={10} /></span>
                        </button>
                      ) : (
                        <span className="speaker-tag-btn speaker-tag-static">
                          <span className="speaker-avatar-circle" style={{ backgroundColor: speakerColor }}>
                            <UserIcon size={11} />
                          </span>
                          <span className="speaker-name-text">{block.speaker_name}</span>
                        </span>
                      )}
                      <span className={`speaker-status-badge is-${status}`}>
                        {status === "identified" || status === "stable"
                          ? "已识别"
                          : status === "anonymous"
                            ? "匿名说话人"
                            : status === "off"
                              ? "分人未启用"
                              : status === "degraded"
                                ? "分人不可用"
                                : "正在确认"}
                      </span>
                      <div className="segment-actions-group" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        {block.isStarred && (
                          <span className="segment-starred-badge" title="包含重点发言片段">
                            <SparklesIcon size={11} /> 重点
                          </span>
                        )}
                        <span className="segment-time">
                          {liveTimeLabel(block.start_ms, block.end_ms)}
                        </span>
                        <button
                          type="button"
                          className="segment-copy-btn"
                          title="复制此段阅读内容"
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleCopyText(block.text);
                          }}
                        >
                          <CopyIcon size={12} />
                        </button>
                      </div>
                    </div>
                    <p className="segment-text reading-block-text">{block.text}</p>
                  </div>
                );
              })}

            {/* 实时未定稿 ASR 气泡 (Partial transcript bubble) */}
            {partialText && (
              <div className="segment-card partial-card" style={{ borderLeftColor: "var(--color-yellow)" }}>
                <div className="segment-top">
                  <span className="speaker-tag-btn" style={{ cursor: "default" }}>
                    <span className="speaker-avatar-circle" style={{ backgroundColor: "var(--color-yellow)" }}>
                      <ClockIcon size={11} />
                    </span>
                    <span className="speaker-name-text">
                      {partialSpeaker || "正在识别说话人..."}
                    </span>
                  </span>
                  <span className="partial-badge">实时识别中</span>
                </div>
                <p className="segment-text partial-text">{partialText}</p>
              </div>
            )}
          </div>

          {/* Floating Copilot trigger when Inner OS panel is stowed */}
          {!isInnerOSOpen && innerOSEnabled && (
            <button
              type="button"
              className="inner-os-floating-trigger"
              onClick={toggleInnerOS}
              title="唤起内心 OS 私密副驾驶 (⌘K)"
              aria-label="唤起内心 OS 私密副驾驶"
              data-testid="inner-os-floating-trigger"
            >
              <span className="inner-os-floating-icon" aria-hidden="true">
                <SparklesIcon size={14} />
              </span>
              <span>内心 OS</span>
              {isGenerating ? (
                <span className="inner-os-floating-badge is-generating">
                  <span className="inner-os-pulsing-dot" /> 研判中
                </span>
              ) : starredIds.size > 0 ? (
                <span className="inner-os-floating-badge">重点 {starredIds.size}</span>
              ) : null}
              <kbd className="inner-os-floating-kbd">⌘K</kbd>
            </button>
          )}
        </div>

        {/* Right side Inner OS Panel */}
        <InnerOSPanel onSelectEvidence={handleSelectEvidence} />
      </div>
    </div>
  );
}
