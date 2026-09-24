# D01: アーキテクチャ・依存規則・ディレクトリ構成（確定版）

作成日: 2026-09-18
改訂: 2026-09-18 v2（レビュー指摘3点の反映、仮置き4件の確定）、2026-09-19 v2.1（F5c 追加）、2026-09-20 v2.2（`common` のモジュール一覧に `canonical.py`・`errors.py` を追記、`configs/datasources/` を追加。依存規則の変更なし）、2026-09-20 v2.3（設定パーサー集約ルール F5c の禁止範囲を `app` 配下全体（`app.config` を除く）へ拡大。ADR-0026）、2026-09-22 v2.4（第4節: 利用側が application より下の層のときのポートの定義場所を明記し、下位足の供給（`IntrabarSeries`）を表に追加。依存規則の変更なし）、**2026-09-23 v2.5（第9節のテスト配置表に統合（`tests/integration/`）と受入（`tests/acceptance/`）の2層を追加。段階2 の実装で必要になって作られた2層が表に無く、テスト配置の正本が実態と食い違っていたため。人間の決定（D08 §14 Q2、選択肢1）により D08 の承認と同じ PR で改訂した。依存規則の変更なし）**、**2026-09-23 v2.6（§10.2 の snapshot ディレクトリ図に、確定 snapshot の検査報告 `integrity_report.json`（確定段階で再実行した場合は暫定報告 `integrity_report_provisional.json` も）を git 管理対象として追記。2026-09-20 の人間の決定3件を 2026-09-23 に main へ追随させて取り込んだもの。ADR-0013 改訂、D03 v1.7。依存規則の変更なし）**
状態: **承認（2026-09-19）**。ADR-0016 条件1（段階1開始前に D01〜D03 を確定）のうち D01 は充足。
上位文書: [全体計画書](fx_research_platform_overall_plan.md) 第3〜4節、[ADR-0001〜0008, 0011〜0013, 0018〜0021, 0026](../decisions/README.md)
対応段階: 段階−1（骨格）で実装し、以降のすべての設計文書・実装が従う。

## 0. 本書の位置付け

全体計画書第3〜4節の提案を、決定済み ADR に基づき「確定」として書き下ろす。本書が確定するのは次の4つである。

1. パッケージと層の定義、各層に置いてよいもの・置いてはいけないもの。
2. パッケージ間・層間の依存規則と、その `import-linter` 契約。
3. ポート（抽象インターフェース）の所在と実装者。
4. ディレクトリ構成と、設定・データ・成果物の配置。

個々の型・関数・ファイルの中身は D02 以降で決める。本書に反しない範囲であれば、後続文書はサブパッケージ内にモジュールを追加できる。**新しいサブパッケージの追加、依存規則の変更は本書の改訂と ADR を要する**（第12節）。

凡例は全体計画書第0節に従う。本書内で「確定」と書いたものは承認後に正本となる。「仮置き」は未決定事項に対する暫定運用で、第14節に一覧する。

### v2 での変更点

| # | 指摘 | 反映箇所 |
|---|---|---|
| 1 | ポート設計（利用側が Protocol を定義し `app` が注入）に合わせ、他パッケージの `application` への直接依存を閉じる | 第1節の公開範囲、第2節 application 層の許可依存、第3.2節の許可表、第4節、第6節 F4/F6/F7/F8 |
| 2 | 許可表と機械検査の不一致（`evaluation → strategy.runtime/catalog` が検出されない、layers 契約が非 exhaustive） | 第6節: L1 を container 付き相対レイヤ契約にし L1〜L2c を `exhaustive = true`、F8 追加 |
| 4（v2.1） | PR #2 の Codex 指摘: `app.cli` / `app.composition` が Pydantic・YAML を import しても F5a が検出しない | 第6節: F5c を追加し、`app.config` だけを例外にする。pyproject への反映は別 PR |
| 5（v2.1） | PR #2 の Codex 指摘（round 2）: F5c が `app.cli → app.config → yaml` の間接経路まで禁止し、正当な設定読込が失敗する | F5c に `allow_indirect_imports = true` を付け、直接 import だけを禁止する |
| 6（v2.3） | PR #4 の Codex 指摘: F5c の source が `app.cli` / `app.composition` の列挙のため、`app/__init__.py` や将来 `app` 直下に追加するモジュールが YAML / Pydantic を直接 import しても検出しない | 第6節: F5c の source を `odyssey_fx.app` 全体にし、`app.config`（とその配下）からの import だけを `ignore_imports` で許可する（ADR-0026） |
| 3 | application 層の外部ライブラリ禁止と `strategy.catalog` の NumPy 許可の不整合 | 第2節の層定義、第5節の NumPy 使用条件 |
| — | 仮置き4件（B-6、B-9、hatchling、NumPy）の確定 | 第5節、第10.1節、第11節、第14節 |

## 1. パッケージと責務（確定）

import 名は `odyssey_fx`（ADR-0005）。6つのトップレベルサブパッケージを持つ。

| パッケージ | 責務 | 他パッケージへ公開するもの | 公開しないもの（`app` だけが結線のために参照できる） |
|---|---|---|---|
| `odyssey_fx.common` | 全パッケージが共有する値型・参照型・理由コード。業務ロジックと I/O を持たない | すべて（時刻、区間、`ProcessingPoint`、ID 型、`Price`/`Quantity`/`Money`、`Symbol`/`SymbolSpec`、`TimeframeRef`、`ReasonCode`、各種 `Ref`） | — |
| `odyssey_fx.marketdata` | 原データの受入れ・snapshot・足生成・カレンダー・公開スケジュール・as-of 参照・完全性検査・遅延シナリオ | `domain`（`Bar`、系列 ID、`SnapshotManifest`、`TradingCalendar`、`SeriesSchedule`、`DelayScenario`、検査結果型） | `application`（ユースケースと、他パッケージのポートを満たす実装クラス）、`adapters` |
| `odyssey_fx.strategy` | 戦略の宣言モデル、部品カタログ、コンパイラ、戦略ランタイム（部品評価・WAIT・追い越し・取引機会） | `declarations`、`records`、`compiler`（`CompiledStrategy`、`compile`）を `backtest`・`evaluation` へ。`runtime`（ランタイム API とポート定義）を `backtest` へ | `catalog`（実装解決は `compiler` 経由）。`runtime` は `evaluation` から不可 |
| `odyssey_fx.backtest` | 時刻進行・フェーズ順序・注文受付・リスク審査・執行・口座台帳・記録 | `domain`（注文・約定・予約・建玉・口座の型）、`trace`（`BacktestResult`、run manifest） | `application`（`RunBacktest` とポート）、`engine`、`admission`、`execution`、`portfolio` |
| `odyssey_fx.evaluation` | 実験定義・manifest・単一/複数実行の指標・診断・研究ポリシー・探索・分割・holdout 隔離・結果保存 | `domain`、`application` | `adapters` |
| `odyssey_fx.app` | 構成ルート（ポートと adapters の結線）、設定ファイル読込、CLI | なし（誰からも import されない） | — |

「公開しないもの」は、`import-linter` の forbidden 契約で機械的に禁止する（第6節）。許可表（第3.2節）と契約は一対一に対応させ、表にあって契約にない禁止を残さない。

## 2. 層の定義と規則（確定）

各パッケージの内部を、内側から次の層に分ける（ADR-0002）。層の名前はパッケージごとに異なるが、規則は共通である。

| 層 | 置くもの | 許可する依存 | 禁止 |
|---|---|---|---|
| **domain** | エンティティ、値オブジェクト、状態機械、ドメイン規則（予約計算、SL 単調性、丸め、理由コードの組合せ検証） | Python 標準ライブラリ、`odyssey_fx.common`、同パッケージの domain、下位パッケージの domain | I/O、外部ライブラリ、実時計、乱数、環境変数 |
| **application** | ユースケース、ポート定義（`Protocol`）、ドメイン横断のサービス、他パッケージのポートを満たす実装（I/O を伴わないもの） | domain、`common`、下位パッケージの domain。他パッケージの application は第3.2節の許可表に列挙したもの（`strategy.compiler`、`strategy.runtime` の公開 API）だけ | adapters、I/O・永続化・設定解析などのインフラライブラリ、実時計、乱数。**例外**: `strategy.catalog` に限り、承認された純粋な数値計算ライブラリ（第5.1節） |
| **adapters** | ポートの実装のうち I/O や外部ライブラリを伴うもの（CSV 読込、Parquet 保存、レポート出力） | 同パッケージの application/domain、`common`、外部ライブラリ | 他パッケージの adapters、他パッケージの application |
| **app** | 結線、CLI、設定読込 | すべて | 業務ロジック |

### 2.1 パッケージ別の層対応

| パッケージ | domain 層 | application 層 | adapters 層 |
|---|---|---|---|
| `common` | 全モジュール | — | — |
| `marketdata` | `domain` | `application` | `adapters` |
| `strategy` | `declarations`、`records` | `catalog`、`compiler`、`runtime` | —（初版では adapters を持たない。設定読込は `app.config`） |
| `backtest` | `domain` | `admission`、`execution`、`portfolio`、`trace`、`engine`、`application` | —（記録の書き出し先は `evaluation.adapters` または `app` が実装） |
| `evaluation` | `domain` | `application` | `adapters` |

`strategy.catalog` は部品実装（純粋関数＋明示状態、ADR-0008）の置き場であり、層としては application に属するが、domain 型（`declarations`、`records`）だけに依存する。

### 2.2 層に共通する規則

1. **境界を跨ぐのは不変の型だけ**。DataFrame や `numpy.ndarray` 等の外部ライブラリ型を domain/application の引数・戻り値・状態・記録に使わない。
2. **決定論**。domain/application は実時計・乱数・環境変数を直接読まない。実行時刻は `ProcessingPoint` として渡し、乱数は seed 付き生成器を注入する。manifest の作成日時など記録目的の実時刻は `app` で取得して渡す。
3. **宣言は不変**。domain の宣言型・記録型は `frozen=True` の dataclass（ADR-0011）。実行中の状態は実行コンテキストに置く。
4. **数値**。`Price`/`Quantity`/`Money`・台帳は Decimal、Feature 計算は float。float → `Price` の変換は `common.money` の専用関数だけが行い、`Decimal(float)` は禁止（ADR-0012）。
5. **構造エラーと欠損の区別**。型・参照・能力の違反は例外で実行を止め、入力欠損は `MissingInputPolicy` で扱う。例外を欠損に読み替えない。
6. **Pydantic は `app.config` だけ**。domain へ Pydantic モデルを流入させず、`app.config` が dataclass へ明示的に変換する（ADR-0011）。
7. **ポートは構造的に満たす**。他パッケージのポートを満たす実装は、そのポート定義を import せずに `Protocol` を構造的に満たす。型の適合は結線箇所（`app.composition`）で mypy が検査する。

## 3. パッケージ間の依存規則（確定）

### 3.1 パッケージ順序

上位から下位への一方向。下位は上位を知らない（ADR-0001）。

```text
app → evaluation → backtest → strategy → marketdata → common
```

### 3.2 サブパッケージ単位の許可表

「可」は import してよい対象。表にない組合せは不可。すべての「不可」は第6節の契約で機械検査する。

| 参照元 | `common` | `marketdata` | `strategy` | `backtest` | `evaluation` |
|---|---|---|---|---|---|
| `marketdata.*` | 可 | 同パッケージの層規則 | 不可 | 不可 | 不可 |
| `strategy.*` | 可 | `domain` のみ | 同パッケージの層規則 | 不可 | 不可 |
| `backtest.*` | 可 | `domain` のみ | `declarations`、`records`、`compiler`、`runtime`（`catalog` は不可） | 同パッケージの層規則 | 不可 |
| `evaluation.*` | 可 | `domain` のみ | `declarations`、`records`、`compiler`（`runtime`、`catalog` は不可） | `domain`、`trace` のみ（`engine`/`admission`/`execution`/`portfolio`/`application` は不可） | 同パッケージの層規則 |
| `app.*` | 可 | すべて | すべて | すべて | すべて |

補足:

- `strategy` が `marketdata.domain` を参照するのは、系列 ID・時間足定義・`Bar` 型を宣言の検証と入力 view に使うため。
- `backtest` が必要とする公開フィード・執行系列・カレンダーは、`backtest.application.ports` の `Protocol` として定義し、`marketdata.application` の実装を `app` が注入する。`backtest` は `marketdata.application` を import しない。
- `evaluation` が必要とする snapshot の取得は `evaluation.application.ports` の `Protocol`（`SnapshotCatalog`）経由、バックテストの実行は `BacktestRunner` 経由とする。`evaluation` は `marketdata.application` と `backtest.application` を import しない。
- `evaluation` が `strategy.compiler` を参照するのは、実験 manifest に `CompiledStrategy` の内容ハッシュを固定するため。`strategy.runtime` と `strategy.catalog` は評価に不要であり禁止する。
- `backtest` が `strategy.runtime` を参照するのは、戦略ランタイムの公開 API 型と、`backtest.engine` が実装するポート定義（`RuntimeContextView`、`OutputSink`）の型を使うため。

### 3.3 パッケージ内の層順序

上から下へ一方向。

| パッケージ | 順序（上 → 下） |
|---|---|
| `marketdata` | `adapters` → `application` → `domain` |
| `strategy` | `runtime` → `compiler` → `catalog` → `records` → `declarations` |
| `backtest` | `application` → `engine` → { `admission` \| `execution` \| `portfolio` \| `trace` } → `domain` |
| `evaluation` | `adapters` → `application` → `domain` |

`backtest` の中間層4つは互いに独立とし、相互 import を禁止する。相互に必要な型は `domain` に置く。`engine` がそれらを組み合わせる。

各 layers 契約は `exhaustive = true` とし、コンテナ直下のサブパッケージはすべて層として宣言する。宣言のないサブパッケージを追加すると契約が失敗する。意図的に層の外に置くモジュールは `exhaustive_ignores` に列挙し、その理由を本書に記す（現時点では該当なし）。

## 4. ポートの所在（確定）

ポートは**利用側**の application 層が `typing.Protocol` で定義し、提供側または `app` が実装する（依存性逆転）。実装者はポート定義を import せず構造的に満たし、`app.composition` が結線する（第2.2節 規則7）。

| ポート | 定義場所 | 実装者 | 用途 |
|---|---|---|---|
| `RawBarSource` | `marketdata.application.ports` | `marketdata.adapters.csv_source` | 原 CSV の読込 |
| `SnapshotStore` | `marketdata.application.ports` | `marketdata.adapters.parquet_store` | snapshot の保存・読込 |
| `MarketDataView` | `strategy.runtime.ports` | `marketdata.application.asof` | 判断時点の as-of 読み取り（最新確定足、履歴窓、期待足検査） |
| `RuntimeContextView` | `strategy.runtime.ports` | `backtest.engine` | 現在処理中の建玉・許可された口座情報 |
| `OutputSink` | `strategy.runtime.ports` | `backtest.engine`（trace へ転送） | `OutputRecord` の受け取り |
| `PublicationFeed` | `backtest.application.ports` | `marketdata.application.publication` | `available_at` 順の公開イベント列 |
| `ExecutionSeries` | `backtest.application.ports` | `marketdata.application.asof` | 執行用系列の open/high/low/close と、予定上の次の足（D03 §6.3） |
| `IntrabarSeries` | `backtest.application.ports` | `marketdata.application.asof` | 足内競合を解く下位足の供給（D06 §7.4） |
| `Calendar` | `backtest.application.ports` | `marketdata.domain.calendar`（domain 型そのもの） | 期限・候補 open・休場の判定 |
| `StrategyRuntime` | `backtest.application.ports` | `strategy.runtime.evaluator` | 公開バッチと現在状態を渡し、出力・注文意図・管理要求を受け取る |
| `TraceSink` | `backtest.application.ports` | `evaluation.adapters.fs_store` または `app` | 記録の書き出し |
| `ResultWriter` | `backtest.application.ports` | 同上 | `BacktestResult` と run manifest の保存 |
| `BacktestRunner` | `evaluation.application.ports` | `backtest.application.run_backtest`（`app` が適合させる） | 単一 run の実行 |
| `SnapshotCatalog` | `evaluation.application.ports` | `marketdata.application`（`app` が適合させる） | 実験 manifest に固定する snapshot の参照と partition のアクセス分類の取得 |
| `ResultRepository` | `evaluation.application.ports` | `evaluation.adapters.fs_store` | 結果の読み書き |
| `ExperimentStore` | `evaluation.application.ports` | `evaluation.adapters.fs_store` | 実験 manifest・探索履歴の保存 |
| `HoldoutAccessLog` | `evaluation.application.ports` | `evaluation.adapters.fs_store` | holdout 閲覧履歴の記録 |

ポート名は D02 以降で変更されうるが、**所在（どのパッケージの application が定義するか）と実装者の層**は本書で確定する。

**利用側が application より下の層なら、構造は下の層で定義し application が同じ名前で再公開する**（確定。v2.4、2026-09-22 の人間の決定。PR #19）。`PublicationFeed` / `ExecutionSeries` / `IntrabarSeries` / `Calendar` を実際に使うのは `backtest.engine` であり、エンジンは1つ上の `backtest.application` を import できない（第3.3節の層順序）。そこで構造（`Protocol`）を `backtest.engine.loop` に置き、`backtest.application.ports` がそれを再公開する。**表の「定義場所」は引き続きポートの所在の正本**であり、結線するのも `app.composition` のままである（実装者はどちらの名前も import せず、構造的に満たす）。この扱いは D06 §3 にも記載する。

## 5. 外部ライブラリの配置（確定）

| ライブラリ | 許可する場所 | 備考 |
|---|---|---|
| 標準ライブラリ（`decimal`、`datetime`、`zoneinfo`、`dataclasses`、`typing`、`hashlib`、`tomllib` 等） | すべて | — |
| Pydantic v2 | `app.config` のみ | ADR-0011 |
| YAML パーサ | `app.config` のみ | ADR-0018。読込条件は第10.1節 |
| 表形式ライブラリ（pandas / polars）、pyarrow | `marketdata.adapters`、`evaluation.adapters`、`app` | B-5 は未決定。いずれにせよ adapters 限定 |
| DuckDB 等の集計 | `evaluation.adapters` のみ | 必要になった時点で追加 |
| NumPy | `strategy.catalog` の部品実装内部のみ（条件は第5.1節）、adapters、`app` | ADR-0021 |
| hypothesis、pytest | `tests/` のみ | — |

初版の `pyproject.toml` はランタイム依存を持たない。ライブラリは、それを使う設計文書の承認時に追加する。

### 5.1 NumPy の使用条件（確定）

`strategy.catalog` に限り、承認された純粋な数値計算ライブラリとして NumPy を使用できる。使用許可は確定、実際に使用する部品は D04/D05 で決める。条件は次のとおり。

- `numpy.ndarray` を部品の公開入出力・状態・trace に出さない。部品の入出力・状態は domain 型（`records`、`declarations` の型と Python の組込み型）だけで表す。
- NumPy の乱数やグローバル状態（`numpy.random`、`numpy.seterr` 等）を使用しない。
- NumPy は部品実装内部の決定論的計算に限定する。
- 使用版を `uv.lock` と環境 manifest（run manifest の環境情報）に記録する。
- NumPy を使わない単純な部品へ使用を強制しない。

## 6. import-linter 契約（確定）

第3節と第5節を次の契約として `pyproject.toml` に記述し、CI と `tests/architecture/` の両方で検査する（ADR-0004）。`include_external_packages = true` とする。layers 契約はすべて `exhaustive = true` とし、未宣言のサブパッケージの追加を検出する。契約名（L1、L2a〜L2c、F1a〜F8、F5c）は CI の出力で参照するため変更しない。

```toml
[tool.importlinter]
root_packages = ["odyssey_fx"]
include_external_packages = true

# (L1) パッケージ間の依存方向。container 付き相対レイヤ・exhaustive で
#      未宣言のトップレベルサブパッケージの追加を検出する。
[[tool.importlinter.contracts]]
name = "L1: package layers (app > evaluation > backtest > strategy > marketdata > common)"
type = "layers"
containers = ["odyssey_fx"]
layers = ["app", "evaluation", "backtest", "strategy", "marketdata", "common"]
exhaustive = true

# (L2) パッケージ内の層。`|` は独立（相互 import 禁止）、`:` は相互 import 許可なので使わない。
[[tool.importlinter.contracts]]
name = "L2a: clean architecture layers inside marketdata / evaluation"
type = "layers"
containers = ["odyssey_fx.marketdata", "odyssey_fx.evaluation"]
layers = ["adapters", "application", "domain"]
exhaustive = true

[[tool.importlinter.contracts]]
name = "L2b: strategy layers (runtime > compiler > catalog > records > declarations)"
type = "layers"
containers = ["odyssey_fx.strategy"]
layers = ["runtime", "compiler", "catalog", "records", "declarations"]
exhaustive = true

[[tool.importlinter.contracts]]
name = "L2c: backtest layers (application > engine > admission|execution|portfolio|trace > domain)"
type = "layers"
containers = ["odyssey_fx.backtest"]
layers = [
    "application",
    "engine",
    "admission | execution | portfolio | trace",
    "domain",
]
exhaustive = true

# (F1) adapters は app と同じパッケージ内の上位層からのみ参照できる
[[tool.importlinter.contracts]]
name = "F1a: marketdata.adapters is not importable outside marketdata and app"
type = "forbidden"
source_modules = [
    "odyssey_fx.common",
    "odyssey_fx.strategy",
    "odyssey_fx.backtest",
    "odyssey_fx.evaluation",
]
forbidden_modules = ["odyssey_fx.marketdata.adapters"]

[[tool.importlinter.contracts]]
name = "F1b: evaluation.adapters is not importable outside evaluation and app"
type = "forbidden"
source_modules = [
    "odyssey_fx.common",
    "odyssey_fx.marketdata",
    "odyssey_fx.strategy",
    "odyssey_fx.backtest",
]
forbidden_modules = ["odyssey_fx.evaluation.adapters"]

# (F2) strategy は marketdata.domain のみ参照できる
[[tool.importlinter.contracts]]
name = "F2: strategy may use marketdata.domain only"
type = "forbidden"
source_modules = ["odyssey_fx.strategy"]
forbidden_modules = ["odyssey_fx.marketdata.application"]

# (F3) backtest は戦略部品カタログを参照しない
[[tool.importlinter.contracts]]
name = "F3: backtest does not depend on the strategy component catalog"
type = "forbidden"
source_modules = ["odyssey_fx.backtest"]
forbidden_modules = ["odyssey_fx.strategy.catalog"]

# (F4) evaluation は backtest の domain / trace のみ参照できる（実行は BacktestRunner ポート経由）
[[tool.importlinter.contracts]]
name = "F4: evaluation may use backtest.domain / trace only"
type = "forbidden"
source_modules = ["odyssey_fx.evaluation"]
forbidden_modules = [
    "odyssey_fx.backtest.engine",
    "odyssey_fx.backtest.admission",
    "odyssey_fx.backtest.execution",
    "odyssey_fx.backtest.portfolio",
    "odyssey_fx.backtest.application",
]

# (F5) 外部ライブラリは adapters と app に限定する
[[tool.importlinter.contracts]]
name = "F5a: no dataframe / serialization libraries outside adapters and app"
type = "forbidden"
source_modules = [
    "odyssey_fx.common",
    "odyssey_fx.marketdata.domain",
    "odyssey_fx.marketdata.application",
    "odyssey_fx.strategy",
    "odyssey_fx.backtest",
    "odyssey_fx.evaluation.domain",
    "odyssey_fx.evaluation.application",
]
forbidden_modules = ["pandas", "polars", "pyarrow", "pydantic", "yaml", "duckdb"]

# (F5c) 設定解析ライブラリは app.config だけ（app 配下の他のすべてのモジュールから不可）
[[tool.importlinter.contracts]]
name = "F5c: config parsers only in app.config"
type = "forbidden"
source_modules = ["odyssey_fx.app"]
forbidden_modules = ["pydantic", "yaml"]
# app.config とその配下だけが設定解析ライブラリを直接 import できる
ignore_imports = [
    "odyssey_fx.app.config -> pydantic",
    "odyssey_fx.app.config.** -> pydantic",
    "odyssey_fx.app.config -> yaml",
    "odyssey_fx.app.config.** -> yaml",
]
# app.cli -> app.config -> yaml の間接経路は正当なので、直接 import だけを禁止する
allow_indirect_imports = true
# app.config が設定解析ライブラリを import するまでは ignore_imports が未使用になるため、エラーにせず警告に留める
unmatched_ignore_imports_alerting = "warn"

[[tool.importlinter.contracts]]
name = "F5b: no numpy outside adapters, app and strategy.catalog"
type = "forbidden"
source_modules = [
    "odyssey_fx.common",
    "odyssey_fx.marketdata.domain",
    "odyssey_fx.marketdata.application",
    "odyssey_fx.strategy.declarations",
    "odyssey_fx.strategy.records",
    "odyssey_fx.strategy.compiler",
    "odyssey_fx.strategy.runtime",
    "odyssey_fx.backtest",
    "odyssey_fx.evaluation.domain",
    "odyssey_fx.evaluation.application",
]
forbidden_modules = ["numpy"]

# (F6) backtest は marketdata.domain のみ参照できる（フィード・執行系列・カレンダーはポート経由）
[[tool.importlinter.contracts]]
name = "F6: backtest may use marketdata.domain only"
type = "forbidden"
source_modules = ["odyssey_fx.backtest"]
forbidden_modules = ["odyssey_fx.marketdata.application"]

# (F7) evaluation は marketdata.domain のみ参照できる（snapshot 取得は SnapshotCatalog ポート経由）
[[tool.importlinter.contracts]]
name = "F7: evaluation may use marketdata.domain only"
type = "forbidden"
source_modules = ["odyssey_fx.evaluation"]
forbidden_modules = ["odyssey_fx.marketdata.application"]

# (F8) evaluation は strategy の declarations / records / compiler のみ参照できる
[[tool.importlinter.contracts]]
name = "F8: evaluation may use strategy.declarations / records / compiler only"
type = "forbidden"
source_modules = ["odyssey_fx.evaluation"]
forbidden_modules = ["odyssey_fx.strategy.runtime", "odyssey_fx.strategy.catalog"]
```

契約の追加・緩和は本書の改訂を伴う（第12節）。`tests/architecture/` には、`lint-imports` の実行に加え、契約定義そのもの（L2c の `|` 区切り、全 layers 契約の `exhaustive = true`、F1a〜F8 の存在）を検査するテストを置く。`lint-imports` は契約が空虚でも成功するためである。

## 7. ディレクトリ構成（確定）

### 7.1 トップレベル

```text
Odyssey-TradingResearch/
├── CLAUDE.md
├── README.md
├── pyproject.toml                # 配布名 odyssey-trading-research、requires-python ">=3.12,<3.13"、hatchling
├── uv.lock
├── .python-version               # 3.12.13
├── .gitignore                    # data/raw、data/snapshots の実体、runs/、.venv 等を除外。snapshot の manifest.json と access_log.jsonl は再包含
├── .github/workflows/ci.yml
├── .claude/skills/pr-review/     # 本リポジトリ用に書き換えたレビュー手順
├── docs/
│   ├── design/                   # 設計文書
│   ├── decisions/                # ADR
│   ├── traces/                   # 紙上トレース（T01）
│   └── pr_review_policy.md       # レビュー方針
├── tools/ops/                    # 運用スクリプト（Codex review poller 等）。パッケージ外
├── src/odyssey_fx/               # 第7.2節
├── configs/                      # 第10節
├── data/                         # 第10節。実体は git 管理外
├── runs/                         # 第10節。git 管理外
└── tests/                        # 第9節
```

### 7.2 `src/odyssey_fx/`

サブパッケージ構成は確定。モジュール名（`.py`）は初期構成であり、後続文書が本書の層規則内で追加・分割できる。

```text
odyssey_fx/
├── __init__.py                   # __version__
├── py.typed
├── common/
│   ├── time.py                   # UtcTime, Interval（半開）, ProcessingPoint
│   ├── ids.py                    # 用途別 ID 型と決定論的採番（ADR-0006）
│   ├── money.py                  # Price, Quantity, Money, CurrencyCode, float→Price 変換
│   ├── symbol.py                 # Symbol, SymbolSpec
│   ├── timeframe.py              # TimeframeRef
│   ├── reason.py                 # ReasonCode, 型付き詳細
│   ├── refs.py                   # PolicyRef, EvidenceRef, ContractRef, ImplementationRef, ConfigDigest 等
│   ├── canonical.py              # 正規化エンコードとダイジェスト（D02 §9）
│   └── errors.py                 # KernelValueError（D02 §10）
├── marketdata/
│   ├── domain/                   # bar, series, calendar, schedule, snapshot, delay, aggregation, integrity
│   ├── application/              # ports, acceptance, aggregation, asof, publication, integrity
│   └── adapters/                 # csv_source, parquet_store
├── strategy/
│   ├── declarations/             # contract, instance, definition, specs, refs, read_spec, missing,
│   │                             # evaluation, entry_policy, state_spec, temporal, datatypes
│   ├── records/                  # records（OutputRecord/Observation）, payloads（役割別）
│   ├── catalog/                  # registry と部品群（features/ conditions/ permissions/ triggers/
│   │                             # filters/ orders/ protection/ exits/）
│   ├── compiler/                 # validate, graph, capability, hashing, compiled
│   └── runtime/                  # ports, evaluator, requests, waiting, supersession, opportunities
├── backtest/
│   ├── domain/                   # orders, fills, reservations, positions, account, events, policies
│   ├── engine/                   # phases, loop, clock, run_end
│   ├── admission/                # request_assembly, risk_assessment, admission
│   ├── execution/                # fill_model, spread, protection_hits, emergency, cost_model
│   ├── portfolio/                # ledger, mtm, conversion
│   ├── trace/                    # recorder, result, manifest
│   └── application/              # run_backtest, ports
├── evaluation/
│   ├── domain/                   # metrics, status, research_policy, experiment, search, splits
│   ├── application/              # evaluate_run, run_experiment, manifest, holdout_gate, ports
│   └── adapters/                 # fs_store, report
└── app/
    ├── composition.py            # 唯一の構成ルート
    ├── config/                   # 設定ファイル → 宣言型（Pydantic v2 と YAML パーサはここだけ）
    └── cli/                      # data accept / strategy compile / backtest run / eval run / experiment run
```

段階−1 ではサブパッケージの `__init__.py`（責務の docstring 付き）だけを作り、モジュールは各設計文書の承認後に追加する。

## 8. 命名・モジュール規約（確定）

- ポートは `Protocol` クラスとし、`ports.py` に集約する。名前は役割名（`MarketDataView`）で、`I` 接頭辞や `Port` 接尾辞は付けない。
- 宣言型・記録型・注文系の型は `@dataclass(frozen=True, slots=True)`。判別可能な union は区分タグ（`kind` フィールド）を持つ dataclass の `Union` で表す。
- ID 型は `NewType` ではなく、用途ごとの frozen dataclass（不透明型）。比較・ハッシュ・文字列化を明示する。
- パッケージ間で参照する型は、提供側の `__init__.py` で再エクスポートしてよい。ただし `adapters` と `application` の型は再エクスポートしない（第3.2節の許可表で不可の参照が `__init__` 経由で通らないようにする）。
- `__all__` を公開モジュールに定義する。
- 例外は各パッケージの domain に基底例外を1つ置き、構造エラー（設計・接続・能力）と実行時失敗（`DATA_ERROR` 等）をサブクラスで分ける。
- モジュール名・関数名は snake_case、型は PascalCase、定数は UPPER_SNAKE。日本語はコメント・docstring に限る。

## 9. テストの配置（確定）

| ディレクトリ | 種別 | 内容 |
|---|---|---|
| `tests/unit/` | 単体 | domain・application の純粋なテスト。パッケージ構成をミラーする |
| `tests/semantics/` | 意味論 | 上位文書 §7.2・§4.7.15E の項目を1件1テストで名前を付けて固定 |
| `tests/property/` | プロパティ | hypothesis による不変条件（先読み不変、SL 単調性、台帳整合、冪等性） |
| `tests/golden/` | golden | 人工データの固定 trace との突合 |
| `tests/integration/` | 統合 | 複数の層を通した経路を確かめる。**内部の関数を直接呼んでも、コマンドを経由してもよい**。紙上トレース T01 の経路がここに入る |
| `tests/acceptance/` | 受入 | **段階の完了条件そのもの**を、人工データを受入れから評価まで1本に通して確かめる |
| `tests/architecture/` | 依存規則 | `lint-imports` の実行と、契約定義そのものの検査（第6節） |
| `tests/fixtures/synthetic/` | 生成器 | DST 境界・週末・欠損・gap・SL/TP 同時到達を含む人工市場データ |

テストは `uv run pytest` で実行し、外部データ・ネットワークに依存しない。実データを使うテストは段階4以降に別マーカーで分ける。

各層の責務・命名・どの層に書くかの判断は [D08](D08_test_strategy.md) が定める。**本表はディレクトリと種別の対応の正本**であり、D08 はこれを参照して責務と命名を足す。

## 10. 設定・データ・成果物の配置（確定）

### 10.1 `configs/`（ADR-0018）

人間が編集する宣言は YAML、機械生成の manifest は JSON。

```text
configs/
├── strategies/     # StrategyDefinition
├── experiments/    # ExperimentSpec
├── policies/       # research / risk / execution / cost（版付き）
├── symbols/        # SymbolSpec
├── calendars/      # 取引カレンダー・時間足定義・公開スケジュール
└── datasources/    # 原データの列対応・時刻規約・宣言する価格基準（D03 §9）
```

YAML の読込条件（`app.config` が強制する）:

- YAML 1.2 相当の安全な読込（任意オブジェクトの構築を行わないローダ）。
- カスタムタグ禁止。
- 重複キーはエラー。
- merge key（`<<`）、anchor/alias の展開など、展開後の内容がレビュー時に分かりにくい機能は禁止。
- 全設定ファイルに `schema_version` 必須。未知の版・未宣言キー・型不一致は拒否。
- Pydantic v2 で検証した後、frozen dataclass（各パッケージの宣言型）へ変換する。Pydantic モデルは `app.config` から外へ出さない。

### 10.2 `data/`（ADR-0013、ADR-0014）

```text
data/
├── raw/market/                     # 移管した20個の CSV。上書き禁止。git 管理外
└── snapshots/<snapshot_id>/
    ├── manifest.json               # git 管理。digest・出所・銘柄・価格基準・期間・行数・変換コード版・
    │                               # partition ごとのアクセス分類
    ├── integrity_report.json       # git 管理。完全性検査の報告（検査種別・系列・区間・重大度・構造的な詳細のみ。
    │                               # 価格統計を含めない）。manifest のダイジェスト対象（D03 v1.7、ADR-0013 改訂）。
    │                               # 確定段階で再実行した snapshot は integrity_report_provisional.json も git 管理
    ├── access_log.jsonl            # git 管理。追記専用の閲覧・消費記録。HoldoutState はここから導出（ADR-0014）
    └── <partition>/…               # 実体。git 管理外。アクセス分類（RESEARCH_HISTORY /
                                    # LEGACY_HOLDOUT / QUARANTINED_UNASSIGNED）ごとに分ける
```

`LEGACY_HOLDOUT` と `QUARANTINED_UNASSIGNED` の partition は、`evaluation.application.holdout_gate` を通らない読込経路を持たない。`marketdata.application.asof` は、渡された partition 集合の外を読めない構造にする（D03 で具体化）。

### 10.3 `runs/`

```text
runs/
├── <run_id>/                       # run manifest、trace、BacktestResult
└── experiments/<experiment_id>/    # 実験 manifest、探索履歴、集計
```

git 管理外。形式は B-7（仮置き: Parquet ＋ JSON）。

## 11. ビルド・CI・品質ゲート（確定）

- ビルドバックエンドは hatchling（ADR-0020）。wheel 対象は `[tool.hatch.build.targets.wheel] packages = ["src/odyssey_fx"]` と明示する。
- 品質ツールは ruff、mypy（strict）、pytest、hypothesis、import-linter（ADR-0019）。
- `.github/workflows/ci.yml` で push と pull_request のたびに次を実行する。すべて成功しなければ merge 不可。

1. `uv sync --frozen`
2. `uv run ruff check .`、`uv run ruff format --check .`
3. `uv run mypy`（strict）
4. `uv run lint-imports`
5. `uv run pytest`

Python は `.python-version`（3.12.13）に固定する。3.13 への更新は依存と golden trace の検証を伴う別変更とする（ADR-0009）。

## 12. 変更手順（確定）

| 変更 | 必要な手続き |
|---|---|
| サブパッケージ内へのモジュール追加 | 該当設計文書の承認。本書の改訂不要 |
| サブパッケージの追加・削除・改名 | 本書第7節の改訂、ADR、該当する layers 契約（exhaustive）の更新 |
| パッケージ間の許可表（第3.2節）の変更 | 本書の改訂と ADR。対応する forbidden 契約を同時に更新 |
| 外部ライブラリの追加 | それを使う設計文書で明記し、第5節と F5 契約を更新 |
| import-linter 契約の緩和 | 本書の改訂と ADR。CI で一時的に無効化しない |

## 13. 段階−1 の実装に対する適合検査項目

段階−1 の PR を本書に照らしてレビューする際の確認項目。

- [ ] `pyproject.toml` の配布名・`requires-python`・`src/` レイアウト・hatchling の wheel 対象が ADR-0005/0009/0020 と一致する
- [ ] `.python-version` が 3.12.13、`uv.lock` が存在し `uv sync --frozen` が通る
- [ ] ランタイム依存がない。dev 依存が第11節のツールに限られる
- [ ] `src/odyssey_fx/` のサブパッケージが第7.2節と一致し、ロジックを含まない
- [ ] `import-linter` 契約が第6節と同一（契約名を含む）で、全 layers 契約が `exhaustive = true`、F1a〜F8 が存在し、`lint-imports` が通る
- [ ] 各 forbidden 契約と L2c の独立性が、違反 import の注入で実際に BROKEN になる（レビュー時に実測）
- [ ] `.gitignore` が `data/raw/`、`data/snapshots/` の実体、`runs/` を除外し、`data/snapshots/*/manifest.json` と `data/snapshots/*/access_log.jsonl` を再包含する（`git check-ignore -v` で確認）
- [ ] `tests/` の構成が第9節と一致し、`tests/architecture/` が `lint-imports` の実行と契約定義の検査を行う
- [ ] CI が第11節の順序で実行される
- [ ] `docs/pr_review_policy.md`、`.claude/skills/pr-review/SKILL.md`、`tools/ops/codex_review_poll.py` が ADR-0017 の構成（旧リポジトリ固有語彙なし、参照先が実在）である
- [ ] 設計文書・データ・CLAUDE.md を変更していない

## 14. 確定した事項と残る未決定

### 14.1 v2 で確定した事項（旧・仮置き）

| 項目 | 確定内容 | ADR |
|---|---|---|
| B-6 設定形式 | 人間編集は YAML（第10.1節の読込条件付き）、機械生成 manifest は JSON | ADR-0018 |
| B-9 品質ツール | ruff、mypy strict、pytest、hypothesis、import-linter | ADR-0019 |
| ビルドバックエンド | hatchling。wheel 対象は `packages = ["src/odyssey_fx"]` | ADR-0020 |
| NumPy | `strategy.catalog` の部品実装内部だけ使用可（第5.1節の条件）。使用許可は確定、具体的に使用する部品は D04/D05 で決定 | ADR-0021 |

### 14.2 残る未決定

| 項目 | 仮置き | 決定時期 |
|---|---|---|
| B-5 表形式ライブラリ | 未定。adapters 限定は確定 | 段階1（D03） |
| B-7 成果物形式 | Parquet ＋ JSON | 段階2（D06/D07） |
| B-8 CLI ライブラリ | argparse | 段階2 |
