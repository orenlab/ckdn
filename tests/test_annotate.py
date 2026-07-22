# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Rendering a digest's findings to GitHub annotations / SARIF (`ckdn annotate`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ckdn import cli
from ckdn.annotate import split_location, to_github, to_sarif
from ckdn.config import load_config
from ckdn.digest import DIGEST_NAME, dump_json
from ckdn.runner import create_run_dir, update_latest

_DIGEST: dict[str, Any] = {
    "schema": "ckdn.digest/2",
    "check": "ruff",
    "status": "fail",
    "rc": 1,
    "run_dir": ".agent-runs/x",
    "findings": [
        {
            "id": "F401",
            "kind": "lint",
            "message": "os imported but unused",
            "location": "src/a.py:1:8",
        },
        {"id": "tests::x", "kind": "test_failure", "message": "assert 1 == 2"},
    ],
}


def test_split_location() -> None:
    assert split_location("a/b.py:12:3") == ("a/b.py", 12, 3)
    assert split_location("a/b.py:12") == ("a/b.py", 12, None)
    assert split_location("a/b.py") == ("a/b.py", None, None)
    assert split_location(None) == (None, None, None)


def test_to_github() -> None:
    lines = to_github(_DIGEST)
    assert lines[0] == (
        "::error file=src/a.py,line=1,col=8,title=F401::os imported but unused"
    )
    # no location -> only title; colons in the id are escaped
    assert lines[1] == "::error title=tests%3A%3Ax::assert 1 == 2"


def test_to_github_escapes_newlines() -> None:
    digest = {"findings": [{"id": "x", "kind": "k", "message": "line1\nline2"}]}
    assert to_github(digest)[0].endswith("::line1%0Aline2")


def test_to_sarif() -> None:
    doc = to_sarif(_DIGEST)
    assert doc["version"] == "2.1.0"
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "ckdn"
    assert {r["id"] for r in driver["rules"]} == {"lint", "test_failure"}
    first = doc["runs"][0]["results"][0]
    assert first["ruleId"] == "lint" and first["level"] == "error"
    physical = first["locations"][0]["physicalLocation"]
    assert physical["artifactLocation"]["uri"] == "src/a.py"
    assert physical["region"] == {"startLine": 1, "startColumn": 8}


def test_no_findings_produces_nothing() -> None:
    assert to_github({"status": "pass"}) == []
    assert to_sarif({"status": "pass"})["runs"][0]["results"] == []


def test_cli_annotate_github_and_sarif(tmp_path: Path, capsys: Any) -> None:
    cfg_path = tmp_path / "ckdn.toml"
    cfg_path.write_text(
        '[run]\nruns_dir = ".agent-runs"\n'
        '[check.a]\ncommand = "true"\nparser = "generic"\n',
        encoding="utf-8",
    )
    cfg = load_config(cfg_path, cwd=tmp_path)
    run_dir = create_run_dir(cfg.runs_dir, "ruff")
    (run_dir / DIGEST_NAME).write_text(dump_json(_DIGEST), encoding="utf-8")
    update_latest(cfg.runs_dir, run_dir)

    args = ["--config", str(cfg_path), "--cwd", str(tmp_path)]
    assert cli.main(["annotate", *args]) == 0
    assert "::error file=src/a.py,line=1,col=8" in capsys.readouterr().out

    import json

    assert cli.main(["annotate", *args, "--format", "sarif"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["version"] == "2.1.0" and doc["runs"][0]["results"]
