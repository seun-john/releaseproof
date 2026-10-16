# Changelog

## Unreleased

- The report has a `coverage` block (required files and smoke commands decided or not; reasons `not_executed`, `install_failed`, `timed_out`), a one-line summary on stderr and in the HTML report.
- `--min-coverage PCT`: exit 3 when fewer than PCT percent of items were decided and nothing else failed.

## 0.1.0

First release.

- `build`: create a source ZIP that leaves out secrets, databases, archives and reports.
- `inspect`: validate an archive (path traversal, links, duplicates, encryption, size limits) and check expected files.
- `test`: with `--allow-execution`, run smoke commands from a temporary copy (or a fresh virtual environment for a wheel); timeouts stop the whole process tree.
