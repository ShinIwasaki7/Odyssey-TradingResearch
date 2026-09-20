# ADR-0026: 設定解析ライブラリの禁止範囲は `app` 配下全体（`app.config` を除く）

- 状態: 承認（2026-09-20）
- 決定者: ユーザー
- 関連: [D01](../design/D01_architecture_and_dependency_rules.md) §5・§6（契約 F5c）・§12、[ADR-0011](0011-frozen-dataclass-domain.md)、[ADR-0018](0018-config-format-yaml-json.md)

## 文脈

設定解析ライブラリ（YAML パーサと Pydantic）を `app.config` に集約するルールは ADR-0011 / ADR-0018 で決まっている。`app` の外側は契約 F5a が検査するが、`app` の内側を検査する契約 F5c（D01 v2.1）は禁止元を `app.cli` と `app.composition` の列挙で書いていた。骨格 PR #4 の Codex レビューで、`app/__init__.py` や将来 `app` 直下に追加するモジュールが YAML / Pydantic を直接 import しても検出されないことが指摘された。列挙方式では、モジュールを足すたびに契約を更新しない限り検査に穴が開く。

## 決定

- 契約 F5c の禁止元を `odyssey_fx.app` 全体にする。`app.config` とその配下からの import だけを `ignore_imports` で許可する。
- 間接経路（`app.cli → app.config → yaml`）は引き続き正当なので、`allow_indirect_imports = true` は維持する。
- `app.config` が設定解析ライブラリを import するまでは `ignore_imports` が未使用になるため、`unmatched_ignore_imports_alerting = "warn"` とし、エラーにしない。段階1以降で `app.config` が実装されれば警告は消える。
- 契約名 `F5c: config parsers only in app.config` は変更しない。

## 影響

- `app` 配下に新しいモジュールを追加しても、契約を更新せずに設定解析ライブラリの直接 import が検出される（PR #4 の指摘を恒久的に解消）。
- `tests/architecture/test_contract_definitions.py` の F5c 定義検査を新しい形（source が `odyssey_fx.app` のみ、`ignore_imports` が `app.config` 系4件、`allow_indirect_imports = true`）に合わせる。
- D01 §6 の契約本文を v2.3 に改訂する。依存規則の強化であり、許可表（D01 §3.2）は変わらない。
