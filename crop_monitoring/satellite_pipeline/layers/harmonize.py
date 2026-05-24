"""
Layer 2: Harmonization — normalize Statistical/Process API responses to canonical band/index dicts.
"""

from __future__ import annotations

from typing import Any, Optional

from crop_monitoring.satellite_pipeline.bands import band_column_name
from crop_monitoring.satellite_pipeline.evalscripts import (
    QUALITY_BAND_NAMES,
    S2_BAND_IDS_V2,
    S2_BAND_IDS_V3,
    S2_INDEX_NAMES_V2,
    S2_INDEX_NAMES_V3,
)


def iter_statistical_daily_items(stats_list: Any) -> list[dict]:
    """
    Normalize Sentinel Hub Statistical ``get_data()`` payload to per-day interval dicts.
    """
    if not stats_list:
        return []
    if isinstance(stats_list, dict):
        stats_list = [stats_list]
    first = stats_list[0]
    if isinstance(first, dict) and isinstance(first.get("data"), list):
        return [x for x in first["data"] if isinstance(x, dict)]
    return [x for x in stats_list if isinstance(x, dict) and ("interval" in x or "outputs" in x)]


def daily_items_from_stored_raw(raw_response: Any) -> list[dict]:
    """Extract daily Statistical intervals from a stored raw_response blob."""
    if not raw_response:
        return []
    if isinstance(raw_response, list):
        return iter_statistical_daily_items(raw_response)
    if not isinstance(raw_response, dict):
        return []
    if isinstance(raw_response.get("data"), list):
        return [x for x in raw_response["data"] if isinstance(x, dict)]
    resp = raw_response.get("response")
    if isinstance(resp, dict):
        if isinstance(resp.get("data"), list):
            return [x for x in resp["data"] if isinstance(x, dict)]
        inner = resp.get("response")
        if inner is not None:
            return iter_statistical_daily_items(inner)
    return iter_statistical_daily_items(resp) if resp else []


def _acquisition_date_from_interval(stats_item: dict) -> Optional[str]:
    interval = stats_item.get("interval") or {}
    from_ts = interval.get("from", "")
    to_ts = interval.get("to", "")
    if len(to_ts) >= 10:
        return to_ts[:10]
    if len(from_ts) >= 10:
        return from_ts[:10]
    return None


def _mean_from_band_stats(band_stats: Any) -> Optional[float]:
    if band_stats and isinstance(band_stats.get("stats"), dict):
        mean_v = band_stats["stats"].get("mean")
        if mean_v is not None:
            return float(mean_v)
    return None


def _attach_quality_pcts(row: dict[str, Any], outputs: dict) -> None:
    """Map aggregated quality band means to percentage columns."""
    qo = outputs.get("quality") or {}
    qb = qo.get("bands") if isinstance(qo.get("bands"), dict) else qo
    if not isinstance(qb, dict):
        return
    for i, qname in enumerate(QUALITY_BAND_NAMES):
        stats = qb.get(f"B{i}") or qb.get(str(i))
        pct = _mean_from_band_stats(stats)
        if pct is not None:
            # Aggregated mean of 0/1 flags → fraction; scale to %
            val = max(0.0, min(100.0, float(pct) * 100.0))
            if qname == "clear":
                row["valid_pixel_percentage"] = val
            elif qname == "cloud":
                row["cloud_pixel_percentage"] = val
            elif qname == "shadow":
                row["shadow_pixel_percentage"] = val
            elif qname == "rejected":
                row["masked_pixel_percentage"] = val
    # masked = non-clear (cloud + shadow + rejected) approximate
    if "masked_pixel_percentage" not in row:
        c = row.get("cloud_pixel_percentage") or 0
        s = row.get("shadow_pixel_percentage") or 0
        row["masked_pixel_percentage"] = min(100.0, c + s)


def parse_s2_band_stats_response(stats_item: dict, *, version: int = 3) -> dict[str, Any]:
    """One daily interval from Statistical API bands evalscript → flat means."""
    band_ids = S2_BAND_IDS_V3 if version >= 3 else S2_BAND_IDS_V2
    outputs = stats_item.get("outputs") or {}
    ob = outputs.get("bands") or {}
    bands_out = ob.get("bands") if isinstance(ob.get("bands"), dict) else ob

    row: dict[str, Any] = {"acquisition_date": _acquisition_date_from_interval(stats_item)}
    for i, bid in enumerate(band_ids):
        band_stats = bands_out.get(f"B{i}") or bands_out.get(str(i))
        row[band_column_name(bid)] = _mean_from_band_stats(band_stats)

    if version >= 3:
        _attach_quality_pcts(row, outputs)
    else:
        mask = outputs.get("dataMask") or {}
        mask_bands = mask.get("bands") if isinstance(mask.get("bands"), dict) else {}
        b0 = mask_bands.get("B0") or mask_bands.get("0")
        if b0 and isinstance(b0.get("stats"), dict):
            sc = b0["stats"].get("sampleCount")
            if sc:
                row["valid_pixel_fraction"] = sc
    return row


def parse_s2_index_stats_response(stats_item: dict, *, version: int = 3) -> dict[str, Any]:
    """One daily interval from Statistical indices evalscript."""
    index_names = S2_INDEX_NAMES_V3 if version >= 3 else S2_INDEX_NAMES_V2
    outputs = stats_item.get("outputs") or {}
    ob = outputs.get("indices") or {}
    bands_out = (ob.get("bands") if isinstance(ob.get("bands"), dict) else ob) or {}

    row: dict[str, Any] = {"acquisition_date": _acquisition_date_from_interval(stats_item)}
    for i, name in enumerate(index_names):
        band_stats = bands_out.get(f"B{i}") or bands_out.get(str(i))
        key = name.lower()
        row[key] = _mean_from_band_stats(band_stats)
        if row[key] is not None:
            row[name] = row[key]
        else:
            row[name] = None

    if row.get("evi") is not None:
        row["lai"] = 3.618 * row["evi"] - 0.118

    if version >= 3:
        _attach_quality_pcts(row, outputs)

    return row


def merge_s2_daily_rows(bands_row: dict, indices_row: dict) -> dict[str, Any]:
    """Merge band + index rows for same acquisition_date."""
    out = dict(bands_row)
    for k, v in indices_row.items():
        if k == "acquisition_date":
            continue
        if out.get(k) is None and v is not None:
            out[k] = v
        elif k.upper() in indices_row and out.get(k.lower()) is None:
            out[k.lower()] = v
    for qk in (
        "valid_pixel_percentage",
        "cloud_pixel_percentage",
        "shadow_pixel_percentage",
        "masked_pixel_percentage",
    ):
        if out.get(qk) is None and indices_row.get(qk) is not None:
            out[qk] = indices_row[qk]
    ad = bands_row.get("acquisition_date") or indices_row.get("acquisition_date")
    out["acquisition_date"] = ad
    return out


def _stats_sample_count(band_stats: Any) -> int:
    if band_stats and isinstance(band_stats.get("stats"), dict):
        sc = band_stats["stats"].get("sampleCount")
        if sc is not None:
            return int(sc)
    return 0


def _mean_if_valid(band_stats: Any) -> Optional[float]:
    if _stats_sample_count(band_stats) <= 0:
        return None
    return _mean_from_band_stats(band_stats)


def _orbit_from_item(stats_item: dict) -> Optional[str]:
    props = stats_item.get("properties") or {}
    for key in ("orbitDirection", "sat:orbit_state", "orbit_direction"):
        val = props.get(key)
        if val:
            return str(val)
    return None


def parse_s1_stats_response(stats_item: dict) -> dict[str, Any]:
    """Daily S1 GRD polygon means from Statistical API (VV/VH linear power)."""
    outputs = stats_item.get("outputs") or {}
    sar_out = outputs.get("sar") or {}
    bands_out = sar_out.get("bands") if isinstance(sar_out.get("bands"), dict) else sar_out
    vv_stats = bands_out.get("B0") or bands_out.get("0")
    vh_stats = bands_out.get("B1") or bands_out.get("1")
    vv = _mean_if_valid(vv_stats)
    vh = _mean_if_valid(vh_stats)
    if vv is not None and vv <= 0:
        vv = None
    if vh is not None and vh <= 0:
        vh = None
    return {
        "acquisition_date": _acquisition_date_from_interval(stats_item),
        "vv": vv,
        "vh": vh,
        "sample_count": max(_stats_sample_count(vv_stats), _stats_sample_count(vh_stats)),
        "orbit_direction": _orbit_from_item(stats_item),
    }


def parse_s3_stats_response(stats_item: dict) -> dict[str, Any]:
    """Daily S3 SLSTR polygon means (S7/S8/S9 brightness temperature Kelvin)."""
    outputs = stats_item.get("outputs") or {}
    th_out = outputs.get("thermal") or {}
    bands_out = th_out.get("bands") if isinstance(th_out.get("bands"), dict) else th_out
    s7_stats = bands_out.get("B0") or bands_out.get("0")
    s8_stats = bands_out.get("B1") or bands_out.get("1")
    s9_stats = bands_out.get("B2") or bands_out.get("2")
    return {
        "acquisition_date": _acquisition_date_from_interval(stats_item),
        "s7": _mean_if_valid(s7_stats),
        "s8": _mean_if_valid(s8_stats),
        "s9": _mean_if_valid(s9_stats),
        "sample_count": max(
            _stats_sample_count(s7_stats),
            _stats_sample_count(s8_stats),
            _stats_sample_count(s9_stats),
        ),
        "orbit_direction": _orbit_from_item(stats_item),
    }


def rows_by_acquisition_date(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        ad = row.get("acquisition_date")
        if ad:
            out[str(ad)[:10]] = row
    return out


def expand_s1s3_to_calendar(
    calendar_dates: list[str],
    s1_by_date: dict[str, dict[str, Any]],
    s3_by_date: dict[str, dict[str, Any]],
) -> dict[str, tuple[Any, ...]]:
    """
    Map bulk Statistical daily rows to calendar slots.
    Missing dates → explicit (None,) * 6 tuple (same as Process API no-pass).
    Tuple: (s7, s8, s9, lst_celsius, vv_lin, vh_lin).
    """
    import numpy as np

    from crop_monitoring.temperature_calculator import lst_celsius as lst_celsius_array

    out: dict[str, tuple[Any, ...]] = {}
    for ad in calendar_dates:
        s1 = s1_by_date.get(ad) or {}
        s3 = s3_by_date.get(ad) or {}
        vv = s1.get("vv")
        vh = s1.get("vh")
        s7 = s3.get("s7")
        s8 = s3.get("s8")
        s9 = s3.get("s9")
        lst_c = None
        if s8 is not None and s9 is not None and np.isfinite(s8) and np.isfinite(s9):
            lst_c = float(lst_celsius_array(np.array([s8]), np.array([s9]))[0])
        out[ad] = (s7, s8, s9, lst_c, vv, vh)
    return out
