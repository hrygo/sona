import { useCallback, useEffect, useRef, useState } from "react";
import {
  SPEECHRAIL_TTS_MODEL,
  voiceService,
  VoiceServiceError,
} from "../services/voiceService";
import { registrationMayHaveCompleted } from "../services/voiceWorkflow";
import { newDesignVoiceId } from "./voiceDesignExamples";
import { playAudioBlob } from "../utils/audioPlayback";
import { showToast } from "./Toast";
import { SoundWaveAnimatedIcon } from "./Icons";
import {
  hasPassingSynthesisProbe,
  hasSynthesisProbeEvidence,
  supportsVoiceCapability,
  synthesisQuality,
  hasAcceptedSynthesis,
  type VoiceQualityReport,
  type VoiceModelCapabilities,
} from "../contracts/voiceContract";
import {
  evaluateCaptureSafety,
  inspectVoiceRecording,
  type LocalVoiceQualityResult,
} from "../utils/voiceQuality";
import {
  type VoiceCatalogItem,
  type VoiceMode,
  resolveVoiceMode,
  VOICE_MODE_META,
} from "./assistantPresentation";
import { VoiceDeleteModal } from "./VoiceDeleteModal";
import { VoiceQualityCard } from "./VoiceQualityCard";
import { VoiceDesignPanel } from "./VoiceDesignPanel";
import { VoiceCandidateReview } from "./VoiceCandidateReview";
import "./VoiceStudioModal.css";

export interface VoiceStudioModalProps {
  readonly currentVoiceId: string;
  readonly initialReviewVoice?: VoiceCatalogItem;
  readonly onRefresh?: () => Promise<void>;
  readonly availableVoices: readonly VoiceCatalogItem[];
  readonly modelCapabilities?: VoiceModelCapabilities;
  readonly onSelectVoice: (voiceId: string) => void | boolean | Promise<void | boolean>;
  readonly onVoiceCreated: (newVoice: VoiceCatalogItem) => void;
  readonly onVoiceDeleted: (voiceId: string) => void | Promise<void>;
  readonly onClose: () => void;
  /** 在打开浏览器麦克风前暂停语音助手，并等待服务端确认。 */
  readonly onStartRecordingVoice?: () => void | Promise<void>;
  /** 录音结束或组件销毁时恢复语音助手输入。 */
  readonly onStopRecordingVoice?: () => void | Promise<void>;
}

interface ClonePromptItem {
  readonly id: string;
  readonly category: string;
  readonly title: string;
  readonly script: string;
  readonly tips: string;
}

const FALLBACK_PROMPTS: readonly ClonePromptItem[] = [
  {
    id: "poetry_tang",
    category: "classic",
    title: "📜 盛唐气象 · 经典诗韵",
    script: "白日依山尽，黄河入海流。欲穷千里目，更上一层楼。春江潮水连海平，海上明月共潮生。",
    tips: "字正腔圆，声调平稳从容，注意句尾自然停顿。",
  },
  {
    id: "prose_technology",
    category: "tech",
    title: "⚡ 科技浪潮 · 现代叙述",
    script: "人工智能正在深刻改变我们的交互方式，让每一次人机对话都充满温度与智慧。保持探索的热情，方能见证未来的无限可能。",
    tips: "语速适中，吐字清脆明快，保持自然表达状态。",
  },
  {
    id: "daily_dialogue",
    category: "life",
    title: "☕ 晨光午后 · 日常伴随",
    script: "清晨的阳光透过窗棂洒在桌前，微风拂过绿植，带来清新怡人的气息。今天也是从容充实的一天，随时为你提供帮助。",
    tips: "语调温和亲切，如同与身旁好友促膝交谈。",
  },
  {
    id: "philosophical_exploration",
    category: "deep",
    title: "🌌 星辰大海 · 哲思沉稳",
    script: "浩瀚星空无垠深邃，人类对真理的探索永不止步。唯有在宁静中沉淀思考，方能听见内心深处最真实的声音。",
    tips: "低沉醇厚，字句饱满有力，略带思考的韵味。",
  },
];

export function VoiceStudioModal({
  currentVoiceId,
  initialReviewVoice,
  onRefresh,
  availableVoices,
  modelCapabilities,
  onSelectVoice,
  onVoiceCreated,
  onVoiceDeleted,
  onClose,
  onStartRecordingVoice,
  onStopRecordingVoice,
}: VoiceStudioModalProps) {
  const canClone = supportsVoiceCapability(modelCapabilities, "supports_clone");
  const canPreview = supportsVoiceCapability(modelCapabilities, "supports_preview");
  const canDesign = supportsVoiceCapability(modelCapabilities, "supports_instruction");

  // 创设模式：录音克隆 (clone) 或 自然语言设计 (design)
  const [activeTab, setActiveTab] = useState<"clone" | "design">(() => (
    canClone ? "clone" : "design"
  ));
  const selectedTab = canClone && activeTab === "clone"
    ? "clone"
    : canDesign
      ? "design"
      : "clone";

  // 音色资产库模式过滤与删除确认弹窗状态
  const [filterMode, setFilterMode] = useState<"all" | VoiceMode>("all");
  const [auditioningVoiceId, setAuditioningVoiceId] = useState<string | null>(null);
  const [deleteTargetVoice, setDeleteTargetVoice] = useState<VoiceCatalogItem | null>(null);

  /* ====================== 1. 录音克隆 (Voice Clone) 状态 ====================== */
  const [prompts, setPrompts] = useState<readonly ClonePromptItem[]>(FALLBACK_PROMPTS);
  const [promptIndex, setPromptIndex] = useState(0);
  const activePrompt = prompts[promptIndex] || FALLBACK_PROMPTS[0];

  const [cloneStage, setCloneStage] = useState<"ready" | "recording" | "recorded" | "submitting" | "success">("ready");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [recordedBlob, setRecordedBlob] = useState<Blob | null>(null);
  const [recordedAudioUrl, setRecordedAudioUrl] = useState<string | null>(null);
  const [cloneName, setCloneName] = useState("");
  const [cloneError, setCloneError] = useState("");
  const [lastClonedVoice, setLastClonedVoice] = useState<VoiceCatalogItem | null>(null);
  const [localQuality, setLocalQuality] = useState<LocalVoiceQualityResult | undefined>();
  const [serverValidation, setServerValidation] = useState<VoiceQualityReport | undefined>();
  const [serverValidationPending, setServerValidationPending] = useState(false);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [designBusy, setDesignBusy] = useState(false);
  const [reviewingVoice, setReviewingVoice] = useState<VoiceCatalogItem | null>(initialReviewVoice ?? null);
  const [refreshing, setRefreshing] = useState(false);
  const [designDirty, setDesignDirty] = useState(false);
  const [closeRequested, setCloseRequested] = useState(false);
  const [abandonClone, setAbandonClone] = useState(false);
  const continueEditing = useRef<HTMLButtonElement | null>(null);
  const [refreshError, setRefreshError] = useState("");
  const refreshInFlight = useRef(false);
  const [recordedReferenceText, setRecordedReferenceText] = useState("");
  const [recordedFilename, setRecordedFilename] = useState("recording.webm");
  const [captureInfo, setCaptureInfo] = useState("");
  const recordingScriptRef = useRef("");
  const [recordingPrompt, setRecordingPrompt] = useState<ClonePromptItem | null>(null);
  const [cloneUncertain, setCloneUncertain] = useState(false);
  const cloneAttemptRef = useRef<{ id: string; name: string; refText: string; form: FormData } | null>(null);
  const cloneController = useRef<AbortController | null>(null);
  const [deletedVoiceId, setDeletedVoiceId] = useState<string>();
  const [selectingVoiceId, setSelectingVoiceId] = useState<string | null>(null);
  const selectInFlight = useRef(false);
  const [selectionError, setSelectionError] = useState("");
  const rawAudioRef = useRef<HTMLAudioElement | null>(null);
  const [rawPlaying, setRawPlaying] = useState(false);
  const [muteState, setMuteState] = useState<"preparing" | "ready" | "error">(onStartRecordingVoice ? "preparing" : "ready");
  const [muteRetry, setMuteRetry] = useState(0);
  const cloneInFlight = useRef(false);
  const auditionController = useRef<AbortController | null>(null);
  const auditionInFlight = useRef(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  // 麦克风录音实例与媒体流引用
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const assistantMuteActiveRef = useRef(false);
  const recordingStartInFlightRef = useRef(false);
  const recordingAttemptRef = useRef(0);
  const isMountedRef = useRef(true);
  const onStopRecordingVoiceRef = useRef(onStopRecordingVoice);
  onStopRecordingVoiceRef.current = onStopRecordingVoice;
  const [isStartingRecording, setIsStartingRecording] = useState(false);

  // 环境音量与信噪指示
  const [micLevelStatus, setMicLevelStatus] = useState<"good" | "quiet" | "loud">("good");

  const releaseAssistantMute = useCallback(async () => {
    if (!assistantMuteActiveRef.current) return;
    // 先清除本地租约，避免 recorder.onstop、reset 和 unmount 重复恢复输入。
    assistantMuteActiveRef.current = false;
    try {
      await onStopRecordingVoiceRef.current?.();
    } catch {
      // 保留租约，让后续 reset/unmount 仍有机会重试恢复助手输入。
      assistantMuteActiveRef.current = true;
      showToast("语音助手麦克风恢复失败，请检查控制连接", "error");
    }
  }, []);

  const operationBusy = designBusy || reviewBusy || cloneStage === "submitting"
    || cloneStage === "recording" || isStartingRecording || auditioningVoiceId !== null
    || selectingVoiceId !== null || deleteTargetVoice !== null || rawPlaying || refreshing;
  const hasLocalDraft = designDirty || (recordedBlob !== null && cloneStage !== "success") || cloneUncertain;
  const requestClose = useCallback(() => {
    if (operationBusy) return;
    if (hasLocalDraft) setCloseRequested(true);
    else onClose();
  }, [operationBusy, hasLocalDraft, onClose]);
  useEffect(() => { if (closeRequested) continueEditing.current?.focus(); }, [closeRequested]);

  // Fail closed on mute failure, and allow retry/close while the command is pending.
  useEffect(() => {
    let cancelled = false;
    assistantMuteActiveRef.current = onStartRecordingVoice !== undefined;
    if (!onStartRecordingVoice) { setMuteState("ready"); return; }
    setMuteState("preparing");
    Promise.resolve().then(onStartRecordingVoice).then(() => {
      if (!cancelled) setMuteState("ready");
    }).catch(() => {
      if (!cancelled) setMuteState("error");
    });
    return () => { cancelled = true; };
  }, [onStartRecordingVoice, muteRetry]);

  // 加载精选引导文案库
  useEffect(() => {
    let cancelled = false;
    async function fetchPrompts() {
      try {
        const data = await voiceService.clonePrompts();
        if (!cancelled && data.length > 0) {
          setPrompts(data);
        }
      } catch {
        // 降级使用内置精选文案库
      }
    }
    void fetchPrompts();
    return () => {
      cancelled = true;
    };
  }, []);

  // 快捷键 Esc 关闭
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented && !operationBusy) {
        if (closeRequested) setCloseRequested(false);
        else requestClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [requestClose, operationBusy, closeRequested]);

  // Keep keyboard navigation inside the modal, restoring the caller on exit.
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialogRef.current || dialogRef.current.querySelector(".voice-delete-modal-dialog")) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary, audio[controls], [tabindex="0"]',
      )).filter((element) => element.getClientRects().length > 0 && !element.matches(":disabled") && !element.closest('[inert]'));
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (!first || !last) { event.preventDefault(); dialogRef.current.focus(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialogRef.current || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => { document.removeEventListener("keydown", trap); previous?.focus(); };
  }, []);

  // 清理音频流与计时器
  const cleanupRecording = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    const recorder = mediaRecorderRef.current;
    mediaRecorderRef.current = null;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        // 静默
      }
    }
    if (audioStreamRef.current) {
      for (const track of audioStreamRef.current.getTracks()) {
        track.stop();
      }
      audioStreamRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      try {
        void audioCtxRef.current.close();
      } catch {
        // 静默
      }
      audioCtxRef.current = null;
    }
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      recordingAttemptRef.current += 1;
      recordingStartInFlightRef.current = false;
      cleanupRecording();
      auditionController.current?.abort();
      cloneController.current?.abort();
      rawAudioRef.current?.pause();
      void releaseAssistantMute();
    };
  }, [cleanupRecording, releaseAssistantMute]);

  useEffect(() => {
    return () => {
      if (recordedAudioUrl) {
        URL.revokeObjectURL(recordedAudioUrl);
      }
    };
  }, [recordedAudioUrl]);

  /* ====================== 录音与波形绘制核心逻辑 ====================== */
  const startRecording = useCallback(async () => {
    if (recordingStartInFlightRef.current || muteState !== "ready" || operationBusy) return;
    recordingStartInFlightRef.current = true;
    const attempt = recordingAttemptRef.current + 1;
    recordingAttemptRef.current = attempt;
    setIsStartingRecording(true);
    setCloneError("");
    setRecordingPrompt(activePrompt);
    recordingScriptRef.current = activePrompt.script;
    try {
      cleanupRecording();

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Request unprocessed capture; verify actual settings rather than assuming
          // these constraints prove a quiet environment or a particular sample rate.
          echoCancellation: false,
          // Preserve the reference timbre; browser NS can gate consonants and
          // introduce artifacts that the clone model learns as part of the voice.
          noiseSuppression: false,
          autoGainControl: false,
          sampleRate: 24000,
        },
      });
      if (!isMountedRef.current || recordingAttemptRef.current !== attempt) {
        for (const track of stream.getTracks()) {
          track.stop();
        }
        return;
      }
      audioStreamRef.current = stream;
      const actual = stream.getAudioTracks?.()[0]?.getSettings?.();
      setCaptureInfo(actual ? `设备实际采样率：${actual.sampleRate ?? "未知"} Hz · 声道：${actual.channelCount ?? "未知"} · 自动增益：${actual.autoGainControl === undefined ? "未报告" : actual.autoGainControl ? "开" : "关"}` : "设备未提供实际采集参数");

      // 初始化 Web Audio API 进行实时波形与能量绘制
      const AudioContextClass = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const audioCtx = new AudioContextClass();
      audioCtxRef.current = audioCtx;
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      audioChunksRef.current = [];
      const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"]
        .find((type) => typeof MediaRecorder.isTypeSupported === "function" && MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      const actualMimeType = recorder.mimeType || mimeType || "audio/webm";
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      const failCapture = () => {
        if (!isMountedRef.current || recordingAttemptRef.current !== attempt) return;
        recordingAttemptRef.current += 1;
        recordingStartInFlightRef.current = false;
        cleanupRecording(); setIsStartingRecording(false);
        setCloneStage("ready"); setRecordingPrompt(null);
        setCloneError("录音设备中断或录制失败，本次未保存。请检查麦克风后重新录制。");
      };
      recorder.onerror = failCapture;
      for (const track of stream.getTracks()) track.onended = failCapture;
      recorder.onstop = () => {
        if (!isMountedRef.current || recordingAttemptRef.current !== attempt || mediaRecorderRef.current !== recorder) return;
        const fullBlob = new Blob(audioChunksRef.current, { type: actualMimeType });
        cleanupRecording();
        if (!fullBlob.size) {
          setCloneStage("ready"); setRecordingPrompt(null);
          setCloneError("没有收到录音数据，请检查麦克风后重新录制。");
          return;
        }
        setRecordedBlob(fullBlob);
        setRecordedReferenceText(recordingScriptRef.current);
        setRecordedFilename(actualMimeType.includes("mp4") ? "recording.m4a" : "recording.webm");
        const url = URL.createObjectURL(fullBlob);
        setRecordedAudioUrl(url);
        setCloneStage("recorded");
        if (!cloneName.trim()) {
          setCloneName(`我的专属音色_${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`);
        }
      };

      recorder.start(250);
      setCloneStage("recording");
      setRecordingSeconds(0);

      const startedAt = performance.now();
      timerRef.current = window.setInterval(() => {
        const elapsed = Math.floor((performance.now() - startedAt) / 1000);
        setRecordingSeconds(Math.min(elapsed, 30));
        if (elapsed >= 30) stopRecording();
      }, 250);

      // 实时绘制 Canvas 波形与电平检测
      const canvas = canvasRef.current;
      if (canvas && typeof canvas.getContext === "function") {
        const ctx = canvas.getContext("2d");
        const dataArray = new Uint8Array(analyser.frequencyBinCount);

        const draw = () => {
          if (!ctx) return;
          analyser.getByteFrequencyData(dataArray);

          let sum = 0;
          for (let i = 0; i < dataArray.length; i++) {
            sum += dataArray[i] * dataArray[i];
          }
          const rms = Math.sqrt(sum / dataArray.length) / 128;
          if (rms < 0.12) {
            setMicLevelStatus("quiet");
          } else if (rms > 0.85) {
            setMicLevelStatus("loud");
          } else {
            setMicLevelStatus("good");
          }

          ctx.clearRect(0, 0, canvas.width, canvas.height);
          const barWidth = (canvas.width / dataArray.length) * 2.2;
          let x = 0;

          const isLight = typeof document !== "undefined" && document.documentElement.dataset.theme === "light";
          for (let i = 0; i < dataArray.length; i += 2) {
            const barHeight = (dataArray[i] / 255) * canvas.height * 0.9;
            const gradient = ctx.createLinearGradient(0, canvas.height - barHeight, 0, canvas.height);
            if (isLight) {
              gradient.addColorStop(0, "rgba(225, 29, 72, 0.95)");
              gradient.addColorStop(1, "rgba(225, 29, 72, 0.4)");
            } else {
              gradient.addColorStop(0, "rgba(239, 68, 68, 0.9)");
              gradient.addColorStop(1, "rgba(244, 63, 94, 0.4)");
            }

            ctx.fillStyle = gradient;
            ctx.beginPath();
            ctx.roundRect(x, (canvas.height - barHeight) / 2, barWidth, Math.max(3, barHeight), 2);
            ctx.fill();

            x += barWidth + 2;
          }

          animFrameRef.current = requestAnimationFrame(draw);
        };
        draw();
      }
    } catch (err) {
      if (!isMountedRef.current || recordingAttemptRef.current !== attempt) return;
      cleanupRecording();
      const msg = err instanceof Error ? err.message : "无法开启麦克风，请检查浏览器权限";
      setCloneError(`麦克风采集失败: ${msg}`);
      setCloneStage("ready"); setRecordingPrompt(null);
    } finally {
      if (recordingAttemptRef.current === attempt) {
        recordingStartInFlightRef.current = false;
        if (isMountedRef.current) setIsStartingRecording(false);
      }
    }
  }, [cleanupRecording, cloneName, activePrompt, muteState, operationBusy]);

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stop();
    }
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
  }, []);

  const handleResetRecording = useCallback(() => {
    if (cloneInFlight.current) return;
    recordingAttemptRef.current += 1;
    recordingStartInFlightRef.current = false;
    cloneAttemptRef.current = null; setCloneUncertain(false);
    rawAudioRef.current?.pause(); setRawPlaying(false);
    cleanupRecording();
    if (recordedAudioUrl) {
      URL.revokeObjectURL(recordedAudioUrl);
      setRecordedAudioUrl(null);
    }
    setRecordedBlob(null);
    setRecordingSeconds(0);
    setCloneStage("ready");
    setCloneError("");
    setLocalQuality(undefined);
    setServerValidation(undefined);
    setServerValidationPending(false);
    setLastClonedVoice(null);
    setRecordedReferenceText("");
    setCaptureInfo(""); setRecordingPrompt(null);
  }, [cleanupRecording, recordedAudioUrl]);

  /* ====================== 参考预检 → 注册 → 恢复 ====================== */
  const handleSubmitClone = useCallback(async () => {
    if (cloneInFlight.current || operationBusy || muteState !== "ready") return;
    if (!canClone) { setCloneError("当前 TTS 模型不支持克隆，请切换至 Quality 配置"); return; }
    if (!recordedBlob) { setCloneError("请先完成一段语音录制"); return; }
    if (!cloneName.trim()) { setCloneError("请输入音色名称"); return; }
    if (!recordedReferenceText.trim()) { setCloneError("请填写这段音频中实际说出的内容"); return; }
    cloneInFlight.current = true;
    const abort = new AbortController(); cloneController.current = abort;
    const stillActive = () => isMountedRef.current && !abort.signal.aborted;
    let sent = false;
    setCloneError(""); setCloneStage("submitting");
    try {
      let attempt = cloneUncertain ? cloneAttemptRef.current : null;
      if (!attempt) {
        try {
          const result = evaluateCaptureSafety(await inspectVoiceRecording(recordedBlob));
          if (!stillActive()) return;
          setLocalQuality(result);
          if (result.status === "reject") { setCloneError(result.primary_action); return; }
        } catch {
          if (!stillActive()) return;
          setLocalQuality({ status: "unevaluated", failure_codes: [], primary_action: "浏览器无法完成本地检查，将由 SpeechRail 进行权威校验" });
        }
        const id = newDesignVoiceId().replace("design_", "clone_");
        const form = new FormData();
        form.append("audio", recordedBlob, recordedFilename);
        form.append("ref_text", recordedReferenceText.trim()); form.append("name", cloneName.trim()); form.append("id", id);
        attempt = { id, name: cloneName.trim(), refText: recordedReferenceText.trim(), form };
        cloneAttemptRef.current = attempt;
        setServerValidationPending(true);
        try {
          const validation = await voiceService.validateClone(form, abort.signal);
          if (!stillActive()) return;
          setServerValidation(validation);
          if (!validation.reference || (validation.status !== "pass" && validation.status !== "warn")) {
            setCloneError("参考预检未通过，尚未注册。请查看原因，修正参考文字或重新录音。"); return;
          }
        } catch {
          if (stillActive()) setCloneError("参考预检不可用，尚未提交注册。请确认 SpeechRail 服务和版本后重试。");
          return;
        } finally { if (isMountedRef.current) setServerValidationPending(false); }
      }
      if (!stillActive()) return;
      sent = true;
      const createdVoice = await voiceService.clone(attempt.form, abort.signal, attempt.id);
      if (!stillActive()) return;
      if (createdVoice.id !== attempt.id || createdVoice.mode !== "clone" || createdVoice.is_system) {
        throw new VoiceServiceError("注册响应不匹配，请检查保存结果", 502, "invalid_response");
      }
      setLastClonedVoice(createdVoice); setCloneUncertain(false);
      onVoiceCreated(createdVoice); setCloneStage("success");
      showToast(`音色「${createdVoice.name}」已保存，请继续检查输出与试听`, "success");
    } catch (cause) {
      if (!isMountedRef.current) return;
      const uncertain = sent && (abort.signal.aborted || registrationMayHaveCompleted(cause));
      setCloneUncertain(uncertain);
      setCloneError(uncertain ? "注册结果尚未确认，请先检查保存结果。重试沿用同一 ID 和录音，不会有意创建另一音色。"
        : cause instanceof Error ? cause.message : "注册未完成，请检查服务状态后重试。");
    } finally {
      cloneInFlight.current = false;
      if (isMountedRef.current) {
        setServerValidationPending(false);
        setCloneStage((stage) => stage === "submitting" ? "recorded" : stage);
        if (abort.signal.aborted && !sent) setCloneError("已停止等待，尚未发送注册请求；录音仍保留，可重试。");
        if (abort.signal.aborted && sent) {
          setCloneUncertain(true); setCloneError("已停止等待，服务端可能已保存。请先检查保存结果。");
        }
      }
    }
  }, [canClone, recordedBlob, cloneName, recordedFilename, recordedReferenceText, onVoiceCreated, cloneUncertain, muteState, operationBusy]);

  async function reconcileClone() {
    const attempt = cloneAttemptRef.current;
    if (!attempt || cloneInFlight.current || operationBusy) return;
    cloneInFlight.current = true; setCloneStage("submitting"); setCloneError("");
    const abort = new AbortController(); cloneController.current = abort;
    try {
      const found = (await voiceService.list(abort.signal)).find((item) => item.id === attempt.id);
      if (!isMountedRef.current || abort.signal.aborted) return;
      if (found?.mode === "clone" && !found.is_system && !found.creation
        && found.name === attempt.name && found.ref_text === attempt.refText) {
        setLastClonedVoice(found); setCloneUncertain(false); onVoiceCreated(found); setCloneStage("success");
      } else setCloneError("暂未找到匹配音色。任务可能尚未结束，请稍后检查或使用同一 ID 重试。");
    } catch { if (isMountedRef.current) setCloneError("档案库暂不可用，注册结果仍待确认。"); }
    finally {
      cloneInFlight.current = false;
      if (isMountedRef.current) setCloneStage((stage) => stage === "submitting" ? "recorded" : stage);
    }
  }

  async function selectVoice(id: string) {
    if (selectInFlight.current) return false;
    selectInFlight.current = true; setSelectingVoiceId(id); setSelectionError("");
    try {
      const acknowledged = await onSelectVoice(id);
      if (acknowledged === false) throw new Error("启用未获确认，请检查控制连接后重试。");
      return true;
    } catch {
      if (isMountedRef.current) setSelectionError("启用未获确认，当前音色以服务端状态为准。请检查控制连接后重试。");
      return false;
    } finally { selectInFlight.current = false; if (isMountedRef.current) setSelectingVoiceId(null); }
  }

  const handleCandidateUpdated = useCallback((updatedVoice: VoiceCatalogItem) => {
    setLastClonedVoice((current) => current?.id === updatedVoice.id ? updatedVoice : current);
    setReviewingVoice((current) => current?.id === updatedVoice.id ? updatedVoice : current);
    onVoiceCreated(updatedVoice);
  }, [onVoiceCreated]);

  /* ====================== 试听生成与播放 ====================== */
  const handleAuditionVoice = useCallback(async (vItem: VoiceCatalogItem) => {
    if (auditionInFlight.current || operationBusy || muteState !== "ready") return;
    auditionInFlight.current = true;
    const abort = new AbortController(); auditionController.current = abort;
    setAuditioningVoiceId(vItem.id);
    try {
      const blob = await voiceService.speech({
        model: SPEECHRAIL_TTS_MODEL,
        input: `你好，我是${vItem.name}，正在为你进行实时试听播放。`,
        voice: vItem.id,
        response_format: "wav",
      }, abort.signal);
      await playAudioBlob(blob, abort.signal);
    } catch (err) {
      if (!isMountedRef.current || abort.signal.aborted) return;
      const msg = err instanceof Error ? err.message : "试听失败";
      showToast(msg, "error");
    } finally {
      auditionInFlight.current = false;
      if (isMountedRef.current) setAuditioningVoiceId(null);
    }
  }, [operationBusy, muteState]);

  const filteredVoices = availableVoices.filter((v) => {
    if (filterMode === "all") return true;
    return resolveVoiceMode(v) === filterMode;
  });

  return (
    <div
      className="voice-studio-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="voice-studio-modal-title"
      onClick={requestClose}
    >
      <div className="voice-studio-dialog" ref={dialogRef} tabIndex={-1} onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        {closeRequested && <div className="voice-exit-confirm" role="group" aria-label="关闭工坊确认">
          <p>还有未提交的录音、已修改描述或待确认注册。关闭会丢弃本地草稿；服务端已经保存的音色不会删除，停止等待也不代表撤销注册。</p>
          <button ref={continueEditing} type="button" className="btn-design-preview" onClick={() => setCloseRequested(false)}>继续编辑</button>
          <button type="button" className="btn-design-preview" onClick={onClose}>关闭并丢弃本地草稿</button>
        </div>}
        <div className="voice-studio-header">
          <div className="voice-studio-header-titles">
            <div className="voice-studio-badge">
              <span className="studio-pulse-dot" />
              <span>VOICE ATELIER · 声音工坊</span>
            </div>
            <h2 id="voice-studio-modal-title" className="voice-studio-title">
              创建声音 · 检查效果 · 确认使用
            </h2>
          </div>
          <button
            type="button"
            className="voice-studio-close-btn"
            onClick={requestClose}
            disabled={operationBusy}
            aria-label="关闭声音工坊"
          >
            ✕
          </button>
        </div>

        {/* Main Body: 两栏布局 */}
        <div className="voice-studio-body">
          {/* 左栏：音色档案资产库 (Voice Deck) */}
          <aside className="voice-deck-sidebar" inert={closeRequested}>
            <div className="voice-deck-header">
              {selectionError && <p role="alert" className="forge-error-banner">{selectionError}</p>}
              <div className="voice-deck-title-line">
                <span className="deck-title">音色档案库</span>
                <span className="deck-count">{filteredVoices.length} 个音色</span>
              </div>
              <div className="voice-deck-filters">
                <button
                  type="button"
                  className={`deck-filter-chip ${filterMode === "all" ? "active" : ""}`}
                  onClick={() => setFilterMode("all")}
                >
                  全部
                </button>
                <button
                  type="button"
                  className={`deck-filter-chip ${filterMode === "system" ? "active" : ""}`}
                  onClick={() => setFilterMode("system")}
                >
                  🏛️ 官方
                </button>
                <button
                  type="button"
                  className={`deck-filter-chip ${filterMode === "clone" ? "active" : ""}`}
                  onClick={() => setFilterMode("clone")}
                >
                  🎙️ 克隆
                </button>
                <button
                  type="button"
                  className={`deck-filter-chip ${filterMode === "instruction" ? "active" : ""}`}
                  onClick={() => setFilterMode("instruction")}
                >
                  ✨ 设计
                </button>
              </div>
            </div>

            {onRefresh && <div className="voice-deck-refresh">
              <button type="button" className="btn-design-preview" disabled={operationBusy}
                onClick={async () => {
                  if (refreshInFlight.current) return;
                  refreshInFlight.current = true; setRefreshing(true); setRefreshError("");
                  try { await onRefresh(); }
                  catch { if (isMountedRef.current) setRefreshError("刷新未完成，保留原列表。请检查 SpeechRail 后重试。"); }
                  finally { refreshInFlight.current = false; if (isMountedRef.current) setRefreshing(false); }
                }}>{refreshing ? "正在刷新…" : "刷新音色与能力"}</button>
              {refreshError && <p className="forge-error-banner" role="alert">{refreshError}</p>}
            </div>}
            <div className="voice-deck-list" role="list">
              {filteredVoices.length === 0 && <p className="voice-flow-help" role="status">这个分类还没有音色。可以切换“全部”，或在右侧创建。</p>}
              {filteredVoices.map((item) => {
                const isSelected = item.id === currentVoiceId;
                const vMode = resolveVoiceMode(item);
                const modeMeta = VOICE_MODE_META[vMode];
                const isAuditioning = auditioningVoiceId === item.id;

                return (
                  <div
                    key={item.id}
                    className={`voice-deck-card ${isSelected ? "selected" : ""}`}
                    role="listitem"
                  >
                    <div className="deck-card-top">
                      <div className="deck-card-name-group">
                        <span className="deck-mode-icon">{modeMeta.icon}</span>
                        <span className="deck-card-name" title={item.name}>
                          {item.name}
                        </span>
                      </div>
                      <div className="deck-card-badges">
                        {isSelected && <span className="deck-active-tag">当前生效</span>}
                        <span className={`deck-type-badge type-${vMode}`}>{modeMeta.badge}</span>
                        {item.mode === "clone" && (
                          <span className={`deck-quality-tag quality-${synthesisQuality(item.quality)?.status ?? "unevaluated"}`}>
                            {hasAcceptedSynthesis(item)
                              ? "输出已检查"
                              : synthesisQuality(item.quality)?.status === "warn"
                                ? "存在风险"
                                : synthesisQuality(item.quality)?.status === "reject"
                                  ? "不建议使用"
                                  : "输出待检查"}
                          </span>
                        )}
                      </div>
                    </div>

                    <p className="deck-card-desc">
                      {item.instruction || (item.ref_text ? `参考文案: ${item.ref_text}` : "本地语音合成预设")}
                    </p>

                    <div className="deck-card-actions">
                      <button
                        type="button"
                        className={`btn-deck-audition ${isAuditioning ? "playing" : ""}`}
                        onClick={() => void handleAuditionVoice(item)}
                        disabled={operationBusy || muteState !== "ready"}
                        title="播放即时试听"
                      >
                        <SoundWaveAnimatedIcon size={12} isPlaying={isAuditioning} />
                        <span>{isAuditioning ? "试听中" : "试听"}</span>
                      </button>

                      {!isSelected && item.mode !== "clone" ? (
                        <button
                          type="button"
                          className="btn-deck-apply"
                          disabled={operationBusy}
                          title="使用此音色"
                          onClick={() => void selectVoice(item.id)}
                        >
                          {selectingVoiceId === item.id ? "正在确认启用…" : "使用此音色"}
                        </button>
                      ) : isSelected ? (
                        <span className="deck-applied-indicator">✓ 已激活</span>
                      ) : null}

                      {item.mode === "clone" && <button type="button" className="btn-deck-review"
                        disabled={operationBusy} onClick={() => setReviewingVoice(item)}>检查与试听</button>}
                      {!item.is_system && (
                        <button
                          type="button"
                          className="btn-deck-delete"
                          disabled={operationBusy}
                          onClick={() => {
                            setDeleteTargetVoice(item);
                          }}
                          title="删除此音色资产"
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {auditioningVoiceId && <button type="button" className="btn-design-preview" onClick={() => auditionController.current?.abort()}>停止试听</button>}
            {/* 自定义音色删除确认弹窗 */}
            {deleteTargetVoice && (
              <VoiceDeleteModal
                isOpen={true}
                voiceName={deleteTargetVoice.name}
                isCurrentActive={deleteTargetVoice.id === currentVoiceId}
                onClose={() => setDeleteTargetVoice(null)}
                onConfirm={async () => {
                  const id = deleteTargetVoice.id;
                  await onVoiceDeleted(id);
                  if (!isMountedRef.current) return;
                  setDeletedVoiceId(id);
                  setReviewingVoice((item) => item?.id === id ? null : item);
                  if (lastClonedVoice?.id === id) handleResetRecording();
                }}
              />
            )}
          </aside>

          {/* 右栏：声学创设舱 (Voice Forge) */}
          <main className="voice-forge-panel" inert={deleteTargetVoice !== null || closeRequested}>
            {muteState !== "ready" && <div className="forge-error-banner" role="status">
              <p>{muteState === "preparing" ? "正在等待助手静音确认，确认前不会录音或播放声音。可以先编辑描述，或关闭工坊。" : "助手静音未获确认，已暂停录音与试听。请恢复控制连接后重试。"}</p>
              {muteState === "error" && <button type="button" onClick={() => setMuteRetry((value) => value + 1)}>重试静音准备</button>}
            </div>}

            {/* Forge Tabs */}
            <div className="voice-forge-tabs">
              {canClone && (
                <button
                  type="button"
                  className={`forge-tab-btn ${selectedTab === "clone" ? "active" : ""}`}
                  disabled={operationBusy}
                  onClick={() => { setActiveTab("clone"); setReviewingVoice(null); }}
                >
                  <span className="tab-icon">🎙️</span>
                  <div className="tab-text-wrap">
                    <span className="tab-title">克隆声音 · 参考录音</span>
                    <span className="tab-desc">录制自然声音 · 核对实际朗读内容</span>
                  </div>
                </button>
              )}
              {canDesign && (
                <button
                  type="button"
                  className={`forge-tab-btn ${selectedTab === "design" ? "active" : ""}`}
                  disabled={operationBusy}
                  onClick={() => { setActiveTab("design"); setReviewingVoice(null); }}
                >
                  <span className="tab-icon">✨</span>
                  <div className="tab-text-wrap">
                    <span className="tab-title">描述声音 · 自然语言设计</span>
                    <span className="tab-desc">选择示例再修改 · 创建可复用音色</span>
                  </div>
                </button>
              )}
            </div>

            {!canClone && !canDesign && (
              <div className="forge-error-banner" role="alert">
                ⚠️ 当前 TTS 模型不支持声音创设，请切换至 Quality 配置（设计与 Base 克隆能力）。
              </div>
            )}

            {/* Tab 1: 声音克隆 (Voice Clone) 创设流程 */}
            {reviewingVoice && <div className="design-forge-container">
              <button type="button" className="btn-design-preview" disabled={reviewBusy} onClick={() => setReviewingVoice(null)}>返回创建声音</button>
              <VoiceCandidateReview key={reviewingVoice.id} voice={reviewingVoice} onUpdated={handleCandidateUpdated}
                disabled={!canClone || muteState !== "ready" || refreshing || auditioningVoiceId !== null}
                onSelect={selectVoice} onBusyChange={setReviewBusy} />
            </div>}
            {!reviewingVoice && selectedTab === "clone" && canClone && (
              <div className="clone-forge-container" inert={auditioningVoiceId !== null || designBusy || selectingVoiceId !== null}>
                {/* 步骤 1: 提词引导与声学校准 */}
                <section className="clone-step-section">
                  <div className="clone-step-header">
                    <span className="step-badge">STEP 1</span>
                    <span className="step-title">提词器引导朗读</span>
                    <div className="step-mic-indicator">
                      <span className={`mic-dot status-${micLevelStatus}`} />
                      <span className="mic-text">
                        {cloneStage !== "recording" ? "尚未采集" : micLevelStatus === "good" ? "输入电平正常" : micLevelStatus === "quiet" ? "音量偏轻" : "输入电平偏高"}
                      </span>
                    </div>
                  </div>

                  <div className="teleprompter-card">
                    <div className="teleprompter-meta">
                      <span className="prompt-category-tag">{(recordingPrompt ?? activePrompt).title}</span>
                      <button
                        type="button"
                        className="btn-switch-prompt"
                        onClick={() => setPromptIndex((idx) => (idx + 1) % prompts.length)}
                        disabled={cloneStage !== "ready" || isStartingRecording}
                      >
                        🔄 换一段文案 ({promptIndex + 1}/{prompts.length})
                      </button>
                    </div>
                    <blockquote className="teleprompter-script">
                      “{(recordingPrompt ?? activePrompt).script}”
                    </blockquote>
                    <p className="teleprompter-tips">自然朗读即可，不需要播音腔；读错、漏读时可在下一步修正参考文本。</p>
                  </div>
                </section>

                {/* 步骤 2: 录音与实时波形 */}
                <section className="clone-step-section">
                  <div className="clone-step-header">
                    <span className="step-badge">STEP 2</span>
                    <span className="step-title">麦克风声学采集</span>
                    {cloneStage === "recording" && (
                      <span className="recording-timer">
                        ⏱️ {String(Math.floor(recordingSeconds / 60)).padStart(2, "0")}:
                        {String(recordingSeconds % 60).padStart(2, "0")} / 建议 5~15 秒
                      </span>
                    )}
                  </div>

                  <div className="waveform-box">
                    <canvas
                      ref={canvasRef}
                      width={640}
                      height={96}
                      className="recording-canvas"
                    />

                    {cloneStage === "ready" && (
                      <div className="waveform-idle-overlay">
                        <p>靠近麦克风约 15~20cm，深呼吸，点击下方按钮开始朗读</p>
                      </div>
                    )}
                  </div>

                  <div className="recording-controls">
                    {cloneStage === "ready" && (
                      <button
                        type="button"
                        className="btn-record-primary"
                        onClick={() => void startRecording()}
                        disabled={isStartingRecording || muteState !== "ready"}
                        aria-busy={isStartingRecording}
                      >
                        <span className="record-dot" />
                        <span>{isStartingRecording ? "正在准备麦克风..." : "开始录音朗读"}</span>
                      </button>
                    )}

                    {cloneStage === "recording" && <button type="button" className="btn-design-preview" onClick={handleResetRecording}>取消这次录音</button>}
                    {isStartingRecording && <button type="button" className="btn-design-preview" onClick={() => {
                      recordingAttemptRef.current += 1; recordingStartInFlightRef.current = false;
                      setIsStartingRecording(false); setRecordingPrompt(null); cleanupRecording();
                      setCloneError("已取消麦克风等待，之后返回的权限结果不会启动录音。");
                    }}>取消麦克风等待</button>}
                    {cloneStage === "recording" && (
                      <button
                        type="button"
                        className="btn-record-stop"
                        onClick={stopRecording}
                      >
                        <span className="stop-square" />
                        <span>完成录制 ({recordingSeconds}s)</span>
                      </button>
                    )}

                    {(cloneStage === "recorded" || cloneStage === "submitting" || cloneStage === "success") && (
                      <div className="recorded-playback-bar">
                        {recordedAudioUrl && (
                          <audio
                            src={recordedAudioUrl}
                            ref={rawAudioRef}
                            onPlay={() => {
                              if (cloneStage === "submitting" || reviewBusy || designBusy || auditioningVoiceId || muteState !== "ready") rawAudioRef.current?.pause();
                              else setRawPlaying(true);
                            }}
                            onPause={() => setRawPlaying(false)} onEnded={() => setRawPlaying(false)}
                            onError={() => { setRawPlaying(false); setCloneError("浏览器无法回放这段录音，可以重录或交由服务端预检；不要将回放失败视为音质通过。"); }}
                            controls
                            className="recorded-audio-player"
                            aria-label="回放刚才录制的音频"
                          />
                        )}
                        <button
                          type="button"
                          className="btn-rerecord"
                          onClick={handleResetRecording}
                          disabled={cloneStage === "submitting" || reviewBusy || cloneUncertain}
                        >
                          🔄 不满意，重新录制
                        </button>
                      </div>
                    )}
                  </div>
                </section>

                {/* 步骤 3: 命名与提交克隆 */}
                {(cloneStage === "recorded" || cloneStage === "submitting" || cloneStage === "success") && (
                  <section className="clone-step-section fade-in">
                    <div className="clone-step-header">
                      <span className="step-badge">STEP 3</span>
                      <span className="step-title">核对参考文本并保存</span>
                    </div>

                    <div className="forge-field">
                      <label htmlFor="clone-reference-text" className="forge-label">音频中实际说出的内容</label>
                      <textarea id="clone-reference-text" className="forge-textarea voice-reference-editor" rows={3}
                        maxLength={2000} value={recordedReferenceText}
                        disabled={cloneStage === "submitting" || cloneStage === "success" || cloneUncertain}
                        onChange={(event) => { setRecordedReferenceText(event.target.value); setServerValidation(undefined); }} />
                      <p className="voice-flow-help">请删除未读出的句子，修正漏字和改读内容。参考文本必须与这次录音对应，不能拿另一段文案代替。</p>
                      <p className="voice-capture-details">{captureInfo}。上传保持原始电平；规范化由 SpeechRail 完成，不保证自动消除背景噪声。</p>
                    </div>
                    <div className="clone-naming-row">
                      <input
                        type="text"
                        className="clone-name-input"
                        placeholder="给你的克隆音色起个名字（如：我的数字分身、清晨男声）..."
                        value={cloneName}
                        maxLength={24}
                        onChange={(e) => {
                          setCloneName(e.target.value);
                          if (cloneError) setCloneError("");
                        }}
                        disabled={cloneStage === "submitting" || cloneStage === "success" || cloneUncertain}
                      />

                      {cloneStage !== "success" ? (
                        <button
                          type="button"
                          className="btn-submit-clone"
                          onClick={() => void handleSubmitClone()}
                          disabled={cloneStage === "submitting" || !cloneName.trim() || !recordedReferenceText.trim() || muteState !== "ready" || rawPlaying}
                        >
                          {cloneStage === "submitting" ? (
                            <span>正在核验参考并保存…</span>
                          ) : (
                            <span>{cloneUncertain ? "使用同一 ID 重试" : "核验参考并保存音色"}</span>
                          )}
                        </button>
                      ) : <span className="clone-candidate-hint">已保存，输出待检查</span>}
                    </div>

                    {cloneStage === "submitting" && <button type="button" className="btn-design-preview" onClick={() => cloneController.current?.abort()}>停止等待</button>}
                    {cloneUncertain && cloneAttemptRef.current && <div className="voice-registration-recovery">
                      <p>待确认注册 ID：<code>{cloneAttemptRef.current.id}</code></p>
                      <button type="button" className="btn-design-preview" disabled={operationBusy} onClick={() => void reconcileClone()}>检查保存结果</button>
                      <button type="button" className="btn-design-preview" disabled={operationBusy} onClick={() => setAbandonClone(true)}>放弃等待并重新录音</button>
                      {abandonClone && <div role="group" aria-label="确认重新录音">
                        <p>上次注册可能已保存。重新录音会使用新 ID，不会删除旧音色；请先检查档案库，避免重复创建。</p>
                        <button type="button" className="btn-design-preview" disabled={operationBusy} onClick={() => { setAbandonClone(false); handleResetRecording(); }}>确认重新录音</button>
                        <button type="button" className="btn-design-preview" onClick={() => setAbandonClone(false)}>保留待确认录音</button>
                      </div>}
                    </div>}
                    {localQuality && (
                      <VoiceQualityCard
                        title="录音预检"
                        localResult={localQuality}
                        pending={cloneStage === "submitting" || reviewBusy || cloneUncertain}
                        onRerecord={handleResetRecording}
                      />
                    )}

                    {(serverValidation || serverValidationPending) && (
                      <VoiceQualityCard
                        title="SpeechRail 参考音频验收"
                        scope="reference"
                        report={serverValidation}
                        pending={serverValidationPending || cloneStage === "submitting" || reviewBusy || cloneUncertain}
                        onRerecord={handleResetRecording}
                      />
                    )}

                    {cloneStage === "success" && lastClonedVoice && (
                      <VoiceCandidateReview key={lastClonedVoice.id} voice={lastClonedVoice}
                        disabled={!canClone || muteState !== "ready" || refreshing || auditioningVoiceId !== null}
                        onUpdated={handleCandidateUpdated} onSelect={selectVoice} onBusyChange={setReviewBusy} />
                    )}
                  </section>
                )}

                {cloneError && (
                  <div className="forge-error-banner" role="alert">
                    ⚠️ {cloneError}
                  </div>
                )}
              </div>
            )}

            {(
              <div hidden={!canDesign || reviewingVoice !== null || selectedTab !== "design"} inert={auditioningVoiceId !== null || selectingVoiceId !== null}><VoiceDesignPanel canRegister={muteState === "ready" && modelCapabilities?.supports_instruction === true && modelCapabilities?.supports_clone === true}
                canPreview={canPreview && muteState === "ready"} deletedVoiceId={deletedVoiceId}
                externalBusy={operationBusy && !designBusy} onDirtyChange={setDesignDirty} onCreated={onVoiceCreated} onSelect={selectVoice} onBusyChange={setDesignBusy} /></div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
