"""Band manifests per Copernicus mission — target completeness for crop monitoring."""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet

PIPELINE_VERSION = "v2"


@dataclass(frozen=True)
class BandManifest:
    satellite: str
    collection: str
    bands: tuple[str, ...]
    optional_bands: tuple[str, ...] = ()

    def all_expected(self) -> FrozenSet[str]:
        return frozenset(self.bands) | frozenset(self.optional_bands)


# Sentinel-2 L2A (10m/20m/60m) — B10 often omitted in L2A; B01 60m coastal aerosol
S2_L2A_BANDS = (
    "B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B11", "B12",
)
S2_L2A_OPTIONAL = ("B01", "B09", "B10")

# Legacy pipeline subset (still ingested for backward compatibility)
S2_LEGACY_STATISTICAL_BANDS = (
    "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12",
)

# Sentinel-1 GRD IW dual-pol
S1_GRD_BANDS = ("VV", "VH")

# Sentinel-3 SLSTR — thermal-focused; S7 added for extended LST recipes
S3_SLSTR_BANDS = ("S7", "S8", "S9")
S3_SLSTR_OPTIONAL = ("S1", "S2", "S3", "S4", "S5", "S6", "F1", "F2")

MANIFESTS = {
    "S2": BandManifest("S2", "sentinel-2-l2a", S2_L2A_BANDS, S2_L2A_OPTIONAL),
    "S1": BandManifest("S1", "sentinel-1-grd", S1_GRD_BANDS),
    "S3": BandManifest("S3", "sentinel-3-slstr", S3_SLSTR_BANDS, S3_SLSTR_OPTIONAL),
}


def band_column_name(band_id: str) -> str:
    """Map B02 → b02, B8A → b8a for DB columns."""
    return band_id.lower().replace("b8a", "b8a")
