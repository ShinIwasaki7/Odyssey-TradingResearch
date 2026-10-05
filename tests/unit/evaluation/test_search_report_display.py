"""探索の実験のレポートの表示の細部（段階5 実装 PR 5 の仮置きへの決定。D09 §17.7.5）。

- 期間は「12日3時間15分」のように単位を明示した表記で出し、保存値は変えない（§17.7.5 の7、
  D09 §11.5 の表の下の注記）。
- 能力検査で止まった run の欠落は、その run を止めた能力検査の対象系列（執行系列と解像度階層の
  各段）について run 区間と重なるものを全件出し、無関係な系列の欠落は出さない（§17.7.5 の6、
  D09 §11.5 の順2・§7.11、D06 §10.5 の手順2）。
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters import search_report
from odyssey_fx.evaluation.domain.search import TrialPhase, TrialUnitKey
from odyssey_fx.marketdata.domain.integrity import CheckKind, CheckResult
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
        unit=TrialUnitKey(fold_index=0, phase=TrialPhase.VALIDATION, trial_index=0),
    )

    lines = search_report._capability_lines(cast(Any, inputs), cast(Any, record))

    assert "欠落の区間 2 件" in lines[0]
    assert f"{execution}" in lines[0] and f"{child}" in lines[0]
    assert f"{unrelated}" not in "\n".join(lines)
    assert lines[1:] == [f"  - {execution}: {inside_a}", f"  - {child}: {inside_b}"]
