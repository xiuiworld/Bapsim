"""Audit recent-window slot locking against the 192-home precision baseline."""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ai.condition_matrix_experiment import household
from ai.in_scope_hybrid_experiment import BASE
from ai.in_scope_sample_audit import RHYTHMS, TARGET_CONDITIONS, VARIATIONS, _summary
from domain.personal_model import OPERATING_WINDOW_SETTINGS


BASELINE_PATH = Path(__file__).with_name("in_scope_precision_audit_result.json")
CANDIDATES = {
    "ungated": {**BASE, "window_history_days": 10, "historical_slot_lock": True},
    "gated": {**BASE, **OPERATING_WINDOW_SETTINGS},
}


def _one_home(spec: tuple[int, str, str, str]) -> list[dict[str, object]]:
    seed, rhythm, condition, candidate = spec
    return household(seed, rhythm, condition, CANDIDATES[candidate],
                     variation=VARIATIONS[condition], opportunity_interruptions=True,
                     runtime_ticks=True, tick_minutes=1, future_days=14)


def experiment(candidate: str) -> dict[str, object]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    if (baseline["setting"] != "current" or baseline["tick_minutes"] != 1 or
            not baseline["precision_met"] or
            tuple(baseline["conditions"]) != TARGET_CONDITIONS or
            tuple(baseline["rhythms"]) != RHYTHMS or
            baseline["variations"] != {name: VARIATIONS[name]
                                       for name in TARGET_CONDITIONS}):
        raise ValueError("precision baseline is incompatible")
    seeds = tuple(baseline["seeds_used"])
    specs = [(seed, rhythm, condition, candidate)
             for condition in TARGET_CONDITIONS for seed in seeds for rhythm in RHYTHMS]
    homes = []
    with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
        for index, rows in enumerate(pool.map(_one_home, specs), 1):
            homes.append(rows)
            if index % 16 == 0:
                print(f"{candidate} slot lock: {index}/{len(specs)} homes", flush=True)
    target = _summary(homes)
    if (target["households"] != baseline["target"]["households"] or
            target["episodes"]["episodes"] != baseline["target"]["episodes"]["episodes"] or
            target["nuisance_normal_days"]["normal_days"] !=
            baseline["target"]["nuisance_normal_days"]["normal_days"]):
        raise ValueError("candidate and baseline denominators differ")
    by_condition = {condition: _summary([rows for rows in homes
                                          if rows[0]["condition"] == condition])
                    for condition in TARGET_CONDITIONS}
    caught = target["episodes"]["caught"]
    nuisance = target["nuisance_normal_days"]["days_with_unrelated_request"]
    base_caught = baseline["target"]["episodes"]["caught"]
    base_nuisance = baseline["target"]["nuisance_normal_days"]["days_with_unrelated_request"]
    no_condition_regression = all(
        by_condition[condition]["episodes"]["caught"] >=
        baseline["by_condition"][condition]["episodes"]["caught"] and
        by_condition[condition]["nuisance_normal_days"]["days_with_unrelated_request"] <=
        baseline["by_condition"][condition]["nuisance_normal_days"]["days_with_unrelated_request"]
        for condition in TARGET_CONDITIONS)
    return {"baseline_result": BASELINE_PATH.name, "candidate_name": candidate,
            "seeds": seeds,
            "conditions": TARGET_CONDITIONS, "rhythms": RHYTHMS, "tick_minutes": 1,
            "candidate_setting": CANDIDATES[candidate],
            "baseline": {"target": baseline["target"], "by_condition": baseline["by_condition"]},
            "candidate": {"target": target, "by_condition": by_condition},
            "adoptable": (caught >= base_caught and nuisance <= base_nuisance and
                          (caught > base_caught or nuisance < base_nuisance) and
                          no_condition_regression)}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) == 2 else "gated"
    if name not in CANDIDATES:
        raise ValueError("candidate must be ungated or gated")
    result = experiment(name)
    path = Path(__file__).with_name(
        "in_scope_slot_lock_audit_result.json" if name == "ungated"
        else "in_scope_drift_gate_audit_result.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"caught": result["candidate"]["target"]["episodes"]["caught"],
                      "nuisance_days": result["candidate"]["target"]
                      ["nuisance_normal_days"]["days_with_unrelated_request"],
                      "adoptable": result["adoptable"]}))
