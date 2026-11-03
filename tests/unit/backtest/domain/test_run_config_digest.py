"""換算の snapshot と `ConfigDigest`（D06 v1.20 案 §19.2・仮置き C1-1。CT-11）。人工の値だけ。"""

from __future__ import annotations

from dataclasses import fields, replace

from odyssey_fx.backtest.domain.policies import RunConfig
from odyssey_fx.backtest.trace.manifest import config_digest_of, config_mapping
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import SnapshotId
from odyssey_fx.common.refs import ConfigDigest, SnapshotRef
from tests.fixtures.backtest.harness import (
    CALENDAR_REF,
    SYMBOL_SPEC_REF,
    TIMEFRAME_REFS,
    _digest,
    build_run,
)
from tests.fixtures.backtest.paths import RUN_INTERVAL, execution_bars, signal_bars


def _config() -> RunConfig:
    return build_run(
        signal_bars=signal_bars(), execution_bars=execution_bars(), run_interval=RUN_INTERVAL
    ).config


def _digest_of(config: RunConfig) -> ConfigDigest:
    return config_digest_of(
        config,
        symbol_spec_ref=SYMBOL_SPEC_REF,
        calendar_ref=CALENDAR_REF,
        timeframe_def_refs=TIMEFRAME_REFS,
    )


def test_ct11_no_conversion_snapshot_leaves_the_config_digest_as_before() -> None:
    """CT-11: 換算の snapshot が `None` の `RunConfig` の `ConfigDigest` は項目を足す前と同じ。"""
    config = _config()
    assert config.conversion_snapshot_ref is None
    before = {
        item.name: getattr(config, item.name)
        for item in fields(config)
        if item.name not in ("sealed_reads", "conversion_snapshot_ref")
    }
    expected = ConfigDigest(
        digest=digest(
            {
                "config": before,
                "symbol_spec_ref": SYMBOL_SPEC_REF,
                "calendar_ref": CALENDAR_REF,
                "timeframe_def_refs": TIMEFRAME_REFS,
            }
        )
    )
    assert _digest_of(config) == expected
    assert "conversion_snapshot_ref" not in config_mapping(config)


def test_ct11_configs_that_differ_only_in_the_conversion_snapshot_differ() -> None:
    """CT-11: 換算の snapshot だけが違う 2 つの `RunConfig` の `ConfigDigest` は違う。"""
    config = _config()
    first = replace(config, conversion_snapshot_ref=SnapshotRef(SnapshotId(_digest("a"))))
    second = replace(config, conversion_snapshot_ref=SnapshotRef(SnapshotId(_digest("b"))))
    assert "conversion_snapshot_ref" in config_mapping(first)
    assert len({_digest_of(config), _digest_of(first), _digest_of(second)}) == 3
