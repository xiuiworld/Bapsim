"""Day-partitioned normalized observations with bounded local retention."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Mapping


class ObservationJournal:
    def __init__(self, directory: Path, keep_days: int = 90, prefix: str = "normalized") -> None:
        if keep_days < 1:
            raise ValueError("keep_days must be positive")
        if not prefix.isalpha():
            raise ValueError("prefix must contain only letters")
        self.directory = Path(directory)
        self.keep_days = keep_days
        self.prefix = prefix

    def append(self, event: Mapping[str, object]) -> Path:
        at = event.get("observed_at", event.get("as_of"))
        if not isinstance(at, str):
            raise ValueError("normalized event needs observed_at")
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("normalized event needs timezone")
        day = moment.astimezone(timezone.utc).date()
        path = self.directory / f"{self.prefix}-{day.isoformat()}.jsonl"
        self.directory.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")) + "\n")
        return path

    def prune(self, now: datetime) -> list[Path]:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("prune time needs timezone")
        cutoff = now.astimezone(timezone.utc).date() - timedelta(days=self.keep_days)
        removed: list[Path] = []
        for path in self.directory.glob(f"{self.prefix}-????-??-??.jsonl"):
            try:
                day = date.fromisoformat(path.stem.removeprefix(f"{self.prefix}-"))
            except ValueError:
                continue
            if day < cutoff:
                path.unlink()
                removed.append(path)
        return removed
