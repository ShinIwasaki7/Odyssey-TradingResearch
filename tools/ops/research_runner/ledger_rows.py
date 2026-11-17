"""台帳の行の識別子だけを読む小さな関数（起動し直したときの照合の (1)。D25 §2.4）。

審査台帳・最終検証の台帳に、ある実行（記録票の識別子と実行番号）の行があるかだけを見る。行の
判定の値・指標は読まない（審査の結果の語彙 `PROCEED`・`DO_NOT_PROCEED`・`DEFERRED` だけを返す）。
例外は自動の削除の確かめ（D25 §4.5 の 2）で、試行台帳の結末の行の判定の語彙だけを読む。

行の形: 審査台帳は `{digest, entry, prev}`（識別子と結果は `entry` の中。`review_store` の
`review_line_bytes`）、最終検証の台帳は最上位に `candidate`、消費の記録は最上位に `candidate`。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any, Final

__all__ = [
    "REVIEW_RESULTS",
    "finished_verdict",
    "has_candidate_row",
    "has_consumption_row",
    "review_result",
    "review_row_count",
]

REVIEW_RESULTS: Final = frozenset({"PROCEED", "DO_NOT_PROCEED", "DEFERRED"})


def _rows(data: bytes) -> Iterator[Mapping[str, Any]]:
    for line in data.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


def _body(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """審査台帳の行なら `entry` の中、それ以外は行そのもの。"""
    entry = row.get("entry")
    return entry if isinstance(entry, Mapping) else row


def _matches(row: Mapping[str, Any], experiment_id: str, execution: int) -> bool:
    for holder in (row, _body(row), row.get("candidate")):
        if isinstance(holder, Mapping) and str(holder.get("experiment_id")) == experiment_id:
            if str(holder.get("execution")) == str(execution):
                return True
    return False


def has_candidate_row(data: bytes | None, experiment_id: str, execution: int) -> bool:
    """台帳のバイト列に、この実行の行があるか（審査台帳は最上位、最終検証の台帳は `candidate`）。"""
    if not data:
        return False
    return any(_matches(row, experiment_id, execution) for row in _rows(data))


def _review_results(data: bytes | None, experiment_id: str, execution: int) -> list[str]:
    """審査台帳のこの実行の行の結果の語彙を、台帳の順（古い順）に並べたもの。"""
    if not data:
        return []
    return [
        str(_body(row)["result"])
        for row in _rows(data)
        if _matches(row, experiment_id, execution) and _body(row).get("result") in REVIEW_RESULTS
    ]


def review_row_count(data: bytes | None, experiment_id: str, execution: int) -> int:
    """審査台帳のこの実行の結果の行の数（審査を起動する前に数えて、起動の印に残す）。"""
    return len(_review_results(data, experiment_id, execution))


def review_result(
    data: bytes | None, experiment_id: str, execution: int, *, after: int = 0
) -> str | None:
    """審査台帳のこの実行の行のうち、先頭から `after` 行より後に書かれた**最新の**行の結果の語彙
    （無ければ `None`）。

    「保留」の後の審査のやり直し（D16 §9.3）では、同じ実行に古い「保留」の行が残る。照合は
    起動の前に数えた行の数（`after`）より後の行だけを見て、その最新を採る（古い「保留」を読んで
    止まらない。やり直しの審査が何も書かずに消えたなら `None`。Codex 7 巡目の指摘 1）。
    """
    results = _review_results(data, experiment_id, execution)[after:]
    return results[-1] if results else None


def finished_verdict(data: bytes | None, experiment_id: str, execution: int) -> str | None:
    """試行台帳のこの実行の結末の行（`event` = `FINISHED`）のうち最新の行の判定の語彙（無ければ
    `None`）。自動の削除の「審査の入口を通りえない」の確かめ（D25 §4.5 の 2）だけに使う。判定の
    語彙のほかは読まない。"""
    found: str | None = None
    for row in _rows(data or b""):
        body = _body(row)
        if body.get("event") == "FINISHED" and _matches(row, experiment_id, execution):
            verdict = body.get("verdict")
            found = str(verdict) if isinstance(verdict, str) else None
    return found


def has_consumption_row(data: bytes | None, experiment_id: str, execution: int) -> bool:
    """消費の記録（`access_log.jsonl`）のバイト列に、この実行の最終検証の消費の行があるか
    （D09 §9.10.4 の書式の版 1。`kind` = `CONSUMED`・`reason` = `FINAL_EVALUATION`・`candidate`
    の識別子だけを見る。区分・時刻・目的は読まない）。"""
    if not data:
        return False
    return any(
        row.get("kind") == "CONSUMED"
        and row.get("reason") == "FINAL_EVALUATION"
        and isinstance(row.get("candidate"), Mapping)
        and _matches(row["candidate"], experiment_id, execution)
        for row in _rows(data)
    )
