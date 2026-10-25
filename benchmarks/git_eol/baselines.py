"""Independent exhaustive and one-setting-at-a-time comparison strategies."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from causescope import __version__
from causescope.engine import Evaluation, ExecutionNotAllowed
from causescope.report import prepare_output, write_reports
from causescope.runner import run_once
from causescope.spec import load_spec

SPEC_PATH = Path(__file__).with_name("experiment.yaml")


def run_baseline(spec, method: str, *, allow_local_execution: bool = False, runner=run_once) -> dict:
    if not allow_local_execution:
        raise ExecutionNotAllowed("--allow-local-execution is required")
    if method not in ("brute_force", "one_at_a_time"):
        raise ValueError(f"Unknown comparison method: {method}")
    started = time.perf_counter()
    created_at = datetime.now(timezone.utc).isoformat()
    baseline = spec.baseline
    total = 1
    for parameter in spec.parameters.values():
        total *= 1 + len(parameter.values)
    if total > spec.settings.max_configurations and method == "brute_force":
        raise ValueError("Brute-force space exceeds the specification configuration budget")
    evaluations = {}

    def evaluate(configuration, *, phase=method):
        key = tuple(sorted(configuration.items()))
        if key not in evaluations:
            changes = {name: value for name, value in configuration.items() if value != baseline[name]}
            evaluations[key] = Evaluation(f"b{len(evaluations) + 1:04}", changes, dict(configuration))
        evaluation = evaluations[key]
        evaluation.phases.append(phase)
        for _ in range(spec.settings.repetitions):
            evaluation.runs.append(runner(spec, configuration))
        outcomes = {run.outcome for run in evaluation.runs}
        evaluation.status = next(iter(outcomes)) if len(outcomes) == 1 else "inconclusive"
        return evaluation

    control = evaluate(baseline, phase="baseline")
    if control.status == "pass":
        if method == "brute_force":
            names = tuple(spec.parameters)
            choices = [(p.baseline,) + p.values for p in spec.parameters.values()]
            for values in itertools.product(*choices):
                configuration = dict(zip(names, values))
                if configuration != baseline:
                    evaluate(configuration)
        else:
            for name, parameter in spec.parameters.items():
                for value in parameter.values:
                    evaluate({**baseline, name: value})
    failing = [evaluation for evaluation in evaluations.values() if evaluation.status == "failure"]
    diagnosis = None
    if control.status != "pass":
        status = "baseline_failure" if control.status == "failure" else "inconclusive"
        reason = "The baseline is not a consistent healthy control."
    elif failing:
        candidate = min(failing, key=lambda e: len(e.changes))
        evaluate(candidate.configuration, phase="confirmation")
        evaluate(baseline, phase="baseline_confirmation")
        confirmed = candidate.status == "failure" and control.status == "pass"
        reductions = [{**candidate.configuration, name: baseline[name]} for name in candidate.changes]
        minimal = all(evaluations.get(tuple(sorted(c.items()))) is not None
                      and evaluations[tuple(sorted(c.items()))].status == "pass" for c in reductions)
        diagnosis = {"changes": candidate.changes, "configuration": candidate.configuration,
                     "confirmed": confirmed, "one_minimal": confirmed and minimal,
                     "evaluation_id": candidate.id}
        status = "failure_found" if confirmed and minimal else "inconclusive"
        reason = "A failing candidate was enumerated and confirmed." if confirmed else "Confirmation changed the result."
    else:
        status = "inconclusive" if any(e.status == "inconclusive" for e in evaluations.values()) else "no_failure_found"
        reason = ("No consistently failing configuration in the exhaustive space." if method == "brute_force" else
                  "No individual change reproduced the predicate; interactions were not tested.")
    conclusive = all(e.status != "inconclusive" for e in evaluations.values())
    complete = len(evaluations) == total
    return {
        "schema_version": 1, "causescope_version": __version__, "comparison_method": method,
        "created_at": created_at, "duration_seconds": time.perf_counter() - started,
        "specification": spec.evidence(), "status": status, "reason": reason, "diagnosis": diagnosis,
        "global_minimum_within_specification": bool(complete and conclusive and diagnosis and diagnosis["one_minimal"]),
        "search": {"total_configurations": total, "tested_configurations": len(evaluations),
                   "search_complete": complete, "budget_exhausted": False,
                   "subprocess_runs": sum(len(e.runs) for e in evaluations.values())},
        "evaluations": [asdict(e) for e in evaluations.values()],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", choices=("brute_force", "one_at_a_time"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-local-execution", action="store_true")
    args = parser.parse_args(argv)
    if not args.allow_local_execution:
        parser.error("--allow-local-execution is required")
    try:
        prepare_output(args.output)
        result = run_baseline(load_spec(SPEC_PATH), args.method, allow_local_execution=True)
        write_reports(result, args.output)
        print(json.dumps({"method": args.method, "status": result["status"], **result["search"]}))
        return 3 if result["status"] in ("inconclusive", "baseline_failure") else 0
    except (ValueError, OSError) as exc:
        print(f"benchmark baseline: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
