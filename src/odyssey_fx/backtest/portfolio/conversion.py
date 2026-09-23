"""通貨換算の経路（D06 §8.5・§8.5.1、Q7・Q9 決定）。

段階2（USDJPY・JPY 口座）は決済通貨と口座通貨が一致するため、換算は**率1の恒等換算**に
なる。それでも経路を通すのは、段階3 以降でクロス通貨の脚を足すときに呼び出し側が変わらない
ようにするためである。

経路の選び方は決定論的に固定する（D06 §8.5.1 の規則1）。

1. 決済通貨と口座通貨が同じ → **恒等換算**（`legs=()`、`rate=1`、`skew=0`）
2. 直接のペアが snapshot にある → その1本
3. 無ければ基軸通貨を経由する2本
4. それも無ければ換算不可

恒等換算を「脚が0本の経路」で表すのは、`ConversionLeg` が系列・足・観測時点を必須にして
おり、参照する市場系列が存在しない恒等換算では架空の観測を書くことになるためである。

段階2の実装は 1 だけを通し、2・3 は段階3 で埋める（D06 §8.5.1 の末尾）。**型と記録項目は
段階2 から置く**ので、段階3 で表15 の列が増えない。

経路の**宣言型**（`ConversionPath` / `ConversionLeg`）は `domain.policies` にある。根拠記録
（`trace`）がそれを項目に持つ一方、中間層4つは相互に import できないためで、ここには経路を
**決める規則**だけを置く（D06 §3 の置き場所との差異は `domain.policies` に書いた）。
"""

from __future__ import annotations

from datetime import timedelta

from odyssey_fx.backtest.domain.policies import ConversionPath, ConversionPolicy
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import ConversionRate, CurrencyCode, decimal_from_int
from odyssey_fx.common.refs import EvidenceRef
from odyssey_fx.common.time import UtcTime

__all__ = [
    "ConversionUnavailable",
    "identity_path",
    "rate_of",
    "resolve_path",
]

#: 恒等換算の率。
_ONE = decimal_from_int(1)


class ConversionUnavailable(Exception):
    """換算経路が組めなかった（D06 §8.5.1 の規則1(d)・規則6）。

    用途に応じて受付前拒否（`DATA_ERROR`）か run の失敗へ読み替えるのは呼び出し側である。
    """


def identity_path(at: UtcTime) -> ConversionPath:
    """恒等換算の経路（脚0本・率1・ずれ0）。"""
    return ConversionPath(legs=(), rate=_ONE, observed_at=at, skew=timedelta(0))


def resolve_path(
    from_currency: CurrencyCode,
    to_currency: CurrencyCode,
    at: UtcTime,
    policy: ConversionPolicy,
) -> ConversionPath:
    """決済通貨から口座通貨への換算経路を決める（D06 §8.5.1 の規則1）。

    段階2 は (a) の恒等換算だけを通す。直接ペア・基軸通貨経由は、市場系列を読む経路が要る
    ため段階3 で足す（`ConversionUnavailable` を投げて黙って率1に落とさない）。
    """
    if not isinstance(from_currency, CurrencyCode) or not isinstance(to_currency, CurrencyCode):
        raise KernelValueError("resolve_path requires CurrencyCode values")
    if not isinstance(policy, ConversionPolicy):
        raise KernelValueError("resolve_path requires a ConversionPolicy")
    if from_currency == to_currency:
        return identity_path(at)
    raise ConversionUnavailable(
        f"no conversion path from {from_currency} to {to_currency} is available in stage 2;"
        f" direct and {policy.pivot_currency}-pivot paths arrive with cross-currency accounts"
        " (D06 §8.5.1)"
    )


def rate_of(
    path: ConversionPath,
    from_currency: CurrencyCode,
    to_currency: CurrencyCode,
    evidence: EvidenceRef | None = None,
) -> ConversionRate:
    """経路を1本の換算率にまとめる（D06 §8.5.1 の規則7）。"""
    if not isinstance(path, ConversionPath):
        raise KernelValueError("rate_of requires a ConversionPath")
    return ConversionRate(
        from_currency=from_currency,
        to_currency=to_currency,
        rate=path.rate,
        observed_at=path.observed_at,
        evidence=evidence,
    )
