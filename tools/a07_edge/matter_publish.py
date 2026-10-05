"""Prepare an explicitly started A07 synthetic replay in an A-owned A05 Matter app.

The generated app reprocesses raw samples on the board. It publishes the
resulting model/observation fields through the unchanged SnapshotPacked v1
attribute with input_kind=test. No firmware/ or SDK source is edited.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

import numpy as np

from .model import export_c, tree_predict
from .observations import Calibration, extract, temperature_adc, PREPROCESS_VERSION
from .raw_board import cases


STAGES = ("inactive", "cooking", "warming", "unknown")


def _candidate_inputs():
    cal = Calibration()
    n = 1200
    tick = np.arange(n)
    for name, case in cases():
        if len(case["t"]) >= n:
            for end in (n, len(case["t"])):
                start = end - n
                yield name, {k: value[start:end].copy() if isinstance(value, np.ndarray) else value
                             for k, value in case.items()}
    # Extra stable combinations cover classes that the board parity cases did
    # not happen to exercise. Selection still uses the actual trained model.
    for pattern in range(16):
        for start_c, end_c in ((25, 25), (30, 75), (45, 85), (65, 65), (65, 35)):
            adc = np.column_stack([
                np.full(n, 3000 if pattern & (1 << bit) else 500) for bit in range(4)
            ] + [np.rint(temperature_adc(np.linspace(start_c, end_c, n), cal))]).astype(float)
            yield f"led{pattern:04b}_{start_c}_{end_c}", {
                "t": tick * 50, "adc": adc, "lid": np.zeros(n, dtype=int),
                "boot": np.ones(n, dtype=int), "seq": tick.copy(), "cal": cal,
            }
    adc = np.full((n, 5), 500., dtype=float)
    adc[:, 4] = np.nan
    yield "ntc_missing_unknown", {"t": tick * 50, "adc": adc,
           "lid": np.zeros(n, dtype=int), "boot": np.ones(n, dtype=int),
           "seq": tick.copy(), "cal": cal}


def select_stages(model):
    found = {}
    for name, data in _candidate_inputs():
        x, valid, _, _ = extract(data["t"], data["adc"], data["lid"],
                                  data["boot"], data["cal"], sequence=data["seq"])
        if len(x) != 1:
            continue
        prediction, _ = tree_predict(model["nodes"], x, valid, model["score_threshold"])
        label = int(prediction[0])
        if label not in found and (bool(valid[0]) if label != 3 else not bool(valid[0])):
            found[label] = (name, data)
        if len(found) == 4:
            return [found[i] for i in range(4)]
    missing = [STAGES[i] for i in range(4) if i not in found]
    raise ValueError(f"Model did not produce required test states: {', '.join(missing)}")


def _calibration_literal(cal):
    values = (*cal.led_off, *cal.led_on, cal.adc_max, cal.series_ohms,
              cal.ntc_r25, cal.ntc_beta, cal.offset_c)
    return "{{" + ",".join(f"{v:.17g}" for v in values[:4]) + "},{" + \
           ",".join(f"{v:.17g}" for v in values[4:8]) + "}," + \
           ",".join(f"{v:.17g}" for v in values[8:]) + "," + \
           str(int(cal.ntc_to_ground)) + "}"


def _raw_header(stages):
    lines = ["#pragma once", "#include <cstdint>",
             "struct A07Raw { uint32_t ms, sequence, boot; uint16_t adc[5]; int8_t lid; };",
             "struct A07Stage { const char *name; a07::Calibration cal; const A07Raw *raw; unsigned count; int expected; };"]
    refs = []
    for index, (name, data) in enumerate(stages):
        rows = []
        for ms, seq, boot, adc, lid in zip(data["t"], data["seq"], data["boot"],
                                            data["adc"], data["lid"]):
            packed = ",".join(str(int(v)) if np.isfinite(v) else "65535" for v in adc)
            rows.append(f"{{{int(ms)},{int(seq)},{int(boot)},{{{packed}}},{int(lid)}}}")
        lines.append(f"static const A07Raw a07_raw_{index}[] = {{" + ",\n".join(rows) + "};")
        refs.append("{" + f'"{name}",{_calibration_literal(data["cal"])},'
                    f"a07_raw_{index},{len(rows)},{index}" + "}")
    lines.append("static const A07Stage a07_stages[] = {" + ",".join(refs) + "};")
    return "\n".join(lines) + "\n"


def prepare(model_path, app):
    app = Path(app).resolve()
    parts = [part.casefold() for part in app.parts]
    if "firmware" in parts or "local-data" not in parts:
        raise ValueError("Use an A-owned disposable local-data app outside firmware/")
    source = app / "src/app_task.cpp"
    if not source.is_file():
        raise ValueError("Prepare the existing A05 probe app first")
    text = source.read_text(encoding="utf-8")
    required = ("uint32_t sReportCounter", "constexpr chip::EndpointId",
                "sLidOpen = !pressed;\n            PublishSnapshot(false);",
                "k_timer_start(&sProbeTimer, K_SECONDS(10), K_SECONDS(15));")
    if any(marker not in text for marker in required) or "A07_SYNTHETIC_MATTER" in text:
        raise ValueError("A05 app source does not match the expected unmodified test probe")
    model = json.loads(Path(model_path).read_text(encoding="utf-8"))
    if model.get("input_kind") != "synthetic" or model.get("live_qualified") is not False or \
       not str(model.get("version", "")).startswith("a07-"):
        raise ValueError("Only a versioned, non-live synthetic A07 model is allowed")
    schema = app / 'src/default_zap/NordicDevKitCluster.xml'
    if not schema.exists() or 'SnapshotHistory' not in schema.read_text(encoding='utf-8'):
        raise ValueError("Regenerate a fresh A05 app with SnapshotHistory before preparing A07")
    stages = select_stages(model)
    shutil.copyfile(Path(__file__).parents[1]/'a05_contract/history.hpp', app/'src/a05_history.hpp')
    if '#include "a05_history.hpp"' not in text:
        text = '#include "a05_history.hpp"\n' + text
    out = app / "src/a07"
    out.mkdir(exist_ok=True)
    export_c(model, out / "a07_model.h")
    shutil.copyfile(Path(__file__).with_name("preprocess.hpp"), out / "preprocess.hpp")
    shutil.copyfile(Path(__file__).with_name("a07_snapshot.hpp"), out / "a07_snapshot.hpp")
    (out / "a07_replay_stages.hpp").write_text(_raw_header(stages), encoding="utf-8")
    snippet = Path(__file__).with_name("matter_publish.cpp.inc").read_text(encoding="utf-8")
    begin, end = text.index(required[0]), text.index(required[1])
    text = text[:begin] + snippet + "\n" + text[end:]
    text = text.replace("#include <zephyr/sys/byteorder.h>",
                        '#include <zephyr/sys/byteorder.h>\n#include "a07/preprocess.hpp"\n'
                        '#include "a07/a07_snapshot.hpp"\n#include "a07/a07_model.h"\n'
                        '#include "a07/a07_replay_stages.hpp"')
    text = text.replace(required[2], "if (pressed) StartA07Replay();")
    text = text.replace(required[3], "// A07 replay starts only after Button 1 is pressed.")
    source.write_text(text, encoding="utf-8")
    cmake = app / "CMakeLists.txt"
    cmake_text = cmake.read_text(encoding="utf-8")
    for old in ("target_sources(app PRIVATE src/a07/selftest.cpp)",
                "target_sources(app PRIVATE src/a07/raw_selftest.cpp)"):
        cmake_text = cmake_text.replace(old, "")
    cmake.write_text(cmake_text, encoding="utf-8")
    manifest = {"model_version": model["version"], "preprocess_version": PREPROCESS_VERSION,
                "input_kind": "test",
                "synthetic_replay": True, "stage_seconds": 15,
                "stages": [{"expected": STAGES[i], "raw_source": name,
                            "samples": len(data["t"])} for i, (name, data) in enumerate(stages)],
                "scope": "board preprocessing/model to Matter; not real sensor performance"}
    (out / "a07_matter_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--app", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.model, args.app), indent=2))
