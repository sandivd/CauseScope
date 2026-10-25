"""Bounded configuration search, repeatability checks, and diagnosis."""

from __future__ import annotations

import itertools
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Callable

from . import __version__
from .minimize import ddmin
from .runner import RunRecord, run_once
from .spec import ExperimentSpec
from .selection import PlanningStopped, Space, configurations
from .confirmation import evidence_status, holdout


class ExecutionNotAllowed(ValueError):
    """Local execution has not been explicitly acknowledged."""


@dataclass
class Evaluation:
    id: str
    changes: dict[str, str]
    configuration: dict[str, str]
    phases: list[str] = field(default_factory=list)
    status: str = "inconclusive"
    runs: list[RunRecord] = field(default_factory=list)
    run_phases: list[str] = field(default_factory=list)


class ExperimentEngine:
    def __init__(self, spec: ExperimentSpec,
                 runner: Callable[[ExperimentSpec, dict[str, str]], RunRecord] = run_once,
                 *, clock: Callable[[], float] = time.perf_counter):
        self.spec = spec
        self.runner = runner
        self.evaluations: dict[tuple, Evaluation] = {}
        self.budget_exhausted = False
        self.clock = clock
        self.started = None
        self.run_count = 0
        self.stop_reason = None
        self.constraint_rejections = 0
        self.space = None
        self.validation = None

    def _observe(self, configuration, deadline=None):
        self._check_budget()
        limit = self.spec.settings.timeout_seconds
        reason = None
        if self.spec.settings.max_seconds is not None:
            limit = min(limit, self.spec.settings.max_seconds-(self.clock()-self.started))
            reason = 'max_seconds'
        if deadline is not None:
            remaining = deadline-self.clock()
            if remaining < limit:
                limit = remaining
                reason = 'confirmation_max_seconds'
        if limit <= 0:
            self.stop_reason = reason
            self.budget_exhausted = True
            raise PlanningStopped(reason)
        run_spec = replace(self.spec, settings=replace(self.spec.settings, timeout_seconds=limit))
        record = self.runner(run_spec, configuration)
        self.run_count += 1
        if record.timed_out and limit < self.spec.settings.timeout_seconds:
            record.outcome = 'inconclusive'
            record.budget_limited_timeout = True
            self.stop_reason = reason
            self.budget_exhausted = True
        return record

    def _check_budget(self):
        reason = None
        if self.spec.settings.max_runs is not None and self.run_count >= self.spec.settings.max_runs:
            reason = "max_runs"
        if (self.spec.settings.max_seconds is not None and self.started is not None
                and self.clock() - self.started >= self.spec.settings.max_seconds):
            reason = "max_seconds"
        if reason:
            self.stop_reason = reason
            self.budget_exhausted = True
            raise PlanningStopped(self.stop_reason)

    def _evaluate(self, changes: tuple[tuple[str, str], ...], phase: str,
                  *, fresh: bool = False) -> str:
        key = tuple(sorted(changes))
        if not self.spec.allows({**self.spec.baseline, **dict(changes)}):
            self.constraint_rejections += 1
            return "invalid"
        if key not in self.evaluations:
            try:
                self._check_budget()
            except PlanningStopped:
                return "inconclusive"
            if len(self.evaluations) >= self.spec.settings.max_configurations:
                self.budget_exhausted = True
                self.stop_reason = "max_configurations"
                return "inconclusive"
            overrides = dict(key)
            configuration = {**self.spec.baseline, **overrides}
            self.evaluations[key] = Evaluation(f"c{len(self.evaluations) + 1:04}", overrides, configuration)
        evaluation = self.evaluations[key]
        if phase not in evaluation.phases:
            evaluation.phases.append(phase)
        if evaluation.runs and not fresh:
            return evaluation.status
        completed = 0
        for _ in range(self.spec.settings.repetitions):
            try:
                record = self._observe(evaluation.configuration)
            except PlanningStopped:
                break
            evaluation.runs.append(record)
            evaluation.run_phases.append(phase)
            completed += 1
        outcomes = {run.outcome for run in evaluation.runs}
        evaluation.status = (next(iter(outcomes)) if completed == self.spec.settings.repetitions
                             and len(outcomes) == 1 else "inconclusive")
        return evaluation.status

    def _discover(self):
        if self.spec.settings.strategy != "legacy":
            self.space = Space(self.spec, self._check_budget)
            for configuration in configurations(self.spec, self.space, self.evaluations, self._check_budget):
                changes = tuple((name, value) for name, value in configuration.items()
                                if value != self.spec.parameters[name].baseline)
                if self._evaluate(changes, "search") == "failure":
                    return changes
                if self.budget_exhausted:
                    return None
            return None
        failing = None
        for name, parameter in self.spec.parameters.items():
            for value in parameter.values:
                self._check_budget()
                changes = ((name, value),)
                result = self._evaluate(changes, "individual")
                if result == "failure" and failing is None:
                    failing = changes
                if self.budget_exhausted:
                    return failing
        if failing is not None:
            return failing
        names = tuple(self.spec.parameters)
        choices = [tuple(value for value in parameter.values + (parameter.baseline,)
                         if self.spec.allows({name: value}))
                   for name, parameter in self.spec.parameters.items()]
        if self.spec.constraints:
            self.space = Space(self.spec, self._check_budget)
        for index, values in enumerate(itertools.product(*choices)):
            self._check_budget()
            if self.spec.constraints and index >= 100_000:
                raise PlanningStopped("Legacy constraint planning step limit reached")
            changes = tuple((name, value) for name, value in zip(names, values)
                            if value != self.spec.parameters[name].baseline)
            if self._evaluate(changes, "search") == "failure":
                return changes
            if self.budget_exhausted:
                return None
        return None

    def run(self, *, allow_local_execution: bool = False) -> dict:
        if not allow_local_execution:
            raise ExecutionNotAllowed("Execution requires --allow-local-execution; use only disposable local targets")
        if self.started is not None:
            raise ValueError("An ExperimentEngine instance can only run once")
        started = self.clock()
        self.started = started
        created_at = datetime.now(timezone.utc).isoformat()
        total = 1
        for parameter in self.spec.parameters.values():
            total *= 1 + len(parameter.values)
        baseline_status = self._evaluate((), "baseline")
        diagnosis = None
        failing = None
        discovery_runs = self.run_count
        if baseline_status != "pass":
            status = "baseline_failure" if baseline_status == "failure" else "inconclusive"
            reason = ("The baseline already reproduces the failure; no change can be attributed."
                      if status == "baseline_failure" else "The baseline could not be established consistently.")
        else:
            try:
                failing = self._discover()
            except PlanningStopped as exc:
                self.stop_reason = self.stop_reason or str(exc)
                failing = None
            discovery_runs = self.run_count
            if failing is not None:
                reduced = ddmin(failing, lambda changes: self._evaluate(changes, "minimize"))
                if self.spec.confirmation.policy == 'holdout':
                    self.validation = holdout(self.spec, reduced.changes, self.evaluations,
                        self._observe, self.clock, self.run_count+1,
                        invocation_deadline=(self.started+self.spec.settings.max_seconds
                            if self.spec.settings.max_seconds is not None else None),
                        blocked_reason=self.stop_reason if self.budget_exhausted else None)
                    confirmed = self.validation['supported']
                    if self.validation['reason'] in ('max_runs','max_seconds','confirmation_max_runs','confirmation_max_seconds'):
                        self.stop_reason = self.validation['reason']
                        self.budget_exhausted = True
                    # A probability contrast is weaker than observed deterministic
                    # deletion minimality. Never infer that boolean from significance.
                    one_minimal = (confirmed and reduced.one_minimal
                        and not self.validation['excluded_invalid_deletions']
                        and all(run['outcome']==('failure' if arm['id']=='candidate' else 'pass')
                            for arm in self.validation['arms'] for run in arm['runs']))
                else:
                    candidate_status = self._evaluate(reduced.changes, "confirmation", fresh=True)
                    control_status = self._evaluate((), "baseline_confirmation", fresh=True)
                    confirmed = candidate_status == "failure" and control_status == "pass"
                    one_minimal = confirmed and reduced.one_minimal
                diagnosis = {
                    "changes": dict(reduced.changes),
                    "configuration": {**self.spec.baseline, **dict(reduced.changes)},
                    "confirmed": confirmed,
                    "one_minimal": one_minimal,
                    "evaluation_id": self.evaluations[tuple(sorted(reduced.changes))].id,
                }
                if not confirmed:
                    status = "inconclusive"
                    reason = "The candidate or baseline could not be confirmed consistently within the available evidence."
                elif self.validation:
                    status = 'failure_found'
                    reason = ('A fixed-sample candidate/control probability contrast is statistically supported '
                              'under the stated assumptions; observed 1-minimality: '+str(one_minimal)+'.')
                elif not reduced.one_minimal:
                    status = "inconclusive"
                    reason = "A failure reproduced, but invalid, inconclusive, or untested reductions prevent a minimality claim."
                else:
                    status = "failure_found"
                    reason = "A confirmed, 1-minimal failure-inducing change set was found."
            else:
                uncertain = any(e.status in ("inconclusive", "invalid") for e in self.evaluations.values())
                exact_total = self.space.exact_size if self.space is not None else total if not self.spec.constraints else None
                complete = exact_total is not None and len(self.evaluations) == exact_total
                if self.budget_exhausted or uncertain or not complete or self.stop_reason:
                    status = "inconclusive"
                    reason = (f"An experiment budget was exhausted ({self.stop_reason})." if self.budget_exhausted else
                              "The sampled evidence does not establish absence of the failure in the full valid space.")
                else:
                    status = "no_failure_found"
                    reason = "The failure predicate was not reproduced in the specified configuration space."
        return {
            "schema_version": 1, "causescope_version": __version__, "created_at": created_at,
            "duration_seconds": self.clock() - started,
            "specification": self.spec.evidence(), "status": status, "reason": reason,
            "diagnosis": diagnosis,
            "evidence": evidence_status(list(self.evaluations.values()), self.validation, diagnosis,
                                        self.spec.settings.repetitions),
            "validation": self.validation,
            "discovery": {"found": failing is not None,
                          "changes": dict(failing) if failing is not None else None,
                          "observations_until_stop": discovery_runs},
            "search": {"total_configurations": total, "tested_configurations": len(self.evaluations),
                       "search_complete": (len(self.evaluations) == self.space.exact_size if self.space is not None
                                           else len(self.evaluations) == total and not self.spec.constraints),
                       "valid_configurations": self.space.exact_size if self.space is not None else total if not self.spec.constraints else None,
                       "budget_exhausted": self.budget_exhausted, "stop_reason": self.stop_reason,
                       "strategy": self.spec.settings.strategy, "constraint_rejections": self.constraint_rejections,
                       "subprocess_runs": self.run_count,
                       "observations_by_phase": {**{phase: sum(e.run_phases.count(phase) for e in self.evaluations.values())
                           for phase in ("baseline", "individual", "search", "minimize", "confirmation", "baseline_confirmation")},
                           **({'holdout_confirmation':self.validation['observations']} if self.validation else {})}},
            "evaluations": [asdict(evaluation) for evaluation in self.evaluations.values()],
        }
