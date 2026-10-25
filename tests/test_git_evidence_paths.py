"""Archive path semantics and tampering checks work on either audit host."""
import hashlib
import json
from pathlib import PurePath, PurePosixPath, PureWindowsPath

import pytest

from benchmarks.git_eol import adapter
from benchmarks.git_eol import audit as auditor


def campaign(root):
    pin = json.loads(adapter.LOCK_PATH.read_text(encoding='utf-8'))
    configuration = {f'GIT_CONFIG_VALUE_{i}': 'false' for i in range(4)}
    runs = []
    for i in range(2):
        work = root / f'observation-{i}'
        executable = 'recorded-git'
        metadata = {'version': pin['version_stdout'], 'binary_sha256': pin['binary_sha256'],
            'archive_sha256': pin['archive_sha256'], 'configuration': adapter.native_configuration(configuration),
            'fixture_sha256': hashlib.sha256(adapter.FIXTURE).hexdigest(),
            'repository': str(work / 'repository'), 'executable': executable, 'git_invocations': 3,
            'commands': [[executable, '--version'],
                         [executable, 'init', '--quiet', f'--template={work / "empty-templates"}'],
                         [executable, 'add', '--', 'fixture.txt']]}
        runs.append({'exit_code': 0, 'outcome': 'pass', 'configuration': dict(configuration),
            'working_directory': str(work), 'stdout': adapter.META_PREFIX + json.dumps(metadata),
            'stderr': '', 'duration_seconds': .01,
            **{k: False for k in ['timed_out','launch_error','termination_error','cleanup_error',
                                 'stdout_truncated','stderr_truncated']}})
    return {'specification': {'settings': {'repetitions': 2}},
            'evaluations': [{'id': 'c0001', 'status': 'pass', 'configuration': configuration, 'runs': runs}],
            'search': {'subprocess_runs': 2}}


ROOTS = [PureWindowsPath('C:' + '/') / 'causescope-evidence-path-test',
         PurePosixPath('/causescope-evidence-path-test')]


@pytest.mark.parametrize('root', ROOTS, ids=['windows-record', 'posix-record'])
def test_archive_audit_accepts_origin_path_flavour_on_any_host(root):
    result = auditor.audit_result(campaign(root))
    assert result['passed'], result['errors']
    assert result['observations'] == 2 and result['git_invocations'] == 6
    native = isinstance(root, type(PurePath()))
    assert result['native_cleanup_existence_checks'] == (2 if native else 0)
    assert result['foreign_cleanup_paths_not_locally_verifiable'] == (0 if native else 2)


@pytest.mark.parametrize('root', ROOTS, ids=['windows-record', 'posix-record'])
@pytest.mark.parametrize('change,expected', [
    ('repository_escape', 'repository outside observation directory'),
    ('template_change', 'unexpected Git commands'),
    ('command_change', 'unexpected Git commands'),
])
def test_cross_platform_archive_checks_still_reject_tampering(root, change, expected):
    result = campaign(root)
    run = result['evaluations'][0]['runs'][0]
    metadata = json.loads(run['stdout'][len(adapter.META_PREFIX):])
    if change == 'repository_escape':
        metadata['repository'] = str(root / 'outside' / 'repository')
    elif change == 'template_change':
        metadata['commands'][1][-1] = f'--template={root / "outside-template"}'
    else:
        metadata['commands'][2] = ['recorded-git', 'status']
    run['stdout'] = adapter.META_PREFIX + json.dumps(metadata)
    checked = auditor.audit_result(result)
    assert not checked['passed']
    assert any(expected in error for error in checked['errors'])


def test_foreign_archive_path_is_never_queried_as_a_local_path(monkeypatch):
    foreign = next(root for root in ROOTS if not isinstance(root, type(PurePath())))
    monkeypatch.setattr(auditor, 'Path', lambda _: pytest.fail('Foreign paths must not query local files'))
    result = auditor.audit_result(campaign(foreign))
    assert result['passed']
    assert result['foreign_cleanup_paths_not_locally_verifiable'] == 2


def test_native_existing_observation_directory_still_fails(tmp_path):
    root = PurePath(tmp_path)
    (tmp_path / 'observation-0').mkdir()
    result = auditor.audit_result(campaign(root))
    assert not result['passed']
    assert any('working directory was not removed' in error for error in result['errors'])


@pytest.mark.parametrize('root', [PurePosixPath('relative'), PureWindowsPath('C:' + 'relative'),
                                 PureWindowsPath('/root-without-drive')],
                         ids=['relative', 'drive-relative', 'root-without-drive'])
def test_relative_paths_cannot_evade_native_cleanup_checks(root):
    result = auditor.audit_result(campaign(root))
    assert not result['passed']
    assert any('working directory is not absolute' in error for error in result['errors'])
