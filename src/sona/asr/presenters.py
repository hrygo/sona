"""ASR 领域对象到既有外部展示协议的纯转换。"""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sona.asr.models import ASRSegment, ASRWindow
from sona.meeting.transcript_models import (
    DisplayBlock,
    SpeakerStatus,
    TimingQuality,
    TranscriptAttributionSpan,
    TranscriptItem,
)
from sona.meeting.transcript_projector import TranscriptPresentationProjector

UNKNOWN_SUBTITLE_SPEAKER = "未识别说话人"


def _subtitle_speaker(segment: ASRSegment) -> str:
    """把不透明 ASR speaker key 映射为 session/epoch 作用域的展示字符串。"""

    key = segment.speaker_key.strip()
    if key == "unknown":
        return UNKNOWN_SUBTITLE_SPEAKER
    if key.startswith("session:"):
        _, epoch, label = key.split(":", 2)
        return f"会话 {epoch} · {label}"
    if key.startswith("epoch:"):
        parts = key.split(":")
        if len(parts) >= 4:
            return f"会话 {parts[1]} · {parts[-1]}"
    return f"说话人 {key}"


def _legacy_timestamp(timestamp_ms: int) -> str:
    hours, remainder = divmod(timestamp_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def legacy_ready_payload() -> dict[str, Any]:
    """生成浏览器当前依赖的 PCM/full 模式握手。"""
    return {"type": "config", "useAudioWorklet": False, "mode": "full"}


def legacy_subtitle_payload(window: ASRWindow) -> dict[str, Any]:
    """生成前端当前消费的完整字幕快照。"""
    display_blocks = _subtitle_display_blocks(window)
    return {
        "type": "full_update",
        "buffer_transcription": window.partial,
        "lines": [
            {
                "speaker": _subtitle_speaker(segment),
                "text": segment.text,
                "start": _legacy_timestamp(segment.start_ms),
                "end": _legacy_timestamp(segment.end_ms),
                "translation": segment.translation,
                "detected_language": segment.detected_language,
            }
            for segment in window.segments
        ],
        "display_blocks": [
            block.model_dump(mode="json") for block in display_blocks
        ],
        "diarization": {
            "status": window.diarization_status,
            "reason": window.diarization_reason,
        },
    }


def _subtitle_display_blocks(window: ASRWindow) -> tuple[DisplayBlock, ...]:
    """把字幕内存窗口适配为统一 transcript facts 后调用共享 projector。"""
    source_session_id = window.source_session_id or f"subtitle-epoch-{window.source_epoch}"
    namespace = uuid5(NAMESPACE_URL, f"sona:subtitle:{source_session_id}")
    source_item_id = f"subtitle:{source_session_id}:{window.source_epoch}"
    items: list[TranscriptItem] = []
    spans: list[TranscriptAttributionSpan] = []
    for sequence, segment in enumerate(window.segments):
        source_uid = segment.source_uid or _fallback_source_uid(segment)
        item_id = uuid5(namespace, f"item:{source_uid}")
        item = TranscriptItem(
            id=item_id,
            meeting_id=namespace,
            source_session_id=segment.source_session_id or source_session_id,
            source_epoch=segment.source_epoch,
            source_item_id=source_item_id,
            source_segment_uid=source_uid,
            sequence=sequence,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=segment.text,
            language=segment.detected_language or "und",
        )
        status = _subtitle_speaker_status(window, segment)
        speaker_key = segment.speaker_key if status in {"identified", "anonymous"} else None
        timing_quality: TimingQuality = (
            "unavailable" if segment.timing_quality == "unavailable" else "aligned"
        )
        spans.append(
            TranscriptAttributionSpan(
                item_id=item_id,
                source_session_id=item.source_session_id,
                source_segment_uid=source_uid,
                text_start=0,
                text_end=len(segment.text),
                audio_start_ms=segment.start_ms,
                audio_end_ms=segment.end_ms,
                timing_quality=timing_quality,
                speaker_key=speaker_key,
                speaker_status=status,
                speaker_name=_subtitle_speaker(segment) if speaker_key else None,
            )
        )
        items.append(item)
    partial_key = window.partial_speaker_key
    partial_status = _subtitle_partial_status(window)
    if partial_status in {"off", "pending", "degraded"}:
        partial_key = None
    return TranscriptPresentationProjector().project(
        items,
        spans,
        partial_text=window.partial,
        partial_speaker_key=partial_key,
        partial_speaker_status=partial_status,
    )


def _subtitle_speaker_status(window: ASRWindow, segment: ASRSegment) -> SpeakerStatus:
    if window.diarization_status == "off":
        return "off"
    if window.diarization_status == "degraded":
        return "degraded"
    return "pending" if segment.speaker_key.strip() == "unknown" else "anonymous"


def _subtitle_partial_status(window: ASRWindow) -> SpeakerStatus:
    if window.diarization_status == "off":
        return "off"
    if window.diarization_status == "degraded":
        return "degraded"
    return (
        "pending"
        if not window.partial_speaker_key or window.partial_speaker_key.strip() == "unknown"
        else "anonymous"
    )


def _fallback_source_uid(segment: ASRSegment) -> str:
    digest = sha256(
        f"{segment.source_epoch}:{segment.order}:{segment.start_ms}:{segment.end_ms}:{segment.text}".encode()
    ).hexdigest()[:24]
    return f"fallback-{digest}"


__all__ = ["UNKNOWN_SUBTITLE_SPEAKER", "legacy_ready_payload", "legacy_subtitle_payload"]
