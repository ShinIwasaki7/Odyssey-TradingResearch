# D12 付録: 提供元の調査の記録（生の記録）

作成日: 2026-10-15。[D12](D12_data_source_strategy_review.md) v0.1 案の付録。本文の数字はこの付録の節（A-n・B-n・C-n）に結び付けている。

**この記録の性質**

- 調査は 2026-10-15 14:14〜14:28 ごろ（JST。取得したファイルの保存時刻から）。**節 A・B の調査役の記録にある 14:25 より後の時刻（「14:42 頃」など）は調査役の推定の書き誤りで、実際の保存時刻は 14:14〜14:23（ページ）・14:17（HistData の zip）・14:18（FXCM の 2019 年分）である**（照合役の確認、2026-10-15）。下では時刻の後に〔保存時刻〕を添えた。公開ページの閲覧と、登録不要・無料のファイルの取得だけ。購入・申込・登録・問い合わせはしていない。
- 節 A・B は調査役（2 つ）の記録をほぼそのまま写した。ページの読み取りの一部は要約の道具（WebFetch）を経由したので、「引用」は要約が返した文で、原文との一字一句の照合はしていない。検索結果だけに基づく事実は【二次】と印を付けた。
- **2024-01-01 以降の価格は取得・閲覧していない**。2024 年以降については、ページの表示か、ファイルの有無（HTTP の HEAD）だけを確かめた。
- サンプルと取得したページは、リポジトリの外の作業用の場所（scratchpad、以下 `<scratch>`）に置いた。`<scratch>/survey_samples/<提供元>/`（サンプル）、`<scratch>/survey_scripts/`（取得したページと解析の道具）、`<scratch>/d12_scripts/`（設計役の道具）。**リポジトリの `data/` には何も置いていない**。正式な置き場は D12 §7.2 の【提案】。
- 価格の値は原則として書かない（D11 の R-2a の方針に準じる）。例外は A-4 の「差の大きい分」の例 1 行で、研究期間（2019 年）の価格である。

---

## A. 無料の候補

### A-1. Dukascopy

#### 出典・取得日時（JST）
- https://www.dukascopy.com/wiki/en/development/data-export/ （2026-10-15 14:15 頃）
- https://www.dukascopy.com/swiss/english/marketwatch/historical/ （同日）
- https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/ （同日）
- https://datafeed.dukascopy.com/datafeed/USDJPY/2019/02/01/12h_ticks.bi5 （14:14、14:15、約 14:17 の 3 回、各 1 リクエストのみ）

#### 確認した事実
- 旧来の公開 HTTP 配信への GET は 3 回とも失敗: 429 `{"error": "Too Many Requests. For instructions, see: https://www.dukascopy.com/wiki/en/development/data-export/"}`、別 UA で 503 `No server is available to handle this request.`、20 秒待って再び 429。サンプルは取得できず。連打はしていない。
- 公式 wiki は現在、S3 バケット `cfg-public-proper-wallaby`（eu-west-1）を "configured with Requester Pays policy" と説明。前提に「Valid AWS credentials」。料金: 「$0.0004 per 1,000 requests」「$0.02 per GB data transfer」、全アーカイブ「~400 GB, ~20,000,000 files」。AWS アカウントと支払い設定が要る＝登録・課金に当たるため実行していない。
- tick レコード: 20 バイト固定、big-endian、「Milliseconds since start of day (UTC)」、ask・bid（整数。スケールは FX 多数 100,000、JPY ペア 1,000）、ask/bid 出来高（float32）。キー形式 `SYMBOL/YEAR/MONTH/DAY_ticks.bi5`、wiki の例は `EURUSD/2024/00/15_ticks.bi5`（月は 0 始まり）。注意書き: "Older files may use ms since start of hour instead of ms since start of day"。欠損日は「no ticks that day」扱い。
- Historical Data Export ページ: "It is free through Dukascopy's Historical Data Export tool"、CSV、tick から月足まで。JForex 経由の取得も可能。
- 提供開始年・8 ペアの有無・欠落補正方針: 公式ページから確認できず（未確認）。価格は bid/ask の気配（約定ではない）。

#### 規約の引用（サイト利用規約。データ専用規約ではない可能性あり）
- "Except written agreement between you and DUKASCOPY, you agree to use the WEBSITE solely for your own non-commercial use and benefit, and not for resale or other transfer or disposition to, or use by or for the benefit of, any other person or entity."
- "Such download is licensed to you by DUKASCOPY ONLY for your own personal, non-commercial use in accordance with the TCU and does not transfer any other rights to you."
- バケット経由の bi5 にこれが及ぶかは未確認（データ専用の規約文は見つからず）。

#### サンプル
なし（取得できず）。品質指標、および Dukascopy 対他社の同一分比較は未実施。

#### 未確認
8 ペアの有無、開始年、旧 HTTP 配信の制限の解除条件、夏時間の扱い、補正方針、規約のデータへの適用範囲。

### A-2. HistData.com

#### 出典・取得日時（JST）
- https://www.histdata.com/f-a-q/ （2026-10-15 14:25 頃〔保存時刻 14:14〜14:23 の間〕）
- https://www.histdata.com/f-a-q/data-files-detailed-specification/
- https://www.histdata.com/download-free-forex-data/ , https://www.histdata.com/download-by-ftp/ , https://www.histdata.com/about-us/
- ペア別の年一覧: `https://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/usdjpy/2019` ほか（同日 14:28〜14:40〔保存時刻 14:14〜14:23 の間〕）

#### 確認した事実
- 通貨ペア: FAQ の一覧に USD/JPY, USD/CHF, AUD/JPY, EUR/USD, EUR/JPY, GBP/JPY, GBP/USD, AUD/USD の 8 つ全てあり。
- 提供期間（ペア別の年一覧の表示）: 1分・tick とも USDJPY / USDCHF / EURUSD / GBPUSD / AUDUSD は 2000〜2026、AUDJPY / EURJPY / GBPJPY は 2002〜2026。表示上の年であり、古い年の中身の密度は未検証。2024 年以降は「期間の表示がある」ことだけ確認（価格は未取得）。
- 粒度: "We can only deliver you time ordered Tick and M1 (1 minute) data."。2019 年は年単位 zip、当年は月単位。
- 価格種別: "the bar prices: Open, High, Low, Close in the data files are based on the tick Bid price."、tick は `DateTime,Bid,Ask,Volume`（"The Ask price is only included in the Tick data of the Generic ASCII format."）。気配。出来高は 0（"we decided to remove the volume information"）。
- 時刻基準: "The timezone of all data is: Eastern Standard Time (EST) time-zone WITHOUT Day Light Savings adjustments."（＝UTC−5 固定）。バー時刻が始点か終点かは公式文に記述なし。サンプルの最終バーが NY 16:59 で週が終わるため、始点表記と推定（推定）。
- 欠落・補正: ファイルごとの状態レポート（最大ギャップ、1 分超のギャップ全件、平均 tick 間隔）が付く。"Keep in mind that some of the gaps can be due to normal Forex Trading pause periods." 補間・補正の方針は明記なし。
- 費用: 手動ダウンロードは無料・登録なし。FTP/SFTP は $27（15 日）、Google Drive 自動更新は月 $7（どちらも未申込）。
- 入手の仕組み（登録不要）: 年一覧ページに使い捨てトークンが埋め込まれ、`/get.php` に POST して zip を得る。

#### 規約の引用
- 明文の利用規約・再配布条件のページは見つからなかった（/terms-of-use/ は 404。サイトにあるのは Privacy Policy と Cookies のみ）。
- FAQ: "Since it's free data, you'll not get from us any kind of warranty or certification. Use the data at your own will and risk."
- About: "These data is then shared for free to help you out testing your own/bought strategies and expert advisors."
- FTP ページ: "(we're not selling the data!!!)"
- 再配布・商用・研究利用の可否: 未確認（明文なし）。

#### サンプル
- 取得: 2026-10-15 約 14:42 JST〔保存時刻 14:17〕。`survey_samples/histdata/HISTDATA_COM_ASCII_USDJPY_M1_2019.zip`
  - SHA-256 `c1e4fed59edb1a31b99e4e8b3d1e71c174ca48b3947bf11fcb31c85b98d78dcd`（3,329,299 バイト）
- 展開（別ディレクトリ `histdata/extracted/`）:
  - `DAT_ASCII_USDJPY_M1_2019.csv` SHA-256 `733abb305f9a0e8f0f50c5cfc0faf2df35c10c5751866f4242e0597eaccb6287`
  - `DAT_ASCII_USDJPY_M1_2019.txt`（状態レポート）SHA-256 `f4ebf6b2408b7b9f4aa7bd7cf731a288fc7d568649d23991d3014c4968eb4fd7`
- 年単位ファイルのため 2019 年全体を含む。解析は 2019-03-01〜03-31 UTC（EST 固定に +5 時間して UTC 化）のみ使用。tick は未取得。
- 形式: `20190301 000000;open;high;low;close;0`（bid OHLC、秒は常に 00）。1 分足に ask なし。

#### 品質指標（2019-03 UTC、USDJPY 1 分足、bid）
- バー数 30,143。期待本数 30,300（定義: 日曜 17:00 NY〜金曜 17:00 NY の各分。米国夏時間は 2019-03-10 から適用）。欠落 157 分（0.52%）。期待範囲外のバー 0。
- 最長欠落 60 分（03-10 21:00Z と 03-31 21:00Z の 2 回、計 120 分）。それ以外の欠落は合計 37 分で、5 分以上の連続欠落はなし。
- 週明け最初のバー（UTC）: 03-03 22:00（NY 冬時間 17:00）、03-10 22:00（夏時間の週だが 1 時間遅れ）、03-17 21:00、03-24 21:00、03-31 22:00（1 時間遅れ）。週末最後のバー: 03-08 21:59Z、03-15/22/29 は 20:59Z。NY 17:00 を境に週が切れ、DST に連動して UTC の時刻が 1 時間動く（EST 固定表記のまま）。
- OHLC 不整合バー 0。

### A-3. FXCM の公開履歴（candledata.fxcorporate.com）

#### 出典・取得日時（JST）
- https://github.com/fxcm/MarketData （README 原文、2026-10-15 14:45 頃〔保存時刻 14:14〜14:23 の間〕）
- https://candledata.fxcorporate.com/m1/USDJPY/2019/{9..14}.csv.gz （同日 14:46 頃〔保存時刻 14:18〕）
- https://tickdata.fxcorporate.com/ （同日。302 で Cloudflare Access のログインへ。ログインしていない）
- EULA https://www.fxcm.com/uk/forms/eula/ は 403（Cloudflare チェック）で本文を読めず。

#### 確認した事実
- URL 形式: `https://candledata.fxcorporate.com/{periodicity}/{instrument}/{year}/{int of week of year}.csv.gz`、periodicity は m1 / H1 / D1。D1 は `D1/{pair}/{year}.csv.gz`。
- README の対象年: "2017, 2018, 2019, 2020"。ただし実ファイルはそれ以外にも存在（HEAD で確認、本文は読まず）: USDJPY m1 週 9 は 2012〜2026 で 200、2009〜2011 は 404。2012 の他の週は未確認。2024 年以降は「週 9 のファイルが存在する」ことだけ確認。
- README の通貨ペア 21 本に AUDUSD がない。ただし `m1/AUDUSD/2019/9.csv.gz` は 200。8 ペア（USDJPY, USDCHF, AUDJPY, EURUSD, EURJPY, GBPJPY, GBPUSD, AUDUSD）の 2019 週 9 は全て 200。
- 時刻基準: README "Timestamps are in UTC"。バー時刻の始点/終点は記述なし。最終バーが金曜 20:59Z 等で終わるため始点表記と推定（推定）。
- 価格種別: 列は `DateTime,BidOpen,BidHigh,BidLow,BidClose,AskOpen,AskHigh,AskLow,AskClose`。README: "Data points are indicative and based on the lowest spreads available exclusively on Active Trader accounts"（約定でなく気配）。
- tick: README "Please send email request to premiumdata@fxcm.com."（問い合わせが必要＝未実施）。tickdata.fxcorporate.com はログイン必須。
- 欠落・補正方針: 記述なし（未確認）。
- 費用: 無料。"FXCM provide several sample data for free."（サンプルという位置づけ）

#### 規約の引用（README）
- "This is for personal use and abides by our EULA"
- "Please use our FXCM apps if you want the full data."
- 再配布・商用の可否は EULA 本文が読めず未確認。

#### サンプル（取得 2026-10-15 約 14:46 JST〔保存時刻 14:18〕、`survey_samples/fxcm/`）
| ファイル | SHA-256 |
|---|---|
| USDJPY_m1_2019_w9.csv.gz | 30a79a84b92b2e93609c781b3f5f6453da906f2b7771c7d39a3d12dcc8f68c98 |
| USDJPY_m1_2019_w10.csv.gz | c224db50b23f89b642d963779c0af791a96dd2feee1d1eddd2880f3145276920 |
| USDJPY_m1_2019_w11.csv.gz | e6bfb8f0ed55d9212dde0949177188740d82b73c016291e08749badd352c5c28 |
| USDJPY_m1_2019_w12.csv.gz | 61e22d23b7294f482a0fe5c619678ca24a981fa7fc7fe7c7957bee52fc18a3b5 |
| USDJPY_m1_2019_w13.csv.gz | 94c21c792e0527bf50a02d0532537ccd5bbd3d4bd0f38fbc742acb7730b2b1d7 |
| USDJPY_m1_2019_w14.csv.gz | 08a1faa8e5c73eef679c9401b0fb32f46e3f1b9a0c64888ad7dacc7f5a9d746e |

週 9 の先頭行が 2019-02-24 22:00 UTC であることを確認。週 9〜14 で 2019-02-24〜04-05 ごろを覆う。解析は 03-01〜03-31 UTC のみ。

#### 品質指標（2019-03 UTC、USDJPY 1 分足）
- バー数 30,198。期待 30,300（HistData と同定義）。欠落 102 分（0.34%）。期待範囲外のバー 0。
- 最長欠落 11 分（03-17 21:45Z）。5 分以上の欠落: 03-07 22:00Z(5)、03-07 22:15Z(5)、03-17 21:45Z(11)、03-28 21:24Z(5)。
- 週明け最初のバー（UTC）: 03-03 22:00、03-10 21:00、03-17 21:00、03-24 21:02、03-31 21:01。週末最後: 03-08 21:59Z、03-15/22/29 は 20:59Z。UTC 表記のまま DST に連動して 1 時間動く（NY 17:00 基準）。
- bid/ask あり。ask−bid の終値差（pip=0.01）: 中央値 0.20、最大 6.00、最小 −1.60（ask<bid の逆転バーが存在）。bid 側 OHLC 不整合 0。

#### 追加の確認（設計役、2026-10-15 14:26〜14:28 JST）

- **2023 年の週 10〜13 の USDJPY 1 分足を追加で取得**（`https://candledata.fxcorporate.com/m1/USDJPY/2023/{10..13}.csv.gz`、`<scratch>/survey_samples/fxcm_2023/`）:

| ファイル | SHA-256 |
|---|---|
| USDJPY_m1_2023_w10.csv.gz | be96bcef271150cef01518391858057791fdfff58c6a5dc526d845f22577f4fb |
| USDJPY_m1_2023_w11.csv.gz | 02f380322c75594bb8c7f848693522186dfa1636ce7c251d91f7274997604169 |
| USDJPY_m1_2023_w12.csv.gz | de14e1e7693c5b76b9533989cfc9bc11b05a5853158c976c454c15d2ba753448 |
| USDJPY_m1_2023_w13.csv.gz | d726614fd1c0e4d0a1a47c1a147c3e32ebb9bdac7c0c17314bf4d7cee37cacd0 |

- 解析（`<scratch>/d12_scripts/fxcm_weeks.py`、SHA-256 は C-3。2024 年以降の行があれば止まる）の結果（2019 年のファイルは上の調査役の取得分）:

| 週 | 行数 | 最初と最後（UTC） | 15 分以上の空白 | ask < bid の足（始値か終値） | bid の OHLC の大小関係の違反 |
|---|---|---|---|---|---|
| 2023 w10 | 7,158 | 03-05 22:00 〜 03-10 21:58 | 0 | 0 | 0 |
| 2023 w11 | 7,155 | 03-12 21:00 〜 03-17 20:58 | 0 | 0 | 0 |
| 2023 w12 | 7,151 | 03-19 21:01 〜 03-24 20:58 | 1（03-19 21:16 から 18 分） | 0 | 0 |
| 2023 w13 | 7,137 | 03-26 21:10 〜 03-31 20:58 | 0 | 0 | 0 |
| 2019 w9 | 7,188 | 02-24 22:00 〜 03-01 21:59 | 0 | 85 | 0 |
| 2019 w10 | 7,177 | 03-03 22:00 〜 03-08 21:59 | 0 | 68 | 0 |
| 2019 w11 | 7,188 | 03-10 21:00 〜 03-15 20:59 | 0 | 58 | 0 |
| 2019 w12 | 7,162 | 03-17 21:00 〜 03-22 20:59 | 0 | 97 | 0 |
| 2019 w13 | 7,177 | 03-24 21:02 〜 03-29 20:59 | 0 | 83 | 0 |
| 2019 w14 | 7,185 | 03-31 21:01 〜 04-05 20:58 | 0 | 80 | 0 |

- **8 ペア × 2017〜2023 年のファイルの有無**（HTTP の HEAD だけ。中身は読んでいない。2026-10-15 14:27〜14:28 JST）: 各ペア・各年の週 2・27・51 の `m1` ファイル（計 168 個）がすべて HTTP 200。対象ペア: USDJPY・USDCHF・AUDJPY・EURUSD・EURJPY・GBPJPY・GBPUSD・AUDUSD。

### A-4. HistData 対 FXCM の同じ分の比較（2019 年 3 月の USDJPY）

- 共通分 30,058 分。HistData の bid 終値と FXCM の BidClose の絶対差（pip=0.01）: 中央値 0.10、95% 点 0.20、99% 点 0.60、最大 13.40。差ゼロの割合 39.0%。
- 最大差は 2019-03-31 22:16〜22:23Z（日曜の週明け直後）。例: 22:21Z で HistData 111.046 対 FXCM 110.912。原因は未確認。
- 時刻ずらし検査: FXCM を ±1 分ずらすと中央値差が 0.50 pip に悪化 → HistData(+5h) と FXCM(UTC) の分の整列は 0 ずれで一致。
- 注意: 元ソースが別（HistData の元ソースは公式に記述なし）なので、差は提供元の集計差を含む。

- 解析の道具と出力: `<scratch>/survey_scripts/analyze_m1.py`（SHA-256 `5f83284165653e7f1a66c5025117d150de55af969358635eebb6f772707f7b63`）、`<scratch>/survey_scripts/analysis_out.txt`（SHA-256 `58759af3bacbc0437d4e235097e4fcb4acd5ef6a433f1d1c866ddb255f2663a7`）。

### A-5. TrueFX

- https://www.truefx.com/truefx-historical-downloads/ （2026-10-15 14:48 頃〔保存時刻 14:14〜14:23 の間〕）
  - "Please log in or register for free access to the historical data downloads."
  - 「historical tick-by-tick market data, at zero cost to you」。"Top-of-book, tick-by-tick market data"。
- 通貨ペア・開始年・形式・時刻基準・bid/ask・規約: 公式ページに記載なし（未確認）。第三者資料（2012 年の R パッケージのコミット）は「15 ペア、2009-05 から、bid/ask tick」とするが古く未検証。
- 登録・サンプル取得はしていない。

### A-6. 無料の候補の調査者の所見

- 機械処理できた無料・登録不要の 1 分足は HistData と FXCM のみ。Dukascopy は公式が AWS 課金型に切り替えており、旧 HTTP は 429/503。
- 「研究目的・再配布」を明確に許す文は 3 社とも見つからなかった（Dukascopy と FXCM は個人・非商用寄り、HistData は明文なし）。判断は設計文書側で。
- 週明けは両社とも NY 17:00 基準。HistData は EST 固定表記なので UTC 化は +5h 固定でよいが、夏時間期間は週の境が UTC で 1 時間早くなる。HistData は 03-10 と 03-31 の週明け先頭 60 分が欠落している。

---

## B. 口座が前提の候補と有料の候補

調査の記録の冒頭（原文）:

取得日時: 2026-10-15 14:14〜約15:00 JST〔終わりの時刻は推定の書き誤り。調査役の終了は 14:18 ごろ（所要約 3.5 分）〕。方法: WebFetch/WebSearch のみ。購入・登録・問い合わせなし。
注意: WebFetch は小型モデルによる要約経由のため、「原文引用」は要約が返した引用であり、ページ原文との照合は未実施。要約・検索結果由来は【二次】と記す。
サンプル: ダウンロードしていない（理由は各項。保存先 survey_samples/ は空）。
対象8ペア: USDJPY USDCHF AUDJPY EURUSD EURJPY GBPJPY GBPUSD AUDUSD。ペア別の提供有無は、特記なき限り「未確認」。

### B-1. OANDA（v20 API）

- 出典: https://developer.oanda.com/rest-live-v20/introduction/ / https://developer.oanda.com/rest-live-v20/instrument-ep（直接取得は 404、検索結果の要約のみ【二次】）/ https://www.oanda.com/sg-en/platforms/rest-api/ / https://www.oanda.jp/fxproduct/api / https://www.oanda.jp/info/755 （全て 2026-10-15 JST）
- 確認した事実:
  - 価格種別: price に M/B/A の組合せ指定可【二次: async-v20 docs】。bid/ask が独立に返る（SG 版ページ: "full, independent Bid and Ask price objects"）。気配（約定ではない）、単一ブローカー（OANDA 自社）。
  - 粒度: S5〜月足（SG ページ）。1リクエスト最大 count=5000、既定500【二次】。
  - 期間: 履歴は 2005 年から（SG ページ）。日本口座での範囲は未確認。
  - 時刻: 返却は UTC。dailyAlignment 既定17、alignmentTimezone 既定 America/New_York、weeklyAlignment 既定 Friday【二次】。バー時刻=開始時刻かは公式ページ未取得のため未確認。
  - 口座: v20 は「OANDA Global Markets と OANDA TMS BROKERS S.A. を除く全部門」で利用可（introduction）。OANDA証券(日本)は除外一覧に無いが明記も無い。
  - OANDA証券(日本)の条件: 口座開設＋残高25万円以上＋Goldステータス＋NYサーバー・プロコース。料金無料。残高不足で予告なく停止あり。技術サポートなし。ローソク足の履歴範囲の記載は日本ページに無い（注文・取引履歴は直近2週間・最大5,000件のみ記載）。
- 規約: 日本 info/755: 「弊社サービスの無断での商用利用は固くお断りいたします。」商用は別契約。API License Agreement（OANDA Corporation 版）は検索要約で "Internal Use"(研究・分析可、第三者への再配布不可)とされる【二次、原文未確認。PDFは機械抽出できず】。日本向け API 規約の本文は未確認。
- 価格: API 利用料は無料（日本ページ。ただし口座・残高条件あり）。
- サンプル: なし（API。口座が要るため取得せず）。
- 未確認: 日本口座での履歴の実際の開始年・粒度制限、日本向け規約の再配布条項、バー時刻の定義、欠損・補正方針、8ペアの有無（FX主要ペアなので有ると推測されるが未確認）。

#### 無料の候補の調査で見た OANDA の公開ページ

- https://developer.oanda.com/rest-live-v20/introduction/ , https://developer.oanda.com/rest-live-v20/instrument-df/ （2026-10-15 14:49 頃〔保存時刻 14:14〜14:23 の間〕）。instrument-ep（エンドポイント詳細）は 404 で未読。
- 条件: "To use this API you must have a v20 trading account…"。"Get an OANDA v20 fxTrade account - Try a free demo account or open a live account"。"Generate your personal access token immediately after you agree to the API license."（口座とトークンが必須。取得していない）
- 粒度: S5, S10, S15, S30, M1（minute alignment）, M2〜M30, H1 など。ローソク足 `time` は "The start time of the candlestick"。bid / ask / mid は要求した種別のみ返る。
- 提供期間、通貨ペア、DST 基準（dailyAlignment 等）、取得上限、データ利用条件（API ライセンス本文）: 未確認。

### B-2. Massive（旧 Polygon.io）

- 出典: https://massive.com/pricing?product=currencies / https://massive.com/docs/rest/forex/aggregates/custom-bars / https://massive.com/legal/terms / https://massive.com/legal/individuals-terms-of-service （2026-10-15 JST）
- 事実: 
  - Currencies Basic $0/月: 履歴2年、分足、日次終了後データ、5回/分。Currencies Starter $49/月(月払い): 履歴10年以上、分足＋秒足、リアルタイム、Flat Files・Quotes 含む。両方「Individual use」。Business 料金は別ページ（内容は未取得）。
  - ドキュメント: 履歴は Starter/Business で 2009-09-25 まで、Basic 2年。バーは "generated from quoted bid/ask prices rather than executed trades"。bid/ask/mid のどれかは記載なし。バー時刻 "start of the aggregate window"、UTC。気配が無い区間はバー無し。limit 最大50000。
  - 気配元（単一か集約か）: 未確認。
- 規約（Individuals）: "solely for your own personal, non-commercial, and non-business purposes"(§2)、"Sell or otherwise transfer the access..."禁止(§6.1(h))、"making unauthorized copies of any content"(§6.1(d)(iii))。Market Data は別の Market Data Terms of Service に従う（未取得）。研究利用の扱いは個人規約に明記なし。事業目的なら Business 契約が必要になる可能性。
- 価格: 上記（公開）。Business は未確認。
- サンプル: なし（無料 Basic は登録要るため取得せず）。
- 未確認: 気配元、bid/mid の別、8ペア個別の提供開始日、DST・欠損方針、Business 価格と再配布条件、Market Data TOS。

### B-3. Tick Data LLC

- 出典: https://tickdata.com/product/historical-forex-data / https://www.tickdata.com/ / https://www.tickdata.com/terms-of-use/ ; 補助: https://www.waterstechnology.com/node/2289567（検索結果のみ）（2026-10-15 JST）
- 事実: スポットFX 2,000ペア超、2008-05-01 から。ティック(bid/ask気配)と1分足(**bid側**から作成)。気配元は95以上のコントリビュータ(銀行等)・80以上の都市＝集約。ミリ秒精度。ASCII 配信。タイムゾーンはページに記載なし(未確認)。収集・検証・クリーニングを行うと記載、手法は非公開。
- 規約: 公開の Terms of Use には研究・再配布・派生データの条項なし（"does not grant any licenses to any copyrights" のみ）。商品別契約は未確認。
- 価格: 非公開。「見積が必要」。見積に必要な条件（推定、要確認）: ペア数、期間、粒度（ティック/1分）、ユーザー数・用途（研究/社内/再配布）、配信方式（ファイル/TickWrite/TickAPI）、更新購読の有無。
- サンプル: フォーム申請(連絡先入力＝登録相当)のため取得せず。
- 未確認: 時刻基準、DST、8ペアの個別有無、ライセンス本文。

### B-4. Kibot

- 出典: http://www.kibot.com/（トップ）, http://www.kibot.com/terms.aspx（2026-10-15 JST）。forex 専用ページは 404 で未取得。
- 事実: Forex bid/ask データ 1,100ペア以上、"Volume" はティック数。CSV Date,Time,O,H,L,C,V。時刻は既定 US Eastern（変更可）。サイト全体では1分足 1998 年から、ティック(NBBO)2009年から（株式向けの可能性、FX固有の開始年は未確認）。
- 規約: "The Data is for the Licensee's own internal use only." "will not resell or redistribute the Data or the Software." 
- 価格: カスタム注文 $40〜（FX の価格は未確認）。単発購入は45日アクセス。
- サンプル: 無料サンプルありと記載、FX は未確認。取得せず。
- 未確認: FX 開始年、気配元、DST の扱い、8ペア有無、バー時刻の定義。

### B-5. FirstRate Data

- 出典: https://firstratedata.com/ , https://firstratedata.com/it/fx , https://firstratedata.com/i/fx/USDJPY （2026-10-15 JST）
- 事実: 74 FX ティッカー（USDJPY, AUDJPY, EURUSD, GBPUSD, AUDUSD, USDCHF を含むと記載。EURJPY/GBPJPY は未確認）。1分/5分/30分/1時間(日足)。OHLCV のみ、bid/ask/mid の別なし(未確認)。USDJPY は 2010-01-01 開始。トップには「最も活発な70クロスを 2010 年まで」とあり。時刻基準・気配元・欠損方針は未確認(データ品質ページ /about/dataquality は未取得)。
- 規約: 公開ページに利用条項なし(未確認)。
- 価格: 初回価格はページに出ず。更新購読 $99.95/年（初月無料）のみ確認。それ以外は「見積/カート確認が必要」。条件: ペア数、粒度、期間、更新購読の有無。
- サンプル: あり（例: https://frd001.s3.us-east-2.amazonaws.com/frd_sample_fx_USDJPY.zip）。データ終端が 2026-10 なので 2024 年以降を含む恐れがあり、取得せず。期間が2023年以前と判明するなら取得可。

### B-6. MT5 のブローカー（Pepperstone・IC Markets など）

- 出典: 検索結果のみ【二次】。Pepperstone の historical-tick-data ページ（https://pepperstone.com/en/client-resources/historical-tick-data）は 404 で確認不可。IC Markets は公式ページ未確認。 MQL5 フォーラム https://www.mql5.com/en/forum/161595 等（2026-10-15 JST）
- 事実: MT5 は口座のブローカーサーバーから気配を取得。ティック深度はブローカー・口座・銘柄で異なる。単一ブローカーの気配(bid/ask)。公式の期間・規約・欠損方針は確認できず。サーバー時刻(DST 付き)基準になるのが一般的だが、各社の仕様は未確認。
- 規約・価格・サンプル: 未確認。
- 未確認: すべて(期間、ペア、再配布可否)。口座開設が必要で、今回は不可。

### B-7. LSEG Tick History

- 出典: https://www.lseg.com/en/data-analytics/financial-data/pricing-and-market-data/instrument-pricing-data（検索結果経由【二次】）、https://www.lseg.com/en/data-catalogue/forex-and-money-market （2026-10-15 JST）
- 事実: Tick History は全資産クラスで 1996 年以降のティックと記載(FX 固有の開始年は未確認)。FX カテゴリの履歴は "From 1975"（ティック水準かは不明）。コントリビュータ気配。LSEG 開発者フォーラムで試用者が FX RIC(GBP=, AUD=)で 2019〜2021 のデータが返らない報告【二次】。
- 規約: 未確認。
- 価格: 非公開。「見積が必要」。条件: 対象RIC数(ペア)、期間、粒度、ユーザー数、用途(社内研究/再配布)、配信方式(Web/API/クラウド)。
- サンプル: なし/未確認。

### B-8. その他（TraderMade・EODHD・Twelve Data・Databento・Barchart）

- TraderMade（https://marketdata.tradermade.com/blog/how-to-get-forex-historical-data ）: ティック・分足 50ペア以上を 2016-09 から、分足OHLCは 2013年中頃から、bid/ask ミリ秒、集約・クリーニング済と記載。無料枠は分足2日・1時間足2ヶ月・日足15年。ダウンロード料金は「カートで確認」。ペア年あたり £240 の掲載が datarade にある【二次】。規約未確認。
- EODHD: FX 日足 20年以上。FX 分足履歴の深さは未確認。有料 $29.99/月〜（intraday を含むプラン）【二次】。
- Twelve Data: 1分足は 2020-02-10 以降のみ(公式ヘルプ検索結果)。FX は数年分【二次】。価格最新版は未確認。
- Databento: FX スポットは今回の検索で未確認(CME の FX 先物は別物)。
- Barchart: 未調査(時間切れ)。
- 参考（スポットFX以外の機関向け）: OneTick GLOBAL_FX 2008-05-07〜、Parameta 2001〜、Cboe FX Trades 2020〜（約定。検索結果のみ【二次】）。
- Dukascopy: 無料エクスポート(ページに登録要否の記載なし)。有料ではないため詳細調査せず。

### B-9. 有料の候補の横断メモ

- 2010年より前（特に2008年金融危機）を要する場合: Tick Data(2008-05〜)、OANDA(2005〜)、Massive Starter(2009-09〜)が候補。FirstRate は 2010〜。
- bid/ask を別々に得られると確認できたのは OANDA と Tick Data(ティック)。Tick Data の1分足は bid 側のみ。FirstRate/Kibot の Kibot は bid/ask と記載あるが OHLC の価格側は未確認。
- 再配布禁止が確認できた: Kibot, Massive(個人規約), OANDA(商用不可、二次)。

---

## C. 手元の HistData の 1 分足（時刻の列だけ）と道具

### C-1. HistData の 1 分足の時刻の列の集計（研究期間 2017〜2023、8 ペア）

> **v0.2 の注記**: この節の集計は「隣の行との差が 24 時間以上の空白を数えない」作り方で、人間の指示（2026-10-15「24時間以上の空白も、根拠なく集計対象外にしない」）により本文での使用を撤回した。記録として残す。取引カレンダーで数え直した表は D-4。

- 対象: `data/raw/histdata/<ペア>/HISTDATA_COM_ASCII_<ペア>_M1<年>/DAT_ASCII_<ペア>_M1_<年>.csv`（手元の既存ファイル。変更していない）。年 2017〜2023、ペア 8（USDJPY・USDCHF・AUDJPY・EURUSD・EURJPY・GBPJPY・GBPUSD・AUDUSD）。
- 読んだもの: **各行の先頭の欄（時刻のラベル `YYYYMMDD HHMMSS`）だけ**。価格の列は分割もしていない。2024 年以降のファイルは開いていない。
- 道具: `<scratch>/d12_scripts/histdata_gaps.py`（SHA-256 は C-3）。出力: `<scratch>/d12_histdata_gaps.csv`（SHA-256 `b5ef85b15309643b5ebb8d8146750ff8c59225332b0e81d372f508fd0b4159f6`。各ファイルの SHA-256・行数・空白の件数を年ごとに 1 行）。実行: 2026-10-15 14:19 ごろ（JST）、`/usr/bin/python3 -I`。
- 定義: 隣り合う行の時刻の差 d（分）について、2 ≤ d < 1,440 を「週の中の空白」とし、d ≥ 1,440 は週末・長い休場として数えない。**休場か欠落かは判定していない**。HistData の 1 分足は tick の無い分の足を作らないので、短い空白には取引の少ない分が含まれる。

| ペア | 年 | 行数 | 15 分以上の空白 | 60 分以上 | 最長の空白（分） |
|---|---|---|---|---|---|
| USDJPY | 2017 | 371,298 | 0 | 0 | 8 |
| USDJPY | 2018 | 371,800 | 1 | 1 | 841 |
| USDJPY | 2019 | 372,585 | 4 | 4 | 841 |
| USDJPY | 2020 | 372,200 | 2 | 2 | 1,141 |
| USDJPY | 2021 | 369,393 | 2 | 2 | 1,201 |
| USDJPY | 2022 | 372,596 | 5 | 3 | 61 |
| USDJPY | 2023 | 322,956 | 685 | 684 | 846 |
| USDCHF | 2017 | 369,892 | 0 | 0 | 11 |
| USDCHF | 2018 | 370,264 | 4 | 1 | 849 |
| USDCHF | 2019 | 371,141 | 4 | 4 | 844 |
| USDCHF | 2020 | 370,406 | 2 | 2 | 1,142 |
| USDCHF | 2021 | 365,550 | 11 | 2 | 1,201 |
| USDCHF | 2022 | 369,408 | 21 | 5 | 61 |
| USDCHF | 2023 | 320,793 | 676 | 669 | 1,267 |
| AUDJPY | 2017 | 372,157 | 0 | 0 | 7 |
| AUDJPY | 2018 | 372,850 | 1 | 1 | 841 |
| AUDJPY | 2019 | 372,945 | 3 | 3 | 841 |
| AUDJPY | 2020 | 372,815 | 2 | 2 | 1,141 |
| AUDJPY | 2021 | 372,172 | 2 | 2 | 1,201 |
| AUDJPY | 2022 | 372,509 | 7 | 7 | 61 |
| AUDJPY | 2023 | 322,847 | 700 | 695 | 873 |
| EURUSD | 2017 | 371,635 | 0 | 0 | 6 |
| EURUSD | 2018 | 372,607 | 1 | 1 | 843 |
| EURUSD | 2019 | 372,530 | 4 | 4 | 841 |
| EURUSD | 2020 | 372,335 | 3 | 2 | 1,141 |
| EURUSD | 2021 | 369,502 | 3 | 2 | 1,201 |
| EURUSD | 2022 | 372,865 | 1 | 1 | 61 |
| EURUSD | 2023 | 322,638 | 683 | 679 | 846 |
| EURJPY | 2017 | 372,258 | 0 | 0 | 7 |
| EURJPY | 2018 | 373,304 | 1 | 1 | 842 |
| EURJPY | 2019 | 372,610 | 5 | 5 | 841 |
| EURJPY | 2020 | 372,707 | 2 | 2 | 1,141 |
| EURJPY | 2021 | 371,305 | 3 | 2 | 1,201 |
| EURJPY | 2022 | 372,194 | 7 | 5 | 61 |
| EURJPY | 2023 | 323,100 | 672 | 668 | 913 |
| GBPJPY | 2017 | 370,949 | 0 | 0 | 14 |
| GBPJPY | 2018 | 372,495 | 2 | 1 | 849 |
| GBPJPY | 2019 | 372,102 | 4 | 4 | 847 |
| GBPJPY | 2020 | 372,709 | 3 | 2 | 1,141 |
| GBPJPY | 2021 | 371,569 | 3 | 2 | 1,201 |
| GBPJPY | 2022 | 372,289 | 8 | 3 | 61 |
| GBPJPY | 2023 | 322,131 | 676 | 672 | 858 |
| GBPUSD | 2017 | 371,204 | 1 | 0 | 23 |
| GBPUSD | 2018 | 372,261 | 2 | 1 | 846 |
| GBPUSD | 2019 | 372,396 | 4 | 4 | 847 |
| GBPUSD | 2020 | 373,057 | 5 | 2 | 1,141 |
| GBPUSD | 2021 | 370,398 | 3 | 2 | 1,201 |
| GBPUSD | 2022 | 372,494 | 5 | 3 | 61 |
| GBPUSD | 2023 | 322,467 | 676 | 672 | 847 |
| AUDUSD | 2017 | 368,436 | 0 | 0 | 12 |
| AUDUSD | 2018 | 369,457 | 1 | 1 | 844 |
| AUDUSD | 2019 | 371,998 | 6 | 6 | 845 |
| AUDUSD | 2020 | 371,875 | 4 | 2 | 1,141 |
| AUDUSD | 2021 | 369,744 | 3 | 2 | 1,201 |
| AUDUSD | 2022 | 371,555 | 6 | 4 | 61 |
| AUDUSD | 2023 | 322,149 | 665 | 662 | 852 |

**USDJPY の空白の内訳**（`<scratch>/d12_scripts/gap_months.py`・`gap_list.py`。時刻はファイルのラベルのまま）:

- 2023 年の 15 分以上の空白の月別: 2023-02: 45、03: 139、04: 109、05: 141、06: 132、07: 117、09: 1、12: 1。長さの上位: 61 分が 598 件、121 分が 79 件。ラベルの日付 2023-03-05〜2023-04-01 の範囲では 121 件。
- 2018〜2022 年の 15 分以上の空白（全 14 件）: 2018-12-25 02:59→17:00（841 分）、2019-03-31 18:59→20:00、2019-12-24 16:59→18:00、2019-12-25 02:59→17:00（841 分）、2019-12-31 09:59→11:00、2020-03-29 18:59→20:00、**2020-11-29 23:59→2020-11-30 19:00（1,141 分）**、2021-03-28 18:59→20:00、**2021-05-30 23:59→2021-05-31 20:00（1,201 分）**、2022-03-27 18:59→20:00、2022-08-31 22:59→2022-09-01 00:00、2022-09-01 13:59→15:00、2022-10-09 17:13→17:28、2022-10-09 17:35→17:50。
- 太字の 2 件は、既存の補充の試行で Dukascopy の tick から埋めた区間（`docs/data/refill_trial_2026-10-01.md` §3 の計画 A: 2020-11-30T05:00Z〜2020-12-01T00:00Z、計画 B: 2021-05-31T04:00Z〜2021-06-01T00:00Z）と対応する（ラベルを公表仕様の EST 固定で UTC に直した場合）。

### C-2. HistData の再取得の同一性

- 2026-10-15 に無料のページから再取得した `HISTDATA_COM_ASCII_USDJPY_M1_2019.zip`（A-2）を展開した `DAT_ASCII_USDJPY_M1_2019.csv` の SHA-256 は `733abb305f9a0e8f0f50c5cfc0faf2df35c10c5751866f4242e0597eaccb6287` で、手元の既存ファイル（`data/raw/histdata/USDJPY/HISTDATA_COM_ASCII_USDJPY_M12019/DAT_ASCII_USDJPY_M1_2019.csv`）と一致した。行数 372,585 も一致。1 ペア・1 年だけの確認である。

### C-3. 道具の SHA-256（`<scratch>/d12_scripts/`）

| ファイル | SHA-256 | 何をするか |
|---|---|---|
| histdata_gaps.py | ee419e18dd72a6b77db689f0e0c1e19c984a0959a8be10dfc2dc2982d756207c | C-1 の表（時刻の列だけ） |
| gap_months.py | ee34b97635694f9075d59660c10d43c8c1ecbacdc2c616b7a7683faa9bede473 | 月別の空白（時刻の列だけ） |
| gap_list.py | 12a62599c887a89284de7f073cb57b1742937a72e390b29b0a193b46a6c8b037 | 空白の一覧（時刻の列だけ） |
| fxcm_weeks.py | 8663ce9e1c5eaa9691e14de85394a575ef5ca6bab05944acc44534d0b1a75cc4 | A-3 の追加の確認（研究期間だけ。2024 年以降の行があれば止まる） |

> 注: B-9 は v0.2 で足したので、節 C の後に置いた。

### B-9. 有料候補の追加調査（v0.2。サンプルの条件・見積の条件・納品物の仕様・利用条件）

調査役の記録（`<scratch>/survey_paid2.md`、SHA-256 `c87d47f63fdd4fecfccbc7b202227699573770461fe75814eecb71ac2e292ce3`）をそのまま写す。時刻は調査役が `date` で実測した値。

実測時刻: 2026-10-15 14:51 JST（`date` 実測。取得は 14:50〜14:52 JST の範囲）。方法: WebFetch/WebSearch のみ。購入・登録・問い合わせ・サンプル取得なし。2024年以降のデータは未取得。
注意: WebFetch は小型モデル要約経由。引用は要約が返した文言で原文照合未実施。検索結果のみは【二次】。前回記録 survey_paid.md と重複する点は省略。

#### Tick Data LLC
- 出典: https://www.tickdata.com/product/historical-forex-data/ , https://www.tickdata.com/forex-faq/ （14:51 JST）
- サンプル: 「Sample Data」ボタン→ウェブフォーム（連絡先入力を伴う）。ペア・期間・粒度の選択可否、任意の月を指定できるか: 未確認。取得せず。
- 見積: 価格非掲載。「Data Pricing」リンクもフォーム。見積項目はページに明記なし（未確認。前回の推定項目も未確認のまま）。
- 納品物:
  - bid/ask: ティックは bid と ask の両方。1分足は "built from Bid side of quotes"（bid のみ）。
  - 形式: "ASCII text files"。ミリ秒精度。
  - 時刻: FAQ "Forex data is also in U.S. Eastern Time."、"output the data in any time zone" 可（ツール経由）。UTC 文書は未確認。DST 扱いは未記載。週の開始 "6:00pm ET" ～金曜 "5:59:59.999pm ET"。
  - クリーニング: "We filter the Forex Bid data on a tick-by-tick basis..." "We do NOT overwrite any ticks, but instead flag suspect ticks and suggest a corrected price." "Most of our data file contain both raw and filtered prices."（生データと補正案の両方が残る点は作業量削減に効く。ただし「Bid data を filter」とあり ask 側の扱いは未確認）。
  - 気配元: "an aggregation of quotes from the various contributing institutions."
  - 休場・祝日カレンダー/欠落報告: 未記載（未確認）。
  - 改訂・版管理: 版管理は未記載。"We will make every attempt to fix the problem."（報告された問題について）。更新購読: 1年単位の日次更新。再ダウンロード可否: 未確認。
- 利用条件: FAQ/商品ページに研究利用・保存の文なし。Terms of Use は前回確認済み（研究条項なし）。未確認。

#### Massive（旧 Polygon）
- 出典: https://massive.com/pricing?product=currencies , https://massive.com/docs/flat-files/forex/quotes , https://massive.com/legal/market-data-terms-of-service , https://massive.com/business （14:51 JST）
- サンプル: 無料 Basic $0（2年・分足・5回/分、登録要）。Quotes は Basic に含まれない。Flat Files は Starter 以上。研究期間の任意月の指定可否（Basic は直近2年のため 2017〜2023 は不可）。
- 見積: Starter $49/月（"Billed monthly"）で "10+ Years Historical Data"、Quotes、Flat Files、Second Aggregates 含む。Business 通貨プランの価格は business ページに非掲載（Stocks Business "$2,499/month" のみ掲載）。Enterprise "Custom"、"Contact us"。
- 納品物:
  - Flat Files forex quotes: bid_price, ask_price, participant_timestamp（ナノ秒 Unix）。"Records date back to September 25, 2009."、"daily downloadable S3 file"。"Updated at 11a ET to include the previous day"。
  - 時刻: Unix なので実質 UTC だが、ページに時刻基準の明記なし（例値から UTC と読めるのみ）。
  - 欠落・補正・クリーニング方針、休場カレンダー、改訂・版管理、再ダウンロード: 記載なし（未確認）。
  - 気配元（単一/集約）: 未確認。bid_exchange/ask_exchange のID項目あり。
- 利用条件（Market Data Terms of Service）: "exclusively for your personal, non-business, and non-commercial purposes"（§1）。"may not be copied, reproduced, republished, uploaded, posted, publicly displayed"（§2、Market Data は原則 display use only との記述）。§5(c) 第三者への派生データ・研究の共有禁止。§8 解約時 "delete all Market Data in your possession"（保存は解約まで）。→ 個人の非公開研究は「非事業」であれば範囲内の可能性があるが、保存の明示許諾文は無し。事業・法人用途は不可。

#### Kibot
- 出典: http://www.kibot.com/ , http://www.kibot.com/free-historical-intraday-data.html , http://www.kibot.com/file-format/data-format-reference.html （14:52 JST）
- サンプル: 無料サンプルに FX なし。"Futures and forex samples are available on request through the contact form."（問い合わせ必要。実施せず）。無料枠自体は登録不要、1分足は直近3か月・ティックは直近30日のみ → 2017〜2023 の任意月は無料枠では不可。
- 見積: FX 専用価格なし。Custom orders は $40〜（前回）。FX の項目別条件は未確認。
- 納品物: FX は Bid/Ask。ティック列 `Date,Time,Price,Bid,Ask,Volume`（Price は bid、Volume は1またはティック数）。時刻は既定 US Eastern（変更可）、DST は未記載。バー時刻 = バー開始時刻（秒・分・日足）。取引の無い区間はバーを出さない（ゼロ出来高バーなし）。気配元、FX 開始年、欠落報告、休場カレンダー、改訂・版管理、再ダウンロード: 未確認。
- 利用条件: 前回確認済み "The Data is for the Licensee's own internal use only."（個人の非公開研究への明示なし。internal use は社内/自己利用と解釈可能だが未確認）。

#### FirstRate Data
- 出典: https://firstratedata.com/it/fx , https://firstratedata.com/about/dataquality , https://firstratedata.com/free-tick-data （14:51 JST）
- サンプル: 各データセットにサンプルあり（FX ページ）。ただし free-tick-data ページの無料枠は株・ETF のみで FX 無し、"limited to one day of tick data (or two weks of 1-minute intraday data)"。登録・メールの要否: 記載なし。FX サンプルの期間は未記載（前回の S3 リンク先は未取得）。任意月指定は不可と見られる（固定サンプル）。
- 見積: FX ページに価格なし（"cost-effective bundles" とバンドルページへの案内）。更新購読 $99.95/年（前回）。FX Majors（70 クロス）バンドルの存在のみ確認。EURJPY・GBPJPY は 74 ティッカー一覧に無し（今回要約。一部項目は "Available only in the" で途切れ、不確実）。
- 納品物: OHLCV の 1分/5分/30分/1時間。bid/ask の別: 記載なし。時刻: "All datasets are in the local timezone of the instrument or index."（FX の具体的基準は未確認、UTC 文書なし）。品質ページ: "checked for gaps, duplicates, truncation, and anomalies"、"we do not include zero volume bars"（欠落は区間欠落として現れる）。"We update all datasets at the end of each trading day."、問題時は "we firstly rerun the data collection and assembly process"。休場カレンダー、改訂履歴・版管理、再ダウンロード可否: 未確認（品質ページは株式の記述が中心で FX への適用は未確認）。
- 利用条件: 公開ページに利用条項なし（about/terms は 404）。未確認。

#### LSEG Tick History
- 出典: 公式 tick-history ページは 404。検索結果のみ【二次】: https://community.developers.lseg.com/discussion/116035/how-to-change-timestamp ほか（14:52 JST）
- サンプル/見積/利用条件: 公開情報で未確認（前回: 価格非公開、見積要）。見積項目も公式には未確認。
- 納品物【二次】: LSEG 開発者フォーラム上のサポート回答で、FX の時刻は GMT/UTC のみ（"local exchange time is also Gmt/Utc as source is from Refinitv"）。クエリ日付範囲は DateRangeTimeZone で指定可。bid/ask の別、クリーニング方針、版管理: 未確認。

#### TraderMade
- 出典: https://tradermade.com/pricing , https://tradermade.com/terms-and-conditions , https://marketdata.tradermade.com/blog/how-to-get-forex-historical-data （14:51 JST）
- サンプル: 無料はお試し的な分足・時間足・日足（"only to show our data quality"）、7-Day Trial（内容未記載）。ティックの無料サンプルは記載なし。2017〜2023 の任意月は未確認。
- 見積: FX & Crypto "£ 599 /month"（"Business Use"、Historical Tick を機能として記載）。Enterprise "Custom"（25+ Years Historical、再配布権）。ダウンロード式は "calculate the data cost without setting up an account at the data shopping cart"（カート計算器で登録なし見積可。ペア・期間で変動。前回の £240/年/ペアは二次）。
- 納品物: CSV、メールまたはリンクで配信。"bid-ask rates with the timestamp in milliseconds"。"aggregated and cleaned for accuracy"。ティック・分足は 2016-09 から。時刻基準、欠落報告、版管理、再ダウンロード: 未確認。
- 利用条件: "receive, process TraderMade API Data & Services internally"、"solely to use ... for your own reference"、"prepare derivative work(s)... based on" 禁止、契約終了後の "use, store, or access" 禁止。→ 保存は契約期間中のみと読める。ダウンロード購入分に別規約があるかは未確認。

#### Dukascopy（有料経路以外）
- 検索結果のみ【二次】: Tickstory サポート返信 "you are also bound by Dukascopy's license terms"。Dukascopy 自身の商用ライセンス条項は今回確認できず（未確認）。

#### 主な発見
1. Tick Data: ティック bid/ask、生値と補正案を併記（上書きしない）、時刻は US Eastern。サンプルと価格はフォーム経由のみ。
2. Massive Starter $49/月で 2009-09 から bid/ask のナノ秒 quotes を日次ファイルで取得可。ただし規約は非事業の個人限定で、保存は解約まで。
3. LSEG は FX 時刻 UTC【二次】だが公式の価格・サンプル・規約は未確認。
4. Kibot の FX サンプルは問い合わせ必須、FirstRate の無料サンプルに FX なし（登録なしで取れる FX サンプルは今回見つからず）。
5. 休場カレンダー・欠落報告・版管理を文書化している候補は、今回の公開情報では無し。

---

## D. v0.2 案で足した確認（2026-10-15 14:50〜15:10 ごろ JST、設計役）

道具はすべて `<scratch>/d12_scripts/`、出力は `<scratch>/d12_work/`（ハッシュは D-7）。価格を読んだのは研究期間（2017〜2023 年）のサンプルだけで、どの道具も 2024 年以降の行があれば止まる。HistData の手元のファイルは時刻の列だけを読んだ。

### D-1. OANDA のサンプル（人間のダウンロード）

- ファイル: `data/raw/oanda/USDJPY/ticks_USDJPY-oj5k_2019-03.csv`（人間が 2026-10-15 14:45 JST ごろに置いた。設計役は読んだだけで、変更・移動・上書きしていない）。`.gitignore` の 17 行目 `data/raw/*` で版管理の外（`git check-ignore -v` で確認）。
- SHA-256 `9ae0fe4de82a5ba2e97cab6450194f0afe29793b92f5ba87ab47d55284448ef2`、235,513,950 バイト、5,607,475 行（見出し 1 行を含む）。
- 見出し: `<DATE>	<TIME>	<BID>	<ASK>	<LAST>	<VOLUME>`（タブ区切り）。`<LAST>`・`<VOLUME>` は空。日付は `YYYY.MM.DD`、時刻はミリ秒まで。
- 道具: `oanda_ticks.py`（出力 `oanda_ticks_out.txt` と、1 分ごとの集計 `oanda_min.csv`。後者は価格を含むので scratch にだけ置く）。
- 結果: 時刻の逆行 0、直前の行と同じ時刻の行 40,442、直前の行と全く同じ内容の行 37,824、ask < bid 0、ask = bid 0、bid・ask の空 0。tick のある分 30,659。
- 日ごとの最初と最後の tick（サーバー時刻）: 2019.02.28 は 17:00:00.001〜23:59:59.717。2019.03.01〜03.29 の平日は、各日 00:00 台〜23:59 台（月曜は 00:00:04〜00:00:19 に始まる）。土日の日付の行は無い。最後の行は 2019.03.29 23:59:59.056。
- 時刻の読み方: 週のデータが月曜 00:00〜金曜 24:00（サーバー時刻）で、2019-03-10（米国の夏時間の開始）の前後で変わらない → サーバー時刻 = ニューヨークの現地時刻 + 7 時間と読んだ（文書での確認は【未確認】）。D-2 のずらしの比較でこの読み方を確かめた。

### D-2. 同じ UTC 区間・同じ取引カレンダーでの比較

- 道具: `compare_gaps.py`。取引カレンダー `fx_ny17` の週（ニューヨーク日曜 17:00〜金曜 17:00、`zoneinfo` の `America/New_York`）の中の 1 分ごとに、各社のデータの有無を数える。空白は長さを問わずすべて数える。HistData は公表仕様（EST 固定）で +5 時間、OANDA は D-1 の読み方、FXCM は UTC のまま。
- 2019-03-01 00:00Z〜2019-03-29 21:00Z（出力 `compare_2019_03.txt`）: 期待 30,120 分。HistData 欠け 96（0.32%）、15 分以上の空白 1（03-10 21:00Z、60 分）、週の外 0。OANDA 欠け 1（0.00%）、最長 1 分、週の外 0。FXCM 欠け 96（0.32%）、15 分以上 0、最長 11 分、週の外 0。
- 2023-03-05 22:00Z〜2023-03-31 21:00Z（出力 `compare_2023_03.txt`）: 期待 28,800 分。HistData 欠け 8,502（29.52%）、15 分以上の空白 124、最長 240 分。FXCM 欠け 199（0.69%）、15 分以上 1（03-19 21:17Z、17 分）、最長 17 分。
- 同じ UTC の分の bid の終値の差の中央値（pip、ずらし −60・−1・0・+1・+60 分）: 2019 年 3 月は HistData 対 OANDA 4.00・0.50・**0.10**・0.50・3.90、HistData 対 FXCM 4.00・0.50・**0.10**・0.50・4.00、OANDA 対 FXCM 4.00・0.50・**0.10**・0.50・4.00。2023 年 3 月の HistData 対 FXCM は 7.70・2.10・**0.20**・2.10・13.60。
- 比べた 2 つの区間に、カレンダー版 2 の休場の宣言は無い（版 2 の宣言は元日とクリスマスだけ）。

### D-3. FXCM の ask < bid の調査

- 追加で取得したサンプル（`https://candledata.fxcorporate.com/m1/USDJPY/<年>/11.csv.gz`、2026-10-15 14:50〜15:00 JST の間、`<scratch>/survey_samples/fxcm_years/`）:

| ファイル | SHA-256 |
|---|---|
| USDJPY_m1_2017_w11.csv.gz | 1c16b1a208db403d2714a23febe65fdb1c0a3754fef569eccc2f3ed8e7480eed |
| USDJPY_m1_2018_w11.csv.gz | 3dd4e2939e6ab0f09ca0210fe68e3896104b8ec176a614e9b12522c802a34c9e |
| USDJPY_m1_2020_w11.csv.gz | 197fbb30cafba59f29935fbaca56f4135db93d78fcc3ef8f2c006b917a941dea |
| USDJPY_m1_2021_w11.csv.gz | bf2e29110b3eec217cd76cb1af10229ae3bea806ea1137c8c913179839564554 |
| USDJPY_m1_2022_w11.csv.gz | 64377cb360133160dccae5fdb2df6ca8db3aecfd042d16a64b4e807e896f1f59 |

- 道具: `fxcm_inversion.py`（出力 `fxcm_inversion.txt`）。逆転の数え方: AskOpen < BidOpen・AskHigh < BidHigh・AskLow < BidLow・AskClose < BidClose のどれか（`any`）。

| 週 | 足 | 逆転（any） | 始値 | 高値 | 安値 | 終値 | 大きさ 中央値・最大（pip） | 多い時間帯（UTC 時: 本数、上位 2） |
|---|---|---|---|---|---|---|---|---|
| 2017 w11 | 7,150 | 154 | 40 | 39 | 61 | 40 | 0.1・0.5 | 18: 24、20: 21 |
| 2018 w11 | 7,187 | 522 | 159 | 144 | 219 | 159 | 0.1・1.0 | 17: 47、20: 41 |
| 2019 w9 | 7,188 | 192 | 44 | 68 | 64 | 44 | 0.1・1.3 | 16: 20、10: 17 |
| 2019 w10 | 7,177 | 130 | 34 | 56 | 23 | 34 | 0.1・0.9 | 8: 13、6: 11 |
| 2019 w11 | 7,188 | 116 | 29 | 31 | 41 | 29 | 0.1・0.5 | 14: 10、10: 9 |
| 2019 w12 | 7,162 | 182 | 61 | 75 | 77 | 61 | 0.1・1.6 | 21: 30、15: 15 |
| 2019 w13 | 7,177 | 147 | 43 | 51 | 50 | 43 | 0.1・0.8 | 21: 13、4: 11 |
| 2019 w14 | 7,185 | 138 | 40 | 34 | 48 | 40 | 0.1・1.4 | 19: 14、9: 13 |
| 2020 w11 | 7,189 | 225 | 60 | 63 | 80 | 60 | 0.1・1.5 | 15: 21、13: 19 |
| 2021 w11 | 7,126 | 265 | 73 | 71 | 95 | 73 | 0.1・0.4 | 9: 32、8: 26 |
| 2022 w11 | 7,139 | 0 | 0 | 0 | 0 | 0 | — | — |
| 2023 w11 | 7,155 | 0 | 0 | 0 | 0 | 0 | — | — |

- 終値のスプレッド（AskClose − BidClose）の中央値（pip）: 2017 年 0.4、2018・2019 年 0.2、2020 年 0.4、2021 年 0.2、2022 年 0.3、2023 年 0.7（各年の週 11）。
- OANDA との同じ分: 2019 年の週 9〜13 の逆転した足のうち、OANDA のサンプル（2019-02-28 15:00Z〜03-29 21:00Z）に同じ UTC の分がある 618 本（週 9: 43、週 10: 130、週 11: 116、週 12: 182、週 13: 147）で、OANDA のその分の最小のスプレッドが負の分は 0。例（週 12、UTC 21 時台）: FXCM の終値のスプレッド −1.2 pip のとき、OANDA の終値のスプレッド 4.3 pip。
- 始値と直前の終値: 2019 年の週 11 で BidOpen = 直前の BidClose が 7,187 本中 7,187 本、AskOpen = 直前の AskClose も同じ。2023 年の週 11 は 7,154 本中 7,154 本（`<scratch>` で 1 行の Python で確認。道具のファイルは無い）。

### D-4. HistData の取引カレンダーでの年ごとの表（時刻の列だけ）

- 道具: `histdata_calendar_gaps.py`。2017〜2023 年（UTC の年。2023 年は 2023-12-29 22:00Z まで）、8 ペア。取引カレンダー `fx_ny17` の週の中の 1 分ごとに 1 分足の有無を数え、空白は長さを問わずすべて数える。カレンダー版 2（`configs/calendars/fx_ny17_v2.yaml`）の休場の宣言に重なる 15 分以上の空白は、数えた上で列 `gaps_overlapping_v2_closure` に件数を出した（除いていない）。2024 年のファイルは開いていない。
- 時刻の読み方を 2 通りで出した。**どちらも全週では合わない**（`outside_session_rows` が 0 でない年がある）。
- `histdata_outside.py` の確認: 公表仕様（EST 固定、+5 時間）で読むと、週の外の行は USDJPY の 2019 年で 1,799 行、2017 年で 2,031 行で、すべて金曜のニューヨーク 17 時台。

**(a) ラベルをニューヨークの現地時刻（夏時間あり）として読む**（出力 `histdata_cal_nylocal.csv`、空白の一覧 `histdata_cal_gaps_nylocal.csv`）

| pair | year_utc | expected_session_min | present_in_session | outside_session_rows | missing_min | missing_pct | gaps_ge15 | gaps_ge60 | gaps_ge1440 | longest_gap_min | gaps_overlapping_v2_closure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| USDJPY | 2017 | 374520 | 371298 | 0 | 3222 | 0.860 | 3 | 3 | 1 | 1440 | 1 |
| USDJPY | 2018 | 375840 | 371800 | 0 | 4040 | 1.075 | 3 | 3 | 0 | 1320 | 3 |
| USDJPY | 2019 | 375840 | 372349 | 176 | 3491 | 0.929 | 11 | 11 | 0 | 1320 | 3 |
| USDJPY | 2020 | 377280 | 371912 | 228 | 5368 | 1.423 | 9 | 9 | 0 | 1320 | 3 |
| USDJPY | 2021 | 375720 | 369168 | 165 | 6552 | 1.744 | 6 | 6 | 0 | 1320 | 1 |
| USDJPY | 2022 | 374400 | 372386 | 150 | 2014 | 0.538 | 7 | 6 | 0 | 60 | 0 |
| USDJPY | 2023 | 374400 | 322729 | 167 | 51671 | 13.801 | 706 | 705 | 0 | 1380 | 1 |
| USDCHF | 2017 | 374520 | 369892 | 0 | 4628 | 1.236 | 3 | 3 | 1 | 1440 | 1 |
| USDCHF | 2018 | 375840 | 370264 | 0 | 5576 | 1.484 | 5 | 3 | 0 | 1320 | 3 |
| USDCHF | 2019 | 375840 | 370905 | 176 | 4935 | 1.313 | 11 | 10 | 0 | 1325 | 3 |
| USDCHF | 2020 | 377280 | 370131 | 216 | 7149 | 1.895 | 10 | 9 | 0 | 1324 | 3 |
| USDCHF | 2021 | 375720 | 365390 | 100 | 10330 | 2.749 | 15 | 6 | 0 | 1320 | 1 |
| USDCHF | 2022 | 374400 | 369255 | 93 | 5145 | 1.374 | 30 | 8 | 0 | 65 | 0 |
| USDCHF | 2023 | 374400 | 320642 | 91 | 53758 | 14.358 | 694 | 688 | 0 | 1380 | 1 |
| AUDJPY | 2017 | 374520 | 372157 | 0 | 2363 | 0.631 | 3 | 3 | 1 | 1441 | 0 |
| AUDJPY | 2018 | 375840 | 372850 | 0 | 2990 | 0.796 | 3 | 3 | 0 | 1320 | 3 |
| AUDJPY | 2019 | 375840 | 372652 | 233 | 3188 | 0.848 | 10 | 9 | 0 | 1320 | 3 |
| AUDJPY | 2020 | 377280 | 372530 | 225 | 4750 | 1.259 | 9 | 9 | 0 | 1320 | 3 |
| AUDJPY | 2021 | 375720 | 371944 | 168 | 3776 | 1.005 | 6 | 6 | 0 | 1320 | 1 |
| AUDJPY | 2022 | 374400 | 372310 | 139 | 2090 | 0.558 | 12 | 11 | 0 | 65 | 0 |
| AUDJPY | 2023 | 374400 | 322732 | 55 | 51668 | 13.800 | 718 | 712 | 0 | 1264 | 1 |
| EURUSD | 2017 | 374520 | 371635 | 0 | 2885 | 0.770 | 3 | 3 | 1 | 1440 | 1 |
| EURUSD | 2018 | 375840 | 372607 | 0 | 3233 | 0.860 | 3 | 3 | 0 | 1320 | 3 |
| EURUSD | 2019 | 375840 | 372241 | 229 | 3599 | 0.958 | 11 | 10 | 0 | 1322 | 3 |
| EURUSD | 2020 | 377280 | 372047 | 228 | 5233 | 1.387 | 10 | 9 | 0 | 1320 | 3 |
| EURUSD | 2021 | 375720 | 369267 | 175 | 6453 | 1.718 | 7 | 6 | 0 | 1320 | 1 |
| EURUSD | 2022 | 374400 | 372640 | 165 | 1760 | 0.470 | 4 | 4 | 0 | 60 | 0 |
| EURUSD | 2023 | 374400 | 322464 | 114 | 51936 | 13.872 | 701 | 698 | 0 | 1264 | 1 |
| EURJPY | 2017 | 374520 | 372258 | 0 | 2262 | 0.604 | 3 | 3 | 1 | 1442 | 0 |
| EURJPY | 2018 | 375840 | 373304 | 0 | 2536 | 0.675 | 3 | 3 | 0 | 1321 | 3 |
| EURJPY | 2019 | 375840 | 372319 | 231 | 3521 | 0.937 | 12 | 11 | 0 | 1326 | 3 |
| EURJPY | 2020 | 377280 | 372443 | 204 | 4837 | 1.282 | 9 | 9 | 0 | 1326 | 3 |
| EURJPY | 2021 | 375720 | 371093 | 152 | 4627 | 1.232 | 7 | 6 | 0 | 1320 | 1 |
| EURJPY | 2022 | 374400 | 371997 | 137 | 2403 | 0.642 | 12 | 8 | 0 | 65 | 0 |
| EURJPY | 2023 | 374400 | 322934 | 106 | 51466 | 13.746 | 690 | 686 | 0 | 1320 | 1 |
| GBPJPY | 2017 | 374520 | 370949 | 0 | 3571 | 0.953 | 3 | 3 | 1 | 1443 | 0 |
| GBPJPY | 2018 | 375840 | 372495 | 0 | 3345 | 0.890 | 4 | 3 | 0 | 1322 | 3 |
| GBPJPY | 2019 | 375840 | 371863 | 179 | 3977 | 1.058 | 11 | 10 | 0 | 1326 | 3 |
| GBPJPY | 2020 | 377280 | 372421 | 228 | 4859 | 1.288 | 10 | 9 | 0 | 1324 | 3 |
| GBPJPY | 2021 | 375720 | 371370 | 139 | 4350 | 1.158 | 9 | 6 | 0 | 1320 | 1 |
| GBPJPY | 2022 | 374400 | 372102 | 127 | 2298 | 0.614 | 15 | 6 | 0 | 66 | 0 |
| GBPJPY | 2023 | 374400 | 321964 | 107 | 52436 | 14.005 | 696 | 693 | 0 | 1320 | 1 |
| GBPUSD | 2017 | 374520 | 371204 | 0 | 3316 | 0.885 | 4 | 3 | 1 | 1440 | 1 |
| GBPUSD | 2018 | 375840 | 372261 | 0 | 3579 | 0.952 | 4 | 3 | 0 | 1320 | 3 |
| GBPUSD | 2019 | 375840 | 372096 | 240 | 3744 | 0.996 | 11 | 10 | 0 | 1326 | 3 |
| GBPUSD | 2020 | 377280 | 372766 | 231 | 4514 | 1.196 | 13 | 9 | 0 | 1322 | 3 |
| GBPUSD | 2021 | 375720 | 370182 | 156 | 5538 | 1.474 | 7 | 6 | 0 | 1320 | 1 |
| GBPUSD | 2022 | 374400 | 372286 | 148 | 2114 | 0.565 | 9 | 6 | 0 | 61 | 0 |
| GBPUSD | 2023 | 374400 | 322352 | 55 | 52048 | 13.902 | 701 | 697 | 0 | 1320 | 1 |
| AUDUSD | 2017 | 374520 | 368436 | 0 | 6084 | 1.624 | 3 | 3 | 1 | 1440 | 1 |
| AUDUSD | 2018 | 375840 | 369457 | 0 | 6383 | 1.698 | 3 | 3 | 0 | 1320 | 3 |
| AUDUSD | 2019 | 375840 | 371700 | 238 | 4140 | 1.102 | 13 | 13 | 0 | 1320 | 3 |
| AUDUSD | 2020 | 377280 | 371594 | 221 | 5686 | 1.507 | 11 | 9 | 0 | 1320 | 3 |
| AUDUSD | 2021 | 375720 | 369513 | 171 | 6207 | 1.652 | 7 | 6 | 0 | 1320 | 1 |
| AUDUSD | 2022 | 374400 | 371347 | 148 | 3053 | 0.815 | 9 | 7 | 0 | 60 | 0 |
| AUDUSD | 2023 | 374400 | 321921 | 168 | 52479 | 14.017 | 685 | 681 | 0 | 1320 | 1 |

**(b) 公表仕様（EST 固定、+5 時間）で読む**（出力 `histdata_cal.csv`、空白の一覧 `histdata_cal_gaps.csv`）

| pair | year_utc | expected_session_min | present_in_session | outside_session_rows | missing_min | missing_pct | gaps_ge15 | gaps_ge60 | gaps_ge1440 | longest_gap_min | gaps_overlapping_v2_closure |
|---|---|---|---|---|---|---|---|---|---|---|---|
| USDJPY | 2017 | 374520 | 369267 | 2031 | 5253 | 1.403 | 37 | 37 | 1 | 1440 | 1 |
| USDJPY | 2018 | 375840 | 369775 | 2025 | 6065 | 1.614 | 37 | 37 | 0 | 1320 | 3 |
| USDJPY | 2019 | 375840 | 370726 | 1799 | 5114 | 1.361 | 37 | 37 | 0 | 1320 | 3 |
| USDJPY | 2020 | 377280 | 370354 | 1786 | 6926 | 1.836 | 35 | 35 | 0 | 1320 | 3 |
| USDJPY | 2021 | 375720 | 367520 | 1813 | 8200 | 2.182 | 34 | 34 | 0 | 1320 | 1 |
| USDJPY | 2022 | 374400 | 370682 | 1854 | 3718 | 0.993 | 34 | 34 | 0 | 90 | 0 |
| USDJPY | 2023 | 374400 | 321760 | 1136 | 52640 | 14.060 | 715 | 714 | 0 | 1380 | 1 |
| USDCHF | 2017 | 374520 | 367868 | 2024 | 6652 | 1.776 | 37 | 37 | 1 | 1440 | 1 |
| USDCHF | 2018 | 375840 | 368277 | 1987 | 7563 | 2.012 | 39 | 37 | 0 | 1320 | 3 |
| USDCHF | 2019 | 375840 | 369290 | 1791 | 6550 | 1.743 | 37 | 36 | 0 | 1325 | 3 |
| USDCHF | 2020 | 377280 | 368586 | 1761 | 8694 | 2.304 | 35 | 35 | 0 | 1324 | 3 |
| USDCHF | 2021 | 375720 | 363729 | 1761 | 11991 | 3.191 | 44 | 34 | 0 | 1320 | 1 |
| USDCHF | 2022 | 374400 | 367500 | 1848 | 6900 | 1.843 | 52 | 36 | 0 | 92 | 0 |
| USDCHF | 2023 | 374400 | 319429 | 1304 | 54971 | 14.682 | 706 | 699 | 0 | 1380 | 1 |
| AUDJPY | 2017 | 374520 | 370118 | 2039 | 4402 | 1.175 | 37 | 37 | 1 | 1441 | 0 |
| AUDJPY | 2018 | 375840 | 370859 | 1991 | 4981 | 1.325 | 37 | 37 | 0 | 1320 | 3 |
| AUDJPY | 2019 | 375840 | 371086 | 1799 | 4754 | 1.265 | 35 | 34 | 0 | 1320 | 3 |
| AUDJPY | 2020 | 377280 | 370958 | 1797 | 6322 | 1.676 | 35 | 35 | 0 | 1320 | 3 |
| AUDJPY | 2021 | 375720 | 370262 | 1850 | 5458 | 1.453 | 34 | 34 | 0 | 1320 | 1 |
| AUDJPY | 2022 | 374400 | 370592 | 1857 | 3808 | 1.017 | 39 | 39 | 0 | 90 | 0 |
| AUDJPY | 2023 | 374400 | 321469 | 1318 | 52931 | 14.138 | 729 | 723 | 0 | 1264 | 1 |
| EURUSD | 2017 | 374520 | 369599 | 2036 | 4921 | 1.314 | 37 | 37 | 1 | 1440 | 1 |
| EURUSD | 2018 | 375840 | 370579 | 2028 | 5261 | 1.400 | 37 | 37 | 0 | 1320 | 3 |
| EURUSD | 2019 | 375840 | 370671 | 1799 | 5169 | 1.375 | 36 | 35 | 0 | 1322 | 3 |
| EURUSD | 2020 | 377280 | 370478 | 1797 | 6802 | 1.803 | 36 | 35 | 0 | 1320 | 3 |
| EURUSD | 2021 | 375720 | 367618 | 1824 | 8102 | 2.156 | 35 | 34 | 0 | 1320 | 1 |
| EURUSD | 2022 | 374400 | 370948 | 1857 | 3452 | 0.922 | 32 | 32 | 0 | 64 | 0 |
| EURUSD | 2023 | 374400 | 321259 | 1319 | 53141 | 14.194 | 710 | 707 | 0 | 1264 | 1 |
| EURJPY | 2017 | 374520 | 370220 | 2038 | 4300 | 1.148 | 37 | 37 | 1 | 1442 | 0 |
| EURJPY | 2018 | 375840 | 371271 | 2033 | 4569 | 1.216 | 37 | 37 | 0 | 1321 | 3 |
| EURJPY | 2019 | 375840 | 370751 | 1799 | 5089 | 1.354 | 37 | 36 | 0 | 1326 | 3 |
| EURJPY | 2020 | 377280 | 370850 | 1797 | 6430 | 1.704 | 35 | 35 | 0 | 1326 | 3 |
| EURJPY | 2021 | 375720 | 369389 | 1856 | 6331 | 1.685 | 35 | 34 | 0 | 1320 | 1 |
| EURJPY | 2022 | 374400 | 370276 | 1858 | 4124 | 1.101 | 38 | 36 | 0 | 90 | 0 |
| EURJPY | 2023 | 374400 | 321660 | 1380 | 52740 | 14.087 | 699 | 695 | 0 | 1320 | 1 |
| GBPJPY | 2017 | 374520 | 368924 | 2025 | 5596 | 1.494 | 37 | 37 | 1 | 1443 | 0 |
| GBPJPY | 2018 | 375840 | 370465 | 2030 | 5375 | 1.430 | 38 | 37 | 0 | 1322 | 3 |
| GBPJPY | 2019 | 375840 | 370242 | 1800 | 5598 | 1.489 | 37 | 36 | 0 | 1326 | 3 |
| GBPJPY | 2020 | 377280 | 370852 | 1797 | 6428 | 1.704 | 36 | 35 | 0 | 1324 | 3 |
| GBPJPY | 2021 | 375720 | 369650 | 1859 | 6070 | 1.616 | 37 | 34 | 0 | 1320 | 1 |
| GBPJPY | 2022 | 374400 | 370369 | 1860 | 4031 | 1.077 | 40 | 34 | 0 | 90 | 0 |
| GBPJPY | 2023 | 374400 | 320751 | 1320 | 53649 | 14.329 | 703 | 701 | 0 | 1320 | 1 |
| GBPUSD | 2017 | 374520 | 369167 | 2037 | 5353 | 1.429 | 38 | 37 | 1 | 1440 | 1 |
| GBPUSD | 2018 | 375840 | 370244 | 2017 | 5596 | 1.489 | 38 | 37 | 0 | 1320 | 3 |
| GBPUSD | 2019 | 375840 | 370537 | 1799 | 5303 | 1.411 | 36 | 35 | 0 | 1326 | 3 |
| GBPUSD | 2020 | 377280 | 371199 | 1798 | 6081 | 1.612 | 38 | 35 | 0 | 1322 | 3 |
| GBPUSD | 2021 | 375720 | 368497 | 1841 | 7223 | 1.922 | 35 | 34 | 0 | 1320 | 1 |
| GBPUSD | 2022 | 374400 | 370579 | 1855 | 3821 | 1.021 | 36 | 34 | 0 | 85 | 0 |
| GBPUSD | 2023 | 374400 | 321272 | 1135 | 53128 | 14.190 | 708 | 704 | 0 | 1320 | 1 |
| AUDUSD | 2017 | 374520 | 366438 | 1998 | 8082 | 2.158 | 37 | 37 | 1 | 1440 | 1 |
| AUDUSD | 2018 | 375840 | 367512 | 1945 | 8328 | 2.216 | 37 | 37 | 0 | 1320 | 3 |
| AUDUSD | 2019 | 375840 | 370143 | 1795 | 5697 | 1.516 | 38 | 38 | 0 | 1320 | 3 |
| AUDUSD | 2020 | 377280 | 370031 | 1784 | 7249 | 1.921 | 37 | 35 | 0 | 1320 | 3 |
| AUDUSD | 2021 | 375720 | 367866 | 1818 | 7854 | 2.090 | 35 | 34 | 0 | 1320 | 1 |
| AUDUSD | 2022 | 374400 | 369646 | 1849 | 4754 | 1.270 | 37 | 35 | 0 | 72 | 0 |
| AUDUSD | 2023 | 374400 | 320686 | 1403 | 53714 | 14.347 | 693 | 689 | 0 | 1320 | 1 |

**USDJPY の 600 分以上の空白（(a) の読み方）**

| 始まり（UTC） | 分 | カレンダー版 2 の休場の宣言と |
|---|---|---|
| 2017-12-24T22:00:00+00:00 | 1440 | 重なる |
| 2018-01-01T00:00:00+00:00 | 1320 | 重なる |
| 2018-12-25T08:00:00+00:00 | 840 | 重なる |
| 2019-01-01T00:00:00+00:00 | 1320 | 重なる |
| 2019-12-25T08:00:00+00:00 | 840 | 重なる |
| 2020-01-01T00:00:00+00:00 | 1320 | 重なる |
| 2020-11-30T05:00:00+00:00 | 1140 | 重ならない |
| 2020-12-25T08:00:00+00:00 | 840 | 重なる |
| 2021-01-01T00:00:00+00:00 | 1320 | 重なる |
| 2021-05-31T04:00:00+00:00 | 1200 | 重ならない |
| 2023-04-07T00:00:00+00:00 | 1380 | 重ならない |
| 2023-12-25T07:59:00+00:00 | 845 | 重なる |

### D-5. FXCM の利用許諾のページ（2026-10-15 15:00〜15:05 JST の間）

- `https://www.fxcm.com/uk/forms/eula/`（WebFetch の要約）: 契約の本文ではなく、FXCM Software End User License Agreement の案内のページ。"Click the button below to be redirected to the form."。フォームは Adobe Sign にあり、中身は取得していない。署名する人・対象のデータ・個人か商用かの記述は案内のページに無い。ページにある関係しそうな文は "FXCM will use data collected for the purposes of providing service, contacting, and sending you important information." と "The products are intended for retail, professional, and eligible counterparty clients." だけ。
- `gh api repos/fxcm/MarketData`（15:05 JST ごろ）: `license` は null（リポジトリに使用許諾の設定が無い）。最後の push は 2026-07-09。
- 署名・申込みはしていない（人間の指示「署名や申込みが必要なら、その条件を確認する段階で止めて判断すればよい」）。

### D-6. Dukascopy の公開 HTTP 配信の再確認と公式ページ

- 2026-10-15 15:05:37 JST に `https://datafeed.dukascopy.com/datafeed/USDJPY/2019/02/05/10h_ticks.bi5`（2019-03-05 10 時台の tick）へ 1 回だけ要求: HTTP 429（`content-type: application/json`、本文 113 バイト）。
- Historical Data Export のページ（`https://www.dukascopy.com/swiss/english/marketwatch/historical/`、WebFetch の要約、15:05 JST ごろ）: "It is free through Dukascopy's Historical Data Export tool"、"The data is in .csv format."。登録・ログインの要否、期間・粒度の制限、時刻帯の選択は、ページの本文からは読めなかった（【未確認】）。
- 公式の wiki（`https://www.dukascopy.com/wiki/en/development/data-export/`、WebFetch の要約、2026-10-15 14:20〜14:30 JST の間）: 公開 HTTP 配信の制限や終了には触れていない。S3 のバケット `cfg-public-proper-wallaby`（eu-west-1）、"configured with Requester Pays policy"、"Valid AWS credentials with appropriate permissions"、"$0.0004 per 1,000 requests"・"$0.02 per GB"、全体 "~400 GB, ~20,000,000 files"、EURUSD は "Total objects: 26,586"・約 2.6 GB。

### D-7. 道具と出力の SHA-256

| ファイル | SHA-256 |
|---|---|
| d12_scripts/oanda_ticks.py | 902fee4d7b35c386a57263be5cc598537ceb321f053c4d5540ceb7f04fcba02a |
| d12_scripts/compare_gaps.py | be3d813135df84ae1940cb58a92b812da1dc53946a36f409113774e7aea42ddb |
| d12_scripts/fxcm_inversion.py | 218e4ce565379ca01ec31243e2f56c35eb28e29046e6d75a1101f1018c67a2f3 |
| d12_scripts/histdata_calendar_gaps.py | c53ccba726b290f549957cd224d3a5b8f48de7c789ed09d436c06033895fb127 |
| d12_scripts/histdata_outside.py | c2701c86d7966b662a8b427180d15c7d24b2afc04f27a18a129c203339dcaca9 |
| d12_work/oanda_ticks_out.txt | f190178a7df27493977fae12731ab86bd96f948e8a4136eae53be3f1352c4cb4 |
| d12_work/oanda_min.csv | 8c06eb689509401bf549ddc231ec5adebcabc50730315c39e10932806e8662bd |
| d12_work/compare_2019_03.txt | c3baace04a287f1898380c8fcf342ca6093cc15f81025d84d6bd12c750ff33b3 |
| d12_work/compare_2023_03.txt | 354cedf5ac66b9f1df63a8090d9dbf73d9e22ca2b085b14c2e5ce5074c81218b |
| d12_work/fxcm_inversion.txt | 9050a9be0d1554a9acd524c44c8e71c5b8d20e25657e35e7c5024b9e41bc2358 |
| d12_work/histdata_cal_nylocal.csv | 2e25a1d6748f4f1f27312402153fb4410dcc8edcdb42b44b5e9e477b44b8c1cf |
| d12_work/histdata_cal_gaps_nylocal.csv | 957dd7c7b03813ad33efe23efe0b0edf786ec944cde96b7c28660d88a735b966 |
| d12_work/histdata_cal.csv | 605047d945f421731743afc7704b326d73fbd206bfc809a1640ff670bff8d593 |
| d12_work/histdata_cal_gaps.csv | 22110aed0767c6dd21b80de4550e589b30daa2e447553022435034174c868401 |

---

## E. Tick Data へのサンプルと見積の条件の申請文（v0.3。**検討記録。送信しない（人間の決定）**）

> **v0.4 の注記**: 人間の第 3 回の指示（2026-10-15）「Tick Dataのサンプル・見積依頼は進めません。申請文は検討記録として残してください。」により、この申請文は送らない。下は v0.3 で作った文面の記録である。

人間の決定（2026-10-15、第 2 回）「Tick Dataはサンプルと見積条件を確認する方向に賛成です。申請文を作成してください。購入・有料契約は別途判断します。」に基づく下書き。送り先は Tick Data の Web のフォーム（"Sample Data"・"Data Pricing"。付録 B-9）。フォームの項目に合わせて切り貼りする前提。**購入・契約・2024 年以降のデータの請求はしない文面にした**。

### E-1. 日本語（人間の確認用）

> 件名: スポット FX の過去データのサンプルと見積の条件についての問い合わせ
>
> 個人で、非公開の研究（売買の手法の検証。第三者への再配布はしません）のために、スポット FX の過去データの購入を検討しています。購入の判断の前に、サンプルと見積の条件を確認させてください。
>
> **1. 対象**
> - 通貨ペア（8）: USD/JPY、USD/CHF、AUD/JPY、EUR/USD、EUR/JPY、GBP/JPY、GBP/USD、AUD/USD
> - 期間: 2017-01-01 〜 2023-12-31（この期間だけ。2024 年以降は今回は不要です）
> - 粒度: tick（第一希望）。1 分足は参考
> - 価格種別: bid と ask の両方（tick）。生の値と、疑わしい tick の印・補正案の両方が含まれる形式を希望
> - 時刻: UTC での出力を希望。できない場合は、時刻帯と夏時間の扱いを書いた資料をください。精度はミリ秒
> - 形式: ASCII（CSV）。ペア × 日（または月）ごとのファイル。ファイルごとのチェックサムがあれば希望
>
> **2. サンプル**
> - USD/JPY の 2019 年 3 月の tick（bid・ask、生の値と印つき）を希望します。難しければ、2017〜2023 年の中の任意の 1 か月でかまいません
> - 2024 年 1 月 1 日以降のデータを含めないでください
>
> **3. 見積の前提**
> - 上の 8 ペア × 2017〜2023 年の tick の一括購入（更新の購読は今は不要。将来の追加は別に相談）
> - 利用者 1 名、個人の非公開の研究、再配布なし、データは非公開の手元の記憶装置に保存
> - 1 分足を同時に購入した場合との差額も知りたい
>
> **4. 確認したいこと**
> 1. 個人の非公開の研究のための利用と、購入後のデータ（と、そこから作る足）の保存が、利用許諾で認められるか。許諾の文書をいただけますか
> 2. 過去データが後から修正されることがあるか。ある場合、版の管理と再ダウンロードの方法
> 3. 欠落・配信の停止・祝日の扱いについての資料があるか
> 4. 気配の出し手（集約の対象）と、bid・ask の作り方（同じ気配の組か）の資料があるか
> 5. 支払い・納品の方法と、購入後の再ダウンロードの可否

### E-2. 英語（送信用の下書き）

> Subject: Request for a sample and quote conditions — historical spot FX tick data (private research)
>
> I am an individual considering a purchase of historical spot FX data for private, non-commercial research (testing trading methods; no redistribution to third parties). Before deciding, I would like to request a sample and confirm the quote conditions.
>
> **1. Scope**
> - Currency pairs (8): USD/JPY, USD/CHF, AUD/JPY, EUR/USD, EUR/JPY, GBP/JPY, GBP/USD, AUD/USD
> - Period: 2017-01-01 to 2023-12-31 only (data from 2024 onward is not needed for this request)
> - Granularity: tick (preferred); 1-minute bars for reference
> - Price types: both bid and ask (tick). Preferably a format containing both raw prices and your suspect-tick flags / suggested corrections
> - Timestamps: UTC output preferred. If not available, please provide documentation of the time zone and daylight-saving handling. Millisecond precision
> - Format: ASCII (CSV), one file per pair per day (or month); per-file checksums if available
>
> **2. Sample**
> - USD/JPY tick data for March 2019 (bid and ask, raw and flagged). If that is not possible, any single month between 2017 and 2023 is fine
> - Please do not include any data dated on or after 2024-01-01
>
> **3. Assumptions for the quote**
> - One-time purchase of tick data for the 8 pairs above, 2017–2023 (no ongoing update subscription for now)
> - One user; private, non-commercial research; no redistribution; data stored on private local storage
> - Please also indicate the price difference if 1-minute bars are included
>
> **4. Questions**
> 1. Does the license permit private research use and retention of the purchased data (and bars derived from it) after purchase? Could you share the license document?
> 2. Is historical data ever revised after delivery? If so, how are versions managed and can updated files be re-downloaded?
> 3. Is there documentation on gaps, feed outages and holiday handling?
> 4. Is there documentation on the contributing sources and on how bid and ask are formed (i.e., whether each bid/ask pair comes from the same quote)?
> 5. Payment and delivery methods, and whether re-downloads are possible after purchase.

### E-3. 送る前に人間が決めること

- 名前・連絡先・国をフォームに書くこと（Tick Data に連絡先を渡すこと）を承認するか。
- 用途を「個人の非公開の研究」と書いてよいか（実態と違えば直す）。
- サンプルの月（2019 年 3 月の USD/JPY を推奨。OANDA・FXCM・HistData と同じ UTC 区間で比べられるため）。

---

## F. v0.3 案で足した確認（2026-10-15 15:36〜15:50 ごろ JST、設計役）

道具は `<scratch>/d12_scripts/`、出力は `<scratch>/d12_work/`（ハッシュは F-6）。OANDA のファイルは人間が置いた研究期間の原データで、読んだだけ（変更・移動・上書きしていない）。元データを読む道具（`oanda_check.py`・`compare2.py`）は 2024 年以降の行があれば止まる。`tick_gap_dist.py`・`longest_runs.py` は、それらの出力（研究期間だけ）を読む。大きいファイルは 1 行ずつ読んだ（ストリーム処理）。

### F-1. OANDA の 6 ファイルの形式と品質（`oanda_check.py`）

| ファイル | SHA-256 | バイト | 行（見出しを除く） | 欄の数 | 日付・時刻の書式の違反 | 小数の桁（bid・ask） | 逆行 | 同じ時刻の連続 | 同じ内容の連続 | ask < bid | ask = bid | 空 | tick のある分 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| USDJPY/ticks_USDJPY-oj5k_2017-03.csv | e81a25e40df1132bac238be51dc53935d9a401eae14c61ba9b9ec64e52ec7f56 | 452,212,530 | 10,766,964 | 6 | 0 | 3・3 | 0 | 649 | 378 | 0 | 0 | 0 | 33,166 |
| USDJPY/ticks_USDJPY-oj5k_2019-03.csv | 9ae0fe4de82a5ba2e97cab6450194f0afe29793b92f5ba87ab47d55284448ef2 | 235,513,950 | 5,607,474 | 6 | 0 | 3・3 | 0 | 40,442 | 37,824 | 0 | 0 | 0 | 30,659 |
| USDJPY/ticks_USDJPY-oj5k_2023-03.csv | 1c65012d9997e065250c2f35e05350464738ebd2f87d0462aa667724f26ed6d8 | 289,878,036 | 6,901,857 | 6 | 0 | 3・3 | 0 | 0 | 0 | 0 | 0 | 0 | 33,430 |
| EURJPY/ticks_EURJPY-oj5k_2019-03.csv | fbfc7dc6dec9505bb4c95118cfcc74c63b7e5f72c7bc4db4e98a37956759f80e | 317,656,080 | 7,563,239 | 6 | 0 | 3・3 | 0 | 12,630 | 11,504 | 0 | 0 | 0 | 30,659 |
| EURUSD/ticks_EURUSD-oj5k_2019-03.csv | 3cdb8eaa56f15269eef62d8e5ce3a1c70741a70d20051f5fb98bddbd98cc4e26 | 206,962,476 | 4,927,677 | 6 | 0 | 5・5 | 0 | 17,600 | 16,588 | 0 | 0 | 0 | 30,660 |
| GBPJPY/ticks_GBPJPY-oj5k_2019-03.csv | 66834f31e4e11ccea93ef5a04db19ff47128978d3a072493433bf42fbc484ea0 | 719,255,208 | 17,125,123 | 6 | 0 | 3・3 | 0 | 24,366 | 21,906 | 0 | 22 | 0 | 30,648 |

- 見出しはすべて `<DATE>	<TIME>	<BID>	<ASK>	<LAST>	<VOLUME>`（タブ区切り）。
- 最初と最後の tick（ファイルの時刻）: 2017-03 は 2017.02.28 17:00:00.029〜2017.03.31 17:59:59.987、2023-03 は 2023.02.28 17:00:00.049〜2023.03.31 17:59:59.982、2019-03 の 4 ファイルは 2019.02.28 17:00 台〜2019.03.29 23:59 台（GBPJPY の最後は 23:59:56.306）。
- 2023-03 の日曜の行（ファイルの日付 2023.03.05・03.12・03.19・03.26 の 22:00 台〜23:59 台）: 169・424・795・470 行。ニューヨーク時間の日曜 15〜17 時（開場前）。2017-03・2019-03 のファイルに日曜の日付の行は無い。
- `.gitignore` の 17 行目 `data/raw/*` の対象（2019-03 の USDJPY で `git check-ignore -v` を確認済み。同じディレクトリの形なので他も対象）。

### F-2. OANDA の時刻の資料（WebFetch の要約、WebSearch。2026-10-15 15:38〜15:42 JST）

- `https://www.oanda.jp/lab-education/blog_column/local_time/`（公式。日付の表示なし）: 「OANDAのMT5/MT4のサーバの時間は米国夏時間がGMT+3、米国冬時間がGMT+2となります。」「OANDAのMT5、MT4のサーバ時間は米国夏時間がGMT＋3、米国冬時間がGMT2となるため、米国夏時間の場合は6、米国冬時間の場合は7と入力すると、GMT9となり、日本時間（GMT+9）が表示されます。」
- `https://www.oanda.jp/lab-education/blog_mt5/basicmt5/tickdata`（公式。日付の表示なし）: マイページの「ティックデータのダウンロード」から、通貨ペア・年・月を選んで ZIP をダウンロードする。**時刻帯・列・ファイル名の説明は無い**。
- `https://www.oanda.jp/info/1159`（公式のお知らせ、2024-05-02）: 「変更前：USDJPY、EURJPY、EURUSD、GBPJPY」「変更後：USDJPY、EURJPY、EURUSD、GBPJPY、JP225、US30、US100、US500、XAUUSD」。改訂後のステータスの表でティックデータのダウンロードは Silver・Gold が利用可。時刻帯・期間・形式の記載は無い。
- `https://www.fxnav.net/historicaldata/`（第三者、2026-09-30）【二次】: 「データは1カ月単位です。」「期間は「日本時間1日0：00～末日23：59まで」のデータになります。」FX は「2016年9月以降」。
- **配布 CSV の時刻の基準を直接書いた公式資料は見つからなかった**。

### F-3. 同じ UTC 区間・同じ取引カレンダーでの比較（`compare2.py`）

- 区間: 各ファイルの日本時間の月（UTC で前月末 15:00Z〜当月末 15:00Z）。取引カレンダー `fx_ny17` の週の中の 1 分ごと（`zoneinfo` の `America/New_York`）。空白は長さを問わずすべて数える。比べた区間にカレンダー版 2 の休場の宣言は無い。
- 読み方: OANDA は「ファイルの時刻 − 7 時間をニューヨークの現地時刻として UTC に直す」、FXCM は UTC のまま、HistData は EST 固定（+5 時間）とニューヨークの現地時刻の 2 通り。
- FXCM の追加のファイル（研究期間。`https://candledata.fxcorporate.com/m1/<ペア>/<年>/<週>.csv.gz`、2026-10-15 15:37 JST ごろ、`<scratch>/survey_samples/fxcm_v3/`）: USDJPY 2017 年の週 9〜13 と 2023 年の週 9、EURUSD・EURJPY・GBPJPY 2019 年の週 9〜13（SHA-256 は F-6）。FXCM は保留（人間の決定）なので、比較の窓を埋めるためだけに使い、逆転の調査はしていない。

| ペア・月 | 期待する分 | OANDA 欠け（週の外） | FXCM 欠け | HistData(EST) 欠け（週の外） | HistData(NY) 欠け（週の外） | bid の終値の差の中央値（ずらし −60・−1・0・+1・+60 分）OANDA 対 FXCM |
|---|---|---|---|---|---|---|
| USDJPY 2017-03 | 33,180 | 14（0） | 197 | 255（120） | 75（0） | 6.30・0.70・0.10・0.70・6.30 |
| USDJPY 2019-03 | 30,660 | 1（0） | 100 | 98（0） | 214（116） | 4.00・0.50・0.10・0.50・4.00 |
| USDJPY 2023-03 | 33,180 | 72（322） | 220 | 9,842（0） | 9,953（111） | 11.70・1.40・0.10・1.40・11.70 |
| EURJPY 2019-03 | 30,660 | 1（0） | 29 | 7（0） | 182（175） | 5.50・0.80・0.10・0.80・5.50 |
| EURUSD 2019-03 | 30,660 | 0（0） | 105 | 10（0） | 183（173） | 3.50・0.50・0.20・0.60・3.60 |
| GBPJPY 2019-03 | 30,660 | 12（0） | 50 | 108（0） | 227（119） | 10.00・1.20・0.20・1.20・10.00 |

- OANDA 対 HistData のずらし 0 分の中央値: 2017-03 は NY の読みで 0.10（EST の読みでは 2.50、+60 分で 0.20）、2019-03 の USDJPY は EST の読みで 0.10（NY の読みでは 2.20）、2023-03 は EST 0.20・NY 0.30、EURJPY は EST 0.10、EURUSD は EST 0.10、GBPJPY は EST 0.30（pip。EURUSD は 0.0001、他は 0.01）。
- 各ペア・月の週ごとの欠けの数と、15 分以上の空白の一覧は、出力のファイル（`cmp_<ペア>_<月>.txt`）にある。

### F-4. OANDA の tick の間隔の分布（`tick_gap_dist.py`）

- 各分の中で終わる tick の間隔の最大（`oanda_check.py` の `max_gap_ms`）から数えた。ファイルの日付の変わり目（ニューヨーク 17:00）をまたぐ間隔は測っていない（限界）。表は本文第9.2節と同じ（出力 `tick_gap_dist.txt`）。

### F-5. 参考: HistData の 1 分足の、15 分以上の空白で切った最長の区間（`longest_runs.py`。時刻の列だけ）

2017-01-01T00:00Z〜2023-12-29T22:00Z。D-4 の空白の一覧から計算。ウォームアップは引いていない。「宣言で切らない」はカレンダー版 2 の休場の宣言に重なる空白で切らない場合。

| ペア | NY 現地時刻の読み: すべてで切る | NY: 宣言で切らない | EST 固定の読み: すべてで切る | EST: 宣言で切らない |
|---|---|---|---|---|
| USDJPY | 357.4 日（2018-01-01〜12-25） | 440.8 日（2017-12-30〜2019-03-15） | 154.0 日（2021-10-24〜2022-03-27） | 154.0 日（同じ） |
| USDCHF | 356.6 日（2017-01-02〜12-24） | 361.6 日（2017-01-02〜12-29） | 86.0 日（2020-01-01〜03-27） | 103.9 日（2020-12-01〜2021-03-14） |
| AUDJPY | 357.4 日 | 440.8 日 | 154.0 日 | 154.0 日 |
| EURUSD | 357.4 日 | 440.8 日 | 154.0 日 | 154.0 日 |
| EURJPY | 357.4 日 | 440.8 日 | 92.9 日 | 154.0 日 |
| GBPJPY | 357.0 日 | 359.9 日 | 88.0 日 | 95.9 日 |
| GBPUSD | 357.0 日 | 359.9 日 | 89.0 日 | 117.9 日 |
| AUDUSD | 357.4 日 | 440.8 日 | 154.0 日 | 154.0 日 |

- 15 分という切り方は参考のための仮の値で、第4.1節の条件ではない。

### F-6. 道具・出力・追加サンプルの SHA-256

| ファイル | SHA-256 |
|---|---|
| d12_scripts/oanda_check.py | f6df4f38b85aa5afda9672409fd7532a6016df091190c4e00a61d1e298d9abbb |
| d12_scripts/compare2.py | 2dcbdda81fd99ad311e455aa53e88886006e5e01cb7f4f33f3c03d0d8c9377b7 |
| d12_scripts/tick_gap_dist.py | 9959752566bdef0bf3e53bd360414b7ce0aba69abc98570e4e42c65c7f8ea786 |
| d12_scripts/longest_runs.py | ec850bd3a0c36ffa5e0504fc5bce3f87721a0336937cd496ecbf906d7adcf076 |
| d12_work/oanda_check_USDJPY_2017-03.txt | fa50121b2ab48a02e9e2065eec882a1328f4a2fba80a0e1baf9037a119a1f1b8 |
| d12_work/oanda_check_USDJPY_2019-03.txt | 6594e85bee37fe6742172d1f09a584b82290d1b5478d6af5e59ff5bf5877a702 |
| d12_work/oanda_check_USDJPY_2023-03.txt | 302ac02100ed484bc16a9157c2315170ed30ff09d8999f6b8112619047b60f62 |
| d12_work/oanda_check_EURJPY_2019-03.txt | b44d9be29fbed4f5bd0a2b89cb95021a91c39897b6d332e187382e2b23b13b4d |
| d12_work/oanda_check_EURUSD_2019-03.txt | 832be426df4b3f4d2011553838629fb5eee871993a1feeea505296ffb1a170e6 |
| d12_work/oanda_check_GBPJPY_2019-03.txt | 99115d0b99d586c69479a0b4467225f5e6b37d06749a1c8368e03c5f1914a9cb |
| d12_work/cmp_USDJPY_2017-03.txt | 809acf3fd4a55b96e2ac96131f7e24a82739fab4cdd612e3e03fdc210f372c4c |
| d12_work/cmp_USDJPY_2019-03.txt | d9a3f29411dd4dbfc1b933c1f4b80c88b70a178ec0f0e06956dbfd59ea5100dd |
| d12_work/cmp_USDJPY_2023-03.txt | 70f0e2acebc0b801dd7498ffc26bc07becf078dd7f4e707dd6765a6ed9883e58 |
| d12_work/cmp_EURJPY_2019-03.txt | 18278b9f21a41a1537eb7ae94f38b1eeb92de3c66a558d5ae4f5c0c11b5c2a73 |
| d12_work/cmp_EURUSD_2019-03.txt | c8efd8fb7a0f433f1f6889209026786c9d86e5007a25ccc590d5908e84824127 |
| d12_work/cmp_GBPJPY_2019-03.txt | 89395fa03d32739928094a1a761c78a90948f444d646e148e8f6289d34df92d9 |
| d12_work/tick_gap_dist.txt | caa81c0488d5ae0e715cf4dae1ae93f2988c9570a3c0160da54b9c1f129e4fd1 |
| d12_work/longest_runs_nylocal.txt | 1fa278ce06a9f2828969b1da53bb43465d7e3f5182bf6d363b47975921247a81 |
| d12_work/longest_runs_est.txt | c5dffc43afb60c4a044f7d6277a35c0fe2df70e2f07eb9d3656d5b60ec06d22b |
| survey_samples/fxcm_v3/USDJPY_m1_2017_w9.csv.gz | 488634ebe46d153249256a00488089c4ce1e8f758874dd5d0183eeae15e081e8 |
| survey_samples/fxcm_v3/USDJPY_m1_2017_w10.csv.gz | bbc30cd35ee107d21abf142abbb91032e0155562c5068df3777125249911df85 |
| survey_samples/fxcm_v3/USDJPY_m1_2017_w11.csv.gz | 1c16b1a208db403d2714a23febe65fdb1c0a3754fef569eccc2f3ed8e7480eed |
| survey_samples/fxcm_v3/USDJPY_m1_2017_w12.csv.gz | 1e962edc944c3d8f408e6aea548ec04ac1daab6e62cbba5176f87d2e2255836a |
| survey_samples/fxcm_v3/USDJPY_m1_2017_w13.csv.gz | a7afa5c817b415cf74af00323dc9eac3df2feaadefe22e6d4d307220d3421142 |
| survey_samples/fxcm_v3/USDJPY_m1_2023_w9.csv.gz | 660c266b93a47fe0f4c3e5de2a4f38e957639951a5eb377fc0ba5a48d8b2e1d6 |
| survey_samples/fxcm_v3/EURUSD_m1_2019_w9.csv.gz | a48078e0282cba504dcb8b6b33ad719a61230278f2a8aa80f7731bf82539b4bd |
| survey_samples/fxcm_v3/EURUSD_m1_2019_w10.csv.gz | f776364ba1c130a80606b5f14b13bcec007716d22eb948e98339a776a6669fd2 |
| survey_samples/fxcm_v3/EURUSD_m1_2019_w11.csv.gz | 3ad14968cc3cccb62c53fc1eec844185a825bec7d7920cbe4768480bc2258a88 |
| survey_samples/fxcm_v3/EURUSD_m1_2019_w12.csv.gz | 50242293489fea4265be6a649e182a2d0adacb2e29a4ee44a5452d8abb7fc6c1 |
| survey_samples/fxcm_v3/EURUSD_m1_2019_w13.csv.gz | 2b7cfc9c0e1413db094362019a8128052dbd06ea254b919a9ec4fa8dcd285f04 |
| survey_samples/fxcm_v3/EURJPY_m1_2019_w9.csv.gz | 16ed0a964a1422b294b9335c0516d59f2bf470558d3fbed4f2c3f5eeccb88ed4 |
| survey_samples/fxcm_v3/EURJPY_m1_2019_w10.csv.gz | f5319e0fec63a57fdee35544f200fe5d69345f239689898a4779b596f9dc39d0 |
| survey_samples/fxcm_v3/EURJPY_m1_2019_w11.csv.gz | e71a7f5e098801302effc2293168c22c7bcc7c2d2fe4611bcd0b3b547d7c5214 |
| survey_samples/fxcm_v3/EURJPY_m1_2019_w12.csv.gz | f18b06b45817d0613c215034d80c7442265e218034ad60fffd64a48f6a99c6f9 |
| survey_samples/fxcm_v3/EURJPY_m1_2019_w13.csv.gz | 6649e4aa9712300ab69ffd955cec723e6b25b3451fb01514375a5c2545523d4c |
| survey_samples/fxcm_v3/GBPJPY_m1_2019_w9.csv.gz | 543fe78c63520d12c91dac894b26ab35d77434e15cbf851becb8d528bd8a052f |
| survey_samples/fxcm_v3/GBPJPY_m1_2019_w10.csv.gz | 1e19cb62476c1da3271b709a148926a96dcaf4fc4b7d1b793bfc511d10e02968 |
| survey_samples/fxcm_v3/GBPJPY_m1_2019_w11.csv.gz | a5ebda727e72846f70730b142a7245614ef8160e8d15663b08638c6bb557de92 |
| survey_samples/fxcm_v3/GBPJPY_m1_2019_w12.csv.gz | dd5ded98e3f97f8f090ff5dfdb013d97b8294685dbb6ad076344d4562e394de2 |
| survey_samples/fxcm_v3/GBPJPY_m1_2019_w13.csv.gz | 1a6fc458c444101d76ef80ca46bca0954d27445ae455e5d84cd1663e7ea3c8c1 |

---

## G. 利用条件の問い合わせの質問文（v0.4。**送っていない**。人間が判断して送る）

人間の指示（2026-10-15、第 3 回）「問い合わせが必要なら、質問文を作成して人間に示してください」に基づく下書き。公開情報で「確認済み」にならなかった点（本文第10.2節）だけを聞く。用途は事実のとおりに書き、「非商用」とは書かない。送るかどうか・どの候補に送るかは人間が決める（外部への問い合わせの送信は人間の判断の後）。

### G-1. OANDA証券（日本語。マイページの問い合わせ窓口など）

> 件名: ティックデータ（マイページの配布 CSV）の利用条件についての確認
>
> 口座を持つ個人です（日本在住）。マイページの「ティックデータのダウンロード」で取得した CSV について、次の点を確認させてください。
>
> 用途: 個人での売買手法の研究とバックテスト、その結果をもとにした将来の自己資金での売買。第三者への再配布はしません。
>
> 1. 上の用途での利用は、利用規約・約款の上で認められますか。該当する規定を教えてください。
> 2. ダウンロードした CSV と、そこから作ったデータ（1 分足・15 分足など）を、手元のパソコン・外部記憶装置に保存し、継続して利用してよいですか。
> 3. 会員ステータスが変わった場合や、口座を解約した場合に、既にダウンロードしたデータとそこから作ったデータを保存・利用し続けてよいですか。
> 4. ダウンロードの回数や量に上限はありますか。プログラムによる自動のダウンロードは認められますか。
> 5. CSV の時刻と月の区切りについて、公式 FAQ（2190・2191）以外に資料があれば教えてください。取得できる最も古い月はいつですか。
> 6. MT5 の標準 API（Python）で、MT5 のサーバーから過去の tick と 1 分足を取得することについて: (a) 取得したデータと、そこから作ったデータの保存・継続利用・解約後の扱いは、上の 1〜3 と同じですか。(b) 取得の回数や量に上限はありますか。(c) USDJPY・EURJPY・EURUSD・GBPJPY・USDCHF・AUDJPY・GBPUSD・AUDUSD について、取得できる最も古い時点（tick と 1 分足それぞれ）はいつですか。（v0.5 で改めた。v0.4 の文は「ティックデータの配布の対象（USDJPY・EURJPY・EURUSD・GBPJPY）以外の通貨ペア（USDCHF・AUDJPY・GBPUSD・AUDUSD）の過去の tick を、他の方法（API・MT5 など）で取得できますか。その条件を教えてください。」）

### G-2. FXCM（英語。送り先は人間が選ぶ。公開の問い合わせ窓口など）

> Subject: Terms of use for the historical candle data in the fxcm/MarketData repository
>
> I am an individual residing in Japan. I would like to confirm the terms for the historical 1-minute candle data published at candledata.fxcorporate.com (GitHub: fxcm/MarketData).
>
> Intended use: my own research and backtesting of trading methods, and possibly future trading with my own funds based on the results. No redistribution to third parties.
>
> 1. Is this use permitted? Which agreement applies (the README refers to the EULA at https://www.fxcm.com/uk/forms/eula/, which redirects to an Adobe Sign form)? Is signing that form required to use the public candle data?
> 2. May I store the downloaded files and data derived from them (e.g., 15-minute bars) locally and keep using them indefinitely?
> 3. Are there limits on automated downloading (number of requests, rate)?
> 4. How are the bid and ask candles constructed? In samples from 2017–2021 we observed 1-minute bars where the ask is below the bid (also for high and low). Is this expected, and is there documentation explaining it?
> 5. Is the candle open always the previous candle's close by design?

### G-3. Dukascopy（英語）

> Subject: Terms of use for historical tick data (Historical Data Export / JForex / S3 archive)
>
> I am an individual residing in Japan, interested in historical FX tick data for 2017–2023 (USD/JPY, USD/CHF, AUD/JPY, EUR/USD, EUR/JPY, GBP/JPY, GBP/USD, AUD/USD).
>
> Intended use: my own research and backtesting of trading methods, and possibly future trading with my own funds based on the results. No redistribution to third parties.
>
> 1. Is this use permitted for data obtained via (a) the Historical Data Export tool on your website, (b) JForex, and (c) the S3 archive (Requester Pays)? Which terms apply to each?
> 2. Is registration or an account (demo or live) required for (a) and (b)?
> 3. May I store the downloaded data and data derived from it locally and keep using it indefinitely, including after closing any account?
> 4. Are there limits on automated downloading? The public HTTP datafeed returned HTTP 429 to us on 2026-10-15; is automated access via that endpoint still supported?
> 5. Are historical files ever revised? If so, how can revisions be identified?

---

## H. FXCM・Dukascopy の利用条件の記録（v0.4。2026-10-15 16:15〜16:21 JST）

### H-1. 調査役の記録（`<scratch>/survey_fxcm_duka_terms.md`、SHA-256 `abb963e61145b53c363cca81241fdedd7933b3383cd2a5d2129e731587581e89`。WebFetch の要約経由の引用を含む）

取得時刻: 2026-10-15 16:15〜16:20 JST（`date` 実測）。使った時間: FXCM 約 3 分、Dukascopy 約 4 分（いずれも上限内。理由: 公式ページで取れる分は取り切り、残りは公式に記述なし／本文が読めないため「未確認」で終了）。
注: WebFetch は小型モデルによる要約経由。引用は要約が返した原文断片であり、法的判断の前に原ページでの再確認が必要。購入・登録・送信・2024 年以降のデータ取得は一切していない。

#### 1. FXCM
##### 1-a 日本在住の個人が使えるか — 条件付き／未確認
- https://www.fxcm.com/markets/trading-services/free-tick-data/ (16:17 JST): 情報は "is not directed at residents of the United States, Canada, United Kingdom, European Union" 及び Hong Kong, Australia, Israel, Japan, India にも向けない旨（要約）。これは「勧誘・案内の対象外」で、公開 CSV の取得を禁じる文ではない。未確認: 日本居住者のダウンロード可否。
- https://github.com/fxcm/MarketData: 利用者資格の記述なし。
##### 1-b 口座・登録の要否 — 条件付き
- GitHub README: candledata の無料サンプルは URL 直取得（既存記録で登録なしを実測済み）。公式に「口座不要」と明記した文は未確認。tick は "Please send email request to premiumdata@fxcm.com."（メール問い合わせ＝未実施）。
##### 1-c 個人の研究・バックテスト・自己売買 — 条件付き
- README: "This is for personal use and abides by our EULA." 個人利用は明記。研究・バックテスト・売買に使えるかの個別記述なし。EULA 本文は未確認。
##### 1-d 元データ・加工後データのローカル保存と継続利用 — 未確認
- README に保存・再配布の記述なし。EULA https://www.fxcm.com/uk/forms/eula/ は本文でなく Adobe Sign 誘導のみ（取得 16:16 JST）。フォームは開いていない（上限内だが本文が静的に出ず未確認）。
##### 1-e 自動取得・量制限・費用 — 条件付き
- 費用: README "FXCM provide several sample data for free."（サンプル位置づけ）。"Please use our FXCM apps if you want the full data."
- 量制限・自動取得の可否: 記述なし（未確認）。範囲は 2017〜2020 の限定セット（検索結果の要約。README 一覧と既存記録の実測が優先）。
##### 1-f 解約後の保存・利用 — 未確認（公開 CSV は口座契約外のため、EULA 次第）
##### bid/ask の作り方・indicative・lowest spreads・逆転
- 確認済み（公式の文）: README "Data points are indicative and based on the lowest spreads available exclusively on Active Trader accounts." → 実際の約定可能価格ではなく「Active Trader 口座の最小スプレッドに基づく気配」。"indicative" の定義自体は README にない。
- bid/ask の生成方法: README に記述なし（未確認）。
- bid > ask（逆転）の説明: README・market-data・free-tick-data ページに記述なし（未確認）。
- 参考（対比）: FXCM Pro の価格フィードページは executable pricing に基づく旨（検索要約、https://www.fxcm.com/pro/market-data/）。無料サンプルとは別商品。

#### 2. Dukascopy
##### 経路 A: 手動 Historical Data Export（https://www.dukascopy.com/swiss/english/marketwatch/historical/ 16:17 JST）
- a 日本在住: 条件付き。利用者資格の記述なし。規約 https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/ §6 は "not aimed at residents of jurisdictions" の規制地域では "you may not access and use the WEBSITE"（日本が該当か明記なし → 未確認）。
- b 口座・ログイン: ページに要件の記述なし（ログイン要否は未確認。実際の画面は未操作）。
- c 個人用途: 条件付き。規約 §3 "solely for your own non-commercial use and benefit"、§4 "licensed to you by DUKASCOPY ONLY for your own personal, non-commercial use"。個人の研究・自己資金売買が「非商用」に当たるかは規約に定義なし（断定しない）。
- d 保存: 条件付き。§3 "Nor may the WEBSITE be stored (in its entirety or in any part) in databases." DB 化の禁止がダウンロード CSV に及ぶかは不明。
- e 自動取得・費用: ページ "It is free through Dukascopy's Historical Data Export tool"、".csv"、tick〜月足。量制限の記述なし。自動化: §3 "scraper," "robot," "bot" は "prior express written consent of DUKASCOPY" が必要 → 手動 UI のスクリプト化は要許可（条件付き）。
- f 解約後: §10 終了後は取得した資料の削除義務（"must delete any materials"、要約）。→ 継続利用は不可の可能性（条件付き）。
- 取得範囲・作業量: 未確認（ページに記述なし）。
##### 経路 B: JForex Historical Data Manager（同ページ ＋ https://www.dukascopy.com/swiss/english/forex/demo-fx-account/）
- 確認済み: "Log in to the demo or live version of JForex" → "Historical Data Manager" から取得。"JForex can give you more custom timeframes"。
- b デモ登録: 確認済み。フォーム項目 First name/Last name/E-mail/Phone＋CAPTCHA、"Demo accounts automatically expire after 14 days."、"By opening demo you express your desire to be contacted by our representatives."。入金不要の明記は無し（無料は "typically free of charge"）。14 日で失効するため、再取得のたびに再登録要否は未確認。
- a 日本居住者のデモ可否: 未確認（デモ資格の国別記述なし。フッターで Belgium, Israel, Russia, Turkey, Canada, UK への勧誘対象外）。
- 履歴データ: https://www.dukascopy.com/wiki/en/development/strategy-api/historical-data/overview-historical-data/ "Historical data returns data from LIVE environment"（デモでも履歴は LIVE と同じ）。FAQ 要約: 保存は tick/1分/1時間/1日のみ、他は合成。
- c〜f: 規約は経路 A と同じサイト規約が適用される想定だが、JForex 利用規約は未読 → 未確認。量制限・自動化（JForex API）は未確認。
##### 経路 C: AWS S3 Requester Pays（https://www.dukascopy.com/wiki/en/development/data-export/ 16:19 JST）
- 確認済み: バケット `cfg-public-proper-wallaby`（eu-west-1）、"SYMBOL/YEAR/MONTH/DAY_ticks.bi5"、月は 0 始まり、"each file represents one full day of tick data per instrument"。前提: "Valid AWS credentials"、"Understanding of requester pays billing model"。全体 "~400 GB, ~20,000,000 files"、EURUSD "Total objects: 26,586" / "Total size: 2.6 GB"。免責: "may be incomplete or outdated."
- a/b: AWS アカウント（認証必須）が要る。AWS 公式 "anonymous access to that bucket is not allowed. You must authenticate all requests"。Dukascopy 側の口座・会員資格は wiki に要件なし。日本居住可否・個人用途・保存・解約後の条件は、wiki に記述なし → c〜f 未確認（規約の適用有無も不明）。
- 1 分足ファイルは wiki に記述なし（tick のみ）。開始年も記述なし。
- 自動取得: AWS CLI 前提の公式手順なので可（条件付き。規約 §3 のロボット禁止との関係は未確認）。
##### 費用見積（8 ペア・2017〜2023）
根拠: S3 GET "$0.0004 per 1,000 requests"（https://aws.amazon.com/s3/pricing/、US 例。eu-west-1 の GET 単価は該当ページに明記なし → 同額と仮定、未確認）。Ireland のインターネット転送 "$0.09 per GB"（同ページ例）。同ページ: "first 100GB per month" の無料枠の言及あり（適用条件は要約では不明）。Dukascopy wiki は転送 $0.02/GB と書くが AWS 公式例 $0.09 と食い違う → 保守的に $0.09 を採用。税: https://aws.amazon.com/tax-help/japan/ "JCT rate has been increased to 10%"、"The prices quoted on the AWS website are JCT exclusive"（個人への扱いは記述なし）。
容量の外挿: EURUSD 26,586 ファイルは 1 日 1 ファイルでは説明できない（約 100 年分になる。旧 hourly ファイル混在の可能性）ため、年数での按分は不可。保守的上限として「8 ペアすべてが EURUSD 全履歴と同じ」（2.6 GB・26,586 ファイル）と置く: 20.8 GB・212,688 リクエスト。（全体平均 20.97 KB/ファイルに対し EURUSD は 100.1 KB で EURUSD は大口ペア。2017〜2023 は全履歴の一部なので実際はこれ以下の見込み。）
- 転送 20.8 GB × $0.09 = $1.87（無料枠が効けば $0）。wiki 単価なら $0.42。
- リクエスト 212.7 千 × $0.0004 = $0.085。
- 合計 上限約 $1.96、JCT 10% 加算で約 $2.2。100 米ドルに対し約 2%。全アーカイブ（400 GB）でも 400×0.09 + 8 = 約 $44、税込み約 $48 で 100 ドル未満。
- 為替・クレジットカード手数料は未調査。AWS アカウント作成にはカード登録が要る（作成はしていない）。
- 1 分足を使う場合の S3 側ファイルは存在が未確認。tick から自前で集約する作業が要る。

#### 主な未確認（優先順）
1. FXCM EULA 本文（保存・再配布・解約後・日本居住者）。2. FXCM の bid/ask 生成と逆転の公式説明（無し）。3. Dukascopy 規約が経路 B/C に及ぶか、日本居住者の可否、手動取得のログイン要否。

### H-2. 設計役が原ページで確かめた文（2026-10-15 16:19〜16:21 JST。ページを保存し、本文の行を取り出した。道具 `html_text.py`）

| ページ（保存したファイルの SHA-256） | 確かめた文 |
|---|---|
| GitHub `fxcm/MarketData` の README（`gh api repos/fxcm/MarketData/readme`） | "Data points are indicative and based on the lowest spreads available exclusively on Active Trader accounts"（46 行目）、"This is for personal use and abides by our [EULA](https://www.fxcm.com/uk/forms/eula/)"（50 行目ほか） |
| `https://www.dukascopy.com/swiss/english/legal-pages/terms-of-use/`（`d61398dea5d37dd6abbf1cee9b1d8fbb787d9bdb22af23c1f6b96deff6703810`） | "you agree to use the WEBSITE solely for your own non-commercial use"、"may the WEBSITE be stored (in its entirety or in any part) in databases for access by"、"\"scraper,\" \"robot,\" \"bot,\" \"spider,\" \"data mining,\" \"computer code,\" or any other"、"The WEBSITE provision is not aimed at residents of jurisdictions from which for whatever"（いずれも行の一部）。照合役の確認（2026-10-15）で、同じ保存ファイルに次の原文がある: "may not be used to construct a database of any kind"、"Nor may the WEBSITE be stored (in its entirety or in any part) in databases for access by you or any third party or to distribute any database WEBSITEs containing all or part of the WEBSITE"、"...without the prior express written consent of DUKASCOPY"、終了のとき "you must discontinue your access and use of the WEBSITE and destroy promptly all materials obtained from the WEBSITE and any copies thereof" |
| `https://www.dukascopy.com/swiss/english/forex/demo-fx-account/`（`558cfdf26e756f780a6ba612860f7c0e5b97fd26b54445eb4726f393e9846610`） | "Demo accounts automatically expire after 14 days"、"By opening demo you express your desire to be contacted by our represen­tatives both from the Swiss headquarters and our worldwide subsi­diaries, branches and/or represen­tative offices."、"Demo trading is typically free of charge." |
| `https://www.fxcm.com/markets/trading-services/free-tick-data/`（`d42947374d04ebf9881a7b238cea749c7c72b8d4005990a6bea37b1c0b0a0757`、5,554 バイト） | 本文が動的に読み込まれ、日本の居住者についての文を取り出せなかった（要約経由のまま。要再確認） |

---

## I. OANDA の利用条件と時刻の資料の記録（v0.4。2026-10-15 16:15〜16:21 JST）

### I-1. 設計役が原ページで確かめた文（ページを保存し、本文の行を取り出した。道具 `html_text.py`、SHA-256 `ddca80750742b54a7453b899382ced83596d1e630fb06c543c052987a9ded01b`）

| ページ（保存したファイルの SHA-256） | 確かめた文 |
|---|---|
| FAQ 2190 `https://help.oanda.jp/oanda/faq/show/2190?site_domain=default`（`345073818cc856ec753234360ee73fec47b16f69fde5468062d195b52d12b196`） | 「MT5サーバー時刻」、「・夏時間（GMT+3適用時）：日本時間との時差は「-6時間」」、「・冬時間（GMT+2適用時）：日本時間との時差は「-7時間」」、「※切替規則：米国サマータイムに準拠いたします。」、「（夏時間開始：3月第2日曜日 / 冬時間移行：11月第1日曜日）」、「なお、弊社のティックデータは日本時間で毎月1日0:00～末日23:59までのデータです。」、「取得データの加工・分析方法、集計ロジック、詳細な技術仕様に関する個別のお問い合わせにつきましては、サポート対象外とさせていただいております。」 |
| FAQ 2191 `https://help.oanda.jp/oanda/faq/show/2191?site_domain=default`（`33de6d4fb7c54262b9cced587757d3a6403f24e92adce5504b45d882349c95fe`） | 「マイページからダウンロードできるデータのZIPファイル名は、「日本時間（JST）」を基準に区切られています。」、「2026.07.31 18:00:00 ～ 2026.08.31 17:59:59」、「日本時間に換算（+6時間）：」、「2026.08.01 00:00:00 ～ 2026.08.31 23:59:59」、「上記のように、CSV単体では前月末日の時刻から開始しているように見えますが、日本時間に換算することで「1日0:00～末日23:59」のデータが収まる仕様です。」 |
| FAQ 965 `https://help.oanda.jp/oanda/faq/show/965?site_domain=default`（`d0d7f1a205bbf6f650c4814381010a91964a4f03b42331ef816b235fd5116f37`） | 「当社に口座をお持ちの方すべて、」「MT5用」「のみ実際に配信した高精度ヒストリカルデータ（ティックデータ）を無料でダウンロードできます。」、「ダウンロードしたデータは、MetaTrader5にインポートして、過去のティックチャートの表示の他、エキスパートアドバイザ (EA) やインディケータのバックテストでご利用いただけます。」、「・FX（2016年9月以降のデータ）」「USDJPY、EURJPY、EURUSD、GBPJPY」 |
| FAQ 1727 `https://help.oanda.jp/oanda/faq/show/1727?site_domain=default`（`c4e40ba94d390da4a381227075372b9f88ea3292db3bee1d77ebcf0f0a9f12da`） | 「会員ステータスがGold以上であること」、「NYサーバーの口座残高が25万以上であること」、「NYサーバープロコースであること」 |

- `https://www.oanda.jp/faq/2190` は HTTP 404（調査役が探した URL）。FAQ は `help.oanda.jp` にある。
- 会員ステータスの表（`https://www.oanda.jp/lab-education/status/`）は、保存したページから列と行の対応を取り出せなかった（「ティックデータのダウンロード」がどのステータスに付くかは未確認）。

### I-2. 調査役の記録（`<scratch>/survey_oanda_terms.md`、SHA-256 `a8b6938cfeb7fd687726bd05b45409995b4d2b738fbcd424098cd51bf1319e75`。取引約款の PDF 以外は WebFetch の要約経由）

取得時刻（JST、date 実測）: 2026-10-15 16:15〜16:18。
注意: 引用の多くは WebFetch（小型モデルの要約経由）。逐語でない場合は「要約経由」と明記。約款 PDF のみ自前で本文抽出して原文を確認（逐語）。
区分: 【確認済み】公式の文で明記 / 【条件付き】条件つきで明記 / 【未確認】公式の文が見つからない
購入・登録・ログイン・問い合わせは未実施。2024 年以降の価格データは未取得。

#### 0. 読んだ URL

- https://www.oanda.jp/faq/2190 → HTTP 404（探した URL。FAQ は help.oanda.jp にある）
- https://help.oanda.jp/oanda/faq/show/2190?site_domain=default（FAQ 2190、取得 16:15 台）
- https://help.oanda.jp/oanda/faq/show/2191?site_domain=default（FAQ 2191）
- https://help.oanda.jp/oanda/faq/show/965?site_domain=default（配布 CSV の対象・無料）
- https://www.oanda.jp/lab-education/status/（会員ステータス）
- https://www.oanda.jp/fxproduct/api と https://help.oanda.jp/oanda/faq/show/1727（API 条件）
- https://developer.oanda.com/rest-live-v20/introduction/
- https://www.oanda.jp/pdf/transaction/fxtrade_manual.pdf（店頭外国為替証拠金取引約款。取得 16:16、PDFKit で全文抽出し原文確認）
- https://www.oanda.jp/lab-education/blog_mt5/basicmt5/tickdata/ と .../historical_data/（解説記事。約款ではない）
- 未取得: REST API 利用規約の本文（トークン発行画面で同意する形式。ログインが要るため読めず）、API 利用規約・情報利用規定の独立ページは検索で見つからず。

#### 1. FAQ 2190 / 2191（時刻と月の区切り）

FAQ 2190（要約経由。逐語でない）:
- 質問: マイページからダウンロードできる MT5 用ヒストリカルティックデータのタイムゾーン。
- CSV 内の日時は「MT5サーバー時刻」。夏時間 GMT+3（日本との差 6 時間）、冬時間 GMT+2（差 7 時間）。切替は米国のサマータイム準拠（3 月第 2 日曜〜11 月第 1 日曜）。
- 「毎月1日0:00から末日23:59まで（日本時間）」のデータ（要約）。
- 加工・分析・集計ロジックの個別問い合わせはサポート対象外。

FAQ 2191（一部は逐語引用として返った）:
- 「マイページからダウンロードできるデータのZIPファイル名は、「日本時間（JST）」を基準に区切られています。」
- 例 ticks_USDJPY-oj5k_2026-08.zip: CSV 内は 2026.07.31 18:00:00 ～ 2026.08.31 17:59:59（夏時間）、+6 時間で JST 2026.08.01 00:00:00 ～ 2026.08.31 23:59:59。
- 含意: CSV 内の生の時刻は前月末日から始まる。月の境界は JST 基準で、CSV 内の時刻はサーバー時刻（UTC ではない）。冬時間は +7 時間の差。
- 注意: 夏時間の例のみ。冬時間の月の CSV 範囲の例は FAQ に無い（未確認）。
- 原文の逐語確認は未了（Web 画面の目視で最終確認が望ましい）。

#### 2. 経路別

##### (1) マイページ「ティックデータのダウンロード」配布 CSV

a. 個人が使えるか: 【確認済み】FAQ 965（要約経由）「当社に口座をお持ちの方すべて、MT5用のみ実際に配信した高精度ヒストリカルデータ（ティックデータ）を無料でダウンロードできます。」法人・金融機関限定の記載なし。
b. 口座・資格: 【条件付き・食い違い】FAQ 965 は「口座をお持ちの方すべて」。一方、会員ステータス表（要約経由）では「ティックデータのダウンロード」に 6 列中 5 列でチェック。どの列（Silver 以上か）かは要約では特定できず、検索結果の要約には「Silver 以上」とあるが一次確認できていない。入金・残高・取引実績の要否は公式の文で見つからず。→ 【未確認】。ステータス表の画面で列の対応を目視要。
c. 戦略研究・バックテスト・将来の自己資金売買: 【条件付き】FAQ 965「MetaTrader 5 にインポートして過去のティックチャート表示や EA・インジケーターのバックテストに使用できる」趣旨（要約経由）。MT5 用のバックテスト用途は明記。MT5 外での研究利用の可否・規約の該当条項は【未確認】。
d. ローカル保存・継続利用・加工後データ: 【未確認】。取引約款にもデータ利用の条項は見つからず。解説記事は ZIP をダウンロードして展開して使うとのみ。再配布・ライセンスの記載は「記載なし」（解説記事・FAQ 965 とも）。
e. 自動取得・取得量・費用: 費用は【確認済み】無料。取得量の制限【未確認】（1 か月単位・銘柄・年月を指定する手動の画面。解説記事「データは1カ月単位」）。自動取得（スクリプト）の可否【未確認】。約款 第9条(16)「当社の認めていないプログラム、ソフトウェア等の使用により、当社のシステムの意図から外れた方法で本サービスを利用」は取引サービスについての条項で、データダウンロードへの適用は不明。
f. 解約後: 【未確認】。約款 第 6 条付近の解約条件列挙に、解約後のデータ保存への言及は見つからず。
g. 通貨ペア・期間: 【確認済み】FAQ 965（要約経由）FX は 2016 年 9 月から USDJPY・EURJPY・EURUSD・GBPJPY。株価指数 CFD は 2020 年 5 月から、XAUUSD は 2022 年 6 月から。USDCHF・AUDJPY・GBPUSD・AUDUSD は無い。
補足: 配布 CSV は東京サーバーで実際に配信された MT5 用ティック（解説記事、要約経由）。

##### (2) v20 REST API（日本の口座）

a. 個人: 【条件付き】fxproduct/api（要約経由）は個人向けと明記する文までは確認できず（トークンは「パーソナルトークン」）。利用ライセンスは「NY サーバープロコースの fxTrade 口座とそのサブ口座」のみ。
b. 条件: 【確認済み】FAQ 1727（要約経由・逐語に近い引用）「会員ステータスがGold以上であること」「NYサーバーの口座残高が25万以上であること」「NYサーバープロコースであること」。fxproduct/api は「25 万円は単一の NY プロコース口座で」と記載（要約経由）。新規口座は開設翌月末まで Gold。条件を満たさないと予告なく停止され得る。個別の例外・期間延長不可。残高が下回ると定期チェックで停止との記載（検索要約）。
c. 用途: 【未確認】API 利用規約本文を読めておらず、データの用途制限は確認できていない。
d. 保存・加工: 【未確認】fxproduct/api は「API で得たデータの利用・保存・再配布には触れていない」（要約）。
e. 自動取得・量・費用: 費用は【確認済み】初期費用・月額とも無料。レート制限: developer.oanda.com の別ページ（検索要約）に 1 秒 120 リクエスト、IP 単位とあるが、今回の WebFetch では確認できず【未確認】。日本の API 案内: 取引履歴は「直近2週間・最大5,000レコード」（要約経由。これは取引履歴で価格ではない）。
f. 解約後: 【未確認】。条件喪失時は API 停止、口座自体は凍結されず fxTrade で建玉管理可（要約経由）。保存データの扱いは記載なし。
g. 通貨ペア・期間: developer.oanda.com「Access historical pricing information dating back to 2005.」（要約経由・英語版の一般説明）。日本の口座で USDCHF・AUDJPY・GBPUSD・AUDUSD が取れ、2005 年まで遡れるかは【未確認】（日本版ページに価格履歴の範囲の記載なし。実測は購入・ログイン不可のため未実施）。
追記: developer.oanda.com の説明ページに個人利用の可否の文は無い（要約）。

##### (3) MT5 のヒストリー（口座のサーバーから）

a. 個人: 【条件付き】MT5 標準 API（Python MetaTrader5 / MQL5）は「本番口座があればステータス不問・無料」（fxproduct/api、要約経由）。サーバー: 東京（TY3）。
b. 条件: 上記のとおり本番口座のみ。デモ口座の可否は【未確認】（MT5 側）。
c. 用途: 【未確認】。解説記事（要約経由）「バックテストの結果が良好でも、将来も同じ成績が得られるとは限りません。」は注意書きのみ。
d. 保存・加工: 【未確認】。
e. 自動取得・量: MT5 の Python/MQL5 での自動取得は API 案内で想定されているが、履歴取得の量制限は公式の文なし【未確認】。解説記事: 「サーバーから取得できるデータの期間や精度は、FX会社によって異なります。」「MT5の初期状態で遡れる期間には限りがある」（具体値なし）。
f. 解約後: 【未確認】。
g. 通貨ペア・期間: 取得可能な銘柄は FX＋金・原油・株価指数 CFD（API 案内、要約経由）。USDCHF・AUDJPY・GBPUSD・AUDUSD の有無と履歴の深さは【未確認】（MT5 の銘柄一覧は口座にログインして見る必要がある）。

##### (4) その他

- OANDA Tick Chart Trader（MT5 インジケーター）からのティック CSV 出力: 検索結果で存在のみ確認。利用条件・取得範囲は未読【未確認】。
- OANDA Global の外部経路（oanda.com の価格履歴等）は日本の口座の対象外か不明。【未確認】。
- OANDA Lab の無料データ提供やヒストリカルデータのページ（lab-education/dictionary/historical_data/）は検索で確認のみ、未読。

#### 3. 取引約款（店頭外国為替証拠金取引約款）の確認結果（逐語・自前抽出）

データの利用・保存・再配布・商用利用を扱う条項: 【未確認】（全文で「商用」「再配信」「著作権」「マーケットデータ」を検索して該当なし。「API」も該当なし）。
関連しうる条項（取引サービスに関するもの。データ取得への適用は不明で、許可・禁止のどちらにも寄せない）:
- 第 9 条（禁止行為）(2)「極めて短時間に機械的に反復して本取引を行う行為」、(3)「取引の如何に関わらず本取引システム又は本取引システムの運用に対して過大に負荷を強いる行為」
- 解約事由 (16)「当社の認めていない プログラム、ソフトウェア等の使用により、当社のシステムの意図から外れた方法で本サービスを利用した場合又は利用しようとする場合」
- 解約事由 (18)「お客様の開設口座のお取引及び全ての残高がなくなった後、相当期間が経過したとき」
- 解約事由 (7)「お客様が国内非居住者となり、居住者に復帰する見込みがなくなったとき」
注: 検索結果の要約は禁止行為を「第8条」としたが、現行 PDF では第 9 条（版の違い。PDF の本文を優先）。デモ口座利用規約（検索要約）には「マーケットデータの加工、または再配信等を当社に無断で行った…場合」に利用停止の条項があるが、本番口座の約款ではなく、本文は未読。商品 CFD 約款（検索要約）に「無断で本取引及び本取引に係る情報を商用利用するなど、個人の取引から逸脱した行為」。いずれも未読の二次情報。

#### 4. 未確認の主な項目（次に確認すべきもの）

1. REST API 利用規約の本文（データの用途・保存・再配布）。
2. 配布 CSV の利用条件（ログイン後の画面に注意書きがある可能性）と、ステータス表で「ティックデータ」が対象の列。
3. FAQ 2190・2191 の原文逐語（画面で目視）。
4. 日本口座の API／MT5 で USDCHF・AUDJPY・GBPUSD・AUDUSD の履歴が取れるか（公式の文なし）。
5. 解約後のデータ保存・継続利用（全経路で公式の文なし）。

## J. OANDA MT5 Python API の経路の記録（v0.5。2026-10-15 16:21〜16:50 ごろ JST）

人間の第 4 回の指示（本文第0.1.5節）に基づく。調査役（読み取りだけ）がページを `<scratch>/d12_pages/mt5/` に保存し、設計役が保存したページの本文から下の文を文字列の検索で照合した（**逐語**）。要約の道具経由で原文を照合できなかったものは「要約経由」と書いた。登録・ログイン・購入・問い合わせ・インストーラーの取得・価格のデータの取得はしていない。調査役の記録は `<scratch>/survey_mt5_api.md`（SHA-256 `4ab99e08a2d90135c7564b2c04a358578d26dd849e7d95cb4716e126198bfe78`）。

### J-1. OANDA証券の公式ページ（逐語）

| ページ | 文 |
|---|---|
| API のページ `https://www.oanda.jp/platform/api`（旧 `https://www.oanda.jp/fxproduct/api`。保存 16:24） | 「（MT5標準APIは口座開設のみでご利用可）」「ご自身の、またはVPS上のWindowsにあるMT5」「本番口座を開設し、マイページからMT5サブアカウントを作成します（会員ステータスは不問）。」「（MT5標準APIも無料で、こちらは口座開設のみで始められます。）」「APIの内容・プログラミング・ネットワーク構築などの技術的なサポートは行っておりません。」デモ口座の案内に「MT5使用可能。」「バックテストも利用可。」 |
| MT5 のページ `https://www.oanda.jp/platform/mt5` | 「MT5口座のご利用には、あらかじめOANDA fxTradeの口座開設をしてください。」「MT5ソフトはMacPCではご利用いただけません。Windows OSでのみ起動可能となっております。」「MT5のチャートは、売り価格（Bid）に基づいて生成されており、買い価格（Offer）はチャート上には表示されません。」 |
| MT5 の接続の解説記事 `https://www.oanda.jp/lab-education/blog_mt5/auto_trading/mt5-claude-code-setup/` | 「デモ口座またはライブ口座に接続されている」（前提の文）、「MT5サブアカウントを作成する」 |
| FAQ 164 `https://help.oanda.jp/oanda/faq/show/164?site_domain=default`（サブアカウントとは何ですか？） | 「東京サーバー/スタンダードプラン FX MT5サブアカウント」 |
| FAQ 1928 `https://help.oanda.jp/oanda/faq/show/1928?site_domain=default`（Python（パイソン）を使ったシステムトレードはできますか？） | 「メタクォーツ公式APIを利用して、PythonからMT5を直接操作できます。」 |
| 取扱通貨ペア（東京サーバー、個人）`https://www.oanda.jp/fx/ty3/retail-lineup` | 一覧に「AUD/JPY」「AUD/USD」「EUR/JPY」「GBP/JPY」「GBP/USD」「USD/CHF」がある（USD/JPY・EUR/USD を含む 8 ペアすべて）。表の「証拠金率（MT5）」の欄に 8 ペアとも値がある（例: GBP/USD 5%、他 4%） |
| 会員ステータス `https://www.oanda.jp/lab-education/status/`（保存 16:21） | 比較表の行「ティックデータのダウンロード」: 「口座未開設の方」は「-」、SILVER（要件「50万米ドル未満」）・GOLD・PLATINUM・DIAMOND・BLACK はすべて ✔（画像の代替文字 "✔" で照合） |

- 見つからなかったもの（**未確認**。許可・禁止のどちらにも寄せない）: MT5 API で取得したデータの利用・保存・加工・解約後の扱い、取得の量・回数の上限、MT5 のサーバーの履歴（tick・足）がいつまで遡れるか、端末での銘柄名の接尾辞。MT5 のマニュアルの PDF は保存したが、本文の検索はしていない。

### J-2. MetaQuotes の公式ページ（逐語）

| ページ | 文 |
|---|---|
| Python の連携の概要 `https://www.mql5.com/en/docs/python_metatrader5` | "MetaTrader package for Python is designed for convenient and fast obtaining of exchange data via interprocessor communication directly from the MetaTrader 5 terminal." |
| `initialize` | "If required, the MetaTrader 5 terminal is launched to establish connection when executing the initialize() call." |
| `copy_ticks_range` `https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py` | "Returns ticks as the numpy array with the named time, bid, ask, last and flags columns." "When creating the 'datetime' object, Python uses the local time zone, while MetaTrader 5 stores tick and bar open time in UTC time zone (without the shift). Therefore, 'datetime' should be created in UTC time for executing functions that use time." tick の履歴の深さ・上限の文は無い |
| `copy_rates_range` `https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py` | "Returns bars as the numpy array with the named time, open, high, low, close, tick_volume, spread and real_volume columns." "MetaTrader 5 terminal provides bars only within a history available to a user on charts. The number of bars available to users is set in the "Max. bars in chart" parameter." |
| PyPI `https://pypi.org/pypi/MetaTrader5/json`（2026-10-15 に設計役が再取得） | 最新 5.0.6231（2026-09-27 公開）。配布物は `metatrader5-5.0.6231-cp36-cp36m-win_amd64.whl` から `cp314-cp314-win_amd64.whl` までの 9 個だけ。platform は "Windows" |
| Mac での端末 `https://www.metatrader5.com/en/terminal/help/start_advanced/install_mac` | "We provide a special installer for the platform on macOS."（Wine の上で動く）。Python の記述は無い |

### J-3. 実測の環境の費用の資料

| 資料 | 内容 | 種類 |
|---|---|---|
| AWS の EC2 の公開の価格表（`https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/Asia%20Pacific%20(Tokyo)/Windows/index.json`、公開日時 2026-10-12T19:39:25Z。設計役が取得） | 東京の Windows の t3.small 0.0456・t3.medium 0.0728・t3.large 0.1364 米ドル／時 | 公式（価格表のデータ） |
| AWS の日本の税の案内（付録 H の AWS の費用と同じ） | 表示価格は消費税 10% を含まない | 公式 |
| Apple のサポート `https://support.apple.com/en-us/102622` | "With Boot Camp, you can install Microsoft Windows 10 on your Intel-based Mac" | 公式（機種の対応は確かめていない） |
| VirtualBox のライセンスの FAQ `https://www.virtualbox.org/wiki/Licensing_FAQ` | 拡張パックは個人・教育用の条件（PUEL）。本体の条件の文は照合していない | 公式（一部） |
| Microsoft の Windows の評価版のページ | 今回は取得できなかった（混雑の表示）。「90 日」は第三者の記事だけ | 未確認 |
| AWS の無料の枠 `https://aws.amazon.com/free/` | "$100 in credits immediately"、さらに最大 100 ドルで 6 か月に最大 200 ドル。Windows の EC2 に使えるかは書かれていない | 公式（Windows への適用は未確認） |
| Parallels・Azure の価格 | ページが JavaScript で描かれ、価格を取得できなかった。Azure の無料のアカウントは "$200 credit to explore Azure for 30 days"（HTML のタグを除いた本文で照合） | 未確認（価格） |

### J-4. 保存したページの SHA-256（`<scratch>/d12_pages/`）

| ファイル | SHA-256 |
|---|---|
| oanda_status.html | 204233728fe23519224bd58b484482a3a44a68652e3b2ee7066ba00a07ac8e5d |
| mt5/o_platform_api.html | c6f9b93a14e3cf850d6e2f6a50749185c12a68047c9ad96b5e0d0293c5cdfc2c |
| mt5/o_mt5.html | 67fca808369f8cf1a26179b373e9721c95c1808e2e92851cf60caafcbcf70aab |
| mt5/o_setup.html | 7853ab33bc903efbf46c7005795ced079a7a431cf9e9b358a1e69aad7f287d65 |
| mt5/faq164.html | 821d80908489149e191bc3a62dbfed96169e0ef043fd79ac2ec6b7e9693c20c0 |
| mt5/faq1928.html | 277db6847a3909ce6d0ba70eea82fa9664237ff360f77a24ce9e708fa05eb2e6 |
| mt5/ty3_retail.html | a60eb20fea967c8f5b2fdf7cf12991d2b8067ede849d1c71fff63657f9e3e55c |
| mt5/py_main.html | 23f5b2affb3b6e9d35221c86863a7aae3ea6d15f4296b5ae923c787f1995f121 |
| mt5/initialize.html | 979e07bd7d2ffba382d27ae94ab646a87f4a6eb2f54390f9164c7b464dc0cc72 |
| mt5/copy_ticks_range.html | a5917096817df73b4ca6d6944f0777a0d0c8a221ba4eb4d52564d2261922353d |
| mt5/copy_rates_range.html | 01cbbaccbb12fb61345ce15ed7246615d97276211d9f6a386abaedf877d3ce1b |
| mt5/pypi_files.html | 328aa8e143df26929c3118aa2a78275b03fd1136bafd1997ee918e4ac7220520 |
| mt5/mq_mac.html | f9ac5bc5cf4d537d389ca337c86fa5a70de50b53b2897614a18c768bb24c10e1 |
| mt5/bootcamp.html | 0dfa5d5f5cee4fef1498a312595b62fad94a935bbf46c611f598517227a05293 |
| mt5/vbox.html | 02563e1fcebacf1917e17e9a2d51dd33365c05cfb6f78099e9fb4820aa39e3ff |
| mt5/aws_ec2_win_tokyo.json | e2b92d16f0ea7638613bced2ae524672f344c7009cdbd913dc1e424d8d846cc0 |
| mt5/awsfree.html | 72882acfecec93739d603bcd32f6c0e71aecc141ba905101367d414ac3c93c46 |
| mt5/azure.html | 678bc19db0f8d194015736dbb78fd18d2fd3b342539b2ae4a59bded8f2c59e56 |

### J-5. AWS 上の Windows での試験の費用の資料（v0.6。設計役が 2026-10-15 16:26〜17:06 JST に取得。AWS の登録・ログインはしていない）

| 資料 | 内容（原文または価格表の値） | 保存したファイルと SHA-256（`<scratch>/d12_pages/`） |
|---|---|---|
| EC2 の価格表（東京・Windows・オンデマンド）`https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/Asia%20Pacific%20(Tokyo)/Windows/index.json`（"hawkFilePublicationDate": "2026-10-12T19:39:25Z"） | t3.small: "price": "0.0456000000"、"Memory": "2 GiB"、"vCPU": "2"。t3.medium: "price": "0.0728000000"、"Memory": "4 GiB"、"vCPU": "2"。両方 "License Model": "No License required"、"Operating System": "Windows" | mt5/aws_ec2_win_tokyo.json `e2b92d16f0ea7638613bced2ae524672f344c7009cdbd913dc1e424d8d846cc0` |
| EBS の価格表 `https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ebs.json`（同 2026-10-12T19:39:25Z） | Asia Pacific (Tokyo) "Storage General Purpose gp3 GB Mo": "0.0960000000"。"Storage Snapshot Amazon S3 GB Mo": "0.0500000000"（作らない） | aws/ebs.json `8c732ea985c13261bdadc051d009ad2e572977d3cadea163d5d7e65d9b5aacdd` |
| データ転送の価格表 `https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/datatransfer/USD/current/datatransfer.json`（"hawkFilePublicationDate": "2026-09-16T13:22:08Z"） | Asia Pacific (Tokyo) "DataTransfer External Outbound Next 10 TB": "0.1140000000"。"DataTransfer External Inbound": "0.0000000000" | aws/datatransfer.json `f88a4f928a40533f3651f199c7c2ec35aca2bb883856481627b89a9af039d6d3` |
| EC2 のオンデマンドの料金ページ `https://aws.amazon.com/ec2/pricing/on-demand/` | "Each partial instance-hour consumed will be billed per-second for Linux, Windows, ..."、"For T2 and T3 instances in Unlimited mode, CPU Credits are charged at: $0.05 per vCPU-Hour for Linux, RHEL and SLES, and $0.096 per vCPU-Hour for Windows and Windows with SQL Web"、"AWS customers receive 100GB of data transfer out to the internet free each month, aggregated across all AWS Services and Regions (except China and GovCloud)." | mt5/aws_win.html `5a36616aa72088b2403b670955ab613cd8adbacd5b79e8723b942cb9dca03994` |
| EBS の料金ページ `https://aws.amazon.com/ebs/pricing/` | "Volume storage for all EBS volume types is charged by the amount of GB you provision per month until you release the storage."、"Provisioned storage, provisioned IOPS, and provisioned throughput for gp3 volumes will be billed in per-second increments, with a 60-second minimum."（例は "in a 30-day month"） | aws/ebs_pricing.html `9f9e4e23ddff4a2ad00ab57f48dfb3dff8acd6fd29762b2d4e65e1ef112ca638` |
| VPC の料金ページ `https://aws.amazon.com/vpc/pricing/` | "Hourly charge for In-use Public IPv4 Address $0.005"、"Hourly charge for Idle Public IPv4 Address $0.005" | aws/vpc_pricing.html `8f3cea1c8589dcb28a3fbc937a35160bd2bd00b75dcb6a870a891663c2d6c7f5` |
| AWS の日本の税の案内（付録 H の AWS の費用と同じ） | 表示価格は消費税 10% を含まない | — |

- 価格表のファイルは 3 つとも gzip で圧縮された JSON。値は設計役が Python で取り出した。
- 確かめていないもの: Windows Server 2022 の AMI の既定のディスクの大きさ、MT5 の端末の必要メモリ、Mac の RDP のアプリの名前と入手先、カードの為替の手数料。

## K. AWS 試験の作業記録（人間の第 8 回の分担。2026-10-15〜。作業役）

人間の第 8 回の依頼（2026-10-15）で、D12 §11.5.3 の 35 手順のうち、AWS の操作（起動・準備・試験・回収・削除）は作業役が AWS CLI と SSM（AWS Systems Manager。インスタンスの中でコマンドを実行する AWS の機能）で行い、人間は「人間にしかできない箇所」だけを行う。人間がやるのは K-1（事前の作業）と K-2（試験の途中の作業）。作業の記録は K-3、費用は K-4。スクリプトは `tools/survey/aws_mt5_probe/`（使い方は同じ場所の `README.md`）。

**守ること**: 課金の起きる操作は、人間の「起動してよい」を司令塔が伝えるまで行わない。AWS CLI はプロファイル `odyssey-mt5-probe`・東京リージョンだけを使う。認証情報・パスワードは、チャット・ファイル・PR に書かない。2024 年以降の価格は要求しない。チャートを開かない。端末が自動で保存した最近の履歴は読まない。

### K-1. 人間の事前の作業（上から順に。1 手順 1 行。〔確認〕が満たせなければ先に進まない）

1. AWS のアカウントを作る（メール・支払いのカード・電話の確認）。〔確認〕ルートユーザーに多要素認証を付けた。
2. コンソールの右上のリージョンを「アジアパシフィック（東京）」にする。〔確認〕画面に ap-northeast-1 と出る。
3. 「Billing and Cost Management」→「Budgets」で月 5 米ドルのコストの予算を作り、実績 80% と予測 100% でメールの通知を付ける。〔確認〕通知の宛先が自分のメール。**この通知は支出を止めない**（止めるのは、Windows の中の自動の停止と、作業役の 6 時間での終了）。
4. 「IAM」→「ポリシー」→「ポリシーの作成」→「JSON」に、リポジトリの `tools/survey/aws_mt5_probe/iam_policy_operator.json` の中身を貼り、名前を `odyssey-mt5-probe-operator-policy` にして作る。〔確認〕エラーなしで作れた（東京リージョンだけ・t3.medium だけ・タグ `Project=odyssey-mt5-probe` の付いたものだけを操作できる権限）。
5. 「IAM」→「ロール」→「ロールを作成」→ 信頼されたエンティティ「AWS のサービス」・ユースケース「EC2」→ 許可 `AmazonSSMManagedInstanceCore` → 名前 `odyssey-mt5-probe-ssm-role` で作る。〔確認〕ロールの画面に「インスタンスプロファイル ARN」があり、末尾が同じ名前（コンソールで EC2 用に作ると、同じ名前のインスタンスプロファイルが自動で作られる）。
6. 「IAM」→「ユーザー」→「ユーザーの作成」→ 名前 `odyssey-mt5-probe-operator`、コンソールへのアクセスは付けない →「ポリシーを直接アタッチ」で手順 4 のポリシーだけを付ける。〔確認〕付いている許可が 1 つだけ。
7. そのユーザーの「セキュリティ認証情報」→「アクセスキーを作成」（用途: コマンドラインインターフェイス）。〔確認〕画面を閉じる前に手順 8 を済ませる。キーはチャット・メモ・ファイルに書かない。
8. Mac のターミナルで `aws configure --profile odyssey-mt5-probe` を実行し、2 つのキー、リージョン `ap-northeast-1`、出力 `json` を入れる。〔確認〕`~/.aws/config` に `[profile odyssey-mt5-probe]` と `region = ap-northeast-1` がある（キーは `~/.aws/credentials` に入る。中身を誰にも見せない）。
9. `aws sts get-caller-identity --profile odyssey-mt5-probe --region ap-northeast-1` を実行する。〔確認〕`Arn` の末尾が `user/odyssey-mt5-probe-operator`（出力は貼らず、「確認した」とだけ伝える）。
10. OANDA のマイページの「口座管理」→「MT5サブアカウントの作成」でサブアカウントを作る（まだ無い場合）。〔確認〕メールで、ログインの番号・サーバー名・MT5 のダウンロードのリンクが届いた。パスワードは人間だけが持つ。
11. 司令塔に「事前の作業が済んだ」と、MT5 のサーバー名だけを伝える（ログインの番号・パスワードは伝えない）。〔確認〕伝えた。
12. 試験の日時を決め、司令塔に「起動してよい」と伝える。起動から約 1 時間後に RDP の作業（K-2）があり、全体は 2〜3 時間。〔確認〕その時間、連絡に応じられる。

### K-2. 試験の途中で人間がする作業（作業役の合図の後）

1. 作業役が伝えるコマンド（`aws ec2 get-password-data ... --priv-launch-key ~/.ssh/odyssey-mt5-probe-<日付>.pem ...`）を Mac で実行し、Windows の管理者のパスワードを出す。〔確認〕表示された（チャットに書かない）。
2. Mac の Microsoft の RDP のアプリ（Windows App）で、作業役が伝えた IP アドレス、ユーザー名 `Administrator`、手順 1 のパスワードで接続する。〔確認〕Windows のデスクトップが出た。
3. Windows のブラウザで、K-1 手順 10 のメールの MT5 のダウンロードのリンク（またはマイページのリンク）を開き、OANDA の MT5 のインストーラーを入れる（作業役が無人で入れた場合は不要）。〔確認〕MT5 の端末が起動する。
4. 起動のときのデモ口座の申請の画面は閉じ、「ファイル」→「取引口座にログイン」でサブアカウントにログインする（パスワードは人間が入れる）。〔確認〕端末の右下に接続の表示がある。
5. 開いているチャートの窓をすべて閉じ、「気配値表示」の窓も閉じる。端末のデータのフォルダは開かない。〔確認〕チャートの窓が 0。
6. RDP の窓を閉じて「切断」にする（Windows の「サインアウト」はしない。MT5 の端末も閉じない）。〔確認〕司令塔に「ログインした」と伝えた。
7. 作業役の USDJPY の照合の報告を受け、「成功。他通貨へ」か「中止」を司令塔に伝える。〔確認〕伝えた。
8. 翌日、「Billing」の請求書と Cost Explorer で、EC2 の料金が止まり、合計が見積（K-4）の範囲であることを確かめ、司令塔に伝える。〔確認〕新しい料金が増えていない。
9. 試験が全部終わり不要になったら、人間が作ったものを消す: IAM ユーザーのアクセスキー（無効化→削除）、IAM ユーザー、ポリシー、ロール `odyssey-mt5-probe-ssm-role`。〔確認〕どれも一覧に無い。

- 人間の手順は K-1 が 12、K-2 が 9（うち 8・9 は試験の後）。D12 §11.5.3 の 35 手順のうち、残りは作業役のスクリプトが行う。
- 時間の見込み（人間）: K-1 は AWS の登録の審査の待ちを除いて約 40〜60 分、K-2 の 1〜6 は約 15〜20 分。

### K-3. 作業の記録（時刻は JST。秘密情報は書かない）

| 時刻 | 作業 | 結果 |
|---|---|---|
| 2026-10-15 17:55〜18:00 | D12 §11.4・§11.5 と `tools/survey/mt5_api_probe.py` を読んだ | — |
| 18:00 | `aws --version`、`aws configure list-profiles` | AWS CLI 2.34.45。プロファイル `odyssey-mt5-probe` は**まだ無い**（K-1 の前）。AWS への呼び出しは 0 回 |
| 18:01〜18:03 | OANDA の MT5 の端末のインストーラーの入手元を確認（`https://www.oanda.jp/platform/mt5`、`https://www.oanda.jp/lab-education/blog_mt5/basicmt5/start/`、`https://www.oanda.jp/lab-education/blog_mt5/auto_trading/mt5-claude-code-setup/` を `curl` で取得） | 公開の URL は無い。FAQ「MT5を再度ダウンロードしたい」の答え「本番またはデモログインページへログイン後にダウンロードリンクを用意しています。」、サブアカウントの作成について「メール内のMT5ダウンロードリンクよりMT5をダウンロードしてください。」→ 既定では人間が RDP の中で入れる（K-2 手順 3） |
| 18:04 | Python 3.12.10 の Windows のインストーラーを python.org から取得し SHA-256 を計算。PyPI の MetaTrader5 の版を確認 | `67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb`（3.12.11 の同じ名前のファイルは 404）。MetaTrader5 最新 5.0.6231、cp312 の wheel `b94b1f8f52087ae36dfac5f352a96f5d04fe308704d9515f82cf5d1470bc31d4` |
| 18:05〜18:15 | `tools/survey/aws_mt5_probe/` のスクリプトと IAM のポリシーを作成。`bash -n`・shellcheck（`uvx --from shellcheck-py`）を通した | 指摘 0 件 |
| 18:09〜18:10 | 偽の `aws` コマンド（呼び出しごとにプロファイルとリージョンを確かめ、決めた値を返す。AWS には接続しない）で `00`→`10`→`20`→`30`→`40`→`90` を通して実行 | 全 104 回の呼び出しが `--profile odyssey-mt5-probe --region ap-northeast-1` 付き（`aws configure list-profiles` を除く）。分けて受け取った zip の SHA-256 が一致し、展開したファイルが元と同じ。段の名前以外の引数（例 `2024-01`）は拒否 |
| 18:15ごろ（司令塔経由） | 人間の報告（原文）「1,2は完了しました。」 | AWS のアカウントと支払いの方法・Budgets の 5 ドルの通知（K-1 の 1〜3 に当たる）と、OANDA の MT5 のサブアカウントの作成（K-1 の 10）が済んだ。IAM（K-1 の 4〜9）はまだ |
| 19:32 | 人間の報告（原文）「事前の作業が済みました。MT5のサーバ名はOANDA-Japan MT5 Liveです。」「起動してよい（今から 2〜3 時間対応できる）」 | K-1 の 4〜12 が済んだ |
| 19:32 | `00_check.sh` | 呼び出し元は `user/odyssey-mt5-probe-operator`。AMI `ami-0922556670ef93267`（`Windows_Server-2022-English-Full-Base-2026.09.17`、既定のディスク 30 GB）。SSM 用のロールあり。試験のタグのもの・スナップショット・自分の AMI・Elastic IP はすべて 0。起動とセキュリティグループの作成の権限は `--dry-run` で確認。**Budgets の予算は 0 件**（応答が空。権限のエラーではない）→ 起動せずに司令塔に報告 |
| 19:40ごろ | 人間の決定（原文）「(b) 予算が無いまま起動を許可する」 | `00_check.sh` の Budgets の表示を「0 件（警告）」に直し、再実行して表示を確認 |
| 19:46:52 | `10_launch.sh --execute` | **起動**: インスタンス `i-0753059f8955751ef`（t3.medium、ap-northeast-1d）、セキュリティグループ `sg-09fae0bb4e3bedf1b`（RDP を 126.221.39.9/32 からだけ）、キーペア `odyssey-mt5-probe-2026-10-15`（秘密鍵は Mac の `~/.ssh/`）。IMDSv2 必須・ディスクの終了時の削除・CPU クレジット標準を describe で確認。**作業役が必ず終了する時刻 2026-10-16 01:46:52 JST**（起動 + 6 時間）。Windows の中の自動の停止は起動 + 5 時間 30 分 |
| 19:47 | `95_deadline_guard.sh` を Mac で裏で起動 | 01:46:52 JST に `90_teardown.sh --execute` を実行する見張り |
| 19:47〜19:56 | `20_prepare.sh --execute`（1 回目） | SSM の接続まで約 1 分。Python 3.12.10（SHA-256 一致）・MetaTrader5 5.0.6231（numpy 2.5.4）を入れ、import を確認。**道具の転送が SHA-256 の不一致で止まった**: 分けて送る base64 の最後の行（改行で終わらない）を読み落とした（18,000 / 18,515 バイト）。`read` の最後の行の扱いを直した |
| 19:56〜19:58 | `20_prepare.sh --execute`（2 回目。Python は導入済みを検出して飛ばす） | 道具の SHA-256 `6edb8b3d2d9fe077abfdcb6cc32db76ec8331c8fc8915b1ca7b730eecce9f536` が Mac と一致。`--help` は Windows の文字コード（cp1252）で日本語を書けずに失敗 → `PYTHONUTF8=1` で動くことを確認し、`20_prepare.sh`・`30_run_probe.sh` に追加 |
| 19:58 | 人間に RDP の接続先とパスワードの出し方を伝えた（パスワードは書かない） | 人間の「ログインした」を待つ |
| 20:45ごろ（司令塔経由） | 人間が RDP で MT5 にログイン（1 回「Invalid account」の後に成功。窓のタイトル「OANDA-Japan MT5 Live - Hedge」、接続中、チャート 0、気配値表示とナビゲーターは閉じた）。RDP は切断（サインアウトなし） | — |
| 20:51〜20:52 | `30_run_probe.sh usdjpy-time --execute` | タスクの結果 0。端末: build 6264（2026-10-14）、`maxbars` 100000、接続中。銘柄名は **`USDJPY.cl`**（接尾辞 `.cl`、候補は 1 つ）。tick: 2019-03-05・2019-03-19 の 00:00〜02:00 UTC とも **0 件**（5 秒後の再試行も 0、`last_error` は (1, 'Success')）。1 分足: 両区間とも **1 件で、時刻は 2026-07-10T01:30:00Z（要求の区間の外）** |
| 20:52〜20:53 | `40_collect.sh usdjpy-time --execute`（1 回目は Windows の出力の改行 CR のため SHA-256 の比較で止まった。CR を除いて再実行） | zip `775dbd148b525c9bda9b6c5eddea9ef544f1947ac8dd8f831ab66d0f91750645`。`summary_usdjpy-time.json` `5de3a98fa1df5c36e598d2cb093ce67a619a252473a3a2ac87671ece9f704b02`、`run.log` `535ef4b2b1286717480e32dd65ae6fe2536e6f1d37e3fa8a8f21c57ea9672b03`、1 分足の CSV 2 つ（同一）`6622e746a5d474a0e18dd70d36af208621d0f4aa170dbb4d48190bec00166377`。**1 分足の CSV は 2024 年以降（2026-07-10）の 1 本を含む**。価格の値は見ていない・書いていない。扱いは人間の判断待ち |
| 20:53 | 配布 CSV との照合 | API の tick が 0 件のため、照合できない（一致の件数は数えていない） |
| 20:54ごろ（司令塔経由） | 人間の決定（原文）「削除する（推奨）」「(1) 端末の足数の上限を Unlimited にして 1 回だけ再試行（推奨）」 | — |
| 20:55 | 区間の外の 1 分足（1 本、2026-07-10 01:30 UTC。**価格は未閲覧**）を含むファイルを削除。Mac: `USDJPY_2019-03-winter_m1.csv`・`USDJPY_2019-03-summer_m1.csv`（どちらも `6622e746a5d474a0e18dd70d36af208621d0f4aa170dbb4d48190bec00166377`）、`out_usdjpy-time.zip`（`775dbd148b525c9bda9b6c5eddea9ef544f1947ac8dd8f831ab66d0f91750645`）。Windows: `C:\probe\out\usdjpy-time\` の同じ CSV 2 つと `C:\probe\out_usdjpy-time.zip`（同じハッシュを確認してから削除） | 要約の JSON と実行のログは残した（価格の欄が無いことを確認）。1 回目の結果は Mac・Windows とも `usdjpy-time_try1` に名前を変えた。`sha256sums.txt` から削除したファイルの行を除いた |
| 20:55〜20:58 | 道具を直した: 返った行のうち要求の区間の外のものは保存せず、件数と最小・最大の時刻（`out_of_window_*`）だけを要約に書く。区間の中が 0 行なら再試行する。単体テスト 2 件を追加 | pytest（道具のテスト 14 件・アーキテクチャのテスト）・ruff・mypy を通過。新しい道具の SHA-256 `3e2c25f969bf2b6747dd69f6bd817d36a41879abba552c1fe614f85f0a91a24d` を `20_prepare.sh` で Windows に置き、一致を確認 |
| 21:00ごろ（司令塔経由） | 人間の報告（原文）「設定して切断しました」（Max bars in chart を Unlimited にして MT5 を再起動、接続中、チャート 0） | 端末の `maxbars` は 100000000 と記録された |
| 21:02〜21:04 | `30_run_probe.sh usdjpy-time --execute`（再試行。1 回だけ）→ `40_collect.sh usdjpy-time --execute` | tick: 両区間とも **0 件**（再試行も 0、`last_error` (1, 'Success')）。1 分足: 両区間とも **120 件**（00:00〜01:59 UTC として読んだ値）。区間の外は各 1 件（02:00 UTC。区間の終わりちょうどの足で、保存していない）。zip `f28b3f1c88a05c833fa5e28723993b6ad5df1d72aa2135c7e31a973a0cc5e899`、要約 `1e7de5c32b515010e17b4549732732079f978aa97c05992598049cd9c6c0f817`、1 分足 winter `3998fab1f81ab5a8c62eb160a804549ab64e4a2a38c221998d707a419b2dbc88`・summer `c27cbf37115e9c083615855a53bad5db544be10d6b0a14a51b52eea5e9c121d5`、ログ `535ef4b2b1286717480e32dd65ae6fe2536e6f1d37e3fa8a8f21c57ea9672b03` |
| 21:05〜21:07 | 1 分足の照合（参考。D12 §11.4 段 1 の 4）: API の 1 分足と、配布 CSV の bid の tick を UTC に直して作った 1 分足を、ずれ −4〜+4 時間で突き合わせ（道具 `<scratch>/d12_scripts/compare_m1.py` `ad8bc79e…`・`compare_m1_diff.py` `482b295e…`、結果 `352eb8a7…`・`2a9cf957…`。価格の水準は出していない） | 始値・高値・安値・終値の完全一致は、どのずれでも 120 本中 0〜1 本（**並びは一致しない**）。終値の差の中央値は**冬・夏とも −2 時間のずれで最小**（冬 0.4 銭、夏 0.5 銭。0 時間では冬 10.9 銭・夏 9.1 銭、−3 時間では冬 1.1 銭・夏 1.7 銭）。読み: API の 1 分足の時刻は、公式の仕様（UTC）より、**冬も夏も約 2 時間進んでいる見込み**。ただし完全一致しないので、配布 CSV と同じ配信とは言えない。tick が 0 件のため、tick の照合はできない。判断は人間に返す |
| 21:55ごろ（司令塔経由） | 人間の決定（原文）「（2）を選びます。追加費用の見積もりを確認し、60GBへ拡張して続けてください。今回は60GBを上限とし、1通貨ずつ容量を監視しながら、1分足の取得可能範囲を確認してください。MT5が要求区間以外に同期する期間・データ種類も記録してください。2024年以降の自動同期分は研究に取り込まない従来の条件を維持します。まず1分足の確認を優先し、tickの追加試験は必要性を示してからにしてください。全量取得や追加拡張は行わず、試験終了後は結果を保存してAWSの後片付けまで行ってください。」 | — |
| 21:58 | ディスクの拡張の追加費用の見積 | gp3 の増分 30 GB × 0.096 米ドル／GB・月 ÷ 720 時間 × 残りの上限 4 時間（01:46:52 JST の終了まで）= 0.016、税込み **約 0.018 米ドル**。上限の見積（K-4）約 2.45 + 0.02 = 約 2.47 で、総額 5 米ドルの目安に収まる |
| 21:59 | `aws ec2 modify-volume --dry-run`（ボリューム `vol-0e7392b808dc5954a`、gp3 30 GB、試験のタグ付き） | **権限が無い**（UnauthorizedOperation: `ec2:ModifyVolume`）。IAM のポリシーの JSON（`iam_policy_operator.json`）に、試験のタグのボリュームだけの `ec2:ModifyVolume` と、読み取りの `ec2:DescribeVolumesModifications` を足した。人間がコンソールでポリシーを更新するまで拡張しない |
| 21:59〜22:02 | 道具に、ディスクの空きが 3 GB を切ったら次の要求を出さずに止まる仕組み、1 分足だけを 1 通貨ずつ新しい年から要求する段 `m1-<通貨>`、各要求の前後の端末のデータのフォルダ（`bases/<サーバー>/<種類>/<銘柄>/`）のファイルの名前・大きさ・更新時刻だけの記録を足した（単体テスト 3 件） | pytest・ruff・mypy・shellcheck を通過。道具の SHA-256 `01aa5360a321e3c0c667772fb34e2f85d7c0d451bf5ba7d191e51f31efccb07c` を Windows に置き、一致を確認 |
| 22:08〜22:09 | 人間の報告（原文）「権限を更新した」を受け、ボリュームを 30→60 GB に広げ、Windows の C の区画を広げた | 使用 26.54 GB・空き 33.46 GB（K-6.3） |
| 22:09〜22:14 | `30_run_probe.sh m1-<通貨> --execute` と `40_collect.sh` を 8 通貨で順に | 結果は K-6.3、端末の取り寄せは K-7 |
| 22:15 | 端末のデータのフォルダのファイルの名前・大きさ・更新時刻の一覧（中身は開いていない） | K-7 |
| 22:16:46 | `90_teardown.sh --execute` | インスタンス `i-0753059f8955751ef` を**終了**（13:16:46 UTC）。セキュリティグループ `sg-09fae0bb4e3bedf1b`・キーペア `odyssey-mt5-probe-2026-10-15`・Mac の秘密鍵の .pem を削除。**残りの確認（東京）: 試験のインスタンス（終了済みを除く）0、試験のボリューム 0、使われていないボリューム 0、自分のスナップショット 0、自分の AMI 0、Elastic IP 0、試験のネットワークインターフェース 0、試験のセキュリティグループ 0、試験のキーペア 0**。人間が作った IAM のロール・インスタンスプロファイル・ユーザー・アクセスキーは消していない |
| 22:17:21 | 6 時間の見張り（`95_deadline_guard.sh`）が、状態のファイルが無いことを見て終了 | — |

- 段階 A では AWS への呼び出しも課金も無い。段階 B（起動）は K-1 の後、司令塔の「起動してよい」を待つ。

### K-4. 費用の再見積（米ドル、消費税 10% 込み。単価は D12 §11.5.2 と同じ AWS の公開の価格表）

| 場合 | 前提 | 金額 |
|---|---|---|
| 想定 | 3 時間、gp3 30 GB、送信 1 GB、CPU クレジット標準 | 約 0.40 |
| 上限 | 6 時間（作業役が必ず終了）、50 GB、送信 5 GB、CPU クレジットを標準にし損ねた場合 | 約 2.45 |
| 止め忘れ | 1 時間あたり（標準のクレジット、30 GB） | 約 0.090（約 54 時間で 5 ドルを超える） |

- D12 §11.5.2 の見積と同じ。スクリプトは CPU クレジットを標準に固定し（`--credit-specification CpuCredits=standard`）、6 時間を超えると終了以外を止める。
- SSM のコマンドの実行（Run Command）は追加の料金が無い（作業役の知識。料金のページとの照合はしていない）。結果の回収は SSM の API の応答で、量は数 MB の見込み。
- 実績は Cost Explorer に翌日反映されるので、終了の後に「使った時間 × 単価」の概算を K-3 に書き、翌日の確認を人間に依頼する（K-2 手順 8）。

**実績の概算（2026-10-15 22:17 JST。Cost Explorer の反映は翌日）**

| 項目 | 計算 | 金額（米ドル） |
|---|---|---|
| インスタンス（t3.medium、Windows 込み） | 2.50 時間（19:46:52〜22:16:46 JST）× 0.0728 | 0.182 |
| ディスク（gp3） | 30 GB × 2.50 時間 + 増分 30 GB × 約 0.15 時間（22:08〜22:17）、0.096／GB・月 ÷ 720 | 0.011 |
| パブリック IPv4 | 2.50 時間 × 0.005 | 0.013 |
| CPU のクレジット | 標準（describe で確認）なので 0 | 0 |
| 送信 | RDP の画面と SSM の応答。量は測っていないが 0.1 GB 以下の見込み × 0.114 | 0.011 以下 |
| 受信（MT5 の履歴の取り寄せ 約 1.5 GB、Python など） | 無料 | 0 |
| 小計 / 消費税 10% / **合計** | | 0.217 / 0.022 / **約 0.24** |

- 人間の目安（総額 5 米ドル）と上限の見積（約 2.47）に収まる。翌日、人間が請求書と Cost Explorer で EC2・EBS・VPC（IPv4）の料金が止まっていることを確かめる（K-2 手順 8）。

### K-5. API の 1 分足の中身と、時刻のずれ・CSV との差の整理（2026-10-15 21:14〜21:22 JST。人間の第 9 回の指示による。作業役）

使ったデータ: K-3 の再試行で取った USDJPY（端末の銘柄名 `USDJPY.cl`）の 1 分足、2019-03-05（米国の冬時間）と 2019-03-19（米国の夏時間。米国の切り替えは 2019-03-10）の 00:00〜02:00（API の時刻の値）各 120 本。比べる相手は配布 CSV `ticks_USDJPY-oj5k_2019-03.csv` の tick（FAQ 2190 の規則で UTC に直した）。**価格の水準は書かない**（差・件数・相関・スプレッドの値だけ）。道具と出力は `<scratch>/d12_scripts/`（`analyze_m1.py` `1f78d5da4a2e2b23b7fc90d7c60b2a9f07b7a8b2be98d939360fdac7aec863a2`、`signed_diff.py` `fdf29eefe7bab6c9da00ac98a57bd3b7067d55bcfcce61386af3c1946bb6ffb6`）、`<scratch>/analyze_m1_result.json` `8fee923bcec060b630ec3fcbe3a8d6538b3b4879cdd7fd9ea6a1c7bcd59fe252`、`<scratch>/signed_diff_result.json` `4a4cd75ecd5c543758e438c0a3be83eb27fa1710c12489280911c544a929247e`。公式のページは `<scratch>/d12_pages/mt5b/` に保存（`mqlrates.html` `083da11289d1229de3c117c14ec43ad72062502512495c70c619b773ea5e62ee`、`charts.html` `89bedad8553c144fc097bfe0602fd2b44cbe034a3784f9273175a20c380e2529`、`copyrates.html` `e4cf6ed6e5b262ac56dc25dbca8916641d5ca3a3d49b0b9e9b000621a77413fd`）。

#### K-5.1 1 分足 1 本の項目

| 列 | 公式の説明（逐語） | 実データ（2019-03、各 120 本） |
|---|---|---|
| time | 「Period start time」（MqlRates `https://www.mql5.com/en/docs/constants/structures/mqlrates`）。Python では "MetaTrader 5 stores tick and bar open time in UTC time zone (without the shift)" "Data received from the MetaTrader 5 terminal has UTC time."（`copy_rates_range` `https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py`） | 足の始めの時刻。ただし値は UTC より約 2 時間進んでいる（K-5.2） |
| open・high・low・close | 「Open price」「The highest price of the period」「The lowest price of the period」「Close price」（MqlRates）。どの価格かは "Bid prices are used for constructing charts."（MetaTrader 5 のヘルプ `https://www.metatrader5.com/en/terminal/help/charts_analysis/charts`）、「MT5のチャートは、売り価格（Bid）に基づいて生成されており、買い価格（Offer）はチャート上には表示されません。」（OANDA `https://www.oanda.jp/platform/mt5`。付録 J-1） | CSV の bid に最も近い（K-5.3）。ask の足は無い |
| tick_volume | 「Tick volume」（MqlRates。数え方の定義の文は無い） | 冬: 最小 1・中央値 26.5・最大 262。夏: 2・18.5・91。CSV の 1 分あたりの tick の数（比べた前後 12 時間の平均で約 140〜160）よりずっと少ない |
| spread | 「Spread」（MqlRates。単位の文は無い）。ヘルプのデータウィンドウの説明に "spread (minimum value on a selected bar)"（同じヘルプ） | 0 の足は無し。冬: 最小 8・中央値 10・最大 46、夏: 3・10・14（単位はポイントとみなした。USDJPY の 1 ポイント = 0.001 円は仮定で、端末の `symbol_info` は読んでいない） |
| real_volume | 「Trade volume」（MqlRates） | **未確認**: 道具が保存する列に入れていなかった（`RATE_FIELDS` は time・open・high・low・close・tick_volume・spread）。FX では 0 の見込みだが確かめていない |

- 区間の扱い: "When requesting data in a specified range of dates, only data from this interval will be returned. The interval is set and counted up to seconds."（MQL5 の `CopyRates` `https://www.mql5.com/en/docs/series/copyrates`）。実際に `copy_rates_range` は終わりちょうど（02:00）の足も返した（終わりを含む）。道具は保存していない（区間外の 1 件として記録）。
- 2026-07-10 の足が返った件（K-3）は、上限の足数より古い区間を求めたときの振る舞いで、上の文と食い違う（観察）。

#### K-5.2 時刻の約 2 時間のずれ（観察と仮説）

- 仮説（司令塔の案）: API の time はサーバーの時計の値（FAQ 2190: 米国の夏時間に合わせて夏 GMT+3・冬 GMT+2）を UTC の数値として返す → **冬は +2 時間、夏は +3 時間**進むはず。
- 検証の方法: API の時刻を −240〜+240 分の 1 分刻みでずらし、1 分ごとの終値の変化の相関と、tick の数の相関が最大になるずれを探した（K-3 の終値の差の中央値より強い方法）。
- 観察: **冬・夏とも −120 分で最大**（冬: 変化の相関 0.768・tick の数の相関 0.551、夏: 0.960・0.839）。−180 分では冬 0.027、夏 −0.217。**夏（3/19）は仮説の −3 時間ではなく −2 時間**。
- 配布 CSV の側の規則（米国の夏時間に合わせる）は、2019-03 の月全体で FXCM（UTC）とずらし 0 分で揃うことを確かめ済み（付録 F-3。終値の差の中央値 0.10）。食い違いは API の側にあると見る。
- 考えられる原因（未確認）: (1) API のサーバーの 2019 年の履歴が、夏時間なしの GMT+2 で付いている。(2) API のサーバーが欧州の夏時間の規則（2019 年は 3/31 に切り替え）で動いていて、3/19 はまだ GMT+2。(3) 照合の限界（2 時間ずつ 2 区間だけ）。(1) と (2) は、欧州の切り替えの後（例: 2023-03-28 や 2017-03-28。配布 CSV が手元にある月）の USDJPY の 2 時間を 1 回要求すれば見分けられる（GMT+2 固定なら −2 時間、欧州の規則なら −3 時間）。この要求は道具の段に無いので、まだ出していない。

#### K-5.3 配布 CSV との差の原因の候補と検証（ずれ −120 分で比較）

| 候補 | 今のデータで検証できるか・方法 | 結果 |
|---|---|---|
| (a) 比べ方の差（CSV は bid・ask の tick、API は足） | できる。CSV の bid・ask・mid の 1 分足を作って比べる | 終値の差（API − CSV、符号つき、銭）の中央値: bid 冬 −0.2・夏 +0.4、mid −0.55・−0.65、ask −0.85・−1.7。**bid が最も近い**が、0.1 銭以内の足は冬 6.7%・夏 0.8% だけ。4 値の完全一致は bid で 120 本中 1 本（冬）・0 本（夏） |
| (b) 配信元の違い（口座の種類 `.cl`、プラン） | 一部できる。スプレッドと tick の数を比べる | API の spread は、CSV の同じ分の始めの ask−bid とも、分の最小の ask−bid とも 120 本中 0 本で一致（CSV の分の最小の中央値は冬 3・夏 23 ポイント、API は冬・夏とも 10）。tick_volume の中央値は CSV の 1 分あたりの tick の数の平均の約 1/6〜1/8（目安）。**別の気配の流れ（間引きや別の値付け）である可能性が高い**。口座の種類の違いそのものは、他の口座で取らないと確かめられない |
| (c) 足の境界（始め・終わり、含む・含まない） | できる。境界を ±1 分ずらして比べる | bid の終値の差の中央値: −121 分 0.5、−120 分 0.4、−119 分 0.6（冬）。夏も −120 分が最小か同じ。**境界のずれでは説明できない** |
| (d) 気配の集約の方法（足を作る規則） | できない（API の足の元の tick が取れない。`copy_ticks_range` が 0 件） | 未確認 |

- まとめ（観察）: API の 1 分足は bid の足に近く、時刻は −2 時間で揃うが、値は CSV の bid と 0.2〜0.5 銭ほど食い違い、スプレッドと tick の数も違う。**配布 CSV と同じ配信ではない見込み**で、差の原因は (b)（または (d)）の可能性が高い。断定はしない。
- バックテストの設計への当てはめ（同じ足の中の利確と損切り、ask の扱い）は別の設計役が行う（人間の第 9 回の指示の (A)）。ここでは、ask の足が無いこと、spread は 1 本に 1 つの値（ヘルプでは足の中の最小）であること、足の中の価格の順序は分からないことだけを記録する。

### K-6. 8 通貨の取得できる範囲と、時刻のずれの追加の検証（2026-10-15 21:24〜21:53 JST。作業役）

経緯: 人間の第 9 回の決定「進める（推奨）」（8 通貨の取得できる範囲の小さな確認）。時刻のずれの原因を見分ける追加の 1 要求（USDJPY 2023-03-28）は、人間の指示「時刻の約2時間のずれとCSVとの差は、原因を整理してください」の範囲として司令塔が判断した。道具に段 `offset-eu-dst`（USDJPY 2023-03-28 00:00〜02:00）と `range-8`（8 通貨 × 2017-01-10・2019-03-05・2023-12-05 の 00:00〜02:00、tick と 1 分足。**要約だけを保存し、価格の行は保存しない**）を足し、銘柄の `chart_mode`・`point`・`digits` と `real_volume` の列を記録するようにした（単体テスト 2 件を追加。道具の SHA-256 `ce76b8e00d8778b0a6a4df435a690da28c0b9bb647c20043b3f41ba79992350f`、Windows で一致を確認）。

#### K-6.1 時刻のずれの追加の検証（`offset-eu-dst`）

| 項目 | 結果 |
|---|---|
| 銘柄 | `USDJPY.cl`、`chart_mode` 0（bid で足を作る）、`point` 0.001、`digits` 3 |
| tick | 0 件（再試行も 0、`last_error` (1, 'Success')） |
| 1 分足 | 110 件（API の時刻の値で 00:00〜01:59。区間外は 1 件で保存していない）。`real_volume` はすべて 0。spread は 3〜56（中央値 35）、tick_volume は 1〜233（中央値 6） |
| ずれの探索（−240〜+240 分。K-5.2 と同じ方法） | **−180 分で最大**（1 分の変化の相関 1.000、tick の数の相関 0.742、終値の差の中央値 0.0 銭）。−120 分では 0.171 |
| 配布 CSV 2023-03 の bid の 1 分足との比較（−180 分） | **始値・高値・安値・終値が 110 本すべて完全に一致**。ask・mid とは一致 0 本 |
| ファイル | zip `6a36b4d1dc78f1a9c0e931fe77b36060413819ce040934a2330e6cc68738448f`、1 分足 `2b9cca07c81aeef19b9eae579064d067cfdf4642e7a10252e1ab82d4804d12fb`、要約 `bd03fb89499d58634357d45d2066c1237e8ffe6d00623cccbaf46e0ae1a30af9`。解析は `<scratch>/d12_scripts/analyze_m1_phase.py` `6da7344e6cfe45111a59d786574b6ff7136273ecd03ba3332a126e325765dbaf`、出力 `<scratch>/analyze_offset_result.json` `8bdc4761336de90fd299fcd96d3616e94f3b07fbb28ac983797dc04a4340ab78` |

観察と仮説（断定しない）:

- 2023-03-28（米国・欧州とも夏時間）では、API の time は UTC より **3 時間進み**、値は配布 CSV の bid と**完全に同じ**。「API の time はサーバーの時計（夏 GMT+3）の値を UTC の数値として返す」という仮説（K-5.2）に合う。
- K-5.2 の候補 1「履歴が全部、夏時間なしの GMT+2」は、2023-03-28 については当てはまらない（GMT+2 固定なら −120 分になるはず）。
- 2019-03-19 の −120 分を説明する候補として残るのは: (1′) **2019 年の履歴だけ**が配布 CSV と別の配信で、夏時間の扱いも違う（2019 年は値も CSV と一致しなかった。2023 年は一致した）、(2) サーバーの時計が欧州の規則で切り替わる（2019 年は 3/31。3/19 はまだ GMT+2）。2023-03-28 は米国・欧州のどちらの規則でも夏時間なので、(2) は見分けられない。見分けるには、米国だけが夏時間の 2023-03-14〜24 の区間（配布 CSV 2023-03 がある）を 1 回要求する（米国の規則なら −180 分、欧州の規則なら −120 分）。
- 値の一致: 2023 年は API の 1 分足 = 配布 CSV の bid で作った 1 分足。2019 年は一致しない（K-5.3）。**年によって API の履歴の出どころが違う可能性**がある。

#### K-6.2 8 通貨の範囲（`range-8`）: 取れなかった（ディスクの空きの不足で止めた）

| 時刻（JST） | 出来事 |
|---|---|
| 21:27:54 | `range-8` を開始（最初の要求は USDJPY 2017-01-10 の tick） |
| 21:39〜21:49 | 道具（python）の CPU は 0.25 秒のまま、MT5 の端末の CPU は約 720→1,200 秒、メモリ 約 1.4〜1.5 GB。端末が履歴を取り寄せている状態。結果の要約はまだ無い |
| 21:49 | **C ドライブ 30 GB のうち 26.0 GB 使用・空き 4.0 GB**、受信 1,496 MB。起動の直後の使用量は記録していない |
| 21:50 | ディスクが一杯になって Windows や SSM が止まるのを避けるため、**タスクを止め、道具（python）を終了**。20 秒・60 秒後の使用量は 25.91→26.10 GB、受信 1,502 MB。21:52 に 26.11 GB・1,504 MB でほぼ止まった。端末は動いたまま（ログイン状態を保つため。端末のデータのフォルダは開いていない・消していない） |
| 21:50 | 要約は作られず（タスクの結果 267014、`run.log` は 0 バイト）。回収した zip は `run.log` だけ（`88d2aec8b9d2478940cfec420ffa88b188544260fcb6cdb9892e1df359e4b30f`） |

- 読み（仮説）: 「チャートの最大バー数」を Unlimited にした後で、2017 年の区間を要求したため、端末が 2017 年から今までの履歴（tick か 1 分足。どちらかは分からない）を取り寄せ始めた。8 通貨・2017 年からの履歴は 30 GB のディスクに入らない見込み。
- 分かったこと: 8 通貨の範囲は**まだ分からない**。tick は USDJPY の 3 区間（2019-03-05・2019-03-19・2023-03-28、各 2 時間）でいずれも 0 件（2019 年の 2 区間は、最初の試験と「Max. bars in chart」を Unlimited にした後の再試行で 2 回ずつ要求した。要求は計 5 回。道具の中の 5 秒後の自動の再試行は数えていない。付録 K-3・K-6.1）。

#### K-6.3 8 通貨の 1 分足の取得できる範囲（人間の第 10 回の決定。ディスクを 60 GB に広げた後。2026-10-15 22:09〜22:14 JST）

- 拡張: 22:08 にボリューム `vol-0e7392b808dc5954a` を 30→60 GB（`modify-volume`。状態は modifying→optimizing）、22:09 に Windows の C の区画を 60 GB に広げた（`Update-HostStorageCache`・`Resize-Partition`）。**使用 26.54 GB・空き 33.46 GB**。これ以上は広げない。
- 要求: 1 分足だけ（tick は要求していない）。1 通貨ずつ、新しい年から（2023-12-05 → 2019-03-05 → 2017-01-10、各 00:00〜02:00。USDJPY は 2017-01-10 だけ。2019-03・2023-03 は K-3・K-6.1 で確認済み）。要約だけを保存し、価格の行は保存していない。空きが 3 GB を切ったら止める仕組みは働かなかった（空きは全要求の前後で 33.46 GB のまま）。
- どの要求も `last_error` は (1, 'Success')、再試行なし。区間外は、どの要求も 1 件（区間の終わりちょうどの 02:00 の足。保存していない）。時刻は API の値のまま（UTC より約 2〜3 時間進む。K-5.2・K-6.1）。

| 通貨（端末の銘柄名） | chart_mode | 最小単位（point）・桁数 | 2023-12-05 の件数（最初〜最後） | 2019-03-05 | 2017-01-10 |
|---|---|---|---|---|---|
| USDJPY（`USDJPY.cl`） | 0（bid） | 0.001・3 | （未要求。2023-03-28 は 110 件） | （K-3: 120 件） | 120（00:00〜01:59） |
| EURUSD（`EURUSD.cl`） | 0 | 0.00001・5 | 120（00:00〜01:59） | 120（00:00〜01:59） | 120（00:00〜01:59） |
| EURJPY（`EURJPY.cl`） | 0 | 0.001・3 | 118（00:00〜01:59） | 120（00:00〜01:59） | 120（00:00〜01:59） |
| GBPJPY（`GBPJPY.cl`） | 0 | 0.001・3 | 118（00:00〜01:59） | 120（00:00〜01:59） | 120（00:00〜01:59） |
| USDCHF（`USDCHF.cl`） | 0 | 0.00001・5 | 117（00:00〜01:59） | 119（00:00〜01:59） | 120（00:00〜01:59） |
| AUDJPY（`AUDJPY.cl`） | 0 | 0.001・3 | 120（00:00〜01:59） | 120（00:00〜01:59） | 119（00:01〜01:59） |
| GBPUSD（`GBPUSD.cl`） | 0 | 0.00001・5 | 119（00:00〜01:59） | 120（00:00〜01:59） | 111（00:00〜01:59） |
| AUDUSD（`AUDUSD.cl`） | 0 | 0.00001・5 | 117（00:00〜01:59） | 120（00:00〜01:59） | 120（00:00〜01:59） |

- 読み: **8 通貨すべてで、2017-01-10・2019-03-05・2023-12-05 の 1 分足が返った**（120 本に満たない分は、その分に気配が無かった可能性。欠けの位置は記録していない）。どの通貨も接尾辞は `.cl` で、候補は 1 つ。値が配布 CSV と一致するか（2019 年の USDJPY は一致しなかった。K-5.3）は、USDJPY 以外では確かめていない（配布 CSV は USDJPY・EURUSD・EURJPY・GBPJPY だけ）。
- 要約のファイルの SHA-256（`data/raw/oanda_mt5_probe/2026-10-15/m1-<通貨>/summary_m1-<通貨>.json`、zip は `out_m1-<通貨>.zip`）:

| 通貨 | 要約 | zip |
|---|---|---|
| USDJPY | `d63f52f3ee6f4add9b889aba53b6b8cf9fa230ac5e5383089e65561f62074ebe` | `c9cde20707521bcd1c823b7969f4e93771a72b5262326fc253ada903a80fbbf9` |
| EURUSD | `57a4e75c31fe8e76723035080cfd2c9fe7de0f52b5be6835803835d3e3f6cf8e` | `b34433d9789b29bee36ed59ac230abec66d4c2cb0ec41de9216fbe907812c966` |
| EURJPY | `2d496db51cb4a109375bc9ca8ba9d389b17df6e7a8326b928b50cdc195e8aa0c` | `23bd2573241a59587a8c652c96d86417fb7d5e84609006497e1e1c2afa09f78e` |
| GBPJPY | `37e5d4364d7417e470d3998a92648b883566553676ce2bc22d9c2cbbdbaac3b4` | `b9a1b9a14f62c75c1c7f55d0ba0d1f32aaa438a168dee54288edb2963bd62c61` |
| USDCHF | `efd55e2e90ae5a7c6902a5da3d16734705ee509aec509faec5c965211b19c0a0` | `4aa2d3325b6a369b54aa20ff578d898caa1dea57036f91c62c876ed0301500fa` |
| AUDJPY | `1f63f096a106a24536f2014acfed79c98e97610ff0e2f67bf8669e10151c9bfe` | `4b1e897cf4fc2d81cdccbe961eebc8d2c08185f4cda6ad6c677b1b1cc4e68d53` |
| GBPUSD | `5f1baa995fd1a3f7a21f894f7e597b942d2a0f733a784081532b5226c2d50c42` | `095e44e89aeb0d119cf7fb605eb953b7d0bf226e273bb96a020edbeb28c82371` |
| AUDUSD | `11155c25a0e1e29d7bbc281dc25a87fd1c9d876d2a2e9cca7c76a3d7b5592fe7` | `d7f575e93d47398759d19b4e01b0ad796a7c27bb7cd893527d8d9c23211d0bfd` |

### K-7. MT5 の端末が要求の区間の外で取り寄せた期間とデータの種類（2026-10-15 22:15 JST に記録。作業役）

人間の第 10 回の決定（「MT5が要求区間以外に同期する期間・データ種類も記録してください。2024年以降の自動同期分は研究に取り込まない従来の条件を維持します。」）による。端末のデータのフォルダ（`C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\<端末の ID>\bases\`）の**ファイルの名前・大きさ・更新時刻だけ**を SSM で一覧にした（中身は開いていない・コピーしていない・回収していない）。一覧は `<scratch>/bases_listing.txt`（口座番号を含むフォルダ名を伏せた版、SHA-256 `05198c36300000ce8a4e1cbec0d345bd1261b43adb9178e21ce53328c7485d3e`）。MT5 の慣行では `history/<銘柄>/<年>.hcc` が 1 分足の履歴、`ticks/<銘柄>/<年月>.tkc` が tick の履歴（MetaQuotes の公式の文での確認はしていない）。

| サーバー・種類 | 銘柄 | ファイル（最初〜最後） | 合計の大きさ | 最後の更新（UTC） |
|---|---|---|---|---|
| OANDA-Japan MT5 Live・history | AUDJPY・AUDUSD・EURJPY・EURUSD・GBPJPY・GBPUSD・USDCHF（各 `.cl`） | 各 15 ファイル、`2012.hcc`〜`2026.hcc` | 各 約 295〜297 MB | 12:47〜13:07 |
| 同 | `USDJPY.cl` | 11 ファイル、`2016.hcc`〜`2026.hcc` | 230.0 MB | 12:58 |
| OANDA-Japan MT5 Live・ticks | 8 通貨（各 `.cl`） | 各 10 ファイル、**`202601.tkc`〜`202610.tkc`**（USDJPY はほかに `ticks.dat`） | 44.0〜167.9 MB（計 約 830 MB） | 12:33〜12:57 |
| 同・ticks | GER30・HK50・JP225・UK100・UKOIL・US100・US30・US500・XAUUSD・USOIL | 各 `ticks.dat` 1 つ | 各 0.1 MB 以下 | 12:01（端末の再起動のとき。気配の表示の既定の銘柄と見られる） |
| OANDA-Japan MT5 Live・history | 接尾辞なしの EURUSD・GBPUSD・USDCHF・USDJPY | 各 `2026.hcc` 1 つ | 0.0 MB | 11:35（端末の導入のとき） |
| Default・History（2 か所: 利用者のフォルダと `C:\Program Files\OANDA MetaTrader 5`） | EURUSD・GBPUSD・USDCHF・USDJPY | `2023.hcc`（と `2026.hcc`） | 各 約 9.5 MB | 11:32〜11:35（導入のとき。導入物に含まれる見本と見られる） |

観察と仮説:

- **1 分足**: 2017〜2023 年の区間を要求した結果、端末は 7 通貨で **2012 年から 2026 年まで**、USDJPY で 2016 年から 2026 年までの 1 分足の履歴を年ごとに取り寄せた（要求の区間の外の 2012〜2016 年と、**2024〜2026 年を含む**）。取り寄せの時刻（12:47〜13:07 UTC）は、`range-8` の実行中と、道具を止めた後の約 17 分。
- **tick**: 端末が取り寄せた tick は 8 通貨とも **2026-01〜2026-10 の月だけ**（取り寄せの時刻 12:33〜12:57 UTC は `range-8` の実行中（12:27〜12:50）と、止めた後の約 7 分）。2017〜2023 年の tick のファイルは無い。USDJPY の 2019・2023 年の tick の要求が 0 件だった（K-3・K-6.1）ことと合わせ、**サーバーから取れる tick の履歴は 2026 年 1 月以降だけの見込み**（仮説。サーバーの仕様の文は無い）。
- ディスクの使用量 26 GB のうち、これらの履歴は計 約 3.2 GB。残りは Windows・Python・MT5 の本体など（起動の直後の使用量を記録しなかったので、内訳は推定）。
- **2024 年以降の自動で取り寄せられた分（`2024.hcc`〜`2026.hcc`、`2026xx.tkc`）は、研究に取り込まない**（D12-21 の条件）。中身を読んでおらず、回収していない。インスタンスの終了で、ボリュームごと消える（K-3 の片付け）。
- tick の追加の試験: 上の観察から、2017〜2023 年の tick はこの経路では取れない見込みで、試験の必要性は低い。必要かどうかは人間が判断する（人間の第 10 回の決定）。

### K-8. 時刻の調査と空白の測定の実施の記録（準備。2026-10-15 23:35〜2026-10-16 00:25 JST。作業役。課金なし）

人間の決定（D12-35 修正後の承認、原文「承認する（推奨）」、第 5 組）による D12 §11.7 の時刻の調査と、人間の決定（D12-38・D12-39、第 6 組。原文「D12-38・39とも（a）を選びます。年2週・8通貨・7年の測定を、時刻調査と同じAWS起動で実施してください。追加112要求、作業合計4時間、ディスク60GBを上限とします。」）による D12 §11.9 の空白の測定の、同じ 1 回の AWS 起動の準備。**AWS は起動していない**（司令塔の「起動してよい」を待つ）。

#### K-8.1 道具とスクリプト

| もの | 中身 |
|---|---|
| `tools/survey/mt5_api_probe.py` の `timesurvey` | §11.7.2 の段 A2（10）・A1（23）・B1（28）・B2（6）の計 67 要求と、決定木の追加（計画のファイル、最大 20）を要求する。**1 分足だけ**。週末の区間は API の時刻の値で金曜 18:00〜月曜 02:00、火曜の区間は 00:00〜02:00。2024-01-01 以降を含む区間があれば何も要求しない。保存するのは要求ごとの件数・区間の中の足の**時刻の並び**・`chart_mode`・`point`・`digits`・ディスクの空き。火曜の区間の配布 CSV との照合（E1）は、Mac で作った CSV の bid の 1 分足（`e1_ref.json.gz`）を Windows に置き、**Windows の上でメモリの中で**比べ、ずれ −4〜+4 時間ごとの「比べた足の数・4 値が完全に一致した足の数・終値の差の中央値（ポイント）」だけを書く。**API の価格の値はファイルにも出力にも出さない**。要求の数の上限とディスクの空き 3 GB で、残りを出さずに止まる |
| `tools/survey/mt5_time_survey.py`（Mac） | `e1-ref`: 配布 CSV（USDJPY 2017-03・2019-03・2023-03、EURJPY・EURUSD・GBPJPY 2019-03）から、火曜の区間の前後 5 時間の bid の 1 分足を作る。`evaluate`: 回収した要約から、週末の境界（E2。金曜の最後の足と日曜の最初の足を、ニューヨーク 17:00 の閉場・開場と比べ、時間に丸める。丸めの残りが 15 分を超えたら読めない）、夏時間の切替の前後（E3）、通貨の間の同じ時刻（E4）、E1 を読み、§11.7.3 の表で合う仮説（US・EU・固定）を残し、年ごとの判定と決定木の分岐を書く |
| `tools/survey/aws_mt5_probe/35_time_survey.sh <段>`（段は A2・A1・B1・B2・extra・G） | 1 段を実行（Administrator の対話のセッションのタスク）→ 回収（`40_collect.sh`）→ Mac で評価（`report.md`・`extra_plan.json`）。要求の数を状態のファイルに積み上げ、上限（67 ＋ 追加 20）を超えない |
| `10_launch.sh` | ディスクを**起動時から 60 GB**（上限。拡張しない） |
| `95_deadline_guard.sh`・`check_deadline` | 起動から **4 時間**（時刻の調査 3 時間＋空白の測定 1 時間）で終了する。時刻の調査の段（A2・A1・B1・B2・extra）は起動から 3 時間を過ぎたら出さない（T7）。Windows の中の自動の停止は **4 時間 30 分**（止め忘れの備え） |
| `timesurvey --stage G`（空白の測定） | §11.9.2 の代表週 112 要求（8 通貨 × 2017〜2023 × W1・W2）。1 要求 = 1 週（API の時刻の値で日曜 00:00〜土曜 03:00。ずれ +2〜+3 時間でも日曜の開場の前から金曜の閉場の後までを含む。最後の終わりは 2023-12-30 03:00 で 2023 年の中）。**足の時刻の列だけ**を保存し、要求ごとの件数と最初・最後の時刻を要約に書く。価格は保存しない。時刻の評価（`evaluate`）には使わず、集計は時刻の規則が確定した年・通貨だけ、AWS の終了の後に Mac で行う（未確定の年・通貨は生の時刻のまま未集計） |

**決定木の自動と司令塔の判断の分け方**:

| 分岐 | 扱い |
|---|---|
| T1（年の 4 状態が 1 つの仮説で揃う） | 自動で「直接確認」と書く。足す要求なし |
| T2（2019 年の週末が仮説 US を示し、2019-03-19 の −2 時間と食い違う） | 自動で 2019-03-12・2019-03-26 の火曜（E1）を足す（2 要求） |
| T4（他の通貨の E2 か E4 が USDJPY と違う） | 自動でその通貨・年の S1・S4 の週末を足す（2 要求） |
| T5（週末の境界が読めない） | 自動で同じ週の火曜を足す（1 要求。補助だけでは確定しないので、その「年 × 状態 × 通貨」は未解決のまま） |
| T6（E1 と E2・E3 が別の仮説） | 自動で同じ火曜を読み直す（1 要求） |
| T3（年の途中の変更の疑い） | **司令塔の判断**（二分探索で足す週末の選び方と、未解決として止めるか） |
| T7（上限: 要求 87、3 時間、空き 3 GB） | 自動で止める。追加の計画が残りを超えたら、**司令塔の判断**（上限内の分だけ出すか、報告して終了するか） |

- 段の順序: A2 → 評価 →（自動の追加があれば `extra`）→ A1 → 評価 → … → B1 → B2 → 評価 →（`extra`）→ **G（空白の測定）**。時刻の調査で T3 の判断が出たら司令塔に報告する。時刻の調査が上限（T7）で終わった場合も、残りの時間で G を行い、起動から 4 時間で必ず止める。
- 判定の規則の実装（§11.7.2a の組合せの近似）: USDJPY の年は「読めた週末が 2 つ以上で、合う仮説の共通部分が 1 つ」で、同じ年の火曜の E1 が一致していればその仮説も同じこと。他の通貨は「その週末の E2 が USDJPY の確定した仮説で説明でき、金曜の最後と日曜の最初の足の時刻が USDJPY と同じ（E4）」。開場の位置だけ、E4 だけでは確定しない。
- E1 の照合の確かめ（2026-10-16 00:10 JST）: 既に回収済みの USDJPY 2023-03-28 の 1 分足（K-6.1）と、`e1-ref` で作った同じ火曜の CSV の bid の足を `e1_stats` で比べ、ずれ +3 時間で 110 本中 110 本が完全に一致し、読みは仮説 US・EU（K-6.1 の手作業の結果と同じ）。E1 の参照のファイル（13 区間、各 715〜720 本）は `<scratch>/e1_ref.json.gz`（83,856 バイト、作るのに約 10 分）。
- 単体テスト（`tests/unit/test_mt5_time_survey.py`）: 空白の測定の段（112 週、日曜〜土曜、W1 の金曜が 6 月の第 1 金曜、W2 の日曜が 12/22〜28、全部 2023-12-31 以前、時刻の評価に混ざらない）に加え、 段の数と区間（全部 2023-12-31 以前、週末は金曜 18:00〜月曜 02:00）、曜日の誤りと 2024 年の計画の拒否、4 状態 × 仮説の読み、境界のぼやけ、年の確定と他の通貨の確定、T2・T5・T3・T4・T7、E1 の統計に価格が出ないこと、要求の上限とディスクの空きで止まること。

#### K-8.2 要求の一覧（67。週末は API の時刻の値で金曜 18:00〜月曜 02:00、火曜は 00:00〜02:00。新しい年から）

| # | 段 | 通貨 | 種類 | 状態 | 区間（API の時刻の値） | 取る証拠 |
|---|---|---|---|---|---|---|
| 1 | A2 | USDJPY | 週末 | S1 | 2023-01-13 18:00〜2023-01-16 02:00 | E2・E3（S2・S4） |
| 2 | A2 | USDJPY | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4） |
| 3 | A2 | USDJPY | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4） |
| 4 | A2 | USDJPY | 週末 | S4 | 2023-10-27 18:00〜2023-10-30 02:00 | E2・E3（S2・S4） |
| 5 | A2 | USDJPY | 週末 | S1 | 2019-01-11 18:00〜2019-01-14 02:00 | E2・E3（S2・S4） |
| 6 | A2 | USDJPY | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4） |
| 7 | A2 | USDJPY | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4） |
| 8 | A2 | USDJPY | 週末 | S4 | 2019-10-25 18:00〜2019-10-28 02:00 | E2・E3（S2・S4） |
| 9 | A2 | USDJPY | 火曜 | S1 | 2023-03-07 00:00〜2023-03-07 02:00 | E1（配布 CSV） |
| 10 | A2 | USDJPY | 火曜 | S2 | 2023-03-14 00:00〜2023-03-14 02:00 | E1（配布 CSV） |
| 11 | A1 | USDJPY | 週末 | S1 | 2022-01-14 18:00〜2022-01-17 02:00 | E2・E3（S2・S4） |
| 12 | A1 | USDJPY | 週末 | S2 | 2022-03-11 18:00〜2022-03-14 02:00 | E2・E3（S2・S4） |
| 13 | A1 | USDJPY | 週末 | S3 | 2022-07-15 18:00〜2022-07-18 02:00 | E2・E3（S2・S4） |
| 14 | A1 | USDJPY | 週末 | S4 | 2022-10-28 18:00〜2022-10-31 02:00 | E2・E3（S2・S4） |
| 15 | A1 | USDJPY | 週末 | S1 | 2021-01-15 18:00〜2021-01-18 02:00 | E2・E3（S2・S4） |
| 16 | A1 | USDJPY | 週末 | S2 | 2021-03-12 18:00〜2021-03-15 02:00 | E2・E3（S2・S4） |
| 17 | A1 | USDJPY | 週末 | S3 | 2021-07-09 18:00〜2021-07-12 02:00 | E2・E3（S2・S4） |
| 18 | A1 | USDJPY | 週末 | S4 | 2021-10-29 18:00〜2021-11-01 02:00 | E2・E3（S2・S4） |
| 19 | A1 | USDJPY | 週末 | S1 | 2020-01-10 18:00〜2020-01-13 02:00 | E2・E3（S2・S4） |
| 20 | A1 | USDJPY | 週末 | S2 | 2020-03-06 18:00〜2020-03-09 02:00 | E2・E3（S2・S4） |
| 21 | A1 | USDJPY | 週末 | S3 | 2020-07-10 18:00〜2020-07-13 02:00 | E2・E3（S2・S4） |
| 22 | A1 | USDJPY | 週末 | S4 | 2020-10-23 18:00〜2020-10-26 02:00 | E2・E3（S2・S4） |
| 23 | A1 | USDJPY | 週末 | S1 | 2018-01-12 18:00〜2018-01-15 02:00 | E2・E3（S2・S4） |
| 24 | A1 | USDJPY | 週末 | S2 | 2018-03-09 18:00〜2018-03-12 02:00 | E2・E3（S2・S4） |
| 25 | A1 | USDJPY | 週末 | S3 | 2018-07-13 18:00〜2018-07-16 02:00 | E2・E3（S2・S4） |
| 26 | A1 | USDJPY | 週末 | S4 | 2018-10-26 18:00〜2018-10-29 02:00 | E2・E3（S2・S4） |
| 27 | A1 | USDJPY | 週末 | S1 | 2017-01-13 18:00〜2017-01-16 02:00 | E2・E3（S2・S4） |
| 28 | A1 | USDJPY | 週末 | S2 | 2017-03-10 18:00〜2017-03-13 02:00 | E2・E3（S2・S4） |
| 29 | A1 | USDJPY | 週末 | S3 | 2017-07-14 18:00〜2017-07-17 02:00 | E2・E3（S2・S4） |
| 30 | A1 | USDJPY | 週末 | S4 | 2017-10-27 18:00〜2017-10-30 02:00 | E2・E3（S2・S4） |
| 31 | A1 | USDJPY | 火曜 | S1 | 2017-03-07 00:00〜2017-03-07 02:00 | E1（配布 CSV） |
| 32 | A1 | USDJPY | 火曜 | S2 | 2017-03-14 00:00〜2017-03-14 02:00 | E1（配布 CSV） |
| 33 | A1 | USDJPY | 火曜 | S3 | 2017-03-28 00:00〜2017-03-28 02:00 | E1（配布 CSV） |
| 34 | B1 | EURJPY | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4）・E4 |
| 35 | B1 | EURJPY | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4）・E4 |
| 36 | B1 | EURJPY | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4）・E4 |
| 37 | B1 | EURJPY | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4）・E4 |
| 38 | B1 | EURUSD | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4）・E4 |
| 39 | B1 | EURUSD | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4）・E4 |
| 40 | B1 | EURUSD | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4）・E4 |
| 41 | B1 | EURUSD | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4）・E4 |
| 42 | B1 | GBPJPY | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4）・E4 |
| 43 | B1 | GBPJPY | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4）・E4 |
| 44 | B1 | GBPJPY | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4）・E4 |
| 45 | B1 | GBPJPY | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4）・E4 |
| 46 | B1 | USDCHF | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4）・E4 |
| 47 | B1 | USDCHF | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4）・E4 |
| 48 | B1 | USDCHF | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4）・E4 |
| 49 | B1 | USDCHF | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4）・E4 |
| 50 | B1 | AUDJPY | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4）・E4 |
| 51 | B1 | AUDJPY | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4）・E4 |
| 52 | B1 | AUDJPY | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4）・E4 |
| 53 | B1 | AUDJPY | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4）・E4 |
| 54 | B1 | GBPUSD | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4）・E4 |
| 55 | B1 | GBPUSD | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4）・E4 |
| 56 | B1 | GBPUSD | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4）・E4 |
| 57 | B1 | GBPUSD | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4）・E4 |
| 58 | B1 | AUDUSD | 週末 | S2 | 2023-03-10 18:00〜2023-03-13 02:00 | E2・E3（S2・S4）・E4 |
| 59 | B1 | AUDUSD | 週末 | S3 | 2023-07-14 18:00〜2023-07-17 02:00 | E2・E3（S2・S4）・E4 |
| 60 | B1 | AUDUSD | 週末 | S2 | 2019-03-08 18:00〜2019-03-11 02:00 | E2・E3（S2・S4）・E4 |
| 61 | B1 | AUDUSD | 週末 | S3 | 2019-07-12 18:00〜2019-07-15 02:00 | E2・E3（S2・S4）・E4 |
| 62 | B2 | EURJPY | 火曜 | S1 | 2019-03-05 00:00〜2019-03-05 02:00 | E1（配布 CSV） |
| 63 | B2 | EURJPY | 火曜 | S2 | 2019-03-19 00:00〜2019-03-19 02:00 | E1（配布 CSV） |
| 64 | B2 | EURUSD | 火曜 | S1 | 2019-03-05 00:00〜2019-03-05 02:00 | E1（配布 CSV） |
| 65 | B2 | EURUSD | 火曜 | S2 | 2019-03-19 00:00〜2019-03-19 02:00 | E1（配布 CSV） |
| 66 | B2 | GBPJPY | 火曜 | S1 | 2019-03-05 00:00〜2019-03-05 02:00 | E1（配布 CSV） |
| 67 | B2 | GBPJPY | 火曜 | S2 | 2019-03-19 00:00〜2019-03-19 02:00 | E1（配布 CSV） |

#### K-8.2a 空白の測定の要求の一覧（112。D12 §11.9.2。8 通貨すべてで同じ区間。1 分足の時刻だけ）

| 年 | W1（6 月の第 1 金曜の週）の区間（API の時刻の値） | W2（日曜が 12/22〜28 の週） |
|---|---|---|
| 2023 | 2023-05-28（日）00:00〜2023-06-03（土）03:00 | 2023-12-24（日）00:00〜2023-12-30（土）03:00 |
| 2022 | 2022-05-29（日）00:00〜2022-06-04（土）03:00 | 2022-12-25（日）00:00〜2022-12-31（土）03:00 |
| 2021 | 2021-05-30（日）00:00〜2021-06-05（土）03:00 | 2021-12-26（日）00:00〜2022-01-01（土）03:00 |
| 2020 | 2020-05-31（日）00:00〜2020-06-06（土）03:00 | 2020-12-27（日）00:00〜2021-01-02（土）03:00 |
| 2019 | 2019-06-02（日）00:00〜2019-06-08（土）03:00 | 2019-12-22（日）00:00〜2019-12-28（土）03:00 |
| 2018 | 2018-05-27（日）00:00〜2018-06-02（土）03:00 | 2018-12-23（日）00:00〜2018-12-29（土）03:00 |
| 2017 | 2017-05-28（日）00:00〜2017-06-03（土）03:00 | 2017-12-24（日）00:00〜2017-12-30（土）03:00 |

- 通貨: USDJPY・EURJPY・EURUSD・GBPJPY・USDCHF・AUDJPY・GBPUSD・AUDUSD（この順。各通貨の中は新しい年から）。8 × 7 × 2 = 112 要求。
- 結果の解釈（D12-39 の原文）: 代表週の空白から全期間の実行不能を断定しない。「測定範囲の結果」と「全期間では未確認のこと」を分けて報告する。休場・短縮取引はカレンダーに従い、ウォームアップは日足 60 本で判定する。時刻が未確定の範囲は生の時刻を保存して未集計とし、推測で空白を判定しない。

- 決定木の追加（最大 20）は、評価が出す `extra_plan.json` の要求（`X-<通貨>-T<火曜>` か `X-<通貨>-W<日曜>`）。どれも 2023-12-31 以前。
- 既にある結果（USDJPY の火曜 2019-03-05・2019-03-19・2023-03-28、8 通貨の 2017-01-10・2019-03-05・2023-12-05）は再要求しない。

#### K-8.3 人間の作業（起動の後、作業役の合図で）

- K-1 の IAM（ユーザー・ポリシー・ロール）は**そのまま使う**（K-2 の手順 9 は、まだしていない前提）。消していた場合は K-1 の 4〜9 をやり直す。
- K-2 の手順 1〜6 を再掲（作業役が新しい IP とコマンドを伝える）:
  1. 作業役が伝えるコマンド（`aws ec2 get-password-data ... --priv-launch-key ~/.ssh/odyssey-mt5-probe-<日付>.pem ...`）を Mac で実行し、管理者のパスワードを出す（チャットに書かない）。
  2. Windows App で、伝えられた IP・`Administrator`・そのパスワードで接続する。
  3. Windows のブラウザで、OANDA のメールかマイページの MT5 のダウンロードのリンクから端末を入れる。
  4. デモ口座の申請の画面を閉じ、「ファイル」→「取引口座にログイン」でサブアカウント（サーバー OANDA-Japan MT5 Live）にログインする。
  5. **「ツール」→「オプション」→「チャート」→「チャートの最大バー数」を Unlimited にし、端末を終了して起動し直す**（新しいインスタンスの端末は既定の値に戻っているため、もう一度設定する）。接続を確かめる。
  6. チャートの窓・気配値表示の窓をすべて閉じ、RDP を「切断」にする（サインアウトしない）。司令塔に「ログインした」と伝える。
- 人間の時間: 約 15〜20 分。

#### K-8.4 時間と費用の再見積（米ドル、消費税 10% 込み。単価は K-4 と同じ）

| 手順 | 時間の見込み |
|---|---|
| 起動・準備（`10_launch.sh`・`20_prepare.sh`・見張り） | 約 15 分 |
| 人間の RDP の作業（K-8.3） | 約 15〜20 分 |
| 段 A2・A1・B1・B2 と決定木の追加（端末の 1 分足の履歴の取り寄せを含む。前回は 8 通貨で約 20 分） | 約 40〜70 分 |
| 段 G（空白の測定、112 要求。取り寄せは時刻の調査で済む見込み） | 約 15〜30 分 |
| 回収・評価・端末のデータのフォルダの一覧・片付け | 約 15 分 |
| 計 | **約 2〜2.5 時間**（作業上限 4 時間） |

| 項目 | 想定 | 上限 |
|---|---|---|
| 前提 | 4 時間（作業上限。時刻の調査 3 時間＋空白の測定 1 時間）、60 GB、送信 0.1 GB、CPU のクレジットは標準 | 6 時間（D12 §11.9.4 と同じ前提。4 時間の終了と 4 時間 30 分の自動の停止の両方が働かない場合を含めて丸めた）、60 GB、送信 5 GB、CPU のクレジットを標準にし損ねた場合 |
| この回の費用 | **約 0.39** | **約 2.5** |

| 予算 | 上限 | 使用済み（K-4 の実績の概算） | この回の後（想定） | この回の後（上限） |
|---|---|---|---|---|
| 試験予算（D12-20） | 5 | 約 0.24 | 約 0.63（残り 約 4.37） | 約 2.74（残り 約 2.26） |
| 全体予算（第 3 回の決定） | 100 | 約 0.24 | 約 0.63（残り 約 99.37） | 約 2.74（残り 約 97.26） |

- D12 §11.9.4 の場合 A（相乗り）の見積と同じ。止め忘れは 1 時間に 約 0.094。前回の実績（約 0.24）は請求の確定前（人間の翌日の確認）。

### K-9. 時刻の調査と空白の測定の実施の記録（2026-10-16 00:21〜02:46 JST。作業役）

人間の決定: 起動（原文「今から起動してよい」）、tick の要求 1 回（原文「(a) tick 要求を 1 回だけ出す（USDJPY 2023-12-27 00:00〜00:05。返った tick は保存しない）（推奨）」。目的は**端末の 1 分足の履歴の取り寄せを起こすことだけ**で、tick の試験ではない）。価格の値はどのファイルにも残していない。2024 年以降は要求していない。

#### K-9.1 作業の記録（時刻は JST）

| 時刻（JST） | 作業 | 結果 |
|---|---|---|
| 2026-10-16 00:1x（司令塔経由） | 人間の決定（原文）「今から起動してよい」 | — |
| 00:21 | `00_check.sh` | 残骸 0、起動の権限あり、AMI `ami-0922556670ef93267`、見積は想定 約 0.39・上限 約 2.46。Budgets の予算は 0 件（前回の人間の決定「予算が無いまま起動を許可する」を当てはめて起動。司令塔に報告） |
| 00:22:29 | `10_launch.sh --execute` | インスタンス `i-0d04426839a9fe9c8`（t3.medium、1c）、gp3 60 GB・CPU クレジット標準を確認、セキュリティグループ `sg-01d0b9e15874c8010`（RDP は 126.221.39.9/32 だけ）、キーペア `odyssey-mt5-probe-2026-10-16`。時刻の調査の打ち切り 03:22、終了 04:22:29、Windows の中の自動の停止 04:52 |
| 00:22 | `95_deadline_guard.sh` を裏で起動 | 04:22:29 に終了する |
| 00:23〜00:32 | `20_prepare.sh --execute` | Python 3.12.10・MetaTrader5 5.0.6231、道具を置き SHA-256 一致 |
| 00:5x（司令塔経由） | 人間の報告（原文）「設定して切断した」 | MT5 にログイン、最大バー数 Unlimited で再起動、チャート 0、RDP 切断 |
| 00:49〜00:56 | `35_time_survey.sh A2 --execute`（1 回目） | 10 区間すべてで**区間の中の足 0 本・区間の外 1 本**。端末: 最大バー数 100000000、接続中。評価は 8 週末が「読めない」（T5）と出たが、取り寄せ待ちの空振りなので追加の要求は出さなかった |
| 00:57〜00:58 | 道具に「区間の中が 0 本なら 10・30・60・120 秒待って同じ区間を出し直す」を足した（出し直しは要求の数に数えない。同じ区間の再実行の記録は、評価で最後のものだけを使う）。単体テストを追加し Windows に置き直した | 時刻の調査の済みの要求の数を 0 に戻した（同じ 10 区間をやり直すため） |
| 00:58〜01:35 | `35_time_survey.sh A2 --execute`（2 回目） | 10 区間すべてで、4 回の出し直しの後も区間の中の足 0 本 |
| 01:17〜01:25 | 端末のデータのフォルダのファイルの名前・大きさ・更新時刻と、端末の操作の記録（ジャーナル）の接続の行だけを確認（口座番号は伏せた） | `USDJPY.cl` の 1 分足の履歴は `2026.hcc` の 1 つ（0.0 MB）だけで、15:55 UTC の後に増えていない。ジャーナル: 15:47 UTC にログインと同期が成功 |
| 01:3x（司令塔経由） | 人間の決定（原文）「(a) tick 要求を 1 回だけ出す（USDJPY 2023-12-27 00:00〜00:05。返った tick は保存しない）（推奨）」 | 目的: 前回（10/15）は tick の要求を 1 分足の前に出していたので、それで端末の履歴の取り寄せが始まった可能性があるため |
| 01:36 | `36_sync_trigger.sh --execute`（tick の要求 1 回。値は保存しない） | 0 件（エラーなし）。端末は `ticks/USDJPY.cl/202601.tkc`〜`202610.tkc`（計 97.8 MB）を取り寄せた（中身は読んでいない）。1 分足の履歴は増えず |
| 01:37 | `30_run_probe.sh m1-USDJPY --execute`（1 分足 1 要求で確認） | 0 本。司令塔に (b)（人間の RDP の再接続と端末の再起動）を依頼 |
| 01:38 | 端末の logs フォルダのファイル名・大きさだけを記録 | `20261015.log`（8,872 バイト、15:55:53 UTC）、`metaeditor.log`（83,130 バイト） |
| 01:4x〜02:0x（司令塔経由） | 人間の RDP の再接続（依頼: 最大バー数の確認、操作履歴の確認、「表示→銘柄→USDJPY.cl→バー」で M1 の 2017.01.01〜2023.12.31 を Request（チャートは開かない。研究期間だけ）、再起動、切断） | **観察（人間の画面）: Request の結果が「0 bars, limited by charts settings」**。API の `terminal_info().maxbars` は 100000000 と返すのに、端末の実際の値としては最大バー数の設定が効いていない。原因の候補（未確認）: (1) 設定が保存される前に端末が終了した、(2) 設定（config）が反映される時機、(3) API が返す値と端末の実効の値の違い。人間に、最大バー数を一度別の値にしてから Unlimited に戻し、「ファイル」→「終了」で終了して再起動し、もう一度 Request することを依頼 |
| 02:0x〜02:1x（司令塔経由） | 人間の報告（原文）「再起動した」（最大バー数を Unlimited に再設定して再起動、「表示→銘柄→USDJPY.cl→バー」で M1 の 2017〜2023 を Request。「limited by charts settings」は消えた） | 端末の `USDJPY.cl` の 1 分足の履歴が `2012.hcc`〜`2026.hcc`（15 ファイル、296.8 MB）になった（02:08〜02:10 に確認。名前と大きさだけ） |
| 02:10 | `30_run_probe.sh m1-USDJPY --execute`（確認の 1 要求） | 2017-01-10 の 1 分足 120 本 |
| 02:11 | `35_time_survey.sh A2`（3 回目。時刻の調査の済みの要求の数を 0 に戻して同じ 10 区間をやり直した） | 10 区間すべて取れた。評価は下の K-9.2 |
| 02:11〜02:29 | `35_time_survey.sh` を extra（T2 の 2 要求）→ A1（23）→ B1（28）→ B2（6）→ extra（T4 の 14 要求）の順に実行 | 時刻の調査の要求は計 **83**（段 67 ＋ 決定木の追加 16。上限 87 の内）。司令塔の判断が要る事項（T3・上限の超過）は出なかった。ディスクの使用量 24.75 GB（空き 35.25 GB） |
| 02:29〜02:44 | `35_time_survey.sh G`（空白の測定） | **112 要求すべて**（出し直し 0、区間外 0）。足の時刻の列だけ（要約 12.4 MB） |
| 02:44 | 端末のデータのフォルダのファイルの名前・大きさ（`<scratch>/bases_listing_2026-10-16.txt`、SHA-256 `81a04f3243685a5476b7cd961e8f8444035fdfbe8141f80479edf17afa669bf5`。口座番号のフォルダ名は伏せた） | 1 分足の履歴: USDJPY.cl 2012〜2026、USDCHF.cl 2015〜2026、他の 6 通貨 2016〜2026（各 約 231 MB）。tick は USDJPY.cl の 2026-01〜10 だけ（K-9 の tick の要求 1 回による）。**2024 年以降の自動の取り寄せ分は読んでいない・回収していない** |
| 02:45:57 | `90_teardown.sh --execute` | インスタンス `i-0d04426839a9fe9c8` を終了、セキュリティグループ `sg-01d0b9e15874c8010`・キーペア `odyssey-mt5-probe-2026-10-16`・Mac の .pem を削除。**残りの確認: 試験のインスタンス（終了済みを除く）0、試験のボリューム 0、使われていないボリューム 0、自分のスナップショット 0、自分の AMI 0、Elastic IP 0、試験のネットワークインターフェース 0、試験のセキュリティグループ 0、試験のキーペア 0**。人間が作った IAM のもの・Budgets は消していない |

- 道具の変更（この回の中）: 区間の中が 0 本のときの出し直し（`SURVEY_RETRY_WAITS`）、同じ区間の再実行の記録は最後のものだけを評価に使う、tick の要求 1 回の段 `synctrigger` と `36_sync_trigger.sh`、`35_time_survey.sh` が作成済みの E1 の参照を使う。単体テストを追加。
- **取り寄せが始まらなかった原因（観察）**: API の  は 100000000（Unlimited）を返していたのに、端末の「銘柄→バー」の Request は「0 bars, limited by charts settings」と表示した。人間が最大バー数を一度別の値にしてから Unlimited に戻して再起動すると、取り寄せが始まった。原因の候補（未確認）: 設定が保存される前の終了、設定の反映の時機、API の値と端末の実効の値の違い。次の起動の手順（K-8.3 の 5）に「一度別の値にしてから Unlimited に戻す」を足すのがよい。

#### K-9.2 時刻の調査の結果（D12 §11.7.7 の 4 区分。評価は `ts-eval/report.md`、SHA-256 `01e29e1e415f99356706f4fedf018dd5a0f083c9cddff6b831b0866742e5de7f`）

観察の要約: 週末の区間 **58 週末（USDJPY 28、他の 7 通貨 30）は全部読めて、全部が仮説 US の列に合った**（閉場・開場のずれ: 両方冬 +2/+2、米国だけ夏の春 +2/+3、両方夏 +3/+3、米国だけ夏の秋 +3/+3。丸めの残りは閉場 −5〜0 分、開場 0〜+14 分）。仮説 EU・固定の列にだけ合う週末は無かった。

| 区分 | 範囲と根拠 |
|---|---|
| **採用可能な範囲（直接確認）**（**v0.16 注記**: S2・S4 の週末の「仮説 US」の読みは日曜の開場の側だけによる。開場は取引時間の設定で決まる可能性があり（K-9.6）、K-11 の金曜の閉場は欧州式の側を示した。本文 第11.7.10節の要決定 D12-55 で、この読みを「US と説明 X の両方と整合」に改める案を示した。下の記録は変えずに残す。**v0.17: D12-55【決定】(a)「「米国式と、取引時間設定による説明の両方と整合（時計の規則の判別には使えない）」に改め、直接確認から仮定付きに下げる。旧記録は残す」により、S2・S4 の週末の「仮説 US」の読みは時計の規則の判別には使わず、それで直接確認とした範囲は仮定付きに下げた（今の区分は本文 第11.7.10節の表 版 1）。下の旧の記録はそのまま残す**） | (1) **USDJPY 2017〜2023 の各年の S1・S3・S4 の週**と **2018・2020〜2023 の S2 の週**: 各年 4 つの週末（E2・E3）が仮説 US だけで説明でき、同じ年の別の週末も同じ仮説（§11.7.2a の CSV の無い場合の組合せ）。2023 年は火曜の E1 も一致（2023-03-07 はずれ +2 時間で 118 本中 118 本、2023-03-14 は +3 時間で 115 本中 115 本が 4 値とも完全一致）。(2) **他の通貨の 2023 年の S2・S3 の週**（EURJPY・EURUSD・USDCHF・AUDJPY・GBPUSD・AUDUSD）と **GBPUSD の 2019 年の S2・S3 の週**: E2・E3 が仮説 US で、金曜の最後と日曜の最初の足の時刻が USDJPY と同じ分（E4）、USDJPY のその年が確定。価格基準: どの要求も `chart_mode` 0（bid で足を作る） |
| **推定範囲（未確認）** | USDJPY の 2017〜2023 の直接確認した週の間（各年の 4 つの週がすべて仮説 US なので、その間も同じ規則と推定）。全期間の 1 分足の取得の後に、全週の週末の境界（E2）を検査すれば直接確認に移せる（§11.7.4 の 2） |
| **採用を妨げる未解決事項** | (1) **2017 年と 2019 年の米国だけ夏時間の春（S2）の週で、E1 と E2 が割れた（T6）**: 週末の境界（2017-03-12・2019-03-10 の日曜の開場）は +3 時間（仮説 US）なのに、配布 CSV との差が最小になるずれは火曜 2017-03-14・2019-03-12・2019-03-19・2019-03-26 のどれも +2 時間（4 値の完全一致は 0 本、終値の差の中央値 3〜6 ポイント、+3 時間では 51〜57 ポイント）。EURJPY・EURUSD・GBPJPY の 2019-03-19 も +2 時間が最小。2023-03-14 は +3 時間で完全一致。どちら（API の履歴か、配布 CSV の時刻の規則か）がずれているかは決められない。評価の道具は、完全一致しない E1 を「証拠なし」として扱ったため、T6 の読み直しの要求を自動で出さなかった（道具の不足）。(2) **他の通貨の 2019 年（GBPUSD を除く）と GBPJPY の 2023 年**: E2・E3 はすべて仮説 US だが、金曜の最後か日曜の最初の足の時刻が USDJPY と 1 分（GBPJPY 2023-07-16 の開場は 14 分）違い、E4 を「同じ分」とする判定で揃わなかった（T4 で S1・S4 の週末を足したが、同じ理由で揃わず）。E4 を「同じ時間の境目」で読めば揃う。判定の基準の決定が要る。(3) 他の通貨の 2017・2018・2020〜2022 年は要求していない（未確認）。(4) 2019-03-19 の火曜の −2 時間（K-5）は (1) と同じ現象 |
| **採用を妨げない差** | 配布 CSV との価格の差: 2017-03・2019-03 は 4 値の完全一致 0 本（差の中央値 3〜18 ポイント、ずれ +2 時間）、2023-03 は完全一致（D12-32 により採用条件にしない）。週末の閉場の最後の足の位置の分単位の違い（0〜5 分早い） |

- 要求の数: 時刻の調査 83（段 67 ＋ 追加 16）。同じ区間のやり直し（A2 の 1・2 回目、各区間の出し直し）は数えていない。ほかに確認の要求 2（1 分足 USDJPY 2017-01-10 を 2 回）と tick の要求 1。
- 回収した要約の SHA-256（`data/raw/oanda_mt5_probe/2026-10-16/`）: A2-1 `57cd4e4c…`・A2-2 `750afc85…`（どちらも 0 本）、A2-3 `3efcd0a5b61b4478afdfe2de8e3b30e812db8a66211ea1315d99dd581fd3e52a`、extra-4 `286fdbe961a5d292a31ebd7ae659e154fade1bec591f5488a2cd4260f43df920`、A1-5 `ba1a66a27b1562abe0e13df424d71969f7189db459dd1812c189782e9359eb62`、B1-6 `39a05aa37542b02a172f97a6441377bcc22ae7812b3fe08fe1d1396de77d92e2`、B2-7 `1e5aac79d26dab00f4bd34fbd8e620dfd87c7e183c95e9f26776e2c01bfe813d`、extra-8 `55e21b384c279b8b590f07bdc96d6e2669fe448f7c94f0c5f718535e08b9ca56`、sync-trigger `3c4bf9ccc7dd3e19d17f312522318dea8b1c80cb89e68fb4d9f2e1b73a20fedf`。

#### K-9.3 空白の測定（段 G。D12 §11.9）

- 取得: 112 要求すべて（8 通貨 × 2017〜2023 × W1・W2）。足の時刻の列だけを保存（要約 `ts-G-9/summary_ts-G.json`、SHA-256 `9a8c23daaaa38757c3b5579ee83604a88c0c9f427893e79a099d4a604bfc6dbe`、12.4 MB）。
- 1 週あたりの足の数（参考。空白の判定ではない）: W1 は 7,008〜7,195、W2 は 5,215〜7,164。

| 区分 | 内容 |
|---|---|
| 測定範囲の結果 | 足の時刻の取得まで。**空白の集計（§11.9.3 の M1〜M6）はまだ行っていない** |
| 全期間では未確認のこと | 代表週の外のすべての週（D12-39 の原文により、代表週の空白から全期間の実行不能を断定しない） |
| 未集計 | 112 週すべて。時刻の規則は USDJPY 2017〜2023 が仮説 US で直接確認（2017・2019 の S2 を除く）。W1（6 月、S3）・W2（12 月、S1）は S2 を含まないので USDJPY は集計できる見込みだが、他の通貨は 2017・2018・2020〜2022 の時刻が未確認で、未集計のまま残す |
| カレンダーに無い休場の候補 | 集計の前なので、まだ判定していない（推測で空白を判定しない） |

#### K-9.4 費用の実績の概算（米ドル、税込み。Cost Explorer の反映は翌日）

| 項目 | 計算 | 金額 |
|---|---|---|
| インスタンス・ディスク 60 GB・IPv4 | 2.39 時間（00:22:29〜02:45:57） | 約 0.226 |
| 送信（RDP・SSM の応答。要約は計 約 13 MB） | 0.1 GB 以下 × 0.114 | 約 0.013 以下 |
| この回の合計 | | **約 0.24** |
| これまでの合計（K-4 の約 0.24 と合わせて） | | **約 0.48**（試験予算 5 の残り 約 4.52、全体予算 100 の残り 約 99.52） |

- 人間が翌日に確かめること: 請求書と Cost Explorer で EC2・EBS・IPv4 の料金が止まり、2 回の合計が約 0.48 前後であること。


#### K-9.5 通貨の間の同じ時刻の判定（E4）の再判定（人間の第 11 回の決定。既取得のデータだけ。2026-10-16 作業役）

人間の決定（原文）: 「（a）を選びます。ただし、±15分は境界付近の更新時刻の差を許容する補助基準とし、同一時刻と認定する意味には使いません。閉場・開場・夏時間切替の前後を組み合わせ、同じ時刻規則を支持するか判定してください。分単位の残差は保存し、元データの時刻は変更しません。」

- 判定の規則（道具 `tools/survey/mt5_time_survey.py` の `e4_compare`）: 同じ週末の USDJPY と比べ、(1) 金曜の閉場と日曜の開場の**ずれの時間（E2・E3）が同じ**で、(2) 金曜の最後の足と日曜の最初の足の**分単位の差が ±15 分以内**なら「同じ時刻の規則を支持する」。±15 分は境界付近の更新時刻の差の許容（補助）で、「同じ時刻」とは認定しない。1 時間の差は許容で吸収しない（単体テストで確認）。これを閉場・開場・切替の前後（S1〜S4）の週末の組合せで読む。
- 残差の表: `data/raw/oanda_mt5_probe/2026-10-16/e4_residuals.csv`（84 行 = 7 通貨 × 42 週末の境界、時刻だけ。SHA-256 `e1e844cb0cce9a0476a40ca6c17b7c0845fbb772438f6fa6a610afbaa5bd1042`）。評価の再実行 `ts-eval-r11/report.md`（`14d391c29e03595799e503bc180152d4683f191fd98db0062ac2fd163f6083ae`）。元の要約・元の評価（`ts-eval/`）は変えていない。
- 結果: **42 週末すべてで、ずれの時間が USDJPY と同じ**、分単位の差は閉場 0〜+1 分、開場 −1〜+14 分（+14 は GBPJPY 2023-07-16 の開場。他は ±1 分以内）。

| 区分 | 通貨 × 年（週末の状態） |
|---|---|
| **直接確認**（USDJPY と同じ時刻の規則を支持） | EURJPY・EURUSD・GBPJPY・USDCHF・AUDJPY・AUDUSD の 2019 年の S1・S3・S4 の週、GBPUSD の 2019 年の S3 の週、GBPJPY の 2023 年の S1〜S4 の週、他の 6 通貨の 2023 年の S2・S3 の週 |
| **推定（未確認）** | 上の通貨の 2019・2023 年の、直接確認した週の間（GBPUSD 2019 の S1・S4、GBPJPY 以外の 2023 の S1・S4 を含む。切替の前後のどちらかしか見ていない） |
| **採用を妨げる未解決** | (1) **7 通貨の 2019 年の S2 の週**（USDJPY の S2 の割れ（K-9.6）をそのまま引き継ぐ。EURJPY・EURUSD・GBPJPY の 2019-03-19 の E1 も +2 時間が最小）。(2) 7 通貨の 2017・2018・2020〜2022 年（要求していない。K-9.2 から変えない） |
| **採用を妨げない差** | 境界の足の位置の分単位の差（閉場 0〜+1 分、開場 −1〜+14 分）。残差は表に残し、元の時刻は変えない |

- K-9.2 の「採用を妨げる未解決」の (2)（E4 の基準）は、この再判定で閉じる。

#### K-9.6 2017・2019 年の春の週（S2）の割れ: 既取得のデータと比較の処理の点検（人間の第 11 回の決定の 1・3。AWS は使っていない）

使ったもの: 配布 CSV `ticks_USDJPY-oj5k_{2017,2019,2023}-03.csv`、K-5 で保存した API の 1 分足（USDJPY 2019-03-05・2019-03-19・2023-03-28 の各 2 時間）、時刻の調査の要約。出力は `data/raw/oanda_mt5_probe/2026-10-16/s2_audit/`（差の統計と時刻だけ）。**価格の水準は書かない**（差は point = 0.001 円の単位）。

| 点検の項目 | 方法 | 結果 |
|---|---|---|
| (a) 配布 CSV の時刻の UTC 変換（`server_time_to_utc`: 元の時刻 − 7 時間をニューヨークの現地時刻とみなす） | CSV の元の時刻で週末の空白の位置を読む（`csv-boundaries`。2017-03・2019-03・2023-03） | 2017・2019 年は**米国の切替（2017-03-12・2019-03-10）の前後も、欧州の切替（2017-03-26）の前後も、毎週 金曜 23:59 → 月曜 00:00（元の時刻）、空白 48.0 時間**。変換後の日曜の開場は、米国の冬 22:00 UTC、米国の夏 21:00 UTC（どちらもニューヨーク 17:00）。CSV が欧州の規則なら米欧の間の週で 47 時間か 49 時間になるはず → **CSV は米国の規則（ニューヨーク + 7 時間）で、変換は正しい**。付録 F-3（2019-03 の月全体で FXCM の UTC とずらし 0 分）とも合う。切替の時間帯（日曜の朝）は休場なので、変換の境目に tick は無い |
| (b) 足の開始時刻のラベル | 道具の読み直しと K-5.3 (c) | API の time は足の始め（MqlRates「Period start time」）。CSV の足も分の始め（`ms // 60000 * 60`）、半開区間 [始め, 始め + 1 分)。週末の閉場は最後の足の終わり（始め + 1 分）で読む。境界を ±1 分ずらしても差は最小にならない（K-5.3 (c)） → **誤りなし** |
| (c) 比較区間の切り出し | 道具の読み直し | E1 の参照は火曜の 2 時間の前後 5 時間（ずれ −4〜+4 時間を覆う）、ずれごとに比べた足は 115〜120 本で、端で欠けるのはずれ ±3〜4 時間の 1〜5 本だけ。月のファイルの境目（前月末 17:00 から）は火曜の区間に掛からない。週末は API の時刻の値で金曜 18:00〜月曜 02:00 → **誤りなし** |
| (d) 「完全一致しない比較を証拠なし」の判定 | 道具の読み直し | **箇所: `read_e1` が 4 値の完全一致 90% 未満なら仮説を空にし、`evaluate` が完全一致の E1 だけを T6 の材料にしていた**。そのため 2017・2019 年の S2 の火曜（完全一致 0 本、差の最小は +2 時間）は T6 にならず、予定していた読み直しの要求を出さなかった（調査漏れ。人間の指摘）。**修正**: 完全一致しない E1 も「差のみの証拠」（`evidence: diff_only`、差が最小のずれ、2 番目との比、その仮説）として記録し、週末の仮説と違えば T6 を出し、読み直しの要求と司令塔への事項を出す。差のみの証拠は**確定には使わない**（その状態の週を「未解決」と表示）。単体テスト 7 件を追加。再実行の結果、T6 が 2017 年（2017-03-14）と 2019 年（2019-03-12・2019-03-26）で出て、読み直し 3 要求が計画に入った（`ts-eval-r11/extra_plan.json`、`20c7b53aa15ca85f531d26b7684c1d455c88cdccabeef87e5be8522340899102`） |

補助の比較（bid・ask・mid。人間の決定の 3「価格差が最小という理由だけで時刻を確定しない」）: K-5 で保存した API の 1 分足を、配布 CSV の bid・ask・mid の 1 分足とずれごとに比べた（`s2-audit`。終値の差の中央値、point）。

| 日（状態） | bid: +2 h / +3 h | ask: +2 h / +3 h | mid: +2 h / +3 h | 4 値の完全一致 |
|---|---|---|---|---|
| 2019-03-05（S1、対照） | 4 / 11 | 9 / 15 | 5.5 / 7.5 | bid +2 h で 1 本 |
| **2019-03-19（S2）** | **5 / 17** | **17 / 35** | **6.5 / 23.5** | 0 本 |
| 2023-03-28（S3、対照） | 120 / **0** | 112 / 37 | 116 / 18.5 | bid +3 h で 110 本中 110 本 |

- 2017 年の S2（2017-03-14）は API の価格を保存していないので、bid の差の統計（時刻の調査の E1。+2 h で 4、+3 h で 51）だけ。ask・mid は既取得のデータでは比べられない。
- SHA-256: `basis_2019-03-05.json` `b60fe1214bad32aa3be2252ff5eeaa0bb37f4c38e551117c97b308ab8c719092`、`basis_2019-03-19.json` `d51838bfd847470625c8702a94ed1ea54025b296abef2cdff6dc2f618e01f2c3`、`basis_2023-03-28.json` `8fd86d485488b7d52e419bec2585e4747f46c77fe46ae4f73f01f9e262d9fa88`、`csv_boundaries_USDJPY_2017-03.json` `bf03c69033cbdeb92bccfb5c92a35e88db105aac49c0205d1ba0cc3c1ac98f0e`、`_2019-03.json` `75ab42ec068f5379517835d9fe5aac73fb9b109a7ea16453f258e3643e39b749`、`_2023-03.json` `f4d1c423c20692753e756b06e6e1f6a08337ab0bb4aac7f60f71e9dfe88795ba`。道具 `tools/survey/mt5_time_survey.py` `749c03b0656b5424a11c84de6275e5eb0c6fee78f14fadc5638a8b35c61e37ee`。

**点検の結論: S2 の割れは「処理の誤り」では説明できない**（UTC 変換・開始時刻のラベル・区間の切り出しに誤りは無い。誤りは (d) の判定の論理だけで、これは割れを生んだのではなく、割れの追加確認を止めていた）。bid・ask・mid のどれでも +2 時間が最小（補助。確定には使わない）。

新しく分かったこと（観察と推定。断定しない）:

1. **週末の開場の位置は、時計のずれではなくサーバーの取引時間の設定で決まっている可能性がある（推定）**。2023-03 の配布 CSV には毎週 日曜 22:00〜24:00（元の時刻）に気配がある（2023-03-12 は約 420 件）が、API の 1 分足は同じ時間帯に 1 本も無く、月曜 00:00 から始まる（2023 年は API の時刻 = CSV の元の時刻が完全一致で確かめ済み）。API は取引時間の外の気配から足を作らないと読める。
2. そうだとすると、**仮説 US と EU を分けていたのは日曜の開場の側だけ**（S2: US (2,3)・EU (2,2)、S4: US (3,3)・EU (3,2)）で、「サーバーの時計が欧州の規則でも、取引時間が月曜 00:00（サーバーの時計）から」なら、開場は US と同じ位置に見える。つまり **K-9.2 の週末（E2・E3）は、US と EU を見分ける根拠として弱い**。US と EU を直接見分けた材料は、2023 年の E1（完全一致、+3 時間）だけで、2017・2019 年の E1 は差のみの証拠で +2 時間（EU 側）を示す。
3. 見分けられる材料（既取得のデータには無い）: **米欧の切替日の間の週の「金曜の閉場」**。この週は金曜も米国だけ夏時間なので、閉場（ニューヨーク 17:00 = 21:00 UTC）は、US なら API の時刻 24:00、EU なら 23:00 に来る（取引時間の終わりで隠れない）。今回の S2 の週末（米国の切替の日曜）は金曜がまだ冬時間で、閉場では見分けられない。
4. 2017-03-12 の日曜の開場は、CSV の最初の気配が月曜 00:00:05（元の時刻）、API の最初の足が 00:05（API の時刻）で、5 分の差がある（分単位の残差として記録だけ）。

【提案】（要決定。K-10 の承認と一緒に判断が要る）2018・2020〜2022 年の S2・S4 の週は、US と EU を分ける根拠が開場の側だけなので、K-9.2 の「直接確認」から「推定（未確認）」に下げる。

**v0.14 注記**: 人間の第 12 回の決定（D12-43）で、調査を閉じるときの区分は「確認済み／仮定付き」になった。この【提案】は新方針の下で「仮定付き（仮説 US）にするか」の問いに置き換え、本文の第11.7.9節（要決定 D12-45）に移した。

#### K-9.7 評価の道具に現行基準（判定条件 版 1）を当てた区分（人間の第 14 回の回答。既取得のデータだけ。2026-10-16 作業役。AWS は使っていない）

人間の決定（原文）: 「(a)を選びます。観測した境界の一致と、現行基準による確認済み／仮定付きの区分を分けてください。現行区分にはC5・C8を適用し、旧記録は保存して差分を追記してください。追加取得・AWS再開は行わず、人工データと取得済み記録で検証してください。」

- **旧の記録（K-9.2・K-9.5）は変えずに残す**。この節は、同じ要約を今の道具で読み直した結果と、旧の記録との差分だけを足す。
- 道具の変更（`tools/survey/mt5_time_survey.py` の `current_classification`）: `evaluate` の報告を「観測」（週末の閉場・開場のずれ、USDJPY との一致（E4）、配布 CSV との照合（bid）の有無と結果）と「現行基準の区分」（本文 第11.7.9節の判定条件 版 1。確認済み／仮定付き）に分けた。調査中の 4 区分（直接確認・推定・未解決）は「観測の読み」として補助の表示に残し、「確認済み」の語は現行基準の列にだけ出す。
  - 確認済みにするのは、その通貨・年・状態の火曜の **bid の 4 値の完全一致**（C2-b）が候補（US・EU）のうち 1 つにだけ合い（C8 (iii)）、同じ状態の他の bid の照合（完全一致・差の統計）・金曜の閉場・週末の読みと食い違わないときだけ（C7 (c)）。範囲は照合のある週だけ（C5）。
  - 配布 CSV との照合が無い通貨・月は仮定付き（C8 (i)。境界と E4 の一致だけでは移さない。C4・C5）。差の統計だけのものも仮定付き（C4）。食い違えば仮定付きにし、通知を出す（追加の要求は出さない）。
  - 米欧の間の週の金曜の閉場（C2-a）は、この段の要求に無いので当てていない（最後の調査の範囲。下の K-11 の項）。
- 単体テスト（人工のデータ）: 配布 CSV の無い通貨は境界が一致しても仮定付き、配布 CSV のある通貨で bid の完全一致と差の統計が整合すれば確認済み（その週だけ）、食い違えば仮定付き＋通知（人間の再現ケースと、完全一致と差の統計の割れ）、候補の間で同じ変換の完全一致は仮定付き。
- 出力（`data/raw/oanda_mt5_probe/2026-10-16/`。既存のファイルは変えていない）: `ts-eval-r14/report.md` `fe143855d4c352017bec863f849a3ef34fae1c012981b4657fe1ef56a9bb54c8`、`ts-eval-r14/plan.json` `e40686f1189581081e8e5b084f7331df4cf10e22f4d2b9f57cbfdd15f8bb4bd4`、`ts-eval-r14/e4_residuals.csv` `e1e844cb0cce9a0476a40ca6c17b7c0845fbb772438f6fa6a610afbaa5bd1042`（K-9.5 の残差の表と同じハッシュ）、`k10-eval-r14/report.md` `3bf15b991393f8b99f4d4f8856369480e91badf9f9a801ca2d87d349134b2789`（K-11 の報告と同じ内容）、`k10-eval-r14/result.json` `3ea4d3c20b7cf930a45eff4e66450af2527ab6e2b30c60ea1e0bd1bc552fae64`。道具 `fa4e1e6d9d619e5d1b0c05c8a50eeaaed78f236a26b007abc38faa8c8a38cf68`。

**現行基準の区分（K-9.5 の 42 週末。他の 7 通貨）**: すべて仮定付き。

| 通貨 × 年 × 状態 | 観測（変えていない） | 現行基準の区分 | 理由 |
|---|---|---|---|
| EURJPY・EURUSD・GBPJPY の 2019 年の S1・S2（6） | 境界は USDJPY と同じ規則を支持。火曜の照合は差の統計だけ（S1・S2 とも最小 +2 h） | 仮定付き | C4（差の統計だけでは確定の根拠にしない） |
| 上の 3 通貨の 2019 年の S3・S4、USDCHF・AUDJPY・AUDUSD の 2019 年の S1〜S4、GBPUSD の 2019 年の S2・S3、7 通貨の 2023 年（36） | 境界は USDJPY と同じ規則を支持（GBPJPY 2023-07-16 の開場の +14 分を含む） | 仮定付き | C8 (i)（配布 CSV との照合なし。境界のみ） |

**旧の記録（K-9.5）との差分**（通貨 × 年 × 状態の 42 件）:

| 旧の区分（K-9.5） | 件数 | 現行基準の区分 | 差分 |
|---|---|---|---|
| 直接確認 | 35（EURJPY・EURUSD・GBPJPY・USDCHF・AUDJPY・AUDUSD の 2019 年の S1・S3・S4 で 18、GBPUSD の 2019 年の S3 で 1、GBPJPY の 2023 年の S1〜S4 で 4、他の 6 通貨の 2023 年の S2・S3 で 12） | 仮定付き | **35 件が変わる**（E4 の一致と境界だけでは確認済みにしない。C4・C5・C8） |
| 採用を妨げる未解決（2019 年の S2） | 7 | 仮定付き | 区分の名だけ（本文 第11.7.9節の 4 区分からの移し方のとおり） |

- USDJPY（K-9.2 の 28 週末と火曜の照合）: 現行基準で確認済みが残るのは **2023 年の S2（2023-03-14 の週。bid の完全一致 +3 h、115 本中 115 本。US）だけ**。2023-03-07（S1）の完全一致は候補の間で同じ変換なので仮定付き（C8 (iii)、D12-53 (b)）。2017・2019 年の火曜は差の統計だけ（C4）、他は照合なし（C8 (i)）。これは本文 第11.7.10節の表 版 1 の確認済みの行と同じ範囲。
- 現行基準の通知は 0 件（取得済みの記録には、bid の完全一致と食い違う観測が無い）。観測の側の T6 の分岐（v0.19 の修正。EURJPY・EURUSD・GBPJPY の 2019-03-19 ほか）は変わらない。
- **K-11（最後の調査）**: 区分は**仮定付きのまま変えない**（D12-52 の原文「今回の調査結果は仮定付きのまま保持します。」、本文 第11.7.9節の「やり直さない」、表 版 1 の行）。`final-evaluate` の再実行の出力は K-11 と同じ。注（作業役の観察。判定には使っていない）: 判定の週末の bid の照合は、比べられた 11 の読みがすべて欧州式の期待のずれに合い（差の統計だけ。2019-03-31 の月曜の側は比べられない）、閉場は 5 週末とも +2 h、対照は +2/+2 なので、版 1 の C2-a・C3・C7 を文言どおりに当てると満たすとも読める。ただし C7 の「閉場と同じずれ」を、側ごとの期待（2017-03-26 の月曜の側は欧州式で +3 h）と読むか、閉場の時間数そのものと読むかで読みが割れる。

### K-10. 最後の調査の計画（D12-41。**v0.14: 人間の第 12 回の決定で修正付きで承認（D12-42）。v0.15: 2026-10-16 に人間が起動を合図（記録は K-11）**。2026-10-16 作業役。終了条件は v0.14 で設計役が改めた）

対応: 人間の決定 D12-41 の 4 段（1. 点検 → 2. 足りない情報だけ追加取得 → 3. bid・ask・mid は補助 → 4. 確定できなければ除外して閉じる）。**v0.14 注記**: 段 4 の「除外して閉じる」は第 11 回の決定の原文（本文 第0.1.14節）のまま残すが、**人間の第 12 回の決定（本文 第0.1.15節、D12-43）で変更された**。判別できない範囲は除外せず、「仮定付き」として閉じる（K-10.5）。範囲（K-10.1・K-10.2）・上限と費用（K-10.3）・人間の作業（K-10.4）は承認どおりで変えていない。段 1 は K-9.6 で済んだ。この計画は段 2〜4。**2024 年以降は要求しない**。AWS の再開は 1 回だけ。

#### K-10.1 足りない情報（既取得で足りるものは要求しない）

| 足りない情報 | なぜ要るか | 既取得で足りるもの（要求しない） |
|---|---|---|
| (A) 米欧の切替日の間の週の**金曜の閉場の位置**（API の時刻） | US と EU を時刻だけで見分けられる唯一の境界（K-9.6 の 3） | 米国の切替の週末（2017-03-12・2019-03-10）、S1・S3・S4 の週末 |
| (B) その週末の前後の足と配布 CSV の bid・ask・mid の差の統計（ずれごと） | 補助。(A) と同じずれを支持するかを見る | 2019-03-19 の bid・ask・mid（K-9.6）、2017-03-07・03-28 と 2019-03-05 の bid |
| (C) 2017 年の S2 の火曜の ask・mid（2017-03-14 の読み直し = T6 の予定していた追加確認） | 2017 年は API の価格を保存しておらず、bid しか無い | 2019 年の S2 の ask・mid（K-9.6 で済み。2019 年の T6 の読み直し 2 要求はこれで代える） |
| (D) 切替の前の週末（対照） | 週末の境界の前後の比べ方が、確かに分かっている冬の週で正しいかを確かめる | 切替の後: 2017-03-28 の E1（+3 時間）。2019 年の欧州の切替の後は配布 CSV が無く（2019-03 は 03-29 まで）、US と EU が同じずれなので要求しない |

#### K-10.2 要求の一覧（USDJPY だけ。1 分足。価格はメモリの中で比べ、差の統計と時刻だけを保存）

| # | 区分 | 要求（API の時刻の値） | 配布 CSV | 見るもの |
|---|---|---|---|---|
| 1 | 切替の前（対照） | 週末 2017-03-05（金曜 03-03 18:00〜月曜 03-06 02:00） | 2017-03 | 閉場・開場の位置、境界の前後の bid・ask・mid の差 |
| 2 | 切替の前（対照） | 週末 2019-03-03 | 2019-03 | 同上 |
| 3 | 米欧の間 | 週末 2017-03-19（金曜 03-17 の閉場） | 2017-03 | **(A)** と (B) |
| 4 | 米欧の間 | 週末 2017-03-26（金曜 03-24 の閉場。日曜は欧州の切替日） | 2017-03 | **(A)** と (B) |
| 5 | 米欧の間 | 週末 2019-03-17 | 2019-03 | **(A)** と (B) |
| 6 | 米欧の間 | 週末 2019-03-24 | 2019-03 | **(A)** と (B) |
| 7 | 米欧の間 | 週末 2019-03-31（金曜 03-29 の閉場） | 2019-03（03-29 まで） | **(A)** と (B) |
| 8 | 米欧の間 | 火曜 2017-03-21 00:00〜02:00 | 2017-03 | (B)（2017 年の S2 の 2 つ目の火曜） |
| 9 | 米欧の間 | 火曜 2017-03-14 の読み直し | 2017-03 | **(C)**（T6 の予定していた追加確認） |

- 要求の数: **計画 9、上限 12**（同じ区間の出し直しは数えない。上限の残り 3 は、端末の取り寄せの空振りのやり直しだけに使う。他の区間は足さない）。
- 道具の変更（承認の後、実行の前に行う。単体テスト付き）: 週末の区間にも E1 と同じ照合をする（配布 CSV の参照を週末の区間にも作る）、照合に ask・mid を加える、状態「米欧の間の週末」（金曜・日曜とも米国だけ夏時間。US (3,3)・EU (2,2)・固定 (2,2)）を判定の表に加える。
- **v0.15（作業役の道具の事実。道具は 92fb8e5）**:
  1. **2017-03-26 と 2019-03-31 の週末（要求 4・7）は、日曜が欧州の切替日**で、日曜の開場は米国・欧州とも夏時間になる。そこで状態「米欧の間の週末」（**S2M**: US (3,3)・EU (2,2)・固定 (2,2)）とは別の状態 **S2E**（US (3,3)・EU (2,3)・固定 (2,2)）とした。判定に使う**金曜の閉場の予想は S2M と同じ**（US +3、EU +2、固定 +2）なので、K-10.5 の (i) の判定（要求 3〜7 の金曜の閉場が同じずれ）は変わらない。違うのは日曜の開場の予想だけで、開場は判定に使わない（K-9.6 の観察 1・2）。
  2. **閉場のずれが +2 時間の場合**、判定の週末（S2M・S2E）では仮説 EU と仮説 固定が同じ変換を与えるので、道具は「確認済み」の規則の名を **「EU, FIXED」と併記**し、「この期間の変換はどちらでも同じ」と注記する（K-10.5）。

#### K-10.3 上限・費用（米ドル、税込み。単価は K-4・K-9.4 と同じ。1 時間 約 0.094）

| 項目 | 想定 | 上限 |
|---|---|---|
| 作業時間（起動から片付けまで） | 約 1.5 時間（人間の RDP の作業と USDJPY 2017・2019 年の履歴の取り寄せを含む） | **作業上限 1.5 時間**で打ち切り、2 時間で終了、2 時間 30 分で Windows の中の自動の停止。止め忘れを含めて 3 時間で見積 |
| ディスク | 60 GB（gp3） | 60 GB |
| 要求 | 9 | 12 |
| 送信 | 0.1 GB 以下 | 1 GB |
| この回の費用 | **約 0.16** | **約 0.40** |
| 累計（K-9.4 の 約 0.48 と合算） | 約 0.64（試験予算 5 の残り 約 4.36、全体予算 100 の残り 約 99.36） | 約 0.88（残り 約 4.12・約 99.12） |

#### K-10.4 人間の作業（K-8.3 の改訂。起動の後、作業役の合図で）

1. K-8.3 の 1〜4（パスワードを出す、RDP で接続、端末を入れる、サブアカウントにログイン）。
2. 「ツール」→「オプション」→「チャート」→「チャートの最大バー数」を**一度別の値（例: 100000）にして OK、もう一度開いて Unlimited にして OK**。「ファイル」→「終了」で端末を終了し、起動し直す（K-9.1 の「0 bars, limited by charts settings」の対処）。
3. 「表示」→「銘柄」→ `USDJPY.cl` →「バー」で、M1・**2017.01.01〜2019.12.31** を Request する（チャートは開かない。研究期間の中だけ）。「limited by charts settings」が出ないことを確かめる。
4. チャートの窓・気配値表示の窓をすべて閉じ、RDP を「切断」にする。司令塔に「設定して切断した」と伝える。
- 人間の時間: 約 20〜25 分。

#### K-10.5 終了条件（どちらかで必ず閉じる。v0.14 で人間の第 12 回の決定に合わせて改めた）

人間の決定（第 12 回、原文）: 「修正してから承認します。追加調査の範囲・時間・費用には同意しますが、判別できなければ全通貨を自動除外する終了条件を変更してください。」（1〜4 の全文と「この方針は従来の「時刻未確定なら除外」からの設計変更として記録してください。」は本文 第0.1.15節）

- **(i) 確認済みとして閉じる**: 要求 3〜7 の金曜の閉場の位置が**すべて同じずれ**（US なら API の時刻 24:00、EU なら 23:00。±15 分は境界付近の更新時刻の差の許容だけに使う）を示し、要求 1・2 の対照が既知のずれ（+2 時間）で読め、補助の比較（bid・ask・mid の差の統計）がそのずれと矛盾しなければ、2017・2019 年の米欧の間の週の時刻の規則をそのずれで特定する。~~US でなかった場合の扱い（その区間だけ別のずれで直すか、除外するか）は、結果を見てから人間が決める（作業役は決めない）。~~ **v0.14（第 12 回の決定 1）**: 根拠付きで特定できた規則で変換して採用する。**米国式でないこと自体は除外の理由にしない**。記録すること: 特定した規則、根拠（どの要求のどの証拠か）、**規則の適用期間**（どの週からどの週まで。2017・2019 年の春の米欧の間の週を超えて当てはめるなら、その根拠）、**他の通貨へ適用できる根拠**（K-10 は USDJPY だけなので、K-9.5 の E4 の再判定（同じ週末で USDJPY と同じ規則を支持）を根拠として書けるか、書けない通貨はどれか）。
- ~~**(ii) 除外して閉じる**: 閉場の位置が週で割れる、読めない、または補助の比較が閉場と別のずれを支持するなら、**影響しうる区間を採用対象から除外し、この調査を閉じる**。除外の区間（【提案】）: 2017-03-12〜2017-03-26、2019-03-10〜2019-03-31（各年の米国の切替日〜欧州の切替日、約 2〜3 週）の全通貨。同じ理由で影響しうる秋の米欧の間（2017-10-29〜11-05、2019-10-27〜11-03、各約 1 週）も除外の候補として示す（要決定）。~~ **v0.14 で削除（第 12 回の決定で変更）**。
- **(ii) 仮定付きとして閉じる**（v0.14。第 12 回の決定 2〜4）: 閉場の位置が週で割れる、読めない、または補助の比較が閉場と別のずれを支持するなら、規則は特定できないとして次のとおり閉じる。**除外はしない**。
  1. **原データを保持する**（API の生の時刻を変えない）。
  2. **時刻の仮定を明示して探索研究に使う**。採用する仮説（例: 仮説 US）と、その根拠・適用範囲を、**戦略成績を見る前に固定する**（固定の手順は本文 第11.8.4節の 2、D12-48【提案】）。
  3. **候補の時刻の規則を変えた感度の検証**を行う（候補の例: 仮説 US と仮説 EU）。**規則によって採否が変わる戦略は、その範囲を根拠に採用しない**（具体は本文 第11.8.4節の 3、D12-49【提案】）。
  4. **追加調査待ちの保留は作らない**。この調査はここで終わる（追加の AWS の再開はしない）。
- **仮定付きになりうる区間**（【提案】。(ii) で閉じた場合）: 2017-03-12〜2017-03-26、2019-03-10〜2019-03-31（各年の米国の切替日〜欧州の切替日、約 2〜3 週）の全通貨。
- **K-10 では決まらない区間**（K-10 は要求しない。区分は本文 第11.7.9節の【提案】で人間が決める）: 秋の米欧の間（2017-10-29〜11-05、2019-10-27〜11-03、各約 1 週。要決定 D12-46。v0.13 では「除外の候補」）、2018・2020〜2022 年（と 2023 年の秋）の米欧の間の週（要決定 D12-45）。どちらも根拠は K-9.6 の観察 1・2（週末の開場の位置がサーバーの取引時間の設定で決まる可能性があり、開場の側は US と EU を見分ける根拠として弱い）。設計役の案はどちらも「仮定付き（仮説 US）」。
- 価格の差が最小という理由だけでは特定しない（第 11 回の決定の 3）。要求の上限・作業上限に達したら、その時点のデータで (i) か (ii) を判定して閉じる（追加の再開はしない）。
- **v0.15: (i) で閉場のずれが +2 時間だった場合の規則の名の書き方**（K-10.2 の v0.15 の 2。本文 第11.7.9節）: 道具の出力は規則の名を「EU, FIXED」と併記する。時刻の規則の表（本文 D12-47【決定】）に書くときは、**規則の列は「その区間で使う変換（API の時刻 − UTC の時間数）」を正とし、同じ変換を与える名を併記する**（例: `+2 h（EU, FIXED。区間の中では同じ変換）`）【提案】。適用期間は K-10 の判定の区間（2017・2019 年の春の米欧の間の週）に限り、併記した名のどれかを選んで区間の外へ当てはめない（EU と 固定は両方夏の週で変換が違う）。確認済みの行なので、感度の検証の候補の列は空（併記した名を候補として扱わない）。+3 時間（US）だった場合は名は 1 つ（US）。
- **v0.15（人間の第 12 回の続きの決定、本文 第0.1.16節）**: D12-46 (a) により、2017・2019 年の秋の米欧の間の週は K-10 の結果にかかわらず仮定付き（仮説 US、候補 EU）。D12-45 (a) により、2018・2020〜2022 年と 2023 年の秋の米欧の間の週も仮定付き。仮定付きの範囲があることを理由に、追加の調査や研究の停止を自動では要求しない（D12-44）。K-10 は 2026-10-16 に人間が起動を合図した（記録は K-11、作業役）。


### K-11. 最後の調査の実施の記録（2026-10-16 11:51〜12:28 JST。作業役）

人間の決定（原文、本文 第12 回の記録）: 「今から起動してよい（この後 30 分対応できる）」。権限の拒否の後に人間が許可の規則（`Bash(tools/survey/aws_mt5_probe/*)`）を足し、「足した。今から起動してよい」。道具は PR #81 の `92fb8e5`。**要求は 9（上限 12 の内）。USDJPY の 1 分足だけ、2017・2019 年だけ。2024 年以降は要求していない。API の価格の値はどのファイルにも残していない**（足の時刻と、配布 CSV との差の統計だけ）。

#### K-11.1 作業の記録（時刻は JST）

| 時刻（JST） | 作業 | 結果 |
|---|---|---|
| 10:38〜10:45 | Mac で `final-ref`（配布 CSV の bid・ask・mid の 1 分足、9 区間の前後 5 時間） | `data/raw/oanda_mt5_probe/_k10/final_ref.json.gz`（235,153 バイト、SHA-256 `af18fe4d59cd4be701c09ad10025504f14ee99382c9df6f029219758e771e245`。各区間 480〜1,080 本。2019-03-31 の週末は 2019-04 の CSV が無いので金曜の側だけ） |
| 11:4x | `10_launch.sh --execute` の 1 回目 | 作業役のセッションの権限の仕組みが拒否（課金の操作）。何も作っていない。人間が許可の規則を足した |
| 11:51:13 | `10_launch.sh --execute` | インスタンス `i-094546943ebb98df7`（t3.medium、1d）、gp3 60 GB、セキュリティグループ `sg-01ec3bed77ed3fe56`（RDP は 126.221.39.9/32 だけ）、キーペア `odyssey-mt5-probe-2026-10-16`、AMI `ami-0ba097b3fdf9feef7`。打ち切り 13:21、終了 13:51、Windows の中の自動の停止 14:21 |
| 11:51 | `95_deadline_guard.sh` を裏で起動 | 片付けの後に自分で終わった |
| 11:51 | 司令塔に RDP の接続先・パスワードの出し方・人間の作業（K-10.4）を送った | — |
| 11:5x〜12:09 | `20_prepare.sh --execute` | Python 3.12.10・MetaTrader5、道具を置き SHA-256 一致 |
| 12:3x 前（司令塔経由） | 人間の報告: 最大バー数を 100000 → Unlimited に再設定し「ファイル」→「終了」で再起動、「銘柄」→「バー」で M1 2017.01.01〜2019.12.31 を Request して「USDJPY.cl,M1: 1114328 bars」、チャートは開いていない、RDP は切断 | **K-9.1 の「0 bars, limited by charts settings」は再発しなかった**（K-10.4 の 2 の手順が効いた） |
| 12:19〜12:26 | `37_final_survey.sh --only K10-USDJPY-W2017-03-05 --execute`（取り寄せの確認を兼ねる 1 区間） | 479 本（出し直し 0）。端末 build 6266、maxbars 100000000、接続中 |
| 12:26〜12:27 | `37_final_survey.sh --remaining --execute`（残り 8） | 8 区間すべて取れた（週末 415〜475 本、火曜 120 本、出し直し 0）。**0 本の区間は無く、`--redo-empty` は使っていない** |
| 12:27:5x | `90_teardown.sh --execute` | インスタンスを終了、セキュリティグループ・キーペア・Mac の .pem を削除。**残りの確認: 試験のインスタンス（終了済みを除く）0、ボリューム 0、使われていないボリューム 0、スナップショット 0、AMI 0、Elastic IP 0、ネットワークインターフェース 0、セキュリティグループ 0、キーペア 0**。人間が作った IAM のもの・Budgets は消していない |

- 要求の数: 9（上限 12）。どの要求にも区間の外の足が 1 本返った（区間の外なので保存していない。K-9 と同じ）。
- 回収: 2 回、計 13,362 バイト（上限 1 GB）。`ts-K10-1/summary_ts-K10.json` `3f35854bb62053c14f8decd3acf9dce63383a07a59bb78a32521434aae58d53c`、`ts-K10-2/summary_ts-K10.json` `a19807110ec1a176ec558201393d652cae8a67cf9ae85d86294a4354b21735b7`。判定 `k10-eval/report.md` `3bf15b991393f8b99f4d4f8856369480e91badf9f9a801ca2d87d349134b2789`、`k10-eval/result.json` `3ea4d3c20b7cf930a45eff4e66450af2527ab6e2b30c60ea1e0bd1bc552fae64`（どれも `data/raw/oanda_mt5_probe/2026-10-16/` の下）。

#### K-11.2 観察（時刻と差の統計だけ。価格の水準は書かない）

| 週末（日曜） | 状態 | 金曜の閉場のずれ（残り） | 日曜の開場のずれ（残り） | 照合の差が最小のずれ（金曜の側 / 月曜の側。bid・ask・mid） |
|---|---|---|---|---|
| 2017-03-05 | S1（対照） | +2 h（0 分） | +2 h（+1 分） | +2 / +2（3 種類とも） |
| 2019-03-03 | S1（対照） | +2 h（−2 分） | +2 h（+2 分） | +2 / +2（3 種類とも） |
| 2017-03-19 | S2M | **+2 h**（−5 分） | +3 h（0 分） | +2 / +2（3 種類とも） |
| 2017-03-26 | S2E | **+2 h**（0 分） | +3 h（+1 分） | +2 / +3（3 種類とも。月曜は欧州の切替の後） |
| 2019-03-17 | S2M | **+2 h**（−2 分） | +3 h（0 分） | +2 / bid +2・**ask +3**・mid +2 |
| 2019-03-24 | S2M | **+2 h**（−2 分） | +3 h（0 分） | +2 / +2（3 種類とも） |
| 2019-03-31 | S2E | **+2 h**（−2 分） | +3 h（+2 分） | +2 / 比べられない（2019-04 の CSV が無い） |

- 火曜 2017-03-14（T6 の読み直し）・2017-03-21: bid・ask・mid とも +2 h が最小（終値の差の中央値 +2 h で 4〜9 ポイント、+3 h で 40〜51 ポイント）。
- 4 値の完全一致は、対照を含めてどの区間でも 0 本（2017・2019 年の配布 CSV とは K-9 と同じく完全一致しない。どの読みも「差のみの証拠」）。
- 照合の差が最小のずれと 2 番目の比は、対照で 2.7〜44.7、判定の週で 1.3〜58.2。**唯一閉場と食い違った 2019-03-17 の月曜の側の ask は比 1.3**（+3 h で 12、+2 h で 16 ポイント）で、同じ側の bid（比 5.4）・mid（比 2.1）は +2 h。
- 金曜の閉場は 5 週末すべて +2 h（仮説 US なら +3 h）。日曜の開場は 5 週末すべて +3 h。

#### K-11.3 判定（道具 `final-evaluate`。区分は「確認済み」「仮定付き」の 2 つだけ）

- **区分: 仮定付き**（2017-03-12〜2017-03-26 と 2019-03-10〜2019-03-31。各年の米国の切替日から欧州の切替日 01:00 UTC の前まで。USDJPY）。
- 理由（道具の出力）: 「補助の比較（bid・ask・mid）が閉場と別のずれを示す: 2019-03-17 open_side ask +3 h」。K-10.5 の (i) の 3 つの条件のうち、閉場（5 週末とも +2 h で揃う）と対照（2 週末とも +2/+2）は満たし、補助の比較の判定に使う 33 の読み（対照の 12 は除く。比べられない 3 も除く）のうち 1 つ（上の ask、比 1.3）だけが食い違った。道具は承認された終了条件のとおり、補助の比較の矛盾を 1 つでも「確認済みにしない」理由として扱った（作業役は判定を変えていない）。
- 候補の規則ごとの解釈（道具の出力。どれも選んでいない）: **US**（期間の中は API の時刻 − 3 時間 = UTC。支持: 開場 5、補助 4。不支持: 閉場 5、補助 29）、**EU**（− 2 時間。支持: 閉場 5、開場 2（欧州の切替日の週末）、補助 32。不支持: 開場 3、補助 1）、**固定**（− 2 時間。この期間では EU と同じ変換。支持: 閉場 5、補助 29。不支持: 開場 5、補助 4）。
- 価格の差が最小という理由だけで判定はしていない（補助の比較は「確認済みにしない」方向にだけ働いた）。

観察からの推定（断定しない。判定には使っていない）:

1. 閉場 +2 h・開場 +3 h の組合せは、表（K-10.2）のどの仮説の列（US (3,3)・EU (2,2)・固定 (2,2)）とも合わない。K-9.6 の観察 1・2 の推定（サーバーの時計は欧州の規則、取引の開始はサーバーの時計の月曜 00:00）なら、開場は UTC 22:00 = ニューヨーク 18:00 になり、ニューヨーク 17:00 と比べて +3 h に見える。閉場（取引時間の終わりで隠れない）と配布 CSV との照合（金曜・月曜・火曜とも +2 h）は、どちらもこの推定と合う。
2. そうだとすると、K-9.2 で 2017・2019 年の米国の切替の週末（S2）を「仮説 US」と読んだのは開場の側だけの読みで、K-9.6 の【提案】（S2・S4 の週を「推定」に下げる）の根拠が強まる。

#### K-11.4 費用の実績の概算（米ドル、税込み。Cost Explorer の反映は翌日）

| 項目 | 金額 |
|---|---|
| インスタンス・ディスク 60 GB・IPv4（0.61 時間、11:51:13〜12:27:5x） | 約 0.058 |
| 送信（RDP・SSM の応答、回収 13 KB、参照の送付 235 KB） | 約 0.01 以下 |
| この回の合計 | **約 0.07**（想定 0.16・上限 0.40 の内） |
| これまでの合計（K-4・K-9.4 の約 0.48 と合わせて） | **約 0.55**（試験予算 5 の残り 約 4.45、全体予算 100 の残り 約 99.45） |

- 人間が翌日に確かめること: 請求書と Cost Explorer で EC2・EBS・IPv4 の料金が止まり、3 回の合計が約 0.55 前後であること。IAM のユーザー・ロール・アクセスキーは残してある（不要なら人間が消す。K-10 で調査は終わるので、消してよいかは人間が決める）。

### K-12. 代表 112 週の空白の集計（D12 §11.9。2026-10-16 作業役。Mac の上だけ。AWS・追加の取得・価格の閲覧はしていない）

人間の第 14 回の指示（「次は取得済み112週の欠損集計へ進むのが妥当です」）と、D12-37・D12-39・D12-51 (b) による。**全 112 週を集計した**。結果は事実の記録で、品質の閾値（D12-4）・実行の可否の規則・カレンダーの改訂は決めていない（K-12.7 は人間が決める候補）。

#### K-12.1 方法

| 項目 | 内容 |
|---|---|
| 道具 | `tools/survey/mt5_gap_aggregate.py`（新規）。単体テスト `tests/unit/test_mt5_gap_aggregate.py`（人工データ 24 件: 表 版 1 の行の当て方 11 通り、研究期間の外の拒否、印、入力の価格の項目を読まないこと、空白の検出・長さ・時間帯・曜日・完全な足、カレンダーの短縮を空白と数えないこと、宣言の無い休場を空白のまま数えること、候補ごとの集計と両方の記載、出力に価格が出ないこと） |
| 入力 | `ts-G-9/summary_ts-G.json`（SHA-256 `9a8c23daaaa38757c3b5579ee83604a88c0c9f427893e79a099d4a604bfc6dbe`。K-9.3）。読むのは要求ごとの `rid`・`pair`・`state`・`date`・要求の区間・`bar_times`（足の時刻）だけ。他の項目は読まない |
| 時刻の変換 | 期間別の時刻の規則の表 版 1（D12 §11.7.10。D12-48）。**112 週はすべて行 8（W2 の 12 月。両方冬、+2 時間）か行 9（W1 の 6 月。両方夏、+3 時間）に当たる**。どちらも区分は仮定付きで、候補の間で変換が同じ（感度の候補が無い）。確認済みの行 3（2023 年 3 月）は代表週に入らない。**したがって全 112 週の印は「仮定付き（候補の間で変換が同じ）」で、確認済みの週は 0、候補の規則で結果が違う週も 0**（道具は候補のある週では候補ごとに集計して両方を出す。テストで確認） |
| 取引時間 | 承認済みの取引カレンダー `fx_ny17` 版 2（`configs/calendars/fx_ny17_v2.yaml`、SHA-256 `eb639afe5caeff3ee573fd28d7dda402acbc4f250af931ad3976b411061782d1`。D03 §3.4・§3.4.1）のセッションの中の分。版 2 が宣言した休場・短縮の中の分は空白と数えない。宣言の無い休場は推測で足さず、空白として数えたまま K-12.4 に載せた。時間足は `timeframes_v1.yaml`（SHA-256 `231d01c542cf5c152303770ed8832003702850380d0f7386467105c774710c81`） |
| 空白 | 取引時間の中で 1 分足が無い分。根拠の無い空白は必要な入力の不足（D12-36）として数え、多いという理由で正常な無更新に分けていない |
| 完全な足（M4） | 空白の分を 1 つでも含む足は欠け（D12 §4.1 の層 3）。足の区間はカレンダーで切り詰めた区間（`expected_interval`）。15 分足・1 時間足・4 時間足（`4h_ny17`）・日足（`1d_ny17`） |
| 出力（版管理の外。時刻と件数だけ） | `data/raw/oanda_mt5_probe/2026-10-16/gap_summary/`: `week_metrics.csv`（112 行。M1〜M6、時間帯は UTC の時ごと・セッションごと・曜日ごと）SHA-256 `a9b26072d59ee65e53daaf246f1c0db0ef7b1b27a57aef326e6c6b4dd855c9f0`、`daily_bars.csv`（日足ごとの完全・欠け）`66856d3d9d10ccee1ebc26d8a2cf213d4a76b31e9a6f933f31473106658e1123`、`gap_runs.csv`（空白 1 件ごとの UTC とニューヨークの時刻・長さ）`32342e55d087ab6fb61e5d0a3c6d3727c214052794604671a8aafd0057397033`、`report.md`（112 週の表）`bd7a323162e8d91a1a13fad3eff65e2b1a74afeb45bc9abe0a67c7cd878c6605`、`inputs.json` |

**作業役の仮置き（設計の決定ではない。K-12.7 の 3）**:

1. 取引のセッション（M3）: 現地時刻で東京 09:00〜18:00、ロンドン 08:00〜17:00、ニューヨーク 08:00〜17:00、日替わりはニューヨーク 17:00〜18:00。重なる時間は両方に数える。D12 §11.9.3 に時刻の定義が無いため。
2. 曜日（M3）: 取引日（ニューヨーク 17:00 で区切る）の終わりの日の曜日（日曜の夕方の分は月曜に数える）。
3. 空白の 1 件: 時刻の上で連続した空白の分の並び。休場をまたぐと別の件。
4. M5（欠けの無い連続区間）: 日足が完全ならその日の 15 分足・1 時間足も完全なので、「戦略が読む時間足（15 分足・1 時間足・日足）が同時に完全な区間」は完全な日足の連続と同じになる。週の中の最長の連続（日足の本数）と、空白の無い分の最長の連続（分）を出した。

#### K-12.2 測定範囲の結果（全 112 週。印はすべて「仮定付き（候補の間で変換が同じ）」）

**通貨 × 週の種類 × 年**（セルは「M1 空白の分の割合（%）・完全な日足 / 存在すべき日足」。W1 と W2 は混ぜない。2017 年の W2 と 2020 年の W2 は版 2 の休場（2017-12-25、2021-01-01）で日足が 4 本）:

| 通貨 | 週 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|
| USDJPY | W1 | 0.07 ・ 2/5 | 0.07 ・ 2/5 | 0.11 ・ 3/5 | 0.14 ・ 2/5 | 0.72 ・ 0/5 | 0.46 ・ 0/5 | 0.75 ・ 0/5 |
| USDJPY | W2 | 6.37 ・ 3/4 | 17.97 ・ 0/5 | 10.70 ・ 0/5 | 0.52 ・ 0/4 | 0.65 ・ 0/5 | 20.31 ・ 0/5 | 9.61 ・ 1/5 |
| EURJPY | W1 | 0.08 ・ 2/5 | 0.08 ・ 2/5 | 0.10 ・ 3/5 | 0.24 ・ 0/5 | 1.29 ・ 0/5 | 0.47 ・ 0/5 | 0.25 ・ 1/5 |
| EURJPY | W2 | 6.37 ・ 2/4 | 17.96 ・ 1/5 | 10.56 ・ 1/5 | 0.40 ・ 0/4 | 0.71 ・ 0/5 | 20.12 ・ 1/5 | 9.81 ・ 0/5 |
| EURUSD | W1 | 0.12 ・ 1/5 | 0.07 ・ 2/5 | 0.11 ・ 3/5 | 0.35 ・ 0/5 | 1.19 ・ 0/5 | 0.36 ・ 1/5 | 0.97 ・ 0/5 |
| EURUSD | W2 | 6.49 ・ 3/4 | 17.99 ・ 0/5 | 11.21 ・ 0/5 | 0.64 ・ 0/4 | 0.57 ・ 0/5 | 20.81 ・ 0/5 | 9.80 ・ 0/5 |
| GBPJPY | W1 | 0.08 ・ 2/5 | 0.07 ・ 2/5 | 0.11 ・ 3/5 | 0.12 ・ 3/5 | 0.24 ・ 1/5 | 0.26 ・ 1/5 | 0.35 ・ 1/5 |
| GBPJPY | W2 | 6.32 ・ 3/4 | 17.99 ・ 1/5 | 10.64 ・ 1/5 | 0.28 ・ 0/4 | 0.67 ・ 0/5 | 20.31 ・ 0/5 | 9.61 ・ 1/5 |
| USDCHF | W1 | 0.15 ・ 2/5 | 0.08 ・ 2/5 | 0.11 ・ 2/5 | 0.36 ・ 0/5 | 2.67 ・ 0/5 | 1.42 ・ 0/5 | 0.93 ・ 0/5 |
| USDCHF | W2 | 6.58 ・ 2/4 | 18.00 ・ 0/5 | 11.60 ・ 0/5 | 0.92 ・ 0/4 | 2.24 ・ 0/5 | 20.93 ・ 0/5 | 10.44 ・ 0/5 |
| AUDJPY | W1 | 0.08 ・ 3/5 | 0.07 ・ 2/5 | 0.10 ・ 3/5 | 0.19 ・ 2/5 | 0.33 ・ 0/5 | 0.22 ・ 0/5 | 0.35 ・ 1/5 |
| AUDJPY | W2 | 6.42 ・ 3/4 | 17.97 ・ 1/5 | 10.61 ・ 0/5 | 0.33 ・ 1/4 | 0.50 ・ 1/5 | 20.22 ・ 0/5 | 9.98 ・ 0/5 |
| GBPUSD | W1 | 0.21 ・ 2/5 | 0.08 ・ 2/5 | 0.11 ・ 3/5 | 0.21 ・ 1/5 | 1.65 ・ 0/5 | 1.22 ・ 0/5 | 0.89 ・ 0/5 |
| GBPUSD | W2 | 6.41 ・ 3/4 | 18.00 ・ 1/5 | 10.92 ・ 0/5 | 0.73 ・ 0/4 | 1.21 ・ 0/5 | 20.75 ・ 0/5 | 10.00 ・ 0/5 |
| AUDUSD | W1 | 0.39 ・ 1/5 | 0.08 ・ 2/5 | 0.14 ・ 1/5 | 0.28 ・ 2/5 | 1.39 ・ 0/5 | 1.19 ・ 0/5 | 0.96 ・ 0/5 |
| AUDUSD | W2 | 6.39 ・ 2/4 | 17.97 ・ 1/5 | 10.94 ・ 0/5 | 0.57 ・ 0/4 | 1.56 ・ 0/5 | 20.58 ・ 0/5 | 9.76 ・ 0/5 |

**年 × 週の種類（8 通貨の幅）**:

| 週 | 年 | M1 空白の割合（%） | M4 15 分足の完全（%） | M4 1 時間足の完全（%） | M4 日足の完全（8 通貨の合計） | M5 完全な日足の最長連続（本） | M5 空白の無い最長（分） | 空白の件数（8 通貨） | うち NY 17 時台の空白の分 / 空白の分 |
|---|---|---|---|---|---|---|---|---|---|
| W1 | 2017 | 0.07〜0.39 | 97.7〜99.4 | 95.0〜98.3 | 15/40 | 1〜2 | 2,745〜5,627 | 57 | 59 / 86 |
| W1 | 2018 | 0.07〜0.08 | 99.2〜99.4 | 96.7〜97.5 | 16/40 | 1 | 2,879〜4,171 | 28 | 10 / 44 |
| W1 | 2019 | 0.10〜0.14 | 99.0〜99.6 | 95.8〜98.3 | 21/40 | 1〜3 | 2,879〜7,193 | 21 | 46 / 64 |
| W1 | 2020 | 0.12〜0.36 | 97.7〜99.2 | 94.2〜98.3 | 10/40 | 0〜3 | 1,440〜7,155 | 85 | 91 / 136 |
| W1 | 2021 | 0.24〜2.67 | 90.0〜97.9 | 80.8〜95.0 | 1/40 | 0〜1 | 1,224〜2,850 | 367 | 472 / 683 |
| W1 | 2022 | 0.22〜1.42 | 94.4〜97.9 | 90.0〜95.8 | 2/40 | 0〜1 | 1,393〜2,862 | 237 | 375 / 404 |
| W1 | 2023 | 0.25〜0.97 | 93.8〜98.5 | 88.3〜96.7 | 3/40 | 0〜1 | 1,386〜2,836 | 263 | 267 / 392 |
| W2 | 2017 | 6.32〜6.58 | 91.4〜93.2 | 89.6〜92.7 | 21/32 | 2〜3 | 3,726〜5,351 | 80 | 480 / 2,958 |
| W2 | 2018 | 17.96〜18.00 | 81.1〜81.6 | 78.3〜80.2 | 5/40 | 0〜1 | 2,467〜3,959 | 39 | 971 / 9,149 |
| W2 | 2019 | 10.56〜11.60 | 84.6〜87.9 | 76.6〜85.0 | 2/40 | 0〜1 | 1,389〜2,705 | 183 | 707 / 5,597 |
| W2 | 2020 | 0.28〜0.92 | 95.1〜98.7 | 88.5〜95.8 | 1/32 | 0〜1 | 1,417〜2,850 | 139 | 241 / 253 |
| W2 | 2021 | 0.50〜2.24 | 86.5〜97.9 | 70.8〜95.8 | 1/40 | 0〜1 | 1,047〜2,868 | 366 | 435 / 583 |
| W2 | 2022 | 20.13〜20.93 | 76.7〜79.2 | 75.0〜77.5 | 1/40 | 0〜1 | 1,393〜2,880 | 174 | 727 / 11,810 |
| W2 | 2023 | 9.61〜10.44 | 87.7〜89.4 | 84.9〜87.7 | 2/40 | 0〜1 | 1,413〜2,875 | 110 | 696 / 5,025 |

- 版 2 の宣言の中の時間に足があった分（取引時間の外の足）は 112 週すべてで 0。分の境目に乗らない足も 0。
- 4 時間足の完全な割合は `week_metrics.csv`・`report.md` にある（1 時間足と日足の間の値）。

#### K-12.3 空白の長さ（M2）と時間帯（M3）

| 週 | 空白の分の合計 / 取引時間の分（8 通貨 × 7 年） | 1 分 | 2 分 | 3〜5 分 | 6〜15 分 | 16〜60 分 | 61 分以上 |
|---|---|---|---|---|---|---|---|
| W1 | 1,809 / 403,200 | 687 件 | 213 件 | 140 件 | 17 件 | 1 件（USDCHF 2021-05-31 NY 17:06 からの 24 分） | 0 件 |
| W2 | 35,375 / 360,480 | 717 件 | 158 件 | 143 件 | 25 件 | 0 件 | 48 件（すべて K-12.4 の年末の 6 種類） |

- **W1 の空白の分の 73%（1,809 分のうち 1,320 分）が UTC 21 時台（夏時間のニューヨーク 17 時台＝日替わりの直後の 1 時間）**。15 分以下の短い空白を NY の時刻で数えると、W1・W2 合わせて 17 時台が最も多い（開始の分の上位は 17:01 の 62 件、17:02 の 51 件など）。セッション別（重なりは両方に数える）: W1 は日替わり 1,320・ニューヨーク 335・東京 11・ロンドン 1 分。
- W2 の空白の大半は年末の長い空白（K-12.4）で、時間帯の分布はその休場の時間に引っ張られる（東京 20,336・ニューヨーク 5,948・ロンドン 4,803・日替わり 4,257 分）。
- 曜日（取引日の終わりの日）: W1 は月 593・火 257・水 244・木 308・金 407 分。W2 は月 17,966・火 8,256・水 8,477・木 326・金 350 分（月〜水は年末の休場の日に当たる年が多い）。UTC の時ごとの数は `week_metrics.csv` の `m3_hour_*`。

#### K-12.4 カレンダーに無い休場・短縮の候補（空白として数えたまま。**提供元の公開情報の出典は未確認**）

61 分以上の空白はすべて年末の次の 6 種類で、**8 通貨すべてに同じ時刻で現れた**（2018-12-25 の 2 つ目だけ 3 通貨が 359 分、5 通貨が 360 分）。時刻はニューヨーク。

| 日 | 版 2 の宣言 | 観察した空白（8 通貨共通） | 版 2 との違い |
|---|---|---|---|
| 2017-12-25 | 取引日単位の休場（12-24 17:00〜12-25 17:00） | 12-25 17:00〜23:00（360 分） | 休場の後の再開が 6 時間遅い |
| 2018-12-24〜25 | 12-25 03:00〜17:00 の短縮 | 12-24 14:00〜12-25 03:00（780 分） | 前日 14:00 から閉じ、クリスマスの取引日は丸ごと足が無い |
| 2018-12-25 | 同上 | 12-25 17:00〜23:00（359〜360 分） | 再開が 6 時間遅い |
| 2019-12-24〜25 | 12-25 04:00〜17:00 の短縮 | 12-24 17:00〜12-25 04:00（660 分） | クリスマスの取引日は丸ごと足が無い |
| 2022-12-26（月） | 宣言なし | 12-25 17:00〜12-26 17:00（1,440 分） | 取引日が丸ごと足が無い（クリスマスの振替の日） |
| 2023-12-24〜25 | 12-25 03:00〜17:00 の短縮 | 12-24 17:00〜12-25 03:00（600 分） | クリスマスの取引日は丸ごと足が無い |

- 版 2 の年末の宣言は、旧データ（HistData 由来の集約足）の段階 4 の分類から読み取ったもので（`fx_ny17_v2.yaml` の注記）、提供元が違う。上の違いはその差と見られるが、**OANDA の公開情報（年末年始の取引時間の告知）は今回見ていない**（追加の取得をしない指示のため）。推測でカレンダーを書き換えず、上の分はすべて空白（必要な入力の不足）として数えた。
- 2020 年の W2（12-27〜）と 2021 年の W2（12-26〜）にはクリスマスの取引日が入らない。2021 年は 61 分以上の空白が無い。

#### K-12.5 実行可能期間（ウォームアップ 日足 60 本）への影響（観測した日に限る）

- 観測した日足: W1 は 280 本のうち完全 68 本、W2 は 264 本のうち完全 33 本。**1 週の中で完全な日足が続いた最長は 3 本**（どの週も 60 本の連続は観測できない長さ。D12 §11.9.6 の 2）。
- **欠けた日足 443 本は、今の規則（空白の分を 1 つでも含む足は欠け。run の開始の前に完全な日足が 60 本続くこと）では、その日を 60 本の中に含む run を実行できない**（観測した日についての事実）。
- 欠けの中身: 欠けた日足 443 本のうち、空白の合計が 5 分以下の日が 188 本（W1 114・W2 74）、15 分以下が 312 本（W1 175・W2 137）、空白がニューヨーク 17 時台（日替わりの直後の 1 時間）だけにある日が 202 本（W1 103・W2 99）。年末の長い空白で欠けた日は W2 の各年 0〜2 日。
- 年による違い: 完全な日足の数は 2017〜2019 年の W1 で 40 本中 15〜21 本、2020 年の W1 で 10 本、2021〜2023 年は W1・W2 とも 40 本中 1〜3 本。

#### K-12.6 全期間では未確認のこと（代表週の空白から全期間の実行不能を断定しない。D12-39）

- 測定していない週（研究期間 2017〜2023 年の約 365 週のうち、代表週 14 週（7 年 × 2）を除くすべて）の空白の頻度と時間帯。代表週の結果が他の週にも当てはまるか。
- 完全な日足が 60 本続く期間があるか、あればどの長さか（実行可能期間の長さ）。
- 米欧の間の週（表 版 1 の行 1〜7。感度の候補がある週）は代表週に入らないので、候補の規則で空白の結果が変わるかは測っていない。
- 年末の 6 種類の空白が、提供元の公表した休場か、配信の欠けか（出典の確認が要る）。
- 未集計（時刻未確定）の区分: なし（調査を閉じた後なので、D12-51 (b) により仮定付きとして全部を集計した）。

#### K-12.7 人間が決める候補（作業役は決めていない）

1. **日替わりの直後の数分の空白の扱い（品質の閾値 D12-4 に関わる）**: 今の規則では、ニューヨーク 17 時台の 1〜数分の空白だけで日足が欠けになり、2021〜2023 年の代表週では日足がほとんど完全にならない。D12-36・D12-37 により、多いという理由だけで正常な無更新には分けられない。決めること: 閾値（例: 足の中の空白の分の許容）を置くか、1 分足の外の根拠（提供元の日替わりの保守の時間の公表など）を探すか、今の規則のままにするか。影響: 実行可能期間の長さが大きく変わる。
2. **年末の休場のカレンダー**: OANDA の配信では 2017〜2019・2023 年のクリスマスの取引日が丸ごと無く、2017・2018 年は 12-25 の再開が 6 時間遅く、2022-12-26 の取引日も無い。版 2 には一部しか宣言が無い。決めること: 公開情報の出典を確かめて D03 の手順（宣言制）でカレンダーの新しい版を作るか、空白（入力の不足）のまま扱うか。影響: W2 の空白の割合（6〜21%）の大半と、年末の日足の完全・欠け。
3. **M3 のセッションの時刻の定義**（K-12.1 の仮置き 1）の承認か修正。
4. **全期間の取得と追加の設計**（D12-37・D12-39 の原文どおり、この結果を見て人間が判断する）。

### K-13. OANDA の公式資料の調査（D12 §11.10 の段 1。人間の第 15 回の決定 D12-60 (a)・D12-61 (a) による。2026-10-16 調査役。公開ページだけ。登録・問い合わせ・購入なし。価格・戦略の成績は見ていない。設計役が調査役の記録と保存 HTML を突き合わせて記録した）

**この記録の性質**: 取得日時はすべて 2026-10-16 17:39〜17:41 JST（保存ファイルの時刻）。保存 HTML・画像と SHA-256 の一覧（53 ファイル）は版管理の外（セッションの作業場所の `survey_oanda_sources/` と `sha256.txt`）。逐語は保存 HTML の本文から。逐語のうち K-13.1 の 7 件（FAQ 882、お知らせ 510・818・821・831・1131・975）と 644・967 の取引時間の段は、設計役が保存 HTML から文字列を取り出して照合した。取引時間表の画像（2020・2021・2023）の読み取りは調査役の記録のままで、設計役は画像を見ていない。

#### K-13.1 資料の一覧（URL・SHA-256・逐語）

URL の `info/<番号>` は `https://www.oanda.jp/info/<番号>`。

| # | 資料 | URL | SHA-256 | 逐語（要点） |
|---|---|---|---|---|
| S1 | 現行 FAQ「取引時間を教えてください」（公開 2024/07/02、更新 2025/08/05） | https://help.oanda.jp/oanda/faq/show/882?site_domain=default | `e0e07fe3a5d6ef80ae440f2f95c0080158cf14507ce4ff6f94e5f3d954559342` | 「※米国東部時間　午後 4 時 59 分から午後 5 時 05 分の 6 分間」「※メンテナンス中は、成行・指値を含むすべての注文は約定されませんので、ご注意ください。（レートの更新は行われません。）」「※毎週火曜日から金曜日に実施いたします。」。サーバーの区別の記載なし |
| S2 | お知らせ 510（2018-08-28）「【重要】東京サーバーのメンテナンス時間設定のお知らせ」 | info/510 | `78b64fb6556d12fa9ca43802163462aa0197a0a730b8dd4c135c3a8948c85c09` | 「日本時間午前5時55分～午前6時10分 （米国東部標準時間採用時の場合、日本時間午前6時55分～午前7時10分）」「※ メンテナンス中のお取引はできませんので、ご注意ください。 （レート更新およびチャートの形成は行われます。）」「※毎週 火～土曜日に実施いたします。」。理由の段に「東京時間午前6時前後は、弊社のカバー先銀行の一部がリスク回避的になる傾向が強まり、レート配信の頻度が極端に低くなる、あるいは配信レートのスプレッドが極端に広くなるといった状況が生じております。」。適用開始は 2018-09-04（調査役の記録） |
| S3 | お知らせ 818（2021-11-19）「【重要】NYサーバーのメンテナンス時間の導入」、821（訂正） | info/818、info/821 | 818 `5ab01ddd9ae94981abe06a8ce31230c1a743992a5a1e833937f01e0049da5e0f`、821 `371414601df8edcd16b84cd746f8a57eace0cacfa209b7579633e96b037146bb` | 「この度、2021年11月29日よりNYサーバーにおいてメンテナンス時間を以下の通り導入いたします」「日本時間午前5時58分～午前6時03分 の合計５分間 （米国東部標準時間採用時の場合、日本時間午前6時58分～午前7時03分）」「（レート更新は行われません。）」「※毎営業日に実施いたします。」 |
| S4 | お知らせ 831（2021-12-10）「【重要】東京サーバーのメンテナンス時間の導入」 | info/831 | `4c980cb38d4035ef323fd2281ebb63901abd43d52bd196bf99e54503013a8179` | 「この度、2021年12月20日より東京サーバーにおいてメンテナンス時間を以下の通り導入します」。月曜は 3 分間（標準時間 7:00〜7:03、夏時間 6:00〜6:03。日本時間）、火〜土は「米国東部標準時間採用時の場合、午前6時55分～午前7時10分（日本時間） 米国東部夏時間採用時の場合、午前5時55分～午前6時10分 （日本時間）」「（レート更新は行われます。）」。2018 年の S2 との関係（変更か再導入か）は書かれていない |
| S5 | お知らせ 1131（2024-02-02）「【重要】 FX取引時間変更およびCFD銘柄のメンテナンス時間導入のご案内」 | info/1131 | `02f9e938fcdb3fba2033be3f0aa75524e4284fcf07275f678d3066ea93ff1bea` | 「この度、2024年2月12日より、FXについては取引時間ならびにメンテナンス時間の変更が行われます。」「◆FX全通貨ペア◆（東京サーバー/ニューヨークサーバー）」「※米国東部時間午後4時59分から午後5時05分の6分間」「（レート更新は行われません。）」「※メンテナンスは毎週火曜日から金曜日に実施いたします。」 |
| S6 | お知らせ 650（2020-01-09）「【1月11日メンテナンス終了後から】本番口座におけるMT5（東京サーバ）リリースのご案内」 | info/650 | `827d7d6187ecd0ec05c2c6248cb9d0afcdde9145068b0273478f02c4ead72e0e` | 本番口座の MT5 は 2020-01-11 から「MT5（東京サーバ）」として提供 |
| S7 | MetaQuotes「Testing Strategies」 | https://www.mql5.com/en/docs/runtime/testing | `a787d7e2e6bd3a079e91196f4e3478e42230cab8f8fa7fcb6391cea293e277d5` | "The new minute bar is not opened at the moment when the new minute begins (number of seconds becomes equal to 0), but when a tick occurs - a price change by at least one point." "Tick data may not coincide with minute bars for various reasons, for example because of connection losses or other failures when transmitting data from a source to the client terminal."（人間の原文の「MetaQuotes公式解説」と同じページかは未確認） |
| S8 | 年末年始のお知らせ 546・550（2018）、644・648（2019）、746・749（2020）、833（2021）、967・975（2022）、1112（2023） | info/<番号> | 下の「年末年始の資料の SHA-256」 | 644 の「■ 取引時間」: 「12月25日(水) 休業 12月26日(木) 午前7時（日本時間）から取引開始 ※OANDA Japan FX（東京サーバ）は午前7時10分（日本時間）から」。967 の「■ 取引時間」: 「年末年始の取引時間は、 こちら をご参照ください。 ※12月26日および1月2日は月曜日ではございますが、「休業」になりますのでご注意ください。」。975: 「【変更後】 ・FX 1月2日 8時5分取引開始」 |
| S9 | 取引時間表の画像（2020・2021・2023） | 各お知らせのリンク先 | 同上 | 調査役の読み取り（下の K-13.3） |

年末年始の資料の SHA-256:

| ファイル | SHA-256 |
|---|---|
| `info_546.html` | `39968575630770877bbbfe67260a4623e009a8fa795b7626d231d28e14854bb8` |
| `info_550.html` | `036d463e3582fe0ba0515d3573e9689a87436af2413713a0b7b41b37a33e4eff` |
| `info_644.html` | `dc6a27c5da5c0dc8887bb6a51eea960de8dc6cff0aa1af5d528efde0a90dffcf` |
| `info_648.html` | `f7a63ecbcc03c2ab06cdafc4457d99b506b123d73f275f68a232bc2effb466be` |
| `info_746.html` | `c59d962b902831a73bb2d81f553dfad1e62346420ab5c721f05a4bffbc46cd7c` |
| `info_749.html` | `1200c485aa4b70a24d51348bca3c1595b63f8117e5a139f4db4373616d03fe75` |
| `info_833.html` | `1df8e9baee10c2287e4597d5cc0203d36c09a060c834f1caa9a56d41d7a72fab` |
| `info_967.html` | `63c5f42a32a5f2b40d25ac7af671048ff7e0499f64061ad53527f2dcb2826384` |
| `info_975.html` | `593e9e547e9b0176221c36ffaa838bb35d763e89e490a5a1eefc15dad3dece92` |
| `info_1112.html` | `dedd5d125a523bec9da19171fe1c425f0f8a7a53f4c8fa3b2abb1bb323278ce9` |
| `tables/t2020_mi.png` | `d78005cecc2a4c07a21b49cf0de92e32fc49a685343fa58f7ea8258d9c2a50f9` |
| `tables/t2021.png` | `b4fdf97dc7447ef84748068bf2cbb0df466acf55c83053aee7b75d4e682ef7d2` |
| `tables/t2023.png` | `a68aef4570f3211a42eafb59a2012e6a68120019cdd4f011260da4995b4f9ac6` |

見つからなかったもの: 2017 年の日替わりのメンテナンスと年末年始の告知、2018-09-04 より前の東京サーバーの日替わりの規定、2022 年末年始の取引時間表（画像の URL が「閉鎖された請求アカウント」のエラー。Wayback Machine は HTTP 429）、OANDA 自身による履歴データ（足の作り方）の説明、「OANDA-Japan MT5 Live」というサーバー名での当時の告知、2020 年以降の年末年始の再開の時刻（2022-01-02 の 8:05 を除く）。2022・2023 年の日替わりのメンテナンスの変更の告知は、お知らせの一覧（80 ページ）に見当たらなかった。

#### K-13.2 日替わりのメンテナンスの時系列（告知から読める範囲。日本時間）

| 期間 | 東京サーバー | ニューヨークサーバー | レートの更新（逐語） |
|---|---|---|---|
| 2017-01-01〜2018-09-03 | 告知なし（有無とも未確認） | 告知なし（未確認） | — |
| 2018-09-04〜2021-11-28 | 火〜土 5:55〜6:10（米国標準時間中 6:55〜7:10）（S2） | 告知なし（未確認） | 東京: 「レート更新およびチャートの形成は行われます。」 |
| 2021-11-29〜2021-12-19 | 同上（S2） | 毎営業日 5:58〜6:03（標準時間中 6:58〜7:03）の 5 分（S3） | 東京: 更新あり（S2）。NY: 「レート更新は行われません。」 |
| 2021-12-20〜2023-12-31 | 月 3 分・火〜土 5:55〜6:10（標準時間中 6:55〜7:10）（S4） | 同上（S3）。変更の告知は見つからなかった | 東京: 「レート更新は行われます。」。NY: 更新なし |
| 2024-02-12〜（研究期間の外。参考） | 火〜金 NY 16:59〜17:05 の 6 分（S5、S1） | 同左 | 「レート更新は行われません。」 |

- 2022-03-18 のお知らせ 856（SHA-256 `aefb407588962e40aff3280232b531535f14a95c836fc5997e134bf96639d05d`）に、東京サーバーの NZ 関連の通貨のメンテナンス時間の設定誤り（MT4/MT5 の夏時間切替でずれた、復旧済み）の記載がある（調査役の記録）。
- MT5 の本番は 2020-01-11 から「MT5（東京サーバ）」（S6）。MT5 の 1 分足（OANDA-Japan MT5 Live）が東京サーバーの規則に従うかは、告知に明記が無い（未確認）。API が 2017-01 からの 1 分足を返したこと（付録 K-5）と、MT5 の本番の提供開始（2020-01-11）との関係（2020 年より前の履歴の出所）も未確認。

#### K-13.3 年末年始の取引時間（FX の行だけ。取引の休止。窓口・入出金は別）

| 年 | 告知 | 休業（FX） | 再開の時刻 | 時刻の基準 |
|---|---|---|---|---|
| 2017 | 見つからなかった | 未確認 | 未確認 | — |
| 2018 | 546・550（本文に表） | 12/25、1/1 | 12/26・1/2 は 7:00（東京サーバーは 7:10） | 本文に「日本時間」 |
| 2019 | 644・648（本文に表） | 12/25、1/1 | 12/26・1/2 は 7:00（東京サーバーは 7:10） | 本文に「日本時間」 |
| 2020 | 746・749（表は画像） | 12/25、1/1（調査役の読み取り） | 表に時刻なし（FX の行は「通常どおり」のみ） | 表に明記なし |
| 2021 | 833（表は画像） | 12/25・1/1 は土曜で「休日」（調査役の読み取り） | 表に時刻なし | 表に明記なし |
| 2022 | 967・975（表の画像は取得できなかった） | 本文: 12/26・1/2 は「休業」。975 で 1/2 は「FX 1月2日 8時5分取引開始」に変更 | 1/2 は 8:05（975） | 未確認 |
| 2023 | 1112（表は画像） | 12/25 休場（調査役の読み取り）。本文の「1月2日、1月3日は「休業日」」は窓口・入出金の行で、FX の行は通常どおり | 表に時刻なし | 表に明記なし |

- サーバーの別が書かれているのは 2018・2019 年の再開の時刻（東京サーバー 7:10）だけ。2020 年以降は FX の 1 行。
- 2022 年は、D12-61 の原文の「リンク先の取引時間表まで確認」を満たせていない（表の画像を取得できなかった）。

#### K-13.4 「認定に必要な確認」（D12 §11.8.3 の v0.21 の表）との対応

| 根拠の候補 | 認定に必要な確認 | 今回の状況 |
|---|---|---|
| 当時の公式取引時間・メンテナンス告知 | 対象サーバー、通貨、適用開始日、休止時刻、配信も止まるか | 一部を確認: 東京 2018-09-04（配信は継続）、NY 2021-11-29（配信停止）、東京 2021-12-20（配信は継続）、両サーバー 2024-02-12（配信停止）。未確認: 2017-01〜2018-09-03、MT5 が従うサーバーの規則、2021-12〜2024-02 の間の変更の有無 |
| 提供元による履歴データの仕様説明 | 「更新のない分は足を作らない」という仕様と、対象履歴の完全性 | MetaQuotes の仕様（S7。tick のあるときだけ足が開く。欠落との区別はできない）は確認。OANDA 自身の説明は見つからなかった。対象履歴の完全性は未確認 |
| 同じ配信の完全性を確認できるtick記録 | 対象分に更新がなく、取得漏れ・配信断でもないこと | 公開ページには無い（調査の対象外） |
| 当時の稼働・配信記録 | 計画休止、障害、正常稼働中の無更新を区別できること | 障害の告知はお知らせの一覧にある（例 593・594 の 2019-06 の東京の緊急メンテナンス、597、706、723、819・820、901。調査役の記録）。網羅は確かめていない |

### K-14. 年末年始の休場（公式告知の揃った年だけ）を外した代表 112 週の再集計（D12 §11.10 の段 2・段 3。人間の第 16 回の決定 D12-66 (a)・D12-68 (a)、第 15 回の D12-60 (a)・D12-61 (a) による。2026-10-16 作業役。Mac の上だけ。AWS・追加の取得・価格の閲覧はしていない）

人間の原文（D12-66 (a)）: 「年末年始の休場（告知の揃った年）だけを外して再集計し、日替わり直後の空白は入力不足のまま数え直す」。D12-60: 「説明できた休止時間だけを、足が存在すべき時間から外して再集計する。これは欠損の許容ではなく、提供元の取引・配信時間に合わせる変更。休止時間をはみ出す空白は入力不足として残す」。**旧集計（K-12）の出力と取引カレンダー 版 2 は変えていない**（下の K-14.1 でハッシュを照合）。結果は事実の記録で、品質の閾値（D12-4）・実行の可否の規則・カレンダーの新しい版は決めていない。

#### K-14.1 方法（K-12.1 からの変更点だけ）

| 項目 | 内容 |
|---|---|
| 休場の定義（新規。調査用で、D03 のカレンダー本体ではない） | `tools/survey/oanda_holiday_exceptions_2026-10-16.json`（SHA-256 `2c85a0936fa0c377568fa054f02268779cfaceeb4371c1ddf65e312af6186604`）。11 行。各行に告知の URL・保存ファイルの SHA-256（K-13.1 と同じ値）・逐語、日本時間と UTC の両方の時刻、時刻の規則の表 版 1 の行（年末の代表週はすべて行 8） |
| 入れたもの | 告知の **FX の行で取引の休止（休業・休場）と明記された日** と、告知に書かれた再開の時刻だけ。窓口・入出金・問い合わせの休業は入れない（2023 年の「1月2日、1月3日は「休業日」」は窓口・入出金の行なので入れない）。土日の「休日」は週の開閉で既に取引時間の外なので行を作らない（2021 年の 12/25・1/1 は土曜で効果が無い） |
| 入れなかったもの | 2017 年（告知が見つからない）と 2022 年（取引時間表の画像が未取得）。D12-68 (a) により、その年の年末の空白は入力の不足のまま。研究期間の外（2024-01-01）も入れない |
| 日付の読み（作業役の読み。K-14.6 の 1） | 告知の日付 D の「休業」「休場」は、取引日 D（ニューヨーク 17:00 区切り。米国東部標準時間中は日本時間 D 07:00〜D+1 07:00 = UTC D−1 22:00〜D 22:00）を閉じると読んだ。根拠: 2018・2019 年の表で前日が「通常どおり」かつ翌日が「午前7時（日本時間）から取引開始」であること。2020 年の表（画像）で 12/24 の列の株価指数に「25日午前3時15分取引終了」とあり、日付の列が翌朝までの取引日を指すこと。日本時間 → UTC は −9 時間（固定） |
| 再開の時刻 | 告知に時刻があるのは 2018・2019 年の告知だけ（翌日の「午前7時（日本時間）から取引開始 ※OANDA Japan FX（東京サーバ）は午前7時10分（日本時間）から」）。MT5 は東京サーバー（お知らせ 650・人間の原文 D12-67）なので **7:10 まで** を「再開の遅れ（東京サーバ）」の別の行（10 分）にした。7:00 で読んだときとの差は K-14.4 に別記。2020・2023 年は表に時刻が無いので日付単位の休場だけで、再開の遅れは外していない |
| 道具の変更 | `tools/survey/mt5_gap_aggregate.py` に `--exclusions`（休場の定義）を追加。**既定は無しで旧の挙動のまま**（下の照合）。外した分は取引カレンダーのセッションの中の分だけで、休場ごとに「外した分・その中で足が無かった分・その中にあった足」を別に数える（`holiday_excluded.csv`）。区間をはみ出す空白は空白のまま。休場の中にあった足は使わない（前値で足を埋めない）。足の区間がすべて休場の中の足（その取引日の日足など）は存在すべき足から外し、一部だけ重なる足は残りの分で完全・欠けを判定する |
| 単体テスト | `tests/unit/test_mt5_gap_aggregate.py` に 11 件を追加（計 35 件）: 既定で旧の結果・列と同じこと、休場の分だけを外し中の足を使わないこと、再開の時刻をはみ出す空白が空白のまま残り日足が欠けのままのこと、日本時間と UTC の不一致・出典の SHA-256 の欠け・重なり・schema の誤りの拒否、定義ファイルの中身（2017・2022 が無い、再開の遅れは 2018・2019 年の告知の 4 行だけ）、出力 |
| 旧の挙動の照合 | 変更後の道具を `--exclusions` 無しで同じ入力に走らせ、出力 5 ファイルの SHA-256 が K-12.1 の旧集計（`gap_summary/`）と **すべて一致**。旧集計のファイルは変更していない。カレンダー `fx_ny17_v2.yaml` の SHA-256 は K-12.1 と同じ `eb639afe…1782d1` |
| 出力（版管理の外。時刻と件数だけ） | `data/raw/oanda_mt5_probe/2026-10-16/gap_summary_r16/`: `week_metrics.csv`（112 行。K-12 の列に `holiday_*` の 4 列を追加）`8c74948fe6e9c5833ef7a749838f63785712b2bcbd4549c6bbee9f4e584e326b`、`daily_bars.csv` `01ef06d5d3c8197800f989b6255c0015f76351390c08e95192bce222676cf303`、`gap_runs.csv` `19f28e6b789cb5cc0824e74a5e4a6c452a9fbda46cf29848d5c6c7da36165690`、`holiday_excluded.csv`（40 行。週 × 休場）`e1f455f9703ad4b47feb59e7f62c77f836871571369f3700ab9bc41434d3e67a`、`report.md` `dbdf60ed245f56f38fd48257dee13b726d25db0b2b682499123d262a631e1f10`、`inputs.json` |

**外した休場（11 行。ニューヨークの時刻。外した分は 8 通貨の合計）**:

| 休場の行 | 種類 | 出典 | 区間（NY） | 外した分 | うち足が無かった分 | うち足があった分 |
|---|---|---|---|---|---|---|
| 2018-12-25 | 休場 | 546・550 | 12-24 17:00〜12-25 17:00 | 4,800 | 4,800 | 0 |
| 2018-12-26 | 再開の遅れ（東京サーバ） | 546 | 12-25 17:00〜17:10 | 80 | 80 | 0 |
| 2019-12-25 | 休場 | 644・648 | 12-24 17:00〜12-25 17:00 | 5,280 | 5,280 | 0 |
| 2019-12-26 | 再開の遅れ（東京サーバ） | 644 | 12-25 17:00〜17:10 | 80 | 15 | **65** |
| 2023-12-25 | 休場 | 1112 の表（画像） | 12-24 17:00〜12-25 17:00 | 4,800 | 4,800 | 0 |
| 2019-01-01・2019-01-02、2020-01-01・2020-01-02、2020-12-25、2021-01-01 | 休場・再開の遅れ | 546・644・746/749 の表（画像） | — | 0 | 0 | 0 |

- 外した分の数は、取引カレンダー 版 2 のセッションの中の分だけ（版 2 が既に閉じている 12-25 の 03:00〜17:00 などは数えない）。下の 6 行は代表週の外か（2019-01-01、2020-01-01、2020-12-25 はどの代表週にも入らない）、版 2 で既に閉じている（2021-01-01）ので 0。
- 3 つのクリスマスの取引日（2018・2019・2023）は、取引時間の中の分に **足が 1 本も無かった**（8 通貨すべて）。告知の休場と合う。
- 2019-12-26 の再開の遅れ（NY 17:00〜17:10）の中には足が 65 本あった（8 通貨の合計。80 分のうち）。告知の東京サーバの 7:10 の取引開始の前にも 1 分足があることを示す（使っていない）。2018-12-26 は 80 分すべて足が無く、その後も空白が続く（K-14.3）。

#### K-14.2 測定範囲の結果（旧 → 新。全 112 週。印はすべて「仮定付き（候補の間で変換が同じ）」で K-12 と同じ）

**変わったのは 2018・2019・2023 年の W2 の 24 週だけ**。W1 の 56 週と、2017・2020・2021・2022 年の W2 の 32 週は、全ての指標が旧と同じ（2017・2022 年は定義に入れていないため。2020・2021 年は代表週に効く休場が無いか版 2 と同じため）。

**年 × 週の種類（8 通貨の幅。W2 の変わった年だけ。他の年は K-12.2 の表のまま）**:

| 週 | 年 | M1 空白の割合（%） 旧 → 新 | 空白の分（8 通貨の合計） 旧 → 新 | 取引時間の分（8 通貨） 旧 → 新 | 日足 完全 / 存在すべき（8 通貨） 旧 → 新 | 15 分足の完全（%） 旧 → 新 | 1 時間足の完全（%） 旧 → 新 | M5 完全な日足の最長連続（本） 旧 → 新 | 空白の件数 旧 → 新 | うち 61 分以上 旧 → 新 |
|---|---|---|---|---|---|---|---|---|---|---|
| W2 | 2018 | 17.96〜18.00 → **9.25〜9.30** | 9,149 → 4,269 | 50,880 → 46,000 | 5/40 → 5/32 | 81.1〜81.6 → 89.6〜90.1 | 78.3〜80.2 → 86.5〜88.5 | 0〜1 → 0〜1 | 39 → 39 | 16 → 16 |
| W2 | 2019 | 10.56〜11.60 → **0.31〜1.39** | 5,597 → 302 | 51,360 → 46,000 | 2/40 → 2/32 | 84.6〜87.9 → 94.3〜97.9 | 76.6〜85.0 → 85.4〜94.8 | 0〜1 → 0〜1 | 183 → 168 | 8 → 0 |
| W2 | 2023 | 9.61〜10.44 → **0.19〜1.11** | 5,025 → 225 | 50,880 → 46,080 | 2/40 → 2/32 | 87.7〜89.4 → 96.9〜98.7 | 84.9〜87.7 → 93.8〜96.9 | 0〜1 → 0〜1 | 110 → 102 | 8 → 0 |

**通貨 × 年（W2 の新。セルは「M1 空白の割合（%）・完全な日足 / 存在すべき日足」。2017・2020〜2022 年は K-12.2 と同じ値）**:

| 通貨 | 週 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|
| USDJPY | W2 | 6.37 ・ 3/4 | 9.27 ・ 0/4 | 0.47 ・ 0/4 | 0.52 ・ 0/4 | 0.65 ・ 0/5 | 20.31 ・ 0/5 | 0.19 ・ 1/4 |
| EURJPY | W2 | 6.37 ・ 2/4 | 9.25 ・ 1/4 | 0.31 ・ 1/4 | 0.40 ・ 0/4 | 0.71 ・ 0/5 | 20.12 ・ 1/5 | 0.42 ・ 0/4 |
| EURUSD | W2 | 6.49 ・ 3/4 | 9.29 ・ 0/4 | 0.99 ・ 0/4 | 0.64 ・ 0/4 | 0.57 ・ 0/5 | 20.81 ・ 0/5 | 0.40 ・ 0/4 |
| GBPJPY | W2 | 6.32 ・ 3/4 | 9.29 ・ 1/4 | 0.38 ・ 1/4 | 0.28 ・ 0/4 | 0.67 ・ 0/5 | 20.31 ・ 0/5 | 0.19 ・ 1/4 |
| USDCHF | W2 | 6.58 ・ 2/4 | 9.30 ・ 0/4 | 1.39 ・ 0/4 | 0.92 ・ 0/4 | 2.24 ・ 0/5 | 20.93 ・ 0/5 | 1.11 ・ 0/4 |
| AUDJPY | W2 | 6.42 ・ 3/4 | 9.27 ・ 1/4 | 0.36 ・ 0/4 | 0.33 ・ 1/4 | 0.50 ・ 1/5 | 20.22 ・ 0/5 | 0.61 ・ 0/4 |
| GBPUSD | W2 | 6.41 ・ 3/4 | 9.30 ・ 1/4 | 0.70 ・ 0/4 | 0.73 ・ 0/4 | 1.21 ・ 0/5 | 20.75 ・ 0/5 | 0.62 ・ 0/4 |
| AUDUSD | W2 | 6.39 ・ 2/4 | 9.27 ・ 1/4 | 0.64 ・ 0/4 | 0.57 ・ 0/4 | 1.56 ・ 0/5 | 20.58 ・ 0/5 | 0.36 ・ 0/4 |

- 存在すべき日足が 5 → 4 本になったのは、休場の取引日（2018-12-25・2019-12-25・2023-12-25）の日足が存在すべき足から外れたため。**外れた 24 本はすべて旧で欠けの日足で、完全な日足の数（W2 で 33 本）は変わらない**。
- 取引時間の外の足（版 2 の宣言の中の足）は新でも 112 週すべてで 0。休場の中の足は 65 本（すべて 2019-12-26 の再開の遅れの 10 分の中）。

#### K-14.3 年末の週の空白（W2。長さ・時間帯）

| 項目 | 旧 → 新（W2 の 8 通貨 × 7 年） |
|---|---|
| 空白の分の合計 / 取引時間の分 | 35,375 / 360,480 → **20,400 / 345,440** |
| 長さの区分（件） | 1 分 717 → 712、2 分 158 → 158、3〜5 分 143 → 141、6〜15 分 25 → 25、16〜60 分 0 → 0、**61 分以上 48 → 32** |
| 日替わり（NY 17 時台）の空白の分 | 4,257 → 2,722。減った 1,535 分はすべて外した休場の中の分（3 つのクリスマスの取引日の始めの 1 時間 8 通貨 × 3 × 60 = 1,440 分と、再開の遅れの中の 95 分） |

**61 分以上の空白（新。32 件。すべて 8 通貨で同じ時刻。入力の不足のまま）**:

| 年 | 空白（NY） | 分 | 外していない理由 |
|---|---|---|---|
| 2017 | 12-25 17:00〜23:00 | 360 | 2017 年は告知が無く定義に入れていない（D12-68 (a)） |
| 2018 | 12-24 14:00〜17:00 | 180 | 告知の休場（12-24 17:00 から）の前。旧の 780 分の空白のうち休場の外にはみ出した分 |
| 2018 | 12-25 17:10〜23:00 | 349〜350 | 告知の再開（東京サーバ 7:10 日本時間 = NY 17:10）の後。旧の 359〜360 分のうち再開の時刻をはみ出した分 |
| 2022 | 12-25 17:00〜12-26 17:00 | 1,440 | 2022 年は取引時間表が未取得で定義に入れていない（D12-68 (a)） |

- 2019・2023 年の年末の長い空白（旧の 660 分・600 分）は、すべて告知の休場の中に入り、新では 0 件。
- 2018 年は、告知の休場と再開の時刻の外に、前日の 3 時間と再開の後の約 6 時間の空白が残る（入力の不足）。

#### K-14.4 日足の完全性とウォームアップ（日足 60 本）の連続区間の変化（観測した日に限る）

| 項目 | 旧（K-12.5） → 新 |
|---|---|
| 観測した日足（存在すべき日足） | W1 280・W2 264 → W1 280・**W2 240** |
| 完全な日足 | W1 68・W2 33 → **同じ**（W1 68・W2 33） |
| 欠けた日足 | 443 → **419**（W1 212 → 212、W2 231 → 207）。減った 24 本は休場の取引日で、欠けから完全に変わった日は 0 |
| 1 週の中で完全な日足が続いた最長 | 3 本 → 3 本（どの週も 60 本の連続は観測できない長さ） |
| 欠けた日足のうち、空白の合計が 5 分以下 / 15 分以下 | 188 / 312 → 189 / 312（2019-12-26 の GBPJPY が再開の遅れの 1 分を外して 6 → 5 分になった） |
| 欠けた日足のうち、空白がすべて NY 17:00〜18:00（日替わりの直後の 1 時間）の中の日 | 旧と新で同じ（W1 103・W2 107）。K-12.5 の 202 本（W1 103・W2 99）とは W2 の数え方が違う（K-12.5 の数え方の記録が無いため、K-14 では「その日の空白の件がすべて NY 17:00〜18:00 の中」で旧・新を同じ方法で数え直した） |

- **日替わりの直後の空白は不変**（D12-66 の原文どおり入力の不足のまま数え直した）。W1 は全指標が旧と同じで、W2 でも外した休場の外の日替わりの空白は 1 分も変わっていない。
- 年末の休場の日は「欠けた日足」から「存在すべきでない日（休場）」に変わった。休場の日は日足 60 本の連続を切らない日になるが、**その前後の日（例 2018-12-24、2018-12-26、2019-12-26）は欠けのまま**なので、年末の週で完全な日足の連続が伸びた週は無い（M5 は旧と新で同じ）。
- 再開の時刻を 7:00（東京サーバ以外の告知の時刻）で読んだ場合: 2018-12-26 と 2019-12-26 の再開の遅れの行を外さないことになり、空白が 8 通貨で 95 分（2018 年 80 分、2019 年 15 分）戻る。この 2 日の日足は 7:10 で読んでも 16 本すべて欠けのままなので、日足の完全・欠けの本数と M5 は 7:00 でも 7:10 でも同じ（`daily_bars.csv` の 2018-12-26・2019-12-26 の行の `missing_minutes` で確かめた）。

#### K-14.5 全期間では未確認のこと（代表週から全期間の実行不能を断定しない。D12-39）

- 測定していない週の空白の頻度。年末以外の休場（研究期間の中の他の祝日など）は今回の定義に入れていない（告知を集めていない）。
- 2017・2022 年の年末年始の休場（告知・表が無いか未取得。D12-68 (a) で未確認のまま。空白は入力の不足として数えている）。
- 2020・2023 年の再開の時刻（表に時刻が無い）と、表の時刻の基準（表に明記が無い。日本の日付と読んだ）。
- MT5 の 1 分足が東京サーバーの規則に従うか（2019-12-26 の 17:00〜17:10 に足があったことは、取引開始の 7:10 の前にも足が作られたことを示す。配信の休止と取引の休止は告知の上でも別。D12-61）。
- 完全な日足が 60 本続く期間があるか（実行可能期間の長さ）。今回の変更は年末の数日だけに効き、日替わりの直後の空白（W1 の空白の分の 73%）は変わらないので、K-12.5 の見通しは変わっていない。

#### K-14.6 人間が決める候補（作業役は決めていない）

1. **日付の読み（K-14.1）の承認か修正**: 告知の「12/25 休業」を、取引日（日本時間 12/25 07:00〜12/26 07:00 = NY 12-24 17:00〜12-25 17:00）を閉じると読んだ。もう一つの読みは暦日（日本時間 12/25 00:00〜24:00 = NY 12-24 10:00〜12-25 10:00）。2020・2023 年の表は時刻の基準の明記が無い。影響: 暦日で読むと **2018 年の 12-24 14:00〜17:00（NY）の 180 分 × 8 通貨が休場の中に入る**（取引日の読みでは入力の不足のまま。K-14.3）。2019・2023 年は、暦日の読みで残る分（NY 12-25 10:00〜17:00）を版 2 が既に閉じているので、空白・日足の結果は同じ。作業役は、2018・2019 年の表の「前日 通常どおり／翌日 午前7時から取引開始」に合う取引日の読みを使った。
2. **再開の時刻を東京サーバの 7:10 にするか、7:00 にするか**: 7:00〜7:10 は通常日の東京サーバのメンテナンス（6:55〜7:10。「レート更新およびチャートの形成は行われます」。お知らせ 510）と同じ時間で、2019-12-26 には足があった。影響は K-14.4 のとおり空白 95 分だけで、日足の完全・欠けは変わらない。
3. **カレンダーの新しい版**: この定義ファイルは調査用。D03 の手順（宣言制）で取引カレンダーの新しい版にするかは、後続の設計 PR で人間が承認する（D12 §11.10.2 の段 1 の判断点）。
4. **2018 年の告知の外の空白（12-24 14:00〜17:00 の 180 分、12-26 の再開の後の約 350 分）** は入力の不足のまま。根拠を探すか、このままにするか。


---

### K-15. 保有済みの配布 tick CSV 6 ファイルの、主系列（正本）候補としての検査（D12 §11.11 の段 T1。人間の第 16 回の D12-67 への回答（D12-69）による。2026-10-16 作業役。Mac の上だけ。AWS・追加の取得・戦略の成績なし。価格の水準は書かない）

人間の原文（D12-67 への回答）: 「保有済みのCSVを、APIとは独立した主系列候補として検査する。時刻、bid/ask、重複、更新のない時間、取引時間との整合を確認し、1分足から日足まで同じ経路で生成します。APIの欠損をCSVで埋める構成にはしません。」。**結果は事実の記録で、主系列にできるか（D12-69 の採否）・段 T2 へ進むか・品質の閾値は決めていない**（K-15.6 は人間が決める候補）。

#### K-15.1 方法

| 項目 | 内容 |
|---|---|
| 道具 | `tools/survey/oanda_csv_series_check.py`（新規。SHA-256 `7a318d89f23f0675d7c1a887ba344ca6a061e30d978c472c256ca3e0cbd1f3a9`）。単体テスト `tests/unit/test_oanda_csv_series_check.py`（人工データ 12 件: 元の時刻 → UTC の変換が既存の規則と一致、FAQ 2191 の月の窓、書式違反・空の価格・LAST の値・逆行・同時刻／同内容の連続・ask<bid・ask=bid・桁・跳び・間隔・セッションの外・月の外の数え、2024 年以降の行とファイルで止まること、1 か月の全分で全時間足が完全になること、1 分の空白でその日の 15 分足〜日足が 1 本ずつ欠けること、定義 (2) の休場が欠けを外すこと、ウォームアップ後の残りの計算、API との比較で CSV の分を変えないこと、出力に価格が出ないこと） |
| 入力（読むだけ。変更していない） | `data/raw/oanda/<通貨>/ticks_<通貨>-oj5k_<年>-<月>.csv` の 6 ファイル。読んだときの SHA-256 は付録 F-1 と 6 件とも一致（原データは変わっていない） |
| 時刻の変換（CSV 側） | 元の時刻（MT5 のサーバー時刻）− 7 時間をニューヨークの現地時刻とみなして UTC に直す（FAQ 2190。既存の `mt5_api_probe.server_time_to_utc`。付録 K-9.6 の (a) で、2017・2019・2023 年の 3 月の週末の空白がすべて 48.0 時間であることから米国の規則と確かめた変換）。月の窓は日本時間の 1 日 0:00〜翌月 1 日 0:00（FAQ 2191）。**API 側の時刻の規則の表 版 1 は CSV に当てていない**（本文 第11.11.6節） |
| 取引時間 | 取引カレンダー `fx_ny17` 版 2（SHA-256 `eb639afe5caeff3ee573fd28d7dda402acbc4f250af931ad3976b411061782d1`）のセッション。宣言の無い休場は推測で足さない |
| 足の生成（同じ経路） | tick の bid から 1 分足（区間に tick が 1 件も無い分は足を作らない。前値で埋めない。出来高は「不明」の 0）→ 本基盤の集約 `odyssey_fx.marketdata.application.aggregation.aggregate`（D03 §5。構成足が 1 本でも欠ける区間は足を作らない）で 15 分足 → 1 時間足 → 4 時間足（`4h_ny17`）→ 日足（`1d_ny17`）。照合として 1 時間足 → 日足（D03 §5.1 の経路）も作り、**6 ファイルとも日足の集合が同じ**だった。時間足の定義は `timeframes_v1.yaml`（`231d01c5…c710c81`）に、1 分足（UTC の 1 分刻み）を道具の中で足した |
| 空白の定義 | (1) 現行規則: 取引時間の中で tick が 1 件も無い分は空白（入力不足）、空白を 1 分でも含む足は欠け。(2) K-13 で説明できた年末年始の休場を「足が存在すべき時間」から外す: 休場の定義は K-14 と同じ `tools/survey/oanda_holiday_exceptions_2026-10-16.json`（`2c85a093…6186604`）。**3 月の 6 ファイルにはどの休場も掛からず、(2) は (1) と同じ** |
| ウォームアップ | 日足 60 本（K-12.5 と同じ）。完全な日足の連続ごとに「連続の長さ − 60」の合計を「ウォームアップ後に評価できる日足」とした |
| 出力（版管理の外。統計値と時刻だけ。生の tick は書き出さない） | `data/raw/oanda_csv_check/2026-10-16/`: `file_metrics.csv`（6 行）`c6965ec8177b08e019e838eae140d1edd15c33b0fadaa31b940dde74bc68fc4d`、`daily_bars.csv`（日足ごとの完全・欠け）`b1b3966a2f54d711a77d7225a8a6f151ad957ad0395535e95a270a9dbe464e16`、`gap_runs.csv`（空白 1 件ごと）`0a41f387c6d513e14d99269e6e75c9e6c09ed050056aff5015c9684f5d3b1082`、`api_compare.csv`（44 行）`790f07981fef1a7d2eb20ec7735d768fd9ee820a1bb56dda0ed047bb9eb5df71`、`report.md` `7f70b792bbff501540297386a7488babd3c9ca3f5e903203708a80f9fbe12615`、`inputs.json` `14288dc43d9d9c74dd255bc279bf28d8d4b10e816f69358afd3b0832bfd0d4a9` |

#### K-15.2 検査項目ごとの結果（通貨 × 月）

**時刻・bid/ask・重複**（スプレッドは point。JPY は 0.001、EURUSD は 0.00001。跳びは連続した tick の間の bid の差で、pip = 10 point）:

| 通貨 | 月 | 行 | 書式違反 | 逆行 | 月の外の行 | 同じ時刻の連続 | 同じ内容の連続 | ask < bid | ask = bid | 小数の桁（bid・ask） | スプレッド 中央値 / 99% 点 / 最大 | 20 pip 以上の跳び（最大） |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| USDJPY | 2017-03 | 10,766,964 | 0 | 0 | 0 | 649 | 378 | 0 | 0 | すべて 3・3 | 5 / 6 / 147 | 0（13） |
| USDJPY | 2019-03 | 5,607,474 | 0 | 0 | 0 | 40,442 | 37,824 | 0 | 0 | すべて 3・3 | 3 / 36 / 180 | 0（15） |
| USDJPY | 2023-03 | 6,901,857 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | すべて 3・3 | 6 / 43 / 200 | 2（39） |
| EURJPY | 2019-03 | 7,563,239 | 0 | 0 | 0 | 12,630 | 11,504 | 0 | 0 | すべて 3・3 | 5 / 52 / 254 | 0（14） |
| EURUSD | 2019-03 | 4,927,677 | 0 | 0 | 0 | 17,600 | 16,588 | 0 | 0 | すべて 5・5 | 5 / 33 / 190 | 0（14） |
| GBPJPY | 2019-03 | 17,125,123 | 0 | 0 | 0 | 24,366 | 21,906 | 0 | 22 | すべて 3・3 | 9 / 89 / 255 | 4（25） |

- 見出しは 6 ファイルとも `<DATE>	<TIME>	<BID>	<ASK>	<LAST>	<VOLUME>`。LAST・VOLUME はすべて空。空・数値でない価格、0 以下の価格は 0。上の数（行・連続・ask=bid）は付録 F-1 と一致。
- 最初と最後の tick（UTC）: 6 ファイルとも最初は前月末 15:00:00 台（日本時間の 1 日 0:00）。最後は 2017-03・2023-03 が 03-31 14:59:59 台（日本時間の月末 23:59）、2019-03 の 4 ファイルが 03-29 20:59 台（金曜の閉場。日本時間の月末は日曜）。**FAQ 2191 の月の区切りと合い、月の外の行は 0**。
- 同じ時刻の連続（ミリ秒まで同じ）は 2023-03 だけ 0。同じ内容の連続は、内容が同じ行の重複か配信の繰り返しかは CSV からは区別できない（足の 4 値には影響しない）。
- 20 pip 以上の跳びは 6 件（GBPJPY 4、USDJPY 2023-03 の 2）。桁の誤り（小数の桁の違い）は 0。跳びの時刻と原因は調べていない（価格の水準を見ないため、跳びの統計だけ）。

**更新のない時間・取引時間との整合**（取引時間の中。tick の間隔は同じセッションの中の連続した tick の間）:

| 通貨 | 月 | tick の間隔 中央値 / 99% 点 / 最大（ミリ秒） | 間隔 1 分以上 / 5 分以上 | 空白の分 / 取引時間の分（%） | 空白の件数・最長（分） | うち NY 17 時台の分 | セッションの外の tick |
|---|---|---|---|---|---|---|---|
| USDJPY | 2017-03 | 62 / 1,903 / 460,000 | 10 / 1 | 14 / 33,180（0.042） | 7・7 | 14 | 0 |
| USDJPY | 2019-03 | 100 / 3,350 / 65,970 | 4 / 0 | 1 / 30,660（0.003） | 1・1 | 1 | 0 |
| USDJPY | 2023-03 | 113 / 2,516 / 237,389 | 143 / 0 | 72 / 33,180（0.217） | 61・3 | 71 | 1,858 |
| EURJPY | 2019-03 | 60 / 2,360 / 111,720 | 2 / 0 | 1 / 30,660（0.003） | 1・1 | 1 | 0 |
| EURUSD | 2019-03 | 100 / 4,000 / 49,510 | 0 / 0 | 0 / 30,660（0.000） | 0・0 | 0 | 0 |
| GBPJPY | 2019-03 | 50 / 750 / 285,241 | 10 / 0 | 12 / 30,660（0.039） | 7・4 | 12 | 0 |

- 空白の分の数は付録 F-3 の「OANDA 欠け」（14・1・72・1・0・12）と一致（別の道具で同じ数）。
- **空白 100 分のうち 99 分がニューヨーク 17 時台（日替わりの直後の 1 時間）**。残りの 1 分は USDJPY 2023-03-28 の NY 18:06。2017-03 は米国の切替の日曜（03-12）の開場直後 17:20〜17:51 に 12 分、2023-03 はほぼ毎日 17:02〜17:59 に 1〜3 分ずつ。最長は 7 分（2017-03-12 17:22〜17:29）。**15 分を超える空白は無い**。
- セッションの外の tick は USDJPY 2023-03 だけ（1,858 件。すべて日曜の NY 15 時台 1,168・16 時台 690 = 開場の前。付録 F-1 の日曜の行と同じ）。足が存在すべき時間の外なので足には使っていない。2017-03・2019-03 は 0。
- 取引カレンダー 版 2 の休場の宣言は 3 月の窓に無く、K-13 の年末年始も掛からない（定義 (2) は (1) と同じ）。
- K-13.2 との関係（手掛かり。認定ではない）: 東京サーバーの日替わりのメンテナンス（2018-09-04〜。夏時間の日本時間 5:55〜6:10 = NY 16:55〜17:10）は「レート更新およびチャートの形成は行われます。」と告知されていて、この告知だけでは NY 17 時台の空白を説明できない。2017-03 は告知の前の期間。**空白はすべて入力不足として数えたまま**（D12-36・D12-60 (a)）。

#### K-15.3 足の生成と日足の完全性（現行規則 (1)。(2) も同じ）

| 通貨 | 月 | 15 分足 完全 / 存在すべき | 1 時間足 | 4 時間足 | 日足 | 欠けた日足（そのうち NY 17 時台の空白だけ・空白 5 分以下） | 完全な日足の最長の連続 | ウォームアップ（60 本）後に評価できる日足 |
|---|---|---|---|---|---|---|---|---|
| USDJPY | 2017-03 | 2,208 / 2,212 | 551 / 553 | 135 / 137 | 20 / 22 | 2（2・1） | 8 | 0 |
| USDJPY | 2019-03 | 2,043 / 2,044 | 510 / 511 | 126 / 127 | 20 / 21 | 1（1・1） | 13 | 0 |
| USDJPY | 2023-03 | 2,180 / 2,212 | 534 / 553 | 118 / 137 | **4 / 22** | 18（17・14） | 1 | 0 |
| EURJPY | 2019-03 | 2,043 / 2,044 | 510 / 511 | 126 / 127 | 20 / 21 | 1（1・1） | 13 | 0 |
| EURUSD | 2019-03 | 2,044 / 2,044 | 511 / 511 | 127 / 127 | **21 / 21** | 0 | 21 | 0 |
| GBPJPY | 2019-03 | 2,039 / 2,044 | 506 / 511 | 122 / 127 | 16 / 21 | 5（5・5） | 10 | 0 |

- 存在すべき足は月の窓に丸ごと入る足だけ数えた（月の初めと終わりで切れる取引日は数えない）。
- **1 か月のファイルでは、完全な日足が 60 本続くことは起こりえない**（存在すべき日足が 21〜22 本）。ウォームアップ後の残りが 0 なのは長さの制約で、品質の結果ではない。60 本の後に評価できる期間が残るかは、連続した月のファイルが無いと測れない（段 T2 の目的）。
- 欠けた日足 27 本のうち、空白が NY 17 時台だけにある日は 26 本、空白の合計が 5 分以下の日は 22 本。

#### K-15.4 API の 1 分足との並べ比較（同じ窓・同じ通貨。時刻だけ。**CSV で API を埋めていない**）

使った API の窓: 既取得の `summary_ts-*.json` の週末（API の時刻で金曜 18:00〜月曜 02:00）と火曜（00:00〜02:00）のうち、CSV の月の中に入る 27 窓（USDJPY 18、他の 3 通貨 各 3。2019-03-31 の週末は月の外で使っていない）。API の時刻は表 版 1 の標準仮説で UTC に直し、候補のある窓は候補でも並べた（44 行。`api_compare.csv`）。数は取引時間の中の分。

| 窓の種類 | 観察 |
|---|---|
| 火曜（NY 17:00〜19:00 の 2 時間。日替わりの直後を含む）、2017・2019 年 | 13 窓・延べ 21 行で、API・CSV とも空白 0 の行が 20。X-USDJPY-T2019-03-12 の候補（+3）の行だけ CSV に 1 分（03-11 NY 17:01）の空白、同じ窓の API は空白 0 |
| 火曜、2023 年（USDJPY 03-07・03-14） | **API と CSV の空白がまったく同じ分**（2 分と 5 分。両方に無い分だけで、片方だけの空白は 0）。2023 年は配布 CSV と API の 4 値が完全に一致している（付録 K-9.6）ことと合う |
| 週末（金曜の閉場の前後と日曜の開場の後） | 金曜: API だけ閉場の前の 1〜5 分が無い（NY 16:55〜16:59。CSV は閉場の直前まで tick がある。付録 K-9.2 の「最後の足の位置の 0〜5 分の違い」と同じ）。日曜: 標準（+2）で読むと 2017・2019 年の米国の切替の後の窓で API に開場の後 60 分が無く、候補（+3）では無くなる（第11.7.10節の開場の位置の観察と同じ。時刻の規則の判別には使わない。D12-55） |
| 週末、CSV に空白がある窓（USDJPY 2017-03-12・2017-03-19） | 2017-03-12 の開場の後の CSV の空白 12 分は、標準の読みでは API の空白と 12 分とも重なり、候補の読みでは重ならない（読みで位置が 1 時間ずれるため）。どちらの系列が欠けかは決めていない |

- 比べられたのは短い窓だけで、同じ週の全体（月〜金の全分）を API と CSV で並べられる既取得のデータは無い（API の代表 112 週は 6 月・12 月で、CSV は 3 月だけ）。

#### K-15.5 段 T2 に進むかの材料（見込みの有無の判断は人間）

- 見込みありの側の材料: 6 ファイルとも時刻・桁・ask<bid・月の区切りに誤りが無く、空白は 100 分（取引時間の 0.000〜0.217%）で、99 分が NY 17 時台。2019-03 は 4 通貨とも欠けた日足が 0〜5 本で、EURUSD は 21 本すべて完全。同じ 2019-03 の API の代表週は無いが、API の代表週（K-12）の W1 は 2019 年でも日足の完全が 40 本中 21 本。
- 見込みなしの側の材料: USDJPY 2023-03 は欠けた日足が 22 本中 18 本で、完全な日足の連続は最長 1 本。この割合が続く年では、今の規則（空白を 1 分でも含む日足は欠け）の下で 60 本の連続は作れない。2017-03 も 22 本中 2 本が欠け、連続は最長 8 本。
- 1 か月の長さでは「日足 60 本の後に評価できる期間」を測れない。
- **段 T2 で追加確認する場合に要る CSV（人間の取得作業）**: USDJPY と EURUSD の、それぞれ連続した 4 か月（研究期間 2017〜2023 年の中。どの 4 か月かは設計役が案を示し人間が決める。D12-8 との関係も人間が確かめる）。人間の作業: OANDA証券のマイページ「ティックデータのダウンロード」で通貨・年・月を選び、ZIP を 8 個（2 通貨 × 4 か月）ダウンロードし、展開した CSV を `data/raw/oanda/<通貨>/` に置く（ファイル名は変えない。手元の例では 1 ファイル 0.2〜0.7 GB）。置いた後、この道具を再実行すれば同じ表が作れる（連続した月のファイルをつないで日足の連続を数える処理は道具にまだ無い。段 T2 の前に足す）。
- 連続した 4 か月の取引日は約 86 本で、すべて完全でも、ウォームアップ 60 本の後に評価できる日足は約 26 本。

#### K-15.6 人間が決める候補（作業役は決めていない）

1. **段 T2 へ進むか**（上の材料。D12-69 の正式な決定の前提）。進むなら、USDJPY・EURUSD のどの連続 4 か月を取るか（2019 年と 2023 年で日足の完全の割合が大きく違うので、年の選び方で結果が変わる）。
2. **NY 17 時台の 1〜数分の空白の扱い**: CSV でも API（K-12）でも同じ時間帯に集中し、2023 年は同じ分で一致した。今の規則のままなら 2023 年のような月は日足がほとんど欠けになる。一律の許容は採らない（D12-60 (a)）ので、根拠（当時の日替わりの配信の資料）を探すか、今の規則のままにするか。
3. 20 pip 以上の跳び 6 件、同じ内容の連続（最大 37,824 件）を品質の検査の項目として扱うか（今は数えただけ。足は変えていない）。

### K-16. 全期間取得（8 通貨 × 2017-01〜2023-12 の 1 分足）の計画と見積もり（人間の第 17 回の決定「(b) API で 8 通貨の全期間を取得して検証（見積もりを先に提示）」による。2026-10-16 作業役。**AWS は起動していない・課金なし。起動は人間の承認の後**。価格の値は見ていない）

**位置付け**: 人間の承認用の計画で、まだ実行しない。数は計算の道具 `tools/survey/mt5_full_fetch_estimate.py`（単体テスト `tests/unit/test_mt5_full_fetch_estimate.py` 10 件）で出した。入力は、付録 K に記録済みの実測の数（足の本数、端末のファイルの大きさ、要求と回収の時刻、費用の単価。単価は `tools/survey/aws_mt5_probe/lib.sh` から読む）と、道具が作る人工の 1 分足（乱数の歩み。保存の大きさを測るためだけ）。配布 CSV・取得済みの価格は読んでいない。作業役の【提案】の値（上限・分け方・経路の推奨）は、人間が決めるまで確定しない。

**前もって確かめておくこと（作業役が見つけた点）**:

1. **今の回収の方法では取り出せない**。今の回収（SSM の出力で 13,500 バイトずつ受け取る。`40_collect.sh`）の実測の速さは 約 1,900 バイト/秒（段 G の zip 1,702,949 バイトを 879 秒以内。付録 K-9.3）。全期間の圧縮後の約 0.3 GB では **約 43 時間**かかり、作業上限に入らない。別の経路が要る（K-16.3。人間が決める）。
2. **この取得は「試験」の範囲の外**。D12-20 の原文は「今回はMT5 APIの小規模試験だけを目的とし、全期間の取得は行いません。」。費用は試験予算 5 米ドルの残りに収まる見込みだが（K-16.5）、性質は本取得なので、**全体予算 100 米ドルからの支出として人間の承認が要る**。
3. **保存・継続利用の条件は未確認のまま**。D12-19 (b)【決定】により、OANDA の取得データの保存・加工後の継続利用・解約後の扱いは未確認で、本文は「全期間の取得の前に、人間がこの未確認の条件を見て進めるかを改めて判断する」としている（本文 第10.6.4節・第11.2節）。今回は初めて 2017〜2023 年の価格を一括で保存する。

#### K-16.1 取得範囲

| 項目 | 内容 |
|---|---|
| 通貨 | USDJPY・EURJPY・EURUSD・GBPJPY・USDCHF・AUDJPY・GBPUSD・AUDUSD（端末の銘柄名は各 `<通貨>.cl`。付録 K-6.3） |
| 期間 | API の時刻の値で 2017-01-01 00:00 から 2024-01-01 00:00 の前まで（各月を 1 要求）。API の時刻は UTC より 2〜3 時間進むので、両端は UTC で 2016-12-31・2023-12-31 の 21:00〜22:00 ごろ。どちらも週末の休場の中（土曜・日曜）で、足は落ちない |
| 種類 | 1 分足だけ（tick は要求しない。2017〜2023 年の tick はこの経路では取れない見込み。付録 K-7） |
| 列 | `time`（API の生の値。秒）・`open`・`high`・`low`・`close`（bid で作った足。`chart_mode` 0）・`tick_volume`・`spread`・`real_volume`（FX では 0。付録 K-6.1）。道具の `RATE_FIELDS` と同じ |
| 時刻 | **API の生の時刻をそのまま保存し、変換しない**（D12-47【決定】。UTC は Mac で時刻の規則の表 版 1 から導く） |
| 価格 | **保存する**（研究期間 2017〜2023 年だけ）。2024 年以降は要求しない・保存しない（D12-21。道具の上限 `REQUEST_END_LIMIT` = 2024-01-01T00:00Z を維持） |
| 端末が要求の外で取り寄せる分 | 1 分足の履歴は 2012〜2026 年、tick は 2026 年の分（付録 K-7・K-9.1）。読まない・回収しない。インスタンスの終了でディスクごと消える（これまでと同じ） |

#### K-16.2 要求の設計【提案】

| 項目 | 案 | 理由 |
|---|---|---|
| 分け方 | **1 要求 = 1 通貨 × 1 か月**。要求の数 **8 × 84 = 672**。通貨の順は上の表、各通貨の中は新しい月から（K-6.3 と同じ向き） | 1 か月は 約 31,000 本（上限 33,120 本 = 23 取引日 × 1,440 分）。端末の既定の最大バー数 100,000 より少なく、1 要求の応答（約 2 MB）も小さい。月の境目は API の時刻の 00:00 で隙間・重なりなし（テストで確認） |
| 1 要求の本数の上限 | 端末と API の上限の文は公式資料に無い（付録 K-5.1）。実測では 1 週 7,195 本・3 年 1,114,328 本（人間の端末の Request。K-11.1）まで返った。月単位なら上限の心配はほぼ無い | — |
| 区間の終わり | `copy_rates_range` は終わりちょうどの足も返す（K-5.1）。その足は区間の外として保存しない（今の `split_by_window`）。次の月の最初の足として次の要求で保存されるので、欠けも重複も出ない | — |
| 0 本の月 | 10・30・60・120 秒待って出し直す（今の `SURVEY_RETRY_WAITS`。出し直しは要求の数に数えない）。それでも 0 本なら「空」と記録して次へ進む。**通貨の最初の月が 0 本なら、その通貨の残りを出さずに止めて報告**（端末の取り寄せが始まっていない合図。K-9.1 の「limited by charts settings」） | 2017〜2023 年のどの月にも取引日はあるので、0 本は取り寄せの問題とみなす |
| 要求の上限 | 計画 672、**上限 750**（残り 78 は「空」の月のやり直しだけに使う。他の区間は足さない） | K-10 と同じ考え方（計画＋やり直しの枠） |
| 保存形式 | **gzip の CSV**（1 ファイル = 1 通貨 × 1 か月、`<通貨>_<YYYY-MM>_m1.csv.gz`。見出し行あり。価格は Python の float の最短の表記で、丸めない）。代わりの案は K-16.8 の 1 | Windows 側は標準ライブラリだけで書ける（新しい依存が要らない）。原データは CSV（`data/raw/market` と同じ）、Parquet は受入れ後の snapshot だけ（ADR-0013）という今の分け方と揃う |
| ファイルごとの記録 | `manifest.json`: ファイルごとに 通貨・端末の銘柄名・月・要求の区間（API の時刻）・行数・最初と最後の足の時刻・区間の外の件数・出し直しの回数・`last_error`・gzip の SHA-256・中身（展開後）の SHA-256。全体に 道具の SHA-256・端末の build と `maxbars`・銘柄の `point`・`digits`・`chart_mode` | 回収の後に Mac で SHA-256 を照合し、再開のときに取得済みの月を見分ける |
| 取得の記録 | `fetch_log.jsonl`: 要求ごとに 要求の区間・応答の本数・実行の時刻・ディスクの空き。**価格の値は書かない** | — |
| 道具の変更 | 承認の後、実行の前に作業役が `mt5_api_probe.py` に段 `fullfetch` を足す（上の分け方・0 本の扱い・上限・ディスクの空き 3 GB での停止・取得済みの月の飛ばし・manifest）。単体テスト付き。今の段の挙動は変えない | 今回は道具を変えていない（見積もりの道具だけを足した） |

#### K-16.3 回収の経路（人間が決める。今の方法では約 43 時間）

| 案 | すること | 速さ | 人間の作業・権限 | 費用（税込み） | 良い点・弱い点 |
|---|---|---|---|---|---|
| (A) **S3 の一時のバケット**（作業役の推奨） | Windows から東京リージョンの一時のバケット（タグ `Project=odyssey-mt5-probe`）に通貨ごとに置き、Mac で受け取って SHA-256 を照合し、バケットを消す | 未測定（数 MB/秒の見込み。1 MB/秒でも約 5 分） | IAM のポリシーの更新（作業役のユーザーに、名前が `odyssey-mt5-probe-` で始まるバケットの作成・置く・読む・消す。SSM 用のロールに、そのバケットへ置く権限）。コンソールで約 10 分 | 0.01 未満（置く期間は最大 48 時間で見積。単価は作業役の知識の値で未照合）。送信は他の案と同じ | 自動で回収でき、インスタンスを終了してもバケットにデータが残る（途中で止まっても失われない）。新しい種類の資源（S3）を使う |
| (B) SSM のポート転送 | Mac から SSM の転送の通り道を開き、Windows の中だけで聞く小さな HTTP の送り手から受け取る | 未測定 | Mac に AWS の Session Manager のプラグインを入れる。IAM に転送の開始の権限を足す | 送信だけ | 新しい保存先を作らない。Mac への導入物が増える。インスタンスの終了の前に受け取り終える必要がある |
| (C) RDP のフォルダの共有 | 人間が Windows App で Mac の空のフォルダを共有して接続したまま、作業役が人間のセッションのタスクでそこへ写す | 未測定 | IAM の変更なし。人間が回収の間（10〜30 分の見込み）接続を保つ | 送信だけ | 権限を増やさない。人間の在席が要る。共有のフォルダの設定を誤ると別の場所が見える |
| (D) 今の方法（SSM の出力で分けて受け取る） | — | 実測 約 1,900 バイト/秒 → **約 43 時間** | なし | — | 作業上限（K-16.6）に入らないので使えない |

- どの案でも、回収したファイルは Mac で manifest の SHA-256 と照合し、合わなければその月を「未回収」として扱う。

#### K-16.4 端末の準備（人間の RDP の作業。K-10.4 を 8 通貨・2017〜2023 年に広げた）

1. K-8.3 の 1〜4（パスワードを出す、RDP で接続、端末を入れる、サブアカウントにログイン）。〔約 10〜15 分〕
2. 「ツール」→「オプション」→「チャート」→「チャートの最大バー数」を**一度別の値（例 100000）にして OK、もう一度開いて Unlimited にして OK**。「ファイル」→「終了」で端末を終了し、起動し直す。〔約 3 分〕
3. 「表示」→「銘柄」で、8 通貨の `<通貨>.cl` を 1 つずつ選び、「バー」で M1・**2017.01.01〜2023.12.31** を Request する。「limited by charts settings」が出ないことと、本数が出ることを確かめる。**チャートは開かない**（研究期間の中だけを Request する）。〔1 通貨 1〜3 分、計 約 10〜25 分。取り寄せは 8 通貨で約 2.4 GB。前回は 8 通貨で約 20 分〕
4. チャートの窓・気配値表示の窓をすべて閉じ、RDP を「切断」にする（サインアウトしない）。司令塔に「設定して切断した」と伝える。〔約 1 分〕
5. （経路 (C) を選んだ場合だけ）作業役の合図で、フォルダの共有を付けて接続し直し、回収の間 接続を保つ。〔10〜30 分〕

- **人間の時間: 約 25〜45 分**（経路 (C) なら さらに 10〜30 分）。ほかに経路 (A)・(B) の IAM の更新が起動の前に 約 10 分。
- 翌日の確認（請求書と Cost Explorer。K-2 の手順 8）は今までどおり。

#### K-16.5 容量・時間・費用の見積もり（米ドル、消費税 10% 込み。単価は K-4 と同じ。道具の出力）

| 項目 | 想定 | 上限 | 根拠 |
|---|---|---|---|
| 要求 | 672 | 750 | K-16.2 |
| 行数 | 約 2,080 万 | 約 2,230 万 | 想定は USDJPY の 3 年 1,114,328 本（K-11.1）÷ 36 か月 × 672。上限は 1 か月 33,120 本 |
| 保存（gzip の CSV） | 約 0.28 GB | 約 0.30 GB | 人工の 1 分足で 1 行 約 13.4 バイト（4 通りの最大） |
| 保存（展開後の CSV） | 約 1.06 GB | 約 1.14 GB | 1 行 約 51 バイト |
| 端末の同期（受信。無料） | 1 分足の履歴 約 2.4 GB | ＋2026 年の tick 約 0.8 GB | 1 通貨 約 297 MB（K-7・K-9.1）。tick は K-7 の最大 |
| ディスク（60 GB） | — | 使用 約 26.2 GB・空き 約 33.8 GB | 履歴の後の使用 24.75 GB（K-9.1）＋ tick ＋ 出力と zip。**60 GB で足りる。拡張は不要**（`ec2:ModifyVolume` の権限も使わない） |
| API の取得の時間 | 約 11 分 | 45 分で打ち切り | 段 G の 112 要求が 15.7 秒（1 要求 0.14 秒、約 7,000 本。K-9.3）を本数で比例させ、書き出し（1 秒 10 万行の見込み）を足した |
| 回収 | 経路 (A)〜(C) で 約 5〜30 分 | 30 分 | K-16.3（(D) は約 43 時間で使えない） |
| インスタンス時間 | 約 1.8 時間 | 段の上限の和 約 3.8 時間。費用の上限は 6 時間で見積 | 起動・準備 20（上限 30）分、人間の作業 35（60）分、取り寄せ 20（40）分、取得 11（45）分、検算・zip 5（15）分、回収 10（30）分、片付け 5（10）分 |
| 送信 | 約 0.38 GB | 約 2.1 GB | 想定は gzip の量＋RDP など 0.1 GB。上限は圧縮しないまま送った場合＋1 GB |
| **この回の費用** | **約 0.22** | **約 2.11** | 上限は 6 時間・CPU のクレジットを標準にし損ねた場合・送信 2.1 GB を重ねた |
| 止め忘れ | 1 時間 約 0.094 | — | K-10.3 と同じ |

| 予算 | 上限 | 使用済み（K-11.4） | この回の後（想定） | この回の後（上限） |
|---|---|---|---|---|
| 試験予算（D12-20） | 5 | 約 0.55 | 約 0.77（残り 約 4.23） | 約 2.66（残り 約 2.34） |
| 全体予算（第 3 回の決定） | 100 | 約 0.55 | 約 0.77（残り 約 99.23） | 約 2.66（残り 約 97.34） |

- 金額は試験予算の残り（約 4.45）にも収まるが、**全期間の取得は D12-20 の「試験」の範囲の外**（冒頭の 2）。どちらの予算として扱うかを人間が決める（K-16.8 の 4）。
- 圧縮の比は人工データで測った。実データでは違いうるが、送信の上限は圧縮しない場合で見積もったので、費用の上限は変わらない。

#### K-16.6 上限と終了条件【提案】

| 項目 | 案 |
|---|---|
| 作業時間（3 段） | **要求の打ち切り**: 起動から 2 時間 30 分（新しい要求を出さず、回収に移る）。**作業役の終了**: 起動から 4 時間（`95_deadline_guard.sh` と `check_deadline`。回収が途中でも終了する）。**Windows の中の自動の停止**: 4 時間 30 分（止め忘れの備え） |
| 要求 | 上限 750（K-16.2） |
| ディスク | 60 GB（拡張しない）。空きが 3 GB を切ったら次の要求を出さない（今の仕組み） |
| 回収・送信 | 回収の合計は 1 GB まで（今の `PROBE_COLLECT_LIMIT_BYTES`）。gzip の上限 0.30 GB の約 3 倍 |
| 回収の単位 | **通貨ごとに**（1 通貨の 84 か月が済むたびに回収）。打ち切りや終了で止まっても、済んだ通貨は Mac に残る |
| 途中で止まった場合 | 同じ起動の中: manifest の済んだ月（SHA-256 が合うもの）を飛ばして続ける。終了の後: Mac の manifest にある月を飛ばす計画で、**次の起動は人間の承認の後**（追加の起動を自動では行わない）。経路 (A) なら、バケットに置いた分は終了の後も回収できる |
| 終了条件 | (1) 672 か月がすべて「取得済み・回収済み・SHA-256 一致」になったら片付けて閉じる。(2) 打ち切り・上限・通貨の最初の月の 0 本で止まったら、その時点の済んだ分を回収して片付け、未取得の月の一覧を報告して閉じる（続けるかは人間） |
| 片付け | `90_teardown.sh --execute`: インスタンスの終了（ディスクも消える）、セキュリティグループ・キーペア・Mac の秘密鍵の削除、残りの確認（K-9.1 と同じ 9 項目）。経路 (A) ならバケットの中身とバケットを消し、残り 0 を確認する。**IAM のユーザー・ロール・ポリシーは消さない（人間の判断）** |

#### K-16.7 取得の後の処理（Mac。別の作業役が行う。AWS は不要）

取得したファイルは人間の承認した置き場（K-16.8 の 2）に置き、原データとして変えない（ADR-0013 と同じ扱い。作り直さず、変えたいときは別の取得日の置き場にする）。後の処理は次の道具の変更が要る（どれも価格の値を報告に出さない）:

1. **複数月をつないで読む部分**（新規）: manifest の SHA-256 と行数を確かめてから読み、月の境目で重複・欠けが無いことを確かめる。時刻は生のまま持つ。
2. **時刻の規則の表 版 1 の適用**: `mt5_gap_aggregate.py` の `time_rule`（表 版 1 の行と標準仮説・感度の候補）を、週の入力（`summary_ts-G.json`）だけでなく 1 の出力にも使えるようにする。候補の規則ごとに UTC に直す（D12-49・D12-51 (b)）。
3. **空白の集計の全期間版**（付録 K-12・K-14 の道具を全期間に）: 入力を月のファイルに広げ、年末年始の休場の定義（`oanda_holiday_exceptions_2026-10-16.json`）は同じものを使う。代表週の集計の出力（K-12・K-14）は変えない。
4. **日足の連続の数え直し**（本文 第11.10節の段 3 の全期間版）: 通貨 × 候補の規則ごとに、日足の完全・欠けの本数と、準備期間（日足 60 本）＋評価期間を含む連続区間の長さを数える。足の生成規則（無記録の分を含む足をどう作るか）は第 17 回の決定で設計役が【提案】を出すので、規則が決まる前は今の規則（空白の分を 1 つでも含む足は欠け）で数え、規則が決まったら同じ入力で数え直す。
5. **週末の境界の全週の検査**（本文 第11.7.4節の 2・第11.8.4節の一覧の 4。任意）: 全週の金曜の閉場・日曜の開場の位置を数える。表の改訂は判定条件を満たす証拠が出たときだけ（一覧の 16）。

#### K-16.8 人間が決める点（作業役は決めていない）

1. **保存形式**: (a) gzip の CSV（作業役の案）／(b) Parquet（Windows に pyarrow の導入と版・ハッシュの固定が要る）／(c) 圧縮しない CSV（約 1.1 GB。送信が約 4 倍）。
2. **保存先**: (a) `data/raw/oanda_mt5/<取得日>/<通貨>/`（manifest・取得の記録は `<取得日>/` の直下。ADR-0013 の一覧に無いので、改訂が要る。本文 第11.8.4節の一覧の 11）／(b) 試験の置き場 `data/raw/oanda_mt5_probe/<取得日>/full/`（D12-25 の決定の範囲。試験と本取得が同じ場所に混ざる）。どちらも `.gitignore` の `data/raw/*` で版管理の外。manifest を版管理に入れるかも合わせて決める。
3. **回収の経路**: K-16.3 の (A) S3（作業役の推奨。IAM の更新が要る）／(B) SSM のポート転送／(C) RDP のフォルダの共有。
4. **ディスクの拡張**: 不要（60 GB で空き 約 33.8 GB の見込み）でよいか。
5. **費用の承認**: この回 想定 約 0.22・上限 約 2.11 米ドル（累計 上限 約 2.66）。「試験」ではなく本取得として全体予算 100 米ドルから出すか。
6. **保存・継続利用の未確認の条件**（D12-19 (b)）を見て、価格の一括の保存に進むか。
7. **上限の値**（K-16.6 の 2 時間 30 分・4 時間・4 時間 30 分、要求 750）でよいか。
8. **起動の日時**: 人間が RDP の作業（約 25〜45 分）と起動から 2 時間ほど対応できる時間帯。

**道具の準備完了（2026-10-16 作業役。AWS は起動していない）**: 人間の第 18 回の決定（S3 の一時バケット・gzip の CSV・`data/raw/oanda_mt5/<取得日>/<通貨>/`・上限は案のとおり）に沿って、道具の段 `fullfetch`・`fullverify`（`tools/survey/mt5_api_probe.py`）、スクリプト `38_full_fetch.sh`・`45_collect_s3.sh`・`90_teardown.sh` の S3 の片付け、上限の値（`lib.sh`）、IAM の変更文面（`iam_policy_operator.json`・`iam_policy_ssm_role_s3.json`。人間が更新する）、手順（`tools/survey/aws_mt5_probe/README.md` の「全期間取得の実行手順」）と単体テスト `tests/unit/test_mt5_full_fetch.py` を足した。

### K-17. 全期間取得の実施の記録（人間の第 18 回の決定 D12-79 と起動の合図「merge して IAM を作った。確認した」による。2026-10-16〜17 作業役（運用役）。**価格の値は見ていない・書いていない**。件数・時刻・SHA-256 だけ）

#### K-17.1 結果の要約

- 8 通貨 × 2017-01〜2023-12 の 1 分足を、**計画の 672 か月すべて取得・回収・照合した**。0 本の月は 0、出し直しは 0。要求は 672 件で、上限 750 に対して残り 78。2024 年以降の要求は出していない（区間の終わりは 2024-01-01T00:00 を含まない）。
- 置き場: Mac の `data/raw/oanda_mt5/2026-10-16/<通貨>/<通貨>_<YYYY-MM>_m1.csv.gz`。672 ファイル、計 約 228 MB。`.gitignore` の `data/raw/*` で版管理の外。`manifest.json`・`fetch_log.jsonl` は `2026-10-16/` の直下に置いた。
- manifest の写しを版管理に入れた（人間の第 18 回の回答「入れる。取得記録 K-17 の別 PR で docs 配下に置く」）: `docs/data/oanda_mt5_full_fetch_2026-10-16/manifest.json`。中身はファイル名・SHA-256（gzip と展開後）・行数・最初と最後の足の時刻・状態で、価格は含まない（列の名前だけ）。
- AWS はすべて片付けた（K-17.6）。費用は この回 約 0.27 米ドル、累計 約 0.82 米ドル（K-17.7）。

#### K-17.2 時刻の記録（UTC）

| 時刻 | 出来事 |
|---|---|
| 14:00〜14:01 | `00_check.sh`（問題なし。料金の通知（Budgets）は無いが、以前に許可済み）→ `10_launch.sh --execute`。インスタンス i-0c000d31ddcd6e34b（t3.medium、gp3 60 GB、ap-northeast-1c、AMI ami-0ba097b3fdf9feef7 `Windows_Server-2022-English-Full-Base-2026.10.14`）を 14:01:04 に起動。`95_deadline_guard.sh` を裏で起動した（終了の時刻 18:01:04） |
| 14:01〜14:08 | `20_prepare.sh --execute`: Python 3.12.10、MetaTrader5 5.0.6231（numpy 2.5.4）、道具 `mt5_api_probe.py`（SHA-256 `62062bf7…fe89bdd`。Windows で一致を確認）。人間に RDP の合図を送った |
| 14:08〜14:10 ごろ | 人間が RDP に接続できなかった。原因は人間の送信元 IP が変わったこと（場所の移動）。司令塔から届いた人間の新しい IP の /32 を、セキュリティグループの RDP（3389）に足した。既存の /32 は残し、他のポートは開けていない。インスタンスのステータスチェックは 2/2 正常、RDP の待ち受け・ファイアウォールも正常だった |
| 14:10〜14:39 | S3 に置く道具の不足に対処した（K-17.4 の 1）。AWS CLI v2 を導入 |
| 14:46〜14:58 | 人間の Request の後、端末の履歴の取り寄せが通貨ごとに進んだ（K-17.3） |
| 14:58 | `38_full_fetch.sh --pairs USDJPY --execute` が、要求を出す前にバケット名を作る行で止まった（K-17.4 の 2） |
| 15:03 | S3 の一時バケット `odyssey-mt5-probe-2026-10-16-t8fm92og` を手作業で作った（K-17.4 の 2）。15:03 に 38 を再実行 |
| 15:18〜15:33 | Windows で道具のタスクが 15:18 に始まった。最初の要求は 15:33:27 で、それまでは端末への接続を待っていた。その間に他の 7 通貨の取り寄せが続いていた（K-17.3） |
| 15:33〜16:31 | 8 通貨を順に取得した。1 通貨 約 7 分 |
| 16:04 | 人間の指示で要求の打ち切りを 17:31 まで延長した（K-17.4 の 3）。取得は旧の打ち切り 16:31 の前に終わったので、延長は結果として使っていない |
| 16:31 | 最後の通貨（AUDUSD）を回収した |
| 16:32〜16:35 | `90_teardown.sh --execute` で片付けた（K-17.6） |

#### K-17.3 人間の作業と端末の履歴の取り寄せ

- 人間は MT5 の端末を入れてサブアカウントにログインした。最大バー数を一度別の値にしてから Unlimited に戻した（manifest の `maxbars` = 100000000）。8 通貨の `.cl` をそれぞれ M1・2017.01.01〜2023.12.31 で Request した（USDJPY・EURJPY・EURUSD は複数回）。表に本数が出たのは USDJPY だけで、他の通貨は空のままだった。
- SSM で読み取りだけ確認したところ、端末の履歴フォルダ（`bases/OANDA-Japan MT5 Live/history/<通貨>.cl/*.hcc`）は通貨ごとに増えていた。表が空だったのは取り寄せ中だったためと読む。
  - 15:38 に 8 通貨すべてが 約 296〜298 MB（各 15 ファイル）で増加が止まった
  - 取り寄せ中、端末は 2 コアの約 78% を使っていた。端末のログに "limited" やエラーは無かった
- 道具のタスクの最初の要求は、取り寄せがほぼ終わるまで待たされた（15:18 から 15:33）。次回は、全通貨の履歴フォルダの増加が止まってから取得を始めるとよい。
- 端末: OANDA MetaTrader 5、build 6266（manifest の `terminal`）。

#### K-17.4 計画との違い（3 件。どれも司令塔・人間の判断の後に行った）

1. **AWS CLI v2 を導入物に追加**（理由: AWS Tools for PowerShell（AWSPowerShell 5.0.313）の読み込みが 300 秒超）。
   - Windows に元からあった S3 の道具は AWSPowerShell だけで、AWS CLI は無かった。
   - `Get-Command Write-S3Object` の読み込みに 324 秒・352 秒かかった（2 回計測。キャッシュは効かない）。`38_full_fetch.sh` は S3 の道具の確認を 120 秒の制限で行うので、時間切れになる見込みだった。
   - 司令塔の判断「A」で、公式の配布元 `https://awscli.amazonaws.com/AWSCLIV2.msi` から入れた。SHA-256 `AD07F9ACD855F48AD016E63B2F0B4F87783C1B6524B86E966F4A6D21D01F524A`、Authenticode の署名は有効（Amazon Web Services, Inc.）、aws-cli/2.38.1。
   - 導入後、38 と同じ確認が 26 秒で通った。
2. **S3 の一時バケットを手作業で作成**（スクリプトの不具合）。
   - `38_full_fetch.sh` の 62 行目 `$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 8)` が、毎回終了コード 141 で止まった（再現 3/3）。head が 8 文字で読むのをやめると、上流の tr が強制終了の合図（SIGPIPE）を受け取る。スクリプトは `set -euo pipefail` なので、ここで止まる。偽の aws による確かめ（K-3）では、この行を通っていなかったとみられる。
   - 要求はまだ出ておらず、バケットも作られていなかった。
   - 司令塔の判断「A」で、道具は変えずに、スクリプトと同じ 3 手順を手で行った。手順は、名前を作る（パイプの失敗を無視する形）、`assert_probe_bucket` で名前の規則を確かめる、`s3api create-bucket`（ap-northeast-1）。続いて `s3_bucket.env` に `S3_BUCKET`・`FETCH_DATE`・`S3_CREATED_AT_UTC` を書いた。その後の 38 は、既存のバケットとして変更なしで使った。
3. **要求の打ち切りを人間の指示で延長**。
   - 人間の原文: 「打ち切り時間は伸ばしてもいいですよ。完了させましょう。」（2026-10-17 約 01:10 JST）。
   - 司令塔の指示で 16:31 から 17:31 に延ばした。`lib.sh` の `FULL_TIME_LIMIT_SECONDS` 9000 → 12600 を Mac の作業コピーだけで変え、コミットせず、取得の後に元に戻した。
   - 終了（18:01）・Windows の中の自動の停止・要求の上限 750・費用の上限は変えていない。見張り（95）は終了の時刻だけを見るので、変えていない。
   - 実行中の 38 は旧の値で動いていたが、16:31:11 に最後の通貨を取り終えた。延長の値は結果として使っていない。

#### K-17.5 取得の結果（manifest から。価格は含まない）

時刻は API の生の時刻（変換しない。D12-47）。最初と最後の足は、通貨の全 84 か月での最小と最大。

| 通貨 | 銘柄 | 取得済み | 0 本 | 出し直し | 行（足の本数） | 区間の外の足（保存しない） | 最初の足 | 最後の足 | gzip の合計（バイト） |
|---|---|---|---|---|---|---|---|---|---|
| USDJPY | USDJPY.cl | 84 / 84 | 0 | 0 | 2,602,100 | 50 | 2017-01-02 00:01 | 2023-12-29 23:59 | 28,817,857 |
| EURJPY | EURJPY.cl | 84 / 84 | 0 | 0 | 2,603,459 | 52 | 2017-01-02 00:03 | 2023-12-29 23:59 | 30,698,358 |
| EURUSD | EURUSD.cl | 84 / 84 | 0 | 0 | 2,599,711 | 51 | 2017-01-02 00:00 | 2023-12-29 23:59 | 28,662,902 |
| AUDJPY | AUDJPY.cl | 84 / 84 | 0 | 0 | 2,605,026 | 50 | 2017-01-02 00:02 | 2023-12-29 23:59 | 29,653,262 |
| GBPJPY | GBPJPY.cl | 84 / 84 | 0 | 0 | 2,603,936 | 49 | 2017-01-02 00:04 | 2023-12-29 23:59 | 32,136,332 |
| USDCHF | USDCHF.cl | 84 / 84 | 0 | 0 | 2,591,600 | 51 | 2017-01-02 00:00 | 2023-12-29 23:59 | 27,632,017 |
| GBPUSD | GBPUSD.cl | 84 / 84 | 0 | 0 | 2,597,174 | 52 | 2017-01-02 00:01 | 2023-12-29 23:59 | 31,098,087 |
| AUDUSD | AUDUSD.cl | 84 / 84 | 0 | 0 | 2,597,832 | 51 | 2017-01-02 00:01 | 2023-12-29 23:59 | 28,006,957 |
| 計 | | 672 / 672 | 0 | 0 | 20,800,838 | 406 | | | 236,705,772 |

- 要求の時刻: 最初 15:33:27、最後 16:30:51（UTC）。`fetch_log.jsonl` は 672 行（要求ごとの区間・本数・時刻・ディスクの空き。価格なし）。
- 銘柄の属性（manifest の `symbols`）: 8 通貨とも `chart_mode` 0（bid の足）。JPY の組は `digits` 3、他は 5。
- 未取得の通貨・月: なし（全期間で取得済み）。

#### K-17.6 回収・照合・片付け

- 回収: 通貨ごとに Windows から S3 の一時バケットに置き、`45_collect_s3.sh` で Mac に受け取った。manifest の SHA-256・行数と照合してから置いた。
  - 8 回とも照合の問題 0 件。照合が合わずに置かなかったファイルは無い
  - 回収の合計 239,862,671 バイト（上限 1 GB の内）
  - 既存の `data/raw` のファイルは変えていない
- 片付けの後の照合: `uv run python -m tools.survey.mt5_api_probe fullverify --root data/raw/oanda_mt5/2026-10-16` → 問題 0 件（672 ファイル）。
- 記録のファイルの SHA-256: `manifest.json` `e4664b011600337d619270986e243d11f467f2be425a841b70e5851807daca53`（docs の写しも同じ）、`fetch_log.jsonl` `0868f4bd0840f41b7bda2c3d7a6b37aaee9dba52af424b7b54da7de53088d9fa`。
- 片付け: `90_teardown.sh --execute`（16:32〜16:35）。
  - インスタンスを 16:32:34 に終了（ディスクも削除）
  - セキュリティグループ、キーペア、Mac の秘密鍵を削除
  - S3 の一時バケットを中身ごと削除した。スクリプトの `s3api wait bucket-not-exists` は「Max attempts exceeded」で止まった。直後の `head-bucket` は 404（バケットは無い）で、もう一度 `90_teardown.sh --execute` を実行して記録を退避し、残りの確認を通した
  - 残りの確認: 試験のインスタンス・ボリューム・スナップショット・AMI・Elastic IP・ネットワークインターフェース・セキュリティグループ・キーペア・S3 の一時バケットは、すべて 0
  - 見張りは状態のファイルが無くなったのを見て、16:33:35 に終わった
- IAM のユーザー・ポリシー・ロール（インラインの `odyssey-mt5-probe-s3-put` を含む）は消していない（人間の判断）。

#### K-17.7 費用（米ドル、税込みの概算。Cost Explorer の反映は翌日）

| 項目 | この回 |
|---|---|
| インスタンス・ディスク・IPv4（起動から終了まで 2.53 時間。`90_teardown.sh` の概算） | 約 0.24 |
| S3 から Mac への送信（約 0.24 GB）と S3 の要求・保管 | 約 0.03 |
| **この回の計** | **約 0.27**（上限 約 2.11 の内） |
| 累計（K-16.5 の使用済み 約 0.55 と合算） | 約 0.82（試験予算 5 の残り 約 4.18） |

#### K-17.8 後続の事項（この PR では行わない）

1. **道具の修正**（後続 PR）:
   - `38_full_fetch.sh` の 62 行目を、パイプの失敗で止まらない形（例: `head -c 8 </dev/urandom` から作る形）にする
   - この行を実際に通るテストを足す
   - S3 の道具の確認の 120 秒の制限を見直す（AWSPowerShell の読み込みが 300 秒超）か、AWS CLI v2 の導入を `20_prepare.sh` に入れる
   - `90_teardown.sh` の `s3api wait bucket-not-exists` の失敗の原因を確かめる（`head-bucket` の 403 の扱いの可能性）
2. データ配置の決定記録（ADR-0013）の改訂（置き場 `data/raw/oanda_mt5/<取得日>/<通貨>/` の追加。K-16.7・本文 第11.8.4節の一覧の 11）。
3. 取得の後の処理（K-16.7 の 1〜5）。

### K-18. 全期間取得の道具の運用規則（2026-10-17 の人間の決定。本文 第0.1.27節に原文）

付録 K-17 の実施で問題になった 3 件を直した道具（`tools/survey/aws_mt5_probe/`、PR #83）が従う規則。K-17 の記録は変えていない。今回は AWS を起動しておらず、規則は偽の `aws` と偽の PowerShell の部品で確かめた（確かめた範囲と実環境で未確認の範囲は、道具の README の「全期間取得の後の道具の修正」）。

| # | 規則 | 道具での実装 |
|---|---|---|
| ① S3 の一時バケットの削除の後の確認 | 間隔 10 秒・最大 30 回、head-bucket で確かめる。404 を確かめた時点で成功。上限に達したら記録を残して異常終了する。待ちの合計は約 5 分だが、**通信の時間を含む厳密な「5 分以内」の保証ではない** | `lib.sh` の `wait_bucket_absent`、`S3_DELETE_WAIT_SECONDS=10`・`S3_DELETE_WAIT_ATTEMPTS=30`。上限では `s3_bucket.env` を残し、残りの確認を表示して終了コード 1（`90_teardown.sh`） |
| ② 403 などの扱い | 「バケットの有無が不明」とする。削除の前に不明なら削除の操作を行わず、記録を残して異常終了する。削除の後に不明なら上限まで確かめ直し、404 を確かめられなければ記録を残して異常終了する | `lib.sh` の `s3_bucket_state`（200 = ある、404 = 無い、それ以外 = 不明）。`90_teardown.sh` の `teardown_bucket` |
| ③ msiexec の終了コード 3010 | 準備の処理を止める。3010 は「導入は成功、完了には再起動が必要」なので、通常の導入の失敗と区別して「導入済み・再起動が必要」と表示する。自動の再起動・自動の続行はせず、人間が再起動した後に準備のスクリプトをもう一度実行するよう案内する | `lib.sh` の `awscli_install_ps` が `AWSCLI_RESTART_REQUIRED` の行を出して止まり、`20_prepare.sh` がそれを見て案内を表示し、終了コード 1 で止まる。回帰テストあり |
| ④ 導入済みの AWS CLI の版 | 正常に動く 2.x（`aws --version` が成功し `aws-cli/2.` で始まる）なら、版を問わず入れ直さない。新しく入れるのは 2.38.1 に固定する。導入済みの場合も、実際に使う正確な版をログに残す | `lib.sh` の `AWSCLI_VERSION`・`AWSCLI_MSI_URL`・`AWSCLI_MSI_SHA256`。`20_prepare.sh` が「使う AWS CLI の版: aws-cli/x.y.z」をログに出す |

- 見送った任意の改善（原文のとおり）: 署名者の確認が Subject の文字列の部分一致であること、削除の操作（`s3 rm`・`delete-bucket`）の失敗の分岐に回帰テストが無いこと。理由は PR #83 の本文。

## 改訂履歴

| 版 | 日付 | 内容 |
|---|---|---|
| v0.1 案 | 2026-10-15 | 初版 |
| v0.2 案 | 2026-10-15 | 節 D（OANDA のサンプル、同じ UTC 区間・同じ取引カレンダーでの比較、FXCM の ask < bid の調査、HistData の取引カレンダーでの年ごとの表、FXCM の利用許諾）と節 B-9（有料候補の追加調査）を追加。調査の時刻の注記を追加 |
| v0.3 案 | 2026-10-15 | 節 E（Tick Data への申請文。日本語と英語。送信していない）と節 F（OANDA の 6 ファイルの形式と品質、OANDA の時刻の資料、同じ UTC 区間での比較、tick の間隔の分布、HistData の参考の最長区間、ハッシュ）を追加 |
| v0.4 案 | 2026-10-15 | 出典の突き合わせで Dukascopy の規約の原文を H-2 に追記。付録 E に「検討記録。送信しない（人間の決定）」の注記。節 G（問い合わせの質問文。送っていない）、節 H（FXCM・Dukascopy の利用条件）、節 I（OANDA の利用条件と時刻の資料）を追加 |
| v0.5 案 | 2026-10-15 | 節 J（OANDA MT5 Python API の経路の記録: OANDA・MetaQuotes の公式ページの逐語、実測の環境の費用の資料、保存したページのハッシュ）を追加。G-1 の問い 6 を MT5 API の問いに改めた |
| v0.6 案 | 2026-10-15 | 節 J-5（AWS 上の Windows での試験の費用の資料: EC2・EBS・データ転送の公開の価格表、EC2・EBS・VPC の料金ページの原文、保存したファイルのハッシュ）を追加 |
| v0.7 案（作業記録） | 2026-10-15 | 節 K（AWS 試験の作業記録: 人間の事前の作業 K-1（12 手順）、試験の途中の人間の作業 K-2（9 手順）、作業役の作業の記録 K-3、費用の再見積 K-4）。人間の第 8 回の分担による。スクリプトは `tools/survey/aws_mt5_probe/` |
| v0.7 案（作業記録の追加） | 2026-10-15 | 節 K-5（API の 1 分足の項目、時刻の約 2 時間のずれの観察と仮説、配布 CSV との差の原因の候補と検証）。人間の第 9 回の指示による |
| v0.7 案（作業記録の追加） | 2026-10-15 | 節 K-6（時刻のずれの追加の検証: 2023-03-28 は −3 時間で配布 CSV の bid と完全に一致。8 通貨の範囲の確認はディスクの空きの不足で止めた） |
| v0.7 案（作業記録の追加） | 2026-10-15 | 節 K-6.3（8 通貨の 1 分足の範囲: 2017-01・2019-03・2023-12 とも取れた）と節 K-7（端末が要求の区間の外で取り寄せた期間とデータの種類: 1 分足は 2012〜2026 年、tick は 2026-01〜10 だけ） |
| v0.9 案 | 2026-10-15 | 節 K-6.2 の tick の要求の区間の数を、挙げた日付に合わせて 3 区間に直した（要求は再試行を含めて計 5 回） |
| v0.11 案（作業記録の追加） | 2026-10-16 | 節 K-8（時刻の調査（D12 §11.7）と空白の測定（§11.9）の準備: 道具とスクリプト、要求の一覧 67＋112、人間の作業、時間と費用の再見積）。AWS は起動していない |
| v0.11 案（作業記録の追加） | 2026-10-16 | 節 K-9（時刻の調査と空白の測定の実施: 端末の履歴の取り寄せの不具合と対処、時刻の調査の 4 区分（USDJPY 2017〜2023 は仮説 US、2017・2019 の S2 で E1 と E2 が割れた）、空白の測定 112 週の時刻の取得（未集計）、片付けと費用の実績） |
| v0.13 案（作業記録の追加） | 2026-10-16 | 節 K-9.5（E4 を人間の第 11 回の基準で再判定。42 週末すべてが USDJPY と同じ規則を支持、分単位の残差を保存）、K-9.6（S2 の割れの点検: 処理の誤りは無し、評価の道具の調査漏れの箇所と修正、bid・ask・mid の補助の比較、週末の開場が取引時間の設定で決まる可能性）、K-10（最後の調査の計画。実行していない） |
| v0.14 案 | 2026-10-16 | 人間の第 12 回の決定（D12-42 修正付きで承認、D12-43 旧「時刻未確定なら除外」→ 新「確認済み／仮定付き」）を反映: K-10 の見出しと冒頭に承認と段 4 の変更の注記、K-10.5 の終了条件を改めた（(i) は根拠付きで特定した規則で採用し、米国式でないことを除外の理由にしない。適用期間と他の通貨への根拠を記録。(ii)「除外して閉じる」を削除し「仮定付きとして閉じる」に置き換え。除外の区間を「仮定付きになりうる区間」に改め、K-10 で決まらない区間を本文の区分の案（D12-45・D12-46）へ）。K-9.6 の末尾の【提案】に新方針での置き換えの注記。K-10 はまだ実行していない（費用の累計は 約 0.48 米ドルのまま） |
| v0.15 案 | 2026-10-16 | K-10.2 に作業役の道具の事実 2 件を反映（2017-03-26・2019-03-31 の週末を状態 S2E として分けたこと。判定に使う金曜の閉場は S2M と同じ。閉場のずれが +2 時間なら規則の名を「EU, FIXED」と併記すること）。K-10.5 に規則の名の併記と時刻の規則の表（本文 D12-47）の列との整合の書き方【提案】と、人間の第 12 回の続きの決定（D12-44〜D12-46）の注記を足した。K-10 の見出しに起動の合図を注記。K-11（作業役の記録）には触れていない |
| v0.16 案 | 2026-10-16 | K-9.2 の直接確認の行に、S2・S4 の週末の読みが開場の側だけによることの注記を足した（本文 第11.7.10節、D12-55）。K-11（作業役の記録）には触れていない |
| v0.17 案 | 2026-10-16 | K-9.2 の注記に D12-55【決定】（開場の位置だけの読みを「両方と整合（判別には使えない）」に改め、仮定付きに下げる。旧の記録は残す）を反映。K-11 には触れていない |
| v0.18 案 | 2026-10-16 | 本文 v0.18 に合わせて版を揃えた。付録の記録の内容は変えていない（K-11 を含む） |
| v0.19 案（作業記録の追加） | 2026-10-16 | 節 K-12（代表 112 週の空白の集計。作業役。Mac の上だけ。人間の第 14 回の指示、D12-37・D12-39・D12-51 (b) による） |
| v0.20 案（作業記録の追加） | 2026-10-16 | 節 K-9.7（評価の道具に現行の基準（判定条件 版 1）を当てた区分と、K-9.5 との差分。作業役。人間の第 14 回の続きの回答による。旧の記録 K-9.2・K-9.5 は変えていない） |
| v0.20 案 | 2026-10-16 | 本文 v0.20 に合わせて版を揃えた（K-12 の結果を本文 第11.9.7節に、K-12.7 の候補 4 件を本文 第6節の D12-60〜D12-63 に、K-9.7 の結果を本文 第11.7.8節・第11.7.9節に、K-9.7 の C7 の読みの割れを本文 第6節の D12-64 に反映）。付録の K 節の本文は設計役は変えていない |
| v0.21 案 | 2026-10-16 | 本文 v0.21 に合わせて版を揃えた（人間の第 15 回の決定 D12-60〜D12-64 を本文 第0.1.22節・第6節に記録し、次の作業計画を本文 第11.10節に置いた）。節 K-13（OANDA の公式資料の調査。調査役の記録を設計役が保存 HTML と照合して記録）を追加。既存の K 節の本文は変えていない |
| v0.22 案 | 2026-10-16 | 本文 v0.22 に合わせて版を揃えた（人間の第 16 回の決定 D12-65〜D12-68 と設計変更案 D12-69 を本文 第0.1.23節・第6節・第11.11節に記録）。付録の K 節の本文は設計役は変えていない（K-14・K-15 は作業役が記録する） |
| v0.22 案（作業記録の追加） | 2026-10-16 | 節 K-15（保有済みの配布 tick CSV 6 ファイルの主系列候補としての検査。D12 §11.11 の段 T1、D12-69。道具 `tools/survey/oanda_csv_series_check.py`。作業役） |
| v0.23 案 | 2026-10-16 | 本文 v0.23 に合わせて版を揃えた（人間の第 17 回の決定 D12-70〜D12-74 を本文 第0.1.24節・第6節に記録し、配布 tick 記録を再現するモデルの 3 つの規則と適用範囲の【提案】を本文 第11.12節に置いた。K-14.6 の 1・2 は D12-72、K-15.6 の 1 は段 T2 を行わない（D12-74）、K-15.6 の 2 は D12-71 で答えられた）。付録の K 節の本文は設計役は変えていない |
| v0.22 案（作業記録の追加） | 2026-10-16 | 節 K-16（全期間取得（8 通貨 × 2017-01〜2023-12 の 1 分足）の計画と見積もり。人間の第 17 回の決定による。AWS は起動していない。計算の道具 `tools/survey/mt5_full_fetch_estimate.py`。作業役） |
| v0.24 案 | 2026-10-16 | 本文 v0.24 に合わせて版を揃えた（人間の第 18 回の決定: 規則 D12-75〜D12-78 は 4 件とも (a)、全期間の取得の計画 K-16 を承認（D12-79。K-16.8 の点ごとの回答は本文 第6節の「D12-79 の内訳」）を本文 第0.1.25節・第6節・第11.12節に記録）。付録の K 節の本文は設計役は変えていない。取得の記録は K-17 として別の PR |
| v0.24 案（作業記録の追加） | 2026-10-17 | 節 K-17（全期間取得の実施の記録: 8 通貨 × 84 か月 = 672 ファイルを取得・回収・照合（0 本の月 0）、計画との違い 3 件（AWS CLI v2 の導入、S3 の一時バケットの手作業での作成、人間の指示による要求の打ち切りの延長）、片付けと費用）。manifest の写しを `docs/data/oanda_mt5_full_fetch_2026-10-16/manifest.json` に置いた。作業役 |
| v0.24 案（道具の運用規則の追加） | 2026-10-17 | 節 K-18（全期間取得の道具の運用規則: S3 の削除の後の確認、403 などの扱い、msiexec の 3010、導入済みの AWS CLI の版。人間の決定 2026-10-17、本文 第0.1.27節に原文）。K-16・K-17 は変えていない。PR #83 |
