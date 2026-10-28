"""探索の実験のレポートの表示の細部（段階5 実装 PR 5 の仮置きへの決定。D09 §17.7.5）。

- 期間は「12日3時間15分」のように単位を明示した表記で出し、保存値は変えない（§17.7.5 の7、
  D09 §11.5 の表の下の注記）。
- 能力検査で止まった run の欠落は、その run を止めた能力検査の対象系列（執行系列と解像度階層の
  各段）について run 区間と重なるものを全件出し、無関係な系列の欠落は出さない（§17.7.5 の6、
  D09 §11.5 の順2・§7.11、D06 §10.5 の手順2）。
- 系譜の先行の実行が既に見た検証区間と、この実行の検証区間が重なるときだけ、判定の欄に
  「独立した確認ではない」の注記を出す（D13 §7.3、D14 §7.2 の E5）。台帳の開始の行と照合
  できないときは、注記の有無を決められない旨を出す。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters import search_report
from odyssey_fx.evaluation.domain.search import (
    ChangeKind,
    Lineage,
    LineageSource,
    TrialPhase,
    TrialUnitKey,
)
from odyssey_fx.evaluation.domain.variant import Variant
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult
from tests.fixtures.evaluation import trial_ledger
from tests.fixtures.synthetic.market import USDJPY, series


@pytest.mark.parametrize(
    ("duration", "shown"),
    [
        (timedelta(days=12, hours=3, minutes=15), "12日3時間15分"),
        (timedelta(days=7), "7日"),
        (timedelta(hours=5, seconds=30), "5時間30秒"),
        (timedelta(0), "0秒"),
        (timedelta(seconds=1, microseconds=500_000), "1.5秒"),
    ],
)
def test_a_period_is_shown_with_explicit_units(duration: timedelta, shown: str) -> None:
    assert search_report._days(duration) == shown


def _interval(start: str, end: str) -> Interval:
    return Interval(start=UtcTime.parse(start), end=UtcTime.parse(end))


def test_the_gaps_shown_are_those_of_the_series_the_capability_check_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = series(USDJPY, "1h")
    child = series(USDJPY, "15m")
    unrelated = series(Symbol("EURUSD"), "1h")
    run_interval = _interval("2015-01-05T22:00:00Z", "2015-01-09T22:00:00Z")
    inside_a = _interval("2015-01-06T01:00:00Z", "2015-01-06T02:00:00Z")
    inside_b = _interval("2015-01-07T03:00:00Z", "2015-01-07T03:15:00Z")
    outside = _interval("2015-01-12T01:00:00Z", "2015-01-12T02:00:00Z")
    results = (
        CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, execution, inside_a),
        CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, execution, outside),
        CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, child, inside_b),
        CheckResult.create(CheckKind.MISSING_EXPECTED_BAR, unrelated, inside_a),
    )
    manifest = SimpleNamespace(
        config=SimpleNamespace(run_interval=run_interval, execution_series=execution),
        resolution_hierarchy=SimpleNamespace(levels=(execution, child)),
        capability_report=SimpleNamespace(integrity=SimpleNamespace(results=results)),
    )

    class _Repository:
        def __init__(self, root: Path) -> None:
            self.root = root

        def read_manifest(self, run_id: object) -> Any:
            return manifest

    monkeypatch.setattr(search_report, "FileSystemResultRepository", _Repository)
    inputs = SimpleNamespace(artifacts_root=Path("/nowhere"), roots=())
    record = SimpleNamespace(
        run_id=SimpleNamespace(hex="ab" * 32),
        unit=TrialUnitKey(
            variant=Variant.STANDARD, fold_index=0, phase=TrialPhase.VALIDATION, trial_index=0
        ),
    )

    lines = search_report._capability_lines(cast(Any, inputs), cast(Any, record))

    assert "欠落の区間 2 件" in lines[0]
    assert f"{execution}" in lines[0] and f"{child}" in lines[0]
    assert f"{unrelated}" not in "\n".join(lines)
    assert lines[1:] == [f"  - {execution}: {inside_a}", f"  - {child}: {inside_b}"]


# --- 「独立した確認ではない」の注記（D13 §7.3）------------------------------------------------

_NOT_INDEPENDENT = (
    "この判定の検証区間は、系譜の先行の実行が既に見た区間を含む（独立した確認ではない）"
)


def _ledger_view(lineage: Lineage | None) -> Any:
    """台帳の開始の行に `lineage` を持つ照合の結果（R7）。検証区間は台帳の部品の既定の1区間。"""
    entry = replace(trial_ledger.started("derived"), lineage=lineage)
    (line,) = trial_ledger.chain([entry])
    return search_report._LedgerView("R7", "", None, None, line)


def _derived(viewed_before: tuple[Interval, ...]) -> Lineage:
    source = LineageSource(experiment_id=trial_ledger.experiment_id("origin"), execution=1)
    return Lineage(
        derived_from=source,
        change_kinds=(ChangeKind.PARAMETERS,),
        viewed_before=viewed_before,
        note_digest=None,
    )


def test_the_note_is_shown_when_a_predecessor_saw_the_validation_interval() -> None:
    """系譜の先行の実行が同じ検証区間を見ていれば（改善後の実験）、注記を出す。"""
    (validation,) = trial_ledger.started().validation_intervals
    assert search_report._lineage_note(_ledger_view(_derived((validation,)))) == _NOT_INDEPENDENT
    partly = _interval("2015-01-12T00:00:00Z", "2015-01-20T22:00:00Z")
    assert search_report._lineage_note(_ledger_view(_derived((partly,)))) == _NOT_INDEPENDENT


@pytest.mark.parametrize(
    "lineage",
    [
        pytest.param(None, id="no-lineage"),
        pytest.param(
            Lineage(
                derived_from=None,
                change_kinds=(ChangeKind.HYPOTHESIS,),
                viewed_before=(),
                note_digest=None,
            ),
            id="derived-from-none",
        ),
        pytest.param(
            _derived((_interval("2015-01-05T22:00:00Z", "2015-01-08T22:00:00Z"),)),
            id="touching-only",
        ),
    ],
)
def test_the_note_is_not_shown_without_a_seen_interval(lineage: Lineage | None) -> None:
    """系譜が無い（元の実験）・元の実行が無い・見た区間が接するだけなら、注記を出さない。"""
    assert search_report._lineage_note(_ledger_view(lineage)) is None


def test_the_note_cannot_be_decided_without_the_started_line() -> None:
    """台帳の開始の行と照合できない（R3・R5・R6）なら、その旨を出す。事前検査で止めた実行
    （R1。台帳に書かない）は何も出さない。"""
    for case in ("R3", "R5", "R6"):
        view = search_report._LedgerView(case, "", None, None, None)
        note = search_report._lineage_note(view)
        assert note is not None and "照合できない" in note and "独立した確認ではない" not in note
    rejected = search_report._LedgerView("R1", "", None, None, None)
    assert search_report._lineage_note(rejected) is None
