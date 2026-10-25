# Coverage.py threshold guard

The adapter maps two environment values to native Coverage.py branch measurement
and a fail-under threshold. The input and four-cell behavior table test an intended
coverage guard, not an upstream defect. `target.lock.json` pins the Windows AMD64 /
CPython 3.10 wheel, URL, archive checksum and installed module hashes.

From the source root with `.[dev,mcp]` installed:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.developer_workflow.coverage_case.setup --allow-download
$env:CAUSESCOPE_RUN_COVERAGE_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_coverage_workflow.py -q
```

Setup writes only ignored `.tools/` content. The tests compare the native outcomes
with a separately approved service investigation. Unsupported configurations are
explicit errors. Historical logs and model transcripts are excluded. The fixture
does not establish real-world performance or an advantage over Codex.
