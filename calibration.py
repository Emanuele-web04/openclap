"""
FILE: calibration.py
Purpose: Collects a short silence sample plus repeated clap events to build one
personalized calibration profile for the clap detector.
Depends on: numpy plus clap_detector.py feature snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import List

import numpy as np

from clap_detector import ClapCalibrationProfile, ClapUpdate


@dataclass
class CalibrationProgress:
    """Live progress snapshot exposed to the daemon status and menu bar."""

    state: str = "idle"
    instruction: str = "Idle"
    captured_claps: int = 0
    target_claps: int = 6
    silence_remaining: float = 0.0
    last_completed_at: float | None = None
    result_message: str = "Not calibrated yet"


class CalibrationSession:
    """Runs a short guided calibration without taking microphone ownership away from the daemon."""

    def __init__(
        self,
        silence_seconds: float = 1.0,
        target_claps: int = 6,
        max_capture_seconds: float = 12.0,
    ) -> None:
        self.silence_seconds = silence_seconds
        self.target_claps = target_claps
        self.max_capture_seconds = max_capture_seconds
        self._stages = [("soft", 2), ("normal", 2), ("loud", 2)]
        self.progress = CalibrationProgress(
            state="silence",
            instruction=f"Stay quiet for {silence_seconds:.1f}s",
            target_claps=target_claps,
            silence_remaining=silence_seconds,
        )
        self._started_at: float | None = None
        self._clap_phase_started_at: float | None = None
        self._silence_updates: List[ClapUpdate] = []
        self._clap_updates: List[ClapUpdate] = []
        self._clap_times: List[float] = []
        self.profile: ClapCalibrationProfile | None = None
        self._capture_peak_threshold = 0.045
        self._capture_transient_threshold = 0.010
        self._capture_score_threshold = 3.6

    # --- Lifecycle ------------------------------------------------------

    def start(self, timestamp: float) -> None:
        """Initializes the calibration clock when the daemon arms the wizard."""

        self._started_at = timestamp

    def is_active(self) -> bool:
        """Returns True while calibration is still collecting audio."""

        return self.progress.state in {"silence", "claps"}

    def observe(self, update: ClapUpdate, timestamp: float) -> None:
        """Consumes one detector update and advances the guided calibration state."""

        if self._started_at is None:
            self.start(timestamp)

        if self.progress.state == "silence":
            self._observe_silence(update, timestamp)
            return

        if self.progress.state == "claps":
            self._observe_claps(update, timestamp)

    # --- Phase handlers -------------------------------------------------

    def _observe_silence(self, update: ClapUpdate, timestamp: float) -> None:
        """Collects a short baseline of room noise before the clap samples start."""

        self._silence_updates.append(update)
        started_at = self._started_at if self._started_at is not None else timestamp
        elapsed = timestamp - float(started_at)
        remaining = max(0.0, self.silence_seconds - elapsed)
        self.progress.silence_remaining = remaining
        if remaining > 0.0:
            self.progress.instruction = f"Stay quiet for {remaining:.1f}s"
            return

        self.progress.state = "claps"
        self._capture_peak_threshold = max(
            0.045,
            self._percentile([item.peak for item in self._silence_updates], 85.0, 0.010) * 3.8,
        )
        self._capture_transient_threshold = max(
            0.010,
            self._percentile([item.transient for item in self._silence_updates], 85.0, 0.006) * 2.2,
        )
        self._capture_score_threshold = max(
            3.2,
            self._percentile([item.clap_score for item in self._silence_updates], 90.0, 1.5) + 1.8,
        )
        self.progress.instruction = self._stage_instruction()
        self._clap_phase_started_at = timestamp

    def _observe_claps(self, update: ClapUpdate, timestamp: float) -> None:
        """Captures clap events and finishes once enough user-specific examples are stored."""

        started_at = self._clap_phase_started_at if self._clap_phase_started_at is not None else timestamp
        if timestamp - started_at > self.max_capture_seconds:
            self.progress.state = "failed"
            self.progress.instruction = "Calibration timed out"
            self.progress.result_message = "Timed out before capturing enough claps"
            return

        if not self._is_calibration_clap(update):
            self.progress.instruction = self._stage_instruction()
            return

        if self._clap_times and timestamp - self._clap_times[-1] < 0.14:
            return

        self._clap_updates.append(update)
        self._clap_times.append(timestamp)
        self.progress.captured_claps = len(self._clap_updates)
        self.progress.instruction = self._stage_instruction(prefix="Captured")

        if self.progress.captured_claps < self.target_claps:
            return

        self.profile = build_calibration_profile(self._silence_updates, self._clap_updates, self._clap_times)
        self.progress.state = "complete"
        self.progress.last_completed_at = self.profile.calibrated_at
        self.progress.instruction = "Calibration complete"
        self.progress.result_message = (
            f"Saved profile from {self.progress.captured_claps} claps"
        )

    # --- Capture heuristics --------------------------------------------

    def _is_calibration_clap(self, update: ClapUpdate) -> bool:
        """Uses a slightly looser gate than normal runtime detection during the wizard."""

        if update.is_impulse:
            return True

        stage = self._current_stage_name()
        peak_scale = 0.84 if stage == "soft" else 1.0
        transient_scale = 0.82 if stage == "soft" else 1.0
        score_scale = 0.86 if stage == "soft" else 1.0
        return (
            update.peak >= self._capture_peak_threshold * peak_scale
            and update.transient >= self._capture_transient_threshold * transient_scale
            and update.clap_score >= self._capture_score_threshold * score_scale
            and update.decay_ratio >= 0.92
            and update.band_ratio >= 1.02
            and update.high_band_share >= 0.15
            and update.event_state == "candidate"
        )

    def _current_stage_name(self) -> str:
        """Returns which clap intensity the wizard is currently collecting."""

        remaining = self.progress.captured_claps
        for stage_name, stage_count in self._stages:
            if remaining < stage_count:
                return stage_name
            remaining -= stage_count
        return self._stages[-1][0]

    def _stage_instruction(self, prefix: str | None = None) -> str:
        """Builds one user-facing instruction for the current clap intensity stage."""

        completed = self.progress.captured_claps
        offset = 0
        for stage_name, stage_count in self._stages:
            if completed < offset + stage_count:
                stage_index = completed - offset
                action = "Do" if prefix is None else prefix
                return f"{action} {stage_name} clap {stage_index + 1}/{stage_count}"
            offset += stage_count
        return "Calibration complete"

    def _percentile(self, values: List[float], q: float, fallback: float) -> float:
        """Returns one percentile over a short calibration feature list."""

        if not values:
            return fallback
        return float(np.percentile(np.asarray(values, dtype=np.float32), q))


def build_calibration_profile(
    silence_updates: List[ClapUpdate],
    clap_updates: List[ClapUpdate],
    clap_times: List[float],
) -> ClapCalibrationProfile:
    """Builds one stable profile from silence baselines and confirmed clap features."""

    def percentile(values: List[float], q: float, fallback: float) -> float:
        if not values:
            return fallback
        return float(np.percentile(np.asarray(values, dtype=np.float32), q))

    noise_rms = percentile([update.rms for update in silence_updates], 75.0, 0.004)
    noise_transient = percentile([update.transient for update in silence_updates], 75.0, 0.008)
    clap_peak = percentile([update.peak for update in clap_updates], 35.0, 0.10)
    clap_rms = percentile([update.rms for update in clap_updates], 35.0, 0.010)
    clap_transient = percentile([update.transient for update in clap_updates], 35.0, 0.020)
    clap_score = percentile([update.clap_score for update in clap_updates], 35.0, 4.8)
    crest_factor = percentile([update.crest_factor for update in clap_updates], 25.0, 2.8)
    band_ratio = percentile([update.band_ratio for update in clap_updates], 25.0, 1.3)
    high_band_share = percentile([update.high_band_share for update in clap_updates], 25.0, 0.24)
    spectral_flatness = percentile([update.spectral_flatness for update in clap_updates], 25.0, 0.18)

    peak_min = percentile([update.peak for update in clap_updates], 15.0, clap_peak)
    peak_median = percentile([update.peak for update in clap_updates], 50.0, clap_peak)
    peak_max = percentile([update.peak for update in clap_updates], 85.0, clap_peak)
    rms_min = percentile([update.rms for update in clap_updates], 15.0, clap_rms)
    rms_median = percentile([update.rms for update in clap_updates], 50.0, clap_rms)
    rms_max = percentile([update.rms for update in clap_updates], 85.0, clap_rms)
    transient_min = percentile([update.transient for update in clap_updates], 15.0, clap_transient)
    transient_median = percentile([update.transient for update in clap_updates], 50.0, clap_transient)
    transient_max = percentile([update.transient for update in clap_updates], 85.0, clap_transient)

    gaps = [current - previous for previous, current in zip(clap_times, clap_times[1:])]
    if gaps:
        ordered = sorted(gaps)
        short_gaps = ordered[: max(1, len(ordered) // 2)]
        observed_gap_seconds = float(np.median(np.asarray(short_gaps, dtype=np.float32)))
    else:
        observed_gap_seconds = 0.32

    return ClapCalibrationProfile(
        captured_claps=len(clap_updates),
        calibrated_at=time.time(),
        noise_rms=noise_rms,
        noise_transient=noise_transient,
        clap_peak=clap_peak,
        clap_rms=clap_rms,
        clap_transient=clap_transient,
        clap_score=clap_score,
        crest_factor=crest_factor,
        band_ratio=band_ratio,
        high_band_share=high_band_share,
        spectral_flatness=spectral_flatness,
        observed_gap_seconds=observed_gap_seconds,
        peak_min=peak_min,
        peak_median=peak_median,
        peak_max=peak_max,
        rms_min=rms_min,
        rms_median=rms_median,
        rms_max=rms_max,
        transient_min=transient_min,
        transient_median=transient_median,
        transient_max=transient_max,
    )
