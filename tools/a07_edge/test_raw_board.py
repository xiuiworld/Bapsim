"""Verify the independent oracle exercises recovery, classes and missing input."""
import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from .raw_board import cases
from .observations import extract
from .verify_raw_log import verify


class ReplayOracleTests(unittest.TestCase):
    def test_safety_evidence_must_be_complete_and_pass(self):
        manifest = dict(model='model', preprocess='v2',
            cases=[dict(case='one',windows=1,samples=1200)],
            safety=dict(rejected=55,accepted=10,recovery=28))
        safety = 'A07 SAFETY END rejected=55 accepted=10 recovery=28 failures=0\n'
        start = 'A07 RAW BEGIN version=v2 state_bytes=28960 model=model\n'
        end = ('A07 RAW CASE one windows=1/1 features=0 valid=0 led=0 model=0\n'
               'A07 RAW END cases=1 windows=1 failures=0 window_mean_ns=1 '
               'window_max_ns=1 stack_rc=0 unused=100\n')
        with TemporaryDirectory() as directory:
            root=Path(directory); m=root/'manifest.json'; log=root/'run.log'
            m.write_text(json.dumps(manifest))
            log.write_text(start+safety+end)
            self.assertEqual(verify(log,m)['safety']['rejected'],55)
            for bad in ('', safety+safety, safety.replace('failures=0','failures=1'),
                        safety.replace('rejected=55','rejected=54')):
                log.write_text(start+bad+end)
                with self.assertRaises(ValueError):verify(log,m)
            manifest['blink']=dict(cases=220,windows=6860)
            m.write_text(json.dumps(manifest))
            blink='A07 BLINK END cases=220 windows=6860 failures=0\n'
            log.write_text(start+safety+blink+end)
            self.assertEqual(verify(log,m)['blink']['cases'],220)
            for bad in ('',blink+blink,blink.replace('failures=0','failures=1'),
                        blink.replace('windows=6860','windows=6859')):
                log.write_text(start+safety+bad+end)
                with self.assertRaises(ValueError):verify(log,m)

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
        for name in ('gap_midpoint_on', 'gap_midpoint_off', 'missing_midpoint', 'startup_midpoint'):
            self.assertFalse(found[name][1][-1])
            self.assertEqual(found[name][3][-1, 0], 0)


if __name__ == '__main__':
    unittest.main()
