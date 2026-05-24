"""
Standalone location ingestion: read KMLs from S3, reverse geocode with Google,
and insert new rows into operations.field_locations. Does not modify existing pipeline logic.
Run independently to populate field_locations from S3 KML files.

Critical: village (from centroid reverse geocode) is the ONLY source of truth for location.
extracted_village (from KML Placemark name) is stored for comparison/validation only and
is NEVER used for uniqueness checks or as the main village value.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# S3 default for CG folder
DEFAULT_BUCKET = "prsti-public-data"
DEFAULT_PREFIX = "seedworks/kml_files/input_files/CG/"

# Village from KML metadata: Village-Amlipara, Village: Amlipara, etc.
VILLAGE_FROM_METADATA_PATTERN = re.compile(r"Village[-:_\s]+([A-Za-z]+)", re.IGNORECASE)

# Punctuation to remove when normalizing extracted_village
_NORMALIZE_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)

# location_id format: IND-{STATECODE}-{NUMBER}, number starting at 600000
LOCATION_ID_START = 600000

# Match KML precision: store coordinates with max 7 decimal places (avoid float artifacts)
COORD_DECIMALS = 7


def _round_coord(x: float, decimals: int = None) -> float:
    return round(float(x), decimals if decimals is not None else COORD_DECIMALS)


def _get_centroid_from_geojson(geojson: dict) -> Tuple[Optional[float], Optional[float]]:
    """Return (lat, lon) of polygon centroid, or (None, None) on failure.
    Coordinates are rounded to 7 decimal places to match KML precision and avoid floating-point artifacts.
    """
    try:
        from shapely.geometry import shape
        geom = shape(geojson)
        centroid = geom.centroid
        lat = round(float(centroid.y), COORD_DECIMALS)
        lon = round(float(centroid.x), COORD_DECIMALS)
        return (lat, lon)
    except Exception as e:
        logger.debug("Centroid from geojson failed: %s", e)
        return (None, None)


def _extract_village_from_metadata(text: str) -> Optional[str]:
    """Extract village from metadata string, e.g. Village-Amlipara Anil Kumar Netam USRH-24 -> Amlipara."""
    if not text or not text.strip():
        return None
    m = VILLAGE_FROM_METADATA_PATTERN.search(text)
    return m.group(1).strip() if m else None


def _extract_village_from_placemark_name(name: str) -> Optional[str]:
    """
    Extract village from Placemark <name>: first token = extracted_village, remaining = grower.
    Normalize: lowercase, strip, remove punctuation. For comparison only; never used for lookup.
    Handles: "motkapalle chella raju", "Village-Motkapalle Chella Raju", "motkapalle_chella_raju".
    """
    if not name or not str(name).strip():
        return None
    # Replace underscores with space so "motkapalle_chella_raju" -> first token "motkapalle"
    text = str(name).strip().replace("_", " ")
    tokens = text.split()
    if not tokens:
        return None
    first = tokens[0].strip()
    # Strip Village- / Village_ / Village: prefix (Village-Grower format)
    for prefix in ("village-", "village_", "village:"):
        if first.lower().startswith(prefix):
            first = first[len(prefix):].strip()
            break
    normalized = _NORMALIZE_PUNCTUATION.sub("", first).strip().lower()
    return normalized if normalized else None


# Centroid tolerance for uniqueness: ~1 meter (0.00001 degrees)
CENTROID_TOLERANCE = 0.00001


def _location_exists_by_centroid(db, latitude: float, longitude: float) -> Optional[str]:
    """
    Return location_id if a row exists within tolerance of the centroid (lat, lon).
    Uniqueness is by FIELD LOCATION (centroid), not village. One location_id per centroid.
    """
    from sqlalchemy import text
    row = db.execute(
        text("""
            SELECT location_id FROM operations.field_locations
            WHERE ABS(latitude - :lat) < :tol AND ABS(longitude - :lon) < :tol
            LIMIT 1
        """),
        {"lat": latitude, "lon": longitude, "tol": CENTROID_TOLERANCE},
    ).fetchone()
    return row[0] if row else None


def _next_location_id_for_state(db, state_code: str) -> str:
    """Generate next IND-{STATECODE}-{NUMBER}. Uses MAX(location_id) WHERE state_code = ?."""
    from sqlalchemy import text
    sc = (state_code or "XX").strip()[:20] or "XX"
    row = db.execute(
        text("""
            SELECT MAX(location_id) FROM operations.field_locations
            WHERE state_code = :sc
        """),
        {"sc": sc},
    ).scalar()
    num = LOCATION_ID_START
    if row:
        match = re.search(r"\d+", str(row))
        if match:
            num = int(match.group()) + 1
    return f"IND-{sc}-{num}"


def _insert_field_location(
    db,
    location_id: str,
    extracted_village: Optional[str],
    village: str,
    mandal: Optional[str],
    district: Optional[str],
    state: Optional[str],
    state_code: Optional[str],
    postalcode: Optional[str],
    latitude: float,
    longitude: float,
) -> None:
    from sqlalchemy import text
    db.execute(
        text("""
            INSERT INTO operations.field_locations
            (location_id, extracted_village, village, mandal, district, state, state_code, postalcode, latitude, longitude)
            VALUES (:location_id, :extracted_village, :village, :mandal, :district, :state, :state_code, :postalcode, :latitude, :longitude)
        """),
        {
            "location_id": location_id,
            "extracted_village": (extracted_village and str(extracted_village).strip()[:200]) or None,
            "village": village,
            "mandal": mandal,
            "district": district,
            "state": state,
            "state_code": state_code,
            "postalcode": postalcode,
            "latitude": latitude,
            "longitude": longitude,
        },
    )
    db.commit()


def process_kml_locations_from_s3(
    bucket: str = DEFAULT_BUCKET,
    prefix: str = DEFAULT_PREFIX,
    s3_client: Any = None,
    db_session: Any = None,
    *,
    geocode_cache: Optional[Dict[Tuple[float, float], dict]] = None,
    limit: Optional[int] = None,
) -> Tuple[int, int]:
    """
    List KML files from S3 prefix, parse each, compute centroid, reverse geocode with Google,
    and insert new locations into operations.field_locations. Skips existing (village, mandal, district, state).
    Returns (inserted_count, skipped_count).
    """
    import boto3
    from services.google_reverse_geocode import get_location_from_coordinates

    if s3_client is None:
        s3_client = boto3.client("s3", region_name="ap-south-1")
    if db_session is None:
        try:
            from core.db import SessionLocal
            db_session = SessionLocal()
            close_db = True
        except ImportError:
            raise RuntimeError("DB session required: pass db_session or install core.db") from None
    else:
        close_db = False

    cache = geocode_cache if geocode_cache is not None else {}

    # List KML keys
    try:
        from crop_monitoring.s3_file_loader import list_kml_files, download_kml
    except ImportError:
        def list_kml_files(sc, b, p, skip_empty=True):
            p = p.rstrip("/") + "/" if p else ""
            keys = []
            paginator = sc.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=b, Prefix=p):
                for obj in page.get("Contents") or []:
                    k = (obj.get("Key") or "").strip()
                    if k.endswith(".kml") and (not skip_empty or (obj.get("Size") or 0) > 0):
                        keys.append(k)
            return sorted(keys)
        def download_kml(sc, b, k, path):
            sc.download_file(b, k, str(path))

    keys = list_kml_files(s3_client, bucket, prefix, skip_empty=True)
    if limit is not None and limit > 0:
        keys = keys[:limit]
        logger.info("Processing limit: %d files", len(keys))
    if not keys:
        logger.info("No KML files found at s3://%s/%s", bucket, prefix)
        if close_db and db_session:
            try:
                db_session.close()
            except Exception:
                pass
        return (0, 0)

    inserted = 0
    skipped = 0

    with tempfile.TemporaryDirectory(prefix="location_ingestor_") as tmpdir:
        tmp = Path(tmpdir)
        for key in keys:
            file_name = Path(key).name
            try:
                logger.info("Processing KML: %s", file_name)
                local_path = tmp / file_name
                download_kml(s3_client, bucket, key, local_path)
            except Exception as e:
                logger.warning("Download failed for %s: %s", file_name, e)
                continue

            try:
                from crop_monitoring.kml_parser import parse_kml
                geojson, metadata = parse_kml(local_path)
            except Exception as e:
                logger.warning("KML parse failed for %s: %s", file_name, e)
                continue

            lat, lon = _get_centroid_from_geojson(geojson)
            if lat is None or lon is None:
                logger.warning("Missing centroid for %s", file_name)
                continue

            lat_r, lon_r = _round_coord(lat), _round_coord(lon)
            cache_key = (lat_r, lon_r)
            if cache_key not in cache:
                geo = get_location_from_coordinates(lat, lon)
                cache[cache_key] = geo
            else:
                geo = cache[cache_key]

            # Village from Google reverse geocode (unchanged)
            village_google = (geo.get("village") or "").strip() or (geo.get("town") or "").strip()
            if not village_google:
                village_google = "Unknown"
            mandal_val = (geo.get("mandal") or "").strip() or None
            district_val = (geo.get("district") or "").strip() or None
            state_val = (geo.get("state") or "").strip() or None
            state_code_val = (geo.get("state_code") or "").strip() or None
            postalcode_val = (geo.get("postcode") or "").strip() or None

            # extracted_village: first token from Placemark <name>, normalized. Reference only; never used for lookup.
            # Fallback to filename stem when KML has no Placemark name (e.g. MOTKAPALL CHELLA RAJU.kml -> motkapall).
            placemark_name = metadata.get("placemark_name") or ""
            extracted_village = _extract_village_from_placemark_name(placemark_name)
            if not extracted_village and file_name:
                name_from_file = Path(file_name).stem
                extracted_village = _extract_village_from_placemark_name(name_from_file)
            logger.info("Extracted village from KML: %s", extracted_village if extracted_village else "(none)")
            logger.info("Village from centroid reverse geocode: %s", village_google)

            # Match check: compare extracted_village vs village (case-insensitive) for validation only
            if extracted_village:
                geocode_village_normalized = village_google.strip().lower()
                extracted_normalized = extracted_village.strip().lower()
                if geocode_village_normalized == extracted_normalized:
                    logger.info("Match status: MATCH")
                else:
                    logger.warning(
                        "Village mismatch:\n  KML extracted: %s\n  Geocode village: %s",
                        extracted_village,
                        village_google,
                    )
                    logger.info("Match status: MISMATCH")

            logger.info("Centroid: %.7f, %.7f", lat, lon)
            logger.info("Mandal: %s", mandal_val or "(none)")
            logger.info("District: %s", district_val or "(none)")
            logger.info("State: %s", state_val or "(none)")

            # Uniqueness: by centroid (one location_id per field/polygon), not by village
            logger.info("Location lookup by centroid")
            existing_id = _location_exists_by_centroid(db_session, lat, lon)
            if existing_id:
                logger.info("Existing location: %s", existing_id)
                skipped += 1
                continue
            logger.info("Existing location: none")

            if not state_code_val and state_val:
                state_code_val = state_val[:2].upper() if len(state_val) >= 2 else "XX"
            if not state_code_val:
                state_code_val = "XX"

            location_id = _next_location_id_for_state(db_session, state_code_val)
            logger.info("Generated location_id: %s", location_id)
            try:
                _insert_field_location(
                    db_session,
                    location_id=location_id,
                    extracted_village=extracted_village,
                    village=village_google,
                    mandal=mandal_val,
                    district=district_val,
                    state=state_val,
                    state_code=state_code_val,
                    postalcode=postalcode_val,
                    latitude=lat,
                    longitude=lon,
                )
                logger.info("Inserted location_id: %s (centroid: %.7f, %.7f)", location_id, lat, lon)
                inserted += 1
            except Exception as e:
                logger.exception("Insert failed for %s: %s", file_name, e)
                try:
                    db_session.rollback()
                except Exception:
                    pass

    if close_db and db_session:
        try:
            db_session.close()
        except Exception:
            pass

    logger.info("Location ingestor finished: inserted=%d, skipped=%d", inserted, skipped)
    return (inserted, skipped)


if __name__ == "__main__":
    import argparse
    import sys
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(_root / ".env")
    except ImportError:
        pass
    parser = argparse.ArgumentParser(description="Ingest locations from S3 KMLs into operations.field_locations")
    parser.add_argument("--limit", type=int, default=None, help="Max number of KML files to process")
    parser.add_argument("--prefix", type=str, default=DEFAULT_PREFIX, help="S3 prefix (default: CG folder)")
    parser.add_argument("--bucket", type=str, default=DEFAULT_BUCKET, help="S3 bucket")
    args = parser.parse_args()
    process_kml_locations_from_s3(bucket=args.bucket, prefix=args.prefix, limit=args.limit)
