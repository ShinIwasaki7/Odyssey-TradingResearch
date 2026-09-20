# D04: 戦略宣言モデル設計（`odyssey_fx.strategy.declarations`）

作成日: 2026-09-20
状態: **承認（2026-09-20、PR #14）**。v1.0: 第19節の要決定 Q1〜Q9 をユーザーが決定し本文へ反映済み（Q3 は選択肢2「任意の部品で状態を許す」、他の8件は推奨案）。段階2（最小縦断）と紙上トレース T01 に必要な範囲だけを扱う。将来機能は第15節で明示的に対象外とする。Codex 指摘7件（1巡目3件・2巡目4件）も反映済み。1巡目: ダイジェスト対象から実装参照を分離（第13.2節）、状態の初期値宣言を追加（第9.1節）、価格パラメータを float のまま保持し `Price` 変換を D06 の境界に置く（第7節）。2巡目: 建玉・口座を読む入力の許可組合せを表で明示（第6.1節）、約定通知で評価を起動する区分を追加（第8節）、使用箇所の一覧を識別子順に正規化（第3節）、銘柄をグラフ上で伝播させて一致検査を実装可能にした（第5節）。ADR-0016 条件2 のうち D04 を本書で充足する。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.3.2〜§4.3.11・§4.3.15・§4.5・§4.6・§4.7.1、[全体計画書](fx_research_platform_overall_plan.md) §5.3.1〜§5.3.4・§7.3 前半、[D01](D01_architecture_and_dependency_rules.md) §2・§3・§5・§7.2・§8・§10.1、[D02](D02_common_kernel.md)、[D03](D03_marketdata_and_time.md) §3.1〜§3.3・§6・§7、ADR-0011（frozen dataclass）、ADR-0016（実装開始条件）、ADR-0018（設定は YAML）、ADR-0021（NumPy の許可範囲）、ADR-0031（確認待ち中の条件再検査）、ADR-0032（再発火と複数取引機会）、ADR-0033（評価要求の追い越しの改名）
対応段階: 段階2で実装。ADR-0016 条件2 のうち D04 を充足する。

## 0. 本書の位置付けと凡例

`odyssey_fx.strategy.declarations` に置く宣言型の構造・不変条件・検証時点・設定ファイル表現を決める。部品の計算規則、ランタイムの状態機械、注文の受付・執行は決めない。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択した事項。**2026-09-20 に Q1〜Q9 をすべて決定済み**。第19節に決定内容を残す |

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

本書が追加するのは保存形式の版（スキーマ版）だけである（Q1 決定、選択肢1）【合意済み】: 3クラスそれぞれのトップレベルに `schema_version: int`（`>= 1`）を置く。設定ファイル先頭の `schema_version`（ADR-0018）と一致しなければ `app.config` が拒否する。トップレベルに置くことで版が内容ハッシュ（第13.2節）に入り、保存形式の解釈規則を変えた事実が再現性の差として現れる。**不採用**: 設定ファイルだけに持つ案、コンパイル結果にだけ記録する案（いずれもハッシュから保存形式が読めない）。

不変条件【提案】: `component_id` は `^[a-z0-9_]+$`、`version >= 1`、`instance_id` は同一戦略内で一意で `^[a-z0-9_]+$`、`components` は 1 件以上、`inputs` / `outputs` / `parameters` のキーは `^[a-z0-9_]+$`。違反は `KernelValueError`（D02 §10）。

`components` の正規化【提案】: 並び順で実行順序を指定しないため【合意済み】§4.3.5、`StrategyDefinition` は構築時に `components` を `instance_id` の Unicode コードポイント順へ並べ替えて保持する。D02 §3.3 の `PhaseSet` と同じ扱いであり、設定ファイル内の記述順が内容ハッシュ（第13.2節）に影響しないようにする。

## 4. 入出力と接続

### 4.1 仕様型【提案】（上位設計書 §4.3.8 の案を確定させる）

| 型 | フィールド |
|---|---|
| `InputSpec` | `data_type: DataTypeRef`、`kind: PortKind`、`arity: InputArity`、`read_spec: InputReadSpec` |
| `OutputSpec` | `data_type: DataTypeRef`、`kind: PortKind`、`reference_schema: Mapping[str, DataTypeRef]`（第11.1節。取引機会を出す出力以外は空） |
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

### 6.3 `MissingInputPolicy`【提案】＋【合意済み】（Q8 決定、選択肢1）

動作区分は `SKIP_EVALUATION` / `ERROR` / `WAIT_FOR_INPUT` / `USE_PREVIOUS` の4つ【合意済み】。段階2で意味が確定しているのは前2者だけであり、後2者のフィールド（待機期限・期限切れ処理・対象区間・再開時刻・`on_superseded`／遡り上限・記録・許可する欠損理由）は D05 で確定する。

**段階2の型は `SkipEvaluation` と `Error` の2区分だけを定義する**（Q8 決定）。`WAIT_FOR_INPUT` / `USE_PREVIOUS` は D05 でフィールドごと確定してから区分を追加し、その追加を**保存形式の版（第3節の `schema_version`）の引き上げ**として扱う。未確定のフィールドを先に固定しない。**不採用**: 4区分をフィールドなしで先に置く案（空の区分が残る）、4区分を今すぐ確定する案（D05 の検討を前倒しし段階2の範囲を超える）。

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

### 9.1 `StateSpec`【提案】＋【合意済み】（Q3 決定、選択肢2）

`StateSpec(state_type: DataTypeRef, initial: StateInitializer, reset_on: tuple[ResetTrigger, ...])`。状態を持たない部品は `state_spec=None`【合意済み】。契約と実装の状態型を二重定義せず、`catalog` の登録時に実装の状態型と `state_type` の一致を検査する【合意済み】§4.3.7。保存・復元の詳細は D05。

**役割を問わず、どの部品も内部状態を持てる**（Q3 決定、選択肢2）。型と契約は役割で制限せず、コンパイラも `state_spec` が `None` でないことだけを理由に拒否しない。状態はランタイムが使用箇所ごとに保持し、部品実装オブジェクトは可変状態を持たない【合意済み】ADR-0008・§5.3.3。段階2で**実際に**状態を使うのは取引機会を検出する部品（Trigger）の再武装（第10.4節）だけだが、これは検証戦略 A の必要範囲がそこに限られるという事実であり、型の側の制限ではない。EMA のような再帰計算の部品も同じ `StateSpec` で宣言でき、その計算規則の確定だけが D05 に残る。**不採用**: Trigger の再武装だけに状態を許す案（型を役割で制限すると D05 で契約を作り直す必要がある）、状態を一切許さない案（再武装が表現できない）。

- `StateInitializer`【提案】: `kind` タグ付きで、初版は `LiteralInitialState(values: Mapping[str, ParameterValue])` の1区分のみ。初期値を宣言に書き切り、実装側の既定値に委ねない。高値突破 Trigger の再武装（第10.4節）であれば「起動時は発火可能（armed）か否か」をここで宣言する。
- `ResetTrigger` は初版 `RUN_START` のみ。リセットは `initial` と同じ値へ戻すことと定義し、「リセット時だけ別の値」を持たせない。
- これにより、同じ宣言からは同じ初期状態になり、段階2の完了条件「同一入力の再実行で trace が一致」（全体計画 §8.2）が実装に依存しなくなる。
- ウォームアップ中に有効な出力を出せない状態（EMA の初期化途中など）は `TemporalConstraints.warmup`（第9.2節）で宣言し、`StateSpec` 側には持たせない。

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

### 10.2 `OpportunityValiditySpec` と `ValidityBinding`【提案】＋【合意済み】（Q5 決定、選択肢1）

`OpportunityValiditySpec(bindings: tuple[ValidityBinding, ...])`、`ValidityBinding(source: OutputRef, mode: SNAPSHOT_AT_OPPORTUNITY | REQUIRE_UNTIL_ORDER_REQUEST, on_missing: MissingInputPolicy)`。必須指定・暗黙の既定値なし【合意済み】ADR-0031。

**後続確認のない戦略でも、空の `bindings` を設定ファイルに明示的に書かせる**（Q5 決定）。`execution_filter` が `None` でも省略を許さず、フィールド自体の省略は `app.config` が拒否する。**不採用**: 省略可にする案（暗黙の既定値が生まれ ADR-0031 に反する）、即時発注専用の区分を設ける案（区分が1つ増える）。

入力ポートの時間的束縛【提案】: 「対象区間束縛」は `SNAPSHOT_AT_OPPORTUNITY`（取引機会生成時の `OutputRecord` を固定）、「現在状態束縛」は `REQUIRE_UNTIL_ORDER_REQUEST`（再検査時点の最新出力を読む）で表し、`InputReadSpec` に新しいフィールドを足さない。再検査の起動点（確認評価時・`OrderRequest` 生成直前）の実装は D05【合意済み】全体計画 §7.3。

### 10.3 `OpportunityConcurrencySpec`【提案】＋【合意済み】（Q6 決定、選択肢1）

必須指定・暗黙の既定値なし、置換の禁止、終端理由の語彙（`EXPIRED` / `MARKET_STATE_INVALIDATED` / `SUPERSEDED` / `CLOSED_BY_ORDER_ACCEPTANCE`）は確定済み【合意済み】ADR-0032・上位設計書 §4.5。非終端の状態名と遷移は D05。

段階2のフィールドは次の3つとする（Q6 決定）。

| フィールド | 型・値 | 意味 |
|---|---|---|
| `max_active` | `int`（`>= 1`） | 同時に保持できる有効な取引機会の上限。上限に達している状態で新しい発火があったときの扱いは `on_new_trigger` が決める |
| `on_new_trigger` | `KEEP_EXISTING` / `SUPERSEDE_EXISTING` | 既存を残して新規を捨てるか、既存を `SUPERSEDED` で終端して新規を生成するか。いずれの場合も既存の内容を上書きしない【合意済み】ADR-0032 |
| `on_order_accepted` | `KEEP_OTHERS` / `CLOSE_OTHERS` | ある注文が受け付けられたとき、他の取引機会を `CLOSED_BY_ORDER_ACCEPTANCE` で終端するか残すか。規則を書かない限り暗黙に終端させない【合意済み】ADR-0032 |

ADR-0032 が挙げる4論点のうち「保持」と「同時競合」は `max_active` に畳んでいる。**不採用**: `on_order_accepted` だけを持つ案（段階3で形が変わる）、4論点に1フィールドずつ置く案（段階2で使わない設定が増える）。

### 10.4 Trigger の再武装【提案】＋【合意済み】（Q4 決定、選択肢1）

条件が true であり続ける場合に再発火とするかどうかは Trigger 部品の契約と状態が持つ【合意済み】ADR-0032。

`ComponentContract.parameters` ではなく契約の固定属性として `retrigger_mode` を置き、次の2値を取る（Q4 決定）。**段階2では両方を許可する**。

| 値 | 意味 |
|---|---|
| `EDGE` | 条件が不成立から成立へ変わった評価でだけ発火する。成立が続く間は再発火しない。再武装は条件が不成立へ戻った時点 |
| `LEVEL` | 条件が成立している評価ごとに発火する。発火のたびに新しい `opportunity_id` を持つ取引機会を生成する【合意済み】ADR-0032 |

`EDGE` の判定に必要な「直前の評価で条件が成立していたか」は `StateSpec`（第9.1節）で宣言し、初期値も宣言に書く。**不採用**: 段階2は `EDGE` のみとする案（`LEVEL` の検証が段階3へ延びる）、宣言せずランタイム規則に委ねる案（宣言から挙動が読めない）。

## 11. 役割出力に関する宣言

### 11.1 `Opportunity.reference_values` のスキーマ【提案】＋【合意済み】（Q7 決定、選択肢1）

突破水準などの根拠値を契約で名前と型を宣言して保持する【合意済み】§4.3.15。

**宣言の置き場所は Trigger 契約の `OutputSpec` の `reference_schema`（`Mapping[str, DataTypeRef]`）とする**（Q7 決定、第4.1節）。`data_type` が `opportunity` でない出力は空の mapping でなければならない。実行時の `Opportunity.reference_values` は、キー集合と各値の型がこの宣言と一致することをランタイムが検査する。後続確認の部品（段階3）は、この宣言を通じて突破水準を型付きで読める。**不採用**: データ型レジストリ側に派生型として登録する案（部品ごとの差を表せない）、実行時検証だけに任せる案（接続検証ができない）。

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

段階2で拒否する構成【提案】: `RuntimeInputRef(PENDING_ORDER)`、`AwaitConfirmation`、`execution_filter` が `None` でない戦略、`MissingInputPolicy` の `WAIT_FOR_INPUT` / `USE_PREVIOUS`、`POSITION_OPENED` 以外の `RuntimeEventKind`、複数銘柄に跨る使用箇所（第5節）、15m より細かい足、距離型 SL、指値、`UPDATE_STOP`。`state_spec` が `None` でないことは拒否の理由にしない（第9.1節、Q3 決定）。拒否は `ReasonCode`（D02 §8.1）付きの構造エラーとし、黙って無視しない。

## 13. 設定ファイル表現と内容ハッシュ

### 13.1 YAML 表現【提案】

`configs/strategies/<strategy_id>_v<version>.yaml`（D01 §10.1）。ADR-0018 の読込条件（安全な読込、カスタムタグ禁止、重複キーはエラー、merge key 禁止、`schema_version` 必須、未宣言キー拒否）をそのまま適用する【合意済み】。区分タグ付き union は `kind:` キーで表す【合意済み】D01 §8。部品契約（`ComponentContract`）は YAML に書かず、`catalog` のコード側の登録を正本とする【提案】。戦略ファイルが書くのは `ComponentInstance` と役割参照・方針だけである。

`app.config` が Pydantic v2 で検証してから frozen dataclass へ変換し、Pydantic モデルを `app.config` の外へ出さない【合意済み】ADR-0018・D01 §5。`declarations` 側の `__post_init__` は構造的な不変条件（第3節）だけを見る。参照解決・型整合は `compiler` が行い、3箇所で同じ規則を重複実装しない【提案】。

### 13.2 内容ハッシュ【提案】＋【合意済み】（Q2 決定、選択肢1）

D02 §9.3 の `canonical.digest` をそのまま使う。ダイジェスト対象の構造（何を含めるか）を決めるのは各設計文書の責務であり（D02 §9.4 末尾）、本書は `strategy` の3つの参照について次を定める。

| 参照 | ダイジェスト対象【提案】 |
|---|---|
| `ContractRef.digest` | `ComponentContract` のうち **`implementation_ref` を除いた**全フィールド。契約の「受け口・出し口・評価条件・状態・時刻制約」の同一性を表す |
| `StrategyRef.digest` | `StrategyDefinition` 全体。各 `ComponentInstance` は `contract_ref` を通じて上記の契約ダイジェストを含むため、**実装コードの同一性は含まない** |
| `CompiledStrategyRef.digest` | 解決済み設定（具体値へ解決したパラメータ、評価順）に加え、使用する各部品の `ImplementationRef`（ID ＋内容ハッシュ）を含む |

`strategy_id` / `version` が同じでもパラメータ割当が違えば別の実行として扱う【合意済み】§4.3.5。上表の切り分けを採用する（Q2 決定、選択肢1）。**不採用**: 契約ダイジェストに `implementation_ref` を含める案（実装を直すたびに人間が管理する戦略の版まで変わる）、どちらにも含めず実行全体のコードダイジェストに任せる案（部品単位・戦略単位で実装差を検出できない）。各3クラスの `schema_version`（第3節）はダイジェスト対象に含まれる。

## 14. 段階2の最小範囲（検証戦略 A）と T01

上位設計書 §7.1 の検証戦略 A（1h 終値で直近の確定高値突破 → 後続確認なしの成行注文 → 初期 SL と固定 RR の TP）を宣言できることが、段階2の必要十分条件である。

| 必要な宣言 | 使う型 |
|---|---|
| 直近 N 本高値（当該足を除く） | `MarketDataRef(USDJPY/1h/bid, HIGH)` ＋ `HistoryWindow(BarsWindow(N), exclude_latest_bars=1)` |
| 高値突破 Trigger | `OnBarClose(1h)` 起動、出力 `opportunity`、`OutputSpec.reference_schema` に突破水準、`retrigger_mode=EDGE`（直前の成立を `StateSpec` で保持） |
| 成行注文意図 | `OnInputEvent(取引機会)` 起動、出力 `order_intent` |
| 初期 SL | 確定情報から絶対価格、出力 `protection_levels` |
| 固定 RR の TP | `OnRuntimeEvent(POSITION_OPENED)` 起動、`RuntimeInputRef(POSITION)` ＋ `CurrentContext` 入力、出力 `management_action`（`SET_TAKE_PROFIT`） |
| 戦略全体 | `schema_version=1`、`market_state=None`、`execution_filter=None`、`entry_policy=ImmediateEntry`、`opportunity_validity=bindings: []`（空を明示）、`opportunity_concurrency=(max_active=1, on_new_trigger=KEEP_EXISTING, on_order_accepted=KEEP_OTHERS)` |

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

**内部状態を持つ部品は対象外にしない**（Q3 決定、選択肢2）。宣言の側では役割を問わず状態を許し（第9.1節）、段階2で実際に状態を使うのは取引機会を検出する部品の再武装だけになる。どの部品を初版カタログに入れるかは D05 が決める。

## 16. 上位文書のバックログ項目との対応（漏れ確認）

全体計画書 §5.3.1 と §7.3 前半の全項目を本書のどこで扱ったかを示す。

| 上位の項目 | 本書 | 印 |
|---|---|---|
| 区分値の enum と型付き union の区別 | §1・§4.3・§6.1 | 【合意済み】 |
| `DataTypeRef` の登録・版固定・接続検証 | §5 | 【提案】 |
| スキーマ版と部品・戦略の版の区別／**スキーマ版の保存場所** | §3・§19 Q1 | 【合意済み】Q1 決定 |
| 構築・読込・コンパイル時の検証（検証ライブラリ B-3） | §13.1 | 【合意済み】ADR-0018 |
| `InputSpec` / `OutputSpec` / `InputBinding`、`InputArity`、複数入力 | §4.1 | 【提案】 |
| `ParameterSpec` / `ParameterValue` | §7 | 【提案】 |
| `EvaluationSpec` / `EvaluationSchedule`、複数起動条件、起動条件ごとの必須入力 | §8 | 【提案】 |
| `MissingInputPolicy` の動作タグごとの型 | §6.3・§19 Q8 | 【合意済み】Q8 決定 |
| `StateSpec` | §9.1・§19 Q3 | 【合意済み】Q3 決定（選択肢2） |
| `TemporalConstraints` | §9.2 | 【提案】 |
| `EntryPolicy` のモード別型 | §10.1 | 【提案】 |
| Trigger の遷移検出・再武装規則 | §10.4・§19 Q4 | 【合意済み】Q4 決定 |
| `OpportunityValiditySpec` / `ValidityBinding` | §10.2・§19 Q5 | 【合意済み】Q5 決定 |
| `OpportunityConcurrencySpec` | §10.3・§19 Q6 | 【合意済み】Q6 決定 |
| `RuntimeInputRef` の対象区分 | §4.3 | 【提案】 |
| 入力ポートの時間的束縛（対象区間束縛／現在状態束縛） | §10.2 | 【提案】 |
| 設定ファイル表現・内容ハッシュの算出規則 | §13・§19 Q2 | 【合意済み】Q2 決定 |
| `Opportunity.reference_values` のスキーマ宣言 | §11.1・§19 Q7 | 【合意済み】Q7 決定 |
| `ManagementAction` の要求種別 | §11.2 | 【提案】 |
| Exit 複数ルールの合成 | §11.3 | 【提案】 |

## 17. 上位文書との差異

1. **本書の対象範囲（解消済み）**。§8.1 は D04 を「第5.3.1〜5.3.4節」とし、部品カタログ（§5.3.3）とコンパイラ（§5.3.4）を含めていた一方、§7.3 のバックログではカタログが D05 側に列挙されており、両者が食い違っていた。Q9 の決定（選択肢1）により、**カタログの部品一覧・計算規則とコンパイラの実装（依存グラフ構築、ハッシュ計算の内部）は D05 が扱う**ことに揃え、全体計画書 §8.1 の D04・D05 の行を本 PR で改めた。本書が扱うのは宣言モデル（§5.3.1）と、宣言から導かれるコンパイル時検査の一覧（第12節）までである。
2. **D01 §7.2 のモジュール一覧に `opportunity.py` がない**。ADR-0031・ADR-0032 は D01 承認後の決定であり、D01 の一覧が追いついていない。第2節で追加を提案し、D01 の次回改訂で一覧へ追記する（D02 が `canonical.py` / `errors.py` を追加した前例と同じ扱い）。
3. **D03 §6.2 の `end_offset_bars` を宣言側では `exclude_latest_bars` と呼ぶ**（第6.2節）。D03 は「宣言側のフィールド名は D04 で決める」としており、差異ではなく委任の履行である。ビュー側の引数名を合わせるかは D05 で決める。

上記以外に、上位設計書・全体計画書・ADR と食い違う提案はない。

## 18. D05〜D07 への引き渡し事項

| 引き渡し先 | 項目 |
|---|---|
| D05 | `WAIT_FOR_INPUT` / `USE_PREVIOUS` のフィールド、取引機会の非終端の状態名と遷移、再検査の起動点、同時刻の複数起動条件の配送・評価回数、合成部品の契約、初版カタログの指標一覧と計算規則、NumPy を実際に使う部品の特定（ADR-0021）、**状態を持つ部品の計算規則と状態の保存・復元**（「状態を持てる部品の範囲」は Q3 で決着済みで引き渡さない）、依存グラフ構築とハッシュ計算の実装、部品カタログの構成、`MarketDataView` の引数名の整合 |
| D06 | `OrderIntent` / `ProtectionLevels` / `ManagementAction` を受け取ってからの注文状態・執行意味論、`RuntimeInputRef(POSITION/ACCOUNT)` として供給する情報の具体、`ConfigDigest` に戦略の digest をどう含めるか、単位付き float パラメータから `Price` への変換と価格刻みの丸め方向（第7節）、`POSITION_OPENED` イベントの発生フェーズと供給方法（第8節） |
| D07 | `CompiledStrategyRef` を実験 manifest に固定する方法、評価側から見た戦略の同一性 |
| D01（次回改訂） | §7.2 のモジュール一覧へ `opportunity.py` を追記 |

## 19. 承認時の確認事項（2026-09-20 承認: Q3 は選択肢2、他の8件は推奨案を採用）

起草時に選択式で提示した9項目。ユーザーが 2026-09-20 にすべて決定し、本文へ反映済み。「選択肢 n」は起草時に並べた番号で、1 が起草時の推奨案である。

| # | 決めたこと | 決定 | 反映先 |
|---|---|---|---|
| Q1 | 保存形式の版（スキーマ版）の保存場所 | **選択肢1（推奨）**: 3クラスのトップレベルに `schema_version: int` を置き、設定ファイルの版と一致検査する | §3 |
| Q2 | 実装コードの同一性をどのダイジェストに入れるか | **選択肢1（推奨）**: 契約ダイジェストから `implementation_ref` を除き、解決済み設定のダイジェストにだけ含める | §13.2 |
| Q3 | 段階2で状態を持つ部品をどこまで許すか | **選択肢2**: 役割を問わず任意の部品が内部状態を持てる。型・契約を役割で制限しない（段階2で実際に使うのは Trigger の再武装だけ） | §9.1、§12、§15、§17、§18 |
| Q4 | Trigger の再武装規則の宣言形式 | **選択肢1（推奨）**: 契約に `retrigger_mode`（`EDGE` / `LEVEL`）を置き、段階2は両方許可する | §10.4 |
| Q5 | 即時発注時の有効性宣言の最小表現 | **選択肢1（推奨）**: 空の `bindings` を設定ファイルに明示させる（省略を許さない） | §10.2 |
| Q6 | 複数取引機会の関係を表す宣言の最小フィールド | **選択肢1（推奨）**: `max_active` / `on_new_trigger` / `on_order_accepted` の3つ | §10.3 |
| Q7 | `Opportunity.reference_values` のスキーマ宣言場所 | **選択肢1（推奨）**: Trigger 契約の `OutputSpec` に `reference_schema` を持たせる | §4.1、§11.1 |
| Q8 | 使わない欠損方針2区分を段階2の型に含めるか | **選択肢1（推奨）**: 含めず、D05 で追加するときに保存形式の版を上げる | §6.3 |
| Q9 | 部品カタログとコンパイラ実装の担当文書 | **選択肢1（推奨）**: D05 のままとし、全体計画書 §8.1 の記載を §7.3 に合わせて改める | §17、全体計画書 §8.1 |

各項目で採らなかった案は、本文の該当節に「不採用」として1行ずつ残してある。

### 19.1 Q3（任意の部品で状態を許す）が他の決定に与える影響

役割で状態を制限しない決定のため、次の点が推奨案の場合と変わる。

- コンパイラの能力検査は、`state_spec` が `None` でないことだけを理由に拒否しない（§12）。
- 段階2の実装でも、EMA のような再帰計算の部品を後から契約を変えずに追加できる。段階2で実際に使うかどうかは D05 の初版カタログの範囲で決める。
- D05 へ引き渡すのは「状態を持つ部品の計算規則と保存・復元」であり、「状態を持てる部品の範囲」ではない（§18）。
