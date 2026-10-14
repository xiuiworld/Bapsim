"""Read-only, bounded-memory replay of CRC-recovered physical samples.

No training, inferred labels, resampling or live ingestion. Counts describe
coverage/predictions, not accuracy. Run from the repository root as a module.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3

import numpy as np
from tools.a07_edge.observations import Calibration, extract, LABELS
from tools.a07_edge.model import tree_predict, rule_predict

FIELDS = ('uptime_ms', 'sequence', 'boot', 'lock_raw', 'warm_raw',
          'white_rice_raw', 'reservation_raw', 'ntc_raw', 'vdd_raw', 'lid_open')


def replay(db_path, output, model_path, calibration_path):
    model = json.loads(model_path.read_text())
    cal = Calibration(**json.loads(calibration_path.read_text()))
    output.mkdir(exist_ok=False, parents=True)
    db = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
    result = {'model': model['version'], 'calibration': cal.version,
              'model_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(),
              'source': str(db_path), 'accuracy': None, 'training_performed': False,
              'limitation': 'Archive snapshot only. No independent state labels; window counts are correlated.',
              'boots': {}}
    groups = db.execute('select distinct device,boot from samples order by device,boot').fetchall()
    for device, boot in groups:
        counts, rules, quality, statuses = Counter(), Counter(), Counter(), Counter()
        total = faults = gaps = 0
        previous = None
        tail = np.empty((0, len(FIELDS)))
        batch = []
        def consume():
            nonlocal tail
            if not batch:
                return
            a = np.concatenate((tail, np.asarray(batch, dtype=float)))
            with np.errstate(divide='ignore', invalid='ignore'):
                adc = a[:, 3:8] / a[:, 8, None] * 4095
            # Match rail/supply checks in Sensors::read; archived status failures
            # were already replaced with NaN while reading below.
            bad = (a[:, 3:8] <= 4) | (a[:, 3:8] >= 4091) | (a[:, 3:8] >= a[:, 8, None])
            supply_mv = np.floor(a[:, 8] * 3600 / 4096)
            bad |= ((supply_mv < 1700) | (supply_mv > 3600) | (a[:, 8] >= 4091))[:, None]
            adc[bad] = np.nan
            x, valid, ends, led = extract(a[:, 0], adc, a[:, 9], a[:, 2], cal, sequence=a[:, 1])
            keep = ends >= len(tail)
            x, valid, led = x[keep], valid[keep], led[keep]
            pred, _ = tree_predict(model['nodes'], x, valid, model['score_threshold'])
            rule = rule_predict(x, valid)
            counts.update(LABELS[int(v)] for v in pred)
            rules.update(LABELS[int(v)] for v in rule)
            quality['valid'] += int(valid.sum())
            quality['invalid'] += int((~valid).sum())
            quality['all_led_unknown'] += int((led == 0).all(axis=1).sum())
            tail = a[-1200:]
            batch.clear()
        for value, in db.execute('select value from samples where device=? and boot=? order by seq', (device, boot)):
            row = json.loads(value)
            total += 1
            if previous:
                ds, dt = row['sequence']-previous[0], row['uptime_ms']-previous[1]
                gaps += max(0, ds-1)
                faults += ds == 1 and not 40 <= dt <= 60
            previous = row['sequence'], row['uptime_ms']
            values = [row[f] for f in FIELDS]
            for i, name in enumerate(('lock', 'warm', 'white_rice', 'reservation', 'ntc')):
                if row.get(name+'_status', 0):
                    statuses[name] += 1
                    values[3+i] = float('nan')
            batch.append(values)
            if len(batch) == 120000:
                consume()
        consume()
        key = f'{device}:{boot}'
        result['boots'][key] = dict(samples=total, missing_sequences=gaps,
            timing_faults=faults, driver_errors=dict(statuses), model=dict(counts),
            rule=dict(rules), quality=dict(quality))
        (output/'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(key, json.dumps(result['boots'][key]), flush=True)
    db.close()
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=Path('firmware/a08/model.json'))
    parser.add_argument('--calibration', type=Path, required=True)
    args = parser.parse_args()
    replay(args.database, args.output, args.model, args.calibration)
