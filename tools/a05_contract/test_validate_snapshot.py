"""Boundary and failure checks for the A05 raw contract candidate."""
import copy
import json
from pathlib import Path
import unittest

from validate_snapshot import validate


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(Path(__file__).with_name("snapshots.test.json").read_text())["cases"]
        self.sample = copy.deepcopy(self.cases[0]["snapshot"])

    def test_examples_preserved_without_normalizing(self):
        for case in self.cases:
            value = copy.deepcopy(case["snapshot"])
            before = copy.deepcopy(value)
            self.assertIs(validate(value), value)
            self.assertEqual(value, before)

    def test_invalid_inputs_rejected(self):
        changes = [
            ("ReportCounter", True), ("ReportCounter", -1),
            ("BootId", 2**32), ("UptimeMs", 2**64),
            ("SchemaVersion", 2), ("LockLed", 4), ("LidOpen", 1),
            ("TemperatureCentiC", -32768), ("TemperatureCentiC", float("nan")),
            ("InferenceMethod", 0), ("ModelVersion", "가" * 22),
            ("InferenceScore", 10001),
        ]
        for name, value in changes:
            with self.subTest(name=name, value=value):
                candidate = copy.deepcopy(self.sample)
                candidate[name] = value
                with self.assertRaises(ValueError):
                    validate(candidate)

    def test_quality_cannot_hide_stale_normal_value(self):
        self.sample["SignalQuality"]["LockLed"] = 2
        with self.assertRaises(ValueError):
            validate(self.sample)
        self.sample["LockLed"] = None
        validate(self.sample)

    def test_missing_fields_fail_closed(self):
        for name in list(self.sample):
            with self.subTest(name=name):
                candidate = copy.deepcopy(self.sample)
                del candidate[name]
                with self.assertRaises(ValueError):
                    validate(candidate)

    def test_zero_temperature_is_valid(self):
        self.sample["TemperatureCentiC"] = 0
        validate(self.sample)


if __name__ == "__main__":
    unittest.main()
