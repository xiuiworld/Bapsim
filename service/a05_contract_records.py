"""B-side validation and A–B records for one atomic A05 SnapshotPacked report."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4


FIELDS = ("LidOpen", "TemperatureCentiC", "LockLed", "WarmLed",
          "WhiteRiceLed", "TimerFirstLed", "CookerPhase")
META = {"SchemaVersion", "BootId", "ObservationSequence", "UptimeMs",
        "ReportCounter", "SignalQuality", "InferenceMethod", "ModelVersion",
        "InferenceScore"}
NAMES = ("lid_open", "surface_temperature_c", "lock_led", "warm_led",
         "white_rice_led", "timer_first_led", "cooker_phase")
QUALITIES = {0: "unknown", 1: "good", 2: "error", 3: "unsupported"}
LED = {1: "off", 2: "on", 3: "blinking"}
PHASE = {1: "inactive", 2: "cooking", 3: "warming"}
METHOD = {1: "edge_model", 2: "rule"}
MAX_AGE = timedelta(minutes=3)


def _uint(value: object, bits: int) -> bool:
    return type(value) is int and 0 <= value < 2 ** bits


def valid_snapshot(raw: object) -> bool:
    """Reject malformed atomic reports before they reach either B path."""
    if (type(raw) is not dict or set(raw) != META | set(FIELDS) or
            not _uint(raw["SchemaVersion"], 16) or raw["SchemaVersion"] != 1):
        return False
    if not all(_uint(raw[key], 32) for key in ("BootId", "ObservationSequence", "ReportCounter")):
        return False
    if not _uint(raw["UptimeMs"], 64):
        return False
    quality = raw["SignalQuality"]
    if type(quality) is not dict or set(quality) != set(FIELDS):
        return False
    for field in FIELDS:
        q, value = quality[field], raw[field]
        if type(q) is not int or q not in QUALITIES:
            return False
        if q != 1:
            if value is not None and not (field in FIELDS[2:] and type(value) is int and value == 0):
                return False
        elif field == "LidOpen":
            if type(value) is not bool:
                return False
        elif field == "TemperatureCentiC":
            if type(value) is not int or not -27315 <= value <= 32767:
                return False
        elif type(value) is not int or value not in (PHASE if field == "CookerPhase" else LED):
            return False
    method, version, score = raw["InferenceMethod"], raw["ModelVersion"], raw["InferenceScore"]
    if type(method) is not int or method not in (0, 1, 2):
        return False
    if type(version) is not str:
        return False
    try:
        version_bytes = version.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(version_bytes) > 64:
        return False
    if score is not None and (not _uint(score, 16) or score > 10000):
        return False
    if quality["CookerPhase"] == 1 and (method == 0 or not version.strip()):
        return False
    if method == 0 and (version or score is not None):
        return False
    return quality["CookerPhase"] == 1 or score is None


def _utc(value: datetime | None) -> str | None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class A05ContractRecords:
    """One live stream per device; never accepts a test-origin report."""

    def __init__(self, household_id: str, device_id: str, source_ref: str):
        self.household_id, self.device_id, self.source_ref = household_id, device_id, source_ref
        self.stream_id: str | None = None
        self.sequence = 0
        self.previous: tuple[int, int, int] | None = None
        self.last_report: datetime | None = None
        self.last_snapshot: dict | None = None
        self.values: dict[str, tuple[object, str]] = {}

    def _new_stream(self) -> None:
        self.stream_id = uuid4().hex
        self.sequence = 0
        self.values.clear()

    def _record(self, signal: str, value: object, quality: str, kind: str,
                now: datetime, source_at: datetime | None, inference: dict | None = None) -> dict:
        assert self.stream_id is not None
        number = self.sequence
        self.sequence += 1
        return {"schema_version": "0.1.0", "record_id": f"{self.stream_id}:{number}",
                "household_id": self.household_id, "device_id": self.device_id,
                "stream_id": self.stream_id, "sequence": number, "mode": "live",
                "kind": kind, "signal": signal, "value": value if quality == "good" else None,
                "quality": quality, "observed_at": None, "source_at": _utc(source_at),
                "received_at": _utc(now), "source_ref": self.source_ref,
                "inference": inference}

    def offline(self, now: datetime) -> list[dict]:
        if self.previous is None:
            return []
        self.previous = None
        self.last_report = None
        self.last_snapshot = None
        self.values.clear()
        return [self._record(name, None, "unavailable", "quality", now, None)
                for name in NAMES] + [self._record("device_connection", "offline", "good",
                                              "change", now, None)]

    def accept(self, raw: dict, now: datetime, source_at: datetime | None) -> list[dict]:
        if not valid_snapshot(raw):
            raise ValueError("invalid A05 snapshot")
        current = (raw["BootId"], raw["ObservationSequence"], raw["ReportCounter"])
        if current == self.previous:
            return []
        prior = self.previous
        gap = prior is not None and (current[0] != prior[0] or
                                    current[1] not in (prior[1], prior[1] + 1) or
                                    current[2] != prior[2] + 1 or
                                    (self.last_report is not None and now > self.last_report + MAX_AGE) or
                                    (self.last_snapshot is not None and raw["UptimeMs"] < self.last_snapshot["UptimeMs"]) or
                                    (current[1] == prior[1] and self.last_snapshot is not None and
                                     (any(raw[field] != self.last_snapshot[field] for field in FIELDS) or
                                      raw["SignalQuality"] != self.last_snapshot["SignalQuality"] or
                                      any(raw[field] != self.last_snapshot[field] for field in
                                          ("InferenceMethod", "ModelVersion", "InferenceScore")))))
        result = self.offline(now) if gap else []
        if self.previous is None:
            self._new_stream()
        baseline = self.previous is None
        if baseline:
            result.append(self._record("device_connection", "online", "good",
                                       "snapshot", now, source_at))
        for field, name in zip(FIELDS, NAMES):
            quality = QUALITIES[raw["SignalQuality"][field]]
            value = raw[field] if quality == "good" else None
            if field == "TemperatureCentiC" and value is not None:
                value /= 100
            elif field == "CookerPhase" and value is not None:
                value = PHASE[value]
            elif field in FIELDS[2:6] and value is not None:
                value = LED[value]
            inference = None
            if field == "CookerPhase" and quality == "good":
                inference = {"method": METHOD[raw["InferenceMethod"]],
                             "version": raw["ModelVersion"],
                             "score": None if raw["InferenceScore"] is None else raw["InferenceScore"] / 10000}
            previous = self.values.get(name)
            kind = ("snapshot" if baseline or (previous is not None and
                                                previous[1] != "good" and quality == "good")
                    else "quality" if previous is not None and previous[1] != quality
                    else "change" if previous is not None and previous[0] != value
                    else "snapshot")
            result.append(self._record(name, value, quality, kind, now, source_at, inference))
            self.values[name] = (value, quality)
        self.previous = current
        self.last_report = now
        self.last_snapshot = raw.copy()
        self.last_snapshot["SignalQuality"] = raw["SignalQuality"].copy()
        return result
