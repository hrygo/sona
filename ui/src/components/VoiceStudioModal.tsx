import { useCallback, useEffect, useRef, useState } from "react";
import {
  SPEECHRAIL_TTS_MODEL,
  voiceService,
} from "../services/voiceService";
import { playAudioBlob } from "../utils/audioPlayback";
import {
  normalizeRecordingForClone,
  SilentRecordingError,
} from "../utils/audioNormalize";
import { showToast } from "./Toast";
import { SoundWaveAnimatedIcon } from "./Icons";
import {
  supportsVoiceCapability,
  type VoiceModelCapabilities,
} from "../contracts/voiceContract";
import {
  type VoiceCatalogItem,
  type VoiceMode,
  resolveVoiceMode,
  VOICE_MODE_META,
} from "./assistantPresentation";
import { VoiceDeleteModal } from "./VoiceDeleteModal";
import "./VoiceStudioModal.css";

export interface VoiceStudioModalProps {
  readonly currentVoiceId: string;
  readonly availableVoices: readonly VoiceCatalogItem[];
  readonly modelCapabilities?: VoiceModelCapabilities;
  readonly onSelectVoice: (voiceId: string) => void;
  readonly onVoiceCreated: (newVoice: VoiceCatalogItem) => void;
  readonly onVoiceDeleted: (voiceId: string) => void;
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

interface InspirationPrompt {
  readonly label: string;
  readonly name: string;
  readonly instruction: string;
}

const DESIGN_INSPIRATIONS: readonly InspirationPrompt[] = [
  {
    label: "🌸 温柔知性",
    name: "知性女声",
    instruction: "温柔轻快、语调柔和的年轻女声，吐字清晰亲和，富有同理心与治愈感。",
  },
  {
    label: "💼 干练职场",
    name: "职场播报",
    instruction: "咬字精准、节奏从容稳健的成熟女性声音，适合新闻播报、会议总结与专业技术讲解。",
  },
  {
    label: "🍵 磁性男声",
    name: "磁性电台",
    instruction: "磁性温润、低沉浑厚的青年男声，语调沉静从容，适合深度交流与夜间电台陪伴。",
  },
  {
    label: "⚡ 元气少年",
    name: "元气阳光",
    instruction: "清脆明快、朝气蓬勃的少年音色，充满热情活力，语速轻快流畅，适合趣味互动与日常闲聊。",
  },
  {
    label: "📜 京味评书",
    name: "说书先生",
    instruction: "经典北京评书艺人口吻，咬字顿挫有力，幽默诙谐，带有传统说书人的腔调感与感染力。",
  },
];

export function VoiceStudioModal({
  currentVoiceId,
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

  /* ====================== 2. 灵感设计 (Voice Design) 状态 ====================== */
  const [designName, setDesignName] = useState("");
  const [designInstruction, setDesignInstruction] = useState("");
  const [designPreviewText, setDesignPreviewText] = useState("你好呀，我是你刚刚设计的专属音色，很高兴与你实时对话。");
  const [isPlayingDesignPreview, setIsPlayingDesignPreview] = useState(false);
  const [isDesignSubmitting, setIsDesignSubmitting] = useState(false);
  const [designError, setDesignError] = useState("");

  // 打开声音工坊时立即静音麦克风，退出声音工坊时恢复
  useEffect(() => {
    let cancelled = false;
    assistantMuteActiveRef.current = onStartRecordingVoice !== undefined;
    if (onStartRecordingVoice) {
      Promise.resolve(onStartRecordingVoice()).catch(() => {
        if (!cancelled) {
          showToast("语音助手麦克风静音失败，请检查控制连接", "error");
        }
      });
    }
    return () => {
      cancelled = true;
    };
  }, [onStartRecordingVoice]);

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
      if (e.key === "Escape" && cloneStage !== "recording" && cloneStage !== "submitting") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, cloneStage]);

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
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      try {
        mediaRecorderRef.current.stop();
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
    return () => {
      isMountedRef.current = false;
      recordingAttemptRef.current += 1;
      recordingStartInFlightRef.current = false;
      cleanupRecording();
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
    if (recordingStartInFlightRef.current) return;
    recordingStartInFlightRef.current = true;
    const attempt = recordingAttemptRef.current + 1;
    recordingAttemptRef.current = attempt;
    setIsStartingRecording(true);
    setCloneError("");
    try {
      cleanupRecording();

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // 声音工坊打开期间语音助手已被租约暂停，无自播回声风险；
          // 浏览器 AEC/AGC 的自适应增益是"录音前响后轻"的根因，克隆采集必须关闭以保真音色电平。
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
      const mimeType = (typeof MediaRecorder !== "undefined" && typeof MediaRecorder.isTypeSupported === "function" && MediaRecorder.isTypeSupported("audio/webm;codecs=opus"))
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const fullBlob = new Blob(audioChunksRef.current, { type: mimeType });
        cleanupRecording();
        if (!isMountedRef.current) return;
        setRecordedBlob(fullBlob);
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

      timerRef.current = window.setInterval(() => {
        setRecordingSeconds((sec) => {
          if (sec >= 30) {
            stopRecording();
            return 30;
          }
          return sec + 1;
        });
      }, 1000);

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
      const msg = err instanceof Error ? err.message : "无法开启麦克风，请检查浏览器权限";
      setCloneError(`麦克风采集失败: ${msg}`);
      setCloneStage("ready");
    } finally {
      if (recordingAttemptRef.current === attempt) {
        recordingStartInFlightRef.current = false;
        if (isMountedRef.current) setIsStartingRecording(false);
      }
    }
  }, [cleanupRecording, cloneName, onStartRecordingVoice, releaseAssistantMute]);

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
    cleanupRecording();
    if (recordedAudioUrl) {
      URL.revokeObjectURL(recordedAudioUrl);
      setRecordedAudioUrl(null);
    }
    setRecordedBlob(null);
    setRecordingSeconds(0);
    setCloneStage("ready");
    setCloneError("");
  }, [cleanupRecording, recordedAudioUrl]);

  /* ====================== 提交克隆至 SpeechRail ====================== */
  const handleSubmitClone = useCallback(async () => {
    if (!canClone) {
      setCloneError("当前 TTS 模型不支持声音克隆，请切换至 Quality / VoiceDesign 配置");
      return;
    }
    if (!recordedBlob) {
      setCloneError("请先完成一段语音录制");
      return;
    }
    if (recordingSeconds < 3) {
      setCloneError("录音时长偏短（建议至少 5~15 秒），请重新录制");
      return;
    }
    if (!cloneName.trim()) {
      setCloneError("请输入音色名称");
      return;
    }
    if (!activePrompt.script.trim()) {
      setCloneError("参考文本缺失，请选择引导文案");
      return;
    }

    setCloneError("");
    setCloneStage("submitting");

    try {
      // 克隆的是音色而不是音量：提交前统一响度并转无损 WAV，
      // 标准化失败（除近静音外）降级提交原始录音，保证功能可用。
      let uploadBlob = recordedBlob;
      let uploadFilename = "recording.webm";
      try {
        const normalized = await normalizeRecordingForClone(recordedBlob);
        uploadBlob = normalized.blob;
        uploadFilename = "recording.wav";
      } catch (err) {
        if (err instanceof SilentRecordingError) {
          setCloneError(err.message);
          setCloneStage("recorded");
          showToast(err.message, "error");
          return;
        }
        showToast("录音响度标准化失败，将按原始录音提交", "info");
      }

      const formData = new FormData();
      formData.append("audio", uploadBlob, uploadFilename);
      formData.append("ref_text", activePrompt.script.trim());
      formData.append("name", cloneName.trim());

      const createdVoice = await voiceService.clone(formData);
      setLastClonedVoice(createdVoice);
      setCloneStage("success");
      onVoiceCreated(createdVoice);
      showToast(`🎉 音色「${createdVoice.name}」已克隆入库！`, "success");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "提交音色克隆失败，请检查服务连接";
      setCloneError(msg);
      setCloneStage("recorded");
      showToast(msg, "error");
    }
  }, [canClone, recordedBlob, recordingSeconds, cloneName, activePrompt.script, onVoiceCreated]);

  /* ====================== 试听生成与播放 ====================== */
  const handleAuditionVoice = useCallback(async (vItem: VoiceCatalogItem) => {
    if (auditioningVoiceId) return;
    setAuditioningVoiceId(vItem.id);
    try {
      const blob = await voiceService.speech({
        model: SPEECHRAIL_TTS_MODEL,
        input: `你好，我是${vItem.name}，正在为你进行实时试听播放。`,
        voice: vItem.id,
        response_format: "wav",
      });
      await playAudioBlob(blob);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "试听失败";
      showToast(msg, "error");
    } finally {
      setAuditioningVoiceId(null);
    }
  }, [auditioningVoiceId]);

  /* ====================== 灵感设计提交 ====================== */
  const handleSubmitDesign = useCallback(async () => {
    if (!canDesign) {
      setDesignError("当前 TTS 模型不支持自然语言设计，请切换至 Quality / VoiceDesign 配置");
      return;
    }
    const trimmedName = designName.trim();
    const trimmedInstruction = designInstruction.trim();
    if (!trimmedName) {
      setDesignError("请输入音色名称");
      return;
    }
    if (!trimmedInstruction) {
      setDesignError("请输入音色特征描述");
      return;
    }

    setDesignError("");
    setIsDesignSubmitting(true);

    try {
      const createdVoice = await voiceService.create({
        name: trimmedName,
        instruction: trimmedInstruction,
      });
      showToast(`专属设计音色「${createdVoice.name}」已创建`, "success");
      onVoiceCreated(createdVoice);
      onSelectVoice(createdVoice.id);
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "创建设计音色失败，请重试";
      setDesignError(msg);
      showToast(msg, "error");
    } finally {
      setIsDesignSubmitting(false);
    }
  }, [canDesign, designName, designInstruction, onVoiceCreated, onSelectVoice, onClose]);

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
      onClick={onClose}
    >
      <div className="voice-studio-dialog" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="voice-studio-header">
          <div className="voice-studio-header-titles">
            <div className="voice-studio-badge">
              <span className="studio-pulse-dot" />
              <span>VOICE ATELIER · 声音工坊</span>
            </div>
            <h2 id="voice-studio-modal-title" className="voice-studio-title">
              全本地专属声音创设与档案库
            </h2>
          </div>
          <button
            type="button"
            className="voice-studio-close-btn"
            onClick={onClose}
            aria-label="关闭声音工坊"
          >
            ✕
          </button>
        </div>

        {/* Main Body: 两栏布局 */}
        <div className="voice-studio-body">
          {/* 左栏：音色档案资产库 (Voice Deck) */}
          <aside className="voice-deck-sidebar">
            <div className="voice-deck-header">
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

            <div className="voice-deck-list" role="list">
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
                      </div>
                    </div>

                    <p className="deck-card-desc">
                      {item.instruction || (item.ref_text ? `参考文案: ${item.ref_text}` : "24kHz 高保真立体声学模型输出")}
                    </p>

                    <div className="deck-card-actions">
                      <button
                        type="button"
                        className={`btn-deck-audition ${isAuditioning ? "playing" : ""}`}
                        onClick={() => void handleAuditionVoice(item)}
                        disabled={isAuditioning}
                        title="播放即时试听"
                      >
                        <SoundWaveAnimatedIcon size={12} isPlaying={isAuditioning} />
                        <span>{isAuditioning ? "试听中" : "试听"}</span>
                      </button>

                      {!isSelected ? (
                        <button
                          type="button"
                          className="btn-deck-apply"
                          onClick={() => {
                            onSelectVoice(item.id);
                            showToast(`已切换助理播报音色为「${item.name}」`, "info");
                          }}
                        >
                          使用此音色
                        </button>
                      ) : (
                        <span className="deck-applied-indicator">✓ 已激活</span>
                      )}

                      {!item.is_system && (
                        <button
                          type="button"
                          className="btn-deck-delete"
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

            {/* 自定义音色删除确认弹窗 */}
            {deleteTargetVoice && (
              <VoiceDeleteModal
                isOpen={true}
                voiceName={deleteTargetVoice.name}
                isCurrentActive={deleteTargetVoice.id === currentVoiceId}
                onClose={() => setDeleteTargetVoice(null)}
                onConfirm={() => {
                  onVoiceDeleted(deleteTargetVoice.id);
                  setDeleteTargetVoice(null);
                }}
              />
            )}
          </aside>

          {/* 右栏：声学创设舱 (Voice Forge) */}
          <main className="voice-forge-panel">
            {/* Forge Tabs */}
            <div className="voice-forge-tabs">
              {canClone && (
                <button
                  type="button"
                  className={`forge-tab-btn ${selectedTab === "clone" ? "active" : ""}`}
                  onClick={() => setActiveTab("clone")}
                >
                  <span className="tab-icon">🎙️</span>
                  <div className="tab-text-wrap">
                    <span className="tab-title">声音克隆 (ICL 录音克隆)</span>
                    <span className="tab-desc">朗读精选引导文案 · 提取声学特征分身</span>
                  </div>
                </button>
              )}
              {canDesign && (
                <button
                  type="button"
                  className={`forge-tab-btn ${selectedTab === "design" ? "active" : ""}`}
                  onClick={() => setActiveTab("design")}
                >
                  <span className="tab-icon">✨</span>
                  <div className="tab-text-wrap">
                    <span className="tab-title">自然语言设计 (Prompt 定制)</span>
                    <span className="tab-desc">使用自然语言描述 · 塑造全新虚拟音色</span>
                  </div>
                </button>
              )}
            </div>

            {!canClone && !canDesign && (
              <div className="forge-error-banner" role="alert">
                ⚠️ 当前 TTS 模型不支持声音创设，请切换至 Quality / VoiceDesign 配置。
              </div>
            )}

            {/* Tab 1: 声音克隆 (Voice Clone) 创设流程 */}
            {selectedTab === "clone" && canClone && (
              <div className="clone-forge-container">
                {/* 步骤 1: 提词引导与声学校准 */}
                <section className="clone-step-section">
                  <div className="clone-step-header">
                    <span className="step-badge">STEP 1</span>
                    <span className="step-title">提词器引导朗读</span>
                    <div className="step-mic-indicator">
                      <span className={`mic-dot status-${micLevelStatus}`} />
                      <span className="mic-text">
                        {micLevelStatus === "good" ? "声学环境良好" : micLevelStatus === "quiet" ? "音量偏轻" : "输入电平过载"}
                      </span>
                    </div>
                  </div>

                  <div className="teleprompter-card">
                    <div className="teleprompter-meta">
                      <span className="prompt-category-tag">{activePrompt.title}</span>
                      <button
                        type="button"
                        className="btn-switch-prompt"
                        onClick={() => setPromptIndex((idx) => (idx + 1) % prompts.length)}
                        disabled={cloneStage === "recording"}
                      >
                        🔄 换一段文案 ({promptIndex + 1}/{prompts.length})
                      </button>
                    </div>
                    <blockquote className="teleprompter-script">
                      “{activePrompt.script}”
                    </blockquote>
                    <p className="teleprompter-tips">💡 发音要领：{activePrompt.tips}</p>
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
                        disabled={isStartingRecording}
                        aria-busy={isStartingRecording}
                      >
                        <span className="record-dot" />
                        <span>{isStartingRecording ? "正在准备麦克风..." : "开始录音朗读"}</span>
                      </button>
                    )}

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
                            controls
                            className="recorded-audio-player"
                            aria-label="回放刚才录制的音频"
                          />
                        )}
                        <button
                          type="button"
                          className="btn-rerecord"
                          onClick={handleResetRecording}
                          disabled={cloneStage === "submitting"}
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
                      <span className="step-title">命名并提交声学模型提取</span>
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
                        disabled={cloneStage === "submitting" || cloneStage === "success"}
                      />

                      {cloneStage !== "success" ? (
                        <button
                          type="button"
                          className="btn-submit-clone"
                          onClick={() => void handleSubmitClone()}
                          disabled={cloneStage === "submitting" || !cloneName.trim()}
                        >
                          {cloneStage === "submitting" ? (
                            <span>🚀 正在提交 SpeechRail 提取声学特征...</span>
                          ) : (
                            <span>🚀 提交克隆此声音</span>
                          )}
                        </button>
                      ) : (
                        <button
                          type="button"
                          className="btn-apply-success"
                          onClick={() => {
                            if (lastClonedVoice) {
                              onSelectVoice(lastClonedVoice.id);
                              showToast(`已成功将「${lastClonedVoice.name}」设为当前助理音色`, "success");
                              onClose();
                            }
                          }}
                        >
                          ✨ 设为当前助理音色并完成
                        </button>
                      )}
                    </div>

                    {cloneStage === "success" && lastClonedVoice && (
                      <div className="clone-success-box">
                        <div className="success-icon-banner">🎉 声学特征提取完成，专属声音已存入本地！</div>
                        <div className="success-audition-action">
                          <button
                            type="button"
                            className="btn-success-audition"
                            onClick={() => void handleAuditionVoice(lastClonedVoice)}
                          >
                            <SoundWaveAnimatedIcon size={14} isPlaying={auditioningVoiceId === lastClonedVoice.id} />
                            <span>试听专属克隆声音</span>
                          </button>
                          <span className="success-hint">点击立即用你的声纹合成测试语句</span>
                        </div>
                      </div>
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

            {/* Tab 2: 灵感设计 (Voice Design) */}
            {selectedTab === "design" && canDesign && (
              <div className="design-forge-container">
                <div className="forge-field">
                  <label htmlFor="design-name-input" className="forge-label">
                    音色名称 <span className="forge-required">*</span>
                  </label>
                  <input
                    id="design-name-input"
                    type="text"
                    className="forge-input"
                    placeholder="例如：极客少年、知性姐姐、温润导师..."
                    value={designName}
                    maxLength={24}
                    onChange={(e) => {
                      setDesignName(e.target.value);
                      if (designError) setDesignError("");
                    }}
                  />
                </div>

                <div className="forge-field">
                  <div className="forge-label-split">
                    <label htmlFor="design-instruction-input" className="forge-label">
                      音色提示词 Prompt <span className="forge-required">*</span>
                    </label>
                    <span className="forge-counter">{designInstruction.length} / 200</span>
                  </div>
                  <textarea
                    id="design-instruction-input"
                    className="forge-textarea"
                    placeholder="详细描述性别、年龄、音质、语速、发音特点与情绪风格。例如：温和清澈的年轻女声，语调柔和，富有同理心..."
                    rows={4}
                    maxLength={200}
                    value={designInstruction}
                    onChange={(e) => {
                      setDesignInstruction(e.target.value);
                      if (designError) setDesignError("");
                    }}
                  />
                </div>

                <div className="forge-field">
                  <span className="forge-sublabel">灵感胶囊预设 (点击一键套用)：</span>
                  <div className="design-inspirations-wrap">
                    {DESIGN_INSPIRATIONS.map((item) => (
                      <button
                        key={item.label}
                        type="button"
                        className="design-inspiration-chip"
                        onClick={() => {
                          setDesignName(item.name);
                          setDesignInstruction(item.instruction);
                          setDesignError("");
                        }}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="forge-field forge-preview-section">
                  <label htmlFor="design-preview-input" className="forge-sublabel">即时试听文本：</label>
                  <div className="design-preview-bar">
                    <input
                      id="design-preview-input"
                      type="text"
                      className="forge-input"
                      value={designPreviewText}
                      maxLength={80}
                      onChange={(e) => setDesignPreviewText(e.target.value)}
                    />
                    <button
                      type="button"
                      className="btn-design-preview"
                      onClick={async () => {
                        if (!designInstruction.trim()) {
                          setDesignError("请先输入音色特征描述");
                          return;
                        }
                        setIsPlayingDesignPreview(true);
                        try {
                          const blob = await voiceService.preview({
                            model: SPEECHRAIL_TTS_MODEL,
                            input: designPreviewText.trim() || "你好，很高兴与你对话。",
                            instruction: designInstruction.trim(),
                            response_format: "wav",
                          });
                          await playAudioBlob(blob);
                        } catch (err) {
                          const msg = err instanceof Error ? err.message : "试听失败";
                          setDesignError(msg);
                        } finally {
                          setIsPlayingDesignPreview(false);
                        }
                      }}
                      disabled={!canPreview || isPlayingDesignPreview || !designInstruction.trim()}
                    >
                      <SoundWaveAnimatedIcon size={13} isPlaying={isPlayingDesignPreview} />
                      <span>{isPlayingDesignPreview ? "生成播放中..." : "试听效果"}</span>
                    </button>
                  </div>
                  {!canPreview && (
                    <div className="forge-capability-hint" role="status">
                      当前模型不支持自然语言试听。
                    </div>
                  )}
                </div>

                {designError && (
                  <div className="forge-error-banner" role="alert">
                    ⚠️ {designError}
                  </div>
                )}

                <div className="design-actions-bar">
                  <button
                    type="button"
                    className="btn-save-design"
                    onClick={() => void handleSubmitDesign()}
                    disabled={!canDesign || isDesignSubmitting || !designName.trim() || !designInstruction.trim()}
                  >
                    {isDesignSubmitting ? "正在固化保存..." : "固化并设为助理声音"}
                  </button>
                </div>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
