"""研究ポリシーファイル（版 1・2・3）と版の登録簿の読込（D07 §20.2、D09 §6.1・§7.1・§10.9）。

段階5 実装 PR 1。版 3 は値が未決定（D09 §17.3 の Q19〜Q32）なので、試験用の版 3 をテストの中で
作る（`tests/fixtures/evaluation/research_policies.py`。リポジトリの登録簿には載せない）。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.research_policy import (
    load_research_policy,
    load_research_policy_registry,
    research_policy_path,
    research_policy_registry_path,
    verify_registered,
)
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.research_policy import ResearchPolicy, current_standard_version
from odyssey_fx.evaluation.domain.search import Comparator, StandardPurpose
from odyssey_fx.evaluation.domain.splits import SplitWindow
from tests.fixtures.evaluation.research_policies import (
    DEFAULT_STANDARD_LINES,
    append_registry_entry,
    policy_v3_text,
    write_policy,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

#: 研究ポリシー版 1 のダイジェスト（段階4 の記録票が持つ値。変わると段階4 の実験の同じ版の
#: 再実行が検査 P3 に当たる。D09 §10.9 の「版 1 の項目の形は変えない」）。
V1_DIGEST = "f31c6088a0ff8447a6003a9c56dbf44d45f0ab71046879f1e6b1178876c71aef"
#: 研究ポリシー版 2（試行数の上限 100。Q7 決定）のダイジェスト。
V2_DIGEST = "c8903543b93db0aca60ff457f382d8cc8c8304ac11ff5c2402661e0400fe2426"


def _load_text(
    text: str, version: int = 3, research_until: UtcTime | None = None
) -> ResearchPolicy:
    path = Path(f"research_policy_v{version}.yaml")
    if research_until is None:
        return load_research_policy(path, policy_id="research_policy", version=version, text=text)
    return load_research_policy(
        path,
        policy_id="research_policy",
        version=version,
        text=text,
        research_until=research_until,
    )


def _refused(text: str, version: int = 3) -> str:
    with pytest.raises(ConfigError) as caught:
        _load_text(text, version)
    return str(caught.value)


# --- リポジトリの版 1・版 2 と登録簿 ------------------------------------------------


def test_the_repository_policies_match_the_repository_registry() -> None:
    """版 1 のダイジェストは変わらず、版 2 は試行数の上限 100 を持ち、どちらも登録簿と一致する。"""
    entries = load_research_policy_registry(research_policy_registry_path(REPO_ROOT))
    assert [(item.policy_id, item.version, item.purpose) for item in entries] == [
        ("research_policy", 1, None),
        ("research_policy", 2, None),
    ]
    v1 = load_research_policy(
        research_policy_path(REPO_ROOT, "research_policy", 1),
        policy_id="research_policy",
        version=1,
    )
    v2 = load_research_policy(
        research_policy_path(REPO_ROOT, "research_policy", 2),
        policy_id="research_policy",
        version=2,
    )
    assert v1.digest.hex == V1_DIGEST
    assert (v1.trial_limit, v1.evaluation_standard) == (None, None)
    assert v2.digest.hex == V2_DIGEST
    assert (v2.trial_limit, v2.evaluation_standard) == (100, None)
    registry = research_policy_registry_path(REPO_ROOT)
    assert verify_registered(v1, entries, registry_path=registry).version == 1
    assert verify_registered(v2, entries, registry_path=registry).version == 2
    # 版 3（評価基準の値）は Q19〜Q32 の決定まで登録しない（D09 §10.9）。現行の版はまだ無い。
    assert current_standard_version(entries, "research_policy") is None


_V1 = (
    "schema_version: 1\nid: research_policy\nversion: {version}\n"
    "complexity_limits: {{component_kinds: 30, instances: 36, parameters: 33,"
    " decision_outputs: 18}}\n"
)


@pytest.mark.parametrize(
    ("version", "extra", "word"),
    [
        (1, "search_limits: {trials: 100}\n", "search_limits"),
        (2, "", "search_limits"),
        (2, "search_limits: {trials: 100}\nevaluation_standard: {}\n", "evaluation_standard"),
        (3, "search_limits: {trials: 100}\n", "evaluation_standard"),
    ],
)
def test_the_version_decides_which_keys_the_file_has(version: int, extra: str, word: str) -> None:
    """版 1 は上限だけ、版 2 は試行数の上限を足し、版 3 以上は評価基準の群も持つ（D09 §10.9）。"""
    assert word in _refused(_V1.format(version=version) + extra, version)


def test_a_non_positive_trial_limit_is_refused() -> None:
    assert "trial_limit" in _refused(
        _V1.format(version=2) + "search_limits: {trials: 0}\n", version=2
    )


# --- 版 3 の読込 ----------------------------------------------------------------


def test_a_version_3_policy_is_read_into_an_evaluation_standard() -> None:
    """評価基準の群を型へ読む。長さは秒数、閾値は `Decimal`（D09 §6.1・§7.1）。"""
    policy = _load_text(policy_v3_text())
    standard = policy.evaluation_standard
    assert standard is not None
    assert policy.trial_limit == 100
    assert standard.purpose is StandardPurpose.MECHANISM_CHECK
    assert standard.split.train_seconds == 4 * 86_400
    assert standard.split.validation_seconds == 2 * 86_400
    assert standard.split.purge_seconds == 0
    assert standard.split.window is SplitWindow.ROLLING
    assert str(standard.split.range.start) == "2015-01-04T22:00:00Z"
    assert standard.selection.metric is MetricId.NET_RETURN_RATE
    assert standard.selection.eligibility == ()
    floor = standard.validation.fold_floors[0]
    assert (floor.metric, floor.comparator, floor.threshold) == (
        MetricId.MAX_DRAWDOWN_MTM_RATE,
        Comparator.LE,
        Decimal("0.2"),
    )
    assert [item.name for item in standard.sufficiency.classes] == ["HIGH", "LOW"]


def test_the_digest_covers_every_value_but_not_the_way_it_is_written() -> None:
    """ダイジェストは評価基準の全項目を含み（閾値1つで変わる）、書き方の揺れでは変わらない。

    長さは秒数、閾値は `Decimal` の正規形で入れる（`"2d"` と `"48h"`、`"0.2"` と `"0.20"`）。
    """
    base = _load_text(policy_v3_text()).digest
    rewritten = policy_v3_text().replace('"2d"', '"48h"').replace('"0.2"', '"0.20"')
    assert _load_text(rewritten).digest == base
    assert _load_text(policy_v3_text().replace('"0.2"', '"0.25"')).digest != base
    assert _load_text(policy_v3_text(trials=99)).digest != base
    standard_purpose = policy_v3_text(replace={"purpose": "  purpose: STANDARD"})
    assert _load_text(standard_purpose).digest != base


def _split(**overrides: str) -> str:
    values = {
        "range": '{start: "2015-01-04T22:00:00Z", end: "2015-01-16T22:00:00Z"}',
        "train_length": '"4d"',
        "validation_length": '"2d"',
        "window": "ROLLING",
        "purge": '"0s"',
        "min_folds": "2",
    }
    values.update(overrides)
    return "  split:\n" + "\n".join(f"    {key}: {value}" for key, value in values.items())


@pytest.mark.parametrize(
    ("name", "replace", "word"),
    [
        # 期間分割の標準規則（D09 §6.1 の検査1・5・6・7）
        (
            "empty range",
            {"split": _split(range='{start: "2015-01-16T22:00:00Z", end: "2015-01-16T22:00:00Z"}')},
            "検査1",
        ),
        ("zero train length", {"split": _split(train_length='"0d"')}, "検査1"),
        ("zero validation length", {"split": _split(validation_length='"0s"')}, "検査1"),
        ("negative purge", {"split": _split(purge='"-1s"')}, "purge"),
        ("calendar month", {"split": _split(train_length='"1M"')}, "train_length"),
        ("compound length", {"split": _split(train_length='"1d12h"')}, "train_length"),
        ("unknown window", {"split": _split(window="SLIDING")}, "window"),
        ("zero min folds", {"split": _split(min_folds="0")}, "検査7"),
        ("too few folds", {"split": _split(min_folds="5")}, "検査7"),
        (
            "range past the research history",
            {"split": _split(range='{start: "2023-06-01T00:00:00Z", end: "2024-01-01T00:00:00Z"}')},
            "検査6",
        ),
        # 評価基準の検査 E1〜E5（D09 §7.1・§7.8）
        (
            "trade count as a floor",
            {
                "validation": DEFAULT_STANDARD_LINES["validation"].replace(
                    "MAX_DRAWDOWN_MTM_RATE, comparator: LE", "TRADE_COUNT, comparator: GE"
                )
            },
            "取引件数",
        ),
        (
            "reference metric for selection",
            {
                "selection": DEFAULT_STANDARD_LINES["selection"].replace(
                    "NET_RETURN_RATE", "HYPOTHETICAL_CLOSED_PROFIT"
                )
            },
            "参考値",
        ),
        (
            "unknown metric",
            {"selection": DEFAULT_STANDARD_LINES["selection"].replace("NET_RETURN_RATE", "ALPHA")},
            "selection.metric",
        ),
        (
            "unknown direction",
            {"selection": DEFAULT_STANDARD_LINES["selection"].replace("MAXIMIZE", "BEST")},
            "direction",
        ),
        (
            "unknown comparator",
            {
                "validation": DEFAULT_STANDARD_LINES["validation"].replace(
                    "comparator: LE", "comparator: EQ"
                )
            },
            "comparator",
        ),
        (
            "unknown statistic",
            {"validation": DEFAULT_STANDARD_LINES["validation"].replace("MEDIAN", "MEAN")},
            "statistic",
        ),
        (
            "empty floors",
            {
                "validation": "  validation:\n    fold_floors: []\n    aggregate: []",
            },
            "E2",
        ),
        (
            "duplicated floor",
            {
                "validation": (
                    "  validation:\n    fold_floors:\n"
                    '      - {metric: NET_RETURN_RATE, comparator: GE, threshold: "0"}\n'
                    '      - {metric: NET_RETURN_RATE, comparator: GE, threshold: "0.1"}\n'
                    "    aggregate: []"
                )
            },
            "E3",
        ),
        (
            "omitted aggregate",
            {
                "validation": (
                    "  validation:\n    fold_floors:\n"
                    '      - {metric: NET_RETURN_RATE, comparator: GE, threshold: "0"}'
                )
            },
            "aggregate",
        ),
        (
            "float threshold",
            {"validation": DEFAULT_STANDARD_LINES["validation"].replace('"0.2"', "0.2")},
            "threshold",
        ),
        (
            "class bounds not descending",
            {"sufficiency": DEFAULT_STANDARD_LINES["sufficiency"].replace('"50"', '"0"')},
            "E4",
        ),
        (
            "last class bound not zero",
            {
                "sufficiency": DEFAULT_STANDARD_LINES["sufficiency"].replace(
                    'LOW, min_train_trades_per_365d: "0"', 'LOW, min_train_trades_per_365d: "1"'
                )
            },
            "E4",
        ),
        (
            "lower-case class name",
            {"sufficiency": DEFAULT_STANDARD_LINES["sufficiency"].replace("HIGH", "High")},
            "E4",
        ),
        ("omitted purpose", {"purpose": None}, "purpose"),
        ("unknown purpose", {"purpose": "  purpose: CHECK"}, "purpose"),
    ],
)
def test_a_broken_evaluation_standard_is_a_policy_error(
    name: str, replace: dict[str, str | None], word: str
) -> None:
    """研究ポリシーを読むときの検査（D09 §6.1 の 1〜3・5〜7、§7.1 の E1〜E5）は設定の誤り。"""
    message = _refused(policy_v3_text(replace=replace))
    assert word in message, (name, message)


def test_the_research_history_boundary_is_taken_from_the_caller() -> None:
    """検査6 の境界は呼び出し側が渡す（既定は D03 §3.8 の初版の境界）。"""
    boundary = UtcTime.parse("2015-01-16T22:00:00Z")
    with pytest.raises(ConfigError, match="検査6"):
        _load_text(policy_v3_text(), research_until=boundary)
    assert _load_text(policy_v3_text(), research_until=boundary + timedelta(seconds=1))


# --- 版の登録簿（D09 §10.9 の照合 (1)〜(5)）------------------------------------------


def _workspace(tmp_path: Path, *, purpose: str = "MECHANISM_CHECK") -> Path:
    write_policy(
        tmp_path,
        "research_policy",
        3,
        policy_v3_text(replace={"purpose": f"  purpose: {purpose}"}),
    )
    return tmp_path


def _read(root: Path, version: int = 3) -> ResearchPolicy:
    return load_research_policy(
        research_policy_path(root, "research_policy", version),
        policy_id="research_policy",
        version=version,
    )


def _verify(root: Path, version: int = 3) -> None:
    registry = research_policy_registry_path(root)
    verify_registered(
        _read(root, version), load_research_policy_registry(registry), registry_path=registry
    )


def test_a_registered_version_3_passes(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    line = append_registry_entry(root, "research_policy", 3)
    assert "purpose: MECHANISM_CHECK" in line
    _verify(root)


def test_a_missing_registry_or_an_unregistered_version_is_refused(tmp_path: Path) -> None:
    """(1) 登録簿が無い、(3) 版参照の要素が無い、は設定の誤り（未登録の版は使えない）。"""
    root = _workspace(tmp_path)
    with pytest.raises(ConfigError, match="登録簿 .* が無い"):
        _verify(root)
    write_policy(root, "research_policy", 4, policy_v3_text(version=4))
    append_registry_entry(root, "research_policy", 4)
    with pytest.raises(ConfigError, match="登録簿に無い"):
        _verify(root)


def test_content_changed_without_a_new_version_is_refused(tmp_path: Path) -> None:
    """(4) ダイジェストの食い違い: 版を上げずに閾値1つを書き換えたファイルは読めない。"""
    root = _workspace(tmp_path)
    append_registry_entry(root, "research_policy", 3)
    path = research_policy_path(root, "research_policy", 3)
    path.write_text(path.read_text(encoding="utf-8").replace('"0.2"', '"0.3"'), encoding="utf-8")
    with pytest.raises(ConfigError, match="ダイジェスト"):
        _verify(root)


def test_a_purpose_that_differs_from_the_registry_is_refused(tmp_path: Path) -> None:
    """(5) 登録簿の要素の `purpose` とファイルの `purpose` が違えば拒否（Q33 決定）。"""
    root = _workspace(tmp_path)
    append_registry_entry(root, "research_policy", 3, purpose="STANDARD")
    with pytest.raises(ConfigError, match="用途"):
        _verify(root)


@pytest.mark.parametrize(
    ("entries", "word"),
    [
        (
            '  - {id: research_policy, version: 1, digest: "' + "a" * 64 + '"}\n'
            '  - {id: research_policy, version: 1, digest: "' + "b" * 64 + '"}\n',
            "2つある",
        ),
        ('  - {id: research_policy, version: 3, digest: "' + "a" * 64 + '"}\n', "purpose"),
        (
            '  - {id: research_policy, version: 3, digest: "' + "a" * 64 + '", purpose: null}\n',
            "purpose",
        ),
        (
            '  - {id: research_policy, version: 3, digest: "' + "a" * 64 + '", purpose: OTHER}\n',
            "purpose",
        ),
        (
            '  - {id: research_policy, version: 2, digest: "'
            + "a" * 64
            + '", purpose: STANDARD}\n',
            "purpose",
        ),
        ('  - {id: research_policy, version: 1, digest: "ABC"}\n', "成立しない"),
        ('  - {id: research_policy, version: 1, digest: "' + "a" * 64 + '", note: x}\n', "note"),
    ],
)
def test_a_broken_registry_is_refused(tmp_path: Path, entries: str, word: str) -> None:
    """登録簿そのものの誤り: 主キーの重複、版 3 以上の `purpose` の欠落、版 1・2 の `purpose`。"""
    path = tmp_path / "registry.yaml"
    path.write_text("schema_version: 1\nentries:\n" + entries, encoding="utf-8")
    with pytest.raises(ConfigError, match=word):
        load_research_policy_registry(path)


def test_the_registry_schema_version_is_one(tmp_path: Path) -> None:
    path = tmp_path / "registry.yaml"
    path.write_text("schema_version: 2\nentries: []\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_research_policy_registry(path)
