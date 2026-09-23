# D06: バックテスト基盤の最小縦断設計（`odyssey_fx.backtest`: engine / domain.orders / admission / execution / portfolio / trace）

作成日: 2026-09-21
状態: **承認（2026-09-21、PR #16）**。v1.6（2026-09-23、PR #24）: 検証戦略 B の紙上トレース [T02](../traces/T02_paper_trace_strategy_b.md) が立てた要決定に人間の決定が出たので、段階3 の範囲で反映した（**段階2 の挙動は変えていない**）。あわせて独立レビューの指摘1件も反映した。(1) **足の確定で生まれた損切り水準の更新（トレーリング）を、受付（rank 10）の直前に建玉へ適用する**（Q25 決定、選択肢1。第4.2節の手順6・第8.3節）。従来の「適用フェーズは約定後の評価（`POST_FILL_EVALUATION`）」では、受付通知も約定通知も無い判断時点で第2回の `step` を呼ばないため、足の確定で出た更新を適用する場所が無かった。**新しいフェーズは足さず**、処理点のフェーズを受付（`ADMISSION`）にする。更新を受付より前に効かせるので、同じ判断時点の新しい注文のリスク審査が更新後の水準を見る。(2) **取引機会の有効性の再検査が失敗した（`MISSING_FAILED`）場合も、評価の失敗と同じ経路で run を止める**（独立レビューの指摘。第4.2節）。再検査は評価の外側で走るため評価記録が作られず、従来の停止条件（評価記録の `Failed`）では止まらないまま注文が受け付けられる経路が残っていた。(3) **確認試行（表18）を、戦略ランタイムの戻り値に足した列（`RuntimeStepResult.confirmation_attempts`）から主キー `(opportunity_id, bar_key)` で置き換えて書く**（Q26 決定、選択肢1。第4.2節の手順5・9・第9.2節）。従来は確認試行がランタイムの内部状態にしか無く、エンジンが表18 を書く経路が存在しなかった。v1.5（2026-09-22、PR #22）: 戦略ランタイム設計（D05 v2.0）の要決定 Q10〜Q18 に対する人間の決定を受け、**D05 §12.1 が挙げた本書への改訂依頼5件をすべて反映した**（段階3 の範囲。段階2 の確定は変えていない）。(1) 第4.1節の rank 4「取引機会のライフサイクル検査」の内容に、**待機中の評価要求の期限と追い越しの検査**を足した（新しいフェーズは足さない）。(2) 第4.4節の「戦略ランタイムが返した記録の処理点はエンジンの時計で番号を振り直す」の対象に、**待機の出来事**（`WaitEvent.at`）と**有効性の再検査**（`ValidityRecheck.at`）を足した。(3) 第8.3節に**損切り水準の更新（トレーリング、`UpdateStop`）の適用意味論**を足した。(4) 第9.2節の判断履歴の表に**段階3 の4表**（待機の出来事・遡った入力・確認試行・取引機会の有効性の再検査）を足した。上流の出力の履歴読み取りについては、D05 §6.12 の決定により**新しい表を足さない**。(5) 第10.1節の末尾処理に、**run 末尾に残った待機要求の終端**を足した。v1.4（2026-09-22、PR #19）: 独立レビュー（Codex）7巡目の保留1件を人間が決定した。(1) **週末持ち越し禁止の判定は週末だけに効かせる**。カレンダーに「休場を適用する前の週の開場区間を返す操作」を足し、受付側はそれで週末境界だけを判定する（第5.3節。同じ PR で D03 §3.4 を改訂）。祝日・短縮セッションをまたぐ候補は有効時間の問題（`NO_CANDIDATE`）として扱う。(2) 判断履歴の数値表記は末尾ゼロを落とす現在の規則のままとし、紙上トレース T01 の `149.500` は値としての等価を示す書き方であって保存文字列は `149.5` であることを第9.1節に明記した。v1.3（2026-09-22、PR #19）: 段階2 のバックテスト基盤の実装で残った11件の仮置きを人間がすべて決定し、本文へ反映した（**未決の項目は残っていない**）。主なものは、(1) 保護水準の更新を決済要求との衝突で捨てた理由に新しい理由コード `SUPERSEDED_BY_EXIT` を置く（第8.3節。同じ PR で上位設計書 §4.7.14 と D02 §8.1 を改訂）、(2) 判断履歴の数値列を人が読める固定小数表記にし、同じ値が常に同じ文字列になる4つの規則を定めた（第9.1節）、(3) 候補の始値を実在する足ではなく**足のスケジュール**から決めるため、執行系列のポートに4つ目の操作を足した（第5.3節。同じ PR で D03 §6.3 を改訂）、(4) 公開が1件も無い判断時点では戦略ランタイムを呼ばない（第4.2節）、(5) 予約と建玉の表は run の終わりに最終状態を1行ずつ書く（第9.2節）、(6) 「診断として残す」の残す先は run manifest の警告群とする（第9.3節）、(7) 層規則に合わせた型の置き場所5件を第3節に確定した。v1.2（2026-09-21、PR #17）: D07 §15 の改訂依頼1〜3 に対する人間の決定（Q12〜Q14、いずれも選択肢1）を反映した。(1) 約定1件ごとの費用を区分別の金額列として読めるようにした（第9.2節の表9、Q12）。(2) run 中の台帳 snapshot の含み損益を、直前に完了した執行足の終値で評価すると定めた（第8.1節、Q13。受付時の参照価格 Q10 と同じ出どころ）。(3) 平坦化の規則の3つの穴（複合表の接頭辞・入れ子レコード・区分タグ付き union）を埋めた（第9.1節、Q14）。あわせて `Reason` の列名の例を一般規則に合わせて `*_code` / `*_detail` に直した（D07 §4.2 が既に使っている列名と一致させるため）。v1.1（2026-09-21、PR #17）: D07 §15 の改訂依頼4 を受け、`BacktestResult` から資産推移の2項目（`balance_series` / `equity_series`）を落とした（第9.4節）。要素の型が定義されておらず、同じ推移が台帳 snapshot の表（表14）と結果 DTO の2経路で読める状態だったためである。**第9.4節が既に定めた「集計前のレコードは `trace_tables` 経由で渡し、`BacktestResult` の中で集計しない」の適用であり、設計の選択は伴わない**（D07 は表14 を直接読むため、段階2の指標・集計・trace はどれも変わらない）。v1.0（2026-09-21）: 第16節の要決定 Q1〜Q11 を人間がすべて決定し（Q7 のみ選択肢2、他の10件は提示時の推奨案である選択肢1）、本文へ反映した。**未決の項目は残っていない**。決定に伴い、**同じ PR で正本を改訂した**: 処理段階の名前に数字を許す規則（D02 §3.3、v1.5）、公開バッチに run 末尾の合図を足す項目（D05 §6.1、v1.1）、保護水準の置き方が不正なときの受付前拒否の理由コード `PROTECTION_INVALID`（上位設計書 §4.7.14 と D02 §8.1、v1.5）、発注の根拠になった出力の識別子を戦略ランタイムの戻り値へ足す改訂（D05 §3・§6.2、v1.2。第6.1節の阻害要因の解消）。あわせて紙上トレース [T01](../traces/T01_paper_trace.md) を書き（置き場所は D01 §7.1 に従い `docs/traces/`）、そこで洗い出した未記述を本書へ反映した（第17節に一覧）。段階2（最小縦断＝戦略定義→注文→約定→単一評価）と紙上トレース T01 に必要な範囲だけを扱う。検証戦略 A（1時間足の高値突破→後続確認なしの成行→初期損切り＋固定リスクリワード比の利確、上位設計書 §7.1）が動くことを必要十分条件とする。ADR-0016 条件2 のうち「D06 の最小縦断範囲」を本書で充足する。全体計画 §6 D-2 が「骨子だけで実装へ進めない」と列挙した5点（注文状態、フェーズ順、原子性、run_end、trace 型）は、最小縦断の範囲でもすべて本書で確定させる。第16節に決定の一覧を置く。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.3.12・§4.5・§4.7（全節）、[全体計画書](fx_research_platform_overall_plan.md) §5.4・§6 B/C/D/E・§7.4・§8.1・§8.2、[D01](D01_architecture_and_dependency_rules.md) §3・§4・§7.2、[D02](D02_common_kernel.md) §3.3・§4・§5.2・§7・§8・§9、[D03](D03_marketdata_and_time.md) §3・§6・§7、[D04](D04_strategy_declarations.md) §1.2・§5・§11、[D05](D05_strategy_runtime.md) §1.2・§6.1・§7・§8・§11・§12、ADR-0006（決定論的 ID）、ADR-0012（Decimal / float 境界）、ADR-0015（初版の縦断実行範囲）、ADR-0016（実装開始条件）、ADR-0027（成果物は Parquet 表＋JSON マニフェスト）、ADR-0029（swap 未計上）、ADR-0030（足内競合解決契約）、ADR-0031・ADR-0032・ADR-0033（取引機会の語彙）
対応段階: 段階2で実装。

## 0. 本書の位置付けと凡例

戦略ランタイムが返した注文意図・保護水準・管理要求を、**因果順序を守って注文・約定・建玉・口座・記録へ変換する**手順と型を決める。戦略の意味（部品の計算規則・取引機会の状態機械）と評価指標は決めない。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D05）で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択する事項。**Q1〜Q11 は 2026-09-21 にすべて決定済み**で、本文は決定後の内容になっている。**未決の項目は残っていない**。第16節に決定の一覧を置く |

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
| 理由コードの語彙 | 上位設計書 §4.7.14（実装側の列挙は D02 §8.1 がその写し） | 参照のみ。本書の Q6 の決定で加えた `PROTECTION_INVALID` は、同じ PR で §4.7.14 と D02 §8.1 の両方へ反映済み |
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

**本書のレビューの対象範囲は下表の第3列と、紙上トレース [T01](../traces/T01_paper_trace.md) である**（T01 は本書の型で経路を1件ずつ追う文書であり、本書と同じ範囲を扱う）。第4列に属する指摘は「対象外（担当へ）」として記録し、本書では直さない。ただし D04 §1.2・D05 §1.2 と同じ例外を置く。**本書の文が第4列の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。第4列の内容を本書に書き足すことはしない。

| # | 領域 | 本書 v0.1 が決めること（対象内） | 後続が決めること（対象外・担当） |
|---|---|---|---|
| 1 | フェーズと処理順 | フェーズ集合の列挙・順位・名前、1つの判断時点の処理順、**D05 が要求した約定後の評価起動点を挿す位置**、戦略ランタイムを呼ぶ回数、冪等性の確定単位（第4節） | 遅延シナリオ4ケース別の処理順の検証（**段階3・D08**）、高速化版と参照実装の同値性検証（**段階6**） |
| 2 | 注文 | 注文の状態機械（4状態・6遷移・発火条件・フェーズ・記録する理由）、発注試行と受付前拒否の記録、有効期限の計算と候補 open との競合（第5節） | 指値・逆指値・部分約定・増し玉・分割決済・週末持ち越し（**段階6・D10**）、再審査の型・配置・回数上限（**段階6・D10**） |
| 3 | 受付 | 要求組立、全順序化の鍵と優先度の向きと ID 比較規則、リスク審査と数量決定の順、**審査・予約・受付の原子性**、受付結果を戦略へ返す通知（第6節） | 戦略別リスク配分と `strategy_priority` の設定値（**D10**）、口座強制縮小の対象と優先順位（**D10**） |
| 4 | 執行 | 候補 open の選び方、約定価格、保護水準の到達判定、**足内競合解決契約（ADR-0030）の宣言形・検査・手順・記録**、gap・約定ずれ超過・緊急決済、費用モデル（第7節） | 期間 Exit（**段階4 以降・D05 §10.2**）、約定直後以外の緊急決済の実行時点（**D10**）。トレーリング（`UPDATE_STOP`）は、評価をどの足で起動するかが D05 §6.11（v2.0）で、適用の意味論が**本書 §8.3（v1.5）**で決まった |
| 5 | 口座・建玉 | 台帳、balance と equity の区別、建玉と保護水準の管理権、`position_context@v1` / `account_context@v1` の項目、通貨換算の適用時点（第8節） | 複数建玉・複数銘柄の台帳と配分（**D10**）、equity 基準の予算へ切り替える拡張（**D10**） |
| 6 | 記録 | trace の行の種類（段階2 の15件と、**v1.5 で足した段階3 の3件**）とフィールド、run manifest の項目、`BacktestResult` の項目、保存形式（第9節） | 指標の算出、終端理由別・診断理由別の集計、実験 manifest への固定（**D07**）、swap 未計上をユーザーへ伝える出力の形（**D07**、ADR-0029） |
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
| `BACKTEST_PHASES` | `engine.phases` | 定数（`PhaseSet`） | 第4.1節の15フェーズ（rank 0〜14） | §4.1 |
| `RunConfig` | `domain.policies` | レコード | `run_interval: Interval` / `snapshot_ref: SnapshotRef` / `compiled_ref: CompiledStrategyRef` / `account: AccountSpec` / `risk_policy_ref: PolicyRef` / `execution_policy_ref: PolicyRef` / `cost_model_ref: PolicyRef` / `conversion_policy_ref: PolicyRef` / `delay_scenario_ref: PolicyRef` / `execution_series: SeriesId` / `seed: int` | §4.2・§8.5・§9.3 |
| `AccountSpec` | `domain.account` | レコード | `account_id: AccountId` / `currency: CurrencyCode` / `initial_balance: Money` | §8.1 |
| `RiskPolicy` | `domain.policies` | レコード | `trial_risk_rate: Decimal` / `account_risk_cap: Decimal`（費用予算 `C(Q)` は数量に比例するため `CostModel` から計算する。§7.6） | §6.4 |
| `ExecutionPolicy` | `domain.policies` | レコード | `entry_delay_bars: int` / `adverse_fill_limits: Mapping[Symbol, PriceOffset]` / `entry_valid_for: timedelta` / `close_valid_for: timedelta` / `resolution_hierarchy: ResolutionHierarchy` / `reference_quote_source: ReferenceQuoteSource` | §7.1・§7.1.1・§7.4・§6.4 |
| `ReferenceQuoteSource` | `domain.policies` | enum | `EXECUTION_SERIES_LAST_CLOSE`（段階2の唯一の値。Q10 決定、選択肢1） | §6.4 |
| `ConversionPolicy` | `domain.policies` | レコード | `pivot_currency: CurrencyCode` / `max_observation_skew: timedelta` | §8.5・Q7・Q9 |
| `ConversionPath` | `domain.policies` | レコード | `legs: tuple[ConversionLeg, ...]`（**0本＝恒等換算 / 1本＝直接 / 2本＝基軸通貨経由**）/ `rate: Decimal`（各 leg の積。0本なら1）/ `observed_at: UtcTime`（各 leg の `observed_at` の**最も古いもの**。0本なら判断時刻）/ `skew: timedelta`（0本・1本なら0） | §8.5 |
| `ConversionLeg` | `domain.policies` | レコード | `series: SeriesId` / `bar_key: BarKey` / `rate: Decimal` / `observed_at: UtcTime` / `inverted: bool` | §8.5 |
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
| `AttemptDecision` | `admission` | union | `AttemptAccepted(attempt_id, order_id, assessment_ref: RiskAssessmentRef \| None)` / `AttemptRejected(attempt_id, reason: Reason, assessment_ref: RiskAssessmentRef \| None)`。発端の識別子は同じ `attempt_id` を持つ `OrderRequest`（表4）から辿る | §6.2 |
| `AdmissionBudget` | `admission` | レコード | `balance: Money` / `trial_budget: Money` / `account_remaining: Money` / `admission_budget: Money` / `consumed: Money` | §6.4 |
| `RiskAssessment` | `admission` | レコード | `assessment_id: EvidenceId` / `attempt_id: AttemptId` / `policy_ref` / `reached_step: int`（3〜8）/ `budget: AdmissionBudget` / `reference_quote: ReferenceQuote` / `stop_before_rounding: Price` / `stop_after_rounding: Price \| None` / `adverse_fill_limit: Price \| None` / `quantity_step: Decimal \| None` / `quantity: Quantity \| None` / `conversion: ConversionRate \| None` / `cost_budget: Money \| None` / `reservation_amount: Money \| None` / `checks: tuple[RiskCheckResult, ...]` | §6.4 |
| `RiskCheckResult` | `admission` | レコード | `check: str` / `passed: bool` / `limit: Money \| Decimal` / `observed: Money \| Decimal` | §6.4 |
| `ReservationStatus` | `domain.reservations` | enum | `HELD` / `TRANSFERRED` / `RELEASED` | §6.5 |
| `ReservationState` | `domain.reservations` | レコード | `reservation_id: ReservationId` / `status: ReservationStatus` / `last_event_id: EventId` / `last_processed_at: ProcessingPoint` | §6.5 |
| `ExecutionTime` | `domain.fills` | union | `ExactExecutionTime(time: UtcTime)` / `BarExecutionInterval(bar_key: BarKey, interval: Interval)` | §7.3 |
| `CostEntry` | `domain.fills` | レコード | `kind: CostKind` / `native: Money` / `account: Money` / `conversion: ConversionRate` | §7.6 |
| `CostKind` | `domain.fills` | enum | `COMMISSION` / `SLIPPAGE_IN_PRICE` / `SPREAD_IN_PRICE`（後2者は価格反映済みの記録用。金額は控除しない） | §7.6 |
| `ResolutionMethod` | `execution` | enum | `SINGLE_HIT` / `RESOLVED_BY_CHILD` / `UNRESOLVED_SL_PRIORITY` | §7.4 |
| `EvidenceKind` | `trace.recorder` | enum | `ORDER_REQUEST` / `ADMISSION` / `FILL` / `PROTECTION_UPDATE` | §9.2 |
| `MarketObservationRef` | `trace.recorder` | レコード | `snapshot_ref: SnapshotRef` / `series: SeriesId` / `interval: Interval` / `field: MarketDataField`（D04 §5 の市場データ項目） | §9.2 |
| `EvidenceRecord` | `trace.recorder` | レコード | `evidence_id: EvidenceId` / `at: ProcessingPoint` / `kind: EvidenceKind` / `output_ids: tuple[OutputId, ...]` / `evaluation_ids: tuple[EvaluationId, ...]` / `attempt_id: AttemptId \| None` / `position_id: PositionId \| None` / `market_refs: tuple[MarketObservationRef, ...]` / `conversion_paths: tuple[ConversionPath, ...]` / `ledger_snapshot_at: ProcessingPoint \| None` / `policy_refs: tuple[PolicyRef, ...]` | §9.2・§8.5.1 |
| `IntrabarResolution` | `execution` | レコード | `position_id: PositionId` / `parent_bar_key: BarKey` / `method: ResolutionMethod` / `series_used: tuple[SeriesId, ...]` / `resolved_child_bar_key: BarKey \| None` / `verdict: CloseCause` / `fill_id: FillId` | §7.4 |
| `ProtectionState` | `domain.positions` | レコード | `version: int` / `stop_loss: Price` / `take_profit: Price \| None` / `effective_from: BarKey` / `owner_instance_id: str \| None` | §8.3 |
| `PositionStatus` | `domain.positions` | enum | `OPEN` / `CLOSED` | §8.2 |
| `Position` | `domain.positions` | レコード | `position_id` / `account_id` / `strategy_id` / `symbol` / `side: OrderSide` / `quantity: Quantity` / `entry_fill_id: FillId` / `entry_price: Price` / `opened_at: ProcessingPoint` / `protection: ProtectionState` / `status: PositionStatus` / `close_fill_id: FillId \| None` / `realized: Money \| None` | §8.2 |
| `PositionRiskAllocation` | `domain.positions` | レコード | `allocation_id: AllocationId` / `position_id` / `source_reservation_id: ReservationId` / `amount: Money` / `created_event_id: EventId` / `released_event_id: EventId \| None` | §8.1 |
| `RiskMeasurement` | `domain.positions` | レコード | `position_id` / `at: ProcessingPoint` / `measured: Money` / `allocated: Money` / `basis: str` | §8.1 |
| `PositionContext` | **`strategy.records.payloads`** | レコード | `position_id` / `symbol` / `direction: TradeDirection` / `quantity: Quantity` / `entry_price: Price` / `effective_stop_loss: Price` / `effective_take_profit: Price \| None` / `opened_at: ProcessingPoint` | §8.4 |
| `AccountContext` | **`strategy.records.payloads`** | レコード | `account_id: AccountId` / `currency: CurrencyCode` / `balance: Money` / `equity: Money` / `consumed_risk: Money` | §8.4 |
| `AccountLedger` | `domain.account` | レコード | `account_id` / `balance: Money` / `orders: Mapping[OrderId, AcceptedOrder]` / `positions: Mapping[PositionId, Position]` / `allocations: Mapping[AllocationId, PositionRiskAllocation]` / `reservations: Mapping[ReservationId, RiskReservation]` / `reservation_states: Mapping[ReservationId, ReservationState]` / `order_states: Mapping[OrderId, OrderState]` / `processed_event_ids: frozenset[EventId]` | §4.4・§8.1 |
| `LedgerSnapshot` | `portfolio.ledger` | レコード | `at: ProcessingPoint` / `balance: Money` / `equity: Money` / `consumed: Money` / `open_position_ids: tuple[PositionId, ...]` | §8.1・§9.2 |
| `FinalSummaries` | `trace.result` | レコード | `realized: Money` / `equity_with_mtm: Money` / `hypothetical_closed: Money` / `cost_breakdown: Mapping[CostKind, Money]` | §10.3 |
| `RunStatus` | `trace.result` | enum | `COMPLETED` / `FAILED_DATA_ERROR` / `FAILED_CAPABILITY` | §9.4・§10.4 |
| `HierarchyCheckResult` | `domain.policies` | レコード | `check: str` / `passed: bool` / `parent_series: SeriesId` / `child_series: SeriesId \| None` / `parent_bar: BarKey \| None` / `child_bar: BarKey \| None` / `expected_interval: Interval \| None` / `child_intervals: tuple[Interval, ...]` / `coverage_gaps: tuple[Interval, ...]` / `coverage_overlaps: tuple[Interval, ...]` / `expected_boundary: UtcTime \| None` / `observed_boundary: UtcTime \| None` / `expected_basis: PriceBasis \| None` / `observed_basis: PriceBasis \| None` / `expected_available_at: UtcTime \| None` / `observed_available_at: UtcTime \| None` | §7.4 |
| `DataCapabilityReport` | `trace.manifest` | レコード | `compiled_match: bool` / `integrity: IntegrityReport` / `hierarchy_checks: tuple[HierarchyCheckResult, ...]` / `runnable: bool` / `reason: Reason \| None` | §7.5・§10.5 |
| `RunManifest` | `trace.manifest` | レコード | 第9.3節の項目 | §9.3 |
| `BacktestResult` | `trace.result` | レコード | 第9.4節の項目 | §9.4 |
| `TraceSink` | `application.ports` | Protocol | `write(table: TraceTable, rows: tuple[object, ...]) -> None` | §9.1 |
| `TraceTable` | `trace.recorder` | enum | 第9.2節の表（段階2 の15件と、**段階3 の4件**＝`WAIT_EVENTS` / `INPUT_SUBSTITUTIONS` / `CONFIRMATION_ATTEMPTS` / `VALIDITY_RECHECKS`。合計19件。v1.5） | §9.2 |
| `CompositeRow` | `trace.recorder` | レコード | `primary: object` / `parts: tuple[tuple[str, type, object], ...]`（接頭辞・従の型・値）。複合表と区分タグ付き union の行を、記録層が受付層・執行層・台帳層を import せずに運ぶための入れ物 | §9.1・§9.2 |
| `ManagementApplication` | `trace.recorder` | レコード | `at: ProcessingPoint` / `applied: bool` / `reason: Reason \| None` / `protection_version: int \| None` / `rounded_take_profit: Price \| None` / `realized_reward_risk: Decimal \| None` | §8.3・§9.2 |
| `PublicationFeed` | `engine.loop` で構造を定義し `application.ports` が再公開 | Protocol | **反復できること**だけを要求する（`__iter__`）。要素は D03 §7.1 の4イベント。run 区間で絞るのはエンジン側で行う | §4.3 |
| `ExecutionSeries` | `engine.loop` で構造を定義し `application.ports` が再公開 | Protocol | D03 §6.3 の4操作（`open_of` / `bar` / `next_bar_key_after` / `next_scheduled_open_after`） | §5.3・§7.1 |
| `IntrabarSeries` | `engine.loop` で構造を定義し `application.ports` が再公開 | Protocol | `bars_in(series, interval)`。足内競合を下位足で解く段でだけ使う（階層が2段以上のとき） | §7.4 |
| `Calendar` | `engine.loop` で構造を定義し `application.ports` が再公開 | Protocol | `marketdata.domain` の `TradingCalendar` を受ける（D01 §4）。期限・候補 open・休場の判定に使う。使う操作は `sessions`（休場を取り除いた取引セッション）と `weekly_session_at`（**休場を適用する前の**週の開場区間。週末持ち越し禁止の判定だけに使う。D03 §3.4） | §5.3 |
| `ResultWriter` | `application.ports` | Protocol | `write(result: BacktestResult, manifest: RunManifest) -> None` | §9.1 |
| `RiskAssessmentRef` | `admission` | レコード | `assessment_id: EvidenceId`（`RiskAssessment.assessment_id` と同じ値。`RISK_ASSESSMENTS` の行を指す） | §6.4 |
| `RunBacktest` | `application.run_backtest` | Protocol | `run(config: RunConfig, compiled: CompiledStrategy) -> BacktestResult`。構築時に**実行の出どころ**（`CodeDigest` / `LockDigest` / `EnvDigest` / git の状態）も受け取る（第9.3節の識別の群。実際に算出して渡すのは `app`） | §4.2・§9.3 |

**中間の4層（受付・執行・台帳・記録）は相互に import できないため、2つ以上の層から参照される型は置き場所を下げる**【確定】（2026-09-22 の人間の決定。PR #19。D01 §3.3 の契約 L2c）。解像度階層の適合検査の結果（`HierarchyCheckResult`）と換算経路（`ConversionPath` / `ConversionLeg`）は、執行層・台帳層だけでなく run manifest と根拠記録にも入るため `domain.policies` に置く（経路を決める規則そのものは `portfolio.conversion` に残す）。データ能力検査の報告（`DataCapabilityReport`）は run manifest と結果 DTO の項目であり、記録層はエンジンを import できないため `trace.manifest` に置く。公開フィード・執行系列・下位足・カレンダーの受け口は、使うのが `engine` でありエンジンは1つ上の `application` を import できないため、**`engine.loop` で構造を定義し `application.ports` が同じ名前で再公開する**（D01 §4 の「ポートは利用側の application が定義する」の、層順序に合わせた実現である）。予約（`RiskReservation`）が持つ審査記録への参照は、参照型（受付層の `RiskAssessmentRef`）ではなく**同じ値の識別子**（`assessment_id: EvidenceId`）とする（`domain` から受付層を参照できないため）。

`PositionContext` と `AccountContext` の2件だけが `strategy` 側に置かれる（理由は第8.4節）。`Symbol` / `Price` / `PriceOffset` / `Quantity` / `Money` / `ConversionRate` / `UtcTime` / `Interval` / `Reason` / `PhaseRank` / `PhaseSet` / `ProcessingPoint` と各 ID 型は D02、`SeriesId` / `BarKey` / `PriceBasis` / `IntegrityReport` は D03、`CompiledStrategy` / `EntryProposal` / `ManagementRequest` / `PublicationBatch` / `AdmissionNotice` / `RuntimeEventNotice` は D04・D05 が正本である。

## 4. エンジン（`engine`）

### 4.1 フェーズ集合【提案】＋【合意済み】（Q1 決定、選択肢1）

D02 §3.3 は「フェーズの具体的な一覧は `backtest.engine` が定義する」とし、`PhaseSet` の構築時に `rank` と `name` の一意性を検査する。D05 §6.1 は、取引機会の記録に押す `ProcessingPoint` のフェーズ順位を `PublicationBatch.phases.by_name(...)` で引く。**したがって本書がフェーズ名を確定させないと、D05 のランタイムは記録を組み立てられない。**

1つの判断時刻 T に属するフェーズを、因果順に次の15件（rank 0〜14）とする。`RUN_END` も `BACKTEST_PHASES` に含め、run 末尾の判断時点でだけ使う。フェーズ集合は run 全体で1つであり、判断時点ごとに変えない（D02 §3.3 の `PhaseSet` は run 内で固定される）。

| rank | 名前 | 内容 | 戦略ランタイムの関与 |
|---|---|---|---|
| 0 | `EXECUTION_BAR_COMPLETE` | 直前の執行足が終了し、その足の開始前に有効だった保護水準による内部約定を解決（第7.3節） | なし |
| 1 | `LEDGER_UPDATE` | rank 0 で確定した台帳に対する MTM（equity）の再評価と、台帳 snapshot の記録（第8.1節） | なし |
| 2 | `ORDER_EXPIRY` | `expires_at <= T` の PENDING 注文を EXPIRED にし予約を解放（第5.3節） | なし |
| 3 | `PUBLICATION` | 同じ `available_at=T` の確定足をまとめて戦略へ公開（上位 §4.3.12 の P0） | `PublicationBatch` の組み立て |
| 4 | `OPPORTUNITY_LIFECYCLE` | 取引機会の期限・失効のライフサイクル検査（上位 §4.3.14。D05 §7.2 の遷移8・11 のうち検査由来のもの）と、**待機中の評価要求の期限・追い越し・再開の検査**（v1.5。D05 §6.8・§6.10） | 第1回 `step` の内部 |
| 5 | `P1_FEATURE` | Feature の評価（上位 §4.3.12 の P1） | 第1回 `step` の内部 |
| 6 | `P2_MARKET_STATE` | MarketState の評価（P2） | 同上 |
| 7 | `P3_TRIGGER` | Trigger の評価と取引機会の生成（P3） | 同上 |
| 8 | `P4_CONFIRMATION` | 後続確認の評価（P4。段階2では起動しない） | 同上 |
| 9 | `P5_ORDER_INTENT` | 注文意図と保護水準の生成（P5） | 同上 |
| 10 | `ADMISSION` | 要求組立・全順序化・リスク審査・予約・受付（第6節） | なし |
| 11 | `EXECUTION_OPEN` | 次の執行足の始値処理: gap 保護決済 → 適格な成行注文の約定 → 新規建玉初期化 → 約定直後の緊急決済（第7.1節・第7.5節） | なし |
| 12 | `POST_FILL_EVALUATION` | 約定後の評価起動点（D05 §8）と受付結果の通知（第6.6節） | 第2回 `step` |
| 13 | `POST_FILL_ADMISSION` | rank 12 で生まれた管理要求のうち、注文になるもの（全数量決済）の要求組立・審査・予約・受付（第6.2節） | なし |
| 14 | `RUN_END` | 末尾処理（run_end の判断時点だけ。第10節） | 第3回 `step`（`is_run_end=True` の公開バッチ。Q2 決定） |

- rank 0〜2 が rank 3 より前にあるのは「終値評価より先に足内約定を反映する」【合意済み】上位 §4.7.12。
- **rank 1 は台帳を書き換えるフェーズではない**【提案】。保護水準の到達による決済は、受付と約定と損益反映を rank 0 の1つの確定単位でまとめて確定する（第4.4節の「エンジン生成の即時決済」）。したがって rank 1 に残るのは、確定後の台帳に対する含み損益の再評価（equity）と `LedgerSnapshot` の記録だけである。rank 1 でもう一度「約定を台帳へ適用する」と書くと、同じ約定を2つのフェーズで反映する読み方ができてしまう。フェーズ名 `LEDGER_UPDATE` は上位 §4.7.12 の「建玉・損益・balance・消費済み枠を更新」に対応する位置として残す。
- rank 10 が rank 9 の直後にあるのは「同時刻 close → 判断/受付 → open」【合意済み】上位 §4.7.11。
- rank 12 が rank 11 の後にあるのは D05 §8 の決定（約定処理より前に置く構成は排除済み）。D05 が本書へ委ねたのは**順位と挿す位置**だけであり、本書はそれを rank 12 として確定する。
- rank 2 が rank 11 より前にあるのは「期限処理フェーズを open 約定フェーズより前に置き、同時刻なら EXPIRED を先に確定する」【合意済み】上位 §4.7.13 B。
- rank 13 を置くのは、rank 12 の評価が返した管理要求のうち**全数量決済**が注文になるためである【提案】。受付フェーズ（rank 10）は既に過ぎており、受け口が無いとこの要求が注文にならないまま消える。rank 13 で受け付けた注文の最初の適格な始値は、rank 11 を過ぎているため**次の執行足の始値**になり、「因果順序上まだ到来していない最初の執行足の始値で約定する」【合意済み】上位 §4.7.11 を満たす。**不採用**: 次の判断時点の受付フェーズまで要求を保持する案（保持する仕組みと有効期限の起算点をもう1つ決めることになり、約定できる最初の始値は同じである）、rank 12 の中で受け付ける案（受付の処理点（`ProcessingPoint`）が評価と同じフェーズになり、判断履歴で評価と受付を区別できなくなる）。

**フェーズ名に数字を使う**【合意済み】（Q1 決定、選択肢1）。D02 §3.3 は `PhaseRank.name` を `^[A-Z_]+$` と定めており数字を許さなかったが、D05 §6.1 は `P1_FEATURE`〜`P5_ORDER_INTENT` という名前で順位を引くと書いていた。2026-09-21 の決定により**共通カーネルの名前規則を `^[A-Z][A-Z0-9_]*$` へ緩め**、上表の名前をそのまま使う。D02 §3.3 は同じ PR で改訂済み（v1.5）。上位設計書 §4.3.12 が確定した P0〜P5 という呼び方が全文書で使われており、名前に数字を残すと判断履歴（trace）のフェーズ列から因果順が名前だけで読める。**不採用**: フェーズ名から数字を除く案（D05 §6.1 の名前の列挙を改訂することになり、trace のフェーズ名から因果順が読めなくなる）、数字を英単語に置き換える案（`PHASE_ONE_FEATURE`。どの文書も改訂せずに済むが、名前が長く上位設計書の呼び方とも一致しない）。

`BACKTEST_PHASES` は `engine.phases` の定数 `PhaseSet` とし、run manifest に記録する【合意済み】D02 §3.3。

### 4.2 1つの判断時点で行うこと【提案】

`RunBacktest.run` は、公開フィードが生成するイベント列（D03 §7.1）を `available_at` 順に読み、**同じ時刻のイベントを1つの判断時点にまとめて**次を行う。

1. rank 0: 執行足の終了通知（`ExecutionBarComplete`）があれば、その足について保護水準の到達判定を行う（第7.3節）。
2. rank 1: 1 で確定した台帳について含み損益（equity）を再評価し、`LedgerSnapshot` を1件残す（第8.1節）。約定そのものの台帳への反映は 1 の確定単位で済んでいる。
3. rank 2: `expires_at <= T` の PENDING 注文を EXPIRED にする。
4. rank 3: `available_at = T` の `Publication` と、`bar_end = T` の `ScheduledBoundary` から `PublicationBatch` を組み立てる（第4.3節）。
5. rank 4〜9: **第1回の `step(batch)`** を呼ぶ。`evaluations` と `transitions` を trace へ渡し、**`wait_events` / `validity_rechecks` / `confirmation_attempts` も同じように trace へ渡す**（段階3。第9.2節の表16・18・19）。**下記の「停止判定」に当たれば、ここで run を止める**（条件の正本は下段落。ここには写さない）。当たらなければ `proposals` と `management_requests` を 6 へ渡す。
6. **rank 10 の直前**: **5（第1回の `step`）が返した** `management_requests` のうち**保護水準の更新（`UpdateStop`）を建玉へ適用する**（第8.3節）【確定】（Q25 決定、2026-09-23、選択肢1。紙上トレース T02 §14 #5）。**第2回の `step` が返した更新はここでは扱わない**（9 で `POST_FILL_EVALUATION` に適用する）。適用の内容と `effective_from` は第8.3節のままで、**新しいフェーズは足さない**。処理点は `ADMISSION`（rank 10）であり、表12（`MANAGEMENT_APPLICATIONS`）の主キー `(position_id, at)` はそのまま使える。**同じ判断時点の決済要求との競合は、この適用より前に解決する**（決済が勝ち、更新は `SUPERSEDED_BY_EXIT` で適用しない。第8.3節）。全数量決済は従来どおり 9（rank 13）で受け付ける。
7. rank 10: 要求組立から受付までを行い、`AttemptDecision` と `AdmissionNotice` を作る（第6節）。リスク審査（第6.4節）は、6 で適用し終えた保護水準を読む。
8. rank 11: 執行系列の始値処理を行う（第7.1節）。
9. rank 12: 7 の `AdmissionNotice` と 8 で生まれた `POSITION_OPENED` の通知があれば、**第2回の `step`** を呼ぶ。**第1回と同じく `evaluations` と `transitions`、および段階3 の `wait_events` / `validity_rechecks` / `confirmation_attempts` を trace へ渡す**（第2回で出る固定リスクリワード比の評価記録と、受付通知による取引機会の終端の遷移は、ここで保存しないと表2・表3から落ちる）。**停止判定は第1回とまったく同じ**（条件の正本は下段落）で、当たれば下記の第2回の停止範囲に従う。当たらなければ、`management_requests` のうち保護水準の更新は建玉へ適用し（第8.3節）、全数量決済は rank 13 へ渡す。通知が1件もなければ呼ばない。
10. rank 13: 9 の `management_requests` のうち全数量決済を要求へ組み立て、第6節と同じ手順で受け付ける。要求が1件も無ければ何もしない。
11. run_end の判断時点だけ rank 14 を行う（第10節）。**第3回の `step`** を `is_run_end=True` の公開バッチで呼び、残存する取引機会の終端（`RUN_END`）を受け取って trace へ渡す（Q2 決定、D05 §6.1 v1.1）。

**停止判定（失敗は受付より前で run を止める）**【提案】。**本段落が停止判定の正本である**（v1.6 で1か所に集約した。上の手順5・手順9 はここを参照するだけで、条件を写さない）。条件を手順の側に写すと、条件を1つ足したときに片方だけ直る。

> **停止判定**: その `step` の `RuntimeStepResult` について、次のどちらかが成り立つこと。
> **(a) `evaluations` に `Failed` が1件以上ある**、**(b) `validity_rechecks` に `MISSING_FAILED` が1件以上ある**。

(a) の根拠: D05 §6.2 は、部品の失敗・`on_missing=Error` の欠損・戻り値の検査違反を `Failed(Reason(DATA_ERROR, ...))` として評価記録に残し、**以降の評価を行わずに結果を返す**と定め、「run を終了させるのはエンジンの責務」と本書へ委ねている【合意済み】。

(b) の根拠【提案】（v1.6。独立レビューの指摘。D05 §7.3 の `MISSING_FAILED` は「run を失敗させる」と定めている）: 取引機会の有効性の再検査は**評価の外側**（確認評価の直前と、注文意図を作る直前）で走るため、失敗しても評価記録（`EvaluationRecord`）は作られず、`validity_rechecks` に `ValidityRecheck(outcome=MISSING_FAILED)` が1件残るだけである。(a) だけを見ると、**発注要求まで有効であり続けることを求める束縛に `Error` を書いた宣言**（D04 §6.3 が段階2 で拒否し、D05 §7.3 の記録ができたことで段階3 で解除するもの）で、run が止まらないまま `proposals` が受付へ渡り、**失敗した判断で注文が受け付けられる**。この経路は段階3 で初めて宣言でき、テスト戦略（D08 §13.2 の未整備6）が意味論テストで通す対象でもある。`MISSING_FAILED` の `reason` は `Reason(DATA_ERROR, ...)` なので、第10.4節の実行失敗の扱いも (a) と同じである。**段階2 の挙動は変わらない**（段階2 の `validity_rechecks` は常に空である）。

停止判定に当たったとき、どちらの `step` でも共通に行うことが2つある。

1. その `RuntimeStepResult` の `evaluations` / `transitions` と、**段階3 の3つの列**（`wait_events` / `validity_rechecks` / `confirmation_attempts`）を trace へ保存する（失敗の診断を判断履歴から消さない。v1.6 で3列を明示した）。**`outputs` は保存しない**。失敗より前に出た出力は、ランタイムが `OutputSink.emit` を呼んだ時点で既に表1 へ入っている（次段落）ので、戻り値からもう一度書くと同じ `output_id` の行が二重に入り、主キーが壊れる。成功した判断時点と同じ扱いであり、失敗時に例外を置かない。
2. その `step` が返した `proposals` と `management_requests` は**一切使わない**。同じ `step` の中で失敗より前に生まれたものも使わない（どこまでが有効な判断だったかが宣言から読めないため）。**第1回で失敗したときは、手順6 の保護水準の更新の適用も行わない**（管理要求を一切使わないので、適用する対象が無い）。

その先は、**どちらの呼び出しで失敗したかで分ける**。すでに確定した処理は巻き戻さない（第4.4節の確定単位は差し替え済みであり、シミュレーションであっても確定した約定を取り消さない【合意済み】上位 §4.7.13 A）。

| 失敗した呼び出し | 確定済みの処理 | 停止する範囲 |
|---|---|---|
| 第1回（rank 4〜9） | rank 0〜2 の内部約定・台帳更新・期限切れ | **手順6（保護水準の更新の適用）と rank 10 以降をすべて実行しない**（更新の適用・受付・始値処理・第2回の `step`・rank 13） |
| 第2回（rank 12） | rank 0〜2 に加え、rank 10 の受付と rank 11 の約定 | rank 13 を実行しない。保護水準の更新も適用しない |

どちらの場合も、その判断時点で止めて第10.4節の実行失敗として扱う（未約定注文を `CANCELED` / `DATA_ERROR`、`status = FAILED_DATA_ERROR`）。第2回で失敗した建玉は**初期の利確を持たないまま**残るが、その状態のまま最終 snapshot に保存し、架空の利確水準を埋めない。

**不採用**: 失敗した使用箇所だけを飛ばして続ける案（D05 が「以降の評価を行わない」と決めた範囲をエンジンが広げ直すことになる）、判断時点の末尾まで進めてから止める案（失敗後に受け付けた注文が約定し、失敗した run の成果物に取引が含まれる）。

**出力記録（`OutputRecord`）は `OutputSink` から受け取った分だけを表1へ書き、`RuntimeStepResult.outputs` を再送しない**【提案】。D05 §6.2 の手順7 はランタイム自身が `OutputSink.emit` を呼んでから結果を返すと定めており【合意済み】、戻り値をもう一度書き出すと同じ `output_id` の行が二重に入り、主キーが重複して出力件数が水増しになる。エンジンは `OutputSink` の実装（D01 §4）として受け取った時点で表1へ書き、戻り値の `outputs` は後続の処理と件数の検算にだけ使う。

**公開も足の終了も無い判断時点では `step` を呼ばない**【確定】（2026-09-22 の人間の決定。PR #19）。執行足の始値だけが来る判断時点（rank 11 しか仕事が無い時点）では、`PublicationBatch` に入れるものが `available_bars` も `scheduled_closes` も無く、部品が読むものが1つも無い。それでも呼ぶと、入力の無い評価記録と空の `step` の呼び出しが判断時点の数だけ判断履歴に並び、「評価を見送った」（`Skipped`）と「そもそも渡すものが無かった」を読む側が区別できなくなる。**不採用**: 空のバッチで毎回呼ぶ案（上記のとおり記録が読めなくなる。D05 §6.1 は空のバッチを拒まないので実行はできるが、意味が無い）。

**戦略ランタイムを1つの判断時点で最大2回（run_end では最大3回）呼ぶ**【提案】。D05 §6.1 は同じ `batch_id` での再呼び出しを `KernelValueError` で拒むため、2回目・3回目は**新しい `EventId` を持つ別の `PublicationBatch`** として渡す。3回とも `decision_time` は同じ T、`phases` は `BACKTEST_PHASES` である。

| 呼び出し | フェーズ | `available_bars` / `scheduled_closes` | `runtime_events` / `admissions` | `is_run_end` |
|---|---|---|---|---|
| 第1回 | rank 4〜9 | その判断時点の公開と足の終了（第4.3節） | 空 | `False` |
| 第2回 | rank 12 | 空 | 受付結果の通知と `POSITION_OPENED` の通知 | `False` |
| 第3回（run_end のみ） | rank 14 | 空 | 空 | `True` |

`RuntimeEventNotice(POSITION_OPENED, position_id, opportunity_id)` の `opportunity_id` は、**約定した注文の `EntryRequest.opportunity_id`**（`AcceptedOrder.attempt_id` から表4 の要求を辿って得る）とする【提案】。同じ判断時点の rank 10 で受け付けた通知によって、その取引機会は既に `FULFILLED_BY_ORDER_ACCEPTANCE` で終端しているが（D05 §7.2 の遷移5）、通知はどの機会から生まれた建玉かを判断履歴に残すためのものであり、機会の状態を再び変えない。終端済みの機会の識別子を入れることが、D05 §6.2 の「終端した取引機会のイベントは下流へ配送しない」に反しないのは、そこで配送を禁じているのが**取引機会の `EVENT` 出力**であり、実行時イベントの通知ではないためである。エンジン生成の決済による建玉終了には対応する機会が無いが、`POSITION_OPENED` 以外の実行時イベントは段階2では配送しない（第12節）。**不採用**: 受付結果を次の判断時点まで持ち越す案（取引機会が `ORDER_PENDING` のまま次の足へ渡り、同時保持上限の数え方が判断時点をまたいで変わる）、受付結果用に別のポート操作を足す案（D05 §6.1 の `StrategyRuntime` は `step` 1操作であり、入口が2つになると呼び出し順の規則がもう1本要る）。

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

- **戦略ランタイムが返した記録の処理点は、エンジンの時計で番号を振り直す**【確定】（v1.3。D02 §3.3）。ランタイムは自分の `step` の中で 0 から数えるため、そのまま残すと、エンジンが同じフェーズで刻んだ記録（受付の判断・保護水準の適用・台帳 snapshot・末尾の取消）と `(時刻, フェーズ, 通し番号)` が重なり、判断履歴を処理点の順に読み直したときにどちらが先だったのかが決まらない。フェーズはランタイムが選んだものをそのまま使い、**番号だけ**をエンジンの採番列へ載せ替える。対象は取引機会の遷移（`OpportunityTransition.at`）で、これが段階2で処理点を持つ唯一のランタイム側の記録である。**段階3 では待機の出来事（`WaitEvent.at`）と取引機会の有効性の再検査（`ValidityRecheck.at`）も対象に入る**（v1.5。D05 §6.8・§7.3 が `RuntimeStepResult` に2つの列を足したため）。待機の開始・入力の到着・再開・期限到達・追い越しと、再検査の結果は、いずれもランタイムが `step` の中で刻む処理点であり、取引機会の遷移と同じ理由で番号を振り直さないとエンジン側の記録と順序が決まらない。フェーズはランタイムが選んだものをそのまま使う規則も同じである。
- 台帳の現在状態は `AccountLedger` 1つの**不変値**で表し、エンジンは可変参照を1つだけ持つ（第1節の例外）。
- **受付済み注文の本体（`AcceptedOrder`）も台帳が持つ**【提案】。受付と候補の始値は別の判断時点になりうるため（`entry_delay_bars=1`、遅延シナリオ、期限が次の足に及ぶ場合）、約定の時点で数量・期限・固定した候補（`ExecutionCommitment.eligibility`）・エントリー条件を引けなければならない。`OrderState` だけではこれらを復元できず、`TraceSink` は書き出し専用（第9.1節）で検索元にならない。終端した注文は `order_states` に残したまま `orders` から外さず、run 中は保持する（第9.1節の書き出しと台帳は別物）。
- **確定単位**は「検査 → 新しい `AccountLedger` の組み立て → 参照の差し替え」の3段で行い、差し替えが起きるまで外へ公開しない。差し替えは1文であり、途中まで更新した状態は観測できない。
- 1つの確定単位に含めるものを次のとおり固定する。`processed_event_ids` への追加を同じ単位に含めるのが冪等性の実装である【合意済み】上位 §4.7.13 A。

各確定単位は `OrderEvent` そのものを含める【提案】。第5.1節は `OrderEvent` の列から `OrderState` を投影すると定めており、識別子だけを残すと `from_status` / `to_status` / 処理点 / 理由 / `fill_id` を復元できず、表8（`ORDER_EVENTS`）と注文状態が食い違う。

| 確定単位 | 含める変更 |
|---|---|
| **エントリーの受付**（第6.5節） | `OrderRequest` ／ `AttemptDecision` ／ `AcceptedOrder` ／ `OrderEvent(None → PENDING)` ＋ そこから投影した `OrderState` ／ `RiskReservation` ＋ `ReservationState(HELD)` ／ `processed_event_ids` への `event_id` の追加 |
| **決済の受付**（第6.5節） | `OrderRequest` ／ `AttemptDecision`（`assessment_ref` は `None`）／ `AcceptedOrder`（`AcceptedCloseTerms`）／ `OrderEvent(None → PENDING)` ＋ `OrderState` ／ `processed_event_ids` への追加。**予約は作らない**（`AcceptedCloseTerms` は予約 ID を持たず、決済は新規予約を作らない【合意済み】上位 §4.7.15 B）。既存建玉の割当は決済の約定まで保持する |
| エントリー約定（第7.1節） | `FillRecord` ／ `Position`（作成、初期損切り有効化）／ `OrderEvent(PENDING → FILLED, fill_id)` ＋ `OrderState` ／ `ReservationState(TRANSFERRED)` ＋ `PositionRiskAllocation` ／ `RiskMeasurement` ／ `processed_event_ids` への追加 |
| 決済約定（第7.3節） | `FillRecord` ／ `Position`（終了）／ `OrderEvent(PENDING → FILLED, fill_id)` ＋ `OrderState` ／ 実現損益・費用・balance ／ `PositionRiskAllocation`（解放）／ `processed_event_ids` への追加 |
| **エンジン生成の即時決済**（第7.3節・第7.5節） | 上の「決済の受付」と「決済約定」を**1つの差し替えにまとめる**。`OrderRequest` ／ `AttemptDecision` ／ `AcceptedOrder`（`eligibility` は `ProtectionHit` または `ImmediateAfterFill`）／ `OrderEvent(None → PENDING)` と `OrderEvent(PENDING → FILLED, fill_id)` の2件 ／ `FillRecord` ／ `Position`（終了）／ 実現損益・費用・balance ／ `PositionRiskAllocation`（解放）／ `processed_event_ids` への追加 |
| 終端（期限・取消） | `OrderEvent(PENDING → EXPIRED \| CANCELED, reason)` ＋ そこから投影した `OrderState`（`terminal_reason` を含む）／ `ReservationState(RELEASED)` ／ `processed_event_ids` への追加 |

- **エンジンが生成する即時決済は、受付と約定を1つの確定単位にまとめる**【提案】。保護水準の到達（第7.3節）と約定直後の緊急決済（第7.5節）は候補の始値を待たず、受付と約定が同じ処理点で確定する【合意済み】上位 §4.7.15 B。受付の単位と約定の単位を順に適用すると、その間で失敗したときに「即時に約定するはずのエンジン注文が `PENDING` のまま残る」状態が保存されてしまう。
- 受付前拒否は台帳を変えない。`OrderRequest` と `AttemptRejected` を trace へ残す【合意済み】上位 §4.7.15 A（「受付前拒否もこの記録に紐付く」）。**`RiskAssessment` を残すのは、第6.4節の手順3（参照価格の固定）まで到達した試行だけ**とする【提案】。期限内に候補が無い（`NO_CANDIDATE`）・末尾（`RUN_END`）・週末持ち越し禁止（`CARRY_NOT_ALLOWED`）・参照価格が取れない（`DATA_ERROR`）・残高が非正（`RISK`、手順1）のように手順3 より前で終わる拒否では、`ReferenceQuote` も予算も存在しないため、行を作れば架空の値を書くことになる。その場合 `AttemptRejected.assessment_ref` は `None` とし、拒否の理由は `Reason` だけが持つ。**手順3 以降で拒否した場合は行を作り**、到達しなかった手順の項目を `None`、`reached_step` に到達した手順の番号を入れる（第6.4節の手順8）。
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

- **遷移3（期限切れ）は、段階2ではどの実行ポリシーの設定でも発生しない**【提案】。受付時に「期限内に候補の始値があること」を検査したうえで候補を固定するため（第5.3節）、受付が成立した時点で `open_time < expires_at` が成り立っている。候補の始値の時刻には必ず判断時点があり（D03 §7.1 の `ExecutionOpen`）、その時点では rank 2 の判定 `expires_at <= T` が成り立たないまま rank 11 で約定する。候補足が実際には無ければ実行失敗（`DATA_ERROR`、遷移5）になる。有効時間を短くしても、候補が期限外になって**受付前拒否**（`NO_CANDIDATE`）になるだけで、注文が作られないため期限切れも起きない（[T01](../traces/T01_paper_trace.md) 経路6 の表）。
  - したがって**段階2の意味論テストは、受付を経由せずに `AcceptedOrder` を直接組み立てて状態機械だけを検証する単体テスト**として書く。実行の中で期限切れが起きるようになるのは、価格条件を待つ注文（指値・逆指値）を入れる段階6（第12節）である。「検証戦略 A では通常発生しない」とだけ書くと、どう検証すればよいか分からない遷移が表に残る。
- **遷移4（末尾の取消）が起きるのは、約定後の受付（rank 13）で受け付けた決済注文の候補の始値が run_end と一致した場合だけ**である【提案】。理由と、候補が run_end 以降になる注文を受け付ける規則（Q11 決定、選択肢1）は第5.3節に書く。
- 現在状態は `AcceptedOrder` を書き換えず、`OrderEvent` の列から `OrderState` へ投影する【合意済み】上位 §4.7.15 B。
- 終端状態からの遷移は表に無い。終端済みの注文への遷移要求は `KernelValueError` で拒否する【提案】（D05 §7.2 の取引機会と同じ扱いに揃える）。
- `ACCEPTED` は状態ではなく受付イベントであり、直後の状態は `PENDING`【合意済み】上位 §4.7.13 A。
- エントリー・戦略の決済要求・エンジンの緊急決済・保護水準の到達による決済は、**すべて同じ状態機械を通す**【合意済み】上位 §4.7.13 A。約定できる時点と会計処理だけが目的によって異なる。
- 遷移6の `POSITION_CLOSED` は D02 §8.1 に登録済みの語である【合意済み】。

### 5.2 発注試行と受付前拒否【提案】

- `AttemptId` は要求組立で採番し、1つの `OrderRequest` に1つ対応する【合意済み】上位 §4.7.15 A。
- 拒否は `AttemptRejected(attempt_id, reason, assessment_ref)` として記録し、注文も予約も作らない。同じ `attempt_id` で再試行しない【合意済み】同節。
- 段階2で使う拒否の理由コードは `RISK` / `NO_CANDIDATE` / `RUN_END` / `DATA_ERROR` / `CARRY_NOT_ALLOWED` / **`PROTECTION_INVALID`** の6件である【合意済み】D02 §8.1。最後の1件は**保護水準の妥当性違反**（買いの損切りが判断時の売却側価格以上など、上位 §4.7.9 B）のために 2026-09-21 の Q6 決定（選択肢1）で加えた語で、上位設計書 §4.7.14 と D02 §8.1 を同じ PR で改訂済みである。損切りの向きの違反は戦略の宣言の誤りであり、口座のリスク上限の違反（`RISK`）と原因も対処も違うため集計で分ける。**不採用**: `RISK` に畳んで詳細で区別する案（リスク上限の違反率に設計ミスが混ざる）、`DATA_ERROR` を使う案（データの問題と宣言の問題が区別できなくなる）。
- **6件のうち、検証戦略 A の宣言から実行中に起きるのは3件だけ**である【提案】（[T01](../traces/T01_paper_trace.md) 経路4〜8）。残り3件は単体テストで検証する。これを書かないと、意味論テストで再現できない理由コードが表に残る。

| 理由コード | 検証戦略 A の実行で起きるか | 検証の仕方 |
|---|---|---|
| `RISK` | 起きる（建玉枠に当たる2件目のエントリー） | 意味論テスト |
| `CARRY_NOT_ALLOWED` | 起きる（週の終わりの判断時点） | 意味論テスト |
| `RUN_END` | 起きる（末尾の判断時点） | 意味論テスト |
| `NO_CANDIDATE` | **T01 の8経路では代表理由として現れない**。`entry_delay_bars=0` では候補が判断時点と同じ執行足の始値になり、有効時間20分の内側に必ず入る。週末境界では成立するが、そこでは `CARRY_NOT_ALLOWED` が代表理由になる（下の順位表）。**平日の休場（祝日・短縮セッション）をまたいで候補が有効時間の外になる場合はこれが代表理由**になる（第5.3節。週末だけが `CARRY_NOT_ALLOWED`） | 単体テスト（`entry_delay_bars=1` と短い有効時間を与えた受付） |
| `PROTECTION_INVALID` | **起きる**。損切りは評価系列（1時間足）の安値から作られ、妥当性の検査は執行系列（15分足）の終値と比べる。両者は**別々の入力ファイル**であり、1時間足は15分足から集約していない（D03 §2・§5.1）。同じ時刻の終値が一致する保証は無く、食い違って損切りが執行系列の終値以上になれば発生する | 意味論テスト（2系列の終値を意図的にずらした人工データ）＋ 単体テスト |
| `DATA_ERROR` | 起きない。参照価格は直前に完了した執行足から引け、執行足が欠けていればそれは実行失敗になる（第10.4節） | 単体テスト |

- **同じ試行に2つ以上の拒否理由が同時に成立したときの代表理由**を次の順で決める【提案】。上位 §4.7.14 は「複数理由が成立した場合の代表理由と診断一覧の扱い」を後続の具体化に委ねており、T01 で週末持ち越し禁止と候補なしが同時成立する経路が見つかったため（[T01](../traces/T01_paper_trace.md) 経路7）ここで固定する。

| 順位 | 理由コード | 先に判定する理由 |
|---|---|---|
| 1 | `RUN_END` | 末尾では他の検査の結果によらず受け付けない【合意済み】上位 §4.7.13 F（「run_end で新たに受付を試みた場合は `NO_CANDIDATE` へ丸めず `RUN_END` を記録する」） |
| 2 | `CARRY_NOT_ALLOWED` | 週末持ち越し禁止は「有効時間を長くしても回避できない」規則であり【合意済み】上位 §4.7.13 B、有効時間の問題（`NO_CANDIDATE`）へ丸めると設定を延ばせば通ると読めてしまう |
| 3 | `NO_CANDIDATE` | 期限内に候補の始値が無い |
| 4 | `PROTECTION_INVALID` | 保護水準の妥当性（第6.4節の手順4） |
| 5 | `RISK` | 予算・数量・同時保持枠（第6.4節の手順1〜7） |
| — | `DATA_ERROR` | 上のどの判定も行えない（参照価格が取れないなど）ときにだけ使う |

代表理由は `AttemptRejected.reason` に1件だけ入れ、同時に成立した他の検査の結果は `RiskAssessment.checks`（審査に入れた場合）と表15 の根拠記録に残す。**不採用**: 理由を複数持てる形にする案（D02 §8.2 の `Reason` は1コードであり、集計の主キーが2つになる）。
- 受付前拒否は取引機会側の `ORDER_ATTEMPT_REJECTED` で終端させる【合意済み】D05 §7.2 の遷移6。エンジンは `AdmissionNotice(accepted=False, reason=...)` を返すだけで、機会の状態は変えない（第6.6節）。

### 5.3 有効期限と候補 open【提案】

- `expires_at = accepted_at.time + valid_for`（UTC 絶対時刻）【合意済み】上位 §4.7.13 B。`valid_for` は `ExecutionPolicy` の `entry_valid_for` / `close_valid_for` から取る。D05 §4.3(3) の `OrderIntent.expiry=None` は「この既定値に従う」を意味する【合意済み】D05 §12。初版値は第7.1.1節（エントリー20分・決済20分、Q5 決定）。
- 約定可能条件は `open_time < expires_at`。同時刻なら期限切れを先に確定する（rank 2 < rank 11）【合意済み】同節。
- 期限は足の到着に依存せず休場中も進む。受付後の延長は行わない【合意済み】同節。
- **受付時点で期限内に候補 open が無ければ受付前拒否**（`NO_CANDIDATE`）。候補はカレンダー（`backtest.application.ports` の `Calendar`）と執行系列の足スケジュールから決め、将来価格や実ファイルの欠損を候補選択に使わない【合意済み】同節。
- **足スケジュールは執行系列のポートの操作として受け取る**【確定】（2026-09-22 の人間の決定。PR #19）。`ExecutionSeries` に4つ目の操作 `next_scheduled_open_after(t)` を置き、候補の始値はこれで選ぶ（D03 §6.3 を同じ PR で改訂した）。実在する足を辿る操作（`next_bar_key_after`）で選ぶと、休場でない区間で足が1本欠けているだけで候補が次の足へずれ、**データの欠損が受付結果を変える**。実在する足を辿る操作は、実際に読んだ足を根拠記録へ載せるときにだけ使う。**不採用**: 実在する足から探し続ける案（上記のとおり本節の規則に反する）、受付層がカレンダーと時間足定義を直接持って予定を計算する案（同じ計算が市場データ側と受付層の2か所に割れる）。
- 候補が週末休場をまたぐ場合は `CARRY_NOT_ALLOWED` で受付前拒否する。有効時間を長くしても回避できない【合意済み】同節。週末をまたぐ候補は有効時間の外にもなるため `NO_CANDIDATE` も同時に成立するが、**代表理由は `CARRY_NOT_ALLOWED`** とする（第5.2節の代表理由の順位）。
- **この規則が効くのは週末だけである**【確定】（2026-09-22 の人間の決定。PR #19）。週末境界の判定には、カレンダーの**休場を適用する前の週の開場区間**を返す操作（`Calendar.weekly_session_at(t)`、D03 §3.4 を同じ PR で改訂）を使い、判断時点を含む週の開場区間の終わりより後に候補があるときだけ `CARRY_NOT_ALLOWED` にする。休場を取り除いた取引セッション（`Calendar.sessions`）で判定すると、**祝日を1日宣言しただけで週が2つに割れ**、祝日や短縮セッションをまたぐ候補まで週末持ち越しとして拒否される。平日の休場をまたいで期限内に候補が無いのは有効時間の問題なので `NO_CANDIDATE` であり、有効時間を延ばせば通る。判断時点がどの週の開場区間にも入らない場合（週の終わりちょうど、または週と週のあいだ）は週末持ち越しとして扱い、診断に載せる週の終わりは観測できる直前のセッション終端を使う。**不採用**: 受付側が曜日と時刻から週の開閉を計算し直す案（同じ計算が市場データ側と受付側の2か所に割れる。D03 §6.3 で足のスケジュールについて退けたのと同じ理由）、祝日も週末と同じに扱う案（有効時間を延ばせば通る拒否に「延ばしても回避できない」理由コードが付き、集計で設計上の制約と設定の問題が混ざる）。
- **候補の始値が run_end 以降になる注文は、そのまま受け付け、末尾で `CANCELED`（理由 `RUN_END`）にする**【合意済み】（Q11 決定、選択肢1）。上位 §4.7.13 F は「候補が run_end 以降と受付時に分かる場合の早期拒否」を詳細設計へ委ねているが、同節の時刻表は「run_end=22:15、期限22:20 → 末尾 open は実行せず、**受付済みなら CANCELED/RUN_END**」と書いており、受け付けたうえで末尾に取り消す経路を前提にしている。本書はその読み方を採り、早期拒否を入れない。早期拒否を入れると、末尾手順5（残存する受付済み注文の取消、第10.1節）が段階2で一度も起きなくなる。**不採用**: 受付時に `RUN_END` で受付前拒否する案（Q11 の選択肢2。約定できない注文を作らずに済むが、末尾手順5 が段階2で常に空になり、テストの書けない手順が残る）、実験設定で選べるようにする案（Q11 の選択肢3。両方を比較できるが、段階2で使わない設定が1つ増える）。
- **この規則の下で、判断時点をまたいで `PENDING` のまま残る注文は次の1種類だけ**である【提案】。`entry_delay_bars=0` では rank 10 で受け付けた注文は同じ判断時点の rank 11 で約定するため、残るのは**約定後の受付（rank 13）で受け付けた決済注文**（最初の適格な始値が次の執行足になる）だけである。その注文が run_end の直前の判断時点で受け付けられ、候補の始値が run_end と一致した場合に、第5.1節の遷移4 が起きる。

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
| `created_at` | `ProcessingPoint(T, その要求を組み立てたフェーズ, 連番)`。フェーズは第6.2節の表の「受け付けるフェーズ」と同じ（エントリーは `ADMISSION`、保護水準の到達は `EXECUTION_BAR_COMPLETE`、緊急決済は `EXECUTION_OPEN`、約定後の決済は `POST_FILL_ADMISSION`）。常に `ADMISSION` を刻むと、保護決済で「要求 rank 10・約定 rank 0」と因果順が逆転し、約定後の決済が rank 12 の評価より前に生まれたように見える |
| `origin` | 戦略由来は `STRATEGY`、保護到達・緊急決済は `ENGINE`。戦略が `ENGINE` を自己申告する経路は作らない【合意済み】上位 §4.7.15 A |
| `payload` | `EntryProposal` からは `EntryRequest`、`ManagementRequest(ClosePosition)` からは `CloseRequest` |
| `evidence_ref` | trace へ保存した根拠記録の参照（第9.2節） |

`EntryRequest` の各項目は `EntryProposal` から次のとおり埋める。`symbol` と `side` は `OrderIntent`、`protection.stop_loss` は `ProtectionLevels.stop_loss`、`exit_plan_ref` は `ExitPlanRef(compiled.compiled_ref, compiled.roles.exit の instance_id)`、`valid_for` は `OrderIntent.expiry` が `None` なら `ExecutionPolicy.entry_valid_for`。

**根拠の出力 ID の出どころ**【合意済み】（D05 v1.2 として本 PR で改訂済み）。上位 §4.7.8 は「注文意図・SL 算定について、使った出力 ID を記録する」ことを要求する。改訂前の D05 §3 の `EntryProposal` は `opportunity_id` / `order_intent` / `protection` / `decision_time` の4項目だけで `OutputId` を持たず、**エンジンは正常経路でも `OrderRequest` を組み立てられなかった**（[T01](../traces/T01_paper_trace.md) 経路1 で判明した阻害要因）。これは設計の選択ではなく欠落の補完であるため、**D05 §3・§6.2（v1.2）を本 PR で改訂し**、`EntryProposal` に `intent_output_id: OutputId` / `protection_output_id: OutputId`、`ManagementRequest` に `source_output_id: OutputId` を足した（第15節）。本書はそれを次のとおり受け取る。

| 本書の項目 | D05 の出どころ |
|---|---|
| `EntryRequest.intent_output_id` | `EntryProposal.intent_output_id`（`roles.order` の出力の `OutputId`） |
| `InitialProtectionPlan.source_output_id` | `EntryProposal.protection_output_id`（`roles.protection` の出力の `OutputId`） |
| `CloseRequest.source_output_id` | 戦略由来（`STRATEGY_EXIT`）は `ManagementRequest.source_output_id`（`roles.exit` の出力の `OutputId`）。エンジンが生成する決済（保護水準の到達・緊急決済）は戦略の出力を根拠に持たないため `None` |

**不採用**: trace の評価記録と出力記録を後から突き合わせて復元する案（同じ判断時点に同じ役割の出力が2件出た場合、機会 ID だけでは一意に決まらず、「名前だけ同じ指標の最新値を後から読み直して根拠を再構成しない」（上位 §4.7.8）に反する）、改訂が済むまで段階2の実装で受付の経路を「完了済み」として扱わない案（正常経路が実装できないまま段階2に入ることになる）。

### 6.2 段階2で組み立てる要求の種類【提案】

| 要求 | 出どころ | 受け付けるフェーズ | `origin` | `payload` |
|---|---|---|---|---|
| 新規エントリー | 第1回の `step` の `proposals` | `ADMISSION`（rank 10） | `STRATEGY` | `EntryRequest` |
| 戦略の全数量決済 | `ManagementRequest(ClosePosition())` | **それを返した `step` の直後の受付フェーズ**（第1回なら rank 10、第2回なら `POST_FILL_ADMISSION`（rank 13）） | `STRATEGY` | `CloseRequest(STRATEGY_EXIT)` |
| 保護水準の到達による決済 | 第7.3節 | `EXECUTION_BAR_COMPLETE`（rank 0）の中で組み立て、同じ確定単位で受け付ける | `ENGINE` | `CloseRequest(STOP_LOSS \| TAKE_PROFIT)` |
| 約定直後の緊急決済 | 第7.5節 | `EXECUTION_OPEN`（rank 11）の中で組み立て、同じ確定単位で受け付ける | `ENGINE` | `CloseRequest(EMERGENCY)` |

エンジンが生成する決済（保護水準の到達・緊急決済）が通常の受付フェーズを通らないのは、候補の始値を探す規則の対象外だからである【合意済み】上位 §4.7.15 B。`eligibility` が `ProtectionHit` / `ImmediateAfterFill` で固定されており、受付と約定が同じ確定単位で確定する（第4.4節）。

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
3. 参照価格を固定する。買いは ask、売りは bid。初版データは bid のみのため、ask は `SpreadModel` から導き、`ReferenceQuote.derived_from_spread=True` を立てる【合意済み】上位 §4.7.9 C。**どの足から bid を取るかは `ExecutionPolicy.reference_quote_source` が決め、段階2は `EXECUTION_SERIES_LAST_CLOSE`（直前に完了した執行足＝15分足の終値）とする**【合意済み】（Q10 決定、選択肢1）。上位 §4.7.9 B は「利用可能な観測または明示した発注判断時の参照価格を使う」とだけ定めており、系列と項目を特定していなかった（[T01](../traces/T01_paper_trace.md) 経路1 で判明）。執行系列を採るのは、約定の判定に使う価格と同じ系列から参照価格を取れば、参照価格と約定価格の差（約定ずれ）が系列差を含まなくなるためである。

`EXECUTION_SERIES_LAST_CLOSE` の具体的な引き方を次のとおり固定する【提案】。

| 事項 | 規則 |
|---|---|
| どの足か | **エンジンがその判断時点までに rank 0（`EXECUTION_BAR_COMPLETE`）で処理し終えた最新の執行足**。エンジンは `ExecutionBarComplete` を受けた `BarKey` を保持しており、`ExecutionSeries.bar(bar_key)`（D03 §6.3）で足を引ける |
| なぜ `MarketDataView` を使わないか | `MarketDataView.latest_available` は**戦略向け**のビューであり、期待足が未到着なら `LATEST_BAR_UNAVAILABLE` を返して古い足へ戻らない（D03 §6.2）。エンジンの参照価格は戦略向けの公開遅延とは別の可用性に従う【合意済み】上位 §4.7.12（「執行用データの可用性と戦略向け公開遅延の区別」） |
| 鮮度 | 直前に完了した執行足に限られるため、鮮度は**構造的に執行足1本分以内**に収まる。別の鮮度上限を置かない【提案】 |
| 記録 | `ReferenceQuote.source_bar` にその足の `BarKey`、`observed_at` にその足の `interval.end` を入れる |
| 取れない場合 | 完了した執行足が1本も無い判断時点（run の先頭）では参照価格を構築できないため `DATA_ERROR` で受付前拒否する（手順3より前の拒否として `RiskAssessment` を作らない）。執行足のデータ自体が欠損していた場合は受付の問題ではなく**実行失敗**である（第10.4節） |
| 系列をまたぐこと | **参照価格は、戦略が判断に使った系列とは別の系列から来る**。初版データでは15分足と1時間足が別々の入力ファイルであり、1時間足は15分足から集約していない（D03 §2・§5.1）。したがって同じ時刻でも終値が食い違いうる。戦略の側では妥当な保護水準でも、執行系列の終値と比べると手順4 で違反になることがある（`PROTECTION_INVALID`、第5.2節）。**これは不具合ではなく、約定の判定に使う系列で検査するという規則の帰結**であり、`ReferenceQuote.source_bar` にどの系列のどの足を使ったかが残る |**不採用**: 戦略が判断に使った評価系列の終値を使う案（評価系列と執行系列の粒度が違うと、約定ずれに系列差が混ざる）、判断時点の執行足の始値を使う案（rank 11 はまだ処理していないため先読みになる）。
4. 保護水準の妥当性を検査する。買いの損切りは判断時の bid より下、売りは ask より上。違反・不正数値・価格情報の不足は `PROTECTION_INVALID` で拒否する（Q6 決定、選択肢1）。ここでいう判断時の bid / ask は手順3で固定した `ReferenceQuote` と同じ足から取り、買いの検査には bid（売却側）、売りの検査には ask（購入側）を使う。
5. 損切りを価格刻みで丸める（第6.5節）。`P_limit = P_ref + d × Δ`、`R(Q) = d × (P_limit − S) × Q × X + C(Q)`。`d × (P_limit − S) > 0` を要求する。`C(Q)` は `CostModel` から計算した数量比例の費用予算である（第7.6節）。
6. `R(Q) <= admission_budget` を満たす最大の数量を数量刻みで**切り下げ**て求める。最小数量未満なら拒否（`RISK`）。切り上げない。
7. 丸め後の数量で `R(Q)` を再計算し、口座制約（総量・数量上限）を再検査する。
8. **到達した段までの入力と結果**を `RiskAssessment` に残し、`reached_step` にどの手順まで進んだかを入れる【提案】。審査は途中の手順で拒否して終わることがあり（手順4 の保護水準の妥当性違反、手順6 の最小数量未満）、その先の手順が作る値は**存在しない**。そこで `stop_after_rounding` / `adverse_fill_limit`（手順5）、`quantity_step` / `quantity` / `cost_budget` / `conversion`（手順6）、`reservation_amount`（手順7）を省略可能にし、到達しなかった手順の項目は `None` にする。`reached_step` があるため、`None` が「計算できなかった」のか「計算したが値が無い」のかを読み分けられる。**不採用**: 必須のままにして架空の値を入れる案（拒否の記録が実際に使った値を持たなくなり、ADR-0012 の「実際に使用した値を保持する」に反する）、途中で拒否した審査は記録しない案（どの検査で落ちたかの実値が消え、`checks` の診断価値が無くなる）、段ごとに別の型を作る案（表6 の主キーが型ごとに分かれ、試行から審査を1件で引けなくなる）。**手順1〜8 はエントリー要求にだけ適用する**【提案】。決済要求は新規リスク予算の審査対象ではなく（上位 §4.7.15 A）、参照価格・丸め前後の損切り・予約額といった `RiskAssessment` の必須項目がそもそも存在しない。したがって決済の受付では `AttemptAccepted.assessment_ref` を `None` とし、代わりに対象建玉の存否・数量・競合・期限・執行条件を検査する【合意済み】同節。この検査の結果は `AttemptRejected.reason` と表15 の根拠記録に残す。**不採用**: 決済用に空の `RiskAssessment` を作る案（架空の参照価格と予約額を記録することになる）、受付結果をエントリー用と決済用の2つの union 要素に分ける案（受付済み注文の側が既に `AcceptedEntryTerms` / `AcceptedCloseTerms` で目的を区別しており、試行結果まで分けると区別が2か所になる）。`assessment_id` は `IdAllocator.next(EvidenceId)` で採番し、`RiskAssessmentRef` はこの値だけを持つ（参照と実体で識別子を二重に持たない）。`attempt_id` を `RiskAssessment` 自身にも持たせるのは、拒否された試行でも審査の記録から試行へ戻れるようにするためである。`checks` には各検査の名前・上限・観測値を `RiskCheckResult` で入れる。D02 §8.2 の `RiskRejectionDetail` は `limit` と `observed` の型一致を要求するため、同じ組で作る。

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
### 7.1.1 実行ポリシーの初版値【合意済み】（Q3・Q4・Q5 決定、いずれも選択肢1）

2026-09-21 に決定した `ExecutionPolicy` の初版値。いずれも実験前に固定し、実行中に変更しない【合意済み】上位 §4.7.9 A。run manifest の `execution_policy_ref` から辿れる（第9.3節）。

| 項目 | 初版値 | 決定 | 根拠 |
|---|---|---|---|
| `entry_delay_bars` | `0`（受付後の最初の適格な始値で約定） | Q3 | 上位 §4.7.11 が基本モデルとして確定したのが0であり、1は保守的な変種として実験設定で切り替える位置付け。T の受付が `[T, T+15m)` の始値で約定し、[T01](../traces/T01_paper_trace.md) の時刻表と一致する |
| `adverse_fill_limits[USDJPY]`（許容不利約定幅 Δ） | `0.05`（円） | Q4 | 上位 §4.7.9 C の数値例と同じ幅。T01 の手計算をそのまま検算に使える |
| `entry_valid_for` | 20分 | Q5 | 執行足15分より長く、2本目には届かない。上位 §4.7.13 F の時刻表（22:00:02 受付・候補22:15・`expires_at=22:20`）と同じ幅 |
| `close_valid_for` | 20分 | Q5 | 同上。エントリーと決済で同じ幅にし、末尾付近の挙動を1つの規則で読めるようにする |

- **銘柄が `adverse_fill_limits` に無ければ run を開始しない**【提案】。`RunConfig.execution_series.symbol` が表に無い状態で受付に入ると、Δ が無いまま予約額を計算することになる。実行前のデータ能力検査（第10.5節）で照合し、不足は `FAILED_CAPABILITY` とする。**不採用**: 既定値を置く案（実験前に固定すべき値を設計が決めることになる）、受付時に拒否する案（設定の不足で全試行が拒否される run が「正常完走」になる）。
- `entry_delay_bars=1` は保守的な変種として実験設定で選べるが、段階2の検証戦略 A の基準実行では使わない【合意済み】上位 §4.7.12。

### 7.2 spread モデル【提案】

段階2の受入れデータは bid のみである【合意済み】D03 §3.1。`FixedSpread(offset)` だけを持ち、`ask = bid + offset` とする。spread を価格にも費用にも重複して加算しない【合意済み】上位 §4.7.9 C。`offset` の値は `ExecutionPolicy` ではなく `CostModel` に置き、実験前に固定する。可変 spread・時間帯別 spread は段階6（第12節）。

### 7.3 保護水準の到達判定【提案】

- 判定は `EXECUTION_BAR_COMPLETE` フェーズで、**その足の開始前に有効だった**保護水準について行う【合意済み】上位 §4.7.7。終値で計算した更新をその足の過去の高値・安値へ適用しない。
- **新規建玉の初期保護水準（初期の損切りと、同じ判断時点で設定される初期の利確）は、約定した執行足の開始時点から有効**とする【提案】。`ProtectionState.effective_from` に約定した足の `BarKey` を入れ、その足の到達判定の対象に含める。初期の利確が `POST_FILL_EVALUATION`（rank 12）で設定されるのに対し、その足の到達判定は次の判断時点の `EXECUTION_BAR_COMPLETE`（rank 0）で行われるため、判定の時点には既に設定済みである。上位 §4.7.7 が「新規約定直後の初期 SL/TP に既存建玉の規則を一律適用してはいけない」と述べているのはこの点であり、`effective_from` を持つことで既存建玉（次の執行足から有効）と新規建玉（約定した足から有効）を同じ1つの規則で扱える。**不採用**: 新規建玉だけ別の判定経路を作る案（同じ到達判定が2か所になる）。
- 判定価格は買い建玉が bid、売り建玉が ask【合意済み】上位 §4.7.12。
- 到達したら、エンジンが `CloseRequest(cause=STOP_LOSS | TAKE_PROFIT)` → `AcceptedOrder`（`eligibility=ProtectionHit(...)`）→ `FillRecord` を生成する【合意済み】上位 §4.7.15 B。通常の候補 open 規則は通さない。
- `FillRecord.execution_time` は `BarExecutionInterval(bar_key, interval)`。足内の正確な到達時刻を観測できないため、終値時刻を到達時刻として記録しない【合意済み】同節。
- 片側だけに触れた場合は `ResolutionMethod.SINGLE_HIT` として記録し、両側に触れた場合は第7.4節へ進む。
- **エンジンが生成する決済要求の `valid_for` には `ExecutionPolicy.close_valid_for` をそのまま入れる**【提案】。`CloseRequest.valid_for` は型として必須であり（上位 §4.7.15 A）、`eligibility` が `ProtectionHit` / `ImmediateAfterFill` の決済は受付と約定が同じ確定単位で終わるため `expires_at` が判定に使われることはない。専用の値や0を入れると、表7 の `expires_at` 列に「約定済みなのに既に期限切れ」という読み方のできる行ができる。**不採用**: `valid_for` を省略可能にする案（上位 §4.7.15 A が確定したフィールドを本書が緩めることになる）。

### 7.4 足内競合解決契約【提案】（ADR-0030 の実施）

契約そのものは確定済み【合意済み】ADR-0030・上位 §4.7.7。本書は宣言形・検査・手順・記録・能力検査を埋める。

**宣言形**: `ResolutionHierarchy(levels: tuple[SeriesId, ...])` を粗い順に持ち、`levels[0]` は `RunConfig.execution_series` と一致しなければならない。**置き場所は `ExecutionPolicy` の項目とする**【合意済み】（Q8 決定、選択肢1）。階層は「どの足で約定を判定するか」という約定の意味そのものであり、執行足の指定と同じ版で固定される方が、両者が食い違った組み合わせを作れない。参照が1つ増えないのも利点である。**不採用**: 独立したポリシー（`IntrabarResolutionPolicy`）にして別の版参照を持つ案（階層だけを差し替える実験はやりやすいが、参照が1つ増える）、実験設定の直下に置く案（版として固定されず、再現性の識別から漏れる）。

**適合検査**（run 開始前。結果は `HierarchyCheckResult` として `DataCapabilityReport.hierarchy_checks` に残す）。検査が比べるのは区間・価格基準・足境界・利用可能時刻であり、金額でも比率でもないため、リスク審査の `RiskCheckResult`（`limit` と `observed` が `Money \| Decimal`）を流用せず専用の型を置く【提案】。流用すると不一致の実値を型どおり記録できず、架空の数値を入れるか診断を捨てることになる。

| # | 検査 | 不合格のとき |
|---|---|---|
| 1 | 隣り合う階層で、下位足が上位足の区間を**完全に被覆**する（区間の和が親の区間に等しく、隙間も食み出しもない） | 実行不可 |
| 2 | 価格基準（`PriceBasis`）が階層内で一致する | 実行不可 |
| 3 | 下位足の境界が親足の境界に整列する（親の `interval` の両端が子の境界と一致する） | 実行不可 |
| 4 | 下位足の `available_at` が親足の `available_at` 以下である（親を解決する時点で子が見えている） | 実行不可 |
| 5 | run 区間と銘柄について、階層の各系列が snapshot に欠損なく存在する（D03 §3.9 の `IntegrityReport`） | 実行不可 |

各検査が `HierarchyCheckResult` に埋める項目は次のとおりとする【提案】。不合格の実値を型どおり残せるようにするためで、どの検査でも埋まらない項目は `None` にする。

| 検査 | 埋める項目 |
|---|---|
| 1（被覆） | `parent_bar`、`expected_interval`（親足の区間）、`child_intervals`（子足の区間を古い順に全件）、`coverage_gaps`（親の区間のうち子足が覆っていない区間）、`coverage_overlaps`（2つ以上の子足が重なった区間）。子足の区間の和は単一の半開区間にならないため、1つの `Interval` に畳まない |
| 2（価格基準） | `expected_basis`（親の `PriceBasis`）、`observed_basis` |
| 3（足境界） | `parent_bar`、**`child_bar`**（ずれた子足）、`expected_boundary`（親の境界時刻）、`observed_boundary`（その子足の対応する端の時刻） |
| 4（利用可能時刻） | `parent_bar`、`child_bar`、`expected_available_at`（親の `available_at`）、`observed_available_at` |
| 5（存在） | `parent_bar`、`expected_interval`（期待した区間）、`child_intervals`（実際に存在した足の区間）、`coverage_gaps`（存在しなかった区間） |

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
- 価格に反映済みの費用（slippage・spread）を金額として二重計上しない【合意済み】全体計画 §5.4.3。記録のために `CostEntry` を残すが、`amount` は balance に反映させない参考値であることを `CostEntry` の区分で表す。**区分は執行モデルの滑り（`SLIPPAGE_IN_PRICE`）と提示価格の幅（`SPREAD_IN_PRICE`）に分ける**【提案】。両方を1つの区分に畳むと、D07 が「執行モデルを変えたときに動く費用」と「データの提示価格に由来する費用」を分けて集計できない（[T01](../traces/T01_paper_trace.md) 経路8 で判明）。bid のみの系列では、買い側の約定にだけ `SPREAD_IN_PRICE` が立つ。
- **費用予算 `C(Q)` は `CostModel` から計算する**【提案】。`C(Q) = commission_per_unit × Q × 2 ＋ close_slippage × Q`（往復手数料と損切り決済の slippage）であり、数量に比例する。`RiskPolicy` に固定額の費用予算を持たせない（T01 経路1 の手順6 で、固定額では表せないことが判明した）。計算結果は `RiskAssessment.cost_budget` に `Money` として残す。**不採用**: `RiskPolicy` に固定額の上乗せを置く案（数量に比例する部分と固定額の2か所に費用予算の定義が分かれる）。
- `CostEntry` は原通貨額・口座通貨計上額・換算根拠を持つ【合意済み】上位 §4.7.15 C。
- 費用予算 `C(Q)`（予約に含める分）は往復手数料と損切り決済の slippage とし、Δ に含めたエントリーの slippage を重複加算しない【合意済み】上位 §4.7.9 C。

## 8. 口座・建玉（`portfolio`）

### 8.1 台帳【提案】

- `balance` は実現損益・費用を反映した口座残高で、未実現損益を含めない。`equity` は含み損益込みの MTM 資産【合意済み】上位 §4.7.10。予算の分母は `balance`。
- **run 中の `LedgerSnapshot.equity` の含み損益は、その判断時点の直前に完了した執行足（`RunManifest.execution_series`）の終値で評価する**【合意済み】（Q13 決定、選択肢1）。買い建玉は決済が売りであるため終値（bid）をそのまま使い、売り建玉は決済が買いであるため第7.2節の spread モデルで導いた ask を使う（買いは bid・売りは ask という向きは第10.3節の `equity_with_mtm` と同じである）。**受付時の参照価格（第6.4節の手順3、`ReferenceQuoteSource.EXECUTION_SERIES_LAST_CLOSE`、Q10 決定）と同じ出どころ**にすることで、同じ判断時点に「約定ずれを測る基準の価格」と「含み損益を評価する価格」の2つが並ばない。評価価格が取れない判断時点は第10.4節の `DATA_ERROR` として扱い、架空の価格で含み損益を埋めない。**不採用**: 執行中の足の始値を使う案（選択肢2。判断時点には最も近いが、参照価格と別の出どころになり、同じ時点の2つの価格が食い違う）、判断時点ごとに決済側の価格を取得し直す案（選択肢3。規則は1つで済むが、台帳の更新そのものが市場データの取得経路を持つことになる）。
- この規則が無いと**最大ドローダウンが判断履歴から一意に決まらない**【合意済み】D07 §5.4。同じ snapshot でも執行足の終値と始値のどちらを使うかで含み損益が変わるため、評価側（D07）が値を再現できない。
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
| `ClosePosition()` | `CloseRequest(STRATEGY_EXIT)` を組み立て、その `step` の直後の受付フェーズへ回す（第6.2節の表） | 対象建玉が開いていること |

- 適用フェーズは、**その管理要求がどちらの `step` から来たかで決まる**【確定】（Q25 決定、2026-09-23、選択肢1）。第2回の `step`（rank 12）が返した要求は `POST_FILL_EVALUATION` で適用する（段階2 はこれだけである）。**足の確定で起動した第1回の `step`（rank 4〜9）が返した保護水準の更新は、受付（rank 10）の直前に適用し、処理点のフェーズは `ADMISSION` とする**（第4.2節の手順6。段階3 のトレーリングがこれに当たる）。新しいフェーズは足さない。更新を受付より前に効かせるのは、**同じ判断時点で新しい注文を受け付ける場合に、その注文のリスク審査が更新後の保護水準を見るべきだから**である。約定の後（rank 12）へ回すと、同じ判断時点の約定が古い損切り水準で判定される。**不採用**: 保護水準の更新だけのために rank 9 と rank 10 のあいだへ新しいフェーズを足す案（フェーズ集合が15件から16件に増え、run manifest に記録するフェーズ集合と D07 が読む `at_phase` の値域が変わる）、rank 12 を通知の有無によらず毎判断時点で実行する案（rank 11 の約定処理の後に適用されるので `effective_from`＝次の執行足の意味が1本ずれる）。
- `effective_from` は次の2つに分ける【提案】。

| 区分 | 条件 | `effective_from` |
|---|---|---|
| **初期の保護水準** | 対象建玉が**同じ判断時点の `EXECUTION_OPEN` で約定**し、`take_profit` がまだ未設定である | **約定した執行足**（初期の損切り水準と同じ、第7.3節） |
| それ以降の更新 | 上記以外（段階3のトレーリングなど） | 次の執行足 |

初期の利確を約定した足から有効にするのは、上位 §4.7.7 が「新規建玉は約定 → 初期損切りの有効化 → Exit による初期利確の算定を行い、**同じ足のその後の値動きに対する保護判定の対象にする**」と定めているためである。D05 §8 も「次の足まで利確が無い」構成を排除している。次足からにすると、約定した足の中で利確に触れた取引を見逃し損益が変わる。既存建玉への更新だけが「終値で計算した更新はその足の過去の高値・安値へ適用しない」（上位 §4.7.7）の対象である。
- **保護水準の更新の検査に失敗した場合は、その要求だけを適用せずに記録し、run は止めない**【提案】。丸めの結果 `take_profit` が約定価格と同値以下（買い）になる場合や、対象建玉が方向と合わない場合がこれに当たる。理由は `PROTECTION_INVALID`（第5.2節で加えた語）とし、表12（`MANAGEMENT_APPLICATIONS`）に「適用しなかった要求」として残す。建玉は利確を持たないまま損切りだけで継続する。**不採用**: run を失敗させる案（戦略の宣言の誤りは判断履歴に残して診断する対象であり、データ誤りと同じ扱いにしない。第4.2節が run を止めるのは部品の評価が `Failed` になった場合に限る）、丸め方向を反転して通す案（利益を上方に丸めない規則（第6.5節）に反する）。
- **管理要求が1件も返らなかった建玉は、初期の利確を持たないまま継続する**【提案】。`fixed_rr_take_profit` の評価が `Skipped`（入力欠損）で終わる場合がこれに当たる。架空の利確水準を埋めず、`ProtectionState.take_profit` を `None` のままにする。評価が `Skipped` であったことは表2（`EVALUATIONS`）に残る。`Failed` の場合は第4.2節の失敗の規則に従って run を止める。
- 同じ建玉への更新と決済要求が同時なら決済を優先し、更新は理由を記録して破棄する【合意済み】上位 §4.7.6。**run 末尾の規則が衝突の規則より優先する**【確定】（v1.3）。末尾では決済要求も `RUN_END` で受付前拒否されるため（第10.1節の手順3）、更新を `SUPERSEDED_BY_EXIT` にすると「押しのけた決済」が存在しないまま記録が残る。末尾では衝突の有無にかかわらず `RUN_END` で適用しない。**破棄の理由は `SUPERSEDED_BY_EXIT`**【確定】（2026-09-22 の人間の決定。PR #19。同じ PR で上位設計書 §4.7.14 と D02 §8.1 の表を改訂した）。既存の語はどれも意味が合わない（`POSITION_CLOSED` は閉じた建玉への要求、`RUN_END` は末尾、`PROTECTION_INVALID` は宣言の誤りであり、ここでは建玉は開いていて宣言も正しい）。取引機会が終端する `SUPERSEDED` と混同しないよう、決済（Exit）に押しのけられたことを名前に入れる。適用を試みてから捨てるのではなく**適用そのものを行わない**（建玉は決済されるので、その更新は一度も有効にならない）。表12（`MANAGEMENT_APPLICATIONS`）に `applied=False` とこの理由で残す。**不採用**: `POSITION_CLOSED` を流用する案（まだ閉じていない建玉に「閉鎖済み」と記録され、集計で本来の閉鎖済み拒否と混ざる）、理由を持たせずに記録だけ残す案（`ManagementApplication` は適用しなかった要求に必ず理由を要求する）。
- 閉じた建玉への要求は `POSITION_CLOSED` で拒否し、run 全体は止めない【合意済み】同節。
- **損切り水準の更新（トレーリング、`UpdateStop(stop_loss)`）の適用意味論**【提案】（v1.5、2026-09-22。D05 §12.1 の依頼3。段階3 の範囲で、段階2 の挙動は変えない）。D04 §11.2 が `UPDATE_STOP` を段階3 の要求種別として確定し（v1.9）、D05 §4.10 が「不利な向きへは動かさない」判定を部品側に置いた。エンジン側は次のとおり扱う。

| 事項 | 規則 |
|---|---|
| 適用先 | `ProtectionState` の `stop_loss` を置き換え、`version` を1増やす。既存の利確（`take_profit`）は触らない |
| 参照価格 | **受付が使うものと同じ `ReferenceQuote`**（第6.4節 手順3）を使う。すなわち、その判断時点までに rank 0 で処理し終えた**最新の執行足**から作り、**買いの損切りの検査には bid、売りの損切りの検査には ask** を読む（第6.4節 手順4 の保護水準の検査と同じ向き）。評価系列（戦略が見た足）ではなく執行系列から取るのは、水準に触れたかどうかを判定するのが執行系列だからである（第7.3節）。参照価格を作れない判断時点（完了した執行足が1本も無い）では**更新を適用せず**、理由 `DATA_ERROR` で表12 に残す |
| 検査 | 価格刻みへ丸め（第6.5節）、買いなら `stop_loss < 参照価格の bid`、売りなら `参照価格の ask < stop_loss`。**丸めた結果が現在の水準より不利になる更新は適用しない**（部品が有利な向きだけを返すのに、丸めで不利側へ動くことがあるため）。適用しなかった要求は表12 に理由 `PROTECTION_INVALID` で残し、run は止めない（上の保護水準の検査と同じ扱い） |
| 丸めの方向 | **建玉にとって不利にならない側へ丸める**。買いの損切りは切り下げ、売りの損切りは切り上げる。損切りを有利側へ丸めると、実際には触れていない水準で決済したことになる（第6.5節の「利益を上方に丸めない」と同じ向きの規則） |
| 適用の処理点 | **その要求を返した `step` で分ける**【確定】（Q25 決定、2026-09-23、選択肢1。上の「適用フェーズ」の規則と同じ分け方であり、`UpdateStop` にも例外を置かない）。**第1回の `step`（rank 4〜9、足の確定で起動する `exit`）が返した更新は受付（rank 10）の直前**に適用し、フェーズは `ADMISSION`。**第2回の `step`（rank 12、約定通知で起動する `exit`）が返した更新は `POST_FILL_EVALUATION`** に適用する（rank 10 は既に過ぎているため）。段階3 のトレーリング（第1回）を受付より前に効かせるのがこの決定の狙いであり、約定通知で起動する `exit` の扱いは段階2 から変えていない |
| `effective_from` | **次の執行足**。初期の保護水準の例外（上表の「初期の保護水準」）は当たらない。終値で計算した更新をその足の過去の高値・安値へ適用しない【合意済み】上位 §4.7.7 |
| 同じ判断時点の決済要求との競合 | 決済を優先し、更新は `SUPERSEDED_BY_EXIT` で適用しない（上の規則をそのまま使う。新しい理由コードは作らない） |
| 同じ判断時点に更新が2件 | 起こらない。`exit` 役割は `OutputRef` 1件のみ【合意済み】D04 §11.3 で、さらに**その使用箇所の足の確定の系列が1つであること**をコンパイル時に検査する（D04 §12 #14、D05 §5.6 の検査 g）。区間の違う起動条件を2件宣言すると同じ建玉に2件の更新が出て、最終的な水準が処理順に依存するため、その宣言を通さない |

  **不採用**: 更新を約定した足から有効にする案（初期の損切りと同じ扱いにすると、終値で計算した水準をその足の過去の値動きへ適用することになり、上位 §4.7.7 に反する）、不利な向きの更新をエンジン側で有利側へ読み替える案（戦略の意図とエンジンの補正が判断履歴で区別できなくなる）。

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

### 8.5 通貨換算【提案】＋【合意済み】（Q7 決定、選択肢2。Q9 決定、選択肢1）

- 換算は**判断時点で利用可能な系列**から取り、不足時は拒否する【合意済み】全体計画 §5.4.4。将来の換算率を使わない。
- 換算率と観測時点は `ConversionRate`（D02 §4.5）で保持し、`RiskAssessment` と `CostEntry` に残す【合意済み】上位 §4.7.9 C。
- 適用時点は、予約額の計算（`ADMISSION`）、費用の口座通貨計上（約定の確定単位）、MTM 評価（`LEDGER_UPDATE` と末尾）の3か所【提案】。
- **段階2は換算が不要**である。ADR-0015 の縦断範囲は USDJPY・JPY 口座で、決済通貨（JPY）と口座通貨（JPY）が一致するため `ConversionRate` は率1の恒等換算になる。恒等換算でも経路を通すのは、段階3以降で経路を足すときに呼び出し側が変わらないようにするためである。
#### 8.5.1 換算経路と観測時点のずれ【合意済み】（Q7 決定、選択肢2。Q9 決定、選択肢1）＋【提案】

2026-09-21 の決定により、**基軸通貨（`ConversionPolicy.pivot_currency`、初版は USD）を経由する2ホップまでの換算を許す**。直接のペアだけを許す案（提示時の推奨）より扱えるペアが広がる代わりに、**2本の系列の観測時点のずれを結果に残す規則**が要る。その規則を次のとおり定める。段階2（USDJPY・JPY 口座）は決済通貨と口座通貨が一致するため換算は率1の恒等換算であり、**本項の規則によって段階2の結果は変わらない**。

| # | 規則 |
|---|---|
| 1 | **経路の選び方は決定論的に固定する**。(a) 決済通貨と口座通貨が同じなら**恒等換算**（`legs=()`、`rate=1`、`observed_at=判断時刻`、`skew=0`）、(b) 直接のペア（`Symbol(from+to)` またはその逆）が snapshot にあればそれを1本使う、(c) 無ければ `pivot_currency` を経由する2本を使う、(d) それも無ければ換算不可として拒否する。同じ通貨対に対して常に同じ経路を選ぶ（先に見つかった方を使う、といった探索順依存にしない）。**恒等換算を `legs` が空の経路で表す**のは、`ConversionLeg` が `series` / `bar_key` / `observed_at` を必須にしており、参照する市場系列が存在しない恒等換算では架空の観測を書くことになるためである |
| 2 | **各ホップは判断時点で利用可能な最新の確定足から取る**。系列・足・項目の選び方は参照価格と同じ規則（第6.4節の手順3、`ExecutionPolicy.reference_quote_source`）に従う。将来の足を使わない【合意済み】全体計画 §5.4.4 |
| 3 | **逆向きのペアは逆数を取り、`ConversionLeg.inverted=True` を立てる**。逆数化は丸めずに `Decimal` のまま保持し、丸めは最終的な `Money` への換算時に1回だけ行う（D02 §4.1 のカーネル精度で計算する） |
| 4 | **合成した換算率の観測時点は、各ホップの `observed_at` のうち最も古いもの**とする。換算率はいちばん古い脚と同じだけしか新しくないためであり、新しい方を採ると、古い脚の情報が「その時点で観測された」と読める記録になる |
| 5 | **ずれの大きさを `ConversionPath.skew`（各ホップの `observed_at` の最大差）として必ず記録する**。1ホップなら0。この値は根拠記録（表15）と `RiskAssessment` / `CostEntry` から辿れる |
| 6 | **ずれが `ConversionPolicy.max_observation_skew` を超えたら換算不可とし、その用途に応じて拒否する**。受付時（予約額の計算）なら `DATA_ERROR` で受付前拒否、費用の計上と MTM 評価なら run の失敗（第10.4節）。**上限の置き場所は換算ポリシーの項目 `max_observation_skew` とし、初版値は執行足1本分（15分）とする**【合意済み】（Q9 決定、選択肢1）。換算固有の設定を実行ポリシーやリスクポリシーに混ぜると、換算を変えたい実験が約定やリスクの版まで動かすことになる。初版値を執行足1本分にするのは、それより大きいずれを許すことが執行足1本分の値動きを見落とすことと同じだからである |
| 7 | **`ConversionRate`（D02 §4.5）は1本の率として持ち、経路は `ConversionPath` が持つ**。`ConversionRate(from_currency, to_currency, rate, observed_at, evidence)` の `rate` に合成後の率、`observed_at` に規則4の値、`evidence` に根拠記録への参照を入れる。D02 の型は改訂しない |
| 8 | **経路そのものは根拠記録に永続化する**。`EvidenceRecord.conversion_paths` に、その処理点で使ったすべての `ConversionPath`（各 leg の系列・足・率・観測時点・逆数化の有無を含む）を入れ、表15（`EVIDENCE`）として保存する。`ConversionRate` は合成後の1つの率しか持たないため、これを入れないと**規則4・5 が要求する情報が15表のどこにも残らず**、`RiskAssessment` と `CostEntry` から経路を再現できない |

**不採用**: 直接のペアだけを許す案（Q7 の選択肢1。実装は最小だが扱えるペアが狭く、段階3以降で JPY 以外の口座を試すたびに設計へ戻ることになる）、経路を実験設定で明示宣言させる案（Q7 の選択肢3。意図しない経路は避けられるが、段階2で使わない宣言型が増える）、3ホップ以上を許す案（ずれの合成が経路の本数だけ増え、上限の意味が経路ごとに変わる）、新しい方の観測時点を採る案（規則4の逆。記録が実態より新しく見える）。

段階2の実装では、(a) の恒等換算だけを通す経路を作り、(b)(c) の分岐と規則3〜6 は段階3で JPY 以外の口座を扱うときに使う。ただし `ConversionPath` と `ConversionLeg` の型、および規則4・5・8 の記録項目は段階2から置く（恒等換算でも `legs=()`・`skew=0` の経路として表15 に記録し、段階3で項目が増えないようにする）。

## 9. 記録（`trace`）

### 9.1 保存形式と書き出し【提案】

- 表形式データは Parquet、manifest は JSON【合意済み】ADR-0027。保存先は `runs/<run_id>/`。
- 書き出しは `TraceSink`（`backtest.application.ports`）経由で、実装は `evaluation.adapters` または `app` が持つ【合意済み】D01 §4。`backtest` は polars も Parquet も直接触らない。
- **行は平坦化して保存する**【提案】。各表の列は本書・D05 の型のフィールドに1対1で対応させ、入れ子の値は次の規則で開く。`ProcessingPoint` は `*_time` / `*_phase` / `*_sequence` の3列、`Reason` は `*_code` と `*_detail`（正規化エンコード文字列、D02 §9.3）の2列、`Money` は `*_amount`（文字列）と `*_currency`、`Decimal` と `Price` と `Quantity` は文字列、`UtcTime` は D02 §3.1 の文字列、ID 型は `__str__`。Decimal を浮動小数として保存すると再現性が壊れるため文字列にする【合意済み】ADR-0012。期間（`timedelta`）は**秒数の十進文字列**（D02 §9.3 v1.7 の注記。段階2で現れるのは換算経路のずれだけ）。
- **十進数の単一の列は、人が読める固定小数で書く**【確定】（2026-09-22 の人間の決定。PR #19）。ダイジェスト用の正規化エンコード（D02 §9.3）は `149.500` を `1495e-1`、`1000000` を `1e6` と書くため、判断履歴を人が読み下せない。そこで**数値の列だけ**を次の4つの規則で書く。

  1. **末尾ゼロを落とす**（スケールの決め方）。`Decimal("149.500")` も `Decimal("149.5")` も `149.5` になる。落とさないと、**同じ値が書き手の持っていた桁数によって別の文字列になり**、再実行の判断履歴を文字列のまま比べられない（第4.4節の「許容誤差は完全一致」）。
  2. **指数表記を使わない**。`Decimal("1E+6")` は `1000000`、`Decimal("5E-4")` は `0.0005`。小数点の前が空にならないよう `0` を補う。
  3. **負号は値が負のときだけ**先頭に付ける。ゼロは符号も桁も捨てて `0`（`-0` も `0.00` も `0`）。
  4. 固定小数で書くと長さが上限（1000 文字）を超える値は**書き出しを失敗させる**。固定小数で書けない大きさは人が読む形にならず、黙って別表記へ落とすと規則1 が崩れる（既定は失敗、ADR-0006）。段階2 の値（価格・数量・金額・率）はいずれも 30 文字に満たない。

  紙上トレース [T01](../traces/T01_paper_trace.md) が価格を `149.500` のように書くのは**値としての等価**（刻み4桁での読み合わせ）を示すためであり、保存される文字列は規則1 により `149.5` である【確定】（2026-09-22 の人間の決定。PR #19。末尾ゼロを落とす規則は変えない）。T01 の表記と判断履歴の文字列を直接比べるときは、`Decimal` に読み戻してから比べる。

  読み戻しは `Decimal(文字列)` で厳密に往復する（D07 §4.3 の `ColumnValueKind` による解釈がこれに当たる）。**この表記を使うのは数値の単一の列だけ**で、理由の型付き詳細（`*_detail`）と可変長の入れ子の列は、復号せずに文字列のまま比べる列なので**正規化エンコードのまま**にする。**不採用**: すべての列を正規化エンコードにする案（同じ値が常に同じ文字列になる利点はあるが、価格表を人が読めず、紙上トレースとの突き合わせが手作業でできない）、列ごとに固定のスケールを決める案（`149.500` のように桁数を揃えられるが、率や比のように刻みを持たない列のスケールを一般に定義できず、規則が列ごとの例外表になる）。
- **可変長の入れ子（`market_refs` / `conversion_paths` / `checks` / `hierarchy_checks` など、レコードの `tuple`）は、要素ごとに D02 §9.3 の正規化エンコード文字列にし、その文字列の `list` 列として保存する**【提案】。要素数が行ごとに変わるため固定の列へ開けず、かといって落とすと診断の実値が消える。ID の `tuple`（`output_ids` など）は `__str__` の `list` 列とする。正規化エンコードを使うのは、同じ内容から常に同じ文字列が出て再現性の比較ができるためである。**不採用**: 子表へ分ける案（表が15を超え、D07 が開く表が増える）、JSON 文字列にする案（`Decimal` の表現が正規化エンコードと二重になる）。
- **入れ子・複合・区分タグ付き union の列名の規則**【合意済み】（Q14 決定、選択肢1）。上の3例（`ProcessingPoint` / `Reason` / `Money`）は、いずれも次の規則1 の適用である。D07 §4.2 が読む列名は、この3件でちょうど確定する。

  1. **行そのものの型のフィールドには接頭辞を付けず、入れ子は `<フィールド名>_` を接頭辞として再帰的に開く**。`Position.opened_at`（`ProcessingPoint`）は `opened_at_time` / `opened_at_phase` / `opened_at_sequence`、`Position.realized`（`Money`）は `realized_amount` / `realized_currency`、`AttemptRejected.reason`（`Reason`）は `reason_code` / `reason_detail` になる。2段の入れ子も同じで、`AcceptedOrder.terms.reference_quote.price` は `terms_reference_quote_price` である。
  2. **区分タグ付き union は、規則1 のもとで `kind` フィールドがそのまま列になる**（D01 §8・ADR-0011 が union を `kind` フィールドを持つ dataclass の `Union` と定めているため、特別扱いを足さない）。列は**全変種のフィールドの和集合**とし、その行の変種に無い列は `None` にする。`EvaluationRecord.outcome` は `outcome_kind` ＋ `outcome_diagnoses` / `outcome_reason_code` など、`OrderRequest.payload` は `payload_kind` ＋ `payload_opportunity_id` / `payload_position_id` になる。**行そのものが union である表**（表5 `ATTEMPT_DECISIONS` の `AttemptDecision`）では接頭辞が付かないため、区分の列は `kind`、変種のフィールドは `order_id` / `reason_code` のように接頭辞なしで並ぶ。
  3. **複合表**（1行が複数の型からなる表10・11・12）は、**第9.2節の「正本の型」欄の先頭に書いた型を主**として接頭辞なしで開き、**従の型は `<型名のスネークケース>_` を接頭辞**にする。表10 は `RiskReservation` が主で `ReservationState.status` は `reservation_state_status`、表11 は `Position` が主で `PositionRiskAllocation.amount`（`Money`）は `position_risk_allocation_amount_amount` / `position_risk_allocation_amount_currency`、`RiskMeasurement.measured`（`Money`）は `risk_measurement_measured_amount` / `risk_measurement_measured_currency`、表12 は `ManagementRequest` が主で適用結果（第8.3節）の項目は `application_` を接頭辞にする。主と従が同名のフィールドを持つ場合も接頭辞で区別される（表11 の `position_id` と `position_risk_allocation_position_id`）。**接頭辞と子のフィールド名が同じ語になっても畳まない**（`amount` という名前の `Money` は `<接頭辞>_amount_amount` / `<接頭辞>_amount_currency` になる）。畳む例外を置くと、`Money` の2列のうち一方だけが短くなって対にならず、列名から型を復元できなくなる。

  **不採用**: union を正規化エンコード1列に畳む案（列は増えないが、区分別に集計するたびに復号が要り、D02 §9.3 が復号を定義していない）、複合表を型ごとの子表に分ける案（第9.2節が既に退けたとおり、表が15を超えて D07 が開く表が増える）。
- 全行が `run_id` を持つ【合意済み】上位 §4.7.15。

### 9.2 trace の行の種類【提案】

段階2で書き出す表は次の15件。**段階3 で足す表は本節の末尾にまとめる**（v1.5）。

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
| 9 | `FILLS` | `FillRecord` | `fill_id` | `order_id` / `position_id`。**費用は区分別の金額列としても保存する**（下記、Q12 決定） |
| 10 | `RESERVATIONS` | `RiskReservation` ＋ `ReservationState` | `reservation_id` | `order_id` / `allocation_id` |
| 11 | `POSITIONS` | `Position` ＋ `PositionRiskAllocation` ＋ `RiskMeasurement` | `position_id` | `entry_fill_id` / `close_fill_id` |
| 12 | `MANAGEMENT_APPLICATIONS` | `ManagementRequest`（D05 §3）＋ 適用結果 | `(position_id, at)` | `position_id` / 元の `output_id` |
| 13 | `INTRABAR_RESOLUTIONS` | `IntrabarResolution` | `fill_id` | `position_id` / `parent_bar_key` |
| 14 | `LEDGER_SNAPSHOTS` | `LedgerSnapshot` | `at` | `open_position_ids` |
| 15 | `EVIDENCE` | `EvidenceRecord` | `evidence_id` | `output_ids` / `evaluation_ids` / `attempt_id` / `position_id` / `market_refs` / `conversion_paths` |

**約定1件ごとの費用を、区分別の金額列としても保存する**【合意済み】（Q12 決定、選択肢1）。`FillRecord.costs`（`CostEntry` の `tuple`）は第9.1節の可変長の規則により正規化エンコード文字列の `list` 列 `costs` になるが、D02 §9.3 は符号化だけを定義して復号を定義していないため、**その列からは区分別の金額を復元できない**（D07 §4.2）。そこで `CostKind` の3区分それぞれについて、**口座通貨での計上額**を `Money` の平坦化規則で2列ずつ足す。

| 列 | 内容 |
|---|---|
| `cost_commission_amount` / `cost_commission_currency` | 手数料（`COMMISSION`）。balance に反映済み |
| `cost_slippage_in_price_amount` / `cost_slippage_in_price_currency` | 執行モデルの滑り（`SLIPPAGE_IN_PRICE`）。価格に反映済みの参考値で balance から控除しない（第7.6節） |
| `cost_spread_in_price_amount` / `cost_spread_in_price_currency` | 提示価格の幅（`SPREAD_IN_PRICE`）。同じく参考値 |

`costs` 列は**そのまま残す**。原通貨額と換算根拠（第7.6節）は区分別の列に入らず、正規化エンコード列が引き続きその正本である。該当する区分の `CostEntry` が無い約定では、その区分の2列を**どちらも `None`** にする（金額 0 の `CostEntry` が記録された場合と区別するため）。読む側は D07 v0.2（段階4）で、取引単位の費用と入場費用を含む取引損益に使う（D07 §12）。段階2 の D07 は run 単位の集計（第10.3節の `cost_breakdown`）だけを使うため、この列が増えても段階2 の指標は変わらない。**不採用**: 正規化エンコード列の復号規則を D02 §9.3 に足す案（符号化の正本に復号の責務が増え、区分別に読むたびに文字列の解析が要る）、費用を子表に分ける案（表が15を超える。第9.1節と同じ理由）。

**予約の表（表10）と建玉の表（表11）は、run の終わりに最終状態を1行ずつ書く**【確定】（2026-09-22 の人間の決定。PR #19）。主キーは `reservation_id` / `position_id` であり、状態が変わるたびに行を足すと同じ主キーの行が複数でき、主キーとして読めなくなる。状態の移り変わりそのものは注文イベントの表（表8）と約定の表（表9）が処理点付きで持っており、そこから復元できる。**不採用**: 状態が変わるたびに行を足す案（主キーが一意でなくなり、D07 §10.2 の整合検査が書けない）、状態遷移用の子表を足す案（表が15を超え、D07 が開く表が増える。第9.1節と同じ理由）。

**ID 連鎖で辿れること**（機会 → 試行 → 注文 → 約定 → 建玉 → 管理要求、および予約）を、表3・4・7・9・11・12・10 の外部キーで満たす。**受付前拒否でも連鎖が切れない**のは、`OrderRequest` を表4として必ず保存するためである（上位 §4.7.15 A が「受付前拒否もこの記録に紐付く」と定めている）。`AttemptRejected` は `AcceptedOrder` を作らないため、発端の取引機会は表4の `opportunity_id` からだけ辿れる【合意済み】全体計画 §5.4.5。**根拠記録は表15 `EVIDENCE` に置く**【提案】。`EvidenceRef`（D02 §9.2）は `evidence_id: EvidenceId` だけを持ち、表1・2・4 の主キーは `OutputId` / `EvaluationId` / `AttemptId` であるため、`EvidenceRef` から直接それらの行を引くことはできない。そこで上位 §4.7.15 が定める根拠記録の内容（入力の出力 ID、市場データの snapshot・系列・区間・項目、読取時点、口座 snapshot、使用した設定の版）を `EvidenceRecord` として型付きで保持し、**そこから表1・2・4・12・14 へ自然キーで辿る**。`EvidenceId` の採番は `backtest.trace`【合意済み】D02 §7.1。`RiskAssessment.assessment_id` も同じ `EvidenceId` の採番列から取り、審査記録それ自体が1件の根拠記録であることを表す。**不採用**: 表1・2 の行に `EvidenceId` を足す案（`OutputRecord` と `EvaluationRecord` は D05 が正本であり、記録の都合で戦略側の型にエンジン側の識別子を足すことになる）、自由記述のログにする案（型付きで辿れない記録が増え、上位 §4.7.15 の「説明文だけのログではない」に反する）。

**段階3 で足す表（v1.5、2026-09-22。D05 §12.1 の依頼4）**【提案】。D05 v2.0 が待機・遡り・確認の意味論を確定したので、その記録の置き場所を決める。段階2 の15表は**1つも変えない**。

| # | `TraceTable` | 正本の型 | 主キー | 辿れる先 |
|---|---|---|---|---|
| 16 | `WAIT_EVENTS` | `WaitEvent`（D05 §3） | `(request_id, at)` | `request_id` → 表2 の評価記録 |
| 17 | `INPUT_SUBSTITUTIONS` | `SubstitutedInput`（D05 §3）＋ その要求の `evaluation_id` | `(evaluation_id, input_name, source_index)` | `evaluation_id` → 表2、`used_output_id` → 表1 |
| 18 | `CONFIRMATION_ATTEMPTS` | `ConfirmationAttempt`（D05 §3） | `(opportunity_id, bar_key)` | `opportunity_id` → 表3、`request_id` → 表2 |
| 19 | `VALIDITY_RECHECKS` | `ValidityRecheck`（D05 §3） | `(opportunity_id, at)` | `opportunity_id` → 表3、`output_id` → 表1 |

- **待機の出来事（表16）を評価記録と別の表にする**【提案】。1つの評価要求が待機・到着・再開・期限到達・追い越しで複数の出来事を持つため、表2 の1行に畳めない。主キーを `(request_id, at)` にするのは、同じ要求の出来事が処理点で一意に並ぶからである（処理点の番号はエンジンが振り直す。第4.4節）。
- **遡った入力（表17）の主キーに接続元の位置を含める**【提案】。1つの入力名に複数の接続元を書けるため（D04 §4.1 の `arity`）、同じ評価で同じ入力名の2つの系列を遡ると行が2件出る。入力名までを主キーにすると片方が上書きされ、実際に代用した観測が判断履歴から消える。`SubstitutedInput` が持つ `source_index`（D05 §3）を鍵に含めて一意にする。
- **遡った入力（表17）を独立させる**【提案】。表2 の `EvaluationRecord.substitutions` は可変長の入れ子であり、第9.1節の規則なら正規化エンコード文字列の `list` 列になる。遡りは「どの足を代わりに読んだか」を区分別に集計する対象（D07）なので、復号せずに読める形が要る。表9 の費用を区分別の列へ開いたのと同じ理由である。
- **確認試行（表18）を独立させる**【提案】。`OpportunityLifecycle.attempts` は機会1件につき期限までの本数だけ並ぶため、表3（取引機会の遷移）の行には収まらない。主キーを `(opportunity_id, bar_key)` にできるのは、**同じ機会・同じ確認足について作る評価要求が1件だけで、試行も1件だけだから**である【合意済み】D05 §7.7。入力が足りずに待機へ入った試行は `WAITING` で始まり、再開して決着したときに**同じ1件の結末が置き換わる**（D05 §7.7）。したがって待機をはさんでも行は増えず、表18 には**その確認足についての最後の結末**が1行だけ残る。待機の経過は表16（待機の出来事）と表2（評価記録）が処理点付きで持つ。**エンジンは `RuntimeStepResult.confirmation_attempts` を受け取って表18 を書く**【確定】（Q25 と同じ日の Q26 決定、2026-09-23、選択肢1。D05 §3・§6.2 の手順10）。戦略ランタイムが返すのは**その `step` で作った・書き換えた試行だけ**なので、エンジンは主キー `(opportunity_id, bar_key)` で**既存の行を置き換える**（同じ鍵の行を足さない）。run 末尾にまとめて受け取る形にしないのは、run が途中で失敗したときに確認の経過が1行も残らないためである。
- **損切り水準の更新は表12 をそのまま使う**【提案】。`UpdateStop` は `ManagementAction` の区分であり、表12 は既に区分タグ付き union を全変種のフィールドの和集合として開く規則を持つ（第9.1節の規則2）。新しい表を足すと、同じ建玉への保護水準の要求が2つの表に分かれる。
- **取引機会の有効性の再検査（表19）を独立させる**【提案】。再検査は**評価の外側**（確認評価の P4 と、注文意図を作る直前の P5）で走るため、評価記録（表2）の行にならない。遷移記録（表3）にも収まらない。遷移記録は条件が**不成立になった**場合にしか作られず、「読めなかったので今回は見送った」「読めなかったので run を失敗させた」を表せないからである。この表が無いと、D04 §6.3 が段階2 で禁じていた「発注要求まで有効であり続けることを求める束縛に `Error` を書いた宣言」を解除したときに、**判断履歴のどこにも残らないまま run が止まる**経路が戻ってくる（D05 §7.3）。主キーを `(opportunity_id, at)` にできるのは、同じ機会について同じ処理点で同じ束縛を2回読まないからである。
- **上流の出力を履歴窓で読んだことは新しい表に残さない**【合意済み】D05 §6.12（2026-09-22 の人間の決定 Q18、選択肢2）。読んだ上流の出力はすべて表1 に `output_id` 付きで残っており、保持と打ち切りの規則が決定論であるため、評価記録の判断時刻と保持本数の計画から読んだ窓を復元できる。**段階3 の表は16〜19 の4件で、合計19件になる**。

**表の件数の上限について**【提案】。第9.1節と第9.2節は「表が15を超える」ことを子表への分割の不採用の理由にしていたが、それは**段階2 の同じ内容を親子に割る**ことへの理由であり、段階3 で新しく生まれる記録に表を足すことを禁じるものではない。表16〜19 はいずれも段階2 に存在しない記録である。

### 9.3 run manifest【提案】

JSON。項目は次のとおり【合意済み】全体計画 §5.4.5 を具体化する。

| 群 | 項目 |
|---|---|
| 識別 | `run_id`、`config_digest`、`code_digest`、`lock_digest`、`env_digest`、git commit と dirty 状態（識別子には含めない、ADR-0006） |
| 入力 | `snapshot_ref`、`strategy_ref`、`compiled_ref`、`run_interval`、`execution_series`、`seed`、**`account`（`AccountSpec` の3項目。特に `initial_balance`）** |
| ポリシー | `risk_policy_ref`、`execution_policy_ref`（`entry_delay_bars`・Δ・有効時間・`resolution_hierarchy`・`reference_quote_source` を含む）、`cost_model_ref`（`swap_modeled=False`）、`conversion_policy_ref`（`pivot_currency`・`max_observation_skew`）、`delay_scenario_ref`、`symbol_spec_ref`、`calendar_ref`、`timeframe_def_refs` |
| 実行の構造 | `BACKTEST_PHASES` のフェーズ集合（D02 §3.3 の要求）、`IdAllocator.snapshot()`（D02 §7.3） |
| 足内競合 | `resolution_hierarchy`、`UNRESOLVED_SL_PRIORITY` の件数と割合（ADR-0030） |
| 能力検査 | **`DataCapabilityReport` の全体**（`compiled_match`、`IntegrityReport`、`HierarchyCheckResult` の全件、`runnable`、`reason`）。要約に畳まない |
| 診断 | **`warnings`（文章の列）**。理由コードの語彙に当てはまらない診断をここに残す |
| 状態 | `RunStatus`、失敗時の `Reason` |

**「診断として残す」の残す先は run manifest の警告群とする**【確定】（2026-09-22 の人間の決定。PR #19）。第7.5節の手順6 は、始値の gap による損切りと約定ずれ超過が重なったとき「約定ずれ超過は診断として残す」と定めるが、残す先を特定していなかった。理由コード（D02 §8.1）はどれも意味が合わず、判断履歴の表に列を足すと、その列を持つ表がこの1件のためだけに広がる。警告群は**実行ごとに0件以上の文章**を持つ項目で、run 全体の診断を読む側が1か所で拾える。なお段階2 の算術ではこの重なりは成立しない（買い建玉では、gap 損切りは始値が損切り水準以下であることを要し、約定ずれ超過は始値が参照価格＋許容幅より上であることを要する。損切り水準は受付時の妥当性検査で参照価格より下と決まっているため両立しない）。設計が定める分岐なので実装は残す。**不採用**: 新しい理由コードを足す案（理由コードは状態遷移の理由の語彙であり、どの遷移にも紐付かない診断を入れると「状態と理由は別フィールド」の体系が崩れる。上位 §4.7.14）、判断履歴の表に列を足す案（1件の診断のために表の列が増え、D07 の入力契約が広がる）。

**実行の出どころ（コード・依存 lock・環境のダイジェストと git の状態）は、実行ユースケースの必須の入力とする**【確定】（2026-09-22 の人間の決定。PR #19）。これらは設定ではなく実行環境の事実であり、`RunId` がこの4つのダイジェストから決まる以上（ADR-0006）、空のままでは識別子が成り立たない。実際に算出して渡すのは `app`（段階2 の3本目の CLI）である。

`ConfigDigest` の対象は「識別」を除く上表の**入力とポリシーの群**とする【提案】（D02 §9.2 が「項目は D06」と委ねた範囲）。口座仕様（`AccountSpec`）を入力群に含めるのは、初期残高だけを変えた実行は数量・損益・資産推移がすべて変わるのに、含めないと同じ `ConfigDigest` と `RunId` になり、別の結果が同じ `runs/<run_id>/` を指すためである。`RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`【合意済み】ADR-0006。

### 9.4 `BacktestResult`【提案】

評価基盤へ渡す正規化 DTO。評価側はこれを改変しない【合意済み】全体計画 §5.4.5。

| 項目 | 内容 |
|---|---|
| `run_id` / `manifest_ref` | run manifest への参照 |
| `status: RunStatus` | 正常完走か失敗か |
| `trace_tables: Mapping[TraceTable, str]` | **第9.2節の表すべての Parquet パス**（段階2 は15表、段階3 は19表。v1.5）。D07 は必要な表をここから開く。段階3 で足した4表も同じ `TraceTable` の列挙値で公開し、読む側が段階で場合分けしなくて済むようにする |
| `summaries: FinalSummaries \| None` | 末尾3集計（第10.3節）。**`status` が `COMPLETED` のときだけ非 `None`** |
| `swap_modeled: bool` | 常に `False`（ADR-0029）。D07 がユーザーへの明記に使う |
| `unresolved_intrabar_count: int` | `UNRESOLVED_SL_PRIORITY` の件数（ADR-0030） |
| `capability_report: DataCapabilityReport` | 能力検査の全結果。`status` が `FAILED_CAPABILITY` の run では trace 表が空になりうるため、結果 DTO からも直接読めるようにする |
| `trade_count` / `opportunity_count` | 完了取引数と生成された取引機会の総数（`status` の検証と手計算の照合に使う最小の件数） |

**資産推移（balance と equity の系列）を結果 DTO に持たせない**【提案】（v1.1）。`LEDGER_SNAPSHOTS`（表14）が各判断時点の `balance` と `equity` を全件持っており、そこから導いた系列を DTO にも置くと、同じ推移が2つの経路で読めて正本がどちらか決める規則がもう1つ要る。要素の型も定義できていなかった。**不採用**: 要素の型を定義して2項目を残す案（読む側が表14 と DTO のどちらを使ってもよくなり、最大ドローダウンの算出元が実装ごとに分かれる）。

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
| 4 | この評価から出た注文意図を**受付前に `RUN_END` で拒否**する。注文を作ってから取り消す方式にしない | `ADMISSION`。rank 11 を行わないため rank 12 の評価は通知の配送だけで、rank 13 で出た決済要求も同じく `RUN_END` で拒否する |
| 5 | 残った受付済み PENDING を CANCELED（理由 `RUN_END`）にし、未約定予約を解放する。run_end から始まる足の始値処理（rank 11）は行わない | `RUN_END`（rank 14） |
| 6 | 残存する取引機会と、**残った待機中の評価要求**を `is_run_end=True` の `step` で終端する（第10.2節） | `RUN_END`（rank 14） |
| 7 | 残存建玉は未決済のまま MTM 評価して最終 snapshot を保存する。建玉割当も解放しない | `RUN_END`（rank 14） |

**run 末尾に残った待機要求も同じ `step` で決着させる**【提案】（v1.5、2026-09-22。D05 §12.1 の依頼5）。`is_run_end=True` の `step` は起動判定も評価も行わない（D05 §6.1）ので、待機中の評価要求はそのままでは何の記録も残さずに run が終わる。そこで、残存する取引機会を終端するのと同じ `step` の中で決着させる。**記録の形は D05 §6.1 が確定する**（v2.0）。本書が要求するのは次の3点である。

1. 決着した評価記録を `RuntimeStepResult.evaluations` で返すこと。したがって**末尾のバッチの戻り値は `evaluations` が空ではない**（第10.2節の表）。
2. その結末が、まだ足りていなかった入力の診断を持つ見送り（`Skipped(diagnoses)`）であること。期限には到達していないので、期限切れ（`on_deadline` に従った `Error`）としては扱わない。データ誤りとして集計されてしまう。
3. 「run が終わったから閉じた」ことが**待機の出来事**（`WaitEvent`）の側に残ること。D05 §6.1 がそのための値（`RUN_END_CLOSED`）を足した。

決着の順序は、取引機会の終端（`opportunity_id.seq` の昇順）の後に `request_id` の昇順とする。順序を決めないと、同じ判断時点の通し番号が run ごとに変わり、「同一入力の再実行で trace が一致」（全体計画 §8.2）を満たせない。**不採用**: 待機要求を記録せずに捨てる案（何を待ったまま run が終わったのかが判断履歴に残らない）、`on_deadline` に従って `Error` で決着させる案（期限には到達しておらず、run が終わっただけである）。

手順5〜7 は rank 14 の中でこの順に行う【提案】。注文の取消を機会の終端より先に置くのは、機会の終端理由が注文の状態に依存しないためであり（D05 §7.2 の遷移9 は「run 末尾に残った」だけを発火条件にする）、順序を逆にすると、終端済みの機会に対応する注文が後から取り消される記録になる。最終 snapshot を最後に置くのは、取消と終端の結果を含んだ台帳を保存するためである。

- 手順4の拒否も `AdmissionNotice(accepted=False, reason=RUN_END)` として `POST_FILL_EVALUATION` で配送し、取引機会を `ORDER_ATTEMPT_REJECTED` で終端させる【提案】。末尾でだけ通知経路を変えない。
- 手順3で生成された管理要求は、**種類で分ける**【提案】。

| 管理要求 | 末尾での扱い |
|---|---|
| 保護水準の更新（`SetTakeProfit`、段階3の `UPDATE_STOP`） | 記録するが**適用しない**。適用しない理由は `RUN_END`【合意済み】上位 §4.7.13 D |
| 全数量決済（`ClosePosition`） | `CloseRequest` へ組み立て、`AttemptRejected(RUN_END)` として**受付前拒否を記録する**。注文と予約は作らない |

全数量決済だけ要求まで作るのは、上位 §4.7.13 D の手順4 が「新規エントリーだけでなく戦略の決済注文も末尾で約定させない」「注文を生成してから取り消す方式にはしない」と定めており、**受付前拒否として試行の連鎖に残る**のが決済注文の正しい終わり方だからである。適用しない記録に畳むと、表4・表5 に試行が現れず、機会 → 試行の連鎖から末尾の決済意図が消える。
- 終了だから未公開情報を解禁することはしない【合意済み】同節。

### 10.2 残存した取引機会の終端【合意済み】（Q2 決定、選択肢1）

D05 §7.2 の遷移9 は「run 末尾に残った取引機会を `RUN_END` で終端する」と定め、フェーズを「末尾処理」としている【合意済み】。取引機会の状態を変えられるのはランタイムだけであり（D05 §7）、その入口は `step(batch)` 1つだけである。2026-09-21 の決定により、**公開バッチに「これが末尾である」ことを示す項目 `is_run_end: bool` を足し、`RUN_END` フェーズ（rank 14）の `step` 呼び出しで立てる**。D05 §6.1 は同じ PR で改訂済み（v1.1）。

| 項目 | 値 |
|---|---|
| `batch_id` | `IdAllocator.next(EventId)` で採番した新しい値（第1回・第2回とは別） |
| `decision_time` | run_end の判断時刻（`RunConfig.run_interval.end`） |
| `phases` | `BACKTEST_PHASES` |
| `available_bars` / `scheduled_closes` / `runtime_events` / `admissions` | すべて空（D05 §6.1 の構築時不変条件） |
| 戻り値 | `transitions`（残存機会の終端）に加えて、**残っていた待機要求の決着**として `evaluations` と `wait_events` が非空になりうる（D05 §6.1 v2.0）。`outputs` / `proposals` / `management_requests` は空のままである |
| `is_run_end` | `True` |

- エンジンは戻り値の `transitions`（残存機会の `RUN_END` 終端）を表3 へ書き、`outputs` / `evaluations` / `proposals` / `management_requests` が空であることを検査する。空でなければ `KernelValueError`（末尾で新しい判断が生まれない規則、上位 §4.7.13 D の「終了だから未公開情報を解禁することはしない」の実施箇所）。
- この呼び出しは1 run に1回だけ行う。失敗した run（第10.4節）では行わない。取引機会は最後に観測された状態のまま最終 snapshot に残り、架空の終端理由を付けない。
- **不採用**: 戦略ランタイムに末尾専用の操作を足す案（ポートの操作が2つになり、呼び出し順の規則がもう1本要る）、エンジンが残存機会を直接終端させる案（取引機会の状態の所有者がランタイムとエンジンに割れる）。

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
- **予定した候補の足が実データに無いまま過ぎた場合も実行失敗とする**【確定】（v1.3。第5.3節で候補をスケジュールから決めると定めたことの帰結）。公開フィードは存在しない足の始値を知らせないため、始値の処理（rank 11）そのものが起きず、その中の「始値が無い」検査には到達しない。判断時点ごとに、**予定の始値がすでに過ぎているのに `PENDING` のまま残っている注文**があれば `DATA_ERROR` で止める。放置すると、注文は期限切れか末尾の取消で終わり、**データの欠損が「取引が成立しなかっただけ」として静かに通る**。
- **この検査は判断時点の先頭、期限切れ（rank 2）より前に行う**【確定】（v1.4）。期限切れや末尾の取消より後に置くと、検査の対象になるはずの注文が先に畳まれ、欠損が普通の `EXPIRED` / `RUN_END` として記録に残る。候補の時刻と期限のあいだに公開イベントが1件も無い場合（欠けた足がその区間の唯一のイベント源である場合）に実際に起きる。**run_end の判断時点でも行う**。ただし末尾では始値を実行しないので（第10.1節）、欠損として扱うのは候補が **run_end より前**の注文だけであり、候補が run_end ちょうどの注文は末尾の取消（`RUN_END`）で終わる。末尾で検査ごと飛ばすと、最後の公開イベントと run_end のあいだで欠けた足が一度も検査されない。
- **取消で予約が解放された状態を、最後の台帳 snapshot に残す**【確定】（v1.3）。残さないと、予約の表は `RELEASED` なのに最後の snapshot はその額を消費済みとして数えたままになり、失敗した run の記録が口座の状態について食い違う。
- `status` は `FAILED_DATA_ERROR`。診断は保存するが、正常完走の採用評価に混ぜない【合意済み】同節。

### 10.5 実行前のデータ能力検査【提案】

run 開始前に必須で行い、`DataCapabilityReport` を残す【合意済み】上位 §4.7.13 C・ADR-0030。

1. **`compiled.compiled_ref == config.compiled_ref` を検査する**【提案】。`RunBacktest.run` は解決済み戦略とその参照を別々に受け取るため、照合しないと、実行 ID と manifest は設定側の参照を指しながら実際の評価と `ExitPlanRef` は引数側の戦略を使い、別の戦略の結果が同じ `runs/<run_id>/` に保存される。不一致は `FAILED_CAPABILITY` とする。同じ理由で `compiled.strategy_ref` と `config` が指す戦略参照、`compiled.symbol` と `config.execution_series.symbol` も照合する。
2. 要求した期間・系列・ウォームアップ・必要な価格項目をカレンダーと照合する（D03 §3.9 の `IntegrityReport`）。**執行に使う系列（執行系列と解像度階層の各段）の「カレンダー上存在すべき足の欠落」は、重大度が警告でも実行不可とする**【確定】（v1.3）。完全性検査はこの欠落を警告に分類する（受入れの段では人間が休場かデータ欠損かを分けるため、D03 §3.9）が、執行モデルはそれらの系列の足を1本ずつ読んで保護水準の到達を決めるので、欠落したまま run を続けられない。重大度だけを見ていると、欠落を知りながら実行可能と判定し、**建玉が開いたまま欠落区間の高値・安値が判定されず、古い評価価格のまま run が完走する**。あわせて、run 区間に予定の執行足がある場合は**その最初の1本が実データにあること**も確かめる（系列が空のときは比べる相手が無く、完全性検査に欠落が1件も載らないため）。run 区間全体が休場で予定が1本も無い場合は、実行するものが無いだけなので実行可能とする（第5.3節の末尾の取消に委ねる）。
2a. **ポリシーが銘柄別の値を持つ項目について、対象銘柄の値があることを照合する**【提案】。段階2で該当するのは `ExecutionPolicy.adverse_fill_limits[execution_series.symbol]`（第7.1.1節）と `SymbolSpec`（価格刻み・数量刻み・最小数量）である。不足は `FAILED_CAPABILITY`。
3. 執行系列が run 区間を覆うことを確認する。
4. 解像度階層の適合検査（第7.4節の検査1〜5）を行う。
5. 既知の欠損・無効値・末尾不足があれば**開始前に失敗**させる（`status = FAILED_CAPABILITY`）。検査結果は戦略へ渡さず、欠損付近だけを取引対象から外すこともしない。
6. `DataCapabilityReport` を**全体のまま** run manifest と `BacktestResult.capability_report` に保存する（第9.3節・第9.4節）。合格・不合格のどちらでも保存し、不合格の個別結果を落とさない。

公開遅延シナリオは元データの欠損とは区別する【合意済み】同節。

## 11. 段階2の最小範囲（検証戦略 A）と T01

D05 §9 の使用箇所に対して、エンジン側で起きることを時刻順に並べる。執行足は15分【合意済み】ADR-0015。

| 時点 | フェーズ | 起きること |
|---|---|---|
| T（1時間足の確定） | `EXECUTION_BAR_COMPLETE` 〜 `LEDGER_UPDATE` | 直前の15分足について、開いている建玉の保護水準の到達判定。段階2の1建玉目より前は対象なし |
| T | `ORDER_EXPIRY` | `expires_at <= T` の PENDING を EXPIRED。段階2では発生しない（理由は第5.1節） |
| T | `PUBLICATION` 〜 `P5_ORDER_INTENT` | 第1回の `step`。取引機会1件の生成（D05 の遷移1）と `EntryProposal` 1件（遷移4） |
| T | `ADMISSION` | `OrderRequest` を1件組み立て、参照価格を固定し、損切りを丸め、数量を決め、予約を確保して受付。`AdmissionNotice(accepted=True)` を作る |
| T | `EXECUTION_OPEN` | `[T, T+15m)` の始値で約定。建玉を作り初期の損切りを有効化（`effective_from` はこの足） |
| T | `POST_FILL_EVALUATION` | 第2回の `step`。受付の通知で機会が `FULFILLED_BY_ORDER_ACCEPTANCE` で終端（遷移5）。`POSITION_OPENED` で `fixed_rr_take_profit` が評価され `SetTakeProfit` を1件返す。丸めて建玉へ適用（初期の利確なので `effective_from` は約定した足、第8.3節） |
| T+15m | `EXECUTION_BAR_COMPLETE` | `[T, T+15m)` の足について到達判定。初期の損切りも初期の利確も約定した足から有効なので、どちらも対象。両方に触れたら第7.4節へ |
| 到達日 | `EXECUTION_BAR_COMPLETE` | 片側だけなら `SINGLE_HIT`。両側なら階層1段のため `UNRESOLVED_SL_PRIORITY` で損切りを採用（第7.4節） |
| run_end | `RUN_END` | 残存注文の取消 → 残存機会の終端（`is_run_end=True` の第3回 `step`）→ 残存建玉の MTM 最終 snapshot（第10.1節の手順5〜7） |

**具体的な値まで追った紙上トレースは [T01](../traces/T01_paper_trace.md) にある**。上の表は時刻とフェーズの骨格であり、T01 は同じ経路を「どの文書のどの型のどのフィールドに何が入るか」まで書き、正常エントリーからの利確・損切り、足内競合の2通り、受付前拒否、同時保持上限、有効時間切れ、週末持ち越し禁止、run 末尾の残存処理の8経路を1件ずつ通している。T01 で見つかった未記述と本書での対処は第17節に一覧する。

**テスト**【提案】: 単体（全順序化の鍵、丸めの方向、予算の式、足内解決の再帰）、意味論（受付拒否で注文と予約が残らない、受付と約定の原子性、同じ `event_id` の再配送、終端後の別イベント、予約移管の二重計上、期限と始値の同時刻、保護決済と通常決済の競合、末尾の非執行、warmup 中の注文ゼロ）、プロパティ（同一入力の再実行で trace が完全一致、`balance` と `equity` と枠の恒等式が全 snapshot で成立）、golden（検証戦略 A の1取引分の trace を固定）【合意済み】上位 §4.7.15 E・全体計画 §8.2。

## 12. 対象外（段階3以降）

本節は**時期**の線引き（段階2で作らないもの）であり、第1.2節は**担当**の線引き（本書 v0.1 が決めないもの）である。

- 指値・逆指値・実行可能な価格制限付き注文 → 段階6・D10。段階2は能力検査で拒否。
- 部分約定・増し玉・分割決済 → 段階6・D10。
- 複数建玉・複数銘柄の台帳とリスク配分、`strategy_priority` の設定値 → 段階6・D10。
- 期間 Exit と、初期利確の算定時点の変種 → 段階4 以降（D05 §10.2）。損切り水準の更新（トレーリング、`UPDATE_STOP`）と待機・追い越し・遡り・確認試行の判断履歴の表は、**v1.5（2026-09-22）で第8.3節・第9.2節に反映済み**。
- 未約定注文の週末持ち越しと、その設定の型名 → 段階6・D10。
- 受付前拒否の再審査（回数上限・設定の置き場所） → 段階6・D10。
- 距離型の保護水準（実約定価格を起点に損切りを置く） → 段階6・D10。段階2は拒否し、価格水準型へ暗黙変換しない。
- 証拠金・レバレッジ検査、口座強制縮小の対象と優先順位、約定直後以外の緊急決済の実行時点 → D10。
- 可変 spread・時間帯別 spread・詳細な費用内訳 → 段階6・D10。
- swap / rollover の計上 → ADR-0029 の改訂を要する別決定。
- 遅延シナリオ4ケースの処理順の検証 → 段階3・D08。
- 高速化版エンジンと参照実装の同値性検証、並列化 → 段階6。
- run の途中再開・実験間の状態引き継ぎ → 段階5以降。

## 13. 上位文書との差異

1. **受付の全順序の鍵に2項目を足した**。上位 §4.7.12 の鍵は `(decision_time, strategy_priority, opportunity_id, attempt_id)` だが、決済要求は取引機会を持たないため `opportunity_id` の位置が埋まらない。本書は先頭に `request_class`（決済が先）を足し、`opportunity_id` の位置を「発端となった対象の連番」（エントリーは機会、決済は建玉）に一般化した（第6.3節）。**4項目の相対順序そのものは変えていない**。
2. **`position_context@v1` に有効な損切り水準を加えた**。D04 §5 は段階2で読む項目を「約定価格・方向・数量」と例示しているが、固定リスクリワード比の利確にはリスク幅が要る【合意済み】D05 §11 の差異1。本書が項目の正本であり、D04 §5 の例示の追記を第15節で依頼する。
3. **フェーズ名の規則の食い違いを、共通カーネル側の改訂で解消した**（解消済み）。D02 は `^[A-Z_]+$`（数字不可）、D05 §6.1 は `P1_FEATURE` を使うと書いていた。Q1 の決定（選択肢1）により D02 §3.3 の正規表現を `^[A-Z][A-Z0-9_]*$` へ緩め、同じ PR で改訂した（D02 v1.5）。
4. **run 末尾の合図を渡す項目を公開バッチに足した**（解消済み）。D05 §7.2 の遷移9 が要求する末尾終端を起こす入口が `StrategyRuntime` に無かった。Q2 の決定（選択肢1）により `PublicationBatch.is_run_end` を足し、同じ PR で D05 §6.1 を改訂した（D05 v1.1。第10.2節）。
5. **`EntryProposal` に根拠の出力 ID が無い**。上位 §4.7.8 が要求する ID 連鎖を満たせないため、D05 への改訂依頼として第15節に挙げる（第6.1節）。
6. **段階2でレバレッジ・証拠金検査を行わない**。上位 §4.7.9 A は「数量上限、証拠金/レバレッジ、同時保有・未約定枠を合わせて検査」としているが、ADR-0015 の縦断範囲に証拠金モデルが無く、検査に使う値が存在しない（第6.4節）。同時保有・未約定枠の検査は行う（第8.2節）。
7. **複数の拒否理由が同時に成立したときの代表理由を固定した**。上位 §4.7.14 は「複数理由が成立した場合の代表理由と診断一覧の扱い」を後続へ委ねている。本書は受付前拒否についてだけ順位を定め（第5.2節）、`RUN_END` と `CARRY_NOT_ALLOWED` を他へ丸めないという同節の既存規則を満たす形にした。執行側の複数理由（gap と約定ずれ超過の同時成立）は上位 §4.7.12 が既に確定している（第7.5節の手順6）。
8. **クロス通貨の換算を基軸通貨経由の2ホップまで許す**（Q7 決定、選択肢2）。全体計画 §5.4.4 は「判断時点で利用可能な系列から取り、不足時は拒否する」とだけ定めている。本書は経路の選び方と、2本の系列の観測時点のずれの扱い（最も古い観測時点を採る・ずれを記録する・上限を超えたら拒否する）を第8.5.1節で足した。段階2（USDJPY・JPY 口座）の結果は変わらない。

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

Q1・Q2・Q6 の決定に伴う改訂と、紙上トレース T01 が見つけた阻害要因（発注の根拠になった出力の識別子が戦略ランタイムの戻り値に無い件）の解消は**本 PR で実施済み**である。残りは承認後に依頼する。

**実施済み（2026-09-21、本 PR）**

| 宛先 | 改訂内容 | 由来 |
|---|---|---|
| D02 §3.3（v1.5） | `PhaseRank.name` の正規表現を `^[A-Z_]+$` から `^[A-Z][A-Z0-9_]*$` へ緩めた | Q1 決定 |
| D05 §3・§6.1・§7.2（v1.1） | `PublicationBatch` に run 末尾を示す項目 `is_run_end` を足し、その扱いと遷移9 の発火条件を書いた | Q2 決定 |
| 上位設計書 §4.7.14・D02 §8.1（v1.5） | 保護水準の妥当性違反の理由コード `PROTECTION_INVALID` を両方の表へ足した（**同じ PR で更新する**【合意済み】D02 §8.1） | Q6 決定 |
| D05 §3・§6.2（v1.2） | `EntryProposal` に `intent_output_id` / `protection_output_id`、`ManagementRequest` に `source_output_id` を足した。**無ければ正常経路でも `OrderRequest` を組み立てられない**阻害要因であり、設計の選択ではなく欠落の補完のため本 PR で実施した（第6.1節） | T01 経路1 |

**未実施（承認後に依頼する）**

| 宛先 | 依頼 |
|---|---|
| D04 §5 | `position_context@v1` の例示に**有効な損切り水準**を足す（第13節の2。D05 §12 が既に依頼済みの項目を、本書が項目表として確定させた） |
| 上位設計書 §4.7.12 | 受付の全順序の鍵に決済要求を含める一般化（第13節の1）を追記する |
| 上位設計書 §4.7.14 | 受付前拒否で複数の理由が同時に成立したときの代表理由の順位（第5.2節、第13節の7）を、同節が「後続で具体化する」としている箇所へ追記する |

## 16. 決定事項（Q1〜Q14、すべて決定済み）

### 16.1 決定の一覧（2026-09-21）

起草時に選択式で提示した11項目（Q1〜Q11）と、D07 の承認時に D07 §15 から差し戻された3項目（Q12〜Q14、v1.2 で追加）。人間が 2026-09-21 にすべて決定し、本文へ反映済みで、**未決の項目は残っていない**。「選択肢 n」は起草時に並べた番号で、1 が起草時の推奨案である。**Q7 だけ推奨と異なる選択肢2 が選ばれた**。採らなかった案は、いずれも本文の該当節に「不採用」として1行ずつ残してある。

| # | 決めたこと | 決定 | 反映先 |
|---|---|---|---|
| Q1 | 処理段階の名前に数字を使えるようにするか | **選択肢1（推奨）**: 共通カーネルの名前規則を `^[A-Z][A-Z0-9_]*$` へ緩め、`P1_FEATURE` などをそのまま使う | §4.1、**D02 §3.3（v1.5、本 PR で改訂）** |
| Q2 | run 末尾に残った取引機会を終端させる合図をどう渡すか | **選択肢1（推奨）**: 公開バッチに「これが末尾である」ことを示す項目（`is_run_end`）を足す | §4.2、§10.2、**D05 §3・§6.1・§7.2（v1.1、本 PR で改訂）** |
| Q3 | 受付した成行注文を最初の始値で約定させるか1本見送るか | **選択肢1（推奨）**: `entry_delay_bars = 0` | §7.1.1 |
| Q4 | 許容できる不利な約定ずれの幅（USDJPY の初版値） | **選択肢1（推奨）**: 0.05 円 | §7.1.1 |
| Q5 | 注文の有効時間の初版値 | **選択肢1（推奨）**: エントリー20分・決済20分 | §7.1.1 |
| Q6 | 損切りの置き方が不正だった場合の受付前拒否の理由コード | **選択肢1（推奨）**: 専用の語 `PROTECTION_INVALID` を正本2文書に足す | §5.2、§6.4、§8.3、**上位設計書 §4.7.14・D02 §8.1（v1.5、本 PR で改訂）** |
| Q7 | 口座通貨と決済通貨が違う場合の換算経路 | **選択肢2（推奨ではない）**: 基軸通貨（USD）を経由する2ホップまで許す | §8.5.1、§3 の型表（`ConversionPolicy` / `ConversionPath` / `ConversionLeg`）、§9.3 |
| Q8 | 足内の競合を解決する解像度階層をどの設定に置くか | **選択肢1（推奨）**: 実行ポリシー（`ExecutionPolicy`）の項目にする | §7.4、§3 の型表、§9.3 |
| Q9 | 2ホップ換算で、2本の系列の観測時点のずれをどこまで許すか | **選択肢1（推奨）**: 上限を換算ポリシーの項目（`ConversionPolicy.max_observation_skew`）に置き、初版値を執行足1本分（15分）とする | §8.5.1 の規則6、§3 の型表、§9.3 |
| Q10 | 受付時の参照価格をどの足から取るか | **選択肢1（推奨）**: 直前に完了した執行足（15分足）の終値（bid）。ask は spread モデルで導く（`EXECUTION_SERIES_LAST_CLOSE`） | §6.4 の手順3、§3 の型表（`ReferenceQuoteSource`）、§7.1.1 |
| Q11 | 候補の始値が run 末尾以降になる注文を、受付時に拒否するか受け付けて末尾で取り消すか | **選択肢1（推奨）**: 受け付けて、末尾で `CANCELED`（理由 `RUN_END`）にする | §5.1 の遷移4、§5.3、§10.1 の手順5 |
| Q12 | 約定1件ごとの費用を区分別の金額列として持つか、符号化された1列の復号規則を定めるか | **選択肢1（推奨）**: 区分別の金額列（`cost_commission_amount` など3区分×2列）を表9 に足し、`costs` 列も残す | §9.2（v1.2、PR #17）。由来: D07 §15 の改訂依頼1 |
| Q13 | run 中の含み損益の評価に使う価格の出どころ | **選択肢1（推奨）**: 直前に完了した執行足の終値。買いは bid、売りは spread モデルで導く ask（受付時の参照価格 Q10 と同じ出どころ） | §8.1（v1.2、PR #17）。由来: D07 §15 の改訂依頼2 |
| Q14 | 平坦化の規則の3つの穴（複合表の接頭辞・入れ子レコード・区分タグ付き union） | **選択肢1（推奨）**: 行そのものの型は接頭辞なし・入れ子は `<フィールド名>_` を接頭辞に再帰的に開く・union は `kind` 列＋全変種の和集合・複合表の従の型は型名を接頭辞にする | §9.1（v1.2、PR #17）。由来: D07 §15 の改訂依頼3 |

### 16.2 Q7・Q9〜Q11 の決定が本書に与えた影響

**Q7（換算経路）から派生した作業**。「2本の系列の観測時点のずれを結果に残す規則」が必要になったため、第8.5.1節に経路の選び方と8つの規則を書いた（最も古い観測時点を採る・ずれを `ConversionPath.skew` として必ず記録する・上限を超えたら拒否する・経路を根拠記録に残す）。段階2（USDJPY・JPY 口座）は決済通貨と口座通貨が一致するため換算は率1の恒等換算であり、**この決定によって段階2の結果は変わらない**。

**Q9（観測時点のずれの上限）**。上限の置き場所を換算ポリシー（`ConversionPolicy.max_observation_skew`）とし、初版値を執行足1本分（15分）に確定した（第8.5.1節の規則6）。ずれの許容量は「約定の意味」でも「リスク上限」でもなく換算固有の設定であり、実行ポリシーやリスクポリシーに混ぜると、換算を変えたい実験が約定やリスクの版まで動かすことになる。初版値を執行足1本分にするのは、それより大きいずれを許すことが、執行足1本分の値動きを見落とすことと同じだからである。上限値は run manifest の `conversion_policy_ref` から読める（第9.3節）。段階2の結果は変わらない（恒等換算のため）。**不採用**: 上限を置かず `skew` の記録だけにする案（選択肢2。何時間ずれた換算率でも受け付けてしまい、結果を見るまで気付けない）、2本の観測時点が完全に一致する場合だけ許す案（選択肢3。規則は最も単純だが、粒度の違う系列を組み合わせた瞬間に換算が常に失敗する）。

**Q10（受付時の参照価格の出どころ）**。直前に完了した執行足（15分足）の終値（bid）に確定した（第6.4節の手順3、`ReferenceQuoteSource.EXECUTION_SERIES_LAST_CLOSE`）。参照価格は「約定がどれだけ不利にずれたか」を測る基準であり（上位 §4.7.9 C）、測る対象と同じ系列から取らないと、ずれの中に系列の粒度差が入り込む。**この決定は段階2でも結果を変える**: 初版データでは15分足と1時間足が別々の入力ファイルで、1時間足を15分足から集約していないため（D03 §2・§5.1）、同じ時刻でも終値が食い違いうる。食い違った場面では、戦略から見て妥当な損切りが執行系列との比較で `PROTECTION_INVALID` になって受付前拒否されることがある（第6.4節の手順3の「系列をまたぐこと」の行、[T01](../traces/T01_paper_trace.md) 経路4）。これは規則の帰結であって不具合ではなく、`ReferenceQuote.source_bar` にどの系列のどの足を使ったかが残る。**不採用**: 戦略が判断に使った評価系列の最新確定足の終値を使う案（選択肢2。戦略の判断と参照価格の出どころは揃うが、約定は執行系列で起きるため約定ずれの数値に系列差が入り込み、評価系列が粗い構成では参照価格が最大で評価足1本分古くなる）、実験設定で参照価格の系列を明示指定する案（選択肢3。執行系列と食い違う組合せを作れてしまい、段階2で使わない設定が1つ増える）。

**Q12〜Q14（D07 から差し戻された3件）**。いずれも D07 v1.0 の承認時に「設計の選択を含むため D06 で決める」として残した項目である（D07 §15）。**Q13 の決定は段階2 の結果を変える**: run 中の `equity` が一意に定まるため、D07 の最大ドローダウン（含み損益込み）が紙上トレース [T01](../traces/T01_paper_trace.md) の数値で検算できるようになり（`1,312 JPY`、率 `0.001312`。T01 §9.4）、D07 §5.2 の15指標がすべて手で確かめられる。**Q12 と Q14 は段階2 の指標を変えない**: Q12 が足す列を読むのは D07 v0.2（段階4）であり、Q14 は既に使われていた列名（D07 §4.2）を規則として書き下ろしたものである。Q14 に伴い `Reason` の列名の例を `*_reason_code` から `*_code` に直したのは、一般規則と D07 §4.2 の双方に合わせるためで、**列名が変わるのは規則の文面だけであり、実装済みのコードは無い**。

**Q11（候補の始値が run 末尾以降になる注文）**。受け付けて末尾で `CANCELED`（理由 `RUN_END`）にすることに確定した（第5.3節）。上位設計書 §4.7.13 F の時刻表（run_end=22:15、期限22:20 →「受付済みなら CANCELED/RUN_END」）とそのまま一致し、末尾手順5（残存する受付済み注文の取消、第10.1節）に段階2で通る経路ができるため、意味論テストを書ける。この経路が起きるのは、約定後の受付（rank 13）で受け付けた決済注文の候補の始値が run_end と一致した場合である（第5.1節の遷移4）。**不採用**: 受付時に `RUN_END` で受付前拒否する案（選択肢2）、実験設定で選べるようにする案（選択肢3）。理由は第5.3節に書いた。

## 17. 紙上トレース T01 で洗い出した未記述と、その反映

[T01](../traces/T01_paper_trace.md) は検証戦略 A の8経路と検証戦略 B を、D03 → D04 → D05 → D06 の型とフィールド名で1判断時点ずつ追った文書である。そこで「書けない＝本書に記述が無い」箇所として見つかったものと、本書での対処を次に示す。

| # | 未記述だった点 | 対処 | 反映先 |
|---|---|---|---|
| 1 | 受付時の参照価格をどの系列・どの足・どの項目から取るかが決まっていない | 実行ポリシーの項目（`reference_quote_source`）にし、段階2は直前に完了した執行足の終値とした（Q10 決定、選択肢1） | §6.4 の手順3、§3、§16 |
| 2 | 台帳更新フェーズ（rank 1）の内容が、保護決済を1つの確定単位でまとめる規則（§4.4）と重複していた | rank 1 を「確定後の含み損益の再評価と台帳 snapshot の記録」に限定した | §4.1、§4.2 の手順2 |
| 3 | 建玉が開いたことを知らせる通知の `opportunity_id` の出どころが書かれていない | 約定した注文の `EntryRequest.opportunity_id` から取ると明記し、その機会が同じ判断時点で終端していても矛盾しない理由を書いた | §4.2 |
| 4 | 期限切れ（遷移3）と末尾の取消（遷移4）が段階2で起きる条件が書かれていない | 遷移3 はどの設定でも発生しないこととその原因・テストの書き方を明記し、遷移4 は約定後の受付で受け付けた決済注文の候補が run 末尾と一致した場合に起きることを明記した | §5.1、§5.3 |
| 5 | 週末持ち越し禁止と「期限内に候補なし」が同時に成立したときの代表理由が無い | 受付前拒否の代表理由の順位表を置いた（`RUN_END` → `CARRY_NOT_ALLOWED` → `NO_CANDIDATE` → `PROTECTION_INVALID` → `RISK`） | §5.2、§13 の7 |
| 6 | 候補の始値が run 末尾以降になる注文の扱いが未決（上位設計書 §4.7.13 F が「詳細設計対象」としたまま） | 受け付けて末尾で `CANCELED`（理由 `RUN_END`）にすることに確定した（Q11 決定、選択肢1） | §5.3、§16 |
| 7 | エンジンが生成する決済要求の `valid_for` に何を入れるかが書かれていない | `close_valid_for` をそのまま入れると明記した | §7.3 |
| 8 | 利確水準の検査に失敗した場合と、管理要求が返らなかった場合の建玉の扱いが書かれていない | 前者はその要求だけ適用せず `PROTECTION_INVALID` で記録、後者は利確を持たないまま継続すると明記した | §8.3 |
| 9 | 銘柄別の実行ポリシー（許容不利約定幅）に対象銘柄が無い場合の扱いが書かれていない | 実行前のデータ能力検査で照合し、不足は `FAILED_CAPABILITY` とした | §7.1.1、§10.5 の手順2a |
| 10 | run 末尾の3つの処理（注文の取消・機会の終端・最終 snapshot）の順序が書かれていない | 手順5〜7 としてこの順に行うことと、その理由を書いた | §10.1 |
| 11 | spread の価格反映分を記録する費用区分が無い（執行モデルの滑りと提示価格の幅を分けて集計できない） | 費用区分に `SPREAD_IN_PRICE` を足した | §3、§7.6 |
| 12 | リスクポリシーの費用予算が固定額（`Money`）で、数量に比例する費用予算 `C(Q)` を表せない | リスクポリシーから固定額の項目を外し、`C(Q)` は費用モデルから計算すると明記した | §3、§6.4 の手順5、§7.6 |
| 13 | 参照価格に鮮度の上限を置くかどうか（仮置き） | 段階2は置かない。直前に完了した執行足に限るため鮮度が構造的に1本分以内に収まる。段階3で再判断する | §6.4 の手順3、T01 §5.3 |
| 14 | 参照価格を**どのポートから**引くかが書かれていない（戦略向けビューは期待足が未到着なら古い足へ戻らない） | エンジンが rank 0 で処理し終えた最新の執行足を `ExecutionSeries.bar` で引くと明記した | §6.4 の手順3 |
| 15 | 6件の受付前拒否の理由コードそれぞれについて、実行で起きるか・どう検証するかが書かれていない | 理由コードごとの到達可否と検証の仕方の表を新設した | §5.2 |
| 21 | 審査の途中（手順4 の保護水準の妥当性など）で拒否すると、審査記録の必須項目（許容不利価格・丸め後の損切り・換算率・費用予算）を構築できない | どこまで進んだかを示す `reached_step` を足し、手順5 以降が作る項目を省略可能にした。審査記録を残す条件も「手順3 まで到達した試行」に一般化した | §3、§4.4、§6.4 の手順8 |
| 20 | 保護水準が**戦略の見ていない系列**と比べて検査されること（評価系列と執行系列は別々の入力ファイルで、終値が一致する保証が無い）が書かれていない | 手順3 に「系列をまたぐこと」の行を足し、規則の帰結であって不具合ではないことを明記した。Q10 の影響欄も「段階2でも結果が変わる」に直した（Q10 は選択肢1 で決定済み） | §6.4 の手順3、§16.2 |
| 16 | 換算の経路（各 leg の系列・率・観測時点・ずれ）を保存する場所が15表のどこにも無い | `EvidenceRecord` に `conversion_paths` を足し、規則8 として明記した | §3、§8.5.1、§9.2 |
| 17 | 恒等換算では参照する市場系列が無く、`ConversionLeg` を1本も作れない | `ConversionPath.legs` を0本（恒等）・1本（直接）・2本（基軸通貨経由）とした | §3、§8.5.1 の規則1 |
| 18 | 可変長の入れ子（レコードの `tuple`）を Parquet へ保存する規則が無い | 要素ごとに正規化エンコード文字列にし、その `list` 列として保存すると明記した | §9.1 |
| 19 | `EntryProposal` に根拠の出力 ID が無く、正常経路でも `OrderRequest` を組み立てられない（阻害要因） | D05 §3・§6.2 を本 PR で改訂し（v1.2）、`intent_output_id` / `protection_output_id` / `source_output_id` を足して解消した | §6.1、§15、D05 §3・§6.2 |

T01 が「本書の範囲では追えない」と記録したもの（検証戦略 B の後続確認・待機、複数建玉の台帳など）は、いずれも第1.2節の第4列または第12節で担当と時期が決まっているものであった。**このうちエンジン側の担当だった4件（待機中の評価要求の検査・待機の出来事の処理点・トレーリングの適用意味論・段階3 の判断履歴の表）は v1.5（2026-09-22）で埋めた**（第4.1節・第4.4節・第8.3節・第9.2節）。複数建玉の台帳は段階6・D10 のままである。
