# Genuine historical Black defect

This benchmark reproduces upstream [Black #5272](https://github.com/psf/black/pull/5272),
using the published input with unmodified Black **26.5.1**, and verifies the repair
with **26.10.0**. It tests `--line-length 40` interacting with
`--skip-magic-trailing-comma`. In the affected release, the normal AST safety check
rejects a changed subscript interpretation (native exit 123); `--fast` writes an
AST-changing result. The fixed release passes the same five native checks.
Historical generated evidence is excluded; run the opted-in integration below
to check the pinned fixture on your machine.

The independent reproduction runs **before** CauseScope. It checks healthy
controls, the affected safe-mode failure, fast-mode AST corruption, and fixed
release AST equivalence. Only after those checks pass does it ask CauseScope to
search the two-setting public space. Its predicate is the native exit 123 and
`not equivalent` stderr, with no planted failure or altered external source.
Exit 70 from the adapter declares an unsupported reproduction and is invalid
evidence. The earlier Git benchmark is an intentional guard, not this defect.

`target.lock.json` records the exact upstream wheel URLs/SHA-256 values and all
installed Python/native module hashes for both versions and their dependencies.
The current artifacts require **Windows AMD64 / CPython 3.10**. Other platforms
and releases are unsupported by this lock. Downloads are explicit and wheels
are installed only into ignored `.tools/black-*` directories. The venv's Black
installation is not used. Pip installation requires a trusted local Python/pip;
checksum validation does not turn execution into a security sandbox.

From the repository root, using a CPython 3.10 venv with CauseScope installed:

```powershell
.\.venv\Scripts\python.exe -m benchmarks.black_regression.setup --allow-download
.\.venv\Scripts\python.exe -m benchmarks.black_regression.reproduce --allow-local-execution --output .\results\black-historical
$env:CAUSESCOPE_RUN_BLACK_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m pytest -q tests/integration/test_black_regression.py
```

Setup without `--allow-download` verifies already acquired artifacts and refuses
missing wheels. Output directories must be fresh. Native commands use an explicit
empty Black config, isolated cache/home/temp paths and a fixture copy in a fresh
temporary working directory. Native timeouts are 10 seconds; outer observations
are capped at 15 seconds. The fixture source, commands, outputs, AST comparison,
input/output hashes, host, lock hash and engine observations are retained. The
environment still inherits OS variables and executable dependencies. Temporary
directories isolate files; they are not a security sandbox.

This validates one real defect, one published input and one platform. CauseScope's
observed 1-minimal setting pair identifies a failure-inducing combination relative
to the healthy baseline, not an internal root cause. It provides no competitive
discovery evidence for large real-world configuration spaces; that remains a
limitation of this evaluation.
