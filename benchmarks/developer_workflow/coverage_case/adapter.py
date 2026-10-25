"""Map environment values to unmodified pinned Coverage.py CLI arguments."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TARGET = ROOT/'.tools/coverage-7.16.2'


def code_hash(directory):
    paths = sorted(p for p in (directory/'coverage').rglob('*') if p.suffix in ('.py', '.pyd'))
    entries = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def verify():
    lock = json.loads((HERE/'target.lock.json').read_text(encoding='utf-8'))
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64','x86_64') or sys.version_info[:2] != (3,10):
        raise RuntimeError('Coverage wheel lock supports Windows AMD64 CPython 3.10 only')
    wheel = ROOT/'.tools/coverage-download'/lock['filename']
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != lock['sha256'] or code_hash(TARGET) != lock['code_sha256']:
        raise RuntimeError('Coverage original wheel/installed code hash mismatch')
    if hashlib.sha256((HERE/'fixture.py').read_bytes()).hexdigest() != lock['fixture_sha256']:
        raise RuntimeError('Coverage fixture hash mismatch')
    return lock


def native(arguments, work, environment):
    command = [sys.executable, '-I', '-c',
        'import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_module("coverage",run_name="__main__")',
        str(TARGET), *arguments]
    result = subprocess.run(command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
        text=True, encoding='utf-8', errors='replace', capture_output=True, timeout=10)
    return {'command': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}


def execute(work, branch, threshold):
    lock = verify()
    if branch not in ('false','true') or threshold not in ('0','90'):
        raise ValueError('Unsupported Coverage configuration')
    work = Path(work)
    (work/'fixture.py').write_bytes((HERE/'fixture.py').read_bytes())
    (work/'empty.coveragerc').write_text('[run]\n', encoding='utf-8')
    environment = {k:v for k,v in os.environ.items() if not k.startswith(('COVERAGE_','PYTHON'))}
    environment.update(HOME=str(work), USERPROFILE=str(work), TMP=str(work), TEMP=str(work),
                       COVERAGE_FILE=str(work/'.coverage'))
    records = [native(['--version'], work, environment)]
    if records[-1]['exit_code'] or '7.16.2' not in records[-1]['stdout']:
        raise RuntimeError('Native Coverage version preflight failed')
    records.append(native(['run', '--rcfile=empty.coveragerc', *(['--branch'] if branch=='true' else []), 'fixture.py'], work, environment))
    if records[-1]['exit_code']:
        raise RuntimeError('Coverage run failed: '+json.dumps(records[-1]))
    records.append(native(['report', '--rcfile=empty.coveragerc', '--fail-under='+threshold], work, environment))
    return {'version': '7.16.2', 'configuration': {'branch':branch,'threshold':threshold},
            'artifact_sha256':lock['sha256'], 'code_sha256':lock['code_sha256'],
            'fixture_sha256':lock['fixture_sha256'], 'native':records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-local-execution', action='store_true')
    args = parser.parse_args()
    if not args.allow_local_execution:
        parser.error('Requires --allow-local-execution')
    try:
        record = execute(Path.cwd(), os.environ['COVERAGE_BRANCH'], os.environ['COVERAGE_THRESHOLD'])
        print(json.dumps(record))
        # Forward native output and status. No fabricated failure oracle.
        print(record['native'][-1]['stdout'], end='')
        print(record['native'][-1]['stderr'], end='', file=sys.stderr)
        return record['native'][-1]['exit_code']
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(str(exc), file=sys.stderr)
        return 70


if __name__ == '__main__':
    raise SystemExit(main())
