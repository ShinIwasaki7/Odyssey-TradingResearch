"""欠落区間の分類の読込（D03 §3.7.1 の 2・§4 の 9・§10 の `classify`）。

受入れの検査が出した警告（存在すべき足の欠落など）を、人間が「休場」か「データ欠損」に
分類した結果を読む。読んだ結果は `marketdata.domain.snapshot.ClosureDecision` になり、
最終の識別子（`snapshot_id`）の計算対象に入る（D03 §3.7.1）。

**この設定は `configs/` に置かない**。分類は snapshot ごとの一度きりの判断であり、
繰り返し使う宣言ではないためで、利用者が任意の場所に置いたファイルを `--decisions` で
指す。検証はほかの設定ファイルと同じ条件（安全な読込、重複キー禁止、`schema_version`
必須、未宣言キー拒否）で行う（D01 §10.1）。

形式:

```yaml
schema_version: 1
# 分類でカレンダーを変えた場合だけ書く。新しい版のカレンダーを指す（D03 §4 の 9）。
calendar: configs/calendars/fx_ny17_v2.yaml
decisions:
  - series: USDJPY/1h/bid
    interval:
      start: "2024-12-25T00:00:00Z"
      end: "2024-12-25T01:00:00Z"
    kind: CLOSURE
    note: クリスマス休場
```
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import Field

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from odyssey_fx.app.config.models import StrictModel, require_schema_version, validate
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import ClosureDecision, ClosureDecisionKind

__all__ = [
    "ClosureDecisionFile",
    "load_closure_decisions",
    "parse_series_id",
    "resolve_series_id",
]

#: この実装が読む設定ファイルの形式版。未知の版は拒否する（D01 §10.1）。
DECISIONS_SCHEMA_VERSION = 1

#: 系列の文字列表記の要素数（`USDJPY/1h/bid`）。
_SERIES_PARTS = 3

#: 時間足の `id` に許す字種（`common.TimeframeRef` と同じ）。
_TIMEFRAME_ID_PATTERN: Final = re.compile(r"^[a-z0-9_]+$")


class _IntervalModel(StrictModel):
    """区間（半開 `[start, end)`）。時刻はオフセット付きで書く。"""

    start: str
    end: str


class _DecisionModel(StrictModel):
    """欠落区間1件の分類（D03 §4 の 9）。"""

    series: str
    interval: _IntervalModel
    kind: str
    note: str = ""


class _DecisionsModel(StrictModel):
    """分類ファイルの形（D03 §10 の `classify`）。"""

    schema_version: int
    decisions: list[_DecisionModel] = Field(default_factory=list)
    calendar: str | None = None


@dataclass(frozen=True, slots=True)
class ClosureDecisionFile:
    """分類ファイルの内容（D03 §10 の `classify`）。

    `calendar` は、分類でカレンダーを変えた場合に指定する新しい版のファイル。指定が
    あれば、受入れのカレンダー照合以降（D03 §4 の 5〜7）を新しいカレンダーで再実行する。
    """

    decisions: tuple[ClosureDecision, ...]
    calendar_path: Path | None = None


def parse_series_id(text: str, label: str) -> tuple[Symbol, str, PriceBasis]:
    """`USDJPY/1h/bid` 形式の系列表記を分解する（`SeriesId.__str__` の逆）。

    返すのは **`SeriesId` そのものではなく分解した要素**（銘柄、時間足の `id`、価格基準）
    である。系列の表記には時間足の**版が含まれない**（`SeriesId.__str__` は `id` だけを
    書く、D03 §3.1）ので、文字列だけからは版を決められない。版を 1 と決め打つと、版 2
    以降の時間足定義を使った snapshot で、分類の系列が報告・系列記録と食い違ったまま
    manifest に記録され、識別子の計算に入ってしまう。

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
    報告を見て書いた分類はそのまま一致する。

    こうすることで、時間足の版は**snapshot が使った定義の版**になる。分類ファイルに版を
    書かせる必要がなく、かつ版が食い違ったまま記録されることもない。

    知らない系列は失敗させる。分類は識別子の計算対象なので、snapshot に無い系列の分類を
    受け入れると、実在しない系列の記録を含む snapshot ができてしまう。
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


def _decision(
    model: _DecisionModel, path: Path, index: int, known: Sequence[SeriesId]
) -> ClosureDecision:
    """分類1件をドメインの型へ変換する。系列は snapshot の系列一覧から解決する。"""
    label = f"{path}: decisions[{index}]"
    try:
        kind = ClosureDecisionKind(model.kind)
    except ValueError as exc:
        raise ConfigError(
            f"{label}.kind が {model.kind!r} だが、受けるのは"
            f" {[member.value for member in ClosureDecisionKind]} のいずれかである"
            "（休場か、データ欠損か）"
        ) from exc
    try:
        return ClosureDecision(
            series_id=resolve_series_id(model.series, known, f"{label}.series"),
            interval=_interval(model.interval, f"{label}.interval"),
            kind=kind,
            note=model.note,
        )
    except MarketDataValueError as exc:
        raise ConfigError(f"{label}: 分類として成立しない: {exc}") from exc


def load_closure_decisions(path: Path, known_series: Sequence[SeriesId]) -> ClosureDecisionFile:
    """欠落区間の分類を読む（D03 §4 の 9・§10 の `classify`）。

    `known_series` は暫定 snapshot が持つ系列の一覧。分類の系列表記はここから解決するので、
    時間足の版は snapshot が実際に使った定義の版になる（版を 1 と決め打たない）。

    同じ系列・同じ区間の分類が2度現れる設定は拒否する。分類は最終の識別子の計算対象
    なので、どちらを採るかが宣言から決まらない状態を残せない（D03 §3.7.1）。
    """
    payload = load_yaml_mapping(path)
    model = validate(_DecisionsModel, payload, path)
    require_schema_version(model.schema_version, DECISIONS_SCHEMA_VERSION, path)

    decisions: list[ClosureDecision] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(model.decisions):
        decision = _decision(entry, path, index, known_series)
        # 突き合わせの鍵は**区間全体**にする。開始時刻だけで見ると、終端の違う分類
        # （別の足を指す分類）が重複と誤判定される（`snapshot_access` の突き合わせと同じ）。
        key = (str(decision.series_id), str(decision.interval))
        if key in seen:
            raise ConfigError(
                f"{path}: 系列 {decision.series_id} の区間 {decision.interval} に対する分類が"
                " 2度現れる。どちらを採るかが宣言から決まらないため拒否する"
            )
        seen.add(key)
        decisions.append(decision)

    calendar_path = None if model.calendar is None else Path(model.calendar)
    return ClosureDecisionFile(decisions=tuple(decisions), calendar_path=calendar_path)
