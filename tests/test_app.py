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
    run_all,
    run_check,
    run_one,
)
from ckdn.app import run as app_run
from ckdn.config import Config, load_config
from ckdn.digest import DIGEST_NAME, META_NAME
from ckdn.runner import (
    LOG_NAME,
    RunOutcome,
    _lock_path,
    create_run_dir,
    resolve_run_dir,
    update_latest,
)


def _write_cfg(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ckdn.toml"
    path.write_text(
        '[run]\nruns_dir = ".agent-runs"\nkeep = 20\n\n' + body,
        encoding="utf-8",
    )
    return path


def _load_cfg(tmp_path: Path, body: str) -> Config:
    return load_config(_write_cfg(tmp_path, body), cwd=tmp_path)


@pytest.fixture(autouse=True)
def _portable_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for ``execute`` so the app-layer run tests are OS-independent
    (Windows has no ``true``/``false``; real subprocess execution is covered by
    test_runner via ``sys.executable``). ``true`` -> rc 0, ``false`` -> rc 1.
    Tests that install their own ``execute`` stub override this."""

    def _fake(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        (run_dir / LOG_NAME).write_text("", encoding="utf-8")
        rc = 1 if tokens and tokens[0] == "false" else 0
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=rc,
            log_text="",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.0,
            timed_out=False,
            exec_note=None,
        )

    monkeypatch.setattr("ckdn.app.run.execute", _fake)


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


def test_reclaimed_lock_is_reported_without_changing_the_verdict(
    tmp_path: Path,
) -> None:
    """A lock left by a crashed run is evidence about the *previous* run.

    It says nothing about this one, so it lands in notes and the status stays
    exactly what the exit code earned.
    """
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    lock = _lock_path(cfg.runs_dir, "ok")
    lock.parent.mkdir(parents=True)
    lock.write_text("ckdn pid 4242", encoding="utf-8")  # killed before releasing

    result = run_one(cfg, cfg.checks["ok"], extra=[])

    assert result.status == "pass"  # the warning must not downgrade a green run
    assert result.exit_code == 0
    assert any("did not exit cleanly" in note for note in result.digest["notes"])
    assert lock.read_text(encoding="utf-8") == "", "this run released it cleanly"


def test_digest_run_dir_uses_posix_separators(tmp_path: Path) -> None:
    # Digest paths are normalized to forward slashes so a digest is byte-stable
    # across OSes (no-op on POSIX; normalizes backslashes on Windows).
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    result = run_one(cfg, cfg.checks["ok"], extra=[])
    run_dir = result.digest["run_dir"]
    assert "\\" not in run_dir
    assert run_dir.startswith(".agent-runs/")


def test_check_env_passed_with_run_dir_substituted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.ok]\ncommand = "true"\nparser = "generic"\n'
        'env = { K = "v", OUT = "{run_dir}/cov.xml" }\n',
    )
    captured: dict[str, dict[str, str] | None] = {}

    def _exec(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        (run_dir / LOG_NAME).write_text("", encoding="utf-8")
        captured["env"] = env
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=0,
            log_text="",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.0,
            timed_out=False,
            exec_note=None,
        )

    monkeypatch.setattr("ckdn.app.run.execute", _exec)
    run_one(cfg, cfg.checks["ok"], extra=[])
    env = captured["env"]
    assert env is not None
    assert env["K"] == "v"
    assert env["OUT"].endswith("/cov.xml") and "{run_dir}" not in env["OUT"]


def test_aggregate_run_dir_matches_member_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The aggregate reports each member's own (relative, posix) digest run_dir.
    cfg = _load_cfg(
        tmp_path,
        '[check.a]\ncommand = "x"\nparser = "generic"\n[check.g]\nmembers = ["a"]\n',
    )

    def _exec(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        (run_dir / LOG_NAME).write_text("", encoding="utf-8")
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=1,  # fail so the aggregate carries the member's run_dir
            log_text="",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.0,
            timed_out=False,
            exec_note=None,
        )

    monkeypatch.setattr("ckdn.app.run.execute", _exec)
    result = run_alias(cfg, cfg.checks["g"])
    agg_member = result.aggregate["members"][0]
    assert agg_member["run_dir"] == result.members[0].digest["run_dir"]


def _rc_by_suffix(run_dir: Path, fail_suffix: str) -> int:
    return 1 if run_dir.name.endswith(fail_suffix) else 0


def test_run_all_runs_every_atomic_skipping_aliases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.a]\ncommand = "true"\nparser = "generic"\n'
        '[check.b]\ncommand = "false"\nparser = "generic"\n'
        '[check.g]\nmembers = ["a", "b"]\n',
    )

    def _exec(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        (run_dir / LOG_NAME).write_text("", encoding="utf-8")
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=_rc_by_suffix(run_dir, "-b"),
            log_text="",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.0,
            timed_out=False,
            exec_note=None,
        )

    monkeypatch.setattr("ckdn.app.run.execute", _exec)
    result = run_all(cfg)
    assert result.alias == "*"
    assert result.aggregate["schema"] == "ckdn.aggregate/1"
    # every atomic ran, in config order; the alias `g` is skipped
    assert [m["check"] for m in result.aggregate["members"]] == ["a", "b"]
    assert result.aggregate["status"] == "fail" and result.exit_code == 1


def test_run_all_fail_fast_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _load_cfg(
        tmp_path,
        '[check.a]\ncommand = "false"\nparser = "generic"\n'
        '[check.b]\ncommand = "true"\nparser = "generic"\n',
    )

    def _exec(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        (run_dir / LOG_NAME).write_text("", encoding="utf-8")
        return RunOutcome(
            run_dir=run_dir,
            tokens=tokens,
            rc=_rc_by_suffix(run_dir, "-a"),
            log_text="",
            started_at="2026-01-01T00:00:00+00:00",
            duration_s=0.0,
            timed_out=False,
            exec_note=None,
        )

    monkeypatch.setattr("ckdn.app.run.execute", _exec)
    result = run_all(cfg, fail_fast=True)
    assert [m["check"] for m in result.aggregate["members"]] == ["a"]


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
            digest={
                "check": check.name,
                "status": "parse_mismatch",
                "rc": 0,
                "run_dir": run_dir.name,
            },
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

    def _execute(tokens, cwd, run_dir, timeout, env=None):  # type: ignore[no-untyped-def]
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
    assert result.digest["run_dir"] == outside.as_posix()
    # Absolute path proves relative_to(cfg.root) fell back.
    assert result.digest["run_dir"].startswith(tmp_path.parent.as_posix())


def _outcome_for(
    run_dir: Path, tokens: list[str], rc: int, *, interrupted: bool
) -> RunOutcome:
    (run_dir / LOG_NAME).write_text("", encoding="utf-8")
    return RunOutcome(
        run_dir=run_dir,
        tokens=tokens,
        rc=rc,
        log_text="",
        started_at="2026-01-01T00:00:00+00:00",
        duration_s=0.0,
        timed_out=False,
        exec_note=None,
        interrupted=interrupted,
    )


def test_interruption_outranks_an_earlier_red_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ruff` fails, then Ctrl-C stops `pytest`.

    The aggregate used to pass through the first non-zero rc, so the series
    exited 1 — the code that means "a check is red" — while the CHANGELOG
    promised 130, and nothing in the document said the rest never ran.
    """
    cfg = _load_cfg(
        tmp_path,
        '[check.red]\ncommand = "false"\nparser = "generic"\n'
        '[check.stopped]\ncommand = "true"\nparser = "generic"\n'
        '[check.both]\nmembers = ["red", "stopped"]\nfail_fast = false\n',
    )

    def _fake(
        tokens: list[str],
        cwd: Path,
        run_dir: Path,
        timeout: float | None,
        env: dict[str, str] | None = None,
    ) -> RunOutcome:
        stopped = tokens[0] == "true"
        return _outcome_for(run_dir, tokens, 130 if stopped else 1, interrupted=stopped)

    monkeypatch.setattr("ckdn.app.run.execute", _fake)
    result = run_alias(cfg, cfg.checks["both"])

    assert result.exit_code == 130, "an early red member masked the interruption"
    assert result.aggregate["interrupted"] is True
    assert [m["check"] for m in result.aggregate["members"]] == ["red", "stopped"]


def test_ctrl_c_while_parsing_still_leaves_a_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command finished; the interrupt lands before its output is read.

    `except Exception` does not catch KeyboardInterrupt, so this used to
    abandon the run directory with only `full.log` in it — the empty run
    directory from the incident, reached by a different door.
    """
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')

    def _interrupt(*_a: object, **_k: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("ckdn.parsers.generic.GenericParser.parse", _interrupt)
    result = run_one(cfg, cfg.checks["ok"], extra=[])

    assert result.status == "error"
    assert result.digest["interrupted"] is True
    assert (result.run_dir / DIGEST_NAME).exists()
    assert (result.run_dir / META_NAME).exists()
    assert (result.run_dir / LOG_NAME).exists()
    # A Ctrl-C is 130 wherever it lands. Asserting only on `interrupted` let
    # the command's own exit code survive here, so a single `ckdn run` exited
    # 1 with `rc: 0` in its digest while the status model promised 130.
    assert result.digest["rc"] == 130
    assert result.exit_code == 130
    assert any("had exited 0" in note for note in result.digest["notes"]), (
        "the command's own exit code must still be recoverable from the run"
    )


def test_ctrl_c_between_parsing_and_the_write_still_leaves_a_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interrupt lands after parsing, while the documents are built.

    Protecting only the write left this window open: the run directory has a
    log and no digest — the same symptom one stage earlier.
    """
    cfg = _load_cfg(tmp_path, '[check.ok]\ncommand = "true"\nparser = "generic"\n')
    calls: list[int] = []

    # `_annotate_baseline` sits between building the digest and writing it,
    # and is a no-op when no baseline is configured — so standing in for it
    # interrupts exactly that window without changing the result.
    def _interrupt_once(*_args: object) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(app_run, "_annotate_baseline", _interrupt_once)
    result = run_one(cfg, cfg.checks["ok"], extra=[])

    assert len(calls) == 2, "the protected step must be retried, not abandoned"
    assert (result.run_dir / DIGEST_NAME).exists()
    assert (result.run_dir / META_NAME).exists()
