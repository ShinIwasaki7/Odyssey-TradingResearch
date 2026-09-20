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
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from odyssey_fx.common.ids import SnapshotId
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.partition_digest import partition_digest_hex
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.errors import (
    HoldoutAccessViolation,
    MarketDataValueError,
    PartitionContentMismatch,
    SnapshotNotApproved,
)
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    ClosureDecision,
    PartitionId,
    SnapshotManifest,
)

__all__ = [
    "PENDING_DIRECTORY",
    "PartitionedBars",
    "ReadableSnapshot",
    "classification_mismatch",
    "freeze_partition_bars",
    "require_readable_snapshot",
]

#: 暫定 snapshot を置くディレクトリ名（D03 §3.7.1 の 1）。この配下の snapshot は承認の
#: 対象にならず、常に読めない。
PENDING_DIRECTORY = "_pending"


def classification_mismatch(
    report: IntegrityReport, decisions: Sequence[ClosureDecision]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """警告と分類の食い違いを返す（D03 §4 の 9）。

    `(未分類の警告, 対応する警告のない分類)` の組。それぞれ `系列 区間` の形の文字列で、
    人間が読める順に並ぶ。対応は**区間全体**で取る。開始時刻だけで突き合わせると、終端の
    違う分類（別の足を指す分類）が対応済みとして通ってしまう。

    確定（`finalize`）と読み取りの関門（`ReadableSnapshot`）が同じ規則を使うための共通の
    純粋関数である。片方だけが検査していると、確定を経ずに組み立てた manifest が読み取り
    側をすり抜ける。
    """
    warned = {f"{result.series} {result.interval}" for result in report.warnings}
    decided = {f"{decision.series_id} {decision.interval}" for decision in decisions}
    return tuple(sorted(warned - decided)), tuple(sorted(decided - warned))


@dataclass(frozen=True, slots=True)
class ReadableSnapshot:
    """読み取りが許された snapshot（D03 §3.7.1 の3）。

    D03 §3.7.1 は「承認前は読めない」だけでなく、**暫定段階（`_pending/`）の snapshot は
    承認の対象にならず常に読めない**、確定した snapshot は**最終 `snapshot_id` ディレクトリ
    にある**と定める。承認の有無だけを見ていると、暫定 manifest に承認を付けたものや、
    別の識別子のディレクトリに置いた manifest を読めてしまう。

    構築時に3点を確かめる。

    1. `directory_name` が暫定ディレクトリ（`_pending`）配下でない。
    2. `directory_name` が manifest から再計算した `snapshot_id` と一致する（＝確定段階を
       経ており、内容とディレクトリ名が食い違っていない）。
    3. 承認が記入されている。
    4. 完全性検査の報告の**すべての警告が分類されている**（D03 §4 の 9）。

    4点目を manifest だけでは確かめられないので、報告（`report`）も併せて受け取る。
    ディレクトリ名と承認だけを見ていると、確定段階（`finalize`）を経ずに組み立てた
    manifest——未分類の警告が残ったまま承認を付けたもの——が、最終識別子の名前で置くだけで
    読めてしまう。報告そのものが manifest の記録どおりであること（ダイジェストの一致）は、
    報告を読む側（`ParquetSnapshotStore.open_readable`）が確かめる。

    読み取り経路（as-of ビュー・執行系列ビュー・公開フィード）は `SnapshotManifest` では
    なくこの型を受け取るので、検査を通っていない manifest は構造的に渡せない。
    """

    manifest: SnapshotManifest
    directory_name: str
    report: IntegrityReport

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SnapshotManifest):
            raise MarketDataValueError("ReadableSnapshot.manifest must be a SnapshotManifest")
        if not isinstance(self.directory_name, str) or not self.directory_name:
            raise MarketDataValueError("ReadableSnapshot.directory_name must be a non-empty str")
        if not isinstance(self.report, IntegrityReport):
            raise MarketDataValueError("ReadableSnapshot.report must be an IntegrityReport")

        parts = PurePosixPath(self.directory_name).parts
        if PENDING_DIRECTORY in parts:
            raise SnapshotNotApproved(
                f"{self.directory_name!r} is a provisional snapshot;"
                " provisional snapshots are never approved and never readable"
                " (D03 §3.7.1 の 1・3)"
            )

        expected = str(self.manifest.snapshot_id())
        if parts[-1] != expected:
            raise MarketDataValueError(
                f"snapshot directory {self.directory_name!r} does not match the manifest's"
                f" snapshot id {expected}; a readable snapshot lives in the directory named"
                " by its final id (D03 §3.7.1 の 2)"
            )
        _require_approved(self.manifest)

        # 確定段階を経ていれば、報告の警告はすべて分類されている（D03 §4 の 9）。
        # 経ていない manifest はここで止まる。
        undecided, extraneous = classification_mismatch(
            self.report, self.manifest.closure_decisions
        )
        if undecided:
            raise SnapshotNotApproved(
                f"{len(undecided)} warning(s) in this snapshot's integrity report are still"
                f" unclassified: {list(undecided)}; it has not been through the classification"
                " stage and cannot be read (D03 §3.7.1 の 2、§4 の 9)"
            )
        if extraneous:
            raise MarketDataValueError(
                f"{len(extraneous)} closure decision(s) do not correspond to any warning in"
                f" this snapshot's integrity report: {list(extraneous)} (D03 §4 の 9)"
            )

    @property
    def snapshot_id(self) -> SnapshotId:
        """この snapshot の最終識別子。"""
        return self.manifest.snapshot_id()


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


def _require_matching_content(
    manifest: SnapshotManifest,
    partition_id: PartitionId,
    bars: Sequence[Bar],
) -> None:
    """渡された足が manifest の記録どおりであることを確かめる（D03 §3.7.1）。

    partition の**鍵**が合っているだけでは、その足が本当にその snapshot のものかは分から
    ない。暫定 snapshot や別 snapshot の足を同じ鍵で渡せば、承認済み manifest の内容として
    読めてしまう。manifest は partition ごとに足数・区間・内容ダイジェストを記録している
    ので、4点すべてを照合する。

    1. 各足の系列が partition の系列と一致する。
    2. 足数が記録と一致する。
    3. 区間（最初の足の開始と最後の足の終了）が記録と一致する。
    4. 内容ダイジェストが記録と一致する。
    """
    record = manifest.partition_record(partition_id)
    if record is None:  # pragma: no cover - 呼び出し前に検査済み
        raise MarketDataValueError(
            f"partition {partition_id} is not recorded in the snapshot manifest"
        )

    for bar in bars:
        if bar.series != partition_id.series:
            raise PartitionContentMismatch(
                f"partition {partition_id} was given a bar of {bar.series};"
                " the supplied bars do not belong to this partition (D03 §3.7.1)"
            )
    if len(bars) != record.bar_count:
        raise PartitionContentMismatch(
            f"partition {partition_id} holds {len(bars)} bar(s) but the manifest records"
            f" {record.bar_count}; the supplied bars are not this snapshot's content"
            " (D03 §3.7.1)"
        )
    if not bars:
        return

    ordered = sorted(bars, key=lambda bar: bar.bar_start.value)
    covered = Interval(start=ordered[0].bar_start, end=ordered[-1].bar_end)
    if covered != record.interval:
        raise PartitionContentMismatch(
            f"partition {partition_id} covers {covered} but the manifest records"
            f" {record.interval} (D03 §3.7.1)"
        )
    digest_hex = partition_digest_hex(ordered)
    if digest_hex != record.digest.hex:
        raise PartitionContentMismatch(
            f"partition {partition_id} does not match the digest recorded in the manifest;"
            " the supplied bars are not this snapshot's content (D03 §3.7.1)"
        )


def require_readable_snapshot(
    snapshot: ReadableSnapshot,
    allowed_partitions: frozenset[PartitionId],
    *,
    label: str,
    partition_bars: Mapping[PartitionId, Sequence[Bar]],
) -> Mapping[PartitionId, tuple[Bar, ...]]:
    """許可 partition と足が読み取りの条件を満たすことを確かめ、足の写しを返す。

    `snapshot` は既に承認・ディレクトリ名・識別子の検査を通った型なので、ここでは許可
    partition と足の内容だけを見る。

    返すのは**後から変更できない写し**である。呼び出し元の可変な列をそのまま保持すると、
    構築時の照合をすり抜けた後で中身を差し替えられる（D03 §6.1）。呼び出し側はこの返り値
    だけを読むこと。

    `label` は失敗時のメッセージに載せる呼び出し側の名前（`AsOfView` など）。
    """
    if not isinstance(snapshot, ReadableSnapshot):
        raise MarketDataValueError(f"{label}.snapshot must be a ReadableSnapshot")
    if not isinstance(allowed_partitions, frozenset):
        raise MarketDataValueError(f"{label}.allowed_partitions must be a frozenset")
    for partition_id in allowed_partitions:
        if not isinstance(partition_id, PartitionId):
            raise MarketDataValueError(f"{label}.allowed_partitions must contain PartitionId")

    # 先に写し取る。以降の照合も読み取りも、この写しだけを見る。
    frozen = freeze_partition_bars(partition_bars)

    manifest = snapshot.manifest
    _reject_quarantined(allowed_partitions)
    for partition_id in sorted(allowed_partitions, key=str):
        if manifest.partition_record(partition_id) is None:
            raise MarketDataValueError(
                f"partition {partition_id} is not recorded in the snapshot manifest"
            )
        _require_matching_content(manifest, partition_id, frozen.get(partition_id, ()))
    return frozen


def freeze_partition_bars(
    partition_bars: Mapping[PartitionId, Sequence[Bar]],
) -> Mapping[PartitionId, tuple[Bar, ...]]:
    """渡された足を、後から変更できない形へ写し取る（D03 §6.1）。

    呼び出し元の可変な `list` / `dict` をそのまま保持すると、**構築時の照合をすり抜けた
    後で中身を差し替えられる**。実際に、研究期間の足だけを記録した manifest でビューを
    作ったあと同じ `list` に封印期間の足を追記すると、その足が読めてしまう。照合は構築時の
    一度きりなので、読むのは必ずその時点で固定した写しでなければならない。

    `MappingProxyType` と `tuple` の組で返すので、返り値自体も書き換えられない。
    """
    frozen = {
        partition_id: tuple(sorted(bars, key=lambda bar: bar.bar_start.value))
        for partition_id, bars in partition_bars.items()
    }
    return MappingProxyType(frozen)


class PartitionedBars:
    """許可された partition の足だけを保持する読み取り面（D03 §6.1）。

    partition の集合と**足そのもの**を構築時に固定し、そこにない系列・区間を読もうとしたら
    `HoldoutAccessViolation` を送出する。「許可されていないものは返さない」ではなく
    「要求そのものを構造エラーにする」ことで、封印期間の読み取りが静かな欠損に化けない
    ようにする。

    足は `tuple` へ写し取るので、呼び出し元が元の列を後から変更しても、この読み取り面が
    返す内容は変わらない。
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
