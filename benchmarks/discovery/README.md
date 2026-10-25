# Synthetic configuration experiments

The seeded generator describes 10-, 20- and 32-parameter synthetic targets with
single/pair/triple triggers, masking, constraints, runtime-invalid outcomes and
controlled flaky schedules. The engine sees domains, constraints and observations;
only the target and post-run auditor read the generated oracle. These are openly
specified fixtures, not undisclosed evaluation questions.

Install `.[dev]` from the source root. Use fresh output paths:

```sh
python -m benchmarks.discovery.generate --output results/fixtures
python -m benchmarks.discovery.study --fixtures results/fixtures --output results/study/simulation --backend simulation --allow-local-execution
python -m benchmarks.discovery.pack results/study/simulation
python -m benchmarks.discovery.audit --fixtures results/fixtures --results results/study/simulation
```

Use `--backend subprocess` in a separate output directory for actual processes.
`--profile tractable` selects the small comparison profile. Full campaigns can
take several minutes. [PLAN.md](PLAN.md) describes the synthetic design and its
limits. Default tests generate small fixtures locally and audit their accounting;
historical run evidence is excluded from this public snapshot.

Simulated observation counts are not physical subprocess counts. Do not pool their
timings. Finite unanimous batches can falsely confirm an unseen flake. A clean
sample does not certify a clean configuration space, and observed 1-minimality
is neither global minimality nor root-cause proof.

Generated JSON/gzip and reports retain execution details. Keep them under ignored
`results/` and review before sharing. `pack` is lossless and checks recorded hashes;
`audit` inspects results without executing targets. No Codex comparison is made.
