"""
List and download KML files from S3. Used by the batch pipeline to process all KMLs in a prefix.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def list_kml_files(
    s3_client,
    bucket: str,
    prefix: str,
    skip_empty: bool = True,
) -> List[str]:
    """
    List all .kml object keys under the given prefix.
    Ignores non-KML files. Optionally skips objects with Size == 0.
    Returns sorted list of keys.
    """
    prefix = prefix.rstrip("/") + "/" if prefix else ""
    keys: List[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            key = (obj.get("Key") or "").strip()
            if not key.endswith(".kml"):
                continue
            if skip_empty and obj.get("Size", 0) == 0:
                logger.debug("Skipping empty file: %s", key)
                continue
            keys.append(key)
    return sorted(keys)


def download_kml(s3_client, bucket: str, key: str, local_path: Path) -> None:
    """Download a single KML object from S3 to local_path."""
    s3_client.download_file(bucket, key, str(local_path))
