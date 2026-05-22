from __future__ import annotations


def test_statistical_indices_band_order_is_stable():
    # Pure import test: no network, no DB.
    from crop_monitoring.statistical_client import STAT_INDEX_NAMES

    assert STAT_INDEX_NAMES == [
        "NDVI",
        "SAVI",
        "NDMI",
        "NDRE",
        "GCI",
        "PSRI",
        "MSAVI",
        "EVI",
        "NDWI",
    ]


def test_s2_band_means_order_is_stable():
    # Validate the mapping used by the batch script.
    from scripts.run_crop_analysis_s3_batch import S2_BAND_IDS

    assert S2_BAND_IDS == ["B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B11", "B12"]

