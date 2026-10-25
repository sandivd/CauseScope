# CauseScope architecture

CauseScope separates specification, execution, search, confirmation and reporting.
The CLI and optional MCP service use the same experiment engine.

| Module | Responsibility |
| --- | --- |
| `spec.py` | Validate YAML, parameter domains, predicates, constraints and budgets. |
| `runner.py`, `windows_job.py` | Execute argv without a shell, isolate working files, capture bounded output and clean up process trees. |
| `selection.py` | Plan legacy, adaptive, random, pairwise, one-at-a-time and brute-force candidates using public domains and current observations. |
| `engine.py`, `minimize.py` | Establish a baseline, discover a failing seed, reduce it and check immediate deletions within budgets. |
| `confirmation.py`, `confidence.py` | Recheck fresh candidate/control observations or analyze a fixed holdout family with simultaneous binomial bounds. |
| `report.py`, `workflow.py` | Export HTML/JSON, summarize evidence and reproduce candidate/control/deletion observations. |
| `service.py`, `mcp_server.py` | Freeze plans, consume one-use CLI approvals, run a background worker and expose local stdio tools. |
| `cli.py`, `wizard.py` | Command-line interface and guided specification creation. |

The default repeat policy requires complete unanimous batches. Unknown or mixed
outcomes do not establish a failure. The engine caches identical configurations
only within one invocation; confirmation collects fresh observations. The optional
holdout policy freezes its candidate and arms before collecting validation data.
See the [specification reference](docs/SPECIFICATION.md) for exact criteria.

Observations retain argv, configuration, working directory, exit, output, timing,
truncation and cleanup errors. Reports escape target output and load no remote
assets. A predicate match is a measured outcome; predicate absence is not a claim
that every aspect of the target succeeded.

Execution requires `scope: local` and an explicit operator opt-in. MCP also requires
an approved frozen plan. This is an accident-prevention boundary within one OS
user account. Targets inherit that account's environment and permissions. Temporary
directories do not restrict network or absolute paths and are not security sandboxes.

Windows targets start suspended, enter an owned Job Object, then resume. Failed
containment prevents execution; timeout, interruption and parent exit trigger
cleanup. POSIX uses process groups. Cleanup failures remain inconclusive. External
brokers and escaped processes need stronger isolation supplied by the operator.

`src/causescope` contains the installable package. `tests` holds regression tests;
`benchmarks` holds source-only synthetic generators and checksum-pinned external
adapters. Generated outputs, downloaded applications, old agent experiments and
historical run evidence are excluded from public source and package archives.
