"""前向き検証の規則のファイル・登録簿の読込（D09 §9.11.12 の 1、受入テスト F-1・F-2。順 6 の F2）。

読むのは版管理の規則のファイル・登録簿・窓の設定ファイル（取得の開始時点の参照先）だけで、封印の
データ・成績・価格は読まない。誤りの例はすべて規則のファイルの文字列を書き換えた人工のもの。
"""

from __future__ import annotations

import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from odyssey_fx.app.config import ConfigError
from odyssey_fx.app.config.forward_validation import (
    candidate_fix_path,
    forward_validation_registry_path,
    forward_validation_rules_path,
    load_forward_validation_registry,
    load_forward_validation_rules,
    load_registered_forward_validation_rules,
)
from odyssey_fx.app.config.loader import load_yaml_mapping
from odyssey_fx.app.config.sealed_window import SEALED_WINDOW_PATH
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.forward_validation import (
    AcquisitionStartRef,
    DeferralExclusion,
    FinalWindowEnd,
    FixTimeRule,
    FixWeekTreatment,
    FrequencySource,
    IncompletePeriodRule,
    JudgmentMethod,
    JudgmentPolicyVersion,
    LengthMethod,
    MinTradesSource,
    OnBoundary,
    PairCombination,
    PairJudgment,
    PeriodEnd,
    Prerequisite,
    ReleaseCondition,
    StartBasis,
    UsableWindow,
    WarmupLength,
    WarmupRule,
    WeekBoundary,
)
from tests.fixtures.evaluation.adoption_review import REPO_ROOT

_RULES = forward_validation_rules_path(REPO_ROOT, 1)
_REGISTRY = forward_validation_registry_path(REPO_ROOT)
#: 版 1 の登録簿の値（F2 の PR で固定した）。
_V1_DIGEST = "ffa59802bc831f1b37cffb69d3f5c2de367a7bbb2b685c342f6241b0be4f8071"


def _text() -> str:
    return _RULES.read_text(encoding="utf-8")


def _replaced(old: str, new: str) -> str:
    text = _text()
    assert old in text, old
    return text.replace(old, new, 1)


def _repo(
    tmp_path: Path,
    *,
    rules_text: str | None = None,
    registry_text: str | None = None,
    with_sealed_window: bool = True,
) -> Path:
    """人工のリポジトリの根（規則のファイル・登録簿・窓の設定ファイルの写し）を作る。"""
    rules = forward_validation_rules_path(tmp_path, 1)
    rules.parent.mkdir(parents=True)
    rules.write_text(_text() if rules_text is None else rules_text, encoding="utf-8")
    registry = forward_validation_registry_path(tmp_path)
    registry.write_text(
        _REGISTRY.read_text(encoding="utf-8") if registry_text is None else registry_text,
        encoding="utf-8",
    )
    if with_sealed_window:
        target = tmp_path / SEALED_WINDOW_PATH
        target.parent.mkdir(parents=True)
        shutil.copyfile(REPO_ROOT / SEALED_WINDOW_PATH, target)
    return tmp_path


# --- F-1: 版 1 が登録簿と一致して読め、値が D09 §9.11.12 の 1 の表と同じ ---------------


def test_f1_the_committed_version_1_matches_its_registry_entry() -> None:
    loaded = load_registered_forward_validation_rules(REPO_ROOT, 1)
    (entry,) = load_forward_validation_registry(_REGISTRY)
    assert (entry.rules_id, entry.version, entry.digest) == ("forward_validation", 1, loaded.digest)
    assert loaded.entry == entry
    assert loaded.digest.hex == _V1_DIGEST
    assert entry.effective_after is None  # 版 1 は最初から効く
    # 取得の開始時点は参照先（窓の設定ファイル）の値（D15-10a (a)、ADR-0037 第4項）。
    assert loaded.acquisition_start == UtcTime.parse("2026-11-01T22:00:00Z")


def test_f1_the_version_1_values_are_the_decided_values() -> None:
    """値は D09 §9.11.12 の 1 の表の版 1 の値（決定 71〜77・仮置き F1-1〜F1-19）。"""
    rules = load_registered_forward_validation_rules(REPO_ROOT, 1).rules
    assert (rules.rules_id, rules.version) == ("forward_validation", 1)
    assert rules.acquisition_start_ref == AcquisitionStartRef(
        path="configs/acquisition/oanda_mt5_m1_sealed_window.yaml", key="acquisition_start"
    )
    assert rules.fix_time is FixTimeRule.FIRST_PARENT_FIRST_INCLUSION_COMMITTER_TIME
    assert rules.start.basis is StartBasis.FIX_TIME
    assert rules.start.week_boundary is WeekBoundary.NEW_YORK_SUNDAY_1700
    assert rules.start.on_boundary is OnBoundary.NEXT_BOUNDARY
    assert rules.final_window.start == UtcTime.parse("2024-01-01T00:00:00Z")
    assert rules.final_window.end is FinalWindowEnd.FIX_TIME
    assert rules.final_window.fix_week is FixWeekTreatment.EXCLUDED
    length = rules.length
    assert length.method is LengthMethod.POISSON
    assert length.probability == Decimal("0.95")
    assert length.min_days == 364
    assert length.min_trades_source is MinTradesSource.RESEARCH_POLICY_MIN_VALIDATION_TRADES_TOTAL
    assert length.frequency_source is FrequencySource.ADOPTION_REVIEW_FIXED_TRIAL_VALIDATION_FOLDS
    assert length.pair_combination is PairCombination.MAX
    assert length.precision_digits == 50
    assert rules.scoring.warmup is WarmupRule.LEADING_PART_OF_PERIOD
    assert (
        rules.scoring.warmup_length
        is WarmupLength.FINAL_VALIDATION_METHOD_ON_FORWARD_SNAPSHOT_CALENDAR
    )
    assert rules.scoring.period_end is PeriodEnd.FIRST_WEEK_BOUNDARY_AT_OR_AFTER_START_PLUS_LENGTH
    assert rules.fix_deferral.usable_window is UsableWindow.LATEST_CONTIGUOUS
    assert set(rules.fix_deferral.exclude) == set(DeferralExclusion)
    assert rules.judgment.method is JudgmentMethod.SAME_AS_FINAL_VALIDATION
    assert rules.judgment.research_policy is JudgmentPolicyVersion.FIXED_RECORD_VERSION
    assert rules.judgment.pair_combination is PairJudgment.D09_SECTION_7_12
    assert rules.prerequisite is Prerequisite.FINAL_VALIDATION_MEETS_BOTH_PAIRS
    assert set(rules.reservation_release) == set(ReleaseCondition)
    assert rules.incomplete_period is IncompletePeriodRule.STOP_WITHOUT_EXTENDING_OR_SHIFTING


def test_f1_the_rules_file_has_the_thirteen_items_of_the_design() -> None:
    """D09 §9.11.12 の 1 の表の 13 項目（`id` / `version` は 1 項目）がある（終了条件 E4）。"""
    keys = set(load_yaml_mapping(_RULES))
    assert keys == {
        "schema_version",
        "id",
        "version",
        "acquisition_start_ref",
        "fix_time",
        "start",
        "final_window",
        "length",
        "scoring",
        "fix_deferral",
        "judgment",
        "prerequisite",
        "reservation_release",
        "incomplete_period",
    }


def test_the_digest_ignores_how_a_value_is_written() -> None:
    _, original = load_forward_validation_rules(_RULES, version=1)
    for old, new in (
        ('probability: "0.95"', 'probability: "0.950"'),
        (
            "exclude: [EARLIER_CANDIDATES_PLANNED_FINAL_WINDOWS,"
            " OTHER_CANDIDATES_RESERVED_FORWARD_PERIODS]",
            "exclude: [OTHER_CANDIDATES_RESERVED_FORWARD_PERIODS,"
            " EARLIER_CANDIDATES_PLANNED_FINAL_WINDOWS]",
        ),
        ('start: "2024-01-01T00:00:00Z"', 'start: "2024-01-01T00:00:00.000Z"'),
    ):
        _, rewritten = load_forward_validation_rules(_RULES, version=1, text=_replaced(old, new))
        assert rewritten == original, new
    _, changed = load_forward_validation_rules(
        _RULES, version=1, text=_replaced('probability: "0.95"', 'probability: "0.9"')
    )
    assert changed != original


def test_the_fix_record_path_follows_the_design() -> None:
    """固定の記録の置き場（仮置き F1-2）。読込は要決定 F1-E の別の実装 PR。"""
    experiment_id = ExperimentId(digest("experiment"))
    path = candidate_fix_path(Path("/repo"), experiment_id, 2, 3)
    assert path == Path(f"/repo/research/candidate_fixes/{experiment_id}/2/3.yaml")


# --- F-2: 項目の欠け・知らない項目・型の誤り・範囲の外・登録簿に無い版・ダイジェストの不一致・
# 参照先の無い設定のどれでも読込が止まる ---------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        # 項目の欠け
        ("incomplete_period: STOP_WITHOUT_EXTENDING_OR_SHIFTING\n", ""),
        ("prerequisite: FINAL_VALIDATION_MEETS_BOTH_PAIRS\n", ""),
        ("  precision_digits: 50\n", ""),
        ("  fix_week: EXCLUDED\n", ""),
        ("  key: acquisition_start\n", ""),
        # 知らない項目
        ("prerequisite: ", "unknown_item: 1\nprerequisite: "),
        ("  min_days: 364\n", "  min_days: 364\n  max_days: 2000\n"),
        # 型の誤り
        ('probability: "0.95"', "probability: 0.95"),
        ("min_days: 364", 'min_days: "364"'),
        ("min_days: 364", "min_days: true"),
        ("precision_digits: 50", "precision_digits: 50.0"),
        ("prerequisite: FINAL_VALIDATION_MEETS_BOTH_PAIRS", "prerequisite: [X]"),
        ('probability: "0.95"', 'probability: "abc"'),
        ('start: "2024-01-01T00:00:00Z"', 'start: "2024-01-01"'),
        # 値の範囲の外
        ('probability: "0.95"', 'probability: "0"'),
        ('probability: "0.95"', 'probability: "1"'),
        ('probability: "0.95"', 'probability: "-0.5"'),
        ("min_days: 364", "min_days: 0"),
        ("precision_digits: 50", "precision_digits: 0"),
        ('start: "2024-01-01T00:00:00Z"', 'start: "2023-01-01T00:00:00Z"'),
        ("week_boundary: {zone: America/New_York,", "week_boundary: {zone: Europe/London,"),
        ('time: "17:00"', 'time: "17:30"'),
        ("weekday: SUNDAY", "weekday: MONDAY"),
        # 決定と違う規則（列挙の外・集合の一部だけ・重複）
        ("fix_time: FIRST_PARENT_FIRST_INCLUSION_COMMITTER_TIME", "fix_time: AUTHOR_TIME"),
        ("method: POISSON", "method: NORMAL"),
        ("warmup: LEADING_PART_OF_PERIOD", "warmup: RESEARCH_HISTORY_ONLY"),
        ("method: SAME_AS_FINAL_VALIDATION", "method: YEARLY_SEGMENTS"),
        ("pair_combination: MAX", "pair_combination: MIN"),
        ("  - FINAL_DECLARED_USED\n", ""),
        ("  - FINAL_DECLARED_USED\n", "  - FINAL_DECLARED_USED\n  - FINAL_DECLARED_USED\n"),
        (
            "exclude: [EARLIER_CANDIDATES_PLANNED_FINAL_WINDOWS,"
            " OTHER_CANDIDATES_RESERVED_FORWARD_PERIODS]",
            "exclude: [EARLIER_CANDIDATES_PLANNED_FINAL_WINDOWS]",
        ),
        # 参照の形の誤り
        (
            "path: configs/acquisition/oanda_mt5_m1_sealed_window.yaml",
            "path: ../configs/acquisition/oanda_mt5_m1_sealed_window.yaml",
        ),
        # id・版・形式版の食い違い
        ("id: forward_validation", "id: adoption_review"),
        ("\nversion: 1\n", "\nversion: 2\n"),
        ("schema_version: 1", "schema_version: 2"),
    ],
)
def test_f2_malformed_rules_files_are_rejected(old: str, new: str) -> None:
    with pytest.raises(ConfigError):
        load_forward_validation_rules(_RULES, version=1, text=_replaced(old, new))


def test_f2_a_missing_rules_file_is_a_read_error(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    forward_validation_rules_path(root, 1).unlink()
    with pytest.raises(ConfigError):
        load_registered_forward_validation_rules(root, 1)


def test_f2_an_unregistered_version_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(ConfigError, match="登録されていない"):
        load_registered_forward_validation_rules(root, 2)


def test_f2_a_digest_mismatch_is_rejected(tmp_path: Path) -> None:
    """版を上げずに中身を変えると、登録簿のダイジェストと違って止まる（決定と同じ範囲の値でも）。"""
    root = _repo(tmp_path, rules_text=_replaced('probability: "0.95"', 'probability: "0.9"'))
    with pytest.raises(ConfigError, match="登録簿の値"):
        load_registered_forward_validation_rules(root, 1)


def test_f2_a_missing_referenced_config_file_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path, with_sealed_window=False)
    with pytest.raises(ConfigError, match="参照先の設定ファイル"):
        load_registered_forward_validation_rules(root, 1)


def test_f2_a_missing_referenced_key_is_rejected(tmp_path: Path) -> None:
    """参照先のキーが無い（参照の書き誤り・参照先から消えた）ときに止まる。"""
    renamed = _replaced("key: acquisition_start", "key: acquisition_begin")
    with pytest.raises(ConfigError):
        load_registered_forward_validation_rules(_repo(tmp_path / "a", rules_text=renamed), 1)
    root = _repo(tmp_path / "b")
    target = root / SEALED_WINDOW_PATH
    text = target.read_text(encoding="utf-8")
    target.write_text(
        text.replace('acquisition_start: "2026-11-01T22:00:00Z"\n', ""), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_registered_forward_validation_rules(root, 1)


def test_f2_an_empty_referenced_value_is_rejected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    target = root / SEALED_WINDOW_PATH
    lines = [
        line for line in target.read_text(encoding="utf-8").splitlines() if not line.startswith("#")
    ]
    body = "\n".join(lines)
    for old, new in (
        ('acquisition_start: "2026-11-01T22:00:00Z"', "acquisition_start: null"),
        ("merge_commit: 7114c775b105e2be00d5f3deac2075d1dae7b90d", "merge_commit: null"),
        ('merge_commit_time: "2026-10-29T00:37:55Z"', "merge_commit_time: null"),
    ):
        assert old in body
        body = body.replace(old, new)
    target.write_text(body + "\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="空"):
        load_registered_forward_validation_rules(root, 1)


def _registry_text(body: str) -> str:
    return f"schema_version: 1\nentries:\n{body}"


_HEX_A = "a" * 64


def _entry(
    *,
    rules_id: str = "forward_validation",
    version: int = 1,
    hex_digest: str = _HEX_A,
    effective_after: str = "null",
    extra: str = "",
) -> str:
    return (
        f'  - {{id: {rules_id}, version: {version}, digest: "{hex_digest}",'
        f" effective_after: {effective_after}{extra}}}\n"
    )


@pytest.mark.parametrize(
    "body",
    [
        "",
        _entry(effective_after=f'"{_HEX_A}"'),
        _entry() + _entry(version=2),
        _entry(rules_id="adoption_review"),
        _entry() + _entry(hex_digest="b" * 64),
        _entry(hex_digest="short"),
        _entry(hex_digest="A" * 64),
        f'  - {{id: forward_validation, version: 1, digest: "{_HEX_A}"}}\n',
        _entry(extra=", note: x"),
    ],
)
def test_f2_malformed_registries_are_rejected(tmp_path: Path, body: str) -> None:
    root = _repo(tmp_path, registry_text=_registry_text(body))
    with pytest.raises(ConfigError):
        load_forward_validation_registry(forward_validation_registry_path(root))
    with pytest.raises(ConfigError):
        load_registered_forward_validation_rules(root, 1)


def test_f2_a_missing_registry_is_a_read_error(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    forward_validation_registry_path(root).unlink()
    with pytest.raises(ConfigError):
        load_registered_forward_validation_rules(root, 1)
