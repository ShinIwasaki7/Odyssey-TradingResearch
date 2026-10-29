"""MT5 経路の人工データ（D12 §11.13 の受入検証 C-1〜C-7 の入力）。

- 時刻の規則の表と承認済みの一覧は、人工の YAML を一時ディレクトリに書き、本番と同じ読込
  （`app.config.time_rules.load_time_rule_table`）で読む（ハッシュの照合 T1 も本番と同じ）。
  表は 1 行
  （USDJPY、変換 +2 時間、仮定付き（候補の間で変換が同じ））。
- 1 分足の行は「API の時刻 = UTC + 2 時間」で作る。指定した分を無記録（行を作らない）にでき、
指定した分
  の 4 値を差し替えられる。それ以外の分の 4 値は既定の値（150.000 / 150.100 / 149.900 / 150.000）。
- 取得記録は 1 か月 1 要求（API の時刻の月の区間）。状態を `failed` にした月は取得不足 (b) になる。

価格は人工の値で、実データの価格ではない。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from odyssey_fx.app.config.time_rules import load_time_rule_table
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.application.acceptance import PendingSnapshot, approve, finalize
from odyssey_fx.marketdata.application.mt5_acceptance import (
    Mt5FetchEntry,
    Mt5RawRow,
    Mt5ReadFile,
    build_mt5_pending,
)
from odyssey_fx.marketdata.application.snapshot_access import ReadableSnapshot
from odyssey_fx.marketdata.domain.access import AccessBoundaries
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.series import PriceBasis
from odyssey_fx.marketdata.domain.snapshot import Approval, BasisDeclaration, QualityReadRecord
from odyssey_fx.marketdata.domain.time_rule import TimeRuleTable, TimeRuleVariant
from odyssey_fx.marketdata.domain.timeframe_def import FixedUtcAlignment, TimeframeDefinition
from tests.fixtures.synthetic import market

__all__ = [
    "APPROVED_LIST",
    "BASE",
    "OFFSET_HOURS",
    "TABLE_FILE",
    "TARGETS",
    "TF_1M",
    "TIMEFRAME_DEFS",
    "MonthSpec",
    "build_pending",
    "minute_rows",
    "readable",
    "write_rule_files",
]

USDJPY = Symbol("USDJPY")
OFFSET_HOURS = 2
TF_1M = TimeframeDefinition(
    ref=TimeframeRef("1m", 1),
    nominal_length=timedelta(minutes=1),
    alignment=FixedUtcAlignment(step=timedelta(minutes=1)),
)
TIMEFRAME_DEFS = {"1m": TF_1M, **market.TIMEFRAME_DEFS}
TARGETS = ("15m", "1h", "4h_ny17", "1d_ny17")

#: 既定の 4 値（始値・高値・安値・終値）。
BASE = ("150.000", "150.100", "149.900", "150.000")

TABLE_FILE = Path("docs/design/time_rules/time_rule_table_v1.yaml")
APPROVED_LIST = Path("docs/design/time_rules/approved_time_rule_tables.yaml")

_TABLE_TEXT = """schema_version: 1
id: synthetic_mt5_time_rules
version: 1
judgment_criteria_version: 2
source_section: "人工データ"
approval:
  decisions: ["人工データ"]
conversion_definition: "API の時刻 − UTC（時間）"
lookup_key: "API の生の時刻の暦日"
coverage:
  pairs: [USDJPY]
  from: 2025-11-30
  to: 2026-03-01
marks:
  confirmed: "確認済み"
  assumed_same: "仮定付き（候補の間で変換が同じ）"
  assumed_differs: "仮定付き（候補の間で変換が違う）"
rows:
  - row: 1
    pairs: [USDJPY]
    label: "人工"
    periods:
      - {from: 2025-11-30, to: 2026-03-01}
    status: 仮定付き
    mark: assumed_same
    standard: {name: 人工, offset_hours: 2}
    candidates: []
    evidence: {discriminating: "なし", opening_position: "なし", price_auxiliary: "なし"}
    observed_scope: "なし"
    extension_assumption: "なし"
    decision: "人工"
"""


def write_rule_files(root: Path, *, revoked: str | None = None) -> TimeRuleTable:
    """人工の表と承認済みの一覧を `root` の下（本番と同じ相対パス）に書き、本番の読込で読む。"""
    table_path = root / TABLE_FILE
    table_path.parent.mkdir(parents=True, exist_ok=True)
    table_path.write_text(_TABLE_TEXT, encoding="utf-8")
    sha = hashlib.sha256(table_path.read_bytes()).hexdigest()
    revoked_text = "null" if revoked is None else f'"{revoked}"'
    (root / APPROVED_LIST).write_text(
        "schema_version: 1\n"
        "tables:\n"
        "  - id: synthetic_mt5_time_rules\n"
        "    version: 1\n"
        f"    file: {TABLE_FILE.as_posix()}\n"
        f"    sha256: {sha}\n"
        "    judgment_criteria_version: 2\n"
        '    approved_by: ["人工データ"]\n'
        '    file_recorded_by: "人工データ"\n'
        f"    revoked: {revoked_text}\n",
        encoding="utf-8",
    )
    if revoked is not None:
        # 取り消した一覧では読込（T1）が通らないので、表は取り消す前の一覧で読む。
        approved_ok = root / "approved_ok.yaml"
        approved_ok.write_text(
            (root / APPROVED_LIST)
            .read_text(encoding="utf-8")
            .replace(f'revoked: "{revoked}"', "revoked: null"),
            encoding="utf-8",
        )
        return load_time_rule_table(table_path, approved_ok)
    return load_time_rule_table(table_path, root / APPROVED_LIST)


def _epoch(moment: UtcTime) -> int:
    return int(moment.value.timestamp())


def minute_rows(
    calendar: TradingCalendar,
    window: Interval,
    *,
    unrecorded: Iterable[Interval] = (),
    prices: Mapping[UtcTime, tuple[str, str, str, str]] | None = None,
    spreads: Mapping[UtcTime, int] | None = None,
) -> tuple[Mt5RawRow, ...]:
    """`window` の中のセッションの分ごとに 1 分足の行を作る（API の時刻 = UTC + 2 時間）。

    `unrecorded` に入る分は行を作らない（無記録）。`prices` で分ごとの 4 値を、`spreads` で分ごとの
    `spread` を差し替える（既定は 3）。
    """
    holes = tuple(unrecorded)
    overrides = dict(prices or {})
    spread_overrides = dict(spreads or {})
    rows: list[Mt5RawRow] = []
    for session in calendar.sessions(window):
        moment = session.start
        while moment < session.end:
            if not any(hole.contains(moment) for hole in holes):
                values = overrides.get(moment, BASE)
                rows.append(
                    Mt5RawRow(
                        api_time=_epoch(moment) + OFFSET_HOURS * 3600,
                        open=values[0],
                        high=values[1],
                        low=values[2],
                        close=values[3],
                        spread=spread_overrides.get(moment, 3),
                    )
                )
            moment = moment + timedelta(minutes=1)
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class MonthSpec:
    """取得記録の要求 1 件（API の時刻の月）。"""

    year: int
    month: int
    rows: tuple[Mt5RawRow, ...] = ()
    status: str = "ok"


def _month_bounds(year: int, month: int) -> tuple[int, int]:
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=UTC)
    return int(start.timestamp()), int(end.timestamp())


#: 人工データのアクセス分類の境界（2026 年は研究履歴）。公開フィードにも同じ境界を渡す。
BOUNDARIES = AccessBoundaries(
    research_until=UtcTime.parse("2027-01-01T00:00:00Z"),
    holdout_until=UtcTime.parse("2028-01-01T00:00:00Z"),
)


def build_pending(
    calendar: TradingCalendar,
    table: TimeRuleTable,
    months: Sequence[MonthSpec],
    *,
    variant: TimeRuleVariant = TimeRuleVariant.STANDARD,
    boundaries: AccessBoundaries | None = None,
) -> PendingSnapshot:
    """人工の取得記録と行から MT5 経路の暫定 snapshot を作る（本番の受入れの関数を呼ぶ）。"""
    boundaries = BOUNDARIES if boundaries is None else boundaries
    entries: list[Mt5FetchEntry] = []
    files: dict[str, Mt5ReadFile] = {}
    for spec in months:
        lo, hi = _month_bounds(spec.year, spec.month)
        rid = f"F-USDJPY-{spec.year}-{spec.month:02d}"
        rows = tuple(row for row in spec.rows if lo <= row.api_time < hi)
        payload = "\n".join(
            f"{row.api_time},{row.open},{row.high},{row.low},{row.close}" for row in rows
        ).encode("utf-8")
        sha_csv = hashlib.sha256(payload).hexdigest()
        sha_gz = hashlib.sha256(b"gz" + payload).hexdigest()
        entry = Mt5FetchEntry(
            rid=rid,
            symbol=USDJPY,
            file=f"USDJPY/USDJPY_{spec.year}-{spec.month:02d}_m1.csv.gz",
            status=spec.status,
            requested_from_api=lo,
            requested_to_api=hi,
            rows=len(rows),
            first_bar_time=rows[0].api_time if rows else None,
            last_bar_time=rows[-1].api_time if rows else None,
            sha256_gz=sha_gz,
            sha256_csv=sha_csv,
        )
        entries.append(entry)
        if spec.status == "ok":
            files[rid] = Mt5ReadFile(
                entry=entry, sha256_gz=sha_gz, sha256_csv=sha_csv, header_ok=True, rows=rows
            )
    built = build_mt5_pending(
        created_at=UtcTime.parse("2026-10-17T00:00:00Z"),
        symbol=USDJPY,
        entries=entries,
        files=files,
        source_paths={entry.rid: f"data/raw/oanda_mt5/synthetic/{entry.file}" for entry in entries},
        fetch_manifest_sha256="b" * 64,
        column_mapping="oanda_mt5_m1@v1",
        table=table,
        variant=variant,
        calendar=calendar,
        minute_def=TF_1M,
        timeframe_defs=TIMEFRAME_DEFS,
        targets=TARGETS,
        boundaries=boundaries,
        basis_declaration=BasisDeclaration(value=PriceBasis.BID, verified=False),
        code_version="synthetic",
    )
    # 研究期間の受入れ（`data accept-mt5`）と同じく、品質確認の許可を要しなかったと記録する
    # （ADR-0036 第7項。分類・承認がこの項目を確かめる）。
    return PendingSnapshot(
        manifest=built.manifest.with_quality_read(QualityReadRecord(permit_required=False)),
        report=built.report,
        partition_bars=built.partition_bars,
    )


def readable(pending: PendingSnapshot) -> ReadableSnapshot:
    """分類の要らない暫定 snapshot を確定・承認して、読み取りの関門を通る形にする。"""
    finalized = finalize(pending, ())
    manifest = approve(
        finalized,
        Approval(approved_by="test", approved_at=UtcTime.parse("2026-10-17T00:00:00Z")),
    )
    return ReadableSnapshot(
        manifest=manifest, directory_name=str(manifest.snapshot_id()), report=pending.report
    )


def day(value: str) -> date:
    """`YYYY-MM-DD` の日付。"""
    return date.fromisoformat(value)
