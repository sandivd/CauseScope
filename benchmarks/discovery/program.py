"""The generated black-box CLI's independent deterministic semantics."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def evaluate(oracle: dict, configuration: dict[str,str], observation_index: int) -> int:
    if any(all(configuration[name] == value for name,value in item.items())
           for item in oracle['runtime_invalid']):
        return 64
    if oracle['kind'] in ('clean','exhaustive_clean'):
        return 0
    triggered = all(configuration[name] == value for name,value in oracle['literals'].items())
    if oracle['mask'] is not None:
        triggered = triggered and configuration[oracle['mask']] == '0'
        # Extra changes can mask the failure: this is deliberately nonmonotonic.
        triggered = triggered and sum(v != '0' for v in configuration.values()) <= 3
    if triggered and oracle['period'] is not None:
        triggered = observation_index % oracle['period'] != 0
    return 23 if triggered else 0


def stable_failure(oracle: dict, configuration: dict[str,str]) -> bool:
    return oracle['period'] is None and evaluate(oracle,configuration,1) == 23


def main() -> int:
    oracle = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    index = int(sys.argv[2]) if len(sys.argv)>2 else 1
    configuration = {name:os.environ[name] for name in oracle['parameters']}
    code = evaluate(oracle,configuration,index)
    if code == 23:
        print('fixture failure',file=sys.stderr)
    elif code == 64:
        print('invalid configuration',file=sys.stderr)
    else:
        print('ok')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
