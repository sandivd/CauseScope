"""Independently verify the old/fixed defect before invoking CauseScope."""

import argparse
import html
import json
import platform
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from causescope.engine import ExperimentEngine
from causescope.report import write_reports
from causescope.spec import load_spec

from .adapter import HERE,execute,sha256,verify


def reproduce(output: Path, *, allow_local_execution: bool=False) -> dict:
    if not allow_local_execution:
        raise ValueError('--allow-local-execution is required')
    if output.exists():
        raise FileExistsError('Historical reproduction output must be fresh')
    output.mkdir(parents=True)
    records=[]
    for release in ('26.5.1','26.10.0'):
        verify(release)
        for length,skip,fast in ((120,False,False),(40,False,False),(120,True,False),(40,True,False),(40,True,True)):
            with tempfile.TemporaryDirectory(prefix='causescope-historical-') as root:
                record=execute(release,Path(root),length,skip,fast)
                record['temporary_directory']=root
                records.append(record)
    bad=[r for r in records if r['release']=='26.5.1' and r['line_length']==40 and r['skip_magic_trailing_comma']]
    good=[r for r in records if r['release']=='26.10.0']
    checks={'old_safe_rejected_non_equivalence':any(not r['fast'] and r['exit_code']==123 and 'not equivalent' in r['stderr'] for r in bad),
            'old_fast_changed_ast':any(r['fast'] and r['exit_code']==0 and not r['ast_equivalent'] for r in bad),
            'fixed_all_preserve_ast':all(r['exit_code']==0 and r['ast_equivalent'] for r in good),
            'old_controls_pass':all(r['exit_code']==0 and r['ast_equivalent'] for r in records
                                   if r['release']=='26.5.1' and (r['line_length']==120 or not r['skip_magic_trailing_comma']))}
    # Evidence of unsuccessful reproduction is exported before deciding whether to diagnose.
    summary={'schema_version':1,'host':platform.platform(),'python':sys.version,'upstream_fix':'https://github.com/psf/black/pull/5272',
             'lock_sha256':sha256(HERE/'target.lock.json'),'fixture_sha256':sha256(HERE/'fixture.txt'),
             'independent_checks':checks,'independent_runs':records,'verified':all(checks.values())}
    (output/'independent.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    if summary['verified']:
        spec=load_spec(HERE/'experiment.yaml')
        result=ExperimentEngine(spec).run(allow_local_execution=True)
        write_reports(result,output/'causescope-bad')
        fixed_command=list(spec.command)
        fixed_command[fixed_command.index('26.5.1')]='26.10.0'
        control=ExperimentEngine(replace(spec,command=tuple(fixed_command))).run(allow_local_execution=True)
        write_reports(control,output/'causescope-fixed')
        summary['causescope_bad']={'status':result['status'],'diagnosis':result['diagnosis'],'search':result['search']}
        summary['causescope_fixed']={'status':control['status'],'search':control['search']}
        summary['verified'] &= (result['status']=='failure_found' and result['diagnosis']['one_minimal']
            and result['diagnosis']['changes']=={'BLACK_LINE_LENGTH':'40','BLACK_SKIP_COMMA':'true'}
            and control['status']=='no_failure_found')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    interpretation = (
        'The affected release rejects the short-line/skip-magic combination under its AST safety check; '
        'under --fast it writes a different AST. The fixed release preserves the AST. This independently '
        'supports a genuine historical defect reproduction, unlike the earlier intentional Git guard.\n\n'
        if summary['verified'] else
        'The reproduction or CauseScope validation did not pass every required check. '
        'No successful external validation is claimed. Inspect the independent checks and any '
        'exported engine results; installation, unsupported environment and target errors must be resolved.\n\n')
    engine_links = (
        '[CauseScope affected-release report](causescope-bad/report.html), '
        '[fixed-release control report](causescope-fixed/report.html).\n\n'
        if 'causescope_bad' in summary else 'CauseScope was not invoked because independent verification failed.\n\n')
    text=('# Historical Black defect reproduction\n\n'
        f"Verified: **{summary['verified']}**. Original pinned Black 26.5.1 and 26.10.0 wheels/dependencies were used unmodified.\n\n"
        'The published upstream regression input was passed directly to each native CLI before CauseScope ran. '
        + interpretation +
        f"Independent checks: `{json.dumps(checks)}`.\n\n"
        '[Upstream fix #5272](https://github.com/psf/black/pull/5272), [complete native command/output evidence](independent.json), '
        '[structured result](summary.json). ' + engine_links +
        'A confirmed 1-minimal setting pair for this fixture would not prove the internal root cause. '
        'The repair release and independent AST checks strengthen external validation. This remains one defect, '
        'one input, Windows AMD64 and CPython 3.10. Other versions/platforms are unsupported by this wheel lock. '
        'Native timeouts are 10 seconds; outer observations are capped at 15 seconds. Temporary directories are file isolation, not a security sandbox.\n\n'
        'Reproduce from the repository root:\n\n```powershell\n'
        '.\\.venv-win\\Scripts\\python.exe -m benchmarks.black_regression.setup --allow-download\n'
        '.\\.venv-win\\Scripts\\python.exe -m benchmarks.black_regression.reproduce --allow-local-execution --output .\\results\\black-historical\n```\n')
    (output/'REPORT.md').write_text(text,encoding='utf-8')
    (output/'REPORT.html').write_text("<!doctype html><meta charset='utf-8'><title>Historical Black defect</title><pre>"+html.escape(text)+'</pre>',encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--allow-local-execution',action='store_true')
    args=parser.parse_args()
    summary=reproduce(args.output,allow_local_execution=args.allow_local_execution)
    print('Historical reproduction verified:',summary['verified'])
    raise SystemExit(0 if summary['verified'] else 3)
