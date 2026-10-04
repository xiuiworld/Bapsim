"""Bapsim SnapshotPacked v1. One read-only Matter octet-string attribute.

This module only validates/decodes raw observations. No B events or inference.
"""
import base64
import struct

from .validate_snapshot import SIGNALS, validate

HEADER = struct.Struct("<4sHBIIQIBh5B7BBHB")
MAGIC = b"BS05"
MAX_SIZE = HEADER.size + 64
PATH = "1/4294048769/4293984260"


def encode(snapshot, *, test_input):
    validate(snapshot)
    if type(test_input) is not bool:
        raise ValueError("Explicit input origin required")
    version = snapshot["ModelVersion"].encode("utf-8")
    lid = snapshot["LidOpen"]
    temp = snapshot["TemperatureCentiC"]
    score = snapshot["InferenceScore"]
    return HEADER.pack(
        MAGIC, snapshot["SchemaVersion"], int(test_input), snapshot["BootId"],
        snapshot["ObservationSequence"], snapshot["UptimeMs"], snapshot["ReportCounter"],
        255 if lid is None else int(lid), -32768 if temp is None else temp,
        *(snapshot[k] or 0 for k in list(SIGNALS)[2:]),
        *(snapshot["SignalQuality"][k] for k in SIGNALS),
        snapshot["InferenceMethod"], 65535 if score is None else score, len(version),
    ) + version


def decode(payload):
    # python-matter-server transports bytes as base64 in JSON; the HA client
    # may already have decoded it. Accept only these two explicit forms.
    if isinstance(payload, str):
        payload = base64.b64decode(payload, validate=True)
    if not isinstance(payload, bytes) or not HEADER.size <= len(payload) <= MAX_SIZE:
        raise ValueError("Invalid SnapshotPacked size/type")
    v = HEADER.unpack_from(payload)
    if v[0] != MAGIC or v[2] not in (0, 1) or len(payload) != HEADER.size + v[-1]:
        raise ValueError("Invalid SnapshotPacked header/length")
    if v[7] not in (0, 1, 255):
        raise ValueError("Invalid lid boolean")
    result = dict(SchemaVersion=v[1], BootId=v[3], ObservationSequence=v[4],
                  UptimeMs=v[5], ReportCounter=v[6],
                  LidOpen=None if v[7] == 255 else bool(v[7]),
                  TemperatureCentiC=None if v[8] == -32768 else v[8])
    result.update(zip(list(SIGNALS)[2:], v[9:14]))
    result["SignalQuality"] = dict(zip(SIGNALS, v[14:21]))
    result.update(InferenceMethod=v[21], InferenceScore=None if v[22] == 65535 else v[22],
                  ModelVersion=payload[HEADER.size:].decode("utf-8"))
    validate(result)
    return result, bool(v[2])


class Continuity:
    """Raw continuity diagnostics, never convert a gap into no activity."""
    def __init__(self):
        self.previous = None

    def reset(self):
        self.previous = None

    def accept(self, current):
        previous, self.previous = self.previous, dict(current)
        if previous is None:
            return "baseline"
        if current["BootId"] != previous["BootId"]:
            return "reboot"
        keys = ("ReportCounter", "ObservationSequence", "UptimeMs")
        if any(current[k] < previous[k] for k in keys):
            return "reset"
        if current["ObservationSequence"] > previous["ObservationSequence"] + 1:
            return "gap"
        if current["ReportCounter"] == previous["ReportCounter"]:
            return "duplicate"
        return "continuous"
