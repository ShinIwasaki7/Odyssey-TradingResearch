"""提供元への実際の通信を使う確認（D03 §14.16、D08 §12 のマーカー `network`）。

**既定の実行と CI では除外する**（`pyproject.toml` の `addopts`）。実行するときは
`uv run pytest -m network` を明示する。代表例の試行（D03 §14.14 の段 3）の前に、提供元の
URL の型と bi5 の形が録画した時間ファイルと同じであることを確かめるためのもの。
"""

from __future__ import annotations

import pytest

from odyssey_fx.marketdata.adapters.dukascopy_source import DukascopyTickSource, decode_bi5
from odyssey_fx.marketdata.domain.refill import HourKey
from tests.fixtures.refill import BI5_00H, HOUR_00, provider_ref
from tests.fixtures.synthetic import market

pytestmark = pytest.mark.network


def test_the_provider_returns_the_recorded_hour() -> None:
    source = DukascopyTickSource()
    url = provider_ref().settings.url_for(HourKey(symbol=market.USDJPY, start=HOUR_00))
    result = source.request(url, timeout_seconds=60)
    assert result.status == 200, result.detail
    assert source.decode(result.body).tick_digest == decode_bi5(BI5_00H).tick_digest
