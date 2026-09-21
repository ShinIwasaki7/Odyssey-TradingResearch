# D07: 単一実行の評価境界設計（`odyssey_fx.evaluation`: domain.metrics / domain.status / application.evaluate_run / adapters）

作成日: 2026-09-21
状態: **承認待ち**。v0.1（2026-09-21）: 段階2（検証戦略 A の単一 run）の結果を、再現可能に・数値で・swap 未計上と明記して出すために必要な**境界**だけを決める。指標を将来まで書き切ることは目的にしない（全体計画 §6 D-2「D05 と D07 の将来機能をすべて書き切る必要はない」）。ADR-0016 条件2 のうち「D07 の単一実行評価境界」を本書で充足する。要決定は第16節に Q1〜Q6 として一覧する。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.7.12・§4.7.13 C/E・§4.7.14・§4.7.15・§5.3・§6、[全体計画書](fx_research_platform_overall_plan.md) §5.5・§5.5.1・§5.5.2・§6 C-5・§6 D-2・§7.5・§8.1・§8.2、[D01](D01_architecture_and_dependency_rules.md) §3.2・§4・§7.2・§10.3、[D02](D02_common_kernel.md) §4・§7.1・§8.1・§8.3・§9、[D03](D03_marketdata_and_time.md) §3.9・§6.1、[D05](D05_strategy_runtime.md) §3・§7.2、[D06](D06_backtest_vertical_slice.md) §9（全項）・§10.3・§14、[T01](../traces/T01_paper_trace.md)、ADR-0006（決定論的 ID）、ADR-0012（Decimal / float 境界）、ADR-0016（実装開始条件）、ADR-0027（成果物は Parquet 表＋JSON マニフェスト）、ADR-0029（swap 未計上）、ADR-0030（足内競合解決契約）
対応段階: 段階2で最小実装、段階4で拡張（v0.2）。

## 0. 本書の位置付けと凡例

バックテストが残した**判断履歴（trace）と実行条件（run manifest）を読み、単一 run の数値の結果を作る**手順と型を決める。バックテストの意味論には立ち入らず（全体計画 §5.5）、trace を作り直したり、結果を都合よく修正したりしない【合意済み】上位 §5.3。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D06、T01）で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択する事項。第16節に Q1〜Q6 として一覧する |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | `domain.metrics`（指標の値と取引の記録）、`domain.status`（評価の状態と整合検査）、`application.evaluate_run`（trace → 指標・集計・診断・状態）、`application.manifest`（評価 manifest）、`adapters.fs_store`（Parquet ＋ JSON） | 【合意済み】D01 §1・全体計画 §5.5.1 |
| 依存できるもの | Python 標準ライブラリ、`odyssey_fx.common`、`marketdata.domain`、`backtest` の `domain` と `trace`（`engine` / `admission` / `execution` / `portfolio` / `application` は不可）、`strategy` の `declarations` / `records` / `compiler`。実行と保存はポート経由 | 【合意済み】D01 §3.2（契約 F4・F7・F8）・§4 |
| 決めないもの | 注文・約定・台帳の意味論（D06）、部品の計算規則（D05）、探索・分割・選定（D09）、実験 manifest と研究ポリシーの内容（本書 v0.2） | 【合意済み】 |
| 外部ライブラリ | 表形式ライブラリ（polars 等）は `evaluation.adapters` だけ。`domain` と `application` は標準ライブラリだけで書く | 【合意済み】D01 §5・ADR-0025 |

型はすべて `@dataclass(frozen=True, slots=True)`、コレクションは `tuple` か凍結 `Mapping`、区分タグ付き union は `kind` フィールドを持つ dataclass の `Union` とする【合意済み】D01 §8・ADR-0011。

### 1.1 語彙と規則の正本がどの文書にあるか【提案】

D04・D05・D06 と同じ方針を引き継ぐ。同じ語彙を2か所に定義しない。

| 事項 | 正本 | 本書の扱い |
|---|---|---|
| trace の15表・主キー・外部キー | D06 §9.2 | 参照のみ。本書は**読む表と列**を選ぶだけで、表を足さない（第4.2節） |
| 保存形式と平坦化の規則 | D06 §9.1 | 参照のみ。本書の成果物も同じ規則で保存する（第8.2節）。足りない規則は改訂依頼（第15節） |
| `BacktestResult` と `RunManifest` の項目 | D06 §9.3・§9.4 | 参照のみ。再定義しない（第4.1節） |
| 末尾の3集計（確定損益・含み込み資産・仮決済） | 上位 §4.7.13 E（定義）、D06 §10.3（`FinalSummaries`） | 参照のみ。本書は**採用指標と参考値の区別**だけを足す（第5.3節。D06 §14 の引き渡し） |
| 理由コードの語彙 | 上位 §4.7.14（実装側の列挙は D02 §8.1 がその写し） | 参照のみ。集計の鍵として使い、語を足さない（第6節） |
| 評価見送りの診断コード | D02 §8.3（`MissingInputReason`） | 参照のみ。集計の鍵として使う（第6節） |
| 取引機会の終端理由 | 上位 §4.5（状態と遷移は D05 §7 が正本） | 参照のみ。集計の鍵として使う（第6節） |
| 正規化エンコードとダイジェスト | D02 §9.3 | 参照のみ。結果のダイジェスト（第9.2節）に使う |
| 金額・価格・数量の型と丸めの機構 | D02 §4 | 参照のみ。**指標ごとの丸めの有無**だけ本書が決める（第5.1節） |
| 指標の一覧・式・単位・欠損時の扱い | **本書 §5** | 全体計画 §5.5.1 と §7.5 が本書へ委ねた |
| 評価の状態区分と失敗の表し方 | **本書 §10** | 全体計画 §5.5.1（`domain.status`）が本書へ委ねた |
| swap 未計上を出力に載せる場所 | **本書 §7.2** | ADR-0029 と D06 §14 が本書へ委ねた |

### 1.2 本書が決めること・後続に委ねること・D06 から受け取ること【提案】（**レビュー対象範囲の正本**）

**本書のレビューの対象範囲は下表の第3列である**。第4列に属する指摘は「対象外（担当へ）」として記録し、本書では直さない。ただし D04 §1.2・D05 §1.2・D06 §1.2 と同じ例外を置く。**本書の文が第4列の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。第4列の内容を本書に書き足すことはしない。

| # | 領域 | 本書 v0.1 が決めること（対象内） | 後続が決めること（対象外・担当） |
|---|---|---|---|
| 1 | 入力契約 | 入力を3つ（`BacktestResult` / `RunManifest` / trace の9表）に限ること、読む列と用途、読まない6表、市場データを読むかどうか（Q1）、読み出しのポートの操作（第4節） | snapshot 参照経由の市場データ読込と holdout の許可（**本書 v0.2・段階4**）、遅延シナリオ4ケースの比較（**段階3・D08**） |
| 2 | 指標 | 段階2の最小集合14件の式・入力列・単位・丸め・欠損時の扱い・T01 での検算値（第5節） | 年率化・リスク調整指標・分布指標（**本書 v0.2・段階4**）、複数 run の集約と選定（**段階5・D09**） |
| 3 | 集計と診断 | 終端理由別・拒否理由別・診断理由別など7種の集計、約定ずれと2つの経過時間の診断（第6節） | 遅延シナリオ別の比較（**段階3・D08**）、人間向けレポートの文面と体裁（**本書 v0.2・段階4**） |
| 4 | 通貨と費用 | 口座通貨で統一すること、価格反映済み費用を二重計上しない区別、swap 未計上を出力に載せる場所（第7節） | swap を計上すること自体（**ADR-0029 の改訂を要する別決定**）、証拠金・レバレッジに基づく指標（**D10**） |
| 5 | 結果の型と保存 | 出力4表と評価 manifest の項目、保存先と識別（Q4）、行の整列鍵（第8節） | 実験 manifest の項目（**本書 v0.2・段階4**）、探索履歴と holdout 閲覧履歴（**段階5・D09**） |
| 6 | 再現性 | 決定論の条件、結果ダイジェスト、評価時のコードのダイジェスト（Q5）（第9節） | 別プロセスでの再現手順の検証（**段階4の完了条件・D08**） |
| 7 | 失敗と0取引 | 評価の状態4値、整合検査8件と致命/警告の別、「値なし」を型で表すこと、失敗 run の扱い（Q6）（第10節） | 探索の中断（`ABORTED`）の扱い（**段階5・D09**）、研究ポリシーの内容と複雑性の計測（**本書 v0.2・段階4**） |

前提として **D06・D05・D02 から受け取るもの**は次のとおりで、本書はこれらを再定義しない。

| 出どころ | 受け取るもの |
|---|---|
| D06 §9.4 | `BacktestResult` の9項目（`status` / `trace_tables` / `summaries` / `swap_modeled` / `unresolved_intrabar_count` / `capability_report` / `trade_count` / `opportunity_count` / `manifest_ref`） |
| D06 §9.3 | `RunManifest` の項目（特に `account`（`AccountSpec` の3項目）、`run_interval`、各 `PolicyRef`、`RunStatus`、`DataCapabilityReport`） |
| D06 §9.2 | trace 15表の `TraceTable` 名・主キー・外部キー |
| D06 §9.1 | 保存形式（Parquet ＋ JSON）と平坦化の規則（`ProcessingPoint` は3列、`Reason` は2列、`Money` は2列、`Decimal` は文字列） |
| D06 §10.3 | `FinalSummaries` の3項目（`realized` / `equity_with_mtm` / `hypothetical_closed`）と `cost_breakdown` |
| D05 §3 | `EvaluationRecord` / `EvaluationOutcome` / `OpportunityTransition` のフィールド（表2・表3 の列の正本） |
| D02 §8.1・§8.3 | 理由コードの語彙と評価見送りの診断コードの語彙（集計の鍵） |

## 2. モジュール構成【提案】

D01 §7.2 の一覧のうち、段階2で作るものと後続で作るものを分ける。モジュールの追加・分割はしない。

| サブパッケージ | 段階2で作る | 段階4以降で作る |
|---|---|---|
| `domain` | `metrics.py`、`status.py` | `research_policy.py`（段階4）、`experiment.py`（段階4）、`search.py`・`splits.py`（段階5） |
| `application` | `evaluate_run.py`、`manifest.py`、`ports.py` | `run_experiment.py`（段階5）、`holdout_gate.py`（段階4） |
| `adapters` | `fs_store.py` | `report.py`（段階4） |

`manifest.py` を段階2から作るのは、単一 run の評価 manifest（第8.3節）が再現性の条件そのものであり、実験 manifest（段階4）を足すときに置き場所を動かさないためである。

## 3. 本書が定義する型の一覧【提案】

本文に出る型名を、区分（enum / `kind` タグ付き union / レコード / `Protocol`）とフィールドまで一覧する。**この表にない型名を本文で使わない**。節を足すときは必ずこの表も更新する。D02・D03・D05・D06 が定義した型はそのまま使い、ここには載せない。

| 型 | 置き場所 | 区分 | フィールド / 値 | 詳細 |
|---|---|---|---|---|
| `MetricId` | `domain.metrics` | enum | 第5.2節の14件 | §5.2 |
| `MetricKind` | `domain.metrics` | enum | `AMOUNT` / `RATIO` / `COUNT` / `DURATION` / `PRICE_OFFSET` | §5.1 |
| `MetricValue` | `domain.metrics` | union | `AmountValue(kind, amount: Money)` / `RatioValue(kind, ratio: Decimal)` / `CountValue(kind, count: int)` / `DurationValue(kind, duration: timedelta)` / `PriceOffsetValue(kind, offset: PriceOffset)` / `Unavailable(kind, reason: MetricUnavailableReason)` | §5.1 |
| `MetricUnavailableReason` | `domain.metrics` | enum | `NO_TRADES` / `NO_OBSERVATIONS` / `UNDEFINED_DENOMINATOR` / `INPUT_NOT_AVAILABLE` | §5.1・§10.3 |
| `MetricCaveat` | `domain.metrics` | enum | `SWAP_NOT_MODELED` / `PRICE_EMBEDDED_COST` / `OPEN_POSITION_EXCLUDED` / `ENTRY_COST_EXCLUDED` / `UNRESOLVED_INTRABAR_PRESENT` | §7.2 |
| `MetricRecord` | `domain.metrics` | レコード | `metric_id: MetricId` / `value: MetricValue` / `caveats: tuple[MetricCaveat, ...]` / `observation_count: int` / `inputs: tuple[TraceTable, ...]` | §5.1・§8.1 |
| `TradeOutcome` | `domain.metrics` | enum | `WIN` / `LOSS` / `BREAK_EVEN` | §5.2 |
| `TradeRecord` | `domain.metrics` | レコード | `trade_seq: int` / `position_id: PositionId` / `opportunity_id: OpportunityId` / `symbol: Symbol` / `side: OrderSide` / `quantity: Quantity` / `entry_fill_id: FillId` / `entry_price: Price` / `entry_at: ProcessingPoint` / `close_fill_id: FillId` / `exit_price: Price` / `exit_at: ProcessingPoint` / `close_cause: CloseCause` / `realized: Money` / `holding: timedelta` / `outcome: TradeOutcome` | §5.2・§8.1 |
| `FillDiagnostic` | `domain.metrics` | レコード | `fill_id: FillId` / `order_id: OrderId` / `position_id: PositionId` / `adverse_fill_offset: PriceOffset \| None` / `reference_to_fill: timedelta \| None` / `acceptance_to_fill: timedelta` / `reference_observed_at: UtcTime \| None` / `accepted_at: UtcTime` / `filled_at: UtcTime` | §6.2・§8.1 |
| `CategoryKind` | `domain.metrics` | enum | 第6.1節の7件 | §6.1 |
| `CategoryCount` | `domain.metrics` | レコード | `category: CategoryKind` / `key: str` / `count: int` | §6.1・§8.1 |
| `EvaluationStatus` | `domain.status` | enum | `COMPLETED` / `REJECTED` / `FAILED` / `ABORTED`（`ABORTED` は段階5でだけ使う） | §10.1 |
| `CheckLevel` | `domain.status` | enum | `FATAL` / `WARNING` | §10.2 |
| `ConsistencyCheckResult` | `domain.status` | レコード | `check: str` / `level: CheckLevel` / `passed: bool` / `table: TraceTable \| None` / `expected: str` / `observed: str`（`expected` / `observed` は D02 §9.3 の正規化エンコード文字列） | §10.2 |
| `ColumnValueKind` | `application.ports` | enum | `STRING` / `DECIMAL` / `INT` / `TIME` / `ENUM` / `LIST_STRING` | §4.3 |
| `TraceColumnSpec` | `application.ports` | レコード | `table: TraceTable` / `column: str` / `value_kind: ColumnValueKind` / `required: bool` | §4.2・§4.3 |
| `EvaluationTable` | `application.manifest` | enum | `METRICS` / `CATEGORY_COUNTS` / `TRADES` / `FILL_DIAGNOSTICS` | §8.1 |
| `RunEvaluationId` | `application.manifest` | レコード | `digest: ContentDigest`（`digest(run_id, metric_set_version, evaluation_code_digest)`）。D02 §7.1 の `EvaluationId`（部品の1回の評価）と**別の型**であり、名前も混同しない | §8.3・§9.2 |
| `EvaluationManifest` | `application.manifest` | レコード | `run_evaluation_id: RunEvaluationId` / `run_id: RunId` / `run_manifest_ref: ContentDigest` / `metric_set_version: int` / `evaluation_code_digest: CodeDigest` / `run_code_digest: CodeDigest` / `account_currency: CurrencyCode` / `swap_modeled: bool` / `status: EvaluationStatus` / `result_digest: ContentDigest` / `input_tables: tuple[TraceTable, ...]` / `fatal_failure_count: int` / `warning_failure_count: int` | §8.3・§9.2 |
| `EvaluationReport` | `application.evaluate_run` | レコード | `manifest: EvaluationManifest` / `status: EvaluationStatus` / `metrics: tuple[MetricRecord, ...]` / `categories: tuple[CategoryCount, ...]` / `trades: tuple[TradeRecord, ...]` / `fill_diagnostics: tuple[FillDiagnostic, ...]` / `checks: tuple[ConsistencyCheckResult, ...]` | §8.1・§10.1 |
| `ResultRepository` | `application.ports` | Protocol | `read_manifest(run_id: RunId) -> RunManifest` / `read_table(run_id: RunId, table: TraceTable, columns: tuple[TraceColumnSpec, ...]) -> tuple[tuple[str \| None, ...], ...]` / `write_evaluation(report: EvaluationReport, rows: Mapping[EvaluationTable, tuple[object, ...]]) -> None` | §4.3・§8.2 |
| `EvaluateRun` | `application.evaluate_run` | Protocol | `evaluate(result: BacktestResult, repository: ResultRepository, metric_set_version: int) -> EvaluationReport` | §4.1 |

`Money` / `Price` / `PriceOffset` / `Quantity` / `Decimal` / `UtcTime` / `Interval` / `ProcessingPoint` / `CurrencyCode` / `ContentDigest` / `CodeDigest` と各 ID 型は D02、`Symbol` は D02 §5.1、`OrderSide` / `CloseCause` / `TraceTable` / `BacktestResult` / `RunManifest` / `FinalSummaries` / `RunStatus` は D06、`OpportunityId` の意味は D05 が正本である。`ResultRepository` は D01 §4 が「結果の読み書き」として所在と実装者を既に確定しており、本書はその操作だけを具体化する。**表の読み出しに新しいポートを足さない**。

## 4. 評価の入力契約

### 4.1 入力は3つだけ【提案】＋【要決定】（Q1）

`EvaluateRun.evaluate` の入力は次の3つに限る。

| # | 入力 | 渡し方 | 正本 |
|---|---|---|---|
| 1 | `BacktestResult` | 引数（`BacktestRunner` ポートの戻り値、または `ResultRepository` が読んだ値） | D06 §9.4 |
| 2 | `RunManifest` | `ResultRepository.read_manifest(run_id)` | D06 §9.3 |
| 3 | trace の9表 | `ResultRepository.read_table(...)`。読む表と列は第4.2節 | D06 §9.2 |

- **評価は run を実行し直さない**【合意済み】全体計画 §5.5。単一 run の実行は `BacktestRunner` ポート（D01 §4）の仕事であり、本書の `evaluate` は実行済みの結果だけを受け取る。
- **評価は trace を書き換えない**【合意済み】全体計画 §5.4.5「評価側はこれを改変しない」。読み出しだけを行い、書き出しは第8節の4表と JSON に限る。
- **評価は生の市場データを読み直さない**【提案】（Q1、推奨案）。段階2の指標14件はすべて trace と manifest から作れる（第5.2節の各行の「入力列」）。市場データを読む経路を作ると、as-of の規則（D03 §6）とアクセス分類の許可（D03 §6.1 の `allowed_partitions`）を評価側にも置くことになり、D03 が正本である規則が2か所に割れる。
- 入力の1と2の整合（`BacktestResult.run_id` と `RunManifest.run_id` が一致すること）は第10.2節の致命検査 C2 で確かめる。

### 4.2 読む表と列【提案】

段階2で読むのは15表のうち**9表**である。各列は D06 §9.1 の平坦化規則で得られる名前を書く。**† を付けた列は、区分タグ付き union・入れ子レコード・複合表の列名の規則が D06 §9.1 に無いため、第15節の改訂依頼3 の採択を前提とする**。

| 表 | `TraceTable` | 読む列 | 何に使うか |
|---|---|---|---|
| 2 | `EVALUATIONS` | `evaluation_id`、`outcome_kind`†、`outcome_diagnoses`†、`outcome_reason_code`† | 評価の結果区分別・評価見送りの診断理由別の集計（第6.1節） |
| 3 | `OPPORTUNITY_TRANSITIONS` | `opportunity_id`、`to_state`、`reason_code` | 取引機会の終端理由別の集計（第6.1節） |
| 4 | `ORDER_REQUESTS` | `attempt_id`、`payload_kind`†、`payload_opportunity_id`†、`payload_position_id`† | 拒否をエントリーと決済に分ける。取引と取引機会を結ぶ（第5.2節の `TradeRecord.opportunity_id`） |
| 5 | `ATTEMPT_DECISIONS` | `attempt_id`、`decision_kind`†、`order_id`†、`reason_code`† | 発注試行の拒否理由別の集計（第6.1節） |
| 7 | `ORDERS` | `order_id`、`attempt_id`、`accepted_at_time`、`terms_kind`†、`terms_cause`†、`terms_position_id`†、`terms_reference_quote_price`†、`terms_reference_quote_observed_at`† | 約定ずれと2つの経過時間の診断（第6.2節）、決済契機別の集計 |
| 9 | `FILLS` | `fill_id`、`order_id`、`position_id`、`processed_at_time`、`price`、`quantity` | 約定時刻、約定価格、取引の突合（第5.2節・第6.2節） |
| 11 | `POSITIONS` | `position_id`、`symbol`、`side`、`quantity`、`entry_price`、`entry_fill_id`、`opened_at_time`、`status`、`close_fill_id`、`realized_amount`†、`realized_currency`† | 完了取引の一覧、損益、勝敗、保有時間（第5.2節） |
| 13 | `INTRABAR_RESOLUTIONS` | `fill_id`、`position_id`、`method` | 足内競合の解決方法別の集計（第6.1節） |
| 14 | `LEDGER_SNAPSHOTS` | `at_time`、`at_phase`、`at_sequence`、`balance_amount`、`balance_currency`、`equity_amount`、`equity_currency` | 資産推移と最大ドローダウン（第5.2節） |

**読まない6表**を明示する【提案】。表1 `OUTPUTS`・表6 `RISK_ASSESSMENTS`・表8 `ORDER_EVENTS`・表10 `RESERVATIONS`・表12 `MANAGEMENT_APPLICATIONS`・表15 `EVIDENCE` は段階2では開かない。段階2の指標と集計に必要な列が無く、開くと入力契約が広がって「どの表が変わると指標が変わるか」が追えなくなるためである。必要になった時点で本表に足す（例: 予約額と実リスクの差を指標にするなら表6と表10）。

**表9 の費用の列を読まない**【提案】。D06 §9.1 は `FillRecord.costs`（`CostEntry` の `tuple`）を正規化エンコード文字列の `list` 列として保存すると定めており、D02 §9.3 は符号化だけを定義して復号を定義していない。したがって**約定1件ごとの費用区分別の金額は、現在の保存形式からは復元できない**。段階2は run 単位の費用集計を `BacktestResult.summaries.cost_breakdown`（D06 §10.3、型付きの `Mapping[CostKind, Money]`）から取ることで成立させ、取引単位の費用は第15節の改訂依頼1 の後に扱う（本書 v0.2）。

### 4.3 読み出しの形【提案】

- `read_table` は**要求した列だけ**を、`TraceColumnSpec` の順に、**文字列（または `None`）の行の `tuple`** で返す。値の解釈（`Decimal` 化、`UtcTime` 化、enum 化）は `evaluation.application` が `ColumnValueKind` に従って行う。Parquet を開くのは `evaluation.adapters.fs_store` だけであり、`domain` と `application` に表形式ライブラリを入れない【合意済み】D01 §5・ADR-0025。
- `required=True` の列が表に無い場合は、その場で例外にせず**致命の整合検査の不合格**として記録する（第10.2節の C1）。評価は「なぜ評価できなかったか」を残すことが仕事であり、読み出し時に落ちると理由が残らない。
- 行の順序は Parquet の格納順に依存させない。集計の前に必ず第8.1節の整列鍵で並べ替える（第9.1節の決定論の条件1）。
- **不採用**: 表ごとの行の型（9個の dataclass）を本書で定義して `read_table` がそれを返す案（D06 の型を評価側で写し取ることになり、列が増えるたびに2か所を直す。第1.1節の二重定義の禁止に反する）、`Mapping[str, object]` の行を返す案（キーの打ち間違いを型で防げず、要求していない列が混ざる）。

### 4.4 入力の前提【合意済み】

- 入力の run が**正常完走していない**場合（`BacktestResult.status != COMPLETED`）は、採用評価に混ぜない【合意済み】上位 §4.7.13 C。扱いは第10.1節（Q6）。
- `RunManifest` の `DataCapabilityReport` は要約に畳まずそのまま保存されている【合意済み】D06 §10.5。本書は**再検査しない**。実行可否の判断は D06 の責務であり、評価がもう一度判定すると同じ規則が2か所に割れる。

## 5. 指標

### 5.1 指標の値の型と丸め【提案】＋【要決定】（Q2）

- **「値なし」を 0 や成功値へ置換しない**【合意済み】全体計画 §5.5.1・上位 §5.3。値が無い指標は `Unavailable(kind, reason)` として型で表し、`MetricRecord` の行は必ず残す（行ごと落とすと「計算できなかった」と「集計し忘れ」を区別できない）。
- 各 `MetricRecord` は `observation_count`（その指標が何件の観測から作られたか）を持つ【提案】。0件から作られた比率と、1件から作られた比率を、値だけで区別できないためである（T01 の勝率は1取引から作った 1 であり、多数の取引から作った 1 とは重みが違う）。
- `inputs` にはその指標が読んだ表を入れる【提案】。第4.2節の表と対応し、trace の表が変わったときに影響する指標を機械的に引ける。
- **丸め**【要決定】（Q2、推奨案）: **金額（`AMOUNT`）と価格差（`PRICE_OFFSET`）は丸めない**。trace の `Decimal` を加減算するだけであり、D02 §4.1 のカーネル精度（28桁）で厳密に求まる。**比率（`RATIO`）は除算を1回だけカーネル精度で行い、その結果を丸めずに保存する**。表示用の桁は報告の関心であり、保存値に桁を決め打ちすると、同じ trace から出した値が桁の変更で変わる。`COUNT` は整数、`DURATION` は `timedelta`（マイクロ秒精度）とする。

### 5.2 段階2の最小集合（14件）【提案】

`d` は方向（買いなら `+1`、売りなら `-1`）、`Σ` は完了した取引（`POSITIONS` の `status=CLOSED` の行）についての合計を表す。**T01 検算**の列は、承認済みの紙上トレース [T01](../traces/T01_paper_trace.md) の経路1（正常エントリー → 利確）と第9節（run 末尾の残存処理）の数値から求めた値である。

| # | `MetricId` | 種別 | 式 | 入力 | 欠損時 | T01 検算 |
|---|---|---|---|---|---|---|
| 1 | `NET_PROFIT` | AMOUNT | 最後の `LEDGER_SNAPSHOTS` の `balance` − `RunManifest.account.initial_balance` | 表14、manifest | 表14 が空なら `Unavailable(NO_OBSERVATIONS)` | `1,036,706 − 1,000,000 = 36,706 JPY`（T01 §9.3 の `realized` と一致） |
| 2 | `CLOSED_TRADE_PROFIT` | AMOUNT | `Σ realized` | 表11 | 0取引なら `Unavailable(NO_TRADES)` | `36,768 JPY`（P1 のみ。P2 は未決済） |
| 3 | `TRADE_COUNT` | COUNT | 完了取引の件数 | 表11 | なし（0 は正しい値） | `1` |
| 4 | `WIN_RATE` | RATIO | 勝ち取引数 ÷ 完了取引数。勝敗は `realized > 0` を `WIN`、`< 0` を `LOSS`、`= 0` を `BREAK_EVEN` とし、`BREAK_EVEN` は勝ちに数えず分母には数える | 表11 | 0取引なら `Unavailable(NO_TRADES)` | `1 ÷ 1 = 1` |
| 5 | `MAX_DRAWDOWN_MTM` | AMOUNT | `at` の昇順に `equity` を走査し、`max(これまでの最大 equity − 現在の equity)` | 表14 | 表14 が空なら `Unavailable(NO_OBSERVATIONS)` | 第5.4節（改訂依頼2 に依存） |
| 6 | `MAX_DRAWDOWN_MTM_RATE` | RATIO | #5 を、その最大値が出た時点の「これまでの最大 equity」で割る | 表14 | #5 が値なし、または分母が 0 なら `Unavailable(UNDEFINED_DENOMINATOR)` | 第5.4節（同上） |
| 7 | `MAX_DRAWDOWN_BALANCE` | AMOUNT | #5 と同じ手順を `balance` 列に適用する（参考値） | 表14 | 同上 | `1,000,000 − 999,968 = 32 JPY` |
| 8 | `MAX_DRAWDOWN_BALANCE_RATE` | RATIO | #7 ÷ その時点の最大 `balance` | 表14 | 同上 | `32 ÷ 1,000,000 = 0.000032` |
| 9 | `EXPOSURE_RATE` | RATIO | `Σ 保有時間 ÷ RunManifest.run_interval の長さ`。保有時間は入場約定の `processed_at` から決済約定の `processed_at` まで。未決済建玉は run 末尾までを数える | 表9、表11、manifest | 建玉が1件も無ければ `RatioValue(0)`（保有時間0は観測された事実であり値なしではない） | `8,100 秒 ÷ 1,036,800 秒 = 0.0078125`（P1 の 09:00Z→11:15Z、run 区間は12日） |
| 10 | `COST_CHARGED_TOTAL` | AMOUNT | `summaries.cost_breakdown[COMMISSION]` | `BacktestResult` | `summaries` が `None` なら `Unavailable(INPUT_NOT_AVAILABLE)` | `94 JPY`（P1 入場32 ＋ P1 決済32 ＋ P2 入場30） |
| 11 | `COST_PRICE_EMBEDDED_TOTAL` | AMOUNT | `cost_breakdown[SLIPPAGE_IN_PRICE] + cost_breakdown[SPREAD_IN_PRICE]`（参考値。balance から控除しない） | `BacktestResult` | 同上 | `940 + 1,240 = 2,180 JPY` |
| 12 | `MAX_ADVERSE_FILL_OFFSET` | PRICE_OFFSET | エントリー約定ごとの `max(0, d × (約定価格 − 参照価格))` の最大値 | 表7、表9 | エントリー約定が無ければ `Unavailable(NO_OBSERVATIONS)` | `+1 × (150.080 − 150.060) = 0.020` |
| 13 | `END_EQUITY_MTM` | AMOUNT | `summaries.equity_with_mtm`（参考値） | `BacktestResult` | `summaries` が `None` なら `Unavailable(INPUT_NOT_AVAILABLE)` | `1,051,706 JPY` |
| 14 | `HYPOTHETICAL_CLOSED_PROFIT` | AMOUNT | `summaries.hypothetical_closed`（参考値） | `BacktestResult` | 同上 | `14,670 JPY` |

**未解決の足内競合の割合を指標に置かない**【提案】。件数と割合は第6.1節の `INTRABAR_METHOD` の集計から読める。同じ値を指標にも置くと、`BacktestResult.unresolved_intrabar_count` と合わせて同じ数が3か所に出る。指標の側は `caveats` の `UNRESOLVED_INTRABAR_PRESENT`（第7.2節）で、その run に未解決の約定が含まれることだけを示す。

### 5.3 末尾3集計の使い分け【提案】（D06 §14 の引き受け）

上位 §4.7.13 E が定めた3集計を、採用指標と参考値に分ける。

| 集計 | 本書での扱い | 理由 |
|---|---|---|
| `realized`（確定損益） | **採用指標**。#1 `NET_PROFIT` と一致することを致命検査 C5 で確かめる | 注文・約定として実際に起きたことだけから求まる |
| `equity_with_mtm`（含み込み資産） | **参考値**（#13）。採否の判断に使わない | 未決済建玉の評価価格に依存し、決済していれば得られたとは限らない |
| `hypothetical_closed`（仮決済損益） | **参考値**（#14）。採否の判断に使わない | D06 §10.3 が「計算だけ行い、注文・約定・完了取引数・balance・リスク枠を変更しない」と定めた値である |

参考値の `MetricRecord` には `caveats` に `OPEN_POSITION_EXCLUDED` を入れる（第7.2節）。**不採用**: 3集計のどれを採用指標にするかを実験設定で選べるようにする案（同じ run の採否が設定で変わり、結果を見てから選べてしまう。全体計画 §5.5.2 の「結果を見る前に固定する」に反する）。

### 5.4 最大ドローダウンの検算が段階2でできない理由【提案】

#5・#6 は `LEDGER_SNAPSHOTS` の `equity` 列を読むだけで定義としては閉じているが、**T01 の数値では検算できない**。D06 §8.1 は run 中の `equity` を「含み損益込みの MTM 資産」とだけ定め、**含み損益の評価に使う価格の出どころ（系列・足・項目、買いは bid・売りは ask）を書いていない**（末尾の `equity_with_mtm` については §10.3 が定めている）。T01 経路1 の 09:00Z の snapshot では、直前に完了した執行足の終値（bid 150.040）を使えば含み損は `−1,280 円`、同じ足の始値（bid 150.050）を使えば `−960 円` となり、ドローダウンの値が変わる。

本書は**この規則を自分で決めない**。台帳の評価は D06 の責務であり（第1.2節の第4列の担当）、評価側で決めると同じ規則が2か所に割れる。第15節の改訂依頼2 として D06 へ差し戻し、それまでは #7・#8（`balance` 列に同じ手順を適用した参考値）で検算する。`balance` 列は T01 が全値を挙げている（`1,000,000 → 999,968 → 1,036,736 → 1,036,706`）ため、最大ドローダウン `32 JPY`・率 `0.000032` が手で確かめられる。

## 6. 集計と診断

### 6.1 集計（7種）【提案】

| `CategoryKind` | 鍵の語彙 | 入力 | T01 経路1 での値 |
|---|---|---|---|
| `OPPORTUNITY_TERMINAL_REASON` | D02 §8.1 の終端理由7語 | 表3（`to_state=TERMINATED` の行の `reason_code`） | `FULFILLED_BY_ORDER_ACCEPTANCE=1`、他は 0 |
| `ENTRY_REJECTION_REASON` | D02 §8.1 の受付前拒否6語 | 表5（`REJECTED`）× 表4（`payload_kind=ENTRY`） | 全語 0 |
| `CLOSE_REJECTION_REASON` | 同上 | 表5 × 表4（`payload_kind=CLOSE`） | 全語 0 |
| `EVALUATION_OUTCOME` | `EVALUATED` / `SKIPPED` / `FAILED` | 表2（`outcome_kind`） | `EVALUATED=6`、他は 0 |
| `MISSING_INPUT_REASON` | D02 §8.3 の4語 | 表2（`SKIPPED` の行の診断） | 全語 0 |
| `CLOSE_CAUSE` | D06 §3 の `CloseCause` 4語 | 表7（`terms_kind=CLOSE` の `terms_cause`） | `TAKE_PROFIT=1`、他は 0。`EMERGENCY` の件数が上位 §4.7.12 の求める緊急決済件数である |
| `INTRABAR_METHOD` | D06 §3 の `ResolutionMethod` 3語 | 表13（`method`） | `SINGLE_HIT=1`、他は 0 |

- **語彙が有限の集計は、0件の鍵も行として出す**【提案】。出さないと「一度も起きなかった」と「集計していない」を後から区別できない。鍵の並びは各正本（D02 §8.1、D02 §8.3、D06 §3）の宣言順に固定する。
- 取引機会の**生成総数**は `BacktestResult.opportunity_count` をそのまま使い、集計し直さない【提案】。終端理由別の件数の合計が生成総数と一致することを警告検査（第10.2節の C6）で確かめる。run 末尾に残った機会が必ず終端する規則（D05 §7.2 の遷移9）が守られていれば一致する。
- **語を足さない**【合意済み】第1.1節。集計に現れる語はすべて他文書が正本であり、本書は鍵として並べるだけである。

### 6.2 約定の診断（3項目）【提案】

上位 §4.7.12 が「遅延シナリオ別・執行粒度別に、参照価格経過時間、不利約定幅、緊急決済件数を診断できるようにする」と求めている3項目のうち、緊急決済件数は第6.1節の `CLOSE_CAUSE` が持つ。残る2項目を `FillDiagnostic` として約定1件につき1行残す。

| 項目 | 式 | 欠損時 | T01 経路1 |
|---|---|---|---|
| `adverse_fill_offset` | `d × (約定価格 − 参照価格)`。参照価格は表7 の `terms_reference_quote_price` | 決済約定には参照価格が無い（`AcceptedCloseTerms`、D06 §3）ため `None` | `+0.020` |
| `reference_to_fill` | `約定時刻 − 参照価格の観測時刻` | 同上 | `09:00Z − 09:00Z = 0` |
| `acceptance_to_fill` | `約定時刻 − 受付時刻` | なし（受付時刻は全注文にある） | `09:00Z − 09:00Z = 0` |

- **参照価格を約定直前の値へ置き換えない**【合意済み】上位 §4.7.12。評価は表7 に固定された値だけを読む。
- 遅延シナリオ別の比較は**1つの run では行えない**。run manifest は `delay_scenario_ref` を1件だけ持つ（D06 §9.3）ため、シナリオ間の差分は複数 run の比較（段階5・D09）か意味論テスト（段階3・D08）の仕事である。本書は1 run 分の診断を、後でシナリオ別に並べられる形（run ごとの表として `delay_scenario_ref` が manifest から引ける形）で残すところまでを担う。

## 7. 通貨と費用

### 7.1 通貨は口座通貨で統一する【提案】

- 金額の指標はすべて `RunManifest.account.currency` 建てとする【合意済み】上位 §5.2・D06 §8.5。trace の `Money` 列は既に口座通貨で計上されているため、**評価側で換算しない**（換算率の取得は D03 / D06 の責務、D02 §4.5）。
- 口座通貨と異なる通貨の `Money` 列が1件でもあれば、致命の整合検査 C8 の不合格とする（第10.2節）。通貨の混じった合計は意味を持たないため、丸めや読み替えで通さない。

### 7.2 swap 未計上と、価格に反映済みの費用【合意済み】＋【提案】

- **swap / rollover は未計上であり、それを結果に明記する**【合意済み】ADR-0029。明記の場所を次の2つに定める【提案】。
  1. 評価 manifest（JSON）の `swap_modeled: bool` を**必須項目**にする（省略不可・`None` 不可）。値は `BacktestResult.swap_modeled` をそのまま写す。
  2. 損益と費用に関わる `MetricRecord`（#1・#2・#10・#11・#13・#14）の `caveats` に `SWAP_NOT_MODELED` を入れる。

  指標1件だけを見た人にも未計上が伝わる形にするため、manifest と指標の両方に置く。**不採用**: manifest だけに置く案（指標を抜き出して比較した時点で注記が消える）、費用区分に `SWAP` を 0 円で足す案（計上した結果 0 円だったのか未計上なのかを区別できない）。
- **価格に反映済みの費用（滑りと提示価格の幅）を金額として二重に引かない**【合意済み】上位 §5.2・D06 §7.6。#11 は参考値であり、`caveats` に `PRICE_EMBEDDED_COST` を入れ、#1 の純損益からは引かない。
- `caveats` の5語の意味【提案】。

| `MetricCaveat` | 意味 | 付ける指標 |
|---|---|---|
| `SWAP_NOT_MODELED` | swap / rollover を計上していない（ADR-0029） | #1・#2・#10・#11・#13・#14 |
| `PRICE_EMBEDDED_COST` | 価格に反映済みで、balance から控除していない参考値 | #11 |
| `OPEN_POSITION_EXCLUDED` | 未決済建玉の評価に依存する、または未決済建玉を含まない | #2・#13・#14 |
| `ENTRY_COST_EXCLUDED` | 入場側の費用を含まない（第4.2節の制約による。改訂依頼1 の後に外す） | #2 |
| `UNRESOLVED_INTRABAR_PRESENT` | `UNRESOLVED_SL_PRIORITY` の約定を含む（ADR-0030）。`BacktestResult.unresolved_intrabar_count > 0` のときだけ付ける | #1・#2・#4 |

## 8. 結果の型と保存

### 8.1 出力する4表【提案】

| `EvaluationTable` | 行の型 | 1行の単位 | 整列鍵 |
|---|---|---|---|
| `METRICS` | `MetricRecord` | 指標1件 | `MetricId` の宣言順（第5.2節の #1〜#14） |
| `CATEGORY_COUNTS` | `CategoryCount` | 集計の鍵1件 | `(CategoryKind の宣言順, 鍵の語彙の宣言順)` |
| `TRADES` | `TradeRecord` | 完了取引1件 | `(entry_at, position_id)` |
| `FILL_DIAGNOSTICS` | `FillDiagnostic` | 約定1件 | `fill_id` |

- **資産推移の表を作らない**【提案】。最大ドローダウンは `LEDGER_SNAPSHOTS`（表14）から直接求める。評価側にも推移を保存すると同じ系列が2か所に残り、どちらが正本か決める規則がもう1つ要る。
- `TRADES` の `opportunity_id` は、建玉 → 入場約定（表9）→ 注文（表7）→ 試行（表4）の外部キーを辿って埋める【合意済み】D06 §9.2 の ID 連鎖。辿れない場合は値を空にせず、致命検査 C4 の不合格とする（連鎖が切れている trace は不整合である）。

### 8.2 保存形式【合意済み】＋【要決定】（Q4）

- 表は Parquet、manifest は JSON【合意済み】ADR-0027。平坦化の規則は D06 §9.1 と同じものを使い、別の規則を作らない（`Decimal` は文字列、`Money` は金額と通貨の2列、`ProcessingPoint` は3列）。
- 書き出しは `ResultRepository.write_evaluation` 経由で、Parquet を触るのは `evaluation.adapters.fs_store` だけ【合意済み】D01 §4・§5。
- 保存先と識別は【要決定】（Q4）。推奨案は `runs/<run_id>/eval/<metric_set_version>/` である（D01 §10.3 の `runs/` の下）。

### 8.3 評価 manifest（JSON）【提案】

| 群 | 項目 |
|---|---|
| 識別 | `run_evaluation_id`、`run_id`、`run_manifest_ref`、`metric_set_version` |
| コード | `evaluation_code_digest`（評価を実行したときのコード）、`run_code_digest`（run manifest から写す。Q5） |
| 入力 | `input_tables`（第4.2節の9表）、`account_currency` |
| 明記 | `swap_modeled`（必須、第7.2節） |
| 状態 | `status`、`fatal_failure_count`、`warning_failure_count` |
| 再現性 | `result_digest`（第9.2節） |

**実行時刻を入れない**【提案】。壁時計の時刻を入れると同じ trace から同じ成果物が出なくなり、第9.1節の再現性の条件を評価自身が壊す。いつ評価したかはファイルシステムの更新時刻で足りる。

## 9. 再現性

### 9.1 同一 trace から同一結果を出す条件【提案】

| # | 条件 | 壊れると何が起きるか |
|---|---|---|
| 1 | 集計の前に第8.1節の整列鍵で並べ替える | Parquet の行順（書き出しの並列度や圧縮設定）で結果の並びが変わる |
| 2 | 金額は丸めず、比率は除算を1回だけ行う（第5.1節） | 丸めの回数と順序で末尾の桁が変わる |
| 3 | `Decimal` は文字列で保存する | 浮動小数へ落ちて再現性が壊れる【合意済み】ADR-0012 |
| 4 | 評価結果に壁時計の時刻・乱数・絶対パスを入れない | 同じ入力から別のダイジェストが出る |
| 5 | 0件の鍵も行として出す（第6.1節） | 語彙が増減したときに行の有無で結果が変わる |

### 9.2 結果のダイジェストと、評価コードのダイジェスト【提案】＋【要決定】（Q5）

- `result_digest` は、4表の全行を第8.1節の整列鍵で並べた列の **D02 §9.3 の正規化エンコードのダイジェスト**とする【提案】。Parquet のファイルそのものはメタデータや圧縮設定でバイト列が変わりうるため、再現性の判定はファイルの一致ではなく `result_digest` の一致で行う。再現性テスト（第11節）は「同じ入力で2回評価して `result_digest` が一致する」ことを確かめる。
- `RunEvaluationId = digest(run_id, metric_set_version, evaluation_code_digest)` とする【提案】。同じ trace を別の指標集合の版で評価した結果が、別の識別子になる。D02 §7.1 の `EvaluationId`（部品の1回の評価）とは別の型であり、名前を似せない（語彙の二重定義を避ける、第1.1節）。
- **評価時のコードのダイジェストを持たせるかどうか**は【要決定】（Q5）。推奨案は、D02 §9.4 と同じ算出（パッケージ全体を対象にした `CodeDigest`）を評価時にもう一度行って `evaluation_code_digest` に入れ、run manifest の `code_digest` と異なるときに結果から判別できるようにすることである。

## 10. 評価の状態と失敗の表し方

### 10.1 状態の4値【提案】＋【要決定】（Q6）

全体計画 §5.5.1 が求める「完了 / 失敗 / 拒否 / 中断」を `EvaluationStatus` の4値とする。段階2で起きるのは前の3つである。

| 値 | 意味 | 段階2で起きるか | 出力 |
|---|---|---|---|
| `COMPLETED` | 評価が完了した。0取引でもこの値 | 起きる | 4表と manifest をすべて出す |
| `REJECTED` | 入力の run が正常完走していない（`BacktestResult.status != COMPLETED`）【合意済み】上位 §4.7.13 C | 起きる | 【要決定】Q6。推奨案は、指標を算出せず `status` と `capability_report` の要約・検査結果だけを出す |
| `FAILED` | 評価自身が完了できなかった（致命の整合検査が1件でも不合格） | 起きる | 指標を算出せず、検査結果を全件出す |
| `ABORTED` | 探索の途中で中断された | **起きない（段階5・D09）** | — |

- **0取引は失敗ではない**【提案】。`COMPLETED` とし、取引に依存する指標を `Unavailable(NO_TRADES)` にする。段階4の完了条件「失敗 / 0取引も説明できる」（全体計画 §8.2）は、状態と値なしの理由の組で満たす。
- **致命の検査が1件でも不合格なら、算出できた指標も出さない**【提案】。部分的に出すと「採用してよい数値」と「不整合な trace から出た数値」が同じ表に混ざる。**不採用**: 算出できた指標だけを出して印を付ける案（印を見落とした利用が起きる。上位 §4.7.13 C の「失敗した run の結果を正常完走の結果と同じ扱いにしない」と同じ理由で退ける）。

### 10.2 整合検査（8件）【提案】

| # | `check` | 水準 | 内容 |
|---|---|---|---|
| C1 | `required_columns_present` | FATAL | 第4.2節の9表が `trace_tables` にあり、`required=True` の列がすべて存在する |
| C2 | `run_id_consistent` | FATAL | `BacktestResult.run_id`、`RunManifest.run_id`、各表の `run_id` 列が一致する |
| C3 | `trade_count_matches` | FATAL | 完了取引の件数（表11）が `BacktestResult.trade_count` と一致する |
| C4 | `id_chain_complete` | FATAL | 各完了取引の `entry_fill_id` / `close_fill_id` が表9 にあり、その `order_id` が表7 にあり、その `attempt_id` が表4 にある |
| C5 | `realized_matches_balance` | FATAL | `最後の balance − initial_balance` が `summaries.realized` と一致する（T01: `36,706 = 36,706`） |
| C6 | `opportunity_count_matches` | WARNING | 終端理由別の件数の合計が `BacktestResult.opportunity_count` と一致する |
| C7 | `snapshot_order_monotonic` | WARNING | 表14 の `at` が処理点の昇順に並んでいる。並んでいなければ本書の整列鍵で並べ替えて続行し、警告を残す |
| C8 | `single_account_currency` | FATAL | すべての `Money` 列の通貨が `RunManifest.account.currency` と一致する |

- 検査結果は合格・不合格のどちらも全件を `EvaluationReport.checks` に残す【提案】。不合格だけを残すと、検査が実施されたのかどうかが結果から分からない（D06 §10.5 の能力検査と同じ扱い）。
- `expected` と `observed` は D02 §9.3 の正規化エンコード文字列で持つ【提案】。型ごとに列を分けると検査の種類だけ列が増え、同じ内容から常に同じ文字列が出る性質も失う。

### 10.3 「値なし」の4つの理由【提案】

| `MetricUnavailableReason` | いつ使うか | 例 |
|---|---|---|
| `NO_TRADES` | 完了取引が0件で、取引を分母または対象にする指標が定まらない | #2・#4 |
| `NO_OBSERVATIONS` | 対象の行が1件も無い（台帳 snapshot が無い、エントリー約定が無い） | #1・#5・#12 |
| `UNDEFINED_DENOMINATOR` | 分母が 0 で比率が定まらない | #6・#8 |
| `INPUT_NOT_AVAILABLE` | 入力そのものが無い（`summaries` が `None`、すなわち run が正常完走していない） | #10・#11・#13・#14 |

## 11. 段階2の最小範囲と検証【提案】

- 範囲は「検証戦略 A の単一 run の結果を、再現可能に・数値で・swap 未計上と明記して出せること」である。指標14件・集計7種・診断3項目・検査8件がその最小集合であり、これ以外を段階2で作らない。
- 全体計画 §8.2 の段階2の完了条件「人工データで注文・数量・損益・資産が手計算に一致」に対し、本書は**第5.2節の T01 検算の列**で対応する。T01 の経路1 と第9節の数値から、指標14件のうち12件が手で確かめられる（残る2件は改訂依頼2 の後、第5.4節）。
- **テスト**【提案】: 単体（各指標の式、0取引・分母0・値なしの分岐、勝敗の3区分）、意味論（`REJECTED` の run で指標を出さない、致命検査の不合格で指標を出さない、0件の鍵が行として出る、通貨違いを拒否する）、プロパティ（同じ入力で2回評価して `result_digest` が一致する、表の行の入力順を入れ替えても結果が変わらない）、golden（T01 経路1 の1取引分の4表を固定する。D06 の golden trace（全体計画 §8.4）の出力をそのまま入力にする）。

## 12. 対象外（段階4以降）

本節は**時期**の線引き（段階2で作らないもの）であり、第1.2節は**担当**の線引き（本書 v0.1 が決めないもの）である。

- 年率化、リスク調整指標、リターンの分布に関する指標 → 段階4・本書 v0.2。厳密な `Decimal` では求まらない演算（平方根・べき）を含むため、数値の型と再現性の規則を別に決める必要がある。
- 取引単位の費用の内訳と、入場費用を含む取引損益 → 第15節の改訂依頼1 の後、本書 v0.2。
- 実験 manifest（仮説・探索計画・分割・評価規則・環境）と、別プロセスでの再現手順 → 段階4・本書 v0.2。
- `ResearchPolicy` v1 の内容・検査・複雑性の計測方法 → 段階4・本書 v0.2。
- `holdout_gate`（`AsOfView` の `allowed_partitions` を決める）と閲覧履歴 → 段階4・本書 v0.2（許可の判定）、段階5・D09（探索経路からの分離）。
- 人間向けレポートの文面・体裁・図 → 段階4・`adapters.report`。
- 遅延シナリオ別の比較、執行粒度別の比較 → 段階3・D08（意味論テスト）と段階5・D09（複数 run の比較）。
- 複数 run の集約・選定・分割・探索履歴 → 段階5・D09。
- swap を計上した指標 → ADR-0029 の改訂を要する別決定。

## 13. 上位文書との差異

1. **段階2の指標から年率化・リスク調整指標を外した**。全体計画 §5.5.1 は `domain.metrics` に「リターン、MTM ドローダウン、リスク調整指標、年率化、取引数、exposure、費用集計」を挙げているが、リスク調整指標と年率化は厳密な `Decimal` で求まらない演算を含み、段階2の完了条件（手計算との一致）にも要らない。第12節のとおり段階4へ送る。全体計画 §6 D-2 の「D07 の将来機能をすべて書き切る必要はない」に沿った範囲の取り方である。
2. **完了取引の損益合計（#2）が純損益（#1）と一致しない**ことを、差分ではなく注記（`ENTRY_COST_EXCLUDED`）で表した。上位 §4.7.15 C は入場費用を約定時に balance へ計上する形にしており、`Position.realized` は決済側の費用しか含まない（T01 §2.6: `36,768 = 36,800 − 32`）。段階2では取引単位の入場費用を読めない（第4.2節）ため、差の 62 円を指標として出さず、注記で示す。
3. **単一 run の評価では遅延シナリオ別の診断を出さない**。上位 §4.7.12 と全体計画 §5.5.1 は「遅延シナリオ別の…診断」を求めているが、1つの run は `delay_scenario_ref` を1件しか持たない（D06 §9.3）。本書は1 run 分をシナリオ別に並べられる形で残すところまでを担い、比較は段階3・D08 と段階5・D09 の仕事とする（第6.2節）。

上記以外に、上位設計書・全体計画書・ADR・D01〜D06・T01 と食い違う提案はない。

## 14. 段階5（D09）への引き渡し事項

| 項目 | 内容 |
|---|---|
| 複数 run の集約 | 本書の `EvaluationReport` を run ごとに1件作り、集約・比較・選定は D09 が行う。単一実行の指標定義を D09 側で作り直さない |
| 選定規則と合否閾値 | 評価窓の長さ・選定指標・閾値・集約方法を、結果を見る前に固定する【合意済み】全体計画 §5.5.2 |
| 分割と持ち越し | train / validation / WF、purge、fold 境界の建玉と状態の扱い |
| holdout の隔離 | 探索経路から holdout を読めない構造と閲覧履歴。本書 Q1 の推奨案（市場データを読まない）を採る場合、段階2の評価は holdout へ触れる経路を1本も作らない |
| 中断の扱い | `EvaluationStatus.ABORTED` と、試行済み / 未試行 / 失敗の区別 |
| 探索で作った設定の識別 | `CompiledStrategyRef`（解決済み設定のダイジェスト）で別実行として識別する【合意済み】全体計画 §5.5.2 |

## 15. 既存文書への改訂依頼

いずれも**承認後に依頼する**。本書の承認前に他文書を書き換えない。

| # | 宛先 | 依頼 | 無いと何ができないか |
|---|---|---|---|
| 1 | D06 §9.1・§9.2（表9） | 約定1件ごとの費用を**区分別の金額列**として読めるようにする（案: `cost_commission_amount` / `cost_slippage_in_price_amount` / `cost_spread_in_price_amount` と通貨列を足し、内訳の正規化エンコード列 `costs` はそのまま残す） | D02 §9.3 は符号化だけを定義し復号を定義していないため、取引単位の費用と、入場費用を含む取引損益（第13節の2）が作れない |
| 2 | D06 §8.1 | run 中の `LedgerSnapshot.equity` の含み損益の評価に使う価格の出どころ（系列・足・項目、買いは bid・売りは ask）を、§10.3 と同じ規則で明記する | 最大ドローダウン（#5・#6）が trace から一意に決まらず、検算もできない（第5.4節） |
| 3 | D06 §9.1 | 平坦化の規則に3件を足す。(a) 複合表（表10・11・12）の列名の接頭辞、(b) 入れ子レコード（`ProtectionState` など）を開く規則、(c) 区分タグ付き union の列表現（案: `<field>_kind` ＋ 変種のフィールドを `<field>_<フィールド名>` で開く） | 第4.2節で † を付けた列の名前が決まらず、入力契約が確定しない |
| 4 | D06 §9.4 | `BacktestResult.balance_series` / `equity_series` の要素の型を定義するか、項目を落とす | 本書は表14 を直接読むため段階2では使わない。型が無いまま残すと、同じ推移が2つの経路で読めることになる |

## 16. 要決定事項（Q1〜Q6）

各項目は「何を決めるか」「結果への影響」「選択肢（推奨を先頭）」「推奨理由」の順に並べる。選択肢はラベルと影響1行だけを書き、ラベルに無い解釈を足さない。

| # | 何を決めるか | 結果への影響 |
|---|---|---|
| Q1 | 評価が市場データを読み直すか | 市場を基準にした指標を段階2で出せるかと、holdout へ触れる経路が増えるかが変わる |
| Q2 | 指標の値の丸め | 保存される数値の桁と、再現性の判定の仕方が変わる |
| Q3 | 最大ドローダウンの基準列 | 採用指標が含み損益込みになるか確定損益だけになるか、D06 への改訂依頼2 が必要かが変わる |
| Q4 | 評価結果の保存先と識別 | 同じ run を別の指標集合で評価したときに上書きになるかが変わる |
| Q5 | 評価時のコードのダイジェストを結果に持たせるか | 指標が別のコードで作られたことを後から判別できるかが変わる |
| Q6 | 正常完走していない run に対する指標の扱い | 失敗した run の数値が他と並べられるかが変わる |

**Q1 評価が市場データを読み直すか**

1. **（推奨）読み直さない**: 入力は `BacktestResult`・`RunManifest`・trace の9表だけになる。
2. **snapshot 参照を経由してだけ読む**: `SnapshotCatalog` ポート経由で読み、許可された partition の外は読めない構造になる。
3. **実験設定で選べるようにする**: 実験ごとに市場データを読むかどうかが変わる。

推奨理由: 段階2の指標14件はすべて trace と manifest から作れ、読まなければ as-of の規則とアクセス分類の規則が D03 の1か所に保たれる。

**Q2 指標の値の丸め**

1. **（推奨）金額と価格差は丸めず、比率は除算を1回だけカーネル精度で行って丸めない**: 保存値は入力の `Decimal` から一意に定まる。
2. **比率を小数第10位で ROUND_HALF_EVEN に丸める**: 保存値の桁が揃い、桁を変えると過去の結果と比較できなくなる。
3. **比率を float で持つ**: 外部ツールへの受け渡しが容易になり、ADR-0012 の境界が評価側にも開く。

推奨理由: 丸めの桁を決めないことで、桁の変更が結果を変える経路をそもそも作らない。

**Q3 最大ドローダウンの基準列**

1. **（推奨）含み損益込み（equity）を採用指標とし、確定損益（balance）を参考値として併記する**: 改訂依頼2 が必要になり、それまでの検算は参考値で行う。
2. **含み損益込み（equity）だけにする**: 指標が2件減り、改訂依頼2 が済むまで検算できる指標が無い。
3. **確定損益（balance）だけにする**: 段階2で検算できるが、全体計画 §5.5.1 の「MTM ドローダウン」を段階4まで持たない。

推奨理由: 上位文書が求める MTM 基準を採用指標に残しつつ、改訂が済むまでの検算手段を確保できる。

**Q4 評価結果の保存先と識別**

1. **（推奨）run のディレクトリの下に指標集合の版ごとの副ディレクトリを作る**: `runs/<run_id>/eval/<metric_set_version>/` に置き、run と評価が同じ場所にまとまる。
2. **評価専用の識別子で別ディレクトリに置く**: `runs/evaluations/<run_evaluation_id>/` に置き、run のディレクトリと評価のディレクトリが分かれる。
3. **run のディレクトリ直下に1組だけ置く**: 別の指標集合で評価し直すと前の結果を上書きする。

推奨理由: 1つの run の成果物が1か所にまとまり、指標集合の版を変えた評価が前の結果を消さない。

**Q5 評価時のコードのダイジェストを結果に持たせるか**

1. **（推奨）評価時に算出した `CodeDigest` を持たせ、run manifest の値と併記する**: 既存の算出規則（D02 §9.4）をそのまま使い、両者が違えば結果から分かる。
2. **持たせない**: run の `code_digest` だけを引き継ぎ、評価コードの変更は結果から分からない。
3. **評価モジュールだけを対象にした別のダイジェストを新たに定義する**: 指標の変更だけを狭く検出でき、算出規則が1つ増える。

推奨理由: 新しい規則を足さずに、同じ trace から別の数値が出た原因がコードの変更かどうかを切り分けられる。

**Q6 正常完走していない run に対する指標の扱い**

1. **（推奨）指標を算出せず、状態と診断だけを出す**: 失敗した run の数値が存在しないため、他と並べられない。
2. **算出できる指標だけ出して「採用不可」の印を付ける**: 途中までの数値を見られる代わりに、印を見落とした利用が起きうる。
3. **常に算出し、採否の判断を段階5へ委ねる**: 単一実行の結果は一様になり、採否の規則が段階5だけに置かれる。

推奨理由: 上位 §4.7.13 C の「失敗した run の結果を正常完走の結果と同じ扱いにしない」を、数値を作らないことで構造的に満たす。
