"""Validate a named raw snapshot candidate, without inference or B normalization.

This is an offline contract check, NOT a Matter TLV decoder or HA integration.
Matter wire IDs/struct decoding still require SDK and server validation.
"""
import argparse
import json
from pathlib import Path

SIGNALS = {
    "LidOpen": None,
    "TemperatureCentiC": None,
    "LockLed": {1, 2, 3},
    "WarmLed": {1, 2, 3},
    "WhiteRiceLed": {1, 2, 3},
    "TimerFirstLed": {1, 2, 3},
    "CookerPhase": {1, 2, 3},
}
META = {"SchemaVersion", "BootId", "ObservationSequence", "UptimeMs",
        "ReportCounter", "SignalQuality", "InferenceMethod", "ModelVersion",
        "InferenceScore"}


def uint(value, bits, name):
    if type(value) is not int or not 0 <= value < 2 ** bits:
        raise ValueError(f"{name}: expected uint{bits}")


def validate(snapshot):
    """Return the original object only if every field is coherent and supported."""
    if type(snapshot) is not dict or set(snapshot) != META | set(SIGNALS):
        raise ValueError("Snapshot fields missing or unexpected")
    uint(snapshot["SchemaVersion"], 16, "SchemaVersion")
    if snapshot["SchemaVersion"] != 1:
        raise ValueError("Unsupported SchemaVersion")
    for field in ("BootId", "ObservationSequence", "ReportCounter"):
        uint(snapshot[field], 32, field)
    uint(snapshot["UptimeMs"], 64, "UptimeMs")
    qualities = snapshot["SignalQuality"]
    if type(qualities) is not dict or set(qualities) != set(SIGNALS):
        raise ValueError("SignalQuality must cover every signal exactly")
    for name, allowed in SIGNALS.items():
        quality = qualities[name]
        uint(quality, 8, f"SignalQuality.{name}")
        if quality not in (0, 1, 2, 3):
            raise ValueError(f"{name}: unsupported quality enum")
        value = snapshot[name]
        if quality != 1:
            # The board may use enum 0 for unknown. Preserve it, never infer off.
            if value is not None and not (allowed and type(value) is int and value == 0):
                raise ValueError(f"{name}: invalid quality cannot carry a normal value")
        elif name == "LidOpen":
            if type(value) is not bool:
                raise ValueError("LidOpen: expected boolean")
        elif name == "TemperatureCentiC":
            if type(value) is not int or not -27315 <= value <= 32767:
                raise ValueError("TemperatureCentiC: expected valid Matter centidegrees")
        elif type(value) is not int or value not in allowed:
            raise ValueError(f"{name}: invalid known enum")
    method = snapshot["InferenceMethod"]
    uint(method, 8, "InferenceMethod")
    if method not in (0, 1, 2):
        raise ValueError("Unsupported inference method")
    version = snapshot["ModelVersion"]
    if type(version) is not str or len(version.encode("utf-8")) > 64:
        raise ValueError("ModelVersion: expected UTF-8 string <=64 bytes")
    score = snapshot["InferenceScore"]
    if score is not None:
        uint(score, 16, "InferenceScore")
        if score > 10000:
            raise ValueError("InferenceScore exceeds 10000")
    if qualities["CookerPhase"] == 1 and (method == 0 or not version.strip()):
        raise ValueError("Known phase requires inference method and version")
    if method == 0 and (version or score is not None):
        raise ValueError("No inference cannot carry a model version or score")
    if qualities["CookerPhase"] != 1 and score is not None:
        raise ValueError("Unavailable phase cannot carry a current score")
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    records = json.loads(args.path.read_text(encoding="utf-8"))
    if type(records) is not dict or records.get("input_kind") != "test":
        raise ValueError("Offline fixtures must explicitly identify test input")
    cases = records["cases"]
    for case in cases:
        validate(case["snapshot"])
    print(f"Validated {len(cases)} offline test snapshots; no device or HA writes.")


if __name__ == "__main__":
    main()
