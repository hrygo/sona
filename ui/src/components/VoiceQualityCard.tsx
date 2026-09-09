import type { VoiceQualityReport, VoiceQualityStatus } from "../contracts/voiceContract";
import type { LocalVoiceQualityResult } from "../utils/voiceQuality";
import "./VoiceQualityCard.css";

export interface VoiceQualityCardProps {
  readonly title: string;
  readonly report?: VoiceQualityReport;
  readonly localResult?: LocalVoiceQualityResult;
  readonly pending?: boolean;
  readonly onRetry?: () => void;
  readonly onRerecord?: () => void;
}

const STATUS_META: Record<VoiceQualityStatus, { label: string; icon: string; detail: string }> = {
  unevaluated: { label: "尚未评估", icon: "○", detail: "服务端尚未返回质量报告" },
  pass: { label: "质量良好", icon: "✓", detail: "可以进入试听或激活确认" },
  warn: { label: "存在风险", icon: "!", detail: "可以继续，但不建议直接设为默认音色" },
  reject: { label: "不建议使用", icon: "×", detail: "请按建议重新录音" },
};

const FAILURE_LABELS: Record<string, string> = {
  audio_too_short: "录音时长过短",
  audio_too_long: "录音时长过长",
  speech_not_detected: "没有检测到足够人声",
  low_speech_coverage: "有效语音覆盖不足",
  high_noise_floor: "环境噪声偏高",
  low_snr: "信噪比偏低",
  clipping: "输入电平发生削波",
  leading_silence: "开头静音过长",
  trailing_silence: "结尾静音过长",
  duration_boundary: "录音时长接近边界",
  transcript_mismatch: "朗读文本与提示词不匹配",
  probe_failed: "固定试听测试未完成",
  output_invalid: "生成音频格式异常",
};

function failureLabel(code: string): string {
  return FAILURE_LABELS[code] ?? code;
}

export function VoiceQualityCard({
  title,
  report,
  localResult,
  pending = false,
  onRetry,
  onRerecord,
}: VoiceQualityCardProps) {
  const status = report?.status ?? localResult?.status ?? "unevaluated";
  const meta = STATUS_META[status];
  const failureCodes = report?.failure_codes ?? localResult?.failure_codes ?? [];
  const synthesis = report?.synthesis;

  return (
    <section className={`voice-quality-card status-${status}`} aria-label={title}>
      <div className="voice-quality-card-header">
        <div>
          <p className="voice-quality-card-eyebrow">质量证据</p>
          <h3>{title}</h3>
        </div>
        <span className="voice-quality-status" aria-label={`质量状态：${meta.label}`}>
          <span className="voice-quality-status-icon" aria-hidden="true">{meta.icon}</span>
          {pending ? "检查中" : meta.label}
        </span>
      </div>

      <div className="voice-quality-live" aria-live="polite">
        {pending ? "正在运行质量检查，请稍候。" : report ? meta.detail : "服务端尚未返回质量报告，当前结果不能视为通过。"}
      </div>

      {failureCodes.length > 0 && (
        <ul className="voice-quality-failures">
          {failureCodes.map((code) => <li key={code}>{failureLabel(code)}</li>)}
        </ul>
      )}

      {synthesis && (
        <div className="voice-quality-evidence-grid">
          <span>
            <strong>{synthesis.successful_probe_count ?? 0} / {synthesis.probe_count ?? 0}</strong>
            个测试通过
          </span>
          {synthesis.deterministic === true && <span><strong>✓</strong> 确定性输出</span>}
          {synthesis.peak_dbfs !== undefined && <span><strong>{synthesis.peak_dbfs.toFixed(1)} dBFS</strong> 峰值</span>}
        </div>
      )}

      <p className="voice-quality-action">{localResult?.primary_action ?? meta.detail}</p>

      {(onRetry || onRerecord) && (
        <div className="voice-quality-actions">
          {onRetry && <button type="button" onClick={onRetry}>重新检查</button>}
          {onRerecord && <button type="button" onClick={onRerecord}>重新录音</button>}
        </div>
      )}
    </section>
  );
}
