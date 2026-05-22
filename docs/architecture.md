## Repository architecture (GitHub-safe)

This repository contains production FastAPI services and a number of operational scripts.
For GitHub readiness and safe collaboration, **new** work should prefer the isolated structure
under `src/copernicus/` for Copernicus/Sentinel integrations.

### Key principles

- **Additive refactor only**: do not break existing imports or production runtime.
- **Secrets never in source**: credentials must come from local `.env` or environment.
- **Safe testing**: `TEST_MODE=true` prevents DB writes in new adapters; tests never call external APIs.

