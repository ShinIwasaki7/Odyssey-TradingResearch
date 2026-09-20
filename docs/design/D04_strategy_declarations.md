# D04: 戦略宣言モデル設計（`odyssey_fx.strategy.declarations`）

作成日: 2026-09-20
状態: **草案 v0.1。承認待ち**。段階2（最小縦断）と紙上トレース T01 に必要な範囲だけを扱う。将来機能は第15節で明示的に対象外とする。PR #14 の Codex 指摘7件（1巡目3件・2巡目4件）を反映済み。1巡目: ダイジェスト対象から実装参照を分離（第13.2節・Q2）、状態の初期値宣言を追加（第9.1節）、価格パラメータを float のまま保持し `Price` 変換を D06 の境界に置く（第7節）。2巡目: 建玉・口座を読む入力の許可組合せを表で明示（第6.1節）、約定通知で評価を起動する区分を追加（第8節）、使用箇所の一覧を識別子順に正規化（第3節）、銘柄をグラフ上で伝播させて一致検査を実装可能にした（第5節）。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.3.2〜§4.3.11・§4.3.15・§4.5・§4.6・§4.7.1、[全体計画書](fx_research_platform_overall_plan.md) §5.3.1〜§5.3.4・§7.3 前半、[D01](D01_architecture_and_dependency_rules.md) §2・§3・§5・§7.2・§8・§10.1、[D02](D02_common_kernel.md)、[D03](D03_marketdata_and_time.md) §3.1〜§3.3・§6・§7、ADR-0011（frozen dataclass）、ADR-0016（実装開始条件）、ADR-0018（設定は YAML）、ADR-0021（NumPy の許可範囲）、ADR-0031（確認待ち中の条件再検査）、ADR-0032（再発火と複数取引機会）、ADR-0033（評価要求の追い越しの改名）
対応段階: 段階2で実装。ADR-0016 条件2 のうち D04 を充足する。

## 0. 本書の位置付けと凡例

`odyssey_fx.strategy.declarations` に置く宣言型の構造・不変条件・検証時点・設定ファイル表現を決める。部品の計算規則、ランタイムの状態機械、注文の受付・執行は決めない。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 本書では決めない。第19節に一覧し、選択式で承認を取る |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | `ComponentContract` / `ComponentInstance` / `StrategyDefinition` と補助型。`backtest`・`evaluation`・`compiler`・`runtime` が読む | 【合意済み】D01 §1 |
| 依存できるもの | Python 標準ライブラリ、`odyssey_fx.common`、`marketdata.domain` のみ。外部ライブラリと I/O を持たない | 【合意済み】D01 §3.2・§5 |
| 決めないもの | 部品の計算規則（D05）、取引機会の状態機械（D05）、注文・約定・リスク（D06）、指標定義（D07） | 【合意済み】 |
| 層 | `strategy` の最下層。`records`・`catalog`・`compiler`・`runtime` から参照されるが、これらを参照しない | 【合意済み】D01 §3.3 |

宣言型はすべて `@dataclass(frozen=True, slots=True)`、コレクションは `tuple` または凍結 `Mapping`、区分タグ付き union は `kind` フィールドを持つ dataclass の `Union` とする【合意済み】D01 §8・ADR-0011。

## 2. モジュール構成【提案】

D01 §7.2 の 12 モジュールに `opportunity.py` を加える（サブパッケージ内へのモジュール追加。D01 の改訂は次回改訂時に §7.2 の一覧へ追記）。

| モジュール | 内容 |
|---|---|
| `contract.py` / `instance.py` / `definition.py` | 3クラス（第3節） |
| `specs.py` | `InputSpec` / `OutputSpec` / `InputBinding` / `InputArity` / `ParameterSpec` / `ParameterValue`（第4節・第7節） |
| `refs.py` | `OutputRef` / `MarketDataRef` / `RuntimeInputRef`（第4.3節） |
| `read_spec.py` | `InputReadSpec` の4区分と窓型（第6節） |
| `missing.py` | `MissingInputPolicy`（第6.3節） |
| `evaluation.py` | `EvaluationSpec` / `EvaluationSchedule`（第8節） |
| `state_spec.py` / `temporal.py` | `StateSpec` / `TemporalConstraints`（第9節） |
| `entry_policy.py` | `EntryPolicy`（第10.1節） |
| `opportunity.py`（追加） | `OpportunityValiditySpec` / `ValidityBinding` / `OpportunityConcurrencySpec`（第10.2〜10.3節） |
| `datatypes.py` | `DataTypeRef` のレジストリ（第5節） |

## 3. 3クラスのトップレベル

トップレベルのフィールド名・型・必須性は上位設計書 §4.3.5 を正本とし、本書は再掲のみ行う【合意済み】。`ComponentContract` は `component_id` / `version` / `implementation_ref` / `inputs` / `outputs` / `parameters` / `evaluation_spec` / `state_spec` / `temporal_constraints`、`ComponentInstance` は `instance_id` / `contract_ref` / `inputs` / `parameters` / `evaluation`、`StrategyDefinition` は `strategy_id` / `version` / `components` / `market_state` / `trigger` / `execution_filter` / `order` / `protection` / `exit` / `entry_policy` / `opportunity_validity` / `opportunity_concurrency`。

本書が追加を検討するのは保存形式の版（スキーマ版）の置き場所だけである → 第19節 Q1【要決定】。

不変条件【提案】: `component_id` は `^[a-z0-9_]+$`、`version >= 1`、`instance_id` は同一戦略内で一意で `^[a-z0-9_]+$`、`components` は 1 件以上、`inputs` / `outputs` / `parameters` のキーは `^[a-z0-9_]+$`。違反は `KernelValueError`（D02 §10）。

`components` の正規化【提案】: 並び順で実行順序を指定しないため【合意済み】§4.3.5、`StrategyDefinition` は構築時に `components` を `instance_id` の Unicode コードポイント順へ並べ替えて保持する。D02 §3.3 の `PhaseSet` と同じ扱いであり、設定ファイル内の記述順が内容ハッシュ（第13.2節）に影響しないようにする。

## 4. 入出力と接続

### 4.1 仕様型【提案】（上位設計書 §4.3.8 の案を確定させる）

| 型 | フィールド |
|---|---|
| `InputSpec` | `data_type: DataTypeRef`、`kind: PortKind`、`arity: InputArity`、`read_spec: InputReadSpec` |
| `OutputSpec` | `data_type: DataTypeRef`、`kind: PortKind`、`reference_schema`（第11.1節 → Q7） |
| `InputBinding` | `sources: tuple[InputSourceRef, ...]` |
| `InputArity` | `min_count: int`（`>= 1`）、`max_count: int \| None`（`None` は上限なし、指定時は `>= min_count`） |

入力名・出力名は `Mapping` のキーが持ち、Spec の内部に重複させない【合意済み】。読み取り条件は `InputSpec` だけが持ち、`InputBinding` は接続先だけを持つ【合意済み】。接続元がないことと、接続元の実行時データが欠損していることは別に扱う【合意済み】。

### 4.2 `PortKind`【合意済み】

`VALUE`（更新まで繰り返し参照する値）、`EVENT`（一度発生した事実・機会）、`COMMAND`（処理を依頼する要求）の小さな enum。

### 4.3 参照元 `InputSourceRef`【提案】

`kind` タグ付きの3区分。

| 区分 | フィールド | 備考 |
|---|---|---|
| `OutputRef` | `instance_id: str`、`output_name: str` | 上位設計書 §4.3.5【合意済み】 |
| `MarketDataRef` | `series: SeriesId`（D03 §3.1）、`field: MarketDataField` | `MarketDataField` は `OPEN`/`HIGH`/`LOW`/`CLOSE`/`VOLUME`【合意済み】。`OPEN` 指定は未確定足への参照権を意味しない |
| `RuntimeInputRef` | `target: RuntimeTarget` | 初版の区分は `POSITION` と `ACCOUNT` の2つだけ。`PENDING_ORDER` は列挙に含めず、宣言に現れたらコンパイル時に拒否する（全体計画 §7.3・§5.3.4 の能力検査） |

## 5. データ型レジストリ `DataTypeRef`

登録制とし、Python のクラス名を動的読込しない【合意済み】。`DataTypeRef(type_id: str, version: int)`、`__str__` は `"<type_id>@v<version>"`（D02 §6 の `TimeframeRef` に合わせる）【提案】。

初版の登録【提案】: `price`、`price_offset`、`ratio`、`condition_state`、`market_permission`、`opportunity`、`confirmation_result`、`order_intent`、`protection_levels`、`management_action`（すべて version 1）。各登録は「正規化エンコード可能な payload 構造」と「対応する `records` の型」を持ち、レジストリは `declarations` 内の静的テーブルとする（実行時の登録 API を持たない）。

接続検証【提案】: 接続元 `OutputSpec` と接続先 `InputSpec` で `(type_id, version)` が一致すること。単位は `ParameterSpec` 側（第7節）が持ち、データ型には持たせない。

銘柄の一致検査【提案】: `DataTypeRef` は銘柄を持たないため、銘柄はコンパイラがグラフ上を伝播させて決める。各使用箇所の銘柄は「その部品の入力に接続された `MarketDataRef` の銘柄」と「上流の使用箇所から伝播した銘柄」の和集合とし、集合が2つ以上になった使用箇所は拒否する（初版は単一銘柄。上位設計書 §4.7.1）。`MarketDataRef` を1つも辿れない使用箇所は銘柄なしとして扱い、`price` 系の出力を持てない。これにより `OutputRef` 経由の価格入力にも銘柄の一致検査が効く。複数銘柄を扱う伝播規則は D10・段階6。

## 6. 読み取り条件と欠損方針

### 6.1 `InputReadSpec`【合意済み】（上位設計書 §4.3.10）

| 区分 | フィールド |
|---|---|
| `LatestAvailable` | `max_age: timedelta \| None`、`on_missing: MissingInputPolicy` |
| `HistoryWindow` | `window: BarsWindow \| DurationWindow`、`max_age: timedelta \| None`、`on_missing: MissingInputPolicy`、`exclude_latest_bars`（第6.2節【提案】） |
| `DeliveredEvent` | 追加フィールドなし |
| `CurrentContext` | 追加フィールドなし |

`PortKind` × 参照元 × 読み方の許可表【提案】。表にない組合せはコンパイル時に拒否する（上位設計書 §4.3.9）。

| `PortKind` | 参照元 | 許可する `InputReadSpec` |
|---|---|---|
| `VALUE` | `OutputRef` / `MarketDataRef` | `LatestAvailable`、`HistoryWindow` |
| `VALUE` | `RuntimeInputRef` | `CurrentContext` のみ |
| `EVENT` | `OutputRef` | `DeliveredEvent` のみ |
| `COMMAND` | — | 段階2では入力に接続しない |

建玉・口座を読む入力（`RuntimeInputRef`）は `PortKind.VALUE` ＋ `CurrentContext` で宣言する。現在処理中の時点情報を1回の評価内で参照するものであり、配送イベントでも履歴でもないためである。

### 6.2 窓と「当該足を除く」【提案】

- `BarsWindow(count: int | ParameterRef)`（`count >= 1`）、`DurationWindow(duration: timedelta)`。部分履歴は許可せず `min_samples` は持たない【合意済み】。
- D03 §6.2 が D04 に委ねた `end_offset_bars` の宣言側の名前を **`exclude_latest_bars: int`（既定 0、`>= 0`）** とする。「直近 N 本高値（当該足を除く）」は `HistoryWindow(BarsWindow(N), exclude_latest_bars=1)` で表す。
- `ParameterRef(parameter_name: str)` は同じ部品のパラメータを指し、コンパイル時に具体値へ解決する【合意済み】上位設計書 §4.3.10。

### 6.3 `MissingInputPolicy`【提案】＋【要決定 Q8】

動作区分は `SKIP_EVALUATION` / `ERROR` / `WAIT_FOR_INPUT` / `USE_PREVIOUS` の4つ【合意済み】。段階2で意味が確定しているのは前2者だけであり、後2者のフィールド（待機期限・期限切れ処理・対象区間・再開時刻・`on_superseded`／遡り上限・記録・許可する欠損理由）は D05 で確定する。段階2の型に後2者の区分を含めるかは Q8。

診断理由は D02 §8.3 の `MissingInputReason`（`WARMUP_INSUFFICIENT` / `INPUT_MISSING_OR_INVALID` / `LATEST_BAR_UNAVAILABLE` / `MAX_AGE_EXCEEDED`）を再利用し、`declarations` 側で新しい語彙を作らない【提案】。`SKIP_EVALUATION` は False や価格 0 の出力ではなく、評価記録として残す【合意済み】。

## 7. パラメータ

| 型 | フィールド | 印 |
|---|---|---|
| `ParameterSpec` | `value_type: ParameterType`（`BOOL`/`INT`/`FLOAT`/`STR`）、`unit: UnitRef \| None`、`bounds: NumericBounds \| None`、`allowed_values: tuple[...] \| None`、`default: ParameterValue \| None` | 【提案】 |
| `ParameterValue` | `kind` タグ付きの `BoolValue` / `IntValue` / `FloatValue` / `StrValue` | 【合意済み】§4.3.5（`bool` と `int` を区別、`float` は有限値のみ） |
| `NumericBounds` | `minimum` / `maximum`（いずれも `None` 可）、`minimum_inclusive: bool`、`maximum_inclusive: bool` | 【提案】 |
| `UnitRef` | `PIPS` / `PRICE` / `RATIO` / `BARS` / `DURATION` の enum | 【提案】初版はこの5値 |

`Decimal` と `Price` をパラメータ値の型に入れない【提案】: `ParameterValue` は上位設計書 §4.3.5 が確定した4区分（`bool` / `int` / `float` / `str`）のままとし、価格・pips・比率は `FLOAT` ＋ `unit` で宣言する。`app.config` は有限 float として読み込み、`ComponentInstance.parameters` にもコンパイル後もその値のまま保持する。`Price`（D02 §4.3）への変換と価格刻みでの丸めは、銘柄仕様と丸め方向を持つ D06 の境界で行い、`declarations` と `app.config` では行わない。省略時の既定値を使った場合も、実行前に有効値を確定して記録する【合意済み】§4.3.5。

## 8. 評価スケジュール【提案】

| 型 | フィールド |
|---|---|
| `EvaluationSpec` | `allowed: tuple[AllowedTrigger, ...]`、`fixed: bool`（契約が評価条件を固定するか）、`required_inputs: Mapping[str, tuple[str, ...]]`（起動条件名 → 必須入力名） |
| `EvaluationSchedule` | `triggers: tuple[EvaluationTrigger, ...]`（1件以上） |
| `EvaluationTrigger` | `kind` タグ付き。`OnBarClose(name: str, series: SeriesId)` / `OnInputEvent(name: str, input_name: str)` / `OnRuntimeEvent(name: str, event: RuntimeEventKind)` |

- 起動条件に名前（`name`）を付け、`required_inputs` と対応付ける【提案】。これがないと「どの起動条件でどの入力が必須か」を宣言できない（上位設計書 §4.3.9 の残項目）。
- `OnBarClose` は D03 §7.2 の `ScheduledBoundary` に結び付く【合意済み】D03。
- `OnRuntimeEvent`【提案】: 上位設計書 §4.3.5 が `EvaluationSchedule` の対象に挙げている「約定通知」を表す区分。`RuntimeEventKind` は段階2では `POSITION_OPENED` の1値のみとし、それ以外（決済通知・保護水準の更新通知など）は段階3で追加する。これがないと、検証戦略 A の「約定価格から固定リスクリワード比の利確水準を決める Exit 部品」を約定時点で評価できず、次の足まで初期の利確水準が付かない。イベントの供給元は `backtest.engine`（`RuntimeContextView` ポート）で、フェーズ順序と供給の詳細は D06。
- 同時刻に複数の起動条件が成立した場合の配送・評価回数は D05【合意済み】全体計画 §7.3。
- 同時刻の実行優先度を部品ごとの自由な整数で指定しない【合意済み】§4.3.5。上流の更新だけで下流を自動評価しない【合意済み】§4.3.2。

## 9. 状態と時刻制約

### 9.1 `StateSpec`【提案】＋【要決定 Q3】

`StateSpec(state_type: DataTypeRef, initial: StateInitializer, reset_on: tuple[ResetTrigger, ...])`。状態を持たない部品は `state_spec=None`【合意済み】。契約と実装の状態型を二重定義せず、`catalog` の登録時に実装の状態型と `state_type` の一致を検査する【合意済み】§4.3.7。保存・復元の詳細は D05。段階2でどこまで状態を許すかは Q3。

- `StateInitializer`【提案】: `kind` タグ付きで、初版は `LiteralInitialState(values: Mapping[str, ParameterValue])` の1区分のみ。初期値を宣言に書き切り、実装側の既定値に委ねない。高値突破 Trigger の再武装（Q4）であれば「起動時は発火可能（armed）か否か」をここで宣言する。
- `ResetTrigger` は初版 `RUN_START` のみ。リセットは `initial` と同じ値へ戻すことと定義し、「リセット時だけ別の値」を持たせない。
- これにより、同じ宣言からは同じ初期状態になり、段階2の完了条件「同一入力の再実行で trace が一致」（全体計画 §8.2）が実装に依存しなくなる。

### 9.2 `TemporalConstraints`【提案】

`TemporalConstraints(warmup: WarmupSpec | None, alignment: tuple[AlignmentRequirement, ...])`。追加制約がない部品も本型を保持し、空であることを明示する【合意済み】§4.3.7。

- `WarmupSpec(series: SeriesId, bars: int | ParameterRef)`: 部品全体が有効な出力を出すまでに必要な確定足数。段階2の完了条件「warmup 中の注文ゼロ」（全体計画 §8.2）はこの宣言で判定する。
- `AlignmentRequirement(input_names: tuple[str, ...], rule: SAME_OBSERVATION_INTERVAL)`: 段階2は規則1種類のみ。入力を跨ぐ時刻整合性はここだけが持ち、`InputSpec` に重複させない【合意済み】§4.3.7。複数系列の整合規則の拡張は D05。

## 10. 取引機会に関する宣言

### 10.1 `EntryPolicy`【提案】

`kind` タグ付き2区分。

| 区分 | フィールド | 段階 |
|---|---|---|
| `ImmediateEntry` | なし | 段階2で使う |
| `AwaitConfirmation` | `deadline: BarsDeadline \| DurationDeadline`、`on_deadline: EXPIRE` | 宣言は段階2で定義、能力検査で拒否（段階3で有効化） |

再発火は `EntryPolicy` の責務から外す【合意済み】ADR-0032。`execution_filter` が `None` なら `ImmediateEntry`、`OutputRef` があれば `AwaitConfirmation` であることをコンパイル時に照合する【提案】。

### 10.2 `OpportunityValiditySpec` と `ValidityBinding`【提案】＋【要決定 Q5】

`OpportunityValiditySpec(bindings: tuple[ValidityBinding, ...])`、`ValidityBinding(source: OutputRef, mode: SNAPSHOT_AT_OPPORTUNITY | REQUIRE_UNTIL_ORDER_REQUEST, on_missing: MissingInputPolicy)`。必須指定・暗黙の既定値なし【合意済み】ADR-0031。即時発注（段階2）での最小表現は Q5。

入力ポートの時間的束縛【提案】: 「対象区間束縛」は `SNAPSHOT_AT_OPPORTUNITY`（取引機会生成時の `OutputRecord` を固定）、「現在状態束縛」は `REQUIRE_UNTIL_ORDER_REQUEST`（再検査時点の最新出力を読む）で表し、`InputReadSpec` に新しいフィールドを足さない。再検査の起動点（確認評価時・`OrderRequest` 生成直前）の実装は D05【合意済み】全体計画 §7.3。

### 10.3 `OpportunityConcurrencySpec`【要決定 Q6】

必須指定・暗黙の既定値なし、置換の禁止、終端理由の語彙（`EXPIRED` / `MARKET_STATE_INVALIDATED` / `SUPERSEDED` / `CLOSED_BY_ORDER_ACCEPTANCE`）は確定済み【合意済み】ADR-0032・上位設計書 §4.5。段階2の最小フィールド構成は Q6。非終端の状態名と遷移は D05。

### 10.4 Trigger の再武装【要決定 Q4】

条件が true であり続ける場合に再発火とするかどうかは Trigger 部品の契約と状態が持つ【合意済み】ADR-0032。その宣言形式と段階2の値域は Q4。

## 11. 役割出力に関する宣言

### 11.1 `Opportunity.reference_values` のスキーマ【要決定 Q7】

突破水準などの根拠値を契約で名前と型を宣言して保持する【合意済み】§4.3.15。宣言の置き場所が Q7。

### 11.2 `ManagementAction` の要求種別【提案】

初版は `SET_TAKE_PROFIT`（初期 TP）と `CLOSE_POSITION`（全数量決済）の2種別。`UPDATE_STOP`（トレーリング）は段階3。水準は解決済みの絶対価格で渡し、距離型は使わない【合意済み】§4.7.1・§4.7.3。要求の実行意味論は D06。

### 11.3 Exit の複数ルール合成【提案】

`exit` は `OutputRef` 1件のみ【合意済み】§4.3.5。複数ルールは明示的な合成部品で表すが、合成部品の契約は D05・段階3。段階2は単一の Exit 部品に限る。

## 12. コンパイル時検査と能力検査

宣言側から要求する検査（全体計画 §5.3.4 の1〜7に対応）【提案】。

| # | 検査 |
|---|---|
| 1 | 参照の存在: `OutputRef` の `instance_id` / `output_name`、`ContractRef` の版と digest（D02 §9.2） |
| 2 | 型の整合: `(type_id, version)` 一致、`price` 系の銘柄一致、`arity`、`PortKind` × `InputReadSpec` の組合せ（第6.1節） |
| 3 | パラメータ: 名前・型・範囲・列挙値、`ParameterRef` の具体値への解決 |
| 4 | 評価スケジュールが `EvaluationSpec.allowed` の範囲内で、`fixed=True` の契約を上書きしていないこと、`required_inputs` の入力が接続済みであること |
| 5 | 役割フィールドの型要求（`trigger`→`opportunity`、`order`→`order_intent`、`protection`→`protection_levels`、`exit`→`management_action`、`market_state`→`market_permission`、`execution_filter`→`confirmation_result`）と、`execution_filter` の有無と `entry_policy` モードの整合、`opportunity_validity` の各 `ValidityBinding` が指す出力の存在と型 |
| 6 | 依存グラフの循環検出と評価順の導出（時間足から順序を推測しない） |
| 7 | 能力検査（下表） |

段階2で拒否する構成【提案】: `RuntimeInputRef(PENDING_ORDER)`、`AwaitConfirmation`、`execution_filter` が `None` でない戦略、`MissingInputPolicy` の `WAIT_FOR_INPUT` / `USE_PREVIOUS`、`POSITION_OPENED` 以外の `RuntimeEventKind`、複数銘柄に跨る使用箇所（第5節）、15m より細かい足、距離型 SL、指値、`UPDATE_STOP`、および Q3 の結論によっては `state_spec` が `None` でない部品。拒否は `ReasonCode`（D02 §8.1）付きの構造エラーとし、黙って無視しない。

## 13. 設定ファイル表現と内容ハッシュ

### 13.1 YAML 表現【提案】

`configs/strategies/<strategy_id>_v<version>.yaml`（D01 §10.1）。ADR-0018 の読込条件（安全な読込、カスタムタグ禁止、重複キーはエラー、merge key 禁止、`schema_version` 必須、未宣言キー拒否）をそのまま適用する【合意済み】。区分タグ付き union は `kind:` キーで表す【合意済み】D01 §8。部品契約（`ComponentContract`）は YAML に書かず、`catalog` のコード側の登録を正本とする【提案】。戦略ファイルが書くのは `ComponentInstance` と役割参照・方針だけである。

`app.config` が Pydantic v2 で検証してから frozen dataclass へ変換し、Pydantic モデルを `app.config` の外へ出さない【合意済み】ADR-0018・D01 §5。`declarations` 側の `__post_init__` は構造的な不変条件（第3節）だけを見る。参照解決・型整合は `compiler` が行い、3箇所で同じ規則を重複実装しない【提案】。

### 13.2 内容ハッシュ【提案】＋【要決定 Q2】

D02 §9.3 の `canonical.digest` をそのまま使う。ダイジェスト対象の構造（何を含めるか）を決めるのは各設計文書の責務であり（D02 §9.4 末尾）、本書は `strategy` の3つの参照について次を定める。

| 参照 | ダイジェスト対象【提案】 |
|---|---|
| `ContractRef.digest` | `ComponentContract` のうち **`implementation_ref` を除いた**全フィールド。契約の「受け口・出し口・評価条件・状態・時刻制約」の同一性を表す |
| `StrategyRef.digest` | `StrategyDefinition` 全体。各 `ComponentInstance` は `contract_ref` を通じて上記の契約ダイジェストを含むため、**実装コードの同一性は含まない** |
| `CompiledStrategyRef.digest` | 解決済み設定（具体値へ解決したパラメータ、評価順）に加え、使用する各部品の `ImplementationRef`（ID ＋内容ハッシュ）を含む |

`strategy_id` / `version` が同じでもパラメータ割当が違えば別の実行として扱う【合意済み】§4.3.5。この3行の切り分けを採るかどうかが Q2 であり、Q2 で別案を採る場合は上表も合わせて変える。

## 14. 段階2の最小範囲（検証戦略 A）と T01

上位設計書 §7.1 の検証戦略 A（1h 終値で直近の確定高値突破 → 後続確認なしの成行注文 → 初期 SL と固定 RR の TP）を宣言できることが、段階2の必要十分条件である。

| 必要な宣言 | 使う型 |
|---|---|
| 直近 N 本高値（当該足を除く） | `MarketDataRef(USDJPY/1h/bid, HIGH)` ＋ `HistoryWindow(BarsWindow(N), exclude_latest_bars=1)` |
| 高値突破 Trigger | `OnBarClose(1h)` 起動、出力 `opportunity`、`reference_values` に突破水準（Q7）、再武装（Q4） |
| 成行注文意図 | `OnInputEvent(取引機会)` 起動、出力 `order_intent` |
| 初期 SL | 確定情報から絶対価格、出力 `protection_levels` |
| 固定 RR の TP | `OnRuntimeEvent(POSITION_OPENED)` 起動、`RuntimeInputRef(POSITION)` ＋ `CurrentContext` 入力、出力 `management_action`（`SET_TAKE_PROFIT`） |
| 戦略全体 | `market_state=None`、`execution_filter=None`、`entry_policy=ImmediateEntry`、`opportunity_validity`（Q5）、`opportunity_concurrency`（Q6） |

T01（紙上トレース）では、この宣言から D06 の注文・約定、D07 の単一評価まで紙上で追跡し、同時刻・数量・末尾処理に暗黙の前提がないことを確認する【合意済み】ADR-0016。

テスト【提案】: 単体（不変条件違反の拒否、`exclude_latest_bars` の境界、`ParameterRef` の解決）、意味論（未宣言キーの拒否、役割の型不一致の拒否、能力検査の各拒否が理由コード付きで返ること）、プロパティ（同じ宣言から同じ digest、`Mapping` のキー順やファイル内の並び順に依存しないこと）、golden（検証戦略 A の YAML → `CompiledStrategy` の固定出力）。

## 15. 対象外（段階3以降）

- 後続確認（`AwaitConfirmation`）と確認期限の宣言 → 段階3・D05。
- `WAIT_FOR_INPUT` / `USE_PREVIOUS` のフィールド確定 → 段階3・D05。
- 合成部品（AND / OR / 遷移検出 / N 本継続 / A 後 N 本以内の B）の契約 → D05。
- 取引機会の非終端の状態名と遷移 → D05。
- 再審査設定、距離型 SL、指値、複数建玉・複数銘柄（銘柄の伝播規則を含む）、分割決済 → 段階6・D10。
- `POSITION_OPENED` 以外の実行時イベント（決済通知、保護水準の更新通知）での評価起動 → 段階3・D05。
- 学習する部品（fit / 推論分離）、探索空間の宣言 → D09 以降。
- `RuntimeInputRef(PENDING_ORDER)` → 未対応として拒否を維持。
- 部品カタログの指標一覧と計算規則（EMA の初期化・更新方法を含む） → D05。

## 16. 上位文書のバックログ項目との対応（漏れ確認）

全体計画書 §5.3.1 と §7.3 前半の全項目を本書のどこで扱ったかを示す。

| 上位の項目 | 本書 | 印 |
|---|---|---|
| 区分値の enum と型付き union の区別 | §1・§4.3・§6.1 | 【合意済み】 |
| `DataTypeRef` の登録・版固定・接続検証 | §5 | 【提案】 |
| スキーマ版と部品・戦略の版の区別／**スキーマ版の保存場所** | §3・§19 Q1 | 【要決定】 |
| 構築・読込・コンパイル時の検証（検証ライブラリ B-3） | §13.1 | 【合意済み】ADR-0018 |
| `InputSpec` / `OutputSpec` / `InputBinding`、`InputArity`、複数入力 | §4.1 | 【提案】 |
| `ParameterSpec` / `ParameterValue` | §7 | 【提案】 |
| `EvaluationSpec` / `EvaluationSchedule`、複数起動条件、起動条件ごとの必須入力 | §8 | 【提案】 |
| `MissingInputPolicy` の動作タグごとの型 | §6.3・§19 Q8 | 【要決定】 |
| `StateSpec` | §9.1・§19 Q3 | 【要決定】 |
| `TemporalConstraints` | §9.2 | 【提案】 |
| `EntryPolicy` のモード別型 | §10.1 | 【提案】 |
| Trigger の遷移検出・再武装規則 | §10.4・§19 Q4 | 【要決定】 |
| `OpportunityValiditySpec` / `ValidityBinding` | §10.2・§19 Q5 | 【要決定】 |
| `OpportunityConcurrencySpec` | §10.3・§19 Q6 | 【要決定】 |
| `RuntimeInputRef` の対象区分 | §4.3 | 【提案】 |
| 入力ポートの時間的束縛（対象区間束縛／現在状態束縛） | §10.2 | 【提案】 |
| 設定ファイル表現・内容ハッシュの算出規則 | §13・§19 Q2 | 【要決定】 |
| `Opportunity.reference_values` のスキーマ宣言 | §11.1・§19 Q7 | 【要決定】 |
| `ManagementAction` の要求種別 | §11.2 | 【提案】 |
| Exit 複数ルールの合成 | §11.3 | 【提案】 |

## 17. 上位文書との差異

1. **本書の対象範囲が全体計画書 §8.1 の記載より狭い**。§8.1 は D04 を「第5.3.1〜5.3.4節」とし、部品カタログ（§5.3.3）とコンパイラ（§5.3.4）を含めている。本書は宣言モデル（§5.3.1）と、宣言から導かれるコンパイル時検査の一覧（第12節）までを扱い、カタログの部品一覧・計算規則とコンパイラの実装（依存グラフ構築、ハッシュ計算の内部）を D05 へ送っている。§7.3 のバックログではカタログが D05 側に列挙されており、§8.1 と §7.3 の記載が食い違っている。どちらに合わせるかは第19節 Q9。
2. **D01 §7.2 のモジュール一覧に `opportunity.py` がない**。ADR-0031・ADR-0032 は D01 承認後の決定であり、D01 の一覧が追いついていない。第2節で追加を提案し、D01 の次回改訂で一覧へ追記する（D02 が `canonical.py` / `errors.py` を追加した前例と同じ扱い）。
3. **D03 §6.2 の `end_offset_bars` を宣言側では `exclude_latest_bars` と呼ぶ**（第6.2節）。D03 は「宣言側のフィールド名は D04 で決める」としており、差異ではなく委任の履行である。ビュー側の引数名を合わせるかは D05 で決める。

上記以外に、上位設計書・全体計画書・ADR と食い違う提案はない。

## 18. D05〜D07 への引き渡し事項

| 引き渡し先 | 項目 |
|---|---|
| D05 | `WAIT_FOR_INPUT` / `USE_PREVIOUS` のフィールド、取引機会の非終端の状態名と遷移、再検査の起動点、同時刻の複数起動条件の配送・評価回数、合成部品の契約、初版カタログの指標一覧と計算規則、NumPy を実際に使う部品の特定（ADR-0021）、状態の保存・復元、依存グラフ構築とハッシュ計算の実装、`MarketDataView` の引数名の整合 |
| D06 | `OrderIntent` / `ProtectionLevels` / `ManagementAction` を受け取ってからの注文状態・執行意味論、`RuntimeInputRef(POSITION/ACCOUNT)` として供給する情報の具体、`ConfigDigest` に戦略の digest をどう含めるか、単位付き float パラメータから `Price` への変換と価格刻みの丸め方向（第7節）、`POSITION_OPENED` イベントの発生フェーズと供給方法（第8節） |
| D07 | `CompiledStrategyRef` を実験 manifest に固定する方法、評価側から見た戦略の同一性 |
| D01（次回改訂） | §7.2 のモジュール一覧へ `opportunity.py` を追記 |

## 19. 要決定一覧（承認時に選択）

各項目は「何を決めるか／結果への影響／選択肢（推奨を先頭）／推奨理由」の順に書く。

**Q1 スキーマ版（3クラスの保存形式の版）の保存場所**
決めること: 保存形式の版を宣言型のフィールドに持つか、設定ファイルだけに持つか。
影響: 保存形式を変えたときに、過去の戦略定義の内容ハッシュが変わるかどうかが決まる。
1.（推奨）3クラスのトップレベルに持つ — ハッシュに含まれ、保存形式の変更が再現性の差として現れる。
2. YAML の `schema_version` だけに持つ — 型は軽いが、ハッシュから保存形式が分からない。
3. `CompiledStrategy` にだけ記録する — 宣言の同一性と保存形式の版が分離する。
推奨理由: 段階2の完了条件が「同一入力の再実行で trace が一致」であり、解釈規則の版が同一性に含まれる方が安全。

**Q2 実装コードの同一性を戦略のダイジェストのどこに入れるか**
決めること: `ImplementationRef` を契約ダイジェスト（`ContractRef.digest`）に含めるか、解決済み設定のダイジェスト（`CompiledStrategyRef.digest`）にだけ含めるか。契約ダイジェストは `StrategyRef.digest` に入れ子で入るため、ここに含めると実装変更が戦略の digest まで波及する。
影響: 部品の実装だけを変更したときに、同じ戦略を別物として扱うかどうかが決まる。
1.（推奨）契約ダイジェストから `implementation_ref` を除き、解決済み設定のダイジェストにだけ含める — 人間が管理する戦略の版は安定し、実行の同一性だけが実装差を検出する。
2. 契約ダイジェストに含める（第13.2節の表を変更） — 実装変更のたびに契約と戦略の digest が変わり、再現性の差は早く出るが戦略の版が不安定になる。
3. どちらにも含めず `RunId` の `CodeDigest` に任せる — 戦略単位・部品単位では実装差を検出できない。
推奨理由: 上位設計書 §4.3.5 の「人間が管理する戦略の版と、各評価の解決済み設定の識別を分ける」に一致する。

**Q3 段階2で状態を持つ部品をどこまで許すか**
決めること: `state_spec` が `None` でない部品を段階2で使えるようにするか。
影響: 高値突破 Trigger の再武装（Q4）を段階2で表現できるかどうかが決まる。
1.（推奨）Trigger の再武装に必要な最小状態だけ許す — 段階2の実装量を抑えつつ再発火の意味を固定できる。
2. 任意の部品で状態を許す — EMA 等も段階2に入り、実装と検証の範囲が広がる。
3. 一切許さない（全部品 `state_spec=None`） — 再武装が表現できず、毎足の再発火になる。
推奨理由: 検証戦略 A で唯一状態が要るのが Trigger の再武装であり、ここだけ開ければ足りる。

**Q4 Trigger の再武装規則の宣言形式**
決めること: 「条件が true であり続ける場合に再発火するか」をどう宣言し、段階2でどの値を許すか。
影響: 同じ条件が連続する足で取引機会がいくつ生成されるかが決まる。
1.（推奨）契約に `retrigger_mode`（`EDGE` / `LEVEL`）を置き段階2は両方許す — 宣言を読めば挙動が決まる。
2. 契約に置くが段階2は `EDGE` のみ許す — 実装は最小だが、`LEVEL` の検証が段階3へ延びる。
3. 宣言せず D05 のランタイム規則に委ねる — 宣言から挙動が読めない。
推奨理由: ADR-0032 が「Trigger 部品の契約と状態」を正本と定めており、宣言に出すのが決定に忠実。

**Q5 即時発注時の `opportunity_validity` の最小表現**
決めること: 後続確認のない戦略で、必須の有効性宣言をどう書くか。
影響: 検証戦略 A の設定ファイルの見た目と、宣言漏れを検出できるかどうかが決まる。
1.（推奨）空の `bindings` を明示的に書かせる — 必須・既定値なしの原則（ADR-0031）を保ったまま段階2が書ける。
2. `execution_filter=None` のとき省略可にする — 書きやすいが暗黙の既定値が生まれる。
3. 即時発注専用の区分 `NoValidityRequired` を設ける — 意図は明確だが区分が1つ増える。
推奨理由: ADR-0031 の「暗黙の既定値を設けない」を最も素直に満たす。

**Q6 `OpportunityConcurrencySpec` の段階2の最小フィールド**
決めること: 同時1建玉・即時発注の段階2で、このクラスに何を持たせるか。
影響: 段階3で後続確認を入れたときに、このクラスの形を変えずに済むかどうかが決まる。
1.（推奨）`max_active`・`on_new_trigger`・`on_order_accepted` の3つ — ADR-0032 の4つの論点を最小の3フィールドで覆う。
2. `on_order_accepted` だけ — 段階2は書けるが段階3で形が変わる。
3. ADR-0032 の4論点に1フィールドずつ（4つ） — 網羅的だが段階2で使わない設定が増える。
推奨理由: 「保持・終了・同時競合・受付後処理」のうち、保持と同時競合は `max_active` に畳める。

**Q7 `Opportunity.reference_values` のスキーマ宣言場所**
決めること: 取引機会に載せる根拠値の名前と型をどこで宣言するか。
影響: 後続確認（段階3）が突破水準を型安全に読めるかどうかが決まる。
1.（推奨）Trigger 契約の `OutputSpec` に `reference_schema` を持たせる — 部品ごとに宣言でき、接続検証に使える。
2. `DataTypeRef` レジストリに `opportunity` の派生型として登録する — 型は固定されるが部品ごとの差を表せない。
3. 宣言せず実行時に検証する — 段階3の接続検証ができない。
推奨理由: `OutputSpec` は既に出力の仕様を持つ場所であり、新しい宣言の置き場を増やさない。

**Q8 段階2の `MissingInputPolicy` に未使用の2区分を含めるか**
決めること: `WAIT_FOR_INPUT` と `USE_PREVIOUS` を段階2の型に定義するか、D05 まで作らないか。
影響: 段階3で区分を追加するとき、保存形式の版（Q1）を上げる必要があるかどうかが決まる。
1.（推奨）段階2は2区分だけ定義し、追加時に保存形式の版を上げる — 未確定のフィールドを先に固定しない。
2. 4区分すべてをフィールドなしで定義し能力検査で拒否する — 版は上げずに済むが、空の区分が残る。
3. 4区分すべてをフィールドごと確定させる — D05 の検討を前倒しし、段階2の範囲を超える。
推奨理由: 全体計画 §7.3 が WAIT / USE_PREVIOUS のフィールドを D05 の対象としており、先に固定すると二度手間になる。

**Q9 本書の対象範囲（全体計画書 §8.1 との食い違いの解消）**
決めること: 部品カタログ（§5.3.3）とコンパイラ実装（§5.3.4）を D04 に戻すか、D05 に置いたままにするか。
影響: D04 の承認だけで段階2の実装に入れるか、D05 の承認も待つかが決まる。
1.（推奨）D05 に置いたままにし、全体計画書 §8.1 の D04 の行を §7.3 に合わせて改める — 承認単位が小さく、設計文書は1文書ずつ承認する方針（ADR-0022）に合う。
2. D04 に戻して本書へ追記する — 1文書で完結するが分量が倍になり承認が遅れる。
3. カタログだけ D04 に戻し、コンパイラ実装は D05 に置く — 中間案だが境界の説明が増える。
推奨理由: ADR-0016 が段階2の開始条件を「D04 と D05 の最小ランタイム範囲」と分けており、分割が前提になっている。
