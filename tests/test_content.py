"""Content checks on the unpacked release (the `checks` list in a spec)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from releaseproof.cli import main
from releaseproof.core import InputError
from releaseproof.release import releaseproof


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    return root


def put(project: Path, name: str, text: str | bytes) -> None:
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def run(project: Path, tmp_path: Path, *checks: dict[str, Any], **extra: Any) -> dict[str, Any]:
    spec = {"checks": list(checks), **extra}
    return releaseproof(spec, project, "build", tmp_path / "rel.zip")


def verdict(result: dict[str, Any], name: str) -> dict[str, Any]:
    return next(f for f in result["findings"] if f["evidence"].get("check") == name)


def status(project: Path, tmp_path: Path, check: dict[str, Any]) -> str:
    return verdict(run(project, tmp_path, check), check["check"])["status"]


# -- json_valid -------------------------------------------------------------------------------


def test_json_valid(project: Path, tmp_path: Path) -> None:
    put(project, "ok.json", '{"a": [1, 2]}')
    put(project, "nested/also.json", "[]")
    assert status(project, tmp_path, {"check": "json_valid"}) == "PASS"


def test_json_invalid_names_the_file(project: Path, tmp_path: Path) -> None:
    put(project, "ok.json", "{}")
    put(project, "data/payload.json", '{"a": 1,}')
    f = verdict(run(project, tmp_path, {"check": "json_valid"}), "json_valid")
    assert f["status"] == "FAIL" and f["evidence"]["problems"][0].startswith("data/payload.json")


def test_json_with_a_byte_order_mark_and_unusual_but_valid_values(
    project: Path, tmp_path: Path
) -> None:
    put(project, "bom.json", b"\xef\xbb\xbf" + b'{"x": 1e-3, "y": null, "z": "\\u00e9"}')
    assert status(project, tmp_path, {"check": "json_valid"}) == "PASS"


def test_other_extensions_are_not_json(project: Path, tmp_path: Path) -> None:
    put(project, "notes.txt", "{not json")
    assert status(project, tmp_path, {"check": "json_valid"}) == "PASS"


# -- max_file_bytes ---------------------------------------------------------------------------


def test_max_file_bytes(project: Path, tmp_path: Path) -> None:
    put(project, "small.txt", "x" * 100)
    assert status(project, tmp_path, {"check": "max_file_bytes", "bytes": 100}) == "PASS"
    (tmp_path / "rel.zip").unlink()
    put(project, "big.bin", b"\x00" * 101)
    f = verdict(run(project, tmp_path, {"check": "max_file_bytes", "bytes": 100}), "max_file_bytes")
    assert f["status"] == "FAIL" and "big.bin" in f["evidence"]["problems"][0]


@pytest.mark.parametrize("bad", [{}, {"bytes": 0}, {"bytes": "100"}, {"bytes": True}])
def test_max_file_bytes_needs_a_positive_integer(
    project: Path, tmp_path: Path, bad: dict[str, Any]
) -> None:
    with pytest.raises(InputError):
        run(project, tmp_path, {"check": "max_file_bytes", **bad})


# -- no_todo_markers --------------------------------------------------------------------------


def test_todo_in_source_fails_but_todo_in_prose_does_not(project: Path, tmp_path: Path) -> None:
    put(project, "README.md", "We still have a TODO list on the wiki.\n")
    put(project, "notes.txt", "FIXME later")
    assert status(project, tmp_path, {"check": "no_todo_markers"}) == "PASS"
    (tmp_path / "rel.zip").unlink()
    put(project, "lib/core.js", "function f() {\n  // FIXME: broken\n}\n")
    f = verdict(run(project, tmp_path, {"check": "no_todo_markers"}), "no_todo_markers")
    assert f["status"] == "FAIL" and f["evidence"]["problems"] == ["lib/core.js:2"]


def test_todo_markers_respect_the_extensions_given_and_whole_words(
    project: Path, tmp_path: Path
) -> None:
    put(project, "app.py", "# todos are tracked elsewhere; AUTODOC is a word\n")
    assert status(project, tmp_path, {"check": "no_todo_markers"}) == "PASS"
    (tmp_path / "rel.zip").unlink()
    put(project, "x.cfg", "TODO: tune\n")
    assert status(project, tmp_path, {"check": "no_todo_markers", "extensions": [".cfg"]}) == "FAIL"


# -- no_hardcoded_credentials -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("config/upload.ini", "[upload]\ntoken = jd96Mdpne7hLGqmIZeJaBFi7oGSvBzoP\n"),
        ("deploy.sh", 'curl -H "Authorization: Bearer B3rHkd4HD5BCfuiqLco4FFallXLgFxdm1poe" x\n'),
        ("settings.py", "PASSWORD = 'Tr0ub4dor&3xyz'\n"),
        ("k.env", "API_KEY=9f8e7d6c5b4a39281706\n"),
        ("aws.txt", "key AKIAIOSFODNN7EXAMPLQ\n"),
        ("key.pem", "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----\n"),
        ("gh.txt", "ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"),
    ],
)
def test_credential_shapes_are_found(project: Path, tmp_path: Path, name: str, text: str) -> None:
    put(project, name, text)
    assert status(project, tmp_path, {"check": "no_hardcoded_credentials"}) == "FAIL"


@pytest.mark.parametrize(
    "text",
    [
        "token = changeme\n",
        "token = sample-token-000000\n",
        "password: <your-password>\n",
        "api_key = ${API_KEY}\n",
        "password: use the vault\n",  # prose
        "Never put a password: in a repository.\n",
        "secret = ********\n",
        "token = example-token-value-123\n",
        "Authorization: Bearer <token>\n",
        "A token is issued when you sign in.\n",
    ],
)
def test_placeholders_and_prose_are_not_credentials(
    project: Path, tmp_path: Path, text: str
) -> None:
    put(project, "docs/policy.md", text)
    assert status(project, tmp_path, {"check": "no_hardcoded_credentials"}) == "PASS", text


def test_extra_placeholders_can_be_declared(project: Path, tmp_path: Path) -> None:
    put(project, "c.ini", "token = Zq81mxPwR4tLd90a\n")
    assert status(project, tmp_path, {"check": "no_hardcoded_credentials"}) == "FAIL"
    (tmp_path / "rel.zip").unlink()
    check = {"check": "no_hardcoded_credentials", "placeholders": ["zq81mx"]}
    assert status(project, tmp_path, check) == "PASS"


def test_the_report_names_where_but_never_prints_the_secret(project: Path, tmp_path: Path) -> None:
    put(project, "c.ini", "token = Zq81mxPwR4tLd90a\n")
    result = run(project, tmp_path, {"check": "no_hardcoded_credentials"})
    assert "Zq81mxPwR4tLd90a" not in json.dumps(result)
    assert verdict(result, "no_hardcoded_credentials")["evidence"]["problems"] == [
        "c.ini:1 (password)"
    ]


# -- no_absolute_paths ------------------------------------------------------------------------


def test_absolute_paths_under_a_folder(project: Path, tmp_path: Path) -> None:
    put(project, "config/a.toml", 'data = "data/input.csv"\nurl = "https://example.org/x/y"\n')
    put(project, "docs/guide.md", "Open C:/Users/ada/notes.txt\n")  # outside config/
    assert status(project, tmp_path, {"check": "no_absolute_paths", "under": "config"}) == "PASS"
    (tmp_path / "rel.zip").unlink()
    put(project, "config/b.toml", 'path = "C:/Users/ada/proj"\n')
    put(project, "config/c.yaml", "home: /home/ada/work\n")
    f = verdict(
        run(project, tmp_path, {"check": "no_absolute_paths", "under": "config"}),
        "no_absolute_paths",
    )
    assert f["status"] == "FAIL" and len(f["evidence"]["problems"]) == 2


def test_relative_and_url_paths_are_not_absolute(project: Path, tmp_path: Path) -> None:
    put(
        project,
        "config/a.toml",
        'a = "./data"\nb = "../shared"\nc = "https://host/home/x"\nd = "//host/share"\n',
    )
    assert status(project, tmp_path, {"check": "no_absolute_paths", "under": "config"}) == "PASS"


def test_missing_folder_is_unchecked_not_passed(project: Path, tmp_path: Path) -> None:
    f = verdict(
        run(project, tmp_path, {"check": "no_absolute_paths", "under": "config"}),
        "no_absolute_paths",
    )
    assert f["status"] == "UNCHECKED" and "not a folder" in f["evidence"]["reason"]


# -- dependencies_pinned ----------------------------------------------------------------------


def test_dependencies_pinned(project: Path, tmp_path: Path) -> None:
    put(
        project,
        "requirements.txt",
        "# pinned\nrequests==2.31.0\nnumpy == 1.26.4  # comment\n-r other.txt\n\nflask[async]==3.0.0; python_version>'3.8'\n",
    )
    assert status(project, tmp_path, {"check": "dependencies_pinned"}) == "PASS"


def test_unpinned_dependencies_are_named(project: Path, tmp_path: Path) -> None:
    put(project, "requirements.txt", "requests>=2.0\nnumpy\npandas~=2.0\nflask==3.0.0\n")
    f = verdict(run(project, tmp_path, {"check": "dependencies_pinned"}), "dependencies_pinned")
    assert f["status"] == "FAIL"
    assert f["evidence"]["problems"] == ["requests>=2.0", "numpy", "pandas~=2.0"]


def test_missing_requirements_file_is_unchecked(project: Path, tmp_path: Path) -> None:
    assert status(project, tmp_path, {"check": "dependencies_pinned"}) == "UNCHECKED"


# -- license_matches --------------------------------------------------------------------------

MIT = (
    "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"
)
APACHE = "Apache License\nVersion 2.0, January 2004\n"
CC_BY = "Creative Commons Attribution 4.0 International Public License\n"


def test_license_matches_in_json_toml_and_yaml_metadata(project: Path, tmp_path: Path) -> None:
    put(project, "LICENSE", MIT)
    for name, text in [
        ("datapackage.json", '{"license": "MIT"}'),
        ("pyproject.toml", '[project]\nlicense = "MIT"\n'),
        ("package.json", '{"license": "MIT"}'),
        ("mkdocs.yml", "site_name: x\ncopyright: Copyright 2026 Ada (MIT)\n"),
        ("dp2.json", '{"licenses": [{"name": "MIT"}]}'),
    ]:
        put(project, name, text)
        check = {"check": "license_matches", "metadata": name}
        if (tmp_path / "rel.zip").exists():
            (tmp_path / "rel.zip").unlink()
        assert status(project, tmp_path, check) == "PASS", name


def test_license_mismatch_names_both_sides(project: Path, tmp_path: Path) -> None:
    put(project, "LICENSE", APACHE)
    put(project, "datapackage.json", '{"license": "MIT"}')
    f = verdict(run(project, tmp_path, {"check": "license_matches"}), "license_matches")
    assert f["status"] == "FAIL"
    assert f["evidence"]["declared"] == "mit" and f["evidence"]["identified"] == ["apache-2.0"]


def test_license_aliases_and_creative_commons(project: Path, tmp_path: Path) -> None:
    put(project, "LICENSE", CC_BY)
    put(project, "datapackage.json", '{"license": "CC-BY-4.0"}')
    assert status(project, tmp_path, {"check": "license_matches"}) == "PASS"


def test_unrecognised_license_text_is_unchecked(project: Path, tmp_path: Path) -> None:
    put(project, "LICENSE", "You may do whatever you like with this, probably.\n")
    put(project, "datapackage.json", '{"license": "MIT"}')
    f = verdict(run(project, tmp_path, {"check": "license_matches"}), "license_matches")
    assert f["status"] == "UNCHECKED" and "not a licence I recognise" in f["evidence"]["reason"]


def test_missing_metadata_or_license_file_is_unchecked(project: Path, tmp_path: Path) -> None:
    assert status(project, tmp_path, {"check": "license_matches"}) == "UNCHECKED"
    (tmp_path / "rel.zip").unlink()
    put(project, "datapackage.json", '{"name": "x"}')
    put(project, "LICENSE", MIT)
    assert status(project, tmp_path, {"check": "license_matches"}) == "UNCHECKED"


# -- version_matches_changelog ----------------------------------------------------------------


def test_version_matches_the_newest_release_and_skips_unreleased(
    project: Path, tmp_path: Path
) -> None:
    put(
        project,
        "CHANGELOG.md",
        "# Changelog\n\n## [Unreleased]\n- later\n\n## [1.2.0] - 2026-02-01\n- x\n\n## [1.1.0]\n",
    )
    put(project, "pyproject.toml", '[project]\nname = "x"\nversion = "1.2.0"\n')
    assert status(project, tmp_path, {"check": "version_matches_changelog"}) == "PASS"


def test_version_mismatch_names_both(project: Path, tmp_path: Path) -> None:
    put(project, "CHANGELOG.md", "## 1.1.1 - 2026-01-18\n- first\n")
    put(project, "datapackage.json", '{"version": "1.1.2"}')
    check = {"check": "version_matches_changelog", "metadata": "datapackage.json"}
    f = verdict(run(project, tmp_path, check), "version_matches_changelog")
    assert f["status"] == "FAIL" and (f["evidence"]["declared"], f["evidence"]["newest"]) == (
        "1.1.2",
        "1.1.1",
    )


def test_v_prefix_and_yaml_metadata(project: Path, tmp_path: Path) -> None:
    put(project, "CHANGELOG.md", "## v0.5.5\n")
    put(project, "mkdocs.yml", "site_name: x\nversion: 0.5.5\n")
    check = {"check": "version_matches_changelog", "metadata": "mkdocs.yml"}
    assert status(project, tmp_path, check) == "PASS"


def test_changelog_without_a_release_is_unchecked(project: Path, tmp_path: Path) -> None:
    put(project, "CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n- x\n")
    put(project, "pyproject.toml", 'version = "1.0.0"\n')
    assert status(project, tmp_path, {"check": "version_matches_changelog"}) == "UNCHECKED"


# -- row_counts_match -------------------------------------------------------------------------


def test_row_counts(project: Path, tmp_path: Path) -> None:
    put(project, "data/a.csv", "id,v\n1,x\n2,y\n3,z\n\n")  # a trailing blank line is not a row
    put(project, "datapackage.json", json.dumps({"resources": [{"path": "data/a.csv", "rows": 3}]}))
    assert status(project, tmp_path, {"check": "row_counts_match"}) == "PASS"


def test_row_count_mismatch_and_missing_csv(project: Path, tmp_path: Path) -> None:
    put(project, "data/a.csv", "id\n1\n2\n")
    put(
        project,
        "datapackage.json",
        json.dumps(
            {"resources": [{"path": "data/a.csv", "rows": 5}, {"path": "data/gone.csv", "rows": 1}]}
        ),
    )
    f = verdict(run(project, tmp_path, {"check": "row_counts_match"}), "row_counts_match")
    assert f["status"] == "FAIL"
    assert f["evidence"]["problems"] == [
        "data/a.csv: states 5 rows, has 2",
        "data/gone.csv: file is not in the release",
    ]


def test_no_stated_row_counts_is_unchecked(project: Path, tmp_path: Path) -> None:
    put(project, "datapackage.json", json.dumps({"resources": [{"path": "data/a.csv"}]}))
    assert status(project, tmp_path, {"check": "row_counts_match"}) == "UNCHECKED"


# -- the list as a whole ----------------------------------------------------------------------


def test_unknown_check_and_bad_shapes_are_rejected(project: Path, tmp_path: Path) -> None:
    for bad in ({"check": "no_such_thing"}, {}, {"check": "json_valid", "under": 3}):
        with pytest.raises(InputError):
            run(project, tmp_path, bad)
    with pytest.raises(InputError):
        releaseproof({"checks": "json_valid"}, project, "build", tmp_path / "x.zip")
    assert not (tmp_path / "x.zip").exists()  # rejected before anything was built


def test_a_path_cannot_leave_the_release(project: Path, tmp_path: Path) -> None:
    with pytest.raises(InputError, match="escapes"):
        run(project, tmp_path, {"check": "no_absolute_paths", "under": "../.."})


def test_unchecked_stops_the_release_being_ready_and_fails_strict(
    project: Path, tmp_path: Path
) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "expected_files": ["app.py"],
                "commands": [["{python}", "app.py"]],
                "checks": [{"check": "dependencies_pinned"}],
            }
        ),
        encoding="utf-8",
    )  # no requirements.txt
    base = ["build", str(spec), "--root", str(project), "--allow-execution", "--strict"]
    code = main([*base, "--archive", str(tmp_path / "r.zip"), "-o", str(tmp_path / "o.json")])
    assert code == 1
    report = json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))
    assert report["release_ready"] is False
    assert report["coverage"]["reasons"] == {"inputs_missing": 1}


def test_coverage_counts_content_checks(project: Path, tmp_path: Path) -> None:
    put(project, "requirements.txt", "requests==2.31.0\n")
    result = run(
        project, tmp_path,
        {"check": "json_valid"}, {"check": "dependencies_pinned"}, {"check": "license_matches"},
    )  # fmt: skip
    block = result["coverage"]
    assert (block["items"], block["decided"], block["undecided"]) == (3, 2, 1)
    assert block["reasons"] == {"inputs_missing": 1}
    assert block["decided"] + block["undecided"] == block["items"]


def test_no_checks_changes_nothing(project: Path, tmp_path: Path) -> None:
    result = releaseproof({}, project, "build", tmp_path / "rel.zip")
    assert result["coverage"]["items"] == 0
    assert all("check" not in f["evidence"] for f in result["findings"])
