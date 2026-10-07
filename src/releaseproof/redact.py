"""Pattern-based redaction with stable placeholders.

Pattern screening is incomplete by nature. It finds well-formed emails, phone numbers,
card numbers, national IDs, secrets and keys; it does not find names or addresses unless
you list them as confidential terms. Review the output before sharing it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .core import InputError

PRIVATE_KEY = (
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]+?"
    r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
)

# (kind, pattern, group). group 0 redacts the whole match; group 1 redacts only the value
# after a keyword, so "password: hunter2" becomes "password: [PASSWORD_001]".
PATTERNS: list[tuple[str, str, int]] = [
    ("PRIVATE_KEY", PRIVATE_KEY, 0),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b", 0),
    (
        "TOKEN",
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|"
        r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[A-Za-z0-9_-]{30,})\b",
        0,
    ),
    (
        "PASSWORD",
        r"(?i)\b(?:password|passwd|pwd|api[_ -]?key|secret|access[_ -]?token|auth[_ -]?token)"
        r"\s*[:=]\s*[\"']?([^\s\"',;]+)",
        1,
    ),
    ("EMAIL", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", 0),
    # Nigerian BVN and NIN are 11 digits; only redact when the label is present.
    ("NATIONAL_ID", r"(?i)\b(?:bvn|nin|national id(?: number)?)\b\D{0,12}?(\d{11})(?!\d)", 1),
    (
        "BANK_ACCOUNT",
        r"(?i)\b(?:account|acct|a/c)(?: (?:no|number|num))?\b\D{0,10}?(\d{10})(?!\d)",
        1,
    ),
    ("PHONE", r"(?<![\w+])\+\d{1,3}[\d ()-]{7,14}\d(?!\w)", 0),
    # Nigerian numbers: 0803 123 4567, 08031234567, 2348031234567.
    ("PHONE", r"(?<![\d+])(?:234|0)[789][01]\d[ -]?\d{3}[ -]?\d{4}(?!\d)", 0),
    ("CARD", r"(?<![\d-])(?:\d[ -]?){13,19}(?![\d-])", 0),
]


def _luhn(digits: str) -> bool:
    total, flip = 0, False
    for ch in reversed(digits):
        n = int(ch)
        if flip:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        flip = not flip
    return total % 10 == 0


def _spans(text: str, terms: Iterable[str]) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    for kind, pattern, group in PATTERNS:
        for match in re.finditer(pattern, text):
            if kind == "CARD":
                digits = re.sub(r"\D", "", match.group())
                if not 13 <= len(digits) <= 19 or not _luhn(digits):
                    continue
            start, end = match.span(group)
            spans.append((start, end, kind))
    for term in terms:
        if not isinstance(term, str) or not term.strip():
            raise InputError("Confidential terms must be non-empty strings")
        spans.extend(
            (m.start(), m.end(), "CONFIDENTIAL") for m in re.finditer(re.escape(term), text, re.I)
        )
    return spans


def redact(text: str, terms: Iterable[str] = ()) -> tuple[str, dict[str, str]]:
    """Return (redacted text, {placeholder: original}). Same value, same placeholder."""
    chosen: list[tuple[int, int, str]] = []
    last = -1
    for start, end, kind in sorted(_spans(text, terms), key=lambda x: (x[0], -x[1])):
        if start >= last:
            chosen.append((start, end, kind))
            last = end
    mapping: dict[str, str] = {}
    by_value: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}
    pieces: list[str] = []
    pos = 0
    for start, end, kind in chosen:
        value = text[start:end]
        key = (kind, value)
        if key not in by_value:
            counts[kind] = counts.get(kind, 0) + 1
            placeholder = f"[{kind}_{counts[kind]:03d}]"
            if placeholder in text:
                raise InputError("Input already contains a generated placeholder; use fresh text")
            by_value[key] = placeholder
            mapping[placeholder] = value
        pieces.extend([text[pos:start], by_value[key]])
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces), mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    """Put originals back. One pass, so restored values are never re-read as placeholders."""
    if not mapping:
        return text
    pattern = "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True))
    return re.sub(pattern, lambda m: mapping[m.group()], text)
