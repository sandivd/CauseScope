"""Fixed-sample binomial bounds; no inference from adaptive discovery samples."""

from __future__ import annotations

import math
from functools import lru_cache


@lru_cache(maxsize=128)
def _log_coefficients(n: int) -> tuple[float, ...]:
    return tuple(math.lgamma(n+1)-math.lgamma(k+1)-math.lgamma(n-k+1) for k in range(n+1))


def _lower_bound(failures: int, n: int, tail: float) -> float:
    if failures == 0:
        return 0.0
    if failures == n:
        return max(0.0, tail ** (1/n)-1e-12)
    coefficients = _log_coefficients(n)
    low, high = 0.0, 1.0
    for _ in range(60):
        p = (low+high)/2
        logp, logq = math.log(p), math.log1p(-p)
        probability = math.fsum(math.exp(coefficients[k]+k*logp+(n-k)*logq)
                                for k in range(failures, n+1))
        if probability < tail:
            low = p
        else:
            high = p
    # Return the conservative side of the numerical bracket.
    return max(0.0, low-1e-12)


def binomial_interval(failures: int, n: int, tail: float) -> tuple[float, float]:
    """Clopper-Pearson interval allocating `tail` to each side, under IID sampling."""
    if (type(n) is not int or not 1 <= n <= 1000 or type(failures) is not int
            or not 0 <= failures <= n or not math.isfinite(tail) or not 0 < tail < .5):
        raise ValueError('Invalid binomial count or tail probability')
    return _lower_bound(failures, n, tail), 1-_lower_bound(n-failures, n, tail)


def assess(arms: list[dict], settings) -> dict:
    """Analyze a complete predeclared family once, never at intermediate looks."""
    tail = settings.alpha/(2*len(arms))
    intervals = {}
    for arm in arms:
        if len(arm['runs']) != settings.samples_per_arm or any(
                run['outcome'] not in ('failure', 'pass') for run in arm['runs']):
            raise ValueError('Only complete valid fixed-sample families can be assessed')
        failures = sum(run['outcome']=='failure' for run in arm['runs'])
        lower, upper = binomial_interval(failures, len(arm['runs']), tail)
        intervals[arm['id']] = {'failures':failures,'observations':len(arm['runs']),
            'failure_rate':failures/len(arm['runs']),'lower':lower,'upper':upper}
    candidate, baseline = intervals['candidate'], intervals['baseline']
    contrast = candidate['lower']-baseline['upper']
    return {'method':'fixed-sample Clopper-Pearson with Bonferroni simultaneous bounds',
            'alpha':settings.alpha,'tail_alpha':tail,'family_size':len(arms),
            'intervals':intervals,'candidate_minus_baseline_lower':contrast,
            'candidate_minus_control_lower':{key:candidate['lower']-value['upper']
                for key,value in intervals.items() if key!='candidate'},
            'supported':candidate['lower']>=settings.min_failure_probability and contrast>=settings.min_effect,
            'min_failure_probability':settings.min_failure_probability,'min_effect':settings.min_effect,
            'assumptions':['fresh observations independent of discovery history',
                'stationary IID Bernoulli observations within each arm',
                'one frozen family; fixed sample size; no optional stopping or retries'],
            'scope':'one invocation, conditional on discovery; no across-invocation error control',
            'meaning':'simultaneous repeated-sampling coverage under assumptions, not probability the claim is true'}
