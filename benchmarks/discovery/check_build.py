"""Inspect built distributions and smoke-test the actual wheel with local opt-in."""

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def check(dist: Path, output: Path, *, allow_local_execution: bool=False):
    if not allow_local_execution:
        raise ValueError('--allow-local-execution is required for the wheel smoke experiment')
    dist=dist.resolve()
    root=Path(__file__).resolve().parents[2]
    from causescope import __version__
    from tools.publication_audit import Audit
    wheel=dist/f'causescope-{__version__}-py3-none-any.whl'
    source=dist/f'causescope-{__version__}.tar.gz'
    audit=Audit(root)
    audit.archive(source, 'sdist')
    audit.archive(wheel, 'wheel')
    if audit.findings:
        raise AssertionError('Distribution publication audit failed: '+str(audit.findings))
    script="""
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import causescope
from causescope.engine import ExperimentEngine
from causescope.spec import parse_spec
assert causescope.__file__.startswith(sys.argv[1]),causescope.__file__
raw={'version':1,'name':'built wheel smoke','scope':'local',
 'command':['{python}','-c',"import os; raise SystemExit(23 if os.getenv('A')==os.getenv('B')=='1' else 0)"],
 'parameters':{name:{'baseline':'0','values':['1']} for name in ('A','B')},
 'failure':{'exit_code':23},'settings':{'strategy':'adaptive','max_configurations':4}}
result=ExperimentEngine(parse_spec(raw,Path.cwd()/'wheel.yaml')).run(allow_local_execution=True)
assert result['status']=='failure_found'
assert result['diagnosis']['one_minimal'] and result['diagnosis']['changes']=={'A':'1','B':'1'}
print(json.dumps({'status':result['status'],'observations':result['search']['subprocess_runs'],
                 'one_minimal':result['diagnosis']['one_minimal'],'loaded_from_wheel':True,
                 'holdout':(lambda result:{'status':result['status'],'evidence_status':result['evidence']['status'],
                    'observations':result['search']['subprocess_runs'],'one_minimal':result['diagnosis']['one_minimal']})(
                    ExperimentEngine(parse_spec({**raw,'confirmation':{'policy':'holdout','samples_per_arm':12,
                        'min_failure_probability':.5}},Path.cwd()/'holdout.yaml')).run(allow_local_execution=True))}))
"""
    with tempfile.TemporaryDirectory(prefix='causescope-wheel-check-') as work:
        completed=subprocess.run([sys.executable,'-I','-c',script,str(wheel)],cwd=work,
                                 capture_output=True,text=True,timeout=60,check=True)
    result={'passed':True,'publication_audit_passed':True,
            'wheel_smoke':json.loads(completed.stdout),'python':sys.version,
            'artifacts':{path.name:{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
                         for path in (wheel,source)}}
    if (result['wheel_smoke']['holdout']['evidence_status']!='statistically_supported'
            or not result['wheel_smoke']['holdout']['one_minimal']):
        raise AssertionError('Built wheel holdout smoke experiment failed')
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-local-execution',action='store_true')
    args=parser.parse_args()
    print(json.dumps(check(args.dist,args.output,allow_local_execution=args.allow_local_execution),indent=2))
