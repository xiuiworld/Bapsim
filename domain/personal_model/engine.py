"""Learn expected cooker-use windows from observed lid openings.

This is a small probabilistic model, not a meal detector. Weak population
priors initialize four broad usage windows; covered personal days gradually
dominate them. Missing reports and declared absence never become inactivity.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, time, timedelta, timezone
from math import atan2, ceil, cos, exp, log, pi, sin, sqrt
from statistics import NormalDist, median
from typing import Mapping

from .evidence import usage_group_id


KST = timezone(timedelta(hours=9))
DAY_MINUTES = 24 * 60
PRIORS = (
    ("slot_1", 8 * 60, 0.35),
    ("slot_2", 13 * 60, 0.45),
    ("slot_3", 19 * 60, 0.40),
    ("slot_4", 30, 0.10),
)
USAGE_GROUP = timedelta(minutes=60)
MAX_REPLAY_DELAY = timedelta(hours=36)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("model timestamps require timezone")
    return parsed.astimezone(timezone.utc)


def _minutes(moment: datetime) -> float:
    local = moment.astimezone(KST)
    return local.hour * 60 + local.minute + local.second / 60


def _distance(a: float, b: float) -> float:
    return abs((a - b + DAY_MINUTES / 2) % DAY_MINUTES - DAY_MINUTES / 2)


def _circular_mean(values: list[float], prior: float) -> float:
    angles = [2 * pi * item / DAY_MINUTES for item in [prior, *values]]
    x = sum(cos(angle) for angle in angles)
    y = sum(sin(angle) for angle in angles)
    return atan2(y, x) * DAY_MINUTES / (2 * pi) % DAY_MINUTES


class PersonalUsageModel:
    def __init__(self, *, min_covered_days: int = 7, history_days: int = 28,
                 window_hours: float = 2.0, alert_probability: float = 0.72,
                 late_grace_hours: float = 0.0, interval_quantile: float = 0.95,
                 interval_strategy: str = "empirical",
                 cadence_window_support: bool = False,
                 interval_with_clock_windows: bool = False,
                 window_history_days: int | None = None,
                 historical_slot_lock: bool = False,
                 drift_gate_minutes: float | None = None) -> None:
        if min_covered_days < 1 or history_days < min_covered_days:
            raise ValueError("invalid learning window")
        if window_history_days is not None and not (
                min_covered_days <= window_history_days <= history_days):
            raise ValueError("window history must fit within the covered history")
        if drift_gate_minutes is not None and (
                drift_gate_minutes <= 0 or window_history_days is None or
                not historical_slot_lock):
            raise ValueError("drift gate requires a recent locked window")
        if (not 0 < window_hours <= 3 or not 0 < alert_probability <= 1 or
                not 0 <= late_grace_hours <= 2 or not 0.5 <= interval_quantile <= 0.95):
            raise ValueError("invalid model window or alert probability")
        if interval_strategy not in {"empirical", "robust_predictive", "guarded_predictive"}:
            raise ValueError("invalid interval strategy")
        self.min_covered_days = min_covered_days
        self.history_days = history_days
        self.window = timedelta(hours=window_hours)
        self.alert_probability = alert_probability
        self.late_grace = timedelta(hours=late_grace_hours)
        self.interval_quantile = interval_quantile
        self.interval_strategy = interval_strategy
        self.cadence_window_support = cadence_window_support
        self.interval_with_clock_windows = interval_with_clock_windows
        self.window_history_days = window_history_days or history_days
        self.historical_slot_lock = historical_slot_lock
        self.drift_gate_minutes = drift_gate_minutes
        self._devices: dict[tuple[str, str], dict[str, object]] = {}
        self._away_intervals: list[tuple[datetime, datetime]] = []
        self._away_since: datetime | None = None

    @property
    def device_keys(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._devices)

    def _device(self, source: str, device_id: str) -> dict[str, object]:
        return self._devices.setdefault((source, device_id), {
            "opens": [], "raw_opens": [], "group_breaks": [], "coverage": [], "online_since": None,
            "last_seen": None, "last_open_at": None,
            "evaluations": {}, "trend_evaluations": {},
            "decline_streak": 0, "recovery_streak": 0, "trend_active": False,
            "initial_windows": [], "centers_cache": {}, "clock_cache": {},
        })

    @staticmethod
    def parse_initial_windows(windows: object) -> list[dict[str, object]]:
        """Validate optional KST home-use times and expected weekdays (Mon=0)."""
        if not isinstance(windows, list) or len(windows) > len(PRIORS):
            raise ValueError("windows must be a list of at most four entries")
        result: list[dict[str, object]] = []
        available = set(range(len(PRIORS)))
        for item in windows:
            if not isinstance(item, Mapping) or not isinstance(item.get("time"), str):
                raise ValueError("each window needs HH:MM time")
            value = item["time"]
            try:
                parsed = time.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("window time must be HH:MM") from exc
            if len(value) != 5 or parsed.second or parsed.microsecond:
                raise ValueError("window time must be HH:MM")
            minute = parsed.hour * 60 + parsed.minute
            weekdays = item.get("weekdays", list(range(7)))
            if (not isinstance(weekdays, list) or not weekdays or
                    any(type(day) is not int or day not in range(7) for day in weekdays) or
                    len(set(weekdays)) != len(weekdays)):
                raise ValueError("weekdays must be unique integers 0..6")
            slot = min(available, key=lambda index: _distance(minute, PRIORS[index][1]))
            available.remove(slot)
            result.append({"slot": slot, "time": value, "minute": minute,
                           "weekdays": sorted(weekdays)})
        return sorted(result, key=lambda item: item["slot"])

    def configure_initial_routine(self, source: str, device_id: str, windows: object) -> None:
        if source not in {"matter", "simulator"} or not isinstance(device_id, str) or not device_id:
            raise ValueError("initial routine needs source and device_id")
        parsed = self.parse_initial_windows(windows)
        state = self._device(source, device_id)
        state["initial_windows"] = parsed
        state["centers_cache"].clear()
        state["clock_cache"].clear()

    def initial_routine(self, source: str, device_id: str) -> list[dict[str, object]]:
        return [{"time": item["time"], "weekdays": list(item["weekdays"])}
                for item in self._device(source, device_id)["initial_windows"]]

    def grouped_open_times(self, source: str, device_id: str) -> tuple[str, ...]:
        """Expose observed usage groups without requiring a persisted snapshot."""
        return tuple(at.isoformat() for at in self._device(source, device_id)["opens"])

    @staticmethod
    def _group_evidence(state: dict[str, object], source: str, device_id: str,
                        openings: list[datetime]) -> list[str]:
        """Link used openings to accepted, persisted group starts."""
        groups = state["opens"]
        starts = set()
        for opening in openings:
            index = bisect_right(groups, opening) - 1
            if index >= 0:
                starts.add(groups[index])
        return [usage_group_id(source, device_id, at.isoformat()) for at in sorted(starts)[-64:]]

    def dump_state(self) -> dict[str, object]:
        devices = []
        for (source, device_id), state in self._devices.items():
            devices.append({
                "source": source, "device_id": device_id,
                "opens": [at.isoformat() for at in state["opens"]],
                "raw_opens": [at.isoformat() for at in state["raw_opens"]],
                "group_breaks": [at.isoformat() for at in state["group_breaks"]],
                "coverage": [[start.isoformat(), end.isoformat()] for start, end in state["coverage"]],
                "online_since": state["online_since"].isoformat() if state["online_since"] else None,
                "last_seen": state["last_seen"].isoformat() if state["last_seen"] else None,
                "last_open_at": state["last_open_at"].isoformat() if state["last_open_at"] else None,
                "evaluations": dict(state["evaluations"]),
                "trend_evaluations": dict(state["trend_evaluations"]),
                "decline_streak": state["decline_streak"],
                "recovery_streak": state["recovery_streak"],
                "trend_active": state["trend_active"],
                "initial_windows": self.initial_routine(source, device_id),
            })
        return {
            "devices": devices,
            "away_intervals": [[start.isoformat(), end.isoformat()] for start, end in self._away_intervals],
            "away_since": self._away_since.isoformat() if self._away_since else None,
        }

    def load_state(self, stored: Mapping[str, object]) -> None:
        self._devices.clear()
        for item in stored.get("devices", []):
            source, device_id = item["source"], item["device_id"]
            state = self._device(source, device_id)
            # Older snapshots only stored group starts. Treat those as the
            # available observations without inventing the missing raw opens.
            state["raw_opens"] = sorted(_time(at) for at in item.get("raw_opens", item.get("opens", [])))
            state["group_breaks"] = sorted(_time(at) for at in item.get(
                "group_breaks", [end for _, end in item.get("coverage", [])]))
            latest_open = item.get("last_open_at")
            state["last_open_at"] = max(
                [*state["raw_opens"], *([_time(latest_open)] if latest_open else [])],
                default=None)
            state["coverage"] = [(_time(start), _time(end)) for start, end in item.get("coverage", [])]
            state["evaluations"] = dict(item.get("evaluations", {}))
            state["trend_evaluations"] = dict(item.get("trend_evaluations", {}))
            state["decline_streak"] = int(item.get("decline_streak", 0))
            state["recovery_streak"] = int(item.get("recovery_streak", 0))
            state["trend_active"] = bool(item.get("trend_active", False))
            state["initial_windows"] = self.parse_initial_windows(item.get("initial_windows", []))
            # A process restart interrupts observed coverage. Close it at the
            # last actual heartbeat; never span an unobserved downtime.
            start = item.get("online_since")
            end = item.get("last_seen")
            if start and end:
                if _time(end) > _time(start):
                    state["coverage"].append((_time(start), _time(end)))
                state["group_breaks"].append(_time(end))
            state["online_since"] = None
            state["last_seen"] = _time(end) if end else None
            state["opens"] = self._group_opens(state["raw_opens"], state["group_breaks"])
        self._away_intervals = [(_time(start), _time(end)) for start, end in stored.get("away_intervals", [])]
        self._away_since = None
        if stored.get("away_since"):
            # The persisted away flag is reapplied by the host after setup.
            self._away_since = _time(stored["away_since"])

    def set_away(self, away: bool, at: str) -> None:
        moment = _time(at)
        if away and self._away_since is None:
            self._away_since = moment
            for state in self._devices.values():
                state["centers_cache"].clear()
                state["clock_cache"].clear()
        elif not away and self._away_since is not None:
            if moment > self._away_since:
                self._away_intervals.append((self._away_since, moment))
            self._away_since = None
            for state in self._devices.values():
                state["centers_cache"].clear()
                state["clock_cache"].clear()

    def availability(self, source: str, device_id: str, online: bool, at: str) -> None:
        moment = _time(at)
        state = self._device(source, device_id)
        last = state["last_seen"]
        if last is not None and moment < last:
            return
        state["last_seen"] = moment
        start = state["online_since"]
        if online:
            if start is None:
                state["online_since"] = moment
                state["clock_cache"].clear()
        elif start is not None:
            if moment > start:
                state["coverage"].append((start, moment))
                state["group_breaks"].append(moment)
            state["online_since"] = None
            state["clock_cache"].clear()
        self._prune(state, moment)

    def observe(self, event: Mapping[str, object]) -> bool:
        """Return whether a lid opening was accepted as usable evidence."""
        kind = event.get("event_type")
        if kind not in {"device_online", "device_offline", "lid_opened"}:
            return False
        source, device_id, at = event.get("source"), event.get("device_id"), event.get("observed_at")
        if not all(isinstance(item, str) and item for item in (source, device_id, at)):
            raise ValueError("canonical event needs source, device_id and observed_at")
        moment = _time(at)
        if kind in {"device_online", "device_offline"}:
            self.availability(source, device_id, kind == "device_online", at)
            return False
        if event.get("availability") != "online":
            return False
        state = self._device(source, device_id)
        last_open = state["last_open_at"]
        if last_open is not None and moment <= last_open:
            return False
        last_seen = state["last_seen"]
        if last_seen is not None and moment < last_seen - MAX_REPLAY_DELAY:
            return False
        raw_opens = state["raw_opens"]
        state["last_open_at"] = moment
        raw_opens.append(moment)
        state["opens"] = self._group_opens(raw_opens, state["group_breaks"])
        state["centers_cache"].clear()
        state["clock_cache"].clear()
        self._prune(state, moment)
        return True

    @staticmethod
    def _group_opens(raw_opens: list[datetime], breaks: list[datetime] = ()) -> list[datetime]:
        starts: list[datetime] = []
        for moment in raw_opens:
            if (not starts or moment >= starts[-1] + USAGE_GROUP or
                    any(starts[-1] < boundary <= moment for boundary in breaks)):
                starts.append(moment)
        return starts

    def _prune(self, state: dict[str, object], now: datetime) -> None:
        cutoff = now - timedelta(days=self.history_days + 3)
        state["raw_opens"] = [at for at in state["raw_opens"] if at >= cutoff]
        state["group_breaks"] = [at for at in state["group_breaks"] if at >= cutoff]
        state["opens"] = self._group_opens(state["raw_opens"], state["group_breaks"])
        state["coverage"] = [(start, end) for start, end in state["coverage"] if end >= cutoff]
        state["evaluations"] = {
            key: status for key, status in state["evaluations"].items()
            if date.fromisoformat(key.split("|", 1)[0]) >= cutoff.astimezone(KST).date()
        }
        state["trend_evaluations"] = {
            key: value for key, value in state["trend_evaluations"].items()
            if date.fromisoformat(key) >= cutoff.astimezone(KST).date()
        }
        self._away_intervals = [(start, end) for start, end in self._away_intervals if end >= cutoff]

    def _centers_for(self, state: dict[str, object], before: date,
                     history_days: int, slot_lock: bool) -> tuple[list[float], list[int]]:
        cutoff = before - timedelta(days=history_days)
        samples = [
            at for at in state["opens"]
            if cutoff <= at.astimezone(KST).date() < before
            and not self._away_at(at)
        ]
        configured = {int(item["slot"]): float(item["minute"])
                      for item in state["initial_windows"]}
        centers = [configured.get(index, float(prior))
                   for index, (_, prior, _) in enumerate(PRIORS)]
        eligible = list(range(len(centers)))
        if slot_lock and history_days < self.history_days:
            anchor_start = before - timedelta(days=self.history_days)
            anchor_end = before - timedelta(days=history_days)
            counts = [0] * len(centers)
            for at in state["opens"]:
                if not (anchor_start <= at.astimezone(KST).date() < anchor_end) or self._away_at(at):
                    continue
                minute = _minutes(at)
                closest = min(range(len(centers)),
                              key=lambda index: _distance(minute, centers[index]))
                if _distance(minute, centers[closest]) <= 6 * 60:
                    counts[closest] += 1
            active = [index for index, count in enumerate(counts)
                      if count >= 3 or index in configured]
            if active:
                eligible = active
        for _ in range(4):
            groups = [[] for _ in PRIORS]
            for at in samples:
                item = _minutes(at)
                closest = min(eligible, key=lambda index: _distance(item, centers[index]))
                if _distance(item, centers[closest]) <= 6 * 60:
                    groups[closest].append(item)
            centers = [
                _circular_mean(group, configured.get(index, float(PRIORS[index][1])))
                if group else configured.get(index, float(PRIORS[index][1]))
                for index, group in enumerate(groups)
            ]
        return centers, eligible

    def _centers(self, state: dict[str, object], before: date) -> tuple[list[float], int]:
        if before in state["centers_cache"]:
            return state["centers_cache"][before]
        recent, eligible = self._centers_for(
            state, before, self.window_history_days, self.historical_slot_lock)
        if self.drift_gate_minutes is None:
            result = (recent, self.window_history_days)
        else:
            baseline, _ = self._centers_for(state, before, self.history_days, False)
            if any(_distance(recent[index], baseline[index]) >= self.drift_gate_minutes
                   for index in eligible):
                result = (recent, self.window_history_days)
            else:
                result = (baseline, self.history_days)
        # Only prior days affect a center; a new opening or away change clears this cache.
        state["centers_cache"][before] = result
        if len(state["centers_cache"]) > 4:
            oldest = min(state["centers_cache"])
            del state["centers_cache"][oldest]
        return result

    def _away_at(self, moment: datetime) -> bool:
        return any(start <= moment < end for start, end in self._away_intervals) or (
            self._away_since is not None and moment >= self._away_since
        )

    def _window(self, day: date, center: float) -> tuple[datetime, datetime]:
        local_midnight = datetime.combine(day, time.min, KST)
        middle = local_midnight + timedelta(minutes=center)
        return (middle - self.window).astimezone(timezone.utc), (middle + self.window).astimezone(timezone.utc)

    def _covered(self, state: dict[str, object], start: datetime, end: datetime, now: datetime) -> bool:
        intervals = list(state["coverage"])
        if state["online_since"] is not None:
            intervals.append((state["online_since"], now))
        cursor = start
        for left, right in sorted(intervals):
            if right <= cursor:
                continue
            if left > cursor:
                return False
            cursor = max(cursor, right)
            if cursor >= end:
                return True
        return False

    def _away_window(self, start: datetime, end: datetime) -> bool:
        intervals = self._away_intervals[:]
        if self._away_since is not None:
            intervals.append((self._away_since, end))
        return any(left < end and right > start for left, right in intervals)

    def _used(self, state: dict[str, object], start: datetime, end: datetime) -> bool:
        # A usage group can start before this window (or on the previous day)
        # while a later, real opening still falls inside it.
        return any(start <= at < end for at in state["raw_opens"])

    def _daily_group_count(self, state: dict[str, object], start: datetime,
                           end: datetime) -> int:
        # The daily trend has a calendar-day grain. Group after selecting the
        # local day so a real opening after midnight is not lost in yesterday's
        # episode; these counts are observed activity, not meal counts.
        daily_opens = [opening for opening in state["raw_opens"]
                       if start <= opening < end]
        return len(self._group_opens(daily_opens, state["group_breaks"]))

    def _cadence_supports_missing_window(self, state: dict[str, object],
                                         decision_at: datetime) -> bool:
        """Back a moderately likely clock window with an unusually long use gap."""
        opens = [at for at in state["opens"] if at <= decision_at and not self._away_at(at)]
        if not opens:
            return False
        intervals = sorted(right - left for left, right in zip(opens, opens[1:])
                           if self._covered(state, left, right, decision_at)
                           and not self._away_window(left, right))
        if len(intervals) < self.min_covered_days:
            return False
        last = opens[-1]
        if not self._covered(state, last, decision_at, decision_at) or self._away_window(last, decision_at):
            return False
        limit = intervals[ceil(0.75 * len(intervals)) - 1]
        return decision_at - last > limit + timedelta(hours=1)

    def _profile(self, state: dict[str, object], day: date, slot: int, center: float,
                 now: datetime, history_days: int, source: str, device_id: str) -> dict[str, object]:
        count = positive = group_count = group_positive = weekday_count = weekday_positive = 0.0
        positive_days = 0
        covered_days = 0
        evidence_times = []
        for offset in range(1, history_days + 1):
            previous = day - timedelta(days=offset)
            start, end = self._window(previous, center)
            if not self._covered(state, start, end, now) or self._away_window(start, end):
                continue
            covered_days += 1
            flagged = state["evaluations"].get(f"{previous.isoformat()}|{slot}") == "check_required"
            weight = 0.15 if flagged else 1.0
            used = self._used(state, start, end)
            if used:
                evidence_times.extend(at for at in state["raw_opens"] if start <= at < end)
            positive_days += int(used)
            count += weight
            positive += weight * used
            if (previous.weekday() >= 5) == (day.weekday() >= 5):
                group_count += weight
                group_positive += weight * used
            if previous.weekday() == day.weekday():
                weekday_count += weight
                weekday_positive += weight * used
        configured = next((item for item in state["initial_windows"] if item["slot"] == slot), None)
        expected_today = configured is not None and day.weekday() in configured["weekdays"]
        prior_probability = (0.80 if expected_today else 0.05) if configured else PRIORS[slot][2]
        all_probability = (2 * prior_probability + positive) / (2 + count)
        group_probability = (4 * all_probability + group_positive) / (4 + group_count)
        probability = (3 * group_probability + weekday_positive) / (3 + weekday_count)
        return {
            "slot": PRIORS[slot][0], "expected_time": (
                datetime.combine(day, time.min, KST) + timedelta(minutes=center)
            ).isoformat(),
            "probability": round(probability, 4),
            "covered_days": covered_days,
            "positive_days": positive_days,
            "personal_weight": round(count / (2 + count), 4),
            "initial_ready": bool(expected_today and 3 <= covered_days < self.min_covered_days
                                  and positive_days >= 2),
            "schedule_excluded": bool(configured and not expected_today and weekday_positive < 3),
            "initial_configured": configured is not None,
            "evidence_ids": self._group_evidence(state, source, device_id, evidence_times),
        }

    def evaluate(self, source: str, device_id: str, as_of: str, *, online: bool,
                 away: bool = False) -> list[dict[str, object]]:
        now = _time(as_of)
        state = self._device(source, device_id)
        self.availability(source, device_id, online, as_of)
        local_day = now.astimezone(KST).date()
        decisions = []
        for day in (local_day - timedelta(days=2), local_day - timedelta(days=1), local_day):
            centers, history_days = self._centers(state, day)
            for index, center in enumerate(centers):
                start, end = self._window(day, center)
                decision_at = end + self.late_grace
                if not decision_at <= now or now - decision_at > MAX_REPLAY_DELAY:
                    continue
                evaluation_key = f"{day.isoformat()}|{index}"
                if evaluation_key in state["evaluations"]:
                    continue
                profile = self._profile(state, day, index, center, now, history_days,
                                        source, device_id)
                cadence_supported = False
                if away or self._away_window(start, decision_at):
                    status, reason, score = "away", "AWAY_DECLARED", None
                elif not self._covered(state, start, decision_at, now):
                    status, reason, score = "data_unavailable", "COOKER_DATA_UNAVAILABLE", None
                elif profile["schedule_excluded"]:
                    status, reason, score = "no_expectation", None, profile["probability"]
                elif profile["covered_days"] < self.min_covered_days and not profile["initial_ready"]:
                    status, reason, score = "learning", "PATTERN_LEARNING", None
                elif self._used(state, start, decision_at):
                    status, reason, score = "normal", None, round(1 - profile["probability"], 4)
                elif profile["probability"] >= (0.65 if profile["initial_ready"] else self.alert_probability):
                    status, reason, score = "check_required", "EXPECTED_USE_MISSING", profile["probability"]
                elif (self.cadence_window_support and profile["probability"] >= 0.55 and
                      self._cadence_supports_missing_window(state, decision_at)):
                    status, reason, score = "check_required", "EXPECTED_USE_MISSING", profile["probability"]
                    cadence_supported = True
                else:
                    status, reason, score = "no_expectation", None, profile["probability"]
                resumed_before_replay = (now > decision_at and any(
                    decision_at < opening <= now for opening in state["opens"]))
                if status == "check_required" and resumed_before_replay:
                    status, reason, score = "normal", None, round(1 - profile["probability"], 4)
                    cadence_supported = False
                state["evaluations"][evaluation_key] = status
                state["clock_cache"].clear()
                # Only an unresolved missed-use decision needs a late workflow
                # update. Connection loss and lid opening have their own live
                # events, and old learning/normal states would be misleading.
                if now >= decision_at + timedelta(hours=1) and status != "check_required":
                    continue
                decisions.append({
                    "source": source, "device_id": device_id, "as_of": decision_at.isoformat(),
                    "routine_status": status,
                    "severity": "warning" if status == "check_required" else "info" if reason else "none",
                    "reason_codes": [reason] if reason else [], "score": score,
                    "evidence": {
                        **profile, "observed_use": self._used(state, start, decision_at),
                        "window_start": start.isoformat(), "window_end": end.isoformat(),
                        "decision_at": decision_at.isoformat(),
                        "replayed_after_delay": now >= decision_at + timedelta(hours=1),
                        "resumed_before_replay": resumed_before_replay,
                        "cadence_supported": cadence_supported,
                        "model": "personal_usage_v1",
                    },
                })
        self._prune(state, now)
        return sorted(decisions, key=lambda item: item["as_of"])

    def profile(self, source: str, device_id: str, as_of: str) -> dict[str, object]:
        now = _time(as_of)
        state = self._device(source, device_id)
        day = now.astimezone(KST).date()
        centers, history_days = self._centers(state, day)
        windows = [self._profile(state, day, index, center, now, history_days, source, device_id)
                   for index, center in enumerate(centers)]
        recent_opens = [at for at in state["opens"] if now - timedelta(hours=24) < at <= now]
        previous_opens = [at for at in state["opens"] if at <= now]
        return {
            "source": source, "device_id": device_id, "as_of": as_of,
            "model": "personal_usage_v1", "min_covered_days": self.min_covered_days,
            "windows": windows,
            "lid_open_count_24h": len(recent_opens),
            "last_lid_open_at": previous_opens[-1].isoformat() if previous_opens else None,
            # Policy section 3: label personal history only after 7 covered
            # days and 5 valid usage days. Early configured decisions remain enabled.
            "ready": any(window["covered_days"] >= self.min_covered_days and
                         window["positive_days"] >= 5 for window in windows),
            "initial_ready": any(window["initial_ready"] for window in windows),
            "decision_ready": any((window["covered_days"] >= self.min_covered_days and
                                   window["positive_days"] >= 2) or window["initial_ready"]
                                  for window in windows),
        }

    def evaluate_interval(self, source: str, device_id: str, as_of: str, *, online: bool,
                          away: bool = False) -> dict[str, object] | None:
        """Use covered inter-use intervals when no clock-time window is reliable."""
        now = _time(as_of)
        state = self._device(source, device_id)
        if not online or away or not state["opens"]:
            return None
        local_day = now.astimezone(KST).date()
        if local_day not in state["clock_cache"]:
            profile = self.profile(source, device_id, as_of)
            state["clock_cache"][local_day] = any(
                not window["schedule_excluded"] and
                window["probability"] >= (0.65 if window["initial_ready"] else self.alert_probability) and
                (window["covered_days"] >= self.min_covered_days or window["initial_ready"])
                for window in profile["windows"])
            if len(state["clock_cache"]) > 3:
                del state["clock_cache"][min(state["clock_cache"])]
        clock_available = state["clock_cache"][local_day]
        if clock_available and not self.interval_with_clock_windows:
            return None
        opens = [at for at in state["opens"] if at <= now and not self._away_at(at)]
        sampled_opens = set()
        intervals = []
        for left, right in zip(opens, opens[1:]):
            if self._covered(state, left, right, now) and not self._away_window(left, right):
                intervals.append(right - left)
                sampled_opens.update((left, right))
        if len(intervals) < self.min_covered_days:
            return None
        intervals.sort()
        p95 = intervals[ceil(0.95 * len(intervals)) - 1]
        if self.interval_strategy in {"robust_predictive", "guarded_predictive"}:
            # Estimate a future gap on the log scale. Median/MAD keep one
            # unusually long normal skip from becoming the entire threshold.
            # A dispersion floor and sample-size factor widen sparse-history
            # predictions; neither implies a field-calibrated risk probability.
            log_hours = [log(item.total_seconds() / 3600) for item in intervals]
            center = median(log_hours)
            spread = median(abs(item - center) for item in log_hours)
            scale = max(1.4826 * spread, 0.25)
            z_score = NormalDist().inv_cdf(self.interval_quantile)
            hours = exp(center + z_score * scale * sqrt(1 + 4 / len(intervals)))
            predictive_limit = timedelta(hours=hours)
            limit = (min(p95, predictive_limit) if self.interval_strategy == "guarded_predictive"
                     else predictive_limit)
        else:
            limit = intervals[ceil(self.interval_quantile * len(intervals)) - 1]
        last = opens[-1]
        if not self._covered(state, last, now, now) or self._away_window(last, now):
            return None
        elapsed = now - last
        status = "normal" if elapsed <= limit else "watch" if elapsed <= limit + timedelta(hours=2) else "check_required"
        # A normal interval does not refute a missed clock window. Sending it
        # through the shared workflow would erase that window's confirmation.
        if clock_available and status != "check_required":
            return None
        return {
            "source": source, "device_id": device_id, "as_of": as_of,
            "routine_status": status,
            "severity": "warning" if status == "check_required" else "none",
            "reason_codes": ["UNUSUAL_USE_GAP"] if status == "check_required" else [],
            "score": None,
            "evidence": {"model": "personal_interval_v1", "sample_count": len(intervals),
                         "evidence_ids": self._group_evidence(
                             state, source, device_id, sorted(sampled_opens | {last})),
                         "last_use_at": last.isoformat(), "p95_hours": round(p95.total_seconds() / 3600, 2),
                         "threshold_hours": round(limit.total_seconds() / 3600, 2),
                         "interval_quantile": self.interval_quantile,
                         "interval_strategy": self.interval_strategy,
                         "elapsed_hours": round(elapsed.total_seconds() / 3600, 2)},
        }

    def evaluate_trend(self, source: str, device_id: str, as_of: str, *, online: bool,
                       away: bool = False) -> dict[str, object] | None:
        """Evaluate complete covered days; never treat an outage as zero use."""
        now = _time(as_of)
        state = self._device(source, device_id)
        day = now.astimezone(KST).date() - timedelta(days=1)
        key = day.isoformat()
        if state["trend_evaluations"].get(key) in {"declining", "normal"}:
            return None
        if not online or away:
            return None
        counts: list[tuple[date, int]] = []
        evidence_times = []
        for offset in range(28, 0, -1):
            sample_day = now.astimezone(KST).date() - timedelta(days=offset)
            start = datetime.combine(sample_day, time.min, KST).astimezone(timezone.utc)
            end = start + timedelta(days=1)
            if not self._covered(state, start, end, now) or self._away_window(start, end):
                continue
            counts.append((sample_day, self._daily_group_count(state, start, end)))
            evidence_times.extend(at for at in state["raw_opens"] if start <= at < end)
        recent = [count for sample_day, count in counts if day - timedelta(days=6) <= sample_day <= day]
        reference = [count for sample_day, count in counts if day - timedelta(days=27) <= sample_day < day - timedelta(days=6)]
        if len(recent) < 5 or len(reference) < 10:
            state["trend_evaluations"][key] = "insufficient"
            return None
        recent_rate = sum(recent) / len(recent)
        reference_rate = sum(reference) / len(reference)
        decline = reference_rate > 0 and recent_rate <= reference_rate * 0.7
        previous = state["trend_evaluations"].get((day - timedelta(days=1)).isoformat())
        if previous != "declining":
            state["decline_streak"] = 0
        if previous != "normal":
            state["recovery_streak"] = 0
        state["trend_evaluations"][key] = "declining" if decline else "normal"
        state["decline_streak"] = state["decline_streak"] + 1 if decline else 0
        state["recovery_streak"] = 0 if decline else state["recovery_streak"] + 1
        if decline and state["decline_streak"] >= 3 and not state["trend_active"]:
            state["trend_active"] = True
            status, reason = "check_required", "DECLINING_USAGE"
        elif decline and state["trend_active"]:
            # Keep supplying a fresh daily assessment to the workflow. Its
            # seven-day cadence, controls and quiet-hours policy decide whether
            # a reminder may actually be sent.
            status, reason = "check_required", "DECLINING_USAGE"
        elif not decline and state["recovery_streak"] >= 3 and state["trend_active"]:
            state["trend_active"] = False
            status, reason = "normal", "DECLINE_RESOLVED"
        else:
            return None
        return {
            "source": source, "device_id": device_id, "as_of": now.isoformat(),
            "routine_status": status, "severity": "warning" if decline else "none",
            "reason_codes": [reason],
            "score": round(1 - recent_rate / reference_rate, 4) if reference_rate else None,
            "evidence": {"model": "personal_usage_v1", "assessment_type": "declining_usage",
                         "evidence_ids": self._group_evidence(state, source, device_id, evidence_times),
                         "recent_days": len(recent), "reference_days": len(reference),
                         "recent_rate": round(recent_rate, 4), "reference_rate": round(reference_rate, 4)},
        }
