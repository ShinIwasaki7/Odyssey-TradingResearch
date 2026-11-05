"""研究ポリシー第 3 版の前提の記録と、比較の前提の固定（D13 §6.4・§6.6、D09 §10.9 の 2026-10-21
改訂。決定 D13-16 (a)・D13-19 (a)・D13-21 (a)、仮置き 4・12・18・21）。

- `PolicyBasis`（研究ポリシーの `evaluation_standard.basis`）: 範囲を当てた承認済みの 2 つの
  snapshot（標準・候補。両方必須）、範囲に取得不足が無いことを確かめた系列、前提の規則のファイル
  （識別・版・パス・SHA-256）の列。読込は形だけを確かめ、評価基準のダイジェストに入れる。
- `FixedComparisonBasis`（研究ポリシーの `evaluation_standard.comparison_basis`）: 比較の前提と
  なる口座（通貨・初期残高）・リスク方針（1 取引の損失予算・口座リスク上限）・費用モデル・数量に
  効く執行ポリシーの値（入場の遅延・許容する不利約定の幅）。用途が `STANDARD` の版を指す探索の
  実験の設定と読込で照合する（照合は `app.config`）。

型名と前提のファイルの名前は実装 PR の仮置き 50・51（2026-10-22 の人間の決定）。

**版 4 以上の形**（研究ポリシー第 4 版。D18 §3.1・§3.2・§3.4、仮置き 4〜7）:

- `PerSymbolPolicyBasis`: 通貨ごとの snapshot の組（`SymbolSnapshots`）、換算の snapshot の組、
  評価の対象系列（通貨 × 時間足の積の形）、前提の規則のファイル（版 3 の 7 件に、研究期間の確認済み
  ハッシュの一覧 `verified_raw` と通貨ごとの銘柄の設定 `symbol_<通貨>` を足したもの）。
- `PerSymbolComparisonBasis`: 通貨で共通の項目（口座・リスク・1 単位の手数料・入場の遅延・換算の
  決め事）と、通貨ごとの 4 つの値（`SymbolCosts`。スプレッド・入場の滑り・決済の滑り・許容する
  不利約定の幅）。

版 3 の型（`PolicyBasis`・`FixedComparisonBasis`）の形と検査は変えない（版 3 のダイジェストを
保つ。D18 の仮置き 7）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import SnapshotId
from odyssey_fx.evaluation.domain.variant import Paired

__all__ = [
    "PREMISE_NAMES",
    "SYMBOL_PREMISE_PREFIX",
    "VERIFIED_RAW_PREMISE",
    "FixedComparisonBasis",
    "PerSymbolComparisonBasis",
    "PerSymbolPolicyBasis",
    "PolicyBasis",
    "PremiseFile",
    "SymbolCosts",
    "SymbolSnapshots",
    "symbol_premise_name",
]

#: 前提の規則のファイルの名前（D12 §12.1 の表の 7 件。実装 PR の仮置き 51）。
PREMISE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "time_rule_table",
        "approved_time_rule_tables",
        "calendar",
        "counting_rules",
        "fetch_manifest",
        "datasource",
        "timeframes",
    }
)

#: 版 4 以上で足す前提: 研究期間の確認済みハッシュの一覧（D18 §3.1、仮置き 5）。
VERIFIED_RAW_PREMISE: Final = "verified_raw"

#: 版 4 以上で足す前提: 通貨ごとの銘柄の設定の名前の頭（`symbol_<通貨>`。D18 §3.1、仮置き 5）。
SYMBOL_PREMISE_PREFIX: Final = "symbol_"

_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
_SERIES: Final = re.compile(r"[A-Z0-9]+/[0-9A-Za-z_]+/(bid|ask|mid)")
_CURRENCY: Final = re.compile(r"[A-Z]{3}")
_SYMBOL: Final = re.compile(r"[A-Z0-9]+")
_SYMBOL_PREMISE: Final = re.compile(rf"{SYMBOL_PREMISE_PREFIX}[A-Z0-9]+")


def symbol_premise_name(symbol: str) -> str:
    """通貨ごとの銘柄の設定の前提の名前（`symbol_<通貨>`。D18 §3.1、仮置き 5）。"""
    return f"{SYMBOL_PREMISE_PREFIX}{symbol}"


def _is_premise_name(name: object) -> bool:
    return isinstance(name, str) and (
        name in PREMISE_NAMES
        or name == VERIFIED_RAW_PREMISE
        or _SYMBOL_PREMISE.fullmatch(name) is not None
    )


@dataclass(frozen=True, slots=True)
class PremiseFile:
    """前提の規則のファイル1件（D13 §6.4 の `basis.premises`）。版の無いものは `version = None`。"""

    name: str
    version: int | None
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not _is_premise_name(self.name):
            raise KernelValueError(
                f"前提のファイルの名前 {self.name!r} は {sorted(PREMISE_NAMES)} のどれか"
                f"（D13 §6.4。実装 PR の仮置き 51）、または版 4 以上の {VERIFIED_RAW_PREMISE!r}・"
                f"`{SYMBOL_PREMISE_PREFIX}<通貨>`（D18 §3.1、仮置き 5）"
            )
        if self.version is not None and (
            isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1
        ):
            raise KernelValueError(f"前提のファイル {self.name} の版は 1 以上の整数か null")
        if (
            not isinstance(self.path, str)
            or not self.path
            or self.path.startswith("/")
            or ".." in self.path.split("/")
        ):
            raise KernelValueError(
                f"前提のファイル {self.name} のパスはリポジトリの根からの相対パス: {self.path!r}"
            )
        if not isinstance(self.sha256, str) or not _SHA256_HEX.fullmatch(self.sha256):
            raise KernelValueError(
                f"前提のファイル {self.name} の sha256 は 16 進 64 文字（小文字）: {self.sha256!r}"
            )


@dataclass(frozen=True, slots=True)
class PolicyBasis:
    """前提の記録（研究ポリシーの `evaluation_standard.basis`。D13 §6.4、D09 付記 A の改訂）。"""

    snapshots: Paired[SnapshotId]
    target_series: tuple[str, ...]
    premises: tuple[PremiseFile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshots, Paired):
            raise KernelValueError("PolicyBasis.snapshots must be Paired[SnapshotId]")
        self.snapshots.require(SnapshotId, "basis.snapshots")
        if (
            not isinstance(self.target_series, tuple)
            or not self.target_series
            or not all(
                isinstance(item, str) and _SERIES.fullmatch(item) for item in self.target_series
            )
        ):
            raise KernelValueError(
                "basis.target_series は `<銘柄>/<時間足>/<bid|ask|mid>` の系列を 1 つ以上並べる"
            )
        if len(set(self.target_series)) != len(self.target_series):
            raise KernelValueError("basis.target_series に同じ系列が 2 つある")
        if not isinstance(self.premises, tuple) or not all(
            isinstance(item, PremiseFile) for item in self.premises
        ):
            raise KernelValueError("basis.premises must be a tuple of PremiseFile")
        names = [item.name for item in self.premises]
        if len(set(names)) != len(names):
            raise KernelValueError(f"basis.premises に同じ名前の前提が 2 つある: {names}")
        extra = sorted(set(names) - PREMISE_NAMES)
        if extra:
            raise KernelValueError(
                f"basis.premises の {extra} は版 4 以上の前提で、版 3 の形には書けない（D13 §6.4、"
                "D18 の仮置き 7）"
            )
        missing = sorted(PREMISE_NAMES - set(names))
        if missing:
            raise KernelValueError(
                f"basis.premises に前提のファイル {missing} が無い（7 件すべてを書く。D13 §6.4。"
                "実装 PR の仮置き 51）"
            )


def _require_rate(value: object, label: str, *, positive: bool) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise KernelValueError(f"comparison_basis.{label} must be a finite Decimal")
    if value < 0 or (positive and value == 0):
        raise KernelValueError(
            f"comparison_basis.{label} は {'正' if positive else '0 以上'}の十進数: {value}"
        )


@dataclass(frozen=True, slots=True)
class FixedComparisonBasis:
    """比較の前提の固定（研究ポリシーの `evaluation_standard.comparison_basis`。D13 §6.6）。

    `adverse_fill_limits` は `(銘柄, 許容する不利約定の幅)` を銘柄の昇順に並べたもの。
    """

    currency: str
    initial_balance: Decimal
    trial_risk_rate: Decimal
    account_risk_cap: Decimal
    spread_offset: Decimal
    commission_per_unit: Decimal
    entry_slippage: Decimal
    close_slippage: Decimal
    entry_delay_bars: int
    adverse_fill_limits: tuple[tuple[str, Decimal], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not _CURRENCY.fullmatch(self.currency):
            raise KernelValueError("comparison_basis.account.currency は 3 文字の通貨コード")
        _require_rate(self.initial_balance, "account.initial_balance", positive=True)
        _require_rate(self.trial_risk_rate, "risk_policy.trial_risk_rate", positive=True)
        _require_rate(self.account_risk_cap, "risk_policy.account_risk_cap", positive=True)
        for label in ("spread_offset", "commission_per_unit", "entry_slippage", "close_slippage"):
            _require_rate(getattr(self, label), f"cost_model.{label}", positive=False)
        if (
            isinstance(self.entry_delay_bars, bool)
            or not isinstance(self.entry_delay_bars, int)
            or self.entry_delay_bars < 0
        ):
            raise KernelValueError(
                "comparison_basis.execution_policy.entry_delay_bars は 0 以上の整数"
            )
        if not isinstance(self.adverse_fill_limits, tuple) or not all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            and _SYMBOL.fullmatch(item[0])
            for item in self.adverse_fill_limits
        ):
            raise KernelValueError(
                "comparison_basis.execution_policy.adverse_fill_limits は (銘柄, 幅) の組の列"
            )
        for symbol, limit in self.adverse_fill_limits:
            _require_rate(limit, f"execution_policy.adverse_fill_limits.{symbol}", positive=False)
        symbols = [item[0] for item in self.adverse_fill_limits]
        if symbols != sorted(set(symbols)):
            raise KernelValueError(
                "comparison_basis.execution_policy.adverse_fill_limits は銘柄の昇順で重複なし"
            )


# --- 版 4 以上の形（D18 §3.1・§3.2・§3.4）---------------------------------------------


def _require_symbol(symbol: object, label: str) -> None:
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        raise KernelValueError(f"{label} の通貨ペアは英大文字と数字の銘柄名: {symbol!r}")


def _require_sorted_symbols(symbols: list[str], label: str) -> None:
    if not symbols:
        raise KernelValueError(f"{label} に通貨ペアが 1 つも無い")
    if symbols != sorted(set(symbols)):
        raise KernelValueError(f"{label} は通貨ペアの昇順で重複なし: {symbols}")


def _require_distinct_pair(pair: object, label: str) -> None:
    if not isinstance(pair, Paired):
        raise KernelValueError(f"{label} must be Paired[SnapshotId]")
    pair.require(SnapshotId, label)
    if pair.standard == pair.candidate:
        raise KernelValueError(
            f"{label} の標準の組と候補の組が同じ snapshot である（2 つは別の snapshot。D13-16 (a)）"
        )


@dataclass(frozen=True, slots=True)
class SymbolSnapshots:
    """1 通貨の承認済みの snapshot の組（版 4 以上の `basis.symbols` の 1 要素。D18 §3.1）。"""

    symbol: str
    snapshots: Paired[SnapshotId]

    def __post_init__(self) -> None:
        _require_symbol(self.symbol, "basis.symbols")
        _require_distinct_pair(self.snapshots, f"basis.symbols.{self.symbol}")


@dataclass(frozen=True, slots=True)
class PerSymbolPolicyBasis:
    """版 4 以上の前提の記録（研究ポリシーの `evaluation_standard.basis`。D18 §3.1・§3.4、
    仮置き 3〜5・7）。

    - `symbols`: 通貨ごとの snapshot の組（通貨の昇順）。
    - `conversion_snapshots`: 換算の snapshot の組（円以外のペアの実験の `conversion_snapshots`
      と役割ごとに照合する。D18 §3.3 の C3）。どの通貨の組と同じであるべきかは比較の前提の換算の
      決め事から決まるので、`EvaluationStandard` が確かめる。
    - `target_series`: 評価の対象系列。通貨ごとに同じ時間足・価格の基準の組を持つ積の形で、通貨の
      集合は `symbols` と同じ（D18 §2.1。1 つ欠ければ拒否）。
    - `premises`: 前提の規則のファイル。版 3 の 7 件・`verified_raw`・通貨ごとの `symbol_<通貨>`
      の過不足ない集合（D18 §3.1、仮置き 5）。SHA-256 と実ファイルの照合は読込（`app.config`）。
    """

    symbols: tuple[SymbolSnapshots, ...]
    conversion_snapshots: Paired[SnapshotId]
    target_series: tuple[str, ...]
    premises: tuple[PremiseFile, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.symbols, tuple) or not all(
            isinstance(item, SymbolSnapshots) for item in self.symbols
        ):
            raise KernelValueError("basis.symbols must be a tuple of SymbolSnapshots")
        names = [item.symbol for item in self.symbols]
        _require_sorted_symbols(names, "basis.symbols")
        _require_distinct_pair(self.conversion_snapshots, "basis.conversion_snapshots")
        self._check_target_series(names)
        self._check_premises(names)

    def _check_target_series(self, names: list[str]) -> None:
        if (
            not isinstance(self.target_series, tuple)
            or not self.target_series
            or not all(
                isinstance(item, str) and _SERIES.fullmatch(item) for item in self.target_series
            )
        ):
            raise KernelValueError(
                "basis.target_series は `<銘柄>/<時間足>/<bid|ask|mid>` の系列を 1 つ以上並べる"
            )
        if len(set(self.target_series)) != len(self.target_series):
            raise KernelValueError("basis.target_series に同じ系列が 2 つある")
        shapes: dict[str, set[str]] = {}
        for series in self.target_series:
            symbol, _, rest = series.partition("/")
            shapes.setdefault(symbol, set()).add(rest)
        if sorted(shapes) != names:
            raise KernelValueError(
                f"basis.target_series の通貨 {sorted(shapes)} が basis.symbols の通貨 {names} と"
                " 違う（D18 §3.4 の読込の検査）"
            )
        first = shapes[names[0]]
        uneven = sorted(symbol for symbol in names if shapes[symbol] != first)
        if uneven:
            raise KernelValueError(
                f"basis.target_series は通貨ごとに同じ時間足と価格の基準を持つ（{names[0]} は"
                f" {sorted(first)}）が、{uneven} が違う（D18 §2.1。1 つ欠けた対象系列は拒否）"
            )

    def _check_premises(self, names: list[str]) -> None:
        if not isinstance(self.premises, tuple) or not all(
            isinstance(item, PremiseFile) for item in self.premises
        ):
            raise KernelValueError("basis.premises must be a tuple of PremiseFile")
        written = [item.name for item in self.premises]
        if len(set(written)) != len(written):
            raise KernelValueError(f"basis.premises に同じ名前の前提が 2 つある: {written}")
        required = (
            set(PREMISE_NAMES)
            | {VERIFIED_RAW_PREMISE}
            | {symbol_premise_name(symbol) for symbol in names}
        )
        missing = sorted(required - set(written))
        extra = sorted(set(written) - required)
        if missing or extra:
            raise KernelValueError(
                f"basis.premises は版 3 の 7 件・{VERIFIED_RAW_PREMISE}・basis.symbols の通貨ごとの"
                f" `{SYMBOL_PREMISE_PREFIX}<通貨>` を過不足なく書く（D18 §3.1、仮置き 5）。"
                f"無い: {missing}、余分: {extra}"
            )

    def snapshots_of(self, symbol: str) -> Paired[SnapshotId] | None:
        """通貨の snapshot の組（無ければ `None`）。"""
        return next((item.snapshots for item in self.symbols if item.symbol == symbol), None)

    def premise(self, name: str) -> PremiseFile:
        """名前で前提を引く（構築時に過不足なくそろうことを確かめ済み）。"""
        return next(item for item in self.premises if item.name == name)


@dataclass(frozen=True, slots=True)
class SymbolCosts:
    """1 通貨の価格の単位の費用の値（版 4 以上の `comparison_basis.symbols` の 1 要素。
    D18 §3.2、仮置き 6）。値は決済通貨の価格の単位（D06 §19.11）。"""

    symbol: str
    spread_offset: Decimal
    entry_slippage: Decimal
    close_slippage: Decimal
    adverse_fill_limit: Decimal

    def __post_init__(self) -> None:
        _require_symbol(self.symbol, "comparison_basis.symbols")
        for label in ("spread_offset", "entry_slippage", "close_slippage", "adverse_fill_limit"):
            _require_rate(getattr(self, label), f"symbols.{self.symbol}.{label}", positive=False)


@dataclass(frozen=True, slots=True)
class PerSymbolComparisonBasis:
    """版 4 以上の比較の前提（研究ポリシーの `evaluation_standard.comparison_basis`。D18 §3.2、
    仮置き 6。決定 35・36）。

    通貨で共通の項目（口座・リスク・1 単位の手数料（円建て）・入場の遅延・換算の決め事）と、
    通貨ごとの 4 つの値（`symbols`。通貨の昇順）を持つ。換算の決め事の上限は秒数で持つ
    （`max_observation_skew_seconds`。実験設定の `conversion_policy.max_observation_skew` と同じ
    長さで照合する）。
    """

    currency: str
    initial_balance: Decimal
    trial_risk_rate: Decimal
    account_risk_cap: Decimal
    commission_per_unit: Decimal
    entry_delay_bars: int
    pivot_currency: str
    max_observation_skew_seconds: int
    symbols: tuple[SymbolCosts, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.currency, str) or not _CURRENCY.fullmatch(self.currency):
            raise KernelValueError("comparison_basis.account.currency は 3 文字の通貨コード")
        _require_rate(self.initial_balance, "account.initial_balance", positive=True)
        _require_rate(self.trial_risk_rate, "risk_policy.trial_risk_rate", positive=True)
        _require_rate(self.account_risk_cap, "risk_policy.account_risk_cap", positive=True)
        _require_rate(self.commission_per_unit, "commission_per_unit", positive=False)
        if (
            isinstance(self.entry_delay_bars, bool)
            or not isinstance(self.entry_delay_bars, int)
            or self.entry_delay_bars < 0
        ):
            raise KernelValueError("comparison_basis.entry_delay_bars は 0 以上の整数")
        if not isinstance(self.pivot_currency, str) or not _CURRENCY.fullmatch(self.pivot_currency):
            raise KernelValueError(
                "comparison_basis.conversion_policy.pivot_currency は 3 文字の通貨コード"
            )
        if (
            isinstance(self.max_observation_skew_seconds, bool)
            or not isinstance(self.max_observation_skew_seconds, int)
            or self.max_observation_skew_seconds <= 0
        ):
            raise KernelValueError(
                "comparison_basis.conversion_policy.max_observation_skew は正の長さ"
            )
        if not isinstance(self.symbols, tuple) or not all(
            isinstance(item, SymbolCosts) for item in self.symbols
        ):
            raise KernelValueError("comparison_basis.symbols must be a tuple of SymbolCosts")
        _require_sorted_symbols([item.symbol for item in self.symbols], "comparison_basis.symbols")

    @property
    def conversion_symbol(self) -> str:
        """換算の率を取る通貨ペア（中継の通貨と口座の通貨の組。USD・JPY なら USDJPY。
        D06 §19.3、D17-3a (a)）。"""
        return f"{self.pivot_currency}{self.currency}"

    def costs_of(self, symbol: str) -> SymbolCosts | None:
        """通貨の 4 つの値（無ければ `None`）。"""
        return next((item for item in self.symbols if item.symbol == symbol), None)
