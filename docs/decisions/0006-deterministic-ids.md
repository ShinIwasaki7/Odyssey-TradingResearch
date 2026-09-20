# ADR-0006: ID は決定論的に生成する

- 状態: 承認（2026-09-18。2026-09-20 に RunId の構成を改訂、同日 `EnvDigest` を追加）
- 決定者: ユーザー
- 関連: [全体計画書](../design/fx_research_platform_overall_plan.md) 第6節 A-6、[D02](../design/D02_common_kernel.md) §7・§9、PR #2 の Codex 指摘（RunId の名前空間衝突）

## 文脈

同一入力の再実行で trace が一致することを検証するため、UUID4 は使えない。

当初の RunId は「解決済み run 設定・snapshot・戦略・ポリシー・seed の正規化内容のダイジェスト」だった。この定義では、設定が同じままエンジン・台帳・執行のコードだけが変わった再実行が同じ RunId になり、`runs/<run_id>/` の成果物と run 内 ID 空間が前回と衝突する。manifest にコード版を記録しても、衝突は識別子が決まった後にしか検出できない（PR #2 Codex 指摘、P1）。

## 決定

### 識別子の構成（2026-09-20 改訂）

- `ConfigDigest`: 解決済み run 設定の正規化内容のダイジェスト。snapshot 参照、コンパイル済み戦略、各ポリシー参照、実行区間、遅延シナリオ、執行系列、seed を含む（項目の正本は D06）。設定だけの同一性を表し、コード版をまたいだ比較のグループ化に使う。
- `CodeDigest`: 実際に実行したソースコードの内容ダイジェスト（`src/odyssey_fx/` 配下の全ソースを正規化して結合したもの。算出規則は D02 §9）。
- `LockDigest`: `uv.lock` の内容ダイジェスト。
- `EnvDigest`: 実際に実行したランタイム環境のダイジェスト。Python の実装名と完全な版、`sys.platform`、`platform.machine()`、実際にインストールされた配布物の「名前==版」を整列した一覧から作る。`uv.lock` はプラットフォーム非依存で、同じ lock でも OS・CPU が違えば別の wheel が選ばれ数値結果が変わりうるため、識別子に含める（PR #2 Codex round 3 指摘）。
- `RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`。
- プラットフォームをまたいだ比較・グループ化には `ConfigDigest` と `CodeDigest` を使う。

### manifest に記録する環境情報

git commit、dirty 状態（未コミット変更の有無）、`EnvDigest` の入力（Python 版・プラットフォーム・配布物一覧）、`ConfigDigest` / `CodeDigest` / `LockDigest` / `EnvDigest` を run manifest に記録する。git 情報は識別子には含めない（dirty な作業ツリーでも `CodeDigest` が内容を識別する）。

### 再実行の扱い

- 同一の完全入力（設定・コード・lock・環境）による再実行は同じ `RunId` として扱う。
- `runs/<run_id>/` が既に存在する場合、既存成果物を無条件に上書きしない。既定は失敗（fail-closed）とし、置換は明示的な指示（CLI フラグ。名称は D06/D07 で決める）でのみ行う。置換時も旧成果物の manifest を記録に残す。
- 個々の再実行履歴を保存する必要が生じた場合のみ `RunAttemptId` を追加する。初版では持たない。

### run 内の ID（変更なし）

- run 内の各 ID: `RunId` ＋ 種別 ＋ 決定論的連番。
- 内容ハッシュだけをイベント ID にしない（同じ内容の異なるイベントを区別できなくなる）。
- 再配送される同一通知は、最初に発行された同じ `EventId` を使う。

## 影響

- `common.ids` の `RunId` と `common.refs` の `ConfigDigest` / `CodeDigest` / `LockDigest` / `EnvDigest` は D02 で確定する。`RunAttemptId` は D02 から外す。
- run manifest の項目（D06）に環境情報を追加する。
- 成果物ディレクトリの衝突検査と置換フラグは D06/D07 の CLI 設計に含める。

## 改訂履歴

| 日付 | 内容 |
|---|---|
| 2026-09-18 | 初版承認 |
| 2026-09-20 | RunId を `ConfigDigest` ＋ `CodeDigest` ＋ `LockDigest` のダイジェストに改訂。環境情報の manifest 記録、再実行時の上書き禁止、`RunAttemptId` の初版除外を追加 |
| 2026-09-20 | `EnvDigest`（インタプリタ・プラットフォーム・インストール済み配布物）を RunId に追加 |
