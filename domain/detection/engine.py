"""Conservative initial rules for routine changes, prior to calibration.

These outputs describe observed cooker behavior. They do not diagnose health or
prove that someone ate a meal.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping

from ..baseline import BaselineSnapshot


@dataclass(frozen=True)
class DetectionResult:
    device_id: str
    source: str
    as_of: str
    routine_status: str
    severity: str
    reason_codes: tuple[str, ...]
    score: float | None
    evidence: dict[str, float | None]

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["reason_codes"] = list(self.reason_codes)
        return result


class RuleDetector:
    def __init__(self, threshold: float = 0.6) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold

    @staticmethod
    def _number(features: Mapping[str, object], name: str) -> float | None:
        value = features.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @staticmethod
    def _ratio(value: float | None, baseline: float | None) -> float | None:
        return value / baseline if value is not None and baseline is not None and baseline > 0 else None

    def detect(
        self,
        features: Mapping[str, object],
        baseline: BaselineSnapshot,
        *,
        away: bool = False,
    ) -> DetectionResult:
        source = str(features["source"])
        device_id = str(features["device_id"])
        as_of = str(features["as_of"])

        def result(status: str, severity: str, reasons: tuple[str, ...], score: float | None = None,
                   evidence: dict[str, float | None] | None = None) -> DetectionResult:
            return DetectionResult(device_id, source, as_of, status, severity, reasons, score, evidence or {})

        if features.get("availability") != "online" or features.get("has_data_gap"):
            return result("data_unavailable", "info", ("COOKER_DATA_UNAVAILABLE",))
        if away:
            return result("away", "info", ("AWAY_DECLARED",))
        if not baseline.ready:
            return result("learning", "info", ("BASELINE_INSUFFICIENT",))

        cook = self._number(features, "cook_count_24h")
        opens = self._number(features, "lid_open_count_24h")
        hours = self._number(features, "hours_since_last_open")
        delay = self._number(features, "latest_first_open_delay_min")
        if delay is None:
            delay = self._number(features, "median_first_open_delay_min")
        cook_base = baseline.metrics.get("cook_count_24h")
        open_base = baseline.metrics.get("lid_open_count_24h")
        delay_base = baseline.metrics.get("median_first_open_delay_min")
        hours_base = baseline.metrics.get("hours_since_last_open")
        if cook is None or opens is None or cook_base is None or open_base is None:
            return result("data_unavailable", "info", ("COOKER_DATA_UNAVAILABLE",))

        cook_ratio = self._ratio(cook, cook_base.median)
        open_ratio = self._ratio(opens, open_base.median)
        delay_ratio = self._ratio(delay, delay_base.median if delay_base else None)
        pending_delay = self._number(features, "pending_first_open_delay_min")
        if pending_delay is not None:
            delay_ratio = self._ratio(pending_delay, delay_base.median if delay_base else None)
        hours_ratio = self._ratio(hours, hours_base.median if hours_base else None)
        evidence = {
            "cook_count_ratio": cook_ratio,
            "lid_open_count_ratio": open_ratio,
            "first_open_delay_ratio": delay_ratio,
            "hours_since_last_open_ratio": hours_ratio,
        }

        waiting_for_first_open = pending_delay is not None
        cooking_in_progress = features.get("cooking_in_progress") is True
        wait_is_long = delay_ratio is not None and delay_ratio >= 2
        open_drop_allowed = not cooking_in_progress and (not waiting_for_first_open or wait_is_long)
        open_drop = max(0.0, min(1.0, 1 - open_ratio)) if open_ratio is not None and open_drop_allowed else 0.0
        delay_growth = max(0.0, min(1.0, (delay_ratio - 1) / 2)) if delay_ratio is not None else 0.0
        mismatch = not cooking_in_progress and cook_ratio is not None and cook_ratio >= 0.75 and (
            (open_drop_allowed and open_ratio is not None and open_ratio <= 0.5) or wait_is_long
        )
        score = min(1.0, 0.4 * open_drop + 0.3 * delay_growth + 0.3 * float(mismatch))
        if mismatch:
            score = max(score, self.threshold)
        reasons: list[str] = []
        if mismatch:
            reasons.append("COOK_OPEN_MISMATCH")
        if open_drop_allowed and open_ratio is not None and open_ratio <= 0.5:
            reasons.append("LID_ACTIVITY_DECREASED")
        if not cooking_in_progress and wait_is_long:
            reasons.append("FIRST_OPEN_DELAY_HIGH")
        if not cooking_in_progress and opens == 0 and open_base.median >= 1 and hours is not None:
            inactivity_limit = max(24.0, (hours_base.median * 2) if hours_base else 24.0)
            if hours >= inactivity_limit:
                reasons.append("ACUTE_INACTIVITY")
                score = max(score, self.threshold)
        if (
            open_ratio is not None and cook_ratio is not None
            and open_ratio < 0.7 and cook_ratio < 0.7
            and baseline.open_count_trend_per_day is not None
            and baseline.open_count_trend_per_day <= -0.2
        ):
            reasons.append("GRADUAL_DECLINE")
            score = max(score, self.threshold)
        if reasons and score >= self.threshold:
            return result("check_required", "warning", tuple(reasons), round(score, 4), evidence)
        return result("normal", "none", (), round(score, 4), evidence)
