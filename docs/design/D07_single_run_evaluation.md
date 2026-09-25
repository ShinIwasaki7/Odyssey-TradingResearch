# D07: 単一実行の評価境界設計（`odyssey_fx.evaluation`: domain.metrics / domain.status / application.evaluate_run / adapters）

作成日: 2026-09-21
状態: **承認（2026-09-21、PR #17）**。v1.5（2026-09-25、PR #40）: 設計文書の必須表（R1（PR #26 承認）。全体計画書 §8.5）を加えた（第1.2節の末尾に置き場所の一覧、第9.3節に値の伝播表、第10.1.1節に状態×出来事表）。本文の規則は変えていない。表を埋める途中で本文から埋められないマスが5件見つかったので、「要決定」として各表の直後に挙げた（R1-D07-1〜5）。v1.4（2026-09-23）: 検証戦略 B の紙上トレース [T02](../traces/T02_paper_trace_strategy_b.md) が、段階3 の判断履歴を本書が読むとどうなるかを確かめた結果を2か所に足した。(1) **段階3 で足される4表（待機の出来事・遡った入力・確認試行・有効性の再検査）を本書は読まない**（第4.2節。読まない表は合計10表になる）。(2) **評価の結果区分の集計は、段階3 では語彙が5語になり（0件の行も出す。出さないと遅延シナリオごとに行の集合が変わる）、その合計が評価要求の数ではなく評価記録の数になる**（第6.1節）。**指標15件の値はどちらでも変わらないが、段階3 の実装で段階2 の run を評価すると集計に0件の行が2行増える**ので、評価側の固定出力（golden）は更新の対象になる。v1.3（2026-09-22、PR #20）: 段階2 の実装（PR #20）が残した**仮置き事項8件に人間の決定が出た**ので本文へ反映した。(1) 建玉を保有していた時間の割合（第5.2節の #9）は**完了取引だけ**を数える。式の本文にあった「未決済建玉は run 末尾までを数える」を削り、同じ節の冒頭の `Σ` の定義（完了した取引についての合計）と検算値 `0.0078125` に揃えた。これで**段階2 の指標15件すべてが T01 の検算値と一致する**。(2) 読む列（第4.2節）に**8列**を足した。処理点を組み立てる7列（表11 の `opened_at_phase` / `opened_at_sequence`、表9 の `processed_at_phase` / `processed_at_sequence`、表3 の `at_time` / `at_phase` / `at_sequence`）と、不利約定幅の符号に要る表7 の `side` である。(3) 評価 manifest の `run_manifest_ref`（第8.3節）は入力とポリシーの群のダイジェスト（`ConfigDigest`）である。(4) 取引機会の終端理由の語彙（第6.1節）は `RUN_END` を含む**8語**であり、語彙に無い鍵も行として残す。(5) 拒否（`REJECTED`）の run でも整合検査を7件実施する（第10.1節。末尾の集計と比べる C5 だけ実施しない）。(6) `EvaluateRun`（第3節）は `Protocol` ではなく具体クラスである。**未確定として残した項目は無い**。v1.1（2026-09-21、PR #17）: 第15節の改訂依頼1〜3 に対する人間の決定（D06 の Q12〜Q14、いずれも選択肢1）が出たため、**同じ PR で D06 を v1.2 に改訂し、本書の未確定箇所をすべて閉じた**。(1) 第4.2節の † を付けていた列名が確定した（平坦化規則の確定による。`decision_kind` は規則どおり `kind` になった）。(2) run 中の含み損益の評価価格が確定し、**最大ドローダウン（含み損益込み、#5・#6）の検算値が求まった**（`1,312 JPY` / `0.001312`）。これにより**段階2の指標15件すべてが紙上トレース [T01](../traces/T01_paper_trace.md) の数値で手で確かめられる**（従来は13件）。検算に必要な `equity` の全値は T01 v1.1（第9.4節）に足した。(3) 約定1件ごとの費用が区分別の金額列として読めるようになった（読むのは本書 v0.2・段階4）。**第15節に未実施の改訂依頼は残っていない**。v1.0（2026-09-21）: 第16節の要決定 Q1〜Q6 を人間がすべて決定し（6件すべてが提示時の推奨案である選択肢1）、本文へ反映した。**未決の項目は残っていない**。決定に伴い、**同じ PR で正本を1件改訂した**: 結果 DTO から資産推移の2項目（`balance_series` / `equity_series`）を落とす改訂（D06 §9.4、v1.1。第15節の改訂依頼4。D06 §9.4 が既に定めた「集計前のレコードは表のパス経由で渡し、結果 DTO の中で集計しない」から一意に導ける補完であり、設計の選択は伴わない）。第15節の改訂依頼1〜3 は設計の選択を含むためこの時点では実施せず、v1.1 で解消した。v0.1（2026-09-21）: 段階2（検証戦略 A の単一 run）の結果を、再現可能に・数値で・swap 未計上と明記して出すために必要な**境界**だけを決める。指標を将来まで書き切ることは目的にしない（全体計画 §6 D-2「D05 と D07 の将来機能をすべて書き切る必要はない」）。ADR-0016 条件2 のうち「D07 の単一実行評価境界」を本書で充足する。第16節に決定の一覧を置く。 v1.2（2026-09-22、PR #19）: 判断履歴の数値の列が人の読める固定小数表記になったことを第4.3節に注記した（D06 §9.1 v1.3）。**読む列の名前と顔ぶれは変わらず**、`Decimal(文字列)` の往復も変わらないため、指標・集計・整合検査はいずれも影響を受けない。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.7.12・§4.7.13 C/E・§4.7.14・§4.7.15・§5.3・§6、[全体計画書](fx_research_platform_overall_plan.md) §5.5・§5.5.1・§5.5.2・§6 C-5・§6 D-2・§7.5・§8.1・§8.2、[D01](D01_architecture_and_dependency_rules.md) §3.2・§4・§7.2・§10.3、[D02](D02_common_kernel.md) §4・§7.1・§8.1・§8.3・§9、[D03](D03_marketdata_and_time.md) §3.9・§6.1、[D05](D05_strategy_runtime.md) §3・§7.2、[D06](D06_backtest_vertical_slice.md) §9（全項）・§10.3・§14、[T01](../traces/T01_paper_trace.md)、ADR-0006（決定論的 ID）、ADR-0012（Decimal / float 境界）、ADR-0016（実装開始条件）、ADR-0027（成果物は Parquet 表＋JSON マニフェスト）、ADR-0029（swap 未計上）、ADR-0030（足内競合解決契約）
対応段階: 段階2で最小実装、段階4で拡張（v0.2）。

## 0. 本書の位置付けと凡例

バックテストが残した**判断履歴（trace）と実行条件（run manifest）を読み、単一 run の数値の結果を作る**手順と型を決める。バックテストの意味論には立ち入らず（全体計画 §5.5）、trace を作り直したり、結果を都合よく修正したりしない【合意済み】上位 §5.3。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D06、T01）で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択する事項。**Q1〜Q6 は 2026-09-21 にすべて決定済み**で、本文は決定後の内容になっている。**未決の項目は残っていない**。第16節に決定の一覧を置く |

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
| 保存形式と平坦化の規則 | D06 §9.1 | 参照のみ。本書の成果物も同じ規則で保存する（第8.2節）。起草時に足りなかった3件の規則は D06 v1.2 で確定した（第15節） |
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

| # | 領域 | 本書 v1.0 が決めること（対象内） | 後続が決めること（対象外・担当） |
|---|---|---|---|
| 1 | 入力契約 | 入力の範囲（Q1 決定、選択肢1: `BacktestResult` / `RunManifest` / trace の9表の3つに限る）、読む列と用途、読まない6表、読み出しのポートの操作（第4節） | 遅延シナリオ4ケースの比較（**段階3・D08**）。snapshot 参照経由の市場データ読込と holdout の許可（**本書 v0.2・段階4**。Q1 の決定により段階2では作らない） |
| 2 | 指標 | 段階2の最小集合15件の式・入力列・単位・丸め・欠損時の扱いと、**その15件すべての T01 での検算値**（第5節） | 年率化・リスク調整指標・分布指標（**本書 v0.2・段階4**）、複数 run の集約と選定（**段階5・D09**） |
| 3 | 集計と診断 | 終端理由別・拒否理由別・診断理由別など7種の集計、約定ずれと2つの経過時間の診断（第6節） | 遅延シナリオ別・執行粒度別の比較（**意味論テストとしての比較は段階3・D08、複数 run を並べる比較そのものは段階5・D09**。第12節と同じ分担）、人間向けレポートの文面と体裁（**本書 v0.2・段階4**） |
| 4 | 通貨と費用 | 口座通貨で統一すること、価格反映済み費用を二重計上しない区別、swap 未計上を出力に載せる場所（第7節） | swap を計上すること自体（**ADR-0029 の改訂を要する別決定**）、証拠金・レバレッジに基づく指標（**D10**） |
| 5 | 結果の型と保存 | 出力5表と評価 manifest の項目、保存先と識別（Q4 決定）、行の整列鍵（第8節） | 実験 manifest の項目（**本書 v0.2・段階4**）、探索履歴と holdout 閲覧履歴（**段階5・D09**） |
| 6 | 再現性 | 決定論の条件、結果ダイジェスト、評価時のコードのダイジェスト（Q5 決定）（第9節） | **別プロセスでの再現手順そのものの定義（本書 v0.2・段階4**。全体計画 §7.5・第12節）と、**その手順が実際に再現することの検証（段階4の完了条件・D08**）。定義と検証を別の担当に置く |
| 7 | 失敗と0取引 | 評価の状態4値、整合検査8件と致命/警告の別、「値なし」を型で表すこと、失敗 run の扱い（Q6 決定）（第10節） | 探索の中断（`ABORTED`）の扱い（**段階5・D09**）、研究ポリシーの内容と複雑性の計測（**本書 v0.2・段階4**） |

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

**必須表（全体計画書 §8.5、R1）の置き場所**（v1.5）。本書は評価の段階と状態（第10節）と、実行から評価へ渡る値（第4節・第8節・第9節）を定めるので、3つの表をすべて持つ。

| 必須表 | 置き場所 |
|---|---|
| 境界表 | 本節の上の2表 |
| 状態×出来事表 | 第10.1.1節（評価の段階 × 出来事） |
| 値の伝播表 | 第9.3節 |

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
| `MetricId` | `domain.metrics` | enum | 第5.2節の15件 | §5.2 |
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
| `TableReadResult` | `application.ports` | レコード | `table: TraceTable` / `table_present: bool` / `missing_columns: tuple[str, ...]` / `rows: tuple[tuple[str \| None, ...], ...]`（`rows` は `missing_columns` が空のときだけ非空になりうる） | §4.3・§10.2 |
| `EvaluationTable` | `application.manifest` | enum | `METRICS` / `CATEGORY_COUNTS` / `TRADES` / `FILL_DIAGNOSTICS` / `CONSISTENCY_CHECKS` | §8.1 |
| `RunEvaluationId` | `application.manifest` | レコード | `digest: ContentDigest`（`digest(run_id, metric_set_version, evaluation_code_digest)`）。D02 §7.1 の `EvaluationId`（部品の1回の評価）と**別の型**であり、名前も混同しない | §8.3・§9.2 |
| `EvaluationManifest` | `application.manifest` | レコード | `run_evaluation_id: RunEvaluationId` / `run_id: RunId` / `run_manifest_ref: ContentDigest` / `metric_set_version: int` / `evaluation_code_digest: CodeDigest`（Q5 で「持たせない」が選ばれた場合は項目ごと外す） / `run_code_digest: CodeDigest` / `run_status: RunStatus` / `run_failure_reason: Reason \| None` / `account_currency: CurrencyCode` / `swap_modeled: bool` / `status: EvaluationStatus` / `result_digest: ContentDigest` / `input_tables: tuple[TraceTable, ...]` / `fatal_failure_count: int` / `warning_failure_count: int` | §8.3・§9.2・§10.1 |
| `EvaluationReport` | `application.evaluate_run` | レコード | `manifest: EvaluationManifest` / `status: EvaluationStatus` / `metrics: tuple[MetricRecord, ...]` / `categories: tuple[CategoryCount, ...]` / `trades: tuple[TradeRecord, ...]` / `fill_diagnostics: tuple[FillDiagnostic, ...]` / `checks: tuple[ConsistencyCheckResult, ...]` | §8.1・§10.1 |
| `ResultRepository` | `application.ports` | Protocol | `read_manifest(run_id: RunId) -> RunManifest` / `read_table(run_id: RunId, table: TraceTable, columns: tuple[TraceColumnSpec, ...]) -> TableReadResult` / `write_evaluation(report: EvaluationReport, rows: Mapping[EvaluationTable, tuple[object, ...]]) -> None` | §4.3・§8.2 |
| `EvaluateRun` | `application.evaluate_run` | 具体クラス（v1.3） | `evaluate(result: BacktestResult, repository: ResultRepository, metric_set_version: int) -> EvaluationReport`。構築時に評価コードのダイジェストを受け取る | §4.1 |

**`EvaluateRun` は `Protocol` ではなく具体クラスである**【確定】（v1.3、2026-09-22 の人間の決定）。評価を差し替える側が居らず（合成が唯一の結線点）、実装は1つだからである。D06 の実行の使用箇所（`RunBacktest`）と同じ形に揃えた。操作の名前と引数は上表の宣言どおりである。評価コードのダイジェストだけは構築時に受け取る。パッケージのソース内容を読むのは入出力であり、`application` は入出力を持たないためである（第9.2節）。

`Money` / `Price` / `PriceOffset` / `Quantity` / `Decimal` / `UtcTime` / `Interval` / `ProcessingPoint` / `CurrencyCode` / `Reason` / `ContentDigest` / `CodeDigest` と各 ID 型は D02、`Symbol` は D02 §5.1、`OrderSide` / `CloseCause` / `TraceTable` / `BacktestResult` / `RunManifest` / `FinalSummaries` / `RunStatus` は D06、`OpportunityId` の意味は D05 が正本である。`ResultRepository` は D01 §4 が「結果の読み書き」として所在と実装者を既に確定しており、本書はその操作だけを具体化する。**表の読み出しに新しいポートを足さない**。

## 4. 評価の入力契約

### 4.1 入力は3つだけ【提案】＋【合意済み】（Q1 決定、選択肢1）

`EvaluateRun.evaluate` の入力は次の3つに限る。

| # | 入力 | 渡し方 | 正本 |
|---|---|---|---|
| 1 | `BacktestResult` | 引数（`BacktestRunner` ポートの戻り値、または `ResultRepository` が読んだ値） | D06 §9.4 |
| 2 | `RunManifest` | `ResultRepository.read_manifest(run_id)` | D06 §9.3 |
| 3 | trace の9表 | `ResultRepository.read_table(...)`。読む表と列は第4.2節 | D06 §9.2 |

- **評価は run を実行し直さない**【合意済み】全体計画 §5.5。単一 run の実行は `BacktestRunner` ポート（D01 §4）の仕事であり、本書の `evaluate` は実行済みの結果だけを受け取る。
- **評価は trace を書き換えない**【合意済み】全体計画 §5.4.5「評価側はこれを改変しない」。読み出しだけを行い、書き出しは第8節の5表と JSON に限る。
- **評価は生の市場データを読み直さない**【合意済み】（Q1 決定、選択肢1）。段階2の指標15件はすべて trace と manifest から作れる（第5.2節の各行の「入力列」）。市場データを読む経路を作ると、as-of の規則（D03 §6）とアクセス分類の許可（D03 §6.1 の `allowed_partitions`）を評価側にも置くことになり、D03 が正本である規則が2か所に割れる。**不採用**: snapshot 参照を経由してだけ読む案（選択肢2。読み出しの契約と holdout の許可を本書に書き足すことになる）、実験設定で選べるようにする案（選択肢3。実験ごとに入力の範囲が変わる）。
- 入力の1と2の整合（`BacktestResult.run_id` と `RunManifest.run_id` が一致すること）は第10.2節の致命検査 C2 で確かめる。

### 4.2 読む表と列【提案】

段階2で読むのは15表のうち**9表**である。各列は D06 §9.1 の平坦化規則で得られる名前であり、**v1.1 で全列が確定した**（D06 v1.2 の Q14 決定。行そのものの型のフィールドは接頭辞なし、入れ子は `<フィールド名>_` を接頭辞に再帰的に開く、区分タグ付き union は `kind` 列＋全変種のフィールドの和集合）。列名が規則から導かれることは、trace の型が変わったときに本書のどの行を直すかを機械的に決められることを意味する。

**9表すべてで `run_id` 列を読む**【提案】。下表では列の欄に書かず、`TraceColumnSpec` を組み立てるときに必ず `required=True` の先頭列として要求する。全行が `run_id` を持つことは D06 §9.1 で確定しており、これを読まないと第10.2節の致命検査 C2（実行の識別子が全表で一致すること）を実施できない。表ごとに書くと9回同じ列名が並び、1か所で落としても気付けない。

| 表 | `TraceTable` | 読む列 | 何に使うか |
|---|---|---|---|
| 2 | `EVALUATIONS` | `evaluation_id`、`outcome_kind`、`outcome_diagnoses`、`outcome_reason_code` | 評価の結果区分別・評価見送りの診断理由別の集計（第6.1節） |
| 3 | `OPPORTUNITY_TRANSITIONS` | `opportunity_id`、`at_time`、`at_phase`、`at_sequence`、`to_state`、`reason_code` | 取引機会の終端理由別の集計（第6.1節）。処理点の3列は主キー `(opportunity_id, at)` を組み立てるため（v1.3） |
| 4 | `ORDER_REQUESTS` | `attempt_id`、`payload_kind`、`payload_opportunity_id`、`payload_position_id` | 拒否をエントリーと決済に分ける。取引と取引機会を結ぶ（第5.2節の `TradeRecord.opportunity_id`） |
| 5 | `ATTEMPT_DECISIONS` | `attempt_id`、`kind`、`order_id`、`reason_code` | 発注試行の拒否理由別の集計（第6.1節）。**行そのものが区分タグ付き union（`AttemptDecision`）であるため接頭辞が付かず、区分の列は `kind` になる**（D06 §9.1 の規則2） |
| 7 | `ORDERS` | `order_id`、`attempt_id`、`accepted_at_time`、`side`、`terms_kind`、`terms_cause`、`terms_position_id`、`terms_reference_quote_price`、`terms_reference_quote_observed_at` | 約定ずれと2つの経過時間の診断（第6.2節）、決済契機別の集計。`side` は不利約定幅の符号 `d` に要る（第6.2節、v1.3） |
| 9 | `FILLS` | `fill_id`、`order_id`、`position_id`、`processed_at_time`、`processed_at_phase`、`processed_at_sequence`、`price`、`quantity` | 約定時刻、約定価格、取引の突合（第5.2節・第6.2節）。処理点の3列は `TradeRecord.exit_at` に要る（v1.3） |
| 11 | `POSITIONS` | `position_id`、`symbol`、`side`、`quantity`、`entry_price`、`entry_fill_id`、`opened_at_time`、`opened_at_phase`、`opened_at_sequence`、`status`、`close_fill_id`、`realized_amount`、`realized_currency` | 完了取引の一覧、損益、勝敗、保有時間（第5.2節）。処理点の3列は `TradeRecord.entry_at` と `TRADES` 表の整列鍵に要る（v1.3） |
| 13 | `INTRABAR_RESOLUTIONS` | `fill_id`、`position_id`、`method` | 足内競合の解決方法別の集計（第6.1節） |
| 14 | `LEDGER_SNAPSHOTS` | `at_time`、`at_phase`、`at_sequence`、`balance_amount`、`balance_currency`、`equity_amount`、`equity_currency` | 資産推移と最大ドローダウン（第5.2節） |

**読まない6表**を明示する【提案】。表1 `OUTPUTS`・表6 `RISK_ASSESSMENTS`・表8 `ORDER_EVENTS`・表10 `RESERVATIONS`・表12 `MANAGEMENT_APPLICATIONS`・表15 `EVIDENCE` は段階2では開かない。段階2の指標と集計に必要な列が無く、開くと入力契約が広がって「どの表が変わると指標が変わるか」が追えなくなるためである。必要になった時点で本表に足す（例: 予約額と実リスクの差を指標にするなら表6と表10）。

**段階3 で足される4表も読まない**【提案】（v1.4、2026-09-23。紙上トレース [T02](../traces/T02_paper_trace_strategy_b.md) §14 #7）。D06 v1.5 が段階3 の記録のために足した表16 `WAIT_EVENTS`・表17 `INPUT_SUBSTITUTIONS`・表18 `CONFIRMATION_ATTEMPTS`・表19 `VALIDITY_RECHECKS` は本書では開かない。**段階2 の指標15件と集計7種はこの4表の列を1つも使わない**ので、段階3 の trace を読んでも本書の出力は変わらない。**読まない表は合計10表になる**。待機・遡り・後続確認・有効性の再検査を指標や集計に反映するかどうかは本書 v0.2（段階4）で判断し、そのとき本節の表と第6.1節へ足す。

**処理点の列は3つで1つの値である**【確定】（v1.3、2026-09-22 の人間の決定）。起草時の表は建玉・約定・遷移の処理時刻について「時刻」の列だけを挙げていたが、第3節の `TradeRecord.entry_at` / `exit_at` は `ProcessingPoint` 型であり、処理点は `(時刻, フェーズ, 通し番号)` の3つで1つである（D06 §9.1）。`TRADES` 表の整列鍵も、`OPPORTUNITY_TRANSITIONS` の主キー `(opportunity_id, at)` も、この3つで決まる。そこで上表へ**7列**（表11 の `opened_at_phase` / `opened_at_sequence`、表9 の `processed_at_phase` / `processed_at_sequence`、表3 の `at_time` / `at_phase` / `at_sequence`）を足した。フェーズの順位は run manifest が記録しているフェーズ集合から引く（D06 §9.3）。あわせて表7 の `side` を足した。第6.2節の不利約定幅は `d × (約定価格 − 参照価格)` であり、方向 `d` は注文の側から引くほかないためである（起草時の表は入力として表7 を挙げながら、この列を落としていた）。

**表9 の費用の列を段階2では読まない**【提案】。D06 v1.2（Q12 決定）が表9 に区分別の金額列（`cost_commission_amount` など3区分×2列）を足したため、**約定1件ごとの費用は読めるようになった**。それでも段階2 は run 単位の費用集計を `BacktestResult.summaries.cost_breakdown`（D06 §10.3、型付きの `Mapping[CostKind, Money]`）から取る。段階2 の指標15件に取引単位の費用を使うものが無く（第5.2節）、読む列を増やすと入力契約だけが広がるためである。取引単位の費用と、入場費用を含む取引損益は本書 v0.2（段階4）で扱う（第12節）。

### 4.3 読み出しの形【提案】

- **数値の列は、人が読める固定小数の十進文字列である**【確定】（D06 §9.1 v1.3、2026-09-22 の人間の決定。PR #19）。`Decimal(文字列)` で厳密に往復するので、本書の解釈（`ColumnValueKind` の `DECIMAL`）は変わらない。**列名も、読む列の顔ぶれも変わらない**。理由の型付き詳細（`*_detail`）と可変長の入れ子の列だけは、これまでどおりダイジェスト用の正規化エンコード文字列であり、本書は段階2 でそれらの列を読まない。
- `read_table` は `TableReadResult` を返す。`rows` は**要求した列だけ**を `TraceColumnSpec` の順に並べた、**文字列（または `None`）の行の `tuple`** である。値の解釈（`Decimal` 化、`UtcTime` 化、enum 化）は `evaluation.application` が `ColumnValueKind` に従って行う。Parquet を開くのは `evaluation.adapters.fs_store` だけであり、`domain` と `application` に表形式ライブラリを入れない【合意済み】D01 §5・ADR-0025。
- **列が無いことと、行が0件であることを、戻り値で区別する**【提案】。表そのものが無ければ `table_present=False`、要求した列のうち表に無いものは `missing_columns` に入れ、`rows` は空にする。行の `tuple` だけを返す形にすると、0行の表では「必須列はあるが行が無い」と「列自体が無い」を呼び出し側が区別できず、第10.2節の C1 を実施できない。
- `required=True` の列が `missing_columns` にある場合、または `table_present=False` の場合は、その場で例外にせず**致命の整合検査の不合格**として記録する（第10.2節の C1）。評価は「なぜ評価できなかったか」を残すことが仕事であり、読み出し時に落ちると理由が残らない。
- 行の順序は Parquet の格納順に依存させない。集計の前に必ず第8.1節の整列鍵で並べ替える（第9.1節の決定論の条件1）。
- **不採用**: 表ごとの行の型（9個の dataclass）を本書で定義して `read_table` がそれを返す案（D06 の型を評価側で写し取ることになり、列が増えるたびに2か所を直す。第1.1節の二重定義の禁止に反する）、`Mapping[str, object]` の行を返す案（キーの打ち間違いを型で防げず、要求していない列が混ざる）。

### 4.4 入力の前提【合意済み】

- 入力の run が**正常完走していない**場合（`BacktestResult.status != COMPLETED`）は、採用評価に混ぜない【合意済み】上位 §4.7.13 C。扱いは第10.1節（Q6）。
- `RunManifest` の `DataCapabilityReport` は要約に畳まずそのまま保存されている【合意済み】D06 §10.5。本書は**再検査しない**。実行可否の判断は D06 の責務であり、評価がもう一度判定すると同じ規則が2か所に割れる。

## 5. 指標

### 5.1 指標の値の型と丸め【提案】＋【合意済み】（Q2 決定、選択肢1）

- **「値なし」を 0 や成功値へ置換しない**【合意済み】全体計画 §5.5.1・上位 §5.3。値が無い指標は `Unavailable(kind, reason)` として型で表し、`MetricRecord` の行は必ず残す（行ごと落とすと「計算できなかった」と「集計し忘れ」を区別できない）。
- 各 `MetricRecord` は `observation_count`（その指標が何件の観測から作られたか）を持つ【提案】。0件から作られた比率と、1件から作られた比率を、値だけで区別できないためである（T01 の勝率は1取引から作った 1 であり、多数の取引から作った 1 とは重みが違う）。
- `inputs` にはその指標が読んだ表を入れる【提案】。第4.2節の表と対応し、trace の表が変わったときに影響する指標を機械的に引ける。
- **丸め**【合意済み】（Q2 決定、選択肢1）: **金額（`AMOUNT`）と価格差（`PRICE_OFFSET`）は丸めない**。trace の `Decimal` を加減算するだけであり、D02 §4.1 のカーネル精度（28桁）で厳密に求まる。**比率（`RATIO`）は除算を1回だけカーネル精度で行い、その結果を丸めずに保存する**。表示用の桁は報告の関心であり、保存値に桁を決め打ちすると、同じ trace から出した値が桁の変更で変わる。`COUNT` は整数、`DURATION` は `timedelta`（マイクロ秒精度）とする。**不採用**: 比率を小数第10位で `ROUND_HALF_EVEN` に丸める案（選択肢2。保存値の桁は揃うが、桁を変えると過去の結果と比較できなくなる）、比率を `float` で持つ案（選択肢3。外部ツールへの受け渡しは容易になるが、ADR-0012 の `Decimal` と `float` の境界が評価側にも開く）。

### 5.2 段階2の最小集合（15件）【提案】

`d` は方向（買いなら `+1`、売りなら `-1`）、`Σ` は完了した取引（`POSITIONS` の `status=CLOSED` の行）についての合計を表す。**T01 検算**の列は、承認済みの紙上トレース [T01](../traces/T01_paper_trace.md) の経路1（正常エントリー → 利確）と第9節（run 末尾の残存処理）の数値から求めた値である。

| # | `MetricId` | 種別 | 式 | 入力 | 欠損時 | T01 検算 |
|---|---|---|---|---|---|---|
| 1 | `NET_PROFIT` | AMOUNT | 最後の `LEDGER_SNAPSHOTS` の `balance` − `RunManifest.account.initial_balance` | 表14、manifest | 表14 が空なら `Unavailable(NO_OBSERVATIONS)` | `1,036,706 − 1,000,000 = 36,706 JPY`（T01 §9.3 の `realized` と一致） |
| 2 | `CLOSED_TRADE_PROFIT` | AMOUNT | `Σ realized` | 表11 | 0取引なら `Unavailable(NO_TRADES)` | `36,768 JPY`（P1 のみ。P2 は未決済） |
| 3 | `TRADE_COUNT` | COUNT | 完了取引の件数 | 表11 | なし（0 は正しい値） | `1` |
| 4 | `WIN_RATE` | RATIO | 勝ち取引数 ÷ 完了取引数。勝敗は `realized > 0` を `WIN`、`< 0` を `LOSS`、`= 0` を `BREAK_EVEN` とし、`BREAK_EVEN` は勝ちに数えず分母には数える | 表11 | 0取引なら `Unavailable(NO_TRADES)` | `1 ÷ 1 = 1` |
| 5 | `MAX_DRAWDOWN_MTM` | AMOUNT | `at` の昇順に `equity` を走査し、`max(これまでの最大 equity − 現在の equity)` | 表14 | 表14 が空なら `Unavailable(NO_OBSERVATIONS)` | `1,000,000 − 998,688 = 1,312 JPY`（P1 入場直後。T01 §9.4） |
| 6 | `MAX_DRAWDOWN_MTM_RATE` | RATIO | #5 を、その最大値が出た時点の「これまでの最大 equity」で割る | 表14 | #5 が値なし、または分母が 0 なら `Unavailable(UNDEFINED_DENOMINATOR)` | `1,312 ÷ 1,000,000 = 0.001312` |
| 7 | `MAX_DRAWDOWN_BALANCE` | AMOUNT | #5 と同じ手順を `balance` 列に適用する（参考値） | 表14 | 同上 | `1,000,000 − 999,968 = 32 JPY` |
| 8 | `MAX_DRAWDOWN_BALANCE_RATE` | RATIO | #7 ÷ その時点の最大 `balance` | 表14 | 同上 | `32 ÷ 1,000,000 = 0.000032` |
| 9 | `EXPOSURE_RATE` | RATIO | `Σ 保有時間 ÷ RunManifest.run_interval の長さ`。保有時間は入場約定の `processed_at` から決済約定の `processed_at` まで。**完了取引（`status=CLOSED`）のみ。未決済建玉は含めない** | 表9、表11、manifest | 建玉が1件も無ければ `RatioValue(0)`（保有時間0は観測された事実であり値なしではない） | `8,100 秒 ÷ 1,036,800 秒 = 0.0078125`（P1 の 09:00Z→11:15Z、run 区間は12日） |
| 10 | `COST_CHARGED_TOTAL` | AMOUNT | `summaries.cost_breakdown[COMMISSION]` | `BacktestResult` | `summaries` が `None` なら `Unavailable(INPUT_NOT_AVAILABLE)` | `94 JPY`（P1 入場32 ＋ P1 決済32 ＋ P2 入場30） |
| 11 | `COST_PRICE_EMBEDDED_TOTAL` | AMOUNT | `cost_breakdown[SLIPPAGE_IN_PRICE] + cost_breakdown[SPREAD_IN_PRICE]`（参考値。balance から控除しない） | `BacktestResult` | 同上 | `940 + 1,240 = 2,180 JPY` |
| 12 | `MAX_ADVERSE_FILL_OFFSET` | PRICE_OFFSET | エントリー約定ごとの `max(0, d × (約定価格 − 参照価格))` の最大値 | 表7、表9 | エントリー約定が無ければ `Unavailable(NO_OBSERVATIONS)` | `+1 × (150.080 − 150.060) = 0.020` |
| 13 | `END_EQUITY_MTM` | AMOUNT | `summaries.equity_with_mtm`（参考値） | `BacktestResult` | `summaries` が `None` なら `Unavailable(INPUT_NOT_AVAILABLE)` | `1,051,706 JPY` |
| 14 | `HYPOTHETICAL_CLOSED_PROFIT` | AMOUNT | `summaries.hypothetical_closed`（参考値） | `BacktestResult` | 同上 | `14,670 JPY` |
| 15 | `NET_RETURN_RATE` | RATIO | #1 ÷ `RunManifest.account.initial_balance`（全体計画 §5.5.1 の「リターン」。期間で割らない単純収益率であり、年率化は段階4） | 表14、manifest | #1 が値なしなら同じ理由、初期残高が 0 なら `Unavailable(UNDEFINED_DENOMINATOR)` | `36,706 ÷ 1,000,000 = 0.036706` |

**未解決の足内競合の割合を指標に置かない**【提案】。件数と割合は第6.1節の `INTRABAR_METHOD` の集計から読める。同じ値を指標にも置くと、`BacktestResult.unresolved_intrabar_count` と合わせて同じ数が3か所に出る。指標の側は `caveats` の `UNRESOLVED_INTRABAR_PRESENT`（第7.2節）で、その run に未解決の約定が含まれることだけを示す。

### 5.3 末尾3集計の使い分け【提案】（D06 §14 の引き受け）

上位 §4.7.13 E が定めた3集計を、採用指標と参考値に分ける。

| 集計 | 本書での扱い | 理由 |
|---|---|---|
| `realized`（確定損益） | **採用指標**。#1 `NET_PROFIT` と一致することを致命検査 C5 で確かめる | 注文・約定として実際に起きたことだけから求まる |
| `equity_with_mtm`（含み込み資産） | **参考値**（#13）。採否の判断に使わない | 未決済建玉の評価価格に依存し、決済していれば得られたとは限らない |
| `hypothetical_closed`（仮決済損益） | **参考値**（#14）。採否の判断に使わない | D06 §10.3 が「計算だけ行い、注文・約定・完了取引数・balance・リスク枠を変更しない」と定めた値である |

参考値の `MetricRecord` には `caveats` に `OPEN_POSITION_EXCLUDED` を入れる（第7.2節）。**不採用**: 3集計のどれを採用指標にするかを実験設定で選べるようにする案（同じ run の採否が設定で変わり、結果を見てから選べてしまう。全体計画 §5.5.2 の「結果を見る前に固定する」に反する）。

### 5.4 最大ドローダウンの基準列と、段階2で検算できない理由【合意済み】（Q3 決定、選択肢1）＋【提案】

**含み損益込み（`equity`）の最大ドローダウン（#5・#6）を採用指標とし、確定損益（`balance`）に同じ手順を適用した値（#7・#8）を参考値として併記する**【合意済み】（Q3 決定、選択肢1）。上位文書と全体計画 §5.5.1 が求める含み損益込みの基準を採用指標に残したうえで、下に述べる改訂が済むまでの検算手段を参考値として確保するためである。**不採用**: 含み損益込みだけにする案（選択肢2。指標が2件減り、改訂依頼2 が済むまで検算できる最大ドローダウンが1件も無くなる）、確定損益だけにする案（選択肢3。段階2では検算できるが、全体計画 §5.5.1 が求める含み損益込みの基準を段階4まで持たない）。

#5・#6 は `LEDGER_SNAPSHOTS` の `equity` 列を読むだけで定義としては閉じているが、**起草時は T01 の数値で検算できなかった**。D06 §8.1 が run 中の `equity` を「含み損益込みの MTM 資産」とだけ定め、含み損益の評価に使う価格の出どころを書いていなかったためである。T01 経路1 の 09:00Z の snapshot では、直前に完了した執行足の終値（bid 150.040）を使えば含み損は `−1,280 円`、同じ足の始値（bid 150.050）を使えば `−960 円` となり、ドローダウンの値が変わっていた。

本書は**この規則を自分で決めなかった**。台帳の評価は D06 の責務であり（第1.2節の第4列の担当）、評価側で決めると同じ規則が2か所に割れるためである。第15節の改訂依頼2 として D06 へ差し戻し、**D06 v1.2（Q13 決定、選択肢1）で「直前に完了した執行足の終値。買いは bid、売りは spread モデルで導く ask」に確定した**（受付時の参照価格 D06 Q10 と同じ出どころ）。これにより 09:00Z の含み損は `−1,280 円`、`equity` は `998,688 円` に定まり、**#5・#6 も T01 の数値で検算できる**（T01 v1.1 §9.4 が `equity` の全値を挙げている）。

| 基準列 | 最大ドローダウン | 率 | 出どころ |
|---|---|---|---|
| `equity`（採用指標 #5・#6） | `1,000,000 − 998,688 = 1,312 JPY` | `0.001312` | T01 §9.4 の行#2（P1 入場直後） |
| `balance`（参考値 #7・#8） | `1,000,000 − 999,968 = 32 JPY` | `0.000032` | T01 が挙げる `balance` の全値（`1,000,000 → 999,968 → 1,036,736 → 1,036,706`） |

2つの値が 41 倍違うのは、入場直後の含み損 1,280 円が `balance` に現れないためである。**採用指標を含み損益込みにする理由がこの差に出ている**: 確定損益だけを見ると、建玉を持っている間にどれだけ資産が沈んだかが結果から消える。

## 6. 集計と診断

### 6.1 集計（7種）【提案】

| `CategoryKind` | 鍵の語彙 | 入力 | T01 経路1 での値 |
|---|---|---|---|
| `OPPORTUNITY_TERMINAL_REASON` | D02 §8.1 の終端理由7語に、run 末尾の終端（`RUN_END`）を加えた**8語**（v1.3） | 表3（`to_state=TERMINATED` の行の `reason_code`） | `FULFILLED_BY_ORDER_ACCEPTANCE=1`、他は 0 |
| `ENTRY_REJECTION_REASON` | D02 §8.1 の受付前拒否6語 | 表5（`REJECTED`）× 表4（`payload_kind=ENTRY`） | 全語 0 |
| `CLOSE_REJECTION_REASON` | 同上 | 表5 × 表4（`payload_kind=CLOSE`） | 全語 0 |
| `EVALUATION_OUTCOME` | **D05 §6.4 の `EvaluationOutcome` の区分をすべて**。段階2 の実装では `EVALUATED` / `SKIPPED` / `FAILED` の**3語**、段階3 の実装では `WAITING` / `SUPERSEDED` を加えた**5語**（v1.4。D05 v2.0 §6.8・§6.10 が2区分を足した） | 表2（`outcome_kind`） | `EVALUATED=6`、他は 0 |
| `MISSING_INPUT_REASON` | D02 §8.3 の4語 | 表2（`SKIPPED` の行の診断） | 全語 0 |
| `CLOSE_CAUSE` | D06 §3 の `CloseCause` 4語 | 表7（`terms_kind=CLOSE` の `terms_cause`） | `TAKE_PROFIT=1`、他は 0。`EMERGENCY` の件数が上位 §4.7.12 の求める緊急決済件数である |
| `INTRABAR_METHOD` | D06 §3 の `ResolutionMethod` 3語 | 表13（`method`） | `SINGLE_HIT=1`、他は 0 |

- **語彙が有限の集計は、0件の鍵も行として出す**【提案】。出さないと「一度も起きなかった」と「集計していない」を後から区別できない。鍵の並びは各正本（D02 §8.1、D02 §8.3、D06 §3）の宣言順に固定する。
- 取引機会の**生成総数**は `BacktestResult.opportunity_count` をそのまま使い、集計し直さない【提案】。終端理由別の件数の合計が生成総数と一致することを警告検査（第10.2節の C6）で確かめる。run 末尾に残った機会が必ず終端する規則（D05 §7.2 の遷移9）が守られていれば一致する。
- **語を足さない**【合意済み】第1.1節。集計に現れる語はすべて他文書が正本であり、本書は鍵として並べるだけである。**取引機会の終端理由は8語である**【確定】（v1.3、2026-09-22 の人間の決定）。起草時は D02 §8.1 の7語と書いていたが、run 末尾に残った機会を終端させる遷移（D05 §7.2 の遷移9）が `RUN_END` を使うため、実際の語彙は8語になる。正本は D02 §8.1 と D05 §7.2 の両方であり、本書は写しを持たない（実装は `strategy.runtime.opportunities` の語彙をそのまま読み、写しが原本とずれないことを機械検査する）。
- **語彙に無い鍵が判断履歴にあったら、行として残す**【確定】（v1.3、2026-09-22 の人間の決定）。語彙の鍵を宣言順にすべて出したうえで、語彙に無い鍵をその後ろに足す。落とすと件数の合計が生成総数と合わなくなり（第10.2節の C6 が不一致になる）、なぜ合わないかも結果から読めなくなる。「語を足さない」は本書が語彙を増やさないという規則であり、**観測された事実を落としてよいという意味ではない**。
- **段階3 では、評価の結果区分（`EVALUATION_OUTCOME`）の語彙が5語になる**【提案】（v1.4、2026-09-23。紙上トレース [T02](../traces/T02_paper_trace_strategy_b.md) §14 #7）。D05 v2.0 が `EvaluationOutcome` に**待機中（`WAITING`）と追い越しで閉じた（`SUPERSEDED`）**の2区分を足した。語彙の正本は D05 §6.4 であり（上の「語を足さない」）、本書は鍵として並べるだけなので、**段階3 の実装では上表の鍵が5語になる**。2語を「語彙に無い鍵」として後ろに足す扱いにはしない。**有限の語彙は0件の鍵も行として出す**（上の規則）からであり、後ろに足す扱いにすると、待機だけが起きた run では `SUPERSEDED` の行が出ず、**遅延シナリオごとに集計の行の集合が変わって4ケースを並べて比べられなくなる**（段階3 の完了条件「遅延シナリオ別の差分を追跡できる」。全体計画 §8.2）。**この帰結として、段階3 の実装で段階2 の run を評価すると、0件の行が2行増える**。値が変わる指標は無いが、評価側の固定出力（golden）はその時点で更新の対象になる（D08 §10.2。戦略ランタイム側の同種の帰結は D05 §6.7 にある）。
- **待機をはさんだ評価要求は評価記録が2件出るので、本集計の合計は評価要求の数ではなく評価記録の数である**【提案】（v1.4、同じ出どころ）。待機に入った要求は `WAITING` の記録を1件残し、再開・期限切れ・追い越しで決着したときにもう1件残る（D05 §6.8）。段階2 では両者が一致していたので区別する必要がなかった。段階4 で待機を指標に反映するときは、要求単位で数えるか記録単位で数えるかを先に決める。**段階2 の指標15件はこの集計を使わない**ので、指標の値は1つも変わらない。

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
| `SWAP_NOT_MODELED` | swap / rollover を計上していない（ADR-0029） | #1・#2・#10・#11・#13・#14・#15 |
| `PRICE_EMBEDDED_COST` | 価格に反映済みで、balance から控除していない参考値 | #11 |
| `OPEN_POSITION_EXCLUDED` | 未決済建玉の評価に依存する、または未決済建玉を含まない | #2・#7・#8・#13・#14 |
| `ENTRY_COST_EXCLUDED` | 入場側の費用を含まない（第4.2節のとおり段階2 は取引単位の費用を読まないため）。**本書 v0.2（段階4）で外す**。外すのに必要な入力は D06 v1.2 の区分別の費用列で揃っている | #2 |
| `UNRESOLVED_INTRABAR_PRESENT` | `UNRESOLVED_SL_PRIORITY` の約定を含む（ADR-0030）。`BacktestResult.unresolved_intrabar_count > 0` のときだけ付ける | #1・#2・#4・#15 |

## 8. 結果の型と保存

### 8.1 出力する5表【提案】

| `EvaluationTable` | 行の型 | 1行の単位 | 整列鍵 |
|---|---|---|---|
| `METRICS` | `MetricRecord` | 指標1件 | `MetricId` の宣言順（第5.2節の #1〜#15） |
| `CATEGORY_COUNTS` | `CategoryCount` | 集計の鍵1件 | `(CategoryKind の宣言順, 鍵の語彙の宣言順)` |
| `TRADES` | `TradeRecord` | 完了取引1件 | `(entry_at, position_id)` |
| `FILL_DIAGNOSTICS` | `FillDiagnostic` | 約定1件 | `fill_id` |
| `CONSISTENCY_CHECKS` | `ConsistencyCheckResult` | 整合検査1件 | 第10.2節の C1〜C8 の宣言順 |

- **整合検査の結果を表として保存する**【提案】。`EvaluationReport.checks` を保存しないと、不合格だった検査の名前・期待値・観測値が結果から消え、**保存済みの成果物だけを見て失敗を説明できない**（第10.1節の `FAILED` と `REJECTED` はここが主な出力になる）。manifest の件数だけでは、どの検査が落ちたかが分からない。
- **どの状態でも5表すべてを書く**【提案】。指標を出さない状態（`FAILED` / `REJECTED`）では `METRICS` / `TRADES` / `FILL_DIAGNOSTICS` / `CATEGORY_COUNTS` を**0行の表**として書く。表の有無で状態を表すと、書き出しが途中で落ちた成果物と区別できない。
- **資産推移の表を作らない**【提案】。最大ドローダウンは `LEDGER_SNAPSHOTS`（表14）から直接求める。評価側にも推移を保存すると同じ系列が2か所に残り、どちらが正本か決める規則がもう1つ要る。
- **実行の能力検査（`DataCapabilityReport`）を写さない**【提案】。正本は run manifest と `BacktestResult`（D06 §10.5）であり、評価 manifest は `run_manifest_ref` と `run_status` / `run_failure_reason` で**そこへ辿れる形**だけを持つ。写すと同じ検査結果が2か所に残る。
- `TRADES` の `opportunity_id` は、建玉 → 入場約定（表9）→ 注文（表7）→ 試行（表4）の外部キーを辿って埋める【合意済み】D06 §9.2 の ID 連鎖。辿れない場合は値を空にせず、致命検査 C4 の不合格とする（連鎖が切れている trace は不整合である）。

### 8.2 保存形式【合意済み】＋【合意済み】（Q4 決定、選択肢1）

- 表は Parquet、manifest は JSON【合意済み】ADR-0027。平坦化の規則は D06 §9.1 と同じものを使い、別の規則を作らない（`Decimal` は文字列、`Money` は金額と通貨の2列、`ProcessingPoint` は3列）。
- 書き出しは `ResultRepository.write_evaluation` 経由で、Parquet を触るのは `evaluation.adapters.fs_store` だけ【合意済み】D01 §4・§5。
- **保存先は `RunEvaluationId` ごとに分ける**【提案】。識別子が違う成果物を同じ場所へ書かない。指標集合の版（`metric_set_version`）だけで場所を分けると、評価コードを変えて評価し直した結果（`RunEvaluationId` は別の値になる）が前の成果物を上書きし、識別子と保存された成果物の対応が崩れる。
- **保存先は `runs/<run_id>/eval/<run_evaluation_id>/` とする**【合意済み】（Q4 決定、選択肢1）。D01 §10.3 の `runs/` の下に置き、1つの run の成果物（trace・run manifest・評価結果）が1か所にまとまる。**不採用**: 評価専用のディレクトリ `runs/evaluations/<run_evaluation_id>/` に置く案（選択肢2。run のディレクトリと評価のディレクトリが分かれる）、run のディレクトリ直下に1組だけ置く案（選択肢3。評価し直すと前の結果を上書きする）。

### 8.3 評価 manifest（JSON）【提案】

| 群 | 項目 |
|---|---|
| 識別 | `run_evaluation_id`、`run_id`、`run_manifest_ref`、`metric_set_version` |
| コード | `evaluation_code_digest`（評価を実行したときのコード。第9.2節）、`run_code_digest`（run manifest から写す） |
| 入力 | `input_tables`（第4.2節の9表）、`account_currency`、`run_status`、`run_failure_reason`（`RunStatus` が `COMPLETED` でないときの理由。D06 §9.3 から写す） |
| 明記 | `swap_modeled`（必須、第7.2節） |
| 状態 | `status`、`fatal_failure_count`、`warning_failure_count` |
| 再現性 | `result_digest`（第9.2節） |

**`run_manifest_ref` は入力とポリシーの群のダイジェスト（`ConfigDigest`、D06 §9.3）である**【確定】（v1.3、2026-09-22 の人間の決定）。保存した JSON のバイト列のダイジェストにはしない。保存形式を変えると参照が変わり、**同じ実行を指せなくなる**ためである。`ConfigDigest` は run 区間・snapshot・コンパイル結果・口座・4ポリシーから決まるので、保存形式に依らず同じ実行条件を指す。

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

### 9.2 結果のダイジェストと、評価コードのダイジェスト【提案】＋【合意済み】（Q5 決定、選択肢1）

- **結果のダイジェストは、台帳 snapshot の格納順を写す**【提案】（v1.3 の注記）。第10.2節の C7 は観測した並びそのものを `observed` に残すのが仕事であり、その行は下の5表の1つ（`CONSISTENCY_CHECKS`）に入る。したがって、内容が同じ判断履歴でも台帳 snapshot の Parquet 格納順が違えば `result_digest` は変わる。これは第9.1節の条件1（**指標・集計・取引・診断**が格納順で変わらないこと）とは別のことであり、矛盾しない。格納順が結果の説明の一部として残ることを意図している。
- `result_digest` は、5表の全行を第8.1節の整列鍵で並べた列の **D02 §9.3 の正規化エンコードのダイジェスト**とする【提案】。Parquet のファイルそのものはメタデータや圧縮設定でバイト列が変わりうるため、再現性の判定はファイルの一致ではなく `result_digest` の一致で行う。再現性テスト（第11節）は「同じ入力で2回評価して `result_digest` が一致する」ことを確かめる。
- `RunEvaluationId = digest(run_id, metric_set_version, evaluation_code_digest)` とする【提案】。同じ trace を別の指標集合の版で、あるいは別の評価コードで評価した結果が、別の識別子になる。D02 §7.1 の `EvaluationId`（部品の1回の評価）とは別の型であり、名前を似せない（語彙の二重定義を避ける、第1.1節）。
- **評価時のコードのダイジェストを持たせる**【合意済み】（Q5 決定、選択肢1）。D02 §9.4 と同じ算出（パッケージ全体を対象にした `CodeDigest`）を評価時にもう一度行って `evaluation_code_digest` に入れ、run manifest から写した `run_code_digest` と併記する。両者が異なるとき、指標が run を実行したときとは別のコードで作られたことが結果だけから分かる。新しい算出規則を足さずに済む。**不採用**: 持たせない案（選択肢2。評価コードの変更が結果から分からず、`RunEvaluationId` は `digest(run_id, metric_set_version, run_code_digest)` になる）、評価モジュールだけを対象にした別のダイジェストを新たに定義する案（選択肢3。指標の変更だけを狭く検出できる代わりに、ダイジェストの算出規則が1つ増える）。

### 9.3 値の伝播表（必須表。R1。v1.5）

実行（D06）から評価へ渡り、評価の成果物に残る値を1行ずつ挙げる（全体計画書 §8.5 の表3）。規則の正本は各行の根拠の節であり、本表はそれを並べ直したものである。

| 値 | 生成元 | 渡り方 | 記録先 | 主キー |
|---|---|---|---|---|
| `run_id`（`RunId`） | 実行が決める（ADR-0006、D06 §9.3） | `BacktestResult.run_id`・`RunManifest.run_id`・trace 9表の `run_id` 列（第4.1節・第4.2節）。`ResultRepository.read_table` / `read_manifest` の引数 | 評価 manifest の `run_id`（第8.3節）と保存先 `runs/<run_id>/eval/…`（第8.2節）。3か所の一致は致命検査 C2 で確かめる（第10.2節） | 評価 manifest は保存先1か所に1件（保存先は `RunEvaluationId` ごとに分ける。第8.2節） |
| `run_manifest_ref`（`ContentDigest`。`ConfigDigest`） | 実行時に計算（D06 §9.3） | `RunManifest` から写す | 評価 manifest の `run_manifest_ref`（第8.3節【確定】） | 同上 |
| `run_code_digest`（`CodeDigest`） | 実行時に計算（D02 §9.4、D06 §9.3） | `RunManifest` から写す | 評価 manifest（第8.3節・第9.2節） | 同上 |
| `evaluation_code_digest`（`CodeDigest`） | 評価時に D02 §9.4 と同じ算出で計算し、`EvaluateRun` の構築時に渡す（第3節・第9.2節） | `EvaluateRun` → `EvaluationManifest` | 評価 manifest。`RunEvaluationId` の入力（第9.2節） | 同上 |
| `metric_set_version`（`int`） | 呼び出し側が `evaluate` の引数で渡す（第3節） | `EvaluateRun.evaluate` → `EvaluationManifest` | 評価 manifest。`RunEvaluationId` の入力 | 同上 |
| `run_evaluation_id`（`RunEvaluationId`） | `EvaluateRun` が `digest(run_id, metric_set_version, evaluation_code_digest)` で計算（第9.2節） | `EvaluationManifest.run_evaluation_id` → `ResultRepository.write_evaluation` | 評価 manifest と保存先のディレクトリ名（第8.2節） | 保存先 `runs/<run_id>/eval/<run_evaluation_id>/` そのもの |
| `run_status`・`run_failure_reason` | 実行（D06 §9.3） | `BacktestResult.status` が状態 `REJECTED` を決め（第10.1節）、値は `RunManifest` から写す（第8.3節） | 評価 manifest | 評価 manifest は保存先1か所に1件 |
| 口座通貨（`CurrencyCode`） | 実行設定の口座（`RunManifest.account.currency`、D06 §9.3） | 致命検査 C8 の期待値（第10.2節）。評価 manifest へ写す | 評価 manifest の `account_currency` | 同上 |
| `swap_modeled`（`bool`） | 実行（`BacktestResult.swap_modeled`、D06 §9.4） | そのまま写す（第7.2節） | 評価 manifest（必須項目。第8.3節） | 同上 |
| 処理点（`ProcessingPoint`。時刻・フェーズ・通し番号の3列） | エンジン（D02 §3.3、D06） | trace の3列（表3 `at_*`、表9 `processed_at_*`、表11 `opened_at_*`、表14 `at_*`）→ `TradeRecord.entry_at` / `exit_at`（第4.2節【確定】） | `TRADES` の `entry_at` / `exit_at`（3列に平坦化。第8.2節） | **要決定（R1-D07-5）**。整列鍵は `(entry_at, position_id)`（第8.1節） |
| 建玉・取引機会・約定の識別子（`position_id`・`opportunity_id`・`entry_fill_id`・`close_fill_id`） | 採番は D06（建玉・約定）と D05（取引機会） | ID 連鎖（表11 → 表9 → 表7 → 表4）を辿って `TradeRecord` に入れる。辿れなければ致命検査 C4 の不合格（第8.1節） | `TRADES` | **要決定（R1-D07-5）** |
| 約定の診断の識別子（`fill_id`・`order_id`・`position_id`） | D06 | 表9・表7 → `FillDiagnostic`（第6.2節） | `FILL_DIAGNOSTICS` | **要決定（R1-D07-5）**。整列鍵は `fill_id`（第8.1節） |
| `result_digest`（`ContentDigest`） | `EvaluateRun` が5表の全行を整列鍵で並べ、D02 §9.3 の正規化エンコードで計算（第9.2節） | `EvaluationManifest.result_digest` | 評価 manifest | 評価 manifest は保存先1か所に1件 |
| 読んだ表の一覧 `input_tables` | 第4.2節の9表の読み出し（`ResultRepository.read_table`、第4.3節） | `EvaluateRun` → `EvaluationManifest` | 評価 manifest の `input_tables`（第8.3節） | 評価 manifest は保存先1か所に1件 |
| 整合検査の期待値（`BacktestResult.trade_count`・`opportunity_count`・`summaries.realized`、口座の `initial_balance`） | 実行（D06 §9.4・§9.3） | 整合検査 C3・C5・C6 の期待値として読む（第10.2節） | `CONSISTENCY_CHECKS` の `expected` / `observed` 列（第3節の `ConsistencyCheckResult`・第8.1節）。値そのものは評価 manifest へ写さない | **要決定（R1-D07-5）**（`CONSISTENCY_CHECKS` の主キー） |
| 評価の状態と不合格件数（`status`・`fatal_failure_count`・`warning_failure_count`） | `EvaluateRun` が整合検査の結果と入力の run の状態から決める（第10.1節・第10.2節） | `EvaluationReport` → `EvaluationManifest` | 評価 manifest の「状態」の群（第8.3節） | 評価 manifest は保存先1か所に1件 |

**要決定のマス**（本文から埋められなかったもの）:

- **R1-D07-5**: 評価5表（`METRICS`・`CATEGORY_COUNTS`・`TRADES`・`FILL_DIAGNOSTICS`・`CONSISTENCY_CHECKS`）の**主キー**。第8.1節は整列鍵だけを定めており、行を一意にする鍵と、重複したときの扱いが書かれていない。

## 10. 評価の状態と失敗の表し方

### 10.1 状態の4値【提案】＋【合意済み】（Q6 決定、選択肢1）

全体計画 §5.5.1 が求める「完了 / 失敗 / 拒否 / 中断」を `EvaluationStatus` の4値とする。段階2で起きるのは前の3つである。

| 値 | 意味 | 段階2で起きるか | 出力 |
|---|---|---|---|
| `COMPLETED` | 評価が完了した。0取引でもこの値 | 起きる | 5表と manifest をすべて出す |
| `REJECTED` | 入力の run が正常完走していない（`BacktestResult.status != COMPLETED`）【合意済み】上位 §4.7.13 C | 起きる | **指標を算出せず、`CONSISTENCY_CHECKS` 表と manifest（`run_status` / `run_failure_reason` を含む）を出し、残る4表を0行で書く**【合意済み】（Q6 決定、選択肢1） |
| `FAILED` | 評価自身が完了できなかった（致命の整合検査が1件でも不合格） | 起きる | 指標を算出せず、`CONSISTENCY_CHECKS` 表に検査結果を全件出し、残る4表を0行で書く |
| `ABORTED` | 探索の途中で中断された | **起きない（段階5・D09）** | — |

- **正常完走していない run の指標は作らない**【合意済み】（Q6 決定、選択肢1）。数値が存在しないことで、失敗した run の値が正常完走の値と並べられる経路そのものを作らない（上位 §4.7.13 C）。**不採用**: 算出できる指標だけ出して「採用不可」の印を付ける案（選択肢2。途中までの数値を見られる代わりに、印を見落とした利用が起きうる）、常に算出して採否の判断を段階5へ委ねる案（選択肢3。単一実行の結果は一様になるが、採否の規則が段階5だけに置かれる）。
- **拒否（`REJECTED`）の run でも整合検査を7件実施する**【確定】（v1.3、2026-09-22 の人間の決定）。実施しないのは末尾の集計と比べる検査（第10.2節の C5）**だけ**である。末尾の集計（確定損益）は正常完走した run だけが持つので（D06 §9.4）、存在しない値との比較を「不合格」として記録すると、完走しなかった run を「不整合な run」として説明することになる。残る7件は判断履歴だけで実施でき、全件を `CONSISTENCY_CHECKS` 表に残す。
- **0取引は失敗ではない**【提案】。`COMPLETED` とし、取引に依存する指標を `Unavailable(NO_TRADES)` にする。段階4の完了条件「失敗 / 0取引も説明できる」（全体計画 §8.2）は、状態と値なしの理由の組で満たす。
- **致命の検査が1件でも不合格なら、算出できた指標も出さない**【提案】。部分的に出すと「採用してよい数値」と「不整合な trace から出た数値」が同じ表に混ざる。**不採用**: 算出できた指標だけを出して印を付ける案（印を見落とした利用が起きる。上位 §4.7.13 C の「失敗した run の結果を正常完走の結果と同じ扱いにしない」と同じ理由で退ける）。

#### 10.1.1 状態×出来事表（必須表。R1。v1.5）

評価の段階を列、段階に届きうる出来事を行に置く（全体計画書 §8.5 の表2）。先頭行が通常の完了の経路で、2行目以降がそれ以外の出来事である。段階の並びは、入力の受け取り（第4.1節）→ 表の読み出しと整合検査（第4.3節・第10.2節）→ 指標・集計・診断の算出（第5節・第6節）→ 保存（第8節）である。各マスの結末の状態（`EvaluationStatus`）は第10.1節の表が正本である。

| 出来事 ＼ 段階 | A. 入力の受け取り（第4.1節） | B. 表の読み出しと整合検査（第4.3節・第10.2節） | C. 指標・集計・診断の算出（第5節・第6節） | D. 保存（第8節） |
|---|---|---|---|---|
| **通常の完了**: 入力の run が正常完走し（`BacktestResult.status == COMPLETED`）、完了取引が1件以上あり、致命の整合検査がすべて合格した | **遷移**: 段階 B へ進む。状態はまだ決めない（第4.1節・第10.1節） | **遷移**: 8件の整合検査をすべて実施して結果を残し、段階 C へ進む（第10.2節） | **遷移**: 指標15件・集計・診断を算出し、状態を `COMPLETED` に決める（第5節・第6節・第10.1節） | **遷移**: 5表と評価 manifest をすべて書く（`status=COMPLETED`。第8.1節〜第8.3節・第10.1節）。`result_digest` を計算して manifest に入れる（第9.2節） |
| 入力の run が正常完走していない（`BacktestResult.status != COMPLETED`） | **遷移**: 状態を `REJECTED` に決め、段階 B へ進む（第10.1節・第4.4節） | **変化なし**: 末尾の集計と比べる C5 だけを行わず、残る7件を実施して全件を残す（第10.1節【確定】） | **到達しない**: `REJECTED` では指標を算出しない（第10.1節、Q6） | **遷移**: `REJECTED` として `CONSISTENCY_CHECKS` と manifest（`run_status` / `run_failure_reason`）を書き、残る4表を0行で書く（第10.1節・第8.1節） |
| 表または必須列が無い（C1 の不合格） | **到達しない**: 段階 A は表を読まない（表は段階 B で読む。第4.1節） | **遷移**: 例外にせず致命の不合格として記録する（第4.3節）。run が正常完走していれば状態は `FAILED`（第10.1節）。run が正常完走していないときの状態は**要決定（R1-D07-1）** | **到達しない**: 致命の不合格が1件でもあれば算出しない（第10.1節） | **遷移**: `CONSISTENCY_CHECKS` に全件、残る4表を0行で書く（第10.1節・第8.1節） |
| 他の致命検査の不合格（C2〜C5・C8） | **到達しない**: 整合検査は段階 B で行う（第10.2節） | **遷移**: 状態は `FAILED`（第10.1節）。run が正常完走していないときの状態は**要決定（R1-D07-1）**（C5 はそのとき実施しない） | **到達しない**: 同上（第10.1節） | **遷移**: 同上（第10.1節・第8.1節） |
| 警告検査の不合格（C6・C7） | **到達しない**: 整合検査は段階 B で行う | **変化なし**: 警告として残して続行する。C7 は本書の整列鍵で並べ替えて続行する（第10.2節） | **変化なし**: 算出する。状態を `FAILED` にするのは致命の不合格だけである（第10.1節） | **変化なし**: `warning_failure_count` に数えて書く（第8.3節） |
| 完了取引が0件 | **到達しない**: 取引の件数は段階 B 以降で読む | **変化なし**: 0件で状態を変える規則は無い（第10.1節「0取引は失敗ではない」） | **変化なし**: 状態は `COMPLETED` のまま、取引に依存する指標を `Unavailable(NO_TRADES)` にする（第10.1節・第10.3節） | **変化なし**: 5表をすべて書く（`TRADES` は0行。第8.1節） |
| 列はあるが値を解釈できない（null・型の不一致） | **到達しない**: 段階 A は表を読まない | **要決定（R1-D07-2）** | **要決定（R1-D07-2）** | **要決定（R1-D07-2）** |
| 保存先（`runs/<run_id>/eval/<run_evaluation_id>/`）に成果物が既にある | **到達しない**: 保存先に触れるのは段階 D だけ（第8.2節） | **到達しない**: 同上 | **到達しない**: 同上 | **要決定（R1-D07-3）** |
| run manifest を読めない（`read_manifest` の失敗） | **要決定（R1-D07-4）** | **要決定（R1-D07-4）** | **要決定（R1-D07-4）** | **要決定（R1-D07-4）** |
| 探索の中断 | **到達しない**: `ABORTED` は段階5・D09 で使う（第10.1節、第1.2節の行7） | **到達しない**: 同左 | **到達しない**: 同左 | **到達しない**: 同左 |

**要決定のマス**（本文から埋められなかったもの）:

- **R1-D07-1**: run が正常完走していない（`REJECTED` の条件）うえに致命の整合検査が不合格のとき、状態を `REJECTED` と `FAILED` のどちらにするか。第10.1節は2つを別の条件で定義し、両方が成り立つときの優先を書いていない（出力の形は「`CONSISTENCY_CHECKS` と manifest、残る4表は0行」で同じだが、manifest の `status` が変わる）。
- **R1-D07-2**: 列はあるが値の解釈（`Decimal` 化・時刻化・enum 化、null）に失敗したときの扱い。第4.3節は表・列の欠落だけを C1 へ寄せており、値の解釈失敗を書いていない。`AGENTS.md` は現在の実装の扱い（検査へ寄せる）を仮置きとし、承認済み・未実施の根本対処 R5（第10.2節の改訂）で定めるとしている。
- **R1-D07-3**: 保存先に評価の成果物が既にあるときの扱い（失敗か置換か）。第8.2節は識別子ごとに保存先を分けることだけを定める。承認済み・未実施の根本対処 R4（成果物の書き込みを「存在すれば失敗」に統一する）の対象である。
- **R1-D07-4**: run manifest を読めない（ファイルが無い・壊れている）ときの扱い。第4.3節の「例外にせず致命の不合格として残す」は trace の表の読み出しだけを定め、`read_manifest`（第4.1節の入力2）には触れていない。

### 10.2 整合検査（8件）【提案】

| # | `check` | 水準 | 内容 |
|---|---|---|---|
| C1 | `required_columns_present` | FATAL | 第4.2節の9表が `trace_tables` にあり（`TableReadResult.table_present`）、`required=True` の列が `missing_columns` に1つも無い |
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
| `NO_OBSERVATIONS` | 対象の行が1件も無い（台帳 snapshot が無い、エントリー約定が無い） | #1・#5・#12・#15 |
| `UNDEFINED_DENOMINATOR` | 分母が 0 で比率が定まらない | #6・#8・#15 |
| `INPUT_NOT_AVAILABLE` | 入力そのものが無い（`summaries` が `None`、すなわち run が正常完走していない） | #10・#11・#13・#14 |

## 11. 段階2の最小範囲と検証【提案】

- 範囲は「検証戦略 A の単一 run の結果を、再現可能に・数値で・swap 未計上と明記して出せること」である。指標15件・集計7種・診断3項目・検査8件がその最小集合であり、これ以外を段階2で作らない。
- 全体計画 §8.2 の段階2の完了条件「人工データで注文・数量・損益・資産が手計算に一致」に対し、本書は**第5.2節の T01 検算の列**で対応する。T01 の経路1・第9節・第9.4節の数値から、**指標15件のすべてが手で確かめられる**（v1.1 で最大ドローダウン2件が加わった。第5.4節）。
- **テスト**【提案】: 単体（各指標の式、0取引・分母0・値なしの分岐、勝敗の3区分）、意味論（`REJECTED` の run で指標を出さない、致命検査の不合格で指標を出さず検査表だけを出す、必須列が欠けた表と0行の表を区別する、0件の鍵が行として出る、通貨違いを拒否する）、プロパティ（同じ入力で2回評価して `result_digest` が一致する、表の行の入力順を入れ替えても結果が変わらない）、golden（T01 経路1 の1取引分の5表を固定する。D06 の golden trace（全体計画 §8.4）の出力をそのまま入力にする）。

## 12. 対象外（段階4以降）

本節は**時期**の線引き（段階2で作らないもの）であり、第1.2節は**担当**の線引き（本書 v1.0 が決めないもの）である。

- 年率化、リスク調整指標、リターンの分布に関する指標 → 段階4・本書 v0.2。厳密な `Decimal` では求まらない演算（平方根・べき）を含むため、数値の型と再現性の規則を別に決める必要がある。
- 取引単位の費用の内訳と、入場費用を含む取引損益 → 本書 v0.2・段階4。読むための列は D06 v1.2（Q12 決定）で揃っており、段階2 では指標を増やさないため読まない（第4.2節）。
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
| holdout の隔離 | 探索経路から holdout を読めない構造と閲覧履歴。Q1 の決定（市場データを読み直さない）により、段階2の評価は holdout へ触れる経路を1本も作らない |
| 中断の扱い | `EvaluationStatus.ABORTED` と、試行済み / 未試行 / 失敗の区別 |
| 探索で作った設定の識別 | `CompiledStrategyRef`（解決済み設定のダイジェスト）で別実行として識別する【合意済み】全体計画 §5.5.2 |

## 15. 既存文書への改訂依頼（4件とも解消済み）

**未実施の依頼は残っていない**。4件のうち1件は本書 v1.0 の PR で、残る3件は人間の決定（D06 の Q12〜Q14）を受けて同じ PR の v1.1 で解消した。

| # | 宛先 | 依頼 | 解消のしかた |
|---|---|---|---|
| 1 | D06 §9.2（表9） | 約定1件ごとの費用を**区分別の金額列**として読めるようにする | **D06 v1.2（Q12 決定、選択肢1）**。3区分（手数料・執行モデルの滑り・提示価格の幅）それぞれの口座通貨計上額を `_amount` / `_currency` の2列ずつ足し、正規化エンコード列 `costs` も残した。段階2 は読まず、本書 v0.2 で使う（第4.2節・第12節） |
| 2 | D06 §8.1 | run 中の `LedgerSnapshot.equity` の含み損益の評価に使う価格の出どころを明記する | **D06 v1.2（Q13 決定、選択肢1）**。直前に完了した執行足の終値（買いは bid、売りは spread モデルで導く ask）に確定。これにより #5・#6 の検算値が求まり、T01 v1.1 §9.4 に `equity` の全値が入った（第5.4節） |
| 3 | D06 §9.1 | 平坦化の規則に3件（複合表の接頭辞・入れ子レコード・区分タグ付き union）を足す | **D06 v1.2（Q14 決定、選択肢1）**。「行そのものの型は接頭辞なし・入れ子は `<フィールド名>_` を接頭辞に再帰的に開く・union は `kind` 列＋全変種のフィールドの和集合・複合表の従の型は型名を接頭辞にする」に確定。第4.2節の全列がこの規則から導ける（`decision_kind` は規則どおり `kind` になった） |
| 4 | D06 §9.4 | `BacktestResult.balance_series` / `equity_series` の要素の型を定義するか、項目を落とす | **D06 v1.1（本書 v1.0 の PR）**。落とした。D06 §9.4 が既に定めた「集計前のレコードは `trace_tables` 経由で渡し、`BacktestResult` の中で集計しない」の適用であり、設計の選択を伴わないため決定を待たずに実施した |

本書が他文書へ出す新しい依頼はない。D06 v1.2 の3件はいずれも**段階2 の trace の中身を変えない**（Q12 は列を足すだけ、Q13 は既に書かれるべきだった値を一意にするだけ、Q14 は既に使われていた列名を規則として書き下ろすだけ）ため、本書の指標・集計・検査の定義もこの改訂で変わっていない。変わったのは**検算できる指標の数（13件 → 15件）**だけである。

## 16. 決定事項（Q1〜Q6、すべて決定済み）

### 16.1 決定の一覧（2026-09-21）

起草時に選択式で提示した6項目。人間が 2026-09-21 にすべて決定し、本文へ反映済みで、**未決の項目は残っていない**。「選択肢 n」は起草時に並べた番号で、1 が起草時の推奨案である。**6件すべてで選択肢1 が選ばれた**。採らなかった案は、いずれも本文の該当節に「不採用」として1行ずつ残してある。

| # | 決めたこと | 決定 | 反映先 |
|---|---|---|---|
| Q1 | 評価が市場データを読み直すか | **選択肢1（推奨）**: 読み直さない。入力は `BacktestResult`・`RunManifest`・trace の9表だけ | §4.1、§1.2 の行1、§14 |
| Q2 | 指標の値の丸め | **選択肢1（推奨）**: 金額と価格差は丸めず、比率は除算を1回だけカーネル精度で行って丸めない | §5.1、§9.1 の条件2 |
| Q3 | 最大ドローダウンの基準列 | **選択肢1（推奨）**: 含み損益込み（`equity`）の #5・#6 を採用指標とし、確定損益（`balance`）の #7・#8 を参考値として併記する | §5.4、§5.2 の #5〜#8、§7.2 の `caveats` 表 |
| Q4 | 評価結果の保存先 | **選択肢1（推奨）**: `runs/<run_id>/eval/<run_evaluation_id>/` | §8.2 |
| Q5 | 評価時のコードのダイジェストを結果に持たせるか | **選択肢1（推奨）**: 評価時に算出した `CodeDigest` を `evaluation_code_digest` として持たせ、run manifest から写した `run_code_digest` と併記する | §9.2、§8.3 の manifest 項目 |
| Q6 | 正常完走していない run に対する指標の扱い | **選択肢1（推奨）**: 指標を算出せず、状態（`REJECTED`）と診断だけを出す | §10.1 |

### 16.2 決定が本書に与えた影響

**Q1（市場データを読み直さない）**。入力契約が第4.1節の3つで閉じ、段階2の評価は holdout へ触れる経路を1本も持たない（第14節）。snapshot 参照経由の読込は本書 v0.2・段階4 の対象へ送った（第1.2節の行1）。ADR-0016 条件2（段階2の開始前に D07 の評価境界を確定する）は、この決定によって充足された。

**Q3（最大ドローダウンの基準列）**。採用指標は含み損益込み（#5・#6）、参考値は確定損益（#7・#8）に確定した。参考値である #7・#8 には `caveats` の `OPEN_POSITION_EXCLUDED` を付ける（第7.2節）。この決定により第15節の改訂依頼2（run 中の `equity` の評価価格の出どころ）が段階2の検算に必要になり、**その依頼は D06 v1.2（Q13 決定）で解消した**。最大ドローダウンは #5・#6 が `1,312 JPY` / `0.001312`、#7・#8 が `32 JPY` / `0.000032` で、いずれも T01 の数値で確かめられる（第5.4節）。

**Q5（評価時のコードのダイジェスト）**。`RunEvaluationId = digest(run_id, metric_set_version, evaluation_code_digest)` が確定し、起草時に用意した代替の式（`run_code_digest` を使う形）は不要になった（第9.2節）。

**Q2・Q4・Q6**。いずれも起草時の本文がそのまま確定したため、本文の内容は変わっていない（印だけが【要決定】から【合意済み】になった）。

**Q12〜Q14（本書 §15 から D06 へ差し戻した3件。v1.1 で解消）**。Q3 の決定によって必要になった改訂依頼2 を含む3件を、人間が 2026-09-21 にすべて選択肢1 で決定し、D06 v1.2 として同じ PR に入れた。**本書にとっての効果は、未確定だった列名が確定したことと、検算できる指標が13件から15件に増えたことの2点である**（第15節）。段階2 の指標の式・集計・整合検査はどれも変わっていない。

