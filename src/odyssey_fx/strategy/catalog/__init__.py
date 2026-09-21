"""strategy の部品カタログ（D05 §4）。

部品を「契約（宣言）＋実装＋実装参照」の1組として静的に登録する。実装は純粋関数の形を
とり、可変状態を内部に持たない（ADR-0008）。状態はランタイムが使用箇所ごとに保持する。

モジュール構成（D05 §2）:

- `inputs`: 部品が受け取るものの形（上の層の型を構造だけで受けるための `Protocol`）
- `registry`: 登録の単位・実装の2形式・登録時の検査
- `features/extreme`・`triggers/breakout`・`orders/market`・`protection/level_stop`・
  `exits/fixed_rr`: 段階2の5部品
- `initial`: 段階2の部品テーブル `INITIAL_CATALOG`

NumPy を使ってよい唯一のサブパッケージだが（ADR-0021・契約 F5b）、段階2の5部品はいずれも
使わない。サブモジュールは明示的に import して使う。
"""
