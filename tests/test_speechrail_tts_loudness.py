from __future__ import annotations

import math
from itertools import pairwise

import pytest

from sona.speechrail.tts_loudness import StreamingPcm16LoudnessGuard


def test_guard_rejects_odd_pcm16() -> None:
    guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)

    with pytest.raises(ValueError, match="PCM16 payload length must be even"):
        guard.process(b"\x00")


def test_safety_mode_does_not_raise_quiet_audio() -> None:
    guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
    guard.set_mode("safety")
    chunk = _constant_pcm16(0.05, 1_920)

    assert guard.process(chunk) == chunk


def test_compatibility_mode_does_not_amplify_near_silence() -> None:
    guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
    chunk = _constant_pcm16(0.0005, 1_920)

    assert guard.process(chunk) == chunk


def test_compatibility_mode_smooths_alternating_levels() -> None:
    guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
    low = _constant_pcm16(0.05, 1_920)
    high = _constant_pcm16(0.40, 1_920)

    output = [guard.process(chunk) for chunk in (low, high, low, high)]

    assert _rms_jump_p95(output) < 8.0


def test_guard_limits_peak_without_pcm_wraparound() -> None:
    guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)

    output = guard.process(_constant_pcm16(0.99, 1_920))

    assert _peak_dbfs(output) <= -1.0 + 0.1
    assert max(abs(value) for value in _decode_pcm16(output)) <= 32767


def test_guard_reset_starts_the_next_request_without_previous_gain() -> None:
    guard = StreamingPcm16LoudnessGuard(sample_rate=24_000)
    loud = _constant_pcm16(0.40, 1_920)
    quiet = _constant_pcm16(0.05, 1_920)

    guard.process(loud)
    guard.reset()

    assert guard.process(quiet) != quiet


def _constant_pcm16(level: float, samples: int) -> bytes:
    value = int(level * 32767)
    return b"".join(value.to_bytes(2, "little", signed=True) for _ in range(samples))


def _decode_pcm16(pcm: bytes) -> list[int]:
    return [
        int.from_bytes(pcm[index : index + 2], "little", signed=True)
        for index in range(0, len(pcm), 2)
    ]


def _rms_dbfs(pcm: bytes) -> float:
    values = _decode_pcm16(pcm)
    rms = math.sqrt(sum(value * value for value in values) / len(values)) / 32767
    return 20 * math.log10(max(rms, 1e-12))


def _peak_dbfs(pcm: bytes) -> float:
    peak = max(abs(value) for value in _decode_pcm16(pcm)) / 32767
    return 20 * math.log10(max(peak, 1e-12))


def _rms_jump_p95(chunks: list[bytes]) -> float:
    jumps = [
        abs(_rms_dbfs(left) - _rms_dbfs(right))
        for left, right in pairwise(chunks)
    ]
    return sorted(jumps)[min(len(jumps) - 1, math.ceil(len(jumps) * 0.95) - 1)]
