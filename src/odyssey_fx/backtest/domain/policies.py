"""実行設定とポリシーの宣言型（D06 §3・§6.4・§7.1.1・§7.2・§7.4・§7.6・§8.5）。

いずれも**実験前に固定し、実行中に変更しない**（上位設計書 §4.7.9 A）。run manifest から
版参照（`PolicyRef`）で辿れるようにする。

`RunConfig` が持つのは参照（`PolicyRef`）だけで、値そのものは持たない。参照を解決するのは
合成の責務（`app`）であり、解決済みの値は実行ユースケースの構築時に渡す
（`application.run_backtest`）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, localcontext
from enum import Enum
from types import MappingProxyType

from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import (
    CurrencyCode,
    Money,
    Price,
    PriceOffset,
    Quantity,
    decimal_from_int,
    kernel_context,
)
from odyssey_fx.common.refs import CompiledStrategyRef, PolicyRef, SnapshotRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "ConversionLeg",
    "ConversionPath",
    "ConversionPolicy",
    "CostModel",
    "ExecutionPolicy",
    "FixedSpread",
    "HierarchyCheckResult",
    "ReferenceQuoteSource",
    "ResolutionHierarchy",
    "RiskPolicy",
    "RunConfig",
    "SpreadModel",
]


#: 往復の回数（エントリーと決済で2回の手数料が要る）。
_ROUND_TRIP = decimal_from_int(2)


def _require_positive(value: object, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise KernelValueError(f"{label} must be a Decimal, got {value!r}")
    if not value.is_finite() or value <= 0:
        raise KernelValueError(f"{label} must be a finite positive Decimal, got {value}")
    return value


def _require_positive_duration(value: object, label: str) -> timedelta:
    if not isinstance(value, timedelta):
        raise KernelValueError(f"{label} must be a timedelta, got {value!r}")
    if value <= timedelta(0):
        raise KernelValueError(f"{label} must be positive, got {value}")
    return value


class ReferenceQuoteSource(Enum):
    """受付時の参照価格の出どころ（D06 §6.4 の手順3、Q10 決定）。

    段階2の唯一の値は「直前に完了した執行足の終値」である。約定の判定に使う価格と同じ系列
    から参照価格を取ると、約定ずれの数値に系列の粒度差が入り込まない。
    """

    EXECUTION_SERIES_LAST_CLOSE = "EXECUTION_SERIES_LAST_CLOSE"


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    """1試行と口座全体のリスク上限（上位設計書 §4.7.10）。

    費用予算 `C(Q)` は数量に比例するため、固定額としてここには置かず `CostModel` から
    計算する（D06 §7.6）。
    """

    trial_risk_rate: Decimal
    account_risk_cap: Decimal

    def __post_init__(self) -> None:
        _require_positive(self.trial_risk_rate, "RiskPolicy.trial_risk_rate")
        _require_positive(self.account_risk_cap, "RiskPolicy.account_risk_cap")
        if self.trial_risk_rate > self.account_risk_cap:
            raise KernelValueError(
                "RiskPolicy.trial_risk_rate must not exceed account_risk_cap"
                f" ({self.trial_risk_rate} > {self.account_risk_cap})"
            )


@dataclass(frozen=True, slots=True)
class FixedSpread:
    """固定の提示価格の幅（D06 §7.2）。`ask = bid + offset`。"""

    offset: PriceOffset
    kind: str = "FIXED_SPREAD"

    def __post_init__(self) -> None:
        if self.kind != "FIXED_SPREAD":
            raise KernelValueError(f"FixedSpread.kind must be 'FIXED_SPREAD', got {self.kind!r}")
        if not isinstance(self.offset, PriceOffset):
            raise KernelValueError("FixedSpread.offset must be a PriceOffset")
        if self.offset.value < 0:
            raise KernelValueError(f"FixedSpread.offset must be >= 0, got {self.offset}")

    def ask_from_bid(self, bid: Price) -> Price:
        """bid だけの系列から ask を導く（D06 §7.2）。

        価格にも費用にも spread を重複して加算しないため、**価格を作るのはここだけ**に
        する。金額としての記録は `CostKind.SPREAD_IN_PRICE` の参考値で行う。
        """
        if not isinstance(bid, Price):
            raise KernelValueError("FixedSpread.ask_from_bid requires a Price")
        return bid + self.offset


#: 区分タグ付き union。可変 spread・時間帯別 spread は段階6（D06 §12）。
SpreadModel = FixedSpread


@dataclass(frozen=True, slots=True)
class ResolutionHierarchy:
    """足内競合を解決する解像度の階層（ADR-0030、D06 §7.4）。粗い順に並べる。"""

    levels: tuple[SeriesId, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.levels, tuple) or not self.levels:
            raise KernelValueError("ResolutionHierarchy.levels must be a non-empty tuple")
        for level in self.levels:
            if not isinstance(level, SeriesId):
                raise KernelValueError("ResolutionHierarchy.levels must contain SeriesId values")
        if len({str(level) for level in self.levels}) != len(self.levels):
            raise KernelValueError("ResolutionHierarchy.levels must not repeat a series")


@dataclass(frozen=True, slots=True)
class HierarchyCheckResult:
    """解像度階層の適合検査1件（D06 §7.4）。

    比べるのは区間・価格基準・足境界・利用可能時刻であり、金額でも比率でもないため、
    リスク審査の `RiskCheckResult`（`limit` と `observed` が `Money | Decimal`）を流用せず
    専用の型を置く。流用すると不一致の実値を型どおり記録できず、架空の数値を入れるか
    診断を捨てることになる。どの検査でも埋まらない項目は `None` にする。

    **置き場所の差異**: D06 §3 の型表は `execution` としているが、この結果は run manifest
    （`trace`）にもそのまま保存される（D06 §9.3）。中間層4つは相互に import できないため
    （D01 §3.3 の契約 L2c）、両方から参照できる `domain` に置く。項目は D06 §7.4 の表の
    ままで、内容の差異はない。
    """

    check: str
    passed: bool
    parent_series: SeriesId
    child_series: SeriesId | None = None
    parent_bar: BarKey | None = None
    child_bar: BarKey | None = None
    expected_interval: Interval | None = None
    child_intervals: tuple[Interval, ...] = ()
    coverage_gaps: tuple[Interval, ...] = ()
    coverage_overlaps: tuple[Interval, ...] = ()
    expected_boundary: UtcTime | None = None
    observed_boundary: UtcTime | None = None
    expected_basis: PriceBasis | None = None
    observed_basis: PriceBasis | None = None
    expected_available_at: UtcTime | None = None
    observed_available_at: UtcTime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.check, str) or not self.check:
            raise KernelValueError("HierarchyCheckResult.check must be a non-empty str")
        if not isinstance(self.passed, bool):
            raise KernelValueError("HierarchyCheckResult.passed must be a bool")
        if not isinstance(self.parent_series, SeriesId):
            raise KernelValueError("HierarchyCheckResult.parent_series must be a SeriesId")


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    """約定の意味を決めるポリシー（D06 §7.1.1・§7.4、Q3・Q4・Q5・Q8・Q10 決定）。"""

    adverse_fill_limits: Mapping[Symbol, PriceOffset]
    entry_valid_for: timedelta
    close_valid_for: timedelta
    resolution_hierarchy: ResolutionHierarchy
    entry_delay_bars: int = 0
    reference_quote_source: ReferenceQuoteSource = ReferenceQuoteSource.EXECUTION_SERIES_LAST_CLOSE

    def __post_init__(self) -> None:
        if not isinstance(self.adverse_fill_limits, Mapping):
            raise KernelValueError("ExecutionPolicy.adverse_fill_limits must be a Mapping")
        for symbol, offset in self.adverse_fill_limits.items():
            if not isinstance(symbol, Symbol):
                raise KernelValueError("ExecutionPolicy.adverse_fill_limits keys must be Symbol")
            if not isinstance(offset, PriceOffset) or offset.value <= 0:
                raise KernelValueError(
                    f"the adverse fill limit for {symbol} must be a positive PriceOffset"
                )
        object.__setattr__(
            self, "adverse_fill_limits", MappingProxyType(dict(self.adverse_fill_limits))
        )
        _require_positive_duration(self.entry_valid_for, "ExecutionPolicy.entry_valid_for")
        _require_positive_duration(self.close_valid_for, "ExecutionPolicy.close_valid_for")
        if not isinstance(self.resolution_hierarchy, ResolutionHierarchy):
            raise KernelValueError(
                "ExecutionPolicy.resolution_hierarchy must be a ResolutionHierarchy"
            )
        if isinstance(self.entry_delay_bars, bool) or not isinstance(self.entry_delay_bars, int):
            raise KernelValueError("ExecutionPolicy.entry_delay_bars must be an int")
        if self.entry_delay_bars < 0:
            raise KernelValueError(
                f"ExecutionPolicy.entry_delay_bars must be >= 0, got {self.entry_delay_bars}"
            )
        if not isinstance(self.reference_quote_source, ReferenceQuoteSource):
            raise KernelValueError(
                "ExecutionPolicy.reference_quote_source must be a ReferenceQuoteSource"
            )

    def adverse_fill_limit(self, symbol: Symbol) -> PriceOffset | None:
        """銘柄の許容不利約定幅 Δ（未登録なら `None`。D06 §7.1.1・§10.5 の手順2a）。"""
        return self.adverse_fill_limits.get(symbol)


@dataclass(frozen=True, slots=True)
class CostModel:
    """費用モデル（D06 §7.6）。swap / rollover は計上しない（ADR-0029）。"""

    commission_per_unit: Money
    entry_slippage: PriceOffset
    close_slippage: PriceOffset
    spread_model: SpreadModel
    swap_modeled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.commission_per_unit, Money):
            raise KernelValueError("CostModel.commission_per_unit must be a Money")
        if self.commission_per_unit.amount < 0:
            raise KernelValueError("CostModel.commission_per_unit must be >= 0")
        for value, label in (
            (self.entry_slippage, "entry_slippage"),
            (self.close_slippage, "close_slippage"),
        ):
            if not isinstance(value, PriceOffset):
                raise KernelValueError(f"CostModel.{label} must be a PriceOffset")
            if value.value < 0:
                raise KernelValueError(f"CostModel.{label} must be >= 0, got {value}")
        if not isinstance(self.spread_model, FixedSpread):
            raise KernelValueError("CostModel.spread_model must be a SpreadModel")
        if self.swap_modeled is not False:
            raise KernelValueError(
                "CostModel.swap_modeled must be False in stage 2 (ADR-0029); modelling swap"
                " requires a separate decision that revises that ADR"
            )

    def budget_per_unit(self) -> Decimal:
        """1通貨あたりの費用予算（D06 §7.6）。

        `往復手数料 ＋ 損切り決済の滑り`。Δ に含めたエントリーの滑りを重複加算しない
        （上位設計書 §4.7.9 C）。数量に比例するため `RiskPolicy` に固定額では置けない。
        """
        with localcontext(kernel_context()):
            return self.commission_per_unit.amount * _ROUND_TRIP + self.close_slippage.value

    def budget_for(self, quantity: Quantity, currency: CurrencyCode) -> Money:
        """予約に含める費用予算 `C(Q)`（D06 §7.6）。"""
        if not isinstance(quantity, Quantity):
            raise KernelValueError("CostModel.budget_for requires a Quantity")
        with localcontext(kernel_context()):
            return Money(self.budget_per_unit() * quantity.units, currency)


@dataclass(frozen=True, slots=True)
class ConversionPolicy:
    """通貨換算のポリシー（D06 §8.5.1、Q7・Q9 決定）。"""

    pivot_currency: CurrencyCode
    max_observation_skew: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.pivot_currency, CurrencyCode):
            raise KernelValueError("ConversionPolicy.pivot_currency must be a CurrencyCode")
        _require_positive_duration(
            self.max_observation_skew, "ConversionPolicy.max_observation_skew"
        )


@dataclass(frozen=True, slots=True)
class ConversionLeg:
    """換算経路の1ホップ（D06 §8.5.1）。逆向きのペアは逆数を取り `inverted=True` を立てる。

    **置き場所の差異**: D06 §3 の型表は `portfolio.conversion` としているが、この経路は
    根拠記録（`trace` の `EvidenceRecord.conversion_paths`）の項目でもあり、中間層4つは
    相互に import できない（D01 §3.3 の契約 L2c）。そこで宣言型を `domain` に置き、経路を
    決める規則（`resolve_path` など）は `portfolio.conversion` に残す。
    """

    series: SeriesId
    bar_key: BarKey
    rate: Decimal
    observed_at: UtcTime
    inverted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise KernelValueError("ConversionLeg.series must be a SeriesId")
        if not isinstance(self.bar_key, BarKey):
            raise KernelValueError("ConversionLeg.bar_key must be a BarKey")
        if not isinstance(self.rate, Decimal) or not self.rate.is_finite() or self.rate <= 0:
            raise KernelValueError(
                f"ConversionLeg.rate must be a positive Decimal, got {self.rate}"
            )
        if not isinstance(self.observed_at, UtcTime):
            raise KernelValueError("ConversionLeg.observed_at must be a UtcTime")
        if not isinstance(self.inverted, bool):
            raise KernelValueError("ConversionLeg.inverted must be a bool")


@dataclass(frozen=True, slots=True)
class ConversionPath:
    """合成前の換算経路（D06 §8.5.1）。脚は0本（恒等）・1本（直接）・2本（基軸通貨経由）。

    `observed_at` は各ホップの観測時点のうち**最も古いもの**、`skew` は観測時点の最大差で
    ある。合成した率が「いちばん古い脚と同じだけしか新しくない」ことを記録に残すためで、
    新しい方を採ると、古い脚の情報がその時点で観測されたと読める記録になる。
    置き場所の差異は `ConversionLeg` と同じ。
    """

    legs: tuple[ConversionLeg, ...]
    rate: Decimal
    observed_at: UtcTime
    skew: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.legs, tuple) or not all(
            isinstance(leg, ConversionLeg) for leg in self.legs
        ):
            raise KernelValueError("ConversionPath.legs must be a tuple of ConversionLeg")
        if len(self.legs) > 2:
            raise KernelValueError(
                f"a conversion path uses at most 2 hops (D06 §8.5.1), got {len(self.legs)}"
            )
        if not isinstance(self.rate, Decimal) or not self.rate.is_finite() or self.rate <= 0:
            raise KernelValueError(
                f"ConversionPath.rate must be a positive Decimal, got {self.rate}"
            )
        if not isinstance(self.observed_at, UtcTime):
            raise KernelValueError("ConversionPath.observed_at must be a UtcTime")
        if not isinstance(self.skew, timedelta):
            raise KernelValueError("ConversionPath.skew must be a timedelta")
        if self.skew < timedelta(0):
            raise KernelValueError(f"ConversionPath.skew must be >= 0, got {self.skew}")
        if len(self.legs) < 2 and self.skew != timedelta(0):
            raise KernelValueError(
                "a path with fewer than two hops has no observation skew (D06 §8.5.1 の規則5)"
            )
        if self.legs:
            oldest = min(leg.observed_at for leg in self.legs)
            if self.observed_at != oldest:
                raise KernelValueError(
                    "ConversionPath.observed_at must be the oldest leg observation"
                    f" ({self.observed_at} != {oldest})"
                )

    @property
    def is_identity(self) -> bool:
        """脚を持たない恒等換算か。"""
        return not self.legs


@dataclass(frozen=True, slots=True)
class RunConfig:
    """1回の run の設定（D06 §3・§9.3）。

    ポリシーは**参照だけ**を持つ。値の解決は合成の責務であり、再実行時に最新版へ
    差し替えない（上位設計書 §4.7.15 の `PolicyRef`）。
    """

    run_interval: Interval
    snapshot_ref: SnapshotRef
    compiled_ref: CompiledStrategyRef
    account: AccountSpec
    risk_policy_ref: PolicyRef
    execution_policy_ref: PolicyRef
    cost_model_ref: PolicyRef
    conversion_policy_ref: PolicyRef
    delay_scenario_ref: PolicyRef
    execution_series: SeriesId
    seed: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.run_interval, Interval):
            raise KernelValueError("RunConfig.run_interval must be an Interval")
        if not isinstance(self.snapshot_ref, SnapshotRef):
            raise KernelValueError("RunConfig.snapshot_ref must be a SnapshotRef")
        if not isinstance(self.compiled_ref, CompiledStrategyRef):
            raise KernelValueError("RunConfig.compiled_ref must be a CompiledStrategyRef")
        if not isinstance(self.account, AccountSpec):
            raise KernelValueError("RunConfig.account must be an AccountSpec")
        for name in (
            "risk_policy_ref",
            "execution_policy_ref",
            "cost_model_ref",
            "conversion_policy_ref",
            "delay_scenario_ref",
        ):
            if not isinstance(getattr(self, name), PolicyRef):
                raise KernelValueError(f"RunConfig.{name} must be a PolicyRef")
        if not isinstance(self.execution_series, SeriesId):
            raise KernelValueError("RunConfig.execution_series must be a SeriesId")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise KernelValueError("RunConfig.seed must be an int")
