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
    "FixedComparisonBasis",
    "PolicyBasis",
    "PremiseFile",
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

_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")
_SERIES: Final = re.compile(r"[A-Z0-9]+/[0-9A-Za-z_]+/(bid|ask|mid)")
_CURRENCY: Final = re.compile(r"[A-Z]{3}")
_SYMBOL: Final = re.compile(r"[A-Z0-9]+")


@dataclass(frozen=True, slots=True)
class PremiseFile:
    """前提の規則のファイル1件（D13 §6.4 の `basis.premises`）。版の無いものは `version = None`。"""

    name: str
    version: int | None
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if self.name not in PREMISE_NAMES:
            raise KernelValueError(
                f"前提のファイルの名前 {self.name!r} は {sorted(PREMISE_NAMES)} のどれか"
                "（D13 §6.4。実装 PR の仮置き 51）"
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
