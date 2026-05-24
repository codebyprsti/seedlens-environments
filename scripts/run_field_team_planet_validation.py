#!/usr/bin/env python3
"""
Enrich manual field-team CSV with operations.crop_indices (Sentinel-2), S3 KML geometry,
and optional PlanetScope (PSScene) daily metadata via Planet Data API.

Does not modify crop_monitoring ingestion pipelines.

Usage:
  python scripts/run_field_team_planet_validation.py \\
    --csv "Data Entry_Field Team.csv" \\
    --date-start 2024-12-01 --date-end 2025-04-01 \\
    --output demo_outputs/field_team_planet_enriched.xlsx

  # Skip Planet (no PLANET_API_KEY): explicit daily rows with planet_status=NOT_QUERIED
  python scripts/run_field_team_planet_validation.py --csv "..." --skip-planet ...

  # No PostgreSQL (offline / VPN): CSV + daily grid only; optional S3 via Field Name column
  python scripts/run_field_team_planet_validation.py --no-db --skip-planet ...

Environment:
  DATABASE_* / core.config settings for DB; PLANET_API_KEY for Planet; AWS_* for S3 if not using instance role.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

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
    p = argparse.ArgumentParser(description="Field team CSV + Sentinel DB + Planet PSScene metadata")
    p.add_argument(
        "--csv",
        type=str,
        default=str(_root / "Data Entry_Field Team.csv"),
        help="Path to Data Entry_Field Team CSV",
    )
    p.add_argument("--date-start", type=str, required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--date-end", type=str, required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument(
        "--output",
        type=str,
        default=str(_root / "demo_outputs" / "field_team_planet_enriched.xlsx"),
        help="Output .xlsx path",
    )
    p.add_argument("--season-id", type=str, default=None, help="Filter crop_indices.season_id (optional)")
    p.add_argument(
        "--s3-bucket",
        type=str,
        default="prsti-public-data",
        help="S3 bucket for KML files",
    )
    p.add_argument(
        "--s3-prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help="S3 key prefix(es) to try with file basename (repeatable). Default: CG/CG batch prefix.",
    )
    p.add_argument("--skip-planet", action="store_true", help="Do not call Planet API")
    p.add_argument("--skip-s3", action="store_true", help="Do not download KML (Planet/Sentinel-only export)")
    p.add_argument(
        "--no-db",
        action="store_true",
        help="Do not connect to PostgreSQL (no crop_indices). Uses CSV KML name for optional S3 fetch.",
    )
    p.add_argument(
        "--planet-observations-only",
        action="store_true",
        help="One Excel row per Planet scene (actual acquisition dates only); no gap-filled daily grid.",
    )
    p.add_argument(
        "--planet-list-assets",
        action="store_true",
        help="For each scene row, call Data API /assets/ and list product keys (ortho_analytic_4b, etc.).",
    )
    p.add_argument(
        "--planet-max-asset-calls",
        type=int,
        default=200,
        metavar="N",
        help="Cap asset-key lookups per location (default 200).",
    )
    p.add_argument(
        "--no-satellite-filter",
        action="store_true",
        help="Include all CSV rows with a Field ID (do not require manual SYNC+NDVI satellite filled).",
    )
    p.add_argument(
        "--s3-try-csv-kml-first",
        action="store_true",
        help="Try CSV 'Field Name KML' basename on S3 before crop_indices file_name (helps KA vs DB path mismatch).",
    )
    p.add_argument(
        "--drop-manual-csv-columns",
        action="store_true",
        help="Omit ground-truth csv_sync / csv_ndvi / related manual columns from daily_enriched (Planet+Sentinel only).",
    )
    p.add_argument(
        "--planet-orders-zonal",
        action="store_true",
        help="Place clipped Orders (analytic bundle) per scene, zonal band means + NDVI/SAVI/EVI/GNDVI. Uses quota; slow.",
    )
    p.add_argument(
        "--planet-zonal-max-items",
        type=int,
        default=2,
        metavar="N",
        help="Max Planet Orders (unique item_ids) to run per location when --planet-orders-zonal (default 2).",
    )
    p.add_argument(
        "--planet-product-bundle",
        type=str,
        default="analytic_udm2",
        help="Orders product_bundle (default analytic_udm2). Try analytic_sr_udm2 for surface reflectance.",
    )
    p.add_argument(
        "--skip-locations-without-geometry",
        action="store_true",
        help="Do not write daily_enriched rows for locations where S3/KML did not resolve.",
    )
    p.add_argument(
        "--export-has-scene-rows-only",
        action="store_true",
        help="Drop rows where planet_status is not HAS_SCENE (removes NOT_QUERIED/MISSING placeholders).",
    )
    args = p.parse_args()

    import pandas as pd

    from field_validation.csv_loader import (
        first_row_for_location_id,
        get_filtered_with_location_ids,
        load_field_team_csv,
    )
    from field_validation.db_queries import (
        crop_indices_files_for_locations,
        crop_indices_sentinel_series,
    )
    from field_validation.geo_utils import geojson_polygon_area_ha
    from field_validation.output_builder import daily_placeholder_without_planet, merge_daily_enrichment
    from field_validation.planet_api import (
        daily_calendar_with_planet,
        enrich_rows_with_planet_asset_keys,
        planet_scenes_observation_rows,
    )
    from field_validation.s3_kml import (
        build_candidate_s3_keys,
        download_kml_first_match,
        kml_basenames_for_location,
        kml_to_geojson_polygon,
    )

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        logger.error("CSV not found: %s", csv_path)
        return 1

    d0 = date.fromisoformat(args.date_start)
    d1 = date.fromisoformat(args.date_end)
    if d0 > d1:
        d0, d1 = d1, d0

    df_raw = load_field_team_csv(csv_path)
    filtered, location_ids = get_filtered_with_location_ids(
        df_raw,
        require_satellite_pair=not args.no_satellite_filter,
    )
    logger.info(
        "CSV rows: %s; unique location_ids: %s (require_satellite_pair=%s)",
        len(filtered),
        len(location_ids),
        not args.no_satellite_filter,
    )
    print(f"unique_location_ids\t{len(location_ids)}")
    for lid in location_ids:
        print(f"location_id\t{lid}")

    prefixes = args.s3_prefix or [
        "seedworks/kml_files/input_files/CG/CG/",
        "seedworks/kml_files/input_files/KA/KA/",
    ]

    session = None
    mapping_rows: list[dict] = []
    if not args.no_db:
        try:
            from core.db import SessionLocal
        except ImportError as e:
            logger.error("core.db.SessionLocal unavailable: %s", e)
            return 1

        session = SessionLocal()
        try:
            mapping_rows = crop_indices_files_for_locations(
                session, location_ids, season_id=args.season_id
            )
        except Exception as e:
            logger.exception("DB query failed (season_id column may be missing; retry without filter): %s", e)
            try:
                mapping_rows = crop_indices_files_for_locations(session, location_ids, season_id=None)
            except Exception as e2:
                logger.exception("DB failed again: %s", e2)
                session.close()
                session = None
                logger.warning(
                    "Use --no-db to build Excel from CSV only when the database is unreachable."
                )
                return 1

    loc_to_files: dict[str, list[str]] = {}
    for m in mapping_rows:
        lid = str(m.get("location_id") or "")
        fn = (m.get("file_name") or "").strip()
        if not lid or not fn:
            continue
        loc_to_files.setdefault(lid, []).append(fn)
    for lid in location_ids:
        loc_to_files.setdefault(lid, [])

    if args.no_db:
        logger.info("--no-db: skipping crop_indices; Sentinel columns will be empty in daily_enriched.")
        for lid in location_ids:
            cs = first_row_for_location_id(filtered, lid) or {}
            kml_hint = (cs.get("field_name_kml") or "").strip()
            if kml_hint:
                base = kml_hint if kml_hint.lower().endswith(".kml") else f"{kml_hint}.kml"
                loc_to_files[lid] = [base.replace("+", " ").strip()]

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_daily: list[pd.DataFrame] = []
    map_records: list[dict] = []
    zonal_stats_all: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="field_val_kml_") as tmp:
        tmp_path = Path(tmp)
        for lid in location_ids:
            csv_static = first_row_for_location_id(filtered, lid) or {
                "state": "",
                "location_id": lid,
                "field_name_kml": "",
                "csv_sync_satellite": "",
                "csv_ndvi_satellite": "",
                "csv_validation_date": "",
            }
            files = list(dict.fromkeys(loc_to_files.get(lid, [])))
            s3_key_used = None
            geojson = None
            if not args.skip_s3:
                bases = kml_basenames_for_location(
                    files,
                    (csv_static.get("field_name_kml") or ""),
                    csv_first=args.s3_try_csv_kml_first,
                )
                if bases:
                    dest = tmp_path / f"{lid.replace('/', '_')}.kml"
                    for base in bases:
                        keys: list[str] = []
                        for pre in prefixes:
                            keys.extend(build_candidate_s3_keys(base, [pre]))
                        s3_key_used = download_kml_first_match(
                            args.s3_bucket,
                            keys,
                            dest,
                        )
                        if s3_key_used and dest.is_file():
                            try:
                                geojson, _meta = kml_to_geojson_polygon(dest)
                            except Exception as e:
                                logger.warning("KML parse failed %s: %s", lid, e)
                                geojson = None
                            if geojson:
                                break
                    if not geojson:
                        logger.warning(
                            "S3 KML not found or unparsed for location_id=%s tried_basenames=%s",
                            lid,
                            bases[:8],
                        )
                else:
                    logger.warning(
                        "No KML basename for location_id=%s (no crop_indices file_name and no CSV Field Name KML)",
                        lid,
                    )
            map_records.append(
                {
                    "location_id": lid,
                    "file_names_db": ";".join(files),
                    "s3_key_resolved": s3_key_used,
                    "geometry_available": bool(geojson),
                }
            )

            if args.skip_locations_without_geometry and not geojson:
                logger.info("Skipping location_id=%s (no geometry)", lid)
                continue

            if session is None:
                sent = []
            else:
                try:
                    sent = crop_indices_sentinel_series(
                        session, lid, d0, d1, season_id=args.season_id
                    )
                except Exception:
                    sent = crop_indices_sentinel_series(session, lid, d0, d1, season_id=None)

            if args.skip_planet:
                planet_daily = daily_placeholder_without_planet(d0, d1, "Skipped by --skip-planet")
            elif not geojson:
                planet_daily = daily_placeholder_without_planet(
                    d0, d1, "No field polygon (S3/KML missing or unparsed)"
                )
            elif not (os.environ.get("PLANET_API_KEY") or "").strip():
                planet_daily = daily_placeholder_without_planet(
                    d0, d1, "PLANET_API_KEY not set"
                )
            else:
                try:
                    if args.planet_observations_only:
                        planet_daily = planet_scenes_observation_rows(geojson, d0, d1)
                    else:
                        planet_daily = daily_calendar_with_planet(geojson, d0, d1)
                    if args.planet_list_assets:
                        planet_daily = enrich_rows_with_planet_asset_keys(
                            planet_daily,
                            max_lookups=args.planet_max_asset_calls,
                        )
                except Exception as e:
                    logger.warning("Planet API failed for %s: %s", lid, e)
                    planet_daily = daily_placeholder_without_planet(
                        d0, d1, f"Planet API error: {e}"
                    )

            if (
                args.planet_orders_zonal
                and geojson
                and (os.environ.get("PLANET_API_KEY") or "").strip()
                and not args.skip_planet
            ):
                from field_validation.planet_orders_zonal import enrich_rows_with_zonal_for_items

                work_orders = tmp_path / "planet_orders" / lid.replace("/", "_")
                want_ids: list[str] = []
                for r in planet_daily:
                    if r.get("planet_status") != "HAS_SCENE":
                        continue
                    iid = r.get("planet_best_item_id")
                    if not iid:
                        continue
                    sid = str(iid)
                    if sid not in want_ids:
                        want_ids.append(sid)
                    if len(want_ids) >= args.planet_zonal_max_items:
                        break
                if want_ids:
                    try:
                        planet_daily, zonal_loc = enrich_rows_with_zonal_for_items(
                            planet_daily,
                            geojson,
                            work_orders,
                            want_ids,
                            product_bundle=args.planet_product_bundle,
                        )
                        zonal_loc["location_id"] = lid
                        zonal_stats_all.append(zonal_loc)
                    except Exception as e:
                        logger.warning("Planet Orders zonal failed for %s: %s", lid, e)

            aoi_ha = None
            if geojson is not None:
                aoi_ha = geojson_polygon_area_ha(geojson)

            merged = merge_daily_enrichment(
                location_id=lid,
                csv_static=csv_static,
                file_names=files,
                s3_key_used=s3_key_used,
                planet_daily_rows=planet_daily,
                sentinel_series=sent,
                aoi_area_ha=aoi_ha,
                default_validation_year=d1.year,
            )
            if args.export_has_scene_rows_only and not merged.empty:
                merged = merged[merged["planet_status"] == "HAS_SCENE"].copy()
            if args.drop_manual_csv_columns:
                _drop = [
                    "csv_sync_satellite",
                    "csv_ndvi_satellite",
                    "csv_sync_source_note",
                    "note_csv_sync_is_phenology",
                ]
                merged = merged.drop(columns=[c for c in _drop if c in merged.columns], errors="ignore")
            if not merged.empty:
                all_daily.append(merged)

    if session is not None:
        session.close()

    df_map = pd.DataFrame(map_records)
    df_daily = pd.concat(all_daily, ignore_index=True) if all_daily else pd.DataFrame()
    orders_ok = sum(s.get("orders_succeeded", 0) for s in zonal_stats_all)
    orders_fail = sum(s.get("orders_failed", 0) for s in zonal_stats_all)
    summary_rows = [
        {"metric": "filtered_csv_rows", "value": len(filtered)},
        {"metric": "unique_location_ids", "value": len(location_ids)},
        {"metric": "date_range", "value": f"{d0.isoformat()}..{d1.isoformat()}"},
        {"metric": "no_db_mode", "value": bool(args.no_db)},
        {"metric": "require_satellite_pair", "value": not args.no_satellite_filter},
        {"metric": "PLANET_API_KEY_set", "value": bool((os.environ.get("PLANET_API_KEY") or "").strip())},
        {"metric": "planet_orders_zonal", "value": bool(args.planet_orders_zonal)},
        {"metric": "planet_product_bundle", "value": args.planet_product_bundle},
        {"metric": "s3_prefixes", "value": ";".join(prefixes)},
        {"metric": "orders_succeeded_total", "value": orders_ok},
        {"metric": "orders_failed_total", "value": orders_fail},
        {"metric": "daily_enriched_rows", "value": len(df_daily)},
        {"metric": "unique_locations_in_daily_enriched", "value": df_daily["location_id"].nunique() if not df_daily.empty and "location_id" in df_daily.columns else 0},
    ]
    summary = pd.DataFrame(summary_rows)

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        df_map.to_excel(writer, sheet_name="location_file_s3_map", index=False)
        if not df_daily.empty:
            df_daily.to_excel(writer, sheet_name="daily_enriched", index=False)
        if zonal_stats_all:
            pd.DataFrame(zonal_stats_all).to_excel(
                writer, sheet_name="planet_orders_zonal_stats", index=False
            )

    logger.info("Wrote %s", out_path)
    print(f"output\t{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
