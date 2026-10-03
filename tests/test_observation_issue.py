from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from service.standalone import AnalysisService
from workflow import ObservationIssueTracker


KST = timezone(timedelta(hours=9))
START = datetime(2026, 10, 2, 10, tzinfo=KST)


def at(minutes: int) -> str:
    return (START + timedelta(minutes=minutes)).isoformat()


class ObservationIssueTests(TestCase):
    def test_sustained_outage_has_daily_limit_and_survives_restart(self) -> None:
        tracker = ObservationIssueTracker()
        self.assertIsNone(tracker.update("matter", "cooker", False, at(0)))
        self.assertIsNone(tracker.update("matter", "cooker", False, at(9)))
        first = tracker.update("matter", "cooker", False, at(10))
        self.assertEqual(first.action, "observation_issue_requested")
        self.assertIsNone(tracker.update("matter", "cooker", False, at(11)))
        restored = ObservationIssueTracker()
        restored.load_state(tracker.dump_state())
        self.assertIsNone(restored.update("matter", "cooker", False, at(60)))
        second = restored.update("matter", "cooker", False, at(24 * 60))
        self.assertNotEqual(first.notification_id, second.notification_id)
        restored.update("matter", "cooker", True, at(24 * 60 + 1))
        self.assertFalse(restored.is_unavailable("matter", "cooker"))

    def test_quiet_outage_is_discarded_after_connection_recovers(self) -> None:
        with TemporaryDirectory() as directory:
            sent = []
            start = datetime(2026, 10, 2, 23, tzinfo=KST)
            moment = lambda minutes: (start + timedelta(minutes=minutes)).isoformat()
            service = AnalysisService("home", Path(directory) / "state.json",
                                      lambda *args: None, lambda update: sent.append(update) or True)
            key = ("matter", "cooker")
            service.availability[key] = False
            service.evaluate(*key, moment(0))
            service.evaluate(*key, moment(10))
            self.assertEqual(len(service.pending_quiet), 1)
            service.availability[key] = True
            service.last_heartbeat[key] = datetime.fromisoformat(moment(9 * 60))
            service.evaluate(*key, moment(11))
            service.tick(moment(9 * 60))
            self.assertEqual(sent, [])
            self.assertEqual(service.pending_quiet, {})
