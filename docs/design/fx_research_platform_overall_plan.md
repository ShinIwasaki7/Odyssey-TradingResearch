# FX 研究基盤：全体構築計画書（アーキテクチャ・ディレクトリ・各基盤設計・進め方）

作成日: 2026-09-18
状態: **ドラフト。第6節の決定（2026-09-18、ADR-0001〜0017／2026-09-20、ADR-0018〜0033）を反映済み。残る要決定は第6節・第10節を参照**。本書は上位文書 [fx_research_platform_greenfield_design.md](fx_research_platform_greenfield_design.md) を出発点として、全体アーキテクチャ・ディレクトリ構成・各基盤の設計方針・構築計画を提案するもの。本書の承認をもって段階0（契約設計）を開始する。実装は本書および後続の各設計文書の承認後に行う。

## 0. 本書の読み方と凡例

上位文書で「確定」「合意済み」とされた事項は本書で再議論しない。本書が新たに提案する事項は、すべて次のいずれかの印を付ける。

| 印 | 意味 | 扱い |
|---|---|---|
| 【合意済み】 | 上位文書で確定・合意済みの事項の引用または要約 | 再議論しない。矛盾があれば上位文書を正本とする |
| 【提案】 | 本書が新たに提案する設計・構成。推奨案を示す | 人間の承認で確定。承認前に実装しない |
| 【要決定】 | 本書では決めない、または選択肢提示に留める事項 | 第6節に一覧。決定は ADR（第8.1節）に記録する |

本書は「何をどの順で決め、どこに置き、どう作るか」を扱う。個々の型のフィールドや計算規則は上位文書の確定事項を継承し、未確定分は後続の設計文書（第8.1節）で扱う。

## 1. 目的と対象範囲

### 1.1 目的

FX 戦略研究のための基盤を新規構築する。基盤は次の3つの責務に分解する。

1. **戦略コード（戦略基盤）**: 戦略を型付き部品の宣言と接続で表し、コンパイル・検証し、判断時点で利用可能な情報だけから出力を生成する。
2. **バックテスト基盤**: コンパイル済み戦略・データ snapshot・口座/リスク/執行/費用モデルから、因果順序を守った注文・約定・建玉・口座・判断記録を生成する。
3. **評価基盤**: 実験定義（仮説・探索計画・分割・評価規則）から単一/複数のバックテストを組み立て、指標・診断・比較・選定結果を生成し、再現に必要な manifest を固定する。

これに加えて、3基盤が共通に依存する **共通カーネル**（時刻・ID・金額・銘柄・時間足・理由コード）と、**市場データ・時刻基盤**（snapshot、カレンダー、足生成、as-of 参照、公開スケジュール）を独立した土台として置く（第6節 A-1、決定済み）。上位文書第8節の依存方向案（`data adapter → snapshot / 時点参照 interface`、`共通の時刻・銘柄・数量・IDの型`）を明示的なパッケージに昇格させたものである。

### 1.2 本書の対象範囲

- 全体アーキテクチャ（レイヤ、依存規則、境界を跨ぐデータ）
- ディレクトリ・パッケージ構成
- 各基盤の責務・主要モジュール・主要インターフェース（方針レベル）
- 要決定事項の一覧と推奨
- 上位文書に散在する「詳細設計対象」の基盤別バックログ
- 構築計画（段階、設計文書、完了条件、進め方）

### 1.3 対象外

- 各型の全フィールド確定、計算規則、設定ファイルの具体例（後続設計文書）
- 実装コード
- ライブ発注、本番移行、既存戦略の自動移植（上位文書と同じく初期対象外）

## 2. 現状確認と上位文書との差異

計画立案にあたりリポジトリの現状を確認した。上位文書の前提と異なる点、計画に影響する点を挙げる。

| 項目 | 確認した事実 | 計画への含意 |
|---|---|---|
| リポジトリ | 初期コミットのみ。`README.md`、`CLAUDE.md`、上位設計書、`.claude/skills/pr-review` | ソースコード・設定・テストは未着手。構成を自由に決められる |
| 市場データ | `data/market/` に10ペア×(15m, 1h) の結合済み CSV、合計約230MB。列は `(index), open, high, low, close, volume, source`。`source` は `histdata` / `dukascopy` | 上位文書第3節の前提と整合。受入れ時に列名・時刻書式・価格基準を固定する |
| データ期間 | 2016-01-03 開始。末尾は AUDUSD・EURJPY・EURUSD・USDJPY が **2026-04-10**、他6ペアが **2026-05-01** | 上位文書の「概ね2016〜2025年」「2024〜2025年を封印」より新しい期間を含む。期間はアクセス状態で三分類する（第6節 C-2、決定済み） |
| git 管理 | `data/` は未追跡。`.gitignore` は存在するが未追跡で、内容は `data/*` の一括除外のみ | このままでは snapshot manifest も追跡できない。段階−1で manifest を再包含する規則に書き換える（第6節 C-1、決定済み） |
| 実行環境 | シェルの `python3` は pyenv の 3.8.8 または Homebrew の 3.9.5。`uv` はインストール済みで、uv 管理の Python 3.12.13 が利用可能。`poetry` なし | Python は 3.12 系に固定し uv で管理する（第6節 B-1・B-2、決定済み） |
| pr-review スキル | 別リポジトリ（claude-trading-system）向けの記述。参照するレビュー方針文書と `tools/ops/codex_review_poll.py` は本リポジトリに存在せず、Gate 分類も旧プロジェクト固有 | スキルを書き換え、本リポジトリ用の最小 poller を新規作成する（第6節 D-3、決定済み） |
| 上位文書の記法 | 「確定」「合意済み」「案」「確認中」「要決定」が混在 | 本書は第0節の3区分に正規化し、上位文書の「案」「確認中」は第7節のバックログに載せる |

## 3. 全体アーキテクチャ

### 3.1 責務分解と境界（A-1〜A-3 決定済み）

5つの境界付きコンテキスト（以下「パッケージ」）に分ける。3基盤は上位文書の責務分解そのもの、残り2つはそれらが共有する土台である。

| パッケージ | 責務 | 所有する主要概念 | 所有しないもの |
|---|---|---|---|
| `common`（共通カーネル） | 全パッケージが共有する値型・参照型・理由コード | tz-aware UTC 時刻、半開区間、`ProcessingPoint`、用途別 ID 型、`Price`/`Quantity`/`Money`、`Symbol`/銘柄仕様、時間足定義参照、`ReasonCode`、`PolicyRef`/`EvidenceRef` | 業務ロジック、I/O |
| `marketdata`（市場データ・時刻基盤） | 原データの受入れ・snapshot 固定・足生成・カレンダー・公開スケジュール・as-of 参照・完全性検査・遅延シナリオ | `Bar`（`bar_start`/`bar_end`/`available_at`）、系列 ID、`SnapshotManifest`、`TradingCalendar`、`SeriesSchedule`、`DelayScenario`、集約規則 | 戦略判断、約定処理 |
| `strategy`（戦略基盤） | 戦略の宣言モデル、部品カタログ、コンパイラ/検証、部品評価ランタイム | `ComponentContract`/`ComponentInstance`/`StrategyDefinition` と補助型、`OutputRecord`/`Observation`/役割別 payload、依存グラフ、評価要求・待機記録・追い越し、取引機会のライフサイクル | 数量決定、約定、口座 |
| `backtest`（バックテスト基盤） | イベントループと因果順序、注文受付・リスク審査・予約、執行モデル、口座台帳、判断/状態記録 | `OrderRequest`/`AcceptedOrder`/`FillRecord`/`RiskReservation`、注文状態機械、`RiskPolicy`/`ExecutionPolicy`/`CostModel`、建玉・口座・MTM、trace、`BacktestResult` | 戦略の意味（部品の計算）、WF 窓生成、採否判定 |
| `evaluation`（評価基盤） | 実験定義と manifest、単一実行の指標・診断、研究ポリシー検査、複数実行の探索・分割・選定、holdout 隔離、結果保存 | `ExperimentSpec`、`ResearchPolicy`、`SearchPlan`、分割定義、指標定義、実行状態、結果リポジトリ | バックテスト結果の改変、戦略部品の再実装 |

これらの上に、実行可能な入口として `app`（構成ルート、CLI、設定ファイル読込）を置く。`app` は全パッケージを知る唯一の場所であり、他のパッケージから参照されない。

**境界の判断基準**: 「評価方式を変えても戦略部品や約定エンジンを書き換えない」「実行エンジンを高速化しても因果の契約を変えない」【合意済み】を満たすよう、変更理由が異なるものを別パッケージにする。

**戦略ランタイムの所属（第6節 A-3、決定済み）**: 部品の評価・待機・追い越し・取引機会のライフサイクルを扱う「戦略ランタイム」は `strategy` に置く。これらは戦略の意味論（上位文書第4.3.11〜4.3.14節）であり、将来ライブ実行や別エンジンでも同じ意味で動く必要がある。`backtest` は時刻の進行・フェーズ順序・注文処理を所有し、公開イベントと現在状態を `strategy` へ渡して注文意図等を受け取る。

### 3.2 クリーンアーキテクチャの適用（A-2 決定済み）

各パッケージの内部を、内側から次の層で構成する。依存は常に内側へ向け、外側の層の名前・型・ライブラリを内側で参照しない。

| 層 | 置くもの | 許可する依存 | 禁止 |
|---|---|---|---|
| **domain** | エンティティ、値オブジェクト、状態機械、ドメイン規則（予約計算式、SL 単調性、丸め、理由コードの組合せ検証） | Python 標準ライブラリ、`common` | I/O、外部ライブラリ、実時計、乱数 |
| **application** | ユースケース（`CompileStrategy`、`AcceptMarketData`、`RunBacktest`、`EvaluateRun`、`RunExperiment`）、**ポート**（抽象インターフェース）、ドメイン横断のサービス | domain、`common`、他パッケージの domain/application の公開型 | 他パッケージの adapters、外部ライブラリ |
| **adapters** | ポートの実装（CSV 読込、Parquet 保存、設定ファイル解析、レポート出力） | application のポート、外部ライブラリ | 他パッケージの adapters を直接呼ぶこと |
| **app（構成ルート）** | ポートと adapters の結線、CLI、設定読込の入口 | すべて | 業務ロジックを持つこと |

適用上の規則:

1. **境界を跨ぐのは不変の型だけ**。DataFrame 等の外部ライブラリ型を domain/application の引数・戻り値にしない。`marketdata` の as-of ビューは `Bar` の列など自前の型で返す。
2. **ポートは利用側が定義する**。例えば「戦略ランタイムが読む市場データ」のポートは `strategy.application` が定義し、`marketdata` がそれを満たす実装を提供する（依存性逆転）。パッケージ間で共有すべき型は `common` か提供側の domain に置く。
3. **決定論**: domain/application は実時計・乱数・環境変数を直接読まない。実行時刻は `ProcessingPoint` として渡し、乱数は seed 付き生成器を注入する。manifest の作成日時など記録目的の実時刻は app で取得して渡す。
4. **宣言は不変**: `StrategyDefinition` 等の宣言データは検証後に凍結する【合意済み】。実行中の状態は実行コンテキストに置く。
5. **構造エラーと欠損の区別**: 型・参照・能力の違反は例外として実行を止め、入力欠損は `MissingInputPolicy` で扱う【合意済み】。例外を欠損に読み替えない。
6. **部品実装は許可された view だけを読む**【合意済み】。部品実装が `marketdata` の adapters や snapshot 全体へ直接アクセスする構成は、コンパイラの能力検査で拒否する。

### 3.3 依存方向

```text
                 app（構成ルート・CLI・設定読込）
                   │
                   ▼
              evaluation ──────────────┐
                   │                   │
                   ▼                   │
               backtest ───────┐       │
                   │           │       │
                   ▼           ▼       ▼
               strategy    marketdata  （evaluation は snapshot 参照と
                   │           │        実験 manifest のために marketdata の
                   │           │        domain 型を参照できる）
                   ▼           ▼
                        common
```

パッケージ間で許可する参照【提案】:

| 参照元 → 参照先 | 許可 | 内容 |
|---|---|---|
| `marketdata` → `common` | 可 | 時刻・銘柄・時間足・ID |
| `strategy` → `marketdata`(domain) | 可 | 系列 ID・時間足定義・`Bar` 型を宣言の検証と入力 view に使う |
| `backtest` → `strategy`, `marketdata` | 可 | コンパイル済み戦略と戦略ランタイムの API、公開フィードと as-of ビューのポート |
| `evaluation` → `backtest`, `strategy`, `marketdata` | 可 | `BacktestResult`、`RunBacktest` のポート、戦略/snapshot の参照型 |
| 逆方向 | 不可 | `common` は誰も知らない。`strategy` は `backtest` を知らない、等 |
| いずれか → 他パッケージの `adapters` | 不可 | 結線は `app` だけが行う |

依存規則は文書だけに頼らず、`import-linter` で CI 上の機械検査にする（第6節 A-4、決定済み）。

### 3.4 横断的関心事の配置

| 関心事 | 配置 | 根拠 |
|---|---|---|
| 時刻・因果順序 | `common.time`（`ProcessingPoint(time, phase, sequence)`、半開区間）。フェーズの列挙は `backtest.engine` が定義し、`common` は「時刻＋段階＋連番」の構造だけを持つ | 上位文書第4.7.15節 |
| ID | `common.ids`。用途別の不透明型。`RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`（ADR-0006、2026-09-20 改訂）、run 内の各 ID は `RunId`＋種別＋決定論的連番。UUID4 は使わない（第6節 A-6、決定済み） | 再現性・照合 |
| 理由コード | `common.reason`。`ReasonCode` 列挙と型付き詳細。使用箇所ごとの許可組合せは各 domain が検証 | 上位文書第4.7.14節 |
| 根拠参照・ポリシー参照 | `common.refs`（`EvidenceRef`、`PolicyRef`、`ContractRef`、`ImplementationRef`） | 上位文書第4.7.15節 |
| 記録（trace） | 記録の**型**は各 domain、記録の**書き出し先**は application のポート、実装は adapters | 記録が業務ロジックに依存し、逆はない |
| manifest | 実行 manifest（run 単位）は `backtest`、実験 manifest は `evaluation`、snapshot manifest は `marketdata`。相互参照は ID と digest で行う | 責務ごとに所有者を分ける |
| 設定ファイル | 解析は `app.config`（adapters 相当）。解析結果は各パッケージの宣言型。スキーマ版は設定ファイル側に持つ | 宣言型を設定形式から独立させる |

### 3.5 主要データフロー

```text
[設定ファイル] ─app.config─▶ StrategyDefinition ─strategy.compiler─▶ CompiledStrategy
                                                                        │
[原 CSV] ─marketdata.adapters─▶ AcceptMarketData ─▶ Snapshot + Manifest   │
                                                      │                  │
[実験定義] ─evaluation─▶ ExperimentSpec ─▶ RunPlan（run ごとの固定設定） ─┤
                                                      │                  │
                                              backtest.RunBacktest ◀─────┘
                                                      │
                             公開フィード（available_at 順）→ フェーズ処理 → 戦略ランタイム
                             → 要求組立 → 審査/予約/受付 → 執行 → 台帳更新 → 記録
                                                      │
                                                      ▼
                                              BacktestResult + trace + run manifest
                                                      │
                                     evaluation.EvaluateRun（指標・診断・状態）
                                                      │
                                     evaluation.RunExperiment（複数 run の集約・選定・保存）
```

## 4. ディレクトリ構成（A-2・A-5・C-1 決定済み。ファイル単位の構成は D01 で確定）

### 4.1 トップレベル

```text
Odyssey-TradingResearch/
├── CLAUDE.md
├── README.md
├── pyproject.toml                # 配布名 odyssey-trading-research。requires-python = ">=3.12,<3.13"
├── .python-version               # 3.12.13
├── .gitignore                    # data/raw、data/snapshots の実体、runs/ を除外。snapshot manifest は再包含
├── docs/
│   ├── design/                   # 設計文書（本書、上位文書、後続の各基盤設計）
│   ├── decisions/                # ADR。要決定事項の決定を1件1ファイルで記録
│   └── traces/                   # 段階0の紙上トレース（2本の検証戦略の時刻表）
├── src/
│   └── odyssey_fx/               # import 名（A-5 で決定）
├── configs/
│   ├── strategies/               # StrategyDefinition の設定ファイル
│   ├── experiments/              # ExperimentSpec
│   ├── policies/                 # research / risk / execution / cost の各ポリシー（版付き）
│   ├── symbols/                  # 銘柄仕様（価格刻み・数量刻み・pip 定義・通貨）
│   └── calendars/                # 取引カレンダー・時間足定義・公開スケジュール
├── data/
│   ├── raw/market/               # 移管した20個の原 CSV。上書き禁止。git 管理外
│   └── snapshots/<snapshot_id>/  # 受入れ後の正規化データ。manifest のみ git 管理、実体は管理外。期間のアクセス分類ごとに partition
├── runs/                         # バックテスト・実験の成果物。git 管理外
└── tests/
    ├── unit/                     # 純粋なドメイン・ユースケースの単体テスト
    ├── semantics/                # 意味論テスト（上位文書 §7.2 / §4.7.15E の項目を1件1テストで）
    ├── property/                 # プロパティテスト（先読み不変、SL 単調性、冪等性）
    ├── golden/                   # 人工データの固定 trace との突合
    ├── architecture/             # 依存規則の検査
    └── fixtures/synthetic/       # 人工市場データ生成
```

現在 `data/market/` にある20個の CSV は `data/raw/market/` へ移動し、上書き禁止・git 管理外とする（第6節 C-1、決定済み）。

### 4.2 パッケージ内部

コンテキスト優先（パッケージごとに domain / application / adapters を持つ）を採用する（第6節 A-2、決定済み）。ファイル名は目安であり、後続設計文書で確定する。

```text
src/odyssey_fx/
├── common/                      # 純粋 Python。domain 相当のみ
│   ├── time.py                  # UtcTime, Interval(半開), ProcessingPoint
│   ├── ids.py                   # RunId, EvaluationId, OutputId, OpportunityId, AttemptId,
│   │                            #   OrderId, FillId, PositionId, ReservationId, EventId …
│   ├── money.py                 # Price, Quantity, Money, CurrencyCode
│   ├── symbol.py                # Symbol, SymbolSpec（版付き）
│   ├── timeframe.py             # TimeframeRef（長さ＋整列/セッション規則の参照）
│   ├── reason.py                # ReasonCode と型付き詳細
│   └── refs.py                  # PolicyRef, EvidenceRef, ContractRef, ImplementationRef
│
├── marketdata/
│   ├── domain/                  # Bar, SeriesId, SnapshotManifest, TradingCalendar,
│   │                            #   SeriesSchedule, DelayScenario, AggregationRule, 検査結果型
│   ├── application/             # ports.py（RawBarSource, SnapshotStore）
│   │                            # acceptance.py（受入れ・manifest 生成・完全性検査）
│   │                            # aggregation.py（1h→4h/1d、NY17時基準、DST）
│   │                            # asof.py（MarketDataView 実装：最新確定足・履歴窓・期待足判定）
│   │                            # publication.py（available_at 順の公開フィード、遅延シナリオ適用）
│   └── adapters/                # csv_source.py, parquet_store.py
│
├── strategy/
│   ├── declarations/            # contract.py, instance.py, definition.py,
│   │                            #   specs.py（InputSpec/OutputSpec/ParameterSpec/…）,
│   │                            #   refs.py（OutputRef/MarketDataRef/RuntimeInputRef）,
│   │                            #   read_spec.py（LatestAvailable/HistoryWindow/DeliveredEvent/CurrentContext）,
│   │                            #   missing.py（MissingInputPolicy の型付き union）,
│   │                            #   evaluation.py（EvaluationSpec/EvaluationSchedule）,
│   │                            #   entry_policy.py, state_spec.py, temporal.py, datatypes.py（DataTypeRef 登録）
│   ├── records/                 # OutputRecord, Observation, ConditionState, MarketPermission,
│   │                            #   Opportunity, ConfirmationResult, OrderIntent, ProtectionLevels, ManagementAction
│   ├── catalog/                 # 部品実装（契約＋実装＋状態型を1組で登録）
│   │   ├── features/            # ema, atr, highest_high, …
│   │   ├── conditions/          # compare, and_, or_, transition, n_bars, within_n_bars_after
│   │   ├── permissions/         # condition → MarketPermission 変換
│   │   ├── triggers/            # breakout
│   │   ├── filters/             # ema_confirmation（include_start_bar を持つ）
│   │   ├── orders/              # market_intent
│   │   ├── protection/          # level_stop（価格水準型）
│   │   ├── exits/               # fixed_rr_take_profit, trailing, time_exit
│   │   └── registry.py          # ImplementationRef（ID＋内容ハッシュ）の登録・解決
│   ├── compiler/                # validate.py（型/参照/arity/パラメータ/評価条件）,
│   │                            # graph.py（依存グラフ＋エンジン因果辺、循環検出、評価順）,
│   │                            # capability.py（未対応構成の拒否）, hashing.py, compiled.py
│   └── runtime/                 # ports.py（MarketDataView, RuntimeContextView, OutputSink）
│                                # evaluator.py（起動条件→依存順評価→OutputRecord 付与）
│                                # requests.py（評価要求のライフサイクル）
│                                # waiting.py（WAIT_FOR_INPUT 記録・再開・期限）
│                                # supersession.py（追い越し）
│                                # opportunities.py（機会の保持・確認・失効・重複防止）
│
├── backtest/
│   ├── domain/                  # orders.py（OrderRequest/AcceptedOrder/OrderState/状態機械）,
│   │                            # fills.py（FillRecord/CostEntry）, reservations.py（RiskReservation/
│   │                            #   ReservationState/PositionRiskAllocation/RiskMeasurement）,
│   │                            # positions.py, account.py（balance/equity/台帳）, events.py（event_id・冪等）,
│   │                            # policies.py（RiskPolicy/ExecutionPolicy/CostModel の宣言型）
│   ├── engine/                  # phases.py（フェーズ列挙と順序）, loop.py（公開フィード駆動）,
│   │                            # clock.py（期限フェーズ）, run_end.py（末尾処理）
│   ├── admission/               # request_assembly.py（部品出力→OrderRequest）,
│   │                            # risk_assessment.py（2%/20%・予約額・数量）, admission.py（全順序・原子的受付）
│   ├── execution/               # fill_model.py（次の適格 open・ask/bid・slippage・entry_delay_bars）,
│   │                            # spread.py, protection_hits.py（SL/TP 到達・足内競合解決・gap）,
│   │                            # emergency.py（約定直後の緊急決済）, cost_model.py
│   ├── portfolio/               # ledger.py（原子的更新・投影）, mtm.py, conversion.py（通貨換算）
│   ├── trace/                   # recorder.py（記録ポートへの書き出し）, result.py（BacktestResult DTO）, manifest.py
│   └── application/             # run_backtest.py（ユースケース）, ports.py（TraceSink, ResultWriter）
│
├── evaluation/
│   ├── domain/                  # metrics.py（指標定義）, status.py（完了/失敗/拒否/中断）,
│   │                            # research_policy.py, experiment.py（ExperimentSpec）,
│   │                            # search.py（SearchPlan）, splits.py（train/validation/WF/purge/holdout）
│   ├── application/             # evaluate_run.py, run_experiment.py, manifest.py,
│   │                            # holdout_gate.py（隔離 holdout のアクセス制御・閲覧記録）,
│   │                            # ports.py（BacktestRunner, ResultRepository, ExperimentStore）
│   └── adapters/                # fs_store.py（Parquet/JSON）, report.py
│
└── app/
    ├── composition.py           # ポートと adapters の結線（唯一の構成ルート）
    ├── config/                  # 設定ファイル → 宣言型。スキーマ版の検査
    └── cli/                     # data accept / strategy compile / backtest run / eval run / experiment run
```

### 4.3 依存規則の機械検査（A-4 決定済み）

- `tests/architecture/` で、第3.3節の表を `import-linter` の契約として記述し CI で検査する。
- 外部ライブラリ（DataFrame ライブラリ、Parquet、設定パーサ）の import を `adapters` と `app` に限定する契約も含める。NumPy は `strategy.catalog` の部品実装内部に限り使用可（ADR-0021、D01 §5.1 の条件付き）。F5b がその境界を機械検査する。D04/D05 で決めるのは「どの部品が実際に NumPy を使うか」であり、許可の可否ではない。

### 4.4 設定・データ・成果物の配置規則

- `configs/` は人間が編集する宣言。すべてのファイルにスキーマ版を持たせ、`app.config` が検証する。
- `data/snapshots/<snapshot_id>/manifest.json` と `access_log.jsonl`（ADR-0014）は git 管理し、データ実体（Parquet 等）は管理外。manifest には上位文書第3.3節の項目（digest、出所、銘柄、価格基準、期間、行数、変換コード版）を記録する。
- `runs/<run_id>/` に run manifest・trace・result を保存し、`evaluation` の実験成果物は `runs/experiments/<experiment_id>/` に置く。形式は第6節 B-7。
- 期間は `RESEARCH_HISTORY` / `LEGACY_HOLDOUT` / `QUARANTINED_UNASSIGNED` に三分類し、受入れ処理が生成する snapshot partition の単位で物理分離する。後二者は `holdout_gate` を通らない読込経路を持たない（第6節 C-2、決定済み）。

## 5. 各基盤の設計方針

各基盤について、責務・主要モジュール・境界のインターフェース・上位文書との対応を示す。フィールドや規則の確定は第8.1節の各設計文書で行う。

### 5.1 共通カーネル（`common`）

**責務**: 全パッケージが共有し、業務ロジックを含まない値型・参照型を提供する。

| モジュール | 内容 | 上位文書 |
|---|---|---|
| `time` | tz-aware UTC の時刻型、半開区間 `[start, end)`、`ProcessingPoint(time, phase, sequence)`。実時計を読む関数は置かない | §4.7.15 共通の値型 |
| `ids` | 用途別の不透明 ID 型。run 内で一意、永続参照は `run_id` との組 | 同上 |
| `money` | `Price` / `Quantity` / `Money(amount: Decimal, currency)`。`Quantity` は基軸通貨単位 | 同上 |
| `symbol` | `Symbol`（区切りなし表記）、`SymbolSpec`（価格刻み・数量刻み・最小数量・base/quote・pip 定義。版付き） | §3.1、§4.7.3 |
| `timeframe` | 時間足定義への参照（長さ＋整列/セッション規則）。固定 enum にしない | §4.3.9 |
| `reason` | `ReasonCode` 列挙と型付き詳細。初期語彙は `RISK` / `NO_CANDIDATE` / `RUN_END` / `DATA_ERROR` / `EXPIRED` / `CARRY_NOT_ALLOWED`。`POSITION_CLOSED` の追加は提案中 | §4.7.14 |
| `refs` | `PolicyRef`、`EvidenceRef`、`ContractRef`、`ImplementationRef` | §4.3.5、§4.7.15 |

**設計上の注意**: `common` は外部ライブラリに依存しない。数値精度は B-4 で決定済み。`Price` / `Quantity` / `Money` は Decimal を基礎とし、`Decimal(float)` による直接変換を禁止する。

### 5.2 市場データ・時刻基盤（`marketdata`）

**責務**: 原データを検査して snapshot として固定し、系列ごとの足境界・カレンダー・公開スケジュールを定義し、判断時点で利用可能な情報だけを返す as-of ビューと、`available_at` 順の公開フィードを提供する。

**主要モジュールと責務**

| モジュール | 責務 | 上位文書 |
|---|---|---|
| `domain.bar` | `bar_start` / `bar_end` / `available_at` / OHLCV / 価格基準（bid 等）を持つ `Bar`。未確定足は生成しない | §3.3、§4.3.9 |
| `domain.calendar` | 取引カレンダー・タイムゾーン・DST・日足の区切り（NY 17時）を設定として保持。固定 UTC 時刻を埋め込まない | §4.3.13 |
| `domain.schedule` | 系列ごとの足境界・整列・通常の公開予定。期待される最新確定足の判定に使う | §4.3.10、§4.3.13 |
| `domain.delay` | 遅延シナリオ（系列固定遅延・指定足への注入・seed 付き確率的遅延）。OHLC は変えず `available_at` と配送順だけ変える | §4.3.13 |
| `domain.snapshot` | `SnapshotManifest`（digest・出所・銘柄・価格基準・期間・行数・変換コード版・閲覧履歴） | §3.3 |
| `application.acceptance` | 受入れユースケース。列名・時刻書式・欠損表現の確認、UTC 変換の固定、`source` 列の分離、検査（DST・週末・短セッション・不完全足・重複・欠損・銘柄間ずれ）、manifest 生成 | §3.2、§3.3 |
| `application.aggregation` | 1h → 4h / 1d の生成。NY 現地時刻の区間、始値/高値/安値/終値/volume 規則、不完全足の採否規則。旧集約の再現版と新規則版を別バージョンにする | §3.2 |
| `application.asof` | `MarketDataView` の実装。`latest_available(series, at)`、`history(series, window, at)`、期待足の存在・有効性検査、履歴窓の完全性検査。未来参照は構造的に不可能にする | §4.3.10 |
| `application.publication` | `available_at` 順の公開イベント列。足区間の終了通知とデータ公開通知を区別する。遅延シナリオを適用 | §4.3.13 |
| `adapters.csv_source` | 既存 CSV の読込。列の対応付けは設定で明示し、時刻の見た目から推測しない | §3.2 |
| `adapters.parquet_store` | snapshot の保存・読込 | — |

**提供するポート（利用側が定義し、本パッケージが実装）**

- `MarketDataView`（`strategy.runtime.ports` が定義）: as-of 読み取り。
- `PublicationFeed` / `ExecutionSeries` / `Calendar`（`backtest.application.ports` が定義）: 公開イベント列、執行用系列の open/high/low/close へのアクセス、期限・候補 open の決定に使うカレンダー。

**初版の能力境界**: 15m より細かいデータは存在しない前提。5m や tick を要求する構成はコンパイラの能力検査で拒否する【合意済み】。bid 系列のみのため ask は spread モデルから導く（`backtest.execution.spread`）。

### 5.3 戦略基盤（`strategy`）

**責務**: 戦略を宣言データとして表し、検証・コンパイルし、公開イベントに対して依存順に部品を評価して型付き出力を生成する。数量・約定・口座には関与しない。

#### 5.3.1 宣言モデル（`declarations`）

上位文書第4.3.5節で確定した `ComponentContract` / `ComponentInstance` / `StrategyDefinition` のトップレベルと、第4.3.8〜4.3.10節の補助型を、不変のデータクラスとして実装する。方針は次のとおり。

- 区分値は「小さな enum」と「区分タグ付きの型付き union」を区別する【合意済み】。`InputSourceRef`、`InputReadSpec`、`MissingInputPolicy`、`EvaluationSchedule` の起動条件、`EntryPolicy` のモードは union。
- `DataTypeRef` は登録制。Price・Ratio・ConditionState・Opportunity 等の型識別子とスキーマを登録し、動的なクラス名読込はしない【合意済み】。
- スキーマ版（3クラスの保存形式の版）と、部品・戦略の版を区別する【合意済み】。スキーマ版の保存場所は設計文書 D04 で決める。
- 宣言は Python の型注釈に頼らず、構築・読込・コンパイル時に検証する【合意済み】。検証ライブラリの選択は第6節 B-3。

#### 5.3.2 実行時の出力型（`records`）

上位文書第4.3.15節で確定した `OutputRecord[T]`、`Observation[T]`、`ConditionState`、`MarketPermission`、`Opportunity`、`ConfirmationResult` に加え、`OrderIntent`、`ProtectionLevels`、`ManagementAction` を置く。部品は payload を計算し、エンジン（戦略ランタイム）が `output_id` / `evaluation_id` / `decision_time` / `available_at` / `sequence` を付ける【合意済み】。

#### 5.3.3 部品カタログ（`catalog`）（担当文書は D05。2026-09-20 に D04 承認時の Q9 で確定）

- 各部品は「契約（宣言）＋実装（関数またはクラス）＋状態型」を1組として登録する。契約の `state_spec` は実装の状態型を参照し、二重定義しない【合意済み】。
- 実装は `ImplementationRef`（ID＋内容ハッシュ）で登録し、契約から実行関数そのものを保存しない【合意済み】。
- 実装が受け取るのは、コンパイラが解決した入力 view（最新値、履歴窓、配送イベント、現在コンテキスト）とパラメータだけ。市場データ全体や台帳へのアクセス手段を渡さない。
- 実装の形式は `evaluate(inputs, parameters) -> outputs` または `evaluate(inputs, parameters, state) -> (outputs, new_state)` の2つに統一する。状態はランタイムが保持し、部品実装オブジェクトは可変状態を持たない（第6節 A-8、決定済み）。
- 初版カタログ（検証戦略 A・B に必要な最小集合）【提案】: EMA、ATR、直近 N 本高値（当該足を除く）、価格比較、AND / OR、条件 → `MarketPermission` 変換、高値突破 Trigger、EMA 確認 ExecutionFilter（`include_start_bar` を持つ）、成行 `OrderIntent`、価格水準型 SL、固定 RR の TP ＋ トレーリング Exit、期間 Exit。段階的に第4.6節の合成部品（遷移検出、N 本継続、A 後 N 本以内の B）を加える。

#### 5.3.4 コンパイラ（`compiler`）（検査一覧は D04、実装の詳細は D05。2026-09-20 に D04 承認時の Q9 で確定）

`StrategyDefinition` → `CompiledStrategy`（不変、内容ハッシュ付き）。検査項目:

1. 参照の存在（`OutputRef` の `instance_id` / `output_name`、`ContractRef` の版とハッシュ）。
2. 型・単位・銘柄の整合（`InputSpec.data_type` と接続元の `OutputSpec` / 市場データ項目）、arity、`PortKind` と読み方の組合せ（VALUE に DeliveredEvent 等を拒否）。
3. パラメータの名前・型・範囲・列挙値。パラメータ依存の履歴本数を具体値に解決。
4. 評価スケジュールが契約の `EvaluationSpec` の範囲内であること。
5. 役割フィールドの型要求（`trigger` は `Opportunity`、`order` は `OrderIntent` 等）と、`execution_filter` の有無と `entry_policy` モードの整合。
6. 依存グラフの構築（明示入力＋エンジン上の因果辺）と循環検出、評価順の導出。時間足から順序を推測しない【合意済み】。
7. 能力検査: 未対応構成（未約定注文への `RuntimeInputRef`、距離型 SL、指値、15m より細かい足、再審査設定の未実装分）を明示的に拒否する。

#### 5.3.5 戦略ランタイム（`runtime`）

公開イベントのバッチ（同じ `available_at` のもの）と現在コンテキストを受け取り、上位文書第4.3.11〜4.3.14節の意味論で部品を評価し、`OutputRecord` 列と、注文意図・保護水準の組、管理要求を返す。

| 責務 | 内容 | 上位文書 |
|---|---|---|
| 起動判定 | `OnBarClose` / `OnInputEvent` の起動条件に該当する使用箇所を特定。上流更新だけで下流を自動評価しない | §4.3.2、§4.3.9 |
| 依存順評価 | 起動対象の上流完了後に下流を評価。同時刻の複数起動条件は機会 ID ごとに集約し、同じ論理確認を二重実行しない | §4.3.12 |
| 入力解決 | `InputReadSpec` に従い as-of ビューから読む。期待足の存在・完全性・`max_age` を検査。不足時は `on_missing` | §4.3.10 |
| 評価要求のライフサイクル | 要求 ID、終端状態（評価済み / SKIP / ERROR / 待機 / 追い越し）。SKIP / ERROR で決着した要求は復活させない | §4.3.13、§4.3.14 |
| 待機（WAIT_FOR_INPUT） | 問いの固定（対象区間・受信済み機会の内容）、有限期限、不足入力到着時のみ再開、現在状態は再開時の `decision_time` で読む | §4.3.14 |
| 追い越し（supersession、`REQUEST_SUPERSEDED`） | Trigger は失効、確認は対象足を進めて新要求を発行。開始足 ID と期限は維持。取引機会の `SUPERSEDED` とは対象が異なるため語を分ける（2026-09-20 改訂、ADR-0033） | §4.3.14 |
| 取引機会の管理 | 保持・確認・期限・失効・重複防止。複数の有効な取引機会を同時に保持できること。再発火は常に新しい `opportunity_id` を生成し、内容の上書き（置換）は禁止。確認待ち中の条件再検査は `OpportunityValiditySpec`、複数機会の関係は `OpportunityConcurrencySpec`（2026-09-20 確定、ADR-0031・ADR-0032） | §4.5 |
| 取引機会の終端理由 | `EXPIRED` / `MARKET_STATE_INVALIDATED`（市場状態による無効化。継続要求条件の不成立と、生成時点で取引が許されていなかった場合を含む。復活なし。2026-09-22 改訂） / `SUPERSEDED`（新Trigger優先設定） / `CLOSED_BY_ORDER_ACCEPTANCE`（`on_order_accepted` による、**他の**機会の受付起因の終了） / `CONCURRENCY_LIMIT_REACHED`（同時保持上限に達しており、記録はするが有効にしない。2026-09-20 改訂、ADR-0032 補足3） / `ORDER_ATTEMPT_REJECTED`（自身の発注試行が受付前の審査で拒否された） / `FULFILLED_BY_ORDER_ACCEPTANCE`（自身の注文が受け付けられて役目を終えた）（末尾2件は 2026-09-21 改訂、ADR-0032 補足4） | §4.5 |
| 出力の付番 | `OutputRecord` の共通メタデータを付与し、因果順序を記録 | §4.3.15 |

非終端の状態名と遷移を含む完全な状態機械は、2026-09-21 に D05 §7 で確定した。非終端は `OPEN`（生成され有効）/ `CONFIRMED`（後続確認が成立した中間状態）/ `ORDER_PENDING`（注文要求を渡し受付待ち）の3つ、終端は `TERMINATED` の1つで、区別は上の終端理由が持つ。上位文書 §4.5 の `ACTIVE` / `CONFIRMED` という仮置きは `OPEN` / `CONFIRMED` に置き換えた。

ランタイムは `backtest` のフェーズ P1〜P5（Feature → MarketState → Trigger → ExecutionFilter → 注文意図）の中身を担当し、フェーズの順序・P0 の公開・P5 以降の受付/執行は `backtest.engine` が駆動する。

### 5.4 バックテスト基盤（`backtest`）

**責務**: 上位文書第4.7.12節の因果順序でイベントを処理し、注文・約定・建玉・口座・記録を生成する。戦略の意味と評価の規則には関与しない。

#### 5.4.1 エンジン（`engine`）

1回の境界時刻 T における処理順【合意済み】:

```text
前の執行足の終了
→ その足で有効だった SL/TP による内部約定を解決（protection_hits）
→ 建玉・損益・balance・消費済み枠を更新（portfolio.ledger）
→ 期限フェーズ：expires_at <= T の PENDING を EXPIRED（clock）
→ 確定データを戦略へ公開（P0、marketdata.publication）
→ 戦略ランタイムを依存順で実行（P1〜P5、strategy.runtime）
→ 要求組立・リスク審査・予約・受付（admission）
→ 次の執行足の open 処理（execution.fill_model）
→ 既存建玉の gap 保護決済・適格な成行注文の約定・新規建玉初期化・約定直後の緊急決済
```

- 公開フィードは `available_at` 順。遅延注入で T+2秒に届いた公開は新しい処理点として扱い、T の判断・注文履歴を書き換えない【合意済み】。
- run_end の手順（内部約定解決 → 期限切れ → 通常評価 → RUN_END で受付前拒否 → 残存 PENDING を CANCELED/RUN_END → 残存建玉は MTM で最終 snapshot）【合意済み】を `run_end` に置く。
- 実行前のデータ完全性検査を必須とし、既知の欠損・末尾不足は開始前に失敗させる【合意済み】。
- 内部方式は単一スレッドのイベント駆動参照実装とし、決定論的な優先キューで処理する（第6節 A-7、決定済み）。高速化版は参照実装との同値性検証を条件に後から加える。

#### 5.4.2 注文・受付・リスク（`domain.orders` / `admission`）

- 上位文書第4.7.15節の `OrderRequest` / `AcceptedOrder` / `FillRecord` / `RiskReservation` と、`OrderState` / `ReservationState` / `PositionRiskAllocation` / `RiskMeasurement` の投影。
- 注文状態は PENDING / FILLED / CANCELED / EXPIRED。受付前拒否は `AttemptDecision` に記録し注文を作らない【合意済み】。
- 受付は「因果的に準備できた要求集合」を `(decision_time, strategy_priority, opportunity_id, attempt_id)` で全順序化し、審査・予約・受付を不可分に確定する【合意済み・優先度の向きと ID 比較規則は D06】。
- リスク審査: 受付判断時 balance × 2% を1試行予算、balance × 20% − U を口座残余、予約額 `R(Q) = d × (P_limit − S) × Q × X + C(Q)` で数量を切り下げ決定【合意済み】。
- 冪等性: `event_id` と処理済み記録を状態更新と同じ確定単位に含める【合意済み】。

#### 5.4.3 執行（`execution`）

- 初版は「受付後、因果順序上まだ到来していない最初の執行足の始値」で約定。買いは ask、売りは bid に slippage を適用。bid のみの系列は spread モデルで ask を導く【合意済み】。
- `ExecutionPolicy` に `entry_delay_bars`（0 または 1）、銘柄別の許容不利約定幅 Δ、有効時間を置き、実験で固定する【合意済み・具体値は D06】。
- SL/TP 到達は執行足ごとに判定する。**両方に触れた場合は、設定された解像度階層に従い時系列順の下位足で再帰的に解決する**（2026-09-20 確定、ADR-0030）。下位足は親足を完全に被覆し価格基準・足境界・利用可能時刻が整合するものだけを使う。最小解像度でもなお順序が観測不能な場合だけ `UNRESOLVED` として SL 優先を適用する。解決方法・使用系列・解決した子足・未解決時の裁定を約定記録と run manifest に残す。下位足が必要なのに存在しない場合の実行可否はデータ能力検査で決め、暗黙に親足の4本値へ落とさない。新規約定直後の SL gap は同じ open で SL 決済、約定ずれ超過は緊急決済。二重決済を防ぐ【合意済み】。
- `CostModel`: 手数料・slippage・spread の扱いを固定し、価格反映済み費用を二重計上しない。swap は初版未計上とし結果に明記する（第6節 C-5）。

#### 5.4.4 口座・建玉（`portfolio`）

- balance（実現損益・費用反映、未実現含まず）と equity（MTM）を区別【合意済み】。
- 建玉の SL 管理権は約定時に単一の Exit 部品へ移す。SL 更新は単調性と現在価格に対する妥当性を検査する【合意済み】。
- 通貨換算は判断時点で利用可能な系列を使い、不足時は拒否する【合意済み】。クロス通貨の換算経路は D06 で決める。
- 末尾の3集計（確定損益・MTM 込み資産・参考損益）を分けて出力【合意済み】。

#### 5.4.5 記録と結果（`trace`）

- run manifest（コンパイル済み戦略のハッシュ、snapshot ID、各ポリシーの `PolicyRef`、遅延シナリオ、執行系列、seed、コード版、環境）。
- trace: `OutputRecord`、評価記録（SKIP / ERROR の理由を含む）、待機記録、追い越し、機会ライフサイクル、`AttemptDecision`、注文・約定・予約・割当・計測、イベント、台帳 snapshot。ID 連鎖（機会 → 試行 → 注文 → 約定 → 建玉 → 管理要求、予約）で辿れること【合意済み】。
- `BacktestResult`: 評価基盤へ渡す正規化 DTO（注文・約定・建玉・現金・MTM 推移・終了状態・3集計・実行状態）。評価側はこれを改変しない。

### 5.5 評価基盤（`evaluation`）

**責務**: 実験を定義・固定し、バックテストを組み立て、結果から指標・診断・比較・選定を生成する。バックテストの意味論に立ち入らない。

#### 5.5.1 単一実行の評価（段階2〜4）

| モジュール | 内容 | 上位文書 |
|---|---|---|
| `domain.metrics` | リターン、MTM ドローダウン、リスク調整指標、年率化、取引数、exposure、費用集計の定義。0 取引・計算不能は「値なし」として型で表し、0 や成功値へ置換しない | §5.3 |
| `domain.status` | 完了 / 失敗 / 拒否 / 中断。失敗した候補も履歴に残す | §5.3 |
| `domain.research_policy` | 初版は共通1種類の `ResearchPolicy`（ID＋版）。事前固定・記録・複雑性計測の規則。複雑性の計測方法（合成部品内部を含む条件数等）を定義するが、初期に上限は設けない | §6 |
| `application.evaluate_run` | `BacktestResult` → 指標・診断・状態。診断には遅延シナリオ別の参照価格経過時間・不利約定幅・緊急決済件数を含む | §4.7.12 |
| `application.manifest` | 実験 manifest（仮説、戦略定義ハッシュ、探索計画、分割、評価規則、データ/モデル/ポリシー参照、seed、環境）。別プロセスで再現できること | §5.3、§7 |
| `adapters.fs_store` | Parquet / JSON への保存 | 第6節 B-7 |

#### 5.5.2 複数実行の評価（段階5）

- `SearchPlan`（試す値・組合せ・アルゴリズム・終了条件）、分割（train / validation / WF、purge、fold 境界の建玉・状態の扱い）、選定規則、集約方法。
- `holdout_gate`: 隔離 holdout への読込は通常の探索経路から不可能にし、アクセスと閲覧履歴を記録する。旧基盤での閲覧履歴を引き継ぐ【合意済み】。
- 評価窓の長さ・選定指標・合否閾値・集約方法は、結果を見る前に固定する【合意済み】。段階5の設計文書 D09 で決める。
- 探索で作った異なるパラメータ割当は、解決済み設定のハッシュで別実行として識別する【合意済み】。

### 5.6 アプリケーション層（`app`）

- `composition`: 唯一の構成ルート。ポートと adapters を結線し、ユースケースを組み立てる。
- `config`: 設定ファイル → 宣言型。スキーマ版を検査し、未宣言キー・型不一致を拒否する。
- `cli`: 初版のコマンド案は `data accept`、`strategy compile`、`backtest run`、`eval run`、`experiment run`。CLI ライブラリは第6節 B-8。

## 6. 決定事項と残る要決定事項

2026-09-18 に、第6節の推奨に対してユーザーが決定を示した。決定済みの項目は `docs/decisions/` の ADR を正本とし、本節はその要約と、まだ決まっていない項目の一覧を保持する。「決定」列は ADR の要約であり、詳細・理由は ADR を参照する。

### A. アーキテクチャ・構成（全項目決定済み）

| # | 項目 | 決定 | 補足 | ADR |
|---|---|---|---|---|
| A-1 | パッケージ分割 | `common` / `marketdata` / `strategy` / `backtest` / `evaluation` の5つ。`app` は構成ルート | 第3.1節のとおり | [ADR-0001](../decisions/0001-five-package-split.md) |
| A-2 | パッケージ内レイアウト | コンテキスト優先。各パッケージ内を domain / application / adapters に分ける | 第4.2節のとおり | [ADR-0002](../decisions/0002-context-first-layout.md) |
| A-3 | 戦略ランタイムの所属 | `strategy` に置く。部品評価・WAIT・追い越し・機会管理は `strategy`、時刻進行・フェーズ順・注文処理は `backtest` | 責務の切り方は下記 | [ADR-0003](../decisions/0003-strategy-runtime-ownership.md) |
| A-4 | 依存規則の機械検査 | `import-linter` を CI 必須にする。循環依存・逆依存・adapters 直接参照を検出 | 第4.3節 | [ADR-0004](../decisions/0004-import-linter-in-ci.md) |
| A-5 | レイアウトとパッケージ名 | `src/` レイアウト。配布名 `odyssey-trading-research`、import 名 `odyssey_fx`、ソースは `src/odyssey_fx/` | 下記「パッケージ名」 | [ADR-0005](../decisions/0005-src-layout-and-package-name.md) |
| A-6 | ID 生成規則 | 決定論的 ID。UUID4 は使わない。RunId は `ConfigDigest`＋`CodeDigest`＋`LockDigest`＋`EnvDigest`（2026-09-20 改訂） | 規則は下記 | [ADR-0006](../decisions/0006-deterministic-ids.md) |
| A-7 | エンジン内部方式 | 単一スレッドのイベント駆動参照実装を先に作る。決定論的な優先キューで処理し、非同期メッセージ基盤は使わない | 高速化版は参照実装との同値性検証を条件とする | [ADR-0007](../decisions/0007-event-driven-reference-engine.md) |
| A-8 | 部品実装の形 | 純粋関数＋明示状態。部品クラス内部に可変状態を隠さない | 形式は下記 | [ADR-0008](../decisions/0008-pure-function-components.md) |

**A-3 の責務の切り方**

```text
backtest
  時刻 T を進める
  → 公開イベントと現在状態を strategy へ渡す

strategy
  起動対象を決める
  → 依存順に部品を評価
  → WAIT・追い越し・取引機会を更新
  → 注文意図等を返す

backtest
  リスク審査・受付・約定・台帳更新を行う
```

**A-6 の ID 規則**（2026-09-20 改訂、ADR-0006）

- `ConfigDigest`: 解決済み run 設定（snapshot・戦略・ポリシー・区間・遅延シナリオ・seed）の正規化内容のダイジェスト。設定だけの同一性を表す。
- `RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`。`CodeDigest` は実行したソースコードの内容、`LockDigest` は `uv.lock` の内容、`EnvDigest` はインタプリタ・プラットフォーム・インストール済み配布物（同じ lock でも環境で wheel と数値結果が変わりうるため）。
- git commit・dirty 状態は manifest に記録する（識別子には含めない）。
- 同一の完全入力による再実行は同じ `RunId`。既存成果物は無条件に上書きせず、置換は明示的な指示でのみ行う。
- run 内の各 ID: `RunId` ＋ 種別 ＋ 決定論的連番。内容ハッシュだけをイベント ID にしない。再配送される同一通知は同じ `EventId`。
- `RunAttemptId` は、再実行履歴の保存が必要になった場合のみ追加する（初版では持たない）。

**A-8 の部品実装の形式**

```text
evaluate(inputs, parameters) -> outputs
evaluate(inputs, parameters, state) -> (outputs, new_state)
```

状態の保存場所はランタイムであり、部品実装オブジェクトではない。

### B. 技術選定

| # | 項目 | 決定／要決定 | 補足 | ADR |
|---|---|---|---|---|
| B-1 | Python 版 | **決定**: 3.12 系に固定。`requires-python = ">=3.12,<3.13"`、`.python-version` は 3.12.13 | 3.13 への更新は依存ライブラリと golden trace を検証した別変更にする | [ADR-0009](../decisions/0009-python-312.md) |
| B-2 | パッケージ管理 | **決定**: uv。Python 版・仮想環境・依存 lock を一元管理 | — | [ADR-0010](../decisions/0010-uv.md) |
| B-3 | 宣言型・検証 | **決定**: domain は frozen dataclass、設定境界（`app.config`）だけ Pydantic v2。Pydantic モデルを domain へ流入させない | — | [ADR-0011](../decisions/0011-frozen-dataclass-domain.md) |
| B-4 | 数値精度 | **決定**: 台帳系は Decimal、Feature は float。変換点と丸めを明示 | 境界は下記 | [ADR-0012](../decisions/0012-decimal-float-boundary.md) |
| B-5 | 表形式ライブラリ | **決定**: polars（adapters 限定） | 型が厳密で高速。adapters に閉じるため後から交換可能 | [ADR-0025](../decisions/0025-polars-in-adapters.md) |
| B-6 | 設定形式 | **決定**: 人間が書く宣言は YAML（安全な読込・カスタムタグ禁止・重複キーエラー・merge key 禁止・`schema_version` 必須）、機械生成の manifest は JSON | Pydantic 検証後に frozen dataclass へ変換 | [ADR-0018](../decisions/0018-config-format-yaml-json.md) |
| B-7 | 成果物保存 | **決定**: ファイルシステムに Parquet（表）＋ JSON（manifest） | 集計は後から DuckDB で読める | [ADR-0027](../decisions/0027-fs-parquet-json-artifacts.md) |
| B-8 | CLI | **決定**: argparse | 依存を増やさない | [ADR-0028](../decisions/0028-argparse-cli.md) |
| B-9 | 品質ツール | **決定**: ruff、mypy（strict）、pytest、hypothesis、import-linter | ビルドバックエンドは hatchling（[ADR-0020](../decisions/0020-hatchling-build-backend.md)）。NumPy は `strategy.catalog` 内部に限り条件付き使用可（[ADR-0021](../decisions/0021-numpy-in-strategy-catalog.md)） | [ADR-0019](../decisions/0019-quality-tools.md) |

**B-4 の境界**

- `Price`、`Quantity`、`Money`、SL/TP、約定価格、費用、balance、予約額: Decimal。
- EMA、ATR、リターンなどの Feature 計算: float。
- float から注文価格へ移すときは、専用変換処理で価格刻みに丸める。
- `Decimal(float_value)` は禁止し、文字列表現などを介して変換する。
- 丸め前の float、変換規則、丸め後の `Price` を根拠記録に残す。
- 初版の執行用 OHLC は Decimal として扱う。高速化版で float や整数 tick へ変える場合は、参照実装との同値性を検証する。

### C. データ・研究規律

| # | 項目 | 決定／要決定 | 補足 | ADR |
|---|---|---|---|---|
| C-1 | `data/` の扱い | **決定**: `data/raw/market/` へ移し、データ実体を git 管理外にする。snapshot manifest だけ git 管理 | 配置は下記 | [ADR-0013](../decisions/0013-data-layout-and-gitignore.md) |
| C-2 | 期間分割と封印 | **決定**: 二択を廃止し、期間をアクセス状態で三分類する | 分類は下記 | [ADR-0014](../decisions/0014-period-access-classification.md) |
| C-3 | 初版の対象 | **決定**: USDJPY・JPY 口座・判断 1h・執行 15m・日足生成。他9ペアの保管・受入れ能力は残す | 「USDJPY 単一」は初版の縦断実行範囲であり、移管済みの他ペアを削除する意味ではない | [ADR-0015](../decisions/0015-initial-vertical-slice-scope.md) |
| C-4 | 足生成規則の版 | **決定**: 旧集約の再現版は作らない。NY 17時基準の新規則（`ny17_v2`）を唯一の集約規則とする | 旧基盤の集約出力が引き渡されておらず照合対象がない。入手できれば別版として追加 | [ADR-0024](../decisions/0024-no-legacy-aggregation-reproduction.md) |
| C-5 | swap / rollover | **決定**: 初版未計上（結果に明記） | 政策金利差を実 swap と同一視しない【合意済み】 | [ADR-0029](../decisions/0029-swap-rollover-not-modeled-in-initial-version.md) |
| C-6 | 補助データ（金利等） | 初版外【合意済み】 | 上位文書 §3.1 | — |

**C-1 の配置**

```text
data/raw/market/
  移管した20個の CSV。上書き禁止。git 管理外

data/snapshots/<snapshot_id>/
  受入れ後の正規化データ。実体は git 管理外、manifest.json と access_log.jsonl は git 管理

runs/
  実行結果。git 管理外
```

現在の `.gitignore` は `data/*` を一括除外しており、このままでは snapshot manifest も追跡できない。manifest だけ再包含する規則を段階−1で入れる。

**C-2 の三分類**

| 区間 | 分類 | 扱い |
|---|---|---|
| 2016〜2023 | `RESEARCH_HISTORY` | 初版の開発・研究に使用可能 |
| 2024〜2025 | `LEGACY_HOLDOUT` | 旧基盤のアクセス履歴を引き継ぐ。使用済みなら `CONSUMED` として再選定に使わない |
| 2026年分 | `QUARANTINED_UNASSIGNED` | 自動的に holdout へ昇格させず、アクセス履歴確認まで通常経路から読めなくする |

2026年分が未観測だったことを確認できた場合だけ、別 ADR で sealed holdout へ割り当てる。確認できない場合は研究履歴として扱い、将来取得するデータを新しい prospective holdout にする。元 CSV は期間をまたいでいるため、物理分離は raw CSV の移動ではなく、受入れ処理で生成する snapshot partition に対して行う。

`LEGACY_HOLDOUT` の partition だけが `HoldoutState`（`SEALED` / `CONSUMED`）を持つ（2026-09-20 改訂、ADR-0014）。`SEALED` は旧基盤で未観測と確認できた partition だけで、holdout_gate を通る最終評価で読むと不可逆に `CONSUMED` へ遷移する。`CONSUMED` は holdout としての再選定・最終評価に永久に使用禁止で、研究用途では明示的な opt-in がある場合のみ読め、利用の事実と目的を run manifest に記録し、結果を holdout 成績として扱わない。状態は追記専用の access log（`access_log.jsonl`、git 管理）から導出し、許可発行・消費記録・公開を fail-closed で行う。消費遷移は origin への push を compare-and-set として直列化し、push 成功前にデータを公開しない。

### D. プロセス

| # | 項目 | 決定／要決定 | 補足 | ADR |
|---|---|---|---|---|
| D-1 | 設計文書の承認単位 | **決定**: 設計文書は1文書ずつ承認する。複数文書の並行執筆は可、承認は個別 | 文書内で確定／案／要決定を分ける | [ADR-0022](../decisions/0022-design-doc-approval-unit.md) |
| D-2 | 段階0の進め方 | **決定**: 修正版 (b)。最小縦断で使う契約が確定してから実装開始。開始条件は下記 | 文書番号だけで判断しない | [ADR-0016](../decisions/0016-implementation-start-conditions.md) |
| D-3 | Codex review ループ | **決定**: 旧ツールをそのまま移植せず、スキルを書き換えて最小 poller を新規作成 | 構成は下記 | [ADR-0017](../decisions/0017-codex-review-tooling.md) |
| D-4 | ADR の運用 | **決定**: 1決定1ファイル `NNNN-<slug>.md`、状態は 提案／承認／廃止。状態・日付・決定者・文脈・決定・影響・関連文書を必須とし、廃止時は後継 ADR への参照を必須 | — | [ADR-0023](../decisions/0023-adr-format.md) |

**D-2 の実装開始条件**

1. 段階1開始前に D01〜D03 を確定する。
2. 段階2開始前に D04、D05 の最小ランタイム範囲、D06 の最小縦断範囲、D07 の単一実行評価境界を確定する。
3. T01 で、戦略定義から注文・約定・単一評価まで紙上で通す。
4. その範囲だけ実装する。
5. WAIT、追い越し、複雑な確認は D05 の残りとして段階3前に確定する。

D06 を「骨子だけ」で実装へ進めない。注文状態、フェーズ順、原子性、run_end、trace 型は最小縦断の範囲でも確定が必要である。一方、D05 と D07 の将来機能をすべて書き切る必要はない。

**D-3 の構成**

現在の pr-review スキルには、旧リポジトリ名の description、存在しないレビュー方針文書への参照、存在しない poll スクリプトへの参照、旧プロジェクト固有の Gate 分類がある。次の構成へ置き換える。

- 本リポジトリ用のレビュー方針を新規定義する。
- pr-review スキルから旧プロジェクト固有語彙を除去する。
- GitHub 上の review、inline comment、issue comment を取得する最小 poller を本リポジトリ用に作る。
- 4分間隔、初回 P0/P1 なしなら終了、最大2巡という運用は維持する。**巡数の規定は 2026-09-20 の人間の決定で置き換えられた**（正本は [PR レビュー方針](../pr_review_policy.md) §4.1・§4.4）。4分間隔は変わらない。
- merge 判断は人間に残す。

### E. 戦略・執行の意味論

A〜D のどれにも属さない、戦略ランタイムと約定モデルの振る舞いに関する決定を置く。上位文書 §4.5・§4.7.7 が両論併記のまま残していた項目である。

| # | 項目 | 決定／要決定 | 補足 | ADR |
|---|---|---|---|---|
| E-1 | 足内の SL/TP 競合 | **決定**: 解像度階層に従い時系列順の下位足で再帰的に解決する（足内競合解決契約）。最小解像度でもなお順序が観測不能な場合だけ `UNRESOLVED` として SL 優先を適用 | 下位足は親足を完全に被覆し価格基準・足境界・利用可能時刻が整合するものだけを使う。解決方法・使用系列・解決した子足・未解決時の裁定を結果に記録。下位足の不足はデータ能力検査で実行可否を決める | [ADR-0030](../decisions/0030-sl-priority-on-intrabar-sl-tp-conflict.md) |
| E-2 | 確認待ち中の条件の再検査 | **決定**: 固定する条件（`SNAPSHOT_AT_OPPORTUNITY`）と継続成立を要求する条件（`REQUIRE_UNTIL_ORDER_REQUEST`）を `OpportunityValiditySpec` として `StrategyDefinition` に必須指定。暗黙の既定値を設けない | 不成立は `MARKET_STATE_INVALIDATED` で終端し復活させない。欠損は `ValidityBinding` の `MissingInputPolicy` に従い、待機しても期限は延長しない。上位文書 §4.5 | [ADR-0031](../decisions/0031-opportunity-validity-spec-for-waiting-conditions.md) |
| E-3 | 再発火と複数取引機会 | **決定**: 各発火が固有 `opportunity_id` を持つ不変の取引機会を生成する。内容の上書き（置換）は禁止。関係は `OpportunityConcurrencySpec` として必須指定 | 新しい Trigger を優先する設定でも既存は `SUPERSEDED` で終端し、新規に生成する。受付起因の終了は専用の `CLOSED_BY_ORDER_ACCEPTANCE`、同時保持上限による見送りは専用の `CONCURRENCY_LIMIT_REACHED`（2026-09-20 改訂）。自身の発注試行の拒否は `ORDER_ATTEMPT_REJECTED`、自身の注文の受付は `FULFILLED_BY_ORDER_ACCEPTANCE`（2026-09-21 改訂、ADR-0032 補足4）。評価要求側の追い越しは `REQUEST_SUPERSEDED` へ改名（ADR-0033）。同一 `event_id` の再配送は冪等性検査で除外。上位文書 §4.5 | [ADR-0032](../decisions/0032-opportunity-concurrency-spec-for-retrigger.md) |

### パッケージ名（決定済み）

```text
distribution name: odyssey-trading-research
import package:    odyssey_fx
source directory:  src/odyssey_fx/
```

使用例: `from odyssey_fx.strategy import StrategyDefinition`、`from odyssey_fx.backtest import RunBacktest`。`odyssey` だけでは用途が広すぎ、`fxresearch` は一般名すぎる。`odyssey_fx` ならリポジトリ固有性と FX 限定という合意済み範囲の両方が分かる（[ADR-0005](../decisions/0005-src-layout-and-package-name.md)）。

## 7. 詳細設計バックログ（上位文書の未確定事項の基盤別整理）

上位文書で「詳細設計対象」「案」「確認中」「要決定」とされた事項を、担当パッケージと扱う設計文書（第8.1節）に割り当てる。ここでは決定しない。

### 7.1 共通カーネル（D02）

- ID の具体表現、`ProcessingPoint` のフェーズ列挙との関係、時刻の比較規則。
- `Money` / `Price` / `Quantity` の演算と丸め API、銘柄仕様の版固定。
- `ReasonCode` の初期語彙全件、複数理由成立時の代表理由と診断一覧、`POSITION_CLOSED` の追加。
- 診断コード（ウォームアップ不足、欠損/無効、未取得/更新遅延、`max_age` 違反）の enum。

### 7.2 市場データ・時刻基盤（D03）

- 受入れ: 列名・時刻書式・欠損表現の確認、UTC 変換の固定、`source` 列の分離、価格基準（bid）の確認、manifest 項目の確定。
- 足生成: NY 17時基準の区間定義と DST、不完全足の採否、区間開始と最初の観測の区別、旧集約再現版と新規則版。
- 検査: DST・週末・短セッション・不完全足・重複・欠損・銘柄間ずれの検査仕様と結果型。
- カレンダー・セッション・時間足定義・公開予定の設定形式。
- `OnBarClose` を足終了通知と公開通知のどちらに結ぶか、通常公開遅延がある系列の起動時点。
- 遅延シナリオの型（固定/注入/確率的）、版、実現した公開時刻列の保存。
- `DurationWindow` の端点、データ開始前、全期間休場の扱い。
- 封印 holdout の物理分離、アクセス規則、閲覧履歴の引継ぎ形式（`evaluation.holdout_gate` と共同）。

### 7.3 戦略基盤（D04・D05）

**宣言モデル（D04）**

- `InputSpec` / `OutputSpec` / `InputBinding` の確定（第4.3.8節の案）、`InputArity`、複数入力の表現。
- `DataTypeRef` の登録・版固定・接続検証。
- `ParameterSpec`（型・単位・範囲・列挙）、`ParameterValue`。
- `EvaluationSpec` / `EvaluationSchedule`（`OnBarClose` / `OnInputEvent`、複数起動条件、起動条件ごとの必須入力）。
- `MissingInputPolicy` の動作タグごとの型（WAIT: 期限・期限切れ処理・対象区間・再開時刻・`on_superseded`／USE_PREVIOUS: 遡り上限・記録・許可する欠損理由）。
- `StateSpec`（状態型の参照・初期化・リセット・保存/復元・実装との照合）。
- `TemporalConstraints`（入力間の時刻整合性・ウォームアップの型と検査）。
- `EntryPolicy` のモード別型（即時/確認待ち、確認期限の単位、通常の失効）。**再発火は `EntryPolicy` の責務から外す**（2026-09-20 改訂、ADR-0032）。
- Trigger 部品の契約と状態における遷移（エッジ）検出・再武装規則。条件が true であり続ける場合に再発火とするかどうかはここで決める。ADR-0032。
- `OpportunityValiditySpec` と `ValidityBinding` の型（`SNAPSHOT_AT_OPPORTUNITY` / `REQUIRE_UNTIL_ORDER_REQUEST` の分類、欠損時の `MissingInputPolicy` の指定）。ADR-0031。
- `OpportunityConcurrencySpec` の型（異なる Trigger イベントから生成された取引機会同士の保持・終了・同時競合・受付後処理、`on_order_accepted`）。ADR-0032。
- `RuntimeInputRef` の対象区分（初版は建玉・許可された口座情報。未約定注文は拒否）。
- 入力ポートの時間的束縛（対象区間束縛／現在状態束縛）のフィールド。
- スキーマ版の保存場所、設定ファイル表現、内容ハッシュの算出規則。
- `Opportunity.reference_values` のスキーマ宣言、`ManagementAction` の要求種別、Exit 複数ルールの合成。

**ランタイム・カタログ（D05）**

- 同時刻の複数起動条件の配送・評価回数、確認待ち状態の管理主体（ランタイム側で一元管理する案）。
- イベント出力の複数購読の配送規則、COMMAND を加工する部品の可否。
- 発火時に凍結する値と随時更新する値の具体的な列挙（`Opportunity.reference_values` のスキーマと合わせる）。待機中の条件再検査（ADR-0031）と再発火・複数機会（ADR-0032）は 2026-09-20 に確定済みで、D05 では `OpportunityValiditySpec` / `OpportunityConcurrencySpec` の型と再検査の起動点（確認評価時・`OrderRequest` 生成直前）、Trigger 部品の再武装規則を定める。
- 取引機会の完全な状態機械（2026-09-21 確定、D05 §7）。非終端は `OPEN` / `CONFIRMED` / `ORDER_PENDING`、終端は `TERMINATED` の1つで、区別は終端理由が持つ。未解決だった3点も決着した: 後続確認が成立した状態は**中間**、受付前の審査で拒否された機会は `ORDER_ATTEMPT_REJECTED` で**終端**、自身の注文が受け付けられた機会は `FULFILLED_BY_ORDER_ACCEPTANCE` で**終端**。
- Feature の鮮度伝播、複数系列 Feature の鮮度。
- 追い越しの既定（Feature / MarketState）、複数入力の同時保留、期限切れの優先順位。
- 再審査設定の型・配置・回数上限（初版は未実装として拒否）。
- 合成部品（AND/OR、遷移検出、N 本継続、A 後 N 本以内の B、確認待ち）の契約。
- 初版カタログの指標一覧と計算規則（EMA の初期化・更新方法を含む）。
- 学習する部品（fit / 推論分離）は将来。

### 7.4 バックテスト基盤（D06）

- 4型（`OrderRequest` / `AcceptedOrder` / `FillRecord` / `RiskReservation`）の補助型全フィールド: `ExecutionCommitment.eligibility`（`ScheduledOpen` / `ImmediateAfterFill` / `ProtectionHit`）、`AcceptedEntryTerms`、`ReferenceQuote`、`CostEntry`、`ExitPlanRef`、`InitialProtectionPlan`、`CloseCause`、`AttemptDecision`、`RiskAssessment`。
- 全順序化の優先度の向き・ID 比較規則・`strategy_priority` の設定。
- 有効時間の具体値（エントリー/決済別）、週末持ち越し設定の型名（初版は禁止）。
- Δ（許容不利約定幅）の銘柄別設定、費用予算の内訳、価格/数量刻み、レバレッジ/保有枠/証拠金検査。
- spread モデル、slippage モデル、`CostModel` の内訳、swap 未計上の明記。
- 足内 SL/TP 競合の解決契約は確定済み（2026-09-20、ADR-0030）。D06 では、解像度階層の宣言形式、下位足の被覆・価格基準・足境界・利用可能時刻の整合検査、再帰的な解決手順、`UNRESOLVED` の裁定、約定記録・run manifest への記録項目（解決方法・使用系列・解決した子足・未解決時の裁定）、下位足不足時のデータ能力検査と実行可否を定める。gap、約定ずれ超過、口座強制縮小の対象・優先順位、約定直後以外の緊急決済の実行時点は引き続き要決定。
- 執行データの遅延/欠損時の停止、内部約定解決と戦略判断の順序。
- 通貨換算（クロス通貨の経路、換算率の観測時点）。
- Exit: トレーリングの評価足、初期 TP の算定時点、期間 Exit。
- 予約から建玉割当への移行の原子性、イベント冪等の実装、失敗時の整合 snapshot。
- run manifest・trace・`BacktestResult` の形式、再現性の許容誤差、能力不足時の拒否仕様。
- 末尾3集計の費用区分と集計式。
- 保守的執行変種（`entry_delay_bars`）の適用範囲。

### 7.5 評価基盤（D07・D09）

**単一実行（D07）**

- 指標定義の確定（リターン、MTM DD、リスク調整、年率化、取引数、exposure、費用）。
- 0 取引・計算不能・失敗の表現、実行状態の区分。
- `ResearchPolicy` v1 の内容と検査、複雑性の計測方法。
- 実験 manifest の項目と保存形式、再現手順。
- 診断（遅延シナリオ別、理由コード別の集計）。

**複数実行（D09、段階5前）**

- `SearchPlan` の表現、探索アルゴリズム、終了条件、中断時の試行済み/未試行/失敗の区別。
- 分割（train/validation/WF、purge、fold 境界の建玉・状態）、ウォームアップと採点区間の分離。
- 選定規則、合否閾値、集約方法（結果を見る前に固定）。
- holdout 隔離と閲覧履歴。
- 将来の WF 持ち越し・実行再開は別設計。

## 8. 構築計画

### 8.1 設計文書の一覧と順序

各文書は「確定／案／要決定」を区別して書き、承認後に実装対象とする。番号は順序の目安である。実装開始条件は第6節 D-2（ADR-0016）に従い、段階1前に D01〜D03、段階2前に D04・D05（最小ランタイム範囲）・D06（最小縦断範囲）・D07（評価境界）・T01 を確定する。

| ID | 文書 | 内容 | 前提 | 対応段階 |
|---|---|---|---|---|
| D00 | 本書 | 全体計画 | 上位文書 | — |
| ADR | 技術選定・構成の決定記録 | 第6節 A/B/D の各決定を1件1ファイル | D00 承認 | 段階−1〜0 |
| D01 | [アーキテクチャ・依存規則・ディレクトリ確定版](D01_architecture_and_dependency_rules.md)（承認 2026-09-19。v2.5 は PR #23、**v2.6 は PR #12**） | 第3〜4節を決定に基づき確定し、`import-linter` 契約を含める | ADR A-1〜A-5 | 段階0 |
| D02 | [共通カーネル型設計](D02_common_kernel.md)（承認 2026-09-20。v1.6 は PR #18、v1.7 は PR #19、**v1.8 は PR #22**） | 第5.1節・第7.1節 | D01、B-4 | 段階0 |
| D03 | [市場データ・時刻基盤設計](D03_marketdata_and_time.md)（承認 2026-09-20。v1.3・v1.4 は PR #19、v1.5 は PR #20、**v1.6 は PR #22、v1.7 は PR #12**） | 第5.2節・第7.2節。受入れ手順と検査仕様、封印分離 | D02、C-1〜C-4 | 段階0〜1 |
| D04 | [戦略宣言モデル詳細設計](D04_strategy_declarations.md)（承認 2026-09-20。v1.8 は PR #18、**v1.9・v1.10 は PR #22、v1.11 は PR #24**。保存形式の版は 2） | 第5.3.1節・第7.3節前半。補助型全フィールド、宣言から導かれるコンパイル時検査の一覧、設定ファイル表現 | D02、D03（系列定義） | 段階0 |
| D05 | [戦略ランタイム・カタログ・コンパイラ設計](D05_strategy_runtime.md)（v1.2 承認 2026-09-21、段階2 の最小ランタイム範囲。v1.3 は PR #18、v1.4 は PR #20、**v2.0 は PR #22**＝段階3 の範囲、**v2.1・v2.2 は PR #24**。承認待ち） | 第5.3.2〜5.3.5節・第7.3節後半。部品カタログの構成と初版の部品、コンパイラの実装（依存グラフ構築・ハッシュ計算）、取引機会の状態機械。段階3 の複数時間足・市場状態・後続確認・待機・遡り・追い越し・上流出力の履歴窓は **v2.0**（他文書が「v0.2」と呼んでいた呼び方は 2026-09-23 に v2.0 へ統一した。要決定 Q10〜Q30 はすべて決定済み（2026-09-23）で、未決の項目は残っていない。段階3 の宣言では実行結果が変わらないため実装は止まらない） | D04 | 段階0〜3 |
| D06 | [バックテストエンジン設計](D06_backtest_vertical_slice.md)（承認 2026-09-21、PR #16。v1.2 は PR #17、v1.3・v1.4 は PR #19、**v1.5 は PR #22、v1.6 は PR #24**。段階2 の最小縦断範囲＋段階3 の記録と適用意味論） | 第5.4節・第7.4節。フェーズ順序、4型全フィールド、状態機械、理由コード、trace/result 形式 | D02〜D05 | 段階0〜2 |
| D07 | [単一実行評価設計](D07_single_run_evaluation.md)（v1.1 承認 2026-09-21、PR #17、段階2 の単一実行評価境界。v1.2 は PR #19、v1.3 は PR #20、**v1.4 は PR #24**） | 第5.5.1節・第7.5節前半。指標・集計・診断・評価の状態・結果の保存と再現性。年率化とリスク調整指標、実験 manifest、研究ポリシーは v0.2（段階4） | D06 | 段階2〜4 |
| D08 | [テスト戦略](D08_test_strategy.md)（**承認 2026-09-23、PR #23**。v1.0。**v1.1 は PR #24**） | 第8.4節を具体化。段階1〜2 で実際に書かれたテスト資産を正本に、テストの7層・意味論テスト一覧（契約行との対応表）・人工データ生成仕様・固定出力の運用・決定論の検証・CI の並びを定める。Q1〜Q3 の決定により、契約行の充足は層を問わないこと・人工データはアクセス分類の対象外であることを確定し、D01 §9 を v2.5 に改訂した | D06 | 段階1〜 |
| T01 | [紙上トレース](../traces/T01_paper_trace.md)（承認 2026-09-21、PR #16。v1.1 は PR #17、v1.2 は PR #18、v1.3 は PR #20、**v1.4 は PR #22**） | 検証戦略 A・B の時刻表を D03〜D06 の型で追跡し、同時刻・数量・末尾処理に暗黙の前提がないことを確認。検証戦略 A は8経路を通し、検証戦略 B は D05 v2.0 待ちの範囲を明示した | D03〜D06 | 段階0 完了条件 |
| T02 | [紙上トレース（検証戦略 B・段階3）](../traces/T02_paper_trace_strategy_b.md)（**承認 2026-09-23、PR #24**。v1.0） | 段階3 の設計（D05 v2.0）が挙げた9経路を、4つの遅延シナリオの人工データで1判断時点ずつ追う。未記述20件を洗い出し、うち13件を D05 v2.1・D07 v1.4 へ反映、7件を要決定 Q23〜Q29 として上げて同日に決定・反映した（D05 v2.2・D06 v1.6・D08 v1.1）。**経路2つが宣言と規則から到達しないことを示した**のが最大の結果である | D03〜D08 | 段階3 の着手条件 |
| D09 | 複数実行評価設計 | 第5.5.2節・第7.5節後半 | 段階4 完了 | 段階5 |
| D10 | 拡張能力設計 | 指値・複数建玉・複数銘柄・分割決済・詳細コスト・並列化のうち必要なもの | 段階5 完了 | 段階6 |

### 8.2 段階計画

上位文書第7節の段階0〜6を継承し、段階−1（基盤整備）を加える。

| 段階 | 作るもの | 設計文書 | 完了条件（上位文書 §7 を継承） |
|---|---|---|---|
| −1. 基盤整備 | `.gitignore` の書き換え（manifest 再包含）、`data/raw/market/` への移動、`pyproject.toml`（3.12 系固定）、`.python-version`、uv lock、ツール設定（ruff/mypy/pytest/import-linter）、`src/odyssey_fx/` と `tests/` の骨格、CI、pr-review スキルの書き換えと最小 poller | ADR-0001〜0017 | `uv run pytest` と依存規則検査が空の状態で通る。データ実体が git 管理外で manifest は追跡可能 |
| 0. 契約設計 | D01〜D08、T01 | 同左 | 2本の検証戦略を紙上で追跡でき、同時刻・数量・末尾処理に暗黙の前提がない |
| 1. データ・時刻 | `common`、`marketdata`（受入れ・snapshot・足生成・カレンダー・as-of・公開フィード・検査・遅延シナリオ） | D02、D03 | 未確定の上位足を参照できない。DST・欠損・再読込・遅延注入の検証が通る。実データ受入れで manifest が生成される |
| 2. 最小の縦断 | `strategy`（宣言・コンパイラ・カタログ最小・ランタイムの非待機部分）、`backtest`（全フェーズ、成行・1建玉・リスク・費用・MTM・trace）、`evaluation`（基本指標・run manifest）、`app` CLI。検証戦略 A を人工データで実行 | D04〜D07 | 人工データで注文・数量・損益・資産が手計算に一致。同一入力の再実行で trace が一致。warmup 中の注文ゼロ |
| 3. MTF と後続確認 | MarketState、複数足入力、取引機会、ExecutionFilter、WAIT_FOR_INPUT、追い越し、期限/失効、合成部品、遅延シナリオ4ケース。検証戦略 B | D05 | 戦略 B を同じ基盤で記述でき、時刻境界と取消理由を trace できる。遅延シナリオ別の差分を追跡できる |
| 4. 単一実行評価の整備 | 実験 manifest、指標定義の確定、診断、研究ポリシー検査、結果保存。許可された実データでの実行 | D07 | 結果から設定と入力を特定でき、別プロセスで再現可能。失敗/0取引も説明できる |
| 5. 複数実行の評価 | 探索、train/validation、WF、頑健性、隔離 holdout | D09 | 選定が train 内で閉じ、全試行を記録し、holdout を通常探索で読めない |
| 6. 実需に応じた拡張 | 指値/逆指値、複数建玉・銘柄、分割決済、詳細コスト、並列化 | D10 | 追加能力ごとの意味論テストが通り、既存設定の意味を変えない |

段階3〜4を終えた状態を最初の基盤リリースとする【合意済み】。

### 8.3 各段階の作業手順

CLAUDE.md の規則に従う。

1. **設計**: 該当する設計文書を `docs/design/` に作成し、「確定／案／要決定」を分ける。要決定は ADR の草案を添える。
2. **合意**: 人間が文書を承認する。承認前に実装を始めない。要決定が残る場合は、その部分を除いた範囲で承認を得る。
3. **隔離**: 実装は `.claude/worktrees/<task>` に隔離して行う（base は `origin/main`）。
4. **実装**: 承認範囲だけを実装する。承認外の設計判断が必要になった場合は、実装を止めて文書へ差し戻す。
5. **検証**: 単体・意味論・プロパティ・golden・依存規則の各テストを通す。
6. **レビュー**: push → PR → `@codex review` → ポーリング → 分類 → 修正 → 再依頼を、承認停止なしで自走する。**規則の正本は [PR レビュー方針](../pr_review_policy.md)** であり、本節に写さない（§2 要否、§3.1 対象範囲と3分類、§4 終了条件・傾向の読み取り・安全弁、§5 設計適合レビュー、§6 仮置き、§7 merge 前提）。
7. **merge**: 人間が merge 可否を判断する。

### 8.4 テスト戦略（D08 で具体化）

| 種別 | 対象 | 例 |
|---|---|---|
| 単体 | domain の値型・状態機械・計算規則 | 予約額計算、SL の丸め方向、注文状態遷移の許可/拒否 |
| 意味論 | 上位文書 §7.2・§4.7.15E の各項目を1件1テストで名前を付けて固定 | 受付拒否で注文/予約が残らない、同じ `event_id` の再配送、期限と open の同時刻、保護決済と通常決済の競合、末尾 MTM の非執行、warmup 中の注文ゼロ |
| プロパティ | 不変条件 | 将来データを追加しても過去時点の出力が変わらない、SL の単調性、台帳の整合（約定・現金・実現/未実現・MTM） |
| golden | 人工データでの固定 trace | 検証戦略 A・B の trace を保存し、変更時に差分を検査 |
| 再現性 | 同一 manifest の再実行 | trace と result の digest が一致 |
| 依存規則 | `import-linter` | 第3.3節の表、外部ライブラリの adapters 限定 |

人工データ生成器（`tests/fixtures/synthetic/`）は、DST 境界・週末・欠損・gap・SL/TP 同時到達を意図的に含むケースを作れるようにする。実データは段階4以降、許可された期間だけで使う。

## 9. リスクと対策

| リスク | 影響 | 対策 |
|---|---|---|
| 契約設計が長引き縦断が通らない | 抜けの発見が遅れる | D-2 の修正版 (b)。最小縦断に必要な契約だけ確定して段階2で戦略 A を早期に通す |
| 上位文書の「案」を確定と誤認して実装する | 手戻り | 本書第7節のバックログで案を明示し、各設計文書で確定へ昇格させてから実装 |
| 依存規則の崩れ（部品実装が市場データ全体を読む等） | 先読み防止の破綻 | `import-linter` と能力検査、view 型の設計で構造的に不可能にする |
| 数値精度の混在（Decimal と float） | 台帳不整合 | B-4 で境界を固定し、変換点を1か所に集約して記録 |
| データの誤コミット | リポジトリ肥大 | 段階−1で `.gitignore` を書き換え（実体除外・manifest 再包含） |
| 封印期間・未割当期間の誤使用 | 評価の信頼性喪失 | snapshot partition 単位の物理分離と `holdout_gate`、閲覧履歴の記録（C-2、決定済み） |
| シェル既定の Python（3.8 / 3.9）での誤実行 | 標準機能不足・環境差 | `.python-version` と uv で 3.12.13 に固定し、`uv run` 経由でのみ実行する |
| pr-review スキルが旧リポジトリ依存のまま | レビュー手順が回らない | 段階−1でスキルを書き換え、最小 poller を新規作成（D-3、決定済み） |

## 10. 次のアクション

第6節の A 全項目、B-1〜B-4、C-1〜C-3、D-2〜D-3、パッケージ名は 2026-09-18 に決定済み（ADR-0001〜0017）。残る事項と着手順は次のとおり。

1. **段階−1 完了・D01〜D03 承認済み**（2026-09-20）。設計上の実装開始条件（ADR-0016 条件1）は満たした。段階1（`common` → `marketdata` の順に別 PR）の着手は、設定パーサー集約ルール（契約 F5c）の pyproject 反映と、snapshot 閲覧記録（`access_log.jsonl`）の `.gitignore` 再包含を行う骨格 PR の merge を前提とする（閲覧記録が追跡できないと holdout の fail-closed 手順が成立しない）。段階0 の残りは D04〜D08 と T01。
2. **段階−1（基盤整備）の着手**: `.gitignore` の書き換え（manifest 再包含）、`data/market/` → `data/raw/market/` の移動、`pyproject.toml`（`requires-python = ">=3.12,<3.13"`）、`.python-version`（3.12.13）、uv による lock、ツール設定、`src/odyssey_fx/` と `tests/` の骨格、import-linter 契約、pr-review スキルの書き換えと最小 poller。コミットを生むため worktree で行い、PR として提出する。
3. **D01（アーキテクチャ・依存規則・ディレクトリ確定版）の作成**: 第3〜4節を決定に基づき確定し、import-linter 契約を含める。
4. **D02、D03 の作成**: 段階1の開始条件。
5. 段階1以降の要決定（B-5、B-7、B-8、C-4、C-5）は、該当する設計文書の中で選択肢と推奨を再掲し、その文書の承認時に決める。
6. **段階4 の実データ分類（第 1 弾、2026-09-25）**: 受入れの検査が報告した分類対象の警告 23,985 区間を、人間の決定 1〜5（元日・2017 年クリスマスの取引日単位の休場、クリスマス部分日の短縮セッション、サマータイム谷間週の日曜早期データの除外、単発の大規模欠損 2 件と散発的な欠落のデータ欠損）で**すべて分類し、snapshot を確定した**（未分類 0。カレンダー `fx_ny17` v2、分類ファイル `configs/calendars/classification_fx_ny17_v2.yaml`）。確定後の検査報告に残る警告は、データ欠損と分類した存在すべき足の欠落 8,140 区間だけである。残る作業は snapshot の承認（`odyssey-fx data approve`）と、データ欠損 8,140 区間を個別に見直すかの判断。

段階−1 と D01 は互いに依存しないため並行できる。どちらを先に進めるかはユーザーの指示に従う。
