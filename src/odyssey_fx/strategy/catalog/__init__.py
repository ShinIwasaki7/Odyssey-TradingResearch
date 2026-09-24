"""strategy の部品カタログ（D05 §4）。

部品を「契約（宣言）＋実装＋実装参照」の1組として静的に登録する。実装は純粋関数の形を
とり、可変状態を内部に持たない（ADR-0008）。状態はランタイムが使用箇所ごとに保持する。

モジュール構成（D05 §2）:

- `inputs`: 部品が受け取るものの形（上の層の型を構造だけで受けるための `Protocol`）
- `registry`: 登録の単位・実装の2形式・登録時の検査
- `features/extreme`・`triggers/breakout`・`orders/market`・`protection/level_stop`・
  `exits/fixed_rr`: 段階2の5部品
- `initial`: 段階2の部品テーブル `INITIAL_CATALOG`
- 段階3 で足した部品（D05 §2）: `features/ema`・`features/atr`（指標）、`conditions/compare`・
  `conditions/logic`・`conditions/transition`（条件）、`permissions/from_condition`（市場状態）、
  `filters/condition_filter`（後続確認）、`exits/trailing_stop`（追従する損切り）。あわせて、
  同じ実装を別の読み取り条件・起動条件で登録した v2 の契約（`ema`・`price_compare`・
  `permission_from_condition`・`breakout_trigger`・`market_order_intent`・`level_stop_loss`。
  D05 §9.2）を各モジュールに置く。**段階3 の登録はまだ `INITIAL_CATALOG` に入れていない**。
  コンパイラとランタイムがそれらを動かせるようになる変更（段階3 の後続の PR）で部品テーブルへ
  載せる。それまで段階2 の実行が受け付ける部品の範囲は変わらない。

NumPy を使ってよい唯一のサブパッケージだが（ADR-0021・契約 F5b）、段階2の5部品はいずれも
使わない。サブモジュールは明示的に import して使う。
"""
