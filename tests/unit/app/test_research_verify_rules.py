"""研究期間の確認の読み取りと確認済みハッシュの一覧の規則（D03 §17.3・§17.5・§17.6、順 7 の A2）。

版管理の規則のファイル（時刻の規則の表 版 1・承認済みの一覧・列対応の宣言）の SHA-256 だけを読む
（原データ・封印データは読まない）。一覧は人工の YAML。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.quality_checks import (
    QUALITY_CHECKS_DIRECTORY,
    load_quality_check_definition,
)
from odyssey_fx.app.config.verified_raw import load_verified_raw_list, verified_raw_path
from odyssey_fx.app.research_raw_verify import (
    RESEARCH_CONFIG_FILES,
    RESEARCH_VERIFY_TOOL,
    research_config_digest,
)
from odyssey_fx.app.violation_judgment import ViolationJudgmentStopped, plan_judgment
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.quality_permit import RESEARCH_PERIOD, QualityTargetKind
from odyssey_fx.marketdata.adapters.quality_access_log_store import (
    QUALITY_ACCESS_LOG_PATH,
    QualityAccessLogFile,
)
from odyssey_fx.marketdata.domain.quality_access import (
    RESEARCH_QUALITY_CLASSES,
    RESEARCH_VERIFY_OUTPUT_KINDS,
    QualityAccessStart,
    QualityCheckKind,
    QualityLogAppendRefused,
    QualityRecordKind,
    RawFileTarget,
)

ROOT = Path(__file__).resolve().parents[3]
T = UtcTime.parse

#: 今の固定のファイルから計算した研究期間用の許可の定義の設定のダイジェスト（D03 §17.5 の
#: `config_digest`。A1-9）。研究期間用の許可の定義（D17 の AP）はこの値を書く。固定のファイルの
#: どれか 1 バイトでも変われば値が変わり、このテストが落ちる（定義を直して人間が承認し直す）。
RESEARCH_DIGEST_OF_TODAY = "ca8e3ecbde13b686d90ba23e2f3138fad127e7a3d3ecb405898497cc13ab32ad"


def test_the_research_config_digest_covers_the_three_fixed_files_of_today() -> None:
    assert [path.as_posix() for path in RESEARCH_CONFIG_FILES] == [
        "docs/design/time_rules/time_rule_table_v1.yaml",
        "docs/design/time_rules/approved_time_rule_tables.yaml",
        "configs/datasources/oanda_mt5_m1_v1.yaml",
    ]
    assert research_config_digest(ROOT) == RESEARCH_DIGEST_OF_TODAY


def test_the_research_definition_file_matches_the_fixed_files_once_it_is_approved() -> None:
    """研究期間用の許可の定義（D03 §17.5）は D17 の AP で人間が merge して置く。

    AP の merge の前はファイルが無いので照合できない（A2 の PR 本文の仮置きに「AP の merge 後に
    照合」と書いた。合格の扱いにしない）。merge の後は、設定のダイジェストと項目を照合する。
    """
    path = ROOT / QUALITY_CHECKS_DIRECTORY / f"{RESEARCH_VERIFY_TOOL.name}.yaml"
    if not path.exists():
        pytest.skip("研究期間用の許可の定義は D17 の AP の merge で置かれる（照合は AP の後）")
    definition = load_quality_check_definition(path)
    assert definition.tool == RESEARCH_VERIFY_TOOL
    assert definition.config_digest == research_config_digest(ROOT)
    assert definition.check_kind is QualityCheckKind.RAW_FILE_VERIFICATION
    assert definition.classes == RESEARCH_QUALITY_CLASSES
    assert definition.period == RESEARCH_PERIOD
    assert definition.target_kind is QualityTargetKind.RAW_FILE
    assert definition.output_kinds == RESEARCH_VERIFY_OUTPUT_KINDS
    assert len(definition.symbols) == 8


# --- 確認済みハッシュの一覧の読込（D03 §17.3 の 4） ---------------------------------------------

FETCH_DATE = "2026-10-16"
LOCATION = f"data/raw/oanda_mt5/{FETCH_DATE}/EURJPY/EURJPY_2017-01_m1.csv.gz"
OTHER = f"data/raw/oanda_mt5/{FETCH_DATE}/USDJPY/USDJPY_2017-01_m1.csv.gz"
LIST_YAML = f"""schema_version: 1
id: oanda_mt5_m1_verified_raw
version: 1
datasource: oanda_mt5_m1
fetch_date: "{FETCH_DATE}"
fetch_manifest_sha256: {"e" * 64}
verified_by: {{name: oanda_mt5_research_raw_verify, version: "1"}}
permit_definition_sha256: {"f" * 64}
declared_period_end: "2024-01-01T00:00:00Z"
files:
  - {{location: {LOCATION}, symbol: EURJPY, sha256: {"a" * 64}, access_id: acc1, result: PASS}}
  - {{location: {OTHER}, symbol: USDJPY, sha256: {"b" * 64}, access_id: acc2, result: PASS}}
"""


def _load(tmp_path: Path, text: str = LIST_YAML, fetch_date: str = FETCH_DATE) -> object:
    path = tmp_path / verified_raw_path(FETCH_DATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return load_verified_raw_list(path, fetch_date=fetch_date)


def test_the_list_of_d03_17_3_loads_and_answers_by_canonical_location(tmp_path: Path) -> None:
    verified = _load(tmp_path)
    assert verified.sha256_of(LOCATION) == "a" * 64  # type: ignore[attr-defined]
    assert verified.sha256_of(LOCATION.replace("EURJPY_", "x")) is None  # type: ignore[attr-defined]
    assert verified_raw_path(FETCH_DATE).as_posix() == (
        "configs/datasources/oanda_mt5_m1_verified_raw_2026-10-16.yaml"
    )


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            'declared_period_end: "2024-01-01T00:00:00Z"',
            'declared_period_end: "2024-01-01T00:00:01Z"',
            "declared period end",
        ),
        ("result: PASS}\n  - ", "result: FAIL}\n  - ", "PASS"),
        ("symbol: USDJPY", "symbol: NZDUSD", "8 通貨の外"),
        (f"sha256: {'a' * 64}", f"sha256: {'A' * 64}", "64 lowercase hex"),
        (LOCATION, LOCATION.replace("EURJPY/EURJPY", "USDJPY/EURJPY"), "directory of its symbol"),
        (
            LOCATION,
            LOCATION.replace("data/raw/oanda_mt5/", "data/raw/oanda_mt5_sealed/"),
            "data/raw/oanda_mt5/<date>",
        ),
        (
            LOCATION,
            LOCATION.replace("data/raw/oanda_mt5/", "data/raw/oanda_mt5x/"),
            "data/raw/oanda_mt5/<date>",
        ),
        (
            LOCATION,
            "data/raw/oanda_mt5/../oanda_mt5/x/EURJPY/a.csv.gz",
            "data/raw/oanda_mt5/<date>",
        ),
        (LOCATION, "/" + LOCATION, "data/raw/oanda_mt5/<date>"),
        (LOCATION, LOCATION.replace("2026-10-16", "2026-10-17"), "fetch date"),
        (
            f"{OTHER}, symbol: USDJPY",
            f"{LOCATION}, symbol: EURJPY",
            "appears twice",
        ),
        ("id: oanda_mt5_m1_verified_raw", "id: other", "`id`"),
        ("datasource: oanda_mt5_m1", "datasource: legacy", "`datasource`"),
        (f'fetch_date: "{FETCH_DATE}"', "fetch_date: 2026-10-16", "fetch_date"),
        (
            f"permit_definition_sha256: {'f' * 64}",
            "permit_definition_sha256: x",
            "permit_definition",
        ),
        ('verified_by: {name: oanda_mt5_research_raw_verify, version: "1"}\n', "", "verified_by"),
    ],
)
def test_a_list_off_its_load_checks_is_refused_as_a_whole(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    assert old in LIST_YAML
    with pytest.raises(ConfigError, match=match):
        _load(tmp_path, LIST_YAML.replace(old, new, 1))


def test_a_list_of_another_fetch_date_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="fetch_date"):
        _load(tmp_path, fetch_date="2026-10-17")


# --- 後からの違反の判定は研究履歴の閲覧の扱いを決めない（D03 §17.6 の 4。A1-6・A1-10） ------------


def test_the_violation_command_stops_on_a_research_history_access(tmp_path: Path) -> None:
    (tmp_path / "research").mkdir()
    (tmp_path / QUALITY_ACCESS_LOG_PATH).write_bytes(b"")
    start = QualityAccessStart(
        row_id="r1",
        access_id="acc1",
        accessed_at=T("2026-11-04T00:00:00Z"),
        tool=RESEARCH_VERIFY_TOOL,
        classes=RESEARCH_QUALITY_CLASSES,
        period=RESEARCH_PERIOD,
        output_kinds=RESEARCH_VERIFY_OUTPUT_KINDS,
        permit_id="p1",
        targets=(RawFileTarget(location=LOCATION, sha256="a" * 64, symbol=Symbol("EURJPY")),),
        check_kind=QualityCheckKind.RAW_FILE_VERIFICATION,
        record_kind=QualityRecordKind.LIVE,
    )
    assert not isinstance(QualityAccessLogFile(tmp_path).append(start), QualityLogAppendRefused)
    before = (tmp_path / QUALITY_ACCESS_LOG_PATH).read_bytes()
    with pytest.raises(ViolationJudgmentStopped) as stopped:
        plan_judgment(
            repo_root=tmp_path,
            snapshots_root=tmp_path / "data" / "snapshots",
            access_id="acc1",
            conditions=frozenset({1}),
            judged_by="ShinIwasaki7",
            judged_at=T("2026-11-05T00:00:00Z"),
        )
    assert stopped.value.stage == "research-history"
    assert (tmp_path / QUALITY_ACCESS_LOG_PATH).read_bytes() == before
