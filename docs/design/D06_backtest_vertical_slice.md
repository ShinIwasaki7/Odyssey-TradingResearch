# D06: バックテスト基盤の最小縦断設計（`odyssey_fx.backtest`: engine / domain.orders / admission / execution / portfolio / trace）

作成日: 2026-09-21
状態: **v0.1 ドラフト（承認待ち）**。段階2（最小縦断＝戦略定義→注文→約定→単一評価）と紙上トレース T01 に必要な範囲だけを扱う。検証戦略 A（1時間足の高値突破→後続確認なしの成行→初期損切り＋固定リスクリワード比の利確、上位設計書 §7.1）が動くことを必要十分条件とする。ADR-0016 条件2 のうち「D06 の最小縦断範囲」を本書で充足する。全体計画 §6 D-2 が「骨子だけで実装へ進めない」と列挙した5点（注文状態、フェーズ順、原子性、run_end、trace 型）は、最小縦断の範囲でもすべて本書で確定させる。第16節に要決定の一覧を置く。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.3.12・§4.5・§4.7（全節）、[全体計画書](fx_research_platform_overall_plan.md) §5.4・§6 B/C/D/E・§7.4・§8.1・§8.2、[D01](D01_architecture_and_dependency_rules.md) §3・§4・§7.2、[D02](D02_common_kernel.md) §3.3・§4・§5.2・§7・§8・§9、[D03](D03_marketdata_and_time.md) §3・§6・§7、[D04](D04_strategy_declarations.md) §1.2・§5・§11、[D05](D05_strategy_runtime.md) §1.2・§6.1・§7・§8・§11・§12、ADR-0006（決定論的 ID）、ADR-0012（Decimal / float 境界）、ADR-0015（初版の縦断実行範囲）、ADR-0016（実装開始条件）、ADR-0027（成果物は Parquet 表＋JSON マニフェスト）、ADR-0029（swap 未計上）、ADR-0030（足内競合解決契約）、ADR-0031・ADR-0032・ADR-0033（取引機会の語彙）
対応段階: 段階2で実装。

## 0. 本書の位置付けと凡例

戦略ランタイムが返した注文意図・保護水準・管理要求を、**因果順序を守って注文・約定・建玉・口座・記録へ変換する**手順と型を決める。戦略の意味（部品の計算規則・取引機会の状態機械）と評価指標は決めない。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D05）で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択する事項。第16節に Q1〜Q8 として一覧する |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | `domain`（注文・約定・予約・建玉・口座の型）、`engine`（時刻進行とフェーズ順）、`admission`（要求組立・審査・受付）、`execution`（約定モデル）、`portfolio`（台帳）、`trace`（記録・run manifest・`BacktestResult`） | 【合意済み】D01 §1・全体計画 §5.4 |
| 依存できるもの | Python 標準ライブラリ、`odyssey_fx.common`、`marketdata.domain`、`strategy` の `declarations` / `records` / `compiler` / `runtime`（`catalog` は不可）。公開フィード・執行系列・カレンダーは `backtest.application.ports` の `Protocol` 経由 | 【合意済み】D01 §3.2（契約 F3・F6）・§4 |
| 決めないもの | 宣言の構造（D04）、部品の計算規則と取引機会の状態機械（D05）、評価指標・実験 manifest・診断の集計（D07） | 【合意済み】 |
| 層順序 | `application` → `engine` → { `admission` \| `execution` \| `portfolio` \| `trace` } → `domain`。中間層4つは相互 import しない | 【合意済み】D01 §3.3 |

型はすべて `@dataclass(frozen=True, slots=True)`、コレクションは `tuple` か凍結 `Mapping`、区分タグ付き union は `kind` フィールドを持つ dataclass の `Union` とする【合意済み】D01 §8・ADR-0011。**唯一の例外**は `IdAllocator`（D02 §7.3）と、`engine` が1 run に1つ持つ可変の実行コンテキスト参照で、第4.4節で範囲を限定する。

### 1.1 語彙と規則の正本がどの文書にあるか【提案】

D04・D05 と同じ方針を引き継ぐ。同じ語彙を2か所に定義しない。

| 事項 | 正本 | 本書の扱い |
|---|---|---|
| 理由コードの語彙 | 上位設計書 §4.7.14（実装側の列挙は D02 §8.1 がその写し） | 参照のみ。足すときは §4.7.14 と D02 §8.1 を同じ PR で更新する（第16節 Q6） |
| 取引機会の終端理由と状態機械 | 上位設計書 §4.5（状態と遷移は D05 §7 が正本） | 参照のみ。本書は受付結果を通知するだけで、機会の状態を自分で変えない（第6.6節） |
| 4型（注文要求・受付済み注文・約定・予約）の全体構成 | 上位設計書 §4.7.15 | 補助型のフィールドを本書が埋める（第5節・第6節・第7節）。既に書かれたフィールドは再定義しない |
| リスク率・口座予算の式 | 上位設計書 §4.7.10 | 参照のみ。本書は適用の時点と丸めの順だけを足す（第6.4節） |
| 足内競合解決契約 | ADR-0030・上位設計書 §4.7.7 | 参照のみ。本書は宣言形・検査・手順・記録項目を埋める（第7.4節） |
| 価格・数量・金額の型と丸めの機構 | D02 §4 | 参照のみ。**丸めの方向と適用時点**だけ本書が決める（第6.5節。D05 §1.2 の行7 が本書へ委ねた） |
| 銘柄仕様（価格刻み・数量刻み） | D02 §5.2、実体は `configs/symbols/` | 参照のみ |
| 足・系列・カレンダー・公開イベント | D03 §3・§7 | 参照のみ。本書は公開イベントを `PublicationBatch` へ変換する規則だけを足す（第4.3節） |
| フェーズの正式な列挙と順位 | **本書 §4.1** | D02 §3.3 と D05 §6.1 が本書へ委ねた。以後は本書を参照する |
| `position_context@v1` / `account_context@v1` の項目 | **本書 §8.4** | D04 §5 と D05 §4.2 が本書へ委ねた。クラスの置き場所は `strategy.records`（第8.4節の理由） |
| 部品の状態・評価記録・取引機会の遷移の内容 | D05 §6・§7 | 参照のみ。本書は trace の表として保存する形だけを決める（第9.2節） |

### 1.2 本書が決めること・後続に委ねること・D04 / D05 から受け取ること【提案】（**レビュー対象範囲の正本**）

**本書 v0.1 のレビューの対象範囲は下表の第3列**である。第4列に属する指摘は「対象外（担当へ）」として記録し、本書では直さない。ただし D04 §1.2・D05 §1.2 と同じ例外を置く。**本書の文が第4列の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。第4列の内容を本書に書き足すことはしない。

| # | 領域 | 本書 v0.1 が決めること（対象内） | 後続が決めること（対象外・担当） |
|---|---|---|---|
| 1 | フェーズと処理順 | フェーズ集合の列挙・順位・名前、1つの判断時点の処理順、**D05 が要求した約定後の評価起動点を挿す位置**、戦略ランタイムを呼ぶ回数、冪等性の確定単位（第4節） | 遅延シナリオ4ケース別の処理順の検証（**段階3・D08**）、高速化版と参照実装の同値性検証（**段階6**） |
| 2 | 注文 | 注文の状態機械（4状態・6遷移・発火条件・フェーズ・記録する理由）、発注試行と受付前拒否の記録、有効期限の計算と候補 open との競合（第5節） | 指値・逆指値・部分約定・増し玉・分割決済・週末持ち越し（**段階6・D10**）、再審査の型・配置・回数上限（**段階6・D10**） |
| 3 | 受付 | 要求組立、全順序化の鍵と優先度の向きと ID 比較規則、リスク審査と数量決定の順、**審査・予約・受付の原子性**、受付結果を戦略へ返す通知（第6節） | 戦略別リスク配分と `strategy_priority` の設定値（**D10**）、口座強制縮小の対象と優先順位（**D10**） |
| 4 | 執行 | 候補 open の選び方、約定価格、保護水準の到達判定、**足内競合解決契約（ADR-0030）の宣言形・検査・手順・記録**、gap・約定ずれ超過・緊急決済、費用モデル（第7節） | トレーリング（`UPDATE_STOP`）の評価足と期間 Exit（**段階3・D05 v0.2 と本書 v0.2**）、約定直後以外の緊急決済の実行時点（**D10**） |
| 5 | 口座・建玉 | 台帳、balance と equity の区別、建玉と保護水準の管理権、`position_context@v1` / `account_context@v1` の項目、通貨換算の適用時点（第8節） | 複数建玉・複数銘柄の台帳と配分（**D10**）、equity 基準の予算へ切り替える拡張（**D10**） |
| 6 | 記録 | trace の行の種類15件とフィールド、run manifest の項目、`BacktestResult` の項目、保存形式（第9節） | 指標の算出、終端理由別・診断理由別の集計、実験 manifest への固定（**D07**）、swap 未計上をユーザーへ伝える出力の形（**D07**、ADR-0029） |
| 7 | run_end とデータ能力 | 末尾処理の順序、残存注文・残存取引機会・残存建玉の扱い、末尾3集計の定義、実行前のデータ能力検査と実行可否（第10節・第7.5節） | 3集計の費用区分を指標へどう使うか（**D07**）、snapshot の受入れ検査そのもの（**D03 が正本**） |

前提として **D04 / D05 から受け取るもの**は次のとおりで、本書はこれらを再定義しない。

| 出どころ | 受け取るもの |
|---|---|
| D04 §5・§11 | データ型識別子13件の登録、役割出力の型要求、`ManagementAction` の2種別（`SET_TAKE_PROFIT` / `CLOSE_POSITION`） |
| D04 §10.3 | `OpportunityConcurrencySpec` の3フィールド（`max_active` / `on_new_trigger` / `on_order_accepted`） |
| D05 §3 | `EntryProposal` / `ManagementRequest` / `PublicationBatch` / `AdmissionNotice` / `RuntimeEventNotice` / `RuntimeStepResult` のフィールド |
| D05 §6.1 | `StrategyRuntime.step` の呼び出し形、`MarketDataView` / `RuntimeContextView` / `OutputSink` の実装責務 |
| D05 §7.2 | 取引機会の遷移5〜7・9 が、エンジンからの通知または末尾処理で起きること |
| D05 §8 | 約定後の利確の評価を「同じ判断時点の約定処理の後」に置くこと。**約定処理より前に置く構成は D05 が排除済み** |
| D05 §11・§12 | `position_context@v1` に有効な損切り水準を含める要求、`OrderIntent.expiry=None` の既定の有効時間、`AdmissionNotice` を返すフェーズ、`PhaseSet` への登録 |

## 2. モジュール構成【提案】

D01 §7.2 の一覧のうち、段階2で作るものと作らないものを分ける。

| サブパッケージ | 段階2で作る | 段階3以降で作る |
|---|---|---|
| `domain` | `orders.py`、`fills.py`、`reservations.py`、`positions.py`、`account.py`、`events.py`、`policies.py` | — |
| `engine` | `phases.py`、`loop.py`、`clock.py`、`run_end.py` | — |
| `admission` | `request_assembly.py`、`risk_assessment.py`、`admission.py` | — |
| `execution` | `fill_model.py`、`spread.py`、`protection_hits.py`、`emergency.py`、`cost_model.py` | — |
| `portfolio` | `ledger.py`、`mtm.py`、`conversion.py` | — |
| `trace` | `recorder.py`、`result.py`、`manifest.py` | — |
| `application` | `run_backtest.py`、`ports.py` | — |

D01 §7.2 の一覧をそのまま使い、モジュールの追加・分割はしない。段階2で中身が最小になるモジュール（`conversion.py` は換算率1の恒等換算だけ、`spread.py` は固定 spread だけ）も、置き場所を先に決めておく方が、段階3で式を足すときに層をまたぐ移動が起きない。

## 3. 本書が定義する型の一覧【提案】

本文に出る型名を、区分（enum / `kind` タグ付き union / レコード / `Protocol` / 定数）とフィールドまで一覧する。**この表にない型名を本文で使わない**。節を足すときは必ずこの表も更新する。D02・D03・D04・D05 が定義した型はそのまま使い、ここには載せない。上位設計書 §4.7.15 が既にフィールドを確定した `OrderRequest` / `AcceptedOrder` / `FillRecord` / `RiskReservation` の4型は、**本書が埋める補助型だけ**をここに載せる。

| 型 | 置き場所 | 区分 | フィールド / 値 | 詳細 |
|---|---|---|---|---|
| `BACKTEST_PHASES` | `engine.phases` | 定数（`PhaseSet`） | 第4.1節の14フェーズ（rank 0〜13） | §4.1 |
| `RunConfig` | `domain.policies` | レコード | `run_interval: Interval` / `snapshot_ref: SnapshotRef` / `compiled_ref: CompiledStrategyRef` / `account: AccountSpec` / `risk_policy_ref: PolicyRef` / `execution_policy_ref: PolicyRef` / `cost_model_ref: PolicyRef` / `delay_scenario_ref: PolicyRef` / `execution_series: SeriesId` / `seed: int` | §4.2・§9.3 |
| `AccountSpec` | `domain.account` | レコード | `account_id: AccountId` / `currency: CurrencyCode` / `initial_balance: Money` | §8.1 |
| `RiskPolicy` | `domain.policies` | レコード | `trial_risk_rate: Decimal` / `account_risk_cap: Decimal` / `cost_budget: Money` | §6.4 |
| `ExecutionPolicy` | `domain.policies` | レコード | `entry_delay_bars: int` / `adverse_fill_limits: Mapping[Symbol, PriceOffset]` / `entry_valid_for: timedelta` / `close_valid_for: timedelta` / `resolution_hierarchy: ResolutionHierarchy` | §7.1・§7.4・Q3〜Q5・Q8 |
| `CostModel` | `domain.policies` | レコード | `commission_per_unit: Money` / `entry_slippage: PriceOffset` / `close_slippage: PriceOffset` / `spread_model: SpreadModel` / `swap_modeled: bool`（段階2は常に `False`） | §7.6 |
| `SpreadModel` | `domain.policies` | union | `FixedSpread(offset: PriceOffset)`（段階2の唯一の値） | §7.2 |
| `ResolutionHierarchy` | `domain.policies` | レコード | `levels: tuple[SeriesId, ...]`（粗い順。先頭は執行系列） | §7.4 |
| `OrderStatus` | `domain.orders` | enum | `PENDING` / `FILLED` / `CANCELED` / `EXPIRED` | §5.1 |
| `OrderSide` | `domain.orders` | enum | `BUY` / `SELL` | §5.2 |
| `RequestOrigin` | `domain.orders` | enum | `STRATEGY` / `ENGINE` | §6.1 |
| `RequestClass` | `domain.orders` | enum | `CLOSE`（順位0） / `ENTRY`（順位1） | §6.3 |
| `CloseCause` | `domain.orders` | enum | `STRATEGY_EXIT` / `EMERGENCY` / `STOP_LOSS` / `TAKE_PROFIT` | §7.3・§7.5 |
| `InitialProtectionPlan` | `domain.orders` | レコード | `stop_loss: Price` / `take_profit: Price \| None`（段階2は常に `None`） / `source_output_id: OutputId` | §6.1 |
| `ExitPlanRef` | `domain.orders` | レコード | `compiled_ref: CompiledStrategyRef` / `exit_instance_id: str \| None` | §6.1 |
| `EntryRequest` | `domain.orders` | レコード | 上位 §4.7.15 の7項目（`opportunity_id` / `symbol` / `side` / `order_type` / `protection: InitialProtectionPlan` / `exit_plan_ref` / `valid_for`）＋ `intent_output_id: OutputId` | §6.1 |
| `CloseRequest` | `domain.orders` | レコード | `position_id: PositionId` / `cause: CloseCause` / `valid_for: timedelta` / `source_output_id: OutputId \| None` | §6.1 |
| `AdmissionKey` | `admission` | レコード | `decision_time: UtcTime` / `request_class: RequestClass` / `strategy_priority: int` / `origin_seq: int` / `attempt_seq: int` | §6.3 |
| `ReferenceQuote` | `domain.orders` | レコード | `price: Price` / `basis: PriceBasis` / `observed_at: UtcTime` / `source_bar: BarKey \| None` / `derived_from_spread: bool` | §6.4 |
| `AcceptedEntryTerms` | `domain.orders` | レコード | `initial_stop: Price` / `exit_plan_ref: ExitPlanRef` / `reservation_id: ReservationId` / `reference_quote: ReferenceQuote` / `adverse_fill_limit: Price` | §6.5 |
| `AcceptedCloseTerms` | `domain.orders` | レコード | `position_id: PositionId` / `cause: CloseCause` | §6.5 |
| `ExecutionCommitment` | `domain.orders` | レコード | `policy_ref: PolicyRef` / `execution_series: SeriesId` / `eligibility: Eligibility` | §7.1 |
| `Eligibility` | `domain.orders` | union | `ScheduledOpen(bar_key: BarKey, open_time: UtcTime)` / `ImmediateAfterFill(trigger_fill_id: FillId, open_event_id: EventId)` / `ProtectionHit(position_id: PositionId, protection_version: int, execution_bar_key: BarKey)` | §7.1・§7.3・§7.5 |
| `OrderState` | `domain.orders` | レコード | `order_id: OrderId` / `status: OrderStatus` / `last_event_id: EventId` / `last_processed_at: ProcessingPoint` / `terminal_reason: Reason \| None` | §5.1 |
| `OrderEvent` | `domain.events` | レコード | `event_id: EventId` / `order_id: OrderId` / `from_status: OrderStatus \| None` / `to_status: OrderStatus` / `at: ProcessingPoint` / `reason: Reason \| None` / `fill_id: FillId \| None` | §5.1 |
| `AttemptDecision` | `admission` | union | `AttemptAccepted(attempt_id, order_id, assessment_ref)` / `AttemptRejected(attempt_id, reason: Reason, assessment_ref: RiskAssessmentRef \| None)`。発端の識別子は同じ `attempt_id` を持つ `OrderRequest`（表4）から辿る | §6.2 |
| `AdmissionBudget` | `admission` | レコード | `balance: Money` / `trial_budget: Money` / `account_remaining: Money` / `admission_budget: Money` / `consumed: Money` | §6.4 |
| `RiskAssessment` | `admission` | レコード | `assessment_id: EvidenceId` / `attempt_id: AttemptId` / `policy_ref` / `budget: AdmissionBudget` / `reference_quote: ReferenceQuote` / `adverse_fill_limit: Price` / `stop_before_rounding: Price` / `stop_after_rounding: Price` / `quantity_step: Decimal` / `quantity: Quantity \| None` / `conversion: ConversionRate` / `cost_budget: Money` / `reservation_amount: Money \| None` / `checks: tuple[RiskCheckResult, ...]` | §6.4 |
| `RiskCheckResult` | `admission` | レコード | `check: str` / `passed: bool` / `limit: Money \| Decimal` / `observed: Money \| Decimal` | §6.4 |
| `ReservationStatus` | `domain.reservations` | enum | `HELD` / `TRANSFERRED` / `RELEASED` | §6.5 |
| `ReservationState` | `domain.reservations` | レコード | `reservation_id: ReservationId` / `status: ReservationStatus` / `last_event_id: EventId` / `last_processed_at: ProcessingPoint` | §6.5 |
| `ExecutionTime` | `domain.fills` | union | `ExactExecutionTime(time: UtcTime)` / `BarExecutionInterval(bar_key: BarKey, interval: Interval)` | §7.3 |
| `CostEntry` | `domain.fills` | レコード | `kind: CostKind` / `native: Money` / `account: Money` / `conversion: ConversionRate` | §7.6 |
| `CostKind` | `domain.fills` | enum | `COMMISSION` / `SLIPPAGE_IN_PRICE`（価格反映済みの記録用。金額は控除しない） | §7.6 |
| `ResolutionMethod` | `execution` | enum | `SINGLE_HIT` / `RESOLVED_BY_CHILD` / `UNRESOLVED_SL_PRIORITY` | §7.4 |
| `EvidenceKind` | `trace.recorder` | enum | `ORDER_REQUEST` / `ADMISSION` / `FILL` / `PROTECTION_UPDATE` | §9.2 |
| `MarketObservationRef` | `trace.recorder` | レコード | `snapshot_ref: SnapshotRef` / `series: SeriesId` / `interval: Interval` / `field: MarketDataField`（D04 §5 の市場データ項目） | §9.2 |
| `EvidenceRecord` | `trace.recorder` | レコード | `evidence_id: EvidenceId` / `at: ProcessingPoint` / `kind: EvidenceKind` / `output_ids: tuple[OutputId, ...]` / `evaluation_ids: tuple[EvaluationId, ...]` / `attempt_id: AttemptId \| None` / `position_id: PositionId \| None` / `market_refs: tuple[MarketObservationRef, ...]` / `ledger_snapshot_at: ProcessingPoint \| None` / `policy_refs: tuple[PolicyRef, ...]` | §9.2 |
| `IntrabarResolution` | `execution` | レコード | `position_id: PositionId` / `parent_bar_key: BarKey` / `method: ResolutionMethod` / `series_used: tuple[SeriesId, ...]` / `resolved_child_bar_key: BarKey \| None` / `verdict: CloseCause` / `fill_id: FillId` | §7.4 |
| `ProtectionState` | `domain.positions` | レコード | `version: int` / `stop_loss: Price` / `take_profit: Price \| None` / `effective_from: BarKey` / `owner_instance_id: str \| None` | §8.3 |
| `PositionStatus` | `domain.positions` | enum | `OPEN` / `CLOSED` | §8.2 |
| `Position` | `domain.positions` | レコード | `position_id` / `account_id` / `strategy_id` / `symbol` / `side: OrderSide` / `quantity: Quantity` / `entry_fill_id: FillId` / `entry_price: Price` / `opened_at: ProcessingPoint` / `protection: ProtectionState` / `status: PositionStatus` / `close_fill_id: FillId \| None` / `realized: Money \| None` | §8.2 |
| `PositionRiskAllocation` | `domain.positions` | レコード | `allocation_id: AllocationId` / `position_id` / `source_reservation_id: ReservationId` / `amount: Money` / `created_event_id: EventId` / `released_event_id: EventId \| None` | §8.1 |
| `RiskMeasurement` | `domain.positions` | レコード | `position_id` / `at: ProcessingPoint` / `measured: Money` / `allocated: Money` / `basis: str` | §8.1 |
| `PositionContext` | **`strategy.records.payloads`** | レコード | `position_id` / `symbol` / `direction: TradeDirection` / `quantity: Quantity` / `entry_price: Price` / `effective_stop_loss: Price` / `effective_take_profit: Price \| None` / `opened_at: ProcessingPoint` | §8.4 |
| `AccountContext` | **`strategy.records.payloads`** | レコード | `account_id: AccountId` / `currency: CurrencyCode` / `balance: Money` / `equity: Money` / `consumed_risk: Money` | §8.4 |
| `AccountLedger` | `domain.account` | レコード | `account_id` / `balance: Money` / `positions: Mapping[PositionId, Position]` / `allocations: Mapping[AllocationId, PositionRiskAllocation]` / `reservations: Mapping[ReservationId, RiskReservation]` / `reservation_states: Mapping[ReservationId, ReservationState]` / `order_states: Mapping[OrderId, OrderState]` / `processed_event_ids: frozenset[EventId]` | §4.4・§8.1 |
| `LedgerSnapshot` | `portfolio.ledger` | レコード | `at: ProcessingPoint` / `balance: Money` / `equity: Money` / `consumed: Money` / `open_position_ids: tuple[PositionId, ...]` | §8.1・§9.2 |
| `FinalSummaries` | `trace.result` | レコード | `realized: Money` / `equity_with_mtm: Money` / `hypothetical_closed: Money` / `cost_breakdown: Mapping[CostKind, Money]` | §10.3 |
| `RunStatus` | `trace.result` | enum | `COMPLETED` / `FAILED_DATA_ERROR` / `FAILED_CAPABILITY` | §9.4・§10.4 |
| `DataCapabilityReport` | `engine` | レコード | `integrity: IntegrityReport` / `hierarchy_checks: tuple[RiskCheckResult, ...]` / `runnable: bool` / `reason: Reason \| None` | §7.5 |
| `RunManifest` | `trace.manifest` | レコード | 第9.3節の項目 | §9.3 |
| `BacktestResult` | `trace.result` | レコード | 第9.4節の項目 | §9.4 |
| `TraceSink` | `application.ports` | Protocol | `write(table: TraceTable, rows: tuple[object, ...]) -> None` | §9.1 |
| `TraceTable` | `trace.recorder` | enum | 第9.2節の15件 | §9.2 |
| `PublicationFeed` | `application.ports` | Protocol | `events(interval)`。要素は D03 §7.1 の4イベントの union（`marketdata.application` が実装し `app` が注入する） | §4.3 |
| `ExecutionSeries` | `application.ports` | Protocol | D03 §6.3 の3操作（`open_of` / `bar` / `next_bar_key_after`） | §7.1 |
| `Calendar` | `application.ports` | Protocol | `marketdata.domain` の `TradingCalendar` を受ける（D01 §4）。期限・候補 open・休場の判定に使う | §5.3 |
| `ResultWriter` | `application.ports` | Protocol | `write(result: BacktestResult, manifest: RunManifest) -> None` | §9.1 |
| `RiskAssessmentRef` | `admission` | レコード | `assessment_id: EvidenceId`（`RiskAssessment.assessment_id` と同じ値。`RISK_ASSESSMENTS` の行を指す） | §6.4 |
| `RunBacktest` | `application.run_backtest` | Protocol | `run(config: RunConfig, compiled: CompiledStrategy) -> BacktestResult` | §4.2 |

`PositionContext` と `AccountContext` の2件だけが `strategy` 側に置かれる（理由は第8.4節）。`Symbol` / `Price` / `PriceOffset` / `Quantity` / `Money` / `ConversionRate` / `UtcTime` / `Interval` / `Reason` / `PhaseRank` / `PhaseSet` / `ProcessingPoint` と各 ID 型は D02、`SeriesId` / `BarKey` / `PriceBasis` / `IntegrityReport` は D03、`CompiledStrategy` / `EntryProposal` / `ManagementRequest` / `PublicationBatch` / `AdmissionNotice` / `RuntimeEventNotice` は D04・D05 が正本である。

## 4. エンジン（`engine`）

### 4.1 フェーズ集合【提案】＋【要決定】（Q1）

D02 §3.3 は「フェーズの具体的な一覧は `backtest.engine` が定義する」とし、`PhaseSet` の構築時に `rank` と `name` の一意性を検査する。D05 §6.1 は、取引機会の記録に押す `ProcessingPoint` のフェーズ順位を `PublicationBatch.phases.by_name(...)` で引く。**したがって本書がフェーズ名を確定させないと、D05 のランタイムは記録を組み立てられない。**

1つの判断時刻 T に属するフェーズを、因果順に次の14件（rank 0〜13）とする。`RUN_END` も `BACKTEST_PHASES` に含め、run 末尾の判断時点でだけ使う。フェーズ集合は run 全体で1つであり、判断時点ごとに変えない（D02 §3.3 の `PhaseSet` は run 内で固定される）。

| rank | 名前 | 内容 | 戦略ランタイムの関与 |
|---|---|---|---|
| 0 | `EXECUTION_BAR_COMPLETE` | 直前の執行足が終了し、その足の開始前に有効だった保護水準による内部約定を解決（第7.3節） | なし |
| 1 | `LEDGER_UPDATE` | 建玉・実現損益・balance・消費済み枠を更新（第8.1節） | なし |
| 2 | `ORDER_EXPIRY` | `expires_at <= T` の PENDING 注文を EXPIRED にし予約を解放（第5.3節） | なし |
| 3 | `PUBLICATION` | 同じ `available_at=T` の確定足をまとめて戦略へ公開（上位 §4.3.12 の P0） | `PublicationBatch` の組み立て |
| 4 | `OPPORTUNITY_LIFECYCLE` | 取引機会の期限・失効のライフサイクル検査（上位 §4.3.14。D05 §7.2 の遷移8・11 のうち検査由来のもの） | 第1回 `step` の内部 |
| 5 | `P1_FEATURE` | Feature の評価（上位 §4.3.12 の P1） | 第1回 `step` の内部 |
| 6 | `P2_MARKET_STATE` | MarketState の評価（P2） | 同上 |
| 7 | `P3_TRIGGER` | Trigger の評価と取引機会の生成（P3） | 同上 |
| 8 | `P4_CONFIRMATION` | 後続確認の評価（P4。段階2では起動しない） | 同上 |
| 9 | `P5_ORDER_INTENT` | 注文意図と保護水準の生成（P5） | 同上 |
| 10 | `ADMISSION` | 要求組立・全順序化・リスク審査・予約・受付（第6節） | なし |
| 11 | `EXECUTION_OPEN` | 次の執行足の始値処理: gap 保護決済 → 適格な成行注文の約定 → 新規建玉初期化 → 約定直後の緊急決済（第7.1節・第7.5節） | なし |
| 12 | `POST_FILL_EVALUATION` | 約定後の評価起動点（D05 §8）と受付結果の通知（第6.6節） | 第2回 `step` |
| 13 | `RUN_END` | 末尾処理（run_end の判断時点だけ。第10節） | 第3回 `step`（Q2 の決定による） |

- rank 0〜2 が rank 3 より前にあるのは「終値評価より先に足内約定を反映する」【合意済み】上位 §4.7.12。
- rank 10 が rank 9 の直後にあるのは「同時刻 close → 判断/受付 → open」【合意済み】上位 §4.7.11。
- rank 12 が rank 11 の後にあるのは D05 §8 の決定（約定処理より前に置く構成は排除済み）。D05 が本書へ委ねたのは**順位と挿す位置**だけであり、本書はそれを rank 12 として確定する。
- rank 2 が rank 11 より前にあるのは「期限処理フェーズを open 約定フェーズより前に置き、同時刻なら EXPIRED を先に確定する」【合意済み】上位 §4.7.13 B。

**フェーズ名に数字を含められるかが未解決**である【要決定】（Q1）。D02 §3.3 は `PhaseRank.name` を `^[A-Z_]+$` と定めており、この正規表現は数字を許さない。一方 D05 §6.1 は `P1_FEATURE`〜`P5_ORDER_INTENT` という名前で引くと書いている。どちらかを改訂しなければ実装できないため、上表は Q1 の決定が「D02 の規則を緩める」場合の名前で書いてある。Q1 で「数字を使わない」を選んだ場合は、上表の rank 5〜9 の名前を `FEATURE` / `MARKET_STATE` / `TRIGGER` / `CONFIRMATION` / `ORDER_INTENT` に置き換え、D05 §6.1 の名前の列挙を同じ PR で改訂する。**どちらを選んでも rank の順序と本書の他の節は変わらない。**

`BACKTEST_PHASES` は `engine.phases` の定数 `PhaseSet` とし、run manifest に記録する【合意済み】D02 §3.3。

### 4.2 1つの判断時点で行うこと【提案】

`RunBacktest.run` は、公開フィードが生成するイベント列（D03 §7.1）を `available_at` 順に読み、**同じ時刻のイベントを1つの判断時点にまとめて**次を行う。

1. rank 0: 執行足の終了通知（`ExecutionBarComplete`）があれば、その足について保護水準の到達判定を行う（第7.3節）。
2. rank 1: 1 で生じた約定を台帳へ適用する（第8.1節）。
3. rank 2: `expires_at <= T` の PENDING 注文を EXPIRED にする。
4. rank 3: `available_at = T` の `Publication` と、`bar_end = T` の `ScheduledBoundary` から `PublicationBatch` を組み立てる（第4.3節）。
5. rank 4〜9: **第1回の `step(batch)`** を呼ぶ。戻り値の `RuntimeStepResult` から `outputs` を `OutputSink` 経由で trace へ、`evaluations` と `transitions` を trace へ、`proposals` と `management_requests` を rank 10 へ渡す。
6. rank 10: 要求組立から受付までを行い、`AttemptDecision` と `AdmissionNotice` を作る（第6節）。
7. rank 11: 執行系列の始値処理を行う（第7.1節）。
8. rank 12: 6 の `AdmissionNotice` と 7 で生まれた `POSITION_OPENED` の通知があれば、**第2回の `step`** を呼ぶ。戻り値の `management_requests` を建玉へ適用する（第8.3節）。通知が1件もなければ呼ばない。
9. run_end の判断時点だけ rank 13 を行う（第10節）。

**戦略ランタイムを1つの判断時点で最大2回（run_end では最大3回）呼ぶ**【提案】。D05 §6.1 は同じ `batch_id` での再呼び出しを `KernelValueError` で拒むため、2回目は**新しい `EventId` を持つ別の `PublicationBatch`** として渡す。`decision_time` は同じ T、`available_bars` と `scheduled_closes` は空、`runtime_events` と `admissions` に通知を入れる。**不採用**: 受付結果を次の判断時点まで持ち越す案（取引機会が `ORDER_PENDING` のまま次の足へ渡り、同時保持上限の数え方が判断時点をまたいで変わる）、受付結果用に別のポート操作を足す案（D05 §6.1 の `StrategyRuntime` は `step` 1操作であり、入口が2つになると呼び出し順の規則がもう1本要る）。

### 4.3 公開イベントから `PublicationBatch` への変換【提案】

`strategy` は `marketdata.application` を参照できない【合意済み】D01 §3.2（契約 F2・F6）。そこでエンジンが D03 §7.1 のイベントを D05 §6.1 の型へ変換する。

| D03 のイベント | `PublicationBatch` の項目 | 変換規則 |
|---|---|---|
| `Publication(series, bar_key, available_at)` | `available_bars` | `available_at = T` のものを `bar_key` の列にする。並びは D03 §7.1 の系列順（`(symbol, 名目長の降順, basis)`）に従う |
| `ScheduledBoundary(series, bar_key, bar_end)` | `scheduled_closes` | `bar_end = T` のものを `BarClosure(bar_key, interval)` にする。`interval` は `TimeframeDefinition.expected_interval`（D03 §3.2）が返す**実際の区間**で、名目の長さから再計算しない |
| `ExecutionOpen` / `ExecutionBarComplete` | 渡さない | 執行モデルだけが消費する【合意済み】D03 §7.3 |

`BarClosure.interval` に実際の区間を入れるのは、夏時間の切替日や短縮セッションで区間が名目と異なり、取引機会の対象区間（`signal_interval`）がそこから決まるためである【合意済み】D05 §6.1。

`batch_id` は `IdAllocator.next(EventId)` で採番する。再配送される同一通知は同じ `EventId` を持つ【合意済み】ADR-0006。

### 4.4 原子性と冪等性【提案】

「審査・予約・受付を不可分に確定する」【合意済み】上位 §4.7.4・全体計画 §5.4.2 を、frozen dataclass の設計で実現する方法を決める。

- 台帳の現在状態は `AccountLedger` 1つの**不変値**で表し、エンジンは可変参照を1つだけ持つ（第1節の例外）。
- **確定単位**は「検査 → 新しい `AccountLedger` の組み立て → 参照の差し替え」の3段で行い、差し替えが起きるまで外へ公開しない。差し替えは1文であり、途中まで更新した状態は観測できない。
- 1つの確定単位に含めるものを次のとおり固定する。`processed_event_ids` への追加を同じ単位に含めるのが冪等性の実装である【合意済み】上位 §4.7.13 A。

| 確定単位 | 含める変更 |
|---|---|
| 受付（第6.5節） | `OrderRequest` ／ `AttemptDecision` ／ `AcceptedOrder` ／ `OrderState(PENDING)` ／ `RiskReservation` ＋ `ReservationState(HELD)` ／ `event_id` |
| エントリー約定（第7.1節） | `FillRecord` ／ `Position`（作成、初期損切り有効化）／ `OrderState(FILLED)` ／ `ReservationState(TRANSFERRED)` ＋ `PositionRiskAllocation` ／ `RiskMeasurement` ／ `event_id` |
| 決済約定（第7.3節） | `FillRecord` ／ `Position`（終了）／ `OrderState(FILLED)` ／ 実現損益・費用・balance ／ `PositionRiskAllocation`（解放）／ `event_id` |
| 終端（期限・取消） | `OrderState(EXPIRED \| CANCELED)` ＋ `terminal_reason` ／ `ReservationState(RELEASED)` ／ `event_id` |

- 受付前拒否は台帳を変えない。`OrderRequest` と `AttemptRejected` を trace へ残す【合意済み】上位 §4.7.15 A（「受付前拒否もこの記録に紐付く」）。**`RiskAssessment` を残すのは、審査に実際に入れた拒否のときだけ**とする【提案】。期限内に候補が無い（`NO_CANDIDATE`）・末尾（`RUN_END`）・参照価格が取れない（`DATA_ERROR`）のように第6.4節の手順3より前で終わる拒否では、`ReferenceQuote` も丸め前後の価格も換算率も存在しないため、行を作れば架空の値を書くことになる。その場合 `AttemptRejected.assessment_ref` は `None` とし、拒否の理由は `Reason` だけが持つ。
- 同じ `event_id` が2度来たら、`processed_event_ids` に含まれることを見て**何もしない**（確定単位を実行しない）。異なる `event_id` で同じ終端効果を要求されたら、注文状態の検査（終端状態からの遷移は表に無い）で拒否する【合意済み】上位 §4.7.13 A。
- 確定単位の内部で検査に失敗した場合は、**差し替えを行わず** run を失敗させ、直前の整合状態と失敗診断を保存する【合意済み】上位 §4.7.13 A・C。部分的に約定した状態で継続しない。
- 採番順は `ProcessingPoint` の順に一致させる【合意済み】D02 §7.3。同一入力の再実行で同じ ID 列・同じ trace になることを再現性テストで検証する。段階2の許容誤差は**完全一致**とする【提案】（Decimal 演算と決定論的採番だけで構成され、浮動小数の非決定性が入らないため）。**不採用**: 金額に許容誤差を置く案（段階2で誤差が出るなら原因は非決定性であり、閾値で隠すべきではない）。

## 5. 注文（`domain.orders`）

### 5.1 注文の状態機械【提案】

受付済み注文の状態は `PENDING` / `FILLED` / `CANCELED` / `EXPIRED` の4つ【合意済み】上位 §4.7.13 A。受付前の拒否は発注試行の終端であり、注文を作らない。遷移は次の6本ですべてである。

| # | 遷移 | 発火条件 | フェーズ | 記録する理由 | 段階 |
|---|---|---|---|---|---|
| 1 | （受付）→ `PENDING` | 審査を通過し数量が確定し、エントリーなら予約を確保した | `ADMISSION` | なし（受付は `AttemptAccepted` に記録） | 2 |
| 2 | `PENDING` → `FILLED` | 適格な始値、または保護水準の到達で約定した | `EXECUTION_OPEN` ／ `EXECUTION_BAR_COMPLETE` | なし（決済の契機は `CloseCause`） | 2 |
| 3 | `PENDING` → `EXPIRED` | `expires_at <= T` | `ORDER_EXPIRY` | `EXPIRED` | 2 |
| 4 | `PENDING` → `CANCELED` | run 末尾に残った | `RUN_END` | `RUN_END` | 2 |
| 5 | `PENDING` → `CANCELED` | 実行中のデータ不整合で run が失敗した | 失敗を検出したフェーズ | `DATA_ERROR` | 2 |
| 6 | `PENDING` → `CANCELED` | 対象建玉が先に閉じた決済注文 | `EXECUTION_OPEN` ／ `EXECUTION_BAR_COMPLETE` | `POSITION_CLOSED` | 2（戦略が決済要求を出したときだけ） |

- 現在状態は `AcceptedOrder` を書き換えず、`OrderEvent` の列から `OrderState` へ投影する【合意済み】上位 §4.7.15 B。
- 終端状態からの遷移は表に無い。終端済みの注文への遷移要求は `KernelValueError` で拒否する【提案】（D05 §7.2 の取引機会と同じ扱いに揃える）。
- `ACCEPTED` は状態ではなく受付イベントであり、直後の状態は `PENDING`【合意済み】上位 §4.7.13 A。
- エントリー・戦略の決済要求・エンジンの緊急決済・保護水準の到達による決済は、**すべて同じ状態機械を通す**【合意済み】上位 §4.7.13 A。約定できる時点と会計処理だけが目的によって異なる。
- 遷移6の `POSITION_CLOSED` は D02 §8.1 に登録済みの語である【合意済み】。

### 5.2 発注試行と受付前拒否【提案】

- `AttemptId` は要求組立で採番し、1つの `OrderRequest` に1つ対応する【合意済み】上位 §4.7.15 A。
- 拒否は `AttemptRejected(attempt_id, reason, assessment_ref)` として記録し、注文も予約も作らない。同じ `attempt_id` で再試行しない【合意済み】同節。
- 段階2で使う拒否の理由コードは `RISK` / `NO_CANDIDATE` / `RUN_END` / `DATA_ERROR` / `CARRY_NOT_ALLOWED` の5件である【合意済み】D02 §8.1。**保護水準の妥当性違反**（買いの損切りが判断時の売却側価格以上など、上位 §4.7.9 B）に対応する語が無く、第16節 Q6 で決める。
- 受付前拒否は取引機会側の `ORDER_ATTEMPT_REJECTED` で終端させる【合意済み】D05 §7.2 の遷移6。エンジンは `AdmissionNotice(accepted=False, reason=...)` を返すだけで、機会の状態は変えない（第6.6節）。

### 5.3 有効期限と候補 open【提案】

- `expires_at = accepted_at.time + valid_for`（UTC 絶対時刻）【合意済み】上位 §4.7.13 B。`valid_for` は `ExecutionPolicy` の `entry_valid_for` / `close_valid_for` から取る。D05 §4.3(3) の `OrderIntent.expiry=None` は「この既定値に従う」を意味する【合意済み】D05 §12。具体値は第16節 Q5。
- 約定可能条件は `open_time < expires_at`。同時刻なら期限切れを先に確定する（rank 2 < rank 11）【合意済み】同節。
- 期限は足の到着に依存せず休場中も進む。受付後の延長は行わない【合意済み】同節。
- **受付時点で期限内に候補 open が無ければ受付前拒否**（`NO_CANDIDATE`）。候補はカレンダー（`backtest.application.ports` の `Calendar`）と執行系列の足スケジュールから決め、将来価格や実ファイルの欠損を候補選択に使わない【合意済み】同節。
- 候補が週末休場をまたぐ場合は `CARRY_NOT_ALLOWED` で受付前拒否する。有効時間を長くしても回避できない【合意済み】同節。

## 6. 受付（`admission`）

### 6.1 要求組立【提案】

`EntryProposal`（D05 §6.2）から `OrderRequest` を作る。

| `OrderRequest` の項目 | 値の出どころ |
|---|---|
| `run_id` | `RunConfig` から解決した `RunId` |
| `attempt_id` | 第6.3節の順に並べてから `IdAllocator.next(AttemptId)` |
| `previous_attempt_id` | 段階2は常に `None`（再審査なし）【合意済み】上位 §4.3.14 |
| `account_id` | `RunConfig.account` |
| `strategy_id` | `CompiledStrategy.strategy_ref.strategy_id` |
| `created_at` | `ProcessingPoint(T, ADMISSION, 連番)` |
| `origin` | 戦略由来は `STRATEGY`、保護到達・緊急決済は `ENGINE`。戦略が `ENGINE` を自己申告する経路は作らない【合意済み】上位 §4.7.15 A |
| `payload` | `EntryProposal` からは `EntryRequest`、`ManagementRequest(ClosePosition)` からは `CloseRequest` |
| `evidence_ref` | trace へ保存した根拠記録の参照（第9.2節） |

`EntryRequest` の各項目は `EntryProposal` から次のとおり埋める。`symbol` と `side` は `OrderIntent`、`protection.stop_loss` は `ProtectionLevels.stop_loss`、`exit_plan_ref` は `ExitPlanRef(compiled.compiled_ref, compiled.roles.exit の instance_id)`、`valid_for` は `OrderIntent.expiry` が `None` なら `ExecutionPolicy.entry_valid_for`。

**根拠の出力 ID をどこから取るかが、現在は解決できない**【提案】。上位 §4.7.8 は「注文意図・SL 算定について、使った出力 ID を記録する」と要求するが、D05 §3 の `EntryProposal` は `opportunity_id` / `order_intent` / `protection` / `decision_time` の4項目だけで `OutputId` を持たない。本書は `EntryRequest.intent_output_id` と `InitialProtectionPlan.source_output_id` を持つ形で設計し、**D05 に `EntryProposal` と `ManagementRequest` へ出力 ID を足す改訂を依頼する**（第15節）。**不採用**: trace の評価記録と出力記録を後から突き合わせて復元する案（同じ判断時点に同じ役割の出力が2件出た場合、機会 ID だけでは一意に決まらず、「名前だけ同じ指標の最新値を後から読み直して根拠を再構成しない」（上位 §4.7.8）に反する）。

### 6.2 段階2で組み立てる要求の種類【提案】

| 要求 | 出どころ | `origin` | `payload` |
|---|---|---|---|
| 新規エントリー | `RuntimeStepResult.proposals` | `STRATEGY` | `EntryRequest` |
| 戦略の全数量決済 | `ManagementRequest(ClosePosition())` | `STRATEGY` | `CloseRequest(STRATEGY_EXIT)` |
| 保護水準の到達による決済 | 第7.3節 | `ENGINE` | `CloseRequest(STOP_LOSS \| TAKE_PROFIT)` |
| 約定直後の緊急決済 | 第7.5節 | `ENGINE` | `CloseRequest(EMERGENCY)` |

`ManagementRequest(SetTakeProfit(price))` は注文ではなく**建玉の保護水準の更新**であり、この表に入らない（第8.3節）。検証戦略 A が出すのはこれだけである。

### 6.3 全順序化【提案】（上位 §4.7.12 の鍵の具体化）

因果的に準備できた要求の集合を作り、その中を全順序で処理する【合意済み】上位 §4.7.4。本書は鍵・向き・比較規則を確定する。

`AdmissionKey = (decision_time, request_class, strategy_priority, origin_seq, attempt_seq)` の**辞書式昇順**で処理する。

| 要素 | 向きと比較規則 | 理由 |
|---|---|---|
| `decision_time` | 早い方が先（`UtcTime` の昇順） | 因果順。未完成の上流を時間キーで追い越さない【合意済み】上位 §4.7.4 |
| `request_class` | `CLOSE`（0）が `ENTRY`（1）より先 | 決済は新規リスク予算の審査対象ではなく（上位 §4.7.15 A）、同じ判断時点でエントリーと予算を奪い合わない。先後を決めておかないと `max_active` 到達時の挙動が実装依存になる |
| `strategy_priority` | **小さい値が先**（昇順）。段階2は単一戦略で常に 0 | 「1が最優先」という自然な読み方に合わせる。設定値の意味は D10 |
| `origin_seq` | エントリーは `opportunity_id.seq`、決済は `position_id.seq` の昇順 | 上位 §4.7.12 の鍵は `opportunity_id` だが、決済要求は機会を持たない。同じ位置に「発端となった対象の連番」を置いて型を揃える |
| `attempt_seq` | `attempt_id.seq` の昇順 | 最終的な一意化 |

- **ID の比較は連番 `seq`（`int`）で行い、文字列表現では比較しない**【提案】。D02 §7.2 の `__str__` は8桁ゼロ詰めであり、99,999,999 を超えると文字列順と数値順が食い違う。
- **`AttemptId` は順序を決めた後に採番する**【提案】。鍵に `attempt_seq` が入るのに採番が先だと循環する。先に `(decision_time, request_class, strategy_priority, origin_seq)` で並べ、その順に `IdAllocator.next(AttemptId)` を採番するため、`attempt_seq` は常に同順位内の決着にだけ効き、採番順と処理順が一致する。
- 審査・予約・受付は**逐次確定**し、次の要求は更新済みの台帳を見る【合意済み】上位 §4.7.4。
- **不採用**: 決済を後に回す案（決済で枠が空くのは約定時であり順序を変えても予算は増えないが、受付済み注文の数の上限に両者が同時に当たる構成で挙動が読みにくくなる）、`strategy_priority` を降順にする案（段階2で観測できず、D10 で設定値を足すときに読み替えが要る）。

### 6.4 リスク審査と数量決定【提案】

式と数値は上位 §4.7.9 C・§4.7.10 で確定済み【合意済み】。本書が足すのは**適用の順**と、各段で記録する値である。

1. 受付判断時の `balance` を読む。`B <= 0` または口座状態を検証できなければ拒否（`RISK`）。
2. `trial_budget = B × 0.02`、`account_remaining = max(0, B × 0.20 − U)`、`admission_budget = min(...)`。`U` は HELD 予約額と未解放の建玉割当額の合計（`TRANSFERRED` の予約は加算しない）。
3. 参照価格を固定する。買いは ask、売りは bid。初版データは bid のみのため、ask は `SpreadModel` から導き、`ReferenceQuote.derived_from_spread=True` を立てる【合意済み】上位 §4.7.9 C。
4. 保護水準の妥当性を検査する。買いの損切りは判断時の bid より下、売りは ask より上。違反・不正数値・価格情報の不足は拒否（理由コードは Q6）。
5. 損切りを価格刻みで丸める（第6.5節）。`P_limit = P_ref + d × Δ`、`R(Q) = d × (P_limit − S) × Q × X + C(Q)`。`d × (P_limit − S) > 0` を要求する。
6. `R(Q) <= admission_budget` を満たす最大の数量を数量刻みで**切り下げ**て求める。最小数量未満なら拒否（`RISK`）。切り上げない。
7. 丸め後の数量で `R(Q)` を再計算し、口座制約（総量・数量上限）を再検査する。
8. すべての段の入力と結果を `RiskAssessment` に残す。`assessment_id` は `IdAllocator.next(EvidenceId)` で採番し、`RiskAssessmentRef` はこの値だけを持つ（参照と実体で識別子を二重に持たない）。`attempt_id` を `RiskAssessment` 自身にも持たせるのは、拒否された試行でも審査の記録から試行へ戻れるようにするためである。`checks` には各検査の名前・上限・観測値を `RiskCheckResult` で入れる。D02 §8.2 の `RiskRejectionDetail` は `limit` と `observed` の型一致を要求するため、同じ組で作る。

段階2はレバレッジ・証拠金の検査を行わない【提案】（ADR-0015 の縦断範囲に証拠金モデルが無く、検査に使う値が存在しないため）。`checks` に「未実施」を入れず、検査そのものを持たない。**不採用**: 仮の証拠金率を置いて検査する案（実験前に固定すべき値を設計が勝手に決めることになる）。

### 6.5 丸めの方向と適用時点【提案】（D05 §1.2 の行7 の引き受け）

D02 §4.3 は機構（`round_to_tick(tick, direction)`）だけを提供し、方向の選択は本書の責務である【合意済み】。

| 対象 | 方向 | 適用時点 | 理由 |
|---|---|---|---|
| 初期の損切り水準 | 買いは `DOWN`、売りは `UP` | `ADMISSION`（審査の前、上記の手順5）。以後は凍結 | 意図より近い損切りへ黙って変更しない【合意済み】上位 §4.7.3 |
| 利確水準 | 買いは `DOWN`、売りは `UP` | `POST_FILL_EVALUATION`（管理要求を建玉へ適用する時点、第8.3節） | 利益を上方に丸めない。丸め後の実リスクリワード比を記録する【合意済み】同節 |
| 数量 | `round_down_to_step`（切り下げのみ） | `ADMISSION`（手順6） | 切り上げてリスク上限を超えさせない【合意済み】同節 |
| 許容不利約定幅 Δ | 値幅を**広げない**向き（絶対価格幅として `DOWN`） | `ADMISSION`（手順5） | 有利に縮めない【合意済み】上位 §4.7.9 C |

刻みの値は `SymbolSpec`（D02 §5.2）の `price_tick` / `quantity_step` / `min_quantity` から取り、`SymbolSpecRef` で版を固定する。丸め前の値・規則・丸め後の値を `RiskAssessment` に残す【合意済み】ADR-0012。

### 6.6 受付結果の通知【提案】（D05 §12 の引き受け）

D05 §7.2 の遷移5〜7 は、エンジンからの `AdmissionNotice` を次の `step` の入口で適用する【合意済み】。

- 通知を作るのは `ADMISSION` フェーズ、配送するのは同じ判断時点の `POST_FILL_EVALUATION` フェーズ（第4.2節）とする【提案】。これにより、受付・拒否から取引機会の終端までが同じ判断時点で閉じ、次の足へ `ORDER_PENDING` のまま持ち越さない。
- `AdmissionNotice(opportunity_id, attempt_id, accepted, reason)` は**エントリー要求についてだけ**作る。決済要求は取引機会を持たないため通知しない。
- `accepted=True` の通知が1件でもあれば、`on_order_accepted=CLOSE_OTHERS` の判定材料になる（D05 §7.2 の遷移7）。**その判定を行うのはランタイムであり、エンジンは他の機会を終わらせる指示を出さない**【合意済み】D05 §7.2。
- 受付前拒否では `reason` に第6.4節の `Reason` をそのまま入れる。ランタイムはそれを機会の終端理由に読み替えず、`ORDER_ATTEMPT_REJECTED` を使う【合意済み】D05 §7.2 の遷移6。

## 7. 執行（`execution`）

### 7.1 成行の候補と約定【提案】

**方式は確定済み**: 受付後、因果順序上まだ到来していない最初の執行足の始値で約定する【合意済み】上位 §4.7.11。本書が足すのは候補の固定と `entry_delay_bars` の適用である。

- 受付時に候補を `ExecutionCommitment.eligibility = ScheduledOpen(bar_key, open_time)` として**固定**する。欠損時に後続の足へ置換しない【合意済み】上位 §4.7.15 B。
- `entry_delay_bars = 1` のときは、最初の適格 open を1回見送り、次の執行足の始値を候補にする。**新規エントリーにだけ適用し**、決済要求・保護水準の到達判定・約定直後の緊急決済には適用しない【合意済み】上位 §4.7.12。見送った分だけ予約は保持し、有効期限は延長しない。候補足の欠損を1足見送りとして数えない。
- 約定価格の基準は買いが ask、売りが bid【合意済み】上位 §4.7.11・§4.7.12。bid のみの系列では `SpreadModel` で ask を導く。**slippage は注文の目的で使い分け、常に不利な方向へ適用する**【提案】。

| 目的 | 基準価格 | 適用する slippage | 不利な方向 |
|---|---|---|---|
| 新規エントリー（買い） | 対象 open の ask | `entry_slippage` | 価格を上げる |
| 新規エントリー（売り） | 対象 open の bid | `entry_slippage` | 価格を下げる |
| 決済（買い建玉を閉じる＝売り） | bid | `close_slippage` | 価格を下げる |
| 決済（売り建玉を閉じる＝買い） | ask | `close_slippage` | 価格を上げる |

決済側の規則は、戦略の全数量決済・保護水準の到達による決済（第7.3節）・始値の gap による損切り決済と約定直後の緊急決済（第7.5節）の**すべて**に同じく適用する。基準価格は、始値で約定する決済は対象 open の価格、保護水準の到達による決済はその保護水準とする。ただし始値が既に保護水準を越えている場合は始値を基準にし、**到達不能な保護水準の価格で約定させない**【合意済み】上位 §4.7.12。`entry_slippage` を決済に流用すると、設定が異なるときに決済価格・実現損益・MTM がすべてずれる。
- `FillRecord.execution_time` は始値約定なら `ExactExecutionTime(open_time)`。
- 約定ずれの判定は `max(0, d × (P_fill − P_ref)) > Δ` で行い、上限一致は許容する【合意済み】上位 §4.7.9 C。超過しても約定は記録し、第7.5節へ進む。
- `entry_delay_bars` の初版値は第16節 Q3、Δ の初版値は Q4。

### 7.2 spread モデル【提案】

段階2の受入れデータは bid のみである【合意済み】D03 §3.1。`FixedSpread(offset)` だけを持ち、`ask = bid + offset` とする。spread を価格にも費用にも重複して加算しない【合意済み】上位 §4.7.9 C。`offset` の値は `ExecutionPolicy` ではなく `CostModel` に置き、実験前に固定する。可変 spread・時間帯別 spread は段階6（第12節）。

### 7.3 保護水準の到達判定【提案】

- 判定は `EXECUTION_BAR_COMPLETE` フェーズで、**その足の開始前に有効だった**保護水準について行う【合意済み】上位 §4.7.7。終値で計算した更新をその足の過去の高値・安値へ適用しない。
- **新規建玉の初期保護水準は、約定した執行足の開始時点から有効**とする【提案】。`ProtectionState.effective_from` に約定した足の `BarKey` を入れ、その足の到達判定の対象に含める。上位 §4.7.7 が「新規約定直後の初期 SL/TP に既存建玉の規則を一律適用してはいけない」と述べているのはこの点であり、`effective_from` を持つことで既存建玉（次の執行足から有効）と新規建玉（約定した足から有効）を同じ1つの規則で扱える。**不採用**: 新規建玉だけ別の判定経路を作る案（同じ到達判定が2か所になる）。
- 判定価格は買い建玉が bid、売り建玉が ask【合意済み】上位 §4.7.12。
- 到達したら、エンジンが `CloseRequest(cause=STOP_LOSS | TAKE_PROFIT)` → `AcceptedOrder`（`eligibility=ProtectionHit(...)`）→ `FillRecord` を生成する【合意済み】上位 §4.7.15 B。通常の候補 open 規則は通さない。
- `FillRecord.execution_time` は `BarExecutionInterval(bar_key, interval)`。足内の正確な到達時刻を観測できないため、終値時刻を到達時刻として記録しない【合意済み】同節。
- 片側だけに触れた場合は `ResolutionMethod.SINGLE_HIT` として記録し、両側に触れた場合は第7.4節へ進む。

### 7.4 足内競合解決契約【提案】（ADR-0030 の実施）

契約そのものは確定済み【合意済み】ADR-0030・上位 §4.7.7。本書は宣言形・検査・手順・記録・能力検査を埋める。

**宣言形**: `ResolutionHierarchy(levels: tuple[SeriesId, ...])` を粗い順に持ち、`levels[0]` は `RunConfig.execution_series` と一致しなければならない。置き場所は第16節 Q8。

**適合検査**（run 開始前。`DataCapabilityReport.hierarchy_checks` に結果を残す）:

| # | 検査 | 不合格のとき |
|---|---|---|
| 1 | 隣り合う階層で、下位足が上位足の区間を**完全に被覆**する（区間の和が親の区間に等しく、隙間も食み出しもない） | 実行不可 |
| 2 | 価格基準（`PriceBasis`）が階層内で一致する | 実行不可 |
| 3 | 下位足の境界が親足の境界に整列する（親の `interval` の両端が子の境界と一致する） | 実行不可 |
| 4 | 下位足の `available_at` が親足の `available_at` 以下である（親を解決する時点で子が見えている） | 実行不可 |
| 5 | run 区間と銘柄について、階層の各系列が snapshot に欠損なく存在する（D03 §3.9 の `IntegrityReport`） | 実行不可 |

**不足時は実行不可とし、暗黙に親足の4本値へ落とさない**【合意済み】ADR-0030。検査1〜5 は「下位足が宣言されている場合」にだけ走る。階層が1段だけなら検査は5だけになる。

**解決の手順**:

1. 親足で損切りと利確の両方に触れたら、階層の次の解像度で親足の区間を覆う子足を**時系列順**に走査する。
2. 片方だけに触れる最初の子足が見つかれば、その側を採用し `RESOLVED_BY_CHILD` として記録する。走査はそこで打ち切る。
3. 同じ子足で両方に触れたら、その子足を親としてさらに次の解像度へ降りる（再帰）。
4. 最小解像度でもなお両方に触れて順序が観測できなければ `UNRESOLVED_SL_PRIORITY` とし、**損切りを採用**する。
5. どの経路でも `IntrabarResolution` を1件残し、`FillRecord` と run manifest から辿れるようにする。

**記録項目**: `IntrabarResolution(position_id, parent_bar_key, method, series_used, resolved_child_bar_key, verdict, fill_id)`。run manifest には `resolution_hierarchy` と、`UNRESOLVED_SL_PRIORITY` の件数・全競合に対する割合を入れる【合意済み】ADR-0030。

**段階2の帰結**: ADR-0015 の縦断範囲では執行足が15分足で、それより下位の解像度は引き渡されていない。したがって段階2の階層は1段であり、**15分足の中で両方に触れた場合は常に `UNRESOLVED_SL_PRIORITY`** になる【提案】。損切り優先が「既定の規則」ではなく「階層を降りきった結果の裁定」であることは、記録の `method` から読める。人工データの生成器には、子足で解決できる場合と `UNRESOLVED` になる場合の両方を作る【合意済み】ADR-0030。

### 7.5 gap・約定ずれ超過・緊急決済【提案】

`EXECUTION_OPEN` フェーズの中の順序を固定する【合意済み】上位 §4.7.12。

1. 既存建玉について、始値が保護水準を飛び越えていれば、同じ始値で通常の損切り決済として処理する（買いは open bid、売りは open ask に決済側の slippage を適用）。到達不能な価格では約定させない。
2. 適格な成行注文を約定させる（第7.1節）。
3. 新規建玉を初期化し、初期の損切り水準を有効化する。
4. 新規約定の直後に損切り水準を越える gap が成立していれば、**同じ始値で通常の損切り決済**として処理する。分類は緊急決済ではなく損切り到達。
5. gap が無く、約定ずれが Δ を超えていれば、同じ始値で緊急決済（`CloseCause.EMERGENCY`、`eligibility=ImmediateAfterFill(...)`）を実行する。
6. 4 と 5 が同時に成立する場合は 4 を実行し、約定ずれ超過は診断として残すが2件目の決済を実行しない【合意済み】上位 §4.7.12。

- エントリー約定は必ず記録し、「無かったこと」にしない【合意済み】同節。
- 閉鎖状態と `PositionId` で二重決済を防ぐ【合意済み】同節。
- **同じ始値で決済された建玉については `POSITION_OPENED` を配送しない**【提案】。利確水準を設定する対象が残っていないためで、上位 §4.7.12 が「初期 TP 算定省略等は明示した初期化分岐」と呼ぶ分岐がこれに当たる。約定と決済の順序は trace に残る。**不採用**: 通知してからランタイムの管理要求を破棄する案（閉じた建玉への要求の拒否（`POSITION_CLOSED`）が、正常な初期化分岐の中で常時発生することになり、異常の診断と区別できなくなる）。
- 約定直後**以外**の時点での口座保護処理（強制縮小）の実行時点は対象外（第12節）【合意済み】全体計画 §7.4。

### 7.6 費用モデル【提案】

- `CostModel` は手数料・slippage・spread だけを扱い、**swap / rollover は計上しない**【合意済み】ADR-0029。`swap_modeled` は段階2で常に `False` とし、`BacktestResult` と run manifest に記録して、未計上であることが結果から読めるようにする。政策金利差による近似も行わない。
- 価格に反映済みの費用（slippage・spread）を金額として二重計上しない【合意済み】全体計画 §5.4.3。記録のために `CostKind.SLIPPAGE_IN_PRICE` の `CostEntry` を残すが、`amount` は balance に反映させない参考値であることを `CostEntry` の区分で表す。
- `CostEntry` は原通貨額・口座通貨計上額・換算根拠を持つ【合意済み】上位 §4.7.15 C。
- 費用予算 `C(Q)`（予約に含める分）は往復手数料と損切り決済の slippage とし、Δ に含めたエントリーの slippage を重複加算しない【合意済み】上位 §4.7.9 C。

## 8. 口座・建玉（`portfolio`）

### 8.1 台帳【提案】

- `balance` は実現損益・費用を反映した口座残高で、未実現損益を含めない。`equity` は含み損益込みの MTM 資産【合意済み】上位 §4.7.10。予算の分母は `balance`。
- 消費済み枠 `U` = HELD の予約額 ＋ 未解放の建玉割当額。`TRANSFERRED` の予約は加算しない【合意済み】上位 §4.7.15 D。
- 受付時に確定した枠を決済まで保持し、約定価格・残高変化で再計算しない【合意済み】上位 §4.7.4。約定時の実リスクとの差は `RiskMeasurement` として別に記録する。
- 台帳 snapshot（`LedgerSnapshot`）は、各判断時点の `LEDGER_UPDATE` と `EXECUTION_OPEN` の後に1件ずつ残す【提案】。約定の前後で残高と枠がどう動いたかを追えるようにするためで、フェーズごとに全件残すと段階2の trace が判断回数の13倍になる。**不採用**: 全フェーズで残す案（記録量に見合う情報が無い）、run 末尾だけ残す案（手計算との照合ができない、全体計画 §8.2 の完了条件）。

### 8.2 建玉と保護水準の管理権【提案】

- 約定時に建玉を作り、保護水準の管理権を**単一の Exit 部品**へ移す【合意済み】上位 §4.7.6。`ProtectionState.owner_instance_id` に `CompiledRoles.exit` の使用箇所 ID を入れる。`None` は Exit を持たない戦略。
- 複数の更新元を同じ建玉へ接続した構成は設計エラーであり、コンパイル時に拒否される（`exit` は `OutputRef` 1件のみ）【合意済み】D04 §11.3。
- 段階2は同時1建玉・全数量決済。部分決済・増し玉は対象外（第12節）【合意済み】上位 §4.7.1。
- 1建玉の制限は「建玉が無いこと」だけで判断せず、**受付済みの未約定エントリー注文による枠の占有も含める**【合意済み】上位 §4.7.12。段階2の実装は、受付時に「未終端のエントリー注文数 ＋ 開いている建玉数 < 1」を検査し、違反は `RISK` で受付前拒否する。

### 8.3 管理要求の適用【提案】

`ManagementRequest`（D05 §6.2）を建玉へ適用する規則。

| 要求 | 適用 | 検査 |
|---|---|---|
| `SetTakeProfit(price)` | `ProtectionState` の `take_profit` を設定し `version` を1増やす | 価格刻みへ丸め（第6.5節）、買いなら `entry < take_profit`、売りなら `take_profit < entry`。丸め後の実リスクリワード比を記録する |
| `ClosePosition()` | `CloseRequest(STRATEGY_EXIT)` を組み立てて受付へ回す（第6.2節） | 対象建玉が開いていること |

- 適用フェーズは `POST_FILL_EVALUATION`。更新した保護水準は `effective_from` を**次の執行足**にする（新規建玉の初期保護水準だけが約定した足から有効、第7.3節）。
- 同じ建玉への更新と決済要求が同時なら決済を優先し、更新は理由を記録して破棄する【合意済み】上位 §4.7.6。
- 閉じた建玉への要求は `POSITION_CLOSED` で拒否し、run 全体は止めない【合意済み】同節。
- 損切り水準の更新（トレーリング）は段階3（第12節）。段階2の `ManagementAction` に `UPDATE_STOP` は無い【合意済み】D04 §11.2。

### 8.4 `position_context@v1` と `account_context@v1` の項目【提案】（D04 §5・D05 §4.2 の引き受け）

**クラスの置き場所は `strategy.records.payloads`**【提案】。この payload を読むのは `strategy.catalog` の部品（D05 §4.3(5) の `fixed_rr_take_profit`）であり、`strategy` は `backtest` を参照できない【合意済み】D01 §3.2。したがって型そのものを `backtest.domain` に置くことはできない。**項目を決めるのは本書**（エンジンが評価時点に供給してよい情報の範囲そのものだから、D04 §5 が本書へ委ねた）だが、**クラスは `strategy.records` に置き、`backtest.engine` が `RuntimeContextView` の実装としてその型の値を作って渡す**。**不採用**: `backtest.domain` に置いて部品から参照する案（依存規則違反）、`object` のまま渡して部品が属性名で読む案（D05 §6.2 の戻り値検査が実行時クラスを照合できない）。

`PositionContext`（`position_context@v1`）:

| 項目 | 型 | 供給の根拠 |
|---|---|---|
| `position_id` | `PositionId` | どの建玉についての評価かを部品が確認できる |
| `symbol` | `Symbol` | — |
| `direction` | `TradeDirection` | D04 §5 が挙げた項目 |
| `quantity` | `Quantity` | 同上 |
| `entry_price` | `Price` | 同上。固定リスクリワード比の起点 |
| `effective_stop_loss` | `Price` | **D05 §11 の差異1 が要求した項目**。リスク幅 `entry − sl` の計算に要る |
| `effective_take_profit` | `Price \| None` | 既に利確が設定済みかを部品が判別できる（段階3の再設定の抑止に要る） |
| `opened_at` | `ProcessingPoint` | 保有期間を読む部品（段階3の期間 Exit）の前提 |

`direction` に `TradeDirection`（D05 §3）を使い台帳側の `OrderSide` を渡さないのは、部品が読むのは戦略側の語彙であり、`OrderSide` は「買い建玉を決済する売り注文」のように建玉の方向と一致しない場面があるためである【提案】。対応は `LONG ↔ BUY`・`SHORT ↔ SELL` の1対1で、エンジンが `RuntimeContextView` の実装で変換する。

`AccountContext`（`account_context@v1`、段階2では使わない）:

| 項目 | 型 |
|---|---|
| `account_id` | `AccountId` |
| `currency` | `CurrencyCode` |
| `balance` | `Money` |
| `equity` | `Money` |
| `consumed_risk` | `Money` |

- 未約定注文の一覧・状態は**どちらにも含めない**【合意済み】上位 §4.7.12。`RuntimeInputRef(PENDING_ORDER)` は段階2の能力検査で拒否される【合意済み】D05 §5.2。
- `RuntimeContextView.position_context(at, position_id)` は、`position_id` が `None` なら開いている唯一の建玉を返す。開いている建玉が無ければ `None` を返し、ランタイムは `on_missing` に従う【合意済み】D05 §6.3。
- 読み取り時点 `at` より後に確定した値を含めない（先読みの禁止）。段階2の項目はいずれも約定または管理要求の適用で確定済みの値である。

### 8.5 通貨換算【提案】＋【要決定】（Q7）

- 換算は**判断時点で利用可能な系列**から取り、不足時は拒否する【合意済み】全体計画 §5.4.4。将来の換算率を使わない。
- 換算率と観測時点は `ConversionRate`（D02 §4.5）で保持し、`RiskAssessment` と `CostEntry` に残す【合意済み】上位 §4.7.9 C。
- 適用時点は、予約額の計算（`ADMISSION`）、費用の口座通貨計上（約定の確定単位）、MTM 評価（`LEDGER_UPDATE` と末尾）の3か所【提案】。
- **段階2は換算が不要**である。ADR-0015 の縦断範囲は USDJPY・JPY 口座で、決済通貨（JPY）と口座通貨（JPY）が一致するため `ConversionRate` は率1の恒等換算になる。恒等換算でも経路を通すのは、段階3以降で経路を足すときに呼び出し側が変わらないようにするためである。
- **クロス通貨の換算経路は第16節 Q7 で決める。**

## 9. 記録（`trace`）

### 9.1 保存形式と書き出し【提案】

- 表形式データは Parquet、manifest は JSON【合意済み】ADR-0027。保存先は `runs/<run_id>/`。
- 書き出しは `TraceSink`（`backtest.application.ports`）経由で、実装は `evaluation.adapters` または `app` が持つ【合意済み】D01 §4。`backtest` は polars も Parquet も直接触らない。
- **行は平坦化して保存する**【提案】。各表の列は本書・D05 の型のフィールドに1対1で対応させ、入れ子の値は次の規則で開く。`ProcessingPoint` は `*_time` / `*_phase` / `*_sequence` の3列、`Reason` は `*_reason_code` と `*_reason_detail`（正規化エンコード文字列、D02 §9.3）の2列、`Money` は `*_amount`（文字列）と `*_currency`、`Decimal` と `Price` と `Quantity` は文字列、`UtcTime` は D02 §3.1 の文字列、ID 型は `__str__`。Decimal を浮動小数として保存すると再現性が壊れるため文字列にする【合意済み】ADR-0012。
- 全行が `run_id` を持つ【合意済み】上位 §4.7.15。

### 9.2 trace の行の種類【提案】

段階2で書き出す表は次の15件。待機記録と追い越し記録は段階3（第12節）。

| # | `TraceTable` | 正本の型 | 主キー | 辿れる先 |
|---|---|---|---|---|
| 1 | `OUTPUTS` | `OutputRecord`（上位 §4.3.15） | `output_id` | `evaluation_id` |
| 2 | `EVALUATIONS` | `EvaluationRecord`（D05 §3） | `evaluation_id` | `request_id` / `opportunity_id` / `position_id` |
| 3 | `OPPORTUNITY_TRANSITIONS` | `OpportunityTransition`（D05 §3） | `(opportunity_id, at)` | `counterpart` / `attempt_id` |
| 4 | `ORDER_REQUESTS` | `OrderRequest`（上位 §4.7.15 A） | `attempt_id` | `opportunity_id`（エントリー）/ `position_id`（決済）/ `intent_output_id` |
| 5 | `ATTEMPT_DECISIONS` | `AttemptDecision` | `attempt_id` | `order_id`（受付時のみ）/ `assessment_ref` |
| 6 | `RISK_ASSESSMENTS` | `RiskAssessment` | `assessment_id` | `attempt_id` |
| 7 | `ORDERS` | `AcceptedOrder` | `order_id` | `attempt_id` / `reservation_id` |
| 8 | `ORDER_EVENTS` | `OrderEvent` | `event_id` | `order_id` / `fill_id` |
| 9 | `FILLS` | `FillRecord` | `fill_id` | `order_id` / `position_id` |
| 10 | `RESERVATIONS` | `RiskReservation` ＋ `ReservationState` | `reservation_id` | `order_id` / `allocation_id` |
| 11 | `POSITIONS` | `Position` ＋ `PositionRiskAllocation` ＋ `RiskMeasurement` | `position_id` | `entry_fill_id` / `close_fill_id` |
| 12 | `MANAGEMENT_APPLICATIONS` | `ManagementRequest`（D05 §3）＋ 適用結果 | `(position_id, at)` | `position_id` / 元の `output_id` |
| 13 | `INTRABAR_RESOLUTIONS` | `IntrabarResolution` | `fill_id` | `position_id` / `parent_bar_key` |
| 14 | `LEDGER_SNAPSHOTS` | `LedgerSnapshot` | `at` | `open_position_ids` |
| 15 | `EVIDENCE` | `EvidenceRecord` | `evidence_id` | `output_ids` / `evaluation_ids` / `attempt_id` / `position_id` / `market_refs` |

**ID 連鎖で辿れること**（機会 → 試行 → 注文 → 約定 → 建玉 → 管理要求、および予約）を、表3・4・7・9・11・12・10 の外部キーで満たす。**受付前拒否でも連鎖が切れない**のは、`OrderRequest` を表4として必ず保存するためである（上位 §4.7.15 A が「受付前拒否もこの記録に紐付く」と定めている）。`AttemptRejected` は `AcceptedOrder` を作らないため、発端の取引機会は表4の `opportunity_id` からだけ辿れる【合意済み】全体計画 §5.4.5。**根拠記録は表15 `EVIDENCE` に置く**【提案】。`EvidenceRef`（D02 §9.2）は `evidence_id: EvidenceId` だけを持ち、表1・2・4 の主キーは `OutputId` / `EvaluationId` / `AttemptId` であるため、`EvidenceRef` から直接それらの行を引くことはできない。そこで上位 §4.7.15 が定める根拠記録の内容（入力の出力 ID、市場データの snapshot・系列・区間・項目、読取時点、口座 snapshot、使用した設定の版）を `EvidenceRecord` として型付きで保持し、**そこから表1・2・4・12・14 へ自然キーで辿る**。`EvidenceId` の採番は `backtest.trace`【合意済み】D02 §7.1。`RiskAssessment.assessment_id` も同じ `EvidenceId` の採番列から取り、審査記録それ自体が1件の根拠記録であることを表す。**不採用**: 表1・2 の行に `EvidenceId` を足す案（`OutputRecord` と `EvaluationRecord` は D05 が正本であり、記録の都合で戦略側の型にエンジン側の識別子を足すことになる）、自由記述のログにする案（型付きで辿れない記録が増え、上位 §4.7.15 の「説明文だけのログではない」に反する）。

### 9.3 run manifest【提案】

JSON。項目は次のとおり【合意済み】全体計画 §5.4.5 を具体化する。

| 群 | 項目 |
|---|---|
| 識別 | `run_id`、`config_digest`、`code_digest`、`lock_digest`、`env_digest`、git commit と dirty 状態（識別子には含めない、ADR-0006） |
| 入力 | `snapshot_ref`、`strategy_ref`、`compiled_ref`、`run_interval`、`execution_series`、`seed`、**`account`（`AccountSpec` の3項目。特に `initial_balance`）** |
| ポリシー | `risk_policy_ref`、`execution_policy_ref`（`entry_delay_bars`・Δ・有効時間を含む）、`cost_model_ref`（`swap_modeled=False`）、`delay_scenario_ref`、`symbol_spec_ref`、`calendar_ref`、`timeframe_def_refs` |
| 実行の構造 | `BACKTEST_PHASES` のフェーズ集合（D02 §3.3 の要求）、`IdAllocator.snapshot()`（D02 §7.3） |
| 足内競合 | `resolution_hierarchy`、`UNRESOLVED_SL_PRIORITY` の件数と割合（ADR-0030） |
| 能力検査 | `DataCapabilityReport` の要約 |
| 状態 | `RunStatus`、失敗時の `Reason` |

`ConfigDigest` の対象は「識別」を除く上表の**入力とポリシーの群**とする【提案】（D02 §9.2 が「項目は D06」と委ねた範囲）。口座仕様（`AccountSpec`）を入力群に含めるのは、初期残高だけを変えた実行は数量・損益・資産推移がすべて変わるのに、含めないと同じ `ConfigDigest` と `RunId` になり、別の結果が同じ `runs/<run_id>/` を指すためである。`RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`【合意済み】ADR-0006。

### 9.4 `BacktestResult`【提案】

評価基盤へ渡す正規化 DTO。評価側はこれを改変しない【合意済み】全体計画 §5.4.5。

| 項目 | 内容 |
|---|---|
| `run_id` / `manifest_ref` | run manifest への参照 |
| `status: RunStatus` | 正常完走か失敗か |
| `trace_tables: Mapping[TraceTable, str]` | **第9.2節の15表すべての Parquet パス**。D07 は必要な表をここから開く |
| `balance_series` / `equity_series` | `LEDGER_SNAPSHOTS` から導いた推移 |
| `summaries: FinalSummaries \| None` | 末尾3集計（第10.3節）。**`status` が `COMPLETED` のときだけ非 `None`** |
| `swap_modeled: bool` | 常に `False`（ADR-0029）。D07 がユーザーへの明記に使う |
| `unresolved_intrabar_count: int` | `UNRESOLVED_SL_PRIORITY` の件数（ADR-0030） |
| `trade_count` / `opportunity_count` | 完了取引数と生成された取引機会の総数（`status` の検証と手計算の照合に使う最小の件数） |

**集計前のレコードは `trace_tables` 経由で渡し、`BacktestResult` の中で集計しない**【提案】。取引機会の終端理由別・評価見送りの診断理由別の集計は D07 の責務であり（第1.2節の行6）、件数に畳んだ値だけを渡すと D07 が集計規則を持てず、集計が両方の文書に割れる。そのため表3（取引機会の遷移）と表2（評価記録）を含む全表のパスを公開する。**不採用**: 必要な表だけを選んで公開する案（D07 が指標を足すたびに D06 の DTO を変えることになる）。

**失敗した run の結果を正常完走の結果と同じ扱いにしない**【合意済み】上位 §4.7.13 C。`status` が `COMPLETED` でなければ、D07 は採用評価に混ぜない。

## 10. run_end と失敗時の扱い

### 10.1 予定した run_end の手順【提案】

順序は確定済み【合意済み】上位 §4.7.13 D。本書はフェーズを割り当てる。

| # | 手順 | フェーズ |
|---|---|---|
| 1 | run_end で終了する足までの内部約定・口座更新を解決する | `EXECUTION_BAR_COMPLETE` / `LEDGER_UPDATE` |
| 2 | run_end までに期限到達した PENDING を EXPIRED にする（run_end と同時刻なら期限切れを優先） | `ORDER_EXPIRY` |
| 3 | 通常の評価スケジュールに従って戦略評価を行い判断履歴を残す | `PUBLICATION` 〜 `P5_ORDER_INTENT` |
| 4 | この評価から出た注文意図を**受付前に `RUN_END` で拒否**する。注文を作ってから取り消す方式にしない | `ADMISSION` |
| 5 | 残った受付済み PENDING を CANCELED（理由 `RUN_END`）にし、未約定予約を解放する。run_end から始まる足の始値処理は行わない | `RUN_END` |
| 6 | 残存建玉は未決済のまま MTM 評価して最終 snapshot を保存する。建玉割当も解放しない | `RUN_END` |

- 手順4の拒否も `AdmissionNotice(accepted=False, reason=RUN_END)` として `POST_FILL_EVALUATION` で配送し、取引機会を `ORDER_ATTEMPT_REJECTED` で終端させる【提案】。末尾でだけ通知経路を変えない。
- 手順3で生成された管理要求は記録するが**適用しない**。適用しない理由は `RUN_END` で残す【合意済み】同節。
- 終了だから未公開情報を解禁することはしない【合意済み】同節。

### 10.2 残存した取引機会の終端【要決定】（Q2）

D05 §7.2 の遷移9 は「run 末尾に残った取引機会を `RUN_END` で終端する」と定め、フェーズを「末尾処理」としている【合意済み】。**しかし取引機会の状態を変えられるのはランタイムだけであり（D05 §7）、D05 §6.1 の `StrategyRuntime` には `step(batch)` しか入口が無く、`PublicationBatch` に「これが最後である」ことを伝える項目が無い。** 合図の渡し方は第16節 Q2 で決める。

### 10.3 末尾の3集計【提案】

定義は確定済み【合意済み】上位 §4.7.13 E。`FinalSummaries` に次を入れる。

| 項目 | 内容 |
|---|---|
| `realized` | 確定した決済損益と計上済み費用の内訳（`cost_breakdown`）。未決済分の入場費用の所属を二重計上なく示す |
| `equity_with_mtm` | 終了 balance ＋ 未決済建玉の含み損益（買いは bid、売りは ask の決済側価格で評価） |
| `hypothetical_closed` | 残存建玉を終了時の価格・費用モデルで仮に閉じた場合の損益。**計算だけ**行い、注文・約定・完了取引数・balance・リスク枠を変更しない |

最終評価価格が無い場合は、正常な最終集計として扱わない（`status` を `FAILED_DATA_ERROR` にする）【合意済み】同節。この場合は `FinalSummaries` を組み立てず、`BacktestResult.summaries` を `None` にする【提案】。含み損益を評価できないまま `equity_with_mtm` と `hypothetical_closed` を埋めると、第10.4節の「保有建玉を架空価格で閉じない」に反する。**不採用**: 3集計の各項目を省略可能にする案（どの項目が算出できたかの組み合わせが増え、D07 が状態ごとに分岐することになる）。費用区分を指標へどう使うかは D07（第14節）。

### 10.4 予期しないデータ末尾と実行失敗【提案】

- 必要データが run_end より前に尽きれば `DATA_ERROR` であり、run_end を短縮も延長もしない【合意済み】上位 §4.7.13 D。
- 実行中の失敗では、未約定注文を CANCELED（理由 `DATA_ERROR`）として予約を解放し、注文・建玉・予約・口座状態と欠損の詳細（系列・区間・項目）を保存する。保有建玉を架空価格で閉じない【合意済み】同節 C。
- `status` は `FAILED_DATA_ERROR`。診断は保存するが、正常完走の採用評価に混ぜない【合意済み】同節。

### 10.5 実行前のデータ能力検査【提案】

run 開始前に必須で行い、`DataCapabilityReport` を残す【合意済み】上位 §4.7.13 C・ADR-0030。

1. 要求した期間・系列・ウォームアップ・必要な価格項目をカレンダーと照合する（D03 §3.9 の `IntegrityReport`）。
2. 執行系列が run 区間を覆うことを確認する。
3. 解像度階層の適合検査（第7.4節の検査1〜5）を行う。
4. 既知の欠損・無効値・末尾不足があれば**開始前に失敗**させる（`status = FAILED_CAPABILITY`）。検査結果は戦略へ渡さず、欠損付近だけを取引対象から外すこともしない。

公開遅延シナリオは元データの欠損とは区別する【合意済み】同節。

## 11. 段階2の最小範囲（検証戦略 A）と T01

D05 §9 の使用箇所に対して、エンジン側で起きることを時刻順に並べる。執行足は15分【合意済み】ADR-0015。

| 時点 | フェーズ | 起きること |
|---|---|---|
| T（1時間足の確定） | `EXECUTION_BAR_COMPLETE` 〜 `LEDGER_UPDATE` | 直前の15分足について、開いている建玉の保護水準の到達判定。段階2の1建玉目より前は対象なし |
| T | `ORDER_EXPIRY` | `expires_at <= T` の PENDING を EXPIRED。検証戦略 A では通常発生しない |
| T | `PUBLICATION` 〜 `P5_ORDER_INTENT` | 第1回の `step`。取引機会1件の生成（D05 の遷移1）と `EntryProposal` 1件（遷移4） |
| T | `ADMISSION` | `OrderRequest` を1件組み立て、参照価格を固定し、損切りを丸め、数量を決め、予約を確保して受付。`AdmissionNotice(accepted=True)` を作る |
| T | `EXECUTION_OPEN` | `[T, T+15m)` の始値で約定。建玉を作り初期の損切りを有効化（`effective_from` はこの足） |
| T | `POST_FILL_EVALUATION` | 第2回の `step`。受付の通知で機会が `FULFILLED_BY_ORDER_ACCEPTANCE` で終端（遷移5）。`POSITION_OPENED` で `fixed_rr_take_profit` が評価され `SetTakeProfit` を1件返す。丸めて建玉へ適用（`effective_from` は次の執行足） |
| T+15m | `EXECUTION_BAR_COMPLETE` | `[T, T+15m)` の足について到達判定。損切りは同じ足から有効なので対象、利確は次の足から |
| 到達日 | `EXECUTION_BAR_COMPLETE` | 片側だけなら `SINGLE_HIT`。両側なら階層1段のため `UNRESOLVED_SL_PRIORITY` で損切りを採用（第7.4節） |
| run_end | `RUN_END` | 残存注文の取消・残存機会の終端（Q2）・残存建玉の MTM 最終 snapshot |

**テスト**【提案】: 単体（全順序化の鍵、丸めの方向、予算の式、足内解決の再帰）、意味論（受付拒否で注文と予約が残らない、受付と約定の原子性、同じ `event_id` の再配送、終端後の別イベント、予約移管の二重計上、期限と始値の同時刻、保護決済と通常決済の競合、末尾の非執行、warmup 中の注文ゼロ）、プロパティ（同一入力の再実行で trace が完全一致、`balance` と `equity` と枠の恒等式が全 snapshot で成立）、golden（検証戦略 A の1取引分の trace を固定）【合意済み】上位 §4.7.15 E・全体計画 §8.2。

## 12. 対象外（段階3以降）

本節は**時期**の線引き（段階2で作らないもの）であり、第1.2節は**担当**の線引き（本書 v0.1 が決めないもの）である。

- 指値・逆指値・実行可能な価格制限付き注文 → 段階6・D10。段階2は能力検査で拒否。
- 部分約定・増し玉・分割決済 → 段階6・D10。
- 複数建玉・複数銘柄の台帳とリスク配分、`strategy_priority` の設定値 → 段階6・D10。
- 損切り水準の更新（トレーリング、`UPDATE_STOP`）と期間 Exit、初期利確の算定時点の変種 → 段階3・本書 v0.2。
- 未約定注文の週末持ち越しと、その設定の型名 → 段階6・D10。
- 受付前拒否の再審査（回数上限・設定の置き場所） → 段階6・D10。
- 距離型の保護水準（実約定価格を起点に損切りを置く） → 段階6・D10。段階2は拒否し、価格水準型へ暗黙変換しない。
- 証拠金・レバレッジ検査、口座強制縮小の対象と優先順位、約定直後以外の緊急決済の実行時点 → D10。
- 可変 spread・時間帯別 spread・詳細な費用内訳 → 段階6・D10。
- swap / rollover の計上 → ADR-0029 の改訂を要する別決定。
- 待機記録・追い越し記録の trace 表 → 段階3（D05 v0.2 が意味論を決めた後）。
- 遅延シナリオ4ケースの処理順の検証 → 段階3・D08。
- 高速化版エンジンと参照実装の同値性検証、並列化 → 段階6。
- run の途中再開・実験間の状態引き継ぎ → 段階5以降。

## 13. 上位文書との差異

1. **受付の全順序の鍵に2項目を足した**。上位 §4.7.12 の鍵は `(decision_time, strategy_priority, opportunity_id, attempt_id)` だが、決済要求は取引機会を持たないため `opportunity_id` の位置が埋まらない。本書は先頭に `request_class`（決済が先）を足し、`opportunity_id` の位置を「発端となった対象の連番」（エントリーは機会、決済は建玉）に一般化した（第6.3節）。**4項目の相対順序そのものは変えていない**。
2. **`position_context@v1` に有効な損切り水準を加えた**。D04 §5 は段階2で読む項目を「約定価格・方向・数量」と例示しているが、固定リスクリワード比の利確にはリスク幅が要る【合意済み】D05 §11 の差異1。本書が項目の正本であり、D04 §5 の例示の追記を第15節で依頼する。
3. **フェーズ名の規則が D02 §3.3 と D05 §6.1 で食い違っている**。D02 は `^[A-Z_]+$`（数字不可）、D05 は `P1_FEATURE` を使うと書いている。どちらかの改訂が要る（第16節 Q1）。本書が新しい規則を勝手に作ることはしない。
4. **run 末尾の合図を渡す経路が無い**。D05 §7.2 の遷移9 が要求する末尾終端を起こす入口が `StrategyRuntime` に無い（第10.2節、Q2）。
5. **`EntryProposal` に根拠の出力 ID が無い**。上位 §4.7.8 が要求する ID 連鎖を満たせないため、D05 への改訂依頼として第15節に挙げる（第6.1節）。
6. **段階2でレバレッジ・証拠金検査を行わない**。上位 §4.7.9 A は「数量上限、証拠金/レバレッジ、同時保有・未約定枠を合わせて検査」としているが、ADR-0015 の縦断範囲に証拠金モデルが無く、検査に使う値が存在しない（第6.4節）。同時保有・未約定枠の検査は行う（第8.2節）。

上記以外に、上位設計書・全体計画書・ADR・D01〜D05 と食い違う提案はない。

## 14. D07 への引き渡し事項

| 項目 | 内容 |
|---|---|
| 指標と集計 | `BacktestResult.trace_tables` が指す表から計算する指標、取引機会の終端理由別（表3）・評価見送りの診断理由別（表2）の集計（全体計画 §7.5）。D06 は集計しない |
| swap 未計上の明記 | `swap_modeled=False` をユーザーへ伝わる形で出力に含める（ADR-0029） |
| 足内競合の診断 | `unresolved_intrabar_count` と割合の提示（ADR-0030） |
| 末尾3集計の使い分け | 採用指標と参考値の区別、費用区分の集計式（上位 §4.7.13 E） |
| 失敗 run の扱い | `status != COMPLETED` の run を採用評価に混ぜない規則の実装箇所 |
| 実験 manifest | run manifest の項目をどう実験側で固定するか（全体計画 §5.5） |
| holdout の許可 | `AsOfView` の `allowed_partitions` を決める `holdout_gate`（D03 §6.1） |

## 15. 既存文書への改訂依頼

本書では他文書の本体を改訂しない。承認後に次を依頼する。

| 宛先 | 依頼 |
|---|---|
| D04 §5 | `position_context@v1` の例示に**有効な損切り水準**を足す（第13節の2。D05 §12 が既に依頼済みの項目を、本書が項目表として確定させた） |
| D05 §3・§6.2 | `EntryProposal` に `intent_output_id` / `protection_output_id`、`ManagementRequest` に `source_output_id` を足す（第6.1節）。根拠の ID 連鎖（上位 §4.7.8）を満たすために要る |
| D05 §6.1（Q1 の決定が「数字を使わない」の場合） | 要求するフェーズ名の列挙を `FEATURE` / `MARKET_STATE` / `TRIGGER` / `CONFIRMATION` / `ORDER_INTENT` へ改める |
| D02 §3.3（Q1 の決定が「規則を緩める」の場合） | `PhaseRank.name` の正規表現を `^[A-Z][A-Z0-9_]*$` へ改める |
| D05 §6.1（Q2 の決定による） | `PublicationBatch` に末尾を示す項目を足す、または `StrategyRuntime` に末尾用の操作を足す |
| 上位設計書 §4.7.14・D02 §8.1（Q6 の決定による） | 保護水準の妥当性違反の理由コードを足す。**両方を同じ PR で更新する**【合意済み】D02 §8.1 |
| 上位設計書 §4.7.12 | 受付の全順序の鍵に決済要求を含める一般化（第13節の1）を追記する |

## 16. 要決定の一覧

各項目は「何を決めるか」「結果への影響」「選択肢（推奨を先頭）」「推奨理由」で書く。

**Q1. フェーズの名前に数字を使えるようにするか**
影響: D02 と D05 のどちらを改訂するかが決まり、trace のフェーズ列の値と、フェーズを名前で引くランタイムの実装が変わる。
1. （推奨）共通カーネルの名前規則を `^[A-Z][A-Z0-9_]*$` へ緩め、`P1_FEATURE` などをそのまま使う — 影響: D02 §3.3 を1行改訂し、D05 と上位設計書 §4.3.12 の呼び方が一致する。
2. フェーズ名から数字を除き、`FEATURE` / `MARKET_STATE` などにする — 影響: D05 §6.1 の名前の列挙を改訂し、trace のフェーズ名から因果順が読めなくなる。
3. 数字を英単語に置き換える（`PHASE_ONE_FEATURE`） — 影響: どの文書も改訂せずに済むが、名前が長く上位設計書の呼び方とも一致しない。
推奨理由: 上位設計書 §4.3.12 が確定した P0〜P5 という呼び方が全文書で使われており、名前に残すと trace を読むときに因果順が名前だけで分かる。

**Q2. run 末尾に残った取引機会を終端させる合図をどう渡すか**
影響: D05 §7.2 の遷移9（末尾で `RUN_END` 終端）が実装できるかどうかと、戦略ランタイムの入口の数が決まる。
1. （推奨）公開バッチに「これが末尾である」ことを示す項目を足し、末尾フェーズの `step` 呼び出しで立てる — 影響: 入口は `step` 1つのまま。D05 §6.1 の `PublicationBatch` に1項目足す改訂が要る。
2. 戦略ランタイムに末尾専用の操作を足す — 影響: ポートの操作が2つになり、呼び出し順の規則がもう1本要る。
3. エンジンが残存機会を直接終端させる — 影響: 取引機会の状態の所有者がランタイムとエンジンに割れる。
推奨理由: 入口を1つに保つと「同じ `batch_id` で2度呼ばない」という既存の不変条件だけで呼び出し規則が閉じる。

**Q3. 初版の `entry_delay_bars` をいくつにするか**
影響: 受付から約定までの待ち時間と、検証戦略 A の約定価格・損益がすべて変わる。
1. （推奨）0（最初の適格な始値で約定） — 影響: T の受付が `[T, T+15m)` の始値で約定し、紙上トレース T01 の時刻表と一致する。
2. 1（最初の始値を1回見送る） — 影響: 約定が15分遅れ、保守的な結果になるが、基本モデルの検証が後回しになる。
推奨理由: 上位設計書 §4.7.11 が基本モデルとして確定したのが0であり、1は保守的な変種として実験設定で切り替える位置付けである。

**Q4. 初版の許容不利約定幅（USDJPY）をいくつにするか**
影響: 予約額と数量が変わり、約定ずれ超過による緊急決済の発生頻度が変わる。
1. （推奨）0.05 円（上位設計書 §4.7.9 C の数値例と同じ幅） — 影響: 例と結果を突き合わせて手計算で検証できる。
2. 価格刻みの10倍（0.01 円） — 影響: 予約額が小さくなり数量が増える。緊急決済が増える。
3. 1 pip（0.01 円）に固定し銘柄別設定を段階6へ回す — 影響: 銘柄別設定の型を段階2で作らずに済む。
推奨理由: 上位設計書の数値例と同じ値なら、紙上トレース T01 の手計算をそのまま検算に使える。

**Q5. 注文の有効時間の初版値をいくつにするか**
影響: 期限内に候補の始値が無い場合の受付前拒否の頻度と、週末持ち越し禁止に掛かる注文の数が変わる。
1. （推奨）エントリー20分・決済20分（執行足15分より長く、2本目には届かない） — 影響: 受付した注文は次の始値1本だけを待ち、届かなければ期限切れになる。
2. エントリー・決済とも15分ちょうど — 影響: 同時刻の境界で期限切れと約定が競合する場面が増える。
3. エントリー35分・決済20分（`entry_delay_bars=1` でも届く長さ） — 影響: 見送り変種でも期限内に約定できるが、待ち時間が長くなる。
推奨理由: 上位設計書 §4.7.13 F の時刻表（22:00:02 受付・候補22:15・`expires_at=22:20`）と同じ幅であり、その表の判定をそのまま検証に使える。

**Q6. 保護水準の妥当性違反による受付前拒否を、どの理由コードで記録するか**
影響: 判断履歴で「リスク超過による拒否」と「損切りの置き方が不正」を区別して集計できるかが決まる。
1. （推奨）専用の語を上位設計書 §4.7.14 と共通カーネルの理由コード表に足す（`PROTECTION_INVALID`） — 影響: 正本2文書を同じ PR で改訂する。集計で両者を分けられる。
2. 既存のリスク拒否（`RISK`）に畳み、詳細で区別する — 影響: 文書の改訂が要らないが、リスク上限の違反率に設計ミスが混ざる。
3. 既存のデータ誤り（`DATA_ERROR`）を使う — 影響: データの問題と宣言の問題が区別できなくなる。
推奨理由: 損切りの向きの違反は戦略の宣言の誤りであり、口座のリスク上限とは原因も対処も違う。D05 §5.2 がコンパイル拒否を共通コードから分けたのと同じ理由による。

**Q7. クロス通貨の換算経路をどう決めるか**
影響: 段階3以降で JPY 以外の口座やクロス通貨ペアを扱うときに、換算率が取れず実行不可になる範囲が変わる。段階2（USDJPY・JPY 口座）の結果は変わらない。
1. （推奨）直接のペアが snapshot にある場合だけ許し、無ければ拒否する — 影響: 実装が最小で、換算の根拠が常に1本の系列に対応する。扱えるペアが狭い。
2. 基軸通貨（USD）を経由する2ホップまで許す — 影響: 扱えるペアが広がるが、2本の系列の観測時点のずれを結果に残す規則が要る。
3. 経路を実験設定で明示宣言させる — 影響: 意図しない経路が使われないが、設定が増え段階2で使わない宣言型が要る。
推奨理由: 段階2では換算が恒等になるため、どの案でも結果は変わらない。最小の案を採り、必要になった段階で広げる方が、使われないまま固定される規則を作らずに済む。

**Q8. 足内競合の解像度階層をどこに宣言するか**
影響: 解像度階層の版が何と一緒に固定されるか（`ExecutionPolicy` の版か、独立したポリシーの版か）が決まり、run manifest の項目が変わる。
1. （推奨）実行ポリシー（`ExecutionPolicy`）の項目にする — 影響: 参照が1つ増えず、約定の意味を決める設定が1か所にまとまる。
2. 独立したポリシー（`IntrabarResolutionPolicy`）にして別の版参照を持つ — 影響: 階層だけを差し替えて比較する実験がやりやすいが、参照が1つ増える。
3. 実験設定の直下に置き、ポリシーにしない — 影響: 版として固定されず、再現性の識別から漏れる。
推奨理由: 階層は「どの足で約定を判定するか」という約定の意味そのものであり、執行足の指定と同じ版で固定される方が、両者が食い違った組み合わせを作れない。
