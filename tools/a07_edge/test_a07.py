import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from .dataset import episode
from .model import fit_tree, phase_fields, tree_predict
from .measured import import_measured
from .observations import Calibration, debounce, extract, ntc_c, read_jsonl, temperature_adc


class ObservationsTest(unittest.TestCase):
    def setUp(self):
        self.cal = Calibration()

    def test_ntc_roundtrip_both_wirings_and_rails(self):
        for ground in (True, False):
            c = Calibration(ntc_to_ground=ground)
            actual = np.array([0, 25, 60, 100.])
            np.testing.assert_allclose(ntc_c(temperature_adc(actual, c), c), actual, atol=1e-9)
            self.assertTrue(np.isnan(ntc_c([0, 4095, -1, np.nan], c)).all())

    def test_debounce_bounce_missing_and_reboot(self):
        np.testing.assert_array_equal(debounce([0, 20, 40, 60, 110, 160, 0, 50], [0, 1, 0, 1, 1, -1, 0, 0]),
                                      [-1, -1, -1, -1, 1, -1, -1, 0])

    def clean(self):
        n = 3600
        return np.arange(n)*50, np.tile([500, 3000, 500, 500, 2048], (n, 1)), np.zeros(n, int), np.ones(n, int)

    def test_led_reheat_blink_and_solid(self):
        t, adc, lid, boot = self.clean()
        adc[:, 1] = np.where(np.arange(len(t)) % 20 < 10, 3000, 500)
        x, valid, _, led = extract(t, adc, lid, boot, self.cal)
        self.assertTrue(valid.all())
        self.assertTrue((led[:, 1] == 3).all())
        self.assertTrue((led[:, 0] == 1).all())

    def test_fault_blocks_model_but_preserves_other_led_observations(self):
        t, adc, lid, boot = self.clean(); adc[1300:1400, 0] = -1
        _, valid, ends, led = extract(t, adc, lid, boot, self.cal)
        affected = (ends >= 1300) & (ends < 1400)
        self.assertFalse(valid[affected].any())
        self.assertTrue((led[affected, 1] == 2).all())

    def test_gap_and_reboot_require_new_complete_window(self):
        for reboot in (False, True):
            t, adc, lid, boot = self.clean()
            if reboot:
                boot[1500:] = 2; t[1500:] -= t[1500]
            else:
                t[1500:] += 1000
            _, valid, ends, _ = extract(t, adc, lid, boot, self.cal)
            self.assertFalse(valid[(ends >= 1500) & (ends < 2699)].any())
            self.assertTrue(valid[ends >= 2699].all())

    def test_causal_features_do_not_change_with_future(self):
        t, adc, lid, boot = self.clean()
        x, _, ends, _ = extract(t, adc, lid, boot, self.cal)
        adc[2000:, :4] = 3000
        z, _, _, _ = extract(t, adc, lid, boot, self.cal)
        np.testing.assert_array_equal(x[ends < 2000], z[ends < 2000])

    def test_sequence_gap_is_not_hidden_by_regular_timestamps(self):
        t, adc, lid, boot = self.clean(); seq = np.arange(len(t)); seq[1500:] += 1
        _, valid, ends, _ = extract(t, adc, lid, boot, self.cal, sequence=seq)
        self.assertFalse(valid[(ends >= 1500) & (ends < 2699)].any())
        self.assertTrue(valid[ends >= 2699].all())

    def test_seeded_generation_reproducible(self):
        a, af = episode(25, 'warming', self.cal)
        b, bf = episode(25, 'warming', self.cal)
        self.assertEqual(af, bf)
        for k in a:
            np.testing.assert_array_equal(a[k], b[k])

    def test_tree_can_learn_and_abstain_independently(self):
        x = np.zeros((400, 11), np.float32); x[200:, 8] = 80
        y = np.repeat([0, 1], 200)
        nodes = fit_tree(x, y, min_leaf=20)
        pred, _ = tree_predict(nodes, x, np.ones(400, bool))
        np.testing.assert_array_equal(pred, y)
        p, _ = tree_predict(nodes, x, np.zeros(400, bool))
        self.assertTrue((p == 3).all())
        x[:, 3] = 3
        p, _ = tree_predict(nodes, x, np.ones(400, bool))
        self.assertTrue((p == 3).all())

    def test_unknown_inference_not_inactive(self):
        f = phase_fields(3, .9)
        self.assertEqual(f['CookerPhase'], 0)
        self.assertIsNone(f['InferenceScore'])
        self.assertEqual(f['input_kind'], 'test')

    def test_nonfinite_inference_abstains(self):
        node = dict(feature=-1, threshold=0, left=-1, right=-1, **{'class':1}, score=1.)
        x = np.zeros((1,11), np.float32); x[0,8] = np.nan
        p, _ = tree_predict([node], x, np.array([True]))
        self.assertEqual(p[0], 3)

    def test_measured_import_excludes_unlabelled_and_checks_group_leakage(self):
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); spec = {'sessions':[]}
            for k, split in enumerate(('train','validation','test')):
                rows = []
                for i in range(1280):
                    rows.append(dict(schema='a07.raw.v1',input_kind='measured',session_id=f's{k}',
                                     boot_id=1,uptime_ms=i*50,sequence=i,adc=[500,3000,500,500,2048],
                                     lid=False,calibration_version=self.cal.version,firmware_version='record-v1',
                                     label='warming' if i>=1200 else None,note='human label'))
                (root/f's{k}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                spec['sessions'].append(dict(path=f's{k}.jsonl',profile=str(k),split=split,
                                             calibration=asdict(self.cal)))
            p = root/'spec.json'; p.write_text(json.dumps(spec))
            result = import_measured(p,root/'out')
            self.assertEqual(result['labelled_windows'],6)
            self.assertTrue((np.load(root/'out/features.npz')['y']==2).all())
            spec['sessions'][1]['profile']='0'; p.write_text(json.dumps(spec))
            with self.assertRaises(ValueError):
                import_measured(p,root/'bad')

    def test_raw_reader_rejects_duplicate_and_missing_provenance(self):
        row = dict(schema='a07.raw.v1', input_kind='measured', session_id='s', boot_id=1,
                   uptime_ms=0, sequence=1, adc=[500]*5, lid=False, calibration_version='c1',
                   firmware_version='f1', label=None, note='unlabeled actual ADC')
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'raw.jsonl'; p.write_text(json.dumps(row)+'\n'+json.dumps(row)+'\n')
            with self.assertRaises(ValueError):
                list(read_jsonl(p))
            del row['input_kind']; p.write_text(json.dumps(row))
            with self.assertRaises(ValueError):
                list(read_jsonl(p))


if __name__ == '__main__':
    unittest.main()
