import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from causescope.cli import main
from causescope.spec import load_spec
from causescope.wizard import create
from causescope.workflow import configure, describe, investigate, reproduce


def write(tmp_path, raw_spec):
    path = tmp_path/'experiment.yaml'
    path.write_text(yaml.safe_dump(raw_spec), encoding='utf-8')
    return path


def test_guided_yaml_is_valid_and_does_not_execute(tmp_path):
    answers = iter(['sample', '{python}', '-m', 'causescope.sample_app', '',
        'CAUSESCOPE_CACHE', 'off', '["on"]', 'CAUSESCOPE_FAST', 'off', '["on"]', '',
        '23', 'interaction bug', ''])
    path = tmp_path/'guided.yaml'
    create(path, ask=lambda _: next(answers))
    spec = load_spec(path)
    assert spec.baseline == {'CAUSESCOPE_CACHE': 'off', 'CAUSESCOPE_FAST': 'off'}
    assert spec.settings.max_runs == 500 and spec.confirmation.policy == 'repeat'
    with pytest.raises(FileExistsError):
        create(path, ask=lambda _: pytest.fail('must not prompt before overwrite rejection'))


def test_guided_cli_from_scripted_stdin(tmp_path):
    answers = '\n'.join(['sample', '{python}', '-m', 'causescope.sample_app', '',
        'CAUSESCOPE_CACHE', 'off', '["on"]', 'CAUSESCOPE_FAST', 'off', '["on"]', '', '23', 'interaction bug', '', ''])
    result = subprocess.run([sys.executable, '-m', 'causescope', 'init', str(tmp_path/'sample.yaml')],
        input=answers, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert load_spec(tmp_path/'sample.yaml').failure.exit_code == 23


def test_cli_validation_and_helpful_errors(raw_spec, tmp_path, capsys):
    path = write(tmp_path, raw_spec)
    assert main(['validate', str(path), '--json']) == 0
    view = json.loads(capsys.readouterr().out)
    assert view['valid'] and not view['executed']
    assert view['plan']['parameters']['CAUSESCOPE_CACHE']['alternatives'] == ['on']
    raw_spec['settings']['max_runz'] = 10
    write(tmp_path, raw_spec)
    assert main(['validate', str(path)]) == 2
    error = capsys.readouterr().err
    assert 'max_runz' in error and 'causescope init' in error


@pytest.mark.parametrize('mode', ['repeat', 'holdout'])
def test_cli_reliability_overrides_preserve_exact_policy_criteria(raw_spec, tmp_path, mode, capsys):
    path = write(tmp_path, raw_spec)
    spec = configure(load_spec(path), reliability=mode, max_runs=20, max_seconds=30)
    assert spec.confirmation.policy == mode
    assert spec.confirmation.samples_per_arm == 64
    assert spec.confirmation.alpha == .05
    assert spec.confirmation.min_effect == .2 and spec.confirmation.min_failure_probability == .9
    assert describe(spec)['budgets']['max_runs'] == 20
    assert main(['run', str(path), '--allow-local-execution', '--output', str(tmp_path/mode),
                 '--reliability', mode, '--max-runs', '20', '--max-seconds', '30']) == (0 if mode == 'repeat' else 3)
    console = capsys.readouterr()
    assert 'remaining:' in console.err and 'configurations 4' in console.err
    result = json.loads((tmp_path/mode/'evidence.json').read_text())
    assert result['search']['subprocess_runs'] == (18 if mode == 'repeat' else 20)
    assert result['evidence']['status'] == ('repeated_failure' if mode == 'repeat' else 'inconclusive')
    if mode == 'holdout':
        assert 'max_runs' in console.out


def test_fresh_reproduction_does_not_run_discovery_or_reuse_evidence(raw_spec, tmp_path, capsys):
    path = write(tmp_path, raw_spec)
    result = investigate(load_spec(path), tmp_path/'run', allow_local_execution=True)
    evidence = tmp_path/'run'/'evidence.json'
    assert result['search']['subprocess_runs'] == 18
    assert main(['show', str(evidence)]) == 0
    console = capsys.readouterr().out
    assert 'reproduce' in console and '18 observations' in console and 'root cause' in console
    marker = tmp_path/'reproduction.json'
    assert main(['reproduce', str(evidence), '--output', str(marker)]) == 2
    assert not marker.exists()
    fresh = reproduce(evidence, marker, allow_local_execution=True)
    assert fresh['pattern_reproduced'] and len(fresh['observations']) == 12
    original_dirs = {r['working_directory'] for e in result['evaluations'] for r in e['runs']}
    assert all(r['working_directory'] not in original_dirs for r in fresh['observations'])
    assert all(not Path(r['working_directory']).exists() for r in fresh['observations'])
    assert fresh['source_evidence_sha256']
    with pytest.raises(FileExistsError):
        reproduce(evidence, marker, allow_local_execution=True)


def test_reproduction_budget_and_invalid_evidence_are_inconclusive(raw_spec, tmp_path):
    path = write(tmp_path, raw_spec)
    investigate(load_spec(path), tmp_path/'run', allow_local_execution=True)
    evidence = tmp_path/'run'/'evidence.json'
    data = json.loads(evidence.read_text())
    data['specification']['settings']['max_runs'] = 2
    evidence.write_text(json.dumps(data))
    fresh = reproduce(evidence, tmp_path/'partial.json', allow_local_execution=True)
    assert fresh['status'] == 'inconclusive' and fresh['stop_reason'] == 'max_runs'
    assert len(fresh['observations']) == 2
    data['diagnosis']['configuration']['INJECTED'] = 'bad'
    evidence.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='Candidate'):
        reproduce(evidence, tmp_path/'invalid.json', allow_local_execution=True)
    assert not (tmp_path/'invalid.json').exists()


def test_overrides_never_mutate_yaml_and_quiet_preserves_counts(raw_spec, tmp_path, capsys):
    path = write(tmp_path, raw_spec)
    original = path.read_bytes()
    assert main(['run', str(path), '--allow-local-execution', '--quiet', '--max-configurations', '1',
                 '--output', str(tmp_path/'small')]) == 3
    output = capsys.readouterr()
    assert not output.err and 'budget' in output.out
    assert path.read_bytes() == original


def test_unrelated_nonzero_exits_are_explained_without_changing_predicate_semantics(raw_spec, tmp_path, capsys):
    raw_spec['command'] = ['{python}', '-c', "import sys; print('unsupported target', file=sys.stderr); sys.exit(70)"]
    path = write(tmp_path, raw_spec)
    assert main(['run', str(path), '--allow-local-execution', '--quiet', '--output', str(tmp_path/'unsupported')]) == 0
    console = capsys.readouterr().out
    assert 'Execution warning:' in console and '70' in console and 'invalid_exit_codes' in console
    evidence = tmp_path/'unsupported/evidence.json'
    result = json.loads(evidence.read_text())
    assert result['status'] == 'no_failure_found'  # exact legacy predicate contract
    assert main(['show', str(evidence), '--json']) == 0
    view = json.loads(capsys.readouterr().out)
    assert view['predicate_absent_nonzero_exit_codes'] == [70]
    assert view['execution_warnings']
