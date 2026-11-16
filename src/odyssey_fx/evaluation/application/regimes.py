"""経路の局面の付与（研究ポリシー第 5 版の実装 PR 4-3。D24 §5.2・§5.3、決定 164）。

組の連続口座の検証経路が終端した後、結末記録より前に、組ごとの局面の成果物を組み立てる
（D24 §5.2 の「計算の時点」、仮置き 15）。区分・振り分け・整合は `evaluation.domain.regimes` の
純粋関数が行い、ここは材料（日足・区間の run の取引・取引日）を集めるだけである。

- 日足は `DailyBarSource`（`evaluation.application.ports`。合成が `marketdata.application` の
  as-of の読み出しを結線する）から読む。読みの制限の外への要求は構造エラーで止まり、ここでは
  捕まえない（RP5-15。例外を欠損に読み替えない。D01 §2.2 の 5）。
- 区間の run の取引は判断履歴の表から評価の取引表と同じ組み立てで読む（`segment_trade_facts`）。
- **局面の付与の失敗は判定を変えない**（D24 §5.2）: 日足が読めない・取引が読めない・経路が終端して
  いないときは、局面の成果物に「出せない（理由）」を書く。

局面は判定・選定・戦略に使わない（決定 130・134）。本モジュールの結果は局面の成果物にだけ入り、
判定の関数（`domain.search`）へは渡らない（RP5-16）。
"""

from __future__ import annotations

from collections.abc import Sequence

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.time import Interval
from odyssey_fx.evaluation.application.evaluate_run import SegmentTradeFacts, segment_trade_facts
from odyssey_fx.evaluation.application.ports import (
    DailyBarSource,
    DailyBarsUnavailable,
    ResultRepository,
)
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    PathSegmentEnd,
    PathSummary,
    PathTradeKind,
    SettledPosition,
)
from odyssey_fx.evaluation.domain.regimes import (
    RegimeLabel,
    RegimeReport,
    RegimeSpec,
    RegimeTrade,
    build_regime_report,
    label_daily_bars,
    trading_day_spans,
    unavailable_regime_report,
)
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.marketdata.domain.calendar import TradingCalendar

__all__ = ["path_regime_report", "path_regime_trades"]


def path_regime_trades(
    repository: ResultRepository, ends: Sequence[PathSegmentEnd]
) -> tuple[RegimeTrade, ...] | str:
    """経路の取引（完了取引と区間の終わりに仮に閉じた建玉）の振り分けの入力（D24 §5.3）。

    `ends` は経路の区間の終端の記録（区間の番号の昇順）。成功した区間だけを読む。1 件の損益の単位は
    経路の取引の集合（D24 §3.3）と同じで、仮に閉じた建玉は「仮決済の損益 − 入場の費用」
    （`SettledPosition.profit`）。並びは区間の番号の順、区間の中は完了取引（取引表の順）→ 仮決済
    （明細の順）。読めないときは理由の文字列。
    """
    found: list[RegimeTrade] = []
    for end in ends:
        if not isinstance(end, PathSegmentEnd):
            raise KernelValueError("path_regime_trades requires PathSegmentEnd values")
        if not end.succeeded:
            continue
        index = end.key.segment_index
        facts = segment_trade_facts(repository, end.run_id)
        if not isinstance(facts, SegmentTradeFacts):
            return f"経路の区間 {index} の取引を読めない: {facts}"
        found.extend(
            RegimeTrade(
                segment_index=index,
                kind=PathTradeKind.CLOSED_TRADE,
                position_id=trade.position_id,
                entry_at=trade.entry_at.time,
                profit=trade.trade_profit,
            )
            for trade in facts.closed_trades
        )
        entries = {entry.position_id: entry for entry in facts.open_entries}
        for line in end.settlement_lines:
            entry = entries.get(line.position_id)
            if entry is None:
                return (
                    f"経路の区間 {index} の終わりに仮に閉じた建玉 {line.position_id} の入場が"
                    "建玉の表に無い"
                )
            found.append(
                RegimeTrade(
                    segment_index=index,
                    kind=PathTradeKind.SEGMENT_END_SETTLEMENT,
                    position_id=line.position_id,
                    entry_at=entry.entry_at,
                    profit=SettledPosition(
                        line=line, entry_commission=entry.entry_commission
                    ).profit,
                )
            )
    return tuple(found)


def path_regime_report(
    *,
    variant: Variant,
    spec: RegimeSpec,
    favored_regimes: tuple[RegimeLabel, ...] | None,
    summary: PathSummary,
    ends: Sequence[PathSegmentEnd],
    daily_bars: DailyBarSource | None,
    calendar: TradingCalendar,
    repository: ResultRepository,
) -> RegimeReport:
    """組の経路の局面の成果物を組み立てる（D24 §5.2〜§5.4、仮置き 11・14・15）。

    局面を付けるのは経路の区間（検証の区間）の取引と取引日だけ（仮置き 11）。日足は snapshot の
    最初の日足から経路の終わりまで読む。経路が区間の run の失敗で終わった（`INCOMPLETE`）なら、
    経路の純損益が定まらないので「出せない」にする。資金喪失（`CAPITAL_LOST`）で終わった経路は、
    走った区間までの取引と取引日に付ける。
    """
    initial = summary.segments[0].initial_balance if summary.segments else None

    def unavailable(reason: str, series: str | None = None) -> RegimeReport:
        return unavailable_regime_report(
            variant=variant,
            spec=spec,
            reason=reason,
            favored_regimes=favored_regimes,
            daily_series=series,
            initial_balance=initial,
        )

    if summary.end is PathEnd.INCOMPLETE or summary.final_balance is None or initial is None:
        return unavailable(
            "経路が終端していない（区間の run の失敗で止まった）ので、経路の純損益が定まらない"
            "（D24 §5.3）"
        )
    if daily_bars is None:
        return unavailable("日足の読み出しが結線されていない（D24 §5.2）")
    interval = Interval(
        start=summary.segments[0].interval.start, end=summary.segments[-1].interval.end
    )
    bars = daily_bars.daily_bars(variant, interval.end)
    if isinstance(bars, DailyBarsUnavailable):
        return unavailable(f"日足を読めない: {bars.reason}")
    if not bars:
        return unavailable("日足が 1 本も無い")
    series = str(bars[0].series)
    days = trading_day_spans(calendar, interval)
    if days is None:
        return unavailable("取引カレンダーの取引日の境界が 1 つに決まらない（D03 §3.4.1）", series)
    trades = path_regime_trades(repository, ends)
    if isinstance(trades, str):
        return unavailable(trades, series)
    return build_regime_report(
        variant=variant,
        spec=spec,
        daily_series=series,
        daily=label_daily_bars(bars, spec),
        days=days,
        trades=trades,
        initial_balance=initial,
        path_net=summary.final_balance - initial,
        favored_regimes=favored_regimes,
    )
