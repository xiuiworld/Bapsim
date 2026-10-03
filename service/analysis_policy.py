"""Shared personal-assessment order for the HA host and MQTT worker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class PersonalModel(Protocol):
    def evaluate(self, source: str, device_id: str, as_of: str, *, online: bool,
                 away: bool) -> list[dict[str, object]]: ...
    def evaluate_interval(self, source: str, device_id: str, as_of: str, *, online: bool,
                          away: bool) -> dict[str, object] | None: ...
    def evaluate_trend(self, source: str, device_id: str, as_of: str, *, online: bool,
                       away: bool) -> dict[str, object] | None: ...


@dataclass(frozen=True)
class PersonalAssessments:
    windows: tuple[dict[str, object], ...]
    interval: dict[str, object] | None
    trend: dict[str, object] | None


def assess_personal_use(model: PersonalModel, source: str, device_id: str,
                        as_of: str, *, online: bool, away: bool) -> PersonalAssessments:
    """Run the three model assessments in the same order in both hosts."""
    windows = tuple(model.evaluate(source, device_id, as_of, online=online, away=away))
    interval = model.evaluate_interval(source, device_id, as_of, online=online, away=away)
    trend = model.evaluate_trend(source, device_id, as_of, online=online, away=away)
    return PersonalAssessments(windows, interval, trend)
