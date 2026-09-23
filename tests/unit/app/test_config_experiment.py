"""実験設定の読込（D01 §10.1、D06 §3、ADR-0018）。

**正本の設定ファイルをそのまま読む**。書き方が変わればこのテストも追従する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.config import load_timeframes
from odyssey_fx.app.config.experiment import ExperimentConfig, load_experiment, parse_series
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.marketdata.domain.series import PriceBasis
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS = REPO_ROOT / "configs"
EXPERIMENT = CONFIGS / "experiments/strategy_a_t01.yaml"


@pytest.fixture
def timeframe_defs() -> dict[str, object]:
    return dict(load_timeframes(CONFIGS / "calendars/timeframes_v1.yaml"))


def _load(path: Path, timeframe_defs: dict[str, object]) -> ExperimentConfig:
    return load_experiment(path, timeframe_defs, INITIAL_CATALOG)  # type: ignore[arg-type]


def test_the_shipped_experiment_loads(timeframe_defs: dict[str, object]) -> None:
    """同梱の実験設定が宣言型へ変換される（D06 §3）。"""
    experiment = _load(EXPERIMENT, timeframe_defs)

    assert experiment.experiment_id == "strategy_a_t01"
    assert str(experiment.execution_series) == "USDJPY/15m/bid"
    assert experiment.account.initial_balance.amount == decimal_from_str("1000000")
    assert experiment.risk_policy.trial_risk_rate == decimal_from_str("0.02")
    assert experiment.cost_model.commission_per_unit.amount == decimal_from_str("0.001")
    assert experiment.cost_model.spread_model.offset.value == decimal_from_str("0.02")
    assert experiment.execution_policy.entry_delay_bars == 0
    assert experiment.strategy.strategy_id == "strategy_a"
    assert [item.instance_id for item in experiment.strategy.components] == [
        "breakout_level",
        "entry_order",
        "entry_trigger",
        "initial_stop",
        "stop_level",
        "take_profit",
    ]
    assert experiment.strategy.opportunity_concurrency.max_active == 1


def test_the_shipped_experiment_compiles(timeframe_defs: dict[str, object]) -> None:
    """宣言が部品カタログと整合し、解決済み設定になる（D04 §12、D05 §5）。"""
    from odyssey_fx.app import composition

    experiment = _load(EXPERIMENT, timeframe_defs)
    compiled = composition.compile_experiment_strategy(experiment, timeframe_defs)  # type: ignore[arg-type]
    assert str(compiled.symbol) == "USDJPY"


def test_the_policy_references_come_from_the_declaration(
    timeframe_defs: dict[str, object], tmp_path: Path
) -> None:
    """中身の違うポリシーは違う版参照になる（上位設計書 §4.7.15）。

    固定の文字列にすると、別の結果が同じ実行の識別子を指してしまう。
    """
    experiment = _load(EXPERIMENT, timeframe_defs)
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        EXPERIMENT.read_text(encoding="utf-8").replace(
            'trial_risk_rate: "0.02"', 'trial_risk_rate: "0.03"'
        ),
        encoding="utf-8",
    )
    other = _load(changed, timeframe_defs)
    assert other.policy_ref("risk") != experiment.policy_ref("risk")
    assert other.policy_ref("cost") == experiment.policy_ref("cost")


def test_a_float_price_is_refused(timeframe_defs: dict[str, object], tmp_path: Path) -> None:
    """価格・比率を浮動小数で書いた設定は拒否する（ADR-0012、D01 §10.1）。"""
    path = tmp_path / "float.yaml"
    path.write_text(
        EXPERIMENT.read_text(encoding="utf-8").replace(
            'trial_risk_rate: "0.02"', "trial_risk_rate: 0.02"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="条件を満たさない"):
        _load(path, timeframe_defs)


def test_an_undeclared_key_is_refused(timeframe_defs: dict[str, object], tmp_path: Path) -> None:
    """宣言していないキーは拒否する（D01 §10.1）。綴りの誤りが既定値で通らないようにする。"""
    path = tmp_path / "extra.yaml"
    path.write_text(EXPERIMENT.read_text(encoding="utf-8") + "\nunknown_key: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="条件を満たさない"):
        _load(path, timeframe_defs)


def test_an_unknown_component_is_refused(timeframe_defs: dict[str, object], tmp_path: Path) -> None:
    """カタログに無い部品を宣言した設定は拒否する（D05 §4.3）。"""
    path = tmp_path / "unknown_component.yaml"
    path.write_text(
        EXPERIMENT.read_text(encoding="utf-8").replace(
            'component: "extreme_price"', 'component: "not_a_component"', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="カタログに無い"):
        _load(path, timeframe_defs)


def test_an_unknown_timeframe_in_a_series_is_refused(
    timeframe_defs: dict[str, object], tmp_path: Path
) -> None:
    """時間足定義の無い系列は拒否する（D03 §3.1・§3.2）。"""
    path = tmp_path / "unknown_timeframe.yaml"
    path.write_text(
        EXPERIMENT.read_text(encoding="utf-8").replace(
            'execution_series: "USDJPY/15m/bid"', 'execution_series: "USDJPY/7m/bid"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="定義が設定に無い"):
        _load(path, timeframe_defs)


def test_a_snapshot_that_is_not_a_digest_is_refused(
    timeframe_defs: dict[str, object], tmp_path: Path
) -> None:
    """snapshot の識別子は16進64文字でなければならない（D03 §3.7.1）。"""
    path = tmp_path / "bad_snapshot.yaml"
    path.write_text(
        EXPERIMENT.read_text(encoding="utf-8").replace(
            f'snapshot: "{"0" * 64}"', 'snapshot: "not-a-digest"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="16進64文字"):
        _load(path, timeframe_defs)


def test_an_input_source_needs_a_known_prefix(
    timeframe_defs: dict[str, object], tmp_path: Path
) -> None:
    """入力の参照元は3つの書き方だけを受ける（D04 §4.3）。"""
    path = tmp_path / "bad_source.yaml"
    path.write_text(
        EXPERIMENT.read_text(encoding="utf-8").replace(
            '- "market:USDJPY/1h/bid:HIGH"', '- "USDJPY/1h/bid:HIGH"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="いずれかで書くこと"):
        _load(path, timeframe_defs)


def test_the_series_parser_resolves_the_timeframe_version(
    timeframe_defs: dict[str, object],
) -> None:
    """系列の文字列は時間足の版を持たないので、定義から解決する（D03 §3.1）。"""
    series = parse_series("USDJPY/1h/bid", timeframe_defs)  # type: ignore[arg-type]
    assert series.timeframe.id == "1h"
    assert series.timeframe.version >= 1
    assert series.basis is PriceBasis.BID


def test_the_series_parser_refuses_a_malformed_literal(timeframe_defs: dict[str, object]) -> None:
    with pytest.raises(ConfigError, match="形で書くこと"):
        parse_series("USDJPY/1h", timeframe_defs)  # type: ignore[arg-type]
