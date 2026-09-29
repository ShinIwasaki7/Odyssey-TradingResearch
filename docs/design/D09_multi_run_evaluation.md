# D09: 複数実行の評価設計（`odyssey_fx.evaluation`: domain.search / domain.splits / application.run_experiment の探索 / application.holdout_gate）

作成日: 2026-09-29
状態: **ドラフト（承認待ち）**。v0.1。第17節の要決定 Q1〜Q12 は未決である。
v0.1（2026-09-29、段階5 の設計 PR）: 段階5「複数実行の評価」の設計を起草した。単一実行の評価（D07 v2.6）が第14節で段階5 へ引き渡した8項目と、全体計画書 §5.5.2・§7.5 後半・§8.2 の段階5 の行を受け、探索・分割・選定と合否・頑健性・holdout の隔離と封印期間の許可判定・試行の記録・成果物の置き場・段階5 の完了条件の確かめ方を決める。**段階4 までの設計（D01〜D08）は変えない**。変える必要がある箇所は第16節に改訂依頼として挙げるだけで、本 PR では改訂しない（全体計画書 §8.1 の D09 の行だけは本書を指すように追随した）。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §5.1・§5.3・§6・§7、[全体計画書](fx_research_platform_overall_plan.md) §5.5.2・§6 C-2・§7.5・§8.1・§8.2・§8.5、[D01](D01_architecture_and_dependency_rules.md) §2.2・§3.2・§4・§7.2・§10.3、[D02](D02_common_kernel.md) §7.1・§9.2・§9.3、[D03](D03_marketdata_and_time.md) §3.8・§3.8.1・§6.1・§7.4、[D04](D04_strategy_declarations.md) §7・§13.2、[D05](D05_strategy_runtime.md) §5.2・§5.5、[D06](D06_backtest_vertical_slice.md) §3・§9.3・§10.1・§10.3、[D07](D07_single_run_evaluation.md) §5.2・§5.3・§5.5・§10.1・§14・§18〜§22・§24、[D08](D08_test_strategy.md) §2.2・§2.3・§9.5、ADR-0006（決定論的 ID）、ADR-0014（期間のアクセス分類）、ADR-0027（成果物の形式）
対応段階: 段階5。

## 0. 本書の位置付けと凡例

段階4 までの基盤は、**1つの実行（run）を、事前に固定した記録票のもとで走らせ、評価し、別プロセスで再現する**ところまでを作った（D07）。本書はその上に、**同じ戦略のパラメータを何通りも試し（探索）、時系列の区間に分けて（分割）、結果を見る前に固定した規則で1つを選び（選定）、別の区間で合否を判定し（合否）、封印した期間を探索から隔離する**ための型・手順・記録を決める。

守る性質は段階5 の完了条件（全体計画書 §8.2）の3つである。

1. **選定が train 内で閉じる**: どの試行を選ぶかは、選定区間（train）の結果だけから決まる。
2. **全試行を記録する**: 試したもの・試していないもの・失敗したもの・途中で止まったものを区別して残す。
3. **holdout を通常探索で読めない**: 封印期間のデータは、探索の経路からは構造的に読めない。

本書は**単一実行の指標・評価・記録票を作り直さない**（D07 §14「単一実行の指標定義を D09 側で作り直さない」）。1つの run の評価は D07 の `EvaluateRun` がそのまま行い、本書は評価の結果を並べて比べるだけである。

凡例は全体計画書第0節に従う。

| 印 | 意味 |
|---|---|
| 【合意済み】 | 上位文書・ADR・承認済み設計文書（D01〜D08）で確定済み。本書で再議論しない。矛盾があれば上位を正本とする |
| 【提案】 | 本書が推奨する設計。承認で確定 |
| 【要決定】 | 本書では決めない事項。選択肢と推奨を第17節に並べ、人間が選ぶ。本文は推奨案で書き、どの要決定に従うかを括弧で示す（決定が推奨と違えば本文を直す） |

## 1. 責務と境界

| 項目 | 内容 | 印 |
|---|---|---|
| 提供するもの | `domain.search`（探索計画・試行の列挙・選定と合否の規則）、`domain.splits`（分割）、`application.run_experiment` の探索の経路、`application.holdout_gate`（封印期間の許可判定）、`adapters.fs_store` の探索の記録の保存 | 【合意済み】D01 §7.2・全体計画書 §5.5.2 |
| 依存できるもの | D07 §1 と同じ（標準ライブラリ、`common`、`marketdata.domain`、`backtest` の `domain` と `trace`、`strategy` の `declarations` / `records` / `compiler`）。実行・保存・閲覧記録はポート経由 | 【合意済み】D01 §3.2（契約 F4・F7・F8）・§4 |
| 決めないもの | 1つの run の意味論（D06）、1つの run の指標と評価（D07 §5〜§10）、閲覧記録の追記と封印状態の導出の規則（D03 §3.8・ADR-0014）、並列実行（D10） | 【合意済み】 |

型はすべて `@dataclass(frozen=True, slots=True)`、コレクションは `tuple` か凍結 `Mapping`、区分タグ付き union は `kind` を持つ dataclass の `Union` とする【合意済み】D01 §8・ADR-0011。domain と application は実時計・乱数・環境変数を読まない【合意済み】D01 §2.2 規則2。

### 1.1 語彙と規則の正本がどの文書にあるか【提案】

| 事項 | 正本 | 本書の扱い |
|---|---|---|
| 指標の一覧・式・値なしの理由 | D07 §5・§10.3 | 参照のみ。選定と合否は D07 の `MetricId` を名前で指す |
| 採用指標と参考値の区別 | D07 §5.3・§22.2（参考値は #7・#8・#11・#13・#14） | 参照のみ。**選定と合否に参考値を使わない**（第7.1節） |
| 1つの run の評価の状態 `EvaluationStatus` | D07 §10.1 | 参照のみ。`ABORTED` の使い方だけ本書が決める（第10.4節） |
| 記録票・結末記録・研究ポリシーの検査・再利用の規則 | D07 §19〜§20 | 参照のみ。探索の項目を**足す**（第10節） |
| 実験設定の書式 v2 のキー | D07 §18 | 参照のみ。`search_plan` / `split` / `selection` / `acceptance` の**語彙**を本書が決める（第5〜7節。D07 §18.2 が「段階5 で D09 が語彙を足す」とした箇所） |
| 期間のアクセス分類・封印状態・fail-closed の手順 | ADR-0014、D03 §3.8・§3.8.1 | 参照のみ。`holdout_gate` の手順を具体化する（第9節） |
| run の識別子と成果物の衝突の扱い | ADR-0006、D07 §19.6 | 参照のみ |
| パラメータの型と値 | D04 §7（`ParameterSpec` / `ParameterValue`） | 参照のみ。探索の軸の値は `ParameterValue` で持つ |
| コンパイルの拒否 | D05 §5.2（`CompileRejection`） | 参照のみ。割当のコンパイル拒否を試行の失敗として記録する（第5.2節） |

### 1.2 本書が決めること・後続に委ねること【提案】（**レビュー対象範囲の正本**）

**本書のレビューの対象範囲は下表の「本書が決めること（対象内）」の列である**。「後続・他文書が決めること」の列に属する指摘は対象外（担当へ）として記録し、本書では直さない。D04〜D08 の §1.2 と同じ例外を置く。**本書の文が対象外の列の挙動を暗示していて誤解を招く場合は、その暗示を消す修正だけ行う**。

| # | 領域 | 本書が決めること（対象内） | 後続・他文書が決めること（対象外・担当） |
|---|---|---|---|
| 1 | 探索計画 | `SearchPlan` の表現、試行の列挙と番号、探索の終了条件、決定論、拒否する探索計画、コンパイル拒否の扱い（第5節）。探索アルゴリズムの範囲（**Q6**） | 部品パラメータの型と範囲（**D04 §7**）、コンパイラの検査（**D05 §5**）、探索空間の宣言を戦略宣言に置くこと（**D04 §15 の後続**。本書は実験設定に置く）、格子以外の探索アルゴリズムの記録と未試行の定義（Q6 で選択肢2・3 を選んだ場合の**本書の後続版**） |
| 2 | 分割 | 分割の種類（train / validation、walk-forward）と書き方、fold ごとの run、purge、fold 境界の建玉と状態の扱い（第6.1〜6.4節）、fold の状態×出来事表（第6.6節）。ウォームアップと採点区間の分離（**Q5**） | fold をまたいで建玉・状態を持ち越すこと、途中からの実行再開（**別設計**。全体計画書 §7.5）、run 末尾の手順そのもの（**D06 §10.1**） |
| 3 | 選定・合否・集約 | 事前固定の規則、選定規則・合否規則・fold の集約の語彙と意味、値なし・失敗の扱い、選定記録を検証の run より前に保存すること（第7節）。値を誰が決めるか（**Q3**）、検証区間で走らせる試行（**Q4**）、最終検証に進める試行（**Q12**） | 選定と合否に使う指標の定義（**D07 §5**）、各実験の具体的な指標・閾値・区間の長さ（**実験設定。人間が実験ごとに書く**） |
| 4 | 頑健性 | 段階5 で測る頑健性の範囲（第8節。**Q9**） | 分布指標・下方リスク指標を指標集合に足すこと（**D07 の指標集合 v3**。Q9 の選択肢3 を選んだ場合） |
| 5 | holdout の隔離 | 探索の経路から封印期間を読めない構造、段階5 の最終検証に使う期間（**Q1**）、`holdout_gate` の許可・消費・公開の順序と拒否、閲覧記録の行の鍵、許可の状態×出来事表、最終検証のコマンド（第9.1〜9.6節）、使用済み期間の opt-in（第9.7節。**Q10**）、人工データでの確かめ方（第9.8節。**Q2**） | アクセス分類と封印状態の規則そのもの（**ADR-0014**）、閲覧記録の追記と状態の導出の実装（**D03 §3.8**）、閲覧記録のポートの実装者（**D01 §4**。**Q11**）、隔離期間（2026年分）の再分類（**別 ADR**） |
| 6 | 試行の記録 | 記録票・試行記録・結末記録の関係と項目、試行の状態（試行済み・未試行・失敗・中断）と `ABORTED` の使い方、試行と実験の状態×出来事表、研究ポリシーの検査の探索への適用（第10.1〜10.8節）、複雑性の上限と試行数の見直し（**Q7**）、研究ポリシーの版の登録簿（**Q8**） | 記録票・結末記録の段階4 の項目と書き込み規則（**D07 §19**）、研究ポリシー v1 の検査 P1〜P6 の中身（**D07 §20.3**） |
| 7 | 成果物の置き場 | `runs/experiments/<名前>/v<版>/` の下の探索の記録の置き場、集約表の主キーと空表、同じ版の再実行、コマンドと終了コード（第11節） | `runs/<run_id>/` の run と評価の成果物（**D06 §9・D07 §8**）、保存形式そのもの（**ADR-0027**） |
| 8 | 受入 | 段階5 の完了条件3つの確かめ方と置く層（第13節）、実装 PR の分け方（第14節） | 受入テストの規約の本文（**D08**。第16節の改訂依頼）、テストの実装（**実装 PR**） |
| 9 | 対象外の明記 | 本書が扱わないものの一覧（第15節） | ― |

**受け取るもの**（本書が再定義しないもの）:

| 出どころ | 受け取るもの |
|---|---|
| D07 §3・§5・§10 | `EvaluateRun`、`EvaluationReport`、`MetricId`・`MetricRecord`・`Unavailable`、`EvaluationStatus`、採用指標と参考値の区別 |
| D07 §18〜§21 | 書式 v2 のキーと読込の拒否規則、記録票・結末記録の項目、`ExperimentStatus`、研究ポリシー v1 の検査 P1〜P6、既存の run 成果物の再利用（§19.6）、終了コード（§21.3） |
| D06 §3・§9.3・§10.1・§10.3 | `RunConfig`（`run_interval` と `compiled_ref` を含む）、run manifest の項目、run 末尾の手順（残った建玉は未決済のまま時価評価）、末尾の3集計 |
| D05 §5.2・§5.5 | コンパイル拒否の区分、`CompiledStrategyRef` のハッシュの対象 |
| D04 §7・§13.2 | `ParameterSpec` / `ParameterValue`、戦略宣言の内容ハッシュ |
| D03 §3.8・§3.8.1・§6.1・§7.4 | アクセス分類、封印状態の状態×出来事表、許可 partition 集合の受け取り、閲覧記録の値の伝播表の行 |
| D02 §7.1・§9.2・§9.3 | `ExperimentId`、`CompiledStrategyRef`、正規化エンコードとダイジェスト |
| ADR-0006・ADR-0014・ADR-0027 | `RunId` の構成と再実行、アクセス分類と fail-closed の手順と消費遷移の直列化、Parquet ＋ JSON |
| D08 §2.2・§2.3・§9.5 | どの層に書くかの判断、受入テストの規約、人工データはアクセス分類の対象外であること |

**必須表（全体計画書 §8.5、R1）の置き場所**。本書は4つの状態機械と、探索の設定から結末記録までの値の伝播を定めるので、3つの表をすべて持つ。

| 必須表 | 置き場所 |
|---|---|
| 境界表 | 本節の上の2表 |
| 状態×出来事表 | 第6.6節（fold）、第9.5節（封印期間の許可）、第10.5節（試行の実行単位）、第10.7節（探索の実験1回） |
| 値の伝播表 | 第12節 |

## 2. モジュール構成【提案】

D01 §7.2 の一覧のうち段階5 で作るものを挙げる。モジュールの追加はしない。

| サブパッケージ | 段階5 で作る・改める | 内容 |
|---|---|---|
| `domain` | `search.py`（新規）、`splits.py`（新規）、`experiment.py`（改める） | 探索計画・試行の列挙・選定と合否の純粋関数（第5節・第7節）、分割（第6節）、記録票・結末記録に探索の項目を足す（第10節） |
| `application` | `run_experiment.py`（改める）、`holdout_gate.py`（新規）、`ports.py`（改める） | 探索の実行（第4節・第10節）、封印期間の許可判定（第9節）、閲覧記録のポート `HoldoutAccessLog`（D01 §4 で所在が確定済み） |
| `adapters` | `fs_store.py`（改める）、`report.py`（改める） | 試行記録・選定記録・集約表の保存（第11節）、レポートに探索の節を足す（第11.4節） |
| `app` | `config`・`composition`・`cli`（改める） | 書式 v2 の語彙の読込、試行ごとのコンパイルと `RunConfig` の組み立て、`experiment finalize` コマンド（第9.6節） |

## 3. 本書が定義する型の一覧【提案】

本文に出る型名を一覧する。**この表にない型名を本文で使わない**。D02〜D07 が定義した型はそのまま使い、ここには載せない。

| 型 | 置き場所 | 区分 | フィールド / 値 | 詳細 |
|---|---|---|---|---|
| `SearchPlanKind` | `domain.search` | enum | `NONE` / `GRID` | §5.1・Q6 |
| `ParameterAxis` | `domain.search` | レコード | `instance_id: str` / `parameter: str` / `values: tuple[ParameterValue, ...]` | §5.1 |
| `SearchPlan` | `domain.search` | レコード | `kind: SearchPlanKind` / `axes: tuple[ParameterAxis, ...]` / `max_trials: int` | §5.1 |
| `ParameterAssignment` | `domain.search` | レコード | `values: tuple[tuple[str, str, ParameterValue], ...]`（`(instance_id, parameter, 値)` を `(instance_id, parameter)` の昇順で） | §5.2 |
| `TrialPlan` | `domain.search` | レコード | `trial_index: int` / `assignment: ParameterAssignment` / `compiled_ref: CompiledStrategyRef \| None` / `compile_rejections: tuple[str, ...]`（コンパイル拒否の `check_id` と区分の正規化エンコード文字列。拒否が無ければ空）/ `expected_config_digests: tuple[tuple[TrialUnitKey, ConfigDigest], ...]` | §5.2・§10.2 |
| `TrialPhase` | `domain.search` | enum | `TRAIN` / `VALIDATION` | §6.2 |
| `TrialUnitKey` | `domain.search` | レコード | `fold_index: int` / `phase: TrialPhase` / `trial_index: int` | §6.2 |
| `TrialStatus` | `domain.search` | enum | `NOT_STARTED`（未試行）/ `COMPLETED`（試行済み）/ `FAILED`（失敗）/ `ABORTED`（中断） | §10.4 |
| `SelectionDirection` | `domain.search` | enum | `MAXIMIZE` / `MINIMIZE` | §7.2 |
| `Comparator` | `domain.search` | enum | `GE` / `GT` / `LE` / `LT` | §7.2・§7.3 |
| `MetricCondition` | `domain.search` | レコード | `metric: MetricId` / `comparator: Comparator` / `threshold: Decimal` | §7.2・§7.3 |
| `SelectionRule` | `domain.search` | レコード | `metric: MetricId` / `direction: SelectionDirection` / `eligibility: tuple[MetricCondition, ...]` | §7.2 |
| `FoldAggregation` | `domain.search` | enum | `ALL_FOLDS` / `MEDIAN` | §7.3 |
| `AcceptanceRule` | `domain.search` | レコード | `conditions: tuple[MetricCondition, ...]` / `aggregation: FoldAggregation` | §7.3 |
| `CandidateStatus` | `domain.search` | enum | `CANDIDATE` / `EXCLUDED_TRIAL_FAILED` / `EXCLUDED_NOT_COMPLETED` / `EXCLUDED_POST_RUN_CHECK` / `EXCLUDED_METRIC_UNAVAILABLE` / `EXCLUDED_INELIGIBLE` | §7.4 |
| `FoldSelection` | `domain.search` | レコード | `fold_index: int` / `selected_trial_index: int \| None` / `selected_value: Decimal \| None` / `inputs: tuple[tuple[int, RunEvaluationId \| None, CandidateStatus], ...]`（`(trial_index, 選定区間の評価の識別子, 候補の区分)` を `trial_index` の昇順で全試行） | §7.2・§7.5 |
| `FoldVerdict` | `domain.search` | enum | `PASSED` / `FAILED` / `NO_CANDIDATE` / `VALIDATION_NOT_COMPLETED` | §7.3・§7.4 |
| `SearchVerdict` | `domain.search` | enum | `PASSED` / `FAILED` | §7.3 |
| `TrialStartRecord` | `domain.search` | レコード | `experiment_id: ExperimentId` / `unit: TrialUnitKey` / `expected_run_id: RunId` | §10.3 |
| `TrialRunRecord` | `domain.search` | レコード | `experiment_id` / `unit: TrialUnitKey` / `status: TrialStatus`（`COMPLETED` だけを書く）/ `expected_run_id: RunId` / `run_id: RunId` / `run_status: RunStatus` / `run_reused: bool` / `run_evaluation_id: RunEvaluationId` / `evaluation_status: EvaluationStatus` / `result_digest: ContentDigest` / `outcome_checks: tuple[PolicyCheckResult, ...]`（P4・P5） | §10.3 |
| `SearchOutcome` | `domain.search` | レコード | `selections: tuple[FoldSelection, ...]` / `fold_verdicts: tuple[tuple[int, FoldVerdict], ...]` / `verdict: SearchVerdict` / `trial_counts: tuple[tuple[TrialStatus, int], ...]`（`TrialStatus` の宣言順、0件も出す） | §10.6 |
| `SplitKind` | `domain.splits` | enum | `NONE` / `TRAIN_VALIDATION` / `WALK_FORWARD` | §6.1 |
| `Fold` | `domain.splits` | レコード | `fold_index: int` / `train: Interval` / `validation: Interval` | §6.1 |
| `FinalHoldoutSpec` | `domain.splits` | レコード | `interval: Interval` / `purpose: str` | §9.2・§9.6 |
| `SplitSpec` | `domain.splits` | レコード | `kind: SplitKind` / `folds: tuple[Fold, ...]` / `purge: timedelta` / `final_holdout: FinalHoldoutSpec \| None` | §6.1 |
| `PreparedTrial` | `application.run_experiment` | レコード | `plan: TrialPlan` / `compiled: CompiledStrategy \| None` / `run_configs: tuple[tuple[TrialUnitKey, RunConfig], ...]` / `expected_run_ids: tuple[tuple[TrialUnitKey, RunId], ...]` | §10.7 |
| `PreparedSearch` | `application.run_experiment` | レコード | D07 §3 の `PreparedExperiment` の項目のうち `run_config` / `compiled` / `expected_run_id` を `trials: tuple[PreparedTrial, ...]` に置き換えたもの | §10.7 |
| `HoldoutPermit` | `application.holdout_gate` | レコード | `permit_id: ContentDigest` / `experiment_id: ExperimentId` / `snapshot_id: SnapshotId` / `partitions: frozenset[PartitionId]` / `purpose: str` | §9.3 |
| `GateRefusalKind` | `application.holdout_gate` | enum | `EXPERIMENT_NOT_ELIGIBLE` / `NOT_LEGACY_HOLDOUT` / `NOT_SEALED` / `ACCESS_LOG_UNREADABLE` / `ACCESS_LOG_UNTRACKED` / `PUSH_REJECTED` / `ORIGIN_UNREACHABLE` | §9.3 |
| `GateRefusal` | `application.holdout_gate` | レコード | `kind: GateRefusalKind` / `detail: str` | §9.3 |
| `HoldoutGate` | `application.holdout_gate` | 具体クラス（構築時に `HoldoutAccessLog` を受け取る） | `issue(manifest: ExperimentManifest, outcome: ExperimentOutcome, snapshot: SnapshotRef, partitions: frozenset[PartitionId]) -> HoldoutPermit \| GateRefusal` / `consume(permit: HoldoutPermit) -> frozenset[PartitionId] \| GateRefusal`（戻り値は公開してよい partition の集合） | §9.3 |
| `PushResult` | `application.ports` | enum | `PUSHED` / `REJECTED_NON_FAST_FORWARD` / `ORIGIN_UNREACHABLE` / `NOT_TRACKED` | §9.3 |
| `AccessLogReadFailure` | `application.ports` | レコード | `snapshot_id: SnapshotId` / `detail: str` | §9.3 |
| `HoldoutAccessLog` | `application.ports` | Protocol | `fetch_states(snapshot: SnapshotRef, partitions: frozenset[PartitionId]) -> Mapping[PartitionId, HoldoutState] \| AccessLogReadFailure`（origin の既定ブランチを取得してから導出する）/ `append_consumption(permit: HoldoutPermit) -> PushResult`（消費記録の追記・コミット・push を1操作で行う）。所在は D01 §4 が確定済み。実装者は **Q11** | §9.3 |
| `FinalEvaluationRecord` | `domain.experiment` | レコード | `experiment_id` / `permit_id: ContentDigest` / `snapshot_id: SnapshotId`（消費した閲覧記録がある snapshot）/ `partitions: tuple[PartitionId, ...]` / `purpose: str` / `trial_index: int` / `run_id: RunId \| None` / `run_status: RunStatus \| None` / `run_evaluation_id: RunEvaluationId \| None` / `evaluation_status: EvaluationStatus \| None` / `result_digest: ContentDigest \| None` / `verdict: FoldVerdict \| None` | §9.6 |

`MetricId` / `RunEvaluationId` / `EvaluationStatus` / `PolicyCheckResult` / `ExperimentManifest` / `ExperimentOutcome` / `PreparedExperiment` は D07、`RunConfig` / `RunStatus` は D06、`CompiledStrategy` は D05、`ParameterValue` は D04、`PartitionId` / `HoldoutState` / `SnapshotRef` は D03・D02、各 ID とダイジェストの型は D02 が正本である。

## 4. 段階5 の全体像【提案】

### 4.1 探索の実験1回の流れ

`odyssey-fx experiment run` に、`search_plan` と `split` が `NONE` でない実験設定（書式 v2）を渡したときの流れである。`NONE` の実験は D07 §19 のまま変わらない。

1. **読込と列挙**（`app.config`・合成）: 実験設定を読み、探索計画から試行を列挙し（第5.2節）、分割から fold を展開する（第6.1節）。試行ごとにコンパイルし、試行 × fold × 局面（選定区間・検証区間）の `RunConfig` と予測 `RunId` を組み立てる。
2. **事前検査と記録票の保存**（D07 §19.4 と同じ）: 研究ポリシーの事前検査を行い、既存の run 成果物を確かめ、記録票を保存する。記録票には**全試行の割当とコンパイル結果、全 fold の区間、選定と合否の規則**が入る（第10.2節）。**記録票の保存が成功するまで、どの run も始めない**。
3. **fold ごとに**、番号の昇順で次を行う（第6.6節）。
   1. 選定区間で、全試行を試行番号の昇順に run し評価する（第10.5節）。
   2. 選定規則を選定区間の評価結果**だけ**に当てて1つを選び、**選定記録を保存する**（第7.2節・第7.5節）。
   3. 選んだ試行を検証区間で run し評価する（Q4 の推奨）。
   4. 合否規則をその fold の検証結果に当てる（第7.3節）。
4. **集約と結末**: fold ごとの判定を集約して実験の合否（`SearchVerdict`）を決め、集約表と結末記録とレポートを書く（第10.6節・第11節）。
5. **最終検証**（任意・別コマンド）: 合格した実験だけが、`odyssey-fx experiment finalize` で封印期間を1回だけ読む（第9.6節）。探索の経路（手順1〜4）は封印期間へ触れる経路を1本も持たない（第9.1節）。

### 4.2 段階5 の完了条件と、それを満たす節

| 完了条件（全体計画書 §8.2） | 満たす節 | 示すテスト（第13節） |
|---|---|---|
| 選定が train 内で閉じる | 第7.2節（選定関数の入力は選定区間の評価だけ）、第7.5節（選定記録を検証の run より前に保存） | 受入・意味論 |
| 全試行を記録する | 第10.2節（記録票に全試行を事前に列挙）、第10.3〜10.5節（試行記録と、試行済み・未試行・失敗・中断の区別） | 受入・意味論 |
| holdout を通常探索で読めない | 第9.1節（探索の経路の許可集合は研究履歴だけ）、第9.3節（封印期間は `holdout_gate` を通る最終検証だけ） | 受入・意味論 |

## 5. 探索計画 `SearchPlan`

### 5.1 書き方【提案】

実験設定（書式 v2）の `search_plan` に書く（D07 §18.2 が「段階5 で D09 が語彙を足す」としたキー）。段階4 の `NONE` はそのまま有効である。

```yaml
search_plan:
  kind: GRID
  max_trials: 12
  axes:
    - {instance: breakout, parameter: lookback_bars, values: [10, 20, 30]}
    - {instance: take_profit, parameter: rr, values: [1.5, 2.0]}
```

- `axes` の各行は、戦略ファイルの使用箇所（`instance`）とそのパラメータ（`parameter`）を指し、試す値を**書いた順**に並べる。値は D04 §7 の `ParameterValue` として読む（型は部品の契約の `ParameterSpec.value_type` に従う）。戦略ファイルに書かれた値は、軸に挙げたパラメータについてだけ試行の値で置き換わる。軸に無いパラメータは戦略ファイルの値のまま全試行で共通である。
- `max_trials` は**探索空間の大きさの上限を事前に書くもの**である。列挙した試行の数がこれを超えたら、設定の誤りとして読込時に拒否する（第5.5節）。**超えた分を切り捨てて一部だけ試す動作はしない**。どの部分集合を試したかが規則の外で決まるためである。
- 探索アルゴリズムは**格子（`GRID`: 全軸の値の直積をすべて試す）だけ**とする（**Q6** の推奨）。試す集合が結果を見る前に確定し、未試行の区別が列挙だけで決まるためである。

### 5.2 試行の列挙と識別【提案】

- **試行（trial）はパラメータ割当1つ**である。試行は fold と局面ごとに1つずつ run を持つ（第6.2節）。
- 列挙の順序: 軸を `(instance, parameter)` の文字列の昇順に並べ、各軸の値は書いた順のまま、**後ろの軸ほど速く変わる**直積の順に並べる。先頭を 0 として `trial_index` を振る。上の例では `(breakout.lookback_bars, take_profit.rr)` = `(10, 1.5)` が 0、`(10, 2.0)` が 1、`(20, 1.5)` が 2 である。
- `trial_index` は記録票の中で試行を指す鍵であり、選定の同点を解く順序でもある（第7.2節）。値の書き順を変えると番号と同点の解き方が変わるので、書き順は事前固定の一部として記録票に入る（第10.2節）。
- 試行ごとにコンパイルし、`CompiledStrategyRef`（D02 §9.2「探索で作る割当を含む」解決済み設定の識別）を得る。**探索で作った異なる割当は、解決済み設定のハッシュで別実行として識別する**【合意済み】全体計画書 §5.5.2。`compiled_ref` は `RunConfig` に入り（D06 §3）、`ConfigDigest` と `RunId` を分ける（ADR-0006）。
- **コンパイルが拒否した割当は、試行の失敗（`FAILED`）として記録票に残す**【提案】。部品のパラメータの範囲外や、パラメータどうしの関係（D04 §12 の #12）が成り立たない割当は、探索空間の中の「実行できない点」であり、設定の誤りではない。拒否の区分（D05 §5.2 の `CompileRejection` と `check_id`）を `TrialPlan.compile_rejections` に残し、その試行の run は作らない。**失敗した候補も探索履歴に残す**【合意済み】上位設計書 §5.3。
- 軸が指す使用箇所・パラメータが戦略に無い、値の型が `ParameterSpec.value_type` と合わない、は**読込時の拒否**である（第5.5節）。1つの誤記で全試行が失敗として記録され、それが「探索の結果」に見えることを防ぐ。

### 5.3 探索アルゴリズムの範囲（Q6）

段階5 は格子だけとする（Q6 の推奨）。seed 付きの無作為抽出（`RANDOM`）と、途中の結果を見て次を決める適応的な探索は作らない。適応的な探索は、試す集合が結果に依存するので、「未試行」が列挙だけでは決まらず、第10.4節の区別の規則を別に要する。

### 5.4 終了条件と決定論【提案】

- **終了条件は「列挙した全試行について、全 fold の選定区間の run と評価が終端し、選んだ試行の検証区間の run と評価が終端したこと」**である（第6.6節・第10.5節）。時間や件数で途中打ち切りする条件は置かない（`max_trials` は読込時の上限であり、打ち切りではない）。
- 途中で止まった（例外・プロセスの中断）場合の扱いは第10.4節。**途中からの実行再開は別設計**であり本書は作らない【合意済み】全体計画書 §7.5。同じ版を再実行すると D07 §19.6 の再利用の規則で既存の run を読み直す（第11.3節）。
- **実行の順序は、fold の昇順 → 局面（選定区間 → 検証区間）→ 試行番号の昇順**に固定する。並列に走らせない（並列化は D10）。各 run の結果は run ごとに決定論的であり（D07 §9）、選定は並べた評価結果の純粋関数なので（第7.2節）、実行の順序は結果に影響しないが、記録の書き込み順と中断時の状態を再現できるようにするため固定する。
- domain と application は乱数を使わない。格子だけなので seed も要らない。

### 5.5 拒否する探索計画・分割【提案】

`app.config` は次を**読込時に拒否**し、`ConfigError` で理由を示す（D07 §18.6 と同じく検査を後段へ回さない）。

1. `kind` が語彙に無い（段階5 は `NONE` / `GRID`）。`GRID` なのに `axes` が空。`NONE` なのに `axes` がある。
2. 軸の `(instance, parameter)` が戦略に無い、同じ組の軸が2つある、値の型が契約の `ParameterSpec.value_type` と合わない、値の列が空、同じ値が2回ある（正規化エンコードで比べる。D02 §9.3）。
3. 列挙した試行の数が `max_trials` を超える。`max_trials` が正の整数でない。
4. `search_plan` と `split` の片方だけが `NONE`（探索だけ・分割だけの実験は作らない。探索せずに分割する使い方は `axes` を1値の軸1本にして書く）。
5. 分割の規則違反（第6.1節の検査1〜5）。
6. `split` が `NONE` でないのに、トップレベルの `run_interval` がある（fold の区間と二重になる。第6.1節）。
7. `selection` / `acceptance` が無い、語彙に無い、参考値の指標を指す（第7.1節）。
8. `split.kind` が `TRAIN_VALIDATION`（fold が1つ）なのに `acceptance.aggregation` が `MEDIAN`（1件の中央値は `ALL_FOLDS` と同じ判定になり、書いた意図と実際の判定が食い違う。第7.3節）。

## 6. 分割

### 6.1 書き方【提案】

```yaml
split:
  kind: WALK_FORWARD
  purge: "0s"
  folds:
    - {train: {start: "2016-01-03T22:00:00Z", end: "2018-01-01T22:00:00Z"}, validation: {start: "2018-01-01T22:00:00Z", end: "2019-01-01T22:00:00Z"}}
    - {train: {start: "2017-01-01T22:00:00Z", end: "2019-01-01T22:00:00Z"}, validation: {start: "2019-01-01T22:00:00Z", end: "2020-01-01T22:00:00Z"}}
  final_holdout: NONE
```

- **fold は区間を明示して並べる**。1つの fold は選定区間（`train`）と検証区間（`validation`）の組である。区間の長さ・ずらし幅・起点から fold を生成する書き方は作らない【提案】。生成規則を持つと、月・取引日・暦日のどれで数えるかという規則がもう1つ要り、生成した区間は結局記録票に展開して残すことになるため、最初から展開した形で書く。**評価窓の長さは結果を見る前に固定する**【合意済み】全体計画書 §5.5.2。区間そのものが実験設定に書かれ、記録票に入る（第10.2節）ので、長さも事前に固定される。
- `kind` は `TRAIN_VALIDATION`（fold がちょうど1つ）か `WALK_FORWARD`（fold が2つ以上）。意味は同じ規則の上の呼び分けであり、処理は変わらない。
- 区間は D02 の半開区間 `[start, end)`。期間は D04 §13.1 の `<整数><単位>`（`purge` は 0 を許すので `"0s"` と書ける）。
- `final_holdout` は最終検証の区間と目的（第9.2節・第9.6節）。使わない実験は `NONE` と書く（省略は拒否。暗黙の既定値を置かない）。

読込時の検査（違反は第5.5節の5）:

1. 各区間は空でない（`start < end`）。
2. 各 fold で `train.end + purge <= validation.start`。
3. fold の番号は書いた順に 0 から振る。**検証区間は fold の順に重ならず前へ進む**（`validation_k.end <= validation_{k+1}.start`）。選定区間は前の fold の検証区間と重なってよい（walk-forward の通常の形）。
4. `final_holdout` があれば、その区間の開始はすべての fold の検証区間の終わり＋`purge` 以上。
5. `purge` は 0 以上。
6. すべての fold の選定区間と検証区間の終わりが、研究履歴の期間境界（D03 §3.8）以下（第9.1節）。

### 6.2 1つの fold の run【提案】

- fold `k` の選定区間の run は、`run_interval = train_k` で全試行について1本ずつ、検証区間の run は `run_interval = validation_k` で選んだ試行について1本作る（検証区間で走らせる試行は **Q4** の推奨）。
- run を指す鍵は `TrialUnitKey(fold_index, phase, trial_index)` である（本書では「試行の実行単位」、略して単位と呼ぶ）。
- どの run も、口座（`account`）・ポリシー・遅延シナリオ・seed は実験設定の値で同じであり、**違うのは `compiled_ref` と `run_interval` だけ**である。したがって同じ試行を同じ区間で走らせる run は、別の fold でも同じ `RunId` になる（ADR-0006）。例えば fold 0 の検証区間と fold 1 の選定区間が同じ区間なら、同じ試行の run は1つの成果物を共有する。これは D07 §19.6 の再利用の規則で扱う（同じ実験の中の2つ目の単位は、1つ目が書いた成果物を再利用する）。
- 各 run の評価は D07 の `EvaluateRun` がそのまま行う。本書は評価の結果（`EvaluationReport` の `metrics` と状態）を読むだけである。

### 6.3 purge【提案】

- **本基盤では、選定区間の run が検証区間の情報を読む経路は構造的に無い**。run は `run_interval` の終わりより後の判断時刻を持たず、as-of の読み取りは判断時刻より後に公開された足を返さず（D03 §6.2）、run 末尾で残った建玉は終わりの時点の価格で時価評価されるだけで決済されない（D06 §10.1・§10.3）。したがって「ラベルの重なり」を除くための purge は、1本の連続した run の結果を後から区間に切り分ける方式でだけ必要になるもので、fold ごとに独立した run を作る本書の方式では不要である。
- それでも `purge` を**書かせる**のは、選定区間の終わりと検証区間の始まりのあいだに**空白（系列の自己相関を避けるための間隔）**を置きたい実験のためである。値は実験ごとに書き（0 を許す）、記録票に入る。本書は値を選ばない。
- **ウォームアップの読み取りは purge の対象ではない**。検証区間の run は、その開始より前の足を指標の履歴として読む（as-of の履歴窓。D03 §6.2）。これは判断時刻より前に公開済みの情報であり、先読みではない。

### 6.4 fold 境界の建玉と状態【提案】

- **fold ごとの run は互いに独立である**。各 run は実験設定の初期口座から始まり、前の run の建玉・予約・取引機会・部品状態を持ち込まない。run の終わりに残った建玉は D06 §10.1 の手順7 のとおり未決済のまま時価評価され、次の run へ持ち越さない。
- 残った建玉の損益の扱いは、D07 の指標の定義のとおりである: 確定損益の指標（#1・#15 など）には入らず、含み損益込みの最大ドローダウン（#5・#6）と日次の資産（#17）には時価評価として入る。仮に閉じた損益（#14）は参考値であり選定と合否に使えない（第7.1節）。本書はこれらを作り直さない。
- **fold をまたいで建玉と状態を持ち越すこと（将来の WF 持ち越し）は別設計**【合意済み】全体計画書 §7.5。

### 6.5 ウォームアップと採点区間（Q5）

**run の区間をそのまま採点区間とし、ウォームアップは run の開始より前の足を履歴として読むことで賄う**（**Q5** の推奨）。

- 指標部品は状態を持たず、毎回の評価を履歴窓だけで決める【合意済み】D05 Q11（D05 §4.5）。したがって指標の値は run の開始位置に依存しない。窓が run の開始より前の足を必要とすれば as-of のビューがそれを返し、データの始まりより前に及べば `WARMUP_INSUFFICIENT` で評価を見送る（D03 §6.2・D05 §6.3）。
- run の開始位置に依存するのは、**run の中で積み上がる状態**（取引機会・Trigger の記憶・評価要求）だけである。これらは各 run の開始時に空であり、採点区間の最初のしばらくは、区間の外から続く連続した run と違う判断をしうる。この差は fold ごとの独立な run という方式に伴うものとして、第6.4節とともに記録票の分割の群に残す（レポートの注記。第11.4節）。
- ウォームアップ用の足を読む partition は、探索の経路では研究履歴だけである（第9.1節）。最終検証の run は、研究履歴の足をウォームアップに読み、封印期間の足を採点区間に読む（第9.6節）。

### 6.6 fold の状態×出来事表（必須表）【提案】

1つの fold の進み方。fold は番号の昇順に1つずつ進む（第5.4節）。状態は「未着手」「選定区間の実行中」「選定済み（選定記録あり・検証の単位は未開始）」「検証の実行中」「判定済み（終端）」「候補なし（終端）」「中断（導出。プロセスが止まった後に記録から導く。第10.4節）」の7つ。

| 状態 ＼ 出来事 | 順番が来た（前の fold が終端した。fold 0 は記録票の保存が成功した） | 選定区間の全単位が終端し、選定で候補が1つ以上あった | 選定区間の全単位が終端し、候補が1つも無かった | 検証区間の単位の開始記録を書いた | 検証区間の単位が終端した | 例外・プロセスの中断 |
|---|---|---|---|---|---|---|
| **未着手** | → **選定区間の実行中**。試行番号の昇順に単位を始める（第10.5節） | 到達しない（単位はまだ1つも始まっていない。第5.4節の順序） | 到達しない（同左） | 到達しない（検証は選定記録の後。第7.5節） | 到達しない（同左） | 変化なし。この fold の記録は何も無く、導出でも未着手のまま（単位はすべて未試行。第10.4節） |
| **選定区間の実行中** | 到達しない（この fold は既に始まっている） | 選定記録を保存して → **選定済み**（第7.2節・第7.5節） | 選定記録（`selected_trial_index = None`）を保存して → **候補なし**（終端。判定は `NO_CANDIDATE`。第7.4節） | 到達しない（選定記録の保存より前に検証の単位を始めない。第7.5節） | 到達しない（同左） | → **中断**。開始記録だけの単位は中断、開始記録も無い単位は未試行（第10.4節）。選定記録は無い |
| **選定済み** | 到達しない | 到達しない（選定は fold に1回。選定記録は書き換えない。第7.5節） | 到達しない（同左） | → **検証の実行中**（検証するのは選んだ試行の1単位。第7.7節） | 到達しない（開始記録の前に終端しない） | → **中断**。選定記録は残り、検証の単位は未試行 |
| **検証の実行中** | 到達しない | 到達しない | 到達しない | 到達しない（検証の単位は1つ） | 合否規則を当ててこの fold の判定を決め（第7.3節）→ **判定済み**（終端。`PASSED` / `FAILED` / `VALIDATION_NOT_COMPLETED`） | → **中断**。選定記録は残り、検証の単位は中断 |
| **判定済み**（終端） | 到達しない（次の fold の出来事であり、この fold は変わらない） | 到達しない | 到達しない | 到達しない | 到達しない | 変化なし（選定記録と試行記録から判定を導ける。第10.6節） |
| **候補なし**（終端） | 到達しない | 到達しない | 到達しない | 到達しない（検証の単位を作らない。第10.4節） | 到達しない（同左） | 変化なし |
| **中断**（導出） | 到達しない（プロセスが居ない。同じ版の再実行は新しい実行として fold 0 から始まる。第11.3節） | 到達しない（同左） | 到達しない（同左） | 到達しない（同左） | 到達しない（同左） | 到達しない（同左） |

- 候補なしの fold があっても、**後ろの fold は続けて実行する**【提案】。全試行を記録する（完了条件2）ためであり、実験の合否は第7.3節の集約で決まる。
- 選定記録の書き込みの失敗（既にある。R4）は構造エラーとして例外になり、「例外・プロセスの中断」の列に当たる。

## 7. 選定・合否・集約

### 7.1 事前固定【合意済み】＋【提案】

- **評価窓の長さ・選定指標・合否閾値・集約方法は、結果を見る前に固定する**【合意済み】全体計画書 §5.5.2・上位設計書 §5.3。本書では、これらを実験設定の `split` / `selection` / `acceptance` に書かせ、記録票に入れる（第10.2節）。記録票は run より前に保存され、同じ版で内容を変えることは研究ポリシーの検査 P3 が拒否する（D07 §19.3・§20.3）。これで「結果を見てから規則を変える」経路は、版を上げて新しい実験として記録する以外に無くなる。
- **値を誰が決めるか**は **Q3** である。本文は推奨（実験ごとに記録票で固定し、本書は語彙と意味だけを決める）で書く。
- **選定と合否に使える指標は採用指標だけ**【提案】。参考値（D07 §22.2 の #7・#8・#11・#13・#14）は「採否の判断に使わない」と D07 §5.3 が定めている。指定したら読込時に拒否する（第5.5節の7）。
- 閾値は十進数の文字列で書き、`Decimal` として比べる（D07 §5.5 の十進数の規則と揃える。`float` を使わない）。比べる相手の値の種類は D07 §5.1 の `MetricValue` の区分に従い、`AmountValue` は金額（口座通貨。通貨は比べない。D07 §7.1 で口座通貨に統一済み）、`RatioValue` は比率、`CountValue` は整数、`DurationValue` と `PriceOffsetValue` を指す条件は段階5 では書けない（読込時に拒否。時間と価格差の閾値の書き方を増やさない）。

### 7.2 選定規則【提案】

```yaml
selection:
  metric: ANNUALIZED_SHARPE_RATIO
  direction: MAXIMIZE
  eligibility:
    - {metric: TRADE_COUNT, comparator: GE, threshold: "30"}
```

- **選定は fold ごとに、その fold の選定区間の評価結果だけを入力にする純粋関数**である（`domain.search`）。入力の型は「fold 番号と、その fold の選定区間の単位ごとの評価結果の列」であり、検証区間の結果や他の fold の結果は**型として渡せない**。これが完了条件1「選定が train 内で閉じる」の構造上の保証である。
- 手順:
  1. 全試行を候補の区分に分ける（第7.4節）。`CANDIDATE` だけが候補である。
  2. 候補のうち、`eligibility` の条件をすべて満たすものだけを残す（満たさないものは `EXCLUDED_INELIGIBLE`）。
  3. 残った候補の `metric` の値が最大（`MAXIMIZE`）または最小（`MINIMIZE`）のものを選ぶ。
  4. 同点（`Decimal` として等しい）は、`trial_index` の最も小さいものを選ぶ。
- 選定の結果は `FoldSelection` として、**全試行の候補の区分と、使った評価の識別子**を持つ。どの評価を見て選んだかが記録から辿れる。

### 7.3 合否規則と fold の集約【提案】

```yaml
acceptance:
  aggregation: ALL_FOLDS
  conditions:
    - {metric: ANNUALIZED_SHARPE_RATIO, comparator: GE, threshold: "0.5"}
    - {metric: MAX_DRAWDOWN_MTM_RATE, comparator: LE, threshold: "0.2"}
```

- 各 fold の判定（`FoldVerdict`）: 選んだ試行の**検証区間の評価**に `conditions` をすべて当て、すべて満たせば `PASSED`、1つでも満たさなければ `FAILED`。**値なし（`Unavailable`）の指標を指す条件は「満たさない」とする**（値なしを合格に寄せない。D07 §5.1）。検証区間の単位が試行済みでない、または評価が `COMPLETED` でない、または事後検査が合格でないなら `VALIDATION_NOT_COMPLETED`。候補が無かった fold は `NO_CANDIDATE`。
- 実験の合否（`SearchVerdict`）は `aggregation` で決める。
  - `ALL_FOLDS`: **すべての fold が `PASSED`** なら `PASSED`、そうでなければ `FAILED`。
  - `MEDIAN`: 条件ごとに、全 fold の検証区間の値の**中央値**を求め、すべての条件を中央値が満たせば `PASSED`。fold の数が偶数なら中央の2つの平均（カーネル精度で1回割る。D02 §4.1）。**1つでも `PASSED` / `FAILED` 以外の fold（`NO_CANDIDATE` / `VALIDATION_NOT_COMPLETED`）があれば `FAILED`**、値なしの fold があればその条件の中央値は求めず `FAILED`（値なしを 0 や除外で埋めない。D07 §5.1）。
- `MEDIAN` は fold が2つ以上の分割（`WALK_FORWARD`）でだけ書ける（第5.5節の8）。
- 集約の語彙はこの2つだけとする【提案】。fold の run を連結して1つの指標を計算し直す方式（プールした指標）は、**単一実行の指標定義を作り直す**ことになるので作らない（D07 §14）。

### 7.4 値なし・失敗の扱い【提案】

選定の入力で、各試行は次の順に最初に当てはまる区分になる。

| 区分 | 条件 |
|---|---|
| `EXCLUDED_TRIAL_FAILED` | 試行がコンパイル拒否で失敗している（第5.2節） |
| `EXCLUDED_NOT_COMPLETED` | 選定区間の単位が試行済みでない、または run が正常完走していない（`RunStatus != COMPLETED`）、または評価が `COMPLETED` でない（D07 §10.1。`REJECTED` / `FAILED`） |
| `EXCLUDED_POST_RUN_CHECK` | 単位の事後検査（P4・P5。第10.8節）が合格でない |
| `EXCLUDED_METRIC_UNAVAILABLE` | 選定の `metric`、または `eligibility` のどれかの指標が値なし（`Unavailable`。D07 §10.3） |
| `CANDIDATE` | 上のどれでもない（この後 `eligibility` で `EXCLUDED_INELIGIBLE` になりうる。第7.2節の手順2） |

- **値なしの指標を持つ試行を 0 や最下位として並べない**。候補から外して区分で残す（D07 §5.1「値なしを 0 や成功値へ置換しない」と同じ考え方）。
- **正常完走していない run の結果を候補にしない**（D07 §4.4・上位設計書 §4.7.13 C「失敗した run を採用評価に混ぜない」の探索への適用）。

### 7.5 選定記録を検証の run より前に保存する【提案】

fold の選定を決めたら、**検証区間の単位を始める前に、選定記録（`FoldSelection`）をファイルに保存する**（第11.1節の `selection_f<k>.json`）。検証区間の結果を見た後で選び直した記録が残らないようにするためであり、記録票を run より前に保存するのと同じ考え方である（D07 §19.4）。選定記録は同じ実行の中で書き換えない（既にあれば何も書かずに失敗する。成果物の書き込みの規則 R4 と同じ。D07 §8.2）。

### 7.6 値を誰が決めるか（Q3）

**実験ごとに実験設定に書き、記録票で事前に固定する。本書は語彙と意味だけを決める**（**Q3** の推奨）。研究ポリシーは「書かれていること」「参考値を使っていないこと」だけを確かめる（第5.5節の7。読込時の拒否）。共通の値を研究ポリシーに置く案は第17節。

### 7.7 検証区間で走らせる試行（Q4）と最終検証へ進める試行（Q12）

- **検証区間では、選んだ1試行だけを run する**（**Q4** の推奨）。全試行を検証区間で走らせると、その結果を人が見て「検証区間で良かった試行」を次の実験で選ぶ経路が開き、検証区間が事実上の選定区間になる。
- **最終検証（第9.6節）に進める試行は、最後の fold（番号の最も大きい fold）で選ばれた試行とする**（**Q12** の推奨）。`TRAIN_VALIDATION` では唯一の fold の選定がこれにあたる。walk-forward の各 fold は時間を進めながら同じ規則で選び直すので、最後の fold の選定が「最新の選定区間で選んだ試行」にあたる。最後の fold が候補なしなら、実験の合否は `FAILED` なので最終検証に進めない（第9.3節の前提）。

## 8. 頑健性（Q9）

**段階5 では頑健性を記録だけにし、合否には使わない**（**Q9** の推奨）。記録するのは次の2つで、どちらも既にある評価の値を並べるだけで、新しい指標を定義しない。

1. **fold 間のばらつき**: 選定の指標と合否の条件の指標について、各 fold の選定区間（選んだ試行）と検証区間の値の最小・中央値・最大。レポートに表で出す（第11.4節）。候補なしの fold は選んだ試行が無いので数えない。
2. **選んだ試行の近傍**: 各 fold で、選んだ試行と**1つの軸だけが1段隣の値**である試行（格子の近傍）の、選定区間の選定の指標の値と候補の区分。選んだ点だけが突出しているのかどうかを読むためのもの。

**値なしの扱い**: 最小・中央値・最大は、その指標が値を持つ fold だけから求め、**値なし（`Unavailable`）の fold の数を理由ごとに並べて出す**。値を持つ fold が1つも無ければ3つとも「値なし」と表示する。値なしを 0 や除外の黙認で埋めない（D07 §5.1）。中央値の求め方は第7.3節の `MEDIAN` と同じ（偶数個なら中央の2つの平均）。近傍の値なしは、値の代わりに理由を表示する。**記録先はレポートだけ**である。どちらも集約表 `trial_metrics`（第11.2節）と選定記録から導く表示であり、新しく保存する値ではない（途中で止まった実験では集約表が無いので、頑健性の表は出さない）。合否（第7.3節）の値なしの扱いとは独立で、ここでの表示は合否を変えない。

D07 §5.5 が「段階5」とした分布指標（最大連敗数・ソルティノレシオ・歪度・尖度）と、D07 §12 が段階5 に送った**遅延シナリオ別・執行粒度別の比較**は、推奨案では作らない（Q9 の選択肢2・3 で扱う。第17節）。執行粒度別の比較は Q9 のどの選択肢でも作らない（段階5 の実験設定は執行系列を1つだけ書く。必要なら別の実験として書き、レポートを並べて読む）。

## 9. holdout の隔離と封印期間の許可判定

### 9.1 探索の経路から封印期間を読めない構造【合意済み】＋【提案】

- **合成（`app.composition`）が探索の run の as-of ビューへ渡す許可 partition 集合は、研究履歴（`RESEARCH_HISTORY`）の partition だけ**である【合意済み】D03 §3.8（段階2〜4 の規則）。本書はこれを探索のすべての単位にそのまま適用する【提案】。as-of ビューは許可集合の外を読めず（`HoldoutAccessViolation`。D03 §6.1）、封印期間（`LEGACY_HOLDOUT`）と隔離期間（`QUARANTINED_UNASSIGNED`）の partition は `holdout_gate` を通らない経路では許可集合に入らない【合意済み】D01 §10.2・D03 §6.1。
- 研究ポリシーの検査 P2（許可集合がすべて研究履歴であること。D07 §20.3）を**探索の全単位に当てる**（第10.8節）。合成の規則が将来変わっても、記録票の段階で止まる二重の保証である。
- **fold の区間（選定区間・検証区間）の終わりが研究履歴の境界（D03 §3.8 の期間境界。初版は `2024-01-01Z`）を超える設定は、読込時に拒否する**【提案】（第5.5節の5 に含める。第6.1節の検査6）。拒否しないと、その単位の run は許可集合の外の足を要求して止まり（`HoldoutAccessViolation`。D03 §6.1）、探索の途中で実験全体が例外で終わる。読めないこと自体は構造で守られているが、止まる場所を読込へ前倒しして、区間の書き誤りを実験の記録より前に知らせる。境界は snapshot を開かずに決まる定数なので、読込で snapshot を見ない規則（D07 §18.6）と両立する。
- **封印期間を読む経路は `experiment finalize`（第9.6節）の1本だけ**であり、その中で `holdout_gate` を必ず通る。`experiment run` は `holdout_gate` を呼ばない。

### 9.2 段階5 の最終検証に使う期間（Q1）

2026-09-25 の時点で、承認待ちの snapshot の旧基盤の閲覧履歴（`legacy_access`）は0件であり、**未観測（`SEALED`）の封印期間は1件も無い**（D07 §24）。ADR-0014 の規則では、履歴を確認できない partition は `CONSUMED` になる。したがって、**実データで封印期間を読む最終検証は、いまの snapshot では1回も行えない**。

**段階5 の最終検証は ADR-0014 の封印期間（`SEALED`）だけで行い、封印期間が無い間は実データの最終検証を行わない。`holdout_gate` は設計し実装し、人工データの封印 partition で確かめる**（**Q1** の推奨。人工データの扱いは Q2）。段階5 の選定と合否は研究履歴の中の選定区間と検証区間で閉じる。研究履歴の末尾を実験ごとの最終検証区間として取り分ける案、使用済みの 2024〜2025 年を参考の検証に使う案は第17節。

### 9.3 `holdout_gate` の手順【提案】（ADR-0014 の具体化）

ADR-0014 の fail-closed の手順（許可の発行 → 消費記録の追記 → データ公開）と消費遷移の直列化（origin への push を compare-and-set とする）を、`HoldoutGate` の2つの操作に割り当てる。**どの段で失敗してもデータを返さない**【合意済み】ADR-0014。

**`issue`（許可の発行）**。入力は記録票・結末記録・snapshot・対象の partition 集合。次をこの順に確かめ、1つでも満たさなければ `GateRefusal` を返す（何も書かない）。

1. 実験が最終検証に進めること（`EXPERIMENT_NOT_ELIGIBLE`）: 結末記録の `status` が `COMPLETED`（D07 §19.4）、探索の合否が `PASSED`（第7.3節）、記録票の `split.final_holdout` が `NONE` でない、目的（`purpose`）が空白だけでない。
2. 対象の partition がすべて `LEGACY_HOLDOUT` である（`NOT_LEGACY_HOLDOUT`。`QUARANTINED_UNASSIGNED` はいかなる経路でも許可しない【合意済み】D03 §3.8）。対象の集合は、`final_holdout.interval` と執行・入力の系列から合成が決め、`issue` に渡す。
3. origin の既定ブランチから閲覧記録を取得して状態を導出できる（`HoldoutAccessLog.fetch_states`。取得・導出の失敗は `ACCESS_LOG_UNREADABLE`）。
4. 対象の partition がすべて `SEALED` である（`NOT_SEALED`）。1つでも `CONSUMED` なら拒否する。**`CONSUMED` は holdout としての再選定・最終評価に永久に使えない**【合意済み】ADR-0014。同じ実験の2回目の最終検証も、1回目で `CONSUMED` になっているのでここで拒否される。

満たせば `HoldoutPermit` を返す。`permit_id = digest(experiment_id, snapshot_id, 対象の partition の整列済み列, purpose)`（D02 §9.3 の正規化エンコード）。**許可はメモリ上の値であり、この時点ではデータを公開しない**。

**`consume`（消費記録の追記と公開）**。

1. `HoldoutAccessLog.append_consumption(permit)` で、対象の partition ごとに消費記録（第9.4節）を閲覧記録へ追記し、コミットし、origin の既定ブランチへ push する。
2. 戻り値が `PUSHED` のときだけ、対象の partition の集合を返す（公開してよい partition）。合成はそれを研究履歴の partition と合わせて as-of ビューの許可集合にする。
3. `REJECTED_NON_FAST_FORWARD`（他のクローンが先に追記した）なら `PUSH_REJECTED`、`ORIGIN_UNREACHABLE` なら `ORIGIN_UNREACHABLE`、閲覧記録のファイルが git で追跡されていないなら `ACCESS_LOG_UNTRACKED` を返し、公開しない。再試行は人間の判断である【合意済み】ADR-0014。

- **ローカルのコミットだけが残り push に失敗した場合**、ローカルの作業ツリーの閲覧記録には消費記録が残るが、正本は origin の既定ブランチの閲覧記録である【合意済み】ADR-0014。次の `issue` は origin から取得して導出するので、ローカルに残った行で状態を誤らない。ローカルの行を取り除くかどうかは人間の判断とし、gate は取り除かない（追記専用の記録を gate が書き換えない）。
- `issue` と `consume` のあいだに他のクローンが消費した場合は、`consume` の push が non-fast-forward で拒否される（compare-and-set）【合意済み】ADR-0014。

### 9.4 閲覧記録の行【提案】

D03 §7.4 の値の伝播表が「行の鍵は gate の実装とともに決める」とした点を決める。行の形と追記・導出の規則の正本は D03 §3.8 であり、本書は gate が書く行に**何を載せるか**と**鍵**だけを決める（D03 の型に項目を足す必要がある。第16節の改訂依頼）。

| 項目 | 内容 |
|---|---|
| 種別 | 消費（`CONSUMED`）。使用済み期間の opt-in（第9.7節）は `RESEARCH_OPT_IN` |
| partition | 対象の partition 1つ |
| `permit_id` | 第9.3節の許可の識別子。1回の最終検証で追記する複数の行を1つに束ねる |
| 実験 | `experiment_id` と `experiment_name` / `experiment_version`（記録票から） |
| 目的 | `final_holdout.purpose`（記録票から。人が書き直せない事前固定の値） |
| 記録時刻・記録者 | `app` が渡す（記録だけ。鍵に入れない。D01 §2.2 規則2） |

- **行の鍵は `(partition, permit_id)`** とする。同じ許可で同じ partition を2回追記しない。gate より前に書かれた `permit_id` を持たない行は、状態の導出では従来どおり扱い（D03 §3.8）、この鍵の一意性の対象にしない。別の許可による同じ partition の消費の行は、2回目の許可が `issue` の手順4 で拒否されるので通常は生じないが、生じた場合も状態は `CONSUMED` のままで導出は変わらない（消費は冪等な終端状態。D03 §3.8）。

### 9.5 封印期間の許可の状態×出来事表（必須表）【提案】

`experiment finalize` 1回の中で、1つの許可が進む状態。表の「要求前」は、第9.6節の手順1〜3（記録の照合・試行の決定・出力先の事前検査）を通った後の状態である。手順1〜3 で止まった `finalize` は許可の要求に入らず、何も書かず閲覧記録にも触れない（終了コード 2 または 5。第11.4節）。partition 1つの封印状態（`SEALED` / `CONSUMED`）の表は D03 §3.8.1 が正本であり、本表はそれを読む側（gate）の進み方である。

| 状態 ＼ 出来事 | 前提（第9.3節の `issue` の1・2）を満たす | 前提を満たさない | 閲覧記録の取得・導出に失敗 | 対象がすべて `SEALED` | 対象に `SEALED` でないものがある | push 成功（`PUSHED`） | push 拒否（non-fast-forward）・origin に到達できない・未追跡 | 最終検証の run と評価が終端した（状態は問わない） | 例外・中断 |
|---|---|---|---|---|---|---|---|---|---|
| **要求前** | → 閲覧記録の取得へ（同じ状態の中で手順3 に進む） | → **拒否**（終端。`EXPERIMENT_NOT_ELIGIBLE` または `NOT_LEGACY_HOLDOUT`。何も書かない） | → **拒否**（終端。`ACCESS_LOG_UNREADABLE`） | `permit` を作って → **許可済み**（メモリ上。データは公開しない） | → **拒否**（終端。`NOT_SEALED`） | 到達しない（push は許可の後） | 到達しない（同左） | 到達しない（run は公開の後） | 終了（何も書かない。データは公開していない） |
| **許可済み**（メモリ上） | 到達しない（前提は検査済み） | 到達しない（同左） | 到達しない（取得は済んだ） | 到達しない（同左） | 到達しない（同左） | → **公開済み**。公開してよい partition を返し、`finalize` は消費の控え `final/permit.json` を書く（第9.3節の `consume` の2、第9.6節の5） | → **拒否**（終端。`PUSH_REJECTED` / `ORIGIN_UNREACHABLE` / `ACCESS_LOG_UNTRACKED`。データを返さない。ローカルに残った行は第9.3節の段落のとおり） | 到達しない（公開前に run しない） | 終了。push 前ならデータは公開されていない。push が成功したかどうかは origin の閲覧記録が正本であり、次の `issue` がそれを読む |
| **公開済み**（最終検証の run 中） | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない（push は1回） | 到達しない（同左） | 最終検証の記録（第9.6節）を書いて → **最終検証済み**（終端） | 終了。**消費は取り消さない**（`SEALED → CONSUMED` は不可逆。ADR-0014）。最終検証の記録は書かれず、同じ実験で再び最終検証はできない（次の `issue` の手順4 で拒否）。消費の控えが残るので `experiment report` が「消費済み・最終検証は中断」と表示する（第9.6節の5）。run の成果物は `runs/<run_id>/` に残りうる |
| **最終検証済み**（終端） | 到達しない（1回の `finalize` はここで終わる） | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 変化なし（記録は書き終えている） |
| **拒否**（終端） | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 変化なし |

### 9.6 最終検証のコマンド【提案】

`odyssey-fx experiment finalize --experiment-dir <runs/experiments/<名前>/v<版>> --snapshots <基点> --out <基点>`。

1. 記録票と結末記録を読み、D07 §21.2 の手順1 と同じく `experiment_id` を再計算して照合する（改変された記録から最終検証へ進まない）。
2. 最終検証へ進める試行を決める（第7.7節。最後の fold の選定記録）。
3. **出力先の事前検査（消費より前）**: 最終検証の run の `RunConfig` と予測 `RunId`・予測の評価の識別子（D07 §9.2）を組み立て、次の**どれか1つでもあれば、消費せずに拒否して終える**（終了コード 5。何も書かない）: `final/permit.json`、`final/final_evaluation.json`、`runs/<予測 RunId>/`、その評価の保存先。**既存の成果物の再利用はしない**（最終検証の run の成果物が消費より前にあることは、封印期間を読んだ run が既にあることを意味し、再利用すると消費の記録と run の対応が崩れる）。出力の基点（`--out`）と実験の版のディレクトリの途中がリンクや別の種類なら、R4 と同じく失敗する（D07 §19.1）。これらはすべてデータの公開より前に判定できるので、**不可逆な消費の後に書き込みの衝突で止まる経路を作らない**。
4. `HoldoutGate.issue` → `consume`（第9.3節）。拒否されたら終了コード 7 で終える（第11.4節）。
5. `consume` が公開してよい partition を返したら、run を始める前に**消費の控え `final/permit.json`**（`permit_id`・`snapshot_id`・partition・目的・試行番号）を書く（既にあれば何も書かずに失敗する。R4）。これ以降に止まると、封印期間は消費済みなのに最終検証の記録が無い状態になる（第9.5節）。`experiment report` は「控えがあり最終検証の記録が無い」を**「消費済み・最終検証は中断」**と先頭に表示する（第11.4節）。push の成功から控えを書き終えるまでのあいだに止まった場合は控えも残らないが、消費の正本は origin の閲覧記録であり（ADR-0014）、同じ実験の次の `finalize` が `NOT_SEALED` で拒否されることでその状態が分かる。
6. 公開された partition と研究履歴の partition を許可集合にして、選んだ試行を `run_interval = final_holdout.interval` で run し、評価する。合否規則の `conditions` を当てて `verdict` を決める。判定の規則は fold の判定（第7.3節の最初の項目）と同じで、run や評価が正常に終わらなければ `VALIDATION_NOT_COMPLETED` とする（集約は1区間なので不要）。
7. 最終検証の記録 `final/final_evaluation.json`（`FinalEvaluationRecord`）を実験の版のディレクトリに書く。既にあれば何も書かずに失敗する（R4）。

- 最終検証の run は研究ポリシーの検査 P2（研究履歴だけ）を満たさない。**P2 の代わりに gate の許可を検査とする**（第16節の改訂依頼。D07 §20.3 の P2 の適用範囲）。
- **最終検証の結果だけが holdout 成績である**。使用済み期間（`CONSUMED`）を読んだ結果を holdout 成績として集計することは拒否する【合意済み】ADR-0014。本書の経路では、`CONSUMED` の partition は `issue` の手順4 で拒否されるので、最終検証の記録に `CONSUMED` の partition が入ることはない。

### 9.7 使用済み期間の opt-in（Q10）

ADR-0014 は、`CONSUMED` の partition を研究・開発用途で読む場合に、明示的な opt-in と、利用の事実と目的の run manifest への記録と、holdout 成績としての集計の拒否を求めている。**段階5 では opt-in の経路を作らない**（**Q10** の推奨）。探索と最終検証の完了条件に要らず、実装すると run manifest の項目（D06 §9.3）と研究ポリシーの検査 P2 の改訂が要るためである。作る場合の設計（書式・記録・検査）は第17節の Q10 の選択肢に書いた。

### 9.8 人工データでの確かめ方（Q2）

人工データの snapshot の partition は、日付によらず常に研究履歴として作る【合意済み】D08 §9.5 の規則1。このままでは `holdout_gate` の許可・消費・公開と、`experiment run` が封印期間を読めないことを、封印 partition を使って確かめられない。

**封印期間のテストに限り、人工データの snapshot に `LEGACY_HOLDOUT` の partition（`SEALED` と `CONSUMED`）を作ってよいとする例外を D08 §9.5 に足す**（**Q2** の推奨。第16節の改訂依頼）。閲覧記録の origin には、テストの中で作るローカルの bare リポジトリを使う（ネットワークに依存しない。D01 §9）。

## 10. 試行の記録

### 10.1 記録票・試行記録・結末記録の関係【提案】

D07 §14 が「探索の試行ごとの記録票と結末記録の関係」を本書へ渡した点を決める。

- **記録票は実験1版に1つ**（D07 §19.1 のまま）。探索の実験では、**全試行の割当とコンパイル結果、全 fold の区間、選定と合否の規則**を記録票に入れる（第10.2節）。試ごとに記録票を作らない。試行の集合そのものが事前固定の対象だからである（試行ごとの記録票では、「どの試行を試す予定だったか」が1か所に残らない）。
- **試行記録は単位（fold × 局面 × 試行）ごとに2つ**: 単位を始める直前に書く**開始記録**（`TrialStartRecord`）と、終端したときに書く**試行記録**（`TrialRunRecord`）。両方とも1ファイルずつで、書き換えない（第11.1節）。
- **選定記録は fold ごとに1つ**（第7.5節）。
- **結末記録は実験の1回の実行に1つ**（D07 §19.3 のまま）。探索の実験では、fold ごとの選定と判定、実験の合否、試行の状態の件数を足す（第10.6節）。
- 集約表（Parquet）は、試行記録と評価の成果物から結末記録と同じ時点に作る**派生の表**である（第11.2節）。正本は試行記録と各 run の評価の成果物である。

### 10.2 記録票に足す項目【提案】

D07 §19.2 の表に、探索の実験で次を足す（`NONE` の実験では足した項目は `None` か空）。

| 群 | 項目 | 内容 |
|---|---|---|
| 事前固定 | `search_plan` | 解決済みの `SearchPlan`（軸の並びと値の書き順を含む） |
| | `split` | 解決済みの `SplitSpec`（展開した全 fold の区間、`purge`、`final_holdout`） |
| | `selection` / `acceptance` | 解決済みの `SelectionRule` / `AcceptanceRule` |
| 解決済みの設定 | `trials` | 全試行の `TrialPlan`（`trial_index` の昇順）。割当、`compiled_ref` かコンパイル拒否、単位ごとの `expected_config_digest` |
| | `compiled_ref` / `expected_config_digest`（D07 §19.2 の単数の項目） | 探索の実験では `None`（試行ごとの値は `trials` にある。第16節の改訂依頼） |
| 複雑性 | `complexity` | 計測値4件は**全試行の最大値**（割当はパラメータの値を変えるだけなので通常は全試行で同じ。D07 §20.4 の数え方は値に依存しない） |
| 事前検査 | `pre_run_checks` | D07 §20.3 の P1・P2・P6 を**全単位の和**で1件ずつ（P2 は全単位の許可集合の和、P6 は上の最大値）。Q7 で試行数の上限を足せばその検査も入る |

- 足した項目はすべて `experiment_id` の識別の入力に入る（D07 §19.2 の「識別の入力」の4 に足す。第16節の改訂依頼）。
- 検証区間の単位の `expected_config_digest` は、**選ばれるかどうかにかかわらず全試行について**入れる。選定は run の後に決まるが、どの試行が選ばれてもその run の設定が事前に固定されているようにするためである（コンパイルとダイジェストの計算だけで run はしない）。

### 10.3 試行記録【提案】

- **開始記録**（`TrialStartRecord`）: 単位の run を始める直前（既存の成果物を再利用する場合も読み出しの前）に書く。`experiment_id`、単位の鍵、予測 `RunId`。
- **試行記録**（`TrialRunRecord`）: 単位の run と評価が終わり、事後検査（P4・P5）を当てた後に書く。`status` は常に `COMPLETED`（試行済み）であり、run と評価そのものの成否は `run_status` / `evaluation_status` が表す（D07 §19.4 の `COMPLETED` と同じ考え方）。
- どちらも JSON（ADR-0027）で、書き出しは `ExperimentStore`（D01 §4）経由、既にあれば何も書かずに失敗する（R4。D07 §8.2）。

### 10.4 試行の状態と `ABORTED`【提案】

試行の状態は**単位ごとに**、記録から次のとおり導く（`TrialStatus`）。導出は純粋関数で、結末記録を書くときとレポートを作るとき（中断した実行を含む）に同じ関数を使う。

| 状態 | 導く条件 | 日本語 |
|---|---|---|
| `FAILED` | 記録票の `TrialPlan.compile_rejections` が空でない（その試行の全単位） | 失敗 |
| `COMPLETED` | 試行記録がある | 試行済み |
| `ABORTED` | 開始記録はあるが試行記録が無い | 中断 |
| `NOT_STARTED` | 開始記録も試行記録も無い | 未試行 |

- **検証区間の単位は、選ばれた試行についてだけ存在する**。選ばれなかった試行の検証区間の単位は「未試行」ではなく、そもそも単位として数えない（第7.7節）。候補なしの fold の検証区間の単位も数えない。
- **`EvaluationStatus.ABORTED`（D07 §10.1。「探索の途中で中断された」。段階4 までは使わない値）は、中断（`ABORTED`）の単位の評価の状態として、`experiment report` が途中で止まった実行から導いて表示する単位の一覧にだけ現れる**。集約表は探索が終端したときにだけ書く（第11.2節）ので、集約表に中断の単位は現れない。評価 manifest に `ABORTED` を書く経路は作らない。中断したプロセスは評価の成果物を書けず、評価 manifest を書けた評価は `COMPLETED` / `REJECTED` / `FAILED` のどれかに決まっているためである（D07 §10.1 の表の `ABORTED` の行「出力 —」と整合する）。
- 途中で止まった実行には結末記録が無い（D07 §19.4 の「結末記録が無い＝途中で止まった」）。`experiment report` は記録票・開始記録・試行記録・選定記録から、各単位の状態と各 fold の状態（第6.6節の「中断」）を導いて表示する。

### 10.5 試行の実行単位の状態×出来事表（必須表）【提案】

1つの単位（fold × 局面 × 試行）の進み方。状態は「未開始」「実行中（開始記録あり）」「試行済み（終端）」「失敗（終端。記録票の段階で決まる）」「中断（導出）」の5つ。

| 状態 ＼ 出来事 | 順番が来た（第5.4節の順序で前の単位が終端した） | 既存の run 成果物を再利用できると分かった（D07 §19.6 の手順2） | run と評価が終わった（状態は問わない） | 事後検査（P4・P5）を当てた | 例外・プロセスの中断 | 同じ版の再実行が始まった |
|---|---|---|---|---|---|---|
| **未開始** | 開始記録を書いて → **実行中**（第10.3節） | 到達しない（再利用の判断は単位を始めた後。下の行） | 到達しない（run は開始記録の後） | 到達しない（同左） | 変化なし（導出では `NOT_STARTED`。第10.4節） | 到達しない（再実行は新しい実行であり、この実行の単位は変わらない。退避は第11.3節） |
| **実行中** | 到達しない（1回だけ起きる） | run せず成果物を読み（`run_reused = true`）、評価の再利用も D07 §19.6 の手順2 で判断して、次の列へ | → 事後検査へ（同じ状態の中で次の列） | 試行記録を書いて → **試行済み**（終端）。事後検査が合格でなければ試行記録の `outcome_checks` に残し、選定では `EXCLUDED_POST_RUN_CHECK`（第7.4節） | → **中断**（導出。開始記録だけが残る） | 到達しない（同上） |
| **試行済み**（終端） | 到達しない | 到達しない | 到達しない | 到達しない（検査済み） | 変化なし（試行記録は書き終えている） | 到達しない（同上） |
| **失敗**（終端。コンパイル拒否） | **変化なし**。run を作らず開始記録も書かず、順番を次の単位へ送る（第5.2節） | 到達しない（run が無い） | 到達しない（同左） | 到達しない（同左） | 変化なし | 到達しない（同上） |
| **中断**（導出） | 到達しない（プロセスが居ない） | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない（この実行の記録は退避され、新しい実行の単位は「未開始」から始まる。第11.3節） |

- 「既存の run 成果物と衝突して再利用できない」ことは、**記録票を保存する前に全単位について確かめる**（D07 §19.6 の手順1〜3 を全単位の予測 `RunId` に当てる）。衝突すれば実験全体を拒否して終える（終了コード 5）ので、単位の実行中には起きない（第10.7節の表の「検査済み」の行）。
- 同じ実験の中で2つの単位が同じ `RunId` を持つ場合（第6.2節）、後の単位は前の単位が書いた成果物を「再利用できる」の列で読む。

### 10.6 結末記録に足す項目【提案】

D07 §19.3 の表に `search: SearchOutcome | None`（探索の実験だけ）を足す。中身は fold ごとの選定（`FoldSelection`）、fold ごとの判定、実験の合否、試行の状態の件数（単位の数で数える）。D07 §19.3 の単数の項目（`run_id` / `run_evaluation_id` / `result_digest` など）は、探索の実験では `None` とし、単位ごとの値は試行記録と集約表にある（第16節の改訂依頼）。

- `status`（`ExperimentStatus`）の意味は D07 §19.4 のまま: `COMPLETED` は探索が最後まで進んだこと（合否は `search.verdict` が表す）、`REJECTED_BY_POLICY` は事前検査で止まったこと、`FAILED_POST_RUN_CHECK` は**どれか1つの単位の事後検査が合格でなかったこと**（第10.8節。探索は最後まで進め、合否も計算するが、レポートは採用不可と先頭に出す）。
- `failed_checks` は D07 §19.3 のまま、合格でなかった検査名（重複なし・宣言順）。

### 10.7 探索の実験1回の状態×出来事表（必須表）【提案】

D07 §19.4 の表（1つの run の実験）を、探索の実験について書き直したもの。`NONE` の実験は D07 §19.4 が正本のまま。

| 状態 ＼ 出来事 | 設定の読込に失敗 | 事前検査が全件合格 | 事前検査に合格でないものあり | 既存の run 成果物と衝突し再利用できない単位がある | 記録票の保存が成功（新規または同一） | 記録票が同じ版で内容違い | 全 fold が終端した | 例外・中断 |
|---|---|---|---|---|---|---|---|---|
| **読込前** | 終了（記録なし。`ConfigError`。第5.5節） | 到達しない（検査は読込・列挙・コンパイルの後） | 到達しない（同左） | 到達しない（同左） | 到達しない（同左） | 到達しない（同左） | 到達しない | 終了（記録なし） |
| **検査済み**（記録票を組み立てた） | 到達しない | 全単位の予測 `RunId` について既存の成果物を確かめ（読むだけ）、衝突が無ければ保存へ | → 保存へ（不合格も記録票に残す。衝突の確認はしない。run しないため） | 記録票も結末記録も書かずに**拒否して終了**（`RUN_ARTIFACT_CONFLICT`、終了コード 5。D07 §19.6 の手順3） | 到達しない（保存の前） | 到達しない（同左） | 到達しない | 終了（記録なし） |
| **保存を試みる** | 到達しない | 到達しない | 到達しない | 到達しない（衝突は保存の前に確かめた） | 事前検査が合格なら、旧い結末記録・レポート・探索の記録を退避して（第11.3節）→ **探索中**。不合格なら結末記録 `REJECTED_BY_POLICY` を書いて**終端** | 結末記録を書かず**拒否して終了**（検査 P3。終了コード 5。D07 §19.4） | 到達しない | 終了（記録票が書けたかは保存の原子性による。D07 §19.4） |
| **探索中**（fold を順に進める。第6.6節） | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 集約（第7.3節）して集約表・結末記録（`COMPLETED`、または事後検査が合格でない単位があれば `FAILED_POST_RUN_CHECK`）・レポートを書いて**終端** | **記録票・開始記録・試行記録・選定記録だけが残る**（結末記録なし）。`experiment report` が中断を導いて表示する（第10.4節） |
| **終端** | 到達しない（コマンドは終わっている） | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 到達しない | 変化なし |

### 10.8 研究ポリシーの検査の探索への適用【提案】

- **事前の検査（P1・P2・P6）** は実験に1回、全単位を合わせて当てる（第10.2節）。1つでも合格でなければ run しない（D07 §20.3）。
- **保存時の検査（P3）** は D07 §20.3 のまま（記録票1つに1回）。
- **事後の検査（P4・P5）** は**単位ごと**に当て、試行記録の `outcome_checks` に残す。P4 は単位の run manifest の `ConfigDigest` が記録票のその単位の `expected_config_digest` と一致し、`run_id` が予測 `RunId` と一致すること。P5 は評価の指標集合の版が記録票と一致すること。合格でない単位は選定の候補から外れ（第7.4節）、実験の `status` は `FAILED_POST_RUN_CHECK` になる（第10.6節）。
- 検査の規則そのもの（P1〜P6 の合格の条件）は D07 §20.3 が正本で、本書は当てる単位だけを決める。

### 10.9 複雑性の上限と試行数の見直し（Q7）、研究ポリシーの版の登録簿（Q8）

- D07 §14 は「複雑性の上限（Q7 決定: 部品数 30・使用箇所数 36・パラメータ数 33・判断を出す使用箇所の数 18）を探索の前に見直す」ことを本書へ渡した。探索はパラメータの**値**を変えるだけで、4つの計測値（D07 §20.4）はどれも値に依存しないので、**探索によって計測値は変わらない**。探索が新しく持ち込む自由度は**試行の数**である。**上限4件は据え置き、研究ポリシーの版 2 で「1実験の試行数（列挙した試行の数）の上限」を足して検査する**（**Q7** の推奨。初期値は Q7 で決める）。
- `max_trials`（第5.1節）は実験が自分で書く上限で読込時に、試行数の上限（Q7）は研究ポリシーが全実験に共通で課す上限で事前検査に、それぞれ独立に当たる。どちらかを超えれば run しない（前者は `ConfigError`、後者は `REJECTED_BY_POLICY`）。
- D07 §20.2 は「版を上げずに研究ポリシーの中身を変えることを登録簿で強制するかは、段階5 で探索が始まる前に判断する」とした。**研究ポリシーの版ごとのダイジェストを git 管理のファイルに登録し、版参照が同じで中身が違うポリシーを読込時に拒否する**（**Q8** の推奨）。

## 11. 成果物の置き場

### 11.1 ディレクトリ【提案】

D01 §10.3 の `runs/experiments/<実験の名前>/v<版>/`（記録票と結末記録、探索履歴、集計）の下に置く。run と評価の成果物は従来どおり `runs/<run_id>/` と `runs/<run_id>/eval/<run_evaluation_id>/` に置き、試行記録がそれを指す。

```text
runs/experiments/<名前>/v<版>/
├── experiment_manifest.json          # 記録票（D07 §19 ＋ 第10.2節）
├── experiment_outcome.json           # 結末記録（D07 §19.3 ＋ 第10.6節）
├── report.md                         # レポート（D07 §22 ＋ 第11.4節）
├── search/
│   ├── units/f<k>_<TRAIN|VALIDATION>_t<i>.start.json   # 開始記録（第10.3節）
│   ├── units/f<k>_<TRAIN|VALIDATION>_t<i>.json         # 試行記録（第10.3節）
│   ├── selection_f<k>.json           # 選定記録（第7.5節）
│   ├── trial_units.parquet           # 集約表1（第11.2節）
│   └── trial_metrics.parquet         # 集約表2（第11.2節）
└── final/
    ├── permit.json                   # 消費の控え（第9.6節の5）
    └── final_evaluation.json         # 最終検証の記録（第9.6節の7）
```

- ファイル名の `<k>` と `<i>` は 0 始まりの10進整数（桁を揃えない）。名前から鍵が一意に読めること、鍵から名前が一意に決まることを求める。
- 書き込みはすべて一時ファイル＋既存を上書きしない改名で行い、既にあれば何も書かずに失敗する（R4。D07 §19.1 の v2.4 の段落と同じ境界。途中がリンクなら失敗）。

### 11.2 集約表の主キーと空表【提案】

| 表 | 1行の単位 | 主キー | 列 |
|---|---|---|---|
| `trial_units` | 単位1つ（失敗の試行は選定区間の fold ごとに1行ずつ。検証区間の単位は第10.4節のとおり選んだ試行の分だけ） | `(fold_index, phase, trial_index)` | 鍵の3列、割当（正規化エンコード文字列）、`compiled_ref`（失敗の試行は空）、`status`（`TrialStatus`。書く時点では `COMPLETED` か `FAILED` だけ）、`run_id`・`run_status`・`run_reused`・`run_evaluation_id`・`evaluation_status`（失敗の試行は空）、`candidate_status`（選定区間の単位だけ。検証区間の単位は空。第7.4節）、`selected`（その fold で選ばれたか） |
| `trial_metrics` | 試行済みの単位 × 評価の指標1件 | `(fold_index, phase, trial_index, metric_id)` | 鍵の4列と、評価の `METRICS` 表の行をそのまま写した列（値・値なしの理由・注記・観測件数。D07 §8.1。**値を計算し直さない**） |

- 整列鍵は主キーと同じ（`phase` は `TRAIN` → `VALIDATION`、`metric_id` は D07 の宣言順）。
- **0行でも列とその型を残して両方の表を書く**（AGENTS.md の「0 行でも列とその型が残る」。例: 全試行が失敗した実験の `trial_metrics`）。
- 集約表は探索が終端したとき（第10.7節の「全 fold が終端した」）に書く。事前検査で止まった実験（`REJECTED_BY_POLICY`）と拒否した実験では `search/` を作らない。途中で止まった実験では書かれず、レポートは記録から導く（第10.4節）。
- 主キーの重複は実装の誤り（入力の試行記録がファイル名で一意なので）であり、構造エラーとする（D01 §2.2 規則5。D07 §9.3 の出力の主キーと同じ扱い）。

### 11.3 同じ版の再実行【提案】

- 同じ実験の同じ版を再実行したときは、D07 §19.3・§22.1 の退避の規則を探索の記録にも当てる: **旧い結末記録・レポートと同じ連番で、`search/` を `search.<n>/` へ退避してから**探索を始める（第10.7節の「保存を試みる」の行）。これで、今回の実行が途中で止まっても前回の試行記録を今回のものとして読まない。
- run と評価の成果物は D07 §19.6 の規則で再利用する（同じコードなら全単位が再利用になる）。
- `final/` は退避しない。最終検証は封印期間を消費した記録であり、実験の版に1回だけ起きる（第9.5節）。

### 11.4 コマンドと終了コード【提案】

| コマンド | 変更 | 終了コード |
|---|---|---|
| `experiment run` | `search_plan` / `split` が `NONE` でない書式 v2 を受ける | D07 §21.3 と同じ（0 / 2 / 3 / 4 / 5。1 は表に無い失敗）。0 は探索が最後まで進んだことで、合否は結末記録とレポートに出る |
| `experiment report` | 探索の節を足す: 最終検証の状態（`final/permit.json` があり記録が無ければ「消費済み・最終検証は中断」。第9.6節の5）、合否と fold ごとの判定を先頭に、試行の状態の件数（試行済み・未試行・失敗・中断）、fold ごとの選定（選んだ試行と値、候補の区分の件数）、頑健性の表（第8節）、fold の独立な run という方式の注記（第6.5節） | D07 §21.3 のまま |
| `experiment finalize`（新規） | 第9.6節 | 0 = 最終検証の記録を書いた（run や評価の失敗を含む）、7 = gate が拒否した（拒否の区分を表示）、5 = 出力先の事前検査で既存の成果物があった（消費していない。第9.6節の3）、2 = 引数・読込の誤り（記録票・結末記録が読めない・照合に失敗した）、1 = 表に無い失敗 |
| `experiment reproduce` | 探索の実験の版のディレクトリを渡されたら、引数の誤り（2）として拒否する（第15節） | D07 §21.3 のまま |

## 12. 値の伝播表（必須表）【提案】

探索の設定から結末記録と最終検証の記録までを運ばれる値。D07 §19.5 の行（`experiment_name` / `experiment_id` / `research_policy_ref` など）は D07 が正本であり、ここには探索で足す値だけを書く。

| 値 | 生成元 | 渡り方 | 記録先 | 主キー |
|---|---|---|---|---|
| 探索計画・分割・選定・合否の規則（`SearchPlan` / `SplitSpec` / `SelectionRule` / `AcceptanceRule`） | 実験設定（人間）。`app.config` が解決（第5.1節・第6.1節・第7.2節・第7.3節） | `PreparedSearch.manifest` | 記録票の事前固定の群（第10.2節）。`experiment_id` の入力 | 記録票は `(experiment_name, experiment_version)` ごとに1つ（D07 §19.3） |
| `trial_index` と割当（`ParameterAssignment`） | `domain.search` の列挙（第5.2節） | `TrialPlan` → `PreparedTrial` → 開始記録・試行記録・選定記録 | 記録票の `trials`、試行記録のファイル名と `unit`、集約表の `trial_index` | 記録票の中で `trial_index` が一意 |
| `compiled_ref`（`CompiledStrategyRef`） | 合成が試行ごとにコンパイル（D05 §5.5） | `TrialPlan.compiled_ref` → `RunConfig.compiled_ref`（D06 §3） | 記録票の `trials`、run manifest（D06 §9.3）、集約表 `trial_units` | 試行ごとに1つ（読込時に重複を拒否。第5.5節の2 で値の重複を拒否するので割当は互いに異なる） |
| `fold_index` と fold の区間 | `domain.splits` の展開（第6.1節） | `Fold` → 単位の `RunConfig.run_interval` | 記録票の `split`、試行記録の `unit`、選定記録、集約表 | 記録票の中で `fold_index` が一意 |
| 単位の鍵（`TrialUnitKey`） | 合成が試行 × fold × 局面で組み立てる（第6.2節） | `PreparedTrial.run_configs` の鍵 | 開始記録・試行記録のファイル名と中身、集約表の主キー | `(fold_index, phase, trial_index)` |
| 単位の `expected_config_digest` | 合成（run の前に `RunConfig` から。D06 §9.3） | `TrialPlan.expected_config_digests` | 記録票（識別に入る。第10.2節） | 単位ごとに1つ |
| 単位の予測 `RunId` | 合成（`expected_config_digest` とこの実行の環境のダイジェストから。ADR-0006） | `PreparedTrial.expected_run_ids` | 開始記録・試行記録（記録票には入れない。D07 §19.2 と同じ理由） | 単位ごとに1つ。同じ実験の2つの単位が同じ値を持ちうる（第6.2節） |
| 単位の `run_id` / `run_status` / `run_reused` | バックテスト（`BacktestRunner.run`）か既存の成果物の読み出し（D07 §19.6） | `RunExperiment` → `TrialRunRecord` | 試行記録、集約表 `trial_units`、`runs/<run_id>/` | 事後検査 P4 で予測 `RunId` と照合（第10.8節） |
| 単位の `run_evaluation_id` / `evaluation_status` / `result_digest` | 評価（D07 §8.3・§9.2） | `EvaluateRun` → `TrialRunRecord` | 試行記録、集約表、`runs/<run_id>/eval/<run_evaluation_id>/` | D07 §9.2 |
| 単位の指標の値（`MetricRecord`） | 評価（D07 §5） | 評価の `METRICS` 表 → 選定・合否の関数の入力（第7.2節・第7.3節） | 集約表 `trial_metrics`（写し） | `(fold_index, phase, trial_index, metric_id)` |
| 候補の区分（`CandidateStatus`） | `domain.search` の選定（第7.4節） | `FoldSelection.inputs` | 選定記録、集約表 `trial_units.candidate_status` | 選定記録の中で `trial_index` が一意 |
| 選んだ試行（`FoldSelection.selected_trial_index`） | `domain.search` の選定（第7.2節） | 選定記録 → 検証区間の単位の選択（第7.7節）→ 最終検証の試行（第9.6節） | 選定記録 `selection_f<k>.json`、結末記録の `search.selections`、集約表 `trial_units.selected`、最終検証の記録の `trial_index` | fold ごとに1つ（候補なしは `None`） |
| fold の判定・実験の合否（`FoldVerdict` / `SearchVerdict`） | `domain.search` の合否（第7.3節） | → `SearchOutcome` → `HoldoutGate.issue` の前提（第9.3節の1） | 結末記録の `search` | 実験の1回の実行に1つ |
| 試行の状態（`TrialStatus`） | 記録からの導出（第10.4節） | 集約表とレポートの作成 | 集約表 `trial_units.status`、結末記録の `search.trial_counts` | 単位ごとに1つ |
| `permit_id` | `HoldoutGate.issue`（第9.3節） | `HoldoutPermit` → `HoldoutAccessLog.append_consumption` → 最終検証の記録 | 閲覧記録 `access_log.jsonl` の行（第9.4節）、`final/permit.json`、`final/final_evaluation.json` | 閲覧記録の行の鍵は `(partition, permit_id)`（第9.4節） |
| 最終検証の partition（`PartitionId`） | 合成が `final_holdout.interval` と系列から決める（第9.3節の2） | `issue` の入力 → `HoldoutPermit.partitions` → `consume` の戻り値 → as-of ビューの許可集合（D03 §6.1） | 閲覧記録の行、最終検証の記録 | 同上 |
| 最終検証の snapshot（`snapshot_id`） | 実験の記録票の `snapshot_id`（D07 §19.2） | `HoldoutPermit.snapshot_id` → `HoldoutAccessLog.append_consumption`（どの snapshot の閲覧記録に追記するか） | 閲覧記録のファイルの場所 `data/snapshots/<snapshot_id>/access_log.jsonl`、`final/permit.json`、`final/final_evaluation.json` | 最終検証1回に1つ |
| 最終検証の目的（`purpose`） | 実験設定（人間。`split.final_holdout.purpose`） | 記録票 → `HoldoutPermit.purpose` | 記録票、閲覧記録の行、最終検証の記録 | 記録票に1つ |
| 最終検証の run と評価の識別子・結果 | バックテスト・評価（第9.6節の6） | `RunExperiment` → `FinalEvaluationRecord` | `final/final_evaluation.json`、`runs/<run_id>/` | 実験の版に1つ（第11.3節） |

## 13. 段階5 の完了条件の確かめ方【提案】

段階5 の完了条件（全体計画書 §8.2）を、段階4 と同じく受入テストで示す（D08 §2.2 の判断の5）。受入テストは `tests/acceptance/test_stage5_completion.py` に置き、人工データで `experiment run` / `experiment report` / `experiment finalize` を `subprocess` で起こす（D08 §2.3 の規約1 を段階5 にも当てる。第16節の改訂依頼）。

| 完了条件 | 受入テストが確かめること | あわせて置く意味論テスト（契約行） |
|---|---|---|
| 選定が train 内で閉じる | (a) 2軸の格子（例: 2×2 の4試行）× 2 fold の実験を通し、各 fold の選定記録の `inputs` が**その fold の選定区間の評価の識別子だけ**を指す。(b) **検証区間の人工データだけを変えた** snapshot で同じ実験を別の版として通し、各 fold の選んだ試行と選定記録の `inputs` の候補の区分が変わらない | 選定関数は検証区間の結果を受け取れない（型）。同点は `trial_index` の小さい方。値なしの試行は候補から外れる。選定記録は検証区間の単位の開始記録より前に書かれる |
| 全試行を記録する | (a) 割当の1つがコンパイル拒否になる探索で、その試行が記録票と集約表に `FAILED` として残る。(b) 全単位が集約表にあり、状態の件数が結末記録と一致する。(c) 開始記録だけがある版のディレクトリ（途中で止まった状態を人工に作る）に `experiment report` を当て、中断・未試行・試行済みが区別して表示される | 試行の状態の導出（第10.4節の4区分）。候補なしの fold の後も次の fold が実行される。事後検査が合格でない単位が候補から外れ、実験が `FAILED_POST_RUN_CHECK` になる |
| holdout を通常探索で読めない | (a) 探索の全単位の記録票の `allowed_partitions` と P2 が研究履歴だけを示す。(b) fold の区間が研究履歴の境界を超える設定を読込時に拒否する。(c) 封印 partition（Q2）と閲覧記録の bare リポジトリを持つ人工データで、合格した実験の `experiment finalize` が1回目は消費して最終検証の記録を書き、2回目は `NOT_SEALED` で拒否する（終了コード 7） | gate の各拒否（第9.3節）でデータを公開しない。push が拒否されたら公開しない。`experiment run` は gate を呼ばない |

- 意味論テストは D08 §7 の対応表に「D09 の意味論の行」として足す（第16節の改訂依頼。行の追加は D08 の改訂で行う）。
- 実データで探索を回すかどうか（段階4 の D07 §23 に当たるもの）は本書では決めない。段階5 の完了条件は人工データで示せる。実データの探索は、研究の実験として人間が実験設定を書いて行う（第15節）。

## 14. 実装 PR の分け方【提案】

段階4 と同じく、下の層から1層ずつ積む。各 PR はその PR の設計節だけを実装する。

| PR | 作るもの | 実装する設計節 | 依存 |
|---|---|---|---|
| 1 探索と分割の型・書式 | `domain.search` / `domain.splits` の型と列挙・検査、書式 v2 の語彙（`search_plan` / `split` / `selection` / `acceptance`）の読込と拒否、記録票の項目の追加 | 第5節、第6.1節、第7.1〜7.4節の型と純粋関数、第10.2節 | 本書の承認 |
| 2 探索の実行と記録 | `RunExperiment` の探索の経路、試行記録・選定記録・集約表、試行の状態の導出、研究ポリシーの検査の適用、レポートの探索の節、Q7・Q8 の結果 | 第6.2〜6.6節、第7.5節、第8節、第10.3〜10.9節、第11節 | 1 |
| 3 封印期間の許可判定 | `holdout_gate`、`HoldoutAccessLog` の実装（Q11）、閲覧記録の行の項目、`experiment finalize`、人工データの封印 partition（Q2） | 第9節 | 2、D03・D01 の改訂（第16節） |
| 4 段階5 の受入 | 段階5 の受入テスト | 第13節 | 3 |

## 15. 対象外

- **fold をまたいだ建玉・状態の持ち越し（将来の WF 持ち越し）と、途中からの実行再開**。別設計【合意済み】全体計画書 §7.5。
- **並列実行**（D10）。
- **適応的な探索・無作為抽出**（Q6 で選べば本書の後続版）。
- **探索の実験の別プロセスでの再現**。D07 §21 の再現は1つの run の実験だけを対象とし、`experiment reproduce` は探索の実験を拒否する（第11.4節）。段階5 の完了条件に再現は含まれない。必要になった時点で本書の後続版が決める。
- **実データでの探索の実行と、その記録文書**。人間が研究として行う。本書は仕組みだけを作る。
- **隔離期間（2026年分）の再分類と、将来取得するデータの新しい封印期間**。別 ADR【合意済み】ADR-0014。
- **学習する部品（fit と推論の分離）**。上位設計書 §5.1 の将来の話であり、段階5 の探索はパラメータの値を変えるだけである。

## 16. 既存文書への改訂依頼

**本 PR では改訂しない**（全体計画書 §8.1 の D09 の行を本書へのリンクにする1行だけ追随した）。本書の承認後、各実装 PR か文書 PR で改訂する。Q の決定で要らなくなる依頼には、どの Q に依るかを書いた。

| # | 宛先 | 依頼 | 理由 |
|---|---|---|---|
| 1 | D07 §18.2・§18.6 の3 | `search_plan` / `split` の `NONE` 以外を受け付け、語彙の正本を D09 §5・§6 とする。`selection` / `acceptance` を書式 v2 のキーに足す。探索の実験ではトップレベルの `run_interval` を拒否する | 第5.5節 |
| 2 | D07 §19.2 | 記録票に探索の項目（第10.2節）を足し、識別の入力の4 に含める。単数の `compiled_ref` / `expected_config_digest` を `None` 可にする | 第10.2節 |
| 3 | D07 §19.3・§3 | 結末記録に `search: SearchOutcome \| None` を足し、単数の run・評価の項目を探索の実験では `None` とする | 第10.6節 |
| 4 | D07 §20.3 | P2 の適用範囲を「`experiment run` の経路」とし、最終検証（`experiment finalize`）では gate の許可を代わりの検査とする。P4・P5 を探索では単位ごとに当てる。Q7 を推奨で決めたら試行数の検査（P7）を足し `PolicyCheck` に値を足す | 第9.6節・第10.8節・Q7 |
| 5 | D07 §20.2 | 研究ポリシーの版の登録簿（Q8 を推奨で決めた場合） | Q8 |
| 6 | D07 §22.1・§21.3 | レポートの探索の節、`experiment finalize` と終了コード 7、`experiment reproduce` が探索の実験を拒否すること | 第11.4節 |
| 7 | D03 §1.2 の行5・§3.8.1 の前文・§7.4 の消費記録の行 | `holdout_gate` の担当を「D07・段階4」から「D09・段階5」へ（§3.8・§6.1 は v1.12 で直したが、この3か所が残っている）。§7.4 の行の鍵を第9.4節の `(partition, permit_id)` にする | 第9.4節 |
| 8 | D03 §3.8（閲覧記録の行の型） | 行に `permit_id`・実験の識別（`experiment_id` と名前・版）を足す。gate（`evaluation`）が行の型を使えるよう、行の型を `marketdata.domain` に置く（`evaluation` は `marketdata.application` を参照できない。D01 §3.2 の契約 F7） | 第9.4節 |
| 9 | D01 §4 | `HoldoutAccessLog` の実装者を Q11 の決定に合わせる（推奨: `marketdata` の閲覧記録の追記・導出と、git の取得・コミット・push を行う adapters を `app` が適合させる） | Q11 |
| 10 | D08 §2.3・§7・§9.5 | 段階5 の受入テストの規約（第13節）、D09 の意味論の行、人工データの封印 partition の例外（Q2 を推奨で決めた場合） | 第13節・Q2 |
| 11 | D06 §9.3 | 使用済み期間の opt-in の記録（Q10 で作ると決めた場合だけ） | Q10 |

## 17. 要決定（Q1〜Q12）

各項目の**選択肢1 が推奨**である。本文は推奨で書いてあり、決定が推奨と違えば本文と第16節を直す。

| # | 何を決めるか | 選択肢（推奨を先頭） | 結果への影響 | 本文の節 |
|---|---|---|---|---|
| **Q1** | **段階5 の最終検証に使う期間**（未観測の封印期間がいまの snapshot に1件も無い） | **1. 封印期間（`SEALED`）だけで行う。無い間は実データの最終検証をせず、gate は人工データで確かめる** / 2. 研究履歴の末尾（例: 2023年）を実験ごとの最終検証区間として取り分け、探索の区間が届かないことを検査する / 3. 使用済みの 2024〜2025 年を opt-in で「参考の検証」に使う（holdout 成績とは呼ばない） | 1 は ADR-0014 の規則だけで閉じ、新しい概念を足さないが、実データの最終検証は将来の封印期間まで行えない。2 は実データで最終検証ができるが、同じ区間を別の実験や人の手の run が読むことを構造では防げず、閲覧履歴の仕組みを1つ足すことになる。3 は Q10 の opt-in の実装が要り、結果は holdout 成績として扱えない | 第9.2節 |
| **Q2** | **封印期間のテストに人工データの封印 partition を作ってよいか**（人工データは常に研究履歴。D08 §9.5） | **1. 封印期間のテストに限り例外を足す（D08 §9.5 の改訂）** / 2. 例外を足さず、gate は閲覧記録のポートの偽物を使う単体テストだけで確かめる | 1 は gate から as-of ビューまでを受入テストで通せる。D08 の決定（Q3、2026-09-23）に例外が1つ増える。2 は D08 を変えないが、段階5 の完了条件3（holdout を読めない）を封印 partition 込みで通す受入テストが書けない | 第9.8節・第13節 |
| **Q3** | **選定指標・合否閾値・集約方法を誰が決めるか** | **1. 実験ごとに実験設定に書き、記録票で固定する。本書は語彙と意味だけ** / 2. 研究ポリシーの新しい版に全実験共通の値を置く / 3. 研究ポリシーが既定値と許容範囲を持ち、実験はその範囲で選ぶ | 1 は戦略ごとに適した指標を選べ、事前固定は記録票と P3 で守られる。実験ごとに基準が違いうる。2 は全実験が同じ物差しで比べられるが、最初の値を今決める必要があり、戦略の性質に合わない指標を強いうる。3 は両者の中間で、範囲という規則がもう1つ増える | 第7.1節・第7.6節 |
| **Q4** | **検証区間で走らせる試行** | **1. 選んだ1試行だけ** / 2. 全試行（選定には使わず記録だけ） / 3. 選定区間の上位 k 試行 | 1 は検証区間の結果が選定に逆流する経路が無い。検証区間での他の試行の成績は分からない。2 は過学習の度合いを後から分析できるが、検証区間の結果を見て次の実験の試行を選ぶ経路が開く。run の数も試行数倍になる。3 は中間で、k という値をもう1つ事前に決める | 第7.7節 |
| **Q5** | **ウォームアップと採点区間の分け方** | **1. run 区間＝採点区間。ウォームアップは run 開始前の足を履歴として読むだけで賄い、run 内の状態は空から始める** / 2. run 区間を採点区間より前へ延ばし、延ばした部分では新規発注をしない規則を D06 に足す / 3. 1本の run を評価側で採点区間に切り出して指標を計算する（D07 の改訂） | 1 は D06・D07 を変えずに済む。指標部品は状態を持たないので値は変わらないが、取引機会など run 内の状態は採点区間の始まりで空になる。2 は run 内の状態も温まるが、エンジンの意味論（D06）の改訂が要る。3 は1本の run で済むが、区間ごとの指標を D07 に足すことになり、選定区間の run が検証区間の情報を読みうる（purge が必要になる） | 第6.5節 |
| **Q6** | **探索アルゴリズムの範囲** | **1. 格子（全組合せ）だけ** / 2. 格子と、seed 付きの無作為抽出 / 3. 2 に加え、途中の結果を見て次を決める適応的な探索 | 1 は試す集合が事前に確定し、未試行の区別が列挙だけで決まる。軸が増えると試行数が掛け算で増える。2 は大きい探索空間を一定の試行数で覆える（seed を記録票に固定）。3 は試す集合が結果に依存し、事前固定と未試行の区別の規則を別に作る必要がある | 第5.1節・第5.3節 |
| **Q7** | **探索の前の複雑性の上限の見直し**（D07 §14） | **1. 上限4件は据え置き、研究ポリシーの版 2 で1実験の試行数の上限を足す（初期値 100）** / 2. 上限4件は据え置き、試行数は記録とレポートに出すだけ / 3. 上限4件も見直す（値は別に決める） | 1 は探索の自由度（試行の数。多重比較の度合い）を事前に抑えられる。100 を超える探索は版を上げて上限を変える手間がかかる。2 は制約が増えないが、試行数を増やして良い結果を拾う経路を抑えない。3 は探索で計測値が変わらないので、見直す根拠が今は無い | 第10.9節 |
| **Q8** | **研究ポリシーの版の登録簿**（D07 §20.2） | **1. 版ごとのダイジェストを git 管理のファイルに登録し、同じ版参照で中身が違うポリシーを読込時に拒否する** / 2. 登録簿を置かず、記録票のダイジェストで後から見分ける運用のまま | 1 は「版を上げずに上限を変えた」ことを機械で止められる。版を足すたびに登録簿の1行が要る。2 は手間が無いが、探索で実験の数が増えると、同じ版で基準が違う実験が並ぶ危険が増える | 第10.9節 |
| **Q9** | **頑健性の範囲** | **1. fold 間のばらつきと、選んだ試行の格子の近傍を記録するだけ。合否に使わない** / 2. 1 に加え、選んだ試行を遅延シナリオ4ケースで検証区間に走らせて比べる（記録だけ） / 3. 1 に加え、D07 に指標集合 v3（分布指標・下方リスク指標）を足して合否に使えるようにする | 1 は新しい指標も run も足さない。頑健でない試行を自動では落とせない。2 は D07 が段階5 へ送った遅延シナリオ別の比較を満たすが、run が fold ごとに4本増える。3 は指標集合の改訂（D07）と golden の更新が要る | 第8節 |
| **Q10** | **使用済み期間（2024〜2025、`CONSUMED`）を研究用途で読む opt-in を段階5 で作るか** | **1. 作らない（研究履歴だけで段階5 を閉じる）** / 2. 作る: 書式 v2 に目的付きの opt-in を足し、run manifest に使用の事実と目的を記録し、結果を holdout 成績として集計しない（D06 §9.3・D07 §20.3 の改訂） | 1 は段階5 の範囲が小さい。2024〜2025 年のデータは研究にも使えない。2 はその2年を研究に使えるが、D06・D07 の改訂と閲覧記録の `RESEARCH_OPT_IN` の行の設計が要り、段階5 の実装が PR 1本分ほど増える | 第9.7節 |
| **Q11** | **閲覧記録のポート（`HoldoutAccessLog`）を誰が実装するか**（D01 §4 は `evaluation.adapters.fs_store`、D03 §3.8 は追記と導出を `marketdata` の担当とする） | **1. `marketdata` の閲覧記録の追記・導出と、git の取得・コミット・push を行う adapters を `app` が適合させる（D01 §4 の実装者の欄を改訂）** / 2. D01 §4 のまま `evaluation.adapters.fs_store` が実装し、行の型と導出を `marketdata.domain` へ移して使う（D03 の改訂） | 1 は閲覧記録の規則が `marketdata` 1か所に残り、D03 §1.2 の担当と揃う。D01 の表を1行直す。2 は D01 を変えないが、`evaluation` の adapters が snapshot のディレクトリと git を触ることになり、市場データの置き場の知識が2つのパッケージに割れる | 第3節・第16節 |
| **Q12** | **最終検証に進める試行**（walk-forward で fold ごとに選ぶ試行が違いうる） | **1. 最後の fold で選んだ試行** / 2. 全 fold の選定区間を合わせた区間で選び直した試行（選定区間の run を1本足す） / 3. 最も多くの fold で選ばれた試行（同数は最後の fold） | 1 は追加の run が無く、最新の区間で選んだものを使う。前の fold の選定は検証の材料に留まる。2 は最も長い区間で選べるが、その選定には検証区間が無い。3 は安定して選ばれた試行を使えるが、最新の区間で選ばれた試行と違いうる | 第7.7節 |

## 18. 本書の版と承認の単位（改訂履歴）

| 版 | 範囲 | 状態 |
|---|---|---|
| v0.1 | 段階5 の設計（第1〜17節）。要決定 Q1〜Q12 | ドラフト（承認待ち） |
