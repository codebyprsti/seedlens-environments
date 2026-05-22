"""
Portable path resolution for satellite v2 / harvest deployment.

Override with environment variables (absolute or relative to repo root):

  SATELLITE_PROJECT_ROOT  — repo root (default: parent of config/)
  SATELLITE_KML_DIR       — KML directory (default: data/kml/harvest_all_fields)
  SATELLITE_MAPPING_DIR   — Excel/CSV/manifest (default: data/mapping)
  SATELLITE_LOG_DIR       — logs (default: logs/satellite)
  SATELLITE_CHECKPOINT_DIR — local run state (default: checkpoints/satellite)
  SATELLITE_REPORT_DIR    — validation reports (default: reports/harvest)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def project_root() -> Path:
    for key in ("SATELLITE_PROJECT_ROOT", "SEEDIQ_REPO_ROOT", "PIPELINE_REPO_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def _resolve(path: str | Path | None, default: Path) -> Path:
    if not path:
        return default
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = project_root() / p
    return p.resolve()


@dataclass(frozen=True)
class SatellitePaths:
    root: Path
    kml_dir: Path
    mapping_dir: Path
    log_dir: Path
    checkpoint_dir: Path
    report_dir: Path
    raw_cache_dir: Path
    tmp_dir: Path

    def ensure_runtime_dirs(self) -> None:
        """Create lab runtime folders (logs, checkpoints, tmp) before nohup or validate."""
        for d in (
            self.root / "logs",
            self.log_dir,
            self.root / "checkpoints",
            self.checkpoint_dir,
            self.tmp_dir,
            self.kml_dir,
            self.mapping_dir,
            self.report_dir,
            self.raw_cache_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def get_satellite_paths() -> SatellitePaths:
    root = project_root()
    return SatellitePaths(
        root=root,
        kml_dir=_resolve(
            os.environ.get("SATELLITE_KML_DIR"),
            root / "data" / "kml" / "harvest_all_fields",
        ),
        mapping_dir=_resolve(
            os.environ.get("SATELLITE_MAPPING_DIR"),
            root / "data" / "mapping",
        ),
        log_dir=_resolve(
            os.environ.get("SATELLITE_LOG_DIR"),
            root / "logs" / "satellite",
        ),
        checkpoint_dir=_resolve(
            os.environ.get("SATELLITE_CHECKPOINT_DIR"),
            root / "checkpoints" / "satellite",
        ),
        report_dir=_resolve(
            os.environ.get("SATELLITE_REPORT_DIR"),
            root / "reports" / "harvest",
        ),
        raw_cache_dir=_resolve(
            os.environ.get("SATELLITE_RAW_CACHE_DIR"),
            root / "data" / "raw_cache",
        ),
        tmp_dir=_resolve(
            os.environ.get("SATELLITE_TMP_DIR"),
            root / "tmp",
        ),
    )
