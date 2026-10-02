"""Cooker-only offline grid search with independent synthetic holdouts."""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path
from typing import Callable

from ai.synthetic import Case, population
from domain.baseline import BaselineSnapshot, MetricStats
from domain.detection import RuleDetector


def components(case: Case) -> tuple[float, float, float, float]:
    open_ratio = case.open_ratio if case.open_ratio is not None else 0.0
    delay_ratio = case.delay_ratio if case.delay_ratio is not None else 1.0
    cook_ratio = case.cook_ratio if case.cook_ratio is not None else 0.0
    hours_ratio = case.hours_ratio if case.hours_ratio is not None else 1.0
    opened = max(0.0, min(1.0, 1 - open_ratio))
    delay = max(0.0, min(1.0, (delay_ratio - 1) / 2))
    mismatch = float(cook_ratio >= 0.75 and (open_ratio <= 0.5 or delay_ratio >= 2))
    inactivity = max(0.0, min(1.0, (hours_ratio - 1) / 2))
    return opened, delay, mismatch, inactivity


def predict(case: Case, weights: tuple[float, ...], threshold: float) -> int | None:
    if case.away or not case.data_available:
        return None
    values = components(case)
    score = sum(w * v for w, v in zip(weights, values))
    return int(score >= threshold)


def _measure(cases: list[Case], predictor: Callable[[Case], int | None]) -> dict[str, float | int]:
    tp = fp = tn = fn = 0
    for case in cases:
        prediction = predictor(case)
        if case.label is None or prediction is None:
            continue
        if case.label and prediction:
            tp += 1
        elif not case.label and prediction:
            fp += 1
        elif case.label:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
        "false_negative_rate": round(fn / (fn + tp), 4) if fn + tp else 0.0,
    }


def metrics(cases: list[Case], weights: tuple[float, ...], threshold: float) -> dict[str, float | int]:
    return _measure(cases, lambda case: predict(case, weights, threshold))


def live_predict(case: Case) -> int | None:
    """Run the actual HA RuleDetector on a synthetic feature/baseline pair."""
    if case.away or not case.data_available:
        return None
    metrics_by_name = {
        "cook_count_24h": MetricStats(2.0, 2.0, 2.0, 7),
        "lid_open_count_24h": MetricStats(2.0, 2.0, 2.0, 7),
        "median_first_open_delay_min": MetricStats(30.0, 30.0, 30.0, 7),
        "hours_since_last_open": MetricStats(12.0, 12.0, 12.0, 7),
    }
    baseline = BaselineSnapshot(
        device_id="synthetic_cooker", source="synthetic", as_of="2026-10-02T00:00:00+00:00",
        sample_days=7, ready=True, metrics=metrics_by_name,
        open_count_trend_per_day=-0.3 if case.scenario == "gradual" else 0.0,
    )
    feature = {
        "device_id": "synthetic_cooker", "source": "synthetic", "as_of": baseline.as_of,
        "availability": "online", "has_data_gap": False,
        "cook_count_24h": round(2 * case.cook_ratio) if case.cook_ratio is not None else None,
        "lid_open_count_24h": round(2 * case.open_ratio) if case.open_ratio is not None else None,
        "hours_since_last_open": 12 * case.hours_ratio if case.hours_ratio is not None else None,
        "latest_first_open_delay_min": 30 * case.delay_ratio if case.delay_ratio is not None else None,
        "median_first_open_delay_min": 30 * case.delay_ratio if case.delay_ratio is not None else None,
    }
    result = RuleDetector().detect(feature, baseline)
    return int(result.routine_status == "check_required")


def live_metrics(cases: list[Case]) -> dict[str, float | int]:
    return _measure(cases, live_predict)


def calibrate(train: list[Case]) -> tuple[tuple[float, ...], float]:
    best: tuple[float, tuple[float, ...], float] | None = None
    for a, b, c, d in product((0.1, 0.2, 0.3, 0.4), repeat=4):
        if abs(a + b + c + d - 1) > 1e-8:
            continue
        weights = (a, b, c, d)
        for threshold in (0.3, 0.4, 0.5, 0.6):
            result = metrics(train, weights, threshold)
            objective = result["f1"] - 0.2 * result["false_positive_rate"]
            candidate = (objective, weights, threshold)
            if best is None or candidate > best:
                best = candidate
    assert best is not None
    return best[1], best[2]


def experiment() -> dict[str, object]:
    train = population(11, 1800)
    holdouts = [population(seed, 900, variability=variance)
                for seed, variance in ((31, 0.2), (47, 0.35), (73, 0.4))]
    cooker = calibrate(train)
    return {
        "scope": "Synthetic scenarios only; no claim about real household or health outcomes.",
        "scenario_labels": "Situation definitions, independent of score weights.",
        "train_seeds": [11], "holdout_seeds": [31, 47, 73],
        "variants": {
            "live_rule": {"weights": None, "threshold": None,
                          "holdouts": [live_metrics(cases) for cases in holdouts]},
            "optimized_cooker": {"weights": cooker[0], "threshold": cooker[1],
                                  "holdouts": [metrics(cases, *cooker) for cases in holdouts]},
        },
    }


if __name__ == "__main__":
    target = Path(__file__).parent / "calibration_result.json"
    target.write_text(json.dumps(experiment(), ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)
