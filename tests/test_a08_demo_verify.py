"""Verification must not silently claim raw sequence coverage for demo logs."""
from pathlib import Path
import tempfile
import unittest

from firmware.a08.verify import verify


class DemoLogVerification(unittest.TestCase):
    def log(self):
        lines = [
            'A08 INTEGRATION END failures=0',
            'A08 ADC config gain=1/4 reference_mv=900 resolution=12',
            'A08 START confirmed=1 cal_valid=1 driver_errors=0',
            'A08 CONTACT enabled=1 high_is_open=1 pin=P1.10',
            'A08 ADC raw=500,1000,1500,2000,1400 status=0,0,0,0,0',
            'A08 ADC mv=439,878,1318,1757,1230',
            'A08 SUPPLY raw=3440 mv=3023 temperature_enabled=1',
        ]
        for i in range(6):
            lines += [
                f'A08 R c={i+1} p=0 h=0 l=0 f=3 t=3400 led=2,2,1,1',
                'A08 TIMING samples=200 min_ms=49 max_ms=51 gaps=0',
                'A08 ACQUIRE read_max_us=600 late_max_ms=1 queue_dropped=0',
            ]
        return '\n'.join(lines)

    def check(self, text, raw_uart=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'boot.log'
            path.write_text(text)
            return verify(path, temperature=True, contact=True,
                          optical=True, raw_uart=raw_uart)

    def test_demo_does_not_claim_raw_or_physical_accuracy(self):
        result = self.check(self.log())
        self.assertFalse(result['raw_sequence_verified'])
        self.assertFalse(result['physical_sensors_verified'])

    def test_original_raw_requirement_remains_strict(self):
        with self.assertRaisesRegex(AssertionError, 'raw 20 Hz'):
            self.check(self.log(), raw_uart=True)

    def masked_log(self):
        return self.log().replace('led=2,2,1,1','led=2,2,1,0')+'\nA08 POLICY trusted_led_mask=7 raw_uart=0'

    def test_untrusted_timer_unknown_is_accepted(self):
        self.assertEqual(self.check(self.masked_log())['trusted_led_mask'],7)

    def test_untrusted_timer_cannot_be_reported_as_blinking(self):
        with self.assertRaisesRegex(AssertionError,'untrusted LED'):
            self.check(self.masked_log().replace('led=2,2,1,0','led=2,2,1,3'))

    def test_trusted_channel_still_required(self):
        with self.assertRaisesRegex(AssertionError,'complete calibrated'):
            self.check(self.masked_log().replace('led=2,2,1,0','led=2,0,1,0'))

    def test_policy_raw_conflict_rejected(self):
        with self.assertRaisesRegex(AssertionError,'policy mismatch'):
            self.check(self.masked_log().replace('raw_uart=0','raw_uart=1'))

    def test_queue_loss_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'queue dropped'):
            self.check(self.log().replace('queue_dropped=0', 'queue_dropped=1', 1))

    def test_incomplete_diagnostics_are_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'insufficient acquisition'):
            self.check(self.log().replace('A08 ACQUIRE', 'missing', 1))

    def test_period_failures_are_not_hidden_by_silent_uart(self):
        with self.assertRaisesRegex(AssertionError, 'continuous 20 Hz'):
            self.check(self.log().replace('max_ms=51 gaps=0', 'max_ms=90 gaps=1'))

    def test_unexpected_raw_output_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'raw UART still enabled'):
            self.check(self.log()+'\nA08 SAMPLE boot=1 seq=0 ms=50 raw=1,2,3,4,5 supply=3440 lid=0')


if __name__ == '__main__':
    unittest.main()
