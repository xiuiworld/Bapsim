"""Rolling, per-person baseline from eligible daily feature snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Mapping
from zoneinfo import ZoneInfo


METRICS = (
    "cook_count_24h",
    "lid_open_count_24h",
    "hours_since_last_open",
    "median_first_open_delay_min",
    "cook_interval_hours",
)


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = int(position)
    weight = position - left
    return ordered[left] * (1 - weight) + ordered[min(left + 1, len(ordered) - 1)] * weight


@dataclass(frozen=True)
class MetricStats:
    median: float
    q25: float
    q75: float
    samples: int


@dataclass(frozen=True)
class BaselineSnapshot:
    device_id: str
    source: str
    as_of: str
    sample_days: int
    ready: bool
    metrics: dict[str, MetricStats]
    open_count_trend_per_day: float | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class PersonalBaselineEngine:
    def __init__(self, window_days: int = 14, min_days: int = 7, timezone_name: str = "Asia/Seoul") -> None:
        if window_days < 1 or min_days < 1 or min_days > window_days:
            raise ValueError("invalid baseline window")
        self.window_days = window_days
        self.min_days = min_days
        # KST has no daylight-saving transitions in the project's time range.
        # A fixed offset also works with the standard Windows Python install,
        # which does not bundle the IANA timezone database.
        self.timezone = timezone(timedelta(hours=9)) if timezone_name == "Asia/Seoul" else ZoneInfo(timezone_name)
        self._days: dict[tuple[str, str], dict[date, dict[str, float | None]]] = {}

    def dump_state(self) -> list[dict[str, object]]:
        return [
            {"source": source, "device_id": device_id, "day": day.isoformat(), "values": values}
            for (source, device_id), days in self._days.items()
            for day, values in days.items()
        ]

    def load_state(self, stored: list[dict[str, object]]) -> None:
        self._days.clear()
        for item in stored:
            source = str(item["source"])
            device_id = str(item["device_id"])
            day = date.fromisoformat(str(item["day"]))
            values = item["values"]
            if not isinstance(values, dict):
                raise ValueError("invalid stored baseline values")
            self._days.setdefault((source, device_id), {})[day] = values

    def _day(self, as_of: str) -> date:
        observed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("baseline time must include a timezone")
        return observed.astimezone(self.timezone).date()

    def record(self, features: Mapping[str, object], status: str) -> bool:
        """Keep one latest normal sample per local day; abnormal days are removed."""
        source = features.get("source")
        device_id = features.get("device_id")
        as_of = features.get("as_of")
        if not all(isinstance(item, str) and item for item in (source, device_id, as_of)):
            raise ValueError("features are missing source, device_id, or as_of")
        day = self._day(as_of)
        key = (source, device_id)
        samples = self._days.setdefault(key, {})
        if status == "check_required":
            samples.pop(day, None)
            return False
        if status not in {"learning", "normal"}:
            return False
        if features.get("availability") != "online" or features.get("has_data_gap"):
            return False
        numeric: dict[str, float | None] = {}
        for name in METRICS:
            value = features.get(name)
            if value is None:
                numeric[name] = None
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                numeric[name] = float(value)
            else:
                raise ValueError(f"invalid feature: {name}")
        samples[day] = numeric
        cutoff = day - timedelta(days=self.window_days)
        for old in tuple(samples):
            if old < cutoff:
                del samples[old]
        return True

    def snapshot(self, source: str, device_id: str, as_of: str) -> BaselineSnapshot:
        today = self._day(as_of)
        cutoff = today - timedelta(days=self.window_days)
        # Exclude today's potentially changing observations from comparison.
        daily = {
            day: values
            for day, values in self._days.get((source, device_id), {}).items()
            if cutoff <= day < today
        }
        metrics: dict[str, MetricStats] = {}
        for name in METRICS:
            values = [value[name] for value in daily.values() if value[name] is not None]
            if values:
                metrics[name] = MetricStats(
                    median=float(median(values)),
                    q25=_quantile(values, 0.25),
                    q75=_quantile(values, 0.75),
                    samples=len(values),
                )
        open_days = sorted(
            (day, values["lid_open_count_24h"])
            for day, values in daily.items()
            if values["lid_open_count_24h"] is not None
        )
        trend = None
        if len(open_days) >= 3:
            xs = [(day - open_days[0][0]).days for day, _ in open_days]
            ys = [value for _, value in open_days]
            avg_x = sum(xs) / len(xs)
            avg_y = sum(ys) / len(ys)
            variance = sum((x - avg_x) ** 2 for x in xs)
            if variance:
                trend = sum((x - avg_x) * (y - avg_y) for x, y in zip(xs, ys)) / variance
        ready = len(daily) >= self.min_days and all(
            name in metrics and metrics[name].samples >= self.min_days
            for name in ("cook_count_24h", "lid_open_count_24h")
        )
        return BaselineSnapshot(
            device_id=device_id,
            source=source,
            as_of=as_of,
            sample_days=len(daily),
            ready=ready,
            metrics=metrics,
            open_count_trend_per_day=trend,
        )
