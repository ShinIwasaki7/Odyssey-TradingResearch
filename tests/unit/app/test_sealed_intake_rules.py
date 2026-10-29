"""封印の取り込みの結線の部品（許可の定義・manifest の記録・初期状態・原ファイルの読み取り）。

人工の値と版管理の設定だけを使う（市場データは読まない）。経路全体は
`tests/integration/app/test_sealed_intake.py`。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.app.config.quality_checks import (
    QUALITY_CHECKS_DIRECTORY,
    load_quality_check_definitions,
)
from odyssey_fx.app.config.sealed_window import SEALED_WINDOW_PATH, load_sealed_window
from odyssey_fx.app.mt5 import load_datasource
from odyssey_fx.app.sealed_intake import (
    SEALED_ACCEPT_TOOL,
    SEALED_CONFIG_FILES,
    SEALED_FINALIZE_TOOL,
    SYMBOL_SPECS_DIR,
    SealedIntakeRefused,
    initial_state_provider,
    require_symbol_spec_matches,
    sealed_config_digest,
)
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.quality_permit import QualityTargetKind
from odyssey_fx.marketdata.adapters.mt5_csv_source import require_raw_read_allowed
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.application.mt5_acceptance import Mt5AcceptanceFailed, Mt5FetchEntry
from odyssey_fx.marketdata.domain.access import (
    AccessClass,
    HoldoutState,
    SealedAccessBoundaries,
)
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation, MarketDataValueError
from odyssey_fx.marketdata.domain.sealed_state import (
    ExistingSealedPartition,
    InitialStateReason,
    ViolationScope,
)
from odyssey_fx.marketdata.domain.snapshot import (
    SEALED_INITIAL_REASONS,
    PartitionId,
    QualityReadRecord,
    SealedInitialState,
)
from tests.fixtures.mt5 import market as m5
from tests.fixtures.synthetic import market, snapshots

ROOT = Path(__file__).resolve().parents[3]
T = UtcTime.parse
WEEK = T("2026-11-01T22:00:00Z")


def _pid(timeframe: str = "1m", start: UtcTime = WEEK) -> PartitionId:
    return PartitionId(
        series=market.series(timeframe_id=timeframe),
        access_class=AccessClass.FORWARD_HOLDOUT,
        period_start=start,
    )


# --- 許可の定義（configs/quality_checks/）----------------------------------------------------


def test_the_two_definitions_match_the_fixed_files_of_today() -> None:
    definitions = {
        definition.tool: definition
        for definition in load_quality_check_definitions(ROOT / QUALITY_CHECKS_DIRECTORY)
    }
    assert set(definitions) == {SEALED_ACCEPT_TOOL, SEALED_FINALIZE_TOOL}
    digest = sealed_config_digest(ROOT)
    assert all(definition.config_digest == digest for definition in definitions.values())
    assert definitions[SEALED_ACCEPT_TOOL].target_kind is QualityTargetKind.RAW_FILE
    finalize = definitions[SEALED_FINALIZE_TOOL]
    assert finalize.target_kind is QualityTargetKind.PARTITION
    assert finalize.timeframes == frozenset({"1m", "15m", "1h", "4h_ny17", "1d_ny17"})
    assert len(finalize.symbols) == 8


# --- 銘柄の設定と検査 3（順 6 の S4b。D03 §16.3.1 の検査 3）-----------------------------------

#: 研究期間の全期間取得（2026-10-16）の取得記録の版管理の写し。端末が返した 8 通貨の銘柄仕様
#: （`symbols` の `point`・`digits`・`chart_mode`）だけを読む（価格・件数・時刻は読まない）。
RESEARCH_FETCH_COPY = ROOT / "docs/data/oanda_mt5_full_fetch_2026-10-16/manifest.json"


def test_the_symbol_settings_of_the_eight_agree_with_the_terminal_record() -> None:
    """8 通貨の銘柄の設定は、OANDA MT5 の端末が研究期間の取得で返した銘柄仕様と同じ。

    検査 3 が合格することで、設定の値の根拠を確かめる（価格データから推定しない）。研究期間の
    取得記録の `symbols` は端末の `symbol_info` の値（`tools/survey/mt5_api_probe.py` の
    `symbol_meta`）で、封印の取得の記録と同じ形。
    """
    record = json.loads(RESEARCH_FETCH_COPY.read_text(encoding="utf-8"))
    window = load_sealed_window(ROOT / SEALED_WINDOW_PATH)
    datasource = load_datasource(ROOT)
    assert len(window.symbols) == 8
    for symbol in window.symbols:
        path = require_symbol_spec_matches(record, symbol, ROOT, datasource)
        assert path == SYMBOL_SPECS_DIR / f"{symbol}.yaml"


def test_the_symbol_settings_are_not_in_the_config_digest() -> None:
    """銘柄の設定は許可の定義の設定のダイジェストの対象ではない。

    S4b の仮置き。承認済みの 9 ファイル（PR #111 の仮置き S4a-7）のまま。
    """
    assert not any(path.is_relative_to(SYMBOL_SPECS_DIR) for path in SEALED_CONFIG_FILES)


def test_the_symbol_check_compares_the_tick_digits_and_chart_mode() -> None:
    datasource = load_datasource(ROOT)
    good = {"symbols": {"EURUSD": {"chart_mode": 0, "point": 1e-05, "digits": 5}}}
    assert require_symbol_spec_matches(good, Symbol("EURUSD"), ROOT, datasource)
    for field, value in (("point", 0.0001), ("digits", 4), ("chart_mode", 1), ("digits", True)):
        bad = {"symbols": {"EURUSD": {**good["symbols"]["EURUSD"], field: value}}}
        with pytest.raises(SealedIntakeRefused, match=field):
            require_symbol_spec_matches(bad, Symbol("EURUSD"), ROOT, datasource)


# --- manifest の記録（ADR-0038 第3項、ADR-0036 第7項）---------------------------------------


def test_the_reason_vocabulary_matches_the_domain_enum() -> None:
    assert {reason.value for reason in InitialStateReason} == SEALED_INITIAL_REASONS


def test_a_sealed_partition_needs_exactly_one_initial_state() -> None:
    record = snapshots.partition(market.series(), AccessClass.FORWARD_HOLDOUT)
    with pytest.raises(MarketDataValueError, match="exactly one initial state"):
        snapshots.manifest(partitions=(record,), sealed_initial_states=())


def test_the_initial_states_enter_the_identity_and_the_quality_read_does_not(
    tmp_path: Path,
) -> None:
    record = snapshots.partition(market.series(), AccessClass.FORWARD_HOLDOUT)
    sealed = snapshots.manifest(partitions=(record,))
    consumed = snapshots.manifest(
        partitions=(record,),
        sealed_initial_states=(
            SealedInitialState(
                partition_id=record.partition_id,
                state=HoldoutState.CONSUMED,
                reasons=("EXISTING_CONSUMED",),
            ),
        ),
    )
    assert sealed.snapshot_id() != consumed.snapshot_id()
    read = sealed.with_quality_read(QualityReadRecord(permit_required=True, access_id="a1"))
    assert read.snapshot_id() == sealed.snapshot_id()
    # 研究期間だけの manifest の識別子は、初期状態の項目を足す前と同じ（鍵を足さない）。
    research = snapshots.manifest()
    assert "sealed_initial_states" not in research.identity_payload()
    store = ParquetSnapshotStore(root=tmp_path)
    directory = str(read.snapshot_id())
    store.create_directory(directory, directory)
    store.write_manifest(directory, read)
    assert store.read_manifest(directory) == read


@pytest.mark.parametrize(("required", "access_id"), [(True, None), (True, ""), (False, "a1")])
def test_the_quality_read_record_pairs_the_flag_and_the_access_id(
    required: bool, access_id: str | None
) -> None:
    with pytest.raises(MarketDataValueError):
        QualityReadRecord(permit_required=required, access_id=access_id)


def test_initial_state_reasons_must_match_the_state() -> None:
    with pytest.raises(MarketDataValueError):
        SealedInitialState(partition_id=_pid(), state=HoldoutState.CONSUMED, reasons=())
    with pytest.raises(MarketDataValueError):
        SealedInitialState(
            partition_id=_pid(), state=HoldoutState.SEALED, reasons=("EXISTING_CONSUMED",)
        )


# --- 初期状態の決め方（例外 1・2）-------------------------------------------------------------


def test_the_provider_applies_both_exceptions() -> None:
    whole = ViolationScope(series=None, symbol=Symbol("USDJPY"), period=None)
    decide = initial_state_provider((whole,), ())
    assert decide(_pid()).reasons == ("VIOLATION_JUDGMENT",)
    existing = (ExistingSealedPartition(partition_id=_pid(), state=HoldoutState.CONSUMED),)
    decide = initial_state_provider((), existing)
    assert decide(_pid()).state is HoldoutState.CONSUMED
    assert decide(_pid("15m")).state is HoldoutState.SEALED  # 別の系列は数えない
    unknown = (ExistingSealedPartition(partition_id=_pid(), state=None),)
    with pytest.raises(Exception, match="cannot be derived"):
        initial_state_provider((), unknown)(_pid())


# --- 研究期間の受入れは封印の区分の partition を作らない（D03 §16.1、S2b-7）------------------


def test_research_bars_from_2024_never_become_a_sealed_partition(tmp_path: Path) -> None:
    table = m5.write_rule_files(tmp_path)
    week = Interval(start=T("2026-01-04T22:00:00Z"), end=T("2026-01-09T22:00:00Z"))
    rows = m5.minute_rows(market.calendar(), week)
    for boundaries in (
        SealedAccessBoundaries(acquisition_start=None),
        SealedAccessBoundaries(acquisition_start=T("2025-11-02T22:00:00Z")),
    ):
        with pytest.raises(Mt5AcceptanceFailed):
            m5.build_pending(
                market.calendar(),
                table,
                [m5.MonthSpec(2026, 1, rows)],
                boundaries=boundaries,  # type: ignore[arg-type]
            )


# --- 原ファイルの読み取りの確かめ（ADR-0036 第8項・第9項の 3）---------------------------------


def _entry(requested_to: str) -> Mt5FetchEntry:
    return Mt5FetchEntry(
        rid="F-USDJPY-2023-12",
        symbol=Symbol("USDJPY"),
        file="USDJPY/USDJPY_2023-12_m1.csv.gz",
        status="ok",
        requested_from_api=int(T("2023-12-01T00:00:00Z").value.timestamp()),
        requested_to_api=int(T(requested_to).value.timestamp()),
        rows=1,
        first_bar_time=None,
        last_bar_time=None,
        sha256_gz="a" * 64,
        sha256_csv="b" * 64,
    )


def test_without_a_permit_a_request_past_2024_is_never_read(tmp_path: Path) -> None:
    path = tmp_path / "oanda_mt5" / "x.csv.gz"
    require_raw_read_allowed(path, _entry("2024-01-01T00:00:00Z"), read_permit=None, location=None)
    with pytest.raises(HoldoutAccessViolation, match="past 2024-01-01"):
        require_raw_read_allowed(
            path, _entry("2024-01-01T00:01:00Z"), read_permit=None, location=None
        )


def test_without_a_permit_the_sealed_directory_is_never_read(tmp_path: Path) -> None:
    path = tmp_path / "data" / "raw" / "oanda_mt5_sealed" / "d" / "USDJPY" / "x.csv.gz"
    with pytest.raises(HoldoutAccessViolation, match="sealed raw file"):
        require_raw_read_allowed(
            path, _entry("2024-01-01T00:00:00Z"), read_permit=None, location=None
        )


def test_replace_keeps_the_manifest_records() -> None:
    record = snapshots.partition(market.series(), AccessClass.HOLDOUT_SINCE_2024)
    manifest = snapshots.manifest(partitions=(record,))
    assert replace(manifest).sealed_initial_states == manifest.sealed_initial_states
