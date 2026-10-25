"""Run each strategy in a fresh CLI process and export measured evidence."""

from __future__ import annotations

import argparse
import html
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .adapter import HERE, ROOT, SETTINGS, sha256, verify_target
from .audit import audit_result
from .baselines import SPEC_PATH

METHODS = ("causescope", "brute_force", "one_at_a_time")
EXPECTED_CHANGES = {"core.autocrlf": "true", "core.safecrlf": "true"}


def powershell_command(arguments: list[str]) -> str:
    return "& " + " ".join("'" + argument.replace("'", "''") + "'" for argument in arguments)


def source_provenance(git_executable: str) -> dict:
    paths = list((ROOT / "src" / "causescope").glob("*.py"))
    paths += [path for path in HERE.iterdir() if path.suffix in (".py", ".json", ".yaml", ".ps1")]
    revision = subprocess.run([git_executable, "-c", f"safe.directory={ROOT.as_posix()}", "rev-parse", "HEAD"],
                              cwd=ROOT, capture_output=True, text=True, timeout=10)
    return {"git_revision": revision.stdout.strip() if revision.returncode == 0 else None,
            "git_error": revision.stderr.strip() if revision.returncode else None,
            "files_sha256": {path.relative_to(ROOT).as_posix(): sha256(path) for path in sorted(paths)}}


def changes_in_native_names(changes: dict) -> dict:
    return {SETTINGS[int(key.rsplit("_", 1)[1])]: value for key, value in changes.items()}


def _truth_table(result: dict) -> dict:
    return {tuple(sorted(e["configuration"].items())): e["status"] for e in result["evaluations"]}


def assess_trial(results: dict[str, dict]) -> dict:
    cause, brute, oat = (results[name] for name in METHODS)
    brute_table = _truth_table(brute)
    agreement = all(brute_table.get(key) == status
                    for result in results.values() for key, status in _truth_table(result).items())
    failing = [e for e in brute["evaluations"] if e["status"] == "failure"]
    minimum = min((len(e["changes"]) for e in failing), default=None)
    minimum_sets = [changes_in_native_names(e["changes"]) for e in failing if len(e["changes"]) == minimum]
    return {
        "causescope_confirmed_expected_pair": bool(cause["status"] == "failure_found" and cause["diagnosis"]
            and cause["diagnosis"]["confirmed"] and cause["diagnosis"]["one_minimal"]
            and changes_in_native_names(cause["diagnosis"]["changes"]) == EXPECTED_CHANGES),
        "brute_force_exhaustive_and_conclusive": bool(brute["search"]["search_complete"]
            and len(brute["evaluations"]) == 16 and all(e["status"] != "inconclusive" for e in brute["evaluations"])
            and brute["global_minimum_within_specification"] and brute["diagnosis"]["confirmed"]),
        "oat_did_not_find_interaction": oat["status"] == "no_failure_found" and oat["diagnosis"] is None,
        "overlapping_observations_agree": agreement,
        "observed_minimum_change_count": minimum,
        "observed_minimum_change_sets": minimum_sets,
        "failing_configurations_in_full_space": len(failing),
    }


def render_report(summary: dict) -> str:
    lines = ["# Measured Git configuration benchmark", "",
        f"Generated: {summary['created_at']}. Verified: **{summary['verified']}**.", "",
        "This is an intended Git safety rejection, not a discovered Git defect or proof of root cause.", "",
        f"Target: `{summary['target']['version']}` from the checksum-pinned MinGit archive.",
        f"Host: `{summary['host']['platform']}`; Python `{summary['host']['python']}`.",
        f"Source revision at measurement: `{summary['source']['git_revision']}` (file hashes in summary.json).", "",
        "## All measured campaigns", "",
        "| Trial | Method | Configurations | Batches | Adapter processes | Git invocations | Wall seconds | Sum observation seconds |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for trial in summary["trials"]:
        for record in trial["measurements"]:
            audit = record["audit"]
            lines.append(f"| {trial['trial']} | {record['method']} | {record['configurations']} | "
                f"{audit['configuration_batches']} | {audit['observations']} | {audit['git_invocations']} | "
                f"{record['wall_seconds']:.6f} | {audit['sum_observation_duration_seconds']:.6f} |")
    lines += ["", "## Medians across campaigns", "",
              "| Method | Wall seconds |", "| --- | ---: |"]
    for method, value in summary["median_wall_seconds"].items():
        lines.append(f"| {method} | {value:.6f} |")
    lines += ["", "## Findings and interpretation", ""]
    if summary["verified"]:
        first = {record["method"]: record for record in summary["trials"][0]["measurements"]}
        checks = summary["trials"][0]["checks"]
        lines += [
            "All campaigns identified `core.autocrlf=true` together with `core.safecrlf=true`.",
            "CauseScope discarded the quotePath/trustctime distractors and confirmed a 1-minimal pair.",
            *[f"{method}: {first[method]['configurations']} distinct configurations, "
              f"{first[method]['audit']['configuration_batches']} batches, "
              f"{first[method]['audit']['observations']} adapter observations (first campaign; all campaigns above)."
              for method in METHODS],
            "OAT missed the interaction; its smaller count does not accomplish the diagnosis task.",
            f"Brute force observed {checks['failing_configurations_in_full_space']} failing configurations.",
            f"Its exhaustive conclusive table establishes minimum cardinality {checks['observed_minimum_change_count']} "
            "only in this 16-configuration space; no failing baseline or single changes were observed.",
            "CauseScope reports 1-minimality. Removing each retained change separately eliminated this predicate",
            "in the observations. In general that local property does not exclude smaller failing sets elsewhere,",
            "or nonmonotonic reductions involving several simultaneous removals. In this particular binary space,",
            "a failing pair plus the passing baseline and all four single changes also suffices to rule out",
            "smaller cardinalities. Brute force independently checks the entire table; its exhaustive sweep is",
            "stronger coverage than needed for that size-two lower bound. The engine does not compute or report",
            "a general global-minimality certificate.",
            "These are reproducible failure-inducing settings for a fixed fixture/version; they do not prove a root cause.",
        ]
    else:
        lines += ["Verification failed. Inspect checks, audit errors, exit statuses, and raw evidence in summary.json;",
                  "no benchmark success or minimality conclusion is supported by this report."]
    lines += ["", "## Evidence links", "", "[Machine-readable comparison and exact argv](summary.json).", ""]
    for trial in summary["trials"]:
        for record in trial["measurements"]:
            directory = record["relative_directory"]
            lines.append(f"- Trial {trial['trial']} {record['method']}: [JSON]({directory}/evidence.json), "
                         f"[HTML]({directory}/report.html), [CLI output]({directory}/cli.txt).")
    lines += ["", "## Reproduction (PowerShell, repository root)", "", "```powershell",
        "powershell -NoProfile -ExecutionPolicy Bypass -File .\\benchmarks\\git_eol\\setup.ps1",
        ".\\.venv-win\\Scripts\\python.exe -m benchmarks.git_eol.adapter --check",
        ".\\.venv-win\\Scripts\\python.exe -m causescope run .\\benchmarks\\git_eol\\experiment.yaml --allow-local-execution --output .\\results\\git-eol-reproduction",
        ".\\.venv-win\\Scripts\\python.exe -m benchmarks.git_eol.compare --allow-local-execution --trials 3 --output .\\results\\git-eol-comparison-reproduction",
        "$env:CAUSESCOPE_RUN_GIT_INTEGRATION = '1'",
        ".\\.venv-win\\Scripts\\python.exe -m pytest -q --tb=short", "```", "",
        "Use the interpreter from your installed environment in place of `.venv-win` where appropriate.",
        "Output directories must be fresh; the harness refuses to overwrite evidence. Installation instructions",
        "and the prespecified design are in [the benchmark protocol](../../README.md).", "",
        "### Exact strategy commands executed for these measurements", "", "```powershell"]
    for trial in summary["trials"]:
        for record in trial["measurements"]:
            lines.append(powershell_command(record["command"]))
    lines += ["```", "", "## Failures and limits", "",
        "The first pinned run failed Windows cleanup of read-only Git objects and was inconclusive;",
        "its [original evidence](../initial-readonly-cleanup/evidence.json) is preserved. A focused runner fix",
        "and regression test were required. The earlier installed-Git probe leaked XDG ignore warnings;",
        "the measured adapter isolates those settings. Acquisition, probes, unsuccessful smoke runs, and tests",
        "are excluded from the timed comparisons; the table includes all measured campaigns.", "",
        f"This is one Windows x86-64 machine and {len(summary['trials'])} campaigns with rotating strategy order, not a statistical",
        "performance claim or validation on Linux, macOS, ARM, other Git versions, or the other two candidates.",
        "Setup/acquisition and preflight are excluded. All three methods run in fresh CLI Python processes;",
        "wall time includes their startup, temporary-directory lifecycle, and HTML/JSON export. Observation",
        "duration sums omit some cleanup/export overhead. Every normal adapter observation invokes Git three times",
        "(version, init, add); these are not three distinct configuration experiments. No cross-strategy cache exists.",
        "The full comparison contains extra fresh confirmation observations for CauseScope and brute force;",
        "OAT has no failing candidate to confirm. Counts before those confirmations are 24, 48, and 15 observations.",
        "The tiny hand-selected space and two intentional distractors favor auditability, not general scalability claims.",
        "A narrow predicate can miss unrelated errors; the independent auditor rejects unexpected statuses,",
        "version/fixture/configuration mismatches, mixed observations, and infrastructure errors.",
        "Three repetitions and three campaigns cannot prove determinism. The runner is not a security sandbox.", ""]
    return "\n".join(lines)


def run_comparison(output: Path, trials: int, *, allow_local_execution: bool = False) -> dict:
    if not allow_local_execution:
        raise ValueError("--allow-local-execution is required")
    if trials < 1:
        raise ValueError("trials must be positive")
    if output.exists():
        raise FileExistsError(f"Choose a fresh benchmark output directory: {output}")
    target = verify_target(check_archive=True)
    summary = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "target": target, "host": {"platform": platform.platform(), "python": sys.version,
               "processor": platform.processor(), "cpu_count": os.cpu_count()},
               "source": source_provenance(target["executable"]), "trials": [], "verified": True}
    output.mkdir(parents=True)
    all_directories = set()
    truth_tables = []
    for number in range(1, trials + 1):
        offset = (number - 1) % len(METHODS)
        order = METHODS[offset:] + METHODS[:offset]
        trial = {"trial": number, "order": list(order), "measurements": []}
        results = {}
        for method in order:
            relative = f"trial-{number:02}/{method}"
            destination = output / relative
            if method == "causescope":
                command = [sys.executable, "-m", "causescope", "run", str(SPEC_PATH),
                           "--allow-local-execution", "--output", str(destination.resolve())]
            else:
                command = [sys.executable, "-m", "benchmarks.git_eol.baselines", method,
                           "--allow-local-execution", "--output", str(destination.resolve())]
            print(f"Trial {number}/{trials}: {method}", flush=True)
            started = time.perf_counter()
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=180)
            wall = time.perf_counter() - started
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "cli.txt").write_text(completed.stdout + completed.stderr, encoding="utf-8")
            result = json.loads((destination / "evidence.json").read_text(encoding="utf-8"))
            results[method] = result
            audit = audit_result(result)
            if all_directories.intersection(audit["working_directories"]):
                audit["errors"].append("Working directory shared across strategies/campaigns")
                audit["passed"] = False
            all_directories.update(audit["working_directories"])
            record = {"method": method, "command": command, "returncode": completed.returncode,
                      "wall_seconds": wall, "relative_directory": relative,
                      "configurations": result["search"]["tested_configurations"], "audit": audit}
            trial["measurements"].append(record)
            summary["verified"] &= audit["passed"] and completed.returncode == 0
        trial["checks"] = assess_trial(results)
        checks = trial["checks"]
        summary["verified"] &= all(checks[key] for key in ("causescope_confirmed_expected_pair",
            "brute_force_exhaustive_and_conclusive", "oat_did_not_find_interaction", "overlapping_observations_agree"))
        truth_tables.append(_truth_table(results["brute_force"]))
        summary["trials"].append(trial)
        # Keep completed campaigns even if a later process is interrupted.
        (output / "summary.partial.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary["truth_tables_reproduced_across_campaigns"] = all(table == truth_tables[0] for table in truth_tables)
    summary["verified"] &= summary["truth_tables_reproduced_across_campaigns"]
    summary["median_wall_seconds"] = {method: statistics.median(record["wall_seconds"]
        for trial in summary["trials"] for record in trial["measurements"] if record["method"] == method) for method in METHODS}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report = render_report(summary)
    (output / "REPORT.md").write_text(report, encoding="utf-8")
    (output / "REPORT.html").write_text("<!doctype html><html lang='en'><meta charset='utf-8'>"
        "<title>CauseScope real Git benchmark</title><style>body{max-width:1100px;margin:32px auto;"
        "padding:0 20px;font:15px/1.5 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
        "<h1>CauseScope real Git benchmark</h1><p><a href='summary.json'>JSON evidence index</a> · "
        "<a href='REPORT.md'>Markdown report with evidence links</a></p><pre>" + html.escape(report) + "</pre></html>", encoding="utf-8")
    (output / "summary.partial.json").unlink()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-local-execution", action="store_true")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = run_comparison(args.output.resolve(), args.trials, allow_local_execution=args.allow_local_execution)
        print(json.dumps({"verified": summary["verified"], "median_wall_seconds": summary["median_wall_seconds"]}))
        return 0 if summary["verified"] else 3
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"benchmark comparison failed/unsupported: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
