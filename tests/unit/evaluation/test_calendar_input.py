"""評価が受け取る取引カレンダーの版参照（D07 §4.1 v2.0、§10.4 の C10）。

run manifest の `calendar_ref` は合成（`app.composition.calendar_ref_of`）が書く。評価は
`app` を import できない（契約 L1）ので同じ書き方を `evaluate_run.calendar_ref_text` に
置いている。2つの書き方がずれると、正しいカレンダーを渡しても C10 が不合格になる。
"""

from __future__ import annotations

from odyssey_fx.app.composition import calendar_ref_of
from odyssey_fx.evaluation.application.evaluate_run import calendar_ref_text
from tests.fixtures.backtest.harness import calendar_ref_of as harness_calendar_ref_of
from tests.fixtures.synthetic import market


def test_the_evaluation_writes_the_calendar_reference_like_the_run() -> None:
    """評価と合成が同じ `"<id>@v<version>"` を作る（D06 §9.3）。"""
    for calendar in (market.calendar(), market.calendar(version=3)):
        assert calendar_ref_text(calendar) == calendar_ref_of(calendar)
        assert calendar_ref_text(calendar) == harness_calendar_ref_of(calendar)
