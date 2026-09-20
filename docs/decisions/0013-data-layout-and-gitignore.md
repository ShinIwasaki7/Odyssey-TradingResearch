# ADR-0013: データは data/raw/market へ移し実体を git 管理外にする

- 状態: 承認（2026-09-18。2026-09-20 に access log の追跡を追加。同日、検査報告の追跡を追加）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 C-1

## 文脈

`data/market/` の20個の CSV（約230MB）は未追跡で、既存の `.gitignore` は `data/*` を一括除外しており snapshot manifest も追跡できない。

## 決定

```text
data/raw/market/            移管した20個の CSV。上書き禁止。git 管理外
data/snapshots/<snapshot_id>/ 受入れ後の正規化データ。実体（Parquet）は git 管理外。
                            manifest.json・integrity_report.json・access_log.jsonl の3ファイルは git 管理
                            （確定段階で再実行した snapshot は integrity_report_provisional.json も git 管理）
data/snapshots/_pending/    暫定 snapshot。すべて git 管理外
runs/                       実行結果。git 管理外
```

## 影響

- `.gitignore` を書き換え、snapshot の `manifest.json` と `access_log.jsonl` を再包含する規則を入れる（段階−1 で manifest.json のみ実装済み。`access_log.jsonl` の再包含は D01 v2.1 の F5c と同じ骨格 PR で追加する）。

## 改訂履歴

| 日付 | 内容 |
|---|---|
| 2026-09-18 | 初版承認 |
| 2026-09-20 | ADR-0014 の消費遷移直列化に伴い、`access_log.jsonl` を git 管理対象に追加 |
| 2026-09-20 | D03 v1.3 に伴い、確定 snapshot の `integrity_report.json` を git 管理対象に追加。検査報告は manifest のダイジェスト対象であり承認・読み取り関門が必要とするため、追跡しなければ別クローンで Parquet を復元しても snapshot を検証できない。報告には価格や封印期間の統計値を含めず、検査種別・系列・区間・重大度・構造的な詳細だけを保存する。確定段階で 5〜7 を再実行した snapshot は、人間が分類の根拠にした暫定報告 `integrity_report_provisional.json` も追跡する（再実行で消えた警告に対する分類の根拠）。`_pending/` 配下は引き続き git 管理外 |
- 検査報告の追跡（2026-09-20 改訂）に伴う `.gitignore` の再包含規則（`!data/snapshots/*/integrity_report.json` と `!data/snapshots/*/integrity_report_provisional.json`）と `tests/architecture/test_gitignore_layout.py` の更新は、D03 v1.3 の実装 PR（分類形式の変更と同じ PR）で行う。それまでは検査報告は追跡されない（既存の確定 snapshot は存在しない）。
- DVC / LFS は初版では使わない。
