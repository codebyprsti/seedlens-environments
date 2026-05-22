import pytest

from copernicus_pipeline.errors import ValidationError
from copernicus_pipeline.runner import map_mode, normalize_basename, validate_iso_date


def test_validate_iso_date_ok() -> None:
    assert validate_iso_date("s", "2025-12-01") == "2025-12-01"


def test_validate_iso_date_bad() -> None:
    with pytest.raises(ValidationError):
        validate_iso_date("s", "2025-13-01")


def test_map_mode() -> None:
    assert map_mode("default") == "crop_indices"
    assert map_mode("crop_indices") == "crop_indices"
    assert map_mode("locations_only") == "locations_only"


def test_normalize_basename() -> None:
    assert normalize_basename("  A  B.kml  ") == "a b.kml"
