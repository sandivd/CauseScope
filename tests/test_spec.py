import math

import pytest
import yaml

from causescope.spec import FailurePredicate, SpecError, load_spec, parse_spec


def test_defaults_and_scalar_conversion(raw_spec, tmp_path):
    raw_spec.pop("settings")
    raw_spec["parameters"] = {"VALUE": {"baseline": False, "values": [True, 1, 1.5, "text"]}}
    spec = parse_spec(raw_spec, tmp_path / "spec.yaml")
    assert spec.baseline == {"VALUE": "false"}
    assert spec.parameters["VALUE"].values == ("true", "1", "1.5", "text")
    assert spec.settings.timeout_seconds == 5
    assert spec.settings.repetitions == 3
    assert spec.settings.max_configurations == 100
    assert spec.settings.max_output_bytes == 1048576


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("scope", "production"),
    ("name", ""), ("name", 1), ("command", "echo hi"), ("command", []),
    ("command", [""]), ("command", ["echo", 1]), ("command", ["echo\x00"]),
    ("parameters", {}), ("parameters", []), ("failure", {}), ("failure", []),
    ("failure", {"exit_code": True}), ("failure", {"timeout": "yes"}),
    ("failure", {"stdout_contains": ""}), ("failure", {"stderr_contains": None}),
    ("failure", {"regex": "bad"}), ("settings", {"timeout_seconds": 0}),
    ("settings", {"timeout_seconds": -1}), ("settings", {"timeout_seconds": math.inf}),
    ("settings", {"timeout_seconds": math.nan}), ("settings", {"timeout_seconds": True}),
    ("settings", {"repetitions": 1}), ("settings", {"repetitions": 2.5}),
    ("settings", {"max_configurations": 0}), ("settings", {"max_configurations": True}),
    ("settings", {"max_output_bytes": 0}), ("settings", {"timeuot_seconds": 3}),
    ("unexpected", True),
])
def test_reject_invalid_fields(raw_spec, tmp_path, field, value):
    raw_spec[field] = value
    with pytest.raises(SpecError):
        parse_spec(raw_spec, tmp_path / "spec.yaml")


@pytest.mark.parametrize("parameters", [
    {"BAD-NAME": {"baseline": "a", "values": ["b"]}},
    {"X": {"baseline": "a", "values": []}},
    {"X": {"baseline": "a", "values": "b"}},
    {"X": {"baseline": "a", "values": ["a"]}},
    {"X": {"baseline": "a", "values": [1, "1"]}},
    {"X": {"baseline": None, "values": ["a"]}},
    {"X": {"baseline": "a", "values": [[]]}},
    {"X": {"baseline": math.inf, "values": ["a"]}},
    {"X": {"baseline": "a\x00", "values": ["b"]}},
    {"X": {"values": ["a"]}},
    {"X": {"baseline": "a", "values": ["b"], "value": "b"}},
    {"X": {"baseline": 0, "values": [1]}, "x": {"baseline": 0, "values": [1]}},
])
def test_reject_invalid_parameters(raw_spec, tmp_path, parameters):
    raw_spec["parameters"] = parameters
    with pytest.raises(SpecError):
        parse_spec(raw_spec, tmp_path / "spec.yaml")


def test_missing_required_field_and_nonmapping(raw_spec, tmp_path):
    raw_spec.pop("scope")
    for value in (raw_spec, None, [], {1: "bad"}):
        with pytest.raises(SpecError):
            parse_spec(value, tmp_path / "spec.yaml")


@pytest.mark.parametrize("text", [
    "scope: local\nscope: production\n", "a: [broken", "1: value",
    "!!python/object/apply:os.system ['echo unsafe']",
    "failure:\n  exit_code: 1\n  exit_code: 2\n",
])
def test_yaml_is_safe_and_rejects_duplicates(tmp_path, text):
    path = tmp_path / "invalid.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SpecError):
        load_spec(path)


def test_yaml_round_trip(raw_spec, tmp_path):
    path = tmp_path / "experiment.yaml"
    path.write_text(yaml.safe_dump(raw_spec), encoding="utf-8")
    assert load_spec(path).baseline == {"CAUSESCOPE_CACHE": "off", "CAUSESCOPE_FAST": "off"}


def test_unreadable_and_invalid_encoding(tmp_path):
    with pytest.raises(SpecError, match="Cannot read YAML"):
        load_spec(tmp_path / "missing.yaml")
    path = tmp_path / "invalid.yaml"
    path.write_bytes(b"\xff")
    with pytest.raises(SpecError, match="Cannot read YAML"):
        load_spec(path)


@pytest.mark.parametrize("predicate,changes,expected", [
    (FailurePredicate(exit_code=23), {}, True),
    (FailurePredicate(exit_code=1), {}, False),
    (FailurePredicate(stdout_contains="hello", stderr_contains="bug"), {}, True),
    (FailurePredicate(exit_code=23, stderr_contains="absent"), {}, False),
    (FailurePredicate(exit_code=23), {"timed_out": True}, None),
    (FailurePredicate(timeout=True), {"timed_out": True}, True),
    (FailurePredicate(timeout=True), {}, False),
    (FailurePredicate(timeout=False), {"timed_out": True}, False),
    (FailurePredicate(exit_code=23), {"launch_error": "missing executable"}, None),
    (FailurePredicate(stdout_contains="absent"), {"stdout_truncated": True}, None),
    (FailurePredicate(stdout_contains="hello"), {"stdout_truncated": True}, True),
    (FailurePredicate(stdout_contains="absent", exit_code=1), {"stdout_truncated": True}, False),
    (FailurePredicate(stderr_contains="absent"), {"stderr_truncated": True}, None),
])
def test_predicate_three_valued_and_semantics(predicate, changes, expected):
    observation = dict(exit_code=23, stdout="hello", stderr="bug", timed_out=False)
    observation.update(changes)
    assert predicate.evaluate(**observation) is expected


def test_literal_braces_in_argv(make_spec):
    assert make_spec(command=["{python}", "-c", "print({'x': 1})"]).command[-1] == "print({'x': 1})"
