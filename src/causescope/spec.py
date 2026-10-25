"""Strict, deliberately small YAML specification format."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


class SpecError(ValueError):
    """An invalid experiment specification."""


class _UniqueLoader(yaml.SafeLoader):
    """Reject duplicate keys rather than silently replacing safety settings."""


def _mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict:
    result: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise SpecError("YAML mapping keys must be strings")
        if key in result:
            raise SpecError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


@dataclass(frozen=True)
class Parameter:
    baseline: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class FailurePredicate:
    exit_code: int | None = None
    stdout_contains: str | None = None
    stderr_contains: str | None = None
    timeout: bool | None = None

    def evaluate(
        self, *, exit_code: int | None, stdout: str, stderr: str,
        timed_out: bool, launch_error: str | None = None,
        stdout_truncated: bool = False, stderr_truncated: bool = False,
    ) -> bool | None:
        """Return None when incomplete evidence cannot settle the predicate."""
        if launch_error or (timed_out and self.timeout is None):
            return None
        conditions: list[bool | None] = []
        if self.timeout is not None:
            conditions.append(timed_out == self.timeout)
        if self.exit_code is not None:
            conditions.append(exit_code == self.exit_code)
        for needle, haystack, truncated in (
            (self.stdout_contains, stdout, stdout_truncated),
            (self.stderr_contains, stderr, stderr_truncated),
        ):
            if needle is not None:
                conditions.append(True if needle in haystack else None if truncated else False)
        if False in conditions:
            return False
        return None if None in conditions else True


@dataclass(frozen=True)
class Settings:
    timeout_seconds: float = 5.0
    repetitions: int = 3
    max_configurations: int = 100
    max_output_bytes: int = 1_048_576
    strategy: str = "legacy"
    seed: int = 0
    max_runs: int | None = None
    max_seconds: float | None = None
    candidate_pool: int = 48


@dataclass(frozen=True)
class ConfirmationSettings:
    policy: str = "repeat"
    samples_per_arm: int = 64
    alpha: float = 0.05
    min_failure_probability: float = 0.9
    min_effect: float = 0.2
    max_runs: int = 512
    max_seconds: float = 60.0
    seed: int = 0


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    command: tuple[str, ...]
    parameters: dict[str, Parameter]
    failure: FailurePredicate
    settings: Settings
    source: Path
    constraints: tuple[dict[str, str], ...] = ()
    invalid_exit_codes: tuple[int, ...] = ()
    confirmation: ConfirmationSettings = ConfirmationSettings()

    def allows(self, configuration: dict[str, str]) -> bool:
        """Partial assignments are rejected only when a forbidden tuple is complete."""
        return not any(all(configuration.get(name) == value for name, value in forbidden.items())
                       for forbidden in self.constraints)

    @property
    def baseline(self) -> dict[str, str]:
        return {name: parameter.baseline for name, parameter in self.parameters.items()}

    def evidence(self) -> dict[str, Any]:
        return {
            "version": 1, "name": self.name, "scope": "local",
            "command": list(self.command),
            "parameters": {name: asdict(value) for name, value in self.parameters.items()},
            "failure": asdict(self.failure), "settings": asdict(self.settings),
            "source": str(self.source),
            "constraints": [{"forbid": item} for item in self.constraints],
            "invalid_exit_codes": list(self.invalid_exit_codes),
            "confirmation": asdict(self.confirmation),
        }


def _fields(value: Any, allowed: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise SpecError(f"{label} must be a mapping with string keys")
    unknown = value.keys() - allowed
    missing = required - value.keys()
    if unknown:
        raise SpecError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")
    if missing:
        raise SpecError(f"Missing {label} fields: {', '.join(sorted(missing))}")
    return value


def _scalar(value: Any, label: str) -> str:
    if type(value) not in (str, int, float, bool):
        raise SpecError(f"{label} must be a string, number, or boolean")
    if isinstance(value, float) and not math.isfinite(value):
        raise SpecError(f"{label} must be finite")
    result = str(value).lower() if isinstance(value, bool) else str(value)
    if "\x00" in result:
        raise SpecError(f"{label} must not contain NUL characters")
    return result


def load_spec(path: str | Path) -> ExperimentSpec:
    source = Path(path).resolve()
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueLoader)
    except (OSError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        raise SpecError(f"Cannot read YAML specification {source}: {exc}") from exc
    return parse_spec(raw, source)


def parse_spec(raw: Any, source: Path) -> ExperimentSpec:
    data = _fields(raw, {"version", "name", "scope", "command", "parameters", "failure", "settings", "constraints", "invalid_exit_codes", "confirmation"},
                   {"version", "name", "scope", "command", "parameters", "failure"}, "specification")
    if type(data["version"]) is not int or data["version"] != 1:
        raise SpecError("version must be 1")
    if data["scope"] != "local":
        raise SpecError("scope must be 'local'; production targets are unsupported")
    if not isinstance(data["name"], str) or not data["name"].strip():
        raise SpecError("name must be a nonempty string")
    command = data["command"]
    if (not isinstance(command, list) or not command
            or not all(isinstance(arg, str) and "\x00" not in arg for arg in command)
            or not command[0].strip()):
        raise SpecError("command must be a nonempty argv list of strings, without NUL characters")
    parameters = data["parameters"]
    if not isinstance(parameters, dict) or not parameters:
        raise SpecError("parameters must be a nonempty mapping")
    parsed: dict[str, Parameter] = {}
    for name, value in parameters.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise SpecError("Parameter names must be valid environment variable names")
        if name.casefold() in {existing.casefold() for existing in parsed}:
            raise SpecError("Parameter names must be distinct ignoring case (Windows compatibility)")
        item = _fields(value, {"baseline", "values"}, {"baseline", "values"}, f"parameter {name}")
        baseline = _scalar(item["baseline"], f"{name}.baseline")
        if not isinstance(item["values"], list) or not item["values"]:
            raise SpecError(f"{name}.values must be a nonempty list")
        values = tuple(_scalar(v, f"{name}.values") for v in item["values"])
        if baseline in values or len(set(values)) != len(values):
            raise SpecError(f"{name}.values must be unique and different from the baseline")
        parsed[name] = Parameter(baseline, values)
    failure = _fields(data["failure"], {"exit_code", "stdout_contains", "stderr_contains", "timeout"}, set(), "failure")
    if not failure:
        raise SpecError("failure must contain at least one predicate")
    if "exit_code" in failure and type(failure["exit_code"]) is not int:
        raise SpecError("failure.exit_code must be an integer")
    if "timeout" in failure and type(failure["timeout"]) is not bool:
        raise SpecError("failure.timeout must be a boolean")
    for field in ("stdout_contains", "stderr_contains"):
        if field in failure and (not isinstance(failure[field], str) or not failure[field]):
            raise SpecError(f"failure.{field} must be a nonempty string")
    settings = _fields(data.get("settings", {}), set(Settings.__dataclass_fields__), set(), "settings")
    defaults = asdict(Settings())
    defaults.update(settings)
    timeout = defaults["timeout_seconds"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise SpecError("settings.timeout_seconds must be a finite positive number")
    for field, minimum in (("repetitions", 2), ("max_configurations", 1), ("max_output_bytes", 1)):
        if type(defaults[field]) is not int or defaults[field] < minimum:
            raise SpecError(f"settings.{field} must be an integer >= {minimum}")
    if defaults["strategy"] not in ("legacy", "adaptive", "random", "pairwise", "one_at_a_time", "brute_force"):
        raise SpecError("Unknown settings.strategy")
    for field, minimum in (("seed", 0), ("candidate_pool", 8)):
        if type(defaults[field]) is not int or defaults[field] < minimum:
            raise SpecError(f"settings.{field} must be an integer >= {minimum}")
    if defaults["max_runs"] is not None and (type(defaults["max_runs"]) is not int or defaults["max_runs"] < 1):
        raise SpecError("settings.max_runs must be an integer >= 1 or null")
    seconds = defaults["max_seconds"]
    if seconds is not None and (type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0):
        raise SpecError("settings.max_seconds must be a finite positive number or null")
    raw_constraints = data.get("constraints", [])
    if not isinstance(raw_constraints, list):
        raise SpecError("constraints must be a list of forbidden tuples")
    constraints = []
    for item in raw_constraints:
        item = _fields(item, {"forbid"}, {"forbid"}, "constraint")
        forbidden = item["forbid"]
        if not isinstance(forbidden, dict) or not forbidden:
            raise SpecError("constraint.forbid must be a nonempty parameter/value mapping")
        converted = {}
        for name, value in forbidden.items():
            if name not in parsed:
                raise SpecError(f"Unknown constraint parameter: {name}")
            converted[name] = _scalar(value, f"constraint.{name}")
            if converted[name] not in (parsed[name].baseline,) + parsed[name].values:
                raise SpecError(f"Unknown constraint value for {name}")
        constraints.append(converted)
    invalid = data.get("invalid_exit_codes", [])
    if not isinstance(invalid, list) or not all(type(code) is int for code in invalid) or len(set(invalid)) != len(invalid):
        raise SpecError("invalid_exit_codes must be a list of unique integers")
    if failure.get("exit_code") in invalid:
        raise SpecError("A failure exit code cannot also denote invalid configuration")
    baseline = {name: parameter.baseline for name, parameter in parsed.items()}
    if any(all(baseline[name] == value for name, value in item.items()) for item in constraints):
        raise SpecError("Constraints must allow the baseline")
    confirmation = asdict(ConfirmationSettings())
    confirmation.update(_fields(data.get("confirmation", {}), set(confirmation), set(), "confirmation"))
    if confirmation['policy'] not in ('repeat', 'holdout'):
        raise SpecError('confirmation.policy must be repeat or holdout')
    for field, minimum in (('samples_per_arm', 2), ('max_runs', 1), ('seed', 0)):
        if type(confirmation[field]) is not int or confirmation[field] < minimum:
            raise SpecError(f'confirmation.{field} must be an integer >= {minimum}')
    if confirmation['samples_per_arm'] > 1000:
        raise SpecError('confirmation.samples_per_arm must be <= 1000')
    for field in ('alpha', 'min_failure_probability', 'min_effect'):
        value = confirmation[field]
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 1:
            raise SpecError(f'confirmation.{field} must be finite and strictly between 0 and 1')
    if confirmation['alpha'] < 1e-12:
        raise SpecError('confirmation.alpha must be >= 1e-12 (numerical limit)')
    seconds = confirmation['max_seconds']
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise SpecError('confirmation.max_seconds must be a finite positive number')
    return ExperimentSpec(data["name"], tuple(command), parsed, FailurePredicate(**failure),
                          Settings(**defaults), source.resolve(), tuple(constraints), tuple(invalid),
                          ConfirmationSettings(**confirmation))
