"""バックテストを人工データで通すための組み立て（T01 の8経路が使う）。

実データも I/O も使わない。15分足（執行系列）と1時間足（評価系列）を明示的に与え、そこから
公開イベント（D03 §7.1 の4種）を機械的に作る。カレンダーは既存の人工データ生成器
（`tests/fixtures/synthetic/market.py`）と同じニューヨーク 17 時基準のものを使う。

数値は T01 の値であり、手計算で検算できることだけを目的にしている（全体計画 §8.2）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from odyssey_fx.backtest.application.run_backtest import RunBacktest
from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.domain.policies import (
    ConversionPolicy,
    CostModel,
    ExecutionPolicy,
    FixedSpread,
    ResolutionHierarchy,
    RiskPolicy,
    RunConfig,
)
from odyssey_fx.backtest.engine.loop import EngineContext, TraceOutputSink
from odyssey_fx.backtest.trace.manifest import RunManifest, config_digest_of
from odyssey_fx.backtest.trace.recorder import TraceTable, flatten_row
from odyssey_fx.backtest.trace.result import BacktestResult
from odyssey_fx.common.ids import AccountId, IdAllocator, SnapshotId
from odyssey_fx.common.money import (
    CurrencyCode,
    Money,
    Price,
    PriceOffset,
    decimal_from_str,
)
from odyssey_fx.common.refs import (
    CodeDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    SnapshotRef,
    run_id,
)
from odyssey_fx.common.symbol import Symbol, SymbolSpec, SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.bar import Bar, BarKey, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompiledStrategy, CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from tests.fixtures.strategy.fakes import FakeMarketDataView
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES, TIMEFRAMES, strategy_a
from tests.fixtures.synthetic.market import USDJPY, calendar, series

__all__ = [
    "ACCOUNT",
    "CONVERSION_POLICY",
    "COST_MODEL",
    "EXECUTION_POLICY",
    "EXECUTION_SERIES",
    "JPY",
    "RISK_POLICY",
    "RunOutput",
    "SYMBOL_SPEC",
    "bars",
    "flatten_all",
    "run_backtest",
]

JPY = CurrencyCode("JPY")

#: 執行系列（T01 §1.1）。
EXECUTION_SERIES = series(USDJPY, "15m", PriceBasis.BID)

#: 口座（T01 §1.1）。
ACCOUNT = AccountSpec(
    account_id=AccountId("ACC1"),
    currency=JPY,
    initial_balance=Money(decimal_from_str("1000000"), JPY),
)

#: 銘柄仕様（T01 §1.1）。
SYMBOL_SPEC = SymbolSpec(
    symbol=USDJPY,
    version=1,
    price_tick=decimal_from_str("0.001"),
    pip_size=decimal_from_str("0.01"),
    quantity_step=decimal_from_str("1000"),
    min_quantity=decimal_from_str("1000"),
)

#: ポリシー（T01 §1.2）。
RISK_POLICY = RiskPolicy(
    trial_risk_rate=decimal_from_str("0.02"), account_risk_cap=decimal_from_str("0.20")
)
COST_MODEL = CostModel(
    commission_per_unit=Money(decimal_from_str("0.001"), JPY),
    entry_slippage=PriceOffset(decimal_from_str("0.01")),
    close_slippage=PriceOffset(decimal_from_str("0.01")),
    spread_model=FixedSpread(offset=PriceOffset(decimal_from_str("0.02"))),
)
EXECUTION_POLICY = ExecutionPolicy(
    adverse_fill_limits={USDJPY: PriceOffset(decimal_from_str("0.05"))},
    entry_valid_for=timedelta(minutes=20),
    close_valid_for=timedelta(minutes=20),
    resolution_hierarchy=ResolutionHierarchy(levels=(EXECUTION_SERIES,)),
)
CONVERSION_POLICY = ConversionPolicy(
    pivot_currency=CurrencyCode("USD"), max_observation_skew=timedelta(minutes=15)
)


def _digest(seed: str) -> ContentDigest:
    """テスト用の固定ダイジェスト（16進64文字）。"""
    return ContentDigest.sha256(hashlib.sha256(seed.encode("utf-8")).hexdigest())


def _policy_ref(kind: str, seed: str) -> PolicyRef:
    return PolicyRef(policy_kind=kind, policy_id=f"{kind}_v1", version=1, digest=_digest(seed))


#: 銘柄仕様・カレンダー・時間足定義の版参照（`ConfigDigest` の対象、D06 §9.3）。
SYMBOL_SPEC_REF = SymbolSpecRef(symbol=USDJPY, version=1, digest=_digest("s"))
CALENDAR_REF = "fx_ny17@v1"
TIMEFRAME_REFS = (TimeframeRef("15m", 1), TimeframeRef("1h", 1))

#: 実行の出どころ（D06 §9.3 の識別の群）。テストでは固定値にする。
CODE_DIGEST = CodeDigest(digest=_digest("code"))
LOCK_DIGEST = LockDigest(digest=_digest("lock"))
ENV_DIGEST = EnvDigest(digest=_digest("env"))


def bars(
    series_id: SeriesId,
    first_start: UtcTime,
    step: timedelta,
    specs: Sequence[tuple[str, str, str, str]],
) -> tuple[Bar, ...]:
    """始値・高値・安値・終値を明示した足を連続して作る（利用可能時刻は足の終了時刻）。"""
    made: list[Bar] = []
    start = first_start
    for open_value, high_value, low_value, close_value in specs:
        interval = Interval(start=start, end=start + step)
        made.append(
            Bar(
                series=series_id,
                interval=interval,
                open=Price(decimal_from_str(open_value)),
                high=Price(decimal_from_str(high_value)),
                low=Price(decimal_from_str(low_value)),
                close=Price(decimal_from_str(close_value)),
                volume=decimal_from_str("1000"),
                available_at=interval.end,
                provenance=Provenance(
                    kind=ProvenanceKind.HISTDATA, source_ref="tests/fixtures/backtest"
                ),
            )
        )
        start = interval.end
    return tuple(made)


@dataclass(frozen=True, slots=True)
class _Kind:
    """公開イベントの種別（`marketdata.application` の列挙と同じ構造）。"""

    value: str


@dataclass(frozen=True, slots=True)
class _Event:
    """公開イベント1件（D03 §7.1）。"""

    kind: _Kind
    series: SeriesId
    bar_key: BarKey
    at: UtcTime


class FakeFeed:
    """足の列から公開イベントを作るフィード（D03 §7.1 の4種）。"""

    def __init__(self, signal: Sequence[Bar], execution: Sequence[Bar]) -> None:
        events: list[_Event] = []
        for bar in signal:
            events.append(_Event(_Kind("PUBLICATION"), bar.series, bar.key, bar.available_at))
            events.append(_Event(_Kind("SCHEDULED_BOUNDARY"), bar.series, bar.key, bar.bar_end))
        for bar in execution:
            events.append(_Event(_Kind("EXECUTION_OPEN"), bar.series, bar.key, bar.bar_start))
            events.append(_Event(_Kind("EXECUTION_BAR_COMPLETE"), bar.series, bar.key, bar.bar_end))
            events.append(_Event(_Kind("PUBLICATION"), bar.series, bar.key, bar.available_at))
            events.append(_Event(_Kind("SCHEDULED_BOUNDARY"), bar.series, bar.key, bar.bar_end))
        self._events = tuple(sorted(events, key=lambda event: event.at.value))

    def __iter__(self) -> Iterator[_Event]:
        """イベントを時刻順に反復する（`marketdata.application` の公開フィードと同じ形）。"""
        return iter(self._events)


class FakeExecutionSeries:
    """執行系列のビュー（D03 §6.3 の3操作）。"""

    def __init__(self, execution: Sequence[Bar]) -> None:
        self._bars = tuple(sorted(execution, key=lambda bar: bar.bar_start.value))

    def bar(self, bar_key: BarKey) -> Bar | None:
        for bar in self._bars:
            if bar.key == bar_key:
                return bar
        return None

    def open_of(self, bar_key: BarKey) -> Price | None:
        found = self.bar(bar_key)
        return None if found is None else found.open

    def next_bar_key_after(self, moment: UtcTime) -> BarKey | None:
        for bar in self._bars:
            if moment < bar.bar_start:
                return bar.key
        return None


class FakeIntrabarSeries:
    """下位足の供給（D06 §7.4 の階層が2段以上のときだけ使う）。"""

    def __init__(self, by_series: Mapping[SeriesId, Sequence[Bar]]) -> None:
        self._by_series = {key: tuple(value) for key, value in by_series.items()}

    def bars_in(self, target: SeriesId, interval: Interval) -> tuple[Bar, ...]:
        return tuple(
            bar
            for bar in sorted(self._by_series.get(target, ()), key=lambda bar: bar.bar_start.value)
            if interval.start <= bar.bar_start and bar.bar_end <= interval.end
        )


@dataclass
class CollectingTraceSink:
    """表ごとの行をそのまま貯める書き出し口。"""

    tables: dict[TraceTable, tuple[object, ...]] = field(default_factory=dict)

    def write(self, table: TraceTable, rows: tuple[object, ...]) -> None:
        self.tables[table] = rows


@dataclass
class CollectingResultWriter:
    """結果と manifest を貯める保存口。"""

    result: BacktestResult | None = None
    manifest: RunManifest | None = None

    def write(self, result: BacktestResult, manifest: RunManifest) -> None:
        self.result = result
        self.manifest = manifest


@dataclass(frozen=True, slots=True)
class RunOutput:
    """1回の run の成果（テストが読む）。"""

    result: BacktestResult
    manifest: RunManifest
    tables: Mapping[TraceTable, tuple[object, ...]]
    context: EngineContext

    def rows(self, table: TraceTable) -> tuple[object, ...]:
        """表の行。"""
        return self.tables[table]


def compiled_strategy(definition: StrategyDefinition | None = None) -> CompiledStrategy:
    """検証戦略 A をコンパイルする。"""
    outcome = compile_strategy(
        strategy_a() if definition is None else definition, INITIAL_CATALOG, TIMEFRAMES
    )
    assert isinstance(outcome, CompileSucceeded), outcome
    return outcome.compiled


def run_backtest(
    *,
    signal_bars: Sequence[Bar],
    execution_bars: Sequence[Bar],
    run_interval: Interval,
    definition: StrategyDefinition | None = None,
    execution_policy: ExecutionPolicy = EXECUTION_POLICY,
    cost_model: CostModel = COST_MODEL,
    risk_policy: RiskPolicy = RISK_POLICY,
    account: AccountSpec = ACCOUNT,
    intrabar: Mapping[SeriesId, Sequence[Bar]] | None = None,
    execution_view_bars: Sequence[Bar] | None = None,
) -> RunOutput:
    """人工データで1回の run を通す。

    `execution_view_bars` に別の列を渡すと、公開フィードは足の到着を知らせるのに執行系列に
    その足が無い状態を作れる（実行中のデータ不整合の検証に使う）。
    """
    compiled = compiled_strategy(definition)
    config = RunConfig(
        run_interval=run_interval,
        snapshot_ref=SnapshotRef(snapshot_id=SnapshotId(_digest("n"))),
        compiled_ref=compiled.compiled_ref,
        account=account,
        risk_policy_ref=_policy_ref("risk", "r"),
        execution_policy_ref=_policy_ref("execution", "e"),
        cost_model_ref=_policy_ref("cost", "c"),
        conversion_policy_ref=_policy_ref("conversion", "v"),
        delay_scenario_ref=_policy_ref("delay", "d"),
        execution_series=EXECUTION_SERIES,
    )
    # `RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`（ADR-0006）。
    # manifest がこの関係を検査するので、テストでも同じ組み立て方で作る。
    config_digest = config_digest_of(
        config,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=CALENDAR_REF,
        timeframe_def_refs=TIMEFRAME_REFS,
    )
    allocator = IdAllocator(run_id(config_digest, CODE_DIGEST, LOCK_DIGEST, ENV_DIGEST))
    sink = TraceOutputSink()
    context = EngineContext(account)
    evaluator = StrategyEvaluator(
        compiled=compiled,
        registry=INITIAL_CATALOG,
        market_data=FakeMarketDataView({SIGNAL_SERIES: tuple(signal_bars)}),
        context=context,
        sink=sink,
        allocator=allocator,
    )
    trace_sink = CollectingTraceSink()
    result_writer = CollectingResultWriter()
    use_case = RunBacktest(
        runtime=evaluator,
        context=context,
        output_sink=sink,
        allocator=allocator,
        feed=FakeFeed(signal_bars, execution_bars),
        execution_series=FakeExecutionSeries(
            execution_bars if execution_view_bars is None else execution_view_bars
        ),
        calendar=calendar(),
        risk_policy=risk_policy,
        execution_policy=execution_policy,
        cost_model=cost_model,
        conversion_policy=CONVERSION_POLICY,
        symbol_spec=SYMBOL_SPEC,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=CALENDAR_REF,
        integrity=IntegrityReport(),
        trace_sink=trace_sink,
        result_writer=result_writer,
        code_digest=CODE_DIGEST,
        lock_digest=LOCK_DIGEST,
        env_digest=ENV_DIGEST,
        git_commit="0" * 40,
        git_dirty=False,
        timeframe_refs=TIMEFRAME_REFS,
        intrabar_series=None if intrabar is None else FakeIntrabarSeries(intrabar),
    )
    result = use_case.run(config, compiled)
    assert result_writer.manifest is not None
    return RunOutput(
        result=result,
        manifest=result_writer.manifest,
        tables=dict(trace_sink.tables),
        context=context,
    )


def _snapshot_id() -> SnapshotId:

    return SnapshotId(_digest("n"))


def flatten_all(output: RunOutput) -> dict[str, list[dict[str, object]]]:
    """全表の行を平坦化する（再実行の一致を比べるために使う）。"""
    return {
        table.value: [flatten_row(row) for row in rows] for table, rows in output.tables.items()
    }


def amount(value: Money) -> Decimal:
    """金額の数値だけを取り出す（検算の比較に使う）。"""
    return value.amount


def compiled_symbol() -> Symbol:
    """検証戦略 A の銘柄。"""
    return USDJPY
