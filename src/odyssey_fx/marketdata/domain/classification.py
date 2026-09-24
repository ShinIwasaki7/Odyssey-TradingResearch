"""人間の分類の語彙と記録（D03 §3.7・§3.9・§4 の 9、v1.7）。

受入れの検査が出した警告のうち、**人間の分類を必須とするのは明示集合
`CLASSIFIABLE_KINDS` の2種別だけ**である（D03 §3.9 v1.7）。種別ごとに許される分類結果を
分け、組合せの誤りは構築時に拒否する。

- 存在すべき足の欠落（`MISSING_EXPECTED_BAR`）: 休場（`CLOSURE`）/ データ欠損
  （`DATA_GAP`）。
- 休場帯の足（`UNEXPECTED_BAR`）: 営業例外（`CALENDAR_EXCEPTION`）/ セッション外データ
  異常（`OUT_OF_SESSION_DATA`）。

記録は2種類ある（D03 §3.7）。

- `ClassificationDecision`: 人間が書いた分類1件（manifest の `closure_decisions` の要素）。
  「検査種別 × 区間 × 対象系列」でまとめて宣言でき、その区間に**完全に含まれる**同種別の
  警告（対象系列のもの）をすべて分類したものとみなす。記入されたままの記録は監査用であり、
  `snapshot_id` の計算対象外。
- `ResolvedClassification`: 分類を警告1件ごとに解決した記録（manifest の
  `resolved_classifications` の要素）。`snapshot_id` の計算対象であり、「分類の正規化内容」
  とはこの列を指す。除外した足の件数（`excluded_bar_count`）は確定時に計算してここにだけ
  持つ。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from odyssey_fx.common.time import Interval
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "ALLOWED_OUTCOMES",
    "CLASSIFIABLE_KINDS",
    "CALENDAR_CHANGING_OUTCOMES",
    "ClassificationDecision",
    "ClassificationOutcome",
    "ResolvedClassification",
]


class ClassificationOutcome(Enum):
    """警告に対する人間の分類結果（D03 §3.9 v1.7）。

    - `CLOSURE`: 休場だった。カレンダーの `closures` へ追加して版を上げる。
    - `DATA_GAP`: データ欠損。そのまま欠損として扱う。
    - `CALENDAR_EXCEPTION`: カレンダー側の営業例外。`openings` へ追加して版を上げる。
    - `OUT_OF_SESSION_DATA`: セッション外データ異常。確定段階の再実行で足を除外する
      （D03 §4 の除外規則）。
    """

    CLOSURE = "CLOSURE"
    DATA_GAP = "DATA_GAP"
    CALENDAR_EXCEPTION = "CALENDAR_EXCEPTION"
    OUT_OF_SESSION_DATA = "OUT_OF_SESSION_DATA"


#: 人間の分類を必須とする検査種別の明示集合（D03 §3.9 v1.7）。集合の変更は D03 の改訂を
#: 伴う。他の警告（銘柄間の境界ずれ、夏時間の異常）は検査報告に保存するだけで、確定・承認・
#: 読み取りの条件にしない。
CLASSIFIABLE_KINDS: frozenset[CheckKind] = frozenset(
    {CheckKind.MISSING_EXPECTED_BAR, CheckKind.UNEXPECTED_BAR}
)

#: 検査種別ごとに許される分類結果（D03 §3.9 の表）。
ALLOWED_OUTCOMES: Mapping[CheckKind, frozenset[ClassificationOutcome]] = MappingProxyType(
    {
        CheckKind.MISSING_EXPECTED_BAR: frozenset(
            {ClassificationOutcome.CLOSURE, ClassificationOutcome.DATA_GAP}
        ),
        CheckKind.UNEXPECTED_BAR: frozenset(
            {
                ClassificationOutcome.CALENDAR_EXCEPTION,
                ClassificationOutcome.OUT_OF_SESSION_DATA,
            }
        ),
    }
)

#: カレンダーへ宣言を追加して版を上げる分類結果（D03 §3.9 の表）。
CALENDAR_CHANGING_OUTCOMES: frozenset[ClassificationOutcome] = frozenset(
    {ClassificationOutcome.CLOSURE, ClassificationOutcome.CALENDAR_EXCEPTION}
)


def _require_kind_and_outcome(kind: object, outcome: object, label: str) -> None:
    """検査種別が分類対象で、分類結果がその種別に許されることを確かめる（D03 §3.9）。"""
    if not isinstance(kind, CheckKind):
        raise MarketDataValueError(f"{label}.kind must be a CheckKind")
    if kind not in CLASSIFIABLE_KINDS:
        raise MarketDataValueError(
            f"{label}.kind {kind.value} does not require a human classification;"
            f" only {sorted(item.value for item in CLASSIFIABLE_KINDS)} are classified"
            " (D03 §3.9)"
        )
    if not isinstance(outcome, ClassificationOutcome):
        raise MarketDataValueError(f"{label}.outcome must be a ClassificationOutcome")
    allowed = ALLOWED_OUTCOMES[kind]
    if outcome not in allowed:
        raise MarketDataValueError(
            f"{label}: {outcome.value} is not an outcome for {kind.value};"
            f" allowed are {sorted(item.value for item in allowed)} (D03 §3.9)"
        )


@dataclass(frozen=True, slots=True)
class ClassificationDecision:
    """人間が書いた分類1件（D03 §3.7 の `closure_decisions` の要素、§4 の 9）。

    `series` は対象系列の**解決済みの明示集合**である。分類ファイルでは「全系列」（`all`）と
    書けるが、確定時に snapshot 内の具体的な `SeriesId` 集合へ解決して保存する（D03 §4 の
    確定時の検査4）。構築時に系列の文字列順へ整列し、重複と空集合を拒否する。

    `calendar_ref` は、この分類が参照するカレンダーの新版（任意）。

    **`snapshot_id` の計算対象外**（D03 §3.7.1）。同じ警告集合を1件の広い区間で書いても、
    複数の狭い区間で書いても識別子が変わらないようにするためで、識別子に入るのは警告1件
    ごとに解決した `ResolvedClassification` である。
    """

    kind: CheckKind
    interval: Interval
    series: tuple[SeriesId, ...]
    outcome: ClassificationOutcome
    note: str = ""
    calendar_ref: str | None = None

    def __post_init__(self) -> None:
        _require_kind_and_outcome(self.kind, self.outcome, "ClassificationDecision")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("ClassificationDecision.interval must be an Interval")
        if not isinstance(self.series, tuple) or not self.series:
            raise MarketDataValueError(
                "ClassificationDecision.series must be a non-empty tuple of SeriesId"
            )
        for series in self.series:
            if not isinstance(series, SeriesId):
                raise MarketDataValueError("ClassificationDecision.series must contain SeriesId")
        if len(set(self.series)) != len(self.series):
            listed = [str(series) for series in self.series]
            raise MarketDataValueError(
                f"ClassificationDecision.series lists a series twice: {listed}"
            )
        normalized = tuple(sorted(self.series, key=str))
        if normalized != self.series:
            object.__setattr__(self, "series", normalized)
        if not isinstance(self.note, str):
            raise MarketDataValueError("ClassificationDecision.note must be a str")
        if self.calendar_ref is not None and (
            not isinstance(self.calendar_ref, str) or not self.calendar_ref
        ):
            raise MarketDataValueError(
                "ClassificationDecision.calendar_ref must be a non-empty str or None"
            )

    def covers(self, kind: CheckKind, series: SeriesId, interval: Interval) -> bool:
        """この分類が、指定した警告を分類するか（D03 §4 の 9）。

        種別が同じで、系列が対象集合に入り、警告の区間が分類の区間に**完全に含まれる**とき。
        """
        return (
            kind is self.kind
            and series in self.series
            and self.interval.start <= interval.start
            and interval.end <= self.interval.end
        )

    def sort_key(self) -> tuple[str, str, str, tuple[str, ...], str]:
        """D03 §3.7.1 の保存時の整列鍵。

        `(kind, interval.start, interval.end, series 文字列の整列済み列, outcome)`。
        """
        return (
            self.kind.value,
            str(self.interval.start),
            str(self.interval.end),
            tuple(str(series) for series in self.series),
            self.outcome.value,
        )

    def label(self) -> str:
        """人間向けの1行表現（失敗時の列挙に使う）。"""
        targets = ",".join(str(series) for series in self.series)
        return f"{self.kind.value} {self.interval} [{targets}] -> {self.outcome.value}"


@dataclass(frozen=True, slots=True)
class ResolvedClassification:
    """分類を警告1件ごとに解決した記録（D03 §3.7 の `resolved_classifications` の要素）。

    `(kind, series_id, interval, outcome, excluded_bar_count)`。`interval` は警告の区間。
    `snapshot_id` の計算対象である。

    `excluded_bar_count` はセッション外データ異常（`OUT_OF_SESSION_DATA`）として原系列から
    除外した足の件数で、**確定時に計算してこの型にだけ持つ**（人間が書く
    `ClassificationDecision` には持たせない、D03 §4 の除外規則）。除外した区間は同じ要素の
    警告区間で表す。除外は `OUT_OF_SESSION_DATA` にしか起きないので、他の結果では 0 に
    限る。
    """

    kind: CheckKind
    series_id: SeriesId
    interval: Interval
    outcome: ClassificationOutcome
    excluded_bar_count: int = 0

    def __post_init__(self) -> None:
        _require_kind_and_outcome(self.kind, self.outcome, "ResolvedClassification")
        if not isinstance(self.series_id, SeriesId):
            raise MarketDataValueError("ResolvedClassification.series_id must be a SeriesId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("ResolvedClassification.interval must be an Interval")
        count = self.excluded_bar_count
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise MarketDataValueError(
                f"ResolvedClassification.excluded_bar_count must be an int >= 0, got {count!r}"
            )
        if count and self.outcome is not ClassificationOutcome.OUT_OF_SESSION_DATA:
            raise MarketDataValueError(
                f"only OUT_OF_SESSION_DATA excludes bars, but {self.outcome.value} records"
                f" {count} excluded bar(s) (D03 §4)"
            )

    def warning_key(self) -> tuple[str, str, str]:
        """対応する警告の鍵 `(kind, series_id 文字列, interval 文字列)`。"""
        return (self.kind.value, str(self.series_id), str(self.interval))

    def sort_key(self) -> tuple[str, str, str, str, str]:
        """D03 §3.7.1 の整列鍵 `(kind, series_id 文字列, interval.start, outcome)`。

        末尾に `interval.end` を足すのは、万一の同順位で並びが入力順に左右されないように
        するための決め手である（D03 の鍵の順序は変えない）。
        """
        return (
            self.kind.value,
            str(self.series_id),
            str(self.interval.start),
            self.outcome.value,
            str(self.interval.end),
        )
