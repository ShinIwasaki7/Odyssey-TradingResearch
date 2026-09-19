# ADR-0012: 台帳系は Decimal、Feature は float

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 B-4

## 文脈

台帳・丸め・予約計算には正確性が要り、Feature 計算には速度が要る。全面 Decimal と全面 float の中間として境界を固定する。

## 決定

- `Price`、`Quantity`、`Money`、SL/TP、約定価格、費用、balance、予約額: Decimal。
- EMA、ATR、リターンなどの Feature 計算: float。
- float から注文価格へ移すときは、専用変換処理で価格刻みに丸める。
- `Decimal(float_value)` は禁止し、文字列表現などを介して変換する。
- 丸め前の float、変換規則、丸め後の `Price` を根拠記録に残す。
- 初版の執行用 OHLC は Decimal として扱う。

## 影響

- 高速化版で float や整数 tick へ変える場合は、参照実装との同値性を検証する。
- 変換処理の配置は D02（`common.money`）で確定する。
