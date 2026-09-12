"""会议助手后端领域层。

该包只承载与前端无关的会议状态、转录和纪要数据契约；运行时编排、HTTP
路由和 React 实现位于各自的边界模块中。
"""

from .model_transcript import ModelTranscript, ModelTranscriptEvidence, build_model_transcript
from .models import (
    ActionItem,
    Decision,
    Highlight,
    MeetingPage,
    MeetingRecord,
    MeetingStatus,
    MinutesJob,
    MinutesRecord,
    MinutesResult,
    MinutesStatus,
    NormalizedSegment,
    OpenQuestion,
    Risk,
    RuntimeMode,
    SpeakerRecord,
    StorageHealth,
    Topic,
    TranscriptDocument,
    TranscriptReconcileResult,
    TranscriptWindow,
)
from .transcript_models import (
    AttributionRevision,
    DisplayBlock,
    ProjectorProfile,
    TranscriptAttributionSpan,
    TranscriptItem,
)
from .transcript_projector import TranscriptPresentationProjector

__all__ = [
    "ActionItem",
    "AttributionRevision",
    "Decision",
    "DisplayBlock",
    "Highlight",
    "MeetingPage",
    "MeetingRecord",
    "MeetingStatus",
    "MinutesJob",
    "MinutesRecord",
    "MinutesResult",
    "MinutesStatus",
    "ModelTranscript",
    "ModelTranscriptEvidence",
    "NormalizedSegment",
    "OpenQuestion",
    "ProjectorProfile",
    "Risk",
    "RuntimeMode",
    "SpeakerRecord",
    "StorageHealth",
    "Topic",
    "TranscriptAttributionSpan",
    "TranscriptDocument",
    "TranscriptItem",
    "TranscriptPresentationProjector",
    "TranscriptReconcileResult",
    "TranscriptWindow",
    "build_model_transcript",
]
