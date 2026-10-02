"""Generate labeled situations independently from candidate score weights."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from random import Random


SCENARIOS = ("normal", "normal", "normal", "mismatch", "gradual", "acute", "away", "communication_failure", "sensor_noise")


@dataclass(frozen=True)
class Case:
    scenario: str
    label: int | None
    open_ratio: float | None
    delay_ratio: float | None
    cook_ratio: float | None
    hours_ratio: float | None
    data_available: bool
    away: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def population(seed: int, size: int = 500, *, variability: float = 0.2) -> list[Case]:
    if size < 1 or variability < 0:
        raise ValueError("invalid population settings")
    rng = Random(seed)
    cases: list[Case] = []
    for index in range(size):
        scenario = SCENARIOS[index % len(SCENARIOS)]
        def jitter(value: float) -> float:
            return max(0.0, rng.gauss(value, variability))
        open_ratio, delay_ratio, cook_ratio, hours_ratio = (
            jitter(1), jitter(1), jitter(1), jitter(1)
        )
        if scenario == "mismatch":
            open_ratio, delay_ratio, cook_ratio = jitter(0.35), jitter(2.4), jitter(1)
        elif scenario == "gradual":
            open_ratio, cook_ratio, hours_ratio = jitter(0.5), jitter(0.55), jitter(1.6)
        elif scenario == "acute":
            open_ratio, hours_ratio = jitter(0.05), jitter(3)
        elif scenario == "sensor_noise":
            open_ratio = jitter(0.1)
        cases.append(Case(
            scenario=scenario,
            label=1 if scenario in {"mismatch", "gradual", "acute"} else 0 if scenario in {"normal", "sensor_noise"} else None,
            open_ratio=open_ratio if scenario != "communication_failure" else None,
            delay_ratio=delay_ratio if scenario != "communication_failure" else None,
            cook_ratio=cook_ratio if scenario != "communication_failure" else None,
            hours_ratio=hours_ratio if scenario != "communication_failure" else None,
            data_available=scenario != "communication_failure",
            away=scenario == "away",
        ))
    rng.shuffle(cases)
    return cases
