# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Parser lab: every built-in parser against real tool output.

Unit tests pin parsers to output someone captured once. Tools keep changing
it -- ruff 0.16 rewrote ``format --check``, and ty was found to exit 1 on
warnings alone -- so this lab runs the real tools instead. Each scenario goes
through ``ckdn run`` and its digest is compared with an independent reading of
the same raw output (JUnit counts, ``Found N``, report arrays, SARIF results):

* the status must follow the tool's exit code (``fail`` when it is nonzero or
  a policy gate breaks, ``pass`` when it is zero) -- never ``error`` or
  ``parse_mismatch`` on output the tool produced correctly;
* the number of findings must equal what the tool itself reported.

Usage::

    python scripts/parser_lab.py {latest,previous,oldest} [--workdir DIR]

Needs ``uv`` on PATH. Builds a throwaway venv holding ckdn from this checkout
and the toolset's tools (``scripts/parser_lab.toml``); ``pip-audit`` queries
the public vulnerability service. Exit 1 on any disagreement.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TOOLSETS = Path(__file__).with_suffix(".toml")

Truth = Callable[[Path], int]

# --- fixture project -------------------------------------------------------------

_TESTS_PASS = """
import pytest

from pkg.good import add
from pkg.partial import used


def test_add() -> None:
    assert add(1, 2) == 3
    assert used(2) == 4


@pytest.mark.skip(reason="not today")
def test_skipped() -> None:
    raise AssertionError


@pytest.mark.xfail(reason="known")
def test_xfail() -> None:
    assert False
"""

_TESTS_FAIL = """
import pytest

from pkg.good import add


@pytest.fixture
def broken() -> int:
    raise RuntimeError("fixture exploded")


def test_ok() -> None:
    assert add(1, 1) == 2


def test_wrong() -> None:
    assert add(2, 2) == 5


def test_errors(broken: int) -> None:
    assert broken
"""

_TYPES_ERR = """
x: int = "a"


def f() -> str:
    return 1


class A:
    def m(self, v: int) -> None: ...


class B(A):
    def m(self, v: str) -> None: ...
"""

_PRECOMMIT_OK_HOOK = """
      - id: always-ok
        name: always ok
        entry: python -c "pass"
        language: system
        pass_filenames: false
"""

FILES: dict[str, str] = {
    "conftest.py": "",
    "pkg/__init__.py": "",
    "pkg/good.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
    "pkg/partial.py": (
        "def used(x: int) -> int:\n    return x * 2\n\n\n"
        "def unused(x: int) -> int:\n    if x > 0:\n        return 1\n    return 2\n"
    ),
    "tests_pass/test_ok.py": _TESTS_PASS,
    "tests_fail/test_bad.py": _TESTS_FAIL,
    "lintdir/imports.py": "import os\nimport sys\n",
    "lintdir/broken.py": "def f(:\n",
    "lintok/fine.py": "def f(x: int) -> int:\n    return x + 1\n",
    "types_err/a.py": _TYPES_ERR,
    "types_ok/a.py": "def f(v: int) -> int:\n    return v\n",
    "ty_warn/pyproject.toml": '[tool.ty.rules]\ninvalid-assignment = "warn"\n',
    "ty_warn/a.py": 'x: int = "a"\n',
    "ty_warn/mix.py": 'x: int = "a"\n\n\ndef f() -> str:\n    return 1\n',
    "pyright_warn/pyrightconfig.json": '{"reportUnusedVariable": "warning"}\n',
    "pyright_warn/a.py": "def f() -> None:\n    unused = 1\n",
    "fmt_bad/a.py": "x=1\n",
    "fmt_bad/sub/b.py": "y  =  2\n",
    "fmt_bad/ok.py": "z = 3\n",
    "fmt_ok/ok.py": "z = 3\n",
    "secdir/a.py": (
        "import subprocess\n\n\ndef run(cmd: str) -> None:\n"
        "    subprocess.call(cmd, shell=True)\n\n\n"
        "def check(x: int) -> None:\n    assert x > 0\n"
    ),
    "secok/a.py": "def f(x: int) -> int:\n    return x\n",
    "pylintdir/a.py": '"""Doc."""\nimport os\nimport sys\n',
    "pylintinfo/a.py": (
        '"""Doc."""\nimport os  # pylint: disable=unused-import\n\nprint(os.getcwd())\n'
    ),
    "pylintok/a.py": '"""Doc."""\nprint("hello")\n',
    "req_vuln.txt": "requests==2.19.1\n",
    "req_clean.txt": "six==1.17.0\n",
    "req_private.txt": "zz-ckdn-lab-not-on-pypi==0.1.0\n",
    "scanbad/broken.py": "def f(:\n",
    "scanbad/fine.py": "x = 1\n",
    "hooks/todo.py": "# FIXME: later\n",
    ".pre-commit-ok.yaml": "repos:\n  - repo: local\n    hooks:" + _PRECOMMIT_OK_HOOK,
    ".pre-commit-skip.yaml": (
        "repos:\n  - repo: local\n    hooks:\n      - id: no-fixme\n"
        "        name: no FIXME markers\n        entry: FIXME\n"
        "        language: pygrep\n        files: ^no-such-dir/\n"
    ),
    ".pre-commit-config.yaml": (
        "repos:\n  - repo: local\n    hooks:"
        + _PRECOMMIT_OK_HOOK
        + "      - id: no-fixme\n        name: no FIXME markers\n"
        "        entry: FIXME\n        language: pygrep\n        files: ^hooks/\n"
    ),
}


def write_fixture(proj: Path) -> None:
    for rel, body in FILES.items():
        path = proj / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    # pre-commit only sees files the index knows about.
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)


# --- independent truth: what the tool itself reported ------------------------


def _log(run_dir: Path) -> str:
    return (run_dir / "full.log").read_text(encoding="utf-8", errors="replace")


def _report(run_dir: Path, name: str) -> Any:
    return json.loads((run_dir / name).read_text(encoding="utf-8"))


def lines_matching(pattern: str) -> Truth:
    regex = re.compile(pattern, re.MULTILINE)
    return lambda run_dir: len(regex.findall(_log(run_dir)))


def declared(pattern: str) -> Truth:
    regex = re.compile(pattern)

    def count(run_dir: Path) -> int:
        match = regex.search(_log(run_dir))
        return int(match.group(1)) if match else 0

    return count


def junit_failures(run_dir: Path) -> int:
    root = ET.parse(run_dir / "junit.xml").getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    return sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)


def ruff_violations(run_dir: Path) -> int:
    return len(_report(run_dir, "ruff.json"))


def mypy_json_errors(run_dir: Path) -> int:
    rows = [ln for ln in _log(run_dir).splitlines() if ln.startswith("{")]
    return sum(json.loads(row).get("severity") == "error" for row in rows)


def pyright_errors(run_dir: Path) -> int:
    text = _log(run_dir)
    data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    return int(data["summary"]["errorCount"])


def pip_audit_vulns(run_dir: Path) -> int:
    deps = _report(run_dir, "pip-audit.json")["dependencies"]
    return sum(len(dep.get("vulns", [])) for dep in deps)


def bandit_results(run_dir: Path) -> int:
    return len(_report(run_dir, "bandit.json")["results"])


def pylint_messages(run_dir: Path) -> int:
    messages = _report(run_dir, "pylint.json")["messages"]
    return sum(msg["type"] != "info" for msg in messages)


def sarif_results(levels: tuple[str, ...]) -> Truth:
    def count(run_dir: Path) -> int:
        runs = _report(run_dir, "report.sarif")["runs"]
        results = [res for run in runs for res in run.get("results", [])]
        return sum(res.get("level", "warning") in levels for res in results)

    return count


def zero(run_dir: Path) -> int:
    return 0


TY_ERRORS = lines_matching(r"^error\[")
MYPY_TEXT = declared(r"Found (\d+) errors?")
REFORMAT = declared(r"(\d+) files? would be reformatted")
PRECOMMIT_FAILED = lines_matching(r"\.+Failed\s*$")
ALL_SARIF = ("error", "warning", "note")

# --- scenarios -------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    name: str
    command: str
    parser: str
    truth: Truth
    options: Mapping[str, object] = field(default_factory=dict)
    subdir: str = ""
    gated: bool = False  # a policy gate turns a green tool run red


_JUNIT = "pytest -q -p no:cacheprovider --junitxml {run_dir}/junit.xml"
_COV = f"{_JUNIT} --cov=pkg --cov-report=xml:{{run_dir}}/coverage.xml"
_COV_ENV = {"env": {"COVERAGE_FILE": "{run_dir}/.coverage"}}
_RUFF = "ruff check --no-cache --output-format"
_MYPY = "mypy --no-incremental --cache-dir"
_AUDIT = "pip-audit --progress-spinner off -f json -o {run_dir}/pip-audit.json"
_AUDIT_OPTS = {"timeout": 300}
_PYLINT = "pylint --disable=all --enable=unused-import"
_PYLINT_OUT = "--output-format=json2:{run_dir}/pylint.json"
_BANDIT = "bandit -q -r secdir"

SCENARIOS: tuple[Scenario, ...] = (
    Scenario("pytest_fail", f"{_JUNIT} tests_fail", "pytest", junit_failures),
    Scenario("pytest_pass", f"{_JUNIT} tests_pass", "pytest", junit_failures),
    Scenario(
        "cov_gate",
        f"{_COV} tests_pass",
        "coverage",
        junit_failures,
        {"fail_under": 100.0, **_COV_ENV},
        gated=True,
    ),
    Scenario(
        "cov_pass",
        f"{_COV} tests_pass",
        "coverage",
        junit_failures,
        {"fail_under": 50.0, **_COV_ENV},
    ),
    Scenario(
        "cov_testfail", f"{_COV} tests_fail", "coverage", junit_failures, _COV_ENV
    ),
    Scenario(
        "ruff_fail",
        f"{_RUFF} json --output-file {{run_dir}}/ruff.json lintdir",
        "ruff",
        ruff_violations,
    ),
    Scenario(
        "ruff_pass",
        f"{_RUFF} json --output-file {{run_dir}}/ruff.json lintok",
        "ruff",
        ruff_violations,
    ),
    Scenario("ty_err", "ty check types_err", "ty", TY_ERRORS),
    Scenario("ty_ok", "ty check types_ok", "ty", TY_ERRORS),
    Scenario(
        "ty_concise_err",
        "ty check --output-format concise types_err",
        "ty",
        lines_matching(r":\d+:\d+: error\["),
    ),
    # ty exits 1 on warnings alone: then the warnings are the findings.
    Scenario(
        "ty_warn",
        "ty check a.py",
        "ty",
        lines_matching(r"^warning\["),
        subdir="ty_warn",
    ),
    Scenario(
        "ty_warn_concise",
        "ty check --output-format concise a.py",
        "ty",
        lines_matching(r":\d+:\d+: warning\["),
        subdir="ty_warn",
    ),
    Scenario(
        "ty_warn_exit_zero", "ty check --exit-zero a.py", "ty", zero, subdir="ty_warn"
    ),
    Scenario("ty_mix", "ty check mix.py", "ty", TY_ERRORS, subdir="ty_warn"),
    Scenario("mypy_text_err", f"{_MYPY} .mypy_t types_err", "mypy", MYPY_TEXT),
    Scenario("mypy_text_ok", f"{_MYPY} .mypy_t types_ok", "mypy", MYPY_TEXT),
    Scenario(
        "mypy_pretty_err", f"{_MYPY} .mypy_p --pretty types_err", "mypy", MYPY_TEXT
    ),
    Scenario(
        "mypy_cols_err",
        f"{_MYPY} .mypy_c --show-column-numbers --show-error-end types_err",
        "mypy",
        MYPY_TEXT,
    ),
    Scenario(
        "mypy_json_err",
        f"{_MYPY} .mypy_j --output json types_err",
        "mypy",
        mypy_json_errors,
        {"format": "json"},
    ),
    Scenario(
        "mypy_json_ok",
        f"{_MYPY} .mypy_j --output json types_ok",
        "mypy",
        mypy_json_errors,
        {"format": "json"},
    ),
    Scenario(
        "pyright_err", "pyright --outputjson types_err", "pyright", pyright_errors
    ),
    Scenario("pyright_ok", "pyright --outputjson types_ok", "pyright", pyright_errors),
    Scenario(
        "pyright_warn",
        "pyright --outputjson a.py",
        "pyright",
        pyright_errors,
        subdir="pyright_warn",
    ),
    Scenario(
        "fmt_ruff_fail", "ruff format --no-cache --check fmt_bad", "reformat", REFORMAT
    ),
    Scenario(
        "fmt_ruff_concise",
        "ruff format --no-cache --check --output-format concise fmt_bad",
        "reformat",
        REFORMAT,
    ),
    Scenario(
        "fmt_ruff_ok", "ruff format --no-cache --check fmt_ok", "reformat", REFORMAT
    ),
    Scenario("fmt_black_fail", "black --check fmt_bad", "reformat", REFORMAT),
    Scenario("fmt_black_ok", "black --check fmt_ok", "reformat", REFORMAT),
    Scenario(
        "audit_fail",
        f"{_AUDIT} --no-deps --disable-pip -r req_vuln.txt",
        "pip_audit",
        pip_audit_vulns,
        _AUDIT_OPTS,
    ),
    Scenario(
        "audit_ok",
        f"{_AUDIT} --no-deps --disable-pip -r req_clean.txt",
        "pip_audit",
        pip_audit_vulns,
        _AUDIT_OPTS,
    ),
    # a package pip-audit cannot audit: rc 0, and unaudited is not clean
    Scenario(
        "audit_skipped",
        f"{_AUDIT} --no-deps --disable-pip -r req_private.txt",
        "pip_audit",
        pip_audit_vulns,
        _AUDIT_OPTS,
        gated=True,
    ),
    Scenario(
        "audit_skipped_ok",
        f"{_AUDIT} --no-deps --disable-pip -r req_private.txt",
        "pip_audit",
        pip_audit_vulns,
        {**_AUDIT_OPTS, "allow_skipped": ["zz-ckdn-lab-not-on-pypi"]},
    ),
    # a file bandit cannot parse: rc 0, and unscanned is not clean
    Scenario(
        "bandit_unscanned",
        "bandit -q -r scanbad -f json -o {run_dir}/bandit.json",
        "bandit",
        bandit_results,
        gated=True,
    ),
    Scenario(
        "bandit_fail",
        f"{_BANDIT} -f json -o {{run_dir}}/bandit.json",
        "bandit",
        bandit_results,
    ),
    Scenario(
        "bandit_high",
        f"{_BANDIT} --severity-level high -f json -o {{run_dir}}/bandit.json",
        "bandit",
        bandit_results,
    ),
    Scenario(
        "bandit_ok",
        "bandit -q -r secok -f json -o {run_dir}/bandit.json",
        "bandit",
        bandit_results,
    ),
    Scenario(
        "pylint_fail", f"{_PYLINT} {_PYLINT_OUT} pylintdir", "pylint", pylint_messages
    ),
    Scenario(
        "pylint_info",
        f"{_PYLINT},useless-suppression {_PYLINT_OUT} pylintinfo",
        "pylint",
        pylint_messages,
    ),
    Scenario(
        "pylint_score",
        f"{_PYLINT} {_PYLINT_OUT} pylintdir",
        "pylint",
        pylint_messages,
        {"score_fail_under": 10.0},
    ),
    Scenario(
        "pylint_score_ok",
        f"{_PYLINT} {_PYLINT_OUT} pylintok",
        "pylint",
        pylint_messages,
        {"score_fail_under": 9.0},
    ),
    Scenario(
        "pylint_ok", f"{_PYLINT} {_PYLINT_OUT} pylintok", "pylint", pylint_messages
    ),
    Scenario(
        "sarif_ruff",
        f"{_RUFF} sarif --output-file {{run_dir}}/report.sarif lintdir",
        "sarif",
        sarif_results(ALL_SARIF),
        {"fail_levels": list(ALL_SARIF)},
    ),
    Scenario(
        "sarif_bandit",
        f"{_BANDIT} -f sarif -o {{run_dir}}/report.sarif",
        "sarif",
        sarif_results(ALL_SARIF),
        {"fail_levels": list(ALL_SARIF)},
    ),
    # default fail_levels: only `error` results are findings (documented)
    Scenario(
        "sarif_bandit_default",
        f"{_BANDIT} -f sarif -o {{run_dir}}/report.sarif",
        "sarif",
        sarif_results(("error",)),
    ),
    Scenario(
        "precommit_fail",
        "pre-commit run --all-files",
        "pre_commit",
        PRECOMMIT_FAILED,
        {"timeout": 300},
    ),
    Scenario(
        "precommit_ok",
        "pre-commit run --all-files --config .pre-commit-ok.yaml",
        "pre_commit",
        PRECOMMIT_FAILED,
        {"timeout": 300},
    ),
    # every hook skipped: rc 0, and nothing was checked
    Scenario(
        "precommit_skipped",
        "pre-commit run --all-files --config .pre-commit-skip.yaml",
        "pre_commit",
        PRECOMMIT_FAILED,
        {"timeout": 300},
        gated=True,
    ),
)

# --- running ---------------------------------------------------------------------


def _toml_value(value: object) -> str:
    if isinstance(value, dict):
        pairs = ", ".join(f"{key} = {json.dumps(item)}" for key, item in value.items())
        return "{ " + pairs + " }"
    return json.dumps(value)


def write_configs(proj: Path) -> None:
    """One ckdn.toml per directory: warnings-only checks need their tool config."""
    for subdir in sorted({sc.subdir for sc in SCENARIOS}):
        lines = ['[run]\nruns_dir = ".agent-runs"\nkeep = 500\ntop = 50\n']
        for sc in (s for s in SCENARIOS if s.subdir == subdir):
            lines.append(f"[check.{sc.name}]")
            lines.append(f"command = {json.dumps(sc.command)}")
            lines.append(f'parser = "{sc.parser}"')
            lines.extend(f"{k} = {_toml_value(v)}" for k, v in sc.options.items())
            lines.append("")
        (proj / subdir / "ckdn.toml").write_text("\n".join(lines), encoding="utf-8")


def build_venv(toolset: str, work: Path) -> Path:
    """Throwaway venv with ckdn from this checkout and the toolset's tools."""
    spec = tomllib.loads(TOOLSETS.read_text(encoding="utf-8"))[toolset]
    venv = work / "venv"
    subprocess.run(["uv", "venv", "-q", "-p", spec["python"], str(venv)], check=True)
    python = str(venv / "bin" / "python")
    install = ["uv", "pip", "install", "-q", "--python", python, str(ROOT)]
    subprocess.run([*install, *spec["tools"]], check=True)
    listing = subprocess.run(
        ["uv", "pip", "list", "--python", python, "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    wanted = {re.split(r"[\[=<>]", tool)[0] for tool in spec["tools"]} | {"ckdn"}
    versions = {p["name"]: p["version"] for p in json.loads(listing.stdout)}
    print(f"{toolset}:", ", ".join(f"{n} {versions[n]}" for n in sorted(wanted)))
    return venv / "bin"


def _verdict(sc: Scenario, digest: dict[str, Any], truth: int) -> tuple[bool, str]:
    expected = "fail" if digest["rc"] != 0 or sc.gated else "pass"
    found = int(digest.get("findings_total", len(digest.get("findings", []))))
    ok = digest["status"] == expected and found == truth
    if sc.gated and not digest.get("gate_failures"):
        ok = False
    detail = (
        f"status={digest['status']} (want {expected}) rc={digest['rc']} "
        f"findings={found} tool={truth}"
    )
    if digest.get("notes") and not ok:
        detail += f" notes={digest['notes']}"
    return ok, detail


def run_scenario(bin_dir: Path, proj: Path, sc: Scenario) -> tuple[bool, str]:
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
    env["VIRTUAL_ENV"] = str(bin_dir.parent)
    for key in ("CKDN_CWD", "CKDN_CONFIG"):
        env.pop(key, None)
    root = proj / sc.subdir
    proc = subprocess.run(
        [str(bin_dir / "ckdn"), "run", sc.name],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    try:
        digest = json.loads(proc.stdout)
        truth = sc.truth(root / digest["run_dir"])
    except (ValueError, KeyError, OSError, ET.ParseError) as exc:
        return False, f"no usable result ({exc!r}); stderr={proc.stderr.strip()[:200]}"
    return _verdict(sc, digest, truth)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("toolset", choices=["latest", "previous", "oldest"])
    parser.add_argument("--workdir", type=Path, help="default: a fresh temp dir")
    args = parser.parse_args(argv)
    work = args.workdir or Path(tempfile.mkdtemp(prefix="ckdn-parser-lab-"))
    proj = work / "proj"
    proj.mkdir(parents=True)
    bin_dir = build_venv(args.toolset, work)
    write_fixture(proj)
    write_configs(proj)
    failed = 0
    for sc in SCENARIOS:
        ok, detail = run_scenario(bin_dir, proj, sc)
        failed += not ok
        print(f"[{'PASS' if ok else 'FAIL'}] {sc.name:22} {detail}")
    print(f"{len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios agree with the tool")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
