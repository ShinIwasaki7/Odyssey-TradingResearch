"""backtest の記録層。

記録ポートへの書き出し、BacktestResult の正規化 DTO、run manifest を置く。
ID 連鎖（機会→試行→注文→約定→建玉→管理要求、予約）で辿れることを保つ。
"""
