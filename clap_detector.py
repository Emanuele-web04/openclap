"""
FILE: clap_detector.py
Purpose: Detects clap-like audio events with overlapped analysis windows,
adaptive thresholds, and a short event state machine.
Depends on: numpy plus Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Sequence

import numpy as np


def _clamp(value: float, minimum: float, maximum: float) -> float:
    """Constrains one float into a stable range."""

    return max(minimum, min(maximum, value))


@dataclass
class ClapCalibrationProfile:
    """User-specific detector profile learned from a guided calibration pass."""

    captured_claps: int = 0
    calibrated_at: float = 0.0
    noise_rms: float = 0.0
    noise_transient: float = 0.0
    clap_peak: float = 0.0
    clap_rms: float = 0.0
    clap_transient: float = 0.0
    clap_score: float = 0.0
    crest_factor: float = 0.0
    band_ratio: float = 0.0
    high_band_share: float = 0.0
    spectral_flatness: float = 0.0
    observed_gap_seconds: float = 0.32
    peak_min: float = 0.0
    peak_median: float = 0.0
    peak_max: float = 0.0
    rms_min: float = 0.0
    rms_median: float = 0.0
    rms_max: float = 0.0
    transient_min: float = 0.0
    transient_median: float = 0.0
    transient_max: float = 0.0


@dataclass
class ClapDetectorConfig:
    """Tunable thresholds for clap-specific audio detection."""

    sample_rate: int = 16_000
    block_duration: float = 0.025
    event_window_seconds: float = 0.050
    warmup_seconds: float = 1.0
    target_claps: int = 2
    clap_window_seconds: float = 2.2
    cooldown_seconds: float = 5.0
    min_clap_gap_seconds: float = 0.18
    refractory_seconds: float = 0.11
    min_peak: float = 0.08
    min_rms: float = 0.008
    min_transient: float = 0.018
    energy_ratio_threshold: float = 3.0
    transient_ratio_threshold: float = 2.5
    min_crest_factor: float = 3.0
    min_band_ratio: float = 1.4
    min_high_band_share: float = 0.30
    min_spectral_flatness: float = 0.22
    min_clap_score: float = 5.2
    noise_floor_alpha: float = 0.96
    band_low_hz: int = 1_400
    band_high_hz: int = 4_200
    low_band_max_hz: int = 900
    calibration_version: int = 1
    calibration_profile: ClapCalibrationProfile | None = None


@dataclass
class ClapUpdate:
    """Snapshot returned for each processed audio chunk."""

    status: str
    clap_count: int
    triggered: bool
    is_impulse: bool
    peak: float
    rms: float
    transient: float
    crest_factor: float
    band_ratio: float
    high_band_share: float
    spectral_flatness: float
    clap_score: float
    noise_floor: float
    transient_floor: float
    cooldown_remaining: float
    warmup_remaining: float
    decay_ratio: float
    event_state: str


class ClapDetector:
    """Counts clap-like audio impulses while suppressing duplicates and room noise."""

    def __init__(self, config: ClapDetectorConfig, sensitivity_preset: str = "balanced") -> None:
        self.base_config = config
        self.sensitivity_preset = sensitivity_preset if sensitivity_preset in {
            "balanced",
            "sensitive",
            "strict",
        } else "balanced"
        self.config = self._build_runtime_config(config, self.sensitivity_preset)
        self.clap_count = 0
        self.first_clap_at: float | None = None
        self.last_impulse_at: float | None = None
        self.cooldown_until = 0.0
        self.processed_seconds = 0.0
        self._analysis_buffer = np.zeros(0, dtype=np.float32)
        self._window_samples = max(1, int(self.config.sample_rate * self.config.event_window_seconds))
        self._confirm_decay_ratio = self._preset_decay_ratio(self.sensitivity_preset)
        self._candidate_max_seconds = max(self.config.event_window_seconds * 1.1, 0.055)
        self._profile = config.calibration_profile
        self._soft_peak_reference = self._profile_metric("peak_min", "clap_peak", fallback=self.config.min_peak * 1.2)
        self._median_peak_reference = self._profile_metric(
            "peak_median",
            "clap_peak",
            fallback=max(self._soft_peak_reference, self.config.min_peak * 1.8),
        )
        self._loud_peak_reference = self._profile_metric(
            "peak_max",
            "peak_median",
            "clap_peak",
            fallback=max(self._median_peak_reference, self.config.min_peak * 2.4),
        )
        self._compression_reference = max(self._median_peak_reference, self.config.min_peak * 1.6, 0.08)
        self.reset_runtime_state()

    # --- Runtime configuration ------------------------------------------

    def _build_runtime_config(
        self,
        config: ClapDetectorConfig,
        sensitivity_preset: str,
    ) -> ClapDetectorConfig:
        """Applies the saved calibration profile and sensitivity preset to one runtime copy."""

        runtime = replace(config)
        profile = config.calibration_profile
        if profile is not None and profile.captured_claps >= 3:
            peak_floor = max(profile.peak_min or 0.0, profile.clap_peak or 0.0, runtime.min_peak)
            peak_center = max(profile.peak_median or 0.0, profile.clap_peak or 0.0, peak_floor)
            rms_floor = max(profile.rms_min or 0.0, profile.clap_rms or 0.0, runtime.min_rms)
            rms_center = max(profile.rms_median or 0.0, profile.clap_rms or 0.0, rms_floor)
            transient_floor = max(
                profile.transient_min or 0.0,
                profile.clap_transient or 0.0,
                runtime.min_transient,
            )
            transient_center = max(
                profile.transient_median or 0.0,
                profile.clap_transient or 0.0,
                transient_floor,
            )
            runtime.min_peak = min(runtime.min_peak, max(profile.noise_rms * 5.0, peak_floor * 0.42, peak_center * 0.34))
            runtime.min_rms = min(runtime.min_rms, max(profile.noise_rms * 2.2, rms_floor * 0.58, rms_center * 0.46))
            runtime.min_transient = min(
                runtime.min_transient,
                max(profile.noise_transient * 2.2, transient_floor * 0.58, transient_center * 0.46),
            )
            runtime.min_crest_factor = min(runtime.min_crest_factor, max(2.25, profile.crest_factor * 0.84))
            runtime.min_band_ratio = min(runtime.min_band_ratio, max(1.18, profile.band_ratio * 0.84))
            runtime.min_high_band_share = min(
                runtime.min_high_band_share,
                max(0.21, profile.high_band_share * 0.86),
            )
            runtime.min_spectral_flatness = min(
                runtime.min_spectral_flatness,
                max(0.16, profile.spectral_flatness * 0.88),
            )
            runtime.min_clap_score = min(runtime.min_clap_score, max(3.6, profile.clap_score * 0.70))

            observed_gap = _clamp(profile.observed_gap_seconds, 0.16, 0.55)
            runtime.min_clap_gap_seconds = _clamp(observed_gap * 0.58, 0.10, 0.26)
            runtime.clap_window_seconds = _clamp(observed_gap + 0.60, 0.95, 1.8)

        if sensitivity_preset == "sensitive":
            runtime.min_peak *= 0.72
            runtime.min_rms *= 0.78
            runtime.min_transient *= 0.72
            runtime.energy_ratio_threshold = max(1.35, runtime.energy_ratio_threshold * 0.78)
            runtime.transient_ratio_threshold = max(1.35, runtime.transient_ratio_threshold * 0.78)
            runtime.min_crest_factor = max(1.95, runtime.min_crest_factor * 0.84)
            runtime.min_band_ratio = max(1.02, runtime.min_band_ratio * 0.82)
            runtime.min_high_band_share = max(0.16, runtime.min_high_band_share * 0.84)
            runtime.min_spectral_flatness = max(0.13, runtime.min_spectral_flatness * 0.84)
            runtime.min_clap_score = max(3.2, runtime.min_clap_score - 1.05)
            runtime.min_clap_gap_seconds = max(0.07, runtime.min_clap_gap_seconds * 0.78)
            runtime.clap_window_seconds = min(2.4, runtime.clap_window_seconds + 0.25)
        elif sensitivity_preset == "strict":
            runtime.min_peak *= 1.10
            runtime.min_rms *= 1.10
            runtime.min_transient *= 1.12
            runtime.energy_ratio_threshold *= 1.08
            runtime.transient_ratio_threshold *= 1.08
            runtime.min_clap_score += 0.55
            runtime.min_clap_gap_seconds = min(0.32, runtime.min_clap_gap_seconds * 1.08)
            runtime.clap_window_seconds = max(0.85, runtime.clap_window_seconds - 0.12)
        return runtime

    def _preset_decay_ratio(self, sensitivity_preset: str) -> float:
        """Returns the required onset-vs-tail decay ratio for a confirmed clap event."""

        if sensitivity_preset == "sensitive":
            return 0.94
        if sensitivity_preset == "strict":
            return 1.16
        return 1.02

    def _profile_metric(self, *names: str, fallback: float) -> float:
        """Returns the first non-zero calibration metric among several compatible field names."""

        if self._profile is None:
            return fallback
        for name in names:
            value = float(getattr(self._profile, name, 0.0) or 0.0)
            if value > 0.0:
                return value
        return fallback

    # --- Sequence helpers -----------------------------------------------

    def reset_runtime_state(self) -> None:
        """Resets detection progress when the service is re-armed or reloaded."""

        self.clap_count = 0
        self.first_clap_at = None
        self.last_impulse_at = None
        self.cooldown_until = 0.0
        self.processed_seconds = 0.0
        self._analysis_buffer = np.zeros(0, dtype=np.float32)
        profile = self.base_config.calibration_profile
        self.noise_floor = max(
            self.config.min_rms / 2.0,
            float(profile.noise_rms) if profile is not None else self.config.min_rms / 2.0,
        )
        self.transient_floor = max(
            self.config.min_transient / 2.0,
            float(profile.noise_transient) if profile is not None else self.config.min_transient / 2.0,
        )
        self._event_state = "idle"
        self._candidate_started_at = 0.0
        self._candidate_last_active_at = 0.0
        self._candidate_peak_at = 0.0
        self._candidate_best_features = self._empty_features()
        self._candidate_best_score = 0.0
        self._candidate_best_decay_ratio = 0.0
        self._candidate_best_impulse = False
        self._refractory_until = 0.0

    def _empty_features(self) -> dict[str, float]:
        """Builds one zeroed feature snapshot used while no event is active."""

        return {
            "peak": 0.0,
            "rms": 0.0,
            "transient": 0.0,
            "crest_factor": 0.0,
            "band_ratio": 0.0,
            "high_band_share": 0.0,
            "spectral_flatness": 0.0,
            "clap_score": 0.0,
            "decay_ratio": 0.0,
        }

    def _expire_sequence_if_needed(self, timestamp: float) -> None:
        """Drops stale clap progress once the multi-clap window has expired."""

        if self.first_clap_at is not None and timestamp - self.first_clap_at > self.config.clap_window_seconds:
            self._reset_sequence()

    def _reset_sequence(self) -> None:
        """Clears the current multi-clap progress without touching cooldown."""

        self.clap_count = 0
        self.first_clap_at = None

    def _register_clap(self, timestamp: float) -> bool:
        """Adds one clap event and returns True when the sequence triggers."""

        if self.first_clap_at is None or timestamp - self.first_clap_at > self.config.clap_window_seconds:
            self.clap_count = 1
            self.first_clap_at = timestamp
        else:
            self.clap_count += 1

        if self.clap_count >= self.config.target_claps:
            self.cooldown_until = timestamp + self.config.cooldown_seconds
            self._reset_sequence()
            return True

        return False

    # --- Feature extraction ---------------------------------------------

    def _to_signal(self, samples: Sequence[float]) -> np.ndarray:
        """Normalizes an incoming analysis window into a 1-D float32 numpy array."""

        signal = np.asarray(samples, dtype=np.float32).reshape(-1)
        if signal.size == 0:
            return signal

        centered = signal - np.mean(signal)
        if centered.size < 2:
            return centered

        # A tiny pre-emphasis stage highlights sharp clap transients cheaply.
        return centered[1:] - 0.97 * centered[:-1]

    def _compress_signal(self, signal: np.ndarray) -> np.ndarray:
        """Applies a lightweight fixed-reference compressor so soft and loud claps score more similarly."""

        if signal.size == 0:
            return signal

        reference = max(self._compression_reference, 1e-4)
        normalized = np.clip(signal / reference, -24.0, 24.0)
        compressed = np.sign(normalized) * np.sqrt(np.abs(normalized)) * reference
        # Blend back some raw signal so the clap keeps its natural transient shape.
        return (0.40 * signal) + (0.60 * compressed)

    def _analysis_window(self, samples: Sequence[float]) -> np.ndarray:
        """Maintains a rolling overlapped analysis window across input chunks."""

        chunk = np.asarray(samples, dtype=np.float32).reshape(-1)
        if chunk.size == 0:
            return self._analysis_buffer

        if self._analysis_buffer.size == 0:
            self._analysis_buffer = chunk
        else:
            self._analysis_buffer = np.concatenate([self._analysis_buffer, chunk])

        if self._analysis_buffer.size > self._window_samples:
            self._analysis_buffer = self._analysis_buffer[-self._window_samples :]
        return self._analysis_buffer

    def _compute_signal_features(self, signal: np.ndarray) -> tuple[float, float, float, float, float, float, float, float]:
        """Extracts clap-friendly time and frequency features from one preprocessed signal."""

        if signal.size == 0:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

        peak = float(np.max(np.abs(signal)))
        rms = float(np.sqrt(np.mean(signal * signal)))
        diffs = np.diff(signal)
        transient = float(np.sqrt(np.mean(diffs * diffs))) if diffs.size else 0.0
        crest_factor = peak / max(rms, 1e-6)

        split = max(1, int(signal.size * 0.35))
        head_rms = float(np.sqrt(np.mean(signal[:split] * signal[:split])))
        tail = signal[split:] if split < signal.size else signal[-1:]
        tail_rms = float(np.sqrt(np.mean(tail * tail)))
        decay_ratio = head_rms / max(tail_rms, 1e-6)

        windowed = signal * np.hanning(signal.size)
        spectrum = np.abs(np.fft.rfft(windowed))
        power = (spectrum * spectrum) + 1e-12
        freqs = np.fft.rfftfreq(signal.size, d=1.0 / self.config.sample_rate)

        band_mask = (freqs >= self.config.band_low_hz) & (freqs <= self.config.band_high_hz)
        low_mask = (freqs >= 80.0) & (freqs <= self.config.low_band_max_hz)
        total_power = float(np.sum(power[1:])) if power.size > 1 else float(np.sum(power))
        band_power = float(np.sum(power[band_mask]))
        low_power = float(np.sum(power[low_mask]))

        band_ratio = band_power / max(low_power, 1e-9)
        high_band_share = band_power / max(total_power, 1e-9)
        spectral_flatness = math.exp(float(np.mean(np.log(power)))) / float(np.mean(power))
        return peak, rms, transient, crest_factor, band_ratio, high_band_share, spectral_flatness, decay_ratio

    def _score_candidate(
        self,
        peak: float,
        rms: float,
        transient: float,
        crest_factor: float,
        band_ratio: float,
        high_band_share: float,
        spectral_flatness: float,
        decay_ratio: float,
        rms_threshold: float,
        transient_threshold: float,
        decay_threshold: float,
    ) -> float:
        """Builds one clap score from amplitude, shape, and spectral evidence."""

        return (
            min(peak / max(self.config.min_peak, 1e-6), 2.5)
            + min(rms / max(rms_threshold, 1e-6), 2.5)
            + min(transient / max(transient_threshold, 1e-6), 2.5)
            + min(crest_factor / max(self.config.min_crest_factor, 1e-6), 2.5)
            + min(band_ratio / max(self.config.min_band_ratio, 1e-6), 2.5)
            + min(high_band_share / max(self.config.min_high_band_share, 1e-6), 2.5)
            + min(spectral_flatness / max(self.config.min_spectral_flatness, 1e-6), 2.5)
            + min(decay_ratio / max(decay_threshold, 1e-6), 2.0)
        )

    def _update_background_floors(self, rms: float, transient: float) -> None:
        """Learns the room noise level only from non-impulse windows."""

        alpha = self.config.noise_floor_alpha
        self.noise_floor = alpha * self.noise_floor + (1.0 - alpha) * rms
        self.transient_floor = alpha * self.transient_floor + (1.0 - alpha) * transient

    # --- Event state machine --------------------------------------------

    def _start_candidate(self, timestamp: float, features: dict[str, float], impulse_candidate: bool) -> None:
        """Begins a short clap candidate event once the score spikes above the gate."""

        self._event_state = "candidate"
        self._candidate_started_at = timestamp
        self._candidate_last_active_at = timestamp
        self._candidate_peak_at = timestamp
        self._candidate_best_features = dict(features)
        self._candidate_best_score = features["clap_score"]
        self._candidate_best_decay_ratio = features["decay_ratio"]
        self._candidate_best_impulse = impulse_candidate

    def _update_candidate(self, timestamp: float, features: dict[str, float], active: bool, impulse_candidate: bool) -> None:
        """Refreshes the best candidate snapshot while the clap event is unfolding."""

        if active:
            self._candidate_last_active_at = timestamp
        if features["clap_score"] >= self._candidate_best_score:
            self._candidate_peak_at = timestamp
            self._candidate_best_features = dict(features)
            self._candidate_best_score = features["clap_score"]
            self._candidate_best_decay_ratio = features["decay_ratio"]
            self._candidate_best_impulse = impulse_candidate

    def _confirm_candidate(self, timestamp: float) -> tuple[bool, bool]:
        """Ends one candidate event and returns `(is_impulse, triggered)`."""

        is_impulse = False
        triggered = False
        enough_gap = (
            self.last_impulse_at is None
            or self._candidate_peak_at - self.last_impulse_at >= self.config.min_clap_gap_seconds
        )
        confirmed = (
            self._candidate_best_impulse
            and self._candidate_best_score >= self.config.min_clap_score
            and self._candidate_best_decay_ratio >= self._confirm_decay_ratio
            and enough_gap
        )
        if confirmed:
            is_impulse = True
            self.last_impulse_at = self._candidate_peak_at
            triggered = self._register_clap(self._candidate_peak_at)
            self._event_state = "refractory"
            self._refractory_until = timestamp + self.config.refractory_seconds
        else:
            self._event_state = "idle"

        self._candidate_started_at = 0.0
        self._candidate_last_active_at = 0.0
        self._candidate_peak_at = 0.0
        self._candidate_best_features = self._empty_features()
        self._candidate_best_score = 0.0
        self._candidate_best_decay_ratio = 0.0
        self._candidate_best_impulse = False
        return is_impulse, triggered

    # --- Chunk processing ------------------------------------------------

    def process_chunk(self, samples: Sequence[float], timestamp: float) -> ClapUpdate:
        """Processes one audio block and returns the latest detector state."""

        analysis_window = self._analysis_window(samples)
        signal = self._to_signal(analysis_window)
        peak, rms, transient, crest_factor, band_ratio, high_band_share, spectral_flatness, decay_ratio = (
            self._compute_signal_features(signal)
        )
        compressed_signal = self._compress_signal(signal)
        (
            soft_peak,
            soft_rms,
            soft_transient,
            soft_crest_factor,
            _soft_band_ratio,
            _soft_high_band_share,
            _soft_spectral_flatness,
            soft_decay_ratio,
        ) = self._compute_signal_features(compressed_signal)

        sample_count = len(samples)
        self.processed_seconds += sample_count / max(self.config.sample_rate, 1)
        warmup_remaining = max(0.0, self.config.warmup_seconds - self.processed_seconds)

        cooldown_remaining = max(0.0, self.cooldown_until - timestamp)
        if cooldown_remaining == 0.0 and self.cooldown_until > 0.0:
            self.cooldown_until = 0.0

        if self._event_state == "refractory" and timestamp >= self._refractory_until:
            self._event_state = "idle"
            self._refractory_until = 0.0

        self._expire_sequence_if_needed(timestamp)

        rms_threshold = max(self.config.min_rms, self.noise_floor * self.config.energy_ratio_threshold)
        transient_threshold = max(
            self.config.min_transient,
            self.transient_floor * self.config.transient_ratio_threshold,
        )
        raw_score = self._score_candidate(
            peak,
            rms,
            transient,
            crest_factor,
            band_ratio,
            high_band_share,
            spectral_flatness,
            decay_ratio,
            rms_threshold,
            transient_threshold,
            self._confirm_decay_ratio,
        )
        soft_score = self._score_candidate(
            soft_peak,
            soft_rms,
            soft_transient,
            soft_crest_factor,
            band_ratio,
            high_band_share,
            spectral_flatness,
            max(decay_ratio, soft_decay_ratio),
            rms_threshold * 0.84,
            transient_threshold * 0.84,
            self._confirm_decay_ratio * 0.94,
        )
        loud_bonus = 0.0
        if peak >= self._loud_peak_reference * 0.85:
            loud_bonus += 0.55
        if peak >= self._loud_peak_reference * 1.05:
            loud_bonus += 0.45
        if peak >= 0.92:
            loud_bonus += 0.35
        loud_score = raw_score + loud_bonus
        clap_score = max(raw_score, soft_score, loud_score)
        strict_impulse_candidate = (
            warmup_remaining <= 0.0
            and peak >= self.config.min_peak
            and rms >= rms_threshold
            and transient >= transient_threshold
            and crest_factor >= self.config.min_crest_factor
            and band_ratio >= self.config.min_band_ratio
            and high_band_share >= self.config.min_high_band_share
            and spectral_flatness >= self.config.min_spectral_flatness
            and raw_score >= self.config.min_clap_score
        )
        soft_impulse_candidate = False
        if self.sensitivity_preset != "strict":
            soft_spectral_votes = sum(
                (
                    band_ratio >= self.config.min_band_ratio * 0.72,
                    high_band_share >= self.config.min_high_band_share * 0.74,
                    spectral_flatness >= self.config.min_spectral_flatness * 0.74,
                )
            )
            soft_impulse_candidate = (
                warmup_remaining <= 0.0
                and soft_peak >= self.config.min_peak * 0.74
                and soft_rms >= rms_threshold * 0.66
                and soft_transient >= transient_threshold * 0.64
                and soft_crest_factor >= self.config.min_crest_factor * 0.74
                and soft_score >= self.config.min_clap_score * 0.74
                and max(decay_ratio, soft_decay_ratio) >= self._confirm_decay_ratio * 0.90
                and soft_spectral_votes >= 2
            )
        loud_spectral_votes = sum(
            (
                band_ratio >= self.config.min_band_ratio * 0.58,
                high_band_share >= self.config.min_high_band_share * 0.58,
                spectral_flatness >= self.config.min_spectral_flatness * 0.54,
            )
        )
        loud_impulse_candidate = (
            warmup_remaining <= 0.0
            and peak >= max(self.config.min_peak * 1.18, self._loud_peak_reference * 0.74)
            and transient >= transient_threshold * 0.74
            and loud_score >= self.config.min_clap_score * 0.66
            and decay_ratio >= self._confirm_decay_ratio * 0.82
            and loud_spectral_votes >= 1
        )
        impulse_candidate = strict_impulse_candidate or soft_impulse_candidate or loud_impulse_candidate
        candidate_active = (
            impulse_candidate
            or clap_score >= self.config.min_clap_score * 0.56
            or soft_transient >= transient_threshold * 0.60
            or peak >= self.config.min_peak * 0.66
        )

        current_features = {
            "peak": peak,
            "rms": rms,
            "transient": transient,
            "crest_factor": crest_factor,
            "band_ratio": band_ratio,
            "high_band_share": high_band_share,
            "spectral_flatness": spectral_flatness,
            "clap_score": clap_score,
            "decay_ratio": decay_ratio,
        }

        if warmup_remaining > 0.0 or not candidate_active:
            self._update_background_floors(rms, transient)

        if cooldown_remaining > 0.0:
            return ClapUpdate(
                status="cooldown",
                clap_count=self.clap_count,
                triggered=False,
                is_impulse=False,
                peak=peak,
                rms=rms,
                transient=transient,
                crest_factor=crest_factor,
                band_ratio=band_ratio,
                high_band_share=high_band_share,
                spectral_flatness=spectral_flatness,
                clap_score=clap_score,
                noise_floor=self.noise_floor,
                transient_floor=self.transient_floor,
                cooldown_remaining=cooldown_remaining,
                warmup_remaining=warmup_remaining,
                decay_ratio=decay_ratio,
                event_state=self._event_state,
            )

        is_impulse = False
        triggered = False
        emit_features = current_features

        if self._event_state == "idle" and candidate_active:
            self._start_candidate(timestamp, current_features, impulse_candidate)
        elif self._event_state == "candidate":
            self._update_candidate(timestamp, current_features, candidate_active, impulse_candidate)
            candidate_age = timestamp - self._candidate_started_at
            quiet_for = timestamp - self._candidate_last_active_at
            if candidate_age >= self._candidate_max_seconds or quiet_for >= self.config.block_duration:
                emit_features = dict(self._candidate_best_features)
                is_impulse, triggered = self._confirm_candidate(timestamp)
        elif self._event_state == "refractory" and timestamp < self._refractory_until:
            emit_features = dict(self._candidate_best_features)

        if warmup_remaining > 0.0:
            status = "warmup"
        elif triggered:
            status = "triggered"
        elif self._event_state == "candidate":
            status = "candidate"
        elif self.clap_count > 0:
            status = f"clap {self.clap_count}/{self.config.target_claps}"
        else:
            status = "listening"

        return ClapUpdate(
            status=status,
            clap_count=self.clap_count,
            triggered=triggered,
            is_impulse=is_impulse,
            peak=emit_features["peak"],
            rms=emit_features["rms"],
            transient=emit_features["transient"],
            crest_factor=emit_features["crest_factor"],
            band_ratio=emit_features["band_ratio"],
            high_band_share=emit_features["high_band_share"],
            spectral_flatness=emit_features["spectral_flatness"],
            clap_score=emit_features["clap_score"],
            noise_floor=self.noise_floor,
            transient_floor=self.transient_floor,
            cooldown_remaining=max(0.0, self.cooldown_until - timestamp),
            warmup_remaining=warmup_remaining,
            decay_ratio=emit_features["decay_ratio"],
            event_state=self._event_state,
        )
