import { useState, useEffect, useRef } from "react";

export interface VoiceDeleteModalProps {
  readonly isOpen: boolean;
  readonly voiceName: string;
  readonly isCurrentActive?: boolean;
  readonly onClose: () => void;
  readonly onConfirm: () => Promise<void> | void;
}

export function VoiceDeleteModal({ isOpen, voiceName, isCurrentActive = false, onClose, onConfirm }: VoiceDeleteModalProps) {
  const [isDeleting, setIsDeleting] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const mounted = useRef(true);
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose); closeRef.current = onClose;
  useEffect(() => {
    mounted.current = true;
    if (!isOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancel.current?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault(); event.stopImmediatePropagation();
        if (!inFlight.current) closeRef.current();
      } else if (event.key === "Tab") {
        const buttons = Array.from(dialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (!first || !last) { event.preventDefault(); dialog.current?.focus(); }
        else if (!dialog.current?.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
          event.preventDefault(); (event.shiftKey ? last : first).focus();
        } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        // Only the top dialog owns keyboard focus.
        event.stopImmediatePropagation();
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => {
      mounted.current = false;
      window.removeEventListener("keydown", keydown, true);
      if (previous?.isConnected) previous.focus();
    };
  }, [isOpen]);
  if (!isOpen) return null;

  async function handleDelete() {
    if (inFlight.current) return;
    inFlight.current = true; setIsDeleting(true); setError("");
    try {
      await onConfirm();
      if (mounted.current) closeRef.current();
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : "删除未完成，请检查连接后重试。音色不会从列表提前移除。");
    } finally {
      inFlight.current = false;
      if (mounted.current) setIsDeleting(false);
    }
  }
  return <div className="modal-backdrop voice-delete-modal-backdrop" onClick={(event) => {
    event.stopPropagation(); if (!inFlight.current) onClose();
  }}>
    <div ref={dialog} tabIndex={-1} className="modal-dialog voice-delete-modal-dialog" role="dialog" aria-modal="true"
      aria-labelledby="voice-delete-modal-title" aria-describedby="voice-delete-modal-desc" onClick={(event) => event.stopPropagation()}>
      <div className="modal-header">
        <h3 id="voice-delete-modal-title" className="modal-title" style={{ color: "var(--color-red)" }}>删除自定义音色</h3>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={isDeleting} aria-label="关闭弹窗">✕</button>
      </div>
      <p id="voice-delete-modal-desc" style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.5, margin: "14px 0" }}>
        确定要永久删除音色资产 <strong style={{ color: "var(--text-primary)" }}>“{voiceName}”</strong> 吗？
        {isCurrentActive ? <span style={{ display: "block", marginTop: "6px", color: "var(--color-yellow)" }}>
          该音色当前正在使用中。先确认切回官方默认音色，再执行删除；切换失败不会删除。删除失败时默认音色可能已生效。
        </span> : " 删除后参考音频与音色配置将无法恢复，不会删除共享的语音模型。"}
      </p>
      {error && <p className="forge-error-banner" role="alert">{error}</p>}
      <div className="modal-actions">
        <button ref={cancel} type="button" className="btn-secondary" onClick={onClose} disabled={isDeleting}>取消</button>
        <button type="button" className="btn-danger" onClick={() => void handleDelete()} disabled={isDeleting}>
          {isDeleting ? "删除中..." : "确认删除"}
        </button>
      </div>
    </div>
  </div>;
}
