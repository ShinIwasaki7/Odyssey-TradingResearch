"""分類と警告の突き合わせ（D03 §3.7・§4 の 9「分類の形式と確定時の検査」、v1.7）。

確定（`acceptance.finalize`）と、読み取り・承認の関門（`snapshot_access.ReadableSnapshot`、
`app.cli` の `data approve`）が**同じ規則**で突き合わせるための純粋関数を置く。片方だけが
検査していると、確定を経ずに組み立てた manifest が読み取り側をすり抜ける。

突き合わせの対象は、**暫定報告（人間が分類の根拠にした報告）と最終報告の和集合**に含まれる
分類対象の警告（`CLASSIFIABLE_KINDS`、D03 §3.9）である。警告は `(kind, series, interval)`
で同一視する（上位足の欠落はカレンダー照合と上位足の生成が詳細違いで2件報告するため）。

分類1件は、種別が同じで系列が対象集合に入り、区間が分類の区間に**完全に含まれる**警告を
すべて分類したものとみなす（`ClassificationDecision.covers`）。確定時の検査（D03 §4）:

1. 分類対象の警告がすべて**ちょうど1件**の分類に含まれる（未分類なし）。
2. 同じ警告を複数の分類が覆わない（競合の拒否）。
3. 対応する警告が1件もない分類を拒否する。
5. 分類が警告を増やすことはない（解決は報告された警告からだけ作る）。
"""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from odyssey_fx.marketdata.domain.classification import (
    CLASSIFIABLE_KINDS,
    ClassificationDecision,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult, IntegrityReport
from odyssey_fx.marketdata.domain.snapshot import SnapshotManifest

__all__ = [
    "ClassificationMatch",
    "WarningKey",
    "classifiable_warnings",
    "match_classifications",
    "require_complete_match",
    "require_recorded_classification",
    "resolved_from",
    "warning_key",
]

#: 警告の同一視に使う鍵 `(kind, series_id 文字列, interval 文字列)`。
WarningKey = tuple[str, str, str]

#: 失敗時のメッセージに列挙する件数の上限。実データでは警告が数万件になりうるので、
#: 全件を1行に詰めず、件数と先頭だけを見せる。
_LISTED = 50


def warning_key(result: CheckResult) -> WarningKey:
    """警告の鍵（詳細 `detail` は含めない）。"""
    return (result.kind.value, str(result.series), str(result.interval))


def _label(key: WarningKey) -> str:
    return " ".join(key)


def classifiable_warnings(reports: Iterable[IntegrityReport]) -> tuple[CheckResult, ...]:
    """報告の和集合から、分類対象の警告を鍵ごとに1件ずつ返す（D03 §3.9 v1.7）。

    並びは鍵の順で、報告を渡す順序に依存しない。
    """
    by_key: dict[WarningKey, CheckResult] = {}
    for report in reports:
        for result in report.warnings:
            if result.kind in CLASSIFIABLE_KINDS:
                by_key.setdefault(warning_key(result), result)
    return tuple(by_key[key] for key in sorted(by_key))


@dataclass(frozen=True, slots=True)
class ClassificationMatch:
    """突き合わせの結果（D03 §4 の確定時の検査 1〜3）。

    - `assignments`: ちょうど1件の分類に含まれた警告と、その分類の組（鍵の順）。
    - `undecided`: どの分類にも含まれない警告の鍵。
    - `conflicting`: 複数の分類に含まれた警告の鍵。
    - `extraneous`: どの警告も含まない分類。
    """

    assignments: tuple[tuple[CheckResult, ClassificationDecision], ...]
    undecided: tuple[WarningKey, ...]
    conflicting: tuple[WarningKey, ...]
    extraneous: tuple[ClassificationDecision, ...]

    @property
    def complete(self) -> bool:
        """未分類・競合・対応なしが1件も無いか。"""
        return not (self.undecided or self.conflicting or self.extraneous)

    def outcomes(self) -> Mapping[WarningKey, str]:
        """警告の鍵から分類結果（文字列）への対応。"""
        return {
            warning_key(result): decision.outcome.value for result, decision in self.assignments
        }


@dataclass(frozen=True, slots=True)
class _Candidates:
    """同じ `(kind, series)` を対象にする分類を、区間の開始順に並べたもの。

    `max_end[i]` は `decisions[0..i]` の終端の最大値。警告を含みうる分類は「開始が警告の
    開始以前」のものに限られ、そのうち終端が警告の終端以上のものだけが含む。開始順に後ろから
    たどり、`max_end` が警告の終端に届かなくなった所で打ち切れる。実データでは警告が数万件、
    分類が数千件になるので、総当たりにしないための索引である。
    """

    starts: list[datetime]
    max_end: list[datetime]
    decisions: list[ClassificationDecision]


def _index(
    decisions: Sequence[ClassificationDecision],
) -> dict[tuple[CheckKind, str], _Candidates]:
    grouped: dict[tuple[CheckKind, str], list[ClassificationDecision]] = {}
    for decision in decisions:
        for series in decision.series:
            grouped.setdefault((decision.kind, str(series)), []).append(decision)
    index: dict[tuple[CheckKind, str], _Candidates] = {}
    for key, items in grouped.items():
        ordered = sorted(items, key=lambda item: item.interval.start.value)
        max_end: list[datetime] = []
        for item in ordered:
            end = item.interval.end.value
            max_end.append(end if not max_end else max(max_end[-1], end))
        index[key] = _Candidates(
            starts=[item.interval.start.value for item in ordered],
            max_end=max_end,
            decisions=ordered,
        )
    return index


def match_classifications(
    reports: Sequence[IntegrityReport], decisions: Sequence[ClassificationDecision]
) -> ClassificationMatch:
    """分類を報告の和集合の分類対象の警告に突き合わせる（D03 §4 の確定時の検査 1〜3）。"""
    for decision in decisions:
        if not isinstance(decision, ClassificationDecision):
            raise MarketDataValueError(
                "match_classifications requires ClassificationDecision values"
            )
    index = _index(decisions)
    used: set[int] = set()
    assignments: list[tuple[CheckResult, ClassificationDecision]] = []
    undecided: list[WarningKey] = []
    conflicting: list[WarningKey] = []
    for result in classifiable_warnings(reports):
        candidates = index.get((result.kind, str(result.series)))
        covering: list[ClassificationDecision] = []
        if candidates is not None:
            start = result.interval.start.value
            end = result.interval.end.value
            position = bisect.bisect_right(candidates.starts, start) - 1
            while position >= 0 and candidates.max_end[position] >= end:
                decision = candidates.decisions[position]
                if decision.covers(result.kind, result.series, result.interval):
                    covering.append(decision)
                position -= 1
        for decision in covering:
            used.add(id(decision))
        if not covering:
            undecided.append(warning_key(result))
        elif len(covering) > 1:
            conflicting.append(warning_key(result))
        else:
            assignments.append((result, covering[0]))
    extraneous = tuple(decision for decision in decisions if id(decision) not in used)
    return ClassificationMatch(
        assignments=tuple(assignments),
        undecided=tuple(undecided),
        conflicting=tuple(conflicting),
        extraneous=tuple(sorted(extraneous, key=lambda decision: decision.sort_key())),
    )


def _listed(labels: Sequence[str]) -> str:
    shown = list(labels[:_LISTED])
    rest = len(labels) - len(shown)
    return f"{shown}" + (f" (and {rest} more)" if rest > 0 else "")


def require_complete_match(match: ClassificationMatch, *, error: type[Exception]) -> None:
    """未分類・競合・対応なしが1件でもあれば、列挙して `error` を送出する（D03 §4）。

    再実行でカレンダーの修正が新たな警告を生んだ場合も、ここで「未分類の警告」として
    列挙される（D03 §4 の反復的な分類）。人間はそれを分類ファイルに追記して、同じ暫定
    snapshot に対して確定をやり直す。
    """
    problems: list[str] = []
    if match.undecided:
        problems.append(
            f"{len(match.undecided)} warning(s) that require a classification are still"
            f" unclassified: {_listed([_label(key) for key in match.undecided])}"
        )
    if match.conflicting:
        problems.append(
            f"{len(match.conflicting)} warning(s) are covered by more than one classification"
            f" (conflicting classifications): "
            f"{_listed([_label(key) for key in match.conflicting])}"
        )
    if match.extraneous:
        problems.append(
            f"{len(match.extraneous)} classification(s) do not correspond to any reported"
            " warning of the provisional or the final report: "
            f"{_listed([decision.label() for decision in match.extraneous])}"
        )
    if problems:
        raise error("; ".join(problems) + " (D03 §4 の 9)")


def require_recorded_classification(
    manifest: SnapshotManifest,
    report: IntegrityReport,
    provisional_report: IntegrityReport,
    *,
    error: type[Exception],
) -> None:
    """manifest の分類が2つの報告と整合することを確かめる（D03 §4 の 9、読み取り関門）。

    1. 記入された分類（`closure_decisions`）が、暫定報告と最終報告の和集合の分類対象の
       警告に過不足なく対応する（未分類・競合・対応なしが無い）。
    2. 解決済みの分類（`resolved_classifications`）が、その突き合わせから導いた「警告ごとの
       分類結果」と一致する（警告の集合も結果も同じ）。

    除外した足の件数（`excluded_bar_count`）は足の実体が無いと再計算できないので、ここでは
    見ない（件数と結果の整合は `ResolvedClassification` の構築時に検査している）。
    """
    match = match_classifications((provisional_report, report), manifest.closure_decisions)
    require_complete_match(match, error=error)
    expected = match.outcomes()
    recorded = {
        resolved.warning_key(): resolved.outcome.value
        for resolved in manifest.resolved_classifications
    }
    if expected != recorded:
        missing = sorted(set(expected) - set(recorded))
        unexpected = sorted(set(recorded) - set(expected))
        differing = sorted(
            key for key in set(expected) & set(recorded) if expected[key] != recorded[key]
        )
        raise error(
            "the resolved classifications recorded in the manifest do not match the"
            " classifications applied to the reports:"
            f" missing {_listed([_label(key) for key in missing])},"
            f" unexpected {_listed([_label(key) for key in unexpected])},"
            f" different outcome {_listed([_label(key) for key in differing])} (D03 §4 の 9)"
        )


def resolved_from(
    match: ClassificationMatch, excluded_counts: Mapping[WarningKey, int]
) -> tuple[ResolvedClassification, ...]:
    """突き合わせの結果から、警告1件ごとの解決済み分類を作る（D03 §3.7）。"""
    return tuple(
        ResolvedClassification(
            kind=result.kind,
            series_id=result.series,
            interval=result.interval,
            outcome=decision.outcome,
            excluded_bar_count=excluded_counts.get(warning_key(result), 0),
        )
        for result, decision in match.assignments
    )
