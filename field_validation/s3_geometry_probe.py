"""S3 KML geometry probe for field validation (shared by orchestration scripts)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from field_validation.s3_kml import (
    build_candidate_s3_keys,
    download_kml_first_match,
    kml_basenames_for_location,
    kml_to_geojson_polygon,
)

ProbeRow = dict[str, Any]


def probe_locations_geometry(
    *,
    location_ids: list[str],
    csv_row_for_location: Callable[[str], Optional[dict[str, str]]],
    loc_to_files: dict[str, list[str]],
    bucket: str,
    prefixes: list[str],
    csv_first: bool,
    tmp_dir: Path,
) -> tuple[list[ProbeRow], list[str]]:
    """
    Try to download KML for each location_id; return probe rows and list of missing location_ids.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    rows: list[ProbeRow] = []
    missing: list[str] = []
    for lid in location_ids:
        csv_static = csv_row_for_location(lid) or {}
        files = list(dict.fromkeys(loc_to_files.get(lid, [])))
        bases = kml_basenames_for_location(
            files,
            (csv_static.get("field_name_kml") or ""),
            csv_first=csv_first,
        )
        s3_key_used = None
        geojson = None
        if bases:
            dest = tmp_dir / f"{lid.replace('/', '_')}_probe.kml"
            for base in bases:
                keys: list[str] = []
                for pre in prefixes:
                    keys.extend(build_candidate_s3_keys(base, [pre]))
                s3_key_used = download_kml_first_match(bucket, keys, dest)
                if s3_key_used and dest.is_file():
                    try:
                        geojson, _meta = kml_to_geojson_polygon(dest)
                    except Exception:
                        geojson = None
                    if geojson:
                        break
        if not geojson:
            missing.append(lid)
        rows.append(
            {
                "location_id": lid,
                "tried_basenames": ";".join(bases[:12]) if bases else "",
                "s3_key_resolved": s3_key_used,
                "geometry_ok": bool(geojson),
            }
        )
    return rows, missing
