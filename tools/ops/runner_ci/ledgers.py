"""検査 1: 3 つの台帳の読込の検査と、基点の台帳が先頭とバイト単位で同じこと（D25 §6.3 の 1）。

- 試行台帳 `research/trial_ledger.jsonl`: 読込の検査 L0〜L10（D09 §10.12.2）。成果物を読む L11 は
  CI で読めない `runs/` を読むので当てない（実行器がコミットの前に当てる。D25 の仮置き 20）。
- 審査台帳 `research/adoption_reviews.jsonl`: 同じ形の読込の検査（D16 §10.2）。
- 最終検証の台帳 `research/final_validations.jsonl`: 行の形・正規化・鎖（D09 §9.10.5）。

読むのは `src/odyssey_fx` の読込の関数だけで、検査の規則をここに書き直さない（D01）。
末尾の書きかけ（改行で終わらない断片）は、読込では失敗にしないが、版管理に入れると次の追記が
切り詰めて「追記だけ」が崩れるので、CI では落とす（本 PR の仮置き）。

出力には食い違いの種類と行番号だけを書く（行の中身・判定の値を書かない）。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from odyssey_fx.app.final_validation_records import FinalLedgerError, parse_final_ledger
from odyssey_fx.evaluation.adapters.fs_store import parse_trial_ledger_bytes
from odyssey_fx.evaluation.adapters.review_store import (
    ReviewLedgerReadFailure,
    parse_review_ledger_bytes,
)
from odyssey_fx.evaluation.application.ports import TrialLedgerReadFailure
from tools.ops.runner_ci.model import CheckInput, CheckResult, result

NAME: Final = "台帳（3 つの台帳の読込の検査と、追記だけであること）"


def _line(number: int | None) -> str:
    return "" if number is None else f"（行 {number}）"


def _trial(data: bytes) -> str | None:
    read = parse_trial_ledger_bytes(data)
    if isinstance(read, TrialLedgerReadFailure):
        return f"{read.kind.value}{_line(read.line_number)}"
    return "TORN_TAIL（末尾に改行で終わらない断片）" if read.torn_tail else None


def _review(data: bytes) -> str | None:
    read = parse_review_ledger_bytes(data)
    if isinstance(read, ReviewLedgerReadFailure):
        return f"{read.kind.value}{_line(read.line_number)}"
    return "TORN_TAIL（末尾に改行で終わらない断片）" if read.torn_tail else None


def _final(data: bytes) -> str | None:
    try:
        parse_final_ledger(data)
    except FinalLedgerError as exc:
        # 理由の文は値を含みうるので書かない（形・正規化・鎖・書きかけのどれか）。
        return f"FINAL_LEDGER_INVALID{_line(exc.line)}"
    return None


#: （人が読む名前, リポジトリの根からのパス, 読込の検査）。
LEDGERS: Final[tuple[tuple[str, str, Callable[[bytes], str | None]], ...]] = (
    ("試行台帳", "research/trial_ledger.jsonl", _trial),
    ("審査台帳", "research/adoption_reviews.jsonl", _review),
    ("最終検証の台帳", "research/final_validations.jsonl", _final),
)


def _first_difference_line(base: bytes, head: bytes) -> int:
    """基点と先頭が最初に違うバイトのある行の番号（1 始まり）。"""
    limit = min(len(base), len(head))
    index = next((i for i in range(limit) if base[i] != head[i]), limit)
    return base.count(b"\n", 0, index) + 1


def check_ledgers(given: CheckInput) -> CheckResult:
    problems: list[str] = []
    notes: list[str] = []
    for label, path, parse in LEDGERS:
        data = given.head.read(path)
        if data is None:
            problems.append(f"{label} {path}: FILE_MISSING（版管理に無い。D09 §10.12.2 の L0）")
            continue
        defect = parse(data)
        if defect is not None:
            problems.append(f"{label} {path}: 読込の検査に当たった {defect}")
        if given.base is None:
            notes.append(f"{label}: 基点が無い push なので、追記だけの確かめは当てない")
            continue
        before = given.base.read(path)
        if before is None:
            if data:
                problems.append(
                    f"{label} {path}: 基点に台帳が無いのに、空でない台帳を足している"
                    "（台帳は空のファイルとして置き、追記で伸ばす）"
                )
            continue
        if not data.startswith(before):
            problems.append(
                f"{label} {path}: 基点の台帳がこの版の台帳の先頭と一致しない"
                f"（行 {_first_difference_line(before, data)} から違う。追記だけでない）"
            )
    notes.append(
        "試行台帳の成果物からの逆照合 L11 は CI では当てない（実行器がコミットの前に当てる）"
    )
    return result(1, NAME, problems, notes)
