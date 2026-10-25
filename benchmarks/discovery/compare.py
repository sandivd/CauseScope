"""Produce matched-cohort comparisons from audited retained summaries."""

import argparse
import html
import json
import statistics
from collections import Counter
from pathlib import Path

STABLE = {'single','pair','triple','nonmonotonic','constrained'}


def describe(rows):
    groups = {}
    for method in sorted({row['method'] for row in rows}):
        selected = [row for row in rows if row['method']==method]
        item = {'trials':len(selected),'outcomes':dict(Counter(r['status'] for r in selected)),
                'event_detections':sum(r['truth']['event_detected'] for r in selected),
                'seed_detections':sum(r['truth']['seed_found'] for r in selected),
                'true_isolations':sum(r['truth']['successful_isolation'] for r in selected),
                'false_confirmations':sum(r['truth']['false_confirmation'] for r in selected),
                'false_minimality_claims':sum(r['truth']['false_minimality_claim'] for r in selected)}
        item['seed_detection_rate']=item['seed_detections']/len(selected)
        item['event_detection_rate']=item['event_detections']/len(selected)
        item['successful_isolation_rate']=item['true_isolations']/len(selected)
        for metric in ('unique_configurations','observations','wall_seconds'):
            values = [r[metric] for r in selected]
            item[metric] = {'mean':statistics.mean(values),
                'stdev':statistics.stdev(values) if len(values)>1 else 0,
                'min':min(values),'max':max(values)}
        groups[method] = item
    return groups


def compare(evidence: Path):
    loaded = {}
    for name in ('simulation','subprocess','tractable'):
        directory = evidence/name
        if not json.loads((directory/'AUDIT.json').read_text(encoding='utf-8'))['passed']:
            raise ValueError('A passing evidence audit is required: '+name)
        loaded[name] = json.loads((directory/'summary.json').read_text(encoding='utf-8'))
    sim, proc, tract = (loaded[name]['records'] for name in ('simulation','subprocess','tractable'))
    cohorts = {
        'bounded_simulation_stable_10_20_32':describe([r for r in sim if r['kind'] in STABLE and r['method']!='brute_force']),
        'bounded_simulation_stable_10_all_methods':describe([r for r in sim if r['kind'] in STABLE and r['dimension']==10]),
        'bounded_subprocess_stable_matched_subset':describe([r for r in proc if r['kind'] in STABLE and r['method']!='brute_force']),
        'bounded_simulation_rare_flaky_matched':describe([r for r in sim if r['kind']=='rare_flaky' and r['method']!='brute_force']),
        'bounded_subprocess_rare_flaky_matched':describe([r for r in proc if r['kind']=='rare_flaky' and r['method']!='brute_force']),
        'tractable_simulation_stable_10':describe([r for r in tract if r['kind'] in STABLE]),
        'tractable_simulation_clean_10':describe([r for r in tract if r['kind']=='clean']),
    }
    result = {'schema_version':1,'cohorts':cohorts,'studies':{name:{
        'trials':len(s['records']),'observations':sum(r['observations'] for r in s['records']),
        'sum_measured_engine_seconds':sum(r['wall_seconds'] for r in s['records']),
        'false_confirmations':sum(r['truth']['false_confirmation'] for r in s['records']),
        'stop_reasons':dict(Counter(str(r['stop_reason']) for r in s['records'])),
        'outcomes':dict(Counter(r['status'] for r in s['records']))} for name,s in loaded.items()}}
    (evidence/'COMPARISON.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    lines = ['# Measured discovery comparison','',
        'These are measurements of the retained fixture family, not proof of general competitiveness.',
        'Adaptive selection is an experimental heuristic; the default legacy strategy remains available.',
        'A seed is a unanimously failing discovery configuration. True isolation additionally requires',
        'a confirmed 1-minimal diagnosis that passes the separate stable-fixture truth audit.',
        'Event detections include any observed target failure, even an inconsistent batch.',
        'A root cause is not inferred. A 1-minimal set has no reproducibly failing immediate deletion;',
        'it need not be the smallest failing set over all subsets or the full configuration space.','',
        '## Matched cohorts','',
        'All methods within each table have identical fixtures, seeds, budgets and confirmation policies.',
        'Runtime is engine plus runner time, including planning/minimization/confirmation, excluding export.',
        'Simulation and subprocess time are different measurements and are never pooled.',
        'Mean +/- sample standard deviation describes variation over the listed seeded trials, not a confidence interval.',
        'Pooled tables mix fixture kinds; per-kind/dimension tables and every seed are in each study summary.','']
    for name, group in cohorts.items():
        lines += ['### '+name,'',
            '| Method | Trials | Events | Seeds (rate) | True isolations (rate) | False confirmations | Mean configs | Mean observations | Wall mean +/- sd (s) |',
            '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for method, row in group.items():
            lines.append(f"| {method} | {row['trials']} | {row['event_detections']} | {row['seed_detections']} ({row['seed_detection_rate']:.1%}) | "
                f"{row['true_isolations']} ({row['successful_isolation_rate']:.1%}) | {row['false_confirmations']} | {row['unique_configurations']['mean']:.2f} | "
                f"{row['observations']['mean']:.2f} | {row['wall_seconds']['mean']:.5f} +/- {row['wall_seconds']['stdev']:.5f} |")
        lines.append('')
    lines += ['## Findings and failures','',
        'The main bounded simulation favours adaptive detection on this low-order, binary fixture family,',
        'but random sampling is close and has much lower simulation planning cost. This is insufficient',
        'evidence to claim general superiority. The smaller subprocess cohort is a separate cross-check.',
        'One-at-a-time finds isolated single-setting failures but cannot exercise interaction-only triggers.',
        'Pairwise coverage is strong for unmasked two-setting triggers; it does not guarantee triple or',
        'non-monotonic failure detection. The non-monotonic family includes a mask and a total-change cap,',
        'so its effective failure context is higher-order than its two positive trigger literals.',
        'Legacy dense-first search can find monotonic triggers promptly, but often misses sparse masked contexts.',
        'Random sampling explores large spaces cheaply but can miss rare sparse contexts.',
        'Brute force is included only at <=1024 feasible public configurations. Its main-study 80-configuration',
        'limit is explicitly truncating; the separate 1024-configuration profile gives all six methods enough',
        'unique slots to enumerate a ten-parameter space, subject to their common observation/time limits.',
        'An early seed still stops discovery for every method. Exhaustive truth-reference tables are not',
        'counted as free experiments for any strategy. Large-space brute-force exclusions are in summary.json.','',
        'Alternating observed flakes remain inconclusive. Rare flakes fail six times and pass on the seventh:',
        'three discovery plus three fresh confirmation observations can falsely confirm them. False confirmations',
        'and false minimality claims are retained, not relabeled successful isolation. Zero false confirmations',
        'for a comparator that missed the trigger is not better flake handling. Finite repetition is a material',
        'limitation for every selector sharing this confirmation policy. No policy was tuned to the flake period.','',
        'Runtime-invalid combinations remain unknown and can prevent a minimality claim. Public forbidden',
        'combinations are excluded before execution. Constrained clean fixtures with only two feasible states',
        'allow successful exhaustive clean outcomes; larger clean samples are inconclusive unless exhausted.',
        'Main-study run/time caps were available but did not bind; unique-configuration limits and invalid/',
        'inconsistent evidence explain its inconclusive outcomes. Unit tests exercise partial-run and deadline limits.','',
        '## Evidence, provenance and limits','',
        'Raw results are losslessly compressed only after measurement, with compressed and original hashes.',
        'Each summary records Python/platform, AllPairs/PyYAML versions, source and fixture hashes, budgets,',
        'outcomes, phase-level observation counts, and seed-specific metrics. AUDIT.json checks hashes,',
        'native exit/oracle agreement, public constraints, accounting, aggregation, and temporary cleanup.',
        'The structured comparison also lists inconclusive outcomes and stop reasons. Audit success does not',
        'validate scientific claims contradicted by the independent truth oracle. See the source README for',
        'source commits and exact execution/test/build commands. No CI result is claimed from local execution.','',
        'Measurements used a shared Windows development host without cold-cache resets or exclusive hardware.',
        'Background development work occurred; timings are descriptive, not controlled hardware performance claims.',
        'Binary domains, low-order seeded templates, five fixture seeds/two search seeds in the main simulation,',
        'two trials per subprocess group, and a six-fixture supplemental profile limit generalization.',
        'Generation is separate from selection, but this is one authored fixture family, not independent research-team validation.',
        'The optional Black reproduction checks',
        'one real defect on unmodified affected/fixed releases. It does not test competitive discovery in a large',
        'real-world configuration space. Git remains an intentional guard benchmark. These gaps remain open.',
        'Temporary working directories provide file isolation, not a security sandbox. Local execution requires opt-in.','',
        'Full reports: [simulation](simulation/REPORT.md), [subprocess](subprocess/REPORT.md),',
        '[tractable profile](tractable/REPORT.md). [Protocol and primary sources](../PLAN.md).',
        '[Exact reproduction commands](../README.md).','']
    content = '\n'.join(lines)
    (evidence/'COMPARISON.md').write_text(content,encoding='utf-8')
    (evidence/'COMPARISON.html').write_text("<!doctype html><html lang='en'><meta charset='utf-8'><title>CauseScope measured comparison</title>"
        "<style>body{font:14px/1.5 system-ui;margin:30px}pre{white-space:pre-wrap}</style><pre>"
        +html.escape(content)+'</pre></html>',encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,required=True)
    args = parser.parse_args()
    result = compare(args.evidence)
    print(json.dumps(result['studies'],indent=2))
