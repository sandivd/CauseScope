# Your first CauseScope investigation (Windows PowerShell)

Use a disposable local target and test data. CauseScope executes arbitrary local
argv with your inherited environment; temporary directories are **not security
sandboxes**. It cannot detect whether a program contacts production services.

Install standard CPython 3.10+ with the Windows `py` launcher, clone/download this
repository and open PowerShell in its root. No activation or execution-policy
change is needed. Upgrade pip before installing; see [installation](../README.md#install-from-this-source-snapshot).
Use fresh output paths on subsequent runs.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install ".[mcp]"
.\.venv\Scripts\python.exe -m causescope validate .\examples\interaction.yaml
.\.venv\Scripts\python.exe -m causescope run .\examples\interaction.yaml --allow-local-execution --max-runs 100 --max-seconds 60 --output .\results\quickstart-first
.\.venv\Scripts\python.exe -m causescope show .\results\quickstart-first\evidence.json
.\.venv\Scripts\python.exe -m causescope reproduce .\results\quickstart-first\evidence.json --allow-local-execution --output .\results\quickstart-reproduction.json
```

The first investigation uses 18 observations and finds `CAUSESCOPE_CACHE=on`
together with `CAUSESCOPE_FAST=on`. Reproduction collects 12 fresh baseline,
candidate and deletion observations. These are expected regression counts, not performance guarantees. The bundled
sample is synthetic; pinned external integrations are documented
[separately](../benchmarks/README.md).

Progress goes to stderr and shows completed observations, unique configurations,
and remaining configuration/observation/time budgets. Confirmation consumes the
observation/time budgets while reusing configuration slots. `--quiet` suppresses
progress. The final output prints HTML/JSON paths and a PowerShell reproduction
command. Open the printed `report.html` path in your browser; no web server is
needed. JSON contains every retained exit, output, timeout and cleanup result.

To investigate your own target, run the guided creator:

```powershell
.\.venv\Scripts\python.exe -m causescope init .\results\my-investigation.yaml
```

Give the executable, then each argument separately (blank ends argv). For Python,
use `{python}` and `{spec_dir}/target.py` for a script beside the YAML, or an
absolute script path. Relative paths otherwise resolve in a fresh temporary
directory; project files are not copied automatically. Define an environment
variable, its literal baseline, and alternatives as JSON such as `["on", "auto"]`.
Repeat for more variables, then give a detectable failure exit and/or stderr
substring. Conditions combine with AND. CLI flags require an existing wrapper
that maps environment values to flags; CauseScope does not guess that mapping.

The creator writes ordinary v1 YAML and executes nothing. `validate` lists the
normalized definitions; `validate --json` provides structured output. Edit YAML
to add forbidden conjunctions, `invalid_exit_codes`, or detailed confirmation
settings. The [schema rules](SPECIFICATION.md#specification-rules) remain compatible.

| Evidence status | What it establishes |
| --- | --- |
| `observed_failure` | At least one discovery observation matched the predicate. |
| `repeated_failure` | Complete unanimous failure batches; accepted repeat confirmation also requires fresh candidate/control batches. No statistical coverage claim. |
| `statistically_supported` | The opt-in holdout's fixed-sample simultaneous bounds met the specified probability/effect criteria under its assumptions. |
| `no_failure_observed` | No matching valid observations in this stage; untested configurations may still fail. |
| `inconclusive` | Unknown, inconsistent, insufficient or incomplete evidence. Inspect the reason and raw observations. |

Read the separate investigation `status` too: a two-observation budget can produce
`status: inconclusive` with `evidence.status: no_failure_observed`. A consistently
failing baseline cannot attribute a change. `diagnosis.confirmed` and
`diagnosis.one_minimal` are separate. Observed 1-minimality checks each immediate
deletion; it does not certify the smallest combination anywhere in the space or
prove an internal root cause. Reproduction is a fresh descriptive check, not an
extension of fixed-sample holdout statistics.

`run --reliability repeat` keeps the established policy. `--reliability holdout`
opts into the existing independent family, preserving all other YAML confirmation
settings. The default holdout uses 64 observations per distinct arm, alpha .05,
candidate failure probability lower bound >=.9 and candidate lower minus baseline
upper >=.2. It may be expensive or inconclusive even after unanimous outcomes.
See [exact criteria and assumptions](SPECIFICATION.md#evidence-reliability-and-optional-holdout-confirmation).
Neither policy is universally superior, and zero false confirmations in one
benchmark does not guarantee zero future false confirmations.

For Codex, follow the [local MCP setup](MCP.md). Describe the target, local input,
controllable settings, expected baseline, detectable predicate and acceptable
budget. Ask Codex to validate and prepare a plan first. Review the concrete plan,
issue its approval outside MCP, then let Codex start and inspect that exact plan.
Do not ask it to infer root cause from a setting combination.

Common errors:

- Missing `--allow-local-execution`: no experiment runs; add it only after review.
- Unknown YAML key or duplicate name: fix the displayed field. Parameters are
  environment variables with alternatives excluding baseline. Quote YAML `on/off`.
- Missing target/file: inspect recorded argv and launch errors; use absolute paths
  or `{spec_dir}`. Inspect runtime metadata for platform-specific pins.
- Nonzero exit without a predicate match: this is predicate absence, not program
  health. Inspect the warning/raw stderr and declare known unsupported/invalid
  exits. CauseScope does not invent an error taxonomy for unfamiliar programs.
- Existing artifact: choose a fresh directory. Ordinary CLI `run --overwrite` is
  explicit; MCP runs never overwrite.
- Inconclusive budget/holdout: inspect untested configurations and unmet bounds.
  A new investigation is a new statistical family; do not cherry-pick retries.
- Command output may contain secrets. Review artifacts before sharing. Interrupted
  processes or infrastructure errors do not establish absence of a defect.
