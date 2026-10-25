"""Publication boundaries, redaction and actual distribution contents."""
import base64
import io
import json
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from tools.publication_audit import Audit, METADATA, ROOT, main, policy


@pytest.mark.parametrize("name", [
    "evidence/screening.json", "ground-truth.json", "raw/transcript.json",
    "private/future.json", ".env", ".env.local", "server.key",
    "participant-responses/person.json", "observations.sqlite",
])
def test_private_members_are_rejected(name, tmp_path):
    archive = tmp_path / "participant.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, "{}")
    audit = Audit()
    audit.archive(archive, "participant")
    assert any(f["rule"] == "private_archive_member" for f in audit.findings)


@pytest.mark.parametrize("name", ["../outside.txt", "/outside.txt", "C:" + "/outside.txt"])
def test_archive_path_escapes_are_detected_without_extracting(name, tmp_path):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr(name, "not extracted")
    audit = Audit()
    audit.archive(archive, "participant")
    assert any(f["rule"] == "unsafe_archive_member" for f in audit.findings)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["input.zip"]


def test_secrets_and_base64_are_reported_without_values():
    secret = "sk-proj-" + "Z" * 36
    encoded = base64.b64encode(secret.encode()).decode()
    audit = Audit()
    audit.text("output.json", json.dumps({"stdout_base64": encoded}))
    report = json.dumps(audit.report())
    assert "recognizable_token" in report
    assert secret not in report and encoded not in report


@pytest.mark.parametrize("literal,rule", [
    ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
    ("Authorization: Bearer " + "Q" * 25, "authentication_header"),
    ("password=" + "invented-test-value", "credential_literal"),
    ("https://" + "test-user:test-password@host.invalid", "credential_url"),
])
def test_recognizable_credentials(literal, rule):
    audit = Audit()
    audit.text("input.txt", literal)
    assert rule in {f["rule"] for f in audit.findings}


def test_personal_paths_detected_in_plain_and_json_strings():
    personal = "C:/" + "Users/ExamplePerson/project/input.py"
    for text in (personal, json.dumps({"path": personal.replace("/", "\\")})):
        audit = Audit()
        audit.text("input.txt", text)
        assert "absolute_local_path" in {f["rule"] for f in audit.findings}


def test_portable_paths_and_license_attribution_are_not_secrets():
    audit = Audit()
    audit.text("instructions.md", r"C:\path\to\python.exe and C:\Windows\System32\cmd.exe")
    audit.text("LICENSE", "Upstream author <maintainer@example.invalid>")
    audit.text("runner.py", r'C:\Windows')
    audit.text("fixture.py", r'k:\n  "word"')
    assert not audit.findings


def test_nested_dependency_wheels_are_inspected(tmp_path):
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, "w") as stream:
        stream.writestr("package/.env", "do not publish")
    archive = tmp_path / "participant.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("wheelhouse/dependency-1-py3-none-any.whl", wheel.getvalue())
    audit = Audit()
    audit.archive(archive, "participant")
    assert any(f["path"].endswith("#package/.env") for f in audit.findings)


def test_populated_participant_feedback_is_not_public(tmp_path):
    archive = tmp_path / "feedback.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("feedback-template.json", json.dumps({
            "status": "template_not_observations", "participant_id": "P01"}))
    audit = Audit()
    audit.archive(archive, "participant")
    assert any(f["rule"] == "participant_observations" for f in audit.findings)


def test_size_limit_fails_closed(tmp_path, monkeypatch):
    import tools.publication_audit as module
    archive = tmp_path / "large.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("README.md", "x" * 200)
    monkeypatch.setattr(module, "MAX_FILE_BYTES", 100)
    audit = Audit()
    audit.archive(archive, "participant")
    assert any(f["rule"] == "scan_size_limit" for f in audit.findings)


def test_cli_never_overwrites_an_existing_report(tmp_path, capsys):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("README.md", "portable instructions")
    output = tmp_path / "report.json"
    assert main(["--participant-zip", str(archive), "--output", str(output)]) == 0
    original = output.read_bytes()
    with pytest.raises(FileExistsError):
        main(["--participant-zip", str(archive), "--output", str(output)])
    assert output.read_bytes() == original
    assert "not guarantee safety" in capsys.readouterr().out


def test_policy_rejects_relative_escapes(tmp_path):
    (tmp_path / "publication-files.json").write_text(json.dumps({
        "sdist": ["../outside"], "source_additions": []}))
    with pytest.raises(ValueError, match="unique relative"):
        policy(tmp_path)


def test_source_audit_rejects_unlisted_answers_and_git_metadata(tmp_path):
    (tmp_path / "publication-files.json").write_text(json.dumps({
        "scope": "test", "sdist": ["publication-files.json"], "source_additions": []}))
    (tmp_path / "answers.json").write_text('{"private": "not approved"}')
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/main")
    audit = Audit(tmp_path)
    audit.source(require_no_vcs=True)
    assert {f["rule"] for f in audit.findings} == {
        "unreviewed_source_file", "version_control_metadata"}
    assert {f["path"] for f in audit.findings} == {"answers.json", ".git"}


def test_source_audit_accepts_root_checkout_metadata_but_rejects_nested_history(tmp_path):
    (tmp_path / "publication-files.json").write_text(json.dumps({
        "scope": "test", "sdist": ["publication-files.json"], "source_additions": []}))
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/main")
    audit = Audit(tmp_path)
    audit.source()
    assert not audit.findings
    assert audit.report()["excluded_local_paths"] == [".git"]
    (tmp_path / "nested/.git").mkdir(parents=True)
    audit = Audit(tmp_path)
    audit.source()
    assert audit.findings == [{"path": "nested/.git", "rule": "version_control_metadata",
                               "line": None, "matched_values": "omitted"}]


def test_source_audit_lists_excluded_local_outputs_without_reading_them(tmp_path):
    (tmp_path / "publication-files.json").write_text(json.dumps({
        "scope": "test", "sdist": ["publication-files.json"], "source_additions": []}))
    (tmp_path / "results").mkdir()
    (tmp_path / "results/transcript.json").write_text("local verification output")
    audit = Audit(tmp_path)
    audit.source()
    assert not audit.findings
    assert audit.report()["excluded_local_paths"] == ["results/"]


def test_real_sdist_excludes_unapproved_files_and_rebuilds_a_wheel(tmp_path):
    # Build a copy of approved input files, never touch the repository's evidence.
    project = tmp_path / "project"
    project.mkdir()
    approved = set(policy()["sdist"])
    for name in approved:
        destination = project / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    unwanted = [
        "benchmarks/future/ground-truth.json", "docs/unreviewed.md", ".env",
        "benchmarks/independent_value/evidence/transcript.json",
        "participant-responses/P01.json", "src/causescope/credentials.json",
    ]
    for name in unwanted:
        destination = project / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("excluded fixture", encoding="utf-8")
    stale = project / "src/causescope.egg-info/SOURCES.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("\n".join(unwanted), encoding="utf-8")
    process = subprocess.run([sys.executable, "-m", "build", "--sdist", "--no-isolation"],
                             cwd=project, capture_output=True, text=True, timeout=90)
    assert process.returncode == 0, process.stdout + process.stderr
    archive, = (project / "dist").glob("*.tar.gz")
    extracted = tmp_path / "from-sdist"
    extracted.mkdir()
    with tarfile.open(archive, "r:gz") as stream:
        names = {m.name.partition("/")[2] for m in stream.getmembers() if m.isfile()}
        assert approved <= names
        assert names <= approved | METADATA
        assert not set(unwanted) & names
        # All members were checked against the relative allowlist before extraction.
        for member in stream.getmembers():
            if not member.isfile():
                continue
            name = member.name.partition("/")[2]
            destination = extracted / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(stream.extractfile(member).read())
    process = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation"],
                             cwd=extracted, capture_output=True, text=True, timeout=90)
    assert process.returncode == 0, process.stdout + process.stderr
    wheel, = (extracted / "dist").glob("*.whl")
    with zipfile.ZipFile(wheel) as stream:
        assert "causescope/cli.py" in stream.namelist()
        assert not any("benchmarks/" in n or "credentials.json" in n for n in stream.namelist())
