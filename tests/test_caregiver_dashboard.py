"""Render the shipped YAML templates, not a duplicate presentation implementation."""
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
import yaml
from jinja2 import Environment
from service.ha_dashboard import HADashboard
from service.output import StatusOutput
from service.a05_contract_records import A05ContractRecords
from test_a05_snapshot_input import report
from workflow.notification_policy import current_request
from workflow.state_manager import DetectionStateManager
from test_ha_service_integration import FakeHass

PATH = Path(__file__).resolve().parents[1] / 'infrastructure/home_assistant/config/dashboards/bapsim_lab.yaml'
NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


def render(attributes=None, states=None):
    attrs = attributes or {}
    env = Environment()
    env.filters['timestamp_custom'] = lambda value, fmt, local=True: datetime.fromtimestamp(value, timezone.utc).strftime(fmt)
    def timestamp(value, default=0):
        try:
            return (value if isinstance(value, datetime) else datetime.fromisoformat(value)).timestamp()
        except (TypeError, ValueError):
            return default
    views = yaml.safe_load(PATH.read_text(encoding='utf-8'))['views']
    templates = next(v for v in views if v['path'] == 'caregiver-details')['cards'][0]['cards']
    return '\n'.join(env.from_string(card['content']).render(
        states=lambda entity: (states or {}).get(entity, 'unknown'),
        state_attr=lambda entity, key: attrs.get(entity, {}).get(key),
        as_timestamp=timestamp, now=lambda: NOW,
    ) for card in templates if card['type'] == 'markdown')


class CaregiverDashboardTests(TestCase):
    def attrs(self, **overrides):
        return {'sensor.bapsim_output_state': {
            'source': 'matter', 'valid_until': '2026-10-14T00:03:00Z',
            'observation': 'available', 'learning': 'personalized', **overrides}}

    def test_no_data_initial_learning_and_clear(self):
        self.assertIn('새 관측을 기다리고', render())
        self.assertIn('개인 사용 기록을 모으는 중', render(self.attrs(learning='insufficient')))
        attrs = self.attrs(learning='collecting', initial_basis=True)
        attrs['sensor.bapsim_initial_routine'] = {'routines': [{'windows': [{'time': '13:00', 'weekdays': list(range(7))}]}]}
        self.assertIn('가족이 입력한 초기 시간', render(attrs))
        self.assertIn('13:00', render(attrs))
        self.assertIn('현재 안내할 사용 변화 없음', render(self.attrs()))

    def test_gap_request_checking_completed_and_resumption(self):
        self.assertIn('사용 공백을 관찰', render(self.attrs(), {'sensor.bapsim_detection_status': 'check_required'}))
        for status, caregiver, reason, expected in [
            ('watch', None, None, '확인 요청 생성'),
            ('notified', 'checking', None, '보호자가 확인 중'),
            ('resolved', 'resolved', 'caregiver_completed', '보호자가 안부 확인 완료'),
            ('resolved', None, 'usage_resumed', '개방 기록으로 사용 재개 확인'),
            ('resolved', None, None, '종료 사유 미확인')]:
            with self.subTest(expected=expected):
                text = render(self.attrs(assessments=[{'type': 'usage_gap', 'status': status,
                    'caregiver_status': caregiver, 'resolution_reason': reason}]))
                self.assertIn(expected, text)

    def test_disconnect_stale_and_delivery_uncertainty(self):
        self.assertIn('연결 문제로 판단 보류', render(self.attrs(observation='unavailable')))
        self.assertIn('새 관측을 기다리고', render(self.attrs(valid_until='2026-10-13T00:00:00Z')))
        for state, label in [('pending','요청 처리 중'), ('failed','전송 실패'),
                             ('uncertain','전송 결과 불명확'), ('ha_accepted','HA 서비스 호출 수락')]:
            self.assertIn(label, render(self.attrs(), {'sensor.bapsim_notification_delivery': state}))

    def test_observation_quality_and_expiry_never_fill_closed_or_zero(self):
        attrs = self.attrs()
        obs = {'source': 'matter', 'valid_until': '2026-10-14T00:03:00Z', 'fields': {
            'lid_open': {'value': True, 'quality': 'good'},
            'surface_temperature_c': {'value': 42, 'quality': 'good'},
            'cooker_phase': {'value': None, 'quality': 'unknown'}}}
        attrs['sensor.bapsim_current_observation'] = obs
        self.assertIn('**열림**', render(attrs))
        self.assertIn('42 °C', render(attrs))
        self.assertIn('**판단 불가**', render(attrs))
        obs['valid_until'] = '2026-10-13T00:00:00Z'
        self.assertNotIn('42 °C', render(attrs))
        self.assertNotIn('**열림**', render(attrs))
        self.assertNotIn('**닫힘**', render(attrs))

    def test_test_contract_cannot_populate_live_observation(self):
        hass = FakeHass()
        dashboard = HADashboard(hass, None, {})
        dashboard.show_observation([{'mode': 'test'}])
        self.assertIsNone(hass.states.get('sensor.bapsim_current_observation'))
        self.assertNotIn('현재 안내할 사용 변화 없음', render(self.attrs(source='simulator')))

    def test_response_provenance_survives_restore(self):
        manager = DetectionStateManager(min_consecutive=1)
        decision = {'source':'simulator', 'device_id':'c', 'as_of':NOW.isoformat(),
                    'routine_status':'check_required', 'reason_codes':['EXPECTED_USE_MISSING']}
        manager.ingest(decision)
        manager.caregiver_response('simulator','c','checking',NOW.isoformat())
        self.assertTrue(current_request('notify_requested', manager.current_state('simulator','c')))
        self.assertEqual(manager.current_state('simulator','c')['caregiver_status'], 'checking')
        # A separate request receives only a reminder deferral, not a caregiver response.
        manager = DetectionStateManager(min_consecutive=1)
        manager.ingest(decision)
        manager.mark_notified('simulator','c',NOW.isoformat())
        manager.caregiver_response('simulator','c','checking',NOW.isoformat(), caregiver_initiated=False)
        self.assertIsNone(manager.current_state('simulator','c')['caregiver_status'])
        manager.caregiver_response('simulator','c','checking',NOW.isoformat())
        self.assertEqual(manager.current_state('simulator','c')['caregiver_status'], 'checking')
        manager.caregiver_response('simulator','c','resolved',NOW.isoformat())
        restored = DetectionStateManager()
        restored.load_state(manager.dump_state())
        state = StatusOutput('test').build(NOW.isoformat(), {}, 'available', restored.current_state('simulator','c'), None)
        self.assertEqual(state['assessments'][0]['resolution_reason'], 'caregiver_completed')
        self.assertEqual(state['assessments'][0]['caregiver_status'], 'resolved')

    def test_live_contract_display_and_offline_invalidation(self):
        _, state = report(1, 1, True, 'live')
        recorder = A05ContractRecords('home', 'cooker', 'sensor.report')
        hass = FakeHass()
        dashboard = HADashboard(hass, None, {})
        dashboard.show_observation(recorder.accept(state.attributes['raw_snapshot'], NOW, NOW))
        current = hass.states.get('sensor.bapsim_current_observation').attributes
        self.assertEqual(current['fields']['lid_open'], {'value': True, 'quality': 'good'})
        self.assertEqual(current['source'], 'matter')
        self.assertIsNone(current['observed_at'])
        dashboard.show_observation(recorder.offline(NOW))
        current = hass.states.get('sensor.bapsim_current_observation').attributes
        self.assertEqual(current['fields']['device_connection']['value'], 'offline')
        self.assertIsNone(current['fields']['lid_open']['value'])
