"""唯一の構成ルート（D01 §7.2）。

ポートと adapters を結線し、設定済みのユースケースを組み立てる。
設定ファイルの解析は `odyssey_fx.app.config` の責務であり、ここでは行わない
（D01 §6 の契約 "F5c: config parsers only in app.config"）。

段階−1 では結線対象がないため、責務の宣言だけを置く。
"""

from __future__ import annotations
