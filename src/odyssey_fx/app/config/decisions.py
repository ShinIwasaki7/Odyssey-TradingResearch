"""分類ファイルの読込（D03 §3.7・§4 の 9・§10 の `classify`、形式版 2）。

受入れの検査が出した分類対象の警告（存在すべき足の欠落 `MISSING_EXPECTED_BAR` と休場帯の
足 `UNEXPECTED_BAR`、D03 §3.9 v1.7）を人間が分類した結果を読む。読んだ結果は
`marketdata.domain.classification.ClassificationDecision` になる。

分類1件は「検査種別 × 区間 × 対象系列（明示集合、または snapshot 内の全系列 `all`）」で
まとめて宣言でき、その区間に**完全に含まれる**同種別の警告（対象系列のもの）をすべて分類
したものとみなす（D03 §4）。`all` は読込の時点で暫定 snapshot の具体的な系列の集合へ解決
する（確定時の検査4）。

**この設定は `configs/` に置かない**。分類は snapshot ごとの一度きりの判断であり、
繰り返し使う宣言ではないためで、利用者が任意の場所に置いたファイルを `--decisions` で
指す。検証はほかの設定ファイルと同じ条件（安全な読込、重複キー禁止、`schema_version`
必須、未宣言キー拒否）で行う（D01 §10.1）。

形式（D03 §10）:

```yaml
schema_version: 2
calendar: configs/calendars/fx_ny17_v2.yaml   # 分類でカレンダーを変えた場合だけ
decisions:
  - kind: MISSING_EXPECTED_BAR
    interval: {start: "2016-12-26T00:00:00Z", end: "2016-12-27T00:00:00Z"}
    series: all                                # または [USDJPY/1h/bid, USDJPY/15m/bid]
    outcome: CLOSURE
    note: クリスマス翌日の休場
  - kind: UNEXPECTED_BAR
    interval: {start: "2020-03-15T20:00:00Z", end: "2020-03-15T22:00:00Z"}
    series: [EURUSD/1h/bid]
    outcome: OUT_OF_SESSION_DATA
    note: 出所の切替に伴う早期配信
```

**形式版 1 は読まない**。版 1（系列1つ・区間・`kind: CLOSURE / DATA_GAP`）とは項目の意味が
異なり（版 1 の `kind` は分類結果、版 2 の `kind` は検査種別）、読み替えると取り違えが
起きうるので、未知の版と同じく拒否する（D01 §10.1）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import Field

from odyssey_fx.app.config.calendars import load_calendar
from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.classification import (
    CALENDAR_CHANGING_OUTCOMES,
    CLASSIFIABLE_KINDS,
    ClassificationDecision,
    ClassificationOutcome,
)
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "ClassificationDecisionFile",
    "load_classification_decisions",
    "parse_series_id",
    "resolve_series_id",
]

#: この実装が読む設定ファイルの形式版。未知の版は拒否する（D01 §10.1、D03 §10 v1.7）。
DECISIONS_SCHEMA_VERSION = 2

#: 系列の文字列表記の要素数（`USDJPY/1h/bid`）。
_SERIES_PARTS = 3

#: 時間足の `id` に許す字種（`common.TimeframeRef` と同じ）。
_TIMEFRAME_ID_PATTERN: Final = re.compile(r"^[a-z0-9_]+$")


class _IntervalModel(StrictModel):
    """区間（半開 `[start, end)`）。時刻はオフセット付きで書く。"""

    start: str
    end: str


class _DecisionModel(StrictModel):
    """分類1件（D03 §4 の「分類の形式」）。"""

    kind: str
    interval: _IntervalModel
    series: Literal["all"] | list[str]
    outcome: str
    note: str = ""


class _DecisionsModel(StrictModel):
    """分類ファイルの形（D03 §10）。"""

    schema_version: int
    decisions: list[_DecisionModel] = Field(default_factory=list)
    calendar: str | None = None


@dataclass(frozen=True, slots=True)
class ClassificationDecisionFile:
    """分類ファイルの内容（D03 §10 の `classify`）。

    `calendar` は、分類でカレンダーを変えた場合に指定する新しい版（`calendar_path` から
    読んだもの）。指定があれば、受入れのカレンダー照合以降（D03 §4 の 5〜7）をこの
    カレンダーで再実行する。
    """

    decisions: tuple[ClassificationDecision, ...]
    calendar: TradingCalendar | None = None
    calendar_path: Path | None = None


def parse_series_id(text: str, label: str) -> tuple[Symbol, str, PriceBasis]:
    """`USDJPY/1h/bid` 形式の系列表記を分解する（`SeriesId.__str__` の逆）。

    返すのは **`SeriesId` そのものではなく分解した要素**（銘柄、時間足の `id`、価格基準）
    である。系列の表記には時間足の**版が含まれない**（`SeriesId.__str__` は `id` だけを
    書く、D03 §3.1）ので、文字列だけからは版を決められない。版を 1 と決め打つと、版 2
    以降の時間足定義を使った snapshot で、分類の系列が報告・系列記録と食い違ったまま
    manifest に記録されてしまう。

    実際の `SeriesId` は `resolve_series_id` が snapshot の系列一覧から文字列一致で解決
    する。
    """
    if not isinstance(text, str):
        raise ConfigError(f"{label} は `<銘柄>/<時間足>/<価格基準>` の文字列で書くこと")
    parts = text.split("/")
    if len(parts) != _SERIES_PARTS:
        raise ConfigError(
            f"{label}: {text!r} は系列として読めない。"
            " `USDJPY/1h/bid` の形（銘柄／時間足／価格基準）で書くこと"
        )
    raw_symbol, raw_timeframe, raw_basis = parts
    try:
        symbol = Symbol(raw_symbol)
    except KernelValueError as exc:
        raise ConfigError(f"{label}: 銘柄 {raw_symbol!r} が読めない: {exc}") from exc
    if not _TIMEFRAME_ID_PATTERN.fullmatch(raw_timeframe):
        raise ConfigError(f"{label}: 時間足 {raw_timeframe!r} が読めない（小文字・数字・下線のみ）")
    try:
        basis = PriceBasis(raw_basis)
    except ValueError as exc:
        raise ConfigError(
            f"{label}: 価格基準 {raw_basis!r} は受けない"
            f"（{[member.value for member in PriceBasis]} のいずれか）"
        ) from exc
    return symbol, raw_timeframe, basis


def resolve_series_id(text: str, known: Sequence[SeriesId], label: str) -> SeriesId:
    """系列の表記を、snapshot が実際に持つ系列へ解決する（D03 §3.1・§4 の 9）。

    `known` は暫定 manifest に記録された系列（`SnapshotManifest.series`）。突き合わせは
    **系列の文字列表記**（`USDJPY/1h/bid`）で行う。検査の報告もこの表記を使うので、人間が
    報告を見て書いた分類はそのまま一致する。時間足の版は snapshot が使った定義の版になる。

    知らない系列は失敗させる。snapshot に無い系列の分類を受け入れると、実在しない系列の
    記録を含む snapshot ができてしまう。
    """
    # 表記として成立するかを先に確かめる（綴り誤りと「未知の系列」を区別するため）。
    parse_series_id(text, label)

    for series in known:
        if str(series) == text:
            return series
    raise ConfigError(
        f"{label}: 系列 {text!r} はこの snapshot にない。"
        f" ある系列は {sorted(str(series) for series in known)}"
    )


def _interval(model: _IntervalModel, label: str) -> Interval:
    """区間を読む。時刻はオフセット必須（見た目から規約を推測しない）。"""
    try:
        return Interval(
            start=UtcTime.parse(model.start),
            end=UtcTime.parse(model.end),
        )
    except KernelValueError as exc:
        raise ConfigError(f"{label}: 区間として成立しない: {exc}") from exc


def _kind(value: str, label: str) -> CheckKind:
    """分類対象の検査種別を読む（D03 §3.9 の明示集合）。"""
    allowed = sorted(kind.value for kind in CLASSIFIABLE_KINDS)
    try:
        kind = CheckKind(value)
    except ValueError as exc:
        raise ConfigError(
            f"{label}.kind が {value!r} だが、分類するのは {allowed} のいずれかである"
        ) from exc
    if kind not in CLASSIFIABLE_KINDS:
        raise ConfigError(
            f"{label}.kind が {value!r} だが、この種別は人間の分類を要しない"
            f"（分類するのは {allowed} だけ。D03 §3.9）"
        )
    return kind


def _outcome(value: str, label: str) -> ClassificationOutcome:
    """分類結果を読む。種別との組合せはドメインの型が拒否する。"""
    try:
        return ClassificationOutcome(value)
    except ValueError as exc:
        raise ConfigError(
            f"{label}.outcome が {value!r} だが、受けるのは"
            f" {[member.value for member in ClassificationOutcome]} のいずれかである"
        ) from exc


def _series(
    value: Literal["all"] | list[str], known: Sequence[SeriesId], label: str
) -> tuple[SeriesId, ...]:
    """対象系列を snapshot の具体的な系列の集合へ解決する（D03 §4 の確定時の検査4）。"""
    if value == "all":
        # 「全系列」は snapshot 内の具体的な `SeriesId` の集合として保存する。
        return tuple(sorted(known, key=str))
    if not value:
        raise ConfigError(f"{label}.series が空である。系列を1つ以上書くか `all` と書くこと")
    resolved = [
        resolve_series_id(text, known, f"{label}.series[{index}]")
        for index, text in enumerate(value)
    ]
    if len(set(resolved)) != len(resolved):
        raise ConfigError(f"{label}.series に同じ系列が2度書かれている: {value}")
    return tuple(resolved)


def _calendar_ref(calendar: TradingCalendar) -> str:
    """カレンダーの版参照（`<id>@v<version>`。run manifest のカレンダー参照と同じ形）。"""
    return f"{calendar.id}@v{calendar.version}"


def _decision(
    model: _DecisionModel,
    path: Path,
    index: int,
    known: Sequence[SeriesId],
    calendar: TradingCalendar | None,
) -> ClassificationDecision:
    """分類1件をドメインの型へ変換する。系列は snapshot の系列一覧から解決する。"""
    label = f"{path}: decisions[{index}]"
    kind = _kind(model.kind, label)
    outcome = _outcome(model.outcome, label)
    # 分類が参照するカレンダーの新版（D03 §3.7 の `calendar_ref`）。カレンダーへ宣言を
    # 追加して版を上げる分類結果（休場・営業例外）にだけ記録する。
    calendar_ref = (
        _calendar_ref(calendar)
        if calendar is not None and outcome in CALENDAR_CHANGING_OUTCOMES
        else None
    )
    try:
        return ClassificationDecision(
            kind=kind,
            interval=_interval(model.interval, f"{label}.interval"),
            series=_series(model.series, known, label),
            outcome=outcome,
            note=model.note,
            calendar_ref=calendar_ref,
        )
    except MarketDataValueError as exc:
        raise ConfigError(f"{label}: 分類として成立しない: {exc}") from exc


def load_classification_decisions(
    path: Path, known_series: Sequence[SeriesId]
) -> ClassificationDecisionFile:
    """分類ファイルを読む（D03 §4 の 9・§10 の `classify`、形式版 2）。

    `known_series` は暫定 snapshot が持つ系列の一覧。分類の系列表記と `all` はここから
    解決するので、時間足の版は snapshot が実際に使った定義の版になる。

    `calendar` が書かれていれば、そのカレンダーも読む（D03 §9 の形式）。

    未分類・競合・対応なしの検査は、報告と突き合わせる確定（`acceptance.finalize`）が
    行う。ここでは1件ずつの形（種別・結果の組合せ、区間、系列）だけを検査する。
    """
    payload = load_yaml_mapping(path)
    model = validate(_DecisionsModel, payload, path)
    require_schema_version(model.schema_version, DECISIONS_SCHEMA_VERSION, path)

    calendar_path = None if model.calendar is None else Path(model.calendar)
    calendar = None if calendar_path is None else load_calendar(calendar_path)
    decisions = tuple(
        _decision(entry, path, index, known_series, calendar)
        for index, entry in enumerate(model.decisions)
    )
    return ClassificationDecisionFile(
        decisions=decisions, calendar=calendar, calendar_path=calendar_path
    )
