from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tools.simulate_notification_flow import replay


class NotificationFlowDemoTests(TestCase):
    def test_simulated_caregiver_journey_survives_restart_without_duplicate(self):
        with TemporaryDirectory() as directory:
            result = replay(Path(directory) / "state.json")
        self.assertEqual(result["first_gap_requests"], 0)
        self.assertEqual(result["second_gap_requests"], 1)
        self.assertEqual(result["acknowledgement"], "accepted")
        self.assertEqual(result["after_resumed_use"], "resolved")
        self.assertEqual(result["after_restart"], "resolved")
        self.assertEqual(result["notification_requests"], 2)
        self.assertEqual(result["simulation_state_topic"],
                         "bapsim/simulation/v1/demo_household/state")
        self.assertFalse(result["real_push_sent"])
