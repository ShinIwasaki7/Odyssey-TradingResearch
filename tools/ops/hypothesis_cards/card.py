"""仮説カードの書式と識別子（D25 §3.1・§3.2、仮置き 4・6・7。決定 171）。

カードは YAML の 1 ファイルが 1 枚で、人間の口も AI の口も同じ書式である。戦略は戦略の宣言の
本体（D04 の形のうち 5 項目）をそのまま書く（決定 171）。

本モジュールは第3.5節の検査 1（書式。`CARD_FORMAT`）を当てる:

- 項目・型・語彙（第3.1節の表）、識別子の形と `<口の 1 文字>-<created_at の日付>-<title>` の一致、
  ファイル名との一致（置き場のパスを渡したとき）。
- 自由文（`prediction`・`inspiration`）に数字（Unicode の数字の分類 N。半角・全角を含む）が無い
  （仮置き 6）。`rule` の中の数は、戦略のパラメータか格子の値に現れる数だけ（仮置き 6）。
- 格子の軸のパラメータを戦略に書いたなら、その値は格子の先頭の値（第3.1節の `strategy` の行）。
- AI の口は `read_scope` を必ず書き、読む束の一覧が渡されたらその中のパスだけ（第5.2節）。

カードの YAML は設定ファイルと同じ安全な読込（重複キー・anchor・alias・merge key を拒む。
`app.config.loader`）で読む。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping

__all__ = [
    "CARD_ID_PATTERN",
    "CARD_SCHEMA_VERSION",
    "MOUTHS",
    "STRATEGY_KEYS",
    "SYMBOLS",
    "Axis",
    "Card",
    "CardFormatError",
    "experiment_id_of",
    "has_digit",
    "parse_card",
    "rule_numbers_outside",
    "strategy_values",
]

CARD_SCHEMA_VERSION: Final = 1
#: 口の値と識別子の 1 文字（D23 §5.1、仮置き 4）。
MOUTHS: Final[Mapping[str, str]] = {"human": "h", "ai": "a"}
#: カードの識別子（仮置き 4）: `<口の 1 文字>-<YYYYMMDD>-<短い名前>`。
CARD_ID_PATTERN: Final = re.compile(r"^(?P<mouth>[ha])-(?P<date>\d{8})-(?P<title>[a-z0-9-]{3,24})$")
_TITLE_PATTERN: Final = re.compile(r"^[a-z0-9-]{3,24}$")
_UTC_TIME: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_NUMBER: Final = re.compile(r"\d+(?:\.\d+)?")
#: 承認済みの研究用 snapshot がある 8 通貨（D23 の仮置き 15）。
SYMBOLS: Final = frozenset(
    {"AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "USDCHF", "USDJPY"}
)
#: カードの `strategy` に書く D04 の宣言の本体（第3.1節）。`schema_version`・`strategy_id`・
#: `version` は生成の道具が入れる。
STRATEGY_KEYS: Final = (
    "entry_policy",
    "opportunity_validity",
    "components",
    "roles",
    "opportunity_concurrency",
)
_REQUIRED: Final = (
    "schema_version",
    "id",
    "mouth",
    "created_at",
    "title",
    "symbol",
    "rule",
    "strategy",
    "grid",
    "prediction",
    "inspiration",
    "evidence_estimate",
)
_OPTIONAL: Final = ("favored_regimes", "read_scope")
_FREQUENCY_BANDS: Final = ("LOW", "MEDIUM", "HIGH")
_REACHES_FLOOR: Final = ("LIKELY", "UNSURE", "UNLIKELY")


class CardFormatError(ValueError):
    """カードが書式に合わない（`CARD_FORMAT`）。`problems` に食い違いを並べる。"""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True, slots=True)
class Axis:
    """格子の軸 1 本（実験設定の `search_plan.axes` の要素と同じ形）。"""

    instance: str
    parameter: str
    values: tuple[bool | int | float | str, ...]

    def as_mapping(self) -> dict[str, object]:
        return {"instance": self.instance, "parameter": self.parameter, "values": list(self.values)}


@dataclass(frozen=True, slots=True)
class Card:
    """読んだカード（`raw` はファイルのバイト列。凍結の SHA-256 の対象。D25 §3.4）。"""

    raw: bytes
    card_id: str
    mouth: str
    created_at: str
    title: str
    symbol: str
    rule: str
    strategy: Mapping[str, Any]
    axes: tuple[Axis, ...]
    prediction: str
    inspiration: str
    frequency_band: str
    reaches_floor: str
    favored_regimes: tuple[str, ...] | None
    read_scope: tuple[str, ...] | None

    @property
    def experiment_id(self) -> str:
        """実験と戦略の識別子（第3.2節）。"""
        return experiment_id_of(self.card_id, self.symbol)


def experiment_id_of(card_id: str, symbol: str) -> str:
    """`c_<口の 1 文字><YYYYMMDD>_<title の - を _ に>_<通貨の小文字>`（第3.2節、仮置き 4）。"""
    matched = CARD_ID_PATTERN.match(card_id)
    if matched is None:
        raise ValueError(f"カードの識別子の形でない: {card_id!r}")
    title = matched["title"].replace("-", "_")
    return f"c_{matched['mouth']}{matched['date']}_{title}_{symbol.lower()}"


def has_digit(text: str) -> bool:
    """数字（Unicode の数字の分類 N。半角・全角を含む）を含むか（仮置き 6）。"""
    return any(unicodedata.category(character).startswith("N") for character in text)


def _numbers(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER.findall(unicodedata.normalize("NFKC", text)):
        try:
            found.add(Decimal(token).normalize())
        except InvalidOperation:  # pragma: no cover - 正規表現が数だけを拾う
            continue
    return found


def strategy_values(value: object) -> set[Decimal]:
    """宣言・格子の値に現れる数（数の値と、文字列の値の中の数）。"""
    if isinstance(value, bool):
        return set()
    if isinstance(value, int | float):
        return _numbers(str(value))
    if isinstance(value, str):
        return _numbers(value)
    if isinstance(value, Mapping):
        return {number for item in value.values() for number in strategy_values(item)}
    if isinstance(value, list | tuple):
        return {number for item in value for number in strategy_values(item)}
    return set()


def rule_numbers_outside(rule: str, strategy: object, grid: object) -> set[Decimal]:
    """`rule` の数のうち、戦略のパラメータにも格子の値にも無いもの（仮置き 6）。"""
    return _numbers(rule) - (strategy_values(strategy) | strategy_values(grid))


def _text(data: Mapping[str, Any], key: str, problems: list[str]) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        problems.append(f"`{key}`: 空でない文字列でない")
        return ""
    return value


def _one_of(
    data: Mapping[str, Any], key: str, vocabulary: Iterable[str], problems: list[str]
) -> str:
    value = data.get(key)
    allowed = tuple(vocabulary)
    if value not in allowed:
        problems.append(f"`{key}`: 語彙（{' / '.join(allowed)}）の外")
        return ""
    assert isinstance(value, str)  # noqa: S101 - 語彙は文字列だけ
    return value


def _axes(data: Mapping[str, Any], problems: list[str]) -> tuple[Axis, ...]:
    grid = data.get("grid")
    if not isinstance(grid, dict) or set(grid) != {"axes"}:
        problems.append("`grid`: `axes` だけを持つ mapping でない")
        return ()
    raw_axes = grid["axes"]
    if not isinstance(raw_axes, list) or not raw_axes:
        problems.append("`grid.axes`: 1 本以上の並びでない")
        return ()
    axes: list[Axis] = []
    for index, item in enumerate(raw_axes):
        where = f"`grid.axes[{index}]`"
        if not isinstance(item, dict) or set(item) != {"instance", "parameter", "values"}:
            problems.append(f"{where}: `instance`・`parameter`・`values` の mapping でない")
            continue
        instance, parameter, values = item["instance"], item["parameter"], item["values"]
        if not isinstance(instance, str) or not instance or not isinstance(parameter, str):
            problems.append(f"{where}: `instance`・`parameter` が文字列でない")
            continue
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, bool | int | float | str) for v in values)
        ):
            problems.append(f"{where}.values: 値（真偽・数・文字列）の 1 つ以上の並びでない")
            continue
        axes.append(Axis(instance, parameter, tuple(values)))
    keys = [(axis.instance, axis.parameter) for axis in axes]
    if len(set(keys)) != len(keys):
        problems.append("`grid.axes`: 同じ軸が 2 度ある")
    return tuple(axes)


def _strategy(data: Mapping[str, Any], problems: list[str]) -> Mapping[str, Any]:
    strategy = data.get("strategy")
    if not isinstance(strategy, dict):
        problems.append("`strategy`: mapping でない")
        return {}
    missing = [key for key in STRATEGY_KEYS if key not in strategy]
    extra = [key for key in strategy if key not in STRATEGY_KEYS]
    if missing:
        problems.append(f"`strategy`: 必須の項目が無い（{', '.join(missing)}）")
    if extra:
        problems.append(
            f"`strategy`: 書式に無い項目（{', '.join(map(str, extra))}。"
            "`schema_version`・`strategy_id`・`version` は生成の道具が入れる）"
        )
    components = strategy.get("components")
    if not isinstance(components, list) or not components:
        problems.append("`strategy.components`: 1 つ以上の並びでない")
    elif not all(isinstance(item, dict) for item in components):
        problems.append("`strategy.components`: 要素が mapping でない")
    for key in ("entry_policy", "opportunity_validity", "roles", "opportunity_concurrency"):
        if key in strategy and not isinstance(strategy[key], dict):
            problems.append(f"`strategy.{key}`: mapping でない")
    return strategy


def _axis_first_values(
    strategy: Mapping[str, Any], axes: Sequence[Axis], problems: list[str]
) -> None:
    """軸のパラメータを戦略に書いたなら、その値は格子の先頭の値（第3.1節。前例のとおり）。"""
    components = strategy.get("components")
    if not isinstance(components, list):
        return
    by_id = {
        item.get("instance_id"): item
        for item in components
        if isinstance(item, dict) and isinstance(item.get("instance_id"), str)
    }
    for axis in axes:
        component = by_id.get(axis.instance)
        if component is None:
            problems.append(
                f"格子の軸 `{axis.instance}.{axis.parameter}`: その使用箇所が戦略に無い"
            )
            continue
        parameters = component.get("parameters")
        written = parameters.get(axis.parameter) if isinstance(parameters, dict) else None
        if isinstance(written, dict) and "value" in written:
            value = written["value"]
            first = axis.values[0]
            if type(value) is not type(first) or value != first:
                problems.append(
                    f"格子の軸 `{axis.instance}.{axis.parameter}`: 戦略に書いた値が格子の先頭の値と"
                    "違う（第3.1節）"
                )


def _optional_names(
    data: Mapping[str, Any], key: str, problems: list[str]
) -> tuple[str, ...] | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        problems.append(f"`{key}`: 空でない文字列の並びでない")
        return None
    if len(set(value)) != len(value):
        problems.append(f"`{key}`: 同じものが 2 度ある")
    return tuple(value)


def _read_scope(
    data: Mapping[str, Any],
    mouth: str,
    allowed: Collection[str] | None,
    problems: list[str],
) -> tuple[str, ...] | None:
    scope = _optional_names(data, "read_scope", problems)
    if mouth == "ai" and "read_scope" not in data:
        problems.append("`read_scope`: AI の口のカードに無い（第3.1節）")
    if scope is not None:
        if not scope:
            problems.append("`read_scope`: 空の並び")
        for path in scope:
            if path.startswith("/") or "\\" in path or ".." in path.split("/"):
                problems.append(f"`read_scope`: 相対パスでない（{path!r}）")
            elif allowed is not None and path not in allowed:
                problems.append(f"`read_scope`: 読む束の一覧に無いパス（{path!r}。第5.2節）")
    return scope


def _created_at(data: Mapping[str, Any], problems: list[str]) -> str:
    value = data.get("created_at")
    if not isinstance(value, str) or _UTC_TIME.match(value) is None:
        problems.append("`created_at`: UTC の時刻（YYYY-MM-DDTHH:MM:SSZ の文字列）でない")
        return ""
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        problems.append("`created_at`: 時刻として成り立たない")
        return ""
    return value


def parse_card(
    raw: bytes,
    *,
    path: str | None = None,
    allowed_read_paths: Collection[str] | None = None,
) -> Card:
    """カードのバイト列を読み、第3.5節の検査 1（書式）を当てる。

    `path` を渡すと、ファイル名（拡張子 `.yaml` を除く）がカードの識別子と同じことも確かめる。
    `allowed_read_paths` を渡すと、`read_scope` がその中のパスだけであることを確かめる（発想役の
    読む束の一覧。第5.2節）。合わなければ `CardFormatError`。
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CardFormatError(["UTF-8 でない"]) from None
    try:
        data = load_yaml_mapping(Path(path or "card.yaml"), text=text)
    except ConfigError as exc:
        located = re.search(r"line \d+", str(exc))
        where = f"（{located[0]}）" if located else ""
        raise CardFormatError([f"YAML として読めない{where}"]) from None
    problems: list[str] = []
    missing = [key for key in _REQUIRED if key not in data]
    extra = [key for key in data if key not in _REQUIRED and key not in _OPTIONAL]
    if missing:
        problems.append(f"必須の項目が無い（{', '.join(missing)}）")
    if extra:
        problems.append(f"書式に無い項目（{', '.join(map(str, extra))}）")
    version = data.get("schema_version")
    if isinstance(version, bool) or version != CARD_SCHEMA_VERSION:
        problems.append(f"`schema_version`: {CARD_SCHEMA_VERSION} でない")
    card_id = data.get("id")
    if not isinstance(card_id, str) or CARD_ID_PATTERN.match(card_id) is None:
        problems.append("`id`: `<口の 1 文字>-<YYYYMMDD>-<短い名前>` の形でない（仮置き 4）")
        card_id = ""
    mouth = _one_of(data, "mouth", MOUTHS, problems)
    created_at = _created_at(data, problems)
    title = data.get("title")
    if not isinstance(title, str) or _TITLE_PATTERN.match(title) is None:
        problems.append("`title`: 英小文字・数字・`-` の 3〜24 字でない")
        title = ""
    if card_id and mouth and created_at and title:
        expected = f"{MOUTHS[mouth]}-{created_at[:10].replace('-', '')}-{title}"
        if card_id != expected:
            problems.append("`id`: `<口>-<created_at の日付>-<title>` と違う（第3.2節）")
    if path is not None and card_id and Path(path).name != f"{card_id}.yaml":
        problems.append("ファイル名がカードの識別子と違う")
    symbol = _one_of(data, "symbol", sorted(SYMBOLS), problems)
    rule = _text(data, "rule", problems)
    strategy = _strategy(data, problems)
    axes = _axes(data, problems)
    prediction = _text(data, "prediction", problems)
    inspiration = _text(data, "inspiration", problems)
    for key, value in (("prediction", prediction), ("inspiration", inspiration)):
        if has_digit(value):
            problems.append(f"自由文 `{key}` に数字がある（仮置き 6）")
    if rule and rule_numbers_outside(rule, data.get("strategy"), data.get("grid")):
        problems.append("`rule` の数に、戦略のパラメータにも格子の値にも無いものがある（仮置き 6）")
    estimate = data.get("evidence_estimate")
    band = floor = ""
    if not isinstance(estimate, dict) or set(estimate) != {"frequency_band", "reaches_floor"}:
        problems.append("`evidence_estimate`: `frequency_band`・`reaches_floor` の mapping でない")
    else:
        band = _one_of(estimate, "frequency_band", _FREQUENCY_BANDS, problems)
        floor = _one_of(estimate, "reaches_floor", _REACHES_FLOOR, problems)
    regimes = _optional_names(data, "favored_regimes", problems)
    scope = _read_scope(data, mouth, allowed_read_paths, problems)
    if strategy and axes:
        _axis_first_values(strategy, axes, problems)
    if problems:
        raise CardFormatError(problems)
    return Card(
        raw=raw,
        card_id=card_id,
        mouth=mouth,
        created_at=created_at,
        title=title,
        symbol=symbol,
        rule=rule,
        strategy=strategy,
        axes=axes,
        prediction=prediction,
        inspiration=inspiration,
        frequency_band=band,
        reaches_floor=floor,
        favored_regimes=regimes,
        read_scope=scope,
    )
