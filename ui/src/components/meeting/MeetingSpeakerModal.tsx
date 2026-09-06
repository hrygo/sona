import { useState, useEffect, useId, useMemo } from "react";
import { XIcon, CheckIcon, UserIcon } from "../Icons";

interface MeetingSpeakerModalProps {
  isOpen: boolean;
  speakerKey: string;
  currentDisplayName: string;
  speakerColor?: string;
  onClose: () => void;
  onSave: (speakerKey: string, newDisplayName: string) => Promise<void>;
}

/**
 * 将技术层 speakerKey（如 "speechrail:spk-e2e-1:speaker-source:s1:spk_01" 或 "spk_1"）
 * 转化为用户友好的说话人辅助代号（如 "说话人 1"、"发言人 01" 等）
 */
export function formatFriendlySpeakerFallback(speakerKey: string): string {
  if (!speakerKey) return "未知发言人";
  const raw = speakerKey.trim();
  if (raw.toLowerCase() === "unknown") return "待识别发言人";

  // 提取末尾的数字或代号，如 spk_01 -> 1, s1 -> 1
  const match = raw.match(/(?:spk_|speaker_|s)?0*([1-9][0-9]*)$/i);
  if (match && match[1]) {
    return `说话人 ${match[1]}`;
  }

  // 若带冒号，提取最后一段语义片段
  const parts = raw.split(":");
  const lastPart = parts[parts.length - 1]?.trim();
  if (lastPart && lastPart !== raw) {
    const subMatch = lastPart.match(/(?:spk_|speaker_|s)?0*([1-9][0-9]*)$/i);
    if (subMatch && subMatch[1]) {
      return `说话人 ${subMatch[1]}`;
    }
  }

  return "参会发言人";
}

const PRESET_ROLES = [
  { label: "主持人", icon: "🎤" },
  { label: "汇报人", icon: "📊" },
  { label: "技术负责", icon: "💻" },
  { label: "产品经理", icon: "🎨" },
  { label: "决策人", icon: "💼" },
  { label: "记录员", icon: "📝" },
] as const;

export function MeetingSpeakerModal({
  isOpen,
  speakerKey,
  currentDisplayName,
  speakerColor = "var(--mod-meeting-accent)",
  onClose,
  onSave,
}: MeetingSpeakerModalProps) {
  const [name, setName] = useState(currentDisplayName);
  const [isSaving, setIsSaving] = useState(false);
  const [showTechnicalId, setShowTechnicalId] = useState(false);
  const inputId = useId();

  useEffect(() => {
    setName(currentDisplayName);
    setShowTechnicalId(false);
  }, [currentDisplayName, isOpen]);

  const friendlyFallback = useMemo(() => {
    return formatFriendlySpeakerFallback(speakerKey);
  }, [speakerKey]);

  if (!isOpen) return null;

  const handleSave = async () => {
    const trimmed = name.trim();
    if (!trimmed || isSaving) return;
    setIsSaving(true);
    try {
      await onSave(speakerKey, trimmed);
      onClose();
    } finally {
      setIsSaving(false);
    }
  };

  const isCurrentDefault = name.trim() === friendlyFallback;

  return (
    <div
      className="modal-backdrop modal-speaker-backdrop"
      onClick={() => {
        if (!isSaving) onClose();
      }}
    >
      <div
        className="modal-dialog modal-speaker-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="speaker-modal-title"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 顶部标题区 */}
        <div className="speaker-modal-header">
          <div className="speaker-modal-header-lead">
            <div
              className="speaker-modal-avatar-preview"
              style={{
                borderColor: speakerColor,
                backgroundColor: `color-mix(in srgb, ${speakerColor} 18%, transparent)`,
                color: speakerColor,
              }}
              aria-hidden="true"
            >
              <UserIcon size={20} />
            </div>
            <div className="speaker-modal-heading">
              <span className="speaker-modal-eyebrow">会议分人管理</span>
              <h3 id="speaker-modal-title" className="speaker-modal-title">
                设置说话人称谓
              </h3>
            </div>
          </div>
          <button
            type="button"
            className="speaker-modal-close-btn"
            aria-label="关闭"
            onClick={onClose}
            disabled={isSaving}
          >
            <XIcon size={16} />
          </button>
        </div>

        {/* 身份提示摘要卡片 */}
        <div className="speaker-modal-context-card">
          <div className="speaker-modal-context-item">
            <span className="speaker-modal-context-label">当前对象</span>
            <div className="speaker-modal-context-val">
              <span
                className="speaker-modal-swatch-dot"
                style={{ backgroundColor: speakerColor }}
                aria-hidden="true"
              />
              <strong className="speaker-modal-current-name">
                {currentDisplayName || friendlyFallback}
              </strong>
              {currentDisplayName && currentDisplayName !== friendlyFallback && (
                <span className="speaker-modal-origin-hint">({friendlyFallback})</span>
              )}
            </div>
          </div>
          <div className="speaker-modal-context-note">
            修改后将原位更新全场会议该说话人的转录与纪要显示
          </div>
        </div>

        {/* 表单输入区 */}
        <div className="speaker-modal-form-group">
          <div className="speaker-modal-label-row">
            <label className="form-label" htmlFor={inputId}>
              真实姓名或角色
            </label>
            <span className="speaker-modal-char-count">{name.length}/50</span>
          </div>

          <div className="speaker-modal-input-wrap">
            <input
              id={inputId}
              className="form-input speaker-modal-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：张工、李总、主持人"
              autoFocus
              maxLength={50}
              disabled={isSaving}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleSave();
                if (e.key === "Escape" && !isSaving) onClose();
              }}
            />
            {name.length > 0 && !isSaving && (
              <button
                type="button"
                className="speaker-modal-input-clear"
                onClick={() => setName("")}
                title="清空输入"
                aria-label="清空输入"
              >
                <XIcon size={13} />
              </button>
            )}
          </div>

          {/* 快捷预设 */}
          <div className="speaker-presets-section">
            <span className="speaker-preset-label">快捷身份:</span>
            <div className="speaker-presets-list" role="group" aria-label="快捷身份选择">
              {PRESET_ROLES.map(({ label, icon }) => {
                const isSelected = name.trim() === label;
                return (
                  <button
                    key={label}
                    type="button"
                    className={`speaker-preset-btn ${isSelected ? "active" : ""}`}
                    onClick={() => setName(label)}
                    disabled={isSaving}
                  >
                    <span className="speaker-preset-icon">{icon}</span>
                    <span>{label}</span>
                    {isSelected && <CheckIcon size={12} className="speaker-preset-check" />}
                  </button>
                );
              })}
              {!isCurrentDefault && (
                <button
                  type="button"
                  className="speaker-preset-btn speaker-preset-reset-btn"
                  onClick={() => setName(friendlyFallback)}
                  disabled={isSaving}
                  title={`恢复系统默认代号: ${friendlyFallback}`}
                >
                  <span>恢复默认</span>
                </button>
              )}
            </div>
          </div>
        </div>

        {/* 底层通道 ID 友好折叠展示（用户看不到乱七八糟的 id，需要时可点开） */}
        <div className="speaker-modal-tech-id-wrapper">
          <button
            type="button"
            className="speaker-modal-tech-id-toggle"
            onClick={() => setShowTechnicalId(!showTechnicalId)}
            aria-expanded={showTechnicalId}
          >
            <span>{showTechnicalId ? "收起通道标识" : "查看技术通道 ID"}</span>
            <span className="speaker-modal-tech-arrow">{showTechnicalId ? "▲" : "▼"}</span>
          </button>
          {showTechnicalId && (
            <div className="speaker-modal-tech-id-box">
              <code className="speaker-modal-tech-code">{speakerKey}</code>
            </div>
          )}
        </div>

        {/* 底部操作栏 */}
        <div className="speaker-modal-actions">
          <button
            type="button"
            className="btn-secondary speaker-modal-btn-cancel"
            onClick={onClose}
            disabled={isSaving}
          >
            <span>取消</span>
            <kbd className="speaker-modal-kbd">Esc</kbd>
          </button>
          <button
            type="button"
            className="speaker-modal-btn-save"
            onClick={() => void handleSave()}
            disabled={isSaving || !name.trim()}
          >
            {isSaving ? (
              <>
                <span className="speaker-modal-spinner" aria-hidden="true" />
                <span>保存中…</span>
              </>
            ) : (
              <>
                <CheckIcon size={14} />
                <span>确认修改</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

