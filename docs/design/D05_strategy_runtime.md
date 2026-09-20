# D05: 戦略ランタイム・部品カタログ・コンパイラ設計（`odyssey_fx.strategy.runtime` / `strategy.catalog` / `strategy.compiler` / `strategy.records`）

作成日: 2026-09-21
状態: **承認（2026-09-21、PR #15）**。v1.1（2026-09-21、PR #16）: D06 の要決定 Q2 に対する人間の決定（選択肢1）により、run 末尾に残った取引機会を終端させる合図を公開バッチで渡す形に確定し、`PublicationBatch` に「これが末尾である」ことを示す項目（`is_run_end`）を足した（第3節の型表・第6.1節・第7.2節の遷移9）。戦略ランタイムの入口は `step` 1つのままである。v1.0: 第13節の要決定 Q1〜Q9 をユーザーがすべて決定し（全件で選択肢1、提示時の推奨案）、本文へ反映済み。Q3・Q4 の決定に伴い、取引機会の終端理由に `ORDER_ATTEMPT_REJECTED` と `FULFILLED_BY_ORDER_ACCEPTANCE` の2語を加え、正本である上位設計書 §4.5・§4.7.14、全体計画書 §5.3.5、D02 §8.1（v1.4）、ADR-0032（決定の補足4）を同じ PR で改訂した。Q1 の決定に伴い、上位設計書 §4.5 が仮置きとしていた非終端の状態名を `OPEN` / `CONFIRMED` / `ORDER_PENDING` に確定し、同節の未解決3点を解消した。段階2（最小縦断＝戦略定義→注文→約定→単一評価）と紙上トレース T01 に必要な範囲だけを扱う。検証戦略 A（1時間足の高値突破→後続確認なしの成行→初期損切り＋固定リスクリワード比の利確）が動くことを必要十分条件とする。待機（`WAIT_FOR_INPUT`）・評価要求の追い越し・後続確認は本書の**後続版 v0.2**（段階3前）で確定する（全体計画 §6 D-2 の条件5）。第13節に決定の一覧を置く。レビュー1巡目の指摘4件を反映済み（取引機会の対象区間と銘柄をランタイムが付ける形に整理: 第4.2節・第6.2節、実行時イベントは通知1件につき1評価要求とし建玉を伝播: 第6.2節・第8節、処理点をエンジンのフェーズ集合から組み立てる: 第6.1節、部品の戻り値を付番の前に検査: 第6.2節）。2巡目の指摘8件も反映済み（終端した取引機会を下流へ配送しない: 第6.2節・第7.4節、繰り返し参照する値の最新出力をランタイムが保持: 第6.5節、評価要求と評価記録へ取引機会・建玉・対象区間を伝播: 第6.2節・第6.4節、失敗を例外ではなく戻り値で返す: 第6.2節、登録時に実装参照の一致を検査: 第4.1節、対象区間が異なる起動は集約しない: 第6.2節・Q6、入力の接続数の指定漏れを補正: 第4.3節）。3巡目の指摘4件も反映済み（市場データの足は項目を射影してから部品へ渡す: 第6.3節、取引機会を組み立ててから付番・送出する順序に修正: 第6.2節、入力イベントも配送1件につき1評価要求: 第6.2節・Q6、部品の呼び出しが例外で終わった場合も失敗として返す: 第6.2節）。4巡目は対象内の重大な設計違反・契約違反が0件で収束し、改善提案2件を反映した（出力参照を履歴窓で読む接続を段階2では拒否: 第5.2節・第6.3節・第10節、発注試行中の取引機会は置換の対象にしない: 第7.2節・第7.4節）。決定反映後の巡では境界表の3点を明確化した（約定後の起動点は「約定処理の後」まで本書が確定し D06 へはフェーズ順位だけを委ねる: 第1.2節の行6・第8節、内容型の決定範囲を D04 §1.2 の行3 に合わせて列挙: 第1.2節の行1、段階3の遷移10・11 は発火条件の詳細だけを v0.2 へ切り出し: 第1.2節の行5・第7.2節）。2巡目は対象内の重大な設計違反・契約違反が0件で収束し、改善提案2件を反映した（条件の成否を表す型のフィールドの正本を上位設計書 §4.3.15 に一本化: 第1.1節・第1.2節の行1・第4.2節、役割出力の `PortKind` の決定を対象内へ明記: 第1.1節・第1.2節の行1）。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.3.11〜§4.3.15・§4.5・§4.6・§4.7.1・§7.1、[全体計画書](fx_research_platform_overall_plan.md) §5.3.2〜§5.3.5・§7.3 後半・§8.1・§8.2、[D01](D01_architecture_and_dependency_rules.md) §3.3・§4・§5.1・§7.2、[D02](D02_common_kernel.md) §3.3・§4・§7・§8・§9、[D03](D03_marketdata_and_time.md) §6.2・§7.1・§7.2、[D04](D04_strategy_declarations.md) 全体（特に §1.2 の境界表）、ADR-0006（決定論的 ID）、ADR-0008（純粋関数の部品）、ADR-0011（frozen dataclass）、ADR-0012（Decimal / float 境界）、ADR-0016（実装開始条件）、ADR-0021（NumPy の許可範囲）、ADR-0030（足内 SL/TP 競合）、ADR-0031（確認待ち中の条件再検査）、ADR-0032（再発火と複数取引機会）、ADR-0033（評価要求の追い越しの改名）
対応段階: 段階2で実装。ADR-0016 条件2 のうち「D05 の最小ランタイム範囲」を本書 v0.1 で充足する。

## 0. 本書の位置付けと凡例

D04 が確定した宣言（戦略の「形」）を、**実行可能な形へ変換し（`compiler`）、実装を対応付け（`catalog`）、公開イベントに対して評価する（`runtime`）**手順と型を決める。注文の受付・執行・約定、口座と建玉、評価指標は決めない。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D04）で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択した事項。**2026-09-21 に Q1〜Q9 をすべて決定済み**。第13節に決定内容を残す |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | `records` の役割別内容型、`catalog` の部品登録、`compiler` の `CompiledStrategy`、`runtime` の評価 API とポート定義 | 【合意済み】D01 §1・全体計画 §5.3.2〜§5.3.5 |
| 依存できるもの | Python 標準ライブラリ、`odyssey_fx.common`、`marketdata.domain`、同パッケージの下位層。`catalog` に限り NumPy（条件は D01 §5.1） | 【合意済み】D01 §3.2・§5 |
| 決めないもの | 宣言型の構造（D04）、注文受付・執行・約定・建玉・口座・フェーズ順序（D06）、評価指標と実験 manifest（D07） | 【合意済み】 |
| 層順序 | `runtime` → `compiler` → `catalog` → `records` → `declarations`。上向き参照はしない | 【合意済み】D01 §3.3 |

型はすべて `@dataclass(frozen=True, slots=True)`、コレクションは `tuple` か凍結 `Mapping`、区分タグ付き union は `kind` フィールドを持つ dataclass の `Union` とする【合意済み】D01 §8・ADR-0011。**唯一の例外**は `runtime` が保持する現在状態への参照1つで、第6.5節で範囲を限定する。

### 1.1 語彙と規則の正本がどの文書にあるか【提案】

D04 と同じ方針を引き継ぐ。同じ語彙を2か所に定義しない。

| 事項 | 正本 | 本書の扱い |
|---|---|---|
| 宣言型（`ComponentContract` ほか）のフィールド | D04 §3.1 | 参照のみ。再定義しない |
| コンパイル時検査の一覧と、各検査が読む宣言 | D04 §12 | 参照のみ。本書は**実行順と失敗時の扱い**だけを足す（第5.2節） |
| 取引機会の終端理由の語彙 | 上位設計書 §4.5（ADR-0031・ADR-0032） | 参照のみ。本書の Q3・Q4 で足した2語も、正本を改訂したうえで参照している（第13節） |
| 理由コードの語彙 | 上位設計書 §4.7.14（実装側の列挙は D02 §8.1 がその写し） | 参照のみ。足すときは §4.7.14 と D02 §8.1 を同じ PR で更新する |
| 実行時の出力3層（`OutputRecord` / `Observation` / 6つの内容型）のフィールド | 上位設計書 §4.3.15 | 参照のみ。`ConditionState.satisfied` もここが正本。本書が定義するのは §4.3.15 に無い内容型だけ（第4.2節） |
| 役割フィールドごとの `PortKind` | **本書 §4.2** | D04 は3区分を確定したが役割ごとの割り当てを決めていない。本書が決め、以後は本書を参照する |
| 欠損の診断理由（`MissingInputReason`） | D02 §8.3 | 参照のみ |
| as-of 読み取りの操作と欠損の返し方 | D03 §6.2 | 参照のみ。`max_age` の判定だけランタイムが行う（D03 §6.2 が委ねた） |
| フェーズの正式な列挙と順位 | D06（`backtest.engine`） | 参照のみ。本書は上位設計書 §4.3.12 の P1〜P5 という**名前**で参照し、新しい列挙を作らない |
| 処理順の全順序 `ProcessingPoint` | D02 §3.3 | 参照のみ |

### 1.2 本書が決めること・後続に委ねること・D04 から受け取ること【提案】（**レビュー対象範囲の正本**）

**本書 v0.1 のレビューの対象範囲は下表の第3列**である。第4列に属する指摘は「対象外（担当へ）」として記録し、本書では直さない。ただし D04 §1.2 と同じ例外を置く。**本書の文が第4列の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。第4列の内容を本書に書き足すことはしない。

| # | 領域 | 本書 v0.1 が決めること（対象内） | 後続が決めること（対象外・担当） |
|---|---|---|---|
| 1 | 実行時の内容型 | 上位設計書 §4.3.15 に無い内容型（`OrderIntent` / `ProtectionLevels` / `ManagementAction` / `TradeDirection` / `OrderType` / `OpportunityContent`）のフィールド、データ型識別子13件と内容型の対応表、部品が返す型と配送される型の切り分け、役割フィールドごとの `PortKind` の対応表（第4.2節。D04 §1.2 の行3が本書へ委ねた範囲）。**§4.3.15 が確定した6つの内容型のフィールドは参照のみで、本書は決めない** | `position_context@v1` / `account_context@v1` の項目（**D06**）、注文有効期限の既定値（**D06**） |
| 2 | 部品カタログ | 登録の単位、実装の形、段階2の5部品の契約と計算規則（第4節） | 指標部品（EMA・ATR）と合成部品（AND/OR・遷移検出・N 本継続）の契約と計算規則（**本書 v0.2**） |
| 3 | コンパイラ | 手順・検査の実行順・失敗時の扱い・`CompiledStrategy` の中身・依存グラフ構築・評価順の導出・ハッシュ計算の手順（第5節） | 検査項目そのものと各検査が読む宣言（**D04 §12 が正本**） |
| 4 | ランタイムの評価 | 起動判定、入力解決、欠損の扱い、評価要求のライフサイクル、部品状態の保持、出力の付番と決定論（第6節） | 待機（`WAIT_FOR_INPUT`）・遡り（`USE_PREVIOUS`）・評価要求の追い越し（`REQUEST_SUPERSEDED`）の意味論と宣言形（**本書 v0.2**、段階3前） |
| 5 | 取引機会 | 非終端状態と終端、遷移の全体像（第7.2節の遷移1〜11）と、**段階2で発生する遷移1〜9**の発火条件・フェーズ・記録する理由、有効性の再検査、同時保持と発火の記録、`opportunity_id` の採番と生成時点の項目（第7節） | 遷移10・11（後続確認の成立、確認期限の到達）の**発火条件の詳細**＝`AwaitConfirmation` の期限の数え方と確認評価の起動（**本書 v0.2**。遷移の始点・終点・理由は本書が確定済みで、v0.2 は発火条件を埋めるだけ）、受付結果を通知するフェーズと通知の内容（**D06**） |
| 6 | 約定後の評価 | `POSITION_OPENED` を受けて利確を評価する起動点を**同じ判断時点の約定処理の後に置くこと**（第8節、Q7 決定）。約定処理より前に置く構成は本書が排除する | その起動点に与えるフェーズ順位と、約定処理の後のどこに挿すか（**D06**。「約定処理の後」という本書の決定を満たす範囲に限る） |
| 7 | 数値の境界 | 比率パラメータを部品へ渡すときの Decimal 化（第4.4節） | 価格刻みへの丸め方向と適用時点（**D06**）、通貨換算（**D06**） |
| 8 | 記録 | 評価記録・取引機会の遷移記録として何を残すか（第6.4節・第7.2節） | trace の保存形式と `BacktestResult`（**D06**）、終端理由別の集計（**D07**） |

前提として **D04 から受け取るもの**は次のとおりで、本書はこれらを再定義しない。

| D04 の節 | 受け取るもの |
|---|---|
| §3・§3.1 | 3クラスのトップレベルと、宣言型35件のフィールド一覧 |
| §5 | データ型識別子13件の登録、接続検証（型・`PortKind`）、銘柄の伝播規則 |
| §6 | 読み取り条件4区分と、段階2の欠損方針2区分（`SkipEvaluation` / `Error`） |
| §8 | 起動条件の型と検証意味論、`RuntimeEventKind` は `POSITION_OPENED` の1値 |
| §9.1 | 状態の型・初期値・リセット契機の宣言 |
| §10.2〜§10.4 | 有効性束縛、同時保持の3フィールド、再武装（`EDGE` / `LEVEL`）と状態の要求 |
| §12 | コンパイル時検査 #1〜#7 と #6b、エンジン上の因果辺2本、段階2で拒否する構成 |
| §13.2 | 3つのダイジェストの対象 |
| §14 | 検証戦略 A に必要な宣言の一覧 |

## 2. モジュール構成【提案】

D01 §7.2 の一覧のうち、段階2で作るものと作らないものを分ける。

| サブパッケージ | 段階2で作る | 段階3（v0.2）で作る |
|---|---|---|
| `records` | `records.py`（`OutputRecord` / `Observation`）、`payloads.py`（役割別内容型） | — |
| `catalog` | `registry.py`、`features/extreme.py`、`triggers/breakout.py`、`orders/market.py`、`protection/level_stop.py`、`exits/fixed_rr.py` | `features/`（EMA・ATR）、`conditions/`、`permissions/`、`filters/` |
| `compiler` | `validate.py`、`graph.py`、`capability.py`、`hashing.py`、`compiled.py` | — |
| `runtime` | `ports.py`、`evaluator.py`、`requests.py`、`opportunities.py` | `waiting.py`、`supersession.py` |

D01 §7.2 の `runtime/` の一覧にある `waiting.py` / `supersession.py` を段階2で作らないのは、待機と追い越しの意味論が v0.2 の範囲だからである（第10節）。

## 3. 本書が定義する型の一覧【提案】

本文に出る型名を、区分（値の集合だけの enum / `kind` タグ付き union / フィールドを持つレコード / `Protocol`）とフィールドまで一覧する。**この表にない型名を本文で使わない**。節を足すときは必ずこの表も更新する。D04 が定義した型はそのまま使い、ここには載せない（D04 §3.1 が正本）。

| 型 | 置き場所 | 区分 | フィールド / 値 | 詳細 |
|---|---|---|---|---|
| `TradeDirection` | `records` | enum | `LONG` / `SHORT` | §4.2 |
| `OrderType` | `records` | enum | `MARKET`（段階2の唯一の値） | §4.2 |
| `OrderIntent` | `records` | レコード | `symbol: Symbol` / `direction: TradeDirection` / `order_type: OrderType` / `price_condition: None` / `expiry: timedelta \| None` | §4.2 |
| `ProtectionLevels` | `records` | レコード | `stop_loss: Price` / `take_profit: Price \| None` | §4.2 |
| `ManagementAction` | `records` | union | `SetTakeProfit(price: Price)` / `ClosePosition()` | §4.2 |
| `OpportunityContent` | `records` | レコード | `direction: TradeDirection` / `reference_values: Mapping[str, object]`（識別子・銘柄・対象区間は持たない） | §4.2 |
| `DataTypePayloadBinding` | `records` | レコード | `data_type: DataTypeRef` / `payload_type: type` | §4.2 |
| `ContractKey` | `catalog` | レコード | `component_id: str` / `version: int` | §4.1 |
| `ComponentRegistration` | `catalog` | レコード | `contract: ComponentContract` / `implementation: ComponentImplementation` / `implementation_ref: ImplementationRef` | §4.1 |
| `ComponentImplementation` | `catalog` | union | `StatelessImplementation(evaluate)` / `StatefulImplementation(evaluate, state_type: DataTypeRef)` | §4.1 |
| `ComponentOutputs` | `catalog` | レコード | `outputs: Mapping[str, object]` / `new_state: object \| None` | §4.1 |
| `CompileResult` | `compiler` | union | `CompileSucceeded(compiled: CompiledStrategy)` / `CompileFailed(errors: tuple[CompileError, ...])` | §5.1 |
| `CompiledStrategy` | `compiler` | レコード | `schema_version: int` / `strategy_ref: StrategyRef` / `compiled_ref: CompiledStrategyRef` / `symbol: Symbol` / `components: tuple[CompiledComponent, ...]` / `evaluation_order: tuple[str, ...]` / `roles: CompiledRoles` / `entry_policy: EntryPolicy` / `opportunity_validity: OpportunityValiditySpec` / `opportunity_concurrency: OpportunityConcurrencySpec` | §5.3 |
| `CompiledComponent` | `compiler` | レコード | `instance_id: str` / `contract_ref: ContractRef` / `implementation_ref: ImplementationRef` / `parameters: Mapping[str, ResolvedParameter]` / `input_plans: Mapping[str, InputPlan]` / `triggers: tuple[EvaluationTrigger, ...]` / `required_inputs: Mapping[str, tuple[str, ...]]` / `state_spec: StateSpec \| None` / `symbol: Symbol \| None` | §5.3 |
| `CompiledRoles` | `compiler` | レコード | `market_state: OutputRef \| None` / `trigger: OutputRef` / `execution_filter: OutputRef \| None` / `order: OutputRef` / `protection: OutputRef` / `exit: OutputRef \| None` | §5.3 |
| `InputPlan` | `compiler` | レコード | `input_name: str` / `data_type: DataTypeRef` / `kind: PortKind` / `read_spec: InputReadSpec` / `resolved_window: BarsWindow \| DurationWindow \| None` / `sources: tuple[ResolvedSource, ...]` | §5.3 |
| `ResolvedSource` | `compiler` | union | `ResolvedOutputSource(instance_id, output_name)` / `ResolvedMarketSource(series: SeriesId, field: MarketDataField)` / `ResolvedContextSource(target: RuntimeTarget)` | §5.3 |
| `ResolvedParameter` | `compiler` | レコード | `name: str` / `value: ParameterValue` / `unit: UnitRef \| None` / `decimal_value: Decimal \| None` | §4.4 |
| `DependencyGraph` | `compiler` | レコード | `nodes: tuple[str, ...]` / `edges: tuple[DependencyEdge, ...]` | §5.4 |
| `DependencyEdge` | `compiler` | レコード | `source_instance: str` / `target_instance: str` / `kind: EdgeKind` | §5.4 |
| `EdgeKind` | `compiler` | enum | `EXPLICIT_INPUT` / `FILL_TRIGGER` / `POSITION_CONTEXT`（後2者は D04 §12 の因果辺） | §5.4 |
| `CompileError` | `compiler` | レコード | `check_id: str` / `rejection: CompileRejection` / `location: DeclarationLocation` / `message: str` | §5.2 |
| `CompileRejection` | `compiler` | enum | `REFERENCE_NOT_FOUND` / `TYPE_MISMATCH` / `PARAMETER_INVALID` / `SCHEDULE_NOT_ALLOWED` / `ROLE_MISMATCH` / `OUTPUT_SPEC_INVALID` / `DEPENDENCY_CYCLE` / `UNSUPPORTED_CONFIGURATION` | §5.2 |
| `DeclarationLocation` | `compiler` | レコード | `instance_id: str \| None` / `field_path: str` | §5.2 |
| `PublicationBatch` | `runtime` | レコード | `batch_id: EventId` / `decision_time: UtcTime` / `phases: PhaseSet` / `available_bars: tuple[BarKey, ...]` / `scheduled_closes: tuple[BarClosure, ...]` / `runtime_events: tuple[RuntimeEventNotice, ...]` / `admissions: tuple[AdmissionNotice, ...]` / `is_run_end: bool`（v1.1。既定 `False`） | §6.1 |
| `BarClosure` | `runtime` | レコード | `bar_key: BarKey` / `interval: Interval` | §6.1 |
| `RuntimeEventNotice` | `runtime` | レコード | `kind: RuntimeEventKind` / `position_id: PositionId` / `opportunity_id: OpportunityId` | §8 |
| `AdmissionNotice` | `runtime` | レコード | `opportunity_id: OpportunityId` / `attempt_id: AttemptId` / `accepted: bool` / `reason: Reason \| None` | §7.2 |
| `RuntimeStepResult` | `runtime` | レコード | `outputs: tuple[OutputRecord, ...]` / `evaluations: tuple[EvaluationRecord, ...]` / `proposals: tuple[EntryProposal, ...]` / `management_requests: tuple[ManagementRequest, ...]` / `transitions: tuple[OpportunityTransition, ...]` | §6.2 |
| `EntryProposal` | `runtime` | レコード | `opportunity_id: OpportunityId` / `order_intent: OrderIntent` / `protection: ProtectionLevels` / `decision_time: UtcTime` | §6.2 |
| `ManagementRequest` | `runtime` | レコード | `position_id: PositionId` / `action: ManagementAction` / `decision_time: UtcTime` | §6.2 |
| `RuntimeState` | `runtime` | レコード | `component_states: Mapping[str, object]` / `latest_outputs: Mapping[OutputRef, OutputRecord]` / `opportunities: tuple[OpportunityLifecycle, ...]` / `last_batch_id: EventId \| None` | §6.5 |
| `EvaluationRequest` | `runtime` | レコード | `request_id: RequestId` / `instance_id: str` / `trigger_names: tuple[str, ...]` / `decision_time: UtcTime` / `target_interval: Interval \| None` / `opportunity_id: OpportunityId \| None` / `position_id: PositionId \| None` | §6.4 |
| `EvaluationRecord` | `runtime` | レコード | `request_id: RequestId` / `evaluation_id: EvaluationId` / `instance_id: str` / `trigger_names: tuple[str, ...]` / `decision_time: UtcTime` / `target_interval: Interval \| None` / `opportunity_id: OpportunityId \| None` / `position_id: PositionId \| None` / `outcome: EvaluationOutcome` | §6.4 |
| `EvaluationOutcome` | `runtime` | union | `Evaluated(output_ids: tuple[OutputId, ...])` / `Skipped(diagnoses: tuple[MissingInputDiagnosis, ...])` / `Failed(reason: Reason)` | §6.4 |
| `MissingInputDiagnosis` | `runtime` | レコード | `input_name: str` / `source: ResolvedSource` / `reason: MissingInputReason` | §6.3 |
| `ResolvedInputs` | `runtime` | レコード | `by_name: Mapping[str, tuple[InputElement, ...]]`（並びは `InputBinding.sources` の順） | §6.3 |
| `InputElement` | `runtime` | union | `ValueSample` / `ValueWindow` / `EventDelivery` / `ContextSnapshot` | §6.3 |
| `ValueSample` | `runtime` | レコード | `payload: object` / `source: ResolvedSource` / `freshness_time: UtcTime` / `source_output_id: OutputId \| None` | §6.3 |
| `ValueWindow` | `runtime` | レコード | `samples: tuple[ValueSample, ...]`（古い順） | §6.3 |
| `EventDelivery` | `runtime` | レコード | `payload: object` / `source_output_id: OutputId` | §6.3 |
| `ContextSnapshot` | `runtime` | レコード | `payload: object` / `read_at: UtcTime` | §6.3 |
| `OpportunityState` | `runtime` | enum | `OPEN` / `CONFIRMED` / `ORDER_PENDING` / `TERMINATED`（Q1・Q2 決定。上位設計書 §4.5 の語彙として確定） | §7.1 |
| `OpportunityLifecycle` | `runtime` | レコード | `opportunity: Opportunity` / `state: OpportunityState` / `created_at: ProcessingPoint` / `created_decision_time: UtcTime` / `snapshots: tuple[ValiditySnapshot, ...]` / `attempt_id: AttemptId \| None` / `terminal: OpportunityTerminal \| None` | §7.1 |
| `ValiditySnapshot` | `runtime` | レコード | `source: OutputRef` / `output_id: OutputId` / `satisfied: bool` | §7.3 |
| `OpportunityTerminal` | `runtime` | レコード | `reason: Reason` / `at: ProcessingPoint` | §7.2 |
| `OpportunityTransition` | `runtime` | レコード | `opportunity_id: OpportunityId` / `from_state: OpportunityState \| None` / `to_state: OpportunityState` / `at: ProcessingPoint` / `phase: PhaseRank` / `reason: Reason \| None` / `counterpart: OpportunityId \| None` / `attempt_id: AttemptId \| None` | §7.2 |
| `MarketDataView` | `runtime.ports` | Protocol | D03 §6.2 の5操作（`latest_available` / `history` / `bar` / `expected_latest_key` / `freshness`） | §6.1 |
| `RuntimeContextView` | `runtime.ports` | Protocol | `position_context(at: UtcTime, position_id: PositionId \| None) -> object \| None` / `account_context(at: UtcTime) -> object` | §6.1 |
| `OutputSink` | `runtime.ports` | Protocol | `emit(records: tuple[OutputRecord, ...]) -> None` | §6.1 |
| `StrategyRuntime` | `runtime` | Protocol | `step(batch: PublicationBatch) -> RuntimeStepResult` | §6.1 |

`SeriesId` / `BarKey` は D03 §3.1・§3.3、`Symbol` / `Price` / `Interval` / `UtcTime` / `Decimal` / `Reason` / `PhaseRank` / `PhaseSet` / `ProcessingPoint` と各 ID 型は D02、`OutputRecord` / `Opportunity` ほか6つの内容型は上位設計書 §4.3.15 が正本である。

## 4. 部品カタログ（`catalog`）

### 4.1 登録の単位と純粋性【提案】

部品は「契約（宣言）＋実装＋実装参照」を1組として `ComponentRegistration` で登録する【合意済み】全体計画 §5.3.3。レジストリは `Mapping[ContractKey, ComponentRegistration]` の静的テーブルで、実行時の登録 API を持たない（D04 §5 のデータ型レジストリと同じ扱い）。

実装は2形式だけとする【合意済み】ADR-0008・全体計画 §5.3.3。

| 区分 | 呼び出し形 |
|---|---|
| `StatelessImplementation` | `evaluate(inputs: ResolvedInputs, parameters: Mapping[str, ResolvedParameter]) -> ComponentOutputs`（`new_state` は `None`） |
| `StatefulImplementation` | `evaluate(inputs, parameters, state: object) -> ComponentOutputs`（`new_state` は非 `None`） |

- 実装は純粋関数とし、実時計・乱数・環境変数・I/O・グローバル可変状態を読まない。同じ `(inputs, parameters, state)` に対して常に同じ `ComponentOutputs` を返す。
- 実装オブジェクトは可変状態を持たない。状態はランタイムが使用箇所ごとに保持する（第6.5節）。
- `ComponentOutputs.outputs` のキー集合は、契約の `outputs` のキー集合の**部分集合**でなければならない。出さなかった出力は「今回の評価では発生しなかった」を意味し、`None` を出力値として入れない（EDGE の Trigger が発火しない評価がこれに当たる）。
- 登録時の検査【提案】: (a) 契約が `catalog` に無い `DataTypeRef` を使っていないこと、(b) `state_spec` がある契約の実装は `StatefulImplementation` で、その `state_type` が契約の `state_spec.state_type` と一致すること、(c) 逆に `state_spec=None` の契約の実装は `StatelessImplementation` であること、(d) **`ComponentRegistration.implementation_ref` が契約の `implementation_ref` と一致すること**。違反は `KernelValueError`（D02 §10）。(b)(c) が D04 §9.1 の「契約と実装の状態型を二重定義せず登録時に照合する」の実施箇所である。(d) を入れるのは、静的テーブルで取り違えると、契約が指す実装と実際に呼ぶ関数、さらに解決済み設定のダイジェスト（第5.5節の3）に入る実装の同一性が食い違い、再現性の識別が壊れるためである。
- 段階2の5部品はいずれも NumPy を使わない【提案】。NumPy の使用は指標部品を足す v0.2 で改めて判断する（D01 §5.1 の条件は既に確定）。

### 4.2 データ型識別子と実行時クラスの対応【提案】（D04 §1.2 行3 の引き受け）

D04 が登録した13件のデータ型識別子に、`records` 側の内容型を1対1で対応付ける。`declarations` はこの表を持たず（上向き参照になるため）、表の正本は `records`、登録時の照合は `catalog`（第4.1節）に置く【合意済み】D04 §5。

| データ型 | 部品が返す型 | `OutputRecord.payload` として配送される型 | 定義 | 段階2 |
|---|---|---|---|---|
| `price@v1` | `Price` | `Price` | D02 §4.3 | 使う |
| `price_offset@v1` | `PriceOffset` | `PriceOffset` | D02 §4.3 | 使わない |
| `ratio@v1` | `Decimal` | `Decimal` | 標準 | 使わない |
| `volume@v1` | `Decimal` | `Decimal` | 標準 | 使わない |
| `condition_state@v1` | `ConditionState` | `ConditionState` | 上位 §4.3.15 | 状態型として使う |
| `market_permission@v1` | `MarketPermission` | `Observation[MarketPermission]` | 上位 §4.3.15 | 使わない |
| `opportunity@v1` | `OpportunityContent`（方向と根拠値だけ） | `Opportunity` | 本書 §3・上位 §4.3.15 | 使う |
| `confirmation_result@v1` | `ConfirmationResult` | `ConfirmationResult` | 上位 §4.3.15 | 使わない |
| `order_intent@v1` | `OrderIntent` | `OrderIntent` | 本書 §3 | 使う |
| `protection_levels@v1` | `ProtectionLevels` | `ProtectionLevels` | 本書 §3 | 使う |
| `management_action@v1` | `ManagementAction` | `ManagementAction` | 本書 §3 | 使う |
| `position_context@v1` | （入力専用） | D06 が定める | D06 | 使う |
| `account_context@v1` | （入力専用） | D06 が定める | D06 | 使わない |

取引機会だけ2つの列が異なる【提案】。`Opportunity`（上位 §4.3.15）の5フィールドのうち、**部品が計算できるのは方向と根拠値の2つだけ**である。識別子を付けるのはエンジン側の責務であり（同節）、銘柄はコンパイラがグラフ上を伝播させて決め（D04 §5）、対象区間は起動した足が決める（第6.2節）。いずれも部品の入力からは復元できない。そこで**部品は `OpportunityContent(direction, reference_values)` を返し、ランタイムが残り3つを付けて `Opportunity` を組み立てる**。

| `Opportunity` のフィールド | 誰が決めるか |
|---|---|
| `opportunity_id` | ランタイム（`IdAllocator`、D02 §7.3） |
| `symbol` | コンパイラが伝播させた銘柄（`CompiledComponent.symbol`、D04 §5） |
| `signal_interval` | その評価の対象区間（`EvaluationRequest.target_interval`、第6.2節） |
| `direction` / `reference_values` | 部品（`OpportunityContent`） |

`Opportunity` のフィールド構成は変えない。**不採用**: 部品が5つとも作る案（採番の所有者が分散し、部品が銘柄と足の区間を入力から復元できない）、仮の値を入れて後で差し替える案（不変のはずの内容が書き換わる）。

`ConditionState` の項目は `satisfied: bool` の1つである【合意済み】上位設計書 §4.3.15。D04 §10.4 は「`condition_state@v1` の項目そのもの（真偽値1つか、確定足の識別子を伴うか）」を本書へ委ねたが、**フィールドの正本は §4.3.15 であり、本書が決めるのはデータ型識別子との対応だけ**である（第1.1節）。項目を増やす必要があれば §4.3.15 の改訂として提案することになるが、その必要はない。これにより `retrigger_mode=EDGE` の契約は `StateSpec(condition_state@v1, LiteralInitialState({"satisfied": BoolValue(False)}), reset_on=(RUN_START,))` と宣言でき、「起動時は発火可能」が宣言に現れる（D04 §9.1）。**不採用**: 成立を観測した確定足の識別子を持たせるよう §4.3.15 の改訂を提案する案（同じ足で2回評価された場合の重複抑止は評価要求の集約（第6.2節、Q6 決定）が担うため、状態に持たせると同じ規則が2か所になる）。

**役割出力の `PortKind`**【提案】。D04 は `PortKind` の3区分を確定したが、役割ごとにどれを使うかは決めていない。次のとおりとする。

| 役割フィールド | 出力の `PortKind` | 理由 |
|---|---|---|
| `market_state` | `VALUE` | 更新まで繰り返し参照する許可状態 |
| `trigger` / `execution_filter` | `EVENT` | 一度発生した事実・機会 |
| `order` / `protection` / `exit` | `COMMAND` | エンジンへの要求 |

役割フィールドが指す出力は**エンジンが役割フィールド経由で読む**ものであり、`InputBinding` では読まない。したがって「`COMMAND` を入力に接続しない」（D04 §6.1）と両立する。

### 4.3 段階2の初版カタログ（5部品）【提案】＋【合意済み】（Q8 決定、選択肢1）

検証戦略 A（上位設計書 §7.1）に必要な最小集合とする。**役割ごとに1部品、ただし高値・安値の抽出は1つの部品にまとめ、使用箇所を2つ置く**（Q8 決定）。段階3で合成部品を足すときに、ここで作る5部品の契約を作り直さずに済む。**不採用**: 条件部品と取引機会への変換部品を分けて6部品以上にする案（段階2で使わない接続が増える）、全体計画 §5.3.3 の12部品を段階2で作る案（段階2の完了条件を超える）。

いずれも `evaluation_spec.fixed=False`、`temporal_constraints=TemporalConstraints(warmup=None, alignment=())` とする。ウォームアップ不足は履歴読み取りが返す `WARMUP_INSUFFICIENT`（D03 §6.2）で判定でき、段階2の5部品は系列を契約に書けないため `WarmupSpec` を使わない（第11節の差異2）。

**(1) `extreme_price` v1**（高値・安値の抽出）

| 項目 | 内容 |
|---|---|
| 入力 | `prices`: `price@v1` / `VALUE` / `HistoryWindow(BarsWindow(ParameterRef("lookback")), max_age=None, on_missing=SkipEvaluation, exclude_latest_bars=1)` / `arity=(1,1)` |
| 出力 | `level`: `price@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `lookback`: `INT` / `BARS` / `[2, 500]`、`mode`: `STR` / 単位なし / `allowed_values=("MAX","MIN")` |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | 窓内の `Price` の最大（`MAX`）または最小（`MIN`）を返す。比較は `Price` 同士のみ（D02 §4.3）。窓は `exclude_latest_bars=1` により当該足を含まない |

**(2) `breakout_trigger` v1**（水準突破の検出）

| 項目 | 内容 |
|---|---|
| 入力 | `price`: `price@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(1,1)`、`level`: 同左 |
| 出力 | `opportunity`: `opportunity@v1` / `EVENT` / `reference_schema={"breakout_level": price@v1}` / `retrigger_mode=EDGE` |
| パラメータ | `direction`: `STR` / `allowed_values=("LONG","SHORT")` |
| 状態 | `StateSpec(condition_state@v1, LiteralInitialState({"satisfied": BoolValue(False)}), reset_on=(RUN_START,))` |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | 成立判定 `now = price > level`（`LONG`）または `price < level`（`SHORT`）。`now` かつ直前状態が不成立のときだけ `OpportunityContent(direction, {"breakout_level": level})` を出す。銘柄・対象区間・識別子はランタイムが付ける（第4.2節）。新しい状態は `ConditionState(now)` |

`retrigger_mode=LEVEL` の契約を段階2で使わないが、宣言としては許可される（D04 §10.4）。同じ実装を `LEVEL` で登録した契約は、成立している評価ごとに出力を出し、状態を持たない。

**(3) `market_order_intent` v1**（成行の注文意図）

| 項目 | 内容 |
|---|---|
| 入力 | `opportunity`: `opportunity@v1` / `EVENT` / `DeliveredEvent` / `arity=(1,1)` |
| 出力 | `intent`: `order_intent@v1` / `COMMAND` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | なし |
| 状態 | なし |
| 許可する起動条件 | `AllowedInputEvent(("opportunity",))` |
| 計算規則 | `OrderIntent(symbol=機会の銘柄, direction=機会の方向, order_type=MARKET, price_condition=None, expiry=None)`。`expiry=None` は「D06 が定める既定の有効時間に従う」を意味する（第12節） |

**(4) `level_stop_loss` v1**（価格水準型の初期損切り）

| 項目 | 内容 |
|---|---|
| 入力 | `opportunity`: `opportunity@v1` / `EVENT` / `DeliveredEvent` / `arity=(1,1)`、`level`: `price@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(1,1)` |
| 出力 | `protection`: `protection_levels@v1` / `COMMAND` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | なし |
| 状態 | なし |
| 許可する起動条件 | `AllowedInputEvent(("opportunity",))` |
| 計算規則 | `ProtectionLevels(stop_loss=level, take_profit=None)`。距離型は使わず解決済みの絶対価格を凍結する【合意済み】上位 §4.7.3。損切りが方向と整合するか（買いなら約定価格より下か）の検査は、参照価格を持つ受付側（D06）が行う |

**(5) `fixed_rr_take_profit` v1**（固定リスクリワード比の利確）

| 項目 | 内容 |
|---|---|
| 入力 | `position`: `position_context@v1` / `VALUE` / `CurrentContext` / `arity=(1,1)` |
| 出力 | `action`: `management_action@v1` / `COMMAND` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `reward_risk`: `FLOAT` / `RATIO` / `(0, 100]` |
| 状態 | なし |
| 許可する起動条件 | `AllowedRuntimeEvent((POSITION_OPENED,))` |
| 計算規則 | 建玉の約定価格 `entry` と有効な損切り水準 `sl` から `risk = entry - sl`（`PriceOffset`）を求め、`SetTakeProfit(entry + risk * reward_risk)`（`LONG`）または `SetTakeProfit(entry - (sl - entry) * reward_risk)`（`SHORT`）を返す。`reward_risk` は Decimal 化して渡す（第4.4節）。価格刻みへの丸めは行わない（D06 の責務） |

この部品は `position_context@v1` から**約定価格・方向・有効な損切り水準**を読む。D04 §5 は段階2で読む項目を「約定価格・方向・数量」と書いているが、リスクリワード比の計算には損切り水準が要る。供給範囲の正本は D06 であり、第12節で引き渡す（第11節の差異1）。

### 4.4 部品への受け渡し【提案】

- 入力は `ResolvedInputs`（第6.3節）だけを渡す。市場データ全体・台帳・他の使用箇所の出力への到達手段は渡さない【合意済み】全体計画 §5.3.3。
- パラメータは `ResolvedParameter` で渡す。`ParameterRef` はコンパイル時に具体値へ解決済みである（D04 §12 #3）。
- **比率・価格・pips の数値は Decimal 化して渡す**。`ComponentInstance.parameters` は有限 float のまま保持する（D04 §7、変更しない）が、`unit` が `RATIO` / `PRICE` / `PIPS` の `FloatValue` は、ランタイムが部品へ渡す時点で `common.money.decimal_from_str(repr(value))` により厳密な `Decimal` へ変換し、`ResolvedParameter.decimal_value` に入れる。部品内部に float 演算を持ち込まない。`Decimal(float)` を直接呼ばないため ADR-0012 と D02 §4.6 の規則を守れる。**不採用**: float のまま部品へ渡す案（`Price` との演算ができず、丸め誤差が trace に入る）、宣言の段階で `Decimal` にする案（D04 §7 の決定を覆す）。

## 5. コンパイラ（`compiler`）

### 5.1 手順【提案】

`compile(definition: StrategyDefinition, registry, data_types) -> CompileResult`。段階は次の順で、**前段が失敗したら後段を実行しない**（存在しない参照の型を比較しようとして二次的な誤りを出さないため）。ただし同じ段の中では全件を検査し、`CompileFailed.errors` にまとめて返す（1件ずつ直す往復を減らす）。

| 段 | 内容 | 対応する D04 §12 の検査 |
|---|---|---|
| 1 | 契約の解決: `contract_ref` をレジストリで引き、版と内容ハッシュを照合 | #1 |
| 2 | 参照の解決: `OutputRef` の使用箇所・出力名、役割フィールドの参照先 | #1・#5 |
| 3 | パラメータの解決: 型・範囲・列挙値、`ParameterRef` を具体値へ | #3 |
| 4 | 銘柄の伝播: D04 §5 の4つの供給元の和集合を取り、2つ以上なら拒否 | #2 |
| 5 | 型と接続の検査: `(type_id, version)`、`PortKind`、市場データ項目の対応、`arity`、読み方の組合せ | #2 |
| 6 | 出力仕様の付随条件 | #6b |
| 7 | 評価スケジュールの検査 | #4 |
| 8 | 依存グラフの構築と循環検出、評価順の導出（第5.4節） | #6 |
| 9 | 能力検査（段階2で拒否する構成） | #7 |
| 10 | ハッシュ計算と `CompiledStrategy` の組み立て（第5.3節・第5.5節） | — |

検査項目そのものと「各検査が読む宣言」は **D04 §12 が正本**であり、本書は再掲しない（第1.1節）。本書が足すのは上の実行順と、次節の失敗時の扱いである。

### 5.2 失敗時の扱い【提案】＋【合意済み】（Q9 決定、選択肢1）

| `CompileRejection` | 出る段 | 例 |
|---|---|---|
| `REFERENCE_NOT_FOUND` | 1・2 | 契約が未登録、`OutputRef` の使用箇所が無い |
| `TYPE_MISMATCH` | 4・5 | データ型不一致、`PortKind` 不一致、銘柄が2つ以上 |
| `PARAMETER_INVALID` | 3 | 範囲外、列挙外、未解決の `ParameterRef` |
| `SCHEDULE_NOT_ALLOWED` | 7 | `allowed` の範囲外、`fixed=True` の上書き、`required_inputs` の不一致 |
| `ROLE_MISMATCH` | 2 | 役割フィールドの型要求違反、`execution_filter` と `entry_policy` の不整合 |
| `OUTPUT_SPEC_INVALID` | 6 | `opportunity` 出力に `retrigger_mode` が無い、`EDGE` に適合する状態が無い |
| `DEPENDENCY_CYCLE` | 8 | 明示辺と因果辺の和に閉路がある |
| `UNSUPPORTED_CONFIGURATION` | 9 | `AwaitConfirmation`、`RuntimeInputRef(PENDING_ORDER)`、15m より細かい足、**出力参照（`OutputRef`）を履歴窓（`HistoryWindow`）で読む接続** ほか |

各 `CompileError` は `check_id`（D04 §12 の番号、例 `"#6b"`）と `location`（使用箇所とフィールド経路）を必ず持ち、原因の宣言を指させる。**黙って無視する経路を作らない**【合意済み】D04 §12。

D04 §12 は「拒否は `ReasonCode`（D02 §8.1）付きの構造エラー」としているが、D02 §8.1 の語彙は注文の受付・評価の理由であり、コンパイル拒否に対応する語が無い。**コンパイラ専用の区分 `CompileRejection` を使い、共通の理由コードは実行時に限る**（Q9 決定）。コンパイルは run の前に終わる処理で、理由コードの主な使用箇所（受付前拒否・評価見送り）とは対象が異なり、実行時の集計に設計ミスの分類が混ざらない。D04 §12 の文言をこの決定に合わせる改訂は、D04 の次回改訂で行う（第12節）。**不採用**: 共通の理由コードへ拒否用の語を加える案（注文・評価の集計に設計ミスが混ざる）、既存の `DATA_ERROR` を流用する案（データの問題と宣言の問題を区別できない）。

### 5.3 コンパイル結果【提案】

`CompiledStrategy` は不変で、内容ハッシュ（`compiled_ref`）を持つ【合意済み】全体計画 §5.3.4。ランタイムは**この型だけを読み、`StrategyDefinition` を再解釈しない**。宣言の解釈を2か所に置かないためである。

- `components` は `evaluation_order` と同じ並びで保持する（並びが2つあると食い違う）。`evaluation_order` は `instance_id` の列で、第5.4節の規則で一意に定まる。
- `InputPlan.resolved_window` は `ParameterRef` を解決した後の窓で、ランタイムはこれをそのまま `MarketDataView.history` へ渡す。
- `CompiledStrategy.symbol` は第5.1節 段4 で伝播させた単一銘柄。
- `CompiledRoles.trigger` / `order` / `protection` は段階2で必須、`market_state` / `execution_filter` / `exit` は `None` を許す（検証戦略 A は `exit` を持つ）。

### 5.4 依存グラフと評価順【提案】

- 節点は使用箇所（`instance_id`）。辺は D04 §12 が確定した3種類（明示入力＋因果辺2本）で、`EdgeKind` で区別して保持する。因果辺も**種類を残したまま**保持するのは、循環が明示接続によるものか帰還路によるものかを `CompileError.message` で示すためである。
- 循環検出は3種の和の上で行う。閉路があれば `DEPENDENCY_CYCLE`。
- 評価順は**トポロジカル順、同順位は `instance_id` の Unicode コードポイント順**とする【提案】。同順位の並びを固定しないと、同じ宣言から出力の `sequence`（第6.6節）が変わり、「同一入力の再実行で trace が一致」（全体計画 §8.2）を満たせない。時間足の大小から順序を推測しない【合意済み】全体計画 §5.3.4 の6。
- 評価順は使用箇所の全件を含む（起動しない使用箇所も並びには含め、実行時に起動判定で落とす）。

### 5.5 ハッシュ計算【提案】

D04 §13.2 が決めた3つの対象を、D02 §9.3 の `canonical.digest` で計算する手順だけを定める。

1. `ContractRef.digest`: 契約から `implementation_ref` を除いた値を正規化エンコードして計算する。カタログ登録時に計算し、レジストリの鍵と一緒に保持する。
2. `StrategyRef.digest`: `StrategyDefinition` 全体。各 `ComponentInstance` が持つ `contract_ref` を通じて 1 を含む。
3. `CompiledStrategyRef.digest`: 解決済みパラメータ・評価順・各部品の `ImplementationRef` を含む。**`CompiledStrategy` 全体をそのまま対象にしない**のは、`strategy_ref` と `compiled_ref` 自身を含む自己参照になるためで、対象は `(strategy_ref, evaluation_order, 各 CompiledComponent の (instance_id, contract_ref, implementation_ref, parameters, input_plans, triggers))` とする。
4. 順序に意味を持たせないコレクションは D04 §3 の表に従って構築時に正規化済みであり、ここで並べ替えを重複実装しない【合意済み】D04 §13.2。

## 6. 戦略ランタイム（`runtime`）

### 6.1 ポートと呼び出し境界【提案】

所在と実装者は D01 §4 が確定している【合意済み】。本書はその呼び出し形を定める。

| ポート | 呼び出し形 | 備考 |
|---|---|---|
| `MarketDataView` | D03 §6.2 の5操作 | 正本は D03。ランタイムは `at=decision_time` を必ず渡す |
| `RuntimeContextView` | `position_context(at, position_id)` / `account_context(at)` | `position_id` は評価要求が指す建玉（第6.2節）。`None` は「現在の建玉」を意味し、段階2の単一建玉でだけ使える。戻り値の payload の項目は D06（第12節） |
| `OutputSink` | `emit(records)` | trace への転送はエンジン側 |
| `StrategyRuntime` | `step(batch) -> RuntimeStepResult` | `backtest.engine` が P1〜P5 の中身として呼ぶ |

`step` は直前と同じ `batch_id` で呼ばれたら `KernelValueError` を送出し、状態を更新しない【提案】（`RuntimeState.last_batch_id` と比較する）。再配送の除外そのものはエンジン側の冪等性検査（全体計画 §5.4）の責務だが、二重適用を型の側でも止める。

公開イベントは **`marketdata.domain` の型だけで渡す**【提案】。D03 §7.1 の `Publication` / `ScheduledBoundary` は `marketdata.application` に属し、`strategy` は `marketdata.domain` しか参照できない（D01 §3.2 の契約 F2）。そこでエンジンが両イベントを変換して渡す。公開は `BarKey` の列（`available_bars`）、足の終了予定は `BarClosure`（`BarKey` ＋ その足の `interval`）の列（`scheduled_closes`）である。

**run 末尾の合図を公開バッチで渡す（v1.1、2026-09-21 の D06 の要決定 Q2 の決定、選択肢1）**。第7.2節の遷移9（run 末尾に残った取引機会を `RUN_END` で終端する）を起こす入口が無かったため、`PublicationBatch` に `is_run_end: bool` を足した。エンジンは run 末尾の判断時点の末尾フェーズ（D06 §4.1 の `RUN_END`）でこの項目を `True` にして `step` を呼ぶ。入口は `step` 1つのままであり、「同じ `batch_id` で2度呼ばない」という既存の不変条件だけで呼び出し規則が閉じる。

| 事項 | 規則 |
|---|---|
| 構築時の不変条件 | `is_run_end=True` のバッチは `available_bars` / `scheduled_closes` / `runtime_events` / `admissions` がすべて空でなければならない。違反は `KernelValueError`。末尾の合図と通常の公開・通知を同じバッチに混ぜない |
| ランタイムの処理 | 起動判定・評価・出力の送出を行わない。非終端（`OPEN` / `CONFIRMED` / `ORDER_PENDING`）の取引機会をすべて `RUN_END` で終端し（遷移9）、`OpportunityTransition` だけを持つ `RuntimeStepResult` を返す。`outputs` / `evaluations` / `proposals` / `management_requests` は空 |
| 終端の順序 | `opportunity_id.seq` の昇順。同じ判断時刻・同じフェーズの中で `sequence` が決定論的に決まるようにするため |
| 1 run に1回 | `is_run_end=True` の `step` は1 run に1回だけ呼ばれる。2回目は `KernelValueError`（`RuntimeState` に記録する） |

`is_run_end=True` でも `decision_time` と `phases` は通常どおり渡す（記録の `ProcessingPoint` を組み立てるため）。**不採用**: 末尾専用の操作をポートに足す案（呼び出し順の規則がもう1本要る）、エンジンが残存機会を直接終端させる案（取引機会の状態の所有者がランタイムとエンジンに割れる）。

**足の区間を `BarClosure` に載せる理由**【提案】: 取引機会の `signal_interval` は起動した足の区間であり（第4.2節）、系列と足の開始時刻だけでは、夏時間の切替日や短縮セッションで実際の区間を復元できない（D03 §3.3）。区間を決めるのはカレンダーを持つ `marketdata` 側であり、戦略側で再計算すると規則が2か所になる。

**処理点（`ProcessingPoint`）は自分で組み立てず、エンジンから受け取った材料で作る**【提案】。取引機会の生成・終端・遷移の記録は `ProcessingPoint(time, phase, sequence)` を持つが、フェーズの順位と全列挙は D06 の責務である（第1.1節）。そこで `PublicationBatch.phases: PhaseSet`（D02 §3.3）を受け取り、**本書はフェーズの名前だけを要求**して `phases.by_name(...)` で順位を引く。要求する名前は上位設計書 §4.3.12 の `P1_FEATURE` / `P2_MARKET_STATE` / `P3_TRIGGER` / `P4_CONFIRMATION` / `P5_ORDER_INTENT` と、本書が D06 へ要求する2つ（ライフサイクル検査・約定後の評価起動点、第8節・第12節）である。未登録の名前は `KernelValueError`（D02 §3.3）。`sequence` は第6.6節の `step` 内の通し番号を使う。**不採用**: 本書でフェーズを列挙する案（D06 と二重定義になる）、記録の刻印をすべてエンジンへ戻す案（どの遷移がどのフェーズで起きたかはランタイムしか知らない）。

### 6.2 1回の `step` で行うこと【提案】

1. **起動判定**: 各使用箇所の各起動条件について、成立を判定する。`OnBarClose(name, series)` は batch の `scheduled_closes` に同じ系列の `BarKey` があるとき（D03 §7.2 の `ScheduledBoundary` に対応）【合意済み】D03 §7.2、`OnInputEvent(name, input_name)` はその入力に接続された `EVENT` 出力が**同じ `step` の上流評価で**出たとき、`OnRuntimeEvent(name, event)` は batch の `runtime_events` に同じ種別の通知があるとき。
2. **評価要求の生成**: 起動した使用箇所ごとに `EvaluationRequest` を作る。**イベントによる起動は、配送されたイベント1件・通知1件につき1要求を作る**【提案】。

| 起動 | 要求の数 |
|---|---|
| `OnBarClose`（足の確定） | 対象区間ごとに1件（同じ対象区間の複数の起動条件は1件に集約する。Q6 決定） |
| `OnInputEvent`（入力イベントの配送） | 配送された `OutputRecord` 1件につき1件 |
| `OnRuntimeEvent`（実行時イベントの通知） | 通知1件につき1件 |

イベントを使用箇所ごとに1件へ畳まないのは、**1つのイベントが1つの対象を指すから**である。同じバッチで2つの建玉が生まれれば通知は2件で、畳むと片方の建玉に利確が付かない。同じように、`LEVEL` の Trigger が同じ判断時点で2つの取引機会を出すと配送も2件で、畳むと片方の機会の注文意図と保護水準が作られない。要求はそのイベントの対象（取引機会・建玉）を持ち、入力解決（第6.3節）と役割出力の組み立て（手順9）へそのまま渡す。段階2は機会も建玉も1つずつだが、規則をここで分けておかないと段階3以降で意味が変わる。
3. **対象区間と対象の確定**: 各要求が持つ3つの値を次のとおり決める【提案】。

| 起動 | `target_interval` | `opportunity_id` | `position_id` |
|---|---|---|---|
| `OnBarClose` | その `BarClosure.interval` | `None` | `None` |
| `OnInputEvent` | そのイベントを生んだ上流の要求の値を引き継ぐ | 配送された payload が `Opportunity` ならその `opportunity_id`、それ以外は上流の値を引き継ぐ | 上流の値を引き継ぐ |
| `OnRuntimeEvent` | `None`（約定は足の区間に属さない） | 通知の `opportunity_id` | 通知の `position_id` |

取引機会の `signal_interval` は `target_interval` である（第4.2節）。`opportunity` 出力を持つ契約が実行時イベントだけで起動する宣言は、対象区間を決められないためコンパイル時に拒否する（第5.2節の `OUTPUT_SPEC_INVALID`）。`opportunity_id` を引き継ぐのは、同じ取引機会から出た注文意図と保護水準を手順7で組にするためであり、引き継がなければ同時に複数の機会が進んだときに組が作れない。

**足の確定どうしは、対象区間が同じものだけを1件に集約する**【合意済み】（Q6 決定、選択肢1）。集約した要求は成立した起動条件名をすべて `trigger_names` に持ち、必須入力は和集合とする（第6.3節）。同じ論理確認を二重実行しない（上位設計書 §4.3.12）。**不採用**: 足の確定ごとに1回ずつ評価する案（同じ足で機会が重複しうる）、宣言でどちらかを選べるようにする案（段階2で使わない設定が増える）。1つの使用箇所に1時間足と15分足の `OnBarClose` を宣言し、両方が同じ判断時点で確定すると `BarClosure.interval` が2つになる。区間の違う起動をまとめると取引機会の対象区間が実装依存になるため、**区間ごとに別の要求**を作る。段階2の検証戦略 A は1系列のため要求は常に1件である。
4. **依存順評価**: `evaluation_order` に従い、起動した使用箇所だけを評価する。上流の更新だけで下流を自動評価しない【合意済み】上位 §4.3.2。
5. **入力解決**（第6.3節）→ **部品の呼び出し** → **戻り値の検査**（次の表）。
6. **取引機会の組み立てと同時保持の判定**: 取引機会を出す出力が出たら、`OpportunityId` を採番して `Opportunity` を組み立て（第4.2節）、第7.4節の判定を行う。有効性の再検査は第7.3節。
7. **出力の付番と送出**: `OutputRecord` を作り（第6.6節）`OutputSink.emit` へ渡し、状態を更新する（第6.5節）。
8. **下流への配送**: `EVENT` 出力を、購読する入力へ配送する。**終端した取引機会は配送しない**（次段落）。
9. **役割出力の取り出し**: `roles.order` と `roles.protection` の出力が**同じ `opportunity_id` の要求から**揃った時点で `EntryProposal` を1件作る（P5）。`roles.exit` の出力は、その評価要求の `position_id` を宛先として `ManagementRequest` にする。
10. `RuntimeStepResult` を返す。

手順6を手順7より前に置くのは、`OutputRecord` の payload が組み立て済みの `Opportunity` だからである（第4.2節）。識別子を付ける前に送出すると、判断履歴に識別子のない内容が残る。

**部品の戻り値は付番の前に検査する**【提案】。次の4点を検査し、1つでも違反すれば `OutputRecord` を作らず、状態も更新しない。

| # | 検査 | 材料 |
|---|---|---|
| 1 | `ComponentOutputs.outputs` のキーが契約の `outputs` のキーの部分集合であること | `ComponentContract.outputs` |
| 2 | 各値の実行時クラスが、その出力の `data_type` に対応する内容型であること | 第4.2節の対応表 |
| 3 | 取引機会の出力は、`reference_values` のキー集合と各値の型が `OutputSpec.reference_schema` と一致すること | D04 §11.1 が要求する実行時検証 |
| 4 | `new_state` の実行時クラスが `StateSpec.state_type` に対応する内容型であること（状態を持たない契約では `None`） | 第4.1節 |

検査を付番の前に置くのは、誤った payload が `OutputRecord` として下流と trace へ配送されるのを防ぐためである。

**失敗は例外ではなく戻り値で返す**【提案】。上の検査違反も、`on_missing=Error` による欠損（第6.3節）も、**部品の呼び出しそのものが例外で終わった場合**（計算の誤り、ゼロ除算、`Decimal` のトラップなど）も、`EvaluationRecord` に `Failed(Reason(DATA_ERROR, ...))` を残し、**以降の評価を行わずに `RuntimeStepResult` を返す**。run を終了させるのはエンジンの責務である（D06）。例外で抜けると、評価記録の唯一の公開経路が `RuntimeStepResult.evaluations` であるため、失敗の診断が判断履歴から消える。`OutputSink` は `OutputRecord` しか受け取らないので、記録の受け渡しに使えない。**不採用**: 例外を送出して呼び出し元に任せる案（診断が残らない）、評価記録専用の受け口をもう1つ作る案（ポートが増え、`RuntimeStepResult` と二重になる）。

同じ `EVENT` 出力を複数の入力が購読する場合、各購読先へ同じ `OutputRecord` を1回ずつ配送する【提案】。配送順は評価順（第5.4節）に従い、同じ論理確認を二重実行しない【合意済み】上位 §4.3.12。

**終端した取引機会のイベントは下流へ配送しない**【提案】。同時保持上限に達した発火は、取引機会として記録したうえで有効にせず終端する（D04 §10.3）。配送を先に行うと、同じイベントを購読する注文意図と保護水準の部品が起動し、終端した機会から発注の組ができてしまう。そこで**付番と `OutputSink.emit` は行い（判断履歴には残す）、`OnInputEvent` の起動判定の対象からは外す**。「発火を捨てない」（ADR-0032）と「有効にしない」（D04 §10.3）を両立させるのはこの順序である。`COMMAND` 出力は入力に接続できない（D04 §6.1）ため、`COMMAND` を加工する部品は段階2では作れない。

### 6.3 入力解決と欠損【提案】

`InputPlan` の区分ごとに、`ResolvedInputs.by_name[input_name]` の各要素を作る。

| 読み方 | 解決の手順 | 要素 |
|---|---|---|
| `LatestAvailable` | 市場データ参照なら `MarketDataView.latest_available(series, decision_time)` が返す足を射影（次段落）、出力参照なら `RuntimeState.latest_outputs` に保持した最新の `OutputRecord`（第6.5節） | `ValueSample` |
| `HistoryWindow` | 市場データ参照のみ。`MarketDataView.history(series, resolved_window, decision_time, end_offset_bars=exclude_latest_bars)` が返す足の列を1本ずつ射影（次段落） | `ValueWindow` |
| `DeliveredEvent` | 同じ `step` で配送された `OutputRecord` | `EventDelivery` |
| `CurrentContext` | `RuntimeContextView` を `decision_time` と評価要求の `position_id` で読む（`RuntimeTarget` が `POSITION` なら `position_context`、`ACCOUNT` なら `account_context`） | `ContextSnapshot` |

- **出力参照を履歴窓で読む接続は、段階2では能力検査で拒否する**【提案】。D04 §6.1 の許可表は `VALUE` の出力参照に `HistoryWindow` も許しているが、履歴を作るにはランタイムが過去の出力を窓の本数分ため込む必要があり、段階2が保持するのは最新1件だけである（第6.5節）。宣言としては通るのに評価できない構成を残さないため、コンパイル時に `UNSUPPORTED_CONFIGURATION` で拒否する（第5.2節）。検証戦略 A の履歴読み取りはすべて市場データ参照であり、この制限にかからない。出力の履歴を保持する仕組みは、上流の出力を遡って読む部品が必要になる v0.2 で扱う（第10節）。**不採用**: 出力の履歴を段階2から保持する案（窓の本数分の記憶と、その打ち切り規則が必要になり、段階2で使わない仕組みが増える）、実行時に欠損として扱う案（宣言から評価できないことが読めない）。
- **市場データの足は項目を射影してから部品へ渡す**【提案】。`MarketDataView` が返すのは `Bar`（D03 §3.3）だが、部品の入力の型は `price@v1` などの単一の内容型である（D04 §5）。そこでランタイムが `ResolvedMarketSource.field` に従って1本ずつ射影し、`ValueSample.payload` に入れる。`OPEN` / `HIGH` / `LOW` / `CLOSE` は `Price`、`VOLUME` は `Decimal` であり、D04 §5 が定めた項目とデータ型の対応（`price@v1` / `volume@v1`）と一致する。`ValueSample.freshness_time` は `MarketDataView.freshness(series, bar)`（確定足なら足の終了時刻）。射影を履歴側でも行うのは、`extreme_price` が `Bar` ではなく `Price` の列から最大・最小を求める契約になっているためである。部品に `Bar` を渡さないことで、宣言していない項目（当該足の終値など）を部品が覗くこともできなくなる。
- **`max_age` の判定はランタイムが行う**【合意済み】D03 §6.2。`decision_time - freshness_time > max_age` なら `MAX_AGE_EXCEEDED`。ビューは鮮度基準時刻を返すだけである。
- **出力参照の鮮度基準時刻**は、その出力を生んだ評価の `decision_time` とする【提案】。上流の観測区間まで遡る鮮度の伝播は段階3（第10節）。段階2の5部品は `max_age=None` のため、この選択は検証戦略 A の結果を変えない。**不採用**: 上流の `Observation.freshness_time` を伝播させる案（複数系列を混ぜる部品が無い段階2では検証できない規則を先に固定することになる）。
- 欠損は **`MissingInputDiagnosis` として集め**、`InputReadSpec.on_missing` に従う。`SkipEvaluation` なら評価を行わず `Skipped` を記録する。`Error` なら `Failed(Reason(DATA_ERROR, ...))` を記録し、以降の評価を行わずに `RuntimeStepResult` を返す（前節の「失敗は戻り値で返す」）。**欠損を False や 0 に変換しない**【合意済み】上位 §4.3.15。
- ウォームアップ不足は `history` が返す `WARMUP_INSUFFICIENT` として現れ、`SkipEvaluation` により評価が飛ぶ。これで「warmup 中の注文ゼロ」（全体計画 §8.2）が成立する。
- 必須入力の判定は、成立した起動条件の `required_inputs` の**和集合**とする【提案】。必須でない入力が欠けても評価は行う。

### 6.4 評価要求のライフサイクル【提案】

段階2の終端は3つだけである【合意済み】全体計画 §5.3.5 の表（待機と追い越しは段階3）。

| 終端 | 記録 |
|---|---|
| `Evaluated` | 生成した `OutputId` の列 |
| `Skipped` | 欠損診断の列（`MissingInputDiagnosis`） |
| `Failed` | `Reason`（段階2では `DATA_ERROR` のみ） |

- **`Skipped` / `Failed` で決着した要求は復活させない**【合意済み】上位 §4.3.14。上流の到着で再開する経路は段階2には無い。
- 評価記録は起動した使用箇所ごとに必ず1件残す。入力不足で評価しなかったことも記録に残す【合意済み】上位 §4.3.15。
- **評価記録は要求の対象（`target_interval` / `opportunity_id` / `position_id`）を写して持つ**【提案】。`EvaluationRequest` そのものは `RuntimeStepResult` に入れないため、写さないと「同じバッチで同じ Exit を2つの建玉について評価した」場合にどの記録がどの建玉のものか `request_id` からは復元できない。**不採用**: 要求を結果にそのまま入れる案（同じ内容が2つの型に並ぶ）。
- `RequestId` / `EvaluationId` / `OutputId` は `IdAllocator`（D02 §7.3）で採番し、採番順は評価順に一致させる。

### 6.5 部品状態の保持と更新【提案】

- 状態はランタイムが使用箇所ごとに保持する【合意済み】ADR-0008。`RuntimeState.component_states` は `instance_id` → 状態 payload の凍結 `Mapping` で、`step` ごとに**新しい `RuntimeState` へ差し替える**。可変参照はランタイム1インスタンスにつき1つだけとし、他はすべて不変とする（第1節の例外）。
- **`VALUE` の出力は最新の1件を `RuntimeState.latest_outputs` に保持する**【提案】。`LatestAvailable` は「更新まで繰り返し参照する値」を読む読み方であり（D04 §4.2）、上流の使用箇所が今回の `step` で起動していないときにも読めなければならない。検証戦略 A では起動が同じ足に揃うが、上流が日足・下流が1時間足という構成（段階3の検証戦略 B）では毎回ずれる。保持するのは出力参照（`OutputRef`）ごとに最新の1件だけで、履歴は持たない（履歴は市場データの `HistoryWindow` が担う）。`EVENT` と `COMMAND` の出力は保持しない。配送された時点で消費されるものであり、保持すると過去のイベントを「最新値」として読めてしまう。**不採用**: 出力の読み取り用ポートを足す案（エンジン側に戦略の出力台帳を持たせることになり、D01 §4 のポートの向きと合わない）、全出力の履歴を持つ案（段階2で使わない記憶が増える）。
- 初期値は `StateSpec.initial`（`LiteralInitialState`）から状態型の payload を構築して作る。実装側の既定値に委ねない【合意済み】D04 §9.1。
- `reset_on=(RUN_START,)` は run 開始時に初期値へ戻すことと定義する【合意済み】D04 §9.1。段階2の `ResetTrigger` は1値のみ。
- 更新は `ComponentOutputs.new_state` の置き換えだけで行う。差分更新・部分更新の経路を作らない。`Skipped` / `Failed` の評価では**状態を更新しない**【提案】。更新すると、欠損で飛ばした評価が再武装の判定（`EDGE`）に影響し、同じ入力でも観測遅延の有無で trace が変わる。
- 段階2では状態を run 内のメモリにだけ持ち、run を跨いで復元しない【提案】。run の途中再開は段階5以降の課題である（全体計画 §7.5）。状態の値そのものは trace に出さず、`EvaluationId` から評価履歴を辿れば再現できる。**不採用**: 毎評価で状態を trace に書く案（段階2で使う状態は真偽値1つで、記録量に見合う情報が無い）。

### 6.6 出力の付番と決定論【提案】

部品は内容を計算し、ランタイムが `OutputRecord` の共通メタデータを付ける【合意済み】上位 §4.3.15。

| フィールド | 付け方 |
|---|---|
| `output_id` | `IdAllocator.next(OutputId)` |
| `evaluation_id` | その評価の `EvaluationId` |
| `producer` | `(instance_id, output_name)` |
| `decision_time` | `batch.decision_time`（エンジンが渡した判断時刻） |
| `available_at` | 段階2は `decision_time` と同じ（計算遅延を入れない） |
| `sequence` | `step` 内の通し番号。評価順（第5.4節）と、1評価内では契約の `outputs` のキー順で決まる |

これにより、同じ宣言・同じ入力・同じ公開順から同じ `sequence` 列が出る。取引機会の記録が持つ `ProcessingPoint` も、この同じ通し番号と `PublicationBatch.phases` から引いた `PhaseRank` で組み立てる（第6.1節）。

## 7. 取引機会の状態機械

### 7.1 状態【合意済み】（Q1 決定、選択肢1。Q2 決定、選択肢1）

上位設計書 §4.5 は `ACTIVE` / `CONFIRMED` を説明用の仮置きとし、正式な語彙ではないと明記していた。**2026-09-21 に次の4つで確定し、同節を改訂した**（Q1・Q2 決定）。

| 状態 | 区分 | 意味 | 段階 |
|---|---|---|---|
| `OPEN` | 非終端 | 生成され有効。確認待ち、または発注試行が可能（上位設計書 §4.5 が `ACTIVE` と仮称していたもの） | 2 |
| `CONFIRMED` | 非終端 | 後続確認が成立した。まだ発注試行をしていない。**終端ではなく中間状態**（Q2 決定） | 3 |
| `ORDER_PENDING` | 非終端 | 注文要求をエンジンへ渡し、受付の可否が返っていない | 2 |
| `TERMINATED` | 終端 | 終端理由（`OpportunityTerminal.reason`）を伴って終わった | 2 |

- **終端は1状態にまとめ、区別は理由が持つ**【提案】。終端理由の語彙の正本は上位設計書 §4.5 であり、状態名を増やすと語彙が2系統になる（第1.1節）。
`CONFIRMED` を中間状態にしたのは、ADR-0031 が「`OrderRequest` 生成直前にも再検査し、成立しなくなれば `MARKET_STATE_INVALIDATED` で終端する」と定めており、確認成立後にも終端しうるためである（Q2 決定）。**不採用**: 確認成立を終端とし確認結果を別の記録に移す案（確認後の無効化を機会の終端として表せず ADR-0031 と矛盾する）。

発注試行中（`ORDER_PENDING`）を独立の状態にしたのは、受付結果の通知をどの機会へ適用するかを暗黙にしないためである（Q1 決定）。**不採用**: 発注試行中を別状態にしない案（同じ判断時点で上限を超えた発注試行が並びうる）、非終端を `OPEN` の1つだけにする案（確認成立と発注試行を判断履歴から区別できない）。

- **非終端状態のすべてを「有効な取引機会」と数える**【提案】。`OpportunityConcurrencySpec.max_active` の判定（第7.4節）はこの数え方による。受付待ちの機会を数から外すと、同じ判断時点で上限を超えた発注試行が並ぶ。
- 取引機会の状態と、注文・建玉の状態は分離する【合意済み】上位 §4.5。`ORDER_PENDING` は「注文がどうなったか」ではなく「機会が発注試行に進んだ」ことを表す。
- 生成時点は `created_at: ProcessingPoint`（エンジンが処理した点）と `created_decision_time: UtcTime`（判断時刻）の2つで表す【提案】。D04 §10.3 の追い出しの鍵「(取引機会の生成時点, `opportunity_id`)」はこのうち `created_decision_time` を使う。`ProcessingPoint` を鍵にすると、フェーズ順位（D06）が決まるまで鍵が定まらない。

### 7.2 遷移【提案】

すべての遷移を列挙する。フェーズは上位設計書 §4.3.12 の名前で示し、正式な列挙と順位は D06 が与える（第1.1節）。「ライフサイクル検査」は評価の前に走る独立の処理で、上位設計書 §4.3.14 が「期限・追い越し・機会失効の検査は実評価とは別のライフサイクル処理として起動できる」と述べているものである。

| # | 遷移 | 発火条件 | フェーズ | 記録する理由 | 段階 |
|---|---|---|---|---|---|
| 1 | （生成）→ `OPEN` | Trigger が発火し、有効な機会数が `max_active` 未満 | P3 | なし | 2 |
| 2 | （生成）→ `TERMINATED` | 上限に達しており `on_new_trigger=KEEP_EXISTING` | P3 | `CONCURRENCY_LIMIT_REACHED` | 2 |
| 3 | `OPEN` / `CONFIRMED` → `TERMINATED` | 上限に達しており `on_new_trigger=SUPERSEDE_EXISTING`。対象は**置換できる状態にある**最も古い機会1件（D04 §10.3 の鍵。`ORDER_PENDING` は対象外、第7.4節） | P3 | `SUPERSEDED` | 2 |
| 4 | `OPEN` / `CONFIRMED` → `ORDER_PENDING` | 注文意図と保護水準が揃い `EntryProposal` を渡した | P5 | なし | 2 |
| 5 | `ORDER_PENDING` → `TERMINATED` | 自身の注文が受け付けられた（`AdmissionNotice.accepted=True`） | D06 の受付フェーズ | `FULFILLED_BY_ORDER_ACCEPTANCE`（Q4 決定） | 2 |
| 6 | `ORDER_PENDING` → `TERMINATED` | 自身の注文が受付前の審査で拒否された（`accepted=False`） | D06 の受付フェーズ | `ORDER_ATTEMPT_REJECTED`（Q3 決定） | 2 |
| 7 | 非終端 → `TERMINATED` | 他の機会の注文が受け付けられ `on_order_accepted=CLOSE_OTHERS` | D06 の受付フェーズ | `CLOSED_BY_ORDER_ACCEPTANCE` | 2 |
| 8 | `OPEN` / `CONFIRMED` → `TERMINATED` | `REQUIRE_UNTIL_ORDER_REQUEST` の束縛条件が不成立になった | P4 と P5 直前 | `MARKET_STATE_INVALIDATED` | 2（束縛が空なら発生しない） |
| 9 | 非終端 → `TERMINATED` | run 末尾に残った（`is_run_end=True` の公開バッチを受けたとき。第6.1節、v1.1） | 末尾処理（D06 §4.1 の `RUN_END`） | `RUN_END`（Q5 決定） | 2 |
| 10 | `OPEN` → `CONFIRMED` | 後続確認が成立した | P4 | なし | 3 |
| 11 | `OPEN` / `CONFIRMED` → `TERMINATED` | `AwaitConfirmation` の期限に到達した | ライフサイクル検査 | `EXPIRED` | 3 |

- 遷移は1件ごとに `OpportunityTransition` として記録する。生成（遷移1・2）は `from_state=None` で表す。
- 終端した機会は復活させない【合意済み】ADR-0031。終端状態からの遷移は表に無く、実装は終端済みの機会への遷移要求を `KernelValueError` で拒否する。
- 理由は `Reason(code, detail=None)`（D02 §8.2）で持ち、**置き換えた相手の機会や発注試行の識別子は `OpportunityTransition` の `counterpart` / `attempt_id` に入れる**【提案】。D02 §8.2 の詳細型は1つの理由コードへ固定して対応付く（`code: ClassVar[ReasonCode]`）ため、5つの終端理由を1つの詳細型では表せない。取引機会専用の終端理由 enum も作らない。D02 §8.1 の `ReasonCode` に5語ともあり、二重定義を避ける（第1.1節）。
- 遷移5〜7は**エンジンからの通知**（`AdmissionNotice`）を次の `step` の入口で適用する。ランタイムは受付の可否を自分で決めない（リスク審査は `backtest.admission`）。
- 遷移5・6で使う2語は 2026-09-21 に人間が決定し（Q4・Q3、いずれも選択肢1）、正本である上位設計書 §4.5・§4.7.14、全体計画書 §5.3.5、D02 §8.1、ADR-0032 を同じ PR で改訂した。本書はその語を参照しているだけである（第1.1節）。`FULFILLED_BY_ORDER_ACCEPTANCE` は**自身の**注文が受け付けられて役目を終えた機会、`CLOSED_BY_ORDER_ACCEPTANCE` は**他の**機会の注文が受け付けられたために終わった機会を指し、判断履歴で両者を集計上区別できる。
- 遷移10・11（後続確認の成立と確認期限の到達）は段階3で発生する。**始点・終点・記録する理由は本書が確定しており**、期限の数え方と確認評価の起動という発火条件の詳細だけを v0.2 で埋める（第1.2節の行5）。
- 遷移9で `RUN_END` を使うのは、残存注文の取消（上位設計書 §4.7.13）と同じ語で末尾処理を読めるようにするためである（Q5 決定）。**不採用**: 終端させず「有効なまま run が終わった」として記録する案（終端理由別の集計で機会の総数が合わない）、機会専用の末尾終端理由を加える案（注文側と別語になる）。
- 遷移8の「P5 直前」は、`EntryProposal` を作る直前に束縛条件を読み直す点を指す【合意済み】ADR-0031。段階2は `bindings` が空のため実行されないが、経路は実装する（段階3で条件を足すだけで動くようにする）。

### 7.3 有効性の再検査【提案】（ADR-0031 の実施）

- `SNAPSHOT_AT_OPPORTUNITY` の束縛は、機会の生成時点で読んだ `OutputRecord` を `ValiditySnapshot` として `OpportunityLifecycle` に固定し、以後更新しない。
- `REQUIRE_UNTIL_ORDER_REQUEST` の束縛は、遷移8のタイミングで**その時点の最新出力**を読み直す。成立しなくなっていれば終端する。再検査で機会の内容（方向・`signal_interval`・`reference_values`）を変更しない【合意済み】ADR-0031。
- 再検査対象が欠損していれば `ValidityBinding.on_missing` に従う。段階2の2区分では `SkipEvaluation`（今回の再検査を行わず機会を残す）か `Error`（run を失敗させる）であり、**欠損を不成立に変換しない**【合意済み】ADR-0031。待機は段階3。
- 束縛の対象は `condition_state@v1` を出す出力に限られ、成立の判定は `ConditionState.satisfied` を読むことである【合意済み】D04 §10.2・本書 §4.2。

### 7.4 同時保持と発火の記録【提案】（ADR-0032 の実施）

Trigger の出力が出た評価では、必ず次の順で処理する。

1. `OpportunityId` を採番し、`Opportunity` を組み立てる（第4.2節）。**発火を捨てない**【合意済み】ADR-0032。
2. 有効な機会（非終端すべて）の数を数える。
3. 上限未満なら遷移1。上限に達していて `KEEP_EXISTING` なら遷移2、`SUPERSEDE_EXISTING` なら遷移3の後に新しい機会を `OPEN` にする。
3a. **発注試行中（`ORDER_PENDING`）の機会は置換の対象にしない**【提案】。注文要求は既にエンジンへ渡っており、受付の可否が返る前に機会だけを終端させると、受け付けられた注文に対応する機会が無い状態になる。渡した注文要求を取り消す経路は D06 の責務であり、段階2にはない。**置換できる機会が1件も無ければ、`SUPERSEDE_EXISTING` でも新しい発火を遷移2（`CONCURRENCY_LIMIT_REACHED`）で終端する**。`KEEP_EXISTING` と同じ結末になるが、理由は同じ「上限に達していた」であり、新しい語彙は要らない。**不採用**: 発注試行中の機会も置換する案（注文と機会の対応が壊れる）、置換できるまで新しい発火を保留する案（発火を保留する仕組みは待機であり段階3の範囲）。
4. どの経路でも `OpportunityTransition` を残す。段階2の設定（`max_active=1`、`KEEP_EXISTING`）でも2本目以降の発火が trace に残り、終端理由で区別できる【合意済み】D04 §10.3。
5. **ここまで終えてから下流へ配送する**。`TERMINATED` になった機会のイベントは配送しない（第6.2節）。遷移3で終端させた既存の機会は、既に配送済みの過去のイベントであり、遡って取り消さない。その機会が発注試行へ進む経路は終端によって閉じる。

この順序を守らないと、上限で有効化しなかった発火から注文の組ができる。「記録はするが有効にしない」（D04 §10.3）の「有効にしない」は、**下流へ配送しないこと**を意味する。

### 7.5 上位設計書 §4.5 の未解決3点の扱い

3点とも 2026-09-21 に人間が決定し（Q2・Q3・Q4、いずれも選択肢1）、上位設計書 §4.5 の「D05 で設計、未確定」の記述を本 PR で改訂した。仮置きは残していない。

| 未解決点 | 決定 |
|---|---|
| 後続確認が成立した状態は終端か中間か | **中間状態**（Q2）。`CONFIRMED` から発注試行と終端の両方へ進める（第7.1節・第7.2節の遷移4・7・8） |
| リスク審査で受付を拒否された後の状態 | **終端**し、終端理由は `ORDER_ATTEMPT_REJECTED`（Q3）。段階2は再審査なし（上位 §4.3.14 の確定規則）のため、その機会から次の発注試行は生まれない。非終端のまま残すと同時保持上限を占有し続ける |
| 自身の注文が受け付けられた後の状態 | **終端**し、終端理由は `FULFILLED_BY_ORDER_ACCEPTANCE`（Q4）。`CLOSED_BY_ORDER_ACCEPTANCE` は**他の**機会を終わらせる理由であり（上位 §4.5）、当の機会自身には使えない |

## 8. 約定後の利確の評価【提案】＋【合意済み】（Q7 決定、選択肢1）

検証戦略 A の利確は、約定価格と有効な損切り水準から決まるため、**約定が確定した後**に評価しなければならない。上位設計書 §4.3.12 の P0〜P5 は注文意図の生成で終わっており、約定後に戦略を評価する起動点が無い。

本書は `RuntimeEventNotice(POSITION_OPENED, position_id, opportunity_id)` を受けて `fixed_rr_take_profit` を評価する起動点を D06 へ要求する。**同じ判断時点の約定処理の後に `step` をもう一度呼ぶ**（Q7 決定）。次足まで待つと建玉が初期の利確水準を持たない時間帯ができ、T01 の紙上トレースに「利確なしの建玉」が現れる。**不採用**: 次の公開バッチの先頭で評価する案（次の足まで利確が無い）、エンジンが利確水準を直接計算する案（戦略の計算規則がエンジンへ漏れ、部品として差し替えられなくなる。上位設計書 §4.7.1 は初期の利確を Exit の責務としている）。

通知は**1件につき1つの評価要求**を作り、その `position_id` を `CurrentContext` の入力解決まで運ぶ（第6.2節）。同じバッチで複数の建玉が生まれても、どの建玉について評価したかが評価記録と管理要求から一意に読める。

D06 へ委ねるのは、この起動点に与えるフェーズ順位と、約定処理の後のどこに挿すかだけである。**約定処理より前に置く構成は本書が排除する**（約定価格と建玉が無ければ利確水準を計算できない）。この起動点で評価できる情報は、その時点の `RuntimeContextView` と、`decision_time` 以前に公開済みの市場データに限る。約定によって生まれた建玉を読むのであり、未来の価格を読むのではない。D04 §12 の因果辺（注文→約定起動、注文→建玉参照）は、この起動点が帰還路を作らないことをコンパイル時に保証する。

## 9. 段階2の最小範囲（検証戦略 A）と T01

D04 §14 が挙げた宣言に、本書の部品と使用箇所を対応させる。

| 使用箇所 | 契約 | 起動条件 | 入力の接続 | 出力 |
|---|---|---|---|---|
| `breakout_level` | `extreme_price` v1（`mode=MAX`、`lookback=20`） | `OnBarClose("h1", USDJPY/1h/bid)` | `prices` ← `MarketDataRef(USDJPY/1h/bid, HIGH)` | `level`: `price@v1` |
| `stop_level` | `extreme_price` v1（`mode=MIN`、`lookback=20`） | `OnBarClose("h1", USDJPY/1h/bid)` | `prices` ← `MarketDataRef(USDJPY/1h/bid, LOW)` | `level`: `price@v1` |
| `entry_trigger` | `breakout_trigger` v1（`direction=LONG`） | `OnBarClose("h1", USDJPY/1h/bid)` | `price` ← `MarketDataRef(..., CLOSE)`、`level` ← `breakout_level.level` | `opportunity`: `opportunity@v1` |
| `entry_order` | `market_order_intent` v1 | `OnInputEvent("opp", "opportunity")` | `opportunity` ← `entry_trigger.opportunity` | `intent`: `order_intent@v1` |
| `initial_stop` | `level_stop_loss` v1 | `OnInputEvent("opp", "opportunity")` | `opportunity` ← `entry_trigger.opportunity`、`level` ← `stop_level.level` | `protection`: `protection_levels@v1` |
| `take_profit` | `fixed_rr_take_profit` v1（`reward_risk=2.0`） | `OnRuntimeEvent("filled", POSITION_OPENED)` | `position` ← `RuntimeInputRef(POSITION)` | `action`: `management_action@v1` |

戦略全体の宣言は D04 §14 のとおり（`market_state=None`、`execution_filter=None`、`entry_policy=ImmediateEntry`、`opportunity_validity.bindings=()`、`opportunity_concurrency=(1, KEEP_EXISTING, KEEP_OTHERS)`）。評価順は第5.4節の規則で一意に定まり、`breakout_level` → `stop_level` → `entry_trigger` → `entry_order` → `initial_stop` → `take_profit` となる（同順位は `instance_id` 順。`take_profit` は約定による起動の因果辺で `entry_order` の下流になる）。

T01（紙上トレース）で追う1回の突破は次のとおり【提案】。

| 時点 | フェーズ | 起きること |
|---|---|---|
| T（1h 足の確定） | P1 | `breakout_level` と `stop_level` が評価され、当該足を除く20本の高値・安値を出す |
| T | P3 | `entry_trigger` が評価され、終値が高値を上回り直前が不成立なら取引機会を1件生成（遷移1） |
| T | P5 | `entry_order` と `initial_stop` が評価され、`EntryProposal` を1件返す（遷移4） |
| T | D06 の受付 | 受付なら遷移5、リスク拒否なら遷移6 |
| T | 約定後の起動点（第8節） | `take_profit` が評価され、`SetTakeProfit` を1件返す |
| T+1h 以降 | P3 | 成立が続く間は `EDGE` により再発火しない。不成立へ戻った時点で再武装される |

テスト【提案】: 単体（各部品の計算規則、`EDGE` の再武装、欠損時の `Skipped`）、意味論（上限到達時に `CONCURRENCY_LIMIT_REACHED` の機会が1件残ること、warmup 中の注文ゼロ、`Skipped` で状態が更新されないこと、終端した機会が復活しないこと）、プロパティ（同じ宣言・同じ入力から同じ `sequence` 列と同じ trace、使用箇所の宣言順を入れ替えても評価順が変わらないこと）、golden（検証戦略 A の1突破分の trace を固定）。

## 10. 対象外

本節は**時期**の線引き（段階2で作らないもの）であり、第1.2節は**担当**の線引き（本書 v0.1 が決めないもの）である。

- 待機（`WAIT_FOR_INPUT`）の意味論と宣言形、`USE_PREVIOUS` の遡り → 本書 v0.2（段階3前）。
- 評価要求の追い越し（`REQUEST_SUPERSEDED`）と `on_superseded` の既定 → 本書 v0.2。
- 後続確認（`AwaitConfirmation`・ExecutionFilter）と確認期限の数え方、開始足の扱い → 本書 v0.2。
- 合成部品（AND / OR / 遷移検出 / N 本継続 / A 後 N 本以内の B） → 本書 v0.2。
- 指標部品（EMA・ATR）と、その初期化・更新規則、NumPy を使う部品の特定 → 本書 v0.2。
- Feature の鮮度伝播と複数系列 Feature の鮮度 → 本書 v0.2。
- 再審査設定の型・配置・回数上限 → 段階6・D10（段階2は能力検査で拒否）。
- 上流の出力を履歴窓で読む仕組み（出力の履歴の保持と打ち切り規則） → 本書 v0.2（段階2は能力検査で拒否。第6.3節）。
- 部品状態の run 跨ぎの保存・復元 → 段階5以降。
- 学習する部品（fit / 推論分離）、探索空間の宣言 → D09 以降。

## 11. 上位文書との差異

1. **`position_context@v1` が供給する項目**。D04 §5 は段階2で読む項目を「約定価格・方向・数量」と書いているが、固定リスクリワード比の利確には**有効な損切り水準**が要る（第4.3節(5)）。供給範囲の正本は D06 であり、第12節で引き渡す。D04 の次回改訂で §5 の例示に1項目足す。
2. **`WarmupSpec` を段階2の部品で使わない**。`WarmupSpec.series` は具体的な `SeriesId` を持つ（D04 §9.2）ため、系列を使用箇所が選ぶ再利用可能な部品の契約には書けない。段階2はウォームアップ不足を履歴読み取りの `WARMUP_INSUFFICIENT` で判定でき（第4.3節）、完了条件「warmup 中の注文ゼロ」を満たす。系列を使用箇所から解決する規則が要るかは、EMA を足す v0.2 で扱う。
3. **初版カタログの品揃え**。全体計画 §5.3.3 が挙げる12部品は検証戦略 A と B の合計であり、段階2（戦略 A）に必要なのは5部品である（第4.3節、Q8 決定）。§5.3.3 の一覧は【提案】の印が付いており、決定との矛盾ではない。
4. **`runtime/` のモジュール**。D01 §7.2 の一覧のうち `waiting.py` / `supersession.py` は段階3で作る（第2節）。一覧の改訂は不要（初期構成であり後続文書が追加・分割できる、D01 §7.2）。

5. **置換の対象から発注試行中の機会を外す**。D04 §10.3 は「最も古い有効な取引機会を1件終端する」と定め、適用の時点と生成時点の項目名を本書へ委ねている。本書は状態機械を置いたうえで、**注文要求を既に渡した機会（`ORDER_PENDING`）を置換の対象から外す**（第7.4節）。D04 の時点では非終端の状態が無く、この区別を書けなかったためであり、「最も古いものを選ぶ」という鍵そのものは変えていない。

上記以外に、上位設計書・全体計画書・ADR・D01〜D04 と食い違う提案はない。

## 12. 他文書への引き渡し

第1.2節の境界表を正本とし、ここには**境界表に載らない細目**だけを挙げる。

| 引き渡し先 | 項目 |
|---|---|
| D06 | `position_context@v1` の項目に**有効な損切り水準**を含めること（第11節の1）。`OrderIntent.expiry=None` のときに適用する既定の有効時間（第4.3節(3)）。`AdmissionNotice` を返すフェーズと、`attempt_id` の対応付け（第7.2節）。約定後の評価起動点のフェーズ順位と挿す位置（第8節。「同じ判断時点の約定処理の後」という本書の決定を満たす範囲で）。`PhaseSet` への P1〜P5 とライフサイクル検査・約定後起動点の登録（D02 §3.3） |
| D07 | 取引機会の終端理由別の集計と、`Skipped` の診断理由別の集計（全体計画 §7.5 の「診断」） |
| D04（次回改訂） | §5 の `position_context@v1` の例示に損切り水準を足す（第11節の1）。§12 の「拒否は `ReasonCode` 付きの構造エラー」を、コンパイラ専用の区分 `CompileRejection` を使う形へ言い換える（第5.2節、Q9 決定）。本書では D04 本体を改訂しない |
| `common`（段階2の実装） | `ReasonCode` 列挙への取引機会の終端理由5件と `REQUEST_SUPERSEDED` の追加。設計側は D02 §8.1（v1.3）で確定済み【合意済み】D04 §18 |

## 13. 承認時の確認事項（2026-09-21 承認: Q1〜Q9 をすべて選択肢1 で決定）

起草時に選択式で提示した9項目。ユーザーが 2026-09-21 にすべて決定し、本文へ反映済みで、**未決の項目は残っていない**。「選択肢 n」は提示時に並べた番号で、1 が提示時の推奨案である。

| # | 決めたこと | 決定 | 反映先 |
|---|---|---|---|
| Q1 | 取引機会の非終端状態をいくつ置くか | **選択肢1（推奨）**: `OPEN` / `CONFIRMED` / `ORDER_PENDING` の3つ | §7.1、上位設計書 §4.5 |
| Q2 | 後続確認が成立した状態は終端か中間か | **選択肢1（推奨）**: 中間状態とし、発注試行と終端の両方へ進めるようにする | §7.1、§7.5、上位設計書 §4.5 |
| Q3 | リスク審査で拒否された後の取引機会をどう記録するか | **選択肢1（推奨）**: 専用の終端理由 `ORDER_ATTEMPT_REJECTED` を加える | §7.2、§7.5、ADR-0032 補足4、上位設計書 §4.5・§4.7.14、全体計画書 §5.3.5、D02 §8.1 |
| Q4 | 自身の注文が受け付けられた後の取引機会をどう記録するか | **選択肢1（推奨）**: 成功の終端理由 `FULFILLED_BY_ORDER_ACCEPTANCE` を加える | 同上 |
| Q5 | run 末尾に残った有効な取引機会をどう終端するか | **選択肢1（推奨）**: 既存の理由コード `RUN_END` で終端する | §7.2 |
| Q6 | 同じ判断時点で足の確定が複数成立したときの評価回数 | **選択肢1（推奨）**: 対象区間が同じ起動条件だけを1回に集約する | §6.2 |
| Q7 | 約定後に利確を評価する起動点をどこに置くか | **選択肢1（推奨）**: 同じ判断時点の約定処理の後に戦略ランタイムをもう一度呼ぶ | §8、§12（D06 へ引き渡し） |
| Q8 | 段階2の初版カタログの粒度 | **選択肢1（推奨）**: 役割ごとに5部品（高値と安値は1部品を2使用箇所） | §4.3 |
| Q9 | コンパイル拒否の理由をどう表すか | **選択肢1（推奨）**: コンパイラ専用の区分 `CompileRejection` を使い、共通の理由コードは実行時に限る | §5.2、§12（D04 の次回改訂へ引き渡し） |

各項目で採らなかった案は、本文の該当節に「不採用」として1行ずつ残してある。

### 13.1 Q3・Q4 で加えた2語と、正本の改訂

取引機会の終端理由の正本は上位設計書 §4.5 であり、本書は語を作らず**正本の改訂として提案し、決定後に同じ PR で反映する**（第1.1節、D04 §1.1 と同じ手順）。命名は D04 の Q10 で加えた `CONCURRENCY_LIMIT_REACHED` と同じく「何が起きて終わったか」を表す形に揃えた。

| 語 | 意味 | 既存の語と重ならない理由 |
|---|---|---|
| `ORDER_ATTEMPT_REJECTED` | 自身の発注試行が受付前の審査で拒否されて終端。段階2は再審査なしのため次の試行は生まれない | 既存5語はいずれも「審査で通らなかった」を表さない。`RISK` は受付前拒否そのものの理由コードで、取引機会の終端ではない |
| `FULFILLED_BY_ORDER_ACCEPTANCE` | 自身の注文が受け付けられ、役目を終えて終端 | `CLOSED_BY_ORDER_ACCEPTANCE` は**他の**機会の注文が受け付けられたことによる終端であり、向きが逆 |

改訂した正本: 上位設計書 §4.5（終端理由の表と、完全な状態機械の節）・§4.7.14（理由コードの表）、全体計画書 §5.3.5（終端理由の行）、D02 §8.1（理由コードの表、v1.4）、ADR-0032（決定の補足4 と改訂履歴・影響）。`common` の `ReasonCode` 列挙への追加は、取引機会の状態機械を実装する段階2 で行う（第12節）。

## 14. 本書の後続版（v0.2）

承認の範囲は第1.2節の表の第3列である。**待機・評価要求の追い越し・後続確認**は v0.2（段階3前）で足す（第10節）。v0.2 で足すときに、第7.2節の遷移10・11（確認成立と期限切れ）の発火条件も確定する。
