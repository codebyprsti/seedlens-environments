"""In-run health / progress metrics for batch ingestion."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class HealthSnapshot:
    total: int = 0
    processed: int = 0
    failed: int = 0
    skipped: int = 0
    in_progress: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    field_durations_sec: list[float] = field(default_factory=list)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.processed - self.failed - self.skipped)

    @property
    def avg_seconds_per_field(self) -> Optional[float]:
        if not self.field_durations_sec:
            return None
        return sum(self.field_durations_sec) / len(self.field_durations_sec)

    @property
    def eta_seconds(self) -> Optional[float]:
        avg = self.avg_seconds_per_field
        if avg is None:
            return None
        return avg * self.remaining

    def to_dict(self) -> dict:
        d = asdict(self)
        d["remaining"] = self.remaining
        d["avg_seconds_per_field"] = self.avg_seconds_per_field
        d["eta_seconds"] = self.eta_seconds
        d["elapsed_seconds"] = time.time() - self.started_at
        return d

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
