"""Pre-flight checks before satellite harvest deployment."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MIN_PYTHON = (3, 10)
MIN_FREE_GB = 5.0


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def check_python_version() -> ValidationResult:
    r = ValidationResult()
    if sys.version_info < MIN_PYTHON:
        r.fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required; got {sys.version.split()[0]}")
    return r


def check_database_url() -> ValidationResult:
    r = ValidationResult()
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        # SeedIQ may use DB_* components
        host = os.environ.get("DB_HOST") or os.environ.get("POSTGRES_HOST")
        if not host:
            r.fail("DATABASE_URL or DB_HOST not set")
    return r


def check_copernicus_credentials() -> ValidationResult:
    r = ValidationResult()
    cid = (
        os.environ.get("SH_CLIENT_ID")
        or os.environ.get("SENTINEL_CLIENT_ID")
        or os.environ.get("COPERNICUS_CLIENT_ID")
    )
    secret = (
        os.environ.get("SH_CLIENT_SECRET")
        or os.environ.get("SENTINEL_CLIENT_SECRET")
        or os.environ.get("COPERNICUS_CLIENT_SECRET")
    )
    if not cid or not secret:
        r.fail("Copernicus/Sentinel Hub credentials missing (SH_CLIENT_ID / SH_CLIENT_SECRET)")
    return r


def check_directories(paths) -> ValidationResult:
    r = ValidationResult()
    for name, p in (
        ("kml_dir", paths.kml_dir),
        ("mapping_dir", paths.mapping_dir),
        ("log_dir", paths.log_dir),
        ("checkpoint_dir", paths.checkpoint_dir),
    ):
        if not p.exists():
            r.warn(f"{name} does not exist yet: {p} (will be created)")
        elif not os.access(p, os.W_OK):
            r.fail(f"{name} not writable: {p}")
    return r


def check_disk_space(paths, min_gb: float = MIN_FREE_GB) -> ValidationResult:
    r = ValidationResult()
    try:
        usage = shutil.disk_usage(paths.root)
        free_gb = usage.free / (1024**3)
        if free_gb < min_gb:
            r.warn(f"Low disk space on {paths.root}: {free_gb:.1f} GB free (recommend >= {min_gb} GB)")
    except OSError as e:
        r.warn(f"Could not check disk space: {e}")
    return r


def check_kml_count(kml_dir: Path, expected: int = 253) -> ValidationResult:
    r = ValidationResult()
    if not kml_dir.is_dir():
        r.fail(f"KML directory missing: {kml_dir}")
        return r
    count = len(list(kml_dir.glob("*.kml")))
    if count == 0:
        r.fail(f"No .kml files in {kml_dir}")
    elif count != expected:
        r.warn(f"KML count {count} (expected ~{expected})")
    manifest = kml_dir / "_harvest_all_manifest.csv"
    if not manifest.is_file():
        r.warn(f"Manifest not found: {manifest} (copy to data/mapping/ or kml dir)")
    return r


def check_mapping_files(mapping_dir: Path) -> ValidationResult:
    r = ValidationResult()
    if not mapping_dir.is_dir():
        r.fail(f"Mapping directory missing: {mapping_dir}")
        return r
    found = list(mapping_dir.glob("*.csv")) + list(mapping_dir.glob("*.xlsx"))
    if not found:
        r.warn(f"No CSV/XLSX in {mapping_dir} — grower/file_name resolution may be incomplete")
    return r


def test_db_connectivity() -> ValidationResult:
    r = ValidationResult()
    try:
        from sqlalchemy import text
        from core.db import SessionLocal

        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        r.fail(f"Database connection failed: {e}")
    return r


def test_copernicus_auth() -> ValidationResult:
    r = ValidationResult()
    try:
        try:
            from crop_monitoring.sh_http_setup import configure_sh_http

            configure_sh_http()
        except Exception:
            pass
        from sentinelhub import SentinelHubSession

        from crop_monitoring.statistical_client import _get_config

        config = _get_config()
        if not (config.sh_client_id or "").strip() or not (config.sh_client_secret or "").strip():
            r.fail(
                "SH_CLIENT_ID / SH_CLIENT_SECRET not set. Add them to "
                f"{os.environ.get('SATELLITE_PROJECT_ROOT', '.')}/.env on the lab machine."
            )
            return r
        session = SentinelHubSession(config=config)
        token = session.token
        if not token:
            r.fail("Sentinel Hub session returned empty token")
    except Exception as e:
        r.fail(f"Copernicus authentication failed: {e}")
    return r


def check_required_env_vars() -> ValidationResult:
    r = ValidationResult()
    if not (os.environ.get("SATELLITE_KML_DIR") or "").strip():
        r.warn("SATELLITE_KML_DIR not set (using default data/kml/harvest_all_fields)")
    if not (os.environ.get("SATELLITE_MAPPING_DIR") or "").strip():
        r.warn("SATELLITE_MAPPING_DIR not set (using default data/mapping or kml dir)")
    return r


def check_harvest_mapping(
    kml_dir: Path,
    mapping_dir: Path,
    *,
    db=None,
) -> ValidationResult:
    """Duplicate internal_ids, missing canonical file_name, missing grower names."""
    r = ValidationResult()
    try:
        from crop_monitoring.satellite_pipeline.harvest_field_mapping import (
            build_harvest_mappings,
            resolve_grower_ids_for_mappings,
        )

        report = build_harvest_mappings(kml_dir, data_root=mapping_dir, db=db)
        if db is not None:
            report = resolve_grower_ids_for_mappings(db, report)

        if report.errors:
            for e in report.errors[:20]:
                r.fail(e)
            if len(report.errors) > 20:
                r.fail(f"... and {len(report.errors) - 20} more mapping errors")

        if report.duplicate_internal_ids:
            r.fail(f"duplicate_internal_ids: {report.duplicate_internal_ids[:10]}")

        fallback_fn = [
            m.internal_id
            for m in report.mappings.values()
            if m.file_name == m.legacy_file_name
        ]
        if fallback_fn:
            r.warn(
                f"missing_canonical_file_name (using disk name): count={len(fallback_fn)} "
                f"sample={fallback_fn[:5]}"
            )

        missing_grower = [m.internal_id for m in report.mappings.values() if not m.grower_name]
        if missing_grower:
            r.warn(
                f"missing_grower_name: count={len(missing_grower)} sample={missing_grower[:5]}"
            )

        unresolved_gid = [
            m.internal_id for m in report.mappings.values() if m.grower_name and not m.grower_id
        ]
        if unresolved_gid and db is not None:
            r.warn(
                f"unresolved_grower_id: count={len(unresolved_gid)} sample={unresolved_gid[:5]}"
            )

        logger.info(
            "Harvest mapping check: %d fields, %d errors, %d warnings",
            len(report.mappings),
            len(report.errors),
            len(report.warnings),
        )
    except Exception as e:
        r.fail(f"Harvest mapping check failed: {e}")
    return r


def run_all_checks(
    paths,
    *,
    expected_kml: int = 253,
    test_db: bool = True,
    test_copernicus: bool = True,
    check_mappings: bool = True,
) -> ValidationResult:
    paths.ensure_runtime_dirs()
    combined = ValidationResult()
    for part in (
        check_python_version(),
        check_required_env_vars(),
        check_database_url(),
        check_copernicus_credentials(),
        check_directories(paths),
        check_disk_space(paths),
        check_kml_count(paths.kml_dir, expected_kml),
        check_mapping_files(paths.mapping_dir),
    ):
        combined.ok = combined.ok and part.ok
        combined.errors.extend(part.errors)
        combined.warnings.extend(part.warnings)

    db = None
    if test_db:
        part = test_db_connectivity()
        combined.ok = combined.ok and part.ok
        combined.errors.extend(part.errors)
        combined.warnings.extend(part.warnings)
        if part.ok:
            try:
                from core.db import SessionLocal

                db = SessionLocal()
            except Exception:
                db = None

    if test_copernicus:
        part = test_copernicus_auth()
        combined.ok = combined.ok and part.ok
        combined.errors.extend(part.errors)
        combined.warnings.extend(part.warnings)

    if check_mappings and paths.kml_dir.is_dir():
        part = check_harvest_mapping(paths.kml_dir, paths.mapping_dir, db=db)
        combined.ok = combined.ok and part.ok
        combined.errors.extend(part.errors)
        combined.warnings.extend(part.warnings)

    if db is not None:
        try:
            db.close()
        except Exception:
            pass

    return combined


def merge_results(*results: ValidationResult) -> ValidationResult:
    out = ValidationResult()
    for r in results:
        out.ok = out.ok and r.ok
        out.errors.extend(r.errors)
        out.warnings.extend(r.warnings)
    return out
