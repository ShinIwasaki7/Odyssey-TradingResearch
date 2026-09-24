"""評価要求の追い越し（`REQUEST_SUPERSEDED`）の検査（D05 §6.10、ADR-0033）。

待機中の評価要求について、「その要求が固定した足より新しい足が、要求の対象系列で公開済みに
なったか」を調べる。**取引機会の終端理由 `SUPERSEDED` とは対象が違う**（機会が終わるのでは
なく、評価要求が閉じる）。

## 対象系列と固定した足（D05 §6.10）

対象系列は、固定した足（`WaitingRequest.pinned_bars`）の系列のうち、**要求の対象区間を
与えた系列**である。足の確定で起動した要求なら、その起動条件の系列である。対象系列の足を
1つも固定していない要求（出力参照だけを読む使用箇所など）は、この規則では追い越されない。

## 後着優先と、押しのけた側の決め方（D05 §6.10 の1〜4）

- 同じ使用箇所・同じ対象系列の待機中の要求は、固定した足の開始時刻の順に並ぶ。
- 新しい足の要求が生まれたとき、**より古い足を固定した待機中の要求すべて**が追い越される
  （1本飛ばしで2件が同時に追い越されることもある）。
- `by_request_id` には押しのけた側、すなわち**いちばん新しい足の要求**を入れる。
- 新しい要求を作るのは追い越しの判定より**後**である。判定を先に行わないと、同じ `step` で
  生まれた要求が自分自身を追い越す。

追い越しがあったときの扱いは宣言（`WaitForInput.on_superseded`）が決める。
`EXPIRE_REQUEST` なら閉じ、`KEEP_WAITING` なら期限まで待ち続けて出来事にも残さない。
ランタイムは役割ごとの既定を持たない（Q17 決定）。
"""

from __future__ import annotations

from collections.abc import Iterable

from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.strategy.declarations.missing import OnSuperseded
from odyssey_fx.strategy.runtime.waiting import WaitingRequest

__all__ = ["newest_published", "pinned_target_bar", "supersedes"]


def pinned_target_bar(waiting: WaitingRequest, target_series: SeriesId | None) -> BarKey | None:
    """待機要求が対象系列について固定した足（D05 §6.10）。

    対象系列の足を固定していなければ `None`（その要求はこの規則では追い越されない）。
    """
    if target_series is None:
        return None
    candidates = [key for key in waiting.pinned_bars.values() if key.series == target_series]
    if not candidates:
        return None
    return max(candidates, key=lambda key: key.bar_start.value)


def newest_published(pinned: BarKey, available_bars: Iterable[BarKey]) -> BarKey | None:
    """公開された足のうち、固定した足と同じ系列でそれより新しい、いちばん新しい足。"""
    newer = [
        key
        for key in available_bars
        if key.series == pinned.series and pinned.bar_start < key.bar_start
    ]
    if not newer:
        return None
    return max(newer, key=lambda key: key.bar_start.value)


def supersedes(
    waiting: WaitingRequest,
    target_series: SeriesId | None,
    available_bars: Iterable[BarKey],
) -> bool:
    """この判断時点の公開で、待機要求が追い越されて閉じるか（D05 §6.10）。

    `on_superseded=KEEP_WAITING` の要求は追い越されても閉じないので偽を返す。
    """
    if waiting.on_superseded is not OnSuperseded.EXPIRE_REQUEST:
        return False
    pinned = pinned_target_bar(waiting, target_series)
    if pinned is None:
        return False
    return newest_published(pinned, available_bars) is not None
