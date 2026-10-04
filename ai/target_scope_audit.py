"""Replay in-scope synthetic households without sparse batch-cooking profiles.

The scripted labels are not meal or real-world caregiver-check outcomes.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai.condition_matrix_experiment import SETTINGS, household, score
from ai.episode_evaluation import episode_score
from ai.robust_notification_experiment import (
    HOLDOUT_CONDITIONS, HOLDOUT_SEEDS, HOLDOUT_VARIATION,
)


# Synthetic 65+ households that open the cooker daily in their baseline.
# Alternating/batch cooking and 50-64 profiles remain stress tests only.
TARGET_RHYTHMS = ("older_morning", "older_twice")


def experiment() -> dict[str, object]:
    rows = [row for condition in HOLDOUT_CONDITIONS
            for seed in HOLDOUT_SEEDS for rhythm in TARGET_RHYTHMS
            for row in household(seed, rhythm, condition, SETTINGS["current"],
                                 variation=HOLDOUT_VARIATION)]
    return {
        "scope": "Synthetic daily cooker access, age 65+; not meals or field accuracy",
        "excluded_profiles": ["middle_evening (age 50-64)",
                              "older_alternate (every-other-day batch cooking)"],
        "seeds": HOLDOUT_SEEDS,
        "conditions": HOLDOUT_CONDITIONS,
        "variation": HOLDOUT_VARIATION,
        "notification_days": score(rows, "notification_request"),
        "episodes": episode_score(rows),
        "by_rhythm": {
            rhythm: {"notification_days": score(
                [row for row in rows if row["rhythm"] == rhythm], "notification_request"),
                     "episodes": episode_score(
                         [row for row in rows if row["rhythm"] == rhythm])}
            for rhythm in TARGET_RHYTHMS},
    }


if __name__ == "__main__":
    result = experiment()
    target = Path(__file__).with_name("target_scope_audit_result.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"notification_days": result["notification_days"],
                      "episodes": result["episodes"]}, ensure_ascii=False))
