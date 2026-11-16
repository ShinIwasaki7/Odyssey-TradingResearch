"""局面の語彙・第 5 版の局面の値の型・局面の付与（D24 §2.1・§5、決定 131・132・134・141）。

局面は日足だけから機械的に付ける相場の区分（向き 3 区分 × 値動きの大きさ 2 区分 = 6）で、
**表示・分析用だけ**である（決定 130・134・135。判定・選定・戦略に使わない）。本モジュールは、
実装 PR 4-1 の語彙と値の型に、実装 PR 4-3 の区分の関数・参照時点の局面・損益の振り分けと局面の
成果物の型を足したもの（D24 §5・§10.1）。日足の読み出し（ポート）は `evaluation.application`、
成果物の保存は `evaluation.adapters` が持つ。

- `RegimeLabel`: 6 局面と、付けられないときの `UNLABELED`（D24 の仮置き 13）。
- `RegimeSpec`: 研究ポリシーの `evaluation_standard.regimes` の値（D24 §2.1）。読込時の検査 E10
  （D24 §8.1）を構築時に当てる。役割の語彙は `DISPLAY_ONLY` の 1 つだけで、判定に使う値を書けない。
  局面をどの条件・選定からも参照する書き方は、研究ポリシーの語彙に無い（型として渡せない）。
- `favored_regimes_of`: 実験設定の任意のキー `favored_regimes`（仮説カードの「得意な局面」の宣言。
  決定 141、D24 の仮置き 16）の検査 E12。判定に使わない。
- `label_daily_bars`（実装 PR 4-3）: 日足の列から日足ごとの局面を付ける十進数の純粋関数（D24 §5.1、
  仮置き 12）。EMA・ATR・中央値を `Decimal` で求め、`float` を使わない（D07 §5.5）。
- `RegimeLookup`: 参照時点 `t` に**公開済み**（公開時刻 `available_at` が `t` 以前）の最後の日足の
  局面を引く（D24 §5.1。D03 §6.2 の as-of の規則）。
- `build_regime_report`: 経路の取引を入場の時点の局面に振り分け、局面ごとの損益の表・時間の割合・
  得意な局面の確かめを組み、局面の損益の和と経路の純損益の整合を確かめる（D24 §5.3・§5.4、仮置き
  14・15）。整合しなければ表を出さず理由を残す（表示は誤りを黙らない）。

`evaluation.domain` に置く（決定 164（D24-7 (a)）。戦略（`strategy.*`）は `evaluation` を import
できないので、局面を戦略の入力に使う経路が構造的に無い。D01 §3.2）。判定の関数（`domain.search`）は
局面の結果の型（`DailyRegime`・`RegimeReport` など）を入力に取らない（D24 の仮置き 15。RP5-16）。
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from enum import Enum
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import PositionId
from odyssey_fx.common.money import Money, decimal_from_int, kernel_context
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.metrics import (
    MetricUnavailableReason,
    average_win_loss_ratio,
    ratio_of,
)
from odyssey_fx.evaluation.domain.path import PathTradeKind
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import ClosureRule, TradingCalendar

__all__ = [
    "LABELED_REGIMES",
    "REGIME_REPORT_SCHEMA_VERSION",
    "REGIME_TABLE_ORDER",
    "DailyRegime",
    "FavoredRegimeCheck",
    "RegimeAt",
    "RegimeDay",
    "RegimeDayRow",
    "RegimeDirectionRule",
    "RegimeLabel",
    "RegimeLabelAt",
    "RegimeLookup",
    "RegimeReport",
    "RegimeReportStatus",
    "RegimeRole",
    "RegimeSpec",
    "RegimeTableRow",
    "RegimeTimeframe",
    "RegimeTrade",
    "RegimeTradeRow",
    "RegimeVolatilityRule",
    "UnlabeledReason",
    "build_regime_report",
    "favored_regimes_of",
    "label_daily_bars",
    "trading_day_spans",
    "unavailable_regime_report",
]


class RegimeLabel(Enum):
    """局面の名前（D24 §5.1、仮置き 13）。向き × 値動きの組と、付けられないとき。"""

    UP_HIGH = "UP_HIGH"
    UP_LOW = "UP_LOW"
    FLAT_HIGH = "FLAT_HIGH"
    FLAT_LOW = "FLAT_LOW"
    DOWN_HIGH = "DOWN_HIGH"
    DOWN_LOW = "DOWN_LOW"
    #: 準備の日足が足りない日と ATR が 0 の日（D24 §5.1、仮置き 12）。
    UNLABELED = "UNLABELED"


#: 付けられた 6 局面（`UNLABELED` を除く。宣言順）。`favored_regimes` に書けるのはこの 6 つだけ。
LABELED_REGIMES: Final[tuple[RegimeLabel, ...]] = tuple(
    label for label in RegimeLabel if label is not RegimeLabel.UNLABELED
)


class RegimeRole(Enum):
    """局面の役割（D24 §2.1。語彙はこの 1 つ。判定に使う値を許さない。決定 134）。"""

    DISPLAY_ONLY = "DISPLAY_ONLY"


class RegimeTimeframe(Enum):
    """局面を付ける足（D24 §2.1。ニューヨーク 17 時の日足だけ）。"""

    DAILY = "1d"


class RegimeLabelAt(Enum):
    """局面を付ける時点（D24 §2.1・§5.1。D23 の仮置き 5、D03 §6.2）。

    取引の入場の時点に**公開済み**（公開時刻 `available_at` が入場の時点以前）の最後の日足の局面。
    """

    LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY = "LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY"


def _require_bars(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise KernelValueError(
            f"{label} は正の整数の本数で書くこと（D24 §8.1 の検査 E10）: {value!r}"
        )


@dataclass(frozen=True, slots=True)
class RegimeDirectionRule:
    """向きの軸の値（D24 §2.1・§5.1。決定 132 の値はファイルが持つ）。

    `g_t = (E_t − E_{t−slope_bars}) ÷ A_t`（`E` は終値の EMA `ema_bars` 本、`A` は ATR `atr_bars`
    本）を `±dead_band_atr_multiple` と比べる。
    """

    ema_bars: int
    slope_bars: int
    atr_bars: int
    dead_band_atr_multiple: Decimal

    def __post_init__(self) -> None:
        for name in ("ema_bars", "slope_bars", "atr_bars"):
            _require_bars(getattr(self, name), f"regimes.direction.{name}")
        value = self.dead_band_atr_multiple
        if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
            raise KernelValueError(
                "regimes.direction.dead_band_atr_multiple は 0 以上の十進数で書くこと"
                f"（D24 §8.1 の検査 E10）: {value!r}"
            )


@dataclass(frozen=True, slots=True)
class RegimeVolatilityRule:
    """値動きの軸の値（D24 §2.1・§5.1）。`v_t = A_t ÷ C_t` をその日を含む直前
    `median_window_bars` 個の `v` の中央値と比べる（`A` は ATR `atr_bars` 本）。"""

    atr_bars: int
    median_window_bars: int

    def __post_init__(self) -> None:
        for name in ("atr_bars", "median_window_bars"):
            _require_bars(getattr(self, name), f"regimes.volatility.{name}")


@dataclass(frozen=True, slots=True)
class RegimeSpec:
    """研究ポリシーの `evaluation_standard.regimes`（D24 §2.1。版 5 以上で必須）。

    読込時の検査 E10（D24 §8.1）: `role` は `DISPLAY_ONLY` だけ、本数は正の整数、不感帯は 0 以上、
    `timeframe` と `label_at` は語彙の 1 値。語彙の外の値は列挙型の構築で読込が拒否する。
    """

    role: RegimeRole
    timeframe: RegimeTimeframe
    direction: RegimeDirectionRule
    volatility: RegimeVolatilityRule
    label_at: RegimeLabelAt

    def __post_init__(self) -> None:
        for name, expected in (
            ("role", RegimeRole),
            ("timeframe", RegimeTimeframe),
            ("direction", RegimeDirectionRule),
            ("volatility", RegimeVolatilityRule),
            ("label_at", RegimeLabelAt),
        ):
            if not isinstance(getattr(self, name), expected):
                raise KernelValueError(
                    f"regimes.{name} must be a {expected.__name__}（D24 §8.1 の検査 E10）"
                )


def favored_regimes_of(names: Sequence[str], label: str) -> tuple[RegimeLabel, ...]:
    """実験設定の `favored_regimes` を局面の名前の列にする（D24 §8.1 の検査 E12、仮置き 16）。

    局面の名前（`UNLABELED` を除く 6 つ）の重複の無い、空でない列だけを受ける。書いた順を保つ。
    違反は `KernelValueError`（読込が設定の誤りに言い換える）。
    """
    if isinstance(names, str) or not isinstance(names, Sequence):
        raise KernelValueError(f"{label} は局面の名前の列で書くこと（D24 §8.1 の検査 E12）")
    allowed = {item.value: item for item in LABELED_REGIMES}
    found: list[RegimeLabel] = []
    for index, name in enumerate(names):
        member = allowed.get(name) if isinstance(name, str) else None
        if member is None:
            raise KernelValueError(
                f"{label}[{index}] が {name!r} だが、書けるのは局面の名前"
                f" {[item.value for item in LABELED_REGIMES]} だけ（`UNLABELED` は書けない。"
                "D24 §8.1 の検査 E12）"
            )
        if member in found:
            raise KernelValueError(
                f"{label} に {name} が 2 回ある。局面の名前は重複させない（D24 §8.1 の検査 E12）"
            )
        found.append(member)
    if not found:
        raise KernelValueError(
            f"{label} が空である。宣言しないときはキーごと書かない（D24 §8.1 の検査 E12）"
        )
    return tuple(found)


# --- 区分の関数（実装 PR 4-3。D24 §5.1、仮置き 12）-----------------------------------


class UnlabeledReason(Enum):
    """`UNLABELED` の理由（D24 §5.1・§9.2 の表の「該当の日・取引を UNLABELED（理由つき）」）。"""

    #: 準備の日足が足りない（EMA の初期値・傾き・ATR・値動きの中央値の窓のどれかが埋まらない）。
    WARMUP_INSUFFICIENT = "WARMUP_INSUFFICIENT"
    #: その日足の ATR が 0（向きの比率が定まらない。値動きも 0 になる）。
    ZERO_ATR = "ZERO_ATR"
    #: 参照時点に公開済みの日足が 1 本も無い。
    NO_PUBLISHED_BAR = "NO_PUBLISHED_BAR"


def _require_time(value: object, label: str) -> None:
    if not isinstance(value, UtcTime):
        raise KernelValueError(f"{label} must be a UtcTime")


def _require_label(label: object, reason: object, name: str) -> None:
    if not isinstance(label, RegimeLabel):
        raise KernelValueError(f"{name}.label must be a RegimeLabel")
    if reason is not None and not isinstance(reason, UnlabeledReason):
        raise KernelValueError(f"{name}.reason must be an UnlabeledReason or None")
    if (label is RegimeLabel.UNLABELED) != (reason is not None):
        raise KernelValueError(f"{name}: `UNLABELED` だけが理由を持ち、理由は必ず持つ（D24 §5.1）")


@dataclass(frozen=True, slots=True)
class DailyRegime:
    """日足 1 本の局面（D24 §5.1）。その日足とそれより前の日足だけから決まる。

    `available_at` は、その実験の遅延シナリオを当てた日足の公開時刻（D24 §5.2。ポートが返す値）。
    """

    bar_start: UtcTime
    available_at: UtcTime
    label: RegimeLabel
    reason: UnlabeledReason | None

    def __post_init__(self) -> None:
        _require_time(self.bar_start, "DailyRegime.bar_start")
        _require_time(self.available_at, "DailyRegime.available_at")
        _require_label(self.label, self.reason, "DailyRegime")


def _true_ranges(bars: Sequence[Bar]) -> list[Decimal]:
    """真の値幅（最初の足は高値 − 安値。以後は前の終値との差を含めた最大。仮置き 12）。"""
    ranges: list[Decimal] = []
    with localcontext(kernel_context()):
        for index, bar in enumerate(bars):
            high = bar.high.value
            low = bar.low.value
            width = high - low
            if index > 0:
                previous = bars[index - 1].close.value
                width = max(width, abs(high - previous), abs(low - previous))
            ranges.append(width)
    return ranges


def _averages(values: Sequence[Decimal], window: int) -> list[Decimal | None]:
    """窓 `window` 本の単純平均（窓が埋まらない位置は `None`）。合計 ÷ 本数の順に 1 回だけ割る。"""
    found: list[Decimal | None] = []
    divisor = decimal_from_int(window)
    with localcontext(kernel_context()):
        for index in range(len(values)):
            if index + 1 < window:
                found.append(None)
                continue
            total = decimal_from_int(0)
            for value in values[index + 1 - window : index + 1]:
                total = total + value
            found.append(total / divisor)
    return found


def _ema(closes: Sequence[Decimal], window: int) -> list[Decimal | None]:
    """終値の EMA（最初の `window` 本の単純平均から始め、`E_t = E_{t−1} + α (C_t − E_{t−1})`、
    `α = 2 ÷ (window + 1)`。D24 の仮置き 12）。初期値が無い位置は `None`。"""
    found: list[Decimal | None] = []
    with localcontext(kernel_context()):
        alpha = decimal_from_int(2) / decimal_from_int(window + 1)
        current: Decimal | None = None
        for index, close in enumerate(closes):
            if index + 1 < window:
                found.append(None)
                continue
            if current is None:
                total = decimal_from_int(0)
                for value in closes[: index + 1]:
                    total = total + value
                current = total / decimal_from_int(window)
            else:
                current = current + alpha * (close - current)
            found.append(current)
    return found


def _median(values: Sequence[Decimal]) -> Decimal:
    """中央値（偶数個なら中央の 2 つの平均。D24 の仮置き 12）。"""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    with localcontext(kernel_context()):
        return (ordered[middle - 1] + ordered[middle]) / decimal_from_int(2)


def label_daily_bars(bars: Sequence[Bar], spec: RegimeSpec) -> tuple[DailyRegime, ...]:
    """日足の列に局面を付ける（D24 §5.1、仮置き 12・13。十進数の純粋関数）。

    `bars` は 1 つの系列の日足を `bar_start` の昇順に並べたもの（重複なし。`available_at` はその
    実験の遅延シナリオを当てた公開時刻）。各日足の局面は、その日足とそれより前の日足だけから
    決まる（後の日足を読まない）。日足の本数は実在する日足で数える（足の欠けた日を詰める。
    本 PR の仮置き）。

    - 向き: `g_t = (E_t − E_{t−s}) ÷ A_t`（`E` は終値の EMA `ema_bars` 本、`s` は `slope_bars`、
      `A` は ATR `direction.atr_bars` 本）。`g_t ≥ +不感帯` で上昇、`g_t ≤ −不感帯` で下降、その間で
      横ばい。
    - 値動き: `v_t = A'_t ÷ C_t`（`A'` は ATR `volatility.atr_bars` 本）を、その日足を含む直前
      `median_window_bars` 個の `v` の中央値と比べ、上なら大、以下なら小。
    - 準備の日足が足りない日足は `UNLABELED`（`WARMUP_INSUFFICIENT`）、準備が足りて ATR が 0 の
      日足は `UNLABELED`（`ZERO_ATR`）。
    """
    if not isinstance(spec, RegimeSpec):
        raise KernelValueError("label_daily_bars requires a RegimeSpec")
    items = tuple(bars)
    if not all(isinstance(bar, Bar) for bar in items):
        raise KernelValueError("label_daily_bars requires Bar values")
    for previous, bar in zip(items, items[1:], strict=False):
        if bar.series != previous.series:
            raise KernelValueError("label_daily_bars: 日足は 1 つの系列だけを渡す（D24 §5.2）")
        if not previous.bar_start < bar.bar_start:
            raise KernelValueError(
                "label_daily_bars: 日足は bar_start の昇順で重複なく渡す（D24 §5.1）"
            )
    direction = spec.direction
    volatility = spec.volatility
    closes = [bar.close.value for bar in items]
    ranges = _true_ranges(items)
    ema = _ema(closes, direction.ema_bars)
    direction_atr = _averages(ranges, direction.atr_bars)
    volatility_atr = _averages(ranges, volatility.atr_bars)
    moves: list[Decimal | None] = []
    with localcontext(kernel_context()):
        for index, atr in enumerate(volatility_atr):
            moves.append(None if atr is None else atr / closes[index])
    band = direction.dead_band_atr_multiple
    slope = direction.slope_bars
    window = volatility.median_window_bars
    found: list[DailyRegime] = []
    for index, bar in enumerate(items):
        label: RegimeLabel = RegimeLabel.UNLABELED
        reason: UnlabeledReason | None = UnlabeledReason.WARMUP_INSUFFICIENT
        current = ema[index]
        before = ema[index - slope] if index >= slope else None
        atr = direction_atr[index]
        recent = moves[index + 1 - window : index + 1] if index + 1 >= window else []
        move = moves[index]
        if (
            current is not None
            and before is not None
            and atr is not None
            and move is not None
            and len(recent) == window
            and all(item is not None for item in recent)
        ):
            vol_atr = volatility_atr[index]
            if atr == 0 or vol_atr == 0:
                reason = UnlabeledReason.ZERO_ATR
            else:
                with localcontext(kernel_context()):
                    gradient = (current - before) / atr
                    negative_band = -band
                median = _median([item for item in recent if item is not None])
                if gradient >= band:
                    trend = "UP"
                elif gradient <= negative_band:
                    trend = "DOWN"
                else:
                    trend = "FLAT"
                size = "HIGH" if move > median else "LOW"
                label = RegimeLabel(f"{trend}_{size}")
                reason = None
        found.append(
            DailyRegime(
                bar_start=bar.bar_start,
                available_at=bar.available_at,
                label=label,
                reason=reason,
            )
        )
    return tuple(found)


@dataclass(frozen=True, slots=True)
class RegimeAt:
    """参照時点の局面（D24 §5.1）。`daily_bar_start` は使った日足（無ければ `None`）。"""

    label: RegimeLabel
    reason: UnlabeledReason | None
    daily_bar_start: UtcTime | None

    def __post_init__(self) -> None:
        _require_label(self.label, self.reason, "RegimeAt")
        if self.daily_bar_start is not None:
            _require_time(self.daily_bar_start, "RegimeAt.daily_bar_start")
        if (self.daily_bar_start is None) != (self.reason is UnlabeledReason.NO_PUBLISHED_BAR):
            raise KernelValueError(
                "RegimeAt: 使った日足が無いのは、公開済みの日足が無いときだけ（D24 §5.1）"
            )


class RegimeLookup:
    """参照時点に公開済みの最後の日足の局面を引く（D24 §5.1。D03 §6.2 の as-of の規則）。

    「公開済みの最後の日足」は、**その日足とそれより前のすべての日足の公開時刻が参照時点以前**で
    ある最後の日足とする（遅延シナリオで公開の順が前後しても、まだ公開されていない日足の値から
    作った局面を使わない。本 PR の仮置き）。公開の順が足の順どおりなら、`available_at ≤ t` の
    最後の日足（`latest_available` と同じ足）と一致する。足の終わり `bar_end` では選ばない
    （`bar_end ≤ t < available_at` の日足は使わず、1 本前の公開済みの日足で付ける。RP5-13）。
    """

    __slots__ = ("_daily", "_published")

    def __init__(self, daily: Sequence[DailyRegime]) -> None:
        items = tuple(daily)
        if not all(isinstance(item, DailyRegime) for item in items):
            raise KernelValueError("RegimeLookup requires DailyRegime values")
        for previous, item in zip(items, items[1:], strict=False):
            if not previous.bar_start < item.bar_start:
                raise KernelValueError("RegimeLookup: 日足の局面は bar_start の昇順で重複なく渡す")
        published: list[UtcTime] = []
        for item in items:
            latest = item.available_at
            if published and published[-1] > latest:
                latest = published[-1]
            published.append(latest)
        self._daily = items
        self._published = tuple(published)

    def at(self, moment: UtcTime) -> RegimeAt:
        """`moment` に公開済みの最後の日足の局面（無ければ `UNLABELED`・`NO_PUBLISHED_BAR`）。"""
        _require_time(moment, "RegimeLookup.at")
        position = bisect_right(self._published, moment) - 1
        if position < 0:
            return RegimeAt(
                label=RegimeLabel.UNLABELED,
                reason=UnlabeledReason.NO_PUBLISHED_BAR,
                daily_bar_start=None,
            )
        item = self._daily[position]
        return RegimeAt(label=item.label, reason=item.reason, daily_bar_start=item.bar_start)


def trading_day_spans(calendar: TradingCalendar, interval: Interval) -> tuple[Interval, ...] | None:
    """経路の取引日の区間の列（D24 §5.4 の「局面の時間の割合」）。

    数える取引日は `metrics.trading_day_ends` と同じ（終わりが `(interval.start, interval.end]` に
    あり、開場時間を持つ取引日。D07 §5.5）で、その取引日の区間（現地の取引日の境界から境界まで）を
    返す。取引日の境界が 1 つに決まらないカレンダーでは `None`。
    """
    if not isinstance(calendar, TradingCalendar):
        raise KernelValueError("trading_day_spans requires a TradingCalendar")
    if not isinstance(interval, Interval):
        raise KernelValueError("trading_day_spans requires an Interval")
    boundary = calendar.trading_day_boundary
    if boundary is None:
        return None
    first: date = interval.start.value.astimezone(calendar.tz).date()
    last: date = interval.end.value.astimezone(calendar.tz).date() + timedelta(days=1)
    spans: list[Interval] = []
    day = first
    while day <= last:
        span = ClosureRule(local_date=day, covers_trading_day=True).utc_interval(
            calendar.tz, trading_day_boundary=boundary
        )
        if interval.start < span.end <= interval.end and calendar.sessions(span):
            spans.append(span)
        day = day + timedelta(days=1)
    return tuple(spans)


# --- 損益の振り分けと局面の成果物（実装 PR 4-3。D24 §5.3・§5.4、仮置き 14・15）-----------


#: 局面の表の行の順（6 局面の宣言順 → `UNLABELED`）。
REGIME_TABLE_ORDER: Final[tuple[RegimeLabel, ...]] = (*LABELED_REGIMES, RegimeLabel.UNLABELED)

#: 局面の成果物の形式の版（`search/regimes_<組>.json`）。
REGIME_REPORT_SCHEMA_VERSION: Final = 1


def _require_count(value: object, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise KernelValueError(f"{label} must be an int >= 0")


@dataclass(frozen=True, slots=True)
class RegimeTrade:
    """経路の取引 1 件の振り分けの入力（D24 §5.3。単位は経路の取引の集合と同じ。D24 §3.3）。

    `entry_at` は建玉の入場約定の時刻。区間の終わりに仮に閉じた建玉（`SEGMENT_END_SETTLEMENT`）の
    `profit` は「仮決済の損益（決済の費用を引いた後）− 入場の費用」。
    """

    segment_index: int
    kind: PathTradeKind
    position_id: PositionId
    entry_at: UtcTime
    profit: Money

    def __post_init__(self) -> None:
        _require_count(self.segment_index, "RegimeTrade.segment_index")
        if not isinstance(self.kind, PathTradeKind):
            raise KernelValueError("RegimeTrade.kind must be a PathTradeKind")
        if not isinstance(self.position_id, PositionId):
            raise KernelValueError("RegimeTrade.position_id must be a PositionId")
        _require_time(self.entry_at, "RegimeTrade.entry_at")
        if not isinstance(self.profit, Money):
            raise KernelValueError("RegimeTrade.profit must be a Money")


@dataclass(frozen=True, slots=True)
class RegimeTradeRow:
    """取引ごとの局面（D24 §9.3 の表 3: 取引は区間の番号と取引の識別子で引く）。"""

    segment_index: int
    kind: PathTradeKind
    position_id: PositionId
    entry_at: UtcTime
    profit: Money
    label: RegimeLabel
    reason: UnlabeledReason | None
    daily_bar_start: UtcTime | None

    def __post_init__(self) -> None:
        RegimeTrade(
            segment_index=self.segment_index,
            kind=self.kind,
            position_id=self.position_id,
            entry_at=self.entry_at,
            profit=self.profit,
        )
        RegimeAt(label=self.label, reason=self.reason, daily_bar_start=self.daily_bar_start)


@dataclass(frozen=True, slots=True)
class RegimeDay:
    """経路の取引日 1 日の局面（取引日の始まりの時点で公開済みの最後の日足の局面。D24 §5.1）。"""

    day: Interval
    label: RegimeLabel
    reason: UnlabeledReason | None
    daily_bar_start: UtcTime | None

    def __post_init__(self) -> None:
        if not isinstance(self.day, Interval):
            raise KernelValueError("RegimeDay.day must be an Interval")
        RegimeAt(label=self.label, reason=self.reason, daily_bar_start=self.daily_bar_start)


@dataclass(frozen=True, slots=True)
class RegimeTableRow:
    """局面ごとの損益の表の 1 行（D24 §5.4）。

    件数は完了取引・区間の終わりの仮決済の別と、勝ち・負け（損益の符号。#4 と同じ）。`total` は
    損益の合計、`total_rate` はその経路の初期資金に対する率（D23 の仮置き 7）。平均利益 ÷ 平均損失は
    その局面の取引から #22 と同じ式で求め、値なしなら理由を持つ（`NO_TRADES`・
    `UNDEFINED_DENOMINATOR`）。
    """

    label: RegimeLabel
    closed_count: int
    settled_count: int
    win_count: int
    loss_count: int
    total: Money
    total_rate: Decimal
    average_win_loss: Decimal | None
    average_win_loss_unavailable: MetricUnavailableReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.label, RegimeLabel):
            raise KernelValueError("RegimeTableRow.label must be a RegimeLabel")
        for name in ("closed_count", "settled_count", "win_count", "loss_count"):
            _require_count(getattr(self, name), f"RegimeTableRow.{name}")
        if self.win_count + self.loss_count > self.closed_count + self.settled_count:
            raise KernelValueError("RegimeTableRow: 勝ち・負けの件数が取引の件数を超える")
        if not isinstance(self.total, Money):
            raise KernelValueError("RegimeTableRow.total must be a Money")
        if not isinstance(self.total_rate, Decimal):
            raise KernelValueError("RegimeTableRow.total_rate must be a Decimal")
        if (self.average_win_loss is None) == (self.average_win_loss_unavailable is None):
            raise KernelValueError(
                "RegimeTableRow: 平均利益 ÷ 平均損失は値か値なしの理由のどちらか一方を持つ"
            )
        if self.average_win_loss is not None and not isinstance(self.average_win_loss, Decimal):
            raise KernelValueError("RegimeTableRow.average_win_loss must be a Decimal or None")
        if self.average_win_loss_unavailable is not None and not isinstance(
            self.average_win_loss_unavailable, MetricUnavailableReason
        ):
            raise KernelValueError(
                "RegimeTableRow.average_win_loss_unavailable must be a MetricUnavailableReason"
            )


@dataclass(frozen=True, slots=True)
class RegimeDayRow:
    """局面の時間の割合の 1 行（経路の取引日のうちの日数と割合。D24 §5.4。成績ではない）。"""

    label: RegimeLabel
    days: int
    share: Decimal | None

    def __post_init__(self) -> None:
        if not isinstance(self.label, RegimeLabel):
            raise KernelValueError("RegimeDayRow.label must be a RegimeLabel")
        _require_count(self.days, "RegimeDayRow.days")
        if self.share is not None and not isinstance(self.share, Decimal):
            raise KernelValueError("RegimeDayRow.share must be a Decimal or None")


@dataclass(frozen=True, slots=True)
class FavoredRegimeCheck:
    """得意な局面の確かめ（D24 §5.4。決定 141。事実だけで、判定に使わない）。

    宣言した局面の損益の合計と件数、それ以外の局面（`UNLABELED` を含む。本 PR の仮置き）の合計と
    件数、宣言した局面の合計が正か。
    """

    declared: tuple[RegimeLabel, ...]
    favored_count: int
    favored_total: Money
    other_count: int
    other_total: Money
    favored_total_positive: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.declared, tuple)
            or not self.declared
            or not all(item in LABELED_REGIMES for item in self.declared)
        ):
            raise KernelValueError(
                "FavoredRegimeCheck.declared must be a non-empty tuple of labels"
            )
        _require_count(self.favored_count, "FavoredRegimeCheck.favored_count")
        _require_count(self.other_count, "FavoredRegimeCheck.other_count")
        if not isinstance(self.favored_total, Money) or not isinstance(self.other_total, Money):
            raise KernelValueError("FavoredRegimeCheck totals must be Money")
        if self.favored_total_positive is not (self.favored_total.amount > 0):
            raise KernelValueError("FavoredRegimeCheck.favored_total_positive must match the total")


class RegimeReportStatus(Enum):
    """局面の成果物の状態（D24 §5.2・§5.3・§9.2 の表の局面の行）。"""

    #: 局面の表を出す（局面の損益の和が経路の純損益と一致した）。
    AVAILABLE = "AVAILABLE"
    #: 局面の損益の和が経路の純損益と一致しない。表を出さず理由を出す（D24 §5.3、仮置き 14）。
    NOT_RECONCILED = "NOT_RECONCILED"
    #: 局面を出せない（日足が読めない・経路が終端していない等。理由つき）。判定は変わらない。
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RegimeReport:
    """組 1 つの局面の成果物（`search/regimes_<組>.json`。D24 §5・§9.3、仮置き 15）。

    結末記録にはこの値の正規化エンコードのダイジェストだけを入れる。レポートの順 9 はこの値を
    読んで表示し、計算し直さない（D09 §11.5）。判定の関数はこの型を入力に取らない（RP5-16）。

    - `AVAILABLE`: 日ごとの局面（`days`）・取引ごとの局面（`trades`）・局面の表（`table`。7 行）・
      時間の割合（`day_table`。7 行）・得意な局面の確かめ（宣言があれば）を持つ。
    - `NOT_RECONCILED`: 日ごとの局面・取引ごとの局面・時間の割合は持つが、損益の表と得意な局面の
      確かめは持たない（`reason` に整合しない値）。
    - `UNAVAILABLE`: 理由だけを持つ。
    """

    schema_version: int
    variant: Variant
    spec: RegimeSpec
    status: RegimeReportStatus
    reason: str | None
    daily_series: str | None
    favored_regimes: tuple[RegimeLabel, ...] | None
    initial_balance: Money | None
    path_net: Money | None
    regime_net: Money | None
    days: tuple[RegimeDay, ...]
    trades: tuple[RegimeTradeRow, ...]
    table: tuple[RegimeTableRow, ...]
    day_table: tuple[RegimeDayRow, ...]
    favored: FavoredRegimeCheck | None

    def __post_init__(self) -> None:
        if self.schema_version != REGIME_REPORT_SCHEMA_VERSION:
            raise KernelValueError(
                f"RegimeReport.schema_version must be {REGIME_REPORT_SCHEMA_VERSION}"
            )
        if not isinstance(self.variant, Variant):
            raise KernelValueError("RegimeReport.variant must be a Variant")
        if not isinstance(self.spec, RegimeSpec):
            raise KernelValueError("RegimeReport.spec must be a RegimeSpec")
        if not isinstance(self.status, RegimeReportStatus):
            raise KernelValueError("RegimeReport.status must be a RegimeReportStatus")
        if self.favored_regimes is not None and (
            not isinstance(self.favored_regimes, tuple)
            or not self.favored_regimes
            or not all(item in LABELED_REGIMES for item in self.favored_regimes)
        ):
            raise KernelValueError("RegimeReport.favored_regimes must be labels or None")
        for name, kind in (
            ("days", RegimeDay),
            ("trades", RegimeTradeRow),
            ("table", RegimeTableRow),
            ("day_table", RegimeDayRow),
        ):
            value = getattr(self, name)
            if not isinstance(value, tuple) or not all(isinstance(item, kind) for item in value):
                raise KernelValueError(f"RegimeReport.{name} must be a tuple of {kind.__name__}")
        for name in ("initial_balance", "path_net", "regime_net"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, Money):
                raise KernelValueError(f"RegimeReport.{name} must be a Money or None")
        if self.daily_series is not None and (
            not isinstance(self.daily_series, str) or not self.daily_series
        ):
            raise KernelValueError("RegimeReport.daily_series must be a non-empty str or None")
        if self.favored is not None and not isinstance(self.favored, FavoredRegimeCheck):
            raise KernelValueError("RegimeReport.favored must be a FavoredRegimeCheck or None")
        if self.status is RegimeReportStatus.AVAILABLE:
            self._check_available()
        elif self.status is RegimeReportStatus.NOT_RECONCILED:
            self._check_not_reconciled()
        else:
            self._check_unavailable()

    def _require_reason(self, present: bool) -> None:
        if present != (isinstance(self.reason, str) and bool(self.reason)):
            raise KernelValueError(
                f"RegimeReport({self.status.value}).reason must be"
                f" {'a non-empty str' if present else 'None'}"
            )

    def _require_measured(self) -> None:
        if self.initial_balance is None or self.path_net is None or self.regime_net is None:
            raise KernelValueError("RegimeReport: 局面を付けた経路は残高と損益の和を持つ")
        if self.daily_series is None:
            raise KernelValueError("RegimeReport: 局面を付けた経路は日足の系列を持つ")
        if tuple(row.label for row in self.day_table) != REGIME_TABLE_ORDER:
            raise KernelValueError("RegimeReport.day_table は 7 局面を表の順に 1 行ずつ持つ")

    def _check_available(self) -> None:
        self._require_reason(False)
        self._require_measured()
        if self.path_net != self.regime_net:
            raise KernelValueError(
                "RegimeReport(AVAILABLE): 局面の損益の和が経路の純損益と一致する"
            )
        if tuple(row.label for row in self.table) != REGIME_TABLE_ORDER:
            raise KernelValueError("RegimeReport.table は 7 局面を表の順に 1 行ずつ持つ")
        if (self.favored is None) != (self.favored_regimes is None):
            raise KernelValueError("RegimeReport: 得意な局面の確かめは宣言があるときだけ持つ")
        if self.favored is not None and self.favored.declared != self.favored_regimes:
            raise KernelValueError("RegimeReport: 得意な局面の確かめは宣言と同じ局面を持つ")

    def _check_not_reconciled(self) -> None:
        self._require_reason(True)
        self._require_measured()
        if self.path_net == self.regime_net:
            raise KernelValueError("RegimeReport(NOT_RECONCILED): 局面の損益の和が一致しない")
        if self.table or self.favored is not None:
            raise KernelValueError("RegimeReport(NOT_RECONCILED): 損益の表と得意な局面を持たない")

    def _check_unavailable(self) -> None:
        self._require_reason(True)
        if self.days or self.trades or self.table or self.day_table or self.favored is not None:
            raise KernelValueError("RegimeReport(UNAVAILABLE): 理由だけを持つ")
        if self.path_net is not None or self.regime_net is not None:
            raise KernelValueError("RegimeReport(UNAVAILABLE): 損益の和を持たない")


def unavailable_regime_report(
    *,
    variant: Variant,
    spec: RegimeSpec,
    reason: str,
    favored_regimes: tuple[RegimeLabel, ...] | None,
    daily_series: str | None = None,
    initial_balance: Money | None = None,
) -> RegimeReport:
    """局面を出せないときの成果物（D24 §5.2: 失敗は判定を変えず、「出せない（理由）」を書く）。"""
    return RegimeReport(
        schema_version=REGIME_REPORT_SCHEMA_VERSION,
        variant=variant,
        spec=spec,
        status=RegimeReportStatus.UNAVAILABLE,
        reason=reason,
        daily_series=daily_series,
        favored_regimes=favored_regimes,
        initial_balance=initial_balance,
        path_net=None,
        regime_net=None,
        days=(),
        trades=(),
        table=(),
        day_table=(),
        favored=None,
    )


def _table_row(
    label: RegimeLabel, rows: Sequence[RegimeTradeRow], initial: Money
) -> RegimeTableRow:
    total = Money(decimal_from_int(0), initial.currency)
    for row in rows:
        total = total + row.profit
    ratio = average_win_loss_ratio([row.profit for row in rows])
    return RegimeTableRow(
        label=label,
        closed_count=sum(1 for row in rows if row.kind is PathTradeKind.CLOSED_TRADE),
        settled_count=sum(1 for row in rows if row.kind is PathTradeKind.SEGMENT_END_SETTLEMENT),
        win_count=sum(1 for row in rows if row.profit.amount > 0),
        loss_count=sum(1 for row in rows if row.profit.amount < 0),
        total=total,
        total_rate=ratio_of(total.amount, initial.amount),
        average_win_loss=None if isinstance(ratio, MetricUnavailableReason) else ratio,
        average_win_loss_unavailable=ratio if isinstance(ratio, MetricUnavailableReason) else None,
    )


def build_regime_report(
    *,
    variant: Variant,
    spec: RegimeSpec,
    daily_series: str,
    daily: Sequence[DailyRegime],
    days: Sequence[Interval],
    trades: Sequence[RegimeTrade],
    initial_balance: Money,
    path_net: Money,
    favored_regimes: tuple[RegimeLabel, ...] | None,
) -> RegimeReport:
    """経路の取引と取引日に局面を付け、局面の成果物を組む（D24 §5.1・§5.3・§5.4、仮置き 14・15）。

    - 取引は入場の時点 `entry_at` に公開済みの最後の日足の局面に振り分ける（完了取引も区間の
      終わりに仮に閉じた建玉も、その建玉の入場の時点。持ち越した建玉は 1 件。D24 §5.3）。
    - 取引日は、その取引日の始まりの時点で公開済みの最後の日足の局面（D24 §5.1）。
    - 整合の規則: 局面ごとの合計（`UNLABELED` を含む）の和が `path_net`（経路の最後の残高 − 初期
      残高）と一致しなければ、損益の表を出さず理由を残す（D24 §5.3、仮置き 14）。
    """
    if not isinstance(initial_balance, Money) or not isinstance(path_net, Money):
        raise KernelValueError("build_regime_report requires Money balances")
    if initial_balance.currency != path_net.currency:
        raise KernelValueError("build_regime_report: 初期残高と経路の純損益の通貨が違う")
    if initial_balance.amount <= 0:
        raise KernelValueError("build_regime_report: 経路の初期残高は正（D13 §4.5）")
    if not isinstance(daily_series, str) or not daily_series:
        raise KernelValueError("build_regime_report requires the daily series name")
    lookup = RegimeLookup(daily)
    inputs = tuple(trades)
    if not all(isinstance(item, RegimeTrade) for item in inputs):
        raise KernelValueError("build_regime_report requires RegimeTrade values")
    keys = [(item.segment_index, item.position_id) for item in inputs]
    if len(set(keys)) != len(keys):
        raise KernelValueError("build_regime_report: 同じ区間の同じ建玉が 2 回ある（D24 §3.3）")
    rows: list[RegimeTradeRow] = []
    for item in inputs:
        if item.profit.currency != initial_balance.currency:
            raise KernelValueError("build_regime_report: 取引の損益の通貨が経路の口座の通貨と違う")
        found = lookup.at(item.entry_at)
        rows.append(
            RegimeTradeRow(
                segment_index=item.segment_index,
                kind=item.kind,
                position_id=item.position_id,
                entry_at=item.entry_at,
                profit=item.profit,
                label=found.label,
                reason=found.reason,
                daily_bar_start=found.daily_bar_start,
            )
        )
    spans = tuple(days)
    if not all(isinstance(item, Interval) for item in spans):
        raise KernelValueError("build_regime_report requires Interval trading days")
    labelled_days: list[RegimeDay] = []
    for span in spans:
        found = lookup.at(span.start)
        labelled_days.append(
            RegimeDay(
                day=span,
                label=found.label,
                reason=found.reason,
                daily_bar_start=found.daily_bar_start,
            )
        )
    day_rows: list[RegimeDayRow] = []
    for label in REGIME_TABLE_ORDER:
        count = sum(1 for item in labelled_days if item.label is label)
        day_rows.append(
            RegimeDayRow(
                label=label,
                days=count,
                share=ratio_of(decimal_from_int(count), decimal_from_int(len(labelled_days)))
                if labelled_days
                else None,
            )
        )
    table = tuple(
        _table_row(label, [row for row in rows if row.label is label], initial_balance)
        for label in REGIME_TABLE_ORDER
    )
    regime_net = Money(decimal_from_int(0), initial_balance.currency)
    for row in table:
        regime_net = regime_net + row.total
    reconciled = regime_net == path_net
    favored: FavoredRegimeCheck | None = None
    if reconciled and favored_regimes is not None:
        chosen = [row for row in table if row.label in favored_regimes]
        others = [row for row in table if row.label not in favored_regimes]
        favored_total = Money(decimal_from_int(0), initial_balance.currency)
        for row in chosen:
            favored_total = favored_total + row.total
        other_total = Money(decimal_from_int(0), initial_balance.currency)
        for row in others:
            other_total = other_total + row.total
        favored = FavoredRegimeCheck(
            declared=favored_regimes,
            favored_count=sum(row.closed_count + row.settled_count for row in chosen),
            favored_total=favored_total,
            other_count=sum(row.closed_count + row.settled_count for row in others),
            other_total=other_total,
            favored_total_positive=favored_total.amount > 0,
        )
    return RegimeReport(
        schema_version=REGIME_REPORT_SCHEMA_VERSION,
        variant=variant,
        spec=spec,
        status=RegimeReportStatus.AVAILABLE if reconciled else RegimeReportStatus.NOT_RECONCILED,
        reason=None
        if reconciled
        else (
            f"局面ごとの損益の和 {regime_net.amount} が経路の純損益 {path_net.amount} と一致しない"
            "ので、局面の表を出さない（D24 §5.3、仮置き 14）"
        ),
        daily_series=daily_series,
        favored_regimes=favored_regimes,
        initial_balance=initial_balance,
        path_net=path_net,
        regime_net=regime_net,
        days=tuple(labelled_days),
        trades=tuple(rows),
        table=table if reconciled else (),
        day_table=tuple(day_rows),
        favored=favored,
    )
