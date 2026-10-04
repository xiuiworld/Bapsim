"""Read only local HA smoke check; never print the API token or entity attributes."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
ENTITIES = ("sensor.bapsim_notification_delivery", "sensor.bapsim_observation_issue_status",
            "sensor.bapsim_workflow_status")


def token() -> str:
    from_environment = os.environ.get("BAPSIM_HA_TOKEN")
    if from_environment:
        return from_environment
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("BAPSIM_HA_TOKEN="):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError("BAPSIM_HA_TOKEN is not configured")


def main() -> None:
    secret = token()
    for entity_id in ENTITIES:
        request = Request(f"http://127.0.0.1:8123/api/states/{entity_id}",
                          headers={"Authorization": f"Bearer {secret}"})
        with urlopen(request, timeout=10) as response:
            data = json.load(response)
            print(f"{response.status} {entity_id}={data.get('state')}")


if __name__ == "__main__":
    main()
