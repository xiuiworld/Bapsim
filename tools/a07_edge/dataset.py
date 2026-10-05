"""Seeded, hypothesis-based sensor excerpts; never a B lifestyle simulator."""
import hashlib
import json
from pathlib import Path

import numpy as np

from . import VERSION
from .observations import Calibration, LABELS, SCHEMA, calibration_dict, extract, temperature_adc

SCENARIOS = ("idle", "menu_select", "reservation", "white_cooking", "other_cooking",
             "warming", "reheat", "cooling", "start_cooking", "finish_cooking",
             "cancel_cooking", "ambiguous")


def episode(seed, scenario, cal, seconds=180, stress=False):
    rng = np.random.default_rng(seed)
    n = seconds*20
    t = np.arange(n)/20
    boundary = rng.uniform(65, 130)
    # Latent operating state chosen first; never threshold generated temperature.
    mode = np.full(n, 0, dtype=np.int8)
    if scenario in ("white_cooking", "other_cooking"):
        mode[:] = 1
    elif scenario == "warming":
        mode[:] = 2
    elif scenario == "reheat" or scenario == "ambiguous":
        mode[:] = 3  # reheat mapping unconfirmed; must not call it fresh cooking
    elif scenario == "start_cooking":
        mode[t >= boundary] = 1
    elif scenario == "finish_cooking":
        mode[:] = 1
        mode[t >= boundary] = 2
    elif scenario == "cancel_cooking":
        mode[:] = 1
        mode[t >= boundary] = 0
    pattern = np.zeros((n, 4), dtype=float)
    freq = rng.uniform(.6, 1.8) if not stress else rng.uniform(.15, 3.5)
    blink = ((t*freq+rng.random()) % 1 < rng.uniform(.3, .7)).astype(float)
    pattern[:, 0] = 1 if scenario not in ("idle", "cooling") else rng.integers(2)
    pattern[:, 1] = mode == 2
    if scenario == "reheat":
        pattern[:, 1] = blink
    if scenario in ("white_cooking", "start_cooking", "finish_cooking", "cancel_cooking"):
        pattern[:, 2] = mode == 1  # UNVERIFIED steady-cooking lamp assumption
    if scenario == "menu_select":
        pattern[:, 2] = blink
    if scenario == "reservation":
        pattern[:, 2] = rng.integers(2)
        pattern[:, 3] = rng.integers(2)  # first time LED alone is insufficient
    if scenario == "other_cooking":
        pattern[:, 3] = rng.integers(2)
    if scenario == "ambiguous":
        pattern[:, 0] = 1  # deliberately identical partial LED evidence
    ambient = rng.uniform(16, 32) if not stress else rng.uniform(5, 40)
    hot = rng.uniform(43, 90)
    warm = rng.uniform(32, 65)
    tau = rng.uniform(70, 650) * (rng.uniform(1.5, 3) if stress else 1)
    temp = np.zeros(seconds)
    temp[0] = rng.uniform(ambient, max(ambient+1, hot))
    if scenario in ("idle", "menu_select", "reservation", "start_cooking"):
        temp[0] = ambient+rng.uniform(0, 8)
    if scenario in ("cooling", "cancel_cooking"):
        temp[0] = hot
    for s in range(1, seconds):
        m = mode[s*20]
        target = hot if m == 1 or scenario == "reheat" else warm if m == 2 else ambient
        temp[s] = temp[s-1]+(target-temp[s-1])/tau+rng.normal(0, .025)
    temp += rng.normal(0, .12, seconds)
    optical_offset = np.asarray(cal.led_off)+rng.normal(0, 25, 4)
    contrast = np.asarray(cal.led_on)-np.asarray(cal.led_off)
    drift = rng.uniform(0, 100 if not stress else 850)*np.sin(t*rng.uniform(.008, .08))
    light = optical_offset+pattern*contrast+drift[:, None]+rng.normal(0, 18 if not stress else 110, (n, 4))
    adc = np.empty((n, 5), np.int16)
    adc[:, :4] = np.clip(np.rint(light), 0, 4095).astype(np.int16)
    adc[:, 4] = np.repeat(np.clip(np.rint(temperature_adc(temp, cal)), 0, 4095).astype(np.int16), 20)
    lid = np.zeros(n, dtype=np.int8)
    if scenario in ("idle", "cooling", "warming"):
        lid[int(boundary*20):int((boundary+15)*20)] = 1
        j = int(boundary*20)
        lid[j:j+4] = [1, 0, 1, 1]
    uptime = np.arange(n, dtype=np.int64)*50
    boot = np.full(n, seed, dtype=np.int64)
    fault = "none"
    if rng.random() < (.18 if not stress else .6):
        fault = str(rng.choice(["missing_adc", "rail_adc", "reboot", "gap", "lid_error"]))
        start = int(rng.uniform(65, 120)*20)
        stop = min(start+int(rng.uniform(4, 20)*20), n)
        if fault in ("missing_adc", "rail_adc"):
            adc[start:stop, rng.integers(5)] = -1 if fault == "missing_adc" else 4095
        elif fault == "reboot":
            boot[start:] += 1
            uptime[start:] -= uptime[start]
        elif fault == "gap":
            uptime[start:] += 5000
        else:
            lid[start:stop] = -1
    # Scalar operation label is deliberately independent of sensor faults.
    return dict(t_ms=uptime, adc=adc, lid=lid, boot=boot, label=mode), fault


def generate(output, profiles=120, per_profile=20, stress_profiles=30, seed=7307):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be empty; keep runs reproducible")
    if profiles < 10 or per_profile < 12 or stress_profiles < 1:
        raise ValueError("need >=10 profiles, >=12 excerpts/profile, >=1 stress profile")
    (output/"raw").mkdir(parents=True, exist_ok=True)
    rows, fx, fy, fv, fs, fg, fe = [], [], [], [], [], [], []
    rng = np.random.default_rng(seed)
    manifest = {"version": VERSION, "input_kind": "synthetic", "seed": seed,
                "target_model": "CRP-G1020MP", "manual_model_match_verified": False,
                "optical_hz": 20, "temperature_hz": 1, "excerpt_seconds": 180,
                "feature_window_seconds": [2, 60], "feature_step_seconds": 2,
                "notes": "Independent 3-minute excerpts, NOT complete cook cycles or measured hours.",
                "scenarios": list(SCENARIOS), "profiles": {}}
    index = 0
    for profile in range(profiles+stress_profiles):
        stress = profile >= profiles
        split = "stress" if stress else "train" if profile < int(profiles*.7) else "validation" if profile < int(profiles*.85) else "test"
        off = rng.uniform(250, 800, 4)
        on = off+rng.uniform(1300, 2300, 4)
        # Polarity may differ between divider layouts; calibration captures it.
        reverse = rng.random(4) < .3
        cal = Calibration(version=f"synthetic-profile-{profile}", led_off=tuple(np.where(reverse, on, off)),
                          led_on=tuple(np.where(reverse, off, on)), offset_c=float(rng.uniform(-1, 1)))
        manifest["profiles"][str(profile)] = {"split": split, "calibration": calibration_dict(cal)}
        for run in range(per_profile):
            e_seed = seed*100000+index
            scenario = SCENARIOS[run % len(SCENARIOS)]
            data, fault = episode(e_seed, scenario, cal, stress=stress)
            x, valid, ends, leds = extract(data["t_ms"], data["adc"], data["lid"], data["boot"], cal)
            session = f"a07-{profile:03d}-{run:03d}"
            raw_path = output/"raw"/(session+".npz")
            np.savez_compressed(raw_path, **data)
            fx.append(x); fy.append(data["label"][ends]); fv.append(valid)
            fs.extend([split]*len(x)); fg.extend([profile]*len(x)); fe.extend([session]*len(x))
            rows.append({"session_id": session, "parent_session_id": session, "profile": profile,
                         "split": split, "seed": e_seed, "scenario": scenario, "fault": fault,
                         "input_kind": "synthetic", "generator_version": VERSION,
                         "firmware_version": "not-a-device", "calibration_version": cal.version,
                         "path": str(raw_path.relative_to(output)), "samples": len(data["t_ms"]),
                         "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest()})
            index += 1
        if (profile+1) % 10 == 0:
            print(f"generated {index} excerpts", flush=True)
    np.savez_compressed(output/"features.npz", x=np.concatenate(fx), y=np.concatenate(fy),
                        valid=np.concatenate(fv), split=np.asarray(fs), profile=np.asarray(fg), session=np.asarray(fe))
    manifest.update(excerpts=len(rows), raw_samples=sum(r["samples"] for r in rows), windows=len(fs))
    (output/"manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (output/"sessions.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows), encoding="utf-8")
    export_jsonl(output, rows[0]["session_id"], output/"raw-example.jsonl")
    return manifest


def export_jsonl(root, session, destination):
    root = Path(root)
    rows = [json.loads(l) for l in (root/"sessions.jsonl").read_text().splitlines()]
    meta = next(r for r in rows if r["session_id"] == session)
    data = np.load(root/meta["path"])
    sequences = {}
    with Path(destination).open("x", encoding="utf-8") as f:
        for i in range(len(data["t_ms"])):
            boot = int(data["boot"][i]); sequences[boot] = sequences.get(boot, 0)+1
            row = {"schema": SCHEMA, "input_kind": "synthetic", "session_id": session,
                   "boot_id": boot, "sequence": sequences[boot], "uptime_ms": int(data["t_ms"][i]),
                   "adc": [None if int(a) < 0 else int(a) for a in data["adc"][i]],
                   "lid": None if data["lid"][i] < 0 else bool(data["lid"][i]),
                   "calibration_version": meta["calibration_version"], "firmware_version": "not-a-device",
                   "label": LABELS[int(data["label"][i])], "note": meta["scenario"]}
            f.write(json.dumps(row)+"\n")
