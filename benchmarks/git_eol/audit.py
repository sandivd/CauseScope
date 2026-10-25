"""Check evidence independently of the narrow CauseScope failure predicate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from .adapter import FIXTURE, LOCK_PATH, META_PREFIX, native_configuration


def _recorded_path(value: str) -> PurePath:
    """Parse archived paths in their recorded flavour, independently of the host."""
    path_type = PureWindowsPath if PureWindowsPath(value).drive else PurePosixPath
    return path_type(value)


def audit_result(result: dict) -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    errors = []
    directories = set()
    git_invocations = 0
    observations = 0
    batches = 0
    native_cleanup_checks = foreign_cleanup_paths = 0
    repetitions = result["specification"]["settings"]["repetitions"]
    for evaluation in result["evaluations"]:
        batches += len(evaluation["runs"]) // repetitions
        outcomes = {run["outcome"] for run in evaluation["runs"]}
        expected_status = next(iter(outcomes)) if len(outcomes) == 1 else "inconclusive"
        if evaluation["status"] != expected_status:
            errors.append(f"{evaluation['id']}: inconsistent evaluation aggregation")
        for index, run in enumerate(evaluation["runs"], 1):
            observations += 1
            label = f"{evaluation['id']}/{index}"
            if run["exit_code"] not in (0, 128):
                errors.append(f"{label}: unexpected exit status {run['exit_code']}")
            expected_outcome = "failure" if run["exit_code"] == 128 else "pass"
            if run["outcome"] != expected_outcome:
                errors.append(f"{label}: inconclusive or mismatched outcome")
            if run["exit_code"] == 128 and "fatal: LF would be replaced by CRLF in fixture.txt" not in run["stderr"]:
                errors.append(f"{label}: unrelated Git rejection")
            for flag in ("timed_out", "launch_error", "termination_error", "cleanup_error", "stdout_truncated", "stderr_truncated"):
                if run[flag]:
                    errors.append(f"{label}: {flag}")
            directory = run["working_directory"]
            recorded_directory = _recorded_path(directory)
            if directory in directories:
                errors.append(f"{label}: working directory reused")
            directories.add(directory)
            if not recorded_directory.is_absolute():
                errors.append(f"{label}: working directory is not absolute")
            elif isinstance(recorded_directory, type(PurePath())):
                native_cleanup_checks += 1
                if Path(directory).exists():
                    errors.append(f"{label}: working directory was not removed")
            else:
                # A foreign archive path is not a meaningful local filesystem probe.
                # Keep lexical containment/argv checks below and report this limit.
                foreign_cleanup_paths += 1
            metadata_lines = [line[len(META_PREFIX):] for line in run["stdout"].splitlines() if line.startswith(META_PREFIX)]
            if len(metadata_lines) != 1:
                errors.append(f"{label}: missing/duplicate adapter metadata")
                continue
            try:
                metadata = json.loads(metadata_lines[0])
                if metadata["version"] != lock["version_stdout"] or metadata["binary_sha256"] != lock["binary_sha256"]:
                    errors.append(f"{label}: version/binary pin mismatch")
                if metadata["archive_sha256"] != lock["archive_sha256"]:
                    errors.append(f"{label}: archive pin mismatch")
                if metadata["configuration"] != native_configuration(run["configuration"]):
                    errors.append(f"{label}: settings metadata mismatch")
                if run["configuration"] != evaluation["configuration"]:
                    errors.append(f"{label}: unexpected configuration change")
                if metadata["fixture_sha256"] != hashlib.sha256(FIXTURE).hexdigest():
                    errors.append(f"{label}: fixture mismatch")
                if type(recorded_directory)(metadata["repository"]).parent != recorded_directory:
                    errors.append(f"{label}: repository outside observation directory")
                executable = metadata["executable"]
                expected_commands = [[executable, "--version"],
                    [executable, "init", "--quiet", f"--template={recorded_directory / 'empty-templates'}"],
                    [executable, "add", "--", "fixture.txt"]]
                if metadata["commands"] != expected_commands or metadata["git_invocations"] != 3:
                    errors.append(f"{label}: unexpected Git commands")
                git_invocations += metadata["git_invocations"]
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(f"{label}: invalid metadata: {exc}")
    if observations != result["search"]["subprocess_runs"]:
        errors.append("Recorded observation count disagrees with search metadata")
    return {"passed": not errors, "errors": errors, "observations": observations,
            "configuration_batches": batches, "git_invocations": git_invocations,
            "working_directories": sorted(directories),
            "native_cleanup_existence_checks": native_cleanup_checks,
            "foreign_cleanup_paths_not_locally_verifiable": foreign_cleanup_paths,
            "sum_observation_duration_seconds": sum(run["duration_seconds"]
                for e in result["evaluations"] for run in e["runs"])}
