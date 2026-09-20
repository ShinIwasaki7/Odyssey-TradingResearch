"""系列と価格基準（D03 §3.1）。

`PriceBasis` は OHLC がどちら側の提示価格かを表す区分、`SeriesId` は「どの銘柄の・どの
時間足の・どの価格基準の足列か」を一意に指す値である。初版の受入れデータは売却側の
提示価格（bid）のみ（D03 §2）。

価格基準はファイルから検証できる事実ではなく人間の宣言なので、snapshot manifest では
`BasisDeclaration`（D03 §3.7）として `verified: false` を添えて記録する。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = ["PriceBasis", "SeriesId"]


class PriceBasis(Enum):
    """OHLC の価格基準（D03 §3.1）。

    `BID` は売却側、`ASK` は購入側、`MID` は両者の中値。初版の受入れデータは `BID` のみ。
    """

    BID = "bid"
    ASK = "ask"
    MID = "mid"


@dataclass(frozen=True, slots=True)
class SeriesId:
    """足列の識別（D03 §3.1）。

    `__str__` は `USDJPY/1h/bid`。時間足は `TimeframeRef` の `id` だけを使い、版は
    manifest の `conversion` と `series` が別に保持する（同じ系列の定義版が上がっても
    系列の呼び名は変わらないため）。
    """

    symbol: Symbol
    timeframe: TimeframeRef
    basis: PriceBasis

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("SeriesId.symbol must be a Symbol")
        if not isinstance(self.timeframe, TimeframeRef):
            raise MarketDataValueError("SeriesId.timeframe must be a TimeframeRef")
        if not isinstance(self.basis, PriceBasis):
            raise MarketDataValueError("SeriesId.basis must be a PriceBasis")

    def __str__(self) -> str:
        return f"{self.symbol}/{self.timeframe.id}/{self.basis.value}"

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3 の `CanonicalScalar`）。

        `SeriesId` はダイジェスト対象の列の整列鍵でもある（D03 §3.7.1）。`__str__` と
        同じ文字列を使い、manifest の記録と整列鍵が食い違わないようにする。
        """
        return str(self)
