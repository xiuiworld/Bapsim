"""Optical correction must reject reference faults and preserve real blinking."""
from dataclasses import replace
import unittest
import numpy as np
from tools.a07_edge.observations import Calibration, extract


class OpticalLeakageTests(unittest.TestCase):
    def setUp(self):
        self.cal = Calibration(led_off=(220,820,1850,202),
            led_on=(1180,1320,2600,810), warm_white_gain=.215,
            white_reference=618.293)
        self.n = 3600
        self.t = np.arange(self.n)*50

    def signal(self, warm=1000.):
        white = np.where(np.arange(self.n)%20<10, 2600., 618.293)
        adc = np.column_stack((np.full(self.n,1200.),
            warm+self.cal.warm_white_gain*(white-self.cal.white_reference),
            white, np.full(self.n,np.nan), np.full(self.n,2048.)))
        return adc

    def run_signal(self, adc, cal=None, t=None):
        return extract(self.t if t is None else t, adc, np.zeros(self.n),
            np.ones(self.n), cal or self.cal, trusted_led_mask=7)

    def test_white_blink_leakage_is_not_warm_blink(self):
        adc=self.signal()
        _,_,_,before=self.run_signal(adc,replace(self.cal,warm_white_gain=0))
        _,valid,_,after=self.run_signal(adc)
        self.assertTrue((before[:,1]==3).all())
        self.assertTrue((after[:,1]==1).all())
        self.assertTrue((after[:,2]==3).all())
        self.assertTrue(valid.all())

    def test_real_warm_blink_is_not_hidden_by_correction(self):
        warm=np.where(np.arange(self.n)%100<50,1300.,900.)
        _,valid,_,led=self.run_signal(self.signal(warm))
        self.assertTrue((led[:,1]==3).all())
        self.assertFalse(valid.any())

    def test_solid_warm_survives_white_blink_and_offset(self):
        for offset in (-100,0,100):
            adc=self.signal(1300.);adc[:,:4]+=offset
            _,_,_,led=self.run_signal(adc)
            self.assertTrue((led[:,1]==2).all())
            self.assertTrue((led[:,3]==0).all())

    def test_bad_white_reference_invalidates_warm_without_imputation(self):
        for bad in (np.nan,0.,4095.):
            adc=self.signal(1300.);adc[1300:1400,2]=bad
            _,valid,ends,led=self.run_signal(adc)
            affected=(ends>=1300)&(ends<1400)
            self.assertTrue((led[affected,1]==0).all())
            self.assertFalse(valid[affected].any())
            self.assertTrue((led[affected,0]==2).all())

    def test_gap_and_future_changes_cannot_be_hidden(self):
        adc=self.signal(1300.); x,_,ends,_=self.run_signal(adc)
        adc[2100:,1]=800
        y,_,_,_=self.run_signal(adc)
        np.testing.assert_array_equal(x[ends<2100],y[ends<2100])
        t=self.t.copy();t[1500:]+=500
        _,valid,ends,_=self.run_signal(adc,t=t)
        self.assertFalse(valid[(ends>=1500)&(ends<2699)].any())

    def test_impossible_leakage_residual_is_unknown_not_off(self):
        adc=self.signal();adc[:,1]=100.;adc[:,2]=4000.
        _,valid,_,led=self.run_signal(adc)
        self.assertTrue((led[:,1]==0).all())
        self.assertFalse(valid.any())

    def test_invalid_parameters_fail_closed(self):
        for field, value in [('warm_white_gain',-1),('warm_white_gain',1.01),
                             ('warm_white_gain',float('nan')),('white_reference',4096)]:
            with self.assertRaises(ValueError):
                replace(self.cal,**{field:value}).validate()


if __name__=='__main__': unittest.main()
