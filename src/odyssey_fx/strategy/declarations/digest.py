"""宣言の内容ハッシュ（D04 §13.2、Q2 決定）。

「この実行は前とまったく同じ戦略か」を機械的に判定するための指紋である。計算方法そのもの
は D02 §9.3 の正規化エンコードを使い、本モジュールが決めるのは**何を対象に含めるか**だけ
である。

| 参照 | 対象 |
|---|---|
| `ContractRef.digest` | `ComponentContract` から **`implementation_ref` を除いた**全フィールド |
| `StrategyRef.digest` | `StrategyDefinition` 全体（各使用箇所の `contract_ref` を通じて
  契約の指紋を含む） |

契約の指紋から実装参照を除くのは、**実装コードを直すたびに人間が管理する戦略の版まで
変わってしまう**のを避けるためである。実装の同一性は解決済み設定の指紋
（`CompiledStrategyRef`、D05 §5.5）にだけ入れる。

順序に意味を持たせないコレクションは構築時に正規化済みなので（D04 §3）、ここで並べ替えを
重複実装しない。

**制限**: 正規化エンコード（D02 §9.3）は `timedelta` を符号化できない。したがって期間値
（`max_age`・`DurationWindow`・`DurationDeadline`）を含む宣言は、現状このダイジェストを
計算できず `KernelValueError` になる。検証戦略 A（段階2）の宣言はいずれも期間値を含まない
ため実行経路には現れないが、符号化規則を足すかどうかは D02 の改訂を要する未決事項である。
"""

from __future__ import annotations

from dataclasses import fields

from odyssey_fx.common import canonical
from odyssey_fx.common.refs import ContentDigest, ContractRef, StrategyRef
from odyssey_fx.strategy.declarations.contract import ComponentContract
from odyssey_fx.strategy.declarations.definition import StrategyDefinition

__all__ = ["contract_digest", "contract_ref_for", "strategy_digest", "strategy_ref_for"]

#: 契約の指紋から外すフィールド（D04 §13.2、Q2 決定）。
_CONTRACT_DIGEST_EXCLUDED = frozenset({"implementation_ref"})


def contract_digest(contract: ComponentContract) -> ContentDigest:
    """契約の内容ハッシュ（D04 §13.2）。`implementation_ref` を除いて計算する。"""
    payload = {
        item.name: getattr(contract, item.name)
        for item in fields(contract)
        if item.name not in _CONTRACT_DIGEST_EXCLUDED
    }
    return canonical.digest(payload)


def contract_ref_for(contract: ComponentContract) -> ContractRef:
    """契約を指す参照（ID ＋版＋指紋）を作る（D02 §9.2）。"""
    return ContractRef(
        component_id=contract.component_id,
        version=contract.version,
        digest=contract_digest(contract),
    )


def strategy_digest(definition: StrategyDefinition) -> ContentDigest:
    """戦略定義の内容ハッシュ（D04 §13.2）。

    各使用箇所が持つ `contract_ref` を通じて契約の指紋を含むため、**実装コードの同一性は
    含まない**。
    """
    return canonical.digest(definition)


def strategy_ref_for(definition: StrategyDefinition) -> StrategyRef:
    """戦略定義を指す参照（ID ＋版＋指紋）を作る（D02 §9.2）。"""
    return StrategyRef(
        strategy_id=definition.strategy_id,
        version=definition.version,
        digest=strategy_digest(definition),
    )
