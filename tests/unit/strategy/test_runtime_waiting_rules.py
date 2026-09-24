"""待機・追い越し・出力の履歴の規則を純粋関数として確かめる（D05 §6.3・§6.8・§6.10・§6.12）。

紙上トレース T02 §19 の引き渡し #3 の3規則は、それぞれ名前の付いた関数になっている。
意味論テスト（`tests/semantics/strategy/test_waiting_t02_routes.py`）が戦略 B の経路で通す
ものを、ここでは組み合わせを尽くして押さえる。
"""

from __future__ import annotations

from datetime import timedelta
from itertools import product

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EvaluationId, OutputId, RequestId
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.common.time import Interval, PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.strategy.compiler.compiled import ResolvedMarketSource
from odyssey_fx.strategy.declarations.entry_policy import BarsDeadline, DurationDeadline
from odyssey_fx.strategy.declarations.missing import (
    Error,
    OnSuperseded,
    SkipEvaluation,
    UsePrevious,
    WaitDeadlineAction,
    WaitForInput,
)
from odyssey_fx.strategy.declarations.read_spec import BarsWindow
from odyssey_fx.strategy.declarations.refs import MarketDataField, OutputRef
from odyssey_fx.strategy.records.payloads import ConditionState
from odyssey_fx.strategy.records.records import Observation, OutputRecord
from odyssey_fx.strategy.runtime.output_history import RetainedOutput, retain, window_ending_at
from odyssey_fx.strategy.runtime.requests import EvaluationRequest, MissingInputDiagnosis
from odyssey_fx.strategy.runtime.supersession import (
    newest_published,
    pinned_target_bar,
    supersedes,
)
from odyssey_fx.strategy.runtime.waiting import (
    LifecycleVerdict,
    PolicyStrength,
    WaitEvent,
    WaitEventKind,
    WaitingRequest,
    WaitUntilBars,
    WaitUntilTime,
    deadline_reached,
    deadline_series,
    effective_strength,
    judge_lifecycle,
    resolve_deadline,
    strongest_policy,
    tick_deadline,
)
from tests.fixtures.synthetic import market

DAILY = market.series(timeframe_id="1d_ny17")
HOURLY = market.series()
T0 = UtcTime.parse("2026-01-06T22:00:00Z")
_WAIT = WaitForInput(
    deadline=BarsDeadline(bars=1),
    on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
    on_superseded=OnSuperseded.EXPIRE_REQUEST,
)
_PREVIOUS = UsePrevious(
    max_lookback=BarsWindow(2), allowed_reasons=(MissingInputReason.LATEST_BAR_UNAVAILABLE,)
)
_UNAVAILABLE = [MissingInputReason.LATEST_BAR_UNAVAILABLE]


# --- 規則(a): 欠損方針の強さ順（Q27）-----------------------------------------------


def test_rule_a_the_order_is_error_skip_wait_use_previous() -> None:
    strengths = {
        "error": effective_strength(Error(), _UNAVAILABLE),
        "skip": effective_strength(SkipEvaluation(), _UNAVAILABLE),
        "wait": effective_strength(_WAIT, _UNAVAILABLE),
        "previous": effective_strength(_PREVIOUS, _UNAVAILABLE),
    }
    ranked = sorted(strengths, key=lambda name: strengths[name].value, reverse=True)
    assert ranked == ["error", "skip", "wait", "previous"]


@pytest.mark.parametrize(
    ("policies", "expected"),
    [
        ((PolicyStrength.WAIT_FOR_INPUT, PolicyStrength.SKIP_EVALUATION), "SKIP_EVALUATION"),
        ((PolicyStrength.SKIP_EVALUATION, PolicyStrength.ERROR), "ERROR"),
        ((PolicyStrength.USE_PREVIOUS, PolicyStrength.WAIT_FOR_INPUT), "WAIT_FOR_INPUT"),
        ((PolicyStrength.USE_PREVIOUS,), "USE_PREVIOUS"),
    ],
)
def test_rule_a_the_strongest_missing_input_decides_the_whole_evaluation(
    policies: tuple[PolicyStrength, ...], expected: str
) -> None:
    assert strongest_policy(policies).name == expected


def test_rule_a_needs_at_least_one_missing_input() -> None:
    with pytest.raises(KernelValueError):
        strongest_policy(())


@pytest.mark.parametrize(
    "reason", [MissingInputReason.WARMUP_INSUFFICIENT, MissingInputReason.MAX_AGE_EXCEEDED]
)
def test_a_wait_on_a_reason_that_waiting_cannot_cure_counts_as_a_skip(
    reason: MissingInputReason,
) -> None:
    """D05 §6.8 の表: 待っても解消しない理由では待機に入らず、見送りと同じに扱う。"""
    assert effective_strength(_WAIT, [reason]) is PolicyStrength.SKIP_EVALUATION


# --- 規則(b): 本数の期限を数える系列（Q28）-------------------------------------------


def test_rule_b_the_first_missing_market_input_gives_the_series() -> None:
    assert deadline_series([DAILY, HOURLY], [HOURLY]) == DAILY


def test_rule_b_an_output_only_wait_counts_on_the_trigger_series() -> None:
    assert deadline_series([], [DAILY, DAILY]) == DAILY


def test_rule_b_an_output_only_wait_needs_exactly_one_trigger_series() -> None:
    """コンパイラの検査 h（Q30）が保証する前提。破れていたら黙って選ばない。"""
    with pytest.raises(KernelValueError):
        deadline_series([], [DAILY, HOURLY])
    with pytest.raises(KernelValueError):
        deadline_series([], [])


# --- 規則(c): 期限 → 追い越し → 失効（Q29）-----------------------------------------


@pytest.mark.parametrize(
    ("deadline", "superseded", "invalidated"), list(product((True, False), repeat=3))
)
def test_rule_c_the_first_condition_in_order_settles_the_request(
    deadline: bool, superseded: bool, invalidated: bool
) -> None:
    verdict = judge_lifecycle(
        deadline_reached=deadline, superseded=superseded, invalidated=invalidated
    )
    if deadline:
        assert verdict is LifecycleVerdict.DEADLINE
    elif superseded:
        assert verdict is LifecycleVerdict.SUPERSEDED
    elif invalidated:
        assert verdict is LifecycleVerdict.INVALIDATED
    else:
        assert verdict is LifecycleVerdict.NONE


# --- 期限の解決と数え方（D05 §6.8）--------------------------------------------------


def test_a_bar_deadline_counts_scheduled_closes_of_its_series_only() -> None:
    deadline = resolve_deadline(BarsDeadline(bars=2), started=T0, series=DAILY)
    assert deadline == WaitUntilBars(series=DAILY, remaining=2)
    unchanged = tick_deadline(deadline, frozenset({HOURLY}))
    assert unchanged == deadline
    once = tick_deadline(deadline, frozenset({DAILY}))
    assert not deadline_reached(once, T0)
    assert deadline_reached(tick_deadline(once, frozenset({DAILY})), T0)


def test_a_duration_deadline_becomes_an_absolute_time() -> None:
    deadline = resolve_deadline(DurationDeadline(timedelta(minutes=30)), started=T0, series=None)
    assert deadline == WaitUntilTime(at=T0 + timedelta(minutes=30))
    assert not deadline_reached(deadline, T0 + timedelta(minutes=29))
    assert deadline_reached(deadline, T0 + timedelta(minutes=30))


def test_a_bar_deadline_needs_a_series() -> None:
    with pytest.raises(KernelValueError):
        resolve_deadline(BarsDeadline(bars=1), started=T0, series=None)


# --- 追い越し（D05 §6.10）-----------------------------------------------------------


def _waiting(on_superseded: OnSuperseded, pinned: dict[str, BarKey]) -> WaitingRequest:
    request = EvaluationRequest(
        request_id=RequestId(1),
        instance_id="entry_trigger",
        trigger_names=("h1",),
        decision_time=T0,
    )
    source = ResolvedMarketSource(DAILY, MarketDataField.HIGH)
    return WaitingRequest(
        request=request,
        missing=(
            MissingInputDiagnosis("level", source, MissingInputReason.LATEST_BAR_UNAVAILABLE),
        ),
        started_at=ProcessingPoint(time=T0, phase=PhaseRank(7, "P3_TRIGGER"), sequence=0),
        deadline_at=WaitUntilBars(series=DAILY, remaining=1),
        on_deadline=WaitDeadlineAction.SKIP_EVALUATION,
        on_superseded=on_superseded,
        pinned_bars=pinned,
    )


HOUR_A = BarKey(series=HOURLY, bar_start=UtcTime.parse("2026-01-06T21:00:00Z"))
HOUR_B = BarKey(series=HOURLY, bar_start=UtcTime.parse("2026-01-06T22:00:00Z"))
HOUR_C = BarKey(series=HOURLY, bar_start=UtcTime.parse("2026-01-06T23:00:00Z"))
DAY = BarKey(series=DAILY, bar_start=UtcTime.parse("2026-01-05T22:00:00Z"))


def test_a_newer_bar_of_the_target_series_supersedes_the_request() -> None:
    waiting = _waiting(OnSuperseded.EXPIRE_REQUEST, {"price": HOUR_A, "level": DAY})
    assert pinned_target_bar(waiting, HOURLY) == HOUR_A
    assert supersedes(waiting, HOURLY, (HOUR_B,))
    # 1本飛ばしでも、いちばん新しい公開足が押しのけた側になる。
    assert newest_published(HOUR_A, (HOUR_B, HOUR_C)) == HOUR_C


def test_a_bar_of_another_series_does_not_supersede() -> None:
    waiting = _waiting(OnSuperseded.EXPIRE_REQUEST, {"price": HOUR_A, "level": DAY})
    newer_day = BarKey(series=DAILY, bar_start=UtcTime.parse("2026-01-06T22:00:00Z"))
    assert not supersedes(waiting, HOURLY, (newer_day,))
    assert not supersedes(waiting, HOURLY, (HOUR_A,))


def test_keep_waiting_is_never_closed_by_a_newer_bar() -> None:
    waiting = _waiting(OnSuperseded.KEEP_WAITING, {"price": HOUR_A})
    assert not supersedes(waiting, HOURLY, (HOUR_B,))


def test_a_request_without_a_pinned_bar_on_its_target_series_is_not_superseded() -> None:
    """出力参照だけを読む使用箇所は対象系列の足を固定しないので、この規則では閉じない。"""
    waiting = _waiting(OnSuperseded.EXPIRE_REQUEST, {})
    assert not supersedes(waiting, HOURLY, (HOUR_B,))
    assert not supersedes(waiting, None, (HOUR_B,))


def test_an_arrival_event_names_the_inputs_that_arrived() -> None:
    at = ProcessingPoint(time=T0, phase=PhaseRank(4, "OPPORTUNITY_LIFECYCLE"), sequence=0)
    WaitEvent(RequestId(1), WaitEventKind.INPUT_ARRIVED, at, arrived=("level",))
    with pytest.raises(KernelValueError):
        WaitEvent(RequestId(1), WaitEventKind.INPUT_ARRIVED, at)
    with pytest.raises(KernelValueError):
        WaitEvent(RequestId(1), WaitEventKind.RESUMED, at, arrived=("level",))


# --- 出力の履歴（D05 §6.12）---------------------------------------------------------

LEVEL = OutputRef("breakout_level", "level")


def _row(hour: int, value: bool, output: int) -> RetainedOutput:
    start = UtcTime.parse(f"2026-01-06T{hour:02d}:00:00Z")
    interval = Interval(start=start, end=start + timedelta(hours=1))
    subject = BarKey(series=HOURLY, bar_start=start)
    record: OutputRecord[object] = OutputRecord(
        output_id=OutputId(output),
        evaluation_id=EvaluationId(output),
        producer=LEVEL,
        decision_time=interval.end,
        available_at=interval.end,
        sequence=0,
        payload=Observation(
            value=ConditionState(value),
            subject=subject,
            observation_interval=interval,
            freshness_time=interval.end,
        ),
    )
    return RetainedOutput(record=record, subject=subject, observation_interval=interval)


def test_retain_keeps_bar_order_and_truncates_the_oldest() -> None:
    limits = {LEVEL: 2}
    history: dict[OutputRef, tuple[RetainedOutput, ...]] = {}
    for hour, output in ((3, 1), (1, 2), (2, 3)):
        history = retain(history, limits, _row(hour, True, output))
    # 到着順ではなく観測した足の順。上限を超えた古い側を落とす。
    assert [row.subject.bar_start.value.hour for row in history[LEVEL] if row.subject] == [2, 3]


def test_retain_replaces_the_row_of_the_same_bar_in_place() -> None:
    """Q20: 同じ足の2件目は積まずに置き換え、並びの位置も変えない。"""
    limits = {LEVEL: 3}
    history: dict[OutputRef, tuple[RetainedOutput, ...]] = {}
    for hour, output in ((1, 1), (2, 2), (3, 3)):
        history = retain(history, limits, _row(hour, True, output))
    history = retain(history, limits, _row(2, False, 9))
    rows = history[LEVEL]
    assert [row.record.output_id for row in rows] == [OutputId(1), OutputId(9), OutputId(3)]


def test_retain_ignores_outputs_without_a_plan_or_a_bar() -> None:
    row = _row(1, True, 1)
    assert retain({}, {}, row) == {}
    unobserved = RetainedOutput(record=row.record, subject=None, observation_interval=None)
    assert retain({}, {LEVEL: 2}, unobserved) == {}


def test_the_window_ends_at_the_target_interval_and_excludes_the_latest() -> None:
    """Q21: 対象区間の終了時刻以下の最も新しい観測を末尾とし、当該足を除いて末尾 n 件。"""
    rows = tuple(_row(hour, True, hour) for hour in (1, 2, 3, 4))
    target_end = UtcTime.parse("2026-01-06T04:00:00Z")  # 03:00 の足の終わり
    window = window_ending_at(rows, target_end, count=2, exclude_latest=1)
    assert window is not None
    assert [row.record.output_id for row in window] == [OutputId(1), OutputId(2)]
    # 足りなければ部分的な窓を渡さない。
    assert window_ending_at(rows, target_end, count=3, exclude_latest=1) is None
