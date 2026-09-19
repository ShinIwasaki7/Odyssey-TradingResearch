# ADR-0013: データは data/raw/market へ移し実体を git 管理外にする

- 状態: 承認（2026-09-18）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 C-1

## 文脈

`data/market/` の20個の CSV（約230MB）は未追跡で、既存の `.gitignore` は `data/*` を一括除外しており snapshot manifest も追跡できない。

## 決定

```text
data/raw/market/            移管した20個の CSV。上書き禁止。git 管理外
data/snapshots/<snapshot_id>/ 受入れ後の正規化データ。実体は git 管理外、manifest は git 管理
runs/                       実行結果。git 管理外
```

## 影響

- `.gitignore` を書き換え、snapshot manifest だけ再包含する規則を入れる（段階−1）。
- DVC / LFS は初版では使わない。
