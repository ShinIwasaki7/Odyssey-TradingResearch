"""書き出しの流れ（D03 §14.7・§14.10・§14.11・§14.11.1・§14.12 の出来事10・11）。

固定の応答を返す偽の取得元と一時ディレクトリの補充の置き場で、計画 → 取得 → 書き出しを通す
（D03 §14.16）。実際の提供元へは通信しない。

- 合格なら補充分 `<refill_id>/` に補充した足のファイル・`validation.json`・`refill_manifest.json`
  を書き、manifest は価格を持たない。書いた後の検算が通る。
- 同じコードでもう一度書き出すと拒否する（存在すれば失敗）。コードの版が違えば新しい
  `refill_id` で書き出し直し、前の補充分は変えない。
- 取得を終えていない・保管場所のファイルが消えた・ロックがある・書きかけの補充分がある、では
  何も書かない。
- 不合格なら補充分を書かず、検証の結果（理由と構造的な記録）を取得記録に追記する。
- 受入れの前の検算（置き場のファイルの集合・sha256・行数、同じ計画の重複、重ねた補充分の
  渡し漏れ）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.adapters.refill_store import FsRefillStore
from odyssey_fx.marketdata.application.refill_fetch import (
    PlanState,
    derive_state,
    fetch_plan,
    read_journal,
)
from odyssey_fx.marketdata.application.refill_finalize import (
    FinalizeReport,
    finalize_plan,
    refill_ids_in_sources,
    require_refill_set,
    verify_refill_directory,
)
from odyssey_fx.marketdata.application.refill_plan import build_plan, create_plan
from odyssey_fx.marketdata.domain.access import INITIAL_ACCESS_BOUNDARIES
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.errors import (
    RefillAlreadyExists,
    RefillAlreadyFinalized,
    RefillChainIncomplete,
    RefillNotFetched,
    RefillPlanDuplicated,
    RefillPlanLocked,
    RefillPlanNotFound,
    RefillStoreInconsistent,
)
from odyssey_fx.marketdata.domain.refill import (
    FailureKind,
    RefillFilter,
    RefillPlan,
    ValidationRecord,
)
from odyssey_fx.marketdata.domain.refill_manifest import (
    REFILL_AGGREGATION_RULE_VERSION,
    RefillManifest,
    refill_id_of,
)
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.refill import (
    BI5_00H,
    BI5_01H,
    HOUR_00,
    HOUR_01,
    PROBE_BARS,
    REFILL_CALENDAR,
    USDJPY_1H,
    USDJPY_15M,
    FakeTickSource,
    calendar_ref,
    gap_resolutions,
    manifest_for,
    probe_bar,
    provider_ref,
    raw_bars,
    url_of,
)
from tests.fixtures.synthetic import market

URL_00 = url_of(HOUR_00)
URL_01 = url_of(HOUR_01)
CODE = "code-v1"
NOW = UtcTime.parse("2026-10-01T12:00:00Z")


class _Inputs:
    """書き出しが計画から読むもの（原データの代わりとカレンダー）。"""

    def __init__(self, bars: Mapping[SeriesId, Sequence[Bar]] | None = None) -> None:
        self._bars = raw_bars() if bars is None else bars

    def raw_bars(
        self, plan: RefillPlan
    ) -> tuple[tuple[SeriesId, ...], Mapping[SeriesId, Sequence[Bar]]]:
        del plan
        return (USDJPY_15M, USDJPY_1H), self._bars

    def calendar(self, plan: RefillPlan) -> TradingCalendar:
        del plan
        return REFILL_CALENDAR


def _plan() -> RefillPlan:
    return build_plan(
        manifest=manifest_for(gap_resolutions()),
        raw_bars=raw_bars(),
        calendar=REFILL_CALENDAR,
        calendar_ref=calendar_ref(),
        timeframe_defs=market.TIMEFRAME_DEFS,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        provider=provider_ref(),
        refill_filter=RefillFilter(),
    )


def _fetched(
    tmp_path: Path, responses: Mapping[str, list[bytes | int | FailureKind]] | None = None
) -> tuple[FsRefillStore, str, RefillPlan]:
    store = FsRefillStore(root=tmp_path / "refill")
    plan = _plan()
    plan_id = create_plan(plan, store)
    source = FakeTickSource(
        {URL_00: [BI5_00H], URL_01: [BI5_01H]} if responses is None else responses
    )
    fetch_plan(plan_id, store=store, source=source, retry_failed=False)
    return store, plan_id, plan


def _finalize(
    store: FsRefillStore,
    plan_id: str,
    *,
    code: str = CODE,
    inputs: _Inputs | None = None,
) -> FinalizeReport:
    return finalize_plan(
        plan_id,
        store=store,
        inputs=_Inputs() if inputs is None else inputs,
        boundaries=INITIAL_ACCESS_BOUNDARIES,
        code_version=code,
        clock=lambda: NOW,
    )


def _refill_dirs(tmp_path: Path) -> list[str]:
    return sorted(
        path.name for path in (tmp_path / "refill").iterdir() if not path.name.startswith("_")
    )


def _verified(store: FsRefillStore, name: str) -> RefillManifest:
    return verify_refill_directory(
        name, store.read_refill_manifest(name), store.list_refill_files(name)
    )


# --- 合格して書き出す（出来事10）--------------------------------------------------------


def test_a_passed_validation_writes_the_refill_with_the_manifest_last(tmp_path: Path) -> None:
    store, plan_id, plan = _fetched(tmp_path)
    report = _finalize(store, plan_id)
    assert report.passed
    assert report.state_before is PlanState.FETCH_DONE
    refill = tmp_path / "refill" / str(report.refill_id)
    assert sorted(path.name for path in refill.iterdir()) == [
        "USDJPY_15m_refill.csv",
        "USDJPY_1h_refill.csv",
        "refill_manifest.json",
        "validation.json",
    ]
    manifest = _verified(store, str(report.refill_id))
    assert manifest.plan_id == plan_id
    assert manifest.plan == plan
    assert manifest.snapshot_id == plan.snapshot_id
    assert manifest.code_version == CODE
    assert manifest.aggregation_rule_version == REFILL_AGGREGATION_RULE_VERSION
    assert [(item.name, item.rows) for item in manifest.bar_files] == [
        ("USDJPY_15m_refill.csv", 4),
        ("USDJPY_1h_refill.csv", 1),
    ]
    assert [(str(c.series), c.targets, c.built, c.not_built) for c in manifest.series_counts] == [
        ("USDJPY/15m/bid", 4, 4, 0),
        ("USDJPY/1h/bid", 1, 1, 0),
    ]
    # 時間ファイルごとの出所（取得した記録。照合用の 00 時も含む）。
    assert [
        (str(item.hour.start), item.outcome.value, item.from_archive) for item in manifest.hours
    ] == [
        ("2020-11-30T00:00:00Z", "FETCHED", False),
        ("2020-11-30T01:00:00Z", "FETCHED", False),
    ]
    assert all(item.url.startswith("https://datafeed.dukascopy.com/") for item in manifest.hours)
    # manifest は価格を持たない。
    text = (refill / "refill_manifest.json").read_text(encoding="utf-8")
    assert "103.8" not in text and "104.0" not in text
    # 書いた後はロックが外れ、状態は書き出し済み。
    assert not (tmp_path / "refill/_work" / plan_id / "lock").exists()


def test_the_bar_files_hold_only_the_target_bars_in_the_raw_format(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    report = _finalize(store, plan_id)
    refill = tmp_path / "refill" / str(report.refill_id)
    quarter = (refill / "USDJPY_15m_refill.csv").read_text(encoding="utf-8").splitlines()
    hourly = (refill / "USDJPY_1h_refill.csv").read_text(encoding="utf-8").splitlines()
    assert quarter[0] == ",open,high,low,close,volume,source"
    expected = [
        f"{row[1][:10]} {row[1][11:19]}+00:00,"
        + ",".join(f"{float(value):.3f}" for value in row[2:6])
        + ",0,dukascopy_refill"
        for row in PROBE_BARS
        if row[1].startswith("2020-11-30T01")
    ]
    # 15分足 4 本は 01 時台の対象足だけ（照合用の 00 時台の足は書かない）。
    assert quarter[1:] == expected[:4]
    assert hourly[1:] == expected[4:]


def test_validation_json_records_the_checks_without_failures(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    report = _finalize(store, plan_id)
    payload = json.loads(
        (tmp_path / "refill" / str(report.refill_id) / "validation.json").read_text("utf-8")
    )
    assert payload["passed"] is True
    assert payload["refill_id"] == report.refill_id
    assert (
        payload["reconciled_count"] == payload["matched_count"] == 5
    )  # 00 時の 15分足 4 本と 1時間足
    assert payload["out_of_range_tick_count"] == 0
    assert {item["side"] for item in payload["neighbors"]} == {"AFTER", "BEFORE"}
    assert all(item["status"] == "COMPARED" for item in payload["neighbors"])


def test_the_refill_id_is_recomputed_from_the_hours_and_versions(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    report = _finalize(store, plan_id)
    manifest = _verified(store, str(report.refill_id))
    assert report.refill_id == refill_id_of(
        plan_id, [item.core for item in manifest.hours], REFILL_AGGREGATION_RULE_VERSION, CODE
    )


# --- 存在すれば失敗・書き出し直し（書き出し済み×出来事10）-------------------------------------


def test_finalizing_again_with_the_same_code_is_refused(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    first = _finalize(store, plan_id)
    before = (tmp_path / "refill" / str(first.refill_id) / "refill_manifest.json").read_bytes()
    with pytest.raises(RefillAlreadyFinalized, match="same aggregation rule and code version"):
        _finalize(store, plan_id)
    assert _refill_dirs(tmp_path) == [first.refill_id]
    assert (tmp_path / "refill" / str(first.refill_id) / "refill_manifest.json").read_bytes() == (
        before
    )


def test_a_new_code_version_finalizes_again_under_a_new_refill_id(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    first = _finalize(store, plan_id)
    before = {
        path.name: path.read_bytes()
        for path in (tmp_path / "refill" / str(first.refill_id)).iterdir()
    }
    second = _finalize(store, plan_id, code="code-v2")
    assert second.passed
    assert second.state_before is PlanState.FINALIZED
    assert second.refill_id != first.refill_id
    assert _refill_dirs(tmp_path) == sorted([str(first.refill_id), str(second.refill_id)])
    after = {
        path.name: path.read_bytes()
        for path in (tmp_path / "refill" / str(first.refill_id)).iterdir()
    }
    assert after == before  # 前の補充分は上書きしない
    # 受入れには同じ計画の補充分を 1 つだけ渡す。
    with pytest.raises(RefillPlanDuplicated):
        require_refill_set(
            [_verified(store, str(first.refill_id)), _verified(store, str(second.refill_id))],
            {},
        )


def test_an_existing_refill_directory_is_never_overwritten(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    first = _finalize(store, plan_id)
    with pytest.raises(RefillAlreadyExists):
        store.create_refill_dir(str(first.refill_id))


# --- 書き出せない（拒否。何も書かない）-----------------------------------------------------


def test_a_plan_not_fetched_yet_is_refused(tmp_path: Path) -> None:
    store = FsRefillStore(root=tmp_path / "refill")
    plan_id = create_plan(_plan(), store)
    with pytest.raises(RefillNotFetched, match="without a final result"):
        _finalize(store, plan_id)
    assert _refill_dirs(tmp_path) == []


def test_a_plan_with_hours_left_is_refused(tmp_path: Path) -> None:
    store = FsRefillStore(root=tmp_path / "refill")
    plan_id = create_plan(_plan(), store)
    # 01 時はほかのコマンドが時間のロックを持っているので取れず、未取得のまま残る。
    assert store.acquire_hour_lock(_plan().hour_keys[1])
    fetch_plan(plan_id, store=store, source=FakeTickSource({URL_00: [BI5_00H]}), retry_failed=False)
    with pytest.raises(RefillNotFetched):
        _finalize(store, plan_id)


def test_a_missing_archive_file_is_refused_until_fetched_again(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    archived = sorted((tmp_path / "refill/_ticks").rglob("*.json"))
    archived[-1].unlink()
    with pytest.raises(RefillNotFetched, match="archive files"):
        _finalize(store, plan_id)
    assert _refill_dirs(tmp_path) == []


def test_a_held_plan_lock_refuses_finalizing(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    store.acquire_plan_lock(plan_id)
    other = FsRefillStore(root=tmp_path / "refill")
    with pytest.raises(RefillPlanLocked):
        _finalize(other, plan_id)
    assert _refill_dirs(tmp_path) == []


def test_an_incomplete_refill_directory_blocks_finalizing(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    (tmp_path / "refill" / ("e" * 64)).mkdir()
    with pytest.raises(RefillStoreInconsistent, match="incomplete refill"):
        _finalize(store, plan_id)


def test_an_unknown_plan_and_a_finalized_plan_without_its_work_directory(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    with pytest.raises(RefillPlanNotFound):
        _finalize(store, "f" * 64)
    _finalize(store, plan_id)
    shutil.rmtree(tmp_path / "refill/_work" / plan_id)
    with pytest.raises(RefillAlreadyFinalized, match="work directory is gone"):
        _finalize(store, plan_id)


# --- 不合格（出来事11）-------------------------------------------------------------------


def test_a_reconciliation_mismatch_writes_nothing_and_records_the_failure(tmp_path: Path) -> None:
    store, plan_id, plan = _fetched(tmp_path)
    altered = dict(raw_bars())
    # 照合用の 00 時の 1時間足の終値が録画した tick と合わない原データ。
    hourly = list(altered[USDJPY_1H])
    index = next(i for i, bar in enumerate(hourly) if bar.bar_start == HOUR_00)
    original = probe_bar("1h", "2020-11-30T00:00:00Z")
    hourly[index] = Bar(
        series=original.series,
        interval=original.interval,
        open=original.open,
        high=original.high,
        low=original.low,
        close=original.open,
        volume=original.volume,
        available_at=original.available_at,
        provenance=original.provenance,
    )
    altered[USDJPY_1H] = tuple(hourly)
    report = _finalize(store, plan_id, inputs=_Inputs(altered))
    assert not report.passed
    assert _refill_dirs(tmp_path) == []
    entries = read_journal(plan_id, store.read_journal(plan_id), plan).entries
    rejected = [entry for entry in entries if isinstance(entry, ValidationRecord)]
    assert len(rejected) == 1 and not rejected[0].passed
    (mismatch,) = rejected[0].details["mismatches"]
    assert mismatch["series"] == "USDJPY/1h/bid" and mismatch["start"] == "2020-11-30T00:00:00Z"
    assert [name for name, _ in mismatch["differences"]] == ["close"]
    assert derive_state(plan, entries, finalized=False) is PlanState.REJECTED
    # もう一度書き出しても結果が順に残る（前の結果は消さない）。
    _finalize(store, plan_id, inputs=_Inputs(altered))
    entries = read_journal(plan_id, store.read_journal(plan_id), plan).entries
    assert sum(isinstance(entry, ValidationRecord) for entry in entries) == 2


def test_no_built_bar_records_why_each_target_was_not_built(tmp_path: Path) -> None:
    store, plan_id, plan = _fetched(tmp_path, {URL_00: [BI5_00H], URL_01: [404]})
    report = _finalize(store, plan_id)
    assert not report.passed
    assert any("no bar was built" in reason for reason in report.validation.failures)
    entries = read_journal(plan_id, store.read_journal(plan_id), plan).entries
    (rejected,) = [entry for entry in entries if isinstance(entry, ValidationRecord)]
    reasons = {
        (item["series"], item["reason"], item["detail"]) for item in rejected.details["not_built"]
    }
    assert reasons == {
        ("USDJPY/15m/bid", "HOUR_NOT_FETCHED", "HTTP_404"),
        ("USDJPY/1h/bid", "HOUR_NOT_FETCHED", "HTTP_404"),
    }
    assert len(rejected.details["not_built"]) == 5


# --- 受入れの前の検算（D03 §14.11・§14.11.1 の W5）----------------------------------------


@pytest.mark.parametrize("case", ["extra", "missing", "changed", "renamed"])
def test_a_tampered_refill_fails_its_check(tmp_path: Path, case: str) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    report = _finalize(store, plan_id)
    name = str(report.refill_id)
    refill = tmp_path / "refill" / name
    if case == "extra":
        (refill / "notes.txt").write_text("x", encoding="utf-8")
    elif case == "missing":
        (refill / "validation.json").unlink()
    elif case == "changed":
        path = refill / "USDJPY_1h_refill.csv"
        path.write_text(path.read_text(encoding="utf-8").replace("dukascopy_refill", "histdata"))
    else:
        moved = tmp_path / "refill" / ("9" * 64)
        refill.rename(moved)
        name = moved.name
    with pytest.raises(RefillStoreInconsistent):
        _verified(store, name)


def test_a_manifest_rewritten_by_hand_fails_the_identity_check(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    report = _finalize(store, plan_id)
    path = tmp_path / "refill" / str(report.refill_id) / "refill_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["code_version"] = "something-else"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RefillStoreInconsistent, match="refill id"):
        _verified(store, str(report.refill_id))


def test_layered_refills_must_be_given_together(tmp_path: Path) -> None:
    store, plan_id, _ = _fetched(tmp_path)
    report = _finalize(store, plan_id)
    manifest = _verified(store, str(report.refill_id))
    earlier = "7" * 64
    sources = (
        "data/raw/market/USDJPY_15m_merged.csv",
        f"data/raw/market/refill/{earlier}/USDJPY_15m_refill.csv",
    )
    assert refill_ids_in_sources(sources) == (earlier,)
    with pytest.raises(RefillChainIncomplete, match=earlier):
        require_refill_set([manifest], {manifest.snapshot_id: sources})
    # 入力 snapshot が補充分を含まなければ通る。読めなければ確かめられないので止める。
    require_refill_set([manifest], {manifest.snapshot_id: sources[:1]})
    with pytest.raises(RefillChainIncomplete, match="cannot be read"):
        require_refill_set([manifest], {})
