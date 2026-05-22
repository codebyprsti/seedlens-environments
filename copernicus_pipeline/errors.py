"""Structured errors and exit codes for the pipeline CLI."""

from __future__ import annotations


class PipelineError(Exception):
    """Base class for CLI / runner failures."""

    exit_code: int = 1


class ValidationError(PipelineError):
    """Invalid arguments or paths."""

    exit_code = 2


class BatchProcessError(PipelineError):
    """Underlying batch script exited non-zero."""

    exit_code = 3
