import React, { useEffect, useState } from "react";
import { useInnerOSStore } from "./innerOSStore";
import { InnerOSAnswerContent } from "./InnerOSAnswerContent";
import "./InnerOSArchive.css";
import {
  CheckIcon,
  ChevronRightIcon,
  DownloadIcon,
  MaskIcon,
  TrashIcon,
} from "../../components/Icons";
import type { InnerOSExchange, InnerOSIntent } from "./contracts";

interface Props {
  readonly meetingId: string;
  readonly onSelectEvidence?: (segmentId: string) => void;
}

const INTENT_LABELS: Record<InnerOSIntent, string> = {
  fact: "事实核查",
  analysis: "局势研判",
  draft: "回应草稿",
  mixed: "综合研判",
};

export const InnerOSHistoryTab: React.FC<Props> = ({
  meetingId,
  onSelectEvidence,
}) => {
  const historyList = useInnerOSStore((s) => s.historyList);
  const isLoading = useInnerOSStore((s) => s.isLoadingHistory);
  const fetchHistory = useInnerOSStore((s) => s.fetchHistory);
  const deleteExchangeAction = useInnerOSStore((s) => s.deleteExchangeAction);
  const exportNotesAsMarkdown = useInnerOSStore((s) => s.exportNotesAsMarkdown);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [confirmDeleteExchange, setConfirmDeleteExchange] = useState<InnerOSExchange | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [copiedAll, setCopiedAll] = useState(false);

  useEffect(() => {
    if (meetingId) {
      fetchHistory(meetingId);
    }
  }, [meetingId, fetchHistory]);

  const executeDelete = async (exchangeId: string) => {
    setDeletingId(exchangeId);
    try {
      await deleteExchangeAction(meetingId, exchangeId);
      setConfirmDeleteExchange(null);
    } finally {
      setDeletingId(null);
    }
  };

  const handleExportAll = async () => {
    const md = exportNotesAsMarkdown();
    try {
      await navigator.clipboard.writeText(md);
      setCopiedAll(true);
      setTimeout(() => setCopiedAll(false), 2000);
    } catch {
      // ignore
    }
  };

  if (isLoading && historyList.length === 0) {
    return (
      <div className="inner-os-history-loading">
        <span className="inner-os-spinner" />
        <span>正在加载内心 OS 档案...</span>
      </div>
    );
  }

  if (historyList.length === 0) {
    return (
      <div className="inner-os-history-empty">
        <span className="inner-os-empty-icon"><MaskIcon size={24} /></span>
        <div className="inner-os-empty-title">暂无保存的内心 OS 记录</div>
        <div className="inner-os-empty-desc">
          在会议进行期间向内心 OS 提问并点击“保存此条问答”，即可在此归档并永久回溯。
        </div>
      </div>
    );
  }

  return (
    <div className="inner-os-history-tab" data-testid="inner-os-history-tab">
      <div className="inner-os-history-meta-bar">
        <span>共有 {historyList.length} 条已保存的问答档案</span>
        <button
          type="button"
          className="inner-os-history-export-btn"
          onClick={handleExportAll}
          title="复制所有已保存问答为 Markdown"
        >
          {copiedAll ? <><CheckIcon size={12} /> 已复制档案</> : <><DownloadIcon size={12} /> 导出档案 (MD)</>}
        </button>
      </div>

      <div className="inner-os-history-list">
        {historyList.map((exchange) => {
          const isExpanded = expandedId === exchange.id;
          return (
            <div key={exchange.id} className="inner-os-history-item-wrap">
              <div className="inner-os-history-item-header">
                <div className="inner-os-history-status-flags">
                  {exchange.evidence_invalidated ? (
                    <span
                      className="inner-os-status-pill pill-danger"
                      title="原证据段已变更或修正"
                      aria-label="原证据段已变更或修正"
                    >
                      <span className="inner-os-status-dot" aria-hidden="true" /> <span aria-hidden="true">证据变更</span>
                    </span>
                  ) : exchange.context_advanced ? (
                    <span
                      className="inner-os-status-pill pill-warning"
                      title="会议后续有新讨论"
                      aria-label="会议后续有新讨论"
                    >
                      <span className="inner-os-status-dot" aria-hidden="true" /> <span aria-hidden="true">有后续</span>
                    </span>
                  ) : (
                    <span
                      className="inner-os-status-pill pill-success"
                      title="证据有效"
                      aria-label="证据有效"
                    >
                      <span className="inner-os-status-dot" aria-hidden="true" /> <span aria-hidden="true">证据有效</span>
                    </span>
                  )}
                  <span className="inner-os-history-time">
                    {new Date(exchange.created_at).toLocaleTimeString()}
                  </span>
                </div>
                <button
                  type="button"
                  className="inner-os-history-del-btn"
                  onClick={() => setConfirmDeleteExchange(exchange)}
                  disabled={deletingId === exchange.id}
                  title="从档案中删除"
                >
                  {deletingId === exchange.id ? "删除中..." : <><TrashIcon size={13} /> 删除</>}
                </button>
              </div>

              <button
                type="button"
                className="inner-os-history-item-toggle"
                onClick={() => setExpandedId(isExpanded ? null : exchange.id)}
                aria-expanded={isExpanded}
                aria-controls={`inner-os-history-detail-${exchange.id}`}
              >
                <span className="inner-os-history-question-wrap">
                  <span className="inner-os-q-tag">Q</span>
                  <span className="inner-os-history-question">{exchange.question}</span>
                </span>
                <span className="inner-os-history-item-meta">
                  <span className="inner-os-intent-badge">
                    {INTENT_LABELS[exchange.intent] || exchange.intent}
                  </span>
                  <span className="inner-os-history-expand-label">
                    {isExpanded ? "收起" : "查看回答"}
                    <ChevronRightIcon className={isExpanded ? "is-expanded" : ""} size={13} />
                  </span>
                </span>
              </button>

              {isExpanded && (
                <div
                  id={`inner-os-history-detail-${exchange.id}`}
                  className="inner-os-history-item-detail"
                >
                  <InnerOSAnswerContent
                    answer={exchange.answer}
                    onSelectEvidence={onSelectEvidence}
                    compact
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* 内心 OS 记录删除确认弹窗 */}
      {confirmDeleteExchange && (
        <div
          className="modal-backdrop"
          onClick={() => {
            if (!deletingId) setConfirmDeleteExchange(null);
          }}
        >
          <div
            className="modal-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="inneros-delete-title"
            aria-describedby="inneros-delete-desc"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <h3 id="inneros-delete-title" className="modal-title" style={{ color: "var(--color-red)" }}>
                删除内心 OS 记录
              </h3>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmDeleteExchange(null)}
                disabled={Boolean(deletingId)}
                style={{ padding: "2px 8px" }}
                aria-label="关闭弹窗"
              >
                ✕
              </button>
            </div>

            <p id="inneros-delete-desc" style={{ fontSize: "0.85rem", color: "var(--text-secondary)", lineHeight: 1.5, margin: "14px 0" }}>
              确定要从会议档案中永久删除此条内心 OS 记录吗？
              <strong style={{ display: "block", marginTop: "6px", color: "var(--text-primary)" }}>
                “{confirmDeleteExchange.question}”
              </strong>
            </p>

            <div className="modal-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setConfirmDeleteExchange(null)}
                disabled={Boolean(deletingId)}
              >
                取消
              </button>
              <button
                type="button"
                className="btn-danger"
                onClick={() => void executeDelete(confirmDeleteExchange.id)}
                disabled={Boolean(deletingId)}
              >
                {deletingId ? "删除中..." : "确认删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
