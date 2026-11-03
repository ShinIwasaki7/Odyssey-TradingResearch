"""関所の許可の定義の照合を研究期間の置き場へ広げる（D03 §17.6、ADR-0040、D17 の G2 (8)）。

人工の定義と一時ディレクトリだけを使う（原ファイルは読まない）。確かめること:

- (a) 研究期間の置き場 `data/raw/oanda_mt5/<取得日>/<通貨>/` と研究履歴の区分を覆う定義で照合が
  通り、開始の行（区分は研究履歴、種類は確認の読み取り）と終了の行が書ける。
- (b) 置き場（封印の置き場・別の通貨の場所・`data/raw/oanda_mt5x`・`..`・絶対・逆斜線）、区分
  （研究履歴の定義で封印の区分、封印の定義で研究履歴）、期間（2024-01-01 以降を含む・2016 年より
  前）、種類（研究履歴の定義で受入れ）、出力（時刻）がずれると許可を出さず、開始の行も書かない。
- (c) 混ぜた定義・隔離の区分を含む定義・研究履歴の定義の規則に外れる定義は作れない（読込でも拒む）。
- (e) 封印の定義と研究期間の定義を並べても、それぞれの要求はそれぞれの定義で通る。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.quality_checks import load_quality_check_definition
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.application.holdout_gate import HoldoutGate
from odyssey_fx.evaluation.domain.errors import EvaluationError
from odyssey_fx.evaluation.domain.quality_permit import (
    RESEARCH_PERIOD,
    QualityCheckDefinition,
    QualityCheckRequest,
    QualityPermitRefusal,
    QualityReadRefused,
    QualityTargetKind,
)
from odyssey_fx.marketdata.adapters.quality_access_log_store import (
    QUALITY_ACCESS_LOG_PATH,
    QualityAccessLogFile,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.quality_access import (
    RESEARCH_QUALITY_CLASSES,
    RESEARCH_VERIFY_OUTPUT_KINDS,
    SEALED_INTAKE_OUTPUT_KINDS,
    AccessPeriod,
    PartitionTarget,
    QualityAccessEnd,
    QualityAccessJudgment,
    QualityAccessLogContents,
    QualityAccessResult,
    QualityAccessStart,
    QualityCheckKind,
    QualityOutputKind,
    QualityReadPermit,
    QualityRecordKind,
    RawFileTarget,
    ToolRef,
    violation_scopes,
)
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market

T = UtcTime.parse
RESEARCH_TOOL = ToolRef(name="oanda_mt5_research_raw_verify", version="1")
SEALED_TOOL = ToolRef(name="oanda_mt5_sealed_accept", version="1")
DIGEST = "c" * 64
EURJPY = Symbol("EURJPY")
RAW = RawFileTarget(
    location="data/raw/oanda_mt5/2026-10-16/EURJPY/EURJPY_2019-03_m1.csv.gz",
    sha256="d" * 64,
    symbol=EURJPY,
)
SEALED_RAW = RawFileTarget(
    location="data/raw/oanda_mt5_sealed/2026-11-14/EURJPY/EURJPY_2026-11-01_m1.csv.gz",
    sha256="e" * 64,
    symbol=EURJPY,
)
RESEARCH_DEFINITION = QualityCheckDefinition(
    tool=RESEARCH_TOOL,
    config_digest=DIGEST,
    datasource="oanda_mt5_m1",
    check_kind=QualityCheckKind.RAW_FILE_VERIFICATION,
    classes=RESEARCH_QUALITY_CLASSES,
    period=RESEARCH_PERIOD,
    target_kind=QualityTargetKind.RAW_FILE,
    symbols=frozenset({EURJPY, Symbol("USDJPY")}),
    timeframes=frozenset({"1m"}),
    output_kinds=RESEARCH_VERIFY_OUTPUT_KINDS,
    source="configs/quality_checks/oanda_mt5_research_raw_verify.yaml",
)
SEALED_DEFINITION = QualityCheckDefinition(
    tool=SEALED_TOOL,
    config_digest=DIGEST,
    datasource="oanda_mt5_m1",
    check_kind=QualityCheckKind.ACCEPTANCE,
    classes=frozenset({AccessClass.HOLDOUT_SINCE_2024, AccessClass.FORWARD_HOLDOUT}),
    period=AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=None),
    target_kind=QualityTargetKind.RAW_FILE,
    symbols=frozenset({EURJPY}),
    timeframes=frozenset({"1m"}),
    output_kinds=SEALED_INTAKE_OUTPUT_KINDS,
    source="configs/quality_checks/oanda_mt5_sealed_accept.yaml",
)
REQUEST = QualityCheckRequest(
    tool=RESEARCH_TOOL,
    config_digest=DIGEST,
    check_kind=QualityCheckKind.RAW_FILE_VERIFICATION,
    classes=RESEARCH_QUALITY_CLASSES,
    period=RESEARCH_PERIOD,
    targets=(RAW,),
    output_kinds=RESEARCH_VERIFY_OUTPUT_KINDS,
)
SEALED_REQUEST = QualityCheckRequest(
    tool=SEALED_TOOL,
    config_digest=DIGEST,
    check_kind=QualityCheckKind.ACCEPTANCE,
    classes=frozenset({AccessClass.HOLDOUT_SINCE_2024, AccessClass.FORWARD_HOLDOUT}),
    period=AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=None),
    targets=(SEALED_RAW,),
    output_kinds=SEALED_INTAKE_OUTPUT_KINDS,
)


def _hour(start: UtcTime) -> Interval:
    return Interval(start=start, end=start + timedelta(hours=1))


def _root(tmp_path: Path) -> Path:
    (tmp_path / "research").mkdir()
    (tmp_path / QUALITY_ACCESS_LOG_PATH).write_bytes(b"")
    return tmp_path


def _gate(root: Path, *definitions: QualityCheckDefinition) -> HoldoutGate:
    return HoldoutGate(definitions or (RESEARCH_DEFINITION,), QualityAccessLogFile(root))


def _begin(gate: HoldoutGate, request: QualityCheckRequest = REQUEST) -> object:
    return gate.begin_quality_read(
        request, permit_id="p1", access_id="a1", row_id="r1", accessed_at=T("2026-11-04T00:00:00Z")
    )


def _rows(root: Path) -> tuple[object, ...]:
    contents = QualityAccessLogFile(root).read()
    assert isinstance(contents, QualityAccessLogContents)
    return contents.rows


# --- (a) 研究期間の置き場と研究履歴の区分で照合が通る ----------------------------------------


def test_a_research_definition_permits_the_verification_read_of_research_raw_files(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    gate = _gate(root)
    permit = _begin(gate)
    assert isinstance(permit, QualityReadPermit)
    assert permit.classes == RESEARCH_QUALITY_CLASSES
    assert permit.covers_raw_file(RAW.location, "d" * 64)
    assert not permit.covers_raw_file(RAW.location, "f" * 64)
    end = gate.finish_quality_read(
        permit,
        result=QualityAccessResult.COMPLETED,
        row_id="r2",
        finished_at=T("2026-11-04T01:00:00Z"),
    )
    assert isinstance(end, QualityAccessEnd)
    start, finished = _rows(root)
    assert isinstance(start, QualityAccessStart) and isinstance(finished, QualityAccessEnd)
    assert start.classes == RESEARCH_QUALITY_CLASSES
    assert start.check_kind is QualityCheckKind.RAW_FILE_VERIFICATION
    assert start.targets == (RAW,) and start.period == RESEARCH_PERIOD
    assert finished.result is QualityAccessResult.COMPLETED


# --- (b) 置き場・区分・期間・種類・出力がずれると許可を出さず、開始の行も書かない -----------------


@pytest.mark.parametrize(
    "location",
    [
        "data/raw/oanda_mt5_sealed/2026-10-16/EURJPY/EURJPY_2019-03_m1.csv.gz",  # 封印の置き場
        "data/raw/oanda_mt5/2026-10-16/USDJPY/EURJPY_2019-03_m1.csv.gz",  # 別の通貨の場所
        "data/raw/oanda_mt5x/2026-10-16/EURJPY/EURJPY_2019-03_m1.csv.gz",  # 前方一致の取り違え
        "data/raw/oanda_mt5/../oanda_mt5/2026-10-16/EURJPY/a.csv.gz",  # ..
        "/data/raw/oanda_mt5/2026-10-16/EURJPY/a.csv.gz",  # 絶対の置き場
        "data\\raw\\oanda_mt5\\2026-10-16\\EURJPY\\a.csv.gz",  # 逆斜線
        "data/raw/oanda_mt5/EURJPY/a.csv.gz",  # 取得日の段が無い
        "data/raw/market/EURJPY/a.csv",  # 旧データの CSV
    ],
)
def test_a_research_raw_file_outside_its_location_gets_no_permit(
    tmp_path: Path, location: str
) -> None:
    root = _root(tmp_path)
    refused = _begin(_gate(root), replace(REQUEST, targets=(replace(RAW, location=location),)))
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is QualityPermitRefusal.OUTSIDE_DEFINITION
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == b""


@pytest.mark.parametrize(
    ("definitions", "request_", "kind"),
    [
        # 研究履歴の定義で封印の区分を要求する。
        (
            (RESEARCH_DEFINITION,),
            replace(REQUEST, classes=frozenset({AccessClass.HOLDOUT_SINCE_2024})),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        # 研究履歴と封印の区分を混ぜて要求する。
        (
            (RESEARCH_DEFINITION,),
            replace(
                REQUEST,
                classes=frozenset({AccessClass.RESEARCH_HISTORY, AccessClass.HOLDOUT_SINCE_2024}),
            ),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        # 封印の定義で研究履歴の区分を要求する（逆）。
        (
            (SEALED_DEFINITION,),
            replace(SEALED_REQUEST, classes=RESEARCH_QUALITY_CLASSES),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        # 封印の定義で研究期間の置き場の原ファイルを要求する。
        (
            (SEALED_DEFINITION,),
            replace(SEALED_REQUEST, targets=(RAW,)),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        # 期間が 2024-01-01 以降を含む・研究履歴の始まりより前から。
        (
            (RESEARCH_DEFINITION,),
            replace(REQUEST, period=AccessPeriod(start=T("2017-01-01T00:00:00Z"), end=None)),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        (
            (RESEARCH_DEFINITION,),
            replace(
                REQUEST,
                period=AccessPeriod(start=T("2017-01-01T00:00:00Z"), end=T("2024-01-08T00:00:00Z")),
            ),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        (
            (RESEARCH_DEFINITION,),
            replace(
                REQUEST,
                period=AccessPeriod(start=T("2015-12-31T00:00:00Z"), end=T("2024-01-01T00:00:00Z")),
            ),
            QualityPermitRefusal.OUTSIDE_DEFINITION,
        ),
        # 研究履歴の定義で受入れ（ACCEPTANCE）を要求する。
        (
            (RESEARCH_DEFINITION,),
            replace(REQUEST, check_kind=QualityCheckKind.ACCEPTANCE),
            QualityPermitRefusal.DEFINITION_MISMATCH,
        ),
        # 研究履歴の定義で時刻（TIMES）の出力を要求する。
        (
            (RESEARCH_DEFINITION,),
            replace(REQUEST, output_kinds=frozenset({QualityOutputKind.TIMES})),
            QualityPermitRefusal.DEFINITION_MISMATCH,
        ),
        # 隔離の区分はどの定義でも覆わない。
        (
            (RESEARCH_DEFINITION, SEALED_DEFINITION),
            replace(REQUEST, classes=frozenset({AccessClass.QUARANTINED_UNASSIGNED})),
            QualityPermitRefusal.QUARANTINED,
        ),
    ],
)
def test_a_request_off_the_research_definition_gets_no_permit_and_no_row(
    tmp_path: Path,
    definitions: tuple[QualityCheckDefinition, ...],
    request_: QualityCheckRequest,
    kind: QualityPermitRefusal,
) -> None:
    root = _root(tmp_path)
    refused = _begin(_gate(root, *definitions), request_)
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is kind
    assert (root / QUALITY_ACCESS_LOG_PATH).read_bytes() == b""


def test_a_research_definition_never_covers_a_partition(tmp_path: Path) -> None:
    week = T("2019-03-03T22:00:00Z")
    pid = PartitionId(
        series=market.series(timeframe_id="1h"), access_class=AccessClass.RESEARCH_HISTORY
    )
    target = PartitionTarget(partition_id=pid, interval=_hour(week))
    root = _root(tmp_path)
    refused = _begin(_gate(root), replace(REQUEST, targets=(target,)))
    assert isinstance(refused, QualityReadRefused)
    assert refused.kind is QualityPermitRefusal.OUTSIDE_DEFINITION


# --- (c) 混ぜた定義・隔離の区分を含む定義・研究履歴の規則に外れる定義は作れない ----------------


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        (
            {"classes": frozenset({AccessClass.RESEARCH_HISTORY, AccessClass.HOLDOUT_SINCE_2024})},
            "research-history class alone or",
        ),
        (
            {
                "classes": frozenset(
                    {AccessClass.RESEARCH_HISTORY, AccessClass.QUARANTINED_UNASSIGNED}
                )
            },
            "research-history class alone or",
        ),
        ({"check_kind": QualityCheckKind.ACCEPTANCE}, "RAW_FILE_VERIFICATION"),
        ({"check_kind": QualityCheckKind.GAP_LIST}, "RAW_FILE_VERIFICATION"),
        (
            {"period": AccessPeriod(start=T("2016-01-01T00:00:00Z"), end=None)},
            "must lie inside",
        ),
        (
            {
                "period": AccessPeriod(
                    start=T("2016-01-01T00:00:00Z"), end=T("2024-01-02T00:00:00Z")
                )
            },
            "must lie inside",
        ),
        (
            {
                "period": AccessPeriod(
                    start=T("2015-01-01T00:00:00Z"), end=T("2024-01-01T00:00:00Z")
                )
            },
            "must lie inside",
        ),
        ({"target_kind": QualityTargetKind.PARTITION}, "RAW_FILE"),
        ({"output_kinds": frozenset({QualityOutputKind.TIMES})}, "ADR-0040"),
        ({"output_kinds": frozenset({QualityOutputKind.INTERVALS})}, "ADR-0040"),
        ({"symbols": frozenset({Symbol("NZDUSD")})}, "8 currencies"),
        ({"datasource": "legacy_merged_csv_v3"}, "OANDA MT5"),
    ],
)
def test_a_research_definition_outside_adr_0040_cannot_be_built(
    changes: dict[str, object], match: str
) -> None:
    with pytest.raises(EvaluationError, match=match):
        replace(RESEARCH_DEFINITION, **changes)  # type: ignore[arg-type]


def test_a_sealed_definition_never_takes_the_verification_kind() -> None:
    with pytest.raises(EvaluationError, match="acceptance of the sealed intake"):
        replace(SEALED_DEFINITION, check_kind=QualityCheckKind.RAW_FILE_VERIFICATION)


RESEARCH_YAML = """schema_version: 1
tool: {name: oanda_mt5_research_raw_verify, version: "1"}
config_digest: %s
datasource: oanda_mt5_m1
check_kind: RAW_FILE_VERIFICATION
classes: [RESEARCH_HISTORY]
period: {start: "2016-01-01T00:00:00Z", end: "2024-01-01T00:00:00Z"}
targets:
  kind: RAW_FILE
  symbols: [USDJPY, USDCHF, AUDJPY, EURUSD, EURJPY, GBPJPY, GBPUSD, AUDUSD]
  timeframes: [1m]
output_kinds: [COUNTS, HASHES, PASS_FAIL]
""" % ("c" * 64)


def test_the_research_definition_file_of_d03_17_5_loads(tmp_path: Path) -> None:
    path = tmp_path / "oanda_mt5_research_raw_verify.yaml"
    path.write_text(RESEARCH_YAML, encoding="utf-8")
    definition = load_quality_check_definition(path)
    assert definition.is_research and definition.raw_root == ("data", "raw", "oanda_mt5")
    assert definition.period == RESEARCH_PERIOD
    assert len(definition.symbols) == 8


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("classes: [RESEARCH_HISTORY]", "classes: [RESEARCH_HISTORY, HOLDOUT_SINCE_2024]"),
        ("classes: [RESEARCH_HISTORY]", "classes: [RESEARCH_HISTORY, QUARANTINED_UNASSIGNED]"),
        ("check_kind: RAW_FILE_VERIFICATION", "check_kind: ACCEPTANCE"),
        ('end: "2024-01-01T00:00:00Z"', 'end: "2024-02-01T00:00:00Z"'),
        ('end: "2024-01-01T00:00:00Z"', "end: null"),
        ("kind: RAW_FILE", "kind: PARTITION"),
        ("  timeframes: [1m]", "  timeframes: [M1]"),
        ("[COUNTS, HASHES, PASS_FAIL]", "[COUNTS, HASHES, PASS_FAIL, TIMES]"),
    ],
)
def test_a_research_definition_file_off_the_rules_is_a_config_error(
    tmp_path: Path, old: str, new: str
) -> None:
    path = tmp_path / "oanda_mt5_research_raw_verify.yaml"
    path.write_text(RESEARCH_YAML.replace(old, new), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_quality_check_definition(path)


# --- (e) 2 種類の定義を並べても、それぞれの要求はそれぞれの定義で通る ----------------------------


def test_sealed_and_research_definitions_side_by_side_each_permit_their_own_request(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    gate = _gate(root, SEALED_DEFINITION, RESEARCH_DEFINITION)
    research = _begin(gate)
    assert isinstance(research, QualityReadPermit)
    sealed = gate.begin_quality_read(
        SEALED_REQUEST,
        permit_id="p2",
        access_id="a2",
        row_id="r2",
        accessed_at=T("2026-11-04T00:00:00Z"),
    )
    assert isinstance(sealed, QualityReadPermit)
    assert sealed.classes == SEALED_REQUEST.classes
    # 道具の名前と版は定義ごとに違うので、研究の道具で封印の原ファイルは要求できない。
    crossed = _begin(gate, replace(REQUEST, targets=(SEALED_RAW,)))
    assert isinstance(crossed, QualityReadRefused)


# --- 読み取りの許可の値・閲覧の記録の行・違反の範囲 ------------------------------------------


def test_a_research_read_permit_outputs_only_counts_hashes_and_pass_fail() -> None:
    permit = QualityReadPermit(
        permit_id="p",
        access_id="a",
        tool=RESEARCH_TOOL,
        classes=RESEARCH_QUALITY_CLASSES,
        period=RESEARCH_PERIOD,
        targets=(RAW,),
        output_kinds=RESEARCH_VERIFY_OUTPUT_KINDS,
    )
    assert permit.covers_raw_file(RAW.location, RAW.sha256 or "")
    with pytest.raises(MarketDataValueError, match="counts, hashes and pass/fail"):
        replace(permit, output_kinds=frozenset({QualityOutputKind.TIMES}))
    with pytest.raises(MarketDataValueError, match="outside the allowed set"):
        replace(
            permit,
            classes=frozenset({AccessClass.RESEARCH_HISTORY, AccessClass.HOLDOUT_SINCE_2024}),
        )


def _start(**changes: object) -> QualityAccessStart:
    base = QualityAccessStart(
        row_id="r1",
        access_id="a1",
        accessed_at=T("2026-11-04T00:00:00Z"),
        tool=RESEARCH_TOOL,
        classes=RESEARCH_QUALITY_CLASSES,
        period=RESEARCH_PERIOD,
        output_kinds=RESEARCH_VERIFY_OUTPUT_KINDS,
        permit_id="p1",
        targets=(RAW,),
        check_kind=QualityCheckKind.RAW_FILE_VERIFICATION,
        record_kind=QualityRecordKind.LIVE,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_a_research_access_row_is_only_a_verification_read_of_raw_files() -> None:
    _start()
    with pytest.raises(MarketDataValueError, match="verification read"):
        _start(check_kind=QualityCheckKind.ACCEPTANCE)
    pid = PartitionId(
        series=market.series(timeframe_id="1h"), access_class=AccessClass.RESEARCH_HISTORY
    )
    week = T("2019-03-03T22:00:00Z")
    with pytest.raises(MarketDataValueError, match="verification read"):
        _start(targets=(PartitionTarget(partition_id=pid, interval=_hour(week)),))
    with pytest.raises(MarketDataValueError, match="outside the allowed set"):
        _start(classes=frozenset({AccessClass.RESEARCH_HISTORY, AccessClass.FORWARD_HOLDOUT}))


def test_a_research_access_is_never_turned_into_a_violation_scope_silently() -> None:
    """研究履歴の閲覧は違反の範囲に入れない。判定の行があれば扱いを決めずに止める（A1-6・A1-10）。"""
    start = _start()
    end = QualityAccessEnd(
        row_id="r2",
        access_id="a1",
        finished_at=T("2026-11-04T01:00:00Z"),
        result=QualityAccessResult.STOPPED,
        record_kind=QualityRecordKind.LIVE,
    )
    assert violation_scopes((start, end)) == ()
    judgment = QualityAccessJudgment(
        row_id="r3",
        access_id="a1",
        violated_conditions=frozenset({1}),
        judged_at=T("2026-11-05T00:00:00Z"),
        judged_by="ShinIwasaki7",
    )
    with pytest.raises(MarketDataValueError, match="decided by a human"):
        violation_scopes((start, end, judgment))
