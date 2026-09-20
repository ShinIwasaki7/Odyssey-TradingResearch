# D03: 市場データ・時刻基盤設計（`odyssey_fx.marketdata`）

作成日: 2026-09-19
状態: **承認（2026-09-20）**。v1.1（2026-09-20）: 設計文書 PR #3 の Codex 指摘により、短縮セッションの足の不変条件をカレンダー対応の期待区間で定義（第3.2節・第3.3節・第5.2節）。v1.2（同日）: 公開遅延の非負制約と `available_at >= bar_end`、DST 切替日の起点解決規則、snapshot ダイジェスト対象の列の正規順序を追加。第13節の9項目はすべて推奨案を採用。承認条件5点（①時間足定義の `length` を `nominal_length` に統一、②価格基準の宣言者・宣言日時を `SnapshotId` の対象から外して決定論化、③`QUARANTINED_UNASSIGNED` は再分類まで読めないと明記、④承認前の snapshot を読めないようにする、⑤CLI を暫定 ID フローに合わせる）を反映済み。ADR-0016 条件1（D01〜D03）を本書で充足し、段階1の実装を開始できる。v1.3（2026-09-20、ユーザー決定3件）: 段階1の実データ受入れ（PR #10）で分類が必要な警告が 23,989 件に達したことを受け、①人間の分類を必須とする検査種別を `MISSING_EXPECTED_BAR` と `UNEXPECTED_BAR` の明示集合に限定し、種別ごとに許される分類結果を分けた（第3.9節）。②分類 1 件を「検査種別 × 区間 × 対象系列（明示集合または全系列）」でまとめて宣言できる形にし、確定時の検査規則を定めた（第3.7節・第3.7.1節・第4節 9・第10節）。③検査報告 `integrity_report.json` を git 管理対象に加えた（第3.7節、ADR-0013 改訂）。併せて、確定段階の再実行が暫定 snapshot の実体を内容照合の上で再利用することを明記した（第4節 9。PR #10 の人間レビュー指摘）。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §3（データ前提）、§4.3.9〜4.3.10（読み方・鮮度）、§4.3.13（公開予定・遅延）、§4.7.13 C（完全性検査・カレンダー）、[全体計画書](fx_research_platform_overall_plan.md) §5.2、[D01](D01_architecture_and_dependency_rules.md) §1・§4・§10.2、[D02](D02_common_kernel.md)、ADR-0013（データ配置）、ADR-0014（期間のアクセス分類）、ADR-0015（初版の対象）
対応段階: 段階1で実装。

## 0. 本書の位置付け

`odyssey_fx.marketdata` の domain 型、受入れ手順、足生成規則、カレンダー、公開スケジュールと遅延シナリオ、as-of ビュー、公開フィード、完全性検査、期間のアクセス分類の物理分離を確定する。戦略の判断・約定処理は扱わない。

凡例は全体計画書第0節に従う。本書が新たに決める事項のうち意味論に影響するものは第13節にまとめ、承認時に確認する。

## 1. 責務と境界（確定）

| 提供するもの | 利用側 | ポート（D01 §4） |
|---|---|---|
| 受入れ済み snapshot（Parquet 実体＋ manifest） | `evaluation`（参照）、`app` | `SnapshotStore`（自パッケージ）、`SnapshotCatalog`（`evaluation` 定義） |
| 判断時点の as-of 読み取り | `strategy.runtime` | `MarketDataView`（`strategy` 定義） |
| 公開イベント列、執行系列、カレンダー | `backtest` | `PublicationFeed`、`ExecutionSeries`、`Calendar`（`backtest` 定義） |

本パッケージは `common` と自パッケージ以外に依存しない（D01 §3.2）。ポート定義を import せず構造的に満たす（D01 §2.2 規則7）。

## 2. 確認済みのデータ事実（受入れの前提）

2026-09-18 に確認した `data/raw/market/` の事実。受入れ時に manifest へ記録し、事実と異なれば受入れを失敗させる。

| 項目 | 事実 |
|---|---|
| ファイル | 10 ペア × 2 時間足（`<SYMBOL>_15m_merged.csv`、`<SYMBOL>_1h_merged.csv`）、計20 |
| 列 | 先頭列（無名）: 時刻、`open`、`high`、`low`、`close`、`volume`、`source` |
| 時刻書式 | `YYYY-MM-DD HH:MM:SS+00:00`（オフセット明示、UTC）。足の**開始**ラベル |
| `source` | `histdata` / `dukascopy`。価格計算には使わず出所として保存 |
| `volume` | HistData 由来は 0。真の出来高と解釈しない |
| 期間 | 2016-01-03 22:00Z 開始。末尾は AUDUSD・EURJPY・EURUSD・USDJPY が 2026-04-10、他6ペアが 2026-05-01 |
| 価格基準 | **検証済みの事実ではない**。上位設計書の記述に基づき人間が bid と**宣言**する。ファイルから検証できないため、manifest には `basis_declaration`（値と `verified: false`。`SnapshotId` の対象）として記録し、宣言者・宣言日時は `declaration_record`（`SnapshotId` の対象外）に分けて記録する。事実とは区別する |

## 3. domain 型（確定。フィールドの細部は案）

### 3.1 系列と価格基準

- `PriceBasis`: `BID` / `ASK` / `MID`。初版の受入れデータは `BID` のみ。
- `SeriesId(symbol: Symbol, timeframe: TimeframeRef, basis: PriceBasis)`。`__str__` は `USDJPY/1h/bid`。

### 3.2 時間足定義 `TimeframeDefinition`

`common.TimeframeRef` が指す定義本体。

| フィールド | 型 | 意味 |
|---|---|---|
| `ref` | `TimeframeRef` | `id` と `version` |
| `nominal_length` | `timedelta` | 名目の長さ（15m、1h、4h、1d）。表示と `BarsWindow` の概算にだけ使い、足の検証には使わない |
| `alignment` | `FixedUtcAlignment \| SessionAlignment` | 足境界の決め方 |

- `FixedUtcAlignment`: UTC のエポックからの `nominal_length` の整数倍に整列（15m、1h）。この整列では足の長さは常に `nominal_length` に等しい。
- `SessionAlignment(tz: "America/New_York", anchors_local: tuple[time, ...])`: 現地時刻の起点（4h は 17:00, 21:00, 01:00, 05:00, 09:00, 13:00。1d は 17:00）から境界を作り、DST を含む `zoneinfo` 規則で UTC へ変換する（上位設計書 §3.2）。固定 UTC 時刻を埋め込まない。**DST 切替日の起点の解決規則**（確定）: 現地時刻が2回現れる日（秋の切り戻し。NY では 01:00 が2度ある）は**最初の出現（`fold=0`）**を起点とし、現地時刻が存在しない日（春の切り替え。NY では 02:00〜02:59 が存在しない）は**次に存在する瞬間**を起点とする。この規則を D02 の `UtcTime.from_local` に `fold` を明示して渡し、曖昧な変換を残さない。**足の長さは固定ではない**: DST 切替日を含む日足は 23 時間または 25 時間、4h 足は 3 時間または 5 時間になる。足の妥当性は「区間の両端が整列規則の計算した境界に一致すること」で検証し、`nominal_length` との一致は要求しない。
- `boundaries(t: UtcTime) -> Interval`: `t` を含む足の**整列上の**区間を整列規則から計算する。`FixedUtcAlignment` は `nominal_length` の整数倍、`SessionAlignment` は現地起点の列から求める。カレンダーは考慮しない。
- `expected_interval(calendar, t: UtcTime) -> Interval | None`: `boundaries(t)` をカレンダーの取引セッションで切り詰めた、**その足が実際に取るべき区間**。区間の末尾が宣言された休場・短縮の開始に掛かる場合は末尾を休場開始で切り詰め、区間全体が休場ならその足は存在しない（`None`）。足の検証と期待足の判定はこちらを使う。
- 初版の定義 ID: `15m`、`1h`、`4h_ny17`、`1d_ny17`（すべて version 1）。`configs/calendars/timeframes_v1.yaml` に置く。

### 3.3 足 `Bar`

| フィールド | 型 | 不変条件・意味 |
|---|---|---|
| `series` | `SeriesId` | — |
| `interval` | `Interval` | `[bar_start, bar_end)`。`timeframe_def.expected_interval(calendar, bar_start) == interval` であること。通常は整列上の区間と一致し、DST 切替日の日足・4h 足は名目より長短する。カレンダーが宣言した短縮セッションでは、期待区間の末尾が休場開始で切り詰められた区間と一致することを要求する（整列上の区間との一致は要求しない） |
| `open` / `high` / `low` / `close` | `Price` | `low <= min(open, close)`、`max(open, close) <= high` |
| `volume` | `Decimal` | `>= 0` |
| `available_at` | `UtcTime` | **`available_at >= interval.end`**（不変条件）。通常は `bar_end` に等しく、遅延シナリオ適用後は後ろへずれる。足の終了前に確定値が見える構成は構築時に拒否する |
| `provenance` | `Provenance` | 出所（`histdata` / `dukascopy` / `aggregated`）と生成元の参照 |

- 足の自然キー `BarKey(series, bar_start)`。連番 ID は持たない。
- 未確定の足は `Bar` として存在しない。執行モデルの始値処理は第7.3節の別イベントで扱う。

### 3.4 取引カレンダー `TradingCalendar`

| フィールド | 型 | 意味 |
|---|---|---|
| `id`、`version` | `str`、`int` | `fx_ny17` v1 |
| `tz` | `ZoneInfo` | `America/New_York` |
| `weekly_open` | `(weekday=Sunday, time=17:00)` | 週の開始（現地） |
| `weekly_close` | `(weekday=Friday, time=17:00)` | 週の終了（現地） |
| `closures` | `tuple[ClosureRule, ...]` | 明示した休場・短縮（現地日付と区間） |
| `openings` | `tuple[OpeningRule, ...]` | 明示した営業例外（v1.3）: 週の休場時間帯のうち開場する現地日付と区間。`closures` と重なる宣言は構築時に拒否する |

- `is_open(t: UtcTime) -> bool`、`sessions(interval) -> tuple[Interval, ...]`、`expected_bar_starts(timeframe_def, interval) -> tuple[UtcTime, ...]`。
- **休場は宣言制**。土日を UTC で一律除外しない。受入れの検査で見つかった「足が存在すべきなのに無い区間」を、人間が「休場（カレンダーへ追加、版を上げる）」か「データ欠損（そのまま欠損として扱う）」に分類する。分類結果は manifest に残す。
- **営業例外も宣言制**（v1.3）。週の休場時間帯（例: 日曜 17:00 前）に足がある区間は「休場帯の足」（`UNEXPECTED_BAR`）として報告され、人間が「カレンダー側の営業例外（`openings` へ追加、版を上げる）」か「セッション外データ異常（足を除外する）」に分類する。`sessions()` は `weekly_open`〜`weekly_close` に `openings` を加え、`closures` を取り除いた区間を返す。

### 3.5 公開予定 `SeriesSchedule`

`SeriesSchedule(series, timeframe_def, calendar_ref, normal_publication_delay: timedelta = 0)`。`normal_publication_delay` は **非負**（負値は構築時に拒否）。

- 期待される足境界はカレンダーと定義から機械的に導く。
- 通常の公開予定 `scheduled_at = bar_end + normal_publication_delay`。初版はすべて 0。
- 「期待される最新の確定足」（上位設計書 §4.3.10）は、判断時刻 `at` に対し `bar_end <= at` かつカレンダー上存在すべき最後の足。

### 3.6 遅延シナリオ `DelayScenario`

| 型 | 内容 |
|---|---|
| `DelayScenario(id, version, rules: tuple[DelayRule, ...])` | 実験設定に置く（部品パラメータではない） |
| `FixedSeriesDelay(series, delay)` | 系列全体に固定遅延 |
| `InjectedBarDelay(series, bar_start, delay)` | 指定足だけ遅延 |
| `SeededRandomDelay(...)` | 将来用。初版は能力検査で拒否 |

- 各 `DelayRule.delay` は**非負**（負値は構築時に拒否）。適用後 `available_at = scheduled_at + delay` であり、`available_at >= bar_end` が常に成り立つ。OHLC と対象区間は変えない。負の遅延で足の終了前に確定値が見える先読みを、設定の誤りからも起こさせない。
- 実現した `available_at` 列は `PublicationLog` として run に保存する（上位設計書 §4.3.13）。

### 3.7 snapshot manifest `SnapshotManifest`

| フィールド | 内容 |
|---|---|
| `snapshot_id` | 第3.7.1節のダイジェスト対象の `canonical.digest`。`created_at`・`access_log`・`approval` は対象外 |
| `created_at` | 受入れ実行時刻（`app` が渡す）。記録のみで識別には使わない |
| `basis_declaration` | 価格基準の宣言のうち識別に関わる部分（`value: BID`、`verified: false`）。検証済み事実ではない |
| `declaration_record` | 宣言の記録（`declared_by`、`declared_at`）。`snapshot_id` の計算対象外。同じ宣言内容なら誰がいつ宣言しても同じ `snapshot_id` になる |
| `sources` | `SourceFile(path, sha256, rows, symbol, timeframe, declared_basis, provenance_counts)` の列 |
| `conversion` | 変換コード版、時刻規約（`explicit_offset_utc`）、集約規則の版、カレンダー版 |
| `series` | `SeriesManifest(series_id, covered_interval, bar_count, partitions)` の列 |
| `partitions` | `PartitionRecord(partition_id, series_id, access_class, interval, bar_count, digest)` |
| `integrity_report_ref` | 最終の検査報告（`integrity_report.json`）のダイジェスト |
| `provisional_report_ref` | 暫定段階の検査報告（人間が分類の根拠にした報告）のダイジェスト（v1.3）。確定段階で 5〜7 を再実行しなかった場合は `integrity_report_ref` と同じ値。異なる場合は暫定報告を `integrity_report_provisional.json` として同ディレクトリに保存し git 管理する（再実行で消えた警告に対する分類の根拠を別クローンでも検証できるようにする） |
| `closure_decisions` | 検査で見つかった警告に対する人間の分類の列（v1.3: `ClassificationDecision` の列。フィールド名は互換のため据え置く）。1 件は `kind`（分類対象の検査種別）、`interval`（区間）、`series`（対象系列の**解決済み**明示集合。確定時に「全系列」を snapshot 内の具体的な `SeriesId` 集合へ解決して保存する）、`outcome`（分類結果。第3.9節の種別ごとの語彙）、`note`（根拠・注記）、`calendar_ref`（参照するカレンダー新版。任意）を持つ。**記入されたままの分類は監査用の記録であり `snapshot_id` の計算対象外**（同じ警告集合を 1 件の広い区間で書いても複数の狭い区間で書いても識別子が変わらないようにするため） |
| `resolved_classifications` | 分類を警告 1 件ごとに解決した列（v1.3）。要素は `(kind, series_id, warning interval, outcome, excluded_bar_count)`。確定時に `closure_decisions` を暫定報告と最終報告の分類対象の警告に適用して導き、`snapshot_id` の計算対象にする。**分類の正規化内容とはこの列を指す** |
| `legacy_access` | 旧基盤から引き継いだ閲覧・使用履歴（`LEGACY_HOLDOUT` の `CONSUMED` 判定の根拠） |
| `access_log` | manifest 内には持たない。独立ファイル `access_log.jsonl`（追記専用、git 管理）に置き、`HoldoutState` はそこから導出する。`snapshot_id` の計算対象外 |
| `approval` | 人間の受入れ承認（承認者・日時・コメント）。`snapshot_id` の計算対象外 |

manifest は `data/snapshots/<snapshot_id>/manifest.json`（git 管理）。検査報告 `integrity_report.json`（確定段階で再実行した場合は暫定報告 `integrity_report_provisional.json` も）と閲覧記録 `access_log.jsonl` も同ディレクトリで git 管理する（v1.3、ADR-0013 改訂）。検査報告は manifest のダイジェスト対象であり承認・読み取り関門が必要とするため、追跡しなければ別クローンで Parquet を復元しても snapshot を検証できない。検査報告には価格や封印期間の統計値を含めず、検査種別・系列・区間・重大度・構造的な詳細だけを保存する（第3.9節）。実体（partition の Parquet）と `_pending/` 配下の暫定成果物は git 管理外のまま。

#### 3.7.1 `SnapshotId` の対象と二段階フロー（確定）

`SnapshotId` の対象: `sources`、`conversion`（カレンダー版を含む）、`basis_declaration`（値と `verified` のみ）、`series`、`partitions`、`integrity_report_ref`、`provisional_report_ref`、`resolved_classifications`、`legacy_access`。対象外: `created_at`、`declaration_record`、`closure_decisions`（記入されたままの分類。v1.3 で対象外に変更）、`access_log`、`approval`。対象はすべて決定論的な内容であり、実行時刻・操作者・承認者は含めない。

**列の正規順序**（確定）: ダイジェスト対象の列は、ファイルシステムの列挙順や検査の実行順に依存しないよう、符号化前に次の鍵で整列する。`sources` は `path`（POSIX 相対パス、コードポイント順）。`series` は `SeriesId` の文字列。`partitions` は `(series_id 文字列, access_class, interval.start)`。`legacy_access` は `(series_id 文字列, interval.start)`。`resolved_classifications` は `(kind, series_id 文字列, interval.start, outcome)`（v1.3）。`closure_decisions` は保存時に `(kind, interval.start, interval.end, series 文字列の整列済み列, outcome)` で整列するが `SnapshotId` には入らない。識別子に入るのは警告 1 件ごとに解決した分類なので、同じ警告集合に同じ結果を与える分類は、区間のまとめ方や「全系列」か明示集合かによらず同じ `snapshot_id` になる。完全性検査の報告（`integrity_report_ref` の対象ファイル）は `CheckResult` を `(series_id 文字列, kind, interval.start, detail の正規化表現)` で整列して符号化する。manifest の保存形式も同じ順序で書く。

受入れは二段階で行う。

1. **暫定段階**: 第4節の 1〜8 を実行し、`closure_decisions` が空の状態で `provisional_id` を計算する。出力は `data/snapshots/_pending/<provisional_id>/` に置く。暫定 snapshot は as-of ビュー・公開フィードから読めない（バックテストの入力にできない）。
2. **確定段階**: 人間が分類対象の警告（第3.9節の明示集合。v1.3）を分類して `closure_decisions` を記入する。分類でカレンダーを変更した場合は版を上げ、**暫定 snapshot の原系列の足を内容照合の上で再利用して** 5〜7 を再実行する（原ファイルは読み直さない。第4節 9）。分類確定後に**最終 `snapshot_id`** を計算し、ディレクトリを `data/snapshots/<snapshot_id>/` へ移す。`approval` はこの後に記入し、識別には影響しない。
3. **承認前は読めない**: `approval` が記入されるまで、最終ディレクトリにある snapshot も as-of ビュー・公開フィード・`SnapshotCatalog` から読めない（`SnapshotNotApproved`、構造エラー）。暫定 snapshot（`_pending/`）は承認の対象にならず、常に読めない。

同じ原ファイル・設定・コード版・分類なら同じ最終 `snapshot_id` になる。分類が異なれば別 snapshot である。

### 3.8 アクセス分類（ADR-0014）

- `AccessClass`: `RESEARCH_HISTORY` / `LEGACY_HOLDOUT` / `QUARANTINED_UNASSIGNED`。
- `HoldoutState`（`LEGACY_HOLDOUT` のみ、ADR-0014 2026-09-20 改訂）: `SEALED` / `CONSUMED`。旧基盤で未観測と確認できた partition だけを `SEALED` とし、使用済みまたは履歴不明なら `CONSUMED`（または `QUARANTINED_UNASSIGNED` へ再分類）。`SEALED → CONSUMED` は不可逆。状態は manifest の追記専用 `access_log` から導出し、直接書き換えるフィールドを持たない。
- `CONSUMED` は holdout としての再選定・最終評価に永久に使用禁止。研究・開発用途では明示的な opt-in がある場合のみ読め（既定は不可）、利用の事実と目的を run manifest に記録し、結果を holdout 成績として扱わない。
- `SEALED` の読み取りは、許可発行 → 消費記録の追記 → origin への push 成功 → データ公開の順で fail-closed に行い、push 成功前にデータを返さない。push の non-fast-forward 拒否を compare-and-set として使い、複数クローン間の重複消費を防ぐ。origin に到達できなければ `SEALED` は読めない（ADR-0014 2026-09-20 追記。gate の実装は D07。本パッケージは `access_log.jsonl` の追記と状態導出、許可 partition 集合の受け取りを担う）。
- `QUARANTINED_UNASSIGNED` は、未観測の確認と別 ADR による再分類（sealed holdout または研究履歴）が行われるまで読めない。既定の経路でも `holdout_gate` でも許可しない。
- 期間境界（初版）: `RESEARCH_HISTORY = [2016-01-01Z, 2024-01-01Z)`、`LEGACY_HOLDOUT = [2024-01-01Z, 2026-01-01Z)`、`QUARANTINED_UNASSIGNED = [2026-01-01Z, ∞)`。
- **足の所属は `bar_end` で決める**（案）。`bar_end` が境界以上の足は後ろの区分に入る。理由: 2023-12-31 22:00Z に始まり 2024-01-01 22:00Z に終わる日足を研究側に入れると、2024 年の情報が研究区分に漏れる。`bar_end` 基準なら研究区分は 2024 年以降の情報を一切含まない。
- 物理分離は raw CSV の移動ではなく、受入れが生成する partition 単位で行う（ADR-0014）。partition ディレクトリ名にアクセス分類を含める（例: `USDJPY_1h_bid/RESEARCH_HISTORY/`）。

### 3.9 完全性検査の結果 `IntegrityReport`

`CheckResult(kind, severity, series, interval, detail)` の列。`kind` の初期語彙:

| kind | 内容 | 既定の severity |
|---|---|---|
| `DUPLICATE_TIMESTAMP` | 同一 `bar_start` の重複行 | ERROR（受入れ失敗） |
| `OHLC_INCONSISTENT` | `low <= open/close <= high` の違反、非正の価格 | ERROR |
| `NAIVE_OR_FOREIGN_TZ` | オフセットなし、または UTC 以外のオフセット | ERROR |
| `IRREGULAR_INTERVAL` | 足の開始が定義の整列に合わない | ERROR |
| `MISSING_EXPECTED_BAR` | カレンダー上存在すべき足の欠落 | WARN（**分類必須**。結果: `CLOSURE`（休場。カレンダーの `closures` へ追加して版を上げる）/ `DATA_GAP`（データ欠損。そのまま欠損として扱う）） |
| `UNEXPECTED_BAR` | カレンダー上休場の時間帯に足がある | WARN（**分類必須**。結果: `CALENDAR_EXCEPTION`（カレンダー側の営業例外。`openings` へ追加して版を上げる）/ `OUT_OF_SESSION_DATA`（セッション外データ異常。第4節 9 の除外規則で足を除外する）） |
| `CROSS_SYMBOL_MISALIGNMENT` | 同じ時間足で銘柄間の足境界がずれる | WARN（記録のみ。分類は要求しない） |
| `SOURCE_TRANSITION` | `source` の切替点 | INFO |
| `ZERO_VOLUME_SPAN` | volume 0 の連続区間 | INFO |
| `DST_BOUNDARY_ANOMALY` | DST 切替週の足数・境界が期待と異なる | WARN（記録のみ。分類は要求しない） |

**分類対象の検査種別**（v1.3、確定）: 人間の分類を必須とするのは `CLASSIFIABLE_KINDS = {MISSING_EXPECTED_BAR, UNEXPECTED_BAR}` の**明示集合**であり、「WARN 全体」ではない。集合の変更は本書の改訂を伴う。他の WARN（銘柄間の境界ずれ、DST 異常）は検査報告に保存し、確定・承認・読み取りの条件にしない。種別ごとに許される分類結果は上表のとおりで、`MISSING_EXPECTED_BAR` に `CALENDAR_EXCEPTION` を、`UNEXPECTED_BAR` に `CLOSURE` / `DATA_GAP` を与える分類は構築時に拒否する（休場帯の足を休場・欠損へ無理に当てはめない）。

`LEGACY_HOLDOUT` と `QUARANTINED_UNASSIGNED` の partition に対する検査結果は、構造情報（件数・区間・種別）だけを含め、価格の統計を含めない。

## 4. 受入れユースケース（`application.acceptance`）（確定）

```text
1. 原ファイルの登録        sha256、行数、銘柄・時間足の推定（ファイル名から）を SourceFile に記録
2. 列対応の適用            configs/datasources/legacy_merged_csv_v1.yaml の宣言で列を対応付ける。
                           時刻の見た目から規約を推測しない（上位設計書 §3.2）
3. 正規化                  時刻: オフセット必須、UTC へ。価格: 文字列から Decimal（float を経由しない）。
                           volume: Decimal。source: provenance
4. 構造検査                DUPLICATE / OHLC / TZ / IRREGULAR。ERROR があれば受入れ失敗
5. カレンダー照合          MISSING_EXPECTED_BAR / UNEXPECTED_BAR / CROSS_SYMBOL / DST を WARN として報告
6. 上位足の生成            1h → 4h_ny17 / 1d_ny17（第5節）。生成元の参照を provenance に残す
7. アクセス分類と partition  bar_end 基準で partition に分け、Parquet に書き出す（adapters）
8. 暫定 manifest 生成      provisional_id を計算し _pending/ に manifest.json を書く（第3.7.1節）
9. 分類と確定              人間が分類対象の警告（第3.9節の明示集合）を分類（closure_decisions）。カレンダー
                           変更なら版を上げて 5〜7 を再実行（暫定 snapshot の実体を内容照合の上で再利用）。
                           分類確定後に最終 snapshot_id を計算してディレクトリを確定し、approval を記入する
```

**分類の形式と確定時の検査**（v1.3、確定）。分類 1 件は「分類対象の検査種別・区間・対象系列（明示集合、または snapshot 内の全系列）・分類結果・根拠と注記・必要なら参照するカレンダー新版」を表し、その区間に**完全に含まれる**同種別の警告（対象系列のもの）をすべて分類したものとみなす。確定時に次を検査し、違反は構造エラーで確定を拒否する。

1. 分類対象の警告がすべて**ちょうど 1 件**の分類に含まれる（未分類なし）。
2. 同じ警告を複数の分類が覆わない（競合する分類は拒否。区間の重なりが警告を共有しなければ許容）。
3. 対応する警告が 1 件もない分類を拒否する（分類は報告された警告に対してのみ行う）。
4. 「全系列」は確定時に snapshot 内の具体的な `SeriesId` 集合へ解決して保存する。保存後の分類は明示集合だけを持つ。
5. 分類の範囲を広げても、報告されていない警告を捏造しない（分類が警告を増やすことはない。除外規則で足が減っても警告は再検査で導く）。
6. `SnapshotId` には分類を警告 1 件ごとに解決した `resolved_classifications`（第3.7節）を含め、記入されたままの `closure_decisions` は含めない。同じ警告集合に対する同じ分類結果は、区間のまとめ方によらず同じ `snapshot_id` になる。

**再実行の入力**（v1.3、確定）。カレンダー新版を伴う分類では、暫定 snapshot の partition から読み戻した原系列の足（出所が `AGGREGATED` でないもの）を再利用し、原ファイルは読み直さない。再利用の前に、検査報告の実ダイジェストが暫定 manifest の `integrity_report_ref` と一致すること、全 partition の系列・件数・区間・内容ダイジェストが暫定 manifest の記録と一致することを検証し、不一致なら何も書かずに失敗する。`sources`・コード版・時刻規約・集約規則の版・`created_at` は引き継ぎ、カレンダーの識別と版だけを置き換える。時間足定義は id と版まで暫定 snapshot の系列と一致しなければ失敗。分類の突き合わせは**暫定報告（人間が分類の根拠にした報告）と最終の再実行後の報告の和集合**に対して行う: 各分類は暫定報告または最終報告のいずれかの分類対象の警告に対応しなければならず（どちらにも対応しない分類は拒否し、失敗時に列挙する）、暫定報告の分類対象の警告と最終報告に残る分類対象の警告はすべて分類済みでなければならない。カレンダーの修正が**新たな警告を生む**ことがある（例: ある系列の休場を追加すると別系列の足が「休場帯の足」になる。営業例外を追加すると別系列に欠落が現れる）。その場合、確定は「再実行後に未分類の警告が残る」として失敗し、新たな警告を表示する。人間はそれらの分類を追記して同じ暫定 snapshot に対して確定を再実行する（**反復的な分類**。暫定 snapshot は失敗時に変更されない）。反復の途中の版（例: カレンダー v2）でだけ現れ、最終の版（v3）で消えた警告に対する分類は「対応なし」として列挙されるので、人間はそれを分類ファイルから削除して再実行する。中間の報告は保存も検証もしない（最終 snapshot の根拠は暫定報告と最終報告の 2 つで閉じる）。確定した snapshot には、暫定報告を `integrity_report_provisional.json`、最終報告を `integrity_report.json` として保存し、両方のダイジェストを manifest に記録する（第3.7節）。読み取り関門は両方の報告を検証し、`closure_decisions` が両報告の和集合の警告に対応すること、`resolved_classifications` が両報告の分類対象の警告と一致することを確かめる。

**セッション外データ異常の除外規則**（v1.3、確定）。`UNEXPECTED_BAR` を `OUT_OF_SESSION_DATA` と分類した区間の足は、確定段階の再実行時に原系列から除外し partition に含めない。**この分類が 1 件でもあれば、カレンダー変更の有無にかかわらず 5〜7 を再実行する**（除外後の原系列に対してカレンダー照合・上位足の生成・partition 分けをやり直す。再実行の入力と検証は上記「再実行の入力」と同じ）。上位足は除外後の原系列から再生成する。除外は WARN 対象の足に限る（ERROR に関与する足は既に受入れ失敗している）。除外した足の件数と区間は分類記録（`note` ではなく構造的な `excluded_bar_count`）に残し、`sources` の行数は原ファイルの行数のまま変えない。除外後に「存在すべき足の欠落」が新たに生じることはない（休場帯の足だから）。

- 受入れは決定論的。同じ原ファイル・設定・コード版・分類なら同じ最終 `snapshot_id` になる（`created_at` は識別に含めない）。
- 受入れ自体は封印区分の価格を読むが、出力（partition 実体）は gate の外に置かれ、報告には価格統計を含めない（第3.9節）。
- 初版は 20 ファイルすべてを受け入れる（保管・受入れ能力を残す、ADR-0015）。縦断実行で使うのは USDJPY だけ。

`configs/datasources/` は D01 §10.1 の一覧にない。列対応の宣言はカレンダーでも銘柄でもないため、D01 の次回改訂で `configs/datasources/` を追加する（サブディレクトリの追加であり依存規則には影響しない）。

## 5. 上位足の生成（`application.aggregation`）

### 5.1 規則（確定）

- 対象: 1h（bid）→ 4h_ny17、1d_ny17。15m からは生成しない（15m は執行系列）。
- 区間は `SessionAlignment` で決める。日足は現地 17:00 → 翌 17:00。4h は現地 17,21,1,5,9,13 起点。
- OHLC: 始値＝最初の構成足の始値、高値＝最大、安値＝最小、終値＝最後の構成足の終値、volume＝合計。
- `bar_start` は**区間の開始**（最初の観測時刻ではない）。旧基盤の「最初の観測時刻ラベル」は採用しない（第13節 C-4）。
- `available_at` = 構成足の `available_at` の最大値（通常は `bar_end`）。

### 5.2 不完全足の扱い（案）

- 区間内にカレンダー上期待される構成足がすべて存在する場合だけ生成する。
- 欠落がある区間は生成せず、集約系列の `MISSING_EXPECTED_BAR` として報告する。下流は通常の欠損規則（`on_missing`）で扱う。
- 「一部の構成足で作った不完全足」を採用する選択肢は初版に設けない（暗黙の部分集計を避ける。上位設計書 §3.2）。
- 短縮セッション（カレンダーが宣言）では、集約足の区間は `expected_interval` で切り詰められた区間とし、その区間内に期待される構成足が揃えば完全とみなす。

### 5.3 旧集約の再現（第13節 C-4）

旧基盤の集約出力は本リポジトリに引き渡されていないため、照合対象が存在しない。再現版を作っても検証できない。**再現版は作らず、`ny17_v2`（本節の規則）を唯一の集約規則とし、C-4 を ADR として記録する**ことを推奨する。将来、旧出力が入手できた場合は別版として追加する。

## 6. as-of ビュー（`application.asof`）（確定）

`MarketDataView`（`strategy.runtime.ports` で定義、D04/D05 で最終化）を実装する。

### 6.1 構築

`AsOfView(snapshot, allowed_partitions: frozenset[PartitionId], schedules, publication_log)`。

- 読める partition は構築時に固定する。範囲外の系列・区間への要求は `HoldoutAccessViolation`（構造エラー。入力欠損ではない）。
- `LEGACY_HOLDOUT` の partition は `evaluation.application.holdout_gate` を通過した場合だけ `allowed_partitions` に入る（D07）。`QUARANTINED_UNASSIGNED` の partition は、別 ADR による再分類が行われるまで**いかなる経路でも** `allowed_partitions` に入らない（gate も許可を発行しない）。
- 構築時に snapshot が承認済み（`approval` あり、最終 `snapshot_id` ディレクトリ）であることを検査し、暫定・未承認の snapshot は `SnapshotNotApproved` で拒否する（第3.7.1節）。

### 6.2 読み取り操作

| 操作 | 意味 |
|---|---|
| `latest_available(series, at) -> Bar \| MissingInput` | `available_at <= at` の足のうち、期待される最新足。期待足が未到着なら `LATEST_BAR_UNAVAILABLE`。古い足へ黙って戻らない |
| `history(series, window, at, *, end_offset_bars=0) -> tuple[Bar, ...] \| MissingInput` | 期待される最新足から `end_offset_bars` 本手前を末尾とし、`BarsWindow.count` 本または `DurationWindow` の範囲。窓内の期待足がすべて存在し有効でなければ `INPUT_MISSING_OR_INVALID`。データ開始前を含めば `WARMUP_INSUFFICIENT` |
| `bar(series, bar_start, at)` | 指定足。`available_at > at` なら不可視 |
| `expected_latest_key(series, at) -> BarKey` | 判断時刻に対し存在すべき最新足の鍵 |
| `freshness(series, bar) -> UtcTime` | 鮮度基準時刻（確定足は `bar_end`） |

- `max_age` の追加検査（`at - bar_end <= max_age`、UTC で休場含む）は `InputReadSpec` を解釈する戦略ランタイムが行い、ビューは鮮度基準時刻を返す（上位設計書 §4.3.10）。
- `DurationWindow`: `bar_end` が `(at' - duration, at']` に入る足（`at'` は末尾足の `bar_end`）。範囲内の期待足がすべて必要。範囲がデータ開始前に及べば `WARMUP_INSUFFICIENT`。全期間休場で空なら `INPUT_MISSING_OR_INVALID`（空履歴からの計算は許さない）。
- `end_offset_bars` は「当該足を除く過去 N 本」を表現するためのもので、宣言側のフィールド名は D04 で決める。
- 未来参照は構造的に不可能: すべての操作が `at` を必須にし、`available_at > at` の足は返さない。

### 6.3 執行系列ビュー

`ExecutionSeries`（`backtest.application.ports`）を同じ snapshot から実装する。戦略ビューとは別インスタンスで、`open_of(bar_key)`、`bar(bar_key)`、`next_bar_key_after(t)` を持つ。open だけを先に公開する段階（第7.3節）を持つため、戦略ビューには渡さない。

## 7. 公開フィード（`application.publication`）

### 7.1 イベント（確定）

run 区間内の全系列について、`available_at` 順に次を生成する。

| イベント | 発生時刻 | 意味 |
|---|---|---|
| `ScheduledBoundary(series, bar_key, bar_end)` | `bar_end` | カレンダー上その足が終了する予定時刻の通知。データ到着とは独立 |
| `Publication(series, bar_key, available_at)` | `available_at`（遅延適用後） | 足のデータが利用可能になった通知 |
| `ExecutionOpen(series, bar_key, open_time)` | `bar_start` | 執行系列の始値が処理可能になる通知。戦略には配送しない |
| `ExecutionBarComplete(series, bar_key)` | `bar_end` | 執行系列の足が終了し、足内約定を解決できる通知 |

- 同時刻の順序: `ExecutionBarComplete` → `Publication`（同じ `available_at` のものをまとめて P0）→ `ScheduledBoundary` → `ExecutionOpen`。上位設計書 §4.7.12 の「前足終了 → 内部約定 → 公開 → 判断 → 次足 open」に対応する。フェーズの正式な列挙は D06。
- 同時刻・同フェーズ内の系列順は `(symbol, timeframe_def.nominal_length 降順, basis)` で固定する。

### 7.2 `OnBarClose` の結び付け（案）

戦略の `OnBarClose` 起動条件は **`ScheduledBoundary` に結び付ける**。データが遅延している場合、評価時に `latest_available` が `LATEST_BAR_UNAVAILABLE` を返し、`on_missing`（SKIP / WAIT / USE_PREVIOUS / ERROR）で扱う。`WAIT_FOR_INPUT` の再開契機は対応する `Publication` の到着。

理由: 上位設計書 §4.3.13「欠損しても到着を待たずに予定時点の検査を起動できるようにする」。`Publication` に結び付けると、遅延した系列の評価が黙って後ろへずれ、SKIP と WAIT の区別が失われる。

### 7.3 執行系列の open 公開（確定）

`ExecutionOpen` は執行モデルだけが消費する。始値だけが処理可能になり、同じ足の high/low/close は `ExecutionBarComplete` まで見えない（上位設計書 §4.7.11）。戦略ビューにはその足は `bar_end` まで存在しない。

## 8. adapters（`adapters`）

| モジュール | 内容 |
|---|---|
| `csv_source.py` | `RawBarSource` の実装。列対応宣言に従い、文字列のまま行を返す（Decimal 化は application） |
| `parquet_store.py` | `SnapshotStore` の実装。partition ごとに Parquet を書き、manifest.json を読み書きする。DataFrame は adapters の外へ出さない |

表形式ライブラリ（B-5）は本書では決めない。推奨は polars（第13節）。

## 9. 設定ファイル

| ファイル | 内容 |
|---|---|
| `configs/calendars/fx_ny17_v1.yaml` | カレンダー（tz、週の開閉、休場） |
| `configs/calendars/timeframes_v1.yaml` | 時間足定義 4 件 |
| `configs/datasources/legacy_merged_csv_v1.yaml` | 列対応、時刻規約、宣言する価格基準、ファイル名パターン |
| `configs/symbols/*.yaml` | 銘柄仕様（D02 §5.2）。初版は 10 ペア |

すべて `schema_version` を持ち、`app.config` が Pydantic で検証して domain 型へ変換する（ADR-0018）。

## 10. CLI（`app.cli`）

暫定 ID フロー（第3.7.1節）に対応する3コマンドとする。CLI ライブラリは B-8（段階2）まで argparse。

| コマンド | 入力 | 出力・効果 |
|---|---|---|
| `odyssey-fx data accept --datasource <yaml> --calendar <yaml> --timeframes <yaml> --symbols <dir> --out data/snapshots/` | 原ファイルと設定 | 第4節 1〜8 を実行し、`data/snapshots/_pending/<provisional_id>/` に暫定 manifest・検査結果・partition を書く。`provisional_id` を表示 |
| `odyssey-fx data classify --pending <provisional_id> --decisions <yaml> --timeframes <yaml> --out data/snapshots/` | 分類対象の警告の分類（第4節 9 の形式）と、必要ならカレンダーの新版 | 暫定 snapshot の実体を内容照合してから `closure_decisions` を記入し、カレンダー変更またはセッション外データ異常の分類があれば暫定 snapshot の原系列の足から 5〜7 を再実行（原ファイルは読み直さない）。再実行が新たな分類対象の警告を生んだ場合は失敗して表示し、人間が分類を追記して再度実行する。最終 `snapshot_id` を計算して `data/snapshots/<snapshot_id>/` へ確定。分類対象の警告が未分類、競合する分類、対応する警告のない分類、内容不一致、既に確定済みのディレクトリがある場合は失敗 |
| `odyssey-fx data approve --snapshot <snapshot_id> --by <name> --comment <text>` | 確定済み snapshot | `approval` と `declaration_record` を記入。暫定 snapshot は承認できない |

承認前の snapshot はどのコマンド・ポートからも読み取り対象にならない（第3.7.1節 3）。

分類ファイルの形式（v1.3、確定。利用者が任意の場所に置き、`app.config` が検証する）:

```yaml
schema_version: 2
calendar: configs/calendars/fx_ny17_v2.yaml   # 分類でカレンダーを変えた場合だけ
decisions:
  - kind: MISSING_EXPECTED_BAR
    interval: {start: "2016-12-26T00:00:00Z", end: "2016-12-27T00:00:00Z"}
    series: all                                # または [USDJPY/1h/bid, USDJPY/15m/bid]
    outcome: CLOSURE
    note: クリスマス翌日の休場
  - kind: UNEXPECTED_BAR
    interval: {start: "2020-03-15T20:00:00Z", end: "2020-03-15T22:00:00Z"}
    series: [EURUSD/1h/bid]
    outcome: OUT_OF_SESSION_DATA
    note: 出所の切替に伴う早期配信
```


## 11. テスト（確定）

| 種別 | 内容 |
|---|---|
| 単体 | `Bar` の不変条件（短縮セッションで切り詰められた区間が受理され、整列上の区間のままの足が拒否されること。`available_at < bar_end` が拒否されること）、負の遅延の拒否、DST 切替日の起点解決（秋は最初の出現、春は次に存在する瞬間）、`SessionAlignment` の境界計算（DST 切替週の 4h/1d 境界を UTC で固定値と照合し、切替日の日足が 23h/25h、4h 足が 3h/5h になること）、カレンダーの週開閉、`DurationWindow` の端点、partition 所属（`bar_end` 基準）、`SnapshotId` が `created_at` に依存しないこと、分類の差で `snapshot_id` が変わること、分類の突き合わせ（未分類・競合・対応なしの拒否、「全系列」の解決、明示集合と「全系列」で同じ `snapshot_id`）、種別と結果の組合せの拒否、営業例外の `sessions()` への反映、セッション外データ異常の除外 |
| 意味論 | 未確定の上位足を参照できない、遅延注入で `Publication` だけが動き OHLC が変わらない、期待足未到着で古い足へ戻らない、範囲外 partition で `HoldoutAccessViolation` |
| プロパティ | 将来の足を追加しても `at` 以前の as-of 結果が変わらない、受入れの決定論性（同じ入力で同じ `snapshot_id`。原ファイルの列挙順・検査の実行順を入れ替えても同じ `snapshot_id`）、集約の OHLC が構成足の集計と一致 |
| golden | 人工 1h 系列から生成した 4h/1d の固定出力 |
| 意味論（確定段階） | 暫定 partition の価格を書き換えた後の分類が内容不一致で拒否される、検査報告を差し替えた後の分類が拒否される、カレンダー新版で説明された警告の分類が「対応なし」として拒否されない |
| 実データ | 段階1の完了条件として実データ受入れを1回実行し、manifest と検査結果を人間が確認する |

## 12. 段階1の完了条件との対応

全体計画書 §8.2「未確定の上位足を参照できない。DST・欠損・再読込・遅延注入の検証が通る。実データ受入れで manifest が生成される」に、第11節のテストと第4節の実行を対応させる。

## 13. 承認時の確認事項（2026-09-20 承認: 全項目で推奨を採用。項目1は ADR-0024、項目6は ADR-0025 に記録）

| # | 事項 | 推奨 | 代替 |
|---|---|---|---|
| 1（C-4） | 旧集約の再現版 | 作らない。`ny17_v2` を唯一の規則とし ADR 化 | 旧出力入手後に別版として追加 |
| 2 | partition 所属の基準 | `bar_end`（研究区分に後続区分の情報を含めない） | `bar_start` |
| 3 | 不完全な集約足 | 生成せず欠損として報告 | フラグ付きで保存しビューで除外 |
| 4 | `OnBarClose` の結び付け | `ScheduledBoundary`（予定時点で起動、欠損は `on_missing`） | `Publication`（到着で起動） |
| 5 | 同時刻の系列順 | `(symbol, nominal_length 降順, basis)` | 設定で指定 |
| 6（B-5） | adapters の表形式ライブラリ | polars | pandas |
| 7 | `configs/datasources/` の追加（D01 §10.1 の改訂） | 追加 | `calendars/` に同居 |
| 8 | 受入れ対象 | 20 ファイルすべて | USDJPY のみ |
| 9 | 期間境界の年 | 2024-01-01Z / 2026-01-01Z（ADR-0014 の暦年） | NY 17:00 基準の年境界 |
