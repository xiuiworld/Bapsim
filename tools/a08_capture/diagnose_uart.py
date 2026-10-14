"""Bounded, read-only UART diagnostic. Does not reset, flash or send commands."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import serial
import numpy as np

ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
SAMPLE = re.compile(r'A08 SAMPLE boot=(\d+) seq=(\d+) ms=(\d+) raw=(-?\d+,-?\d+,-?\d+,-?\d+,-?\d+) supply=(-?\d+) lid=(-?\d+)$')
REPORT = re.compile(r'A08 R c=(\d+) p=(\d+) h=(\d+) l=(\d+) f=(\d+) t=(-?\d+) led=([0-3],[0-3],[0-3],[0-3])$')


def summarize(path):
    counts, led, lids = Counter(), Counter(), Counter()
    samples, timing, acquisition, errors = [], [], [], []
    reports = []
    with path.open(encoding='utf-8') as f:
        for line in f:
            row = json.loads(line)
            text = row['line']
            m = SAMPLE.search(text)
            if m:
                samples.append([int(m[1]), int(m[2]), int(m[3]), *map(int,m[4].split(',')),int(m[5]),int(m[6])])
            m = REPORT.search(text)
            if m:
                counts[m[5]] += 1; led[m[7]] += 1; lids[m[4]] += 1
                reports.append({'utc':row['utc'], 'counter':int(m[1]), 'phase':int(m[5]), 'lid':int(m[4]),'led':m[7]})
                if (m[2],m[3]) != ('0','0'):
                    errors.append(text)
            m = re.search(r'A08 TIMING samples=200 min_ms=(\d+) max_ms=(\d+) gaps=(\d+)',text)
            if m: timing.append(list(map(int,m.groups())))
            m = re.search(r'A08 ACQUIRE read_max_us=(\d+) late_max_ms=(\d+) queue_dropped=(\d+)',text)
            if m: acquisition.append(list(map(int,m.groups())))
            if '<err>' in text or 'dropped' in text.lower() and 'queue_dropped=' not in text:
                errors.append(text)
    result = dict(reports=len(reports),phase=dict(counts),led=dict(led),lid=dict(lids),
                  timing=timing,acquisition=acquisition,errors=errors,accuracy=None,
                  reason='Requires separately recorded operator ground truth; no labels inferred.')
    if samples:
        a=np.asarray(samples,dtype=np.int64)
        same=a[1:,0]==a[:-1,0]; ds=np.diff(a[:,1]); dt=np.diff(a[:,2])
        good=(a[:,8]>0)&(a[:,8]<4091)
        result.update(samples=len(a),boots=dict(Counter(map(int,a[:,0]))),
                      timing_faults=int((same&(ds==1)&((dt<40)|(dt>60))).sum()),
                      missing_sequences=int(np.maximum(ds[same]-1,0).sum()),
                      normalized_adc_quantiles=np.quantile(a[good,3:8]/a[good,8,None]*4095,[.05,.5,.95],axis=0).tolist())
    path.with_suffix('.summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    path.with_suffix('.reports.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2),flush=True)
    return result


def capture(path, seconds, port):
    pending=b''; deadline=time.monotonic()+seconds
    with serial.Serial(port,115200,timeout=.1) as connection, path.open('x',encoding='utf-8') as out:
        print('Capturing diagnostic until bounded deadline',flush=True)
        while time.monotonic()<deadline and not path.with_suffix('.stop').exists():
            pending+=connection.read(max(1,connection.in_waiting))
            while b'\n' in pending:
                raw,pending=pending.split(b'\n',1)
                text=ANSI.sub('',raw.decode(errors='replace')).strip()
                out.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(),'line':text})+'\n')
            out.flush()
    return summarize(path)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output',type=Path); p.add_argument('--seconds',type=float,default=120)
    p.add_argument('--port',default='COM8'); p.add_argument('--summarize',action='store_true')
    a=p.parse_args()
    if a.summarize: summarize(a.output)
    else:
        if not 0<a.seconds<=3600: p.error('seconds must be within 0..3600')
        capture(a.output,a.seconds,a.port)
