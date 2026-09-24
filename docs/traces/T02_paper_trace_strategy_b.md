# T02: 紙上トレース（検証戦略 B・段階3）

作成日: 2026-09-23
状態: **承認（2026-09-23、PR #24）v1.0**。**v1.2（2026-09-25、段階3 実装 PR 5/5）**: 段階3 の実装で検証戦略 B の run をエンジンの全フェーズまで通し、受入れテストの実測値と第16節の検算値を突き合わせた（第18節の v1.2 の予定）。**検算値はすべて一致した**。検算値ではない記述の誤りを4件直した（`01-06 15:00Z` の発火の見落とし、決済の約定の識別子、P5 直前の再検査の位置、経路2 の参照価格。第1.6節・第3.4節・第4節・第13節）。突き合わせの結果と遅延シナリオ4ケースの実測は第20節に置いた。各経路の結論は変わらない。**v1.1（2026-09-24、PR #27）**: 検証戦略 B の評価順についての人間の再決定を受け、第1.3節の評価順を D05 v2.4 §9.2 の規則の出力（取引機会の参照の因果辺を含む）に合わせ、並びが変わった箇所（第3.2節 rank 5 の `stop_level` と `m15_above_ema` の出力 ID・通し番号、第7.1節の評価順の列）を直した。市場状態 → 取引機会 → 確認 → 注文 の並びと、各経路の結論・検算値（第16節）は変わらない。第15節の要決定 Q23〜Q29 を人間がすべて決定し（7件とも提示時の推奨案である選択肢1）、本文と関係する設計文書へ反映した。決定に伴い**同じ PR で正本を3件改訂した**: 戦略ランタイム設計（[D05](../design/D05_strategy_runtime.md) v2.2。Q23・Q24・Q26〜Q29）、バックテスト基盤（[D06](../design/D06_backtest_vertical_slice.md) v1.6。Q25・Q26）、テスト戦略（[D08](../design/D08_test_strategy.md) v1.1。Q24 の意味論テスト）。**その反映の途中で新しく生じた Q30 も同じ日に決定した**（第15.2節。コンパイル時の検査を1件足す。D04 v1.11）。**未決の要決定は残っていない**。既存実装との差は第19節に「段階3 実装への引き渡し」として並べた。
v0.1（2026-09-23 起草）: 段階3 の戦略ランタイム設計（D05 v2.0）が §9.4 で挙げた**9経路**を、D03〜D07 の型とフィールド名で1判断時点ずつ追ったものである。T01（検証戦略 A の紙上トレース）§11.2 が「B を紙上で最後まで通せるようになるのは D05 v2.0 の承認後」として残した7件と、§14 の後続版の計画（検証戦略 B の時刻表・遅延シナリオ4ケース）を引き取る。置き場所は D05 §14 が起草時に決めるとした選択であり、**T01 への追記ではなく新しい文書（T02）**とした（理由は第18節）。追う run の日付は 2015年1月であり、T01 v1.3 と D08 §9.5 の扱いに合わせている（2026年以降は未分類の隔離期間で、いかなる経路でも読めない。D03 §3.8、ADR-0014）。

上位文書: [上位設計書](../design/fx_research_platform_greenfield_design.md) §4.3.11・§4.3.13・§4.3.14・§4.5・§7.1、[全体計画書](../design/fx_research_platform_overall_plan.md) §8.2、[D03](../design/D03_marketdata_and_time.md)、[D04](../design/D04_strategy_declarations.md)、[D05](../design/D05_strategy_runtime.md) §9.2〜§9.4、[D06](../design/D06_backtest_vertical_slice.md)、[D07](../design/D07_single_run_evaluation.md)、[D08](../design/D08_test_strategy.md) §9、[T01](T01_paper_trace.md)、ADR-0014・ADR-0030・ADR-0031・ADR-0032・ADR-0033

## 0. 本書の位置付けと読み方

紙上トレースとは、**実装を書く前に、1つの判断時点で何がどの型のどのフィールドに入るかを人間が手で追う作業**である（T01 §0）。型やフィールドが決まっていない箇所で手が止まるため、設計文書の未記述を機械的に洗い出せる。本書は「追えたこと」と「追えなかったこと」の両方を残す。

- 追えなかった箇所は第14節に**未記述の一覧**としてまとめ、分類（規則の欠落 / 型の不足 / 順序・保存形式 / 到達可否 / 重複 / 阻害要因）は T01 §13 と同じものを使う。
- **設計の選択を含まない未記述は、本書と同じ PR で D05 v2.1（必要に応じて D07）の改訂として反映した**。設計の選択を含むものは起草時には確定させず、第15節に要決定（Q23〜Q29）として残した（CLAUDE.md「前提を silent に決めない」）。**7件とも 2026-09-23 に人間が決定し（すべて選択肢1）、本書と D05 v2.2・D06 v1.6・D08 v1.1 へ反映済みである**（第15.1節）。**その反映の途中で新しく生じた1件（Q30）も同じ日に決定し（選択肢1。D04 v1.11 の検査 #15）、第15.2節に残した**。**未決の要決定は残っていない**。
- 数値は人工データの値であり、**手計算で検算できること**だけを目的にしている。指数移動平均（EMA）の値は割り切れる値だけを使うよう人工データを設計してある（第2.4節）。
- 本書は設計を新たに決めない。

**本書がいちばん強く報告すること**を先に書く。追えなかった経路が2つある。

| 経路 | 状態 | 節 |
|---|---|---|
| 経路1・経路5 のうち「**日足・1時間足・15分足がそろって終了する判断時点**で取引機会が生まれる」部分 | **到達しない**。突破水準が同じ判断時点で確定した日足そのものを含むため、その判断時点では1時間足の終値が日足高値を超えられない（第3.1節） | 第3.1節・第7.3節・**決定 Q23**（宣言は変えず、設計文書の例文のほうを改める） |
| 経路4（確認待ちのあいだに日足の条件が崩れて終端する） | **到達しない**。確認期限（15分足4本＝1時間）と日足境界が1時間境界の上にあることから、日足の条件が変わりうる唯一の確認足が期限の足と重なり、期限が先に勝つ（第6節） | 第6節・**決定 Q24**（到達しない経路として記録し、再検査そのものは意味論テストで検証する） |

どちらも**経路を追うのをあきらめて範囲を狭めたのではなく、宣言と規則から到達しないことが導けた**ものである。取引機会の生成そのものは、日足境界ではない1時間足の確定（第3.2節）で問題なく追えた。**2026-09-23 の決定（Q23・Q24、いずれも選択肢1）により、検証戦略 B の宣言も部品カタログも変えず、D05 の例文と経路一覧のほうを改めた**（D05 v2.2 §9.3・§9.4）。

## 1. 共通の前提

### 1.1 データと実行設定

| 項目 | 値 | 出どころ |
|---|---|---|
| 銘柄 | `USDJPY` | ADR-0015 |
| 系列 | `USDJPY/1d/bid`（`1d_ny17` v1）/ `USDJPY/1h/bid`（`1h` v1）/ `USDJPY/15m/bid`（`15m` v1） | D05 §9.2・D03 §3.2 |
| 執行系列 | `USDJPY/15m/bid`（`RunConfig.execution_series`） | ADR-0015・D06 §3 |
| 確認系列 | `USDJPY/15m/bid`（`ConfirmationPlan.series`） | D05 §5.6 の検査 b |
| カレンダー | `fx_ny17` v1（週開 日17:00 NY、週閉 金17:00 NY。冬時間では 22:00Z）。本書の区間に休場規則の追加は無い | D03 §3.4 |
| run 区間 | `Interval[2015-01-04T22:00Z, 2015-01-16T22:00Z)`（`RunConfig.run_interval`） | T01 §1.1 と同じ |
| 日足の被覆 | snapshot の日足は `2014-09-30T22:00Z` 以降を持つ（`daily_ema` の窓60本がウォームアップ不足にならないための設定。第2.4節） | 本書 |
| 銘柄仕様 | `price_tick=0.001` / `pip_size=0.01` / `quantity_step=1000` / `min_quantity=1000` | D02 §5.2 |
| 口座 | `AccountSpec(account_id=ACC1, currency=JPY, initial_balance=Money(1000000, JPY))` | D06 §8.1 |

`RunId = digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)`（ADR-0006、D06 §9.3）。以下では `RunId` を `run-B`、`StrategyRef` を `S2`、`CompiledStrategyRef` を `CS2` と書く。

**日足は1時間足からの集約である**【重要】。D03 §5.1 は「1h（bid）→ 4h_ny17、1d_ny17」を集約の対象と確定しており、日足の高値は構成する1時間足の高値の最大、日足の終値は最後の構成足の終値、日足の `available_at` は構成足の `available_at` の最大である。**15分足は集約に使わない**（執行系列であるため。同節）ので、1時間足と15分足の値が一致する保証は無い（T01 §2.1 と同じ扱い）。この非対称が第3.1節の到達可否に直結する。

### 1.2 ポリシー

T01 §1.2 と同じ値を使う。段階3 で値を変える理由が無く、同じ値なら T01 の検算手順をそのまま当てられるためである。

| ポリシー | 値 | 出どころ |
|---|---|---|
| `RiskPolicy` | `trial_risk_rate=0.02` / `account_risk_cap=0.20` | 上位 §4.7.10 |
| `ExecutionPolicy` | `entry_delay_bars=0` / `adverse_fill_limits={USDJPY: PriceOffset(0.05)}` / `entry_valid_for=20分` / `close_valid_for=20分` / `resolution_hierarchy=ResolutionHierarchy((USDJPY/15m/bid,))` / `reference_quote_source=EXECUTION_SERIES_LAST_CLOSE` | D06 §7.1.1 |
| `CostModel` | `commission_per_unit=Money(0.001, JPY)`（片道・1通貨あたり）/ `entry_slippage=PriceOffset(0.01)` / `close_slippage=PriceOffset(0.01)` / `spread_model=FixedSpread(PriceOffset(0.02))` / `swap_modeled=False` | D06 §7.2・§7.6 |
| `ConversionPolicy` | `pivot_currency=USD` / `max_observation_skew=15分` | D06 §8.5.1 |

`ask = bid + 0.02`。決済通貨（JPY）と口座通貨（JPY）が一致するため換算は率1の恒等換算であり、`ConversionPath` は `legs=()` である（T01 §1.2 と同じ）。

### 1.3 検証戦略 B の宣言（D05 §9.2 の具体値）

使用箇所12件。D05 §9.2 の表をそのまま写した。

| 使用箇所 | 契約 | 起動条件 | 入力の接続 | 出力 | 役割 |
|---|---|---|---|---|---|
| `daily_ema` | `ema` v2（`period=20`、`window_bars=60`、`prices` の `on_missing=WaitForInput`） | `OnBarClose("d1", USDJPY/1d/bid)` | `prices` ← `MarketDataRef(USDJPY/1d/bid, CLOSE)` | `value`: `price@v1` | — |
| `daily_above_ema` | `price_compare` v2（`operator=GT`、`left`/`right` の `on_missing=WaitForInput`） | `OnBarClose("d1", USDJPY/1d/bid)` | `left` ← `MarketDataRef(USDJPY/1d/bid, CLOSE)`、`right` ← `daily_ema.value` | `condition`: `condition_state@v1` | — |
| `no_short` | `constant_condition` v1（`value=False`） | `OnBarClose("d1", USDJPY/1d/bid)` | なし | `condition`: `condition_state@v1` | — |
| `market_state` | `permission_from_condition` v2（`long_allowed` の `on_missing=WaitForInput`） | `OnBarClose("d1", USDJPY/1d/bid)` | `long_allowed` ← `daily_above_ema.condition`、`short_allowed` ← `no_short.condition` | `permission`: `market_permission@v1` | `market_state` |
| `entry_trigger` | `breakout_trigger` v2（`direction=LONG`、`level` の `on_missing=WaitForInput`） | `OnBarClose("h1", USDJPY/1h/bid)` | `price` ← `MarketDataRef(USDJPY/1h/bid, CLOSE)`、`level` ← `MarketDataRef(USDJPY/1d/bid, HIGH)` | `opportunity`: `opportunity@v1` | `trigger` |
| `m15_ema` | `ema` v1（`period=20`、`window_bars=60`） | `OnBarClose("m15", USDJPY/15m/bid)` | `prices` ← `MarketDataRef(USDJPY/15m/bid, CLOSE)` | `value`: `price@v1` | — |
| `m15_above_ema` | `price_compare` v1（`operator=GT`） | `OnBarClose("m15", USDJPY/15m/bid)` | `left` ← `MarketDataRef(USDJPY/15m/bid, CLOSE)`、`right` ← `m15_ema.value` | `condition`: `condition_state@v1` | — |
| `entry_filter` | `condition_filter` v1（`include_start_bar=True`） | `OnBarClose("m15", USDJPY/15m/bid)` | `condition` ← `m15_above_ema.condition`、`opportunity` ← `RuntimeInputRef(OPPORTUNITY)` | `confirmation`: `confirmation_result@v1` | `execution_filter` |
| `entry_order` | `market_order_intent` v2 | `OnInputEvent("conf", "confirmation")` | `confirmation` ← `entry_filter.confirmation`、`opportunity` ← `RuntimeInputRef(OPPORTUNITY)` | `intent`: `order_intent@v1` | `order` |
| `initial_stop` | `level_stop_loss` v2 | `OnInputEvent("conf", "confirmation")` | `confirmation` ← `entry_filter.confirmation`、`level` ← `stop_level.level` | `protection`: `protection_levels@v1` | `protection` |
| `stop_level` | `extreme_price` v1（`mode=MIN`、`lookback=20`、`exclude_latest_bars=1`） | `OnBarClose("h1", USDJPY/1h/bid)` | `prices` ← `MarketDataRef(USDJPY/1h/bid, LOW)` | `level`: `price@v1` | — |
| `trailing` | `trailing_stop` v1 | `OnBarClose("h1", USDJPY/1h/bid)` | `position` ← `RuntimeInputRef(POSITION)`、`level` ← `stop_level.level` | `action`: `management_action@v1` | `exit` |

戦略全体の宣言（D05 §9.2）:

| フィールド | 値 |
|---|---|
| `entry_policy` | `AwaitConfirmation(deadline=BarsDeadline(bars=4), on_deadline=EXPIRE)` |
| `opportunity_validity` | `bindings=(ValidityBinding(source=OutputRef("daily_above_ema","condition"), mode=REQUIRE_UNTIL_ORDER_REQUEST, on_missing=SkipEvaluation),)` |
| `opportunity_concurrency` | `OpportunityConcurrencySpec(max_active=1, on_new_trigger=KEEP_EXISTING, on_order_accepted=KEEP_OTHERS)` |

待機の宣言（D05 §9.2）は、日足の連鎖の3段と突破 Trigger のいずれも `WaitForInput(deadline=BarsDeadline(bars=1), on_deadline=SKIP_EVALUATION, on_superseded=EXPIRE_REQUEST)` である。

評価順は D05 §5.4 の規則で一意に定まり、`daily_ema` → `m15_ema` → `no_short` → `stop_level` → `daily_above_ema` → `m15_above_ema` → `market_state` → `entry_trigger` → `entry_filter` → `entry_order` → `initial_stop` → `trailing` になる（D05 v2.4 §9.2 と同じ。v1.1 で、取引機会を出す部品 → 取引機会を読む部品の因果辺を含めた規則の出力に合わせた）。

コンパイル結果のうち本書が値を使うもの:

| 型 | 値 |
|---|---|
| `CompiledRoles` | `market_state=OutputRef("market_state","permission")` / `trigger=OutputRef("entry_trigger","opportunity")` / `execution_filter=OutputRef("entry_filter","confirmation")` / `order=OutputRef("entry_order","intent")` / `protection=OutputRef("initial_stop","protection")` / `exit=OutputRef("trailing","action")` / `confirmation=ConfirmationPlan(filter_instance="entry_filter", series=USDJPY/15m/bid, include_start_bar=True, deadline=BarsDeadline(4), on_deadline=EXPIRE)` |
| `CompiledStrategy.output_retention` | 第12節。**全項目が 1** である |

### 1.4 遅延シナリオ4ケースの設定

遅延モデルは実験のデータ公開設定に置き、OHLC と対象区間は変えず `available_at` と配送順序だけを変える（D03 §3.6）。4ケースは**同じ素の足**（第2節）に違う `DelayScenario` を当てたものであり、差が遅延だけであることを構造で保証する（D08 §9.6 の方針2）。

| # | 名前 | `DelayScenario` | 本書で追う経路 |
|---|---|---|---|
| 1 | 遅延なし | `DelayScenario(id="none", version=1, rules=())` | 経路1〜4・経路8・経路9 |
| 2 | 日足のみ2秒遅延 | `DelayScenario(id="d1_2s", version=1, rules=(FixedSeriesDelay(USDJPY/1d/bid, timedelta(seconds=2)),))` | 経路5 |
| 3 | 待機期限を超える遅延 | `DelayScenario(id="d1_25h", version=1, rules=(FixedSeriesDelay(USDJPY/1d/bid, timedelta(hours=25)),))` | 経路6 |
| 4 | 次足まで到着しない | `DelayScenario(id="d1_bar_hold", version=1, rules=(InjectedBarDelay(USDJPY/1d/bid, bar_start=2015-01-06T22:00Z, delay=timedelta(hours=25)),))` | 経路7 |

ケース3 を**系列全体の固定遅延**にしてあるのは意図的である。ケース4 のように1本だけを遅らせると、期限に到達する判断時点（次の日足の予定境界）で**新しい日足が公開されて追い越しも同時に成立する**。起草時はその優先順位が決まっていなかったため（第14節 #12）、期限だけを追いたいケース3 では系列全体を遅らせて追い越しが起きないようにした。**決定 Q29（2026-09-23、選択肢1）により期限が勝つ**ことが確定したので、1本だけを遅らせても記録は `DEADLINE_REACHED` ＋ `Skipped` に定まるが、**ケース3 の設定は変えない**（系列全体の遅延のほうが「期限だけ」を見る意図が読みやすく、追い越しはケース4 が担当するため）。

### 1.5 1つの判断時点の骨格（段階3 で増えるもの）

フェーズの列挙と順位は D06 §4.1 の15件（rank 0〜14）で、段階2 から変わらない。段階3 で中身が増えるのは次の4つである。

| rank | 名前 | 段階3 で増えること |
|---|---|---|
| 4 | `OPPORTUNITY_LIFECYCLE` | 確認期限（遷移11）の判定に加え、**待機中の評価要求の期限・追い越し・市場データの到着の検査**（D05 §6.8 手順1・2、D06 §4.1 v1.5） |
| 6 | `P2_MARKET_STATE` | 市場状態の使用箇所が実際に評価される（段階2 は宣言なし） |
| 8 | `P4_CONFIRMATION` | 確認待ちの取引機会1件につき1件の確認評価（D05 §6.11）、遷移10、有効性の再検査（D05 §7.7） |
| 9 | `P5_ORDER_INTENT` | 注文意図と保護水準が**確認結果の配送**で起動する。トレーリングの評価（`exit` 役割が足の確定で起動する）もここに来る。その出力（`UpdateStop`）を建玉へ適用するのは**rank 10 の直前**である（第14節 #5、**決定 Q25**。D06 v1.6 §4.2 の手順6） |

判断時点の数え方も段階3 で1つ増える。**遅れて届いた `Publication` は、足の終了予定が1つも無い判断時点を作る**（第7.2節）。D06 §4.2 の「公開も足の終了も無い判断時点では `step` を呼ばない」には当たらないので、`step` は呼ばれる。

### 1.6 識別子の書き方（本書の約束）

連番の識別子（`OutputId` など）は、**種別ごとに run 全体で 1 から単調に増え、その順序は処理順に一致する**（D02 §7.3）。本書は run 区間（`01-04 22:00Z` 〜 `01-16 22:00Z`）のうち**いくつかの判断時点だけ**を追うので、**本書に書く番号は2種類に分かれる**。読み違えると検算が合わなくなるため、ここで分けておく。

| 種別 | 本書の番号の意味 |
|---|---|
| `AttemptId` / `OrderId` / `PositionId` / `ReservationId` / `EvidenceId` | **実際の値である**。検証戦略 B は追跡した判断時点より前に注文も建玉も1つも作らないので、そこで採番される値が run の 1 件目になる |
| `OpportunityId` / `FillId` | **v1.2 で訂正: 実際の値ではない**（第20節）。v1.1 までは「人工データが `01-07 09:00Z` まで突破を成立させない」としてこの2種も実際の値に数えていたが、受入れテストの実測で2点の誤りが分かった。(1) `01-06 15:00Z` に1時間足の終値 149.250 が前日の日足高値 149.050（D(Jan5)）を超えて**発火する**。`01-05 22:00Z` の市場状態は買いを許さない（D(Jan5) の終値 149.000 が日足 EMA 149.000 を超えない）ので遷移12 で終端するが、`OpportunityId` は1つ消費される。したがって経路1 の機会は `00000002`、経路9 の機会は `00000003` になる。(2) エンジンは建玉を持つ執行足の終了ごとに保護水準の到達判定のための `FillId` を先に採番する（D06 §7.3 の実装。段階2 から同じ）ので、経路8 の決済の約定は `00000002` ではなく `00000018` になる。本書の番号は**相対的な見出し**として読む |
| `OutputId` / `EvaluationId` / `RequestId` / `EventId` | **相対的な見出しである**。これらは run の最初の判断時点（`01-05 22:00Z` 前後）から増え続けており、`01-07 09:00Z` までに実際の値は数百まで進んでいる。本書は追跡した判断時点の中で 1 から振り直して書く |

**本書の主張が使うのは相対的な順序だけ**であり、絶対値ではない。たとえば「適用した取引許可は、その発火が作った取引機会の出力記録より小さい `OutputId` を持つ最後の許可の出力である」（第3.2節、D05 §7.6）は、**同じ run の中で番号が処理順に増える**ことだけに依っている。段階3 の受入れテストと固定出力（golden）は実装が採番した実際の値を持つので、本書と突き合わせるときは**番号そのものではなく順序と件数**を比べる（第16節の検算値に識別子は1件も含めていない）。

## 2. 人工データの時刻表

値は 2015年1月6日（火）〜1月8日（木）に集中している。1時間足を素の系列として置き、日足はそこから集約した値である（第1.1節）。表に無い区間は「直前の行と同じ」として読む。

### 2.1 1時間足（`USDJPY/1h/bid`。素の系列）

| 区間（UTC） | `open` | `high` | `low` | `close` |
|---|---|---|---|---|
| … 〜 `2015-01-06T14:00Z`（D(Jan5) の全体と D(Jan6) の前半） | 149.000 | 149.050 | 148.950 | 149.000 |
| `[01-06 14:00, 15:00)` | 149.000 | **149.300** | 148.950 | 149.250 |
| `[01-06 15:00, 21:00)`（6本） | 149.250 | 149.290 | 149.150 | 149.250 |
| `[01-06 21:00, 22:00)` | 149.250 | 149.290 | 149.150 | **149.210** |
| `[01-06 22:00, 01-07 08:00)`（10本） | 149.250 | 149.290 | 149.150 | 149.250 |
| `[01-07 08:00, 09:00)` | 149.250 | 149.360 | 149.150 | **149.350** |
| `[01-07 09:00, 10:00)` | 149.360 | **149.520** | 149.340 | 149.450 |
| `[01-07 10:00, 13:00)`（3本） | 149.450 | 149.500 | 149.300 | 149.400 |
| `[01-07 13:00, 14:00)` | 149.400 | 149.420 | **149.050** | 149.100 |
| `[01-07 14:00, 21:00)`（7本） | 149.100 | 149.150 | 148.950 | 149.000 |
| `[01-07 21:00, 22:00)` | 149.000 | 149.020 | **148.800** | **148.810** |
| `[01-07 22:00, 01-08 08:00)`（10本） | 148.810 | 148.900 | 148.700 | 148.800 |
| `[01-08 08:00, 09:00)` | 148.800 | 149.750 | 148.780 | **149.700** |

### 2.2 日足（`USDJPY/1d/bid`。1時間足からの集約。D03 §5.1）

| 足 | 区間 | `open` | `high` | `low` | `close` | `available_at`（ケース1） |
|---|---|---|---|---|---|---|
| D(Jan5) 以前（60本以上） | 各 `[前営業日22:00Z, 当日22:00Z)` | 149.000 | 149.050 | 148.950 | **149.000** | 区間の終端 |
| D(Jan6) | `[01-05 22:00Z, 01-06 22:00Z)` | 149.000 | **149.300** | 148.950 | **149.210** | `01-06 22:00Z` |
| D(Jan7) | `[01-06 22:00Z, 01-07 22:00Z)` | 149.250 | **149.520** | 148.800 | **148.810** | `01-07 22:00Z` |
| D(Jan8) | `[01-07 22:00Z, 01-08 22:00Z)` | 148.810 | 149.750 | 148.700 | （本書では使わない） | `01-08 22:00Z` |

`Bar` の不変条件（D03 §3.3）を確認する。D(Jan6): `148.950 <= min(149.000, 149.210)` かつ `max(149.000, 149.210) <= 149.300`。D(Jan7): `148.800 <= min(149.250, 148.810)` かつ `max(149.250, 148.810) <= 149.520`。いずれも成立する。

### 2.3 15分足（`USDJPY/15m/bid`。執行系列かつ確認系列）

| 区間（UTC） | `open` | `high` | `low` | `close` |
|---|---|---|---|---|
| `[01-06 18:00, 01-07 08:45)`（**59本**。14時間45分 ÷ 15分 = 59） | 149.245 | 149.280 | 149.200 | **149.245** |
| `[01-07 08:45, 09:00)`（**確認の開始足**） | 149.245 | 149.360 | 149.240 | **149.350** |
| `[01-07 09:00, 09:15)` | 149.360 | 149.500 | 149.340 | 149.450 |
| `[01-07 09:15, 12:00)`（11本） | 149.450 | 149.500 | 149.300 | 149.400 |
| `[01-07 12:00, 13:00)`（4本） | 149.400 | 149.420 | 149.300 | 149.400 |
| `[01-07 13:00, 13:15)` | 149.400 | 149.410 | **149.050** | 149.080 |

**本数の数え方**（独立レビューの指摘を受けて v1.0 で明示）。区間は半開区間であり、`fx_ny17` の平日は 24 時間つながっている（週末以外に休場を置いていない。第1.1節）ので、15分足の本数は「区間の長さ ÷ 15分」でそのまま出る。1行目は 14時間45分 = 885分 = **59本**、4行目の `[01-07 09:15, 12:00)` は 165分 = **11本**、5行目の `[01-07 12:00, 13:00)` は 60分 = **4本**である。1行目と開始足（2行目）を合わせた **60本**が、第2.4節の `m15_ema` の窓（`window_bars=60`）にちょうど対応する。

`[01-07 08:45, 09:00)` の終値 149.350 は同じ時刻に終わる1時間足の終値と同じ値に作ってある。**一致は構造的な保証ではなく人工データの設定である**（15分足は集約の入力にも出力にもならない。D03 §5.1）。この扱いは T01 §2.1 と同じである。

### 2.4 手計算で確かめる指標値

`ema` の計算規則は D05 §4.5 のとおり「窓の古い側の `period` 本の単純平均を種とし、残りの足に `e = alpha*x + (1-alpha)*e` を古い順に適用する」である。`period=20` なので `alpha = 2/21` であり、更新式は `e_new = (2*x + 19*e) / 21` と書ける。**人工データは、この式が割り切れる値だけを通るように作ってある**。

| 判断時刻 | 対象 | 窓の中身 | 結果 |
|---|---|---|---|
| `01-06 22:00Z` | `daily_ema` | 古い59本が終値 149.000、末尾が D(Jan6) の 149.210 | 種 149.000 → `(2*149.210 + 19*149.000)/21 = 3129.420/21 = ` **`149.020`** |
| `01-06 22:00Z` | `daily_above_ema` | `left=149.210`、`right=149.020` | `149.210 > 149.020` → **`ConditionState(True)`** |
| `01-07 22:00Z` | `daily_ema` | 古い58本が 149.000、次が 149.210、末尾が D(Jan7) の 148.810 | 149.000 → 149.020 → `(2*148.810 + 19*149.020)/21 = 3129.000/21 = ` **`149.000`** |
| `01-07 22:00Z` | `daily_above_ema` | `left=148.810`、`right=149.000` | `148.810 > 149.000` は偽 → **`ConditionState(False)`** |
| `01-07 09:00Z` | `m15_ema` | 古い59本が 149.245、末尾が開始足の 149.350 | 種 149.245 → `(2*149.350 + 19*149.245)/21 = 3134.355/21 = ` **`149.255`** |
| `01-07 09:00Z` | `m15_above_ema` | `left=149.350`、`right=149.255` | **`ConditionState(True)`** |
| `01-07 09:00Z` | `stop_level` | 1時間足の安値20本（`[01-06 12:00Z, 01-07 08:00Z)`。当該足を除く） | 148.950（`01-06 12:00`〜`14:00` の3本）と 149.150（残り17本）の最小 → **`Price(148.950)`** |

`daily_ema` の窓が 60 本あることは第1.1節の被覆（`2014-09-30T22:00Z` 以降）で満たす。`2014-10-01` から `2015-01-06` までの平日は 70 日あり、週末は日足が存在しない（カレンダーが区間を持たない）ので 60 本を超える。したがって `WARMUP_INSUFFICIENT` にはならない。`window_bars >= 2 * period`（60 >= 40）はコンパイル時の検査 e（D05 §5.6）を通る。

**Decimal の文脈**: `alpha` は `2/21` で割り切れないが、本書の窓ではすべての更新が `e_new = (2*x + 19*e)/21` の形で 3 桁の十進数になる。したがって有効桁28・`ROUND_HALF_EVEN`（D05 §4.5）の丸めは1度も働かない。**検算値が丸めに依存しないことを人工データの設計で保証している**のがこの節の目的である。

## 3. 経路1: 遅延なしで市場状態 → 取引機会 → 確認成立 → 発注 → 約定

D05 §9.4 の経路1 に対応する。**ただし「同じ判断時点で市場状態 → 取引機会 → 確認成立まで進む」のは日足境界ではない**。理由を第3.1節で示す。

### 3.1 T = 2015-01-06T22:00Z（日足の確定。突破は成立しない）

この時刻には日足・1時間足・15分足がそろって終了する（D05 §9.3 が例に挙げる時刻）。`PublicationBatch` の `scheduled_closes` には3系列の `BarClosure` が入り、`available_bars` にも3系列の `BarKey` が入る。並びは D03 §7.1 の系列順（`(symbol, 名目長の降順, basis)`）で日足 → 1時間足 → 15分足である。

| rank | フェーズ | 何が起きるか |
|---|---|---|
| 5 | `P1_FEATURE` | `daily_ema` → `Observation[Price](value=Price(149.020), subject=BarKey(1d, 01-05 22:00Z), observation_interval=Interval[01-05 22:00Z, 01-06 22:00Z), freshness_time=01-06 22:00Z)`。`daily_above_ema` → `Observation[ConditionState](value=ConditionState(True), …同じ subject と区間…, freshness_time=01-06 22:00Z)`。`m15_ema`・`m15_above_ema`・`stop_level` も評価される |
| 6 | `P2_MARKET_STATE` | `market_state` → `Observation[MarketPermission](value=MarketPermission(allow_long=True, allow_short=False), subject=BarKey(1d, 01-05 22:00Z), observation_interval=Interval[01-05 22:00Z, 01-06 22:00Z), freshness_time=01-06 22:00Z)` |
| 7 | `P3_TRIGGER` | `entry_trigger` は `price = 149.210`（1時間足 `[21:00,22:00)` の終値）、`level = 149.300`（`latest_available(1d, 22:00Z)` が返す D(Jan6) の高値）。`149.210 > 149.300` は**偽** → 取引機会を出さない。`EvaluationRecord(outcome=Evaluated(()))`、新しい状態は `ConditionState(False)` |

`freshness_time` が `01-06 22:00Z` になる根拠（D05 §6.7）。`daily_ema` は履歴窓を読むので代表は**末尾＝いちばん新しい要素**の鮮度基準時刻であり、`freshness(1d, D(Jan6)) = bar_end = 01-06 22:00Z`。`daily_above_ema` は市場データ（鮮度 `01-06 22:00Z`）と上流出力（`Observation.freshness_time = 01-06 22:00Z`）の最小。`no_short` は市場データも上流出力も読まないので `decision_time`。`market_state` はその2つの最小。**すべて 22:00Z に揃う**ので、段階3 で鮮度の材料が正確になったことは、この判断時点では値を変えない。

**追えなかった点（本書がいちばん強く報告すること）**。`entry_trigger.level` は `MarketDataRef(USDJPY/1d/bid, HIGH)` を `LatestAvailable` で読む。日足境界では、`latest_available` が返す日足は**いま終わったばかりの日足**であり、その高値は D03 §5.1 の集約規則により**同じ時刻に終わった1時間足の高値を必ず含む**。したがって `1時間足の終値 <= その1時間足の高値 <= 日足の高値` が常に成り立ち、`price > level` は**日足境界では構造的に成立しない**。

- D05 §9.3 のケース2 の例（「T+2秒 に再開して O1 を生成し、`created_decision_time` は T+2秒」）と、§9.4 の経路1・経路5 の「同じ判断時点で取引機会が生まれる」は、**この判断時点では到達しない**。
- 上位設計書 §4.3.14 は履歴窓について「区間指定に当該足を含むか除くかを明示し、突破判定が当該足自身の高値を基準にしてしまうことを避ける」と定めている。日足高値を `LatestAvailable` で読む宣言は、日足境界でだけ同じ問題を持ち込む。`LatestAvailable` には「当該足を除く」に当たるフィールドが無い（D04 §6.1）。
- **戦略として壊れているわけではない**。上位設計書 §7.1 の B が言う「利用可能な日足高値」は、日中は**直前に完了した日足の高値**であり、その使い方（第3.2節）は問題なく追える。壊れているのは D05 §9.3・§9.4 の**例文が置いた判断時点**である。
- → 第14節 #1、**決定 Q23（2026-09-23、選択肢1）**: **宣言は変えず、D05 の例文のほうを改める**。D05 v2.2 §9.3 は「T では取引機会は生まれない」を確定として書き、取引機会が生まれるのは日足境界でない1時間足の確定（第3.2節）だと明記した。§9.4 の経路1・5 も同じ形に改めた。部品カタログは11件のままである。

### 3.2 T = 2015-01-07T09:00Z の1判断時点

1時間足 `[08:00,09:00)` と15分足 `[08:45,09:00)` が終了する。日足は終わらない（次の日足境界は 22:00Z）ので `latest_available(1d, 09:00Z)` が返すのは **D(Jan6)**（高値 149.300）である。

起動する使用箇所は `m15_ema` / `m15_above_ema` / `stop_level`（P1）、`entry_trigger`（P3）、`entry_filter`（P4）、`entry_order` / `initial_stop`（P5）である。日足の4件（`daily_ema` / `daily_above_ema` / `no_short` / `market_state`）は起動しない。`trailing` は建玉が0件なので要求を作らない（D05 §6.11）。

`step` 内の通し番号（`sequence`）は 0 から1本で振り、出力記録・遷移記録・待機の出来事・有効性の再検査が共有する（D05 §6.6。段階3 で増えた2種類を含めることは本 PR の D05 v2.1 で明記した。第14節 #10）。

| rank | フェーズ | 何が起きるか | どの型のどのフィールドに何が入るか |
|---|---|---|---|
| 0 | `EXECUTION_BAR_COMPLETE` | 建玉なし | 記録なし |
| 1 | `LEDGER_UPDATE` | 台帳 snapshot | `LedgerSnapshot(at=ProcessingPoint(09:00Z, LEDGER_UPDATE, 0), balance=Money(1000000,JPY), equity=Money(1000000,JPY), consumed=Money(0,JPY), open_position_ids=())` → 表14 |
| 2 | `ORDER_EXPIRY` | `PENDING` なし | 記録なし |
| 3 | `PUBLICATION` | 公開バッチの組み立て | `PublicationBatch(batch_id=EventId 00000010, decision_time=09:00Z, phases=BACKTEST_PHASES, available_bars=(BarKey(1h, 08:00Z), BarKey(15m, 08:45Z)), scheduled_closes=(BarClosure(BarKey(1h,08:00Z), Interval[08:00,09:00)), BarClosure(BarKey(15m,08:45Z), Interval[08:45,09:00))), runtime_events=(), admissions=(), is_run_end=False)` |
| 4 | `OPPORTUNITY_LIFECYCLE` | 待機中の要求なし、有効な機会なし | 記録なし |
| 5 | `P1_FEATURE` | `m15_ema` → `Observation[Price](Price(149.255), subject=BarKey(15m,08:45Z), observation_interval=Interval[08:45,09:00), freshness_time=09:00Z)`（`OutputId 00000001`、`sequence=0`）。`stop_level` → `Observation[Price](Price(148.950), subject=BarKey(1h,08:00Z), observation_interval=Interval[08:00,09:00), freshness_time=08:00Z)`（`00000002`、`sequence=1`）。`m15_above_ema` → `Observation[ConditionState](ConditionState(True), 同じ subject と区間, freshness_time=09:00Z)`（`00000003`、`sequence=2`）。評価順（第1.3節）で `stop_level` は `m15_above_ema` より前の段にある | `EvaluationRecord` 3件 → 表2 |
| 6 | `P2_MARKET_STATE` | 起動なし（日足は終わっていない） | 記録なし |
| 7 | `P3_TRIGGER` | `price=149.350 > level=149.300` かつ直前状態が `ConditionState(False)` → 発火。**D05 §7.4 の手順2a** で `CompiledRoles.market_state` が指す出力の最新（`01-06 22:00Z` の `Observation[MarketPermission]`、`allow_long=True`）を読み、`LONG` が許されているので遷移1 へ進む | `Opportunity(opportunity_id=OpportunityId 00000001, symbol=USDJPY, signal_interval=Interval[08:00,09:00), direction=LONG, reference_values={"breakout_level": Price(149.300)})`。`OpportunityTransition(00000001, None → OPEN, at=ProcessingPoint(09:00Z, P3_TRIGGER, 3), phase=P3_TRIGGER, reason=None)` → 表3。`OutputRecord(00000004, producer=("entry_trigger","opportunity"), payload=Opportunity(…), sequence=4)` → 表1 |
| 8 | `P4_CONFIRMATION` | 第3.3節 | |
| 9 | `P5_ORDER_INTENT` | 第3.4節 | |
| 10 | `ADMISSION` | 第3.5節 | |
| 11 | `EXECUTION_OPEN` | 第3.5節 | |
| 12 | `POST_FILL_EVALUATION` | 第2回の `step`。受付通知で遷移5。`POSITION_OPENED` を購読する使用箇所が**1件も無い**（`trailing` は足の確定で起動する）ので、評価は1件も起きない | `OpportunityTransition(00000001, ORDER_PENDING → TERMINATED, at=ProcessingPoint(09:00Z, POST_FILL_EVALUATION, 0), reason=Reason(FULFILLED_BY_ORDER_ACCEPTANCE), attempt_id=00000001)` → 表3 |
| 13 | `POST_FILL_ADMISSION` | 全数量決済の要求なし | 記録なし |

**追えた要点**: 市場状態の許可は**11時間前の日足から作られた出力**（`freshness_time=01-06 22:00Z`、`observation_interval=[01-05 22:00Z, 01-06 22:00Z)`）であり、役割フィールドには `max_age` を宣言する場所が無いので鮮度の上限は課されない（D05 §7.6）。この「古い許可をそのまま使ってよい」という帰結が、段階3 で鮮度を `Observation` に載せた目的（D05 §6.7 の理由1）と表裏である点は追えた。

**追えなかった点**: D05 §7.6 は「`Observation.freshness_time` と `observation_interval` を判断履歴に残す」と書くが、**残す先のフィールドが無い**。`OpportunityTransition` にも `ValidityRecheck` にもその欄は無い。実際には許可の出力記録が表1 に残っており、「**その発火の遷移記録の処理点より前に出た最後の出力**」として一意に復元できるが、それは本書が導いたことで文書には書かれていなかった。**判断時刻だけで絞ると足りない**。日足と1時間足が同じ判断時点で確定する場合（第3.1節・第7.2節）は、同じ `step` の P2（rank 6）で出たばかりの許可を P3（rank 7）が適用するので、判断時刻より前で切るとその更新を取りこぼす。**「前」の比べ方は出力の識別子の大小である**。出力記録は処理点を持たず（`decision_time` と `sequence` だけ）、`sequence` は `step` ごとに 0 へ戻るので、出力記録どうしを `(時刻, フェーズ順位, 通し番号)` では並べられない。使えるのは `OutputId` で、**連番 ID の採番順は run 全体で処理順に一致する**（D02 §7.3）ため、判断時点も `step` もまたいで単調に増える。したがって「適用した許可＝**その発火が作った取引機会を載せた出力記録の `OutputId` より小さい `OutputId` を持つ、許可の出力参照の最後の出力**」で一意に決まる。本書の経路では、取引機会の出力記録の直前に出た許可の出力記録が `01-06 22:00Z` の `market_state` の出力なので、この規則でそのまま引ける（本書が書く番号は相対的な見出しであり、比べるのは順序である。第1.6節）。→ 第14節 #2（設計の選択を含まないので D05 §7.6 に明記した。識別子で比べる形は独立レビューの指摘を受けて v2.2 で確定した）。

### 3.3 rank 8（`P4_CONFIRMATION`）の中身

**確認の開始足**（D05 §7.7）: `MarketDataView.latest_available(USDJPY/15m/bid, 09:00Z)` が返す足の `BarKey` = `BarKey(15m, 08:45Z)`。`OpportunityLifecycle.confirmation_start_bar` に固定する。`include_start_bar=True` なので、この足で確認評価を行う。

**確認待ちの機会を数える時点は P4 である**（D05 §7.7）。同じ `step` の P3 で生まれたばかりの O1 も対象に含む。D05 §6.11 により、確認足の確定1本につき確認待ちの機会1件ごとに1要求を作るので、要求は1件（`attempt_index=0`）である。

| 手順 | 値 | 型とフィールド |
|---|---|---|
| 1. 有効性の再検査（D05 §7.7 の末尾） | `daily_above_ema.condition` の最新出力（`01-06 22:00Z` 生成、`ConditionState(True)`）を読む → 成立 | `ValidityRecheck(opportunity_id=00000001, source=OutputRef("daily_above_ema","condition"), mode=REQUIRE_UNTIL_ORDER_REQUEST, at=ProcessingPoint(09:00Z, P4_CONFIRMATION, 5), outcome=SATISFIED, output_id=OutputId <01-06 22:00Z の `daily_above_ema` の出力>, reason=None)` → 表19 |
| 2. 確認評価 | 入力は `condition` ← `m15_above_ema.condition`（`ConditionState(True)`）、`opportunity` ← ランタイムが持つ `OpportunityLifecycle` の `Opportunity`（D05 §6.11。`RuntimeContextView` には問い合わせない） | `EvaluationRequest(request_id=RequestId 00000005, instance_id="entry_filter", trigger_names=("m15",), decision_time=09:00Z, target_interval=Interval[08:45,09:00), opportunity_id=00000001, position_id=None, attempt_index=0)` |
| 3. 部品の戻り値 | `ConfirmationOutcome(confirmed=True, reference_values={})` | 部品は成否だけを返す（D05 §4.8） |
| 4. ランタイムが組み立て | `ConfirmationResult(opportunity_id=00000001, confirmation_interval=Interval[08:45,09:00), confirmed=True)` | `OutputRecord(00000005, producer=("entry_filter","confirmation"), payload=ConfirmationResult(…), decision_time=09:00Z, available_at=09:00Z, sequence=6)` → 表1 |
| 5. 遷移10 | `OPEN → CONFIRMED` | `OpportunityTransition(00000001, OPEN → CONFIRMED, at=ProcessingPoint(09:00Z, P4_CONFIRMATION, 7), phase=P4_CONFIRMATION, reason=None)` → 表3 |
| 6. 確認試行 | `ConfirmationAttempt(opportunity_id=00000001, bar_key=BarKey(15m, 08:45Z), request_id=00000005, outcome=CONFIRMED)` | `OpportunityLifecycle.attempts` に1件 → 表18 |

**確認期限の解決**（D05 §7.7、Q19 決定）: `BarsDeadline(4)` を `WaitUntilBars(series=USDJPY/15m/bid, remaining=4)` に解決し、`OpportunityLifecycle.deadline_at` に持つ。**機会を生成した足（`[08:45,09:00)`）の次の確定足を1本目**と数えるので、1本目 `09:15Z`、2本目 `09:30Z`、3本目 `09:45Z`、4本目 `10:00Z` である。経路1 では 09:00Z に確認が成立するので期限に到達しない。

**追えなかった点**: 確認試行（`ConfirmationAttempt`）は `OpportunityLifecycle` の中にしか無く、`RuntimeStepResult` に列が無い（D05 §3 の改訂は `wait_events` と `validity_rechecks` の2つを足しただけである）。**エンジンが表18 を書くための受け渡し経路が存在しない**。T01 §13 #19（`EntryProposal` に根拠の出力 ID が無く正常経路でも `OrderRequest` を組み立てられなかった）と同じ形の**阻害要因**である。→ 第14節 #4、**決定 Q26（2026-09-23、選択肢1）**: `RuntimeStepResult` に確認試行の列（`confirmation_attempts`、既定は空）を足し、**その `step` で作った・書き換えた試行だけを毎回返す**。エンジンは主キー `(opportunity_id, bar_key)` で表18 の行を置き換える（D05 v2.2 §3・§6.2 の手順10・§7.7、D06 v1.6 §4.2・§9.2）。この決定で表18 が書けるようになり、上の表の6行目がそのまま判断履歴に残る。

**追えなかった点（順序）**: 確認結果の出力記録（`sequence=6`）と遷移10（`sequence=7`）のどちらが先かは、D05 に規則が無い。取引機会の生成については D05 §6.6 が「生成の遷移は出力記録より小さい番号になる」と明記している（§6.2 の手順6 が手順7 より前だから）が、確認は**出力の内容（`confirmed`）から遷移が決まる**ので順序が逆になる。本書は出力記録を先に置いて追った。→ 第14節 #11（D05 v2.1 で明記した）。

### 3.4 rank 9（`P5_ORDER_INTENT`）の中身

`entry_order` と `initial_stop` は `OnInputEvent("conf","confirmation")` で起動する。`confirmed=True` の確認結果は下流へ配送される（`confirmed=False` なら配送しない。D05 §7.7）。

| 手順 | 値 |
|---|---|
| 1. 有効性の再検査（`OrderRequest` 生成直前。遷移8 の判定点） | `ValidityRecheck(00000001, OutputRef("daily_above_ema","condition"), REQUIRE_UNTIL_ORDER_REQUEST, at=ProcessingPoint(09:00Z, P5_ORDER_INTENT, …), outcome=SATISFIED, output_id=…)` → 表19。**v1.2 で訂正**: v1.1 はこの再検査を手順2・3 の出力（通し番号9・10）より前の通し番号8 に置いていたが、D05 §7.3 は再検査を「`EntryProposal` を作る直前」と定めており、実装もそのとおり**手順2・3 の出力の後、手順4 の組み立ての直前**に走る。再検査 → 遷移4 の前後関係と件数は変わらない（第20節） |
| 2. `entry_order` | 要求は `EvaluationRequest(request_id=00000006, instance_id="entry_order", trigger_names=("conf",), decision_time=09:00Z, target_interval=Interval[08:45,09:00), opportunity_id=00000001, attempt_index=0)`。`target_interval` と `opportunity_id` は上流（確認評価）の要求から引き継ぐ（D05 §6.2 手順3）。出力は `OutputRecord(00000006, producer=("entry_order","intent"), payload=OrderIntent(symbol=USDJPY, direction=LONG, order_type=MARKET, price_condition=None, expiry=None), sequence=9)` |
| 3. `initial_stop` | `level` ← `stop_level.level` を `LatestAvailable` で読む（同じ `step` の P1 で出た `Price(148.950)`）。`OutputRecord(00000007, producer=("initial_stop","protection"), payload=ProtectionLevels(stop_loss=Price(148.950), take_profit=None), sequence=10)` |
| 4. 役割出力が揃う | `EntryProposal(opportunity_id=00000001, order_intent=…, protection=…, decision_time=09:00Z, intent_output_id=OutputId 00000006, protection_output_id=OutputId 00000007)` |
| 5. 遷移4 | `OpportunityTransition(00000001, CONFIRMED → ORDER_PENDING, at=ProcessingPoint(09:00Z, P5_ORDER_INTENT, 11), phase=P5_ORDER_INTENT, reason=None)` → 表3 |

**追えた要点**: 段階2 との違いは遷移4 の始点が `OPEN` ではなく `CONFIRMED` であることだけで、`EntryProposal` の組み立て（D05 §6.2 手順9）は段階2 と同じ型・同じフィールドを通る。**`initial_stop` v2 が `stop_level.level` を読めるのは、1時間足の `stop_level` と15分足の確認が同じ判断時点で揃うからではなく、`RuntimeState.latest_outputs` が最新1件を保持しているからである**（D05 §6.5）。経路2（第4節）のように確認が別の判断時点で成立する場合も、同じ経路で読める。

### 3.5 rank 10・11（受付と約定）

`AdmissionKey = (decision_time=09:00Z, request_class=ENTRY(1), strategy_priority=0, origin_seq=1, attempt_seq)`。要求は1件なので順序は自明。

| 手順 | 値 |
|---|---|
| 要求組立 | `OrderRequest(run_id=run-B, attempt_id=AttemptId 00000001, previous_attempt_id=None, account_id=ACC1, strategy_id=S2, created_at=ProcessingPoint(09:00Z, ADMISSION, 0), origin=STRATEGY, payload=EntryRequest(opportunity_id=00000001, symbol=USDJPY, side=BUY, order_type=MARKET, protection=InitialProtectionPlan(stop_loss=Price(148.950), take_profit=None, source_output_id=OutputId 00000007), exit_plan_ref=ExitPlanRef(compiled_ref=CS2, exit_instance_id="trailing"), valid_for=20分, intent_output_id=OutputId 00000006), evidence_ref=EvidenceRef(EvidenceId 00000001))` → 表4 |
| 1. balance | `B = Money(1000000, JPY)` |
| 2. 予算 | `trial_budget = 20000`、`U = 0`、`account_remaining = 200000`、`admission_budget = 20000` |
| 3. 参照価格 | 直前に完了した執行足 `15m [08:45,09:00)` の終値 bid `149.350` → 買いは ask `149.370`。`ReferenceQuote(price=Price(149.370), basis=ASK, observed_at=09:00Z, source_bar=BarKey(15m, 08:45Z), derived_from_spread=True)` |
| 4. 保護水準の妥当性 | 買いの損切り `148.950 < 判断時 bid 149.350` → 合格 |
| 5. 丸めと予約式 | `S = 148.950`（刻み `0.001` に整列済み）、`Δ = 0.050`、`P_limit = 149.370 + 0.050 = 149.420`、`d × (P_limit − S) = 0.470 > 0` |
| 6. 数量 | `C(Q) = (往復手数料 0.001×2 + 損切り決済の slippage 0.010) × Q = 0.012 Q`。`R(Q) = 0.470 Q + 0.012 Q = 0.482 Q <= 20000` → `Q <= 41493.7…` → 数量刻み 1000 で切り下げ → `Quantity(41000)` |
| 7. 再検査 | `R(41000) = 0.482 × 41000 = 19762` 円 <= 20000。同時保持枠も合格 |
| 8. 記録 | `RiskAssessment(assessment_id=EvidenceId 00000002, attempt_id=00000001, reached_step=8, adverse_fill_limit=Price(149.420), stop_before_rounding=stop_after_rounding=Price(148.950), quantity_step=Decimal("1000"), quantity=Quantity(41000), cost_budget=Money(492,JPY), reservation_amount=Money(19762,JPY), …)` → 表6 |

受付の確定単位: `AttemptAccepted(00000001, OrderId 00000001, RiskAssessmentRef(EvidenceId 00000002))` → 表5。`AcceptedOrder(order_id=00000001, quantity=Quantity(41000), expires_at=09:20Z, execution=ExecutionCommitment(eligibility=ScheduledOpen(BarKey(15m, 09:00Z), 09:00Z)), terms=AcceptedEntryTerms(initial_stop=Price(148.950), reservation_id=ReservationId 00000001, reference_quote=…, adverse_fill_limit=Price(149.420)))` → 表7。`RiskReservation(reservation_id=00000001, amount=Money(19762,JPY))` → 表10。

約定（rank 11、D06 §7.5 の6手順）:

| 項目 | 値 |
|---|---|
| 約定価格 | `[09:00,09:15)` の `open` bid `149.360` → ask `149.380` → `entry_slippage` を不利方向へ → **`Price(149.390)`** |
| 約定ずれ | `max(0, +1 × (149.390 − 149.370)) = 0.020 <= 0.050` → 緊急決済なし |
| 費用 | `CostEntry(COMMISSION, Money(41,JPY))`、`CostEntry(SLIPPAGE_IN_PRICE, Money(410,JPY))`、`CostEntry(SPREAD_IN_PRICE, Money(820,JPY))` |
| 建玉 | `Position(position_id=PositionId 00000001, side=BUY, quantity=Quantity(41000), entry_price=Price(149.390), protection=ProtectionState(version=1, stop_loss=Price(148.950), take_profit=None, effective_from=BarKey(15m, 09:00Z), owner_instance_id="trailing"), status=OPEN)` → 表11 |
| 実リスクの計測 | `RiskMeasurement(measured=Money(18040,JPY), allocated=Money(19762,JPY), basis="entry_price - initial_stop")`（`(149.390 − 148.950) × 41000 = 18040`） |
| balance | 手数料 41 円だけを控除して `Money(999959, JPY)` |
| 台帳 snapshot | 含み損益の評価価格は直前に完了した執行足 `[08:45,09:00)` の終値 bid `149.350`（D06 §8.1）。`(149.350 − 149.390) × 41000 = −1640` → `LedgerSnapshot(at=ProcessingPoint(09:00Z, EXECUTION_OPEN, 1), balance=Money(999959,JPY), equity=Money(998319,JPY), consumed=Money(19762,JPY), open_position_ids=(00000001,))` → 表14 |

**追えた要点**: 検証戦略 B は利確を出す部品を持たない（`exit` 役割はトレーリング）ので、**建玉は初期の利確を持たないまま開く**。D06 §8.3 は「管理要求が1件も返らなかった建玉は、初期の利確を持たないまま継続する」と定めており、架空の利確水準を埋めない。D05 §8 が排除したのは「**利確を出す戦略なのに**次足まで利確が無い」構成であり、利確を出さない戦略はこれに当たらない。この読み分けは追えたが、文書には書かれていない（第14節 #18）。

### 3.6 この経路が残す trace 行と D07 への影響

| 表 | 行 |
|---|---|
| 1 `OUTPUTS` | 7件（`00000001`〜`00000007`）。うち `00000001`〜`00000003` の payload は `Observation[...]`、`00000004` は `Opportunity`、`00000005` は `ConfirmationResult`、`00000006`・`00000007` は `COMMAND` の内容型 |
| 2 `EVALUATIONS` | 7件。第2回の `step` では0件（購読する使用箇所が無い） |
| 3 `OPPORTUNITY_TRANSITIONS` | 4件（遷移1・10・4・5） |
| 18 `CONFIRMATION_ATTEMPTS` | 1件（`(00000001, BarKey(15m, 08:45Z))` → `CONFIRMED`） |
| 19 `VALIDITY_RECHECKS` | 2件（P4 と P5 直前。どちらも `SATISFIED`） |
| 16 `WAIT_EVENTS` / 17 `INPUT_SUBSTITUTIONS` | 0件（遅延なしのケースでは待機も遡りも起きない） |
| 4〜15 | T01 経路1 と同じ形。利確の決済が無いぶん表4・5・7・8・9 の行が1件ずつ少ない |

D07 への影響: 本経路だけでは完了取引が無い（決済は第10節）。段階2 の指標15件のうち、この判断時点で変わるのは `MAX_DRAWDOWN_MTM` の走査対象（表14 に `equity=998319` が入る）だけである。**D07 §6.1 の「評価の結果区分別の集計」は段階3 で区分が2つ増える**（`Waiting` と `Superseded`）が、この経路では現れない（第14節 #7）。

## 4. 経路2: 開始足で未確認、次の15分足で成立

D05 §9.4 の経路2。経路1 と**日足・1時間足を共有し、15分足だけを差し替える**。

| 確認足 | 15分足の終値 | `m15_ema` | `m15_above_ema` | 判断時刻 | `ConfirmationAttempt.outcome` |
|---|---|---|---|---|---|
| `[08:45,09:00)`（開始足） | 149.161 | `(2*149.161 + 19*149.245)/21 = 3133.977/21 = ` **149.237** | `False` | 09:00Z | `NOT_CONFIRMED` |
| `[09:00,09:15)`（1本目） | 149.426 | `(2*149.426 + 19*149.237)/21 = 3134.355/21 = ` **149.255** | `True` | 09:15Z | `CONFIRMED` |

09:00Z に起きること（経路1 との差分だけ）:

- P4 で `ConfirmationResult(opportunity_id=00000001, confirmation_interval=Interval[08:45,09:00), confirmed=False)` が**出力として出る**（D05 §4.8。未成立を入力不足・期限切れ・追い越しと区別するため）。`OutputSink.emit` は行い表1 に残るが、`OnInputEvent` の起動判定の対象から外す（D05 §7.7）ので `entry_order` と `initial_stop` は起動しない。
- 機会は `OPEN` のまま。遷移は起きない。`ConfirmationAttempt((00000001, BarKey(15m,08:45Z)), NOT_CONFIRMED)`。

09:15Z に起きること:

| rank | 何が起きるか |
|---|---|
| 3 | `PublicationBatch(available_bars=(BarKey(15m, 09:00Z),), scheduled_closes=(BarClosure(BarKey(15m,09:00Z), Interval[09:00,09:15)),))`。**1時間足は終わらない** |
| 4 | 確認期限の判定。`remaining` は 4 → 3（15分足の `ScheduledBoundary` を1本受けた）。到達しない |
| 5 | `m15_ema` → `Observation[Price](Price(149.255), subject=BarKey(15m,09:00Z), observation_interval=Interval[09:00,09:15), freshness_time=09:15Z)`、`m15_above_ema` → `True`。**`stop_level` は起動しない**（1時間足の確定が無い） |
| 7 | `entry_trigger` は起動しない |
| 8 | `entry_filter` が O1 について評価。`target_interval=Interval[09:00,09:15)` → `ConfirmationResult(00000001, Interval[09:00,09:15), True)` → 遷移10 |
| 9 | `entry_order` と `initial_stop` が確認結果の配送で起動。`initial_stop.level` は `latest_outputs` から `stop_level` の 09:00Z の出力（`Price(148.950)`、`freshness_time=08:00Z`）を読む。遷移4 |
| 10・11 | 参照価格は直前に完了した執行足 `[09:00,09:15)` の終値 bid `149.426` → ask `149.446`。約定は `[09:15,09:30)` の始値。**v1.2 で訂正**: v1.1 は経路1 の表の終値 `149.450` を書いていたが、経路2 はこの足を上の表の `149.426` に差し替えている（本書の内部の食い違い。受入れテストは経路2 を通さないので実測では確かめていない。第20節） |

**追えた要点**: 確認が別の判断時点で成立しても、`initial_stop` が読む損切り水準は**1時間足で計算した最後の値**である。`max_age` を宣言していないので古さは判定に効かず、`Observation.freshness_time=08:00Z` が判断履歴に残るだけである。「確認を待つほど損切り水準が古くなる」ことは**判断履歴から読めるが、宣言では制限できない**。これは設計どおり（読み取り条件は契約が固定する。D04 §4.1）であり、未記述ではない。

## 5. 経路3: 確認期限（15分足4本）に到達して EXPIRED

D05 §9.4 の経路3。経路2 と同じく15分足だけを差し替え、4本とも未成立にする。

| 確認足 | 終値 | `m15_ema` | 判断時刻 | `ConfirmationAttempt.outcome` |
|---|---|---|---|---|
| `[08:45,09:00)`（開始足） | 149.161 | 149.237 | 09:00Z | `NOT_CONFIRMED` |
| `[09:00,09:15)`（1本目） | 149.153 | `(2*149.153 + 19*149.237)/21 = 3133.809/21 = ` 149.229 | 09:15Z | `NOT_CONFIRMED` |
| `[09:15,09:30)`（2本目） | 149.145 | `(2*149.145 + 19*149.229)/21 = 3133.641/21 = ` 149.221 | 09:30Z | `NOT_CONFIRMED` |
| `[09:30,09:45)`（3本目） | 149.137 | `(2*149.137 + 19*149.221)/21 = 3133.473/21 = ` 149.213 | 09:45Z | `NOT_CONFIRMED` |
| `[09:45,10:00)`（4本目） | — | — | 10:00Z | **確認評価は行われない** |

10:00Z の判断時点:

| rank | 何が起きるか |
|---|---|
| 4 | `OPPORTUNITY_LIFECYCLE`。15分足の `ScheduledBoundary` を受けて `remaining` が 1 → 0。**期限に到達**。`OpportunityTransition(00000001, OPEN → TERMINATED, at=ProcessingPoint(10:00Z, OPPORTUNITY_LIFECYCLE, 0), phase=OPPORTUNITY_LIFECYCLE, reason=Reason(EXPIRED))` → 表3 |
| 8 | `P4_CONFIRMATION`。確認待ちの機会が0件なので要求を作らない（D05 §6.11）。**4本目の確認足は確認に使われない** |

**追えた要点（そして未記述）**: 期限の判定（rank 4）が確認の評価（rank 8）より先に来る（D05 §7.7 の「判定の順序」、D06 §4.1）ので、**`bars=n` で数えた n 本目の確認足は確認に使われない**。結果として、`include_start_bar=True` の戦略が実際に確認を試す回数は **n 回**（開始足＋1〜n-1本目）、`include_start_bar=False` なら **n-1 回**になる。Q19 の決定理由（「宣言を読んで確認の回数が分かる形に揃えた」）はこの数え方で成り立つが、**その対応が D05 §7.7 に書かれていない**。→ 第14節 #3（設計の選択を含まないので D05 v2.1 で明記した）。

`ConfirmationAttempt` は4件（開始足＋1〜3本目）で、`EXPIRED` の行は表18 に増えない（期限は遷移記録 表3 が持つ。D05 §4.8 の対応表のとおり）。

D07 への影響: `OPPORTUNITY_TRANSITIONS` の終端理由別の集計（D07 §6.1）に `EXPIRED` が1件増える。注文は1件も出ないので表4 以降に行は増えない。

## 6. 経路4: 確認待ちのあいだに日足の条件が崩れる（**到達しない**）

D05 §9.4 の経路4（「確認待ちのあいだに日足の条件が崩れ `MARKET_STATE_INVALIDATED` で終端する」）を追おうとして、**この宣言では到達しないこと**が分かった。

導出は3段である。

1. 有効性束縛が読むのは `daily_above_ema.condition` であり、この出力が新しく出るのは**日足の確定（`OnBarClose("d1")`）**の判断時点だけである（待機からの再開を含む。第7節）。日足境界は冬時間で 22:00Z、夏時間で 21:00Z であり、**いずれも1時間境界の上にある**（D03 §3.2 の `SessionAlignment`）。
2. 取引機会は `entry_trigger`（`OnBarClose("h1")`）が作るので、生成の判断時刻は必ず1時間境界である。確認期限は `BarsDeadline(bars=4)` ＝ 15分足4本 ＝ ちょうど1時間なので、**機会が有効でいられるのは生成時刻から次の1時間境界まで**である。
3. したがって、確認待ちのあいだに日足の確定が入りうるのは「生成時刻が日足境界の1時間前」の場合だけで、そのとき日足の確定は**4本目の確認足＝期限の足と同じ判断時点**に来る。期限は rank 4 で、有効性の再検査は rank 8（確認評価のたび）と rank 9 の直前なので、**期限が先に勝って `EXPIRED` で終端する**（D05 §7.7 の「判定の順序」）。

本書の人工データで確かめると、`01-07 21:00Z` に生成した機会は `remaining` が `21:15Z`（1）、`21:30Z`（2）、`21:45Z`（3）、`22:00Z`（4）と減り、`22:00Z` の rank 4 で期限に到達する。同じ `22:00Z` の rank 5〜6 で `daily_above_ema` が `ConditionState(False)` を出すが、そのときには機会は既に `TERMINATED` である。

**この経路は D05 §9.4 が段階3 で追うと宣言した9経路の1つなので、到達しないことは設計側の判断を要する**。→ 第14節 #6、**決定 Q24（2026-09-23、選択肢1）**: **検証戦略 B の宣言も部品も変えず、経路4 を「段階3 の宣言では到達しない」という到達可否の記述に改める**（D05 v2.2 §9.4）。決定記録 ADR-0031 が求める「確認待ち中の条件再検査」そのものは、**確認期限だけを長くした小さな戦略を使う意味論テスト**で、再検査の記録（`ValidityRecheck`）の4区分をすべて通して検証する（D08 v1.1 §13.2 の未整備6）。**「読めず失敗した」の区分は有効性束縛の欠損方針が `Error` の宣言でしか生じない**（D05 §7.3。検証戦略 B は `SkipEvaluation` を宣言している）ので、その意味論テストは欠損方針が違う2つの宣言を用意する。確認期限を8本に延ばして到達させる案を採らなかったのは、段階3 の受入れテストの人工データまで作り直しになる一方、得られるのが経路1本の実演だけだからである。

なお、`MARKET_STATE_INVALIDATED` という終端理由そのものは経路9（第11節）で到達する。D05 §7.6 が同じ語を2つの場面（生成時に許可が無い／確認待ちのあいだに条件が崩れる）に使い、`OpportunityTransition.from_state` で区別すると定めた設計のうち、**段階3 で実際に到達するのは `from_state=None` の側だけ**である。

## 7. 経路5: 日足が2秒遅れ、待機して再開する（遅延シナリオ2）

D05 §9.4 の経路5、§9.3 のケース2。判断時刻 T = `2015-01-07T22:00Z`、`DelayScenario(id="d1_2s")`。

### 7.1 T = 2015-01-07T22:00Z（待機に入る）

この時刻に発生するイベント（D03 §7.1）は `ExecutionBarComplete(15m, [21:45,22:00))`、`Publication(1h, [21:00,22:00))`、`Publication(15m, [21:45,22:00))`、`ScheduledBoundary` 3件（日足・1時間足・15分足）、`ExecutionOpen(15m, 22:00Z)` である。**日足の `Publication` は 22:00:02Z へずれる**（`available_at = bar_end + 2秒`。D03 §3.6）。

`PublicationBatch(batch_id=EventId 00000040, decision_time=22:00Z, available_bars=(BarKey(1h, 21:00Z), BarKey(15m, 21:45Z)), scheduled_closes=(BarClosure(BarKey(1d, 01-06 22:00Z), Interval[01-06 22:00Z, 01-07 22:00Z)), BarClosure(BarKey(1h,21:00Z), Interval[21:00,22:00)), BarClosure(BarKey(15m,21:45Z), Interval[21:45,22:00))), runtime_events=(), admissions=(), is_run_end=False)`

`OnBarClose` は `ScheduledBoundary` に結び付く（D03 §7.2）ので、日足が届いていなくても日足の4使用箇所は**起動する**。

| 評価順 | 使用箇所 | 何が起きるか | 記録 |
|---|---|---|---|
| 1 | `daily_ema` | `history(1d, BarsWindow(60), 22:00Z, end_offset_bars=0)` が窓の末尾の足を読めず `INPUT_MISSING_OR_INVALID`。この理由は待機に入れる（D05 §6.8 の表） | `EvaluationRecord(request_id=RequestId 00000041, evaluation_id=…, instance_id="daily_ema", trigger_names=("d1",), decision_time=22:00Z, target_interval=Interval[01-06 22:00Z, 01-07 22:00Z), attempt_index=0, outcome=Waiting(diagnoses=(MissingInputDiagnosis("prices", ResolvedMarketSource(USDJPY/1d/bid, CLOSE), INPUT_MISSING_OR_INVALID),), deadline_at=WaitUntilBars(USDJPY/1d/bid, remaining=1)))` → 表2。`WaitEvent(00000041, WAIT_STARTED, at=ProcessingPoint(22:00Z, P1_FEATURE, 0), reason=None, arrived=())` → 表16 |
| — | （待機記録） | `WaitingRequest(request=…, pinned_bars={"prices": BarKey(1d, 01-06 22:00Z)}, pinned_events={}, opportunity=None, missing=(…), started_at=ProcessingPoint(22:00Z, P1_FEATURE, 0), deadline_at=WaitUntilBars(USDJPY/1d/bid, 1), on_deadline=SKIP_EVALUATION, on_superseded=EXPIRE_REQUEST)`。**`pinned_bars` は「オフセットを適用する前の基準足」**（D05 §6.8）であり、`exclude_latest_bars=0` なのでそのまま窓の末尾になる | 判断履歴には出ない（`RuntimeState` の中） |
| 2 | `m15_ema` | 15分足は遅れていないので通常どおり評価 | `Evaluated` |
| 3 | `no_short` | 入力を持たないので待機しない。`Observation[ConditionState](ConditionState(False), subject=BarKey(1d, 01-06 22:00Z), observation_interval=Interval[01-06 22:00Z, 01-07 22:00Z), freshness_time=22:00Z)` | `Evaluated` |
| 4 | `stop_level` | 通常どおり評価 | `Evaluated` |
| 5 | `daily_above_ema` | `left`（日足終値）は `LATEST_BAR_UNAVAILABLE`、`right`（`daily_ema.value`）は上流が待機中なので「まだ出ていない」＝`INPUT_MISSING_OR_INVALID`（D05 §6.8 の「待機の伝播」）。どちらも `WaitForInput` → `Waiting`。期限は**最初に足りなくなった入力の系列**で数える → `WaitUntilBars(USDJPY/1d/bid, 1)` | `Waiting(diagnoses=2件, …)` → 表2、`WaitEvent(WAIT_STARTED)` → 表16 |
| 6 | `m15_above_ema` | 15分足は遅れていないので通常どおり評価 | `Evaluated` |
| 7 | `market_state` | `long_allowed` が「まだ出ていない」→ `Waiting`。`short_allowed` は読めている | `Waiting(diagnoses=1件, deadline_at=…)` → 表2 |
| 8 | `entry_trigger` | 2つの理由で待機する。(a) 自身の `level`（日足高値）が `LATEST_BAR_UNAVAILABLE` で `on_missing=WaitForInput`、(b) D05 §7.6 の「市場状態が待機中なら、取引機会を出す評価も待機する」 | `Waiting(diagnoses=1件, deadline_at=WaitUntilBars(USDJPY/1d/bid, 1))` → 表2。`pinned_bars={"price": BarKey(1h, 21:00Z), "level": BarKey(1d, 01-06 22:00Z)}` |
| 9 | `entry_filter` | 確認待ちの機会が0件 → 要求なし | 記録なし |
| 12 | `trailing` | 建玉が0件（P1 は `01-07 13:15Z` に決済済み。第10節） → 要求なし | 記録なし |

**追えなかった点（2件）**:

- `market_state` が待機に入るとき、足りない入力は `long_allowed` **だけ**であり、その接続元は**出力参照**である。`BarsDeadline(1)` を `WaitUntilBars(series=その入力の系列, remaining=1)` へ解決する規則（D05 §6.8）は市場データ参照を前提にしており、**出力参照の入力には系列が無い**。本書は「その使用箇所の起動条件の系列（日足）」として追ったが、規則は書かれていない。→ 第14節 #8、**決定 Q28（2026-09-23、選択肢1）**: **その使用箇所の起動条件（`OnBarClose`）の系列で数える**（D05 v2.2 §6.8）。本書が追ったとおりであり、`market_state` の期限は日足で数え、上流の `daily_ema` の期限と一致する。なお、**その使用箇所が系列の違う足の確定を2件以上宣言している場合は、コンパイル時に拒否する**（**決定 Q30**、2026-09-23、選択肢1。第15.2節、D05 v2.2 §5.6 の検査 h、D04 v1.11 の検査 #15）。検証戦略 B の該当2件はどちらも1系列でしか起動しないので、**本書の追跡結果は変わらない**。
- `entry_trigger` は自身の入力の待機（`level` の `WaitForInput`）と、市場状態の連鎖による待機（D05 §7.6）の**両方**が成立している。§7.6 は「期限と `on_superseded` は、その連鎖の起点になった待機が宣言したものを引き継ぐ」と定めるが、**自身の待機が同時に成立しているときにどちらを使うか**は書かれていない。検証戦略 B では両方とも `BarsDeadline(1)` の日足・`EXPIRE_REQUEST` に解決するので実行結果は変わらない。→ 第14節 #9。

### 7.2 T = 2015-01-07T22:00:02Z（再開する）

`Publication(1d, BarKey(1d, 01-06 22:00Z), available_at=22:00:02Z)` だけが発生する判断時点である。`PublicationBatch(batch_id=EventId 00000050, decision_time=22:00:02Z, available_bars=(BarKey(1d, 01-06 22:00Z),), scheduled_closes=(), runtime_events=(), admissions=(), is_run_end=False)`。**`scheduled_closes` が空なので `OnBarClose` は1件も起動せず、新しい評価要求は作られない**。動くのは待機中の要求だけである。

| rank | 何が起きるか | 記録 |
|---|---|---|
| 4 | `OPPORTUNITY_LIFECYCLE`。待機中の各要求について市場データの到着を検査（D05 §6.8 手順1）。`daily_ema`（`prices`）、`daily_above_ema`（`left`）、`entry_trigger`（`level`）の市場データ入力が `available_bars` に含まれる系列の足である → 到着。期限・追い越し・失効はいずれも不成立 | `WaitEvent(00000041, INPUT_ARRIVED, at=ProcessingPoint(22:00:02Z, OPPORTUNITY_LIFECYCLE, 0), arrived=("prices",))` ほか → 表16。**`arrived` が入力名の `tuple` なので、1つの処理点で届いた入力をまとめて1件とする**（D05 v2.1 で明記。第14節 #13） |
| 5 | `P1_FEATURE`。`daily_ema` が再開。`history_ending_at(USDJPY/1d/bid, BarsWindow(60), base_bar_start=01-06 22:00Z, at=22:00:02Z, end_offset_bars=0)` で**固定した対象足を末尾とする窓**を読み直す（D05 §6.8 手順4、D03 §6.2 v1.6）。`at` は**再開した判断時刻**であり、元の判断時刻へ戻さない | `WaitEvent(00000041, RESUMED, at=ProcessingPoint(22:00:02Z, P1_FEATURE, 1))` → 表16。`EvaluationRecord(request_id=00000041, evaluation_id=<新しい値>, decision_time=22:00:02Z, target_interval=Interval[01-06 22:00Z, 01-07 22:00Z), outcome=Evaluated((OutputId …,)))` → 表2。**`request_id` は元のまま、`evaluation_id` は新しく採番**（D05 §6.8） |
| 5 | 出力 | `OutputRecord(producer=("daily_ema","value"), payload=Observation[Price](value=Price(149.000), subject=BarKey(1d, 01-06 22:00Z), observation_interval=Interval[01-06 22:00Z, 01-07 22:00Z), freshness_time=01-07 22:00Z), decision_time=22:00:02Z, available_at=22:00:02Z)` → 表1。**`decision_time` は 22:00:02Z、`freshness_time` は 22:00Z** であり、上位設計書 §4.3.15 の例とそのまま同じ形になる |
| 5 | `daily_above_ema` が同じ `step` で再開。上流が評価順の前に出力を出したので `right` が読める（D05 §6.8 手順1 の「出力参照の再開は評価の段で判定する」）。`left` は `bar(USDJPY/1d/bid, 01-06 22:00Z, 22:00:02Z)` で固定した足を読む → 終値 `148.810` | `Observation[ConditionState](ConditionState(False), subject=BarKey(1d, 01-06 22:00Z), observation_interval=Interval[01-06 22:00Z, 01-07 22:00Z), freshness_time=01-07 22:00Z)` |
| 6 | `P2_MARKET_STATE`。`market_state` が再開 | `Observation[MarketPermission](MarketPermission(allow_long=False, allow_short=False), …, freshness_time=01-07 22:00Z)` |
| 7 | `P3_TRIGGER`。`entry_trigger` が再開。`price` は `bar(1h, 21:00Z, 22:00:02Z)` → `148.810`、`level` は `bar(1d, 01-06 22:00Z, 22:00:02Z)` → `149.520`。`148.810 > 149.520` は偽 | `EvaluationRecord(request_id=<元の値>, outcome=Evaluated(()))`、新しい状態は `ConditionState(False)`。`WaitEvent(RESUMED)` |

**追えた要点**: 待機 → 到着 → 再開の連鎖（D05 §6.8 の手順1〜5）は、型とフィールドがすべてそろっており**1つも欠けずに追えた**。とくに、(a) 日足の連鎖3段が**同じ判断時点で**評価順に沿って1段ずつ進むこと、(b) 市場入力を**固定した対象足**で読み直すのに `history_ending_at` と `bar` の2操作で足りること、(c) 現在状態の入力（`CurrentContext`）が1つも無いので再開時の読み直しが起きないこと、の3点は宣言と規則から一意に決まった。

### 7.3 §9.3 ケース2 の記述との食い違い

D05 §9.3 のケース2 は、この再開で「取引機会 O1 が生成され、`created_decision_time` は T+2秒、`signal_interval` は T で終わる1時間足の区間」になると書いている。**本書の追跡ではそうならない**。第3.1節の導出のとおり、再開後に読む日足高値は**固定した対象足＝いま終わった日足**の高値であり、同じ判断時点で終わった1時間足の終値がそれを超えることはない。遅延の有無に関係なく、`pinned_bars` が指すのは `expected_latest_key` が返す足だからである。

したがって §9.3 のケース2 の例は、**待機と再開の仕組みは正しく描いているが、その結果として取引機会が生まれるという部分だけが成り立たない**。→ 第14節 #1、**決定 Q23（2026-09-23、選択肢1）**: D05 v2.2 §9.3 のケース2 は「このケースが追うのは待機と再開までである」と改め、取引機会が待機の後に生まれる場合（日足境界でない1時間足の確定で日足が遅れて届いた場合）の `created_decision_time` と `signal_interval` の決まり方を一般の規則として残した。

## 8. 経路6: 待機期限を超え Skipped で決着する（遅延シナリオ3）

D05 §9.4 の経路6、§9.3 のケース3。`DelayScenario(id="d1_25h")`（日足系列に25時間の固定遅延）。

| 判断時刻 | 何が起きるか |
|---|---|
| `01-07 22:00Z` | 第7.1節と同じ。日足の4使用箇所と `entry_trigger` が待機に入る。`deadline_at=WaitUntilBars(USDJPY/1d/bid, remaining=1)` |
| `01-07 23:00Z` | 1時間足が確定。`entry_trigger` の待機要求は**追い越される**（第9節と同じ）。日足の待機要求は対象系列が日足なので追い越されない |
| `01-08 22:00Z` | 日足の `ScheduledBoundary`（D(Jan8)）を受けて `remaining` が 1 → 0。**期限に到達** |

`01-08 22:00Z` の rank 4 で起きること:

| 記録 | 値 |
|---|---|
| `WaitEvent` | `WaitEvent(00000041, DEADLINE_REACHED, at=ProcessingPoint(01-08 22:00Z, OPPORTUNITY_LIFECYCLE, 0), reason=None, arrived=())` → 表16 |
| 評価記録 | `on_deadline=SKIP_EVALUATION` なので `EvaluationRecord(request_id=00000041, evaluation_id=<新しい値>, decision_time=01-08 22:00Z, target_interval=Interval[01-06 22:00Z, 01-07 22:00Z), outcome=Skipped(diagnoses=(MissingInputDiagnosis("prices", …, INPUT_MISSING_OR_INVALID),)))` → 表2 |
| 連鎖 | `daily_above_ema` と `market_state` も同じ判断時点で期限に到達し `Skipped` で決着する。**日足が 01-08 23:00Z に届いてもこれらの要求は復活しない**（上位 §4.3.14） |
| 取引機会 | この判断時点の注文数は 0。`EntryProposal` は1件も出ない |
| 同じ判断時点の新しい要求 | `01-08 22:00Z` は日足 D(Jan8) の予定境界でもあるので、日足の4使用箇所に**新しい評価要求**が作られ、D(Jan8) も25時間遅れているのでその要求もまた待機に入る（`WaitEvent(WAIT_STARTED)`） |

**追えた要点**: 「期限は `ScheduledBoundary` で数え、`Publication` では数えない」（D05 §6.8）ことが、この経路で実際に効いている。日足が1本も届かないので `Publication` では1度も数えられず、予定境界だけで期限に到達する。

**なぜケース3 を系列全体の遅延にしたか**（第1.4節の再掲）。1本だけを遅らせる `InjectedBarDelay` を使うと、`01-08 22:00Z` に D(Jan8) が公開されるので、**同じ判断時点で期限の到達と追い越し（D05 §6.10）の両方が成立する**。D05 §6.8 の手順2 は「期限と追い越しと失効を検査する」と並べるだけで、**同時に成立したときにどちらの記録が残るか**（`DEADLINE_REACHED` ＋ `Skipped` か、`SUPERSEDED` ＋ `Superseded` か）を決めていない。→ 第14節 #12、**決定 Q29（2026-09-23、選択肢1）**: **既に書かれている並び（期限 → 追い越し → 失効）を判定順とし、期限が勝つ**（D05 v2.2 §6.8 の手順2）。したがって1本だけを遅らせた場合でも記録は `DEADLINE_REACHED` ＋ `Skipped` に定まる。期限は宣言が決めた上限で、追い越しは到着順が決める事象なので、宣言が決めたほうを優先する。

D07 への影響: `EVALUATIONS` の結果区分別の集計（D07 §6.1）に、段階2 に無い `WAITING` と、**同じ `request_id` に対する2件目の評価記録**が現れる。D07 §6.1 は要求ではなく評価記録を数えるので、待機して決着した要求は**2回数えられる**。これは D07 の集計の意味の問題であり、D07 への引き渡しとして第14節 #7 に挙げた。

## 9. 経路7: 1時間足の待機要求が追い越される（遅延シナリオ4）

D05 §9.4 の経路7、§9.3 のケース4。`DelayScenario(id="d1_bar_hold")`（D(Jan7) だけを25時間遅らせる）。

| 判断時刻 | 何が起きるか |
|---|---|
| `01-07 22:00Z` | 第7.1節と同じ。`entry_trigger` の要求 `RequestId 00000048` が待機に入る。`pinned_bars={"price": BarKey(1h, 21:00Z), "level": BarKey(1d, 01-06 22:00Z)}`、`on_superseded=EXPIRE_REQUEST` |
| `01-07 23:00Z` | 1時間足 `[22:00,23:00)` が確定して公開される |

`01-07 23:00Z` の rank 4（D05 §6.10）:

1. 追い越しの判定の対象系列は、**要求の `target_interval` を与えた系列**、すなわち1時間足である（`entry_trigger` の起動条件は `OnBarClose("h1")`）。
2. `PublicationBatch.available_bars` に `BarKey(1h, 22:00Z)` が含まれる。固定した足 `BarKey(1h, 21:00Z)` より新しい → **追い越し成立**。
3. **新しい要求を作るのは追い越しの判定より後**である（D05 §6.10 の4）。

| 記録 | 値 |
|---|---|
| 評価記録 | `EvaluationRecord(request_id=00000048, evaluation_id=<新しい値>, instance_id="entry_trigger", decision_time=01-07 23:00Z, target_interval=Interval[21:00,22:00), outcome=Superseded(by_request_id=RequestId 00000055))` → 表2 |
| 待機の出来事 | `WaitEvent(00000048, SUPERSEDED, at=ProcessingPoint(23:00Z, OPPORTUNITY_LIFECYCLE, 0), reason=Reason(REQUEST_SUPERSEDED), arrived=())` → 表16 |
| 新しい要求 | `EvaluationRequest(request_id=00000055, instance_id="entry_trigger", trigger_names=("h1",), decision_time=23:00Z, target_interval=Interval[22:00,23:00), attempt_index=0)`。日足はまだ届いていないので**この要求もまた待機に入る**（`WaitEvent(00000055, WAIT_STARTED)`） |
| 日足の連鎖 | `daily_ema` などの要求は対象系列が日足であり、新しい日足は公開されていないので**追い越されない**。期限（日足1本）まで待ち続ける |

**追えた要点**: 「古い突破を後から実行しない」（上位 §4.3.14 の Trigger の標準方針）が、`on_superseded=EXPIRE_REQUEST` の具体値として実際に働いた。`by_request_id` が押しのけた側を指すので、判断履歴から「どの足の問いがどの足の問いに置き換わったか」を辿れる。

**1本飛ばしの追い越し**（D05 §6.10 の2）も同じ経路で追える。`01-07 23:00Z` の判断時点が起きずに `01-08 00:00Z` まで飛んだ場合、`21:00Z` と `22:00Z` を固定した2件の要求が同時に追い越され、どちらの `by_request_id` にも**いちばん新しい足の要求**が入る。本書の人工データでは判断時点が飛ばないので、この枝は紙上で確認しただけで数値は置かない。

## 10. 経路8: 建玉の保有中のトレーリング

D05 §9.4 の経路8。第3節で開いた建玉 P1（`entry_price=149.390`、`quantity=41000`、`stop_loss=148.950`）を、1時間足の確定ごとに `trailing` が評価する。

`trailing` は `OnBarClose("h1")` で起動し、`RuntimeInputRef(POSITION)` を読むので、**開いている建玉1件につき1要求**を作る（D05 §6.11）。建玉が0件の判断時点では要求を作らない。

| 判断時刻 | `stop_level` の窓（当該足を除く20本） | `stop_level.level` | 現在の `effective_stop_loss` | `trailing` の出力 |
|---|---|---|---|---|
| `01-07 10:00Z` | `[01-06 13:00Z, 01-07 09:00Z)` | `Price(148.950)` | `Price(148.950)` | **出力なし**（`148.950 > 148.950` は偽） |
| `01-07 11:00Z` | `[01-06 14:00Z, 01-07 10:00Z)` | `Price(148.950)` | `Price(148.950)` | 出力なし |
| `01-07 12:00Z` | `[01-06 15:00Z, 01-07 11:00Z)` | `Price(149.150)` | `Price(148.950)` | **`UpdateStop(Price(149.150))`** |

`01-07 12:00Z` の判断時点:

| rank | 何が起きるか | 記録 |
|---|---|---|
| 5 | `stop_level` → `Observation[Price](Price(149.150), subject=BarKey(1h, 11:00Z), observation_interval=Interval[11:00,12:00), freshness_time=11:00Z)`。**`freshness_time` が 12:00Z ではなく 11:00Z になる**のは、`extreme_price` v1 が当該足を除いて読む（`exclude_latest_bars=1`）ため、窓の末尾が `[10:00,11:00)` の足になるからである。履歴窓の代表の鮮度は窓の末尾＝いちばん新しい要素の鮮度基準時刻（D05 §6.7）であり、その足の終了時刻は 11:00Z である。`subject` と `observation_interval` は評価を起こした足のもの（`[11:00,12:00)`）であり、鮮度とは別の足を指す | 表1・表2 |
| 9 | `trailing` の要求 `EvaluationRequest(instance_id="trailing", trigger_names=("h1",), decision_time=12:00Z, target_interval=Interval[11:00,12:00), position_id=PositionId 00000001, attempt_index=0)`。入力 `position` は `RuntimeContextView.position_context(12:00Z, PositionId 00000001)` → `PositionContext(direction=LONG, entry_price=Price(149.390), effective_stop_loss=Price(148.950), …)`。`149.150 > 148.950` なので `UpdateStop(Price(149.150))` を返す | `OutputRecord(producer=("trailing","action"), payload=UpdateStop(stop_loss=Price(149.150)))` → 表1。`ManagementRequest(position_id=00000001, action=UpdateStop(Price(149.150)), decision_time=12:00Z, source_output_id=<その出力 ID>)` |
| 10 の直前 | **この管理要求を建玉へ適用する**（決定 Q25。D06 v1.6 §4.2 の手順6）。処理点のフェーズは受付（`ADMISSION`） | 表12 に1行 |

適用の内容そのものは D06 §8.3（v1.5）が決めている。参照価格は直前に完了した執行足 `[11:45,12:00)` の終値 bid `149.400`。検査は買いなので `149.150 < 149.400` → 合格。丸めは建玉にとって不利にならない側（買いの損切りは切り下げ）で、`149.150` は刻み `0.001` に整列済み。`effective_from` は**次の執行足** `BarKey(15m, 12:00Z)`。結果は `ProtectionState(version=2, stop_loss=Price(149.150), take_profit=None, effective_from=BarKey(15m, 12:00Z), owner_instance_id="trailing")` で、表12（`MANAGEMENT_APPLICATIONS`、主キー `(position_id, at)`）に1行残る。**主キーの `at` は `ProcessingPoint(12:00Z, ADMISSION, …)`** である（決定 Q25 により適用の処理点が受付フェーズに決まったため）。

**追えなかった点（本書で2番目に重い）**: D06 §8.3 は管理要求の「適用フェーズは `POST_FILL_EVALUATION`」（rank 12）と定めている。しかし rank 12 で第2回の `step` を呼ぶのは**受付通知か `POSITION_OPENED` の通知がある判断時点だけ**であり（D06 §4.2 の手順9。v1.6 で手順を1つ足す前は手順8 だった）、`01-07 12:00Z` にはどちらも無い。一方 D06 §4.2 の手順5（決定前の版）は、第1回の `step` が返した `management_requests` を **rank 10（受付）へ渡す**と書いているが、保護水準の更新は注文ではないので受付の要求の表（D06 §6.2）に入らない。**足の確定で生まれた `UpdateStop` を適用する場所が、どちらの手順にも無い**。段階2 では管理要求が rank 12 でしか生まれなかったため、この穴は現れなかった。→ 第14節 #5、**決定 Q25（2026-09-23、選択肢1）**: **受付（rank 10）の直前に、第1回の `step` が返した保護水準の更新を建玉へ適用する**（D06 v1.6 §4.2 の手順6・§8.3）。新しいフェーズは足さず、処理点のフェーズは受付（`ADMISSION`）になるので、表12 の主キー `(position_id, at)` はそのまま使える。適用の内容と `effective_from`（次の執行足）は D06 §8.3 のままである。同じ判断時点の決済要求との競合も受付の前に解決される。**本書の `01-07 12:00Z` の追跡では、`UpdateStop(149.150)` の適用が `ProcessingPoint(12:00Z, ADMISSION, …)` で起き、`effective_from` は上の段落のとおり `BarKey(15m, 12:00Z)` のままである**。この判断時点に新しい注文は無いので、受付の前に適用しても審査の結果は変わらず、**第10節・第16節の検算値は1つも動かない**。

**建玉の決済**（`01-07 13:15Z`）。執行足 `[13:00,13:15)` の安値 `149.050` が有効な損切り水準 `149.150` に触れる。利確は無いので片側だけの到達である。

| 記録 | 値 |
|---|---|
| 解決 | `IntrabarResolution(position_id=00000001, parent_bar_key=BarKey(15m, 13:00Z), method=SINGLE_HIT, series_used=(USDJPY/15m/bid,), resolved_child_bar_key=None, verdict=STOP_LOSS, fill_id=FillId 00000002)` → 表13 |
| 約定価格 | 基準は保護水準 `149.150`（始値 `149.400` はこれを越えていない）。売り決済なので `close_slippage` を不利方向（下げる）へ → **`Price(149.140)`** |
| 損益 | `(149.140 − 149.390) × 41000 = −10250` 円。決済手数料 `41` 円 → `balance = 999959 − 10250 − 41 = Money(989668, JPY)` |
| 建玉 | `status=CLOSED`、`close_fill_id=00000002`、`realized=Money(-10291, JPY)` |
| 予約との対比 | 受付時に確保した枠 `19762`、約定時の計測リスク `18040`、実際の損失 `10291`。**トレーリングで損切りを引き上げたぶん、実際の損失が計測リスクより小さい**ことが3つの数値の並びから読める |

D07 への影響（段階2 の指標15件で値が決まるもの）:

| 指標 | 値 |
|---|---|
| `NET_PROFIT` | `989668 − 1000000 = −10332 JPY` |
| `CLOSED_TRADE_PROFIT` | `−10291 JPY` |
| `TRADE_COUNT` | `1` |
| `WIN_RATE` | `0 ÷ 1 = 0` |
| `EXPOSURE_RATE` | `15300 秒 ÷ 1036800 秒`（`09:00Z → 13:15Z`、run 区間は12日） |
| `MAX_ADVERSE_FILL_OFFSET` | `+1 × (149.390 − 149.370) = 0.020` |

**`MANAGEMENT_APPLICATIONS`（表12）に `UpdateStop` の行が入るのは段階3 が初めて**である。D07 は段階2 でこの表を読まない（D07 §4.2 の「読まない6表」）ので、段階2 の指標は変わらない。

## 11. 経路9: 市場状態が許さない発火（遷移12）

D05 §9.4 の経路9。`DelayScenario(id="none")`、判断時刻 `2015-01-08T09:00Z`。

前提の状態: `01-07 22:00Z` に `market_state` が `MarketPermission(allow_long=False, allow_short=False)` を出している（第2.4節）。`entry_trigger` の状態は `01-07 13:00Z` の評価（終値 `149.100 <= 149.300`）で `ConditionState(False)` に戻っており、再武装済みである。

| rank | 何が起きるか | 記録 |
|---|---|---|
| 7 | `P3_TRIGGER`。`price = 149.700`（1時間足 `[08:00,09:00)` の終値）、`level = 149.520`（`latest_available(1d, 09:00Z)` が返す D(Jan7) の高値）。`149.700 > 149.520` かつ直前が不成立 → **発火する**。D05 §7.4 の手順1 で `OpportunityId 00000002` を採番し `Opportunity` を組み立てる（**発火を捨てない**。ADR-0032） | `Opportunity(opportunity_id=00000002, symbol=USDJPY, signal_interval=Interval[08:00,09:00), direction=LONG, reference_values={"breakout_level": Price(149.520)})` |
| 7 | 手順2a（D05 §7.6）。`CompiledRoles.market_state` が指す出力の最新は `01-07 22:00Z` 生成の `Observation[MarketPermission](MarketPermission(False, False), subject=BarKey(1d, 01-06 22:00Z), observation_interval=Interval[01-06 22:00Z, 01-07 22:00Z), freshness_time=01-07 22:00Z)`。方向 `LONG` に対して `allow_long=False` → **遷移12** | `OpportunityTransition(00000002, None → TERMINATED, at=ProcessingPoint(09:00Z, P3_TRIGGER, n), phase=P3_TRIGGER, reason=Reason(MARKET_STATE_INVALIDATED), counterpart=None, attempt_id=None)` → 表3 |
| 7 | 出力は**付番して判断履歴に残すが、下流へ配送しない**（D05 §6.2・§7.4 の手順5） | `OutputRecord(producer=("entry_trigger","opportunity"), payload=Opportunity(…))` → 表1。新しい状態は `ConditionState(True)` |
| 8 | `P4_CONFIRMATION`。確認待ち（`OPEN`）の機会は0件なので要求を作らない | 記録なし |

**追えた要点（3件）**:

1. 手順2a が**同時保持の数え方（手順2）より前**に置かれているので、許可されない発火は `max_active=1` の枠を消費しない。枠を消費していれば、同じ run のあとで許可された発火が `CONCURRENCY_LIMIT_REACHED` で見送られていた。
2. 終端理由は既存の `MARKET_STATE_INVALIDATED` を使い、`from_state=None` であることが「生成時点で許可が無かった」ことを示す（D05 §7.6）。第6節のとおり、`from_state` が `OPEN` / `CONFIRMED` の側は段階3 では到達しない。
3. 有効性の再検査（`ValidityRecheck`）はこの経路で**1件も作られない**。再検査が走るのは確認評価（P4）と `EntryProposal` を作る直前（P5）であり、機会はその前に終端しているからである。D05 §7.7 の「確認を始める前に機会が終わった場合は `ConfirmationAttempt` を積まない」と同じ扱いになる。

D07 への影響: `OPPORTUNITY_TRANSITIONS` の終端理由別の集計に `MARKET_STATE_INVALIDATED` が1件増える。`ORDER_REQUESTS`（表4）に行は増えないので、**この機会は表3 からしか辿れない**。T01 §10 が確認した ID 連鎖（機会 → 試行 → 注文 → …）は、受付前拒否では表4 が受け皿になっていたが、**遷移12 は受付にすら到達しない**ので連鎖の起点が表3 だけになる。これは設計どおり（発火は記録するが有効にしない）であり、未記述ではない。

## 12. 上流の出力の履歴窓（Q18〜Q21）をどこまで追えたか

D05 §6.12 が段階3 で作ると決めた「上流の出力を過去 N 本で読む仕組み」について、検証戦略 B で何が起きるかを追った。

`OutputRetentionPlan.by_output` は、その出力参照を読む入力の計画（`InputPlan`）から導く（D05 §6.12）。検証戦略 B の接続を当てはめると次になる。

| 出力参照 | 読む入力 | 読み方 | 要求する本数 |
|---|---|---|---|
| `daily_ema.value` | `daily_above_ema.right` | `LatestAvailable` | 1 |
| `daily_above_ema.condition` | `market_state.long_allowed` | `LatestAvailable` | 1 |
| `no_short.condition` | `market_state.short_allowed` | `LatestAvailable` | 1 |
| `m15_ema.value` | `m15_above_ema.right` | `LatestAvailable` | 1 |
| `m15_above_ema.condition` | `entry_filter.condition` | `LatestAvailable` | 1 |
| `stop_level.level` | `initial_stop.level`、`trailing.level` | `LatestAvailable` ×2 | 1 |
| `market_state.permission` | `InputBinding` からは読まれない（役割フィールド経由） | — | 載せない |
| `entry_trigger.opportunity` / `entry_filter.confirmation` | `EVENT` は保持しない（D05 §6.5） | — | 載せない |

**結論: 検証戦略 B の保持本数はすべて 1 であり、履歴窓の読み取りは1度も起きない**。Q12 の決定により段階3 では履歴窓で上流の出力を読む部品を作らないので、これは設計どおりである。仕組みは作られるが、**段階3 の受入れテストでは経路が1つも通らない**。

追えた副次的なこと:

- 有効性束縛（`ValidityBinding.source`）と役割フィールド（`CompiledRoles.market_state`）は `InputPlan` ではないので保持本数の計画に載らないが、どちらも `RuntimeState.latest_outputs` を読む（D05 §7.3・§7.6）ので、`latest_outputs` を残す判断（D05 §6.12）が効いている。
- 保持本数が 1 の出力参照については、`latest_outputs` と `output_history` が**同じ1件を二重に持つ**。読み取りは読み方ごとに別の保持先を使うので結果は変わらない。「2つの最新が食い違う」場面（D05 §6.12）は `on_superseded=KEEP_WAITING` でだけ起きるが、検証戦略 B は `EXPIRE_REQUEST` しか宣言しないので食い違わない。→ 第14節 #14（D05 v2.1 で明記した）。

## 13. 各表に残る行の一覧

D06 §9.2 の19表（段階2 の15表＋段階3 の4表）について、本書が追った経路で行が入るものを挙げる。件数はケース1（遅延なし）で経路1 と経路8 と経路9 を通した run のものである。

| # | 表 | 行数 | 主キーの値 |
|---|---|---|---|
| 1 | `OUTPUTS` | 経路1 で7、経路8 で3（`stop_level` 3本ぶん）＋1（`UpdateStop`）、経路9 で1。ほか日足・15分足の評価ごとに増える | `OutputId` |
| 2 | `EVALUATIONS` | 出力より多い（`Evaluated(())` の評価と `Skipped` を含むため） | `EvaluationId` |
| 3 | `OPPORTUNITY_TRANSITIONS` | **6**（v1.2 で訂正。`01-06 15:00Z` の発火の遷移12、経路1 の機会の遷移1・10・4・5、経路9 の機会の遷移12。v1.1 は `01-06 15:00Z` の発火を見落として 5 と書いていた。第1.6節・第20節） | `(opportunity_id, at)` |
| 4 | `ORDER_REQUESTS` | 2（エントリー1・保護水準の到達による決済1） | `AttemptId` |
| 5 | `ATTEMPT_DECISIONS` | 2 | 同上 |
| 6 | `RISK_ASSESSMENTS` | 1（決済は審査に入らない） | `EvidenceId 00000002` |
| 7 | `ORDERS` | 2 | `OrderId` |
| 8 | `ORDER_EVENTS` | 4 | `EventId` |
| 9 | `FILLS` | 2 | `FillId` |
| 10 | `RESERVATIONS` | 1（`HELD → TRANSFERRED`） | `ReservationId 00000001` |
| 11 | `POSITIONS` | 1 | `PositionId 00000001` |
| 12 | `MANAGEMENT_APPLICATIONS` | **1**（`UpdateStop` の適用。段階3 で初めて `UPDATE_STOP` の行が入る） | `(position_id, at)` |
| 13 | `INTRABAR_RESOLUTIONS` | 1（`FillId 00000002`） | `fill_id` |
| 14 | `LEDGER_SNAPSHOTS` | 判断時点数 × 2 まで | `ProcessingPoint` |
| 15 | `EVIDENCE` | 4 以上 | `EvidenceId` |
| 16 | `WAIT_EVENTS` | ケース1 では **0**。ケース2 では待機開始・到着・再開が使用箇所ごとに並ぶ。ケース3 では期限到達、ケース4 では追い越しと新しい待機 | `(request_id, at)` |
| 17 | `INPUT_SUBSTITUTIONS` | **0**。検証戦略 B は `UsePrevious` を1つも宣言しない（第14節 #15） | `(evaluation_id, input_name, source_index)` |
| 18 | `CONFIRMATION_ATTEMPTS` | 経路1 で1、経路2 で2、経路3 で4 | `(opportunity_id, bar_key)` |
| 19 | `VALIDITY_RECHECKS` | 経路1 で2（P4 と P5 直前）。経路9 では 0 | `(opportunity_id, at)` |

**ID 連鎖の確認**: 機会 `00000001` → 確認試行（表18 の `opportunity_id`）→ 確認評価（表18 の `request_id` → 表2）→ 注文意図の出力（表1）→ 試行 `00000001`（表4 の `opportunity_id` と `intent_output_id`）→ 注文 → 約定 → 建玉 → 管理要求（表12）と辿れる。段階3 で増えた表16・18・19 はいずれも既存の主キー（`request_id` / `opportunity_id`）で既存の表へ戻れる。**表17 だけは `evaluation_id` を要求するが、`SubstitutedInput` 自体は `evaluation_id` を持たない**（D06 §9.2 が「＋ その要求の `evaluation_id`」と補っている）ので、エンジンが組み合わせて書く必要がある。本書の経路では行が0件なので確かめられなかった。

## 14. 未記述の一覧（本書で見つけたもの）

分類は T01 §13 と同じ区分を使う。**対処の列に「D05 v2.1」「D07」とあるものは、設計の選択を含まないので起草時にそのまま反映した**。「決定 Qxx」とあるものは設計の選択を含むので起草時には確定させず要決定として残し、**2026-09-23 の人間の決定（すべて選択肢1）を受けて同じ PR で反映した**（決定の一覧は第15.1節）。

| # | 分類 | 未記述だった点 | 発見した経路 | 対処 |
|---|---|---|---|---|
| 1 | 到達可否 | 突破水準が `LatestAvailable` で読む日足高値であるため、**日足境界では1時間足の終値が日足高値を超えられない**（日足は1時間足からの集約。D03 §5.1）。D05 §9.3 のケース2 の例と §9.4 の経路1・5 が置いた判断時点では取引機会が生まれない | 経路1・経路5 | **決定 Q23**（2026-09-23、選択肢1）: 宣言は変えず、D05 v2.2 §9.3・§9.4 の例文を「取引機会が生まれるのは日足境界でない1時間足の確定」に改めた |
| 2 | 型の不足 | D05 §7.6 が「取引許可の `freshness_time` と `observation_interval` を判断履歴に残す」と書くが、`OpportunityTransition` にも `ValidityRecheck` にもその欄が無い | 経路1・経路9 | **D05 §7.6**: 残る先は表1 の出力記録であり、適用した許可は「**その発火が作った取引機会を載せた出力記録の `OutputId` より小さい `OutputId` を持つ、許可の出力参照の最後の出力**」として一意に復元できることを明記（v2.1 で書き、v2.2 で比べ方を識別子の大小に確定した。判断時刻だけで絞ると、日足と1時間足が同じ判断時点で確定したときに同じ `step` の更新を取りこぼす） |
| 3 | 規則の帰結の未記述 | `BarsDeadline(n)` の n 本目の確認足は、期限の判定（rank 4）が確認の評価（rank 8）より先に来るため**確認に使われない**。したがって実際の確認試行の回数は `include_start_bar=True` で n 回、`False` で n-1 回になる | 経路3 | **D05 v2.1 §7.7**: 期限と確認試行の回数の対応を明記 |
| 4 | 阻害要因 | 確認試行（`ConfirmationAttempt`）は `OpportunityLifecycle` の中にしか無く、`RuntimeStepResult` に列が無い。**エンジンが D06 表18 を書くための受け渡し経路が存在しない** | 経路1・経路2・経路3 | **決定 Q26**（2026-09-23、選択肢1）: `RuntimeStepResult` に `confirmation_attempts` を足し、その `step` の差分だけを返して主キー `(opportunity_id, bar_key)` で表18 を置き換える（D05 v2.2 §3・§6.2・§7.7、D06 v1.6 §4.2・§9.2） |
| 5 | 規則の欠落 | **足の確定で生まれた保護水準の更新（`UpdateStop`）を適用するフェーズが無い**。D06 §8.3 は `POST_FILL_EVALUATION` と定めるが、そのフェーズは受付通知か約定通知がある判断時点でしか動かない。D06 §4.2 の手順5（決定前の版）は第1回の `step` の管理要求を rank 10 へ渡すが、保護水準の更新は注文ではない | 経路8 | **決定 Q25**（2026-09-23、選択肢1）: 受付（rank 10）の直前に適用する。新しいフェーズは足さず、処理点のフェーズは `ADMISSION`（D06 v1.6 §4.2 の手順6・§8.3） |
| 6 | 到達可否 | 確認待ちのあいだに日足の条件が崩れる経路（D05 §9.4 の経路4）が、`BarsDeadline(bars=4)` と日足境界が1時間境界上にあることから**到達しない**（期限が先に勝つ） | 経路4 | **決定 Q24**（2026-09-23、選択肢1）: 到達しない経路として記録し、再検査そのものは宣言を変えた小さな戦略の意味論テストで検証する（D05 v2.2 §9.4、D08 v1.1 §13.2 の未整備6） |
| 7 | 他文書への引き渡し | 段階3 で `EvaluationOutcome` に区分が2つ増え（`Waiting` / `Superseded`）、表16〜19 が増えるが、D07 §4.2 の「読まない6表」の列挙と §6.1 の「評価の結果区分別の集計」が段階3 を想定していない。**集計の語彙が3語のままだと、待機だけが起きた run で `SUPERSEDED` の行が出ず、遅延シナリオ4ケースを並べて比べられない**（有限の語彙は0件の鍵も行として出すという同節の規則に反する）。また、待機して決着した要求は**同じ `request_id` で2件の評価記録**になるため、評価記録を数えると要求を2回数える | 経路5・経路6 | **D07 §4.2・§6.1**: 読まない表に16〜19 を足し、**評価の結果区分の語彙を段階3 では5語にする**（0件の行も出す）ことと、合計が評価要求の数ではなく評価記録の数になることを明記。段階2 の指標15件の値は変わらないが、**段階3 の実装で段階2 の run を評価すると0件の行が2行増える**ので、評価側の固定出力（golden）は更新の対象になる |
| 8 | 規則の欠落 | 足りない入力が**出力参照だけ**の待機で、`BarsDeadline(n)` をどの系列の確定足で数えるかが決まっていない（出力参照の入力に系列が無い） | 経路5（`market_state`） | **決定 Q28**（2026-09-23、選択肢1）: その使用箇所の起動条件（`OnBarClose`）の系列で数える（D05 v2.2 §6.8）。複数系列で起動する使用箇所は**決定 Q30**（選択肢1）によりコンパイル時に拒否する（D04 v1.11 の検査 #15） |
| 9 | 規則の欠落 | 取引機会を出す評価が**自身の入力の待機**と**市場状態の連鎖による待機**（D05 §7.6）の両方を満たすとき、どちらの期限と `on_superseded` を使うかが決まっていない | 経路5（`entry_trigger`） | **D05 v2.1 §7.6**: 検証戦略 B では両者が同じ値（日足1本・`EXPIRE_REQUEST`）に解決するため段階3 の実行結果は変わらないことを明記し、一般の規則は段階4 の要決定へ送る |
| 10 | 規則の欠落 | D05 §6.6 の「`step` 内の1本の通し番号を出力記録と遷移記録が共有する」に、段階3 で増えた**待機の出来事（`WaitEvent.at`）と有効性の再検査（`ValidityRecheck.at`）**が含まれていない。D06 §4.4（v1.5）はこの2つも番号を振り直すと定めているので、共有しないと順序が決まらない | 経路1・経路5 | **D05 v2.1 §6.6**: 共有する記録に2件を足す |
| 11 | 順序の欠落 | 確認成立の遷移10 を刻む処理点が、確認結果の出力記録の**前か後か**が決まっていない。取引機会の生成については D05 §6.6 が明記している | 経路1 | **D05 v2.1 §7.7**: 確認結果を付番して送出した後に遷移10 を刻むと明記（確認の成立は出力の内容から決まるため、取引機会の生成とは前後が逆になる） |
| 12 | 順序の欠落 | 同じ判断時点で待機要求の**期限到達と追い越しが同時に成立**したときに、どちらの記録が残るかが決まっていない（`DEADLINE_REACHED` ＋ `Skipped` か、`SUPERSEDED` ＋ `Superseded` か） | 経路6（1本だけを遅らせた場合） | **決定 Q29**（2026-09-23、選択肢1）: 既に書かれている並び（期限 → 追い越し → 失効）を判定順とし、期限が勝つ（D05 v2.2 §6.8 の手順2） |
| 13 | 規則の欠落 | `WaitEvent(INPUT_ARRIVED)` を**入力ごとに1件**残すのか、その処理点で届いた入力をまとめて1件にするのかが書かれていない | 経路5 | **D05 v2.1 §6.8**: `arrived` が入力名の `tuple` であることから、1つの処理点につき1件にまとめると明記 |
| 14 | 規則の帰結の未記述 | 保持本数が 1 の出力参照は、`latest_outputs` と `output_history` が同じ1件を二重に持つ。検証戦略 B ではすべての出力参照がこれに当たる | 第12節 | **D05 v2.1 §6.12**: 二重に持つこと、読み方ごとに別の保持先を使うので結果が変わらないことを明記 |
| 15 | 到達可否 | 遡り（`USE_PREVIOUS`）を宣言する使用箇所が検証戦略 B に1つも無いため、**D06 表17 と `SubstitutedInput` は段階3 の受入れテストで1行も生まれない**。上流の出力を履歴窓で読む仕組み（D05 §6.12）も同じく1度も通らない（第12節） | 第12節・第13節 | **D05 v2.1 §9.4**: 段階3 の受入れで経路が通らない2つの仕組みを明記し、検証の担当（D08 の意味論テスト）を指す |
| 16 | 規則の欠落 | `WaitingRequest.pinned_bars` は「入力名 → 固定した対象足」だが、**出力参照の入力には足が無い**。何を載せるか（載せないか）が書かれていない | 経路5（`daily_above_ema`） | **D05 v2.1 §6.8**: 市場データ参照の入力だけを載せると明記（出力参照の読み直しは `latest_outputs` と `output_history` を使い、窓の末尾は要求の対象区間から決まるため足を固定する必要が無い。§6.8 手順4・§6.12） |
| 17 | 規則の帰結の未記述 | 段階3 のランタイムは宣言によらず `VALUE` の出力をすべて `Observation` で包む（D05 §4.2・§6.7）。したがって**段階3 のランタイムで検証戦略 A を動かすと、T01 の golden trace の表1 の payload 列が変わる** | 第2節・第3.2節 | **D05 v2.1 §6.7**: 包む範囲が戦略の宣言に依らないことと、段階2 の golden が更新対象になることを明記（更新の運用は D08 §10.2） |
| 18 | 規則の帰結の未記述 | 検証戦略 B は利確を出す部品を持たないため、**建玉は初期の利確を持たないまま開く**。D05 §8 が排除したのは「利確を出す戦略なのに次足まで利確が無い」構成であり、この戦略は当たらない | 経路1 | **D05 v2.1 §8**: 読み分けを1行足す（D06 §8.3 の「管理要求が1件も返らなかった建玉」と同じ扱い） |
| 19 | 規則の欠落 | 1回の評価で複数の入力が欠け、それぞれの欠損方針（`on_missing`）が食い違うときにどの方針に従うかが決まっていない。`breakout_trigger` v2 は `level` が待機、`price` が見送りなので、日足と1時間足が同時に遅れると成立する | 経路5（宣言から導いた。本書の人工データでは1時間足を遅らせていないので実演していない） | **決定 Q27**（2026-09-23、選択肢1）: 強い方針が勝つ順序 `Error` > `SkipEvaluation` > `WaitForInput` > `UsePrevious` を置いた（D05 v2.2 §6.3） |
| 20 | 規則の欠落 | D05 §6.5 の「`Skipped` / `Failed` の評価では状態を更新しない」に、段階3 で足した2つの結末（`Waiting` / `Superseded`）が入っていない。どちらも部品を呼んでいないので更新する材料が無い | 経路5・経路7（`entry_trigger` の `ConditionState`） | **D05 v2.1 §6.5**: 2つの結末でも更新しないことと、その理由（部品を呼んでいないので `new_state` が無い）を明記 |

分類の内訳: 規則の欠落8件（#5・#8・#9・#10・#13・#16・#19・#20）、規則の帰結の未記述4件（#3・#14・#17・#18）、到達可否3件（#1・#6・#15）、順序の欠落2件（#11・#12）、型の不足1件（#2）、阻害要因1件（#4）、他文書への引き渡し1件（#7）。合計20件。**うち13件は設計の選択を含まないので本 PR の D05 v2.1・D07 v1.4 の改訂として反映し、7件を要決定（Q23〜Q29）として残した**。**7件とも 2026-09-23 に人間が決定し（すべて選択肢1）、同じ PR で D05 v2.2・D06 v1.6・D08 v1.1 へ反映した**（上の表の対処欄）。したがって**本書が見つけた20件はすべて対処済み**であり、Q28 の反映の途中で新しく生じた Q30 も同じ日に決定済みである（第15.2節）。**未決は残っていない**。

## 15. 要決定の一覧（Q23〜Q30 はすべて決定済み）

番号は D05 の連番を継ぐ（Q22 まで決定済み。D05 §15）。起草時（v0.1）は7件とも確定させずに選択肢と推奨を並べ、**2026-09-23 に人間が7件すべてを決定した**。決定はいずれも**選択肢1（起草時の推奨）**である。その反映の途中で新しく生じた1件（Q30）も、同じ日に決定した（第15.2節）。**本書に未決の要決定は残っていない**。

### 15.1 Q23〜Q29 の決定（2026-09-23。**7件すべて選択肢1**）

| # | 決めたこと | 決定 | どこへ反映したか |
|---|---|---|---|
| Q23 | 日足境界で突破が成立しないことを、宣言を変えて直すか、設計文書の例文のほうを直すか | **宣言は変えない**。D05 §9.3 のケース2 と §9.4 の経路1・5 の例文を「取引機会が生まれるのは日足境界でない1時間足の確定である」に改める | D05 v2.2 §9.3・§9.4。本書 第0節・第3.1節・第7.3節 |
| Q24 | 確認待ち中に市場状態が失効する経路が到達しないことを、期限を延ばして到達させるか、到達しない経路として記録するか | **到達しないと記録する**。再検査の経路（`ValidityRecheck` の4区分）は、宣言を変えた小さな戦略を使う意味論テストで検証する | D05 v2.2 §9.4、D08 v1.1 §13.2 の未整備6。本書 第6節 |
| Q25 | 足の確定で生まれた保護水準の更新を、どのフェーズで建玉へ適用するか | **受付（rank 10）の直前に適用する**。適用の内容と `effective_from` は従来のまま。同じ判断時点の決済要求との競合は受付の前に解決する | D06 v1.6 §4.2 の手順6・§8.3。本書 第1.5節・第10節 |
| Q26 | 確認試行の記録をエンジンがどう受け取り、判断履歴の表18 をいつ書くか | **戻り値に列を足す**。`RuntimeStepResult.confirmation_attempts`（既定は空）に、その `step` で作った・書き換えた試行だけを毎回載せ、エンジンは主キー `(opportunity_id, bar_key)` で表18 の行を置き換える | D05 v2.2 §3・§6.2 の手順10・§7.7、D06 v1.6 §4.2・§9.2。本書 第3.3節 |
| Q27 | 1回の評価で欠けた入力の欠損方針が食い違うときにどちらに従うか | **強い方針が勝つ**。順序は `Error` > `SkipEvaluation` > `WaitForInput` > `UsePrevious` | D05 v2.2 §6.3。本書 第14節 #19 |
| Q28 | 足りない入力が出力参照だけのとき、本数で数える待機期限をどの系列の足で数えるか | **その使用箇所の起動条件（`OnBarClose`）の系列**で数える | D05 v2.2 §6.8。本書 第7.1節 |
| Q29 | 同じ判断時点で待機期限の到達と追い越しが同時に成立したとき、どちらの記録が残るか | **既に書かれている並び（期限 → 追い越し → 失効）を判定順とする**。期限が勝つ | D05 v2.2 §6.8 の手順2。本書 第1.4節・第9節 |

**7件の決定で何が変わり、何が変わらなかったか**を1行ずつ書く。

- **変わらないもの**: 検証戦略 B の宣言（第1.3節）、段階3 の部品カタログ11件、本書の検算値（第16節）、段階2 の実行結果。Q23・Q24 が「宣言を変えない」側で決まったためである。
- **変わるもの**: 戦略ランタイムの戻り値に列が1つ増える（Q26）、損切り水準の更新を適用する処理点が受付フェーズになる（Q25）、待機の期限と判定順に規則が3つ増える（Q27〜Q29）。**いずれも段階3 で初めて通る経路であり、既存の実装は変えない**（第19節）。

**採らなかった案**（起草時の選択肢2・3。各設計文書の該当節にも「不採用」として1行ずつ残してある）:

| # | 選択肢2 | 選択肢3（承認済みの設計を維持する案） |
|---|---|---|
| Q23 | `entry_trigger.level` を「1本前の日足の高値」にする部品（`breakout_trigger` v3）をカタログへ足す。部品が11件から12件に増える | 例文を残し、日足境界で突破が成立しないことを到達可否として注記する。同じ節で例文と注記が食い違ったままになる |
| Q24 | 確認期限を15分足8本（2時間）に延ばして経路4 を到達させる。受入れテストの人工データまで作り直しになる | 経路4 を9経路の一覧に残し「到達したら追う」とする。完了条件に到達しない経路が残る |
| Q25 | 保護水準の更新のための新しいフェーズを rank 9 と rank 10 のあいだに足す。フェーズ集合が15件から16件に増え、run manifest と評価が読む値域が変わる | 約定後の評価（rank 12）を通知の有無によらず毎判断時点で実行する。約定の後に適用されるので有効開始の足が1本ずれる |
| Q26 | run 末尾に確認試行をまとめて渡す。run が途中で失敗すると確認の経過が1行も残らない | 表18 を評価記録と遷移記録からの派生表と位置づけ、エンジンが書くのをやめる。表を独立させた理由と噛み合わない |
| Q27 | 入力ごとに方針を適用し、待機できる入力があれば待機に入る。解消しない入力を抱えたまま期限まで run が止まる | 方針が食い違う構成をコンパイル時に拒否する。`breakout_trigger` v2 を作り直すことになり契約の版が増える |
| Q28 | 上流をたどって最初に見つかった市場データ参照の系列で数える。たどる規則を新しく作る必要がある | 出力参照だけの待機では本数の期限を使えないことにする。期間で数える期限しか書けず、ダイジェストを計算できないので事実上取れない |
| Q29 | 追い越しを先に判定する。到着順が宣言上の上限を上書きすることになる | 同時成立が起きない遅延設定でだけ段階3 を進める。実データの公開遅延は選べないので段階4 以降で必ず戻ってくる |

### 15.2 Q30 の決定（2026-09-23。**選択肢1**）

Q28 の決定（待機の本数期限は、その使用箇所の起動条件の系列で数える）を設計文書へ書くときに、**決定そのものでは決まらない場合が1つ残った**ので要決定として上げ、**同じ日に人間が決定した**。

**何を決めたか**: ある使用箇所が、**区間の同じ足の確定を2つ以上の系列について宣言している**とき、待機の本数期限をどちらの系列で数えるか。区間が違う起動条件は区間ごとに別の評価要求になる（D05 §6.2 の手順3）ので数える系列は要求ごとに定まるが、**区間が同じ起動条件は1件の要求へ集約される**ため定まらない。起動系列がただ1つであることをコンパイル時に検査しているのは、確認部品（D05 §5.6 の検査 b）と決済部品（検査 g）だけで、**市場状態や条件の使用箇所には検査が無かった**。

**決定（選択肢1＝推奨）**: **コンパイル時の検査を1件足す**。接続元が出力参照だけの入力に本数で数える待機期限を書いた使用箇所は、**足の確定の起動条件の系列がただ1つ**でなければならない。そうでない宣言は起動条件が許されない構成（`SCHEDULE_NOT_ALLOWED`）として拒否する。

| 反映先 | 内容 |
|---|---|
| D04 §12 の検査 #15（**v1.11**） | 検査の正本。検査の内容と「その検査が読む宣言」 |
| D05 §5.6 の検査 h と同節の段落（v2.2） | コンパイラのどの段で実行し、どの拒否の区分で返すか |
| D05 §6.8 の「期限を絶対の形へ解決する」（v2.2） | 未決だった注記を、検査で担保される旨へ書き換え |

**本書の追跡結果は変わらない**。検証戦略 B で出力参照だけの待機が起きる使用箇所（`market_state`・`daily_above_ema`）は、どちらも日足1系列でしか起動しないので、この検査に引っかからない（第1.3節）。

**採らなかった案**: (2) 集約した要求では系列 ID の昇順で先頭の系列で数える — 検査は増えないが、待機の期限の長さが戦略の意図と無関係な系列名の並び順で決まる。(3) 承認済みの設計を維持し、その構成では実行時に失敗させる — 文書の改訂は最小だが、宣言から書けてしまう構成を run の途中まで見つけられず、D05 §5.6 が検査 f・g で採った「宣言時に止める」方針と逆になる。

## 16. D08 への引き渡し（T02 再現生成器の仕様）

D08 §9.1 は生成器を「汎用」と「T01 再現」の2つに分けている。本書の人工データは3つ目の生成器（**T02 再現生成器**）として、段階3 の受入れテストの入力になる。D08 §9.4（T01 再現生成器）と同じ形で仕様を置く。

| 項目 | 値 |
|---|---|
| 置き場所 | `tests/fixtures/acceptance/t02_market.py` |
| run 区間 | `Interval[2015-01-04T22:00Z, 2015-01-16T22:00Z)`（T01 と同じ。12日 = 1,036,800 秒）。`RunConfig.run_interval` に渡す値であり、**足を作る区間ではない** |
| 生成区間（既定） | `Interval[2014-09-30T22:00Z, 2015-01-16T22:00Z)`。**run 区間ではなくウォームアップの開始まで遡って作る**【重要】。`daily_ema` は60本の窓を要求し（第1.3節）、run 区間の中で集約できる日足は数本しかないため、run 区間だけで snapshot を作ると `WARMUP_INSUFFICIENT` になり、第2.4節の検算値（149.020 / 149.000）も後続の全経路も再現できない。第1.1節の「snapshot の日足は `2014-09-30T22:00Z` 以降を持つ」は**この生成区間のことである** |
| 公開関数 | `bars_for(timeframe_id, definition, calendar, window=GENERATION_INTERVAL)`、`csv_text(bars)`、`series_of(timeframe_id)`、`apply_delay(bars, scenario)`。**既定の `window` は生成区間であって run 区間ではない**（上の行）。15分足の固定テーブル（第2.3節）は run 区間の前後だけを覆えばよいが、1時間足は生成区間の全体を覆う（日足はそこから集約する） |
| 作り方 | **1時間足だけを固定 OHLC テーブルから作り（第2.1節）、日足は D03 §5.1 の集約規則でそこから生成する**。15分足は別の固定テーブル（第2.3節）から作る |
| 日付 | 2015年1月。人工データはアクセス分類の対象外であり、partition は日付によらず研究履歴（`RESEARCH_HISTORY`）として作る（D08 §9.5 の規則1） |

**T01 再現生成器と分ける理由**【提案】。T01 の生成器は1時間足と15分足の固定テーブルだけを持ち、日足を作らない。本書は日足を**集約で作る**ことが検算の前提（第3.1節の到達可否はこの集約から導かれる）なので、同じ関数に日足を足すと T01 の検算値の意味が変わる。D08 §9.1 の「役割が違う生成器は混ぜない」に従い、別の生成器にする。

必要な拡張は次の4つで、うち3つは D08 §9.6 が既に方針として挙げているものである。

| # | 拡張 | D08 §9.6 との対応 |
|---|---|---|
| 1 | 遅延規則（`DelayScenario`）を受け取り、**`available_at` だけを動かす**関数 | §9.6 の1（そのまま） |
| 2 | 素の足を作る関数と遅延を当てる関数を分け、4ケースが同じ素の足を共有できるようにする | §9.6 の2（そのまま） |
| 3 | 4ケースそれぞれで run を回し、**4つの trace を1つの意味論テストの中で突き合わせる** | §9.6 の3（そのまま） |
| 4 | **日足を1時間足から集約する入り口**（`aggregate(bars_1h, timeframe_def, calendar)`）。集約の規則そのものは `marketdata.application.aggregation`（D03 §5.1）が持つので、生成器はそれを呼ぶだけにする | **本書で新しく必要になったもの** |

**検算値**（受入れテストが固定する値）:

| 対象 | 値 |
|---|---|
| `daily_ema`（`01-06 22:00Z` / `01-07 22:00Z`） | 149.020 / 149.000 |
| `m15_ema`（`01-07 09:00Z`） | 149.255 |
| `stop_level`（`01-07 09:00Z` / `01-07 12:00Z`） | 148.950 / 149.150。出力の `freshness_time` は **08:00Z / 11:00Z**（当該足を除いて読むので窓の末尾が1本手前の足になる。第10節） |
| 建玉 P1 | 約定 149.390 / 数量 41,000 / 初期損切り 148.950 / 更新後の損切り 149.150 |
| 決済 | 149.140、確定損益 −10,291 |
| 末尾 | `balance = 989,668`、`NET_PROFIT = −10,332`、`TRADE_COUNT = 1`、`WIN_RATE = 0` |
| ケース2 の再開 | `daily_ema` の出力の `decision_time = 01-07T22:00:02Z`、`freshness_time = 01-07T22:00Z`、`subject = BarKey(1d, 01-06T22:00Z)` |
| ケース3 の期限 | `WaitEvent(DEADLINE_REACHED)` の処理点が `01-08T22:00Z` の `OPPORTUNITY_LIFECYCLE` |
| ケース4 の追い越し | `Superseded` の処理点が `01-07T23:00Z` の `OPPORTUNITY_LIFECYCLE` |

**不変条件**（D08 §9.2 の5件に加えて本書が要求するもの）:

1. **日足は必ず1時間足から集約する**。日足を直接テーブルで与えない。与えると、第3.1節の到達可否（日足高値が1時間足の高値を含むこと）が人工データの設定になってしまい、規則の帰結として検証できない。
2. **遅延を当てても OHLC と対象区間は1つも変わらない**。4ケースの trace の差が `available_at` と配送順序だけであることを、テストが突き合わせで確かめる。
3. **15分足は集約の入力にも出力にもしない**（D03 §5.1）。1時間足との値の一致は人工データの設定であり構造的な保証ではないことを、生成器のコメントに残す。
4. **生成区間は run 区間より広い**。1時間足は `2014-09-30T22:00Z` から作り、日足の窓60本と15分足の窓60本がどちらもウォームアップ不足にならないことを、生成器のテストが本数で確かめる（日足は `01-06 22:00Z` 時点で60本以上、15分足は `01-07 09:00Z` 時点で60本以上）。既定の生成区間を run 区間に縮めると第2.4節の検算値が1つも出ない。

## 17. 段階3 の完了条件との対応

全体計画書 §8.2 は段階3 の完了条件を「戦略 B を同じ基盤で記述でき、時刻境界と取消理由を trace できる。遅延シナリオ別の差分を追跡できる」としている。

| 条件 | 本書での確認 | 状態 |
|---|---|---|
| 戦略 B を同じ基盤で記述できる | 第1.3節（使用箇所12件・戦略全体の宣言・コンパイル結果）。D04 の宣言型と D05 のカタログで**すべて書けた** | 満たす |
| 時刻境界を trace できる | 第3.1節（日足境界）、第7節（遅延した公開が作る判断時点）、第9節（1時間足の境界での追い越し）。`Observation.observation_interval` と `freshness_time` で「どの足を見た答えか」が全出力に載る | 満たす |
| 取消理由を trace できる | 第5節（`EXPIRED`）、第9節（`REQUEST_SUPERSEDED`）、第11節（`MARKET_STATE_INVALIDATED`）。確認試行（表18）の受け渡し経路は**決定 Q26 で確定**した（戻り値に `confirmation_attempts` を足す） | 満たす |
| 遅延シナリオ別の差分を追跡できる | 第7節・第8節・第9節。4ケースの差が `WAIT_EVENTS`（表16）と評価記録の結果区分に現れることを追えた。ケース2 の例文は**決定 Q23 で改めた**（追うのは待機と再開まで） | 満たす |
| D05 §9.4 の9経路 | 経路1・2・3・5・6・7・8・9 は追えた。経路4 は**決定 Q24 により「到達しない経路」として記録**し、検証は意味論テストへ渡した | 満たす |
| トレーリングの適用 | 第10節。適用の処理点は**決定 Q25 で受付（rank 10）の直前に確定**した | 満たす |

**「満たす」は設計としての充足である**。段階3 の完了条件そのものは、ここで確定した設計を実装し、受入れテストが本書の検算値（第16節）を再現したときに成立する。実装側で足すものは第19節に並べた。

## 18. 本書の後続版

T01 への追記ではなく新しい文書にしたのは、追う run が違うからである。T01 は検証戦略 A の1突破分（建玉2件）を12日の run で追っており、本書は検証戦略 B の run を**4つの遅延シナリオで4回**追う。同じ文書に入れると、第1節の共通の前提が「どの run のものか」で分かれ、各表の行数（T01 §10、本書 §13）も2つの戦略で二重になる。D05 §14 が「T01 への追記でも新しい T02 でもよく、置き場所は起草時に決める」としていた選択を、この理由で T02 にした。T01 §14 の後続版の表のうち、v1.1 の2行（検証戦略 B の時刻表・遅延シナリオ4ケース）は本書が引き取ったので、T01 側では繰り下げではなく**本書への参照**にする（T01 の改訂は本 PR の対象外であり、T01 §14 の読み替えは D05 §9.4 の記述が担う）。

| 版 | 追記する内容 | 前提 |
|---|---|---|
| ~~v0.2~~ → **v1.0**（2026-09-23、PR #24） | 要決定 Q23〜Q29 の決定と、その反映から生じた Q30 の決定を反映した（到達しない2経路の扱い、トレーリングの適用フェーズ、確認試行の受け渡し、欠損方針と待機の3規則、待機期限を数える系列のコンパイル時検査）。**起草時に予定していた v0.2 の内容がそのまま決定の反映で埋まったので、v0.2 を置かずに v1.0 とした** | Q23〜Q30 の決定（**済**） |
| **v1.1**（2026-09-24、PR #27） | 検証戦略 B の評価順についての人間の再決定（取引機会を出す部品 → 取引機会を読む部品の因果辺を足す。D04 v1.13・D05 v2.4）を受け、評価順（第1.3節）と、並びが変わった箇所の出力 ID・通し番号（第3.2節）と評価順の列（第7.1節）を直した。各経路の結論と検算値は変わらない | 再決定（**済**） |
| **v1.2**（2026-09-25、段階3 実装 PR 5/5） | 段階3 の実装が出たあと、受入れテストの実測値と本書の検算値（第16節）を突き合わせ、食い違いがあれば本書側の誤りを直す。**第16節の検算値はすべて一致した**。検算値ではない記述の誤りを4件直した（第1.6節・第3.4節・第4節・第13節）。突き合わせの結果は第20節 | 段階3 の実装（**済**） |

## 19. 段階3 実装への引き渡し（既存実装との差）

Q23〜Q29 の決定で、**いま `src/odyssey_fx/` にある段階2 の実装と設計の差が3件生じた**。本 PR では実装を1行も変えていない（3件とも段階3 で初めて通る経路であり、段階2 の振る舞いは変わらない）。段階3 の実装に入るときに、次を順に足す。

| # | 足すもの | 出どころ | 段階2 への影響 |
|---|---|---|---|
| 1 | **戦略ランタイムの戻り値に確認試行の列を足す**。`RuntimeStepResult` に `confirmation_attempts: tuple[ConfirmationAttempt, ...]`（既定は空）を置き、`runtime` がその `step` で作った・書き換えた試行だけを載せる。エンジン側は受け取った試行を主キー `(opportunity_id, bar_key)` で判断履歴の表18 へ置き換えとして書く | 決定 Q26。D05 v2.2 §3・§6.2 の手順10・§7.7、D06 v1.6 §4.2・§9.2 | **無し**。段階2 のランタイムは確認を行わないので、列は常に空で返る（既定値のまま） |
| 2 | **バックテストエンジンのフェーズ手順に、保護水準の更新の適用を1つ足す**。受付（rank 10）の直前で、第1回の `step` が返した `UpdateStop` を建玉へ適用する。処理点のフェーズは `ADMISSION`。同じ判断時点の決済要求との競合はこの適用より前に解決する | 決定 Q25。D06 v1.6 §4.2 の手順6・§8.3 | **無し**。段階2 の第1回の `step` は保護水準の更新を返さない（トレーリング部品が無い）ので、適用の対象が1件も無い |
| 3 | **待機と欠損の規則を3つ実装する**。(a) 欠損方針が食い違うときに強い方針が勝つ順序（`Error` > `SkipEvaluation` > `WaitForInput` > `UsePrevious`）、(b) 出力参照だけが欠けた待機の本数期限をその使用箇所の起動系列で数える解決、(c) 期限 → 追い越し → 失効の判定順（先に成立したものだけで決着させる） | 決定 Q27・Q28・Q29。D05 v2.2 §6.3・§6.8 | **無し**。待機そのものが段階3 で初めて解禁される（段階2 は能力検査で拒否している） |

**判断履歴の固定出力（golden）への影響**は、この3件からは生じない。段階2 の run では 1 の列が空、2 の適用が0件、3 の経路が1度も通らないためである。段階3 のランタイムで段階2 の run を動かしたときに固定出力が更新対象になるのは、本書が別に挙げた2件（`VALUE` の出力が `Observation` で包まれること＝第14節 #17、評価の結果区分の集計に0件の行が2行増えること＝第14節 #7）であり、こちらは Q23〜Q29 の決定とは関係がない。

## 20. 受入れテストの実測値との突き合わせ（v1.2、2026-09-25、段階3 実装 PR 5/5）

段階3 の実装（PR 1/5〜5/5）で、検証戦略 B の run をエンジンの全フェーズまで通せるようになった。T02 再現生成器（`tests/fixtures/acceptance/t02_market.py`）の足で run を回し、第16節の検算値を受入れテスト（`tests/acceptance/test_stage3_completion.py`）で、遅延シナリオ4ケースの差分を意味論テスト（`tests/semantics/backtest/test_delay_scenarios.py`）で固定した。run を組む道具は `tests/fixtures/acceptance/t02_run.py` である。

### 20.1 第16節の検算値（すべて一致）

| 対象 | 本書（期待） | 実測 |
|---|---|---|
| `daily_ema`（`01-06 22:00Z` / `01-07 22:00Z`） | 149.020 / 149.000 | 149.020 / 149.000 |
| `m15_ema`（`01-07 09:00Z`） | 149.255 | 149.255 |
| `stop_level`（`01-07 09:00Z` / `01-07 12:00Z`）と鮮度 | 148.950（08:00Z）/ 149.150（11:00Z） | 148.950（08:00Z）/ 149.150（11:00Z） |
| 建玉 P1 | 約定 149.390 / 数量 41,000 / 初期損切り 148.950 / 更新後 149.150 | 同じ。更新は `01-07 12:00Z` の `ADMISSION` の処理点で適用され、`effective_from` は `BarKey(15m, 12:00Z)`（第10節） |
| 決済 | 149.140、確定損益 −10,291 | 149.140、−10,291 |
| 末尾 | `balance = 989,668`、`NET_PROFIT = −10,332`、`TRADE_COUNT = 1`、`WIN_RATE = 0` | 同じ |
| ケース2 の再開 | `daily_ema` の出力の `decision_time = 01-07T22:00:02Z`、`freshness_time = 01-07T22:00Z`、`subject = BarKey(1d, 01-06T22:00Z)` | 同じ |
| ケース3 の期限 | `WaitEvent(DEADLINE_REACHED)` の処理点が `01-08T22:00Z` の `OPPORTUNITY_LIFECYCLE` | 同じ（日足の連鎖3件と、待機中の `entry_trigger` の要求） |
| ケース4 の追い越し | `Superseded` の処理点が `01-07T23:00Z` の `OPPORTUNITY_LIFECYCLE` | 同じ（`by_request_id` は `[22:00, 23:00)` の足の新しい要求で、その要求も待機に入る） |

受付の中間値（第3.5節の予約 19,762 円、計測リスク 18,040 円、参照価格 ask 149.370、許容不利約定の上限 149.420、費用予算 492 円）も実測と一致した。

### 20.2 本書の記述の誤り（検算値ではないもの。v1.2 で訂正）

| # | 箇所 | 誤り | 訂正 |
|---|---|---|---|
| 1 | 第1.6節・第13節 | 「人工データが `01-07 09:00Z` まで突破を成立させない」 | `01-06 15:00Z` に1時間足の終値 149.250 が前日の日足高値 149.050 を超えて発火し、市場状態（買い不可）により遷移12 で終端する。取引機会は run 全体で3件、表3 は6行。経路1・経路9 の機会の識別子は1つずつ後ろへずれる。**第16節の検算値と各経路の結論は変わらない** |
| 2 | 第1.6節・第10節 | 決済の約定の `FillId` を実際の値（`00000002`）として書いた | エンジンは建玉を持つ執行足の終了ごとに到達判定のための `FillId` を先に採番する（段階2 からの実装）。実測は `00000018`。`FillId` も相対的な見出しとして読む |
| 3 | 第3.4節 | P5 直前の再検査を注文意図・保護水準の出力より前（通し番号8）に置いた | D05 §7.3 の「`EntryProposal` を作る直前」どおり、出力の後に走る（PR #33 が T02 との差として報告した候補1） |
| 4 | 第4節 | 経路2 の参照価格を `149.450`（経路1 の表の値）と書いた | 経路2 の表の `149.426`（ask `149.446`）。受入れテストは経路2 を通さないので実測はしていない（PR #33 の候補2） |

### 20.3 遅延シナリオ4ケースの差分（第17節の「遅延シナリオ別の差分を追跡できる」の実測）

| ケース | 差分が現れた場所 |
|---|---|
| 2（日足2秒） | 取引の結果（表3〜13・18・19）は遅延なしと同じ。差は、日足の連鎖の出力の公開時刻（+2秒）、遅れた公開が作る判断時点（台帳 snapshot が1件ずつ増える）、待機の出来事（`WAIT_STARTED` → `INPUT_ARRIVED` → `RESUMED`、run 末尾の日足だけ `RUN_END_CLOSED`）、評価記録の結果区分（`WAITING` が増える）に限られる |
| 3（日足全体25時間） | 待機期限の到達と追い越しが判断を変える。取引機会は1件も生まれず、注文は run 全体で 0 件（第8節の「その時刻の注文数は 0」が run 全体に広がる） |
| 4（D(Jan7) だけ25時間） | `01-07 22:00Z` より前の判断履歴は遅延なしと1行も違わない（経路1 の発注から経路8 の決済まで同じ）。その後は追い越しが続き、経路9（`01-08 09:00Z` の発火）は起きない |

4ケースとも、評価の結果区分の集計（D07 §6.1）は同じ5語の行を持ち、0件の鍵も行として出るので並べて比べられる。
