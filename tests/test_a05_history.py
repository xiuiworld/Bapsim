"""Buffered reports must survive polling without inventing historical usage."""
import asyncio
import copy
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import ast
import logging
import time

from test_a05_snapshot_input import report
from test_ha_service_integration import COMPONENT, FakeHass, ha_modules
from service.a05_snapshot_input import (HISTORY_PATH, pending_reports,
                                       verified_report, verified_test_report, report_time)
from tools.a05_contract.wire import decode_history, decode, encode, Continuity, PATH


def buffered(numbers, origin="live", boot=7):
    rows=[]
    for n in numbers:
        entry,state=report(n,n,n%2==0,origin,boot)
        row=state.attributes['raw_snapshot']
        row['UptimeMs']=n*1000
        rows.append(row)
    state.attributes.update(read_method='buffered_read_5s',history_path=HISTORY_PATH,
                            raw_history=rows)
    return entry,state


def wire(rows, origin=True):
    frames=[encode(r,test_input=origin) for r in rows]
    return b'BH\x01'+bytes([len(frames)])+b''.join(bytes([len(f)])+f for f in frames)


class HistoryTests(TestCase):
    def test_estimated_time_rejects_stale_extreme_and_backward_clock(self):
        now = datetime(2026, 10, 5, tzinfo=timezone.utc)
        self.assertEqual(report_time({'UptimeMs': 0}, {'UptimeMs': 5000}, now),
                         now - timedelta(seconds=5))
        for uptime in (180001, 2**64-1, -1):
            self.assertIsNone(report_time({'UptimeMs': 0}, {'UptimeMs': uptime}, now))
        self.assertIsNone(report_time({'UptimeMs': 0}, {'UptimeMs': 0}, now, now))

    def test_actual_coordinator_replays_continuity_and_rejects_bad_journal(self):
        # Execute the production coordinator class with only HA's scheduling
        # superclass replaced. The actual parser and update method are used.
        source=Path('tools/a05_contract/ha_sensor.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='SnapshotCoordinator')
        class Coordinator:
            def __init__(self,hass,*a,**kw):self.hass=hass
        class ReadFailed(Exception):pass
        namespace=dict(DataUpdateCoordinator=Coordinator,UpdateFailed=ReadFailed,
                       asyncio=asyncio,timedelta=timedelta,time=time,Continuity=Continuity,
                       decode=decode,decode_history=decode_history,PATH=PATH,HISTORY_PATH=HISTORY_PATH,
                       _LOGGER=logging.getLogger(__name__))
        exec(compile(ast.Module(body=[node],type_ignores=[]),'<coordinator>','exec'),namespace)
        async def scenario():
            _,state=buffered([1]);payload=wire(state.attributes['raw_history'])
            async def read(node,path):
                self.assertEqual(path,HISTORY_PATH)
                return {path:payload}
            runtime=SimpleNamespace(adapter=SimpleNamespace(matter_client=SimpleNamespace(read_attribute=read)))
            hass=SimpleNamespace(config_entries=SimpleNamespace(async_get_entry=lambda _:SimpleNamespace(runtime_data=runtime)))
            c=namespace['SnapshotCoordinator'](hass,dict(matter_entry_id='entry-1',node_id=4,snapshot_history=True))
            self.assertEqual((await c._async_update_data())['continuity'],'baseline')
            _,state=buffered(range(1,7));payload=wire(state.attributes['raw_history'])
            self.assertEqual((await c._async_update_data())['continuity'],'continuous')
            self.assertEqual(c.discontinuity,'baseline')
            self.assertEqual((await c._async_update_data())['continuity'],'duplicate')
            _,state=buffered(range(13,21));payload=wire(state.attributes['raw_history'])
            await c._async_update_data();self.assertEqual(c.discontinuity,'gap')
            self.assertEqual(c.last_gap,dict(boot_id=7,first_missing=7,last_missing=12,count=6))
            payload=b'bad'
            with self.assertRaises(ReadFailed):await c._async_update_data()
            self.assertEqual(c.tracker.previous['ReportCounter'],20)
            self.assertEqual(c.discontinuity,'read_error')
            _,state=buffered(range(18,26));payload=wire(state.attributes['raw_history'])
            self.assertEqual((await c._async_update_data())['continuity'],'continuous')
            payload=b'bad'
            with self.assertRaises(ReadFailed):await c._async_update_data()
            _,state=buffered(range(30,38));payload=wire(state.attributes['raw_history'])
            await c._async_update_data()
            self.assertEqual(c.last_gap,dict(boot_id=7,first_missing=26,last_missing=29,count=4))
            self.assertEqual(c.discontinuity,'gap')
            _,state=buffered([1],boot=8);payload=wire(state.attributes['raw_history'])
            self.assertEqual((await c._async_update_data())['continuity'],'reboot')
        asyncio.run(scenario())

    def test_atomic_history_codec_and_fail_closed(self):
        _,s=buffered(range(1,9))
        rows=s.attributes['raw_history']; payload=wire(rows)
        self.assertEqual(decode_history(payload),(rows,True))
        for bad in (payload[:-1],payload+b'x',b'BH\x01\x00',wire(rows+rows[:1])):
            with self.assertRaises(ValueError):decode_history(bad)
        for key,value in [('BootId',99),('ReportCounter',40),('ObservationSequence',40),('UptimeMs',0)]:
            broken=copy.deepcopy(rows);broken[3][key]=value
            with self.assertRaises(ValueError):decode_history(wire(broken))
        a=encode(rows[0],test_input=True); b=encode(rows[1],test_input=False)
        with self.assertRaises(ValueError):decode_history(b'BH\x01\x02'+bytes([len(a)])+a+bytes([len(b)])+b)

    def test_provenance_and_history_tail_cannot_be_bypassed(self):
        e,s=buffered(range(1,5))
        self.assertIsNotNone(verified_report(e,s,'entry-1',4))
        self.assertIsNone(verified_test_report(e,s,'entry-1',4))
        self.assertIsNone(verified_report(e,s,'other',4))
        for key,value in [('history_path','bad'),('raw_history',[]),('raw_history',s.attributes['raw_history'][:-1])]:
            bad=copy.deepcopy(s);bad.attributes[key]=value
            self.assertIsNone(verified_report(e,bad,'entry-1',4))

    def test_initial_reboot_duplicate_and_overflow_selection(self):
        _,s=buffered(range(12,20));latest=s.attributes['raw_snapshot']
        self.assertEqual(pending_reports(s,latest,None),[latest])
        self.assertEqual(pending_reports(s,latest,(6,11,11)),[latest])
        self.assertEqual(pending_reports(s,latest,(7,19,19)),[])
        self.assertEqual(pending_reports(s,latest,(7,15,15)),s.attributes['raw_history'][4:])
        # Do not invent rows 10/11: downstream sequence gap remains visible.
        self.assertEqual(pending_reports(s,latest,(7,9,9))[0]['ReportCounter'],12)

    def test_real_ha_callback_records_each_short_edge_once_and_overflow_gap(self):
        async def scenario(root):
            with patch.dict(sys.modules,ha_modules({})):
                spec=importlib.util.spec_from_file_location('history_ha_test',COMPONENT)
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                hass=FakeHass();hass.config=SimpleNamespace(path=lambda name:str(Path(root)/name))
                entity='sensor.a05_reportcounter'
                e,s=buffered([1]);hass.registry_entries[entity]=e
                hass.states.async_set(entity,s.state,s.attributes)
                await module.async_setup(hass,{})
                await hass.services.async_call('bapsim','register_a05_snapshot',dict(
                    device_id='cooker',entity_id=entity,matter_entry_id='entry-1',node_id=4))
                clock=datetime(2026,10,5,10,tzinfo=timezone.utc)
                class Clock:
                    @staticmethod
                    def now(tz=None):return clock
                def change(ns,origin='live',boot=7):
                    old=hass.states.get(entity);_,new=buffered(ns,origin,boot)
                    hass.states.async_set(entity,new.state,new.attributes)
                    hass.bus.async_fire('state_changed',dict(entity_id=entity,old_state=old,new_state=hass.states.get(entity)))
                with patch.object(module,'datetime',Clock):
                    change([1])  # initial baseline, not a new opening
                    clock+=timedelta(seconds=5)
                    change(range(1,7))  # five one-second changes within one poll
                    change(range(1,7))  # identical read must not replay
                    clock+=timedelta(seconds=15)
                    change(range(13,21))  # buffer overflow, missing 7..12
                    clock+=timedelta(seconds=5)
                    change([1],boot=8)  # reboot is a fresh baseline
                    clock+=timedelta(seconds=5)
                    change([2],origin='test',boot=8)  # must not reach live use
                if hass.tasks:await asyncio.gather(*hass.tasks)
                rows=[json.loads(line) for p in (Path(root)/'bapsim_contract_observations').glob('*.jsonl') for line in p.read_text().splitlines()]
                lid=[r for r in rows if r['signal']=='lid_open']
                self.assertEqual([r['value'] for r in lid[:6]],[False,True,False,True,False,True])
                self.assertEqual(len({r['record_id'] for r in rows}),len(rows))
                self.assertEqual(lid[6]['quality'],'unavailable')
                self.assertEqual(lid[7]['kind'],'snapshot')
                raw=[v for name,v in hass.bus.events if name==module.RAW_EVENT and v['device_id']=='cooker']
                self.assertEqual([r['value'] for r in raw if r['event_type']=='lid_state'][:6],['closed','open','closed','open','closed','open'])
                times=[r['observed_at'] for r in raw[:7]]
                self.assertEqual(times,sorted(times))
        with TemporaryDirectory() as root:asyncio.run(scenario(root))
