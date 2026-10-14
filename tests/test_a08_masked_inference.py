import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from firmware.a08.profile import load_profile, render_profile
from tools.a07_edge.model import tree_predict, tree_predict_masked
from tools.a07_edge.observations import Calibration, extract

MASK = ((1 << 11)-1) & ~(3 << 6)


class MaskedInferenceTests(unittest.TestCase):
    def tree(self, right_class=2, right_score=1.):
        return [dict(feature=6, threshold=.5, left=1, right=2, **{'class':3, 'score':0}),
                dict(feature=-1, **{'class':2, 'score':1.}),
                dict(feature=-1, **{'class':right_class, 'score':right_score})]

    def test_missing_feature_never_selects_a_preferred_branch(self):
        x = np.zeros((3,11)); x[:,6] = [float('nan'), -999, 999]
        for nodes in (self.tree(1), self.tree(3), self.tree(2,.5)):
            p,s = tree_predict_masked(nodes,x,np.ones(3,bool),.975,MASK)
            self.assertTrue((p==3).all()); self.assertTrue((s==0).all())
        p,s = tree_predict_masked(self.tree(),x,np.ones(3,bool),.975,MASK)
        self.assertTrue((p==2).all()); self.assertTrue((s==1).all())

    def test_known_channel_fault_and_warm_blink_still_abstain(self):
        x=np.zeros((4,11)); x[0,1]=np.nan; x[1,3]=2; x[2,3]=np.nan
        p,_=tree_predict_masked(self.tree(),x,np.array([1,1,1,0],bool),.975,MASK)
        self.assertTrue((p==3).all())

    def test_real_model_consensus_cannot_disagree_with_timer_completions(self):
        m=json.loads(Path('firmware/a08/model.json').read_text())
        rng=np.random.default_rng(42); x=rng.uniform(0,1,(500,11)).astype(np.float32)
        x[:,1:8:2]=rng.integers(0,5,(500,4)); x[:,8]=rng.uniform(15,100,500);x[:,9]=rng.uniform(-3,3,500)
        valid=np.ones(500,bool)
        p,_=tree_predict_masked(m['nodes'],x,valid,m['score_threshold'],MASK)
        accepted=p!=3
        self.assertTrue(accepted.any())
        # Test both sides of every timer split, including exact boundaries.
        cuts=[np.float32(n['threshold']) for n in m['nodes'] if n['feature']==6]
        for value in [0.,1.,*cuts,*[np.nextafter(v,np.float32(np.inf)) for v in cuts]]:
            altered=x.copy(); altered[:,6]=value
            expected,_=tree_predict(m['nodes'],altered,valid,m['score_threshold'])
            np.testing.assert_array_equal(p[accepted],expected[accepted])

    def test_preprocessing_preserves_gaps_and_other_channel_faults(self):
        n=2500;t=np.arange(n)*50;adc=np.tile([500.,3000.,500.,np.nan,2048.],(n,1));lid=np.zeros(n);boot=np.ones(n)
        x,valid,ends,led=extract(t,adc,lid,boot,Calibration(),trusted_led_mask=7)
        self.assertTrue(valid.all());self.assertTrue(np.isnan(x[:,6:8]).all());self.assertTrue((led[:,3]==0).all())
        adc[1300:1400,0]=np.nan
        _,v,e,_=extract(t,adc,lid,boot,Calibration(),trusted_led_mask=7)
        self.assertFalse(v[(e>=1300)&(e<1400)].any())
        t[1500:]+=500
        _,v,e,_=extract(t,adc,lid,boot,Calibration(),trusted_led_mask=7)
        self.assertFalse(v[e>=1500].any())

    def test_checked_in_profile_header_matches_and_bad_settings_fail(self):
        with tempfile.TemporaryDirectory() as d:
            dest=Path(d)/'profile.hpp'
            render_profile('firmware/a08/site_profile.json',dest)
            self.assertEqual(dest.read_text(),Path('firmware/a08/site_profile.hpp').read_text())
            data=json.loads(Path('firmware/a08/site_profile.json').read_text())
            data['timer_trusted']='false'; src=Path(d)/'bad.json';src.write_text(json.dumps(data))
            with self.assertRaises(ValueError):load_profile(src)


if __name__=='__main__': unittest.main()
