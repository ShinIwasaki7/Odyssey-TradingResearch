"""refill_report（補充の後の残存欠落と連続期間の報告。D03 v1.17 §14.15）のテスト。

補充の置き場の中身は手書きしない。本体の ``build_plan`` → ``create_plan`` → ``fetch_plan``
（録画した bi5 を返す偽の取得元。通信しない）→ ``finalize_plan`` を実際に走らせて、正式な計画・
取得記録・補充分を一時ディレクトリに作る（決定論なので毎回同じ）。報告が読む snapshot の
manifest は本体の ``ParquetSnapshotStore`` で書く（新 snapshot の ``sources`` は書き出した
補充分の記録から作る）。実データには触れない。

場面（USDJPY 2020-11-30。原データの 01 時と 02 時を落とした）:

- 計画 A: 01 時・02 時の対象足。01 時は録画の bi5、02 時は 404 → 01 時だけ補充（部分補充）。
- 計画 B（併記の場面だけ）: 同じ入力 snapshot・同じ対象足の、提供元の設定だけが違う計画。
  照合の 00 時の原データを変えて検証で不合格にする。A とは参照関係で比べられない。
- 新 snapshot: A の補充分を含み、02 時（取得できなかった）・04 時（計画の絞り込みの外）・
  2017 年元日の候補区間を短くした欠落が残る。
"""

from __future__ import annotations

import csv
import json
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.adapters.refill_store import FsRefillStore
from odyssey_fx.marketdata.application.refill_fetch import fetch_plan
from odyssey_fx.marketdata.application.refill_finalize import finalize_plan
from odyssey_fx.marketdata.application.refill_inventory import Rejection, VerifiedRefill
from odyssey_fx.marketdata.application.refill_plan import build_plan, create_plan
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES, AccessClass
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.classification import (
    ClassificationOutcome,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.refill import RefillFilter, RefillPlan
from odyssey_fx.marketdata.domain.refill_manifest import RefillManifest
from odyssey_fx.marketdata.domain.refill_validation import UnreconciledCause, UnreconciledChunk
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    PartitionId,
    PartitionRecord,
    SeriesManifest,
    SnapshotManifest,
    SourceFile,
)
from odyssey_fx.marketdata.domain.time_label_correction import (
    TimeLabelCorrectionRecord,
    TimeLabelCorrectionRule,
)
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
    REFILL_CALENDAR,
    USDJPY_1H,
    USDJPY_15M,
    FakeTickSource,
    calendar_ref,
    communication,
    gap_resolutions,
    provider_ref,
    raw_bars,
    url_of,
)
from tests.fixtures.synthetic import market
from tests.fixtures.synthetic import snapshots as synthetic
from tools.ops import refill_report as rr
from tools.ops import research_history_gaps as rhg

HOUR_02 = UtcTime.parse("2020-11-30T02:00:00Z")
HOUR_03 = UtcTime.parse("2020-11-30T03:00:00Z")
HOUR_04 = UtcTime.parse("2020-11-30T04:00:00Z")
_URLS_00_02 = tuple(url_of(hour) for hour in (HOUR_00, HOUR_01, HOUR_02))
NOW = UtcTime.parse("2026-10-01T12:00:00Z")
WINDOW = Interval(
    start=UtcTime.parse("2016-01-01T00:00:00Z"), end=UtcTime.parse("2021-01-01T00:00:00Z")
)
APPROVAL = Approval(approved_by="human", approved_at=UtcTime.parse("2026-10-02T00:00:00Z"))
#: 計画の絞り込み: USDJPY の 2020-11-30 00〜03 時（04 時の欠落と 2017 年の欠落は入らない）。
FILTER = RefillFilter(
    symbols=(market.USDJPY,),
    interval=Interval(
        start=UtcTime.parse("2020-11-30T00:00:00Z"), end=UtcTime.parse("2020-11-30T03:00:00Z")
    ),
)
FILTER_04 = RefillFilter(
    symbols=(market.USDJPY,),
    interval=Interval(
        start=UtcTime.parse("2020-11-30T04:00:00Z"), end=HOUR_04 + timedelta(hours=1)
    ),
)
SOURCES = (
    synthetic.source("data/raw/market/USDJPY_15m_merged.csv", "USDJPY", "15m"),
    synthetic.source("data/raw/market/USDJPY_1h_merged.csv", "USDJPY", "1h"),
)

# 残存欠落の行の鍵 (銘柄, 時間足, 始端)。
ROW_02_15M = ("USDJPY", "15m@v1", "2020-11-30T02:00Z")
ROW_02_1H = ("USDJPY", "1h@v1", "2020-11-30T02:00Z")
ROW_04_15M = ("USDJPY", "15m@v1", "2020-11-30T04:00Z")
ROW_2017 = ("USDJPY", "1h@v1", "2017-01-02T03:00Z")


def _sid(symbol: str, tf: str) -> SeriesId:
    return SeriesId(symbol=Symbol(symbol), timeframe=TimeframeRef.parse(tf), basis=PriceBasis.BID)


def _gap(series: SeriesId, start: str, end: str) -> ResolvedClassification:
    return ResolvedClassification(
        kind=CheckKind.MISSING_EXPECTED_BAR,
        series_id=series,
        interval=Interval(start=UtcTime.parse(start), end=UtcTime.parse(end)),
        outcome=ClassificationOutcome.DATA_GAP,
    )


def _hours_2017(skip: int) -> list[ResolvedClassification]:
    """2017 年元日の欠落（候補 2 の区間、9 時間）の 1 時間足。先頭の ``skip`` 時間を除く。"""
    start = UtcTime.parse("2017-01-01T22:00:00Z")
    found = []
    for index in range(9):
        moment = start + timedelta(hours=index)
        if index < skip:
            continue
        found.append(_gap(USDJPY_1H, str(moment), str(moment + timedelta(hours=1))))
    return found


def _snapshot(
    resolved: Sequence[ResolvedClassification],
    sources: Sequence[SourceFile] = SOURCES,
    *,
    approved: bool = True,
) -> SnapshotManifest:
    """20 系列の研究履歴の partition と分類を持つ manifest（識別子は内容から決まる）。"""
    partitions = [
        PartitionRecord(
            partition_id=PartitionId(series=_sid(s, tf), access_class=AccessClass.RESEARCH_HISTORY),
            interval=WINDOW,
            bar_count=1,
            digest=synthetic.digest_for(f"{s}{tf}"),
        )
        for s in rhg.SYMBOLS
        for tf in rhg.TIMEFRAMES
    ]
    return synthetic.manifest(
        sources=sources,
        series_records=[
            SeriesManifest(
                series_id=record.partition_id.series,
                covered_interval=WINDOW,
                bar_count=1,
                partitions=(record.partition_id,),
            )
            for record in partitions
        ],
        partitions=partitions,
        resolved_classifications=resolved,
        approval=APPROVAL if approved else None,
    )


#: 旧 snapshot（計画の入力）: 01・02 時、04 時の 15分足、2017 年元日の 9 時間。
OLD_MANIFEST = _snapshot(
    [
        *gap_resolutions(hours=(HOUR_01, HOUR_02)),
        _gap(USDJPY_15M, "2020-11-30T04:00:00Z", "2020-11-30T04:15:00Z"),
        *_hours_2017(0),
    ]
)
OLD = str(OLD_MANIFEST.snapshot_id())


def _other_snapshot() -> SnapshotManifest:
    """新 snapshot の祖先でない snapshot（04 時の 15分足の欠落だけを持つ）。"""
    return _snapshot([_gap(USDJPY_15M, "2020-11-30T04:00:00Z", "2020-11-30T04:15:00Z")])


class _Inputs:
    """書き出しが計画から読むもの（原データの代わりとカレンダー）。"""

    def __init__(self, bars: Mapping[SeriesId, Sequence[Bar]]) -> None:
        self._bars = bars

    def raw_bars(
        self, plan: RefillPlan
    ) -> tuple[tuple[SeriesId, ...], Mapping[SeriesId, Sequence[Bar]]]:
        del plan
        return (USDJPY_15M, USDJPY_1H), self._bars

    def calendar(self, plan: RefillPlan) -> TradingCalendar:
        del plan
        return REFILL_CALENDAR


def _raw() -> dict[SeriesId, tuple[Bar, ...]]:
    return raw_bars(drop_hours=(HOUR_01, HOUR_02))


def _raw_with_changed_reference(
    kind: ProvenanceKind = ProvenanceKind.DUKASCOPY,
) -> dict[SeriesId, tuple[Bar, ...]]:
    """照合の 00 時の 15分足 1 本の終値を変えた原データ。

    出所が提供元と同じ配信元（`dukascopy`）なら提供元の訂正の疑いで検証が不合格になり、`histdata`
    なら配信元の値の差でその塊を作らない（D03 §14.7 の v1.19）。
    """
    bars = _raw()
    changed = []
    for bar in bars[USDJPY_15M]:
        if bar.bar_start == HOUR_00:
            bar = replace(
                bar,
                close=Price(decimal_from_str("104.050")),
                provenance=Provenance(kind=kind, source_ref=bar.provenance.source_ref),
            )
        changed.append(bar)
    return {**bars, USDJPY_15M: tuple(changed)}


def _plan(
    comm_interval: int,
    manifest: SnapshotManifest = OLD_MANIFEST,
    raw: Mapping[SeriesId, Sequence[Bar]] | None = None,
    refill_filter: RefillFilter = FILTER,
) -> RefillPlan:
    return build_plan(
        manifest=manifest,
        raw_bars=_raw() if raw is None else raw,
        calendar=REFILL_CALENDAR,
        calendar_ref=calendar_ref(),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        provider=provider_ref(comm=communication(request_interval_seconds=comm_interval)),
        refill_filter=refill_filter,
    )


def _run(
    store: FsRefillStore,
    plan: RefillPlan,
    responses: Mapping[str, list[bytes | int]],
    inputs: _Inputs,
) -> tuple[str, str | None]:
    plan_id = create_plan(plan, store)
    fetch_plan(plan_id, store=store, source=FakeTickSource(responses), retry_failed=False)
    report = finalize_plan(
        plan_id,
        store=store,
        inputs=inputs,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        code_version="code-v1",
        clock=lambda: NOW,
    )
    return plan_id, report.refill_id


@dataclass(frozen=True)
class Scene:
    """本体で作った置き場と snapshot、報告の引数。"""

    root: Path
    plan_a: str
    refill_a: str
    plan_b: str | None
    new: str
    args: list[str]
    plan_c: str | None = None

    @property
    def refill_root(self) -> Path:
        return self.root / "refill"

    @property
    def out(self) -> Path:
        return self.root / "out"

    def rows(self, name: str = "residual_gaps.csv") -> dict[tuple[str, str, str], dict[str, str]]:
        with (self.out / name).open(encoding="utf-8") as handle:
            return {
                (r["symbol"], r["timeframe"], r["start_utc"]): r for r in csv.DictReader(handle)
            }

    def report(self, name: str = "refill_report.md") -> str:
        return (self.out / name).read_text(encoding="utf-8")


def _scene(
    root: Path,
    *,
    with_b: bool = False,
    with_later: bool = False,
    with_open: bool = False,
    with_other: bool = False,
    approved: bool = True,
    b_kind: ProvenanceKind = ProvenanceKind.DUKASCOPY,
    new_rule: TimeLabelCorrectionRule | None = None,
) -> Scene:
    """計画 A（と B）を本体で計画・取得・書き出しし、新 snapshot を書く。"""
    store = FsRefillStore(root=root / "refill")
    plan_a, refill_a = _run(
        store,
        _plan(8),
        {url_of(HOUR_00): [BI5_00H], url_of(HOUR_01): [BI5_01H], url_of(HOUR_02): [404]},
        _Inputs(_raw()),
    )
    assert refill_a is not None
    plan_b = None
    if with_b:
        # 02 時にも tick を返す（B の不合格の記録では 02 時の足も「検証で不合格」になる）。
        plan_b, refill_b = _run(
            store,
            _plan(9),
            {url_of(HOUR_00): [BI5_00H], url_of(HOUR_01): [BI5_01H], url_of(HOUR_02): [BI5_01H]},
            _Inputs(_raw_with_changed_reference(b_kind)),
        )
        assert refill_b is None
    manifest = RefillManifest.from_payload(
        json.loads((root / "refill" / refill_a / "refill_manifest.json").read_text())
    )
    refill_sources = [
        SourceFile(
            path=f"data/raw/market/refill/{refill_a}/{item.name}",
            sha256=item.sha256,
            rows=int(item.rows or 0),
            symbol=item.series.symbol,
            timeframe=item.series.timeframe,
            declared_basis=PriceBasis.BID,
        )
        for item in manifest.bar_files
        if item.series is not None
    ]
    new_manifest = _snapshot(
        [
            *gap_resolutions(hours=(HOUR_02,)),
            _gap(USDJPY_15M, "2020-11-30T04:00:00Z", "2020-11-30T04:15:00Z"),
            *_hours_2017(5),
        ],
        [*SOURCES, *refill_sources],
        approved=approved,
    )
    if new_rule is not None:
        new_manifest = replace(
            new_manifest,
            conversion=replace(
                new_manifest.conversion,
                time_label_correction=TimeLabelCorrectionRecord(
                    rule=new_rule, shifted_bar_counts=()
                ),
            ),
        )
    new = str(new_manifest.snapshot_id())
    snapshots = ParquetSnapshotStore(root=root / "snapshots")
    snapshots.write_manifest(OLD, OLD_MANIFEST)
    snapshots.write_manifest(new, new_manifest)
    args = [
        "--snapshot-root",
        str(root / "snapshots"),
        "--snapshot-id",
        new,
        "--previous-snapshot-id",
        OLD,
        "--candidates-snapshot-id",
        OLD,
        "--refill-root",
        str(root / "refill"),
        "--out",
        str(root / "out"),
    ]
    if with_open:
        # 計画 D: 計画だけ（取得も書き出しもまだ）。計画 E: 書き出したが、その補充分は新
        # snapshot に入れない（02 時の足も作る）。
        create_plan(_plan(11), store)
        _, refill_e = _run(
            store,
            _plan(12),
            {url: [BI5_00H if url == url_of(HOUR_00) else BI5_01H] for url in _URLS_00_02},
            _Inputs(_raw()),
        )
        assert refill_e is not None
    plan_c = None
    if with_other:
        # 計画 X: 新 snapshot の祖先でない snapshot（04 時の 15分足の欠落だけを持つ）を入力に
        # した、04 時の足の計画。照合の時間の原データは人工の値（histdata）なので、配信元の値の差で
        # 塊を作らず不合格になる。
        other = _other_snapshot()
        snapshots.write_manifest(str(other.snapshot_id()), other)
        hours = (HOUR_03, HOUR_04, HOUR_04 + timedelta(hours=1))
        plan_c, refill_x = _run(
            store,
            _plan(13, other, _raw(), FILTER_04),
            {url_of(hour): [BI5_01H] for hour in hours},
            _Inputs(_raw()),
        )
        assert refill_x is None
    if with_later:
        # 計画 C: A の補充分を含む新 snapshot を入力にした、02 時の足の計画（補充を重ねる）。
        # 照合の時間の原データは人工の値（histdata）なので、配信元の値の差で塊を作らず不合格になる。
        later_raw = raw_bars(drop_hours=(HOUR_02,))
        plan_c, refill_c = _run(
            store,
            _plan(10, new_manifest, later_raw),
            {url: [BI5_01H] for url in (url_of(HOUR_01), url_of(HOUR_02), url_of(HOUR_03))},
            _Inputs(later_raw),
        )
        assert refill_c is None
    return Scene(root, plan_a, refill_a, plan_b, new, args, plan_c)


# --- 状態・履歴・根拠（D03 §14.15 の R2）--------------------------------------------------


def test_residual_gaps_carry_their_states_from_the_formal_records(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    assert rr.main(scene.args) == 0
    rows = scene.rows()
    assert {key: row["states"] for key, row in rows.items()} == {
        ROW_02_15M: rr.NOT_FETCHED,
        ROW_02_1H: rr.NOT_FETCHED,
        ROW_04_15M: rr.NOT_PLANNED,
        ROW_2017: rr.NOT_PLANNED,
    }
    row = rows[ROW_02_15M]
    assert row["basis_plan_ids"] == scene.plan_a
    assert row["bars_with_unordered_records"] == "0"
    assert f"plan={scene.plan_a} refill:{scene.refill_a} {rr.NOT_FETCHED}×4" in row["history"]
    assert rows[ROW_04_15M]["basis_plan_ids"] == ""


def test_records_that_cannot_be_ordered_are_shown_side_by_side(tmp_path: Path) -> None:
    """(i) 同じ足を比較できない 2 つの計画が記録したら、1 つを選ばずに併記する。"""
    scene = _scene(tmp_path, with_b=True)
    assert rr.main(scene.args) == 0
    row = scene.rows()[ROW_02_15M]
    assert row["states"] == f"{rr.NOT_FETCHED}|{rr.VALIDATION_REJECTED}"
    assert row["basis_plan_ids"] == "|".join(sorted([scene.plan_a, str(scene.plan_b)]))
    assert row["bars_with_unordered_records"] == "4"
    # 履歴は上書きしない: 両方の記録が全件残る。
    assert f"plan={scene.plan_a} refill:{scene.refill_a} {rr.NOT_FETCHED}×4" in row["history"]
    assert f"plan={scene.plan_b} journal:" in row["history"]
    report = scene.report()
    assert f"| 不合格のままの計画（自動収集） | `{scene.plan_b}` |" in report
    assert "計画 2 件・補充分 1 件" in report


def test_a_source_difference_is_its_own_state_with_its_reason(tmp_path: Path) -> None:
    """v1.19: 配信元の値の差（照合用の足の原データが histdata で、時刻ズレでも表現誤差でもない差）の
    塊は補充せず、状態「配信元の値の差」と、理由（不一致の足の数・差の最大）を示す。"""
    scene = _scene(tmp_path, with_b=True, b_kind=ProvenanceKind.HISTDATA)
    assert rr.main(scene.args) == 0
    row = scene.rows()[ROW_02_15M]
    assert row["states"] == f"{rr.NOT_FETCHED}|{rr.SOURCE_DIFFERENCE}"
    assert row[f"bars_{rr.SOURCE_DIFFERENCE}"] == "4"
    report = scene.report()
    assert "配信元の値の差の塊" in report
    assert "（対象足 8 本。理由: 不一致の足 1 本、差の最大 0.009（0.9 pip））" in report
    assert "配信元の値の差（`SOURCE_DIFFERENCE`）" in report


# --- 補正の前の入力の記録（D03 §14.15 の v1.19、人間の決定 DST-4〜DST-6）---------------------

#: 2020-11-30 の週（補正の前のラベル）を対象にした人工の補正規則。計画 A の対象足（01・02 時）を
#: 含む。読み替えだけを確かめるので、週が暦から導けることは問わない。
_RULE_2020 = TimeLabelCorrectionRule(
    rule_id="histdata_us_only_dst_weeks",
    rule_version=1,
    shift=timedelta(hours=1),
    series=("USDJPY/15m/bid", "USDJPY/1h/bid"),
    weeks=(
        Interval(
            start=UtcTime.parse("2020-11-29T21:00:00Z"),
            end=UtcTime.parse("2020-12-04T21:00:00Z"),
        ),
    ),
    daylight_zone="America/New_York",
    standard_zone="Europe/London",
)


def test_records_of_a_plan_on_an_uncorrected_snapshot_are_relabelled_and_marked(
    tmp_path: Path,
) -> None:
    """DST-4: 補正なしの snapshot を入力にした計画 A の足の鍵を +1 時間に読み替え、印を付ける。"""
    scene = _scene(tmp_path, new_rule=_RULE_2020)
    root = tmp_path / "snapshots"
    store = FsRefillStore(root=scene.refill_root)
    chain = rr.snapshot_chain(root, store, scene.new)
    collection = rr.collect_records(root, store, chain, frozenset(), _RULE_2020)
    assert collection.complete, collection.problems
    (record,) = collection.records
    assert record.pre_correction
    starts = sorted({key[2] for key in record.results})
    # 補正の前のラベル 01:00〜02:45 → 02:00〜03:45。
    assert str(UtcTime(starts[0])) == "2020-11-30T02:00:00Z"
    assert str(UtcTime(starts[-1])) == "2020-11-30T03:45:00Z"
    # 新 snapshot と同じ補正規則の入力（または両方なし）は読み替えない。
    same = rr.collect_records(root, store, chain, frozenset(), None)
    assert not same.records[0].pre_correction
    assert sorted({key[2] for key in same.records[0].results})[0] == HOUR_01.value


def test_the_history_marks_a_relabelled_record(tmp_path: Path) -> None:
    scene = _scene(tmp_path, new_rule=_RULE_2020)
    store = FsRefillStore(root=scene.refill_root)
    root = tmp_path / "snapshots"
    collection = rr.collect_records(
        root, store, rr.snapshot_chain(root, store, scene.new), frozenset(), _RULE_2020
    )
    keys = [("USDJPY", "15m@v1", (HOUR_02 + timedelta(hours=1)).value)]
    (entry,) = rr._history(keys, collection)
    assert "（補正の前の入力）" in entry[2]


#: 対象の週の終端が 02:00 の人工の規則。計画 A の 01 時の足を +1 時間に読み替えると、読み替えない
#: 02 時の足（同じ計画の対象足）の鍵と重なる（D03 §14.15 の v1.20 の決定 1・B）。
_RULE_ENDING_02 = replace(
    _RULE_2020,
    weeks=(Interval(start=UtcTime.parse("2020-11-29T21:00:00Z"), end=HOUR_02),),
)


def test_an_overlap_of_relabelled_keys_is_reported_and_keeps_both_records(
    tmp_path: Path,
) -> None:
    """決定 1・B: 読み替えた鍵が読み替えない鍵と重なれば、網羅性を確かめられない理由に載せ、
    その足の状態は「理由未確定」1 つとし、重なった 2 つの記録をどちらも履歴に残す。"""
    scene = _scene(tmp_path, new_rule=_RULE_ENDING_02)
    root = tmp_path / "snapshots"
    store = FsRefillStore(root=scene.refill_root)
    chain = rr.snapshot_chain(root, store, scene.new)
    collection = rr.collect_records(root, store, chain, frozenset(), _RULE_ENDING_02)
    (record,) = collection.records
    overlapped = ("USDJPY", "15m@v1", HOUR_02.value)
    assert record.results[overlapped] == rr.RELABEL_OVERLAP
    # 15分足 4 本と 1時間足 1 本が重なる。
    assert len(record.overlaps) == 5
    assert [origin for origin, _, _ in record.overlaps[overlapped]] == [
        HOUR_01.value,
        HOUR_02.value,
    ]
    assert not collection.complete
    [problem] = [item for item in collection.problems if "15m@v1 2020-11-30T02:00Z" in item]
    assert "鍵が重なった" in problem and "元の鍵 2020-11-30T01:00Z・2020-11-30T02:00Z" in problem
    assert sum("鍵が重なった" in item for item in collection.problems) == 5
    # 状態は「理由未確定」1 つ（2 つの結果のどちらも選ばない）。
    states = rr.bar_states([overlapped], collection, chain)
    assert states[overlapped].states == (rr.UNDETERMINED,)
    # 履歴には元の 2 つの記録がどちらも残る（01 時は補充した、02 時は取得できなかった）。
    entries = rr._history([overlapped], collection)
    assert len(entries) == 2
    assert {entry[3] for entry in entries} == {rr.BUILT_NOT_IN_SNAPSHOT, rr.NOT_FETCHED}
    assert all("元の鍵" in entry[2] for entry in entries)


def test_the_cause_of_an_unreconciled_chunk_is_shown_for_its_bars() -> None:
    """v1.20: 未照合の足の履歴に塊の原因を示す（照合用の足を作れない＋配信元の値の差。決定 A）。"""
    plan = _plan(9)
    chunk = UnreconciledChunk(
        series=USDJPY_15M,
        chunk_start=HOUR_01,
        target_count=8,
        causes=(UnreconciledCause.RECONCILIATION_NOT_BUILT, UnreconciledCause.SOURCE_DIFFERENCE),
        unbuilt_reconciliation_bars=((USDJPY_15M, UtcTime.parse("2020-11-30T00:15:00Z")),),
    )
    notes = rr._unreconciled_notes(plan, (chunk,))
    assert len(notes) == 8
    assert {series for series, _ in notes} == {USDJPY_15M}
    assert set(notes.values()) == {"原因 RECONCILIATION_NOT_BUILT+SOURCE_DIFFERENCE"}


def test_the_old_and_candidate_snapshots_must_share_the_new_correction(tmp_path: Path) -> None:
    """DST-5・DST-6: 比べる旧 snapshot と休場の候補区間の snapshot は補正後の snapshot に限る。"""
    scene = _scene(tmp_path, new_rule=_RULE_2020)
    assert rr.main(scene.args) == 1
    assert not scene.out.exists() or not any(scene.out.iterdir())


def test_a_record_built_on_a_snapshot_containing_the_refill_is_later(tmp_path: Path) -> None:
    """R3: 補充分 A を含む snapshot を入力にした計画 C の記録が後（参照関係で確かめる）。

    A の結果（取得できなかった）は状態に使わず履歴にだけ残し、C の結果だけを状態にする。
    """
    scene = _scene(tmp_path, with_later=True)
    assert rr.main(scene.args) == 0
    row = scene.rows()[ROW_02_15M]
    # C の照合の時間の原データ（人工の値。出所 histdata）は提供元の値と合わないので、v1.19 では
    # 配信元の値の差としてその塊を作らずに不合格（補充した足が 0 本）になる。
    assert row["states"] == rr.SOURCE_DIFFERENCE
    assert row["basis_plan_ids"] == scene.plan_c
    assert row["bars_with_unordered_records"] == "0"
    assert f"plan={scene.plan_a} refill:{scene.refill_a} {rr.NOT_FETCHED}×4" in row["history"]
    assert f"plan={scene.plan_c} journal:" in row["history"]


def test_unfinished_and_unused_records_make_the_state_undetermined(tmp_path: Path) -> None:
    """R3: 途中の計画と、足を作ったのに新 snapshot に入っていない補充分は「理由未確定」。

    どちらも A とは参照関係で比べられないので、A の結果と併記する。履歴にそれぞれの結果を示す。
    """
    scene = _scene(tmp_path, with_open=True)
    assert rr.main(scene.args) == 0
    row = scene.rows()[ROW_02_15M]
    assert row["states"] == f"{rr.NOT_FETCHED}|{rr.UNDETERMINED}"
    assert row[f"bars_{rr.UNDETERMINED}"] == "4"
    assert row["bars_with_unordered_records"] == "4"
    assert len(row["basis_plan_ids"].split("|")) == 3
    assert f" plan {rr.IN_PROGRESS}×4" in row["history"]
    assert f"{rr.BUILT_NOT_IN_SNAPSHOT}×4" in row["history"]
    # 計画の無い足は変わらない（置き場はすべて読めた）。
    assert scene.rows()[ROW_04_15M]["states"] == rr.NOT_PLANNED


def test_a_rejected_plan_on_a_snapshot_outside_the_ancestry_is_collected(
    tmp_path: Path,
) -> None:
    """R4（決定 2026-10-01「置き場の全計画」）: 祖先でない snapshot を入力にした不合格の計画も
    集めて足ごとに突き合わせる。その足を「計画されていない」と示さない。"""
    scene = _scene(tmp_path, with_other=True)
    assert rr.main(scene.args) == 0
    row = scene.rows()[ROW_04_15M]
    assert rr.NOT_PLANNED not in row["states"]
    # 照合の時間の原データ（人工の値。出所 histdata）は配信元の値の差になる（v1.19）。
    assert row["states"] == rr.SOURCE_DIFFERENCE
    assert row["basis_plan_ids"] == scene.plan_c
    assert "置き場の中はすべて読めた" in scene.report()


def test_an_unreadable_input_snapshot_of_a_record_leaves_coverage_unknown(
    tmp_path: Path,
) -> None:
    """R4: 集めた記録の入力 snapshot が読めなければ前後を確かめられないので網羅性不明。"""
    scene = _scene(tmp_path, with_other=True)
    other = str(_other_snapshot().snapshot_id())
    (tmp_path / "snapshots" / other / "manifest.json").write_text("{")
    assert rr.main(scene.args) == 0
    rows = scene.rows()
    assert rows[ROW_2017]["states"] == rr.UNDETERMINED
    report = scene.report()
    assert "網羅性を確かめられない" in report
    assert f"記録の入力 snapshot の補充分の参照を辿れない: {other}" in report


def test_an_unreadable_journal_drops_only_its_plan(tmp_path: Path) -> None:
    """取得記録の読み取りの OS エラーは計画単位で「読めない」とし、ほかの記録は捨てない（R4）。"""
    scene = _scene(tmp_path, with_b=True)
    journal = scene.refill_root / "_work" / str(scene.plan_b) / "journal.jsonl"
    journal.unlink()
    journal.mkdir()  # 読むと IsADirectoryError（OSError）
    assert rr.main(scene.args) == 0
    rows = scene.rows()
    # 計画 A の補充分は使える。計画 B の記録は落ちる（併記にならない）。
    assert rows[ROW_02_15M]["states"] == rr.NOT_FETCHED
    assert rows[ROW_02_15M]["basis_plan_ids"] == scene.plan_a
    # 網羅性は確かめられないので、記録の無い足は「理由未確定」。
    assert rows[ROW_04_15M]["states"] == rr.UNDETERMINED
    assert f"_work/{scene.plan_b}" in scene.report()


def test_each_state_has_its_own_count_column(tmp_path: Path) -> None:
    """(v) 複数の状態を含む区間を 1 つの理由として数えない（状態ごとの足の数の列）。"""
    scene = _scene(tmp_path, with_b=True)
    assert rr.main(scene.args) == 0
    row = scene.rows()[ROW_02_15M]
    assert row["bar_count"] == "4"
    assert row[f"bars_{rr.NOT_FETCHED}"] == "4"
    assert row[f"bars_{rr.VALIDATION_REJECTED}"] == "4"
    assert row[f"bars_{rr.NOT_PLANNED}"] == "0"
    report = scene.report()
    assert f"| {rr.STATE_LABELS[rr.NOT_FETCHED]}（`{rr.NOT_FETCHED}`） | 2 | 5 | 2.00h |" in report
    assert (
        f"| {rr.STATE_LABELS[rr.VALIDATION_REJECTED]}（`{rr.VALIDATION_REJECTED}`）"
        " | 2 | 5 | 2.00h |"
    ) in report
    assert "- 複数の状態の足を含む区間: 2 / 区間の総数 4" in report


def test_a_partial_refill_keeps_the_holiday_candidate_mark(tmp_path: Path) -> None:
    """(ii) 候補の印は候補区間との重なりで付ける。欠落が短くなっても消えない。"""
    scene = _scene(tmp_path)
    assert rr.main(scene.args) == 0
    rows = scene.rows()
    shortened = rows[ROW_2017]
    assert shortened["holiday_candidate_pending"] == "True"
    assert shortened["holiday_candidates"] == "2"
    assert shortened["states"] == rr.NOT_PLANNED  # 状態とは別の列
    assert rows[ROW_02_15M]["holiday_candidate_pending"] == "False"
    # 短くなった区間だけを見ると、PR #55 の帰属の規則では候補 2 に当たらない。
    gap = rhg.GapInterval(
        "USDJPY",
        "1h@v1",
        rhg.parse_utc("2017-01-02T03:00:00Z"),
        rhg.parse_utc("2017-01-02T07:00:00Z"),
    )
    assert rr.candidate_number(gap) is None


def _gap_utc(symbol: str, tf: str, start: str, end: str) -> rhg.GapInterval:
    return rhg.GapInterval(symbol, tf, rhg.parse_utc(start), rhg.parse_utc(end))


def test_candidate_10_marks_only_its_enumerated_interval() -> None:
    """D03 v1.21: 候補 9 は閉じ、候補 10 は USDCHF/15m/bid の 2021-12-24T21:00Z〜21:30Z だけに付く。

    「金曜 NY 16 時台」の規則では候補を作らない（他の金曜・他の時間足・他の銘柄には付かない）。
    """
    candidates = rr.candidate_intervals({})
    assert rr.HOLIDAY_CANDIDATES == ("1", "2", "10")
    target = _gap_utc("USDCHF", "15m@v1", "2021-12-24T21:00:00Z", "2021-12-24T21:30:00Z")
    assert rr.overlapping_candidates(target, candidates) == ["10"]
    # 部分的に重なる欠落にも付く（候補区間との重なりで付ける）。
    part = _gap_utc("USDCHF", "15m@v1", "2021-12-24T21:15:00Z", "2021-12-24T21:30:00Z")
    assert rr.overlapping_candidates(part, candidates) == ["10"]
    for other in (
        # 別の金曜の NY 16 時台（旧候補 9 の規則に当たる形）
        _gap_utc("USDCHF", "15m@v1", "2021-12-17T21:00:00Z", "2021-12-17T21:30:00Z"),
        # 同じ時刻の別の時間足・別の銘柄
        _gap_utc("USDCHF", "1h@v1", "2021-12-24T21:00:00Z", "2021-12-24T22:00:00Z"),
        _gap_utc("USDJPY", "15m@v1", "2021-12-24T21:00:00Z", "2021-12-24T21:30:00Z"),
        # 区間の直後に接するだけの欠落
        _gap_utc("USDCHF", "15m@v1", "2021-12-24T21:30:00Z", "2021-12-24T21:45:00Z"),
    ):
        assert rr.candidate_number(other) is None
        assert rr.overlapping_candidates(other, candidates) == []
    assert "9" not in {number for items in candidates.values() for _, _, number in items}


def _chunk(start: UtcTime, count: int = 4) -> UnreconciledChunk:
    return UnreconciledChunk(
        series=USDJPY_15M,
        chunk_start=start,
        target_count=count,
        causes=(UnreconciledCause.NO_RECONCILIATION_BAR,),
    )


def test_unreconciled_chunks_are_shown_per_record_with_relabelled_times() -> None:
    """D03 v1.21: 未照合の塊は記録元ごとに分けて示し、合計は「記録上の延べ N 塊」。補正前の入力の
    計画の塊は補正後の時刻へ読み替えて示し、元の時刻と「補正前の入力」の印も示す。"""
    refill = cast(
        VerifiedRefill,
        SimpleNamespace(
            manifest=SimpleNamespace(
                refill_id="a" * 64,
                plan_id="b" * 64,
                unreconciled=(_chunk(HOUR_02), _chunk(HOUR_03)),
            )
        ),
    )
    old_plan = rr.Record(
        plan_id="c" * 64,
        input_snapshot="d" * 64,
        source="journal:7",
        refill_id=None,
        recorded_at="2026-10-05T00:00:00Z",
        is_state=True,
        results={},
        rejection=cast(Rejection, SimpleNamespace(unreconciled=(_chunk(HOUR_01, 8),))),
        pre_correction=True,
        relabel=_RULE_2020,
    )
    lines = rr._unreconciled_lines([refill], [old_plan], {"b" * 64: None})
    text = "\n".join(lines)
    assert f"| 補充分 `{'a' * 12}…`（新 snapshot に入っている。計画 `{'b' * 12}…`） | 2 |" in lines
    assert f"| 不合格のままの計画 `{'c' * 12}…`（journal:7。補正前の入力） | 1 |" in lines
    assert "記録上の延べ 3 塊" in text
    # 補充分の塊は元の時刻のまま、印なし。
    assert "- USDJPY/15m/bid 2020-11-30T02:00:00Z（対象足 4 本。" in text
    # 補正前の入力の計画の塊: 補正後の時刻（01:00 → 02:00）と元の時刻と印。
    assert (
        "- USDJPY/15m/bid 2020-11-30T02:00:00Z（元の時刻 2020-11-30T01:00:00Z・補正前の入力）"
        "（対象足 8 本。原因 NO_RECONCILIATION_BAR）" in text
    )
    # 112 のような 1 つの数だけの表示はしない。
    assert "未照合の塊（補充分に書かず、人間の判断を待つ）: " not in text


def test_the_report_counts_unreconciled_chunks_as_a_total_over_records(tmp_path: Path) -> None:
    scene = _scene(tmp_path, with_b=True)
    assert rr.main(scene.args) == 0
    report = scene.report()
    assert "記録上の延べ" in report
    assert f"| 補充分 `{scene.refill_a[:12]}…`（新 snapshot に入っている。計画" in report
    assert f"| 不合格のままの計画 `{str(scene.plan_b)[:12]}…`（journal:" in report
    assert "（D03 §3.4.2 の候補 1・2・10。" in report


def test_an_unapproved_snapshot_gives_only_a_draft(tmp_path: Path) -> None:
    """(iii) 承認されていない新 snapshot からの出力は「下書き」と明示する。"""
    scene = _scene(tmp_path, approved=False)
    assert rr.main(scene.args) == 0
    assert sorted(path.name for path in scene.out.iterdir()) == [
        "refill_report_draft.md",
        "residual_gaps_draft.csv",
    ]
    report = scene.report("refill_report_draft.md")
    assert report.startswith("# 【下書き】")
    assert "未承認（この出力は下書き）" in report


@pytest.mark.parametrize("stray", ["file", "link", "dir", "work_file", "broken_plan", "partial"])
def test_unknown_coverage_turns_not_planned_into_undetermined(tmp_path: Path, stray: str) -> None:
    """(iv) 置き場に読めないもの・想定外のものがあれば「計画されていない」と言わない。"""
    scene = _scene(tmp_path)
    root = scene.refill_root
    if stray == "file":
        (root / ("9" * 64)).write_text("not a refill")
    elif stray == "link":
        (root / ("8" * 64)).symlink_to(root / scene.refill_a)
    elif stray == "dir":
        (root / "_orphan").mkdir()
    elif stray == "work_file":
        (root / "_work" / "notes.txt").write_text("x")
    elif stray == "broken_plan":
        (root / "_work" / ("e" * 64)).mkdir()
        (root / "_work" / ("e" * 64) / "plan.json").write_text("{not json")
    else:
        (root / ("f" * 64)).mkdir()  # 完成の印の無い書きかけの補充分
    assert rr.main(scene.args) == 0
    rows = scene.rows()
    assert rows[ROW_04_15M]["states"] == rr.UNDETERMINED
    assert rows[ROW_2017]["states"] == rr.UNDETERMINED
    assert rows[ROW_02_15M]["states"] == rr.NOT_FETCHED  # 記録のある足は変わらない
    assert "網羅性を確かめられない" in scene.report()


# --- 第6巡の指摘の再現（本体の検算を使う）--------------------------------------------------


def test_a_manifest_whose_counts_disagree_with_its_lists_is_refused(tmp_path: Path) -> None:
    """補充の manifest 全体の不変条件（系列ごとの本数と一覧・行数）を本体の型で確かめる。

    系列ごとの本数は ``refill_id`` に入らないので、識別子の計算し直しだけでは見つからない。
    """
    scene = _scene(tmp_path)
    path = scene.refill_root / scene.refill_a / "refill_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    count = next(c for c in payload["series_counts"] if c["series"] == "USDJPY/15m/bid")
    count["built"] -= 1
    count["not_built"] += 1
    path.write_text(json.dumps(payload))
    assert rr.main(scene.args) == 1
    assert not scene.out.exists()


def test_a_journal_line_of_an_unknown_shape_is_not_used(tmp_path: Path) -> None:
    """取得記録の行の中身を本体の行の型で読む。ダイジェストが合っても形が違えば使わない。"""
    scene = _scene(tmp_path)
    store = FsRefillStore(root=scene.refill_root)
    store.append_journal(
        scene.plan_a,
        {"kind": "validation", "passed": False, "at": "2026-10-02T00:00:00Z", "reasons": ["x"]},
    )
    assert rr.main(scene.args) == 0
    rows = scene.rows()
    assert rows[ROW_04_15M]["states"] == rr.UNDETERMINED
    assert f"_work/{scene.plan_a}" in scene.report()


def test_the_reproduction_command_quotes_paths_for_the_shell(tmp_path: Path) -> None:
    """再現のコマンドは空白を含むパスでもそのまま動くようにシェル向けに引用する。"""
    scene = _scene(tmp_path / "with space")
    assert rr.main(scene.args) == 0
    report = scene.report()
    block = report.split("## 5. 再現", 1)[1].split("```")[1].strip()
    assert shlex.split(block) == [
        "uv",
        "run",
        "python",
        "-m",
        "tools.ops.refill_report",
        *scene.args,
    ]


# --- 入力の検算（本体の検算で止まる）--------------------------------------------------------


@pytest.mark.parametrize(
    "case", ["bar_changed", "bar_missing", "extra", "temporary", "validation", "identity"]
)
def test_every_file_of_a_used_refill_is_checked(tmp_path: Path, case: str) -> None:
    """報告に使う補充分は本体の W5 の検算をすべて通す。合わなければ何も書かずに止まる。"""
    scene = _scene(tmp_path)
    refill = scene.refill_root / scene.refill_a
    if case == "bar_changed":
        with (refill / "USDJPY_15m_refill.csv").open("ab") as handle:
            handle.write(b"\n")
    elif case == "bar_missing":
        (refill / "USDJPY_15m_refill.csv").unlink()
    elif case == "extra":
        (refill / "notes.txt").write_text("x")
    elif case == "temporary":
        (refill / ".tmp-validation.json-1-x").write_text("x")
    elif case == "validation":
        (refill / "validation.json").write_text("{}")
    else:
        path = refill / "refill_manifest.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["code_version"] = "something-else"
        path.write_text(json.dumps(payload))
    assert rr.main(scene.args) == 1
    assert not scene.out.exists()


def test_an_altered_snapshot_manifest_is_refused(tmp_path: Path) -> None:
    """snapshot の識別子は本体の読込が内容から計算し直す（D03 §3.7.1）。"""
    scene = _scene(tmp_path)
    path = tmp_path / "snapshots" / scene.new / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["resolved_classifications"] = payload["resolved_classifications"][1:]
    path.write_text(json.dumps(payload))
    assert rr.main(scene.args) == 1
    assert not scene.out.exists()


@pytest.mark.parametrize(
    "field, value",
    [("approved_by", ["human"]), ("comment", {"note": "x"}), ("approved_at", 20261002)],
)
def test_an_approval_record_of_another_shape_is_refused(
    tmp_path: Path, field: str, value: object
) -> None:
    """承認の記録は識別子の計算対象外なので、文字列へ変換せずに形と型を確かめる（R1）。"""
    scene = _scene(tmp_path)
    path = tmp_path / "snapshots" / scene.new / "manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["approval"][field] = value
    path.write_text(json.dumps(payload))
    assert rr.main(scene.args) == 1
    assert not scene.out.exists()


def test_an_existing_report_is_never_overwritten(tmp_path: Path) -> None:
    """出力は「存在すれば失敗」: どちらかが既にあれば、どちらも書かない。"""
    scene = _scene(tmp_path)
    scene.out.mkdir()
    (scene.out / "refill_report.md").write_text("earlier report")
    assert rr.main(scene.args) == 1
    assert (scene.out / "refill_report.md").read_text() == "earlier report"
    assert not (scene.out / "residual_gaps.csv").exists()


# --- 本文と起動 --------------------------------------------------------------------------


def test_the_report_has_the_five_sections_and_no_prices(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    assert rr.main(scene.args) == 0
    report = scene.report()
    for heading in (
        "## 1. 入力と出力の識別",
        "## 2. 補充の結果",
        "## 3. 残存欠落",
        "## 4. 実行可能な連続期間",
        "## 5. 再現",
    ):
        assert heading in report
    assert not report.startswith("# 【下書き】")
    assert f"`{scene.refill_a}`" in report
    assert "計画 1 件・補充分 1 件" in report
    assert "置き場の中はすべて読めた" in report
    short = f"`{scene.refill_a[:12]}…`"
    assert f"| {short} | USDJPY/15m/bid | 8 | 4 | 4 | HOUR_NOT_FETCHED 4 |" in report
    assert f"| {short} | USDJPY/1h/bid | 2 | 1 | 1 | HOUR_NOT_FETCHED 1 |" in report
    assert "| USDJPY 15m@v1 | 2 / 2.25h | 2 / 1.25h |" in report
    assert "104.0" not in report  # 価格は書かない
    assert "戦略の成績" in report


def test_the_reproduction_command_runs_from_the_repository_root() -> None:
    """報告の「再現」に書くコマンドの形（``-m tools.ops.refill_report``）で起動できる。"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "tools.ops.refill_report", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
