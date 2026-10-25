"""Execute one observation in a disposable working directory."""

from __future__ import annotations

import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .spec import ExperimentSpec


@dataclass
class RunRecord:
    command: list[str]
    working_directory: str
    configuration: dict[str, str]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    launch_error: str | None
    stdout_truncated: bool
    stderr_truncated: bool
    outcome: str
    termination_error: str | None = None
    cleanup_error: str | None = None
    timeout_seconds: float | None = None
    budget_limited_timeout: bool = False


def _stop_process_tree(process: subprocess.Popen) -> str | None:
    error = None
    if os.name == "nt" and hasattr(process, 'terminate_tree'):
        try:
            process.terminate_tree()
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"Process-tree termination failed: {exc}"
    elif os.name == "nt":
        # An absolute path avoids running a target-controlled taskkill from PATH.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            result = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=5)
            if result.returncode:
                error = f"Process-tree termination failed: {result.stderr.decode('utf-8', errors='replace').strip()}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = f"Process-tree termination failed: {exc}"
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as exc:
            error = f"Process-group termination failed: {exc}"
    if process.poll() is None:
        process.kill()
    process.wait()
    return error


@dataclass
class _Workspace:
    path: Path
    cleanup_error: str | None = None


def _clear_windows_readonly_files(root: Path) -> None:
    """Git and other targets create read-only artifacts that Windows cannot unlink."""
    if os.name != "nt":
        return
    boundary = root.resolve()
    for directory, _, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            try:
                # Never change a symlink target or a file resolved outside our tree.
                if path.is_symlink() or not path.resolve().is_relative_to(boundary):
                    continue
                mode = path.stat().st_mode
                if not mode & stat.S_IWRITE:
                    path.chmod(mode | stat.S_IWRITE)
            except FileNotFoundError:
                continue


@contextmanager
def _workspace():
    workspace = _Workspace(Path(tempfile.mkdtemp(prefix="causescope-")))
    try:
        yield workspace
    finally:
        # Bounded retries avoid recursive TemporaryDirectory cleanup on older
        # Python versions when a Windows descendant still holds its cwd open.
        for attempt in range(5):
            try:
                _clear_windows_readonly_files(workspace.path)
                shutil.rmtree(workspace.path)
                break
            except FileNotFoundError:
                break
            except OSError as exc:
                if attempt == 4:
                    workspace.cleanup_error = f"Temporary-directory cleanup failed: {exc}"
                else:
                    time.sleep(0.05)


def _read_output(path: Path, limit: int) -> tuple[str, bool]:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    return data[:limit].decode("utf-8", errors="replace"), len(data) > limit


def run_once(spec: ExperimentSpec, configuration: dict[str, str]) -> RunRecord:
    with _workspace() as workspace:
        root_path = workspace.path
        work = root_path / "work"
        work.mkdir()
        replacements = {"{python}": sys.executable, "{spec_dir}": str(spec.source.parent),
                        "{work_dir}": str(work)}
        command = [re.sub(r"\{(?:python|spec_dir|work_dir)\}",
                          lambda match: replacements[match.group()], argument)
                   for argument in spec.command]
        environment = os.environ.copy()
        environment.update(configuration)
        out_path, err_path = root_path / "stdout", root_path / "stderr"
        started = time.perf_counter()
        timed_out = False
        launch_error = None
        termination_error = None
        exit_code = None
        process = None
        with out_path.open("wb") as stdout_file, err_path.open("wb") as stderr_file:
            try:
                if os.name == 'nt':
                    from .windows_job import WindowsJobProcess
                    process = WindowsJobProcess(command, cwd=work, env=environment,
                                                stdout=stdout_file, stderr=stderr_file)
                else:
                    process = subprocess.Popen(
                        command, cwd=work, env=environment, stdin=subprocess.DEVNULL,
                        stdout=stdout_file, stderr=stderr_file, shell=False,
                        start_new_session=True,
                    )
                try:
                    exit_code = process.wait(timeout=spec.settings.timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    termination_error = _stop_process_tree(process)
                    exit_code = process.returncode
            except OSError as exc:
                launch_error = f"{type(exc).__name__}: {exc}"
            except BaseException:
                if process is not None:
                    _stop_process_tree(process)
                raise
            finally:
                if process is not None and hasattr(process, 'close'):
                    close_error = process.close()
                    if close_error:
                        termination_error = close_error if not termination_error else termination_error + '; ' + close_error
        duration = time.perf_counter() - started
        stdout, stdout_truncated = _read_output(out_path, spec.settings.max_output_bytes)
        stderr, stderr_truncated = _read_output(err_path, spec.settings.max_output_bytes)
        result = spec.failure.evaluate(
            exit_code=exit_code, stdout=stdout, stderr=stderr, timed_out=timed_out,
            launch_error=launch_error, stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        record = RunRecord(command, str(work), dict(configuration), exit_code, stdout, stderr,
                           duration, timed_out, launch_error, stdout_truncated, stderr_truncated,
                           "failure" if result is True else "pass" if result is False else "inconclusive",
                           termination_error=termination_error)
        record.timeout_seconds = spec.settings.timeout_seconds
    record.cleanup_error = workspace.cleanup_error
    if record.cleanup_error or record.termination_error:
        record.outcome = "inconclusive"
    elif not record.timed_out and not record.launch_error and record.exit_code in spec.invalid_exit_codes:
        record.outcome = "invalid"
    return record
