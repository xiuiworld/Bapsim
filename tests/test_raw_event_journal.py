"""Check that the durable raw log keeps exact source fields and separates streams."""

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from inputs.base.journal import RawEventJournal


class RawEventJournalTests(TestCase):
    def test_append_retains_raw_values_and_partitions_by_utc_reception_day(self):
        raw = {
            "device_id": "cooker_01", "event_type": "board_observation_temperature",
            "value": "41.50", "observed_at": "2026-10-02T00:01:00+09:00",
            "received_at": "2026-10-02T00:02:00+09:00", "source": "matter",
            "availability": "online", "quality": "observed",
        }
        with TemporaryDirectory() as directory:
            journal = RawEventJournal(Path(directory))
            path = journal.append("cooker", raw)
            journal.append("cooker", raw)
            context = journal.append("context", {**raw, "event_type": "occupancy_state", "value": "off"})
            self.assertEqual(path.name, "cooker-2026-10-01.jsonl")
            self.assertEqual(context.name, "context-2026-10-01.jsonl")
            self.assertEqual([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()], [raw, raw])
            self.assertEqual(json.loads(context.read_text(encoding="utf-8"))["value"], "off")

    def test_rejects_incomplete_or_naive_events(self):
        raw = {"device_id": "x", "event_type": "lid_state", "value": "open",
               "observed_at": datetime.now(timezone.utc).isoformat(),
               "received_at": datetime.now(timezone.utc).isoformat(),
               "source": "matter", "availability": "online", "quality": "observed"}
        with TemporaryDirectory() as directory:
            journal = RawEventJournal(Path(directory))
            with self.assertRaises(ValueError):
                journal.append("cooker", {**raw, "received_at": "2026-10-02T00:00:00"})
            with self.assertRaises(ValueError):
                journal.append("cooker", {key: value for key, value in raw.items() if key != "quality"})
            self.assertEqual(list(Path(directory).iterdir()), [])
