"""`odyssey_fx.common.timeframe` の単体テスト（D02 §6・§11）。"""

from __future__ import annotations

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.timeframe import TimeframeRef


@pytest.mark.parametrize("identifier", ["15m", "1h", "4h_ny17", "1d_ny17", "m1"])
def test_timeframe_ref_accepts_the_documented_identifiers(identifier: str) -> None:
    assert TimeframeRef(identifier, 1).id == identifier


@pytest.mark.parametrize("identifier", ["15M", "4h-ny17", "4h ny17", "", "15m!"])
def test_timeframe_ref_rejects_invalid_identifiers(identifier: str) -> None:
    with pytest.raises(KernelValueError, match="TimeframeRef.id"):
        TimeframeRef(identifier, 1)


@pytest.mark.parametrize("version", [0, -1])
def test_timeframe_ref_version_must_be_at_least_one(version: int) -> None:
    with pytest.raises(KernelValueError, match=">= 1"):
        TimeframeRef("15m", version)


def test_timeframe_ref_rejects_a_bool_version() -> None:
    with pytest.raises(KernelValueError, match="must be an int"):
        TimeframeRef("15m", True)


def test_timeframe_ref_str_and_parse_roundtrip() -> None:
    ref = TimeframeRef("4h_ny17", 2)
    assert str(ref) == "4h_ny17@v2"
    assert TimeframeRef.parse(str(ref)) == ref


@pytest.mark.parametrize("text", ["15m", "15m@2", "@v1", "15m@vx", ""])
def test_timeframe_ref_parse_rejects_malformed_literals(text: str) -> None:
    with pytest.raises(KernelValueError):
        TimeframeRef.parse(text)
