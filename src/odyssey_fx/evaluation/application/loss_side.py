"""研究ポリシーの版 5 以上の判定の材料を run の成果物から組み立てる（D24 §3.3・§4。実装 PR 4-2）。

探索の実行（`run_experiment`）と最終検証の段 2 (f) の再計算（`final_recompute`）が同じ関数で材料を
作る（同じ量を 2 通りに作らない）。どれも保存済みの run の判断履歴を読むだけで、run も評価もしない。

- 経路の取引の集合の要約（`path_trade_totals`）: 経路の各区間の run の完了取引（評価の取引表と同じ
  作り方。局面の振り分けと同じ材料 `segment_trade_facts`）と、区間の終わりに仮に閉じた建玉（区間の
  終端の記録の明細と、その建玉の入場の手数料）から
  `domain.path.path_trade_set` で集合を組み、恒等式を確かめて要約にする（D24 §3.3、仮置き 4・14）。
- 日次の収益率（`run_daily_returns`・`path_daily_returns`）: #17 と同じ日次の作り方（取引日の終わり
  以前で最後の資産。無ければ前日を持ち越す。D24 の仮置き 8）。経路は区間の終わりの費用を引いた後の
  点を含む資産の系列に当てる。系列は保存しない（D24 の仮置き 10）。
- 判定の追加の入力（`loss_side_evidence`）: 近傍の表（`grid_neighbors`）、試行の数 `M`（コンパイル
  拒否でない試行。仮置き 6）、選定の区間の試行ごとの日次の収益率と経路の日次の収益率。**局面を
  読まない**（D24 の仮置き 15、RP5-16）。

成果物が読めない・解釈できないときは構造エラー（`KernelValueError`）で止める（経路の資産の系列が
読めないときと同じ。D01 §2.2 の 5）。取引カレンダーの取引日の境界が 1 つに決まらないときは、#17 と
同じく入力が無い値なし（`INPUT_NOT_AVAILABLE`）にする。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from odyssey_fx.backtest.trace.manifest import RunManifest
from odyssey_fx.backtest.trace.result import HypotheticalCloseLine
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import PositionId, RunId
from odyssey_fx.common.money import Money
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.application.evaluate_run import (
    SegmentTradeFacts,
    equity_points,
    segment_trade_facts,
)
from odyssey_fx.evaluation.application.ports import ResultRepository
from odyssey_fx.evaluation.domain.deflated_downside import (
    DailyReturns,
    DeflatedDownsideInputs,
    TrialDailyReturns,
)
from odyssey_fx.evaluation.domain.deflated_sharpe import daily_returns
from odyssey_fx.evaluation.domain.metrics import MetricUnavailableReason, trading_day_ends
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    PathSegmentEnd,
    PathSummary,
    PathSummaryV5,
    PathTradeTotals,
    SegmentTrades,
    SettledPosition,
    path_trade_set,
)
from odyssey_fx.evaluation.domain.search import (
    FoldEvidence,
    LossSideEvidence,
    SearchPlan,
    TrialStatus,
    grid_neighbors,
)
from odyssey_fx.marketdata.domain.calendar import TradingCalendar

__all__ = [
    "loss_side_evidence",
    "path_daily_returns",
    "path_summary_v5",
    "path_trade_totals",
    "run_daily_returns",
]


def _material(repository: ResultRepository, run_id: RunId, label: str) -> SegmentTradeFacts:
    """区間の run の完了取引と残った建玉の入場（局面の振り分けと同じ材料）。"""
    found = segment_trade_facts(repository, run_id)
    if isinstance(found, str):
        raise KernelValueError(f"{label}: {found} (D24 §3.3)")
    return found


def _commission(
    commissions: Mapping[PositionId, Money | None], line: HypotheticalCloseLine
) -> Money | None:
    """仮に閉じた建玉の入場の手数料（区間の run に残った建玉でなければ構造エラー）。"""
    if line.position_id not in commissions:
        raise KernelValueError(
            f"the settled position {line.position_id} is not an open position of the run (D24 §3.3)"
        )
    return commissions[line.position_id]


def path_trade_totals(
    repository: ResultRepository,
    summary: PathSummary,
    ends: Mapping[int, PathSegmentEnd],
) -> PathTradeTotals:
    """経路の取引の集合を組み、恒等式を確かめて要約にする（D24 §3.3、仮置き 4・14）。

    判定できないで終えた経路には使わない（呼び出し側が分ける）。候補なしの区間は残高を現金で保つ
    ので取引を持たない。`ends` は区間の番号から区間の終端の記録への対応。
    """
    if summary.end is PathEnd.INCOMPLETE or summary.final_balance is None:
        raise KernelValueError("path_trade_totals requires a path that was not left incomplete")
    segments: list[SegmentTrades] = []
    for row in summary.segments:
        if row.trial_index is None:
            continue
        end = ends.get(row.segment_index)
        label = f"the path segment {row.segment_index}"
        if end is None or not end.succeeded or end.settled_equity is None:
            raise KernelValueError(f"{label} has no succeeded end record (D24 §3.3)")
        material = _material(repository, end.run_id, label)
        commissions = {item.position_id: item.entry_commission for item in material.open_entries}
        segments.append(
            SegmentTrades(
                segment_index=row.segment_index,
                initial_balance=row.initial_balance,
                settled_equity=end.settled_equity,
                closed_trades=material.closed_trades,
                settled=tuple(
                    SettledPosition(line=line, entry_commission=_commission(commissions, line))
                    for line in end.settlement_lines
                ),
            )
        )
    initial = summary.segments[0].initial_balance
    return PathTradeTotals.of(
        path_trade_set(segments, initial_balance=initial, final_balance=summary.final_balance)
    )


def path_summary_v5(
    summary: PathSummary,
    repository: ResultRepository,
    ends: Mapping[int, PathSegmentEnd],
) -> PathSummaryV5:
    """版 4 の形の経路の要約に、経路の取引の集合の要約を足す（D24 §3.3・§9.3）。"""
    trades = (
        None if summary.end is PathEnd.INCOMPLETE else path_trade_totals(repository, summary, ends)
    )
    return PathSummaryV5(
        segments=summary.segments,
        end=summary.end,
        end_segment=summary.end_segment,
        end_reason=summary.end_reason,
        metrics=summary.metrics,
        trade_count=summary.trade_count,
        final_balance=summary.final_balance,
        trades=trades,
    )


def _returns_of(
    initial: Decimal,
    points: Sequence[tuple[UtcTime, Decimal]],
    calendar: TradingCalendar,
    interval: Interval,
) -> DailyReturns:
    ends = trading_day_ends(calendar, interval)
    if ends is None:
        return MetricUnavailableReason.INPUT_NOT_AVAILABLE
    found = daily_returns(initial, points, ends)
    return MetricUnavailableReason.UNDEFINED_DENOMINATOR if found is None else found


def run_daily_returns(
    repository: ResultRepository, run_id: RunId, calendar: TradingCalendar
) -> DailyReturns:
    """独立口座の run の日次の収益率（#17 と同じ作り方。D24 §4.1、仮置き 8）。"""
    manifest = repository.read_manifest(run_id)
    if not isinstance(manifest, RunManifest):
        raise KernelValueError(f"the run manifest of {run_id} cannot be read (D24 §4)")
    points = equity_points(repository, run_id)
    if isinstance(points, str):
        raise KernelValueError(f"{points} (D24 §4)")
    return _returns_of(
        manifest.config.account.initial_balance.amount,
        points,
        calendar,
        manifest.config.run_interval,
    )


def path_daily_returns(
    repository: ResultRepository,
    summary: PathSummary,
    ends: Mapping[int, PathSegmentEnd],
    calendar: TradingCalendar,
) -> DailyReturns:
    """経路の日次の収益率（D24 §4.1、仮置き 8）。

    経路の区間の始まりから最後の区間の終わりまでの取引日に、各区間の run の台帳の資産の点と、区間の
    終わりの費用を引いた後の資産の点（区間の終わりの時刻）をつないだ系列を当てる（D07 §5.6、D13 の
    仮置き 28）。候補なしの区間は資産が変わらない（前日を持ち越す）。
    """
    if summary.end is PathEnd.INCOMPLETE or not summary.segments:
        raise KernelValueError("path_daily_returns requires a path that was not left incomplete")
    points: list[tuple[UtcTime, Decimal]] = []
    for row in summary.segments:
        if row.trial_index is None:
            continue
        end = ends.get(row.segment_index)
        if end is None or end.settled_equity is None:
            raise KernelValueError(f"the path segment {row.segment_index} has no settled equity")
        read = equity_points(repository, end.run_id)
        if isinstance(read, str):
            raise KernelValueError(f"the path segment {row.segment_index}: {read} (D24 §4)")
        points.extend(read)
        points.append((row.interval.end, end.settled_equity.amount))
    points.sort(key=lambda item: item[0])
    interval = Interval(
        start=summary.segments[0].interval.start, end=summary.segments[-1].interval.end
    )
    return _returns_of(summary.segments[0].initial_balance.amount, points, calendar, interval)


def loss_side_evidence(
    *,
    plan: SearchPlan,
    folds: Sequence[FoldEvidence],
    train_runs: Mapping[tuple[int, int], RunId],
    summary: PathSummary,
    ends: Mapping[int, PathSegmentEnd],
    repository: ResultRepository,
    calendar: TradingCalendar,
) -> LossSideEvidence:
    """版 5 以上の判定の追加の入力を組み立てる（D24 §3.5・§4。仮置き 6・7・10）。

    `train_runs` は `(fold_index, trial_index)` から選定の区間の単位の run への対応（試行記録の
    `run_id`）。結果が有効な単位（`has_valid_result`）だけ日次の収益率を読み、それ以外（未試行・中断・
    run の失敗・事後検査の不合格）は入力が無い値なしにする。コンパイル拒否の試行は試行の数にも材料
    にも入れない（仮置き 6）。
    """
    neighbors = grid_neighbors(plan)
    deflated: DeflatedDownsideInputs | None = None
    if summary.end is not PathEnd.INCOMPLETE:
        trials: list[TrialDailyReturns] = []
        counted: set[int] = set()
        for item in sorted(folds, key=lambda fold: fold.fold.fold_index):
            for unit in sorted(item.train_units, key=lambda found: found.trial_index):
                if unit.status is TrialStatus.FAILED:
                    continue
                counted.add(unit.trial_index)
                returns: DailyReturns = MetricUnavailableReason.INPUT_NOT_AVAILABLE
                if unit.has_valid_result:
                    run_id = train_runs.get((item.fold.fold_index, unit.trial_index))
                    if run_id is None:
                        raise KernelValueError(
                            f"the selection run of fold {item.fold.fold_index} trial"
                            f" {unit.trial_index} is not known (D24 §4)"
                        )
                    returns = run_daily_returns(repository, run_id, calendar)
                trials.append(
                    TrialDailyReturns(
                        fold_index=item.fold.fold_index,
                        trial_index=unit.trial_index,
                        returns=returns,
                    )
                )
        deflated = DeflatedDownsideInputs(
            path_returns=path_daily_returns(repository, summary, ends, calendar),
            trial_count=len(counted),
            trials=tuple(trials),
        )
    return LossSideEvidence(neighbors=neighbors, deflated=deflated)
