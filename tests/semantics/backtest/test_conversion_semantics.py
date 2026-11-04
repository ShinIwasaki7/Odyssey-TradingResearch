"""換算の先読みの禁止（D06 v1.20 案 §19.4。CT-14）。人工の足と率だけを使う。"""

from __future__ import annotations

from typing import Final

from odyssey_fx.backtest.trace.recorder import TraceTable, flatten_row
from odyssey_fx.common.time import Interval, UtcTime
from tests.fixtures.backtest.conversion import (
    EURUSD,
    USDJPY,
    FakeQuotes,
    minute_rates,
    relabel,
    run_converted,
)
from tests.fixtures.backtest.harness import RunOutput
from tests.fixtures.backtest.paths import RUN_INTERVAL, execution_bars, signal_bars

_SPAN: Final = Interval(
    start=UtcTime.from_components(2026, 1, 6, 6, 0), end=UtcTime.from_components(2026, 1, 6, 13, 0)
)
_CUT: Final = UtcTime.from_components(2026, 1, 6, 10, 0)


def _run(rate: object) -> RunOutput:
    quotes = FakeQuotes(
        symbols=frozenset({USDJPY}),
        bars={USDJPY: minute_rates(USDJPY, _SPAN, rate)},  # type: ignore[arg-type]
    )
    return run_converted(
        symbol=EURUSD,
        signal_bars=relabel(signal_bars(), EURUSD),
        execution_bars=relabel(execution_bars(), EURUSD),
        run_interval=RUN_INTERVAL,
        quotes=quotes,
    )


def _before_cut(output: RunOutput, table: TraceTable, key: str) -> list[dict[str, object]]:
    rows = [flatten_row(row) for row in output.rows(table)]
    return [row for row in rows if str(row[key]) < str(_CUT)]


def test_ct14_rates_after_a_moment_do_not_change_anything_before_it() -> None:
    """CT-14: 処理点より後の USDJPY の足を書き換えても、それより前の数量・損益・資産は同じ。"""
    steady = _run("150")
    changed = _run(lambda moment: "150" if moment.value < _CUT.value else "180")
    for table, key in (
        (TraceTable.LEDGER_SNAPSHOTS, "at_time"),
        (TraceTable.FILLS, "processed_at_time"),
        (TraceTable.EVIDENCE, "at_time"),
        (TraceTable.RISK_ASSESSMENTS, "reference_quote_observed_at"),
    ):
        assert _before_cut(steady, table, key), table
        assert _before_cut(changed, table, key) == _before_cut(steady, table, key), table
    # 後の処理点では率が変わる（書き換えが効いていることの確認）。
    assert [flatten_row(row) for row in changed.rows(TraceTable.LEDGER_SNAPSHOTS)] != [
        flatten_row(row) for row in steady.rows(TraceTable.LEDGER_SNAPSHOTS)
    ]
