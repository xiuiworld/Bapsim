import unittest

from tools.build_caregiver_replay import build


class ReplayTests(unittest.TestCase):
    def test_real_policy_and_isolated_outputs(self):
        result = build()
        self.assertEqual(result, build())
        self.assertFalse(result['real_push_sent'])
        frames = result['frames']
        outputs = [f['states']['sensor.bapsim_output_state']['attributes'] for f in frames]
        self.assertTrue(all(o['source'] == 'simulator' and o['household_id'] == 'demo_household' for o in outputs))
        start, request = result['start_index'], result['request_index']
        checking, resolved = (result['response_indices'][k] for k in ('checking', 'resolved'))
        self.assertEqual(outputs[start]['learning'], 'personalized')
        self.assertTrue(all(not o['assessments'] for o in outputs[start:request]))
        self.assertTrue(any(f['states'].get('sensor.bapsim_detection_status',{}).get('state')=='check_required' for f in frames[start:request]))
        self.assertEqual(outputs[request]['assessments'][0]['status'], 'notified')
        self.assertTrue(frames[request]['wait_for_response'])
        self.assertEqual(outputs[checking]['assessments'][0]['caregiver_status'], 'checking')
        self.assertEqual(outputs[resolved]['assessments'][0]['resolution_reason'], 'caregiver_completed')
        self.assertNotEqual(outputs[resolved]['last_usage_at'], outputs[-1]['last_usage_at'])
        self.assertEqual(outputs[-1]['assessments'][0]['resolution_reason'], 'caregiver_completed')
        self.assertTrue(all(f['states']['sensor.bapsim_notification_delivery']['state'] == 'simulation' for f in frames))


if __name__ == '__main__':
    unittest.main()
