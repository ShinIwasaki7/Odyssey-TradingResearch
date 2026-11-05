"""通貨換算の経路（D06 §8.5・§8.5.1、Q7・Q9 決定。v1.20 案 §19）。

決済通貨と口座通貨が一致する run（USDJPY・円のペア）では、換算は**率1の恒等換算**になる。
経路を通すのは、円以外の決済通貨の脚を足しても呼び出し側が変わらないようにするためである。

経路の選び方は決定論的に固定する（D06 §8.5.1 の規則1、§19.3）。

1. 決済通貨と口座通貨が同じ → **恒等換算**（`legs=()`、`rate=1`、`skew=0`）
2. 直接のペア（`<決済通貨><口座通貨>`、無ければその逆を逆数で）が、run が読む承認済みの
   snapshot の集合（執行の snapshot と換算の snapshot）にある → その1本
3. 無ければ基軸通貨（`pivot_currency`）を経由する2本（各脚も直接 → 逆の順）
4. それも無ければ換算不可

恒等換算を「脚が0本の経路」で表すのは、`ConversionLeg` が系列・足・観測時点を必須にして
おり、参照する市場系列が存在しない恒等換算では架空の観測を書くことになるためである。

各脚は、その通貨ペアの 1 分足（bid）のうち**処理点の時刻までに終わった最後に記録のある分**
から取る（決定 (18) (a)。§19.4）。率はその分の終値（逆向きなら逆数。丸めない）、観測時刻は
その分の開始時刻。公開遅延は当てない。

検査の順は (1) 経路を組めるか → (2) 各脚の観測があるか → (3) ずれ（規則6）→ (4) 古さ
（規則9）で、最初に当たった理由を返す（仮置き C1-20）。古さは**休場の時間を除いて**測る
（観測時刻から処理点までのうち、run の取引カレンダーのセッションの中の時間だけを数える。
決定 (25) (a)）。ずれも同じ測り方で、2 本の脚の観測時刻の間のセッションの中の時間を数える
（決定 (28) (a)。v1.21 案 §19.5）。さらに、取引の通貨ペア（執行系列の通貨ペア。仮置き C4-1）
の 1 分足が渡されたときは、その 1 分足に記録の無い分も古さとずれに数えない（決定 (31) (a)。
v1.22 案 §19.5、仮置き C4-6）。渡されない run（取引の通貨ペアの 1 分足を持たない run）は
休場だけを除く（仮置き C4-2）。記録の `ConversionPath.skew`（規則5）は実際の差のまま残す。
上限は `ConversionPolicy.max_observation_skew` をずれと古さで共有する（仮置き C1-6）。
上限ちょうどは許す。

経路の**宣言型**（`ConversionPath` / `ConversionLeg`）は `domain.policies` にある。根拠記録
（`trace`）がそれを項目に持つ一方、中間層4つは相互に import できないためで、ここには経路を
**決める規則**だけを置く（D06 §3 の置き場所との差異は `domain.policies` に書いた）。
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from enum import Enum
from typing import Protocol, runtime_checkable

from odyssey_fx.backtest.domain.policies import ConversionLeg, ConversionPath, ConversionPolicy
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import (
    ConversionRate,
    CurrencyCode,
    decimal_from_int,
    kernel_context,
)
from odyssey_fx.common.refs import EvidenceRef, SnapshotRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "ConversionFailure",
    "ConversionQuotes",
    "ConversionUnavailable",
    "RecordedMinuteSpans",
    "RecordedMinutes",
    "RouteHop",
    "exceeds_limit",
    "identity_path",
    "last_minute_index",
    "legs_skew",
    "observation_staleness",
    "observe_path",
    "rate_of",
    "require_usable",
    "resolve_path",
    "resolve_route",
    "session_skew",
    "session_staleness",
    "session_time_between",
]

#: 恒等換算の率。
_ONE = decimal_from_int(1)
#: 換算の脚の 1 分足の長さ（決定 (18) (a)）。
_MINUTE = timedelta(minutes=1)


class ConversionFailure(Enum):
    """換算不可の理由（D06 §19.5 の検査の順。仮置き C1-20）。"""

    NO_ROUTE = "NO_ROUTE"
    NO_OBSERVATION = "NO_OBSERVATION"
    SKEW_EXCEEDED = "SKEW_EXCEEDED"
    STALENESS_EXCEEDED = "STALENESS_EXCEEDED"


class ConversionUnavailable(Exception):
    """換算できなかった（D06 §8.5.1 の規則1(d)・規則6・規則9）。

    用途に応じて受付前拒否（`DATA_ERROR`）か run の失敗へ読み替えるのは呼び出し側である。
    `path` は観測まで組めた経路（ずれ・古さの超過のとき。根拠記録に残す）。`series` は理由の
    系列（観測が無かった脚、またはいちばん古い脚の 1 分足の系列。経路が無ければ `None`）。
    `length` はずれか古さの長さ。理由の文には価格を書かない。
    """

    def __init__(
        self,
        failure: ConversionFailure,
        message: str,
        *,
        series: SeriesId | None = None,
        path: ConversionPath | None = None,
        length: timedelta | None = None,
    ) -> None:
        super().__init__(message)
        self.failure = failure
        self.series = series
        self.path = path
        self.length = length


@dataclass(frozen=True, slots=True)
class RouteHop:
    """経路の1ホップの通貨ペアと向き（`inverted=True` ならそのペアの逆数を使う）。"""

    symbol: Symbol
    inverted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise KernelValueError("RouteHop.symbol must be a Symbol")
        if not isinstance(self.inverted, bool):
            raise KernelValueError("RouteHop.inverted must be a bool")


@runtime_checkable
class ConversionQuotes(Protocol):
    """換算の脚の 1 分足の供給（D06 §19.3・§19.4）。合成（`app`）が実装する。

    `symbols` は経路を探す集合（執行の snapshot と換算の snapshot の系列の通貨ペアの和集合）。
    `last_minute(symbol, moment)` は、その通貨ペアの記録のある 1 分足（bid）のうち、終わりが
    `moment` 以下の最後の足（無ければ `None`）。`series_of(symbol)` はその通貨ペアの 1 分足の
    系列、`snapshot_of(symbol)` はその足を読む snapshot。
    """

    @property
    def symbols(self) -> frozenset[Symbol]: ...

    def last_minute(self, symbol: Symbol, moment: UtcTime) -> Bar | None: ...

    def series_of(self, symbol: Symbol) -> SeriesId: ...

    def snapshot_of(self, symbol: Symbol) -> SnapshotRef: ...


@runtime_checkable
class RecordedMinutes(Protocol):
    """取引の通貨ペアの記録のある 1 分足（D06 v1.22 案 §19.5 の決定 (31) の (ii)。仮置き C4-2）。

    `minute_bars_in(interval)` は、区間に始まる記録のある 1 分足（時刻順）。MT5 経路の執行系列の
    1 分足（`backtest.engine.loop.MinuteObservation`）がそのまま満たす。
    """

    def minute_bars_in(self, interval: Interval) -> Sequence[Bar]: ...


@runtime_checkable
class RecordedMinuteSpans(Protocol):
    """取引の通貨ペアの記録のある 1 分足の**時刻だけ**（D18 §4.2。価格を持たない）。

    `minute_spans_in(interval)` は、区間に始まる記録のある 1 分足の `(開始, 終わり)`（時刻順）。
    エンジンは `RecordedMinutes`（1 分足）をこの形に読み替えて渡し、古さとずれの集計の道具
    （`tools/survey/conversion_staleness_scan.py`）は snapshot の時刻の列だけからこの形を作る。
    両方が同じ `session_time_between` を呼ぶので、数え方がずれない（D18 の仮置き 9）。
    """

    def minute_spans_in(self, interval: Interval) -> Iterable[tuple[UtcTime, UtcTime]]: ...


@dataclass(frozen=True, slots=True)
class _BarSpans:
    """`RecordedMinutes`（1 分足）を時刻だけの `RecordedMinuteSpans` に読み替える。"""

    recorded: RecordedMinutes

    def minute_spans_in(self, interval: Interval) -> Iterable[tuple[UtcTime, UtcTime]]:
        return ((bar.bar_start, bar.bar_end) for bar in self.recorded.minute_bars_in(interval))


def _spans(recorded: RecordedMinutes | None) -> RecordedMinuteSpans | None:
    return None if recorded is None else _BarSpans(recorded)


def last_minute_index(starts: Sequence[UtcTime], moment: UtcTime) -> int:
    """記録のある 1 分足の開始（昇順）のうち、終わりが `moment` 以下の最後の足の位置（D06 §19.4）。

    1 分足の終わりは開始の 1 分後なので、開始が `moment − 1 分` 以下の最後の足。無ければ −1。
    換算の脚の供給（`app.conversion`）と古さとずれの集計の道具が同じ関数で脚の観測を引く。
    """
    return bisect.bisect_right(starts, moment - _MINUTE) - 1


def identity_path(at: UtcTime) -> ConversionPath:
    """恒等換算の経路（脚0本・率1・ずれ0）。"""
    return ConversionPath(legs=(), rate=_ONE, observed_at=at, skew=timedelta(0))


def _symbol_of(base: CurrencyCode, quote: CurrencyCode) -> Symbol:
    return Symbol(f"{base.code}{quote.code}")


def _hop(
    from_currency: CurrencyCode, to_currency: CurrencyCode, available: frozenset[Symbol]
) -> RouteHop | None:
    """1ホップ: 直接のペア（`from→to`）、無ければ逆のペアの逆数（規則1 (b)・規則3）。"""
    direct = _symbol_of(from_currency, to_currency)
    if direct in available:
        return RouteHop(symbol=direct)
    reverse = _symbol_of(to_currency, from_currency)
    if reverse in available:
        return RouteHop(symbol=reverse, inverted=True)
    return None


def resolve_route(
    from_currency: CurrencyCode,
    to_currency: CurrencyCode,
    available: frozenset[Symbol],
    policy: ConversionPolicy,
) -> tuple[RouteHop, ...] | None:
    """決済通貨から口座通貨への経路の通貨ペアと向き（D06 §8.5.1 の規則1、§19.3）。

    恒等なら空のタプル、組めなければ `None`。同じ入力に対して常に同じ経路を返す。
    """
    if not isinstance(from_currency, CurrencyCode) or not isinstance(to_currency, CurrencyCode):
        raise KernelValueError("resolve_route requires CurrencyCode values")
    if not isinstance(policy, ConversionPolicy):
        raise KernelValueError("resolve_route requires a ConversionPolicy")
    if from_currency == to_currency:
        return ()
    direct = _hop(from_currency, to_currency, available)
    if direct is not None:
        return (direct,)
    pivot = policy.pivot_currency
    if pivot in (from_currency, to_currency):
        return None
    first = _hop(from_currency, pivot, available)
    second = _hop(pivot, to_currency, available)
    if first is None or second is None:
        return None
    return (first, second)


def resolve_path(
    from_currency: CurrencyCode,
    to_currency: CurrencyCode,
    at: UtcTime,
    policy: ConversionPolicy,
) -> ConversionPath:
    """市場系列を読まない経路（恒等換算）を返す。決済通貨が違えば `ConversionUnavailable`。

    円以外の決済通貨の経路は `observe_path` が脚の 1 分足から組む。ここで黙って率1に落とさない。
    """
    if not isinstance(from_currency, CurrencyCode) or not isinstance(to_currency, CurrencyCode):
        raise KernelValueError("resolve_path requires CurrencyCode values")
    if not isinstance(policy, ConversionPolicy):
        raise KernelValueError("resolve_path requires a ConversionPolicy")
    if from_currency == to_currency:
        return identity_path(at)
    raise ConversionUnavailable(
        ConversionFailure.NO_ROUTE,
        f"no conversion path from {from_currency} to {to_currency} without market series;"
        " the legs are observed from the conversion snapshot (D06 §19.3)",
    )


def observe_path(
    route: tuple[RouteHop, ...], at: UtcTime, quotes: ConversionQuotes | None
) -> ConversionPath:
    """経路の各脚を 1 分足から引き、合成した経路にする（D06 §19.3・§19.4、規則3〜5）。

    観測の無い脚があれば `ConversionUnavailable`（`NO_OBSERVATION`）。ずれ・古さは見ない
    （`require_usable`）。
    """
    if not route:
        return identity_path(at)
    if quotes is None:
        raise ConversionUnavailable(
            ConversionFailure.NO_OBSERVATION,
            "the run has no conversion series to observe the legs (D06 §19.3)",
        )
    legs: list[ConversionLeg] = []
    with localcontext(kernel_context()):
        rate = _ONE
        for hop in route:
            bar = quotes.last_minute(hop.symbol, at)
            if bar is None:
                raise ConversionUnavailable(
                    ConversionFailure.NO_OBSERVATION,
                    f"no recorded 1m bar of {hop.symbol} ends by {at} (D06 §19.4)",
                    series=quotes.series_of(hop.symbol),
                )
            close: Decimal = bar.close.value
            leg_rate = _ONE / close if hop.inverted else close
            rate = rate * leg_rate
            legs.append(
                ConversionLeg(
                    series=bar.series,
                    bar_key=bar.key,
                    rate=leg_rate,
                    observed_at=bar.bar_start,
                    inverted=hop.inverted,
                )
            )
    observed = [leg.observed_at for leg in legs]
    oldest = min(observed, key=lambda value: value.value)
    newest = max(observed, key=lambda value: value.value)
    return ConversionPath(legs=tuple(legs), rate=rate, observed_at=oldest, skew=newest - oldest)


def session_time_between(
    start: UtcTime,
    end: UtcTime,
    calendar: TradingCalendar,
    recorded: RecordedMinuteSpans | None = None,
) -> timedelta:
    """`start` から `end` までのうち、古さ・ずれに数える時間（時刻だけを受け取る。D18 §4.2）。

    `recorded` が無ければ、カレンダーのセッションの中の時間（休場の時間を除く。決定 (25) (a)）。
    あれば、1 分ごとの区間 `[m, m + 1 分)` のうち (i) セッションの中にあり (ii) 取引の通貨ペアの
    1 分足に記録のあるものの長さ（決定 (31) (a)。v1.22 案 §19.5、仮置き C4-1・C4-2）。数える分は
    どれも `end` までに終わっている（`m + 1 分 ≤ end`）ので、将来の足を見ない。`end` が `start`
    以前なら 0。古さ（規則9）とずれ（規則6）が同じ測り方を使う。
    """
    if not start < end:
        return timedelta(0)
    total = timedelta(0)
    for session in calendar.sessions(Interval(start=start, end=end)):
        if recorded is None:
            total += session.end - session.start
            continue
        for minute_start, minute_end in recorded.minute_spans_in(session):
            if session.start <= minute_start and minute_end <= session.end:
                total += minute_end - minute_start
    return total


def observation_staleness(
    observed_at: UtcTime,
    at: UtcTime,
    calendar: TradingCalendar,
    recorded: RecordedMinuteSpans | None = None,
) -> timedelta:
    """観測時刻 `observed_at` の経路の、処理点 `at` での古さ（規則9。時刻だけを受け取る）。"""
    return session_time_between(observed_at, at, calendar, recorded)


def legs_skew(
    observed: Sequence[UtcTime],
    calendar: TradingCalendar,
    recorded: RecordedMinuteSpans | None = None,
) -> timedelta:
    """脚の観測時刻の間のずれ（規則6。時刻だけを受け取る）。脚が 2 本未満なら 0。"""
    if len(observed) < 2:
        return timedelta(0)
    oldest = min(observed, key=lambda value: value.value)
    newest = max(observed, key=lambda value: value.value)
    return session_time_between(oldest, newest, calendar, recorded)


def exceeds_limit(length: timedelta, limit: timedelta) -> bool:
    """古さ・ずれが上限を超えるか（上限ちょうどは許す。D06 §19.5）。"""
    return length > limit


def session_staleness(
    path: ConversionPath,
    at: UtcTime,
    calendar: TradingCalendar,
    recorded: RecordedMinutes | None = None,
) -> timedelta:
    """判定に使う古さ（観測時刻から処理点まで。決定 (25) (a)・(31) (a)）。

    休場の時間を除き、`recorded`（取引の通貨ペアの 1 分足）があれば記録の無い分も除く。
    恒等換算の古さは 0。休場をまたがず、取引の通貨ペアが毎分記録を持つ処理点では
    `at − observed_at` と同じ。数えるのは時刻だけの `observation_staleness`（D18 §4.2）。
    """
    if path.is_identity:
        return timedelta(0)
    return observation_staleness(path.observed_at, at, calendar, _spans(recorded))


def session_skew(
    path: ConversionPath,
    calendar: TradingCalendar,
    recorded: RecordedMinutes | None = None,
) -> timedelta:
    """判定に使うずれ（2 本の脚の観測時刻の間。決定 (28) (a)、仮置き C4-6）。

    古さと同じ測り方（休場を除き、`recorded` があれば取引の通貨ペアに記録の無い分も除く）。
    脚が 2 本未満なら 0。休場をまたがず、取引の通貨ペアが毎分記録を持つ 2 本の脚では記録の
    `path.skew`（実際の差）と同じ。いちばん新しい脚は処理点より前なので、この値は同じ処理点・
    同じ `recorded` の `session_staleness` を超えない。数えるのは時刻だけの `legs_skew`。
    """
    return legs_skew(tuple(leg.observed_at for leg in path.legs), calendar, _spans(recorded))


def require_usable(
    path: ConversionPath,
    at: UtcTime,
    policy: ConversionPolicy,
    calendar: TradingCalendar,
    recorded: RecordedMinutes | None = None,
) -> None:
    """ずれ（規則6）と古さ（規則9）の上限を確かめる。超えれば `ConversionUnavailable`。

    どちらも休場の時間を除いて測る（決定 (25) (a)・(28) (a)）。`recorded`（取引の通貨ペアの
    1 分足。MT5 経路の run）があれば、その 1 分足に記録の無い分も除く（決定 (31) (a)、仮置き
    C4-2・C4-6）。上限ちょうどは許す（「超えたら」）。ずれを古さより先に見る（仮置き C1-20）。
    理由の長さは判定に使った値（仮置き C3-2・C4-5）。
    """
    if path.is_identity:
        return
    limit = policy.max_observation_skew
    measure = (
        "session time"
        if recorded is None
        else "session time counting only minutes the traded pair recorded"
    )
    skew = session_skew(path, calendar, recorded)
    if exceeds_limit(skew, limit):
        raise ConversionUnavailable(
            ConversionFailure.SKEW_EXCEEDED,
            f"the conversion legs are {skew} apart in {measure}, more than {limit}"
            " (D06 §8.5.1 の規則6)",
            series=_oldest_series(path),
            path=path,
            length=skew,
        )
    staleness = session_staleness(path, at, calendar, recorded)
    if exceeds_limit(staleness, limit):
        raise ConversionUnavailable(
            ConversionFailure.STALENESS_EXCEEDED,
            f"the conversion observation at {path.observed_at} is {staleness} old in {measure}"
            f" at {at}, more than {limit} (D06 §19.5 の規則9)",
            series=_oldest_series(path),
            path=path,
            length=staleness,
        )


def _oldest_series(path: ConversionPath) -> SeriesId | None:
    """いちばん古い脚の系列（合成の観測時刻を決めた脚）。"""
    for leg in path.legs:
        if leg.observed_at == path.observed_at:
            return leg.series
    return None


def rate_of(
    path: ConversionPath,
    from_currency: CurrencyCode,
    to_currency: CurrencyCode,
    evidence: EvidenceRef | None = None,
) -> ConversionRate:
    """経路を1本の換算率にまとめる（D06 §8.5.1 の規則7）。"""
    if not isinstance(path, ConversionPath):
        raise KernelValueError("rate_of requires a ConversionPath")
    return ConversionRate(
        from_currency=from_currency,
        to_currency=to_currency,
        rate=path.rate,
        observed_at=path.observed_at,
        evidence=evidence,
    )
