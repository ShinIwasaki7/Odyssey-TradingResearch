"""読む束の試験（D25 §5.2。順 11 の PR 8b の 2 本目）。すべて人工のファイル（一時ディレクトリ）。

- 役ごとに、許されたものだけが入る。
- 封印・2024 年以降・最終検証と前向き検証の結果に見立てた偽のファイルが、どの役の束にも入らない
  （中身の印がどの束のファイルにも現れない）。
- 一覧の外が要るときは、写す前に `BundleOutOfScope`（何も書かない）。
- リンクと `..` で束の外・許しの外に届かない。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Final

import pytest

from tests.fixtures.hypothesis_cards import card_bytes, card_data
from tools.ops.hypothesis_cards.card import parse_card
from tools.ops.hypothesis_cards.repo import DictView
from tools.ops.research_runner.bundle import (
    BundlePlan,
    BundleScope,
    Entry,
    check_plan,
    design_section,
    plan_bundle,
    research_period_sections,
    write_bundle,
)
from tools.ops.research_runner.ports import BundleOutOfScope

RAW_CARD: Final = card_bytes(card_data())
CARD: Final = parse_card(RAW_CARD)
CARD_ID: Final = CARD.card_id
EXPERIMENT: Final = CARD.experiment_id
OTHER: Final = "c_h20261101_other_eurusd"
POLICY: Final = "configs/policies/research/research_policy_v5.yaml"
#: 封印・最終検証・前向き検証・2024 年以降に見立てた偽のファイルの中身の印。
SEALED: Final = b"SEALED-MARKER-do-not-copy"

D25_TEXT: Final = """# D25

### 3.1 書式【提案】

カードの書式の写し。

### 3.2 識別子

ここは写さない。

### 3.9 改善の変更の提案【提案】

変更の提案の写し。

---

## 4. 状態
"""

README: Final = """# 研究の一巡

## 研究期間の結果

研究期間の節。

## 最終検証の結果

最終検証の節（写さない）。

### 最終検証の下の節

これも写さない。

## 次の研究

次の節。
"""


def _view() -> DictView:
    return DictView(
        {
            "research/trial_ledger.jsonl": b'{"event":"STARTED"}\n',
            "research/adoption_reviews.jsonl": b"\n",
            "research/final_validations.jsonl": SEALED,
            "research/quality_access_log.jsonl": SEALED,
            f"research/hypotheses/taken/{CARD_ID}.yaml": RAW_CARD,
            "research/hypotheses/done/h-20261101-other.yaml": b"other: card\n",
            f"research/loops/{CARD_ID}/change_v2.yaml": b"change: 1\n",
            f"research/loops/{CARD_ID}/prereg_v1.yaml": b"prereg: 1\n",
            "research/loops/h-20261101-other/prereg_v1.yaml": b"other: 1\n",
            f"configs/experiments/{EXPERIMENT}.yaml": b"experiment: 1\n",
            f"configs/experiments/{EXPERIMENT}_v2.yaml": b"experiment: 2\n",
            f"configs/experiments/{OTHER}.yaml": b"other: 1\n",
            f"configs/strategies/{EXPERIMENT}_v1.yaml": b"strategy: 1\n",
            f"configs/strategies/{OTHER}_v1.yaml": b"other: 1\n",
            POLICY: b"policy: 5\n",
            "docs/design/D25_research_runner_and_queue.md": D25_TEXT.encode(),
            "docs/data/eurusd_step10_research_loop/README.md": README.encode(),
            "docs/data/sealed/README.md": SEALED,
            "docs/status/progress.md": SEALED,
            "data/sealed/2024.parquet": SEALED,
        }
    )


def _version(runs: Path, experiment: str, version: int) -> Path:
    base = runs / "experiments" / experiment / f"v{version}"
    (base / "search").mkdir(parents=True)
    (base / "report.md").write_text(f"# {experiment} v{version}\n", encoding="utf-8")
    (base / "analysis").mkdir()
    (base / "analysis" / "analysis_memo.md").write_text("メモ\n", encoding="utf-8")
    (base / "analysis" / "comparison.md").write_text("比較\n", encoding="utf-8")
    (base / "review" / "ab12cd34").mkdir(parents=True)
    (base / "review" / "ab12cd34" / "review.md").write_text("審査\n", encoding="utf-8")
    (base / "review" / "ab12cd34" / "review_record.json").write_bytes(SEALED)
    # 写さないもの（最終検証の出力・console.log・CSV）。
    (base / "final").mkdir()
    (base / "final" / "report.md").write_bytes(SEALED)
    (base / "console.log").write_bytes(SEALED)
    (base / "trades_2024.csv").write_bytes(SEALED)
    return base


@pytest.fixture
def runs(tmp_path: Path) -> Path:
    root = tmp_path / "repo" / "runs"
    _version(root, EXPERIMENT, 1)
    _version(root, EXPERIMENT, 2)
    _version(root, OTHER, 1)
    # 研究期間の外に見立てた置き場へのリンク（実験のディレクトリそのもの・版の中のファイル）。
    outside = tmp_path / "outside"
    (outside / "v1" / "search").mkdir(parents=True)
    (outside / "v1" / "report.md").write_bytes(SEALED)
    os.symlink(outside, root / "experiments" / "c_link_out")
    linked = root / "experiments" / OTHER / "v1" / "analysis" / "comparison.md"
    linked.unlink()
    os.symlink(tmp_path / "outside" / "v1" / "report.md", linked)
    (root / "experiments" / "bad..name").mkdir()
    return root


def _plan(role: str, runs: Path, versions: tuple[int, ...] = (1, 2)) -> BundlePlan:
    scope = (
        BundleScope(role="ideator")
        if role == "ideator"
        else BundleScope(role=role, card_id=CARD_ID, experiment=EXPERIMENT, versions=versions)
    )
    report_text = runs / "experiments" / EXPERIMENT / f"v{max(versions)}" / "analysis"
    (report_text / "report_text.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    return plan_bundle(scope, view=_view(), runs=runs, policy_path=POLICY, catalog=b"{}\n")


def _written(target: Path) -> dict[str, bytes]:
    found: dict[str, bytes] = {}
    for root, _dirs, names in os.walk(target):
        for name in names:
            path = Path(root) / name
            assert not path.is_symlink(), path
            found[path.relative_to(target).as_posix()] = path.read_bytes()
    return found


@pytest.mark.parametrize("role", ["ideator", "analyst_change", "analyst_report", "verifier"])
def test_no_bundle_contains_sealed_final_forward_or_later_data(role: str, runs: Path) -> None:
    plan = _plan(role, runs)
    check_plan(plan)
    target = runs / "runner" / "bundles" / role
    write_bundle(plan, target=target, agent="x", definition=b"definition\n")
    files = _written(target)
    assert not [path for path, data in files.items() if SEALED in data]
    for path in files:
        lowered = path.lower()
        assert "final" not in lowered and "sealed" not in lowered and "status" not in lowered
        assert not lowered.endswith((".csv", "console.log", ".parquet"))
        assert not path.startswith("data/")


def test_the_ideator_bundle_has_every_place_of_the_table(runs: Path) -> None:
    dests = {entry.dest for entry in _plan("ideator", runs).entries}
    assert "ledger/trial_ledger.jsonl" in dests
    assert "ledger/adoption_reviews.jsonl" in dests
    for experiment in (EXPERIMENT, OTHER):
        assert f"reports/{experiment}/v1/report.md" in dests
    assert f"analysis/{EXPERIMENT}/v2/analysis_memo.md" in dests
    assert f"reviews/{EXPERIMENT}/v1/ab12cd34/review.md" in dests
    assert f"records/research/loops/{CARD_ID}/change_v2.yaml" in dests
    assert "records/research/loops/h-20261101-other/prereg_v1.yaml" in dests
    assert f"records/configs/experiments/{OTHER}.yaml" in dests
    assert "records/docs/data/eurusd_step10_research_loop/README.md" in dests
    assert {"catalog/components.json", "rules/research_policy_v5.yaml"} <= dests
    assert {"rules/d25_3_1.md", "rules/d25_3_9.md"} <= dests
    # 審査の数値の記録・リンクの先・字種に合わない名前は無い。
    assert not [dest for dest in dests if "review_record" in dest or "c_link_out" in dest]
    assert not [dest for dest in dests if "bad..name" in dest]


def test_the_analyst_bundle_has_only_its_card(runs: Path) -> None:
    dests = {entry.dest for entry in _plan("analyst_change", runs).entries}
    assert not [dest for dest in dests if dest.startswith("reviews/")]
    assert not [dest for dest in dests if OTHER in dest or "h-20261101-other" in dest]
    assert f"reports/{EXPERIMENT}/v2/report.md" in dests
    assert f"analysis/{EXPERIMENT}/v1/comparison.md" in dests
    assert f"records/configs/experiments/{EXPERIMENT}_v2.yaml" in dests
    assert f"records/configs/strategies/{EXPERIMENT}_v1.yaml" in dests
    assert f"records/research/hypotheses/taken/{CARD_ID}.yaml" in dests
    assert "ledger/trial_ledger.jsonl" in dests and "catalog/components.json" in dests


def test_the_verifier_bundle_has_no_ledger_or_catalog(runs: Path) -> None:
    dests = {entry.dest for entry in _plan("verifier", runs).entries}
    assert not [dest for dest in dests if dest.startswith(("ledger/", "catalog/", "reviews/"))]
    assert "records/report_text.yaml" in dests
    assert f"reports/{EXPERIMENT}/v1/report.md" in dests
    assert {"rules/d25_3_1.md", "rules/d25_3_9.md", "rules/research_policy_v5.yaml"} <= dests


def test_links_are_not_followed_and_are_recorded_as_skipped(runs: Path) -> None:
    plan = _plan("ideator", runs)
    assert f"experiments/{OTHER}/v1/analysis/comparison.md" in plan.skipped
    assert f"analysis/{OTHER}/v1/comparison.md" not in {entry.dest for entry in plan.entries}


def test_a_link_at_the_runs_root_copies_nothing_from_runs(tmp_path: Path, runs: Path) -> None:
    linked_runs = tmp_path / "linked_runs"
    os.symlink(runs, linked_runs)
    plan = plan_bundle(
        BundleScope(role="ideator"), view=_view(), runs=linked_runs, policy_path=POLICY, catalog=b""
    )
    assert not [entry for entry in plan.entries if entry.origin.startswith("runs:")]


def test_the_readme_keeps_only_the_research_period_sections() -> None:
    kept = research_period_sections(README)
    assert "研究期間の節。" in kept and "次の節。" in kept
    assert "最終検証" not in kept


def test_the_design_sections_are_cut_at_the_next_heading() -> None:
    section = design_section(D25_TEXT, "3.1")
    assert section is not None and "カードの書式の写し" in section and "3.2" not in section
    section = design_section(D25_TEXT, "3.9")
    assert section is not None and "変更の提案の写し" in section and "## 4." not in section


@pytest.mark.parametrize(
    ("dest", "origin"),
    [
        ("records/data/sealed/2024.parquet", "repo:data/sealed/2024.parquet"),
        ("ledger/final_validations.jsonl", "repo:research/final_validations.jsonl"),
        ("reports/x/v1/report.md", f"runs:experiments/{EXPERIMENT}/v1/final/report.md"),
        ("records/docs/status/progress.md", "repo:docs/status/progress.md"),
        ("records/x.csv", "repo:research/loops/x/old.csv"),
        ("records/../../escape.yaml", f"repo:research/loops/{CARD_ID}/x.yaml"),
        ("records/x.yaml", "repo:research/../data/x.yaml"),
        ("records/x.yaml", "repo:/etc/passwd"),
        ("reviews/x/v1/n/review.md", f"runs:experiments/{EXPERIMENT}/v1/review/n/review.md"),
        ("records/research/loops/other/x.yaml", "repo:research/loops/h-20261101-other/x.yaml"),
        ("records/console.log", f"runs:experiments/{EXPERIMENT}/v1/console.log"),
        ("ledger/review.jsonl", "repo:research/quality_access_log.jsonl"),
    ],
)
def test_anything_outside_the_list_stops_before_copying(dest: str, origin: str, runs: Path) -> None:
    good = _plan("analyst_change", runs)
    bad = BundlePlan(good.scope, (*good.entries, Entry(dest, origin, data=SEALED)))
    target = runs / "runner" / "bundles" / "bad"
    with pytest.raises(BundleOutOfScope):
        check_plan(bad)
    with pytest.raises(BundleOutOfScope):
        write_bundle(bad, target=target, agent="analyst", definition=b"d\n")
    assert not target.exists()  # 写す前に止まった（何も書いていない）


@pytest.mark.parametrize("experiment", ["..", "../data", "a/b", "", "-x"])
def test_an_experiment_name_that_is_not_a_place_name_stops_before_copying(
    experiment: str, runs: Path
) -> None:
    scope = BundleScope(
        role="analyst_change", card_id=CARD_ID, experiment=experiment, versions=(1,)
    )
    with pytest.raises(BundleOutOfScope):
        plan_bundle(scope, view=_view(), runs=runs, policy_path=POLICY, catalog=b"")


def test_the_manifest_lists_every_copied_file_with_its_sha256(runs: Path) -> None:
    plan = _plan("analyst_report", runs)
    target = runs / "runner" / "bundles" / "report"
    manifest = write_bundle(plan, target=target, agent="analyst", definition=b"definition\n")
    assert json.loads((target / "manifest.json").read_text(encoding="utf-8")) == manifest
    for item in manifest["files"]:
        data = (target / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    assert (target / ".claude" / "agents" / "analyst.md").read_bytes() == b"definition\n"
    assert (target / "out").is_dir() and not any((target / "out").iterdir())


def test_a_rebuilt_bundle_drops_the_previous_output(runs: Path) -> None:
    plan = _plan("verifier", runs)
    target = runs / "runner" / "bundles" / "again"
    write_bundle(plan, target=target, agent="verifier", definition=b"d\n")
    (target / "out" / "verdict.yaml").write_text("old\n", encoding="utf-8")
    write_bundle(plan, target=target, agent="verifier", definition=b"d\n")
    assert not (target / "out" / "verdict.yaml").exists()


def test_the_catalog_is_made_from_the_code() -> None:
    from tools.ops.research_runner.bundle import catalog_document

    data = json.loads(catalog_document())
    assert data["schema_version"] == 1 and data["components"]
    for item in data["components"]:
        assert set(item) == {"name", "version", "inputs", "outputs", "parameters"}
