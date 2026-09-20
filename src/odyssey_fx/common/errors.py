"""共通カーネルの例外（D02 §10）。

値型の不変条件違反は構造エラーであり、`MissingInputPolicy`（入力欠損の扱い）の対象では
ない。各パッケージの domain はこの例外を継承せず、自パッケージの基底例外を持つ
（D01 §8）。カーネルの例外が上位で捕捉されるのは、設定読込（`app.config`）と契約検証
（`strategy.compiler`）の境界に限る。
"""

from __future__ import annotations

__all__ = ["KernelValueError"]


class KernelValueError(ValueError):
    """値型・参照型の不変条件に違反したことを表す構造エラー（D02 §10）。"""
