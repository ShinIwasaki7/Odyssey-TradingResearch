"""共通カーネル: 全パッケージが共有する値型・参照型・理由コード。

tz-aware UTC 時刻、半開区間、ProcessingPoint、用途別 ID 型、Price/Quantity/Money、
Symbol/銘柄仕様、時間足定義参照、ReasonCode、PolicyRef/EvidenceRef を所有する。
業務ロジックと I/O は持たない。

モジュール構成（D02 §2）:

- `errors`: `KernelValueError`（不変条件違反を表す構造エラー）
- `time`: `UtcTime`、`Interval`、`PhaseRank`、`PhaseSet`、`ProcessingPoint`
- `canonical`: 正規化エンコードとダイジェスト、`CodeDigest` / `LockDigest` の算出
- `ids`: 用途別 ID 型、`RunId`、`IdAllocator`
- `refs`: `ContentDigest` と各種参照型、`RunId` の組み立て
- `money`: `CurrencyCode`、`Price`、`PriceOffset`、`Quantity`、`Money`、`ConversionRate`、
  丸め、float からの変換
- `symbol`: `Symbol`、`SymbolSpec`、`SymbolSpecRef`
- `timeframe`: `TimeframeRef`
- `reason`: `ReasonCode`、`Reason`、型付き詳細、`MissingInputReason`

サブモジュールは明示的に import して使う（`from odyssey_fx.common.money import Price`）。
本パッケージの `__init__` は再輸出を行わない。
"""
