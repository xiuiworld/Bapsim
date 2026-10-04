"""Grow independent target households until synthetic estimates are precise enough."""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ai.in_scope_sample_audit import RHYTHMS, TARGET_CONDITIONS, VARIATIONS, _one_home, _summary


SEEDS = tuple(12001 + 19 * index for index in range(64))
BATCH_SEEDS = 16
MIN_HOUSEHOLDS = 128
TARGET_RECALL_WIDTH = 0.06  # +/- 3 percentage points
CONDITION_RECALL_WIDTH = 0.10  # +/- 5 percentage points
TARGET_NUISANCE_WIDTH = 0.04  # +/- 2 percentage points


def _width(bounds: list[float]) -> float:
    return bounds[1] - bounds[0]


def _summaries(homes: list[list[dict[str, object]]]) -> dict[str, object]:
    by_condition = {condition: _summary([rows for rows in homes
                                          if rows[0]["condition"] == condition])
                    for condition in TARGET_CONDITIONS}
    target = _summary(homes)
    intervals = target["household_bootstrap_95pct"]
    precision_met = (len(homes) >= MIN_HOUSEHOLDS and
                     _width(intervals["recall"]) <= TARGET_RECALL_WIDTH and
                     _width(intervals["nuisance_day_rate"]) <= TARGET_NUISANCE_WIDTH and
                     all(_width(value["household_bootstrap_95pct"]["recall"]) <=
                         CONDITION_RECALL_WIDTH for value in by_condition.values()))
    return {"target": target, "by_condition": by_condition,
            "precision_met": precision_met}


def experiment() -> dict[str, object]:
    homes: list[list[dict[str, object]]] = []
    history = []
    with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
        for offset in range(0, len(SEEDS), BATCH_SEEDS):
            batch = SEEDS[offset:offset + BATCH_SEEDS]
            specs = [(seed, rhythm, condition)
                     for condition in TARGET_CONDITIONS for seed in batch for rhythm in RHYTHMS]
            for index, rows in enumerate(pool.map(_one_home, specs), 1):
                homes.append(rows)
                if index % 16 == 0:
                    print(f"batch {offset // BATCH_SEEDS + 1}: {index}/{len(specs)} homes", flush=True)
            summary = _summaries(homes)
            record = {"households": len(homes),
                      "episodes": summary["target"]["episodes"]["episodes"],
                      "caught": summary["target"]["episodes"]["caught"],
                      "recall_interval": summary["target"]["household_bootstrap_95pct"]["recall"],
                      "nuisance_interval": summary["target"]["household_bootstrap_95pct"]["nuisance_day_rate"],
                      "precision_met": summary["precision_met"]}
            history.append(record)
            print(json.dumps(record), flush=True)
            if summary["precision_met"]:
                break
    return {"scope": "Synthetic age-65-plus daily cooker-use opportunities",
            "setting": "current", "seeds_used": SEEDS[:len(homes) //
                                                   (len(TARGET_CONDITIONS) * len(RHYTHMS))],
            "rhythms": RHYTHMS, "conditions": TARGET_CONDITIONS,
            "variations": {key: VARIATIONS[key] for key in TARGET_CONDITIONS},
            "tick_minutes": 1, "batch_seeds": BATCH_SEEDS,
            "stopping_criteria": {"min_households": MIN_HOUSEHOLDS,
                                  "target_recall_95pct_width": TARGET_RECALL_WIDTH,
                                  "condition_recall_95pct_width": CONDITION_RECALL_WIDTH,
                                  "target_nuisance_95pct_width": TARGET_NUISANCE_WIDTH},
            "history": history, **summary}


if __name__ == "__main__":
    result = experiment()
    target = Path(__file__).with_name("in_scope_precision_audit_result.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"households": result["target"]["households"],
                      "caught": result["target"]["episodes"]["caught"],
                      "episodes": result["target"]["episodes"]["episodes"],
                      "precision_met": result["precision_met"]}, ensure_ascii=False))
