"""宣言型が共有する構築時検査の部品（D04 §3）。

D04 §3 は「宣言型の `__post_init__` は構造的な不変条件だけを見る」と定めている。参照の
解決・型の整合・銘柄の伝播は `compiler` の仕事であり、ここに書かない。

本モジュールが担うのは次の3つだけである。

1. **字種と範囲の検査**: 識別子（`^[a-z0-9_]+$`）・版（`>= 1`）・本数などの素朴な検査。
2. **凍結**: `Mapping` を `MappingProxyType` で包み、構築後に書き換えられないようにする
   （D04 §1・ADR-0011）。
3. **正規化**: 順序に意味を持たせないコレクションを安定な鍵で並べ替え、重複を拒否する
   （D04 §3 の表）。意味の同じ2つの宣言が「書いた順序」の違いだけで別の内容ハッシュに
   なると、実験の同一性が分かれてしまうため。

違反はすべて `KernelValueError`（D02 §10）で送出する。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType
from typing import Final

from odyssey_fx.common.errors import KernelValueError

__all__ = [
    "IDENTIFIER_PATTERN",
    "freeze_mapping",
    "normalized_unique",
    "require_bool",
    "require_identifier",
    "require_instance",
    "require_int",
    "require_mapping_of",
    "require_tuple_of",
    "require_version",
]

#: 宣言に現れる識別子（部品 ID・使用箇所 ID・入出力名・パラメータ名）の字種（D04 §3）。
#: 照合は `fullmatch`（`$` は末尾の改行にも一致するため。D02 の字種検査と同じ方針）。
IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z0-9_]+$")


def require_instance[T](value: object, expected: type[T], label: str) -> T:
    """`value` が `expected` のインスタンスであることを要求する。"""
    if not isinstance(value, expected):
        raise KernelValueError(f"{label} must be a {expected.__name__}, got {value!r}")
    return value


def require_bool(value: object, label: str) -> bool:
    """真偽値であることを要求する（`int` の紛れ込みを拒否する）。"""
    if not isinstance(value, bool):
        raise KernelValueError(f"{label} must be a bool, got {value!r}")
    return value


def require_int(value: object, label: str) -> int:
    """整数であることを要求する。`bool` は `int` の派生型なので先に弾く。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    return value


def require_identifier(value: object, label: str) -> str:
    """識別子の字種（`^[a-z0-9_]+$`）を要求する（D04 §3）。"""
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.fullmatch(value):
        raise KernelValueError(f"{label} must match ^[a-z0-9_]+$, got {value!r}")
    return value


def require_version(value: object, label: str) -> int:
    """版が 1 以上の整数であることを要求する（D04 §3）。"""
    version = require_int(value, label)
    if version < 1:
        raise KernelValueError(f"{label} must be >= 1, got {version}")
    return version


def require_tuple_of[T](value: object, expected: type[T], label: str) -> tuple[T, ...]:
    """`tuple` であり、全要素が `expected` であることを要求する。"""
    if not isinstance(value, tuple):
        raise KernelValueError(f"{label} must be a tuple, got {type(value).__name__}")
    for index, item in enumerate(value):
        if not isinstance(item, expected):
            raise KernelValueError(f"{label}[{index}] must be a {expected.__name__}, got {item!r}")
    return value


def require_mapping_of[V](
    value: object,
    expected: type[V],
    label: str,
    *,
    key_check: Callable[[object, str], str] = require_identifier,
) -> Mapping[str, V]:
    """`Mapping` であり、キーが識別子・値が `expected` であることを要求する。"""
    if not isinstance(value, Mapping):
        raise KernelValueError(f"{label} must be a Mapping, got {type(value).__name__}")
    for key, item in value.items():
        key_check(key, f"{label} key")
        if not isinstance(item, expected):
            raise KernelValueError(f"{label}[{key!r}] must be a {expected.__name__}, got {item!r}")
    return value


def freeze_mapping[V](value: Mapping[str, V]) -> Mapping[str, V]:
    """構築後に書き換えられない `Mapping` を返す（D04 §1・ADR-0011）。

    キーは Unicode コードポイント順に整列してから包む。`Mapping` の反復順は正規化
    エンコード（D02 §9.3）がキー順に並べ替えるためダイジェストには影響しないが、
    記録・エラーメッセージの並びまで入力順に左右されないようにする。
    """
    return MappingProxyType({key: value[key] for key in sorted(value)})


def normalized_unique[T](
    items: Iterable[T],
    key: Callable[[T], object],
    label: str,
) -> tuple[T, ...]:
    """順序に意味を持たせないコレクションを鍵で整列し、重複を拒否する（D04 §3）。

    重複の判定は整列鍵で行う。鍵が同じ要素が2つあると、整列しても並びが一意に定まらず、
    同じ宣言から2通りの内容ハッシュが出うるためである。
    """
    ordered = sorted(items, key=lambda item: _sort_key(key(item)))
    seen: set[str] = set()
    for item in ordered:
        item_key = _sort_key(key(item))
        if item_key in seen:
            raise KernelValueError(f"{label} must not contain duplicates: {item_key}")
        seen.add(item_key)
    return tuple(ordered)


def _sort_key(value: object) -> str:
    """整列鍵を、比較もハッシュもできる1本の文字列へ正規化する。

    鍵として渡ってくるのは文字列か、文字列の組（区分タグと正規化エンコードなど）である。
    union 型のまま `sorted` に渡すと比較できないので、**先頭に種別の印を付けて**文字列へ
    落とす。印を付けるのは、文字列 `"('a',)"` と組 `('a',)` が同じ鍵に潰れないようにする
    ためである（潰れると別の宣言が重複として拒否されてしまう）。
    """
    if isinstance(value, tuple):
        return "T" + repr(tuple(_sort_key(item) for item in value))
    if isinstance(value, str):
        return "S" + value
    return "S" + str(value)
