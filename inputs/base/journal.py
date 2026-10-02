"""Append-only local audit log for the raw input boundary.

The journal preserves transport values. It never normalizes signals or infers
behavior. Files are partitioned by UTC reception date for easy backup/export.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Mapping


FIELDS = (
    "device_id", "event_type", "value", "observed_at", "received_at",
    "source", "availability", "quality",
)
STREAMS = {"cooker", "context"}


class RawEventJournal:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def append(self, stream: str, event: Mapping[str, object]) -> Path:
        if stream not in STREAMS:
            raise ValueError("invalid raw event stream")
        if not all(isinstance(event.get(key), str) for key in FIELDS):
            raise ValueError("raw event must contain the eight string fields")
        received = datetime.fromisoformat(str(event["received_at"]).replace("Z", "+00:00"))
        observed = datetime.fromisoformat(str(event["observed_at"]).replace("Z", "+00:00"))
        if received.tzinfo is None or received.utcoffset() is None or observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("raw event times require timezone")
        day = received.astimezone(timezone.utc).date().isoformat()
        path = self.directory / f"{stream}-{day}.jsonl"
        self.directory.mkdir(parents=True, exist_ok=True)
        record = {key: event[key] for key in FIELDS}
        with path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return path
