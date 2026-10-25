"""Delta debugging with explicit uncertainty and 1-minimality verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

Change = tuple[str, str]


@dataclass
class Minimized:
    changes: tuple[Change, ...]
    one_minimal: bool


def ddmin(changes: tuple[Change, ...], test: Callable[[tuple[Change, ...]], str]) -> Minimized:
    """Input must already fail; only consistently failing reductions are retained.

    The callback returns failure, pass, or inconclusive. Budget exhaustion should
    also return inconclusive. No unknown result is treated as evidence of health.
    """
    current = changes
    granularity = 2
    while len(current) >= 2:
        size = max(1, (len(current) + granularity - 1) // granularity)
        chunks = [current[start:start + size] for start in range(0, len(current), size)]
        reduced = False
        for chunk in chunks:
            complement = tuple(item for item in current if item not in chunk)
            if test(complement) == "failure":
                current = complement
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if reduced:
            continue
        if granularity >= len(current):
            break
        granularity = min(len(current), granularity * 2)

    # This check makes the final guarantee explicit, including inconclusive tests.
    while current:
        one_minimal = True
        for index in range(len(current)):
            candidate = current[:index] + current[index + 1:]
            status = test(candidate)
            if status == "failure":
                current = candidate
                break
            if status != "pass":
                one_minimal = False
        else:
            return Minimized(current, one_minimal)
    return Minimized(current, False)
