#!/usr/bin/env python3
"""
STEP 1–6: Validate environment + S3 geometry, run Planet Orders zonal enrichment, print report.

Requires PLANET_API_KEY, AWS credentials for S3, optional DB for crop_indices file_name mapping.

Example:
  python scripts/planet_zonal_full_pipeline.py \\
    --date-start 2024-12-01 --date-end 2025-04-01 \\
    --output demo_outputs/planet_zonal_full_enriched.xlsx
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    p = argparse.ArgumentParser(description="Full Planet Orders zonal pipeline with validation steps")
    p.add_argument("--csv", type=str, default=str(_root / "Data Entry_Field Team.csv"))
    p.add_argument("--date-start", type=str, default="2024-12-01")
    p.add_argument("--date-end", type=str, default="2025-04-01")
    p.add_argument(
        "--output",
        type=str,
        default=str(_root / "demo_outputs" / "planet_zonal_full_enriched.xlsx"),
    )
    p.add_argument("--s3-bucket", type=str, default="prsti-public-data")
    p.add_argument("--s3-prefix", action="append", default=[], metavar="PREFIX")
    p.add_argument("--season-id", type=str, default=None)
    p.add_argument("--planet-product-bundle", type=str, default="analytic_udm2")
    p.add_argument("--planet-zonal-max-items", type=int, default=1)
    p.add_argument("--planet-list-assets", action="store_true", help="List /assets/ keys per scene")
    p.add_argument("--no-db", action="store_true", help="Skip PostgreSQL (CSV KML names only)")
    args = p.parse_args()

    # --- STEP 1: API key ---
    print("=== STEP 1: Environment (PLANET_API_KEY) ===")
    key = (os.environ.get("PLANET_API_KEY") or "").strip()
    if not key:
        print("STOP: PLANET_API_KEY is not set or empty after loading .env.")
        print("Set PLANET_API_KEY in .env or the environment, then re-run.")
        return 1
    print(f"PLANET_API_KEY: detected (length {len(key)} characters)")

    import pandas as pd

    from field_validation.csv_loader import (
        first_row_for_location_id,
        get_filtered_with_location_ids,
        load_field_team_csv,
    )
    from field_validation.db_queries import crop_indices_files_for_locations
    from field_validation.s3_geometry_probe import probe_locations_geometry

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"STOP: CSV not found: {csv_path}")
        return 1

    df_raw = load_field_team_csv(csv_path)
    filtered, location_ids = get_filtered_with_location_ids(df_raw, require_satellite_pair=True)
    print(f"CSV filtered rows: {len(filtered)}; unique location_ids: {len(location_ids)}")

    loc_to_files: dict[str, list[str]] = {}
    if not args.no_db:
        try:
            from core.db import SessionLocal

            session = SessionLocal()
            try:
                mapping_rows = crop_indices_files_for_locations(
                    session, location_ids, season_id=args.season_id
                )
            except Exception:
                mapping_rows = crop_indices_files_for_locations(session, location_ids, season_id=None)
            session.close()
            for m in mapping_rows:
                lid = str(m.get("location_id") or "")
                fn = (m.get("file_name") or "").strip()
                if lid and fn:
                    loc_to_files.setdefault(lid, []).append(fn)
        except Exception as e:
            logger.warning("DB mapping failed (%s); use CSV KML only", e)
    for lid in location_ids:
        loc_to_files.setdefault(lid, [])
    if args.no_db:
        for lid in location_ids:
            cs = first_row_for_location_id(filtered, lid) or {}
            kh = (cs.get("field_name_kml") or "").strip()
            if kh:
                base = kh if kh.lower().endswith(".kml") else f"{kh}.kml"
                loc_to_files[lid] = [base.replace("+", " ").strip()]

    prefixes = args.s3_prefix or [
        "seedworks/kml_files/input_files/CG/CG/",
        "seedworks/kml_files/input_files/KA/KA/",
    ]

    # --- STEP 2: S3 geometry ---
    print("\n=== STEP 2: S3 geometry probe ===")
    with tempfile.TemporaryDirectory(prefix="s3_probe_") as tmp:
        probe_rows, missing = probe_locations_geometry(
            location_ids=location_ids,
            csv_row_for_location=lambda lid: first_row_for_location_id(filtered, lid),
            loc_to_files=loc_to_files,
            bucket=args.s3_bucket,
            prefixes=prefixes,
            csv_first=True,
            tmp_dir=Path(tmp),
        )
    for pr in probe_rows:
        if pr.get("s3_key_resolved"):
            print(f"  OK {pr['location_id']}\ts3_key={pr['s3_key_resolved']}")
        elif pr.get("tried_basenames"):
            print(f"  MISS {pr['location_id']}\ttried={pr.get('tried_basenames', '')[:120]}")
        else:
            print(f"  MISS {pr['location_id']}\t(no basenames)")
    print(f"\nMissing geometry ({len(missing)}): {', '.join(missing) if missing else 'none'}")

    # --- STEP 3: Run enrichment via subprocess ---
    print("\n=== STEP 3: Running Planet Orders zonal pipeline (subprocess) ===")
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_root / "scripts" / "run_field_team_planet_validation.py"),
        "--csv",
        str(csv_path),
        "--date-start",
        args.date_start,
        "--date-end",
        args.date_end,
        "--output",
        str(out_path),
        "--s3-bucket",
        args.s3_bucket,
        "--planet-orders-zonal",
        "--planet-zonal-max-items",
        str(args.planet_zonal_max_items),
        "--planet-product-bundle",
        args.planet_product_bundle,
        "--planet-observations-only",
        "--drop-manual-csv-columns",
        "--skip-locations-without-geometry",
        "--export-has-scene-rows-only",
        "--s3-try-csv-kml-first",
    ]
    if args.no_db:
        cmd.append("--no-db")
    if args.season_id:
        cmd.extend(["--season-id", args.season_id])
    for pre in args.s3_prefix or []:
        cmd.extend(["--s3-prefix", pre])
    if args.planet_list_assets:
        cmd.append("--planet-list-assets")

    print("Command:", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(_root))
    if r.returncode != 0:
        print(f"STOP: enrichment subprocess exited with code {r.returncode}")
        return r.returncode

    # --- STEP 4 & 5: Validate sample + file stats ---
    print("\n=== STEP 4: Sample NDVI + timing SYNC check ===")
    if not out_path.is_file():
        print(f"No output file at {out_path}")
        return 1
    df = pd.read_excel(out_path, sheet_name="daily_enriched")
    sample = df.iloc[0] if len(df) else None
    if sample is not None:
        nir = sample.get("planet_raw_nir")
        red = sample.get("planet_raw_red")
        ndvi_stored = sample.get("planet_ndvi_raster")
        if nir is not None and red is not None and (float(nir) + float(red)) != 0:
            ndvi_calc = (float(nir) - float(red)) / (float(nir) + float(red))
            print(f"  location_id={sample.get('location_id')}  date={sample.get('calendar_date')}")
            print(f"  planet_raw_nir={nir}  planet_raw_red={red}")
            print(f"  NDVI (NIR-Red)/(NIR+Red) = {ndvi_calc}")
            print(f"  planet_ndvi_raster (stored) = {ndvi_stored}")
        else:
            print("  (No planet_raw_nir/red on first row — orders may have failed or no rows.)")
        print(
            f"  planet_sync_synchronous_index={sample.get('planet_sync_synchronous_index')}  "
            f"lag_days={sample.get('planet_acq_vs_validation_lag_days')}"
        )
    else:
        print("  daily_enriched is empty (no HAS_SCENE rows after filters).")

    print("\n=== STEP 5: Output summary ===")
    print(f"  output_file: {out_path.resolve()}")
    print(f"  total_rows: {len(df)}")
    if "location_id" in df.columns:
        print(f"  unique location_ids: {df['location_id'].nunique()}")
    if "planet_best_item_id" in df.columns:
        print(f"  unique Planet item_ids: {df['planet_best_item_id'].nunique(dropna=True)}")

    zf = None
    try:
        zf = pd.read_excel(out_path, sheet_name="planet_orders_zonal_stats")
    except Exception:
        pass
    if zf is not None and len(zf):
        print(f"  zonal stats rows: {len(zf)}")
        if "orders_failed" in zf.columns:
            print(f"  orders_failed (sum): {zf['orders_failed'].sum()}")
        if "orders_succeeded" in zf.columns:
            print(f"  orders_succeeded (sum): {zf['orders_succeeded'].sum()}")

    # --- STEP 6: Report ---
    print("\n=== STEP 6: Final report ===")
    print(f"  Missing geometries (from probe): {len(missing)} location(s)")
    print("  Bundle: analytic_udm2 includes ortho_analytic_4b (BGRN); no SWIR -> NDMI not from 4-band.")
    print("  RedEdge: only if you order an 8-band product; current zonal targets 4b GeoTIFF.")
    print("  SYNC columns: planet_sync_synchronous_index / planet_acq_vs_validation_lag_days = timing vs Validation Date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
