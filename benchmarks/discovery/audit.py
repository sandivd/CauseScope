"""Check retained benchmark evidence without executing any target."""

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from causescope.spec import load_spec

from .program import evaluate
from .study import aggregate, truth_check


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def audit(fixtures: Path, results: Path) -> dict:
    fixtures, results = fixtures.resolve(), results.resolve()
    summary = json.loads((results/'summary.json').read_text(encoding='utf-8'))
    errors = []
    directories = set()
    observations = 0
    manifest_bytes = (fixtures/'manifest.json').read_bytes()
    if sha256(manifest_bytes) != summary['fixture_manifest_sha256']:
        errors.append('fixture manifest hash mismatch')
    manifest = json.loads(manifest_bytes)
    for relative, expected in manifest['files_sha256'].items():
        if sha256((fixtures/relative).read_bytes()) != expected:
            errors.append('fixture hash mismatch: '+relative)
    for record in summary['records']:
        label = record['evidence']
        path = (results/label).resolve()
        if not path.is_relative_to(results):
            errors.append('unsafe evidence path: '+label)
            continue
        data = path.read_bytes()
        if sha256(data) != record['evidence_sha256']:
            errors.append('evidence hash mismatch: '+label)
            continue
        raw = gzip.decompress(data) if path.suffix == '.gz' else data
        if path.suffix == '.gz' and sha256(raw) != record['uncompressed_sha256']:
            errors.append('uncompressed hash mismatch: '+label)
            continue
        result = json.loads(raw)
        spec = load_spec(fixtures/record['case']/'experiment.yaml')
        oracle = json.loads((fixtures/record['case']/'oracle.json').read_text(encoding='utf-8'))
        evaluations = result['evaluations']
        runs = [run for item in evaluations for run in item['runs']]
        phases = Counter(phase for item in evaluations for phase in item['run_phases'])
        observations += len(runs)
        checks = {
            'status': record['status'] == result['status'],
            'unique accounting': record['unique_configurations'] == len(evaluations) == result['search']['tested_configurations'],
            'observation accounting': record['observations'] == len(runs) == result['search']['subprocess_runs'],
            'phase accounting': record['by_phase'] == result['search']['observations_by_phase']
                and all(record['by_phase'].get(name) == count for name, count in phases.items())
                and sum(record['by_phase'].values()) == len(runs)
                and all(len(item['run_phases']) == len(item['runs']) for item in evaluations),
            'truth audit': record['truth'] == truth_check(oracle, spec, result),
            'public constraints': all(spec.allows(item['configuration']) for item in evaluations),
            'unique budget': len(evaluations) <= summary['budgets']['unique_configurations'],
            'observation budget': len(runs) <= summary['budgets']['observations'],
            'invalid accounting': record['invalid_evaluations'] == sum(e['status']=='invalid' for e in evaluations),
            'inconclusive accounting': record['inconclusive_evaluations'] == sum(e['status']=='inconclusive' for e in evaluations),
        }
        for name, passed in checks.items():
            if not passed:
                errors.append(name+': '+label)
        for item in evaluations:
            for index, run in enumerate(item['runs'], 1):
                if run['configuration'] != item['configuration']:
                    errors.append('effective parameters: '+label)
                if not any(run.get(flag) for flag in ('launch_error','cleanup_error','termination_error','timed_out')):
                    if run['exit_code'] != evaluate(oracle, item['configuration'], index):
                        errors.append('native exit disagrees with independent oracle: '+label)
                if record['backend'] == 'subprocess':
                    directory = run['working_directory']
                    if directory in directories or Path(directory).exists():
                        errors.append('temporary directory reused or remains: '+directory)
                    directories.add(directory)
    if aggregate(summary['records']) != summary['aggregates']:
        errors.append('aggregate metrics mismatch')
    return {'passed':not errors,'errors':errors,'trials':len(summary['records']),
            'observations':observations,'physical_temporary_directories':len(directories),
            'false_confirmations':sum(r['truth']['false_confirmation'] for r in summary['records']),
            'note':'False confirmations are measured scientific limitations, not artifact-integrity errors.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--results', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.fixtures, args.results)
    (args.results/'AUDIT.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
