"""Materialize A08 product integration in an A-owned generated SDK app.

For a new app first run tools/prepare_a05_probe.py and west zap-generate.
This overlay uses the same A05 data model without copying the Nordic SDK.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from tools.a07_edge.model import export_c
from firmware.a08.profile import render_profile

def prepare(app, model_path, profile_path=None, *, live_observations=False, live_edge=False):
    live_observations = live_observations or live_edge
    app=Path(app).resolve()
    if 'local-data' not in app.parts or 'firmware' in app.parts:
        raise ValueError('Generated app must be under local-data outside firmware')
    root=Path(__file__).resolve().parents[2]
    here=Path(__file__).parent
    source=app/'src/app_task.cpp'
    text=source.read_text(encoding='utf-8')
    if 'uint32_t sReportCounter' not in text or 'sLidOpen = !pressed;' not in text:
        raise ValueError('Expected a fresh A05 app; preserve/restore existing source first')
    model=json.loads(Path(model_path).read_text(encoding='utf-8'))
    if model.get('live_qualified') is not False:
        raise ValueError('Commissioning build expects a non-live candidate')
    out=app/'src/a08'; out.mkdir(exist_ok=True)
    for name in ('config.hpp','pipeline.hpp','sensors.hpp','selftest.hpp','temperature.hpp'):
        shutil.copyfile(here/name,out/name)
    if live_observations:
        config=(out/'config.hpp').read_text()
        if 'constexpr bool CONTACT_ENABLED = true;' not in config:
            raise ValueError('Live observations require the physical contact input')
        (out/'config.hpp').write_text(config.replace('LIVE_OBSERVATIONS = false','LIVE_OBSERVATIONS = true'))
    if live_edge:
        config=(out/'config.hpp').read_text()
        (out/'config.hpp').write_text(config.replace('LIVE_EDGE_OUTPUT = false','LIVE_EDGE_OUTPUT = true'))
    profile = render_profile(profile_path or here/'site_profile.json',out/'site_profile.hpp')
    a07=app/'src/a07'; a07.mkdir(exist_ok=True)
    for name in ('preprocess.hpp','a07_snapshot.hpp'):
        shutil.copyfile(root/'tools/a07_edge'/name,a07/name)
    shutil.copyfile(root/'tools/a05_contract/history.hpp',app/'src/a05_history.hpp')
    export_c(model,a07/'a07_model.h')
    begin,end=text.index('uint32_t sReportCounter'),text.index('constexpr chip::EndpointId')
    text=text[:begin]+(here/'runtime.cpp.inc').read_text(encoding='utf-8')+'\n'+text[end:]
    text='#include <platform/logging/LogV.h>\n#include <lib/support/logging/CHIPLogging.h>\n#include "a08/sensors.hpp"\n#include "a08/selftest.hpp"\n'+text
    text=text.replace('            sLidOpen = !pressed;\n            PublishSnapshot(false);','            // Product contact comes from GPIO P1.10, never a user button.')
    text=text.replace('            booleanState->SetStateValue(pressed);','            // Do not overwrite the physical contact with a button.')
    text=text.replace('    k_timer_init(&sProbeTimer, ProbeTick, nullptr);','')
    text=text.replace('    sBootId = sys_rand32_get();','    sBootId = sys_rand32_get();\n    k_sem_give(&sStartSensors);')
    text=text.replace('    k_timer_start(&sProbeTimer, K_SECONDS(10), K_SECONDS(15));','')
    source.write_text(text,encoding='utf-8')
    cmake=app/'CMakeLists.txt'; content=cmake.read_text(encoding='utf-8')
    for name in ('selftest.cpp','raw_selftest.cpp'):
        content=content.replace(f'target_sources(app PRIVATE src/a07/{name})','')
    cmake.write_text(content,encoding='utf-8')
    overlay=app/'boards/nrf54lm20dk_nrf54lm20b_cpuapp.overlay'
    overlay.write_text(overlay.read_text(encoding='utf-8')+'\n'+(here/'sensors.overlay').read_text(),encoding='utf-8')
    with (app/'prj.conf').open('a') as f:
        f.write('\n# A08 physical acquisition, test origin retained\nCONFIG_ADC=y\n')
        f.write((here/'logging.conf').read_text())
    (app/'VERSION').write_text('VERSION_MAJOR = 3\nVERSION_MINOR = 4\nPATCHLEVEL = 1\nVERSION_TWEAK = 17\nEXTRAVERSION = a08-demo-optical\n')
    manifest={'firmware':'a08-demo-optical+17','origin':'test','calibration_config':(here/'config.hpp').read_text(),
              'live_qualified':False,'installation_profile':profile,
              'model':model['version'],'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
              for p in here.iterdir() if p.is_file()}}
    if live_observations:
        (app/'VERSION').write_text('VERSION_MAJOR = 3\nVERSION_MINOR = 4\nPATCHLEVEL = 1\nVERSION_TWEAK = 18\nEXTRAVERSION = a08-live-observations\n')
        manifest.update(firmware='a08-live-observations+18', origin='live',
                        live_scope='physical_observations_only', model_live_qualified=False,
                        calibration_config=(out/'config.hpp').read_text())
    if live_edge:
        (app/'VERSION').write_text('VERSION_MAJOR = 3\nVERSION_MINOR = 4\nPATCHLEVEL = 1\nVERSION_TWEAK = 21\nEXTRAVERSION = a08-live-edge\n')
        manifest.update(firmware='a08-live-edge+21', live_scope='physical_observations_and_edge_candidate',
                        model_live_qualified=False, calibration_config=(out/'config.hpp').read_text())
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--app',required=True); parser.add_argument('--model',required=True)
    parser.add_argument('--profile',help='Installation settings JSON; defaults to the checked-in profile')
    parser.add_argument('--live-observations',action='store_true',help='Publish physical observations as live; omit unqualified model inference')
    parser.add_argument('--live-edge',action='store_true',help='Transmit existing Edge AI candidate on physical live input; qualification unchanged')
    args=parser.parse_args(); print(json.dumps(prepare(args.app,args.model,args.profile,live_observations=args.live_observations,live_edge=args.live_edge),indent=2))
