#!/usr/bin/env python3
"""
Run crop monitoring analysis for a KML farm polygon.
Computes indices and stores them in operations.crop_indices.

Usage:
    python scripts/run_crop_analysis.py path/to/farm.kml
    python scripts/run_crop_analysis.py path/to/farm.kml --start 2025-01-01 --end 2025-01-31
    python scripts/run_crop_analysis.py path/to/farm.kml --no-db

Requires: SH_CLIENT_ID, SH_CLIENT_SECRET for Copernicus. DB from core.config.
"""

import argparse
import logging
import sys
from pathlib import Path

import numpy as np

# Project root
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Load .env so SH_CLIENT_ID / SH_CLIENT_SECRET are available
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env")
except ImportError:
    pass

from crop_monitoring.pipeline import run_crop_analysis


def main():
    parser = argparse.ArgumentParser(description="Crop monitoring: KML -> indices -> DB")
    parser.add_argument("kml_path", type=Path, help="Path to KML file with farm polygon")
    parser.add_argument("--start", type=str, help="Start date YYYY-MM-DD (default: 30 days ago)")
    parser.add_argument("--end", type=str, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--no-db", action="store_true", help="Do not store results in database")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only print the report (no INFO logs)")
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
        for _ in ["crop_monitoring.pipeline", "crop_monitoring.sentinel_client"]:
            logging.getLogger(_).setLevel(logging.WARNING)

    if not args.kml_path.exists():
        print(f"Error: KML not found: {args.kml_path}", file=sys.stderr)
        sys.exit(1)

    db = None
    try:
        from core.db import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        # Force a real connection so we catch connection errors now, not later
        db.execute(text("SELECT 1"))
    except ImportError as e:
        if not args.no_db:
            print(f"Warning: DB not available (import failed: {e}). Running with --no-db.", file=sys.stderr)
        args.no_db = True
    except Exception as e:
        if not args.no_db:
            print(f"Warning: DB connection failed: {e}. Running with --no-db.", file=sys.stderr)
        args.no_db = True
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
            db = None
    had_db_session = db is not None

    try:
        result = run_crop_analysis(
            args.kml_path,
            start_date=args.start,
            end_date=args.end,
            store_in_db=not args.no_db,
            db_session=db,
        )
    except Exception as e:
        logging.getLogger(__name__).exception("Pipeline failed")
        print("\n-----------------------------")
        print("KML INPUT STATUS")
        print("-----------------------------")
        print("KML parsed successfully: NO")
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        if db is not None:
            db.close()

    meta = result.get("metadata") or {}
    village = meta.get("village") or ""
    grower = meta.get("grower") or ""
    variety = meta.get("variety") or ""
    area_ha = result.get("polygon_area_ha")
    area_str = f"{area_ha:.6f} ha" if area_ha is not None and not (isinstance(area_ha, float) and np.isnan(area_ha)) else "N/A"
    bbox = result.get("bbox")
    if bbox is not None and len(bbox) >= 4:
        bbox_str = f"min_lon={bbox[0]:.6f}, min_lat={bbox[1]:.6f}, max_lon={bbox[2]:.6f}, max_lat={bbox[3]:.6f}"
    else:
        bbox_str = "N/A"

    idx = result.get("indices") or {}
    index_order = [
        "NDVI", "SAVI", "NDMI", "NDRE", "GCI", "PSRI", "MSAVI", "EVI", "LAI", "LST_C",
        "vv_db", "vh_db", "vh_vv_ratio",
    ]

    def fmt_val(v):
        if v is None:
            return "NaN"
        if isinstance(v, float) and np.isnan(v):
            return "NaN"
        if isinstance(v, float):
            return f"{v:.6f}"
        return str(v)

    nan_indices = [k for k in index_order if (idx.get(k) is None or (isinstance(idx.get(k), float) and np.isnan(idx.get(k))))]
    if nan_indices:
        logging.getLogger(__name__).warning(
            "Indices %s are NaN or missing. Possible causes: no valid Sentinel-2 optical pixels in date range, "
            "cloud cover filtered out all scenes, or polygon outside scene coverage. Pipeline retries with relaxed cloud cover.",
            nan_indices,
        )

    print()
    print("-----------------------------")
    print("KML INPUT STATUS")
    print("-----------------------------")
    print("KML parsed successfully: YES")
    print()
    print("Village:", village or "N/A")
    print("Grower:", grower or "N/A")
    print("Variety:", variety or "N/A")
    conf = (result.get("metadata") or {}).get("confidence") or {}
    if conf:
        print("Confidence (village, grower, variety):", conf.get("village"), conf.get("grower"), conf.get("variety"))
    print()
    print("-----------------------------")
    print("MASTER DATA MATCHING")
    print("-----------------------------")
    loc_id = result.get("location_id")
    grower_id = result.get("grower_id")
    var_id = result.get("variety_id")
    loc_matched = result.get("location_matched")
    grower_matched = result.get("grower_matched")
    variety_matched = result.get("variety_matched")
    loc_score = result.get("location_score")
    grower_score = result.get("grower_score")
    variety_score = result.get("variety_score")
    if not had_db_session:
        print("(DB not connected; IDs could not be resolved)")
    else:
        print()
        print("Village Input:", village or "N/A")
        print("Matched Village:", loc_matched if loc_matched else "- (no match)")
        print("Location ID:", loc_id if loc_id else "-")
        print("Match Score:", f"{loc_score:.1f}" if loc_score is not None else "-")
        print()
        print("Grower Input:", grower or "N/A")
        print("Matched Grower:", grower_matched if grower_matched else "- (no match)")
        print("Grower ID:", grower_id if grower_id else "-")
        print("Match Score:", f"{grower_score:.1f}" if grower_score is not None else "-")
        print()
        print("Variety Input:", variety or "N/A")
        print("Matched Variety:", variety_matched if variety_matched else "- (no match)")
        print("Variety ID:", var_id if var_id else "-")
        print("Match Score:", f"{variety_score:.1f}" if variety_score is not None else "-")
    print()
    print("-----------------------------")
    print("MATCH RESULT")
    print("-----------------------------")
    print("Location Match:", "SUCCESS" if loc_id else "NO MATCH")
    print("Grower Match:", "SUCCESS" if grower_id else "NO MATCH")
    print("Variety Match:", "SUCCESS" if var_id else "NO MATCH")
    print()
    print("Polygon Area:", area_str)
    print("Bounding box:", bbox_str)
    det = result.get("detected_location") or {}
    if any(det.get(k) for k in ["village", "district", "state", "country"]):
        print()
        print("Detected location from polygon centroid:")
        print("  Village:", det.get("village") or "-")
        print("  District:", det.get("district") or "-")
        print("  State:", det.get("state") or "-")
        print("  Country:", det.get("country") or "-")
    print()
    print("-----------------------------")
    print("SATELLITE INDICES OUTPUT")
    print("-----------------------------")
    print("(Sentinel-2: B02-B11 | Sentinel-3: S8,S9 -> LST_C | Sentinel-1: VV,VH -> vv_db, vh_db, vh_vv_ratio)")
    print()
    for k in index_order:
        v = idx.get(k)
        print(f"{k}:", fmt_val(v))
    print("-----------------------------")
    if nan_indices:
        print(f"Note: {len(nan_indices)} index/indices are NaN (see logs for possible cause).")
    else:
        print("All indices computed successfully.")
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()
