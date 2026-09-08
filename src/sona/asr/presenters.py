"""ASR 领域对象到既有外部展示协议的纯转换。"""

from __future__ import annotations

from typing import Any

from sona.asr.models import ASRSegment, ASRWindow

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
        "diarization": {
            "status": window.diarization_status,
            "reason": window.diarization_reason,
        },
    }


__all__ = ["UNKNOWN_SUBTITLE_SPEAKER", "legacy_ready_payload", "legacy_subtitle_payload"]
