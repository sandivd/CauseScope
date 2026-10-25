# Windows process cleanup checks

`tests/test_windows_job.py` and the real subprocess tests in `tests/test_runner.py`
exercise Job Object setup, fail-closed containment, timeout, descendant cleanup,
interruption and termination errors. Statistical/engine tests also cover partial
or cleanup-failed observations remaining inconclusive.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_windows_job.py tests/test_runner.py -q
```

CauseScope starts Windows targets suspended, assigns an owned Job Object and then
resumes them. Job close terminates members; successful cleanup checks accounting
for remaining processes. It does not depend on `taskkill` to contain targets.
Processes launched through external brokers/services remain outside this guarantee.
Temporary directories provide file isolation, not a security sandbox.

See [PUBLICATION_STATUS.md](PUBLICATION_STATUS.md) for checks executed on the public
alpha snapshot. Historical machine logs and old validation counts are not shipped.
