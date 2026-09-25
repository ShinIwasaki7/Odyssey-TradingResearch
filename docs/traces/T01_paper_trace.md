# T01: 紙上トレース（検証戦略 A・B）

作成日: 2026-09-21
状態: **承認（2026-09-21、PR #16）**。v1.5（2026-09-25、PR #40）: 設計文書の必須表（R1（PR #26 承認）。全体計画書 §8.5）に合わせ、**経路ごとに通る状態機械のマスの対応表**を第15節に加えた。紙上トレースは状態機械を定めないので、状態×出来事表と値の伝播表の代わりにこの対応表を置き、値の伝播の実例は第10節を指す。**追跡した経路・数値・追えない範囲の一覧は1つも変えていない**。v1.4（2026-09-23、PR #22）: 段階3 の戦略ランタイム設計を「**D05 v0.2**」、その段階3 の記録を受け取るバックテスト縦断設計を「**D06 v0.3**」と呼んでいた箇所を、実際の版番号（**D05 v2.0**・**D06 v1.5**。どちらも PR #22）へ揃えた（第11.2節・第12節・第14節。D05 §14）。**呼び方だけの整理であり、追跡した経路・数値・追えない範囲の一覧は1つも変えていない**。v1.3（2026-09-22、PR #20）: 本書が追う run の**日付を 2026 年1月から 2015 年1月へ改めた**（人間の決定）。2026 年以降は未分類の隔離期間であり、いかなる経路でも読めない（D03 §3.8、ADR-0014）ため、承認済み snapshot を経由する通しの検証をこの期間では構造的に行えなかった。曜日の並びと冬時間の条件が同じ 2015 年へずらしてあり（`2015-01-04` は日曜、`2015-01-16` は金曜。どちらの年も1月は米国東部標準時）、run 区間の長さ（12 日 = 1,036,800 秒）も週の開閉の位置も変わらない。**価格・数量・損益・資産は日付に依存しないので、本書の数値は1つも変えていない**。段階2 の受入テストの人工データはこの日付で作られている。v1.2（2026-09-21、PR #18）: 段階2 の戦略基盤の実装に合わせ、第2.2節の**取引機会の遷移記録の通し番号**をD05 §6.6（v1.3）が確定した規則どおりの値へ直した（`4` → `2`、`7` → `6`）。起草時の値は規則が未記述のまま置いた例示であり、規則（`step` 内で 0 から始まる1本の番号を、出力記録と遷移記録が発生順に共有する）に当てはめると別の値になる。**経路1〜8 の追跡内容と、他のすべての値は変えていない**。第14節の後続版の表で v1.2 としていた計画は v1.3 へ繰り下げた。v1.1（2026-09-21、PR #17）: D06 の Q13（run 中の含み損益の評価に使う価格の出どころ）が決定したため、本書が `…` のままにしていた台帳 snapshot の `equity` を確定させた（第2.4節）。あわせて、D07 の最大ドローダウン（含み損益込み）を検算できるように、`equity` の全値と、そのために人工データへ置く2つの設定を第9.4節に足した。第14節の v1.2（D07 への引き渡し）のうち、評価価格と `equity` に関する部分だけを前倒しした改訂であり、**経路1〜8 の追跡内容そのものは変えていない**。v1.0（2026-09-21）: D06 の要決定 Q1〜Q11 の決定をすべて反映した設計で、検証戦略 A の8経路と検証戦略 B を、D03 → D04 → D05 → D06 の型とフィールド名で1判断時点ずつ追った。本書が差し戻した Q10・Q11 は決定済みであり、本書が阻害要因として挙げた「発注の根拠になった出力の識別子」（第13節 #19）は D05 v1.2 の改訂で解消した。置き場所は D01 §7.1 のディレクトリ構成に従い `docs/traces/` である。ADR-0016 条件3（「T01 で、戦略定義から注文・約定・単一評価まで紙上で通す」）と、全体計画書 §8.2 の段階0 完了条件（「2本の検証戦略を紙上で追跡でき、同時刻・数量・末尾処理に暗黙の前提がない」）を満たすことが目的である。
上位文書: [上位設計書](../design/fx_research_platform_greenfield_design.md) §4.3.12・§4.5・§4.7・§7.1、[全体計画書](../design/fx_research_platform_overall_plan.md) §7・§8.1・§8.2、[D03](../design/D03_marketdata_and_time.md)、[D04](../design/D04_strategy_declarations.md)、[D05](../design/D05_strategy_runtime.md)、[D06](../design/D06_backtest_vertical_slice.md)、ADR-0015・ADR-0016・ADR-0027・ADR-0029・ADR-0030・ADR-0031・ADR-0032
対応段階: 段階0 の完了条件。段階2 の意味論テストと golden trace の下敷きにする。

## 0. 本書の位置付けと読み方

紙上トレースとは、**実装を書く前に、1つの判断時点で何がどの型のどのフィールドに入るかを人間が手で追う作業**である。型やフィールドが決まっていない箇所では手が止まるため、設計文書の未記述を機械的に洗い出せる。本書は「追えたこと」と「追えなかったこと」の両方を残す。

- 追えなかった箇所は第13節に**未記述の一覧**としてまとめ、D06 側での対処（D06 第17節）と対応させる。
- 数値は人工データの値である。実データでの値ではなく、**手計算で検算できること**だけを目的にしている（全体計画 §8.2 の段階2 完了条件「人工データで注文・数量・損益・資産が手計算に一致」）。
- 本書は設計を新たに決めない。判断が割れる点は D06 第16節の要決定として差し戻した（Q10・Q11。いずれも 2026-09-21 に決定済み）。

## 1. 共通の前提

### 1.1 データと実行設定

| 項目 | 値 | 出どころ |
|---|---|---|
| 銘柄 | `USDJPY` | ADR-0015 |
| 評価系列 | `USDJPY/1h/bid`（`SeriesId`、D03 §3.1） | D05 §9 |
| 執行系列 | `USDJPY/15m/bid`（`RunConfig.execution_series`） | ADR-0015・D06 §3 |
| カレンダー | `fx_ny17` v1（週開 日17:00 NY、週閉 金17:00 NY。冬時間では 22:00Z） | D03 §3.4 |
| run 区間 | `Interval[2015-01-04T22:00Z, 2015-01-16T22:00Z)`（`RunConfig.run_interval`） | D06 §9.3 |
| 銘柄仕様 | `price_tick=0.001` / `pip_size=0.01` / `quantity_step=1000` / `min_quantity=1000` | D02 §5.2 |
| 口座 | `AccountSpec(account_id=ACC1, currency=JPY, initial_balance=Money(1000000, JPY))` | D06 §8.1 |

`RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`（ADR-0006、D06 §9.3）。以下では `RunId` を `run-A` と書く。

### 1.2 ポリシー

| ポリシー | 値 | 出どころ |
|---|---|---|
| `RiskPolicy` | `trial_risk_rate=0.02` / `account_risk_cap=0.20` | 上位 §4.7.10 |
| `ExecutionPolicy` | `entry_delay_bars=0` / `adverse_fill_limits={USDJPY: PriceOffset(0.05)}` / `entry_valid_for=20分` / `close_valid_for=20分` / `resolution_hierarchy=ResolutionHierarchy((USDJPY/15m/bid,))` / `reference_quote_source=EXECUTION_SERIES_LAST_CLOSE` | D06 §7.1.1（Q3・Q4・Q5・Q8・Q10） |
| `CostModel` | `commission_per_unit=Money(0.001, JPY)`（片道・1通貨あたり）/ `entry_slippage=PriceOffset(0.01)` / `close_slippage=PriceOffset(0.01)` / `spread_model=FixedSpread(PriceOffset(0.02))` / `swap_modeled=False` | D06 §7.2・§7.6・ADR-0029 |
| `ConversionPolicy` | `pivot_currency=USD` / `max_observation_skew=15分` | D06 §8.5.1（Q7・Q9） |
| 遅延シナリオ | 経路4 以外は遅延なし | D03 §7・D06 §9.3 |

`ask = bid + 0.02` とする（D06 §7.2）。決済通貨（JPY）と口座通貨（JPY）が一致するため、換算は率1の恒等換算であり `ConversionPath` は `legs=()`・`rate=1`・`skew=0` の経路になる（D06 §8.5.1 の規則1）。恒等換算に参照する市場系列は無いため、`ConversionLeg` を1本も作らない。

### 1.3 戦略の宣言と使用箇所

D04 §14・D05 §9 のとおり。`market_state=None`、`execution_filter=None`、`entry_policy=ImmediateEntry`、`opportunity_validity.bindings=()`、`opportunity_concurrency=OpportunityConcurrencySpec(max_active=1, on_new_trigger=KEEP_EXISTING, on_order_accepted=KEEP_OTHERS)`。

| 使用箇所 | 契約 | 起動条件 | 出力 |
|---|---|---|---|
| `breakout_level` | `extreme_price` v1（`mode=MAX`、`lookback=20`） | `OnBarClose("h1", USDJPY/1h/bid)` | `level: price@v1` |
| `stop_level` | `extreme_price` v1（`mode=MIN`、`lookback=20`） | 同上 | `level: price@v1` |
| `entry_trigger` | `breakout_trigger` v1（`direction=LONG`） | 同上 | `opportunity: opportunity@v1` |
| `entry_order` | `market_order_intent` v1 | `OnInputEvent("opp", "opportunity")` | `intent: order_intent@v1` |
| `initial_stop` | `level_stop_loss` v1 | 同上 | `protection: protection_levels@v1` |
| `take_profit` | `fixed_rr_take_profit` v1（`reward_risk=2.0`） | `OnRuntimeEvent("filled", POSITION_OPENED)` | `action: management_action@v1` |

評価順は `breakout_level` → `stop_level` → `entry_trigger` → `entry_order` → `initial_stop` → `take_profit`（D05 §5.4・§9）。

### 1.4 1つの判断時点の骨格

D06 §4.1 の15フェーズ（rank 0〜14）を、判断時刻 T = **2015-01-06T09:00Z**（火曜）で例示する。この時刻には次のイベントが同時に発生する（D03 §7.1）。

| イベント | 値 | 受け取る側 |
|---|---|---|
| `ExecutionBarComplete(USDJPY/15m/bid, BarKey(…, 08:45Z))` | — | rank 0（執行モデル） |
| `Publication(USDJPY/1h/bid, BarKey(…, 08:00Z), available_at=09:00Z)` | — | rank 3 → `PublicationBatch.available_bars` |
| `Publication(USDJPY/15m/bid, BarKey(…, 08:45Z), available_at=09:00Z)` | — | 同上 |
| `ScheduledBoundary(USDJPY/1h/bid, BarKey(…, 08:00Z), bar_end=09:00Z)` | — | rank 3 → `scheduled_closes` |
| `ScheduledBoundary(USDJPY/15m/bid, BarKey(…, 08:45Z), bar_end=09:00Z)` | — | 同上 |
| `ExecutionOpen(USDJPY/15m/bid, BarKey(…, 09:00Z), open_time=09:00Z)` | — | rank 11（執行モデル） |

`available_bars` と `scheduled_closes` には**両方の系列**が入るが、宣言している起動条件は `OnBarClose("h1", USDJPY/1h/bid)` だけなので、評価要求は1件だけ生まれる（D05 §6.2 の手順1・2）。15分足の確定は起動条件に結び付いていないため評価を起こさない。対象区間が異なるため集約もされない（D05 §6.2、Q6 決定）。**この点は追えた**（暗黙の前提なし）。

## 2. 経路1: 正常エントリー → 利確

### 2.1 人工データ（抜粋）

| 系列 | 足 | 値 |
|---|---|---|
| `USDJPY/1h/bid` | 直前20本（`[…, 08:00Z)`、当該足を除く） | `HIGH` の最大 = 150.000、`LOW` の最小 = 149.500 |
| `USDJPY/1h/bid` | `BarKey(…, 08:00Z)`、`interval=[08:00,09:00)` | `close=150.040`、`available_at=09:00Z` |
| `USDJPY/15m/bid` | `BarKey(…, 08:45Z)`、`interval=[08:45,09:00)` | `close=150.040`、`available_at=09:00Z`（**この経路では1時間足の終値と同じ値に作ってある**。初版データでは15分足と1時間足が別々の入力ファイルであり、1時間足を15分足から集約していない（D03 §2・§5.1）ため、一致は構造的な保証ではなく人工データの設定である。食い違う場合は経路4） |
| `USDJPY/15m/bid` | `BarKey(…, 09:00Z)`、`interval=[09:00,09:15)` | `open=150.050`、`high=150.120`、`low=150.020`、`close=150.100` |
| `USDJPY/15m/bid` | `BarKey(…, 11:00Z)`、`interval=[11:00,11:15)` | `high=151.300`、`low=151.000` |
| `USDJPY/15m/bid` | 建玉 P1 を保有している間（`[09:00,09:15)` から `[11:00,11:15)` まで）の各足 | **終値はいずれも 150.040 以上**（v1.1 の追記）。個々の足の終値までは決めず、**保有中の最安の評価価格が入場直後の snapshot になる**ことだけを人工データの設定として固定する。これにより第9.4節の `equity` の最小値が1点に定まる |

### 2.2 T = 2015-01-06T09:00Z の1判断時点

| rank | フェーズ | 何が起きるか | どの型のどのフィールドに何が入るか |
|---|---|---|---|
| 0 | `EXECUTION_BAR_COMPLETE` | 建玉なし。到達判定の対象なし | 記録なし |
| 1 | `LEDGER_UPDATE` | 含み損益の再評価と台帳 snapshot | `LedgerSnapshot(at=ProcessingPoint(09:00Z, LEDGER_UPDATE, 0), balance=Money(1000000,JPY), equity=Money(1000000,JPY), consumed=Money(0,JPY), open_position_ids=())` → 表14 |
| 2 | `ORDER_EXPIRY` | `PENDING` 注文なし | 記録なし |
| 3 | `PUBLICATION` | 公開イベントを `PublicationBatch` へ変換（D06 §4.3） | `PublicationBatch(batch_id=EventId 00000001, decision_time=09:00Z, phases=BACKTEST_PHASES, available_bars=(1h 08:00Z, 15m 08:45Z), scheduled_closes=(BarClosure(1h 08:00Z, Interval[08:00,09:00)), BarClosure(15m 08:45Z, Interval[08:45,09:00))), runtime_events=(), admissions=(), is_run_end=False)` |
| 4 | `OPPORTUNITY_LIFECYCLE` | 有効な機会なし。`bindings=()` のため再検査なし | 記録なし（D05 §7.3） |
| 5 | `P1_FEATURE` | `breakout_level` と `stop_level` を評価 | `EvaluationRecord(request_id=RequestId 00000001, evaluation_id=EvaluationId 00000001, instance_id="breakout_level", trigger_names=("h1",), decision_time=09:00Z, target_interval=Interval[08:00,09:00), opportunity_id=None, position_id=None, outcome=Evaluated((OutputId 00000001,)))` → 表2。出力は `OutputRecord(output_id=00000001, evaluation_id=00000001, producer=("breakout_level","level"), payload=Price(150.000), decision_time=09:00Z, available_at=09:00Z, sequence=0)` → 表1。`stop_level` も同様に `Price(149.500)`（`EvaluationId 00000002` / `OutputId 00000002`） |
| 6 | `P2_MARKET_STATE` | 宣言なし | 記録なし |
| 7 | `P3_TRIGGER` | `entry_trigger` が `150.040 > 150.000` で発火。直前状態 `ConditionState(False)` から成立へ変わったため `EDGE` が通る | 部品は `OpportunityContent(direction=LONG, reference_values={"breakout_level": Price(150.000)})` を返す。ランタイムが `Opportunity(opportunity_id=OpportunityId 00000001, symbol=USDJPY, signal_interval=Interval[08:00,09:00), direction=LONG, reference_values={...})` を組み立てる（D05 §4.2）。有効な機会数 0 < `max_active`=1 → 遷移1。`OpportunityTransition(opportunity_id=00000001, from_state=None, to_state=OPEN, at=ProcessingPoint(09:00Z, P3_TRIGGER, 2), phase=P3_TRIGGER, reason=None, counterpart=None, attempt_id=None)` → 表3。`OutputRecord(output_id=00000003, producer=("entry_trigger","opportunity"), payload=Opportunity(…))` → 表1。新しい状態 `ConditionState(True)` |
| 8 | `P4_CONFIRMATION` | 宣言なし | 記録なし |
| 9 | `P5_ORDER_INTENT` | `entry_order` と `initial_stop` が同じ機会の配送で起動 | `OutputRecord(output_id=00000004, producer=("entry_order","intent"), payload=OrderIntent(symbol=USDJPY, direction=LONG, order_type=MARKET, price_condition=None, expiry=None))` と `OutputRecord(output_id=00000005, producer=("initial_stop","protection"), payload=ProtectionLevels(stop_loss=Price(149.500), take_profit=None))` → 表1。役割出力が揃い `EntryProposal(opportunity_id=00000001, order_intent=…, protection=…, decision_time=09:00Z, intent_output_id=OutputId 00000004, protection_output_id=OutputId 00000005)` を1件返す（根拠の出力 ID は D05 v1.2 で必須。第2.3節でそのまま `OrderRequest` へ渡る）。遷移4: `OPEN → ORDER_PENDING`（`at=ProcessingPoint(09:00Z, P5_ORDER_INTENT, 6)`）→ 表3 |
| 10 | `ADMISSION` | 要求組立 → 全順序化 → 審査 → 予約 → 受付（D06 §6） | 第2.3節 |
| 11 | `EXECUTION_OPEN` | `[09:00,09:15)` の始値で約定 | 第2.4節 |
| 12 | `POST_FILL_EVALUATION` | 第2回の `step`。受付通知と `POSITION_OPENED` | 第2.5節 |
| 13 | `POST_FILL_ADMISSION` | 全数量決済の要求なし | 記録なし |

**未記述だった点（1）**: rank 5 の `EvaluationRecord` までは D05 の型で埋まるが、**rank 10 で使う参照価格をどの系列のどの足から取るか**が D06 に書かれていなかった。執行系列の最新確定足（`15m 08:45Z`、`close=150.040`）とするか、評価系列の最新確定足（`1h 08:00Z`、`close=150.040`）とするかで、値が一致するのは「同じ時刻に両方が確定する」段階2 の構成だからにすぎない。→ D06 §6.4 の手順3 に規則を足し、判断が割れる点を Q10 として差し戻した（**選択肢1 で決定済み**: 直前に完了した執行足の終値）。

### 2.3 rank 10（`ADMISSION`）の中身

`AdmissionKey = (decision_time=09:00Z, request_class=ENTRY(1), strategy_priority=0, origin_seq=opportunity_id.seq=1, attempt_seq)` の昇順（D06 §6.3）。要求は1件なので順序は自明。並べてから `AttemptId 00000001` を採番する。

**この表の2つのフィールドは、当初 D05 の改訂が済むまで埋められなかった**（本書が見つけた阻害要因、第13節 #19）。`EntryRequest.intent_output_id` と `InitialProtectionPlan.source_output_id` の値は `EntryProposal` がどの出力から作られたかを指すが、改訂前の D05 §3 の `EntryProposal` は `opportunity_id` / `order_intent` / `protection` / `decision_time` の4項目しか持たず、エンジンはこの2つを受け取れなかった。保存済みの出力記録（表1）から後で推測しても、同じ判断時点に同じ役割の出力が複数出た場合に一意に決まらない（D06 §6.1）。**D05 §3・§6.2（v1.2）で `EntryProposal.intent_output_id` / `protection_output_id` と `ManagementRequest.source_output_id` を足して解消済み**であり、下表の `OutputId 00000004` / `00000005` はその改訂後の値である。

| 手順 | 値 | 型とフィールド |
|---|---|---|
| 要求組立 | — | `OrderRequest(run_id=run-A, attempt_id=00000001, previous_attempt_id=None, account_id=ACC1, strategy_id=S1, created_at=ProcessingPoint(09:00Z, ADMISSION, 0), origin=STRATEGY, payload=EntryRequest(opportunity_id=00000001, symbol=USDJPY, side=BUY, order_type=MARKET, protection=InitialProtectionPlan(stop_loss=Price(149.500), take_profit=None, source_output_id=OutputId 00000005), exit_plan_ref=ExitPlanRef(compiled_ref=CS1, exit_instance_id="take_profit"), valid_for=20分, intent_output_id=OutputId 00000004), evidence_ref=EvidenceRef(EvidenceId 00000001))` → 表4 |
| 1. balance | `B = Money(1000000, JPY)` | `AdmissionBudget.balance` |
| 2. 予算 | `trial_budget = 20000`、`U = 0`、`account_remaining = 200000`、`admission_budget = min(20000, 200000) = 20000` | `AdmissionBudget` の各項目 |
| 3. 参照価格 | 執行系列の最新確定足 `15m 08:45Z` の `close=150.040`（bid）→ 買いは ask なので `150.040 + 0.020 = 150.060` | `ReferenceQuote(price=Price(150.060), basis=ASK, observed_at=09:00Z, source_bar=BarKey(USDJPY/15m/bid, 08:45Z), derived_from_spread=True)` |
| 4. 保護水準の妥当性 | 買いの損切り `149.500 < 判断時 bid 150.040` → 合格 | `RiskCheckResult(check="protection_direction", passed=True, limit=Decimal("150.040"), observed=Decimal("149.500"))` |
| 5. 丸めと予約式 | `S = 149.500`（`price_tick=0.001` に整列済み、買いは `DOWN`）。`Δ = 0.050`（値幅を広げない向きに丸め済み）。`P_limit = 150.060 + 0.050 = 150.110`。`d × (P_limit − S) = 0.610 > 0` | `RiskAssessment.stop_before_rounding = stop_after_rounding = Price(149.500)`、`adverse_fill_limit = Price(150.110)` |
| 6. 数量 | `C(Q) = (往復手数料 0.001×2 + 損切り決済の slippage 0.010) × Q = 0.012 Q`。`R(Q) = 0.610 Q + 0.012 Q = 0.622 Q ≤ 20000` → `Q ≤ 32154.3…` → 数量刻み 1000 で**切り下げ** → `Q = 32000` | `Quantity(32000)`、`quantity_step=Decimal("1000")` |
| 7. 再検査 | `R(32000) = 0.622 × 32000 = 19904` 円 ≤ 20000。同時保持枠（未終端のエントリー注文0 ＋ 開いている建玉0 < 1）も合格 | `RiskCheckResult(check="admission_budget", passed=True, limit=Money(20000,JPY), observed=Money(19904,JPY))`、`RiskCheckResult(check="position_slot", passed=True, limit=Decimal("1"), observed=Decimal("0"))` |
| 8. 記録 | `RiskAssessment(assessment_id=EvidenceId 00000002, attempt_id=00000001, policy_ref=RP1, reached_step=8, budget=…, reference_quote=…, adverse_fill_limit=Price(150.110), stop_before_rounding=Price(149.500), stop_after_rounding=Price(149.500), quantity_step=Decimal("1000"), quantity=Quantity(32000), conversion=ConversionRate(JPY, JPY, 1, 09:00Z, EvidenceRef(EvidenceId 00000002)), cost_budget=Money(384,JPY), reservation_amount=Money(19904,JPY), checks=(…))` → 表6。換算の経路そのものは `EvidenceRecord(evidence_id=00000002, …, conversion_paths=(ConversionPath(legs=(), rate=1, observed_at=09:00Z, skew=0分),))` として表15 に残す（D06 §8.5.1 の規則8） |

受付の確定単位（D06 §4.4）で次を1回の差し替えにまとめる。

| 記録 | 値 |
|---|---|
| `AttemptDecision` | `AttemptAccepted(attempt_id=00000001, order_id=OrderId 00000001, assessment_ref=RiskAssessmentRef(EvidenceId 00000002))` → 表5 |
| `AcceptedOrder` | `run_id=run-A, order_id=00000001, attempt_id=00000001, accepted_at=ProcessingPoint(09:00Z, ADMISSION, 1), symbol=USDJPY, side=BUY, quantity=Quantity(32000), expires_at=09:20Z, execution=ExecutionCommitment(policy_ref=EP1, execution_series=USDJPY/15m/bid, eligibility=ScheduledOpen(bar_key=BarKey(…,09:00Z), open_time=09:00Z)), terms=AcceptedEntryTerms(initial_stop=Price(149.500), exit_plan_ref=…, reservation_id=ReservationId 00000001, reference_quote=…, adverse_fill_limit=Price(150.110)), evidence_ref=EvidenceRef(EvidenceId 00000002)` → 表7 |
| `OrderEvent` | `(event_id=EventId 00000002, order_id=00000001, from_status=None, to_status=PENDING, at=ProcessingPoint(09:00Z, ADMISSION, 1), reason=None, fill_id=None)` → 表8。ここから `OrderState(order_id=00000001, status=PENDING, last_event_id=00000002, last_processed_at=…, terminal_reason=None)` を投影 |
| `RiskReservation` | `(run_id=run-A, reservation_id=00000001, order_id=00000001, account_id=ACC1, created_at=ProcessingPoint(09:00Z, ADMISSION, 1), amount=Money(19904,JPY), assessment_ref=RiskAssessmentRef(00000002))` ＋ `ReservationState(00000001, HELD, …)` → 表10 |
| `AdmissionNotice` | `(opportunity_id=00000001, attempt_id=00000001, accepted=True, reason=None)`。作るのは `ADMISSION`、配送は rank 12（D06 §6.6） |

**候補 open の検査**（D06 §5.3）: `expires_at = 09:00Z + 20分 = 09:20Z`。`entry_delay_bars=0` なので最初の適格 open は `09:00Z`。`09:00Z < 09:20Z` を満たし、週末休場をまたがず、`run_interval.end` より前なので受け付ける。

### 2.4 rank 11（`EXECUTION_OPEN`）の中身

D06 §7.5 の6手順の順で処理する。

1. 既存建玉なし → gap 決済なし。
2. 適格な成行注文（`ScheduledOpen(09:00Z)`）を約定させる。基準は `[09:00,09:15)` の `open` bid `150.050` → ask `150.070` → `entry_slippage` を不利方向（上げる）に適用して **`Price(150.080)`**。
3. 新規建玉を作り初期の損切りを有効化。
4. gap 判定: `150.080 > 149.500` なので損切りを飛び越えていない。
5. 約定ずれ: `max(0, +1 × (150.080 − 150.060)) = 0.020 ≤ Δ = 0.050` → 緊急決済なし。
6. 4・5 とも不成立。

| 記録 | 値 |
|---|---|
| `FillRecord` | `(run_id=run-A, fill_id=FillId 00000001, event_id=EventId 00000003, order_id=00000001, position_id=PositionId 00000001, processed_at=ProcessingPoint(09:00Z, EXECUTION_OPEN, 0), execution_time=ExactExecutionTime(09:00Z), price=Price(150.080), quantity=Quantity(32000), costs=(CostEntry(COMMISSION, Money(32,JPY), Money(32,JPY), 率1), CostEntry(SLIPPAGE_IN_PRICE, Money(320,JPY), Money(320,JPY), 率1), CostEntry(SPREAD_IN_PRICE, Money(640,JPY), Money(640,JPY), 率1)), evidence_ref=EvidenceRef(EvidenceId 00000003))` → 表9 |
| `Position` | `(position_id=00000001, account_id=ACC1, strategy_id=S1, symbol=USDJPY, side=BUY, quantity=Quantity(32000), entry_fill_id=00000001, entry_price=Price(150.080), opened_at=ProcessingPoint(09:00Z, EXECUTION_OPEN, 0), protection=ProtectionState(version=1, stop_loss=Price(149.500), take_profit=None, effective_from=BarKey(USDJPY/15m/bid, 09:00Z), owner_instance_id="take_profit"), status=OPEN, close_fill_id=None, realized=None)` → 表11 |
| `OrderEvent` | `(event_id=00000003, order_id=00000001, from_status=PENDING, to_status=FILLED, at=…, reason=None, fill_id=00000001)` → 表8 |
| 予約の移管 | `ReservationState(00000001, TRANSFERRED, …)` ＋ `PositionRiskAllocation(allocation_id=AllocationId 00000001, position_id=00000001, source_reservation_id=00000001, amount=Money(19904,JPY), created_event_id=00000003, released_event_id=None)` |
| 実リスクの計測 | `RiskMeasurement(position_id=00000001, at=…, measured=Money(18560,JPY), allocated=Money(19904,JPY), basis="entry_price - initial_stop")`。`(150.080 − 149.500) × 32000 = 18560`。差 1344 円は新規枠へ戻さない【合意済み】上位 §4.7.15 D |
| balance | 手数料 32 円だけを控除して `Money(999968, JPY)`。価格に反映済みの slippage と spread は**金額として控除しない**（D06 §7.6） |
| 台帳 snapshot | `LedgerSnapshot(at=ProcessingPoint(09:00Z, EXECUTION_OPEN, 1), balance=Money(999968,JPY), equity=Money(998688,JPY), consumed=Money(19904,JPY), open_position_ids=(00000001,))` → 表14 |
| `equity` の内訳（v1.1） | 含み損益の評価価格は**直前に完了した執行足 `[08:45,09:00)` の終値（bid）`150.040`**（D06 §8.1、Q13 決定）。買い建玉なので bid をそのまま使う。含み損益 `(150.040 − 150.080) × 32000 = −1280` → `equity = 999968 − 1280 = 998688`。**約定した足 `[09:00,09:15)` の始値 `150.050` は使わない**（使えば `−960` になり値が変わる。この食い違いが D07 §5.4 の差し戻しの理由だった） |

### 2.5 rank 12（`POST_FILL_EVALUATION`）の中身

第2回の `step`。`PublicationBatch(batch_id=EventId 00000004, decision_time=09:00Z, phases=BACKTEST_PHASES, available_bars=(), scheduled_closes=(), runtime_events=(RuntimeEventNotice(POSITION_OPENED, position_id=00000001, opportunity_id=00000001),), admissions=(AdmissionNotice(00000001, 00000001, True, None),), is_run_end=False)`。

| 順 | 何が起きるか | 記録 |
|---|---|---|
| 1 | 入口で受付通知を適用（D05 §7.2 の遷移5） | `OpportunityTransition(00000001, ORDER_PENDING → TERMINATED, at=ProcessingPoint(09:00Z, POST_FILL_EVALUATION, 0), reason=Reason(FULFILLED_BY_ORDER_ACCEPTANCE), attempt_id=00000001)` → 表3 |
| 2 | `POSITION_OPENED` で `take_profit` を起動。入力は `RuntimeContextView.position_context(09:00Z, PositionId 00000001)` | `PositionContext(position_id=00000001, symbol=USDJPY, direction=LONG, quantity=Quantity(32000), entry_price=Price(150.080), effective_stop_loss=Price(149.500), effective_take_profit=None, opened_at=ProcessingPoint(09:00Z, EXECUTION_OPEN, 0))`（D06 §8.4） |
| 3 | 部品が `risk = 150.080 − 149.500 = 0.580` から `SetTakeProfit(Price(151.240))` を返す（`reward_risk=2.0`。丸めは D06 の責務） | `ManagementRequest(position_id=00000001, action=SetTakeProfit(Price(151.240)), decision_time=09:00Z, source_output_id=OutputId 00000006)`（D05 v1.2 で必須。`roles.exit` の出力 `00000006` を指す）、`EvaluationRecord(… position_id=00000001, outcome=Evaluated((OutputId 00000006,)))` → 表2 |
| 4 | エンジンが丸めて建玉へ適用（買いは `DOWN`。`151.240` は刻みに整列済み）。検査 `entry 150.080 < take_profit 151.240` 合格 | `ProtectionState(version=2, stop_loss=Price(149.500), take_profit=Price(151.240), effective_from=BarKey(…, 09:00Z), owner_instance_id="take_profit")`。**初期の利確なので `effective_from` は約定した足**（D06 §8.3） |
| 5 | 適用結果を記録。丸め後の実リスクリワード比 `(151.240 − 150.080) / (150.080 − 149.500) = 1.160 / 0.580 = 2.0` | 表12（`MANAGEMENT_APPLICATIONS`、主キー `(position_id, at)`） |

**追えた要点**: 「利確なしの建玉」が1判断時点も現れない（D05 §8 の要求）。初期の損切りと初期の利確がどちらも `effective_from = BarKey(…, 09:00Z)` であり、**約定したその足の到達判定の対象になる**（D06 §7.3）。

### 2.6 T = 2015-01-06T11:15Z（利確の到達）

`[11:00,11:15)` の足が終了し、`ExecutionBarComplete` が rank 0 を起こす。`high=151.300 ≥ take_profit 151.240`、`low=151.000 > stop_loss 149.500` → **片側だけの到達**。

| 記録 | 値 |
|---|---|
| 解決 | `IntrabarResolution(position_id=00000001, parent_bar_key=BarKey(…,11:00Z), method=SINGLE_HIT, series_used=(USDJPY/15m/bid,), resolved_child_bar_key=None, verdict=TAKE_PROFIT, fill_id=FillId 00000002)` → 表13 |
| 要求 | `OrderRequest(attempt_id=00000002, created_at=ProcessingPoint(11:15Z, EXECUTION_BAR_COMPLETE, 0), origin=ENGINE, payload=CloseRequest(position_id=00000001, cause=TAKE_PROFIT, valid_for=20分, source_output_id=None))` → 表4 |
| 受付 | `AcceptedOrder(order_id=00000002, side=SELL, quantity=Quantity(32000), expires_at=11:35Z, execution=ExecutionCommitment(…, eligibility=ProtectionHit(position_id=00000001, protection_version=2, execution_bar_key=BarKey(…,11:00Z))), terms=AcceptedCloseTerms(position_id=00000001, cause=TAKE_PROFIT))` → 表7。**予約は作らない**（D06 §4.4） |
| 約定 | 基準は保護水準 `151.240`（始値はこれを越えていない）。買い建玉の決済は売りなので bid 基準に `close_slippage` を不利方向（下げる）に適用 → `Price(151.230)`。`FillRecord(fill_id=00000002, execution_time=BarExecutionInterval(BarKey(…,11:00Z), Interval[11:00,11:15)), price=Price(151.230), quantity=Quantity(32000), costs=(CostEntry(COMMISSION, Money(32,JPY), …), CostEntry(SLIPPAGE_IN_PRICE, Money(320,JPY), …)))` → 表9 |
| 損益 | 値幅 `151.230 − 150.080 = 1.150` → `1.150 × 32000 = 36800` 円。手数料 32 円を控除して `balance = 999968 + 36800 − 32 = Money(1036736, JPY)` |
| 建玉 | `Position.status=CLOSED`、`close_fill_id=00000002`、`realized=Money(36768, JPY)`（`36800 − 32`、決済側の手数料だけ。入場手数料は約定時に計上済み） |
| 割当 | `PositionRiskAllocation.released_event_id` に決済イベントの `EventId`。`consumed` は 0 へ戻る |

受付・約定・損益・割当解放は**1つの確定単位**にまとめる（D06 §4.4 の「エンジン生成の即時決済」）。rank 1 では含み損益の再評価と `LedgerSnapshot` だけを行う。

**未記述だった点（2）**: 当初の D06 は rank 1（`LEDGER_UPDATE`）を「建玉・実現損益・balance・消費済み枠を更新」と書いており、rank 0 の確定単位と重なっていた。同じ約定が2つのフェーズで反映される読み方ができたため、rank 1 を「確定後の含み損益の再評価と台帳 snapshot の記録」に限定した。

## 3. 経路2: 正常エントリー → 損切り

経路1 と rank 0〜13 まで同一。到達判定の足だけが異なる（`[11:00,11:15)` の `low=149.400 ≤ 149.500`、`high=150.900 < 151.240`）。

| 項目 | 値 |
|---|---|
| 解決 | `IntrabarResolution(method=SINGLE_HIT, verdict=STOP_LOSS, …)` |
| 約定価格 | 基準は保護水準 `149.500`。始値がこれを越えていないため水準を基準にし、`close_slippage` を下げる方向に適用 → `Price(149.490)` |
| 損益 | `(149.490 − 150.080) × 32000 = −18880` 円。手数料 32 円 → `balance = 999968 − 18880 − 32 = Money(981056, JPY)` |
| 予約との差 | 受付時に確保した枠 19904 円、約定時の計測リスク 18560 円、実際の損失 18912 円。**枠は上限であって保証ではない**ことが3つの数値の並びから読める（上位 §4.7.9 C） |

`CloseCause=STOP_LOSS` と `Reason` は別フィールドである（上位 §4.7.14）。決済理由は `CloseCause`、注文の終端理由は `OrderState.terminal_reason`（正常約定では `None`）。

## 4. 経路3: 足内で損切りと利確の両方に触れる

`[11:00,11:15)` が `high=151.300`（利確到達）と `low=149.400`（損切り到達）の両方を満たす場合。

### 4.1 経路3a: 下位足で解決（`RESOLVED_BY_CHILD`）

`ExecutionPolicy.resolution_hierarchy = ResolutionHierarchy((USDJPY/15m/bid, USDJPY/5m/bid))` を宣言した**人工データ限定**の構成（ADR-0030 は「子足で解決できる場合と `UNRESOLVED` になる場合の両方を人工データで作る」と定めている）。上位設計書 §7.1 が「保有データにない5m足は例の前提にしない」としているため、**実データでは段階2 でこの経路は起きない**。

run 開始前の適合検査（D06 §7.4 の検査1〜5）:

| 検査 | `HierarchyCheckResult` |
|---|---|
| 1 被覆 | `(check="coverage", passed=True, parent_series=USDJPY/15m/bid, child_series=USDJPY/5m/bid, parent_bar=BarKey(…,11:00Z), expected_interval=Interval[11:00,11:15), child_intervals=(Interval[11:00,11:05), Interval[11:05,11:10), Interval[11:10,11:15)), coverage_gaps=(), coverage_overlaps=(), …)` |
| 2 価格基準 | `expected_basis=BID`、`observed_basis=BID`、`passed=True` |
| 3 足境界 | `expected_boundary=11:15Z`、`observed_boundary=11:15Z`、`passed=True` |
| 4 利用可能時刻 | `expected_available_at=11:15Z`、`observed_available_at=11:05Z`、`passed=True` |
| 5 存在 | `child_intervals` が3本そろう、`coverage_gaps=()`、`passed=True` |

解決の手順（D06 §7.4）:

1. 親足で両方に触れた → 次の解像度の子足を時系列順に走査。
2. `[11:00,11:05)`: `high=151.300 ≥ 151.240`、`low=151.000 > 149.500` → **利確だけに触れる** → 採用して走査を打ち切る。
3. `IntrabarResolution(position_id=00000001, parent_bar_key=BarKey(…,11:00Z), method=RESOLVED_BY_CHILD, series_used=(USDJPY/15m/bid, USDJPY/5m/bid), resolved_child_bar_key=BarKey(USDJPY/5m/bid, 11:00Z), verdict=TAKE_PROFIT, fill_id=00000002)` → 表13。
4. 約定価格・損益は経路1 と同じ（`Price(151.230)`、36800 円）。`FillRecord.execution_time` は `BarExecutionInterval(BarKey(USDJPY/15m/bid, 11:00Z), Interval[11:00,11:15))` のまま。**子足で順序を決めても、約定時刻の精度は親足の区間のまま**である（D06 §7.3。足内の正確な到達時刻は観測できない）。

**追えた要点**: `series_used` と `resolved_child_bar_key` があるため、判断履歴から「どの解像度まで降りて決めたか」を読める。

### 4.2 経路3b: 解決できない（`UNRESOLVED_SL_PRIORITY`）

段階2 の実データ構成では階層が1段（`levels=(USDJPY/15m/bid,)`）なので、検査は5だけが走り、両方に触れた時点で最小解像度に到達している。

| 記録 | 値 |
|---|---|
| 解決 | `IntrabarResolution(method=UNRESOLVED_SL_PRIORITY, series_used=(USDJPY/15m/bid,), resolved_child_bar_key=None, verdict=STOP_LOSS, fill_id=00000002)` → 表13 |
| 約定 | 損切りを採用（ADR-0030）。経路2 と同じ `Price(149.490)`、`−18880` 円 |
| run manifest | `resolution_hierarchy`、`UNRESOLVED_SL_PRIORITY` の件数 1、全競合に対する割合 1.0（D06 §9.3） |
| `BacktestResult` | `unresolved_intrabar_count = 1` |

**追えた要点**: 損切り優先が「既定の規則」ではなく「階層を降りきった結果の裁定」であることが `method` から読める。

## 5. 経路4: 受付前拒否（`PROTECTION_INVALID`）

### 5.1 この経路が起きる条件: 2つの系列の終値の食い違い

**保護水準は評価系列から作られ、その妥当性は執行系列と比べて検査される。** この2つは初版データでは**別々の入力ファイル**であり（D03 §2 の `<SYMBOL>_15m_merged.csv` と `<SYMBOL>_1h_merged.csv`）、1時間足を15分足から集約してはいない（D03 §5.1 の集約対象は `1h → 4h/1d` だけ）。したがって同じ時刻の終値が一致する保証は無く、食い違えばこの経路が実行中に起きる。

| 値の出どころ | 値 |
|---|---|
| `breakout_level`（評価系列 `USDJPY/1h/bid` の直前20本の `HIGH` の最大） | `Price(150.000)` |
| `stop_level`（同じ窓の `LOW` の最小） | `Price(149.990)` |
| `entry_trigger` が見る終値（1時間足 `[08:00,09:00)` の `close`） | `Price(150.040)` → `150.040 > 150.000` で発火 |
| 参照価格の出どころ（執行系列 `USDJPY/15m/bid` の `[08:45,09:00)` の `close`） | **`Price(149.980)`**（1時間足の終値と6 pips 食い違う人工データ） |

rank 5〜9 は経路1 と同じに進み、`EntryProposal(opportunity_id=00000001, order_intent=OrderIntent(USDJPY, LONG, MARKET, None, None), protection=ProtectionLevels(stop_loss=Price(149.990), take_profit=None), decision_time=09:00Z, intent_output_id=OutputId 00000004, protection_output_id=OutputId 00000005)` が1件出る（出力 ID の採番は経路1 と同じ順序）。

### 5.2 rank 10（`ADMISSION`）で拒否されるまで

| 手順 | 値 |
|---|---|
| 3. 参照価格 | 直前に完了した執行足 `15m 08:45Z` の `close=149.980`（bid）→ ask `150.000`。`ReferenceQuote(price=Price(150.000), basis=ASK, observed_at=09:00Z, source_bar=BarKey(USDJPY/15m/bid, 08:45Z), derived_from_spread=True)` |
| 4. 保護水準の妥当性 | 買いの損切り `149.990` は判断時 bid `149.980` **以上** → 違反（上位 §4.7.9 B） |
| `RiskCheckResult` | `(check="protection_direction", passed=False, limit=Decimal("149.980"), observed=Decimal("149.990"))` |
| 審査記録 | 手順4 で拒否したため手順5〜7 へ進まない。`RiskAssessment(assessment_id=EvidenceId 00000002, attempt_id=00000001, policy_ref=RP1, reached_step=4, budget=AdmissionBudget(balance=Money(1000000,JPY), trial_budget=Money(20000,JPY), account_remaining=Money(200000,JPY), admission_budget=Money(20000,JPY), consumed=Money(0,JPY)), reference_quote=…, stop_before_rounding=Price(149.990), stop_after_rounding=None, adverse_fill_limit=None, quantity_step=None, quantity=None, conversion=None, cost_budget=None, reservation_amount=None, checks=(RiskCheckResult("protection_direction", False, …),))` → 表6。**到達しなかった手順の項目は `None`** であり、`reached_step` がどこで止まったかを示す（D06 §6.4 の手順8） |
| 拒否 | `AttemptRejected(attempt_id=00000001, reason=Reason(PROTECTION_INVALID), assessment_ref=RiskAssessmentRef(EvidenceId 00000002))` → 表5。手順3 まで到達しているため `RiskAssessment` を残す（D06 §4.4） |
| 台帳 | 変化なし。注文も予約も作らない |
| 通知 | `AdmissionNotice(opportunity_id=00000001, attempt_id=00000001, accepted=False, reason=Reason(PROTECTION_INVALID))` を rank 12 で配送 |
| 機会 | 遷移6: `ORDER_PENDING → TERMINATED`、`reason=Reason(ORDER_ATTEMPT_REJECTED)`（D05 §7.2）。エンジンの拒否理由を機会の終端理由に読み替えない |

**追えた要点**: 拒否理由（`PROTECTION_INVALID`、エンジン側）と機会の終端理由（`ORDER_ATTEMPT_REJECTED`、戦略側）が別の語であり、表5 と表3 で別々に集計できる。受付前拒否でも `OrderRequest`（表4）が残るため、機会 → 試行の連鎖が切れない。

### 5.3 参照価格の出どころ（Q10、選択肢1 で決定済み）は段階2 でも結果を変える

**同じ人工データで、Q10 の不採用案である選択肢2（評価系列の終値を参照価格にする）を採ると結果が変わる。** 参照価格が1時間足の `close=150.040` → ask `150.060` になり、損切り `149.990` は判断時 bid `150.040` より下なので検査に合格し、**受け付けられて約定する**。つまり Q10 は「判断履歴にどの足が記録されるか」だけの選択ではなく、**取引が成立するかどうかの選択**である。

どちらを採っても代償がある。

| 選択肢 | 代償 |
|---|---|
| 1（執行系列。**決定**） | 戦略が見ていない系列で保護水準を検査するため、戦略から見て妥当な損切りが2系列の食い違いで拒否される |
| 2（評価系列） | 検査は戦略の見ている値と揃うが、約定は執行系列で起きるため、**約定ずれ（`max(0, d × (P_fill − P_ref))`）の中に2系列の差が入り込む**。約定ずれ超過による緊急決済の発生頻度が、執行モデルではなくデータの食い違いで決まる |

紙上では「保護水準の検査の正確さ」と「約定ずれの測定の正確さ」のどちらを優先するかの選択であり、**どちらを選んでも2系列の食い違いはどこかに現れる**。本書は上位設計書 §4.7.9 C が許容不利約定幅を「約定がどれだけ不利にずれたか」を測る基準としていることから選択肢1 を推奨として書き、**2026-09-21 に選択肢1 で決定された**（D06 §16）。

**仮置き**: 参照価格に鮮度の上限（`max_age`）は置かない。直前に完了した執行足に限るため、鮮度は構造的に執行足1本分以内に収まるからである（D06 §6.4 の手順3）。

## 6. 経路5: 同時保持上限と建玉枠

### 6.1 経路5a: 取引機会の同時保持上限（`CONCURRENCY_LIMIT_REACHED`）

検証戦略 A の基準の宣言（`breakout_level.lookback = stop_level.lookback = 20`）では、**この経路は起きない**。取引機会は生成された判断時点のうちに `FULFILLED_BY_ORDER_ACCEPTANCE` か `ORDER_ATTEMPT_REJECTED` で終端するため、有効な機会が次の判断時点へ持ち越されないからである。

上限に達する状況を作るには、**取引機会が `OPEN` のまま残る**必要がある。段階2 でそれが起きるのは、注文意図と保護水準の役割出力が揃わない場合だけである（D05 §6.2 の手順9）。ウォームアップの長さが違う宣言（`breakout_level.lookback=20`、`stop_level.lookback=50`）を使うと、21本目から50本目までの間に次が起きる。

| 時点 | 何が起きるか | 記録 |
|---|---|---|
| T_a（30本目の1時間足の確定） | `breakout_level` は `Evaluated`、`stop_level` は履歴不足で `Skipped` | `EvaluationRecord(instance_id="stop_level", outcome=Skipped((MissingInputDiagnosis(input_name="prices", source=ResolvedMarketSource(USDJPY/1h/bid, LOW), reason=WARMUP_INSUFFICIENT),)))` → 表2 |
| T_a | `entry_trigger` が発火し機会1を生成（遷移1、`OPEN`） | 表3 |
| T_a | `entry_order` は `intent` を出すが、`initial_stop` は `level` 入力（`stop_level.level`）が無く `Skipped` → **役割出力が揃わず `EntryProposal` は作られない** | 表2。`proposals=()` |
| T_a | rank 10 に渡る要求が無い | 表4・表5 に行なし。**warmup 中の注文ゼロ**（全体計画 §8.2 の完了条件）がここで成立する |
| T_b（価格が水準を割り込む） | `entry_trigger` の状態が `ConditionState(False)` へ戻り再武装 | 表1・表2 |
| T_c（再び突破） | 新しい機会2を採番して組み立てた後、有効な機会数を数える。機会1 が `OPEN` のままなので 1 ≥ `max_active=1`、`on_new_trigger=KEEP_EXISTING` → **遷移2** | `OpportunityTransition(opportunity_id=00000002, from_state=None, to_state=TERMINATED, at=ProcessingPoint(T_c, P3_TRIGGER, n), reason=Reason(CONCURRENCY_LIMIT_REACHED), counterpart=None, attempt_id=None)` → 表3 |

`n` は D05 §6.6 の規則で決まる `step` 内の通し番号である。この経路は起動する使用箇所の数がウォームアップの進み具合で変わるため、本書は値を固定しない（規則どおりに数えれば一意に決まる）。
| T_c | 終端した機会2 のイベントは**下流へ配送しない**（D05 §6.2・§7.4）。`entry_order` と `initial_stop` は起動しない | 表4 に行なし |

エンジン側（D06）でこの経路に現れるのは、表3 と表2 の行を `TraceSink` へ書くことだけである。受付・執行・台帳はいっさい動かない。**発火は捨てずに記録し、有効にしない**（ADR-0032）ことが、表3 に2件の機会が残り終端理由で区別できることから確認できる。

### 6.2 経路5b: 建玉枠による受付前拒否（`RISK`）

建玉が開いている間に、価格がいったん水準を割り込んでから再び突破した場合。取引機会は正常に生成され `EntryProposal` まで進むが、受付の検査で止まる。

| 手順 | 値 |
|---|---|
| 7. 再検査 | 未終端のエントリー注文 0 ＋ 開いている建玉 1 = 1、上限 1 未満ではない → 違反（D06 §8.2） |
| `RiskCheckResult` | `(check="position_slot", passed=False, limit=Decimal("1"), observed=Decimal("1"))` |
| 拒否 | `AttemptRejected(attempt_id=…, reason=Reason(RISK), assessment_ref=RiskAssessmentRef(…))` → 表5 |
| 機会 | 遷移6: `ORDER_PENDING → TERMINATED`、`reason=Reason(ORDER_ATTEMPT_REJECTED)` |

**追えた要点**: 取引機会の同時保持上限（戦略側、`CONCURRENCY_LIMIT_REACHED`）と建玉・未約定注文の枠（エンジン側、`RISK`）は**別の上限**であり、別の表・別の語で記録される。上位 §4.7.12 の「戦略が注文を見ないことによって無制限に受付できることにはしない」は、後者が担保している。

## 7. 経路6: 有効時間切れ

**この経路は段階2 では発生しない。** 紙上で追うと次のように詰まる。

1. 受付（rank 10）で `expires_at = accepted_at.time + valid_for` を決め、同時に**期限内に候補の始値があることを検査**して、その候補を `ScheduledOpen(bar_key, open_time)` として固定する（D06 §5.3）。したがって受付が成立した時点で `open_time < expires_at` が成り立っている。
2. 候補を欠損時に後続の足へ置換しない（上位 §4.7.15 B）。期限は延長しない（上位 §4.7.13 B）。
3. 候補の始値の時刻には必ず判断時点がある（`ExecutionOpen` が生成される、D03 §7.1）。その判断時点で rank 2（`ORDER_EXPIRY`）は `expires_at <= T` を判定するが、`T = open_time < expires_at` なので期限切れにならず、rank 11 で約定する。
4. 候補足が実際には存在しなかった場合は、期限切れではなく**実行失敗**（`DATA_ERROR`、遷移5）になる（上位 §4.7.13 C）。

有効時間を短くしても結果は変わらない。短くすると候補が期限外になり、受付が成立せず `NO_CANDIDATE` の**受付前拒否**になるためである（注文が作られないので期限切れも起きない）。`entry_delay_bars=1` にしても同じで、1本見送った先の候補が期限内にあるかどうかが受付時に検査される。

| 設定 | 結果 |
|---|---|
| `entry_valid_for=20分`（初版） | 候補 `09:00Z` < `09:20Z` → 受付 → 約定 |
| `entry_valid_for=10分` | 候補 `09:00Z` < `09:10Z` → 受付 → 約定（`entry_delay_bars=0` では候補が同時刻なので常に期限内） |
| `entry_delay_bars=1`、`entry_valid_for=10分` | 候補 `09:15Z` は `09:10Z` を超える → `NO_CANDIDATE` の受付前拒否。注文は作られない |
| `entry_delay_bars=1`、`entry_valid_for=20分` | 候補 `09:15Z` < `09:20Z` → 受付 → `09:15Z` に約定 |

**未記述だった点（3）**: D06 §11 は「検証戦略 A では通常発生しない」とだけ書いており、**どの設定でも発生しない**ことと、その原因（候補を受付時に固定し期限内であることを検査する規則）が書かれていなかった。段階2 の意味論テストは、受付を経由せずに `AcceptedOrder` を直接組み立てて状態機械だけを検証する単体テストとして書く必要がある。期限切れが実際の実行で起きるのは、価格条件を待つ注文（指値・逆指値）を入れる段階6 である。→ D06 §5.1 の遷移3 の扱いを改めた。

## 8. 経路7: 週末持ち越し禁止（`CARRY_NOT_ALLOWED`）

T = **2015-01-09T22:00Z**（金曜、NY 17:00 の週の終わり）。この時刻に1時間足 `[21:00,22:00)` が確定し、突破が成立して `EntryProposal` が1件出る。

| 手順 | 値 |
|---|---|
| 候補の探索 | カレンダー（`fx_ny17` v1）と執行系列の足スケジュールから、`22:00Z` より後の最初の執行足の始値を探す → `2015-01-11T22:00Z`（日曜 NY 17:00 の週開け） |
| 週末の判定 | 候補が週末休場をまたぐ → `CARRY_NOT_ALLOWED`（D06 §5.3） |
| 期限の判定 | `expires_at = 22:20Z` なので候補は期限外でもある → `NO_CANDIDATE` も同時に成立 |
| 代表理由 | **`CARRY_NOT_ALLOWED`**（D06 §5.2 の順位表）。有効時間を長くしても回避できない規則であることを、理由コードで読めるようにする |
| 記録 | `OrderRequest`（表4）と `AttemptRejected(reason=Reason(CARRY_NOT_ALLOWED), assessment_ref=None)`（表5）。候補が無いまま終わるため `RiskAssessment` は作らない（D06 §4.4） |
| 機会 | 遷移6: `ORDER_ATTEMPT_REJECTED` で終端 |
| 台帳 | 変化なし |

**未記述だった点（4）**: 週末持ち越し禁止と「期限内に候補なし」が**同時に成立する**ことが紙上で判明したが、どちらを代表理由にするかが D06 にも上位設計書にも書かれていなかった（上位 §4.7.14 は「複数理由が成立した場合の代表理由」を後続へ委ねている）。→ D06 §5.2 に受付前拒否の代表理由の順位表を置いた。

**追えた要点**: 週末の識別はカレンダーで行い、UTC の土日判定に置き換えない（上位 §4.7.13 B）。冬時間の金曜は 22:00Z、夏時間では 21:00Z になるため、時刻をコードに埋め込む実装はこの経路で壊れる。

## 9. 経路8: run 末尾の残存処理

run_end = **2015-01-16T22:00Z**（`run_interval.end`）。この時点で、経路1 の取引は決済済み、2件目の建玉 P2 が開いているものとする。

| 項目 | 値 |
|---|---|
| P2 | `entry_price=Price(151.000)`、`quantity=Quantity(30000)`、`protection=ProtectionState(version=2, stop_loss=Price(150.400), take_profit=Price(152.200), …)`、入場手数料 30 円は計上済み |
| balance | `1036736 − 30 = Money(1036706, JPY)` |
| 最終評価価格 | `USDJPY/15m/bid` の `[21:45,22:00)` の `close = 151.500`（買い建玉の評価は bid、上位 §4.7.13 E） |
| P2 保有中の評価価格（v1.1） | `USDJPY/15m/bid` の各足の**終値はいずれも入場価格 `151.000` 以上**とする。第2.1節の P1 と同じ趣旨の人工データの設定で、P2 の保有中に `equity` が `balance` を下回らないことを固定する |

D06 §10.1 の手順を順に追う。

| # | 手順 | フェーズ | 記録 |
|---|---|---|---|
| 1 | `[21:45,22:00)` の足について保護水準の到達判定 → どちらにも触れていない | `EXECUTION_BAR_COMPLETE` / `LEDGER_UPDATE` | `LedgerSnapshot`（表14） |
| 2 | `expires_at <= 22:00Z` の `PENDING` なし | `ORDER_EXPIRY` | 記録なし |
| 3 | 通常どおり第1回の `step` を呼び、判断履歴を残す。突破が成立すれば機会を1件生成する | `PUBLICATION` 〜 `P5_ORDER_INTENT` | 表1〜表3 |
| 4 | 手順3 から出た `EntryProposal` を**受付前に拒否**する | `ADMISSION` | `OrderRequest`（表4）＋ `AttemptRejected(reason=Reason(RUN_END), assessment_ref=None)`（表5）。注文も予約も作らない |
| 4' | rank 11 は行わない。rank 12 では手順4 の通知を配送するだけ | `POST_FILL_EVALUATION` | 遷移6（`ORDER_ATTEMPT_REJECTED`）→ 表3 |
| 4'' | rank 12 で全数量決済の管理要求が出れば、rank 13 で要求を組み立てて `RUN_END` で受付前拒否する | `POST_FILL_ADMISSION` | 表4・表5 |
| 5 | 残った受付済み `PENDING` を `CANCELED`（`RUN_END`）にし、未約定予約を解放する | `RUN_END` | 第9.1節 |
| 6 | 残存する取引機会を終端する | `RUN_END` | 第9.2節 |
| 7 | 残存建玉を MTM 評価して最終 snapshot を保存する | `RUN_END` | 第9.3節 |

### 9.1 手順5（残存注文の取消）が起きる条件

紙上で追うと、**判断時点をまたいで `PENDING` のまま残る注文は1種類しかない**ことが分かる。`entry_delay_bars=0` では rank 10 で受け付けた注文は同じ判断時点の rank 11 で約定するため、残るのは**約定後の受付（rank 13）で受け付けた決済注文**（最初の適格な始値が次の執行足になる）だけである。

| 時点 | 何が起きるか |
|---|---|
| 2015-01-16T21:45Z の rank 12 | Exit 部品が `ClosePosition()` を返す（段階3 の期間 Exit などを想定） |
| 同 rank 13 | `CloseRequest(position_id=P2, cause=STRATEGY_EXIT, valid_for=20分)` を受け付ける。候補は次の執行足の始値 `22:00Z`、`expires_at=22:05Z` |
| 2015-01-16T22:00Z（run_end） | rank 11 を行わないため約定しない。手順5 で `OrderEvent(PENDING → CANCELED, reason=Reason(RUN_END))`、`ReservationState` は無い（決済は予約を作らない）。建玉割当は解放しない |

候補の始値が run_end 以降になる注文を**受け付けるか、受付時に拒否するか**は上位 §4.7.13 F が「詳細設計対象」としたまま残っており、本書では受け付ける側（同節の時刻表「受付済みなら CANCELED/RUN_END」）で追った。→ D06 の Q11 として差し戻し、**選択肢1（受け付けて末尾で `CANCELED`（理由 `RUN_END`）にする）で決定済み**である。本節の追跡はその決定と一致する。

### 9.2 手順6（残存機会の終端）

`is_run_end=True` の第3回 `step`（D05 §6.1 v1.1、D06 §10.2）。

| 項目 | 値 |
|---|---|
| バッチ | `PublicationBatch(batch_id=EventId …, decision_time=2015-01-16T22:00Z, phases=BACKTEST_PHASES, available_bars=(), scheduled_closes=(), runtime_events=(), admissions=(), is_run_end=True)` |
| 戻り値 | `RuntimeStepResult(outputs=(), evaluations=(), proposals=(), management_requests=(), transitions=(OpportunityTransition(opportunity_id=00000001, from_state=OPEN, to_state=TERMINATED, at=ProcessingPoint(22:00Z, RUN_END, 0), phase=RUN_END, reason=Reason(RUN_END), counterpart=None, attempt_id=None),))` |
| 検査 | エンジンは `outputs` / `evaluations` / `proposals` / `management_requests` が空であることを検査する（末尾で新しい判断が生まれない） |

経路5a でウォームアップ中に生まれ `OPEN` のまま残っていた機会1 が、ここで `RUN_END` で終端する。終端しないまま run が終わると、終端理由別の集計で機会の総数が合わなくなる（D05 §7.2 の遷移9 の趣旨）。

### 9.3 手順7（最終集計）

| 項目 | 値 | 計算 |
|---|---|---|
| `FinalSummaries.realized` | `Money(36706, JPY)` | 確定した決済損益 36800 − 計上済み手数料 94（P1 入場32 ＋ P1 決済32 ＋ P2 入場30）。`balance − initial_balance` と一致する |
| `cost_breakdown[COMMISSION]` | `Money(94, JPY)` | 上の内訳。balance に反映済み |
| `cost_breakdown[SLIPPAGE_IN_PRICE]` | `Money(940, JPY)` | `0.010 × (32000 + 32000 + 30000)`。**価格に反映済みの参考値であり balance から控除しない** |
| `cost_breakdown[SPREAD_IN_PRICE]` | `Money(1240, JPY)` | `0.020 × (32000 + 30000)`（買い側だけ ask を使うため）。同じく参考値 |
| `FinalSummaries.equity_with_mtm` | `Money(1051706, JPY)` | `1036706 + (151.500 − 151.000) × 30000 = 1036706 + 15000` |
| `FinalSummaries.hypothetical_closed` | `Money(14670, JPY)` | 残存建玉を終了時の価格・費用モデルで仮に閉じた場合: `(151.500 − 0.010 − 151.000) × 30000 − 0.001 × 30000 = 14700 − 30`。**計算だけ行い、注文・約定・完了取引数・balance・リスク枠を変更しない** |
| `BacktestResult.trade_count` | 1 | 決済まで終わった建玉の数。P2 は未決済なので数えない |
| `BacktestResult.opportunity_count` | 表3 に現れた `opportunity_id` の総数 | 終端理由別の集計は D07 の責務 |
| `swap_modeled` | `False` | ADR-0029。未計上であることが結果から読める |

**未記述だった点（5）**: 費用の区分（`CostKind`）が `COMMISSION` と `SLIPPAGE_IN_PRICE` の2つしかなく、**spread の価格反映分を記録する区分が無かった**。D06 §7.6 は「価格に反映済みの費用（slippage・spread）を `SLIPPAGE_IN_PRICE` の `CostEntry` として残す」と書いていたが、それでは D07 が「執行モデルの滑り」と「提示価格の幅」を分けて集計できない。→ D06 に `SPREAD_IN_PRICE` を足した。

**未記述だった点（6）**: 手順5・6・7 の**順序**が D06 に書かれていなかった。注文の取消 → 機会の終端 → 最終 snapshot の順でないと、終端済みの機会に対応する注文が後から取り消される記録になる。→ D06 §10.1 に順序と理由を足した。

### 9.4 台帳 snapshot の `equity` の全値（v1.1。D07 の最大ドローダウンの検算）

D06 §8.1（Q13 決定）により、run 中の `equity` は**直前に完了した執行足の終値**で評価する。台帳 snapshot は各判断時点の `LEDGER_UPDATE` と `EXECUTION_OPEN` の後に1件ずつ残る（同節）。本書の人工データで `equity` が取る値は次のとおりで、D07 §5.2 の #5・#6（最大ドローダウン、含み損益込み）はここから手で確かめられる。

| # | 時点 | `balance` | 建玉 | 評価価格 | 含み損益 | `equity` |
|---|---|---|---|---|---|---|
| 1 | 2015-01-06T09:00Z `LEDGER_UPDATE` | 1,000,000 | なし | — | 0 | **1,000,000** |
| 2 | 同 09:00Z `EXECUTION_OPEN` | 999,968 | P1 買 32,000 @150.080 | `[08:45,09:00)` の終値 150.040 | −1,280 | **998,688** |
| 3 | 09:15Z〜11:00Z の各 `LEDGER_UPDATE` | 999,968 | P1 | 各足の終値（第2.1節の設定で 150.040 以上） | −1,280 以上 | 998,688 以上 |
| 4 | 2015-01-06T11:15Z `LEDGER_UPDATE` | 1,036,736 | なし（P1 決済済み） | — | 0 | **1,036,736** |
| 5 | P2 入場から 21:45Z までの各 snapshot | 1,036,706 | P2 買 30,000 @151.000 | 各足の終値（第9節の設定で 151.000 以上） | 0 以上 | 1,036,706 以上 |
| 6 | 2015-01-16T22:00Z `RUN_END`（最終） | 1,036,706 | P2 | `[21:45,22:00)` の終値 151.500 | +15,000 | **1,051,706** |

建玉が1件も無い判断時点では `equity = balance` である（含み損益が 0 のため）。行#2 と行#5 以外の時点で `equity` が `balance` を下回らないことは、第2.1節と第9節の2つの設定が保証する。

**最大ドローダウン（含み損益込み）の検算**。`at` の昇順に走査すると、これまでの最大 `equity` は 1,000,000 → 1,036,736 → 1,051,706 と上がり、下回るのは行#2（998,688）と行#5（最小 1,036,706）の2か所だけである。

- 行#2: `1,000,000 − 998,688 = 1,312 JPY`、率は `1,312 ÷ 1,000,000 = 0.001312`
- 行#5: `1,036,736 − 1,036,706 = 30 JPY`（P2 の入場手数料 30 円の分）

したがって**最大ドローダウン（含み損益込み）は `1,312 JPY`、率は `0.001312`** である。確定損益（`balance`）だけで同じ手順を行うと `1,000,000 − 999,968 = 32 JPY`、率 `0.000032` になる（D07 §5.2 の #7・#8）。含み損益を含めると値が 41 倍になるのは、入場直後の含み損 1,280 円が `balance` には現れないためで、2つの基準を採用指標と参考値に分ける理由（D07 §5.4）がこの数値に出ている。

## 10. 各表に残る行の一覧（経路1 の1取引分）

D06 §9.2 の15表のうち、経路1 の1取引で行が入るのは次のとおり。golden trace（全体計画 §8.4）の対象になる。

| # | 表 | 行数 | 主キーの値 |
|---|---|---|---|
| 1 | `OUTPUTS` | 6 | `OutputId 00000001`〜`00000006`（水準2件・取引機会・注文意図・保護水準・管理要求） |
| 2 | `EVALUATIONS` | 6 | `EvaluationId 00000001`〜`00000006` |
| 3 | `OPPORTUNITY_TRANSITIONS` | 3 | `(00000001, P3_TRIGGER)` / `(00000001, P5_ORDER_INTENT)` / `(00000001, POST_FILL_EVALUATION)` |
| 4 | `ORDER_REQUESTS` | 2 | `AttemptId 00000001`（エントリー）/ `00000002`（利確の決済） |
| 5 | `ATTEMPT_DECISIONS` | 2 | 同上 |
| 6 | `RISK_ASSESSMENTS` | 1 | `EvidenceId 00000002`（決済は審査に入らないため行なし） |
| 7 | `ORDERS` | 2 | `OrderId 00000001` / `00000002` |
| 8 | `ORDER_EVENTS` | 4 | 受付2件・約定2件 |
| 9 | `FILLS` | 2 | `FillId 00000001` / `00000002` |
| 10 | `RESERVATIONS` | 1 | `ReservationId 00000001`（`HELD → TRANSFERRED`） |
| 11 | `POSITIONS` | 1 | `PositionId 00000001` |
| 12 | `MANAGEMENT_APPLICATIONS` | 1 | `(00000001, ProcessingPoint(09:00Z, POST_FILL_EVALUATION, n))` |
| 13 | `INTRABAR_RESOLUTIONS` | 1 | `FillId 00000002` |
| 14 | `LEDGER_SNAPSHOTS` | 判断時点数 × 2 まで | `ProcessingPoint`（`LEDGER_UPDATE` と `EXECUTION_OPEN` の後） |
| 15 | `EVIDENCE` | 4 以上 | 要求・審査・約定・保護水準の更新 |

**ID 連鎖の確認**: 機会 `00000001` → 試行 `00000001`（表4 の `opportunity_id`）→ 注文 `00000001`（表5 の `order_id`）→ 約定 `00000001`（表9 の `order_id`）→ 建玉 `00000001`（表9 の `position_id`）→ 管理要求（表12 の `position_id`）と、予約 `00000001`（表7 の `reservation_id` → 表10 → 表11 の `PositionRiskAllocation`）が、いずれも外部キーで辿れる。受付前拒否（経路4・5b・7・8）でも表4 が残るため連鎖は切れない。

## 11. 検証戦略 B をどこまで追えたか

検証戦略 B（上位設計書 §7.1 の B: 日足 EMA から MarketState、1時間足と日足高値から Trigger、同時刻に利用可能な15分足で後続確認、未確認なら後続の15分足確定で再判定、期限内に成立すれば発注、1時間足で管理水準を更新）を、現在の文書の型で追えた範囲と追えない範囲を分ける。

### 11.1 追えた範囲

| 層 | 追えたこと |
|---|---|
| 宣言（D04） | 複数系列の入力（`MarketDataRef(USDJPY/1d/bid, CLOSE)` と `USDJPY/1h/bid`）、`market_state` と `execution_filter` の役割フィールド、`entry_policy=AwaitConfirmation(...)`、`opportunity_validity.bindings`（`SNAPSHOT_AT_OPPORTUNITY` / `REQUIRE_UNTIL_ORDER_REQUEST`）、`opportunity_concurrency` の3項目。**D04 の型で宣言そのものは書ける** |
| 公開（D03） | 日足・1時間足・15分足の `ScheduledBoundary` と `Publication` が同じ判断時点に並ぶこと、系列順が `(symbol, 名目長の降順, basis)` で固定されること、日足の `interval` が DST 切替日に 23/25 時間になること |
| 受付以降（D06） | 取引機会が2件同時に `ORDER_PENDING` へ進んだ場合の**受付の全順序**（`AdmissionKey` の `origin_seq = opportunity_id.seq` の昇順、D06 §6.3）、建玉枠による2件目の `RISK` 拒否（経路5b と同じ）、約定・台帳・費用・記録（経路1 と同じ） |
| 末尾 | 残存する複数の取引機会が `is_run_end=True` の `step` で `opportunity_id.seq` の昇順に `RUN_END` で終端すること（D05 §6.1 v1.1） |

**エンジン側（D06）に、検証戦略 B のために足りない型は見つからなかった**。B が A と違うのは戦略ランタイムの中身（待機・確認・再判定）であり、受付から記録までの経路は同じ型を通る。

### 11.2 追えない範囲と、その理由

| 追えないこと | 理由 | 担当と時期 |
|---|---|---|
| 日足 EMA の評価 | 指標部品（EMA・ATR）がカタログに無い。D05 §4.3 の5部品は検証戦略 A 用 | D05 v2.0（段階3前） |
| MarketState の許可状態と `market_permission@v1` の配送 | 部品が無く、`Observation[MarketPermission]` を返す契約が未定義 | D05 v2.0 |
| 後続確認（`AwaitConfirmation`・ExecutionFilter）の意味論 | 確認期限の数え方、開始足の扱い、確認評価の起動が未定義 | D05 v2.0（§10 で対象外と明記） |
| 待機（`WAIT_FOR_INPUT`）と `USE_PREVIOUS` の遡り | 意味論と宣言形が未定義 | D05 v2.0 |
| 評価要求の追い越し（`REQUEST_SUPERSEDED`）と待機記録・追い越し記録の trace 表 | 意味論が未定義のため、表の列も決められない | D05 v2.0 → D06 v1.5（§12） |
| 1時間足での管理水準の更新（トレーリング、`UPDATE_STOP`） | `ManagementAction` は段階2 で `SET_TAKE_PROFIT` / `CLOSE_POSITION` の2種別のみ（D04 §11.2） | 段階3・D06 v1.5 |
| 遅延シナリオ4ケース別の処理順の検証 | D06 §1.2 の第4列（段階3・D08） | 段階3・D08 |
| 複数建玉・複数銘柄の台帳とリスク配分 | D06 §1.2 の第4列（D10） | 段階6・D10 |

**範囲を狭めていないことの確認**: 上の7件はいずれも D05 §10 または D06 §12 が**時期**を、D06 §1.2 の境界表が**担当**を既に決めている項目であり、本書が追跡を諦めて範囲を狭めたものではない。B を紙上で最後まで通せるようになるのは D05 v2.0 の承認後であり、そこで T01 に B の時刻表を追記する（全体計画 §8.2 の段階3 完了条件）。

## 12. 段階0 の完了条件との対応

全体計画書 §8.2 は段階0 の完了条件を「2本の検証戦略を紙上で追跡でき、同時刻・数量・末尾処理に暗黙の前提がない」としている。

| 条件 | 本書での確認 | 状態 |
|---|---|---|
| 検証戦略 A を紙上で追跡できる | 第2〜9節（8経路） | 満たす |
| 検証戦略 B を紙上で追跡できる | 第11節 | **部分的**。エンジン側の経路は追えるが、戦略ランタイム側（後続確認・待機・指標部品）は D05 v2.0 待ち |
| 同時刻に暗黙の前提がない | 第1.4節（1判断時点の15フェーズとイベントの対応）、第2.2節、経路7（週末境界） | 満たす |
| 数量に暗黙の前提がない | 第2.3節（予算 → 数量 → 再検査の8手順を手計算で確認） | 満たす |
| 末尾処理に暗黙の前提がない | 第9節（手順1〜7）。Q11 は選択肢1 で決定済み | 満たす |

## 13. 未記述の一覧（本書で見つけたもの）

| # | 分類 | 未記述だった点 | 発見した経路 | 対処 |
|---|---|---|---|---|
| 1 | 規則の欠落 | 受付時の参照価格をどの系列・どの足・どの項目から取るかが無い | 経路1・経路4 | D06 §6.4 の手順3 に規則を足し、判断が割れるため **Q10** として差し戻し。**選択肢1 で決定済み**（直前に完了した執行足の終値） |
| 2 | 規則の重複 | 台帳更新フェーズ（rank 1）の内容が、保護決済の確定単位と重なっていた | 経路1 | D06 §4.1・§4.2 で rank 1 を含み損益の再評価と snapshot の記録に限定 |
| 3 | 到達不能な遷移 | 期限切れ（遷移3）がどの設定でも発生しないことと、その原因が書かれていない | 経路6 | D06 §5.1 に到達条件を明記し、テストの書き方を示した |
| 4 | 代表理由の欠落 | 週末持ち越し禁止と「期限内に候補なし」が同時成立したときの代表理由が無い | 経路7 | D06 §5.2 に受付前拒否の代表理由の順位表を新設 |
| 5 | 型の不足 | spread の価格反映分を記録する費用区分が無い | 経路8 | D06 の `CostKind` に `SPREAD_IN_PRICE` を追加 |
| 6 | 順序の欠落 | run 末尾の3処理（注文の取消・機会の終端・最終 snapshot）の順序が無い | 経路8 | D06 §10.1 に手順5〜7 として順序と理由を明記 |
| 7 | 規則の欠落 | 建玉が開いたことを知らせる通知の `opportunity_id` の出どころが無い | 経路1 | D06 §4.2 に「約定した注文の `EntryRequest.opportunity_id` から取る」と明記 |
| 8 | 規則の欠落 | エンジンが生成する決済要求の `valid_for` に何を入れるかが無い | 経路1・経路2 | D06 §7.3 に `close_valid_for` をそのまま入れると明記 |
| 9 | 規則の欠落 | 利確水準の検査に失敗した場合と、管理要求が返らなかった場合の建玉の扱いが無い | 経路1 | D06 §8.3 に2つの分岐を明記 |
| 10 | 規則の欠落 | 銘柄別の実行ポリシー（許容不利約定幅）に対象銘柄が無い場合の扱いが無い | 経路1 | D06 §7.1.1・§10.5 で実行前のデータ能力検査に含めた |
| 11 | 型の不整合 | `RiskPolicy.cost_budget: Money`（固定額）では、数量に比例する費用予算 `C(Q)` を表せない | 経路1 の手順6 | D06 §3・§6.4 で `RiskPolicy` から固定額の項目を外し、`C(Q)` は `CostModel` から計算すると明記 |
| 12 | 差し戻し（決定済み） | 候補の始値が run 末尾以降になる注文を受け付けるか拒否するか（上位 §4.7.13 F が詳細設計対象としたまま） | 経路8 | D06 §5.3 に本書の読み方を書き、**Q11** として差し戻し。**選択肢1 で決定済み**（受け付けて末尾で取消） |
| 13 | 仮置き | 参照価格に鮮度の上限を置くかどうか | 経路4 | 段階2 は置かない（理由は第5.3節）。段階3 で再判断 |
| 14 | 規則の欠落 | 参照価格を**どのポートから**引くかが無い。戦略向けビューの `latest_available` は期待足が未到着なら古い足へ戻らない（D03 §6.2）ため、そこからは引けない | 経路1・経路4 | D06 §6.4 の手順3 に「エンジンが rank 0 で処理し終えた最新の執行足を `ExecutionSeries.bar` で引く」と明記 |
| 15 | 到達可否の明示 | 6件の受付前拒否の理由コードそれぞれについて、実行で起きるか・どう検証するかが書かれていない | 経路4〜8 | D06 §5.2 に理由コードごとの到達可否と検証の仕方の表を新設 |
| 20 | 規則の帰結の未記述 | 保護水準が**戦略の見ていない系列**と比べて検査されること（評価系列と執行系列は別々の入力ファイルで、終値が一致する保証が無い）が書かれていない | 経路4 | D06 §6.4 の手順3 に「系列をまたぐこと」の行を足し、これが規則の帰結であって不具合ではないことを明記。Q10 の影響欄も「段階2でも結果が変わる」に直した |
| 16 | 型の不足 | 換算の経路（各 leg の系列・率・観測時点・ずれ）を保存する場所が15表のどこにも無い。`ConversionRate` は合成後の率しか持たない | 経路1（恒等換算）・Q7 の規則 | D06 の `EvidenceRecord` に `conversion_paths` を追加し、§8.5.1 に規則8 として明記 |
| 17 | 型の表現 | 恒等換算（JPY→JPY）では参照する市場系列が無く、`series` / `bar_key` / `observed_at` が必須の `ConversionLeg` を1本も作れない | 経路1 | `ConversionPath.legs` を「0本＝恒等 / 1本＝直接 / 2本＝基軸通貨経由」とし、恒等換算を空の経路で表すことにした |
| 18 | 保存形式の欠落 | 可変長の入れ子（レコードの `tuple`）を平坦化して Parquet へ保存する規則が無い | 経路1（`checks` / `market_refs` / `conversion_paths`） | D06 §9.1 に「要素ごとに正規化エンコード文字列にし、その文字列の `list` 列として保存する」を追加 |
| 19 | 阻害要因（解消済み） | `EntryProposal` に根拠の出力 ID が無く、**正常経路でも `OrderRequest` を組み立てられない** | 経路1 の要求組立 | **D05 §3・§6.2（v1.2）を同じ PR で改訂し、`EntryProposal.intent_output_id` / `protection_output_id` と `ManagementRequest.source_output_id` を足して解消**。受け取り先は D06 §6.1 の表 |

| 21 | 型の不整合 | 審査の途中（手順4 の保護水準の妥当性など）で拒否すると、`RiskAssessment` の必須項目（許容不利価格・丸め後の損切り・換算率・費用予算）を構築できない | 経路4 | どこまで進んだかを示す `reached_step` を足し、手順5 以降が作る項目を省略可能にした。審査記録を残す条件も「手順3 まで到達した試行」に一般化した |
| 22 | 規則の欠落 | run 中の台帳 snapshot の含み損益を**どの足のどの価格で評価するか**が無く、`equity` を `…` のままにするしかなかった | 経路1（第2.4節） | D07 §5.4 が差し戻し、D06 の **Q13** として決定（直前に完了した執行足の終値）。D06 §8.1 に規則を足し、本書 v1.1 で `equity` を確定させた（第2.4節・第9.4節） |

分類の内訳: 規則の欠落9件（#1・#4・#7・#8・#9・#10・#14・#20・#22）、型の不足・不整合・表現5件（#5・#11・#16・#17・#21）、規則の重複1件（#2）、順序・保存形式の欠落2件（#6・#18）、到達可否の明示2件（#3・#15）、差し戻して決定済み3件（Q10・Q11・Q13＝#1 の一部と #12・#22）、阻害要因で解消済み1件（#19）、仮置き1件（#13）。合計22件。**差し戻した3件は 2026-09-21 に決定され、阻害要因1件は D05 v1.2 の改訂で解消した。本書の時点で残る未解決はない。**

## 14. 本書の後続版

| 版 | 追記する内容 | 前提 |
|---|---|---|
| v1.1 | 検証戦略 B の時刻表（MarketState・後続確認・待機・再判定・トレーリング） | D05 v2.0 の承認 |
| v1.1 | 遅延シナリオ4ケース別の処理順の差分 | D08 |
| v1.6 | 単一実行評価（D07）までの引き渡しの残り（指標の一覧・診断・実験 manifest） | D07 の承認 |

v1.1（2026-09-21、PR #17）では、上表の最終行（D07 への引き渡しの残り）のうち**含み損益の評価価格と `equity` の全値だけ**を前倒しした（第2.4節・第9.4節）。D07 の最大ドローダウンの検算値がこの2点に依存しており、D07 の承認と同じ PR で必要になったためである。v1.2（2026-09-21、PR #18）は上表のどの行でもなく、段階2 の実装に合わせた通し番号の訂正である（第2.2節）。そのため上表の残りは v1.3 へ繰り下げた。v1.3（2026-09-22、PR #20）も上表のどの行でもなく、隔離期間を読まずに通しの検証を行えるようにするための日付の改訂である（数値は不変）。v1.4（2026-09-23、PR #22）も上表のどの行でもなく、他文書の版の呼び方を揃える改訂である（内容は不変）。上表の残りはさらに v1.5 へ繰り下げた。v1.5（2026-09-25、PR #40）も上表のどの行でもなく、設計文書の必須表（R1）に合わせて第15節の対応表を足す改訂である（内容は不変）。上表の残りはさらに v1.6 へ繰り下げた。

## 15. 必須表との対応（R1。v1.5）

全体計画書 §8.5 は、状態機械や実行時の値の伝播を定める設計文書に3つの必須表を求め、紙上トレースには**表2（状態×出来事表）の代わりに経路ごとに通るマスの対応表**を、**表3（値の伝播表）の代わりに既存の「各表に残る行の一覧」**を置くとしている。本書は状態機械を定めず、D05・D06 の状態機械を1判断時点ずつ追う文書だからである。レビュー対象範囲は追跡する設計文書の §1.2（D06 §1.2）に従う。

### 15.1 経路ごとに通る遷移

遷移の番号は、取引機会が D05 §7.2 の遷移1〜12、注文が D06 §5.1 の遷移1〜6 である。

| 経路 | 取引機会（D05 §7.2） | 注文（D06 §5.1） | 本書の節 |
|---|---|---|---|
| 経路1: 正常エントリー → 利確 | 1 → 4 → 5（表3 の3行） | エントリー注文 1 → 2（`EXECUTION_OPEN`）。利確の決済注文 1 → 2（`EXECUTION_BAR_COMPLETE`） | 第2節・第10節 |
| 経路2: 正常エントリー → 損切り | 経路1 と同じ | 経路1 と同じ（決済は損切りの到達） | 第3節 |
| 経路3a・3b: 足内で損切りと利確の両方に触れる | 経路1 と同じ | 経路1 と同じ（決済は足内競合の解決） | 第4節 |
| 経路4: 受付前拒否（`PROTECTION_INVALID`） | 1 → 4 → 6 | なし（受付前拒否は注文を作らない） | 第5節 |
| 経路5a: 取引機会の同時保持上限 | 機会1 は 1 のあと `OPEN` のまま残り、経路8 で 9。機会2 は 2 | なし | 第6.1節・第9.2節 |
| 経路5b: 建玉枠による受付前拒否（`RISK`） | 1 → 4 → 6 | なし | 第6.2節 |
| 経路6: 有効時間切れ | 通らない | 遷移3 は**到達しない**（受付時に期限内の候補を固定するため）。候補足が実際に無ければ遷移5（`DATA_ERROR`）になると述べるが、経路としては追っていない | 第7節 |
| 経路7: 週末持ち越し禁止（`CARRY_NOT_ALLOWED`） | 1 → 4 → 6 | なし | 第8節 |
| 経路8: run 末尾の残存処理 | run_end で出た `EntryProposal` は 4 → 6（手順4・4'）。残存機会（経路5a の機会1）は 9（手順6） | 約定後の受付（rank 13）で受け付けた決済注文 1 → 4（`CANCELED`、理由 `RUN_END`） | 第9節 |

**本書のどの経路も通らない遷移**:

- 取引機会の遷移3（`SUPERSEDED`）: 本書の宣言は `on_new_trigger=KEEP_EXISTING`（第6.1節）。
- 取引機会の遷移7（`CLOSED_BY_ORDER_ACCEPTANCE`）: 本書に経路が無い（理由の記述も無い）。
- 取引機会の遷移8（`MARKET_STATE_INVALIDATED`）: 本書の宣言は `bindings=()` なので再検査が起きない（第2.2節の rank 4）。
- 取引機会の遷移10〜12: 段階3 の遷移であり、本書は追えない範囲として第11.2節に挙げた。[T02](T02_paper_trace_strategy_b.md) が追う。
- 注文の遷移3（`EXPIRED`）: 到達しない（第7節、D06 §5.1）。注文の遷移5（`DATA_ERROR`）: 条件だけを述べ、経路としては追っていない（第7節）。注文の遷移6（`POSITION_CLOSED`）: 本書に経路が無い（理由の記述も無い）。

### 15.2 値の伝播の実例

値の伝播の実例は第10節（各表に残る行の一覧と ID 連鎖の確認）である。規則の正本は D05 §7.8・D06 §9.5 の値の伝播表（R1）であり、本書はそれを追加も変更もしない。
