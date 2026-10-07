"""Shared helpers: hashing, strict JSON loading, path confinement and report building."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__ as VERSION


class InputError(ValueError):
    """The input or a file it names is unusable. The CLI reports it and exits with 2."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def digest(value: Any) -> str:
    """SHA-256 of bytes, or of the canonical JSON form of any JSON value."""
    data = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: str | Path) -> Any:
    """Read a UTF-8 JSON file, rejecting NaN and Infinity."""

    def reject(value: str) -> Any:
        raise InputError(f"Non-finite JSON number: {value}")

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject)
    except json.JSONDecodeError as exc:
        raise InputError(f"{path}: invalid JSON ({exc})") from exc
    except (OSError, UnicodeError) as exc:
        raise InputError(f"Cannot read {path}: {exc}") from exc


def require(data: Any, key: str, kind: type | tuple[type, ...]) -> Any:
    """Fetch data[key] and insist on its type. bool is never accepted as a number."""
    if not isinstance(data, dict):
        raise InputError(f"Expected an object while reading '{key}'")
    value = data.get(key)
    kinds = kind if isinstance(kind, tuple) else (kind,)
    ok = isinstance(value, kind) and not (
        isinstance(value, bool) and bool not in kinds and any(k in (int, float) for k in kinds)
    )
    if not ok:
        names = " or ".join(k.__name__ for k in kinds)
        raise InputError(f"'{key}' must be {names}")
    return value


def number(value: Any) -> float:
    if isinstance(value, bool):
        raise InputError("A boolean is not a number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise InputError(f"Not a number: {value!r}") from None
    if not math.isfinite(result):
        raise InputError("Numbers must be finite")
    return result


def inside(root: str | Path, relative: str) -> Path:
    """Resolve `relative` under `root`, refusing anything that escapes it."""
    base = Path(root).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise InputError(f"Path escapes the project root: {relative}")
    return path


def finding(status: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"status": status, "message": message, "evidence": evidence}


def report(tool: str, findings: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tool": tool,
        "version": VERSION,
        "generated_at": now(),
        "findings": findings,
        **extra,
    }


def norm(value: Any) -> str:
    """Case-folded text with punctuation and runs of whitespace collapsed."""
    return re.sub(r"\W+", " ", str(value).casefold()).strip()


@contextmanager
def database(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open SQLite, commit on success, roll back on error, and always close.

    `with sqlite3.connect(...)` only commits; it leaves the file open, which on Windows
    keeps it locked until the process exits.
    """
    db = sqlite3.connect(path)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
