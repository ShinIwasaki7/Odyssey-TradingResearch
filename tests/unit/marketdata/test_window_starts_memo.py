"""足の窓の開始時刻の計算を覚えても、読み取りの答えが変わらないこと（D26 §2.2 の 3）。

as-of の履歴窓（`AsOfView.history`・`history_ending_at`）と表22（`read_resets`）は、どちらも
`window_starts` で「基準の足から読む窓の期待足の開始時刻」を求める。この計算は時間足定義と取引
カレンダーだけから決まる純粋な計算なので、PR-A で答えを覚える形にした。先読みを防ぐ読み取りの
関門の中の処理なので、**返す値・送出する例外の型と文言**が変わらないことを、境目（週末・祝日・
短縮セッション・夏時間の切り替え・全分無記録の足・snapshot の端）を含む入力で確かめる。

期待値の作り方（PR #93 と同じ）: 覚える形に変える前の木（main de2ab40 に本テストだけを足したもの）
で本テストを走らせ、答えの列の件数とダイジェストを下の定数に写した。答えの列は `repr` の
SHA-256 で、例外は型の名前と文言を答えとして入れる。人工データだけを使う（成績は読まない）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from datetime import date, time, timedelta
from functools import partial
from pathlib import Path
from typing import Final

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.asof import (
    AsOfView,
    BarsWindow,
    DurationWindow,
    HistoryWindowLike,
    window_starts,
)
from odyssey_fx.marketdata.application.mt5_observation import Mt5Observation
from odyssey_fx.marketdata.application.read_readiness import read_resets
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.readiness import ReadKind, ReadWindowSpec
from odyssey_fx.marketdata.domain.schedule import SeriesSchedule
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market

#: `window_starts` の格子（下の `_grid_answers`）の答えの件数とダイジェスト。変更の前の木で記録。
EXPECTED_GRID_COUNT: Final = 40320
EXPECTED_GRID_DIGEST: Final = "d57d93276a9e4a04d927a62c00580a8a811bf8127b5b9fff991a3d7788db6ae9"
#: MT5 経路の人工 snapshot での読み取りと表22 の行（下の `_snapshot_answers`）。変更の前の木で記録。
EXPECTED_SNAPSHOT_COUNT: Final = 13136
EXPECTED_SNAPSHOT_DIGEST: Final = "b75ea28e54484c56ebaced2b62f67d8522189875bde85836945090a2ed785dd0"


def _utc(text: str) -> UtcTime:
    return UtcTime.parse(text)


def _span(start: str, end: str) -> Interval:
    return Interval(start=_utc(start), end=_utc(end))


def _answer(call: Callable[[], object]) -> object:
    """呼び出しの答え。例外は型の名前と文言を答えにする（例外も変わらないことを確かめる）。"""
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - 例外の型と文言そのものを比べる
        return ("raise", type(error).__name__, str(error))


def _digest(answers: list[object]) -> str:
    return hashlib.sha256(repr(answers).encode("utf-8")).hexdigest()


# --- window_starts の格子（週末・祝日・短縮セッション・夏時間の切り替え）---------------------

#: 祝日（取引日単位の休場）・短縮セッション。夏時間の切り替え（2026-03-08・11-01）の前後と
#: 年末年始に置く。
CLOSURES: Final = (
    market.closure(date(2026, 1, 1), trading_day=True, note="元日"),
    market.closure(date(2026, 3, 9), time(10, 0), time(12, 0), note="夏時間の切替直後の短縮"),
    market.closure(date(2026, 3, 11), trading_day=True, note="取引日単位の休場"),
    market.closure(date(2026, 10, 30), time(13, 0), time(17, 0), note="切り戻し前の短縮"),
    market.closure(date(2026, 11, 3), trading_day=True, note="切り戻し後の休場"),
    market.closure(date(2026, 12, 24), time(13, 0), time(17, 0), note="短縮"),
    market.closure(date(2026, 12, 25), trading_day=True, note="クリスマス"),
)

#: 基準の足を取る区間（夏時間の切り替えの週・年末年始の週。どれも週末をまたぐ）。
SPANS: Final = (
    _span("2026-03-04T00:00:00Z", "2026-03-13T00:00:00Z"),
    _span("2026-10-28T00:00:00Z", "2026-11-06T00:00:00Z"),
    _span("2026-12-22T00:00:00Z", "2027-01-02T00:00:00Z"),
)

WINDOWS: Final[tuple[HistoryWindowLike, ...]] = (
    BarsWindow(1),
    BarsWindow(4),
    BarsWindow(30),
    DurationWindow(timedelta(hours=1)),
    DurationWindow(timedelta(hours=7)),
    DurationWindow(timedelta(days=2)),
)
OFFSETS: Final = (0, 2)

#: 各時間足で基準の足を何本おきに取るか（15 分足は本数が多いので間引く）。
STRIDE: Final = {"15m": 7, "1h": 3, "4h_ny17": 1, "1d_ny17": 1}


def _bases(definition: TimeframeDefinition, span: Interval) -> Iterator[UtcTime]:
    """区間の整列上の足の開始時刻（休場の足も含む）と、足の途中の時刻。"""
    stride = STRIDE[definition.ref.id]
    moment = definition.boundaries(span.start).start
    index = 0
    while moment < span.end:
        if index % stride == 0:
            yield moment
            # 整列していない時刻（足の途中）。基準として渡されても答えが変わらないこと。
            yield moment + timedelta(minutes=7)
        index += 1
        moment = definition.boundaries(moment).end


def _grid_answers() -> list[object]:
    answers: list[object] = []
    for calendar in (market.calendar(), market.calendar(CLOSURES)):
        for timeframe_id, definition in sorted(market.TIMEFRAME_DEFS.items()):
            schedule = SeriesSchedule(
                series=market.series(timeframe_id=timeframe_id),
                timeframe_def=definition,
                calendar=calendar,
            )
            for span in SPANS:
                for base in _bases(definition, span):
                    for window in WINDOWS:
                        for offset in OFFSETS:
                            call = partial(window_starts, schedule, window, base, offset)
                            key = (len(calendar.closures), timeframe_id, base, window, offset)
                            answers.append((key, _answer(call)))
    return answers


def test_window_starts_match_the_record_made_before_the_memo() -> None:
    first = _grid_answers()
    # 2 回目は覚えた答えを引く経路を通る。1 回目（覚えながら求める経路）と同じであること。
    second = _grid_answers()
    assert first == second
    assert (len(first), _digest(first)) == (EXPECTED_GRID_COUNT, EXPECTED_GRID_DIGEST)


# --- MT5 経路の人工 snapshot（全分無記録の足・snapshot の端）---------------------------------

SNAPSHOT_CALENDAR: Final = market.calendar(
    closures=(
        market.closure(date(2026, 3, 9), time(10, 0), time(12, 0), note="夏時間の切替直後の短縮"),
        market.closure(date(2026, 3, 11), trading_day=True, note="取引日単位の休場"),
    )
)
#: snapshot の区間（2026-03-01 の週の開場から 03-13 の閉場まで。夏時間の切り替え 03-08 を含む）。
DATA: Final = _span("2026-03-01T22:00:00Z", "2026-03-13T21:00:00Z")
#: 全分無記録の分（日中の数分・日中の 2 時間・取引日まるごと・snapshot の終わりの直前）。
UNRECORDED: Final = (
    _span("2026-03-03T10:00:00Z", "2026-03-03T10:07:00Z"),
    _span("2026-03-04T11:00:00Z", "2026-03-04T13:00:00Z"),
    _span("2026-03-09T21:00:00Z", "2026-03-10T21:00:00Z"),
    _span("2026-03-13T18:00:00Z", "2026-03-13T21:00:00Z"),
)
SPECS: Final = (
    ReadWindowSpec(kind=ReadKind.BARS, bars=5),
    ReadWindowSpec(kind=ReadKind.BARS, bars=3, exclude_latest_bars=2),
    ReadWindowSpec(kind=ReadKind.DURATION, duration=timedelta(hours=6)),
    ReadWindowSpec(kind=ReadKind.DURATION, duration=timedelta(hours=4), exclude_latest_bars=1),
    ReadWindowSpec(kind=ReadKind.LATEST),
)
#: 読み取りの判断時刻の間隔（下の `_reads`）。
READ_STEP: Final = timedelta(minutes=105)
#: 表22 を求める run の区間（snapshot の始まり・夏時間の切り替え・無記録の日・snapshot の終わり）。
RUNS: Final = (
    _span("2026-03-02T00:00:00Z", "2026-03-03T12:00:00Z"),
    _span("2026-03-06T00:00:00Z", "2026-03-09T12:00:00Z"),
    _span("2026-03-10T06:00:00Z", "2026-03-12T00:00:00Z"),
    _span("2026-03-12T00:00:00Z", "2026-03-13T21:00:00Z"),
)


def _window_of(spec: ReadWindowSpec) -> HistoryWindowLike:
    if spec.kind is ReadKind.BARS:
        assert spec.bars is not None
        return BarsWindow(spec.bars)
    assert spec.duration is not None
    return DurationWindow(spec.duration)


def _reads(view: AsOfView, series: SeriesId, definition: TimeframeDefinition) -> list[object]:
    """判断時刻を snapshot の始まりの 1 日前から終わりの 1 日後まで動かした読み取り。

    `READ_STEP` は 15 分の奇数倍なので、判断時刻は 15 分足のどの位置（毎時 0・15・30・45 分）にも
    来る。
    """
    answers: list[object] = []
    at = DATA.start - timedelta(days=1)
    while at <= DATA.end + timedelta(days=1):
        answers.append((str(series), at, _answer(partial(view.latest_available, series, at))))
        expected = view.expected_latest_key(series, at)
        for spec in SPECS:
            if spec.kind is ReadKind.LATEST:
                continue
            window = _window_of(spec)
            offset = spec.exclude_latest_bars
            read = partial(view.history, series, window, at, end_offset_bars=offset)
            answers.append((str(series), at, spec.sort_key(), _answer(read)))
            if expected is None:
                continue
            # 基準の足を指定した読み取り（期待される最新足とその前後。後ろは未来なので例外）。
            for base in (
                definition.boundaries(expected.bar_start - timedelta(microseconds=1)).start,
                expected.bar_start,
                definition.boundaries(expected.bar_start).end,
            ):
                read = partial(
                    view.history_ending_at, series, window, base, at, end_offset_bars=offset
                )
                answers.append((str(series), at, spec.sort_key(), base, _answer(read)))
        at = at + READ_STEP
    return answers


def _snapshot_answers(tmp_path: Path) -> list[object]:
    table = m5.write_rule_files(tmp_path, coverage=("2026-02-01", "2026-04-01"))
    rows = m5.minute_rows(SNAPSHOT_CALENDAR, DATA, unrecorded=UNRECORDED)
    pending = m5.build_pending(SNAPSHOT_CALENDAR, table, [m5.MonthSpec(2026, 3, rows)])
    snapshot = m5.readable(pending)
    schedules: dict[SeriesId, SeriesSchedule] = {
        record.series_id: SeriesSchedule(
            series=record.series_id,
            timeframe_def=m5.TIMEFRAME_DEFS[record.series_id.timeframe.id],
            calendar=SNAPSHOT_CALENDAR,
        )
        for record in snapshot.manifest.series
    }
    view = AsOfView(
        snapshot=snapshot,
        allowed_partitions=frozenset(pending.partition_bars),
        schedules=schedules,
        partition_bars=pending.partition_bars,
    )
    bars_of: dict[SeriesId, list[Bar]] = {}
    for partition_id, bars in pending.partition_bars.items():
        bars_of.setdefault(partition_id.series, []).extend(bars)
    observation = Mt5Observation.from_snapshot(snapshot)
    assert observation is not None

    answers: list[object] = []
    targets = sorted((series for series in schedules if series.timeframe.id in m5.TARGETS), key=str)
    for series in targets:
        answers.extend(_reads(view, series, schedules[series].timeframe_def))
        bars = tuple(sorted(bars_of[series], key=lambda bar: bar.bar_start.value))
        no_record = observation.no_record.get(series, frozenset())
        for spec in SPECS:
            for run in RUNS:
                rows_of = partial(read_resets, schedules[series], spec, bars, no_record, run)
                answers.append((str(series), spec.sort_key(), run, _answer(rows_of)))
    return answers


def test_reads_and_history_resets_match_the_record_made_before_the_memo(tmp_path: Path) -> None:
    # 覚えた答えを引く経路は、同じ run の中の隣り合う判断時刻どうしで通る（窓がほぼ重なる）。
    answers = _snapshot_answers(tmp_path)
    assert (len(answers), _digest(answers)) == (EXPECTED_SNAPSHOT_COUNT, EXPECTED_SNAPSHOT_DIGEST)


def test_a_wrongly_typed_base_raises_the_same_error_as_before() -> None:
    """型の誤りの基準時刻（ハッシュできない値を含む）は、覚え書きを通らず、変更の前と同じ例外を
    送出する（変更の前の木で同じ型と文言を確かめた）。"""
    schedule = SeriesSchedule(
        series=market.series(timeframe_id="1h"),
        timeframe_def=market.TF_1H,
        calendar=market.calendar(),
    )
    bases: tuple[object, ...] = ([], "2026-03-04T00:00:00Z", _utc("2026-03-04T00:00:00Z").value)
    windows: tuple[HistoryWindowLike, ...] = (BarsWindow(3), DurationWindow(timedelta(hours=3)))
    for base in bases:
        for window in windows:
            answer = _answer(partial(window_starts, schedule, window, base, 0))  # type: ignore[arg-type]
            assert answer == ("raise", "MarketDataValueError", "boundaries() requires a UtcTime")
