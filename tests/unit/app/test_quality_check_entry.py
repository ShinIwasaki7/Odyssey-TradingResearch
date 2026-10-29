"""品質確認専用の入口・許可の定義と窓の設定の読込・関所の外の経路が無いこと（ADR-0036 第5項・
第7項・第9項、ADR-0037 第4項、ADR-0038 第1項、D11 §10 の受入条件 25）。

人工の設定と一時ディレクトリだけを使う。窓の設定の取得の開始時点は人工の値（S4a で実際の値を
書く）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import odyssey_fx.app.composition as composition
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.quality_checks import (
    QUALITY_CHECKS_DIRECTORY,
    load_quality_check_definitions,
)
from odyssey_fx.app.config.sealed_window import load_sealed_window
from odyssey_fx.app.quality_check import QualityCheckRefused, run_quality_check
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.quality_permit import QualityCheckRequest, QualityPermitRefusal
from odyssey_fx.marketdata.adapters.quality_access_log_store import (
    QUALITY_ACCESS_LOCK_PATH,
    QUALITY_ACCESS_LOG_PATH,
    QualityAccessLogFile,
)
from odyssey_fx.marketdata.application.snapshot_access import VerifiedPartitionBars
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.errors import AcquisitionStartUnset, HoldoutAccessViolation
from odyssey_fx.marketdata.domain.quality_access import (
    AccessPeriod,
    QualityAccessEnd,
    QualityAccessLogContents,
    QualityAccessResult,
    QualityAccessStart,
    QualityCheckKind,
    QualityOutputKind,
    QualityReadPermit,
    RawFileTarget,
    ToolRef,
)
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.synthetic import market, snapshots

T = UtcTime.parse
SRC = Path(__file__).resolve().parents[3] / "src" / "odyssey_fx"
DIGEST = "c" * 64
DEFINITION_YAML = f"""schema_version: 1
tool: {{name: accept_mt5_sealed, version: "3"}}
config_digest: {DIGEST}
datasource: oanda_mt5_m1
check_kind: ACCEPTANCE
classes: [HOLDOUT_SINCE_2024, FORWARD_HOLDOUT]
period: {{start: "2024-01-01T00:00:00Z", end: null}}
targets: {{kind: RAW_FILE, symbols: [USDJPY], timeframes: [1m]}}
output_kinds: [COUNTS, HASHES, INTERVALS, PASS_FAIL, TIMES]
"""
RAW = RawFileTarget(
    location="data/raw/oanda_mt5_sealed/20301101/USDJPY/2024-01.csv.gz",
    sha256="d" * 64,
    symbol=Symbol("USDJPY"),
)
REQUEST = QualityCheckRequest(
    tool=ToolRef(name="accept_mt5_sealed", version="3"),
    config_digest=DIGEST,
    check_kind=QualityCheckKind.ACCEPTANCE,
    classes=frozenset({AccessClass.HOLDOUT_SINCE_2024}),
    period=AccessPeriod(start=T("2024-01-01T00:00:00Z"), end=None),
    targets=(RAW,),
    output_kinds=frozenset({QualityOutputKind.COUNTS}),
)


def _repo(tmp_path: Path, *, definition: str | None = DEFINITION_YAML) -> Path:
    (tmp_path / "research").mkdir()
    (tmp_path / QUALITY_ACCESS_LOG_PATH).write_bytes(b"")
    if definition is not None:
        directory = tmp_path / QUALITY_CHECKS_DIRECTORY
        directory.mkdir(parents=True)
        (directory / "accept_mt5_sealed.yaml").write_text(definition, encoding="utf-8")
    return tmp_path


def _ids() -> object:
    counter = iter(range(100))
    return lambda: f"id{next(counter)}"


def _rows(root: Path) -> tuple[object, ...]:
    contents = QualityAccessLogFile(root).read()
    assert isinstance(contents, QualityAccessLogContents)
    return contents.rows


def _run(root: Path, tool: object) -> object:
    return run_quality_check(
        repository_root=root,
        request=REQUEST,
        tool=tool,  # type: ignore[arg-type]
        now=lambda: T("2030-11-05T00:00:00Z"),
        new_id=_ids(),  # type: ignore[arg-type]
    )


# --- 入口の手順（ADR-0036 第7項）--------------------------------------------------------


def test_the_tool_runs_under_the_permit_between_the_start_and_end_rows(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    seen: list[QualityReadPermit] = []

    def tool(permit: QualityReadPermit) -> int:
        assert isinstance(_rows(root)[0], QualityAccessStart)  # 開始の行は読み取りの前
        seen.append(permit)
        return 7

    assert _run(root, tool) == 7
    start, end = _rows(root)
    assert isinstance(start, QualityAccessStart) and isinstance(end, QualityAccessEnd)
    assert seen[0].access_id == start.access_id == end.access_id
    assert end.result is QualityAccessResult.COMPLETED


def test_without_an_approved_definition_the_tool_is_never_called(tmp_path: Path) -> None:
    root = _repo(tmp_path, definition=None)
    calls: list[object] = []
    with pytest.raises(QualityCheckRefused) as caught:
        _run(root, calls.append)
    assert caught.value.refusal.kind is QualityPermitRefusal.NO_DEFINITION
    assert calls == []
    assert _rows(root) == ()


def test_a_stopped_tool_leaves_a_stopped_end_row_and_raises(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    def tool(permit: QualityReadPermit) -> int:
        raise RuntimeError("stopped")

    with pytest.raises(RuntimeError, match="stopped"):
        _run(root, tool)
    end = _rows(root)[1]
    assert isinstance(end, QualityAccessEnd) and end.result is QualityAccessResult.STOPPED


def test_no_result_is_returned_when_the_end_row_cannot_be_written(tmp_path: Path) -> None:
    root = _repo(tmp_path)

    def tool(permit: QualityReadPermit) -> int:
        (root / QUALITY_ACCESS_LOCK_PATH).write_text("held\n")
        return 7

    with pytest.raises(QualityCheckRefused) as caught:
        _run(root, tool)
    assert caught.value.refusal.kind is QualityPermitRefusal.RECORD_FAILED
    assert len(_rows(root)) == 1  # 開始の行だけ（結果不明）


# --- 許可の定義の読込（ADR-0036 第4項）---------------------------------------------------


def test_a_missing_definition_directory_means_no_permit(tmp_path: Path) -> None:
    assert load_quality_check_definitions(tmp_path / QUALITY_CHECKS_DIRECTORY) == ()


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("datasource: oanda_mt5_m1", "datasource: legacy_merged_csv_v3"),
        ("check_kind: ACCEPTANCE", "check_kind: GAP_LIST"),
        ("[HOLDOUT_SINCE_2024, FORWARD_HOLDOUT]", "[QUARANTINED_UNASSIGNED]"),
        ("[COUNTS, HASHES, INTERVALS, PASS_FAIL, TIMES]", "[COUNTS, EVIDENCE]"),
        ("symbols: [USDJPY]", "symbols: [USDJPY, USDJPY]"),
        ("name: accept_mt5_sealed", "name: other_tool"),
    ],
)
def test_a_definition_outside_the_allowed_scope_is_a_config_error(
    tmp_path: Path, old: str, new: str
) -> None:
    root = _repo(tmp_path, definition=DEFINITION_YAML.replace(old, new))
    with pytest.raises(ConfigError):
        load_quality_check_definitions(root / QUALITY_CHECKS_DIRECTORY)


# --- 窓の設定（ADR-0037 第4項、ADR-0038 第1項）-------------------------------------------

WINDOW_YAML = """schema_version: 1
decision: ADR-0037
datasource: oanda_mt5_m1
price_basis: bid
timeframe: M1
symbols: [USDJPY, USDCHF, AUDJPY, EURUSD, EURJPY, GBPJPY, GBPUSD, AUDUSD]
allowed_start: "2024-01-01T00:00:00Z"
acquisition_start: null
acquisition_start_basis:
  merge_commit: null
  merge_commit_time: null
"""


def _with_start(text: str, start: str, *, merged: str, commit: str = "a" * 40) -> str:
    return text.replace("acquisition_start: null", f'acquisition_start: "{start}"').replace(
        "  merge_commit: null\n  merge_commit_time: null",
        f'  merge_commit: {commit}\n  merge_commit_time: "{merged}"',
    )


def _window(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "window.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_empty_acquisition_start_classifies_nothing_from_2024(tmp_path: Path) -> None:
    window = load_sealed_window(_window(tmp_path, WINDOW_YAML))
    assert window.acquisition_start is None
    with pytest.raises(AcquisitionStartUnset):
        window.boundaries.classify(T("2024-02-01T00:00:00Z"), T("2024-02-01T00:01:00Z"))


def test_the_acquisition_start_is_read_from_the_window(tmp_path: Path) -> None:
    # 人工の値（2030-11-03 は日曜。標準時なので 17:00 は 22:00Z）。
    text = _with_start(WINDOW_YAML, "2030-11-03T22:00:00Z", merged="2030-10-30T00:00:00Z")
    window = load_sealed_window(_window(tmp_path, text))
    assert window.boundaries.acquisition_start == T("2030-11-03T22:00:00Z")
    assert (
        window.boundaries.classify(T("2030-11-03T22:00:00Z"), T("2030-11-03T22:01:00Z"))
        is AccessClass.FORWARD_HOLDOUT
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('allowed_start: "2024-01-01T00:00:00Z"', 'allowed_start: "2023-12-31T00:00:00Z"'),
        ("acquisition_start: null", 'acquisition_start: "2030-11-04T22:00:00Z"'),
        # 取得の開始時点だけを書いて根拠を書かない。
        ("acquisition_start: null", 'acquisition_start: "2030-11-03T22:00:00Z"'),
        # 根拠だけを書いて取得の開始時点を書かない。
        ("  merge_commit: null", "  merge_commit: " + "a" * 40),
        ("symbols: [USDJPY,", "symbols: [USDCAD,"),
        ("timeframe: M1", "timeframe: M5"),
        ("decision: ADR-0037", "decision: ADR-0014"),
    ],
)
def test_a_window_outside_the_revised_constraint_is_a_config_error(
    tmp_path: Path, old: str, new: str
) -> None:
    with pytest.raises(ConfigError):
        load_sealed_window(_window(tmp_path, WINDOW_YAML.replace(old, new)))


def test_a_missing_window_file_stops(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_sealed_window(tmp_path / "absent.yaml")


# --- 関所の外の経路が無いこと（ADR-0036 第5項、受入条件 25）--------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize(
    "relative",
    [
        "app/quality_check.py",
        "evaluation/application/holdout_gate.py",
        "evaluation/domain/quality_permit.py",
        "app/config/quality_checks.py",
    ],
)
def test_the_quality_path_never_assembles_a_backtest_or_a_strategy(relative: str) -> None:
    modules = _imported_modules(SRC / relative)
    forbidden = sorted(
        module
        for module in modules
        if module.startswith(("odyssey_fx.backtest", "odyssey_fx.strategy"))
        or module == "odyssey_fx.app.composition"
    )
    assert forbidden == []


def test_importing_the_quality_entry_never_loads_a_strategy_runner_or_a_run_assembler() -> None:
    """間接の import も含めて、戦略を動かす・run を組み立てるモジュールが読み込まれない。

    S3 の残 P2 の 1。関所のポートの定義を通して `backtest`・`strategy` の型のモジュールは読み込ま
    れる（入口の説明文に書いたとおり）が、実行のモジュールは読み込まれないことを、別のプロセスの
    `sys.modules` で確かめる。
    """
    import subprocess
    import sys

    code = "import sys, odyssey_fx.app.quality_check\nprint('\\n'.join(sorted(sys.modules)))\n"
    loaded = subprocess.run(
        [sys.executable, "-c", code], check=True, capture_output=True, text=True
    ).stdout.split()
    runners = (
        "odyssey_fx.app.composition",
        "odyssey_fx.backtest.engine",
        "odyssey_fx.backtest.application",
        "odyssey_fx.strategy.runtime",
        "odyssey_fx.evaluation.application.run_experiment",
        "odyssey_fx.evaluation.application.evaluate_run",
    )
    assert sorted(name for name in loaded if name.startswith(runners)) == []
    # 説明文のとおり、型のモジュールは間接に読み込まれる（検査が空振りしていないことの確かめ）。
    assert "odyssey_fx.app.quality_check" in loaded
    assert "odyssey_fx.evaluation.application.ports" in loaded


def test_no_run_or_experiment_entry_accepts_a_quality_permit() -> None:
    quality_types = ("QualityReadPermit", "QualityCheckRequest", "QualityCheckDefinition")
    for name, member in inspect.getmembers(composition, inspect.isfunction):
        if member.__module__ != composition.__name__:
            continue
        annotations = " ".join(str(value) for value in member.__annotations__.values())
        assert not any(kind in annotations for kind in quality_types), name
    for name, cls in inspect.getmembers(composition, inspect.isclass):
        if cls.__module__ != composition.__name__:
            continue
        for _, function in inspect.getmembers(cls, inspect.isfunction):
            annotations = " ".join(str(value) for value in function.__annotations__.values())
            assert not any(kind in annotations for kind in quality_types), name


def test_a_definition_spelling_the_timeframe_as_m1_is_refused_on_load(tmp_path: Path) -> None:
    """時間足は識別（1m）で書く。M1 の綴りは照合で必ず外れるので読込で止める（S3 の残 P2 の 2）。"""
    spelled = DEFINITION_YAML.replace("timeframes: [1m]", "timeframes: [M1]")
    root = _repo(tmp_path, definition=spelled)
    with pytest.raises(ConfigError, match="時間足の定義の識別"):
        load_quality_check_definitions(root / QUALITY_CHECKS_DIRECTORY)


def test_a_sealed_partition_can_never_be_granted_to_an_as_of_view() -> None:
    bars = {
        PartitionId(series=market.series(), access_class=AccessClass.RESEARCH_HISTORY): (),
    }
    snapshot = snapshots.readable_for(bars)
    sealed = PartitionId(
        series=market.series(timeframe_id="1m"),
        access_class=AccessClass.HOLDOUT_SINCE_2024,
        period_start=T("2024-01-07T22:00:00Z"),
    )
    with pytest.raises(HoldoutAccessViolation, match="sealed partitions from 2024"):
        VerifiedPartitionBars(snapshot, frozenset({sealed}), bars, label="AsOfView")
