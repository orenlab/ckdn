# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""Tests for the stdlib application facade."""

from __future__ import annotations

from pathlib import Path

import pytest

from ckdn.app import (
    MAX_EVIDENCE_LIMIT,
    AliasExtraArgsError,
    ArtifactError,
    DigestError,
    NotAliasError,
    NotAtomicError,
    RunNotFoundError,
    UnknownCheckError,
    get_digest,
    get_evidence,
    list_checks,
    list_runs,
    run_alias,
    run_check,
    run_one,
)
from ckdn.config import Config, load_config
from ckdn.digest import DIGEST_NAME, META_NAME
from ckdn.runner import LOG_NAME, create_run_dir, resolve_run_dir, update_latest


def _write_cfg(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ckdn.toml"
    path.write_text(
        '[run]\nruns_dir = ".agent-runs"\nkeep = 20\n\n' + body,
        encoding="utf-8",
    )
    return path


def _load_cfg(tmp_path: Path, body: str) -> Config:
    return load_config(_write_cfg(tmp_path, body), cwd=tmp_path)


def test_list_checks_shapes(tmp_path: Path) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        '[check.g]\nmembers = ["ok"]\nfail_fast = false\n',
    )
    items = {c["name"]: c for c in list_checks(cfg)}
    assert items["ok"]["kind"] == "atomic"
    assert items["ok"]["parser"] == "generic"
    assert items["g"]["kind"] == "alias"
    assert items["g"]["members"] == ["ok"]
    assert items["g"]["fail_fast"] is False


def test_run_one_and_digest_roundtrip(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    result = run_one(cfg, cfg.checks["ok"], extra=[])
    assert result.status == "pass"
    assert result.exit_code == 0
    assert result.digest["schema"] == "ckdn.digest/2"
    assert result.digest["rc"] == 0
    loaded = get_digest(cfg)
    assert loaded["check"] == "ok"
    assert loaded["status"] == "pass"


def test_run_check_unknown_and_alias_extra(tmp_path: Path) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        '[check.g]\nmembers = ["ok"]\n',
    )
    with pytest.raises(UnknownCheckError):
        run_check(cfg, "nope")
    with pytest.raises(NotAtomicError):
        run_one(cfg, cfg.checks["g"], extra=[])
    with pytest.raises(AliasExtraArgsError):
        run_check(cfg, "g", extra=["-x"])


def test_run_alias_aggregate(tmp_path: Path) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        '[check.g]\nmembers = ["ok"]\n',
    )
    result = run_alias(cfg, cfg.checks["g"])
    assert result.exit_code == 0
    assert result.aggregate["schema"] == "ckdn.aggregate/1"
    assert result.aggregate["alias"] == "g"
    assert result.aggregate["status"] == "pass"
    assert result.aggregate["rc"] == 0
    assert len(result.members) == 1


def test_list_runs_and_evidence_bounds(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_one(cfg, cfg.checks["ok"], extra=[])
    rows = list_runs(cfg, limit=5)
    assert rows
    assert rows[-1]["check"] == "ok"

    evidence = get_evidence(cfg)
    assert "artifacts" in evidence
    assert "artifact" not in evidence  # no body without explicit artifact=
    assert LOG_NAME in evidence["artifacts"]

    log_ev = get_evidence(cfg, artifact=LOG_NAME, offset=0, limit=2)
    assert log_ev["artifact"]["name"] == LOG_NAME
    assert log_ev["artifact"]["limit"] == 2
    assert len(log_ev["artifact"]["lines"]) <= 2

    capped = get_evidence(cfg, artifact=LOG_NAME, limit=MAX_EVIDENCE_LIMIT + 50)
    assert capped["artifact"]["limit"] == MAX_EVIDENCE_LIMIT


def test_evidence_rejects_path_escape(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_one(cfg, cfg.checks["ok"], extra=[])
    with pytest.raises(ArtifactError):
        get_evidence(cfg, artifact="../secrets.txt")
    with pytest.raises(ArtifactError):
        get_evidence(cfg, artifact="/etc/passwd")
    with pytest.raises(ArtifactError):
        get_evidence(cfg, artifact="missing.json")


def test_read_path_rejects_escaping_run(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_one(cfg, cfg.checks["ok"], extra=[])
    # A sibling run dir outside runs_dir that must stay unreachable via MCP.
    outside = tmp_path / "victim" / "run"
    outside.mkdir(parents=True)
    (outside / DIGEST_NAME).write_text('{"check": "secret"}', encoding="utf-8")
    (outside / LOG_NAME).write_text("TOP_SECRET\n", encoding="utf-8")

    for bad in (str(outside), "..", ".", "../victim/run", "sub/run"):
        # Escaped/invalid refs read as "no such run", not "nothing has run".
        with pytest.raises(RunNotFoundError, match="not a valid run id"):
            get_digest(cfg, bad)
        with pytest.raises(RunNotFoundError, match="not a valid run id"):
            get_evidence(cfg, ref=bad)
        with pytest.raises(RunNotFoundError, match="not a valid run id"):
            get_evidence(cfg, ref=bad, artifact=LOG_NAME)


def test_read_path_rejects_symlinked_run(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_one(cfg, cfg.checks["ok"], extra=[])
    outside = tmp_path / "victim"
    outside.mkdir()
    (outside / DIGEST_NAME).write_text('{"check": "secret"}', encoding="utf-8")
    (outside / LOG_NAME).write_text("TOP_SECRET\n", encoding="utf-8")
    evil = cfg.runs_dir / "evil"
    try:
        evil.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(RunNotFoundError):
        get_digest(cfg, "evil")
    with pytest.raises(RunNotFoundError):
        get_evidence(cfg, ref="evil", artifact=LOG_NAME)


def test_evidence_digest_bound_to_run_dir(tmp_path: Path) -> None:
    """The digest is read from the resolved run dir, never a same-named decoy."""
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_one(cfg, cfg.checks["ok"], extra=[])  # latest → check "ok"
    decoy = create_run_dir(cfg.runs_dir, "decoy")
    (decoy / DIGEST_NAME).write_text(
        '{"schema": "ckdn.digest/2", "check": "decoy", '
        '"status": "pass", "rc": 0, "run_dir": "decoy"}',
        encoding="utf-8",
    )
    (decoy / LOG_NAME).write_text("decoy body\n", encoding="utf-8")
    ev = get_evidence(cfg, ref=decoy.name, artifact=LOG_NAME)
    assert ev["run_id"] == decoy.name
    assert ev["check"] == "decoy"  # digest came from decoy, not from latest
    assert ev["artifact"]["lines"] == ["decoy body"]


def test_evidence_streaming_slice_bounds(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_one(cfg, cfg.checks["ok"], extra=[])
    run_dir = resolve_run_dir(cfg.runs_dir, None)
    assert run_dir is not None
    (run_dir / LOG_NAME).write_text(
        "".join(f"line{i}\n" for i in range(50)), encoding="utf-8"
    )
    mid = get_evidence(cfg, artifact=LOG_NAME, offset=10, limit=5)["artifact"]
    assert mid["total_lines"] == 50
    assert mid["lines"] == [f"line{i}" for i in range(10, 15)]
    assert mid["truncated"] is True

    tail = get_evidence(cfg, artifact=LOG_NAME, offset=48, limit=10)["artifact"]
    assert tail["lines"] == ["line48", "line49"]
    assert tail["truncated"] is False


def test_slice_artifact_lines_edges_and_cap(tmp_path: Path) -> None:
    from ckdn.app.queries import _MAX_EVIDENCE_LINE_BYTES, _slice_artifact_lines

    p = tmp_path / "a.log"
    # CRLF + LF, no trailing newline.
    p.write_bytes(b"a\r\nb\nc")
    assert _slice_artifact_lines(p, 0, 10) == (["a", "b", "c"], 3)
    # Empty file → no lines.
    p.write_bytes(b"")
    assert _slice_artifact_lines(p, 0, 10) == ([], 0)
    # Trailing newline does not fabricate an extra line.
    p.write_bytes(b"x\ny\n")
    assert _slice_artifact_lines(p, 0, 10) == (["x", "y"], 2)
    # A lone blank line is one empty line.
    p.write_bytes(b"\n")
    assert _slice_artifact_lines(p, 0, 10) == ([""], 1)
    # A single unbounded line is length-capped, not loaded whole.
    p.write_bytes(b"z" * (_MAX_EVIDENCE_LINE_BYTES + 5000))
    lines, total = _slice_artifact_lines(p, 0, 10)
    assert total == 1
    assert len(lines[0]) == _MAX_EVIDENCE_LINE_BYTES


def test_list_runs_limit_capped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ckdn.app import queries as q

    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    for _ in range(4):
        run_one(cfg, cfg.checks["ok"], extra=[])
    monkeypatch.setattr(q, "MAX_LIST_RUNS_LIMIT", 2)
    assert len(list_runs(cfg, limit=10_000)) == 2  # clamped to the cap
    assert list_runs(cfg, limit=0) == []  # zero means none, not everything


def test_get_digest_missing_run(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    with pytest.raises(RunNotFoundError):
        get_digest(cfg)


def test_corrupt_digest(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_dir = create_run_dir(cfg.runs_dir, "ok")
    (run_dir / DIGEST_NAME).write_text("{not-json", encoding="utf-8")
    update_latest(cfg.runs_dir, run_dir)
    with pytest.raises(DigestError):
        get_digest(cfg)


def test_list_checks_timeout_and_options(tmp_path: Path) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\ntimeout = 1.5\ntop = 3\n',
    )
    item = {c["name"]: c for c in list_checks(cfg)}["ok"]
    assert item["timeout"] == 1.5
    assert item["options"]["top"] == 3


def test_get_digest_non_object(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    run_dir = create_run_dir(cfg.runs_dir, "ok")
    (run_dir / DIGEST_NAME).write_text("[1, 2]", encoding="utf-8")
    update_latest(cfg.runs_dir, run_dir)
    with pytest.raises(DigestError, match="not an object"):
        get_digest(cfg)


def test_list_runs_corrupt_and_fields(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    good = run_one(cfg, cfg.checks["ok"], extra=[])
    assert "rc" in list_runs(cfg, limit=5)[-1]
    assert "run_dir" in list_runs(cfg, limit=5)[-1]

    bad = create_run_dir(cfg.runs_dir, "ok")
    (bad / DIGEST_NAME).write_text("{bad", encoding="utf-8")
    update_latest(cfg.runs_dir, bad)
    rows = {r["run_id"]: r for r in list_runs(cfg, limit=20)}
    assert rows[bad.name]["status"] == "corrupt"
    assert good.run_dir.name in rows


def test_evidence_invalid_name_meta_and_missing_run(tmp_path: Path) -> None:
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    with pytest.raises(RunNotFoundError):
        get_evidence(cfg)
    run_one(cfg, cfg.checks["ok"], extra=[])
    with pytest.raises(ArtifactError, match="invalid"):
        get_evidence(cfg, artifact="  full.log")
    with pytest.raises(ArtifactError, match="invalid"):
        get_evidence(cfg, artifact="")

    # directory named like an allowed artifact → not a file
    from ckdn.runner import resolve_run_dir

    run_dir = resolve_run_dir(cfg.runs_dir, None)
    assert run_dir is not None
    meta_dir = run_dir / META_NAME
    if meta_dir.is_file():
        meta_dir.unlink()
    meta_dir.mkdir()
    with pytest.raises(ArtifactError, match="not a file"):
        get_evidence(cfg, artifact=META_NAME)

    # recreate meta as corrupt file for include_meta branch
    meta_dir.rmdir()
    (run_dir / META_NAME).write_text("{nope", encoding="utf-8")
    ev = get_evidence(cfg, include_meta=True)
    assert "meta_error" in ev

    (run_dir / META_NAME).write_text('{"ok": true}', encoding="utf-8")
    ev2 = get_evidence(cfg, include_meta=True)
    assert ev2["meta"] == {"ok": True}

    # evidence key copy when present on digest
    digest = get_digest(cfg)
    digest["notes"] = ["n1"]
    (run_dir / DIGEST_NAME).write_text(
        __import__("json").dumps(digest), encoding="utf-8"
    )
    ev3 = get_evidence(cfg)
    assert ev3["notes"] == ["n1"]


def test_run_alias_not_alias_and_status_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ckdn.app import run as app_run
    from ckdn.app.types import AtomicRunResult

    cfg = _load_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        '[check.g]\nmembers = ["ok"]\n',
    )
    with pytest.raises(NotAliasError):
        run_alias(cfg, cfg.checks["ok"])

    def _fake_run_one(cfg_arg, check, *, extra=None):  # type: ignore[no-untyped-def]
        run_dir = create_run_dir(cfg_arg.runs_dir, check.name)
        return AtomicRunResult(
            check=check.name,
            status="parse_mismatch",
            rc=0,
            run_dir=run_dir,
            digest={"check": check.name, "status": "parse_mismatch", "rc": 0},
            exit_code=1,
        )

    monkeypatch.setattr(app_run, "run_one", _fake_run_one)
    result = run_alias(cfg, cfg.checks["g"])
    assert result.exit_code == 1
    assert result.status == "fail"


def test_run_one_rejects_parser_artifact_escape(tmp_path: Path) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.bad]\ncommand = "true"\nparser = "pytest"\njunit = "/etc/passwd"\n',
    )
    result = run_one(cfg, cfg.checks["bad"], extra=[])
    assert result.status == "parse_mismatch"
    assert any("ArtifactPathError" in note for note in result.digest.get("notes", []))


def test_run_one_run_dir_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ckdn.app import run as app_run
    from ckdn.runner import RunOutcome

    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    outside = tmp_path.parent / f"{tmp_path.name}-outside-run"
    outside.mkdir()

    def _execute(tokens, cwd, run_dir, timeout):  # type: ignore[no-untyped-def]
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=0,
            log_text="ok\n",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.01,
            timed_out=False,
            exec_note=None,
        )

    def _create_run_dir(runs_dir, check_name):  # type: ignore[no-untyped-def]
        return outside

    monkeypatch.setattr(app_run, "execute", _execute)
    monkeypatch.setattr(app_run, "create_run_dir", _create_run_dir)
    monkeypatch.setattr(app_run, "update_latest", lambda *a, **k: None)
    monkeypatch.setattr(app_run, "prune", lambda *a, **k: None)
    result = run_one(cfg, cfg.checks["ok"], extra=[])
    assert result.digest["run_dir"] == str(outside)
    # Absolute path proves relative_to(cfg.root) fell back.
    assert result.digest["run_dir"].startswith(str(tmp_path.parent))
