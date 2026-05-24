#!/usr/bin/env python3
"""
PART 5 — Scalar verification: NDVI = (NIR - Red) / (NIR + Red).

Pass band means (same units as your GeoTIFF zonal means). No API calls.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

from field_validation.planet_indices import scalar_ndvi  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Verify NDVI from NIR and Red means")
    p.add_argument("--nir", type=float, required=True)
    p.add_argument("--red", type=float, required=True)
    p.add_argument("--stored-ndvi", type=float, default=None, help="Value from Excel/pipeline to compare")
    args = p.parse_args()
    v = scalar_ndvi(args.nir, args.red)
    den = args.nir + args.red
    print(f"inputs  NIR={args.nir}  Red={args.red}  (NIR+Red)={den}")
    print(f"NDVI    (NIR-Red)/(NIR+Red) = {v}")
    if args.stored_ndvi is not None and v is not None:
        diff = abs(v - args.stored_ndvi)
        print(f"stored  {args.stored_ndvi}")
        print(f"abs_diff {diff}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
