"""
Sentinel-2 cloud policy — configurable thresholds, pixel masking, scene fallback ladder.

Default scene search ceiling is 60% (monsoon/agricultural continuity).
Indices are computed on SCL/QA-masked clear pixels only, not by rejecting whole tiles.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence


def _env_float(key: str, default: float) -> float:
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    return (os.environ.get(key) or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class S2CloudSettings:
    """Production cloud handling for Sentinel-2."""

    # Scene catalogue / Hub pre-filter (upper bound — not pixel rejection)
    max_scene_cloud_pct: float = 60.0
    # Minimum clear-pixel fraction after SCL masking to mark usable_scene
    min_valid_pixel_pct: float = 30.0
    # Escalating scene cloud limits until masking yields enough clear pixels
    fallback_maxcc_ladder: tuple[float, ...] = (20.0, 40.0, 60.0, 80.0)
    # Optional last-resort scene inclusion
    allow_maxcc_80: bool = True
    # Use SCL + dataMask (QA60 when available on collection)
    use_scl_mask: bool = True
    use_qa60_mask: bool = True

    @classmethod
    def from_env(cls, overrides: dict | None = None) -> "S2CloudSettings":
        ladder_raw = (os.environ.get("S2_CLOUD_FALLBACK_LADDER") or "20,40,60,80").strip()
        ladder = tuple(float(x.strip()) for x in ladder_raw.split(",") if x.strip())
        if not ladder:
            ladder = (20.0, 40.0, 60.0, 80.0)
        if not _env_bool("S2_ALLOW_MAXCC_80", True):
            ladder = tuple(x for x in ladder if x <= 60.0)

        base = cls(
            max_scene_cloud_pct=_env_float("S2_MAX_CLOUD_COVER", 60.0),
            min_valid_pixel_pct=_env_float("S2_MIN_VALID_PIXEL_PCT", 30.0),
            fallback_maxcc_ladder=ladder,
            allow_maxcc_80=_env_bool("S2_ALLOW_MAXCC_80", True),
            use_scl_mask=_env_bool("S2_USE_SCL_MASK", True),
            use_qa60_mask=_env_bool("S2_USE_QA60_MASK", True),
        )
        if overrides is None:
            return base
        d = {**base.__dict__, **overrides}
        return cls(**d)

    def effective_ladder(self) -> Sequence[float]:
        """Unique ascending ladder capped by max_scene_cloud_pct."""
        seen: set[float] = set()
        out: list[float] = []
        for v in self.fallback_maxcc_ladder:
            if v > self.max_scene_cloud_pct and v != self.max_scene_cloud_pct:
                continue
            if not self.allow_maxcc_80 and v > 60:
                continue
            if v not in seen:
                seen.add(v)
                out.append(v)
        if self.max_scene_cloud_pct not in seen:
            out.append(self.max_scene_cloud_pct)
        return sorted(out)


# Module-level default (imported across pipeline)
DEFAULT_S2_CLOUD = S2CloudSettings.from_env()
