"""Export a reviewable candidate; synthetic results never auto-deploy to HA."""

from __future__ import annotations

import json
from pathlib import Path

from ai.calibration import experiment


def export_candidate(path: Path) -> dict[str, object]:
    result = experiment()
    cooker = result["variants"]["optimized_cooker"]
    candidate = {
        "schema_version": 1,
        "status": "offline_candidate_only",
        "features": ["open_drop", "delay_growth", "cook_open_mismatch", "inactivity_growth"],
        "weights": cooker["weights"][:4],
        "threshold": cooker["threshold"],
        "train_seed": 11,
        "holdout_seeds": result["holdout_seeds"],
        "warning": "Evaluated on synthetic situations only. Does not replace live conservative rules.",
    }
    path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    return candidate


if __name__ == "__main__":
    target = Path(__file__).parent / "model_config_candidate.json"
    export_candidate(target)
    print(target)
