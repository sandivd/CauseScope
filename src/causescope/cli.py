"""Command-line workflow, preserving the original validate/run interface."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .engine import ExecutionNotAllowed
from .spec import SpecError, load_spec
from .workflow import configure, describe, findings, investigate, reproduce


def _options(parser):
    parser.add_argument('--reliability', choices=['repeat', 'holdout'], help='repeat: repeated observations; holdout: independent fixed-sample probability validation (more cost, IID assumptions)')
    parser.add_argument('--max-runs', type=int, help='Cap ALL observations, including confirmation')
    parser.add_argument('--max-seconds', type=float, help='Cooperative total time budget; cleanup may overrun')
    parser.add_argument('--max-configurations', type=int, help='Cap distinct configurations')


def _configured(args):
    return configure(load_spec(args.specification), reliability=args.reliability,
        max_runs=args.max_runs, max_seconds=args.max_seconds, max_configurations=args.max_configurations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Investigate configuration failures in disposable local programs.')
    parser.add_argument('--version', action='version', version=f'CauseScope {__version__}')
    commands = parser.add_subparsers(dest='action', required=True)
    init = commands.add_parser('init', help='Guided YAML creation; executes nothing')
    init.add_argument('specification', type=Path)
    validate = commands.add_parser('validate', help='Validate YAML; executes nothing')
    validate.add_argument('specification', type=Path)
    validate.add_argument('--json', action='store_true', help='Machine-readable validation and parameter definitions')
    plan = commands.add_parser('plan', help='Prepare a reviewable bounded plan; executes nothing')
    plan.add_argument('specification', type=Path)
    plan.add_argument('--workspace', type=Path, default=Path.cwd())
    plan.add_argument('--output', type=Path, required=True, help='Fresh directory for eventual investigation artifacts')
    _options(plan)
    approve = commands.add_parser('approve', help='Issue a one-use local MCP approval after reviewing a plan; executes nothing')
    approve.add_argument('plan', type=Path)
    approve.add_argument('--workspace', type=Path, required=True)
    approve.add_argument('--allow-local-execution', action='store_true')
    run = commands.add_parser('run', help='Investigate and export HTML/JSON evidence')
    run.add_argument('specification', type=Path)
    run.add_argument('--allow-local-execution', action='store_true', help='Acknowledge local arbitrary command execution')
    run.add_argument('--output', type=Path, default=Path('results'))
    run.add_argument('--overwrite', action='store_true', help='Explicitly replace existing HTML/JSON reports')
    run.add_argument('--quiet', action='store_true', help='Suppress per-observation progress on stderr')
    _options(run)
    show = commands.add_parser('show', help='Read findings and artifact paths; executes nothing')
    show.add_argument('evidence', type=Path)
    show.add_argument('--json', action='store_true')
    again = commands.add_parser('reproduce', help='Fresh baseline/candidate/deletion observations from reviewed evidence')
    again.add_argument('evidence', type=Path)
    again.add_argument('--allow-local-execution', action='store_true')
    again.add_argument('--output', type=Path, required=True, help='New reproduction JSON file (never overwrites)')
    mcp = commands.add_parser('mcp', help='Optional local stdio MCP server (install .[mcp])')
    mcp.add_argument('--workspace', type=Path, required=True)
    mcp.add_argument('--allow-local-execution', action='store_true', help='Enable approved runs; approvals still required per plan')
    args = parser.parse_args(argv)
    try:
        if args.action == 'mcp':
            from .mcp_server import serve
            serve(args.workspace, allow_local_execution=args.allow_local_execution)
            return 0
        if args.action == 'init':
            from .wizard import create
            create(args.specification)
            print(f'Created: {args.specification.resolve()}\nNext: causescope validate "{args.specification}"')
            return 0
        if args.action == 'plan':
            from .service import InvestigationService
            result = InvestigationService(args.workspace).prepare(str(args.specification.resolve()), str(args.output),
                reliability=args.reliability, max_runs=args.max_runs, max_seconds=args.max_seconds,
                max_configurations=args.max_configurations)
            print(json.dumps(result, indent=2))
            return 0
        if args.action == 'approve':
            from .service import InvestigationService
            result = InvestigationService(args.workspace).approve(args.plan, allow_local_execution=args.allow_local_execution)
            print(json.dumps(result, indent=2))
            return 0
        if args.action == 'reproduce':
            result = reproduce(args.evidence, args.output, allow_local_execution=args.allow_local_execution)
            print(f"{result['status']}: {len(result['observations'])} fresh observations; JSON: {args.output.resolve()}")
            return 0 if result['pattern_reproduced'] else 3
        if args.action == 'show':
            result = json.loads(args.evidence.read_text(encoding='utf-8'))
            view = findings(result, args.evidence)
            if args.json:
                print(json.dumps(view, indent=2, ensure_ascii=False))
            else:
                _print_findings(view)
            return 0
        if args.action == 'validate':
            spec = load_spec(args.specification)
            if args.json:
                print(json.dumps({'valid': True, 'executed': False, 'plan': describe(spec)}, indent=2))
            else:
                print(f'Valid: {spec.name}; {len(spec.parameters)} parameter(s). No commands executed.')
                for name, parameter in spec.parameters.items():
                    print(f'  {name}: baseline={parameter.baseline!r}; alternatives={list(parameter.values)!r}')
                print('Reliability: ' + describe(spec)['policy_explanation'])
            return 0
        spec = _configured(args)
        def progress(p):
            if not args.quiet:
                runs = 'unlimited' if p['remaining_observations'] is None else str(p['remaining_observations'])
                seconds = 'unlimited' if p['remaining_seconds'] is None else f"{p['remaining_seconds']:.1f}s"
                print(f"Observed {p['observations']}; configurations {p['unique_configurations']}; "
                      f"remaining: {runs} observations, {p['remaining_configurations']} new configurations, {seconds}", file=sys.stderr, flush=True)
        result = investigate(spec, args.output, allow_local_execution=args.allow_local_execution, overwrite=args.overwrite, callback=progress)
        _print_findings(findings(result, args.output/'evidence.json'))
        return 3 if result['status'] in ('inconclusive', 'baseline_failure') else 0
    except (SpecError, ExecutionNotAllowed, OSError, ValueError, KeyError, TypeError, EOFError) as exc:
        print(f'causescope: {exc}', file=sys.stderr)
        print('Use causescope init for guided YAML or causescope validate SPEC to check parameter definitions.', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('causescope: interrupted', file=sys.stderr)
        return 130


def _print_findings(view):
    print(f"{view['status']}: {view['reason']}")
    print('Evidence: ' + view['evidence']['status'] + '; finite observations, not certainty.')
    if view['diagnosis']:
        print('Candidate changes: ' + json.dumps(view['diagnosis']['changes'], ensure_ascii=False))
        print(f"Confirmed: {view['diagnosis']['confirmed']}; observed 1-minimal: {view['diagnosis']['one_minimal']}")
    print(f"{view['counts']['tested_configurations']} configurations; {view['counts']['subprocess_runs']} observations; {view['duration_seconds']:.3f}s")
    for tip in view['inconclusive_help']:
        print('  ' + tip)
    for warning in view['execution_warnings']:
        print('Execution warning: ' + warning)
    print('HTML: ' + view['artifacts']['html'] + '\nJSON: ' + view['artifacts']['json'])
    if view['diagnosis']:
        print('Fresh reproduction (review recorded argv before execution):\n' + view['reproduction']['command_powershell'])
    print(view['claim_boundary'])


if __name__ == '__main__':
    raise SystemExit(main())
