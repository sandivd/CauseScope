# GitHub Actions

`.github/workflows/tests.yml` runs on pushes to `main`, pull requests targeting
`main`, and manual dispatch. It uses read-only repository contents permissions,
standard hosted runners and public dependencies. No repository secret, private
repository, old commit, local virtual environment or preinstalled target is needed.

The primary matrix tests Ubuntu and Windows with CPython 3.10 and 3.13, installing
`.[dev,mcp]` before the coverage-enabled default suite. A separate Ubuntu job builds
and audits the wheel/source archive, checks metadata and runs the sample from a
fresh wheel environment.

Three Windows/Python 3.10 jobs explicitly download checksum-pinned Git, Black and
Coverage.py targets into `.tools`, then enable their respective integration tests.
The Black and Coverage.py locks require Windows AMD64 / CPython 3.10. Network
availability and continued upstream artifact access are required for these jobs.
Default tests leave those external integrations skipped until opted in; after
opt-in, missing or mismatched artifacts fail.

All source inputs are in this snapshot. Historical agent-evaluation tests and
archived-evidence assertions are excluded with their development-only data.
Local verification and unexecuted platforms are listed in
[PUBLICATION_STATUS.md](PUBLICATION_STATUS.md). Workflow review or local execution
cannot certify a hosted GitHub Actions run; confirm every job in the fresh repository.
