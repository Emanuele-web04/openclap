"""
FILE: logging_utils.py
Purpose: Provides event-based rotating log setup for the daemon and menu bar app.
Depends on: app_paths.py for log destinations.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app_paths import AppPaths, ensure_runtime_directories


def setup_logger(name: str, paths: AppPaths, debug: bool = False) -> logging.Logger:
    """Configures a process-local rotating file logger and returns it."""

    ensure_runtime_directories(paths)
    logger = logging.getLogger(name)
    if logger.handlers:
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        return logger

    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    log_path = paths.logs_dir / f"{name}.log"
    handler = RotatingFileHandler(log_path, maxBytes=512_000, backupCount=3)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
