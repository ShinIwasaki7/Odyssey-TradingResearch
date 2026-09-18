"""backtest の口座・建玉層。

台帳の原子的更新と投影、MTM、通貨換算を置く。
balance（実現損益・費用反映）と equity（MTM 込み）を区別する。
"""
