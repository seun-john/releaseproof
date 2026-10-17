"""Content checks on the unpacked release: facts about the files that need no model to judge.

A spec lists them under ``"checks"``::

    {"checks": [{"check": "json_valid"},
                {"check": "max_file_bytes", "bytes": 102400},
                {"check": "no_todo_markers", "extensions": [".py", ".js"]},
                {"check": "no_hardcoded_credentials"},
                {"check": "no_absolute_paths", "under": "config"},
                {"check": "dependencies_pinned", "file": "requirements.txt"},
                {"check": "license_matches", "metadata": "datapackage.json", "license_file": "LICENSE"},
                {"check": "version_matches_changelog", "metadata": "datapackage.json"},
                {"check": "row_counts_match", "metadata": "datapackage.json"}]}

Each check gives PASS or FAIL. When its inputs are missing or not recognised (no changelog, a
licence text it cannot identify) it gives UNCHECKED with the reason, never a guess.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .core import InputError, finding, inside
from .redact import PATTERNS

TEXT_LIMIT = 2 * 1024 * 1024
SOURCE_EXTENSIONS = (".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")
PLACEHOLDER_WORDS = (
    "changeme", "change-me", "change_me", "example", "sample", "placeholder", "dummy", "fake",
    "your", "xxxx", "0000", "redacted", "insert", "todo", "none", "null", "test", "secret-here",
)  # fmt: skip
ABSOLUTE_PATH = re.compile(
    r"(?<![\w/])(?:[A-Za-z]:[\\/](?!/)[^\s\"'<>|]+|/(?:home|Users|root)/[^\s\"'<>|]+)"
)
KNOWN_CHECKS = (
    "json_valid", "max_file_bytes", "no_todo_markers", "no_hardcoded_credentials",
    "no_absolute_paths", "dependencies_pinned", "license_matches",
    "version_matches_changelog", "row_counts_match",
)  # fmt: skip

# SPDX id -> regex that identifies the licence text.
LICENSE_TEXT = {
    "mit": r"permission is hereby granted, free of charge",
    "apache-2.0": r"apache license\s+version 2\.0",
    "bsd-3-clause": r"redistribution and use in source and binary forms[\s\S]*neither the name",
    "bsd-2-clause": r"redistribution and use in source and binary forms(?![\s\S]*neither the name)",
    "gpl-3.0": r"gnu general public license\s+version 3",
    "gpl-2.0": r"gnu general public license\s+version 2",
    "lgpl-3.0": r"gnu lesser general public license\s+version 3",
    "mpl-2.0": r"mozilla public license,? version 2\.0",
    "isc": r"permission to use, copy, modify, and/or distribute this software",
    "unlicense": r"this is free and unencumbered software released into the public domain",
    "cc0-1.0": r"cc0 1\.0 universal|creative commons zero",
    "cc-by-4.0": r"creative commons attribution 4\.0",
    "cc-by-sa-4.0": r"creative commons attribution-sharealike 4\.0",
    "cc-by-nc-4.0": r"creative commons attribution-noncommercial 4\.0",
}
LICENSE_ALIASES = {
    "mit license": "mit", "apache 2.0": "apache-2.0", "apache-2": "apache-2.0",
    "apache license 2.0": "apache-2.0", "bsd-3": "bsd-3-clause", "bsd 3-clause": "bsd-3-clause",
    "gpl-3.0-only": "gpl-3.0", "gpl-3.0-or-later": "gpl-3.0", "gplv3": "gpl-3.0",
    "gpl-2.0-only": "gpl-2.0", "gpl-2.0-or-later": "gpl-2.0", "cc0": "cc0-1.0",
}  # fmt: skip


def _text(path: Path) -> str | None:
    try:
        if path.stat().st_size > TEXT_LIMIT:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def _files(package: Path) -> list[Path]:
    return sorted(p for p in package.rglob("*") if p.is_file())


def _rel(package: Path, path: Path) -> str:
    return path.relative_to(package).as_posix()


def _result(check: str, problems: list[str], message_ok: str, message_bad: str, **extra: Any):
    return finding(
        "FAIL" if problems else "PASS",
        message_bad if problems else message_ok,
        check=check,
        problems=problems[:20],
        **extra,
    )


def _unchecked(check: str, reason: str) -> dict[str, Any]:
    return finding("UNCHECKED", f"Could not run this check: {reason}.", check=check, reason=reason)


# -- individual checks ---------------------------------------------------------------------


def json_valid(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    problems = []
    for path in _files(package):
        if path.suffix.lower() != ".json":
            continue
        text = _text(path)
        if text is None:
            problems.append(f"{_rel(package, path)}: could not be read")
            continue
        try:
            json.loads(text.lstrip("﻿"))
        except ValueError as exc:
            problems.append(f"{_rel(package, path)}: {exc}")
    return _result("json_valid", problems, "Every .json file parses.", "A .json file is invalid.")


def max_file_bytes(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    limit = spec["bytes"]
    problems = [
        f"{_rel(package, p)}: {p.stat().st_size} bytes"
        for p in _files(package)
        if p.stat().st_size > limit
    ]
    return _result(
        "max_file_bytes", problems, f"No file is larger than {limit} bytes.",
        f"A file is larger than {limit} bytes.", limit=limit,
    )  # fmt: skip


def no_todo_markers(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    extensions = tuple(e.lower() for e in spec.get("extensions", SOURCE_EXTENSIONS))
    pattern = re.compile(r"\b(?:TODO|FIXME|XXX)\b")
    problems = []
    for path in _files(package):
        if path.suffix.lower() not in extensions:
            continue
        text = _text(path) or ""
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                problems.append(f"{_rel(package, path)}:{number}")
    return _result(
        "no_todo_markers", problems, "No source file has a TODO or FIXME marker.",
        "A source file has a TODO or FIXME marker.", extensions=list(extensions),
    )  # fmt: skip


def _placeholder(value: str) -> bool:
    lowered = value.lower().strip("'\"")
    if (
        not lowered
        or lowered.startswith(("<", "${", "{{", "%", "$("))
        or set(lowered) <= {"*", "x", "-", "."}
    ):
        return True
    return any(word in lowered for word in PLACEHOLDER_WORDS)


def _looks_like_a_secret(value: str) -> bool:
    """A password value must look chosen, not like a word of prose: 8+ characters with a digit
    or a symbol. Tokens with a known prefix (sk-, ghp_, AKIA...) are judged by the prefix."""
    return len(value) >= 8 and any(not c.isalpha() for c in value)


# Credential shapes. The redaction patterns cover private keys, JWTs and prefixed tokens; these
# add keyword assignments (token = ..., client_secret: ...) and Authorization headers.
CREDENTIAL_PATTERNS: list[tuple[str, str, int]] = [
    (kind, pattern, group)
    for kind, pattern, group in PATTERNS
    if kind in {"PRIVATE_KEY", "JWT", "TOKEN"}
] + [
    (
        "PASSWORD",
        r"(?i)\b(?:password|passwd|pwd|secret|(?:api|access|auth|client|private)[_ -]?(?:key|token|secret)|token)"
        r"\s*[:=]\s*[\"']?([^\s\"',;]+)",
        1,
    ),
    (
        "AUTHORIZATION",
        r"(?i)\bauthorization\s*:\s*[\"']?(?:bearer|basic|token)\s+([A-Za-z0-9._~+/=\-]{12,})",
        1,
    ),
]


def no_hardcoded_credentials(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    extra = tuple(str(w).lower() for w in spec.get("placeholders", ()))
    problems = []
    for path in _files(package):
        text = _text(path)
        if text is None:
            continue
        for kind, pattern, group in CREDENTIAL_PATTERNS:
            for match in re.finditer(pattern, text):
                value = match.group(group)
                if kind in ("PASSWORD", "AUTHORIZATION") and not _looks_like_a_secret(value):
                    continue
                if _placeholder(value) or any(w in value.lower() for w in extra):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{_rel(package, path)}:{line} ({kind.lower().replace('_', ' ')})")
    return _result(
        "no_hardcoded_credentials", problems, "No hard-coded credential was found.",
        "A file contains what looks like a hard-coded credential.",
    )  # fmt: skip


def no_absolute_paths(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    under = spec.get("under", ".")
    root = inside(package, under)
    if not root.is_dir():
        return _unchecked("no_absolute_paths", f"'{under}' is not a folder in the release")
    problems = []
    for path in _files(root):
        text = _text(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            hit = ABSOLUTE_PATH.search(line)
            if hit:
                problems.append(f"{_rel(package, path)}:{number}: {hit.group(0)[:60]}")
    return _result(
        "no_absolute_paths", problems, f"No absolute local path under {under}.",
        f"A file under {under} contains an absolute local path.", under=under,
    )  # fmt: skip


def dependencies_pinned(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    name = spec.get("file", "requirements.txt")
    path = inside(package, name)
    text = _text(path) if path.is_file() else None
    if text is None:
        return _unchecked("dependencies_pinned", f"{name} is not in the release")
    problems = []
    for line in text.splitlines():
        entry = line.split("#", 1)[0].strip()
        if not entry or entry.startswith(("-", "git+", "http://", "https://")):
            continue
        requirement = entry.split(";", 1)[0].strip()
        if not re.match(r"^[A-Za-z0-9_.\-]+(\[[^\]]+\])?\s*===?\s*[^=\s<>~!]+$", requirement):
            problems.append(requirement)
    return _result(
        "dependencies_pinned", problems, f"Every dependency in {name} is pinned with ==.",
        f"A dependency in {name} is not pinned to an exact version.", file=name,
    )  # fmt: skip


def _metadata_text(package: Path, name: str) -> str | None:
    path = inside(package, name)
    return _text(path) if path.is_file() else None


def _normal_license(raw: str) -> str:
    key = raw.strip().strip("()").strip().lower()
    return LICENSE_ALIASES.get(key, key)


def _declared_license(text: str, name: str) -> str | None:
    """The SPDX id a metadata file declares, from a license key or a '(ID)' in a copyright line."""
    if name.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        value = data.get("license") or data.get("licenses")
        if isinstance(value, list) and value:
            value = value[0]
        if isinstance(value, dict):
            value = value.get("name") or value.get("type") or value.get("id")
        if isinstance(value, str):
            return _normal_license(value)
        return None
    known = sorted({*LICENSE_TEXT, *LICENSE_ALIASES}, key=len, reverse=True)
    for line in text.splitlines():
        if re.search(r"\b(licen[sc]e|licenses|copyright)\b\s*[:=(]?", line, re.I):
            lowered = line.lower()
            for ident in known:
                if re.search(rf"(?<![\w.-]){re.escape(ident)}(?![\w.-])", lowered):
                    return _normal_license(ident)
    return None


def license_matches(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    metadata = spec.get("metadata", "datapackage.json")
    license_file = spec.get("license_file", "LICENSE")
    meta_text, lic_path = _metadata_text(package, metadata), inside(package, license_file)
    if meta_text is None:
        return _unchecked("license_matches", f"{metadata} is not in the release")
    declared = _declared_license(meta_text, metadata)
    if declared is None:
        return _unchecked("license_matches", f"{metadata} does not declare a licence")
    text = _text(lic_path) if lic_path.is_file() else None
    if text is None:
        return _unchecked("license_matches", f"{license_file} is not in the release")
    identified = [i for i, pattern in LICENSE_TEXT.items() if re.search(pattern, text, re.I)]
    if not identified:
        return _unchecked(
            "license_matches", f"the text of {license_file} is not a licence I recognise"
        )
    ok = declared in identified
    problems = (
        []
        if ok
        else [f"{metadata} says {declared}; {license_file} reads as {', '.join(identified)}"]
    )
    return _result(
        "license_matches", problems, f"{metadata} and {license_file} name the same licence.",
        f"{metadata} and {license_file} name different licences.",
        declared=declared, identified=identified,
    )  # fmt: skip


def _declared_version(text: str, name: str) -> str | None:
    if name.endswith(".json"):
        try:
            value = json.loads(text).get("version")
        except (ValueError, AttributeError):
            return None
        return str(value) if value is not None else None
    match = re.search(r"^\s*version\s*[:=]\s*[\"']?([0-9][^\s\"']*)", text, re.M)
    return match.group(1) if match else None


def version_matches_changelog(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    metadata = spec.get("metadata", "pyproject.toml")
    changelog = spec.get("changelog", "CHANGELOG.md")
    meta_text, log_text = _metadata_text(package, metadata), _metadata_text(package, changelog)
    if meta_text is None:
        return _unchecked("version_matches_changelog", f"{metadata} is not in the release")
    if log_text is None:
        return _unchecked("version_matches_changelog", f"{changelog} is not in the release")
    declared = _declared_version(meta_text, metadata)
    if declared is None:
        return _unchecked("version_matches_changelog", f"{metadata} does not declare a version")
    newest = None
    for line in log_text.splitlines():
        heading = re.match(r"^#{1,3}\s*\[?v?(\d+(?:\.\d+)+[\w.+\-]*)\]?", line, re.I)
        if heading:
            newest = heading.group(1)
            break  # an [Unreleased] heading has no version, so it is skipped by the pattern
    if newest is None:
        return _unchecked("version_matches_changelog", f"{changelog} has no released version")
    problems = (
        []
        if declared.lstrip("v") == newest
        else [f"{metadata} says {declared}; {changelog} says {newest}"]
    )
    return _result(
        "version_matches_changelog", problems, "The declared version is the newest in the changelog.",
        "The declared version is not the newest in the changelog.", declared=declared, newest=newest,
    )  # fmt: skip


def row_counts_match(package: Path, spec: dict[str, Any]) -> dict[str, Any]:
    metadata = spec.get("metadata", "datapackage.json")
    text = _metadata_text(package, metadata)
    if text is None:
        return _unchecked("row_counts_match", f"{metadata} is not in the release")
    try:
        resources = json.loads(text).get("resources", [])
    except (ValueError, AttributeError):
        return _unchecked("row_counts_match", f"{metadata} is not valid JSON")
    stated = [r for r in resources if isinstance(r, dict) and "rows" in r and r.get("path")]
    if not stated:
        return _unchecked("row_counts_match", f"{metadata} states no row counts")
    problems = []
    for resource in stated:
        path = inside(package, resource["path"])
        if not path.is_file():
            problems.append(f"{resource['path']}: file is not in the release")
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            # blank lines are not data rows
            actual = max(sum(1 for row in csv.reader(handle) if any(c.strip() for c in row)) - 1, 0)
        if actual != resource["rows"]:
            problems.append(f"{resource['path']}: states {resource['rows']} rows, has {actual}")
    return _result(
        "row_counts_match", problems, "Stated row counts match the CSV files.",
        "A stated row count does not match its CSV file.",
    )  # fmt: skip


CHECKS = {
    "json_valid": json_valid, "max_file_bytes": max_file_bytes, "no_todo_markers": no_todo_markers,
    "no_hardcoded_credentials": no_hardcoded_credentials, "no_absolute_paths": no_absolute_paths,
    "dependencies_pinned": dependencies_pinned, "license_matches": license_matches,
    "version_matches_changelog": version_matches_changelog, "row_counts_match": row_counts_match,
}  # fmt: skip


def validate_checks(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not all(isinstance(c, dict) for c in raw):
        raise InputError("'checks' must be an array of objects")
    for entry in raw:
        if entry.get("check") not in KNOWN_CHECKS:
            raise InputError(
                f"Unknown check {entry.get('check')!r}; choose from: {', '.join(KNOWN_CHECKS)}"
            )
        if entry["check"] == "max_file_bytes" and (
            type(entry.get("bytes")) is not int or entry["bytes"] <= 0
        ):
            raise InputError("max_file_bytes needs a positive integer 'bytes'")
        for key in ("under", "file", "metadata", "license_file", "changelog"):
            if key in entry and not isinstance(entry[key], str):
                raise InputError(f"'{key}' in a check must be a string")
    return raw


def run_checks(package: Path, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run the listed checks against the unpacked release and return one finding per check."""
    return [CHECKS[entry["check"]](package, entry) for entry in checks]
