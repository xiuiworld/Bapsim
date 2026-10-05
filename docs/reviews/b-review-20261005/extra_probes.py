import asyncio, json, logging, sys, types
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
ROOT=next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').is_file())
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'tests'))
from test_ha_service_integration import FakeHass
from domain.personal_model import operating_usage_model
from service.ha_dashboard import HADashboard
from service.ha_mqtt_subscriptions import bind_mqtt_subscriptions
from service.ha_caregiver_commands import bind_caregiver_commands
from service.output import StatusOutput
from workflow import DetectionStateManager
from workflow.notification_controls import NotificationControls
from domain.events.normalizer import CookerEventNormalizer

def raw_grouping():
    normalizer=CookerEventNormalizer(lid_debounce=timedelta(0),raw_fields={
        ('matter','lid_state'):'lid',('matter','availability'):'availability'})
    model=operating_usage_model();normalized=[]
    base=datetime(2026,8,1,8,tzinfo=timezone(timedelta(hours=9)))
    for minute,kind,value,availability in [(-2,'availability','online','online'),
        (-1,'lid_state','closed','online'),(0,'lid_state','open','online'),
        (10,'availability','offline','offline'),(20,'availability','online','online'),
        (21,'lid_state','closed','online'),(30,'lid_state','open','online')]:
        at=(base+timedelta(minutes=minute)).isoformat()
        for item in normalizer.ingest(dict(source='matter',device_id='audit',event_type=kind,
             value=value,observed_at=at,received_at=at,availability=availability,quality='observed')):
            normalized.append(item.as_dict());model.observe(item.as_dict())
    return {'normalized_events':normalized,'actual_groups':model.grouped_open_times('matter','audit'),
            'normalized_openings':sum(e['event_type']=='lid_opened' for e in normalized)}

async def run():
    now=datetime.now(timezone.utc)
    hass=FakeHass();hass.config=SimpleNamespace()
    listeners={};mqtt=types.ModuleType('homeassistant.components.mqtt')
    async def wait(h):return True
    async def subscribe(h,topic,handler,**kw):listeners[topic]=handler;return lambda:None
    mqtt.async_wait_for_mqtt_client=wait;mqtt.async_subscribe=subscribe
    components=types.ModuleType('homeassistant.components');components.mqtt=mqtt
    root=types.ModuleType('homeassistant');root.components=components
    timers=[]
    dashboard=HADashboard(hass,operating_usage_model(),{},lambda *args:timers.append(args))
    async def noop(*args):pass
    with patch.dict(sys.modules,{'homeassistant':root,'homeassistant.components':components,
                                'homeassistant.components.mqtt':mqtt}):
        bind_mqtt_subscriptions(hass,StatusOutput('audit'),noop,dashboard.show,logging.getLogger('audit'),True)
        await asyncio.gather(*hass.tasks);hass.tasks.clear()
        payload=StatusOutput('audit').build((now-timedelta(hours=1)).isoformat(),{'ready':True},'available',None,None)
        payload['summary']='EXPIRED TEST PAYLOAD'
        listeners['bapsim/v1/audit/state'](SimpleNamespace(payload=json.dumps(payload)))
    expired={'valid_until':payload['valid_until'],'read_at':now.isoformat(),
             'shown_state':hass.states.get('sensor.bapsim_output_state').state,
             'expiry_timers':len(timers)}
    gap=DetectionStateManager(min_consecutive=1);trend=DetectionStateManager(min_consecutive=1)
    for manager,reason,delta in [(gap,'EXPECTED_USE_MISSING',0),(trend,'DECLINING_USAGE',1)]:
        manager.ingest(dict(source='matter',device_id='audit',as_of=(now-timedelta(minutes=delta)).isoformat(),
                            routine_status='check_required',reason_codes=[reason]))
    hass.states.async_set('input_datetime.bapsim_control_until',
                         (now+timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),{})
    handlers=bind_caregiver_commands(lambda f:f,hass,gap,trend,NotificationControls(),operating_usage_model(),
        SimpleNamespace(model_away=False),StatusOutput('audit'),False,lambda *a:None,noop,lambda *a:None,
        lambda:None,logging.getLogger('audit'))
    try:
        await handlers.dashboard_control(SimpleNamespace(data={'action':'acknowledge'}))
        acknowledgement='accepted'
    except ValueError as e:acknowledgement=str(e)
    return {'external_expiry':expired,'two_active_dashboard_acknowledge':acknowledgement,
            'raw_pipeline_grouping':raw_grouping()}

if __name__=='__main__':
    result=asyncio.run(run())
    Path(__file__).with_name('extra-probes.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
