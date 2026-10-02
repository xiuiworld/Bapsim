import unittest

from inputs.matter import MatterBoardSignalAdapter, MatterContactAdapter, MatterContextAdapter, MatterCookingStateAdapter


NOW = "2026-10-02T10:00:00+09:00"


class MatterAdapterTests(unittest.TestCase):
    def test_on_polarity_and_raw_boundary(self):
        adapter = MatterContactAdapter("cooker_matter", on_is_open=True)
        event = adapter.translate("on", NOW, NOW)
        self.assertEqual(event["value"], "open")
        self.assertEqual(event["source"], "matter")
        self.assertEqual(len(event), 8)
        self.assertEqual(MatterContactAdapter("x", on_is_open=False).translate("on", NOW, NOW)["value"], "closed")

    def test_unavailable_is_not_closed(self):
        event = MatterContactAdapter("x", on_is_open=True).translate("unavailable", NOW, NOW)
        self.assertEqual(event["event_type"], "availability")
        self.assertEqual(event["availability"], "offline")
        self.assertEqual(event["quality"], "unknown")

    def test_cooking_state_mapping_keeps_raw_boundary(self):
        adapter = MatterCookingStateAdapter("cooker_matter", started_state="running", finished_state="idle")
        started = adapter.translate("running", NOW, NOW)
        finished = adapter.translate("idle", NOW, NOW)
        self.assertEqual(started["event_type"], "cooking_state")
        self.assertEqual(started["value"], "started")
        self.assertEqual(finished["value"], "finished")
        self.assertEqual(len(started), 8)
        unavailable = adapter.translate("unavailable", NOW, NOW)
        self.assertEqual(unavailable["event_type"], "cooking_input_availability")
        self.assertEqual(unavailable["availability"], "offline")
        self.assertEqual(unavailable["quality"], "unknown")
        with self.assertRaises(ValueError):
            adapter.translate("keep_warm", NOW, NOW)
        with self.assertRaises(ValueError):
            MatterCookingStateAdapter("cooker_matter", started_state="on", finished_state="on")

    def test_optional_context_forwards_raw_state_without_behavior_inference(self):
        adapter = MatterContextAdapter("motion_01", signal="occupancy")
        observed = adapter.translate("on", NOW, NOW)
        self.assertEqual(observed["event_type"], "occupancy_state")
        self.assertEqual(observed["value"], "on")
        self.assertEqual(observed["source"], "matter")
        self.assertEqual(len(observed), 8)
        missing = adapter.translate("unavailable", NOW, NOW)
        self.assertEqual(missing["availability"], "offline")
        self.assertEqual(missing["quality"], "unknown")
        with self.assertRaises(ValueError):
            MatterContextAdapter("x", signal="cooking")

    def test_board_observation_and_inference_remain_distinct_raw_signals(self):
        temperature = MatterBoardSignalAdapter("cooker_01", kind="observation", signal="temperature")
        inference = MatterBoardSignalAdapter("cooker_01", kind="inference", signal="cooker_phase")
        measured = temperature.translate("41.5", NOW, NOW)
        estimated = inference.translate("heating", NOW, NOW)
        self.assertEqual(measured["event_type"], "board_observation_temperature")
        self.assertEqual(measured["value"], "41.5")
        self.assertEqual(estimated["event_type"], "board_inference_cooker_phase")
        self.assertEqual(estimated["value"], "heating")
        self.assertEqual(len(measured), 8)
        self.assertEqual(inference.translate("unknown", NOW, NOW)["availability"], "offline")
        with self.assertRaises(ValueError):
            MatterBoardSignalAdapter("cooker_01", kind="prediction", signal="cooker_phase")


if __name__ == "__main__":
    unittest.main()
