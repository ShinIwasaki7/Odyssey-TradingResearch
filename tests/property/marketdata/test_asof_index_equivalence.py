"""as-of ビューと執行系列ビューの索引が、全足の走査と同じ答えを返すことのプロパティテスト。

as-of ビュー（`AsOfView`）は、系列の足を開始時刻で引く索引と各足の利用可能時刻を系列ごとに
1度だけ作り、判断時刻ごとの読み取りはその索引を引く。執行系列ビュー（`ExecutionSeriesView`）
も同じく、足の列と開始時刻の索引を構築時に1度だけ作る。**高速化のための変更であり、答えは
変えない**。ここでは、読み取りのたびに全足を先頭から走査していた従来の手順を参照実装として
並べ、同じ入力で同じ答え（足・欠損の理由・構造エラーの型と文言）になることを確かめる。

入力には、答えが食い違いやすい形を混ぜる。

- 足の欠損（カレンダー上存在すべき足を間引く）。
- 1本ごとの公開遅延（遅延シナリオの `InjectedBarDelay`）。利用可能時刻が時刻順に単調で
  なくなり、「見えている足は先頭からの連続区間」とは言えなくなる。
- 本数窓と経過時間窓、末尾のずらし（`end_offset_bars`）、基準の足を指定した窓、過去値への
  遡り（`previous_available`）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import (
    AsOfView,
    BarsWindow,
    DurationWindow,
    ExecutionSeriesView,
    HistoryWindowLike,
)
from odyssey_fx.marketdata.application.publication import build_publication_log
from odyssey_fx.marketdata.application.snapshot_access import PartitionedBars
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, BarKey
from odyssey_fx.marketdata.domain.schedule import DelayScenario, InjectedBarDelay, SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market, snapshots

HOURLY = market.series()
CALENDAR = market.calendar()
PARTITION = PartitionId(series=HOURLY, access_class=AccessClass.RESEARCH_HISTORY)
SCHEDULE = SeriesSchedule(series=HOURLY, timeframe_def=market.TF_1H, calendar=CALENDAR)
SCHEDULES = {HOURLY: SCHEDULE}

WINDOW = Interval(
    start=UtcTime.parse("2026-01-12T22:00:00Z"), end=UtcTime.parse("2026-01-16T22:00:00Z")
)
ALL_BARS = market.make_bars(HOURLY, market.TF_1H, CALENDAR, WINDOW)
STARTS = tuple(bar.bar_start for bar in ALL_BARS)


class _ScanningVisibility:
    """従来の手順: 読み取りのたびに系列の全足を走査し、`available_at <= at` の足を残す。"""

    def __init__(self, view: AsOfView, series: SeriesId) -> None:
        self._view = view
        self._series = series

    def _visible(self, at: UtcTime) -> tuple[Bar, ...]:
        bars = PartitionedBars(self._view.partition_bars, self._view.allowed_partitions)
        return tuple(
            bar
            for bar in bars.require_series(self._series)
            if self._view._available_at(bar) <= at  # noqa: SLF001 - 参照実装のため
        )

    def first_visible(self, bar_start: UtcTime, at: UtcTime) -> Bar | None:
        for bar in self._visible(at):
            if bar.bar_start == bar_start:
                return bar
        return None

    def last_visible(self, bar_start: UtcTime, at: UtcTime) -> Bar | None:
        return {bar.bar_start: bar for bar in self._visible(at)}.get(bar_start)


class _ScanningAsOfView(AsOfView):
    """索引の代わりに従来の走査で答える参照実装。"""

    def _series_visibility(self, series: SeriesId) -> _ScanningVisibility:  # type: ignore[override]
        return _ScanningVisibility(self, series)


def _outcome(operation: Callable[[AsOfView], object], view: AsOfView) -> tuple[str, object]:
    """呼び出しの結果を、値か「例外の型と文言」として比べられる形にする。"""
    try:
        return ("value", operation(view))
    except Exception as exc:  # noqa: BLE001 - 例外も答えの一部として比べる
        return ("error", (type(exc).__name__, str(exc)))


_windows: st.SearchStrategy[HistoryWindowLike] = st.one_of(
    st.integers(min_value=1, max_value=8).map(lambda count: BarsWindow(count=count)),
    st.integers(min_value=1, max_value=10).map(
        lambda hours: DurationWindow(duration=timedelta(hours=hours))
    ),
)


@st.composite
def _cases(
    draw: st.DrawFn,
) -> tuple[AsOfView, AsOfView, UtcTime, HistoryWindowLike, int, UtcTime]:
    skipped = draw(st.sets(st.sampled_from(STARTS[1:]), max_size=12))
    bars = tuple(bar for bar in ALL_BARS if bar.bar_start not in skipped)
    delayed = draw(st.sets(st.sampled_from(STARTS), max_size=12))
    rules = tuple(
        InjectedBarDelay(
            series=HOURLY,
            bar_start=start,
            delay=timedelta(minutes=draw(st.integers(min_value=0, max_value=300))),
        )
        for start in sorted(delayed, key=lambda value: value.value)
    )
    partition_bars = {PARTITION: bars}
    snapshot = snapshots.readable_for(partition_bars)
    allowed = frozenset({PARTITION})
    log = build_publication_log(
        snapshot,
        allowed,
        partition_bars,
        SCHEDULES,
        DelayScenario(id="equivalence", version=1, rules=rules),
    )

    def make(kind: type[AsOfView]) -> AsOfView:
        return kind(
            snapshot=snapshot,
            allowed_partitions=allowed,
            schedules=SCHEDULES,
            partition_bars=partition_bars,
            publication_log=log,
        )

    at = WINDOW.start + timedelta(minutes=draw(st.integers(min_value=0, max_value=4 * 24 * 60)))
    base = draw(st.sampled_from(STARTS))
    return (
        make(AsOfView),
        make(_ScanningAsOfView),
        at,
        draw(_windows),
        draw(st.integers(min_value=0, max_value=2)),
        base,
    )


@given(_cases())
@settings(max_examples=150, deadline=None)
def test_the_indexed_view_answers_like_the_full_scan(
    case: tuple[AsOfView, AsOfView, UtcTime, HistoryWindowLike, int, UtcTime],
) -> None:
    """公開操作のすべてで、索引を引くビューと全足を走査するビューの答えが一致する。"""
    indexed, scanning, at, window, offset, base = case
    operations: tuple[Callable[[AsOfView], object], ...] = (
        lambda view: view.latest_available(HOURLY, at),
        lambda view: view.bar(HOURLY, base, at),
        lambda view: view.history(HOURLY, window, at, end_offset_bars=offset),
        lambda view: view.history_ending_at(HOURLY, window, base, at, end_offset_bars=offset),
        lambda view: view.previous_available(HOURLY, base, at, max_lookback=window),
    )
    for operation in operations:
        assert _outcome(operation, indexed) == _outcome(operation, scanning)


@st.composite
def _execution_cases(draw: st.DrawFn) -> tuple[tuple[Bar, ...], ExecutionSeriesView, UtcTime]:
    skipped = draw(st.sets(st.sampled_from(STARTS), max_size=20))
    bars = tuple(bar for bar in ALL_BARS if bar.bar_start not in skipped)
    if not bars:
        bars = ALL_BARS[:1]
    partition_bars = {PARTITION: bars}
    view = ExecutionSeriesView(
        snapshot=snapshots.readable_for(partition_bars),
        series=HOURLY,
        allowed_partitions=frozenset({PARTITION}),
        partition_bars=partition_bars,
        schedule=SCHEDULE,
    )
    moment = (WINDOW.start - timedelta(hours=2)) + timedelta(
        minutes=draw(st.integers(min_value=0, max_value=5 * 24 * 60))
    )
    return bars, view, moment


@given(_execution_cases(), st.sampled_from(STARTS))
@settings(max_examples=150, deadline=None)
def test_the_execution_series_index_answers_like_the_full_scan(
    case: tuple[tuple[Bar, ...], ExecutionSeriesView, UtcTime], start: UtcTime
) -> None:
    """執行系列ビューの足の参照と「次の足」が、全足を走査した答えと一致する。"""
    bars, view, moment = case
    key = BarKey(series=HOURLY, bar_start=start)
    expected_bar = next((bar for bar in bars if bar.bar_start == start), None)
    assert view.bar(key) == expected_bar
    assert view.open_of(key) == (None if expected_bar is None else expected_bar.open)
    expected_next = next((bar.key for bar in bars if moment < bar.bar_start), None)
    assert view.next_bar_key_after(moment) == expected_next
    # 開始時刻ちょうどの時刻は「より後」に含まれない（`moment < bar_start`）。
    exact = next((bar.key for bar in bars if start < bar.bar_start), None)
    assert view.next_bar_key_after(start) == exact
