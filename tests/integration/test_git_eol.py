"""Opt in with CAUSESCOPE_RUN_GIT_INTEGRATION=1 after verified setup."""

import copy
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from causescope.engine import ExperimentEngine
from causescope.runner import run_once
from causescope.spec import load_spec
from benchmarks.git_eol.adapter import ROOT, verify_target
from benchmarks.git_eol.audit import audit_result
from benchmarks.git_eol.baselines import SPEC_PATH, run_baseline
from benchmarks.git_eol.compare import assess_trial

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pinned_spec():
    if os.environ.get("CAUSESCOPE_RUN_GIT_INTEGRATION") != "1":
        pytest.skip("Run verified setup, then set CAUSESCOPE_RUN_GIT_INTEGRATION=1")
    # Once opted in, missing binaries, unsupported platforms, or wrong pins FAIL.
    verify_target(check_archive=True)
    return load_spec(SPEC_PATH)


@pytest.fixture(scope="module")
def real_results(pinned_spec):
    return {
        "causescope": ExperimentEngine(pinned_spec).run(allow_local_execution=True),
        "brute_force": run_baseline(pinned_spec, "brute_force", allow_local_execution=True),
        "one_at_a_time": run_baseline(pinned_spec, "one_at_a_time", allow_local_execution=True),
    }


@pytest.mark.parametrize("autocrlf,safecrlf,expected", [("false", "false", 0), ("true", "false", 0),
                                                       ("false", "true", 0), ("true", "true", 128)])
def test_real_git_four_cell_interaction(pinned_spec, autocrlf, safecrlf, expected):
    configuration = {**pinned_spec.baseline, "GIT_CONFIG_VALUE_0": autocrlf, "GIT_CONFIG_VALUE_1": safecrlf}
    run = run_once(pinned_spec, configuration)
    assert run.exit_code == expected
    assert run.outcome == ("failure" if expected else "pass")
    assert run.cleanup_error is None


def test_real_strategies_counts_and_minimality_agree(real_results):
    checks = assess_trial(real_results)
    assert checks["causescope_confirmed_expected_pair"]
    assert checks["brute_force_exhaustive_and_conclusive"]
    assert checks["oat_did_not_find_interaction"]
    assert checks["overlapping_observations_agree"]
    assert checks["observed_minimum_change_count"] == 2
    assert checks["failing_configurations_in_full_space"] == 4
    for method, configs, runs in (("causescope", 8, 30), ("brute_force", 16, 54), ("one_at_a_time", 5, 15)):
        result = real_results[method]
        assert result["search"]["tested_configurations"] == configs
        assert result["search"]["subprocess_runs"] == runs


def test_all_real_evidence_is_audited_and_isolated(real_results):
    directories = set()
    for result in real_results.values():
        audit = audit_result(result)
        assert audit["passed"], audit["errors"]
        assert audit["git_invocations"] == audit["observations"] * 3
        assert not directories.intersection(audit["working_directories"])
        directories.update(audit["working_directories"])
    assert len(directories) == 99


def test_auditor_rejects_tampered_real_metadata(real_results):
    corrupted = copy.deepcopy(real_results["causescope"])
    corrupted["evaluations"][0]["runs"][0]["stdout"] = corrupted["evaluations"][0]["runs"][0]["stdout"].replace(
        "e49c81e2d2f84e259d40e2fb8192f3bcd198b355184845d76d8f58807d0d78ee", "bad-fixture-hash")
    audit = audit_result(corrupted)
    assert not audit["passed"]
    assert any("fixture mismatch" in error for error in audit["errors"])


def test_real_target_cli_exports_confirmed_diagnosis(pinned_spec, tmp_path):
    output = tmp_path / "cli-results"
    command = [sys.executable, "-m", "causescope", "run", str(SPEC_PATH), "--allow-local-execution", "--output", str(output)]
    completed = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "failure_found"
    assert evidence["diagnosis"]["changes"] == {"GIT_CONFIG_VALUE_0": "true", "GIT_CONFIG_VALUE_1": "true"}
    assert audit_result(evidence)["passed"]
    assert "LF would be replaced by CRLF" in (output / "report.html").read_text(encoding="utf-8")


def test_real_target_does_not_use_inherited_repository_or_user_settings(pinned_spec, tmp_path, monkeypatch):
    home = tmp_path / "host-home"
    home.mkdir()
    (home / ".gitconfig").write_text("[core]\n autocrlf = invalid\n", encoding="utf-8")
    index = tmp_path / "host-index"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home))
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "host-repo"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path))
    monkeypatch.setenv("GIT_INDEX_FILE", str(index))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "invalid inherited configuration")
    run = run_once(pinned_spec, pinned_spec.baseline)
    assert run.exit_code == 0 and run.outcome == "pass"
    assert run.stderr == ""
    assert not index.exists()
    assert not (tmp_path / "host-repo").exists()


def test_unbenchmarked_value_is_reported_unsupported(pinned_spec):
    configuration = {**pinned_spec.baseline, "GIT_CONFIG_VALUE_0": "input"}
    run = run_once(pinned_spec, configuration)
    assert run.exit_code == 70
    assert "unsupported" in run.stderr.lower()
    # CauseScope's narrow predicate alone means "predicate absent", not health.
    assert run.outcome == "pass"
    result = {"specification": pinned_spec.evidence(), "search": {"subprocess_runs": 1},
        "evaluations": [{"id": "unsupported", "status": "pass", "configuration": configuration, "runs": [asdict(run)]}]}
    audit = audit_result(result)
    assert not audit["passed"]
    assert any("unexpected exit status 70" in error for error in audit["errors"])
