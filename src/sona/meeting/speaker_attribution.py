"""会议讲话人归属（SPK-E2E-1）的稳定领域对象与纯判定逻辑。

这里定义 completed 正文追加与 speaker-only 归属修订的专用事务输入/输出。
它们与 SpeechRail wire event 严格分离：transport 类型必须先经显式转换，
禁止把未校验 dict 送入 repository。

 speaker-only 事务不重用 ``reconcile_window`` 的历史后缀替换语义：
正文、时间、segment 身份不可变，只有归属与证据列可以变化。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "SPEAKER_KEY_UNKNOWN",
    "SPEAKER_STATUS_UNKNOWN",
    "AttributionCandidate",
    "CompletedAttributionUnit",
    "CompletedItem",
    "SpeakerPatch",
    "SpeakerPatchEvent",
    "SpeakerPatchResult",
    "canonical_payload_hash",
    "patch_target_errors",
    "resolve_patch_application",
    "segment_identity",
    "speaker_source_key",
]

# 无归属保留 key：不是 UUID 身份，不得出现在实名候选列表。
SPEAKER_KEY_UNKNOWN = "unknown"
SPEAKER_STATUS_UNKNOWN = "unknown"

_NEW_MODE_IDENTITY = "speechrail:spk-e2e-1"
_SPEAKER_SOURCE_IDENTITY = "speechrail:spk-e2e-1:speaker-source"


class AttributionCandidate(BaseModel):
    """一个候选声学来源及其支持度（不是身份概率，不做归一化）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_speaker: str = Field(min_length=1, max_length=64)
    support_ratio: float = Field(ge=0.0, le=1.0)


class CompletedAttributionUnit(BaseModel):
    """一个不可变归属单元：canonical text 的 code point 切片（左闭右开）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_uid: str = Field(min_length=1, max_length=128)
    text_start: int = Field(ge=0)
    text_end: int = Field(gt=0)
    audio_start_sample: int = Field(ge=0)
    audio_end_sample: int = Field(ge=0)
    timing_quality: Literal["aligned", "unavailable"]

    @model_validator(mode="after")
    def _validate_ranges(self) -> CompletedAttributionUnit:
        if self.text_end <= self.text_start:
            raise ValueError("text_end 必须大于 text_start")
        if self.audio_end_sample < self.audio_start_sample:
            raise ValueError("audio_end_sample 必须大于等于 audio_start_sample")
        return self

    def text(self, canonical: str) -> str:
        """按 code point 切片取单元正文；调用方保证范围已无缝校验。"""
        return canonical[self.text_start : self.text_end]


class CompletedItem(BaseModel):
    """一次 commit 的固定正文事实（canonical text + 不可变归属单元）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_session_id: str = Field(min_length=1, max_length=128)
    source_epoch: int = Field(ge=0)
    meeting_start_sample: int = Field(ge=0)
    item_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    audio_start_sample: int = Field(ge=0)
    audio_end_sample: int = Field(ge=0)
    canonical_text: str = Field(max_length=100_000)
    units: tuple[CompletedAttributionUnit, ...] = Field(max_length=4096)

    @model_validator(mode="after")
    def _validate_tiling(self) -> CompletedItem:
        """单元范围必须无缝完整分割 canonical text；空文本必须无单元。"""
        expected = 0
        for unit in self.units:
            if unit.text_start != expected or unit.text_end > len(self.canonical_text):
                raise ValueError("attribution units 必须无缝分割 canonical text")
            expected = unit.text_end
        if expected != len(self.canonical_text):
            raise ValueError("attribution units 必须无缝分割 canonical text")
        if self.audio_end_sample < self.audio_start_sample:
            raise ValueError("audio_end_sample 必须大于等于 audio_start_sample")
        return self


class SpeakerPatch(BaseModel):
    """对单个归属单元的一次修订（revision 从 1 严格递增）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    segment_uid: str = Field(min_length=1, max_length=128)
    revision: int = Field(ge=1)
    status: Literal["unknown", "tentative", "stable"]
    source_speaker: str | None = Field(default=None, min_length=1, max_length=64)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    overlap_ratio: float = Field(ge=0.0, le=1.0)
    candidates: tuple[AttributionCandidate, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def _validate_speaker(self) -> SpeakerPatch:
        if self.status == "unknown" and self.source_speaker is not None:
            raise ValueError("unknown 状态的主 speaker 必须为 null")
        if self.status != "unknown" and self.source_speaker is None:
            raise ValueError("tentative/stable 状态必须携带 source speaker")
        return self


class SpeakerPatchEvent(BaseModel):
    """一批归属修订（同连接顺序交付；一次事务一次版本）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_session_id: str = Field(min_length=1, max_length=128)
    event_id: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    group_generation: str | None = Field(default=None, min_length=1, max_length=128)
    stable_through_sample: int = Field(ge=0)
    patches: tuple[SpeakerPatch, ...] = Field(default=(), max_length=256)
    links: tuple[str, ...] = Field(default=(), max_length=16)


class SpeakerPatchResult(BaseModel):
    """一次 speaker-only 事务的产出。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    meeting_id: UUID
    changed_segment_ids: tuple[UUID, ...] = ()
    transcript_revision: int = Field(ge=0)
    content_revision: int = Field(ge=0)
    diarization_status: Literal["legacy", "active", "complete", "degraded"]


def segment_identity(meeting_id: UUID, session_id: str, segment_uid: str) -> UUID:
    """稳定 segment UUID：UUIDv5(meeting, session + ':' + uid)（设计 §4.2）。"""
    return uuid5(meeting_id, f"{session_id}:{segment_uid}")


def speaker_source_key(meeting_id: UUID, session_id: str, source_speaker: str) -> str:
    """把 (session, 匿名标签) 解析为会议内不透明应用身份 UUID 字符串。

    同一 session 内同标签恒同身份；跨 session 相同编号不合并，只有显式
    speaker_link 且无人工冲突时才由上层建立关联。
    """
    return str(uuid5(meeting_id, f"{_SPEAKER_SOURCE_IDENTITY}:{session_id}:{source_speaker}"))


def canonical_payload_hash(payload: object) -> str:
    """对规范化 JSON 载荷取 sha256；同 event 不同内容必须可判定冲突。"""
    import hashlib
    import json

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def patch_target_errors(
    event: SpeakerPatchEvent,
    targets: dict[str, tuple[int, bool]],
    *,
    allow_frozen: bool = False,
) -> list[str]:
    """整批校验 patch 目标；返回错误列表（空 = 全部可应用）。

    ``targets``: uid -> (当前 speaker_revision, speaker_frozen)。
    未知 UID、revision 不连续（非 last+1）、已冻结且不允许覆盖均算错误；
    任何错误都要求整批拒绝，不允许半批写入。
    """
    errors: list[str] = []
    for patch in event.patches:
        current = targets.get(patch.segment_uid)
        if current is None:
            errors.append(f"unknown_uid:{patch.segment_uid}")
            continue
        revision, frozen = current
        if patch.revision != revision + 1:
            errors.append(f"revision_gap:{patch.segment_uid}:{patch.revision}")
            continue
        if frozen and not allow_frozen:
            errors.append(f"frozen:{patch.segment_uid}")
    return errors


def resolve_patch_application(
    *,
    override_key: str | None,
    model_key: str | None,
    status: str,
) -> tuple[str, str | None, str | None]:
    """决定一次修订落库后的 (speaker_key, model_speaker_key, override_key)。

    人工 override 优先且不被自动修订覆盖：override 存在时 speaker_key 保持
    用户结果，模型值只写入 model_speaker_key 供追溯；override 为 None 时
    speaker_key 跟随最新模型归属（unknown 时使用保留 key）。
    """
    if override_key is not None:
        return override_key, model_key, override_key
    effective = model_key if model_key is not None else SPEAKER_KEY_UNKNOWN
    return effective, model_key, None
