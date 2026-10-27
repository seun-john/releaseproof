<p align="center">
  <img src="assets/logo.png" alt="ReleaseProof logo" width="420">
</p>

# ReleaseProof

Build a release ZIP, inspect it safely and smoke-test it in a temporary folder before you publish it.

"It works on my machine" is not a release check. ReleaseProof builds a source ZIP that leaves out secrets and local state, validates any archive for the tricks that make unpacking dangerous, checks that the files you expect are really inside, and (only when you allow it) runs your smoke commands from a throwaway copy. It uses only the Python standard library and makes no network requests of its own.

## Install

Requires Python 3.10 or newer.

```bash
pip install git+https://github.com/seun-john/releaseproof.git
```

## Use

```bash
releaseproof build spec.json --root . --archive dist-check/app-0.1.0.zip -o report.json --strict
releaseproof inspect spec.json --root . -o report.json     # an existing archive; runs nothing
releaseproof test spec.json --root . --allow-execution     # smoke-test an existing archive
```

```json
{
  "expected_files": ["app.py", {"path": "README.md", "min_bytes": 20}],
  "commands": [["{python}", "app.py"]],
  "expected_stdout": {"0": "Hello"},
  "timeout_seconds": 10,
  "exclude_paths": ["notes/*"],
  "package_subdir": ".",
  "artifact_path": "dist-check/app-0.1.0.zip"
}
```

`artifact_path` is used by `inspect` and `test`; `build` creates the archive itself.

## What each step does

**Build** copies the project's files into a new ZIP. It never overwrites an existing file, and it leaves out `.env`, `.env.*`, SQLite databases, other ZIPs and wheels, `reports/`, `.git`, virtual environments, `node_modules`, caches, `dist` and `build`, plus anything matching your `exclude_paths` (`fnmatch` patterns; `*` also crosses `/`). **Read the list of included files before you distribute the archive.** This is not secret detection.

**Inspect** refuses archives that contain traversal paths (`../`, absolute paths, drive letters, backslashes), duplicate or case-colliding names, links or special files, encrypted entries, more than 10,000 entries, or more than 100 MiB unpacked. It then checks every `expected_files` entry for existence, `min_bytes` (default 1) and an optional `sha256`.

**Test** does the same, then runs each command from a temporary copy with no shell, a minimal environment (your API keys and other variables are not passed on), a 1-60 second timeout, and logs capped at 64 KiB with emails, keys and similar values redacted. A timeout stops the whole process tree, not just the first process. `{python}` is replaced with the interpreter running ReleaseProof; `{root}` with the working folder. For a `.whl`, the wheel is installed with `pip --no-index --no-deps` into a fresh virtual environment and the commands run from an empty folder, so you test the installed package rather than the extracted source.

Commands only run when you use `test` (or `build`) **and** pass `--allow-execution`. Otherwise the report says `NOT_EXECUTED`.

**Content checks** run on the unpacked release, never execute anything, and are listed under `checks` in the spec:

```json
{"checks": [
  {"check": "json_valid"},
  {"check": "max_file_bytes", "bytes": 102400},
  {"check": "no_todo_markers", "extensions": [".py", ".js"]},
  {"check": "no_hardcoded_credentials", "placeholders": ["acme-demo"]},
  {"check": "no_absolute_paths", "under": "config"},
  {"check": "dependencies_pinned", "file": "requirements.txt"},
  {"check": "license_matches", "metadata": "datapackage.json", "license_file": "LICENSE"},
  {"check": "version_matches_changelog", "metadata": "pyproject.toml", "changelog": "CHANGELOG.md"},
  {"check": "row_counts_match", "metadata": "datapackage.json"}
]}
```

| Check | Passes when |
| --- | --- |
| `json_valid` | every `.json` file parses (a byte order mark is accepted) |
| `max_file_bytes` | no file is larger than `bytes` |
| `no_todo_markers` | no `TODO`, `FIXME` or `XXX` in files with the listed extensions (default: `.py .js .ts .jsx .tsx .mjs .cjs`); prose files are not looked at |
| `no_hardcoded_credentials` | no private key, JWT, prefixed token (`sk-`, `ghp_`, `AKIA`...), `Authorization: Bearer` value, or `password` / `secret` / `token` / `api_key` assignment whose value is 8+ characters with a digit or symbol and is not a placeholder (`changeme`, `example`, `sample`, `<...>`, `${...}`, `****`, and any word you list under `placeholders`). The report names the file and line, never the value |
| `no_absolute_paths` | no file under `under` contains `C:\...`, `/home/...`, `/Users/...` or `/root/...` |
| `dependencies_pinned` | every requirement is pinned with `==` (comments, `-r` and option lines are skipped) |
| `license_matches` | the licence the metadata declares (JSON `license` or `licenses`, TOML or YAML `license`, or an SPDX id in a `copyright` line) is the licence the text of `license_file` reads as. It recognises MIT, Apache-2.0, BSD-2/3-Clause, GPL-2.0/3.0, LGPL-3.0, MPL-2.0, ISC, Unlicense, CC0 and CC-BY variants |
| `version_matches_changelog` | the metadata `version` equals the newest version heading in the changelog (`[Unreleased]` has no version and is skipped) |
| `row_counts_match` | every resource in `datapackage.json` that states `rows` has that many data rows in its CSV (blank lines do not count) |

Each check gives `PASS` or `FAIL`. When its inputs are missing or not recognised (no changelog, a licence text it cannot identify, a folder that is not in the release) it gives `UNCHECKED` with the reason, which keeps `release_ready` false and counts as undecided in the coverage. The credential and licence checks are heuristics: a clean result means nothing matched, not that the release is safe, and an unusual licence text will come back `UNCHECKED` rather than guessed.

`release_ready` is true only when files were expected, commands were configured and run, and every finding passed. It means "these checks passed on this computer". It is not a guarantee of correct behaviour or of compatibility with other systems.

| Finding | Meaning |
| --- | --- |
| `BUILT`, `PASS` | The step succeeded. |
| `FAIL`, `TIMEOUT` | A file check, command or output assertion failed, or a command ran out of time. |
| `NOT_EXECUTED`, `NO_SMOKE_TESTS`, `NO_EXPECTATIONS` | Nothing was run or required, so the release cannot be called ready. |
| `UNCHECKED` | A content check could not run because its inputs were missing or not recognised. |

All commands take `-o report.json`, `--html report.html` and `--strict` (exit 1 on anything other than `PASS` or `BUILT`). Exit codes: 0 completed, 1 strict findings, 2 unusable input or an unsafe archive, 3 `--min-coverage` was not met.

**Coverage.** The report has a `coverage` object and a line on stderr, for example `coverage: decided 5 of 6 checks; 1 could not be checked (not_executed 1)`. The items are the required files plus the smoke commands. Required files are always checked. A smoke command is decided only if it ran and finished: one that was not run (`not_executed`, because you did not pass `--allow-execution` or used `inspect`), could not run because the wheel failed to install (`install_failed`), or timed out (`timed_out`) is undecided. A command that ran and failed is decided. The `notes` say when no required files or no commands were configured. `--min-coverage PCT` exits 3 when fewer than PCT percent of items were decided and nothing else failed.

## Limits

- **A temporary folder is not a sandbox.** Package code can read anything your account can read and can use the network. Only run `test` on archives you trust.
- Only source ZIPs are built. Wheels can be tested but not built here.
- Tests run on the current computer and Python version only.
- Wheel testing needs `venv` and `ensurepip`. Some minimal Linux Pythons need their `python3-venv` package.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && pytest -q
```

MIT licence.


## Research paper

This tool was evaluated, with and without each tool, in an open preprint: Oluwawalemi, O. J. (2026). Do Verification Tools Improve on an AI Reader? A With-and-Without Evaluation of Fifteen Open-Source Tools for AI-Assisted Research. Zenodo. https://doi.org/10.5281/zenodo.23404947

The test corpora, truth labels, verdicts and scripts are in the same Zenodo record. Independent replication and testing are welcome. Please open an issue to share results.
