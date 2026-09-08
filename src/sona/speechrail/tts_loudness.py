"""Stateful PCM16 loudness protection for SpeechRail TTS playback.

The guard is intentionally small and vendor-neutral.  It keeps its state for
one TTS response, so a stream of short audio deltas is treated as one signal
instead of a collection of independent normalization jobs.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Literal

LoudnessMode = Literal["safety", "compatibility"]

_PCM16_FULL_SCALE = 32768.0
_SILENCE_GATE_DBFS = -50.0
_TRANSIENT_COMPRESSION_RATIO = 4.0
_TRANSIENT_COMPRESSION = 1.0 - (1.0 / _TRANSIENT_COMPRESSION_RATIO)
_GAIN_RAMP_MS = 5.0


@dataclass(frozen=True, slots=True)
class Pcm16LoudnessConfig:
    """Boundaries for request-scoped PCM16 loudness protection."""

    target_dbfs: float = -20.0
    peak_ceiling_dbfs: float = -1.0
    max_gain_db: float = 12.0
    max_attenuation_db: float = -6.0
    calibration_ms: int = 240
    attack_ms: int = 250
    release_ms: int = 800

    def __post_init__(self) -> None:
        for name, value in (
            ("target_dbfs", self.target_dbfs),
            ("peak_ceiling_dbfs", self.peak_ceiling_dbfs),
            ("max_gain_db", self.max_gain_db),
            ("max_attenuation_db", self.max_attenuation_db),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.max_gain_db < 0:
            raise ValueError("max_gain_db must not be negative")
        if self.max_attenuation_db > 0:
            raise ValueError("max_attenuation_db must not be positive")
        if self.max_attenuation_db > self.peak_ceiling_dbfs:
            raise ValueError("max_attenuation_db must reach the peak ceiling")
        if self.calibration_ms <= 0:
            raise ValueError("calibration_ms must be positive")
        if self.attack_ms <= 0:
            raise ValueError("attack_ms must be positive")
        if self.release_ms <= 0:
            raise ValueError("release_ms must be positive")


class StreamingPcm16LoudnessGuard:
    """Apply bounded, stateful gain control to a mono little-endian PCM16 stream."""

    _gain_db: float
    _base_gain_db: float | None
    _level_dbfs: float | None
    _calibration_sample_limit: int
    _calibration_samples_seen: int
    _calibration_active_samples: int
    _calibration_energy: float

    def __init__(
        self,
        *,
        sample_rate: int,
        config: Pcm16LoudnessConfig | None = None,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self._sample_rate = sample_rate
        self._config = config or Pcm16LoudnessConfig()
        self._mode: LoudnessMode = "compatibility"
        self.reset()

    @property
    def mode(self) -> LoudnessMode:
        """Return the current protection mode."""
        return self._mode

    def set_mode(self, mode: LoudnessMode) -> None:
        """Select server-contract safety or legacy compatibility protection."""
        if mode not in ("safety", "compatibility"):
            raise ValueError(f"unsupported loudness mode: {mode}")
        if mode != self._mode:
            self._mode = mode
            self._reset_signal_state()

    def reset(self) -> None:
        """Discard all signal state while keeping the selected mode."""
        self._reset_signal_state()

    def process(self, pcm: bytes) -> bytes:
        """Process one PCM16 delta without changing its sample count."""
        if len(pcm) % 2:
            raise ValueError("PCM16 payload length must be even")
        if not pcm:
            return b""

        values = list(struct.unpack(f"<{len(pcm) // 2}h", pcm))
        peak_dbfs = _dbfs(max(abs(value) for value in values))
        if self._mode == "safety":
            desired_gain_db = min(0.0, self._config.peak_ceiling_dbfs - peak_dbfs)
            desired_gain_db = max(desired_gain_db, self._config.max_attenuation_db)
        else:
            desired_gain_db = self._compatibility_gain(values, peak_dbfs)

        desired_gain_db = min(
            desired_gain_db,
            self._config.peak_ceiling_dbfs - peak_dbfs,
        )
        desired_gain_db = _clamp(
            desired_gain_db,
            self._config.max_attenuation_db,
            self._config.max_gain_db,
        )
        previous_gain_db = self._gain_db
        ramp_start_gain_db = previous_gain_db
        if self._mode == "compatibility" and not any(
            abs(value) >= _SILENCE_GATE_LINEAR for value in values
        ):
            ramp_start_gain_db = min(ramp_start_gain_db, 0.0)
        output = _apply_gain(
            values,
            previous_gain_db=ramp_start_gain_db,
            target_gain_db=desired_gain_db,
            ceiling_gain_db=self._config.peak_ceiling_dbfs - peak_dbfs,
            sample_rate=self._sample_rate,
        )
        self._gain_db = desired_gain_db
        if desired_gain_db == 0.0 and previous_gain_db == 0.0:
            return pcm
        return struct.pack(f"<{len(output)}h", *output)

    def _compatibility_gain(self, values: list[int], peak_dbfs: float) -> float:
        active = [value for value in values if abs(value) >= _SILENCE_GATE_LINEAR]
        if not active:
            # Do not carry a positive gain into silence and amplify the noise
            # floor.  A previously required attenuation may safely remain.
            return min(self._gain_db, 0.0)

        active_rms_dbfs = _dbfs(
            math.sqrt(sum(value * value for value in active) / len(active))
        )
        self._update_calibration(values, active)
        self._update_level_estimate(active_rms_dbfs, len(values))
        assert self._level_dbfs is not None

        base_gain_target = _clamp(
            self._config.target_dbfs - self._level_dbfs,
            self._config.max_attenuation_db,
            self._config.max_gain_db,
        )
        if self._base_gain_db is None:
            self._base_gain_db = base_gain_target
        else:
            self._base_gain_db = _slew_db(
                self._base_gain_db,
                base_gain_target,
                duration_ms=len(values) * 1_000 / self._sample_rate,
                rising_time_ms=self._config.release_ms,
                falling_time_ms=self._config.attack_ms,
            )
        transient_gain = -(
            active_rms_dbfs - self._level_dbfs
        ) * _TRANSIENT_COMPRESSION
        desired_gain_db = self._base_gain_db + transient_gain
        return min(desired_gain_db, self._config.peak_ceiling_dbfs - peak_dbfs)

    def _update_calibration(self, values: list[int], active: list[int]) -> None:
        if self._calibration_samples_seen >= self._calibration_sample_limit:
            return
        remaining = self._calibration_sample_limit - self._calibration_samples_seen
        self._calibration_samples_seen += min(len(values), remaining)
        calibration_values = values[:remaining]
        calibration_active = [
            value for value in calibration_values if abs(value) >= _SILENCE_GATE_LINEAR
        ]
        self._calibration_active_samples += len(calibration_active)
        self._calibration_energy += sum(value * value for value in calibration_active)
        if self._calibration_samples_seen < self._calibration_sample_limit:
            return
        if self._calibration_active_samples and self._level_dbfs is not None:
            calibrated_rms_dbfs = _dbfs(
                math.sqrt(
                    self._calibration_energy / self._calibration_active_samples
                )
            )
            # Blend the finite-window estimate into the live envelope so the
            # calibration boundary cannot create a gain step.
            self._level_dbfs += (calibrated_rms_dbfs - self._level_dbfs) * 0.25

    def _update_level_estimate(self, current_dbfs: float, sample_count: int) -> None:
        if self._level_dbfs is None:
            self._level_dbfs = current_dbfs
            return
        duration_ms = sample_count * 1_000 / self._sample_rate
        time_ms = (
            self._config.attack_ms
            if current_dbfs > self._level_dbfs
            else self._config.release_ms
        )
        alpha = 1.0 - math.exp(-duration_ms / time_ms)
        self._level_dbfs += (current_dbfs - self._level_dbfs) * alpha

    def _reset_signal_state(self) -> None:
        self._gain_db = 0.0
        self._base_gain_db = None
        self._level_dbfs = None
        self._calibration_sample_limit = max(
            1,
            round(self._sample_rate * self._config.calibration_ms / 1_000),
        )
        self._calibration_samples_seen = 0
        self._calibration_active_samples = 0
        self._calibration_energy = 0.0


_SILENCE_GATE_LINEAR = _PCM16_FULL_SCALE * 10 ** (_SILENCE_GATE_DBFS / 20.0)


def _dbfs(value: float) -> float:
    return 20.0 * math.log10(max(value / _PCM16_FULL_SCALE, 1e-12))


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _slew_db(
    current: float,
    target: float,
    *,
    duration_ms: float,
    rising_time_ms: int,
    falling_time_ms: int,
) -> float:
    time_ms = rising_time_ms if target > current else falling_time_ms
    alpha = 1.0 - math.exp(-duration_ms / time_ms)
    return current + (target - current) * alpha


def _apply_gain(
    values: list[int],
    *,
    previous_gain_db: float,
    target_gain_db: float,
    ceiling_gain_db: float,
    sample_rate: int,
) -> list[int]:
    if previous_gain_db == target_gain_db:
        gain = 10 ** (target_gain_db / 20.0)
        return [_scale_sample(value, gain) for value in values]

    ramp_samples = min(
        len(values),
        max(1, round(sample_rate * _GAIN_RAMP_MS / 1_000)),
    )
    output: list[int] = []
    for index, value in enumerate(values):
        if index < ramp_samples:
            fraction = (index + 1) / ramp_samples
            gain_db = previous_gain_db + (target_gain_db - previous_gain_db) * fraction
        else:
            gain_db = target_gain_db
        gain_db = min(gain_db, ceiling_gain_db)
        output.append(_scale_sample(value, 10 ** (gain_db / 20.0)))
    return output


def _scale_sample(value: int, gain: float) -> int:
    return int(_clamp(round(value * gain), -32768, 32767))


__all__ = ["Pcm16LoudnessConfig", "StreamingPcm16LoudnessGuard"]
