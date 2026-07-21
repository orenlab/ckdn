# SPDX-FileCopyrightText: Copyright (c) 2026 Den Rozhnovskiy <rozhnovskiydenis@gmail.com>
# SPDX-License-Identifier: MIT
"""The digest is byte-deterministic; nondeterministic provenance lives in meta.

README promises ``digest.json`` is byte-identical for identical input. These
tests guard that: key order is normalized, repeated builds match to the byte,
and no timestamp/duration/hash leaks into the digest (those belong to
``meta.json``).
"""

from __future__ import annotations

from pathlib import Path

from digest_factory import make_finding, make_outcome

from ckdn.digest import build_alias_aggregate, build_digest, build_meta, dump_json
from ckdn.parsers.base import ParseResult
from ckdn.reconcile import reconcile

_NONDETERMINISTIC = ("started_at", "duration_s", "log_sha256", "log_bytes")


def _fail_digest() -> dict[str, object]:
    result = ParseResult(parser_ok=True, findings=[make_finding(1)])
    status, reason, include_tail = reconcile(1, result)
    return build_digest(
        check="pytest",
        status=status,
        reason=reason,
        outcome=make_outcome(1),
        result=result,
        run_dir_rel=".agent-runs/x",
        top=20,
        include_tail=include_tail,
        tail_lines=40,
        artifacts=["full.log", "junit.xml"],
    )


def test_dump_json_is_key_order_independent() -> None:
    a = {"schema": "ckdn.digest/2", "check": "ruff", "status": "pass", "rc": 0}
    b = {"rc": 0, "status": "pass", "check": "ruff", "schema": "ckdn.digest/2"}
    assert dump_json(a) == dump_json(b)


def test_dump_json_is_repeatable() -> None:
    digest = _fail_digest()
    assert dump_json(digest) == dump_json(digest)


def test_build_digest_is_byte_identical_across_calls() -> None:
    # two independent builds from identical inputs must serialize identically
    assert dump_json(_fail_digest()) == dump_json(_fail_digest())


def test_digest_carries_no_nondeterministic_fields() -> None:
    digest = _fail_digest()
    for key in _NONDETERMINISTIC:
        assert key not in digest


def test_meta_carries_the_nondeterministic_fields() -> None:
    meta = build_meta(check="pytest", parser="pytest", outcome=make_outcome(1))
    assert set(_NONDETERMINISTIC) <= set(meta)


def test_aggregate_is_byte_identical_across_calls() -> None:
    def build() -> dict[str, object]:
        return build_alias_aggregate(
            alias="lint",
            results=[
                ("ruff", "pass", 0, Path(".agent-runs/x-ruff")),
                ("pylint", "fail", 1, Path(".agent-runs/x-pylint")),
            ],
            status="fail",
            rc=1,
        )

    assert dump_json(build()) == dump_json(build())
