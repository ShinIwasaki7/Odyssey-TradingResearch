# D07: 単一実行の評価境界設計（`odyssey_fx.evaluation`: domain.metrics / domain.status / application.evaluate_run / adapters）

作成日: 2026-09-21
状態: **v2.0（2026-09-25、段階4 の設計 PR）。承認待ち**。段階2 の範囲（v0.1〜v1.4）は**承認（2026-09-21、PR #17）**済みで、本改訂は原則として変えていない（例外5件は第1.2節と第13節）。
v2.3（2026-09-25、成果物の書き込みを「存在すれば失敗」に統一する根本対処 R4 の PR）: 必須表の空欄 R1-D07-3（保存先 `runs/<run_id>/eval/<run_evaluation_id>/` が既にあるときの扱い）を R4 の規則で埋めた。**存在すれば、何も書かずに失敗する**（空・書きかけも同じ。失敗は `ArtifactAlreadyExists`）。**評価は置換の指示を持たない**（置換を許すのは run の成果物だけ。ADR-0006、D06 §9.3）。第8.2節・第10.1.1節を改め、第19.6節（実験の経路の再利用）をこの規則に合わせた。第1〜27節の番号と評価の中身・識別子は変えていない。
v2.2（2026-09-25、段階4 実装 PR 1。PR #44）: 実装で見つかった設計の空白への人間の決定（2026-09-25）を反映した。(1) 結果 DTO と run manifest の run の状態の食い違いを見る致命の整合検査 **C13 `run_status_consistent`** を足した（第10.4節・第10.1.1節）。(2) 実装 PR 1 の仮置き6件を【確定】として第10.5節に書いた。第1〜27節の番号は変えていない。
v2.1（2026-09-25、段階4 実装 PR 2・PR #45）: 書式 v2（第18節）の実装で決めた細則を、人間の決定（2026-09-25）に従って第18節へ確定した。(1) 戦略ファイルの有効性束縛（`opportunity_validity`）は**省略を拒否**する（第18.4節の表の「省略は束縛なし」を D04 §10.2・ADR-0031 に合わせて改めた）。入場方針（`entry_policy`）も省略を拒否する。(2) 遅延シナリオの版参照は**シナリオの id と版をそのまま載せ、ダイジェストは規則の正規形から作る**（第18.3節。起草時の「ポリシーの版参照と同じ作り方」を改めた）。遅延なしの参照は書式 v1 と同じ値のまま。(3) 遅延は戦略向けの公開時刻だけを動かし、**執行系列に当てても約定の時刻は変わらない**（第18.3節）。(4) 書式 v2 にコマンドの環境の引数も渡したら拒否（第18.5節）、絶対パスとリポジトリの根の外を指すパスの拒否（第18.2節）、`delay_scenario: null` と遅延 0 の拒否（第18.3節）、同梱の設定の指標集合の版とカレンダー（第18.7節）。
v2.0（2026-09-25）: 段階4「単一実行評価の整備」の設計を足した。人間の決定6件（2026-09-25。第16.3節）を写し、決定に含まれない設計判断を要決定 Q7〜Q13（第26節）として挙げ、**同日に人間が7件とも決定した**（Q7 だけは推奨ではない値）。あわせて、PR #40 が必須表に残した空欄のうち本書の担当4件（R1-D07-1・2・4・5）を埋めた（第10.1.1節・第9.3節）。中身は、(1) **指標集合 v2**（年率化リターン・年率化シャープレシオ・プロフィットファクター・平均取引損益の4件を十進数のまま足す。第5.5節）、(2) **取引単位の費用**と入場費用を含む取引損益（第7.3節）、(3) 待機をはさんだ評価要求を**要求単位で数える集計**（第6.3節）、(4) 整合検査の結果の**3区分（合格・不合格・読めなかった）**（第10.4節。AGENTS.md の後続対処 R5）、(5) **実験設定の書式 v2**（第18節）、(6) **実験の記録票と結末記録**（第19節）、(7) **研究ポリシー v1**（事前固定の検査と複雑性の上限。第20節）、(8) **別プロセスでの再現**（第21節）、(9) **人間向けレポート**（第22節）、(10) **実データでの実行**（第23節）、(11) **封印期間の許可判定を段階5 へ送った記録**（第24節）。実装は4本の PR に分け、各 PR が実装する設計節を第17.2節に置いた。第1〜16節の番号は変えていない。
v1.5（2026-09-25、PR #40）: 設計文書の必須表（R1（PR #26 承認）。全体計画書 §8.5）を加えた（第1.2節の末尾に置き場所の一覧、第9.3節に値の伝播表、第10.1.1節に状態×出来事表）。本文の規則は変えていない。表を埋める途中で本文から埋められないマスが5件見つかったので、「要決定」として各表の直後に挙げた（R1-D07-1〜5）。
v1.4（2026-09-23）: 検証戦略 B の紙上トレース [T02](../traces/T02_paper_trace_strategy_b.md) が、段階3 の判断履歴を本書が読むとどうなるかを確かめた結果を2か所に足した。(1) **段階3 で足される4表（待機の出来事・遡った入力・確認試行・有効性の再検査）を本書は読まない**（第4.2節。読まない表は合計10表になる）。(2) **評価の結果区分の集計は、段階3 では語彙が5語になり（0件の行も出す。出さないと遅延シナリオごとに行の集合が変わる）、その合計が評価要求の数ではなく評価記録の数になる**（第6.1節）。**指標15件の値はどちらでも変わらないが、段階3 の実装で段階2 の run を評価すると集計に0件の行が2行増える**ので、評価側の固定出力（golden）は更新の対象になる。v1.3（2026-09-22、PR #20）: 段階2 の実装（PR #20）が残した**仮置き事項8件に人間の決定が出た**ので本文へ反映した。(1) 建玉を保有していた時間の割合（第5.2節の #9）は**完了取引だけ**を数える。式の本文にあった「未決済建玉は run 末尾までを数える」を削り、同じ節の冒頭の `Σ` の定義（完了した取引についての合計）と検算値 `0.0078125` に揃えた。これで**段階2 の指標15件すべてが T01 の検算値と一致する**。(2) 読む列（第4.2節）に**8列**を足した。処理点を組み立てる7列（表11 の `opened_at_phase` / `opened_at_sequence`、表9 の `processed_at_phase` / `processed_at_sequence`、表3 の `at_time` / `at_phase` / `at_sequence`）と、不利約定幅の符号に要る表7 の `side` である。(3) 評価 manifest の `run_manifest_ref`（第8.3節）は入力とポリシーの群のダイジェスト（`ConfigDigest`）である。(4) 取引機会の終端理由の語彙（第6.1節）は `RUN_END` を含む**8語**であり、語彙に無い鍵も行として残す。(5) 拒否（`REJECTED`）の run でも整合検査を7件実施する（第10.1節。末尾の集計と比べる C5 だけ実施しない）。(6) `EvaluateRun`（第3節）は `Protocol` ではなく具体クラスである。**未確定として残した項目は無い**。v1.1（2026-09-21、PR #17）: 第15節の改訂依頼1〜3 に対する人間の決定（D06 の Q12〜Q14、いずれも選択肢1）が出たため、**同じ PR で D06 を v1.2 に改訂し、本書の未確定箇所をすべて閉じた**。(1) 第4.2節の † を付けていた列名が確定した（平坦化規則の確定による。`decision_kind` は規則どおり `kind` になった）。(2) run 中の含み損益の評価価格が確定し、**最大ドローダウン（含み損益込み、#5・#6）の検算値が求まった**（`1,312 JPY` / `0.001312`）。これにより**段階2の指標15件すべてが紙上トレース [T01](../traces/T01_paper_trace.md) の数値で手で確かめられる**（従来は13件）。検算に必要な `equity` の全値は T01 v1.1（第9.4節）に足した。(3) 約定1件ごとの費用が区分別の金額列として読めるようになった（読むのは本書 v0.2・段階4）。**第15節に未実施の改訂依頼は残っていない**。v1.0（2026-09-21）: 第16節の要決定 Q1〜Q6 を人間がすべて決定し（6件すべてが提示時の推奨案である選択肢1）、本文へ反映した。**未決の項目は残っていない**。決定に伴い、**同じ PR で正本を1件改訂した**: 結果 DTO から資産推移の2項目（`balance_series` / `equity_series`）を落とす改訂（D06 §9.4、v1.1。第15節の改訂依頼4。D06 §9.4 が既に定めた「集計前のレコードは表のパス経由で渡し、結果 DTO の中で集計しない」から一意に導ける補完であり、設計の選択は伴わない）。第15節の改訂依頼1〜3 は設計の選択を含むためこの時点では実施せず、v1.1 で解消した。v0.1（2026-09-21）: 段階2（検証戦略 A の単一 run）の結果を、再現可能に・数値で・swap 未計上と明記して出すために必要な**境界**だけを決める。指標を将来まで書き切ることは目的にしない（全体計画 §6 D-2「D05 と D07 の将来機能をすべて書き切る必要はない」）。ADR-0016 条件2 のうち「D07 の単一実行評価境界」を本書で充足する。第16節に決定の一覧を置く。 v1.2（2026-09-22、PR #19）: 判断履歴の数値の列が人の読める固定小数表記になったことを第4.3節に注記した（D06 §9.1 v1.3）。**読む列の名前と顔ぶれは変わらず**、`Decimal(文字列)` の往復も変わらないため、指標・集計・整合検査はいずれも影響を受けない。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.7.12・§4.7.13 C/E・§4.7.14・§4.7.15・§5.3・§6、[全体計画書](fx_research_platform_overall_plan.md) §5.5・§5.5.1・§5.5.2・§6 C-5・§6 D-2・§7.5・§8.1・§8.2、[D01](D01_architecture_and_dependency_rules.md) §3.2・§4・§7.2・§10.3、[D02](D02_common_kernel.md) §4・§7.1・§8.1・§8.3・§9、[D03](D03_marketdata_and_time.md) §3.9・§6.1、[D05](D05_strategy_runtime.md) §3・§7.2、[D06](D06_backtest_vertical_slice.md) §9（全項）・§10.3・§14、[T01](../traces/T01_paper_trace.md)、ADR-0006（決定論的 ID）、ADR-0012（Decimal / float 境界）、ADR-0016（実装開始条件）、ADR-0027（成果物は Parquet 表＋JSON マニフェスト）、ADR-0029（swap 未計上）、ADR-0030（足内競合解決契約）。v2.0 で足したもの: 上位設計書 §6（研究ポリシー）・§7、全体計画書 §8.2 の段階4 の行・§10、[D03](D03_marketdata_and_time.md) §3.4・§3.6・§3.8、[D04](D04_strategy_declarations.md) §5・§13.1、[D05](D05_strategy_runtime.md) §6.1・§6.4、[D06](D06_backtest_vertical_slice.md) §3・§10.5、[D08](D08_test_strategy.md) §2.3、ADR-0014（期間のアクセス分類）、ADR-0018（設定ファイル）、ADR-0028（CLI）、[AGENTS.md の設計](agents_md_for_codex_review.md) §5 の R5
対応段階: 段階2で最小実装（v0.1〜v1.4）、段階4で拡張（**v2.0**。他文書が「v0.2」と呼んでいた版。第27節）。

## 0. 本書の位置付けと凡例

バックテストが残した**判断履歴（trace）と実行条件（run manifest）を読み、単一 run の数値の結果を作る**手順と型を決める。バックテストの意味論には立ち入らず（全体計画 §5.5）、trace を作り直したり、結果を都合よく修正したりしない【合意済み】上位 §5.3。

凡例は全体計画書第0節に従い、本書は各項目に次のいずれかを付ける。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D06、T01）で確定済み。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【確定】 | 承認後の人間の決定で本文へ確定した事項（v1.3 以降） |
| 【要決定】 | 承認時にユーザーが選択する事項。**Q1〜Q6 は 2026-09-21 に、段階4 の Q7〜Q13 は 2026-09-25 にすべて決定済み**（第16節・第26節）。**未決の項目は残っていない**（必須表の空欄 R1-D07-3 は v2.3 で根本対処 R4 により決定） |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | `domain.metrics`（指標の値と取引の記録）、`domain.status`（評価の状態と整合検査）、`application.evaluate_run`（trace → 指標・集計・診断・状態）、`application.manifest`（評価 manifest）、`adapters.fs_store`（Parquet ＋ JSON） | 【合意済み】D01 §1・全体計画 §5.5.1 |
| 依存できるもの | Python 標準ライブラリ、`odyssey_fx.common`、`marketdata.domain`、`backtest` の `domain` と `trace`（`engine` / `admission` / `execution` / `portfolio` / `application` は不可）、`strategy` の `declarations` / `records` / `compiler`。実行と保存はポート経由 | 【合意済み】D01 §3.2（契約 F4・F7・F8）・§4 |
| 決めないもの | 注文・約定・台帳の意味論（D06）、部品の計算規則（D05）、探索・分割・選定（D09）、封印期間の許可判定（`holdout_gate`。D09、第24節）。実験の記録票と研究ポリシーの内容は v2.0 で本書が決めた（第19節・第20節） | 【合意済み】 |
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

### 1.2 段階2 で確定したこと・段階4 で本改訂（v2.0）が決めること・後続に委ねること【提案】（**レビュー対象範囲の正本**）

**v2.0 のレビューの対象範囲は、下表の「段階4 で本改訂（v2.0）が決めること」の列**である。列は名前で呼ぶ（番号で数えると、列が増えたときに指す先がずれる。D05 §1.2 と同じ扱い）。

| 列 | 呼び方 | v2.0 での扱い |
|---|---|---|
| 3列目 | **段階2 で確定**（v1.0〜v1.4） | 承認済み。**本改訂では原則として変えない**。ここへの指摘は「段階2 の確定への異議」として記録し、本 PR では直さない。**例外は5件**で、いずれも人間の決定、承認済みの根本対処、または要決定に基づく: (a) 完了取引の損益（#2）に入場費用を含める改訂（第7.3節。v1.x が「本書 v0.2 で外す」と予告していた注記 `ENTRY_COST_EXCLUDED` の解除）、(d) 取引の勝敗（第5.2節の #4 と `TradeRecord.outcome`）を入場費用込みの取引損益 `trade_profit` の符号で決める改訂（第7.3節。Q10 決定）、(b) 整合検査の結果を3区分にする改訂（第10.4節。AGENTS.md の後続対処 R5、2026-09-24 の人間の承認）、(c) 封印期間の許可判定（`holdout_gate`）の担当を本書から段階5 の D09 へ移す改訂（第24節。2026-09-25 の人間の決定2）、(e) 評価の識別子（`RunEvaluationId`）の算出元に、評価が受け取った取引カレンダーの識別と版を足す改訂（第9.2節。Q8 決定でカレンダーが評価の入力になったことの帰結）。5件とも第13節に差異として挙げる |
| 4列目 | **段階4 で本改訂（v2.0）が決めること** | **レビュー対象範囲**。ここへの指摘を直す |
| 5列目 | **後続が決めること** | 対象外（担当へ）として記録し、本書では直さない |

D04 §1.2・D05 §1.2・D06 §1.2 と同じ例外を置く。**本書の文が「後続が決めること」の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。その列の内容を本書に書き足すことはしない。

| # | 領域 | 段階2 で確定（v1.0〜v1.4。本改訂では変えない） | 段階4 で本改訂（v2.0）が決めること（**対象内**） | 後続が決めること（対象外・担当） |
|---|---|---|---|---|
| 1 | 入力契約 | 入力の範囲（Q1 決定: `BacktestResult` / `RunManifest` / trace の9表）、読む列と用途、読まない表、読み出しのポートの操作（第4節） | 段階4 で足す読む列（表9 の費用の区分別の列、表2 の `request_id` / `decision_time`。第4.2節）、**取引カレンダーを4つめの入力に加えること**（第5.5節。Q8 決定）。段階3 で足された4表は引き続き読まない | 遅延シナリオ別・複数 run の比較（**段階5・D09**） |
| 2 | 指標 | 指標15件の式・入力列・単位・丸め・欠損時の扱いと T01 検算値（第5.2節） | **指標集合 v2**: 追加指標4件（年率化リターン・年率化シャープレシオ・プロフィットファクター・平均取引損益）の式・演算の順序・欠損時の扱い、候補と選定理由、十進数のまま平方根を取る規則（第5.5節。人間の決定3） | 分布指標・下方リスク指標（ソルティノ等）と複数 run の集約・選定（**段階5・D09**） |
| 3 | 集計と診断 | 集計7種、約定の診断3項目（第6節） | 待機をはさんだ評価要求を**要求単位で数える集計**を1種足すこと（第6.3節。Q13 決定） | 人間向けレポートの見た目の改良（本書の後続版） |
| 4 | 通貨と費用 | 口座通貨で統一、価格反映済み費用を二重計上しない、swap 未計上の明記（第7.1節・第7.2節） | **取引単位の費用**と、入場費用を含む取引損益（第7.3節）。注記 `ENTRY_COST_EXCLUDED` を指標集合 v2 で外すこと | swap の計上（**ADR-0029 の改訂を要する別決定**）、証拠金に基づく指標（**D10**） |
| 5 | 結果の型と保存 | 評価5表と評価 manifest、保存先（第8節） | **実験の記録票（実験 manifest）と実験の結末記録**の項目・保存先・書き込み規則（第19節）、**人間向けレポート**（第22節） | 探索履歴・分割・holdout 閲覧履歴（**段階5・D09**） |
| 6 | 再現性 | 決定論の条件、結果ダイジェスト、評価コードのダイジェスト（第9節） | **別プロセスでの再現の手順と判定**（第21節）。検証の置き場所は D08 v1.9 §2.3 | 別の機械での再現（本書の後続版。第21.5節） |
| 7 | 失敗と0取引 | 評価の状態4値、整合検査8件、「値なし」の型、失敗 run の扱い（第10.1〜10.3節） | 整合検査の結果の**3区分（合格・不合格・読めなかった）**（第10.4節。R5）、**研究ポリシー v1** の検査・複雑性の計測と上限（第20節。人間の決定5） | 探索の中断（`ABORTED`。**段階5・D09**） |
| 8 | 実験設定の書式 | ― | **実験設定の書式 v2**（検証戦略 B・遅延シナリオ・承認済み snapshot・仮説・研究ポリシー参照を書ける。第18節。人間の決定4）。解決済みの値（`RunConfig`）は D06 §3 のまま | 戦略宣言ファイルの表現の一般規則（**D04 §13.1**。本書第18.4節はその適用を挙げるだけ）、探索計画・分割の書き方（**段階5・D09**） |
| 9 | 実データ | ― | 実データでの実行の範囲と、その記録・テストでの扱い（第23節。人間の決定6） | 実データのデータ欠損 8,140 区間を個別に見直すか（**人間の判断**。本書は扱わない） |
| 10 | 封印期間 | ― | **封印期間の許可判定を段階5 へ送った記録**（第24節。人間の決定2） | `holdout_gate` と閲覧履歴・使用済み期間の opt-in の設計（**段階5・D09**） |

**状態×出来事表と値の伝播表**（AGENTS.md の後続対処 R1 の必須表）: 本改訂が新しく定める状態機械は**1つの実験の進み方**（第19.4節の状態×出来事表）であり、段階をまたぐ値（`experiment_name`・記録票の識別子・`run_id`・評価の識別子）の伝播は第19.5節の表に置く。段階2 で確定した評価の状態（第10.1節）は状態機械ではなく入力から一度だけ決まる区分であり、表を置かない。

前提として **D06・D05・D04・D03・D02 から受け取るもの**は次のとおりで、本書はこれらを再定義しない。

| 出どころ | 受け取るもの |
|---|---|
| D06 §9.4 | `BacktestResult` の9項目（`status` / `trace_tables` / `summaries` / `swap_modeled` / `unresolved_intrabar_count` / `capability_report` / `trade_count` / `opportunity_count` / `manifest_ref`） |
| D06 §9.3 | `RunManifest` の項目（特に `account`（`AccountSpec` の3項目）、`run_interval`、各 `PolicyRef`、`calendar_ref`、`RunStatus`、`DataCapabilityReport`）と、`ConfigDigest`・`RunId` の算出規則 |
| D06 §9.2 | trace 19表（段階2 は15表）の `TraceTable` 名・主キー・外部キー、表9 の費用の区分別の列（Q12 決定） |
| D06 §9.1 | 保存形式（Parquet ＋ JSON）と平坦化の規則 |
| D06 §10.3・§10.5 | `FinalSummaries` の3項目と `cost_breakdown`、実行前のデータ能力検査（第23節が当たる制約） |
| D06 §3 | 実行の解決済みの値（`RunConfig`）。書式 v2 は同じ値へ解決する |
| D05 §3・§6.1 | `EvaluationRecord` のフィールド（表2 の列の正本）、run 末尾で待機要求を決着させる規則 |
| D04 §3・§13.1 | 戦略宣言の型と、設定ファイル表現の一般規則（戦略ファイルの正本） |
| D03 §3.4・§3.6・§3.8 | 取引カレンダー（取引日の区切り）、遅延シナリオの型、アクセス分類 |
| D02 §4・§8.1・§8.3・§9 | 十進数のカーネル精度（28桁・`ROUND_HALF_EVEN`）、理由コードと評価見送りの診断コードの語彙、正規化エンコードとダイジェスト |

**必須表（全体計画書 §8.5、R1）の置き場所**（v1.5）。本書は評価の段階と状態（第10節）と、実行から評価へ渡る値（第4節・第8節・第9節）を定めるので、3つの表をすべて持つ。

| 必須表 | 置き場所 |
|---|---|
| 境界表 | 本節の上の2表 |
| 状態×出来事表 | 第10.1.1節（評価の段階 × 出来事）。段階4 の1つの実験の進み方は第19.4節 |
| 値の伝播表 | 第9.3節。段階4 の実験の値は第19.5節 |

## 2. モジュール構成【提案】

D01 §7.2 の一覧のうち、段階2で作るものと後続で作るものを分ける。モジュールの追加・分割はしない。

| サブパッケージ | 段階2で作る | 段階4以降で作る |
|---|---|---|
| `domain` | `metrics.py`、`status.py` | `research_policy.py`（段階4）、`experiment.py`（段階4）、`search.py`・`splits.py`（段階5） |
| `application` | `evaluate_run.py`、`manifest.py`、`ports.py` | `run_experiment.py`（**段階4** で単一実行を作り、段階5 で探索を足す。v2.0）、`holdout_gate.py`（**段階5・D09**。v2.0、人間の決定2） |
| `adapters` | `fs_store.py` | `report.py`（段階4） |

`manifest.py` を段階2から作るのは、単一 run の評価 manifest（第8.3節）が再現性の条件そのものであり、実験 manifest（段階4）を足すときに置き場所を動かさないためである。

**v2.0 の変更**【提案】: `run_experiment.py` を段階5 から段階4 へ前倒しする。段階4 の完了条件（記録票を run より先に保存し、研究ポリシーを検査してから run し、別プロセスで再現する）は、run と評価を1つのユースケースで順に呼ぶ置き場所を要し、D01 §7.2 がその名前を `run_experiment` と決めているためである。`holdout_gate.py` は人間の決定2 により段階5 へ送った（第24節）。実験の記録票は `domain.experiment`（型）と `adapters.fs_store`（保存）に置き、`application.manifest` は評価 manifest のままにする（D01 §7.2 のモジュール一覧は変えない）。

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

**v2.0（段階4）で改める型と足す型**【提案】。上表の段階2 の行のうち、次の7行は v2.0 で改める（改めた後の形を書く）。その下は v2.0 で足す型である。

| 型 | 置き場所 | 区分 | v2.0 の形 | 詳細 |
|---|---|---|---|---|
| `MetricId` | `domain.metrics` | enum | 第5.2節の15件＋第5.5節の4件（#16〜#19）。宣言順は #1〜#19 | §5.5 |
| `MetricCaveat` | `domain.metrics` | enum | `SWAP_NOT_MODELED` / `PRICE_EMBEDDED_COST` / `OPEN_POSITION_EXCLUDED` / `UNRESOLVED_INTRABAR_PRESENT`（`ENTRY_COST_EXCLUDED` は指標集合 v2 で外す） | §7.3 |
| `TradeRecord` | `domain.metrics` | レコード | 段階2 の16フィールド＋`entry_commission` / `entry_slippage_in_price` / `entry_spread_in_price` / `close_commission` / `close_slippage_in_price` / `close_spread_in_price`（いずれも `Money \| None`。`None` はその区分の費用記録が無いこと。D06 §9.2 の表9）＋`trade_profit: Money`。`outcome` は `trade_profit` の符号で決める | §7.3 |
| `CategoryKind` | `domain.metrics` | enum | 第6.1節の7件＋`EVALUATION_REQUEST_FINAL_OUTCOME`（第6.3節） | §6.3 |
| `ConsistencyCheckResult` | `domain.status` | レコード | `check: str` / `level: CheckLevel` / `outcome: CheckOutcome` / `table: TraceTable \| None` / `expected: str` / `observed: str`（`passed: bool` を `outcome` に置き換える） | §10.4 |
| `EvaluationManifest` | `application.manifest` | レコード | 段階2 の項目＋`unreadable_check_count: int`（`outcome` が `UNREADABLE` の検査の件数）＋`calendar_ref`（評価が受け取った取引カレンダーの識別と版 `(id, version)`。D03 §7.4 の値の伝播表の主キーと同じ組。`RunEvaluationId` の算出元。第9.2節）。run manifest から写す5項目（`run_manifest_ref` / `run_code_digest` / `run_status` / `run_failure_reason` / `account_currency`）は `None` を許す（run manifest が読めないとき。§10.1.1 の R1-D07-4） | §10.4 |
| `ManifestReadFailure` | `application.ports` | レコード | `run_id: RunId` / `detail: str`（読めなかった理由。正規化エンコード文字列） | §10.1.1 |
| `ResultRepository` | `application.ports` | Protocol | 段階2 の操作のうち `read_manifest(run_id: RunId) -> RunManifest \| ManifestReadFailure` に改める（ファイルが無い・壊れているときに例外にしない）。**`read_result(run_id: RunId) -> BacktestResult \| ResultReadFailure` を足す**（`runs/<run_id>/result.json` を読む。第4.1節の v2.0 段落） | §4.1・§10.1.1・§19.6 |
| `ResultReadFailure` | `application.ports` | レコード | `run_id: RunId` / `detail: str`（読めなかった理由。正規化エンコード文字列）。`ManifestReadFailure` と同じ形 | §4.1 |
| `EvaluateRun` | `application.evaluate_run` | 具体クラス | `evaluate(result: BacktestResult, repository: ResultRepository, metric_set_version: int, calendar: TradingCalendar) -> EvaluationReport`（Q8 決定）。カレンダーの渡し方は第4.1節 | §4.1・§5.5 |
| `CheckOutcome` | `domain.status` | enum | `PASSED` / `FAILED` / `UNREADABLE`。整合検査と研究ポリシーの検査が共有する | §10.4・§20.3 |
| `ExperimentStatus` | `domain.experiment` | enum | `COMPLETED` / `REJECTED_BY_POLICY` / `FAILED_POST_RUN_CHECK` | §19.4 |
| `ResolvedFile` | `domain.experiment` | レコード | `role: str` / `text: str` / `sha256: str` | §19.2 |
| `ExperimentManifest` | `domain.experiment` | レコード | 第19.2節の表の項目すべて（`experiment_id: ExperimentId`（D02 §7.1 の型）と `experiment_name: str` / `experiment_version: int` を含む） | §19.2 |
| `ExperimentOutcome` | `domain.experiment` | レコード | 第19.3節の表の項目すべて | §19.3 |
| `ComplexityLimits` | `domain.research_policy` | レコード | `component_kinds: int` / `instances: int` / `parameters: int` / `decision_outputs: int`（いずれも正の整数） | §20.2 |
| `ComplexityMeasures` | `domain.research_policy` | レコード | 同じ4フィールド（それぞれ `int \| None`。0 以上の整数、または**計測できなかったことを表す `None`**。`None` の計測値があれば検査 P6 は `UNREADABLE`） | §20.4 |
| `InstanceProfile` | `domain.research_policy` | レコード | `instance_id: str` / `component_id: str` / `parameter_count: int` / `output_data_types: tuple[str, ...]` | §20.4 |
| `ResearchPolicy` | `domain.research_policy` | レコード | `policy_id: str` / `version: int` / `digest: ContentDigest` / `limits: ComplexityLimits` | §20.2 |
| `PolicyCheck` | `domain.research_policy` | enum | `HYPOTHESIS_PRESENT` / `RESEARCH_HISTORY_ONLY` / `PREREGISTRATION_UNCHANGED` / `RUN_MATCHES_PREREGISTRATION` / `EVALUATION_RULE_MATCHES` / `COMPLEXITY_WITHIN_LIMITS`（第20.3節の P1〜P6 の順） | §20.3 |
| `PolicyCheckStage` | `domain.research_policy` | enum | `PRE_RUN` / `ON_SAVE` / `POST_RUN` | §20.3 |
| `PolicyCheckResult` | `domain.research_policy` | レコード | `check: PolicyCheck` / `stage: PolicyCheckStage` / `outcome: CheckOutcome` / `expected: str` / `observed: str`（正規化エンコード文字列） | §20.3 |
| `ManifestSaveResult` | `application.ports` | enum | `CREATED` / `ALREADY_IDENTICAL` / `CONFLICT` | §19.3 |
| `ExperimentStore` | `application.ports` | Protocol | `save_manifest(manifest: ExperimentManifest) -> ManifestSaveResult` / `read_manifest(path: str) -> ExperimentManifest` / `write_outcome(outcome: ExperimentOutcome) -> None`。D01 §4 が所在と実装者（`fs_store`）を確定済みで、本書は操作だけを具体化する | §19.3 |
| `BacktestRunner` | `application.ports` | Protocol | `run(config: RunConfig, compiled: CompiledStrategy) -> BacktestResult`。D01 §4 が確定済み（実装は `app` が `backtest.application.run_backtest` を適合させる） | §19.4 |
| `SnapshotCatalog` | `application.ports` | Protocol | `access_classes(snapshot: SnapshotRef, partitions: frozenset[PartitionId]) -> Mapping[PartitionId, AccessClass]`。D01 §4 が確定済み | §20.3 の P2 |
| `PreparedExperiment` | `application.run_experiment` | レコード | 合成が組み立てた1回分の入力: `manifest: ExperimentManifest` / `run_config: RunConfig` / `compiled: CompiledStrategy` / `calendar: TradingCalendar` / `expected_run_id: RunId` / `code_digest: CodeDigest` / `lock_digest: LockDigest` / `env_digest: EnvDigest` / `git_commit: str` / `git_dirty: bool`（後ろの6つは**この実行**の予測 `RunId` と環境。合成が計算し、`RunExperiment` は結末記録へ写し、第19.6節の既存成果物の確認に使う。記録票の環境の群とは別の値でありうる） | §19.3・§19.6 |
| `RunExperiment` | `application.run_experiment` | 具体クラス | `execute(prepared: PreparedExperiment) -> ExperimentOutcome \| ExperimentRefusal`。結末記録を書かずに拒否する2つの経路（記録票の内容違い、既存の run 成果物と衝突して再利用できない）で後者を返す（第19.4節・第19.6節） | §19.4 |
| `RefusalKind` | `application.run_experiment` | enum | `MANIFEST_CONFLICT`（記録票が同じ版で内容違い。検査 P3 の不合格）/ `RUN_ARTIFACT_CONFLICT`（`runs/<expected_run_id>/` があり再利用できない。第19.6節） | §19.4・§19.6 |
| `ExperimentRefusal` | `application.run_experiment` | レコード | `kind: RefusalKind` / `experiment_id: ExperimentId` / `detail: str`。コマンドはこれを終了コード 5 に写す（第21.3節） | §19.4・§21.3 |
| `ReproductionVerdict` | `application.run_experiment` | enum | `REPRODUCED` / `RUN_ID_MISMATCH` / `RESULT_MISMATCH` / `ENVIRONMENT_MISMATCH` / `MANIFEST_TAMPERED` | §21.2 |
| `ReproductionReport` | `application.run_experiment` | レコード | `experiment_id` / `verdict: ReproductionVerdict` / `expected_run_id`（結末記録の `run_id`） / `observed_run_id: RunId \| None` / `expected_result_digest` / `observed_result_digest: ContentDigest \| None` | §21.2 |

`TradingCalendar` / `AccessClass` / `PartitionId` は D03、`RunConfig` は D06 §3（`backtest.domain`）、`CompiledStrategy` は D05 §5.3（`strategy.compiler`）が正本である。評価がこれらを参照できることは D01 §3.2 の許可表（契約 F4・F7・F8）のとおりで、依存規則は変えない。**`RunExperiment` を段階4 で作る**（第2節の表で段階5 としていた置き場所を前倒しする）。段階4 は探索計画が `NONE` の単一実行だけを扱い、段階5 で D09 が探索計画と分割を足す。

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

**v2.0（段階4）: 取引カレンダーを4つめの入力に加える**【提案】（Q8 決定）。年率化（#16）と日次の資産系列（#17）は「取引日」で数える必要があり、取引日の区切り（NY 17時、休場日を除く）は取引カレンダー（D03 §3.4）の規則である。**カレンダーは市場データではない**ので、Q1 の決定が退けた「市場データを読み直す経路」（as-of の規則とアクセス分類の許可を評価側にも置くこと）は生じない。評価は `EvaluateRun.evaluate` の引数としてカレンダーを受け取り、**run manifest の `calendar_ref` と一致すること**を致命の整合検査 C10（第10.4節）で確かめる。一致しないカレンダーで数えると、run と評価で別の取引日を使うことになる。

**カレンダーの渡し方**【提案】: run manifest は `calendar_ref`（識別と版）だけを持ち本文を持たないので、評価の呼び出し側がカレンダーを読み込んで渡す。(a) **`evaluate` コマンドに `--calendar <YAML>` を必須の引数として足す**（段階2 の `run` コマンドと同じ渡し方）。(b) `experiment run` / `experiment reproduce` は記録票の `resolved_files` の `calendar` の本文から読み込む（第19.2節）。どちらも C10 で `calendar_ref` との一致を確かめ、違えば評価は `FAILED` になる（黙って別の取引日で数えない）。

**v2.0（段階4）: 保存済みの `BacktestResult` を読む操作を `ResultRepository` に足す**【提案】。入力1 の「`ResultRepository` が読んだ値」を読む操作が、段階2 のポートには無かった（段階2 の実装は `fs_store` の具体クラスにだけ `read_result` を置き、`app` の `evaluate` コマンドがそれを呼んでいる）。段階4 では**アプリケーション層の `RunExperiment` が、既存の run 成果物を再利用するとき（第19.6節の手順2）に保存済みの結果を読む**ので、ポートの操作にする。
- 操作: `read_result(run_id: RunId) -> BacktestResult | ResultReadFailure`。読む先は **`runs/<run_id>/result.json`**（JSON）で、保存先と形式の正本は D06 §9.1・§9.4（v1.11、R1-D06-3。2026-09-25 の人間の決定）である。本書はファイルの形式を再定義しない。系列の時間足は同じディレクトリの `manifest.json` の定義を使う（同じく D06 §9.1）。
- **読めないとき（ファイルが無い・壊れている・中身の `run_id` が引数と違う）は例外にせず `ResultReadFailure` を返す**（`read_manifest` の R1-D07-4 と同じ扱い）。ただし `BacktestResult` は評価の入力1 そのものなので、`ManifestReadFailure` と違って**評価を始められない**。呼び出し側の扱いは2つだけである: (a) `evaluate` コマンドは読込の誤りとして終了コード 2 で終わる（評価の成果物は書かない。段階2 の `evaluate` の引数の誤りと同じ）、(b) `RunExperiment` は「既存の成果物を再利用できない」として第19.6節の手順3（拒否。終了コード 5）へ進む。
- 実装は PR 3（第17.2節）で、段階2 の `fs_store` の `read_result` をこの操作に移す。

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

**v2.0（段階4）で読む列を足す**【提案】。段階4 の指標と集計のために、次の列を上表の各行に**足す**（表の顔ぶれ9表は変えない）。

| 表 | 足す列 | 何に使うか |
|---|---|---|
| 2 `EVALUATIONS` | `request_id`、`decision_time` | 評価要求ごとの最終の結果区分（第6.3節） |
| 9 `FILLS` | `cost_commission_amount` / `cost_commission_currency`、`cost_slippage_in_price_amount` / `cost_slippage_in_price_currency`、`cost_spread_in_price_amount` / `cost_spread_in_price_currency` | 取引単位の費用と入場費用を含む取引損益（第7.3節）。区分別の列は D06 v1.2（Q12 決定）で揃っている。**区分の2列がどちらも空（`None`）なのは、その区分の費用記録が無いこと**であり（D06 §9.2）、読めない値ではない |

段階3 で足された4表（表16〜19）は **v2.0 でも読まない**。待機の数え方（第6.3節）は表2 の列だけで決まり、待機の出来事の表（表16）を開く必要が無い。

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
| 4 | `WIN_RATE` | RATIO | 勝ち取引数 ÷ 完了取引数。勝敗は `realized > 0` を `WIN`、`< 0` を `LOSS`、`= 0` を `BREAK_EVEN` とし、`BREAK_EVEN` は勝ちに数えず分母には数える。**指標集合 v2 では `realized` の代わりに `trade_profit`（入場費用込み）の符号で決める**（第7.3節、Q10 決定） | 表11 | 0取引なら `Unavailable(NO_TRADES)` | `1 ÷ 1 = 1` |
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

### 5.5 指標集合 v2（段階4）【提案】＋【合意済み】（2026-09-25 の人間の決定3）

**指標集合の版を 2 に上げる**。v2 は第5.2節の15件（#2 の定義を第7.3節で改める）に、下の4件（#16〜#19）を足した19件である。版は評価の識別子に入る（第9.2節）ので、同じ run を v1 と v2 で評価した結果は別の識別子・別の保存先になる。**実装が持つ指標集合は最新の1版だけとする**【提案】。v1 の式を並べて残すと、同じ #2 に2つの定義が並び、どちらの定義で出た値かを版だけで読み分けることになる。v1 で出した既存の成果物は保存先ごと残る。

**人間の決定3**は「追加指標（年率化・リスク調整）は少数に絞り、十進数型のまま計算する（平方根も十進数）」である。

#### 候補と選定理由【提案】

| 候補 | 採否 | 理由 |
|---|---|---|
| 単純年率化リターン | **採用（#16）** | 全体計画 §5.5.1 の「年率化」。乗算と除算だけで求まる |
| 複利年率化リターン（CAGR） | 不採用 | 非整数の指数のべき乗が要る。CPython の十進数は非整数指数のべき乗を「ほぼ常に正しく丸める」とだけ保証しており、決定論の根拠にできない |
| 年率化シャープレシオ（日次、無リスク金利 0） | **採用（#17）** | 全体計画 §5.5.1 の「リスク調整指標」の代表。平方根は十進数の `sqrt()` が正しく丸めた値を返す |
| ソルティノレシオ | 不採用（段階5） | 下方の日次観測だけを使うため、単一 run では観測数が少なく値が不安定。#17 と同じ日次系列から後で足せる |
| 年率化ボラティリティ | 不採用 | #17 の途中の値であり、独立の指標にすると同じ量が2か所に出る |
| プロフィットファクター | **採用（#18）** | 取引単位の損益（第7.3節）の分布を1つの比で表す。除算1回 |
| 平均取引損益（期待値） | **採用（#19）** | 取引1件あたりの損益。#2 ÷ #3 |
| 平均勝ち・平均負け・ペイオフレシオ | 不採用 | 取引表（`TRADES`）から導ける。指標を増やさない |
| 最大連敗数 | 不採用（段階5） | 取引の順序に依存する分布指標で、頑健性の評価（段階5）で扱う |
| 歪度・尖度 | 不採用（段階5） | 観測数が少ないと不安定で、単一 run の判断に使えない |
| カルマーレシオ | 不採用 | #16 と #6 から導ける |

#### 4件の定義【提案】

`N` は **run 区間に終わり（NY 17時）が入る取引日の数**である。取引日と休場は取引カレンダー（D03 §3.4、Q8 決定）が決め、取引日の終わりが `(run_interval.start, run_interval.end]` にあるものを数える。年率化の係数は**年 260 取引日**（週5日 × 52週）とし、指標集合 v2 の定義の一部として固定する（設定で変えない。変えると同じ run の年率化の値が設定で変わる）。

| # | `MetricId` | 種別 | 式（**この順に計算する**） | 入力 | 欠損時 | T01 検算 |
|---|---|---|---|---|---|---|
| 16 | `ANNUALIZED_RETURN` | RATIO | `(#15 × 260) ÷ N` | 表14、manifest、カレンダー | #15 が値なしなら同じ理由。`N = 0` なら `UNDEFINED_DENOMINATOR` | `N = 10`（2015-01-05〜09・12〜16）。`0.036706 × 260 ÷ 10 = 0.954356` |
| 17 | `ANNUALIZED_SHARPE_RATIO` | RATIO | 日次の資産 `E_0 = 初期残高`、`E_k` = k 番目の取引日の終わり**以前で最後**の台帳 snapshot の `equity`（無ければ `E_{k−1}` を持ち越す）。`r_k = E_k ÷ E_{k−1} − 1`（k = 1〜N）、`m = (Σ r_k) ÷ N`、`v = (Σ (r_k − m)²) ÷ (N − 1)`、`s = v.sqrt()`、**`(m ÷ s) × 260.sqrt()`** | 表14、manifest、カレンダー | `N < 2` なら `NO_OBSERVATIONS`、`s = 0` または `E_{k−1} = 0` なら `UNDEFINED_DENOMINATOR` | **T01 では検算できない**（T01 §9.4 の行#3・#5 の `equity` は範囲でしか決まっていない）。代わりに次の例で検算する: `E = 1,000,000 → 1,010,000 → 999,900 → 1,009,899` なら `r = 0.01, −0.01, 0.01`、値は `√(65/3) ≈ 4.6547466812563`（28桁の値は PR 1 の単体テストで固定） |
| 18 | `PROFIT_FACTOR` | RATIO | `(勝ち取引の trade_profit の合計) ÷ (負け取引の trade_profit の合計の絶対値)` | 表9、表11 | 0取引なら `NO_TRADES`、負け取引が無ければ `UNDEFINED_DENOMINATOR`（無限大を値にしない） | 負け取引が無いので `Unavailable(UNDEFINED_DENOMINATOR)` |
| 19 | `AVERAGE_TRADE_PROFIT` | AMOUNT | `(Σ trade_profit) ÷ 完了取引数` | 表9、表11 | 0取引なら `NO_TRADES` | `36,736 ÷ 1 = 36,736 JPY` |

- **数値の規則**【提案】（人間の決定3）: **すべて十進数（`Decimal`）で計算し、`float` を使わない**。上の式の演算の順序を固定し、各演算を D02 §4 のカーネル精度（28桁・`ROUND_HALF_EVEN`。`kernel_context()`）で行い、**最終値を丸めずに保存する**。平方根は同じコンテキストの `Decimal.sqrt()` を使う（結果は正しく丸められ、実行環境に依存しない）。**非整数の指数のべき乗は使わない**。第5.1節の「比率は除算を1回だけ」は段階2 の15件の規則であり、#16〜#19 は演算が複数回になるので、その代わりに**演算の順序を式で固定する**ことで決定論を保つ。#19 は金額だが除算を含むので、同じくカーネル精度で1回割って丸めない（第5.1節の「金額は丸めない」は加減算だけの金額の規則である）。
- **無リスク金利を 0 とする**【提案】。swap / rollover を計上していない（ADR-0029）ので、金利差を入れると計上していない収益を指標だけに入れることになる。
- **注記**（第7.2節の表に足す）: #16・#17 に `SWAP_NOT_MODELED`、#16・#18・#19 に `UNRESOLVED_INTRABAR_PRESENT`（条件は同じ）、#18・#19 に `OPEN_POSITION_EXCLUDED`（完了取引だけを数える）と `SWAP_NOT_MODELED`。
- **#17 の日次系列は含み損益込み（`equity`）**で作る【提案】。採用指標の最大ドローダウン（#5）と同じ基準列である（Q3 決定の趣旨）。

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

### 6.3 待機をはさんだ評価要求の数え方（段階4）【提案】（Q13 決定）

第6.1節の末尾で「段階4 で待機を指標に反映するときは、要求単位で数えるか記録単位で数えるかを先に決める」とした点を決める。

- **8種目の集計 `EVALUATION_REQUEST_FINAL_OUTCOME` を足す**【提案】。評価要求（表2 の `request_id`）ごとに**最後の記録**（`decision_time` の昇順、同じ時刻なら `evaluation_id` の昇順で最後のもの）の結果区分を1件と数える。鍵の語彙は `EVALUATION_OUTCOME` と同じ5語（D05 §6.4）で、0件の鍵も行として出す。**合計は評価要求の数である**。
- **既存の `EVALUATION_OUTCOME` は記録単位のまま変えない**。段階3 で確定した集計（v1.4）であり、待機に入った記録（`WAITING`）の件数はこちらで読める。2つを並べると「待機に入った記録が何件あり、その要求が最終的にどう決着したか」が読める。
- 最終の区分が `WAITING` になる要求は、**run 末尾で待機要求を決着させる規則**（D05 §6.1: 残った待機要求は `Skipped` で決着する）により、通常は0件である。0 でない行が出たら、その規則が守られていない判断履歴である（行を落とさずに出すのは第6.1節の「観測された事実を落とさない」と同じ）。
- 指標集合 v2 の19件は**この集計を使わない**。待機を指標にするか（例: 待機の決着率）は段階5 の頑健性の評価で判断する。
- T01 経路1 での値: 待機が無いので `EVALUATED=6`、他は 0（記録単位の集計と同じ）。
- 整列鍵（第8.1節）: `CategoryKind` の宣言順で7種の後ろに並ぶ。

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
| `SWAP_NOT_MODELED` | swap / rollover を計上していない（ADR-0029） | #1・#2・#10・#11・#13・#14・#15。v2.0 で #16・#17・#18・#19 |
| `PRICE_EMBEDDED_COST` | 価格に反映済みで、balance から控除していない参考値 | #11 |
| `OPEN_POSITION_EXCLUDED` | 未決済建玉の評価に依存する、または未決済建玉を含まない | #2・#7・#8・#13・#14。v2.0 で #18・#19 |
| `ENTRY_COST_EXCLUDED` | 入場側の費用を含まない（第4.2節のとおり段階2 は取引単位の費用を読まないため）。**本書 v0.2（段階4）で外す**。外すのに必要な入力は D06 v1.2 の区分別の費用列で揃っている。→ **v2.0 で外した**（指標集合 v2 では付けず、列挙からも外す。第7.3節） | #2（指標集合 v1 だけ） |
| `UNRESOLVED_INTRABAR_PRESENT` | `UNRESOLVED_SL_PRIORITY` の約定を含む（ADR-0030）。`BacktestResult.unresolved_intrabar_count > 0` のときだけ付ける | #1・#2・#4・#15。v2.0 で #16・#18・#19 |

### 7.3 取引単位の費用と、入場費用を含む取引損益（段階4）【提案】

第4.2節（v1.x）が「取引単位の費用と入場費用を含む取引損益は本書 v0.2（段階4）で扱う」とし、第7.2節の注記 `ENTRY_COST_EXCLUDED` が「本書 v0.2 で外す」とした点を決める。

- **約定1件ごとの費用を区分別に読み、取引の記録に入場側と決済側の6列を持たせる**（第3節の v2.0 の `TradeRecord`）。値は表9 の区分別の列（D06 §9.2、Q12 決定）をそのまま写す。区分の費用記録が無い約定は `None` のまま残す（0 に置き換えない。「費用が0円だった」と「費用記録が無い」を区別する D06 の規則を評価側でも保つ）。
- **取引損益 `trade_profit = realized − entry_commission`**（`entry_commission` が `None` なら 0 として引く）。建玉の確定損益（`realized`）は決済側の手数料だけを含み（T01 §2.6: `36,768 = 36,800 − 32`）、入場の手数料は約定時に残高へ計上されているので（上位 §4.7.15 C）、**入場の手数料を引けば取引1件の損益が balance の動きと一致する**。価格に反映済みの費用（滑り・提示価格の幅）は引かない（二重計上になる。第7.2節）。
- **#2 `CLOSED_TRADE_PROFIT` は指標集合 v2 で `Σ trade_profit` とする**。T01 の検算値は `36,768 → 36,736 JPY` に変わる（P1 の入場手数料 32 円）。注記 `ENTRY_COST_EXCLUDED` は付けない（列挙からも外す）。#1 `NET_PROFIT`（`36,706`）との差は `−30 JPY` になり、これは**未決済の建玉 P2 の入場手数料**である（未決済建玉の費用は完了取引に入らない。#2 の注記 `OPEN_POSITION_EXCLUDED` がこれを示す）。
- **勝敗（`outcome`）は `trade_profit` の符号で決める**【提案】（Q10 決定）。勝率（#4）・プロフィットファクター（#18）・平均取引損益（#19）が同じ損益の定義に揃う。T01 の P1 は `WIN` のまま。
- 読む列が増えた分、**致命の整合検査 C8（通貨）**は足した費用の列の通貨も口座通貨と比べる。
- T01 経路1 の取引の記録（v2 で足す列）:

| 列 | 値 | 出どころ |
|---|---|---|
| `entry_commission` / `entry_slippage_in_price` / `entry_spread_in_price` | `32` / `320` / `640 JPY` | 入場約定 `FillId 00000001` の費用（`0.001 × 32000`、`0.010 × 32000`、`0.020 × 32000`） |
| `close_commission` / `close_slippage_in_price` / `close_spread_in_price` | `32` / `320 JPY` / `None` | 決済約定 `FillId 00000002`（売りは bid 基準なので提示価格の幅の記録が無い。T01 §2.6） |
| `trade_profit` | `36,768 − 32 = 36,736 JPY` | 上の式 |

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
- **保存先が既にあれば、何も書かずに失敗する**【確定】（v2.3。成果物の書き込みを統一する根本対処 R4、R1-D07-3）。`write_evaluation` は5表と評価 manifest を書く前に、保存先を**新しく作ること自体で**確かめる（`fs_store.create_artifact_directory`。run の保存先と同じ書き込み前検査。D06 §9.1）。空のディレクトリも書きかけのディレクトリも「ある」と数える。`runs/` から保存先の親まで（`<run_id>`・`eval`）がシンボリックリンクや別の種類のものなら、リンク先に書かずに失敗する。失敗は**型付きの構造エラー `ArtifactAlreadyExists`**（`evaluation.domain.errors`）で、CLI は1行の失敗として表示し終了コード 1 で終える。**置換の指示は持たない**（置換を許すのは run の成果物だけ。ADR-0006）。同じ識別子で評価し直したいときは、人間が先にそのディレクトリを移動または削除する。識別子が違う評価（評価コード・指標集合の版・カレンダーが違う）は別の保存先なので、並べて書ける。run を置換したときは評価の成果物も畳まれる（D06 §9.3）。

### 8.3 評価 manifest（JSON）【提案】

| 群 | 項目 |
|---|---|
| 識別 | `run_evaluation_id`、`run_id`、`run_manifest_ref`、`metric_set_version`、`calendar_ref`（v2.0。第9.2節） |
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
- **v2.0（段階4）: `RunEvaluationId = digest(run_id, metric_set_version, evaluation_code_digest, calendar_ref)` に改める**【提案】（第1.2節の例外 (e)。Q8 決定の帰結）。`calendar_ref` は評価が**受け取った**取引カレンダーの識別と版 `(id, version)` である（run manifest の `calendar_ref` ではない）。Q8 決定でカレンダーが評価の入力になり、C10 の合否と #16・#17 の値がカレンダーで変わるので、算出元に入れないと、同じ run を違うカレンダーで評価した2つの結果（一方は C10 不合格で `FAILED`）が同じ識別子・同じ保存先になり、第19.6節の手順2 が `FAILED` の評価を再利用しうる。カレンダーを変えたら版を上げる規則（D03 §3.4、主キーは D03 §7.4 の `(id, version)`）があるので、識別と版で内容を指せる。評価 manifest にも `calendar_ref` を書く（第8.3節）。
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
| `run_evaluation_id`（`RunEvaluationId`） | `EvaluateRun` が `digest(run_id, metric_set_version, evaluation_code_digest)` で計算（第9.2節）。v2.0 では算出元に受け取ったカレンダーの `calendar_ref` を足す（第9.2節） | `EvaluationManifest.run_evaluation_id` → `ResultRepository.write_evaluation` | 評価 manifest と保存先のディレクトリ名（第8.2節） | 保存先 `runs/<run_id>/eval/<run_evaluation_id>/` そのもの |
| `run_status`・`run_failure_reason` | 実行（D06 §9.3） | `BacktestResult.status` が状態 `REJECTED` を決め（第10.1節）、値は `RunManifest` から写す（第8.3節） | 評価 manifest | 評価 manifest は保存先1か所に1件 |
| 口座通貨（`CurrencyCode`） | 実行設定の口座（`RunManifest.account.currency`、D06 §9.3） | 致命検査 C8 の期待値（第10.2節）。評価 manifest へ写す | 評価 manifest の `account_currency` | 同上 |
| `swap_modeled`（`bool`） | 実行（`BacktestResult.swap_modeled`、D06 §9.4） | そのまま写す（第7.2節） | 評価 manifest（必須項目。第8.3節） | 同上 |
| 処理点（`ProcessingPoint`。時刻・フェーズ・通し番号の3列） | エンジン（D02 §3.3、D06） | trace の3列（表3 `at_*`、表9 `processed_at_*`、表11 `opened_at_*`、表14 `at_*`）→ `TradeRecord.entry_at` / `exit_at`（第4.2節【確定】） | `TRADES` の `entry_at` / `exit_at`（3列に平坦化。第8.2節） | `TRADES` の主キーは `position_id`（v2.0。R1-D07-5 の決定。下の段落）。整列鍵は `(entry_at, position_id)`（第8.1節） |
| 建玉・取引機会・約定の識別子（`position_id`・`opportunity_id`・`entry_fill_id`・`close_fill_id`） | 採番は D06（建玉・約定）と D05（取引機会） | ID 連鎖（表11 → 表9 → 表7 → 表4）を辿って `TradeRecord` に入れる。辿れなければ致命検査 C4 の不合格（第8.1節） | `TRADES` | 主キー `position_id`（同上） |
| 約定の診断の識別子（`fill_id`・`order_id`・`position_id`） | D06 | 表9・表7 → `FillDiagnostic`（第6.2節） | `FILL_DIAGNOSTICS` | 主キー `fill_id`（同上。整列鍵と同じ。第8.1節） |
| `result_digest`（`ContentDigest`） | `EvaluateRun` が5表の全行を整列鍵で並べ、D02 §9.3 の正規化エンコードで計算（第9.2節） | `EvaluationManifest.result_digest` | 評価 manifest | 評価 manifest は保存先1か所に1件 |
| 読んだ表の一覧 `input_tables` | 第4.2節の9表の読み出し（`ResultRepository.read_table`、第4.3節） | `EvaluateRun` → `EvaluationManifest` | 評価 manifest の `input_tables`（第8.3節） | 評価 manifest は保存先1か所に1件 |
| 整合検査の期待値（`BacktestResult.trade_count`・`opportunity_count`・`summaries.realized`、口座の `initial_balance`） | 実行（D06 §9.4・§9.3） | 整合検査 C3・C5・C6 の期待値として読む（第10.2節） | `CONSISTENCY_CHECKS` の `expected` / `observed` 列（第3節の `ConsistencyCheckResult`・第8.1節）。値そのものは評価 manifest へ写さない | `CONSISTENCY_CHECKS` の主キーは `check`（同上） |
| 評価の状態と不合格件数（`status`・`fatal_failure_count`・`warning_failure_count`） | `EvaluateRun` が整合検査の結果と入力の run の状態から決める（第10.1節・第10.2節） | `EvaluationReport` → `EvaluationManifest` | 評価 manifest の「状態」の群（第8.3節） | 評価 manifest は保存先1か所に1件 |

**空欄だったマスの決定**（v1.5 の R1 が残した空欄。v2.0 で本書が埋めた。2026-09-25 の人間の決定で本 PR の担当になった）:

- **R1-D07-5 評価5表の主キー**【提案】: `METRICS` は `metric_id`、`CATEGORY_COUNTS` は `(category, key)`、`TRADES` は `position_id`、`FILL_DIAGNOSTICS` は `fill_id`、`CONSISTENCY_CHECKS` は `check`。いずれも**行を作る元の表の主キー、または本書の語彙から1対1に決まる**（`TRADES` は表11 の1行＝1建玉から1行、`FILL_DIAGNOSTICS` は表9 の1行から1行。1建玉が完了取引1件なのは段階2〜4 の1建玉・全量決済の前提による。分割決済は段階6・D10）。
  - **入力の表の主キーの重複**（表11 の `position_id`、表9 の `fill_id`、表7 の `order_id`、表4 の `attempt_id`）は、致命の整合検査 **C12 `input_keys_unique`** で見つけて不合格にし、どちらかの行を黙って採らない（後勝ちにしない）。重複の件数と最初の鍵を `observed` に残す（第10.4節）。
  - **出力の5表で主キーが重複したら実装の誤り**である（入力の主キーが一意なら出力も一意になるため）。構造エラーとして例外にする（D01 §2.2 規則5）。
  - 整列鍵（第8.1節）は並びの規則、主キーは一意性の規則であり、`TRADES` だけ両者が違う（整列鍵は時刻順に読むため `entry_at` を先に置く）。

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
| **通常の完了**: 入力の run が正常完走し（`BacktestResult.status == COMPLETED`）、完了取引が1件以上あり、致命の整合検査がすべて合格した | **遷移**: 段階 B へ進む。状態はまだ決めない（第4.1節・第10.1節） | **遷移**: 13件の整合検査（C1〜C13）をすべて実施して結果を残し、段階 C へ進む（第10.2節・第10.4節。v2.0 で C9〜C12、v2.2 で C13 を足した） | **遷移**: 指標（指標集合 v2 では19件。第5.5節）・集計・診断を算出し、状態を `COMPLETED` に決める（第5節・第6節・第10.1節） | **遷移**: 5表と評価 manifest をすべて書く（`status=COMPLETED`。第8.1節〜第8.3節・第10.1節）。`result_digest` を計算して manifest に入れる（第9.2節） |
| 入力の run が正常完走していない（`BacktestResult.status != COMPLETED`） | **遷移**: 状態を `REJECTED` に決め、段階 B へ進む（第10.1節・第4.4節） | **変化なし**: 末尾の集計と比べる C5 だけを行わず、残る12件（C1〜C4・C6〜C13）を実施して全件を残す（第10.1節【確定】・第10.4節） | **到達しない**: `REJECTED` では指標を算出しない（第10.1節、Q6） | **遷移**: `REJECTED` として `CONSISTENCY_CHECKS` と manifest（`run_status` / `run_failure_reason`）を書き、残る4表を0行で書く（第10.1節・第8.1節） |
| 表または必須列が無い（C1 の不合格） | **到達しない**: 段階 A は表を読まない（表は段階 B で読む。第4.1節） | **遷移**: 例外にせず致命の不合格として記録する（第4.3節）。run が正常完走していれば状態は `FAILED`（第10.1節）。run が正常完走していないときの状態は `REJECTED`（R1-D07-1 の決定。下の段落） | **到達しない**: 致命の不合格が1件でもあれば算出しない（第10.1節） | **遷移**: `CONSISTENCY_CHECKS` に全件、残る4表を0行で書く（第10.1節・第8.1節） |
| 他の致命検査の不合格（C2〜C5・C8・C10・C12・C13） | **到達しない**: 整合検査は段階 B で行う（第10.2節） | **遷移**: 状態は `FAILED`（第10.1節）。run が正常完走していないときの状態は `REJECTED`（R1-D07-1 の決定。C5 はそのとき実施しない） | **到達しない**: 同上（第10.1節） | **遷移**: 同上（第10.1節・第8.1節） |
| 警告検査の不合格（C6・C7） | **到達しない**: 整合検査は段階 B で行う | **変化なし**: 警告として残して続行する。C7 は本書の整列鍵で並べ替えて続行する（第10.2節） | **変化なし**: 算出する。状態を `FAILED` にするのは致命の不合格だけである（第10.1節） | **変化なし**: `warning_failure_count` に数えて書く（第8.3節） |
| 完了取引が0件 | **到達しない**: 取引の件数は段階 B 以降で読む | **変化なし**: 0件で状態を変える規則は無い（第10.1節「0取引は失敗ではない」） | **変化なし**: 状態は `COMPLETED` のまま、取引に依存する指標を `Unavailable(NO_TRADES)` にする（第10.1節・第10.3節） | **変化なし**: 5表をすべて書く（`TRADES` は0行。第8.1節） |
| 列はあるが値を解釈できない（null・型の不一致） | **到達しない**: 段階 A は表を読まない | **遷移**: 例外にせず、致命の検査 C9 を不合格、その列を読む他の検査を `UNREADABLE` として全件残す（第10.4節）。状態は `FAILED`（run が正常完走していなければ `REJECTED`。R1-D07-1） | **到達しない**: 致命の検査が合格でなければ算出しない（第10.1節） | **遷移**: `CONSISTENCY_CHECKS` に全件、残る4表を0行で書く。manifest に `unreadable_check_count` を書く（第10.4節） |
| 保存先（`runs/<run_id>/eval/<run_evaluation_id>/`）に成果物が既にある | **到達しない**: 保存先に触れるのは段階 D だけ（第8.2節） | **到達しない**: 同上 | **到達しない**: 同上 | **拒否**: 5表も評価 manifest も書かずに `ArtifactAlreadyExists` で失敗する。評価の結果は保存しない。置換の指示は無い【確定】（v2.3、R1-D07-3。根本対処 R4。第8.2節） |
| run manifest を読めない（`read_manifest` の失敗） | **遷移**: 例外にせず「読めなかった」として受け取り（`ManifestReadFailure`）、段階 B へ進む（R1-D07-4 の決定。下の段落）。状態はまだ決めない | **遷移**: 致命の検査 C11 `run_manifest_readable` を不合格とし、manifest の値を使う検査（C2 の manifest 側・C5・C8・C10・C13。処理点の順位を manifest から引く C7 も。v2.2）を `UNREADABLE`、manifest を使わない検査は実施して全件残す。状態は `FAILED`（`BacktestResult.status` が正常完走でなければ `REJECTED`。R1-D07-1） | **到達しない**: 致命の検査が合格でなければ算出しない | **遷移**: `CONSISTENCY_CHECKS` に全件、残る4表を0行で書く。評価 manifest の run 由来の項目（`run_manifest_ref`・`run_code_digest`・`run_status`・`run_failure_reason`・`account_currency`）は `None` で書く |
| 探索の中断 | **到達しない**: `ABORTED` は段階5・D09 で使う（第10.1節、第1.2節の行7） | **到達しない**: 同左 | **到達しない**: 同左 | **到達しない**: 同左 |

**空欄だったマスの決定**（v2.0。2026-09-25 の人間の決定で、R1-D07-1・2・4 は本 PR、R1-D07-3 は根本対処 R4 の担当になった（v2.3 で決定）。下の起草時の問いの後ろに決定を書く）:

- **R1-D07-1**: run が正常完走していない（`REJECTED` の条件）うえに致命の整合検査が不合格のとき、状態を `REJECTED` と `FAILED` のどちらにするか。第10.1節は2つを別の条件で定義し、両方が成り立つときの優先を書いていない（出力の形は「`CONSISTENCY_CHECKS` と manifest、残る4表は0行」で同じだが、manifest の `status` が変わる）。→ **決定（v2.0）【提案】: `REJECTED` を優先する**。`REJECTED` は段階 A で入力だけから決まり、原因（run が正常完走していない）が先にある。致命の不合格は `fatal_failure_count` と `CONSISTENCY_CHECKS` に全件残るので、両方の事実は失われない。段階2 の実装（`evaluate_run.py`）も既にこの順で判定しており、振る舞いは変わらない。
- **R1-D07-2**: 列はあるが値の解釈（`Decimal` 化・時刻化・enum 化、null）に失敗したときの扱い。第4.3節は表・列の欠落だけを C1 へ寄せており、値の解釈失敗を書いていない。`AGENTS.md` は現在の実装の扱い（検査へ寄せる）を仮置きとし、承認済み・未実施の根本対処 R5（第10.2節の改訂）で定めるとしている。→ **決定（v2.0）: 第10.4節**（C9 `all_values_readable` と `UNREADABLE` の区分）。
- **R1-D07-3**: 保存先に評価の成果物が既にあるときの扱い（失敗か置換か）。第8.2節は識別子ごとに保存先を分けることだけを定める。起草時には承認済み・未実施だった根本対処 R4（v2.3 で実施済み。成果物の書き込みを「存在すれば失敗」に統一する）の対象である。→ **決定（v2.3、R4）【確定】: 存在すれば、何も書かずに失敗する**（暫定 snapshot の R1-D03-1、run の成果物の R1-D06-2 と同じ規則）。評価は置換の指示を持たない（第8.2節）。第19.6節の再利用の規則をこれに合わせた（再利用の判断は書き込みの前に行い、書き込みの失敗を既存の成果物の検出に使わない）。
- **R1-D07-4**: run manifest を読めない（ファイルが無い・壊れている）ときの扱い。第4.3節の「例外にせず致命の不合格として残す」は trace の表の読み出しだけを定め、`read_manifest`（第4.1節の入力2）には触れていない。→ **決定（v2.0）【提案】: 例外にせず、致命の検査 C11 `run_manifest_readable` の不合格として残す**（第10.4節）。評価は「なぜ評価できなかったか」を残すのが仕事であり（第4.3節）、根本対処 R5 の「例外で検査を抜ける経路を無くす」と同じ扱いにする。`ResultRepository.read_manifest` は `RunManifest | ManifestReadFailure` を返し、評価 manifest の run 由来の項目は `None` を許す（第3節の v2.0 の表）。**不採用**: 構造エラーとして例外にする案（評価の成果物が何も残らず、run のディレクトリが壊れていることを結果から説明できない）。

### 10.2 整合検査（8件）【提案】

（v2.0: 検査の結果を3区分にし、C9〜C12 を足して12件にした。v2.2 で C13 を足して13件。第10.4節）

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

### 10.4 検査結果の3区分と「値が読めない」ことの扱い（段階4）【提案】（AGENTS.md の後続対処 R5）

2026-09-24 に人間が承認した根本対処 R5（「観測値として残す」を検査結果の型の規則にする）のうち、**整合検査の側**を本節で決める。能力検査の側（`DataCapabilityReport`、D06 §3・§10.5）は D06 の改訂として別の PR で行い、本書はその報告を読むだけで再検査しない（第4.4節）。

- **検査の結果を3区分にする**: `CheckOutcome` = `PASSED`（合格）/ `FAILED`（不合格）/ `UNREADABLE`（**検査に要る値が読めず、検査を実施できなかった**）。`ConsistencyCheckResult.passed: bool` を `outcome` に置き換える（評価の表 `CONSISTENCY_CHECKS` の列も `passed` から `outcome` へ）。**2区分のままだと、読めなかった検査を「合格」か「不合格」のどちらかに寄せるしかなく**、合格に寄せれば見逃し、不合格に寄せれば「検査して食い違いを見つけた」と誤って説明する。
- **読めない値**とは、保存された判断履歴の値のうち、設計上いつも埋まっているはずの列が空、列挙の語彙に無い、十進数・時刻として解釈できない、のいずれかである。**列や表が無いこと**は従来どおり C1 の不合格であり、読めない値とは区別する（第4.3節の区別を保つ）。
- **致命の検査 C9 `all_values_readable` を足す**。読む列（第4.2節）のすべての値を解釈し、読めない値が1つも無ければ合格、あれば不合格とする。`observed` は読めない値の件数と、整列鍵の順で最初の1件（`表.列[主キー] = 元の文字列`）である。**評価はこの検査の不合格で `FAILED` になり、指標と集計を出さない**（第10.1節の「致命の検査が1件でも不合格なら指標を出さない」の適用。読めない値の上に作った数値を出さない）。
- **読めない値のある列を読む他の検査は `UNREADABLE` とする**。読めない値の無い列だけで実施できる検査は実施する。例外で検査を抜けることはしない。どの検査がどの表を読むかは下表のとおり。
- **例外にしてよいもの**は、実装の誤り（構造エラー）だけである（D01 §2.2 規則5）。保存済みの成果物の値が読めないことは入力の欠陥であり、観測値として残す。**段階2 の実装が置いた仮置き**（値が読めないことを C1 の不合格へ寄せる、`evaluate_run.py`）は、本節の C9 と `UNREADABLE` に置き換える（PR 1）。C1 は起草時の意味（9表があり、必須列が欠けていない）に戻る。
- 評価 manifest に `unreadable_check_count` を足し、`fatal_failure_count` / `warning_failure_count` は**水準ごとの `PASSED` でない検査の件数**（`FAILED` と `UNREADABLE` の両方）とする。

| # | `check` | 水準 | 読む表・値 |
|---|---|---|---|
| C1 | `required_columns_present` | FATAL | 9表の有無と列の有無（値は読まない） |
| C2 | `run_id_consistent` | FATAL | 9表の `run_id` |
| C3 | `trade_count_matches` | FATAL | 表11 |
| C4 | `id_chain_complete` | FATAL | 表11・表9・表7・表4 |
| C5 | `realized_matches_balance` | FATAL | 表14、`summaries` |
| C6 | `opportunity_count_matches` | WARNING | 表3 |
| C7 | `snapshot_order_monotonic` | WARNING | 表14 |
| C8 | `single_account_currency` | FATAL | すべての `Money` 列（第7.3節で足した費用の列を含む） |
| C9 | `all_values_readable` | FATAL | 読む列すべて（v2.0 で新設） |
| C10 | `calendar_matches_run` | FATAL | 受け取ったカレンダーの識別と run manifest の `calendar_ref`（v2.0 で新設。Q8 決定） |
| C11 | `run_manifest_readable` | FATAL | run manifest が読めること（v2.0 で新設。第10.1.1節の R1-D07-4） |
| C12 | `input_keys_unique` | FATAL | 表11 の `position_id`・表9 の `fill_id`・表7 の `order_id`・表4 の `attempt_id` に重複が無い（v2.0 で新設。第9.3節の R1-D07-5）。**v2.2: 読む9表すべての主キー**に広げる（第10.5節の1） |
| C13 | `run_status_consistent` | FATAL | `BacktestResult.status` と run manifest の `status` が一致し、失敗理由の有無が状態と合う（正常完走なら無く、そうでなければ有る。D06 §9.3・§9.4）。2つの保存済み成果物の食い違いは例外にせず不合格として残す（v2.2 で新設。2026-09-25 の人間の決定）。不合格のとき評価 manifest の `run_failure_reason` は `None` で書く（食い違う片方を写さない） |

**拒否（`REJECTED`）の run** では、段階2 の決定どおり C5 だけを実施しない（第10.1節）。C9〜C13 は判断履歴と manifest だけで実施できるので実施する。第8.1節の `CONSISTENCY_CHECKS` の整列鍵は C1〜C13 の宣言順になる。

### 10.5 実装 PR 1 で確定した規則【確定】（v2.2、2026-09-25 の人間の決定。PR #44 の仮置き6件）

1. **主キーの重複（C12）は読む9表すべてで見る**。第9.3節の4表に絞ると、同じ処理点に残高の違う台帳 snapshot が2行あっても検出されず、行の並びで最終残高が変わるため（段階2 の実装の扱いを保つ）。
2. **取引日の境界が1つに決まらないカレンダー**（週の開始と終了の時刻が違う。D03 §3.4.1 の `trading_day_boundary` が `None`）では、#16・#17 を `Unavailable(INPUT_NOT_AVAILABLE)` とする。
3. **#16・#17 の観測件数（`observation_count`）は取引日の数 `N`** とする。
4. **読めない値の列ごとの規則**: 設計上いつも埋まる列が空なら読めない値。条件付きで埋まる列は、その区分の行（終端への遷移の終端理由、拒否した試行の拒否理由、受け付けた試行の注文、入場要求の取引機会・決済要求の建玉、決済注文の決済契機と建玉、入場注文の参照価格、見送り・待機の診断（要素0件も空と読む））で空なら読めない値。2列で1つの値（費用の区分ごとの金額と通貨、参照価格と観測時刻、確定損益の金額と通貨）は片方だけ空なら読めない値。決済済みの建玉の決済約定と確定損益の欠けは C3・C4 が見る。数値の列は固定小数・カーネル精度（28桁）以内の十進文字列でなければ読めない値（D06 §9.1）。
5. **run manifest が読めないとき、評価 manifest の `run_status` は `None`** で書く（第10.1.1節の R1-D07-4 の文言どおり。`BacktestResult.status` からは埋めない）。
6. **語彙に無い列挙値を読めない値とするのは、値を型に直して計算に使う列だけ**（発注要求の種別、試行の結果、注文と建玉の売買方向、注文条件の種別、決済契機、建玉の状態）。集計の鍵としてだけ使う列（結果区分・理由コード・足内競合の解決方法・取引機会の状態）には語彙の検査をかけず、第6.1節の「語彙に無い鍵は行として残す」【確定】を優先する。

## 11. 段階2の最小範囲と検証【提案】

- 範囲は「検証戦略 A の単一 run の結果を、再現可能に・数値で・swap 未計上と明記して出せること」である。指標15件・集計7種・診断3項目・検査8件がその最小集合であり、これ以外を段階2で作らない。
- 全体計画 §8.2 の段階2の完了条件「人工データで注文・数量・損益・資産が手計算に一致」に対し、本書は**第5.2節の T01 検算の列**で対応する。T01 の経路1・第9節・第9.4節の数値から、**指標15件のすべてが手で確かめられる**（v1.1 で最大ドローダウン2件が加わった。第5.4節）。
- **テスト**【提案】: 単体（各指標の式、0取引・分母0・値なしの分岐、勝敗の3区分）、意味論（`REJECTED` の run で指標を出さない、致命検査の不合格で指標を出さず検査表だけを出す、必須列が欠けた表と0行の表を区別する、0件の鍵が行として出る、通貨違いを拒否する）、プロパティ（同じ入力で2回評価して `result_digest` が一致する、表の行の入力順を入れ替えても結果が変わらない）、golden（T01 経路1 の1取引分の5表を固定する。D06 の golden trace（全体計画 §8.4）の出力をそのまま入力にする）。

## 12. 対象外（段階4以降）

本節は**時期**の線引き（段階2で作らないもの）であり、第1.2節は**担当**の線引き（本書 v1.0 が決めないもの）である。

- 年率化、リスク調整指標、リターンの分布に関する指標 → 段階4・本書 v0.2。厳密な `Decimal` では求まらない演算（平方根・べき）を含むため、数値の型と再現性の規則を別に決める必要がある。→ **v2.0 で決めた**（第5.5節。4件を十進数のまま。分布指標は段階5）。
- 取引単位の費用の内訳と、入場費用を含む取引損益 → 本書 v0.2・段階4。読むための列は D06 v1.2（Q12 決定）で揃っており、段階2 では指標を増やさないため読まない（第4.2節）。→ **v2.0 で決めた**（第7.3節）。
- 実験 manifest（仮説・探索計画・分割・評価規則・環境）と、別プロセスでの再現手順 → 段階4・本書 v0.2。→ **v2.0 で決めた**（第19節・第21節。探索計画と分割は段階5 まで `NONE` だけ）。
- `ResearchPolicy` v1 の内容・検査・複雑性の計測方法 → 段階4・本書 v0.2。→ **v2.0 で決めた**（第20節。人間の決定5 で上限も置く）。
- `holdout_gate`（`AsOfView` の `allowed_partitions` を決める）と閲覧履歴 → 段階4・本書 v0.2（許可の判定）、段階5・D09（探索経路からの分離）。→ **v2.0 で許可の判定も段階5・D09 へ移した**（第24節。人間の決定2）。
- 人間向けレポートの文面・体裁・図 → 段階4・`adapters.report`。→ **v2.0 で決めた**（第22節。図は作らない）。
- 遅延シナリオ別の比較、執行粒度別の比較 → 段階3・D08（意味論テスト）と段階5・D09（複数 run の比較）。
- 複数 run の集約・選定・分割・探索履歴 → 段階5・D09。
- swap を計上した指標 → ADR-0029 の改訂を要する別決定。
- **v2.0 でも作らないもの**: 分布指標・下方リスク指標（第5.5節の不採用の行）、別の機械での再現（第21.5節）、使用済み期間（`CONSUMED`）の opt-in 読み（第24節）、検証戦略 B の実データでの実行（人間の決定6 の範囲外）。

## 13. 上位文書との差異

1. **段階2の指標から年率化・リスク調整指標を外した**。全体計画 §5.5.1 は `domain.metrics` に「リターン、MTM ドローダウン、リスク調整指標、年率化、取引数、exposure、費用集計」を挙げているが、リスク調整指標と年率化は厳密な `Decimal` で求まらない演算を含み、段階2の完了条件（手計算との一致）にも要らない。第12節のとおり段階4へ送る。全体計画 §6 D-2 の「D07 の将来機能をすべて書き切る必要はない」に沿った範囲の取り方である。
2. **完了取引の損益合計（#2）が純損益（#1）と一致しない**ことを、差分ではなく注記（`ENTRY_COST_EXCLUDED`）で表した。上位 §4.7.15 C は入場費用を約定時に balance へ計上する形にしており、`Position.realized` は決済側の費用しか含まない（T01 §2.6: `36,768 = 36,800 − 32`）。段階2では取引単位の入場費用を読めない（第4.2節）ため、差の 62 円を指標として出さず、注記で示す。
3. **単一 run の評価では遅延シナリオ別の診断を出さない**。上位 §4.7.12 と全体計画 §5.5.1 は「遅延シナリオ別の…診断」を求めているが、1つの run は `delay_scenario_ref` を1件しか持たない（D06 §9.3）。本書は1 run 分をシナリオ別に並べられる形で残すところまでを担い、比較は段階3・D08 と段階5・D09 の仕事とする（第6.2節）。

4. **研究ポリシーに複雑性の上限を置く**（v2.0、人間の決定5）。上位設計書 §6 と全体計画 §5.5.1 は「初期には数値制限を増やさない」「初期に上限は設けない（計測方法を定義する）」としているが、人間は計測だけでなく上限による検査を選んだ（第20節）。上位文書の「共通の研究ポリシーを1種類だけ運用する」「将来複数へ拡張できる参照構造」は守る。上位文書の文言の改訂は、上位文書の次の改訂へ渡す（第25節）。
5. **封印期間の許可判定（`holdout_gate`）を本書ではなく D09 に置く**（v2.0、人間の決定2）。ADR-0014 の「影響」と D03 §3.8・§6.1 は gate の実装を D07 としていた。決定の内容（fail-closed の手順・直列化）は変えず、担当と時期だけを段階5 へ移した（第24節）。
6. **段階2 で確定した5点を段階4 で改めた**（第1.2節の例外）。(a) #2 に入場費用を含め、注記 `ENTRY_COST_EXCLUDED` を外した（第7.3節。上の差異2 はこれで解消し、#1 と #2 の差は未決済建玉の入場費用だけになる）、(b) 整合検査の結果を3区分にし C9〜C12 を足した（第10.4節）、(c) 上の5、(d) 勝敗の基準を `realized` から `trade_profit` へ改めた（第5.2節の #4・第7.3節。Q10 決定）、(e) `RunEvaluationId` の算出元に取引カレンダーの識別と版（`calendar_ref`）を足した（第9.2節。Q8 決定の帰結）。

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
| 封印期間の許可判定（v2.0） | `holdout_gate` の許可発行・消費記録・公開の順序、`CONSUMED` の opt-in と run manifest への記録、holdout 成績としての集計の拒否（ADR-0014）。人間の決定2 で本書から移した（第24節） |
| 実験の記録票と研究ポリシーの拡張（v2.0） | 本書の記録票（第19節）の `search_plan` / `split` に語彙を足すこと、探索の試行ごとの記録票と結末記録の関係、複雑性の上限（Q7 決定）を探索の前に見直すこと |

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

### 16.3 段階4 の人間の決定（2026-09-25、6件）

段階4 の設計に先立ち、司令塔が選択式で出した6件に人間が回答した。**決定5 だけは推奨案（複雑性は計測のみ）ではなく、上限を置く案が選ばれた**。

| # | 決めたこと | 決定 | 反映先 |
|---|---|---|---|
| 1 | 段階4 の PR の分け方 | **案A**: 設計1本（本改訂）＋実装4本（評価の中身 / 書式 v2 / 記録票・研究ポリシー・再現 / レポート・実データ・受入） | 第17.2節 |
| 2 | 封印期間（2024〜2025）の許可判定を段階4 で作るか | **段階5（D09）へ送る**。旧基盤の閲覧履歴が0件で、未観測の封印期間が存在しない | 第24節、第2節、第12節、第14節、D03 v1.12、ADR-0014 の改訂履歴 |
| 3 | 追加指標の範囲と数値の型 | **少数に絞り、十進数のまま計算する（平方根も十進数）** | 第5.5節 |
| 4 | 記録票と実験設定の関係 | **書式を v2 に上げ、記録票はその解決済み内容を保存したもの**。v2 は検証戦略 B・遅延シナリオ・承認済み snapshot を書ける | 第18節・第19節 |
| 5 | 研究ポリシー v1 の強さ | **事前固定（ダイジェスト）を検査し、複雑性にも上限を置いて検査する**（推奨の「計測のみ」ではない） | 第20節、第13節の差異4 |
| 6 | 実データでの実行の範囲 | **検証戦略 A・USDJPY・2016〜2023（研究履歴）で1本。CI は人工データのまま** | 第23節（承認済みの D06 §10.5 との両立は Q9 で決定: 2018 年の1本を足す） |

決定に含まれない設計判断は、第26節に要決定 Q7〜Q13 として挙げ、同日に人間が決定した（第26節の表の「決定」列）。

## 17. 段階4 の全体像と実装の分割【提案】＋【合意済み】（2026-09-25 の人間の決定1）

第17節〜第27節は **v2.0（段階4「単一実行評価の整備」）で足した節**である。段階2 で確定した第1〜16節の番号は動かさず、段階4 の改訂が既存の節に及ぶところは、その節の中に v2.0 の段落として書いた（第4.2節・第5.5節・第6.3節・第7.3節・第10.4節）。

### 17.1 段階4 の完了条件と、それを満たす節【提案】

全体計画書 §8.2 の段階4 の行（作るもの: 実験 manifest、指標定義の確定、診断、研究ポリシー検査、結果保存、許可された実データでの実行。完了条件: 結果から設定と入力を特定でき、別プロセスで再現可能。失敗 / 0取引も説明できる）を、本書の節とテストの層に割り当てる。層の選び方は D08 §2.2、段階4 の受入テストの規約は D08 v1.9 §2.3 に置く。

| 完了条件・作るもの | 満たす節 | 示すテスト（D08 v1.9 §2.3） |
|---|---|---|
| 結果から設定と入力を特定できる | 第19節（記録票が解決済みの設定の本文とダイジェストを持つ） | 受入 |
| 別プロセスで再現可能 | 第21節（記録票と結末記録だけを入力にした再現コマンドと判定） | 受入（`subprocess` で別プロセスを起こす） |
| 失敗 / 0取引も説明できる | 第10節（評価の状態と値なしの理由）、第10.4節（読めなかった検査）、第20節（ポリシーで止めた理由）、第22節（レポートの先頭に出す） | 受入（失敗・0取引・ポリシー違反の3経路） |
| 研究ポリシー検査 | 第20節 | 単体（各検査の合否）＋受入（違反で run しないこと） |
| 指標定義の確定・診断 | 第5.5節・第6.3節・第7.3節 | 単体・golden（指標集合 v2 の固定出力） |
| 結果保存 | 第8節・第19節 | 統合・受入 |
| 許可された実データでの実行 | 第23節 | 実データ用のマーカー付きテスト（CI では実行しない）と実行記録の文書 |

**段階4 で足す意味論の行**【提案】。第11節の意味論5行（段階2）に次の5行を足し、D08 §7.2 の対応表の #6〜#10 とする。既存の5行は意味を変えない（#2 の「致命検査」には v2.0 の C9〜C12 と v2.2 の C13 も入り、#5 の「通貨違い」には第7.3節の費用の列も入る）。

| # | 意味論の行（v2.0） | 節 | 実装する PR |
|---|---|---|---|
| 6 | 読めない値があると C9 が不合格、その列を読む検査は `UNREADABLE` になり、例外にならず指標を出さない | 第10.4節 | PR 1 |
| 7 | 待機をはさんだ評価要求は、要求単位の集計では1件と数える（記録単位の集計は2件のまま） | 第6.3節 | PR 1 |
| 8 | 記録票は run より前に保存され、同じ版で内容が違えば記録票を書き換えず run もしない | 第19.3節・第19.4節 | PR 3 |
| 9 | 研究ポリシーの事前検査に1件でも合格しないものがあれば run しない（結末記録 `REJECTED_BY_POLICY`。複雑性の上限を1つ超えた場合を含む） | 第20.3節 | PR 3 |
| 10 | 研究履歴以外の partition が許可集合に入っていれば run しない（検査 P2） | 第20.3節 | PR 3 |

### 17.2 実装 PR の分割と各 PR の設計節【合意済み】（人間の決定1: 案A）

段階4 は**設計 1 本（本改訂）＋実装 4 本**で進める。下の層から上へ1層ずつ積み、各 PR はその PR の設計節だけを実装する。

| PR | 作るもの | 実装する設計節 | 主なテスト | 依存 |
|---|---|---|---|---|
| 0（本 PR） | D07 v2.0、D08 v1.9、全体計画・D03・D04・ADR-0014 の追随 | 全体 | なし（文書のみ） | ― |
| 1 評価の中身 | 追加指標4件、取引単位の費用と入場費用込みの取引損益、要求単位の集計、整合検査の3区分、指標集合 v2、取引カレンダーの入力（評価の識別子の算出元に含める）、評価の golden の更新 | 第3節の v2.0 行、第4.2節、第5.5節、第6.3節、第7.3節、第9.2節の v2.0 項目、第10.4節 | 単体（各式・値なしの分岐・演算の順序）、プロパティ（行順で結果不変）、golden（期待値の更新は独立コミット。D08 §10.3） | 0 |
| 2 実験設定の書式 v2 | `app.config` の書式 v2（遅延シナリオ・入場方針・有効性束縛・仮説・研究ポリシー参照）、v1 の読込の維持、合成での遅延シナリオの結線、`run` コマンドが v2 を受けること | 第18節 | 単体（未宣言キー・型不一致・`search_plan` の拒否）、統合（**検証戦略 B を `run` コマンド経由で通し**、段階3 のエンジン直呼びの判断履歴と `run_id` 列以外が一致すること） | 0 |
| 3 記録票・研究ポリシー・再現 | `evaluation.domain.experiment` / `research_policy`、`application.run_experiment`、`ExperimentStore` の実装、`ResultRepository.read_result`（段階2 の `fs_store` の具体クラスから移す）、`experiment run` / `experiment reproduce` コマンド | 第4.1節の `read_result` の段落・第19節・第20節・第21節 | 単体（ポリシーの各検査・上限の境界）、意味論（記録票が run より前に保存される・同じ版で中身が違えば拒否）、統合（別プロセスでの再現一致） | 1・2 |
| 4 レポート・実データ・段階4 受入 | `adapters.report`、実データ用マーカー、段階4 の受入テスト、実データ実行の記録文書 | 第22節・第23節 | 受入（`tests/acceptance/test_stage4_completion.py`）、実データ（マーカー付き、CI 外） | 3、snapshot 承認 PR |

- **PR 1 の merge 時に、AGENTS.md の「Missing data, failures and overwrites」節の但し書き**（値の解釈失敗の扱いは D07 の改訂が導入されるまで規則の対象にしない）**を外す**。本 PR では外さない（設計が承認されても実装が無いうちは、規則の対象が存在しないため）。
- AGENTS.md の後続対処のうち、成果物の書き込みを「存在すれば失敗」に統一する R4 は PR 3 より前に merge するのが望ましい（PR 3 が `fs_store` に記録票の書き込みを足すため）。順序が逆になった場合は、PR 3 の書き込みを第19.3節の規則どおりに作れば R4 の対象にも適合する。**v2.3: R4 は PR 3 より前に実施した**。

## 18. 実験設定の書式 v2【提案】＋【合意済み】（2026-09-25 の人間の決定4）

### 18.1 担当の分け方【提案】

実験設定の書式は、これまでどの設計文書も持っていなかった（書式 v1 は段階2 の実装 `app/config/experiment.py` が事実上の正本だった）。**書式 v2 の正本は本節とする**。人間の決定4 が「実験の記録票は書式 v2 の解決済み内容を保存したもの」と決めたため、記録票（第19節）と書式を同じ文書に置くと、記録票の項目と書式のキーの対応を1か所で確かめられる。

| 事項 | 正本 | 本節の扱い |
|---|---|---|
| 実験設定ファイルのキー・必須・既定・拒否の規則 | **本節** | 定める |
| 解決済みの実行条件（`RunConfig`）と `ConfigDigest` の対象 | D06 §3・§9.3 | 参照のみ。書式 v2 は**同じ値へ解決する**。D06 は変えない |
| 戦略宣言の型と設定ファイル表現の一般規則 | D04 §3・§13.1 | 参照のみ。段階3 の宣言を書くためのキーは一般規則の適用として第18.4節に挙げる（D04 v1.15 が本節を参照する1行を足す） |
| 遅延シナリオの型 | D03 §3.6 | 参照のみ。書き方だけを第18.3節で決める |
| 市場データの受入れ・分類・承認のコマンド | D03 §10 | 変えない |
| 実行前のデータ能力検査 | D06 §10.5 | 変えない（第23節が当たる制約） |
| 研究ポリシーのファイル | **本書第20.2節** | 書式 v2 からは版参照で指す |

### 18.2 トップレベルのキー【提案】

`schema_version: 2`。ADR-0018 の読込条件（安全な読込・カスタムタグ禁止・重複キーはエラー・未宣言キーの拒否）をそのまま適用する。

| キー | 必須 | v1 との違い | 解決先 |
|---|---|---|---|
| `schema_version` | 必須（`2`） | 1 → 2 | 読込の分岐 |
| `id` / `version` | 必須 | 同じ | 記録票の `experiment_name` / `experiment_version`（第19.2節）。`ConfigDigest` には入らない |
| `hypothesis` | **必須（新規）**。空白だけの文字列は拒否 | 新規 | 記録票。研究ポリシーの検査 P1（第20.3節） |
| `research_policy` | **必須（新規）**。`{id, version}` | 新規 | 研究ポリシーファイル（第20.2節）の版参照 |
| `strategy` | 必須。**戦略ファイルへのパス**（Q11 決定） | v1 は本文を同じファイルに書いていた | D04 の戦略宣言（第18.4節） |
| `snapshot` | 必須 | 同じ（承認済み snapshot の識別子。承認前は読めない関門も同じ） | `RunConfig.snapshot_ref` |
| `run_interval` / `execution_series` / `resolution_hierarchy` / `seed` / `account` | 必須（`seed` は既定 0） | 同じ | `RunConfig` |
| `risk_policy` / `execution_policy` / `cost_model` / `conversion_policy` | 必須 | 同じ（期間は v1 と同じ ISO 8601 の書き方のまま） | `RunConfig` の各ポリシーと版参照 |
| `delay_scenario` | 任意（**省略は遅延なし**） | 新規（v1 は遅延なしに固定） | `RunConfig.delay_scenario_ref` と市場データへの適用（第18.3節） |
| `environment` | 必須（新規）。`{calendar, timeframes, symbols}` の3つのパス | v1 はコマンドの引数で渡していた | 取引カレンダー・時間足定義・銘柄仕様の読込 |
| `evaluation` | 必須（新規）。`{metric_set_version}` | 新規 | 記録票に固定する評価規則（研究ポリシーの検査 P5） |
| `search_plan` / `split` | 必須（新規）。段階4 で書けるのは `NONE` だけ | 新規 | 単一実行であることの明示。段階5 で D09 が語彙を足す |

- **期間の書き方が2つ並ぶ**【提案】。ポリシーの期間（`entry_valid_for: "PT20M"` など）は v1 と同じ ISO 8601、戦略と遅延シナリオの期間（`"2s"`・`"25h"`）は D04 §13.1 の `<整数><単位>` である。ポリシーの版参照は**宣言の文字列のダイジェスト**から作るため（`app/config/experiment.py` の `_policy_ref`）、ポリシーの書き方を変えると検証戦略 A の `ConfigDigest` と `run_id` が変わる。**不採用**: v2 でポリシーの期間も D04 の書き方へ揃える案（書式は揃うが、書式を v2 へ書き換えただけで既存の run と別の識別子になる）。
- **パスはリポジトリの根からの相対パス**で書く【提案】。読込時に実体を読み、記録票には本文とダイジェストを保存する（第19.2節）。パスそのものは識別に使わない（同じ内容を別の場所に置いても同じ実験である）。**絶対パスと、根の外を指すパス（`../` で根を出るもの）は読込時に拒否する**【確定】（v2.1、2026-09-25 の人間の決定）。根の外を指せると、同じ設定ファイルが置き場所によって別のファイルを読む。根は `run` コマンドの `--repo-root` で渡す。
- **書式 v2 の `id` / `version` / `hypothesis` / `research_policy` / `evaluation` / `search_plan` / `split` は `ConfigDigest` に入らない**【提案】。`ConfigDigest` は D06 §9.3 が定める「実行の入力とポリシー」のダイジェストであり、仮説を書き直しただけで `run_id` が変わると、同じ実行が別の識別子になる。これらは記録票の識別子（第19.2節）にだけ入る。

### 18.3 遅延シナリオの書き方【提案】

```yaml
delay_scenario:
  id: d1_2s
  version: 1
  rules:
    - {kind: FIXED_SERIES_DELAY, series: "USDJPY/1d_ny17/bid", delay: "2s"}
    - {kind: INJECTED_BAR_DELAY, series: "USDJPY/1d_ny17/bid", bar_start: "2015-01-06T22:00:00Z", delay: "25h"}
```

- 規則の型と不変条件（遅延は非負、`available_at >= bar_end`）は D03 §3.6 が正本であり、`app.config` は型の構築で検査する。`SeededRandomDelay` は D03 §3.6 のとおり能力検査で拒否する（書式でも受け付けない）。
- **遅延なしは `delay_scenario` を書かない形だけで表す**【提案】。`rules: []` は拒否する。遅延なしの版参照は **v1 と同じ値**（`_policy_ref("delay", "NO_DELAY")`）にする。こうすると、検証戦略 A の設定を v2 へ書き換えても `ConfigDigest` と `run_id` が変わらない（第18.5節）。**不採用**: 遅延なしを `{id: none, rules: []}` と書かせる案（同じ意味の書き方が2つになり、どちらを書いたかで版参照が変わる）。
- 遅延シナリオの版参照は、**シナリオの `id` と `version` をそのまま載せ、ダイジェストは規則の正規形から作る**【確定】（v2.1、2026-09-25 の人間の決定。戦略の参照 `StrategyRef` と同じ作り方）。manifest の参照からどのシナリオかが読める。規則の並びは意味を持たない（同じ足に複数の規則が当たれば最大の遅延を採る。D03 §3.6）ので、系列・期間・時刻の書き方を揃えた正規形で整列してからダイジェストに入れ、同じ規則の重複は拒否する。並べ替えだけで `ConfigDigest` と `run_id` が変わらないようにするためである（第18.5節）。遅延なしの版参照は上のとおり書式 v1 と同じ値のまま変えない。
- **遅延は戦略向けの公開時刻だけを動かす。執行系列に当てても約定の時刻は変わらない**【確定】（v2.1、2026-09-25 の人間の決定。上位設計書 §4.7.12・D06 §6.4 が執行用データの可用性と戦略向けの公開遅延を分けている）。執行系列への遅延も書ける。
- `delay_scenario: null` は拒否する（遅延なしを表すのは書かない形だけ）。遅延の期間は D04 §13.1 の `<正の整数><単位>` で書くので、**遅延 0（`"0s"`）も拒否される**（0 の規則は書かないのと同じ）【確定】（v2.1、2026-09-25 の人間の決定）。
- 遅延シナリオを市場データへ適用する場所は合成（`app.composition`）であり、公開フィードへ渡す前に `available_at` を計算する（段階3 の受入テストが fixture で行っていた適用を、合成へ移す。D03 §3.6 の「実現した `available_at` 列は run に保存する」はそのまま）。

### 18.4 戦略ファイルと、段階3 の宣言を書くキー【提案】

**戦略宣言は別ファイル（`configs/strategies/<strategy_id>_v<version>.yaml`）に置き、実験設定からパスで指す**（Q11 決定）。D01 §10.1 の配置（`strategies/` と `experiments/` を分ける）と D04 §13.1 の置き場所に合わせる。検証戦略 B の遅延シナリオ4ケースのように、**同じ戦略を複数の実験で使うとき、戦略の本文を1か所に保てる**。

戦略ファイルの書き方は D04 §13.1 の一般規則（区分タグ付き union は `kind:`、期間は `<整数><単位>`、未宣言キーの拒否）に従い、**書式 v1 の `strategy:` 節の略記**（入力の参照元を `market:` / `output:` / `runtime:` の文字列で書く、起動条件を `when:` で書く）をそのまま引き継ぐ。段階3 の宣言のために足すキーは次の3つだけである（読み取り条件と欠損方針は部品の契約 `InputSpec` が持ち、使用箇所では書かない。D04 §4.1）。

| キー | 書き方 | 対応する型（D04） |
|---|---|---|
| `entry_policy` | `{kind: IMMEDIATE}` または `{kind: AWAIT_CONFIRMATION, deadline: {kind: BARS, bars: 4}, on_deadline: EXPIRE}`（`deadline` は `{kind: DURATION, duration: "1h"}` も可） | `ImmediateEntry` / `AwaitConfirmation`（§10.1） |
| `opportunity_validity` | `{bindings: [{source: "daily_above_ema.condition", mode: REQUIRE_UNTIL_ORDER_REQUEST, on_missing: {kind: SKIP_EVALUATION}}]}`。省略は拒否（D04 §10.2、ADR-0031。束縛の無い戦略は `bindings: []` と書く。v2.1） | `OpportunityValiditySpec` / `ValidityBinding`（§10.2） |
| `roles.market_state` / `roles.execution_filter` | 出力参照の文字列（v1 と同じ形） | 役割フィールド（§3） |

- 戦略ファイルのトップレベルは `schema_version: 1`（D04 §3 の保存形式の版。段階3 でも 1 のまま）を持つ。**実験設定の版（2）と戦略ファイルの版（1）は別の版**であり、揃えない。
- 書式 v1 の `entry_policy: "IMMEDIATE"`（文字列）は v2 の戦略ファイルでは受け付けない（区分タグ付き union を `kind:` で書く D01 §8 の規則に揃える）。v1 の実験設定の中では引き続き文字列で読む（第18.5節）。
- **`entry_policy` と `opportunity_validity` はどちらも省略を拒否する**【確定】（v2.1、2026-09-25 の人間の決定）。暗黙の既定値を置かない原則（ADR-0031。有効性束縛は D04 §10.2 の Q5 決定）に従い、戦略ファイルの中で束縛だけが必須・入場方針は任意という非対称を作らないためである。後続確認の期限時の扱い（`on_deadline`）と束縛1件の欠損方針（`on_missing`）も省略できない。

### 18.5 書式 v1 の扱い【提案】（Q12 決定）

- **v1 の読込を残す**。`run` コマンドは v1 と v2 の両方を受け、`schema_version` で分岐する。v1 の設定（`configs/experiments/strategy_a_t01.yaml`）は書き換えない。段階2 の受入テストはこのファイルを入力にしており、書き換えると段階2 の完了条件の証拠を作り直すことになる。
- **書式 v2 では取引カレンダー・時間足定義・銘柄仕様を `environment` だけで指す**。`run` コマンドに `--calendar` / `--timeframes` / `--symbols` も渡したら拒否する（同じものを2か所で指せると、どちらを使ったかが設定から読めない）。書式 v1 では従来どおり引数で必須【確定】（v2.1、2026-09-25 の人間の決定）。
- **`experiment run` コマンド（第19節）は v2 だけを受ける**。v1 には仮説と研究ポリシーの参照が無く、研究ポリシーの検査 P1 を満たせないためである。
- v1 と v2 で同じ実行条件を書いた場合、**解決済みの `RunConfig` と `ConfigDigest` は一致する**（第18.2節・第18.3節の2つの規則による）。PR 2 の単体テストで、検証戦略 A の v1 と v2 の設定が同じ `ConfigDigest` になることを固定する。

### 18.6 拒否する設定【提案】

`app.config` は次を**読込時に拒否**し、`ConfigError` で理由を示す（検査を後段へ回さない）。

1. 未宣言キー、型不一致、`schema_version` が 1・2 以外。
2. `hypothesis` が空または空白だけ。
3. `search_plan` / `split` が `NONE` 以外（段階5 の語彙。書式 v2 の段階4 の範囲外であることを理由に示す）。
4. `delay_scenario.rules` が空、`SeededRandomDelay` を書いた、遅延が負。
5. `environment` / `strategy` のパスが無い・読めない。
6. `evaluation.metric_set_version` がこの実装の持たない版（段階2 の `evaluate` コマンドと同じ検査）。

研究ポリシーの検査（第20節）と実行前のデータ能力検査（D06 §10.5）は**読込では行わない**。前者は記録票に結果を残すため、後者は run の結果に残すためである。

### 18.7 例（検証戦略 B、遅延シナリオ d1_2s）

```yaml
schema_version: 2
id: strategy_b_t02_d1_2s
version: 1
hypothesis: "日足の上昇相場で1時間足の高値更新を15分足で確認して買えば、遅延2秒でも T02 の経路どおりに約定する"
research_policy: {id: research_policy, version: 1}
strategy: configs/strategies/strategy_b_v1.yaml
environment:
  calendar: configs/calendars/fx_ny17_v2.yaml
  timeframes: configs/calendars/timeframes_v1.yaml
  symbols: configs/symbols
snapshot: "<承認済み snapshot の識別子>"
run_interval: {start: "2015-01-04T22:00:00Z", end: "2015-01-16T22:00:00Z"}
execution_series: "USDJPY/15m/bid"
resolution_hierarchy: ["USDJPY/15m/bid"]
seed: 0
account: {account_id: "ACC1", currency: "JPY", initial_balance: "1000000"}
# risk_policy / execution_policy / cost_model / conversion_policy は v1 と同じ書き方
delay_scenario:
  id: d1_2s
  version: 1
  rules:
    - {kind: FIXED_SERIES_DELAY, series: "USDJPY/1d_ny17/bid", delay: "2s"}
evaluation: {metric_set_version: 2}
search_plan: NONE
split: NONE
```

**同梱の設定**【確定】（v2.1、2026-09-25 の人間の決定）。`configs/experiments/strategy_b_t02_*.yaml`（遅延シナリオ4ケース）と `strategy_a_t01_v2.yaml` は、人工データ（紙上トレース T01・T02）と同じ取引カレンダー `fx_ny17_v1.yaml` を指し、`snapshot` は書式 v1 と同じゼロの既定値（環境ごとに承認済みの識別子へ書き換える）を置く。実データの承認済み snapshot を指す設定は実データ実行（第23節、実装 PR 4）で足す。**指標集合の版は、実装がその版の式を持つまで書けない**（第18.6節の6）ので、指標集合 v2 の実装（実装 PR 1）より前に入った同梱の設定は版 1 を書き、実装 PR 1 と PR 2 のうち後に merge する側が 2 へ上げる。

## 19. 実験の記録票（実験 manifest）と実験の結末記録【提案】＋【合意済み】（人間の決定4）

### 19.1 2つのファイルに分ける【提案】

1つの実験について、**実行の前に書いて以後書き換えない記録票**（`experiment_manifest.json`）と、**実行の後に書く結末記録**（`experiment_outcome.json`）の2つを残す。

- 記録票は**事前固定の証拠**である。run の結果を見た後で書き換えられないよう、run より前に保存し、同じ版に別の内容を書くことを拒否する（第20.3節の検査 P3）。結果を同じファイルへ追記する形にすると、ファイルの更新そのものが「結果を見た後の書き換え」と区別できなくなる。
- 結末記録は、記録票の識別子・`run_id`・評価の識別子・結果ダイジェスト・研究ポリシーの事後検査の結果を持ち、**記録票から結果へ辿る唯一の経路**である。

保存先は `runs/experiments/<experiment_name>/v<experiment_version>/` とする。D01 §10.3 の図は `runs/experiments/<experiment_id>/` と書いているが、`ExperimentId` は内容のダイジェスト（D02 §7.1）なので、それをディレクトリ名にすると**同じ版の中身を書き換えたときに別のディレクトリになり、検査 P3 が前の記録票を見つけられない**。そこで**人間が付けた名前と版**をディレクトリ名にし、ダイジェストは記録票の中の `experiment_id` に置く（D01 v2.7 で §10.3 の図をこの形に改めた）。run と評価の成果物は従来どおり `runs/<run_id>/` と `runs/<run_id>/eval/<run_evaluation_id>/` に置き、結末記録がそれを指す。**同じ run を2つの実験が指してもよい**（同じ実行条件なら `run_id` が同じになるため。ADR-0006）。

### 19.2 記録票の項目【提案】

| 群 | 項目 | 内容 |
|---|---|---|
| 識別 | `experiment_id` | **共通カーネルの `ExperimentId`（D02 §7.1「実験 spec のダイジェスト」、採番は本書）**。下の「識別の入力」の段落が列挙する値の正規化エンコードのダイジェスト（D02 §9.3）。人間が書く名前（`experiment_name`）とは別の値である |
| | `experiment_name` / `experiment_version` / `schema_version` | 実験設定の `id` / `version`（人間が付ける名前と版。保存先のディレクトリ名と、検査 P3 で「同じ版」を探す鍵に使う）と書式の版（2） |
| 事前固定 | `hypothesis` | 実験設定の本文そのまま |
| | `research_policy_ref` | `{id, version, digest}`。`digest` は研究ポリシーファイルの解決済み内容のダイジェスト |
| | `metric_set_version` | 実験設定の `evaluation.metric_set_version` |
| | `search_plan` / `split` | 段階4 は `NONE` |
| 解決済みの設定 | `resolved_files` | 実験設定・戦略ファイル・研究ポリシー・取引カレンダー・時間足定義・銘柄仕様の**本文（UTF-8 文字列）とその SHA-256**。役割名（`experiment` / `strategy` / `research_policy` / `calendar` / `timeframes` / `symbol:<銘柄>`）で引く。銘柄仕様は**実行する銘柄と、換算の経路に現れる銘柄**のものだけを入れる |
| | `strategy_ref` / `compiled_ref` | 戦略定義の内容ハッシュとコンパイル結果のハッシュ（D04 §13.2、D05 §5.5） |
| | `expected_config_digest` | run の前に合成が計算した `ConfigDigest`（D06 §9.3）。事後検査 P4 が実際の run と照合する。**`RunId` は記録票に入れない**（下の段落） |
| データ | `snapshot_id` | 承認済み snapshot |
| | `allowed_partitions` | 合成が as-of ビューへ渡す許可 partition の一覧と、それぞれのアクセス分類（`SnapshotCatalog` から取る。D01 §4） |
| 複雑性 | `complexity` | 計測値4件（第20.4節。計測できなかった値は `None` のまま残し、0 で埋めない）と、研究ポリシーの上限4件 |
| 事前検査 | `pre_run_checks` | 研究ポリシーの**事前の段階（`PRE_RUN`）の検査** P1・P2・P6 の全件（合格・不合格とも。第20.3節）。保存時の検査 P3 はここに入れない（記録票の識別子と比べる検査なので、識別子の入力に入れると循環する。P3 の結果は結末記録に書く。第19.3節） |
| 環境 | `code_digest` / `lock_digest` / `env_digest` | run manifest と同じ算出（D02 §9.4、D06 §9.3） |
| | `git_commit` / `git_dirty` | 記録だけ。識別に入れない（ADR-0006 と同じ扱い） |

- **識別の入力**（`experiment_id` を計算する値。この一覧に無いものは入れない）:
  1. `experiment_name` / `experiment_version` / `schema_version`
  2. **実験設定の値からパスを持つキーを除いたもの**: 実験設定の YAML を読み込んだ値（文字列・整数・入れ子の mapping と列。解決前の宣言の形）から、`strategy` と `environment`（`calendar` / `timeframes` / `symbols`）の**キーごと取り除いた** mapping。実験設定ファイルの本文やその SHA-256 は**入れない**（本文にはパスの文字列が書かれているため）
  3. **参照先のファイルの内容**: `resolved_files` のうち役割が `strategy` / `research_policy` / `calendar` / `timeframes` / `symbol:<銘柄>` の要素の `(role, sha256)` の組を、役割名の文字列の昇順に並べた列。**銘柄仕様は `symbols` ディレクトリ全体ではなく、実行と換算に使う銘柄のファイルだけ**である（ディレクトリのハッシュは定義しない）。役割 `experiment` の要素は入れない（2 が代わる）
  4. 事前固定の群（`hypothesis` / `research_policy_ref` / `metric_set_version` / `search_plan` / `split`）、`strategy_ref` / `compiled_ref` / `expected_config_digest`、データの群（`snapshot_id` / `allowed_partitions`）、複雑性の群、`pre_run_checks`（P1・P2・P6 だけ）
- **パスは識別に入らない**（第18.2節の「同じ内容を別の場所に置いても同じ実験である」）。戦略ファイルを中身そのままで別の場所へ移し、実験設定のパスだけを書き換えて同じ版を再実行しても、2 と 3 は変わらないので識別子は変わらず、検査 P3 は合格する。関係の無い銘柄のファイルを `symbols` ディレクトリに足しても、3 に入らないので識別子は変わらない。
- **環境の群は識別に入れない**。環境の群を入れると、コードを1行直しただけで同じ実験の同じ版が「別の内容」になり、検査 P3 が事前固定の違反と誤判定する。コードが違う実行の区別は `run_id`（コードのダイジェストを含む）が担う。**同じ理由で、予測した `RunId` も記録票に入れない**（`RunId` はコード・lock・環境のダイジェストから決まる。ADR-0006）。予測した `RunId` と実行時の環境のダイジェストは、**実行ごとに結末記録へ書く**（第19.3節）。記録票の環境の群は、その版を**最初に保存したとき**の環境の記録である。
- **本文を保存する**のは、人間の決定4（記録票は解決済み内容を保存したもの）の直接の実施であり、別プロセスでの再現（第21節）が**設定を記録票だけから組み立てられる**ための条件である（比べる相手の結果は結末記録にある）。パスとダイジェストだけでは、ファイルを消したり書き換えたりした後に再現できない。銘柄仕様を**実行と換算に使う銘柄のもの**に限るのは、関係の無いファイルの変更で記録票の識別子が変わらないようにするためである。
- **実行時刻を入れない**。第8.3節の評価 manifest と同じ理由である。

### 19.3 結末記録の項目と書き込み規則【提案】

| 項目 | 内容 |
|---|---|
| `experiment_id` | 対応する記録票 |
| `status` | `ExperimentStatus`（第19.4節の終端3値） |
| `expected_run_id` | この実行の前に合成が計算した `RunId`（`expected_config_digest` とこの実行の環境のダイジェストから。ADR-0006）。事前検査で止まった場合も書く |
| `code_digest` / `lock_digest` / `env_digest` / `git_commit` / `git_dirty` | **この実行**の環境（記録票の環境の群と同じ算出）。別プロセスでの再現はこの値と比べる（第21.2節） |
| `run_id` / `run_status` / `run_reused` | run が行われた（または既存の成果物を再利用した。第19.6節）場合だけ。行われなければ `None` |
| `run_evaluation_id` / `evaluation_status` / `result_digest` | 評価が行われた場合だけ |
| `outcome_checks` | 研究ポリシーの**保存時の検査 P3 と事後の検査 P4・P5** の全件（第20.3節）。事前検査で止まった場合は P3 だけ（P4・P5 は run が無いので実施しない） |
| `failed_checks` | `status` が `COMPLETED` でないときに、合格でなかった研究ポリシーの検査（`tuple[PolicyCheck, ...]`。第20.3節の宣言順）。`COMPLETED` なら空。**D02 §8.1 の理由型（`Reason`）は使わない**。理由コードは状態遷移の理由の語彙であり（D02 §8.1、上位 §4.7.14）、研究ポリシーの検査名はその語彙に無いので、検査名そのものを型で持つ |

- **記録票の書き込み**: 保存先に記録票が無ければ書く。**あり、内容のダイジェストが同じなら何もしない**（同じ実験の同じ版をもう一度実行するのは正当である）。**あり、ダイジェストが違えば書かずに拒否する**（検査 P3 の不合格。既存の記録票は上書きしない。ADR-0006 の「既定は失敗」）。
- **結末記録の書き込み**: **記録票の保存が成功した（新規または同一）直後、run を始める前に、同じ版に既にある結末記録を `experiment_outcome.<n>.json`（n は 1 からの連番）へ退避する**。こうしておくと、今回の実行が途中で止まったときに「結末記録が無い＝途中で止まった」（第19.4節・第22節）がそのまま成り立ち、前の実行の結末記録を今回の結果として読むことが無い。今回の結末記録はその後に `experiment_outcome.json` として書く。記録票と違い結末記録は再実行で変わりうる（例: コードを直して同じ実験の同じ版を再実行すると、記録票は同じままで `run_id` と結果が変わる）ため、置き換えは許すが旧い記録は消さない（ADR-0006 の run 成果物の置換と同じ考え方）。
- 両ファイルとも JSON（ADR-0027）。書き出しは `ExperimentStore` ポート（D01 §4）経由で、実装は `evaluation.adapters.fs_store` が持つ。

### 19.4 1つの実験の進み方（状態×出来事表）【提案】

`experiment run` コマンド1回の処理は次の順に進む。**記録票の保存が成功するまで run を始めない**（事前固定の構造的な保証）。

| 状態 ＼ 出来事 | 設定の読込に失敗 | 事前検査が全件合格 | 事前検査に合格でないもの（`FAILED` または `UNREADABLE`）あり | 記録票の保存が成功（新規または同一） | 記録票が同じ版で内容違い | run が終わった（状態は問わない） | 評価が終わった | 事後検査が全件合格 | 事後検査に合格でないもの（`FAILED` または `UNREADABLE`）あり | 例外・中断 |
|---|---|---|---|---|---|---|---|---|---|---|
| **読込前** | 終了（記録なし。`ConfigError` を表示） | 到達しない（検査は読込の後） | 到達しない（同左） | 到達しない（同左） | 到達しない（同左） | 到達しない（run は保存の後） | 到達しない | 到達しない | 到達しない | 終了（記録なし） |
| **検査済み**（記録票を組み立てた） | 到達しない（読込は済んだ） | → 既存の run 成果物を確かめる（第19.6節の手順1〜3。読むだけで何も書かない）。**再利用できない衝突なら、記録票も結末記録も書かずに拒否して終了**（`RUN_ARTIFACT_CONFLICT`、終了コード 5。版のディレクトリは変わらない）。そうでなければ保存へ | → 保存へ（不合格も記録票に残す） | 到達しない（保存の前） | 到達しない（同左） | 到達しない | 到達しない | 到達しない | 到達しない | 終了（記録なし） |
| **保存を試みる** | 到達しない | 到達しない | 到達しない | 事前検査が合格なら **記録済み**へ。不合格なら結末記録 `REJECTED_BY_POLICY` を書いて**終端** | 結末記録を書かず**拒否して終了**（検査 P3 `PREREGISTRATION_UNCHANGED` の不合格として表示する。既存の記録票は変えない） | 到達しない | 到達しない | 到達しない | 到達しない | 終了（記録票が書けたかは保存の原子性による。第19.3節の書き込みは一時ファイル＋改名で原子的に行う） |
| **記録済み** | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | → **実行済み**（`run_id` を保持。既存の run 成果物を再利用した場合も同じ。第19.6節） | 到達しない（評価は run の後） | 到達しない | 到達しない | **記録票だけが残る**（結末記録なし）。第22節のレポートは「結末記録が無い＝途中で止まった」と表示する |
| **実行済み** | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない（run は1回） | → **評価済み**。run が正常完走していなければ評価は `REJECTED`（第10.1節）のまま進む | 到達しない | 到達しない | 同上（記録票だけが残る。run の成果物は `runs/<run_id>/` に残る） |
| **評価済み** | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 結末記録 `COMPLETED` を書いて**終端** | 結末記録 `FAILED_POST_RUN_CHECK` を書いて**終端** | 同上 |

- **「全件合格」とは、全件の `outcome` が `PASSED` であること**である。`UNREADABLE`（計測や照合ができなかった）は合格として扱わず、`FAILED` と同じ列（合格でないものあり）へ進む（第20.3節）。
- 既存の run 成果物と `run_id` が衝突したときの扱いは第19.6節に置く。**確かめるのは記録票を保存する前**（上表の「検査済み」行）で、衝突して再利用できない場合は「例外・中断」の列ではなく、**何も書かずに**拒否して終了する（終了コード 5。第21.3節）。保存の前に確かめるので、旧い結末記録とレポートの退避（第19.3節）も起きず、同じ版の前回の結果はそのまま残る。拒否が「結末記録が無い＝途中で止まった」（第22.1節）と取り違えられることは無い。
- `ExperimentStatus` の終端は3値: `COMPLETED`（事前・事後の検査が合格し、run と評価が行われた。**run や評価そのものの失敗は、それぞれの状態（`RunStatus` / `EvaluationStatus`）が表す**）、`REJECTED_BY_POLICY`（事前検査で不合格。run しない）、`FAILED_POST_RUN_CHECK`（事後検査で不合格。成果物は残すが、第22節のレポートは「採用不可」と先頭に出す）。
- 「記録票の保存が内容違いで拒否された」ことは結末記録に残せない（書く先が、書き換えを拒否した当の版のディレクトリになるため）。**コマンドの終了コードと表示で示す**（第21.3節の終了コード表）。この場合は `version` を上げて新しい版として実行する。

### 19.5 値の伝播表【提案】

全体計画書の必須表（R1）の形（値・生成元・渡り方・記録先・主キー）に合わせる。

| 値 | 生成元 | 渡り方 | 記録先 | 主キー・一意性 |
|---|---|---|---|---|
| `experiment_name` / `experiment_version` | 実験設定（人間） | `app.config` → `ExperimentManifest` | 記録票、保存先のディレクトリ名 `runs/experiments/<name>/v<version>/`、結末記録（記録票の識別子を経由） | `(experiment_name, experiment_version)` ごとに記録票は1つ（第19.3節） |
| `experiment_id` | `evaluation.domain.experiment`（記録票の識別に入る項目のダイジェスト。第19.2節） | `ExperimentManifest` → `ExperimentOutcome` | 記録票、結末記録、再現の報告（第21.2節） | 同じ内容なら同じ値 |
| `expected_config_digest` | 合成（run の前に `RunConfig` から計算。D06 §9.3） | `PreparedExperiment.manifest` | 記録票（識別に入る） | 同じ解決済みの設定なら同じ値 |
| `expected_run_id` | 合成（`expected_config_digest` とこの実行の環境のダイジェストから計算。ADR-0006） | `PreparedExperiment.expected_run_id` → `ExperimentOutcome` | 結末記録（記録票には入れない。第19.2節） | 実行ごとに1つ |
| `run_id` | バックテスト（`BacktestRunner.run` の戻り値の `BacktestResult`） | `RunExperiment` → `ExperimentOutcome` | 結末記録、`runs/<run_id>/` | ADR-0006。事後検査 P4 で結末記録の `expected_run_id` と照合 |
| `run_evaluation_id` / `result_digest` | 評価（第8.3節・第9.2節） | `EvaluateRun` → `ExperimentOutcome` | 結末記録、評価 manifest | 第9.2節 |
| `research_policy_ref` | 研究ポリシーファイル（第20.2節） | `app.config` → `ExperimentManifest` | 記録票 | `(id, version, digest)` の組で記録する。同じ `(id, version)` で内容が違うファイルを使った実験どうしは、記録票の `digest` で後から見分けられる（第20.2節） |
| 検査結果（`PolicyCheckResult`） | `evaluation.domain.research_policy`（第20.3節） | 事前（P1・P2・P6）は `ExperimentManifest`、保存時（P3）と事後（P4・P5）は `ExperimentOutcome` | 記録票の `pre_run_checks`、結末記録の `outcome_checks` | 検査名（`PolicyCheck`）ごとに1件 |

### 19.6 同じ実験の再実行と、既存の run 成果物との衝突【提案】

同じ実験の同じ版を同じコードで再実行すると、`run_id` は同じ値になる（ADR-0006）。`runs/<run_id>/` は既に存在し、決定論的 ID の決定記録（ADR-0006）の「既定は失敗、置換は明示の指示と旧 manifest の保存を要する」に当たる。`experiment run` は次のとおり扱う。

1. **事前検査が全件合格したら、記録票を保存する前に、`runs/<expected_run_id>/` があるかを見る**（第19.4節の「検査済み」行）。無ければ保存へ進み、run する。
2. **あり、その run manifest の `run_id` と `ConfigDigest` がこの実行の期待値と一致すれば、run し直さず既存の成果物を再利用する**。`run_id` は入力・コード・lock・環境から決まる内容の識別子であり、一致する成果物は同じ入力から決定論的に作られた成果物だからである（D06 §4.4 の再実行一致）。結末記録に `run_reused: true` を書く。再利用する run の結果は `ResultRepository.read_result`（第4.1節）で読み、読めなければ再利用できないとして手順3 へ進む。評価も同じで、`runs/<run_id>/eval/<run_evaluation_id>/` があり評価 manifest の `run_evaluation_id` と `result_digest` の算出元が一致すれば再利用する（`run_evaluation_id` は受け取ったカレンダーを含む。第9.2節）。
3. **あり、run manifest か結果が読めない・一致しない場合は、記録票を保存せず、run せず拒否して終了する**（終了コード 5。成果物も版のディレクトリも変えない）。置き換えたいときは既存の `run --replace`（ADR-0006 の明示の置換。旧 manifest を残す）を人間が使う。**`experiment run` には置換の指示を持たせない**。事前固定の記録を持つ実験の経路で、既存の成果物を黙って置き換える余地を作らないためである。
4. `BacktestRunner.run` は置換の指示を受け取らない（常に「存在すれば失敗」で書く）。再利用の判断は `RunExperiment` が記録票の保存の前に `ResultRepository.read_manifest` と `read_result` で行う。
5. **書き込みは根本対処 R4 の規則に従う**（v2.3）。run の保存先・評価の保存先は、存在すれば何も書かずに `ArtifactAlreadyExists` で失敗する（D06 §9.1、第8.2節）。そのため手順1〜3 の判断は**必ず書き込みの前に**行い、書き込みの失敗を「既存の成果物がある」ことの検出に使わない。手順2 で評価を再利用しない場合（評価の保存先が無い）だけ評価を書く。評価の保存先があるのに再利用できない（読めない・一致しない）ときは、手順3 と同じく拒否して終了する。評価の成果物に置換の指示は無いので、置き換えたいときは人間が先にそのディレクトリを移動または削除する。

**不採用**: 再実行のたびに `--replace` 相当で置き換える案（同じ内容なら置き換える意味が無く、内容が違えば旧い成果物を失う）、再実行を常に失敗させる案（第19.3節が正当とした「同じ版をもう一度実行する」経路が使えない）。

## 20. 研究ポリシー v1【提案】＋【合意済み】（2026-09-25 の人間の決定5）

### 20.1 何を検査するか【合意済み】

人間の決定5 は「**事前固定（ダイジェスト）を検査し、複雑性にも上限を置いて検査する**」である。上位設計書 §6 と全体計画書 §5.5.1 は「初期に上限は設けない（計測だけ）」としていたので、**上位文書との差異になる**（第13節の差異4）。上位文書 §6 の「共通の研究ポリシーを1種類だけ運用する」「将来複数へ拡張できる参照構造を持つ」はそのまま守る。

### 20.2 研究ポリシーのファイル【提案】

`configs/policies/research/research_policy_v1.yaml`（D01 §10.1 の `policies/`）。

```yaml
schema_version: 1
id: research_policy
version: 1
complexity_limits:
  component_kinds: 30    # Q7 決定（検証戦略 B の約3倍）
  instances: 36
  parameters: 33
  decision_outputs: 18
```

- **検査の規則そのものはコードに置き、ファイルは上限の値だけを持つ**。規則をファイルで書ける形にすると、検査の意味が設定で変わり、「共通1種類」の意味が崩れる。規則を変えるときは `ResearchPolicy` の版をコードとファイルの両方で上げる。
- 版参照 `research_policy_ref = (id, version, digest)`。`digest` は解決済みの内容のダイジェストである。**同じ実験の同じ版を、上限だけ書き換えたポリシーで再実行すること**は、記録票に `digest` が入るため検査 P3 が拒否する。**別の実験どうしで同じ `(id, version)` の中身が違うこと**は段階4 では拒否しない（ポリシーの版の登録簿を持たないため）が、記録票の `digest` で後から見分けられる。版を上げずに中身を変えることは運用で禁じ、登録簿による強制が要るかは段階5（D09）で探索が始まる前に判断する。

### 20.3 検査の一覧【提案】

| # | 検査 | 時点 | 合格の条件 | 不合格のとき |
|---|---|---|---|---|
| P1 | `hypothesis_present` | 事前 | 仮説が空でない（書式でも拒否するが、記録票の検査として残す） | run しない |
| P2 | `research_history_only` | 事前 | `allowed_partitions` のアクセス分類がすべて `RESEARCH_HISTORY`（D03 §3.8） | run しない |
| P3 | `preregistration_unchanged` | 保存時 | 同じ `(experiment_name, experiment_version)` の記録票が無いか、あっても同じ `experiment_id` | 記録票を書かず拒否（第19.4節） |
| P4 | `run_matches_preregistration` | 事後 | run manifest の `ConfigDigest` が記録票の `expected_config_digest` と一致し、実際の `run_id` がこの実行の `expected_run_id`（結末記録）と一致 | `FAILED_POST_RUN_CHECK` |
| P5 | `evaluation_rule_matches` | 事後 | 評価 manifest の `metric_set_version` が記録票と一致 | `FAILED_POST_RUN_CHECK` |
| P6 | `complexity_within_limits` | 事前 | 第20.4節の計測値4件がすべて上限以下 | run しない |

- **計測できなかった複雑性**は、`ComplexityMeasures` の該当項目を `None` とし、P6 を `UNREADABLE`、`observed` に計測できなかった項目と原因（例: 部品の登録に出力のデータ型が無い）を書く。記録票は `None` のまま保存する（第19.4節の「保存を試みる」行。0 で埋めると上限の内側に見えてしまう）。
- **合格は `PASSED` だけ**である。`FAILED` と `UNREADABLE` はどちらも「合格でない」とし、上表の「不合格のとき」の扱いに従う（第19.4節の状態表の列）。
- **検査結果は合格・不合格とも全件を残す**（第10.2節の整合検査と同じ扱い）。型は `PolicyCheckResult(check, stage, outcome, expected, observed)` で、`outcome` は第10.4節の `CheckOutcome`（合格・不合格・読めなかった）を使う。**計測できなかった複雑性は「読めなかった」とし、合格として扱わない**。
- P2 は封印期間を読む経路が段階4 に無いことの**二重の保証**である（合成は研究履歴しか許可集合に入れない。D03 §3.8 v1.5）。合成の規則が将来変わっても、研究ポリシーが記録票の段階で止める。
- P4 が不合格になるのは、記録票の保存から run までの間に合成の入力が変わった場合（例: 同じパスのカレンダーファイルを書き換えた）である。記録票の本文と実際の入力の食い違いを、結果を採用する前に見つける。

### 20.4 複雑性の計測【提案】

計測の**規則**は `evaluation.domain.research_policy` の純粋関数に置き、**材料**は合成（`app`）が組み立てて渡す。材料には部品の契約（出力のデータ型）が要るが、評価は部品カタログを参照できない（D01 §3.2 の契約 F8）ためである。材料は使用箇所ごとの `InstanceProfile(instance_id, component_id, parameter_count, output_data_types)` の列で、コンパイル結果（`CompiledStrategy`）と部品の登録から作る。

| 計測値 | 数え方 | 検証戦略 A | 検証戦略 B |
|---|---|---|---|
| `component_kinds`（部品数） | 使われている部品の種類（`component_id`。版は区別しない）の数 | 5 | 10 |
| `instances`（使用箇所数） | 使用箇所（`ComponentInstance`）の数 | 6 | 12 |
| `parameters`（パラメータ数） | 全使用箇所の**解決済みパラメータ**（既定値で埋めたものを含む。`CompiledComponent.parameters`）の件数の合計 | 6（宣言の件数。解決済みの件数は PR 3 で確定） | 11（同左） |
| `decision_outputs`（判断を出す使用箇所の数） | 出力のデータ型が `condition_state` / `market_permission` / `opportunity` / `confirmation_result`（D04 §5）のいずれかを1つ以上持つ使用箇所の数 | 1 | 6 |

- **「合成部品の内部も含む条件数」**（上位設計書 §6）は、`decision_outputs` がそのまま満たす。段階3 のカタログの合成部品（論理合成）は、合成される条件を**別の使用箇所の出力として入力に受ける**形であり（D05 §4.6）、内部に条件を隠さない。したがって合成部品も合成された条件も、それぞれ1件として数えられる。**内部に条件を持つ部品を将来カタログへ足すときは、その部品の登録が内部の条件数を宣言する**必要があり、それは D05 の改訂として扱う（第14節の引き渡しではなく、カタログ側の後続）。
- パラメータ数に**戦略レベルの宣言**（確認期限の本数・同時保持の上限など）を含めない【提案】。それらは D04 の型で件数が決まっており（入場方針は1つ、同時保持の宣言は1つ）、上限で抑える意味が無い。探索（段階5）がそれらを動かすようになったら数え方を見直す。
- 検証戦略 A・B の値は起草時に宣言から数えたものであり、`parameters` は解決済みの件数が宣言の件数より多い可能性がある。**PR 3 の単体テストで2戦略の4つの計測値を固定し、上限（Q7 決定: 30・36・33・18）の内側にあることを確かめる**。外側になった場合は上限を決め直す要決定として上げる（黙って上限を上げない）。

## 21. 別プロセスでの再現【提案】

### 21.1 何をもって「再現した」とするか【提案】

**同じ実行環境（コード・依存 lock・環境のダイジェストがすべて同じ）で、記録票と結末記録だけを入力に別プロセスで run と評価をやり直し、`run_id` と `result_digest` が結末記録と一致すること**を「再現した」とする。比べる相手は記録票ではなく結末記録である。記録票は実行の結果を持たず（第19.1節）、結果と実行時の環境は実行ごとに結末記録に書かれるためである。

- `run_id` の一致は、実行条件（`ConfigDigest`）とコード・lock・環境が同じであることを意味する（ADR-0006）。
- `result_digest` の一致は、評価5表の全行が同じであることを意味する（第9.2節）。判断履歴19表そのものの一致は、段階2・3 の受入テストが同一プロセスでの再実行として確かめており（D08 §11）、別プロセスの再現では評価の結果までを比べる。
- **別プロセス**とは、再現コマンドを新しい OS のプロセスとして起こし、そのプロセスが受け取るのは**実験の版のディレクトリ（記録票と結末記録がある場所）と snapshot の基点と出力の基点だけ**であることをいう。元のプロセスのメモリ上の状態や、元の実験設定ファイル・戦略ファイルを読まない。

### 21.2 再現の手順【提案】

`odyssey-fx experiment reproduce --experiment-dir <runs/experiments/<id>/v<版>> --snapshots <基点> --out <別の出力の基点>` は次の順に行う。

1. 記録票と結末記録を読み、`experiment_id` を再計算して記録票と結末記録の両方の値と一致することを確かめる（記録票の改変と、別の版の結末記録との取り違えを検出する）。結末記録が無い、または `run_id` を持たない（run が行われていない）なら、再現する結果が無いので `MANIFEST_TAMPERED` ではなく**引数の誤り**として終了する。
2. 現在の環境の `code_digest` / `lock_digest` / `env_digest` を計算し、**結末記録**と比べる。**1つでも違えば run せず、`ENVIRONMENT_MISMATCH` として終了する**（Q12 決定。違う環境での結果は同じ `run_id` になりえず、判定の対象外である）。
3. `resolved_files` の本文を、ファイルシステムへ書き戻さずに `app.config` の読込へ文字列として渡し、`RunConfig` を組み立てる。組み立てた `ConfigDigest` が記録票の `expected_config_digest` と、`RunId` が結末記録の `run_id` と一致することを確かめる。**どちらかが一致しなければ run せず、判定 `RUN_ID_MISMATCH`**（`observed_run_id` は組み立てた `RunId`、`observed_result_digest` は `None`）**として `--out` の下の `reproduction.json` に書き、終了コード 6 で終わる**【提案】。記録票と結末記録は手順1 で改変が無いと確かめ、環境は手順2 で同じと確かめてあるので、ここでの不一致は「記録票の入力からは結末記録の run に届かない」ことを意味する。典型は検査 P4 が不合格だった実験（`FAILED_POST_RUN_CHECK`。記録票の保存から run までの間に入力が変わった。第20.3節）で、その run は記録票からは再現できないのが正しい判定である。
4. `--out` の下で run と評価を行う（元の `runs/` を上書きしない。**`--out` が元の成果物と同じ基点なら拒否する**）。
5. `run_id` と `result_digest` を記録と比べ、判定を `--out` の下の `reproduction.json` に書く（手順2 の `ENVIRONMENT_MISMATCH` と手順3 の `RUN_ID_MISMATCH` も同じファイルに書く）。判定は `REPRODUCED` / `RUN_ID_MISMATCH` / `RESULT_MISMATCH` / `ENVIRONMENT_MISMATCH` / `MANIFEST_TAMPERED` の5値。

### 21.3 コマンドと終了コード【提案】

段階4 で足すコマンドは `experiment` の下の3つ（`run` / `reproduce` / `report`）である（ADR-0028 の argparse のまま）。既存の5コマンドのうち `data accept` / `classify` / `approve` は変えない。**`run` は書式 v2 も受け（第18.5節）、`evaluate` は `--calendar` を必須の引数として足す**（第4.1節。Q8 決定）。

| コマンド | 入力 | 終了コード |
|---|---|---|
| `odyssey-fx experiment run --experiment <v2 の YAML> --snapshots <基点> --out <基点>` | 実験設定 v2 | 0 = `COMPLETED`（run や評価そのものが失敗でも、実験としては最後まで進んだ）、3 = `REJECTED_BY_POLICY`、4 = `FAILED_POST_RUN_CHECK`、5 = 記録票の内容違い、または既存の run 成果物と衝突して再利用できないため拒否（第19.6節）、2 = 設定の誤り（`ConfigError`） |
| `odyssey-fx experiment report --experiment-dir <実験の版のディレクトリ>` | 記録票と、あれば結末記録・評価の成果物 | 0 = レポートを書いた、2 = 引数・読込の誤り（第22.1節） |
| `odyssey-fx experiment reproduce --experiment-dir <実験の版のディレクトリ> --snapshots <基点> --out <別の基点>` | 記録票と結末記録 | 0 = `REPRODUCED`、6 = それ以外の判定（判定は `reproduction.json` と表示に出す）、2 = 引数・読込の誤り |

- run や評価の失敗を終了コード 0 にするのは、それらが「失敗を説明する成果物が揃った」状態であり、コマンドの失敗ではないためである（段階2 の `evaluate` コマンドが `FAILED` の評価でも成果物を書いて終わるのと同じ考え方）。状態は結末記録とレポートに出る。

### 21.4 段階4 の完了条件の検証【提案】

受入テストが、人工データで `experiment run` を `subprocess` で1回起こし、続けて `experiment reproduce` を**別の `subprocess`** で起こして判定が `REPRODUCED` であることを確かめる（D08 v1.9 §2.3）。第1.2節の行6 が「定義は本書、検証は D08」と分けた分担のとおりである。

### 21.5 範囲外【提案】

別の機械（環境のダイジェストが違う）での再現は段階4 の範囲外とする。その場合に何を比べるか（`run_id` の代わりに `ConfigDigest` と判断履歴の内容を比べる等）は、必要になった時点で本書の後続版が決める。

## 22. 人間向けレポート【提案】

### 22.1 何を出すか【提案】

`evaluation.adapters.report` が、保存済みの成果物（記録票・結末記録・評価 manifest・評価5表・run manifest）**だけ**から、実験ごとに Markdown のファイル1つ（`runs/experiments/<experiment_name>/v<version>/report.md`）を作る。`experiment run` の最後に作る。**再実行のときは、旧い結末記録を退避する（第19.3節）のと同時に、旧い `report.md` も `report.<n>.md`（結末記録と同じ連番）へ退避する**。そのため、今回の実行が途中で止まると、同じ版のディレクトリには記録票だけが残り、レポートも結末記録も無い。この状態のレポートは、**`odyssey-fx experiment report --experiment-dir <実験の版のディレクトリ>`** で記録票だけから作り直せる（先頭に「結末記録が無い＝途中で止まった」と書く）。このコマンドは保存済みの成果物を読むだけで、既存の `report.md` があれば内容が同じなら何もせず、違えば退避してから書く。

| 順 | 見出し | 内容 |
|---|---|---|
| 1 | 結論 | 実験の状態（`ExperimentStatus`）、run の状態、評価の状態を1行ずつ。**採用できる結果かどうか**を先頭の1文で書く（`COMPLETED` かつ評価 `COMPLETED` のときだけ「採用可」） |
| 2 | なぜそうなったか | 合格でない研究ポリシーの検査・整合検査（読めなかったものを含む）を、検査名・期待値・観測値で全件。run の失敗理由（`run_failure_reason`）。**0取引のときは「取引が0件だったので値なしの指標がある」と書く** |
| 3 | 値なしの指標 | 指標名と理由（`MetricUnavailableReason`）の一覧 |
| 4 | 指標 | 指標集合の全件を、採用指標と参考値に分けて、注記（`caveats`）付きで |
| 5 | 集計 | 集計8種（第6.1節・第6.3節） |
| 6 | 設定と入力の特定 | `experiment_id`、仮説、研究ポリシーの版、snapshot、`run_id`、`ConfigDigest`、コード・lock・環境のダイジェスト、git の状態、**再現のコマンドの1行** |

- 「失敗 / 0取引も説明できる」（全体計画 §8.2）の人間向けの出力先が本節である。状態と理由を**先頭**に置くのは、数値の表だけを見て失敗に気づかない読み方を防ぐためである。
- **表示の桁**【提案】: 比率は小数第6位まで `ROUND_HALF_EVEN` で表示し、金額は保存値をそのまま表示する。**保存値は変えない**（第5.1節の Q2 決定: 表示の桁は報告の関心）。
- **決定論**: 壁時計の時刻・絶対パスを入れない。同じ成果物からは同じバイト列のレポートが出る。レポートは結果ダイジェストの対象に**入れない**（成果物から導いた表示であり、正本ではない）。
- **不採用**: HTML で出す案（体裁は良くなるが、差分をテキストで読めず、依存が増える）、評価ごと（`runs/<run_id>/eval/<id>/`）に置く案（研究ポリシーの結果と記録票は実験の側にあり、評価の側からは見えない）。

## 23. 実データでの実行【提案】＋【合意済み】（2026-09-25 の人間の決定6）

### 23.1 範囲【合意済み】

**検証戦略 A・USDJPY・研究履歴（2016〜2023）で1本の run を回す。CI は人工データのまま**（人間の決定6）。前提は snapshot `a498b8cf…` の承認（`approval` の記入）であり、その PR の merge 後に行う。

### 23.2 実行前のデータ能力検査に当たる制約【提案】（Q9 決定）

起草時に snapshot の検査報告を数えたところ、**執行系列 USDJPY 15分足に、データ欠損と分類した「存在すべき足の欠落」が研究履歴の中に376区間ある**（2016年66・2017年36・2019年43・2020年96・2021年96・2022年26・2023年13。**2018年は0**）。D06 §10.5 の手順2 は「執行に使う系列の欠落は、重大度が警告でも実行不可」と確定している（v1.3）ので、**2016〜2023 を1本の run にすると、実行前の能力検査で `FAILED_CAPABILITY` になる**。人間の決定6 の範囲と承認済みの D06 の規則がこの点で両立しないため、本書は規則を曲げずに要決定 Q9 として上げ、人間が次のとおり決めた。

**Q9 決定（2026-09-25）**: **決定どおり 2016〜2023 の1本を回して能力検査で止まることを記録し（「失敗も説明できる」の実データでの実例になる）、あわせて欠落の無い 2018 年で完走する1本を回す**。D06 §10.5 は変えない。実験設定は2つ（`configs/experiments/strategy_a_usdjpy_research_history.yaml` と `configs/experiments/strategy_a_usdjpy_2018.yaml`、どちらも書式 v2）とし、第23.3節の実行記録に両方を書く。

### 23.3 記録とテストでの扱い【提案】

- 実験設定は第23.2節の2つ（書式 v2）。仮説は「検証戦略 A の規則が研究履歴で、費用込みでどの程度の損益になるかを記録する（採否の判断はしない）」とする。**段階4 は採否を決めない**（選定は段階5・D09）。
- 実行の記録は `docs/runs/stage4_realdata_run.md`（新設）に、記録票の識別子・`run_id`・結末・主な指標・値なしの理由・能力検査の結果（欠落の件数）を書く。`runs/` の成果物は git 管理外のまま（D01 §10.3）で、文書が識別子で指す。
- 実データを使うテストは**マーカー `realdata`** を付け、`pyproject.toml` の既定で除外する（D01 §9「実データを使うテストは段階4以降に別マーカーで分ける」、D08 §12 の「最初のテストと同じ PR で決める」）。CI では実行しない。
- データ欠損と分類した区間（全銘柄で 8,140 区間）は、評価側で読み直さない（第4.1節の Q1 決定）。run manifest の能力検査の結果に残っているものを、レポートの「なぜそうなったか」が表示する。

## 24. 封印期間の許可判定を段階5 へ送った記録【合意済み】（2026-09-25 の人間の決定2）

- **決定**: 封印期間（2024〜2025、`LEGACY_HOLDOUT`）の partition を読むための許可判定（`holdout_gate`）と閲覧履歴、使用済み期間（`CONSUMED`）を研究用途で明示的に読む opt-in は、**本書（D07）ではなく段階5 の D09 で設計し実装する**。
- **理由**: 承認待ちの snapshot の旧基盤の閲覧履歴（`legacy_access`）は**0件**である。ADR-0014 の規則では履歴を確認できない partition は `SEALED`（未観測）にならず `CONSUMED`（使用済み）になるので、**未観測の封印期間は現状1件も存在しない**。段階4 の単一実行評価は研究履歴だけで完了条件を満たせる。
- **段階4 でどうなるか**: 読める期間は研究履歴だけのまま（D03 §3.8 v1.5 の合成の規則）。研究ポリシーの検査 P2（第20.3節）が同じことを記録票の段階で二重に確かめる。封印期間へ触れる経路は段階4 で1本も増えない。
- **追随した文書**: ADR-0014 の「影響」の `holdout_gate`（D07）を D09 と読む注記（改訂履歴に1行）、D03 §3.8・§6.1 の「gate の実装は D07」「段階4 で足す」を「D09・段階5」へ（D03 v1.12）、本書第2節のモジュール表・第12節・第14節。D01 §10.2 はモジュールの置き場所（`evaluation.application.holdout_gate`）を書いているだけで担当文書を書いていないため変えない。

## 25. 他文書への反映（本 PR で実施）

| # | 宛先 | 反映 |
|---|---|---|
| 1 | D08 → v1.9 | 第7.2節（D07 の意味論の行を5行から10行へ。新しい5行は PR 1・3 で実装する予定として「未整備」）、第2.3節（段階4 の受入テストの規約）、第10.5節（評価の golden を指標集合 v2 へ上げる方針）、第12節（実データのマーカー）、第1.2節の行3・行5・行6 の担当の更新 |
| 2 | 全体計画書 | §8.1 の D07 の行、§8.2 の段階4 の行（設計文書の版と PR 分割）、§5.5.1 の `domain.research_policy` の行（上限を置く決定の注記）、§10 の次のアクション |
| 3 | D03 → v1.12 | §3.8 と §6.1 の `holdout_gate` の担当を D09・段階5 へ（第24節） |
| 4 | D04 → v1.15 | §13.1 に、段階3 の宣言を書くキーの一覧が本書第18.4節にあることを1行足す（一般規則は変えない） |
| 5 | ADR-0014 | 改訂履歴に1行（`holdout_gate` の担当を D09 へ。決定の内容は変えない） |
| 6 | `agents_md_for_codex_review.md` §5 | R5 の行に「D07 v2.0 に取り込み」 |
| 7 | D06 §9.2（表9 の費用の列の段落） | 「D07 v0.2（段階4）」の呼び方を「D07 v2.0」へ、参照先を第7.3節へ（呼び方だけ。D06 の規則は変えない） |
| 8 | D01 → v2.7 | §10.3 の `runs/experiments/` の下のディレクトリ名を、人間が付けた実験の名前と版にする（`ExperimentId` は内容のダイジェストなので記録票の中に置く。第19.1節） |

**上位設計書（`fx_research_platform_greenfield_design.md`）は書き換えない**。§6 の「初期に上限は設けない」と人間の決定5 の差異は、第13節の差異4 として記録し、上位文書の次の改訂へ渡す（D08 §14.1 と同じ扱い）。

## 26. 段階4 の要決定（Q7〜Q13。2026-09-25 にすべて決定済み）

人間の6件の決定（第16.3節）に含まれない設計判断のうち、結果に影響するものを挙げた。番号は第16.1節の Q1〜Q6 に続けて振り、再利用しない。起草時は**各項目の選択肢1 を推奨**とした。**2026-09-25 に人間が7件とも決定した**。Q8〜Q13 は推奨どおり、**Q7 だけは推奨（選択肢1）ではなく選択肢3（検証戦略 B の約3倍）**が選ばれた。本文は決定後の内容である。採らなかった選択肢は下表に残す。

| # | 何を決めるか | 選択肢（起草時の推奨を先頭） | 結果への影響 | 本文の節 | 決定（2026-09-25） |
|---|---|---|---|---|---|
| **Q7** | **研究ポリシーの複雑性の上限の値**（部品数・使用箇所数・パラメータ数・判断を出す使用箇所の数） | **1. 検証戦略 B の約1.5倍**: 部品数 15・使用箇所 18・パラメータ 16・判断 9 / 2. 検証戦略 B ちょうど: 10・12・11・6 / 3. 検証戦略 B の約3倍: 30・36・33・18 | 1 は現行の2戦略が通り、部品を数個足す程度の新しい戦略も通る。段階5 の探索の前に見直す前提。2 は新しい部品を1つ足すだけで違反になり、上限の改訂が頻発する。3 は実質的に計測だけに近く、上限を置いた決定の意味が薄れる | 第20.2節・第20.4節 | **選択肢3（推奨ではない）**: 部品数 30・使用箇所 36・パラメータ 33・判断 18 |
| **Q8** | **年率化と日次の資産系列の「1日」の区切り**（取引カレンダーを評価の入力に加えるか） | **1. 取引カレンダーを4つめの入力に加える**（run manifest の `calendar_ref` と一致を検査。NY17 時の取引日で区切り、年率化は年 260 取引日）/ 2. 判断履歴だけで数える（UTC 0時で区切り、年率化は年 365 暦日）/ 3. 日次系列を使う指標（シャープレシオ）を段階5 へ送り、年率化リターンだけ run 区間の暦日数で出す | 1 は FX の取引日（日曜 17時〜金曜 17時 NY、休場を除く）で数えられ、週末の0リターン日が入らない。入力契約（Q1 決定の3つ）が1つ広がる。2 は入力契約を変えないが、週末の0リターン日と日曜夕方の数時間が1日として入り、シャープレシオが小さく出る。3 は段階4 の指標が1件減る | 第5.5節・第4.1節 | **選択肢1（推奨）** |
| **Q9** | **実データ実行の範囲**: 研究履歴全体の run は、執行系列の欠落 376 区間で実行前の能力検査に止められる | **1. 決定どおり 2016〜2023 の1本を回して能力検査で止まることを記録し、欠落の無い 2018 年で完走する1本を足す** / 2. 範囲を 2018 年だけに変える（1本）/ 3. D06 §10.5 を改訂し、データ欠損と分類した区間を通す規則を足す（その区間は新規発注と約定判定を止める等） | 1 は決定の範囲を守りつつ、失敗の説明と完走の評価の両方を実データで示す（run は2本になる）。2 は完走の1本だけで、失敗の実例は人工データだけになる。3 は D06・D05 の意味論の改訂が要り、段階4 が半日〜1日延びる | 第23.2節 | **選択肢1（推奨）**: 2016〜2023 の1本（能力検査で止まる記録）＋ 2018 年の1本。D06 §10.5 は変えない |
| **Q10** | **取引の勝敗の基準**: 入場費用を含めた取引損益で判定するか | **1. 入場費用込みの取引損益（`trade_profit`）で判定する** / 2. 従来どおり建玉の確定損益（`realized`。決済側の費用だけを含む）で判定する | 1 は勝率・プロフィットファクター・平均取引損益が同じ損益の定義に揃う。決済側だけ見ると僅かな勝ちが、入場の手数料を含めると負けになる取引の分類が変わる。2 は指標ごとに損益の定義が分かれる | 第7.3節 | **選択肢1（推奨）** |
| **Q11** | **戦略宣言の置き場所** | **1. 別ファイル（`configs/strategies/`）に置き、実験設定からパスで指す** / 2. v1 と同じく実験設定の中に書く | 1 は同じ戦略を複数の実験（遅延シナリオ4ケースなど）で1か所に保てる。ファイルが2つになる。2 は1ファイルで完結するが、4ケースで戦略の本文が4回重複する | 第18.4節 | **選択肢1（推奨）** |
| **Q12** | **書式 v1 の扱いと、再現で環境が違うときの扱い** | **1. v1 の読込を残し（`run` は v1・v2、`experiment` は v2 だけ）、再現は環境のダイジェストが1つでも違えば run しない** / 2. v1 の設定を v2 へ書き換えて v1 の読込を消し、再現は環境が違っても run して `ConfigDigest` で比べる | 1 は段階2 の受入テストの入力を変えずに済み、再現の判定が `run_id` の一致1つで閉じる。2 は読込が1つになるが、段階2 の受入テストの入力を作り直し、環境違いの比べ方を今決める必要がある | 第18.5節・第21.2節 | **選択肢1（推奨）** |
| **Q13** | **待機をはさんだ評価要求の数え方** | **1. 集計に「要求ごとの最終の結果区分」（要求単位）を1種足す。既存の結果区分の集計は記録単位のまま** / 2. 既存の集計を要求単位に改める / 3. 数え方を足さない（記録単位の集計だけ） | 1 は段階3 で確定した集計を変えずに、待機で決着した要求の数を読める。golden に8種目の行が増える。2 は段階3 の確定（D07 v1.4 §6.1）の改訂になる。3 は待機が指標にも集計にも現れない | 第6.3節 | **選択肢1（推奨）** |


## 27. 本書の版と承認の単位（改訂履歴）

**呼び方について**: 本書の旧版の本文と他文書は、段階4 の改訂を「**本書 v0.2**」と呼んでいた。版番号は v1.4 まで進んでいたため、**段階4 の改訂の版を v2.0 とし、「v0.2」は本改訂を指すもの**として読む（D05 §14 と同じ扱い）。本 PR で、他文書に残っていた「D07 v0.2」の呼び方を「D07 v2.0」へ揃えた（全体計画書 §8.1、D08 §1.2、D06 §9.2。過去の経緯を書いた箇所は残す）。本書の第1〜16節の本文に残る「本書 v0.2」は、段階2 の時点で書いた予告の記録としてそのまま残す（本改訂が実施した先は第1.2節の表で引ける）。

| 版 | 範囲 | 状態 |
|---|---|---|
| v0.1〜v1.4 | 段階2 の単一実行評価の境界（第1〜16節）。v1.4 は段階3 の判断履歴を読んだときの影響（T02） | **承認済み**（2026-09-21、PR #17。以後の改訂は人間の決定で本文へ確定） |
| **v2.0**（本改訂） | 段階4 の範囲（第1.2節の「段階4 で本改訂（v2.0）が決めること」の列）。指標集合 v2、取引単位の費用、要求単位の集計、検査結果の3区分（R5）、書式 v2、実験の記録票と結末記録、研究ポリシー v1、別プロセスでの再現、レポート、実データでの実行、封印期間を段階5 へ送った記録 | 人間の決定6件と Q7〜Q13 の決定（いずれも 2026-09-25）を反映。PR #40 の必須表の空欄のうち本書担当の4件を埋めた。承認待ち |
| v2.1 | 第18節の細則の確定（書式 v2 の実装 PR 2 で人間が決めたもの。冒頭の v2.1 の段落） | 人間の決定（2026-09-25、PR #45） |
| v2.2 | 実装 PR 1（PR #44）の人間の決定: 整合検査 C13 の追加（第10.4節・第10.1.1節）、仮置き6件の確定（第10.5節） | 人間の決定（2026-09-25） |
