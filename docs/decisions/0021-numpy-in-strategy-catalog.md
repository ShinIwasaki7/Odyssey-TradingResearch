# ADR-0021: NumPy は strategy.catalog の部品実装内部に限り使用可

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 仮置き（D01 §14）、[D01](../design/D01_architecture_and_dependency_rules.md)

## 文脈

application 層は外部ライブラリを禁止するが、Feature 計算（float）には数値計算ライブラリが有用。

## 決定

`strategy.catalog` に限り、承認された純粋な数値計算ライブラリとして NumPy を使用できる（条件付き承認）。使用許可は確定、具体的に使用する部品は D04/D05 で決める。条件:

- `numpy.ndarray` を部品の公開入出力・状態・trace に出さない。
- NumPy の乱数やグローバル状態を使用しない。
- 部品実装内部の決定論的計算に限定する。
- 使用版を `uv.lock` と環境 manifest に記録する。
- NumPy を使わない単純部品へ使用を強制しない。

## 影響

- D01 §2 の application 層の定義を「I/O・永続化・設定解析などのインフラライブラリに依存しない。`strategy.catalog` に限り承認された純粋数値計算ライブラリを使用できる」とした。F5b 契約で catalog 以外への NumPy import を禁止する。
