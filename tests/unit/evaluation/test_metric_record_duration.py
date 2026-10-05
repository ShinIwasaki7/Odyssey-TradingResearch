"""`METRICS` 表の時間の値の読み戻しはプロセス全体の十進数設定に依存しない（ADR-0012、D02）。

段階5 実装 PR 4 の残件（集約表・保存済みの評価の指標の読み戻しの `DURATION` の換算）。指標集合 v2
には時間の指標が無いので、宣言の表を差し替えて換算の経路だけを確かめる。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import localcontext

import pytest

from odyssey_fx.evaluation.adapters.fs_store import _metric_record_of
from odyssey_fx.evaluation.domain.metrics import METRIC_KINDS, DurationValue, MetricId, MetricKind


def _row(seconds: str) -> dict[str, object]:
    return {
        "metric_id": "EXPOSURE_RATE",
        "value_kind": "DURATION",
        "value_reason": None,
        "value_amount_amount": None,
        "value_amount_currency": None,
        "value_ratio": None,
        "value_count": None,
        "value_duration": seconds,
        "value_offset": None,
        "caveats": [],
        "observation_count": 1,
        "inputs": [],
    }


def test_a_duration_is_read_back_exactly_under_a_low_precision_global_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(METRIC_KINDS, MetricId.EXPOSURE_RATE, MetricKind.DURATION)
    with localcontext() as context:
        context.prec = 3
        record = _metric_record_of(_row("123456.789012"))
    assert isinstance(record.value, DurationValue)
    assert record.value.duration == timedelta(seconds=123456, microseconds=789012)
