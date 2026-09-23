"""バックテストのフェーズ集合（D06 §4.1）。

フェーズの順位と全列挙は `backtest.engine.phases`（D06）が正本である。戦略ランタイムは
取引機会の遷移に処理点（時刻・フェーズ・通し番号）を押すが、引くのは名前だけであり、順位の
意味には踏み込まない。

段階2 でエンジンが実装されたため、テストは**同じ定数をそのまま使う**。ここで組み立て直すと
正本が2つになり、フェーズ名や順位が食い違っても気付けなくなる。
"""

from __future__ import annotations

from odyssey_fx.backtest.engine.phases import BACKTEST_PHASES, PHASE_ORDER

__all__ = ["BACKTEST_PHASES", "PHASE_ORDER"]
