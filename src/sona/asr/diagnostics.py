"""ASR 流的隐私安全、有限计数诊断。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

__all__ = ["ASRDiagnostics"]


@dataclass(slots=True)
class ASRDiagnostics:
    """记录 ASR 工作负载计数，不保存音频、转录或说话人内容。"""

    sent_samples: int = 0
    partial_events: int = 0
    empty_completed: int = 0
    nonempty_completed: int = 0
    committed_events: int = 0
    reconnects: int = 0
    protocol_errors: int = 0

    def record_audio_samples(self, samples: int) -> None:
        """累加成功发送到后端的 PCM 样本数。"""
        if samples < 0:
            raise ValueError("samples 必须非负")
        self.sent_samples += samples

    def record_partial(self) -> None:
        self.partial_events += 1

    def record_empty_completed(self) -> None:
        self.empty_completed += 1

    def record_nonempty_completed(self) -> None:
        self.nonempty_completed += 1

    def record_committed(self) -> None:
        self.committed_events += 1

    def record_reconnect(self) -> None:
        self.reconnects += 1

    def record_protocol_error(self) -> None:
        self.protocol_errors += 1

    def reset(self) -> None:
        """清空当前连接计数，保持对象可复用。"""
        self.sent_samples = 0
        self.partial_events = 0
        self.empty_completed = 0
        self.nonempty_completed = 0
        self.committed_events = 0
        self.reconnects = 0
        self.protocol_errors = 0

    def snapshot(self) -> dict[str, int]:
        """返回固定字段的副本，避免诊断输出携带内容数据。"""
        return {key: int(value) for key, value in asdict(self).items()}
