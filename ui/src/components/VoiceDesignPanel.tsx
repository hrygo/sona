import { useEffect, useRef, useState } from "react";
import type { VoiceCatalogItem, VoiceDesignRequest } from "../contracts/voiceContract";
import { SPEECHRAIL_TTS_MODEL, VoiceServiceError, voiceService } from "../services/voiceService";
import { registrationMayHaveCompleted } from "../services/voiceWorkflow";
import { playAudioBlob } from "../utils/audioPlayback";
import { VoiceCandidateReview } from "./VoiceCandidateReview";
import { DESIGN_REFERENCE_TEXT, VOICE_DESIGN_EXAMPLES, newDesignVoiceId, validateDesignText, type VoiceDesignExample } from "./voiceDesignExamples";
import "./VoiceDesignPanel.css";

interface Props {
  readonly canRegister: boolean;
  readonly canPreview: boolean;
  readonly deletedVoiceId?: string;
  readonly externalBusy?: boolean;
  readonly onDirtyChange?: (dirty: boolean) => void;
  readonly onCreated: (voice: VoiceCatalogItem) => void;
  readonly onSelect: (id: string) => void | boolean | Promise<void | boolean>;
  readonly onBusyChange: (busy: boolean) => void;
}

function designErrorMessage(error: unknown): string {
  if (error instanceof VoiceServiceError) {
    if (error.status === 404 || error.status === 405) return "SpeechRail 尚不支持新的音色注册流程，请升级到包含 /v1/voices/designs 的版本。不会退回仅保存提示词的旧流程。";
    if (error.code === "voice_design_unsupported" || error.code === "voice_registration_unsupported" || error.code === "voice_design_registration_unsupported") return "请在 SpeechRail 中启用 Quality 档并安装所需模型。";
    if (error.code === "transcription_unavailable") return "本地 ASR 不可用，无法核对参考朗读。请恢复 SpeechRail ASR 后再试。";
    if (error.code === "transcript_mismatch") return "生成语音与参考文本不一致。可以简化参考文本，或适当调整声音描述。";
    if (error.code === "mode_conflict") return "会议或字幕正在占用音频资源。请先结束该模式，再创建音色；当前尚未注册。";
    if (error.status === 409) return "此注册 ID 已存在，可能是上次请求已完成。请先检查保存结果，不会覆盖已有音色。";
    if (error.status === 429) return "语音资源正在使用中，请在会议、字幕或其他语音任务结束后重试。";
    if (error.status >= 500 && !registrationMayHaveCompleted(error)) return "生成或参考核验未完成，尚未保存音色。请检查服务，可以修改后重试。";
    if (error.status === 0 || error.status >= 500) return "尚未确认注册结果。请先检查档案库，避免重复创建；重试将保留同一注册 ID。";
    return error.message;
  }
  return "注册未完成，请检查服务状态后重试。";
}

export function VoiceDesignPanel({ canRegister, canPreview, onCreated, onSelect, onBusyChange, deletedVoiceId, externalBusy = false, onDirtyChange }: Props) {
  const [name, setName] = useState("");
  const [instruction, setInstruction] = useState("");
  const [reference, setReference] = useState(DESIGN_REFERENCE_TEXT);
  const [seedText, setSeedText] = useState("42");
  const [selected, setSelected] = useState<VoiceDesignExample | null>(null);
  const [pendingExample, setPendingExample] = useState<VoiceDesignExample | null>(null);
  const [busy, setBusy] = useState<"preview" | "register" | "reconcile" | null>(null);
  const [error, setError] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [voice, setVoice] = useState<VoiceCatalogItem | null>(null);
  const [attempt, setAttempt] = useState<VoiceDesignRequest | null>(null);
  const [candidateBusy, setCandidateBusy] = useState(false);
  const [restartRequested, setRestartRequested] = useState(false);
  const [previewed, setPreviewed] = useState(false);
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const descriptionInput = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; controller.current?.abort(); };
  }, []);
  useEffect(() => { if (!voice) onBusyChange(busy !== null); }, [busy, voice, onBusyChange]);

  useEffect(() => {
    const edited = !voice && (uncertain || Boolean(instruction.trim() && instruction !== selected?.instruction)
      || Boolean(name.trim() && name !== selected?.name) || reference !== DESIGN_REFERENCE_TEXT || seedText !== "42");
    onDirtyChange?.(edited);
  }, [voice, uncertain, instruction, name, selected, reference, seedText, onDirtyChange]);

  function resumeEditing() {
    setVoice(null); setAttempt(null); setUncertain(false); setPreviewed(false);
    setRestartRequested(false); setError(""); setCandidateBusy(false); onBusyChange(false);
  }
  useEffect(() => {
    if (deletedVoiceId && voice?.id === deletedVoiceId) {
      setVoice(null); setAttempt(null); setUncertain(false); setCandidateBusy(false);
      setError("候选音色已删除，描述已保留，可以修改后重新创建。");
    }
  }, [deletedVoiceId, voice?.id]);

  function applyExample(example: VoiceDesignExample) {
    if (!name.trim() || name === selected?.name) setName(example.name);
    setInstruction(example.instruction); setSelected(example); setPendingExample(null);
    setError(""); setPreviewed(false); descriptionInput.current?.focus();
  }
  function chooseExample(example: VoiceDesignExample) {
    if (instruction.trim() && instruction !== selected?.instruction) setPendingExample(example);
    else applyExample(example);
  }
  function validation() {
    if (!name.trim()) return "请给这个声音起一个名称";
    if (!seedText.trim()) return "随机种子不能为空";
    return validateDesignText(instruction, reference, Number(seedText));
  }
  async function preview() {
    if (inFlight.current || externalBusy || !canPreview) return;
    const invalid = validateDesignText(instruction, reference, seedText.trim() ? Number(seedText) : NaN);
    if (invalid) { setError(invalid); return; }
    inFlight.current = true; setBusy("preview"); setPreviewed(false); setError("");
    const abort = new AbortController(); controller.current = abort;
    try {
      const audio = await voiceService.preview({ model: SPEECHRAIL_TTS_MODEL,
        input: reference.trim(), instruction: instruction.trim(), seed: Number(seedText),
        language: "zh", response_format: "wav" }, abort.signal);
      await playAudioBlob(audio, abort.signal);
      if (mounted.current && !abort.signal.aborted) setPreviewed(true);
    } catch { if (mounted.current && !abort.signal.aborted) setError("草稿试听未完成，未创建音色。请检查语音服务后重试，也可以修改描述。"); }
    finally { inFlight.current = false; if (mounted.current) setBusy(null); }
  }
  async function register() {
    if (inFlight.current || externalBusy || !canRegister) return;
    const invalid = validation();
    if (invalid) { setError(invalid); return; }
    inFlight.current = true; setBusy("register"); setError("");
    const abort = new AbortController(); controller.current = abort;
    // Ambiguous transport outcomes retain BOTH the ID and exact request body.
    let sent = false;
    try {
    const request = uncertain && attempt ? attempt : {
      id: attempt?.id ?? newDesignVoiceId(), name: name.trim(), instruction: instruction.trim(),
      reference_text: reference.trim(), seed: Number(seedText), language: "zh" as const,
    };
    setAttempt(request);
    sent = true;
      const result = await voiceService.design(request, abort.signal);
      if (!mounted.current || abort.signal.aborted) return;
      setVoice(result.voice); setUncertain(false); onCreated(result.voice);
    } catch (cause) {
      if (!mounted.current) return;
      setError(abort.signal.aborted ? "已停止等待注册，服务端可能已保存。请先检查保存结果，不要直接重复创建。" : designErrorMessage(cause));
      setUncertain(sent && (abort.signal.aborted || registrationMayHaveCompleted(cause)));
    } finally { inFlight.current = false; if (mounted.current) { setBusy(null); onBusyChange(false); } }
  }
  async function reconcile() {
    if (inFlight.current || !attempt) return;
    inFlight.current = true; setBusy("reconcile"); setError("");
    const abort = new AbortController(); controller.current = abort;
    try {
      const found = (await voiceService.list(abort.signal)).find((item) => item.id === attempt.id);
      if (!mounted.current || abort.signal.aborted) return;
      if (found?.creation?.origin === "generated" && found.mode === "clone" && found.name === attempt.name && found.creation.seed === attempt.seed) {
        setVoice(found); setUncertain(false); onCreated(found);
      } else setError("暂未找到匹配的已保存音色。请确认服务端任务结束后，再用同一注册 ID 重试；不要反复创建新任务。");
    } catch { if (mounted.current) setError("档案库暂不可用，注册结果仍未确认。"); }
    finally { inFlight.current = false; if (mounted.current) setBusy(null); }
  }

  if (voice) return <div className="design-forge-container">
    <button type="button" className="btn-design-preview" disabled={candidateBusy || externalBusy} onClick={resumeEditing}>基于此描述再设计</button>
    <p className="voice-flow-help">返回后保留描述，新设计使用新的 ID，不会覆盖已保存的音色。</p>
    <VoiceCandidateReview key={voice.id} voice={voice} disabled={externalBusy || !canRegister} onUpdated={onCreated} onSelect={onSelect} onBusyChange={(value) => { setCandidateBusy(value); onBusyChange(value); }} />
  </div>;

  const locked = busy !== null || uncertain || externalBusy;
  return <div className="design-forge-container" aria-busy={busy !== null}>
    <section className="voice-example-section" aria-labelledby="voice-examples-heading">
      <div className="voice-section-heading"><span className="voice-step-number">1</span><div>
        <h3 id="voice-examples-heading">先选一个示例，再改成你的声音</h3>
        <p>以下是可编辑的描述模板，不是预制音频。不需要掌握专业提示词。</p>
      </div></div>
      <div className="voice-example-grid" aria-label="音色描述示例">
        {VOICE_DESIGN_EXAMPLES.map((example) => <button type="button" key={example.id}
          className={`voice-example-card design-inspiration-chip ${selected?.id === example.id ? "selected" : ""}`}
          aria-pressed={selected?.id === example.id} disabled={locked}
          onClick={() => chooseExample(example)}>
          <strong>{example.title}{selected?.id === example.id && <span className="voice-example-selected">已选</span>}</strong><span>{example.use}</span><small>{example.traits}</small>
        </button>)}
      </div>
      {pendingExample && <div className="voice-template-confirm" role="group" aria-label="确认替换已修改的描述">
        <p>当前描述有修改。套用「{pendingExample.title}」将替换描述，自定义名称和参考文本会保留。</p>
        <button type="button" disabled={locked} onClick={() => applyExample(pendingExample)}>替换描述</button>
        <button type="button" disabled={locked} onClick={() => setPendingExample(null)}>保留当前描述</button>
      </div>}
    </section>
    <fieldset className="voice-design-fields" disabled={locked}>
      <legend><span className="voice-step-number">2</span> 调整描述与参考文本</legend>
      <div className="forge-field"><label htmlFor="design-name-input" className="forge-label">音色名称</label>
        <input id="design-name-input" className="forge-input" value={name} maxLength={64}
          placeholder="例如：技术分享男声" onChange={(event) => { setName(event.target.value); setError(""); }} />
      </div>
      <div className="forge-field"><div className="forge-label-split">
        <label htmlFor="design-instruction-input" className="forge-label">声音描述（可直接修改示例）</label>
        <span className="forge-counter">{Array.from(instruction).length} / 1000</span></div>
        <textarea ref={descriptionInput} id="design-instruction-input" className="forge-textarea" rows={4}
          maxLength={1000} value={instruction} aria-describedby="voice-description-help"
          placeholder="先选一个示例，或描述声音的音色、发音、节奏和语气。"
          onChange={(event) => { setInstruction(event.target.value); setPreviewed(false); setError(""); }} />
        <p id="voice-description-help" className="voice-flow-help">可以只改一两处：男声/女声、明亮/温润、适中/稍慢、平和/活泼。避免同时要求“低沉又尖细、飞快又舒缓”。这里写声音特点，不写要朗读的内容。</p>
        {selected && <span className="voice-template-state">基于「{selected.title}」{instruction === selected.instruction ? " · 可继续修改" : " · 已修改"}</span>}
      </div>
      <div className="forge-field"><div className="forge-label-split">
        <label htmlFor="design-preview-input" className="forge-label">参考朗读文本</label>
        <span className="forge-counter">{Array.from(reference.trim()).length} / 240</span></div>
        <textarea id="design-preview-input" className="forge-textarea" rows={3} value={reference} maxLength={240}
          aria-describedby="voice-reference-help"
          onChange={(event) => { setReference(event.target.value); setPreviewed(false); setError(""); }} />
        <p id="voice-reference-help" className="voice-flow-help">这是模型实际要读的内容（中文 20–240 字）。默认文本即可；它会成为可复用音色的参考，而不是固定每次播报的话。</p>
      </div>
      <details className="voice-design-advanced"><summary>高级设置 · 随机种子</summary>
        <label htmlFor="design-seed">随机种子（默认 42）</label>
        <input id="design-seed" className="forge-input" type="number" min="0" max="4294967295" step="1" value={seedText}
          onChange={(event) => { setSeedText(event.target.value); setPreviewed(false); }} />
        <p className="voice-flow-help">改变种子可以探索另一个声音；固定种子不保证不同文本的声纹完全一致。</p>
      </details>
    </fieldset>
    {!canRegister && <p className="voice-flow-help" role="status">创建可复用音色需要 SpeechRail Quality 档的设计与克隆能力，以及支持新注册接口的版本。请先确认服务配置。</p>}
    <div className="voice-design-footer">
      <div className="voice-result-actions">
        <button type="button" className="btn-design-preview" disabled={!canPreview || locked || !instruction.trim()}
          onClick={() => void preview()}>{busy === "preview" ? "正在试听草稿…" : "试听草稿"}</button>
        <button type="button" className="btn-submit-design" disabled={!canRegister || externalBusy || busy !== null || !name.trim() || !instruction.trim()}
          onClick={() => void register()}>{busy === "register" ? "正在生成并核验参考…" : uncertain ? "使用同一 ID 重试" : "生成并保存可复用音色"}</button>
      </div>
      {busy && <button type="button" className="btn-design-preview" onClick={() => {
        controller.current?.abort(); setPreviewed(false);
        if (busy === "preview") setError("草稿试听已停止，可以修改后重试。");
      }}>{busy === "preview" ? "停止试听" : "停止等待"}</button>}
      <p className="voice-flow-help" aria-live="polite">{busy === "register"
        ? "服务端正在生成参考、检查音频和核对朗读文本。此阶段尚未验证最终合成输出。"
        : previewed ? "已试听草稿。保存时会重新生成并核验参考；最终音色需在保存后另外试听。"
          : "草稿试听不会保存音色。保存后仍需检查输出与试听，不会自动启用。"}</p>
    </div>
    {error && <p className="forge-error-banner" role="alert">{error}</p>}
    {uncertain && attempt && <div className="voice-registration-recovery">
      <p className="voice-flow-help">待确认注册 ID：<code>{attempt.id}</code></p>
      <button type="button" className="btn-design-preview" disabled={busy !== null} onClick={() => void reconcile()}>检查保存结果</button>
      <button type="button" className="btn-design-preview" disabled={busy !== null || externalBusy} onClick={() => setRestartRequested(true)}>修改描述并创建新候选</button>
      {restartRequested && <div role="group" aria-label="确认新建候选">
        <p>上次请求可能已经保存。新候选会使用另一 ID，旧候选仍留在档案库，请确认没有重复后再继续。</p>
        <button type="button" disabled={busy !== null} onClick={resumeEditing}>确认开始新候选</button>
        <button type="button" onClick={() => setRestartRequested(false)}>保留当前待确认记录</button>
      </div>}
    </div>}
  </div>;
}
