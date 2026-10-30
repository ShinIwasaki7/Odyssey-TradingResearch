"""`RunConfig` の封印の snapshot と partition の並び（D06 v1.18 案 §3・§9.3。仮置き R4b-1〜R4b-5）。

- 空の並びは `ConfigDigest` の材料に入れない（項目を足す前の値と同じ）。
- 並びだけが違う 2 つの `RunConfig` の `ConfigDigest` は違う。
- 並びの形（系列の順・期間の始まりの順・partition の識別子が重ならない）が外れれば作れない。
- run manifest は並びが空でないときだけ並びを書き、項目の無い manifest は空の並びと読む。
"""

from __future__ import annotations

import json
from dataclasses import fields, replace

import pytest

from odyssey_fx.backtest.trace.manifest import config_digest_of, config_mapping
from odyssey_fx.backtest.trace.recorder import canonical_text
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.refs import ConfigDigest
from odyssey_fx.common.symbol import SymbolSpecRef
from odyssey_fx.common.time import UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.adapters.fs_store import _run_config_of, partition_id_of
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.final_grant import GrantedRead
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.evaluation.traces import EXECUTION_SERIES, USDJPY, manifest_for

_SPEC = SymbolSpecRef(symbol=USDJPY, version=1, digest=digest("spec"))
_TIMEFRAMES = {"15m": TimeframeRef("15m", 1)}


def _read(week: str, snapshot: str = "a" * 64) -> GrantedRead:
    return GrantedRead(
        snapshot_id=snapshot,
        partition_id=PartitionId(
            series=EXECUTION_SERIES,
            access_class=AccessClass.HOLDOUT_SINCE_2024,
            period_start=UtcTime.parse(week),
        ),
    )


_READS = (_read("2024-01-01T00:00:00Z"), _read("2024-01-07T22:00:00Z"))


def _digest(config: object) -> ConfigDigest:
    return config_digest_of(
        config,  # type: ignore[arg-type]
        symbol_spec_ref=_SPEC,
        calendar_ref="fx_ny17@v1",
        timeframe_def_refs=(TimeframeRef("15m", 1),),
    )


def test_an_empty_list_of_sealed_reads_leaves_the_config_digest_unchanged() -> None:
    config = manifest_for().config
    assert config.sealed_reads == ()
    before = {
        item.name: getattr(config, item.name)
        for item in fields(config)
        if item.name != "sealed_reads"
    }
    expected = ConfigDigest(
        digest=digest(
            {
                "config": before,
                "symbol_spec_ref": _SPEC,
                "calendar_ref": "fx_ny17@v1",
                "timeframe_def_refs": (TimeframeRef("15m", 1),),
            }
        )
    )
    assert _digest(config) == expected
    assert "sealed_reads" not in config_mapping(config)


def test_configs_that_differ_only_in_the_sealed_reads_have_different_digests() -> None:
    config = manifest_for().config
    one = replace(config, sealed_reads=_READS[:1])
    two = replace(config, sealed_reads=_READS)
    other = replace(config, sealed_reads=(_read("2024-01-01T00:00:00Z", "b" * 64),))
    digests = {_digest(config), _digest(one), _digest(two), _digest(other)}
    assert len(digests) == 4


def test_a_list_out_of_order_or_with_a_repeated_partition_cannot_build_a_config() -> None:
    config = manifest_for().config
    with pytest.raises(KernelValueError):
        replace(config, sealed_reads=(_READS[1], _READS[0]))
    with pytest.raises(KernelValueError):
        replace(config, sealed_reads=(_READS[0], _read("2024-01-01T00:00:00Z", "b" * 64)))


def test_the_manifest_config_round_trips_and_a_missing_key_reads_as_empty() -> None:
    config = replace(manifest_for().config, sealed_reads=_READS)
    payload = json.loads(canonical_text(config_mapping(config)))
    assert len(payload["sealed_reads"]) == 2
    assert _run_config_of(payload, _TIMEFRAMES) == config
    empty = json.loads(canonical_text(config_mapping(manifest_for().config)))
    assert "sealed_reads" not in empty
    assert _run_config_of(empty, _TIMEFRAMES).sealed_reads == ()


def test_a_partition_literal_is_read_back_only_in_its_canonical_form() -> None:
    partition = _READS[1].partition_id
    assert partition_id_of(str(partition), _TIMEFRAMES) == partition
    daily = PartitionId(
        series=replace(EXECUTION_SERIES, timeframe=TimeframeRef("1d_ny17", 1)),
        access_class=AccessClass.FORWARD_HOLDOUT,
        period_start=UtcTime.parse("2024-06-02T21:00:00Z"),
    )
    timeframes = {**_TIMEFRAMES, "1d_ny17": TimeframeRef("1d_ny17", 1)}
    assert partition_id_of(str(daily), timeframes) == daily
    with pytest.raises(KernelValueError):
        partition_id_of("USDJPY_15m_bid/HOLDOUT_SINCE_2024", _TIMEFRAMES)
