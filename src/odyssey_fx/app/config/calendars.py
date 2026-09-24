"""取引カレンダーと時間足定義の読込（D03 §3.2・§3.4・§9、D01 §10.1）。

`configs/calendars/fx_ny17_v1.yaml` と `configs/calendars/timeframes_v1.yaml` を読み、
`marketdata.domain` の frozen dataclass（`TradingCalendar` / `TimeframeDefinition`）へ
変換する。

Pydantic モデルは**この層の外へ出さない**（D01 §10.1）。モデルは「設定ファイルの形」を
表すだけで、意味を持つのは変換後の domain 型である。未宣言キーは `extra="forbid"` で
拒否し、型不一致はモデルの型注釈が拒否する。

現地時刻（`17:00:00`）は文字列として受け、`zoneinfo` の規則で UTC へ変換するのは domain
の責務である（固定 UTC 時刻を設定ファイルに書かない、D03 §3.2）。
"""

from __future__ import annotations

from datetime import date, time, timedelta
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.app.config.scalars import parse_duration, parse_local_date, parse_local_time
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.calendar import (
    ClosureRule,
    OpeningRule,
    TradingCalendar,
    WeeklyMoment,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.timeframe_def import (
    FixedUtcAlignment,
    SessionAlignment,
    TimeframeDefinition,
)

__all__ = ["load_calendar", "load_timeframes"]

#: この実装が読む設定ファイルの形式版。未知の版は拒否する（D01 §10.1）。
CALENDAR_SCHEMA_VERSION = 1
TIMEFRAMES_SCHEMA_VERSION = 1


class _WeeklyMomentModel(StrictModel):
    """週の開閉の時点（曜日と現地時刻）。"""

    weekday: Annotated[int, Field(ge=0, le=6)]
    at: str


class _ClosureModel(StrictModel):
    """宣言した休場・短縮1件（D03 §3.4・§3.4.1）。

    終日休場は `covers_whole_day: true`、取引日単位の休場（前日の取引日の境界〜当日の
    取引日の境界。v1.9）は `covers_trading_day: true`、短縮は `start` / `end` で書く。
    2つ以上の形を書く、どれも書かない、といった組合せは domain の `ClosureRule` が拒否する。
    `covers_trading_day` は省略可能なキーで、省略すれば従来の意味のまま読める（形式の版は
    上げない。D03 §9）。
    """

    local_date: str
    covers_whole_day: bool = False
    covers_trading_day: bool = False
    start: str | None = None
    end: str | None = None
    note: str = ""


class _OpeningModel(StrictModel):
    """宣言した営業例外1件（D03 §3.4 v1.7）。

    週の休場時間帯のうち市場が開いていた現地日付と区間。通常の週の開場区間に接するか
    重なること、休場と重ならないことは domain の `TradingCalendar` が検証する。
    """

    local_date: str
    start: str
    end: str
    note: str = ""


class _CalendarModel(StrictModel):
    """`configs/calendars/*.yaml` の形（D03 §9）。"""

    schema_version: int
    id: str
    version: Annotated[int, Field(ge=1)]
    tz: str
    weekly_open: _WeeklyMomentModel
    weekly_close: _WeeklyMomentModel
    closures: list[_ClosureModel] = Field(default_factory=list)
    openings: list[_OpeningModel] = Field(default_factory=list)


class _FixedUtcAlignmentModel(StrictModel):
    """UTC のエポックから名目長の整数倍で区切る整列（D03 §3.2）。"""

    kind: Literal["fixed_utc"]


class _SessionAlignmentModel(StrictModel):
    """現地時刻の起点から区切る整列（D03 §3.2）。

    固定 UTC 時刻は書かない。現地の起点時刻と時間帯だけを宣言し、夏時間を含む変換は
    domain が `zoneinfo` の規則で行う。
    """

    kind: Literal["session"]
    tz: str
    anchors_local: Annotated[list[str], Field(min_length=1)]


class _TimeframeModel(StrictModel):
    """時間足定義1件（D03 §3.2）。"""

    id: str
    version: Annotated[int, Field(ge=1)]
    nominal_length: str
    alignment: _FixedUtcAlignmentModel | _SessionAlignmentModel = Field(discriminator="kind")


class _TimeframesModel(StrictModel):
    """`configs/calendars/timeframes_v1.yaml` の形（D03 §9）。"""

    schema_version: int
    timeframes: Annotated[list[_TimeframeModel], Field(min_length=1)]


def _zone(name: str, path: Path, label: str) -> ZoneInfo:
    """時間帯の名前から `ZoneInfo` を作る。未知の名前は拒否する。"""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"{path}: {label} の時間帯 {name!r} は解決できない: {exc}") from exc


def _closure_rule(model: _ClosureModel, path: Path, index: int) -> ClosureRule:
    """宣言した休場をドメインの型へ変換する。"""
    label = f"closures[{index}]"
    local_day: date = parse_local_date(model.local_date, f"{path}: {label}.local_date")
    start: time | None = (
        None if model.start is None else parse_local_time(model.start, f"{path}: {label}.start")
    )
    end: time | None = (
        None if model.end is None else parse_local_time(model.end, f"{path}: {label}.end")
    )
    try:
        return ClosureRule(
            local_date=local_day,
            covers_whole_day=model.covers_whole_day,
            covers_trading_day=model.covers_trading_day,
            start=start,
            end=end,
            note=model.note,
        )
    except MarketDataValueError as exc:
        raise ConfigError(f"{path}: {label} は休場の宣言として成立しない: {exc}") from exc


def _opening_rule(model: _OpeningModel, path: Path, index: int) -> OpeningRule:
    """宣言した営業例外をドメインの型へ変換する（D03 §3.4 v1.7）。"""
    label = f"openings[{index}]"
    try:
        return OpeningRule(
            local_date=parse_local_date(model.local_date, f"{path}: {label}.local_date"),
            start=parse_local_time(model.start, f"{path}: {label}.start"),
            end=parse_local_time(model.end, f"{path}: {label}.end"),
            note=model.note,
        )
    except MarketDataValueError as exc:
        raise ConfigError(f"{path}: {label} は営業例外の宣言として成立しない: {exc}") from exc


def load_calendar(path: Path) -> TradingCalendar:
    """取引カレンダーを読む（D03 §3.4・§9）。

    週の開閉と宣言した休場・営業例外（`openings`、v1.7）を持つ `TradingCalendar` を返す。
    土日の扱いも週の開閉から導かれるのであって、UTC の曜日判定では決めない（D03 §3.4）。

    `openings` は省略できる（既存のカレンダーファイルはそのまま読める）。離れた営業例外と
    休場に重なる営業例外は domain の構築時検証が拒否し、ここで設定の誤りとして報告する。
    """
    payload = load_yaml_mapping(path)
    model = validate(_CalendarModel, payload, path)
    require_schema_version(model.schema_version, CALENDAR_SCHEMA_VERSION, path)

    tz = _zone(model.tz, path, "カレンダー")
    try:
        return TradingCalendar(
            id=model.id,
            version=model.version,
            tz=tz,
            weekly_open=WeeklyMoment(
                weekday=model.weekly_open.weekday,
                at=parse_local_time(model.weekly_open.at, f"{path}: weekly_open.at"),
            ),
            weekly_close=WeeklyMoment(
                weekday=model.weekly_close.weekday,
                at=parse_local_time(model.weekly_close.at, f"{path}: weekly_close.at"),
            ),
            closures=tuple(
                _closure_rule(closure, path, index) for index, closure in enumerate(model.closures)
            ),
            openings=tuple(
                _opening_rule(opening, path, index) for index, opening in enumerate(model.openings)
            ),
        )
    except MarketDataValueError as exc:
        raise ConfigError(f"{path}: カレンダーとして成立しない: {exc}") from exc


def _timeframe_definition(model: _TimeframeModel, path: Path) -> TimeframeDefinition:
    """時間足定義1件をドメインの型へ変換する（D03 §3.2）。"""
    label = f"timeframes[{model.id}]"
    nominal_length: timedelta = parse_duration(
        model.nominal_length, f"{path}: {label}.nominal_length"
    )

    alignment: FixedUtcAlignment | SessionAlignment
    if isinstance(model.alignment, _FixedUtcAlignmentModel):
        # 固定 UTC 整列では足の長さは常に名目長に等しい（D03 §3.2）。刻みを別に書かせると
        # 名目長と食い違いうるので、名目長そのものを刻みにする。
        alignment = FixedUtcAlignment(step=nominal_length)
    else:
        alignment = SessionAlignment(
            tz=_zone(model.alignment.tz, path, label),
            anchors_local=tuple(
                parse_local_time(anchor, f"{path}: {label}.anchors_local[{index}]")
                for index, anchor in enumerate(model.alignment.anchors_local)
            ),
        )

    try:
        return TimeframeDefinition(
            ref=TimeframeRef(id=model.id, version=model.version),
            nominal_length=nominal_length,
            alignment=alignment,
        )
    except (KernelValueError, MarketDataValueError) as exc:
        raise ConfigError(f"{path}: {label} は時間足定義として成立しない: {exc}") from exc


def load_timeframes(path: Path) -> dict[str, TimeframeDefinition]:
    """時間足定義をすべて読み、`id` から定義への対応を返す（D03 §3.2・§9）。

    受入れ（`build_pending_snapshot`）は `id` から定義を引くので、その形で返す。同じ `id`
    が2度現れる設定は拒否する（どちらを採るかが宣言から読み取れないため）。
    """
    payload = load_yaml_mapping(path)
    model = validate(_TimeframesModel, payload, path)
    require_schema_version(model.schema_version, TIMEFRAMES_SCHEMA_VERSION, path)

    definitions: dict[str, TimeframeDefinition] = {}
    for entry in model.timeframes:
        if entry.id in definitions:
            raise ConfigError(
                f"{path}: 時間足 {entry.id!r} が2度宣言されている。"
                " どちらを採るかが宣言から読み取れないため拒否する"
            )
        definitions[entry.id] = _timeframe_definition(entry, path)
    return definitions
