"""
Download KML from S3 by file_name; parse to GeoJSON via crop_monitoring.kml_parser.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def kml_basenames_for_location(
    db_files: list[str],
    field_name_kml: str,
    *,
    csv_first: bool = False,
) -> list[str]:
    """Ordered KML basenames to try on S3: crop_indices file_name(s) plus CSV Field Name KML."""
    from_db: list[str] = []
    seen: set[str] = set()
    for f in db_files:
        f = (f or "").strip()
        if f and f not in seen:
            seen.add(f)
            from_db.append(f)
    k = (field_name_kml or "").strip()
    from_csv: list[str] = []
    if k:
        base = k if k.lower().endswith(".kml") else f"{k}.kml"
        base = base.replace("+", " ").strip()
        if base:
            from_csv.append(base)
    primary = from_csv + from_db if csv_first else from_db + from_csv
    out: list[str] = []
    seen2: set[str] = set()
    for f in primary:
        if f not in seen2:
            seen2.add(f)
            out.append(f)
    return out


def normalize_kml_file_name(file_name: str) -> str:
    s = (file_name or "").replace("+", " ").strip()
    if not s.lower().endswith(".kml"):
        s = f"{s}.kml"
    return s


def build_candidate_s3_keys(file_name: str, prefixes: list[str]) -> list[str]:
    """S3 object keys to try (basename under each prefix)."""
    base = normalize_kml_file_name(file_name)
    name_only = Path(base).name
    keys = []
    for p in prefixes:
        p = p.rstrip("/") + "/"
        keys.append(f"{p}{name_only}")
    return keys


def download_kml_first_match(
    bucket: str,
    keys: list[str],
    dest_path: Path,
    *,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    region_name: str = "ap-south-1",
) -> Optional[str]:
    """
    Try keys in order; save first hit to dest_path. Returns key used or None.
    """
    try:
        import boto3
    except ImportError:
        logger.error("boto3 required for S3 download")
        return None

    kwargs: dict[str, Any] = {"region_name": region_name}
    if aws_access_key_id and aws_secret_access_key:
        kwargs["aws_access_key_id"] = aws_access_key_id
        kwargs["aws_secret_access_key"] = aws_secret_access_key

    from botocore.exceptions import ClientError

    client = boto3.client("s3", **kwargs)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    for key in keys:
        try:
            client.download_file(bucket, key, str(dest_path))
            logger.info("Downloaded s3://%s/%s -> %s", bucket, key, dest_path)
            return key
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                continue
            logger.debug("S3 error for %s: %s", key, e)
            continue
        except Exception as e:
            logger.debug("S3 miss or error for %s: %s", key, e)
            continue
    return None


def kml_to_geojson_polygon(kml_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Returns (geojson_polygon, metadata_dict)."""
    from crop_monitoring.kml_parser import parse_kml

    geojson, meta = parse_kml(kml_path)
    return geojson, meta
