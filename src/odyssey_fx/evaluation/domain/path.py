"""連続口座の検証経路（D13 §3.2・§4.3〜§4.7、D09 §6.4・§10.3・§10.6 の 2026-10-21 改訂）。

決定 D13-4 (a)・D13-5 (b)・D13-22 (a)・D13-23 (a)・D13-25 (a)、仮置き 6・11・14〜20・25・26・
28・29、実装 PR の仮置き 45・48・50（2026-10-22 の人間の決定）。

- **経路の区間**（`plan_segments`）: 組ごとに、全 fold の選定記録がそろった後に、隣り合う fold の
  選定記録の `trial_index` が等しい間は 1 つの区間にまとめ（仮置き 14）、候補なしの fold は単独の
  区間にする（仮置き 15）。1 つの区間は 1 本の run（D13-22 (a)）で、区間の中の fold の境目は run の
  途中の時刻にすぎない（建玉・注文はそのまま続く）。
- **区間の終わりの決済**（実装 PR の仮置き 45: 方式 (ii) 仮に閉じた損益で決める）: 区間の run の
  末尾の3集計の `hypothetical_closed`（建玉ごとの明細つき）で、区間の終わりの**費用を引いた後の
  資産** = 台帳の残高 + 仮に閉じた損益（手数料・滑りを引いた後）を求め、経路の資産の系列のその
  区間の最後の点と、次の区間に渡す残高の両方にする（仮置き 28）。
- **資金喪失**（仮置き 29）: 区間の終わりの費用を引いた後の残高が 0 以下なら、次の区間を始めず
  経路を `CAPITAL_LOST` で終える。
- **経路の指標**（D07 §5.6）: 全体の純収益率（最後の残高 ÷ 最初の残高 − 1）と、全体の最大
  ドローダウン率（区間の資産の系列をつなぎ、最高値を持ち越して #20 の式を当てる）。

ここは純粋関数と記録の型だけを持つ。区間の run の実行・評価・記録の保存は
`evaluation.application.run_experiment` が行う。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from typing import TYPE_CHECKING, Final

from odyssey_fx.backtest.domain.policies import RunEndMode
from odyssey_fx.backtest.trace.result import HypotheticalCloseLine, RunStatus
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId, RunId
from odyssey_fx.common.money import Money, decimal_from_int, kernel_context
from odyssey_fx.common.refs import CompiledStrategyRef, ConfigDigest, ContentDigest
from odyssey_fx.common.time import Interval
from odyssey_fx.evaluation.domain.metrics import (
    MetricId,
    MetricKind,
    MetricUnavailableReason,
    MetricValue,
    RatioValue,
    Unavailable,
    max_drawdown_rate_peak,
    ratio_of,
)
from odyssey_fx.evaluation.domain.splits import Fold
from odyssey_fx.evaluation.domain.status import EvaluationStatus
from odyssey_fx.evaluation.domain.variant import Variant

if TYPE_CHECKING:  # pragma: no cover - 型検査のためだけの参照（循環を避けて使う時点で引く）
    from odyssey_fx.evaluation.domain.research_policy import PolicyCheckResult

__all__ = [
    "PATH_METRICS",
    "PathEnd",
    "PathSegmentEnd",
    "PathSegmentKey",
    "PathSegmentRow",
    "PathSegmentStart",
    "PathSummary",
    "PlannedSegment",
    "path_metrics",
    "plan_segments",
]

#: 経路の条件に書ける指標（D07 §5.6 が経路に定義した指標。実装 PR の仮置き 64）。
PATH_METRICS: Final[frozenset[MetricId]] = frozenset(
    {MetricId.NET_RETURN_RATE, MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK}
)

#: 区間の run の事後検査（区間の開始記録と照合する P4 と、今のままの P5。D13 §4.7 の 2）。
_SEGMENT_POST_RUN_CHECKS: Final = ("run_matches_preregistration", "evaluation_rule_matches")


def _policy_result_type() -> type:
    """`PolicyCheckResult`（`domain.research_policy` は `domain.search` を import し、`search` は
    本モジュールを import するので、循環を避けて使う時点で引く）。"""
    from odyssey_fx.evaluation.domain.research_policy import PolicyCheckResult

    return PolicyCheckResult


def _require_index(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise KernelValueError(f"{label} must be an int >= 0, got {value!r}")
    return value


class PathEnd(Enum):
    """経路の終端（D13 §4.6 の表 2 の終端の状態。仮置き 16・29、実装 PR の仮置き 50）。"""

    #: 全区間を走り終えた。
    COMPLETED = "COMPLETED"
    #: 区間の run が失敗した（判定できない。後ろの区間は走らせない。仮置き 16）。
    INCOMPLETE = "INCOMPLETE"
    #: 区間の終わりの費用を引いた後の残高が 0 以下（資金喪失。仮置き 29）。
    CAPITAL_LOST = "CAPITAL_LOST"


@dataclass(frozen=True, slots=True)
class PathSegmentKey:
    """経路の区間の run を指す鍵 `(variant, segment_index)`（D13 §4.5、仮置き 19）。"""

    variant: Variant
    segment_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.variant, Variant):
            raise KernelValueError("PathSegmentKey.variant must be a Variant")
        _require_index(self.segment_index, "PathSegmentKey.segment_index")


@dataclass(frozen=True, slots=True)
class PlannedSegment:
    """経路の区間1つ（D13 §4.5 の「経路の区間」）。

    `trial_index` は区間の fold に選んだ試行（候補なしの区間は `None`）。`interval` は区間に
    入る fold の検証区間をつないだ 1 本の区間（検証区間は互いに接する。U2）。
    """

    segment_index: int
    fold_indices: tuple[int, ...]
    trial_index: int | None
    interval: Interval

    def __post_init__(self) -> None:
        _require_index(self.segment_index, "PlannedSegment.segment_index")
        if (
            not isinstance(self.fold_indices, tuple)
            or not self.fold_indices
            or list(self.fold_indices)
            != list(range(self.fold_indices[0], self.fold_indices[0] + len(self.fold_indices)))
        ):
            raise KernelValueError("PlannedSegment.fold_indices は連続した fold の番号")
        if self.trial_index is None:
            if len(self.fold_indices) != 1:
                raise KernelValueError("候補なしの fold は単独の区間（D13 §4.5。仮置き 15）")
        else:
            _require_index(self.trial_index, "PlannedSegment.trial_index")
        if not isinstance(self.interval, Interval):
            raise KernelValueError("PlannedSegment.interval must be an Interval")

    @property
    def carried_boundaries(self) -> int:
        """区間の中の fold の境目の数（同じ試行が続いて建玉を持ち越した境目）。"""
        return len(self.fold_indices) - 1


def plan_segments(
    folds: Sequence[Fold], selected: Sequence[int | None]
) -> tuple[PlannedSegment, ...]:
    """全 fold の選定記録から経路の区間の列を決める（D13 §4.5・§4.6、仮置き 11・14・15）。

    `selected[k]` は fold k の選定記録の `selected_trial_index`。隣り合う fold の値が等しい間は
    1 つの区間、候補なし（`None`）の fold は単独の区間にする。候補なしを挟んで同じ試行が再び
    選ばれても区間はつながらない（仮置き 15）。
    """
    items = tuple(folds)
    chosen = tuple(selected)
    if len(items) != len(chosen) or not items:
        raise KernelValueError("plan_segments: fold と選定記録は同じ数で 1 つ以上")
    if [fold.fold_index for fold in items] != list(range(len(items))):
        raise KernelValueError("plan_segments: fold は 0 からの連番")
    groups: list[list[int]] = []
    for index, trial in enumerate(chosen):
        if groups and trial is not None and chosen[groups[-1][-1]] == trial:
            groups[-1].append(index)
        else:
            groups.append([index])
    segments: list[PlannedSegment] = []
    for number, group in enumerate(groups):
        first, last = items[group[0]], items[group[-1]]
        for previous, current in zip(group, group[1:], strict=False):
            if items[previous].validation.end != items[current].validation.start:
                raise KernelValueError(
                    "plan_segments: 同じ区間の fold の検証区間が接していない（D13 §2.4 の U2）"
                )
        segments.append(
            PlannedSegment(
                segment_index=number,
                fold_indices=tuple(group),
                trial_index=chosen[group[0]],
                interval=Interval(start=first.validation.start, end=last.validation.end),
            )
        )
    return tuple(segments)


@dataclass(frozen=True, slots=True)
class PathSegmentStart:
    """区間の開始記録（D13 §4.7 の 1。仮置き 25、実装 PR の仮置き 48・50）。

    区間の run を始める直前に書き、書き換えない。事後検査 P4 の照合先・既存の成果物の確認・
    再現の照合先である。
    """

    experiment_id: ExperimentId
    key: PathSegmentKey
    fold_indices: tuple[int, ...]
    trial_index: int
    compiled_ref: CompiledStrategyRef
    interval: Interval
    initial_balance: Money
    run_end: RunEndMode
    config_digest: ConfigDigest
    expected_run_id: RunId

    def __post_init__(self) -> None:
        for label, expected in (
            ("experiment_id", ExperimentId),
            ("key", PathSegmentKey),
            ("compiled_ref", CompiledStrategyRef),
            ("interval", Interval),
            ("initial_balance", Money),
            ("run_end", RunEndMode),
            ("config_digest", ConfigDigest),
            ("expected_run_id", RunId),
        ):
            if not isinstance(getattr(self, label), expected):
                raise KernelValueError(f"PathSegmentStart.{label} must be a {expected.__name__}")
        if not isinstance(self.fold_indices, tuple) or not self.fold_indices:
            raise KernelValueError("PathSegmentStart.fold_indices must be a non-empty tuple")
        for item in self.fold_indices:
            _require_index(item, "PathSegmentStart.fold_indices")
        _require_index(self.trial_index, "PathSegmentStart.trial_index")
        if self.run_end is not RunEndMode.CLOSE_ALL:
            raise KernelValueError("区間の run の終わり方は CLOSE_ALL（D13 §4.5。仮置き 22）")
        if self.initial_balance.amount <= 0:
            raise KernelValueError(
                "区間の初期残高は正（0 以下なら経路は資金喪失で終わる。D13 §4.5。仮置き 29）"
            )


@dataclass(frozen=True, slots=True)
class PathSegmentEnd:
    """区間の終端の記録（D13 §4.5 の「記録」、§4.7。実装 PR の仮置き 48・50）。

    区間の run が終端したら（再利用を含む）、開始記録の隣に書き、書き換えない。run が正常完走
    しなかった・評価が `COMPLETED` でない・末尾の3集計が無いときは、残高と仮決済の項目は `None`
    （区間の失敗。仮置き 16）。

    - `final_balance` / `final_equity`: 台帳の最後の残高と、最後の時価の資産（末尾の3集計）。
    - `settlement_lines`: 区間の終わりの全決済の明細（方式 (ii)。建玉ごと。仮置き 45）。
    - `settled_equity`: 区間の終わりの**費用を引いた後の資産**（= 残高 + 仮に閉じた損益）。経路の
      資産の系列の最後の点で、次の区間に渡す残高（仮置き 28）。
    - `trade_count`: 区間の run の完了取引の件数（区間の終わりの決済は含まない）。
    - `outcome_checks`: 事後検査 P4（開始記録と照合）・P5 の 2 件。
    """

    experiment_id: ExperimentId
    key: PathSegmentKey
    expected_run_id: RunId
    run_id: RunId
    run_status: RunStatus
    run_reused: bool
    run_evaluation_id: ContentDigest | None
    evaluation_status: EvaluationStatus | None
    result_digest: ContentDigest | None
    final_balance: Money | None
    final_equity: Money | None
    settlement_lines: tuple[HypotheticalCloseLine, ...]
    settled_equity: Money | None
    trade_count: int | None
    outcome_checks: tuple[PolicyCheckResult, ...]

    def __post_init__(self) -> None:
        for label, expected in (
            ("experiment_id", ExperimentId),
            ("key", PathSegmentKey),
            ("expected_run_id", RunId),
            ("run_id", RunId),
            ("run_status", RunStatus),
        ):
            if not isinstance(getattr(self, label), expected):
                raise KernelValueError(f"PathSegmentEnd.{label} must be a {expected.__name__}")
        if not isinstance(self.run_reused, bool):
            raise KernelValueError("PathSegmentEnd.run_reused must be a bool")
        for label, optional in (
            ("run_evaluation_id", ContentDigest),
            ("evaluation_status", EvaluationStatus),
            ("result_digest", ContentDigest),
            ("final_balance", Money),
            ("final_equity", Money),
            ("settled_equity", Money),
        ):
            value = getattr(self, label)
            if value is not None and not isinstance(value, optional):
                raise KernelValueError(f"PathSegmentEnd.{label} must be a {optional.__name__}")
        if not isinstance(self.settlement_lines, tuple) or not all(
            isinstance(item, HypotheticalCloseLine) for item in self.settlement_lines
        ):
            raise KernelValueError("PathSegmentEnd.settlement_lines must be HypotheticalCloseLine")
        if self.trade_count is not None:
            _require_index(self.trade_count, "PathSegmentEnd.trade_count")
        settled = (self.final_balance, self.final_equity, self.settled_equity, self.trade_count)
        if any(item is None for item in settled) and not all(item is None for item in settled):
            raise KernelValueError(
                "PathSegmentEnd: 残高・時価の資産・費用を引いた後の資産・取引件数はそろって値を"
                "持つかそろって None"
            )
        if self.settled_equity is None and self.settlement_lines:
            raise KernelValueError("PathSegmentEnd: 決済の明細は費用を引いた後の資産と一緒に持つ")
        result_type = _policy_result_type()
        checks = self.outcome_checks
        if not isinstance(checks, tuple) or not all(isinstance(c, result_type) for c in checks):
            raise KernelValueError("PathSegmentEnd.outcome_checks must be PolicyCheckResult")
        if tuple(item.check.value for item in checks) != _SEGMENT_POST_RUN_CHECKS:
            raise KernelValueError(
                "PathSegmentEnd.outcome_checks は区間の run の事後検査 P4・P5 の2件をこの順に持つ"
                "（D13 §4.7 の 2）"
            )

    @property
    def post_run_checks_passed(self) -> bool:
        """事後検査 P4・P5 がすべて合格か。"""
        return all(item.passed for item in self.outcome_checks)

    @property
    def succeeded(self) -> bool:
        """区間の run が成功したか（正常完走・評価 `COMPLETED`・P4・P5 合格・残高がある）。"""
        return (
            self.run_status is RunStatus.COMPLETED
            and self.evaluation_status is EvaluationStatus.COMPLETED
            and self.post_run_checks_passed
            and self.settled_equity is not None
        )

    @property
    def settlement_cost(self) -> Money | None:
        """区間の終わりの決済の費用（手数料と滑り。明細の和）。"""
        if self.settled_equity is None:
            return None
        currency = self.settled_equity.currency
        total = Money(decimal_from_int(0), currency)
        for line in self.settlement_lines:
            total = total + line.commission + line.slippage
        return total


@dataclass(frozen=True, slots=True)
class PathSegmentRow:
    """経路の要約の区間の行（D13 §4.6 の表 3。主キー `(experiment_id, execution, variant,
    segment_index)` の後ろの 2 つ）。

    候補なしの区間は `trial_index` と run の項目が `None` で、`end_balance` は渡された残高の
    まま（現金で保つ。仮置き 15）。区間の run が失敗した区間は `end_balance` が `None`。
    """

    segment_index: int
    fold_indices: tuple[int, ...]
    trial_index: int | None
    interval: Interval
    initial_balance: Money
    end_balance: Money | None
    carried_boundaries: int
    settlement_count: int
    settlement_cost: Money | None
    trade_count: int | None
    run_id: RunId | None
    run_reused: bool | None

    def __post_init__(self) -> None:
        _require_index(self.segment_index, "PathSegmentRow.segment_index")
        if not isinstance(self.fold_indices, tuple) or not self.fold_indices:
            raise KernelValueError("PathSegmentRow.fold_indices must be a non-empty tuple")
        if not isinstance(self.interval, Interval) or not isinstance(self.initial_balance, Money):
            raise KernelValueError("PathSegmentRow.interval / initial_balance の型が違う")
        _require_index(self.carried_boundaries, "PathSegmentRow.carried_boundaries")
        _require_index(self.settlement_count, "PathSegmentRow.settlement_count")
        if self.trial_index is None and (self.run_id is not None or self.run_reused is not None):
            raise KernelValueError("候補なしの区間は run を持たない（D13 §4.6 の表 2 の E3）")


@dataclass(frozen=True, slots=True)
class PathSummary:
    """組ごとの経路の要約（D13 §4.6 の表 3、D09 §10.6 の 2026-10-21 改訂）。

    - `end`: 経路の終端（完了・判定できない・資金喪失）。`end_segment` は判定できない・資金喪失で
      終えた区間の番号（完了は `None`）、`end_reason` はその理由の文。
    - `metrics`: 経路の指標（`PATH_METRICS` の 2 件を `MetricId` の宣言順に）。判定できないで
      終えた経路は空。
    - `trade_count`: 経路の取引件数（区間の和。診断だけで、証拠の要件には使わない。D13-20 (a)）。
    - `final_balance`: 経路の最後の残高（判定できないで終えた経路は `None`）。
    """

    segments: tuple[PathSegmentRow, ...]
    end: PathEnd
    end_segment: int | None
    end_reason: str | None
    metrics: tuple[tuple[MetricId, MetricValue], ...]
    trade_count: int | None
    final_balance: Money | None

    def __post_init__(self) -> None:
        if not isinstance(self.segments, tuple) or not all(
            isinstance(item, PathSegmentRow) for item in self.segments
        ):
            raise KernelValueError("PathSummary.segments must be a tuple of PathSegmentRow")
        if [item.segment_index for item in self.segments] != list(range(len(self.segments))):
            raise KernelValueError("PathSummary.segments は区間の番号 0 からの連番")
        if not isinstance(self.end, PathEnd):
            raise KernelValueError("PathSummary.end must be a PathEnd")
        if (self.end is PathEnd.COMPLETED) != (self.end_segment is None):
            raise KernelValueError("PathSummary.end_segment は完了でない経路だけが持つ")
        if (self.end is PathEnd.COMPLETED) != (self.end_reason is None):
            raise KernelValueError("PathSummary.end_reason は完了でない経路だけが持つ")
        if self.end is PathEnd.INCOMPLETE:
            if self.metrics or self.final_balance is not None or self.trade_count is not None:
                raise KernelValueError("判定できないで終えた経路は指標と最後の残高を持たない")
        elif [item[0] for item in self.metrics] != sorted(
            PATH_METRICS, key=lambda item: list(MetricId).index(item)
        ):
            raise KernelValueError("PathSummary.metrics は経路の指標 2 件を宣言順に持つ")

    def metric(self, metric: MetricId) -> MetricValue | None:
        """経路の指標の値（無ければ `None`）。"""
        for key, value in self.metrics:
            if key is metric:
                return value
        return None


def path_metrics(
    initial_balance: Money,
    series: Sequence[Decimal],
    final_balance: Money,
) -> tuple[tuple[MetricId, MetricValue], ...]:
    """経路の指標（D07 §5.6、D13 §3.2・§4.4）。

    - `series`: 経路の資産の系列（各区間の台帳の `equity` を区間の順につなぎ、各区間の最後に
      費用を引いた後の資産を置いたもの。仮置き 28）。最初に初期残高を置く（#5 と同じく最初の
      snapshot から数える。区間 0 の最初の snapshot は初期残高に等しい）。
    - 全体の純収益率 = 最後の残高 ÷ 最初の残高 − 1（費用はすべて残高に入っている）。
    - 全体の最大ドローダウン率 = 系列に #20 の式（最高値は区間の境目でリセットしない）。
    """
    if not isinstance(initial_balance, Money) or not isinstance(final_balance, Money):
        raise KernelValueError("path_metrics requires Money balances")
    if initial_balance.currency != final_balance.currency:
        raise KernelValueError("path_metrics: 最初と最後の残高の通貨が違う")
    if initial_balance.amount <= 0:
        net: MetricValue = Unavailable(
            MetricKind.RATIO, MetricUnavailableReason.UNDEFINED_DENOMINATOR
        )
    else:
        with localcontext(kernel_context()):
            profit = final_balance.amount - initial_balance.amount
        net = RatioValue(ratio_of(profit, initial_balance.amount))
    rate = max_drawdown_rate_peak([initial_balance.amount, *series])
    drawdown: MetricValue = (
        RatioValue(rate) if isinstance(rate, Decimal) else Unavailable(MetricKind.RATIO, rate)
    )
    values = {MetricId.NET_RETURN_RATE: net, MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK: drawdown}
    return tuple((metric, values[metric]) for metric in MetricId if metric in values)
