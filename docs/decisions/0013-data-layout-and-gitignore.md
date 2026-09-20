# ADR-0013: データは data/raw/market へ移し実体を git 管理外にする

- 状態: 承認（2026-09-18。2026-09-20 に access log の追跡を追加）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 C-1

## 文脈

`data/market/` の20個の CSV（約230MB）は未追跡で、既存の `.gitignore` は `data/*` を一括除外しており snapshot manifest も追跡できない。

## 決定

```text
data/raw/market/            移管した20個の CSV。上書き禁止。git 管理外
data/snapshots/<snapshot_id>/ 受入れ後の正規化データ。実体は git 管理外。manifest.json と access_log.jsonl は git 管理
runs/                       実行結果。git 管理外
```

## 影響

- `.gitignore` を書き換え、snapshot の `manifest.json` と `access_log.jsonl` を再包含する規則を入れる（段階−1 で manifest.json のみ実装済み。`access_log.jsonl` の再包含は D01 v2.1 の F5c と同じ骨格 PR で追加する）。

## 改訂履歴

| 日付 | 内容 |
|---|---|
| 2026-09-18 | 初版承認 |
| 2026-09-20 | ADR-0014 の消費遷移直列化に伴い、`access_log.jsonl` を git 管理対象に追加 |
- DVC / LFS は初版では使わない。
