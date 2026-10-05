"""Small independent schedule generator; no use of B's household generator.
Equal input/clock for operating model and fixed-24h one-request-per-gap baseline.
No phone transport or quiet-hour filter; metrics concern request candidates.
"""
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
ROOT=next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
sys.path.insert(0,str(ROOT))
from domain.personal_model import operating_usage_model
from service.analysis_policy import assess_personal_use, route_personal_decision
from workflow import DetectionStateManager

START=datetime(2026,6,1,tzinfo=timezone(timedelta(hours=9)))

def one_case(hours, drift, seed):
    rng=Random(seed); openings=[]; missed=[]
    # Fixed predeclared omissions: first two expected openings after day 23.
    future_index=0
    for day in range(30):
        for hour in hours:
            planned=START+timedelta(days=day,hours=hour,
                minutes=rng.randint(-20,20)+(max(0,day-21)*25 if drift else 0))
            if day>=23 and future_index<2:
                missed.append(planned);future_index+=1
            else:openings.append(planned)
    openings.sort(); missed.sort()
    m=operating_usage_model(); gap=DetectionStateManager(candidate_window=timedelta(hours=36),resumption_alerts=False)
    decline=DetectionStateManager(min_consecutive=1,resumption_alerts=False)
    def ev(t,kind):return dict(source='matter',device_id='independent',event_type=kind,
                             observed_at=t.isoformat(),availability='online')
    m.observe(ev(START,'device_online'))
    actions=[(t,'open') for t in openings]
    for day in range(30):
        actions.extend((START+timedelta(days=day,minutes=minute),'tick') for minute in range(0,1440,60 if day<21 else 1))
    requests={'personal':[],'fixed24':[]};last=None;fixed_sent=False
    for t,kind in sorted(actions):
        if kind=='open':
            m.observe(ev(t,'lid_opened'));gap.ingest_lid_opening('matter','independent',t.isoformat())
            last=t;fixed_sent=False
        else:
            m.availability('matter','independent',True,t.isoformat())
            a=assess_personal_use(m,'matter','independent',t.isoformat(),online=True,away=False)
            for typ,items in [('window',a.windows),('interval',[a.interval] if a.interval else [])]:
                for d in items:
                    manager,updates=route_personal_decision(typ,d,gap,decline)
                    for u in updates:
                        if u.action in {'notify_requested','reminder_requested'}:
                            if t>=START+timedelta(days=21):requests['personal'].append(t)
                            manager.mark_notified('matter','independent',t.isoformat())
            if last and t-last>timedelta(hours=24) and not fixed_sent:
                fixed_sent=True
                if t>=START+timedelta(days=21):requests['fixed24'].append(t)
    result={}
    for name,times in requests.items():
        hits=[t for t in times if missed[-1]<=t<missed[-1]+timedelta(days=1)]
        result[name]={'caught':bool(hits),
          'delay_hours':round((min(hits)-missed[-1]).total_seconds()/3600,3) if hits else None,
          'early_requests':sum(missed[0]<=t<missed[-1] for t in times),
          'unrelated_requests':sum(not missed[0]<=t<missed[-1]+timedelta(days=1) for t in times),
          'all_request_times':[t.isoformat() for t in times]}
    return {'hours':hours,'drift_minutes_per_day':25 if drift else 0,'seed':seed,
            'missed_times':[t.isoformat() for t in missed],'result':result}

if __name__=='__main__':
    cases=[]
    for index,hours in enumerate([(7,),(19,),(7,19),(7,12,19)]):
        for drift in (False,True):
            cases.append(one_case(hours,drift,202610050+index))
            print('case',len(cases),cases[-1]['result'],flush=True)
    output={'scope':'8 deterministic synthetic schedules; not population performance',
            'history_days':21,'evaluation_days':9,'conditions':'stable and 25-min/day shift; +/-20-min jitter',
            'quiet_hours_applied':False,'cases':cases}
    Path(__file__).with_name('independent-cases.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
