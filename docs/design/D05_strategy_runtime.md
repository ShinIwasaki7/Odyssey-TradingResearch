# D05: 戦略ランタイム・部品カタログ・コンパイラ設計（`odyssey_fx.strategy.runtime` / `strategy.catalog` / `strategy.compiler` / `strategy.records`）

作成日: 2026-09-21
状態: **v2.0（2026-09-22）。段階3 範囲は Q10〜Q18 決定済み、Q19〜 は未決**。段階2 で承認済みの範囲（**承認（2026-09-21、PR #15）**、v1.0〜v1.4）は本改訂で変えていない。
v2.0（2026-09-22、PR #22）: 第15節の要決定 **Q10〜Q18 をユーザーがすべて決定**し、本文へ反映した。Q10〜Q17 は提示時の推奨案（選択肢1）、**Q18 は選択肢2（上流の出力を履歴窓で読む仕組みを段階3 で作る。推奨ではない）**である。Q18 の決定により、段階2 が「最新1件だけ保持」と決めていた出力の保持（第6.5節）と、出力参照を履歴窓で読む接続の拒否（第6.3節・第5.6節・第10.2節）を改め、**保持する本数の上限の決め方・捨てる時点・待機からの再開時の扱い・判断履歴への記録**を第6.12節に置いた。あわせて第12.1節の他文書への改訂依頼18件を正本へ反映し（D04 v1.9・D06 v1.5・D03 v1.6・上位設計書・D02 v1.8。反映16件、見送り2件）、確認期限を数える系列の改め（第11節の6）と Q18 に伴って選択が割れる2点を、**新しい要決定 Q19〜Q21** として第15節に残した。
v2.0-draft（2026-09-22）: ADR-0016 の実装開始条件5（待機・追い越し・複雑な確認は D05 の残りとして段階3 前に確定する）と全体計画 §8.2 の段階3 の行に従い、**段階3「複数の時間足と後続確認」に必要な残りの設計**を追加した。追加した範囲は、複数の時間足をまたぐ入力と出力の鮮度（第6.7節）、市場状態の適用（第7.6節）、後続確認と `CONFIRMED` 経路（第7.7節。第7.2節の遷移10・11 の発火条件を含む）、入力が足りないときの待機（`WAIT_FOR_INPUT`、第6.8節）と過去値への遡り（`USE_PREVIOUS`、第6.9節）、評価要求の追い越し（`REQUEST_SUPERSEDED`、第6.10節）、段階3 のカタログ（第4.5〜4.10節）、検証戦略 B の宣言（第9.2節）、遅延シナリオ4ケースが判断履歴にどう現れるか（第9.3節）である。第1.2節の境界表を「段階2 で確定 / 段階3 で本改訂が決める / 後続に委ねる」の3列に改め、**本改訂のレビュー対象範囲は「段階3 で本改訂（v2.0）が決めること」の列**とした。段階3 の要決定は第15節に Q10 以降として一覧する（Q1〜Q9 は段階2 で決定済みであり、番号は再利用しない）。他文書への改訂依頼は第12.1節に集約した。
v1.4（2026-09-22、PR #20）: 第6.3節の**履歴窓の受け口を構造的な `Protocol` に改めた**（人間の決定）。宣言の履歴窓（`strategy.declarations.read_spec`、本数はコンパイル時に解決するため整数またはパラメータ参照）と as-of ビューの履歴窓（`marketdata.application.asof`、本数は解決済みの整数）は**同じ名前の別の型**であり、どちらが境界を渡るかをどの設計文書も決めていなかった。`strategy` は `marketdata.application` を参照できず（契約 F2）、`marketdata` は `strategy` を参照できない（層順序）ため、それまでは両方を参照できる合成（`app`）が言い換えていた。受け口の側で構造（本数か経過時間を読み出せること）だけを要求する形にして、その言い換えを無くした。入力不足の診断（`MissingInputView`）と同じ手法である。裏側の改訂として D03 §6.2 を v1.5 にした。v1.3（2026-09-21、PR #18）: 段階2 の戦略基盤の実装が埋めた記述不足を、人間の決定により本文へ確定した。第2節（`catalog` のモジュール2件を追記）、第4.4節（部品が上の層の型を構造だけの `Protocol` で受けること）、第5.4節（「同順位」を Kahn 法の段として定義）、第5.5節（`ImplementationRef.digest` の算出規則と、期間値を含む宣言の制限）、第6.2節 手順9（宛先の建玉が無い保有管理の要求は構造エラー）、第6.3節（必須入力の宣言が無い場合は接続済みの入力すべてを必須とする）、第6.6節（`step` 内の通し番号の規則）、第7.2節（受付結果による終端を記録するフェーズ）。レビュー指摘3件も反映した（対象区間を持たない性質が入力イベントの連鎖を伝うこと: 第6.2節 手順3、空でない時刻制約を段階2 では拒否すること: 第10節・第11節の2、置換の前に新しい機会の束縛を固定すること: 第7.3節）。v1.2（2026-09-21、PR #16）: 紙上トレース T01 が見つけた欠落の補完。**発注の根拠になった出力を指す識別子が戻り値に無く、バックテストエンジンが正常経路でも注文要求（`OrderRequest`）を組み立てられなかった**ため、`EntryProposal` に `intent_output_id` / `protection_output_id`、`ManagementRequest` に `source_output_id` を足した（第3節の型表・第6.2節の手順9・第12節）。上位設計書 §4.7.8 が要求する根拠の識別子の連鎖を戻り値だけで満たすための補完であり、設計の選択は伴わない。v1.1（2026-09-21、PR #16）: D06 の要決定 Q2 に対する人間の決定（選択肢1）により、run 末尾に残った取引機会を終端させる合図を公開バッチで渡す形に確定し、`PublicationBatch` に「これが末尾である」ことを示す項目（`is_run_end`）を足した（第3節の型表・第6.1節・第7.2節の遷移9）。戦略ランタイムの入口は `step` 1つのままである。v1.0: 第13節の要決定 Q1〜Q9 をユーザーがすべて決定し（全件で選択肢1、提示時の推奨案）、本文へ反映済み。Q3・Q4 の決定に伴い、取引機会の終端理由に `ORDER_ATTEMPT_REJECTED` と `FULFILLED_BY_ORDER_ACCEPTANCE` の2語を加え、正本である上位設計書 §4.5・§4.7.14、全体計画書 §5.3.5、D02 §8.1（v1.4）、ADR-0032（決定の補足4）を同じ PR で改訂した。Q1 の決定に伴い、上位設計書 §4.5 が仮置きとしていた非終端の状態名を `OPEN` / `CONFIRMED` / `ORDER_PENDING` に確定し、同節の未解決3点を解消した。段階2（最小縦断＝戦略定義→注文→約定→単一評価）と紙上トレース T01 に必要な範囲だけを扱う。検証戦略 A（1時間足の高値突破→後続確認なしの成行→初期損切り＋固定リスクリワード比の利確）が動くことを必要十分条件とする。待機（`WAIT_FOR_INPUT`）・評価要求の追い越し・後続確認は本書の**後続版 v0.2**（段階3前）で確定する（全体計画 §6 D-2 の条件5）。第13節に決定の一覧を置く。レビュー1巡目の指摘4件を反映済み（取引機会の対象区間と銘柄をランタイムが付ける形に整理: 第4.2節・第6.2節、実行時イベントは通知1件につき1評価要求とし建玉を伝播: 第6.2節・第8節、処理点をエンジンのフェーズ集合から組み立てる: 第6.1節、部品の戻り値を付番の前に検査: 第6.2節）。2巡目の指摘8件も反映済み（終端した取引機会を下流へ配送しない: 第6.2節・第7.4節、繰り返し参照する値の最新出力をランタイムが保持: 第6.5節、評価要求と評価記録へ取引機会・建玉・対象区間を伝播: 第6.2節・第6.4節、失敗を例外ではなく戻り値で返す: 第6.2節、登録時に実装参照の一致を検査: 第4.1節、対象区間が異なる起動は集約しない: 第6.2節・Q6、入力の接続数の指定漏れを補正: 第4.3節）。3巡目の指摘4件も反映済み（市場データの足は項目を射影してから部品へ渡す: 第6.3節、取引機会を組み立ててから付番・送出する順序に修正: 第6.2節、入力イベントも配送1件につき1評価要求: 第6.2節・Q6、部品の呼び出しが例外で終わった場合も失敗として返す: 第6.2節）。4巡目は対象内の重大な設計違反・契約違反が0件で収束し、改善提案2件を反映した（出力参照を履歴窓で読む接続を段階2では拒否: 第5.2節・第6.3節・第10節、発注試行中の取引機会は置換の対象にしない: 第7.2節・第7.4節）。決定反映後の巡では境界表の3点を明確化した（約定後の起動点は「約定処理の後」まで本書が確定し D06 へはフェーズ順位だけを委ねる: 第1.2節の行6・第8節、内容型の決定範囲を D04 §1.2 の行3 に合わせて列挙: 第1.2節の行1、段階3の遷移10・11 は発火条件の詳細だけを v0.2 へ切り出し: 第1.2節の行5・第7.2節）。2巡目は対象内の重大な設計違反・契約違反が0件で収束し、改善提案2件を反映した（条件の成否を表す型のフィールドの正本を上位設計書 §4.3.15 に一本化: 第1.1節・第1.2節の行1・第4.2節、役割出力の `PortKind` の決定を対象内へ明記: 第1.1節・第1.2節の行1）。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.3.11〜§4.3.15・§4.5・§4.6・§4.7.1・§7.1、[全体計画書](fx_research_platform_overall_plan.md) §5.3.2〜§5.3.5・§7.3 後半・§8.1・§8.2、[D01](D01_architecture_and_dependency_rules.md) §3.3・§4・§5.1・§7.2、[D02](D02_common_kernel.md) §3.3・§4・§7・§8・§9、[D03](D03_marketdata_and_time.md) §6.2・§7.1・§7.2、[D04](D04_strategy_declarations.md) 全体（特に §1.2 の境界表）、ADR-0006（決定論的 ID）、ADR-0008（純粋関数の部品）、ADR-0011（frozen dataclass）、ADR-0012（Decimal / float 境界）、ADR-0016（実装開始条件）、ADR-0021（NumPy の許可範囲）、ADR-0030（足内 SL/TP 競合）、ADR-0031（確認待ち中の条件再検査）、ADR-0032（再発火と複数取引機会）、ADR-0033（評価要求の追い越しの改名）
対応段階: 段階2で実装。ADR-0016 条件2 のうち「D05 の最小ランタイム範囲」を本書 v0.1 で充足する。

## 0. 本書の位置付けと凡例

D04 が確定した宣言（戦略の「形」）を、**実行可能な形へ変換し（`compiler`）、実装を対応付け（`catalog`）、公開イベントに対して評価する（`runtime`）**手順と型を決める。注文の受付・執行・約定、口座と建玉、評価指標は決めない。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D04）で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択する事項。段階2 の Q1〜Q9 は **2026-09-21 に決定済み**（第13節）、段階3 の Q10〜Q18 は **2026-09-22 に決定済み**（第15節）。**未決は Q19 以降**で、第15節に選択肢と推奨を置く |
| 【確定】 | 承認後に人間の決定で本文へ確定した事項（v1.3・v1.4・v2.0 で使った印） |

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

### 1.2 段階2 で確定したこと・段階3 で本改訂が決めること・後続に委ねること【提案】（**レビュー対象範囲の正本**）

**v2.0 のレビューの対象範囲は、下表の「段階3 で本改訂（v2.0）が決めること」の列**である。列は名前で呼ぶ（番号で数えると、段階2 の4列から段階3 の5列へ増えたときに指す先がずれる）。

| 列 | 呼び方 | v2.0-draft での扱い |
|---|---|---|
| 3列目 | **段階2 で確定** | 承認済み。**本改訂では原則として変えない**。ここへの指摘は「段階2 の確定への異議」として記録し、本 PR では直さない。**唯一の例外**は、上流の出力を履歴窓で読む仕組みを段階3 で作るという人間の決定（Q18、選択肢2）に伴う出力の保持の改訂であり、第11節の差異10 に挙げて要決定へ上げたうえで改めている |
| 4列目 | **段階3 で本改訂（v2.0）が決めること** | **レビュー対象範囲**。ここへの指摘を直す |
| 5列目 | **後続が決めること** | 対象外（担当へ）として記録し、本書では直さない |

ただし D04 §1.2 と同じ例外を置く。**本書の文が「後続が決めること」の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。その列の内容を本書に書き足すことはしない。

「段階2 で確定」の内容を段階3 の都合で書き換える必要が出た場合は、黙って直さず「段階2 の確定の改訂」として第11節に差異として挙げ、要決定（第15節）に上げる。

| # | 領域 | 段階2 で確定（v1.0〜v1.4。本改訂では変えない） | 段階3 で本改訂（v2.0）が決めること（**対象内**） | 後続が決めること（対象外・担当） |
|---|---|---|---|---|
| 1 | 実行時の内容型 | 上位設計書 §4.3.15 に無い内容型（`OrderIntent` / `ProtectionLevels` / `ManagementAction` / `TradeDirection` / `OrderType` / `OpportunityContent`）のフィールド、データ型識別子13件と内容型の対応表、役割フィールドごとの `PortKind`（第4.2節） | 段階3 で足す内容型（`ConfirmationOutcome` / `UpdateStop`）のフィールドと、`Observation` で包んで配送するデータ型の範囲（第4.2節の改訂・第6.7節） | `position_context@v1` / `account_context@v1` の項目（**D06 §8.4 が確定済み**）、注文有効期限の既定値（**D06**） |
| 2 | 部品カタログ | 登録の単位、実装の形、段階2 の5部品の契約と計算規則（第4節） | 指標部品（EMA・ATR）、比較部品、市場状態部品、確認部品（ExecutionFilter）、トレーリング Exit、合成部品の契約と計算規則（第4.5〜4.10節） | 学習する部品（fit / 推論分離）と探索空間（**D09 以降**）、期間 Exit など段階3 の範囲外の部品（**段階4 以降**） |
| 3 | コンパイラ | 手順・検査の実行順・失敗時の扱い・`CompiledStrategy` の中身・依存グラフ構築・評価順の導出・ハッシュ計算の手順（第5節） | 段階3 で解除する能力検査の一覧（第5.6節）、段階3 で必要になる検査6件の**D04 §12 への改訂提案**とその実行順・失敗時の扱い（第5.6節・第12.1節）、`CompiledRoles` に確認の計画を載せること・`CompiledStrategy` に出力の保持本数の計画を載せること（第5.3節の改訂） | 検査項目そのものと各検査が読む宣言（**D04 §12 が正本**。本書は提案し、確定は D04 の改訂で行う。本 PR では D04 v1.9 として反映済み） |
| 4 | ランタイムの評価 | 起動判定、入力解決、欠損の扱い、評価要求のライフサイクル、部品状態の保持、出力の付番と決定論（第6節） | 複数の時間足をまたぐ入力と出力の鮮度・観測区間（第6.7節）、待機（`WAIT_FOR_INPUT`、第6.8節）、遡り（`USE_PREVIOUS`、第6.9節）、追い越し（`REQUEST_SUPERSEDED`、第6.10節）の意味論と宣言形、**上流の出力を履歴窓で読む仕組み**（保持する本数と捨てる時点。第6.12節。Q18 決定、選択肢2） | 待機記録・追い越し記録の判断履歴への保存形式（**D06 v1.5 で確定済み**）、遅延シナリオ4ケース別の処理順の検証（**D08**） |
| 5 | 取引機会 | 非終端状態と終端、遷移の全体像（第7.2節の遷移1〜11）と、段階2 で発生する遷移1〜9 の発火条件・フェーズ・記録する理由、有効性の再検査、同時保持と発火の記録、`opportunity_id` の採番と生成時点の項目（第7節） | 遷移10・11 の発火条件（確認評価の起動、`AwaitConfirmation` の期限の数え方、開始足の識別）と `CONFIRMED` 経路の全体（第7.7節）、市場状態の適用点と許可されない発火の扱い（第7.6節） | 受付結果を通知するフェーズと通知の内容（**D06 §4.1・§6.6 が確定済み**）、再審査（**段階6・D10**） |
| 6 | 約定後の評価 | `POSITION_OPENED` を受けて利確を評価する起動点を同じ判断時点の約定処理の後に置くこと（第8節、Q7 決定） | 建玉・取引機会を対象とする評価要求を足の確定で作る規則（第6.11節。トレーリングと確認の両方が使う） | その起動点に与えるフェーズ順位（**D06 §4.1 が rank 12 で確定済み**）、トレーリングの適用意味論（**D06 v0.3**） |
| 7 | 数値の境界 | 比率パラメータを部品へ渡すときの Decimal 化（第4.4節） | 段階3 の部品（EMA・ATR）が使う除算・平滑化の丸め規則（第4.5節） | 価格刻みへの丸め方向と適用時点（**D06**）、通貨換算（**D06**） |
| 8 | 記録 | 評価記録・取引機会の遷移記録として何を残すか（第6.4節・第7.2節） | 待機・遡り・追い越しを評価記録のどこに残すか（第6.8〜6.10節）、遅延シナリオ4ケースが判断履歴にどう現れるか（第9.3節） | trace の保存形式と `BacktestResult`（**D06**）、終端理由別・診断理由別の集計（**D07**） |

前提として **D04 から受け取るもの**は次のとおりで、本書はこれらを再定義しない。

| D04 の節 | 受け取るもの |
|---|---|
| §3・§3.1 | 3クラスのトップレベルと、宣言型35件のフィールド一覧 |
| §5 | データ型識別子13件の登録、接続検証（型・`PortKind`）、銘柄の伝播規則 |
| §6 | 読み取り条件4区分と、段階2 の欠損方針2区分（`SkipEvaluation` / `Error`）。残る2区分（`WAIT_FOR_INPUT` / `USE_PREVIOUS`）のフィールドは **D04 §1.2 の行1 が明示の例外として本書へ委ねている**（第6.8節・第6.9節） |
| §8 | 起動条件の型と検証意味論、`RuntimeEventKind` は `POSITION_OPENED` の1値 |
| §9.1 | 状態の型・初期値・リセット契機の宣言 |
| §9.2 | `TemporalConstraints`（ウォームアップと観測区間の一致）。段階2 は空でないものを拒否し、守らせる仕組みは本書 v2.0 が足す（第6.7節） |
| §10.1 | `AwaitConfirmation(deadline, on_deadline=EXPIRE)` と期限型（`BarsDeadline` / `DurationDeadline`）。段階2 は能力検査で拒否し、段階3 で有効にする（第7.7節） |
| §10.2〜§10.4 | 有効性束縛、同時保持の3フィールド、再武装（`EDGE` / `LEVEL`）と状態の要求 |
| §11.2 | `ManagementAction` の要求種別。`UPDATE_STOP`（トレーリング）は段階3 で足す（第4.2節の改訂） |
| §12 | コンパイル時検査 #1〜#7 と #6b、エンジン上の因果辺2本、段階2 で拒否する構成 |
| §13.2 | 3つのダイジェストの対象 |
| §14 | 検証戦略 A に必要な宣言の一覧 |

## 2. モジュール構成【提案】

D01 §7.2 の一覧のうち、段階2で作るものと作らないものを分ける。

| サブパッケージ | 段階2 で作った（v1.x） | 段階3（v2.0）で足す |
|---|---|---|
| `records` | `records.py`（`OutputRecord` / `Observation`）、`payloads.py`（役割別内容型） | `payloads.py` に `ConfirmationOutcome` と `UpdateStop` を足す（第4.2節） |
| `catalog` | `registry.py`、`inputs.py`（部品が受け取るものの形。第4.4節）、`initial.py`（状態の初期値の組み立て）、`features/extreme.py`、`triggers/breakout.py`、`orders/market.py`、`protection/level_stop.py`、`exits/fixed_rr.py` | `features/ema.py`、`features/atr.py`（第4.5節）、`conditions/compare.py`、`conditions/logic.py`、`conditions/transition.py`（第4.6節・第4.9節）、`permissions/from_condition.py`（第4.7節）、`filters/condition_filter.py`（第4.8節）、`exits/trailing_stop.py`（第4.10節） |
| `compiler` | `validate.py`、`graph.py`、`capability.py`、`hashing.py`、`compiled.py` | 既存モジュールの改訂のみ（第5.6節）。新しいモジュールは作らない |
| `runtime` | `ports.py`、`evaluator.py`、`requests.py`、`opportunities.py` | `waiting.py`（待機記録と再開。第6.8節・第6.9節）、`supersession.py`（追い越しの検査。第6.10節）、`confirmation.py`（確認の制御。第7.7節）、`output_history.py`（上流の出力の保持と打ち切り。第6.12節。Q18 決定） |

D01 §7.2 の `runtime/` の一覧にある `waiting.py` / `supersession.py` を段階2 で作らなかったのは、待機と追い越しの意味論が本改訂の範囲だからである（第10節）。`confirmation.py` と `output_history.py` は D01 §7.2 の一覧に無いモジュールの追加であり、D01 §7.2 の但し書き（「モジュール名は初期構成であり、後続文書が本書の層規則内で追加・分割できる」）の範囲内である。`catalog` の部品は D01 §7.2 が挙げるサブディレクトリ（`features/` `conditions/` `permissions/` `filters/` `orders/` `protection/` `exits/`）にそのまま収まる。

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
| `EntryProposal` | `runtime` | レコード | `opportunity_id: OpportunityId` / `order_intent: OrderIntent` / `protection: ProtectionLevels` / `decision_time: UtcTime` / `intent_output_id: OutputId`（v1.2） / `protection_output_id: OutputId`（v1.2） | §6.2 |
| `ManagementRequest` | `runtime` | レコード | `position_id: PositionId` / `action: ManagementAction` / `decision_time: UtcTime` / `source_output_id: OutputId`（v1.2） | §6.2 |
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

**段階3（v2.0）で足す型と、改訂する型**【提案】。同じ規約（この表にない型名を本文で使わない）を段階3 にも適用する。「改訂」はフィールドを足す既存型で、**段階2 の実装（PR #18）で作った型に対する変更**であることを明示する。

| 型 | 置き場所 | 区分 | フィールド / 値 | 詳細 |
|---|---|---|---|---|
| `ConfirmationOutcome` | `records` | レコード | `confirmed: bool` / `reference_values: Mapping[str, object]`（識別子・確認足の区間は持たない） | §4.2 |
| `UpdateStop` | `records` | `ManagementAction` の区分（**改訂**: 段階2 の2区分に1件足す） | `stop_loss: Price` | §4.2 |
| `WaitForInput` | `declarations.missing` | `MissingInputPolicy` の区分（**D04 §6.3 への追加依頼**） | `deadline: BarsDeadline \| DurationDeadline` / `on_deadline: WaitDeadlineAction` / `on_superseded: OnSuperseded` | §6.8 |
| `UsePrevious` | `declarations.missing` | `MissingInputPolicy` の区分（**D04 §6.3 への追加依頼**） | `max_lookback: BarsWindow \| DurationWindow` / `allowed_reasons: tuple[MissingInputReason, ...]`（1件以上） | §6.9 |
| `WaitDeadlineAction` | `declarations.missing` | enum（**D04 への追加依頼**） | `SKIP_EVALUATION` / `ERROR` | §6.8 |
| `OnSuperseded` | `declarations.missing` | enum（**D04 への追加依頼**） | `EXPIRE_REQUEST` / `KEEP_WAITING` | §6.10 |
| `WaitingRequest` | `runtime.waiting` | レコード | `request: EvaluationRequest` / `pinned_bars: Mapping[str, BarKey]`（入力名 → 固定した対象足） / `pinned_events: Mapping[str, tuple[EventDelivery, ...]]` / `opportunity: Opportunity \| None` / `missing: tuple[MissingInputDiagnosis, ...]` / `started_at: ProcessingPoint` / `deadline_at: WaitDeadline` / `on_deadline: WaitDeadlineAction` / `on_superseded: OnSuperseded` | §6.8 |
| `WaitDeadline` | `runtime.waiting` | union | `WaitUntilTime(at: UtcTime)` / `WaitUntilBars(series: SeriesId, remaining: int)` | §6.8 |
| `WaitEvent` | `runtime.waiting` | レコード | `request_id: RequestId` / `kind: WaitEventKind` / `at: ProcessingPoint` / `reason: Reason \| None` / `arrived: tuple[str, ...]`（到着した入力名） | §6.8 |
| `WaitEventKind` | `runtime.waiting` | enum | `WAIT_STARTED` / `INPUT_ARRIVED` / `RESUMED` / `DEADLINE_REACHED` / `SUPERSEDED` | §6.8 |
| `SubstitutedInput` | `runtime.requests` | レコード | `input_name: str` / `source: ResolvedSource` / `used_bar_key: BarKey \| None` / `used_output_id: OutputId \| None` / `freshness_time: UtcTime` / `reason: MissingInputReason` | §6.9 |
| `ConfirmationPlan` | `compiler` | レコード | `filter_instance: str` / `series: SeriesId` / `include_start_bar: bool` / `deadline: BarsDeadline \| DurationDeadline` / `on_deadline: DeadlineAction` | §5.3・§7.7 |
| `OutputRetentionPlan` | `compiler` | レコード | `by_output: Mapping[OutputRef, int]`（出力参照 → 保持する本数。1 以上。読み手が1つも無い出力は載せない） | §5.3・§6.12 |
| `RetainedOutput` | `runtime.output_history` | レコード | `record: OutputRecord` / `subject: BarKey \| None` / `observation_interval: Interval \| None`（`Observation` から写した観測の識別。第6.7節） | §6.12 |
| `ConfirmationAttempt` | `runtime.confirmation` | レコード | `opportunity_id: OpportunityId` / `bar_key: BarKey` / `request_id: RequestId` / `outcome: ConfirmationAttemptOutcome` | §7.7 |
| `ConfirmationAttemptOutcome` | `runtime.confirmation` | enum | `CONFIRMED` / `NOT_CONFIRMED` / `SKIPPED` / `WAITING` / `SUPERSEDED` | §7.7 |
| `ValueSample` | `runtime.requests` | レコード（**改訂**） | 段階2 の4項目に `observation_interval: Interval \| None` と `subject: BarKey \| None` を足す | §6.7 |
| `EvaluationRequest` | `runtime.requests` | レコード（**改訂**） | 段階2 の7項目に `attempt_index: int`（同じ `step` の中で同じ使用箇所が作った要求の通し番号。0 起点）を足す | §6.11 |
| `EvaluationRecord` | `runtime.requests` | レコード（**改訂**） | 段階2 の9項目に `substitutions: tuple[SubstitutedInput, ...]`（既定は空）を足す | §6.9 |
| `EvaluationOutcome` | `runtime.requests` | union（**改訂**） | 段階2 の3区分に `Waiting(diagnoses: tuple[MissingInputDiagnosis, ...], deadline_at: WaitDeadline)` と `Superseded(by_request_id: RequestId)` を足す | §6.8・§6.10 |
| `OpportunityLifecycle` | `runtime.opportunities` | レコード（**改訂**） | 段階2 の7項目に `confirmation_start_bar: BarKey \| None` / `deadline_at: WaitDeadline \| None` / `attempts: tuple[ConfirmationAttempt, ...]` を足す | §7.7 |
| `CompiledRoles` | `compiler` | レコード（**改訂**） | 段階2 の6項目に `confirmation: ConfirmationPlan \| None` を足す | §5.3 |
| `CompiledStrategy` | `compiler` | レコード（**改訂**） | 段階2 の9項目に `output_retention: OutputRetentionPlan` を足す（読み手が1つも無ければ空の `by_output`） | §5.3・§6.12 |
| `RuntimeState` | `runtime` | レコード（**改訂**） | 段階2 の4項目に `output_history: Mapping[OutputRef, tuple[RetainedOutput, ...]]`（古い順。`OutputRetentionPlan` に載る出力参照だけを持つ）を足す | §6.5・§6.12 |
| `RuntimeStepResult` | `runtime` | レコード（**改訂**） | 段階2 の5項目に `wait_events: tuple[WaitEvent, ...]`（既定は空）を足す | §6.8 |

`BarsDeadline` / `DurationDeadline` / `DeadlineAction` / `BarsWindow` / `DurationWindow` / `MissingInputReason` は D04・D02 が正本であり、本書は参照するだけである（第1.1節）。

`SeriesId` / `BarKey` は D03 §3.1・§3.3、`Symbol` / `Price` / `Interval` / `UtcTime` / `Decimal` / `Reason` / `PhaseRank` / `PhaseSet` / `ProcessingPoint` と各 ID 型は D02、`OutputRecord` / `Opportunity` ほか6つの内容型は上位設計書 §4.3.15 が正本である。`OutputId` は D02 の ID 型であり、採番は第6.6節が正本である。

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
- 段階2の5部品はいずれも NumPy を使わない【提案】。指標部品を足す段階3 でも使わない（第4.5節の末尾）。窓の大きい指標を足す段階4 以降で改めて判断する（D01 §5.1 の条件は既に確定）。

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

**段階3（v2.0）でこの表を改訂する行**【提案】＋【合意済み】（Q13 決定、選択肢1）。段階2 で「使わない」としていた4件が段階3 で使われ、そのうち2件は部品が返す型と配送される型が分かれる。第4.2節の表そのものは置き換えず、次の差分を重ねる。

| データ型 | 部品が返す型 | 配送される型（`OutputRecord.payload`） | 段階3 |
|---|---|---|---|
| `condition_state@v1` | `ConditionState` | `Observation[ConditionState]`（Q13 決定） | 使う |
| `market_permission@v1` | `MarketPermission` | `Observation[MarketPermission]`【合意済み】上位 §4.3.15 | 使う |
| `confirmation_result@v1` | `ConfirmationOutcome`（成否と根拠値だけ） | `ConfirmationResult` | 使う |
| `price@v1` | `Price` | `Observation[Price]`（Q13 決定） | 使う |
| `price_offset@v1` | `PriceOffset` | `Observation[PriceOffset]`（Q13 決定） | `atr` を作る場合に使う |
| `management_action@v1` | `ManagementAction`（`UpdateStop` を足した3区分） | 同左 | 使う |

**`ManagementAction` に `UpdateStop(stop_loss: Price)` を足す**【提案】。検証戦略 B の「1時間足で管理水準を更新する」にはトレーリングが要り、D04 §11.2 は `UPDATE_STOP` を段階3 と定めている【合意済み】。水準は解決済みの絶対価格で渡し、距離型は使わない【合意済み】上位 §4.7.1・§4.7.3。D04 §11.2 への改訂依頼として第12.1節に挙げる。要求の実行意味論（どの執行足から有効か、同じ判断時点の決済要求との競合）は D06（第12節）。

**`Observation` で包む範囲**【合意済み】（Q13 決定、選択肢1）。上位設計書 §4.3.15 は `Observation[T]`（`value` / `subject` / `observation_interval` / `freshness_time`）を確定しており、「市場の状態は `OutputRecord[Observation[MarketPermission]]` とする。イベントまで一律に `Observation` で包む必要はない」と書いている。段階3 は**`VALUE` の出力をすべて包む**（第6.7節に理由を書く）。包むのは**ランタイム**であり、部品は内容だけを返す（第4.2節の取引機会と同じ手法）。`EVENT` と `COMMAND` の出力は包まない。

| `Observation` のフィールド | 誰が決めるか |
|---|---|
| `value` | 部品 |
| `subject` | ランタイム（その評価の対象区間を与えた足の `BarKey`） |
| `observation_interval` | ランタイム（その評価の `target_interval`） |
| `freshness_time` | ランタイム（その評価が読んだ入力の鮮度基準時刻の最小値。市場データを1つも読まない評価では `decision_time`） |

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

- **部品は、上の層の型を「構造だけを述べる型」として受け取る**【提案】（v1.3、2026-09-21 の人間の決定。PR #18）。渡すもの自体の定義は本節と第6.3節のままで、**置き場所だけ**を次のとおり確定する。`ResolvedInputs` の実体は `runtime`、`ResolvedParameter` の実体は `compiler` にあり（第3節）、どちらも部品を置く `catalog` より**上の層**である。部品の実装がこれらの型を直接 import すると、D01 §3.3 の層順序（契約 L2b）に違反する。そこで `catalog` 側に**構造だけを述べる型（Python の `Protocol`）**を置き、上の層の具体型がそれを構造的に満たす形にする。型そのものの正本は引き続き本書（第3節）であり、`catalog` 側に型の定義を二重に置くわけではない。
  - 同じ理由が、固定リスクリワード比の利確が読む建玉の情報（`position_context@v1`）にも当てはまる。その payload の項目を定めるのは D06 であり（第4.2節）、段階2 の `strategy` にその型は無い。部品が必要とする3項目（方向・約定価格・有効な損切り水準）だけを構造として要求する。
  - **不採用**: 部品が `runtime` / `compiler` の型を直接 import する案（層順序に違反し、`import-linter` の契約で落ちる）、`ResolvedInputs` と `ResolvedParameter` を `catalog` か `declarations` へ下ろす案（ランタイムの実行時の型を宣言の層へ持ち込むことになり、第3節の置き場所を作り直す必要がある）。

### 4.5 段階3 の指標部品（EMA・ATR）【提案】＋【合意済み】（Q11・Q12 決定、いずれも選択肢1）

検証戦略 B（上位設計書 §7.1）は「日足終値と日足 EMA から市場状態を作る」「15分足終値と15分足 EMA で確認する」ため、**指数移動平均（EMA）が必須**である。真の値幅の平均（ATR）は戦略 B に現れないが、全体計画 §5.3.3 の初版カタログに挙がっている。作る範囲は Q12 の決定（選択肢1）により、検証戦略 B に必要な部品と基本合成に `atr` を加えた計11件とする（第15節）。

**(6) `ema` v1**（指数移動平均）

| 項目 | 内容 |
|---|---|
| 入力 | `prices`: `price@v1` / `VALUE` / `HistoryWindow(BarsWindow(ParameterRef("window_bars")), max_age=None, on_missing=SkipEvaluation, exclude_latest_bars=0)` / `arity=(1,1)` |
| 出力 | `value`: `price@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `period`: `INT` / `BARS` / `[2, 500]`、`window_bars`: `INT` / `BARS` / `[4, 2000]` |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | 平滑化係数 `alpha = 2 / (period + 1)`。窓の**古い側の `period` 本の単純平均**を種 `e` とし、残りの足について古い順に `e = alpha * x + (1 - alpha) * e` を適用し、最後の `e` を返す。`window_bars >= 2 * period` を要求する（次段落） |

**パラメータどうしの関係はコンパイル時に検査する**【提案】。`window_bars >= 2 * period` は契約の構築時には確かめられない。どちらも使用箇所が値を選ぶパラメータであり（D04 §7）、契約は各パラメータの範囲しか持たないからである。種を作る `period` 本のあとに平滑化する足が1本も残らない設定を通すと、EMA が単純移動平均に化けたまま動く。第5.6節の新しい検査 e として、コンパイラのパラメータ解決の段（第5.1節 段3）で確かめる。同じ検査を `atr` にも使う。

**状態を持たせず、毎回の評価を窓だけで決める**【合意済み】（Q11 決定、選択肢1）。状態に前回値を積み上げると、**同じ対象区間の答えが run の開始位置に依存する**（run を1本早く始めると種が変わる）。上位設計書 §4.3.14 は「EMA 等の状態付き Feature は対象区間に対応する履歴出力を参照または再現し、現在の最新キャッシュを過去区間の答えとして使わない」と定めており、待機からの再開（第6.8節）で対象区間を固定して読み直す規則とも噛み合う。窓だけで決めれば、再開時に同じ窓を読み直すだけで同じ値が出る。ウォームアップ不足は履歴読み取りの `WARMUP_INSUFFICIENT`（D03 §6.2）として現れ、段階2 と同じ規則で見送りになる（`WarmupSpec` を使わずに済む。第11節の2）。**不採用**: 状態に前回値を持って再帰更新する案（run の開始位置で値が変わり、待機からの再開で状態を巻き戻す必要が出る）、両方を別の契約として登録する案（同じ指標に2つの計算規則が並び、実験の同一性の議論が二重になる）。

**(7) `atr` v1**（真の値幅の平均。Q12 決定により段階3 で作る）

| 項目 | 内容 |
|---|---|
| 入力 | `highs` / `lows` / `closes`: いずれも `price@v1` / `VALUE` / `HistoryWindow(BarsWindow(ParameterRef("window_bars")), max_age=None, on_missing=SkipEvaluation, exclude_latest_bars=0)` / `arity=(1,1)` |
| 出力 | `value`: `price_offset@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `period`: `INT` / `BARS` / `[2, 500]`、`window_bars`: `INT` / `BARS` / `[4, 2000]` |
| 状態 | なし |
| 時刻制約 | `TemporalConstraints(warmup=None, alignment=(AlignmentRequirement(("closes","highs","lows"), SAME_OBSERVATION_INTERVAL),))` |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | 各足の真の値幅を `max(high - low, abs(high - prev_close), abs(low - prev_close))`（`PriceOffset`）とし、古い側の `period` 本の単純平均を種にして `e = (e * (period - 1) + tr) / period`（Wilder の平滑）を古い順に適用する。窓の先頭の足は前足の終値が無いため真の値幅を計算せず、2本目から数える |

`atr` は**観測区間の一致（`AlignmentRequirement`）を実際に使う唯一の段階3 部品**である。3本の窓が別々の系列や別々の区間から来ると、真の値幅が別の足の高値と安値から計算される。守らせる仕組みは第6.7節に置く。

**数値の扱い**【提案】。`alpha` や Wilder の平滑は割り切れないため、**部品内部の計算は有効桁28・`ROUND_HALF_EVEN` を明示した局所的な `Decimal` の文脈**で行い、処理系やプロセスの既定文脈に依存させない（D02 §4.1 が定めた「Decimal の文脈に依存しない」の実施）。出力が `Price` の部品は D02 §4.3 の構築規則に従い、**銘柄の価格刻みへの丸めは行わない**（丸めは D06 の責務。第1.2節の行7）。**不採用**: `float` で計算して最後に `Decimal` へ直す案（ADR-0012 と D02 §4.6 に反する）、既定文脈のまま計算する案（同じ入力から別の値が出る経路が残る）。

`ema` と `atr` は NumPy を使わない【提案】。窓は最大でも2000本であり、`Decimal` の逐次計算のほうが D01 §5.1 の条件（`ndarray` を出力・状態・判断履歴へ出さない。ADR-0021）を守りやすい。NumPy を使う部品の特定は、窓の大きい指標を足す段階4 以降で改めて判断する。

### 4.6 段階3 の条件部品（価格比較・論理合成・遷移検出）【提案】＋【合意済み】（Q12 決定、選択肢1）

**(8) `price_compare` v1**（価格比較）

| 項目 | 内容 |
|---|---|
| 入力 | `left` / `right`: いずれも `price@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(1,1)` |
| 出力 | `condition`: `condition_state@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `operator`: `STR` / 単位なし / `allowed_values=("GT","GE","LT","LE")` |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | `ConditionState(left <operator> right)`。比較は `Price` 同士のみ（D02 §4.3）。等値は `GE` / `LE` が含み、`GT` / `LT` が含まない |

読み取り条件（`max_age` と `on_missing`）は `InputSpec` だけが持ち、使用箇所は選べない【合意済み】D04 §4.1・§6.1。鮮度の上限や待機を使う戦略は、**同じ実装を別の読み取り条件で登録した契約の版**（`price_compare` v2 など）を使う。検証戦略 B が待機を必要とするのは日足を直接読む入力であり（第9.2節）、比較部品ではない。

**(9) `all_conditions` v1 / (10) `any_condition` v1**（AND / OR）

| 項目 | 内容 |
|---|---|
| 入力 | `conditions`: `condition_state@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(2, None)` |
| 出力 | `condition`: `condition_state@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | なし |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | `all_conditions` は接続された全要素の `satisfied` の論理積、`any_condition` は論理和 |

**欠損を `False` に変換しない**【合意済み】上位 §4.3.15。1件でも欠損すれば `on_missing` に従い、評価そのものを行わない。`arity=(2, None)` の可変個数入力は `InputBinding.sources` の並びを保つ（D04 §3 の表。並べ替えない）。論理積と論理和は並びに依存しないが、**判断履歴で「どの条件が偽だったか」を接続の並びから読むために**並びを崩さない規則をここでも使う。

**(11) `condition_transition` v1**（遷移検出）

| 項目 | 内容 |
|---|---|
| 入力 | `condition`: `condition_state@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(1,1)` |
| 出力 | `condition`: `condition_state@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `edge`: `STR` / 単位なし / `allowed_values=("RISING","FALLING")` |
| 状態 | `StateSpec(condition_state@v1, LiteralInitialState({"satisfied": BoolValue(False)}), reset_on=(RUN_START,))` |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | `RISING` は「直前が不成立で今回が成立」、`FALLING` は「直前が成立で今回が不成立」のときだけ `ConditionState(True)` を返し、それ以外は `ConditionState(False)` を返す。新しい状態は今回の入力の `ConditionState` |

**取引機会を出す部品の再武装（`EDGE`）と、この遷移検出は別物である**【提案】。再武装は「同じ条件で何度発火するか」を取引機会を出す出力仕様が決める規則（D04 §10.4）、遷移検出は「条件そのものを遷移の条件へ変換する」部品である。遷移検出は取引機会を出さないので `retrigger_mode` を持たない（D04 §12 #6b）。両方を同じ戦略に書けるが、意味は重ならない。

**N 本継続（`sustained_condition`）と「A の後 N 本以内の B」（`within_bars`）は段階3 では作らない**【合意済み】（Q12 決定、選択肢1）。どちらも経過本数を数える状態が要り、`condition_state@v1` では表せないため、**新しいデータ型（経過本数を持つ状態）を D04 §5 のレジストリへ足すことになる**。検証戦略 B はどちらも使わず、段階3 の完了条件にも現れない。上位設計書 §4.6 が「段階的に用意する」と書いているとおり、必要になった段階で足す。

**上流の出力を履歴窓で読む仕組みを段階3 で作っても、この2部品は段階3 では作らない**【合意済み】（Q18 決定、選択肢2 と Q12 決定、選択肢1 の両立）。Q18 の決定により、他の部品の出力を過去 N 本で読む接続は段階3 で使えるようになる（第6.12節）ので、N 本継続は「条件の出力を N 本の履歴窓で読んで全件が成立しているかを見る」形にすれば**新しいデータ型も状態も要らずに書ける**。それでも段階3 で作らないのは、Q12 の決定が「検証戦略 B に必要な部品＋基本合成3件＋ATR の計11件」に範囲を限っており、その決定を Q18 が覆すものではないからである。**段階3 で作るのは仕組みだけ**であり、その仕組みを使う部品を足すのは段階4 以降になる（第10.2節）。

### 4.7 段階3 の市場状態部品【提案】

**(12) `permission_from_condition` v1**（条件から取引許可への変換）

| 項目 | 内容 |
|---|---|
| 入力 | `long_allowed` / `short_allowed`: いずれも `condition_state@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(1,1)` |
| 出力 | `permission`: `market_permission@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | なし |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | `MarketPermission(allow_long=long_allowed.satisfied, allow_short=short_allowed.satisfied)` |

**(13) `constant_condition` v1**（固定値の条件）

| 項目 | 内容 |
|---|---|
| 入力 | なし |
| 出力 | `condition`: `condition_state@v1` / `VALUE` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `value`: `BOOL` / 単位なし |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | `ConditionState(value)` |

上位設計書 §4.3.6 は「日足終値と EMA を受け取り、直接 `MarketPermission` を返す部品を実装してもよい」としている。段階3 は**変換部品を1つだけ置き、条件の作り方は `price_compare` と論理合成に任せる**【提案】。検証戦略 B の「日足終値が日足 EMA を上回るなら買い許可」は、`price_compare(GT)` の出力を `long_allowed` に、`constant_condition(value=False)` の出力を `short_allowed` に接続して表す。**不採用**: 価格を直接受け取る専用の市場状態部品を置く案（比較の規則が比較部品と市場状態部品の2か所に分かれる）、`short_allowed` を省略可にする案（省略時の既定が暗黙の許可または暗黙の禁止になり、ADR-0031 の「暗黙の既定値を設けない」と揃わない）、片方向だけの `MarketPermission` を表す区分を足す案（上位設計書 §4.3.15 が確定した2フィールドを変えることになる）。

入力を1つも持たない部品（`constant_condition`）は、依存グラフで入る辺を持たないため評価順の先頭の段に来る（第5.4節の段の作り方）。**銘柄は使用箇所の起動条件（`OnBarClose.series`）から伝播する**（D04 §5 の供給元2）ので、「市場データ参照を1つも辿れない使用箇所」にはならない。起動条件を1件も持たない使用箇所は宣言できない（`EvaluationSchedule.triggers` は1件以上、D04 §8）ため、銘柄なしにはならない。

### 4.8 段階3 の確認部品（ExecutionFilter）【提案】

**(14) `condition_filter` v1**（条件の成立による後続確認）

| 項目 | 内容 |
|---|---|
| 入力 | `condition`: `condition_state@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(1,1)`、`opportunity`: `opportunity@v1` / `VALUE` / `CurrentContext` / `arity=(1,1)`（第6.11節） |
| 出力 | `confirmation`: `confirmation_result@v1` / `EVENT` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | `include_start_bar`: `BOOL` / 単位なし |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | `ConfirmationOutcome(confirmed=condition.satisfied, reference_values={})` を返す。識別子と確認足の区間はランタイムが付ける（次段落） |

**部品は確認の成否だけを返し、ランタイムが `ConfirmationResult` を組み立てる**【提案】。`ConfirmationResult`（上位設計書 §4.3.15）の3フィールドのうち、部品が計算できるのは `confirmed` だけである。`opportunity_id` は評価要求が指す機会（第6.11節）、`confirmation_interval` はその確認評価の対象区間であり、どちらもランタイムが持っている。第4.2節で取引機会について決めた切り分け（部品は `OpportunityContent` を返し、ランタイムが識別子・銘柄・対象区間を付ける）と**同じ手法**である。

| `ConfirmationResult` のフィールド | 誰が決めるか |
|---|---|
| `opportunity_id` | ランタイム（評価要求の `opportunity_id`。第6.11節） |
| `confirmation_interval` | ランタイム（その評価の `target_interval`） |
| `confirmed` | 部品（`ConfirmationOutcome.confirmed`） |

**不採用**: 部品が3つとも作る案（部品が機会の識別子を書けるようになり、別の機会の確認結果を作れてしまう）、`ConfirmationResult` を配送用と部品の戻り値用の2つの型へ分ける案（上位設計書 §4.3.15 が確定したフィールド構成を変えることになる）。

**`include_start_bar` は契約のパラメータに置き、使用箇所が値を明示する**【合意済み】全体計画 §5.3.3・上位設計書 §4.3.13。`EntryPolicy` には同じ設定を置かない。コンパイラは、`execution_filter` 役割に接続された出力を持つ使用箇所の契約が `include_start_bar`（`BOOL`、既定値なし）を持つことを検査し、解決済みの値を `ConfirmationPlan`（第5.3節）へ載せる。持たない契約を `execution_filter` に接続した宣言は `ROLE_MISMATCH` で拒否する（第5.2節）。

`condition_filter` は `confirmed=False` も**出力として出す**【提案】。上位設計書 §4.3.15 は「`ConfirmationResult` の `False` は条件未成立であり、入力不足・期限切れ・追い越しと区別する」と定めており、未成立を出力として残さないと4者を判断履歴で区別できない。対応は次のとおりである。

| 何が起きたか | 判断履歴のどこに出るか |
|---|---|
| 条件が成立しなかった | `OutputRecord[ConfirmationResult]`（`confirmed=False`）と `ConfirmationAttempt(NOT_CONFIRMED)` |
| 入力が足りなかった | 評価記録の `Skipped` と `ConfirmationAttempt(SKIPPED)` |
| 確認期限に到達した | 取引機会の遷移11（`EXPIRED`） |
| 確認足が追い越された | 評価記録の `Superseded` と `ConfirmationAttempt(SUPERSEDED)`（第6.10節） |

### 4.9 検証戦略 B の突破 Trigger について【提案】

検証戦略 B の「1時間足の終値と、利用可能な日足高値から Trigger を作る」は、**段階2 の `breakout_trigger` v1 をそのまま使える**【提案】。この契約の `level` 入力は `price@v1` を `LatestAvailable` で読むだけであり、接続元が同じ1時間足でも日足でもよい。使用箇所が `level` に `MarketDataRef(USDJPY/1d/bid, HIGH)` を接続すれば「利用可能な日足高値」になる。新しい契約を作らない。

ただし**複数の時間足をまたぐ読み取りになるため、鮮度の扱いが段階2 と変わる**。日足が未到着のまま1時間足だけが確定した場合、`latest_available` は `LATEST_BAR_UNAVAILABLE` を返し（D03 §6.2。古い足へ黙って戻らない）、使用箇所が宣言した `on_missing` に従う。段階2 の `breakout_trigger` v1 は `on_missing=SkipEvaluation` を契約に書いているため、**待機を使う戦略 B では `on_missing=WaitForInput(...)` を持つ版（`breakout_trigger` v2）を登録する**（第9.2節）。読み取り条件は契約が固定するので（D04 §4.1）、版を分けるほかない。v1 は段階2 の検証戦略 A のために残す。

### 4.10 段階3 のトレーリング Exit【提案】＋【合意済み】（Q12・Q14 決定、いずれも選択肢1）

**(16) `trailing_stop` v1**（追従する損切り水準の更新）

| 項目 | 内容 |
|---|---|
| 入力 | `position`: `position_context@v1` / `VALUE` / `CurrentContext` / `arity=(1,1)`、`level`: `price@v1` / `VALUE` / `LatestAvailable(max_age=None, on_missing=SkipEvaluation)` / `arity=(1,1)` |
| 出力 | `action`: `management_action@v1` / `COMMAND` / `reference_schema={}` / `retrigger_mode=None` |
| パラメータ | なし |
| 状態 | なし |
| 許可する起動条件 | `AllowedBarClose(timeframes=None)` |
| 計算規則 | 建玉の方向が `LONG` なら、`level` が現在の有効な損切り水準（`PositionContext.effective_stop_loss`、D06 §8.4）**より高い**ときだけ `UpdateStop(level)` を返す。`SHORT` なら `level` が現在の水準**より低い**ときだけ返す。条件を満たさない評価では**出力を出さない**（第4.1節の「出さなかった出力は、今回の評価では発生しなかったことを意味する」） |

**損切りを不利な向きへ動かさないことを部品側で保証する**【提案】。水準を緩める更新はリスク管理の前提を崩す。受付側（D06）が向きを検査しても「宣言として不正」（`PROTECTION_INVALID`）としか記録できず、戦略の意図なのか誤りなのかが判断履歴から読めない。部品が返さなければ「その足では更新がなかった」として残る。**不採用**: 常に `UpdateStop(level)` を返して受付側に任せる案（同じ規則が戦略側と受付側に割れる）、緩める方向も許す設定を置く案（段階3 で使わない設定が増える）。

この部品は**建玉が存在する足の確定でだけ評価される**。その起動の作り方は第6.11節（Q14 決定、選択肢1）による。`ManagementAction` に `UpdateStop` を足す改訂は第4.2節に書き、D04 §11.2 への改訂依頼として第12.1節に挙げる。適用の意味論（どの執行足から有効か、同じ判断時点の決済要求との競合）は D06 の責務である（D06 §8.3。段階3 は D06 v0.3）。

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
| `UNSUPPORTED_CONFIGURATION` | 9 | `RuntimeInputRef(PENDING_ORDER)`、15m より細かい足 ほか（段階2 では `AwaitConfirmation` と**出力参照（`OutputRef`）を履歴窓（`HistoryWindow`）で読む接続**もここで拒否したが、どちらも段階3 で解除する。第5.6節） |

各 `CompileError` は `check_id`（D04 §12 の番号、例 `"#6b"`）と `location`（使用箇所とフィールド経路）を必ず持ち、原因の宣言を指させる。**黙って無視する経路を作らない**【合意済み】D04 §12。

D04 §12 は「拒否は `ReasonCode`（D02 §8.1）付きの構造エラー」としているが、D02 §8.1 の語彙は注文の受付・評価の理由であり、コンパイル拒否に対応する語が無い。**コンパイラ専用の区分 `CompileRejection` を使い、共通の理由コードは実行時に限る**（Q9 決定）。コンパイルは run の前に終わる処理で、理由コードの主な使用箇所（受付前拒否・評価見送り）とは対象が異なり、実行時の集計に設計ミスの分類が混ざらない。D04 §12 の文言をこの決定に合わせる改訂は、D04 の次回改訂で行う（第12節）。**不採用**: 共通の理由コードへ拒否用の語を加える案（注文・評価の集計に設計ミスが混ざる）、既存の `DATA_ERROR` を流用する案（データの問題と宣言の問題を区別できない）。

### 5.3 コンパイル結果【提案】

`CompiledStrategy` は不変で、内容ハッシュ（`compiled_ref`）を持つ【合意済み】全体計画 §5.3.4。ランタイムは**この型だけを読み、`StrategyDefinition` を再解釈しない**。宣言の解釈を2か所に置かないためである。

- `components` は `evaluation_order` と同じ並びで保持する（並びが2つあると食い違う）。`evaluation_order` は `instance_id` の列で、第5.4節の規則で一意に定まる。
- `InputPlan.resolved_window` は `ParameterRef` を解決した後の窓で、ランタイムはこれをそのまま `MarketDataView.history` へ渡す。
- `CompiledStrategy.symbol` は第5.1節 段4 で伝播させた単一銘柄。
- `CompiledRoles.trigger` / `order` / `protection` は段階2で必須、`market_state` / `execution_filter` / `exit` は `None` を許す（検証戦略 A は `exit` を持つ）。
- **`CompiledStrategy.output_retention` は、上流の出力を履歴窓で読む接続から導いた保持本数の計画**である（第6.12節。Q18 決定、選択肢2）。ランタイムはこの1件だけを読んで何本ため込むかを決め、宣言と契約を実行時に読み直さない（本節の「ランタイムは `CompiledStrategy` だけを読む」）。読み手が1つも無ければ `by_output` は空で、段階2 の挙動（最新1件だけ保持）と一致する。

### 5.4 依存グラフと評価順【提案】

- 節点は使用箇所（`instance_id`）。辺は D04 §12 が確定した3種類（明示入力＋因果辺2本）で、`EdgeKind` で区別して保持する。因果辺も**種類を残したまま**保持するのは、循環が明示接続によるものか帰還路によるものかを `CompileError.message` で示すためである。
- 循環検出は3種の和の上で行う。閉路があれば `DEPENDENCY_CYCLE`。
- 評価順は**トポロジカル順、同順位は `instance_id` の Unicode コードポイント順**とする【提案】。**「同順位」とは同じ段のことである**（v1.3、2026-09-21 の人間の決定。PR #18）。段は Kahn 法で作る: 先に評価すべき相手がもう残っていない使用箇所を1つの段としてまとめ、その段を `instance_id` 順に並べてから次の段へ進む。これを繰り返して連結したものが評価順である。「同順位」を定義しないと、トポロジカル順の作り方（深さ優先か幅優先か）だけで並びが変わり、同じ宣言から別の通し番号が出る。同順位の並びを固定しないと、同じ宣言から出力の `sequence`（第6.6節）が変わり、「同一入力の再実行で trace が一致」（全体計画 §8.2）を満たせない。時間足の大小から順序を推測しない【合意済み】全体計画 §5.3.4 の6。
- 評価順は使用箇所の全件を含む（起動しない使用箇所も並びには含め、実行時に起動判定で落とす）。

### 5.5 ハッシュ計算【提案】

D04 §13.2 が決めた3つの対象を、D02 §9.3 の `canonical.digest` で計算する手順だけを定める。

1. `ContractRef.digest`: 契約から `implementation_ref` を除いた値を正規化エンコードして計算する。カタログ登録時に計算し、レジストリの鍵と一緒に保持する。
2. `StrategyRef.digest`: `StrategyDefinition` 全体。各 `ComponentInstance` が持つ `contract_ref` を通じて 1 を含む。
3. `CompiledStrategyRef.digest`: 解決済みパラメータ・評価順・各部品の `ImplementationRef` を含む。**`CompiledStrategy` 全体をそのまま対象にしない**のは、`strategy_ref` と `compiled_ref` 自身を含む自己参照になるためで、対象は `(strategy_ref, evaluation_order, 各 CompiledComponent の (instance_id, contract_ref, implementation_ref, parameters, input_plans, triggers))` とする。
4. 順序に意味を持たせないコレクションは D04 §3 の表に従って構築時に正規化済みであり、ここで並べ替えを重複実装しない【合意済み】D04 §13.2。
5. `ImplementationRef.digest`: **宣言した実装識別子（`implementation_id`）と改訂番号（`revision`）の2項目だけ**を対象に `canonical.digest` で計算する（v1.3、2026-09-21 の人間の決定。PR #18。規則の正本は D02 §9.2）。カタログ登録時に計算し、登録の一部として保持する。実装のソース内容を読まないのは `catalog` が I/O を持てないためであり（D01 §5）、run 全体のソース内容は `CodeDigest`（D02 §9.4）が別に識別している。したがって **`revision` は実装の計算規則を変えたら必ず上げる**という運用上の約束と対になる。上げ忘れると、計算規則が変わったのに 3 の解決済み設定の指紋が同じままになる。

**期間値を含む宣言は、現状どの指紋も計算できない**（v1.3、同じ決定）。正規化エンコード（D02 §9.3 v1.6）は期間（`timedelta`）を符号化せず、`KernelValueError` になる。対象は `InputReadSpec.max_age`・`DurationWindow.duration`・`DurationDeadline.duration` を持つ宣言である。符号化規則を足すかどうかは D02 の次回改訂へ引き渡してある（D04 §13.2 の注記）。段階2の検証戦略 A（第9節）はいずれの期間値も持たないため、実行経路には現れない。

### 5.6 段階3 で解除する能力検査と、新しく行う検査【提案】

D04 §12 の「段階2 で拒否する構成」のうち、**段階3 で解除するもの・解除しないもの**を区別する。解除とは能力検査（第5.1節 段9）から外すことであり、D04 の宣言型は変わらない。

| 段階2 で拒否していた構成 | 段階3 | 理由 |
|---|---|---|
| `AwaitConfirmation`（確認待ちの発注方針） | **解除** | 確認評価の起動・開始足の決め方・`CONFIRMED` から先の経路を第7.7節が確定する。**期限をどの系列の確定足で数えるかだけは未決**（第15節 Q19）であり、その一点が決まるまで期限の本数の数え方は実装しない |
| `execution_filter` が `None` でない戦略 | **解除** | 同上 |
| `MissingInputPolicy` の `WAIT_FOR_INPUT` / `USE_PREVIOUS` | **解除** | 意味論と宣言形を第6.8節・第6.9節が確定する（D04 への追加依頼と同時） |
| 発注要求まで有効であり続けることを求める束縛（`REQUIRE_UNTIL_ORDER_REQUEST`）に `Error` を書いた宣言 | **解除** | D04 §6.3 が「再検査の結果そのものを残す記録型を D05 v0.2 で足し、失敗の書き先ができてから解除する」と定めた。第7.7節の確認試行の記録（`ConfirmationAttempt`）と、再検査の結果を残す遷移記録がその書き先になる |
| 空でない `TemporalConstraints`（ウォームアップ本数・観測区間の一致） | **一部を解除** | 観測区間の一致（`AlignmentRequirement`）は守らせる仕組みを第6.7節が足すので解除する。**ウォームアップ本数（`WarmupSpec`）は拒否を続ける**（第11節の2。段階3 の指標部品は履歴窓の不足で成立し、`WarmupSpec.series` を再利用可能な契約に書けない問題が残るため） |
| 出力参照（`OutputRef`）を履歴窓（`HistoryWindow`）で読む接続 | **本数で数える窓（`BarsWindow`）だけ解除**（Q18 決定、選択肢2） | 保持する本数と捨てる時点の規則を第6.12節が確定する。**経過時間の窓（`DurationWindow`）は拒否を続ける**。保持の上限は「接続が要求する最大の窓」から導く規則にしたが、経過時間の窓は上流の出力がどの間隔で出るかが宣言から分からず、コンパイル時に本数へ直せない（第6.12節） |
| `RuntimeInputRef(PENDING_ORDER)` | **拒否を続ける** | 未約定注文は戦略部品へ公開しない【合意済み】上位 §4.3.14・D06 §8.4 |
| `POSITION_OPENED` 以外の `RuntimeEventKind` | **拒否を続ける** | 検証戦略 B は決済通知・保護水準の更新通知で評価を起動しない。トレーリングは足の確定で起動する（第6.11節） |
| 複数銘柄に跨る使用箇所 / 15m より細かい足 / 距離型 SL / 指値 / 再審査設定 | **拒否を続ける** | いずれも段階6・D10【合意済み】D04 §15 |
| `UPDATE_STOP` | **解除** | 第4.2節・第4.10節（D04 §11.2 への追加依頼と同時） |

段階3 で**新しく必要になる検査**は次の6件である【提案】。**検査項目そのものと「その検査が読む宣言」の正本は D04 §12 であり（第1.1節・第1.2節の行3）、本節はそれを確定するものではない**。ここに挙げるのは D04 §12 への**改訂提案**であり、6件は第12.1節の依頼7 として D04 の改訂で確定する。**本書が決めるのは、確定後の6件をコンパイラのどの段で実行し、失敗をどの拒否の区分で返すか**（第5.1節・第5.2節）だけである。この分担を崩して本書側で検査を確定させると、正本の検査一覧に載っていない検査が生まれ、D04 §12 だけを読んで作ったコンパイラがそれらを実施しない。**D04 の改訂が入るまでは、この6件はコンパイラに実装しない**（本 PR で D04 v1.9 へ反映済みである）。起草時は5件だったが、Q18 の決定（選択肢2）により検査 f が加わった。

| # | 検査（D04 §12 への改訂提案） | 検査が読む宣言（D04 §12 の第3列へ足す内容） | 本書が決める拒否の区分 |
|---|---|---|---|
| a | `execution_filter` 役割に接続された出力を持つ使用箇所の契約が `include_start_bar`（`BOOL`、既定値なし）を持ち、使用箇所がその値を明示していること | 役割フィールド、`ComponentContract.parameters`、`ComponentInstance.parameters` | `ROLE_MISMATCH` |
| b | `execution_filter` 役割の使用箇所が `OnBarClose` の起動条件を1件以上持ち、**その系列がただ1つ**であること（確認足の系列が定まらないと期限も開始足も決まらない） | `EvaluationSchedule.triggers` | `SCHEDULE_NOT_ALLOWED` |
| c | `WaitForInput` / `UsePrevious` を書いた入力の読み方が許可された組合せであること（待機は `LatestAvailable` と `HistoryWindow`、遡りは **`LatestAvailable` のみ**。上位 §4.3.13） | `InputSpec.read_spec`、`MissingInputPolicy` | `UNSUPPORTED_CONFIGURATION` |
| d | `market_state` 役割がある戦略で、取引機会を出す使用箇所と市場状態の使用箇所のあいだに**エンジン上の因果辺**を1本引き、その辺を含めて循環を検出すること（第7.6節） | 役割フィールド、`OutputSpec.data_type` | `DEPENDENCY_CYCLE` |
| e | パラメータどうしの関係（`ema` と `atr` の `window_bars >= 2 * period`）が成り立つこと。契約は各パラメータの範囲しか持てず、関係は解決済みの値でしか確かめられない（第4.5節） | `ComponentInstance.parameters`、`ParameterSpec.bounds` | `PARAMETER_INVALID` |
| f | 出力参照（`OutputRef`）を履歴窓（`HistoryWindow`）で読む接続の窓が**本数で数える窓（`BarsWindow`）**であり、解決済みの本数が 1 以上であること（第6.12節。Q18 決定、選択肢2） | `InputSpec.read_spec`（`HistoryWindow.window`）、`InputBinding.sources`、`ParameterRef` の解決結果 | `UNSUPPORTED_CONFIGURATION` |

**`CompiledRoles` に確認の計画を載せる**【提案】（第5.3節の改訂）。`CompiledRoles` に `confirmation: ConfirmationPlan | None` を足し、コンパイラが検査 a・b で読んだ値（確認部品の使用箇所、確認足の系列、`include_start_bar`、`AwaitConfirmation` の期限と期限切れの動作）を1つのレコードにまとめる。ランタイムは**この1件だけを読んで確認を制御し、`StrategyDefinition` と契約を実行時に読み直さない**（第5.3節の「ランタイムは `CompiledStrategy` だけを読む」）。`execution_filter` が `None` の戦略では `confirmation=None` であり、`entry_policy` は `ImmediateEntry` でなければならない【合意済み】D04 §10.1。**不採用**: ランタイムが実行時に契約のパラメータから `include_start_bar` を引く案（コンパイル結果だけを読む規則が崩れる）、`ConfirmationPlan` を `EntryPolicy` に持たせる案（上位設計書 §4.3.13 の「`EntryPolicy` に同じ設定を重複して置かない」に反する）。

**`CompiledStrategy` に出力の保持本数の計画を載せる**【提案】（第5.3節の改訂。Q18 決定、選択肢2）。コンパイラは第5.1節 段5（型と接続の検査）で読んだ接続から `OutputRetentionPlan` を組み立て、段10 で `CompiledStrategy` に載せる。導き方は第6.12節が定める。ランタイムはこの1件だけを読んで何本ため込むかを決める。**不採用**: ランタイムが実行時に `InputPlan` をたどって窓の本数を集める案（同じ導出がコンパイラとランタイムの2か所に分かれ、片方だけ直すと保持本数と読み取り本数が食い違う）、保持本数を戦略の宣言に書かせる案（読み手の窓から一意に決まる値を人が二重に書くことになり、食い違ったときにどちらが正しいか決まらない）。

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

取引機会の `signal_interval` は `target_interval` である（第4.2節）。`opportunity` 出力を持つ契約が実行時イベントで起動する宣言は、対象区間を決められないためコンパイル時に拒否する（第5.2節の `OUTPUT_SPEC_INVALID`）。**「対象区間を持たない」性質は入力イベントの連鎖を伝って下流へ伝わる**【提案】（v1.3）。入力イベントによる起動は上流の要求から対象区間を引き継ぐため、上流をたどって実行時イベントに行き着く起動も同じく拒否する。直近の起動条件の種類だけを見ると、宣言は通るのに取引機会を作る評価がすべて失敗する構成が残る。`opportunity_id` を引き継ぐのは、同じ取引機会から出た注文意図と保護水準を手順7で組にするためであり、引き継がなければ同時に複数の機会が進んだときに組が作れない。

**足の確定どうしは、対象区間が同じものだけを1件に集約する**【合意済み】（Q6 決定、選択肢1）。集約した要求は成立した起動条件名をすべて `trigger_names` に持ち、必須入力は和集合とする（第6.3節）。同じ論理確認を二重実行しない（上位設計書 §4.3.12）。**不採用**: 足の確定ごとに1回ずつ評価する案（同じ足で機会が重複しうる）、宣言でどちらかを選べるようにする案（段階2で使わない設定が増える）。1つの使用箇所に1時間足と15分足の `OnBarClose` を宣言し、両方が同じ判断時点で確定すると `BarClosure.interval` が2つになる。区間の違う起動をまとめると取引機会の対象区間が実装依存になるため、**区間ごとに別の要求**を作る。段階2の検証戦略 A は1系列のため要求は常に1件である。
4. **依存順評価**: `evaluation_order` に従い、起動した使用箇所だけを評価する。上流の更新だけで下流を自動評価しない【合意済み】上位 §4.3.2。
5. **入力解決**（第6.3節）→ **部品の呼び出し** → **戻り値の検査**（次の表）。
6. **取引機会の組み立てと同時保持の判定**: 取引機会を出す出力が出たら、`OpportunityId` を採番して `Opportunity` を組み立て（第4.2節）、第7.4節の判定を行う。有効性の再検査は第7.3節。
7. **出力の付番と送出**: `OutputRecord` を作り（第6.6節）`OutputSink.emit` へ渡し、状態を更新する（第6.5節）。
8. **下流への配送**: `EVENT` 出力を、購読する入力へ配送する。**終端した取引機会は配送しない**（次段落）。
9. **役割出力の取り出し**: `roles.order` と `roles.protection` の出力が**同じ `opportunity_id` の要求から**揃った時点で `EntryProposal` を1件作る（P5）。`roles.exit` の出力は、その評価要求の `position_id` を宛先として `ManagementRequest` にする。**このとき、根拠になった出力の `OutputId` を戻り値へ載せる**【提案】（v1.2）。`EntryProposal.intent_output_id` には `roles.order` の出力の `OutputId`、`EntryProposal.protection_output_id` には `roles.protection` の出力の `OutputId`、`ManagementRequest.source_output_id` には `roles.exit` の出力の `OutputId` を入れる。いずれも手順7 で採番済みの値であり（第6.6節）、ここで新たに採番しない。載せる理由は、上位設計書 §4.7.8 が「注文意図・損切り算定について、使った出力 ID を記録する」ことを要求しており、**受け取る側（D06 §6.1）がこの識別子を持たないと正常経路でも注文要求を組み立てられない**ためである。**不採用**: 判断履歴に保存した出力記録と評価記録を後から突き合わせて復元する案（同じ判断時点に同じ役割の出力が2件出ると、取引機会の識別子だけでは一意に決まらない）。**宛先の建玉が無い保有管理の要求は構造エラーで止める**【提案】（v1.3、2026-09-21 の人間の決定。PR #18）。`roles.exit` の出力が出たのに評価要求の `position_id` が `None` である場合、`ManagementRequest` は宛先を持てない。段階2 の Exit は約定通知（`OnRuntimeEvent(POSITION_OPENED)`）でだけ起動し、通知が必ず建玉を運ぶ（第8節）ため、この状態はカタログの5部品では起こらない。起こったとすればコンパイラの検査か部品の宣言の誤りであり、**黙って捨てず `KernelValueError`（構造エラー）で止める**。**不採用**: 要求を黙って捨てる案（建玉に利確が付かないまま run が進み、原因が判断履歴に残らない）、評価記録の失敗として残す案（部品の計算ではなく宣言と検査の誤りであり、データ起因の失敗 `DATA_ERROR` と混ざる）。
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
| `HistoryWindow` | 市場データ参照なら `MarketDataView.history(series, resolved_window, decision_time, end_offset_bars=exclude_latest_bars)` が返す足の列を1本ずつ射影（次段落）、出力参照なら `RuntimeState.output_history` に保持した過去の `OutputRecord` の列（第6.12節。段階3 で解禁。Q18 決定） | `ValueWindow` |
| `DeliveredEvent` | 同じ `step` で配送された `OutputRecord` | `EventDelivery` |
| `CurrentContext` | `RuntimeContextView` を `decision_time` と評価要求の `position_id` で読む（`RuntimeTarget` が `POSITION` なら `position_context`、`ACCOUNT` なら `account_context`） | `ContextSnapshot` |

- **出力参照を履歴窓で読む接続は、段階2では能力検査で拒否する**【提案】。D04 §6.1 の許可表は `VALUE` の出力参照に `HistoryWindow` も許しているが、履歴を作るにはランタイムが過去の出力を窓の本数分ため込む必要があり、段階2が保持するのは最新1件だけである（第6.5節）。宣言としては通るのに評価できない構成を残さないため、コンパイル時に `UNSUPPORTED_CONFIGURATION` で拒否する（第5.2節）。検証戦略 A の履歴読み取りはすべて市場データ参照であり、この制限にかからない。**不採用**: 出力の履歴を段階2から保持する案（窓の本数分の記憶と、その打ち切り規則が必要になり、段階2で使わない仕組みが増える）、実行時に欠損として扱う案（宣言から評価できないことが読めない）。
- **段階3 は本数で数える窓に限って解除する**【合意済み】（Q18 決定、選択肢2）。2026-09-22 の人間の決定により、上流の出力を過去 N 本で読む仕組みを段階3 で作ることになった。保持する本数と捨てる時点の規則は第6.12節に置く。経過時間の窓（`DurationWindow`）で出力参照を読む接続は**拒否を続ける**（第5.6節の検査 f）。段階2 の拒否そのものは変えていない（段階2 の実行経路は最新1件の保持のままである）。
- **履歴窓は受け口の側で構造だけを要求する**【確定】（v1.4、2026-09-22 の人間の決定）。`MarketDataView.history` が受ける窓の型を、宣言の履歴窓の具体クラスではなく**構造的な `Protocol`**（本数を読み出せる窓か、経過時間を読み出せる窓）とする。宣言の履歴窓（D04 §6.2、本数は `int | ParameterRef`）と as-of ビューの履歴窓（D03 §6.2、本数は解決済みの `int`）は同じ名前の別の型であり、`strategy` は `marketdata.application` を参照できず（D01 §3.2 の契約 F2）、`marketdata` は `strategy` を参照できない（層順序）。具体クラスを要求すると、両方を参照できる層（`app`）が層をまたいで窓を言い換えるほかなくなり、段階4 で別のビューへ差し替えるたびに言い換えを書き足すことになる。構造だけを要求すれば、as-of ビューをそのままランタイムへ渡せる。第4.4節（部品が上の層の型を構造だけの `Protocol` で受ける）と同じ手法である。ランタイムは、コンパイラが解決済みの本数（第5.3節）をその受け口の形にして渡す。**不採用**: 合成が言い換える案（層をまたぐ言い換えが恒久的に残り、ビューを差し替えるたびに増える）、宣言の型を `marketdata` 側へ移す案（宣言の型はパラメータ参照を持てる必要があり、コンパイル前の宣言を市場データ層に置くことになる）。
- **市場データの足は項目を射影してから部品へ渡す**【提案】。`MarketDataView` が返すのは `Bar`（D03 §3.3）だが、部品の入力の型は `price@v1` などの単一の内容型である（D04 §5）。そこでランタイムが `ResolvedMarketSource.field` に従って1本ずつ射影し、`ValueSample.payload` に入れる。`OPEN` / `HIGH` / `LOW` / `CLOSE` は `Price`、`VOLUME` は `Decimal` であり、D04 §5 が定めた項目とデータ型の対応（`price@v1` / `volume@v1`）と一致する。`ValueSample.freshness_time` は `MarketDataView.freshness(series, bar)`（確定足なら足の終了時刻）。射影を履歴側でも行うのは、`extreme_price` が `Bar` ではなく `Price` の列から最大・最小を求める契約になっているためである。部品に `Bar` を渡さないことで、宣言していない項目（当該足の終値など）を部品が覗くこともできなくなる。
- **`max_age` の判定はランタイムが行う**【合意済み】D03 §6.2。`decision_time - freshness_time > max_age` なら `MAX_AGE_EXCEEDED`。ビューは鮮度基準時刻を返すだけである。
- **出力参照の鮮度基準時刻**は、その出力を生んだ評価の `decision_time` とする【提案】。上流の観測区間まで遡る鮮度の伝播は段階3（第10節）。段階2の5部品は `max_age=None` のため、この選択は検証戦略 A の結果を変えない。**不採用**: 上流の `Observation.freshness_time` を伝播させる案（複数系列を混ぜる部品が無い段階2では検証できない規則を先に固定することになる）。
- 欠損は **`MissingInputDiagnosis` として集め**、`InputReadSpec.on_missing` に従う。`SkipEvaluation` なら評価を行わず `Skipped` を記録する。`Error` なら `Failed(Reason(DATA_ERROR, ...))` を記録し、以降の評価を行わずに `RuntimeStepResult` を返す（前節の「失敗は戻り値で返す」）。**欠損を False や 0 に変換しない**【合意済み】上位 §4.3.15。
- ウォームアップ不足は `history` が返す `WARMUP_INSUFFICIENT` として現れ、`SkipEvaluation` により評価が飛ぶ。これで「warmup 中の注文ゼロ」（全体計画 §8.2）が成立する。
- 必須入力の判定は、成立した起動条件の `required_inputs` の**和集合**とする【提案】。必須でない入力が欠けても評価は行う。
- **契約が必須入力を1件も宣言していない場合は、接続済みの入力すべてを必須として扱う**【提案】（v1.3、2026-09-21 の人間の決定。PR #18）。段階2 の5部品（第4.3節）はいずれも `required_inputs` を書いていない。文字どおり「和集合＝空集合」を必須とすると、履歴が足りなくて読めない入力があっても評価を行うことになり、T01 §6.1 が示す「ウォームアップ中は見送る」挙動にならず、全体計画 §8.2 の「warmup 中の注文ゼロ」も成立しない。判定は「その起動条件のキーが `required_inputs` にあればその和集合、1件も無ければ接続済みの入力すべて」とする。**不採用**: 空集合をそのまま必須とする案（ウォームアップ中に部品が欠損した入力で評価される）、5部品の契約に `required_inputs` を全入力ぶん書く案（すべての契約で同じ列を書き写すことになり、書き忘れが黙って挙動を変える）。

### 6.4 評価要求のライフサイクル【提案】

段階2の終端は3つだけである【合意済み】全体計画 §5.3.5 の表（待機と追い越しは段階3）。

| 終端 | 記録 |
|---|---|
| `Evaluated` | 生成した `OutputId` の列 |
| `Skipped` | 欠損診断の列（`MissingInputDiagnosis`） |
| `Failed` | `Reason`（段階2では `DATA_ERROR` のみ） |

**段階3 は終端をあと2つ足す**（第3節の型表の改訂。第6.8節・第6.10節）。`Waiting(diagnoses, deadline_at)` は入力の到着を待っている状態、`Superseded(by_request_id)` は新しい足の要求に追い越されて閉じた状態である。`Waiting` は**終端ではなく途中経過**であり、同じ `request_id` の要求が後で `Evaluated` / `Skipped` / `Failed` / `Superseded` のいずれかで決着する。「`Skipped` / `Failed` で決着した要求は復活させない」という規則は変わらない【合意済み】上位 §4.3.14。

- **`Skipped` / `Failed` で決着した要求は復活させない**【合意済み】上位 §4.3.14。上流の到着で再開する経路は段階2には無い。
- 評価記録は起動した使用箇所ごとに必ず1件残す。入力不足で評価しなかったことも記録に残す【合意済み】上位 §4.3.15。
- **評価記録は要求の対象（`target_interval` / `opportunity_id` / `position_id`）を写して持つ**【提案】。`EvaluationRequest` そのものは `RuntimeStepResult` に入れないため、写さないと「同じバッチで同じ Exit を2つの建玉について評価した」場合にどの記録がどの建玉のものか `request_id` からは復元できない。**不採用**: 要求を結果にそのまま入れる案（同じ内容が2つの型に並ぶ）。
- `RequestId` / `EvaluationId` / `OutputId` は `IdAllocator`（D02 §7.3）で採番し、採番順は評価順に一致させる。

### 6.5 部品状態の保持と更新【提案】

- 状態はランタイムが使用箇所ごとに保持する【合意済み】ADR-0008。`RuntimeState.component_states` は `instance_id` → 状態 payload の凍結 `Mapping` で、`step` ごとに**新しい `RuntimeState` へ差し替える**。可変参照はランタイム1インスタンスにつき1つだけとし、他はすべて不変とする（第1節の例外）。
- **`VALUE` の出力は最新の1件を `RuntimeState.latest_outputs` に保持する**【提案】。`LatestAvailable` は「更新まで繰り返し参照する値」を読む読み方であり（D04 §4.2）、上流の使用箇所が今回の `step` で起動していないときにも読めなければならない。検証戦略 A では起動が同じ足に揃うが、上流が日足・下流が1時間足という構成（段階3の検証戦略 B）では毎回ずれる。保持するのは出力参照（`OutputRef`）ごとに最新の1件だけで、履歴は持たない（履歴は市場データの `HistoryWindow` が担う）。`EVENT` と `COMMAND` の出力は保持しない。配送された時点で消費されるものであり、保持すると過去のイベントを「最新値」として読めてしまう。**不採用**: 出力の読み取り用ポートを足す案（エンジン側に戦略の出力台帳を持たせることになり、D01 §4 のポートの向きと合わない）、全出力の履歴を持つ案（段階2で使わない記憶が増える）。
- **段階3 は、履歴窓で読まれる出力についてだけ過去も保持する**【合意済み】（Q18 決定、選択肢2。第11節の差異10）。`RuntimeState` に `output_history` を足し、**コンパイル結果が保持本数を指定した出力参照だけ**、その本数の過去を古い順に持つ。上の「最新の1件だけ」という規則は、`output_retention.by_output` に載らない出力（＝履歴で読む相手が1つも無い出力）についてはそのまま変わらない。`EVENT` と `COMMAND` を保持しない規則も変えない（履歴で読めるのは `VALUE` の出力参照だけである。D04 §6.1 の許可表）。全出力の履歴を持たないのは、上の不採用の理由が段階3 でもそのまま当てはまるためである。規則の全体は第6.12節に置く。
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

**通し番号の規則**【提案】（v1.3、2026-09-21 の人間の決定。PR #18）。

- 1回の `step` につき **0 から始まる1本の番号**を使い、`step` が終わるたびに捨てる。次の `step` はまた 0 から始まる。
- **出力記録の `sequence` と、取引機会の遷移記録の `ProcessingPoint.sequence` は、この1本の番号を共有する**。番号を分けると、同じ判断時点の出力と遷移のどちらが先だったかが記録から読めなくなる。
- 採番は**発生順**、すなわち第6.2節の手順の実行順である。取引機会の組み立てと同時保持の判定（手順6）は出力の付番（手順7）より前なので、**生成の遷移は、その取引機会を載せた出力記録より小さい番号になる**。
- フェーズごとに番号を数え直さない。`ProcessingPoint` の全順序は `(time, phase.rank, sequence)`（D02 §3.3）であり、`step` 内で単調に増える番号はその中で一意であれば足りる。番号が飛ぶ（あるフェーズの番号が 0 から始まらない）のは正常である。
- **不採用**: フェーズごとに 0 から数え直す案（出力と遷移の前後関係が記録から読めなくなる）、出力と遷移で別の番号を使う案（同じ理由）。

### 6.7 複数の時間足をまたぐ入力、鮮度と観測区間【提案】＋【合意済み】（Q13 決定、選択肢1）

段階2 の検証戦略 A は1つの系列しか読まないため、「どの足を観測したか」を出力に載せる必要がなかった。検証戦略 B は日足・1時間足・15分足を同じ戦略の中で読むので、次の3つが要る。

1. **上流の出力の鮮度**。市場状態が読む日足 EMA の鮮度は、EMA を計算した判断時刻ではなく**その EMA が観測した日足の終了時刻**である。上位設計書 §4.3.15 の例がそのとおりに書いている（「22:00 に終了した日足から EMA を22:00:02 に計算した場合、`decision_time` と `available_at` は 22:00:02。`Observation` の対象は当該日足、`freshness_time` は 22:00」）。段階2 の規則（出力参照の鮮度基準時刻はその出力を生んだ評価の `decision_time`、第6.3節）のままだと、**前日の日足から作った市場状態が今日の判断時刻の鮮度を持ってしまい、`max_age` がまったく効かない**。
2. **観測区間の一致**。`atr` のように複数の入力を組にして読む部品では、3本の窓が同じ区間の足から来ていることを確かめる必要がある（`AlignmentRequirement(SAME_OBSERVATION_INTERVAL)`、D04 §9.2）。段階2 は守らせる仕組みを持たず、空でない `TemporalConstraints` を拒否していた（第11節の2）。
3. **待機からの再開で対象区間を固定して読み直すこと**（第6.8節）。固定する対象を表すには、出力にも「どの足を見た結果か」が要る。

**`VALUE` の出力をランタイムが `Observation` で包む**【合意済み】（Q13 決定、選択肢1）。上位設計書 §4.3.15 が確定した `Observation[T]` の4フィールドをそのまま使い、**包むのはランタイム、部品は内容だけを返す**（第4.2節の取引機会と同じ手法）。

| `Observation` のフィールド | ランタイムが入れる値 |
|---|---|
| `value` | 部品が返した内容 |
| `subject` | その評価の対象区間を与えた足の `BarKey`（`OnBarClose` なら `BarClosure.bar_key`、入力イベントによる起動なら上流の要求から引き継いだもの。第6.2節 手順3） |
| `observation_interval` | その評価の `target_interval` |
| `freshness_time` | その評価が読んだ全入力の鮮度基準時刻の**最小値**。市場データも上流出力も読まない評価（`constant_condition` など）では `decision_time` |

読む側の扱いは次のとおりとする。

- `LatestAvailable` で出力参照を読むとき、`ValueSample.payload` には `Observation.value`（＝部品が返した内容）を入れ、`ValueSample.freshness_time` には `Observation.freshness_time`、`ValueSample.observation_interval` と `ValueSample.subject` には同名のフィールドを入れる。**部品は `Observation` を受け取らない**（部品の入力の型は `price@v1` などの単一の内容型であり、D04 §5 の接続検証が見るのもその型である）。
- `max_age` の判定（第6.3節）はこの `freshness_time` を使う。規則そのものは段階2 と同じで、材料だけが正確になる。
- **鮮度基準時刻の最小値を採るのは、「その出力が答えている問いが、いちばん古い観測にどこまで引きずられているか」を表すため**である。日足 EMA と15分足 EMA を混ぜた部品の出力は、日足の終了時刻の鮮度しか主張できない。

**不採用**: ランタイムが出力参照ごとの鮮度表を `RuntimeState` に別に持つ案（`OutputRecord` を判断履歴から読み直しても鮮度が復元できず、遅延シナリオの差分を追跡できない）、`OutputRecord` に鮮度の項目を足す案（上位設計書 §4.3.15 が確定した7フィールドの改訂になり、`Observation` と役割が重なる）、段階2 の規則のまま `max_age` を市場データ参照にだけ効かせる案（市場状態が古いまま取引機会が作られる経路が残る）。

**観測区間の一致を守らせる**【提案】。`AlignmentRequirement(input_names, SAME_OBSERVATION_INTERVAL)` を宣言した契約では、入力解決（第6.3節）の直後・部品の呼び出しの直前に、**`input_names` に挙がった入力のすべての要素の `observation_interval` が等しいこと**を確かめる。等しくなければ部品を呼ばず、`MissingInputDiagnosis` ではなく `Failed(Reason(DATA_ERROR, ...))` を評価記録に残す（欠損ではなく、宣言と接続の食い違いが実行時に現れたものだから）。市場データ参照の要素の `observation_interval` は射影元の足の区間、出力参照の要素の `observation_interval` は上流の `Observation.observation_interval` である。**不採用**: コンパイル時にだけ検査する案（同じ系列でも夏時間の切替日は区間の長さが変わるため、宣言からは確かめられない）、違反を欠損として扱う案（`on_missing` が待機ならデータが届いても永久に解消しない）。

**時間足の大小から順序を推測しない規則は変えない**【合意済み】全体計画 §5.3.4 の6。評価順は依存グラフだけから決まり（第5.4節）、日足が1時間足より先に評価されるのは接続がそう書かれているからである。

### 6.8 待機（`WAIT_FOR_INPUT`）【提案】＋【合意済み】（Q15 決定、選択肢1）

D04 §1.2 の行1 は、欠損方針の残る2区分の宣言形を**明示の例外として本書へ委ねている**（人間の Q8 決定）。本節がその宣言形と意味論を決め、D04 §6.3 への追加依頼として第12.1節に挙げる。追加は保存形式の版（`schema_version`）の引き上げとして扱う【合意済み】D04 §6.3。

**宣言形**【合意済み】（Q15 決定、選択肢1）:

```
WaitForInput(
    deadline: BarsDeadline | DurationDeadline,
    on_deadline: WaitDeadlineAction,   # SKIP_EVALUATION | ERROR
    on_superseded: OnSuperseded,       # EXPIRE_REQUEST | KEEP_WAITING
)
```

上位設計書 §4.3.13 が待機に要求する追加条件は「有限の待機期限、期限切れ処理、対象入力区間、再開時の評価時刻」の4つである。このうち**対象入力区間と再開時の評価時刻は宣言ではなく実行時に決まる**（前者は最初の評価要求が解決した区間、後者は入力が届いた判断時刻）ので、宣言に置くのは前2つと、§4.3.14 が要求する `on_superseded` の3つになる。期限型は D04 §10.1 が確定した2つをそのまま使い、新しい期限型を作らない。

**待機できる欠損理由をランタイムの固定規則で絞る**【提案】。`MissingInputReason`（D02 §8.3）の4区分のうち、**待っても解消しない2つでは待機に入らない**。

| 欠損理由 | 待機に入るか |
|---|---|
| `LATEST_BAR_UNAVAILABLE`（期待される最新足が未到着） | **入る**。対応する `Publication` の到着で解消する（D03 §7.2） |
| `INPUT_MISSING_OR_INVALID`（窓内の期待足が欠けている） | **入る**。同上 |
| `WARMUP_INSUFFICIENT`（データ開始前に及ぶ） | 入らない。過去のデータは後から増えない。`SkipEvaluation` と同じ扱いにし、評価記録に `Skipped` を残す |
| `MAX_AGE_EXCEEDED`（鮮度の上限を超えた） | 入らない。値は届いているが古い。待つと鮮度はさらに悪化する。同じく `Skipped` |

**不採用**: 待機を許す欠損理由を宣言で絞れるようにする案（Q15 の選択肢2。待っても解消しない理由を書けてしまい、宣言から挙動が読めなくなる。上限つきの待機なので安全側には倒れるが、判断履歴に「絶対に解消しない待機」が並ぶ）、4区分すべてで待機する案（run が期限まで無駄に止まる）。

**待機記録（`WaitingRequest`）に何を固定するか**【提案】。上位設計書 §4.3.14 の「待機記録」の4項目をそのまま型にする。**固定するのは「問い」であり、市場入力の値そのものではない**【合意済み】同節。

| フィールド | 中身 | §4.3.14 のどの項目か |
|---|---|---|
| `request: EvaluationRequest` | 評価要求そのもの（要求 ID・使用箇所・起動条件名・判断時刻・対象区間・機会・建玉） | 評価要求 ID、部品使用箇所 ID |
| `pinned_bars: Mapping[str, BarKey]` | 入力名ごとに固定した対象足（`LatestAvailable` なら期待される最新足の鍵、`HistoryWindow` なら窓の末尾の足の鍵） | 対象足の系列 ID と区間、解決済み入力区間 |
| `pinned_events: Mapping[str, tuple[EventDelivery, ...]]` | 既に配送された入力イベント。再開時には再配送されないので待機記録が持つ | 既に受け取った取引機会の内容 |
| `opportunity: Opportunity \| None` | 受信済みの取引機会（内容は不変） | 同上 |
| `missing: tuple[MissingInputDiagnosis, ...]` | まだ足りない入力 | 不足している入力の識別情報 |
| `started_at: ProcessingPoint` | 待機を始めた処理点 | 待機開始時刻 |
| `deadline_at: WaitDeadline` | 期限を絶対の形に解決したもの（`WaitUntilTime` か `WaitUntilBars`） | 有限の期限 |
| `on_deadline` / `on_superseded` | 宣言の写し | 期限切れ時の動作、`on_superseded` の設定 |

実験・戦略・契約・パラメータの固定参照（§4.3.14 の1項目め）は `CompiledStrategy` が run 全体で1つであり、待機記録ごとに持たない【提案】。run の中で戦略は差し替わらず、持たせると同じ値が待機記録の数だけ並ぶ。

**期限を絶対の形へ解決する**【提案】。`DurationDeadline(d)` は `WaitUntilTime(待機開始の decision_time + d)`、`BarsDeadline(n)` は `WaitUntilBars(series=その入力の系列, remaining=n)` にする。本数で数える期限は**足りない入力の系列の確定足**（`ScheduledBoundary`）で1ずつ減らす。データが遅れて到着しないときに数えるものが要るので、`Publication` ではなく `ScheduledBoundary` を使う（D03 §7.2 と同じ考え方）。足りない入力が複数あるときは、**最初に足りなくなった入力の系列**で数える。

**再開の契機と処理**【提案】（上位設計書 §4.3.14 の5手順の実施）。判断時点ごとに、評価より前の**ライフサイクル検査**（D06 の `OPPORTUNITY_LIFECYCLE` フェーズ）で次を行う。

1. 待機中の各要求について、`missing` に挙がった入力が読めるようになったかを調べる。読めるようになる条件は、**その入力が指す系列の足が `PublicationBatch.available_bars` に含まれること**である。別の系列の足の到着は再開の理由にしない【合意済み】同節。
2. 期限（`deadline_at`）と追い越し（第6.10節）と、受信済み機会の失効（第7.3節の `REQUIRE_UNTIL_ORDER_REQUEST` の再検査）を検査する。**期限・追い越し・失効は再開より先に判定する**。
3. 足りない入力がまだ残っていれば、`missing` を更新して待機を続ける。**部分的に届いた入力で評価を始めない**【合意済み】同節。
4. すべて読めるようになったら、**固定した対象足（`pinned_bars`）で市場入力を読み直し**、現在状態の入力（`CurrentContext`）は**今回の `decision_time`** で読み直す。配送イベントの入力は `pinned_events` から復元する。
5. 評価を行い、結果を下流へ配送する。`decision_time` は**再開した判断時刻**であり、元の対象足の終了時刻へ遡らせない。

再開した評価の評価記録は、**元の `request_id` を保ったまま** `Evaluated` / `Skipped` / `Failed` のいずれかで決着する【提案】。要求 ID を変えると「待機に入った要求」と「再開した評価」を判断履歴で結べない。ただし `EvaluationRecord.decision_time` は再開した判断時刻であり、待機を始めた時刻は `WaitEvent(WAIT_STARTED)` が持つ。

**待機そのものは評価記録の終端にしない**【提案】。`EvaluationOutcome` に `Waiting(diagnoses, deadline_at)` を足し、**待機に入ったことを1件の評価記録として残す**。その後の再開・期限切れ・追い越しは `WaitEvent` の列（`RuntimeStepResult.wait_events`）に残り、決着したときにもう1件の評価記録が出る。同じ `request_id` の評価記録が複数出ることになるが、**`EvaluationId` は毎回新しく採番する**ので記録は一意である。**不採用**: 決着するまで評価記録を出さない案（待機中の run が落ちると、何を待っていたかが判断履歴に残らない）、待機を評価記録ではなく別の表にだけ残す案（第6.4節の「起動した使用箇所ごとに必ず1件残す」が崩れる）。

**期限切れの処理**【提案】。`on_deadline=SKIP_EVALUATION` なら `Skipped(missing の診断)` で決着し、`ERROR` なら `Failed(Reason(DATA_ERROR, ...))` で決着して以降の評価を行わずに返す（第6.2節の「失敗は戻り値で返す」）。どちらも `WaitEvent(DEADLINE_REACHED)` を残す。**待機しても取引機会本来の期限は延長しない**【合意済み】ADR-0031・上位 §4.5。

**待機の伝播**【提案】。上位設計書 §4.3.14 は「全下流を無条件に待機へ変換するわけではなく、解決済みの各入力ポリシーと戦略の実行契約が待機を認める場合に限る」と定めている。段階3 の規則は次の2つだけにする。

- **明示の接続をたどる伝播は行わない**。上流が待機中なら、その出力を読む下流の入力は「まだ出ていない」＝`INPUT_MISSING_OR_INVALID` として扱われ、**下流自身が宣言した `on_missing` に従う**。下流も待機を宣言していれば待機し、見送りを宣言していれば見送る。これが「各入力ポリシーが待機を認める場合に限る」の実施である。
- **市場状態の役割だけは例外**で、取引機会を出す評価が待機する（第7.6節）。役割フィールドには `InputSpec` が無く `on_missing` を宣言できないため、規則をランタイムが持つ。

**不採用**: 上流が待機したら下流をすべて待機させる案（同節が明示的に否定している）、市場状態も伝播させない案（遅延シナリオで日足が2秒遅れたとき、許可が読めないまま取引機会が終端し、上位設計書 §4.3.14 の「22:00:02 に 21:00〜22:00 の1時間足を対象に Trigger を評価して O1 を生成する」という確定例と食い違う）。

### 6.9 遡り（`USE_PREVIOUS`）【提案】＋【合意済み】（Q16 決定、選択肢1）

**宣言形**【合意済み】（Q16 決定、選択肢1）:

```
UsePrevious(
    max_lookback: BarsWindow | DurationWindow,
    allowed_reasons: tuple[MissingInputReason, ...],   # 1件以上
)
```

上位設計書 §4.3.13 が遡りに要求する追加条件は「遡れる足数/経過時間、実際に使った観測の記録、許可する欠損理由」の3つである。**記録はランタイムの責務**なので宣言に置かず、宣言には残る2つを置く。窓の型は D04 §6.2 の2つをそのまま使う。

**許可する欠損理由を宣言で絞る**【提案】。待機（第6.8節）と扱いが逆なのは、上位設計書 §4.3.13 が「用途は許可した未取得・公開遅延等に限定し、**破損データや計算例外まで一律に過去値で隠さない**」と明記しているためである。待機は待てば解消するかどうかが理由ごとに決まる（だからランタイムの固定規則でよい）のに対し、遡りは**どこまでを「隠してよい欠損」とみなすかが戦略ごとの判断**になる。

**適用範囲**【合意済み】上位 §4.3.13:

- **`LatestAvailable` にだけ許す**。固定本数の `HistoryWindow` の穴埋めには使わない。20本の窓の欠損を21本目の古い足で補うことは禁止する。コンパイル時に拒否する（第5.6節の検査 c）。
- `max_age`・未来参照の禁止・型と単位の条件は緩和しない。**遡って選んだ足も `max_age` の判定を受ける**。`max_lookback` を満たしても `max_age` を超える足は使えない。
- 有効性の再検査（第7.3節）の `ValidityBinding.on_missing` にも書ける。ただし「欠損を不成立へ暗黙変換しない」規則は変わらない【合意済み】ADR-0031。遡って読めた値が不成立ならそれは不成立であり、遡れなければ欠損のままである。

**実際に使った観測を評価記録に残す**【提案】。`EvaluationRecord` に `substitutions: tuple[SubstitutedInput, ...]` を足し、遡って使った入力ごとに1件残す。

| `SubstitutedInput` のフィールド | 中身 |
|---|---|
| `input_name` / `source` | どの入力の、どの接続元か |
| `used_bar_key` / `used_output_id` | 実際に読んだ足、または上流の出力（どちらか一方が非 `None`） |
| `freshness_time` | 読んだ値の鮮度基準時刻 |
| `reason` | 遡りを発動させた欠損理由 |

**不採用**: 遡りを使ったことを出力側（`Observation`）にだけ残す案（評価が見送られた場合と区別できず、`Skipped` の診断と並べて集計できない）、遡りの記録を持たない案（上位設計書 §4.3.13 が明示的に要求している）。

**遡りは「最新の確定足を要求する標準動作への明示的な例外」である**【合意済み】上位 §4.3.13。遡って読んだ値をそのまま `Observation` で包むとき、`subject` と `observation_interval` は**実際に読んだ古い足**のものになる（第6.7節）。これにより、下流の `max_age` と観測区間の一致の判定に古さがそのまま伝わる。

### 6.10 評価要求の追い越し（`REQUEST_SUPERSEDED`）【提案】＋【合意済み】（Q17 決定、選択肢1）

語の正本は上位設計書 §4.3.14 と ADR-0033 である。**取引機会の終端理由 `SUPERSEDED` とは対象が異なる**（機会が終わるのではなく、評価要求が閉じる）。

**追い越しの判定**【提案】。同じ判断時点のライフサイクル検査で、待機中の各要求について次を調べる。

> その要求の**対象系列**（`pinned_bars` が指す足の系列のうち、要求の `target_interval` を与えた系列）について、**要求が固定した足より新しい足が公開済みになった**か。

公開済みとは `PublicationBatch.available_bars` にその系列の新しい足が含まれることである。追い越しがあったときの扱いは `WaitForInput.on_superseded` が決める。

| `on_superseded` | 扱い |
|---|---|
| `EXPIRE_REQUEST` | 古い要求を `Superseded(by_request_id)` で閉じ、`WaitEvent(SUPERSEDED)` を残す。理由コードは `REQUEST_SUPERSEDED`（D02 §8.1） |
| `KEEP_WAITING` | 期限まで待ち続ける。追い越し自体は `WaitEvent` に残さない |

**同一戦略内の評価要求の順序と、後着優先の規則**【提案】。追い越しは「新しい足の要求が古い足の要求を押しのける」という**後着優先**である。押しのける側とされる側を一意に決めるため、次の順序を使う。

1. 同じ使用箇所・同じ対象系列の待機中の要求は、**固定した足の `bar_key`（足の開始時刻）の昇順**に並ぶ。同じ足を固定した要求が2件並ぶことはない（同じ対象区間の起動は1件に集約するため。第6.2節 手順3、Q6 決定）。
2. 新しい足の確定で新しい要求が生まれたとき、その使用箇所・その系列の**より古い足を固定した待機中の要求すべて**が追い越される。1本飛ばしで2件が同時に追い越されることもある（遅延シナリオの「次足まで到着しない場合」がこれに当たる）。
3. `by_request_id` には**押しのけた側の要求 ID**（いちばん新しい足の要求）を入れる。押しのけた要求が同じ `step` の中でさらに待機に入ることもあるが、追い越しの記録は変わらない。
4. **新しい要求を作るのは追い越しの判定より後**である。判定を先に行わないと、同じ `step` の中で生まれた要求が自分自身を追い越す。

**確認の追い越しは取引機会を失効させない**【合意済み】上位 §4.3.14。標準方針は「対象確認足が追い越されても、上流の取引機会そのものはこの理由だけでは失効させない。確認の対象足を進める」である。実施は第7.7節に置く。**Trigger の標準方針**は「古い対象足が追い越されたら、その待機評価を失効させる。古い突破を後から新たに実行しない」であり、`on_superseded=EXPIRE_REQUEST` がそれに当たる。

**既定をランタイムに持たせない**【合意済み】（Q17 決定、選択肢1）。上位設計書 §4.3.14 は「部品/用途別の既定を実験固定時に解決・記録する」としているが、段階3 は**宣言（`WaitForInput.on_superseded`）を必須にし、役割ごとの既定をランタイムに持たせない**。役割ごとの既定を置くと、同じ挙動が「ランタイムの既定」と「宣言」の2か所から決まり、ADR-0031 が退けた「暗黙の既定値」がここで復活する。標準方針は**第9.2節の検証戦略 B の宣言例が具体値として示す**。**不採用**: 役割ごとの既定をランタイムが持ち宣言は上書きだけとする案（Q17 の選択肢2。宣言を書かなくても動くが、判断履歴から既定の出どころを追うのに実験の固定設定を見る必要が出る）、両方を持つ案（同じ規則が2か所になる）。

### 6.11 取引機会・建玉を対象とする評価要求【提案】＋【合意済み】（Q14 決定、選択肢1）

段階2 は「イベント1件につき1要求」だけで足りた（第6.2節 手順2）。段階3 は次の2つが**足の確定で起動しながら、特定の取引機会または特定の建玉を対象にする**。

- 後続確認（`condition_filter`）: 確認足の確定ごとに、**確認待ちの取引機会1件ごとに**評価する。
- トレーリング（`trailing_stop`）: 1時間足の確定ごとに、**建玉1件ごとに**評価する。

**現在コンテキストの対象を読む使用箇所は、足の確定で起動するとき対象1件につき1要求を作る**【合意済み】（Q14 決定、選択肢1）。第6.2節 手順2 の表に1行足す。

| 起動 | 要求の数 |
|---|---|
| `OnBarClose` で、`RuntimeInputRef(OPPORTUNITY)` を読む使用箇所 | その対象区間について、**確認待ちの取引機会1件につき1件**（0件なら要求を作らない） |
| `OnBarClose` で、`RuntimeInputRef(POSITION)` を読む使用箇所 | その対象区間について、**開いている建玉1件につき1件**（0件なら要求を作らない） |

要求の `opportunity_id` / `position_id` にはその対象を入れ、`target_interval` は `BarClosure.interval` とする（第6.2節 手順3 の表に対応する2行を足す）。`CurrentContext` の入力解決（第6.3節）は段階2 と同じく**評価要求が指す対象**を読む。機会を読む場合は `RuntimeContextView` ではなく**ランタイム自身が保持する `OpportunityLifecycle`** から `Opportunity` を渡す（取引機会の所有者はランタイムであり、エンジンに問い合わせない。第7節）。

**`RuntimeTarget` に `OPPORTUNITY` を足す**【提案】。D04 §4.3 の `RuntimeTarget` は段階2 で `POSITION` / `ACCOUNT` の2値であり、取引機会を読む区分が無い。D04 への追加依頼として第12.1節に挙げる。対応するデータ型は既に登録済みの `opportunity@v1` であり、新しいデータ型は要らない。

**同じ `step` で同じ使用箇所が複数の要求を作るので、要求に通し番号を持たせる**【提案】。`EvaluationRequest` に `attempt_index: int`（0 起点）を足す。同じ使用箇所・同じ対象区間・同じ判断時刻の要求が2件以上並ぶとき、`request_id` だけでは判断履歴で並びが読めない。番号の順は**対象の識別子の昇順**（`opportunity_id.seq` または `position_id.seq`）とし、`RequestId` の採番もその順に行う（第6.4節の「採番順は評価順に一致させる」を対象の順まで細かくしたもの）。

**不採用**: 新しい起動条件の区分（確認足ごとの起動、建玉ごとの起動）を D04 に足す案（Q14 の選択肢2。宣言から「対象ごとに1回」が読めるようになるが、起動条件の区分が2つ増え、`AllowedTrigger` と `EvaluationTrigger` の両方に対応する区分が要る）、取引機会を配送イベントとして確認足ごとに再配送する案（Q14 の選択肢3。ADR-0032 の「同一 `event_id` の再配送は冪等性検査で除外する」と衝突し、同じ機会のイベントが何度も判断履歴に並ぶ）。

### 6.12 上流の出力を履歴窓で読む【提案】＋【合意済み】（Q18 決定、選択肢2）＋【要決定】Q20・Q21

2026-09-22 の人間の決定により、**他の部品の出力を「過去 N 本」で読む仕組みを段階3 で作る**（Q18、選択肢2）。起草時の推奨は段階4 以降へ送る案だったが、決定は逆である。決定の影響欄が述べるとおり「保持する本数と捨てる時点の規則」が要るので、本節がそれを置く。**この仕組みを使う部品は段階3 では作らない**（Q12 の決定は変えない。第4.6節・第10.2節）。仕組みだけを先に作るのは、段階4 以降で N 本継続の合成部品を足すときに、新しいデータ型も部品の状態も足さずに済むようにするためである。

#### 何を保持するか【提案】

保持するのは **`VALUE` の出力**だけで、`EVENT` と `COMMAND` は段階2 と同じく保持しない（第6.5節）。1件は `RetainedOutput`（出力記録そのものと、そこから写した `subject` / `observation_interval`）であり、`RuntimeState.output_history` に**出力参照ごとに古い順**で持つ。観測の識別を写すのは、読み手が `ValueWindow` の各要素の観測区間を見て観測区間の一致（第6.7節）を確かめられるようにするためである。

#### 保持する本数の上限をどう決めるか【提案】（決定の影響欄が要求する1つめ）

**接続が要求する最大の窓から導く**。コンパイラは第5.1節 段5 で、ある出力参照 `r` を読む入力の計画（`InputPlan`）をすべて集め、次の値を `OutputRetentionPlan.by_output[r]` に入れる。

| 読み方 | その読み手が要求する本数 |
|---|---|
| `HistoryWindow(BarsWindow(n), exclude_latest_bars=k)` | `n + k`（当該足を除く指定があるぶんだけ古い側まで要る） |
| `LatestAvailable` | 1 |
| 読み手が1件も無い | 載せない（`by_output` に鍵を作らない） |

**その出力参照についての最大値**を採る。1つの出力を20本の窓と5本の窓が読むなら20本を保持し、窓ごとに別々の記憶を作らない。経過時間の窓（`DurationWindow`）で出力参照を読む接続を拒否し続ける（第5.6節の検査 f）のは、**この導出がコンパイル時にできないから**である。上流の出力が何分おきに出るかは、上流の起動条件が指す系列のカレンダーまで見ないと分からず、宣言だけからは本数へ直せない。上限を導けない窓を通すと、打ち切りの規則が run 中の実データに依存し、「同じ宣言・同じ入力から同じ trace」（全体計画 §8.2）が保てない。

**不採用**: run 全体の上限を1つ決めて全出力に同じ本数を保持する案（使わない記憶が増え、上限を超える窓を宣言したときに宣言が黙って無視される）、宣言に保持本数を書かせる案（読み手の窓から一意に決まる値を人が二重に書き、食い違ったときにどちらが正しいか決まらない）。

#### いつ捨てるか【提案】

**新しい1件を積んだその場**で、古い側から溢れたぶんを捨てる。`len(output_history[r]) > by_output[r]` になったら、超えた本数だけ先頭（いちばん古い側）を落とす。捨てる判定を判断時点の末尾やフェーズの境目に置かないのは、同じ `step` の中で積む処理と読む処理が両方起きうる（上流と下流が同じ判断時点で評価される）ためで、まとめて捨てると読んだ本数が処理順に依存する。`reset_on=(RUN_START,)` の状態と違い、**保持した出力は run 開始以外では初期化しない**（初期化する契機を置くと、同じ足の答えが run 内の位置で変わる。第4.5節の EMA と同じ理由）。

**何を「1件」と数えるかは未決**（第15節 Q20）。同じ使用箇所が同じ足について2回評価されうるため（待機からの再開、対象ごとの要求。第6.8節・第6.11節）、評価ごとに1件積むのか、観測した足ごとに1件（同じ足の2回目は置き換え）にするのかで、N 本の窓が指す過去の範囲が変わる。本文は決めず、要決定に上げる。

#### 待機から再開したときにどう読むか【提案】

待機からの再開では、市場入力を**固定した対象足（`pinned_bars`）で読み直す**（第6.8節 手順4）。上流の出力の履歴についても同じ考え方が要るが、**どこを基準に窓を切るかは未決**である（第15節 Q21）。固定した対象足に対応する時点まで遡って窓を切るのか、再開した判断時点の最新から切るのかで、待機して再開した評価の答えが変わる。本文は決めず、要決定に上げる。待機に入った時点で読めた本数を `WaitingRequest` に写し取る案も含め、選択肢は Q21 に並べる。

#### 判断履歴に何を残すか【提案】（決定の影響欄が要求する2つめ）

**新しい記録の型も新しい表も足さない**。読んだ上流の出力は、そのすべてが既に出力記録（`OutputRecord`）として判断履歴の出力の表に `output_id` 付きで残っており（第6.6節、D06 §9.2 の表1）、`ValueWindow` の各要素（`ValueSample`）が持つ `source_output_id` がその行を指す。保持と打ち切りの規則が上のとおり決定論であるため、**評価記録の `decision_time` と保持本数の計画から、その評価が読んだ窓を後から一意に復元できる**。

**不採用**: 読んだ出力 ID の列を評価記録へ足す案（同じ識別子が出力の表と評価記録の両方に並び、窓の本数だけ列が伸びる。遡り（第6.9節）で `SubstitutedInput` を残すのは「標準動作への明示的な例外」を記録するためであり、通常の履歴読み取りはその例外ではない）、保持している履歴そのものを判断履歴へ書き出す案（同じ出力記録が表1 と重複して保存される）。

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
| 5 | `ORDER_PENDING` → `TERMINATED` | 自身の注文が受け付けられた（`AdmissionNotice.accepted=True`） | 通知が配送された処理点＝約定後の評価起動点（第8節。v1.3） | `FULFILLED_BY_ORDER_ACCEPTANCE`（Q4 決定） | 2 |
| 6 | `ORDER_PENDING` → `TERMINATED` | 自身の注文が受付前の審査で拒否された（`accepted=False`） | 通知が配送された処理点＝約定後の評価起動点（第8節。v1.3） | `ORDER_ATTEMPT_REJECTED`（Q3 決定） | 2 |
| 7 | 非終端 → `TERMINATED` | 他の機会の注文が受け付けられ `on_order_accepted=CLOSE_OTHERS` | 通知が配送された処理点＝約定後の評価起動点（第8節。v1.3） | `CLOSED_BY_ORDER_ACCEPTANCE` | 2 |
| 8 | `OPEN` / `CONFIRMED` → `TERMINATED` | `REQUIRE_UNTIL_ORDER_REQUEST` の束縛条件が不成立になった | P4 と P5 直前 | `MARKET_STATE_INVALIDATED` | 2（束縛が空なら発生しない） |
| 9 | 非終端 → `TERMINATED` | run 末尾に残った（`is_run_end=True` の公開バッチを受けたとき。第6.1節、v1.1） | 末尾処理（D06 §4.1 の `RUN_END`） | `RUN_END`（Q5 決定） | 2 |
| 10 | `OPEN` → `CONFIRMED` | その機会を対象とする確認評価が `ConfirmationResult(confirmed=True)` を出した（**v2.0 で発火条件を確定**。確認評価の起動は第6.11節、対象の足と `include_start_bar` の扱いは第7.7節） | P4 | なし | 3 |
| 11 | `OPEN` / `CONFIRMED` → `TERMINATED` | `AwaitConfirmation` の期限に到達した（**v2.0 で発火条件を確定**。`BarsDeadline(n)` は確認足の系列（**どの系列で数えるかは第15節 Q19 として未決**）で機会の生成した足の次を1本目と数えて n 本目の確定（`ScheduledBoundary`）を受けた時点、`DurationDeadline(d)` は `decision_time - created_decision_time >= d` になった時点。第7.7節） | ライフサイクル検査 | `EXPIRED` | 3 |

- 遷移は1件ごとに `OpportunityTransition` として記録する。生成（遷移1・2）は `from_state=None` で表す。
- 終端した機会は復活させない【合意済み】ADR-0031。終端状態からの遷移は表に無く、実装は終端済みの機会への遷移要求を `KernelValueError` で拒否する。
- 理由は `Reason(code, detail=None)`（D02 §8.2）で持ち、**置き換えた相手の機会や発注試行の識別子は `OpportunityTransition` の `counterpart` / `attempt_id` に入れる**【提案】。D02 §8.2 の詳細型は1つの理由コードへ固定して対応付く（`code: ClassVar[ReasonCode]`）ため、5つの終端理由を1つの詳細型では表せない。取引機会専用の終端理由 enum も作らない。D02 §8.1 の `ReasonCode` に5語ともあり、二重定義を避ける（第1.1節）。
- 遷移5〜7は**エンジンからの通知**（`AdmissionNotice`）を次の `step` の入口で適用する。ランタイムは受付の可否を自分で決めない（リスク審査は `backtest.admission`）。
- **記録するフェーズは「受付を判定したフェーズ」ではなく「通知が配送された処理点」である**【提案】（v1.3、2026-09-21 の人間の決定。PR #18）。受付の判定はエンジンが済ませており、ランタイムはその結果を機会へ写すだけで、写せるのは通知が届いた `step` の入口だけである。段階2 でエンジンが受付結果を配送するのは**約定後の評価起動点**（第8節。D06 が `POST_FILL_EVALUATION` として実装する起動点）であり、T01 §2.5 の値もそこで記録している。遷移記録の処理点は「いつ機会の状態が変わったか」を表すものなので、ランタイムが実際に状態を変えた処理点を書く。エンジン側の受付そのものは注文の記録（`AttemptDecision`・`OrderEvent`、D06）に残り、両者は `attempt_id` でつながる。**不採用**: 受付を判定したフェーズを書く案（ランタイムはそのフェーズを通っておらず、状態がまだ変わっていない時点を記録することになる）、通知の中に判定フェーズを持たせる案（同じ事実が注文側の記録と二重になる）。
- 遷移5・6で使う2語は 2026-09-21 に人間が決定し（Q4・Q3、いずれも選択肢1）、正本である上位設計書 §4.5・§4.7.14、全体計画書 §5.3.5、D02 §8.1、ADR-0032 を同じ PR で改訂した。本書はその語を参照しているだけである（第1.1節）。`FULFILLED_BY_ORDER_ACCEPTANCE` は**自身の**注文が受け付けられて役目を終えた機会、`CLOSED_BY_ORDER_ACCEPTANCE` は**他の**機会の注文が受け付けられたために終わった機会を指し、判断履歴で両者を集計上区別できる。
- 遷移10・11（後続確認の成立と確認期限の到達）は段階3 で発生する。始点・終点・記録する理由は v1.0 が確定しており、**期限の数え方と確認評価の起動という発火条件の詳細を v2.0 が埋めた**（上の表と第7.7節）。
- **v2.0 で遷移を1件足す**【提案】。市場状態が取引を許可しない方向の発火を記録して終端する経路である（第7.6節）。

| # | 遷移 | 発火条件 | フェーズ | 記録する理由 | 段階 |
|---|---|---|---|---|---|
| 12 | （生成）→ `TERMINATED` | 役割 `market_state` が指す出力の最新の取引許可が、発火した方向を許していない | P3 | `MARKET_STATE_INVALIDATED` | 3 |

- 遷移12 が遷移2（同時保持上限で有効にしない）と別なのは、終わった原因が違うからである。どちらも `from_state=None` の生成直後の終端であり、判断履歴では理由コードで区別できる。**新しい終端理由の語は作らない**（第7.6節）。
- 遷移9で `RUN_END` を使うのは、残存注文の取消（上位設計書 §4.7.13）と同じ語で末尾処理を読めるようにするためである（Q5 決定）。**不採用**: 終端させず「有効なまま run が終わった」として記録する案（終端理由別の集計で機会の総数が合わない）、機会専用の末尾終端理由を加える案（注文側と別語になる）。
- 遷移8の「P5 直前」は、`EntryProposal` を作る直前に束縛条件を読み直す点を指す【合意済み】ADR-0031。段階2は `bindings` が空のため実行されないが、経路は実装する（段階3で条件を足すだけで動くようにする）。

### 7.3 有効性の再検査【提案】（ADR-0031 の実施）

- `SNAPSHOT_AT_OPPORTUNITY` の束縛は、機会の生成時点で読んだ `OutputRecord` を `ValiditySnapshot` として `OpportunityLifecycle` に固定し、以後更新しない。**固定は、既存の機会を置き換えるより前に行う**【提案】（v1.3）。束縛が読めずに失敗する場合（`on_missing=Error`）、先に古い機会を終端していると、置き換えた相手がひとつも公開されないまま終端の記録だけが判断履歴に残る。
- `REQUIRE_UNTIL_ORDER_REQUEST` の束縛は、遷移8のタイミングで**その時点の最新出力**を読み直す。成立しなくなっていれば終端する。再検査で機会の内容（方向・`signal_interval`・`reference_values`）を変更しない【合意済み】ADR-0031。
- 再検査対象が欠損していれば `ValidityBinding.on_missing` に従う。段階2の2区分では `SkipEvaluation`（今回の再検査を行わず機会を残す）か `Error`（run を失敗させる）であり、**欠損を不成立に変換しない**【合意済み】ADR-0031。待機は段階3。
- 束縛の対象は `condition_state@v1` を出す出力に限られ、成立の判定は `ConditionState.satisfied` を読むことである【合意済み】D04 §10.2・本書 §4.2。

### 7.4 同時保持と発火の記録【提案】（ADR-0032 の実施）

Trigger の出力が出た評価では、必ず次の順で処理する。

1. `OpportunityId` を採番し、`Opportunity` を組み立てる（第4.2節）。**発火を捨てない**【合意済み】ADR-0032。
2. 有効な機会（非終端すべて）の数を数える。
2a. **段階3: 市場状態の取引許可を読む**（第7.6節）。`CompiledRoles.market_state` があり、機会の方向が許されていなければ遷移12 で終端し、以降の手順を行わない。**同時保持の数え方より先に置く**のは、許可されない発火が同時保持の枠を消費しないようにするためである。枠を消費すると、許可された次の発火が上限で見送られる。
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

### 7.6 市場状態の適用【提案】＋【合意済み】（Q10 決定、選択肢1）

上位設計書 §4.3.11 は**確定事項**として次の因果順序を定めている。

> 3. Trigger を評価し、更新後の MarketState で取引可否を判断する。
> 4. 許可された取引機会について ExecutionFilter を評価する。

**取引許可はランタイムが役割フィールド経由で読んで適用する**【合意済み】（Q10 決定、選択肢1）。適用の時点は P3（取引機会を出す出力が出た直後、第7.4節の手順1と2のあいだ）である。

| 事項 | 規則 |
|---|---|
| 何を読むか | `CompiledRoles.market_state` が指す出力の最新の `OutputRecord`（`RuntimeState.latest_outputs`、第6.5節）。payload は `Observation[MarketPermission]` |
| どう判定するか | 発火した取引機会の `direction` が `LONG` なら `allow_long`、`SHORT` なら `allow_short` を読む。真なら遷移1〜3 の判定へ進み、偽なら遷移12 で終端する |
| 鮮度 | `Observation.freshness_time` と `observation_interval`（第6.7節）を判断履歴に残す。役割フィールドには `InputSpec` が無く `max_age` を宣言できないため、**鮮度の上限は課さない**（課すなら宣言する場所が要る。第14節の引き渡し） |
| `market_state` が `None` の戦略 | 適用そのものを行わない。段階2 の検証戦略 A がこれに当たり、挙動は変わらない |

**取引が許可されない発火も取引機会として記録する**【提案】。ADR-0032 は「Trigger の各発火は固有の `opportunity_id` を持つ不変の取引機会を生成する」「発火そのものを捨てることはしない」と定めている。したがって許可されない方向の発火も、`OpportunityId` を採番して `Opportunity` を組み立て、**有効にせず遷移12 で終端する**。同時保持上限による遷移2 と同じ形であり、判断履歴で「市場状態が許さなかった発火」を数えられる。

**終端理由は既存の `MARKET_STATE_INVALIDATED` を使い、新しい語を作らない**【提案】。上位設計書 §4.5 はこの語を「`REQUIRE_UNTIL_ORDER_REQUEST` の条件が成立しなくなって終端」と説明しているが、語そのものの意味は「市場状態によって無効になった」である。生成時点で許可が無かった場合と、確認待ちのあいだに条件が崩れた場合は、**遷移記録の `from_state`（生成時は `None`、待機中は `OPEN` か `CONFIRMED`）で区別できる**。語を増やすと、終端理由別の集計（D07）で同じ原因が2語に割れる。上位設計書 §4.5 の説明文を1行広げる改訂依頼として第12.1節に挙げる。**不採用**: 新しい終端理由 `MARKET_STATE_NOT_PERMITTED` を加える案（正本の語彙が8語になり、集計で「市場状態が原因」を数えるのに2語を足す必要が出る）、許可されない発火は取引機会を作らない案（ADR-0032 の「発火を捨てない」に反する）。

**市場状態が待機中なら、取引機会を出す評価も待機する**【提案】。役割フィールドには `on_missing` を宣言する場所が無いため、この1件だけはランタイムが規則を持つ（第6.8節の「待機の伝播」の例外）。市場状態の使用箇所が**同じ対象区間について**待機中である（第6.8節）とき、`market_state` 役割を持つ戦略の取引機会を出す評価は、同じ判断時点では行わず、その市場状態の待機に連なる待機要求として保持する。期限と `on_superseded` は**市場状態の入力が宣言したもの**を引き継ぐ。上位設計書 §4.3.14 の確定例（22:00 に日足が未公開で市場状態が待機し、22:00:02 に再開して同じ判断時点で 21:00〜22:00 の1時間足を対象に Trigger を評価して O1 を生成する）がこの経路である。

**最新の取引許可が一度も無い場合**は、`INPUT_MISSING_OR_INVALID` の欠損と同じに扱い、上の待機の規則が当てはまらなければ（市場状態が待機していなければ）**発火を遷移12 で終端する**【提案】。欠損を許可へ変換しない【合意済み】上位 §4.3.15。

**エンジン上の因果辺を1本足す**【提案】。市場状態の使用箇所から、取引機会を出す出力を持つ使用箇所へ辺を引く（第5.6節の検査 d）。明示の入力接続が無くてもランタイムが読むので、辺が無いと市場状態が Trigger より後に評価される評価順が通ってしまう。D04 §12 の因果辺の表への追加依頼として第12.1節に挙げる。

**不採用**（Q10 の他の選択肢）: 市場状態を Trigger 部品の明示入力として接続し部品が適用する案（待機が既存の欠損方針だけで説明でき、例外規則が1つ減る。一方で `breakout_trigger` の契約に許可の入力を足した版が要り、許可の判定規則が Trigger 部品の数だけ写される。上位設計書 §4.3.11 の「Trigger を評価し、更新後の MarketState で取引可否を判断する」という**確定した因果順序の読み方も変わる**）、両方を許して宣言で選ぶ案（同じ規則が2か所から決まる）。

### 7.7 後続確認と `CONFIRMED` 経路【提案】

`execution_filter` が `None` でない戦略（＝`entry_policy` が `AwaitConfirmation`、D04 §10.1）の状態機械を確定する。制御はすべて `ConfirmationPlan`（第5.3節・第5.6節）の1件を読んで行う。

**確認の開始足**【合意済み】上位設計書 §4.3.13。取引機会が**実際に生成された `decision_time` 時点で利用可能な、確認足の系列の最新の確定足**を開始足とし、その `BarKey` を `OpportunityLifecycle.confirmation_start_bar` に記録する。

| 事項 | 規則 |
|---|---|
| どう求めるか | `MarketDataView.latest_available(ConfirmationPlan.series, decision_time)` が返す足の `BarKey` |
| 読めない場合 | `latest_available` が欠損を返したら `confirmation_start_bar=None` とする。**欠損によって古い足へ黙って戻らない**【合意済み】同節。`include_start_bar` の判定は「開始足が無い」として扱い、次の確認足から確認する |
| 固定 | 開始足は**一度決めたら変えない**。追い越しで確認の対象足が進んでも、開始足の識別子は保存する【合意済み】上位 §4.3.14 |

**`include_start_bar` の適用**【提案】。確認評価の対象足が開始足と同じ `BarKey` であるとき、`include_start_bar=True` なら評価し、`False` なら評価しない（要求を作らない）。同じ判断時点で機会が生成され確認足も確定している場合がこれに当たる。**入力が利用可能であることは両方で必要**【合意済み】上位 §4.3.13。

**確認評価の起動**【提案】。第6.11節の規則により、確認足の確定ごとに**確認待ちの取引機会1件につき1要求**を作る。確認待ちとは `OpportunityState` が `OPEN` の機会である（`CONFIRMED` は確認済み、`ORDER_PENDING` は発注試行中なので対象にしない）。

| 対象にするか | 機会の状態 |
|---|---|
| する | `OPEN` かつ、対象足が開始足でないか `include_start_bar=True` |
| しない | `CONFIRMED` / `ORDER_PENDING` / `TERMINATED`、または対象足が開始足で `include_start_bar=False` |

**確認待ちの機会を数える時点は P4 である**【提案】。同じ `step` の中で、取引機会は P3（遷移1）で生まれ、確認は P4 で評価される。**確認要求を作るために機会を数えるのは P4 に入った時点**であり、同じ `step` の P3 で生まれたばかりの機会も対象に含む。これが「Trigger と同じ時刻に利用可能な15分足で確認してよい」（上位設計書 §7.1 の検証戦略 B）と、開始足での確認（`include_start_bar=True`）を成り立たせる。`step` の入口で数えると、同じ判断時点に生まれた機会が1本も確認されない。

**同じ機会に対する同じ論理確認を二重実行しない**【合意済み】上位 §4.3.12。同じ判断時点で「機会の生成」と「確認足の確定」が同時に起きても、その機会・その確認足について作る要求は1件である。第6.11節の数え方（対象1件につき1要求）がこれを満たす。

**確認試行の記録**【提案】。`OpportunityLifecycle.attempts` に `ConfirmationAttempt(opportunity_id, bar_key, request_id, outcome)` を1件ずつ積む。同じ機会が期限まで何本の確認足で何を返したかが、機会の記録だけから読める。

| `ConfirmationAttemptOutcome` | いつ |
|---|---|
| `CONFIRMED` | `confirmed=True` の確認結果が出た（遷移10） |
| `NOT_CONFIRMED` | `confirmed=False` の確認結果が出た。機会は `OPEN` のまま次の確認足を待つ |
| `SKIPPED` | 入力不足で評価を見送った（`Skipped`）。機会は `OPEN` のまま |
| `WAITING` | 入力不足で待機に入った（`Waiting`、第6.8節）。機会は `OPEN` のまま |
| `SUPERSEDED` | 確認足が追い越されて要求が閉じた（第6.10節）。機会は `OPEN` のまま |

**確認の追い越しは機会を失効させない**【合意済み】上位設計書 §4.3.14。確認足が進んだときは、**古い確認要求を `REQUEST_SUPERSEDED` で閉じ、同じ機会に新しい対象足の要求を作る**。古い問いの対象区間を上書きしない。**開始足の識別子は保存し、期限も黙って延長しない**。新しい要求の発行は通常の確認足の確定に従い、無関係な入力の到着による再開とは区別する（第6.10節）。

**期限の数え方**【提案】＋【要決定】Q19（遷移11 の発火条件）。`AwaitConfirmation.deadline` を機会の生成時点で絶対の形へ解決し、`OpportunityLifecycle.deadline_at` に持つ。**本数で数える期限をどの系列の確定足で数えるかは未決**であり（第15節 Q19）、下表は推奨案（確認足の系列）で書いてある。決定が「Trigger の系列のまま」であれば、下表の `series` と「確認足の系列」という語だけが差し替わり、遷移11 の始点・終点・記録する理由と、判定の順序（下の「判定の順序」）は変わらない。

| 期限型 | 解決 | 到達の判定 |
|---|---|---|
| `BarsDeadline(n)` | `WaitUntilBars(series=ConfirmationPlan.series, remaining=n)` | **確認足の系列**の `ScheduledBoundary` を1本受けるごとに `remaining` を1減らし、0 になった判断時点で到達。**機会を生成した足の次の確定足を1本目と数える**（`include_start_bar` の値によらない。開始足を確認に使えるかどうかと、期限を何本数えるかは別の設定である） |
| `DurationDeadline(d)` | `WaitUntilTime(created_decision_time + d)` | `decision_time >= その時刻` になった判断時点で到達 |

D04 §10.1 は `BarsDeadline` を「Trigger の系列の確定足で数える」と書いている【合意済み】。**本改訂は「確認足の系列で数える」に改めることを提案するが、確定はしない**（第11節の差異6、第15節 Q19）。期限は「あと何本の確認の機会があるか」を表すものであり、Trigger の系列（検証戦略 B なら1時間足）で数えると、確認足（15分足）の本数が時間足の比で変わってしまう。承認済みの D04 §10.1 の意味を書き換える選択であるため、人間の決定を待つ。決定が出るまで D04 §10.1 は改訂せず（第12.1節の依頼4 は見送り）、`AwaitConfirmation` の能力検査の解除もこの一点を残したまま行う（解除そのものは Q18 とは独立で、確認の起動と開始足の決め方は本節が確定している）。選択肢と推奨の理由は第15節 Q19 に置く。

**判定の順序**【提案】。期限の到達（遷移11）は**ライフサイクル検査**（D06 の `OPPORTUNITY_LIFECYCLE`、rank 4）で行い、確認の評価（遷移10、P4、rank 8）より**先**に判定する。同じ判断時点で期限に到達し、かつ確認条件も成立している場合は、**期限が勝って `EXPIRED` で終端する**。理由は、期限が「その判断時点より前に確認できなかった」ことを表すからであり、D06 が rank 4 を rank 8 より前に置いているのもこの順序のためである（D06 §4.1 の rank 4 の説明が本書の遷移8・11 を指している）。

**`CONFIRMED` から先**【合意済み】（v1.0 の Q2 決定）。`CONFIRMED` は中間状態であり、そこから進む先は3つだけである。

| 先 | 遷移 | 起こること |
|---|---|---|
| `ORDER_PENDING` | 4 | 注文意図と保護水準が揃い `EntryProposal` を渡した（P5） |
| `TERMINATED` | 8 | `REQUIRE_UNTIL_ORDER_REQUEST` の束縛が P5 直前の再検査で不成立になった（`MARKET_STATE_INVALIDATED`） |
| `TERMINATED` | 7・9・11 | 他の機会の注文が受け付けられた / run 末尾 / 確認期限の到達 |

**確認が成立した機会だけが注文意図の評価を起動する**【提案】。段階2 は取引機会の配送そのもの（`OnInputEvent`）が注文意図の部品を起動していた（第9節の `entry_order`）。段階3 の確認待ちの戦略では、**注文意図と保護水準の部品は確認結果（`confirmation_result@v1`）の配送で起動する**。確認結果は `EVENT` であり（第4.2節の役割ごとの `PortKind`）、`confirmed=False` の確認結果も配送される。そこで、**`confirmed=False` の確認結果は下流へ配送しない**【提案】。第6.2節の「終端した取引機会のイベントは下流へ配送しない」と同じ扱いで、**付番して判断履歴には残すが、`OnInputEvent` の起動判定の対象から外す**。配送すると、確認が成立していない機会から注文意図の組ができる。

**不採用**: `confirmed=False` を出力として出さない案（上位設計書 §4.3.15 の「`False` は条件未成立であり、入力不足・期限切れ・追い越しと区別する」に反する）、注文意図の部品が確認結果の中身を見て判断する案（同じ判定が確認部品と注文部品の2か所に分かれ、`confirmed=False` のときに何も出さない部品を書き忘れると発注される）。

**ADR-0031 の再検査は確認評価のたびにも走る**【合意済み】上位 §4.5。`REQUIRE_UNTIL_ORDER_REQUEST` の束縛は、**確認評価のたび**と `OrderRequest` 生成直前の2か所で読み直す。段階2 は後者だけが経路として存在した（遷移8 の「P4 と P5 直前」のうち P5 直前）。段階3 は前者（P4）も動く。不成立なら確認結果を出さずに遷移8 で終端し、その機会の確認試行には `ConfirmationAttempt` を積まない（確認を始める前に機会が終わったため）。

## 8. 約定後の利確の評価【提案】＋【合意済み】（Q7 決定、選択肢1）

検証戦略 A の利確は、約定価格と有効な損切り水準から決まるため、**約定が確定した後**に評価しなければならない。上位設計書 §4.3.12 の P0〜P5 は注文意図の生成で終わっており、約定後に戦略を評価する起動点が無い。

本書は `RuntimeEventNotice(POSITION_OPENED, position_id, opportunity_id)` を受けて `fixed_rr_take_profit` を評価する起動点を D06 へ要求する。**同じ判断時点の約定処理の後に `step` をもう一度呼ぶ**（Q7 決定）。次足まで待つと建玉が初期の利確水準を持たない時間帯ができ、T01 の紙上トレースに「利確なしの建玉」が現れる。**不採用**: 次の公開バッチの先頭で評価する案（次の足まで利確が無い）、エンジンが利確水準を直接計算する案（戦略の計算規則がエンジンへ漏れ、部品として差し替えられなくなる。上位設計書 §4.7.1 は初期の利確を Exit の責務としている）。

通知は**1件につき1つの評価要求**を作り、その `position_id` を `CurrentContext` の入力解決まで運ぶ（第6.2節）。同じバッチで複数の建玉が生まれても、どの建玉について評価したかが評価記録と管理要求から一意に読める。

D06 へ委ねるのは、この起動点に与えるフェーズ順位と、約定処理の後のどこに挿すかだけである。**約定処理より前に置く構成は本書が排除する**（約定価格と建玉が無ければ利確水準を計算できない）。この起動点で評価できる情報は、その時点の `RuntimeContextView` と、`decision_time` 以前に公開済みの市場データに限る。約定によって生まれた建玉を読むのであり、未来の価格を読むのではない。D04 §12 の因果辺（注文→約定起動、注文→建玉参照）は、この起動点が帰還路を作らないことをコンパイル時に保証する。

## 9. 検証戦略ごとの最小範囲と紙上トレース

### 9.1 段階2の最小範囲（検証戦略 A）と T01

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

### 9.2 段階3 の最小範囲（検証戦略 B）【提案】

上位設計書 §7.1 の検証戦略 B を、本改訂の部品と使用箇所で宣言できることが段階3 の必要十分条件である（全体計画 §8.2 の段階3 の完了条件「戦略 B を同じ基盤で記述でき、時刻境界と取消理由を trace できる。遅延シナリオ別の差分を追跡できる」）。系列は USDJPY の日足・1時間足・15分足（いずれも bid）とする。

| 使用箇所 | 契約 | 起動条件 | 入力の接続 | 出力 | 役割 |
|---|---|---|---|---|---|
| `daily_ema` | `ema` v1（`period=20`、`window_bars=60`） | `OnBarClose("d1", USDJPY/1d/bid)` | `prices` ← `MarketDataRef(USDJPY/1d/bid, CLOSE)` | `value`: `price@v1` | — |
| `daily_above_ema` | `price_compare` v1（`operator=GT`） | `OnBarClose("d1", USDJPY/1d/bid)` | `left` ← `MarketDataRef(USDJPY/1d/bid, CLOSE)`、`right` ← `daily_ema.value` | `condition`: `condition_state@v1` | — |
| `no_short` | `constant_condition` v1（`value=False`） | `OnBarClose("d1", USDJPY/1d/bid)` | なし | `condition`: `condition_state@v1` | — |
| `market_state` | `permission_from_condition` v1 | `OnBarClose("d1", USDJPY/1d/bid)` | `long_allowed` ← `daily_above_ema.condition`、`short_allowed` ← `no_short.condition` | `permission`: `market_permission@v1` | `market_state` |
| `entry_trigger` | `breakout_trigger` v2（`direction=LONG`。`level` の `on_missing` が `WaitForInput`） | `OnBarClose("h1", USDJPY/1h/bid)` | `price` ← `MarketDataRef(USDJPY/1h/bid, CLOSE)`、`level` ← `MarketDataRef(USDJPY/1d/bid, HIGH)` | `opportunity`: `opportunity@v1` | `trigger` |
| `m15_ema` | `ema` v1（`period=20`、`window_bars=60`） | `OnBarClose("m15", USDJPY/15m/bid)` | `prices` ← `MarketDataRef(USDJPY/15m/bid, CLOSE)` | `value`: `price@v1` | — |
| `m15_above_ema` | `price_compare` v1（`operator=GT`） | `OnBarClose("m15", USDJPY/15m/bid)` | `left` ← `MarketDataRef(USDJPY/15m/bid, CLOSE)`、`right` ← `m15_ema.value` | `condition`: `condition_state@v1` | — |
| `entry_filter` | `condition_filter` v1（`include_start_bar=True`） | `OnBarClose("m15", USDJPY/15m/bid)` | `condition` ← `m15_above_ema.condition`、`opportunity` ← `RuntimeInputRef(OPPORTUNITY)` | `confirmation`: `confirmation_result@v1` | `execution_filter` |
| `entry_order` | `market_order_intent` v2（入力が確認結果） | `OnInputEvent("conf", "confirmation")` | `confirmation` ← `entry_filter.confirmation` | `intent`: `order_intent@v1` | `order` |
| `initial_stop` | `level_stop_loss` v2（入力が確認結果） | `OnInputEvent("conf", "confirmation")` | `confirmation` ← `entry_filter.confirmation`、`level` ← `stop_level.level` | `protection`: `protection_levels@v1` | `protection` |
| `stop_level` | `extreme_price` v1（`mode=MIN`、`lookback=20`） | `OnBarClose("h1", USDJPY/1h/bid)` | `prices` ← `MarketDataRef(USDJPY/1h/bid, LOW)` | `level`: `price@v1` | — |
| `trailing` | `trailing_stop` v1 | `OnBarClose("h1", USDJPY/1h/bid)` | `position` ← `RuntimeInputRef(POSITION)`、`level` ← `stop_level.level` | `action`: `management_action@v1` | `exit` |

戦略全体の宣言は次のとおりである。

| フィールド | 値 |
|---|---|
| `entry_policy` | `AwaitConfirmation(deadline=BarsDeadline(bars=4), on_deadline=EXPIRE)`（15分足4本＝1時間） |
| `opportunity_validity` | `bindings=(ValidityBinding(source=daily_above_ema.condition, mode=REQUIRE_UNTIL_ORDER_REQUEST, on_missing=SkipEvaluation),)` |
| `opportunity_concurrency` | `(max_active=1, on_new_trigger=KEEP_EXISTING, on_order_accepted=KEEP_OTHERS)` |
| `market_state` / `trigger` / `execution_filter` / `order` / `protection` / `exit` | 上表の役割の列 |

**注文意図と保護水準の部品は版を上げる**【提案】。段階2 の `market_order_intent` v1 と `level_stop_loss` v1 は取引機会の配送（`opportunity@v1` の `DeliveredEvent`）で起動する。段階3 の確認待ちの戦略では**確認結果の配送で起動する**（第7.7節）ため、入力の型と起動条件が変わる。読み取り条件と起動条件は契約が固定するので（D04 §4.1・§8）、版を上げるほかない。v2 は `confirmation: confirmation_result@v1 / EVENT / DeliveredEvent / arity=(1,1)` を入力に持ち、`AllowedInputEvent(("confirmation",))` を許可する。注文意図に必要な銘柄と方向は**確認結果ではなく機会から来る**ため、v2 も `RuntimeInputRef(OPPORTUNITY)` の入力を1つ持つ（第6.11節。評価要求の `opportunity_id` は確認結果の配送から引き継がれる。第6.2節 手順3）。

**`breakout_trigger` も版を上げる**（第4.9節）。日足の高値を待つために `level` 入力の `on_missing` が `WaitForInput(deadline=BarsDeadline(bars=1), on_deadline=SKIP_EVALUATION, on_superseded=EXPIRE_REQUEST)` になる（日足1本ぶん待つ）。`on_superseded=EXPIRE_REQUEST` は上位設計書 §4.3.14 の「Trigger の標準方針: 古い対象足が追い越されたら、その待機評価を失効させる」の具体値である。

**期間値を含む宣言はダイジェストを計算できないため、本数で数える形に統一する**【合意済み】D02 §9.3・D04 §13.2・本書 §5.5。起草時はこの待機期限を `DurationDeadline("30m")` と書いていたが、期間値を持つ宣言は内容ハッシュを計算できず、実験として固定できない。検証戦略 B は**期間値を使わない形（`BarsDeadline` と `BarsWindow` だけ）で書ける**ので、上のとおり本数で数える形に確定した。期間の符号化規則を共通カーネル（D02 §9.3）へ足す改訂は、これにより段階3 の受入れの前提ではなくなったため**見送る**（第12.1節）。待機期限や確認期限を「30分」のように時間で書きたい戦略は段階3 でも書けないままであり、その制約は段階4 以降へ持ち越す。

評価順は第5.4節の規則で一意に定まり、`daily_ema` → `daily_above_ema` → `m15_ema` → `m15_above_ema` → `no_short` → `stop_level` → `market_state` → `entry_trigger` → `entry_filter` → `entry_order` → `initial_stop` → `trailing` の順になる（同じ段の中は `instance_id` 順。`market_state` → `entry_trigger` は第7.6節の因果辺、`entry_order` → `trailing` は D04 §12 の因果辺）。

### 9.3 遅延シナリオ4ケースが判断履歴にどう現れるか【提案】

上位設計書 §4.3.13 が「最初に検証するケース」として挙げる4件（遅延なし / 日足のみ2秒遅延 / 待機期限を超える遅延 / 次足まで到着しない場合）について、**同じ宣言・同じ価格で何が変わるか**を示す。遅延モデルは実験のデータ公開設定（D03 §3.6 の `DelayScenario`）にあり、OHLC と対象区間は変わらず `available_at` と配送順序だけが変わる【合意済み】同節。

判断時刻 T を、日足・1時間足・15分足がそろって終了する時点とする（上位設計書 §4.3.12 の例）。`entry_trigger` の `level` は日足の高値であり、日足が来ないと読めない。

| # | シナリオ | 日足の `available_at` | 判断履歴に出るもの |
|---|---|---|---|
| 1 | 遅延なし | T | `daily_ema` → `market_state` → `entry_trigger` がすべて T で評価される。取引機会 O1 が `decision_time=T` で生成（遷移1）。同じ T の15分足で確認が成立すれば遷移10、注文意図と保護水準が出て遷移4 |
| 2 | 日足のみ2秒遅延 | T+2秒 | **T**: `daily_ema` は `latest_available` が `LATEST_BAR_UNAVAILABLE` を返し、宣言が待機なら `Waiting` の評価記録と `WaitEvent(WAIT_STARTED)`。`market_state` も同じ区間で待機。`entry_trigger` は第7.6節の規則で待機に連なる。`m15_ema` は独立なので通常どおり評価される。**T+2秒**: `WaitEvent(INPUT_ARRIVED)` と `WaitEvent(RESUMED)`、固定した日足を読み直して `daily_ema` が評価され、`market_state` と `entry_trigger` が続く。O1 の `created_decision_time` は **T+2秒**であり、`signal_interval` は **T で終わる1時間足の区間**のまま。確認の開始足は「T+2秒 時点で利用可能な最新の15分足」＝T で確定した15分足 |
| 3 | 待機期限を超える遅延 | 期限より後 | **T**: 2 と同じく待機に入る。**期限に到達した判断時点**: `WaitEvent(DEADLINE_REACHED)` と、`on_deadline` に従って `Skipped`（`SKIP_EVALUATION`）または `Failed(DATA_ERROR)`（`ERROR`）の評価記録。取引機会は生成されず、その時刻の注文数は 0。日足がその後に届いても**この要求は復活しない**【合意済み】上位 §4.3.14 |
| 4 | 次足まで到着しない | 次の日足の予定時刻より後 | **T**: 待機に入る。**次の1時間足が確定した判断時点**: `entry_trigger` の待機要求が追い越され、`on_superseded=EXPIRE_REQUEST` なら `Superseded(by_request_id)` の評価記録と `WaitEvent(SUPERSEDED)`、理由コードは `REQUEST_SUPERSEDED`。新しい1時間足を対象とする要求が改めて待機に入る。**古い突破を後から実行しない**【合意済み】同節 |

**4ケースの差分が読める材料**【提案】。上位設計書 §4.3.13 は「使用した観測・評価時刻・確認結果・注文数の差を追跡できるようにする」ことを要求している。対応は次のとおりで、**新しい記録の型は要らない**。

| 追跡したいもの | どこに出るか |
|---|---|
| 使用した観測 | `Observation.subject` と `observation_interval`（第6.7節）、遡ったときは `SubstitutedInput.used_bar_key`（第6.9節） |
| 評価時刻 | `EvaluationRecord.decision_time`（再開した判断時刻）と `WaitEvent(WAIT_STARTED).at`（待機を始めた処理点） |
| 確認結果 | `OutputRecord[ConfirmationResult]` と `ConfirmationAttempt`（第7.7節） |
| 注文数の差 | `EntryProposal` の件数と、取引機会の遷移記録（終端理由別） |

**同じシナリオの再実行は同じ結果になる**【合意済み】上位 §4.3.13。本改訂が足した経路のうち決定論に関わるのは、待機要求の並び（第6.10節の順序）、対象ごとの要求の並び（第6.11節の `attempt_index`）、確認試行の並び（第7.7節、確認足の `BarKey` 昇順）の3つであり、いずれも入力から一意に決まる規則を置いた。

**遅延シナリオ別の処理順そのものの検証は D08（テスト戦略）と段階3 の受入れテストの範囲である**【合意済み】D06 §1.2 の行1。本節が決めるのは「何が判断履歴に現れるか」までである。

### 9.4 T02（検証戦略 B の紙上トレース）で追う経路【提案】

T01（検証戦略 A の紙上トレース）は §11.2 で「検証戦略 B のうち戦略ランタイム側は D05 v0.2 待ち」と7件を挙げ、「B を紙上で最後まで通せるようになるのは D05 v0.2 の承認後であり、そこで T01 に B の時刻表を追記する」としている【合意済み】T01 §11.2・§14。本改訂の承認後に追記する経路を、**追える材料がそろったことの確認として**ここに列挙する。文書名は T01 への追記でも新しい T02 でもよく、**置き場所は起草時に決める**（第14節）。

| # | 追う経路 | 本改訂のどこが材料か | T01 §11.2 のどの項目を閉じるか |
|---|---|---|---|
| 1 | 遅延なしで、同じ判断時点に市場状態 → 取引機会 → 確認成立 → 注文意図まで進む | §7.6・§7.7・§9.3 の1 | 日足 EMA の評価、市場状態の配送、後続確認 |
| 2 | 開始足で未確認、次の15分足で確認が成立する | §7.7（`include_start_bar` と確認試行の記録） | 後続確認 |
| 3 | 期限（15分足4本）に到達して `EXPIRED` で終端する | §7.7 の期限の数え方、遷移11 | 後続確認 |
| 4 | 確認待ちのあいだに日足の条件が崩れ `MARKET_STATE_INVALIDATED` で終端する | §7.3・§7.7 の末尾（確認評価のたびの再検査） | 後続確認 |
| 5 | 日足が2秒遅れ、市場状態が待機して再開し、同じ判断時点で機会が生成される | §6.8・§7.6・§9.3 の2 | 待機、遅延シナリオ |
| 6 | 待機期限を超え、`Skipped` で決着して注文が 0 件になる | §6.8 の期限切れ、§9.3 の3 | 待機、遅延シナリオ |
| 7 | 日足が次足まで届かず、1時間足の待機要求が追い越される | §6.10・§9.3 の4 | 追い越し |
| 8 | 建玉の保有中に1時間足ごとに `UpdateStop` が出て、水準が不利な向きには動かない | §4.10・§6.11 | 1時間足での管理水準の更新 |
| 9 | 市場状態が買いを許さない判断時点で、発火が記録されたうえで終端する（遷移12） | §7.6 | （本改訂で新しく生じる経路） |

**D08（テスト戦略）と D06 v0.3 が要るのは 5〜8 の判断履歴への保存形式だけである**【提案】。待機記録・追い越し記録の trace 表は D06 v0.3（D06 §12）、遅延シナリオ4ケースの処理順の検証は D08（D06 §1.2 の行1）が担当する。紙上トレース自体は本改訂の型だけで追える。

## 10. 対象外

本節は**時期**の線引き（その段階で作らないもの）であり、第1.2節は**担当**の線引き（本書が決めないもの）である。

### 10.1 段階2 で作らず、本改訂（v2.0）が埋めたもの

v1.4 の本節が「本書 v0.2」としていた項目のうち、次は本改訂が扱った。

| 項目 | 本改訂のどこ |
|---|---|
| 待機（`WAIT_FOR_INPUT`）の意味論と宣言形、`USE_PREVIOUS` の遡り | 第6.8節・第6.9節 |
| 評価要求の追い越し（`REQUEST_SUPERSEDED`）と `on_superseded` の既定 | 第6.10節 |
| 後続確認（`AwaitConfirmation`・ExecutionFilter）と確認期限の数え方、開始足の扱い | 第7.7節 |
| 合成部品（AND / OR / 遷移検出） | 第4.6節 |
| 指標部品（EMA・ATR）と、その初期化・更新規則、NumPy を使う部品の特定 | 第4.5節 |
| Feature の鮮度伝播と複数系列 Feature の鮮度 | 第6.7節 |
| 追加の時刻制約（観測区間の一致）を守らせる仕組み | 第6.7節 |
| 上流の出力を履歴窓で読む仕組み（保持と打ち切り。Q18 決定、選択肢2） | 第6.12節 |

### 10.2 段階3 でも作らないもの

- 合成部品のうち **N 本継続**と「**A の後 N 本以内の B**」 → 段階4 以降（第4.6節、Q12 決定）。検証戦略 B が使わない。段階3 で作る出力の履歴窓の仕組み（第6.12節）を使えば新しいデータ型も状態も要らずに書けるが、**部品そのものは段階3 では作らない**。
- **経過時間の窓（`DurationWindow`）で上流の出力を読む接続** → 段階4 以降（第5.6節の検査 f、第6.12節）。保持本数をコンパイル時に導けないため拒否を続ける。本数で数える窓（`BarsWindow`）は段階3 で解除した（Q18 決定、選択肢2）。
- **ウォームアップ本数（`WarmupSpec`）を守らせる仕組み** → 段階4 以降（第11節の2）。段階3 の指標部品は履歴窓の不足で成立する。空でない `warmup` は能力検査で拒否を続ける（第5.6節）。
- **期間 Exit**（保有期間で決済する部品） → 段階4 以降。検証戦略 B に現れない。`PositionContext.opened_at` は D06 が既に供給している（D06 §8.4）ので、足すときに D06 の改訂は要らない。
- **`POSITION_OPENED` 以外の実行時イベント**（決済通知、保護水準の更新通知）での評価起動 → 段階4 以降（第5.6節）。トレーリングは足の確定で起動する（第6.11節）。
- **役割フィールドが読む出力への鮮度の上限**（`market_state` の `max_age` に相当するもの） → 段階4 以降（第7.6節）。宣言する場所（役割フィールドに読み取り条件を持たせるか）の設計が要る。
- 再審査設定の型・配置・回数上限 → 段階6・D10（能力検査で拒否を続ける）。
- 部品状態の run 跨ぎの保存・復元 → 段階5 以降。
- 学習する部品（fit / 推論分離）、探索空間の宣言 → D09 以降。

## 11. 上位文書との差異

1. **`position_context@v1` が供給する項目**。D04 §5 は段階2で読む項目を「約定価格・方向・数量」と書いているが、固定リスクリワード比の利確には**有効な損切り水準**が要る（第4.3節(5)）。供給範囲の正本は D06 であり、第12節で引き渡す。D04 の次回改訂で §5 の例示に1項目足す。
2. **`WarmupSpec` を段階2・段階3 の部品で使わない**。`WarmupSpec.series` は具体的な `SeriesId` を持つ（D04 §9.2）ため、系列を使用箇所が選ぶ再利用可能な部品の契約には書けない。段階2 はウォームアップ不足を履歴読み取りの `WARMUP_INSUFFICIENT` で判定でき（第4.3節）、完了条件「warmup 中の注文ゼロ」を満たす。**段階3 で EMA を足しても同じである**（第4.5節。EMA は状態を持たず窓だけで決まるため、窓の不足がそのままウォームアップ不足になる）。したがって**空でない `TemporalConstraints` の拒否のうち、ウォームアップ本数の部分は段階3 でも続ける**。一方**観測区間の一致（`AlignmentRequirement`）は段階3 で解除する**（第6.7節が守らせる仕組みを置いたため）。拒否一覧を2つに分ける改訂を D04 §12 へ依頼する（第12.1節）。

3. **初版カタログの品揃え**。全体計画 §5.3.3 が挙げる12部品は検証戦略 A と B の合計であり、段階2（戦略 A）に必要なのは5部品である（第4.3節、Q8 決定）。§5.3.3 の一覧は【提案】の印が付いており、決定との矛盾ではない。
4. **`runtime/` のモジュール**。D01 §7.2 の一覧のうち `waiting.py` / `supersession.py` は段階3で作る（第2節）。一覧の改訂は不要（初期構成であり後続文書が追加・分割できる、D01 §7.2）。

5. **置換の対象から発注試行中の機会を外す**。D04 §10.3 は「最も古い有効な取引機会を1件終端する」と定め、適用の時点と生成時点の項目名を本書へ委ねている。本書は状態機械を置いたうえで、**注文要求を既に渡した機会（`ORDER_PENDING`）を置換の対象から外す**（第7.4節）。D04 の時点では非終端の状態が無く、この区別を書けなかったためであり、「最も古いものを選ぶ」という鍵そのものは変えていない。

6. **確認期限を数える系列を「Trigger の系列」から「確認足の系列」へ改めるかどうか**（段階3、第7.7節）【要決定】Q19。D04 §10.1 は `BarsDeadline(bars)` を「Trigger の系列の確定足で数える」と書いている。期限は「あと何回の確認の機会があるか」を表すものであり、Trigger の系列で数えると、同じ `bars=2` が確認の回数として2回にも8回にもなる（1時間足 Trigger と15分足の確認なら4倍）。宣言から確認の回数が読めないため、本書は確認足の系列で数える形を推奨する。**ただしこれは承認済みの他文書（D04 §10.1）の意味を書き換える設計の選択であり、本改訂では確定しない**。第15節の要決定 Q19 として選択肢を並べ、決定が出るまで D04 §10.1 は改訂しない（第12.1節の依頼4 は見送り）。**これは段階2 の確定（第1.2節の「段階2 で確定」の列）ではなく、段階2 では能力検査で拒否していて一度も動いていない宣言の意味を、動かす前に決め直すものである**。

7. **市場状態の取引許可をランタイムが役割フィールド経由で適用する**（段階3、第7.6節）。上位設計書 §4.3.11 の確定した因果順序（「Trigger を評価し、更新後の MarketState で取引可否を判断する」）を実施する規則であり、食い違いではない。ただし本書 §4.2 が書いた「役割フィールドが指す出力はエンジンが役割フィールド経由で読むものであり、`InputBinding` では読まない」という規則に**市場状態の適用という具体的な中身を与える**ものなので、ここに挙げる。第15節 Q10 で選択肢を提示している。

8. **`ConfirmationResult` を部品の戻り値と配送される型に分ける**（段階3、第4.8節）。上位設計書 §4.3.15 が確定した3フィールドは変えない。部品が返すのは `ConfirmationOutcome`（成否と根拠値）であり、識別子と確認足の区間はランタイムが付ける。取引機会について v1.0 が決めた切り分け（第4.2節）と同じ手法であり、正本のフィールド構成を変えないための補完である。

9. **`VALUE` の出力をランタイムが `Observation` で包む**（段階3、第6.7節）。上位設計書 §4.3.15 は「市場の状態は `OutputRecord[Observation[MarketPermission]]` とする。イベントまで一律に `Observation` で包む必要はない」と書いており、`VALUE` の出力を包むことを禁じてはいない。本改訂は包む範囲を `VALUE` まで広げることを提案する（第15節 Q13）。§4.3.15 の確定したフィールドは変えない。

10. **段階2 が確定した「出力は最新1件だけ保持する」を、履歴窓で読まれる出力に限って改める**（段階3、第6.5節・第6.12節）【合意済み】（Q18 決定、選択肢2）。第6.5節（出力の保持）と第6.3節（出力参照を履歴窓で読む接続の拒否）は第1.2節の「段階2 で確定」の列に属する。第1.2節は「段階2 で確定の内容を段階3 の都合で書き換える必要が出た場合は、黙って直さず第11節に差異として挙げ、要決定に上げる」と定めており、本改訂は起草時に Q18 としてそれを上げた。**2026-09-22 に人間が選択肢2（段階3 で作る）を選んだ**ため、その決定に従って改めている。段階2 の実行経路（検証戦略 A）は履歴窓で出力を読む接続を1つも持たないので、保持本数の計画は空になり、挙動は変わらない。

上記以外に、上位設計書・全体計画書・ADR・D01〜D04 と食い違う提案はない。

## 12. 他文書への引き渡し

第1.2節の境界表を正本とし、ここには**境界表に載らない細目**だけを挙げる。

| 引き渡し先 | 項目 |
|---|---|
| D06 | `position_context@v1` の項目に**有効な損切り水準**を含めること（第11節の1）。`OrderIntent.expiry=None` のときに適用する既定の有効時間（第4.3節(3)）。`AdmissionNotice` を返すフェーズと、`attempt_id` の対応付け（第7.2節）。約定後の評価起動点のフェーズ順位と挿す位置（第8節。「同じ判断時点の約定処理の後」という本書の決定を満たす範囲で）。`PhaseSet` への P1〜P5 とライフサイクル検査・約定後起動点の登録（D02 §3.3）。`EntryProposal.intent_output_id` / `protection_output_id` と `ManagementRequest.source_output_id` を注文要求のどの項目へ移すか（v1.2 で足した項目。D06 §6.1 が受け取り先を確定済み） |
| D07 | 取引機会の終端理由別の集計と、`Skipped` の診断理由別の集計（全体計画 §7.5 の「診断」） |
| D04（次回改訂） | §12 の「拒否は `ReasonCode` 付きの構造エラー」を、コンパイラ専用の区分 `CompileRejection` を使う形へ言い換える（第5.2節、Q9 決定）。§5 の `position_context@v1` の例示に損切り水準を足す件（第11節の1）は、**第12.1節の依頼9 として 2026-09-22 に反映済み**（D04 v1.9） |
| `common`（段階2の実装） | `ReasonCode` 列挙への取引機会の終端理由5件と `REQUEST_SUPERSEDED` の追加。設計側は D02 §8.1（v1.3）で確定済み【合意済み】D04 §18 |
| `strategy`（**段階3 の実装への改訂依頼**） | 段階2 の実装（PR #18）と本改訂の設計が食い違う箇所はないが、段階3 では次を足す必要がある。いずれも本改訂で設計が確定した範囲である。(1) `declarations/missing.py` に「入力を待つ」（`WaitForInput`）と「過去値へ遡る」（`UsePrevious`）の2区分と2つの列挙を足し、`declarations/contract.py` の保存形式の版（`SCHEMA_VERSION`）を 1 から 2 へ上げる（D04 §6.3 v1.9）。(2) `declarations/refs.py` の `RuntimeTarget` に取引機会（`OPPORTUNITY`）を足す（D04 §4.3 v1.9）。(3) `records/payloads.py` に `ConfirmationOutcome` と `UpdateStop` を足す（第4.2節）。(4) `compiler/capability.py` の拒否一覧を D04 §12 の新しい2表に合わせて分ける。(5) `runtime` に `waiting.py` / `supersession.py` / `confirmation.py` / `output_history.py` を足す（第2節）。**Q19〜Q21 が決まるまで実装しない部分**: 確認期限の本数の数え方（Q19）、保持した出力の1件の数え方（Q20）、待機から再開したときの窓の基準（Q21） |

### 12.1 段階3（v2.0）のための他文書への改訂（**反映済み**。2026-09-22、PR #22）

起草時は「本改訂が承認されたら同じ PR か直後の PR で改訂が要る」18件の**依頼**だった。**Q10〜Q18 の決定が 2026-09-22 に出たため、本 PR で正本へ反映した**。内訳は**反映16件・見送り2件**である。見送りの2件は、片方が新しい要決定（Q19）の結果待ち、もう片方が反映の条件が成立しなかったものであり、どちらも理由を下に書く。

| 宛先 | 版 | 反映 | 見送り |
|---|---|---|---|
| 戦略宣言モデル（D04） | v1.8 → **v1.9** | 8件 | 1件（依頼4。Q19 の決定待ち） |
| バックテスト縦断（D06） | v1.4 → **v1.5** | 5件 | 0件 |
| 市場データと時刻（D03） | v1.5 → **v1.6** | 2件 | 0件 |
| 上位設計書 | — | 1件 | 0件 |
| 共通カーネル（D02） | v1.7 → **v1.8** | 0件（条件つきの1件は見送り） | 1件（依頼1。条件が成立しなかった） |

**D04（戦略宣言モデル）: 8件を反映、1件を見送り**

| # | 改訂 | 本書のどこ | 状態 |
|---|---|---|---|
| 1 | §6.3 の `MissingInputPolicy` に `WaitForInput` / `UsePrevious` の2区分と、`WaitDeadlineAction` / `OnSuperseded` の2つの列挙を足し、§3.1 の型表にも載せ、**保存形式の版（`schema_version`）を 2 へ上げた** | §6.8・§6.9 | **反映**（Q15・Q16 が選択肢1 で決まり、フィールドが推奨案どおり確定したため） |
| 2 | §4.3 の `RuntimeTarget` に `OPPORTUNITY` を足した（対応するデータ型は登録済みの `opportunity@v1`） | §6.11 | **反映**（Q14 が選択肢1） |
| 3 | §11.2 の `ManagementAction` の要求種別に `UPDATE_STOP`（`UpdateStop(stop_loss: Price)`）を足した | §4.2・§4.10 | **反映**（Q12 が選択肢1 でトレーリングを段階3 で作る） |
| 4 | §10.1 の `BarsDeadline` の説明を「Trigger の系列の確定足で数える」から「確認足の系列の確定足で数える」へ改める | §7.7・第11節の6 | **見送り**。確認期限を数える系列の変更は**設計の選択**であり、本改訂では確定しない（第15節 Q19）。決定が出てから D04 §10.1 を改訂する。見送っても段階3 の他の改訂は進む（期限の始点・終点・記録する理由は変わらないため） |
| 5 | §12 の段階2 拒否一覧を「段階3 で解除するもの」と「段階3 でも拒否を続けるもの」に分けた | §5.6 | **反映**。Q18 が選択肢2 で決まったため、出力参照を履歴窓で読む接続は**本数で数える窓だけ解除**の側に置いた |
| 6 | §12 の拒否一覧の「空でない `TemporalConstraints`」を、ウォームアップ本数（拒否を続ける）と観測区間の一致（解除）に分けた | §6.7・第11節の2 | **反映** |
| 7 | §12 の検査一覧に段階3 の検査6件を足した（確認部品の `include_start_bar`、確認足の系列の一意性、待機・遡りと読み方の組合せ、市場状態の因果辺、パラメータどうしの関係、**出力参照の履歴窓が本数で数える窓であること**） | §5.6 | **反映**。起草時は5件だったが、Q18 の決定で検査 f が加わり6件になった |
| 8 | §12 の「エンジン上の因果辺」の表に、市場状態の使用箇所から取引機会を出す使用箇所への辺を足した | §7.6 | **反映**（Q10 が選択肢1） |
| 9 | §5 の `position_context@v1` の例示に有効な損切り水準を足した（段階2 から持ち越していた改訂依頼） | 第11節の1 | **反映** |

**D06（バックテスト縦断）: 5件を反映**

| # | 改訂 | 本書のどこ | 状態 |
|---|---|---|---|
| 1 | §4.1 の rank 4 `OPPORTUNITY_LIFECYCLE` の内容に、待機中の評価要求の期限・追い越しの検査を足した（新しいフェーズは足さない） | §6.8・§6.10 | **反映** |
| 2 | §4.4 の「戦略ランタイムが返した記録の処理点はエンジンの時計で番号を振り直す」の対象に、待機の出来事（`WaitEvent.at`）を足した | §6.8 | **反映** |
| 3 | §8.3 に `UpdateStop`（トレーリング）の適用意味論を足した（どの執行足から有効か、同じ判断時点の決済要求との競合、価格刻みへの丸め方向） | §4.10 | **反映** |
| 4 | §9.2 の trace の表に、段階3 の4表（待機の出来事・遡り・確認試行・出力の履歴の読み取り根拠）の扱いを足した | §6.8〜6.10・§6.12・§7.7 | **反映**。出力の履歴については Q18 の決定を受けて「新しい表を足さない」ことを明記した（本書 §6.12） |
| 5 | §10.1 の末尾処理に、run 末尾に残った待機要求の終端を足した | §6.1・§6.8 | **反映** |

**D03（市場データと時刻）: 2件を反映**

| # | 改訂 | 本書のどこ | 状態 |
|---|---|---|---|
| 1 | §6.2 に、**末尾の足を指定して履歴窓を読む操作**を足した。待機からの再開では「最初の対象足から解決した窓」を読み直す必要があるが、`history(series, window, at, end_offset_bars)` は判断時刻 `at` を基準にしか読めず、再開時には窓が後ろへずれる | §6.8 | **反映** |
| 2 | §7.2 の「`OnBarClose` の結び付け（案）」を**確定**にした | §6.8 | **反映** |

**上位設計書: 1件を反映**

| # | 改訂 | 本書のどこ | 状態 |
|---|---|---|---|
| 1 | §4.5 の終端理由の表で、`MARKET_STATE_INVALIDATED` の説明を「市場状態によって無効になって終端（継続成立を要求した条件が崩れた場合と、生成時点で市場状態が取引を許していなかった場合）」へ広げた。新しい語は作っていない | §7.6 | **反映**（Q10 が選択肢1）。同じ説明の写しが §4.7.14 と D02 §8.1 にもあるため、**同じ PR で3か所を揃えた**（語彙を足すときは §4.7.14 と D02 §8.1 を同じ PR で更新する、という D02 §8.1 の規則に合わせた扱い） |

**D02（共通カーネル）: 条件つきの1件は見送り**

| # | 改訂 | 本書のどこ | 状態 |
|---|---|---|---|
| 1 | §9.3 の正規化エンコードに期間（`timedelta`）の符号化規則を足すかどうか | §5.5・§9.2 | **見送り**。反映の条件は「検証戦略 B が期間で期限や窓を宣言する必要があるか」であり、**成立しなかった**。検証戦略 B は本数で数える宣言だけで書けることを第9.2節で確かめ、待機期限の例も `BarsDeadline(bars=1)` へ確定したため、段階3 の受入れは符号化規則が無くても止まらない。符号化規則を足すこと自体は設計の選択（正規化の単位と表現をどう決めるか）を伴うので、D02 の次回改訂へ引き渡したままにする。**残る制約**: 待機期限や確認期限を「30分」のように時間で書きたい戦略は、段階3 でも実験として固定できない |

**反映しなかったことの影響**【提案】。見送った2件はいずれも段階3 の実装を止めない。依頼4（確認期限の系列）は Q19 が決まるまで D04 §10.1 の文言が「Trigger の系列」のままであり、**その文言のまま実装すると本書 §7.7 の推奨と食い違う**ので、Q19 が決まるまで確認期限の本数の数え方は実装しない。依頼1（期間の符号化）は、期間値を持つ宣言を能力検査が通す／通さないの問題ではなく、内容ハッシュが計算できないという既存の制約がそのまま残るだけである。

## 13. 段階2 の承認時の確認事項（2026-09-21 承認: Q1〜Q9 をすべて選択肢1 で決定。**決定済み**）

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

## 14. 本書の版と承認の単位

**呼び方について**: 他の文書（全体計画 §8.1、D04 §1.2、D06 §12、T01 §11.2 など）は本改訂を「**D05 v0.2**」と呼んでいる。本書の版番号は v1.4 まで進んでいるため、**本改訂の版を v2.0 とし、他文書の「v0.2」は本改訂を指す**ものとして読む。承認後に、他文書の「v0.2」という呼び方を「v2.0」へ揃える改訂を出す（第12.1節には含めていない。内容ではなく呼び方だけの整理であり、承認の可否に影響しないため）。

| 版 | 範囲 | 状態 |
|---|---|---|
| v1.0〜v1.4 | 段階2 の最小ランタイム範囲（検証戦略 A、紙上トレース T01） | **承認済み**（2026-09-21、PR #15。以後の改訂は人間の決定で本文へ確定） |
| **v2.0**（本改訂） | 段階3 の範囲（第1.2節の「段階3 で本改訂（v2.0）が決めること」の列）。複数の時間足・市場状態・後続確認・待機・遡り・追い越し・上流の出力の履歴窓・段階3 のカタログ・検証戦略 B | **Q10〜Q18 は 2026-09-22 に決定済み**（第15節）。第12.1節の他文書への反映も同じ日に済ませた。**残る未決は Q19〜Q21** で、いずれも段階3 の実装の一部（確認期限の本数の数え方、出力の履歴の1件の数え方と待機からの再開）を止めるだけであり、本改訂の他の範囲は決定済みである |
| v2.1 以降 | 第10.2節が段階4 以降へ送った項目と、Q19〜Q21 の決定の反映 | 未起草 |

承認の範囲は第1.2節の表の**「段階3 で本改訂（v2.0）が決めること」の列**である。「段階2 で確定」の列は、Q18 の決定に伴う出力の保持の改訂（第11節の差異10）を除いて変えていない。「後続が決めること」の列は担当文書が決める。

検証戦略 B の紙上トレース（第9.4節の9経路）は**本改訂の承認後に起草する**。T01 への追記か新しい文書（T02）かは起草時に決める。T01 §14 は「v1.1 で検証戦略 B の時刻表を足す」としており、T01 への追記が既定の読み方である。

## 15. 段階3 の決定（Q10〜Q18。**決定済み**）と、残る要決定（Q19〜Q21。**未決**）

段階2 の Q1〜Q9 は 2026-09-21 に決定済みで（第13節）、段階3 の Q10〜Q18 は **2026-09-22 に決定済み**である。番号は再利用しない。

### 15.1 Q10〜Q18 の決定（2026-09-22。**決定済み**）

「選択肢 n」は起草時に並べた番号で、1 が起草時の推奨案である。**Q10〜Q17 は選択肢1（推奨案）、Q18 だけは選択肢2（推奨案ではない）**で決まった。

| # | 決めたこと | 決定 | 反映先 |
|---|---|---|---|
| Q10 | 市場状態の取引許可をどこで適用するか | **選択肢1（推奨）**: ランタイムが役割フィールド経由で読み、取引機会の生成時（P3）に適用する | §7.4 の手順2a・§7.6、§5.6 の検査 d、D04 §12（因果辺）、上位設計書 §4.5 |
| Q11 | 指数移動平均（EMA）の初期化と更新をどう決めるか | **選択肢1（推奨）**: 履歴窓から毎回計算する（状態を持たない） | §4.5 |
| Q12 | 段階3 で足す部品カタログの範囲 | **選択肢1（推奨）**: 検証戦略 B に必要な部品＋基本合成3件＋ATR の計11件。N 本継続と「A の後 N 本以内の B」は作らない | §4.5〜§4.10、§10.2、D04 §11.2 |
| Q13 | 出力の鮮度と観測区間をどう運ぶか | **選択肢1（推奨）**: `VALUE` の出力をランタイムが `Observation` で包む | §4.2・§6.7 |
| Q14 | 取引機会・建玉を対象とする評価を足の確定でどう起動するか | **選択肢1（推奨）**: ランタイムが対象1件につき1要求を作る | §6.11、D04 §4.3（`RuntimeTarget.OPPORTUNITY`） |
| Q15 | 入力を待つ設定（`WAIT_FOR_INPUT`）に置くフィールド | **選択肢1（推奨）**: 待機期限・期限切れの動作・追い越し時の動作の3つ。待機できる欠損理由はランタイムの固定規則 | §6.8、D04 §6.3 |
| Q16 | 過去値へ遡る設定（`USE_PREVIOUS`）に置くフィールド | **選択肢1（推奨）**: 遡り上限と、遡りを許す欠損理由の2つ | §6.9、D04 §6.3 |
| Q17 | 評価要求の追い越しの既定をどこに置くか | **選択肢1（推奨）**: 宣言を必須にし、ランタイムに既定を持たせない | §6.10 |
| Q18 | 上流の出力を履歴窓で読む仕組みをいつ作るか | **選択肢2（推奨案ではない）**: **段階3 で作る**。保持する本数と捨てる時点の規則を置く | §5.3・§5.6（検査 f）・§6.3・§6.5・**§6.12**・§10.2・第11節の差異10 |

各項目で採らなかった案は、本文の該当節に「不採用」として1行ずつ残してある。

**Q18 の決定が他に与えた影響**【提案】。決定が推奨と逆だったため、次の3点を本文で改めた。(a) 出力参照を履歴窓で読む接続の拒否を、**本数で数える窓に限って解除**した（§5.6・§6.3）。経過時間の窓は保持本数をコンパイル時に導けないため拒否を続ける。(b) 実行時の状態に出力の履歴を足し、保持本数の計画をコンパイル結果に載せた（§3 の型表・§5.3・§6.5）。(c) **Q12 の決定（N 本継続の合成部品を段階3 で作らない）は変えていない**。段階3 で作るのは仕組みだけである（§4.6・§10.2）。

### 15.2 残る要決定（Q19〜Q21。**未決**）

Q10〜Q18 の決定と、その反映の作業から新しく生じた3件である。各項目は「何を決めるか / 影響 / 選択肢（3つ以内、**先頭が推奨**）/ 推奨の理由」の形で書く。**選択肢のラベルに書いていない解釈を足さない**。

#### Q19 後続確認の期限を、どの系列の確定足で数えるか

**何を決めるか**: 「確認が成立しなければ 2 本で諦める」と書いたときの「2 本」を、取引機会を出した足の系列（検証戦略 B なら1時間足）で数えるか、確認に使う足の系列（同 15分足）で数えるか。

**影響**: 同じ `bars=2` という宣言が、確認を試みる回数として 2 回にも 8 回にもなる（1時間足と15分足なら4倍）。宣言を読んで確認の回数が分かるかどうかが変わる。承認済みの戦略宣言モデル（D04 §10.1）の説明文を書き換えるかどうかも、この決定で決まる。

| # | 選択肢 | 影響（1行） |
|---|---|---|
| 1（推奨） | 確認足の系列で数える（D04 §10.1 の説明を改める） | 宣言の数字がそのまま「あと何回確認できるか」になり、時間足の組合せを変えても意味が変わらない |
| 2 | Trigger の系列のまま数える（D04 §10.1 を変えない） | 承認済みの文書を変えずに済むが、同じ宣言が時間足の比で別の回数を意味する |
| 3 | 期限型に系列を書けるようにする | 宣言で明示できるが、確認の計画（`ConfirmationPlan.series`）と系列を二重に書くことになる |

**推奨の理由**: 期限は「あと何回の確認の機会があるか」を表すものであり、確認足の本数で数えるのが宣言の意味と一致する。この宣言は段階2 では能力検査で拒否していて一度も動いておらず、**動かす前なら書き換えの影響が実行結果に及ばない**。

#### Q20 保持した上流の出力を、何をもって「1 件」と数えるか

**何を決めるか**: 上流の出力を過去 N 本で読むとき（第6.12節）、N の 1 本を「評価が出力を1件出すたび」と数えるか、「観測した足1本につき1件」と数えるか。

**影響**: 同じ使用箇所が同じ足について2回出力しうる場面（待機から再開したとき、確認や建玉のように対象ごとに評価するとき）で、N 本の窓が指す過去の範囲が変わる。評価ごとに数えると、待機が1回起きただけで窓が1本ぶん手前まで届かなくなる。

| # | 選択肢 | 影響（1行） |
|---|---|---|
| 1（推奨） | 観測した足（`Observation.subject`）ごとに1件とし、同じ足の2件目は置き換える | 窓の N 本が「過去 N 本の足」を素直に指し、待機の有無で窓の範囲が変わらない |
| 2 | 出力記録1件につき1件とし、同じ足でも2件積む | 規則が単純だが、待機や再評価の回数だけ窓が手前へずれる |
| 3 | 観測した足ごとに1件とし、同じ足の2件目は捨てる（先に出たほうを残す） | 置き換えの判断が要らないが、待機から再開して読み直した新しい値が窓に入らない |

**推奨の理由**: 履歴窓は市場データ側でも「過去 N 本の足」を意味しており（D03 §6.2）、上流の出力でも同じ読み方にすると、同じ宣言の `BarsWindow(20)` が読む相手によって別の意味にならない。置き換えを選ぶのは、待機から再開して読み直した値のほうが、その足について最後に確定した答えだからである。

#### Q21 待機から再開したとき、上流の出力の履歴をどこを基準に読むか

**何を決めるか**: 待機していた評価が再開したとき（第6.8節 手順4）、上流の出力の履歴窓を「待機に入ったときに固定した対象足」まで遡って切るか、「再開した判断時点の最新」から切るか。

**影響**: 待機して再開した評価の答えが、待たなかった場合と同じになるかどうかが決まる。市場入力は固定した対象足で読み直す規則が既にあるので（第6.8節）、上流の出力だけ基準が違うと、同じ評価の中で2つの基準が混ざる。

| # | 選択肢 | 影響（1行） |
|---|---|---|
| 1（推奨） | 固定した対象足に対応する観測まで遡って窓を切る（市場入力と同じ基準にする） | 待機の有無で答えが変わらず、第6.8節の「固定するのは問いである」と揃う |
| 2 | 再開した判断時点の最新から窓を切る | 実装が単純だが、待機したぶんだけ新しい観測が窓に入り、待たなかった場合と答えが変わる |
| 3 | 待機に入った時点で読めた履歴を待機記録（`WaitingRequest`）に写し取って使う | 基準が1つに決まるが、待機記録が窓の本数ぶんの出力を抱え、待機の件数だけ記憶が増える |

**推奨の理由**: 待機記録が固定するのは「問い」であり、その問いには「どの足を対象に評価するか」が含まれる（上位設計書 §4.3.14）。市場入力を固定した足で読み直すのと同じ基準を上流の出力にも当てれば、遅延シナリオの4ケースを比べたときに「待ったから答えが変わった」経路が残らない（第9.3節）。
