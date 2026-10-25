"""Developer-facing operations; the investigation engine and criteria stay unchanged."""
from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from .engine import ExecutionNotAllowed, ExperimentEngine
from .report import prepare_output, write_reports
from .runner import run_once
from .spec import ConfirmationSettings, ExperimentSpec, SpecError, load_spec, parse_spec


POLICIES = {
    'repeat': 'Unanimous repeated observations plus fresh candidate/control batches; no probability coverage claim.',
    'holdout': 'Fresh fixed-size candidate/baseline/deletion family; simultaneous binomial bounds under stationary IID assumptions. More observations, potentially less power.',
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def document(spec: ExperimentSpec) -> dict:
    """Turn normalized evidence back into strict v1 input without null predicates."""
    raw = spec.evidence()
    raw.pop('source')
    raw['failure'] = {k: v for k, v in raw['failure'].items() if v is not None}
    for value in raw['parameters'].values():
        value['values'] = list(value['values'])
    return raw


def configure(spec, *, reliability=None, max_runs=None, max_seconds=None, max_configurations=None):
    raw = document(spec)
    if reliability is not None:
        raw['confirmation']['policy'] = reliability
    for name, value in [('max_runs', max_runs), ('max_seconds', max_seconds),
                        ('max_configurations', max_configurations)]:
        if value is not None:
            raw['settings'][name] = value
    return parse_spec(raw, spec.source)


def describe(spec) -> dict:
    total = 1
    for parameter in spec.parameters.values():
        total *= 1 + len(parameter.values)
    return {
        'name': spec.name, 'command': list(spec.command),
        'parameters': {k: {'baseline': p.baseline, 'alternatives': list(p.values)} for k, p in spec.parameters.items()},
        'failure': document(spec)['failure'], 'predicate_semantics': 'AND; absent predicate does not imply program health',
        'constraints': document(spec)['constraints'], 'invalid_exit_codes': list(spec.invalid_exit_codes),
        'raw_configurations': total, 'budgets': asdict(spec.settings),
        'confirmation': asdict(spec.confirmation), 'policy_explanation': POLICIES[spec.confirmation.policy],
        'execution': 'Disposable local targets only. Inherited environment and network access are unrestricted. Temporary directories are not security sandboxes.',
        'limitations': 'Finite observations do not prove permanent reproduction, global minimality or internal root cause.',
    }


class Progress:
    def __init__(self, spec, callback: Callable[[dict], None] | None = None):
        self.spec, self.callback = spec, callback
        self.started = time.perf_counter()
        self.completed = 0
        self.configurations = set()

    def snapshot(self):
        elapsed = time.perf_counter() - self.started
        settings = self.spec.settings
        return {'observations': self.completed, 'unique_configurations': len(self.configurations),
            'elapsed_seconds': elapsed,
            'remaining_observations': None if settings.max_runs is None else max(0, settings.max_runs-self.completed),
            'remaining_configurations': max(0, settings.max_configurations-len(self.configurations)),
            'remaining_seconds': None if settings.max_seconds is None else max(0, settings.max_seconds-elapsed)}

    def run(self, spec, configuration):
        record = run_once(spec, configuration)
        self.completed += 1
        self.configurations.add(tuple(sorted(configuration.items())))
        if self.callback:
            self.callback(self.snapshot())
        return record


def investigate(spec, output, *, allow_local_execution=False, overwrite=False, callback=None):
    if not allow_local_execution:
        raise ExecutionNotAllowed('Execution requires --allow-local-execution; use only disposable local targets')
    output = Path(output)
    prepare_output(output, overwrite=overwrite)
    progress = Progress(spec, callback)
    result = ExperimentEngine(spec, progress.run).run(allow_local_execution=True)
    result['workflow'] = {'specification_sha256': sha256(spec.source),
        'interpreter': sys.executable, 'python_version': sys.version,
        'reproduction': reproduction_steps(output / 'evidence.json') if result['diagnosis'] else
            {'available': False, 'reason': 'No selected candidate to reproduce; inspect the retained observations.'},
        'inconclusive_help': advice(result)}
    write_reports(result, output, overwrite=overwrite)
    return result


def powershell(arguments) -> str:
    return '& ' + ' '.join("'" + str(arg).replace("'", "''") + "'" for arg in arguments)


def reproduction_steps(evidence_path) -> dict:
    path = Path(evidence_path).resolve()
    return {'command_powershell': powershell([sys.executable, '-m', 'causescope', 'reproduce', str(path),
        '--allow-local-execution', '--output', str(path.parent / 'reproduction.json')]),
        'meaning': 'Fresh baseline, candidate and valid immediate deletions using the recorded specification. No discovery cache or statistical reconfirmation; review raw observations.',
        'assumptions': 'Same target files, interpreter, dependencies and inherited environment. Evidence is data, not trusted code: review its argv before opting in.'}


def advice(result) -> list[str]:
    if result['status'] not in ('inconclusive', 'baseline_failure'):
        return []
    tips = [result['reason']]
    if result['status'] == 'baseline_failure':
        tips.append('Choose a baseline where this failure predicate is absent before attributing a change.')
    reason = (result.get('validation') or {}).get('reason') or result['search'].get('stop_reason')
    if reason:
        tips.append('Recorded stop/validation reason: ' + str(reason))
    if result['search']['budget_exhausted']:
        tips.append('Review untested configurations and budgets. A larger budget is a new investigation, not an extension of this fixed-sample validation.')
    if result.get('validation'):
        tips.append('Inspect validation.arms for incomplete, invalid or mixed outcomes and statistics for unmet bounds. Do not reuse discovery as validation or cherry-pick retries.')
    else:
        tips.append('Inspect raw exits, launch/cleanup errors and mixed repetitions. A pass only means the specified predicate was absent.')
    tips.append('Sampled passes do not establish absence throughout an untested space.')
    return tips


def findings(result, evidence_path) -> dict:
    runs = [r for e in result['evaluations'] for r in e['runs']]
    runs += [r for a in (result.get('validation') or {}).get('arms', []) for r in a['runs']]
    unmatched = sorted({r['exit_code'] for r in runs if r['outcome'] == 'pass'
                        and r['exit_code'] not in (None, 0)})
    warnings = ([] if not unmatched else [
        f'Predicate-absent observations had nonzero exit codes {unmatched}. Inspect stderr: these may be unsupported targets or unrelated errors, not healthy executions. Declare known invalid_exit_codes in a new specification.'])
    return {'status': result['status'], 'reason': result['reason'], 'evidence': result['evidence'],
        'diagnosis': result['diagnosis'], 'counts': result['search'],
        'duration_seconds': result['duration_seconds'], 'inconclusive_help': advice(result),
        'execution_warnings': warnings, 'predicate_absent_nonzero_exit_codes': unmatched,
        'artifacts': {'json': str(Path(evidence_path).resolve()),
                      'html': str(Path(evidence_path).resolve().with_name('report.html'))},
        'reproduction': reproduction_steps(evidence_path) if result['diagnosis'] else
            {'available': False, 'reason': 'No selected candidate to reproduce; inspect the retained observations.'},
        'claim_boundary': 'A failure-inducing configuration is not a proven internal root cause. Observed 1-minimality is deletion-local, not global minimum cardinality.'}


def reproduce(evidence_path, output, *, allow_local_execution=False):
    if not allow_local_execution:
        raise ExecutionNotAllowed('Reproduction executes recorded argv; requires --allow-local-execution')
    evidence_path, output = Path(evidence_path).resolve(), Path(output).resolve()
    result = json.loads(evidence_path.read_text(encoding='utf-8'))
    raw = dict(result['specification'])
    source = Path(raw.pop('source'))
    raw['failure'] = {k: v for k, v in raw['failure'].items() if v is not None}
    spec = parse_spec(raw, source)
    diagnosis = result.get('diagnosis')
    if not diagnosis:
        raise SpecError('This evidence has no candidate to reproduce')
    candidate = diagnosis['configuration']
    if (set(candidate) != set(spec.parameters) or not spec.allows(candidate)
            or any(value not in (spec.parameters[name].baseline,)+spec.parameters[name].values
                   for name, value in candidate.items())
            or diagnosis['changes'] != {n: v for n, v in candidate.items() if v != spec.baseline[n]}):
        raise SpecError('Candidate configuration or changes do not match the recorded domains/constraints')
    # Fail before execution, including an existing artifact or missing parent.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        started = time.perf_counter()
        arms = [('baseline', spec.baseline), ('candidate', diagnosis['configuration'])]
        for name in diagnosis['changes']:
            deletion = {**diagnosis['configuration'], name: spec.baseline[name]}
            if spec.allows(deletion) and deletion not in [c for _, c in arms]:
                arms.append(('delete:' + name, deletion))
        observations = []
        # Use the existing engine's observation/time budget logic without discovery.
        engine = ExperimentEngine(spec)
        engine.started = engine.clock()
        from .selection import PlanningStopped
        stop = None
        try:
            for role, configuration in arms:
                for _ in range(spec.settings.repetitions):
                    observations.append({'role': role, **asdict(engine._observe(configuration))})
        except PlanningStopped as exc:
            stop = str(exc)
        matched = (stop is None and len(observations) == len(arms)*spec.settings.repetitions
            and all(r['outcome'] == ('failure' if r['role'] == 'candidate' else 'pass') for r in observations))
        reproduction = {'schema_version': 1, 'source_evidence_sha256': sha256(evidence_path),
            'specification': spec.evidence(), 'observations': observations, 'stop_reason': stop,
            'duration_seconds': time.perf_counter()-started, 'pattern_reproduced': matched,
            'status': 'repeated_failure' if matched else 'inconclusive',
            'claim': 'Fresh descriptive observations only; no new probability coverage, root cause or global minimality claim.'}
        json.dump(reproduction, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
    return reproduction
