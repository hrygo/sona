import { useEffect, useRef, useState } from "react";
import { hasAcceptedSynthesis, synthesisQuality, type VoiceCatalogItem } from "../contracts/voiceContract";
import { SPEECHRAIL_TTS_MODEL, voiceService } from "../services/voiceService";
import { playAudioBlob } from "../utils/audioPlayback";
import { VoiceQualityCard } from "./VoiceQualityCard";

interface Props {
  readonly voice: VoiceCatalogItem;
  readonly disabled?: boolean;
  readonly onUpdated: (voice: VoiceCatalogItem) => void;
  readonly onSelect: (id: string) => void | boolean | Promise<void | boolean>;
  readonly onBusyChange?: (busy: boolean) => void;
}

/** Registration/reference evidence never stands in for synthesized-output evidence. */
export function VoiceCandidateReview({ voice, onUpdated, onSelect, onBusyChange, disabled = false }: Props) {
  const [reference] = useState(voice.quality?.reference ? voice.quality : undefined);
  const [output, setOutput] = useState(synthesisQuality(voice.quality));
  const [busy, setBusy] = useState<"quality" | "audition" | "activate" | null>(null);
  const [heard, setHeard] = useState(false);
  const [activated, setActivated] = useState(false);
  const [message, setMessage] = useState("");
  const [playbackMessage, setPlaybackMessage] = useState("");
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; controller.current?.abort(); };
  }, []);
  useEffect(() => { onBusyChange?.(busy !== null); }, [busy, onBusyChange]);

  async function checkOutput() {
    if (inFlight.current || disabled) return;
    inFlight.current = true;
    const abort = new AbortController();
    controller.current = abort;
    setBusy("quality"); setOutput(undefined); setHeard(false); setActivated(false); setMessage(""); setPlaybackMessage("");
    onUpdated({ ...voice, quality: undefined });
    try {
      const report = await voiceService.qualityRun(voice.id, { runs: 3 }, abort.signal);
      if (!mounted.current || abort.signal.aborted) return;
      const scoped = synthesisQuality(report);
      setOutput(scoped);
      if (scoped) onUpdated({ ...voice, quality: scoped });
      else setMessage("未收到合成输出报告，不能把参考验收视为输出通过。请确认 SpeechRail 版本。");
    } catch {
      if (mounted.current && !abort.signal.aborted) {
        setMessage("输出检查未完成，音色仍已保存。请确认服务状态后重新检查，无需重新创建。");
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  async function audition() {
    if (inFlight.current || disabled) return;
    inFlight.current = true;
    const abort = new AbortController(); controller.current = abort;
    setBusy("audition"); setHeard(false); setActivated(false); setPlaybackMessage("");
    try {
      const audio = await voiceService.speech({
        model: SPEECHRAIL_TTS_MODEL, voice: voice.id, response_format: "wav", language: "zh",
        input: "你好，这是保存后的声音。我们换一段新内容，听听音色是否自然，停顿是否清楚。",
      }, abort.signal);
      await playAudioBlob(audio, abort.signal);
      if (mounted.current && !abort.signal.aborted) setHeard(true);
    } catch {
      if (mounted.current && !abort.signal.aborted) setPlaybackMessage("实际音色试听失败，尚未完成听感确认。");
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  async function activate() {
    if (inFlight.current || disabled || !accepted || !heard) return;
    inFlight.current = true; setBusy("activate"); setMessage("");
    try {
      const acknowledged = await onSelect(voice.id);
      if (!mounted.current) return;
      if (acknowledged === false) setMessage("启用未获确认，候选音色仍已保存。请检查控制连接后重试。");
      else setActivated(true);
    } catch {
      if (mounted.current) setMessage("启用未获确认，候选音色仍已保存。请检查控制连接后重试。");
    } finally {
      inFlight.current = false;
      if (mounted.current) setBusy(null);
    }
  }
  function cancel() {
    controller.current?.abort();
    if (busy === "quality") setMessage("已停止等待输出检查，音色仍已保存，可重新检查。服务端可能仍在完成清理。");
    else setPlaybackMessage("试听已停止，请重新完整试听后确认使用。");
    setHeard(false);
  }

  const accepted = hasAcceptedSynthesis({ ...voice, quality: output });
  return <section className="voice-candidate-review" aria-label="已保存音色的检查与使用">
    <div className="voice-candidate-heading">
      <span className="voice-step-number">3</span>
      <div><h3>「{voice.name}」已保存</h3><p>参考已入库，不会自动替换当前音色。先检查输出，再试听确认。</p></div>
    </div>
    <VoiceQualityCard title="参考音频验收" report={reference} scope="reference" />
    <VoiceQualityCard title="合成输出检查" report={output} pending={busy === "quality"} scope="synthesis" />
    <p className="voice-flow-help">完整检查包含 6 类文本，每类重复 3 次，并由本地 ASR 核对内容。通过不代表声纹或背景噪声已获独立验证。</p>
    {message && <p className="forge-error-banner" role="alert">{message}</p>}
    {playbackMessage && <p className="forge-error-banner" role="alert">{playbackMessage}</p>}
    <div className="voice-result-actions">
      <button type="button" className="btn-design-preview" disabled={disabled || busy !== null} onClick={() => void checkOutput()}>
        {busy === "quality" ? "正在检查 18 段输出…" : output ? "重新检查输出" : "检查输出（18 段）"}
      </button>
      <button type="button" className="btn-design-preview" disabled={disabled || busy !== null} onClick={() => void audition()}>
        {busy === "audition" ? "正在试听…" : "试听实际音色"}
      </button>
      <button type="button" className="btn-submit-design" disabled={disabled || busy !== null || !accepted || !heard || activated}
        onClick={() => void activate()}>{busy === "activate" ? "正在确认启用…" : activated ? "已确认启用" : "确认使用此音色"}</button>
      {(busy === "quality" || busy === "audition") && <button type="button" className="btn-design-preview" onClick={cancel}>
        {busy === "quality" ? "停止等待检查" : "停止试听"}
      </button>}
    </div>
    <p className="voice-flow-help" aria-live="polite">
      {activated ? "服务端已确认启用，可以关闭声音工坊。" : !accepted ? "输出通过检查后，再试听并确认使用。" : !heard ? "输出检查已通过，请试听保存后的实际声音。" : "已完成输出检查与试听，可以手动确认使用。"}
    </p>
  </section>;
}
