"""評価の終着と、状態の更新規則（D05 §6.3〜§6.5）。

入力が足りないときに何が起きるかは、判断履歴の読み方を決める。ここで確かめるのは4つ。

1. **ウォームアップ中は注文が出ない**（全体計画 §8.2 の完了条件）。履歴が足りなければ評価を
   見送り、False や 0 に変換しない。
2. **見送りは記録に残る**。起動した使用箇所ごとに評価記録が必ず1件残る。
3. **見送った評価では状態を更新しない**（D05 §6.5）。更新すると、欠損で飛ばした評価が再武装
   の判定に影響し、同じ入力でも観測遅延の有無で判断履歴が変わる。
4. **役割出力がそろわなければ発注提案は作られない**（T01 §6.1）。
"""

from __future__ import annotations

from odyssey_fx.common.ids import EventId, IdAllocator, RunId
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.records.payloads import ConditionState
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from odyssey_fx.strategy.runtime.ports import BarClosure, PublicationBatch
from odyssey_fx.strategy.runtime.requests import Evaluated, Skipped
from tests.fixtures.strategy.fakes import (
    CollectingSink,
    FakeContextView,
    FakeMarketDataView,
    hourly_bars,
)
from tests.fixtures.strategy.phases import BACKTEST_PHASES
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES, TIMEFRAMES, strategy_a

FIRST_START = UtcTime.from_components(2026, 1, 5, 12, 0)


def _bars(count: int) -> tuple[Bar, ...]:
    """`count` 本の1時間足（末尾で高値 150.000 を上抜ける）。"""
    specs: list[tuple[str, str, str, str]] = []
    for index in range(count - 1):
        high = "150.000" if index == 3 else "149.900"
        low = "149.500" if index == 7 else "149.700"
        specs.append(("149.800", high, low, "149.850"))
    specs.append(("149.900", "150.040", "149.800", "150.040"))
    return hourly_bars(SIGNAL_SERIES, FIRST_START, specs)


def _evaluator(bars: tuple[Bar, ...], *, stop_lookback: int | None = None) -> StrategyEvaluator:
    definition = strategy_a(stop_lookback=stop_lookback)
    result = compile_strategy(definition, INITIAL_CATALOG, TIMEFRAMES)
    assert isinstance(result, CompileSucceeded), result
    return StrategyEvaluator(
        compiled=result.compiled,
        registry=INITIAL_CATALOG,
        market_data=FakeMarketDataView({SIGNAL_SERIES: bars}),
        context=FakeContextView(),
        sink=CollectingSink(),
        allocator=IdAllocator(RunId(ContentDigest.sha256("c" * 64))),
    )


def _batch(bars: tuple[Bar, ...], batch_id: int = 1) -> PublicationBatch:
    bar = bars[-1]
    return PublicationBatch(
        batch_id=EventId(batch_id),
        decision_time=bar.bar_end,
        phases=BACKTEST_PHASES,
        available_bars=(bar.key,),
        scheduled_closes=(BarClosure(bar_key=bar.key, interval=bar.interval),),
    )


def test_no_order_is_produced_while_the_history_is_too_short() -> None:
    """全体計画 §8.2: ウォームアップ中の注文はゼロ。"""
    bars = _bars(5)
    evaluator = _evaluator(bars)

    result = evaluator.step(_batch(bars))

    assert result.proposals == ()
    assert result.management_requests == ()


def test_a_skipped_evaluation_records_why_it_was_skipped() -> None:
    """D04 §6.3: 見送りは False や価格 0 の出力に変換せず、診断を添えて記録する。"""
    bars = _bars(5)
    evaluator = _evaluator(bars)

    result = evaluator.step(_batch(bars))

    skipped = {
        record.instance_id: record.outcome
        for record in result.evaluations
        if isinstance(record.outcome, Skipped)
    }
    # 上流が出力を出していないので、突破の検出も同じ判断時点で見送りになる。
    assert set(skipped) == {"breakout_level", "stop_level", "entry_trigger"}
    breakout_outcome = skipped["breakout_level"]
    assert isinstance(breakout_outcome, Skipped)
    diagnosis = breakout_outcome.diagnoses[0]
    assert diagnosis.input_name == "prices"
    assert diagnosis.reason is MissingInputReason.WARMUP_INSUFFICIENT


def test_a_skipped_evaluation_does_not_update_the_component_state() -> None:
    """D05 §6.5: 見送った評価で状態を更新すると、観測遅延の有無で判断履歴が変わる。"""
    bars = _bars(5)
    evaluator = _evaluator(bars)
    before = evaluator.state.component_states["entry_trigger"]

    evaluator.step(_batch(bars))

    assert before == ConditionState(False)
    assert evaluator.state.component_states["entry_trigger"] == ConditionState(False)


def test_a_component_with_enough_history_is_evaluated() -> None:
    """履歴がそろえば同じ宣言がそのまま評価される（見送りは一時的な状態である）。"""
    bars = _bars(21)
    evaluator = _evaluator(bars)

    result = evaluator.step(_batch(bars))

    assert all(isinstance(record.outcome, Evaluated) for record in result.evaluations)
    assert len(result.proposals) == 1


def test_a_half_warmed_strategy_opens_an_opportunity_without_proposing_an_order() -> None:
    """T01 §6.1: 損切り水準だけ履歴が足りないと、役割出力がそろわず発注提案が作られない。"""
    bars = _bars(21)
    evaluator = _evaluator(bars, stop_lookback=50)

    result = evaluator.step(_batch(bars))

    outcomes = {record.instance_id: type(record.outcome).__name__ for record in result.evaluations}
    assert outcomes["stop_level"] == "Skipped"
    assert outcomes["entry_trigger"] == "Evaluated"
    assert outcomes["entry_order"] == "Evaluated"
    assert outcomes["initial_stop"] == "Skipped"
    assert result.proposals == ()
    assert evaluator.state.opportunities[0].state.value == "OPEN"
