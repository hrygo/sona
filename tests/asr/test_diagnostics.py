"""ASR 事件诊断的隐私边界与计数行为测试。"""

from __future__ import annotations

from sona.asr.diagnostics import ASRDiagnostics


def test_diagnostics_snapshot_contains_only_bounded_counters() -> None:
    diagnostics = ASRDiagnostics()

    diagnostics.record_audio_samples(160)
    diagnostics.record_partial()
    diagnostics.record_empty_completed()
    diagnostics.record_nonempty_completed()
    diagnostics.record_committed()
    diagnostics.record_reconnect()
    diagnostics.record_protocol_error()

    assert diagnostics.snapshot() == {
        "sent_samples": 160,
        "partial_events": 1,
        "empty_completed": 1,
        "nonempty_completed": 1,
        "committed_events": 1,
        "reconnects": 1,
        "protocol_errors": 1,
    }


def test_diagnostics_reset_clears_counters() -> None:
    diagnostics = ASRDiagnostics(sent_samples=3, partial_events=2)

    diagnostics.reset()

    assert diagnostics.snapshot() == {
        "sent_samples": 0,
        "partial_events": 0,
        "empty_completed": 0,
        "nonempty_completed": 0,
        "committed_events": 0,
        "reconnects": 0,
        "protocol_errors": 0,
    }


def test_diagnostics_snapshot_does_not_accept_or_emit_transcript_data() -> None:
    diagnostics = ASRDiagnostics()

    snapshot = diagnostics.snapshot()

    assert all(
        key not in snapshot
        for key in ("text", "transcript", "audio", "prompt", "speaker", "name")
    )
