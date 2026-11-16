"""台帳と定型 PR の CI の段（`tools.ops.runner_ci`）の試験の人工の入力。

台帳・カード・記録はすべて人工の値（成績・判定の値・価格を読まない・書かない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

import yaml

from odyssey_fx.app.final_validation_records import (
    FINAL_LEDGER_PATH,
    append_ledger_row,
    candidate_payload,
)
from odyssey_fx.evaluation.adapters.fs_store import ledger_line_bytes
from odyssey_fx.evaluation.adapters.review_store import review_line_bytes
from odyssey_fx.evaluation.domain.final_validation import CandidateKey, FinalLedgerResult
from tests.fixtures.evaluation.adoption_review import review_entry, review_lines
from tests.fixtures.evaluation.trial_ledger import chain, experiment_id, finished, started
from tools.ops.runner_ci.model import (
    Change,
    ChangeStatus,
    CheckInput,
    DictSnapshot,
    PullRequest,
    sha256_hex,
)
from tools.ops.runner_ci.records import experiment_id_of
from tools.ops.runner_ci.regen import header_line
from tools.ops.runner_ci.routine import FIXED_SENTENCE

TRIAL: Final = "research/trial_ledger.jsonl"
REVIEW: Final = "research/adoption_reviews.jsonl"
FINAL: Final = "research/final_validations.jsonl"
CARD_ID: Final = "a-20261120-ema-pullback"
HUMAN_CARD_ID: Final = "h-20261120-ema-pullback"
EXPERIMENT: Final = experiment_id_of(CARD_ID, "EURUSD")
GENERATOR: Final = "b" * 64


# --- 台帳 ----------------------------------------------------------------------


def trial_ledger(count: int, *, name: str = "a") -> bytes:
    """人工の試行台帳（`count` 行。開始と結末を交互に）。"""
    entries = []
    for index in range(count):
        opening = started(f"{name}{index // 2}", nonce=f"{index // 2:032x}")
        entries.append(opening if index % 2 == 0 else finished(opening))
    return b"".join(ledger_line_bytes(line) for line in chain(entries))


def review_ledger(count: int) -> bytes:
    entries = [
        review_entry(experiment_id(f"r{index}"), nonce=f"{index:032x}") for index in range(count)
    ]
    return b"".join(review_line_bytes(line) for line in review_lines(entries))


def final_ledger(tmp: Path, count: int) -> bytes:
    """人工の最終検証の台帳（書き手の関数で `count` 行を書いたもの）。"""
    (tmp / FINAL_LEDGER_PATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp / FINAL_LEDGER_PATH).write_bytes(b"")
    key = CandidateKey(
        experiment_id="a" * 64, execution=1, experiment_name="exp", experiment_version=1
    )
    for _ in range(count):
        append_ledger_row(
            tmp,
            {
                "result": FinalLedgerResult.WITHDRAWN.value,
                "candidate": candidate_payload(key),
                "permit_id": "",
                "t_fix": "",
                "consumption_start": "",
                "consumption_end": "",
                "partition_count": 0,
                "partitions_digest": "",
                "consumption_commit": "",
                "combined_verdict": "",
                "resume_count": 0,
                "resume_failures": [],
                "prior_final_validations": 0,
                "reason": "WITHDRAWN",
                "final_evaluation_sha256": "",
                "declaration_sha256": "",
                "recorded_at": "2026-11-20T00:00:00Z",
                "actor": "人間",
            },
        )
    return (tmp / FINAL_LEDGER_PATH).read_bytes()


def ledgers(tmp: Path, trial: int = 2, review: int = 1, final: int = 1) -> dict[str, bytes]:
    return {
        TRIAL: trial_ledger(trial),
        REVIEW: review_ledger(review),
        FINAL: final_ledger(tmp, final),
    }


# --- カードと記録 ------------------------------------------------------------------


def dump(payload: Mapping[str, Any]) -> bytes:
    return yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False).encode("utf-8")


def card(mouth: str = "ai", **override: Any) -> dict[str, Any]:
    title = override.pop("title", "ema-pullback")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "id": f"{mouth[0]}-20261120-{title}",
        "mouth": mouth,
        "created_at": "2026-11-20T03:00:00Z",
        "title": title,
        "symbol": "EURUSD",
        "rule": "短い EMA が長い EMA を上に抜けたら入り、下に抜けたら出る",
        "strategy": {
            "entry_policy": {"kind": "IMMEDIATE"},
            "opportunity_validity": {"bindings": []},
            "components": [{"instance": "fast", "component": "ema", "params": {"period": "10"}}],
            "roles": {"trigger": "fast"},
            "opportunity_concurrency": {"max_active": 1},
        },
        "grid": {"axes": [{"instance": "fast", "parameter": "period", "values": ["10", "20"]}]},
        "prediction": "上昇の局面で取引が多く、横ばいで少ない",
        "inspiration": "既存の記録の読み",
        "evidence_estimate": {"frequency_band": "MEDIUM", "reaches_floor": "LIKELY"},
    }
    if mouth == "ai":
        payload["read_scope"] = ["ledger/trial_ledger.jsonl"]
    payload.update(override)
    return payload


def card_bytes(mouth: str = "ai", **override: Any) -> bytes:
    return dump(card(mouth, **override))


def run_record(card_id: str = CARD_ID, version: int = 1) -> bytes:
    return dump(
        {
            "schema_version": 1,
            "card": card_id,
            "version": version,
            "experiment_id": EXPERIMENT,
            "execution": 1,
            "started_at": "2026-11-21T00:00:00Z",
            "finished_at": "2026-11-21T09:00:00Z",
            "exit_code": 0,
            "max_rss_bytes": 1,
            "record_id": "e" * 64,
            "artifacts": [{"path": "runs/experiments/x/v1/report.md", "sha256": "c" * 64}],
            "memo": None,
        }
    )


def outcome(card_id: str = CARD_ID) -> bytes:
    return dump(
        {
            "schema_version": 1,
            "card": card_id,
            "reason": "FINISHED",
            "at": "2026-11-22T00:00:00Z",
            "last_version": 2,
        }
    )


def report(card_id: str = CARD_ID, prediction: str = "上昇の局面で取引が多い") -> bytes:
    return dump(
        {
            "schema_version": 1,
            "card": card_id,
            "final_version": 2,
            "text": {
                "prediction": prediction,
                "change_reasons": ["横ばいの局面の取引を減らすため"],
                "change_contents": ["格子の軸の値を変えた"],
            },
            "comparison": {"path": "runs/experiments/x/analysis/comparison.md", "sha256": "d" * 64},
            "gate": {
                "items": [{"item": number, "result": "PASS"} for number in range(1, 6)],
                "verifier": {"result": "PASS", "reasons": []},
            },
        }
    )


def generated_files(card_raw: bytes) -> dict[str, bytes]:
    head = header_line(CARD_ID, sha256_hex(card_raw), GENERATOR)
    return {
        f"configs/strategies/{EXPERIMENT}_v1.yaml": f"{head}\nschema_version: 1\n".encode(),
        f"configs/experiments/{EXPERIMENT}.yaml": f"{head}\nschema_version: 2\n".encode(),
    }


def prereg_note(card_raw: bytes, generated: Mapping[str, bytes]) -> bytes:
    return dump(
        {
            "schema_version": 1,
            "card": CARD_ID,
            "version": 1,
            "experiment_id": EXPERIMENT,
            "card_sha256": sha256_hex(card_raw),
            "change_sha256": None,
            "generator_digest": GENERATOR,
            "generated": [
                {"path": path, "sha256": sha256_hex(data)}
                for path, data in sorted(generated.items())
            ],
            "checks": {
                "load": "PASS",
                "compile": "PASS",
                "prepare": "PASS",
                "trial_count": 2,
                "rejected_count": 0,
                "seconds": 1,
                "max_rss_bytes": 1,
            },
        }
    )


# --- 入力 -----------------------------------------------------------------------


def changes_between(base: Mapping[str, bytes], head: Mapping[str, bytes]) -> tuple[Change, ...]:
    found: list[Change] = []
    for path in sorted(set(base) | set(head)):
        if path not in base:
            found.append(Change(path, ChangeStatus.ADDED))
        elif path not in head:
            found.append(Change(path, ChangeStatus.DELETED))
        elif base[path] != head[path]:
            found.append(Change(path, ChangeStatus.MODIFIED))
    return tuple(found)


def body(
    kind: str,
    card_line: str,
    head: Mapping[str, bytes],
    changes: tuple[Change, ...],
    *,
    version: int | None = 1,
    experiment: str | None = None,
    extra: tuple[str, ...] = (),
) -> str:
    lines = [f"種類: {kind}", f"カード: {card_line}"]
    if version is not None:
        lines.append(f"版: {version}")
    if experiment is not None:
        lines.append(f"実験: {experiment}")
    lines.append("SHA-256:")
    lines += [
        f"- {change.path}: {sha256_hex(head[change.path])}"
        for change in changes
        if change.status is not ChangeStatus.DELETED
    ]
    lines += ["時刻: 2026-11-21T09:00:00Z", "件数: 試行 2、行 1", *extra, "", FIXED_SENTENCE]
    return "\n".join(lines) + "\n"


def make_input(
    base: Mapping[str, bytes] | None,
    head: Mapping[str, bytes],
    *,
    branch: str | None = None,
    title: str = "",
    body_text: str | None = None,
    kind: str = "",
    card_line: str = CARD_ID,
    version: int | None = 1,
    experiment: str | None = None,
    auto_merge: bool = False,
) -> CheckInput:
    """基点と先頭の辞書から入力を作る。`body_text` を渡さなければ型どおりの本文を作る。"""
    changes = changes_between(base or {}, head) if base is not None else ()
    pr = None
    if branch is not None:
        text = (
            body_text
            if body_text is not None
            else body(kind, card_line, head, changes, version=version, experiment=experiment)
        )
        pr = PullRequest(branch=branch, title=title, body=text, auto_merge=auto_merge)
    return CheckInput(
        base=None if base is None else DictSnapshot(dict(base)),
        head=DictSnapshot(dict(head)),
        changes=changes,
        pull_request=pr,
    )
