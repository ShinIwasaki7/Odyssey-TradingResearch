"""配布 tick 記録を再現するモデルの受入検証のうち、市場データの側（D12 §11.13 の C-1・C-2・C-7）。

人工の 1 分足（2026-01-04〜01-30。API の時刻 = UTC + 2 時間）を本番の受入れで snapshot にし、本番の
as-of ビュー・公開フィード・`build_mt5_run_parts` で読む。値は人工。

- 休場: カレンダーで 2026-01-14 の取引日を休場と宣言する（`[01-13 22:00Z, 01-14 22:00Z)`）。
- 予定取引日の全日無記録: 2026-01-21 の取引日（`[01-20 22:00Z, 01-21 22:00Z)`）の 1 分足を作らない。
- 部分観測の日: 2026-01-08 の取引日に 10:00〜10:07Z と 11:00〜13:00Z の無記録（127 分）。
- 公開時刻の確認: 2026-01-29 の取引日は 18:00Z 以後が無記録（最後の観測は 17:59Z）。

日足の窓は 3 本（60 本は現在の戦略の必要履歴で、規則は本数を持たない。D12-88）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from odyssey_fx.app.mt5_run import build_mt5_run_parts, strategy_reads
from odyssey_fx.common.reason import MissingInputReason
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.acceptance import PendingSnapshot
from odyssey_fx.marketdata.application.asof import AsOfView, BarsWindow, MissingInput
from odyssey_fx.marketdata.application.mt5_observation import Mt5Observation
from odyssey_fx.marketdata.application.publication import (
    PublicationKind,
    build_feed,
    build_publication_log,
)
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.readiness import ScheduledBar, history_resets
from odyssey_fx.marketdata.domain.schedule import (
    DelayScenario,
    FixedSeriesDelay,
    SeriesSchedule,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.compiler.compiled import ResolvedMarketSource
from odyssey_fx.strategy.declarations.read_spec import DurationWindow
from tests.fixtures.backtest.harness import EXECUTION_SERIES, compiled_strategy
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market

CALENDAR = market.calendar(closures=(market.closure(m5.day("2026-01-14"), trading_day=True),))
DAILY = market.series(timeframe_id="1d_ny17")
HOURLY = market.series(timeframe_id="1h")
M15 = market.series(timeframe_id="15m")


def _t(text: str) -> UtcTime:
    return UtcTime.parse(text)


def _span(start: str, end: str) -> Interval:
    return Interval(start=_t(start), end=_t(end))


UNRECORDED = (
    _span("2026-01-08T10:00:00Z", "2026-01-08T10:07:00Z"),
    _span("2026-01-08T11:00:00Z", "2026-01-08T13:00:00Z"),
    _span("2026-01-20T22:00:00Z", "2026-01-21T22:00:00Z"),
    _span("2026-01-29T18:00:00Z", "2026-01-29T22:00:00Z"),
)


def _snapshot(tmp_path: Path) -> tuple[PendingSnapshot, ReadableSnapshot]:
    table = m5.write_rule_files(tmp_path)
    rows = m5.minute_rows(
        CALENDAR, _span("2026-01-04T22:00:00Z", "2026-01-30T22:00:00Z"), unrecorded=UNRECORDED
    )
    pending = m5.build_pending(CALENDAR, table, [m5.MonthSpec(2026, 1, rows)])
    return pending, m5.readable(pending)


def _schedules(snapshot: ReadableSnapshot) -> dict[SeriesId, SeriesSchedule]:
    return {
        record.series_id: SeriesSchedule(
            series=record.series_id,
            timeframe_def=m5.TIMEFRAME_DEFS[record.series_id.timeframe.id],
            calendar=CALENDAR,
        )
        for record in snapshot.manifest.series
    }


def _view(pending: PendingSnapshot, snapshot: ReadableSnapshot) -> AsOfView:
    return AsOfView(
        snapshot=snapshot,
        allowed_partitions=frozenset(pending.partition_bars),
        schedules=_schedules(snapshot),
        partition_bars=pending.partition_bars,
    )


def _bars_of(pending: PendingSnapshot) -> Mapping[SeriesId, tuple[Bar, ...]]:
    out: dict[SeriesId, tuple[Bar, ...]] = {}
    for partition_id, bars in pending.partition_bars.items():
        out[partition_id.series] = tuple(sorted(bars, key=lambda bar: bar.bar_start.value))
    return out


def test_c1_partial_daily_bar_is_built_with_coverage_and_survives_the_partition(
    tmp_path: Path,
) -> None:
    """C-1（D12-82・D12-87、D03 §15.4・§15.6 の 3）: 部分観測の日足を被覆の属性つきで作り、
    partition の列から
    読み戻せる。被覆率に閾値は無く（127 分欠けても作る）、全分観測とは呼ばない。"""
    pending, _ = _snapshot(tmp_path)
    daily = {bar.bar_start: bar for bar in _bars_of(pending)[DAILY]}
    partial = daily[_t("2026-01-07T22:00:00Z")]
    assert partial.coverage is not None
    assert (partial.coverage.expected_minutes, partial.coverage.recorded_minutes) == (1440, 1313)
    assert partial.coverage.is_fully_observed is False
    assert partial.bar_start == _t("2026-01-07T22:00:00Z")  # 区間の開始（最初の観測ではない）
    assert partial.available_at == partial.bar_end
    store = ParquetSnapshotStore(root=tmp_path / "snapshots")
    partition_id = next(pid for pid in pending.partition_bars if pid.series == DAILY)
    store.write_partition("x", partition_id, pending.partition_bars[partition_id])
    reread = {bar.bar_start: bar for bar in store.read_partition("x", partition_id)}
    assert reread[partial.bar_start].coverage == partial.coverage
    summary = [r for r in pending.report.results if r.kind is CheckKind.PARTIAL_COVERAGE_BAR]
    assert any(r.series == DAILY for r in summary)


def test_c2_a_declared_closure_does_not_break_the_daily_window(tmp_path: Path) -> None:
    """C-2（D12-83、D03 §15.7）: 休場の日は日足が無く、
    窓は休場を飛ばして数える（準備は途切れない）。"""
    pending, snapshot = _snapshot(tmp_path)
    view = _view(pending, snapshot)
    window = view.history(DAILY, BarsWindow(3), _t("2026-01-15T22:00:00Z"))
    assert not isinstance(window, MissingInput)
    assert [bar.bar_start for bar in window] == [
        _t("2026-01-11T22:00:00Z"),
        _t("2026-01-12T22:00:00Z"),
        _t("2026-01-14T22:00:00Z"),
    ]


def test_c2_an_all_no_record_scheduled_day_resets_readiness(tmp_path: Path) -> None:
    """C-2（D12-84、D03 §15.7）: 予定取引日の全日無記録は日足を作らず、準備をやり直す。

    2026-01-21 の取引日の予定の公開時刻（01-21 22:00Z）より前は従来どおり（最新足は 01-20 の日足）。
    それ以後は、その日足を必要とする読み取りがどれも `WARMUP_INSUFFICIENT`。その後の日足 3 本
    （01-22・01-23・01-26）の 3 本目の公開時刻 01-26 22:00Z に準備が整う。
    """
    pending, snapshot = _snapshot(tmp_path)
    view = _view(pending, snapshot)
    before = view.history(DAILY, BarsWindow(3), _t("2026-01-21T21:59:00Z"))
    assert not isinstance(before, MissingInput)
    warmup = MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
    at = _t("2026-01-21T22:00:00Z")
    assert view.history(DAILY, BarsWindow(3), at) == warmup
    assert view.latest_available(DAILY, at) == warmup
    assert view.bar(DAILY, _t("2026-01-20T22:00:00Z"), at) == warmup
    assert view.history(DAILY, BarsWindow(3), _t("2026-01-26T21:59:00Z")) == warmup
    ready = view.history(DAILY, BarsWindow(3), _t("2026-01-26T22:00:00Z"))
    assert not isinstance(ready, MissingInput)
    assert [bar.bar_start for bar in ready][0] == _t("2026-01-21T22:00:00Z")

    sequence = [
        ScheduledBar(
            bar_start=start,
            available_at=bar.available_at if bar is not None else end,
            built=bar is not None,
            no_record=bar is None,
        )
        for start, end, bar in _daily_schedule(pending)
    ]
    (reset,) = history_resets(sequence, 3)
    assert reset.no_record_bar_start == _t("2026-01-20T22:00:00Z")
    assert reset.ready_at == _t("2026-01-26T22:00:00Z")


def _daily_schedule(pending: PendingSnapshot) -> list[tuple[UtcTime, UtcTime, Bar | None]]:
    built = {bar.bar_start: bar for bar in _bars_of(pending)[DAILY]}
    definition = m5.TIMEFRAME_DEFS["1d_ny17"]
    out = []
    for start in CALENDAR.expected_bar_starts(
        definition, _span("2026-01-04T22:00:00Z", "2026-01-30T22:00:00Z")
    ):
        interval = definition.expected_interval(CALENDAR, start)
        assert interval is not None
        out.append((start, interval.end, built.get(start)))
    return out


def test_c2_the_run_records_history_resets_and_keeps_going(tmp_path: Path) -> None:
    """C-2（D06 §18.10 の表22、D12-90 (a)）: 戦略が読む 1 時間足の全分無記録の足ごとに、
    準備が実際に整う
    足と公開時刻を表22 の行にする（窓 20 本＋末尾から除く 1 本＝21 本）。2026-01-21 の取引日の
    24 本の後、01-22 の 21 本目
    `[01-22 18:00Z, 19:00Z)` の公開 19:00Z に整う。"""
    pending, snapshot = _snapshot(tmp_path)
    observation = Mt5Observation.from_snapshot(snapshot)
    assert observation is not None
    parts = build_mt5_run_parts(
        observation=observation,
        compiled=compiled_strategy(),
        execution_series=EXECUTION_SERIES,
        hierarchy=(EXECUTION_SERIES,),
        schedules=_schedules(snapshot),
        bars_of=_bars_of(pending),
        calendar=CALENDAR,
        run_interval=_span("2026-01-21T00:00:00Z", "2026-01-23T00:00:00Z"),
        repo_root=tmp_path,
    )
    rows = [row for row in parts.history_resets if row.series == HOURLY and row.window_bars == 21]
    assert len(rows) == 24
    assert {row.ready_at for row in rows} == {_t("2026-01-22T19:00:00Z")}
    latest_only = [row for row in parts.history_resets if row.window_bars == 1]
    assert {row.ready_at for row in latest_only} == {_t("2026-01-21T23:00:00Z")}


def test_c7_a_bar_is_not_readable_before_its_publication_time(tmp_path: Path) -> None:
    """C-7（D03 §15.5、第6.2節）: 足の公開時刻より前に指標や判断へ使われない。

    2026-01-29 の取引日は 17:59Z が最後の観測だが、日足の公開時刻は区間の終わり 22:00Z
    のまま（観測が
    早く終わっても早めない）。18:30Z の最新足は前日の日足で、その日の日足は読めない。執行足の
    `ExecutionOpen` は最初の観測の時刻（10:07Z）に出し、全分無記録の執行足には出さない。
    """
    pending, snapshot = _snapshot(tmp_path)
    view = _view(pending, snapshot)
    at = _t("2026-01-29T18:30:00Z")
    latest = view.latest_available(DAILY, at)
    assert isinstance(latest, Bar)
    assert latest.bar_start == _t("2026-01-27T22:00:00Z")
    day = _t("2026-01-28T22:00:00Z")
    assert view.bar(DAILY, day, at) == MissingInput(MissingInputReason.INPUT_MISSING_OR_INVALID)
    published = view.latest_available(DAILY, _t("2026-01-29T22:00:00Z"))
    assert isinstance(published, Bar) and published.bar_start == day
    assert published.coverage is not None
    assert published.coverage.last_observed_at == _t("2026-01-29T17:59:00Z")

    feed = build_feed(
        snapshot,
        frozenset(pending.partition_bars),
        pending.partition_bars,
        _schedules(snapshot),
        _span("2026-01-08T09:00:00Z", "2026-01-08T14:00:00Z"),
        execution_series=frozenset({M15}),
        internal_series=frozenset({market.series(timeframe_id="1m")}),
    )
    opens = {
        event.bar_key.bar_start: event.at
        for event in feed
        if event.kind is PublicationKind.EXECUTION_OPEN
    }
    assert opens[_t("2026-01-08T10:00:00Z")] == _t("2026-01-08T10:07:00Z")
    assert _t("2026-01-08T11:00:00Z") not in opens  # 全分無記録の執行足
    assert opens[_t("2026-01-08T13:00:00Z")] == _t("2026-01-08T13:00:00Z")
    daily_publications = [
        event
        for event in feed
        if event.kind is PublicationKind.PUBLICATION and event.series.timeframe.id == "1m"
    ]
    assert daily_publications == []  # 1 分足は戦略へ配送しない（D06 §18.2）


def test_c7_a_no_record_bar_is_not_known_before_its_scheduled_publication(tmp_path: Path) -> None:
    """C-7（D03 §15.7・§15.5。第 1 巡のレビューの指摘）:
    全分無記録の足が準備不足として見えるのは、その足の
    予定の公開時刻（区間の終わり＋通常遅延）以後だけ。それより前は、作られた足が未公開のときと同じ答え
    （最新足は `LATEST_BAR_UNAVAILABLE`、窓は `INPUT_MISSING_OR_INVALID`）で、
    記録の有無を先に知らせない。"""
    pending, snapshot = _snapshot(tmp_path)
    delayed = {
        series: replace(schedule, normal_publication_delay=timedelta(minutes=5))
        for series, schedule in _schedules(snapshot).items()
    }
    view = AsOfView(
        snapshot=snapshot,
        allowed_partitions=frozenset(pending.partition_bars),
        schedules=delayed,
        partition_bars=pending.partition_bars,
    )
    bar_end = _t("2026-01-21T22:00:00Z")
    before = bar_end + timedelta(minutes=4)
    assert view.latest_available(DAILY, before) == MissingInput(
        MissingInputReason.LATEST_BAR_UNAVAILABLE
    )
    assert view.history(DAILY, BarsWindow(3), before) == MissingInput(
        MissingInputReason.INPUT_MISSING_OR_INVALID
    )
    after = bar_end + timedelta(minutes=5)
    warmup = MissingInput(MissingInputReason.WARMUP_INSUFFICIENT)
    assert view.latest_available(DAILY, after) == warmup
    assert view.history(DAILY, BarsWindow(3), after) == warmup


def test_duration_window_reads_are_checked_for_shortfall() -> None:
    """D06 §18.9 の 2・4（第 1 巡のレビューの指摘）: 経過時間の窓だけで読む系列も、
    能力検査の取得不足の照合と
    被覆の報告の対象に入る（表22 の窓の本数には入れない）。"""
    compiled = compiled_strategy()
    component = compiled.components[0]
    name, plan = next(iter(component.input_plans.items()))
    duration = DurationWindow(duration=timedelta(hours=30))
    changed_plan = replace(plan, resolved_window=duration)
    changed = replace(
        compiled,
        components=(
            replace(component, input_plans={**component.input_plans, name: changed_plan}),
            *compiled.components[1:],
        ),
    )
    series = next(
        source.series for source in plan.sources if isinstance(source, ResolvedMarketSource)
    )
    reads = strategy_reads(changed)
    assert timedelta(hours=30) in reads[series]


def test_c7_a_no_record_bar_follows_the_delay_scenario(tmp_path: Path) -> None:
    """C-7（D03 §15.7「予定の公開時刻（遅延シナリオ込み）以後」。第 2 巡のレビューの指摘）:
    遅延シナリオで
    日足の公開を 2 時間遅らせると、全分無記録の日足が準備不足として見えるのも 2 時間後から。"""
    pending, snapshot = _snapshot(tmp_path)
    schedules = _schedules(snapshot)
    scenario = DelayScenario(
        id="d1_2h",
        version=1,
        rules=(FixedSeriesDelay(series=DAILY, delay=timedelta(hours=2)),),
    )
    log = build_publication_log(
        snapshot, frozenset(pending.partition_bars), pending.partition_bars, schedules, scenario
    )
    view = AsOfView(
        snapshot=snapshot,
        allowed_partitions=frozenset(pending.partition_bars),
        schedules=schedules,
        partition_bars=pending.partition_bars,
        publication_log=log,
    )
    bar_end = _t("2026-01-21T22:00:00Z")
    assert view.latest_available(DAILY, bar_end + timedelta(hours=1)) == MissingInput(
        MissingInputReason.LATEST_BAR_UNAVAILABLE
    )
    assert view.latest_available(DAILY, bar_end + timedelta(hours=2)) == MissingInput(
        MissingInputReason.WARMUP_INSUFFICIENT
    )


def test_an_execution_series_read_by_the_strategy_is_checked_over_its_history(
    tmp_path: Path,
) -> None:
    """D06 §18.9 の 2・4（第 2 巡のレビューの指摘）: 執行に使う系列を戦略も履歴窓で読むなら、
    その系列の
    報告と取得不足の照合の区間は run 区間だけでなく、窓が遡る履歴の分を含む。"""
    pending, snapshot = _snapshot(tmp_path)
    observation = Mt5Observation.from_snapshot(snapshot)
    assert observation is not None
    run = _span("2026-01-22T00:00:00Z", "2026-01-22T06:00:00Z")
    parts = build_mt5_run_parts(
        observation=observation,
        compiled=compiled_strategy(),
        execution_series=HOURLY,
        hierarchy=(HOURLY,),
        schedules=_schedules(snapshot),
        bars_of=_bars_of(pending),
        calendar=CALENDAR,
        run_interval=run,
        repo_root=tmp_path,
    )
    (report,) = [item for item in parts.capability.coverage.series if item.series == HOURLY]
    # 窓 21 本（20 本＋末尾から除く 1 本）を遡る。
    assert report.interval.start == _t("2026-01-21T03:00:00Z")
    assert report.interval.end == run.end
