import base64
import copy
import json
from pathlib import Path
import unittest
from tools.a05_contract.wire import encode, decode, Continuity, HEADER


class WireTests(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads(Path(__file__).with_name("snapshots.test.json").read_text())["cases"]

    def test_roundtrip_all_scenarios_and_base64(self):
        for case in self.cases:
            snapshot = case["snapshot"]
            # enum null and enum 0 have the same defined wire representation.
            wire = encode(snapshot, test_input=True)
            self.assertEqual(decode(wire), (snapshot, True))
            self.assertEqual(decode(base64.b64encode(wire).decode()), (snapshot, True))

    def test_bad_payloads_rejected(self):
        wire = encode(self.cases[0]["snapshot"], test_input=True)
        for bad in (wire[:-1], wire + b'x', b'junk' + wire[4:], '%%%bad', list(wire)):
            with self.subTest(bad=type(bad)):
                with self.assertRaises((ValueError, UnicodeError)):
                    decode(bad)

    def test_continuity(self):
        tracker = Continuity()
        first = self.cases[0]["snapshot"]
        self.assertEqual(tracker.accept(first), "baseline")
        self.assertEqual(tracker.accept(first), "duplicate")
        second = copy.deepcopy(first)
        second.update(ReportCounter=2, ObservationSequence=2, UptimeMs=2000)
        self.assertEqual(tracker.accept(second), "continuous")
        second.update(ObservationSequence=5, ReportCounter=3)
        self.assertEqual(tracker.accept(second), "gap")
        second.update(BootId=2, ObservationSequence=0, ReportCounter=0, UptimeMs=0)
        self.assertEqual(tracker.accept(second), "reboot")
        tracker.reset()
        self.assertEqual(tracker.accept(second), "baseline")
