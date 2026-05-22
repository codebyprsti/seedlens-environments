"""
Build harvest KML → grower/location mappings from manifest + Excel/CSV (no Google API).
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

INTERNAL_ID_RE = re.compile(r"^(IND-[A-Z]{2}-\d{6})\.kml$", re.I)
DEFAULT_SEASON_ID = "RABI_25_26"

def _default_paths():
    try:
        from config.satellite_paths import get_satellite_paths

        p = get_satellite_paths()
        return p.mapping_dir, p.kml_dir
    except ImportError:
        root = Path(__file__).resolve().parents[2]
        return root / "data" / "mapping", root / "data" / "kml" / "harvest_all_fields"


DEFAULT_DATA_ROOT, DEFAULT_KML_DIR = _default_paths()


def normalize_kml_key(name: str) -> str:
    s = (name or "").replace("+", " ").strip().upper()
    return re.sub(r"\s+", " ", s)


def normalize_kml_filename(name: Optional[str]) -> Optional[str]:
    """Ensure .kml suffix; strip whitespace."""
    if not name or not str(name).strip():
        return None
    s = str(name).strip()
    if s.lower() == "nan":
        return None
    if not s.lower().endswith(".kml"):
        s = f"{s}.kml"
    return s


def resolve_canonical_file_name(
    *,
    location_id: str,
    manifest_row: Optional[dict[str, str]] = None,
    stringbio: Optional[dict[str, Any]] = None,
    yields_row: Optional[dict[str, Any]] = None,
    crop_indices_name: Optional[str] = None,
) -> Optional[str]:
    """
    DB-facing file_name (matches crop_indices / manifest), not renamed on-disk KML.
    Priority: manifest → STRINGbio crop_indices_file_name → KML File Name → yields KML Name → crop_indices.
    """
    candidates: list[Optional[str]] = []
    if manifest_row:
        candidates.append(manifest_row.get("file_name"))
    if stringbio:
        candidates.append(stringbio.get("crop_indices_file_name"))
        candidates.append(stringbio.get("kml_file_name"))
    if yields_row:
        candidates.append(yields_row.get("kml_name"))
    candidates.append(crop_indices_name)
    for raw in candidates:
        fn = normalize_kml_filename(raw)
        if fn:
            return fn
    return None


def load_crop_indices_file_names(db) -> dict[str, str]:
    """location_id → latest crop_indices.file_name."""
    from sqlalchemy import text

    try:
        rows = db.execute(
            text("""
                SELECT location_id, file_name FROM (
                    SELECT location_id, file_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY location_id ORDER BY id DESC
                           ) AS rn
                    FROM operations.crop_indices
                    WHERE location_id IS NOT NULL AND file_name IS NOT NULL
                      AND TRIM(file_name) <> ''
                ) t
                WHERE rn = 1
            """)
        ).fetchall()
        return {str(r[0]).strip().upper(): str(r[1]).strip() for r in rows}
    except Exception as e:
        logger.warning("Could not load crop_indices file names: %s", e)
        return {}


def internal_id_from_kml_path(path: Path) -> Optional[str]:
    m = INTERNAL_ID_RE.match(path.name)
    return m.group(1).upper() if m else None


@dataclass
class FieldMapping:
    internal_id: str
    location_id: str
    file_name: str
    season_id: str = DEFAULT_SEASON_ID
    legacy_file_name: Optional[str] = None
    grower_name: Optional[str] = None
    grower_id: Optional[str] = None
    source_grower_code: Optional[str] = None  # Excel numeric code (audit only)
    kml_path: Optional[str] = None
    state_code: Optional[str] = None
    validation_status: str = "ok"
    validation_notes: Optional[str] = None


@dataclass
class MappingReport:
    mappings: dict[str, FieldMapping] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unmatched_kml: list[str] = field(default_factory=list)
    duplicate_internal_ids: list[str] = field(default_factory=list)
    created_grower_ids: list[str] = field(default_factory=list)


def _load_manifest(manifest_path: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    if not manifest_path.is_file():
        return out
    with manifest_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lid = (row.get("location_id") or "").strip().upper()
            if lid:
                out[lid] = {
                    "file_name": (row.get("file_name") or "").strip(),
                    "status": (row.get("status") or "").strip(),
                }
    return out


def _load_yields_csv(path: Path) -> dict[str, dict[str, Any]]:
    """location_id -> grower + kml name."""
    out: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return out
    try:
        import pandas as pd

        df = pd.read_csv(path, dtype=str, low_memory=False)
    except Exception as e:
        logger.warning("Could not read yields CSV %s: %s", path, e)
        return out
    for _, row in df.iterrows():
        lid = str(row.get("location_id") or "").strip().upper()
        if not lid or lid == "NAN":
            continue
        gn = row.get("Grower Name as per Account") or row.get("grower_name")
        kmln = row.get("KML Name") or row.get("kml_name")
        iid_raw = row.get("internal_id") or row.get("Internal ID")
        out[lid] = {
            "grower_name": str(gn).strip() if gn and str(gn) != "nan" else None,
            "kml_name": str(kmln).strip() if kmln and str(kmln) != "nan" else None,
            "internal_id": str(iid_raw).strip().upper() if iid_raw and str(iid_raw) != "nan" else None,
        }
    return out


def _load_stringbio_excel(path: Path) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    """
    Returns (by_location_id, by_kml_key, by_internal_id).
    Primary source: location_id + Grower Name from STRINGbio workbooks.
    """
    by_loc: dict[str, dict[str, Any]] = {}
    by_kml: dict[str, dict[str, Any]] = {}
    by_iid: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return by_loc, by_kml, by_iid
    try:
        import pandas as pd

        df = pd.read_excel(path, dtype=str)
    except Exception as e:
        logger.warning("Could not read Excel %s: %s", path, e)
        return by_loc, by_kml, by_iid

    kml_col = None
    for c in df.columns:
        cl = str(c).lower()
        if "kml" in cl and "file" in cl:
            kml_col = c
            break
    loc_col = "location_id" if "location_id" in df.columns else None
    iid_col = None
    for c in df.columns:
        cl = str(c).lower().replace(" ", "_")
        if cl in ("internal_id", "internalid", "field_id"):
            iid_col = c
            break
    grower_col = "Grower Name" if "Grower Name" in df.columns else None
    gid_col = "Grower ID" if "Grower ID" in df.columns else None
    season_col = "Season " if "Season " in df.columns else None
    ci_fn_col = "crop_indices_file_name" if "crop_indices_file_name" in df.columns else None
    kml_fn_col = None
    if kml_col:
        kml_fn_col = kml_col
    elif "KML File Name" in df.columns:
        kml_fn_col = "KML File Name"

    for _, row in df.iterrows():
        grower_name = None
        if grower_col and str(row.get(grower_col)) != "nan":
            grower_name = str(row[grower_col]).strip()
        source_code = None
        if gid_col and str(row.get(gid_col)) != "nan":
            source_code = str(row[gid_col]).strip()
        season_raw = None
        if season_col and str(row.get(season_col)) != "nan":
            season_raw = str(row[season_col]).strip()

        kml_file_name = None
        crop_indices_file_name = None
        if kml_fn_col and str(row.get(kml_fn_col)) != "nan":
            kml_file_name = str(row[kml_fn_col]).strip()
        if ci_fn_col and str(row.get(ci_fn_col)) != "nan":
            crop_indices_file_name = str(row[ci_fn_col]).strip()

        payload = {
            "grower_name": grower_name,
            "source_grower_code": source_code,
            "season_id": season_raw,
            "source_file": path.name,
            "kml_file_name": kml_file_name,
            "crop_indices_file_name": crop_indices_file_name,
        }

        if loc_col:
            lid = str(row.get(loc_col) or "").strip().upper()
            if lid and lid != "NAN" and lid.startswith("IND-"):
                by_loc[lid] = payload
                by_iid.setdefault(lid, payload)

        if iid_col:
            iid = str(row.get(iid_col) or "").strip().upper()
            if iid and iid != "NAN" and iid.startswith("IND-"):
                by_iid[iid] = payload

        if kml_col:
            kmln = row.get(kml_col)
            if kmln and str(kmln) != "nan":
                by_kml[normalize_kml_key(str(kmln))] = payload

    return by_loc, by_kml, by_iid


def _discover_mapping_files(data_root: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    names = (
        "Harvested Field Yields_US24 (1)(Data) (1).csv",
        "Harvested_Field_Yields_US24_with_PRSTI.csv",
        "KA STRINGbio sampled GROWERS for Single drainage.xlsx",
        "Odisha STRING BIO sample growers for Single Drainage.xlsx",
        "_harvest_all_manifest.csv",
    )
    for n in names:
        p = data_root / n
        if p.is_file():
            found.append(p)
    for pattern in ("*STRING*bio*.xlsx", "*STRING*BIO*.xlsx", "*Harvested*Yields*.csv"):
        found.extend(data_root.glob(pattern))
    return tuple(dict.fromkeys(found))


def build_harvest_mappings(
    kml_dir: Path,
    *,
    data_root: Optional[Path] = None,
    excel_paths: Optional[tuple[Path, ...]] = None,
    season_id: str = DEFAULT_SEASON_ID,
    db=None,
) -> MappingReport:
    kml_dir = Path(kml_dir).resolve()
    data_root = Path(data_root or DEFAULT_DATA_ROOT)
    report = MappingReport()

    manifest = _load_manifest(kml_dir / "_harvest_all_manifest.csv")
    if not manifest:
        manifest = _load_manifest(data_root / "_harvest_all_manifest.csv")
    yields: dict[str, dict] = {}
    stringbio_by_loc: dict[str, dict] = {}
    stringbio_by_kml: dict[str, dict] = {}
    stringbio_by_iid: dict[str, dict] = {}

    crop_fn_by_loc = load_crop_indices_file_names(db) if db is not None else {}

    paths = excel_paths or _discover_mapping_files(data_root)
    for p in paths:
        p = Path(p)
        if not p.is_file():
            report.warnings.append(f"Missing data file: {p}")
            continue
        if p.suffix.lower() == ".csv":
            yields.update(_load_yields_csv(p))
        elif p.suffix.lower() in (".xlsx", ".xls"):
            loc_map, kml_map, iid_map = _load_stringbio_excel(p)
            stringbio_by_loc.update(loc_map)
            stringbio_by_kml.update(kml_map)
            stringbio_by_iid.update(iid_map)

    kml_files = sorted(kml_dir.glob("*.kml"))
    seen_internal: dict[str, str] = {}

    for kml_path in kml_files:
        iid = internal_id_from_kml_path(kml_path)
        if not iid:
            report.errors.append(f"Invalid KML filename (no internal_id): {kml_path.name}")
            continue
        if iid in seen_internal:
            report.duplicate_internal_ids.append(iid)
            report.errors.append(f"Duplicate internal_id {iid}: {kml_path.name} and {seen_internal[iid]}")
            continue
        seen_internal[iid] = kml_path.name

        location_id = iid
        man = manifest.get(location_id, {})
        disk_kml_name = kml_path.name
        field_season = season_id

        grower_name: Optional[str] = None
        source_code: Optional[str] = None

        # Priority 1: STRINGbio by internal_id / location_id (authoritative)
        sb_iid = stringbio_by_iid.get(iid) or stringbio_by_loc.get(location_id)
        if sb_iid:
            grower_name = sb_iid.get("grower_name")
            source_code = sb_iid.get("source_grower_code")
            if sb_iid.get("season_id"):
                field_season = _normalize_season(sb_iid["season_id"]) or field_season

        # Priority 2: yields CSV by location_id / internal_id
        y = yields.get(location_id, {}) or yields.get(iid, {})
        if y.get("grower_name"):
            grower_name = grower_name or y["grower_name"]

        canonical_fn = resolve_canonical_file_name(
            location_id=location_id,
            manifest_row=man or None,
            stringbio=sb_iid,
            yields_row=y if y else None,
            crop_indices_name=crop_fn_by_loc.get(location_id),
        )

        # STRINGbio keyed by original KML name (when manifest/crop_indices missing)
        if not canonical_fn and man.get("file_name"):
            sb_kml = stringbio_by_kml.get(normalize_kml_key(man["file_name"]))
            if sb_kml:
                canonical_fn = resolve_canonical_file_name(
                    location_id=location_id, stringbio=sb_kml
                )
                grower_name = grower_name or sb_kml.get("grower_name")
                source_code = source_code or sb_kml.get("source_grower_code")

        notes = []
        if not man:
            notes.append("not_in_manifest")
        if not grower_name:
            notes.append("missing_grower_name")
        if not canonical_fn:
            notes.append("missing_canonical_file_name")
            canonical_fn = disk_kml_name

        report.mappings[iid] = FieldMapping(
            internal_id=iid,
            location_id=location_id,
            file_name=canonical_fn,
            season_id=field_season,
            legacy_file_name=disk_kml_name,
            grower_name=grower_name,
            grower_id=None,
            source_grower_code=source_code,
            kml_path=str(kml_path),
            state_code=location_id.split("-")[1] if location_id.startswith("IND-") else None,
            validation_status="ok" if not notes else "warning",
            validation_notes="; ".join(notes) if notes else None,
        )

    for lid in manifest:
        expected = f"{lid}.kml"
        if expected not in {p.name for p in kml_files}:
            report.warnings.append(f"Manifest entry missing KML file: {expected}")

    logger.info(
        "Harvest mapping: %d KMLs, %d STRINGbio loc rows, %d errors, %d warnings",
        len(report.mappings),
        len(stringbio_by_loc),
        len(report.errors),
        len(report.warnings),
    )
    return report


def resolve_grower_ids_for_mappings(db, report: MappingReport) -> MappingReport:
    """Populate grower_id on each FieldMapping via operations.growers (create if missing)."""
    from crop_monitoring.satellite_pipeline.grower_resolver import GrowerResolver

    resolver = GrowerResolver(db)
    for iid, m in report.mappings.items():
        if not m.grower_name:
            continue
        gid, canonical = resolver.resolve(
            m.grower_name,
            location_id=m.location_id,
            excel_grower_id_hint=m.source_grower_code,
        )
        if gid:
            m.grower_name = canonical
            m.grower_id = gid
        else:
            report.warnings.append(f"unresolved_grower_id: {iid} ({m.grower_name})")

    report.created_grower_ids = list(resolver.created_ids)
    if resolver.created_ids:
        logger.info("Created %d new growers (G_* ids)", len(resolver.created_ids))
    return report


def _normalize_season(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).upper().replace(" ", "_").replace("-", "_")
    if "RABI" in s and ("25" in s or "26" in s):
        return "RABI_25_26"
    return None


def validate_kml_geometry(kml_path: Path) -> tuple[bool, Optional[str]]:
    try:
        from crop_monitoring.kml_parser import parse_kml

        geojson, _ = parse_kml(kml_path)
        if not geojson or not geojson.get("coordinates"):
            return False, "empty_geometry"
        from shapely.geometry import shape

        geom = shape(geojson)
        if geom.is_empty or geom.area <= 0:
            return False, "invalid_polygon_area"
        return True, None
    except Exception as e:
        return False, str(e)
