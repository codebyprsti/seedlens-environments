"""
Production orchestrator v2: checkpointed, STAC-linked, bulk Statistical S2/S1/S3.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Optional

import numpy as np
from sqlalchemy.orm import Session

from crop_monitoring.database.sentinel_repositories import (
    sentinel2_row_exists,
    upsert_sentinel1_indices,
    upsert_sentinel2_indices,
    upsert_sentinel3_indices,
)
from crop_monitoring.satellite_pipeline.checkpoint import (
    CheckpointStatus,
    Stage,
    should_skip_stage,
    upsert_checkpoint,
)
from crop_monitoring.satellite_pipeline.cloud_config import DEFAULT_S2_CLOUD, S2CloudSettings
from crop_monitoring.satellite_pipeline.cloud_mask import assess_daily_quality, sufficient_valid_pixels
from crop_monitoring.satellite_pipeline.fetch_bulk import fetch_s2_bulk_statistical
from crop_monitoring.satellite_pipeline.gap_detection import find_missing_dates
from crop_monitoring.satellite_pipeline.layers.harmonize import merge_s2_daily_rows
from crop_monitoring.satellite_pipeline.layers.indices import (
    build_sentinel1_record,
    build_sentinel2_record,
    build_sentinel3_record,
)
from crop_monitoring.satellite_pipeline.layers.raw_ingestion import store_raw
from crop_monitoring.satellite_pipeline.reprocess import reprocess_file_from_raw
from crop_monitoring.satellite_pipeline.stac_catalog import (
    persist_stac_scenes,
    scenes_by_acquisition_date,
    search_stac_scenes,
)
from crop_monitoring.satellite_pipeline.field_context import FieldContext
from crop_monitoring.satellite_pipeline.raw_cache import (
    indices_row_exists,
    raw_exists_for_s1s3_bulk,
    raw_exists_for_s2_season,
)
from crop_monitoring.satellite_pipeline.temporal_batch import BatchStrategy, calendar_dates_inclusive

logger = logging.getLogger(__name__)

S1_LOOKBACK_DAYS = 12
S3_PAD_BEFORE = 1
S3_PAD_AFTER = 2
MAX_S1_S3_WORKERS = max(1, int(os.environ.get("SATELLITE_MAX_S1_S3_WORKERS", "4")))
API_RETRY_ATTEMPTS = 3
API_RETRY_BASE_SEC = 2.0
FAILED_DAY_RETRY = 2


def _retry(fn, *args, **kwargs):
    last: Exception | None = None
    for attempt in range(API_RETRY_ATTEMPTS):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last = e
            wait = API_RETRY_BASE_SEC * (2**attempt)
            logger.warning("API attempt %s failed: %s; retry in %.1fs", attempt + 1, e, wait)
            time.sleep(wait)
    raise last  # type: ignore[misc]


def _polygon_mean_s1_s3(geojson: dict, analysis_date_str: str) -> tuple[Any, ...]:
    from crop_monitoring.sar_calculator import compute_sar_metrics
    from crop_monitoring.sentinel_client import fetch_s1_sar, fetch_s3_thermal
    from crop_monitoring.temperature_calculator import lst_celsius as lst_celsius_array

    try:
        d0 = date.fromisoformat(str(analysis_date_str)[:10])
    except ValueError:
        return (None,) * 6

    s3_from = (d0 - timedelta(days=S3_PAD_BEFORE)).isoformat()
    s3_to = (d0 + timedelta(days=S3_PAD_AFTER + 1)).isoformat()
    s1_from = (d0 - timedelta(days=S1_LOOKBACK_DAYS)).isoformat()
    s1_to = (d0 + timedelta(days=1)).isoformat()

    s7 = s8 = s9 = lst_c = vv_lin = vh_lin = None
    s1 = None

    try:
        s3 = _retry(fetch_s3_thermal, geojson, (s3_from, s3_to))
        if s3:
            for key, target in (("S7", "s7"), ("S8", "s8"), ("S9", "s9")):
                if key in s3:
                    arr = np.asarray(s3[key], dtype=np.float64).ravel()
                    if np.any(np.isfinite(arr)):
                        if target == "s7":
                            s7 = float(np.nanmean(arr[np.isfinite(arr)]))
                        elif target == "s8":
                            s8 = float(np.nanmean(arr[np.isfinite(arr)]))
                        else:
                            s9 = float(np.nanmean(arr[np.isfinite(arr)]))
            if s8 is not None and s9 is not None:
                lst_c = float(lst_celsius_array(np.array([s8]), np.array([s9]))[0])
    except Exception as e:
        logger.debug("S3 %s: %s", analysis_date_str, e)

    try:
        s1 = _retry(fetch_s1_sar, geojson, (s1_from, s1_to))
        if s1 and "VV" in s1:
            vv = np.asarray(s1["VV"], dtype=np.float64)
            vh = np.asarray(s1["VH"], dtype=np.float64)
            valid = np.isfinite(vv) & (vv > 0)
            if np.any(valid):
                vv_lin = float(np.nanmean(vv[valid]))
                vh_lin = float(np.nanmean(vh[valid]))
    except Exception as e:
        logger.debug("S1 %s: %s", analysis_date_str, e)

    return s7, s8, s9, lst_c, vv_lin, vh_lin


class SatelliteIngestionOrchestrator:
    def _write_s1_s3_for_date(
        self,
        ad: str,
        *,
        s1s3_by_date: dict[str, tuple],
        location_id: str,
        file_name: str,
        internal_id: Optional[str],
        grower_name: Optional[str],
        grower_id: Optional[str],
        skip_existing: bool,
        counts: dict[str, Any],
        orbit_direction_s1: Optional[str] = None,
        orbit_direction_s3: Optional[str] = None,
        raw_id_s1: Optional[int] = None,
        raw_id_s3: Optional[int] = None,
    ) -> bool:
        """Upsert S1/S3 rows for one calendar day. Returns True if any row written."""
        if skip_existing and internal_id:
            if indices_row_exists(
                self.db, "S1", location_id=location_id, season_id=self.season_id,
                internal_id=internal_id, observation_date=ad,
            ) and indices_row_exists(
                self.db, "S3", location_id=location_id, season_id=self.season_id,
                internal_id=internal_id, observation_date=ad,
            ):
                return False

        s7, s8, s9, _lst, vv_lin, vh_lin = s1s3_by_date.get(ad, (None,) * 6)
        wrote = False
        s1_rec = build_sentinel1_record(
            vv_lin,
            vh_lin,
            location_id=location_id,
            file_name=file_name,
            season_id=self.season_id,
            acquisition_date=ad,
            raw_observation_id=raw_id_s1,
            internal_id=internal_id,
            grower_name=grower_name,
            grower_id=grower_id,
        )
        if orbit_direction_s1:
            s1_rec["orbit_direction"] = orbit_direction_s1
        if upsert_sentinel1_indices(self.db, s1_rec):
            counts["s1"] += 1
            wrote = True

        s3_rec = build_sentinel3_record(
            s7, s8, s9,
            location_id=location_id,
            file_name=file_name,
            season_id=self.season_id,
            acquisition_date=ad,
            raw_observation_id=raw_id_s3,
            internal_id=internal_id,
            grower_name=grower_name,
            grower_id=grower_id,
        )
        if orbit_direction_s3:
            s3_rec["orbit_direction"] = orbit_direction_s3
        if upsert_sentinel3_indices(self.db, s3_rec):
            counts["s3"] += 1
            wrote = True
        return wrote

    def _fetch_s1s3_process_per_day(
        self,
        geojson: dict,
        api_dates: list[str],
    ) -> dict[str, tuple[Any, ...]]:
        """Legacy Process API path — one S1 + one S3 call per calendar day."""
        s1s3_by_date: dict[str, tuple[Any, ...]] = {}
        if not api_dates:
            return s1s3_by_date
        with ThreadPoolExecutor(max_workers=min(MAX_S1_S3_WORKERS, len(api_dates))) as ex:
            futs = {ex.submit(_polygon_mean_s1_s3, geojson, ad): ad for ad in api_dates}
            for fut in as_completed(futs):
                ad = futs[fut]
                for attempt in range(FAILED_DAY_RETRY):
                    try:
                        s1s3_by_date[ad] = fut.result()
                        break
                    except Exception as e:
                        if attempt + 1 >= FAILED_DAY_RETRY:
                            logger.warning("S1/S3 Process API failed date=%s: %s", ad, e)
                            s1s3_by_date[ad] = (None,) * 6
                        time.sleep(API_RETRY_BASE_SEC)
        return s1s3_by_date

    def _fetch_s1s3_calendar(
        self,
        *,
        geojson: dict,
        file_name: str,
        location_id: str,
        start_str: str,
        end_str: str,
        calendar_dates: list[str],
        s1s3_fetch_dates: list[str],
        iid: Optional[str],
        grower_name: Optional[str],
        grower_id: Optional[str],
    ) -> tuple[dict[str, tuple[Any, ...]], dict[str, str], dict[str, str], Optional[int], Optional[int]]:
        """
        Bulk Statistical API (default) or Process API fallback.
        Returns (s1s3_by_date, s1_orbit_by_date, s3_orbit_by_date, raw_id_s1, raw_id_s3).
        """
        from crop_monitoring.satellite_pipeline.fetch_s1_s3_statistical import (
            fetch_s1s3_bulk_calendar,
            load_s1s3_from_stored_raw,
            use_s1s3_statistical_api,
        )
        from crop_monitoring.satellite_pipeline.reprocess import load_raw_rows

        expansion_dates = calendar_dates if self.s1_s3_every_calendar_day else s1s3_fetch_dates
        s1s3_by_date: dict[str, tuple[Any, ...]] = {}
        s1_orbit: dict[str, str] = {}
        s3_orbit: dict[str, str] = {}
        raw_id_s1: Optional[int] = None
        raw_id_s3: Optional[int] = None

        if not expansion_dates:
            return s1s3_by_date, s1_orbit, s3_orbit, raw_id_s1, raw_id_s3

        raw_cached = raw_exists_for_s1s3_bulk(
            self.db,
            location_id=location_id,
            file_name=file_name,
            season_id=self.season_id,
            start_date=start_str,
        )
        skip_fetch = (
            should_skip_stage(self.db, self.run_id, file_name, Stage.RAW_S1, resume=self.resume)
            or raw_cached
        )

        use_stat = use_s1s3_statistical_api()

        if skip_fetch and use_stat:
            s1_payload = s3_payload = None
            for row in load_raw_rows(
                self.db, location_id=location_id, file_name=file_name
            ):
                src = row.get("source") or ""
                if src == "copernicus_s1_statistical_v2":
                    s1_payload = row.get("raw_response")
                    raw_id_s1 = raw_id_s1 or row.get("id")
                if src == "copernicus_s3_statistical_v2":
                    s3_payload = row.get("raw_response")
                    raw_id_s3 = raw_id_s3 or row.get("id")
            if s1_payload and s3_payload:
                s1s3_by_date = load_s1s3_from_stored_raw(
                    s1_payload, s3_payload, expansion_dates
                )
                logger.info(
                    "Reusing stored S1/S3 Statistical raw for %s (%d calendar days)",
                    file_name,
                    len(expansion_dates),
                )
                return s1s3_by_date, s1_orbit, s3_orbit, raw_id_s1, raw_id_s3
            logger.warning("Stored S1/S3 raw unparseable for %s — refetching", file_name)
            skip_fetch = False

        if use_stat and not skip_fetch:
            try:
                bulk_by_date, s1_raw, s3_raw, api_stats = _retry(
                    fetch_s1s3_bulk_calendar,
                    geojson,
                    start_str,
                    end_str,
                    expansion_dates,
                    batch_strategy=self.batch_strategy,
                )
                s1s3_by_date = bulk_by_date
                from crop_monitoring.satellite_pipeline.layers.harmonize import (
                    parse_s1_stats_response,
                    parse_s3_stats_response,
                )

                s1_daily_rows = [
                    parse_s1_stats_response(item)
                    for chunk in s1_raw
                    for item in (chunk.get("data") or [])
                ]
                s3_daily_rows = [
                    parse_s3_stats_response(item)
                    for chunk in s3_raw
                    for item in (chunk.get("data") or [])
                ]
                for row in s1_daily_rows:
                    ad = row.get("acquisition_date")
                    if ad and row.get("orbit_direction"):
                        s1_orbit[str(ad)[:10]] = row["orbit_direction"]
                for row in s3_daily_rows:
                    ad = row.get("acquisition_date")
                    if ad and row.get("orbit_direction"):
                        s3_orbit[str(ad)[:10]] = row["orbit_direction"]

                raw_id_s1 = store_raw(
                    self.db,
                    location_id=location_id,
                    file_name=file_name,
                    satellite="S1",
                    source="copernicus_s1_statistical_v2",
                    api_type="statistical",
                    season_id=self.season_id,
                    run_id=self.run_id,
                    internal_id=iid,
                    grower_name=grower_name,
                    grower_id=grower_id,
                    bands=s1_daily_rows,
                    raw_response={
                        "layer": "statistical_s1_grd_p1d",
                        "interval": [start_str, end_str],
                        "strategy": self.batch_strategy,
                        "api_stats": api_stats,
                        "chunks": s1_raw,
                    },
                    metadata={"api_stats": api_stats},
                )
                raw_id_s3 = store_raw(
                    self.db,
                    location_id=location_id,
                    file_name=file_name,
                    satellite="S3",
                    source="copernicus_s3_statistical_v2",
                    api_type="statistical",
                    season_id=self.season_id,
                    run_id=self.run_id,
                    internal_id=iid,
                    grower_name=grower_name,
                    grower_id=grower_id,
                    bands=s3_daily_rows,
                    raw_response={
                        "layer": "statistical_s3_slstr_p1d",
                        "interval": [start_str, end_str],
                        "strategy": self.batch_strategy,
                        "api_stats": api_stats,
                        "chunks": s3_raw,
                    },
                    metadata={"api_stats": api_stats},
                )
                upsert_checkpoint(
                    self.db,
                    run_id=self.run_id,
                    file_name=file_name,
                    stage=Stage.RAW_S1,
                    status=CheckpointStatus.DONE,
                    location_id=location_id,
                    raw_observation_id=raw_id_s1,
                )
                upsert_checkpoint(
                    self.db,
                    run_id=self.run_id,
                    file_name=file_name,
                    stage=Stage.RAW_S3,
                    status=CheckpointStatus.DONE,
                    location_id=location_id,
                    raw_observation_id=raw_id_s3,
                )
                logger.info(
                    "%s: S1/S3 Statistical bulk OK (%s)",
                    file_name,
                    api_stats,
                )
                return s1s3_by_date, s1_orbit, s3_orbit, raw_id_s1, raw_id_s3
            except Exception as e:
                logger.warning(
                    "S1/S3 Statistical bulk failed for %s: %s — falling back to Process API",
                    file_name,
                    e,
                )

        # Process API fallback (per-day) for remaining/missing dates only
        api_dates = list(s1s3_fetch_dates)
        if skip_fetch and not use_stat:
            return s1s3_by_date, s1_orbit, s3_orbit, raw_id_s1, raw_id_s3
        process_dates = api_dates
        if use_stat and s1s3_by_date:
            process_dates = [
                ad
                for ad in api_dates
                if ad not in s1s3_by_date or all(v is None for v in s1s3_by_date.get(ad, (None,) * 6))
            ]
        if process_dates:
            logger.info(
                "%s: S1/S3 Process API fallback for %d days",
                file_name,
                len(process_dates),
            )
            proc = self._fetch_s1s3_process_per_day(geojson, process_dates)
            s1s3_by_date.update(proc)
        return s1s3_by_date, s1_orbit, s3_orbit, raw_id_s1, raw_id_s3

    def __init__(
        self,
        db: Session,
        *,
        season_id: str,
        start_date: date,
        end_date: date,
        max_cloud_cover: Optional[float] = None,
        cloud_settings: Optional[S2CloudSettings] = None,
        run_id: Optional[uuid.UUID] = None,
        batch_strategy: BatchStrategy = "quarterly",
        resume: bool = True,
        use_stac: bool = True,
        s1_s3_every_calendar_day: bool = True,
    ):
        self.db = db
        self.season_id = season_id
        self.start_date = start_date
        self.end_date = end_date
        self.cloud = cloud_settings or DEFAULT_S2_CLOUD
        if max_cloud_cover is not None:
            self.cloud = S2CloudSettings.from_env({"max_scene_cloud_pct": float(max_cloud_cover)})
        self.run_id = run_id or uuid.uuid4()
        self.batch_strategy = batch_strategy
        self.resume = resume
        self.use_stac = use_stac
        self.s1_s3_every_calendar_day = s1_s3_every_calendar_day

    def process_kml_file_reprocess(
        self,
        *,
        location_id: str,
        file_name: str,
        field: Optional[FieldContext] = None,
    ) -> dict[str, int]:
        """Layer: raw → indices without API."""
        grower_name = field.grower_name if field else None
        grower_id = field.grower_id if field else None
        internal_id = field.internal_id if field else None
        if not grower_name and internal_id:
            from crop_monitoring.satellite_pipeline.harvest_registry import load_registry_by_internal_id

            reg = load_registry_by_internal_id(self.db, internal_id)
            if reg:
                grower_name = reg.get("grower_name")
                grower_id = reg.get("grower_id")
        if grower_name and not grower_id:
            from crop_monitoring.satellite_pipeline.grower_resolver import ensure_grower_linked

            grower_id, grower_name, _ = ensure_grower_linked(
                self.db,
                grower_name=grower_name,
                location_id=location_id,
            )
        upsert_checkpoint(
            self.db,
            run_id=self.run_id,
            file_name=file_name,
            stage=Stage.HARMONIZE_S2,
            status=CheckpointStatus.PENDING,
            location_id=location_id,
        )
        counts = reprocess_file_from_raw(
            self.db,
            location_id=location_id,
            file_name=file_name,
            season_id=self.season_id,
            max_cloud_cover=self.cloud.max_scene_cloud_pct,
            internal_id=internal_id,
            grower_name=grower_name,
            grower_id=grower_id,
        )
        upsert_checkpoint(
            self.db,
            run_id=self.run_id,
            file_name=file_name,
            stage=Stage.HARMONIZE_S2,
            status=CheckpointStatus.DONE,
            location_id=location_id,
        )
        upsert_checkpoint(
            self.db,
            run_id=self.run_id,
            file_name=file_name,
            stage=Stage.COMPLETE,
            status=CheckpointStatus.DONE,
            location_id=location_id,
        )
        return counts

    def process_kml_file(
        self,
        *,
        geojson: dict,
        file_name: str,
        location_id: str,
        skip_existing: bool = True,
        reprocess_only: bool = False,
        field: Optional[FieldContext] = None,
    ) -> dict[str, Any]:
        if reprocess_only:
            c = self.process_kml_file_reprocess(
                location_id=location_id, file_name=file_name, field=field
            )
            return {**c, "skipped": 0, "errors": 0, "gaps": {}}

        counts: dict[str, Any] = {"s2": 0, "s1": 0, "s3": 0, "skipped": 0, "errors": 0, "gaps": {}}
        iid = field.internal_id if field else None
        grower_name = field.grower_name if field else None
        grower_id = field.grower_id if field else None
        if grower_name and not grower_id:
            from crop_monitoring.satellite_pipeline.grower_resolver import ensure_grower_linked

            grower_id, grower_name, _ = ensure_grower_linked(
                self.db,
                grower_name=grower_name,
                grower_id=grower_id,
                location_id=location_id,
            )
            if field:
                field.grower_id = grower_id
                field.grower_name = grower_name

        if should_skip_stage(self.db, self.run_id, file_name, Stage.COMPLETE, resume=self.resume):
            counts["skipped"] = 1
            return counts

        start_str = self.start_date.isoformat()
        end_str = self.end_date.isoformat()
        stac_by_date: dict[str, dict] = {}

        # --- STAC catalogue (metadata cache) ---
        if self.use_stac and not should_skip_stage(
            self.db, self.run_id, file_name, Stage.STAC, resume=self.resume
        ):
            try:
                scenes = _retry(
                    search_stac_scenes,
                    geojson,
                    start_str,
                    end_str,
                    max_cloud_cover=self.cloud.max_scene_cloud_pct,
                )
                stac_by_date = scenes_by_acquisition_date(scenes)
                persist_stac_scenes(
                    self.db,
                    location_id=location_id,
                    file_name=file_name,
                    season_id=self.season_id,
                    satellite="S2",
                    scenes=list(stac_by_date.values()),
                )
                upsert_checkpoint(
                    self.db,
                    run_id=self.run_id,
                    file_name=file_name,
                    stage=Stage.STAC,
                    status=CheckpointStatus.DONE,
                    location_id=location_id,
                )
            except Exception as e:
                upsert_checkpoint(
                    self.db,
                    run_id=self.run_id,
                    file_name=file_name,
                    stage=Stage.STAC,
                    status=CheckpointStatus.FAILED,
                    location_id=location_id,
                    last_error=str(e),
                )
                logger.warning("STAC failed for %s: %s", file_name, e)

        index_rows: list[dict] = []
        band_rows: list[dict] = []
        merged_days: list[dict] = []
        raw_id_indices: Optional[int] = None

        # --- Bulk S2 Statistical (chunked) ---
        raw_cached = raw_exists_for_s2_season(
            self.db,
            location_id=location_id,
            file_name=file_name,
            season_id=self.season_id,
            start_date=start_str,
            end_date=end_str,
        )
        skip_raw_fetch = should_skip_stage(
            self.db, self.run_id, file_name, Stage.RAW_S2, resume=self.resume
        ) or raw_cached

        if skip_raw_fetch:
            from crop_monitoring.satellite_pipeline.reprocess import load_s2_merged_days_from_raw

            merged_days = load_s2_merged_days_from_raw(
                self.db, location_id=location_id, file_name=file_name
            )
            if merged_days:
                logger.info("Reusing stored S2 raw for %s (%d days)", file_name, len(merged_days))
            else:
                logger.warning("Stored raw unparseable for %s — refetching from API", file_name)
                skip_raw_fetch = False

        if not skip_raw_fetch:
            try:
                index_rows, band_rows, raw_idx, raw_bands = _retry(
                    fetch_s2_bulk_statistical,
                    geojson,
                    start_str,
                    end_str,
                    cloud=self.cloud,
                    batch_strategy=self.batch_strategy,
                )
                raws_idx = [raw_idx] if raw_idx else []
                raws_bands = [raw_bands] if raw_bands else []
                raw_idx = raws_idx[0] if raws_idx else None
                raw_bands = raws_bands[0] if raws_bands else None
            except Exception as e:
                upsert_checkpoint(
                    self.db,
                    run_id=self.run_id,
                    file_name=file_name,
                    stage=Stage.RAW_S2,
                    status=CheckpointStatus.FAILED,
                    location_id=location_id,
                    last_error=str(e),
                )
                counts["errors"] += 1
                return counts

            raw_id_indices = store_raw(
                self.db,
                location_id=location_id,
                file_name=file_name,
                satellite="S2",
                source="copernicus_s2_indices_v2",
                api_type="statistical",
                season_id=self.season_id,
                run_id=self.run_id,
                internal_id=iid,
                grower_name=grower_name,
                grower_id=grower_id,
                max_cloud_cover_pct=self.cloud.max_scene_cloud_pct,
                raw_response={
                    "layer": "statistical_indices_v3_scl_masked",
                    "cloud_policy": {
                        "max_scene_cloud_pct": self.cloud.max_scene_cloud_pct,
                        "min_valid_pixel_pct": self.cloud.min_valid_pixel_pct,
                        "ladder": list(self.cloud.effective_ladder()),
                    },
                    "interval": [start_str, end_str],
                    "strategy": self.batch_strategy,
                    "response": raw_idx,
                },
                indices=index_rows,
            )
            store_raw(
                self.db,
                location_id=location_id,
                file_name=file_name,
                satellite="S2",
                source="copernicus_s2_bands_v2",
                api_type="statistical",
                season_id=self.season_id,
                run_id=self.run_id,
                internal_id=iid,
                grower_name=grower_name,
                grower_id=grower_id,
                bands=band_rows,
                raw_response={
                    "layer": "statistical_bands_v2",
                    "interval": [start_str, end_str],
                    "response": raw_bands,
                },
            )
            upsert_checkpoint(
                self.db,
                run_id=self.run_id,
                file_name=file_name,
                stage=Stage.RAW_S2,
                status=CheckpointStatus.DONE,
                location_id=location_id,
                raw_observation_id=raw_id_indices,
            )

        if not skip_raw_fetch:
            band_by_date = {r.get("acquisition_date"): r for r in band_rows if r.get("acquisition_date")}
            merged_days = []
            for ir in index_rows:
                ad = ir.get("acquisition_date")
                if not ad:
                    continue
                merged_days.append(merge_s2_daily_rows(band_by_date.get(ad, {"acquisition_date": ad}), ir))

        if not merged_days and not skip_raw_fetch:
            logger.warning(
                "No S2 daily rows parsed for %s — trying legacy Statistical indices path",
                file_name,
            )
            try:
                from crop_monitoring.statistical_client import fetch_s2_indices_timeseries

                legacy = _retry(
                    fetch_s2_indices_timeseries,
                    geojson,
                    start_str,
                    end_str,
                    maxcc=self.cloud.max_scene_cloud_pct,
                )
                for row in legacy:
                    ad = row.get("analysis_date") or row.get("interval_to")
                    if ad:
                        merged_days.append(
                            {
                                "acquisition_date": str(ad)[:10],
                                "ndvi": row.get("NDVI"),
                                "savi": row.get("SAVI"),
                                "ndmi": row.get("NDMI"),
                                "ndre": row.get("NDRE"),
                                "gci": row.get("GCI"),
                                "psri": row.get("PSRI"),
                                "msavi": row.get("MSAVI"),
                                "evi": row.get("EVI"),
                                "ndwi": row.get("NDWI"),
                                "lai": row.get("LAI"),
                            }
                        )
            except Exception as e:
                logger.warning("Legacy Statistical fallback failed for %s: %s", file_name, e)

        logger.info(
            "%s: index_rows=%d band_rows=%d merged_days=%d",
            file_name,
            len(index_rows),
            len(band_rows),
            len(merged_days),
        )

        s2_observation_dates = sorted({m["acquisition_date"] for m in merged_days if m.get("acquisition_date")})
        calendar_dates = calendar_dates_inclusive(self.start_date, self.end_date)
        s1s3_fetch_dates = calendar_dates if self.s1_s3_every_calendar_day else s2_observation_dates
        logger.info(
            "%s: S2 observation days=%d | S1/S3 fetch days=%d (range %s..%s)",
            file_name,
            len(s2_observation_dates),
            len(s1s3_fetch_dates),
            start_str,
            end_str,
        )
        s1s3_by_date: dict[str, tuple[Any, ...]] = {}
        s1_orbit_by_date: dict[str, str] = {}
        s3_orbit_by_date: dict[str, str] = {}
        raw_id_s1: Optional[int] = None
        raw_id_s3: Optional[int] = None

        s1s3_api_dates = list(s1s3_fetch_dates)
        if skip_existing and iid and s1s3_api_dates:
            s1s3_api_dates = [
                ad
                for ad in s1s3_fetch_dates
                if not (
                    indices_row_exists(
                        self.db,
                        "S1",
                        location_id=location_id,
                        season_id=self.season_id,
                        internal_id=iid,
                        observation_date=ad,
                    )
                    and indices_row_exists(
                        self.db,
                        "S3",
                        location_id=location_id,
                        season_id=self.season_id,
                        internal_id=iid,
                        observation_date=ad,
                    )
                )
            ]
            skipped_days = len(s1s3_fetch_dates) - len(s1s3_api_dates)
            if skipped_days:
                logger.info(
                    "%s: S1/S3 skip %d/%d days already in DB; fetching %d",
                    file_name,
                    skipped_days,
                    len(s1s3_fetch_dates),
                    len(s1s3_api_dates),
                )

        if s1s3_fetch_dates and not should_skip_stage(
            self.db, self.run_id, file_name, Stage.INDICES_S1, resume=self.resume
        ):
            if s1s3_api_dates or not (skip_existing and iid):
                s1s3_by_date, s1_orbit_by_date, s3_orbit_by_date, raw_id_s1, raw_id_s3 = (
                    self._fetch_s1s3_calendar(
                        geojson=geojson,
                        file_name=file_name,
                        location_id=location_id,
                        start_str=start_str,
                        end_str=end_str,
                        calendar_dates=calendar_dates,
                        s1s3_fetch_dates=s1s3_fetch_dates,
                        iid=iid,
                        grower_name=grower_name,
                        grower_id=grower_id,
                    )
                )

        db_dates: set[str] = set()
        s1s3_written: set[str] = set()
        stat_dates = set(s2_observation_dates)
        cat_dates = set(stac_by_date.keys())

        for merged in merged_days:
            ad = merged.get("acquisition_date")
            if not ad:
                continue
            s2_skip = skip_existing and sentinel2_row_exists(
                self.db,
                location_id,
                file_name,
                ad,
                self.season_id,
                internal_id=iid,
            )

            stac_meta = stac_by_date.get(ad) or {}
            if stac_meta.get("cloud_cover_pct") is not None:
                merged["scene_cloud_cover_pct"] = stac_meta["cloud_cover_pct"]

            quality = assess_daily_quality(merged, min_valid_pct=self.cloud.min_valid_pixel_pct)
            merged.update(quality)

            s2_rec = build_sentinel2_record(
                merged,
                location_id=location_id,
                file_name=file_name,
                season_id=self.season_id,
                raw_observation_id=raw_id_indices,
                max_cloud_cover_pct=self.cloud.max_scene_cloud_pct,
                internal_id=iid,
                grower_name=grower_name,
                grower_id=grower_id,
            )
            s2_rec["product_id"] = stac_meta.get("product_id")
            s2_usable = bool(quality.get("usable_scene"))
            if not s2_skip:
                if upsert_sentinel2_indices(self.db, s2_rec):
                    counts["s2"] += 1
                    db_dates.add(ad)
            else:
                counts["skipped"] += 1

            if not s2_usable:
                logger.info(
                    "S2 not usable for %s %s — S1/S3 still stored for this day",
                    file_name,
                    ad,
                )

            if self._write_s1_s3_for_date(
                ad,
                s1s3_by_date=s1s3_by_date,
                location_id=location_id,
                file_name=file_name,
                internal_id=iid,
                grower_name=grower_name,
                grower_id=grower_id,
                skip_existing=skip_existing,
                counts=counts,
                orbit_direction_s1=s1_orbit_by_date.get(ad),
                orbit_direction_s3=s3_orbit_by_date.get(ad),
                raw_id_s1=raw_id_s1,
                raw_id_s3=raw_id_s3,
            ):
                s1s3_written.add(ad)

            try:
                self.db.commit()
            except Exception as e:
                logger.exception("Commit failed %s %s: %s", file_name, ad, e)
                self.db.rollback()
                counts["errors"] += 1

        # S1/S3 for calendar days without an S2 observation (or S2-only skip path)
        if self.s1_s3_every_calendar_day:
            for ad in calendar_dates:
                if ad in s1s3_written:
                    continue
                if self._write_s1_s3_for_date(
                    ad,
                    s1s3_by_date=s1s3_by_date,
                    location_id=location_id,
                    file_name=file_name,
                    internal_id=iid,
                    grower_name=grower_name,
                    grower_id=grower_id,
                    skip_existing=skip_existing,
                    counts=counts,
                    orbit_direction_s1=s1_orbit_by_date.get(ad),
                    orbit_direction_s3=s3_orbit_by_date.get(ad),
                    raw_id_s1=raw_id_s1,
                    raw_id_s3=raw_id_s3,
                ):
                    s1s3_written.add(ad)
                try:
                    self.db.commit()
                except Exception as e:
                    logger.exception("Commit failed %s %s (S1/S3 only): %s", file_name, ad, e)
                    self.db.rollback()
                    counts["errors"] += 1

        counts["gaps"] = find_missing_dates(
            catalogue_dates=cat_dates,
            db_dates=db_dates,
            statistical_dates=stat_dates,
        )
        wrote_data = counts["s2"] + counts["s1"] + counts["s3"] > 0
        s2_already_complete = bool(s2_observation_dates) and all(
            sentinel2_row_exists(
                self.db,
                location_id,
                file_name,
                ad,
                self.season_id,
                internal_id=iid,
            )
            for ad in s2_observation_dates
        )
        if counts["errors"] == 0 and (wrote_data or s2_already_complete):
            upsert_checkpoint(
                self.db,
                run_id=self.run_id,
                file_name=file_name,
                stage=Stage.COMPLETE,
                status=CheckpointStatus.DONE,
                location_id=location_id,
            )
            if not wrote_data and s2_already_complete:
                logger.info(
                    "%s: all %d S2 days already in DB — marked COMPLETE (skipped=%d)",
                    file_name,
                    len(s2_observation_dates),
                    counts["skipped"],
                )
        elif counts["errors"] == 0 and not wrote_data:
            logger.warning(
                "No index rows written for %s — COMPLETE checkpoint not set (safe to re-run)",
                file_name,
            )
        return counts
