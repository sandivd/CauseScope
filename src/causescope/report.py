"""Portable HTML and JSON exports containing all retained observations."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def prepare_output(directory: Path, *, overwrite: bool = False) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("evidence.json", "report.html"):
        path = directory / name
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path} already exists; choose another --output or use --overwrite")
        if path.is_dir():
            raise IsADirectoryError(str(path))


def render_html(result: dict) -> str:
    diagnosis = result["diagnosis"]
    diagnosis_html = "<p>No confirmed minimal change set.</p>"
    if diagnosis:
        diagnosis_html = (
            f"<p>Confirmed: <strong>{_escape(diagnosis['confirmed'])}</strong>; "
            f"1-minimal: <strong>{_escape(diagnosis['one_minimal'])}</strong>.</p>"
            f"<pre>{_escape(json.dumps(diagnosis['changes'], indent=2, ensure_ascii=False))}</pre>"
        )
    evaluations = []
    for evaluation in result["evaluations"]:
        runs = []
        for number, run in enumerate(evaluation["runs"], 1):
            runs.append(
                f"<details><summary>Observation {number}: {_escape(run['outcome'])}; "
                f"exit {_escape(run['exit_code'])}; {run['duration_seconds']:.4f}s</summary>"
                f"<p>Timeout: {_escape(run['timed_out'])}; "
                f"timeout limit: {_escape(run.get('timeout_seconds'))}; "
                f"budget-limited timeout: {_escape(run.get('budget_limited_timeout', False))}; "
                f"launch error: {_escape(run['launch_error'])}; "
                f"termination error: {_escape(run['termination_error'])}; "
                f"cleanup error: {_escape(run['cleanup_error'])}</p>"
                f"<p>argv</p><pre>{_escape(json.dumps(run['command'], ensure_ascii=False))}</pre>"
                f"<p>Temporary working directory</p><pre>{_escape(run['working_directory'])}</pre>"
                f"<p>stdout (truncated: {_escape(run['stdout_truncated'])})</p><pre>{_escape(run['stdout'])}</pre>"
                f"<p>stderr (truncated: {_escape(run['stderr_truncated'])})</p><pre>{_escape(run['stderr'])}</pre>"
                "</details>"
            )
        evaluations.append(
            f"<article id='{_escape(evaluation['id'])}'><h3>{_escape(evaluation['id'])}: "
            f"{_escape(evaluation['status'])}</h3>"
            f"<p>Phases: {_escape(', '.join(evaluation['phases']))}</p>"
            f"<p>Changes from baseline</p><pre>{_escape(json.dumps(evaluation['changes'], indent=2, ensure_ascii=False))}</pre>"
            f"<p>Effective parameters</p><pre>{_escape(json.dumps(evaluation['configuration'], indent=2, ensure_ascii=False))}</pre>"
            + "".join(runs) + "</article>"
        )
    search = result["search"]
    evidence_html = ""
    if result.get('evidence'):
        evidence_html = ('<h2>Evidence status</h2><pre>'
            + _escape(json.dumps(result['evidence'], indent=2)) + '</pre>')
    validation_html = ""
    workflow_html = ""
    if result.get('workflow'):
        workflow_html = ('<h2>Reproduction and next steps</h2><pre>'
            + _escape(json.dumps(result['workflow'], indent=2, ensure_ascii=False)) + '</pre>')
    if result.get('validation'):
        validation_html = ('<h2>Independent holdout validation</h2><p>'
            'One frozen candidate/control family; discovery observations are excluded from these statistics. '
            'Bounds assume fresh stationary IID observations within each arm. Finite confidence is not certainty. '
            'A supported probability contrast does not itself establish 1-minimality.</p><details>'
            '<summary>Protocol, simultaneous bounds and every fresh observation</summary><pre>'
            + _escape(json.dumps(result['validation'], indent=2, ensure_ascii=False)) + '</pre></details>')
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>CauseScope experiment report</title><style>"
        "body{font:16px/1.55 system-ui,sans-serif;color:#172d39;background:#f5f7f8;"
        "max-width:960px;margin:40px auto;padding:0 24px}h1,h2,h3{line-height:1.2}"
        "article{background:white;border:1px solid #ccd7dc;padding:20px;margin:20px 0}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#edf2f4;padding:12px}"
        "summary{cursor:pointer;padding:8px 0}small{color:#425967}</style></head><body>"
        f"<small>CauseScope {_escape(result['causescope_version'])} · {_escape(result['created_at'])}</small>"
        f"<h1>{_escape(result['specification']['name'])}</h1>"
        f"<h2>{_escape(result['status'])}</h2><p>{_escape(result['reason'])}</p>"
        f"<p>{search['tested_configurations']} tested configurations; "
        f"{_escape(search.get('valid_configurations', search['total_configurations']))} valid configurations "
        f"(unknown if None); {search['total_configurations']} raw Cartesian configurations; "
        f"{search['subprocess_runs']} subprocess runs; {result['duration_seconds']:.3f}s.</p>"
        f"<p>Search complete: {_escape(search['search_complete'])}; "
        f"budget exhausted: {_escape(search['budget_exhausted'])}; "
        f"stop reason: {_escape(search.get('stop_reason'))}; "
        f"strategy: {_escape(search.get('strategy', 'legacy'))}.</p>"
        + evidence_html + validation_html + workflow_html + "<h2>Diagnosis</h2>" + diagnosis_html +
        "<p>1-minimal means removing any one reported change stopped reproducing the predicate "
        "in the observations collected. It does not establish a globally smallest set or prove causality. "
        "A pass means the failure predicate was absent, which may include other program errors. "
        "Inconclusive observations do not establish absence of a failure.</p>"
        "<h2>Specification</h2>"
        f"<pre>{_escape(json.dumps(result['specification'], indent=2, ensure_ascii=False))}</pre>"
        "<h2>Observations</h2>" + "".join(evaluations) + "</body></html>"
    )


def write_reports(result: dict, directory: Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    prepare_output(directory, overwrite=overwrite)
    evidence_path, report_path = directory / "evidence.json", directory / "report.html"
    evidence_path.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    report_path.write_text(render_html(result), encoding="utf-8")
    return evidence_path, report_path
