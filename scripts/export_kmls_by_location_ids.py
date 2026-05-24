#!/usr/bin/env python3
"""
Export KML files for a list of location_ids.

Flow:
1) Query operations.crop_indices for (location_id, file_name) pairs.
2) For each file_name:
   - Try S3 first (head_object under provided prefixes; optionally build an index by listing).
   - If not found, fallback to local directory (default: /opt/prsti/kml_files).
3) Copy/download to output dir with name: <location_id>_<original_file_name>.kml

Idempotent:
- If output file already exists, it is skipped.

Notes:
- This utility does not modify any business logic; it only fetches/copies KML assets.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(_ROOT))

from core.db import SessionLocal  # noqa: E402

logger = logging.getLogger(__name__)


DEFAULT_LOCATION_IDS = [
    # CG
    "IND-CG-600000",
    "IND-CG-600002",
    "IND-CG-600003",
    "IND-CG-600004",
    "IND-CG-600005",
    "IND-CG-600006",
    "IND-CG-600007",
    "IND-CG-600009",
    "IND-CG-600010",
    "IND-CG-600013",
    "IND-CG-600015",
    "IND-CG-600016",
    "IND-CG-600017",
    "IND-CG-600018",
    "IND-CG-600019",
    "IND-CG-600021",
    "IND-CG-600025",
    "IND-CG-600026",
    "IND-CG-600027",
    "IND-CG-600028",
    "IND-CG-600030",
    "IND-CG-600031",
    "IND-CG-600032",
    "IND-CG-600033",
    "IND-CG-600034",
    # KA
    "IND-KA-600000",
    "IND-KA-600001",
    "IND-KA-600003",
    "IND-KA-600005",
    "IND-KA-600006",
    "IND-KA-600010",
    "IND-KA-600011",
    "IND-KA-600012",
    "IND-KA-600013",
    "IND-KA-600014",
    "IND-KA-600015",
    "IND-KA-600016",
    "IND-KA-600017",
    "IND-KA-600018",
    "IND-KA-600019",
    "IND-KA-600020",
    "IND-KA-600021",
    "IND-KA-600022",
    "IND-KA-600023",
    "IND-KA-600024",
    "IND-KA-600025",
    "IND-KA-600026",
    "IND-KA-600027",
    "IND-KA-600028",
    "IND-KA-600029",
    # OD
    "IND-OD-600001",
    "IND-OD-600003",
    "IND-OD-600004",
    "IND-OD-600006",
    "IND-OD-600007",
    "IND-OD-600008",
    "IND-OD-600010",
    "IND-OD-600011",
    "IND-OD-600012",
    "IND-OD-600013",
    "IND-OD-600016",
    "IND-OD-600017",
    "IND-OD-600018",
    "IND-OD-600019",
    "IND-OD-600020",
    "IND-OD-600021",
    "IND-OD-600022",
    "IND-OD-600023",
    "IND-OD-600024",
    "IND-OD-600026",
    "IND-OD-600027",
    "IND-OD-600028",
    "IND-OD-600029",
    "IND-OD-600031",
    "IND-OD-600033",
    # WB
    "IND-WB-600000",
    "IND-WB-600001",
    "IND-WB-600002",
    "IND-WB-600003",
    "IND-WB-600004",
    "IND-WB-600005",
    "IND-WB-600006",
    "IND-WB-600007",
    "IND-WB-600008",
    "IND-WB-600009",
    "IND-WB-600010",
    "IND-WB-600011",
    "IND-WB-600012",
    "IND-WB-600013",
    "IND-WB-600014",
    "IND-WB-600015",
    "IND-WB-600016",
    "IND-WB-600017",
    "IND-WB-600018",
    "IND-WB-600019",
    "IND-WB-600020",
    "IND-WB-600021",
    "IND-WB-600022",
    "IND-WB-600023",
    "IND-WB-600024",
    "IND-WB-600025",
]


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


_BAD_FILENAME_CHARS = re.compile(r'[<>:"/\\\\|?*]+')


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    name = _BAD_FILENAME_CHARS.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "file.kml"


def _read_location_ids_file(path: str | Path) -> list[str]:
    p = Path(path)
    text_in = p.read_text(encoding="utf-8", errors="replace")
    out: list[str] = []
    for line in text_in.splitlines():
        s = line.strip().strip('"').strip("'")
        if not s or s.lower() in ("location_id",):
            continue
        if s.startswith("#"):
            continue
        out.append(s)
    return out


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _fetch_location_to_files(location_ids: list[str]) -> dict[str, list[str]]:
    """
    Query operations.crop_indices for distinct (location_id, file_name) for given location_ids.
    """
    db = SessionLocal()
    try:
        mapping: dict[str, set[str]] = {lid: set() for lid in location_ids}
        # Use IN (...) with chunking to avoid parameter limits.
        for chunk in _chunks(location_ids, 500):
            params = {f"lid_{i}": v for i, v in enumerate(chunk)}
            in_list = ", ".join(f":lid_{i}" for i in range(len(chunk)))
            q = text(
                f"""
                SELECT DISTINCT location_id, file_name
                FROM operations.crop_indices
                WHERE file_name IS NOT NULL
                  AND location_id IN ({in_list})
                """
            )
            rows = db.execute(q, params).fetchall()
            for loc_id, file_name in rows:
                if loc_id is None or file_name is None:
                    continue
                mapping.setdefault(str(loc_id), set()).add(str(file_name))
        return {k: sorted(v) for k, v in mapping.items() if v}
    finally:
        db.close()


@dataclass(frozen=True)
class FetchResult:
    found: bool
    source: str
    source_ref: str


def _s3_client_from_env_or_secrets():
    """
    Create boto3 S3 client.
    - Prefer explicit AWS env vars if present.
    - Fallback to Secrets Manager via scripts/run_crop_analysis_s3_batch.get_aws_credentials().
    """
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("boto3 is required for S3 download. pip install boto3") from e

    aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
    aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
    aws_region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"

    if aws_access_key_id and aws_secret_access_key:
        return boto3.client(
            "s3",
            region_name=aws_region,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

    # Secrets Manager (same as the batch pipeline)
    from scripts.run_crop_analysis_s3_batch import get_aws_credentials, AWS_REGION  # noqa: WPS433

    creds = get_aws_credentials()
    return boto3.client(
        "s3",
        region_name=AWS_REGION,
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
    )


def _s3_key_candidates(file_name: str, prefixes: list[str]) -> list[str]:
    fn = file_name.lstrip("/").replace("\\", "/")
    out: list[str] = []
    for p in prefixes:
        p2 = (p or "").strip()
        if p2 and not p2.endswith("/"):
            p2 += "/"
        out.append(f"{p2}{fn}")
    # Also try bare file name
    out.append(fn)
    return list(dict.fromkeys(out))


def _build_s3_basename_index(s3_client, *, bucket: str, prefixes: list[str]) -> dict[str, str]:
    """
    Build basename -> key index by listing under prefixes.
    Slower upfront, but avoids head_object per file for large sets.
    """
    try:
        from crop_monitoring.s3_file_loader import list_kml_files
    except ImportError:
        list_kml_files = None

    index: dict[str, str] = {}
    for p in prefixes:
        p2 = (p or "").strip()
        if not p2:
            continue
        if list_kml_files:
            keys = list_kml_files(s3_client, bucket, p2, skip_empty=True)
        else:
            # Fallback list via paginator
            paginator = s3_client.get_paginator("list_objects_v2")
            keys = []
            pref = p2.rstrip("/") + "/"
            for page in paginator.paginate(Bucket=bucket, Prefix=pref):
                for obj in page.get("Contents") or []:
                    k = (obj.get("Key") or "").strip()
                    if k.endswith(".kml") and (obj.get("Size") or 0) > 0:
                        keys.append(k)
        for k in keys:
            base = Path(k).name
            # Keep first seen; duplicates across prefixes are ambiguous anyway.
            index.setdefault(base, k)
    return index


def _try_fetch_from_s3(
    *,
    s3_client,
    bucket: str,
    file_name: str,
    output_path: Path,
    prefixes: list[str],
    basename_index: dict[str, str] | None,
) -> FetchResult:
    # Index lookup first (if available)
    if basename_index is not None:
        key = basename_index.get(Path(file_name).name)
        if key:
            s3_client.download_file(bucket, key, str(output_path))
            return FetchResult(True, "s3", f"s3://{bucket}/{key}")

    # Otherwise, try head_object on candidates
    for key in _s3_key_candidates(file_name, prefixes):
        try:
            s3_client.head_object(Bucket=bucket, Key=key)
        except Exception:
            continue
        s3_client.download_file(bucket, key, str(output_path))
        return FetchResult(True, "s3", f"s3://{bucket}/{key}")

    return FetchResult(False, "s3", "")


def _try_fetch_from_local(*, local_root: Path, file_name: str, output_path: Path) -> FetchResult:
    # Common: local root contains multiple subfolders; search by basename.
    target_base = Path(file_name).name
    # Fast path: direct join (if root already contains flat files)
    direct = local_root / target_base
    if direct.is_file():
        shutil.copyfile(direct, output_path)
        return FetchResult(True, "local", str(direct))

    # Recursive search
    matches = list(local_root.rglob(target_base))
    matches = [m for m in matches if m.is_file()]
    if not matches:
        return FetchResult(False, "local", "")
    # Deterministic pick: shortest path, then lexicographic
    matches.sort(key=lambda p: (len(str(p)), str(p).lower()))
    chosen = matches[0]
    shutil.copyfile(chosen, output_path)
    return FetchResult(True, "local", str(chosen))


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(description="Export/download KMLs by location_id mapping from crop_indices.")
    parser.add_argument("--location-ids-file", type=str, default=None, help="Text file with one location_id per line")
    parser.add_argument("--output-dir", type=str, default=r"C:\Users\madan\Downloads\kml_location_files", help="Target directory")
    parser.add_argument("--s3-bucket", type=str, default="prsti-public-data", help="S3 bucket name")
    parser.add_argument(
        "--s3-prefix",
        action="append",
        default=[],
        help="S3 prefix to try (repeatable). Example: seedworks/kml_files/input_files/CG/CG/",
    )
    parser.add_argument(
        "--build-s3-index",
        action="store_true",
        help="List KML keys under prefixes and build a basename->key index (faster for many files).",
    )
    parser.add_argument("--local-root", type=str, default="/opt/prsti/kml_files", help="Local fallback root directory")
    parser.add_argument("--dry-run", action="store_true", help="Do not download/copy, only log actions")
    args = parser.parse_args()

    location_ids = _read_location_ids_file(args.location_ids_file) if args.location_ids_file else list(DEFAULT_LOCATION_IDS)
    location_ids = [s.strip() for s in location_ids if s and s.strip()]
    location_ids = list(dict.fromkeys(location_ids))

    out_dir = Path(args.output_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    local_root = Path(args.local_root).expanduser()
    if not local_root.exists():
        logger.warning("Local root does not exist (will rely on S3 only): %s", local_root)

    logger.info("Location IDs requested: %d", len(location_ids))
    mapping = _fetch_location_to_files(location_ids)
    logger.info("Location IDs with file_name(s) found in crop_indices: %d", len(mapping))

    # Flatten requested downloads
    desired: list[tuple[str, str]] = []
    for lid, files in mapping.items():
        for fn in files:
            desired.append((lid, fn))

    logger.info("Total file references to fetch: %d", len(desired))
    if not desired:
        logger.warning("No file_name mappings found for provided location_ids.")
        return

    # S3 init
    s3_client = None
    basename_index = None
    if args.s3_prefix or args.build_s3_index:
        try:
            s3_client = _s3_client_from_env_or_secrets()
            prefixes = args.s3_prefix or []
            if args.build_s3_index and prefixes:
                logger.info("Building S3 basename index (prefix count=%d)...", len(prefixes))
                basename_index = _build_s3_basename_index(s3_client, bucket=args.s3_bucket, prefixes=prefixes)
                logger.info("S3 basename index size: %d", len(basename_index))
        except Exception as e:
            logger.warning("S3 client unavailable; will use local fallback only. Error: %s", e)
            s3_client = None
            basename_index = None

    prefixes = args.s3_prefix or []

    total_downloaded = 0
    total_found = 0
    missing: list[str] = []

    for lid, fn in desired:
        safe_out_name = _safe_filename(f"{lid}_{Path(fn).name}")
        if not safe_out_name.lower().endswith(".kml"):
            safe_out_name += ".kml"
        out_path = out_dir / safe_out_name

        if out_path.exists() and out_path.stat().st_size > 0:
            total_found += 1
            continue

        if args.dry_run:
            logger.info("[dry-run] Would fetch: location_id=%s file_name=%s -> %s", lid, fn, out_path)
            total_found += 1
            continue

        fetched = FetchResult(False, "", "")
        if s3_client is not None:
            try:
                fetched = _try_fetch_from_s3(
                    s3_client=s3_client,
                    bucket=args.s3_bucket,
                    file_name=fn,
                    output_path=out_path,
                    prefixes=prefixes,
                    basename_index=basename_index,
                )
            except Exception as e:
                logger.warning("S3 fetch failed file=%s: %s", fn, e)
                fetched = FetchResult(False, "s3", "")

        if not fetched.found and local_root.exists():
            try:
                fetched = _try_fetch_from_local(local_root=local_root, file_name=fn, output_path=out_path)
            except Exception as e:
                logger.warning("Local fetch failed file=%s: %s", fn, e)
                fetched = FetchResult(False, "local", "")

        if fetched.found:
            total_found += 1
            total_downloaded += 1
            logger.info("Saved: %s (source=%s %s)", out_path, fetched.source, fetched.source_ref)
        else:
            missing.append(fn)
            logger.warning("Missing in S3+local: %s (location_id=%s)", fn, lid)

    logger.info("----- SUMMARY -----")
    logger.info("Total location_ids requested: %d", len(location_ids))
    logger.info("Total location_ids with mappings: %d", len(mapping))
    logger.info("Total file references: %d", len(desired))
    logger.info("Total files found (including already-present outputs): %d", total_found)
    logger.info("Total files downloaded/copied this run: %d", total_downloaded)
    logger.info("Missing files: %d", len(missing))
    if missing:
        for m in missing[:200]:
            logger.info("MISSING: %s", m)
        if len(missing) > 200:
            logger.info("... and %d more", len(missing) - 200)


if __name__ == "__main__":
    main()

