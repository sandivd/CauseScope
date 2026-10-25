"""Isolated fixed-sample validation of one candidate selected using discovery only."""

from __future__ import annotations

import random
from dataclasses import asdict

from .confidence import assess
from .selection import PlanningStopped


def holdout(spec, changes, evaluations, observe, clock, first_observation,
            invocation_deadline=None, blocked_reason=None):
    settings = spec.confirmation
    started = clock()
    deadline = started+settings.max_seconds
    time_reason = 'confirmation_max_seconds'
    if invocation_deadline is not None and invocation_deadline < deadline:
        deadline = invocation_deadline
        time_reason = 'max_seconds'
    arms = []
    by_configuration = {}

    def add(identifier, configuration, role):
        key = tuple(sorted(configuration.items()))
        if key in by_configuration:
            by_configuration[key]['roles'].append(role)
        else:
            arm = {'id':identifier,'configuration':dict(configuration),'roles':[role],'runs':[]}
            by_configuration[key] = arm
            arms.append(arm)

    candidate = {**spec.baseline, **dict(changes)}
    add('candidate', candidate, 'candidate')
    add('baseline', spec.baseline, 'baseline')
    excluded = []
    for name, _ in changes:
        reduced = {**candidate, name:spec.baseline[name]}
        if spec.allows(reduced):
            add('delete:'+name, reduced, 'delete:'+name)
        else:
            excluded.append(name)
    result = {'policy':'holdout','status':'inconclusive','supported':False,
        'reason':None,'arms':arms,'excluded_invalid_deletions':excluded,
        'samples_per_arm':settings.samples_per_arm,'planned_observations':len(arms)*settings.samples_per_arm,
        'observations':0,'last_discovery_observation':first_observation-1,
        'order_seed':settings.seed,'fixed_family':True,'statistics':None}
    if blocked_reason:
        result['reason'] = blocked_reason
        result['duration_seconds'] = clock()-started
        return result
    # No hidden extra unique-configuration budget: every arm must already have
    # been executed by baseline/discovery/deletion checks. Unknown arms stay unknown.
    for arm in arms:
        key = tuple(sorted((n,v) for n,v in arm['configuration'].items() if v!=spec.baseline[n]))
        if key not in evaluations or not evaluations[key].runs:
            result['reason'] = 'untested_deletion_or_configuration_budget'
            result['duration_seconds'] = clock()-started
            return result
    rng = random.Random(settings.seed)
    try:
        for block in range(settings.samples_per_arm):
            order = list(arms)
            rng.shuffle(order)
            for arm in order:
                if result['observations'] >= settings.max_runs:
                    raise PlanningStopped('confirmation_max_runs')
                if clock() >= deadline:
                    raise PlanningStopped(time_reason)
                record = observe(arm['configuration'], deadline)
                arm['runs'].append({**asdict(record),'observation_id':first_observation+result['observations'],
                                    'block':block+1,'phase':'holdout_confirmation'})
                result['observations'] += 1
                if record.outcome not in ('pass','failure'):
                    raise PlanningStopped('invalid_or_unknown_validation_observation')
        if clock() > deadline:
            raise PlanningStopped(time_reason)
        result['statistics'] = assess(arms, settings)
        if clock() > deadline:
            raise PlanningStopped(time_reason)
        result['supported'] = result['statistics']['supported']
        result['status'] = 'statistically_supported' if result['supported'] else 'inconclusive'
        result['reason'] = 'fixed_sample_criteria_met' if result['supported'] else 'probability_bounds_insufficient'
    except PlanningStopped as exc:
        result['reason'] = str(exc)
    result['duration_seconds'] = clock()-started
    return result


def evidence_status(evaluations, validation, diagnosis, repetitions):
    """Descriptive evidence labels never imply mathematical certainty."""
    runs = [run for item in evaluations for run,phase in zip(item.runs,item.run_phases)
            if phase not in ('confirmation','baseline_confirmation')]
    failures = sum(run.outcome=='failure' for run in runs)
    batches = [[run for run,phase in zip(item.runs,item.run_phases)
                if phase not in ('confirmation','baseline_confirmation')] for item in evaluations]
    repeated = any(len(batch)>=repetitions and all(r.outcome=='failure' for r in batch) for batch in batches)
    discovery = ('repeated_failure' if repeated else 'observed_failure' if failures else
                 'inconclusive' if any(run.outcome not in ('pass','failure') for run in runs) or not runs
                 else 'no_failure_observed')
    confirmation = ('not_attempted' if diagnosis is None else
                    validation['status'] if validation else
                    'repeated_failure' if diagnosis['confirmed'] else 'inconclusive')
    return {'status':confirmation if confirmation!='not_attempted' else discovery,
        'discovery_status':discovery,'confirmation_status':confirmation,
        'discovery_failures':failures,'discovery_observations':len(runs),
        'fresh_validation_observations':validation['observations'] if validation else
            sum(item.run_phases.count('confirmation')+item.run_phases.count('baseline_confirmation') for item in evaluations),
        'statistical_support':bool(validation and validation['supported']),
        'certainty':'finite observations; no mathematical certainty or proven root cause'}
