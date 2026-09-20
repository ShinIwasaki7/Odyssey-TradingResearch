"""`Decimal(` の呼び出し位置の制限（D02 §4.6、ADR-0012）。

`Decimal(float_value)` は二進浮動小数の誤差をそのまま持ち込むため禁止されている
（ADR-0012）。禁止を字面で守るのではなく機械検査にするため、`Decimal` を直接構築してよい
モジュールを `common/money.py` と `common/canonical.py` に限る。他モジュールは
`money.decimal_from_str(s)` / `money.decimal_from_int(i)` を使う。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "src" / "odyssey_fx"

#: `Decimal(...)` を直接呼んでよいモジュール（D02 §4.6）。
ALLOWED_MODULES = frozenset(
    {
        "common/money.py",
        "common/canonical.py",
    }
)


def _decimal_call_lines(source: str) -> list[int]:
    """`Decimal(...)` / `decimal.Decimal(...)` の呼び出し行を返す。"""
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "Decimal":
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr == "Decimal":
            lines.append(node.lineno)
    return lines


def test_decimal_is_constructed_only_in_the_allowed_modules() -> None:
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        if relative in ALLOWED_MODULES:
            continue
        for lineno in _decimal_call_lines(path.read_text(encoding="utf-8")):
            offenders.append(f"{relative}:{lineno}")

    assert not offenders, (
        "Decimal(...) may only be constructed in "
        f"{sorted(ALLOWED_MODULES)} (D02 §4.6); found calls at: {offenders}. "
        "Use money.decimal_from_str / money.decimal_from_int instead."
    )


def test_the_allowed_modules_exist() -> None:
    """許可リストが実在するモジュールを指していることを確かめる（空虚な契約の防止）。"""
    for relative in sorted(ALLOWED_MODULES):
        assert (SOURCE_ROOT / relative).is_file(), f"{relative} is missing"
