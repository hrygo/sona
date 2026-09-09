"""统一转录事实和展示对象。

正文事实与归属事实故意分成两个不可变模型。归属可以修订，正文模型本身不提供
任何替换正文、时间或 source identity 的接口。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

SpeakerStatus = Literal["identified", "anonymous", "pending", "off", "degraded"]
TimingQuality = Literal["aligned", "unavailable"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TranscriptItem(_FrozenModel):
    """一条 completed 正文事实；一条 source item 始终只对应一行。"""

    id: UUID = Field(default_factory=uuid4)
    meeting_id: UUID
    source_session_id: str = Field(min_length=1, max_length=128)
    source_epoch: int = Field(ge=0)
    source_item_id: str = Field(min_length=1, max_length=128)
    source_segment_uid: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=100_000)
    language: str = Field(min_length=1, max_length=32)
    status: Literal["completed"] = "completed"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def validate_time_range(self) -> TranscriptItem:
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms 必须大于等于 start_ms")
        return self


class TranscriptAttributionSpan(_FrozenModel):
    """正文范围上的可修订 speaker/timing 元数据，不重复保存正文。"""

    id: UUID = Field(default_factory=uuid4)
    item_id: UUID
    text_start: int = Field(ge=0)
    text_end: int = Field(gt=0)
    audio_start_ms: int = Field(ge=0)
    audio_end_ms: int = Field(ge=0)
    timing_quality: TimingQuality
    speaker_key: str | None = Field(default=None, min_length=1, max_length=200)
    speaker_status: SpeakerStatus
    speaker_name: str | None = Field(default=None, min_length=1, max_length=200)
    speaker_confidence: float | None = Field(default=None, ge=0, le=1)
    speaker_revision: int = Field(default=0, ge=0)
    manually_corrected: bool = False
    candidates: tuple[str, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_ranges(self) -> TranscriptAttributionSpan:
        if self.text_end <= self.text_start:
            raise ValueError("text_end 必须大于 text_start")
        if self.audio_end_ms < self.audio_start_ms:
            raise ValueError("audio_end_ms 必须大于等于 audio_start_ms")
        if self.speaker_status in {"off", "pending"} and self.speaker_key is not None:
            raise ValueError("off/pending attribution 不得携带 speaker_key")
        return self


class AttributionRevision(_FrozenModel):
    """一次 speaker-only 修订审计记录。"""

    id: UUID = Field(default_factory=uuid4)
    span_id: UUID
    revision: int = Field(ge=1)
    speaker_key: str | None = Field(default=None, min_length=1, max_length=200)
    speaker_status: SpeakerStatus
    manually_corrected: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DisplayBlock(_FrozenModel):
    """projector 的可读派生对象，不是数据库事实主键。"""

    block_id: str = Field(min_length=1, max_length=256)
    item_ids: tuple[UUID, ...] = ()
    source_ids: tuple[str, ...] = ()
    text: str = Field(min_length=1, max_length=100_000)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)
    speaker_key: str | None = Field(default=None, max_length=200)
    speaker_name: str | None = Field(default=None, max_length=200)
    speaker_status: SpeakerStatus
    speaker_color_token: str = Field(min_length=1, max_length=64)
    timing_quality: TimingQuality
    is_partial: bool = False

    @model_validator(mode="after")
    def validate_time_range(self) -> DisplayBlock:
        if self.start_ms is None or self.end_ms is None:
            if self.timing_quality != "unavailable":
                raise ValueError("缺失时间范围时 timing_quality 必须为 unavailable")
            return self
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms 必须大于等于 start_ms")
        return self


class ProjectorProfile(_FrozenModel):
    """展示 profile；不改变底层正文事实约束。"""

    max_gap_ms: int = Field(default=1_200, ge=0)
    max_duration_ms: int = Field(default=15_000, gt=0)
    max_chars: int = Field(default=180, gt=0)


__all__ = [
    "AttributionRevision",
    "DisplayBlock",
    "ProjectorProfile",
    "SpeakerStatus",
    "TimingQuality",
    "TranscriptAttributionSpan",
    "TranscriptItem",
]
