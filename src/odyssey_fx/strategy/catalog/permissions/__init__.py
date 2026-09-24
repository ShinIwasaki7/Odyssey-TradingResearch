"""市場状態を作る部品（D05 §4.7、段階3）。

条件から取引許可への変換（`permission_from_condition`）と、固定値の条件
（`constant_condition`）を `from_condition` に置く。条件の作り方は `conditions` に任せ、
比較の規則を2か所に分けない。
"""
