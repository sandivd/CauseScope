"""Generate fixtures before invoking any discovery code. No search imports."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
from pathlib import Path

import yaml

KINDS = ('single', 'pair', 'triple', 'nonmonotonic', 'constrained', 'flaky', 'rare_flaky', 'clean', 'exhaustive_clean')
FIXTURE_SEEDS = (104729, 130363, 155921, 196613, 262147)
DIMENSIONS = (10, 20, 32)


def fixture(seed: int, dimension: int, kind: str) -> tuple[dict, dict]:
    if kind not in KINDS or dimension < 10:
        raise ValueError('Unknown kind or fewer than 10 parameters')
    rng = random.Random(f'{seed}:{dimension}:{kind}')
    names = [f'OPT_{i:02}' for i in range(dimension)]
    rng.shuffle(names)
    parameters = {name: {'baseline':'0','values':['1']} for name in names}
    width = 1 if kind == 'single' else 3 if kind in ('triple','constrained') else 2
    chosen = rng.sample(names, width + 2)
    literals = {name:'1' for name in chosen[:width]}
    forbidden = []
    invalid = []
    mask = None
    if kind == 'nonmonotonic':
        mask = chosen[width]
    if kind == 'constrained':
        others = [name for name in names if name not in literals]
        for _ in range(2):
            pair = rng.sample(others, 2)
            forbidden.append({'forbid':{name:'1' for name in pair}})
        invalid.append({name:'1' for name in rng.sample(others, 2)})
    if kind == 'exhaustive_clean':
        free = rng.choice(names)
        forbidden = [{'forbid':{name:'1'}} for name in names if name != free]
    oracle = {'schema_version':1,'seed':seed,'dimension':dimension,'kind':kind,
              'literals':literals,'mask':mask,'runtime_invalid':invalid,
              'period':2 if kind=='flaky' else 7 if kind=='rare_flaky' else None,
              'parameters':list(parameters),'baseline':{name:'0' for name in parameters}}
    public = {'version':1,'name':f'{kind}-{dimension}-{seed}','scope':'local',
        'command':['{python}',str(Path(__file__).with_name('program.py').resolve()),
                   '{spec_dir}/oracle.json'],
        'parameters':parameters,'constraints':forbidden,'invalid_exit_codes':[64],
        'failure':{'exit_code':23,'stderr_contains':'fixture failure'},
        'settings':{'strategy':'adaptive','repetitions':3,'timeout_seconds':2,
                    'max_configurations':80,'max_runs':300,'max_seconds':15,'seed':17}}
    return public, oracle


def generate(output: Path) -> dict:
    if output.exists():
        raise FileExistsError('Fixture output directory must be fresh')
    output.mkdir(parents=True)
    files = {}
    cases = []
    for dimension in DIMENSIONS:
        for kind in KINDS:
            for seed in FIXTURE_SEEDS:
                public, oracle = fixture(seed, dimension, kind)
                directory = output/f'{kind}-{dimension}-{seed}'
                directory.mkdir()
                relative_program = Path(os.path.relpath(Path(__file__).with_name('program.py'),directory)).as_posix()
                public['command'][1] = '{spec_dir}/'+relative_program
                (directory/'experiment.yaml').write_text(yaml.safe_dump(public,sort_keys=False),encoding='utf-8')
                (directory/'oracle.json').write_text(json.dumps(oracle,indent=2)+'\n',encoding='utf-8')
                cases.append(directory.name)
                for path in directory.iterdir():
                    files[path.relative_to(output).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'generator_version':1,'cases':cases,'files_sha256':files,
                'fixture_seeds':list(FIXTURE_SEEDS),'dimensions':list(DIMENSIONS),'kinds':list(KINDS)}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    result = generate(args.output)
    print(f"Generated {len(result['cases'])} independent fixtures")


if __name__ == '__main__':
    main()
