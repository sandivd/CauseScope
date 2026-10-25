import itertools
from dataclasses import replace

import pytest

from causescope.engine import ExperimentEngine
from causescope.selection import Space, pairwise_candidates
from causescope.spec import Parameter, SpecError, parse_spec


def binary(names):
    return {name: {"baseline": "0", "values": ["1"]} for name in names}


@pytest.mark.parametrize("method", ["adaptive", "random", "pairwise", "one_at_a_time", "brute_force"])
def test_all_strategies_use_same_minimization_and_confirmation(make_spec, fake_runner, method):
    spec = make_spec(parameters=binary("ABC"), settings={"strategy": method, "seed": 7})
    runner = fake_runner(lambda c, n: "failure" if c['A'] == '1' else "pass")
    result = ExperimentEngine(spec, runner).run(allow_local_execution=True)
    assert result["status"] == "failure_found"
    assert result["diagnosis"]["changes"] == {"A": "1"}
    assert result["diagnosis"]["one_minimal"] and result["diagnosis"]["confirmed"]
    assert result["search"]["observations_by_phase"]["confirmation"] == 3
    assert result["search"]["observations_by_phase"]["baseline_confirmation"] == 3
    assert sum(result["search"]["observations_by_phase"].values()) == result["search"]["subprocess_runs"]


def test_standard_pairwise_array_covers_all_pairs(make_spec):
    space = Space(make_spec(parameters=binary("ABCDEFGH")))
    rows = list(pairwise_candidates(space, 23, lambda: None))
    assert len(rows) < 2 ** 8
    for a, b in itertools.combinations(space.names, 2):
        assert {(row[a], row[b]) for row in rows} == set(itertools.product("01", repeat=2))


def test_constrained_pairwise_coverage_is_audited_against_feasible_space(make_spec):
    spec = replace(make_spec(parameters=binary("ABCDE")), constraints=({"A": "1", "B": "1"},))
    space = Space(spec)
    rows = list(pairwise_candidates(space, 11, lambda: None))
    assert all(spec.allows(row) for row in rows)
    feasible = list(space.enumerate())
    for a, b in itertools.combinations(space.names, 2):
        assert {(row[a], row[b]) for row in rows} >= {(row[a], row[b]) for row in feasible}


@pytest.mark.parametrize("method", ["legacy", "adaptive", "random", "pairwise", "one_at_a_time", "brute_force"])
def test_invalid_public_combinations_never_execute(make_spec, fake_runner, method):
    spec = replace(make_spec(parameters=binary("ABC"), settings={"strategy": method}),
                   constraints=({"A": "1", "B": "1"},))
    def classify(c, n):
        assert spec.allows(c)
        return "pass"
    result = ExperimentEngine(spec, fake_runner(classify)).run(allow_local_execution=True)
    assert all(spec.allows(e['configuration']) for e in result['evaluations'])


def test_invalid_reductions_do_not_certify_minimality(make_spec, fake_runner):
    spec = replace(make_spec(parameters=binary("AB")), constraints=({"A": "0", "B": "1"},))
    result = ExperimentEngine(spec, fake_runner(lambda c, n: "failure" if c['A'] == c['B'] == '1' else "pass")).run(allow_local_execution=True)
    assert result['status'] == 'inconclusive'
    assert result['diagnosis']['confirmed']
    assert not result['diagnosis']['one_minimal']


def test_partial_batch_cannot_confirm_failure(make_spec, fake_runner):
    spec = make_spec(settings={"max_runs": 2})
    result = ExperimentEngine(spec, fake_runner(lambda c, n: "failure")).run(allow_local_execution=True)
    assert result['status'] == 'inconclusive'
    assert result['search']['subprocess_runs'] == 2
    assert result['evaluations'][0]['status'] == 'inconclusive'


def test_confirmations_share_total_run_budget(make_spec, fake_runner):
    spec = make_spec(parameters=binary("AB"), settings={"max_runs": 14})
    result = ExperimentEngine(spec, fake_runner(lambda c, n: "failure" if all(v == '1' for v in c.values()) else "pass")).run(allow_local_execution=True)
    assert result['search']['subprocess_runs'] == 14
    assert result['search']['stop_reason'] == 'max_runs'
    assert result['status'] == 'inconclusive'
    assert not result['diagnosis']['confirmed']


def test_deadline_includes_selection_and_clips_target_timeout(make_spec, fake_runner):
    now = [0.0]
    timeouts = []
    inner = fake_runner(lambda c, n: 'pass')
    def run(spec, configuration):
        timeouts.append(spec.settings.timeout_seconds)
        now[0] += 0.4
        return inner(spec, configuration)
    spec = make_spec(settings={"max_seconds": 1.0, "strategy": "adaptive"})
    result = ExperimentEngine(spec, run, clock=lambda: now[0]).run(allow_local_execution=True)
    assert result['status'] == 'inconclusive'
    assert result['search']['subprocess_runs'] == 3
    assert result['search']['stop_reason'] == 'max_seconds'
    assert timeouts == pytest.approx([1.0, 0.6, 0.2])


def test_adaptive_reuses_only_exact_evidence_and_is_seeded(make_spec, fake_runner):
    spec = make_spec(parameters=binary("ABCDEFGH"), settings={"strategy": "adaptive", "seed": 37, "max_configurations": 20})
    orders = []
    for _ in range(2):
        result = ExperimentEngine(spec, fake_runner(lambda c, n: 'pass')).run(allow_local_execution=True)
        orders.append([e['configuration'] for e in result['evaluations']])
        assert result['search']['subprocess_runs'] == 3 * len(result['evaluations'])
        assert result['status'] == 'inconclusive'
    assert orders[0] == orders[1]


def test_known_clean_small_valid_space_can_finish(make_spec, fake_runner):
    spec = replace(make_spec(parameters=binary("ABCDEFGHIJ"), settings={"strategy": "adaptive"}),
                   constraints=tuple({name: '1'} for name in 'ABCDEFGHI'))
    result = ExperimentEngine(spec, fake_runner(lambda c, n: 'pass')).run(allow_local_execution=True)
    assert result['status'] == 'no_failure_found'
    assert result['search']['valid_configurations'] == 2
    assert result['search']['search_complete']


@pytest.mark.parametrize("field,value", [('strategy','unknown'), ('seed', True), ('seed', -1),
    ('max_runs',0),('max_runs',False),('max_seconds',0),('max_seconds',float('inf')),('candidate_pool',1)])
def test_bad_selection_settings_rejected(make_spec, field, value):
    with pytest.raises(SpecError):
        make_spec(settings={field:value})


@pytest.mark.parametrize("constraints", [[{'forbid':{}}], [{'forbid':{'MISSING':'1'}}],
    [{'forbid':{'CAUSESCOPE_CACHE':'invalid'}}], [{'forbid':{'CAUSESCOPE_CACHE':'off'}}], {'forbid':{}}])
def test_bad_or_baseline_excluding_constraints_rejected(raw_spec, tmp_path, constraints):
    raw_spec['constraints'] = constraints
    with pytest.raises(SpecError):
        parse_spec(raw_spec, tmp_path/'spec.yaml')


def test_runtime_invalid_exit_code_is_not_a_failure_or_health(make_spec):
    from causescope.runner import run_once
    spec = replace(make_spec(command=['{python}','-c','raise SystemExit(64)']),invalid_exit_codes=(64,))
    run = run_once(spec, spec.baseline)
    assert run.outcome == 'invalid'
    result = ExperimentEngine(spec).run(allow_local_execution=True)
    assert result['status'] == 'inconclusive'


def test_unstarted_configuration_is_not_counted(make_spec, fake_runner):
    ticks = iter([0.0, 2.0, 2.0])
    result = ExperimentEngine(make_spec(settings={'max_seconds': 1}),
        fake_runner(lambda c,n:'pass'), clock=lambda: next(ticks)).run(allow_local_execution=True)
    assert result['search']['tested_configurations'] == 0
    assert result['search']['subprocess_runs'] == 0


def test_budget_shortened_timeout_is_not_a_reproduced_timeout_failure(make_spec, fake_runner):
    inner = fake_runner(lambda c,n:'failure')
    def timed_out(spec, configuration):
        record = inner(spec, configuration)
        record.timed_out = True
        return record
    result = ExperimentEngine(make_spec(failure={'timeout':True},settings={'max_seconds':1}),timed_out).run(allow_local_execution=True)
    assert result['status'] == 'inconclusive'
    assert result['evaluations'][0]['runs'][0]['budget_limited_timeout']
