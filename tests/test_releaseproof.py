from __future__ import annotations

import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest

from releaseproof.cli import main
from releaseproof.core import InputError
from releaseproof.release import releaseproof


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "app.py").write_text("print('Hello from app')\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo project readme\n", encoding="utf-8")
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    return root


def statuses(result: dict[str, Any]) -> list[str]:
    return [f["status"] for f in result["findings"]]


def build(
    project: Path, tmp_path: Path, data: dict[str, Any] | None = None, **kw: Any
) -> dict[str, Any]:
    return releaseproof(data or {}, project, "build", tmp_path / "rel.zip", **kw)


def names(archive: Path) -> set[str]:
    with zipfile.ZipFile(archive) as z:
        return set(z.namelist())


def test_build_includes_sources_and_leaves_out_secrets(project: Path, tmp_path: Path) -> None:
    (project / ".env").write_text("KEY=1", encoding="utf-8")
    (project / "local.sqlite3").write_bytes(b"db")
    (project / "reports").mkdir()
    (project / "reports" / "r.json").write_text("{}", encoding="utf-8")
    (project / "old.zip").write_bytes(b"zip")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("x", encoding="utf-8")
    result = build(project, tmp_path)
    assert statuses(result) == ["BUILT", "NO_EXPECTATIONS", "NO_SMOKE_TESTS"]
    assert names(tmp_path / "rel.zip") == {"app.py", "README.md", "pkg/__init__.py"}


def test_user_excludes_are_applied(project: Path, tmp_path: Path) -> None:
    build(project, tmp_path, {"exclude_paths": ["pkg/*"]})
    assert "pkg/__init__.py" not in names(tmp_path / "rel.zip")


def test_build_refuses_to_overwrite(project: Path, tmp_path: Path) -> None:
    build(project, tmp_path)
    before = (tmp_path / "rel.zip").read_bytes()
    with pytest.raises(InputError, match="already exists"):
        build(project, tmp_path)
    assert (tmp_path / "rel.zip").read_bytes() == before


def test_build_with_nothing_to_ship(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(InputError, match="No files"):
        releaseproof({}, empty, "build", tmp_path / "r.zip")


def test_expected_files_sizes_and_hashes(project: Path, tmp_path: Path) -> None:
    from releaseproof.core import file_digest

    data = {
        "expected_files": [
            "app.py",
            {"path": "README.md", "min_bytes": 5},
            {"path": "README.md", "min_bytes": 10_000},
            {"path": "app.py", "sha256": "0" * 64},
            {"path": "app.py", "sha256": file_digest(project / "app.py")},
            "missing.py",
        ]
    }
    got = statuses(build(project, tmp_path, data))
    assert got[1:7] == ["PASS", "PASS", "FAIL", "FAIL", "PASS", "FAIL"]


def test_inspect_runs_nothing_even_if_allowed(project: Path, tmp_path: Path) -> None:
    build(project, tmp_path)
    marker = tmp_path / "ran.txt"
    code = f"open(r'{marker}', 'w').write('x')"
    data = {"artifact_path": "../rel.zip", "commands": [["{python}", "-c", code]]}
    with pytest.raises(InputError, match="escapes"):
        releaseproof(data, project, "inspect", allow_execution=True)
    data["artifact_path"] = "rel.zip"
    (project / "rel.zip").write_bytes((tmp_path / "rel.zip").read_bytes())
    result = releaseproof(data, project, "inspect", allow_execution=True)
    assert "NOT_EXECUTED" in statuses(result) and not marker.exists()
    assert result["release_ready"] is False


def make_archive(tmp_path: Path, members: dict[str, bytes]) -> Path:
    archive = tmp_path / "a" / "x.zip"
    archive.parent.mkdir()
    with zipfile.ZipFile(archive, "w") as z:
        for name, content in members.items():
            z.writestr(name, content)
    return archive.parent


@pytest.mark.parametrize(
    "name",
    [
        "../evil.py",
        "/abs.py",
        "a/../../b.py",
        "C:evil.py",
        pytest.param(
            "back\\slash.py",
            marks=pytest.mark.skipif(
                sys.platform == "win32", reason="zipfile rewrites backslashes on Windows"
            ),
        ),
    ],
)
def test_unsafe_member_paths_are_refused(tmp_path: Path, name: str) -> None:
    root = make_archive(tmp_path, {name: b"x"})
    with pytest.raises(InputError, match="Unsafe path"):
        releaseproof({"artifact_path": "x.zip"}, root, "inspect")


def test_case_colliding_members_are_refused(tmp_path: Path) -> None:
    root = make_archive(tmp_path, {"A.py": b"1", "a.py": b"2"})
    with pytest.raises(InputError, match="case-colliding"):
        releaseproof({"artifact_path": "x.zip"}, root, "inspect")


def test_symlink_member_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "a"
    root.mkdir()
    with zipfile.ZipFile(root / "x.zip", "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "target")
    with pytest.raises(InputError, match="Links"):
        releaseproof({"artifact_path": "x.zip"}, root, "inspect")


def test_missing_artifact_and_bad_package_subdir(project: Path, tmp_path: Path) -> None:
    with pytest.raises(InputError, match="not found"):
        releaseproof({"artifact_path": "nope.zip"}, project, "inspect")
    build(project, tmp_path)
    (project / "rel.zip").write_bytes((tmp_path / "rel.zip").read_bytes())
    with pytest.raises(InputError, match="subdirectory"):
        releaseproof({"artifact_path": "rel.zip", "package_subdir": "nope"}, project, "inspect")


@pytest.mark.parametrize(
    "bad",
    [
        {"commands": "python app.py"},
        {"commands": [["python", "app.py"]]},
        {"commands": [[]]},
        {"commands": [["{python}", 3]]},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
        {"timeout_seconds": 1.5},
        {"commands": [["{python}"]], "expected_stdout": {"5": "x"}},
        {"expected_stdout": {"0": 1}},
        {"exclude_paths": "x"},
        {"expected_files": "app.py"},
    ],
)
def test_configuration_is_validated(project: Path, tmp_path: Path, bad: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        build(project, tmp_path, bad)


def smoke(project: Path, tmp_path: Path, commands: list[list[str]], **extra: Any) -> dict[str, Any]:
    data = {"expected_files": ["app.py"], "commands": commands, **extra}
    return releaseproof(data, project, "build", tmp_path / "rel.zip", allow_execution=True)


def test_commands_do_not_run_without_permission(project: Path, tmp_path: Path) -> None:
    data = {"expected_files": ["app.py"], "commands": [["{python}", "app.py"]]}
    result = releaseproof(data, project, "build", tmp_path / "rel.zip")
    assert "NOT_EXECUTED" in statuses(result) and result["release_ready"] is False


def test_passing_smoke_test_makes_the_release_ready(project: Path, tmp_path: Path) -> None:
    result = smoke(
        project, tmp_path, [["{python}", "app.py"]], expected_stdout={"0": "Hello from app"}
    )
    assert statuses(result) == ["BUILT", "PASS", "PASS", "PASS"]
    assert result["release_ready"] is True
    assert result["runs"][0]["stdout"].strip() == "Hello from app"


def test_failing_command_and_wrong_output(project: Path, tmp_path: Path) -> None:
    failing = smoke(project, tmp_path, [["{python}", "-c", "raise SystemExit(3)"]])
    assert "FAIL" in statuses(failing) and failing["release_ready"] is False
    (tmp_path / "rel.zip").unlink()
    wrong = smoke(project, tmp_path, [["{python}", "app.py"]], expected_stdout={"0": "Goodbye"})
    assert statuses(wrong)[-1] == "FAIL" and wrong["release_ready"] is False


def test_timeout_kills_the_whole_process_tree(project: Path, tmp_path: Path) -> None:
    flag = tmp_path / "grandchild.txt"
    (project / "grandchild.py").write_text(
        f"import time\ntime.sleep(3)\nopen({str(flag)!r}, 'w').write('x')\n", encoding="utf-8"
    )
    (project / "spawner.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, 'grandchild.py'])\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    result = smoke(project, tmp_path, [["{python}", "spawner.py"]], timeout_seconds=1)
    assert "TIMEOUT" in statuses(result)
    assert time.monotonic() - started < 15
    time.sleep(4)
    assert not flag.exists(), "the grandchild process survived the timeout"


def test_environment_secrets_are_not_passed_on(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_SECRET_API_KEY", "super-secret-value")
    code = "import os;print(os.environ.get('MY_SECRET_API_KEY','absent'))"
    result = smoke(project, tmp_path, [["{python}", "-c", code]], expected_stdout={"0": "absent"})
    assert result["release_ready"] is True


def test_secrets_in_logs_are_redacted(project: Path, tmp_path: Path) -> None:
    result = smoke(project, tmp_path, [["{python}", "-c", "print('mail ada@example.com')"]])
    assert result["runs"][0]["stdout"].strip() == "mail [EMAIL_001]"


def test_wheel_is_installed_into_a_fresh_environment(tmp_path: Path) -> None:
    root = tmp_path / "w"
    root.mkdir()
    wheel = root / "demopkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("demopkg/__init__.py", "VALUE = 41 + 1\n")
        z.writestr(
            "demopkg-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: demopkg\nVersion: 1.0\n",
        )
        z.writestr(
            "demopkg-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        z.writestr("demopkg-1.0.dist-info/RECORD", "")
    data = {
        "artifact_path": wheel.name,
        "expected_files": ["demopkg/__init__.py"],
        "commands": [["{python}", "-c", "import demopkg;print(demopkg.VALUE)"]],
        "expected_stdout": {"0": "42"},
    }
    result = releaseproof(data, root, "test", allow_execution=True)
    assert result["release_ready"] is True, statuses(result)


def test_cli_build_then_strict_gate(project: Path, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"expected_files": ["app.py"], "commands": [["{python}", "app.py"]]}),
        encoding="utf-8",
    )
    base = ["build", str(spec), "--root", str(project), "--archive", str(tmp_path / "r1.zip")]
    assert main([*base, "-o", str(tmp_path / "o.json"), "--strict"]) == 1  # NOT_EXECUTED
    base[-1] = str(tmp_path / "r2.zip")
    code = main([*base, "--allow-execution", "-o", str(tmp_path / "o.json"), "--strict"])
    assert code == 0
    assert json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["release_ready"] is True


def test_cli_will_not_write_the_report_over_the_archive(project: Path, tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text("{}", encoding="utf-8")
    archive = tmp_path / "r.zip"
    code = main(
        ["build", str(spec), "--root", str(project), "--archive", str(archive), "-o", str(archive)]
    )
    assert code == 2 and not archive.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only check of process groups")
def test_posix_process_group_is_used(project: Path, tmp_path: Path) -> None:
    result = smoke(
        project,
        tmp_path,
        [["{python}", "-c", "import os;print(os.getpgrp()==os.getpid())"]],
        expected_stdout={"0": "True"},
    )
    assert result["release_ready"] is True
