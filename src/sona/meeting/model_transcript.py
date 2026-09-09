"""供 summary/Inner OS 使用的 block-level ModelTranscript。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .transcript_models import DisplayBlock


@dataclass(frozen=True, slots=True)
class ModelTranscriptEvidence:
    alias: str
    block_id: str
    source_ids: tuple[str, ...]
    start_ms: int | None
    end_ms: int | None
    speaker_name: str | None
    text: str


@dataclass(frozen=True, slots=True)
class ModelTranscript:
    text: str
    evidence: tuple[ModelTranscriptEvidence, ...]


def build_model_transcript(blocks: Sequence[DisplayBlock]) -> ModelTranscript:
    evidence: list[ModelTranscriptEvidence] = []
    lines: list[str] = []
    for index, block in enumerate((block for block in blocks if not block.is_partial), 1):
        alias = f"B{index:04d}"
        evidence.append(
            ModelTranscriptEvidence(
                alias=alias,
                block_id=block.block_id,
                source_ids=block.source_ids,
                start_ms=block.start_ms,
                end_ms=block.end_ms,
                speaker_name=block.speaker_name,
                text=block.text,
            )
        )
        timing = (
            "time:unavailable"
            if block.start_ms is None or block.end_ms is None
            else f"{_format_timestamp(block.start_ms)}–{_format_timestamp(block.end_ms)}"
        )
        speaker = block.speaker_name or _speaker_status_label(block.speaker_status)
        lines.append(f"[{alias}][{timing}][{speaker}] {block.text}")
    return ModelTranscript(text="\n".join(lines), evidence=tuple(evidence))


def _format_timestamp(milliseconds: int) -> str:
    seconds, remainder = divmod(milliseconds, 1_000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{remainder:03d}"


def _speaker_status_label(status: str) -> str:
    return {
        "identified": "已识别说话人",
        "anonymous": "匿名说话人",
        "pending": "正在确认",
        "off": "分人未启用",
        "degraded": "分人不可用",
    }.get(status, "说话人")


__all__ = ["ModelTranscript", "ModelTranscriptEvidence", "build_model_transcript"]
