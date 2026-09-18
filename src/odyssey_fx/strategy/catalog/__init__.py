"""strategy の部品カタログ。

各部品を「契約＋実装＋状態型」の1組として登録する（features, conditions, permissions,
triggers, filters, orders, protection, exits と、ImplementationRef の登録・解決）。
実装は純粋関数の形をとり、可変状態を内部に持たない。
"""
