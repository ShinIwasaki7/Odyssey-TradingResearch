"""読んだ YAML の値を、再帰的に変更できない形にする・作業用の写しに戻す。

凍結した dataclass（`Card`・`Change`・`LoopState`・`Preregistration`）が持つ入れ子の値を、外から
書き換えられないようにする（AGENTS.md の「凍結 dataclass に可変の入れ物を公開しない」）。mapping は
`MappingProxyType`、並びは `tuple` にする。操作を当てるときは `thaw` で dict・list の写しに戻す。

あわせて、カード・変更の提案に書ける値の型を確かめる（`plain_problems`）: 文字列・整数・有限の
浮動小数・真偽・null と、それらの mapping（キーは文字列）・並びだけ。YAML の安全な読込が返しうる
日時や、非有限の浮動小数（`.nan`・`.inf`）は、生成物に書けないので入力の誤りとして扱う。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

__all__ = ["freeze", "plain_problems", "thaw"]


def freeze(value: Any) -> Any:
    """mapping を `MappingProxyType`、並びを `tuple` にした写し（中身も再帰的に）。"""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """`freeze` の逆（dict・list の写し）。"""
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [thaw(item) for item in value]
    return value


def plain_problems(value: object, where: str) -> list[str]:
    """書ける値の型でない箇所の一覧（空なら合格）。"""
    if value is None or isinstance(value, bool | int | str):
        return []
    if isinstance(value, float):
        return [] if math.isfinite(value) else [f"{where}: 有限でない数"]
    if isinstance(value, Mapping):
        problems: list[str] = []
        for key, item in value.items():
            if not isinstance(key, str):
                problems.append(f"{where}: 文字列でないキー")
            else:
                problems += plain_problems(item, f"{where}.{key}")
        return problems
    if isinstance(value, list | tuple):
        return [
            problem
            for index, item in enumerate(value)
            for problem in plain_problems(item, f"{where}[{index}]")
        ]
    return [f"{where}: 書けない値の型（{type(value).__name__}）"]
