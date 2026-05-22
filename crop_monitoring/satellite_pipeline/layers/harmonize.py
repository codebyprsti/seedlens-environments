"""
Layer 2: Harmonization — normalize Statistical/Process API responses to canonical band/index dicts.
"""

from __future__ import annotations

from typing import Any, Optional

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
        col = bid.lower().replace("b8a", "b8a")
        row[col] = _mean_from_band_stats(band_stats)

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
