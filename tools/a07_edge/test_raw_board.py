"""Verify the independent oracle exercises recovery, classes and missing input."""
import unittest
import numpy as np
from .raw_board import cases
from .observations import extract


class ReplayOracleTests(unittest.TestCase):
    def test_faults_recover_only_after_required_history(self):
        found = {}
        for name, d in cases():
            x, valid, ends, led = extract(d['t'], d['adc'], d['lid'], d['boot'],
                                         d['cal'], sequence=d['seq'])
            found[name] = (x, valid, ends, led)
        self.assertEqual(len(found['warmup'][0]), 0)
        for name in ('time_gap', 'sequence_gap', 'boot_restart'):
            valid = found[name][1]
            self.assertTrue(valid[0])
            self.assertFalse(valid[1:30].any())
            self.assertTrue(valid[30:].all())
        for name in ('missing_led', 'ntc_fault', 'contact_fault'):
            self.assertTrue((~found[name][1]).any())
            self.assertTrue(found[name][1][-1])
        # A missing lock LED must not erase the white LED observation.
        self.assertEqual(found['missing_led'][3][0, 0], 0)
        self.assertEqual(found['missing_led'][3][0, 2], 2)
        self.assertTrue(np.all(found['blink_and_bounce'][3][:, 1] == 3))
        np.testing.assert_allclose(found['steady_ramp'][0], found['inverted_led'][0])
        self.assertTrue(found['reversed_ntc'][1].all())


if __name__ == '__main__':
    unittest.main()
