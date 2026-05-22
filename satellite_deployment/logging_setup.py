"""Structured file logging for long-running satellite ingestion."""

from __future__ import annotations

import logging
from pathlib import Path


def setup_deployment_logging(
    log_dir: Path,
    *,
    level: str = "INFO",
    run_id: str | None = None,
) -> dict[str, Path]:
    log_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_{run_id[:8]}" if run_id else ""

    paths = {
        "ingestion": log_dir / f"ingestion{suffix}.log",
        "failed": log_dir / f"failed{suffix}.log",
        "retry": log_dir / f"retry{suffix}.log",
        "performance": log_dir / f"performance{suffix}.log",
        "api": log_dir / f"api{suffix}.log",
    }

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Console handler (single)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root.addHandler(ch)

    def _file_logger(name: str, path: Path, level: int = logging.INFO) -> logging.Logger:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = True
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        lg.addHandler(fh)
        return lg

    _file_logger("satellite.ingestion", paths["ingestion"])
    _file_logger("satellite.failed", paths["failed"], logging.WARNING)
    _file_logger("satellite.retry", paths["retry"], logging.WARNING)
    _file_logger("satellite.performance", paths["performance"])
    _file_logger("satellite.api", paths["api"])

    return paths
