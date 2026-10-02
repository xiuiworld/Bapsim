import unittest

from ai.calibration import components, experiment, live_predict, metrics, predict
from ai.synthetic import population


class CalibrationTests(unittest.TestCase):
    def test_labels_are_situation_based_and_gates_hold(self):
        cases = population(1, 90)
        self.assertEqual({case.label for case in cases if case.scenario == "mismatch"}, {1})
        self.assertEqual({case.label for case in cases if case.scenario == "sensor_noise"}, {0})
        for case in cases:
            if case.away or not case.data_available:
                self.assertIsNone(predict(case, (0.4, 0.3, 0.3, 0), 0.6))
        result = metrics(cases, (0.4, 0.3, 0.3, 0), 0.6)
        self.assertEqual(result["tp"] + result["fp"] + result["tn"] + result["fn"],
                         sum(case.label is not None for case in cases))

    def test_zero_open_ratio_counts_as_mismatch(self):
        from dataclasses import replace
        case = replace(population(2, 9)[0], open_ratio=0.0, cook_ratio=1.0,
                       data_available=True, away=False)
        self.assertEqual(components(case)[2], 1.0)

    def test_live_baseline_runs_the_operational_detector(self):
        from dataclasses import replace
        case = replace(population(2, 9)[0], scenario="mismatch", open_ratio=0.0,
                       delay_ratio=2.5, cook_ratio=1.0, hours_ratio=2.0,
                       data_available=True, away=False)
        self.assertEqual(live_predict(case), 1)
        self.assertIsNone(live_predict(replace(case, away=True)))
        self.assertIsNone(live_predict(replace(case, data_available=False)))
        result = experiment()
        self.assertEqual(set(result["variants"]), {"live_rule", "optimized_cooker"})
        self.assertIsNone(result["variants"]["live_rule"]["weights"])


if __name__ == "__main__":
    unittest.main()
