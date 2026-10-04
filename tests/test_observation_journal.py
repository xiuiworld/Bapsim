from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from service import ObservationJournal


class ObservationJournalTests(TestCase):
    def test_partition_and_retention_boundary(self):
        with TemporaryDirectory() as directory:
            journal = ObservationJournal(Path(directory), keep_days=90)
            old = journal.append({"event_type": "lid_opened", "observed_at": "2026-07-04T00:00:00Z"})
            boundary = journal.append({"event_type": "lid_opened", "observed_at": "2026-07-05T00:00:00Z"})
            self.assertEqual(json.loads(boundary.read_text(encoding="utf-8"))["event_type"], "lid_opened")
            removed = journal.prune(datetime(2026, 10, 3, tzinfo=timezone.utc))
            self.assertEqual(removed, [old])
            self.assertTrue(boundary.exists())
