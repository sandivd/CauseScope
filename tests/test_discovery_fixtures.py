import itertools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.discovery.generate import KINDS, fixture
from benchmarks.discovery.program import evaluate, stable_failure
from benchmarks.discovery.study import FixtureRunner, truth_check
from causescope.engine import ExperimentEngine
from causescope.spec import parse_spec


@pytest.mark.parametrize('dimension',[10,20,32])
@pytest.mark.parametrize('kind',KINDS)
def test_seeded_fixture_is_deterministic_and_has_valid_baseline(kind,dimension,tmp_path):
    public,oracle=fixture(99991,dimension,kind)
    assert (public,oracle)==fixture(99991,dimension,kind)
    other=fixture(99989,dimension,kind)
    assert oracle!=other[1]
    spec=parse_spec(public,tmp_path/'experiment.yaml')
    assert evaluate(oracle,spec.baseline,1)==0
    assert spec.allows(spec.baseline)
    assert len(spec.parameters)==dimension
    if kind not in ('clean','exhaustive_clean'):
        witness={**spec.baseline,**oracle['literals']}
        assert evaluate(oracle,witness,1)==23
        assert spec.allows(witness)
        assert stable_failure(oracle,witness)==(oracle['period'] is None)


@pytest.mark.parametrize('kind',KINDS)
def test_cli_and_simulation_semantics_match_on_32_parameters(kind,tmp_path):
    public,oracle=fixture(65537,32,kind)
    path=tmp_path/'oracle.json'; path.write_text(json.dumps(oracle),encoding='utf-8')
    public['command'][2]=str(path)
    spec=parse_spec(public,tmp_path/'experiment.yaml')
    witness={**spec.baseline,**oracle['literals']}
    simulated=FixtureRunner(oracle,'simulation')(spec,witness)
    actual=FixtureRunner(oracle,'subprocess')(spec,witness)
    assert (actual.exit_code,actual.outcome)==(simulated.exit_code,simulated.outcome)
    assert not Path(actual.working_directory).exists()


def test_alternating_flake_remains_inconclusive(tmp_path):
    public,oracle=fixture(99991,10,'flaky')
    public['settings']['max_runs']=60
    spec=parse_spec(public,tmp_path/'experiment.yaml')
    result=ExperimentEngine(spec,FixtureRunner(oracle,'simulation')).run(allow_local_execution=True)
    assert result['status']=='inconclusive'
    assert not result['diagnosis']
    assert truth_check(oracle,spec,result)['event_detected']


def test_rare_flake_can_fool_finite_confirmation_and_audit_exposes_it(tmp_path):
    public,oracle=fixture(99991,10,'rare_flaky')
    spec=parse_spec(public,tmp_path/'experiment.yaml')
    result=ExperimentEngine(spec,FixtureRunner(oracle,'simulation')).run(allow_local_execution=True)
    assert result['diagnosis']['confirmed']
    audit=truth_check(oracle,spec,result)
    assert audit['false_confirmation']
    assert not audit['successful_isolation']


def test_nonmonotonic_mask_and_invalid_codes_are_independent_of_algorithm():
    public,oracle=fixture(99991,20,'nonmonotonic')
    witness={**oracle['baseline'],**oracle['literals']}
    assert evaluate(oracle,witness,1)==23
    assert evaluate(oracle,{**witness,oracle['mask']:'1'},1)==0
    public,oracle=fixture(99991,20,'constrained')
    invalid={**oracle['baseline'],**oracle['runtime_invalid'][0]}
    assert evaluate(oracle,invalid,1)==64
