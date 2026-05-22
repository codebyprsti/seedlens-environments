"""
Sentinel-2 cloud policy — scene search ceiling + pixel-level SCL masking.

Tile-level rejection at 20% caused monsoon gaps in India. Production default is 60% scene
cloud with SCL masking; indices use clear pixels only. See satellite_pipeline/cloud_config.py.
"""

from crop_monitoring.satellite_pipeline.cloud_config import DEFAULT_S2_CLOUD

# Legacy names (backward compatible)
DOCUMENT_MAXCC = int(DEFAULT_S2_CLOUD.max_scene_cloud_pct)
RETRY_MAXCC = tuple(
    int(x) for x in DEFAULT_S2_CLOUD.fallback_maxcc_ladder if x != DEFAULT_S2_CLOUD.max_scene_cloud_pct
)


def get_primary_maxcc() -> float:
    """Scene catalogue / Hub search ceiling (%)."""
    return DEFAULT_S2_CLOUD.max_scene_cloud_pct


def get_retry_maxcc_sequence() -> tuple[float, ...]:
    """Escalating scene cloud limits until enough SCL-valid pixels."""
    return DEFAULT_S2_CLOUD.fallback_maxcc_ladder
