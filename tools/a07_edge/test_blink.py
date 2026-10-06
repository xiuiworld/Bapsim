"""Independent temporal assertions; synthetic timing bounds are not measurements."""
import unittest
import numpy as np
from .observations import Calibration, extract
from .model import tree_predict
from .blink_fixture import periodic_on


def waveform(hz, duty=.5, phase=0., seconds=180):
    t = np.arange(seconds*20)*50
    on = periodic_on(t,hz,duty,phase)
    return t, on


def observe(t, on, channel=1):
    adc=np.tile([500.,3000.,500.,500.,2048.],(len(t),1))
    adc[:,channel]=np.where(on,3000.,500.)
    return extract(t,adc,np.zeros(len(t)),np.ones(len(t)),Calibration())


class SlowBlinkTests(unittest.TestCase):
    def test_complete_led_history_excludes_the_preceding_broken_link(self):
        t=np.arange(1720)*50;t[1200:]+=200
        _,valid,ends,led=observe(t,np.ones(len(t),bool))
        self.assertEqual(led[ends==1559,1].item(),0)
        self.assertEqual(led[ends==1599,1].item(),2)
        self.assertFalse(valid[ends==1599].item())  # 60 s model coverage is not restored yet

    def test_frequency_duty_phase_grid_never_becomes_solid(self):
        for hz in (.1,.15,.25,.5,1.,2.):
            for duty in (.1,.5,.9):
                for phase in (0.,.23,.71):
                    with self.subTest(hz=hz,duty=duty,phase=phase):
                        x,valid,_,led=observe(*waveform(hz,duty,phase))
                        self.assertTrue((led[:,1]==3).all())
                        self.assertFalse(valid.any())
                        # Even a tree that always says warming must abstain.
                        node=dict(feature=-1,threshold=0,left=-1,right=-1,score=1.,**{'class':2})
                        pred,_=tree_predict([node],x,valid)
                        self.assertTrue((pred==3).all())

    def test_single_transition_or_pulse_is_uncertain_then_settles(self):
        for pulse in (False,True):
            t=np.arange(2400)*50;on=np.ones(2400,bool)
            on[1600:]=False
            if pulse:on[1620:]=True
            _,valid,ends,led=observe(t,on)
            self.assertFalse((led[:,1]==3).any())
            self.assertTrue((led[(ends>=1600)&(ends<1999),1]==0).all())
            self.assertFalse(valid[(ends>=1600)&(ends<1999)].any())
            self.assertEqual(led[-1,1],2 if pulse else 1)
            self.assertTrue(valid[-1])

    def test_blink_to_solid_has_bounded_settling_and_all_channels_work(self):
        t,on=waveform(.15,seconds=140)
        on[t>=90000]=True
        for channel in range(4):
            _,valid,ends,led=observe(t,on,channel)
            self.assertTrue((led[t[ends]<90000,channel]==3).all())
            self.assertTrue((led[t[ends]>=110000,channel]==2).all())
            self.assertTrue(valid[t[ends]>=110000].all())

    def test_prefix_does_not_depend_on_future_light(self):
        t,on=waveform(.13,seconds=140)
        x,v,e,led=observe(t,on)
        changed=on.copy();changed[2000:]=True
        xx,vv,ee,ll=observe(t,changed)
        np.testing.assert_array_equal(led[e<2000],ll[ee<2000])
        np.testing.assert_array_equal(v[e<2000],vv[ee<2000])
        np.testing.assert_array_equal(x[e<2000],xx[ee<2000])
