"""Forward native Black behavior on a published historical regression fixture."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
LOCK=HERE/'target.lock.json'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(release: str) -> dict:
    lock=json.loads(LOCK.read_text(encoding='utf-8'))
    if (sys.platform!='win32' or sys.version_info[:2]!=(3,10)
            or platform.machine().lower() not in ('amd64','x86_64') or sys.maxsize<=2**32):
        raise RuntimeError('This exact historical wheel pin requires Windows AMD64 / CPython 3.10')
    if release not in lock['installed_code_sha256']:
        raise ValueError('Unpinned Black release')
    installation=ROOT/'.tools'/('black-'+release)
    for relative,expected in lock['installed_code_sha256'][release].items():
        if sha256(installation/relative)!=expected:
            raise RuntimeError('Installed Black/dependency code hash mismatch: '+relative)
    return {'release':release,'installation':str(installation),'lock_sha256':sha256(LOCK)}


def execute(release: str, work: Path, length: int, skip: bool, fast: bool=False) -> dict:
    target=verify(release)
    source=(HERE/'fixture.txt').read_text(encoding='utf-8')
    work.mkdir(parents=True,exist_ok=True)
    if any(work.iterdir()):
        raise ValueError('Historical reproduction requires an empty working directory')
    for name in ('home','cache','tmp'):
        (work/name).mkdir()
    path=work/'case.py'
    path.write_text(source,encoding='utf-8')
    config=work/'black.toml'; config.write_text('',encoding='utf-8')
    environment={key:value for key,value in os.environ.items() if not key.startswith('BLACK_')}
    environment.update(PYTHONPATH=target['installation'],BLACK_CACHE_DIR=str(work/'cache'),
        HOME=str(work/'home'),USERPROFILE=str(work/'home'),XDG_CACHE_HOME=str(work/'cache'),
        TMP=str(work/'tmp'),TEMP=str(work/'tmp'),PYTHONIOENCODING='utf-8')
    command=[sys.executable,'-m','black','--required-version',release,'--config',str(config),
             '--line-length',str(length)]+(['--skip-magic-trailing-comma'] if skip else [])+(['--fast'] if fast else [])+[str(path)]
    completed=subprocess.run(command,cwd=work,env=environment,capture_output=True,timeout=10)
    output=path.read_text(encoding='utf-8')
    return {**target,'command':command,'line_length':length,'skip_magic_trailing_comma':skip,'fast':fast,
        'exit_code':completed.returncode,'stdout':completed.stdout.decode('utf-8',errors='replace'),
        'stderr':completed.stderr.decode('utf-8',errors='replace'),'input':source,'output':output,
        'input_sha256':hashlib.sha256(source.encode()).hexdigest(),'output_sha256':hashlib.sha256(output.encode()).hexdigest(),
        'ast_equivalent':ast.dump(ast.parse(source))==ast.dump(ast.parse(output))}


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',choices=('26.5.1','26.10.0'),required=True)
    parser.add_argument('--allow-local-execution',action='store_true')
    args=parser.parse_args()
    if not args.allow_local_execution:
        parser.error('--allow-local-execution is required')
    try:
        length=int(os.environ['BLACK_LINE_LENGTH'])
        skip=os.environ['BLACK_SKIP_COMMA']=='true'
        result=execute(args.release,Path.cwd(),length,skip)
        print(result['stdout'],end='')
        print(result['stderr'],end='',file=sys.stderr)
        print('CAUSESCOPE_BLACK_META='+json.dumps(result),flush=True)
        return result['exit_code']
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as exc:
        print('unsupported historical reproduction: '+str(exc),file=sys.stderr)
        return 70


if __name__=='__main__':
    raise SystemExit(main())
