# v0.1.0-alpha public-release readiness

Verified on 25 October 2026 (Australia/Sydney). Public tag: `v0.1.0-alpha`;
Python package version: `0.1.0a0`.

**Local release preparation passed. Hosted CI confirmation remains pending.**
This report covers this standalone public snapshot only. The original private
repository was not accessed or modified. No Git initialization, commit, push,
repository creation or release publication was performed.

## Publication boundary and cleanup

The initial recursive inventory found no Git metadata or history. It did find
historical generated benchmark evidence and raw workflow transcripts, so the
initial snapshot was unsuitable for publication. Those outputs, the unfinished
agent-evaluation suite, frozen development engine copies and their dependent
historical assertions were excluded from this public copy. Files removed during
cleanup were first preserved and verified byte-for-byte in a temporary recovery
archive outside the snapshot. No private repository or backup was accessed.

The final reviewed public inventory is **93 files, under 0.5 MiB**, including all
source, tests, examples, documentation, manifests and the GitHub workflow. The
complete source audit rejects unlisted files and links, rather than checking only
a partial allowlist. The standalone snapshot check requires no Git metadata.
Local environments, downloaded targets, build artifacts and raw verification logs
are explicitly excluded from publication and remain ignored.

No credentials, private evaluation answers, raw transcripts, personal filesystem
paths or generated run evidence were found in the intended public files or built
package archives. Recognizable-pattern scanning is heuristic; this is a bounded
review, not proof that every possible secret format can be detected. Scanner tests
use invented credential-shaped values, not real credentials.

## Executed verification

All runtime checks below ran on Windows AMD64. No historical development counts
are used as evidence for this release.

| Check | Executed result |
| --- | --- |
| CPython 3.10.0 editable `.[dev,mcp]` installation and `pip check` | Passed. |
| CPython 3.13.1 editable `.[dev,mcp]` installation and `pip check` | Passed in a separate fresh environment. |
| Coverage-enabled default suite on Python 3.10.0 | **360 passed, 20 skipped**, 93% package coverage. |
| Coverage-enabled default suite on Python 3.13.1 | **360 passed, 20 skipped**, 93% package coverage. |
| Opted-in pinned Black/Coverage.py integrations plus publication regressions | **37 passed**; downloaded target and installed-code checksums verified. |
| Final publication regression suite, including fresh-checkout metadata handling | **28 passed**, including real source-archive exclusion and wheel rebuild checks. |
| Wheel and source distribution builds | Passed using `python -m build --no-isolation`. |
| Distribution metadata | Both passed `twine check`; package/runtime versions agree; alpha classifier, Python minimum, entry point and optional MCP dependency verified. |
| MIT license | Standard MIT grant, conditions and disclaimer retained; attribution retained; identical LICENSE bytes verified in both artifacts. Metadata declares `License-Expression: MIT`. |
| Fresh core wheel, source-archive and MCP-extra installations | Each installed offline in a separate environment, passed `pip check`, CLI version/spec validation, the 18-observation sample and 12-observation fresh reproduction. No pytest, build or twine in these runtimes; core/source installs contain no MCP SDK. |
| Actual wheel experiment helper | Passed repeat confirmation (18 observations) and holdout confirmation (60 observations), including expected diagnosis/minimality and `statistically_supported` holdout evidence. |
| Clean-wheel SDK stdio integration | Seven tools enumerated; unapproved execution refused; out-of-band CLI approval consumed; completed the expected 18-observation investigation and returned findings/evidence. |
| Complete source and archive publication audits | Passed with zero findings; exact source/archive membership checked against the explicit policy. |
| Documentation links | Local Markdown file targets checked; none broken. README specification matches the shipped example. |
| GitHub Actions syntax | Actionlint 1.7.12 passed; binary verified against its official release checksum. Optional shellcheck/pyflakes were unavailable and disabled. |

The two full-suite runs intentionally skipped 20 external integration tests:
10 Git, 4 Black and 6 Coverage.py. The Black and Coverage.py cases subsequently
passed in the separate opted-in run. The final publication suite includes one
additional checkout-boundary regression added after those full-suite runs.

Build outputs are `dist/causescope-0.1.0a0.tar.gz` and
`dist/causescope-0.1.0a0-py3-none-any.whl`. They are local verification artifacts,
not source files or published release assets. Raw commands, logs, JSON audits and
JUnit records are under ignored `results/release-checks/`; these can contain local
paths and must not be included in the public repository.

## Fresh-repository workflow review

The workflow targets pushes to `main`, pull requests into `main`, and manual
dispatch. Repository permissions are read-only. Its Ubuntu/Windows, Python
3.10/3.13 matrix installs declared dependencies from source; a packaging job checks
both artifacts and exercises a clean wheel install. Dedicated Windows jobs acquire
their pinned Git, Black and Coverage.py targets. No private repository, secret,
old development commit or historical evidence file is required. Root `.git`
metadata from Actions checkout is explicitly excluded by the normal audit;
`--require-no-vcs` remains available for this standalone snapshot.

## Remaining release gates and limitations

- **Hosted GitHub Actions has not run in the new repository.** Native Ubuntu and
  macOS were not executed locally. Confirm every hosted matrix, packaging and
  integration job after the owner creates the repository; syntax validation and
  Windows runs cannot certify hosted success.
- **Git integration was not executed locally.** Its fixture setup initializes
  temporary Git repositories, so it was left unrun to honor the no-initialization
  instruction. The fresh-repository Git job still needs confirmation.
- Persistent Codex/IDE MCP registration was not exercised. The configuration
  template was checked against official documentation, and real SDK stdio behavior
  was tested. Client-specific setup remains an installation check.
- The Codex-only versus Codex + CauseScope A/B evaluation remains incomplete.
  No speedup, success-rate advantage, equivalence or superiority claim follows.
  Synthetic fixtures and narrow external behaviors do not establish representative
  real-world performance. Alpha APIs/evidence formats may change.

There are no known failing local checks after the documented fixes. Release
approval remains with the owner; this preparation performs no publication action.
