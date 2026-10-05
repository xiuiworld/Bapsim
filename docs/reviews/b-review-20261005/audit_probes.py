"""Read-only runtime probes. Uses synthetic data and fake transports only."""
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
sys.path.insert(0, str(ROOT))
from domain.personal_model import operating_usage_model
from service.analysis_policy import assess_personal_use, route_personal_decision
from service.ha_caregiver_commands import bind_caregiver_commands
from service.ha_notification_transport import send_mobile
from service.output import StatusOutput
from workflow import DetectionStateManager
from workflow.notification_controls import NotificationControls

KST = timezone(timedelta(hours=9))
START = datetime(2026, 8, 1, tzinfo=KST)
def at(day, hour=0, minute=0):
    return (START + timedelta(days=day, hours=hour, minutes=minute)).isoformat()
def event(day, hour, kind='lid_opened', minute=0):
    return dict(source='matter', device_id='audit_cooker', event_type=kind,
                observed_at=at(day, hour, minute), availability='online' if kind!='device_offline' else 'offline')

def grouping_probe():
    m = operating_usage_model()
    for e in [event(0,0,'device_online'), event(0,8), event(0,8,'device_offline',10),
              event(0,8,'device_online',20), event(0,8,minute=30)]:
        m.observe(e)
    return {'expected_groups_after_connection_break':2,
            'actual_groups':list(m.grouped_open_times('matter','audit_cooker'))}

def readiness_probe():
    m=operating_usage_model()
    m.observe(event(0,0,'device_online'))
    p=m.profile('matter','audit_cooker',at(8,12))
    return {'opening_count':p['lid_open_count_24h'], 'profile_ready':p['ready'],
            'output_learning':StatusOutput('audit').build(at(8,12),p,'available',None,None)['learning'],
            'covered_days':[w['covered_days'] for w in p['windows']]}

def trend_probe():
    m=operating_usage_model()
    manager=DetectionStateManager(min_consecutive=1,candidate_window=timedelta(days=1),
                                  reminder_cadence=timedelta(days=7),resumption_alerts=False)
    m.observe(event(0,0,'device_online'))
    decisions=[]; requests=[]
    for day in range(42):
        for hour in ((8,18) if day<21 else (8,)):
            m.observe(event(day,hour))
        d=m.evaluate_trend('matter','audit_cooker',at(day,23),online=True)
        if d:
            decisions.append(d)
            for update in manager.ingest(d):
                requests.append(update.as_dict())
                if update.action in {'notify_requested','reminder_requested'}:
                    manager.mark_notified('matter','audit_cooker',update.as_of)
    return {'days':42,'history':'21 days twice daily then 21 days once daily',
            'decisions':decisions,'requests':requests,
            'final_model_trend_active':m._device('matter','audit_cooker')['trend_active']}

async def control_probe():
    calls=[]
    class Services:
        def has_service(self,*args):return False
        async def async_call(self,*args,**kwargs):calls.append({'transport_args':args})
    hass=SimpleNamespace(services=Services())
    gap=DetectionStateManager(min_consecutive=1)
    trend=DetectionStateManager(min_consecutive=1)
    controls=NotificationControls()
    mode=SimpleNamespace(model_away=False)
    async def send_controls():pass
    def show(*args):calls.append({'show':args})
    def save_later():calls.append({'save':'scheduled_only'})
    handlers=bind_caregiver_commands(lambda f:f,hass,gap,trend,controls,operating_usage_model(),mode,
        StatusOutput('audit'),False,show,send_controls,lambda u:None,save_later,logging.getLogger('audit'))
    now=datetime.now(timezone.utc)
    cmd=dict(command_id='audit-pause',action='pause_lifestyle',pause_id='audit',
             starts_at=now.isoformat(),ends_at=(now+timedelta(hours=2)).isoformat())
    await handlers.set_notification_control(SimpleNamespace(data=cmd))
    await send_mobile(hass,'fake_target','title','body','notify_requested','matter','audit_cooker','incident')
    return {'call_sequence':calls,'durable_save_awaited':False,
            'note':'fake transport; no HA or phone calls made'}

def output_probe():
    item={'incident_id':'matter:audit:1','status':'checking','reason_codes':['EXPECTED_USE_MISSING']}
    return StatusOutput('audit').build(at(8,12),{'ready':True,'model':'personal_usage_v1'},'available',item,None)

def lifecycle_scenarios():
    results={}
    for case in ['normal','late_90m','missing_two','away','offline','resume','cold_start']:
        m=operating_usage_model()
        gap=DetectionStateManager(candidate_window=timedelta(hours=36),resumption_alerts=False)
        trend=DetectionStateManager(min_consecutive=1)
        m.observe(event(0,0,'device_online'))
        if case!='cold_start':
            for day in range(21):
                for hour in (8,18):m.observe(event(day,hour))
                m.evaluate('matter','audit_cooker',at(day,23),online=True)
        actions=[]; decisions=[]
        start_day=21 if case!='cold_start' else 0
        for minute in range(3*1440):
            day=start_day+minute//1440
            minute_of_day=minute%1440
            moment=START+timedelta(days=day,minutes=minute_of_day)
            online=case!='offline'; away=case=='away'
            m.set_away(away,moment.isoformat())
            m.availability('matter','audit_cooker',online,moment.isoformat())
            opening=(minute_of_day in ((570,1170) if case=='late_90m' else (480,1080))
                     and case in {'normal','late_90m','cold_start'}) or (case=='resume' and minute==1440+480)
            if opening:
                e=dict(source='matter',device_id='audit_cooker',event_type='lid_opened',
                       observed_at=moment.isoformat(),availability='online')
                if m.observe(e):
                    actions.extend(u.as_dict() for u in gap.ingest_lid_opening('matter','audit_cooker',moment.isoformat()))
            if not online or away:
                gap.ingest(dict(source='matter',device_id='audit_cooker',as_of=moment.isoformat(),
                                routine_status='away' if away else 'data_unavailable',reason_codes=[]))
            assessment=assess_personal_use(m,'matter','audit_cooker',moment.isoformat(),online=online,away=away)
            for kind,ds in [('window',assessment.windows),('interval',[assessment.interval] if assessment.interval else []),
                            ('trend',[assessment.trend] if assessment.trend else [])]:
                for d in ds:
                    if d['routine_status']=='check_required':decisions.append(d)
                    manager,updates=route_personal_decision(kind,d,gap,trend)
                    for u in updates:
                        actions.append(u.as_dict())
                        if u.action in {'notify_requested','reminder_requested'}:
                            manager.mark_notified(u.source,u.device_id,u.as_of)
        results[case]={'check_required_count':len(decisions),'decisions':decisions,'actions':actions,
                       'final_gap':gap.current_state('matter','audit_cooker')}
    return results

if __name__=='__main__':
    results={'grouping':grouping_probe(),'readiness':readiness_probe(),'trend':trend_probe(),
             'control_and_mobile':asyncio.run(control_probe()),'output':output_probe(),
             'scenarios':lifecycle_scenarios()}
    target=Path(__file__).with_name('audit-probes.json')
    target.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Wrote',target)
    print(json.dumps({k:{'candidates':v['check_required_count'],
          'requests':[u['as_of'] for u in v['actions'] if u['action']=='notify_requested']}
          for k,v in results['scenarios'].items()},ensure_ascii=False))
    print('trend requests', len(results['trend']['requests']))
