# Synthetic discovery design

The source generator defines openly specified synthetic fixtures. This is a
repeatable algorithm exercise, not an undisclosed agent evaluation. This snapshot
includes no historical observations, timings or performance claims.

Fixtures have 10, 20 or 32 binary parameters and independent seeds 104729, 130363,
155921, 196613 and 262147. Kinds cover single, pair and triple triggers, masking,
public constraints, runtime-invalid combinations, clean spaces and controlled
flakes. Generation imports no selection or engine code. Generated oracle data is
read by the target and post-run auditor; the selector sees only the public spec
and observed outcomes. These local files do not enforce confidentiality.

The bounded profile uses 80 configurations, 300 observations and three repetitions.
Simulation receives a 15-second budget; real subprocess trials receive 60 seconds
with a two-second per-target timeout. Search seeds are 17 and 43. Methods share
the same predicate, runner semantics, reduction and fresh confirmation policy.
The tractable simulation profile selects small pair/triple/clean spaces and gives
all methods 1024 configurations and 3080 observations for exhaustive comparisons.

The subprocess subset covers each fixture kind and all three dimensions using
fixture seeds 104729 and 130363, with search seed 17. Simulation and physical
process timings are separate populations. Controlled flakes depend on observation
index; they are not independent Bernoulli samples. Finite repeated observations
can falsely confirm a flake that changes outside the observed window.

Keep fresh generated specifications, oracles, observations and reports under
ignored `results/`. The audit recomputes accounting and checks recorded hashes;
packing preserves original bytes using gzip. No archived run is needed for tests.
See [README.md](README.md) for runnable commands.

Observed 1-minimality under deletion does not prove a globally smallest set or an
internal root cause. Bounded clean samples do not certify absence of failure.
Adaptive and pairwise search have no universal advantage over random sampling or
exhaustive enumeration. No Codex comparison is made.

Algorithm references: [AllPairs source](https://github.com/thombashi/allpairspy),
[NIST combinatorial testing FAQ](https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software/faqs),
and [NIST coverage measurement](https://csrc.nist.gov/pubs/ir/7878/final).
