"""进入会议转录实体的唯一 ASR 映射边界。"""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from sona.asr.models import ASRSegment, ASRWindow
from sona.meeting.models import NormalizedSegment, TranscriptWindow
from sona.meeting.speaker_attribution import (
    SAMPLES_PER_MS,
    CompletedAttributionUnit,
    CompletedItem,
)

__all__ = ["meeting_sample", "to_transcript_window"]

# SPK-E2E-1 扩展模式的稳定身份种子前缀（source session + segment UID）。
_NEW_MODE_IDENTITY_PREFIX = "speechrail:spk-e2e-1"

# 16 kHz：1 ms = 16 samples。样本域换算保持整数精确，不逐包取整累加。
_SAMPLES_PER_MS = 16


def meeting_sample(epoch_start: int, rail_sample: int) -> int:
    """把 SpeechRail session 样本域时间换算为会议时间线样本。

    ``meeting_sample = epoch_start + rail_sample``：一个 source epoch 只加一次
    起点，禁止在 item/VAD 层重复叠加偏移。不校验负值——调用方（适配器）负责
    在校验样本范围后再换算。
    """
    return epoch_start + rail_sample


def to_transcript_window(window: ASRWindow) -> TranscriptWindow:
    """把 ASR 中立窗口投影为会议 TranscriptWindow。

    legacy segment UUID 使用版本化种子，包含 source epoch、顺序、带会议 group
    的 speaker key、绝对时间区间和文本；同一窗口重播保持 ID 稳定。扩展模式的
    归属单元（带 ``source_uid``）改用 ``source session + segment UID`` 派生稳定
    UUID——重连后的同编号 UID 不会误并身份。历史已落库 ID 不做迁移。
    """
    return TranscriptWindow(
        source_epoch=window.source_epoch,
        partial=window.partial,
        partial_speaker_key=window.partial_speaker_key,
        segments=tuple(_to_normalized_segment(window, segment) for segment in window.segments),
        speaker_remap=window.speaker_remap,
        completed=tuple(
            _to_completed_item(item, window)
            for item in window.completed_items
            if item.canonical_text.strip()
        ),
    )


def _to_completed_item(item: object, window: ASRWindow) -> CompletedItem:
    """把扩展模式的 completed（session 样本域）转换为 repository 输入。"""
    from sona.asr.models import ASRCompletedItem

    assert isinstance(item, ASRCompletedItem)
    return CompletedItem(
        source_session_id=window.source_session_id or f"epoch:{window.source_epoch}",
        source_epoch=window.source_epoch,
        meeting_start_sample=window.offset_ms * SAMPLES_PER_MS,
        item_id=item.item_id,
        event_id=item.event_id,
        sequence=item.sequence,
        audio_start_sample=item.audio_start_sample,
        audio_end_sample=item.audio_end_sample,
        canonical_text=item.canonical_text,
        units=tuple(
            CompletedAttributionUnit(
                segment_uid=unit.segment_uid,
                text_start=unit.text_start,
                text_end=unit.text_end,
                audio_start_sample=unit.audio_start_sample,
                audio_end_sample=unit.audio_end_sample,
                timing_quality=unit.timing_quality,  # type: ignore[arg-type]
            )
            for unit in item.units
        ),
    )


def _to_normalized_segment(window: ASRWindow, segment: ASRSegment) -> NormalizedSegment:
    identity = (
        _new_mode_identity(window, segment)
        if segment.source_uid is not None
        else _segment_identity_seed(window, segment)
    )
    return NormalizedSegment(
        id=uuid5(NAMESPACE_URL, identity),
        order=segment.order,
        source_epoch=segment.source_epoch,
        speaker_key=segment.speaker_key,
        start_ms=segment.start_ms,
        end_ms=segment.end_ms,
        text=segment.text,
        translation=segment.translation,
        detected_language=segment.detected_language,
    )


def _new_mode_identity(window: ASRWindow, segment: ASRSegment) -> str:
    """扩展模式身份：source session + segment UID（meeting 作用域由库层隔离）。"""
    session_id = window.source_session_id or f"epoch:{window.source_epoch}"
    return f"{_NEW_MODE_IDENTITY_PREFIX}:{session_id}:{segment.source_uid}"


def _segment_identity_seed(window: ASRWindow, segment: ASRSegment) -> str:
    """Return the deterministic identity seed for one absolute ASR segment."""

    identity = json.dumps(
        [
            window.source_epoch,
            segment.source_epoch,
            segment.order,
            segment.speaker_key,
            segment.start_ms,
            segment.end_ms,
            segment.text,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"speechrail:v2:{identity}"
