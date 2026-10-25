# Reproducible benchmark source

These repository-only helpers are excluded from wheel and source distributions.
Generated output and downloaded programs belong under ignored `results/` and
`.tools/`, not in published source. Historical evidence and the unfinished agent
A/B evaluation suite are not included.

- [Synthetic discovery fixtures](discovery/README.md): seeded generator, simulation,
  subprocess runner and result audit. These are openly specified synthetic cases,
  not private evaluation questions or an agent comparison.
- [Git line-ending guard](git_eol/README.md): locked Windows Git distribution,
  isolated local fixture repositories and native configuration interaction.
- [Historical Black regression](black_regression/README.md): locked affected/fixed
  Windows wheels with independent AST checks.
- [Coverage.py threshold guard](developer_workflow/coverage_case/README.md): locked
  Windows wheel with a four-cell native behavior check and service workflow test.

Run setup only when you intend to download third-party targets. Pins and hashes
are verified before use. No third-party binaries are bundled in CauseScope.
Interpret narrow fixtures as behavior checks; they do not establish CauseScope
performance or superiority over Codex.
