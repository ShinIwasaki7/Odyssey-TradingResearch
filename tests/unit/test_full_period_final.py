"""full_period_final（全期間のデータ上の実行可能性の最終集計）の単体テスト。人工データと規則ファイルだけ。"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from tools.survey import full_period_final as fin
from tools.survey.full_period_feasibility import (
    FeasibilityError,
    FileWindow,
    MinuteStatus,
    PairSeries,
    iso,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "docs/design/counting_rules/counting_rules_v1.yaml"
TABLE = ROOT / "docs/design/time_rules/time_rule_table_v1.yaml"
APPROVED = ROOT / "docs/design/time_rules/approved_time_rule_tables.yaml"


def _e(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


@pytest.fixture(autouse=True)
def _at_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)  # 規則ファイルの中のパスはリポジトリの根から


# --- 入力点 1: 数え方の規則 -------------------------------------------------------------


def test_load_counting_rules_reads_values_from_file() -> None:
    rules = fin.load_counting_rules(RULES)
    assert rules.warmup_daily_bars == 60
    assert rules.variants == ("standard", "candidate")
    assert rules.timeframes == ("15m", "1h", "4h_ny17", "1d_ny17")
    assert rules.calendar_id == "fx_ny17_oanda"
    assert rules.sha256 == sha256_file(RULES)
    fin.verify_warmup_source(rules)


def test_load_counting_rules_rejects_null_and_unsupported(tmp_path: Path) -> None:
    text = RULES.read_text(encoding="utf-8")
    nulled = tmp_path / "nulled.yaml"
    nulled.write_text(text.replace("daily_bars: 60", "daily_bars: null"), encoding="utf-8")
    with pytest.raises(fin.UnsupportedRuleError, match="null"):
        fin.load_counting_rules(nulled)
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        text.replace("coverage_threshold: none", "coverage_threshold: half"), encoding="utf-8"
    )
    with pytest.raises(fin.UnsupportedRuleError, match="coverage_threshold"):
        fin.load_counting_rules(changed)


def test_verify_warmup_source_detects_mismatch() -> None:
    rules = fin.load_counting_rules(RULES)
    wrong = fin.CountingRules(**{**_asdict(rules), "warmup_daily_bars": 59})
    with pytest.raises(fin.UnsupportedRuleError, match="戦略の宣言"):
        fin.verify_warmup_source(wrong)


def _asdict(rules: fin.CountingRules) -> dict[str, Any]:
    return {name: getattr(rules, name) for name in rules.__slots__}


# --- 入力点 2: 時刻の規則の表 -----------------------------------------------------------


def test_load_time_rule_table_builds_standard_and_candidate() -> None:
    standard, candidate = fin.load_time_rule_table(TABLE, APPROVED)
    assert (standard.name, candidate.name) == ("standard", "candidate")
    between_2019 = _e("2019-03-12T10:00:00")
    assert standard.offset_hours("USDJPY", between_2019) == 2  # 行 1: 標準 欧州式
    assert candidate.offset_hours("USDJPY", between_2019) == 3  # 候補 米国式
    confirmed = _e("2023-03-14T10:00:00")
    assert standard.offset_hours("USDJPY", confirmed) == 3  # 行 3: 確認済み（候補なし）
    assert candidate.offset_hours("USDJPY", confirmed) == 3
    assert candidate.offset_hours("EURUSD", confirmed) == 2  # 行 4: 候補 欧州式
    winter = _e("2021-01-12T10:00:00")
    assert standard.offset_hours("GBPUSD", winter) == candidate.offset_hours("GBPUSD", winter) == 2
    assert standard.row_of_utc("USDJPY", confirmed).row == 3
    with pytest.raises(FeasibilityError, match="覆わない"):
        standard.offset_hours("USDJPY", _e("2024-01-01T00:00:00"))


def test_load_time_rule_table_requires_approved_hash(tmp_path: Path) -> None:
    copy = tmp_path / "table.yaml"
    copy.write_text(TABLE.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(FeasibilityError, match="T1"):
        fin.load_time_rule_table(copy, APPROVED)


# --- 足の区分 -----------------------------------------------------------------------


def test_bar_stats_kinds_and_coverage() -> None:
    base = _e("2019-01-08T00:00:00")
    minutes = [base + 60 * i for i in range(8)]
    status = MinuteStatus(
        pair="USDJPY",
        conversion="standard",
        expected=minutes,
        recorded=[minutes[0], minutes[1], minutes[4], minutes[5]],
        no_record=[minutes[2], minutes[3]],
        shortfall=[minutes[7]],
    )
    status.no_record.append(minutes[6])
    intervals = [
        (base, base + 120),
        (base + 120, base + 240),
        (base + 240, base + 360),
        (base + 360, base + 480),
    ]
    bars = fin.bar_stats(status, "2m", intervals)
    assert [b.kind for b in bars] == ["full", "no_record", "full", "shortfall"]
    partial = fin.bar_stats(status, "4m", [(base, base + 240)])[0]
    assert partial.kind == "partial"
    assert partial.coverage == 0.5
    assert partial.unrecorded == 2
    assert partial.first_observed == minutes[0]
    assert partial.last_observed == minutes[1]


# --- 連続区間・準備・研究対象区間 -----------------------------------------------------------


def _day(label: str, kind: str) -> dict[str, Any]:
    start = _e(f"{label}T00:00:00") - 2 * 3600
    end = start + 86400
    return {
        "trading_day": label,
        "day_class": kind,
        "interval_start_utc": iso(start),
        "interval_end_utc": iso(end),
        "_start": start,
        "_end": end,
    }


def _pv(
    days: list[dict[str, Any]], variant: str = "standard", pair: str = "USDJPY"
) -> fin.PairVariant:
    pv = fin.PairVariant(
        pair=pair,
        variant=variant,
        status=MinuteStatus(pair=pair, conversion=variant, expected=[]),
        utc_times=[],
        bars={},
        outside_by_week=Counter(),
        uninterpretable_bars=0,
        data_start=0,
    )
    pv.days = days
    return pv


def _rules(need: int) -> fin.CountingRules:
    return fin.CountingRules(
        **{**_asdict(fin.load_counting_rules(RULES)), "warmup_daily_bars": need}
    )


def _labels(n: int, start: date = date(2019, 1, 1)) -> list[str]:
    return [date.fromordinal(start.toordinal() + i).isoformat() for i in range(n)]


def test_segments_warmup_partial_counts_and_no_record_resets() -> None:
    labels = _labels(10)
    kinds = [
        "full",
        "partial",
        "full",
        "no_record",
        "partial",
        "full",
        "full",
        "full",
        "full",
        "full",
    ]
    pv = _pv([_day(lab, k) for lab, k in zip(labels, kinds, strict=True)])
    fin.continuous_segments(pv, [], _rules(3))
    (segment,) = pv.segments
    assert (segment["start_reason"], segment["end_reason"]) == ("data_start", "data_end")
    assert segment["built_daily_bars"] == 9
    assert segment["partial_daily_bars"] == 2
    assert segment["no_record_days"] == 1
    (window,) = pv.windows
    # 準備完了は 3 本目（部分観測を数える）の公開時刻＝区間の終わり
    assert window["warmup_complete_published_at_utc"] == pv.days[2]["interval_end_utc"]
    assert window["research_daily_bars"] == 6  # 4〜10 日目の作った日足（3 本目より後）
    (reset,) = pv.resets
    assert reset["trading_day"] == labels[3]
    assert reset["was_ready_before"] is True
    assert reset["not_ready_from_utc"] == pv.days[3]["interval_end_utc"]
    assert reset["ready_again_at_utc"] == pv.days[6]["interval_end_utc"]  # やり直し後の 3 本目
    assert reset["not_ready_hours"] == "72.00"
    assert window["research_daily_bars_while_rewarming"] == 3
    assert window["warmup_resets"] == 1


def test_segments_cut_only_by_shortfall_and_closures_do_not_break() -> None:
    labels = _labels(7)
    kinds = ["full", "full", "shortfall", "full", "partial", "full", "full"]
    pv = _pv([_day(lab, k) for lab, k in zip(labels, kinds, strict=True)])
    fin.continuous_segments(pv, ["2019-01-05"], _rules(2))
    first, second = pv.segments
    assert (first["start_reason"], first["end_reason"]) == ("data_start", "acquisition_shortfall")
    assert (second["start_reason"], second["end_reason"]) == (
        "after_acquisition_shortfall",
        "data_end",
    )
    assert second["closure_days_inside"] == 1
    assert [w["research_daily_bars"] for w in pv.windows] == [0, 2]


def test_segment_that_never_warms_up() -> None:
    pv = _pv([_day(lab, "full") for lab in _labels(2)])
    fin.continuous_segments(pv, [], _rules(5))
    (window,) = pv.windows
    assert window["research_start_utc"] == ""
    assert window["note"] == "必要本数に届かない"


def test_common_windows_intersect_pairs() -> None:
    labels = _labels(6)
    a = _pv([_day(lab, "full") for lab in labels], pair="USDJPY")
    kinds_b = ["full", "full", "full", "no_record", "full", "full"]
    b = _pv([_day(lab, k) for lab, k in zip(labels, kinds_b, strict=True)], pair="EURUSD")
    for pv in (a, b):
        fin.continuous_segments(pv, [], _rules(2))
    (common,) = fin.common_windows([a, b])
    assert common["start_utc"] == a.days[1]["interval_end_utc"]
    assert common["trading_days"] == 4
    assert common["trading_days_not_built_in_some_pair"] == 1
    assert common["warmup_reset_days"] == labels[3]


def test_candidate_differences_list_changed_days() -> None:
    labels = _labels(3)
    std = _pv([_day(lab, "full") for lab in labels])
    cand = _pv(
        [_day(lab, k) for lab, k in zip(labels, ["full", "partial", "full"], strict=True)],
        "candidate",
    )
    for pv in (std, cand):
        for d in pv.days:
            d.update(expected_minutes=1440, recorded_minutes=1440, unrecorded_minutes=0)
        fin.continuous_segments(pv, [], _rules(1))
    cand.days[1].update(recorded_minutes=1380, unrecorded_minutes=60)
    rows = fin.candidate_differences(std, cand)
    changed = {(r["key"], r["field"]) for r in rows if r["scope"] == "daily_bar"}
    assert changed == {
        (labels[1], "day_class"),
        (labels[1], "recorded_minutes"),
        (labels[1], "unrecorded_minutes"),
    }


def test_week_sunday_groups_ny_trading_week() -> None:
    assert fin._week_sunday(_e("2021-03-19T20:30:00")) == "2021-03-14"  # 金曜 16:30 NY
    assert fin._week_sunday(_e("2021-03-14T21:30:00")) == "2021-03-14"  # 日曜 17:30 NY


def test_closure_days_are_weekdays_without_daily_bar() -> None:
    labels = {d for d in _labels(2600, date(2017, 1, 2))} - {"2017-12-25"}
    assert fin.closure_days(labels) == ["2017-12-25"]


def test_decision_record_hashes_original_and_requires_both(tmp_path: Path) -> None:
    original = tmp_path / "human_decision.md"
    original.write_text("回答: 「よい（推奨）」\n", encoding="utf-8")
    record = fin.decision_record(str(original), "D12 §0.1.29")
    assert record == {
        "ref": "D12 §0.1.29",
        "file": "human_decision.md",
        "sha256": sha256_file(original),
    }
    assert fin.decision_record("", "") is None
    with pytest.raises(FeasibilityError):
        fin.decision_record(str(original), "")


# --- 第 1 巡のレビューの指摘への回帰テスト ------------------------------------------------


def _window(pair: str, lo: str, hi: str, *, acquired: bool, reason: str = "") -> FileWindow:
    return FileWindow(
        rid=f"F-{pair}-{lo}",
        pair=pair,
        year=int(lo[:4]),
        month=int(lo[5:7]),
        lo_api=_e(f"{lo}T00:00:00"),
        hi_api=_e(f"{hi}T00:00:00"),
        acquired=acquired,
        reason=reason,
    )


def _aggregate(series: PairSeries, expected: list[int]) -> fin.PairVariant:
    standard, _ = fin.load_time_rule_table(TABLE, APPROVED)
    return fin.aggregate_pair_variant(series, standard, expected, {"1m": []}, ["1m"])


def test_failed_first_month_is_shortfall_not_data_start() -> None:
    series = PairSeries(pair="USDJPY")
    series.windows = [
        _window("USDJPY", "2017-01-01", "2017-02-01", acquired=False, reason="状態 error"),
        _window("USDJPY", "2017-02-01", "2017-03-01", acquired=True),
    ]
    feb = _e("2017-02-06T00:00:00")  # API の時刻（UTC は −2 時間）
    series.times_api = [feb]
    jan_minute = _e("2017-01-10T10:00:00")
    feb_minutes = [feb - 2 * 3600, feb - 2 * 3600 + 60]
    pv = _aggregate(series, [jan_minute, *feb_minutes])
    assert pv.data_start == _e("2016-12-31T22:00:00")
    assert pv.status.shortfall == [jan_minute]  # 失敗した月は取得不足（データの開始前ではない）
    assert pv.status.recorded == [feb_minutes[0]]
    assert pv.status.no_record == [feb_minutes[1]]


def test_pair_not_in_table_is_shortfall_not_crash() -> None:
    series = PairSeries(pair="NZDUSD")
    series.windows = [_window("NZDUSD", "2017-02-01", "2017-03-01", acquired=True)]
    series.times_api = [_e("2017-02-06T00:00:00")]
    minute = _e("2017-02-06T10:00:00")
    pv = _aggregate(series, [minute])
    assert pv.uninterpretable_bars == 1
    assert pv.status.shortfall == [minute]
    assert pv.status.recorded == []


def test_acquired_range_end_uses_last_inside_instant() -> None:
    series = PairSeries(pair="USDJPY")
    series.windows = [_window("USDJPY", "2023-12-01", "2024-01-01", acquired=True)]
    standard, _ = fin.load_time_rule_table(TABLE, APPROVED)
    _, acquired, _, _ = fin.convert_with_table(series, standard)
    assert acquired == [(_e("2023-11-30T22:00:00"), _e("2023-12-31T22:00:00"))]


def _table_with(tmp_path: Path, old: str, new: str) -> tuple[Path, Path]:
    text = TABLE.read_text(encoding="utf-8")
    assert old in text
    table = tmp_path / "table.yaml"
    table.write_text(text.replace(old, new, 1), encoding="utf-8")
    approved = tmp_path / "approved.yaml"
    approved.write_text(
        APPROVED.read_text(encoding="utf-8").replace(
            "70bb369bc606d8402b172672c07d75506951f86b1295c00344661a68247b3ec3",
            sha256_file(table),
        ),
        encoding="utf-8",
    )
    return table, approved


def test_time_rule_table_rejects_gap(tmp_path: Path) -> None:
    table, approved = _table_with(
        tmp_path, "{from: 2017-03-26, to: 2017-10-29}", "{from: 2017-04-02, to: 2017-10-29}"
    )
    with pytest.raises(FeasibilityError, match="T2"):
        fin.load_time_rule_table(table, approved)


def test_time_rule_table_rejects_non_sunday_boundary(tmp_path: Path) -> None:
    # 行 1・2 の 2017 年の春の終わりと行 9 の始まりを同じ月曜にずらす（隙間なし、日曜でない）
    text_old = "{from: 2017-03-12, to: 2017-03-26}"
    table, approved = _table_with(tmp_path, text_old, "{from: 2017-03-12, to: 2017-03-27}")
    text = table.read_text(encoding="utf-8")
    text = text.replace(text_old, "{from: 2017-03-12, to: 2017-03-27}")
    text = text.replace("{from: 2017-03-26, to: 2017-10-29}", "{from: 2017-03-27, to: 2017-10-29}")
    table.write_text(text, encoding="utf-8")
    approved.write_text(
        APPROVED.read_text(encoding="utf-8").replace(
            "70bb369bc606d8402b172672c07d75506951f86b1295c00344661a68247b3ec3",
            sha256_file(table),
        ),
        encoding="utf-8",
    )
    with pytest.raises(FeasibilityError, match="T3"):
        fin.load_time_rule_table(table, approved)


def test_time_rule_table_rejects_candidate_on_confirmed_row(tmp_path: Path) -> None:
    old = "    mark: confirmed\n    standard: {name: 米国式, offset_hours: 3}\n    candidates: []"
    new = (
        "    mark: confirmed\n    standard: {name: 米国式, offset_hours: 3}\n"
        "    candidates:\n      - {name: 欧州式, offset_hours: 2}"
    )
    table, approved = _table_with(tmp_path, old, new)
    with pytest.raises(FeasibilityError, match="T5"):
        fin.load_time_rule_table(table, approved)
