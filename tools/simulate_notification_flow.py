"""Replay a synthetic caregiver-alert journey without HA, MQTT, or a real phone."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from service.standalone import AnalysisService


KST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 1, tzinfo=KST)
SOURCE = "simulator"
DEVICE = "demo_cooker"


def at(day: int, hour: int = 0, minute: int = 0) -> str:
    return (START + timedelta(days=day, hours=hour, minutes=minute)).isoformat()


def observation(day: int, hour: int, kind: str) -> dict[str, str]:
    return {"source": SOURCE, "device_id": DEVICE, "event_type": kind,
            "observed_at": at(day, hour), "availability": "online"}


def replay(state_file: Path) -> dict[str, object]:
    published: list[tuple[str, str, bool]] = []
    requested = []
    service = AnalysisService(
        "demo_household", state_file,
        lambda topic, payload, retain: published.append((topic, payload, retain)),
        lambda update: requested.append(update) or True,
        quiet_start="00:00", quiet_end="00:01",
    )
    service.process_event(observation(0, 0, "device_online"), at(0))
    for day in range(7):
        service.process_event(observation(day, 13, "lid_opened"), at(day, 13))
        service.evaluate(SOURCE, DEVICE, at(day, 15, 30))

    service.evaluate(SOURCE, DEVICE, at(7, 15, 30))
    first_gap_requests = len(requested)
    service.evaluate(SOURCE, DEVICE, at(8, 15, 30))
    if first_gap_requests != 0 or len(requested) != 1:
        raise RuntimeError("expected exactly one request after the second missed window")
    incident_id = requested[0].incident_id
    if incident_id is None:
        raise RuntimeError("notification request has no incident ID")
    command = {"schema_version": "0.1.0", "household_id": "demo_household",
               "command_id": "demo-ack-1", "action": "acknowledge",
               "assessment_id": incident_id, "created_at": at(8, 15, 31),
               "ends_at": at(9, 15, 31)}
    acknowledgement = service.process_control(command, at(8, 15, 31))
    if acknowledgement["status"] != "accepted":
        raise RuntimeError("caregiver acknowledgement was rejected")
    service.process_event(observation(8, 16, "lid_opened"), at(8, 16))
    gap = next(item for item in service.gap.dump_state()
               if item["source"] == SOURCE and item["device_id"] == DEVICE)
    if gap["status"] != "resolved":
        raise RuntimeError("resumed use did not resolve the synthetic incident")

    restored = AnalysisService(
        "demo_household", state_file, lambda *args: None,
        lambda update: requested.append(update) or True,
        quiet_start="00:00", quiet_end="00:01",
    )
    restored_gap = next(item for item in restored.gap.dump_state()
                        if item["source"] == SOURCE and item["device_id"] == DEVICE)
    if restored_gap["status"] != "resolved" or len(requested) != 1:
        raise RuntimeError("restart changed the resolved incident or repeated notification")
    states = [json.loads(payload) for topic, payload, _ in published
              if topic.endswith("/state")]
    return {
        "mode": "simulation", "household_id": "demo_household",
        "learned_days": 7, "first_gap_requests": first_gap_requests,
        "second_gap_requests": 1, "incident_id": incident_id,
        "acknowledgement": acknowledgement["status"],
        "after_resumed_use": gap["status"],
        "after_restart": restored_gap["status"],
        "notification_requests": len(requested),
        "simulation_state_topic": "bapsim/simulation/v1/demo_household/state",
        "state_messages": len(states),
        "real_push_sent": False,
    }


def main() -> None:
    with TemporaryDirectory(prefix="bapsim-alert-demo-") as directory:
        print(json.dumps(replay(Path(directory) / "state.json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
