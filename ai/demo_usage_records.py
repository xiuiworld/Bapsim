"""Write clearly marked simulated observations and independent scenario labels."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path

from ai.usage_record_pipeline import KST, SCHEMA, evaluate


START = datetime(2026, 1, 5, tzinfo=KST)
HOMES = {"demo_morning": (8,), "demo_twice": (8, 19)}
TOTAL_DAYS = 35
EVALUATION_START = (START + timedelta(days=21)).date()
GAP_DAYS = {24, 25, 31}
NATURAL_SKIP_DAYS = {22, 33}
AWAY_DAY = 28
OFFLINE_DAY = 30


def generate() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    observations: list[dict[str, object]] = []
    labels: list[dict[str, object]] = []
    for household, hours in HOMES.items():
        device = "cooker_01"
        common = {"schema_version": SCHEMA, "mode": "simulation", "household_id": household,
                  "device_id": device, "source": "simulator"}
        for index in range(TOTAL_DAYS):
            start = START + timedelta(days=index)
            away, offline = index == AWAY_DAY, index == OFFLINE_DAY
            if index in {AWAY_DAY, AWAY_DAY + 1}:
                observations.append({**common, "event_type": "away_state",
                                     "observed_at": start.isoformat(), "away": away})
            if offline:
                observations.append({**common, "event_type": "device_offline",
                                     "observed_at": start.isoformat(), "availability": "offline"})
            else:
                for hour in range(24):
                    observations.append({**common, "event_type": "device_online",
                                         "observed_at": (start + timedelta(hours=hour)).isoformat(),
                                         "availability": "online"})
            if not (offline or away or index in GAP_DAYS):
                for hour in hours[:-1] if index in NATURAL_SKIP_DAYS else hours:
                    observations.append({**common, "event_type": "lid_opened",
                                         "observed_at": (start + timedelta(hours=hour, minutes=5)).isoformat(),
                                         "availability": "online"})
            if index >= 21:
                unknown = away or offline
                labels.append({**common, "local_date": start.date().isoformat(),
                               "review": "unknown" if unknown else
                                         "check_needed" if index in GAP_DAYS else "no_check_needed",
                               "basis": "unreviewed" if unknown else "scripted_scenario"})
    observations.sort(key=lambda row: (row["household_id"], row["observed_at"], row["event_type"]))
    return observations, labels


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create simulator-only evaluation inputs")
    parser.add_argument("--output-dir", type=Path, default=Path("local-data/usage-demo"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    observations, labels = generate()
    write_jsonl(args.output_dir / "observations.jsonl", observations)
    write_jsonl(args.output_dir / "labels.jsonl", labels)
    report = evaluate(observations, labels, EVALUATION_START)
    with (args.output_dir / "report.json").open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(f"{args.output_dir}: {len(observations)} simulated observations, "
          f"{len(labels)} independent scenario labels; "
          f"{report['personal']['eligible_days']} eligible days")


if __name__ == "__main__":
    main()
