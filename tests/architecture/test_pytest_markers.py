"""テストのマーカーと既定の除外（D08 §12）。

実データを使うテスト（`realdata`）と提供元への通信を使うテスト（`network`。D03 §14.16）は、
既定の実行と CI から除外する。除外が外れると、CI が実データや外部の提供元に依存してしまう。
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _pytest_options() -> dict[str, Any]:
    loaded = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    options = loaded["tool"]["pytest"]["ini_options"]
    assert isinstance(options, dict)
    return options


def test_realdata_and_network_markers_are_declared() -> None:
    markers = _pytest_options()["markers"]
    names = {str(marker).split(":", 1)[0] for marker in markers}
    assert {"realdata", "network"} <= names


def test_realdata_and_network_tests_are_excluded_by_default() -> None:
    assert _pytest_options()["addopts"] == "-m 'not realdata and not network'"
