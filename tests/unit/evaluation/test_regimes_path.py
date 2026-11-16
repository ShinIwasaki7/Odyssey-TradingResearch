"""経路の局面の成果物の組み立て（D24 §5.2・§5.3、仮置き 14・15。実装 PR 4-3）。

紙上トレース T01 の判断履歴（完了取引 1 件と残存建玉 1 件）を区間 1 つの run とみなし、
区間の終わりに残存建玉を仮に閉じた経路の取引を、入場の時点の局面に振り分ける。

- 取引の材料は評価の取引表と同じ組み立て（`segment_trade_facts`）で読み、仮に閉じた建玉は
  「仮決済の損益 − 入場の費用」（RP5-14 の入力の単位）。
- 局面の損益の和が経路の純損益と一致すれば表を出し、ずらした経路では表を出さず理由を出す。
- 局面の付与の失敗（経路が終端していない・日足を読めない・建玉の入場が無い）は「出せない
  （理由）」にする（D24 §5.2）。

値はすべて人工で、成績を読まない。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Final

from odyssey_fx.backtest.domain.orders import OrderSide
from odyssey_fx.backtest.trace.result import HypotheticalCloseLine, RunStatus
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId, PositionId
from odyssey_fx.common.money import Money, Price, Quantity, decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.application.evaluate_run import SegmentTradeFacts, segment_trade_facts
from odyssey_fx.evaluation.application.ports import DailyBarsUnavailable
from odyssey_fx.evaluation.application.regimes import path_regime_report, path_regime_trades
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    PathSegmentEnd,
    PathSegmentKey,
    PathSegmentRow,
    PathSummary,
    PathTradeKind,
    path_metrics,
)
from odyssey_fx.evaluation.domain.regimes import (
    RegimeDirectionRule,
    RegimeLabel,
    RegimeLabelAt,
    RegimeReport,
    RegimeReportStatus,
    RegimeRole,
    RegimeSpec,
    RegimeTimeframe,
    RegimeVolatilityRule,
)
from odyssey_fx.evaluation.domain.research_policy import check_evaluation_rule, check_run_matches
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from tests.fixtures.evaluation.traces import CALENDAR, JPY, RUN_INTERVAL, repository_for
from tests.fixtures.synthetic import market

SPEC: Final = RegimeSpec(
    role=RegimeRole.DISPLAY_ONLY,
    timeframe=RegimeTimeframe.DAILY,
    direction=RegimeDirectionRule(
        ema_bars=2, slope_bars=1, atr_bars=1, dead_band_atr_multiple=decimal_from_str("0.5")
    ),
    volatility=RegimeVolatilityRule(atr_bars=1, median_window_bars=1),
    label_at=RegimeLabelAt.LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY,
)
INITIAL: Final = Money(decimal_from_str("1000000"), JPY)
#: 区間の終わりに仮に閉じた残存建玉（T01 の P2）の仮決済の損益（人工。決済の費用を引いた後）。
SETTLED_PROFIT: Final = Money(decimal_from_str("-5000"), JPY)


def _daily(index: int, close: str) -> Bar:
    start = UtcTime.parse("2014-12-31T22:00:00Z") + timedelta(days=index)
    interval = Interval(start=start, end=start + timedelta(days=1))
    value = decimal_from_str(close)
    return Bar(
        series=market.series(timeframe_id="1d_ny17"),
        interval=interval,
        open=Price(value),
        high=Price(value + 1),
        low=Price(value - 1),
        close=Price(value),
        volume=Decimal(0),
        available_at=interval.end,
        provenance=Provenance(kind=ProvenanceKind.HISTDATA, source_ref="test"),
    )


class _Daily:
    """日足の読み出しの偽物（`DailyBarSource`）。毎日 1 ずつ上がる日足。"""

    def __init__(self, unavailable: str | None = None) -> None:
        self.unavailable = unavailable
        self.asked: list[tuple[Variant, UtcTime]] = []

    def daily_bars(
        self, variant: Variant, until: UtcTime
    ) -> tuple[Bar, ...] | DailyBarsUnavailable:
        self.asked.append((variant, until))
        if self.unavailable is not None:
            return DailyBarsUnavailable(self.unavailable)
        bars = tuple(_daily(index, str(150 + index)) for index in range(20))
        return tuple(bar for bar in bars if bar.bar_start < until)


def _facts() -> tuple[object, SegmentTradeFacts]:
    repository = repository_for()
    facts = segment_trade_facts(repository, repository.manifest.run_id)
    assert isinstance(facts, SegmentTradeFacts)
    return repository, facts


def _line(position: int) -> HypotheticalCloseLine:
    return HypotheticalCloseLine(
        position_id=PositionId(seq=position),
        side=OrderSide.BUY,
        quantity=Quantity(decimal_from_str("30000")),
        reference_bid=Price(decimal_from_str("150.80")),
        exit_price=Price(decimal_from_str("150.79")),
        commission=Money(decimal_from_str("30"), JPY),
        slippage=Money(decimal_from_str("300"), JPY),
        profit=SETTLED_PROFIT,
    )


def _end(settled_equity: Money, position: int = 2) -> PathSegmentEnd:
    repository = repository_for()
    run_id = repository.manifest.run_id
    return PathSegmentEnd(
        experiment_id=ExperimentId(digest("experiment")),
        key=PathSegmentKey(variant=Variant.STANDARD, segment_index=0),
        expected_run_id=run_id,
        run_id=run_id,
        run_status=RunStatus.COMPLETED,
        run_reused=False,
        run_evaluation_id=digest("evaluation"),
        evaluation_status=EvaluationStatus.COMPLETED,
        result_digest=digest("result"),
        final_balance=settled_equity,
        final_equity=settled_equity,
        settlement_lines=(_line(position),),
        settled_equity=settled_equity,
        trade_count=1,
        outcome_checks=(
            check_run_matches(
                expected_config_digest_hex="0" * 64,
                expected_run_id_hex=run_id.hex,
                observed_config_digest_hex="0" * 64,
                observed_run_id_hex=run_id.hex,
            ),
            check_evaluation_rule(4, 4),
        ),
    )


def _summary(final: Money | None, end: PathEnd = PathEnd.COMPLETED) -> PathSummary:
    row = PathSegmentRow(
        segment_index=0,
        fold_indices=(0,),
        trial_index=0,
        interval=RUN_INTERVAL,
        initial_balance=INITIAL,
        end_balance=final,
        carried_boundaries=0,
        settlement_count=1,
        settlement_cost=None,
        trade_count=1,
        run_id=repository_for().manifest.run_id,
        run_reused=False,
    )
    if end is PathEnd.INCOMPLETE:
        return PathSummary(
            segments=(row,),
            end=end,
            end_segment=0,
            end_reason="failed",
            metrics=(),
            trade_count=None,
            final_balance=None,
        )
    assert final is not None
    return PathSummary(
        segments=(row,),
        end=end,
        end_segment=None,
        end_reason=None,
        metrics=path_metrics(INITIAL, [INITIAL.amount, final.amount], final),
        trade_count=1,
        final_balance=final,
    )


def _net() -> Money:
    """経路の取引の和（完了取引の取引損益 + 仮決済の損益 − 残存建玉の入場の費用）。"""
    _, facts = _facts()
    entry = facts.open_entries[0]
    assert entry.entry_commission is not None
    return facts.closed_trades[0].trade_profit + SETTLED_PROFIT - entry.entry_commission


def test_the_trade_facts_hold_the_closed_trade_and_the_open_entry() -> None:
    _, facts = _facts()
    assert [trade.position_id for trade in facts.closed_trades] == [PositionId(seq=1)]
    assert [entry.position_id for entry in facts.open_entries] == [PositionId(seq=2)]
    assert facts.open_entries[0].entry_at == UtcTime.parse("2015-01-08T10:00:00Z")
    assert facts.open_entries[0].entry_commission == Money(decimal_from_str("30"), JPY)


def test_the_path_trades_carry_the_settlement_with_its_entry_cost() -> None:
    repository, _ = _facts()
    trades = path_regime_trades(repository, (_end(INITIAL + _net()),))  # type: ignore[arg-type]
    assert not isinstance(trades, str)
    assert [(item.kind, item.position_id) for item in trades] == [
        (PathTradeKind.CLOSED_TRADE, PositionId(seq=1)),
        (PathTradeKind.SEGMENT_END_SETTLEMENT, PositionId(seq=2)),
    ]
    assert trades[1].profit == SETTLED_PROFIT - Money(decimal_from_str("30"), JPY)
    assert trades[1].entry_at == UtcTime.parse("2015-01-08T10:00:00Z")


def _report(
    final: Money, daily: _Daily | None = None, end: PathEnd = PathEnd.COMPLETED
) -> RegimeReport:
    repository, _ = _facts()
    source = _Daily() if daily is None else daily
    return path_regime_report(
        variant=Variant.STANDARD,
        spec=SPEC,
        favored_regimes=(RegimeLabel.UP_LOW,),
        summary=_summary(final, end),
        ends=(_end(final),),
        daily_bars=source,
        calendar=CALENDAR,
        repository=repository,  # type: ignore[arg-type]
    )


def test_rp5_14_the_regimes_of_a_path_add_up_to_its_net() -> None:
    daily = _Daily()
    final = INITIAL + _net()
    report = _report(final, daily)
    assert report.status is RegimeReportStatus.AVAILABLE, report.reason
    assert report.regime_net == report.path_net == _net()
    assert daily.asked == [(Variant.STANDARD, RUN_INTERVAL.end)]
    # 毎日 1 ずつ上がる日足なので、入場の時点に公開済みの日足の局面は上昇。
    assert {trade.label for trade in report.trades} == {RegimeLabel.UP_LOW}
    assert report.favored is not None and report.favored.favored_count == 2
    assert len(report.days) == 10  # 2015-01-04T22:00Z〜01-16T22:00Z の取引日


def test_rp5_14_a_shifted_path_net_shows_no_table() -> None:
    shifted = INITIAL + _net() + Money(decimal_from_str("1"), JPY)
    report = _report(shifted)
    assert report.status is RegimeReportStatus.NOT_RECONCILED
    assert report.table == () and report.reason is not None


def test_a_path_that_did_not_end_cannot_be_labelled() -> None:
    report = _report(INITIAL, end=PathEnd.INCOMPLETE)
    assert report.status is RegimeReportStatus.UNAVAILABLE
    assert report.reason is not None and "終端していない" in report.reason


def test_unreadable_daily_bars_are_reported_not_raised() -> None:
    report = _report(INITIAL + _net(), _Daily(unavailable="系列が無い"))
    assert report.status is RegimeReportStatus.UNAVAILABLE
    assert report.reason == "日足を読めない: 系列が無い"


def test_a_settlement_without_its_entry_is_reported() -> None:
    repository, _ = _facts()
    trades = path_regime_trades(repository, (_end(INITIAL, position=9),))  # type: ignore[arg-type]
    assert isinstance(trades, str) and "POS:00000009" in trades
