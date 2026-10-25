import pytest

from causescope.minimize import ddmin


@pytest.mark.parametrize("size", range(1, 8))
def test_ddmin_returns_failing_set_with_all_immediate_reductions_passing(size):
    changes = tuple((str(index), "1") for index in range(size))
    # Multiple possible minimal causes exercise different reduction paths.
    causes = [{changes[0]}, {changes[-1]}] if size == 1 else [{changes[0], changes[-1]}]
    def test(candidate):
        return "failure" if any(cause <= set(candidate) for cause in causes) else "pass"
    result = ddmin(changes, test)
    assert result.one_minimal
    assert test(result.changes) == "failure"
    for index in range(len(result.changes)):
        assert test(result.changes[:index] + result.changes[index + 1:]) == "pass"


def test_inconclusive_reductions_are_never_accepted():
    changes = (("A", "1"), ("B", "1"), ("C", "1"))
    result = ddmin(changes, lambda c: "inconclusive" if len(c) < 3 else "failure")
    assert result.changes == changes
    assert not result.one_minimal


def test_nonmonotonic_failure_has_verified_local_minimum():
    changes = tuple((str(i), "1") for i in range(6))
    def test(candidate):
        return "failure" if len(candidate) in (2, 4, 6) else "pass"
    result = ddmin(changes, test)
    assert result.one_minimal
    assert test(result.changes) == "failure"
    assert all(test(result.changes[:i] + result.changes[i + 1:]) == "pass" for i in range(len(result.changes)))
