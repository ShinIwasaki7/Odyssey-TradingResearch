"""集計の鍵の語彙が実装の列挙と一致することの機械検査（D07 §6.1・§1.1）。

評価は `backtest.execution` と `strategy.runtime` を import できない（契約 F4・F8）ため、
足内競合の解決方法と取引機会の終端理由の語彙は写しになっている。**写しが原本とずれると
集計の鍵が黙って欠ける**ので、テストからは両方を import して突き合わせる（テストは
import 規則の対象外である）。

読む列の名前も同じ理由で写しになっていないことを確かめる。列名の正本は
`backtest.trace.recorder` の宣言であり、評価が読む列がそこに無ければ、実行が書かない列を
読もうとしていることになる。
"""

from __future__ import annotations

import typing

from odyssey_fx.backtest.domain.orders import CloseCause
from odyssey_fx.backtest.execution.protection_hits import ResolutionMethod
from odyssey_fx.backtest.trace.recorder import table_columns
from odyssey_fx.common.reason import MissingInputReason, ReasonCode
from odyssey_fx.evaluation.application.evaluate_run import COLUMN_SPECS, INPUT_TABLES
from odyssey_fx.evaluation.domain.metrics import (
    ADMISSION_REJECTION_KEYS,
    CATEGORY_KEYS,
    CLOSE_CAUSE_KEYS,
    EVALUATION_OUTCOME_KEYS,
    INTRABAR_METHOD_KEYS,
    MISSING_INPUT_KEYS,
    OPPORTUNITY_TERMINAL_KEYS,
    CategoryKind,
)
from odyssey_fx.strategy.runtime.opportunities import TERMINAL_REASONS
from odyssey_fx.strategy.runtime.requests import (
    Evaluated,
    EvaluationOutcome,
    Failed,
    Skipped,
    Superseded,
    Waiting,
)


def test_the_intrabar_method_keys_match_the_implementation() -> None:
    """足内競合の解決方法3語が実装の列挙と一致する（D06 §3）。"""
    assert INTRABAR_METHOD_KEYS == tuple(method.value for method in ResolutionMethod)


def test_the_opportunity_terminal_keys_match_the_implementation() -> None:
    """取引機会の終端理由が実装の集合と一致する（D05 §7.2）。

    D07 §6.1 は「D02 §8.1 の終端理由7語」と書いているが、D05 §7.2 の遷移9（run 末尾に
    残った機会）が `RUN_END` を使うため、実装の語彙は8語である。並びは `ReasonCode` の
    宣言順に固定する（D07 §6.1）。
    """
    assert set(OPPORTUNITY_TERMINAL_KEYS) == {code.value for code in TERMINAL_REASONS}
    declared = [code.value for code in ReasonCode if code.value in set(OPPORTUNITY_TERMINAL_KEYS)]
    assert list(OPPORTUNITY_TERMINAL_KEYS) == declared


def test_the_admission_rejection_keys_are_the_six_of_the_design() -> None:
    """受付前拒否の理由は6語（D06 §5.2）。並びは `ReasonCode` の宣言順。"""
    assert ADMISSION_REJECTION_KEYS == (
        ReasonCode.RISK.value,
        ReasonCode.NO_CANDIDATE.value,
        ReasonCode.RUN_END.value,
        ReasonCode.DATA_ERROR.value,
        ReasonCode.CARRY_NOT_ALLOWED.value,
        ReasonCode.PROTECTION_INVALID.value,
    )


def test_the_evaluation_outcome_keys_match_the_runtime_union() -> None:
    """評価の結果区分5語が判断履歴へ書かれる区分タグと一致する（D05 §6.4、D07 §6.1 v1.4）。

    段階3 で待機中（`Waiting`）と追い越しで閉じた（`Superseded`）の2区分が足された。並びは
    `EvaluationOutcome` の union の宣言順である。
    """
    assert EVALUATION_OUTCOME_KEYS == tuple(
        variant.__dataclass_fields__["kind"].default
        for variant in typing.get_args(EvaluationOutcome)
    )
    assert EVALUATION_OUTCOME_KEYS == (
        Evaluated().kind,
        Skipped.__dataclass_fields__["kind"].default,
        Failed.__dataclass_fields__["kind"].default,
        Waiting.__dataclass_fields__["kind"].default,
        Superseded.__dataclass_fields__["kind"].default,
    )


def test_the_missing_input_keys_match_the_kernel_enum() -> None:
    """評価見送りの診断コード4語が共通カーネルの列挙と一致する（D02 §8.3）。"""
    assert MISSING_INPUT_KEYS == tuple(reason.value for reason in MissingInputReason)


def test_the_close_cause_keys_match_the_backtest_enum() -> None:
    """決済の執行契機4語がバックテストの列挙と一致する（D06 §3）。"""
    assert CLOSE_CAUSE_KEYS == tuple(cause.value for cause in CloseCause)


def test_every_category_has_a_finite_vocabulary() -> None:
    """7種の集計すべてに鍵の語彙がある（D07 §6.1 の「0件の鍵も行として出す」の前提）。"""
    assert set(CATEGORY_KEYS) == set(CategoryKind)
    for kind, keys in CATEGORY_KEYS.items():
        assert keys, kind
        assert len(set(keys)) == len(keys), kind


def test_the_columns_read_by_the_evaluation_exist_in_the_trace() -> None:
    """読む列が判断履歴の宣言に実在する（D07 §4.2、D06 §9.2）。

    実行が書かない列を読もうとしていれば、整合検査 C1 が毎回不合格になる。列名の食い違い
    はここで止める。
    """
    for table in INPUT_TABLES:
        declared = set(table_columns(table))
        for spec in COLUMN_SPECS[table]:
            assert spec.column in declared, (table.value, spec.column)


def test_every_table_is_read_with_the_run_identifier_first() -> None:
    """9表すべてで `run_id` を先頭の必須列として要求する（D07 §4.2）。"""
    assert len(INPUT_TABLES) == 9
    for table in INPUT_TABLES:
        specs = COLUMN_SPECS[table]
        assert specs[0].column == "run_id"
        assert specs[0].required
        assert all(spec.required for spec in specs)
