"""台帳の行の識別子だけを読む小さな関数（起動し直したときの照合の (1)。D25 §2.4）。

審査台帳・最終検証の台帳に、ある実行（記録票の識別子と実行番号）の行があるかだけを見る。行の
判定の値・指標は読まない（審査の結果の語彙 `PROCEED`・`DO_NOT_PROCEED`・`DEFERRED` だけを返す）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any, Final

__all__ = ["REVIEW_RESULTS", "has_candidate_row", "review_result"]

REVIEW_RESULTS: Final = frozenset({"PROCEED", "DO_NOT_PROCEED", "DEFERRED"})


def _rows(data: bytes) -> Iterator[Mapping[str, Any]]:
    for line in data.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


def _matches(row: Mapping[str, Any], experiment_id: str, execution: int) -> bool:
    for holder in (row, row.get("candidate")):
        if isinstance(holder, Mapping) and str(holder.get("experiment_id")) == experiment_id:
            if str(holder.get("execution")) == str(execution):
                return True
    return False


def has_candidate_row(data: bytes | None, experiment_id: str, execution: int) -> bool:
    """台帳のバイト列に、この実行の行があるか（審査台帳は最上位、最終検証の台帳は `candidate`）。"""
    if not data:
        return False
    return any(_matches(row, experiment_id, execution) for row in _rows(data))


def review_result(data: bytes | None, experiment_id: str, execution: int) -> str | None:
    """審査台帳のこの実行の行の結果の語彙（無ければ `None`）。"""
    if not data:
        return None
    for row in _rows(data):
        if _matches(row, experiment_id, execution) and row.get("result") in REVIEW_RESULTS:
            return str(row["result"])
    return None
