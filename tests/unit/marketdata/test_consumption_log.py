"""消費の記録の行の版 0・版 1 の読込と構造の誤り（D09 §9.10.4。受入テスト 17）。"""

from __future__ import annotations

import json

import pytest

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.consumption_log import (
    AccessLogReadFailure,
    ConsumptionCandidate,
    ConsumptionReason,
    ConsumptionRow,
    ParsedAccessLog,
    consumption_line_bytes,
    parse_access_log,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

SEALED = "USDJPY_1h_bid/HOLDOUT_SINCE_2024/20240107T2200Z"
OTHER = "USDJPY_1h_bid/HOLDOUT_SINCE_2024/20240114T2200Z"
LEGACY = "USDJPY_1h_bid/LEGACY_HOLDOUT"
PARTITIONS = frozenset({SEALED, OTHER, LEGACY})
SEALED_SET = frozenset({SEALED, OTHER})
CANDIDATE = ConsumptionCandidate(
    experiment_id="a" * 64, execution="1", experiment_name="exp", experiment_version="2"
)


def _row(partition: str = SEALED, permit: str = "p" * 32, **changes: object) -> ConsumptionRow:
    values: dict[str, object] = {
        "row_id": "r" * 32,
        "partition": partition,
        "reason": ConsumptionReason.FINAL_EVALUATION,
        "permit_id": permit,
        "candidate": CANDIDATE,
        "access_id": "",
        "recorded_at": UtcTime.parse("2026-11-20T00:00:00Z"),
        "actor": "人間",
        "purpose": "最終検証",
    }
    values.update(changes)
    return ConsumptionRow(**values)  # type: ignore[arg-type]


def _v0(kind: str = "CONSUMED", partition: str = LEGACY) -> bytes:
    payload = {
        "actor": "t",
        "kind": kind,
        "partition": partition,
        "purpose": "",
        "recorded_at": "2026-09-20T00:00:00Z",
    }
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()


def _parse(data: bytes) -> ParsedAccessLog | AccessLogReadFailure:
    return parse_access_log(data, partitions=PARTITIONS, sealed_partitions=SEALED_SET)


def test_version_0_and_version_1_rows_mix_and_both_consume() -> None:
    data = _v0() + consumption_line_bytes(_row()) + consumption_line_bytes(_row(OTHER))
    parsed = _parse(data)
    assert isinstance(parsed, ParsedAccessLog)
    assert parsed.consumed() == frozenset({LEGACY, SEALED, OTHER})
    assert len(parsed.rows_of_permit("p" * 32)) == 2


def test_the_version_1_line_is_the_canonical_encoding_with_sorted_keys() -> None:
    line = consumption_line_bytes(_row())
    payload = json.loads(line)
    assert line.endswith(b"\n") and b" " not in line.replace("最終検証".encode(), b"")
    assert list(payload) == sorted(payload)
    assert payload["schema_version"] == "1" and payload["kind"] == "CONSUMED"
    assert payload["candidate"] == CANDIDATE.payload()


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        (b"{broken\n", "JSONDecodeError"),
        (
            consumption_line_bytes(_row()).replace(
                b'"schema_version":"1"', b'"schema_version":"2"'
            ),
            "schema_version",
        ),
        (
            json.dumps(json.loads(consumption_line_bytes(_row())), indent=1)
            .replace("\n", "")
            .encode()
            + b"\n",
            "canonical",
        ),
        (consumption_line_bytes(_row()) + consumption_line_bytes(_row(row_id="s" * 32)), "twice"),
        (consumption_line_bytes(_row()).replace(b"FINAL_EVALUATION", b"UNKNOWN"), "ValueError"),
        (_v0(kind="GRANTED", partition=SEALED), "GRANTED"),
        (_v0(kind="RESEARCH_OPT_IN", partition=SEALED), "RESEARCH_OPT_IN"),
        (_v0(partition="USDJPY_4h_bid/RESEARCH_HISTORY"), "lacks"),
        (consumption_line_bytes(_row())[:-1], "fragment"),
    ],
)
def test_structure_errors_make_the_state_underivable(data: bytes, fragment: str) -> None:
    parsed = _parse(data)
    assert isinstance(parsed, AccessLogReadFailure)
    assert fragment in parsed.detail


def test_a_version_1_row_lacking_an_item_is_a_structure_error() -> None:
    payload = json.loads(consumption_line_bytes(_row()))
    del payload["actor"]
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    assert isinstance(_parse(data), AccessLogReadFailure)


def test_version_0_duplicates_are_not_counted() -> None:
    parsed = _parse(_v0() + _v0())
    assert isinstance(parsed, ParsedAccessLog) and len(parsed.legacy) == 2


def test_the_same_partition_under_two_permits_is_two_keys() -> None:
    data = consumption_line_bytes(_row()) + consumption_line_bytes(_row(permit="q" * 32))
    assert isinstance(_parse(data), ParsedAccessLog)


def test_rows_check_the_items_of_their_reason() -> None:
    with pytest.raises(MarketDataValueError):
        _row(reason=ConsumptionReason.VIOLATION_JUDGMENT)  # 候補と目的を持たない
    violation = _row(
        reason=ConsumptionReason.VIOLATION_JUDGMENT, candidate=None, access_id="x" * 32, purpose=""
    )
    assert violation.key == (SEALED, "p" * 32)
    with pytest.raises(MarketDataValueError):
        _row(access_id="x" * 32)  # 最終検証の行は閲覧の識別子を持たない
