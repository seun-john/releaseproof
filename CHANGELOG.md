# Changelog

## Unreleased

- New `checks` list in the spec: nine content checks on the unpacked release (`json_valid`, `max_file_bytes`, `no_todo_markers`, `no_hardcoded_credentials`, `no_absolute_paths`, `dependencies_pinned`, `license_matches`, `version_matches_changelog`, `row_counts_match`). Each gives `PASS` or `FAIL`, or the new `UNCHECKED` when its inputs are missing or not recognised; `UNCHECKED` keeps `release_ready` false and counts as undecided (`inputs_missing`) in the coverage.
- Existing specs behave as before: without `checks` the report is unchanged.
- The report has a `coverage` block (required files and smoke commands decided or not; reasons `not_executed`, `install_failed`, `timed_out`), a one-line summary on stderr and in the HTML report.
- `--min-coverage PCT`: exit 3 when fewer than PCT percent of items were decided and nothing else failed.

## 0.1.0

First release.

- `build`: create a source ZIP that leaves out secrets, databases, archives and reports.
- `inspect`: validate an archive (path traversal, links, duplicates, encryption, size limits) and check expected files.
- `test`: with `--allow-execution`, run smoke commands from a temporary copy (or a fresh virtual environment for a wheel); timeouts stop the whole process tree.
