"""Build, inspect and smoke-test a release archive.

`inspect` and `build` never run archive code. `test` runs commands you list, from a temporary
copy, only when `allow_execution` is true. A temporary directory is not a sandbox: package
code can read anything your account can and can use the network. Only test archives you trust.
"""

from __future__ import annotations

import fnmatch
import os
import signal
import stat
import subprocess
import sys
import tempfile
import venv
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any

from .core import InputError, file_digest, finding, inside, report, require
from .redact import redact

MAX_MEMBERS = 10_000
MAX_UNPACKED = 100 * 1024 * 1024
LOG_LIMIT = 64 * 1024
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
}
DEFAULT_EXCLUDES = (
    "*.egg-info/*",
    ".env",
    ".env.*",
    "*.sqlite3",
    "*.sqlite3-*",
    "*.zip",
    "*.whl",
    "reports/*",
)
PASSING = {"PASS", "BUILT"}


def _project_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            yield path


def safe_members(bundle: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Reject archives that could write outside a folder or exhaust it."""
    infos = bundle.infolist()
    if len(infos) > MAX_MEMBERS or sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise InputError("The archive exceeds the member count or unpacked size limit")
    seen: set[str] = set()
    for entry in infos:
        name = entry.filename
        parts = PurePosixPath(name).parts
        if (
            not name
            or "\\" in name
            or name.startswith("/")
            or ".." in parts
            or any(":" in p for p in parts)
        ):
            raise InputError(f"Unsafe path in archive: {name!r}")
        folded = name.rstrip("/").casefold()
        if folded in seen:
            raise InputError(f"Duplicate or case-colliding path in archive: {name!r}")
        seen.add(folded)
        mode = entry.external_attr >> 16
        if stat.S_ISLNK(mode) or (
            mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
        ):
            raise InputError(f"Links and special files are not supported: {name!r}")
        if entry.flag_bits & 1:
            raise InputError("Encrypted archives are not supported")
    return infos


def clean_environment() -> dict[str, str]:
    """A minimal environment, so API keys in yours are not handed to package code."""
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env.update(
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        PIP_NO_INDEX="1",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
    )
    return env


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def run_command(
    argv: list[str], cwd: Path | str, timeout: int, environment: dict[str, str]
) -> dict[str, Any]:
    """Run argv with no shell. On timeout the whole process tree is stopped."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        extra: dict[str, Any] = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            **extra,
        )
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc)
            proc.wait()
        out.seek(0)
        err.seek(0)
        stdout = out.read(LOG_LIMIT).decode("utf-8", "replace")
        stderr = err.read(LOG_LIMIT).decode("utf-8", "replace")
    return {
        "argv": argv,
        "exit_code": None if timed_out else proc.returncode,
        "timed_out": timed_out,
        "stdout": redact(stdout)[0],
        "stderr": redact(stderr)[0],
        "log_limit_bytes": LOG_LIMIT,
    }


def _validate(data: dict[str, Any]) -> tuple[list[list[str]], int, dict[str, str], list[str]]:
    commands = data.get("commands", [])
    if not isinstance(commands, list):
        raise InputError("'commands' must be an array of argument arrays")
    for command in commands:
        if (
            not isinstance(command, list)
            or not command
            or command[0] != "{python}"
            or not all(isinstance(a, str) for a in command)
        ):
            raise InputError("Each command must be an array of strings starting with {python}")
    timeout = data.get("timeout_seconds", 30)
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise InputError("timeout_seconds must be an integer from 1 to 60")
    expected_stdout = data.get("expected_stdout", {})
    if not isinstance(expected_stdout, dict) or not all(
        isinstance(v, str) for v in expected_stdout.values()
    ):
        raise InputError("expected_stdout must map command indexes to strings")
    valid_indexes = {str(i) for i in range(len(commands))}
    if set(expected_stdout) - valid_indexes:
        raise InputError("expected_stdout refers to a command index that does not exist")
    if not isinstance(data.get("expected_files", []), list):
        raise InputError("expected_files must be an array")
    excludes = data.get("exclude_paths", [])
    if not isinstance(excludes, list) or not all(isinstance(p, str) for p in excludes):
        raise InputError("exclude_paths must be an array of patterns")
    return commands, timeout, expected_stdout, excludes


def releaseproof(
    data: dict[str, Any],
    root: str | Path,
    action: str = "inspect",
    archive: str | Path | None = None,
    allow_execution: bool = False,
) -> dict[str, Any]:
    if action not in ("build", "inspect", "test"):
        raise InputError("action must be build, inspect or test")
    commands, timeout, expected_stdout, user_excludes = _validate(data)
    base = Path(root).resolve()
    out: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    if action == "build":
        if not archive:
            raise InputError("build needs --archive")
        artifact = Path(archive).resolve()
        if artifact.exists():
            raise InputError("The release artifact already exists; choose a new file name")
        patterns = [*DEFAULT_EXCLUDES, *user_excludes]
        chosen = [
            (p, p.relative_to(base).as_posix())
            for p in _project_files(base)
            if p.resolve() != artifact
        ]
        chosen = [
            (p, rel) for p, rel in chosen if not any(fnmatch.fnmatchcase(rel, x) for x in patterns)
        ]
        if not chosen:
            raise InputError("No files were selected for the release")
        if len(chosen) > MAX_MEMBERS or sum(p.stat().st_size for p, _ in chosen) > MAX_UNPACKED:
            raise InputError("The project exceeds the archive size limits")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(artifact, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path, rel in chosen:
                bundle.write(path, rel)
        out.append(finding("BUILT", "Source ZIP built from the selected files.", files=len(chosen)))
    else:
        artifact = inside(base, require(data, "artifact_path", str))
        if not artifact.is_file():
            raise InputError(f"Artifact not found: {data['artifact_path']}")
    artifact_hash = file_digest(artifact)

    expectations = data.get("expected_files", [])
    with tempfile.TemporaryDirectory(prefix="releaseproof-") as temp:
        extracted = Path(temp) / "extracted"
        extracted.mkdir()
        with zipfile.ZipFile(artifact) as bundle:
            for entry in safe_members(bundle):
                target = inside(extracted, entry.filename)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(entry) as src, target.open("wb") as dst:
                        dst.write(src.read())
        package = inside(extracted, data.get("package_subdir", "."))
        if not package.is_dir():
            raise InputError("The package subdirectory does not exist in the archive")
        for expected in expectations:
            if isinstance(expected, str):
                expected = {"path": expected, "min_bytes": 1}
            rel = require(expected, "path", str)
            minimum = expected.get("min_bytes", 1)
            if type(minimum) is not int or minimum < 0:
                raise InputError("min_bytes must be a non-negative integer")
            target = inside(package, rel)
            valid = target.is_file() and target.stat().st_size >= minimum
            if valid and expected.get("sha256"):
                valid = file_digest(target) == expected["sha256"]
            out.append(
                finding(
                    "PASS" if valid else "FAIL",
                    "Expected file is present and large enough."
                    if valid
                    else "Expected file is missing, too small or has the wrong hash.",
                    path=rel,
                    min_bytes=minimum,
                )
            )
        if not expectations:
            out.append(finding("NO_EXPECTATIONS", "No required files were configured."))

        should_run = allow_execution and action != "inspect"
        if commands and not should_run:
            out.append(
                finding(
                    "NOT_EXECUTED",
                    "Smoke commands were not run. Use the test command with --allow-execution.",
                )
            )
        if should_run and commands:
            env = clean_environment()
            python = sys.executable
            workdir = package
            if artifact.suffix.lower() == ".whl":
                env_root = Path(temp) / "venv"
                venv.EnvBuilder(with_pip=True).create(env_root)
                python = str(env_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
                install = run_command(
                    [
                        python,
                        "-m",
                        "pip",
                        "install",
                        "--no-index",
                        "--no-deps",
                        "--no-compile",
                        str(artifact),
                    ],
                    temp,
                    max(timeout, 60),
                    env,
                )
                runs.append({"stage": "wheel_install", **install})
                ok = install["exit_code"] == 0
                out.append(
                    finding(
                        "PASS" if ok else "FAIL",
                        "Offline install of the wheel into a fresh virtual environment.",
                        exit_code=install["exit_code"],
                    )
                )
                if not ok:
                    return report(
                        "ReleaseProof",
                        out,
                        artifact=str(artifact),
                        artifact_sha256=artifact_hash,
                        runs=runs,
                        release_ready=False,
                    )
                workdir = (
                    Path(temp) / "wheel-test"
                )  # empty, so the extracted source is not importable
                workdir.mkdir()
            for index, command in enumerate(commands):
                argv = [
                    a.replace("{python}", python).replace("{root}", str(workdir)) for a in command
                ]
                result = run_command(argv, workdir, timeout, env)
                runs.append({"stage": "smoke_test", **result})
                status = (
                    "TIMEOUT"
                    if result["timed_out"]
                    else "PASS"
                    if result["exit_code"] == 0
                    else "FAIL"
                )
                out.append(
                    finding(
                        status,
                        "Ran a smoke command from a temporary copy.",
                        argv=argv,
                        exit_code=result["exit_code"],
                    )
                )
                wanted = expected_stdout.get(str(index))
                if wanted is not None:
                    seen = wanted in result["stdout"]
                    out.append(
                        finding(
                            "PASS" if seen else "FAIL",
                            "Required text appeared in the output."
                            if seen
                            else "Required text did not appear in the output.",
                            command_index=index,
                            expected=wanted,
                        )
                    )
        if not commands:
            out.append(finding("NO_SMOKE_TESTS", "No smoke commands were configured."))
    ready = bool(expectations and commands and runs) and all(f["status"] in PASSING for f in out)
    return report(
        "ReleaseProof",
        out,
        artifact=str(artifact),
        artifact_sha256=artifact_hash,
        runs=runs,
        release_ready=ready,
        runtime={"python": sys.version.split()[0], "platform": sys.platform},
        scope="this computer only; a temporary folder is not a sandbox and a source ZIP is not an installed package",
    )
