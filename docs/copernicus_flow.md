## Copernicus (Sentinel) flow — high level

This project uses Copernicus Data Space Ecosystem (CDSE) / Sentinel Hub APIs in two modes:

- **Process API**: returns raster arrays for an AOI and time interval (S2 optical, S1 SAR, S3 thermal).
- **Statistical API**: returns zonal statistics (e.g. daily polygon means) over an AOI and time interval.

### Where it lives today

- Production pipeline code: `crop_monitoring/`
- Service helpers (API-facing): `services/`
- FastAPI endpoints: `api/v1/endpoints/sentinel.py`

### Safe isolated layer (new)

`src/copernicus/` is the recommended place for new modules, tests, and refactors.
It is designed to be **additive** and to support **TEST_MODE** without DB writes.

