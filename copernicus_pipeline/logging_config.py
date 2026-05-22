"""Logging setup for the pipeline CLI (does not replace batch script file logging)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logging(level: str = "INFO", log_file: Optional[Path] = None) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    lvl = getattr(logging, level.upper(), logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(fmt)
    h.setLevel(lvl)
    root.addHandler(h)
    root.setLevel(lvl)
    if log_file:
        log_file = log_file.resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
        fh.setFormatter(fmt)
        fh.setLevel(lvl)
        root.addHandler(fh)
