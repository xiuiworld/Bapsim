"""Generate raw replay fixtures and an independent Python oracle for the board."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import shutil
import numpy as np
from .observations import Calibration, extract, temperature_adc, PREPROCESS_VERSION
from .model import tree_predict


def cases():
    n = 2520
    i = np.arange(n)
    cal = Calibration()
    adc = np.column_stack([np.full(n, 3000), np.full(n, 500),
                           np.full(n, 3000), np.full(n, 500),
                           np.rint(temperature_adc(30+i/400, cal))]).astype(float)
    base = dict(t=i*50, adc=adc, lid=np.zeros(n, dtype=int),
                boot=np.ones(n, dtype=int), seq=i.copy(), cal=cal)
    for name in ('steady_ramp', 'warming', 'cooling', 'blink_and_bounce', 'inverted_led', 'warmup',
                 'missing_led', 'ntc_fault', 'contact_fault', 'time_gap',
                 'sequence_gap', 'boot_restart', 'hysteresis_edges', 'reversed_ntc',
                 'gap_midpoint_on', 'gap_midpoint_off', 'missing_midpoint', 'startup_midpoint'):
        d = {k: v.copy() if isinstance(v, np.ndarray) else v for k, v in base.items()}
        if name == 'warming':
            d['adc'][:, :4] = [500, 3000, 500, 500]
            d['adc'][:, 4] = np.rint(temperature_adc(np.full(n, 65.), cal))
        elif name == 'cooling':
            d['adc'][:, :4] = 500
            d['adc'][:, 4] = np.rint(temperature_adc(65-i/400, cal))
        elif name == 'blink_and_bounce':
            d['adc'][:, 1] = np.where((i//10)%2, 3000, 500)
            d['lid'][1160:1170] = i[1160:1170]%2
            d['lid'][1170:] = 1
        elif name == 'inverted_led':
            d['cal'] = replace(cal, led_off=(3000.,)*4, led_on=(500.,)*4)
            d['adc'][:, :4] = 3500-d['adc'][:, :4]
        elif name == 'warmup':
            d = {k: v[:1199] if isinstance(v, np.ndarray) else v for k, v in d.items()}
        elif name == 'missing_led':
            d['adc'][1180:1210, 0] = np.nan
            d['adc'][1230:1250, 2] = 4095
        elif name == 'ntc_fault':
            d['adc'][1200, 4] = np.nan
            d['adc'][1220, 4] = 0
            d['adc'][1240, 4] = 4095
        elif name == 'contact_fault':
            d['lid'][1180:1250] = -1
        elif name == 'time_gap':
            d['t'][1200:] += 200
        elif name == 'sequence_gap':
            d['seq'][1200:] += 1
        elif name == 'boot_restart':
            d['boot'][1200:] = 2
            d['t'][1200:] -= 60000
            d['seq'][1200:] -= 1200
        elif name == 'hysteresis_edges':
            d['adc'][:, 0] = np.resize([1499, 1500, 1501, 1999, 2000, 2001], n)
            d['adc'][:, 1] = np.where(i%40 < 36, 3000, 500)
            d['adc'][:, 2] = np.where(i%40 < 4, 3000, 500)
        elif name == 'reversed_ntc':
            d['cal'] = replace(cal, ntc_to_ground=False, offset_c=1.25)
            d['adc'][:, 4] = 4095-d['adc'][:, 4]
        elif name in ('gap_midpoint_on', 'gap_midpoint_off', 'missing_midpoint', 'startup_midpoint'):
            d['adc'][:1200, 0] = 500 if name == 'gap_midpoint_off' else 3000
            d['adc'][1200:, 0] = 1750
            if name.startswith('gap_'):
                d['t'][1200:] += 200
            elif name == 'missing_midpoint':
                d['adc'][1200, 0] = np.nan
            else:
                d['adc'][:, 0] = 1750
        yield name, d


def prepare(model_path, app):
    app = Path(app).resolve()
    if 'firmware' in [p.lower() for p in app.parts] or 'local-data' not in app.parts:
        raise ValueError('A disposable local-data app is required')
    out = app/'src/a07'
    if not (out/'a07_model.h').exists():
        raise ValueError('Run a07_edge.board first with the same model')
    model = json.loads(Path(model_path).read_text())
    from .model import export_c
    export_c(model, out/'a07_model.h')
    shutil.copyfile(Path(__file__).with_name('preprocess.hpp'), out/'preprocess.hpp')
    lit = lambda v: format(float(v), '.17e')
    lines = ['struct Raw { uint32_t ms, sequence, boot; uint16_t adc[5]; int8_t lid; };',
             'struct Expected { float x[11]; uint8_t led[4]; bool valid; int cls; float score; };']
    refs, report = [], []
    for ci, (name, d) in enumerate(cases()):
        x, valid, ends, leds = extract(d['t'], d['adc'], d['lid'], d['boot'], d['cal'], sequence=d['seq'])
        predicted, scores = tree_predict(model['nodes'], x, valid, model['score_threshold'])
        rows = []
        for t, seq, boot, adc, lid in zip(d['t'], d['seq'], d['boot'], d['adc'], d['lid']):
            packed = ','.join(str(int(v)) if np.isfinite(v) else '65535' for v in adc)
            rows.append(f'{{{t},{seq},{boot},{{{packed}}},{lid}}}')
        lines.append(f'static const Raw raw_{ci}[] = {{'+',\n'.join(rows)+'};')
        erows = []
        for row, led, v, p, score in zip(x, leds, valid, predicted, scores):
            erows.append('{{'+','.join(lit(f)+'f' for f in row)+'},{'+','.join(map(str, led))+
                         '},'+f'{int(v)},{int(p)},{lit(score)}f'+'}')
        lines.append(f'static const Expected expected_{ci}[] = {{'+(',\n'.join(erows) if erows else '{}')+'};')
        c = d['cal']
        calstr = '{{'+','.join(map(lit, c.led_off))+'},{'+','.join(map(lit, c.led_on))+'},'+','.join(map(lit,
            (c.adc_max, c.series_ohms, c.ntc_r25, c.ntc_beta, c.offset_c)))+','+str(int(c.ntc_to_ground))+'}'
        refs.append(f'{{"{name}",{calstr},raw_{ci},{len(d["t"])},expected_{ci},{len(x)}}}')
        report.append({'case': name, 'samples': len(d['t']), 'windows': len(x), 'valid': int(valid.sum()),
                       'invalid': int((~valid).sum()), 'classes': np.bincount(predicted, minlength=4).tolist()})
    lines.append('struct ReplayCase { const char *name; a07::Calibration cal; const Raw *raw; unsigned samples; const Expected *expected; unsigned windows; };')
    lines.append('static const ReplayCase cases[] = {'+','.join(refs)+'};')
    (out/'raw_vectors.h').write_text('\n'.join(lines))
    shutil.copyfile(Path(__file__).with_name('raw_selftest.cpp'), out/'raw_selftest.cpp')
    shutil.copyfile(Path(__file__).with_name('blink_selftest.hpp'), out/'blink_selftest.hpp')
    cmake = app/'CMakeLists.txt'
    marker = 'target_sources(app PRIVATE src/a07/raw_selftest.cpp)'
    text = cmake.read_text()
    if marker not in text:
        cmake.write_text(text+'\n'+marker+'\n')
    result = {'model': model['version'], 'preprocess': PREPROCESS_VERSION, 'cases': report,
              'safety': {'rejected': 55, 'accepted': 10, 'recovery': 28},
              'blink': {'cases': 220, 'windows': 6860},
              'tolerance': {'absolute': 0.00002, 'relative': 0.000002}, 'measured': False}
    (out/'raw_manifest.json').write_text(json.dumps(result, indent=2))
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--model', required=True)
    p.add_argument('--app', required=True)
    args = p.parse_args()
    print(json.dumps(prepare(args.model, args.app), indent=2))
