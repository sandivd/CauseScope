"""Opt-in third OSS application; documented guard, not a planted/historical bug."""
import json
import os
import time
from pathlib import Path

import pytest

from benchmarks.developer_workflow.coverage_case.adapter import HERE, execute, verify
from causescope.service import InvestigationService

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def pinned_coverage():
    if os.environ.get('CAUSESCOPE_RUN_COVERAGE_INTEGRATION') != '1':
        pytest.skip('Set CAUSESCOPE_RUN_COVERAGE_INTEGRATION=1 after pinned Coverage setup')
    return verify()


@pytest.mark.parametrize('branch,threshold,expected', [('false','0',0),('true','0',0),('false','90',0),('true','90',2)])
def test_native_original_coverage_guard(pinned_coverage, tmp_path, branch, threshold, expected):
    record = execute(tmp_path, branch, threshold)
    assert record['native'][-1]['exit_code'] == expected
    assert ('Coverage failure' in record['native'][-1]['stdout']) == (expected == 2)
    assert len(record['native']) == 3


def test_coverage_service_diagnosis_matches_native_four_cell_table(pinned_coverage, tmp_path):
    import yaml
    raw = yaml.safe_load((HERE/'experiment.yaml').read_text())
    raw['command'] = [arg.replace('{spec_dir}',str(HERE)) for arg in raw['command']]
    (tmp_path/'spec.yaml').write_text(yaml.safe_dump(raw), encoding='utf-8')
    service = InvestigationService(tmp_path, allow_local_execution=True)
    plan = service.prepare('spec.yaml','artifacts')
    grant = service.approve(plan['plan_path'], allow_local_execution=True)
    job = service.start(plan['id'],grant['approval_id'])
    deadline = time.monotonic()+90
    while service.inspect(job['job_id'])['state'] == 'running' and time.monotonic() < deadline:
        time.sleep(.05)
    assert service.inspect(job['job_id'])['state'] == 'completed'
    result = service.findings(job['job_id'])
    assert result['diagnosis']['changes'] == {'COVERAGE_BRANCH':'true','COVERAGE_THRESHOLD':'90'}
    assert result['diagnosis']['one_minimal'] and result['evidence']['status'] == 'repeated_failure'
    assert result['counts']['subprocess_runs'] == 18


def test_unsupported_coverage_configuration_is_explicit(pinned_coverage, tmp_path):
    with pytest.raises(ValueError, match='Unsupported'):
        execute(tmp_path,'invalid','90')
