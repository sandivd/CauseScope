import json
import threading
import time
from pathlib import Path

import pytest
import yaml

from causescope.cli import main
from causescope.service import InvestigationService


@pytest.fixture
def service(tmp_path, raw_spec):
    raw_spec['settings'].update(max_runs=100, max_seconds=30)
    (tmp_path/'spec.yaml').write_text(yaml.safe_dump(raw_spec), encoding='utf-8')
    return InvestigationService(tmp_path, allow_local_execution=True)


def prepared(service, output='output'):
    return service.prepare('spec.yaml', output)


def wait(service, job):
    end = time.monotonic()+30
    while time.monotonic() < end:
        state = service.inspect(job['job_id'])
        if state['state'] != 'running':
            return state
        time.sleep(.01)
    pytest.fail('worker did not complete')


def test_planning_and_validation_never_execute(service, monkeypatch):
    import causescope.workflow as workflow
    monkeypatch.setattr(workflow, 'run_once', lambda *a: pytest.fail('planning must not execute'))
    assert service.capabilities()['execution_enabled']
    assert service.validate('spec.yaml')['valid']
    plan = prepared(service)
    assert not plan['executed'] and plan['review']['raw_configurations'] == 4
    assert 'approve' in plan['approve_command_powershell']
    assert not (service.root/'output').exists()
    assert prepared(service)['id'] == plan['id']


@pytest.mark.parametrize('target', ['../outside.yaml', '../outside/report', '/outside/spec.yaml'])
def test_workspace_escape_rejected(service, target):
    with pytest.raises(ValueError, match='inside'):
        service.path(target)
    assert not service.validate(target)['valid']


def test_symlink_escape_rejected(service, tmp_path):
    link = service.root/'link'
    try:
        link.symlink_to(service.root.parent, target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit directory symlinks')
    with pytest.raises(ValueError, match='inside'):
        service.path(link/'escape')


@pytest.mark.parametrize('field', ['max_runs', 'max_seconds'])
def test_unbounded_mcp_plan_rejected(service, field):
    path = service.root/'spec.yaml'
    raw = yaml.safe_load(path.read_text())
    raw['settings'].pop(field)
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match='finite'):
        prepared(service)
    assert not (service.root/'output').exists()


def test_execution_needs_server_opt_in_and_external_matching_approval(service):
    plan = prepared(service)
    with pytest.raises(ValueError, match='requires'):
        service.approve(plan['plan_path'])
    grant = service.approve(plan['plan_path'], allow_local_execution=True)
    disabled = InvestigationService(service.root)
    with pytest.raises(ValueError, match='disabled'):
        disabled.start(plan['id'], grant['approval_id'])
    other = prepared(service, 'other')
    with pytest.raises(ValueError, match='different plan'):
        service.start(other['id'], grant['approval_id'])
    with pytest.raises(FileNotFoundError):
        service.start(plan['id'], '0'*32)
    assert not (service.root/'output').exists()


@pytest.mark.parametrize('mutation', ['spec', 'plan', 'expiry', 'target'])
def test_changes_and_expired_approval_rejected_before_execution(service, mutation):
    if mutation == 'target':
        target = service.root/'target.py'
        target.write_text('print(1)')
        path = service.root/'spec.yaml'
        raw = yaml.safe_load(path.read_text())
        raw['command'] = ['{python}', '{spec_dir}/target.py']
        path.write_text(yaml.safe_dump(raw))
    plan = prepared(service)
    grant = service.approve(plan['plan_path'], allow_local_execution=True)
    if mutation == 'spec':
        path = service.root/'spec.yaml'
        path.write_text(path.read_text()+'\n# changed\n')
    elif mutation == 'target':
        target.write_text('print(2)')
    elif mutation == 'plan':
        path = Path(plan['plan_path'])
        data = json.loads(path.read_text()); data['output'] += 'changed'
        path.write_text(json.dumps(data))
    else:
        path = service._file('approvals', grant['approval_id'])
        data = json.loads(path.read_text()); data['expires_at_unix'] = 0
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        service.start(plan['id'], grant['approval_id'])
    assert not (service.root/'output').exists()


def test_completed_job_is_audited_paginated_and_cannot_reuse_approval(service):
    plan = prepared(service)
    grant = service.approve(plan['plan_path'], allow_local_execution=True)
    job = service.start(plan['id'], grant['approval_id'])
    finished = wait(service, job)
    assert finished['state'] == 'completed', finished
    assert finished['progress']['observations'] == 18
    assert finished['progress']['remaining_observations'] == 82
    assert service.findings(job['job_id'])['evidence']['status'] == 'repeated_failure'
    assert service.evidence(job['job_id'], offset=16, limit=5)['total_observations'] == 18
    assert len(service.evidence(job['job_id'], offset=16)['observations']) == 2
    evidence = json.loads(Path(finished['artifacts']['json']).read_text())
    assert evidence['workflow']['approved_plan_id'] == plan['id']
    assert evidence['workflow']['approval_id'] == grant['approval_id']
    assert InvestigationService(service.root).findings(job['job_id'])['diagnosis']['one_minimal']
    with pytest.raises((FileExistsError, FileNotFoundError)):
        service.start(plan['id'], grant['approval_id'])


def test_one_job_at_a_time_and_worker_errors_are_retained(service, monkeypatch):
    import causescope.service as module
    entered, release = threading.Event(), threading.Event()
    def fail(*a, **kw):
        entered.set()
        assert release.wait(10)
        raise OSError('controlled export error')
    monkeypatch.setattr(module, 'investigate', fail)
    plan = prepared(service)
    grant = service.approve(plan['plan_path'], allow_local_execution=True)
    job = service.start(plan['id'], grant['approval_id'])
    assert entered.wait(5)
    try:
        with pytest.raises(ValueError, match='One investigation'):
            service.start(plan['id'], grant['approval_id'])
        assert service.inspect(job['job_id'])['state'] == 'running'
    finally:
        release.set()
    finished = wait(service, job)
    assert finished['state'] == 'error' and 'controlled export error' in finished['error']
    assert InvestigationService(service.root).inspect(job['job_id']) == finished


@pytest.mark.parametrize('identifier', ['../escape', 'x'*64, ''])
def test_invalid_identifiers_rejected(service, identifier):
    with pytest.raises(ValueError, match='identifier'):
        service.inspect(identifier)


@pytest.mark.parametrize('offset,limit', [(-1,10),(0,0),(0,51),(True,10)])
def test_bad_evidence_pagination_rejected(service, offset, limit):
    with pytest.raises(ValueError):
        service.evidence('0'*32, offset=offset, limit=limit)


def test_cli_plan_and_approval_execute_nothing(service, capsys):
    args = ['plan', str(service.root/'spec.yaml'), '--workspace', str(service.root), '--output', 'cli-output']
    assert main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert main(['approve', plan['plan_path'], '--workspace', str(service.root), '--allow-local-execution']) == 0
    grant = json.loads(capsys.readouterr().out)
    assert grant['plan_id'] == plan['id']
    assert not (service.root/'cli-output').exists()


def test_independent_servers_cannot_race_the_same_artifact_directory(service, monkeypatch):
    import causescope.service as module
    entered, release = threading.Event(), threading.Event()
    original = module.investigate
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'investigate', paused)
    plan = prepared(service)
    first = service.approve(plan['plan_path'], allow_local_execution=True)
    second = service.approve(plan['plan_path'], allow_local_execution=True)
    job = service.start(plan['id'],first['approval_id'])
    assert entered.wait(5)
    try:
        other = InvestigationService(service.root,allow_local_execution=True)
        with pytest.raises(FileExistsError,match='fresh'):
            other.start(plan['id'],second['approval_id'])
        assert service._file('approvals',second['approval_id']).exists()
    finally:
        release.set()
    assert wait(service,job)['state']=='completed'


def test_prepare_does_not_inspect_command_argument_paths_outside_workspace(service, monkeypatch):
    external = service.root.parent/'private-evaluator-data.txt'
    path = service.root/'spec.yaml'
    raw = yaml.safe_load(path.read_text())
    raw['command'] = ['{python}', '-c', 'pass', str(external)]
    path.write_text(yaml.safe_dump(raw))
    original = Path.resolve
    def guarded(self, *args, **kwargs):
        if self == external:
            pytest.fail('Planning must not resolve/stat arbitrary out-of-workspace argv paths')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path,'resolve',guarded)
    plan = prepared(service)
    assert str(external) not in plan['files_sha256']
    assert not plan['executed']


def test_consumed_approval_stays_unusable_after_artifacts_are_archived(service):
    plan = prepared(service)
    grant = service.approve(plan['plan_path'],allow_local_execution=True)
    job = service.start(plan['id'],grant['approval_id'])
    assert wait(service,job)['state']=='completed'
    (service.root/'output').rename(service.root/'archived-output')
    with pytest.raises(FileNotFoundError):
        InvestigationService(service.root,allow_local_execution=True).start(plan['id'],grant['approval_id'])
    assert not (service.root/'output').exists()


def test_foreign_running_job_never_becomes_a_finding_or_restarts(service):
    identifier = 'a'*32
    path = service._file('jobs',identifier)
    path.parent.mkdir(parents=True)
    stale = {'state':'running','artifacts':None,'progress':{'observations':1}}
    path.write_text(json.dumps(stale),encoding='utf-8')
    other = InvestigationService(service.root)
    assert other.inspect(identifier)['state']=='interrupted_or_other_server'
    assert other.findings(identifier)['state']=='interrupted_or_other_server'
    assert other.evidence(identifier)['state']=='interrupted_or_other_server'
    assert json.loads(path.read_text())==stale
    assert not (service.root/'output').exists()
