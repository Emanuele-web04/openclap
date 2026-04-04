"""
FILE: pector_backend.py
Purpose: Installs and streams audio into the optional external pector detector.
Depends on: app_paths.py, clap_detector.py, and Python stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import time
from typing import Sequence

import numpy as np

from app_paths import AppPaths
from clap_detector import ClapDetectorConfig, ClapUpdate


PECTOR_REPO_URL = "https://github.com/JorenSix/pector"
PECTOR_LICENSE = "GPL-3.0"


def managed_pector_root(paths: AppPaths) -> Path:
    """Returns the managed install root for a locally built external pector checkout."""

    return paths.app_support_dir / "vendor" / "pector"


def managed_pector_binary(paths: AppPaths) -> Path:
    """Returns the expected binary path for the managed pector checkout."""

    return managed_pector_root(paths) / "bin" / "pector_c"


def install_pector_checkout(paths: AppPaths) -> Path:
    """Clones and builds the upstream pector repository in the user's app-support folder."""

    root = managed_pector_root(paths)
    root.parent.mkdir(parents=True, exist_ok=True)

    if root.exists():
        subprocess.run(["git", "pull", "--ff-only"], cwd=root, check=True)
    else:
        subprocess.run(["git", "clone", "--depth", "1", PECTOR_REPO_URL, str(root)], check=True)

    _patch_upstream_pector_source(root)
    subprocess.run(["make", "compile"], cwd=root, check=True)
    binary_path = managed_pector_binary(paths)
    if not binary_path.exists():
        raise RuntimeError(f"pector build completed without producing {binary_path}")
    return binary_path


def _patch_upstream_pector_source(root: Path) -> None:
    """Applies a tiny build-fix patch for the current upstream checkout on macOS toolchains."""

    source_path = root / "src" / "pector.c"
    if not source_path.exists():
        return

    source = source_path.read_text(encoding="utf-8")
    patched = source.replace('    f\n', "")
    patched = patched.replace(
        '    printf(stderr,"\\t\\tIf on clap is given, the percussion detector waits for a double clap and then exits.\\n");',
        '    fprintf(stderr,"\\t\\tIf on clap is given, the percussion detector waits for a double clap and then exits.\\n");',
    )
    if patched != source:
        source_path.write_text(patched, encoding="utf-8")


@dataclass
class PectorProcessState:
    """Tracks the external process plus its current health flags."""

    process: subprocess.Popen | None = None
    status: str = "starting"
    last_error: str = ""


class PectorDetector:
    """Streams float32 mono audio into the external pector binary and reacts to double-clap exits."""

    def __init__(
        self,
        paths: AppPaths,
        config: ClapDetectorConfig,
        *,
        clock=time.monotonic,
        popen=subprocess.Popen,
    ) -> None:
        self.paths = paths
        self.base_config = config
        self.config = ClapDetectorConfig(
            backend="pector",
            pector_binary_path=config.pector_binary_path,
            sample_rate=44_100,
            block_duration=512 / 44_100,
            event_window_seconds=config.event_window_seconds,
            warmup_seconds=config.warmup_seconds,
            target_claps=2,
            clap_window_seconds=0.45,
            cooldown_seconds=max(config.cooldown_seconds, 1.5),
            min_clap_gap_seconds=0.1,
            refractory_seconds=config.refractory_seconds,
            min_peak=config.min_peak,
            min_rms=config.min_rms,
            min_transient=config.min_transient,
            energy_ratio_threshold=config.energy_ratio_threshold,
            transient_ratio_threshold=config.transient_ratio_threshold,
            min_crest_factor=config.min_crest_factor,
            min_band_ratio=config.min_band_ratio,
            min_high_band_share=config.min_high_band_share,
            min_spectral_flatness=config.min_spectral_flatness,
            min_zero_crossing_rate=config.min_zero_crossing_rate,
            min_spectral_centroid_hz=config.min_spectral_centroid_hz,
            min_clap_score=config.min_clap_score,
            noise_floor_alpha=config.noise_floor_alpha,
            band_low_hz=config.band_low_hz,
            band_high_hz=config.band_high_hz,
            low_band_max_hz=config.low_band_max_hz,
            min_inter_clap_similarity=config.min_inter_clap_similarity,
            max_recent_transient_rate=config.max_recent_transient_rate,
            recent_transient_window=config.recent_transient_window,
            calibration_version=config.calibration_version,
            calibration_profile=config.calibration_profile,
        )
        self._clock = clock
        self._popen = popen
        self._state = PectorProcessState()
        self._cooldown_until = 0.0
        self._processed_seconds = 0.0
        self._noise_floor = self.config.min_rms / 2.0
        self._transient_floor = self.config.min_transient / 2.0

    # --- Lifecycle --------------------------------------------------

    def close(self) -> None:
        """Stops the external pector process if it is running."""

        process = self._state.process
        self._state.process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                process.kill()

    def reset_runtime_state(self) -> None:
        """Clears timing state and restarts the subprocess on the next chunk."""

        self.close()
        self._cooldown_until = 0.0
        self._processed_seconds = 0.0
        self._state.status = "starting"
        self._state.last_error = ""

    # --- Chunk Processing -------------------------------------------

    def process_chunk(self, samples: Sequence[float], timestamp: float) -> ClapUpdate:
        """Feeds one chunk into pector and converts its process state into a ClapUpdate."""

        chunk = np.asarray(samples, dtype=np.float32).reshape(-1)
        self._processed_seconds += chunk.size / max(self.config.sample_rate, 1)
        warmup_remaining = max(0.0, self.config.warmup_seconds - self._processed_seconds)

        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        rms = float(np.sqrt(np.mean(chunk * chunk))) if chunk.size else 0.0
        diffs = np.diff(chunk)
        transient = float(np.sqrt(np.mean(diffs * diffs))) if diffs.size else 0.0
        crest_factor = peak / max(rms, 1e-6)

        self._noise_floor = self.config.noise_floor_alpha * self._noise_floor + (1.0 - self.config.noise_floor_alpha) * rms
        self._transient_floor = self.config.noise_floor_alpha * self._transient_floor + (1.0 - self.config.noise_floor_alpha) * transient

        cooldown_remaining = max(0.0, self._cooldown_until - timestamp)
        if cooldown_remaining > 0.0:
            return self._build_update(
                status="cooldown",
                peak=peak,
                rms=rms,
                transient=transient,
                crest_factor=crest_factor,
                cooldown_remaining=cooldown_remaining,
                warmup_remaining=warmup_remaining,
                rejection_reason="cooldown",
            )

        if not self._ensure_process():
            return self._build_update(
                status=self._state.status,
                peak=peak,
                rms=rms,
                transient=transient,
                crest_factor=crest_factor,
                cooldown_remaining=0.0,
                warmup_remaining=warmup_remaining,
                rejection_reason="missing backend" if self._state.status == "missing-backend" else "",
            )

        triggered = False
        process = self._state.process
        try:
            if process is not None and process.stdin is not None and chunk.size:
                process.stdin.write(chunk.astype("<f4", copy=False).tobytes())
                process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._state.last_error = str(exc)
            self.close()
            self._state.status = "error"

        process = self._state.process
        if process is not None:
            exit_code = process.poll()
            if exit_code == 0:
                triggered = True
                self._cooldown_until = timestamp + self.config.cooldown_seconds
                self.close()
                self._state.status = "ready"
            elif exit_code is not None:
                self._state.last_error = f"pector exited with code {exit_code}"
                self.close()
                self._state.status = "error"

        if warmup_remaining > 0.0:
            status = "warmup"
        elif triggered:
            status = "triggered"
        elif self._state.status == "ready":
            status = "listening"
        else:
            status = self._state.status

        return self._build_update(
            status=status,
            peak=peak,
            rms=rms,
            transient=transient,
            crest_factor=crest_factor,
            cooldown_remaining=max(0.0, self._cooldown_until - timestamp),
            warmup_remaining=warmup_remaining,
            triggered=triggered,
            is_impulse=triggered,
            confidence=1.0 if triggered else 0.0,
        )

    # --- Internal Helpers ------------------------------------------

    def _resolve_binary_path(self) -> Path | None:
        """Chooses either a user-configured pector binary or the managed install path."""

        configured = self.base_config.pector_binary_path.strip()
        if configured:
            path = Path(configured).expanduser()
            return path if path.exists() else None

        managed = managed_pector_binary(self.paths)
        return managed if managed.exists() else None

    def _ensure_process(self) -> bool:
        """Starts the background pector subprocess when it is missing."""

        current = self._state.process
        if current is not None and current.poll() is None:
            self._state.status = "ready"
            return True

        binary_path = self._resolve_binary_path()
        if binary_path is None:
            self._state.status = "missing-backend"
            self._state.last_error = "pector binary is not installed"
            return False

        self.close()
        self._state.process = self._popen(
            [str(binary_path), "on", "clap"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._state.status = "ready"
        self._state.last_error = ""
        return True

    def _build_update(
        self,
        *,
        status: str,
        peak: float,
        rms: float,
        transient: float,
        crest_factor: float,
        cooldown_remaining: float,
        warmup_remaining: float,
        triggered: bool = False,
        is_impulse: bool = False,
        confidence: float = 0.0,
        rejection_reason: str = "",
    ) -> ClapUpdate:
        """Creates one compatibility ClapUpdate for the daemon status pipeline."""

        return ClapUpdate(
            status=status,
            clap_count=0,
            triggered=triggered,
            is_impulse=is_impulse,
            peak=peak,
            rms=rms,
            transient=transient,
            crest_factor=crest_factor,
            band_ratio=0.0,
            high_band_share=0.0,
            spectral_flatness=0.0,
            zero_crossing_rate=0.0,
            spectral_centroid=0.0,
            clap_score=0.0,
            confidence=confidence,
            rejection_reason=rejection_reason,
            noise_floor=self._noise_floor,
            transient_floor=self._transient_floor,
            transient_density=0.0,
            cooldown_remaining=cooldown_remaining,
            warmup_remaining=warmup_remaining,
            decay_ratio=0.0,
            event_state=status,
        )
