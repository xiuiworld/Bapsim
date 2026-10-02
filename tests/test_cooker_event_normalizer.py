from datetime import datetime, timedelta, timezone
import unittest

from domain.events.normalizer import CookerEventNormalizer


START = datetime(2026, 10, 2, 7, 0, tzinfo=timezone.utc)


def raw(
    second: int,
    event_type: str,
    value: str,
    *,
    availability: str = "online",
    source: str = "simulator",
    observed_second: int | None = None,
) -> dict[str, str]:
    return {
        "device_id": "cooker_01",
        "event_type": event_type,
        "value": value,
        "observed_at": (START + timedelta(seconds=observed_second if observed_second is not None else second)).isoformat(),
        "received_at": (START + timedelta(seconds=second)).isoformat(),
        "source": source,
        "availability": availability,
        "quality": "simulated",
    }


class CookerEventNormalizerTests(unittest.TestCase):
    def test_lid_requires_known_transition_and_debounce(self) -> None:
        normalizer = CookerEventNormalizer()
        self.assertEqual([e.event_type for e in normalizer.ingest(raw(0, "lid_state", "closed"))], ["device_online"])
        self.assertEqual(normalizer.ingest(raw(1, "lid_state", "closed")), [])
        self.assertEqual(normalizer.ingest(raw(2, "lid_state", "open")), [])
        self.assertEqual(normalizer.ingest(raw(3, "lid_state", "closed")), [])  # bounce
        self.assertEqual(normalizer.flush(START + timedelta(seconds=5)), [])

        self.assertEqual(normalizer.ingest(raw(6, "lid_state", "open")), [])
        events = normalizer.flush(START + timedelta(seconds=8))
        self.assertEqual([e.event_type for e in events], ["lid_opened"])
        self.assertEqual(events[0].observed_at, raw(6, "lid_state", "open")["observed_at"])
        self.assertEqual(events[0].received_at, raw(6, "lid_state", "open")["received_at"])
        self.assertEqual(normalizer.ingest(raw(9, "lid_state", "open")), [])
        self.assertEqual(normalizer.ingest(raw(10, "lid_state", "closed")), [])
        self.assertEqual([e.event_type for e in normalizer.flush(START + timedelta(seconds=12))], ["lid_closed"])

    def test_cooking_transitions_and_unknown_reset(self) -> None:
        normalizer = CookerEventNormalizer()
        self.assertEqual([e.event_type for e in normalizer.ingest(raw(0, "cooking_state", "started"))], ["device_online"])
        self.assertEqual(normalizer.ingest(raw(1, "cooking_state", "started")), [])
        self.assertEqual([e.event_type for e in normalizer.ingest(raw(2, "cooking_state", "finished"))], ["cooking_finished"])
        self.assertEqual(normalizer.ingest(raw(3, "cooking_state", "unknown")), [])
        self.assertEqual(normalizer.ingest(raw(4, "cooking_state", "started")), [])
        self.assertEqual([e.event_type for e in normalizer.ingest(raw(5, "cooking_state", "finished"))], ["cooking_finished"])

    def test_offline_cancels_pending_and_clears_baseline(self) -> None:
        normalizer = CookerEventNormalizer()
        normalizer.ingest(raw(0, "lid_state", "closed"))
        normalizer.ingest(raw(1, "lid_state", "open"))
        offline = normalizer.ingest(raw(2, "availability", "offline", availability="offline"))
        self.assertEqual([e.event_type for e in offline], ["device_offline"])
        self.assertEqual(normalizer.flush(START + timedelta(seconds=10)), [])
        self.assertEqual(normalizer.ingest(raw(11, "lid_state", "open", availability="offline")), [])
        self.assertEqual([e.event_type for e in normalizer.ingest(raw(12, "availability", "online"))], ["device_online"])
        self.assertEqual(normalizer.ingest(raw(13, "lid_state", "open")), [])
        self.assertEqual(normalizer.ingest(raw(14, "lid_state", "closed")), [])
        self.assertEqual([e.event_type for e in normalizer.flush(START + timedelta(seconds=16))], ["lid_closed"])

    def test_old_report_and_unrelated_away_state_are_ignored(self) -> None:
        normalizer = CookerEventNormalizer(lid_debounce=timedelta(0))
        normalizer.ingest(raw(0, "lid_state", "closed"))
        self.assertEqual([e.event_type for e in normalizer.ingest(raw(2, "lid_state", "open"))], ["lid_opened"])
        self.assertEqual(normalizer.ingest(raw(3, "lid_state", "closed", observed_second=1)), [])
        self.assertEqual(normalizer.ingest(raw(4, "away_state", "away")), [])
        self.assertEqual(normalizer.ingest(raw(5, "lid_state", "open")), [])

    def test_late_online_report_cannot_override_offline(self) -> None:
        normalizer = CookerEventNormalizer()
        normalizer.ingest(raw(0, "lid_state", "closed"))
        self.assertEqual(
            [e.event_type for e in normalizer.ingest(raw(3, "availability", "offline", availability="offline"))],
            ["device_offline"],
        )
        self.assertEqual(normalizer.ingest(raw(4, "lid_state", "open", observed_second=2)), [])
        self.assertEqual(normalizer.ingest(raw(5, "availability", "offline", availability="offline")), [])

    def test_future_matter_raw_mapping_uses_same_canonical_contract(self) -> None:
        normalizer = CookerEventNormalizer(
            lid_debounce=timedelta(0),
            raw_fields={("matter", "contact"): "lid"},
            raw_values={("matter", "contact", "false"): "closed", ("matter", "contact", "true"): "open"},
        )
        self.assertEqual([e.event_type for e in normalizer.ingest(raw(0, "contact", "false", source="matter"))], ["device_online"])
        events = normalizer.ingest(raw(1, "contact", "true", source="matter"))
        self.assertEqual([e.event_type for e in events], ["lid_opened"])
        self.assertEqual(events[0].value, "open")
        self.assertEqual(events[0].source, "matter")

    def test_rejects_missing_or_naive_timestamps(self) -> None:
        normalizer = CookerEventNormalizer()
        with self.assertRaises(ValueError):
            normalizer.ingest({"device_id": "cooker_01"})
        event = raw(0, "lid_state", "closed")
        event["observed_at"] = "2026-10-02T07:00:00"
        with self.assertRaises(ValueError):
            normalizer.ingest(event)


if __name__ == "__main__":
    unittest.main()
