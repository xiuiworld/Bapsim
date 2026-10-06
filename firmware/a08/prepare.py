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

def prepare(app, model_path):
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
    for name in ('config.hpp','pipeline.hpp','sensors.hpp','selftest.hpp'):
        shutil.copyfile(here/name,out/name)
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
    with (app/'prj.conf').open('a') as f: f.write('\n# A08 real acquisition, commissioning gate closed\nCONFIG_ADC=y\n')
    (app/'VERSION').write_text('VERSION_MAJOR = 3\nVERSION_MINOR = 4\nPATCHLEVEL = 1\nVERSION_TWEAK = 8\nEXTRAVERSION = a08-adc-range-2\n')
    manifest={'firmware':'a08-adc-range-2','origin':'test','calibration_config':(here/'config.hpp').read_text(),
              'model':model['version'],'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
              for p in here.iterdir() if p.is_file()}}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--app',required=True); parser.add_argument('--model',required=True)
    args=parser.parse_args(); print(json.dumps(prepare(args.app,args.model),indent=2))
