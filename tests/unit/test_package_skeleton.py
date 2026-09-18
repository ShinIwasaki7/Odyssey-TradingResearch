"""パッケージ骨格が import できることの確認（全体計画書 §4.2）。

段階−1 の完了条件は「依存規則検査とテストが空の状態で通る」ことなので、
ここでは構造の存在だけを検査し、振る舞いは検査しない。
"""

import importlib

import pytest

import odyssey_fx

SUBPACKAGES = [
    "odyssey_fx.common",
    "odyssey_fx.marketdata",
    "odyssey_fx.marketdata.domain",
    "odyssey_fx.marketdata.application",
    "odyssey_fx.marketdata.adapters",
    "odyssey_fx.strategy",
    "odyssey_fx.strategy.declarations",
    "odyssey_fx.strategy.records",
    "odyssey_fx.strategy.catalog",
    "odyssey_fx.strategy.compiler",
    "odyssey_fx.strategy.runtime",
    "odyssey_fx.backtest",
    "odyssey_fx.backtest.domain",
    "odyssey_fx.backtest.engine",
    "odyssey_fx.backtest.admission",
    "odyssey_fx.backtest.execution",
    "odyssey_fx.backtest.portfolio",
    "odyssey_fx.backtest.trace",
    "odyssey_fx.backtest.application",
    "odyssey_fx.evaluation",
    "odyssey_fx.evaluation.domain",
    "odyssey_fx.evaluation.application",
    "odyssey_fx.evaluation.adapters",
    "odyssey_fx.app",
    "odyssey_fx.app.config",
    "odyssey_fx.app.cli",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_is_importable(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} must document its responsibility"


def test_version_is_exposed() -> None:
    assert odyssey_fx.__version__ == "0.0.1"
