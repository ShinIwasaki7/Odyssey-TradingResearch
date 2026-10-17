"""準備（ウォームアップ）のやり直しの数え方（D03 §15.7、D06 §18.10 の表22）。

根拠の決定は D12-82〜D12-84・D12-90。

戦略の指標は窓だけで値を決める（状態を持たない）ので、準備ができているかは「履歴窓が必要な本数の
作られた足を返せるか」で決まる（D03 §15.7）。MT5 経路では次の規則になる。

- 部分観測の足は 1 本として数える（D12-82）。
- 休場の区間には足が存在しない（予定の足の列に現れない）ので、準備は途切れない（D12-83）。
- 全分無記録の足（予定の足で、作られなかったもの）を窓が含む間は準備不足。その後の足で数え直す
  （D12-84。日中の時間足も同じ。D12-90 (a)）。
- 準備が整う時刻は、必要な本数の最後の足の公開時刻（`available_at`。D03 §15.5・§15.7）。

本モジュールは足の列（予定の足を時刻順に並べ、作られたかどうかと公開時刻を添えたもの）から、
無記録の足ごとの「準備が実際に再び整う足」を決める（全期間の集計の道具（PR-B）が使う。D12 §11.13 の
5・15）。窓の本数は呼び出し側（集計の道具が照合した数え方の規則の `warmup.daily_bars`）
から受け取り、
本モジュールは本数を持たない（D12-88）。run の表22 は、読み取りの窓（`ReadWindowSpec`）ごとに実際の
読み取りの条件から時刻を導く `marketdata.application.read_readiness` が作る（D12-127）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError

__all__ = [
    "HistoryReset",
    "ReadKind",
    "ReadWindowSpec",
    "ScheduledBar",
    "history_resets",
    "ready_index_after",
]


class ReadKind(Enum):
    """戦略の読み取りの窓の種類（D06 §18.10 の表22 の `window_kind`。D12-127）。"""

    BARS = "BARS"  # 本数窓（`history`）
    DURATION = "DURATION"  # 経過時間の窓（`history`）
    LATEST = "LATEST"  # 最新足だけ（`latest_available`）


@dataclass(frozen=True, slots=True)
class ReadWindowSpec:
    """表22 の 1 行が指す読み取りの窓（D12-127: 窓の種類・本数・末尾から除く本数・経過時間）。

    経過時間の窓は本数に換算しない（D12-127「経過時間窓を固定本数へ換算する方法は採りません」）。
    最新足だけの読み取りは本数・経過時間を持たない。
    """

    kind: ReadKind
    bars: int | None = None
    exclude_latest_bars: int = 0
    duration: timedelta | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ReadKind):
            raise MarketDataValueError("ReadWindowSpec.kind must be a ReadKind")
        if isinstance(self.exclude_latest_bars, bool) or self.exclude_latest_bars < 0:
            raise MarketDataValueError("ReadWindowSpec.exclude_latest_bars must be >= 0")
        if self.kind is ReadKind.BARS:
            if self.bars is None or self.bars < 1 or self.duration is not None:
                raise MarketDataValueError("a BARS window needs bars >= 1 and no duration")
        elif self.kind is ReadKind.DURATION:
            if self.duration is None or self.duration <= timedelta(0) or self.bars is not None:
                raise MarketDataValueError("a DURATION window needs a positive duration, no bars")
        elif self.bars is not None or self.duration is not None or self.exclude_latest_bars:
            raise MarketDataValueError("a LATEST read has no bars, duration or excluded bars")

    def sort_key(self) -> tuple[str, int, int, float]:
        """並びの鍵（種類・本数・末尾から除く本数・経過時間）。"""
        return (
            self.kind.value,
            -1 if self.bars is None else self.bars,
            self.exclude_latest_bars,
            -1.0 if self.duration is None else self.duration.total_seconds(),
        )


@dataclass(frozen=True, slots=True)
class ScheduledBar:
    """予定の足 1 本（休場で存在しない足は列に入れない）。

    `built` は作られたか（全分観測・部分観測）。作られない足は全分無記録（`no_record=True`）か
    取得不足（`no_record=False`）。`available_at` は公開時刻（作られない足では予定の公開時刻）。
    """

    bar_start: UtcTime
    available_at: UtcTime
    built: bool
    no_record: bool = False

    def __post_init__(self) -> None:
        if self.built and self.no_record:
            raise MarketDataValueError("a built bar is not a no-record bar")


@dataclass(frozen=True, slots=True)
class HistoryReset:
    """表22 の 1 行（D06 §18.10）: 無記録の足 1 本 × 窓の本数。

    `ready_bar_start` / `ready_at` は準備が**実際に**再び整う足とその公開時刻（後に続く無記録の足も
    考えた結果。データの終わりまで整わなければ `None`）。
    """

    no_record_bar_start: UtcTime
    no_record_available_at: UtcTime
    window_bars: int
    ready_bar_start: UtcTime | None
    ready_at: UtcTime | None


def ready_index_after(bars: Sequence[ScheduledBar], index: int, window_bars: int) -> int | None:
    """`index` の足（作られない足）の後で、準備が整う最初の足の位置。

    位置 `j` で準備が整うのは、`j` を末尾とする `window_bars` 本がすべて作られた足のとき。
    """
    if isinstance(window_bars, bool) or not isinstance(window_bars, int) or window_bars < 1:
        raise MarketDataValueError(f"window_bars must be a positive int, got {window_bars!r}")
    run = 0
    for position in range(index + 1, len(bars)):
        if bars[position].built:
            run += 1
            if run >= window_bars:
                return position
        else:
            run = 0
    return None


def history_resets(bars: Sequence[ScheduledBar], window_bars: int) -> tuple[HistoryReset, ...]:
    """無記録の足ごとの準備のやり直し（D06 §18.10 の表22 の行）。

    取得不足の足は表22 に載せない（取得不足の run は実行前に止まる。D06 §18.9）。
    """
    out: list[HistoryReset] = []
    for index, bar in enumerate(bars):
        if bar.built or not bar.no_record:
            continue
        ready = ready_index_after(bars, index, window_bars)
        out.append(
            HistoryReset(
                no_record_bar_start=bar.bar_start,
                no_record_available_at=bar.available_at,
                window_bars=window_bars,
                ready_bar_start=None if ready is None else bars[ready].bar_start,
                ready_at=None if ready is None else bars[ready].available_at,
            )
        )
    return tuple(out)
