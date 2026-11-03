"""研究期間の原ファイルの確認の読み取りの判定（D03 §17.4、ADR-0040 第3項の 4。人工データだけ）。

読んだ事実（`MonthTimes`）を人工の値で作り、表は人工の YAML を本番の読込で読む。止まる境目は
人工の表の期間の中に置く（`until`）。表 版 1 は API の暦日 2024-01-01 以降を覆わないので、実際の
2024 年以降の行はまず「表が覆わない」で止まる（`tests/integration/app/
test_research_raw_verify.py`）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from odyssey_fx.app.config.time_rules import load_time_rule_table
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.mt5_acceptance import Mt5FetchEntry
from odyssey_fx.marketdata.application.raw_verification import (
    MonthTimes,
    RawFileUnreadable,
    RawVerifyReason,
    pre_read_problems,
    verify_raw_files,
)
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTable

T = UtcTime.parse
EURUSD = Symbol("EURUSD")
#: 人工の境目（表の期間の中）。本番は 2024-01-01T00:00Z。
UNTIL = T("2026-01-01T00:00:00Z")
#: 2025-12-31T21:00 API（API の時刻 − 3 時間 = UTC。候補は − 2 時間）。
BASE = int(T("2025-12-31T21:00:00Z").value.timestamp())

_TABLE = """schema_version: 1
id: synthetic_verify_rules
version: 1
judgment_criteria_version: 2
source_section: "人工データ"
approval:
  decisions: ["人工データ"]
conversion_definition: "API の時刻 − UTC（時間）"
lookup_key: "API の生の時刻の暦日"
coverage:
  pairs: [EURUSD]
  from: 2025-11-30
  to: 2026-03-01
marks:
  confirmed: "確認済み"
  assumed_same: "仮定付き（候補の間で変換が同じ）"
  assumed_differs: "仮定付き（候補の間で変換が違う）"
rows:
  - row: 1
    pairs: [EURUSD]
    label: "人工"
    periods:
      - {from: 2025-11-30, to: 2026-03-01}
    status: 仮定付き
    mark: assumed_differs
    standard: {name: 人工の標準, offset_hours: 3}
    candidates:
      - {name: 人工の候補, offset_hours: 2}
    evidence: {discriminating: "なし", opening_position: "なし", price_auxiliary: "なし"}
    observed_scope: "なし"
    extension_assumption: "なし"
    decision: "人工"
"""


@pytest.fixture
def table(tmp_path: Path) -> TimeRuleTable:
    path = tmp_path / "table.yaml"
    path.write_text(_TABLE, encoding="utf-8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    approved = tmp_path / "approved.yaml"
    approved.write_text(
        "schema_version: 1\ntables:\n  - id: synthetic_verify_rules\n    version: 1\n"
        f"    file: {path.name}\n    sha256: {sha}\n    judgment_criteria_version: 2\n"
        '    approved_by: ["人工データ"]\n    file_recorded_by: "人工データ"\n    revoked: null\n',
        encoding="utf-8",
    )
    return load_time_rule_table(path, approved)


def _entry(rid: str, times: list[int], **changes: object) -> Mt5FetchEntry:
    values: dict[str, object] = {
        "rid": rid,
        "symbol": EURUSD,
        "file": f"EURUSD/{rid}.csv.gz",
        "status": "ok",
        "requested_from_api": BASE - 86400,
        "requested_to_api": int(UNTIL.value.timestamp()),
        "rows": len(times),
        "first_bar_time": times[0] if times else None,
        "last_bar_time": times[-1] if times else None,
        "sha256_gz": hashlib.sha256(rid.encode()).hexdigest(),
        "sha256_csv": "b" * 64,
    }
    values.update(changes)
    return Mt5FetchEntry(**values)  # type: ignore[arg-type]


def _read(
    entry: Mt5FetchEntry, rows: list[int | None] | Iterator[int | None], **changes: object
) -> MonthTimes:
    values: dict[str, object] = {
        "sha256": entry.sha256_gz,
        "readable": True,
        "header_ok": True,
        "times": iter(rows),
    }
    values.update(changes)
    return MonthTimes(**values)  # type: ignore[arg-type]


def _verify(
    table: TimeRuleTable, files: list[tuple[Mt5FetchEntry, MonthTimes]]
) -> tuple[object, list[str]]:
    opened: list[str] = []
    reads = {entry.rid: read for entry, read in files}

    def open_times(entry: Mt5FetchEntry) -> MonthTimes:
        opened.append(entry.rid)
        return reads[entry.rid]

    result = verify_raw_files(
        [entry for entry, _ in files],
        symbol=EURUSD,
        locations={entry.rid: f"data/raw/oanda_mt5/d/{entry.file}" for entry, _ in files},
        open_times=open_times,
        table=table,
        until=UNTIL,
    )
    return result, opened


def test_rows_before_the_boundary_under_both_conversions_pass(table: TimeRuleTable) -> None:
    times = [BASE, BASE + 60, BASE + 3600]  # 候補の変換でも足の終わりは 2025-12-31T20:01Z まで
    entry = _entry("a", times)
    result, opened = _verify(table, [(entry, _read(entry, list(times)))])
    assert opened == ["a"]
    assert result.passed == 1 and result.failed == 0 and not result.stopped  # type: ignore[attr-defined]


def test_a_row_past_the_boundary_under_the_candidate_only_stops_the_whole_symbol(
    table: TimeRuleTable,
) -> None:
    """標準（− 3 時間）では前でも、候補（− 2 時間）で境目以降なら止める（両方の変換で確かめる）。"""
    past = int(
        T("2026-01-01T02:30:00Z").value.timestamp()
    )  # 標準 23:30Z（前）・候補 00:30Z（以降）
    first = _entry("a", [BASE, past, BASE + 60])
    second = _entry("b", [BASE])
    result, opened = _verify(
        table,
        [(first, _read(first, [BASE, past, BASE + 60])), (second, _read(second, [BASE]))],
    )
    assert opened == ["a"]  # 残りのファイルは開かない
    (verdict,) = result.verdicts  # type: ignore[attr-defined]
    assert verdict.reason is RawVerifyReason.ROW_AT_OR_AFTER_2024
    assert result.stopped and result.unread == 1  # type: ignore[attr-defined]
    assert verdict.location == "data/raw/oanda_mt5/d/EURUSD/a.csv.gz"


def test_a_row_the_table_does_not_cover_stops_as_well(table: TimeRuleTable) -> None:
    outside = int(T("2026-03-02T00:00:00Z").value.timestamp())
    entry = _entry("a", [BASE, outside])
    result, _ = _verify(table, [(entry, _read(entry, [BASE, outside]))])
    assert result.verdicts[0].reason is RawVerifyReason.TIME_RULE_NOT_COVERED  # type: ignore[attr-defined]
    assert result.stopped  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("times", "changes", "entry_changes", "reason"),
    [
        ([BASE], {"sha256": "f" * 64}, {}, RawVerifyReason.HASH_MISMATCH),
        ([BASE], {"readable": False}, {}, RawVerifyReason.FILE_UNREADABLE),
        ([BASE], {"header_ok": False}, {}, RawVerifyReason.HEADER_MISMATCH),
        ([BASE, None], {}, {"rows": 2}, RawVerifyReason.ROW_MALFORMED),
        ([BASE, BASE + 60], {}, {"rows": 3}, RawVerifyReason.ROW_COUNT_MISMATCH),
        (
            [BASE, BASE + 60],
            {},
            {"rows": 2, "last_bar_time": BASE},
            RawVerifyReason.FIRST_LAST_MISMATCH,
        ),
    ],
)
def test_other_failures_fail_the_file_and_keep_reading_the_rest(
    table: TimeRuleTable,
    times: list[int | None],
    changes: dict[str, object],
    entry_changes: dict[str, object],
    reason: RawVerifyReason,
) -> None:
    good = [t for t in times if t is not None]
    first = _entry("a", good, **entry_changes)
    second = _entry("b", [BASE])
    result, opened = _verify(
        table,
        [(first, _read(first, list(times), **changes)), (second, _read(second, [BASE]))],
    )
    assert opened == ["a", "b"]
    assert [item.reason for item in result.verdicts] == [reason, None]  # type: ignore[attr-defined]
    assert not result.stopped and result.failed == 1  # type: ignore[attr-defined]


def test_a_file_that_breaks_while_reading_is_unreadable(table: TimeRuleTable) -> None:
    def broken() -> Iterator[int | None]:
        yield BASE
        raise RawFileUnreadable("x")

    entry = _entry("a", [BASE, BASE + 60])
    result, _ = _verify(table, [(entry, _read(entry, broken()))])
    assert result.verdicts[0].reason is RawVerifyReason.FILE_UNREADABLE  # type: ignore[attr-defined]


def test_a_hash_mismatch_never_consumes_the_rows(table: TimeRuleTable) -> None:
    def forbidden() -> Iterator[int | None]:
        raise AssertionError("the rows of a file with another hash are never read")
        yield None  # pragma: no cover

    entry = _entry("a", [BASE])
    result, _ = _verify(table, [(entry, _read(entry, forbidden(), sha256="f" * 64))])
    assert result.verdicts[0].reason is RawVerifyReason.HASH_MISMATCH  # type: ignore[attr-defined]


def test_the_pre_read_check_uses_only_the_fetch_record() -> None:
    limit = int(T("2024-01-01T00:00:00Z").value.timestamp())
    ok = _entry("ok", [limit - 60], requested_to_api=limit)
    assert pre_read_problems([ok]) == ()
    assert pre_read_problems([_entry("f", [], status="failed")]) == ("f: NOT_ACQUIRED",)
    assert pre_read_problems([_entry("r", [limit - 60], requested_to_api=limit + 60)]) == (
        "r: REQUEST_PAST_2024",
    )
    assert pre_read_problems([_entry("l", [limit], requested_to_api=limit)]) == (
        "l: LAST_BAR_PAST_2024",
    )
