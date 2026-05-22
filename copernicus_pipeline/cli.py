"""Typer CLI: ``copernicus-pipeline run ...`` (wraps ``scripts/run_crop_analysis_s3_batch.py``)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

import typer

from copernicus_pipeline import __version__
from copernicus_pipeline.config import get_settings
from copernicus_pipeline.env_sync import apply_pipeline_environment, load_dotenv_files
from copernicus_pipeline.errors import PipelineError, ValidationError
from copernicus_pipeline.logging_config import setup_logging
from copernicus_pipeline.pipeline import run_pipeline

app = typer.Typer(
    name="copernicus-pipeline",
    help="Production CLI for the SeedIQ Copernicus / Sentinel Hub KML batch (no duplicate satellite code).",
    no_args_is_help=True,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit(0)


@app.callback()
def _main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Print version and exit"),
    ] = False,
) -> None:
    del ctx, version


@app.command("run")
def run_cmd(
    kml_dir: Annotated[Path, typer.Option("--kml-dir", help="Directory of .kml files (recursive)")],
    start: Annotated[str, typer.Option("--start", help="YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--end", help="YYYY-MM-DD")],
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help=(
                "default/crop_indices = legacy crop_indices; locations_only = geocode; "
                "satellite_v2 = v2 sentinel tables; reprocess_raw = indices from raw only"
            ),
        ),
    ] = "default",
    batch_strategy: Annotated[
        str,
        typer.Option(
            "--batch-strategy",
            help="v2 only: weekly|monthly|quarterly|full Statistical API chunking",
        ),
    ] = "quarterly",
    maxcc: Annotated[
        float,
        typer.Option(
            "--maxcc",
            help="S2 scene search ceiling %% (pixel SCL mask applied); default 60 for monsoon",
        ),
    ] = 60.0,
    no_stac: Annotated[bool, typer.Option("--no-stac", help="v2: skip STAC catalogue")] = False,
    season_id: Annotated[Optional[str], typer.Option("--season-id", help="Season id (e.g. RABI_25_26)")] = None,
    limit: Annotated[Optional[int], typer.Option("--limit", help="Max number of KML files")] = None,
    only_file: Annotated[
        Optional[list[str]],
        typer.Option("--only-file", help="Process only this basename (repeatable)"),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="List KMLs only")] = False,
    log_file: Annotated[Optional[Path], typer.Option("--log-file", help="Append logs to this path")] = None,
    skip_satellite_raw: Annotated[
        bool,
        typer.Option("--skip-satellite-raw", help="Set SKIP_SATELLITE_RAW_OBSERVATION=1 for the batch process"),
    ] = False,
    geocode_delay: Annotated[
        Optional[float],
        typer.Option("--geocode-delay", help="Seconds between geocode calls (locations_only)"),
    ] = None,
    jobs: Annotated[int, typer.Option("--jobs", help="Parallel batch subprocesses (1 = single batch run)")] = 1,
    progress: Annotated[bool, typer.Option("--progress/--no-progress", help="Progress bar (needs tqdm)")] = False,
    skip_env_checks: Annotated[
        bool,
        typer.Option(
            "--skip-env-checks",
            help="Do not validate DATABASE_URL/DB_* or Sentinel credentials before run (not recommended)",
        ),
    ] = False,
) -> None:
    """Run legacy crop_indices, locations_only, satellite_v2, or reprocess_raw."""
    load_dotenv_files()
    settings = get_settings()
    apply_pipeline_environment(settings)
    setup_logging(settings.log_level, log_file)

    try:
        code = run_pipeline(
            kml_dir=kml_dir,
            start=start,
            end=end,
            mode=mode,
            season_id=season_id,
            limit=limit,
            only_files=only_file if only_file else None,
            dry_run=dry_run,
            log_file=log_file,
            geocode_delay=geocode_delay,
            skip_satellite_raw=skip_satellite_raw,
            jobs=jobs,
            progress=progress,
            skip_env_checks=skip_env_checks,
            batch_strategy=batch_strategy,
            maxcc=maxcc,
            use_stac=not no_stac,
        )
    except ValidationError as e:
        logging.getLogger(__name__).error("%s", e)
        raise typer.Exit(2) from e
    except PipelineError as e:
        logging.getLogger(__name__).error("%s", e)
        raise typer.Exit(e.exit_code) from e
    except Exception as e:
        logging.getLogger(__name__).exception("Unexpected failure: %s", e)
        raise typer.Exit(1) from e

    raise typer.Exit(int(code))


def main() -> None:
    """Console script entry point."""
    app()


if __name__ == "__main__":
    main()
