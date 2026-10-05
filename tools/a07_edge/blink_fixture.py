"""Exact synthetic square-wave sampling; no floating boundary drift.

This only constructs test input. It does not detect blinking or label real data.
"""
from fractions import Fraction
from math import lcm
import numpy as np


def periodic_on(t_ms, hz, duty, phase):
    frequency, duty, phase=(Fraction(str(v)) for v in (hz,duty,phase))
    scale=lcm(1000*frequency.denominator,duty.denominator,phase.denominator)
    ticks=np.asarray(t_ms,dtype=np.int64)*(frequency.numerator*scale//(1000*frequency.denominator))
    ticks+=phase.numerator*(scale//phase.denominator)
    return ticks%scale < duty.numerator*(scale//duty.denominator)
