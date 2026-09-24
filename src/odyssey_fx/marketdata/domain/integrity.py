"""完全性検査の結果（D03 §3.9）。

受入れで行う検査の種別・重大度・結果を表す。重大な違反（`ERROR`）が1件でもあれば受入れを
失敗させる。警告（`WARN`）のうち人間の分類を要するのは、明示集合
（`classification.CLASSIFIABLE_KINDS`: 存在すべき足の欠落と休場帯の足）だけであり、分類は
manifest に残す（D03 §3.9・§4 の 9 v1.7）。他の警告は報告に保存するだけである。

封印期間・未分類の隔離期間の partition に対する検査結果は、**構造情報（件数・区間・種別）
だけ**を含め、価格の統計を含めない（D03 §3.9）。`CheckResult.detail` は文字列のキーと値の
mapping であり、価格を入れないことは `IntegrityReport` の構築時に検査する。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from odyssey_fx.common.time import Interval
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = ["CheckKind", "CheckResult", "IntegrityReport", "Severity"]


class Severity(Enum):
    """検査結果の重大度（D03 §3.9）。

    - `ERROR`: 受入れを失敗させる。
    - `WARN`: 人間の分類・カレンダー修正の候補。
    - `INFO`: 記録のみ。
    """

    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


class CheckKind(Enum):
    """検査の種別と既定の重大度（D03 §3.9）。"""

    #: 同一 `bar_start` の重複行。
    DUPLICATE_TIMESTAMP = "DUPLICATE_TIMESTAMP"
    #: `low <= open/close <= high` の違反、非正の価格。
    OHLC_INCONSISTENT = "OHLC_INCONSISTENT"
    #: オフセットなし、または UTC 以外のオフセット。
    NAIVE_OR_FOREIGN_TZ = "NAIVE_OR_FOREIGN_TZ"
    #: 足の開始が定義の整列に合わない。
    IRREGULAR_INTERVAL = "IRREGULAR_INTERVAL"
    #: カレンダー上存在すべき足の欠落。
    MISSING_EXPECTED_BAR = "MISSING_EXPECTED_BAR"
    #: カレンダー上休場の時間帯に足がある。
    UNEXPECTED_BAR = "UNEXPECTED_BAR"
    #: 同じ時間足で銘柄間の足境界がずれる。
    CROSS_SYMBOL_MISALIGNMENT = "CROSS_SYMBOL_MISALIGNMENT"
    #: `source` の切替点。
    SOURCE_TRANSITION = "SOURCE_TRANSITION"
    #: volume 0 の連続区間。
    ZERO_VOLUME_SPAN = "ZERO_VOLUME_SPAN"
    #: DST 切替週の足数・境界が期待と異なる。
    DST_BOUNDARY_ANOMALY = "DST_BOUNDARY_ANOMALY"


#: 各検査種別の既定の重大度（D03 §3.9 の表）。
DEFAULT_SEVERITY: Mapping[CheckKind, Severity] = MappingProxyType(
    {
        CheckKind.DUPLICATE_TIMESTAMP: Severity.ERROR,
        CheckKind.OHLC_INCONSISTENT: Severity.ERROR,
        CheckKind.NAIVE_OR_FOREIGN_TZ: Severity.ERROR,
        CheckKind.IRREGULAR_INTERVAL: Severity.ERROR,
        CheckKind.MISSING_EXPECTED_BAR: Severity.WARN,
        CheckKind.UNEXPECTED_BAR: Severity.WARN,
        CheckKind.CROSS_SYMBOL_MISALIGNMENT: Severity.WARN,
        CheckKind.SOURCE_TRANSITION: Severity.INFO,
        CheckKind.ZERO_VOLUME_SPAN: Severity.INFO,
        CheckKind.DST_BOUNDARY_ANOMALY: Severity.WARN,
    }
)


def _normalize_detail(detail: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """詳細をキーのコードポイント順に整列した組の列へ正規化する（D03 §3.7.1）。

    `dict` のままだと同値性・ダイジェストが挿入順に左右されうるので、frozen dataclass の
    フィールドとしては整列済みの tuple で保持する。値を文字列に限るのは、価格などの数値を
    そのまま入れて封印期間の統計が漏れることを構造的に防ぐため（D03 §3.9）。
    """
    if not isinstance(detail, Mapping):
        raise MarketDataValueError("CheckResult.detail must be a mapping of str to str")
    items: list[tuple[str, str]] = []
    for key, value in detail.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise MarketDataValueError(
                "CheckResult.detail must map str to str"
                " (structural information only, no price statistics; D03 §3.9)"
            )
        items.append((key, value))
    return tuple(sorted(items, key=lambda pair: pair[0]))


@dataclass(frozen=True, slots=True)
class CheckResult:
    """1件の検査結果（D03 §3.9）。"""

    kind: CheckKind
    severity: Severity
    series: SeriesId
    interval: Interval
    detail: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CheckKind):
            raise MarketDataValueError("CheckResult.kind must be a CheckKind")
        if not isinstance(self.severity, Severity):
            raise MarketDataValueError("CheckResult.severity must be a Severity")
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("CheckResult.series must be a SeriesId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("CheckResult.interval must be an Interval")
        object.__setattr__(self, "detail", _normalize_detail(dict(self.detail)))

    @classmethod
    def create(
        cls,
        kind: CheckKind,
        series: SeriesId,
        interval: Interval,
        *,
        severity: Severity | None = None,
        detail: Mapping[str, str] | None = None,
    ) -> CheckResult:
        """既定の重大度（D03 §3.9 の表）を使って結果を作る。"""
        return cls(
            kind=kind,
            severity=DEFAULT_SEVERITY[kind] if severity is None else severity,
            series=series,
            interval=interval,
            detail=_normalize_detail(detail or {}),
        )

    def detail_canonical(self) -> str:
        """整列鍵に使う詳細の正規化表現（D03 §3.7.1）。"""
        return ";".join(f"{key}={value}" for key, value in self.detail)

    def sort_key(self) -> tuple[str, str, str, str]:
        """D03 §3.7.1 の整列鍵 `(series_id, kind, interval.start, detail の正規化表現)`。"""
        return (
            str(self.series),
            self.kind.value,
            str(self.interval.start),
            self.detail_canonical(),
        )


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """完全性検査の報告（D03 §3.9）。

    `results` は構築時に D03 §3.7.1 の整列鍵で並べ替える。検査の実行順がダイジェストに
    影響しないようにするため（同じ入力なら同じ `snapshot_id` になる、D03 §4）。
    """

    results: tuple[CheckResult, ...] = field(default=())

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple):
            raise MarketDataValueError("IntegrityReport.results must be a tuple")
        for result in self.results:
            if not isinstance(result, CheckResult):
                raise MarketDataValueError("IntegrityReport.results must contain CheckResult")
        normalized = tuple(sorted(self.results, key=lambda result: result.sort_key()))
        if normalized != self.results:
            object.__setattr__(self, "results", normalized)

    def of_severity(self, severity: Severity) -> tuple[CheckResult, ...]:
        """指定した重大度の結果だけを返す。"""
        return tuple(result for result in self.results if result.severity is severity)

    @property
    def errors(self) -> tuple[CheckResult, ...]:
        """受入れを失敗させる結果（D03 §4 の 4）。"""
        return self.of_severity(Severity.ERROR)

    @property
    def warnings(self) -> tuple[CheckResult, ...]:
        """警告（WARN）の結果。分類を要するのはこのうち分類対象の2種別だけ（D03 §3.9）。"""
        return self.of_severity(Severity.WARN)

    def has_errors(self) -> bool:
        """重大な違反が1件でもあるか。"""
        return bool(self.errors)
