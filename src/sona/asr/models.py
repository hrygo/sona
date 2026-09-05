"""ASR 端口输出给应用的中立结果模型。

这些 dataclass 与具体 SpeechRail wire event 和会议实体都无关；会议实体
只在 ``sona.meeting.asr_mapping`` 的唯一 mapper 中生成。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ASRAttributionUnitSpan", "ASRCompletedItem", "ASRSegment", "ASRWindow"]


@dataclass(frozen=True, slots=True)
class ASRSegment:
    """一段已确认的 ASR 转录，时间与说话人键已由适配器投影到会话时间轴。"""

    order: int
    source_epoch: int
    speaker_key: str
    start_ms: int
    end_ms: int
    text: str
    translation: str | None = None
    detected_language: str | None = None
    # SPK-E2E-1 扩展模式：source session 内的不可变归属单元标识与时间质量。
    source_uid: str | None = None
    timing_quality: str | None = None

    def __post_init__(self) -> None:
        if self.order < 0:
            raise ValueError("order 必须非负")
        if self.source_epoch < 0:
            raise ValueError("source_epoch 必须非负")
        if self.start_ms < 0:
            raise ValueError("start_ms 必须非负")
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms 必须大于等于 start_ms")
        if not self.speaker_key.strip():
            raise ValueError("speaker_key 不能为空")
        if not self.text.strip():
            raise ValueError("text 不能为空")
        if self.timing_quality is not None and self.timing_quality not in (
            "aligned",
            "unavailable",
        ):
            raise ValueError("timing_quality 只允许 aligned/unavailable")


@dataclass(frozen=True, slots=True)
class ASRAttributionUnitSpan:
    """扩展模式 completed 内的一个归属单元（session 样本域）。"""

    segment_uid: str
    text_start: int
    text_end: int
    audio_start_sample: int
    audio_end_sample: int
    timing_quality: str


@dataclass(frozen=True, slots=True)
class ASRCompletedItem:
    """扩展模式一次 commit 的固定正文事实（未经会议时钟换算）。"""

    item_id: str
    event_id: str
    sequence: int
    audio_start_sample: int
    audio_end_sample: int
    canonical_text: str
    units: tuple[ASRAttributionUnitSpan, ...] = ()


@dataclass(frozen=True, slots=True)
class ASRWindow:
    """ASR 端口当前 confirmed 窗口与易失 partial 文本。"""

    source_epoch: int
    partial: str = ""
    partial_speaker_key: str | None = None
    segments: tuple[ASRSegment, ...] = ()
    speaker_remap: tuple[tuple[str, str], ...] = ()
    # SPK-E2E-1 扩展模式：产生本窗口的 SpeechRail session id（重放/身份用）。
    source_session_id: str | None = None
    completed_items: tuple[ASRCompletedItem, ...] = ()
    # 本窗口 source epoch 的会议时间起点（毫秒）；completed 换算 meeting 样本用。
    offset_ms: int = 0

    def __post_init__(self) -> None:
        if self.source_epoch < 0:
            raise ValueError("source_epoch 必须非负")
