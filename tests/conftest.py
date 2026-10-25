from __future__ import annotations

import copy
import pytest

from causescope.runner import RunRecord
from causescope.spec import parse_spec


@pytest.fixture
def raw_spec():
    return {
        "version": 1, "name": "test", "scope": "local",
        "command": ["{python}", "-m", "causescope.sample_app"],
        "parameters": {
            "CAUSESCOPE_CACHE": {"baseline": "off", "values": ["on"]},
            "CAUSESCOPE_FAST": {"baseline": "off", "values": ["on"]},
        },
        "failure": {"exit_code": 23, "stderr_contains": "interaction bug"},
        "settings": {"timeout_seconds": 3, "repetitions": 3, "max_configurations": 100},
    }


@pytest.fixture
def make_spec(raw_spec, tmp_path):
    def make(*, command=None, parameters=None, failure=None, settings=None, name=None):
        raw = copy.deepcopy(raw_spec)
        for key, value in (("command", command), ("parameters", parameters), ("failure", failure), ("name", name)):
            if value is not None:
                raw[key] = value
        raw["settings"].update(settings or {})
        return parse_spec(raw, tmp_path / "experiment.yaml")
    return make


def observation(configuration: dict, outcome: str) -> RunRecord:
    return RunRecord(["fake-target"], "temporary", dict(configuration),
                     23 if outcome == "failure" else 0, "", "", 0.01,
                     False, None, False, False, outcome)


@pytest.fixture
def fake_runner():
    def factory(classify):
        counts = {}
        def run(spec, configuration):
            key = tuple(sorted(configuration.items()))
            counts[key] = counts.get(key, 0) + 1
            return observation(configuration, classify(configuration, counts[key]))
        run.counts = counts
        return run
    return factory
