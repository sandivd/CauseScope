"""Public-domain-only selection. This module has no fixture/oracle dependencies."""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable, Iterator

from allpairspy import AllPairs

from .spec import ExperimentSpec


class PlanningStopped(Exception):
    """A bounded selector could not produce further justified experiments."""


class Space:
    def __init__(self, spec: ExperimentSpec, check: Callable[[], None] = lambda: None):
        self.spec = spec
        self.check = check
        self.names = tuple(spec.parameters)
        self.domains = tuple(tuple(value for value in (p.baseline,) + p.values
                                   if spec.allows({name: value}))
                             for name, p in spec.parameters.items())
        self.raw_size = math.prod(1 + len(p.values) for p in spec.parameters.values())
        effective_size = math.prod(map(len, self.domains))
        self.exact_size = (sum(1 for _ in self.enumerate()) if effective_size <= 4096 else None)

    def key(self, configuration: dict[str, str]) -> tuple[str, ...]:
        return tuple(configuration[name] for name in self.names)

    def configuration(self, values) -> dict[str, str]:
        return dict(zip(self.names, values))

    def enumerate(self) -> Iterator[dict[str, str]]:
        for values in itertools.product(*self.domains):
            self.check()
            configuration = self.configuration(values)
            if self.spec.allows(configuration):
                yield configuration

    def random_valid(self, rng: random.Random, check: Callable[[], None], changes: int | None = None):
        for _ in range(128):
            check()
            values = list(self.key(self.spec.baseline))
            if changes is None:
                values = [rng.choice(domain) for domain in self.domains]
            else:
                eligible = [i for i, domain in enumerate(self.domains) if len(domain) > 1]
                for index in rng.sample(eligible, min(changes, len(eligible))):
                    values[index] = rng.choice([v for v in self.domains[index] if v != values[index]])
            configuration = self.configuration(values)
            if self.spec.allows(configuration):
                return configuration
        return None


def pairwise_candidates(space: Space, seed: int, check: Callable[[], None]):
    """Established AllPairs generator with native partial forbidden-tuple filtering."""
    rng = random.Random(seed)
    order = list(range(len(space.names)))
    rng.shuffle(order)
    domains = [list(space.domains[i]) for i in order]
    for domain in domains:
        rng.shuffle(domain)
    calls = 0
    def allowed(values):
        nonlocal calls
        calls += 1
        check()
        if calls > 100_000:
            raise PlanningStopped("AllPairs planning step limit reached")
        return space.spec.allows({space.names[index]: value for index, value in zip(order, values)})
    if len(order) == 1:
        for value in domains[0]:
            yield {space.names[0]: value}
        return
    emitted = set()
    covered = set()
    def remember(configuration):
        key = space.key(configuration)
        emitted.add(key)
        covered.update((i, key[i], j, key[j]) for i, j in itertools.combinations(range(len(key)), 2))
    for values in AllPairs(domains, filter_func=allowed):
        check()
        configuration = {space.names[index]: value for index, value in zip(order, values)}
        if space.spec.allows(configuration) and space.key(configuration) not in emitted:
            remember(configuration)
            yield configuration
    # AllPairs' partial filter can leave feasible pairs uncovered. Complete only
    # those gaps with public-constraint witnesses; never label an unknown pair infeasible.
    for i, j in itertools.combinations(range(len(space.names)), 2):
        for a, b in itertools.product(space.domains[i], space.domains[j]):
            check()
            if (i, a, j, b) in covered:
                continue
            partial = {space.names[i]: a, space.names[j]: b}
            nodes = 0
            def complete():
                nonlocal nodes
                check()
                nodes += 1
                if nodes > 10_000:
                    raise PlanningStopped("Pair feasibility planning limit reached")
                if not space.spec.allows(partial):
                    return None
                if len(partial) == len(space.names):
                    return dict(partial)
                index = min((n for n, name in enumerate(space.names) if name not in partial),
                            key=lambda n: len(space.domains[n]))
                name = space.names[index]
                for value in space.domains[index]:
                    partial[name] = value
                    result = complete()
                    if result is not None:
                        del partial[name]
                        return result
                del partial[name]
                return None
            witness = complete()
            if witness is not None:
                remember(witness)
                yield witness


def configurations(spec: ExperimentSpec, space: Space, evaluations: dict,
                   check: Callable[[], None]) -> Iterator[dict[str, str]]:
    method = spec.settings.strategy
    rng = random.Random(spec.settings.seed)
    def tested():
        return {space.key(e.configuration) for e in evaluations.values()}
    if method == "one_at_a_time":
        for name, parameter in spec.parameters.items():
            for value in parameter.values:
                check()
                configuration = {**spec.baseline, name: value}
                if spec.allows(configuration):
                    yield configuration
        return
    if method == "brute_force":
        for configuration in space.enumerate():
            check()
            yield configuration
        return
    if method == "pairwise":
        yield from pairwise_candidates(space, spec.settings.seed, check)
        return
    if method == "random":
        if space.exact_size is not None:
            rows = list(space.enumerate())
            rng.shuffle(rows)
            yield from rows
        else:
            misses = 0
            while misses < 1024:
                check()
                configuration = space.random_valid(rng, check)
                if configuration is not None and space.key(configuration) not in tested():
                    misses = 0
                    yield configuration
                else:
                    misses += 1
        return
    if method != "adaptive":
        raise ValueError(f"Selection method {method!r} is not supported here")

    pool = {space.key(row): row for row in pairwise_candidates(space, spec.settings.seed, check)}
    # Singles are candidates, not an obligatory prefix; novelty decides their order.
    for name, parameter in spec.parameters.items():
        for value in parameter.values:
            row = {**spec.baseline, name: value}
            if spec.allows(row):
                pool[space.key(row)] = row
    fixed_candidates = set(pool)
    pair_indices = tuple(itertools.combinations(range(len(space.names)), 2))
    triples = list(itertools.combinations(range(len(space.names)), 3))
    triple_indices = tuple(rng.sample(triples, min(128, len(triples))))
    def interactions(key, indices):
        return {(index, tuple(key[i] for i in index)) for index in indices}
    seen_pairs, seen_triples = set(), set()
    processed = set()
    step = 0
    misses = 0
    while misses < 128:
        check()
        seen = tested()
        if space.exact_size is not None and len(seen) >= space.exact_size:
            return
        for evaluation in evaluations.values():
            key = space.key(evaluation.configuration)
            if key not in processed and evaluation.status in ("pass", "failure"):
                processed.add(key)
                seen_pairs.update(interactions(key, pair_indices))
                seen_triples.update(interactions(key, triple_indices))
        for key in list(pool):
            if key in seen or key not in fixed_candidates:
                del pool[key]
        for sample in range(spec.settings.candidate_pool):
            density = (None, 1, 2, 3)[sample % 4]
            row = space.random_valid(rng, check, density)
            if row is not None and space.key(row) not in seen:
                pool[space.key(row)] = row
        if not pool:
            misses += 1
            # Exhaust small spaces rather than incorrectly declare them healthy.
            if space.exact_size is not None:
                for row in space.enumerate():
                    if space.key(row) not in seen:
                        pool[space.key(row)] = row
                        break
            if not pool:
                continue
        misses = 0
        candidates = list(pool)
        # Every fourth choice explores sparse contexts that dense coverage can mask.
        if step % 4 == 3:
            sparse = [key for key in candidates if 1 <= sum(v != spec.baseline[n]
                       for n, v in zip(space.names, key)) <= 3]
            candidates = sparse or candidates
        def score(key):
            return (len(interactions(key, pair_indices) - seen_pairs),
                    len(interactions(key, triple_indices) - seen_triples), rng.random())
        chosen = max(candidates, key=score)
        yield pool.pop(chosen)
        step += 1
