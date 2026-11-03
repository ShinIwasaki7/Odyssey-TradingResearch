"""MT5 経路の結線（D03 §15、D12 §11.13）。

規則のファイル（数え方の規則・時刻の規則の表・承認済みの一覧・`fx_ny17_oanda` のカレンダー・時間足の
定義 版 2）を読み、互いの食い違いを確かめてから、受入れ（`marketdata.application.mt5_acceptance`）を
結線する。業務の規則は持たない（D01 §7）。

規則のファイルの置き場は数え方の規則のファイルが指す（D03 §15.12 の表）。
全期間の集計の道具（PR-B）と
基盤が同じ入口（`load_mt5_rules`）で同じファイルを読むので、規則を別々に持たない（2026-10-17 の
指示 3-C）。

研究期間の受入れ（品質確認の許可を要しない読み取り）は、原ファイルを読む前に確認済みハッシュの
一覧（`configs/datasources/oanda_mt5_m1_verified_raw_<取得日>.yaml`。`load_verified_raw`）と
照合する（ADR-0036 第8項、ADR-0040 第4項、D03 §17.7。`accept_mt5_in_memory`）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from odyssey_fx.app.config.calendars import load_calendar, load_timeframes
from odyssey_fx.app.config.counting_rules import load_counting_rules
from odyssey_fx.app.config.mt5_datasource import Mt5DataSourceConfig, load_mt5_datasource
from odyssey_fx.app.config.time_rules import load_time_rule_table
from odyssey_fx.app.config.verified_raw import load_verified_raw_list, verified_raw_path
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.mt5_csv_source import (
    file_sha256,
    read_fetch_manifest,
    read_symbol_files,
    require_listed_before_reading,
)
from odyssey_fx.marketdata.application.acceptance import PendingSnapshot
from odyssey_fx.marketdata.application.mt5_acceptance import (
    Mt5Boundaries,
    Mt5FetchEntry,
    build_mt5_pending,
)
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.counting_rules import CountingRules
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.time_rule import (
    TimeRuleTable,
    TimeRuleVariant,
    check_lookup_agreement,
)
from odyssey_fx.marketdata.domain.timeframe_def import TimeframeDefinition
from odyssey_fx.marketdata.domain.verified_raw import VerifiedRawList

__all__ = [
    "DEFAULT_COUNTING_RULES",
    "DEFAULT_MT5_DATASOURCE",
    "SEALED_COUNTING_RULES",
    "DEFAULT_TIMEFRAMES_V2",
    "Mt5Rules",
    "accept_mt5_in_memory",
    "load_mt5_rules",
    "load_verified_raw",
    "require_calendar_matches_derivation",
    "require_list_matches_fetch_record",
]

#: 数え方の規則 版 1（D03 §15.12。PR #84 で置かれる）。
DEFAULT_COUNTING_RULES = Path("docs/design/counting_rules/counting_rules_v1.yaml")
#: 2024 年以降の封印の取り込み用の数え方の規則 版 1（規則の値は版 1 と同じ。指す表と
#: カレンダーが違う。D03 §16.2。順 6 の S4a）。
SEALED_COUNTING_RULES = Path("docs/design/counting_rules/counting_rules_sealed_v1.yaml")
#: 列対応の宣言（MT5 経路）。
DEFAULT_MT5_DATASOURCE = Path("configs/datasources/oanda_mt5_m1_v1.yaml")
#: 1 分足を含む時間足の定義 版 2。
DEFAULT_TIMEFRAMES_V2 = Path("configs/calendars/timeframes_v2.yaml")


@dataclass(frozen=True, slots=True)
class Mt5Rules:
    """MT5 経路の規則一式（照合済み）。"""

    counting: CountingRules
    table: TimeRuleTable
    calendar: TradingCalendar
    timeframe_defs: Mapping[str, TimeframeDefinition]
    approved_list: Path

    @property
    def targets(self) -> tuple[str, ...]:
        """1 分足から作る上位足（数え方の規則の `bars.timeframes`）。"""
        return self.counting.timeframes


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _holiday_closures(path: Path, tz_calendar: TradingCalendar) -> list[tuple[int, int]]:
    """休場の定義ファイルの区間（UTC の epoch 秒）。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: list[tuple[int, int]] = []
    for entry in payload["entries"]:
        start = UtcTime.parse(str(entry["start_utc"]).replace("Z", ":00Z"))
        end = UtcTime.parse(str(entry["end_utc"]).replace("Z", ":00Z"))
        out.append((int(start.value.timestamp()), int(end.value.timestamp())))
    del tz_calendar
    return out


def require_calendar_matches_derivation(
    calendar: TradingCalendar,
    base: TradingCalendar,
    holidays: Sequence[tuple[int, int]],
    *,
    window: Interval,
) -> int:
    """カレンダーのセッションの分が「版 2 のセッションの分 − 休場の定義の区間」と一致するか。

    D03 §15.6 の 1 の正式化の手順（版 2 と定義ファイルの和集合）を、ファイルにしたカレンダーが守って
    いることを 1 分の単位で確かめる（道具の再集計（付録 K-14）と本番の休場が同じであることの検証。
    D12 §11.13 の 15）。除かれた分の数を返す。
    """

    def minutes(cal: TradingCalendar) -> set[int]:
        out: set[int] = set()
        for session in cal.sessions(window):
            lo = int(session.start.value.timestamp())
            hi = int(session.end.value.timestamp())
            out.update(range(lo, hi, 60))
        return out

    base_minutes = minutes(base)
    removed = {minute for minute in base_minutes if any(lo <= minute < hi for lo, hi in holidays)}
    expected = base_minutes - removed
    actual = minutes(calendar)
    if actual != expected:
        extra = sorted(actual - expected)[:3]
        missing = sorted(expected - actual)[:3]
        raise MarketDataValueError(
            f"the calendar {calendar.id} v{calendar.version} does not equal the version-2 sessions"
            f" minus the holiday definition (extra {extra}, missing {missing}; D03 §15.6 の 1)"
        )
    return len(removed)


def load_mt5_rules(repo_root: Path, counting_rules: Path = DEFAULT_COUNTING_RULES) -> Mt5Rules:
    """規則のファイルを読み、互いに照合する（D03 §15.9・§15.12、D06 §18.9 の 3）。

    1. 数え方の規則を読み、実装の手順と食い違わないことを確かめる（`require_supported`）。
    2. 時刻の規則の表を承認済みの一覧と照合して読む（T1〜T3・T5）。
    3. カレンダーのファイルの SHA-256・識別・版が数え方の規則の宣言（D12-93 (a)）と一致する。
    4. 休場の定義ファイルの SHA-256 が数え方の規則の値と一致する。
    5. 検査 T4（表を API の暦日で引いても UTC の暦日で引いても同じ行）。
    """
    rules = load_counting_rules(repo_root / counting_rules)
    rules.require_supported()
    approved = repo_root / rules.approved_list_file
    table = load_time_rule_table(repo_root / rules.time_rule_table_file, approved)
    declared = rules.calendar()
    calendar_path = repo_root / declared.file
    if _sha256(calendar_path) != declared.sha256:
        raise MarketDataValueError(
            f"{declared.file} does not have the SHA-256 the counting rules declare"
            f" ({declared.sha256}; D03 §15.6 の 1)"
        )
    calendar = load_calendar(calendar_path)
    if (calendar.id, calendar.version) != (declared.calendar_id, declared.version):
        raise MarketDataValueError(
            f"{declared.file} declares {calendar.id} v{calendar.version}, but the counting rules"
            f" name {declared.calendar_id} v{declared.version}"
        )
    holidays = repo_root / rules.holiday_exceptions_file
    if _sha256(holidays) != rules.holiday_exceptions_sha256:
        raise MarketDataValueError(
            f"{rules.holiday_exceptions_file} does not have the SHA-256 the counting rules declare"
        )
    span = Interval(
        start=UtcTime.parse(f"{table.coverage_start.isoformat()}T00:00:00Z"),
        end=UtcTime.parse(f"{table.coverage_end.isoformat()}T00:00:00Z"),
    )
    check_lookup_agreement(table, calendar.sessions(span))
    timeframe_defs = load_timeframes(repo_root / DEFAULT_TIMEFRAMES_V2)
    for target in (rules.built_from, *rules.timeframes):
        if target not in timeframe_defs:
            raise MarketDataValueError(f"the timeframe {target!r} is not defined (D03 §15.4)")
    return Mt5Rules(
        counting=rules,
        table=table,
        calendar=calendar,
        timeframe_defs=timeframe_defs,
        approved_list=approved,
    )


def check_calendar_derivation(repo_root: Path, rules: Mt5Rules) -> int:
    """`require_calendar_matches_derivation` を規則のファイルで呼ぶ（研究期間全体）。"""
    base = load_calendar(repo_root / rules.counting.calendar_base.file)
    holidays = _holiday_closures(repo_root / rules.counting.holiday_exceptions_file, base)
    window = Interval(
        start=UtcTime.parse(f"{rules.table.coverage_start.isoformat()}T00:00:00Z"),
        end=UtcTime.parse(f"{rules.table.coverage_end.isoformat()}T00:00:00Z"),
    )
    return require_calendar_matches_derivation(rules.calendar, base, holidays, window=window)


def load_verified_raw(repo_root: Path, fetch_date: str) -> VerifiedRawList:
    """取得日の確認済みハッシュの一覧を読む（D03 §17.7 の 1）。

    一覧が無ければ `HoldoutAccessViolation`（研究期間の原ファイルは許可なしでは読めない。A2 の
    merge から A3a の merge までの間はこの状態。A1-8）。読込の検査に反すれば `ConfigError`。
    """
    path = Path(repo_root) / verified_raw_path(fetch_date)
    if not path.is_file():
        raise HoldoutAccessViolation(
            f"there is no verified hash list for the fetch date {fetch_date}"
            f" ({verified_raw_path(fetch_date).as_posix()}); raw files are taken to contain data"
            " from 2024 unless listed, so nothing is read without a quality-check permit"
            " (ADR-0036 第8項, D03 §17.7 の 1)"
        )
    return load_verified_raw_list(path, fetch_date=fetch_date)


def require_list_matches_fetch_record(
    verified: VerifiedRawList,
    entries: Sequence[Mt5FetchEntry],
    *,
    fetch_manifest_sha256: str,
    location_prefix: str,
) -> None:
    """一覧が受入れの読む取得記録と食い違わないか（D03 §17.3 の 5）。

    一覧の `fetch_manifest_sha256` が取得記録の SHA-256 と一致し、一覧の各行の置き場が取得記録の
    ファイル（正規の置き場 `location_prefix/<file>`）で、`sha256` がそのファイルの `sha256_gz` と
    一致すること。一致しなければ一覧を誤りとして止める（何も読まない。`HoldoutAccessViolation`）。
    """
    if verified.fetch_manifest_sha256 != fetch_manifest_sha256:
        raise HoldoutAccessViolation(
            f"the verified hash list {verified.source} was made for the fetch record"
            f" {verified.fetch_manifest_sha256}, not for {fetch_manifest_sha256}; the list is"
            " taken to be wrong and nothing is read (D03 §17.3 の 5)"
        )
    recorded = {f"{location_prefix}/{entry.file}": entry for entry in entries}
    for item in verified.files:
        entry = recorded.get(item.location)
        if entry is None or entry.symbol != item.symbol or entry.sha256_gz != item.sha256:
            raise HoldoutAccessViolation(
                f"the row {item.location} of the verified hash list {verified.source} does not"
                " match the fetch record (missing file, other symbol or other sha256_gz); the"
                " list is taken to be wrong and nothing is read (D03 §17.3 の 5)"
            )


def accept_mt5_in_memory(
    *,
    rules: Mt5Rules,
    datasource: Mt5DataSourceConfig,
    raw_dir: Path,
    symbol: Symbol,
    variant: TimeRuleVariant,
    created_at: UtcTime,
    boundaries: Mt5Boundaries,
    code_version: str,
    verified: VerifiedRawList | None,
    months: Iterable[tuple[int, int]] | None = None,
    docs_manifest_copy: Path | None = None,
    source_prefix: str | None = None,
) -> PendingSnapshot:
    """原データから暫定 snapshot をメモリの上に作る（書き出さない。D03 §15.3 の 1〜8）。

    `raw_dir` は `data/raw/oanda_mt5/<取得日>`（実際の置き場。`--raw-root` で別の場所を指せる）。
    `docs_manifest_copy` を渡すと、版管理の写しと原本の取得記録のハッシュが一致することを確かめる
    （D03 §15.3 の 1）。`months` を渡すと、その年月（API の時刻）の要求だけで受け入れる（範囲の外は
    snapshot の外）。

    品質確認の許可を要しない読み取りなので、月のファイルを行に分けて読む前に確認済みハッシュの
    一覧 `verified` と照合する（D03 §17.7。`None` なら何も読まずに止まる）: 一覧が取得記録と
    食い違わない（`require_list_matches_fetch_record`）、読むすべてのファイルの正規の置き場
    （列対応の宣言の `root`/<取得日>/<file>。`raw_dir` に依らない）が一覧に載りハッシュが一致する
    （`require_listed_before_reading`）。読むときにも同じバイト列で照らし直す。
    """
    manifest_path = raw_dir / datasource.fetch_manifest
    manifest_sha = file_sha256(manifest_path)
    if docs_manifest_copy is not None and file_sha256(docs_manifest_copy) != manifest_sha:
        raise MarketDataValueError(
            f"the fetch record {manifest_path} differs from its versioned copy"
            f" {docs_manifest_copy} (D03 §15.3 の 1)"
        )
    entries = read_fetch_manifest(manifest_path)
    wanted = None if months is None else set(months)
    own: list[Mt5FetchEntry] = [
        entry
        for entry in entries
        if entry.symbol == symbol and (wanted is None or _month_of(entry) in wanted)
    ]
    canonical = f"{datasource.root.as_posix()}/{raw_dir.name}"
    if verified is not None:
        if verified.fetch_date != raw_dir.name:
            raise HoldoutAccessViolation(
                f"the verified hash list {verified.source} is for the fetch date"
                f" {verified.fetch_date}, not {raw_dir.name}; nothing is read (D03 §17.7)"
            )
        require_list_matches_fetch_record(
            verified, entries, fetch_manifest_sha256=manifest_sha, location_prefix=canonical
        )
    require_listed_before_reading(raw_dir, own, location_prefix=canonical, verified=verified)
    files = read_symbol_files(
        raw_dir, own, datasource.columns, location_prefix=canonical, verified=verified
    )
    prefix = source_prefix if source_prefix is not None else canonical
    minute_def = rules.timeframe_defs[datasource.minute_timeframe]
    return build_mt5_pending(
        created_at=created_at,
        symbol=symbol,
        entries=own,
        files=files,
        source_paths={entry.rid: f"{prefix}/{entry.file}" for entry in own},
        fetch_manifest_sha256=manifest_sha,
        column_mapping=datasource.ref,
        table=rules.table,
        variant=variant,
        calendar=rules.calendar,
        minute_def=minute_def,
        timeframe_defs=rules.timeframe_defs,
        targets=rules.targets,
        boundaries=boundaries,
        basis_declaration=datasource.basis_declaration,
        code_version=code_version,
    )


def _month_of(entry: Mt5FetchEntry) -> tuple[int, int]:
    from datetime import UTC, datetime

    moment = datetime.fromtimestamp(entry.requested_from_api, UTC)
    return moment.year, moment.month


def load_datasource(repo_root: Path, path: Path = DEFAULT_MT5_DATASOURCE) -> Mt5DataSourceConfig:
    """列対応の宣言を読む。"""
    return load_mt5_datasource(repo_root / path)
