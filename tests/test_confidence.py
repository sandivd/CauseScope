import math
import html as html_module
import json
from dataclasses import replace

import pytest

from causescope.confidence import binomial_interval
from causescope.engine import ExperimentEngine
from causescope.spec import ConfirmationSettings, FailurePredicate, SpecError, parse_spec
from causescope.report import render_html, write_reports
from pathlib import Path


def binary(names):
    return {name:{'baseline':'0','values':['1']} for name in names}


def heldout(make_spec, **options):
    return replace(make_spec(parameters=binary('AB'), failure={'exit_code':23}),
                   confirmation=ConfirmationSettings(policy='holdout',**options))


def test_exact_binomial_intervals_match_boundaries_and_published_example():
    assert binomial_interval(10,10,.025)[0] == pytest.approx(.025**.1)
    assert binomial_interval(0,10,.025)[1] == pytest.approx(1-.025**.1)
    assert binomial_interval(0,10,.025)[0] == 0
    assert binomial_interval(10,10,.025)[1] == 1
    # NIST's 90% interval for four defects among twenty observations.
    assert binomial_interval(4,20,.05) == pytest.approx((.07135388,.40102812),abs=1e-7)


def test_binomial_coverage_by_exact_enumeration_instead_of_monte_carlo():
    n=12; alpha=.05
    bounds=[binomial_interval(k,n,alpha/2) for k in range(n+1)]
    for p in (.001,.01,.1,.3,.5,.8,.99,.999):
        coverage=sum(math.comb(n,k)*p**k*(1-p)**(n-k)
                     for k,(low,high) in enumerate(bounds) if low<=p<=high)
        assert coverage >= 1-alpha-1e-12


@pytest.mark.parametrize('count,n,tail',[(-1,3,.1),(4,3,.1),(1,0,.1),(1,1001,.1),(True,2,.1),(1,2,0),(1,2,float('nan'))])
def test_invalid_interval_input_rejected(count,n,tail):
    with pytest.raises(ValueError):
        binomial_interval(count,n,tail)


def test_holdout_freezes_fresh_family_and_never_reuses_discovery(make_spec,fake_runner):
    runner=fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' else 'pass')
    result=ExperimentEngine(heldout(make_spec),runner).run(allow_local_execution=True)
    validation=result['validation']
    assert result['evidence']['status']=='statistically_supported'
    assert result['diagnosis']['one_minimal']
    assert validation['statistics']['family_size']==4
    assert validation['statistics']['tail_alpha']==.05/8
    assert validation['observations']==256
    assert sum(len(e['runs']) for e in result['evaluations'])==12
    assert result['search']['subprocess_runs']==268
    ids=[r['observation_id'] for a in validation['arms'] for r in a['runs']]
    assert set(ids)==set(range(13,269))
    assert min(ids)>validation['last_discovery_observation']
    assert all(len(a['runs'])==64 for a in validation['arms'])
    assert all(a['runs'][0]['block']==1 and a['runs'][-1]['block']==64 for a in validation['arms'])
    assert sum(result['search']['observations_by_phase'].values())==268
    assert result['evidence']['discovery_observations']==12


def test_discovery_success_cannot_substitute_for_failed_holdout(make_spec,fake_runner):
    runner=fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' and n<=3 else 'pass')
    result=ExperimentEngine(heldout(make_spec),runner).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    assert result['evidence']['status']=='inconclusive'
    assert result['evidence']['discovery_status']=='repeated_failure'
    assert not result['diagnosis']['confirmed']
    assert result['validation']['statistics']['intervals']['candidate']['failures']==0
    assert result['validation']['observations']==256  # no successful or failed optional peeking


def test_mixed_validation_can_support_probability_contrast_without_minimality(make_spec,fake_runner):
    def outcomes(c,n):
        if c['A']==c['B']=='1':
            return 'failure'
        return 'failure' if n>3 and n%20==0 else 'pass'
    result=ExperimentEngine(heldout(make_spec),fake_runner(outcomes)).run(allow_local_execution=True)
    assert result['status']=='failure_found'
    assert result['diagnosis']['confirmed']
    assert not result['diagnosis']['one_minimal']
    assert result['evidence']['statistical_support']
    assert result['validation']['statistics']['intervals']['baseline']['failures']>0


def test_high_background_rate_does_not_confirm_an_association(make_spec,fake_runner):
    def outcomes(c,n):
        if n<=3:
            return 'failure' if c['A']==c['B']=='1' else 'pass'
        return 'pass' if n%10==0 else 'failure'
    result=ExperimentEngine(heldout(make_spec),fake_runner(outcomes)).run(allow_local_execution=True)
    assert not result['diagnosis']['confirmed']
    assert result['validation']['statistics']['candidate_minus_baseline_lower']<.2


@pytest.mark.parametrize('limit,global_limit,expected',[(5,None,'confirmation_max_runs'),(512,17,'max_runs')])
def test_partial_validation_budgets_are_inconclusive(make_spec,fake_runner,limit,global_limit,expected):
    spec=heldout(make_spec,max_runs=limit)
    spec=replace(spec,settings=replace(spec.settings,max_runs=global_limit))
    result=ExperimentEngine(spec,fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' else 'pass')).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    assert result['validation']['reason']==expected
    assert result['validation']['observations']==5
    assert result['validation']['statistics'] is None
    assert result['search']['subprocess_runs']==17
    assert result['search']['budget_exhausted']


def test_short_batch_cannot_support_even_unanimous_extreme_outcomes(make_spec,fake_runner):
    result=ExperimentEngine(heldout(make_spec,samples_per_arm=3),
        fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' else 'pass')).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    assert result['validation']['observations']==12
    assert result['validation']['reason']=='probability_bounds_insufficient'


@pytest.mark.parametrize('local', [True,False])
def test_validation_deadlines_include_last_observation(make_spec,fake_runner,local):
    now=[0.0]
    inner=fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' else 'pass')
    limits=[]
    def runner(spec,configuration):
        limits.append(spec.settings.timeout_seconds)
        now[0]+=.01
        return inner(spec,configuration)
    spec=heldout(make_spec,max_seconds=.045 if local else 60)
    if not local:
        spec=replace(spec,settings=replace(spec.settings,max_seconds=.165))
    result=ExperimentEngine(spec,runner,clock=lambda:now[0]).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    assert result['search']['stop_reason']==('confirmation_max_seconds' if local else 'max_seconds')
    assert result['validation']['observations']==5
    assert not result['diagnosis']['one_minimal']
    assert limits[-1] <= .0051


def test_unknown_validation_observation_is_retained_not_dropped(make_spec,fake_runner):
    inner=fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' else 'pass')
    calls=[0]
    def runner(spec,c):
        calls[0]+=1
        r=inner(spec,c)
        if calls[0]==15:
            r.outcome='invalid'; r.exit_code=64
        return r
    result=ExperimentEngine(heldout(make_spec),runner).run(allow_local_execution=True)
    assert result['validation']['observations']==3
    assert result['validation']['statistics'] is None
    assert any(r['outcome']=='invalid' for arm in result['validation']['arms'] for r in arm['runs'])
    assert result['status']=='inconclusive'


def test_constrained_invalid_deletion_does_not_become_minimality(make_spec,fake_runner):
    spec=replace(heldout(make_spec),constraints=({'A':'0','B':'1'},))
    result=ExperimentEngine(spec,fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' else 'pass')).run(allow_local_execution=True)
    assert result['diagnosis']['confirmed']
    assert not result['diagnosis']['one_minimal']
    assert result['validation']['excluded_invalid_deletions']==['A']
    assert all(spec.allows(a['configuration']) for a in result['validation']['arms'])


def test_deduplicated_baseline_is_not_a_fake_independent_deletion_arm(make_spec,fake_runner):
    spec=replace(heldout(make_spec),parameters={'A':heldout(make_spec).parameters['A']})
    result=ExperimentEngine(spec,fake_runner(lambda c,n:'failure' if c['A']=='1' else 'pass')).run(allow_local_execution=True)
    assert len(result['validation']['arms'])==2
    baseline=next(a for a in result['validation']['arms'] if a['id']=='baseline')
    assert baseline['roles']==['baseline','delete:A']
    assert result['validation']['observations']==128


def test_evidence_statuses_describe_observation_strength(make_spec,fake_runner):
    mixed=ExperimentEngine(make_spec(),fake_runner(lambda c,n:'failure' if n==1 else 'pass')).run(allow_local_execution=True)
    assert mixed['evidence']['discovery_status']=='observed_failure'
    repeated=ExperimentEngine(make_spec(),fake_runner(lambda c,n:'failure' if all(v=='on' for v in c.values()) else 'pass')).run(allow_local_execution=True)
    assert repeated['evidence']['status']=='repeated_failure'
    assert not repeated['evidence']['statistical_support']
    assert repeated['evidence']['fresh_validation_observations']==6
    assert repeated['evidence']['discovery_observations']==12


@pytest.mark.parametrize('field,value',[('policy','sequential'),('samples_per_arm',1),('samples_per_arm',1001),
    ('samples_per_arm',True),('alpha',0),('alpha',1),('alpha',1e-300),('alpha',False),
    ('min_effect',float('nan')),('min_effect',-1),('min_failure_probability',1),
    ('max_runs',0),('max_seconds',float('inf')),('max_seconds',True),('seed',-1),('typo',2)])
def test_confirmation_settings_strictly_validated(raw_spec,tmp_path,field,value):
    raw_spec['confirmation']={field:value}
    with pytest.raises(SpecError):
        parse_spec(raw_spec,tmp_path/'spec.yaml')


def test_confirmation_yaml_round_trip_and_execution_opt_in(raw_spec,tmp_path):
    raw_spec['confirmation']={'policy':'holdout','samples_per_arm':80,'seed':7}
    spec=parse_spec(raw_spec,tmp_path/'spec.yaml')
    assert spec.evidence()['confirmation']['samples_per_arm']==80
    with pytest.raises(ValueError,match='allow-local-execution'):
        ExperimentEngine(spec).run()


def test_real_holdout_processes_are_isolated_and_all_evidence_is_exported(make_spec,tmp_path):
    spec=heldout(make_spec,samples_per_arm=12,min_failure_probability=.5)
    spec=replace(spec,command=('{python}','-c',
        "import os; raise SystemExit(23 if os.getenv('A')==os.getenv('B')=='1' else 0)"))
    result=ExperimentEngine(spec).run(allow_local_execution=True)
    assert result['diagnosis']['one_minimal']
    runs=[r for e in result['evaluations'] for r in e['runs']]
    runs += [r for a in result['validation']['arms'] for r in a['runs']]
    assert len(runs)==60
    assert len({r['working_directory'] for r in runs})==60
    assert all(not Path(r['working_directory']).exists() for r in runs)
    evidence,report=write_reports(result,tmp_path/'report')
    html=report.read_text(encoding='utf-8')
    assert 'Independent holdout validation' in html
    assert 'Clopper-Pearson' in html
    assert all(r['working_directory'].replace('&','&amp;') in html for r in runs[:12])
    assert all(html_module.escape(json.dumps(r['working_directory'])) in html for r in runs[12:])


def test_unique_budget_exhaustion_cannot_be_promoted_by_statistical_validation(make_spec,fake_runner):
    spec=replace(heldout(make_spec),parameters={n:heldout(make_spec).parameters['A'] for n in 'ABCDE'})
    spec=replace(spec,settings=replace(spec.settings,max_configurations=7))
    result=ExperimentEngine(spec,fake_runner(lambda c,n:'failure' if all(v=='1' for v in c.values()) else 'pass')).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    assert not result['diagnosis']['confirmed']
    assert result['validation']['observations']==0
    assert result['validation']['reason']=='max_configurations'


def test_intermittent_candidate_support_is_distinct_from_unanimous_reproduction(make_spec,fake_runner):
    runner=fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' and n%10 else 'pass')
    result=ExperimentEngine(heldout(make_spec,min_failure_probability=.5),runner).run(allow_local_execution=True)
    assert result['evidence']['status']=='statistically_supported'
    assert result['diagnosis']['confirmed']
    assert not result['diagnosis']['one_minimal']
    candidate=next(a for a in result['validation']['arms'] if a['id']=='candidate')
    assert {r['outcome'] for r in candidate['runs']}=={'failure','pass'}


def test_local_deadline_timeout_cannot_support_a_timeout_predicate(make_spec,fake_runner):
    inner=fake_runner(lambda c,n:'failure' if c['A']==c['B']=='1' else 'pass')
    calls=[0]
    def runner(spec,c):
        calls[0]+=1
        record=inner(spec,c)
        record.timed_out=calls[0]>12 or record.outcome=='failure'
        return record
    spec=replace(heldout(make_spec,max_seconds=.5),failure=FailurePredicate(timeout=True))
    result=ExperimentEngine(spec,runner).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    observed=[r for a in result['validation']['arms'] for r in a['runs']]
    assert len(observed)==1 and observed[0]['budget_limited_timeout']
    assert observed[0]['outcome']=='inconclusive'


def test_engine_cannot_retry_after_a_zero_observation_deadline(make_spec,fake_runner):
    ticks=iter([0.0,2.0,2.0])
    spec=make_spec(settings={'max_seconds':1})
    engine=ExperimentEngine(spec,fake_runner(lambda c,n:'pass'),clock=lambda:next(ticks))
    assert engine.run(allow_local_execution=True)['search']['subprocess_runs']==0
    with pytest.raises(ValueError,match='only run once'):
        engine.run(allow_local_execution=True)


def test_real_holdout_timeout_with_tree_permissions_is_inconclusive(make_spec):
    from causescope.runner import run_once
    spec=heldout(make_spec,max_seconds=.2)
    spec=replace(spec,command=('{python}','-c',
        "import os,sys,time; time.sleep(30 if int(sys.argv[1])>12 else 0); "
        "raise SystemExit(23 if os.getenv('A')==os.getenv('B')=='1' else 0)"))
    calls=[0]
    def runner(spec,configuration):
        calls[0]+=1
        return run_once(replace(spec,command=spec.command+(str(calls[0]),)),configuration)
    result=ExperimentEngine(spec,runner).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    assert not result['diagnosis']['confirmed']
    assert result['search']['stop_reason']=='confirmation_max_seconds'
    runs=[r for arm in result['validation']['arms'] for r in arm['runs']]
    assert runs and any(r['timed_out'] and r['budget_limited_timeout'] for r in runs)
    assert all(r['termination_error'] is None and r['cleanup_error'] is None for r in runs)
    assert all(not Path(r['working_directory']).exists() for r in runs)
