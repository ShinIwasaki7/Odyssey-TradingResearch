"""原データの時刻ラベルの宣言された補正（D03 §2・§3.7・§4 の v1.19）。

人工データの週（2019-03-10 の週。米国だけが夏時間で、開場は日曜 21:00Z・閉場は金曜 21:00Z）を
1 時間早いラベルで書いた原の行を作り、宣言された補正で直すことを確かめる。前の週（2019-03-03 の週。
米国も冬時間で開場 22:00Z）はラベルが正しく、補正しない。

確かめること（D08 §5 の v1.19 の行）:

- 対象の週の足だけが +1 時間され、対象でない週・系列の足は変わらない（決定論的）。
- 列挙した週が夏時間の暦から導けなければ受入れを止める（人間の決定 DST-1）。
- 補正した足が休場の時間帯に入れば受入れを失敗させる（人間の決定 DST-2）。ずれない週を誤って
  列挙すると、補正した足が補正していない足と重なり重複で止まる（DST-3 の注記どおり）。
- manifest の `conversion` に規則・系列ごとの動かした本数・再検査の結果が記録され、識別子の対象に
  なる。補正規則の無い宣言の `conversion` は従来の 5 項目のまま（既存の識別子は変わらない）。
- 列の正規順序（設定に書いた順序によらない）と、同じ系列・同じ週を 2 度書いた宣言の拒否。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from odyssey_fx.app.composition import AcceptanceService
from odyssey_fx.app.config import load_datasource
from odyssey_fx.app.config.datasources import DataSourceConfig
from odyssey_fx.common import canonical
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.parquet_store import _manifest_from_payload, manifest_payload
from odyssey_fx.marketdata.application.acceptance import (
    PendingSnapshot,
    RawFile,
    normalize_rows,
    normalize_rows_with_correction,
    recheck_corrected_bars,
)
from odyssey_fx.marketdata.application.ports import RawFileContent, RawRow
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import (
    IntegrityCheckFailed,
    MarketDataValueError,
    TimeLabelCorrectionFailed,
)
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import ConversionRecord
from odyssey_fx.marketdata.domain.time_label_correction import (
    TimeLabelCorrectionRecord,
    TimeLabelCorrectionRule,
    verify_correction_weeks,
)
from tests.fixtures.synthetic import market
from tests.unit.app.test_composition import _StubStore

REPO_ROOT = Path(__file__).resolve().parents[3]
DATASOURCE_V3 = REPO_ROOT / "configs/datasources/legacy_merged_csv_v3.yaml"

HOUR = timedelta(hours=1)
USDJPY_1H = market.series(market.USDJPY, "1h")
USDJPY_15M = market.series(market.USDJPY, "15m")
EURUSD_1H = market.series(market.EURUSD, "1h")

#: 補正の対象の週（補正の前のラベル。日曜 20:00Z〜金曜 20:00Z）と、正しい週の開場区間。
WEEK = Interval(
    start=UtcTime.parse("2019-03-10T20:00:00Z"), end=UtcTime.parse("2019-03-15T20:00:00Z")
)
TRUE_WEEK = Interval(start=WEEK.start + HOUR, end=WEEK.end + HOUR)
#: 前の週（ラベルが正しい）を含む人工データの範囲。
WINDOW = Interval(
    start=UtcTime.parse("2019-03-03T22:00:00Z"), end=UtcTime.parse("2019-03-15T21:00:00Z")
)


def _rule(
    weeks: Sequence[Interval] = (WEEK,),
    series: Sequence[str] = ("USDJPY/15m/bid", "USDJPY/1h/bid"),
) -> TimeLabelCorrectionRule:
    return TimeLabelCorrectionRule(
        rule_id="histdata_us_only_dst_weeks",
        rule_version=1,
        shift=HOUR,
        series=tuple(series),
        weeks=tuple(weeks),
        daylight_zone="America/New_York",
        standard_zone="Europe/London",
    )


def _true_bars(series_id: SeriesId = USDJPY_1H) -> tuple[Bar, ...]:
    definition = market.TIMEFRAME_DEFS[series_id.timeframe.id]
    return market.make_bars(series_id, definition, market.calendar(), WINDOW)


def _raw_rows(bars: Sequence[Bar], *, shifted_week: Interval | None = TRUE_WEEK) -> list[RawRow]:
    """原の行。`shifted_week` に入る足は 1 時間早いラベルで書く（原データの夏時間ズレ）。"""
    rows: list[RawRow] = []
    for bar, row in zip(bars, market.csv_rows(bars), strict=True):
        if shifted_week is not None and shifted_week.contains(bar.bar_start):
            moved = bar.bar_start - HOUR
            row = {**row, "timestamp": moved.value.strftime("%Y-%m-%d %H:%M:%S+00:00")}
        rows.append(row)
    return rows


def _raw_file(series_id: SeriesId = USDJPY_1H) -> RawFile:
    return RawFile(
        path=f"data/raw/market/{series_id.symbol}_{series_id.timeframe.id}_merged.csv",
        sha256="0" * 64,
        symbol=series_id.symbol,
        timeframe=series_id.timeframe,
        declared_basis=PriceBasis.BID,
    )


def _datasource() -> DataSourceConfig:
    return load_datasource(DATASOURCE_V3)


# --- 適用（D03 §4 の v1.19 の「適用」）-----------------------------------------------


def test_only_bars_of_the_declared_weeks_move_by_one_hour() -> None:
    truth = _true_bars()
    rows = _raw_rows(truth)
    mapping = _datasource().mapping
    bars, corrected = normalize_rows_with_correction(
        _raw_file(), rows, mapping, market.TF_1H, market.calendar(), correction=_rule()
    )
    # 補正の後の足は、正しいラベルの足と開始時刻・区間・価格まで一致する。
    assert [(bar.interval, bar.open, bar.close) for bar in bars] == [
        (bar.interval, bar.open, bar.close) for bar in truth
    ]
    assert len(corrected) == sum(1 for bar in truth if TRUE_WEEK.contains(bar.bar_start))
    assert all(TRUE_WEEK.contains(bar.bar_start) for bar in corrected)
    # 前の週（ラベルが正しい）の足は動かない。
    before = [bar for bar in bars if bar.bar_start < TRUE_WEEK.start]
    assert before and all(bar not in corrected for bar in before)
    # 決定論: 同じ入力で同じ結果。
    again = normalize_rows_with_correction(
        _raw_file(), rows, mapping, market.TF_1H, market.calendar(), correction=_rule()
    )
    assert again == (bars, corrected)


def test_a_series_outside_the_rule_and_a_missing_rule_change_nothing() -> None:
    truth = _true_bars(EURUSD_1H)
    rows = _raw_rows(truth)
    mapping = _datasource().mapping
    plain = normalize_rows(_raw_file(EURUSD_1H), rows, mapping, market.TF_1H, market.calendar())
    _, corrected = normalize_rows_with_correction(
        _raw_file(EURUSD_1H), rows, mapping, market.TF_1H, market.calendar(), correction=_rule()
    )
    assert corrected == ()
    labels = [UtcTime.parse(row["timestamp"].replace(" ", "T", 1)) for row in rows]
    assert [bar.bar_start for bar in plain] == labels


# --- 列挙の検算（人間の決定 DST-1）----------------------------------------------------


def test_the_configured_weeks_are_derivable_from_the_daylight_saving_calendars() -> None:
    rule = _datasource().time_label_correction
    assert rule is not None
    assert (len(rule.weeks), len(rule.series)) == (26, 20)
    verify_correction_weeks(rule, market.calendar())  # 止まらない


@pytest.mark.parametrize(
    ("week", "reason"),
    [
        # 2019-04-07 の週は米国も英国も夏時間（英国は 3 月 31 日から）。
        (("2019-04-07T20:00:00Z", "2019-04-12T20:00:00Z"), "on daylight saving time"),
        # 2019-03-03 の週はどちらも冬時間。開場は 22:00Z なので 1 時間前は 21:00Z。
        (("2019-03-03T21:00:00Z", "2019-03-08T21:00:00Z"), "off daylight saving time"),
        # 打ち間違い（終端が 1 時間ずれている）。
        (("2019-03-10T20:00:00Z", "2019-03-15T19:00:00Z"), "the weekly session is"),
    ],
)
def test_a_week_not_derivable_from_the_calendars_stops_acceptance(
    week: tuple[str, str], reason: str
) -> None:
    rule = _rule(weeks=(Interval(start=UtcTime.parse(week[0]), end=UtcTime.parse(week[1])),))
    with pytest.raises(TimeLabelCorrectionFailed, match=reason):
        verify_correction_weeks(rule, market.calendar())


# --- 補正の後の再検査（人間の決定 DST-2・DST-3）------------------------------------------


def test_a_corrected_bar_in_a_closure_fails_acceptance() -> None:
    truth = _true_bars()
    rows = _raw_rows(truth)
    # 2019-03-13（水）を取引日単位の休場にしたカレンダー。補正した足がその休場帯に入る。
    closed = market.calendar(closures=(market.closure(date(2019, 3, 13), trading_day=True),))
    mapping = _datasource().mapping
    bars, corrected = normalize_rows_with_correction(
        _raw_file(), rows, mapping, market.TF_1H, closed, correction=_rule()
    )
    with pytest.raises(TimeLabelCorrectionFailed, match="outside the calendar sessions"):
        recheck_corrected_bars(
            {USDJPY_1H: bars},
            {USDJPY_1H: corrected},
            timeframe_defs=market.TIMEFRAME_DEFS,
            calendar=closed,
        )


def test_listing_a_week_whose_labels_are_right_stops_on_duplicates() -> None:
    truth = _true_bars()
    rows = _raw_rows(truth, shifted_week=None)  # ラベルが正しい週を誤って列挙した
    mapping = _datasource().mapping
    bars, corrected = normalize_rows_with_correction(
        _raw_file(), rows, mapping, market.TF_1H, market.calendar(), correction=_rule()
    )
    with pytest.raises(IntegrityCheckFailed) as caught:
        recheck_corrected_bars(
            {USDJPY_1H: bars},
            {USDJPY_1H: corrected},
            timeframe_defs=market.TIMEFRAME_DEFS,
            calendar=market.calendar(),
        )
    report = caught.value.report
    assert report is not None
    assert {item.kind for item in report.results} == {CheckKind.DUPLICATE_TIMESTAMP}
    # 金曜 20:00Z の補正しなかった足と、19:00Z から動いた足が重なる。
    assert [str(item.interval.start) for item in report.results] == ["2019-03-15T20:00:00Z"]


# --- 受入れの通し（manifest の記録。D03 §3.7）-------------------------------------------


class _FileSource:
    """原ファイルごとに行を返す読込ポートの代役。"""

    def __init__(self, files: dict[str, list[RawRow]]) -> None:
        self.files = files

    def read_file(self, path: str) -> RawFileContent:
        rows = self.files[path]
        digest = hashlib.sha256(repr(rows).encode()).hexdigest()
        return RawFileContent(sha256=digest, rows=tuple(rows))


def _accept(rule: TimeLabelCorrectionRule | None) -> PendingSnapshot:
    files = {
        "USDJPY_1h_merged.csv": _raw_rows(_true_bars(USDJPY_1H)),
        "USDJPY_15m_merged.csv": _raw_rows(_true_bars(USDJPY_15M)),
    }
    service = AcceptanceService(
        source=_FileSource(files),
        store=_StubStore(),
        datasource=replace(_datasource(), time_label_correction=rule),
        calendar=market.calendar(),
        timeframe_defs=market.TIMEFRAME_DEFS,
    )
    return service.accept(
        [(market.USDJPY, "1h"), (market.USDJPY, "15m")],
        created_at=UtcTime.parse("2026-10-05T00:00:00Z"),
    )


def test_acceptance_records_the_rule_and_the_shifted_counts_in_the_manifest() -> None:
    pending = _accept(_rule())
    record = pending.manifest.conversion.time_label_correction
    assert record is not None
    assert record.rule == _rule()
    in_week_1h = sum(1 for bar in _true_bars(USDJPY_1H) if TRUE_WEEK.contains(bar.bar_start))
    assert dict(record.shifted_bar_counts) == {
        "USDJPY/15m/bid": 4 * in_week_1h,
        "USDJPY/1h/bid": in_week_1h,
    }
    assert (record.recheck_duplicates, record.recheck_out_of_calendar) == (0, 0)
    # 補正の後は見かけの警告（金曜 20 時台の欠落・日曜 20 時台の休場帯の足）が出ない。
    assert not [
        item
        for item in pending.report.results
        if item.kind in (CheckKind.MISSING_EXPECTED_BAR, CheckKind.UNEXPECTED_BAR)
    ]
    # manifest.json の形に書いて読み戻しても識別子は変わらない。
    restored = _manifest_from_payload(manifest_payload(pending.manifest))
    assert restored.snapshot_id() == pending.manifest.snapshot_id()
    assert restored.conversion.time_label_correction == record


def test_without_the_correction_the_week_shows_the_apparent_warnings() -> None:
    pending = _accept(None)
    assert pending.manifest.conversion.time_label_correction is None
    kinds = {item.kind for item in pending.report.results}
    assert {CheckKind.MISSING_EXPECTED_BAR, CheckKind.UNEXPECTED_BAR} <= kinds
    assert pending.provisional_id != _accept(_rule()).provisional_id


def test_a_conversion_without_a_rule_keeps_the_five_identity_items() -> None:
    conversion = ConversionRecord(
        code_version="c",
        time_convention="explicit_offset_utc",
        aggregation_rule_version="a",
        calendar_id="fx_ny17",
        calendar_version=2,
    )
    payload = conversion.identity_payload()
    assert set(payload) == {
        "aggregation_rule_version",
        "calendar_id",
        "calendar_version",
        "code_version",
        "time_convention",
    }

    # 補正の記録を持たない dataclass の 5 項目と同じ符号化（既存の snapshot の識別子は変わらない）。
    @dataclass(frozen=True)
    class _FiveFieldConversion:
        """v1.19 より前の `ConversionRecord`（補正の記録の項目を持たない）と同じ形。"""

        code_version: str
        time_convention: str
        aggregation_rule_version: str
        calendar_id: str
        calendar_version: int

    legacy = _FiveFieldConversion("c", "explicit_offset_utc", "a", "fx_ny17", 2)
    assert canonical.encode(payload) == canonical.encode(legacy)


# --- 列の正規順序と重複の拒否（D03 §3.7.1 の v1.19）------------------------------------


def test_the_order_written_in_the_declaration_does_not_change_the_record() -> None:
    other = Interval(
        start=UtcTime.parse("2019-03-17T20:00:00Z"), end=UtcTime.parse("2019-03-22T20:00:00Z")
    )
    first = _rule(weeks=(other, WEEK), series=("USDJPY/1h/bid", "USDJPY/15m/bid"))
    second = _rule(weeks=(WEEK, other), series=("USDJPY/15m/bid", "USDJPY/1h/bid"))
    assert first == second
    record_a = TimeLabelCorrectionRecord(
        rule=first, shifted_bar_counts=(("USDJPY/1h/bid", 2), ("USDJPY/15m/bid", 8))
    )
    record_b = TimeLabelCorrectionRecord(
        rule=second, shifted_bar_counts=(("USDJPY/15m/bid", 8), ("USDJPY/1h/bid", 2))
    )
    assert canonical.encode(record_a.payload()) == canonical.encode(record_b.payload())
    assert TimeLabelCorrectionRecord.from_payload(record_a.payload(), "x") == record_a


def test_a_series_or_week_written_twice_is_rejected() -> None:
    with pytest.raises(MarketDataValueError, match="twice"):
        _rule(series=("USDJPY/1h/bid", "USDJPY/1h/bid"))
    with pytest.raises(MarketDataValueError, match="twice"):
        _rule(weeks=(WEEK, WEEK))
