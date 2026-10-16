"""Command-line plumbing shared by every command: JSON in, JSON and HTML reports out."""

from __future__ import annotations

import argparse
import contextlib
import html
import json
import sqlite3
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .core import VERSION, InputError, decided_share, describe_coverage, load


@dataclass
class Command:
    """One subcommand.

    func(args, data) returns a report dict. `data` is the parsed JSON input, or None when
    `takes_input` is False. `ok` lists the finding statuses that do not count as a problem
    under --strict. `protect` names options whose paths must never be overwritten by output.
    """

    func: Callable[[argparse.Namespace, Any], dict[str, Any]]
    help: str
    ok: frozenset[str] = frozenset()
    takes_input: bool = True
    options: list[tuple[tuple[str, ...], dict[str, Any]]] = field(default_factory=list)
    protect: tuple[str, ...] = ()


def render_html(result: dict[str, Any]) -> str:
    title = html.escape(str(result["tool"]))
    rows = "".join(
        f"<tr><td>{html.escape(str(f['status']))}</td><td>{html.escape(str(f['message']))}</td></tr>"
        for f in result["findings"]
    )
    payload = html.escape(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    cov = result.get("coverage")
    coverage_line = f"<p>{html.escape(describe_coverage(cov))}</p>" if cov else ""
    style = (
        "body{font:16px system-ui;background:#f5f7fb;color:#17253d;margin:40px auto;"
        "max-width:1100px;padding:0 20px}h1{color:#234e70}table{width:100%;"
        "border-collapse:collapse;background:#fff}td,th{text-align:left;padding:12px;"
        "border-bottom:1px solid #d9e2ee}pre{white-space:pre-wrap;overflow-wrap:anywhere;"
        "background:#fff;padding:20px;border-radius:10px}small{color:#53657e}"
    )
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        f"<title>{title} report</title><style>{style}</style>"
        f"<h1>{title}</h1><small>ReleaseProof {html.escape(VERSION)}</small>"
        f"{coverage_line}"
        f"<table><tr><th>Status</th><th>Observation</th></tr>{rows}</table>"
        f"<h2>Evidence and scope</h2><pre>{payload}</pre></html>"
    )


def run(
    prog: str, description: str, commands: dict[str, Command], argv: list[str] | None = None
) -> int:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--version", action="version", version=VERSION)
    subs = parser.add_subparsers(dest="command", required=True)
    for name, spec in commands.items():
        sub = subs.add_parser(name, help=spec.help, description=spec.help)
        if spec.takes_input:
            sub.add_argument("input", help="JSON input file")
        sub.add_argument("--output", "-o", help="write the JSON report here (default: stdout)")
        sub.add_argument("--html", help="also write a browser-readable HTML report")
        sub.add_argument(
            "--strict",
            action="store_true",
            help="exit 1 when any finding is not an OK status (for CI)",
        )
        sub.add_argument(
            "--min-coverage",
            type=float,
            metavar="PCT",
            help="exit 3 if fewer than PCT percent of items could be judged and nothing "
            "else failed (a --strict failure still exits 1)",
        )
        for flags, kwargs in spec.options:
            sub.add_argument(*flags, **kwargs)
    args = parser.parse_args(argv)
    spec = commands[args.command]
    try:
        data = None
        if spec.takes_input:
            data = load(args.input)
            if not isinstance(data, dict):
                raise InputError("Input must be a JSON object")
        protected = {Path(args.input).resolve()} if spec.takes_input else set()
        for attr in spec.protect:
            value = getattr(args, attr, None)
            if value:
                protected.add(Path(value).resolve())
        outputs = [Path(p).resolve() for p in (args.output, args.html) if p]
        if len(set(outputs)) != len(outputs) or any(p in protected for p in outputs):
            raise InputError("Output paths collide with an input, database, archive or each other")
        result = spec.func(args, data)
        text = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        if args.html:
            Path(args.html).parent.mkdir(parents=True, exist_ok=True)
            Path(args.html).write_text(render_html(result), encoding="utf-8")
        block = result.get("coverage")
        if block:
            print(f"coverage: {describe_coverage(block)}", file=sys.stderr)
        if args.strict and any(f["status"] not in spec.ok for f in result["findings"]):
            return 1
        if args.min_coverage is not None and block:
            share = decided_share(block)
            if share is None or share < args.min_coverage:
                shown = "no items" if share is None else f"{share:g}% decided"
                print(
                    f"Coverage {shown} is below --min-coverage {args.min_coverage:g}.",
                    file=sys.stderr,
                )
                return 3
        return 0
    except (InputError, ValueError, KeyError, TypeError, OSError, UnicodeError) as exc:
        print(f"Input or file error: {exc}", file=sys.stderr)
        return 2
    except (sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"Storage error: {exc}", file=sys.stderr)
        return 2


def main_wrapper(main: Callable[[list[str] | None], int]) -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            with contextlib.suppress(OSError, ValueError):
                reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(None))
