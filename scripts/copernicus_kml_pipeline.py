#!/usr/bin/env python3
"""
Legacy entry: forwards to the packaged CLI (``copernicus-pipeline``).

Prefer: ``pip install -e .`` then ``copernicus-pipeline run ...``,
or ``python -m copernicus_pipeline run ...`` from the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from copernicus_pipeline.cli import main

if __name__ == "__main__":
    # Backward compatible: allow ``python scripts/copernicus_kml_pipeline.py --kml-dir ...``
    # without the ``run`` subcommand.
    if len(sys.argv) > 1 and sys.argv[1] != "run":
        sys.argv.insert(1, "run")
    main()
