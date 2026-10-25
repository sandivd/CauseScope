"""Create test data and forward unmodified Git output/status; no failure oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LOCK_PATH = HERE / "target.lock.json"
FIXTURE = b"alpha\nbeta\n"
META_PREFIX = "CAUSESCOPE_GIT_META="
SETTINGS = ("core.autocrlf", "core.safecrlf", "core.quotePath", "core.trustctime")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_environment(home: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("GIT_")}
    environment.update({
        "HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / "xdg"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "0", "LC_ALL": "C", "LANG": "C",
    })
    return environment


def verify_target(*, check_archive: bool = False) -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError("This exact benchmark pin supports Windows x86-64 only")
    installation = ROOT / lock["installation_directory"]
    hashes = {}
    for relative, expected in lock["binary_sha256"].items():
        actual = sha256(installation / relative)
        if actual != expected:
            raise RuntimeError(f"Binary SHA256 mismatch: {relative}")
        hashes[relative] = actual
    if check_archive:
        archive = ROOT / ".tools" / lock["archive_url"].rsplit("/", 1)[1]
        if sha256(archive) != lock["archive_sha256"]:
            raise RuntimeError("Archive SHA256 mismatch")
    executable = installation / lock["executable"]
    version = subprocess.run([str(executable), "--version"], capture_output=True,
                             encoding="utf-8", errors="replace", timeout=5,
                             env=clean_environment(installation / "unused-home"))
    if version.returncode or version.stdout.strip() != lock["version_stdout"]:
        raise RuntimeError(f"Unsupported Git version: {version.stdout!r} {version.stderr!r}")
    return {"executable": str(executable), "version": version.stdout.strip(),
            "binary_sha256": hashes, "archive_sha256": lock["archive_sha256"]}


def native_configuration(configuration: dict[str, str]) -> dict[str, str]:
    native = {}
    for index, name in enumerate(SETTINGS):
        key = f"GIT_CONFIG_VALUE_{index}"
        value = configuration[key]
        if value not in ("true", "false"):
            raise ValueError(f"Unsupported {key} value {value!r}: expected true or false")
        native[name] = value
    return native


def observe() -> int:
    configuration = {f"GIT_CONFIG_VALUE_{i}": os.environ[f"GIT_CONFIG_VALUE_{i}"] for i in range(4)}
    native = native_configuration(configuration)
    root = Path.cwd()
    if any(root.iterdir()):
        raise RuntimeError("Adapter requires an empty, disposable working directory")
    target = verify_target()
    home, templates, repository = root / "home", root / "empty-templates", root / "repository"
    for directory in (home, templates, repository):
        directory.mkdir()
    environment = clean_environment(home)
    executable = target["executable"]
    init_command = [executable, "init", "--quiet", f"--template={templates}"]
    initialization = subprocess.run(init_command, cwd=repository, env=environment,
                                    capture_output=True, timeout=5)
    if initialization.returncode:
        raise RuntimeError(f"git init failed: {initialization.stderr.decode('utf-8', errors='replace')}")
    (repository / "fixture.txt").write_bytes(FIXTURE)
    environment["GIT_CONFIG_COUNT"] = str(len(native))
    for index, (name, value) in enumerate(native.items()):
        environment[f"GIT_CONFIG_KEY_{index}"] = name
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    command = [executable, "add", "--", "fixture.txt"]
    metadata = {**target, "configuration": native, "fixture_sha256": hashlib.sha256(FIXTURE).hexdigest(),
                "repository": str(repository), "git_invocations": 3,
                "commands": [[executable, "--version"], init_command, command]}
    print(META_PREFIX + json.dumps(metadata, sort_keys=True), flush=True)
    # The observed exit status and diagnostic come directly from the external CLI.
    return subprocess.run(command, cwd=repository, env=environment, timeout=5).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify the pinned archive, binaries, and version")
    parser.add_argument("--allow-local-execution", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.check and not arguments.allow_local_execution:
        parser.error("--allow-local-execution is required")
    try:
        if arguments.check:
            print(json.dumps(verify_target(check_archive=True), indent=2))
            return 0
        return observe()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"benchmark adapter unsupported: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
