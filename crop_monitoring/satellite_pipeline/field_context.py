"""
Field identity for harvest / v2 ingestion — no Google geocoding required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FieldContext:
    internal_id: str
    location_id: str
    file_name: str
    season_id: str
    grower_name: Optional[str] = None
    grower_id: Optional[str] = None
    legacy_file_name: Optional[str] = None

    def as_metadata(self) -> dict[str, Optional[str]]:
        return {
            "internal_id": self.internal_id,
            "grower_name": self.grower_name,
            "grower_id": self.grower_id,
            "legacy_file_name": self.legacy_file_name,
        }
