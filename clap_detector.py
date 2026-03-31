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
    zero_crossing_rate: float = 0.0
    spectral_centroid: float = 0.0
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
    min_zero_crossing_rate: float = 0.28
    min_spectral_centroid_hz: float = 2200.0
    min_clap_score: float = 6.2
    noise_floor_alpha: float = 0.96
    band_low_hz: int = 1_400
    band_high_hz: int = 4_200
    low_band_max_hz: int = 900
    min_inter_clap_similarity: float = 0.40
    max_recent_transient_rate: float = 3.0
    recent_transient_window: float = 5.0
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
    zero_crossing_rate: float
    spectral_centroid: float
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
            "responsive",
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
            runtime.min_clap_score = min(runtime.min_clap_score, max(4.2, profile.clap_score * 0.70))

            if profile.zero_crossing_rate > 0.0:
                runtime.min_zero_crossing_rate = min(
                    runtime.min_zero_crossing_rate,
                    max(0.18, profile.zero_crossing_rate * 0.80),
                )
            if profile.spectral_centroid > 0.0:
                runtime.min_spectral_centroid_hz = min(
                    runtime.min_spectral_centroid_hz,
                    max(1500.0, profile.spectral_centroid * 0.75),
                )

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
            runtime.min_clap_score = max(3.8, runtime.min_clap_score - 1.05)
            runtime.min_clap_gap_seconds = max(0.07, runtime.min_clap_gap_seconds * 0.78)
            runtime.clap_window_seconds = min(2.4, runtime.clap_window_seconds + 0.25)
            runtime.min_zero_crossing_rate = max(0.18, runtime.min_zero_crossing_rate * 0.82)
            runtime.min_spectral_centroid_hz = max(1600.0, runtime.min_spectral_centroid_hz * 0.82)
            runtime.min_inter_clap_similarity = max(0.25, runtime.min_inter_clap_similarity * 0.78)
        elif sensitivity_preset == "responsive":
            runtime.min_peak *= 0.86
            runtime.min_rms *= 0.89
            runtime.min_transient *= 0.86
            runtime.energy_ratio_threshold = max(1.48, runtime.energy_ratio_threshold * 0.89)
            runtime.transient_ratio_threshold = max(1.48, runtime.transient_ratio_threshold * 0.89)
            runtime.min_crest_factor = max(2.00, runtime.min_crest_factor * 0.90)
            runtime.min_band_ratio = max(1.08, runtime.min_band_ratio * 0.90)
            runtime.min_high_band_share = max(0.17, runtime.min_high_band_share * 0.92)
            runtime.min_spectral_flatness = max(0.140, runtime.min_spectral_flatness * 0.92)
            runtime.min_clap_score = max(4.0, runtime.min_clap_score - 0.60)
            runtime.min_clap_gap_seconds = max(0.15, runtime.min_clap_gap_seconds * 0.90)
            runtime.clap_window_seconds = min(1.75, runtime.clap_window_seconds + 0.12)
            runtime.min_zero_crossing_rate = max(0.20, runtime.min_zero_crossing_rate * 0.88)
            runtime.min_spectral_centroid_hz = max(1800.0, runtime.min_spectral_centroid_hz * 0.88)
            runtime.min_inter_clap_similarity = max(0.30, runtime.min_inter_clap_similarity * 0.85)
        elif sensitivity_preset == "strict":
            runtime.min_peak *= 1.10
            runtime.min_rms *= 1.10
            runtime.min_transient *= 1.12
            runtime.energy_ratio_threshold *= 1.08
            runtime.transient_ratio_threshold *= 1.08
            runtime.min_clap_score += 0.55
            runtime.min_clap_gap_seconds = min(0.32, runtime.min_clap_gap_seconds * 1.08)
            runtime.clap_window_seconds = max(0.85, runtime.clap_window_seconds - 0.12)
            runtime.min_zero_crossing_rate *= 1.06
            runtime.min_spectral_centroid_hz *= 1.06
            runtime.min_inter_clap_similarity = min(0.65, runtime.min_inter_clap_similarity * 1.10)
        else:
            # Balanced should still feel usable, but random external transients should have a harder time pairing up.
            runtime.min_clap_score += 0.20
            runtime.min_clap_gap_seconds = max(0.20, runtime.min_clap_gap_seconds)
            runtime.clap_window_seconds = min(1.45, runtime.clap_window_seconds)
        return runtime

    def _preset_decay_ratio(self, sensitivity_preset: str) -> float:
        """Returns the required onset-vs-tail decay ratio for a confirmed clap event."""

        if sensitivity_preset == "sensitive":
            return 0.94
        if sensitivity_preset == "responsive":
            return 0.95
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

    def _minimum_broadband_presence(self) -> float:
        """Returns the minimum high-band-presence product required for one clap-like impulse."""

        baseline = max(0.060, self.config.min_high_band_share * self.config.min_spectral_flatness * 0.95)
        if self.sensitivity_preset == "sensitive":
            return baseline * 0.92
        if self.sensitivity_preset == "responsive":
            return baseline * 0.94
        if self.sensitivity_preset == "strict":
            return baseline * 1.08
        return baseline

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
        self.ambience_rms_floor = self.noise_floor
        self.ambience_transient_floor = self.transient_floor
        self._event_state = "idle"
        self._candidate_started_at = 0.0
        self._candidate_last_active_at = 0.0
        self._candidate_peak_at = 0.0
        self._candidate_best_features = self._empty_features()
        self._candidate_best_score = 0.0
        self._candidate_best_decay_ratio = 0.0
        self._candidate_best_impulse = False
        self._refractory_until = 0.0
        self._candidate_spectral_envelope: np.ndarray | None = None
        self._first_clap_spectral_envelope: np.ndarray | None = None
        self._recent_transient_timestamps: list[float] = []
        self._density_penalty: float = 0.0

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
            "zero_crossing_rate": 0.0,
            "spectral_centroid": 0.0,
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
        self._first_clap_spectral_envelope = None

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

    def _compute_signal_features(
        self, signal: np.ndarray
    ) -> tuple[float, float, float, float, float, float, float, float, float, float]:
        """Extracts clap-friendly time and frequency features from one preprocessed signal."""

        if signal.size == 0:
            return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

        peak = float(np.max(np.abs(signal)))
        rms = float(np.sqrt(np.mean(signal * signal)))
        diffs = np.diff(signal)
        transient = float(np.sqrt(np.mean(diffs * diffs))) if diffs.size else 0.0
        crest_factor = peak / max(rms, 1e-6)

        # Zero-crossing rate: high for noise-like signals (claps), low for tonal (speech, drums).
        if signal.size > 1:
            zero_crossing_rate = float(np.sum(signal[:-1] * signal[1:] < 0)) / (signal.size - 1)
        else:
            zero_crossing_rate = 0.0

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

        # Spectral centroid: high for broadband claps, low for tonal speech/drums.
        if total_power > 1e-9 and freqs.size > 1:
            spectral_centroid = float(np.sum(freqs[1:] * power[1:])) / max(float(np.sum(power[1:])), 1e-12)
        else:
            spectral_centroid = 0.0

        return peak, rms, transient, crest_factor, band_ratio, high_band_share, spectral_flatness, decay_ratio, zero_crossing_rate, spectral_centroid

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
        zero_crossing_rate: float,
        spectral_centroid: float,
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
            + min(zero_crossing_rate / max(self.config.min_zero_crossing_rate, 1e-6), 2.0)
            + min(spectral_centroid / max(self.config.min_spectral_centroid_hz, 1.0), 1.5)
        )

    # --- Spectral similarity --------------------------------------------

    def _compute_spectral_envelope(self, signal: np.ndarray) -> np.ndarray:
        """Computes a normalized spectral envelope for inter-clap similarity comparison."""

        fft_size = self._window_samples
        n_bins = fft_size // 2 + 1
        if signal.size < 2:
            return np.zeros(n_bins, dtype=np.float32)
        # Zero-pad or trim to a fixed length so all envelopes share the same bin count.
        if signal.size < fft_size:
            padded = np.zeros(fft_size, dtype=np.float32)
            padded[:signal.size] = signal
        else:
            padded = signal[-fft_size:]
        windowed = padded * np.hanning(fft_size)
        spectrum = np.abs(np.fft.rfft(windowed))
        power = spectrum * spectrum + 1e-12
        total = float(np.sum(power))
        if total > 0:
            return (power / total).astype(np.float32)
        return power.astype(np.float32)

    @staticmethod
    def _spectral_similarity(env1: np.ndarray, env2: np.ndarray) -> float:
        """Cosine similarity between two normalized spectral envelopes."""

        if env1.size != env2.size or env1.size == 0:
            return 0.0
        dot = float(np.dot(env1, env2))
        norm1 = float(np.sqrt(np.dot(env1, env1)))
        norm2 = float(np.sqrt(np.dot(env2, env2)))
        if norm1 < 1e-12 or norm2 < 1e-12:
            return 0.0
        return dot / (norm1 * norm2)

    # --- Transient density tracking -------------------------------------

    def _track_transient_density(self, timestamp: float, is_energetic: bool) -> float:
        """Tracks the rate of energetic audio frames to detect rhythmic music patterns."""

        if is_energetic:
            self._recent_transient_timestamps.append(timestamp)
        cutoff = timestamp - self.config.recent_transient_window
        self._recent_transient_timestamps = [t for t in self._recent_transient_timestamps if t >= cutoff]
        if len(self._recent_transient_timestamps) < 2:
            return 0.0
        window = timestamp - self._recent_transient_timestamps[0]
        if window < 0.5:
            return 0.0
        return len(self._recent_transient_timestamps) / window

    # --- Background floor tracking --------------------------------------

    def _update_background_floors(self, rms: float, transient: float) -> None:
        """Learns the room noise level only from non-impulse windows."""

        alpha = self.config.noise_floor_alpha
        self.noise_floor = alpha * self.noise_floor + (1.0 - alpha) * rms
        self.transient_floor = alpha * self.transient_floor + (1.0 - alpha) * transient

    def _update_ambience_floors(self, rms: float, transient: float) -> None:
        """Tracks slower room ambience so continuous music raises the runtime gate."""

        alpha = max(self.config.noise_floor_alpha, 0.985)
        self.ambience_rms_floor = alpha * self.ambience_rms_floor + (1.0 - alpha) * rms
        self.ambience_transient_floor = alpha * self.ambience_transient_floor + (1.0 - alpha) * transient

    def _ambient_energy_multiplier(self) -> float:
        """Returns how much stronger one event must be than the recent ambience floor."""

        if self.sensitivity_preset == "sensitive":
            return 1.60
        if self.sensitivity_preset == "responsive":
            return 1.72
        if self.sensitivity_preset == "strict":
            return 2.00
        return 1.85

    def _ambient_transient_multiplier(self) -> float:
        """Returns how much sharper one event must be than the recent ambience transient floor."""

        if self.sensitivity_preset == "sensitive":
            return 1.45
        if self.sensitivity_preset == "responsive":
            return 1.52
        if self.sensitivity_preset == "strict":
            return 1.75
        return 1.65

    # --- Event state machine --------------------------------------------

    def _start_candidate(
        self,
        timestamp: float,
        features: dict[str, float],
        impulse_candidate: bool,
        spectral_envelope: np.ndarray,
    ) -> None:
        """Begins a short clap candidate event once the score spikes above the gate."""

        self._event_state = "candidate"
        self._candidate_started_at = timestamp
        self._candidate_last_active_at = timestamp
        self._candidate_peak_at = timestamp
        self._candidate_best_features = dict(features)
        self._candidate_best_score = features["clap_score"]
        self._candidate_best_decay_ratio = features["decay_ratio"]
        self._candidate_best_impulse = impulse_candidate
        self._candidate_spectral_envelope = spectral_envelope

    def _update_candidate(
        self,
        timestamp: float,
        features: dict[str, float],
        active: bool,
        impulse_candidate: bool,
        spectral_envelope: np.ndarray,
    ) -> None:
        """Refreshes the best candidate snapshot while the clap event is unfolding."""

        if active:
            self._candidate_last_active_at = timestamp
        should_replace_best = False
        if impulse_candidate and (not self._candidate_best_impulse or features["clap_score"] >= self._candidate_best_score):
            should_replace_best = True
        elif not self._candidate_best_impulse and features["clap_score"] >= self._candidate_best_score:
            should_replace_best = True
        if should_replace_best:
            self._candidate_peak_at = timestamp
            self._candidate_best_features = dict(features)
            self._candidate_best_score = features["clap_score"]
            self._candidate_best_decay_ratio = features["decay_ratio"]
            self._candidate_best_impulse = impulse_candidate
            self._candidate_spectral_envelope = spectral_envelope

    def _confirm_candidate(self, timestamp: float) -> tuple[bool, bool]:
        """Ends one candidate event and returns `(is_impulse, triggered)`."""

        is_impulse = False
        triggered = False
        enough_gap = (
            self.last_impulse_at is None
            or self._candidate_peak_at - self.last_impulse_at >= self.config.min_clap_gap_seconds
        )

        # Inter-clap spectral similarity: reject second clap if its spectral shape
        # does not resemble the first (e.g. two random music transients).
        similar_enough = True
        if (
            self.clap_count >= 1
            and self._first_clap_spectral_envelope is not None
            and self._candidate_spectral_envelope is not None
        ):
            similarity = self._spectral_similarity(
                self._first_clap_spectral_envelope,
                self._candidate_spectral_envelope,
            )
            similar_enough = similarity >= self.config.min_inter_clap_similarity

        # Raise the effective score threshold when there is high transient activity.
        effective_min_score = self.config.min_clap_score + self._density_penalty

        confirmed = (
            self._candidate_best_impulse
            and self._candidate_best_score >= effective_min_score
            and self._candidate_best_decay_ratio >= self._confirm_decay_ratio
            and enough_gap
            and similar_enough
        )
        if confirmed:
            is_impulse = True
            self.last_impulse_at = self._candidate_peak_at
            # Store spectral envelope of first clap for similarity comparison.
            if self.clap_count == 0:
                self._first_clap_spectral_envelope = self._candidate_spectral_envelope
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
        self._candidate_spectral_envelope = None
        return is_impulse, triggered

    # --- Chunk processing ------------------------------------------------

    def process_chunk(self, samples: Sequence[float], timestamp: float) -> ClapUpdate:
        """Processes one audio block and returns the latest detector state."""

        analysis_window = self._analysis_window(samples)
        signal = self._to_signal(analysis_window)
        (
            peak, rms, transient, crest_factor,
            band_ratio, high_band_share, spectral_flatness, decay_ratio,
            zero_crossing_rate, spectral_centroid,
        ) = self._compute_signal_features(signal)
        compressed_signal = self._compress_signal(signal)
        (
            soft_peak, soft_rms, soft_transient, soft_crest_factor,
            _soft_band_ratio, _soft_high_band_share, _soft_spectral_flatness, soft_decay_ratio,
            _soft_zero_crossing_rate, _soft_spectral_centroid,
        ) = self._compute_signal_features(compressed_signal)
        effective_decay_ratio = max(decay_ratio, soft_decay_ratio)

        spectral_envelope = self._compute_spectral_envelope(signal)

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

        # Track transient density to detect rhythmic music and raise the bar.
        is_energetic_frame = (
            peak >= self.config.min_peak * 0.4
            and transient >= self.config.min_transient * 0.3
        )
        transient_density = self._track_transient_density(timestamp, is_energetic_frame)
        if transient_density > self.config.max_recent_transient_rate:
            excess = transient_density - self.config.max_recent_transient_rate
            self._density_penalty = min(excess * 0.8, 3.0)
        else:
            self._density_penalty = 0.0

        rms_threshold = max(self.config.min_rms, self.noise_floor * self.config.energy_ratio_threshold)
        rms_threshold = max(rms_threshold, self.ambience_rms_floor * self._ambient_energy_multiplier())
        transient_threshold = max(
            self.config.min_transient,
            self.transient_floor * self.config.transient_ratio_threshold,
        )
        transient_threshold = max(
            transient_threshold,
            self.ambience_transient_floor * self._ambient_transient_multiplier(),
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
            zero_crossing_rate,
            spectral_centroid,
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
            effective_decay_ratio,
            zero_crossing_rate,
            spectral_centroid,
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
        # Keyboard ticks and snaps can look "sharp" but still lack the broadband energy spread of a real clap.
        broadband_high_share_floor = max(0.18, self.config.min_high_band_share * 0.74)
        broadband_presence = high_band_share * spectral_flatness
        broadband_presence_floor = self._minimum_broadband_presence()
        strict_impulse_candidate = (
            warmup_remaining <= 0.0
            and peak >= self.config.min_peak
            and rms >= rms_threshold
            and transient >= transient_threshold
            and crest_factor >= self.config.min_crest_factor
            and band_ratio >= self.config.min_band_ratio
            and high_band_share >= self.config.min_high_band_share
            and spectral_flatness >= self.config.min_spectral_flatness
            and zero_crossing_rate >= self.config.min_zero_crossing_rate
            and spectral_centroid >= self.config.min_spectral_centroid_hz
            and broadband_presence >= broadband_presence_floor
            and raw_score >= self.config.min_clap_score
        )
        soft_impulse_candidate = False
        if self.sensitivity_preset != "strict":
            soft_spectral_votes = sum(
                (
                    band_ratio >= self.config.min_band_ratio * 0.72,
                    high_band_share >= self.config.min_high_band_share * 0.74,
                    spectral_flatness >= self.config.min_spectral_flatness * 0.74,
                    zero_crossing_rate >= self.config.min_zero_crossing_rate * 0.74,
                    spectral_centroid >= self.config.min_spectral_centroid_hz * 0.74,
                )
            )
            soft_impulse_candidate = (
                warmup_remaining <= 0.0
                and soft_peak >= self.config.min_peak * 0.74
                and soft_rms >= rms_threshold * 0.66
                and soft_transient >= transient_threshold * 0.64
                and soft_crest_factor >= self.config.min_crest_factor * 0.74
                and high_band_share >= broadband_high_share_floor
                and broadband_presence >= broadband_presence_floor
                and soft_score >= self.config.min_clap_score * 0.74
                and effective_decay_ratio >= self._confirm_decay_ratio * 0.90
                and soft_spectral_votes >= 4
            )
        loud_spectral_votes = sum(
            (
                band_ratio >= self.config.min_band_ratio * 0.58,
                high_band_share >= self.config.min_high_band_share * 0.58,
                spectral_flatness >= self.config.min_spectral_flatness * 0.54,
                zero_crossing_rate >= self.config.min_zero_crossing_rate * 0.58,
                spectral_centroid >= self.config.min_spectral_centroid_hz * 0.58,
            )
        )
        loud_impulse_candidate = (
            warmup_remaining <= 0.0
            and peak >= max(self.config.min_peak * 1.18, self._loud_peak_reference * 0.74)
            and transient >= transient_threshold * 0.74
            and high_band_share >= broadband_high_share_floor
            and broadband_presence >= broadband_presence_floor
            and loud_score >= self.config.min_clap_score * 0.66
            and decay_ratio >= self._confirm_decay_ratio * 0.82
            and loud_spectral_votes >= 3
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
            "zero_crossing_rate": zero_crossing_rate,
            "spectral_centroid": spectral_centroid,
            "clap_score": clap_score,
            "decay_ratio": effective_decay_ratio,
        }

        self._update_ambience_floors(rms, transient)
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
                zero_crossing_rate=zero_crossing_rate,
                spectral_centroid=spectral_centroid,
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
            self._start_candidate(timestamp, current_features, impulse_candidate, spectral_envelope)
        elif self._event_state == "candidate":
            self._update_candidate(timestamp, current_features, candidate_active, impulse_candidate, spectral_envelope)
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
            zero_crossing_rate=emit_features["zero_crossing_rate"],
            spectral_centroid=emit_features["spectral_centroid"],
            clap_score=emit_features["clap_score"],
            noise_floor=self.noise_floor,
            transient_floor=self.transient_floor,
            cooldown_remaining=max(0.0, self.cooldown_until - timestamp),
            warmup_remaining=warmup_remaining,
            decay_ratio=emit_features["decay_ratio"],
            event_state=self._event_state,
        )
