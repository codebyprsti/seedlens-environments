#!/usr/bin/env python3
"""
Lab/server entry point for full harvest satellite v2 deployment (253 KMLs).

  python scripts/run_satellite_lab.py bootstrap --install-deps
  python scripts/run_satellite_lab.py validate
  python scripts/run_satellite_lab.py run --start 2025-12-01 --end 2026-03-18
  python scripts/run_satellite_lab.py run --resume-failed --start ... --end ...
  python scripts/run_satellite_lab.py reprocess --start 2025-12-01 --end 2026-03-18
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

try:
    from dotenv import load_dotenv

    load_dotenv(_root / ".env")
except ImportError:
    pass

try:
    from crop_monitoring.sh_http_setup import configure_sh_http

    configure_sh_http()
except Exception:
    pass


def _ensure_lab_layout() -> Path:
    """Create logs/satellite, checkpoints, tmp before any command."""
    root = Path(__file__).resolve().parent.parent
    for rel in ("logs", "logs/satellite", "checkpoints", "checkpoints/satellite", "tmp"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def _paths():
    from config.satellite_paths import get_satellite_paths

    paths = get_satellite_paths()
    paths.ensure_runtime_dirs()
    return paths


def _write_validation_log(paths, lines: list[str]) -> Path:
    log_path = paths.log_dir / "validation.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


def cmd_bootstrap(args: argparse.Namespace) -> int:
    from satellite_deployment.bootstrap import main as bootstrap_main

    argv = []
    if args.install_deps:
        argv.append("--install-deps")
    if args.upgrade:
        argv.append("--upgrade")
    if args.skip_db_test:
        argv.append("--skip-db-test")
    if args.skip_copernicus_test:
        argv.append("--skip-copernicus-test")
    argv.extend(["--expected-kml", str(args.expected_kml)])
    return bootstrap_main(argv)


def cmd_validate(args: argparse.Namespace) -> int:
    from satellite_deployment.validate import run_all_checks

    paths = _paths()
    r = run_all_checks(
        paths,
        expected_kml=args.expected_kml,
        test_db=not args.skip_db_test,
        test_copernicus=not args.skip_copernicus_test,
    )
    lines = [f"validate ok={r.ok}", f"kml_dir={paths.kml_dir}", f"mapping_dir={paths.mapping_dir}"]
    for w in r.warnings:
        print("WARN:", w)
        lines.append(f"WARN: {w}")
    for e in r.errors:
        print("ERROR:", e)
        lines.append(f"ERROR: {e}")
    log_path = _write_validation_log(paths, lines)
    print(f"Wrote {log_path}")
    return 0 if r.ok else 1


def cmd_migrate(_args: argparse.Namespace) -> int:
    import subprocess

    script = _root / "scripts" / "run_sql_migrations_v2.py"
    return subprocess.call([sys.executable, str(script)])


def _run_config(args: argparse.Namespace, *, mode: str) -> int:
    from satellite_deployment.logging_setup import setup_deployment_logging
    from satellite_deployment.runner import HarvestDeploymentRunner, RunConfig

    paths = _paths()
    paths.ensure_runtime_dirs()

    run_id = uuid.UUID(args.run_id) if getattr(args, "run_id", None) else uuid.uuid4()
    setup_deployment_logging(paths.log_dir, level=args.log_level, run_id=str(run_id))

    if not args.validate_only and not args.dry_run and (not args.start or not args.end):
        print("--start and --end required", file=sys.stderr)
        return 2

    start_d = date.fromisoformat(args.start) if args.start else date.today()
    end_d = date.fromisoformat(args.end) if args.end else date.today()

    runner = HarvestDeploymentRunner(paths=paths)
    cfg = RunConfig(
        start=start_d,
        end=end_d,
        season_id=args.season_id,
        mode=mode,
        batch_strategy=args.batch_strategy,
        resume=not args.no_resume,
        use_stac=not args.no_stac,
        max_cloud_cover=args.maxcc,
        api_delay_seconds=args.api_delay,
        max_retries_per_field=args.max_retries,
        skip_errors=not args.stop_on_error,
        limit=args.limit,
        only_internal_ids=args.only_internal_id,
        run_id=run_id,
        validate_only=args.validate_only,
        dry_run=args.dry_run,
    )
    return runner.run(cfg, resume_failed_only=getattr(args, "resume_failed", False))


def _check_lab_entrypoint() -> None:
    script = Path(__file__).resolve()
    if not script.is_file():
        print(f"FATAL: missing lab runner {script}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    _ensure_lab_layout()
    _check_lab_entrypoint()
    parser = argparse.ArgumentParser(description="Satellite harvest lab deployment CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_boot = sub.add_parser("bootstrap", help="Install deps + validate environment")
    p_boot.add_argument("--install-deps", action="store_true")
    p_boot.add_argument("--upgrade", action="store_true")
    p_boot.add_argument("--skip-db-test", action="store_true")
    p_boot.add_argument("--skip-copernicus-test", action="store_true")
    p_boot.add_argument("--expected-kml", type=int, default=253)

    p_val = sub.add_parser("validate", help="Pre-flight checks only")
    p_val.add_argument("--expected-kml", type=int, default=253)
    p_val.add_argument("--skip-db-test", action="store_true")
    p_val.add_argument("--skip-copernicus-test", action="store_true")

    sub.add_parser("migrate", help="Apply SQL migrations")

    for name, mode in (("run", "incremental"), ("reprocess", "reprocess_raw")):
        p = sub.add_parser(name, help=f"Harvest batch ({mode})")
        p.add_argument("--start", type=str)
        p.add_argument("--end", type=str)
        p.add_argument("--season-id", type=str, default="RABI_25_26")
        p.add_argument("--batch-strategy", default="quarterly")
        p.add_argument("--maxcc", type=float, default=None)
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--only-internal-id", action="append", default=None)
        p.add_argument("--no-resume", action="store_true")
        p.add_argument("--resume-failed", action="store_true")
        p.add_argument("--no-stac", action="store_true")
        p.add_argument("--validate-only", action="store_true")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--run-id", type=str, default=None)
        p.add_argument("--api-delay", type=float, default=2.0)
        p.add_argument("--max-retries", type=int, default=2)
        p.add_argument("--stop-on-error", action="store_true")
        p.add_argument("--log-level", type=str, default="INFO")

    args = parser.parse_args()
    if args.command == "bootstrap":
        return cmd_bootstrap(args)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "migrate":
        return cmd_migrate(args)
    if args.command == "run":
        return _run_config(args, mode="incremental")
    if args.command == "reprocess":
        return _run_config(args, mode="reprocess_raw")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
