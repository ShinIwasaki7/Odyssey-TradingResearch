"""MT5 経路の規則のファイルと型（D03 §15.2・§15.9・§15.12、D12 §11.13 の行 1・2・15）。"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pytest

from odyssey_fx.app.config.counting_rules import load_counting_rules
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.time_rules import ApprovedTable, load_time_rule_table, require_approved
from odyssey_fx.app.mt5 import check_calendar_derivation, load_mt5_rules
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.counting_rules import PendingRule, UnsupportedRule
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.minute_status import (
    MINUTE,
    MinuteCounts,
    ShortfallReason,
    classify_minutes,
)
from odyssey_fx.marketdata.domain.readiness import ScheduledBar, history_resets
from odyssey_fx.marketdata.domain.time_rule import (
    TimeRuleMark,
    TimeRulePeriod,
    TimeRuleRow,
    TimeRuleTable,
    TimeRuleVariant,
)

REPO = Path(__file__).resolve().parents[3]

#: PR #84 で承認された規則のファイルの SHA-256（D12 §0.1.26・§0.1.27、D03 §15.6 の 1・§15.9 の 2）。
#: 実装はこのファイルを読み、ハッシュを snapshot と実行の記録に残す。
COUNTING_RULES_SHA256 = "fa98eef8cec035a4564b849bc4098d1488aee968bc3a5c2d44efb3ba75ca6444"
CALENDAR_SHA256 = "a9da0d9ae6b3246b5bb67757a7b6cb4ac50e5a012c96a5c4a075bc1148a79b96"
TIME_RULE_TABLE_SHA256 = "70bb369bc606d8402b172672c07d75506951f86b1295c00344661a68247b3ec3"


def _sha(path: str) -> str:
    return hashlib.sha256((REPO / path).read_bytes()).hexdigest()


def test_the_rule_files_have_the_approved_hashes() -> None:
    """実装が読む 3 つの規則のファイルが、設計（PR #84）で承認されたハッシュと一致する。"""
    assert _sha("docs/design/counting_rules/counting_rules_v1.yaml") == COUNTING_RULES_SHA256
    assert _sha("configs/calendars/fx_ny17_oanda_v1.yaml") == CALENDAR_SHA256
    assert _sha("docs/design/time_rules/time_rule_table_v1.yaml") == TIME_RULE_TABLE_SHA256


def test_load_mt5_rules_reads_and_cross_checks_the_files() -> None:
    """規則のファイルを本番の入口で読み、互いに照合する（T1〜T5、カレンダーと休場の定義のハッシュ）。

    読んだ数え方の規則と表のハッシュは、実行の記録に書く値（D03 §15.12）と同じ。
    """
    rules = load_mt5_rules(REPO)
    assert rules.counting.sha256 == COUNTING_RULES_SHA256
    assert rules.table.sha256 == TIME_RULE_TABLE_SHA256
    assert rules.counting.calendar().sha256 == CALENDAR_SHA256
    assert (rules.calendar.id, rules.calendar.version) == ("fx_ny17_oanda", 1)
    assert rules.targets == ("15m", "1h", "4h_ny17", "1d_ny17")
    assert len(rules.table.rows) == 9


def test_the_oanda_calendar_equals_version_2_minus_the_holiday_definition() -> None:
    """D03 §15.6 の 1: `fx_ny17_oanda` 版 1 の 2017〜2023 年のセッションの分は、版 2
    のセッションの分から
    休場の定義ファイルの 11 行の区間を除いたもの（除かれた分 2,500）。道具の再集計（付録 K-14）
    と本番の
    休場が同じであることの 1 分単位の照合（D12 §11.13 の 15）。"""
    rules = load_mt5_rules(REPO)
    assert check_calendar_derivation(REPO, rules) == 2500


def test_the_counting_rules_match_the_implemented_procedure() -> None:
    """数え方の規則の値（版 1）が実装の手順と同じ。null の項目は無い（第 20 回の決定。D12
    §0.1.27）。"""
    rules = load_counting_rules(REPO / "docs/design/counting_rules/counting_rules_v1.yaml")
    rules.require_supported()
    assert rules.warmup_daily_bars == 60
    assert rules.variants == ("standard", "candidate")


def test_a_null_counting_rule_stops_the_step_that_needs_it(tmp_path: Path) -> None:
    """D03 §15.12: null の項目を使う処理は既定値で埋めずに失敗し、実装と違う値も黙って使わない。"""
    text = (REPO / "docs/design/counting_rules/counting_rules_v1.yaml").read_text(encoding="utf-8")
    pending = tmp_path / "pending.yaml"
    pending.write_text(
        text.replace("intraday_no_record_bar: resets_like_daily", "intraday_no_record_bar: null"),
        encoding="utf-8",
    )
    with pytest.raises(PendingRule):
        load_counting_rules(pending).require_supported("intraday_no_record_bar")
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        text.replace("coverage_threshold: none", "coverage_threshold: half"), encoding="utf-8"
    )
    with pytest.raises(UnsupportedRule):
        load_counting_rules(changed).require_supported()


def test_a_table_not_in_the_approved_list_is_refused(tmp_path: Path) -> None:
    """T1（D12-48）: 承認済みの一覧に無いハッシュの表は読まない。"""
    original = (REPO / "docs/design/time_rules/time_rule_table_v1.yaml").read_text(encoding="utf-8")
    edited = tmp_path / "table.yaml"
    edited.write_text(original + "\n# 1 バイトでも変えれば別の版\n", encoding="utf-8")
    with pytest.raises(MarketDataValueError, match="not in the approved list"):
        load_time_rule_table(edited, REPO / "docs/design/time_rules/approved_time_rule_tables.yaml")
    with pytest.raises(MarketDataValueError, match="revoked"):
        require_approved(
            (ApprovedTable("t", 1, "f", "a" * 64, "取り消し"),),
            table_id="t",
            version=1,
            sha256="a" * 64,
        )


def _row(
    row: int, start: date, end: date, mark: TimeRuleMark, offsets: tuple[int, ...]
) -> TimeRuleRow:
    return TimeRuleRow(
        row=row,
        pairs=("USDJPY",),
        periods=(TimeRulePeriod(start=start, end=end),),
        mark=mark,
        standard_offset_hours=2,
        candidate_offset_hours=offsets,
    )


def _table(*rows: TimeRuleRow) -> TimeRuleTable:
    return TimeRuleTable(
        table_id="t",
        version=1,
        sha256="a" * 64,
        pairs=("USDJPY",),
        coverage_start=date(2017, 1, 1),
        coverage_end=date(2017, 2, 5),
        rows=rows,
    )


def test_time_rule_table_structure_checks() -> None:
    """T2（重なり・隙間）・T3（境目が日曜）・T5（候補と区分）を構築時に拒否する。"""
    same = TimeRuleMark.ASSUMED_SAME
    _table(
        _row(1, date(2017, 1, 1), date(2017, 1, 15), same, ()),
        _row(2, date(2017, 1, 15), date(2017, 2, 5), TimeRuleMark.ASSUMED_DIFFERS, (3,)),
    )
    with pytest.raises(MarketDataValueError, match="T2"):
        _table(
            _row(1, date(2017, 1, 1), date(2017, 1, 15), same, ()),
            _row(2, date(2017, 1, 22), date(2017, 2, 5), same, ()),
        )
    with pytest.raises(MarketDataValueError, match="T3"):
        _table(
            _row(1, date(2017, 1, 1), date(2017, 1, 16), same, ()),
            _row(2, date(2017, 1, 16), date(2017, 2, 5), same, ()),
        )
    with pytest.raises(MarketDataValueError, match="T5"):
        _row(1, date(2017, 1, 1), date(2017, 2, 5), same, (3,))
    with pytest.raises(MarketDataValueError, match="T5"):
        _row(1, date(2017, 1, 1), date(2017, 2, 5), TimeRuleMark.ASSUMED_DIFFERS, ())


def test_variant_conversion_follows_the_table() -> None:
    """D12-92 (a): `candidate` は候補を持つ行だけを候補の変換に替える。表が覆わない日は
    `None`（取得不足 (d)）。"""
    table = _table(
        _row(1, date(2017, 1, 1), date(2017, 1, 15), TimeRuleMark.ASSUMED_SAME, ()),
        _row(2, date(2017, 1, 15), date(2017, 2, 5), TimeRuleMark.ASSUMED_DIFFERS, (3,)),
    )
    first = table.row_for("USDJPY", date(2017, 1, 3))
    second = table.row_for("USDJPY", date(2017, 1, 20))
    assert first is not None and second is not None
    assert first.offset_hours(TimeRuleVariant.CANDIDATE) == 2
    assert second.offset_hours(TimeRuleVariant.STANDARD) == 2
    assert second.offset_hours(TimeRuleVariant.CANDIDATE) == 3
    assert table.row_for("USDJPY", date(2017, 2, 5)) is None
    assert table.row_for("EURUSD", date(2017, 1, 3)) is None


def test_classify_minutes_order_and_exclusivity() -> None:
    """D03 §15.2: 1 分に区分は 1 つ。休場 → 取得不足 → 記録あり → 無記録の順。
    取得不足は無記録にしない。"""
    m = MINUTE
    ledger = classify_minutes(
        sessions=[(0, 10 * m), (20 * m, 30 * m)],
        recorded_minutes=[0, m, 4 * m, 15 * m, 20 * m, 29 * m],
        acquired=[(0, 25 * m)],
        failed=[(25 * m, 28 * m, ShortfallReason.REQUEST_FAILED_OR_NOT_FETCHED)],
        data_start=0,
        data_end=30 * m,
    )
    assert ledger.counts(0, 30 * m) == MinuteCounts(
        expected=20, recorded=4, unrecorded=11, shortfall=5
    )
    reasons = [(s.start // m, s.end // m, s.reason) for s in ledger.shortfall]
    assert reasons == [
        (25, 28, ShortfallReason.REQUEST_FAILED_OR_NOT_FETCHED),
        (28, 30, ShortfallReason.OUTSIDE_ACQUIRED_RANGE),
    ]
    # 休場帯の足（15 分）と取得の範囲の外の足（29 分）は記録ありに数えない。
    assert not ledger.is_recorded(15 * m)
    assert not ledger.is_recorded(29 * m)
    assert ledger.resumptions_in(0, 30 * m) == ((4 * m, 2 * m),)


def test_history_resets_count_partial_bars_and_skip_closures() -> None:
    """D12-82〜D12-84: 作られた足（部分観測を含む）を数え、無記録の足で数え直す。
    準備が整う時刻は公開時刻。"""

    def bar(hour: int, built: bool) -> ScheduledBar:
        start = UtcTime.parse(f"2026-01-05T{hour:02d}:00:00Z")
        end = UtcTime.parse(f"2026-01-05T{hour + 1:02d}:00:00Z")
        return ScheduledBar(bar_start=start, available_at=end, built=built, no_record=not built)

    sequence = [
        bar(0, True),
        bar(1, False),
        bar(2, True),
        bar(3, False),
        bar(4, True),
        bar(5, True),
    ]
    resets = history_resets(sequence, 2)
    assert [r.ready_at for r in resets] == [
        UtcTime.parse("2026-01-05T06:00:00Z"),
        UtcTime.parse("2026-01-05T06:00:00Z"),
    ]


def test_counting_rules_file_is_required(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_counting_rules(tmp_path / "missing.yaml")


def test_the_file_table_matches_the_survey_tool_for_every_day() -> None:
    """D12 §11.13 の行 1（D03 §15.9 の 4）: 表のファイルと、付録 K-12・K-14 の集計に使った道具の関数
    （`tools/survey/mt5_gap_aggregate.time_rule`）が、8 通貨 × 2017-01-01〜2023-12-31
    の全暦日で同じ行・
    区分・標準の変換・候補の変換を返す（設計 PR で行った突き合わせの回帰テスト）。"""
    from datetime import timedelta

    from tools.survey.mt5_gap_aggregate import time_rule

    table = load_mt5_rules(REPO).table
    status = {
        TimeRuleMark.CONFIRMED: "確認済み",
        TimeRuleMark.ASSUMED_SAME: "仮定付き",
        TimeRuleMark.ASSUMED_DIFFERS: "仮定付き",
    }
    checked = 0
    for pair in table.pairs:
        day = date(2017, 1, 1)
        while day < date(2024, 1, 1):
            row = table.row_for(pair, day)
            tool = time_rule(pair, day)
            assert row is not None
            assert row.row == tool.row, (pair, day)
            assert status[row.mark] == tool.status, (pair, day)
            assert row.standard_offset_hours == tool.standard.hours, (pair, day)
            assert row.candidate_offset_hours == tuple(c.hours for c in tool.candidates)
            checked += 1
            day += timedelta(days=1)
    assert checked == 20448
