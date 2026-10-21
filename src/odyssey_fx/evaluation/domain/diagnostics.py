"""診断の表示の計算（D13 §5.3・§5.4、D09 §11.5 の順9 の 2026-10-21 改訂。判定に使わない）。

決定 D13-12 (a)・D13-13 (a)、仮置き 10、実装 PR の仮置き 58・59（2026-10-22 の人間の決定）。

- 年ごと・fold ごとの取引件数と純損益（年は UTC 暦年。取引は決済の時刻で年に入れる）。
- 利益の集中度（勝ち取引の利益の合計に占める上位 1 件・上位 3 件の割合と、最大の利益の 1 件・
  上位 3 件を除いた全体の純損益）。
- 時期の集中（UTC 暦月ごとの件数、最も件数の多い連続 12 か月に入る取引の割合、取引どうしの
  最長の間隔）。
- 保有期間（最小・中央値・最大、取引日の終わり（NY17）をまたいだ回数）。
- リスクリワード・勝率。
- 統計の診断: 1 件あたりの平均損益と標準誤差、片側 t 検定の t 値（p 値は出さない）、符号検定
  （勝ちの件数と、勝ち負けが半々という仮説の下で勝ちがその件数以上になる片側の正確な確率）。
  ブロック・ブートストラップは乱数と長さの選び方が要るので計算しない（仮置き 58）。試行数で
  割り引いたシャープレシオは順 6（D13-17 (a)）。

どれも実時計・乱数・ファイルを読まない純粋関数で、取引の損益・時刻と区間だけを入力にする。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from fractions import Fraction

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, kernel_context
from odyssey_fx.common.time import Interval, UtcTime

__all__ = [
    "Concentration",
    "HoldingSummary",
    "RewardSummary",
    "StatisticsSummary",
    "TimingSummary",
    "TradeFact",
    "concentration",
    "holding_summary",
    "reward_summary",
    "statistics_summary",
    "timing_summary",
    "yearly_counts",
]


@dataclass(frozen=True, slots=True)
class TradeFact:
    """診断の入力の取引 1 件（入場・決済の時刻と取引損益（入場費用込み。D07 §7.3））。"""

    entry_at: UtcTime
    exit_at: UtcTime
    profit: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.entry_at, UtcTime) or not isinstance(self.exit_at, UtcTime):
            raise KernelValueError("TradeFact times must be UtcTime")
        if self.exit_at < self.entry_at:
            raise KernelValueError("TradeFact.exit_at must not precede entry_at")
        if not isinstance(self.profit, Decimal) or not self.profit.is_finite():
            raise KernelValueError("TradeFact.profit must be a finite Decimal")


def _year(moment: UtcTime) -> int:
    return int(str(moment)[:4])


def _month(moment: UtcTime) -> tuple[int, int]:
    text = str(moment)
    return int(text[:4]), int(text[5:7])


def yearly_counts(
    trades: Sequence[TradeFact], interval: Interval
) -> tuple[tuple[int, int, Decimal], ...]:
    """区間にかかる UTC 暦年ごとの `(年, 件数, 純損益)`（0 件の年も出す。決済の時刻で数える）。"""
    first, last = _year(interval.start), _year(interval.end)
    counts: Counter[int] = Counter()
    sums: dict[int, Decimal] = {}
    for trade in trades:
        year = _year(trade.exit_at)
        counts[year] += 1
        sums[year] = sums.get(year, decimal_from_int(0)) + trade.profit
    years = range(
        min(first, *counts) if counts else first, max(last, *counts) + 1 if counts else last + 1
    )
    return tuple((year, counts[year], sums.get(year, decimal_from_int(0))) for year in years)


@dataclass(frozen=True, slots=True)
class Concentration:
    """利益の集中度（`None` は勝ち取引が無いなど、割合が定まらないこと）。"""

    winning_total: Decimal
    top1_share: Decimal | None
    top3_share: Decimal | None
    net_without_top1: Decimal
    net_without_top3: Decimal


def concentration(trades: Sequence[TradeFact]) -> Concentration:
    """勝ち取引の利益の合計に占める上位 1 件・3 件の割合と、上位を除いた純損益（D13 §5.4）。"""
    profits = sorted((trade.profit for trade in trades), reverse=True)
    total = sum(profits, decimal_from_int(0))
    winners = [value for value in profits if value > 0]
    winning_total = sum(winners, decimal_from_int(0))

    def share(count: int) -> Decimal | None:
        if winning_total <= 0:
            return None
        with localcontext(kernel_context()):
            return sum(winners[:count], decimal_from_int(0)) / winning_total

    with localcontext(kernel_context()):
        without1 = total - sum(winners[:1], decimal_from_int(0))
        without3 = total - sum(winners[:3], decimal_from_int(0))
    return Concentration(
        winning_total=winning_total,
        top1_share=share(1),
        top3_share=share(3),
        net_without_top1=without1,
        net_without_top3=without3,
    )


@dataclass(frozen=True, slots=True)
class TimingSummary:
    """時期の集中（UTC 暦月ごとの件数は 0 件の月を除く）。"""

    monthly: tuple[tuple[str, int], ...]
    max_12_months_share: Decimal | None
    longest_gap: timedelta | None


def timing_summary(trades: Sequence[TradeFact]) -> TimingSummary:
    """月ごとの件数（入場の月）、最も件数の多い連続 12 か月の割合、入場どうしの最長の間隔。"""
    if not trades:
        return TimingSummary(monthly=(), max_12_months_share=None, longest_gap=None)
    months = Counter(_month(trade.entry_at) for trade in trades)
    ordered = sorted(months)
    best = 0
    for year, month in ordered:
        start = year * 12 + month - 1
        window = sum(
            count for (y, m), count in months.items() if start <= y * 12 + m - 1 < start + 12
        )
        best = max(best, window)
    with localcontext(kernel_context()):
        share = decimal_from_int(best) / decimal_from_int(len(trades))
    entries = sorted(trade.entry_at for trade in trades)
    gaps = [later - earlier for earlier, later in zip(entries, entries[1:], strict=False)]
    return TimingSummary(
        monthly=tuple(
            (f"{year:04d}-{month:02d}", months[(year, month)]) for year, month in ordered
        ),
        max_12_months_share=share,
        longest_gap=max(gaps) if gaps else None,
    )


@dataclass(frozen=True, slots=True)
class HoldingSummary:
    """保有期間（完了取引の保有時間）と取引日の終わりをまたいだ回数（`None` は数えられない）。"""

    shortest: timedelta | None
    median: timedelta | None
    longest: timedelta | None
    day_end_crossings: int | None


def holding_summary(
    trades: Sequence[TradeFact], day_ends: Sequence[UtcTime] | None
) -> HoldingSummary:
    """保有時間の最小・中央値・最大と、`(入場, 決済]` に入る取引日の終わりの数の合計。

    中央値は偶数個なら中央の 2 つの平均（マイクロ秒の整数で切り捨て）。`day_ends` が `None`
    （取引カレンダーが無い・取引日の境界が決まらない）なら回数は `None`。
    """
    if not trades:
        return HoldingSummary(None, None, None, 0 if day_ends is not None else None)
    spans = sorted(trade.exit_at - trade.entry_at for trade in trades)
    middle = len(spans) // 2
    median = spans[middle] if len(spans) % 2 else (spans[middle - 1] + spans[middle]) / 2
    crossings: int | None = None
    if day_ends is not None:
        crossings = sum(
            1 for trade in trades for end in day_ends if trade.entry_at < end <= trade.exit_at
        )
    return HoldingSummary(spans[0], median, spans[-1], crossings)


@dataclass(frozen=True, slots=True)
class RewardSummary:
    """リスクリワード（平均利益 ÷ 平均損失の絶対値）と勝率（`None` は定まらない）。"""

    reward_ratio: Decimal | None
    win_rate: Decimal | None


def reward_summary(trades: Sequence[TradeFact]) -> RewardSummary:
    """勝ちは取引損益 > 0、負けは < 0（0 はどちらにも数えない。D07 §7.3 の勝敗と同じ符号）。"""
    wins = [trade.profit for trade in trades if trade.profit > 0]
    losses = [-trade.profit for trade in trades if trade.profit < 0]
    with localcontext(kernel_context()):
        ratio = (
            (sum(wins, decimal_from_int(0)) / decimal_from_int(len(wins)))
            / (sum(losses, decimal_from_int(0)) / decimal_from_int(len(losses)))
            if wins and losses
            else None
        )
        rate = decimal_from_int(len(wins)) / decimal_from_int(len(trades)) if trades else None
    return RewardSummary(reward_ratio=ratio, win_rate=rate)


@dataclass(frozen=True, slots=True)
class StatisticsSummary:
    """統計の診断（D13 §5.3。判定に使わない。`None` は件数が足りず定まらない）。"""

    count: int
    mean: Decimal | None
    standard_error: Decimal | None
    t_value: Decimal | None
    wins: int
    sign_test_p: Decimal | None


def statistics_summary(trades: Sequence[TradeFact]) -> StatisticsSummary:
    """1 件あたりの平均損益・標準誤差（標本標準偏差 ÷ √N）・t 値と、符号検定の片側の確率。

    符号検定は勝ち（取引損益 > 0）と負け（< 0）だけを数え（0 は除く）、勝ちの確率 1/2 の二項
    分布で「勝ちが観測の件数以上」の確率を正確に（分数で）求めてから十進数にする。
    """
    values = [trade.profit for trade in trades]
    count = len(values)
    wins = sum(1 for value in values if value > 0)
    losses = sum(1 for value in values if value < 0)
    mean = se = t_value = None
    with localcontext(kernel_context()):
        if count:
            mean = sum(values, decimal_from_int(0)) / decimal_from_int(count)
        if count >= 2 and mean is not None:
            variance = sum(((value - mean) ** 2 for value in values), decimal_from_int(0)) / (
                decimal_from_int(count - 1)
            )
            sd = variance.sqrt()
            se = sd / decimal_from_int(count).sqrt()
            t_value = mean / se if se != 0 else None
        decided = wins + losses
        p_value: Decimal | None = None
        if decided:
            tail = sum(
                (Fraction(_binomial(decided, k), 2**decided) for k in range(wins, decided + 1)),
                Fraction(0),
            )
            p_value = decimal_from_int(tail.numerator) / decimal_from_int(tail.denominator)
    return StatisticsSummary(
        count=count,
        mean=mean,
        standard_error=se,
        t_value=t_value,
        wins=wins,
        sign_test_p=p_value,
    )


def _binomial(n: int, k: int) -> int:
    result = 1
    for index in range(1, k + 1):
        result = result * (n - k + index) // index
    return result
