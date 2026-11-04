# 研究期間の 7 通貨の受入れ（順 7 の A3b）— 確定の一覧

研究期間（2017〜2023 年、取得日 2026-10-16）の USDJPY を除く 7 通貨 × 2 組の確定 snapshot の一覧（D03 §17.9）。承認の材料と手順の詳細は `acceptance_summary.md`。価格・成績は書いていない。

- 受入れ・分類を行ったコミット: `c5a9f33b7d2bcf23c865353ada8dd6b09803e94a`（origin/main）。固定ファイル 7 つ（D12 §12.1）と確認済みハッシュの一覧（`configs/datasources/oanda_mt5_m1_verified_raw_2026-10-16.yaml`、SHA-256 `2b548743e16e33da162427efff8d73df6b00c409d29b9f178a6aefcb2ceae64f`）の SHA-256 は `acceptance_summary.md` §2。
- 全期間の集計（PR #85）との突き合わせ: 14 個すべて一致（`acceptance_summary.md` §5）。
- 分類ファイルの要約（週・区間・本数・分類）: `acceptance_summary.md` §4。分類の確認の原文: `human_decision_2026-11-04_classification.md`。
- 承認: まだ（質問 2 の後に `data approve` で記入する）。

列: 作った足は確定の後の 1 分・15 分・1 時間・4 時間・日足の数。無記録の分・取得不足は 1 分足の系列の数。除いた足は分類で除いた 1 分足（標準の組）。カレンダーの外の足は候補の組で足の生成に使わず件数だけを報告した 1 分足（D12-97 (a)）。manifest の SHA-256 は承認の前の値。

| 通貨 | 組 | 最終の識別子 | 暫定の識別子 | 原ファイル（数・行） | 作った足（1m・15m・1h・4h・1d） | 無記録の分 | 取得不足 | 休場帯の足（分類前） | カレンダーの外の足 | 除いた足 | 除いた理由 | 分類ファイル SHA-256 | manifest SHA-256 | integrity_report SHA-256 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| EURJPY | standard | `db6cf465bcfd40ff250bdd70c254fbde3dd3c1c42d538f4b731456f21fc0380d` | `09ec35dbaf2d3ed79cb1d63975557d842484d67d261dbf3647d790fa6a2ff2cc` | 84・2,603,459 | 2,603,380・174,056・43,525・10,887・1,815 | 11,620 | 0 | 79 | — | 79 | D12-99「セッション外のデータ異常として使わずに確定」 | `af62fb2c0bd41667d479c92ea567bb0c422e7628dd7569018121acdbbf67bb20` | `05607ddc929f68267275b01110c8d1c8a532e55bf1da530c7b4e4fe0d0183aca` | `a1c4067f404db40c4351fc5bae89bd2862d0b114532709bb13d3510ffba2f403` |
| EURJPY | candidate | `50475022304808c7468b638bdc1536e2bef906e28b6a2618a03ed80bcd713545` | `50475022304808c7468b638bdc1536e2bef906e28b6a2618a03ed80bcd713545` | 84・2,603,459 | 2,602,704・174,009・43,514・10,887・1,815 | 12,296 | 0 | — | 755 | 0 | 分類で除いた足なし（カレンダーの外の足は D12-97 (a)） | `f212062cf9fbf1c75b6383a045c96523631d8391b3c3c134a79ec3527302a60a` | `9185810b1ffab5002e26b4e92bb95c5f2af1d2e64a80c51b09b8110672eacce3` | `16b96c96c0ea9d393c44b2824f0e72cf82cc3ca1707d9b16931534fe0f054dfb` |
| GBPJPY | standard | `455d1d38f924f2711aea51eebea7d5d8d24708505e64c7085c7b099333fa5188` | `655ee966a3d690777f75ee51ef6f5a57a3f150cf0089f2e42243b504925ef2a6` | 84・2,603,936 | 2,603,859・174,000・43,511・10,885・1,815 | 11,141 | 0 | 77 | — | 77 | D12-99「セッション外のデータ異常として使わずに確定」 | `8dfa574080ea0bf0eaf88b40ec581388e2fc57a83b954e9d908b5b55f7b33e2f` | `3b0291433d18f0c88fd8a879b63188bf0dbd391343ada7026670e4d19aad0e7e` | `eac3e370072705e9f90f98141791c11dc4308e22b5bd91fead1fe989a9a5c9a5` |
| GBPJPY | candidate | `3141cedcd378652b1f66493b23b3e52be09efee0568d6929281704938ef6377b` | `3141cedcd378652b1f66493b23b3e52be09efee0568d6929281704938ef6377b` | 84・2,603,936 | 2,603,182・173,952・43,499・10,885・1,815 | 11,818 | 0 | — | 754 | 0 | 分類で除いた足なし（カレンダーの外の足は D12-97 (a)） | `51dd9f9fe304e7ec0118050f393b55dd883b6a627014861938b30883ede509b6` | `1fbc41827f08d13a552eb6e6c7bccf5dfe1352986fb3d5e3fc7bca1528e158cf` | `900ceef05d0c0d3651fa6f5e885d816bafd859ae0b44c8ac364d5459d2fc5639` |
| AUDJPY | standard | `dc411b9ec1054b073d0c610d74bad27a6801d85c9b46f76ff0a0ac705dc36843` | `0e294575562ad3d80ca76032334a27532d77e64086e4f01d3ac98d9a567895a6` | 84・2,605,026 | 2,604,948・174,054・43,527・10,887・1,815 | 10,052 | 0 | 78 | — | 78 | D12-99「セッション外のデータ異常として使わずに確定」 | `3808045e8ea233f65d4d09eaaaf41e2773ca49bf06ec33b89c4c31fbbf68a170` | `04e10636291b13c33454ad04c5471da2c1f277721db2110a1aa983abe9bda6bc` | `8df4f679b173d16a9259f7b4bfb4639923d52c8ef9fabeb89b0076e828965865` |
| AUDJPY | candidate | `ce0aac848a58e2845b286bd839da421c213e918814a9911a52bbba6fa6825821` | `ce0aac848a58e2845b286bd839da421c213e918814a9911a52bbba6fa6825821` | 84・2,605,026 | 2,604,271・174,006・43,515・10,887・1,815 | 10,729 | 0 | — | 755 | 0 | 分類で除いた足なし（カレンダーの外の足は D12-97 (a)） | `b57c446030cc339c9843d6967972455c28e744237a8498e223e1b3fe5be5529e` | `e5703fddec8f7db6dbb7a1a7143416dbd5f5b7a0524fae27f89c60dc4c9e755b` | `eef2d36c0818d1ed26f1a7c749ac365d6b92eb9c293c7e1369b031398ef0f828` |
| EURUSD | standard | `4e6eb8d40df023685b6b0739bd202d132c97997de68e8d55f0b4114c89bb917b` | `210554bb9a4336bc27e199b2f67157cde74962afc2d366267b6d5899635d2e67` | 84・2,599,711 | 2,599,636・174,072・43,526・10,887・1,815 | 15,364 | 0 | 75 | — | 75 | D12-99「セッション外のデータ異常として使わずに確定」 | `4abffddca1e9d9d83790d379f992e172e2b0bea309e5e450726a6b9a66b5aa0e` | `ad2d80402a3737623d003095b43019ef26822501cf38cd300bd4a8e3ba023899` | `3948c2f670c883b6faf49a65cf240188e7f8b0354da22abcd342b302d89277eb` |
| EURUSD | candidate | `9c03bc1283e5c7474dc1602104d943fe7985b11c745dae3135813fc7ddee1b19` | `9c03bc1283e5c7474dc1602104d943fe7985b11c745dae3135813fc7ddee1b19` | 84・2,599,711 | 2,598,959・174,024・43,514・10,887・1,815 | 16,041 | 0 | — | 752 | 0 | 分類で除いた足なし（カレンダーの外の足は D12-97 (a)） | `29e58eb1919becf2f316d4c7f34a38dbbeb1a60a1a18db93a3894c9157d1f778` | `8d0419ddc2a441f3f9e8185d48f37e7e84c328bb4df0e81e990fad8aa87551dc` | `cf4ad7816a51c5c5b67f84c1d207b5747e5f4f4a979b1b2ef42922d26e03eb3c` |
| GBPUSD | standard | `b9ca3a2142bf7c498c5b0b1fb803c0c44df6938dad76a11bd6203e9667437f6e` | `e6d77cc492cd3791873cf31e6a2930df7f4da5e889bc23e5cbd0daf775235ed8` | 84・2,597,174 | 2,597,096・174,070・43,526・10,887・1,815 | 17,904 | 0 | 78 | — | 78 | D12-99「セッション外のデータ異常として使わずに確定」 | `3ffb3ddda8cd6bf0d20c333f7d09907ca6f30fe35737268c7118265ed2efe800` | `eee8a6cd3bce100d56e50d2e10a4aa9ae9404aea8d7e4b31faf0e8307cd5a667` | `d2dbf17f0ace54e9d7cbab884372e9c3d05725bb1907f32397c793d4394b7a7c` |
| GBPUSD | candidate | `35bcd732a0d696eb95766094e35e4aeab0cdb779e3abae1326691155c7f7732e` | `35bcd732a0d696eb95766094e35e4aeab0cdb779e3abae1326691155c7f7732e` | 84・2,597,174 | 2,596,420・174,023・43,515・10,887・1,815 | 18,580 | 0 | — | 754 | 0 | 分類で除いた足なし（カレンダーの外の足は D12-97 (a)） | `244eea3d2967952af01ed0a1feb9359009ca1e24a49025c13694075e0251009d` | `be251cca78d45456e434a3aeba63cc674022fd1a3833dfde71174ecf8ea4a920` | `78baecac0e9558b719ada2011a20f1367134b5caa4a8360101c03484e841de65` |
| AUDUSD | standard | `07362e2223e99b24030b61433c0cc6a78c1a34cbbe9708e1cc04adb744bfe75d` | `3fc8b8bf197476ed2f02ffb325bb83d5239935240fa13485bc56eed09483ed08` | 84・2,597,832 | 2,597,759・174,071・43,524・10,887・1,815 | 17,241 | 0 | 73 | — | 73 | D12-99「セッション外のデータ異常として使わずに確定」 | `fc6783745464b9d100f3e7ea331331021264ef8240ca2374e7d3b29cc4ba5ba2` | `276577252e24e7044279a014a071e4643ec7c689e14b8ef5faf5e40502e92512` | `113c6a5c80f16ebabd696dc10eb5825b6e03ef56936fc959e7a05256d117e69f` |
| AUDUSD | candidate | `b9d213ea6fd957a5d317b33c3472ef3c72a0292d0fed77a4b0003cbf65b074b1` | `b9d213ea6fd957a5d317b33c3472ef3c72a0292d0fed77a4b0003cbf65b074b1` | 84・2,597,832 | 2,597,083・174,023・43,512・10,887・1,815 | 17,917 | 0 | — | 749 | 0 | 分類で除いた足なし（カレンダーの外の足は D12-97 (a)） | `53190a064b1c3205d5411a0be0a50de9b5720c57444a2ae204d9994bd92e8cb2` | `7952adc5b7f586686e30196890b4e7bc50c4ee3cd2e579872537fa3bfcf285a1` | `f66d1c90d3ff242f36be8b5da438ea433a97da44e97d4598d47cb99da313447f` |
| USDCHF | standard | `1139abd01c3d1f1d4c564e5f123e4a979b42b8ed8ae1fc8b15b0d27752472298` | `71e03dd008c3b61919da894ef885100d270aaf50d1c9b0d6bb49546601f22061` | 84・2,591,600 | 2,591,528・174,071・43,524・10,887・1,815 | 23,472 | 0 | 72 | — | 72 | D12-99「セッション外のデータ異常として使わずに確定」 | `1b333ddfe55905595917ac7422717413a9ccea51d259c0849bf86af26922a268` | `4d31e3e2d67b836675620e22f4b45180995dd775e282b235978d20fe4d39a8e8` | `55c5da149c0b027e449c819b316deb6aa9150da9cfa5a4bb943f3c8fed97c2c7` |
| USDCHF | candidate | `3d5e44b84807ddf1e46251e9b430a6bcf0c62323062ceb07dfda1df83fcc6c62` | `3d5e44b84807ddf1e46251e9b430a6bcf0c62323062ceb07dfda1df83fcc6c62` | 84・2,591,600 | 2,590,854・174,023・43,512・10,887・1,815 | 24,146 | 0 | — | 746 | 0 | 分類で除いた足なし（カレンダーの外の足は D12-97 (a)） | `0bd5e26e0865f986d3d0910c2d46528ae25a1afd3216c41d484387f18bb44cf5` | `e5c5d54c3fe0e428d7b0cc91caa44aee38102f3e3e1ba77d1738dfeb2a26aa7f` | `d07118f36614076b21ffe81924aafae9e9a81868dd87174edb81cd11567fcafa` |

標準の組の暫定の検査報告 `integrity_report_provisional.json` の SHA-256（暫定の `integrity_report.json` と同じ）:

- EURJPY: `bfd54db8b462f8101282dc77d2b263df1aeabdb0fd6ae9e23d39c2f8e78cb1e7`
- GBPJPY: `2f4346b403258a07a0a4800e1298725cc7e363c45765e55d001ee9d761403ed5`
- AUDJPY: `c679e07c08a1aeb7e36b3cfa0f10709e5fef82ef99aefa996a8fdb66b6733252`
- EURUSD: `b5a424e80c26641a6739dc69022e663c756cf0e6ee6e0bfd1bb273804c7ab137`
- GBPUSD: `951c7c4d89813f8104f0662b99eb06dfb0dbc7142abd3fc782c998afc96f1d51`
- AUDUSD: `bfce1efec8ba201759044b8f5cca8e27e04668c67e1aa5c1177b641ef2ac7c47`
- USDCHF: `4a983e579d2daf6d759b5538d86658702b797439326b2662810273bc81177790`

## 手順の記録

1. 固定ファイルの照合（`shasum -a 256`）: すべて一致。
2. 受入れ（通貨ごとに 2 回、1 個ずつ順に。作業木 `.claude/worktrees/step7-a3b-acceptance` の根で）:
   ```
   uv run odyssey-fx data accept-mt5 --fetch-date 2026-10-16 --symbol <通貨> --variant <standard|candidate> \
     --raw-root <本体の作業ディレクトリ>/data/raw/oanda_mt5 --out <本体の作業ディレクトリ>/data/snapshots
   ```
3. 質問 1（分類の確認）の後、分類と確定（14 回、1 個ずつ順に）:
   ```
   uv run odyssey-fx data classify --pending <暫定の識別子> --decisions docs/data/step7_research_acceptance/<通貨>/<組>/classification.yaml \
     --timeframes configs/calendars/timeframes_v2.yaml --out <本体の作業ディレクトリ>/data/snapshots
   ```
4. 質問 2（snapshot の承認）の後に承認の記入（未実行）:
   ```
   uv run odyssey-fx data approve --snapshot <最終の識別子> --by ShinIwasaki7 --out <本体の作業ディレクトリ>/data/snapshots
   ```
