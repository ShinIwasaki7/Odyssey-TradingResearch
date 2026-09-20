"""`odyssey_fx.common.errors` の単体テスト（D02 §10）。"""

from __future__ import annotations

from odyssey_fx.common.errors import KernelValueError


def test_kernel_value_error_is_a_value_error() -> None:
    """設定読込・契約検証の境界で `ValueError` として捕捉できる（D02 §10）。"""
    assert issubclass(KernelValueError, ValueError)


def test_kernel_value_error_carries_its_message() -> None:
    error = KernelValueError("Price.value must be > 0")
    assert str(error) == "Price.value must be > 0"
