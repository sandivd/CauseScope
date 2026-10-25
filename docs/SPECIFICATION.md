# Specification and evidence reference

## Specification rules

- `command` is an argv list, executed with `shell=False` and stdin closed.
  `{python}` expands to CauseScope's interpreter, `{spec_dir}` to the YAML file's
  directory, and `{work_dir}` to the fresh temporary working directory. Other
  braces are literal. For a script beside the YAML file, use
  `["{python}", "{spec_dir}/target.py"]`. Relative paths resolve inside the
  temporary directory; CauseScope does not copy project files there.
- Each parameter is an environment variable with one `baseline` and a nonempty
  list of alternative `values`. Strings, finite numbers, and booleans are accepted;
  booleans become `true`/`false` and other values become strings. Quote `"on"`,
  `"off"`, `"yes"`, and `"no"` to avoid YAML boolean conversion. Parameter names
  must be distinct ignoring case. The target inherits CauseScope's environment,
  with all specified parameters overridden on every run.
- At least one failure condition is required: `exit_code` (integer),
  `stdout_contains`, `stderr_contains` (case-sensitive, nonempty strings), or
  `timeout` (boolean). Conditions are combined with **AND**. A pass means the
  predicate did not match; it does not mean every aspect of the target succeeded.
- Settings default to 5 seconds per subprocess, 3 repetitions per configuration,
  100 distinct configurations, and 1 MiB retained **per output stream per run**.
  At least 2 repetitions are required. Unknown fields and duplicate YAML keys
  are rejected, so misspelled limits cannot silently take effect.

Optional constrained, adaptive search settings (the default strategy remains
`legacy`):

```yaml
constraints:
  - forbid: {CAUSESCOPE_CACHE: "on", CAUSESCOPE_FAST: "on"}
invalid_exit_codes: [64]
settings:
  strategy: adaptive
  seed: 17
  candidate_pool: 48
  max_configurations: 80
  max_runs: 300
  max_seconds: 60
  repetitions: 3
  timeout_seconds: 2
```

The forbidden conjunction above is an illustrative constraint; applying it to
the bundled sample excludes its known failure. Values must belong to the stated
parameter domains and the baseline must be allowed. Forbidden configurations are
never executed. A declared invalid exit code is unknown evidence, rather than a
healthy result; it cannot also be the predicate's failure exit code. Constraints
can make a reduction invalid, preventing a 1-minimality claim.

## Results and interpretation

Every observation records argv, effective parameters, temporary working-directory
path, exit code, stdout, stderr, elapsed duration, timeout and launch-error flags,
output truncation, cleanup/termination errors, and predicate outcome. Temporary
directory cleanup is attempted after each run; a failure is recorded and makes
the observation inconclusive. JSON also includes the normalized specification,
all evaluations, confirmation observations, search coverage, and the final diagnosis. HTML includes
the same retained evidence, with target output escaped and no external assets.

During discovery and default repeat confirmation, a configuration reproduces a failure only when **all repetitions match** the
predicate. Mixed results, launch/cleanup/termination errors, and unrequested
timeouts are inconclusive.
Use `failure: {timeout: true}` when timeout itself is the intended failure.
If a searched substring is absent from truncated output, that condition is
unknown. A consistently failing baseline stops diagnosis because there is no
healthy control. The candidate and baseline each receive a fresh batch of
repetitions before any minimal diagnosis is accepted; disagreement with earlier
observations makes the result inconclusive.

The default `legacy` engine tests every individual alternative first, then a lazy Cartesian search
with alternatives before baseline values, stopping at the first reproducible
seed. Including baseline values in that search also finds failures that another
change masks. Delta debugging removes chunks and verifies each immediate
single-change reduction. Only consistently failing reductions are retained;
unknown reductions prevent a minimality claim. Results are **1-minimal under
deletion**, not necessarily globally smallest, unique, or proof of causality.
The prototype diagnoses one failure-inducing set per invocation.

Discovery chooses configurations until it finds a unanimously failing seed;
delta debugging then reduces that seed. These are separate stages, with separate
phase counts in JSON. Optional strategies are `adaptive`, `random`, `pairwise`,
`one_at_a_time` and `brute_force`. All share the same runner, confirmation policy
and minimizer. AllPairs 2.5.1 generates pairwise candidates, with bounded
constraint-witness completion to cover feasible pairs missed by filtering.
Pairwise coverage does not guarantee detection of higher-order or masked failures.
Adaptive selection prioritises uncovered pairs, sampled triple novelty, and
periodic sparse exploration from untested candidates. It uses conclusive evidence
from the current invocation, with a bounded random candidate pool and seeded tie
breaking. It does not know fixture triggers, learn root causes, guarantee full
three-way coverage, or necessarily beat random sampling. Candidate planning is
included in the time budget. Constraint search limits can stop planning with an
explicit inconclusive result.

`max_configurations` counts distinct configurations, including baseline, individual
tests, and reductions. Cached configurations do not spend the budget again.
With the repeat policy, final candidate/control confirmations do not spend distinct-configuration slots;
they add at most `2 * repetitions` subprocess runs. Thus a run uses at most
`(max_configurations + 2) * repetitions` subprocesses. A budget-limited search
without a confirmed minimal diagnosis is inconclusive. Even an exhaustive clean
search establishes only that this predicate was absent in these observations.
`search_complete` means every specified configuration was observed; it does not
imply that every observation was conclusive.
The optional holdout policy uses its separate confirmation caps described below;
the repeat-policy subprocess bound does not apply to it.

`max_runs` optionally caps **all** observations, including partial batches,
minimization and fresh confirmations. `max_seconds` optionally caps cooperative
wall time for planning and execution; each subprocess timeout is shortened to the
remaining allowance. Such a shortened timeout is inconclusive even for a timeout
predicate. OS process termination and cleanup can overrun that wall-time allowance.
Neither limit is enabled by default. Cache reuse is confined to an identical
configuration in the current engine; there is no cross-target or cross-run cache.
`valid_configurations` is the exact public constrained space size when cheaply
enumerable, otherwise null; `total_configurations` retains the raw Cartesian size.
Non-exhaustive clean samples are inconclusive, including completed pairwise/OAT
plans. Run/time exhaustion or incomplete confirmation prevents a diagnosis.

Finite unanimous observations cannot establish permanent reproducibility. The
rare-flake benchmark fails on six successive trigger observations, then passes
on the seventh: the default three discovery plus three confirmation observations
can falsely confirm it. The synthetic fixture illustrates this limitation. An
inconsistent observed batch remains inconclusive; unseen instability cannot be
ruled out by this policy.

## Evidence reliability and optional holdout confirmation


The compatibility policy is `confirmation: {policy: repeat}`. Its existing
unanimous repetitions and fresh three-observation candidate/control checks retain
their original counts. They establish repeated observations, with **no statistical
coverage claim**. Evidence reports separate that claim from probability-based validation.

To request independent fixed-sample probability validation, add this top-level
section (not inside `settings`):

```yaml
confirmation:
  policy: holdout
  samples_per_arm: 64
  alpha: 0.05
  min_failure_probability: 0.9
  min_effect: 0.2
  max_runs: 512
  max_seconds: 60
  seed: 0
```

Discovery and delta debugging are unchanged. One candidate, baseline, and all valid
immediate deletions are frozen before validation. Identical controls are deduplicated.
Every validation call is fresh, in its own temporary working directory; no discovery
or cached observation contributes to its statistics. Balanced arm order is randomized
with the configured seed. Validation records are separate in JSON's `validation.arms`;
`evaluations` retains discovery/minimization records. Total observation/phase counts
include both. Validation's `observation_id` values follow the final discovery ID.

For m unique arms, each Clopper-Pearson binomial interval allocates alpha/(2m) to
each tail. A complete fixed sample supports a finding only if the candidate lower
bound is at least `min_failure_probability` and candidate lower minus baseline upper
is at least `min_effect`. All arms must have the requested number of valid outcomes;
unknown/invalid observations are retained and make validation inconclusive. Mixed
pass/failure outcomes are valid probability data and need not themselves invalidate
the contrast. Candidate/control drift, serial dependence or shared external state
can violate the model even when these checks pass.

Bounds assume fresh observations independent of discovery and stationary IID
Bernoulli outcomes within each arm. Under those assumptions the simultaneous
intervals have at least 1-alpha repeated-sampling coverage for this single frozen
family. This is **not a probability that the conclusion is true**. There is no early
success test, optional extension, validation-driven candidate reselection or retry.
Adaptive discovery is separated from this one held-out family; repeated invocations
and cherry-picked reports have no across-run error control. An operator combining
them needs an additional error allocation or an appropriate sequential procedure.

JSON's `evidence` distinguishes two stages. Discovery is `observed_failure` after
any matching observation, `repeated_failure` after a complete unanimous failure
batch, `inconclusive` when only unknown/no observations exist, or
`no_failure_observed` when valid observations contain no failure. Confirmation is
`not_attempted`, `repeated_failure` for the compatibility policy's accepted batches,
`statistically_supported` for the holdout criteria, or `inconclusive` otherwise.
The overall evidence status uses confirmation when attempted, otherwise discovery.
These labels describe evidence strength; the separate experiment `status` and
reason describe the outcome. A failing baseline is never attributed to a setting.

With holdout, `failure_found` means a supported candidate/control probability
contrast; `diagnosis.one_minimal` may be false. Observed 1-minimality additionally
requires the existing deletion checks, all candidate validation observations failing,
and all fresh baseline/deletion observations passing. Statistical separation alone
does not prove predicate absence on deletions. Neither property proves a global
minimum, permanent reproducibility or an internal root cause. Repeat-policy status
semantics are preserved. The conservative default threshold can substantially
reduce power for intermittent failures; change it only to match the intended claim.

Validation budgets also consume the invocation's total budgets. A partial batch,
exhausted deadline, or previously exhausted configuration budget is inconclusive.
The fixed sample can be too small to meet the requested bounds even when all
observations agree. Time includes analysis, with best-effort OS cleanup overhead.
Sample sizes must be 2..1000; alpha must be >=1e-12 and <1 for numerical stability.
Temporary directories provide file isolation, not statistical independence or a
security sandbox. Execution still requires the explicit local opt-in.

CLI exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Valid specification, confirmed diagnosis, or completed search without failure |
| 2 | Invalid specification, missing execution opt-in, CLI usage, or artifact error |
| 3 | Inconclusive experiment or failing baseline; inspect the exported evidence |
| 130 | Interrupted |

## Execution boundary

`validate` executes nothing. `run` refuses execution unless the specification
declares `scope: local` **and** the operator supplies `--allow-local-execution`.
There is no production mode. Review commands and use test data and disposable
local targets. CauseScope cannot infer whether an arbitrary program contacts a
production service; the opt-in is your acknowledgement of the local-only contract.

Temporary directories isolate working files; they do **not** restrict absolute
paths, network access, credentials, or inherited environment variables. This is
not a security sandbox. Timeouts kill the process group on POSIX. On Windows,
the target starts suspended, joins an owned Job Object, then resumes; timeout,
interruption and normal parent exit clean up the job's descendants. Job setup
failure prevents target execution, and termination/cleanup errors remain
inconclusive. This avoids a dependency on `taskkill` permissions. Processes launched
through external brokers/services are outside these guarantees; use an external
sandbox for untrusted targets. OS cleanup can add a few seconds to the configured
limit. See the [Windows regression verification](WINDOWS_REGRESSION.md).
Output is spooled to disk and retained up to the configured cap; the cap does not
limit the target's disk usage. Reports may contain secrets printed by the target;
review them before sharing.
