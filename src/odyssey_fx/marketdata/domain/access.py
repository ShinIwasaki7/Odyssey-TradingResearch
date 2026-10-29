"""期間のアクセス分類と封印状態（D03 §3.8、ADR-0014・ADR-0038）。

`AccessClass` は「その期間のデータを何に使ってよいか」の分類。

旧データの snapshot（手元の旧データの CSV などから作ったもの。ADR-0038 第0項の 2・第6項）の
区分は ADR-0014 のまま変えない。

- `RESEARCH_HISTORY`（研究履歴、2016〜2023年）: 開発・研究に使える。
- `LEGACY_HOLDOUT`（旧基盤の封印期間、2024〜2025年）: `HoldoutState` を持つ。
- `QUARANTINED_UNASSIGNED`（未分類の隔離期間、2026年以降）: 未観測の確認と別の決定記録
  （ADR）による再分類が行われるまで、**いかなる経路でも読めない**。

OANDA MT5 API の 1 分足の経路（列対応の宣言 `oanda_mt5_m1`）から作る snapshot の区分は
ADR-0038 第1項による（順 6 の S3 で実装。区分の名前は ADR-0038 の仮の名をそのまま使う）。

- `RESEARCH_HISTORY` `[2016-01-01Z, 2024-01-01Z)`: 今のまま。
- `HOLDOUT_SINCE_2024`（2024 年以降の封印の区分）`[2024-01-01Z, 取得の開始時点)`。
- `FORWARD_HOLDOUT`（前向きの封印の区分）`[取得の開始時点, ∞)`。

2 つの封印の区分はどちらも `HoldoutState` を持ち、系列ごとに**取引週**（ニューヨークの日曜
17:00 で切る）の partition に分ける（ADR-0038 第2項）。取得の開始時点は窓の設定
（`configs/acquisition/oanda_mt5_m1_sealed_window.yaml`。ADR-0037 第4項）から `app` が読んで
渡す。本モジュールに日付を書かない（値が空の間は 2024-01-01 以降の足を分類しない。fail-closed）。

`HoldoutState` は封印の区分の partition だけが持つ。`SEALED` から `CONSUMED`（使用済み）への
遷移は**不可逆**で、逆遷移は存在しない。状態は manifest に直接書き換えるフィールドを持たず、
追記専用の消費の記録（`access_log.jsonl`）から導出する（ADR-0014 の消費遷移の直列化。
ADR-0038 第8項で引き継ぐ）。

**足の所属は `bar_end` で決める**（D03 §3.8）。`bar_end` が境界以上の足は後ろの区分に入る。
2023-12-31 22:00Z に始まり 2024-01-01 22:00Z に終わる日足を研究区分に入れると 2024 年の
情報が研究区分へ漏れるため。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Final
from zoneinfo import ZoneInfo

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.errors import (
    AcquisitionStartUnset,
    MarketDataValueError,
    SealedBoundaryBar,
)

__all__ = [
    "INITIAL_ACCESS_BOUNDARIES",
    "OANDA_SEALED_CLASSES",
    "PROTECTED_CLASSES",
    "SEALED_CLASSES",
    "SEALED_RESEARCH_UNTIL",
    "TRADING_WEEK_ZONE",
    "AccessBoundaries",
    "AccessClass",
    "HoldoutState",
    "SealedAccessBoundaries",
    "is_trading_week_start",
    "next_trading_week_start",
    "sealed_partition_period",
    "trading_week_start",
]


class AccessClass(Enum):
    """期間のアクセス分類（D03 §3.8、ADR-0014・ADR-0038）。

    値は partition ディレクトリ名にそのまま使う（例: `USDJPY_1h_bid/RESEARCH_HISTORY/`）。
    """

    RESEARCH_HISTORY = "RESEARCH_HISTORY"
    LEGACY_HOLDOUT = "LEGACY_HOLDOUT"
    QUARANTINED_UNASSIGNED = "QUARANTINED_UNASSIGNED"
    #: 2024 年以降の封印の区分（ADR-0038 第1項）。`[2024-01-01Z, 取得の開始時点)`。
    HOLDOUT_SINCE_2024 = "HOLDOUT_SINCE_2024"
    #: 前向きの封印の区分（ADR-0038 第1項）。`[取得の開始時点, ∞)`。
    FORWARD_HOLDOUT = "FORWARD_HOLDOUT"


#: OANDA MT5 の経路の 2 つの封印の区分（ADR-0038 第1項）。取引週の partition を持つ。
OANDA_SEALED_CLASSES: Final = frozenset(
    {AccessClass.HOLDOUT_SINCE_2024, AccessClass.FORWARD_HOLDOUT}
)

#: 封印の区分（`HoldoutState` を持つ区分すべて。ADR-0035 第0項の 3）。
SEALED_CLASSES: Final = frozenset({AccessClass.LEGACY_HOLDOUT, *OANDA_SEALED_CLASSES})

#: 2024 年以降の区分（封印の区分と隔離の区分の総称。ADR-0035 第0項の 3）。品質確認の閲覧の
#: 記録の対象になる区分。
PROTECTED_CLASSES: Final = frozenset({*SEALED_CLASSES, AccessClass.QUARANTINED_UNASSIGNED})


class HoldoutState(Enum):
    """封印の区分の partition の状態（D03 §3.8、ADR-0014 2026-09-20 改訂・ADR-0038 第3項）。

    旧データの snapshot の `LEGACY_HOLDOUT` は、旧基盤で未観測と確認できた partition だけを
    `SEALED` にする（ADR-0014）。OANDA MT5 の経路の 2 つの封印の区分は受入れで `SEALED` で
    始める（例外 2 つ。ADR-0038 第3項）。`SEALED → CONSUMED` は不可逆。
    """

    SEALED = "SEALED"
    CONSUMED = "CONSUMED"


@dataclass(frozen=True, slots=True)
class AccessBoundaries:
    """旧データの snapshot のアクセス分類の期間境界（D03 §3.8、ADR-0014）。

    `research_until` 未満が研究履歴、`research_until` 以上 `holdout_until` 未満が封印期間、
    `holdout_until` 以上が未分類の隔離期間。境界は暦年の UTC 時刻（ADR-0014 の承認事項9）。
    OANDA MT5 の経路の境界は `SealedAccessBoundaries`（ADR-0038）。
    """

    research_until: UtcTime
    holdout_until: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.research_until, UtcTime):
            raise MarketDataValueError("AccessBoundaries.research_until must be a UtcTime")
        if not isinstance(self.holdout_until, UtcTime):
            raise MarketDataValueError("AccessBoundaries.holdout_until must be a UtcTime")
        if not self.research_until < self.holdout_until:
            raise MarketDataValueError(
                "AccessBoundaries requires research_until < holdout_until, got"
                f" {self.research_until} and {self.holdout_until}"
            )

    def classify(self, bar_end: UtcTime) -> AccessClass:
        """足の終了時刻から所属する分類を決める（D03 §3.8）。

        `bar_start` ではなく `bar_end` を使うのは、区間をまたぐ足の後半の情報が前の区分へ
        漏れないようにするため。
        """
        if not isinstance(bar_end, UtcTime):
            raise MarketDataValueError("AccessBoundaries.classify requires a UtcTime")
        if bar_end < self.research_until:
            return AccessClass.RESEARCH_HISTORY
        if bar_end < self.holdout_until:
            return AccessClass.LEGACY_HOLDOUT
        return AccessClass.QUARANTINED_UNASSIGNED


#: 初版の期間境界（D03 §3.8）。旧データの snapshot の境界（ADR-0038 第0項の 2）。
#: 研究履歴 `[2016-01-01Z, 2024-01-01Z)` / 封印期間 `[2024-01-01Z, 2026-01-01Z)` /
#: 未分類の隔離期間 `[2026-01-01Z, ∞)`。
INITIAL_ACCESS_BOUNDARIES = AccessBoundaries(
    research_until=UtcTime.from_components(2024, 1, 1),
    holdout_until=UtcTime.from_components(2026, 1, 1),
)

#: OANDA MT5 の経路の研究履歴の終わり＝2024 年以降の封印の区分の始まり（ADR-0038 第1項）。
#: 旧データの snapshot の研究履歴の終わりと同じ値で、取得を許す期間の始まり（ADR-0037 第2項の
#: 1、窓の設定の `allowed_start`）とも一致する（`app.config` が照合する）。
SEALED_RESEARCH_UNTIL: Final = INITIAL_ACCESS_BOUNDARIES.research_until


# --- 取引週（ADR-0038 第2項、ADR-0037 第2項の 2）------------------------------------------

#: 取引週の境目を決める時間帯（`America/New_York`。夏時間を含む規則で UTC に直す）。
TRADING_WEEK_ZONE: Final = ZoneInfo("America/New_York")
#: 取引週の境目の現地時刻（日曜 17:00）。夏時間の切替は 02:00 なので、17:00 は曖昧にも
#: 不存在にもならない。
_WEEK_OPEN: Final = time(17, 0)
_SUNDAY: Final = 6  # `date.weekday()` の日曜


def _sunday_open(day: datetime) -> UtcTime:
    """現地の日付 `day`（日曜）の 17:00 を UTC に直す。"""
    return UtcTime.from_local(datetime.combine(day.date(), _WEEK_OPEN), TRADING_WEEK_ZONE)


def trading_week_start(moment: UtcTime) -> UtcTime:
    """`moment` を含む取引週の始まり（`moment` 以前で最も新しいニューヨークの日曜 17:00）。

    休場の宣言は見ない（時刻だけで決まる。ADR-0037 第2項の 2）。取引週は半開区間
    `[始まり, 次の始まり)` なので、ちょうど境目の時刻はその境目から始まる週に入る。
    """
    if not isinstance(moment, UtcTime):
        raise MarketDataValueError("trading_week_start requires a UtcTime")
    local = moment.value.astimezone(TRADING_WEEK_ZONE).replace(tzinfo=None)
    sunday = local - timedelta(days=(local.weekday() - _SUNDAY) % 7)
    candidate = _sunday_open(sunday)
    if moment < candidate:
        candidate = _sunday_open(sunday - timedelta(days=7))
    return candidate


def next_trading_week_start(week_start: UtcTime) -> UtcTime:
    """取引週の始まり `week_start` の次の取引週の始まり（夏時間の切替の週は 167・169 時間）。"""
    if not is_trading_week_start(week_start):
        raise MarketDataValueError(
            f"{week_start} is not the start of a trading week (New York Sunday 17:00)"
        )
    local = week_start.value.astimezone(TRADING_WEEK_ZONE).replace(tzinfo=None)
    return _sunday_open(local + timedelta(days=7))


def is_trading_week_start(moment: UtcTime) -> bool:
    """`moment` がちょうど取引週の境目（ニューヨークの日曜 17:00）か。"""
    if not isinstance(moment, UtcTime):
        raise MarketDataValueError("is_trading_week_start requires a UtcTime")
    return trading_week_start(moment) == moment


# --- OANDA MT5 の経路の境界（ADR-0038 第1項・第2項）--------------------------------------


@dataclass(frozen=True, slots=True)
class SealedAccessBoundaries:
    """OANDA MT5 の経路の区分の境界（ADR-0038 第1項）。

    研究履歴の終わりは `SEALED_RESEARCH_UNTIL`（2024-01-01T00:00Z）。`acquisition_start`
    （取得の開始時点。ADR-0037 第2項の 2）は窓の設定から `app` が読んで渡し、空（`None`）の
    間は 2024-01-01 以降の足を分類しない（`AcquisitionStartUnset`。ADR-0038 第1項の「値が
    空の間は、2024-01-01 以降の足を含む受入れを始めない」）。取得の開始時点は取引週の境目で
    なければならない（ADR-0038 第2項の 1）。
    """

    acquisition_start: UtcTime | None

    def __post_init__(self) -> None:
        start = self.acquisition_start
        if start is None:
            return
        if not isinstance(start, UtcTime):
            raise MarketDataValueError(
                "SealedAccessBoundaries.acquisition_start must be a UtcTime or None"
            )
        if not SEALED_RESEARCH_UNTIL < start:
            raise MarketDataValueError(
                f"the acquisition start {start} must be after {SEALED_RESEARCH_UNTIL}"
                " (ADR-0038 第1項)"
            )
        if not is_trading_week_start(start):
            raise MarketDataValueError(
                f"the acquisition start {start} is not the start of a trading week"
                " (New York Sunday 17:00. ADR-0037 第2項の 2・ADR-0038 第2項の 1)"
            )

    @property
    def research_until(self) -> UtcTime:
        """研究履歴の終わり（2024-01-01T00:00Z。ADR-0038 第1項）。"""
        return SEALED_RESEARCH_UNTIL

    def classify(self, bar_start: UtcTime, bar_end: UtcTime) -> AccessClass:
        """足の区分を決める（所属は `bar_end`。D03 §3.8・ADR-0038 第0項の 3）。

        始まりが 2024-01-01T00:00Z より前で終わりがそれ以後の足（終わりがちょうど
        2024-01-01T00:00Z の足を含む）は、研究期間の snapshot にも封印の取り込みにも現れない
        はずの足なので `SealedBoundaryBar`（構造エラー）で止める（D03 §16.1・PR #109 の仮置き
        S2b-7）。
        """
        if not isinstance(bar_start, UtcTime) or not isinstance(bar_end, UtcTime):
            raise MarketDataValueError("SealedAccessBoundaries.classify requires UtcTime values")
        if not bar_start < bar_end:
            raise MarketDataValueError(
                f"a bar must start before it ends, got [{bar_start}, {bar_end})"
            )
        if bar_end < SEALED_RESEARCH_UNTIL:
            return AccessClass.RESEARCH_HISTORY
        if bar_start < SEALED_RESEARCH_UNTIL:
            raise SealedBoundaryBar(
                f"a bar [{bar_start}, {bar_end}) straddles {SEALED_RESEARCH_UNTIL}: a research"
                " snapshot never holds a sealed partition and the sealed intake never requests a"
                " bar that starts before the window. Nothing was written (D03 §16.1, PR #109 の"
                " 仮置き S2b-7)"
            )
        if self.acquisition_start is None:
            raise AcquisitionStartUnset(
                f"a bar ending at {bar_end} belongs to a sealed class, but the acquisition start"
                " is not set in configs/acquisition/oanda_mt5_m1_sealed_window.yaml; nothing is"
                " classified until it is (ADR-0038 第1項)"
            )
        if bar_end < self.acquisition_start:
            return AccessClass.HOLDOUT_SINCE_2024
        return AccessClass.FORWARD_HOLDOUT

    def partition_period(self, access_class: AccessClass, bar_end: UtcTime) -> Interval:
        """封印の区分の足が入る partition の期間（取引週。ADR-0038 第2項の 1）。

        期間は `[取引週の始まり, 次の取引週の始まり)`。最初の partition だけは
        `[2024-01-01T00:00Z, 最初の境目)` に切る。取得の開始時点は取引週の境目なので、
        2 つの封印の区分の境目でも partition は切れる。
        """
        if access_class not in OANDA_SEALED_CLASSES:
            raise MarketDataValueError(
                f"only the OANDA MT5 sealed classes are split into trading weeks, got"
                f" {access_class.value} (ADR-0038 第2項)"
            )
        if not isinstance(bar_end, UtcTime):
            raise MarketDataValueError("partition_period requires a UtcTime")
        if bar_end <= SEALED_RESEARCH_UNTIL:
            raise MarketDataValueError(
                f"a bar ending at {bar_end} does not belong to a sealed class (ADR-0038 第1項)"
            )
        week = trading_week_start(bar_end)
        start = week if SEALED_RESEARCH_UNTIL <= week else SEALED_RESEARCH_UNTIL
        return Interval(start=start, end=next_trading_week_start(week))


def sealed_partition_period(period_start: UtcTime) -> Interval:
    """封印の区分の partition の始まりから、その partition の期間を作る（ADR-0038 第2項）。

    `period_start` は取引週の始まりか、最初の partition の始まり（2024-01-01T00:00Z）。
    取引週の始まりが 2024-01-01T00:00Z より前なら 2024-01-01T00:00Z に切る。
    """
    if not isinstance(period_start, UtcTime):
        raise MarketDataValueError("sealed_partition_period requires a UtcTime")
    if period_start < SEALED_RESEARCH_UNTIL:
        raise MarketDataValueError(
            f"a sealed partition cannot start before {SEALED_RESEARCH_UNTIL}, got {period_start}"
        )
    week = trading_week_start(period_start)
    start = week if SEALED_RESEARCH_UNTIL <= week else SEALED_RESEARCH_UNTIL
    if start != period_start:
        raise MarketDataValueError(
            f"{period_start} is not the start of a sealed partition (a trading-week start, or"
            f" {SEALED_RESEARCH_UNTIL} for the first one. ADR-0038 第2項の 1)"
        )
    return Interval(start=start, end=next_trading_week_start(week))
