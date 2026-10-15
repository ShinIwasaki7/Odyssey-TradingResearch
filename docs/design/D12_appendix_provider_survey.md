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
> 6. ティックデータの配布の対象（USDJPY・EURJPY・EURUSD・GBPJPY）以外の通貨ペア（USDCHF・AUDJPY・GBPUSD・AUDUSD）の過去の tick を、他の方法（API・MT5 など）で取得できますか。その条件を教えてください。

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

---

## 改訂履歴

| 版 | 日付 | 内容 |
|---|---|---|
| v0.1 案 | 2026-10-15 | 初版 |
| v0.2 案 | 2026-10-15 | 節 D（OANDA のサンプル、同じ UTC 区間・同じ取引カレンダーでの比較、FXCM の ask < bid の調査、HistData の取引カレンダーでの年ごとの表、FXCM の利用許諾）と節 B-9（有料候補の追加調査）を追加。調査の時刻の注記を追加 |
| v0.3 案 | 2026-10-15 | 節 E（Tick Data への申請文。日本語と英語。送信していない）と節 F（OANDA の 6 ファイルの形式と品質、OANDA の時刻の資料、同じ UTC 区間での比較、tick の間隔の分布、HistData の参考の最長区間、ハッシュ）を追加 |
| v0.4 案 | 2026-10-15 | 出典の突き合わせで Dukascopy の規約の原文を H-2 に追記。付録 E に「検討記録。送信しない（人間の決定）」の注記。節 G（問い合わせの質問文。送っていない）、節 H（FXCM・Dukascopy の利用条件）、節 I（OANDA の利用条件と時刻の資料）を追加 |
