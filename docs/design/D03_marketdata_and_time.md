# D03: 市場データ・時刻基盤設計（`odyssey_fx.marketdata`）

作成日: 2026-09-19
状態: **承認（2026-09-20）**。最新版 v1.9（2026-09-24）。v1.6（2026-09-22、PR #22）: 戦略ランタイム設計（D05 v2.0）の要決定 Q10〜Q18 の決定を受け、**D05 §12.1 が挙げた本書への改訂依頼2件を反映した**。(1) 第6.2節に、**基準の足を指定して履歴窓を読む操作**（`history_ending_at`）と、**上限つきで過去の有効足を1本探す操作**（`previous_available`）を足した。後者は、過去値へ遡る欠損方針（`USE_PREVIOUS`、D05 §6.9）が段階3 で有効になるのに、既存の操作では「欠けた期待足の1本手前をたどる」ことができないためである。前者は、入力を待っていた評価が再開したとき、待機に入ったときに固定した基準足から同じ窓を読み直せないと、窓が後ろへずれて答えが変わるためである（D05 §6.8 の手順4）。(2) 第7.2節の「`OnBarClose` の結び付け」を**案から確定**にした。入力を待つ欠損方針（`WAIT_FOR_INPUT`）が段階3 で実装され、その再開契機を足のデータの到着（`Publication`）とする規則が実際に使われるため、案のままにしておく理由が無くなった。v1.1（2026-09-20）: 設計文書 PR #3 の Codex 指摘により、短縮セッションの足の不変条件をカレンダー対応の期待区間で定義（第3.2節・第3.3節・第5.2節）。v1.2（同日）: 公開遅延の非負制約と `available_at >= bar_end`、DST 切替日の起点解決規則、snapshot ダイジェスト対象の列の正規順序を追加。第13節の9項目はすべて推奨案を採用。承認条件5点（①時間足定義の `length` を `nominal_length` に統一、②価格基準の宣言者・宣言日時を `SnapshotId` の対象から外して決定論化、③`QUARANTINED_UNASSIGNED` は再分類まで読めないと明記、④承認前の snapshot を読めないようにする、⑤CLI を暫定 ID フローに合わせる）を反映済み。ADR-0016 条件1（D01〜D03）を本書で充足し、段階1の実装を開始できる。 v1.3（2026-09-22、PR #19）: 第6.3節の執行系列ビューに、**予定上の次の足を返す操作**（`next_scheduled_open_after`）を人間の決定で追加した（3操作 → 4操作）。注文の約定候補を実ファイルの欠損から切り離すためで、D06 §5.3 の引き受けである。 v1.4（2026-09-22、PR #19）: 第3.4節の取引カレンダーに、**休場を適用する前の週の開場区間を返す操作**（`weekly_session_at`）を人間の決定で追加した（3操作 → 4操作）。バックテストの週末持ち越し禁止の判定が、祝日や短縮セッションにも効いてしまうのを直すためで、D06 §5.3 の引き受けである。 v1.5（2026-09-22、PR #20）: 段階2 の実装（PR #20）が残した仮置き事項3件に人間の決定が出たので本文へ反映した。(1) 公開フィード（第7.1節）は**実行区間の終端で終わる足のイベントを出す**（足の始値だけは出さない）。(2) 段階2 の実行と評価が読めるのは**研究履歴だけ**である（第3.8節。封印期間の許可判定は段階4）。(3) as-of ビューの `history`（第6.2節）が受ける**履歴窓は構造で要求する**（D05 §6.3 の決定の裏側。層をまたぐ言い換えを無くすため）。 **v1.7（2026-09-23、2026-09-20 の人間の決定3件を main へ追随させて統合）**: 段階1の実データ受入れ（PR #10）で分類が必要な警告が 23,989 件に達したことを受けた決定3件を反映した。決定の内容は 2026-09-20 のもので変更はなく、main が先に取り込んだ v1.3〜v1.6 の改訂と衝突しないよう版番号だけを繰り下げた。①人間の分類を必須とする検査種別を `MISSING_EXPECTED_BAR` と `UNEXPECTED_BAR` の明示集合に限定し、種別ごとに許される分類結果を分けた（第3.4節・第3.9節）。②分類 1 件を「検査種別 × 区間 × 対象系列（明示集合または全系列）」でまとめて宣言できる形にし、確定時の検査規則を定めた（第3.7節・第3.7.1節・第4節 9・第10節）。③検査報告 `integrity_report.json` を git 管理対象に加えた（第3.7節、ADR-0013 改訂、D01 v2.6）。併せて、確定段階の再実行が暫定 snapshot の実体を内容照合の上で再利用することを明記した（第4節 9。PR #10 の人間レビュー指摘）。併せて、main 追随時の設計適合レビューで見つかった空欄を人間の決定（2026-09-24）で埋め、第3.4節の `weekly_session_at` が `openings` を週の開場区間に含めることを明記した。さらに人間の追加レビューの決定（2026-09-24）2点を反映した: ①通常の週の開場区間に接しも重なりもしない営業例外はカレンダーの構築時検証で拒否する（第3.4節）、②除外した足の件数 `excluded_bar_count` は確定時に計算して `resolved_classifications` にだけ持ち、人間が書く分類宣言には持たせない（第3.7節・第4節）。 **v1.8（2026-09-24、D03 v1.7 の実装 PR #28 が残した仮置き事項2件への人間の決定。除外で原系列が空になる場合の扱いを追記）**: ①再実行で初めて現れる「休場帯の足」をセッション外データ異常と分類した場合は、足を除外せずに確定を止める（第4節の除外規則）。休場の宣言は系列ごとに人間が調整する。保存する報告は暫定・最終の 2 つに限るので、除外すると後から分類を検証できないためである。②カレンダーを変えない再実行では、分類ファイルの `calendar` に暫定 snapshot と同じ版のカレンダーを書く（第10節）。 **v1.9（2026-09-24、人間の決定「FX の祝日を取引日単位の休場としてカレンダーに書けるよう形式を広げる」）**: 実データの元日の欠落が 7 年すべて・全 20 系列で「前日 17:00〜当日 17:00（ニューヨーク時刻）」であり（2017 年のクリスマスも同じ形）、既存の終日休場（現地 0〜24 時）で登録すると当日 17〜24 時に実在する足が休場帯の足になって v1.8 の除外規則で確定できないため、休場規則に**取引日単位の休場**（`covers_trading_day`）を足した（第3.4節）。取引日の境界は新しく定めず、週の開閉の現地時刻（17:00。日足 `1d_ny17` の起点と同じ値）を使う。既存の終日休場の意味は変えない。第4節の除外・確定の規則は変えず、整合だけを明記した。カレンダー設定ファイルには省略可能なキーを 1 つ足すだけで、形式の版は変えない（第9節）。分類ファイルの形式（第10節）も変えない。
上位文書: [上位設計書](fx_research_platform_greenfield_design.md) §3（データ前提）、§4.3.9〜4.3.10（読み方・鮮度）、§4.3.13（公開予定・遅延）、§4.7.13 C（完全性検査・カレンダー）、[全体計画書](fx_research_platform_overall_plan.md) §5.2、[D01](D01_architecture_and_dependency_rules.md) §1・§4・§10.2、[D02](D02_common_kernel.md)、ADR-0013（データ配置）、ADR-0014（期間のアクセス分類）、ADR-0015（初版の対象）
対応段階: 段階1で実装。

## 0. 本書の位置付け

`odyssey_fx.marketdata` の domain 型、受入れ手順、足生成規則、カレンダー、公開スケジュールと遅延シナリオ、as-of ビュー、公開フィード、完全性検査、期間のアクセス分類の物理分離を確定する。戦略の判断・約定処理は扱わない。

凡例は全体計画書第0節に従う。本書が新たに決める事項のうち意味論に影響するものは第13節にまとめ、承認時に確認する。

## 1. 責務と境界（確定）

| 提供するもの | 利用側 | ポート（D01 §4） |
|---|---|---|
| 受入れ済み snapshot（Parquet 実体＋ manifest） | `evaluation`（参照）、`app` | `SnapshotStore`（自パッケージ）、`SnapshotCatalog`（`evaluation` 定義） |
| 判断時点の as-of 読み取り | `strategy.runtime` | `MarketDataView`（`strategy` 定義） |
| 公開イベント列、執行系列、カレンダー | `backtest` | `PublicationFeed`、`ExecutionSeries`、`Calendar`（`backtest` 定義） |

本パッケージは `common` と自パッケージ以外に依存しない（D01 §3.2）。ポート定義を import せず構造的に満たす（D01 §2.2 規則7）。

## 2. 確認済みのデータ事実（受入れの前提）

2026-09-18 に確認した `data/raw/market/` の事実。受入れ時に manifest へ記録し、事実と異なれば受入れを失敗させる。

| 項目 | 事実 |
|---|---|
| ファイル | 10 ペア × 2 時間足（`<SYMBOL>_15m_merged.csv`、`<SYMBOL>_1h_merged.csv`）、計20 |
| 列 | 先頭列（無名）: 時刻、`open`、`high`、`low`、`close`、`volume`、`source` |
| 時刻書式 | `YYYY-MM-DD HH:MM:SS+00:00`（オフセット明示、UTC）。足の**開始**ラベル |
| `source` | `histdata` / `dukascopy`。価格計算には使わず出所として保存 |
| `volume` | HistData 由来は 0。真の出来高と解釈しない |
| 期間 | 2016-01-03 22:00Z 開始。末尾は AUDUSD・EURJPY・EURUSD・USDJPY が 2026-04-10、他6ペアが 2026-05-01 |
| 価格基準 | **検証済みの事実ではない**。上位設計書の記述に基づき人間が bid と**宣言**する。ファイルから検証できないため、manifest には `basis_declaration`（値と `verified: false`。`SnapshotId` の対象）として記録し、宣言者・宣言日時は `declaration_record`（`SnapshotId` の対象外）に分けて記録する。事実とは区別する |

## 3. domain 型（確定。フィールドの細部は案）

### 3.1 系列と価格基準

- `PriceBasis`: `BID` / `ASK` / `MID`。初版の受入れデータは `BID` のみ。
- `SeriesId(symbol: Symbol, timeframe: TimeframeRef, basis: PriceBasis)`。`__str__` は `USDJPY/1h/bid`。

### 3.2 時間足定義 `TimeframeDefinition`

`common.TimeframeRef` が指す定義本体。

| フィールド | 型 | 意味 |
|---|---|---|
| `ref` | `TimeframeRef` | `id` と `version` |
| `nominal_length` | `timedelta` | 名目の長さ（15m、1h、4h、1d）。表示と `BarsWindow` の概算にだけ使い、足の検証には使わない |
| `alignment` | `FixedUtcAlignment \| SessionAlignment` | 足境界の決め方 |

- `FixedUtcAlignment`: UTC のエポックからの `nominal_length` の整数倍に整列（15m、1h）。この整列では足の長さは常に `nominal_length` に等しい。
- `SessionAlignment(tz: "America/New_York", anchors_local: tuple[time, ...])`: 現地時刻の起点（4h は 17:00, 21:00, 01:00, 05:00, 09:00, 13:00。1d は 17:00）から境界を作り、DST を含む `zoneinfo` 規則で UTC へ変換する（上位設計書 §3.2）。固定 UTC 時刻を埋め込まない。**DST 切替日の起点の解決規則**（確定）: 現地時刻が2回現れる日（秋の切り戻し。NY では 01:00 が2度ある）は**最初の出現（`fold=0`）**を起点とし、現地時刻が存在しない日（春の切り替え。NY では 02:00〜02:59 が存在しない）は**次に存在する瞬間**を起点とする。この規則を D02 の `UtcTime.from_local` に `fold` を明示して渡し、曖昧な変換を残さない。**足の長さは固定ではない**: DST 切替日を含む日足は 23 時間または 25 時間、4h 足は 3 時間または 5 時間になる。足の妥当性は「区間の両端が整列規則の計算した境界に一致すること」で検証し、`nominal_length` との一致は要求しない。
- `boundaries(t: UtcTime) -> Interval`: `t` を含む足の**整列上の**区間を整列規則から計算する。`FixedUtcAlignment` は `nominal_length` の整数倍、`SessionAlignment` は現地起点の列から求める。カレンダーは考慮しない。
- `expected_interval(calendar, t: UtcTime) -> Interval | None`: `boundaries(t)` をカレンダーの取引セッションで切り詰めた、**その足が実際に取るべき区間**。区間の末尾が宣言された休場・短縮の開始に掛かる場合は末尾を休場開始で切り詰め、区間全体が休場ならその足は存在しない（`None`）。足の検証と期待足の判定はこちらを使う。
- 初版の定義 ID: `15m`、`1h`、`4h_ny17`、`1d_ny17`（すべて version 1）。`configs/calendars/timeframes_v1.yaml` に置く。

### 3.3 足 `Bar`

| フィールド | 型 | 不変条件・意味 |
|---|---|---|
| `series` | `SeriesId` | — |
| `interval` | `Interval` | `[bar_start, bar_end)`。`timeframe_def.expected_interval(calendar, bar_start) == interval` であること。通常は整列上の区間と一致し、DST 切替日の日足・4h 足は名目より長短する。カレンダーが宣言した短縮セッションでは、期待区間の末尾が休場開始で切り詰められた区間と一致することを要求する（整列上の区間との一致は要求しない） |
| `open` / `high` / `low` / `close` | `Price` | `low <= min(open, close)`、`max(open, close) <= high` |
| `volume` | `Decimal` | `>= 0` |
| `available_at` | `UtcTime` | **`available_at >= interval.end`**（不変条件）。通常は `bar_end` に等しく、遅延シナリオ適用後は後ろへずれる。足の終了前に確定値が見える構成は構築時に拒否する |
| `provenance` | `Provenance` | 出所（`histdata` / `dukascopy` / `aggregated`）と生成元の参照 |

- 足の自然キー `BarKey(series, bar_start)`。連番 ID は持たない。
- 未確定の足は `Bar` として存在しない。執行モデルの始値処理は第7.3節の別イベントで扱う。

### 3.4 取引カレンダー `TradingCalendar`

| フィールド | 型 | 意味 |
|---|---|---|
| `id`、`version` | `str`、`int` | `fx_ny17` v1 |
| `tz` | `ZoneInfo` | `America/New_York` |
| `weekly_open` | `(weekday=Sunday, time=17:00)` | 週の開始（現地） |
| `weekly_close` | `(weekday=Friday, time=17:00)` | 週の終了（現地） |
| `closures` | `tuple[ClosureRule, ...]` | 明示した休場・短縮（現地日付と区間）。形は短縮セッション・終日休場・取引日単位の休場（v1.9）の 3 つ（下記「休場規則の形」） |
| `openings` | `tuple[OpeningRule, ...]` | 明示した営業例外（v1.7）: 週の休場時間帯のうち開場する現地日付と区間。`closures` と重なる宣言は構築時に拒否する。**通常の週の開場区間（`weekly_open`〜`weekly_close`）に接するか重なる営業例外だけを許し、接しも重なりもしない（離れた）営業例外はカレンダーの構築時検証で拒否する**（`MarketDataValueError`。`closures` の不正な宣言と同じ扱い。2026-09-24 の人間の決定）。これにより `weekly_session_at` の戻り値は常に単一の区間になり意味が一意に定まる。離れた営業例外が将来必要になれば、`weekly_session_at` の戻り値の意味と併せて本書の改訂で緩める |

- `is_open(t: UtcTime) -> bool`、`sessions(interval) -> tuple[Interval, ...]`、`expected_bar_starts(timeframe_def, interval) -> tuple[UtcTime, ...]`、`weekly_session_at(t: UtcTime) -> Interval | None`。
- **休場を適用する前の週の開場区間を返す操作を持つ（確定。v1.4、2026-09-22 の人間の決定。PR #19）**。`weekly_session_at(t)` は `t` を含む週の開場区間（`weekly_open` から `weekly_close` まで）をそのまま返し、`closures` で宣言した休場は取り除かない。**`openings` で宣言した営業例外は週の開場区間に含める**（v1.7、2026-09-24 の人間の決定）。営業例外が週の開場区間に接するか重なるなら両者を連結した区間を返し、`t` が営業例外の区間に入るときもその区間（連結したもの）を返す。適用しないのは `closures` だけであり、営業例外の扱いは `sessions()` と揃う。どの週の開場区間（営業例外で広げたものを含む）にも入らない `t`（週と週のあいだ、および週の終わりちょうど）には `None` を返す。**週末をまたぐかどうかだけ**を知りたい利用側のための操作である。バックテストの週末持ち越し禁止の判定（D06 §5.3）が `sessions()` を使うと、祝日を1日宣言しただけで週が2つに割れて見え、**祝日や短縮セッションをまたぐ注文まで週末扱いで拒否される**。**不採用**: 利用側が曜日と時刻から週の開閉を計算し直す案（同じ計算が市場データ側と受付側の2か所に割れ、カレンダーの版を上げたときに片方だけ古くなる）、`sessions()` に「休場を適用しない」引数を足す案（同じ操作の戻り値の意味が引数で変わり、呼び出し側の読み手が毎回引数を確認しなければならない）。
- **休場は宣言制**。土日を UTC で一律除外しない。受入れの検査で見つかった「足が存在すべきなのに無い区間」を、人間が「休場（カレンダーへ追加、版を上げる）」か「データ欠損（そのまま欠損として扱う）」に分類する。分類結果は manifest に残す。
- **営業例外も宣言制**（v1.7）。週の休場時間帯（例: 日曜 17:00 前）に足がある区間は「休場帯の足」（`UNEXPECTED_BAR`）として報告され、人間が「カレンダー側の営業例外（`openings` へ追加、版を上げる）」か「セッション外データ異常（足を除外する）」に分類する。`sessions()` は `weekly_open`〜`weekly_close` に `openings` を加え、`closures` を取り除いた区間を返す。

#### 3.4.1 休場規則の形と取引日単位の休場（確定。v1.9、2026-09-24 の人間の決定）

休場規則 `ClosureRule` は現地日付 `local_date` と、次の **3 つの形のうちちょうど 1 つ**を持つ。2 つ以上を同時に書いた宣言、どれも書かない宣言は構築時に拒否する（`MarketDataValueError`）。

| 形 | 宣言 | 閉じる区間（ニューヨーク現地、半開区間） | 使う場面の例 |
|---|---|---|---|
| 短縮セッション | `start`・`end`（`start < end`） | `local_date` の `start`〜`end` | クリスマスの部分日（大半の年は 03:00〜17:00） |
| 終日休場 | `covers_whole_day=True` | `local_date` の 0:00〜翌日 0:00（**意味は v1.8 から変えない**） | 暦日で閉じる休場 |
| **取引日単位の休場**（v1.9） | `covers_trading_day=True` | **`local_date` の前日の取引日の境界〜`local_date` の取引日の境界**。初版カレンダー `fx_ny17` では前日 17:00〜当日 17:00 | 元日（全 20 系列・7 年すべてがこの形で欠落する）、2017 年のクリスマス |

- **取引日の境界は新しく定めない**。カレンダーの週の開閉の現地時刻（`weekly_open.at`、`weekly_close.at`。初版はどちらも 17:00）を取引日の境界とする。これは日足 `1d_ny17` の起点（第3.2節の `SessionAlignment`、第5.1節「日足は現地 17:00 → 翌 17:00」）と同じ値であり、**取引日単位の休場の区間は、`local_date` の 17:00 に終わる日足の整列上の区間とちょうど一致する**（夏時間の切替を含む取引日は 23 時間または 25 時間になる。現地時刻から UTC への解決は第3.2節と同じ規則）。週の開閉時刻とは「週の最初の取引日が始まり、最後の取引日が終わる時刻」であり、取引日の境界そのものだからである。カレンダーはこの値を `trading_day_boundary`（週の開閉時刻が一致しないカレンダーでは `None`）として答え、休場規則は区間を求めるときにカレンダーから境界を受け取る（休場規則自身は時刻を持たない）。宣言の整列順（第3.4節の正規化）は現地日付・開始・終了に形を加えた鍵で決め、同じ日付の終日休場と取引日単位の休場も順序が一意に定まる。
- **`local_date` はその取引日が終わる現地日付**で名付ける（元日の休場は `local_date: 1 月 1 日`、閉じる区間は 12 月 31 日 17:00〜1 月 1 日 17:00）。FX の取引日の慣行と、日足の終端の日付に揃える。
- **構築時の検証**（`TradingCalendar`、違反は `MarketDataValueError`。`closures` の他の不正な宣言と同じ扱い）:
  1. 取引日単位の休場を 1 件でも持つカレンダーは、`weekly_open.at == weekly_close.at` でなければならない。週の開始と終了の時刻が違うと、取引日の境界が 1 つに決まらないためである。
  2. 取引日単位の休場は、**他の休場（どの形でも）と重なってはならない**。端で接するのは許す（12 月 25 日と 26 日を続けて閉じる、など）。理由: 同じ時刻を 2 つの宣言で閉じると、どちらの宣言がその欠落を説明するのかが宣言から読めない。特に同じ日付の終日休場と併記すると当日 17〜24 時が閉じたまま残り、本形を足した理由（当日 17 時以降の足を休場帯の足にしない）が黙って失われる。終日休場から取引日単位の休場への書き換え漏れを構築時に見つけるための規則でもある。**終日休場と短縮セッションどうしの重なりは本改訂では変えない**（従来どおり検証しない）。
  3. 営業例外（`openings`）と重なる宣言は、他の形と同じく拒否する（第3.4節の表。規則は変えない）。
- **他の規則との整合**（本改訂は次の規則を変えない。形を足しても成り立つことを確認した結果）:
  - 期待区間（第3.2節 `expected_interval`）: 取引日の境界は日足・4h 足（起点 17:00）と、1h・15m（UTC の整数時刻。NY 17:00 は UTC 21:00 または 22:00）の足の境界に一致する。よって取引日単位の休場の中の足は丸ごと「存在すべきでない」（`None`）になり、切り詰められた足は生じない。当日 17:00 以降の足は通常どおり期待される。
  - 休場帯の足の検査（第3.9節 `UNEXPECTED_BAR`）と除外規則（第4節 9、v1.8）: 当日 17:00 以降の足は休場帯の足にならない。したがって、終日休場で登録したときに再実行で初めて現れていた「当日 17〜24 時の休場帯の足」は生じず、v1.8 の規則（再実行で初めて現れた休場帯の足は除外せずに確定を止める）に掛からない。取引日単位の休場の区間の中に足が実在する系列があれば、その足は従来どおり休場帯の足として報告され、v1.8 の規則がそのまま適用される（形に応じた例外は設けない）。
  - 営業例外の検証と `weekly_session_at`: 休場の形によらず同じ（`weekly_session_at` は休場を適用しない）。
  - 分類の突き合わせ（第4節の分類の形式、カレンダー新版で説明された警告）: 休場の宣言を読むのは `sessions()` を通じてだけなので、形による違いは無い。
- **不採用**:
  - (a) **当日 0〜17 時を短縮セッション、前日 17〜24 時をデータ欠損として分類する案**: 休場である事実を「欠損」として manifest に残すことになり、下流の欠損方針（`on_missing`）が休場の時間帯で働く。7 年 × 20 系列の同じ形の欠落を毎回 2 種類の分類に割る必要もある。
  - (b) **前日 17〜24 時と当日 0〜17 時を 2 件の短縮セッションで書く案**: 1 つの休場が 2 件の宣言に割れ、片方の書き忘れを検出できない。短縮セッションは日付をまたがない形（`start < end`）なので、日付をまたぐ 1 つの休場をそのまま表せない。
  - (c) **終日休場の意味を「取引日」に変える案**: 既存のカレンダーと確定済み snapshot が、カレンダーの版を変えないまま別の区間を閉じる意味へ読み替わる（同じ版・同じ宣言で結果が変わり、決定論性を壊す）。
  - (d) **休場規則に任意の開始・終了日時を書ける形**: 表現力は最大だが、17:00 の境界を宣言ごとに書き写すことになり、取引日の境界の定義が宣言の数だけ分散する。取引日と一致しない区間も書けてしまう。
  - (e) **取引日の境界を休場規則に 17:00 として埋め込む案、またはカレンダーに新しいフィールドとして持つ案**: 新しい時刻の定義を持ち込むことになり、週の開閉・日足の起点と食い違いうる。週の開閉時刻を使えば定義は 1 か所に留まる。
- **週の開閉時刻と日足の起点の一致**は、設定ファイル（`configs/calendars/fx_ny17_v1.yaml` と `timeframes_v1.yaml`）の単体テストで固定する。実行時の検査は足さない（カレンダーと時間足定義は別々に構築され、一致しなくても取引日単位の休場の区間が日足の区間とずれるだけで、期待区間の切り詰め（第3.2節）が正しく扱う）。


### 3.5 公開予定 `SeriesSchedule`

`SeriesSchedule(series, timeframe_def, calendar_ref, normal_publication_delay: timedelta = 0)`。`normal_publication_delay` は **非負**（負値は構築時に拒否）。

- 期待される足境界はカレンダーと定義から機械的に導く。
- 通常の公開予定 `scheduled_at = bar_end + normal_publication_delay`。初版はすべて 0。
- 「期待される最新の確定足」（上位設計書 §4.3.10）は、判断時刻 `at` に対し `bar_end <= at` かつカレンダー上存在すべき最後の足。

### 3.6 遅延シナリオ `DelayScenario`

| 型 | 内容 |
|---|---|
| `DelayScenario(id, version, rules: tuple[DelayRule, ...])` | 実験設定に置く（部品パラメータではない） |
| `FixedSeriesDelay(series, delay)` | 系列全体に固定遅延 |
| `InjectedBarDelay(series, bar_start, delay)` | 指定足だけ遅延 |
| `SeededRandomDelay(...)` | 将来用。初版は能力検査で拒否 |

- 各 `DelayRule.delay` は**非負**（負値は構築時に拒否）。適用後 `available_at = scheduled_at + delay` であり、`available_at >= bar_end` が常に成り立つ。OHLC と対象区間は変えない。負の遅延で足の終了前に確定値が見える先読みを、設定の誤りからも起こさせない。
- 実現した `available_at` 列は `PublicationLog` として run に保存する（上位設計書 §4.3.13）。

### 3.7 snapshot manifest `SnapshotManifest`

| フィールド | 内容 |
|---|---|
| `snapshot_id` | 第3.7.1節のダイジェスト対象の `canonical.digest`。`created_at`・`access_log`・`approval` は対象外 |
| `created_at` | 受入れ実行時刻（`app` が渡す）。記録のみで識別には使わない |
| `basis_declaration` | 価格基準の宣言のうち識別に関わる部分（`value: BID`、`verified: false`）。検証済み事実ではない |
| `declaration_record` | 宣言の記録（`declared_by`、`declared_at`）。`snapshot_id` の計算対象外。同じ宣言内容なら誰がいつ宣言しても同じ `snapshot_id` になる |
| `sources` | `SourceFile(path, sha256, rows, symbol, timeframe, declared_basis, provenance_counts)` の列 |
| `conversion` | 変換コード版、時刻規約（`explicit_offset_utc`）、集約規則の版、カレンダー版 |
| `series` | `SeriesManifest(series_id, covered_interval, bar_count, partitions)` の列 |
| `partitions` | `PartitionRecord(partition_id, series_id, access_class, interval, bar_count, digest)` |
| `integrity_report_ref` | 最終の検査報告（`integrity_report.json`）のダイジェスト |
| `provisional_report_ref` | 暫定段階の検査報告（人間が分類の根拠にした報告）のダイジェスト（v1.7）。確定段階で 5〜7 を再実行しなかった場合は `integrity_report_ref` と同じ値。異なる場合は暫定報告を `integrity_report_provisional.json` として同ディレクトリに保存し git 管理する（再実行で消えた警告に対する分類の根拠を別クローンでも検証できるようにする） |
| `closure_decisions` | 検査で見つかった警告に対する人間の分類の列（v1.7: `ClassificationDecision` の列。フィールド名は互換のため据え置く）。1 件は `kind`（分類対象の検査種別）、`interval`（区間）、`series`（対象系列の**解決済み**明示集合。確定時に「全系列」を snapshot 内の具体的な `SeriesId` 集合へ解決して保存する）、`outcome`（分類結果。第3.9節の種別ごとの語彙）、`note`（根拠・注記）、`calendar_ref`（参照するカレンダー新版。任意）を持つ。**記入されたままの分類は監査用の記録であり `snapshot_id` の計算対象外**（同じ警告集合を 1 件の広い区間で書いても複数の狭い区間で書いても識別子が変わらないようにするため） |
| `resolved_classifications` | 分類を警告 1 件ごとに解決した列（v1.7）。要素は `(kind, series_id, warning interval, outcome, excluded_bar_count)`。**`excluded_bar_count`（セッション外データ異常として除外した足の件数）は確定時に計算し、この列の要素にだけ持つ**（人間が書く分類宣言 `ClassificationDecision` には持たせない。第4節の除外規則）。確定時に `closure_decisions` を暫定報告と最終報告の分類対象の警告に適用して導き、`snapshot_id` の計算対象にする。**分類の正規化内容とはこの列を指す** |
| `legacy_access` | 旧基盤から引き継いだ閲覧・使用履歴（`LEGACY_HOLDOUT` の `CONSUMED` 判定の根拠） |
| `access_log` | manifest 内には持たない。独立ファイル `access_log.jsonl`（追記専用、git 管理）に置き、`HoldoutState` はそこから導出する。`snapshot_id` の計算対象外 |
| `approval` | 人間の受入れ承認（承認者・日時・コメント）。`snapshot_id` の計算対象外 |

manifest は `data/snapshots/<snapshot_id>/manifest.json`（git 管理）。検査報告 `integrity_report.json`（確定段階で再実行した場合は暫定報告 `integrity_report_provisional.json` も）と閲覧記録 `access_log.jsonl` も同ディレクトリで git 管理する（v1.7、ADR-0013 改訂、D01 v2.6）。検査報告は manifest のダイジェスト対象であり承認・読み取り関門が必要とするため、追跡しなければ別クローンで Parquet を復元しても snapshot を検証できない。検査報告には価格や封印期間の統計値を含めず、検査種別・系列・区間・重大度・構造的な詳細だけを保存する（第3.9節）。実体（partition の Parquet）と `_pending/` 配下の暫定成果物は git 管理外のまま。

#### 3.7.1 `SnapshotId` の対象と二段階フロー（確定）

`SnapshotId` の対象: `sources`、`conversion`（カレンダー版を含む）、`basis_declaration`（値と `verified` のみ）、`series`、`partitions`、`integrity_report_ref`、`provisional_report_ref`、`resolved_classifications`、`legacy_access`。対象外: `created_at`、`declaration_record`、`closure_decisions`（記入されたままの分類。v1.7 で対象外に変更）、`access_log`、`approval`。対象はすべて決定論的な内容であり、実行時刻・操作者・承認者は含めない。

**列の正規順序**（確定）: ダイジェスト対象の列は、ファイルシステムの列挙順や検査の実行順に依存しないよう、符号化前に次の鍵で整列する。`sources` は `path`（POSIX 相対パス、コードポイント順）。`series` は `SeriesId` の文字列。`partitions` は `(series_id 文字列, access_class, interval.start)`。`legacy_access` は `(series_id 文字列, interval.start)`。`resolved_classifications` は `(kind, series_id 文字列, interval.start, outcome)`（v1.7）。`closure_decisions` は保存時に `(kind, interval.start, interval.end, series 文字列の整列済み列, outcome)` で整列するが `SnapshotId` には入らない。識別子に入るのは警告 1 件ごとに解決した分類なので、同じ警告集合に同じ結果を与える分類は、区間のまとめ方や「全系列」か明示集合かによらず同じ `snapshot_id` になる。完全性検査の報告（`integrity_report_ref` の対象ファイル）は `CheckResult` を `(series_id 文字列, kind, interval.start, detail の正規化表現)` で整列して符号化する。manifest の保存形式も同じ順序で書く。

受入れは二段階で行う。

1. **暫定段階**: 第4節の 1〜8 を実行し、`closure_decisions` が空の状態で `provisional_id` を計算する。出力は `data/snapshots/_pending/<provisional_id>/` に置く。暫定 snapshot は as-of ビュー・公開フィードから読めない（バックテストの入力にできない）。
2. **確定段階**: 人間が分類対象の警告（第3.9節の明示集合。v1.7）を分類して `closure_decisions` を記入する。分類でカレンダーを変更した場合は版を上げ、**暫定 snapshot の原系列の足を内容照合の上で再利用して** 5〜7 を再実行する（原ファイルは読み直さない。第4節 9）。分類確定後に**最終 `snapshot_id`** を計算し、ディレクトリを `data/snapshots/<snapshot_id>/` へ移す。`approval` はこの後に記入し、識別には影響しない。
3. **承認前は読めない**: `approval` が記入されるまで、最終ディレクトリにある snapshot も as-of ビュー・公開フィード・`SnapshotCatalog` から読めない（`SnapshotNotApproved`、構造エラー）。暫定 snapshot（`_pending/`）は承認の対象にならず、常に読めない。

同じ原ファイル・設定・コード版・分類なら同じ最終 `snapshot_id` になる。分類が異なれば別 snapshot である。

### 3.8 アクセス分類（ADR-0014）

- `AccessClass`: `RESEARCH_HISTORY` / `LEGACY_HOLDOUT` / `QUARANTINED_UNASSIGNED`。
- `HoldoutState`（`LEGACY_HOLDOUT` のみ、ADR-0014 2026-09-20 改訂）: `SEALED` / `CONSUMED`。旧基盤で未観測と確認できた partition だけを `SEALED` とし、使用済みまたは履歴不明なら `CONSUMED`（または `QUARANTINED_UNASSIGNED` へ再分類）。`SEALED → CONSUMED` は不可逆。状態は manifest の追記専用 `access_log` から導出し、直接書き換えるフィールドを持たない。
- `CONSUMED` は holdout としての再選定・最終評価に永久に使用禁止。研究・開発用途では明示的な opt-in がある場合のみ読め（既定は不可）、利用の事実と目的を run manifest に記録し、結果を holdout 成績として扱わない。
- `SEALED` の読み取りは、許可発行 → 消費記録の追記 → origin への push 成功 → データ公開の順で fail-closed に行い、push 成功前にデータを返さない。push の non-fast-forward 拒否を compare-and-set として使い、複数クローン間の重複消費を防ぐ。origin に到達できなければ `SEALED` は読めない（ADR-0014 2026-09-20 追記。gate の実装は D07。本パッケージは `access_log.jsonl` の追記と状態導出、許可 partition 集合の受け取りを担う）。
- `QUARANTINED_UNASSIGNED` は、未観測の確認と別 ADR による再分類（sealed holdout または研究履歴）が行われるまで読めない。既定の経路でも `holdout_gate` でも許可しない。
- 期間境界（初版）: `RESEARCH_HISTORY = [2016-01-01Z, 2024-01-01Z)`、`LEGACY_HOLDOUT = [2024-01-01Z, 2026-01-01Z)`、`QUARANTINED_UNASSIGNED = [2026-01-01Z, ∞)`。
- **足の所属は `bar_end` で決める**（案）。`bar_end` が境界以上の足は後ろの区分に入る。理由: 2023-12-31 22:00Z に始まり 2024-01-01 22:00Z に終わる日足を研究側に入れると、2024 年の情報が研究区分に漏れる。`bar_end` 基準なら研究区分は 2024 年以降の情報を一切含まない。
- 物理分離は raw CSV の移動ではなく、受入れが生成する partition 単位で行う（ADR-0014）。partition ディレクトリ名にアクセス分類を含める（例: `USDJPY_1h_bid/RESEARCH_HISTORY/`）。
- **段階2 の実行と評価が読めるのは研究履歴（`RESEARCH_HISTORY`）だけである**（v1.5、2026-09-22 の人間の決定）。合成が as-of ビューへ渡す許可 partition 集合に、封印期間（`LEGACY_HOLDOUT` の `SEALED`）と未分類の隔離期間を1件も入れない。封印期間を読むための許可判定（`holdout_gate`）は段階4 で足す。それまで段階2 の実行は封印されたデータへ触れる経路を1本も持たない。

### 3.9 完全性検査の結果 `IntegrityReport`

`CheckResult(kind, severity, series, interval, detail)` の列。`kind` の初期語彙:

| kind | 内容 | 既定の severity |
|---|---|---|
| `DUPLICATE_TIMESTAMP` | 同一 `bar_start` の重複行 | ERROR（受入れ失敗） |
| `OHLC_INCONSISTENT` | `low <= open/close <= high` の違反、非正の価格 | ERROR |
| `NAIVE_OR_FOREIGN_TZ` | オフセットなし、または UTC 以外のオフセット | ERROR |
| `IRREGULAR_INTERVAL` | 足の開始が定義の整列に合わない | ERROR |
| `MISSING_EXPECTED_BAR` | カレンダー上存在すべき足の欠落 | WARN（**分類必須**。結果: `CLOSURE`（休場。カレンダーの `closures` へ追加して版を上げる）/ `DATA_GAP`（データ欠損。そのまま欠損として扱う）） |
| `UNEXPECTED_BAR` | カレンダー上休場の時間帯に足がある | WARN（**分類必須**。結果: `CALENDAR_EXCEPTION`（カレンダー側の営業例外。`openings` へ追加して版を上げる）/ `OUT_OF_SESSION_DATA`（セッション外データ異常。第4節 9 の除外規則で足を除外する）） |
| `CROSS_SYMBOL_MISALIGNMENT` | 同じ時間足で銘柄間の足境界がずれる | WARN（記録のみ。分類は要求しない） |
| `SOURCE_TRANSITION` | `source` の切替点 | INFO |
| `ZERO_VOLUME_SPAN` | volume 0 の連続区間 | INFO |
| `DST_BOUNDARY_ANOMALY` | DST 切替週の足数・境界が期待と異なる | WARN（記録のみ。分類は要求しない） |

**分類対象の検査種別**（v1.7、確定）: 人間の分類を必須とするのは `CLASSIFIABLE_KINDS = {MISSING_EXPECTED_BAR, UNEXPECTED_BAR}` の**明示集合**であり、「WARN 全体」ではない。集合の変更は本書の改訂を伴う。他の WARN（銘柄間の境界ずれ、DST 異常）は検査報告に保存し、確定・承認・読み取りの条件にしない。種別ごとに許される分類結果は上表のとおりで、`MISSING_EXPECTED_BAR` に `CALENDAR_EXCEPTION` を、`UNEXPECTED_BAR` に `CLOSURE` / `DATA_GAP` を与える分類は構築時に拒否する（休場帯の足を休場・欠損へ無理に当てはめない）。

`LEGACY_HOLDOUT` と `QUARANTINED_UNASSIGNED` の partition に対する検査結果は、構造情報（件数・区間・種別）だけを含め、価格の統計を含めない。

## 4. 受入れユースケース（`application.acceptance`）（確定）

```text
1. 原ファイルの登録        sha256、行数、銘柄・時間足の推定（ファイル名から）を SourceFile に記録
2. 列対応の適用            configs/datasources/legacy_merged_csv_v1.yaml の宣言で列を対応付ける。
                           時刻の見た目から規約を推測しない（上位設計書 §3.2）
3. 正規化                  時刻: オフセット必須、UTC へ。価格: 文字列から Decimal（float を経由しない）。
                           volume: Decimal。source: provenance
4. 構造検査                DUPLICATE / OHLC / TZ / IRREGULAR。ERROR があれば受入れ失敗
5. カレンダー照合          MISSING_EXPECTED_BAR / UNEXPECTED_BAR / CROSS_SYMBOL / DST を WARN として報告
6. 上位足の生成            1h → 4h_ny17 / 1d_ny17（第5節）。生成元の参照を provenance に残す
7. アクセス分類と partition  bar_end 基準で partition に分け、Parquet に書き出す（adapters）
8. 暫定 manifest 生成      provisional_id を計算し _pending/ に manifest.json を書く（第3.7.1節）
9. 分類と確定              人間が分類対象の警告（第3.9節の明示集合）を分類（closure_decisions）。カレンダー
                           変更なら版を上げて 5〜7 を再実行（暫定 snapshot の実体を内容照合の上で再利用）。
                           分類確定後に最終 snapshot_id を計算してディレクトリを確定し、approval を記入する
```

**分類の形式と確定時の検査**（v1.7、確定）。分類 1 件は「分類対象の検査種別・区間・対象系列（明示集合、または snapshot 内の全系列）・分類結果・根拠と注記・必要なら参照するカレンダー新版」を表し、その区間に**完全に含まれる**同種別の警告（対象系列のもの）をすべて分類したものとみなす。確定時に次を検査し、違反は構造エラーで確定を拒否する。

1. 分類対象の警告がすべて**ちょうど 1 件**の分類に含まれる（未分類なし）。
2. 同じ警告を複数の分類が覆わない（競合する分類は拒否。区間の重なりが警告を共有しなければ許容）。
3. 対応する警告が 1 件もない分類を拒否する（分類は報告された警告に対してのみ行う）。
4. 「全系列」は確定時に snapshot 内の具体的な `SeriesId` 集合へ解決して保存する。保存後の分類は明示集合だけを持つ。
5. 分類の範囲を広げても、報告されていない警告を捏造しない（分類が警告を増やすことはない。除外規則で足が減っても警告は再検査で導く）。
6. `SnapshotId` には分類を警告 1 件ごとに解決した `resolved_classifications`（第3.7節）を含め、記入されたままの `closure_decisions` は含めない。同じ警告集合に対する同じ分類結果は、区間のまとめ方によらず同じ `snapshot_id` になる。

**再実行の入力**（v1.7、確定）。カレンダー新版を伴う分類では、暫定 snapshot の partition から読み戻した原系列の足（出所が `AGGREGATED` でないもの）を再利用し、原ファイルは読み直さない。再利用の前に、検査報告の実ダイジェストが暫定 manifest の `integrity_report_ref` と一致すること、全 partition の系列・件数・区間・内容ダイジェストが暫定 manifest の記録と一致することを検証し、不一致なら何も書かずに失敗する。`sources`・コード版・時刻規約・集約規則の版・`created_at` は引き継ぎ、カレンダーの識別と版だけを置き換える。時間足定義は id と版まで暫定 snapshot の系列と一致しなければ失敗。分類の突き合わせは**暫定報告（人間が分類の根拠にした報告）と最終の再実行後の報告の和集合**に対して行う: 各分類は暫定報告または最終報告のいずれかの分類対象の警告に対応しなければならず（どちらにも対応しない分類は拒否し、失敗時に列挙する）、暫定報告の分類対象の警告と最終報告に残る分類対象の警告はすべて分類済みでなければならない。カレンダーの修正が**新たな警告を生む**ことがある（例: ある系列の休場を追加すると別系列の足が「休場帯の足」になる。営業例外を追加すると別系列に欠落が現れる）。その場合、確定は「再実行後に未分類の警告が残る」として失敗し、新たな警告を表示する。人間はそれらの分類を追記して同じ暫定 snapshot に対して確定を再実行する（**反復的な分類**。暫定 snapshot は失敗時に変更されない）。反復の途中の版（例: カレンダー v2）でだけ現れ、最終の版（v3）で消えた警告に対する分類は「対応なし」として列挙されるので、人間はそれを分類ファイルから削除して再実行する。中間の報告は保存も検証もしない（最終 snapshot の根拠は暫定報告と最終報告の 2 つで閉じる）。確定した snapshot には、暫定報告を `integrity_report_provisional.json`、最終報告を `integrity_report.json` として保存し、両方のダイジェストを manifest に記録する（第3.7節）。読み取り関門は両方の報告を検証し、`closure_decisions` が両報告の和集合の警告に対応すること、`resolved_classifications` が両報告の分類対象の警告と一致することを確かめる。

**セッション外データ異常の除外規則**（v1.7、確定）。`UNEXPECTED_BAR` を `OUT_OF_SESSION_DATA` と分類した区間の足は、確定段階の再実行時に原系列から除外し partition に含めない。**この分類が 1 件でもあれば、カレンダー変更の有無にかかわらず 5〜7 を再実行する**（除外後の原系列に対してカレンダー照合・上位足の生成・partition 分けをやり直す。再実行の入力と検証は上記「再実行の入力」と同じ）。上位足は除外後の原系列から再生成する。除外は WARN 対象の足に限る（ERROR に関与する足は既に受入れ失敗している）。除外した足の件数は、`note` ではなく構造的な `excluded_bar_count` として**確定時に計算し、`resolved_classifications`（第3.7節）の要素にだけ持つ**。人間が書く分類宣言（`closure_decisions` の `ClassificationDecision`）には持たせない。除外した区間は、同じ要素の警告区間で表す。`sources` の行数は原ファイルの行数のまま変えない。除外後に「存在すべき足の欠落」が新たに生じることはない（休場帯の足だから）。**除外の対象は暫定報告に現れた休場帯の足に限る**（v1.8、2026-09-24 の人間の決定）。再実行で初めて現れた休場帯の足（例: USDJPY の欠落を休場と宣言したために、同じ時間の EURUSD の足が休場帯の足になる）をセッション外データ異常と分類した場合は、**足を除外せずに確定を止め**、除外されなかった足を列挙する。人間は休場の宣言を系列ごとに調整する（その系列には休場を宣言しない、など）。理由: 確定 snapshot が保存する検査報告は暫定報告と最終報告の 2 つに限る（中間の報告は保存しない）。暫定報告に無い警告を除外すると最終報告からも消え、その分類を 2 つの報告のどちらからも検証できなくなる。**不採用**: 除外して中間の報告を追加保存する案（最終 snapshot の根拠が 2 つの報告で閉じなくなり、読み取り関門の検証対象が増える）。

**除外で原系列が空になる場合は確定を止める**【決定 2026-09-24】。セッション外データ異常の除外で、ある原系列の足がすべて消える場合は、他に原系列があるかどうかにかかわらず、既存の「原系列が 1 本も無ければ止める」規則と同じエラーで確定を止める。空の系列を黙って落として原ファイルの記録（`sources`）にだけ残すことはしない。

- 受入れは決定論的。同じ原ファイル・設定・コード版・分類なら同じ最終 `snapshot_id` になる（`created_at` は識別に含めない）。
- 受入れ自体は封印区分の価格を読むが、出力（partition 実体）は gate の外に置かれ、報告には価格統計を含めない（第3.9節）。
- 初版は 20 ファイルすべてを受け入れる（保管・受入れ能力を残す、ADR-0015）。縦断実行で使うのは USDJPY だけ。

`configs/datasources/` は D01 §10.1 の一覧にない。列対応の宣言はカレンダーでも銘柄でもないため、D01 の次回改訂で `configs/datasources/` を追加する（サブディレクトリの追加であり依存規則には影響しない）。

## 5. 上位足の生成（`application.aggregation`）

### 5.1 規則（確定）

- 対象: 1h（bid）→ 4h_ny17、1d_ny17。15m からは生成しない（15m は執行系列）。
- 区間は `SessionAlignment` で決める。日足は現地 17:00 → 翌 17:00。4h は現地 17,21,1,5,9,13 起点。
- OHLC: 始値＝最初の構成足の始値、高値＝最大、安値＝最小、終値＝最後の構成足の終値、volume＝合計。
- `bar_start` は**区間の開始**（最初の観測時刻ではない）。旧基盤の「最初の観測時刻ラベル」は採用しない（第13節 C-4）。
- `available_at` = 構成足の `available_at` の最大値（通常は `bar_end`）。

### 5.2 不完全足の扱い（案）

- 区間内にカレンダー上期待される構成足がすべて存在する場合だけ生成する。
- 欠落がある区間は生成せず、集約系列の `MISSING_EXPECTED_BAR` として報告する。下流は通常の欠損規則（`on_missing`）で扱う。
- 「一部の構成足で作った不完全足」を採用する選択肢は初版に設けない（暗黙の部分集計を避ける。上位設計書 §3.2）。
- 短縮セッション（カレンダーが宣言）では、集約足の区間は `expected_interval` で切り詰められた区間とし、その区間内に期待される構成足が揃えば完全とみなす。

### 5.3 旧集約の再現（第13節 C-4）

旧基盤の集約出力は本リポジトリに引き渡されていないため、照合対象が存在しない。再現版を作っても検証できない。**再現版は作らず、`ny17_v2`（本節の規則）を唯一の集約規則とし、C-4 を ADR として記録する**ことを推奨する。将来、旧出力が入手できた場合は別版として追加する。

## 6. as-of ビュー（`application.asof`）（確定）

`MarketDataView`（`strategy.runtime.ports` で定義、D04/D05 で最終化）を実装する。

### 6.1 構築

`AsOfView(snapshot, allowed_partitions: frozenset[PartitionId], schedules, publication_log)`。

- 読める partition は構築時に固定する。範囲外の系列・区間への要求は `HoldoutAccessViolation`（構造エラー。入力欠損ではない）。
- `LEGACY_HOLDOUT` の partition は `evaluation.application.holdout_gate` を通過した場合だけ `allowed_partitions` に入る（D07）。`QUARANTINED_UNASSIGNED` の partition は、別 ADR による再分類が行われるまで**いかなる経路でも** `allowed_partitions` に入らない（gate も許可を発行しない）。
- 構築時に snapshot が承認済み（`approval` あり、最終 `snapshot_id` ディレクトリ）であることを検査し、暫定・未承認の snapshot は `SnapshotNotApproved` で拒否する（第3.7.1節）。

### 6.2 読み取り操作

| 操作 | 意味 |
|---|---|
| `latest_available(series, at) -> Bar \| MissingInput` | `available_at <= at` の足のうち、期待される最新足。期待足が未到着なら `LATEST_BAR_UNAVAILABLE`。古い足へ黙って戻らない |
| `history(series, window, at, *, end_offset_bars=0) -> tuple[Bar, ...] \| MissingInput` | 期待される最新足から `end_offset_bars` 本手前を末尾とし、`BarsWindow.count` 本または `DurationWindow` の範囲。窓内の期待足がすべて存在し有効でなければ `INPUT_MISSING_OR_INVALID`。データ開始前を含めば `WARMUP_INSUFFICIENT` |
| `bar(series, bar_start, at)` | 指定足。`available_at > at` なら不可視 |
| `history_ending_at(series, window, base_bar_start, at, *, end_offset_bars=0) -> tuple[Bar, ...] \| MissingInput`（v1.6） | **基準の足を `base_bar_start` で指定して**読む履歴窓。`history` と同じ窓の条件・同じ欠損の返し方で、違いは1点だけ: `history` が基準を「`at` における期待される最新足」から求めるのに対し、本操作は**呼び出し側が渡した足を基準にする**。`end_offset_bars` の適用は `history` とまったく同じで、基準の足から `end_offset_bars` 本手前が窓の末尾になる。`base_bar_start` が `at` の時点で不可視なら `LATEST_BAR_UNAVAILABLE`、`at` における期待される最新足より後の足を基準に指定されたら（未来参照）構造エラー |
| `previous_available(series, before_bar_start, at, *, max_lookback) -> Bar \| MissingInput`（v1.6） | `before_bar_start` の**1本手前から古い側へ**たどり、`available_at <= at` で有効な足を**最初に1本**返す。`max_lookback` は**本数か経過時間のどちらかを読み出せる窓**（第6.2節の末尾の構造的な要求。`history` が受ける窓と同じ形）で、本数なら予定上の足でその本数まで、経過時間なら `at` からその時間だけさかのぼった範囲までたどる。**経過時間を本数へ直すのはビューの側**である（カレンダーと時間足定義を持つのは市場データ層だけだから）。範囲に有効な足が無ければ `INPUT_MISSING_OR_INVALID`、範囲がデータ開始前に及べば `WARMUP_INSUFFICIENT`。上限は呼び出し側が必ず渡し、既定値を持たない |
| `expected_latest_key(series, at) -> BarKey` | 判断時刻に対し存在すべき最新足の鍵 |
| `freshness(series, bar) -> UtcTime` | 鮮度基準時刻（確定足は `bar_end`） |

- `max_age` の追加検査（`at - bar_end <= max_age`、UTC で休場含む）は `InputReadSpec` を解釈する戦略ランタイムが行い、ビューは鮮度基準時刻を返す（上位設計書 §4.3.10）。
- `DurationWindow`: `bar_end` が `(at' - duration, at']` に入る足（`at'` は末尾足の `bar_end`）。範囲内の期待足がすべて必要。範囲がデータ開始前に及べば `WARMUP_INSUFFICIENT`。全期間休場で空なら `INPUT_MISSING_OR_INVALID`（空履歴からの計算は許さない）。
- `end_offset_bars` は「当該足を除く過去 N 本」を表現するためのもので、宣言側のフィールド名は D04 で決める（`exclude_latest_bars`、D04 §6.2）。
- **過去の有効足を1本だけ探す操作を置く理由**（v1.6、2026-09-22）。過去値へ遡る欠損方針（`USE_PREVIOUS`、D05 §6.9）が段階3 で有効になると、「期待される最新足が欠けているとき、上限の範囲で直近の有効な足を使う」動作が要る。既存の3操作ではこれができない。`latest_available` は期待足が無ければ欠損を返して**古い足へ黙って戻らない**（それがこの操作の役目である）、`bar` は探したい足の開始時刻を呼び出し側が既に知っていることを前提にする、`history` は窓内に1本でも欠ければ全体を拒否する。戦略ランタイムはカレンダーにも時間足定義にも到達できないため（D01 §3.2 の契約 F2）、前の足の開始時刻を自分で計算できない。そこで「1本手前から古い側へ、上限本数までたどって最初の有効足を返す」操作をビュー側に置く。上限を呼び出し側が必ず渡すのは、宣言の遡り上限（`UsePrevious.max_lookback`）をそのまま渡させるためであり、既定値を置くと暗黙の遡り距離が生まれる。**上限を本数に限らず経過時間でも受けるのは、宣言が両方を許しているから**である（D04 §6.2 の2つの窓型）。本数しか受けないと、戦略ランタイムが経過時間を本数へ直さなければならないが、そのためにはカレンダーと時間足定義が要り、市場データ層しか持っていない。窓を具体クラスではなく構造で受けるのは、`history` と同じ理由（本節の末尾、v1.5）である。**不採用**: `latest_available` に「古い足へ戻ってよい」引数を足す案（同じ操作が2つの意味を持ち、遡りを宣言していない入力でも古い足が返る経路ができる）、戦略側が `bar` を呼びながら足の開始時刻を数える案（カレンダーと時間足定義の規則が市場データ層と戦略層に割れる）。
- **基準の足を指定して読む操作を置く理由**（v1.6、2026-09-22。D05 §12.1 の依頼1）。入力を待っていた評価が再開すると、判断時刻は再開した時刻へ進むが、**読み直す窓は待機に入ったときに固定した対象足を末尾としなければならない**（上位設計書 §4.3.14「`HistoryWindow` も同じ基準から窓を固定する」、D05 §6.8 の手順4）。`history` は判断時刻 `at` から「期待される最新足」を求めて基準にするため、再開時に呼ぶと窓が後ろへずれ、待たなかった場合と別の足を読む。最新の1件を読む側は既存の `bar(series, bar_start, at)` で固定した足を読めるので、足りないのは履歴窓の1操作だけである。**引数が「窓の末尾」ではなく「オフセットを適用する前の基準足」なのは、`end_offset_bars` を二重に適用しないため**である。呼び出し側が末尾（オフセット適用後）の足を渡したうえで同じオフセットをもう一度渡すと、`exclude_latest_bars=1` の窓は再開時だけさらに1本古い位置へずれ、待たなかった場合と違う答えになる。基準足を渡す形にすれば、`history` と `history_ending_at` は基準の求め方だけが違う同じ操作になる。`at` は引き続き必須で、未来参照が不可能であることは変わらない。**不採用**: `history` の `at` に元の判断時刻を渡す案（`available_at > 元の at` の足が見えなくなるだけでなく、鮮度の判定と根拠記録の読取時点まで過去へ戻り、遅延して届いたデータを読むという再開の目的そのものが達成できない）、呼び出し側が本数を数え直して `end_offset_bars` で調整する案（休場と短縮セッションを戦略側で数えることになり、カレンダーの規則が2か所に割れる）。
- **`history` が受ける履歴窓は、具体クラスではなく構造で要求する**（v1.5、2026-09-22 の人間の決定。D05 §6.3 の決定の裏側）。呼び出し側の戦略ランタイムは `marketdata.application` を参照できない（D01 §3.2 の契約 F2）ので、本節の `BarsWindow` / `DurationWindow` そのものを渡せない。具体クラスを要求すると、両方を参照できる層（`app`）で窓を言い換えるほかなくなる。そこで `history` は「本数を読み出せる」か「経過時間を読み出せる」ことだけを要求し、本数が解決済みの正の整数であること・経過時間が正であることをその場で確かめる（具体クラスの構築時検査を通らない値が来るため）。`BarsWindow` / `DurationWindow` は `marketdata` 自身が窓を組み立てるときの具体型として残る。
- 未来参照は構造的に不可能: すべての操作が `at` を必須にし、`available_at > at` の足は返さない。

### 6.3 執行系列ビュー

`ExecutionSeries`（`backtest.application.ports`）を同じ snapshot から実装する。戦略ビューとは別インスタンスで、`open_of(bar_key)`、`bar(bar_key)`、`next_bar_key_after(t)`、`next_scheduled_open_after(t)` の4操作を持つ。open だけを先に公開する段階（第7.3節）を持つため、戦略ビューには渡さない。

| 操作 | 意味 |
|---|---|
| `open_of(bar_key)` | 執行足の始値。足が無ければ `None` |
| `bar(bar_key)` | 執行足そのもの（高値・安値・終値を含む）。足が無ければ `None` |
| `next_bar_key_after(t)` | `t` より後に始まる、**実在する**最初の執行足の鍵。実際に読んだ足を根拠記録へ載せるときに使う |
| `next_scheduled_open_after(t)` | `t` より後に始まる、**予定上の**最初の執行足の鍵（確定。v1.3、2026-09-22 の人間の決定。PR #19） |

**予定上の足を返す操作を持つ（確定。v1.3）**。注文の約定候補は「カレンダーと執行系列の足スケジュールから決め、将来価格や実ファイルの欠損を候補選択に使わない」と定められている（D06 §5.3、上位設計書 §4.7.13 B）。実在する足を辿る操作だけでは、休場でない区間で足が1本欠けているだけで候補が次の足へずれ、**データの欠損が受付結果を変える**。そこで、カレンダー（`TradingCalendar`）と時間足定義（`TimeframeDefinition.expected_interval`）だけから次の足の開始時刻を決める操作を置く。ビューは系列の公開予定（`SeriesSchedule`、第3.5節）を構築時に受け取り、系列の食い違う予定表は拒否する。休場が続く区間は飛ばして次の開場中の足を返し、run 区間より長い休場は扱わない（上限本数を超えたら `None`）。**不採用**: 実在する足から探し続ける案（欠損が受付結果を変え、D06 §5.3 に反する）、受付層がカレンダーと時間足定義を直接持って計算する案（同じ計算が市場データ側と受付層の2か所に割れ、時間足定義の版が変わったときに片方だけ古くなる）。

## 7. 公開フィード（`application.publication`）

### 7.1 イベント（確定）

run 区間内の全系列について、`available_at` 順に次を生成する。

| イベント | 発生時刻 | 意味 |
|---|---|---|
| `ScheduledBoundary(series, bar_key, bar_end)` | `bar_end` | カレンダー上その足が終了する予定時刻の通知。データ到着とは独立 |
| `Publication(series, bar_key, available_at)` | `available_at`（遅延適用後） | 足のデータが利用可能になった通知 |
| `ExecutionOpen(series, bar_key, open_time)` | `bar_start` | 執行系列の始値が処理可能になる通知。戦略には配送しない |
| `ExecutionBarComplete(series, bar_key)` | `bar_end` | 執行系列の足が終了し、足内約定を解決できる通知 |

- **実行区間の終端を含む**（v1.5、2026-09-22 の人間の決定）。`bar_end` が `run_interval.end` にちょうど等しい足について、足の終了（`ExecutionBarComplete`）・公開（`Publication`）・予定境界（`ScheduledBoundary`）は**生成する**。足の**始値**（`ExecutionOpen`）だけは終端を含めない。理由: D06 §10.1 の手順1 が「`run_end` で終了する足までの内部約定・口座更新を解決する」と定めており、T01 第9節はその足の終値を残存建玉の最終評価価格にしている。半開区間で絞るとその判断時点が1つ丸ごと起きず、含み込み資産と仮決済損益が1本手前の足の終値で決まってしまう。一方、同節の手順5 は「`run_end` から始まる足の始値処理は行わない」と定めるので、始値だけは除く。
- 同時刻の順序: `ExecutionBarComplete` → `Publication`（同じ `available_at` のものをまとめて P0）→ `ScheduledBoundary` → `ExecutionOpen`。上位設計書 §4.7.12 の「前足終了 → 内部約定 → 公開 → 判断 → 次足 open」に対応する。フェーズの正式な列挙は D06。
- 同時刻・同フェーズ内の系列順は `(symbol, timeframe_def.nominal_length 降順, basis)` で固定する。

### 7.2 `OnBarClose` の結び付け（確定。v1.6、2026-09-22）

戦略の `OnBarClose` 起動条件は **`ScheduledBoundary` に結び付ける**。データが遅延している場合、評価時に `latest_available` が `LATEST_BAR_UNAVAILABLE` を返し、`on_missing`（見送り / 待機 / 遡り / 失敗）で扱う。**入力を待つ欠損方針（`WAIT_FOR_INPUT`）の再開契機は、対応する `Publication` の到着**とする。別の系列の足の到着は再開の理由にしない【合意済み】上位設計書 §4.3.14・D05 §6.8 の手順1。

理由: 上位設計書 §4.3.13「欠損しても到着を待たずに予定時点の検査を起動できるようにする」。`Publication` に結び付けると、遅延した系列の評価が黙って後ろへずれ、見送りと待機の区別が失われる。

**案から確定へ改めた**（v1.6。D05 §12.1 の依頼2）。起草時は待機の意味論がどの文書でも決まっておらず、再開契機を案のままにしていた。D05 v2.0 §6.8 が待機の宣言形と再開の手順を確定し、段階3 で実装されるため、案としておく理由が無くなった。本数で数える待機期限を `ScheduledBoundary` で減らす規則（D05 §6.8）も、この結び付けと同じ考え方（データの到着ではなく予定で数える）による。

### 7.3 執行系列の open 公開（確定）

`ExecutionOpen` は執行モデルだけが消費する。始値だけが処理可能になり、同じ足の high/low/close は `ExecutionBarComplete` まで見えない（上位設計書 §4.7.11）。戦略ビューにはその足は `bar_end` まで存在しない。

## 8. adapters（`adapters`）

| モジュール | 内容 |
|---|---|
| `csv_source.py` | `RawBarSource` の実装。列対応宣言に従い、文字列のまま行を返す（Decimal 化は application） |
| `parquet_store.py` | `SnapshotStore` の実装。partition ごとに Parquet を書き、manifest.json を読み書きする。DataFrame は adapters の外へ出さない |

表形式ライブラリ（B-5）は本書では決めない。推奨は polars（第13節）。

## 9. 設定ファイル

| ファイル | 内容 |
|---|---|
| `configs/calendars/fx_ny17_v1.yaml` | カレンダー（tz、週の開閉、休場） |
| `configs/calendars/timeframes_v1.yaml` | 時間足定義 4 件 |
| `configs/datasources/legacy_merged_csv_v1.yaml` | 列対応、時刻規約、宣言する価格基準、ファイル名パターン |
| `configs/symbols/*.yaml` | 銘柄仕様（D02 §5.2）。初版は 10 ペア |

すべて `schema_version` を持ち、`app.config` が Pydantic で検証して domain 型へ変換する（ADR-0018）。

カレンダーの休場の宣言（`closures` の 1 件）には、取引日単位の休場を表す**省略可能なキー `covers_trading_day: true`** を足す（v1.9。第3.4.1節）。省略すれば `false` で、既存のファイルは同じ意味のまま読める。そのため**カレンダーの形式の版（`schema_version: 1`）は上げない**（v1.7 で営業例外 `openings` を省略可能なキーとして足したときと同じ扱い）。新しいキーを書いたファイルを古い実装が読むと、未宣言キーとして拒否される（黙って読み飛ばされない）。

## 10. CLI（`app.cli`）

暫定 ID フロー（第3.7.1節）に対応する3コマンドとする。CLI ライブラリは B-8（段階2）まで argparse。

| コマンド | 入力 | 出力・効果 |
|---|---|---|
| `odyssey-fx data accept --datasource <yaml> --calendar <yaml> --timeframes <yaml> --symbols <dir> --out data/snapshots/` | 原ファイルと設定 | 第4節 1〜8 を実行し、`data/snapshots/_pending/<provisional_id>/` に暫定 manifest・検査結果・partition を書く。`provisional_id` を表示 |
| `odyssey-fx data classify --pending <provisional_id> --decisions <yaml> --timeframes <yaml> --out data/snapshots/` | 分類対象の警告の分類（第4節 9 の形式）と、必要ならカレンダーの新版 | 暫定 snapshot の実体を内容照合してから `closure_decisions` を記入し、カレンダー変更またはセッション外データ異常の分類があれば暫定 snapshot の原系列の足から 5〜7 を再実行（原ファイルは読み直さない）。再実行が新たな分類対象の警告を生んだ場合は失敗して表示し、人間が分類を追記して再度実行する。最終 `snapshot_id` を計算して `data/snapshots/<snapshot_id>/` へ確定。分類対象の警告が未分類、競合する分類、対応する警告のない分類、内容不一致、既に確定済みのディレクトリがある場合は失敗 |
| `odyssey-fx data approve --snapshot <snapshot_id> --by <name> --comment <text>` | 確定済み snapshot | `approval` と `declaration_record` を記入。暫定 snapshot は承認できない |

承認前の snapshot はどのコマンド・ポートからも読み取り対象にならない（第3.7.1節 3）。

分類ファイルの形式（v1.7、確定。利用者が任意の場所に置き、`app.config` が検証する）:

```yaml
schema_version: 2
calendar: configs/calendars/fx_ny17_v2.yaml   # 分類でカレンダーを変えた場合だけ
decisions:
  - kind: MISSING_EXPECTED_BAR
    interval: {start: "2016-12-26T00:00:00Z", end: "2016-12-27T00:00:00Z"}
    series: all                                # または [USDJPY/1h/bid, USDJPY/15m/bid]
    outcome: CLOSURE
    note: クリスマス翌日の休場
  - kind: UNEXPECTED_BAR
    interval: {start: "2020-03-15T20:00:00Z", end: "2020-03-15T22:00:00Z"}
    series: [EURUSD/1h/bid]
    outcome: OUT_OF_SESSION_DATA
    note: 出所の切替に伴う早期配信
```

分類ファイルの形式（`schema_version: 2`）は v1.9 でも変えない。休場の形（第3.4.1節）はカレンダー側の宣言であり、分類ファイルは警告の区間と分類結果だけを書くためである（元日の休場を分類するときも、警告の区間を覆う区間と `CLOSURE` を書き、`calendar` に取引日単位の休場を宣言した新版を指す）。

`calendar` は、分類でカレンダーを変えない再実行（セッション外データ異常の分類だけがある場合）でも省略せず、**暫定 snapshot と同じ識別・版のカレンダーを書く**（v1.8、2026-09-24 の人間の決定。暫定 snapshot はカレンダーの識別と版だけを記録し中身を保存しないため、再実行に使うカレンダーをここで受け取る）。再実行が要るのに書かれていなければ、書くべき識別と版を示して失敗する。


## 11. テスト（確定）

| 種別 | 内容 |
|---|---|
| 単体 | `Bar` の不変条件（短縮セッションで切り詰められた区間が受理され、整列上の区間のままの足が拒否されること。`available_at < bar_end` が拒否されること）、負の遅延の拒否、DST 切替日の起点解決（秋は最初の出現、春は次に存在する瞬間）、`SessionAlignment` の境界計算（DST 切替週の 4h/1d 境界を UTC で固定値と照合し、切替日の日足が 23h/25h、4h 足が 3h/5h になること）、カレンダーの週開閉、`DurationWindow` の端点、partition 所属（`bar_end` 基準）、`SnapshotId` が `created_at` に依存しないこと、分類の差で `snapshot_id` が変わること、分類の突き合わせ（未分類・競合・対応なしの拒否、「全系列」の解決、明示集合と「全系列」で同じ `snapshot_id`）、種別と結果の組合せの拒否、営業例外の `sessions()` への反映、セッション外データ異常の除外、取引日単位の休場（v1.9）の構築と検証（3 形の排他、週の開閉時刻の一致、他の休場との重なりの拒否と接する宣言の許容、営業例外との重なりの拒否）と設定ファイルの読込 |
| 意味論 | 未確定の上位足を参照できない、遅延注入で `Publication` だけが動き OHLC が変わらない、期待足未到着で古い足へ戻らない、範囲外 partition で `HoldoutAccessViolation`、取引日単位の休場（v1.9）で前日 17:00〜当日 17:00 の足が期待されず、当日 17:00 以降の足が休場帯の足にならない（同じ日の終日休場では休場帯の足になることとの対比） |
| プロパティ | 将来の足を追加しても `at` 以前の as-of 結果が変わらない、受入れの決定論性（同じ入力で同じ `snapshot_id`。原ファイルの列挙順・検査の実行順を入れ替えても同じ `snapshot_id`）、集約の OHLC が構成足の集計と一致、取引日単位の休場の区間が夏時間の切替（3 月・11 月）をまたぐ取引日を含むどの日付でも `1d_ny17` の日足の整列上の区間と一致し、その区間の中の足だけが期待されない（v1.9） |
| golden | 人工 1h 系列から生成した 4h/1d の固定出力 |
| 意味論（確定段階） | 暫定 partition の価格を書き換えた後の分類が内容不一致で拒否される、検査報告を差し替えた後の分類が拒否される、カレンダー新版で説明された警告の分類が「対応なし」として拒否されない |
| 実データ | 段階1の完了条件として実データ受入れを1回実行し、manifest と検査結果を人間が確認する |

## 12. 段階1の完了条件との対応

全体計画書 §8.2「未確定の上位足を参照できない。DST・欠損・再読込・遅延注入の検証が通る。実データ受入れで manifest が生成される」に、第11節のテストと第4節の実行を対応させる。

## 13. 承認時の確認事項（2026-09-20 承認: 全項目で推奨を採用。項目1は ADR-0024、項目6は ADR-0025 に記録）

| # | 事項 | 推奨 | 代替 |
|---|---|---|---|
| 1（C-4） | 旧集約の再現版 | 作らない。`ny17_v2` を唯一の規則とし ADR 化 | 旧出力入手後に別版として追加 |
| 2 | partition 所属の基準 | `bar_end`（研究区分に後続区分の情報を含めない） | `bar_start` |
| 3 | 不完全な集約足 | 生成せず欠損として報告 | フラグ付きで保存しビューで除外 |
| 4 | `OnBarClose` の結び付け | `ScheduledBoundary`（予定時点で起動、欠損は `on_missing`） | `Publication`（到着で起動） |
| 5 | 同時刻の系列順 | `(symbol, nominal_length 降順, basis)` | 設定で指定 |
| 6（B-5） | adapters の表形式ライブラリ | polars | pandas |
| 7 | `configs/datasources/` の追加（D01 §10.1 の改訂） | 追加 | `calendars/` に同居 |
| 8 | 受入れ対象 | 20 ファイルすべて | USDJPY のみ |
| 9 | 期間境界の年 | 2024-01-01Z / 2026-01-01Z（ADR-0014 の暦年） | NY 17:00 基準の年境界 |
