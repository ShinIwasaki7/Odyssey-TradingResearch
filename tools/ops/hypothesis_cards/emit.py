"""生成物の YAML を決まったバイト列で書く（D25 §3.4 の作り直しの一致）。

作り直しはバイト単位で比べる（決定 172）ので、書き方をこのモジュールに固定する。PyYAML の
書き出しは版で字面が変わりうるため使わず、標準ライブラリだけで書く。

- mapping はブロックの形（`キー: 値`）。キーは英字・数字・`_` だけなら引用符なし、それ以外は
  二重引用符。空の mapping は `{}`。
- 並びは、要素がすべてスカラーなら流れの形（`[a, b]`）、mapping を含めばブロックの形（`- `）。
  空の並びは `[]`。
- 文字列は JSON と同じ二重引用符の形（YAML の二重引用符のスカラーとして読める。`ensure_ascii`
  を使わず日本語はそのまま）。整数はそのまま、浮動小数は小数点を必ず含む形（PyYAML の安全な
  読込が浮動小数として読む形。指数だけの `1e-05` は文字列として読まれるので `1.0e-05` にする）、
  真偽は `true`・`false`、`None` は `null`。
- 書いた後に、設定ファイルと同じ読込（`app.config.loader`）で読み直し、元の値と一致することを
  確かめる（一致しなければ道具の失敗）。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from tools.ops.hypothesis_cards.repo import ToolFailure

__all__ = ["dump_document", "scalar_text"]

_PLAIN_KEY: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INDENT: Final = "  "


def _key(key: object) -> str:
    if not isinstance(key, str):
        raise ToolFailure(f"YAML のキーは文字列だけを書く（{key!r}）")
    return key if _PLAIN_KEY.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def _float_text(value: float) -> str:
    if math.isnan(value) or math.isinf(value):
        raise ToolFailure(f"有限でない浮動小数は書かない（{value!r}）")
    text = repr(value)
    if "e" in text or "E" in text:
        mantissa, exponent = text.lower().split("e")
        if "." not in mantissa:
            mantissa += ".0"
        sign = "-" if exponent.startswith("-") else "+"
        return f"{mantissa}e{sign}{exponent.lstrip('+-')}"
    return text if "." in text else text + ".0"


def scalar_text(value: object) -> str:
    """スカラー 1 つの字面。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _float_text(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise ToolFailure(f"YAML に書けない値の型（{type(value).__name__}）")


def _is_scalar(value: object) -> bool:
    return value is None or isinstance(value, bool | int | float | str)


def _value_lines(value: object, depth: int) -> tuple[str, list[str]]:
    """値を `(同じ行に続ける字面, 次の行からの行)` にする。"""
    if _is_scalar(value):
        return " " + scalar_text(value), []
    if isinstance(value, Mapping):
        if not value:
            return " {}", []
        return "", _mapping_lines(value, depth + 1)
    if isinstance(value, Sequence) and not isinstance(value, str):
        if not value:
            return " []", []
        if all(_is_scalar(item) for item in value):
            return " [" + ", ".join(scalar_text(item) for item in value) + "]", []
        return "", _sequence_lines(value, depth + 1)
    raise ToolFailure(f"YAML に書けない値の型（{type(value).__name__}）")


def _mapping_lines(value: Mapping[Any, object], depth: int) -> list[str]:
    lines: list[str] = []
    for key, item in value.items():
        inline, rest = _value_lines(item, depth)
        lines.append(f"{_INDENT * depth}{_key(key)}:{inline}")
        lines.extend(rest)
    return lines


def _sequence_lines(value: Sequence[object], depth: int) -> list[str]:
    lines: list[str] = []
    prefix = _INDENT * depth + "- "
    for item in value:
        if isinstance(item, Mapping) and item:
            inner = _mapping_lines(item, depth + 1)
            # 先頭の行の字下げを `- ` に置き換える（YAML のブロックの並びの mapping）。
            lines.append(prefix + inner[0][len(_INDENT * (depth + 1)) :])
            lines.extend(inner[1:])
        elif _is_scalar(item) or isinstance(item, Mapping):
            lines.append(prefix + ("{}" if isinstance(item, Mapping) else scalar_text(item)))
        elif isinstance(item, Sequence) and not isinstance(item, str):
            inline, rest = _value_lines(item, depth)
            if rest:
                raise ToolFailure("並びの中の、mapping を含む並びは書かない")
            lines.append(prefix + inline.lstrip())
        else:
            raise ToolFailure(f"YAML に書けない値の型（{type(item).__name__}）")
    return lines


def dump_document(data: Mapping[str, object], *, comments: Sequence[str] = ()) -> bytes:
    """先頭のコメント行と最上位の mapping を、決まったバイト列（UTF-8、行末は LF）にする。

    書いた本文を読み直して `data` と一致することを確かめる（一致しなければ `ToolFailure`）。
    """
    for comment in comments:
        if "\n" in comment or "\r" in comment:
            raise ToolFailure("コメントは 1 行ずつ渡す")
    lines = [f"# {comment}" if comment else "#" for comment in comments]
    if comments:
        lines.append("")
    lines.extend(_mapping_lines(dict(data), 0))
    text = "\n".join(lines) + "\n"
    try:
        loaded = load_yaml_mapping(Path("generated.yaml"), text=text)
    except ConfigError as exc:
        raise ToolFailure(f"書いた YAML を読み直せない: {exc}") from exc
    if not _same(loaded, data):
        raise ToolFailure("書いた YAML を読み直した値が元の値と違う")
    return text.encode("utf-8")


def _same(left: object, right: object) -> bool:
    """型まで含めて同じ値か（`1` と `1.0`、`True` と `1` を区別する）。"""
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return list(left.keys()) == list(right.keys()) and all(
            _same(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, Sequence) and not isinstance(right, str):
        return len(left) == len(right) and all(
            _same(a, b) for a, b in zip(left, right, strict=True)
        )
    return type(left) is type(right) and left == right
