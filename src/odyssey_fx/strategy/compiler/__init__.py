"""strategy のコンパイラ。

StrategyDefinition を検証（参照・型・arity・パラメータ・評価スケジュール・能力）し、
依存グラフの構築と循環検出、評価順の導出を行い、
不変で内容ハッシュ付きの CompiledStrategy を作る。
"""
