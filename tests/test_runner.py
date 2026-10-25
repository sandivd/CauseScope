import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from causescope.runner import run_once
from causescope import runner as runner_module


def test_capture_exit_output_duration_environment_and_isolation(make_spec, monkeypatch):
    monkeypatch.setenv("CAUSESCOPE_INHERITED", "inherited")
    script = (
        "import os, pathlib, sys; "
        "print(os.environ['VALUE']); print(os.environ['CAUSESCOPE_INHERITED']); "
        "print(pathlib.Path.cwd()); print(pathlib.Path('marker').exists()); "
        "pathlib.Path('marker').write_text('created'); "
        "print('bug', file=sys.stderr); sys.exit(23)"
    )
    spec = make_spec(command=["{python}", "-c", script],
                     parameters={"VALUE": {"baseline": "base", "values": ["changed value"]}},
                     failure={"exit_code": 23, "stdout_contains": "changed value", "stderr_contains": "bug"})
    first = run_once(spec, {"VALUE": "changed value"})
    second = run_once(spec, {"VALUE": "changed value"})
    assert first.exit_code == 23
    assert first.outcome == "failure"
    assert first.stderr.strip() == "bug"
    assert first.stdout.splitlines()[0:2] == ["changed value", "inherited"]
    assert first.stdout.splitlines()[-1] == second.stdout.splitlines()[-1] == "False"
    assert first.working_directory != second.working_directory
    assert not Path(first.working_directory).exists()
    assert not Path(second.working_directory).exists()
    assert first.duration_seconds > 0
    assert first.configuration == {"VALUE": "changed value"}
    assert "CAUSESCOPE_INHERITED" not in first.configuration


def test_tokens_paths_with_spaces_no_shell_and_closed_stdin(make_spec, tmp_path):
    script_path = tmp_path / "target with spaces.py"
    script_path.write_text("import sys; print(sys.argv[1]); print(sys.argv[2]); print(repr(sys.stdin.read()))", encoding="utf-8")
    spec = make_spec(command=["{python}", "{spec_dir}/target with spaces.py", "{work_dir}", "a & echo injected"],
                     failure={"exit_code": 99})
    run = run_once(spec, spec.baseline)
    assert run.stdout.splitlines() == [run.working_directory, "a & echo injected", "''"]
    assert run.exit_code == 0
    assert run.outcome == "pass"


def test_timeout_retains_partial_output_and_is_unknown_by_default(make_spec):
    spec = make_spec(command=["{python}", "-c", "import time; print('started', flush=True); time.sleep(30)"],
                     failure={"exit_code": 23}, settings={"timeout_seconds": 0.3})
    run = run_once(spec, spec.baseline)
    assert run.timed_out
    assert run.stdout.strip() == "started"
    assert run.outcome == "inconclusive"
    assert run.exit_code is not None
    assert 0.3 <= run.duration_seconds < 8
    assert not Path(run.working_directory).exists()


def test_timeout_can_be_explicit_failure(make_spec):
    spec = make_spec(command=["{python}", "-c", "import time; time.sleep(30)"],
                     failure={"timeout": True}, settings={"timeout_seconds": 0.2})
    run = run_once(spec, spec.baseline)
    assert run.timed_out
    assert run.outcome == "failure"


def test_launch_error_is_evidence(make_spec, tmp_path):
    spec = make_spec(command=[str(tmp_path / "nonexistent.exe")])
    run = run_once(spec, spec.baseline)
    assert run.outcome == "inconclusive"
    assert run.exit_code is None
    assert "FileNotFoundError" in run.launch_error
    assert run.stdout == run.stderr == ""


def test_output_is_capped_and_missing_truncated_match_is_unknown(make_spec):
    spec = make_spec(command=["{python}", "-c", "import sys; print('x' * 10000 + 'needle'); print('y' * 10000, file=sys.stderr)"],
                     failure={"stdout_contains": "needle"}, settings={"max_output_bytes": 64})
    run = run_once(spec, spec.baseline)
    assert len(run.stdout) == len(run.stderr) == 64
    assert run.stdout_truncated and run.stderr_truncated
    assert run.outcome == "inconclusive"


def test_non_utf8_output_preserved_with_replacement(make_spec):
    spec = make_spec(command=["{python}", "-c", "import sys; sys.stdout.buffer.write(bytes([255]))"],
                     failure={"exit_code": 23})
    run = run_once(spec, spec.baseline)
    assert run.stdout == "\ufffd"
    assert run.outcome == "pass"


@pytest.mark.parametrize("cache,fast,code", [("off", "off", 0), ("on", "off", 0), ("off", "on", 0), ("on", "on", 23)])
def test_sample_bug_requires_both_options(make_spec, cache, fast, code):
    spec = make_spec()
    run = run_once(spec, {"CAUSESCOPE_CACHE": cache, "CAUSESCOPE_FAST": fast})
    assert run.exit_code == code
    assert run.outcome == ("failure" if code else "pass")
    assert ("interaction bug" in run.stderr) == (code == 23)


def test_timeout_stops_descendant(make_spec, tmp_path):
    marker = tmp_path / "child-survived"
    child = "import time, pathlib, sys; time.sleep(1.5); pathlib.Path(sys.argv[1]).write_text('survived')"
    parent = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
        "print('spawned', flush=True); time.sleep(30)"
    )
    spec = make_spec(command=["{python}", "-c", parent, child, str(marker)],
                     failure={"timeout": True}, settings={"timeout_seconds": 0.4})
    run = run_once(spec, spec.baseline)
    assert run.timed_out
    assert "spawned" in run.stdout
    # Let the descendant's scheduled write become observable if cleanup failed.
    import time
    time.sleep(1.6)
    assert not marker.exists()


def test_locked_temporary_directory_has_bounded_cleanup(make_spec, monkeypatch):
    original = runner_module.shutil.rmtree
    attempts = []
    def locked(path):
        attempts.append(path)
        raise PermissionError("directory is locked")
    monkeypatch.setattr(runner_module.shutil, "rmtree", locked)
    spec = make_spec(command=["{python}", "-c", "pass"], failure={"exit_code": 99})
    try:
        run = run_once(spec, spec.baseline)
        assert run.outcome == "inconclusive"
        assert "directory is locked" in run.cleanup_error
        assert len(attempts) == 5
    finally:
        if attempts:
            original(attempts[0])


@pytest.mark.skipif(runner_module.os.name != "nt", reason="Windows taskkill fallback")
@pytest.mark.parametrize("failure", ["denied", "missing", "timeout"])
def test_windows_termination_failure_uses_direct_fallback(monkeypatch, failure):
    calls = []
    process = SimpleNamespace(pid=1234, poll=lambda: None,
                              kill=lambda: calls.append("kill"), wait=lambda: calls.append("wait"))
    def taskkill(*args, **kwargs):
        if failure == "missing":
            raise FileNotFoundError("taskkill missing")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("taskkill", 5)
        return SimpleNamespace(returncode=1, stderr=b"Access denied")
    monkeypatch.setattr(runner_module.subprocess, "run", taskkill)
    error = runner_module._stop_process_tree(process)
    assert "Process-tree termination failed" in error
    assert calls == ["kill", "wait"]


def test_termination_error_is_inconclusive_even_for_timeout_predicate(make_spec, monkeypatch):
    real_stop = runner_module._stop_process_tree
    def stop_with_error(process):
        real_stop(process)
        return "tree cleanup failed"
    monkeypatch.setattr(runner_module, "_stop_process_tree", stop_with_error)
    spec = make_spec(command=["{python}", "-c", "import time; time.sleep(30)"],
                     failure={"timeout": True}, settings={"timeout_seconds": 0.2})
    run = run_once(spec, spec.baseline)
    assert run.timed_out and run.outcome == "inconclusive"
    assert run.termination_error == "tree cleanup failed"


def test_posix_process_group_cleanup(monkeypatch):
    calls = []
    fake_os = SimpleNamespace(name="posix", killpg=lambda pid, sig: calls.append((pid, sig)))
    monkeypatch.setattr(runner_module, "os", fake_os)
    monkeypatch.setattr(runner_module, "signal", SimpleNamespace(SIGKILL=9))
    process = SimpleNamespace(pid=1234, poll=lambda: 0, wait=lambda: calls.append("wait"))
    assert runner_module._stop_process_tree(process) is None
    assert calls == [(1234, 9), "wait"]


@pytest.mark.parametrize("exception", [ProcessLookupError, PermissionError])
def test_posix_group_cleanup_failure_does_not_prevent_direct_cleanup(monkeypatch, exception):
    calls = []
    def killpg(*args):
        raise exception("process group unavailable")
    monkeypatch.setattr(runner_module, "os", SimpleNamespace(name="posix", killpg=killpg))
    monkeypatch.setattr(runner_module, "signal", SimpleNamespace(SIGKILL=9))
    process = SimpleNamespace(pid=1234, poll=lambda: None,
                              kill=lambda: calls.append("kill"), wait=lambda: calls.append("wait"))
    error = runner_module._stop_process_tree(process)
    assert calls == ["kill", "wait"]
    assert (error is None) == (exception is ProcessLookupError)


def test_expansion_does_not_reinterpret_braces_in_resolved_paths(make_spec, tmp_path):
    from dataclasses import replace
    directory = tmp_path / "literal {work_dir}"
    directory.mkdir()
    script = directory / "target.py"
    script.write_text("print('ok')", encoding="utf-8")
    spec = replace(make_spec(command=["{python}", "{spec_dir}/target.py"], failure={"exit_code": 99}),
                   source=directory / "experiment.yaml")
    result = run_once(spec, spec.baseline)
    assert result.exit_code == 0
    assert result.stdout.strip() == "ok"


def test_cleanup_removes_read_only_target_artifacts(make_spec):
    spec = make_spec(command=["{python}", "-c",
        "import pathlib, os, stat; p=pathlib.Path('readonly.txt'); p.write_text('artifact'); os.chmod(p, stat.S_IREAD)"],
        failure={"exit_code": 99})
    run = run_once(spec, spec.baseline)
    assert run.exit_code == 0
    assert run.cleanup_error is None
    assert run.outcome == "pass"
    assert not Path(run.working_directory).exists()


def test_real_inconsistent_target_is_inconclusive(make_spec, tmp_path):
    from causescope.engine import ExperimentEngine
    script = tmp_path / "alternating.py"
    script.write_text(
        "import os, pathlib, sys\n"
        "if os.environ['VALUE'] == '0': sys.exit(0)\n"
        "counter = pathlib.Path(sys.argv[1])\n"
        "n = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(n))\n"
        "sys.exit(23 if n % 2 else 0)\n", encoding="utf-8")
    spec = make_spec(command=["{python}", str(script), str(tmp_path / "counter")],
                     parameters={"VALUE": {"baseline": "0", "values": ["1"]}},
                     failure={"exit_code": 23})
    result = ExperimentEngine(spec).run(allow_local_execution=True)
    assert result["status"] == "inconclusive"
    assert result["diagnosis"] is None
    evaluation = next(e for e in result["evaluations"] if e["changes"])
    assert [run["exit_code"] for run in evaluation["runs"]] == [23, 0, 23]
    assert evaluation["status"] == "inconclusive"
