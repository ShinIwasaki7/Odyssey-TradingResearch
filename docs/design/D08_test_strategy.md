# D08: テスト戦略（`tests/` の層・意味論テスト一覧・人工データ生成仕様・golden の運用）

作成日: 2026-09-22
状態: **承認（2026-09-23、PR #23）v1.0**。第14節の要決定 Q1〜Q3 を人間がすべて決定し（3件とも提示時の推奨案である選択肢1）、本文へ反映した。**未決の項目は残っていない**。決定に伴い、**同じ PR で正本を1件改訂した**: テスト配置表に統合（`tests/integration/`）と受入（`tests/acceptance/`）の2層を足す改訂（[D01](D01_architecture_and_dependency_rules.md) §9、v2.5。Q2 の決定）。
v1.1（2026-09-23、PR #24）: 戦略ランタイム設計（D05）の要決定 Q24 に対する人間の決定（選択肢1）を受け、**未整備を1件足した**（第13.2節の #6、合計 10件 → 11件）。確認待ちのあいだに市場状態が失効する経路が検証戦略 B では構造的に到達しないと分かったため（紙上トレース [T02](../traces/T02_paper_trace_strategy_b.md) §6）、**取引機会の有効性の再検査の4つの結末を、確認期限だけを長くした小さな戦略で意味論テストする**ことになった。本文の他の節は変えていない。
v1.2（2026-09-24、段階3 実装 PR 1/5）: 段階3 実装の PR 分割が人間に承認されたことを受け、**第9.6節（遅延シナリオ4ケースへの生成器の拡張方針）を【提案】から【合意済み】へ改めた**。あわせて、紙上トレース [T02](../traces/T02_paper_trace_strategy_b.md) §16 が新しく必要とした拡張（日足を1時間足から集約する入り口）を第9.6節に1行足した。拡張1・2 と日足集約の入り口は同じ PR で実装した（`tests/fixtures/synthetic/market.py` の `apply_delay`、`tests/fixtures/acceptance/t02_market.py`）。本文の他の節は変えていない。
v0.1（2026-09-22 起草）: 全体計画書 §8.4「テスト戦略（D08 で具体化）」を、**段階1〜2 で実際に書かれたテスト資産を正本として**具体化する。新しいテストを書く文書ではなく、**すでにあるものを記述し、足りないところを名指しする**文書である。第13節に未整備10件の一覧、第14節に決定の一覧を置く。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.7.15 E・§7.2、[全体計画書](fx_research_platform_overall_plan.md) §8.2・§8.4、[D01](D01_architecture_and_dependency_rules.md) §6・§9、[D02](D02_common_kernel.md) §11、[D03](D03_marketdata_and_time.md) §11、[D04](D04_strategy_declarations.md) §12、[D05](D05_strategy_runtime.md) §11、[D06](D06_backtest_vertical_slice.md) §11・§9.1・§10.1、[D07](D07_single_run_evaluation.md) §9・§11、[T01](../traces/T01_paper_trace.md)、ADR-0004（依存規則の機械検査）、ADR-0006（決定論的 ID）、ADR-0012（Decimal / float 境界）、ADR-0014（期間のアクセス分類）、ADR-0024（時間足の集約規則）、ADR-0027（成果物は Parquet 表＋JSON マニフェスト）
対応段階: 段階1〜。段階3（遅延シナリオ4ケース・検証戦略 B）の拡張方針は第9.6節に置き、v1.2（2026-09-24）で確定した。

## 0. 本書の位置付けと凡例

テストの**層をいくつ持ち、どの層に何を書き、どこに置き、どう名付けるか**を決める。個々のテストの中身は決めない（それは各設計文書の契約が決める）。

本書の特徴は、**記述の根拠を「設計文書がこう書いたから」ではなく「実際に `tests/` にこう書かれているから」に置く**ことである。段階1〜2 の実装（PR #14〜#20）で 1,100 件を超えるテストが既に書かれており、そこに現れた規約が事実上の正本になっている。本書はそれを言葉にし、**設計文書の契約と実際のテストの対応が切れている箇所を名指しする**（第4〜7節の対応表と、第13節の未整備の一覧）。

凡例は全体計画書第0節に従う。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 承認済みの設計文書・ADR で確定済み、または**既存のテスト資産がすでにそうなっている**事実の記述。本書で再議論しない |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 承認時にユーザーが選択する事項。**Q1〜Q3 は 2026-09-23 にすべて決定済み**で、本文は決定後の内容になっている。**未決の項目は残っていない**。第14節に決定の一覧を置く |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | テストの層の責務・命名・書き分けの判断（第2〜3節）、意味論テストと契約行の対応表（第4〜7節）、人工データ生成仕様（第9節）、golden の運用（第10節）、決定論の検証（第11節）、CI の並び（第12節） | 【提案】 |
| 決めないもの | 個々の契約の内容（D02〜D07 が正本）、指標や状態機械の正しさ（同）、実データの受入れ手順（D03 §4）、複数 run の評価（D09）、性能・負荷の測定 | 【合意済み】 |
| 変えないもの | 既存のテストの中身。**本書は記述であって指示ではない**。記述と実際が食い違う箇所は第13節に「未整備」として挙げ、直す PR は本書の承認後に別に立てる | 【提案】 |

### 1.1 語彙と規則の正本がどの文書にあるか【提案】

D04〜D07 と同じ方針を引き継ぐ。同じ語彙を2か所に定義しない。

| 事項 | 正本 | 本書の扱い |
|---|---|---|
| テストの配置（ディレクトリと層の対応） | **D01 §9**（本書は決めない） | 参照のみ。本書 §2.1 は7層を**記述**するだけで、置き場所を新たに定めない。**D01 §9 に行が無かった2層（統合・受入）は、Q2 の決定により同じ PR で D01 §9 へ追加した**（D01 v2.5）。これで D01 §9 は7層すべてを持つ |
| 依存規則の契約名（L1・L2a〜L2c・F1a〜F8） | D01 §6 | 参照のみ。検査の置き場所だけを第7節で決める |
| 各契約の意味論（何が正しい振る舞いか） | **上位設計書 §7.2・§4.7.15 E**（起点）と、それを受けた D02〜D07 の各節 | 参照のみ。本書は**どのテストがどの契約行を押さえているか**の対応だけを書き、契約を足さない（第4〜7節） |
| trace の15表・保存形式・平坦化の規則 | D06 §9.1・§9.2 | 参照のみ。golden の期待値の形式はこれに従う（第10節） |
| 結果ダイジェストと決定論の条件 | D07 §9 | 参照のみ。検証の仕方だけを第11節で決める |
| 期間のアクセス分類 | ADR-0014・D03 §3.8 | 参照のみ。**人工データはこの分類の対象外**とする（第9.5節。Q3 の決定）。ADR-0014 は変えない |
| 紙上トレースの数値 | [T01](../traces/T01_paper_trace.md) | 参照のみ。受入テストと golden の期待値の出どころ（第9.4節・第10節） |

### 1.2 本書が決めること・後続に委ねること【提案】（**レビュー対象範囲の正本**）

**本書のレビューの対象範囲は下表の第3列である**。第4列に属する指摘は「対象外（担当へ）」として記録し、本書では直さない。D04 §1.2・D05 §1.2・D06 §1.2・D07 §1.2 と同じ例外を置く。**本書の文が第4列の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。第4列の内容を本書に書き足すことはしない。

| # | 領域 | 本書 v0.1 が決めること（対象内） | 後続が決めること（対象外・担当） |
|---|---|---|---|
| 1 | 層の定義 | 7層（単体・意味論・プロパティ・golden・統合・受入・依存規則）の**責務・命名・どの層に書くかの判断**（第2.2節・第3節）。**置き場所（ディレクトリと層の対応）の正本は D01 §9 であり、本書は決めない**。Q2 の決定により、**統合・受入の2層を D01 §9 へ足す改訂を同じ PR に含めた**（D01 v2.5） | 性能テスト・負荷テスト・変異テストを層として持つか（**本書 v0.2・段階4以降**） |
| 2 | 意味論テストの対応 | **棚卸しの起点である上位設計書 §7.2（7項目）と §4.7.15 E（8項目）**、および D03〜D07 の各文書が挙げた**意味論の契約行**と、実在するテストの対応、および未整備の名指し（第4〜7節）。**下位文書に再掲されていない上位の契約も対象内**である（D01 §9・全体計画 §8.4 が意味論層の対象を上位2節と定義しているため） | **契約行そのものの内容**（上位設計書と D02〜D07 が正本。本書は対応を書くだけで契約を足さない）。段階3 の契約行（待機・追い越し・後続確認・再発火）との対応（**段階3・D05 v0.2 の承認後に本書 v0.2**）。評価窓外の採点と選定（**段階5・D09**） |
| 3 | 人工データ | 2つの生成器の役割分担、**現に実装されている入力と不変条件の記述**（第9.1〜9.2節）、**未実装の2条件（gap・SL/TP 同時到達）の入り口と不変条件の仕様**（第9.3節。実装は後続 PR）、日付の扱い（第9.5節）、段階3 の遅延シナリオ4ケースへの**拡張方針**（第9.6節） | **遅延シナリオ4ケースの具体的な中身**（**段階3・D05 v0.2 と D03 §3.6**）、実データを使うテストのマーカー設計（**段階4**）、第9.3節の仕様の**実装そのもの**（本書の承認後の別 PR） |
| 4 | golden | 期待値の置き場所・形式・更新条件・差分の読み方・承認（第10節） | 検証戦略 B の golden（**段階3**）、複数 run を並べた比較（**段階5・D09**） |
| 5 | 決定論 | 再実行一致の検証をどの層で行うか、比較する対象（第11節） | **別プロセスでの再現手順そのものの定義**（**D07 v0.2・段階4**。D07 §1.2 行6 と同じ分担） |
| 6 | CI | テストの実行の並びと前提（第12節）。既存の `.github/workflows/ci.yml` の記述 | CI の並列化・キャッシュ・実行時間の目標（**本書 v0.2**）、実データを使うジョブの分離（**段階4**） |

**受け取るもの**（本書が再定義しないもの）:

| 出どころ | 受け取るもの |
|---|---|
| D01 §9 | テストの配置表（6行）と「外部データ・ネットワークに依存しない」規則 |
| D01 §6 | 依存規則の契約名と、`lint-imports` が空虚な契約でも成功するため契約定義そのものを検査するという方針 |
| 上位設計書 §7.2・§4.7.15 E | **意味論層が対象とする契約の正本**（7項目＋8項目）。第4.1節と第7.1節の対応表の左列 |
| D02 §11・D03 §11・D04 §12・D05 §11・D06 §11・D07 §11 | 各文書が上位2節に足したテストの種別と項目（第5〜7節の対応表の左列） |
| D06 §9.1 | 判断履歴の保存形式と平坦化の規則（golden の期待値の形式） |
| D07 §9 | 決定論の3条件と `result_digest` の定義 |
| T01 | 受入テストと golden の期待値の数値 |

## 2. テストの層【合意済み】（既存資産の記述）

### 2.1 層の一覧

`tests/` には現在7つの層がある。件数は `def test_` の数（`parametrize` の展開前、2026-09-22 時点）。

| 層 | 置き場所 | 責務 | ファイル数 | 件数 |
|---|---|---|---|---|
| **単体** | `tests/unit/<package>/` | 型の不変条件、計算規則、状態遷移の許可と拒否を**1つの関数・1つのクラスの範囲で**確かめる。パッケージ構成をミラーする | 47 | 885 |
| **意味論** | `tests/semantics/<package>/` | 上位文書 §7.2・§4.7.15 E の項目を**1件1テストで名前を付けて固定**する。複数の部品をまたぐ振る舞いを、設計文書の契約行と1対1で対応させる | 5 | 115 |
| **プロパティ** | `tests/property/<package>/` | 不変条件を `hypothesis` で確かめる（先読み不変、単調性、台帳の整合、冪等性、決定論） | 7 | 52 |
| **golden** | `tests/golden/<package>/` | 人工データでの**固定出力**との突合。期待値は `expected/` にテキストで置く | 2 | 7 |
| **統合** | `tests/integration/<package>/` | 複数の層を通した経路を確かめる。**内部の関数を直接呼んでも、コマンドを経由してもよい**。T01 の8経路（`test_t01_paths.py`）は関数を直接呼び、受入れコマンドの経路（`test_data_cli.py`）はコマンドを通す | 3 | 60 |
| **受入** | `tests/acceptance/` | **段階の完了条件そのもの**を、人工データを受入れから評価まで1本に通して確かめる | 1 | 20 |
| **依存規則** | `tests/architecture/` | `lint-imports` の実行と、**契約定義そのもの**の検査（D01 §6） | 4 | 12 |

補助として `tests/fixtures/` があり、これはテストではなく**テストが使う道具**である（第9節）。

**統合（`tests/integration/`）と受入（`tests/acceptance/`）の2層は、段階2 の実装で必要になって追加されたもので、D01 §9 のテスト配置表に行が無かった**。Q2 の決定（2026-09-23、選択肢1）により、**この2行を D01 §9 へ足す改訂を本書の承認と同じ PR に含めた**（D01 v2.5）。**テスト配置の正本は D01 §9 の1か所**であり、本書はそれを参照して責務・命名・書き分けの判断を足す。

### 2.2 どの層に書くかの判断【提案】

同じ振る舞いを2つの層に書くと、片方を直し忘れる。次の順に上から見て、**最初に当てはまる層1つに書く**。

1. **設計文書が「意味論」として名前を付けて挙げている項目**か → 意味論。契約行と1対1にする。
2. **すべての入力について成り立つ性質**か（順序を入れ替えても、値を変えても） → プロパティ。
3. **出力の形そのもの**を固定したいか（表の全行、集約した足の列） → golden。
4. **1つの関数・1つの型の範囲**で閉じるか → 単体。
5. **段階の完了条件そのもの**か（全体計画 §8.2 の表の文言に対応するもの。成果物の置き場所、再実行一致、手計算との一致） → 受入。
6. **複数の層を通した経路**を確かめたいか → 統合。

**統合と受入の違いはコマンドを使うかどうかではない**。どちらもコマンドを経由してよい（実際に
`tests/integration/app/test_data_cli.py` はコマンドを通す）。違いは**何を主張するか**である。
受入は「**段階の完了条件を満たした**」と主張し、そのために受入れから評価まで1本に通す。
統合は「**この経路がこう動く**」と主張し、通す範囲は経路ごとに決める。
判断の順で受入を先に置いたのは、完了条件に当たるものが統合へ流れないようにするためである。
7. **依存の向きや契約の定義**か → 依存規則。

**例外**: warmup 中の注文ゼロのように**段階の完了条件でもあり契約行でもある**項目は、意味論と受入の両方に置いてよい【合意済み】（実際に `tests/semantics/backtest/test_engine_semantics.py::test_no_order_is_placed_while_the_warmup_is_incomplete` と `tests/acceptance/test_stage2_completion.py::test_no_order_is_placed_during_the_warmup` の両方にある）。**この重複は意図的**で、片方は契約の固定、もう片方は完了条件の証明という別の役割を持つ。

## 3. 命名【合意済み】（既存資産の記述）

### 3.1 ファイル名

| 層 | 形 | 実例 |
|---|---|---|
| 単体 | `test_<モジュール名>.py`（パッケージ構成をミラー） | `tests/unit/common/test_money.py`、`tests/unit/marketdata/test_calendar.py` |
| 意味論 | `test_<対象>_semantics.py` / `test_<概念>_<名詞>.py` | `tests/semantics/backtest/test_engine_semantics.py`、`tests/semantics/strategy/test_opportunity_transitions.py` |
| プロパティ | `test_<対象>_properties.py`（決定論だけ例外） | `tests/property/common/test_money_properties.py`、`tests/property/evaluation/test_determinism.py` |
| golden | `test_<対象>_golden.py` | `tests/golden/evaluation/test_evaluation_golden.py` |
| 統合 | `test_<対象>_<経路の呼び名>.py` | `tests/integration/backtest/test_t01_paths.py`（関数を直接呼ぶ）、`tests/integration/app/test_data_cli.py`（コマンドを通す） |
| 受入 | `test_<段階>_completion.py` | `tests/acceptance/test_stage2_completion.py` |
| 依存規則 | `test_<検査対象>.py` | `tests/architecture/test_import_contracts.py`、`tests/architecture/test_contract_definitions.py` |

### 3.2 関数名

**英語の平叙文**で、「何が起きるか」を書く。`test_` の後ろは主語から始め、実装の名前ではなく**振る舞い**を書く【合意済み】。

- 良い例（実在）: `test_a_rejected_attempt_leaves_no_order_and_no_reservation`、`test_an_unfinished_daily_bar_is_not_visible`、`test_injecting_a_delay_moves_only_the_availability_not_the_ohlc`
- 状態遷移は**遷移番号を名前に入れる**（設計文書の遷移表と突き合わせるため）: `test_transition_1_a_firing_within_the_limit_opens_an_opportunity` 〜 `test_transition_11_confirmed_can_expire`
- 紙上トレースの経路は**経路番号を入れる**: `test_path1_fills_at_the_paper_trace_price` 〜 `test_path8_keeps_the_open_position_and_values_it`

### 3.3 docstring【提案】

**意味論テストは、1行目に検証する契約の出どころを書く**【合意済み】（既存の `tests/semantics/backtest/test_engine_semantics.py` はほぼ全関数がそうなっている）。形は `D0x §y.z: <契約の要約>。` または `ADR-00xx: <決定の要約>。`。

- 実例: `"""D06 §5.2: 受付前拒否は注文も予約も作らない。"""`、`"""ADR-0006: `RunId` は完全入力のダイジェスト。違えば1行も書かずに止める。"""`

この1行が**第4〜7節の対応表の機械可読な根拠**になる。意味論テストに出どころの1行が無いものは、対応表で追えないため**未整備として扱う**【提案】。他の層の docstring は任意とし、性質の定義や手計算の根拠を書きたいときに書く。

## 4. 意味論テスト一覧: 表の読み方【提案】

**棚卸しの起点は上位設計書の2節である**【合意済み】D01 §9・全体計画 §8.4。両文書は意味論層の対象を「**上位文書 §7.2・§4.7.15 E の各項目**」と定義している。D02〜D07 の §11 はその再掲であって起点ではない。**下位文書に再掲されなかった上位の契約は、検証されていなくても「未整備」として検出されない**ため、本書は上位2節から始めて下位文書へ降りる。

- **上位 §7.2（優先する検証、7項目）** → 第4.1節の表。
- **上位 §4.7.15 E（実装時の意味論検証、8項目）** → **D06 §11 がそのまま再掲している**ため、第7.1節の表の #1〜#8 がこれに当たる（同節に対応を明記する）。
- **D03〜D07 の §11・§12 が足した契約行** → 第5〜7節の表。

第4.1節と第5〜7節は、いずれも**契約行を左列に置き、それを検証している実在のテストを右列に書く**。これが全体計画 §8.4 の「意味論テスト一覧」の具体化である。

状態は3つ。

**契約行の充足は層を問わない**【決定 2026-09-23、Q1】。契約行が要求するのは「**その振る舞いが、名前の付いたテスト1件で固定されていること**」であり、そのテストが `tests/semantics/` にあるか `tests/unit/` にあるかは問わない。したがって**本書の対応表が、契約行の充足を示す索引の正本**である。

この読み替えを採ったのは、契約行を押さえるテストが**実在して緑になっている**のに、置き場所だけを理由に「未整備」と数えるのは実態に合わないためである。足りないのは検証ではなく索引であり、それは本書の表で埋まる。**不採用**: 約90件のテストを `tests/semantics/` へ移して層と契約行を1対1にする案（Q1 の選択肢2。移動で変更履歴が切れる割に、得られるのは同じ索引）、対応表を作らない案（同 選択肢3。全体計画 §8.4 が求める「意味論テスト一覧」が無いままになる）。

**新しいテストをどの層に書くか**は、層と契約行の対応が緩んだぶん、第2.2節の判断に委ねる。

状態は3つ。

| 状態 | 意味 |
|---|---|
| **対応あり** | その契約行を押さえるテストが**層を問わず**実在し、本表がその名前を指している |
| **対応あり（層は意味論の外）** | 同上。ただしテストが `tests/semantics/` 以外にある。**契約行としては充足している**。どこにあるかを追えるように区別だけする |
| **未整備** | その契約行を押さえるテストが見当たらない |

範囲の外にある項目は**「対象外（担当へ）」**と書き、担当の段階・文書を添える。未整備とは区別する。

### 4.1 上位設計書 §7.2（優先する検証）の7項目

| # | 契約行（上位 §7.2） | 状態 | テスト |
|---|---|---|---|
| 1 | 将来のデータを追加しても、過去時点の部品出力・意思決定が変わらない | **未整備** | **市場データの見え方までは対応あり**: `tests/property/marketdata/test_asof_properties.py::test_adding_future_bars_does_not_change_the_latest_available`、`::test_every_returned_bar_was_already_available`。**部品の出力と意思決定まで通した先読み不変のテストが無い**（同じ run を、未来の足を足した入力でもう一度回して trace が一致することを確かめるもの） |
| 2 | 上位足確定直前/直後、同時刻の close/open、DST・週末で情報の可視性が正しい | 対応あり | `tests/semantics/marketdata/test_asof_semantics.py::test_an_unfinished_daily_bar_is_not_visible`、`::test_a_bar_is_invisible_before_its_availability`、`::test_the_freshness_reference_of_a_confirmed_bar_is_its_end`。DST 週は `tests/golden/marketdata/expected/` の4件、週の開閉は `tests/unit/marketdata/test_calendar.py`（29件） |
| 3 | Trigger の記憶、発火水準の固定、後続確認、期限切れ、再発火が宣言どおり | **一部は対象外（段階3・D05 v0.2）** | 段階2 の範囲（発火・上限・置換・終端）は `tests/semantics/strategy/test_opportunity_transitions.py` の遷移1〜11 が押さえる。**後続確認・待機・追い越し・再発火は D05 v0.2 の担当**であり、段階3 で本書 v0.2 の表に足す |
| 4 | warmup 中の注文がゼロ。欠損/鮮度切れから意図しない発注が起きない | 対応あり | warmup は第6.2節 #2・第7.1節 #9。欠損・鮮度切れは `tests/semantics/backtest/test_engine_semantics.py::test_a_missing_execution_bar_fails_the_run_and_cancels_pending_orders`、`::test_a_scheduled_candidate_bar_that_never_arrives_fails_the_run`、`::test_a_missing_expected_bar_on_an_executed_series_is_not_runnable` |
| 5 | **gap**、SL/TP 競合、コスト、数量丸め、通貨換算を手計算で検証できる | **未整備（gap のみ）** | SL/TP 競合は `tests/unit/backtest/test_execution.py`（17件、ADR-0030 の足内解決）。コスト・数量丸め・通貨換算は `tests/semantics/backtest/test_engine_semantics.py::test_the_conversion_rate_points_at_its_evidence_record`、`::test_an_account_currency_unlike_the_settlement_currency_blocks_the_run`、`::test_a_commission_in_another_currency_blocks_the_run` と、受入層の T01 検算（`tests/acceptance/test_stage2_completion.py::test_the_metrics_match_the_paper_trace_check_values`）。**gap だけ、生成器に入り口が無く（第9.3節）テストも無い** |
| 6 | 約定・現金・実現/未実現損益・MTM 資産の台帳が整合する | 対応あり | `tests/semantics/backtest/test_engine_semantics.py::test_the_ledger_identity_holds_at_every_snapshot`、`::test_the_open_position_is_valued_with_the_last_completed_close`、`::test_a_failed_run_records_the_ledger_state_after_the_cancellations`。受入層は `tests/acceptance/test_stage2_completion.py::test_the_balance_and_equity_path_matches_the_paper_trace` |
| 7 | 評価窓外の採点や test を使った選定がない。異常終了を成功結果として扱わない | **後半は対応あり。前半は対象外（段階5・D09）** | 異常終了: `tests/semantics/backtest/test_engine_semantics.py::test_a_failed_run_is_not_reported_as_completed`、`tests/unit/evaluation/test_evaluate_run.py::test_a_run_that_did_not_complete_is_rejected_without_metrics`。**評価窓・選定は複数 run の話であり、探索と分割を扱う D09（段階5）の担当** |

7項目のうち**対応あり3件、一部が対象外2件、未整備2件**（#1 の部品出力まで通した先読み不変、#5 の gap）。

**D02（共通カーネル）には意味論の行が無い**【合意済み】D02 §11。同書のテストは単体・プロパティ・アーキテクチャの3種で、値型と演算規則しか持たないため意味論の対象になる「複数部品をまたぐ振る舞い」が存在しない。これは欠落ではなく設計どおりである。対応は第7.3節（層ごとの置き場所）で示す。

## 5. 意味論テスト対応表: D03（市場データ・時刻基盤）

D03 §11 の意味論4行。

| # | 契約行（D03 §11） | 状態 | テスト |
|---|---|---|---|
| 1 | 未確定の上位足を参照できない | 対応あり | `tests/semantics/marketdata/test_asof_semantics.py::test_an_unfinished_daily_bar_is_not_visible` |
| 2 | 遅延注入で `Publication` だけが動き OHLC が変わらない | 対応あり | 同ファイル `::test_injecting_a_delay_moves_only_the_availability_not_the_ohlc`。通常の公開遅延は `::test_a_normal_publication_delay_hides_the_bar_until_its_scheduled_time` と `::test_a_normal_publication_delay_also_hides_the_bar_from_history` |
| 3 | 期待足未到着で古い足へ戻らない | 対応あり | 同ファイル `::test_a_missing_expected_bar_does_not_fall_back_to_an_older_one`、`::test_the_expected_latest_key_is_independent_of_arrival`、`::test_a_history_window_requires_every_expected_bar` |
| 4 | 範囲外 partition で `HoldoutAccessViolation` | 対応あり | 同ファイル `::test_reading_a_series_outside_the_allowed_partitions_is_a_structural_error`、`::test_a_quarantined_partition_may_never_be_granted_to_a_view`、`::test_the_execution_view_also_refuses_a_quarantined_partition` |

**D03 は意味論4行すべてに対応がある**。加えて、設計文書が挙げていない意味論テストが `tests/semantics/marketdata/` に多数ある（as-of 32件・承認関門 27件）。承認済み snapshot の関門（暫定ディレクトリ・未承認 manifest・未分類の警告・検査報告の改竄）は D03 §3.7.1 の内容で、**契約行としては §11 に挙がっていない**。第13節の未整備には数えないが、**D03 §11 の意味論の行が実態より少ない**ことは記録する（本書の指摘であり、D03 の改訂は本書の対象外）。

## 6. 意味論テスト対応表: D04・D05（戦略宣言・ランタイム）

### 6.1 D04（戦略宣言モデル）§12 の意味論3行

**`tests/semantics/` に D04 に対応するファイルは無い**。3行とも**契約行としては充足しており**（Q1 の決定）、テストは `tests/unit/strategy/` にある。

| # | 契約行（D04 §12） | 状態 | 実在するテスト（別の層） |
|---|---|---|---|
| 1 | 未宣言キーの拒否 | **対応あり（層は意味論の外）** | `tests/unit/strategy/test_declarations.py`（33件）に含まれる。例: `::test_an_unregistered_data_type_is_rejected`、`::test_an_allowed_input_event_must_name_a_delivered_event_input` |
| 2 | 役割の型不一致の拒否 | **対応あり（層は意味論の外）** | `tests/unit/strategy/test_compiler.py`（33件）に含まれる |
| 3 | 能力検査の各拒否が理由コード付きで返ること | **対応あり（層は意味論の外）** | `tests/unit/marketdata/test_schedule.py::test_a_seeded_random_delay_is_rejected_by_the_capability_check` ほか |

### 6.2 D05（戦略ランタイム）§11 の意味論4行

| # | 契約行（D05 §11） | 状態 | テスト |
|---|---|---|---|
| 1 | 上限到達時に `CONCURRENCY_LIMIT_REACHED` の機会が1件残ること | 対応あり | `tests/semantics/strategy/test_opportunity_transitions.py::test_transition_2_a_firing_over_the_limit_is_recorded_but_not_opened`、`::test_all_non_terminal_states_count_towards_the_limit` |
| 2 | warmup 中の注文ゼロ | 対応あり | `tests/semantics/strategy/test_evaluation_outcomes.py::test_no_order_is_produced_while_the_history_is_too_short`、`::test_a_half_warmed_strategy_opens_an_opportunity_without_proposing_an_order` |
| 3 | `Skipped` で状態が更新されないこと | 対応あり | 同ファイル `::test_a_skipped_evaluation_does_not_update_the_component_state`、`::test_a_skipped_evaluation_records_why_it_was_skipped` |
| 4 | 終端した機会が復活しないこと | 対応あり | `tests/semantics/strategy/test_opportunity_transitions.py::test_a_terminated_opportunity_is_never_revived`、`::test_nothing_is_accepted_after_the_run_end_batch` |

**D05 は意味論4行すべてに対応がある**。加えて、取引機会の状態機械の**遷移1〜11 がすべて名前付きで固定されている**（`test_transition_1_…` 〜 `test_transition_11_…`）。段階2 では発火しない遷移8・10・11 も「表現できること」として押さえてある（`::test_transition_8_is_expressible_even_though_stage_2_never_fires_it`）。これは D06 §5.1 が「どの設定でも発生しない遷移はどう検証するか」を明記した方針の実践であり、**本書はこのやり方を全層の規約として採る**【提案】: **段階の中で発火しない遷移も、表現できることを1件のテストで固定し、発火しない理由を docstring に書く**。

## 7. 意味論テスト対応表: D06・D07（バックテスト・評価）

### 7.1 D06（バックテスト基盤）§11 の意味論9行 ＝ 上位 §4.7.15 E の8項目 ＋ warmup

**#1〜#8 は上位設計書 §4.7.15 E の「実装時の意味論検証」8項目をそのまま再掲したもの**である（受付拒否で注文/予約が残らない、受付/約定の原子性、同じ `event_id` の再配送、終端後の別イベント、予約移管の二重計上、期限と open の同時刻、保護決済と通常決済の競合、末尾 MTM の非執行）。#9（warmup 中の注文ゼロ）は上位 §7.2 から来ている。したがって**この表が上位 §4.7.15 E の棚卸しを兼ねる**。

| # | 契約行（D06 §11） | 状態 | テスト |
|---|---|---|---|
| 1 | 受付拒否で注文と予約が残らない | 対応あり | `tests/semantics/backtest/test_engine_semantics.py::test_a_rejected_attempt_leaves_no_order_and_no_reservation` |
| 2 | 受付と約定の原子性 | **未整備** | 見当たらない。近いのは `tests/semantics/backtest/…::test_a_failed_run_records_the_ledger_state_after_the_cancellations`（失敗時の台帳）だが、**1判断時点の中で受付と約定が分割不能であること**は押さえていない |
| 3 | 同じ `event_id` の再配送 | **未整備** | `tests/integration/strategy/test_strategy_a_trace.py::test_a_batch_is_not_processed_twice` がバッチ単位の重複を押さえるが、**`event_id` 単位ではなく、層も統合**である |
| 4 | 終端後の別イベント | **対応あり（層は意味論の外）** | `tests/semantics/strategy/test_opportunity_transitions.py::test_nothing_is_accepted_after_the_run_end_batch`、`::test_an_admission_notice_for_an_unknown_opportunity_is_rejected` が**戦略側**にある。バックテスト側の意味論としては無い |
| 5 | 予約移管の二重計上 | **対応あり（層は意味論の外）** | `tests/unit/backtest/test_portfolio.py::test_a_transferred_reservation_is_not_counted_twice`、`::test_the_consumed_budget_counts_held_reservations`（**単体層**） |
| 6 | 期限と始値の同時刻 | **未整備** | `tests/unit/backtest/test_orders.py::test_transition3_expiry_records_the_reason` は期限切れそのもの。**期限到来と候補の始値が同時刻に並んだときの順序**は押さえていない。D06 §5.1 は「遷移3 はどの設定でも発生しない」と書いており、§6.2 の表現可能性テストの形で書くのが筋 |
| 7 | 保護決済と通常決済の競合 | 対応あり | `tests/semantics/backtest/test_engine_semantics.py::test_a_close_request_supersedes_a_protection_update_on_the_same_position`、`::test_the_end_of_run_rule_wins_over_the_close_collision` |
| 8 | 末尾の非執行 | 対応あり | 同ファイル `::test_the_decision_points_stop_at_the_run_end`、`::test_the_final_snapshot_comes_after_the_end_of_run_cancellations`、`::test_the_end_of_run_transitions_get_their_own_processing_points` |
| 9 | warmup 中の注文ゼロ | 対応あり | 同ファイル `::test_no_order_is_placed_while_the_warmup_is_incomplete`（受入層にも `tests/acceptance/test_stage2_completion.py::test_no_order_is_placed_during_the_warmup`。第2.2節の意図的な重複） |

**9行のうち、契約行として充足しているのは6行**（対応あり5行＝#1・#7・#8・#9 と、層が意味論の外の #5）。**未整備は3行**（#2 受付と約定の原子性、#3 同じ `event_id` の再配送、#6 期限と始値の同時刻）。#4 は戦略側の意味論層にあり充足している。

なお `tests/semantics/backtest/test_engine_semantics.py` には契約行に挙がっていない意味論テストが多数ある（全32件）。台帳の恒等式（`::test_the_ledger_identity_holds_at_every_snapshot`）、実行識別子の完全一致（`::test_a_run_id_unlike_the_complete_input_fails_before_anything_is_written`）、根拠の出どころ（`::test_the_evidence_rows_carry_their_provenance`、`::test_only_the_child_bars_actually_read_become_evidence`）などで、これらは D06 の他の節の契約を押さえている。

### 7.2 D07（単一実行評価）§11 の意味論5行

**`tests/semantics/evaluation/` は無い**。5行とも**契約行としては充足している**（Q1 の決定）。テストは `tests/unit/evaluation/test_evaluate_run.py`（51件）にある。

| # | 契約行（D07 §11） | 状態 | 実在するテスト（単体層） |
|---|---|---|---|
| 1 | `REJECTED` の run で指標を出さない | **対応あり（層は意味論の外）** | `tests/unit/evaluation/test_evaluate_run.py::test_a_run_that_did_not_complete_is_rejected_without_metrics` |
| 2 | 致命検査の不合格で指標を出さず検査表だけを出す | **対応あり（層は意味論の外）** | 同ファイル `::test_a_missing_table_is_a_fatal_check_not_an_exception`、`::test_a_trade_count_mismatch_is_fatal`、`::test_a_broken_id_chain_is_fatal`、`::test_a_close_side_chain_break_is_fatal` |
| 3 | 必須列が欠けた表と0行の表を区別する | **対応あり（層は意味論の外）** | 同ファイル `::test_a_missing_column_is_distinguished_from_an_empty_table` |
| 4 | 0件の鍵が行として出る | **対応あり（層は意味論の外）** | 同ファイル `::test_every_category_key_gets_a_row_even_at_zero`、`::test_the_rejection_categories_split_entry_from_close` |
| 5 | 通貨違いを拒否する | **対応あり（層は意味論の外）** | 同ファイル `::test_a_foreign_currency_column_is_fatal`、`::test_a_foreign_ledger_currency_is_reported_not_raised` |

**5行すべてにテストは存在する**。Q1 の決定により、これらは契約行を充足しているものとして数える。

### 7.3 意味論以外の層の置き場所【合意済み】

各設計文書の他の種別は次のとおり配置されている。

| 文書 | 単体 | プロパティ | golden | その他 |
|---|---|---|---|---|
| D02 | `tests/unit/common/`（11ファイル、265件） | `tests/property/common/`（4ファイル、39件） | — | アーキテクチャ: `tests/architecture/test_decimal_construction.py`（`Decimal(` の呼び出し位置、D02 §4.6） |
| D03 | `tests/unit/marketdata/`（13ファイル、247件） | `tests/property/marketdata/`（2ファイル、11件） | `tests/golden/marketdata/test_aggregation_golden.py`（5件） | 実データ受入れ（段階1 完了条件）は自動テスト外 |
| D04・D05 | `tests/unit/strategy/`（3ファイル、90件） | — （**未整備**。D04 §12・D05 §11 が挙げた「同じ宣言から同じ digest」「宣言順を入れ替えても評価順が変わらない」に対応するプロパティテストが無い） | — （**未整備**。D04 の「YAML → `CompiledStrategy` の固定出力」、D05 の「1突破分の trace の固定」が無い） | 統合: `tests/integration/strategy/test_strategy_a_trace.py`（13件） |
| D06 | `tests/unit/backtest/`（6ファイル、84件） | — （**未整備**。D06 §11 の「同一入力の再実行で trace が完全一致」「恒等式が全 snapshot で成立」のうち、恒等式は意味論層に、再実行一致は受入層にある） | — （**未整備**。D06 §11 の「検証戦略 A の1取引分の trace の固定」が無い） | 統合: `tests/integration/backtest/test_t01_paths.py`（21件、T01 の8経路） |
| D07 | `tests/unit/evaluation/`（4ファイル、90件） | `tests/property/evaluation/test_determinism.py`（2件） | `tests/golden/evaluation/test_evaluation_golden.py`（2件、5表） | 受入: `tests/acceptance/test_stage2_completion.py`（20件） |
| D01 | — | — | — | 依存規則: `tests/architecture/`（4ファイル、12件） |

## 8. 依存規則の検査【合意済み】（既存資産の記述）

D01 §6 の方針（`lint-imports` は契約が空虚でも成功するため、契約定義そのものにも検査を置く）がそのまま実装されている。

| ファイル | 責務 |
|---|---|
| `tests/architecture/test_import_contracts.py`（1件） | `lint-imports` を実行する。CI と同じ検査をローカルでも走らせる |
| `tests/architecture/test_contract_definitions.py`（5件） | `pyproject.toml` の `[tool.importlinter]` を直接読み、**layers 契約4件（L1・L2a〜L2c）がすべて `exhaustive = true`** であること、**forbidden 契約11件（F1a・F1b・F2・F3・F4・F5a・F5b・F5c・F6・F7・F8）がすべて存在する**こと、L2c の中間層の区切り（`\|`）の意味を取り違えていないこと、**設定パーサー集約ルール（F5c）の各フィールドの値**を固定する |
| `tests/architecture/test_decimal_construction.py`（2件） | `Decimal(` の呼び出しが許可モジュールだけにあること（ADR-0012・D02 §4.6） |
| `tests/architecture/test_gitignore_layout.py`（4件） | データ実体が git 管理外で、**snapshot の manifest と閲覧記録（`access_log.jsonl`）は追跡される**こと。追跡されているデータ成果物が無いこと |

**契約名（L1・L2a〜L2c・F1a〜F8・F5c）は CI の出力で参照するため変更しない**【合意済み】D01 §6。本書もこれを変えない。

## 9. 人工データ生成仕様

### 9.1 2つの生成器【合意済み】（既存資産の記述）

役割が違う生成器が2つあり、**混ぜない**。

| 生成器 | 置き場所 | 役割 | 使う層 |
|---|---|---|---|
| **汎用生成器** | `tests/fixtures/synthetic/market.py`、`同/snapshots.py` | 条件（DST 境界・週末・欠損・短縮セッション）を指定して足とカレンダーと snapshot manifest を作る。**値そのものに意味は無い** | 単体・意味論・プロパティ・golden・統合 |
| **T01 再現生成器** | `tests/fixtures/acceptance/t01_market.py` | 紙上トレース T01 の数値を**そのまま**再現する固定テーブル。値の1つ1つが手計算の検算値 | 受入 |

そのほか `tests/fixtures/backtest/`（`harness.py`・`paths.py`）、`tests/fixtures/strategy/`（`strategy_a.py`・`phases.py`・`fakes.py`）、`tests/fixtures/evaluation/`（`traces.py`）が、各層の組み立てを短くするための道具として置かれている。

**`tests/conftest.py` は現在空である**（「共有フィクスチャは D08 の確定後に追加する」という注記だけ）。本書の承認後も、**共有フィクスチャを `conftest.py` へ集めない**【提案】。既存の資産は `tests/fixtures/` 配下の**明示的に import する関数**で組み立てており、暗黙に注入される fixture より追いやすい。`conftest.py` に置くのは `tests/acceptance/conftest.py` のように**セッション全体で1回だけ走らせたい重い準備**に限る。

### 9.2 汎用生成器の入力と不変条件【合意済み】

`tests/fixtures/synthetic/market.py` の公開関数:

| 関数 | 入力 | 出力 |
|---|---|---|
| `calendar(closures=(), version=1)` | 休場規則の列 | `TradingCalendar` |
| `closure(local_day, start, end, note="")` | 現地日・時刻の範囲 | `ClosureRule`（短縮セッション・祝日の表現） |
| `series(symbol=USDJPY, timeframe_id="1h", basis=BID)` | 銘柄・時間足・価格基準 | `SeriesId` |
| `make_bar(series_id, interval, *, volume, available_at, provenance_kind, source_ref)` | 1本分 | `Bar` |
| `make_bars(series_id, timeframe_def, trading_calendar, window, *, skip_starts=(), volume)` | 区間とカレンダー、**欠損させたい足の開始時刻** | `Bar` の列 |
| `csv_rows(bars)` / `csv_text(bars)` | 足の列 | 受入れコマンドに渡せる CSV |

**不変条件**【合意済み】:

1. **値は決定論的**。乱数を使わず、足の開始時刻から価格を算出する。同じ入力からは常に同じ足が出る。
2. **OHLC の整合**: `low <= min(open, close)` かつ `max(open, close) <= high` を必ず満たす（`make_bar` が構成で保証）。
3. **`available_at >= interval.end`**（D03 §3.2 の不変条件）。生成器は違反する足を作らない。
4. **欠損は明示的**。`skip_starts` に渡した開始時刻の足だけが落ちる。暗黙に落ちることはない。
5. **区間はカレンダーが決める**。`make_bars` はカレンダーから区間を引き、区間が取れない場合は組み立てを止める。

### 9.3 未実装の2条件（gap・SL/TP 同時到達）の入り口と不変条件【提案】

全体計画 §8.4 と `tests/fixtures/synthetic/README.md` は「DST 境界・週末・**欠損**・**gap**・**SL/TP 同時到達**を意図的に含むケースを作れるようにする」と書いている。このうち **DST 境界・週末・欠損・短縮セッションは第9.2節の入り口で作れる**が、**gap と SL/TP 同時到達を直接指定する入り口が無い**。どちらも足の値を手で組み立てれば作れるため、**テストごとに作り方が違う**状態になっている。

本節は**入り口と不変条件を仕様として決める**。実装は本書の承認後の別 PR で行う（第15節）。

#### 9.3.1 gap（前の足の終値と次の足の始値が飛ぶこと）

| 項目 | 仕様 |
|---|---|
| 入り口 | `make_bars(..., gaps: Mapping[UtcTime, PriceOffset] = {})`。鍵は**飛ばす足の開始時刻**、値は**直前の足の終値からの差**（符号付き。正で上、負で下） |
| 効果 | その足の `open` を「直前の足の `close` ＋ 指定した差」に置き、`high` / `low` はその `open` と `close` を含むよう組み直す。**それ以降の足は新しい水準から続く**（1本だけ飛ばして戻る形にはしない） |
| 不変条件 1 | 第9.2節の OHLC の整合（`low <= min(open, close)` かつ `max(open, close) <= high`）を引き続き満たす |
| 不変条件 2 | **対象区間と `available_at` は変えない**。gap は値の話であって時刻の話ではない（時刻を動かすのは第9.6節の遅延） |
| 不変条件 3 | 指定できるのは**区間が存在する足の開始時刻だけ**。カレンダーが区間を持たない時刻を渡したら組み立てを止める。`skip_starts` で落とした足の時刻も拒否する（落ちた足に gap は付けられない） |
| 不変条件 4 | 差が 0 の指定は**拒否する**。「gap を指定したのに飛んでいない」テストを書けてしまうため |
| 週末との関係 | 週明けの最初の足に gap を置くのが典型（週末の窓開け）。カレンダーの週境界をまたぐかどうかは生成器が判断せず、**呼び手が開始時刻で指定する** |

#### 9.3.2 SL/TP 同時到達（1本の足の中で損切りと利確の両方に触れること）

足内の到達順序は OHLC からは分からない（上位 §10 の用語表）。競合の解決規則は ADR-0030 と D06 §6 が決めており、**生成器の仕事は「両方に触れる足を作ること」だけ**で、どちらが先に約定するかは決めない。

| 項目 | 仕様 |
|---|---|
| 入り口 | `make_bars(..., straddles: Mapping[UtcTime, tuple[Price, Price]] = {})`。鍵は**足の開始時刻**、値は**その足が必ず包む2つの価格**（順不同） |
| 効果 | その足の `high` を「2値の高い方以上」、`low` を「2値の低い方以下」に広げる。`open` と `close` は元の値のまま動かさない |
| 不変条件 1 | 第9.2節の OHLC の整合を引き続き満たす |
| 不変条件 2 | **`open` と `close` を動かさない**。動かすと、その足を使う他のテストの検算値が変わる |
| 不変条件 3 | 2値が等しい指定は**拒否する**（同時到達にならない） |
| 不変条件 4 | 指定できるのは区間が存在し、落としていない足の開始時刻だけ（9.3.1 の不変条件3 と同じ） |
| 決めないこと | **どちらが先に到達したことにするか**。それは足内競合解決契約（ADR-0030）と D06 §6 が決め、生成器は関与しない。`unresolved_intrabar_count` に数えられるのは実行側の判断である |

#### 9.3.3 2つの入り口の共通規則【提案】

- **どちらも既定は空**。指定しなければ第9.2節と同じ足が出る。既存のテストは1件も影響を受けない。
- **同じ足に両方を指定してよい**。gap で水準を飛ばしたうえで、その足に2値を包ませる。適用の順は **gap → straddle**（gap が `open` を動かし、straddle が `high` / `low` を広げる）。逆順にすると straddle が広げた幅を gap が壊す。
- **値は決定論的**（第9.2節の不変条件1）。乱数を入れない。

### 9.4 T01 再現生成器【合意済み】

`tests/fixtures/acceptance/t01_market.py` は T01 §9 の run（正常エントリー → 利確の取引1件と、run 末尾まで残る建玉1件）を、**受入れコマンドから実行・評価まで通せる形**で作る。

| 項目 | 値 |
|---|---|
| run 区間 | `Interval[2015-01-04T22:00Z, 2015-01-16T22:00Z)`（12日 = 1,036,800 秒） |
| 公開関数 | `bars_for(timeframe_id, definition, calendar, window=RUN_INTERVAL)`、`csv_text(bars)`、`series_of(timeframe_id)` |
| 作り方 | 固定 OHLC テーブル（`_TRIGGER1_1H` 等）から時刻に応じて選ぶ表駆動。汎用生成器の `make_bar` を使って `Bar` に組む |
| 不変条件 | **欠落を1本も作らない**（執行モデルが読む系列の足が欠けると run を開始できない。D06 §10.5 手順2） |
| 検算値 | 建玉1: 約定 150.080 / 数量 32,000 / 利確 151.230。建玉2: 約定 151.000 / 数量 30,000 / 損切り 150.400。末尾: 確定損益 36,706 / 含み込み資産 1,051,706 / 仮決済 14,670 |

### 9.5 日付の扱い【合意済み】＋【決定 2026-09-23、Q3】

| 生成器 | 日付 | 理由 |
|---|---|---|
| T01 再現生成器 | **2015年1月** | T01 の起草時は 2026年1月だったが、**2026年以降は未分類の隔離期間でいかなる経路でも読めない**（D03 §3.8、ADR-0014）ため、承認済み snapshot を経由する通しの検証が構造的にできなかった。2026-09-22 の人間の決定で、**曜日の並びと冬時間の条件が同じ 2015年**へ寄せた（`2015-01-04` は日曜、`2015-01-16` は金曜。どちらの年も1月は米国東部標準時）。run 区間の長さも週の開閉の位置も変わらず、**価格・数量・損益・資産は日付に依存しないため T01 の数値は1つも変わらない** |
| 汎用生成器 | 価格の基準は **2016-01-01**、snapshot manifest の被覆区間は **2016-01-03 〜 2023-12-31** | ADR-0014 の研究履歴（`RESEARCH_HISTORY`）の範囲（2016〜2023）に収めている |

#### 人工データは期間のアクセス分類の対象外である【決定 2026-09-23、Q3】

ADR-0014 のアクセス分類の表は **2016〜2023 = 研究履歴 / 2024〜2025 = 旧基盤の封印 / 2026年分 = 未分類の隔離**の3行で、**2016年より前の期間について何も言っていない**。T01 再現生成器の 2015年のデータは、受入テストの中で snapshot の partition を**研究履歴として作って**通している（`tests/fixtures/synthetic/snapshots.py` が `AccessClass.RESEARCH_HISTORY` を使う）。

**人工データはアクセス分類の対象外とする**。規則は次の2つである。

| # | 規則 |
|---|---|
| 1 | **人工データから作る snapshot の partition は、日付によらず常に研究履歴（`RESEARCH_HISTORY`）として作る**。ADR-0014 の表は参照しない |
| 2 | **実データの分類は ADR-0014 の表だけが決める**。人工データの扱いが実データの分類に影響することはない |

理由: アクセス分類の目的は「**未観測の期間を誤って研究に使わないこと**」である（ADR-0014 の文脈）。人工データは観測されたデータではないので、この目的にそもそも当たらない。**ADR-0014 は変えない**。

**不採用**: ADR-0014 の表を「2016年より前 = 研究履歴」まで広げる案（Q3 の選択肢2。**手元に無い期間について先に決める**ことになり、将来そのデータを入手したときに未観測だったかを確かめないまま研究履歴になるため、分類の目的が薄まる）、人工データの日付を 2016年以降へ動かす案（同 選択肢3。2026-09-22 に決めたばかりの T01 の日付をまた動かすことになり、曜日の並びと冬時間の条件が同じ年を選び直す作業が再発する）。

**残る事項**: **実データに 2015年以前の期間が現れた場合の分類は、まだ決まっていない**。そのときに ADR-0014 の改訂として別に決める。本書はその場合を扱わない（第1.2節の対象外）。

### 9.6 段階3 の遅延シナリオ4ケースへの拡張方針【合意済み】（2026-09-24、PR 分割の承認で確定）

全体計画 §8.2 の段階3 は「**遅延シナリオ別の差分を追跡できる**」を完了条件に挙げ、D07 §1.2 行3 は「遅延シナリオ別の比較は**意味論テストとして段階3・D08**、複数 run を並べる比較そのものは段階5・D09」と分担を決めている。本書 v0.1 は**具体的な4ケースの中身を決めず**（D05 v0.2 と D03 §3.6 の担当）、**生成器側に必要な拡張だけ**を決める。

必要な拡張は3つ（v1.2 で確定）。

1. **遅延規則を入力として受ける入り口**。D03 §3.6 が定める `DelayScenario`（`FixedSeriesDelay(series, delay)` と `InjectedBarDelay(series, bar_start, delay)`）を、`make_bars` と同じ層で受け取り、**`available_at` だけを動かして OHLC と対象区間は変えない**足の列を返す関数を足す。非負の制約は型の側が持つ（D03 §3.6）ので生成器は検査しない。
2. **同じ足の列に複数のシナリオを当てられること**。4ケースの比較は「**同じ素の足**に違う遅延を当てて、判断時点の見え方だけが変わる」形で書く。素の足を作る関数と遅延を当てる関数を分けておけば、4ケースのテストが素の足を共有でき、**差が遅延だけであることが構造で保証される**。
3. **比較の置き場所は意味論層**。1つの run では遅延シナリオ別の比較ができない（run manifest は `delay_scenario_ref` を1件しか持たない。D07 §6.3）ため、4ケースそれぞれで run を回して**4つの trace を1つのテストの中で突き合わせる**。golden にはしない（4ケース分の固定出力は差分が読みにくく、遅延が変わるたびに4本とも書き換わる）。

4. **日足を1時間足から集約する入り口**（v1.2 で追加。T02 §16 の拡張4）。T02 再現生成器は `aggregate(bars_1h, timeframe_def, calendar)` で日足を作り、集約の規則そのものは `marketdata.application.aggregation`（D03 §5.1）を呼ぶだけにする。日足を直接テーブルで与える経路は置かない。

この4点は段階3 の着手時（2026-09-24、段階3 実装の PR 分割の承認）に確定した。1・2・4 は段階3 実装 PR 1/5 で実装し、3 は遅延4ケースの突き合わせを書く PR 5/5 で使う。

## 10. golden の運用

### 10.1 現状【合意済み】（既存資産の記述）

| 対象 | 期待値 | 検証するテスト |
|---|---|---|
| 時間足の集約（D03 §5.1、ADR-0024） | `tests/golden/marketdata/expected/` の CSV 4件（`1h_dst_week.csv`、`4h_ny17_dst_week.csv`、`1d_ny17_dst_week.csv`、`1d_ny17_shortened.csv`） | `tests/golden/marketdata/test_aggregation_golden.py`（5件） |
| 評価結果5表（D07 §5・§6・§8.1） | `tests/golden/evaluation/expected/` の TSV 5件（`metrics.tsv`、`category_counts.tsv`、`trades.tsv`、`fill_diagnostics.tsv`、`consistency_checks.tsv`） | `tests/golden/evaluation/test_evaluation_golden.py`（2件） |

期待値は**テキスト**（CSV / TSV）で置く【合意済み】。Parquet はメタデータや圧縮設定でバイト列が変わるため固定に向かない（D07 §9.2 が `result_digest` を使う理由と同じ）。数値の書き方は D06 §9.1 の平坦化の規則（`Decimal` は文字列、固定小数表記、同じ値は常に同じ文字列）に従う。

**自動更新の仕組みは無い**【合意済み】。`--update` のようなフラグも環境変数も存在しない。期待値ファイルは**手で書き換える**。

### 10.2 更新の条件【提案】

golden が落ちたとき、**期待値を直すのは最後の手段**である。次の順に判断する。

1. **実装のバグか** → 実装を直す。期待値は触らない。
2. **設計文書の契約が変わったか** → **設計文書の改訂を同じ PR に含めてから**期待値を更新する。契約が変わっていないのに出力が変わったなら、それは 1 のバグである。
3. **表現だけが変わったか**（列の順序、数値の書き方） → D06 §9.1 の規則に照らし、規則どおりなら期待値を更新する。規則の側を変えるなら D06 の改訂が要る。

既存のテストはこの判断をアサーションのメッセージに埋め込んでいる【合意済み】。集約の golden は「**ここが変われば ny17_v2 の規則が変わったということ**（D03 §5.1、ADR-0024）」、評価の golden は「**ここが変われば指標の式・注記・分類の語彙・整列鍵のどれかが変わったということ**（D07 §5・§6・§8.1）」と書いてある。**このやり方を規約にする**【提案】: **golden テストのアサーションには、差分が出たときに何が変わったことを意味するかを書く**。

### 10.3 差分の読み方と承認【提案】

- **差分は PR の diff として出す**。期待値ファイルはテキストなので、`git diff` がそのまま差分になる。**期待値の更新を実装の変更と同じコミットに混ぜない**（どちらが原因か読めなくなる）。期待値の更新は**独立したコミット**にし、メッセージに 10.2 の1〜3のどれかを書く。
- **期待値の更新を含む PR は、PR 本文に「golden を更新した理由」を1行で書く**。独立レビューの対象範囲（PR レビュー方針 §3.1）の中に必ず入れる。
- **承認は人間が行う**。golden の更新は「出力が変わってよい」という判断であり、設計の変更と同じ重みを持つ。Codex が clean でも、**期待値の差分は人間が merge 前に目で見る**。

### 10.4 未整備【提案】

D04 §12 の golden（検証戦略 A の YAML → `CompiledStrategy` の固定出力）、D05 §11 の golden（1突破分の trace の固定）、D06 §11 の golden（検証戦略 A の1取引分の trace の固定）が**いずれも無い**。全体計画 §8.4 が「検証戦略 A・B の trace を保存し、変更時に差分を検査する」と書いた部分は、**評価結果5表の golden だけで代用されている状態**である。第13節に挙げる。

## 11. 決定論の検証【合意済み】＋【提案】

D07 §9.1 の決定論の3条件（壁時計の時刻を入れない、行の順序を格納順に依存させない、`Decimal` は文字列で保存する）を、3つの層で確かめている。

| 層 | テスト | 比較する対象 |
|---|---|---|
| プロパティ | `tests/property/evaluation/test_determinism.py::test_the_same_input_twice_gives_the_same_result_digest` | `result_digest`（D07 §9.2。5表の全行を整列鍵で並べた正規化エンコードのダイジェスト） |
| プロパティ | 同ファイル `::test_the_row_order_of_the_trace_does_not_change_the_result` | 入力の行の順序を入れ替えても結果が同じこと |
| 意味論 | `tests/semantics/backtest/test_engine_semantics.py::test_a_second_run_with_the_same_input_allocates_the_same_ids` | 採番された ID の列（ADR-0006、D02 §7.3） |
| 統合 | `tests/integration/backtest/test_t01_paths.py::test_the_same_input_produces_the_same_trace` | trace の全表 |
| 受入 | `tests/acceptance/test_stage2_completion.py::test_the_rerun_produces_the_same_run_identifier`、`::test_the_rerun_produces_the_same_trace`、`::test_the_rerun_produces_the_same_evaluation` | **コマンド経由で2回通した成果物**（`tests/acceptance/conftest.py` の `artifacts` と `rerun` の2つのセッション fixture が同じ入力を2回通す） |

**許容誤差は置かない**【合意済み】D06 §9.1。段階2 の比較は**完全一致**である。Decimal 演算と決定論的採番だけで構成され、浮動小数の非決定性が入らないため、誤差が出るならそれは非決定性のバグであり、閾値で隠してはならない。

**再現性の判定はファイルの一致ではなく `result_digest` の一致で行う**【合意済み】D07 §9.2。Parquet のバイト列は圧縮設定で変わりうる。

**本書が足す規約**【提案】: **決定論の検証は、層ごとに比較する対象を変えて重ねる**。プロパティ層は digest、統合層は trace の全表、受入層はコマンド経由の成果物。1つの層だけで済ませると、その層より下で入った非決定性（例: ファイル名の生成、ディレクトリの列挙順）を取り逃がす。

## 12. CI の並び【合意済み】（既存資産の記述）

`.github/workflows/ci.yml` の単一ジョブ `checks` が次の順で走る。

```
1. Check out
2. Set up uv
3. Install Python 3.12.13
4. uv sync --frozen
5. uv run ruff check .
6. uv run ruff format --check .
7. uv run mypy
8. uv run lint-imports
9. uv run pytest
```

**テストは最後**である。書式・型・依存規則の失敗はテストを待たずに分かるため、速い検査を前に置く。PR レビュー方針 §4 の手順3（検証を通す）もこの並びをそのまま使う。

`pyproject.toml` の pytest 設定は `testpaths = ["tests"]` と `pythonpath = ["."]` の2つだけで、**マーカーも `addopts` も定義していない**【合意済み】。`pytest.ini` / `setup.cfg` / `tox.ini` は無い。

**実データを使うテストは段階4以降に別マーカーで分ける**【合意済み】D01 §9。段階2 の時点でマーカーが1つも無いのは、**実データを使うテストがまだ1件も無いから**であり、欠落ではない。マーカーの名前と既定の除外設定は、実データを使う最初のテストと同じ PR で決める【提案】。

**テストは外部データ・ネットワークに依存しない**【合意済み】D01 §9。受入テストもコマンドを呼ぶだけで、データは人工データ生成器が作る。

**件数の数え方が2通りある**ことに注意する【提案】。`def test_` を数えると約 1,151 件、`pytest` が収集すると `parametrize` の展開が入って増える（進捗ファイルの「1,450 件」はこちら）。**報告に件数を書くときは、どちらの数え方かを添える**。既定は `uv run pytest --collect-only -q` の収集件数とする。

## 13. 未整備の一覧【提案】

本書は**直す文書ではない**。ここに挙げたものは、本書の承認後に別の PR で埋める候補である。

### 13.1 意味論テストの対応（3件）

Q1 の決定（契約行の充足は層を問わない）により、**従来「層違い」として数えていた10件は充足しているものとして数える**。したがって未整備は3件である。

| 区分 | 件数 | 内訳 |
|---|---|---|
| **未整備** | 3 | D06 §11 の #2 受付と約定の原子性、#3 同じ `event_id` の再配送、#6 期限と始値の同時刻 |

参考（未整備ではないが、どこにあるかを追うための記録）: **契約行を充足しているがテストが `tests/semantics/` の外にあるのは10件**。D04 §12 の3行すべて（単体層）、D06 §11 の #4 終端後の別イベント（戦略側の意味論層）・#5 予約移管の二重計上（単体層）、D07 §11 の5行すべて（単体層）。

### 13.2 その他（6件）

| # | 未整備 | 出どころ |
|---|---|---|
| 1 | D04・D05 のプロパティテスト（同じ宣言から同じ digest、宣言順を入れ替えても評価順が変わらない）が無い | D04 §12・D05 §11 |
| 2 | D04・D05・D06 の golden（宣言のコンパイル結果、1突破分の trace、1取引分の trace）が無い | D04 §12・D05 §11・D06 §11・全体計画 §8.4 |
| 3 | 人工データ生成器に **gap** と **SL/TP 同時到達**を指定する入り口が**まだ実装されていない**（仕様は第9.3節で決めた。実装は承認後の別 PR） | 全体計画 §8.4・`tests/fixtures/synthetic/README.md`・本書 §9.3 |
| 4 | D03 §11 の意味論の行が実態より少ない（承認済み snapshot の関門 27件が契約行に挙がっていない） | D03 §11・§3.7.1 |
| 5 | **上位設計書 §7.2 の文言の改訂依頼**。同節の項目は意味論層の対象を定めるが、Q1 の決定により**充足の判定は層を問わない**ことになった。「意味論テストとして固定する」と読める箇所を「**名前の付いたテスト1件で固定する（層は問わない）**」へ改める依頼を、上位設計書の次の改訂に渡す。本書の承認では上位文書を書き換えない | 上位 §7.2・本書 §4（Q1 の決定） |
| 6 | **取引機会の有効性の再検査（`ValidityRecheck`）の4区分を通す意味論テストが無い**【新規】（v1.1、2026-09-23。D05 の Q24 決定、選択肢1）。検証戦略 B では、確認待ちのあいだに市場状態が失効する経路が**構造的に到達しない**ことが紙上トレースで分かった（確認期限が15分足4本＝ちょうど1時間で、日足境界も1時間境界の上にあるため、日足の条件が変わりうる唯一の確認足が期限の足と重なり、期限が先に勝つ。T02 §6・D05 §9.4）。**検証戦略 B の宣言は変えない**と決めたので、段階3 の受入れテストではこの経路が1度も通らない。そこで、**確認期限だけを長くした小さな戦略を意味論テスト専用に宣言し**、再検査の記録の4つの結末（成立した / 成立しなかった / 読めず見送った / 読めず失敗した。D05 §7.3）をすべて1件ずつ通す。**「読めず失敗した」を通すには宣言を2つ用意する**。この結末は有効性束縛の欠損方針が `Error` の宣言でしか生じず（D05 §7.3）、検証戦略 B は `SkipEvaluation` を宣言しているからである。すなわち、**欠損方針が `SkipEvaluation` の版**（成立 / 不成立 / 読めず見送り の3区分を通す）と、**`Error` の版**（読めず失敗の1区分を通す）の2つを宣言する。どちらも確認期限を長くしたほかは検証戦略 B と同じ形でよい。これが無いと、決定記録 ADR-0031（確認待ち中の条件再検査）が要求する経路が**どのテストでも実行されない** | D05 §7.3・§9.4（Q24 決定）・T02 §6 |

### 13.3 上位設計書 §7.2 由来（2件）

| # | 未整備 | 出どころ |
|---|---|---|
| 1 | **部品の出力と意思決定まで通した先読み不変のテストが無い**。市場データの見え方（`tests/property/marketdata/test_asof_properties.py`）までは押さえているが、**同じ run を未来の足を足した入力でもう一度回して trace が一致すること**は確かめていない。これは上位 §7.2 の筆頭項目であり、先読み防止の中心にある | 上位 §7.2・本書 §4.1 #1 |
| 2 | **gap を含む足での手計算検証が無い**（生成器の入り口が無いため。13.2 #3 と同じ原因） | 上位 §7.2・本書 §4.1 #5 |

**未整備は合計 11件**（意味論の対応 3件、その他 6件、上位由来 2件）。起草時は20件だったが、**Q1 の決定で10件が「充足」に変わった**ため 10件になり、**2026-09-23 の Q24 の決定で1件（その他 #6）が加わって 11件**になった。テストが増えたわけではない。

## 14. 決定の一覧（2026-09-23、3件すべて決定済み）

Q1〜Q3 は 2026-09-23 に人間がすべて決定した。**3件とも提示時の推奨案である選択肢1** である。本文は決定後の内容になっており、**未決の項目は残っていない**。

| # | 決めたこと | 決定 | 本文の反映先 |
|---|---|---|---|
| **Q1** | 契約行と実際のテストの「層」が食い違う10件の揃え方 | **選択肢1**: 契約行の充足は**層を問わない**（名前の付いたテスト1件で固定されていればよい）と読み替え、**本書の対応表を索引の正本にする**。テストは1件も移動しない | 第4節（状態の定義と読み替えの理由）、第5〜7節（表の状態）、第13.1節（未整備が13件 → **3件**） |
| **Q2** | D01 §9 のテスト配置表に統合・受入の2層を足すか | **選択肢1**: **足す**。本書の承認と同じ PR で D01 §9 を改訂した（**D01 v2.5**）。テスト配置の正本は D01 §9 の1か所 | 第1.1節、第1.2節の行1、第2.1節、第13.2節（旧 #4 を削除） |
| **Q3** | 人工データの日付（2015年）をアクセス分類にどう収めるか | **選択肢1**: **人工データはアクセス分類の対象外**と本書に明記する。人工データの snapshot は日付によらず常に研究履歴として作り、実データの分類は ADR-0014 の表だけが決める。**ADR-0014 は変えない** | 第9.5節（規則2つ、理由、不採用の2案、残る事項） |

各決定の理由と不採用にした案は、反映先の本文に書いてある。

### 14.1 上位文書への改訂依頼（1件）

Q1 の決定により、**充足の判定は層を問わない**ことになった。上位設計書 §7.2 は意味論層の対象を定める節であり、「意味論テストとして固定する」と読める箇所がある。**「名前の付いたテスト1件で固定する（層は問わない）」へ改める依頼**を、上位設計書の次の改訂に渡す。本書の承認では上位文書を書き換えない（第13.2節 #5）。

## 15. 承認後にすること

1. **第9.3節の2つの入り口（gap・SL/TP 同時到達）を実装する**。別 PR で `tests/fixtures/synthetic/market.py` に足し、上位 §7.2 #5 の手計算検証を1件ずつ書く。
2. **上位 §7.2 #1 の先読み不変テストを書く**（未来の足を足した入力で同じ run を回し、trace が一致すること）。第13.3節 #1。
3. **未整備の残り3件**（第13.1節。受付と約定の原子性、同じイベント識別子の再配送、期限と始値の同時刻）を埋める。D06 §5.1 が「どの設定でも発生しない」と書いた遷移は、第6.2節の表現可能性テストの形で書く。
4. **上位設計書 §7.2 の文言の改訂依頼**（第14.1節）を、上位設計書の次の改訂に渡す。
5. 第13節の残りを、埋める順に並べて全体計画 §10 の「次のアクション」へ渡す。
6. 段階3 の着手時に、第9.6節の3点を D08 v0.2 として確定させる（遅延シナリオ4ケースの生成器の入り口・比較の置き場所）。
