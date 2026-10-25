# Git line-ending safety interaction

This fixture exercises Git's documented `core.autocrlf` and `core.safecrlf`
interaction on LF-only input. It targets an intended safety guard. Two additional
settings are distractors for reduction. The adapter creates disposable local
fixture repositories, disables system/global configuration, and forwards native
`git add` exit/output. It does not clone, commit, push or contact a remote.

`target.lock.json` pins MinGit 2.45.2 for Windows x86-64, its upstream ZIP and
executable hashes. Setup installs only under `.tools/`; it does not replace system
Git. From the repository root with `.[dev]` installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\benchmarks\git_eol\setup.ps1
$env:CAUSESCOPE_RUN_GIT_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_git_eol.py -q
```

The integration checks the four native setting combinations, engine reduction,
exhaustive and one-at-a-time controls, CLI reports, pin verification and cleanup.
Missing pins fail once opted in. Historical generated evidence is not included.
Narrow fixture results do not establish broad discovery performance or Codex value.

Sources: [Git v2.45.2 configuration source](https://github.com/git/git/blob/v2.45.2/Documentation/config/core.txt)
and [MinGit release](https://github.com/git-for-windows/git/releases/tag/v2.45.2.windows.1).
