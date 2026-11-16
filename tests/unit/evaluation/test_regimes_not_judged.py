"""判定の関数は局面を入力に取らない（受入テスト RP5-16 の型の試験。D24 の仮置き 15、決定 134）。

局面の結果の型（日足ごとの局面・取引ごとの局面・局面の表・局面の成果物・成果物のダイジェスト）は、
判定・選定の関数（`evaluation.domain.search`）の引数の型にも、判定の結果の型の項目にも現れない。
研究ポリシーの局面の値（`RegimeSpec`）は評価基準の群の 1 つとして判定の関数に渡る評価基準の中に
あるが、判定の関数はそれを読まない（判定の結果は局面の値に依らない。下の 2 つ目の試験）。

局面の成果物を壊しても判定の値が変わらないことは、結合テスト
`tests/integration/app/test_search_v5_regimes.py` の `test_rp5_16_*` が確かめる。
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import fields, is_dataclass
from typing import Any, Final

from odyssey_fx.evaluation.domain import regimes, search
from odyssey_fx.evaluation.domain.experiment import RegimeDigest

#: 局面の結果の型（判定の関数が受け取ってはならないもの）の名前。
RESULT_TYPES: Final[frozenset[str]] = frozenset(
    kind.__name__
    for kind in (
        regimes.DailyRegime,
        regimes.RegimeAt,
        regimes.RegimeLookup,
        regimes.RegimeTrade,
        regimes.RegimeTradeRow,
        regimes.RegimeDay,
        regimes.RegimeTableRow,
        regimes.RegimeDayRow,
        regimes.FavoredRegimeCheck,
        regimes.RegimeReport,
        regimes.RegimeLabel,
        regimes.UnlabeledReason,
        RegimeDigest,
    )
)

_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _names_in(annotation: object) -> set[str]:
    """注釈（`from __future__ import annotations` の文字列）に現れる名前。"""
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", "")
    return set(_NAME.findall(str(text)))


def _judgement_functions() -> list[tuple[str, Any]]:
    return [
        (name, member)
        for name, member in vars(search).items()
        if inspect.isfunction(member) and member.__module__ == search.__name__
    ]


def test_rp5_16_no_judgement_function_takes_a_regime_result() -> None:
    functions = _judgement_functions()
    assert {"build_variant_outcome", "build_search_outcome", "select_trial"} <= {
        name for name, _ in functions
    }
    for name, function in functions:
        signature = inspect.signature(function)
        annotations = [item.annotation for item in signature.parameters.values()]
        for annotation in (*annotations, signature.return_annotation):
            assert not (_names_in(annotation) & RESULT_TYPES), (name, annotation)


def test_rp5_16_no_judgement_record_holds_a_regime_result() -> None:
    """判定の結果の型（結末記録の探索の項目とその中身）の項目に局面の結果の型が無い。"""
    seen: set[str] = set()
    pending: list[str] = ["SearchOutcome"]
    while pending:
        name = pending.pop()
        kind = vars(search).get(name)
        if name in seen or not (isinstance(kind, type) and is_dataclass(kind)):
            continue
        seen.add(name)
        for item in fields(kind):
            found = _names_in(item.type)
            assert not (found & RESULT_TYPES), (name, item.name)
            pending.extend(found)
    assert {"SearchOutcome", "VariantOutcome"} <= seen


def test_rp5_16_the_judgement_module_never_reads_the_regime_values() -> None:
    """判定・選定のモジュールは評価基準の `regimes` を読まない（`self.regimes` の型の検査だけ）。"""
    tree = ast.parse(inspect.getsource(search))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "regimes"
        and not (isinstance(node.value, ast.Name) and node.value.id == "self")
    ]
    assert reads == []
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == regimes.__name__
        for alias in node.names
    }
    assert imported <= {"RegimeSpec"}
