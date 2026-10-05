import unittest
from dataclasses import replace
import numpy as np
from .observations import Calibration, extract


class RecoveryTests(unittest.TestCase):
    def test_board_safety_calibration_matrix(self):
        # Matches the explicitly enumerated board safety test, including legal
        # zero/negative temperature offsets and zero LED endpoints.
        fields = ('adc_max','series_ohms','ntc_r25','ntc_beta','offset_c',
                  *(f'off{i}' for i in range(4)), *(f'on{i}' for i in range(4)))
        rejected = accepted = 0
        for index, field in enumerate(fields):
            for value in (float('nan'),float('inf'),float('-inf'),0,-1):
                expected = np.isfinite(value) and (index == 4 or (index >= 5 and value == 0))
                if index >= 5:
                    name = 'led_off' if index < 9 else 'led_on'
                    values = list(getattr(Calibration(), name))
                    values[(index-5)%4] = value
                    c = replace(Calibration(), **{name: tuple(values)})
                else:
                    c = replace(Calibration(), **{field:value})
                with self.subTest(field=field,value=value):
                    if expected:
                        c.validate(); accepted += 1
                    else:
                        with self.assertRaises(ValueError): c.validate()
                        rejected += 1
        self.assertEqual((rejected,accepted),(55,10))

    def test_invalid_calibration_is_rejected_before_temperature_conversion(self):
        for field in ('adc_max','series_ohms','ntc_r25','ntc_beta','offset_c'):
            for value in (float('inf'),float('-inf'),float('nan')):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    replace(Calibration(),**{field:value}).validate()
        for field in ('series_ohms','ntc_r25','ntc_beta'):
            for value in (0,-1):
                with self.assertRaises(ValueError):replace(Calibration(),**{field:value}).validate()
        for off,on in [((-1,)*4,(3000,)*4),((500,)*4,(5000,)*4),((500,)*4,(550,)*4)]:
            with self.assertRaises(ValueError):Calibration(led_off=off,led_on=on).validate()
        Calibration(led_off=(3000,)*4,led_on=(500,)*4).validate()

    def test_uncertain_light_stays_unknown_after_every_discontinuity(self):
        for initial in (500,3000):
            for fault in ('time','sequence','boot','missing','rail','startup'):
                with self.subTest(initial=initial,fault=fault):
                    n=2840;t=np.arange(n)*50;seq=np.arange(n);boot=np.ones(n)
                    adc=np.tile([1750.,3000.,500.,500.,2048.],(n,1))
                    adc[:1200,0]=initial
                    if fault=='time':t[1200:]+=200
                    if fault=='sequence':seq[1200:]+=1
                    if fault=='boot':boot[1200:]=2
                    if fault=='missing':adc[1200,0]=np.nan
                    if fault=='rail':adc[1200,0]=4095
                    if fault=='startup':adc[:,0]=1750
                    x,valid,ends,led=extract(t,adc,np.zeros(n),boot,Calibration(),sequence=seq)
                    self.assertFalse(valid[-1]);self.assertEqual(led[-1,0],0)
                    self.assertEqual(led[-1,1],2)  # other channel still known
                    adc[2400:,0]=3000
                    x,valid,ends,led=extract(t,adc,np.zeros(n),boot,Calibration(),sequence=seq)
                    self.assertTrue(valid[-1]);self.assertEqual(led[-1,0],2)

    def test_continuous_hysteresis_preserves_known_state(self):
        for initial,expected in ((500,1),(3000,2)):
            adc=np.tile([1750.,3000.,500.,500.,2048.],(1240,1));adc[0,0]=initial
            _,valid,_,led=extract(np.arange(1240)*50,adc,np.zeros(1240),np.ones(1240),Calibration())
            self.assertTrue(valid.all());self.assertTrue((led[:,0]==expected).all())
