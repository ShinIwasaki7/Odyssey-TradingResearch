"""後続確認の規則と記録の型を純粋関数・型の不変条件として確かめる（D05 §7.3・§7.7）。

意味論テスト（`tests/semantics/strategy/test_confirmation_t02_routes.py`）が検証戦略 B の経路で
通すものを、ここでは組み合わせで押さえる。
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import OpportunityId, OutputId, RequestId
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.time import Interval, PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.compiler.compiled import ConfirmationPlan
from odyssey_fx.strategy.declarations.entry_policy import (
    BarsDeadline,
    DeadlineAction,
    DurationDeadline,
)
from odyssey_fx.strategy.declarations.opportunity import ValidityMode
from odyssey_fx.strategy.declarations.refs import OutputRef
from odyssey_fx.strategy.records.payloads import Opportunity, TradeDirection
from odyssey_fx.strategy.runtime.confirmation import (
    ConfirmationAttempt,
    ConfirmationAttemptOutcome,
    confirmation_deadline,
    wants_confirmation,
)
from odyssey_fx.strategy.runtime.opportunities import (
    OpportunityLifecycle,
    OpportunityState,
    ValidityRecheck,
    ValidityRecheckOutcome,
)
from odyssey_fx.strategy.runtime.requests import EvaluationRequest, RuntimeStepResult
from odyssey_fx.strategy.runtime.waiting import WaitUntilBars, WaitUntilTime
from tests.fixtures.synthetic import market

M15 = market.series(timeframe_id="15m")
HOURLY = market.series()
T0 = UtcTime.parse("2015-01-07T09:00:00Z")
PHASE = PhaseRank(rank=8, name="P4_CONFIRMATION")
O1 = OpportunityId(1)


def _bar(minutes: int, series: object = M15) -> BarKey:
    return BarKey(series=series, bar_start=T0 + timedelta(minutes=minutes))  # type: ignore[arg-type]


def _plan(*, include_start_bar: bool, deadline: object = None) -> ConfirmationPlan:
    return ConfirmationPlan(
        filter_instance="entry_filter",
        series=M15,
        include_start_bar=include_start_bar,
        deadline=deadline or BarsDeadline(bars=4),  # type: ignore[arg-type]
        on_deadline=DeadlineAction.EXPIRE,
    )


def _point(sequence: int = 0) -> ProcessingPoint:
    return ProcessingPoint(time=T0, phase=PHASE, sequence=sequence)


def _attempt(minutes: int, outcome: ConfirmationAttemptOutcome) -> ConfirmationAttempt:
    return ConfirmationAttempt(
        opportunity_id=O1,
        bar_key=_bar(minutes),
        request_id=RequestId(minutes + 100),
        outcome=outcome,
    )


def _lifecycle() -> OpportunityLifecycle:
    opportunity = Opportunity(
        opportunity_id=O1,
        symbol=market.USDJPY,
        direction=TradeDirection.LONG,
        signal_interval=Interval(start=T0 - timedelta(hours=1), end=T0),
        reference_values={},
    )
    return OpportunityLifecycle(
        opportunity=opportunity,
        state=OpportunityState.OPEN,
        created_at=_point(),
        created_decision_time=T0,
    )


# --- 開始足と include_start_bar（D05 §7.7 の表）-------------------------------------


@pytest.mark.parametrize(
    ("include_start_bar", "target", "expected"),
    [
        (True, -15, True),  # 開始足で確認する設定
        (False, -15, False),  # 開始足は確認に使わない
        (False, 0, True),  # 次の確認足からは確認する
        (True, 0, True),
    ],
)
def test_the_start_bar_follows_include_start_bar(
    include_start_bar: bool, target: int, expected: bool
) -> None:
    """対象の足が開始足と同じときだけ `include_start_bar` が効く（D05 §7.7）。"""
    plan = _plan(include_start_bar=include_start_bar)
    assert wants_confirmation(plan, _bar(-15), _bar(target)) is expected


def test_an_opportunity_without_a_start_bar_is_confirmed_on_every_bar() -> None:
    """開始足が読めなかった機会は、開始足にあたる足が無いので次の確認足から確認する。"""
    plan = _plan(include_start_bar=False)
    assert wants_confirmation(plan, None, _bar(0)) is True


def test_a_bar_of_another_series_is_not_a_confirmation_bar() -> None:
    """確認足の系列でない足は確認の起動ではない（コンパイラの検査 b の裏側）。"""
    assert wants_confirmation(_plan(include_start_bar=True), None, _bar(0, HOURLY)) is False


def test_a_confirmation_needs_a_target_bar() -> None:
    """足の確定で起動しない確認評価は無い（D05 §7.7）。"""
    with pytest.raises(KernelValueError):
        wants_confirmation(_plan(include_start_bar=True), None, None)


# --- 確認期限の解決（D05 §7.7、Q19 決定）-------------------------------------------


def test_a_bar_deadline_counts_on_the_confirmation_series() -> None:
    """本数の期限は確認足の系列で数える（Trigger の系列ではない）。"""
    assert confirmation_deadline(_plan(include_start_bar=True), T0) == WaitUntilBars(
        series=M15, remaining=4
    )


def test_a_duration_deadline_is_added_to_the_creation_time() -> None:
    """経過時間の期限は生成した判断時刻に足した時刻になる。"""
    plan = _plan(include_start_bar=True, deadline=DurationDeadline(duration=timedelta(hours=1)))
    assert confirmation_deadline(plan, T0) == WaitUntilTime(at=T0 + timedelta(hours=1))


# --- 確認試行（D05 §7.7、Q26 決定）-------------------------------------------------


def test_an_attempt_is_replaced_on_the_same_confirmation_bar() -> None:
    """同じ確認足の試行は1件で、決着したら同じ1件の結末を置き換える（WAITING → SKIPPED）。"""
    lifecycle = _lifecycle().with_attempt(_attempt(0, ConfirmationAttemptOutcome.WAITING))
    lifecycle = lifecycle.with_attempt(_attempt(-15, ConfirmationAttemptOutcome.NOT_CONFIRMED))
    lifecycle = lifecycle.with_attempt(_attempt(0, ConfirmationAttemptOutcome.SKIPPED))

    assert [(item.bar_key, item.outcome) for item in lifecycle.attempts] == [
        (_bar(-15), ConfirmationAttemptOutcome.NOT_CONFIRMED),
        (_bar(0), ConfirmationAttemptOutcome.SKIPPED),
    ]


def test_attempts_are_kept_when_the_state_moves() -> None:
    """状態が変わっても確認試行・開始足・期限は引き継がれる。"""
    lifecycle = replace_start(_lifecycle()).with_attempt(
        _attempt(-15, ConfirmationAttemptOutcome.CONFIRMED)
    )
    moved = lifecycle.moved_to(OpportunityState.CONFIRMED, at=_point(1))
    assert moved.attempts == lifecycle.attempts
    assert moved.confirmation_start_bar == _bar(-15)
    assert moved.deadline_at == WaitUntilBars(series=M15, remaining=4)


def replace_start(lifecycle: OpportunityLifecycle) -> OpportunityLifecycle:
    """開始足と期限を持つ機会（テスト用）。"""
    return OpportunityLifecycle(
        opportunity=lifecycle.opportunity,
        state=lifecycle.state,
        created_at=lifecycle.created_at,
        created_decision_time=lifecycle.created_decision_time,
        confirmation_start_bar=_bar(-15),
        deadline_at=WaitUntilBars(series=M15, remaining=4),
    )


def test_a_step_result_carries_each_attempt_key_once() -> None:
    """戻り値の確認試行は `(機会, 確認足)` ごとに1件（表18 の主キー。D06 §9.2）。"""
    with pytest.raises(KernelValueError):
        RuntimeStepResult(
            confirmation_attempts=(
                _attempt(0, ConfirmationAttemptOutcome.WAITING),
                _attempt(0, ConfirmationAttemptOutcome.SKIPPED),
            )
        )


def test_attempts_of_another_opportunity_are_rejected() -> None:
    """機会の記録には、その機会の試行だけが載る。"""
    stranger = ConfirmationAttempt(
        opportunity_id=OpportunityId(2),
        bar_key=_bar(0),
        request_id=RequestId(1),
        outcome=ConfirmationAttemptOutcome.CONFIRMED,
    )
    with pytest.raises(KernelValueError):
        _lifecycle().with_attempt(stranger)


# --- 有効性の再検査の記録（D05 §7.3）-----------------------------------------------


def _recheck(
    outcome: ValidityRecheckOutcome,
    output_id: OutputId | None = None,
    reason: Reason | None = None,
) -> ValidityRecheck:
    return ValidityRecheck(
        opportunity_id=O1,
        source=OutputRef("daily_above_ema", "condition"),
        mode=ValidityMode.REQUIRE_UNTIL_ORDER_REQUEST,
        at=_point(),
        outcome=outcome,
        output_id=output_id,
        reason=reason,
    )


def test_the_four_recheck_outcomes_have_their_own_evidence() -> None:
    """読めたら出力の識別子、読めずに失敗したら `DATA_ERROR`、読めずに見送ったら何も持たない。"""
    _recheck(ValidityRecheckOutcome.SATISFIED, output_id=OutputId(1))
    _recheck(ValidityRecheckOutcome.NOT_SATISFIED, output_id=OutputId(1))
    _recheck(ValidityRecheckOutcome.MISSING_SKIPPED)
    _recheck(ValidityRecheckOutcome.MISSING_FAILED, reason=Reason(code=ReasonCode.DATA_ERROR))


@pytest.mark.parametrize(
    ("outcome", "output_id", "reason"),
    [
        (ValidityRecheckOutcome.SATISFIED, None, None),
        (ValidityRecheckOutcome.MISSING_SKIPPED, OutputId(1), None),
        (ValidityRecheckOutcome.MISSING_SKIPPED, None, Reason(code=ReasonCode.DATA_ERROR)),
        (ValidityRecheckOutcome.MISSING_FAILED, None, None),
        (ValidityRecheckOutcome.MISSING_FAILED, None, Reason(code=ReasonCode.EXPIRED)),
        (
            ValidityRecheckOutcome.NOT_SATISFIED,
            OutputId(1),
            Reason(code=ReasonCode.DATA_ERROR),
        ),
    ],
)
def test_a_recheck_record_must_match_its_outcome(
    outcome: ValidityRecheckOutcome, output_id: OutputId | None, reason: Reason | None
) -> None:
    """結末と根拠（出力の識別子・理由）が食い違う記録は作れない。"""
    with pytest.raises(KernelValueError):
        _recheck(outcome, output_id=output_id, reason=reason)


def test_the_attempt_index_is_a_non_negative_int() -> None:
    """評価要求の通し番号（D05 §6.11）は 0 起点の整数。"""
    with pytest.raises(KernelValueError):
        EvaluationRequest(
            request_id=RequestId(1),
            instance_id="entry_filter",
            trigger_names=("m15",),
            decision_time=T0,
            attempt_index=-1,
        )
