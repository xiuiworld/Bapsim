from unittest import TestCase

from ai.condition_matrix_experiment import SETTINGS, household, score
from ai.robust_notification_experiment import HOLDOUT_VARIATION, loss


class RobustNotificationExperimentTests(TestCase):
    def test_perturbation_preserves_independent_scripted_labels(self):
        base = household(1601, "older_alternate", "gradual_drift", SETTINGS["current"])
        shifted = household(1601, "older_alternate", "gradual_drift",
                            SETTINGS["current"], variation=HOLDOUT_VARIATION)
        self.assertEqual([(row["day"], row["label"]) for row in base],
                         [(row["day"], row["label"]) for row in shifted])
        self.assertEqual(score(shifted, "notification_request")["alerted_away_or_offline"], 0)

    def test_worst_group_loss_penalizes_missed_and_nuisance_requests(self):
        self.assertGreater(loss({"recall": 0.5, "false_positive_rate": 0.1}),
                           loss({"recall": 0.5, "false_positive_rate": 0.05}))
