"""Prepare disposable A05 SDK copy; never edits firmware/ or SDK originals."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sdk', required=True)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    subprocess.run([sys.executable, str(Path(__file__).with_name('prepare_a04_probe.py')),
                    '--sdk', a.sdk, '--output', str(a.output)], check=True)
    zap = a.output / 'src/default_zap'
    model = json.loads((zap / 'contact_sensor.zap').read_text())
    cluster = next(c for c in model['endpointTypes'][1]['clusters'] if c['name'] == 'NordicDevKit')
    attr = copy.deepcopy(next(x for x in cluster['attributes'] if x['name'] == 'ReportCounter'))
    attr.update(name='SnapshotPacked', code=0xFFF10004, type='octet_string', defaultValue='', maxLength=110)
    cluster['attributes'].append(attr)
    (zap / 'contact_sensor.zap').write_text(json.dumps(model, indent=2))
    xml = ET.parse(zap / 'NordicDevKitCluster.xml')
    ET.SubElement(xml.getroot().find('cluster'), 'attribute', side='server',
                  code='0xfff10004', define='SNAPSHOT_PACKED', type='octet_string',
                  length='110', writable='false', optional='false', default='',
                  name='SnapshotPacked', reportable='true')
    xml.write(zap / 'NordicDevKitCluster.xml', encoding='utf-8', xml_declaration=True)
    app = a.output / 'src/app_task.cpp'
    text = app.read_text()
    text = text.replace('#include <zephyr/logging/log.h>', '#include <zephyr/logging/log.h>\n#include <zephyr/random/random.h>\n#include <zephyr/sys/byteorder.h>')
    start, end = text.index('uint32_t sReportCounter'), text.index('constexpr chip::EndpointId')
    text = text[:start] + Path(__file__).with_name('a05_contract').joinpath('probe_cpp.inc').read_text() + '\n' + text[end:]
    text = text.replace('Clusters::NordicDevKit::Attributes::UserLED::Set(1, pressed);',
                        'Clusters::NordicDevKit::Attributes::UserLED::Set(1, pressed);\n            sLidOpen = !pressed;\n            PublishSnapshot(false);')
    text = text.replace('k_timer_init(&sProbeTimer, ProbeTick, nullptr);',
                        'sBootId = sys_rand32_get();\n    k_timer_init(&sProbeTimer, ProbeTick, nullptr);')
    text = text.replace('K_SECONDS(60)', 'K_SECONDS(15)')
    app.write_text(text)
    with (a.output / 'prj.conf').open('a') as f:
        f.write('\nCONFIG_CHIP_DEVICE_SOFTWARE_VERSION=3401005\nCONFIG_CHIP_DEVICE_SOFTWARE_VERSION_STRING="bapsim-a05-test-1"\n')
    print('A05 test snapshot prepared; 15-second accelerated scenarios, not live sensors.')


if __name__ == '__main__':
    main()
