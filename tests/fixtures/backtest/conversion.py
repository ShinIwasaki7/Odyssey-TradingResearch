"""換算の受入テストの組み立て（D06 v1.20 案 §19.16。人工データだけ）。

T01 の人工の足（`tests/fixtures/backtest/paths.py`）を別の通貨ペアの系列に付け替え、換算の脚の
1 分足（人工の率）を `ConversionQuotes` の偽物で与えて、エンジンの利用口（`RunBacktest`）を
直接結線する。価格・率は人工の値で、実データの価格ではない。

- `relabel(bars, symbol)`: 足の系列だけを別の通貨ペアにする（値は同じ）。
- `minute_rates(symbol, interval, rate, holes)`: 区間のセッションの各分に、終値が `rate`（関数なら
  分ごとの値）の 1 分足を作る。`holes` の分は作らない（無記録）。
- `FakeQuotes`: 通貨ペアごとの 1 分足から `ConversionQuotes` を満たす。
- `FakeMinutes`: 取引の通貨ペア（執行系列の通貨ペア）の 1 分足と無記録の区間から、MT5 経路の
  `MinuteObservation`（と換算の `RecordedMinutes`）を満たす（D06 v1.22 案 §19.5 の決定 (31)）。
- `run_converted(...)`: 執行の通貨ペア・口座・換算の脚を指定して run を 1 回通す。
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Final

from odyssey_fx.app.mt5_run import publication_rows
from odyssey_fx.backtest.application.run_backtest import ConversionSupply, RunBacktest
from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.domain.policies import (
    ConversionPolicy,
    CostModel,
    ExecutionPolicy,
    ResolutionHierarchy,
    RunConfig,
)
from odyssey_fx.backtest.engine.loop import EngineContext, MinuteObservation, TraceOutputSink
from odyssey_fx.backtest.portfolio.conversion import ConversionQuotes
from odyssey_fx.backtest.trace.manifest import config_digest_of
from odyssey_fx.common.ids import IdAllocator, SnapshotId
from odyssey_fx.common.money import CurrencyCode, Price, PriceOffset, decimal_from_str
from odyssey_fx.common.refs import SnapshotRef, run_id
from odyssey_fx.common.symbol import Symbol, SymbolSpec, SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.mt5_observation import Resumption
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from tests.fixtures.backtest.harness import (
    ACCOUNT,
    CALENDAR,
    CODE_DIGEST,
    COST_MODEL,
    ENV_DIGEST,
    LOCK_DIGEST,
    RISK_POLICY,
    TIMEFRAME_REFS,
    CollectingResultWriter,
    CollectingTraceSink,
    FakeExecutionSeries,
    FakeFeed,
    RunOutput,
    _digest,
    _policy_ref,
    calendar_ref_of,
    compiled_strategy,
)
from tests.fixtures.strategy.fakes import FakeMarketDataView
from tests.fixtures.strategy.strategy_a import strategy_a
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic.market import TF_15M

__all__ = [
    "AUDJPY",
    "CONVERSION_POLICY",
    "EURUSD",
    "MINUTE_REF",
    "USDCHF",
    "USDJPY",
    "FakeMinutes",
    "FakeQuotes",
    "minute_rates",
    "relabel",
    "run_converted",
]

EURUSD = Symbol("EURUSD")
USDCHF = Symbol("USDCHF")
USDJPY = Symbol("USDJPY")
AUDJPY = Symbol("AUDJPY")

#: 換算の脚の時間足（1 分足）の版参照。
MINUTE_REF = TimeframeRef("1m", 1)
_MINUTE = timedelta(minutes=1)

#: 円以外の決済通貨の run の換算のポリシー（上限 30 分。人間の決定 (32)、D06 v1.23 案 §19.5）。
#: `harness.CONVERSION_POLICY`（15 分）は恒等換算の run の固定値テストが使うので変えない。
CONVERSION_POLICY: Final = ConversionPolicy(
    pivot_currency=CurrencyCode("USD"), max_observation_skew=timedelta(minutes=30)
)

#: 換算の snapshot（人工の識別子）。
CONVERSION_SNAPSHOT = SnapshotRef(snapshot_id=SnapshotId(_digest("conversion")))
#: 執行の snapshot（`harness` と同じ人工の識別子）。
EXECUTION_SNAPSHOT = SnapshotRef(snapshot_id=SnapshotId(_digest("n")))


def minute_series(symbol: Symbol) -> SeriesId:
    """通貨ペアの 1 分足（bid）の系列。"""
    return SeriesId(symbol=symbol, timeframe=MINUTE_REF, basis=PriceBasis.BID)


def relabel(bars: Sequence[Bar], symbol: Symbol) -> tuple[Bar, ...]:
    """足の系列だけを別の通貨ペアにする（4 本値・区間・利用可能時刻は同じ）。"""
    return tuple(
        replace(
            bar,
            series=SeriesId(symbol=symbol, timeframe=bar.series.timeframe, basis=bar.series.basis),
        )
        for bar in bars
    )


def minute_rates(
    symbol: Symbol,
    interval: Interval,
    rate: str | Callable[[UtcTime], str],
    *,
    holes: Sequence[Interval] = (),
    calendar: TradingCalendar = CALENDAR,
) -> tuple[Bar, ...]:
    """区間のセッションの各分に、終値が `rate` の 1 分足を作る（`holes` の分は作らない）。"""
    made: list[Bar] = []
    for session in calendar.sessions(interval):
        moment = session.start
        while moment < session.end:
            if not any(hole.contains(moment) for hole in holes):
                text = rate(moment) if callable(rate) else rate
                value = Price(decimal_from_str(text))
                span = Interval(start=moment, end=moment + _MINUTE)
                made.append(
                    Bar(
                        series=minute_series(symbol),
                        interval=span,
                        open=value,
                        high=value,
                        low=value,
                        close=value,
                        volume=decimal_from_str("0"),
                        available_at=span.end,
                        provenance=Provenance(
                            kind=ProvenanceKind.HISTDATA, source_ref="tests/fixtures/conversion"
                        ),
                    )
                )
            moment = moment + _MINUTE
    return tuple(made)


@dataclass(frozen=True, slots=True)
class FakeQuotes:
    """`ConversionQuotes` の偽物（通貨ペアごとの 1 分足と snapshot）。

    `symbols` は経路を探す集合。`bars` に無い通貨ペアの足は無い（観測なし）。
    """

    symbols: frozenset[Symbol]
    bars: Mapping[Symbol, tuple[Bar, ...]]
    snapshots: Mapping[Symbol, SnapshotRef] = field(default_factory=dict)

    def last_minute(self, symbol: Symbol, moment: UtcTime) -> Bar | None:
        found = sorted(self.bars.get(symbol, ()), key=lambda bar: bar.bar_start.value)
        starts = [bar.bar_end.value for bar in found]
        index = bisect.bisect_right(starts, moment.value) - 1
        return found[index] if index >= 0 else None

    def series_of(self, symbol: Symbol) -> SeriesId:
        return minute_series(symbol)

    def snapshot_of(self, symbol: Symbol) -> SnapshotRef:
        return self.snapshots.get(symbol, CONVERSION_SNAPSHOT)


@dataclass(frozen=True, slots=True)
class FakeMinutes:
    """取引の通貨ペアの 1 分足と無記録の区間（MT5 経路の `MinuteObservation` の偽物）。

    `bars` は記録のある 1 分足、`unrecorded` はセッションの中の無記録の区間（`minute_rates` の
    `holes` と同じ区間を渡す）。再開の分は、無記録の区間の終わりで 1 分足のある分。
    """

    minute_series: SeriesId
    bars: tuple[Bar, ...]
    unrecorded: tuple[Interval, ...] = ()

    def _ordered(self) -> list[Bar]:
        return sorted(self.bars, key=lambda bar: bar.bar_start.value)

    def minute_bars_in(self, interval: Interval) -> tuple[Bar, ...]:
        return tuple(bar for bar in self._ordered() if interval.contains(bar.bar_start))

    def minute_bar_at(self, minute: UtcTime) -> Bar | None:
        return next((bar for bar in self.bars if bar.bar_start == minute), None)

    def last_bar_before(self, moment: UtcTime) -> Bar | None:
        before = [bar for bar in self._ordered() if bar.bar_start < moment]
        return before[-1] if before else None

    def resumption_at(self, minute: UtcTime) -> Resumption | None:
        if self.minute_bar_at(minute) is None:
            return None
        for hole in self.unrecorded:
            if hole.end == minute:
                return Resumption(minute=minute, unrecorded_from=hole.start)
        return None

    def resumptions_in(self, interval: Interval) -> tuple[Resumption, ...]:
        found = (self.resumption_at(hole.end) for hole in self.unrecorded)
        return tuple(
            item
            for item in sorted(
                (item for item in found if item is not None), key=lambda item: item.minute.value
            )
            if interval.contains(item.minute)
        )

    def is_unrecorded(self, minute: UtcTime) -> bool:
        return any(hole.contains(minute) for hole in self.unrecorded)


def _spec(symbol: Symbol) -> SymbolSpec:
    """人工の銘柄仕様（どの通貨ペアでも T01 の足の刻みに合わせる）。

    数量の刻みと最小数量は 1（率 150 の換算でも数量の式を手計算で確かめられる大きさにする）。
    """
    return SymbolSpec(
        symbol=symbol,
        version=1,
        price_tick=decimal_from_str("0.001"),
        pip_size=decimal_from_str("0.01"),
        quantity_step=decimal_from_str("1"),
        min_quantity=decimal_from_str("1"),
    )


def run_converted(
    *,
    symbol: Symbol,
    signal_bars: Sequence[Bar],
    execution_bars: Sequence[Bar],
    run_interval: Interval,
    quotes: ConversionQuotes | None,
    account: AccountSpec = ACCOUNT,
    cost_model: CostModel = COST_MODEL,
    calendar: TradingCalendar = CALENDAR,
    conversion_policy: ConversionPolicy = CONVERSION_POLICY,
    shortfall: Mapping[SeriesId, tuple[tuple[Interval, str], ...]] | None = None,
    present: frozenset[SeriesId] | None = None,
    conversion_snapshot: SnapshotRef | None = CONVERSION_SNAPSHOT,
    minutes: MinuteObservation | None = None,
) -> RunOutput:
    """執行の通貨ペア `symbol` の run を 1 回通す（換算の脚は `quotes`）。

    `signal_bars` と `execution_bars` は `symbol` の系列の足を渡す（`relabel` で付け替える）。
    `quotes` が `None` なら換算の脚の供給を渡さない（決済通貨が円の run）。`minutes` は取引の
    通貨ペアの 1 分足（MT5 経路の run。渡さなければ 1 分足を持たない run）。
    """
    signal = SeriesId(symbol=symbol, timeframe=market.TF_1H.ref, basis=PriceBasis.BID)
    execution = SeriesId(symbol=symbol, timeframe=TF_15M.ref, basis=PriceBasis.BID)
    compiled = compiled_strategy(strategy_a(signal_series=signal))
    spec = _spec(symbol)
    spec_ref = SymbolSpecRef(symbol=symbol, version=1, digest=_digest(f"s-{symbol}"))
    execution_policy = ExecutionPolicy(
        adverse_fill_limits={symbol: PriceOffset(decimal_from_str("0.05"))},
        entry_valid_for=timedelta(minutes=20),
        close_valid_for=timedelta(minutes=20),
        resolution_hierarchy=ResolutionHierarchy(levels=(execution,)),
    )
    config = RunConfig(
        run_interval=run_interval,
        snapshot_ref=EXECUTION_SNAPSHOT,
        compiled_ref=compiled.compiled_ref,
        account=account,
        risk_policy_ref=_policy_ref("risk", "r"),
        execution_policy_ref=_policy_ref("execution", "e"),
        cost_model_ref=_policy_ref("cost", "c"),
        conversion_policy_ref=_policy_ref("conversion", "v"),
        delay_scenario_ref=_policy_ref("delay", "d"),
        execution_series=execution,
        conversion_snapshot_ref=None if quotes is None else conversion_snapshot,
    )
    calendar_ref = calendar_ref_of(calendar)
    digest = config_digest_of(
        config,
        symbol_spec_ref=spec_ref,
        calendar_ref=calendar_ref,
        timeframe_def_refs=TIMEFRAME_REFS,
    )
    allocator = IdAllocator(run_id(digest, CODE_DIGEST, LOCK_DIGEST, ENV_DIGEST))
    sink = TraceOutputSink()
    context = EngineContext(account)
    evaluator = StrategyEvaluator(
        compiled=compiled,
        registry=INITIAL_CATALOG,
        market_data=FakeMarketDataView({signal: tuple(signal_bars)}),
        context=context,
        sink=sink,
        allocator=allocator,
    )
    feed = FakeFeed(signal_bars, execution_bars)
    schedule = SeriesSchedule(series=execution, timeframe_def=TF_15M, calendar=calendar)
    published = publication_rows(
        feed,
        {bar.key: bar for bar in (*signal_bars, *execution_bars)},
        {
            signal: SeriesSchedule(series=signal, timeframe_def=market.TF_1H, calendar=calendar),
            execution: schedule,
        },
        run_interval,
    )
    gaps = dict(shortfall or {})
    supply = (
        None
        if quotes is None
        else ConversionSupply(
            quotes=quotes,
            present=frozenset(
                minute_series(item)
                for item in (quotes.bars if isinstance(quotes, FakeQuotes) else {})
            )
            if present is None
            else present,
            shortfall=lambda series, interval: tuple(
                item for item in gaps.get(series, ()) if item[0].overlaps(interval)
            ),
        )
    )
    trace_sink = CollectingTraceSink()
    writer = CollectingResultWriter()
    use_case = RunBacktest(
        runtime=evaluator,
        context=context,
        output_sink=sink,
        allocator=allocator,
        feed=feed,
        execution_series=FakeExecutionSeries(execution_bars, schedule=schedule),
        calendar=calendar,
        risk_policy=RISK_POLICY,
        execution_policy=execution_policy,
        cost_model=cost_model,
        conversion_policy=conversion_policy,
        symbol_spec=spec,
        symbol_spec_ref=spec_ref,
        calendar_ref=calendar_ref,
        integrity=IntegrityReport(),
        trace_sink=trace_sink,
        result_writer=writer,
        code_digest=CODE_DIGEST,
        lock_digest=LOCK_DIGEST,
        env_digest=ENV_DIGEST,
        git_commit="0" * 40,
        git_dirty=False,
        timeframe_refs=TIMEFRAME_REFS,
        publication_rows=published,
        conversion=supply,
        minutes=minutes,
    )
    result = use_case.run(config, compiled)
    assert writer.manifest is not None
    return RunOutput(
        result=result, manifest=writer.manifest, tables=dict(trace_sink.tables), context=context
    )
