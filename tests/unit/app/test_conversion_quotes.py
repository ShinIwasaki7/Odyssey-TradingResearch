"""換算の脚の供給（`SnapshotConversionQuotes`）の能力検査への渡し方（D06 v1.20 案 §19.8）。

人工の値だけを使う。
"""

from __future__ import annotations

from odyssey_fx.app.conversion import SnapshotConversionQuotes
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.ids import SnapshotId
from odyssey_fx.common.refs import SnapshotRef
from tests.fixtures.backtest.conversion import (
    EURUSD,
    MINUTE_REF,
    USDJPY,
    minute_series,
    relabel,
    run_converted,
)
from tests.fixtures.backtest.harness import _digest
from tests.fixtures.backtest.paths import RUN_INTERVAL, execution_bars, signal_bars


def test_a_conversion_pair_without_1m_bars_is_reported_as_a_failed_capability() -> None:
    """換算の snapshot に USDJPY の系列はあるが 1 分足が無い run は、能力検査の結果を残して止まる。

    経路は組める（USDJPY は集合にある）が脚の系列が無い。snapshot の参照は 1 分足の有無によらず
    持つので、検査の途中で例外にならず `FAILED_CAPABILITY` の成果物が残る（D06 §19.8 の 1）。
    """
    ref = SnapshotRef(snapshot_id=SnapshotId(_digest("usdjpy-without-1m")))
    quotes = SnapshotConversionQuotes(
        symbols=frozenset({EURUSD, USDJPY}),
        minutes={},
        fallback_timeframe=MINUTE_REF,
        snapshots={USDJPY: ref},
    )
    assert quotes.snapshot_of(USDJPY) == ref
    output = run_converted(
        symbol=EURUSD,
        signal_bars=relabel(signal_bars(), EURUSD),
        execution_bars=relabel(execution_bars(), EURUSD),
        run_interval=RUN_INTERVAL,
        quotes=quotes,
        present=frozenset(),
    )
    assert output.result.status is RunStatus.FAILED_CAPABILITY
    report = output.manifest.capability_report.conversion
    assert report is not None and report.route_found is True
    (leg,) = report.legs
    assert (leg.series, leg.snapshot_ref, leg.present) == (minute_series(USDJPY), ref, False)
