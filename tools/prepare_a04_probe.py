"""Create a disposable A04 Matter probe from installed Nordic SDK samples.

Does not edit firmware/ or the SDK. Output must be a new directory.
Generated samples retain their upstream license headers.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    samples = args.sdk.resolve() / 'nrf/samples/matter'
    out = args.output.resolve()
    if out.exists():
        raise SystemExit('Output already exists; choose a new directory.')
    if 'firmware' in (part.casefold() for part in out.parts) or out.is_relative_to(args.sdk.resolve()):
        raise SystemExit('Output must be outside firmware/ and the SDK.')
    shutil.copytree(samples / 'contact_sensor', out)
    zap_dir = out / 'src/default_zap'
    matter = args.sdk.resolve() / 'modules/lib/matter'
    def read_json(path):
        return json.loads(path.read_text(encoding='utf-8'))
    def write_json(path, value):
        path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')

    custom_dir = samples / 'manufacturer_specific/src/default_zap'
    model = read_json(zap_dir / 'contact_sensor.zap')
    custom = read_json(custom_dir / 'manufacturer_specific.zap')
    temp = read_json(samples / 'temperature_sensor/src/default_zap/temperature_sensor.zap')
    cluster = copy.deepcopy(next(c for e in custom['endpointTypes'] for c in e['clusters'] if c['name'] == 'NordicDevKit'))
    # Probe is read-only: do not expose the sample's LED-control command.
    cluster['commands'] = []
    cluster['events'] = []
    cluster['attributes'] = [a for a in cluster['attributes'] if a['code'] < 0xFFFF0000]
    template = copy.deepcopy(next(a for a in cluster['attributes'] if a['name'] == 'ClusterRevision'))
    template.update(name='ReportCounter', code=0xFFF10003, type='int32u', defaultValue='0', reportable=1)
    cluster['attributes'].append(template)
    for attr in cluster['attributes']:
        if attr['name'] in ('UserButton', 'UserLED'):
            attr['reportable'] = 1
    model['endpointTypes'][1]['clusters'].append(cluster)
    temp_type = copy.deepcopy(temp['endpointTypes'][temp['endpoints'][1]['endpointTypeIndex']])
    temp_type.update(id=3, name='A04 Temperature Probe')
    model['endpointTypes'].append(temp_type)
    temp_endpoint = copy.deepcopy(temp['endpoints'][1])
    temp_endpoint.update(endpointId=2, endpointTypeIndex=2, endpointTypeName='A04 Temperature Probe')
    model['endpoints'].append(temp_endpoint)
    for package in model['package']:
        package['pathRelativity'] = 'relativeToZap'
        package['path'] = 'zcl.json' if package['type'] == 'zcl-properties' else os.path.relpath(matter / 'src/app/zap-templates/app-templates.json', zap_dir)
    write_json(zap_dir / 'contact_sensor.zap', model)

    zcl = read_json(custom_dir / 'zcl.json')
    zcl['xmlRoot'] = [os.path.relpath(matter / 'src/app/zap-templates/zcl', zap_dir).replace('\\', '/'), os.path.relpath(matter / 'src/app/zap-templates/zcl/data-model/chip', zap_dir).replace('\\', '/'), '.']
    zcl['manufacturersXml'] = os.path.relpath(matter / 'src/app/zap-templates/zcl/data-model/manufacturers.xml', zap_dir).replace('\\', '/')
    write_json(zap_dir / 'zcl.json', zcl)
    xml = ET.parse(custom_dir / 'NordicDevKitCluster.xml')
    root = xml.getroot()
    for extension in list(root.findall('clusterExtension')):
        root.remove(extension)
    custom_cluster = root.find('cluster')
    for item in list(custom_cluster):
        if item.tag in ('command', 'event'):
            custom_cluster.remove(item)
        elif item.tag == 'attribute':
            item.set('writable', 'false')
            item.set('reportable', 'true')
    ET.SubElement(custom_cluster, 'attribute', side='server', code='0xfff10003', define='REPORT_COUNTER', type='int32u', writable='false', optional='false', default='0', name='ReportCounter', reportable='true')
    xml.write(zap_dir / 'NordicDevKitCluster.xml', encoding='utf-8', xml_declaration=True)

    cmake_path = out / 'CMakeLists.txt'
    cmake = cmake_path.read_text()
    zap_setup = 'include(${ZEPHYR_NRF_MODULE_DIR}/samples/matter/common/cmake/zap_helpers.cmake)\nncs_get_zap_parent_dir(ZAP_PARENT_DIR)'
    cmake = cmake.replace(zap_setup, '')
    cmake = cmake.replace('# Enable GNU STD support.', zap_setup + '\nget_filename_component(CHIP_APP_ZAP_DIR ${ZAP_PARENT_DIR}/zap-generated REALPATH)\n\n# Enable GNU STD support.')
    cmake = cmake.replace('ncs_configure_data_model()', 'ncs_configure_data_model(EXTERNAL_CLUSTERS "NORDIC_DEV_KIT_CLUSTER")')
    cmake_path.write_text(cmake)
    with (out / 'prj.conf').open('a') as f:
        f.write('\n# A04 transport probe: all temperature values are synthetic.\nCONFIG_CHIP_APP_ZAP_DIR="${APPLICATION_CONFIG_DIR}/src/default_zap/zap-generated"\nCONFIG_CHIP_DEVICE_VENDOR_ID=65521\n')

    app_path = out / 'src/app_task.cpp'
    app = app_path.read_text()
    app = app.replace('Nrf::Matter::IdentifyCluster sIdentifyCluster(kContactSensorEndpointId);', 'Nrf::Matter::IdentifyCluster sIdentifyCluster(kContactSensorEndpointId);\nNrf::Matter::IdentifyCluster sTemperatureIdentifyCluster(2);')
    app = app.replace('ReturnErrorOnFailure(sIdentifyCluster.Init());', 'ReturnErrorOnFailure(sIdentifyCluster.Init());\n    ReturnErrorOnFailure(sTemperatureIdentifyCluster.Init());')
    app = app.replace('namespace\n{', '''namespace
{
uint32_t sReportCounter = 0;
k_timer sProbeTimer;
void ProbeTick(k_timer *)
{
    PlatformMgr().ScheduleWork([](intptr_t) {
        const uint32_t next = sReportCounter + 1;
        const auto counterStatus = Clusters::NordicDevKit::Attributes::ReportCounter::Set(1, next);
        const int16_t temperature = 2000 + (next % 11) * 50;
        const auto temperatureStatus = Clusters::TemperatureMeasurement::Attributes::MeasuredValue::Set(2, temperature);
        if (counterStatus == Protocols::InteractionModel::Status::Success) sReportCounter = next;
        LOG_INF("A04 TEST counter=%u temperature_raw=%d status=%u/%u", sReportCounter, temperature,
                static_cast<unsigned>(counterStatus), static_cast<unsigned>(temperatureStatus));
    }, 0);
}
''', 1)
    app = app.replace('booleanState->SetStateValue(state);', '''const bool pressed = (state & APPLICATION_BUTTON_MASK) != 0;
            booleanState->SetStateValue(pressed);
            Clusters::NordicDevKit::Attributes::UserButton::Set(1, pressed);
            Clusters::NordicDevKit::Attributes::UserLED::Set(1, pressed);''')
    app = app.replace('ReturnErrorOnFailure(Init());', '''ReturnErrorOnFailure(Init());
    k_timer_init(&sProbeTimer, ProbeTick, nullptr);
    k_timer_start(&sProbeTimer, K_SECONDS(10), K_SECONDS(60));''')
    app += '\nvoid MatterNordicDevKitPluginServerInitCallback() {}\nvoid emberAfNordicDevKitClusterInitCallback(chip::EndpointId) {}\n'
    app_path.write_text(app)
    print(f'Prepared A04 probe: {out}')
    print('Next: west zap-generate --full -z <output>/src/default_zap/contact_sensor.zap -j <output>/src/default_zap/zcl.json')


if __name__ == '__main__':
    main()
