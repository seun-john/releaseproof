"""Command line for ReleaseProof."""

from __future__ import annotations

import argparse
from typing import Any

from .release import releaseproof
from .runner import Command, main_wrapper, run

ROOT = (("--root",), {"default": ".", "help": "project folder (default: current folder)"})
OK = frozenset({"PASS", "BUILT"})


def _build(args: argparse.Namespace, data: Any) -> dict[str, Any]:
    return releaseproof(data, args.root, "build", args.archive, args.allow_execution)


def _inspect(args: argparse.Namespace, data: Any) -> dict[str, Any]:
    return releaseproof(data, args.root, "inspect")


def _test(args: argparse.Namespace, data: Any) -> dict[str, Any]:
    return releaseproof(data, args.root, "test", None, args.allow_execution)


ALLOW = (
    ("--allow-execution",),
    {
        "action": "store_true",
        "help": "run the configured smoke commands (executes code from the archive; trusted archives only)",
    },
)

COMMANDS = {
    "build": Command(
        _build,
        "Build a source ZIP, inspect it and optionally smoke-test it",
        OK,
        options=[
            ROOT,
            (("--archive",), {"required": True, "help": "new ZIP file to create"}),
            ALLOW,
        ],
        protect=("archive",),
    ),
    "inspect": Command(
        _inspect, "Validate an archive and check expected files (runs nothing)", OK, options=[ROOT]
    ),
    "test": Command(
        _test,
        "Smoke-test an archive in a temporary folder (needs --allow-execution)",
        OK,
        options=[ROOT, ALLOW],
    ),
}


def main(argv: list[str] | None = None) -> int:
    return run(
        "releaseproof", "Inspect and smoke-test a release before publishing.", COMMANDS, argv
    )


if __name__ == "__main__":
    main_wrapper(main)
