import { useState, useEffect } from "react";

export interface VoiceDeleteModalProps {
  readonly isOpen: boolean;
  readonly voiceName: string;
  readonly isCurrentActive?: boolean;
  readonly onClose: () => void;
  readonly onConfirm: () => Promise<void> | void;
}

export function VoiceDeleteModal({
  isOpen,
  voiceName,
  isCurrentActive = false,
  onClose,
  onConfirm,
}: VoiceDeleteModalProps) {
  const [isDeleting, setIsDeleting] = useState(false);

  // 快捷键 Esc 关闭
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isDeleting) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, isDeleting, onClose]);

  if (!isOpen) return null;

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await onConfirm();
      onClose();
    } finally {
      setIsDeleting(false);
    }
  };

  return (
    <div
      className="modal-backdrop voice-delete-modal-backdrop"
      onClick={() => {
        if (!isDeleting) onClose();
      }}
    >
      <div
        className="modal-dialog voice-delete-modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="voice-delete-modal-title"
        aria-describedby="voice-delete-modal-desc"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h3 id="voice-delete-modal-title" className="modal-title" style={{ color: "var(--color-red)" }}>
            删除自定义音色
          </h3>
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            disabled={isDeleting}
            style={{ padding: "2px 8px" }}
            aria-label="关闭弹窗"
          >
            ✕
          </button>
        </div>

        <p
          id="voice-delete-modal-desc"
          style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.5, margin: "14px 0" }}
        >
          确定要永久删除音色资产 <strong style={{ color: "var(--text-primary)" }}>“{voiceName}”</strong> 吗？
          {isCurrentActive ? (
            <span style={{ display: "block", marginTop: "6px", color: "var(--color-yellow)" }}>
              ⚠️ 该音色当前正在使用中，删除后系统将自动切回官方默认原声音色。
            </span>
          ) : (
            " 删除后本地声学模型参数与提示词配置将无法恢复。"
          )}
        </p>

        <div className="modal-actions">
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            disabled={isDeleting}
          >
            取消
          </button>
          <button
            type="button"
            className="btn-danger"
            onClick={() => void handleDelete()}
            disabled={isDeleting}
          >
            {isDeleting ? "删除中..." : "确认删除"}
          </button>
        </div>
      </div>
    </div>
  );
}
