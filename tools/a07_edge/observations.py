"""Raw sensor conversion, temporal features and strict JSONL recording.

No HA writes, physical ADC driver, event normalization or personal analysis.
ADC topology and calibration are explicit hypotheses until measured.
"""
import json
import math
import sys
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

LEDS = ("lock_led", "warm_led", "white_rice_led", "timer_first_led")
FEATURES = tuple(f"{name}_{stat}" for name in LEDS for stat in ("duty", "edges")) + (
    "surface_c", "slope_c_per_min", "lid_open_fraction")
LABELS = ("inactive", "cooking", "warming", "unknown")
SCHEMA = "a07.raw.v1"
PREPROCESS_VERSION = "a07-preprocess-v3"
# Candidate bound until physical LEDs are measured: 20 s at 20 Hz.
LED_HISTORY_SAMPLES = 400


@dataclass(frozen=True)
class Calibration:
    version: str = "unmeasured-candidate-v1"
    adc_max: int = 4095
    led_off: tuple = (500., 500., 500., 500.)
    led_on: tuple = (3000., 3000., 3000., 3000.)
    series_ohms: float = 10000.
    ntc_r25: float = 10000.
    ntc_beta: float = 3950.
    ntc_to_ground: bool = True
    offset_c: float = 0.

    def validate(self):
        if not isinstance(self.led_off, (tuple, list)) or not isinstance(self.led_on, (tuple, list)) or \
                len(self.led_off) != 4 or len(self.led_on) != 4:
            raise ValueError("four LED calibrations required")
        numbers = (self.adc_max, self.series_ohms, self.ntc_r25, self.ntc_beta,
                   *self.led_off, *self.led_on, self.offset_c)
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                   and math.isfinite(x) for x in numbers):
            raise ValueError("nonfinite or nonnumeric calibration")
        if self.adc_max <= 2 or min(self.series_ohms, self.ntc_r25, self.ntc_beta) <= 0:
            raise ValueError("invalid divider/NTC calibration")
        if type(self.ntc_to_ground) is not bool:
            raise ValueError("divider direction must be boolean")
        if any(abs(a-b) < 100 for a, b in zip(self.led_off, self.led_on)):
            raise ValueError("insufficient LED calibration contrast")
        if any(not 0 <= x <= self.adc_max for x in (*self.led_off, *self.led_on)):
            raise ValueError("LED calibration outside ADC range")


def ntc_c(adc, cal):
    cal.validate()
    a = np.asarray(adc, dtype=float)
    good = np.isfinite(a) & (a > 1) & (a < cal.adc_max-1)
    safe = np.where(good, a, cal.adc_max/2)
    r = cal.series_ohms * safe / (cal.adc_max-safe)
    if not cal.ntc_to_ground:
        r = cal.series_ohms**2 / r
    inv = 1/298.15 + np.log(r/cal.ntc_r25)/cal.ntc_beta
    t = np.where(inv > 0, 1/np.maximum(inv, 1e-9)-273.15+cal.offset_c, np.nan)
    return np.where(good & (t >= -20) & (t <= 130), t, np.nan)


def temperature_adc(c, cal):
    r = cal.ntc_r25 * np.exp(cal.ntc_beta*(1/(np.asarray(c)+273.15)-1/298.15))
    a = cal.adc_max*r/(cal.series_ohms+r)
    return a if cal.ntc_to_ground else cal.adc_max-a


def debounce(t_ms, values, hold_ms=50):
    """Unknown until first stable contact; reset at gaps/reboot/invalid input."""
    out = np.full(len(values), -1, dtype=np.int8)
    stable = candidate = -1
    since = 0
    previous = None
    for i, (t, v) in enumerate(zip(t_ms, values)):
        if previous is not None and (t <= previous or t-previous > 100):
            stable = candidate = -1
        previous = t
        if v not in (0, 1):
            stable = candidate = -1
            continue
        if v != candidate:
            candidate, since = v, t
        if t-since >= hold_ms:
            stable = candidate
        out[i] = stable
    return out


def extract(t_ms, adc, lid, boot, cal, step_seconds=2, sequence=None):
    """20Hz input; 2s features, 20s LED guard, 60s temperature/link history.

    Missing a required channel abstains the model; LED/NTC observations remain
    individually available. Returns features, validity, end sample and LED enums.
    """
    cal.validate()
    t_ms, adc = np.asarray(t_ms), np.asarray(adc, dtype=float)
    lid, boot = np.asarray(lid), np.asarray(boot)
    if adc.shape != (len(t_ms), 5) or lid.shape != t_ms.shape or boot.shape != t_ms.shape:
        raise ValueError("raw array shape mismatch")
    if not np.isfinite(t_ms).all():
        raise ValueError("nonfinite timestamp")
    n = len(t_ms)
    sequence = np.arange(n) if sequence is None else np.asarray(sequence)
    if sequence.shape != t_ms.shape or step_seconds <= 0 or step_seconds*20 < 1:
        raise ValueError("invalid sequence shape/window step")
    if n < 1200:
        return np.empty((0, 11)), np.empty(0, bool), np.empty(0, int), np.empty((0, 4), int)
    normalized = (adc[:, :4]-np.array(cal.led_off))/(np.array(cal.led_on)-cal.led_off)
    optical_good = np.isfinite(adc[:, :4]) & (adc[:, :4] > 1) & (adc[:, :4] < cal.adc_max-1)
    # Hysteresis prevents chatter. No derived label is used as a feature.
    on = np.zeros((n, 4), dtype=bool)
    state = np.zeros(4, dtype=bool)
    known = np.zeros(4, dtype=bool)
    for i in range(n):
        if i == 0 or not (40 <= t_ms[i]-t_ms[i-1] <= 60 and
                          boot[i] == boot[i-1] and sequence[i] == sequence[i-1]+1):
            state[:] = False
            known[:] = False
        good = optical_good[i].copy()
        known &= good
        state &= good
        known |= good & ((normalized[i] > .6) | (normalized[i] < .4))
        state = np.where(normalized[i] > .6, True, np.where(normalized[i] < .4, False, state))
        optical_good[i] = good & known
        on[i] = state & known
    temp = ntc_c(adc[:, 4], cal)
    # NTC acquisition is 1Hz: intermediate storage must not manufacture samples.
    contact = debounce(t_ms, lid)
    ends = np.arange(1199, n, int(step_seconds*20))
    features, valid, observations = [], [], []
    for end in ends:
        a, b = end-39, end-1199
        duty = on[a:end+1].mean(axis=0)
        edges = np.count_nonzero(np.diff(on[a:end+1].astype(np.int8), axis=0), axis=0)
        q = optical_good[a:end+1].all(axis=0)
        h = end-LED_HISTORY_SAMPLES+1
        long_edges = np.count_nonzero(np.diff(on[h:end+1].astype(np.int8), axis=0), axis=0)
        long_link = (np.all((np.diff(t_ms[h:end+1]) >= 40) & (np.diff(t_ms[h:end+1]) <= 60))
                     and np.all(boot[h:end+1] == boot[end])
                     and np.all(np.diff(sequence[h:end+1]) == 1))
        long_good = optical_good[h:end+1].all(axis=0) & long_link
        # Three edges distinguish repeated switching from a single pulse.
        # One/two edges are transitional, not evidence of sustained on/off.
        enums = np.where(long_good, np.where(long_edges >= 3, 3,
                    np.where(long_edges == 0, np.where(on[end], 2, 1), 0)), 0)
        ti = np.arange(b, end+1, 20)
        temps = temp[ti]
        temp_ok = np.isfinite(temps).all()
        x = (t_ms[ti]-t_ms[ti[0]])/60000.
        slope = float(np.dot(x-x.mean(), temps-temps.mean())/np.dot(x-x.mean(), x-x.mean())) if temp_ok else 0.
        timing_ok = np.all((np.diff(t_ms[b:end+1]) >= 40) & (np.diff(t_ms[b:end+1]) <= 60))
        continuous = timing_ok and np.all(boot[b:end+1] == boot[end]) and np.all(np.diff(sequence[b:end+1]) == 1)
        contact_ok = (contact[a:end+1] >= 0).all()
        f = np.column_stack((duty, edges)).ravel().tolist()
        f += [float(temps[-1]) if temp_ok else 0., slope, float(np.maximum(contact[a:end+1], 0).mean())]
        features.append(f)
        # Keep the trained 2 s features unchanged; gate their use with the
        # longer observation. Warm blinking is reheat/ambiguous, not warming.
        valid.append(bool(q.all() and (enums != 0).all() and enums[1] != 3
                          and temp_ok and continuous and contact_ok))
        observations.append(enums)
    return np.asarray(features, np.float32), np.asarray(valid), ends, np.asarray(observations, np.int8)


def read_jsonl(path):
    """Streaming raw log reader: explicit source, labels and per-run versions.

    Caller supplies human labels; no HA test data silently becomes measured data.
    """
    previous = {}
    stream = nullcontext(sys.stdin) if str(path) == '-' else Path(path).open(encoding="utf-8")
    with stream as f:
        for line_no, line in enumerate(f, 1):
            row = json.loads(line)
            required = {"schema", "input_kind", "session_id", "boot_id", "uptime_ms", "sequence",
                        "adc", "lid", "calibration_version", "firmware_version", "label", "note"}
            if not required <= row.keys() or row["schema"] != SCHEMA:
                raise ValueError(f"line {line_no}: missing/invalid schema")
            if row["input_kind"] not in ("synthetic", "measured") or row["label"] not in (*LABELS, None):
                raise ValueError(f"line {line_no}: invalid provenance/label")
            if not row["session_id"] or not row["calibration_version"] or not row["firmware_version"]:
                raise ValueError("run/version metadata required")
            if any(type(row[k]) is not int or row[k] < 0 for k in ("boot_id", "uptime_ms", "sequence")):
                raise ValueError("invalid counter/time")
            if len(row["adc"]) != 5 or any(v is not None and (type(v) is not int or not 0 <= v <= 4095) for v in row["adc"]):
                raise ValueError("ADC must contain 5 nullable 12-bit counts")
            if row["lid"] is not None and type(row["lid"]) is not bool:
                raise ValueError("lid must be nullable boolean")
            key = (row["session_id"], row["boot_id"])
            last = previous.get(key)
            if last and (row["uptime_ms"] <= last[0] or row["sequence"] <= last[1]):
                raise ValueError("duplicate/reversed timestamps or sequence")
            previous[key] = (row["uptime_ms"], row["sequence"])
            yield row


def record_jsonl(source, destination):
    """Validate while recording an existing JSONL stream; never overwrite logs."""
    count = 0
    with Path(destination).open("x", encoding="utf-8") as f:
        for row in read_jsonl(source):
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")
            count += 1
    return count


def calibration_dict(cal):
    return asdict(cal)
