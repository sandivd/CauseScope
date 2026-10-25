"""Small terminal wizard producing ordinary, compatible version-1 YAML."""
import json
from pathlib import Path

import yaml

from .spec import SpecError, parse_spec


def create(path: Path, *, ask=input):
    if path.exists():
        raise FileExistsError(f'{path} already exists; choose a new specification path')
    print('Create a disposable LOCAL investigation. Nothing will execute.')
    print('Each command argument is separate. Use {python}, {spec_dir}, {work_dir} if needed.')
    name = ask('Investigation name: ').strip()
    command = [ask('Executable (for example {python}): ').strip()]
    while True:
        argument = ask('Next argument (blank ends argv): ')
        if not argument:
            break
        command.append(argument)
    print('Parameters are ENVIRONMENT variables. CLI flags need a wrapper that maps these values.')
    parameters = {}
    while True:
        variable = ask('Parameter name (blank ends parameters): ').strip()
        if not variable:
            break
        if variable in parameters:
            raise SpecError(f'Duplicate parameter: {variable}')
        baseline = ask('Baseline value (literal string): ')
        values = json.loads(ask('Alternative values as JSON (for example ["on"]): '))
        parameters[variable] = {'baseline': baseline, 'values': values}
    code = ask('Failure exit code (blank to use stderr text only): ').strip()
    text = ask('Required stderr text (blank for exit code only; conditions use AND): ')
    failure = {}
    if code:
        failure['exit_code'] = int(code)
    if text:
        failure['stderr_contains'] = text
    policy = ask('Reliability repeat or holdout [repeat]: ').strip() or 'repeat'
    print('repeat: unanimous batches, no probability guarantee. holdout: fresh fixed sample, more cost; IID assumptions.')
    raw = {'version': 1, 'name': name, 'scope': 'local', 'command': command,
        'parameters': parameters, 'failure': failure,
        'settings': {'timeout_seconds': 5, 'repetitions': 3, 'max_configurations': 100,
                     'max_runs': 500, 'max_seconds': 120}, 'confirmation': {'policy': policy}}
    parse_spec(raw, path.resolve())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        yaml.safe_dump(raw, stream, sort_keys=False)
    return raw
