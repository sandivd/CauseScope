# CauseScope

**Find and reduce failure-inducing configuration combinations in disposable local programs.**

CauseScope is an MIT-licensed Python CLI. Give it a command, environment-variable
parameters and a failure predicate. It checks a baseline, searches combinations,
reduces a reproducible failure with delta debugging, and exports the observations
as JSON and a standalone HTML report.

This is the first public alpha, **v0.1.0-alpha** (Python package version
`0.1.0a0`). APIs, evidence formats and behavior may change. The Codex-only versus
Codex + CauseScope A/B evaluation remains incomplete; no performance or
superiority claim is established.

## Capabilities

- Strict YAML specifications with environment-variable domains, forbidden
  combinations, explicit invalid outcomes, and configurable failure predicates.
- Six search strategies: legacy, adaptive, random, pairwise, one-at-a-time and
  brute force. All share repetition, reduction and confirmation rules.
- Budgets for configurations, observations and elapsed time; explicit
  inconclusive outcomes when evidence or budgets are insufficient.
- Fresh candidate/control confirmation, optional fixed-sample holdout bounds,
  and fresh reproduction from saved evidence.
- A guided specification creator, progress reporting, and local stdio MCP tools
  for preparing, approving and inspecting bounded investigations.

The core has no LLM API dependency or telemetry. Targets run as subprocesses;
CauseScope does not import or modify their source.

## Install from this source snapshot

Use CPython **3.10 or later**. Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
```

Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
```

Install `'.[mcp]'` for MCP integration or `-e '.[dev,mcp]'` for development.
Activation is optional: use the environment's Python as shown. This alpha does
not assume a PyPI publication; install from the source directory or a locally
built wheel. Core dependencies are PyYAML and AllPairs; the optional MCP SDK is
pinned separately.

## Run the included example

From the source root on Windows:

```powershell
.\.venv\Scripts\python.exe -m causescope validate examples/interaction.yaml
.\.venv\Scripts\python.exe -m causescope run examples/interaction.yaml --allow-local-execution --output results/interaction
.\.venv\Scripts\python.exe -m causescope show results/interaction/evidence.json
.\.venv\Scripts\python.exe -m causescope reproduce results/interaction/evidence.json --allow-local-execution --output results/reproduction.json
```

On Linux/macOS, replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`.
Use fresh output paths on subsequent runs. Open `results/interaction/report.html`
in a browser; no server is needed.

The synthetic target passes at baseline and with either setting alone. It prints
`interaction bug` and exits with code 23 when both are enabled. The expected
result is `failure_found`, both settings in `diagnosis.changes`,
`diagnosis.confirmed: true`, and `diagnosis.one_minimal: true`. The regression
suite checks 18 investigation observations and 12 fresh reproduction observations.
See the [release verification report](docs/PUBLICATION_STATUS.md) for checks
actually executed on this snapshot.

```yaml
version: 1
name: Two-option interaction bug
scope: local
command: ["{python}", "-m", "causescope.sample_app"]
parameters:
  CAUSESCOPE_CACHE:
    baseline: "off"
    values: ["on"]
  CAUSESCOPE_FAST:
    baseline: "off"
    values: ["on"]
failure:
  exit_code: 23
  stderr_contains: "interaction bug"
settings:
  timeout_seconds: 3
  repetitions: 3
  max_configurations: 32
```

`command` is an argv list, executed without a shell in a fresh temporary working
directory. Parameters override inherited environment variables. Failure conditions
combine with AND. Quote YAML values such as `"on"` and `"off"`.
For a script beside your YAML, use `["{python}", "{spec_dir}/target.py"]`;
relative paths otherwise resolve inside the temporary working directory.

Use `python -m causescope init results/my-investigation.yaml` to create a spec
interactively, and `python -m causescope --help` for all commands. See the
[quickstart](docs/QUICKSTART.md) and [specification/evidence reference](docs/SPECIFICATION.md)
for constraints, budgets, status interpretation, holdout assumptions and exit codes.

## MCP integration

Install `'.[mcp]'`, then configure a local stdio MCP client to launch the installed
interpreter with:

```text
-m causescope mcp --workspace <absolute-investigation-directory>
```

This starts in planning-only mode. Execution also needs
`--allow-local-execution` on the server and a separate, matching one-use approval
issued through the CLI after reviewing a frozen plan. MCP exposes capabilities,
validation, planning, start, progress, evidence and findings tools; it has no
approval tool. The [MCP guide](docs/MCP.md) includes a Codex configuration template
and the complete approval flow. Protocol tests exercise real SDK stdio clients;
client-specific registration must be checked in your own installation.

## Limitations and execution boundary

- A result identifies a failure-inducing combination under the supplied predicate.
  Observed 1-minimality under deletion does not establish the globally smallest
  combination, a unique answer or an internal root cause.
- Configuration spaces grow exponentially. Partial searches, invalid outcomes,
  drift, mixed observations and exhausted budgets can remain inconclusive.
- Finite repeated observations can miss intermittent behavior. Holdout bounds
  require fresh, stationary IID observations and apply to one frozen family;
  repeated cherry-picked runs have no across-run error control.
- **Temporary directories are not security sandboxes.** Targets inherit the
  caller's environment and OS permissions, including network and filesystem
  access. Use reviewed commands, disposable local targets and test data.
- Process cleanup and elapsed-time limits depend on OS behavior. External
  brokers/services are outside process-tree guarantees; output caps limit
  retained output, not target disk use.
- Reports retain target output and local execution paths. Review generated
  artifacts before sharing. Do not publish credentials or private evaluation data.

Git line-ending and Coverage.py threshold integrations exercise intended guards;
the Black integration targets a historical defect. These narrow cases and the
synthetic example do not establish representative real-world performance or
improvement over Codex.

## Develop and verify

```sh
python -m pip install -e '.[dev,mcp]'
python -m pytest --cov=causescope --cov-report=term-missing
python -m build --no-isolation
python -m twine check dist/*
python tools/publication_audit.py --source --sdist dist/causescope-0.1.0a0.tar.gz --wheel dist/causescope-0.1.0a0-py3-none-any.whl
```

The default tests require no external target downloads. Pinned Windows integration
tests need separate setup and explicit opt-in; see [benchmarks](benchmarks/README.md).
The [CI guide](docs/CI_MATRIX.md) describes the Windows/Linux matrix and packaging
checks. A configured CI job is not evidence of a successful hosted run.

See [ARCHITECTURE.md](ARCHITECTURE.md), [publication policy](docs/PUBLICATION.md),
[release notes](RELEASE_NOTES.md), and [LICENSE](LICENSE). This public source snapshot
excludes private evaluation material and historical generated evidence.
