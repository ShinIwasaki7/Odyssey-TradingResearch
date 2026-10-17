"""full_period_final（全期間のデータ上の実行可能性の最終集計）の単体テスト。人工データと規則ファイルだけ。"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from tools.survey import full_period_final as fin
from tools.survey.full_period_feasibility import FeasibilityError, MinuteStatus, iso, sha256_file

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
        shortfall_reasons=[],
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
