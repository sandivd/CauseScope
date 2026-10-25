"""Common-budget evaluation; fixture truth is consulted only by the runner/auditor."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import platform
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from importlib.metadata import version
from pathlib import Path

from causescope.engine import ExperimentEngine
from causescope.report import write_reports
from causescope.runner import RunRecord, run_once
from causescope.selection import Space
from causescope.spec import load_spec

from .generate import FIXTURE_SEEDS, KINDS
from .program import evaluate, stable_failure

METHODS = ('legacy','adaptive','one_at_a_time','random','pairwise','brute_force')
SEARCH_SEEDS = (17,43)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixtureRunner:
    def __init__(self, oracle: dict, backend: str):
        self.oracle = oracle
        self.backend = backend
        self.counts = Counter()

    def __call__(self, spec, configuration):
        key = tuple(sorted(configuration.items()))
        self.counts[key] += 1
        index = self.counts[key]
        if self.backend == 'subprocess':
            return run_once(replace(spec,command=spec.command+(str(index),)),configuration)
        started = time.perf_counter()
        code = evaluate(self.oracle,configuration,index)
        outcome = 'failure' if code == 23 else 'invalid' if code == 64 else 'pass'
        return RunRecord(['in-process-fixture',str(index)],'not-a-subprocess',dict(configuration),code,
            'ok' if code==0 else '', 'fixture failure' if code==23 else 'invalid configuration' if code==64 else '',
            time.perf_counter()-started,False,None,False,False,outcome)


def truth_check(oracle: dict, spec, result: dict) -> dict:
    diagnosis = result['diagnosis']
    confirmed = bool(diagnosis and diagnosis['confirmed'])
    stable = bool(diagnosis and stable_failure(oracle,diagnosis['configuration']))
    valid_reductions = True
    if diagnosis:
        for name in diagnosis['changes']:
            reduced = {**diagnosis['configuration'],name:spec.baseline[name]}
            if not spec.allows(reduced) or evaluate(oracle,reduced,1) != 0:
                valid_reductions = False
    claimed_minimal = bool(diagnosis and diagnosis['one_minimal'])
    return {'event_detected':any(run['exit_code']==23 for e in result['evaluations'] for run in e['runs']),
        'seed_found':result['discovery']['found'],
        'confirmed':confirmed,'true_stable_confirmation':confirmed and stable,
        'false_confirmation':confirmed and not stable,
        'successful_isolation':claimed_minimal and stable and valid_reductions,
        'false_minimality_claim':claimed_minimal and not (stable and valid_reductions),
        'all_executed_public_constraints_valid':all(spec.allows(e['configuration']) for e in result['evaluations'])}


def reference(oracle: dict, spec) -> dict:
    space = Space(spec)
    if space.exact_size is None or space.exact_size>1024:
        return {'enumerated':False,'reason':'Valid public space exceeds 1024 configurations'}
    codes = Counter()
    minimum = None
    for configuration in space.enumerate():
        code = evaluate(oracle,configuration,1)
        codes[code] += 1
        if stable_failure(oracle,configuration):
            changes = sum(configuration[n] != value for n,value in spec.baseline.items())
            minimum = changes if minimum is None else min(minimum,changes)
    return {'enumerated':True,'valid_public_configurations':space.exact_size,
            'first_observation_exit_counts':dict(codes),'stable_minimum_cardinality':minimum}


def aggregate(records: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for record in records:
        groups[(record['backend'],record['kind'],record['dimension'],record['method'])].append(record)
    output = []
    for (backend,kind,dimension,method), rows in sorted(groups.items()):
        item = {'backend':backend,'kind':kind,'dimension':dimension,'method':method,'trials':len(rows),
                'outcomes':dict(Counter(r['status'] for r in rows)),
                'seed_detection_rate':statistics.mean(r['truth']['seed_found'] for r in rows),
                'event_detection_rate':statistics.mean(r['truth']['event_detected'] for r in rows),
                'successful_isolation_rate':statistics.mean(r['truth']['successful_isolation'] for r in rows),
                'false_confirmations':sum(r['truth']['false_confirmation'] for r in rows),
                'false_minimality_claims':sum(r['truth']['false_minimality_claim'] for r in rows)}
        for metric in ('unique_configurations','observations','wall_seconds'):
            values = [r[metric] for r in rows]
            item[metric] = {'mean':statistics.mean(values),'median':statistics.median(values),
                            'stdev':statistics.stdev(values) if len(values)>1 else 0,
                            'min':min(values),'max':max(values)}
        output.append(item)
    return output


def report(summary: dict) -> str:
    lines = ['# Discovery and isolation evaluation','',
        f"Backend: **{summary['backend']}**. Trials: **{len(summary['records'])}**.",
        f"Budget profile: **{summary.get('budget_profile','bounded')}**; limits: `{summary['budgets']}`.",
        'Simulation runtime measures Python selection/oracle work, not subprocess performance.',
        'Subprocess results are exported separately and must not be pooled with simulation timings.','',
        'All methods use the same constraints, repetitions, minimizer, confirmations and total budgets.',
        'Brute force is excluded above 1024 feasible public configurations. No tuning against fixture literals occurs.','',
        '| Kind | N | Method | Trials | Seed detection | True isolation | False confirmations | Mean configs | Mean observations | Wall mean ± sd (s) |',
        '| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in summary['aggregates']:
        lines.append(f"| {row['kind']} | {row['dimension']} | {row['method']} | {row['trials']} | "
            f"{row['seed_detection_rate']:.2f} | {row['successful_isolation_rate']:.2f} | {row['false_confirmations']} | "
            f"{row['unique_configurations']['mean']:.2f} | {row['observations']['mean']:.2f} | "
            f"{row['wall_seconds']['mean']:.5f} ± {row['wall_seconds']['stdev']:.5f} |")
    lines += ['', '## Interpretation and limits','',
        'A detected event, a unanimous discovery seed, and a confirmed 1-minimal diagnosis are separate metrics.',
        'A bounded or pairwise-only clean sample is inconclusive unless the entire valid space was tested.',
        'Declared-invalid configurations are excluded before execution; runtime-invalid results are unknown evidence.',
        'Rare flaky fixtures intentionally have a seven-observation cycle: six agreeing observations can falsely',
        'confirm stability under the fixed three-plus-three repetition policy. The truth audit counts those',
        'false confirmations rather than relabeling or hiding them. Alternating flaky fixtures should remain inconclusive.',
        'A method with zero false confirmations may simply fail to discover triggering configurations; this is not',
        'evidence of better flake handling. These timings came from a shared development host without cold-cache',
        'resets or exclusive hardware. Engine/runner time is measured; JSON export and compression are excluded.',
        'A valid 1-minimal diagnosis rules out immediate single-change deletions only. It does not prove a global',
        'minimum, root cause, or permanent reproducibility. Full reference tables are truth audits, not free comparator tests.',
        'AllPairs 2.5.1 supplies standard pairwise rows. Constraint filtering alone left feasible gaps in a regression',
        'case, so public-constraint witness completion fills uncovered feasible pairs with a bounded backtracking check.',
        'Adaptive selection uses these candidates, conclusive pair coverage, sampled triple novelty, and sparse exploration.',
        'It is a heuristic, not a general superiority or full three-way coverage guarantee. Its planning cost is included.',
        'Budgeted time is cooperative and target timeouts are clipped to remaining time; OS cleanup may add overhead.',
        'The fixed program family, low-order triggers, binary domains, five fixture seeds and two search seeds',
        'limit generalization. Fixture and search randomness differ; there are only ten trials per main group.',
        'Library/comparator versions, source/fixture hashes, all records and per-phase counts are in summary.json.',
        'Full result JSON preserves every observation. File isolation is not a security sandbox.','',
        'Reproduction: see ../PLAN.md and the repository README for generation and explicit execution commands.','']
    return '\n'.join(lines)


def run_study(fixtures: Path, output: Path, backend: str, *, allow_local_execution: bool = False,
              profile: str = 'bounded') -> dict:
    if not allow_local_execution:
        raise ValueError('--allow-local-execution is required for both benchmark backends')
    if output.exists():
        raise FileExistsError('Study output must be fresh')
    if profile not in ('bounded','tractable') or (profile=='tractable' and backend!='simulation'):
        raise ValueError('Tractable profile is simulation-only')
    max_configurations,max_runs=(1024,3080) if profile=='tractable' else (80,300)
    manifest = json.loads((fixtures/'manifest.json').read_text(encoding='utf-8'))
    for relative, expected in manifest['files_sha256'].items():
        if digest(fixtures/relative) != expected:
            raise ValueError('Fixture hash mismatch: '+relative)
    output.mkdir(parents=True)
    root = Path(__file__).resolve().parents[2]
    source_files = list((root/'src/causescope').glob('*.py')) + list(Path(__file__).parent.glob('*.py'))
    summary = {'schema_version':1,'backend':backend,'budget_profile':profile,'host':{'platform':platform.platform(),'python':sys.version},
        'versions':{'allpairspy':version('allpairspy'),'PyYAML':version('PyYAML')},
        'source_sha256':{p.relative_to(root).as_posix():digest(p) for p in sorted(source_files)},
        'fixture_manifest_sha256':digest(fixtures/'manifest.json'),'records':[],'references':{},'excluded':[],
        'budgets':{'unique_configurations':max_configurations,'observations':max_runs,'repetitions':3,'seconds':60 if backend=='subprocess' else 15}}
    cases = manifest['cases']
    if profile=='tractable':
        selected={f'{kind}-10-{seed}' for kind in ('pair','triple','clean') for seed in FIXTURE_SEEDS[:2]}
        cases=[case for case in cases if case in selected]
    if backend == 'subprocess':
        matrix={'single':10,'pair':20,'triple':32,'nonmonotonic':32,'constrained':20,
                'flaky':10,'rare_flaky':10,'clean':10,'exhaustive_clean':32}
        selected={f'{kind}-{dimension}-{seed}' for kind,dimension in matrix.items() for seed in FIXTURE_SEEDS[:2]}
        cases = [case for case in cases if case in selected]
        summary['subprocess_case_matrix']=matrix
        summary['subprocess_fixture_seeds']=list(FIXTURE_SEEDS[:2])
    for case_number, case in enumerate(cases,1):
        print(f'{backend}: fixture {case_number}/{len(cases)} {case}',flush=True)
        directory = fixtures/case
        spec = load_spec(directory/'experiment.yaml')
        oracle = json.loads((directory/'oracle.json').read_text(encoding='utf-8'))
        summary['references'][case] = reference(oracle,spec)
        seeds = SEARCH_SEEDS if backend=='simulation' and profile=='bounded' else (SEARCH_SEEDS[0],)
        for search_seed in seeds:
            # Rotate method order independently of hidden trigger locations.
            order = METHODS[(case_number+search_seed)%len(METHODS):]+METHODS[:(case_number+search_seed)%len(METHODS)]
            for method in order:
                if method=='brute_force' and (Space(spec).exact_size is None or Space(spec).exact_size>1024):
                    summary['excluded'].append({'case':case,'seed':search_seed,'method':method,'reason':'Space exceeds 1024'})
                    continue
                trial_spec = replace(spec,settings=replace(spec.settings,strategy=method,seed=search_seed,
                                     max_configurations=max_configurations,max_runs=max_runs,
                                     max_seconds=60 if backend=='subprocess' else 15))
                started = time.perf_counter()
                result = ExperimentEngine(trial_spec,FixtureRunner(oracle,backend)).run(allow_local_execution=True)
                wall = time.perf_counter()-started
                identifier = f'{case}__{search_seed}__{method}'
                payload = output/'runs'/f'{identifier}.json'
                payload.parent.mkdir(exist_ok=True)
                payload.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
                truth = truth_check(oracle,trial_spec,result)
                if not truth['all_executed_public_constraints_valid']:
                    raise AssertionError('A strategy executed a forbidden public combination')
                record = {'case':case,'kind':oracle['kind'],'dimension':oracle['dimension'],'fixture_seed':oracle['seed'],
                    'search_seed':search_seed,'method':method,'backend':backend,'status':result['status'],'truth':truth,
                    'unique_configurations':result['search']['tested_configurations'],
                    'observations':result['search']['subprocess_runs'],'wall_seconds':wall,
                    'by_phase':result['search']['observations_by_phase'],'stop_reason':result['search']['stop_reason'],
                    'invalid_evaluations':sum(e['status']=='invalid' for e in result['evaluations']),
                    'inconclusive_evaluations':sum(e['status']=='inconclusive' for e in result['evaluations']),
                    'evidence':payload.relative_to(output).as_posix(),'evidence_sha256':digest(payload)}
                summary['records'].append(record)
        (output/'summary.partial.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    summary['aggregates'] = aggregate(summary['records'])
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    content = report(summary)
    (output/'REPORT.md').write_text(content,encoding='utf-8')
    (output/'REPORT.html').write_text("<!doctype html><html lang='en'><meta charset='utf-8'><title>CauseScope strategy study</title>"
        "<style>body{font:14px/1.5 system-ui;margin:30px}pre{white-space:pre-wrap}</style>"
        "<a href='summary.json'>Structured results</a><pre>"+html.escape(content)+"</pre></html>",encoding='utf-8')
    (output/'summary.partial.json').unlink()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--backend',choices=('simulation','subprocess'),required=True)
    parser.add_argument('--allow-local-execution',action='store_true')
    parser.add_argument('--profile',choices=('bounded','tractable'),default='bounded')
    args = parser.parse_args()
    summary = run_study(args.fixtures.resolve(),args.output.resolve(),args.backend,
                        allow_local_execution=args.allow_local_execution,profile=args.profile)
    print(f"Completed {len(summary['records'])} {args.backend} trials; inspect all inconclusive and false-confirmation metrics")


if __name__ == '__main__':
    main()
