import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from causescope.engine import ExecutionNotAllowed, ExperimentEngine
from benchmarks.git_eol import adapter, compare
from benchmarks.git_eol.audit import audit_result
from benchmarks.git_eol.baselines import run_baseline
from benchmarks.git_eol.compare import powershell_command, run_comparison


def test_comparison_strategies_have_explicit_opt_in(make_spec):
    with pytest.raises(ExecutionNotAllowed):
        run_baseline(make_spec(), "brute_force")
    with pytest.raises(ValueError, match="allow-local-execution"):
        run_comparison(Path("never-created"), 3)


def test_brute_force_enumerates_and_confirms_without_search_shortcuts(make_spec, fake_runner):
    runner = fake_runner(lambda c, n: "failure" if all(v == "on" for v in c.values()) else "pass")
    result = run_baseline(make_spec(), "brute_force", allow_local_execution=True, runner=runner)
    assert result["search"]["tested_configurations"] == 4
    assert result["search"]["subprocess_runs"] == 18
    assert result["search"]["search_complete"]
    assert result["global_minimum_within_specification"]
    assert result["diagnosis"]["confirmed"]
    assert len(result["diagnosis"]["changes"]) == 2


def test_one_at_a_time_misses_interactions(make_spec, fake_runner):
    runner = fake_runner(lambda c, n: "failure" if all(v == "on" for v in c.values()) else "pass")
    result = run_baseline(make_spec(), "one_at_a_time", allow_local_execution=True, runner=runner)
    assert result["search"]["tested_configurations"] == 3
    assert result["search"]["subprocess_runs"] == 9
    assert not result["search"]["search_complete"]
    assert result["status"] == "no_failure_found"
    assert result["diagnosis"] is None


def test_brute_force_flakiness_prevents_global_claim(make_spec, fake_runner):
    runner = fake_runner(lambda c, n: "failure" if all(v == "on" for v in c.values()) and n % 2 else "pass")
    result = run_baseline(make_spec(), "brute_force", allow_local_execution=True, runner=runner)
    assert result["status"] == "inconclusive"
    assert not result["global_minimum_within_specification"]


def test_comparison_control_drift_is_inconclusive(make_spec, fake_runner):
    def classify(c, n):
        if all(v == "off" for v in c.values()) and n > 3:
            return "failure"
        return "failure" if all(v == "on" for v in c.values()) else "pass"
    result = run_baseline(make_spec(), "brute_force", allow_local_execution=True, runner=fake_runner(classify))
    assert result["status"] == "inconclusive"
    assert not result["diagnosis"]["confirmed"]
    assert not result["global_minimum_within_specification"]


def test_brute_force_honors_configuration_budget(make_spec, fake_runner):
    with pytest.raises(ValueError, match="budget"):
        run_baseline(make_spec(settings={"max_configurations": 1}), "brute_force",
                     allow_local_execution=True, runner=fake_runner(lambda c, n: "pass"))


def test_auditor_rejects_a_predicate_pass_for_unrelated_error(make_spec, fake_runner):
    result = ExperimentEngine(make_spec(), fake_runner(lambda c, n: "pass")).run(allow_local_execution=True)
    result["evaluations"][0]["runs"][0]["exit_code"] = 70
    audit = audit_result(result)
    assert not audit["passed"]
    assert any("unexpected exit status 70" in error for error in audit["errors"])
    assert any("metadata" in error for error in audit["errors"])


def test_environment_isolation_removes_all_inherited_git_controls(tmp_path, monkeypatch):
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_CONFIG_VALUE_0"):
        monkeypatch.setenv(key, "poison")
    environment = adapter.clean_environment(tmp_path)
    assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_VALUE_0"} & environment.keys()
    assert environment["GIT_CONFIG_COUNT"] == "0"
    assert environment["HOME"] == environment["USERPROFILE"] == str(tmp_path)
    assert environment["XDG_CONFIG_HOME"] == str(tmp_path / "xdg")
    assert environment["GIT_ATTR_NOSYSTEM"] == environment["GIT_CONFIG_NOSYSTEM"] == "1"


def test_unsupported_platform_is_explicit(monkeypatch):
    monkeypatch.setattr(adapter, "sys", SimpleNamespace(platform="linux"))
    with pytest.raises(RuntimeError, match="Windows x86-64"):
        adapter.verify_target()


def test_pin_mismatch_is_not_a_healthy_observation(monkeypatch):
    monkeypatch.setattr(adapter, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(adapter.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(adapter, "sha256", lambda p: "mismatched-binary")
    with pytest.raises(RuntimeError, match="Binary SHA256 mismatch"):
        adapter.verify_target()


def test_native_configuration_does_not_accept_unbenchmarked_values():
    with pytest.raises(ValueError, match="Unsupported"):
        adapter.native_configuration({f"GIT_CONFIG_VALUE_{i}": "input" for i in range(4)})


def test_powershell_reproduction_quotes_paths_and_literal_apostrophes():
    assert powershell_command(["a b/python.exe", "it's literal"]) == "& 'a b/python.exe' 'it''s literal'"


def test_comparison_refuses_overwriting_evidence(tmp_path):
    with pytest.raises(FileExistsError, match="fresh"):
        run_comparison(tmp_path, 3, allow_local_execution=True)


def test_comparison_rejects_empty_campaign(tmp_path):
    with pytest.raises(ValueError, match="positive"):
        run_comparison(tmp_path / "new", 0, allow_local_execution=True)


def test_provenance_uses_pinned_git_and_normalized_safe_directory(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="measured-revision\n", stderr="")
    monkeypatch.setattr(compare.subprocess, "run", run)
    result = compare.source_provenance("pinned-git.exe")
    assert calls == [["pinned-git.exe", "-c", f"safe.directory={adapter.ROOT.as_posix()}", "rev-parse", "HEAD"]]
    assert result["git_revision"] == "measured-revision"
    assert result["git_error"] is None
    assert result["files_sha256"]


def test_missing_repository_metadata_is_recorded_without_losing_hashes(monkeypatch):
    monkeypatch.setattr(compare.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=128, stdout="", stderr="not a repository"))
    result = compare.source_provenance("pinned-git.exe")
    assert result["git_revision"] is None
    assert result["git_error"] == "not a repository"
    assert result["files_sha256"]
