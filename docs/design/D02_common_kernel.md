# D02: 共通カーネル型設計（`odyssey_fx.common`）

作成日: 2026-09-19
状態: **承認（2026-09-20）**。v1.8（2026-09-22、PR #22）: 戦略ランタイム設計（D05 v2.0）の要決定 Q10 の決定（市場状態の取引許可を戦略ランタイムが適用する）により、上位設計書 §4.5 の終端理由の説明が広がったため、第8.1節の `MARKET_STATE_INVALIDATED` の説明を同じ PR で揃えた（語彙は増やしていない）。**期間（`timedelta`）の符号化規則を第9.3節へ足す改訂は見送った**（D05 §12.1 の D02 への依頼1）。段階3 の検証戦略 B は本数で数える宣言だけで書けるため受入れが止まらず、符号化規則そのものは正規化の単位と表現をどう決めるかという設計の選択を伴うので、本書の次回改訂へ引き渡したままにする。v1.1（2026-09-20）: 設計文書 PR #3 の Codex 指摘により、正規化ダイジェストの `Decimal` 表現をコンテキスト非依存の厳密表現に修正（第9.3節）。ユーザーの条件4点（①不変型と `IdAllocator` の例外、②`RunId` は完全入力の論理識別、③`CodeDigest` の曖昧でない定義、④`PhaseRank` の一意性）を反映済み。v1.2（2026-09-20）: 段階1の `common` 実装 PR #6 で判明した2点をユーザー決定により確定（第3.3節: フェーズ集合型 `PhaseSet` を `common` に置き順位順に正規化する。第8.2節: `RiskRejectionDetail` の `limit` / `observed` は型を一致させる）。併せて実装で定めた `TimeframeRef` の文字列形式を第6節に、Codex 指摘で確定した Decimal 文脈の扱いと刻み丸めの厳密化を第4.1節に記録。v1.3（2026-09-20）: 理由コードの表（第8.1節）が上位設計書 §4.7.14 の写しであることを明記し、ADR-0031・ADR-0032・ADR-0033 で §4.7.14 に加わった取引機会の終端理由と評価要求の追い越し（`MARKET_STATE_INVALIDATED` / `SUPERSEDED` / `CLOSED_BY_ORDER_ACCEPTANCE` / `CONCURRENCY_LIMIT_REACHED` / `REQUEST_SUPERSEDED`）を表へ反映した（D04 の PR #14）。`common` の列挙への追加は段階2 の実装で行う。v1.4（2026-09-21）: D05 の要決定 Q3・Q4 に対する人間の決定（PR #15）により、取引機会の終端理由に `ORDER_ATTEMPT_REJECTED`（自身の発注試行が受付前の審査で拒否された）と `FULFILLED_BY_ORDER_ACCEPTANCE`（自身の注文が受け付けられて役目を終えた）が上位設計書 §4.7.14 へ加わったため、第8.1節の表に反映した（ADR-0032 補足4）。`common` の列挙への追加は引き続き段階2 の実装で行う。v1.5（2026-09-21）: D06 の要決定 Q1・Q6 に対する人間の決定（PR #16）を反映した。Q1（選択肢1）により、処理段階の名前の規則（第3.3節の `PhaseRank.name`）を `^[A-Z_]+$` から `^[A-Z][A-Z0-9_]*$` へ緩め、上位設計書 §4.3.12 の P0〜P5 の呼び方をそのままフェーズ名に使えるようにした。Q6（選択肢1）により、保護水準の置き方が不正であることによる受付前拒否の理由コード `PROTECTION_INVALID` が上位設計書 §4.7.14 へ加わったため、第8.1節の表に反映した。ADR-0016 条件1（D01〜D03）のうち D02 を充足。 v1.6（2026-09-21、PR #18）: 段階2 の戦略基盤の実装で判明した2点を人間の決定により確定した。第9.2節に **`ImplementationRef.digest` の算出規則**（宣言した実装識別子と改訂番号の正規化ダイジェスト）を追記し、第9.3節に **期間（`timedelta`）は正規化エンコードの対象外**であること（期間を含む宣言はダイジェストを計算できない）と、符号化規則を足すかどうかを将来の改訂へ引き渡すことを明記した。 v1.7（2026-09-22、PR #19）: 段階2 のバックテスト基盤の実装で判明した3点を人間の決定により確定した。第7.1節の ID の一覧に **口座の識別子（`AccountId`）** を加えた（設定が与える名前であり、採番しない）。第8.1節の理由コードの表に **`SUPERSEDED_BY_EXIT`**（同じ判断時点の決済要求に押しのけられた保護水準の更新の破棄。D06 §8.3）を加えた（同じ PR で上位設計書 §4.7.14 も改訂した）。第9.3節に、**判断履歴の列に書く期間（`timedelta`）は秒数の十進文字列**であり、それは表示の書式であってダイジェスト用の正規化エンコードではないことを明記した（正規化エンコードが期間を拒否する規則は v1.6 のまま変えない）。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §4.7.15「共通の値型と参照」、§4.3.15、§4.7.14、[D01](D01_architecture_and_dependency_rules.md) §1・§2・§5・§7.2、ADR-0006（決定論的 ID）、ADR-0011（frozen dataclass）、ADR-0012（Decimal / float 境界）
対応段階: 段階1で実装。以降の全パッケージが依存する。

## 0. 本書の位置付け

`odyssey_fx.common` に置く値型・参照型・理由コードの構造と規則を確定する。本書は「型の形」と「不変条件」を決め、業務規則（SL の丸め方向、予約計算式など）は決めない。それらは各パッケージの domain（D04〜D07）に置く。

凡例は全体計画書第0節に従う。本書の「確定」は承認後に正本となる。「案」は本書で推奨を示し、承認時に確定または差し戻す。

## 1. 設計原則（確定）

1. **標準ライブラリのみ**。`decimal`、`datetime`、`zoneinfo`、`dataclasses`、`enum`、`hashlib`、`json`、`re` 以外に依存しない（D01 §5）。
2. **不変**。すべての値型・参照型・記録型は `@dataclass(frozen=True, slots=True)`。コレクションは `tuple` か `Mapping` の凍結ビューで持つ。**唯一の例外は `IdAllocator`（第7.3節）**で、これは値ではなく実行コンテキストに属する採番器であり、run ごとに1つ生成し、記録・ダイジェストの対象に含めない。`common` にこれ以外の可変オブジェクトを置かない。
3. **構築時検証**。各型は `__post_init__` で不変条件を検査し、違反は `KernelValueError`（構造エラー）を送出する。検証を通った値は以後常に有効である。
4. **決定論**。実時計・乱数・環境変数を読まない。ID の採番は明示的な採番器を通す（第7節）。
5. **明示的な同値・順序・文字列化**。比較・ハッシュ・`__str__` の意味を型ごとに定義し、暗黙の型混在（`Price` と `Decimal` の比較など）は `TypeError` にする。
6. **数値の境界**。`Decimal` は文字列または整数からのみ構築する。`Decimal(float)` は禁止し、float からの変換は第4.6節の専用関数だけが行う（ADR-0012）。
7. **シリアライズは外側**。JSON / YAML / Parquet への変換は `app` と adapters の責務。カーネルは正規化文字列表現（`__str__` と `parse`）と、ダイジェスト用の正規化エンコード（第9節）だけを提供する。

## 2. モジュール構成（確定）

D01 §7.2 の7モジュールに、ダイジェスト用の `canonical.py` と例外の `errors.py` を加える（サブパッケージ内へのモジュール追加。D01 改訂不要、次回改訂で §7.2 の一覧に追記）。

| モジュール | 内容 |
|---|---|
| `time.py` | `UtcTime`、`Interval`、`PhaseRank`、`PhaseSet`、`ProcessingPoint` |
| `ids.py` | 用途別 ID 型、`RunId`、`IdAllocator` |
| `money.py` | `CurrencyCode`、`Price`、`PriceOffset`、`Quantity`、`Money`、`ConversionRate`、丸め、float 変換 |
| `symbol.py` | `Symbol`、`SymbolSpec`、`SymbolSpecRef` |
| `timeframe.py` | `TimeframeRef` |
| `reason.py` | `ReasonCode`、`Reason`、型付き詳細、`MissingInputReason` |
| `refs.py` | `ContentDigest`、`ContractRef`、`ImplementationRef`、`PolicyRef`、`StrategyRef`、`CompiledStrategyRef`、`ConfigDigest`、`CodeDigest`、`LockDigest`、`SnapshotRef`、`EvidenceRef` |
| `canonical.py` | 正規化エンコードとダイジェスト |
| `errors.py` | `KernelValueError`（基底例外） |

## 3. 時刻（`time.py`）

### 3.1 `UtcTime`（案: 専用型で包む）

`datetime` をそのまま使うと naive な値や別タイムゾーンの値が混入しうる。専用型で包み、構築時に UTC を強制する。

| フィールド | 型 | 不変条件 |
|---|---|---|
| `value` | `datetime` | `tzinfo` が UTC（`utcoffset() == 0` かつ tz 名が UTC）。マイクロ秒精度 |

- 比較・ハッシュは `value` に従う。`UtcTime + timedelta -> UtcTime`、`UtcTime - UtcTime -> timedelta`。
- `__str__` は `YYYY-MM-DDTHH:MM:SSZ`（マイクロ秒が 0 でなければ `.ffffff` を付ける）。`UtcTime.parse(str)` はこの形式と `+00:00` 形式を受け、それ以外（naive、他オフセット）は拒否する。
- `UtcTime.from_local(naive: datetime, tz: ZoneInfo, *, fold: int | None)` を提供し、DST の曖昧・不存在時刻は明示的に指定させる（曖昧なのに `fold` 未指定、または不存在時刻なら `KernelValueError`）。カレンダー計算（D03）はこれを使う。

代替案は bare `datetime` ＋ 検証関数。型で防げないため採用しない（要承認）。

### 3.2 `Interval`（確定）

半開区間 `[start, end)`。

| フィールド | 型 | 不変条件 |
|---|---|---|
| `start` | `UtcTime` | — |
| `end` | `UtcTime` | `start < end`（空区間は不可） |

- `contains(t)`: `start <= t < end`。`overlaps(other)`、`duration -> timedelta`、`adjacent_to(other)`。
- 足の対象区間、観測区間、待機記録の対象区間に使う。

### 3.3 `PhaseRank` と `ProcessingPoint`（確定）

上位設計書 §4.7.15 の `ProcessingPoint(time, phase, sequence)`。フェーズの具体的な一覧は `backtest.engine` が定義し（D06）、`common` は「順位を持つ段階」という構造だけを持つ。

| 型 | フィールド | 不変条件 |
|---|---|---|
| `PhaseRank` | `rank: int`、`name: str` | `rank >= 0`。`name` は `^[A-Z][A-Z0-9_]*$`（v1.5。先頭は英大文字、2文字目以降は英大文字・数字・下線） |
| `ProcessingPoint` | `time: UtcTime`、`phase: PhaseRank`、`sequence: int` | `sequence >= 0` |

- **名前に数字を許す（v1.5、2026-09-21 の D06 の要決定 Q1 の決定）**: 正規表現を `^[A-Z_]+$` から `^[A-Z][A-Z0-9_]*$` へ緩めた。上位設計書 §4.3.12 が確定した P0〜P5 という段階の呼び方を、D05 §6.1 と D06 §4.1 が `P1_FEATURE`〜`P5_ORDER_INTENT` というフェーズ名でそのまま使うためである。改訂前の規則では数字を含む名前を構築時に拒否してしまい、ランタイムが `PhaseSet.by_name("P1_FEATURE")` でフェーズ順位を引けなかった。先頭を英大文字に限るのは、数字始まりの名前を許さないためである。
- 全順序は `(time, phase.rank, sequence)`。同じ `time` でも phase と sequence で区別する。
- **一意性**: run 内で使うフェーズの集合は `backtest.engine` が固定の tuple として定義し、`rank` と `name` はそれぞれ集合内で一意（rank ↔ name は全単射）。同じ `rank` に異なる `name`、同じ `name` に異なる `rank`、および同一要素の重複を含む集合は構築時に拒否する。`ProcessingPoint` の全順序はこの一意性を前提とし、フェーズ集合の定義は run manifest に記録する。
- **`PhaseSet`（v1.2、確定）**: 上記の一意性検査は `common` の `PhaseSet(phases: tuple[PhaseRank, ...])` が構築時に行う（各パッケージでの再実装を防ぐ）。`PhaseSet` は内部のフェーズ列を **`rank` の昇順に正規化して保持**し、入力の並び順によって同値性・ハッシュ・正規化エンコード（第9.3節）・manifest の記録内容が変わらない。`by_name(name)` / `by_rank(rank)` で引け、未登録は `KernelValueError`。空集合は拒否する。具体的なフェーズ一覧の定義は引き続き `backtest.engine`（D06）が行い、`common` は構造と検査だけを持つ。
- `ProcessingPoint` は「エンジンがいつ処理したか」であり、市場で起きた時刻ではない。市場時刻は `Interval` / `UtcTime` で別に持つ。

## 4. 金額・価格・数量（`money.py`）

### 4.1 Decimal コンテキスト（確定）

- `KERNEL_DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)`。トラップは `InvalidOperation`、`DivisionByZero`、`Overflow`。`Inexact` はトラップしない。
- カーネルの算術は `with localcontext(kernel_context())` の中で行う（v1.2: `kernel_context()` は上記設定から**毎回新しい `Context` を作って返す**関数。公開定数 `KERNEL_DECIMAL_CONTEXT` は参照用で算術には使わない。可変な `Context` オブジェクトを書き換えられても計算結果が変わらないようにするため。PR #6 の Codex 指摘）。プロセス全体のコンテキスト（`decimal.getcontext()`）は変更しない。
- 刻みへの丸め（`round_to_tick`、`round_down_to_step`、`Money.round_to`）はコンテキストの精度に依存しない**厳密な整数演算**で行う（v1.2: `value / step` を精度 28 で先に丸めると、有効桁が 28 を超える値で丸め方向が失われるため。PR #6 の Codex 指摘）。
- `NaN`、`Infinity` は構築時に拒否する。

### 4.2 `CurrencyCode`（確定）

`code: str`。`^[A-Z]{3}$`。`__str__` は `code`。

### 4.3 `Price` と `PriceOffset`（案）

| 型 | フィールド | 不変条件 | 用途 |
|---|---|---|---|
| `Price` | `value: Decimal` | 有限、`> 0` | 提示価格、約定価格、SL/TP 水準 |
| `PriceOffset` | `value: Decimal` | 有限（符号任意） | 価格差、許容不利約定幅 Δ、spread |

- 演算: `Price - Price -> PriceOffset`、`Price ± PriceOffset -> Price`（結果が `<= 0` なら `KernelValueError`）、`PriceOffset * Decimal -> PriceOffset`、`PriceOffset` 同士の加減。`Price + Price` は定義しない。
- 比較は同型同士のみ。`Price < Decimal` は `TypeError`。
- `Price.round_to_tick(tick: Decimal, direction: RoundingDirection) -> Price`。`RoundingDirection = DOWN | UP | NEAREST_HALF_EVEN`。SL は買いなら DOWN、売りなら UP といった**方向の選択**は D06 の責務であり、本型は機構だけを提供する。
- `Price.is_on_tick(tick) -> bool`。

`Price` と `PriceOffset` を分ける理由: 「価格」と「価格差」を同じ型にすると、`SL + entry` のような無意味な演算を型で防げない。単一型案は採用しない（要承認）。

### 4.4 `Quantity`（確定）

`units: Decimal`。有限、`> 0`。基軸通貨の単位数（上位設計書 §4.7.15）。「建玉なし」は数量 0 ではなく値の不在で表す。

- `Quantity.round_down_to_step(step: Decimal) -> Quantity | None`（ステップ未満になれば `None`。切り上げはしない）。
- 数量と価格差の積などの金額計算は `money.py` の関数で行い、結果は決済通貨建ての `Money` にする。

### 4.5 `Money` と `ConversionRate`（確定）

| フィールド | 型 | 不変条件 |
|---|---|---|
| `amount` | `Decimal` | 有限。符号任意 |
| `currency` | `CurrencyCode` | — |

- `Money ± Money` は同一通貨のみ。異なる通貨は `KernelValueError`。`Money * Decimal`、`Money / Decimal`。
- 比較は同一通貨のみ。
- `Money.round_to(step: Decimal, direction)`。
- 通貨換算は `Money` の責務ではない。`ConversionRate(from_currency, to_currency, rate: Decimal, observed_at: UtcTime, evidence: EvidenceRef | None)` と `convert(money, rate) -> Money` を提供し、換算率の取得は D03/D06 が担う。

### 4.6 float からの変換（確定、ADR-0012）

Feature 計算（float）の結果を注文価格・水準に移すときだけ使う。

```text
price_from_float(raw: float, *, tick: Decimal, direction: RoundingDirection) -> FloatConversion
```

| `FloatConversion` のフィールド | 意味 |
|---|---|
| `raw: float` | 丸め前の float |
| `exact: Decimal` | `Decimal(repr(raw))`。float の最短往復表現を文字列経由で Decimal 化した値 |
| `tick: Decimal`、`direction` | 適用した丸め規則 |
| `result: Price` | 丸め後の価格 |

- `raw` が `nan` / `inf` なら `KernelValueError`。
- `FloatConversion` は根拠記録（`EvidenceRef` の対象）として保存できる形にする。
- `src/` 内で `Decimal(` を float 引数で呼ぶことを防ぐため、`tests/architecture/` に「`Decimal(` の呼び出しが `common/money.py` と `common/canonical.py` 以外の `src/` に現れない」ことを検査するテストを置く。他モジュールは `money.decimal_from_str(s)` / `decimal_from_int(i)` を使う。

## 5. 銘柄（`symbol.py`）

### 5.1 `Symbol`（確定）

`code: str`。`^[A-Z]{6}$`（区切りなし表記、上位設計書 §3.1）。`base -> CurrencyCode`（先頭3文字）、`quote -> CurrencyCode`（末尾3文字）。

### 5.2 `SymbolSpec`（確定。フィールドは案）

| フィールド | 型 | 不変条件・意味 |
|---|---|---|
| `symbol` | `Symbol` | — |
| `version` | `int` | `>= 1` |
| `price_tick` | `Decimal` | `> 0`。価格刻み（例: USDJPY 0.001） |
| `pip_size` | `Decimal` | `> 0`、`price_tick` の整数倍（例: USDJPY 0.01） |
| `quantity_step` | `Decimal` | `> 0`。数量刻み（例: 1000） |
| `min_quantity` | `Decimal` | `>= quantity_step`、`quantity_step` の整数倍 |
| `lot_size` | `Decimal \| None` | 表示・換算用（例: 100000）。計算の正本にはしない |

- `SymbolSpecRef(symbol, version, digest: ContentDigest)` で版と内容を固定する。銘柄仕様の実体は `configs/symbols/` に置き、`app.config` が読み込む。
- spread、許容不利約定幅、費用は銘柄仕様ではなく実行ポリシー（D06）に置く。

## 6. 時間足参照（`timeframe.py`）（確定）

`TimeframeRef(id: str, version: int)`。`id` は `^[a-z0-9_]+$`（例: `15m`、`1h`、`4h_ny17`、`1d_ny17`）、`version >= 1`。長さ・整列・セッション規則を持つ定義本体は `marketdata.domain`（D03）に置き、`common` は参照だけを持つ。固定 enum にしない（上位設計書 §4.3.9）。

`__str__` / `parse` の形式（v1.2、実装で確定）: `"<id>@v<version>"`（例: `1h@v1`）。第9.3節の正規化エンコードはこの文字列を使うため、形式の変更はダイジェストの変更を意味する。

## 7. ID（`ids.py`）（確定、ADR-0006）

### 7.1 ID 型の一覧

| 型 | 種別コード | 用途 | 採番の所有者 |
|---|---|---|---|
| `RunId` | — | **完全入力（設定・コード・lock・環境）の論理識別**。`digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`（ADR-0006、2026-09-20 改訂）。物理的な1回の実行を指す ID ではなく、同じ完全入力なら何度実行しても同じ値 | `backtest.trace.manifest`（D06） |
| `SnapshotId` | — | snapshot manifest のダイジェスト | `marketdata`（D03） |
| `ExperimentId` | — | 実験 spec のダイジェスト | `evaluation`（D07） |
| `AccountId` | — | **口座の識別**。設定が与える名前であり採番しない（`^[A-Za-z0-9_-]{1,32}$`）。口座仕様（`AccountSpec`）と台帳・建玉・注文要求が持つ | 設定（`app.config`、D06 §9.3 の入力の群） |
| `EvaluationId` | `EVAL` | 部品の1回の評価 | 戦略ランタイム |
| `RequestId` | `REQ` | 評価要求（待機・追い越しの単位） | 戦略ランタイム |
| `OutputId` | `OUT` | `OutputRecord` | 戦略ランタイム |
| `OpportunityId` | `OPP` | 取引機会 | 戦略ランタイム |
| `AttemptId` | `ATT` | 発注試行 | `backtest.admission` |
| `OrderId` | `ORD` | 受付済み注文 | `backtest.admission` |
| `FillId` | `FIL` | 約定 | `backtest.execution` |
| `PositionId` | `POS` | 建玉 | `backtest.portfolio` |
| `ReservationId` | `RSV` | リスク予約 | `backtest.admission` |
| `AllocationId` | `ALC` | 建玉リスク割当 | `backtest.portfolio` |
| `EventId` | `EVT` | 状態遷移イベント。再配送でも同じ値 | `backtest.engine` |
| `EvidenceId` | `EVD` | 根拠記録 | `backtest.trace` |

### 7.2 構造

- ダイジェスト系（`RunId`、`SnapshotId`、`ExperimentId`）: `digest: ContentDigest`。`__str__` は 16進 64 文字。短縮表示（先頭 12 文字）は表示用関数で行い、識別には使わない。
- `RunId` の入力は `ConfigDigest`（解決済み run 設定）、`CodeDigest`（実行したソースコードの内容）、`LockDigest`（`uv.lock` の内容）、`EnvDigest`（実行環境）の4つ（第9.2節・第9.4節）。同一の完全入力による再実行は同じ `RunId` になり、既存成果物を無条件に上書きしない（衝突時の扱いは D06/D07）。git commit・dirty 状態・Python バージョンは manifest に記録し、識別子には含めない。
- `RunAttemptId` は初版では持たない。再実行履歴の保存が必要になった場合に追加する。
- 連番系（表の種別コードを持つもの）: `seq: int`（`>= 1`）。`__str__` は `f"{KIND}:{seq:08d}"`（例: `ORD:00000042`）。型ごとに `KIND` をクラス定数として持ち、`parse` は種別が一致しない文字列を拒否する。
- 連番系の ID は **run 内で一意**。永続参照は `(RunId, ID)` の組（上位設計書 §4.7.15）。各記録は `run_id` を別フィールドで持つ。
- 内容ハッシュだけをイベント ID にしない（同じ内容の異なるイベントを区別できなくなる）。

### 7.3 `IdAllocator`（確定）

```text
IdAllocator(run_id: RunId)
  .next(OrderId) -> OrderId   # 種別ごとに 1 から単調増加
  .snapshot() -> Mapping[str, int]   # 各種別の最終値。run manifest と trace に保存
```

- 可変オブジェクトだが実行コンテキストの一部であり、`backtest.engine` が1 run に1つ生成して各層へ渡す。domain の値型には含めない。
- 採番順は処理順（`ProcessingPoint` の順）に一致させる。同一入力の再実行で同じ ID 列になることを再現性テストで検証する。

## 8. 理由コード（`reason.py`）

### 8.1 `ReasonCode`（確定。語彙は初期版）

**本節の表は上位設計書 §4.7.14 の語彙の写しである**（v1.3 で明記）。正本は §4.7.14 であり、語彙を足す決定をした設計文書・ADR は、**同じ PR で §4.7.14 と本節の表の両方を更新する**。片方だけ更新すると、決定した理由を実装側の列挙で構築できなくなる。

上位設計書 §4.7.14 の初期語彙に `POSITION_CLOSED`（§4.7.15 の提案）を加える。

| コード | 主な使用箇所 |
|---|---|
| `RISK` | 受付前拒否（率・総量・数量・証拠金の違反） |
| `NO_CANDIDATE` | 期限内に適格 open がない受付前拒否 |
| `RUN_END` | 末尾の受付前拒否、残存注文の CANCELED、管理要求の未適用 |
| `DATA_ERROR` | 完全性検査・実行失敗、失敗に伴う残存注文の CANCELED |
| `EXPIRED` | 期限による注文の EXPIRED、期限切れによる取引機会の終端（上位設計書 §4.5） |
| `CARRY_NOT_ALLOWED` | 週末持ち越し禁止による受付前拒否 |
| `POSITION_CLOSED` | 閉鎖済み建玉への要求の拒否、保護決済後の残存決済注文の取消 |
| `MARKET_STATE_INVALIDATED` | 市場状態によって無効になったことによる取引機会の終端（v1.3、ADR-0031。v1.8 で説明を広げた: 継続成立を要求した条件が崩れた場合と、生成時点で市場状態が取引を許していなかった場合を含む。上位設計書 §4.5・D05 §7.6） |
| `SUPERSEDED` | 新しい Trigger を優先する設定による取引機会の終端（v1.3、ADR-0032） |
| `CLOSED_BY_ORDER_ACCEPTANCE` | 別の注文が受け付けられたことによる取引機会の終端（v1.3、ADR-0032） |
| `CONCURRENCY_LIMIT_REACHED` | 同時保持上限に達していたことによる取引機会の終端（v1.3、ADR-0032 補足3。発火は取引機会として記録したうえで有効にしない） |
| `ORDER_ATTEMPT_REJECTED` | 自身の発注試行が受付前の審査で拒否されたことによる取引機会の終端（v1.4、ADR-0032 補足4） |
| `FULFILLED_BY_ORDER_ACCEPTANCE` | 自身の注文が受け付けられたことによる取引機会の終端（v1.4、ADR-0032 補足4）。**他の**機会が終わる `CLOSED_BY_ORDER_ACCEPTANCE` と混同しない |
| `REQUEST_SUPERSEDED` | 同じ系列の新しい足による評価要求の追い越し（v1.3、ADR-0033 で改名。取引機会の `SUPERSEDED` と混同しない） |
| `PROTECTION_INVALID` | 保護水準の置き方が宣言として不正であることによる受付前拒否（v1.5、D06 の Q6 決定）。買いの損切りが判断時の売却側価格以上、売りの損切りが購入側価格以下、不正数値、必要な価格情報の不足（上位設計書 §4.7.9 B）。口座のリスク上限の違反である `RISK` とは原因も対処も異なる |
| `SUPERSEDED_BY_EXIT` | 同じ判断時点の同じ建玉への決済要求が優先されたことによる、保護水準の更新の破棄（v1.7、D06 §8.3 の決定）。取引機会が終端する `SUPERSEDED` とは対象も結果も異なる。使用箇所は表12（`MANAGEMENT_APPLICATIONS`）の「適用しなかった要求」に限る |

`MARKET_STATE_INVALIDATED` から `REQUEST_SUPERSEDED` までの7件は、2026-09-20 の ADR-0031・ADR-0032・ADR-0033 と 2026-09-21 の ADR-0032 補足4 で上位設計書 §4.7.14 に加わった語彙であり、v1.3 と v1.4 で本節の表へ反映した。取引機会の終端理由の意味の正本は上位設計書 §4.5、評価要求の追い越しの正本は §4.3.14 である。`common` の `ReasonCode` 列挙への追加は、取引機会の状態機械を実装する段階2（D04・D05）で行う。取引機会の状態機械そのもの（非終端の状態名と全遷移）は D05 §7 が正本である。

`PROTECTION_INVALID` は 2026-09-21 の D06 の要決定 Q6 の決定で上位設計書 §4.7.14 に加わった語であり、v1.5 で本節の表へ反映した（同じ PR で §4.7.14 も改訂した）。損切りの向きの違反は戦略の宣言の誤りであり、口座のリスク上限の違反（`RISK`）と集計上分けられるようにするための語である。使用箇所は D06 §5.2・§6.4 の受付前拒否に限る。`common` の `ReasonCode` 列挙への追加は段階2の実装で行う。

語彙の追加（執行理由など）は該当設計文書（D06）で行い、本節の表を更新する。「状態と理由は別フィールド」「許可された組合せの検証は各 domain」（上位設計書 §4.7.14）。

### 8.2 `Reason` と型付き詳細（確定。詳細型の一覧は初期版）

```text
Reason(code: ReasonCode, detail: ReasonDetail | None)
```

`ReasonDetail` は `Protocol`（`code: ClassVar[ReasonCode]` を持つ frozen dataclass）。`Reason` は `detail.code == code` を検証する。`common` が定義する詳細型は、`common` の型だけで表現できるものに限る。

| 詳細型 | フィールド | 対応コード |
|---|---|---|
| `DataErrorDetail` | `symbol`、`timeframe: TimeframeRef`、`field: str`、`expected_interval: Interval \| None`、`observed_interval: Interval \| None`、`cause: str` | `DATA_ERROR` |
| `ExpiryDetail` | `expires_at: UtcTime`、`observed_at: ProcessingPoint` | `EXPIRED` |
| `NoCandidateDetail` | `expires_at: UtcTime`、`earliest_candidate: UtcTime \| None` | `NO_CANDIDATE` |
| `RunEndDetail` | `run_end: UtcTime` | `RUN_END` |
| `CarryNotAllowedDetail` | `next_candidate: UtcTime`、`session_close: UtcTime` | `CARRY_NOT_ALLOWED` |
| `PositionClosedDetail` | `position_id: PositionId`、`closed_at: ProcessingPoint` | `POSITION_CLOSED` |
| `RiskRejectionDetail` | `check: str`、`limit: Money \| Decimal`、`observed: Money \| Decimal` | `RISK`。`check` の語彙は D06。**`limit` と `observed` は型を一致させ（両方 `Money` か両方 `Decimal`）、`Money` 同士なら通貨も一致させる**（v1.2、確定。比較不能な記録を構築時に拒否する）。`Decimal` 同士の意味（比率・件数など）の検査は D06 が行う |

### 8.3 入力不足の診断コード（確定）

上位設計書 §4.3.10 の4分類を `MissingInputReason` として定義する。`ReasonCode` とは別の enum（受付拒否ではなく評価見送りの理由）。

`WARMUP_INSUFFICIENT`、`INPUT_MISSING_OR_INVALID`、`LATEST_BAR_UNAVAILABLE`、`MAX_AGE_EXCEEDED`。使用箇所は戦略ランタイムの評価記録（D04/D05）と市場データビュー（D03）。

## 9. 参照型とダイジェスト（`refs.py`、`canonical.py`）

### 9.1 `ContentDigest`（確定）

`algorithm: str`（初版は `"sha256"` のみ許可）、`hex: str`（64 文字小文字16進）。

### 9.2 参照型（確定）

| 型 | フィールド | 意味 |
|---|---|---|
| `ContractRef` | `component_id: str`、`version: int`、`digest` | 部品契約の固定参照（上位設計書 §4.3.5） |
| `ImplementationRef` | `implementation_id: str`、`digest` | 登録済み実装の ID と内容ハッシュ |
| `PolicyRef` | `policy_kind: str`（`research` / `risk` / `execution` / `cost` / `delay` 等）、`policy_id: str`、`version: int`、`digest` | ポリシーの不変参照。run manifest から解決する |
| `StrategyRef` | `strategy_id: str`、`version: int`、`digest` | `StrategyDefinition` の参照 |
| `CompiledStrategyRef` | `digest` | 解決済み設定（探索で作る割当を含む）の識別 |
| `ConfigDigest` | `digest` | 解決済み run 設定の正規化内容のダイジェスト。項目は D06 |
| `CodeDigest` | `digest` | 実行したソースコードの内容ダイジェスト。定義は第9.4節 |
| `LockDigest` | `digest` | `uv.lock` の内容の sha256 |
| `EnvDigest` | `digest` | 実行環境のダイジェスト。定義は第9.4節 |
| `SnapshotRef` | `snapshot_id: SnapshotId` | データ snapshot の参照 |
| `EvidenceRef` | `evidence_id: EvidenceId` | 保存された根拠記録への参照 |

**`ImplementationRef.digest` の算出（確定。v1.6、2026-09-21 の人間の決定。PR #18）**。部品の実装1件を指す指紋は、**宣言した実装識別子（`implementation_id`）と改訂番号（`revision`）の2項目だけ**を対象に、第9.3節の `canonical.digest({"implementation_id": …, "revision": …})` で作る。

- 実装のソース内容そのものを読まないのは、部品を登録する `strategy.catalog` が I/O を持てない（D01 §5）ためである。run 全体のソース内容は `CodeDigest`（第9.4節）が別に識別しており、実装ごとの指紋の役目は**解決済み設定の指紋（D05 §5.5 の3）に部品単位の改訂を映すこと**に限られる。
- したがって `revision` は**実装の計算規則を変えたら必ず上げる**という運用上の約束と対になる。上げ忘れると、計算規則が変わったのに解決済み設定の指紋が同じままになる。同じ約束を D05 §5.5 にも置く。
- **不採用**: 実装コードのソース内容を読んでハッシュする案（`catalog` に I/O が要り、D01 §5 の依存規則に反する）、実装ごとの指紋を持たず `CodeDigest` に任せる案（部品単位で実装差を検出できず、D04 §13.2 の切り分けが成立しない）。

### 9.3 正規化エンコードとダイジェスト（確定。細部は案）

`canonical.encode(obj) -> bytes` と `canonical.digest(obj) -> ContentDigest`（sha256）。

エンコード規則:

- JSON 互換のテキスト。キーは Unicode コードポイント順に整列、空白なし、`ensure_ascii=False`、UTF-8。
- `Decimal`: **Decimal コンテキストに依存しない厳密表現**。`normalize()` はコンテキストの精度で丸めるため使わない（精度 28 では `123456789012345678901234567890` と `…7900` が同じ表現になり、別の値が同じダイジェストになる）。代わりに `as_tuple()` の `(sign, digits, exponent)` から末尾のゼロ桁だけを取り除いて指数を調整し、その組を**展開せずに**文字列 `"<sign><digits>e<exponent>"`（例: `150.00` → `"15e1"`、`-0.5` → `"-5e-1"`、`0` → `"0e0"`。`-0` は `0` に正規化）として符号化する。指数を桁に展開しないため、`Decimal("1E+1000000000")` のような巨大な指数でもメモリを消費しない。桁数・指数に上限を設けず、いかなるコンテキストでも同じ値は同じ表現、異なる値は異なる表現になる。`150.00` と `150` は同じ値として同じ表現になる。人間向けの表示形式（固定小数）は別途 `__str__` が担い、ダイジェストには使わない。
- `float`: 有限値のみ、`repr` の最短往復表現。`nan` / `inf` は拒否。
- `UtcTime`: 第3.1節の文字列。`Interval`: `{"start":…, "end":…}`。ID 型・`Symbol`・`CurrencyCode`・`TimeframeRef`: `__str__`。
- `Enum`: `value`。dataclass: フィールド名をキーとする mapping（型名は含めない。型はスキーマ側で決まる）。`tuple` / `list`: 配列。`Mapping`: オブジェクト。`set` は拒否（順序が定まらない）。
- `None`: `null`。それ以外の型は `KernelValueError`。
- **期間（`timedelta`）は符号化しない（確定。v1.6、2026-09-21 の人間の決定。PR #18）**。上の一覧に `timedelta` は無く、「それ以外の型」として `KernelValueError` になる。したがって**期間値を含む宣言はダイジェストを計算できない**。D04 §13.1 が定めた設定ファイル上の期間の書き方（`<正の整数><単位>` の文字列）はあるが、それは読み込みの書式であり、正規化エンコードの規則ではない。両者を暗黙に同一視すると、`90m` と `1h30m` のように同じ期間を指す別表記を、正規化の規則を決めないままダイジェストへ通すことになる。**将来の改訂への引き渡し**: 期間の符号化規則（正規化の単位と表現）を足すかどうかは本書の次回改訂で決める。足すまでの間、期間値を持つ宣言（`InputReadSpec.max_age`・`DurationWindow`・`DurationDeadline`）は指紋の対象にできない（D04 §13.2 の注記、D05 §5.5）。段階2の検証戦略 A はいずれも期間値を持たないため、実行経路には現れない。

**判断履歴の列に書く期間は、秒数の十進文字列とする（確定。v1.7、2026-09-22 の人間の決定。PR #19）**。これは**表示の書式**であって、上の正規化エンコードの規則ではない。ダイジェストは引き続き期間を拒否する。段階2で判断履歴に期間が現れるのは換算経路の観測時点のずれ（`ConversionPath.skew`）だけで、D06 §9.1 の平坦化がこの書式で1列に書く。二重定義を避けるため、列の書式の正本は D06 §9.1、ダイジェストの正本は本節とする。

`ConfigDigest`、`SnapshotId`、`ExperimentId`、`CompiledStrategyRef` はこのダイジェストで作る。`RunId` は `{"config": <ConfigDigest.hex>, "code": <CodeDigest.hex>, "lock": <LockDigest.hex>, "env": <EnvDigest.hex>}` の mapping のダイジェストとする。

### 9.4 `CodeDigest` と `LockDigest` の算出（確定）

`CodeDigest` は「実際に import されたパッケージのソース内容」を曖昧なく識別する。算出は `app` が run 開始時に行い、`common` は型と算出関数だけを持つ。

- 対象ディレクトリ: `odyssey_fx.__file__` が解決するパッケージディレクトリ（editable install では `src/odyssey_fx/`）。git の作業ツリーやリポジトリのパスからは決めない。
- 対象ファイル: そのディレクトリ配下の拡張子 `.py` のファイルすべて。`__pycache__`、`.pyc`、`py.typed`、テスト、`tools/`、`docs/` は含めない。
- 順序: パッケージディレクトリからの相対パス（POSIX 区切り）を Unicode コードポイント順に整列。
- 入力: 各ファイルについて `path_bytes + b"\0" + str(len(content)).encode() + b"\0" + content_bytes` を順に sha256 へ投入する。改行コードや空白の正規化は行わない（内容がそのまま識別対象）。
- 出力: 16進 64 文字。
- git commit・dirty 状態は算出に使わず、manifest に別途記録する。dirty な作業ツリーでも `CodeDigest` は実際の内容を識別する。

`LockDigest` はリポジトリ直下 `uv.lock` の内容バイト列の sha256。`uv.lock` が存在しない、または `pyproject.toml` と整合しない（`uv lock --check` 相当が失敗する）場合は run を開始しない。

`EnvDigest` は次の mapping の `canonical.digest`: `python_implementation`（`platform.python_implementation()`）、`python_version`（`sys.version_info` の完全な版、例 `3.12.13`）、`sys_platform`（`sys.platform`）、`machine`（`platform.machine()`）、`distributions`（`importlib.metadata.distributions()` から得た「名前==版」を名前の正規化形（小文字、`-`/`_`/`.` を `-` に統一）で整列した列。`odyssey_fx` 自身は `CodeDigest` が識別するため除外）。同じ `uv.lock` でも環境が違えば別の wheel が選ばれ数値結果が変わりうるため識別子に含める。入力の各項目は run manifest にも記録する。同じ名前・版で異なるビルドの wheel、libc、CPU 機能差は識別しない（ADR-0006 の残余リスク）。これらは golden trace と再現性テストで検出し、manifest に `platform.platform()` と `platform.libc_ver()` を参考記録する。ダイジェスト対象の構造（何を含めるか）は各設計文書（D03/D04/D06/D07）が決める。

## 10. 例外（`errors.py`）（確定）

- `KernelValueError(ValueError)`: 値型の不変条件違反。構造エラーであり、`MissingInputPolicy` の対象ではない。
- 各パッケージの domain はこれを継承せず、自パッケージの基底例外を持つ（D01 §8）。カーネルの例外が上位で捕捉されるのは、設定読込（`app.config`）と契約検証（`strategy.compiler`）の境界に限る。

## 11. テスト（確定）

| 種別 | 内容 |
|---|---|
| 単体 | 各型の不変条件（拒否される値）、演算の型規則、`__str__` / `parse` の往復、`round_to_tick` の方向、`Money` の通貨不一致、`from_local` の DST 曖昧・不存在時刻、`PhaseSet` の一意性違反の拒否と並び順に依らない同値性、`RiskRejectionDetail` の型・通貨不一致の拒否 |
| プロパティ | `ProcessingPoint` の全順序性、`canonical.digest` がキー順序に依存しないこと、`Decimal` 表現の正規化（`150.00` と `150`）と厳密性（精度 28 を超える桁数の異なる2値が異なる表現になり、コンテキスト精度を変えても表現が変わらないこと、巨大な指数の値でも展開しないこと）、`price_from_float` の `exact` が `Decimal(repr(x))` と一致すること、`IdAllocator` の再現性 |
| アーキテクチャ | `Decimal(` の呼び出し位置の制限（第4.6節）、`common` が標準ライブラリ以外を import しないこと（D01 F5a/F5b で機械検査済み） |

## 12. 段階1での実装範囲

本書の全モジュールを実装する。`ConversionRate` の取得元、`RiskRejectionDetail.check` の語彙、`ReasonCode` の追加語彙は D03/D06 の承認時に追加する。

## 13. 承認時の確認事項（2026-09-20 承認: 全項目で推奨を採用）

| # | 事項 | 推奨 | 代替 |
|---|---|---|---|
| 1 | `UtcTime` を専用型で包む | 包む（naive / 他 TZ の混入を型で防ぐ） | bare `datetime` ＋ 検証関数 |
| 2 | `Price` と `PriceOffset` を分ける | 分ける（無意味な演算を型で防ぐ） | 単一の `Price` 型 |
| 3 | 連番 ID の文字列形式 `KIND:00000008` | 採用（種別が見え、run 内で一意） | 種別なしの整数 |
| 8（2026-09-20 追加） | `EnvDigest` を `RunId` に含める | 採用（ADR-0006 再改訂） | manifest 記録のみ |
| 4 | `canonical` で float を `repr` で許可 | 許可（`ParameterValue` に float がある） | float を拒否し、パラメータは Decimal に限定 |
| 5 | Decimal 精度 28・`ROUND_HALF_EVEN` を既定コンテキストに | 採用 | 精度を上げる |
| 6 | `ReasonDetail` を `Protocol` で開放し、各 domain が詳細型を追加できる | 採用 | 詳細型を `common` に集約 |
| 7 | `canonical.py` と `errors.py` の追加（D01 §7.2 のモジュール一覧に含まれない） | 追加（サブパッケージ内のモジュール追加なので D01 改訂不要。D01 §7.2 の一覧には次回改訂で追記） | — |
