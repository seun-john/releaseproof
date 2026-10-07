# Changelog

## 0.1.0

First release.

- `build`: create a source ZIP that leaves out secrets, databases, archives and reports.
- `inspect`: validate an archive (path traversal, links, duplicates, encryption, size limits) and check expected files.
- `test`: with `--allow-execution`, run smoke commands from a temporary copy (or a fresh virtual environment for a wheel); timeouts stop the whole process tree.
