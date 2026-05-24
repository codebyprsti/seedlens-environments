# Crop Monitoring — Development Roadmap

**Reference:** Indices_Extraction_Guide.html, Indices Extraction.docx  
**Status:** Extend current pipeline to a production-grade, time-series–capable system without rewriting working components.

---

## 1. Missing Components

Based on the specification documents and production needs:

| Component | Document / rationale | Current state |
|-----------|------------------------|---------------|
| **Cloud filtering policy** | Doc: "Set cloud cover filter to ≤ 20%" | ✅ Fixed: default 20%, retry 50→80 only when no data |
| **Data validation** | Doc: valid reflectance, band ranges; QC before storing | Not centralized: masking in pipeline only |
| **Metadata logging** | Doc: "Record Mean Value", "Export Data"; audit trail for runs | Logging ad-hoc; no structured run metadata store |
| **Quality control** | Scientific: index ranges (e.g. NDVI -1..1), outlier flags | Not implemented |
| **Output formats** | Doc: "Export or manually tabulate", "Spreadsheet" | DB only; no CSV/JSON/Excel export |
| **Time-series tracking** | Doc: "date/time window corresponding to crop stage (e.g. 30 DAS, 60 DAS)" | Single-date runs; no aggregation by location over time |
| **Error handling** | Robust batch: partial failure, retries, clear errors | Basic try/except; no retry policy or error taxonomy |
| **Scheduling** | Doc: periodic collection for crop stages | Manual/script only; no cron/scheduler |
| **API layer** | Dashboard / external consumers need REST | No HTTP API for crop_indices or time-series |
| **Reporting** | Doc: "Statistical Information", export to spreadsheet | No report generator (CSV/JSON/summary) |
| **Caching** | Scalability: avoid re-fetching same polygon/date | No cache for Sentinel responses |
| **Rate limiting** | CDSE/Sentinel Hub limits | No backoff or queue; batch runs sequentially |
| **Async / queue** | Large KML batches without blocking | Synchronous only |

---

## 2. Implementation Roadmap

### Phase 1 — Immediate fixes (done / small changes)

| Item | Status | Notes |
|------|--------|--------|
| Cloud filter ≤ 20% | ✅ Done | Default maxcc=20; retry 50 then 80 only when no valid optical data |
| Centralize cloud policy | Optional | `cloud_filter.py` constants for document compliance |

### Phase 2 — Feature completion

| Item | Purpose | Where |
|------|---------|--------|
| **Quality control** | Validate index ranges, flag suspicious values before DB | `quality_control.py`; call from `pipeline.py` before `insert_crop_indices` |
| **Metadata / run logging** | Audit: which KML, dates, cloud used, success/fail | `metadata_logger.py`; hook in pipeline and batch script |
| **Report generator** | Export indices to CSV/JSON (doc: "Export Data", spreadsheet) | `report_generator.py`; optional after pipeline or from DB query |
| **Time-series builder** | Query `operations.crop_indices` by location/date range; DAS-style windows | `time_series_builder.py` + `scripts/run_time_series_analysis.py` |
| **REST API** | Dashboard integration: list indices, time-series by location | New `api/v1/endpoints/crop_indices.py` (or extend existing) |

### Phase 3 — Production readiness

| Item | Purpose | Where |
|------|---------|--------|
| **Scheduling** | Periodic runs (e.g. weekly) for fixed KML set | Scheduler (cron, Celery, or internal) + batch script |
| **Caching** | Reduce API calls for same (geometry, date range) | Optional cache layer in `sentinel_client` or band_extractor (e.g. disk or Redis keyed by bbox+dates) |
| **Rate limiting / backoff** | Respect CDSE limits; retry with backoff on 429 | `sentinel_client.py`: retry decorator or wrapper |
| **Async / queue** | Large batches: queue KMLs, process with concurrency limit | Optional: Celery tasks or asyncio pool calling existing pipeline |
| **Schema improvements** | Support time-series: e.g. `acquisition_date`, `cloud_cover_used`, `run_id` | SQL migrations; extend `operations.crop_indices` if needed |
| **Monitoring** | Metrics: runs/day, failures, latency | Logging + optional Prometheus/health endpoint |

---

## 3. Suggested Folder Structure

```
crop_monitoring/
    kml_parser.py              # existing
    sentinel_client.py          # existing
    band_extractor.py           # existing
    index_calculator.py         # existing
    temperature_calculator.py   # existing
    pipeline.py                 # existing
    cloud_filter.py             # NEW: document-compliant maxcc constants
    metadata_logger.py          # NEW: structured run metadata
    quality_control.py           # NEW: validate indices, flag outliers
    time_series_builder.py      # NEW: query DB, build time series
    report_generator.py         # NEW: CSV/JSON export
    database/
        repository.py           # existing; add query helpers for time-series if needed
        ...
scripts/
    run_crop_analysis.py        # existing
    run_crop_analysis_batch.py  # existing
    run_time_series_analysis.py # NEW: time-series from DB
api/v1/endpoints/
    crop_indices.py             # NEW (or extend): GET indices, time-series by location
```

---

## 4. Code Modules to Add

| Module | Responsibility | Integrates with |
|--------|----------------|-----------------|
| `cloud_filter.py` | Constants: DOCUMENT_MAXCC=20, RETRY_MAXCC=[50, 80]. Single place for document compliance. | `pipeline.py`, `band_extractor.py` (optional refactor) |
| `metadata_logger.py` | Log run: kml_path, start/end date, maxcc_used, valid_pixels, indices_present, success, error. Optional: write to DB or file. | `pipeline.run_crop_analysis`, batch script |
| `quality_control.py` | `validate_indices(means)`: check NDVI/SAVI in [-1,1], LST in reasonable range; return warnings/flags. `flag_outliers(means)` optional. | `pipeline.py` before insert |
| `time_series_builder.py` | `get_time_series(session, location_id, date_start, date_end)` → list of {date, indices}. Optional: by grower_id, variety_id. | API, report generator, run_time_series_analysis |
| `report_generator.py` | `to_csv(results)` / `to_json(results)`; optional `summary_report(batch_results)`. Input: list of pipeline results or DB rows. | Batch script, API response |

---

## 5. Pipeline Improvements

- **Scalability**
  - Keep single-polygon pipeline as-is; batch script iterates. For scale: add queue (Celery) or asyncio worker pool that calls `run_crop_analysis` with a concurrency limit.
  - Cache: key = (bbox_hash, start_date, end_date, maxcc). Store Sentinel response (bands) on disk or Redis; TTL e.g. 7 days. In `band_extractor` or `sentinel_client`: check cache before request, write after.

- **Large KML batch runs**
  - Batch script: already continues on per-file failure and prints summary.
  - Add `--limit N` for testing.
  - Optional: persist queue (e.g. list of KML paths) and process in chunks with progress file.

- **API rate limits**
  - Sentinel Hub / CDSE: use exponential backoff on 429 or connection errors in `sentinel_client.fetch_s2_bands` / `fetch_s3_thermal` (e.g. retry 3 times with 2^attempt seconds delay).
  - Optional: global rate limiter (e.g. max N requests per minute) when using async.

- **Caching Sentinel responses**
  - In `band_extractor.fetch_band_data` or `sentinel_client`: before `SentinelHubRequest.get_data()`, compute cache key from (geometry bounds, time_interval, maxcc, resolution). If cache hit, load arrays and return; else request and store.

- **Async requests**
  - Optional: `async def fetch_s2_bands_async(...)` using `sentinelhub` async if available, or run sync `fetch_s2_bands` in thread pool. Batch script could use `asyncio.gather` with semaphore to cap concurrency.

- **Queue-based processing**
  - Celery (or RQ): task `run_crop_analysis.delay(kml_path, start_date, end_date)`. Worker runs existing pipeline. Enqueue all KMLs from a folder; monitor task results.

---

## 6. Example Code Skeletons

### 6.1 `crop_monitoring/cloud_filter.py`

```python
"""Document-compliant cloud cover policy: primary ≤20%, fallback 50 then 80."""

DOCUMENT_MAXCC = 20   # Indices Extraction Guide: "Set cloud cover filter to ≤ 20%"
RETRY_MAXCC = (50, 80)  # Fallback only when no valid optical data
```

### 6.2 `crop_monitoring/metadata_logger.py`

```python
"""Structured logging of pipeline run metadata for audit and debugging."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

def log_run(
    kml_path: str,
    start_date: str,
    end_date: str,
    maxcc_used: float,
    valid_pixels: Optional[int] = None,
    indices_computed: Optional[list[str]] = None,
    success: bool = True,
    error: Optional[str] = None,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    logger = logging.getLogger("crop_monitoring.run")
    msg = (
        f"run kml={kml_path} start={start_date} end={end_date} maxcc={maxcc_used} "
        f"valid_pixels={valid_pixels} indices={indices_computed} success={success}"
    )
    if error:
        msg += f" error={error}"
    logger.info(msg, extra=extra or {})
```

### 6.3 `crop_monitoring/quality_control.py`

```python
"""Validate index values and flag outliers before DB insert."""

from __future__ import annotations

from typing import Any

def validate_indices(means: dict[str, float]) -> tuple[bool, list[str]]:
    """Check index ranges (e.g. NDVI/SAVI in [-1,1]). Return (ok, list of warnings)."""
    warnings = []
    for key, low, high in [
        ("NDVI", -1.0, 1.0), ("SAVI", -1.0, 1.0), ("NDMI", -1.0, 1.0),
        ("MSAVI", -1.0, 1.0), ("NDRE", -1.0, 1.0), ("EVI", -1.0, 1.0),
    ]:
        v = means.get(key)
        if v is not None and isinstance(v, float) and (v < low or v > high):
            warnings.append(f"{key}={v} outside [{low},{high}]")
    lst = means.get("LST_C")
    if lst is not None and isinstance(lst, float) and (lst < -50 or lst > 60):
        warnings.append(f"LST_C={lst} outside typical range [-50,60]")
    return len(warnings) == 0, warnings
```

### 6.4 `crop_monitoring/time_series_builder.py`

```python
"""Build time series of crop indices from operations.crop_indices."""

from __future__ import annotations

from typing import Any, Optional

def get_time_series(
    session,
    location_id: Optional[str] = None,
    grower_id: Optional[str] = None,
    variety_id: Optional[str] = None,
    date_start: Optional[str] = None,
    date_end: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Query operations.crop_indices and return rows ordered by date (for charts)."""
    from sqlalchemy import text
    q = "SELECT date_start, date_end, ndvi, savi, ndmi, ndre, gci, psri, msavi, evi, lai, lst_celsius FROM operations.crop_indices WHERE 1=1"
    params = {}
    if location_id:
        q += " AND location_id = :location_id"
        params["location_id"] = location_id
    if grower_id:
        q += " AND grower_id = :grower_id"
        params["grower_id"] = grower_id
    if variety_id:
        q += " AND variety_id = :variety_id"
        params["variety_id"] = variety_id
    if date_start:
        q += " AND date_end >= :date_start"
        params["date_start"] = date_start
    if date_end:
        q += " AND date_start <= :date_end"
        params["date_end"] = date_end
    q += " ORDER BY date_start"
    rows = session.execute(text(q), params).fetchall()
    return [dict(r._mapping) for r in rows]
```

### 6.5 `crop_monitoring/report_generator.py`

```python
"""Export crop indices to CSV/JSON for reporting and dashboard."""

from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

def to_csv(rows: list[dict[str, Any]], fieldnames: Optional[list[str]] = None) -> str:
    if not rows:
        return ""
    fieldnames = fieldnames or list(rows[0].keys())
    buf = StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()

def to_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2)
```

### 6.6 `scripts/run_time_series_analysis.py`

```python
#!/usr/bin/env python3
"""Query time series from operations.crop_indices (e.g. by location_id or date range)."""

import sys
from pathlib import Path
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

def main():
    from core.db import SessionLocal
    from crop_monitoring.time_series_builder import get_time_series
    from crop_monitoring.report_generator import to_csv, to_json
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--location-id", type=str, help="Filter by location_id")
    p.add_argument("--date-start", type=str)
    p.add_argument("--date-end", type=str)
    p.add_argument("--format", choices=["csv", "json"], default="json")
    args = p.parse_args()
    session = SessionLocal()
    rows = get_time_series(
        session, location_id=args.location_id,
        date_start=args.date_start, date_end=args.date_end,
    )
    if args.format == "csv":
        print(to_csv(rows))
    else:
        print(to_json(rows))
    session.close()
```

---

## 7. Ensuring Final System Supports

| Requirement | How |
|-------------|-----|
| **Time-series crop monitoring** | `time_series_builder.get_time_series()` + API endpoint + optional `run_time_series_analysis.py` exporting CSV/JSON. |
| **Periodic data collection** | Cron (or scheduler) running `run_crop_analysis_batch.py --dir <kml_dir>` on a fixed schedule; optionally with `--start-date` / `--end-date` for last week. |
| **Dashboard integration** | REST API: e.g. `GET /api/v1/crop-indices?location_id=...&date_start=...&date_end=...` returning time-series JSON. |
| **Scalable processing** | Keep current pipeline; add caching + rate limiting in sentinel_client; optional queue (Celery) for large batches. |

---

## 8. Summary

- **Phase 1:** Cloud filter aligned with document (done).
- **Phase 2:** Add QC, metadata logging, report generator, time-series builder, and REST API for indices/time-series.
- **Phase 3:** Add scheduling, caching, rate limiting, optional async/queue, and monitoring.

New modules are designed to **wrap or hook into** the existing pipeline rather than replace it; all new code is additive.
