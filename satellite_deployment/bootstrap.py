"""Deployment bootstrap: venv hint, deps, env validation, connectivity tests."""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ensure_dirs(paths) -> None:
    paths.ensure_runtime_dirs()


def install_requirements(root: Path, *, upgrade: bool = False) -> int:
    req = root / "requirements.txt"
    if not req.is_file():
        logger.error("requirements.txt not found at %s", req)
        return 1
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(req)]
    if upgrade:
        cmd.append("--upgrade")
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap satellite deployment on lab machine")
    parser.add_argument("--install-deps", action="store_true", help="pip install -r requirements.txt")
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--skip-db-test", action="store_true")
    parser.add_argument("--skip-copernicus-test", action="store_true")
    parser.add_argument("--expected-kml", type=int, default=253)
    args = parser.parse_args(argv)

    root = _repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from dotenv import load_dotenv

        load_dotenv(root / ".env")
    except ImportError:
        pass

    from config.satellite_paths import get_satellite_paths
    from satellite_deployment.validate import run_all_checks

    paths = get_satellite_paths()
    ensure_dirs(paths)

    if args.install_deps:
        rc = install_requirements(root, upgrade=args.upgrade)
        if rc != 0:
            return rc

    import os

    os.environ.setdefault("SATELLITE_PROJECT_ROOT", str(root))

    result = run_all_checks(
        paths,
        expected_kml=args.expected_kml,
        test_db=not args.skip_db_test,
        test_copernicus=not args.skip_copernicus_test,
    )

    for w in result.warnings:
        logger.warning(w)
    for e in result.errors:
        logger.error(e)

    if result.ok:
        logger.info("Bootstrap OK — paths: kml=%s mapping=%s", paths.kml_dir, paths.mapping_dir)
        return 0
    logger.error("Bootstrap failed with %d error(s)", len(result.errors))
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    raise SystemExit(main())
