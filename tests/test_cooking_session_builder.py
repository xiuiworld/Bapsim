from datetime import datetime, timedelta, timezone
import unittest

from domain.sessions import CookingSessionBuilder


BASE = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)


def event(minute: int, kind: str, *, device_id: str = "cooker_01") -> dict[str, str]:
    return {
        "device_id": device_id,
        "source": "simulator",
        "event_type": kind,
        "observed_at": (BASE + timedelta(minutes=minute)).isoformat(),
    }


class CookingSessionBuilderTests(unittest.TestCase):
    def test_full_session_and_next_start_finalize_previous(self) -> None:
        builder = CookingSessionBuilder()
        started = builder.ingest(event(0, "cooking_started"))[0]
        self.assertTrue(started.session.incomplete)
        builder.ingest(event(42, "cooking_finished"))
        builder.ingest(event(65, "lid_opened"))
        builder.ingest(event(70, "lid_opened"))
        updates = builder.ingest(event(180, "cooking_started"))
        finished = updates[0].session
        self.assertEqual(updates[0].kind, "session_finalized")
        self.assertEqual(finished.lid_open_count, 2)
        self.assertEqual(finished.first_open_delay_min, 23)
        self.assertFalse(finished.incomplete)
        self.assertTrue(finished.finalized)
        self.assertEqual(updates[1].session.lid_open_count, 0)
        self.assertTrue(started.session.incomplete)  # Earlier snapshots stay unchanged.

    def test_missing_start_and_data_gap_are_visible(self) -> None:
        builder = CookingSessionBuilder()
        orphan = builder.ingest(event(42, "cooking_finished"))[0].session
        self.assertTrue(orphan.incomplete)
        self.assertTrue(orphan.data_gap)
        builder.ingest(event(65, "lid_opened"))
        final = builder.finalize_all()[0].session
        self.assertEqual(final.lid_open_count, 1)
        self.assertTrue(final.finalized)

    def test_offline_marks_gap_without_inventing_activity(self) -> None:
        builder = CookingSessionBuilder()
        builder.ingest(event(0, "cooking_started"))
        self.assertEqual(builder.ingest(event(10, "device_offline"))[0].session.data_gap, True)
        self.assertEqual(builder.ingest(event(11, "device_offline")), [])
        self.assertEqual(builder.ingest(event(12, "device_online")), [])
        final = builder.finalize_all()[0].session
        self.assertTrue(final.incomplete)
        self.assertEqual(final.lid_open_count, 0)

    def test_open_before_finish_or_new_start_is_not_counted(self) -> None:
        builder = CookingSessionBuilder()
        builder.ingest(event(0, "cooking_started"))
        self.assertEqual(builder.ingest(event(20, "lid_opened")), [])
        builder.ingest(event(42, "cooking_finished"))
        self.assertEqual(builder.ingest(event(40, "lid_opened")), [])
        builder.ingest(event(60, "cooking_started"))
        self.assertEqual(builder.ingest(event(50, "lid_opened")), [])

    def test_late_start_can_complete_orphan_finish(self) -> None:
        builder = CookingSessionBuilder()
        builder.ingest(event(42, "cooking_finished"))
        repaired = builder.ingest(event(0, "cooking_started"))[0].session
        self.assertFalse(repaired.incomplete)
        self.assertFalse(repaired.data_gap)


if __name__ == "__main__":
    unittest.main()
