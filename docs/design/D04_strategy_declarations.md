# D04: 戦略宣言モデル設計（`odyssey_fx.strategy.declarations`）

作成日: 2026-09-20
状態: **承認（2026-09-20、PR #14）**。v1.9（2026-09-22、PR #22）: 戦略ランタイム設計（D05 v2.0）の要決定 Q10〜Q18 に対する人間の決定を受け、**D05 §12.1 が挙げた本書への改訂依頼9件のうち8件を反映した**。(1) 第6.3節の欠損方針に「入力を待つ」（`WaitForInput`）と「過去値へ遡る」（`UsePrevious`）の2区分と、期限切れの動作（`WaitDeadlineAction`）・追い越し時の動作（`OnSuperseded`）の2列挙を足し、**保存形式の版を 2 へ上げた**（Q15・Q16 決定）。(2) 第4.3節の現在コンテキストの対象に取引機会（`OPPORTUNITY`）を足した（Q14 決定）。(3) 第11.2節の保有管理の要求種別に損切り水準の更新（`UPDATE_STOP`）を足した（Q12 決定）。(5)(6) 第12節の段階2 拒否一覧を「段階3 で解除する / 拒否を続ける」に分け、空でない時刻制約をウォームアップ本数（拒否を続ける）と観測区間の一致（解除）に分けた。(7) 第12節の検査一覧に段階3 の検査6件（#8〜#13）を足した。(8) 第12節の因果辺の表に市場状態から取引機会を出す使用箇所への辺を足した（Q10 決定）。(9) 第5節の建玉の情報の例示に有効な損切り水準を足した。**依頼4（確認期限を数える系列を Trigger の系列から確認足の系列へ改める）は見送った**。系列の変更は設計の選択であり、D05 §15 の要決定 Q19 として人間の決定を待つため、第10.1節の記述は変えていない。v1.0: 第19節の要決定 Q1〜Q9 をユーザーが決定し本文へ反映済み（Q3 は選択肢2「任意の部品で状態を許す」、他の8件は推奨案）。段階2（最小縦断）と紙上トレース T01 に必要な範囲だけを扱う。将来機能は第15節で明示的に対象外とする。Codex 指摘7件（1巡目3件・2巡目4件）も反映済み。1巡目: ダイジェスト対象から実装参照を分離（第13.2節）、状態の初期値宣言を追加（第9.1節）、価格パラメータを float のまま保持し `Price` 変換を D06 の境界に置く（第7節）。2巡目: 建玉・口座を読む入力の許可組合せを表で明示（第6.1節）、約定通知で評価を起動する区分を追加（第8節）、使用箇所の一覧を識別子順に正規化（第3節）、銘柄をグラフ上で伝播させて一致検査を実装可能にした（第5節）。v1.1（同日）: Codex 3巡目の指摘4件を反映（取引機会を必ず記録する規則: 第10.3節、再武装モードの置き場所を出力仕様に確定: 第4.1節・第10.4節、許可する起動条件の型と検証意味論を定義: 第8節、データ型レジストリから実行時クラスの対応を外す: 第5節）。v1.7（同日）: レビュー7巡目（対象内 P0=0・P1=0）の改善提案2件のうち、期間値の YAML 表現を1形式に確定した（第13.1節）。欠損方針2区分の宣言形を本書で確定せよという提案は、Q8 の決定（人間）と衝突するため不採用とし、境界表の行1に明示の例外として記した。v1.6（同日）: **本書が決めることと後続文書に委ねることの境界表を第1.2節に新設**し、レビューの対象範囲をその左列と定めた（人間の決定）。第18節は境界表の参照にまとめ、既存文書の改訂依頼だけを残した。v1.5（同日）: レビュー6巡目の指摘5件を反映（エンジン上の因果辺を循環検出に含める: 第12節、接続元と接続先の `PortKind` 一致: 第5節、有効性束縛の型と一意性: 第10.2節、`EDGE` の状態の中身まで検査: 第10.4節）。v1.4（同日）: レビュー5巡目の指摘4件を反映（市場データの項目とデータ型の対応表と `volume` の登録: 第5節、理由コードの表 D02 §8.1 への取引機会の終端理由の反映: D02 v1.3、起動条件名の一意性: 第8節、必須入力名の列の正規化: 第3節）。v1.3（同日）: レビュー4巡目の指摘5件を反映（建玉・口座の入力型 `position_context` / `account_context` を登録: 第5節、銘柄の供給元に起動条件とウォームアップの系列を追加: 第5節、`EDGE` は状態の宣言を要求: 第10.4節・第12節、追い出す取引機会の鍵を取引機会側の項目で定義: 第10.3節、順序に意味のないコレクションの正規化を一般規則化: 第3節）。v1.2（同日）: 残っていた1点、同時保持上限に達して有効化せず終端した取引機会の終端理由の名前（第19節 Q10）を人間が決定し（選択肢1、`CONCURRENCY_LIMIT_REACHED`）、第10.3節へ反映するとともに ADR-0032・上位設計書 §4.5/§4.7.14・全体計画書 §5.3.5 の語彙へ同じ語を加えた。**要決定は残っていない**。あわせて、レビューで同じ種類の指摘が繰り返された原因への手当てを入れた: 本書が定義する宣言型を1つの表へ集約し（第3.1節）、語彙と責務の正本がどの文書かを1つの表で示し（第1.1節）、コンパイル時検査の表に「その検査が読む宣言」の列を足した（第12節）。ADR-0016 条件2 のうち D04 を本書で充足する。 v1.8（2026-09-21、PR #18）: 段階2 の戦略基盤の実装で判明した2点を人間の決定により確定した。(a) 起動条件ごとの必須入力の検査（第8節・第12節 #4）の文言を「キー集合が一致する」から**「`required_inputs` のキーはすべて、使用箇所が宣言済みの起動条件を指していなければならない（余計なキーを許さない）」**へ改訂した。(b) 取引機会の有効性の再検査で欠損を失敗扱いにする組合せ（`REQUIRE_UNTIL_ORDER_REQUEST` × `Error`）を段階2 では能力検査で拒否することと、その解除時期を第6.3節・第12節に明記した。あわせて第13.2節に、期間値を含む宣言は現状ダイジェストを計算できないこと（D02 §9.3 v1.6）を注記した。レビュー指摘により、段階2 が守らせる仕組みを持たない**空でない `TemporalConstraints`** も拒否一覧へ加えた（第12節）。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.3.2〜§4.3.11・§4.3.15・§4.5・§4.6・§4.7.1、[全体計画書](fx_research_platform_overall_plan.md) §5.3.1〜§5.3.4・§7.3 前半、[D01](D01_architecture_and_dependency_rules.md) §2・§3・§5・§7.2・§8・§10.1、[D02](D02_common_kernel.md)、[D03](D03_marketdata_and_time.md) §3.1〜§3.3・§6・§7、ADR-0011（frozen dataclass）、ADR-0016（実装開始条件）、ADR-0018（設定は YAML）、ADR-0021（NumPy の許可範囲）、ADR-0031（確認待ち中の条件再検査）、ADR-0032（再発火と複数取引機会）、ADR-0033（評価要求の追い越しの改名）
対応段階: 段階2で実装。ADR-0016 条件2 のうち D04 を充足する。

## 0. 本書の位置付けと凡例

`odyssey_fx.strategy.declarations` に置く宣言型の構造・不変条件・検証時点・設定ファイル表現を決める。部品の計算規則、ランタイムの状態機械、注文の受付・執行は決めない。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択した事項。**2026-09-20 に Q1〜Q10 をすべて決定済み**。第19節に決定内容を残す |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | `ComponentContract` / `ComponentInstance` / `StrategyDefinition` と補助型。`backtest`・`evaluation`・`compiler`・`runtime` が読む | 【合意済み】D01 §1 |
| 依存できるもの | Python 標準ライブラリ、`odyssey_fx.common`、`marketdata.domain` のみ。外部ライブラリと I/O を持たない | 【合意済み】D01 §3.2・§5 |
| 決めないもの | 部品の計算規則（D05）、取引機会の状態機械（D05）、注文・約定・リスク（D06）、指標定義（D07） | 【合意済み】 |
| 層 | `strategy` の最下層。`records`・`catalog`・`compiler`・`runtime` から参照されるが、これらを参照しない | 【合意済み】D01 §3.3 |

宣言型はすべて `@dataclass(frozen=True, slots=True)`、コレクションは `tuple` または凍結 `Mapping`、区分タグ付き union は `kind` フィールドを持つ dataclass の `Union` とする【合意済み】D01 §8・ADR-0011。

### 1.1 語彙と規則の正本がどの文書にあるか【提案】

同じ語彙や規則が複数の文書に書かれていると、片方だけ改訂されて食い違う。本書が使う語彙・規則について、**正本を1か所に定め、本書は参照するだけ**にする。

| 事項 | 正本 | 本書の扱い |
|---|---|---|
| 3クラスのトップレベルのフィールド構成・型・必須性 | 上位設計書 §4.3.5 | 再掲のみ。本書が足すのは `schema_version` だけ（第3節） |
| 取引機会の終端理由の語彙 | 上位設計書 §4.5（ADR-0031・ADR-0032） | 参照のみ。本書で新しい語を作らない |
| 理由コードの語彙 | 上位設計書 §4.7.14（実装側の列挙は D02 §8.1 がその写し） | 参照のみ。語彙を足すときは §4.7.14 と D02 §8.1 の両方を同じ PR で更新する |
| 欠損の診断理由の語彙（`MissingInputReason`） | D02 §8.3 | 参照のみ（第6.3節） |
| 内容ハッシュの計算方法 | D02 §9.3 | 参照のみ。**何を対象に含めるか**だけ本書が決める（第13.2節） |
| 系列・時間足の語彙（`SeriesId` / `TimeframeRef`） | D03 §3.1・D02 §6 | 参照のみ |
| 同時到達した注文の受付順（全順序） | 上位設計書 §4.7.12 | 参照のみ（第10.3節） |

新しい語彙が必要になったときは、本書で独自に定義せず、**正本（ADR または上位設計書）の改訂として提案し、本書はその改訂を参照する**。第19節 Q10 の `CONCURRENCY_LIMIT_REACHED` はこの手順で ADR-0032 を改訂して追加した。

新しい語彙が必要になったときの手順は上のとおりである。**どこまでを本書が決めるか**の境界は、次の第1.2節の表を正本とする。

### 1.2 本書が決めることと、後続文書に委ねること【提案】（**レビュー対象範囲の正本**）

本書は「宣言の形」を決める文書であり、「宣言のとおりに動かす手順」は決めない。この線を文章ではなく1つの表で引く。**本書のレビューの対象範囲は左列**であり、右列に属する指摘は「対象外（担当文書へ）」として記録し、本書では直さない。

ただし1つ例外がある。**本書の文が右列の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。右列の内容を本書に書き足すことはしない。

| # | 領域 | 本書が決めること（対象内） | 後続文書が決めること（対象外・担当） |
|---|---|---|---|
| 1 | 宣言型の構造 | 3クラスと補助型のフィールド・不変条件・区分、正規化、設定ファイル表現（第3節・第3.1節・第4〜11節・第13.1節）。**段階2で使う区分はすべて本書で完結する** | 段階2で使わない欠損方針2区分（`WAIT_FOR_INPUT` / `USE_PREVIOUS`）の宣言形。人間が Q8 で決めた**明示の例外**で、意味論と同時に **D05** が確定し、追加は保存形式の版の引き上げとして扱う（第6.3節・第19節 Q8） |
| 2 | 部品カタログ | 契約が取りうる形と、宣言に現れる型識別子（第5節） | 初版カタログの品揃え、各部品の計算規則、合成部品の契約、NumPy を使う部品の特定（**D05**） |
| 3 | データ型の中身 | 型識別子と版の登録、接続検証に使う対応表（第5節） | 各 payload の項目そのもの（`condition_state` / `position_context` / `account_context` ほか）と、`records` の実行時クラスとの対応・一致検査（**D05**、供給範囲は **D06**） |
| 4 | 取引機会のライフサイクル | 宣言のフィールドと、ADR-0031・ADR-0032 が確定した終端規則（第10.2節・第10.3節） | 非終端の状態名と遷移、各規則を適用するフェーズ、`opportunity_id` の採番と生成時点を表す項目名（**D05**） |
| 5 | 評価の起動 | 起動条件の型・検証意味論・起動条件ごとの必須入力の宣言（第8節） | 同時刻に複数成立したときの配送と評価回数、評価要求の生成と追い越し、`POSITION_OPENED` の発生フェーズと供給方法（**D05**・**D06**） |
| 6 | 部品の状態 | 状態型・初期値・リセット契機の宣言（第9.1節） | 状態の更新規則と、保存・復元の方法（**D05**） |
| 7 | 欠損入力 | 段階2の2区分と診断理由の参照（第6.3節） | `WAIT_FOR_INPUT` / `USE_PREVIOUS` のフィールドと、待機・遡り・記録の意味論（**D05**） |
| 8 | コンパイル時検査 | 検査項目の一覧と、各検査が読む宣言（第12節） | 依存グラフ構築・ハッシュ計算・エラー整形の実装方法（**D05**） |
| 9 | 数値と価格 | パラメータの型・単位・範囲の宣言（第7節） | `Price` への変換と価格刻みの丸め方向（**D06**） |
| 10 | 注文・執行 | 役割出力の型要求と `ManagementAction` の要求種別（第11節） | 注文状態・執行・約定の意味論、`RuntimeInputRef` で供給する情報の具体（**D06**） |
| 11 | 実験の同一性 | 各参照のダイジェスト対象（第13.2節） | 実験 manifest への固定方法と、評価側から見た戦略の同一性（**D07**） |

この線の引き方は「宣言を読めば形が決まり、宣言だけでは動きが決まらない」という原則による。右列を本書へ取り込むと D05 の設計を前倒しすることになり、左列を右列へ逃がすと**宣言から実装できない**（レビューで繰り返し指摘された状態）に戻る。どちらにも寄せない。

D01・D02 など**既存文書の改訂依頼**として残るものは第18節に挙げる。

## 2. モジュール構成【提案】

D01 §7.2 の 12 モジュールに `opportunity.py` を加える（サブパッケージ内へのモジュール追加。D01 の改訂は次回改訂時に §7.2 の一覧へ追記）。

| モジュール | 内容 |
|---|---|
| `contract.py` / `instance.py` / `definition.py` | 3クラス（第3節） |
| `specs.py` | `InputSpec` / `OutputSpec` / `InputBinding` / `InputArity` / `RetriggerMode` / `ParameterSpec` / `ParameterValue`（第4節・第7節・第10.4節） |
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

本書が追加するのは保存形式の版（スキーマ版）だけである（Q1 決定、選択肢1）【合意済み】: 3クラスそれぞれのトップレベルに `schema_version: int`（`>= 1`）を置く。**現在の版は 2 である**（v1.9、2026-09-22）。段階2 は 1 で、第6.3節に欠損方針の2区分（入力を待つ・過去値へ遡る）を足したときに 2 へ上げた。版を上げるのは、同じ設定ファイルでも解釈規則が変わった事実を内容ハッシュへ載せるためである（第13.2節）。設定ファイル先頭の `schema_version`（ADR-0018）と一致しなければ `app.config` が拒否する。トップレベルに置くことで版が内容ハッシュ（第13.2節）に入り、保存形式の解釈規則を変えた事実が再現性の差として現れる。**不採用**: 設定ファイルだけに持つ案、コンパイル結果にだけ記録する案（いずれもハッシュから保存形式が読めない）。

不変条件【提案】: `component_id` は `^[a-z0-9_]+$`、`version >= 1`、`instance_id` は同一戦略内で一意で `^[a-z0-9_]+$`、`components` は 1 件以上、`inputs` / `outputs` / `parameters` のキーは `^[a-z0-9_]+$`。違反は `KernelValueError`（D02 §10）。

コレクションの正規化【提案】: 意味の同じ2つの設定ファイルが、書いた順序の違いだけで別の内容ハッシュ（第13.2節）になると、実験の同一性やキャッシュが分かれてしまう。そこで**順序に意味を持たせないコレクションは、構築時に安定な鍵で並べ替えて保持する**。D02 §3.3 の `PhaseSet` と同じ扱いである。新しいコレクションを足すときは、この表に「順序に意味があるか」を必ず書く。

| コレクション | 順序の意味 | 構築時の扱い |
|---|---|---|
| `StrategyDefinition.components` | なし【合意済み】§4.3.5 | `instance_id` の Unicode コードポイント順 |
| `EvaluationSchedule.triggers` | なし（同時刻の配送・評価回数は D05） | 起動条件名 `name` 順 |
| `EvaluationSpec.allowed` | なし | （区分タグ, 正規化エンコード）順 |
| `AllowedBarClose.timeframes` / `AllowedInputEvent.input_names` / `AllowedRuntimeEvent.events` | なし（許可の集合） | 各要素の文字列表現順 |
| `OpportunityValiditySpec.bindings` | なし | (`source.instance_id`, `source.output_name`) 順 |
| `TemporalConstraints.alignment` と各 `AlignmentRequirement.input_names` | なし | 入力名を並べ替えたうえで、その結果の順 |
| `EvaluationSpec.required_inputs` の各値（必須入力名の列） | なし（必須入力の集合であり評価順ではない） | 入力名順 |
| `StateSpec.reset_on` | なし | 列挙名順 |
| `ParameterSpec.allowed_values` | なし（許可値の集合） | 正規化エンコード順 |
| `InputBinding.sources` | **あり** | 並べ替えない。可変個数入力は「型付き参照の列」であり、並びを部品実装が参照しうる【合意済み】§4.3.5 |

順序に意味を持たせないコレクションは、重複要素を構築時に拒否する（`KernelValueError`）。

### 3.1 本書が定義する宣言型の一覧【提案】

本書が名前を挙げる宣言型を、区分（値の集合だけの enum / `kind` タグ付き union / フィールドを持つレコード）とフィールドまで一覧する。**この表にない型名を本文で使わない**。型名だけが本文に現れてフィールドが決まっていない状態（実装できない宣言）を構造的に防ぐための規約であり、節を追加するときは必ずこの表も更新する。「上位」は上位設計書、「詳細」は該当節を指す。

| 型 | 区分 | フィールド / 値 | 詳細 |
|---|---|---|---|
| `ComponentContract` | レコード | 上位 §4.3.5 の9フィールド ＋ `schema_version: int` | §3 |
| `ComponentInstance` | レコード | 上位 §4.3.5 の5フィールド ＋ `schema_version: int` | §3 |
| `StrategyDefinition` | レコード | 上位 §4.3.5 の12フィールド ＋ `schema_version: int` | §3 |
| `InputSpec` | レコード | `data_type` / `kind` / `arity` / `read_spec` | §4.1 |
| `OutputSpec` | レコード | `data_type` / `kind` / `reference_schema` / `retrigger_mode` | §4.1 |
| `InputBinding` | レコード | `sources: tuple[InputSourceRef, ...]` | §4.1 |
| `InputArity` | レコード | `min_count: int` / `max_count: int \| None` | §4.1 |
| `PortKind` | enum | `VALUE` / `EVENT` / `COMMAND` | §4.2 |
| `InputSourceRef` | union | `OutputRef` / `MarketDataRef` / `RuntimeInputRef` | §4.3 |
| `OutputRef` | レコード | `instance_id: str` / `output_name: str` | §4.3 |
| `MarketDataRef` | レコード | `series: SeriesId` / `field: MarketDataField` | §4.3 |
| `MarketDataField` | enum | `OPEN` / `HIGH` / `LOW` / `CLOSE` / `VOLUME` | §4.3 |
| `RuntimeInputRef` | レコード | `target: RuntimeTarget` | §4.3 |
| `RuntimeTarget` | enum | `POSITION` / `ACCOUNT` / `OPPORTUNITY`（`OPPORTUNITY` は v1.9 で追加。`PENDING_ORDER` は列挙に含めない） | §4.3 |
| `DataTypeRef` | レコード | `type_id: str` / `version: int` | §5 |
| `InputReadSpec` | union | `LatestAvailable` / `HistoryWindow` / `DeliveredEvent` / `CurrentContext` | §6.1 |
| `BarsWindow` | レコード | `count: int \| ParameterRef` | §6.2 |
| `DurationWindow` | レコード | `duration: timedelta` | §6.2 |
| `ParameterRef` | レコード | `parameter_name: str` | §6.2 |
| `MissingInputPolicy` | union | `SkipEvaluation` / `Error`（追加フィールドなし） / `WaitForInput` / `UsePrevious`（v1.9 で追加。段階3 で使う） | §6.3 |
| `WaitForInput` | レコード（`MissingInputPolicy` の区分。v1.9） | `deadline: BarsDeadline \| DurationDeadline` / `on_deadline: WaitDeadlineAction` / `on_superseded: OnSuperseded` | §6.3 |
| `UsePrevious` | レコード（`MissingInputPolicy` の区分。v1.9） | `max_lookback: BarsWindow \| DurationWindow` / `allowed_reasons: tuple[MissingInputReason, ...]`（1件以上） | §6.3 |
| `WaitDeadlineAction` | enum（v1.9） | `SKIP_EVALUATION` / `ERROR` | §6.3 |
| `OnSuperseded` | enum（v1.9） | `EXPIRE_REQUEST` / `KEEP_WAITING` | §6.3 |
| `ParameterSpec` | レコード | `value_type` / `unit` / `bounds` / `allowed_values` / `default` | §7 |
| `ParameterType` | enum | `BOOL` / `INT` / `FLOAT` / `STR` | §7 |
| `ParameterValue` | union | `BoolValue` / `IntValue` / `FloatValue` / `StrValue`（各区分は `value` 1件） | §7 |
| `NumericBounds` | レコード | `minimum` / `maximum` / `minimum_inclusive` / `maximum_inclusive` | §7 |
| `UnitRef` | enum | `PIPS` / `PRICE` / `RATIO` / `BARS` / `DURATION` | §7 |
| `EvaluationSpec` | レコード | `allowed` / `fixed` / `required_inputs` | §8 |
| `AllowedTrigger` | union | `AllowedBarClose` / `AllowedInputEvent` / `AllowedRuntimeEvent` | §8 |
| `EvaluationSchedule` | レコード | `triggers: tuple[EvaluationTrigger, ...]` | §8 |
| `EvaluationTrigger` | union | `OnBarClose` / `OnInputEvent` / `OnRuntimeEvent` | §8 |
| `RuntimeEventKind` | enum | `POSITION_OPENED`（段階2の唯一の値） | §8 |
| `StateSpec` | レコード | `state_type` / `initial` / `reset_on` | §9.1 |
| `StateInitializer` | union | `LiteralInitialState(values)`（初版はこの1区分） | §9.1 |
| `ResetTrigger` | enum | `RUN_START`（初版の唯一の値） | §9.1 |
| `TemporalConstraints` | レコード | `warmup: WarmupSpec \| None` / `alignment` | §9.2 |
| `WarmupSpec` | レコード | `series: SeriesId` / `bars: int \| ParameterRef` | §9.2 |
| `AlignmentRequirement` | レコード | `input_names` / `rule: AlignmentRule` | §9.2 |
| `AlignmentRule` | enum | `SAME_OBSERVATION_INTERVAL`（段階2の唯一の値） | §9.2 |
| `EntryPolicy` | union | `ImmediateEntry`（フィールドなし） / `AwaitConfirmation` | §10.1 |
| `AwaitConfirmation` | レコード | `deadline: BarsDeadline \| DurationDeadline` / `on_deadline: DeadlineAction` | §10.1 |
| `BarsDeadline` | レコード | `bars: int`（`>= 1`） | §10.1 |
| `DurationDeadline` | レコード | `duration: timedelta`（正） | §10.1 |
| `DeadlineAction` | enum | `EXPIRE`（段階2の唯一の値） | §10.1 |
| `OpportunityValiditySpec` | レコード | `bindings: tuple[ValidityBinding, ...]` | §10.2 |
| `ValidityBinding` | レコード | `source: OutputRef` / `mode: ValidityMode` / `on_missing: MissingInputPolicy` | §10.2 |
| `ValidityMode` | enum | `SNAPSHOT_AT_OPPORTUNITY` / `REQUIRE_UNTIL_ORDER_REQUEST` | §10.2 |
| `OpportunityConcurrencySpec` | レコード | `max_active` / `on_new_trigger` / `on_order_accepted` | §10.3 |
| `OnNewTrigger` | enum | `KEEP_EXISTING` / `SUPERSEDE_EXISTING` | §10.3 |
| `OnOrderAccepted` | enum | `KEEP_OTHERS` / `CLOSE_OTHERS` | §10.3 |
| `RetriggerMode` | enum | `EDGE` / `LEVEL` | §10.4 |

`ValidityMode` / `OnNewTrigger` / `OnOrderAccepted` / `DeadlineAction` / `AlignmentRule` / `ParameterType` は、値そのものは既に本文で確定していた列挙に**型名を与えた**ものであり、値は増やしていない【提案】。

本書が定義しない型は次のとおりで、いずれも他文書が正本である（第1.1節）: `SeriesId`（D03 §3.1）、`TimeframeRef`（D02 §6）、`ContractRef` / `ImplementationRef` / `StrategyRef` / `CompiledStrategyRef` / `ContentDigest`（D02 §9.2）、`MissingInputReason`（D02 §8.3）、`ReasonCode`（D02 §8.1）、取引機会の終端理由（上位 §4.5）。

## 4. 入出力と接続

### 4.1 仕様型【提案】（上位設計書 §4.3.8 の案を確定させる）

| 型 | フィールド |
|---|---|
| `InputSpec` | `data_type: DataTypeRef`、`kind: PortKind`、`arity: InputArity`、`read_spec: InputReadSpec` |
| `OutputSpec` | `data_type: DataTypeRef`、`kind: PortKind`、`reference_schema: Mapping[str, DataTypeRef]`（第11.1節。取引機会を出す出力以外は空）、`retrigger_mode: RetriggerMode \| None`（第10.4節。取引機会を出す出力でのみ必須、他は `None`） |
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
| `RuntimeInputRef` | `target: RuntimeTarget` | 区分は建玉（`POSITION`）・口座（`ACCOUNT`）・取引機会（`OPPORTUNITY`）の3つ。**取引機会は v1.9 で追加**（2026-09-22 の人間の決定 Q14、選択肢1。後続確認の部品が「いまどの機会について評価しているか」を読むために要る。対応するデータ型は登録済みの `opportunity@v1`、読み方は `CurrentContext`、供給元はエンジンではなく戦略ランタイム自身で、詳細は D05 §6.11）。`PENDING_ORDER` は列挙に含めず、宣言に現れたらコンパイル時に拒否する（全体計画 §7.3・§5.3.4 の能力検査） |

## 5. データ型レジストリ `DataTypeRef`

登録制とし、Python のクラス名を動的読込しない【合意済み】。`DataTypeRef(type_id: str, version: int)`、`__str__` は `"<type_id>@v<version>"`（D02 §6 の `TimeframeRef` に合わせる）【提案】。

初版の登録【提案】: `price`、`price_offset`、`ratio`、`condition_state`、`market_permission`、`opportunity`、`confirmation_result`、`order_intent`、`protection_levels`、`management_action`、`position_context`、`account_context`、`volume`（すべて version 1）。レジストリは `declarations` 内の静的テーブルとし、実行時の登録 API を持たない。

`RuntimeInputRef` の入力の型【提案】: `InputSpec.data_type` は他の入力と同じく登録済みの `DataTypeRef` でなければならないため、建玉・口座を読む入力にも型が要る。`target` が `POSITION` なら `position_context@v1`、`ACCOUNT` なら `account_context@v1`、**`OPPORTUNITY` なら `opportunity@v1`**（v1.9）であることをコンパイル時に要求する（第12節 #2）。建玉と口座の payload に何の項目が入るかは、エンジンが評価時点に供給してよい情報の範囲そのものであるため **D06（`RuntimeContextView` ポート）が確定**し、`declarations` は識別子と版だけを持つ（第1.1節の境界表）。取引機会は例外で、**エンジンではなく戦略ランタイム自身が持っている値**を渡すため、供給元は D05 §6.11 である。段階2の検証戦略 A が読むのは建玉の**約定価格・方向・数量・有効な損切り水準**である（損切り水準は v1.9 で追記。固定リスクリワード比の利確がリスク幅 `約定価格 − 損切り水準` を計算するのに要る。項目の正本は D06 §8.4 の `effective_stop_loss`、由来は D05 §11 の差異1）。

**レジストリが持つのは正規化エンコード可能な payload の構造だけ**【提案】。`records` の実行時クラスへの対応は `declarations` に置かない。`declarations` は `strategy` の最下層で `records` を参照できず（D01 §3.3、本書第1節）、クラスを直接持てば禁止された上向き import になり、クラス名の文字列で持てば検査できない対応表になるためである。データ型識別子と実行時クラスの対応、およびその一致検査は `records`（型の側）と `catalog`（部品登録時）に置き、D05 で確定する。

`MarketDataRef` の型【提案】: 市場データ参照は `OutputSpec` を持たないため、接続元の型を `field` から決める。`OPEN` / `HIGH` / `LOW` / `CLOSE` は `price@v1`、`VOLUME` は `volume@v1` とする。この対応表を置かないと、検証戦略 A が使う `MarketDataRef(..., HIGH)` について接続検証（第12節 #2）で比較する型が存在しない。`volume` を独立した型にするのは、出来高を価格として扱う接続（`VOLUME` を `price` 入力へつなぐなど）をコンパイル時に拒否するためである。

接続検証【提案】: 接続元（`OutputSpec` の `data_type`、または `MarketDataRef` の上表による型）と接続先 `InputSpec` で `(type_id, version)` が一致すること。単位は `ParameterSpec` 側（第7節）が持ち、データ型には持たせない。

**`PortKind` も接続元と接続先で一致させる**【提案】。`OutputRef` の接続では `OutputSpec.kind` と `InputSpec.kind` が同じでなければならない。型だけを見ると、繰り返し参照する値（`VALUE`）の出力を、配送イベント（`EVENT` ＋ `DeliveredEvent`）として読む入力につなげてしまい、イベントが配送されないまま接続が通る。第6.1節の表は接続先の `kind` と読み方の組合せしか見ないため、この検査は接続の側に要る（第12節 #2）。市場データ参照は `VALUE` の接続元として扱う。

銘柄の一致検査【提案】: `DataTypeRef` は銘柄を持たないため、銘柄はコンパイラがグラフ上を伝播させて決める。各使用箇所の銘柄は、次の4つの供給元の和集合とする。集合が2つ以上になった使用箇所は拒否する（初版は単一銘柄。上位設計書 §4.7.1）。

| # | 供給元 |
|---|---|
| 1 | 入力に接続された `MarketDataRef.series` の銘柄（第4.3節） |
| 2 | 使用箇所の `EvaluationSchedule` に含まれる `OnBarClose.series` の銘柄（第8節。第8節は銘柄の検査をここへ委ねている） |
| 3 | `TemporalConstraints.warmup` の `WarmupSpec.series` の銘柄（第9.2節） |
| 4 | 上流の使用箇所から伝播した銘柄 |

起動条件（2）とウォームアップ（3）を含めるのは、EURUSD の価格を読みながら USDJPY の足確定で起動する使用箇所のように、**市場データ参照だけを見ていては食い違いを検出できない**ためである。`MarketDataRef` を1つも辿れない使用箇所は銘柄なしとして扱い、`price` 系の出力を持てない。これにより `OutputRef` 経由の価格入力にも銘柄の一致検査が効く。複数銘柄を扱う伝播規則は D10・段階6。

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

動作区分は `SKIP_EVALUATION` / `ERROR` / `WAIT_FOR_INPUT` / `USE_PREVIOUS` の4つ【合意済み】。**v1.9（2026-09-22）で4区分すべてが型として定義された**。段階2 は前2者だけを定義しており、後2者は D05 が意味論と一体でフィールドを確定してから足す約束だった（Q8 決定）。D05 §6.8・§6.9 の起草と、2026-09-22 の人間の決定（Q15・Q16、いずれも選択肢1）でフィールドが確定したため、本節に区分を足し、**保存形式の版（第3節の `schema_version`）を 1 から 2 へ上げた**。

| 区分 | フィールド | 意味 |
|---|---|---|
| `SkipEvaluation` | なし | 評価を行わず、見送ったことを評価記録に残す |
| `Error` | なし | 実行失敗として扱う |
| `WaitForInput`（v1.9） | `deadline: BarsDeadline \| DurationDeadline`、`on_deadline: WaitDeadlineAction`、`on_superseded: OnSuperseded` | 入力が届くまで評価を保留する |
| `UsePrevious`（v1.9） | `max_lookback: BarsWindow \| DurationWindow`、`allowed_reasons: tuple[MissingInputReason, ...]`（1件以上） | 上限内の過去の有効値を明示的に使う |

`WaitDeadlineAction` は `SKIP_EVALUATION` / `ERROR` の2値、`OnSuperseded` は `EXPIRE_REQUEST` / `KEEP_WAITING` の2値の列挙である（v1.9）。期限型（`BarsDeadline` / `DurationDeadline`）は第10.1節のものをそのまま使い、窓型（`BarsWindow` / `DurationWindow`）は第6.2節のものをそのまま使う。**新しい期限型も窓型も作らない**。

**待機できる欠損理由は宣言で選べず、遡りを許す欠損理由は宣言で選ぶ**【合意済み】（Q15・Q16 決定、いずれも選択肢1。意味論の正本は D05 §6.8・§6.9）。待機が解消しうるのは「期待される最新足が未到着」と「窓内の期待足が欠けている」の2つだけで、データ開始前と鮮度切れは待っても直らないため、選ばせる意味がない。遡りは逆に「どこまでを隠してよい欠損とみなすか」が戦略ごとの判断であり、破損データや計算例外まで一律に過去値で隠さないために宣言で絞る【合意済み】上位設計書 §4.3.13。**遡りは `LatestAvailable` にだけ書ける**（固定本数の窓の穴埋めには使えない。第12節 #10）。

**この2区分を段階2 で置かなかった経緯**（Q8 決定、選択肢1。第1.2節の境界表の行1 に対する明示の例外）。宣言形だけを先に決めても、待機期限・遡り上限・記録といったフィールドは待機の意味論と一体でしか定まらず、フィールドの無い空の区分が残るだけになる。そこで D05 がフィールドごと確定してから区分を足し、その追加を保存形式の版の引き上げとして扱うと決めていた。**v1.9 がそのとおりに実施したものである**。**不採用**: 4区分をフィールドなしで先に置く案（空の区分が残る）、段階2 で4区分を確定する案（D05 の検討を前倒しし段階2の範囲を超える）。

診断理由は D02 §8.3 の `MissingInputReason`（`WARMUP_INSUFFICIENT` / `INPUT_MISSING_OR_INVALID` / `LATEST_BAR_UNAVAILABLE` / `MAX_AGE_EXCEEDED`）を再利用し、`declarations` 側で新しい語彙を作らない【提案】。`SKIP_EVALUATION` は False や価格 0 の出力ではなく、評価記録として残す【合意済み】。

**段階2 だけの制限: 取引機会の有効性の再検査では、欠損を失敗扱いにできない（v1.8、2026-09-21 の人間の決定。PR #18）**。取引機会が発注要求まで有効であり続けることを求める束縛（第10.2節の `ValidityBinding(mode=REQUIRE_UNTIL_ORDER_REQUEST)`）に `Error` を書いた宣言は、**段階2 ではコンパイル時の能力検査で拒否していた**（第12節 #7 の拒否一覧。v1.9 で解除済み。下の「解除時期」を参照）。

- 理由: この再検査は評価の外側（発注要求を組み立てる直前）で走るため、失敗を書き残す評価記録が存在しない（D05 §6.4・§7.3）。`Error` を許すと、判断履歴のどこにも残らないまま run が止まる経路ができる。「記録に残らない失敗を作らない」は上位設計書 §4.3.15 の要求である。
- 影響範囲: 本節の欠損方針2区分そのものは変えない。`SkipEvaluation`（その回の再検査を行わず機会を残す）は再検査でも使える。制限がかかるのは「再検査 × `Error`」の組合せ1つだけであり、部品の評価における `Error` は従来どおり使える。検証戦略 A（第14節）は束縛を1件も持たないため、この制限にかからない。
- **解除時期**: 段階3（**v1.9 で解除済み**）。再検査の結果そのものを残す書き先が D05 v2.0 でできた（確認試行の記録 `ConfirmationAttempt` と、再検査による終端を残す遷移記録。D05 §7.7・§7.2 の遷移8）ため、第12節の拒否一覧から外した。
- **不採用**: 記録の無いまま `Error` を実行時に許す案（判断履歴から追えない失敗が残る）、再検査の失敗を直前の評価記録へ後付けする案（評価と再検査は別の処理であり、どの評価に帰属させるかが決まらない）。

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
| `EvaluationSpec` | `allowed: tuple[AllowedTrigger, ...]`（1件以上）、`fixed: bool`（契約が評価条件を固定するか）、`required_inputs: Mapping[str, tuple[str, ...]]`（起動条件名 → 必須入力名） |
| `AllowedTrigger` | `kind` タグ付き。`AllowedBarClose(timeframes: tuple[TimeframeRef, ...] \| None)` / `AllowedInputEvent(input_names: tuple[str, ...])` / `AllowedRuntimeEvent(events: tuple[RuntimeEventKind, ...])` |
| `EvaluationSchedule` | `triggers: tuple[EvaluationTrigger, ...]`（1件以上） |
| `EvaluationTrigger` | `kind` タグ付き。`OnBarClose(name: str, series: SeriesId)` / `OnInputEvent(name: str, input_name: str)` / `OnRuntimeEvent(name: str, event: RuntimeEventKind)` |

- 起動条件に名前（`name`）を付け、`required_inputs` と対応付ける【提案】。これがないと「どの起動条件でどの入力が必須か」を宣言できない（上位設計書 §4.3.9 の残項目）。**`name` は1つの `EvaluationSchedule` の中で一意**とし、重複は構築時に拒否する（`KernelValueError`）。重複を許すと `required_inputs` の1つのキーに2つの起動条件が畳まれ、起動条件ごとに違う必須入力を宣言できなくなるうえ、第3節の正規化（`name` 順）でも並びが一意に定まらない。
- `AllowedTrigger` の検証意味論【提案】: 使用箇所の各 `EvaluationTrigger` は、区分が一致する `AllowedTrigger` が `allowed` に1件以上あり、かつその制約を満たすときだけ有効。`AllowedBarClose.timeframes` は許可する時間足の一覧で、`None` は「任意の時間足を許す」。銘柄は制約せず第5節の銘柄伝播で検査する。`AllowedInputEvent.input_names` は `DeliveredEvent` を読む入力名に限り、契約の `inputs` に存在しなければ構築時に拒否する。`AllowedRuntimeEvent.events` は許可する実行時イベントの一覧。`fixed=True` の契約では `allowed` がちょうど1件で、その1件が制約まで一意に定まる（`timeframes` が `None` でなく1件、など）ことを構築時に要求し、使用箇所は同じ内容の起動条件しか書けない。**`required_inputs` のキーはすべて、使用箇所が宣言した起動条件名のどれかを指していなければならない**（余計なキーを許さない。v1.8、2026-09-21 の人間の決定。PR #18）。逆向き（使用箇所のすべての起動条件名が `required_inputs` に現れること）は求めない。`required_inputs` を持つのは契約、起動条件の名前を選ぶのは使用箇所（第3.1節）であり、必須入力を1件も宣言しない契約（D05 §4.3 の段階2の5部品がそうである）は `required_inputs` が空になる。両向きの一致を求めると、その契約を使う使用箇所はどれもコンパイルできず、検証戦略 A（第14節）が成立しない。指すことができない名前がキーに現れたら、それは契約と使用箇所の食い違いであり、検査 #4 で拒否する。**不採用**: 両向きの一致を求める案（必須入力を宣言しない契約が使えなくなる）、キーの検査をしない案（契約が書いた必須入力の宣言が、名前の綴り違いで黙って効かなくなる）。
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

期限型のフィールド【提案】: `BarsDeadline(bars: int)`（`>= 1`、Trigger の系列の確定足で数える。**この「どの系列で数えるか」は D05 §15 の要決定 Q19 として再検討中**であり、決定が出るまで本節の記述は変えない。D05 §7.7 は確認足の系列で数える形を推奨している）、`DurationDeadline(duration: timedelta)`（正）。`on_deadline` は `DeadlineAction` 列挙で、段階2の値は `EXPIRE` の1つだけであり、期限切れの取引機会は終端理由 `EXPIRED`（上位設計書 §4.5）で終わる。段階3で有効化するまで能力検査が `AwaitConfirmation` を拒否するため、段階2の実行には現れない。

再発火は `EntryPolicy` の責務から外す【合意済み】ADR-0032。`execution_filter` が `None` なら `ImmediateEntry`、`OutputRef` があれば `AwaitConfirmation` であることをコンパイル時に照合する【提案】。

### 10.2 `OpportunityValiditySpec` と `ValidityBinding`【提案】＋【合意済み】（Q5 決定、選択肢1）

`OpportunityValiditySpec(bindings: tuple[ValidityBinding, ...])`、`ValidityBinding(source: OutputRef, mode: SNAPSHOT_AT_OPPORTUNITY | REQUIRE_UNTIL_ORDER_REQUEST, on_missing: MissingInputPolicy)`。必須指定・暗黙の既定値なし【合意済み】ADR-0031。

**後続確認のない戦略でも、空の `bindings` を設定ファイルに明示的に書かせる**（Q5 決定）。`execution_filter` が `None` でも省略を許さず、フィールド自体の省略は `app.config` が拒否する。**不採用**: 省略可にする案（暗黙の既定値が生まれ ADR-0031 に反する）、即時発注専用の区分を設ける案（区分が1つ増える）。

`source` が指せる出力の型と一意性【提案】。`ValidityBinding.source` は **`condition_state@v1` を出す出力**（第5節）でなければならず、それ以外の型（価格・注文意図など）を指す宣言はコンパイル時に拒否する（第12節 #5）。有効性の束縛は「条件が成立し続けているか」を読むものであり、条件かどうかを型で判定できなければ、成立しなくなった取引機会を `MARKET_STATE_INVALIDATED` で終端する規則（ADR-0031）を実装できない。あわせて、`bindings` の中で `(source.instance_id, source.output_name)` が**一意**であることを構築時に要求する。同じ出力に別々の `mode` や `on_missing` を書けると、同じ条件が二通りに分類されるうえ、第3節の正規化の鍵（接続元）でも並びが一意に定まらない。

入力ポートの時間的束縛【提案】: 「対象区間束縛」は `SNAPSHOT_AT_OPPORTUNITY`（取引機会生成時の `OutputRecord` を固定）、「現在状態束縛」は `REQUIRE_UNTIL_ORDER_REQUEST`（再検査時点の最新出力を読む）で表し、`InputReadSpec` に新しいフィールドを足さない。再検査の起動点（確認評価時・`OrderRequest` 生成直前）の実装は D05【合意済み】全体計画 §7.3。

### 10.3 `OpportunityConcurrencySpec`【提案】＋【合意済み】（Q6 決定、選択肢1。Q10 決定、選択肢1）

必須指定・暗黙の既定値なし、置換の禁止、終端理由の語彙は確定済み【合意済み】ADR-0032・上位設計書 §4.5。語彙の正本は上位設計書 §4.5 であり、本書は参照するだけで新しい語を作らない（第1.1節）。非終端の状態名と遷移は D05。

段階2のフィールドは次の3つとする（Q6 決定）。

| フィールド | 型・値 | 意味 |
|---|---|---|
| `max_active` | `int`（`>= 1`） | 同時に保持できる有効な取引機会の上限。上限に達している状態で新しい発火があったときの扱いは `on_new_trigger` が決める |
| `on_new_trigger` | `KEEP_EXISTING` / `SUPERSEDE_EXISTING` | `max_active` に達している状態で新しい発火があったとき、既存を残すか、既存を `SUPERSEDED` で終端して新しい機会を有効にするか。いずれの場合も既存の内容を上書きしない【合意済み】ADR-0032 |
| `on_order_accepted` | `KEEP_OTHERS` / `CLOSE_OTHERS` | ある注文が受け付けられたとき、他の取引機会を `CLOSED_BY_ORDER_ACCEPTANCE` で終端するか残すか。規則を書かない限り暗黙に終端させない【合意済み】ADR-0032 |

ADR-0032 が挙げる4論点のうち「保持」と「同時競合」は `max_active` に畳んでいる。**不採用**: `on_order_accepted` だけを持つ案（段階3で形が変わる）、4論点に1フィールドずつ置く案（段階2で使わない設定が増える）。

**発火は必ず取引機会として記録する**【合意済み】ADR-0032 ＋（Q10 決定、選択肢1）。ADR-0032 は「Trigger の各発火は固有の `opportunity_id` を持つ不変の取引機会を生成する」「異なる Trigger イベントは内容が同じでも別の市場事実として記録する」と定めている。したがって `KEEP_EXISTING` でも新しい発火を黙って捨てず、**新しい `opportunity_id` を持つ取引機会を生成したうえで、有効にせず終端理由 `CONCURRENCY_LIMIT_REACHED` で終端する**。段階2の設定（`max_active=1`、`KEEP_EXISTING`）でも、2本目以降の発火は判断履歴（trace）に残り、期限切れ・置き換え・受付起因の終了と集計上区別できる。

この終端理由は 2026-09-20 に人間が決定し（第19.0節 Q10、選択肢1）、正本である ADR-0032 を補足3 として改訂したうえで、上位設計書 §4.5・§4.7.14 と全体計画書 §5.3.5 の語彙表にも同じ語を加えた。本書はその語を参照しているだけである。**不採用**: 既存の `SUPERSEDED` を双方向の意味へ広げる案（新旧どちらが終わったのか trace から読めない）、発火を取引機会として生成しない案（ADR-0032 に反する）。

**終端する既存の機会の選び方**【提案】。`on_new_trigger=SUPERSEDE_EXISTING` で有効な取引機会が複数ある（`max_active >= 2`）ときに、どれを `SUPERSEDED` で終端するかを決めておく。鍵は**取引機会そのものが持つ項目**で決める。`(取引機会の生成時点, opportunity_id)` の辞書式順序で**最小、すなわち最も古い有効な取引機会を1件**終端し、新しい発火を有効にする。同じ評価時点に生成された機会が並ぶ場合は `opportunity_id` が一意な決着を与えるため、順序は常に定まる。

上位設計書 §4.7.12 の全順序（`decision_time` → `strategy_priority` → `opportunity_id` → `attempt_id`）は**発注試行の受付順**であり、ここでは使えない。まだ発注試行のない有効な取引機会には `attempt_id` がなく、`strategy_priority` は同一戦略内の機会を区別しないためである。両者は対象が異なる規則であり、受付順の規則そのものは変更しない【合意済み】上位設計書 §4.7.12。

段階2の設定は `max_active=1` のため候補は常に1件だが、規則を書かなければ `max_active >= 2` の挙動が実装依存になる。この規則が適用される時点（どのフェーズで判定するか）と、生成時点を表す項目名は D05（取引機会の状態機械）。

### 10.4 Trigger の再武装【提案】＋【合意済み】（Q4 決定、選択肢1）

条件が true であり続ける場合に再発火とするかどうかは Trigger 部品の契約と状態が持つ【合意済み】ADR-0032。

`ComponentContract.parameters`（使用箇所が値を選べる設定）ではなく、契約が固定する属性として `retrigger_mode` を置く（Q4 決定）。**置き場所は取引機会を出す出力の `OutputSpec`**（第4.1節）とする。理由は次の2つ。

- 再武装は「取引機会を出す出力の発火の仕方」であり、部品全体の性質ではない。`data_type` が `opportunity` でない出力には意味を持たない。
- `ComponentContract` のトップレベルのフィールド構成は上位設計書 §4.3.5 が正本で、本書が追加するのは `schema_version` だけである（第3節）。`OutputSpec` に置けば正本を変えずに済み、`ContractRef` の内容ハッシュにも入る（第13.2節）。

`OutputSpec.retrigger_mode: RetriggerMode | None`。`data_type` が `opportunity` の出力では必須（`None` を拒否）、それ以外の出力では `None` でなければならない。値は次の2つで、**段階2では両方を許可する**。

| 値 | 意味 |
|---|---|
| `EDGE` | 条件が不成立から成立へ変わった評価でだけ発火する。成立が続く間は再発火しない。再武装は条件が不成立へ戻った時点 |
| `LEVEL` | 条件が成立している評価ごとに発火する。発火のたびに新しい `opportunity_id` を持つ取引機会を生成する【合意済み】ADR-0032 |

`EDGE` の判定に必要な「直前の評価で条件が成立していたか」は `StateSpec`（第9.1節）で宣言し、初期値も宣言に書く。したがって **`retrigger_mode=EDGE` の出力を持つ契約は、直前の成立を保持できる状態を宣言しなければならない**【提案】。`state_spec` が `None` でないことだけでは足りず、次の2つまで要求し、満たさない宣言はコンパイル時に拒否する（第12節 #6b）。

1. `state_spec.state_type` が `condition_state@v1`（第5節）であること。任意の型（価格など）では直前の成立を表せない。
2. `state_spec.initial` が `LiteralInitialState` で、その `values` が `condition_state@v1` の項目と型に一致すること（＝起動時に発火可能かどうかが宣言に書かれていること。第9.1節）。

`condition_state@v1` の項目そのもの（真偽値1つか、確定足の識別子を伴うか）は D05 が確定する。`LEVEL` は直前の評価を参照しないため、この要求はない。**不採用**: 段階2は `EDGE` のみとする案（`LEVEL` の検証が段階3へ延びる）、宣言せずランタイム規則に委ねる案（宣言から挙動が読めない）。

## 11. 役割出力に関する宣言

### 11.1 `Opportunity.reference_values` のスキーマ【提案】＋【合意済み】（Q7 決定、選択肢1）

突破水準などの根拠値を契約で名前と型を宣言して保持する【合意済み】§4.3.15。

**宣言の置き場所は Trigger 契約の `OutputSpec` の `reference_schema`（`Mapping[str, DataTypeRef]`）とする**（Q7 決定、第4.1節）。`data_type` が `opportunity` でない出力は空の mapping でなければならない。実行時の `Opportunity.reference_values` は、キー集合と各値の型がこの宣言と一致することをランタイムが検査する。後続確認の部品（段階3）は、この宣言を通じて突破水準を型付きで読める。**不採用**: データ型レジストリ側に派生型として登録する案（部品ごとの差を表せない）、実行時検証だけに任せる案（接続検証ができない）。

### 11.2 `ManagementAction` の要求種別【提案】

要求種別は `SET_TAKE_PROFIT`（初期 TP）・`CLOSE_POSITION`（全数量決済）・**`UPDATE_STOP`（損切り水準の更新＝トレーリング）**の3種別である。`UPDATE_STOP` は **v1.9（2026-09-22）で追加**した（D05 §12.1 の依頼3。Q12 の決定により、追従する損切りの部品を段階3 で作ることが決まったため）。段階2 は前2種別だけを使い、能力検査が `UPDATE_STOP` を拒否していた（第12節）。水準は解決済みの絶対価格で渡し、距離型は使わない【合意済み】§4.7.1・§4.7.3。実行時の内容型は `UpdateStop(stop_loss: Price)`（D05 §3・§4.2）、水準を不利な向きへ動かさない判定は部品側（D05 §4.10）、要求の実行意味論（どの執行足から有効か、同じ判断時点の決済要求との競合、価格刻みへの丸め方向）は D06 §8.3 である。

### 11.3 Exit の複数ルール合成【提案】

`exit` は `OutputRef` 1件のみ【合意済み】§4.3.5。複数ルールは明示的な合成部品で表すが、合成部品の契約は D05・段階3。段階2は単一の Exit 部品に限る。

## 12. コンパイル時検査と能力検査

宣言側から要求する検査（全体計画 §5.3.4 の1〜7に対応）【提案】。

第3列は**その検査が読む宣言**である。ここが埋まらない検査は、宣言の側に材料がないことを意味する。検査を足すときは必ずこの列を埋め、埋まらないなら先に宣言型（第3.1節）を足す。

| # | 検査 | 検査が読む宣言（第3.1節の型） |
|---|---|---|
| 1 | 参照の存在: `OutputRef` の `instance_id` / `output_name`、`ContractRef` の版と digest（D02 §9.2） | `StrategyDefinition.components`、`ComponentInstance.contract_ref`、`InputBinding.sources` |
| 2 | 型の整合: `(type_id, version)` 一致、接続元と接続先の `PortKind` 一致（第5節）、`MarketDataRef` の `field` と入力の型の対応（第5節）、`RuntimeInputRef` の `target` と入力の型の対応（第5節）、銘柄の伝播と単一銘柄の検査（第5節）、`arity`、`PortKind` × `InputReadSpec` の組合せ（第6.1節） | `InputSpec`（`data_type` / `kind` / `arity` / `read_spec`）、`OutputSpec.data_type`、`RuntimeInputRef.target`、銘柄の供給元（`MarketDataRef.series`・`OnBarClose.series`・`WarmupSpec.series`） |
| 3 | パラメータ: 名前・型・範囲・列挙値、`ParameterRef` の具体値への解決 | `ParameterSpec`、`ComponentInstance.parameters`、`ParameterRef` |
| 4 | 評価スケジュールが `EvaluationSpec.allowed` の範囲内（第8節の検証意味論）で、`fixed=True` の契約を上書きしていないこと、`required_inputs` のキーがすべて使用箇所の宣言済みの起動条件名を指し（第8節、v1.8）、その入力が契約に宣言されていて使用箇所で接続済みであること | `EvaluationSpec`（`allowed` / `fixed` / `required_inputs`）、`EvaluationSchedule.triggers`、`AllowedTrigger` 各区分の制約 |
| 5 | 役割フィールドの型要求（`trigger`→`opportunity`、`order`→`order_intent`、`protection`→`protection_levels`、`exit`→`management_action`、`market_state`→`market_permission`、`execution_filter`→`confirmation_result`）と、`execution_filter` の有無と `entry_policy` モードの整合、`opportunity_validity` の各 `ValidityBinding` が指す出力の存在と型（`condition_state@v1` であること。第10.2節） | `StrategyDefinition` の役割フィールド（型は上位設計書 §4.3.5 が正本）、`EntryPolicy` の区分、`ValidityBinding.source` と接続先の `OutputSpec.data_type` |
| 6 | 依存グラフの構築（**明示入力の辺＋エンジン上の因果辺**）と循環検出、評価順の導出（時間足から順序を推測しない）【合意済み】全体計画 §5.3.4 の6 | 全 `ComponentInstance.inputs` の `OutputRef`（明示辺）、下表の因果辺 |
| 6b | 出力仕様の付随条件: `data_type` が `opportunity` の出力は `retrigger_mode` が必須で `reference_schema` を持て、それ以外の出力は `retrigger_mode=None` かつ `reference_schema` が空であること（第4.1節）。`retrigger_mode=EDGE` の出力を持つ契約は、`state_spec` が `condition_state@v1` の `LiteralInitialState` 付きで宣言されていること（第10.4節） | `OutputSpec`（`data_type` / `retrigger_mode` / `reference_schema`）、`ComponentContract.state_spec`（`state_type` / `initial`） |
| 7 | 能力検査（本節末尾の「能力検査が拒否する構成」の2表） | 同2表が挙げる各型 |
| 8 | **後続確認の部品が開始足の扱いを宣言していること**（v1.9）: `execution_filter` 役割に接続された出力を持つ使用箇所の契約が `include_start_bar`（`BOOL`、既定値なし）を持ち、使用箇所がその値を明示していること。持たない契約を `execution_filter` に接続した宣言は拒否する | 役割フィールド、`ComponentContract.parameters`、`ComponentInstance.parameters` |
| 9 | **確認足の系列がただ1つに定まること**（v1.9）: `execution_filter` 役割の使用箇所が `OnBarClose` の起動条件を1件以上持ち、その系列が1つであること。系列が定まらないと確認の開始足も期限も決まらない | `EvaluationSchedule.triggers`、`OnBarClose.series` |
| 10 | **待機・遡りと読み方の組合せが許可されたものであること**（v1.9）: `WaitForInput` は `LatestAvailable` と `HistoryWindow` に、`UsePrevious` は **`LatestAvailable` にだけ**書ける【合意済み】上位設計書 §4.3.13 | `InputSpec.read_spec`、`MissingInputPolicy` |
| 11 | **市場状態から取引機会への因果辺を含めて循環を検出すること**（v1.9）: `market_state` 役割がある戦略では、下表の因果辺3本目を引いたうえで #6 の循環検出を行う | 役割フィールド、`OutputSpec.data_type` |
| 12 | **パラメータどうしの関係が成り立つこと**（v1.9）: 契約は各パラメータの範囲しか持てないため、指標部品の `window_bars >= 2 * period` のような関係は解決済みの値でしか確かめられない。関係そのものは部品の契約が持つ（D05 §4.5） | `ComponentInstance.parameters`、`ParameterSpec.bounds` |
| 13 | **出力参照を履歴窓で読む接続の窓が本数で数える窓であること**（v1.9）: `OutputRef` を `HistoryWindow` で読む接続では、窓が `BarsWindow` であり解決済みの本数が 1 以上でなければならない。経過時間の窓（`DurationWindow`）は、上流の出力が出る間隔が宣言から分からず保持本数を導けないため拒否する（D05 §6.12） | `InputSpec.read_spec`（`HistoryWindow.window`）、`InputBinding.sources`、`ParameterRef` の解決結果 |

**エンジン上の因果辺**【提案】。明示的な `OutputRef` だけを辺とすると、エンジンを一周して戻る帰還路を見逃す。全体計画 §5.3.4 の6 が求める「エンジン上の因果辺」を、段階2では次の2本とする。

| 因果辺 | 起点 | 終点 | 理由 |
|---|---|---|---|
| 約定による起動 | `order` 役割に接続された出力を持つ使用箇所 | `OnRuntimeEvent(POSITION_OPENED)` で起動する使用箇所（第8節） | 建玉生成イベントは、その注文意図がエンジンを通って約定した結果である |
| 現在コンテキストの参照 | `order` 役割に接続された出力を持つ使用箇所 | `RuntimeInputRef(POSITION)` を読む使用箇所（第4.3節） | 読む建玉は、その注文意図がエンジンを通って生まれたものである |
| **市場状態の適用**（v1.9） | `market_state` 役割に接続された出力を持つ使用箇所 | `trigger` 役割に接続された出力を持つ使用箇所 | 戦略ランタイムが取引機会の生成時に取引許可を読むため（2026-09-22 の人間の決定 Q10、選択肢1。D05 §7.6）。明示の入力接続が無くても読むので、辺が無いと市場状態が Trigger より後に評価される評価順が通ってしまう |

循環検出は**明示辺と因果辺の和**の上で行う。たとえば注文意図を出す使用箇所が `OnRuntimeEvent(POSITION_OPENED)` でも起動する宣言は自己ループになり、拒否される。これがないと、注文が約定して次の注文を起動する帰還路をコンパイラが通してしまう。検証戦略 A（第14節）では Trigger→注文（明示辺）と注文→利確 Exit（因果辺）の2本が加わるだけで、循環はない。`RuntimeInputRef(ACCOUNT)` は特定の注文に由来しないため辺を引かない。段階3で実行時イベントを増やすときは、この表に辺を追加する。段階3 の市場状態の辺（3本目）は、`market_state` が `None` の戦略（検証戦略 A）では引かれないため、段階2 の循環検出の結果は変わらない。

能力検査が拒否する構成【提案】。**v1.9（2026-09-22）で「段階3 で解除するもの」と「段階3 でも拒否を続けるもの」に分けた**（D05 §12.1 の依頼5・6。区分の理由の正本は D05 §5.6）。拒否は `ReasonCode`（D02 §8.1）付きの構造エラーとし、黙って無視しない。`state_spec` が `None` でないことは拒否の理由にしない（第9.1節、Q3 決定）。

**段階3 で解除するもの**（段階2 では拒否していた）

| 拒否していた構成 | 解除できる理由 |
|---|---|
| `AwaitConfirmation`（確認待ちの発注方針）と、`execution_filter` が `None` でない戦略 | 確認評価の起動・開始足の決め方・`CONFIRMED` から先の経路を D05 §7.7 が確定した。**期限をどの系列の確定足で数えるかだけは未決**（D05 §15 の Q19）であり、その一点が決まるまで期限の本数の数え方は実装しない |
| `MissingInputPolicy` の `WAIT_FOR_INPUT` / `USE_PREVIOUS` | 宣言形（第6.3節、v1.9）と意味論（D05 §6.8・§6.9）が確定した |
| `REQUIRE_UNTIL_ORDER_REQUEST` の束縛に `Error` を書いた宣言（第6.3節の制限、v1.8） | 再検査の失敗を残す書き先ができた（D05 §7.7・§7.2 の遷移8） |
| 空でない `TemporalConstraints` のうち**観測区間の一致**（`AlignmentRequirement`、第9.2節） | 守らせる仕組みを D05 §6.7 が置いた |
| `UPDATE_STOP`（第11.2節） | 追従する損切りの部品を段階3 で作る（Q12 決定、D05 §4.10） |
| 出力参照（`OutputRef`）を**本数で数える履歴窓**（`HistoryWindow(BarsWindow(...))`）で読む接続 | 上流の出力を保持する本数と捨てる時点の規則を D05 §6.12 が置いた（2026-09-22 の人間の決定 Q18、選択肢2） |

**段階3 でも拒否を続けるもの**

| 拒否する構成 | 拒否を続ける理由 |
|---|---|
| `RuntimeInputRef(PENDING_ORDER)` | 未約定注文は戦略部品へ公開しない【合意済み】上位設計書 §4.3.14 |
| 空でない `TemporalConstraints` のうち**ウォームアップ本数**（`WarmupSpec`、第9.2節） | `WarmupSpec.series` は具体的な系列を持つため、系列を使用箇所が選ぶ再利用可能な契約には書けない。ウォームアップ不足は履歴窓の不足として判定できる（D05 §11 の2） |
| 出力参照を**経過時間の履歴窓**（`HistoryWindow(DurationWindow(...))`）で読む接続 | 上流の出力が出る間隔が宣言から分からず、保持本数をコンパイル時に導けない（第12節 #13、D05 §6.12） |
| `POSITION_OPENED` 以外の `RuntimeEventKind` | 検証戦略 B は決済通知・保護水準の更新通知で評価を起動しない。トレーリングは足の確定で起動する（D05 §6.11） |
| 複数銘柄に跨る使用箇所（第5節）、15m より細かい足、距離型 SL、指値、再審査設定 | いずれも段階6・D10（第15節） |

## 13. 設定ファイル表現と内容ハッシュ

### 13.1 YAML 表現【提案】

`configs/strategies/<strategy_id>_v<version>.yaml`（D01 §10.1）。ADR-0018 の読込条件（安全な読込、カスタムタグ禁止、重複キーはエラー、merge key 禁止、`schema_version` 必須、未宣言キー拒否）をそのまま適用する【合意済み】。区分タグ付き union は `kind:` キーで表す【合意済み】D01 §8。

**期間値（`timedelta`）の YAML 表現**【提案】: `DurationWindow.duration`、`DurationDeadline.duration`、`InputReadSpec` の `max_age` など、宣言に現れる期間はすべて **`<正の整数><単位>` の文字列**で書く。単位は `s`（秒）/ `m`（分）/ `h`（時）/ `d`（日）の4つで、1つの値に単位は1つだけ（`1h30m` のような複合は書けず、`90m` と書く）。例: `max_age: "2h"`、`duration: "30m"`。

受理するのはこの1形式だけとし、素の数値（単位が読み手の解釈になる）、ISO 8601 の期間（`PT2H`）、複数単位の連結は `app.config` が拒否する。複数の書き方を受理すると、同じ意味の設定ファイルが別の文字列になり、`app.config` の実装ごとに受理範囲がぶれる。単位の綴りは D02 §6 の時間足の文字列表記（`15m` / `1h` / `1d`）と同じ読み方であり、設定ファイルの中で2つの流儀が並ばないようにする。部品契約（`ComponentContract`）は YAML に書かず、`catalog` のコード側の登録を正本とする【提案】。戦略ファイルが書くのは `ComponentInstance` と役割参照・方針だけである。

`app.config` が Pydantic v2 で検証してから frozen dataclass へ変換し、Pydantic モデルを `app.config` の外へ出さない【合意済み】ADR-0018・D01 §5。`declarations` 側の `__post_init__` は構造的な不変条件（第3節）だけを見る。参照解決・型整合は `compiler` が行い、3箇所で同じ規則を重複実装しない【提案】。

### 13.2 内容ハッシュ【提案】＋【合意済み】（Q2 決定、選択肢1）

D02 §9.3 の `canonical.digest` をそのまま使う。ダイジェスト対象の構造（何を含めるか）を決めるのは各設計文書の責務であり（D02 §9.4 末尾）、本書は `strategy` の3つの参照について次を定める。

| 参照 | ダイジェスト対象【提案】 |
|---|---|
| `ContractRef.digest` | `ComponentContract` のうち **`implementation_ref` を除いた**全フィールド。契約の「受け口・出し口・評価条件・状態・時刻制約」の同一性を表す |
| `StrategyRef.digest` | `StrategyDefinition` 全体。各 `ComponentInstance` は `contract_ref` を通じて上記の契約ダイジェストを含むため、**実装コードの同一性は含まない** |
| `CompiledStrategyRef.digest` | 解決済み設定（具体値へ解決したパラメータ、評価順）に加え、使用する各部品の `ImplementationRef`（ID ＋内容ハッシュ）を含む |

順序に意味を持たせないコレクションは第3節の表に従って構築時に正規化済みであり、ダイジェスト計算の側で並べ替えを重複実装しない【提案】。`strategy_id` / `version` が同じでもパラメータ割当が違えば別の実行として扱う【合意済み】§4.3.5。上表の切り分けを採用する（Q2 決定、選択肢1）。**不採用**: 契約ダイジェストに `implementation_ref` を含める案（実装を直すたびに人間が管理する戦略の版まで変わる）、どちらにも含めず実行全体のコードダイジェストに任せる案（部品単位・戦略単位で実装差を検出できない）。各3クラスの `schema_version`（第3節）はダイジェスト対象に含まれる。

**期間値を含む宣言は、現状このダイジェストを計算できない（v1.8、2026-09-21 の人間の決定。PR #18）**。正規化エンコード（D02 §9.3 v1.6）は期間（`timedelta`）を符号化せず、`KernelValueError` になる。対象は `InputReadSpec.max_age`・`DurationWindow.duration`・`DurationDeadline.duration` を持つ宣言である。第13.1節が定めた設定ファイル上の書き方（`<正の整数><単位>`）は読み込みの書式であって、正規化エンコードの規則ではない。符号化規則を足すかどうかは D02 の次回改訂に引き渡してある。**2026-09-22 の時点でも足していない**（D05 §12.1 の D02 への依頼1 は見送り。段階3 の検証戦略 B も本数で数える宣言だけで書けるため、受入れは止まらない）。検証戦略 A（第14節）はいずれの期間値も持たないため、段階2の実行経路には現れない。

## 14. 段階2の最小範囲（検証戦略 A）と T01

上位設計書 §7.1 の検証戦略 A（1h 終値で直近の確定高値突破 → 後続確認なしの成行注文 → 初期 SL と固定 RR の TP）を宣言できることが、段階2の必要十分条件である。

| 必要な宣言 | 使う型 |
|---|---|
| 直近 N 本高値（当該足を除く） | `MarketDataRef(USDJPY/1h/bid, HIGH)` ＋ `HistoryWindow(BarsWindow(N), exclude_latest_bars=1)` |
| 高値突破 Trigger | `OnBarClose(1h)` 起動、出力 `opportunity`、その `OutputSpec` に突破水準の `reference_schema` と `retrigger_mode=EDGE`（直前の成立を `StateSpec` で保持） |
| 成行注文意図 | `OnInputEvent(取引機会)` 起動、出力 `order_intent` |
| 初期 SL | 確定情報から絶対価格、出力 `protection_levels` |
| 固定 RR の TP | `OnRuntimeEvent(POSITION_OPENED)` 起動、`RuntimeInputRef(POSITION)` ＋ `CurrentContext` 入力、出力 `management_action`（`SET_TAKE_PROFIT`） |
| 戦略全体 | `schema_version=1`、`market_state=None`、`execution_filter=None`、`entry_policy=ImmediateEntry`、`opportunity_validity=bindings: []`（空を明示）、`opportunity_concurrency=(max_active=1, on_new_trigger=KEEP_EXISTING, on_order_accepted=KEEP_OTHERS)` |

T01（紙上トレース）では、この宣言から D06 の注文・約定、D07 の単一評価まで紙上で追跡し、同時刻・数量・末尾処理に暗黙の前提がないことを確認する【合意済み】ADR-0016。

テスト【提案】: 単体（不変条件違反の拒否、`exclude_latest_bars` の境界、`ParameterRef` の解決）、意味論（未宣言キーの拒否、役割の型不一致の拒否、能力検査の各拒否が理由コード付きで返ること）、プロパティ（同じ宣言から同じ digest、`Mapping` のキー順やファイル内の並び順に依存しないこと）、golden（検証戦略 A の YAML → `CompiledStrategy` の固定出力）。

## 15. 対象外（段階3以降）

本節は**時期**の線引き（段階2で作らないもの）であり、第1.2節は**担当文書**の線引き（本書が決めないもの）である。軸が違うため、段階3で作る機能でも宣言の形は本書が決める（`AwaitConfirmation` など）。

- 後続確認（`AwaitConfirmation`）と確認期限の宣言 → 段階3・D05（**宣言は第10.1節で定義済み。能力検査の拒否は v1.9 で解除**。期限をどの系列で数えるかだけ D05 §15 の Q19 として未決）。
- `WAIT_FOR_INPUT` / `USE_PREVIOUS` のフィールド確定 → **v1.9 で第6.3節に反映済み**（保存形式の版は 2）。
- 合成部品（AND / OR / 遷移検出）の契約 → D05 §4.6（段階3 で作る）。N 本継続と「A 後 N 本以内の B」 → D05 §10.2（段階4 以降）。
- 取引機会の非終端の状態名と遷移 → D05 §7（2026-09-21 に確定済み）。
- 再審査設定、距離型 SL、指値、複数建玉・複数銘柄（銘柄の伝播規則を含む）、分割決済 → 段階6・D10。
- `POSITION_OPENED` 以外の実行時イベント（決済通知、保護水準の更新通知）での評価起動 → **段階4 以降**（D05 §10.2。段階3 のトレーリングは足の確定で起動するため要らない）。
- 学習する部品（fit / 推論分離）、探索空間の宣言 → D09 以降。
- `RuntimeInputRef(PENDING_ORDER)` → 未対応として拒否を維持。
- 部品カタログの指標一覧と計算規則（EMA の初期化・更新方法を含む） → D05 §4.5（段階3 の11部品を確定済み）。

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
| `OpportunityConcurrencySpec`（終端理由 `CONCURRENCY_LIMIT_REACHED` を含む） | §10.3・§19 Q6・§19.0 Q10 | 【合意済み】Q6・Q10 決定 |
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

4. **段階3 の改訂は D05 v2.0 の決定に従って入れた**（v1.9、解消済み）。第6.3節の欠損方針2区分・第4.3節の取引機会の対象・第11.2節の `UPDATE_STOP`・第12節の検査6件と因果辺1本・第5節の損切り水準は、いずれも本書が単独で決めたものではなく、**D05 §12.1 が挙げた改訂依頼**を 2026-09-22 の人間の決定（Q10〜Q18）の後に反映したものである。意味論の正本は D05 の該当節であり、本書は宣言の形だけを持つ（第1.2節の境界表は変えていない）。

上記以外に、上位設計書・全体計画書・ADR と食い違う提案はない。

## 18. 他文書への引き渡し

後続文書（D05・D06・D07）へ委ねる事項は、**第1.2節の境界表を正本とする**。本節では重複して並べず、境界表に載らない「既存文書の改訂依頼」と、段階2の実装で行う作業だけを挙げる。

| 引き渡し先 | 項目 |
|---|---|
| D05（境界表に載らない細目） | `MarketDataView` の引数名の整合（第17節の3、D03 §6.2 の `end_offset_bars` と本書の `exclude_latest_bars`）、「状態を持てる部品の範囲」は Q3 で決着済みのため引き渡さないこと |
| D06（境界表に載らない細目） | `ConfigDigest` に戦略のダイジェストをどう含めるか（第13.2節） |
| D01（次回改訂） | §7.2 のモジュール一覧へ `opportunity.py` を追記（第17節の2） |
| `common`（段階2 の実装） | `ReasonCode` 列挙への取引機会の終端理由の追加（`MARKET_STATE_INVALIDATED` / `SUPERSEDED` / `CLOSED_BY_ORDER_ACCEPTANCE` / `CONCURRENCY_LIMIT_REACHED`）と評価要求の追い越し（`REQUEST_SUPERSEDED`）。設計側は D02 §8.1（v1.3）で確定済みで、列挙への反映は取引機会の状態機械を実装する段階2 で行う |

## 19. 承認時の確認事項（2026-09-20 承認: Q3 は選択肢2、他の9件は推奨案を採用）

起草時に選択式で提示した9項目（Q1〜Q9）と、レビュー中に判明して追加提示した1項目（Q10）。ユーザーが 2026-09-20 にすべて決定し、本文へ反映済みで、**未決の項目は残っていない**。「選択肢 n」は提示時に並べた番号で、1 が提示時の推奨案である。

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
| Q10 | 同時保持上限で有効化されなかった取引機会の終端理由の名前 | **選択肢1（推奨）**: 新しい終端理由 `CONCURRENCY_LIMIT_REACHED` を加える | §10.3、ADR-0032 補足3、上位設計書 §4.5・§4.7.14、全体計画書 §5.3.5 |

各項目で採らなかった案は、本文の該当節に「不採用」として1行ずつ残してある。

### 19.0 Q10（決定済み。2026-09-20、選択肢1。ADR-0032 の改訂を伴った）

レビュー3巡目で判明し、Q1〜Q9 とは別に人間へ提示した1項目。**2026-09-20 に選択肢1 で決定し、本文と正本の文書へ反映済み**。

**Q10 同時保持上限に達して有効化されなかった取引機会の終端理由の名前**
決めたこと: `on_new_trigger=KEEP_EXISTING` の設定で上限に達しているときに発火した取引機会を、どの終端理由で記録するか。
影響: 判断履歴（trace）で「上限で見送った発火」を他の終端（期限切れ・新しい発火に置き換えられた・他の注文が通った）と集計上区別できるかどうかが決まる。
**決定（選択肢1、推奨案）**: 新しい終端理由 `CONCURRENCY_LIMIT_REACHED` を加える。上限で見送った発火だけを集計でき、既存4語彙の意味を変えない。ADR-0032 が受付起因の終了に専用の理由（`CLOSED_BY_ORDER_ACCEPTANCE`）を与えた判断と同じ考え方である。
**不採用**: 既存の `SUPERSEDED` を双方向の意味へ広げる案（新旧どちらが終わったのか trace から読めない）、終端理由を持たせず発火を取引機会として生成しない案（ADR-0032 の「異なる Trigger イベントは別の市場事実として記録する」に反する）。
反映先: 第10.3節（規則と理由の確定）、ADR-0032 の決定の補足3 と改訂履歴、上位設計書 §4.5 の終端理由表・§4.7.14 の理由コード表、全体計画書 §5.3.5 の終端理由行と §9 の E-3 行。語彙の正本は上位設計書 §4.5 であり、本書は参照するだけである（第1.1節）。

### 19.1 Q3（任意の部品で状態を許す）が他の決定に与える影響

役割で状態を制限しない決定のため、次の点が推奨案の場合と変わる。

- コンパイラの能力検査は、`state_spec` が `None` でないことだけを理由に拒否しない（§12）。
- 段階2の実装でも、EMA のような再帰計算の部品を後から契約を変えずに追加できる。段階2で実際に使うかどうかは D05 の初版カタログの範囲で決める。
- D05 へ引き渡すのは「状態を持つ部品の計算規則と保存・復元」であり、「状態を持てる部品の範囲」ではない（§18）。
