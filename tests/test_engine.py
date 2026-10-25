import pytest

from causescope.engine import ExecutionNotAllowed, ExperimentEngine


def parameter_space(names, values=("1",)):
    return {name: {"baseline": "0", "values": list(values)} for name in names}


def test_library_requires_execution_opt_in(make_spec):
    def forbidden(*args):
        pytest.fail("must not execute")
    with pytest.raises(ExecutionNotAllowed):
        ExperimentEngine(make_spec(), forbidden).run()


def test_interaction_and_confirmations_recorded(make_spec, fake_runner):
    runner = fake_runner(lambda c, n: "failure" if all(v == "on" for v in c.values()) else "pass")
    result = ExperimentEngine(make_spec(), runner).run(allow_local_execution=True)
    assert result["status"] == "failure_found"
    assert result["diagnosis"]["changes"] == {"CAUSESCOPE_CACHE": "on", "CAUSESCOPE_FAST": "on"}
    assert result["diagnosis"]["one_minimal"] is True
    assert result["diagnosis"]["confirmed"] is True
    assert result["search"]["subprocess_runs"] == 18
    assert result["search"]["tested_configurations"] == 4
    assert result["search"]["search_complete"]
    baseline = next(e for e in result["evaluations"] if not e["changes"])
    assert {"baseline", "baseline_confirmation"} <= set(baseline["phases"])
    assert len(baseline["runs"]) == 6
    individuals = [e for e in result["evaluations"] if len(e["changes"]) == 1]
    assert len(individuals) == 2
    assert all(e["status"] == "pass" for e in individuals)
    assert all(count in (3, 6) for count in runner.counts.values())


def test_chunk_minimization_discards_irrelevant_changes(make_spec, fake_runner):
    spec = make_spec(parameters=parameter_space("ABCDEFGH"))
    runner = fake_runner(lambda c, n: "failure" if c["B"] == c["G"] == "1" else "pass")
    result = ExperimentEngine(spec, runner).run(allow_local_execution=True)
    assert result["status"] == "failure_found"
    assert result["diagnosis"]["changes"] == {"B": "1", "G": "1"}
    assert result["diagnosis"]["one_minimal"]
    assert any(len(e["changes"]) == 8 for e in result["evaluations"])
    assert any(2 < len(e["changes"]) < 8 and "minimize" in e["phases"] for e in result["evaluations"])
    for name in "ACDEFH":
        assert result["diagnosis"]["configuration"][name] == "0"


def test_failure_masked_by_another_option_is_found(make_spec, fake_runner):
    spec = make_spec(parameters=parameter_space("ABC"))
    runner = fake_runner(lambda c, n: "failure" if c["A"] == c["B"] == "1" and c["C"] == "0" else "pass")
    result = ExperimentEngine(spec, runner).run(allow_local_execution=True)
    assert result["status"] == "failure_found"
    assert result["diagnosis"]["changes"] == {"A": "1", "B": "1"}
    assert any(e["changes"] == {"A": "1", "B": "1", "C": "1"} and e["status"] == "pass"
               for e in result["evaluations"])


def test_multiple_parameter_values_and_single_changes(make_spec, fake_runner):
    spec = make_spec(parameters=parameter_space("AB", values=("1", "2")))
    runner = fake_runner(lambda c, n: "failure" if c["A"] == "2" else "pass")
    result = ExperimentEngine(spec, runner).run(allow_local_execution=True)
    assert result["status"] == "failure_found"
    assert result["diagnosis"]["changes"] == {"A": "2"}
    assert result["search"]["total_configurations"] == 9
    assert len([e for e in result["evaluations"] if "individual" in e["phases"]]) == 4
    assert not any("search" in e["phases"] for e in result["evaluations"])


@pytest.mark.parametrize("outcome,status", [("failure", "baseline_failure"), ("inconclusive", "inconclusive")])
def test_no_diagnosis_without_healthy_baseline(make_spec, fake_runner, outcome, status):
    result = ExperimentEngine(make_spec(), fake_runner(lambda c, n: outcome)).run(allow_local_execution=True)
    assert result["status"] == status
    assert result["diagnosis"] is None
    assert result["search"]["tested_configurations"] == 1
    assert result["search"]["subprocess_runs"] == 3


def test_flaky_baseline_is_inconclusive(make_spec, fake_runner):
    result = ExperimentEngine(make_spec(), fake_runner(lambda c, n: "failure" if n % 2 else "pass")).run(allow_local_execution=True)
    assert result["status"] == "inconclusive"
    assert result["diagnosis"] is None
    assert result["evaluations"][0]["status"] == "inconclusive"


def test_flaky_combination_does_not_count_as_failure(make_spec, fake_runner):
    def classify(c, n):
        return "failure" if all(v == "on" for v in c.values()) and n % 2 else "pass"
    result = ExperimentEngine(make_spec(), fake_runner(classify)).run(allow_local_execution=True)
    assert result["status"] == "inconclusive"
    assert result["diagnosis"] is None
    assert result["search"]["search_complete"]
    assert result["evaluations"][-1]["status"] == "inconclusive"


@pytest.mark.parametrize("drifting", ["candidate", "baseline"])
def test_confirmation_drift_invalidates_diagnosis(make_spec, fake_runner, drifting):
    def classify(c, n):
        candidate = all(v == "on" for v in c.values())
        baseline = all(v == "off" for v in c.values())
        if n > 3 and ((candidate and drifting == "candidate") or (baseline and drifting == "baseline")):
            return "pass" if candidate else "failure"
        return "failure" if candidate else "pass"
    result = ExperimentEngine(make_spec(), fake_runner(classify)).run(allow_local_execution=True)
    assert result["status"] == "inconclusive"
    assert result["diagnosis"]["confirmed"] is False
    assert result["diagnosis"]["one_minimal"] is False
    assert len([e for e in result["evaluations"] if len(e["runs"]) == 6]) == 2


def test_unknown_reductions_prevent_minimality_claim(make_spec, fake_runner):
    def classify(c, n):
        count = sum(v == "on" for v in c.values())
        return "pass" if count == 0 else "failure" if count == 2 else "inconclusive"
    result = ExperimentEngine(make_spec(), fake_runner(classify)).run(allow_local_execution=True)
    assert result["status"] == "inconclusive"
    assert result["diagnosis"]["confirmed"] is True
    assert result["diagnosis"]["one_minimal"] is False


def test_configuration_budget_exhaustion_is_inconclusive(make_spec, fake_runner):
    spec = make_spec(settings={"max_configurations": 1})
    result = ExperimentEngine(spec, fake_runner(lambda c, n: "pass")).run(allow_local_execution=True)
    assert result["status"] == "inconclusive"
    assert result["search"]["budget_exhausted"]
    assert result["search"]["tested_configurations"] == 1
    assert result["search"]["subprocess_runs"] == 3


def test_minimization_budget_does_not_create_false_minimality(make_spec, fake_runner):
    spec = make_spec(parameters=parameter_space("ABCD"), settings={"max_configurations": 6})
    runner = fake_runner(lambda c, n: "failure" if all(v == "1" for v in c.values()) else "pass")
    result = ExperimentEngine(spec, runner).run(allow_local_execution=True)
    assert result["status"] == "inconclusive"
    assert result["diagnosis"]["confirmed"]
    assert not result["diagnosis"]["one_minimal"]
    assert result["search"]["tested_configurations"] == 6
    assert result["search"]["subprocess_runs"] == (6 + 2) * 3


def test_exhaustive_clean_search_covers_every_value_combination(make_spec, fake_runner):
    parameters = parameter_space("AB")
    parameters["A"]["values"] = ["1", "2"]
    result = ExperimentEngine(make_spec(parameters=parameters), fake_runner(lambda c, n: "pass")).run(allow_local_execution=True)
    assert result["status"] == "no_failure_found"
    assert result["search"]["search_complete"]
    assert not result["search"]["budget_exhausted"]
    assert result["search"]["total_configurations"] == result["search"]["tested_configurations"] == 6
    assert result["search"]["subprocess_runs"] == 18
    assert result["diagnosis"] is None


def test_budget_exactly_covering_search_is_not_exhausted(make_spec, fake_runner):
    result = ExperimentEngine(make_spec(settings={"max_configurations": 4}),
                              fake_runner(lambda c, n: "pass")).run(allow_local_execution=True)
    assert result["status"] == "no_failure_found"
    assert not result["search"]["budget_exhausted"]


def test_engine_cannot_reuse_old_observations(make_spec, fake_runner):
    engine = ExperimentEngine(make_spec(), fake_runner(lambda c, n: "pass"))
    engine.run(allow_local_execution=True)
    with pytest.raises(ValueError, match="only run once"):
        engine.run(allow_local_execution=True)
