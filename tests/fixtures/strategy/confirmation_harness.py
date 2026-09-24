"""検証戦略 B のランタイムを、エンジンの受付と約定だけを真似て判断時点ごとに動かす道具。

紙上トレース T02 の経路1・2・3・8・9（確認・市場状態・トレーリング）を、エンジンを使わずに
戦略ランタイム単体で再現するためのものである（D05 §7.6・§7.7・§6.11）。待機の経路（経路5〜7）
の道具（`runtime_harness`）と同じく、本物の as-of ビューと、エンジンと同じ規則で組み立てた
公開バッチ（D06 §4.3）を使う。

**エンジンの代わりにすること**（D06 §4.2 の手順のうち、ランタイムの入口に届くものだけ）:

- 第1回の `step` が発注提案を返したら、同じ判断時刻の約定後の評価起動点で第2回の `step` を
  呼ぶ。受付結果の通知（`AdmissionNotice(accepted=True)`）を渡し、`open_positions=True` なら
  約定通知（`POSITION_OPENED`）も渡して、現在コンテキストに建玉を置く。
- 建玉の値（約定価格・数量・損切り水準）は T02 §3.5 の値を渡す。約定そのもの（価格の決め方）
  はエンジンの責務であり、ここでは計算しない（段階3 実装 PR 5/5 の受入れテストが確かめる）。

受付の審査・約定・台帳は真似ない。管理要求（損切り水準の更新）の適用も行わない（PR 5/5）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from odyssey_fx.common.ids import (
    AttemptId,
    EventId,
    IdAllocator,
    OpportunityId,
    PositionId,
    RunId,
)
from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.schedule import DelayRule, DelayScenario
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.catalog.registry import ComponentRegistry
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from odyssey_fx.strategy.declarations.definition import StrategyDefinition
from odyssey_fx.strategy.declarations.evaluation import RuntimeEventKind
from odyssey_fx.strategy.records.payloads import TradeDirection
from odyssey_fx.strategy.runtime.evaluator import StrategyEvaluator
from odyssey_fx.strategy.runtime.ports import AdmissionNotice, PublicationBatch, RuntimeEventNotice
from odyssey_fx.strategy.runtime.requests import EvaluationRecord, RuntimeStepResult
from tests.fixtures.acceptance import t02_market
from tests.fixtures.strategy.fakes import CollectingSink, FakePositionContext
from tests.fixtures.strategy.phases import BACKTEST_PHASES
from tests.fixtures.strategy.runtime_harness import asof_view, batches
from tests.fixtures.strategy.strategy_b import (
    DAILY_SERIES,
    HOURLY_SERIES,
    M15_SERIES,
    TIMEFRAMES,
    strategy_b,
)
from tests.fixtures.synthetic import market

__all__ = ["ConfirmationRun", "MutableContextView", "t02_bars"]

#: T02 §3.5 の建玉 P1 の値（約定価格・初期損切り）。エンジンが決める値の写しである。
_P1_ENTRY = Price(decimal_from_str("149.390"))
_P1_STOP = Price(decimal_from_str("148.950"))


def t02_bars(*rules: DelayRule, m15_variant: str = "route1") -> dict[SeriesId, tuple[Bar, ...]]:
    """T02 の人工データ（日足は1時間足から集約する。T02 §16）に遅延を当てた足。"""
    calendar = market.calendar()
    hourly = t02_market.bars_for(
        "1h",
        market.TF_1H,
        calendar,
        Interval(
            start=UtcTime.parse("2014-09-30T22:00:00Z"), end=UtcTime.parse("2015-01-09T22:00:00Z")
        ),
    )
    quarter = t02_market.bars_for(
        "15m",
        market.TF_15M,
        calendar,
        Interval(
            start=UtcTime.parse("2015-01-06T00:00:00Z"), end=UtcTime.parse("2015-01-09T22:00:00Z")
        ),
        m15_variant=m15_variant,
    )
    daily = t02_market.aggregate(hourly, market.TF_1D_NY17, calendar)
    scenario = DelayScenario(id="t02", version=1, rules=tuple(rules))
    return {
        DAILY_SERIES: t02_market.apply_delay(daily, scenario),
        HOURLY_SERIES: t02_market.apply_delay(hourly, scenario),
        M15_SERIES: t02_market.apply_delay(quarter, scenario),
    }


@dataclass
class MutableContextView:
    """建玉を後から置ける現在コンテキスト（エンジンの `RuntimeContextView` の代わり）。"""

    positions: dict[PositionId, FakePositionContext] = field(default_factory=dict)

    def position_context(self, at: UtcTime, position_id: PositionId | None) -> object | None:
        del at
        if position_id is None:
            if len(self.positions) == 1:
                return next(iter(self.positions.values()))
            return None
        return self.positions.get(position_id)

    def account_context(self, at: UtcTime) -> object:
        del at
        return None


class ConfirmationRun:
    """戦略ランタイムを判断時点ごとに進め、各 `step` の結果を控える。

    `results[t]` は判断時刻 `t` の第1回の `step`、`post_fill[t]` は同じ時刻の第2回の `step`
    （受付と約定の通知を運ぶ回）の結果である。
    """

    def __init__(
        self,
        *rules: DelayRule,
        m15_variant: str = "route1",
        definition: StrategyDefinition | None = None,
        registry: ComponentRegistry = INITIAL_CATALOG,
        open_positions: bool = True,
    ) -> None:
        self.bars = t02_bars(*rules, m15_variant=m15_variant)
        compiled = compile_strategy(definition or strategy_b(), registry, TIMEFRAMES)
        assert isinstance(compiled, CompileSucceeded), compiled
        self.context = MutableContextView()
        self.sink = CollectingSink()
        self.evaluator = StrategyEvaluator(
            compiled=compiled.compiled,
            registry=registry,
            market_data=asof_view(self.bars),
            context=self.context,
            sink=self.sink,
            allocator=IdAllocator(RunId(ContentDigest.sha256("c" * 64))),
        )
        self.open_positions = open_positions
        self.results: dict[UtcTime, RuntimeStepResult] = {}
        self.post_fill: dict[UtcTime, RuntimeStepResult] = {}
        self._extra_batch = 1_000_000
        self._attempts = 0
        self._positions = 0

    def run(self, start: UtcTime, end: UtcTime) -> ConfirmationRun:
        """`[start, end]` の判断時点を順に処理する。"""
        for batch in batches(self.bars, start, end):
            result = self.evaluator.step(batch)
            self.results[batch.decision_time] = result
            if result.proposals:
                self._admit(batch.decision_time, [item.opportunity_id for item in result.proposals])
        return self

    def _admit(self, at: UtcTime, opportunities: Sequence[OpportunityId]) -> None:
        """発注提案をすべて受け付け、約定したものとして第2回の `step` を呼ぶ。"""
        admissions: list[AdmissionNotice] = []
        opened: list[RuntimeEventNotice] = []
        for opportunity_id in opportunities:
            self._attempts += 1
            admissions.append(
                AdmissionNotice(
                    opportunity_id=opportunity_id,
                    attempt_id=AttemptId(self._attempts),
                    accepted=True,
                )
            )
            if not self.open_positions:
                continue
            self._positions += 1
            position_id = PositionId(self._positions)
            self.context.positions[position_id] = FakePositionContext(
                position_id=position_id,
                direction=TradeDirection.LONG,
                entry_price=_P1_ENTRY,
                effective_stop_loss=_P1_STOP,
            )
            opened.append(
                RuntimeEventNotice(
                    kind=RuntimeEventKind.POSITION_OPENED,
                    position_id=position_id,
                    opportunity_id=opportunity_id,
                )
            )
        self._extra_batch += 1
        batch = PublicationBatch(
            batch_id=EventId(self._extra_batch),
            decision_time=at,
            phases=BACKTEST_PHASES,
            runtime_events=tuple(opened),
            admissions=tuple(admissions),
        )
        self.post_fill[at] = self.evaluator.step(batch)

    def evaluations(self, at: UtcTime, instance_id: str) -> list[EvaluationRecord]:
        """判断時刻 `at` の第1回の `step` の、その使用箇所の評価記録。"""
        return [
            record for record in self.results[at].evaluations if record.instance_id == instance_id
        ]

    def only(self, at: UtcTime, instance_id: str) -> EvaluationRecord:
        """その使用箇所の評価記録がちょうど1件であることを確かめて返す。"""
        records = self.evaluations(at, instance_id)
        assert len(records) == 1, records
        return records[0]

    def payloads(self, at: UtcTime) -> Mapping[str, object]:
        """判断時刻 `at` の第1回の `step` の出力を、使用箇所の名前で引けるようにする。"""
        return {record.producer.instance_id: record.payload for record in self.results[at].outputs}
