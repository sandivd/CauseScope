import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest
import yaml

from causescope import __version__
from causescope.cli import main
from causescope.engine import ExperimentEngine
from causescope.report import prepare_output, render_html, write_reports


ROOT = Path(__file__).resolve().parents[1]


def invoke(*arguments, cwd=None):
    return subprocess.run([sys.executable, "-m", "causescope", *map(str, arguments)],
                          cwd=cwd, capture_output=True, text=True, timeout=30)


def test_installed_cli_end_to_end_from_unrelated_directory(tmp_path):
    output = tmp_path / "results with spaces"
    completed = invoke("run", ROOT / "examples" / "interaction.yaml", "--allow-local-execution", "--output", output, cwd=tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert "failure_found" in completed.stdout
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["schema_version"] == 1
    assert evidence["causescope_version"] == __version__
    assert evidence["diagnosis"]["changes"] == {"CAUSESCOPE_CACHE": "on", "CAUSESCOPE_FAST": "on"}
    assert evidence["diagnosis"]["one_minimal"]
    assert evidence["search"]["subprocess_runs"] == 18
    assert all(e["runs"] for e in evidence["evaluations"])
    runs = [run for e in evidence["evaluations"] for run in e["runs"]]
    assert len({r["working_directory"] for r in runs}) == 18
    assert all(not Path(r["working_directory"]).exists() for r in runs)
    for run in runs:
        assert {"exit_code", "stdout", "stderr", "duration_seconds", "command", "configuration"} <= run.keys()
    report = (output / "report.html").read_text(encoding="utf-8")
    assert "<!doctype html>" in report and "1-minimal" in report and "interaction bug" in report
    assert "https://" not in report


def test_console_script_entry_point(tmp_path):
    executable = Path(sysconfig.get_path("scripts")) / ("causescope.exe" if os.name == "nt" else "causescope")
    completed = subprocess.run([str(executable), "--version"], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0
    assert completed.stdout.strip() == f"CauseScope {__version__}"


def test_validate_and_missing_opt_in_execute_nothing(raw_spec, tmp_path):
    marker = tmp_path / "executed"
    raw_spec["command"] = ["{python}", "-c", "import pathlib, sys; pathlib.Path(sys.argv[1]).touch()", str(marker)]
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(raw_spec), encoding="utf-8")
    assert invoke("validate", path).returncode == 0
    completed = invoke("run", path, "--output", tmp_path / "output")
    assert completed.returncode == 2
    assert "--allow-local-execution" in completed.stderr
    assert not marker.exists()
    assert not (tmp_path / "output").exists()


def test_nonlocal_spec_is_rejected_even_with_opt_in(raw_spec, tmp_path):
    raw_spec["scope"] = "production"
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(raw_spec), encoding="utf-8")
    result = invoke("run", path, "--allow-local-execution", "--output", tmp_path / "output")
    assert result.returncode == 2
    assert "scope must be 'local'" in result.stderr
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("failure,expected", [({"exit_code": 0}, "baseline_failure"), ({"exit_code": 99}, "inconclusive")])
def test_inconclusive_and_baseline_failure_export_evidence(raw_spec, tmp_path, failure, expected):
    raw_spec["command"] = ["{python}", "-c", "pass"]
    raw_spec["failure"] = failure
    raw_spec["settings"]["max_configurations"] = 1
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(raw_spec), encoding="utf-8")
    output = tmp_path / "output"
    completed = invoke("run", path, "--allow-local-execution", "--output", output)
    assert completed.returncode == 3
    assert json.loads((output / "evidence.json").read_text(encoding="utf-8"))["status"] == expected
    assert (output / "report.html").exists()


def test_existing_report_prevents_execution(tmp_path, monkeypatch, capsys):
    output = tmp_path / "output"
    output.mkdir()
    (output / "report.html").write_text("preserve", encoding="utf-8")
    def forbidden(*args, **kwargs):
        pytest.fail("existing artifacts must be checked before execution")
    monkeypatch.setattr(ExperimentEngine, "run", forbidden)
    assert main(["run", str(ROOT / "examples" / "interaction.yaml"), "--allow-local-execution", "--output", str(output)]) == 2
    assert "already exists" in capsys.readouterr().err
    assert (output / "report.html").read_text(encoding="utf-8") == "preserve"


def test_report_escapes_all_untrusted_text_and_overwrite_is_explicit(make_spec, fake_runner, tmp_path):
    attack = '<script>alert("target")</script>'
    result = ExperimentEngine(make_spec(name=attack), fake_runner(lambda c, n: "pass")).run(allow_local_execution=True)
    result["evaluations"][0]["runs"][0]["stdout"] = attack
    result["evaluations"][0]["runs"][0]["stderr"] = attack
    result["evaluations"][0]["runs"][0]["command"] = [attack]
    html = render_html(result)
    assert attack not in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    evidence, report = write_reports(result, tmp_path / "reports")
    assert json.loads(evidence.read_text(encoding="utf-8"))["specification"]["name"] == attack
    with pytest.raises(FileExistsError):
        write_reports(result, evidence.parent)
    result["reason"] = "updated"
    write_reports(result, evidence.parent, overwrite=True)
    assert "updated" in report.read_text(encoding="utf-8")


def test_invalid_spec_has_clean_error(tmp_path, capsys):
    assert main(["validate", str(tmp_path / "missing.yaml")]) == 2
    assert "Cannot read YAML" in capsys.readouterr().err


def test_interruption_returns_130(monkeypatch, tmp_path, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(ExperimentEngine, "run", interrupted)
    assert main(["run", str(ROOT / "examples" / "interaction.yaml"), "--allow-local-execution", "--output", str(tmp_path)]) == 130
    assert "interrupted" in capsys.readouterr().err


def test_report_filename_cannot_be_a_directory(tmp_path):
    (tmp_path / "report.html").mkdir()
    with pytest.raises(IsADirectoryError):
        prepare_output(tmp_path, overwrite=True)
