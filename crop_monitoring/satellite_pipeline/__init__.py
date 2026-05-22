"""
Multi-satellite ingestion pipeline (raw → harmonize → indices → analytics).

Writes to operations.sentinel{1,2,3}_indices without modifying crop_indices.
"""

__all__ = ["SatelliteIngestionOrchestrator"]


def __getattr__(name: str):
    if name == "SatelliteIngestionOrchestrator":
        from crop_monitoring.satellite_pipeline.orchestrator import SatelliteIngestionOrchestrator

        return SatelliteIngestionOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
