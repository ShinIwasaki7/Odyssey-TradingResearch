# 決定記録（ADR）

全体計画書第6節の要決定事項に対する決定を、1件1ファイルで記録する。形式は `NNNN-<slug>.md`、状態は 提案 / 承認 / 廃止。運用形式は ADR-0023 で確定した。

| ADR | 題名 | 計画書 |
|---|---|---|
| [0001](0001-five-package-split.md) | パッケージ分割は5つとする | A-1 |
| [0002](0002-context-first-layout.md) | パッケージ内はコンテキスト優先で層化する | A-2 |
| [0003](0003-strategy-runtime-ownership.md) | 戦略ランタイムは strategy に置く | A-3 |
| [0004](0004-import-linter-in-ci.md) | 依存規則は import-linter で CI 必須検査にする | A-4 |
| [0005](0005-src-layout-and-package-name.md) | src レイアウトと odyssey_fx を採用する | A-5 |
| [0006](0006-deterministic-ids.md) | ID は決定論的に生成する | A-6 |
| [0007](0007-event-driven-reference-engine.md) | 単一スレッドのイベント駆動参照実装を先に作る | A-7 |
| [0008](0008-pure-function-components.md) | 部品実装は純粋関数＋明示状態とする | A-8 |
| [0009](0009-python-312.md) | Python は 3.12 系に固定する | B-1 |
| [0010](0010-uv.md) | パッケージ管理は uv とする | B-2 |
| [0011](0011-frozen-dataclass-domain.md) | domain は frozen dataclass、設定境界だけ Pydantic v2 | B-3 |
| [0012](0012-decimal-float-boundary.md) | 台帳系は Decimal、Feature は float | B-4 |
| [0013](0013-data-layout-and-gitignore.md) | データは data/raw/market へ移し実体を git 管理外にする | C-1 |
| [0014](0014-period-access-classification.md) | 期間はアクセス状態で三分類する | C-2 |
| [0015](0015-initial-vertical-slice-scope.md) | 初版の縦断実行範囲は USDJPY・JPY 口座・判断 1h・執行 15m | C-3 |
| [0016](0016-implementation-start-conditions.md) | 実装開始は最小縦断の契約確定を条件とする | D-2 |
| [0017](0017-codex-review-tooling.md) | pr-review スキルを書き換え、最小 poller を新規作成する | D-3 |
| [0018](0018-config-format-yaml-json.md) | 設定は YAML、機械生成 manifest は JSON | B-6 |
| [0019](0019-quality-tools.md) | 品質ツールは ruff / mypy strict / pytest / hypothesis / import-linter | B-9 |
| [0020](0020-hatchling-build-backend.md) | ビルドバックエンドは hatchling | D01 §14 |
| [0021](0021-numpy-in-strategy-catalog.md) | NumPy は strategy.catalog の部品実装内部に限り使用可 | D01 §14 |
| [0022](0022-design-doc-approval-unit.md) | 設計文書は1文書ずつ承認する | D-1 |
| [0023](0023-adr-format.md) | ADR は1決定1ファイルで、必須項目と廃止規則を持つ | D-4 |
| [0024](0024-no-legacy-aggregation-reproduction.md) | 上位足の集約は新規則のみとし、旧集約の再現版は作らない | C-4 |
| [0025](0025-polars-in-adapters.md) | adapters の表形式ライブラリは polars | B-5 |
