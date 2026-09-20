"""snapshot を読むための共通の関門と読み取り面（D03 §3.7.1・§6.1）。

as-of ビュー（D03 §6）と公開フィード（D03 §7）は、どちらも「承認済みの snapshot の、
許可された partition だけ」を読む。その検査をここ1箇所にまとめ、読み取り経路ごとに
条件が食い違わないようにする。

関門は3つで、順に厳しくなる。

1. **承認済みか**（D03 §3.7.1 の3）。承認の記録が入るまで、暫定 snapshot も最終ディレクトリ
   にある snapshot も読めない（`SnapshotNotApproved`）。
2. **隔離区分を含まないか**（D03 §6.1）。未分類の隔離期間（`QUARANTINED_UNASSIGNED`）は
   再分類まで**いかなる経路でも**許可集合に入らない（`HoldoutAccessViolation`）。
3. **manifest に記録された partition か**。記録にない partition を許可しても、そこに何が
   入っているかを snapshot から確かめられない。

`_PartitionedBars`（`PartitionedBars`）は、許可された partition の足だけを保持する読み取り
面である。許可されていない系列・区間への要求は、黙って空を返すのではなく構造エラーで止める。
封印期間の読み取りが静かな欠損に化けると、「データが無い」のか「読んではいけない」のかが
区別できなくなるためである（D03 §6.1）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import (
    HoldoutAccessViolation,
    MarketDataValueError,
    SnapshotNotApproved,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId, SnapshotManifest

__all__ = [
    "PartitionedBars",
    "require_readable_snapshot",
]


def _require_approved(manifest: SnapshotManifest) -> None:
    """承認済みの snapshot だけを読めるようにする（D03 §3.7.1 の3）。"""
    if not manifest.is_approved:
        raise SnapshotNotApproved(
            "this snapshot has not been approved; as-of views, the publication feed and the"
            " snapshot catalog cannot read it until the approval is recorded (D03 §3.7.1)"
        )


def _reject_quarantined(allowed_partitions: frozenset[PartitionId]) -> None:
    """未分類の隔離期間の partition を許可集合から締め出す（D03 §6.1）。

    未分類の隔離期間（`QUARANTINED_UNASSIGNED`）は、未観測の確認と別の決定記録（ADR）に
    よる再分類が行われるまで**いかなる経路でも**読めない。封印期間の解除手続き
    （holdout gate）も許可を発行しない。呼び出し側が誤って渡した場合に黙って無視すると、
    「渡したのに読めない」のか「そもそも渡してはいけない」のかが区別できなくなるため、
    構築時に構造エラーで拒否する。
    """
    quarantined = sorted(
        str(partition_id)
        for partition_id in allowed_partitions
        if partition_id.access_class is AccessClass.QUARANTINED_UNASSIGNED
    )
    if quarantined:
        raise HoldoutAccessViolation(
            f"quarantined partitions may never be granted to a view: {quarantined};"
            " they stay unreadable until an ADR reclassifies them (D03 §6.1, ADR-0014)"
        )


def require_readable_snapshot(
    manifest: SnapshotManifest,
    allowed_partitions: frozenset[PartitionId],
    *,
    label: str,
) -> None:
    """snapshot と許可 partition が読み取りの条件を満たすことを確かめる。

    `label` は失敗時のメッセージに載せる呼び出し側の名前（`AsOfView` など）。
    """
    if not isinstance(manifest, SnapshotManifest):
        raise MarketDataValueError(f"{label}.manifest must be a SnapshotManifest")
    if not isinstance(allowed_partitions, frozenset):
        raise MarketDataValueError(f"{label}.allowed_partitions must be a frozenset")
    for partition_id in allowed_partitions:
        if not isinstance(partition_id, PartitionId):
            raise MarketDataValueError(f"{label}.allowed_partitions must contain PartitionId")
    _require_approved(manifest)
    _reject_quarantined(allowed_partitions)
    for partition_id in sorted(allowed_partitions, key=str):
        if manifest.partition_record(partition_id) is None:
            raise MarketDataValueError(
                f"partition {partition_id} is not recorded in the snapshot manifest"
            )


class PartitionedBars:
    """許可された partition の足だけを保持する読み取り面（D03 §6.1）。

    partition の集合を構築時に固定し、そこにない系列・区間を読もうとしたら
    `HoldoutAccessViolation` を送出する。「許可されていないものは返さない」ではなく
    「要求そのものを構造エラーにする」ことで、封印期間の読み取りが静かな欠損に化けない
    ようにする。
    """

    __slots__ = ("_by_series",)

    def __init__(
        self,
        partition_bars: Mapping[PartitionId, Sequence[Bar]],
        allowed_partitions: frozenset[PartitionId],
    ) -> None:
        by_series: dict[SeriesId, list[Bar]] = {}
        for partition_id, bars in partition_bars.items():
            if partition_id not in allowed_partitions:
                continue
            by_series.setdefault(partition_id.series, []).extend(bars)
        self._by_series = {
            series: tuple(sorted(bars, key=lambda bar: bar.bar_start.value))
            for series, bars in by_series.items()
        }

    def series(self) -> tuple[SeriesId, ...]:
        """読める系列の一覧（系列文字列の昇順）。"""
        return tuple(sorted(self._by_series, key=str))

    def require_series(self, series: SeriesId) -> tuple[Bar, ...]:
        """許可された partition にその系列があることを確かめて足を返す。"""
        bars = self._by_series.get(series)
        if bars is None:
            raise HoldoutAccessViolation(
                f"series {series} is not inside the allowed partitions of this view;"
                " reading it would cross an access-class boundary (D03 §6.1)"
            )
        return bars

    def bars_or_empty(self, series: SeriesId) -> tuple[Bar, ...]:
        """その系列の足（読めなければ空）。

        公開フィードは「公開予定を持つが足が1本も届いていない系列」も扱う（予定境界は
        データ到着と独立、D03 §7.1）ので、要求を構造エラーにせず空で返す経路が要る。
        許可されていない partition の足は、そもそもこの読み取り面に入っていない。
        """
        return self._by_series.get(series, ())

    def starts_before_data(self, series: SeriesId, moment: UtcTime) -> bool:
        """要求した時刻が、読める足の最初より前かを判定する。

        「データ開始前」は助走不足（`WARMUP_INSUFFICIENT`）であり、アクセス分類の境界を
        越える要求とは別物である（上位設計書 §4.3.10 の4分類）。両者を混同すると、単に
        データが始まっていないだけの窓が構造エラーになってしまう。
        """
        bars = self.require_series(series)
        return bool(bars) and moment < bars[0].bar_start

    def require_covered(self, series: SeriesId, moment: UtcTime) -> None:
        """要求した時刻が許可された partition の範囲内であることを確かめる。

        範囲の外は、封印期間や未分類の隔離期間の partition が渡されなかったことを意味する。
        入力欠損に読み替えず、構造エラーで止める（D03 §6.1）。

        読める範囲は**半開区間** `[first.bar_start, last.bar_end)` である。上端を含めて
        しまうと、読める最後の足の直後に始まる足（＝隣の partition の最初の足）が範囲内と
        見なされ、封印区分の足が期待足になったときに構造エラーではなく「最新足が未到着」
        という入力欠損が返ってしまう。
        """
        bars = self.require_series(series)
        if not bars:  # pragma: no cover - 空の partition は記録されない
            raise HoldoutAccessViolation(f"no readable bars for {series} (D03 §6.1)")
        first, last = bars[0], bars[-1]
        if moment < first.bar_start or last.bar_end <= moment:
            raise HoldoutAccessViolation(
                f"{moment} lies outside the readable range of {series}"
                f" [{first.bar_start}, {last.bar_end}); the surrounding partition was not"
                " granted to this view (D03 §6.1)"
            )
