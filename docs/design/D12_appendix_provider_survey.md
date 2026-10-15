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

---

## 改訂履歴

| 版 | 日付 | 内容 |
|---|---|---|
| v0.1 案 | 2026-10-15 | 初版 |
