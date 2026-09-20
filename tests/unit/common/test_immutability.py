"""`common` の型がすべて不変であることの確認（D02 §1 規則2、ADR-0011）。

`common` に置いてよい可変オブジェクトは `IdAllocator`（実行コンテキストの採番器）だけで、
それ以外の値型・参照型・記録型は `@dataclass(frozen=True, slots=True)` でなければならない。
規則を字面で守るのではなく、モジュールを走査して機械検査する。
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import is_dataclass
from enum import Enum
from typing import Any

import pytest

import odyssey_fx.common
from odyssey_fx.common.ids import IdAllocator
from odyssey_fx.common.money import CurrencyCode

#: 可変であってよい唯一の型（D02 §1 規則2・§7.3）。
MUTABLE_EXCEPTIONS = frozenset({IdAllocator})

COMMON_MODULES = sorted(module.name for module in pkgutil.iter_modules(odyssey_fx.common.__path__))


def _public_classes(module_name: str) -> list[tuple[str, type[Any]]]:
    module = importlib.import_module(f"odyssey_fx.common.{module_name}")
    exported = getattr(module, "__all__", [])
    found: list[tuple[str, type[Any]]] = []
    for name in exported:
        obj = getattr(module, name)
        if inspect.isclass(obj):
            found.append((name, obj))
    return found


def test_the_module_list_matches_the_design() -> None:
    """D02 §2 が挙げる9モジュールがすべて存在する。"""
    assert COMMON_MODULES == [
        "canonical",
        "errors",
        "ids",
        "money",
        "reason",
        "refs",
        "symbol",
        "time",
        "timeframe",
    ]


@pytest.mark.parametrize("module_name", COMMON_MODULES)
def test_public_dataclasses_are_frozen_with_slots(module_name: str) -> None:
    offenders: list[str] = []
    for name, obj in _public_classes(module_name):
        if not is_dataclass(obj):
            continue
        params = obj.__dataclass_params__  # type: ignore[attr-defined]
        if not params.frozen:
            offenders.append(f"{name} is not frozen")
        # `slots=True` の dataclass は `__slots__` を必ず持つ。フィールドを追加しない
        # 派生クラスでは空 tuple になる（スロットは基底クラス側が持つ）ので、
        # 真偽ではなく存在を見る。`__dict__` が生えていないことも併せて確かめる。
        if not hasattr(obj, "__slots__"):
            offenders.append(f"{name} has no __slots__")
        elif "__dict__" in dir(obj):
            offenders.append(f"{name} still carries a __dict__")
    assert not offenders, f"odyssey_fx.common.{module_name}: {offenders}"


@pytest.mark.parametrize("module_name", COMMON_MODULES)
def test_no_unexpected_mutable_classes(module_name: str) -> None:
    """dataclass でも Enum でも Protocol でもない公開クラスは、例外リストにあるものだけ。"""
    offenders: list[str] = []
    for name, obj in _public_classes(module_name):
        if is_dataclass(obj) or issubclass(obj, (Enum, BaseException)):
            continue
        if getattr(obj, "_is_protocol", False):
            continue
        if obj in MUTABLE_EXCEPTIONS:
            continue
        offenders.append(name)
    assert not offenders, (
        f"odyssey_fx.common.{module_name} exposes non-frozen classes {offenders};"
        " only IdAllocator may be mutable (D02 §1)"
    )


def test_representative_values_reject_mutation() -> None:
    """凍結が宣言だけでなく実際に効いていることを、代表的な型で確かめる。"""
    from decimal import Decimal

    from odyssey_fx.common.ids import OrderId
    from odyssey_fx.common.money import Money, Price
    from odyssey_fx.common.symbol import Symbol
    from odyssey_fx.common.time import UtcTime

    samples: list[tuple[Any, str, Any]] = [
        (UtcTime.from_components(2026, 3, 1), "value", None),
        (Price(Decimal(1)), "value", Decimal(2)),
        (Money(Decimal(1), CurrencyCode("JPY")), "amount", Decimal(2)),
        (Symbol("USDJPY"), "code", "EURUSD"),
        (OrderId(1), "seq", 2),
    ]
    for value, field, replacement in samples:
        with pytest.raises((AttributeError, TypeError)):
            setattr(value, field, replacement)


def test_id_allocator_is_the_only_mutable_object() -> None:
    assert MUTABLE_EXCEPTIONS == {IdAllocator}
    assert not is_dataclass(IdAllocator)
    assert IdAllocator.__slots__ == ("_counters", "_run_id")
