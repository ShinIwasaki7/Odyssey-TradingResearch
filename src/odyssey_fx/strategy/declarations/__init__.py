"""strategy の宣言モデル層（D04）。

戦略の「形」を表す不変データだけを置く。部品の計算規則・取引機会の状態機械・注文の受付は
ここでは決めない（D05・D06）。外部ライブラリと I/O を持たず、依存できるのは標準ライブラリ、
`odyssey_fx.common`、`marketdata.domain` だけである（D01 §3.2・§5）。

モジュール構成（D04 §2）:

- `validation`: 構築時検査・凍結・正規化の共有部品
- `duration`: 設定ファイルに書く期間値（`"2h"` 形式）の解析（D04 §13.1）
- `datatypes`: `DataTypeRef` のレジストリと、市場データ項目・現在コンテキストの対応表
- `refs`: `OutputRef` / `MarketDataRef` / `RuntimeInputRef`
- `read_spec`: `InputReadSpec` の4区分と窓型
- `missing`: `MissingInputPolicy`（段階2の2区分と段階3 の待機 `WaitForInput`・遡り `UsePrevious`）
- `specs`: `InputSpec` / `OutputSpec` / `InputBinding` / `InputArity` / `RetriggerMode` /
  `ParameterSpec` / `ParameterValue`
- `evaluation`: `EvaluationSpec` / `EvaluationSchedule`
- `state_spec` / `temporal`: `StateSpec` / `TemporalConstraints`
- `entry_policy`: `EntryPolicy`
- `opportunity`: `OpportunityValiditySpec` / `OpportunityConcurrencySpec`
- `contract` / `instance` / `definition`: 3クラスのトップレベル
- `digest`: 契約と戦略定義の内容ハッシュ（D04 §13.2）

サブモジュールは明示的に import して使う
（`from odyssey_fx.strategy.declarations.specs import InputSpec`）。本パッケージの
`__init__` は再輸出を行わない（`common` と同じ方針）。
"""
