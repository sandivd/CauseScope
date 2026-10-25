# Public snapshot publication policy

This directory is a standalone public source snapshot. Do not import the private
repository's Git history, evaluation answers, transcripts or historical run output.
Do not publish generated reports without reviewing their retained output and paths.

`publication-files.json` lists every intended public source file: `sdist` contains
package source-archive inputs and `source_additions` contains repository-only files,
including tests, benchmark source and GitHub Actions. `MANIFEST.in` resets automatic
setuptools selection and explicitly includes only the `sdist` list. The wheel
contains only the package, license and packaging metadata. New files require review
and an update to the appropriate lists.

The source audit walks the complete directory, rejects unlisted source files,
links/junctions and nested version-control metadata, and scans approved contents for
recognizable credentials and personal paths. It explicitly lists excluded local
verification directories such as `.venv`, `.tools`, `results`, `build` and `dist`;
these are not public source. Ignore rules alone are not publication approval.
Root version-control metadata is excluded in a normal checkout. Add
`--require-no-vcs` to verify that a standalone snapshot contains none.

```sh
python tools/publication_audit.py --source --sdist dist/causescope-0.1.0a0.tar.gz --wheel dist/causescope-0.1.0a0-py3-none-any.whl --output results/publication-audit.json
```

The report path must be new. Archive inspection is bounded, does not extract or
execute contents, rejects unapproved members and fails closed at size limits.
Matched credential values are omitted from reports. A clean heuristic scan cannot
prove the absence of every secret or authenticate third-party artifacts.

The public tag is `v0.1.0-alpha`; Python uses its PEP 440 prerelease equivalent
`0.1.0a0`. Source installation does not depend on a Git checkout or private target.
No PyPI publication is assumed. MIT text and attribution ship in both distributions.
Third-party applications are downloaded separately from locked URLs and are never
vendored into the source tree or CauseScope distributions.

Before release, run tests, build both distributions, rebuild from the source
archive, check metadata, install into fresh environments, run the bundled example
and MCP protocol checks, and inspect the exact intended public files. Record only
executed checks in [PUBLICATION_STATUS.md](PUBLICATION_STATUS.md). GitHub-hosted
matrix results remain pending until run in the new repository.
