"""観測できない項目の固定の一覧（D03 §15.6 の 5 v1.25、D07 §28.3 v2.13、D12-132 (a)）。

OANDA MT5 API の 1 分足では保証しない項目（D12 §11.8.2 の表 2 の第 3 列を行ごとに写し、T1・T3・T4 の
3 セルだけ決定の文言に直したもの。文言の正本は D12 §12.8 の「観測できない項目の一覧 版 1」）。

受入れ・分類の画面出力と、評価レポートの節 6「データの被覆」の末尾に毎回出す表示だけの値で、
検査報告のファイル・snapshot の識別子・評価の結果には入れない（D12 §12.8-6 の 2・3 の決定。原文は
D12 §0.1.32）。文言を変えるときは版を上げる。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "UNOBSERVABLE_ITEMS",
    "UNOBSERVABLE_LIST_ID",
    "UNOBSERVABLE_LIST_VERSION",
    "UnobservableItem",
    "unobservable_heading",
]

#: 一覧の識別。
UNOBSERVABLE_LIST_ID = "mt5_m1_unobservable"

#: 一覧の版。文言を 1 文字でも変えたら上げる。
UNOBSERVABLE_LIST_VERSION = 1


@dataclass(frozen=True, slots=True)
class UnobservableItem:
    """一覧の 1 行（D12 §4.1 の条件と、1 分足では観測できない項目）。"""

    condition: str
    item: str


#: 観測できない項目の一覧 版 1（D12 §12.8。行の順は表 2 のまま）。
UNOBSERVABLE_ITEMS: tuple[UnobservableItem, ...] = (
    UnobservableItem(
        "T1 時刻の変換規則",
        "時刻の規則の表 版 1 で確認済みは行 3 だけ。他の行の変換は仮定付き",
    ),
    UnobservableItem("T2 逆行・重複", "tick の逆行・重複"),
    UnobservableItem(
        "T3 ask ≥ bid",
        "各 tick の ask ≥ bid。1 分足のスプレッドが 0 以上でも、足の中の全 tick で ask ≥ bid とは"
        "読まない",
    ),
    UnobservableItem(
        "T4 tick の間隔の上限",
        "1 分より短い配信断。無記録の分が配信断か取引の少ない分かは見分けない",
    ),
    UnobservableItem("T5 週末・休場との整合", "1 分の中の tick の件数"),
    UnobservableItem("T6 開場と閉場の位置", "1 分より細かい位置"),
    UnobservableItem("T7 配信断の公表", "—"),
    UnobservableItem(
        "（層 2）足の中の値",
        "足の中の価格の順序、ask の 4 値、tick の数（`tick_volume` の数え方は未確認）",
    ),
)


def unobservable_heading() -> str:
    """一覧の見出し（識別と版を含む）。画面出力とレポートで同じ文字列を使う。"""
    return (
        f"観測できない項目（1 分足では保証しない。一覧 {UNOBSERVABLE_LIST_ID}"
        f" 版 {UNOBSERVABLE_LIST_VERSION}）"
    )
