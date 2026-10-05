"""Frozen synthetic evaluation of the long LED guard, not a measured accuracy claim.

No model training or threshold selection. The comparison reuses identical 2 s
features and the existing model; clean periodic inputs isolate the new guard.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from .observations import Calibration, PREPROCESS_VERSION, extract, temperature_adc
from .model import tree_predict
from .blink_fixture import periodic_on


def evaluate(model_path):
    model_bytes=Path(model_path).read_bytes()
    model=json.loads(model_bytes)
    cal=Calibration()
    t=np.arange(3600)*50
    rows=[]
    groups={
        'design': ((.1,.15,.25,.5,1.,2.),(.1,.5,.9),(0.,.23,.71)),
        'independent': ((.11,.13,.17,.23,.37,.73,1.3,1.9),(.15,.35,.65,.85),(.07,.29,.61)),
        'outside_candidate_range': ((.025,.05),(.1,.5,.9),(0.,.23,.71)),
    }
    for group,(frequencies,duties,phases) in groups.items():
        for hz in frequencies:
            for duty in duties:
                for phase in phases:
                    on=periodic_on(t,hz,duty,phase)
                    adc=np.tile([500.,500.,500.,500.,float(temperature_adc(55.,cal))],(len(t),1))
                    adc[:,1]=np.where(on,3000.,500.)
                    x,valid,ends,led=extract(t,adc,np.zeros(len(t)),np.ones(len(t)),cal)
                    # All inputs here have valid optical/thermal/contact/link
                    # data. v2 passed every such window to the existing model.
                    before,_=tree_predict(model['nodes'],x,np.ones(len(x),bool),model['score_threshold'])
                    after,_=tree_predict(model['nodes'],x,valid,model['score_threshold'])
                    rows.append(dict(group=group,hz=hz,duty=duty,phase=phase,windows=len(x),
                        before_warming=int((before==2).sum()),after_warming=int((after==2).sum()),
                        before_accepted=int((before!=3).sum()),after_accepted=int((after!=3).sum()),
                        blink=int((led[:,1]==3).sum()),uncertain=int((led[:,1]==0).sum())))
    transitions=[]
    for initial,target in ((False,True),(True,False)):
        on=np.full(len(t),initial);on[t>=80000]=target
        adc=np.tile([500.,3000.,500.,500.,float(temperature_adc(55.,cal))],(len(t),1))
        adc[:,1]=np.where(on,3000.,500.)
        _,valid,ends,led=extract(t,adc,np.zeros(len(t)),np.ones(len(t)),cal)
        matches=(t[ends]>=80000)&(led[:,1]==(2 if target else 1))
        delay=float((t[ends[matches][0]]-80000)/1000)
        transitions.append(dict(initial=initial,target=target,settling_seconds=delay,
                                transient_blink=int((led[:,1]==3).sum())))
    totals={}
    for group in groups:
        selected=[r for r in rows if r['group']==group]
        totals[group]={'cases':len(selected),**{k:sum(r[k] for r in selected) for k in
            ('windows','before_warming','after_warming','before_accepted','after_accepted','blink','uncertain')}}
    return dict(preprocess=PREPROCESS_VERSION,model=model['version'],
        model_sha256=hashlib.sha256(model_bytes).hexdigest(),synthetic_only=True,
        thresholds_tuned_in_this_evaluation=False,threshold_seconds=20,
        waveform_generator='exact rational phase sampled at integer milliseconds',
        scope='warm LED repeated switching; candidate 0.1..2 Hz, duty 0.1..0.9; 20 Hz input',
        limitations=['No waveform can be inferred before its first observed transition.',
                     'Very slow or undersampled signals may still look steady; physical validation required.'],
        totals=totals,transitions=transitions,cases=rows)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();result=evaluate(args.model)
    with Path(args.output).open('x',encoding='utf-8') as out:
        json.dump(result,out,indent=2);out.write('\n')
    print(json.dumps({k:result[k] for k in ('preprocess','totals','transitions')},indent=2))
