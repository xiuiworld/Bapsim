"""Synthetic domain randomization and worst-group notification selection.

This adapts published validation ideas; it does not estimate field accuracy.
Every household is replayed forward in time with independent scripted labels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ai.condition_matrix_experiment import SETTINGS, RHYTHMS, household, score


TRAIN_SEEDS = (113, 127)
TRAIN_CONDITIONS = ("stable", "natural_skips")
HOLDOUT_SEEDS = (1601, 1613)
HOLDOUT_CONDITIONS = ("gradual_drift", "skip_and_late")
TRAIN_VARIATIONS = (
    {"name": "base"},
    {"name": "behavior_shift", "skip_delta": 0.15, "jitter_minutes": 75, "shift_hours": 0.75},
    {"name": "missed_open", "missed_open_probability": 0.08},
)
HOLDOUT_VARIATION = {"name": "combined_unseen", "skip_delta": 0.12,
                     "jitter_minutes": 65, "shift_hours": 0.5,
                     "missed_open_probability": 0.06}
CANDIDATES = ("current", "higher_threshold", "grace_and_cadence", "single_high_confidence")


def cohort(setting_name: str, seeds: tuple[int, ...], conditions: tuple[str, ...],
           variations: tuple[Mapping[str, float | str], ...]) -> list[dict[str, object]]:
    setting = SETTINGS[setting_name]
    return [row for variation in variations for condition in conditions
            for seed in seeds for rhythm in RHYTHMS
            for row in household(seed, rhythm, condition, setting, variation=variation)]


def groups(rows: list[dict[str, object]]) -> dict[str, dict[str, int | float]]:
    return {f"{variation}/{rhythm}": score(
        [row for row in rows if row["variation"] == variation and row["rhythm"] == rhythm],
        "notification_request")
        for variation in sorted({str(row["variation"]) for row in rows})
        for rhythm in RHYTHMS}


def loss(metrics: dict[str, int | float]) -> float:
    """One missed-script unit plus two nuisance-request units."""
    return round(1 - float(metrics["recall"]) + 2 * float(metrics["false_positive_rate"]), 6)


def experiment() -> dict[str, object]:
    training = {}
    for name in CANDIDATES:
        rows = cohort(name, TRAIN_SEEDS, TRAIN_CONDITIONS, TRAIN_VARIATIONS)
        per_group = groups(rows)
        training[name] = {"overall": score(rows, "notification_request"),
                          "worst_group_loss": max(loss(item) for item in per_group.values()),
                          "worst_groups": sorted(per_group, key=lambda group: -loss(per_group[group]))[:3],
                          "by_group": per_group}
    selected = min(CANDIDATES, key=lambda name: (
        training[name]["worst_group_loss"], loss(training[name]["overall"]),
        name != "current"))
    current_rows = cohort("current", HOLDOUT_SEEDS, HOLDOUT_CONDITIONS,
                          (HOLDOUT_VARIATION,))
    selected_rows = (current_rows if selected == "current" else
                     cohort(selected, HOLDOUT_SEEDS, HOLDOUT_CONDITIONS,
                            (HOLDOUT_VARIATION,)))
    return {
        "scope": "Synthetic cooker-use interruption labels, not meals, health or field accuracy",
        "method": "domain-randomized scenarios; forward-time replay; worst-group loss selection",
        "loss": "1 - scripted-interruption-day recall + 2 * normal-day notification-request rate",
        "train_seeds": TRAIN_SEEDS, "train_conditions": TRAIN_CONDITIONS,
        "train_variations": TRAIN_VARIATIONS, "holdout_seeds": HOLDOUT_SEEDS,
        "holdout_conditions": HOLDOUT_CONDITIONS,
        "holdout_variation": HOLDOUT_VARIATION, "candidates": CANDIDATES,
        "training": training, "selected": selected,
        "selected_settings": SETTINGS[selected],
        "holdout_current": score(current_rows, "notification_request"),
        "holdout_selected": score(selected_rows, "notification_request"),
        "holdout_by_rhythm_current": {rhythm: score(
            [row for row in current_rows if row["rhythm"] == rhythm], "notification_request")
            for rhythm in RHYTHMS},
        "holdout_by_rhythm_selected": {rhythm: score(
            [row for row in selected_rows if row["rhythm"] == rhythm], "notification_request")
            for rhythm in RHYTHMS},
    }


if __name__ == "__main__":
    target = Path(__file__).with_name("robust_notification_result.json")
    result = experiment()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"selected": result["selected"],
                      "holdout_current": result["holdout_current"],
                      "holdout_selected": result["holdout_selected"]}, ensure_ascii=False))
