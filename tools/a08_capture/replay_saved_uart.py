"""Replay saved UART files only; no serial port, resampling, training or HA writes."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from firmware.a08.profile import load_profile
from tools.a07_edge.model import tree_predict_masked
from tools.a07_edge.observations import LABELS, extract
from .diagnose_uart import SAMPLE


def replay(paths, profile_path, model_path, output):
    profile, cal = load_profile(profile_path)
    mask = 15 if profile['timer_trusted'] else 7
    available = ((1 << 11)-1) if mask == 15 else ((1 << 11)-1) & ~(3 << 6)
    model = json.loads(Path(model_path).read_text())
    result = {'model': model['version']+('-mask1' if mask!=15 else ''), 'profile': profile,
              'model_sha256': hashlib.sha256(Path(model_path).read_bytes()).hexdigest(),
              'accuracy': None, 'training_performed': False,
              'scope': 'Saved files replayed separately; windows overlap. No inferred truth labels.', 'sources': []}
    for path in map(Path, paths):
        groups = {}
        for line in path.read_text(encoding='utf-8').splitlines():
            row = json.loads(line); m = SAMPLE.search(row['line'])
            if m:
                values = [int(m[2]),int(m[3]),int(m[6]),*map(int,m[4].split(',')),int(m[5])]
                groups.setdefault(int(m[1]), []).append((row['utc'],values))
        entry = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'boots': []}
        for boot, rows in groups.items():
            a=np.asarray([r[1] for r in rows],dtype=float)
            supply=a[:,8]; raw=a[:,3:8]
            with np.errstate(divide='ignore',invalid='ignore'): adc=raw/supply[:,None]*4095
            bad=(raw<=4)|(raw>=4091)|(raw>=supply[:,None])
            mv=np.floor(supply*3600/4096)
            bad|=((supply<=4)|(supply>=4091)|(mv<1700)|(mv>3600))[:,None];adc[bad]=np.nan
            x,valid,ends,led=extract(a[:,1],adc,a[:,2],np.full(len(a),boot),cal,
                                     sequence=a[:,0],trusted_led_mask=mask)
            pred,scores=tree_predict_masked(model['nodes'],x,valid,model['score_threshold'],available)
            timer_unknown=bool((led[:,3]==0).all()) if mask==7 else None
            windows=[{'utc':rows[int(e)][0],'sequence':int(a[e,0]),'valid':bool(v),
                      'phase':LABELS[int(p)],'score':float(s),'led':l.tolist()}
                     for e,v,p,s,l in zip(ends,valid,pred,scores,led)]
            entry['boots'].append({'boot':boot,'samples':len(a),'windows':len(windows),
                'phase_counts':dict(Counter(LABELS[int(v)] for v in pred)),
                'invalid_windows':int((~valid).sum()),'timer_always_unknown':timer_unknown,'results':windows})
        result['sources'].append(entry)
    with Path(output).open('x',encoding='utf-8') as f: json.dump(result,f,indent=2)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('sources',nargs='+'); p.add_argument('--profile',default='firmware/a08/site_profile.json')
    p.add_argument('--model',default='firmware/a08/model.json');p.add_argument('--output',required=True)
    a=p.parse_args();r=replay(a.sources,a.profile,a.model,a.output)
    for source in r['sources']:
        print(source['path'],[{k:v for k,v in b.items() if k!='results'} for b in source['boots']])
