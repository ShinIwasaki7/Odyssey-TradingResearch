"""事前登録の生成と作り直し（D25 §3.4・§3.8、決定 172・174）と、生成物の読込と全試行のコンパイルの
試験（人工のカードで）。

- 生成物はテストの中のリポジトリの根（`configs/` と研究ポリシーの前提のファイルの写し）に書き、
  `experiment run` と同じ読込（`load_experiment_v2`。照合 C1〜C7・登録簿・前提のファイルの照合を
  含む）に通し、格子の全試行をコンパイルする。
- 作り直し（`regenerate`。PR 6 の CI の検査 4 の呼び口）は、同じ入力から同じバイト列を出す。
- 改善の版（版 2・3）は、変更の提案と前の版の実行記録（人工）から作る。

snapshot も成績も読まない。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app.composition import trial_strategy
from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.app.config.loader import load_yaml_mapping
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.search import ChangeKind, enumerate_assignments
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from odyssey_fx.strategy.compiler.compiled import CompileSucceeded
from odyssey_fx.strategy.compiler.validate import compile_strategy
from tests.fixtures.hypothesis_cards import (
    card_bytes,
    card_data,
    install_policy_v5,
    run_record,
    workspace,
)
from tools.ops.hypothesis_cards.changes import CHANGE_PREREG_REJECTED
from tools.ops.hypothesis_cards.generate import (
    FINAL_HOLDOUT_START,
    PreregChecks,
    generate,
    generator_digest,
    header_line,
    prereg_note,
    regenerate,
)
from tools.ops.hypothesis_cards.repo import DictView, DirView, OverlayView, ToolFailure
from tools.ops.hypothesis_cards.screening import check_change, verify_preregistration

CARD_ID = "h-20261120-dual-ema"
EXPERIMENT_ID = "c_h20261120_dual_ema_gbpusd"
MEMO = "cd" * 32


def _load(root: Path, relative: str) -> ExperimentV2:
    return load_experiment_v2(
        root / relative,
        repo_root=root,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )


def _write(root: Path, files: dict[str, bytes]) -> None:
    for path, body in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)


def _compile_all(loaded: ExperimentV2) -> int:
    search = loaded.search
    assert search is not None
    timeframes = {d.ref: d for d in loaded.environment.timeframe_defs.values()}
    assignments = enumerate_assignments(search.plan)
    for assignment in assignments:
        outcome = compile_strategy(
            trial_strategy(loaded.body.strategy, assignment), INITIAL_CATALOG, timeframes
        )
        assert isinstance(outcome, CompileSucceeded), outcome
    return len(assignments)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return workspace(tmp_path)


@pytest.mark.parametrize("symbol", ["GBPUSD", "USDJPY", "AUDJPY", "USDCHF"])
def test_version_1_loads_with_the_policy_checks_and_every_trial_compiles(
    root: Path, symbol: str
) -> None:
    """円の組は換算の snapshot を書かず、それ以外は書く（読込が要否を確かめる）。"""
    raw = card_bytes(card_data(symbol=symbol))
    prereg = generate(DirView(root), card=raw, version=1)
    assert set(prereg.files) == {prereg.strategy_path, prereg.experiment_path}
    _write(root, dict(prereg.files))
    loaded = _load(root, prereg.experiment_path)
    # 登録簿の現行の版（第 5 版。D24 §8.2）と、その条件の指標を持つ指標集合 v4 を選ぶ。
    assert loaded.policy.version == 5 and loaded.metric_set_version == 4
    assert _compile_all(loaded) == 4
    search = loaded.search
    assert search is not None and search.final_holdout is not None
    assert str(search.final_holdout.interval.start).startswith(FINAL_HOLDOUT_START[:10])
    assert search.lineage is None
    payload = load_yaml_mapping(root / prereg.experiment_path)
    assert ("conversion_snapshots" in payload) is (symbol[3:] != "JPY")
    assert payload["hypothesis"].endswith("は、費用を引いた後も、両組で共通基準を満たす")


def test_the_first_line_is_the_generation_mark(root: Path) -> None:
    raw = card_bytes(card_data())
    prereg = generate(DirView(root), card=raw, version=1)
    expected = "# " + header_line(CARD_ID, hashlib.sha256(raw).hexdigest(), generator_digest())
    for body in prereg.files.values():
        assert body.split(b"\n", 1)[0].decode() == expected
    assert expected.startswith(f"# generated-by: research_runner card={CARD_ID} card_sha256=")


def test_regeneration_is_byte_identical_and_reads_only_the_given_view(root: Path) -> None:
    raw = card_bytes(card_data())
    first = generate(DirView(root), card=raw, version=1).files
    again = regenerate(card=raw, change=None, version=1, head=DirView(root))
    assert again == first
    # 同じファイルを辞書で持つ時点（git のコミットの代わり）からも同じバイト列になる。
    needed = [
        "configs/policies/research/registry.yaml",
        "configs/policies/research/research_policy_v5.yaml",
        "configs/runner/experiment_templates/GBPUSD.yaml",
    ]
    snapshot = DictView({path: (root / path).read_bytes() for path in needed})
    assert regenerate(card=raw, change=None, version=1, head=snapshot) == first


def test_a_changed_card_byte_changes_the_generated_bytes(root: Path) -> None:
    raw = card_bytes(card_data())
    first = generate(DirView(root), card=raw, version=1).files
    edited = generate(DirView(root), card=raw + b"\n", version=1).files
    assert first.keys() == edited.keys() and first != edited


def test_policy_version_five_selects_metric_set_four_and_copies_favored_regimes(
    root: Path,
) -> None:
    install_policy_v5(root)
    data = card_data()
    data["favored_regimes"] = ["UP_HIGH", "UP_LOW"]
    prereg = generate(DirView(root), card=card_bytes(data), version=1)
    _write(root, dict(prereg.files))
    loaded = _load(root, prereg.experiment_path)
    assert (loaded.policy.version, loaded.metric_set_version) == (5, 4)
    assert loaded.search is not None and loaded.search.favored_regimes is not None
    assert [label.value for label in loaded.search.favored_regimes] == ["UP_HIGH", "UP_LOW"]
    assert verify_preregistration(DirView(root), prereg).passed


def test_a_missing_template_is_a_tool_failure(root: Path) -> None:
    (root / "configs/runner/experiment_templates/GBPUSD.yaml").unlink()
    with pytest.raises(ToolFailure):
        generate(DirView(root), card=card_bytes(card_data()), version=1)


def test_the_eight_templates_are_well_formed() -> None:
    from tests.fixtures.hypothesis_cards import REPO_ROOT
    from tools.ops.hypothesis_cards.card import SYMBOLS
    from tools.ops.hypothesis_cards.generate import load_template

    for symbol in sorted(SYMBOLS):
        template = load_template(DirView(REPO_ROOT), symbol)
        assert template.execution_series == f"{symbol}/15m/bid"


# --- 改善の版 -------------------------------------------------------------------------


def _change(from_version: int, kind: str, operations: list[dict[str, Any]]) -> bytes:
    from tools.ops.hypothesis_cards.emit import dump_document

    return dump_document(
        {
            "schema_version": 1,
            "card": CARD_ID,
            "from_version": from_version,
            "decision": "CHANGE",
            "change_kind": kind,
            "operations": operations,
            "reason": "長い側の移動平均を広げると取引の数がどう変わるかを見る",
            "prediction": "取引が減り、横ばいの局面での負けが減る",
            "memo_sha256": MEMO,
        }
    )


def _taken(root: Path, raw: bytes) -> None:
    """版 1 の事前登録と実行記録が main にある状態（カードは taken/）を作る。"""
    prereg = generate(DirView(root), card=raw, version=1)
    files = dict(prereg.files)
    files[f"research/hypotheses/taken/{CARD_ID}.yaml"] = raw
    files[f"research/loops/{CARD_ID}/run_v1.yaml"] = run_record(CARD_ID, EXPERIMENT_ID, 1)
    _write(root, files)


def test_a_parameters_change_makes_only_the_next_experiment(root: Path) -> None:
    raw = card_bytes(card_data())
    _taken(root, raw)
    change = _change(
        1,
        "PARAMETERS",
        [
            {
                "op": "set_axis_values",
                "instance": "slow_ema",
                "parameter": "period",
                "values": [100, 150],
            }
        ],
    )
    result = check_change(DirView(root), card=raw, change=change, from_version=1)
    assert result.passed, result.problems
    prereg = result.prereg
    assert prereg is not None
    assert set(prereg.files) == {f"configs/experiments/{EXPERIMENT_ID}_v2.yaml"}
    assert prereg.strategy_path == f"configs/strategies/{EXPERIMENT_ID}_v1.yaml"
    _write(root, dict(prereg.files))
    _write(root, {f"research/loops/{CARD_ID}/change_v2.yaml": change})
    loaded = _load(root, prereg.experiment_path)
    search = loaded.search
    assert search is not None and search.lineage is not None
    assert search.lineage.change_kinds == (ChangeKind.PARAMETERS,)
    assert search.lineage.derived_from is not None
    assert search.lineage.derived_from.execution == 1
    assert loaded.values["version"] == 2
    assert _compile_all(loaded) == 4
    head = DirView(root)
    assert regenerate(card=raw, change=change, version=2, head=head) == prereg.files


def test_a_strategy_code_change_bumps_the_strategy_version(root: Path) -> None:
    raw = card_bytes(card_data())
    _taken(root, raw)
    change = _change(
        1,
        "STRATEGY_CODE",
        [
            {
                "op": "set_parameter",
                "instance": "stop_level",
                "parameter": "lookback",
                "value": {"type": "INT", "value": 30},
            }
        ],
    )
    result = check_change(DirView(root), card=raw, change=change, from_version=1)
    assert result.passed, result.problems
    prereg = result.prereg
    assert prereg is not None
    assert set(prereg.files) == {
        f"configs/strategies/{EXPERIMENT_ID}_v2.yaml",
        f"configs/experiments/{EXPERIMENT_ID}_v2.yaml",
    }
    _write(root, dict(prereg.files))
    loaded = _load(root, prereg.experiment_path)
    assert loaded.body.strategy.version == 2
    assert _compile_all(loaded) == 4


def test_version_three_reads_the_version_two_change_and_record(root: Path) -> None:
    raw = card_bytes(card_data())
    _taken(root, raw)
    first = _change(
        1,
        "PARAMETERS",
        [
            {
                "op": "set_axis_values",
                "instance": "slow_ema",
                "parameter": "period",
                "values": [100, 150],
            }
        ],
    )
    v2 = check_change(DirView(root), card=raw, change=first, from_version=1).prereg
    assert v2 is not None
    _write(root, dict(v2.files))
    _write(
        root,
        {
            f"research/loops/{CARD_ID}/change_v2.yaml": first,
            f"research/loops/{CARD_ID}/run_v2.yaml": run_record(CARD_ID, EXPERIMENT_ID, 2),
        },
    )

    def second_change(values: list[int]) -> bytes:
        return _change(
            2,
            "PARAMETERS",
            [
                {"op": "remove_axis", "instance": "take_profit", "parameter": "reward_risk"},
                {
                    "op": "set_axis_values",
                    "instance": "slow_ema",
                    "parameter": "period",
                    "values": values,
                },
            ],
        )

    # 長い EMA の窓 300 は期間 200 の 2 倍より短いので、コンパイルが拒否する（停止の型）。
    refused = check_change(
        DirView(root), card=raw, change=second_change([100, 150, 200, 250]), from_version=2
    )
    assert refused.reason == CHANGE_PREREG_REJECTED
    second = second_change([60, 80, 100, 150])
    result = check_change(DirView(root), card=raw, change=second, from_version=2)
    assert result.passed, result.problems
    assert result.prereg is not None
    assert set(result.prereg.files) == {f"configs/experiments/{EXPERIMENT_ID}_v3.yaml"}
    assert regenerate(card=raw, change=second, version=3, head=DirView(root)) == result.prereg.files


def test_the_lineage_needs_the_previous_run_record(root: Path) -> None:
    raw = card_bytes(card_data())
    change = _change(
        1,
        "PARAMETERS",
        [
            {
                "op": "set_axis_values",
                "instance": "slow_ema",
                "parameter": "period",
                "values": [100, 150],
            }
        ],
    )
    with pytest.raises(ToolFailure):
        generate(DirView(root), card=raw, version=2, changes={2: change})


# --- 事前登録の注記 -------------------------------------------------------------------


def test_the_preregistration_note_lists_the_hashes(root: Path) -> None:
    raw = card_bytes(card_data())
    prereg = generate(DirView(root), card=raw, version=1)
    note = load_yaml_mapping(
        Path("prereg_v1.yaml"),
        text=prereg_note(prereg, PreregChecks(4, 0, 12, 1024)).decode(),
    )
    assert list(note) == [
        "schema_version",
        "card",
        "version",
        "experiment_id",
        "card_sha256",
        "change_sha256",
        "generator_digest",
        "generated",
        "checks",
    ]
    assert note["card_sha256"] == hashlib.sha256(raw).hexdigest()
    assert note["change_sha256"] is None
    assert {item["path"]: item["sha256"] for item in note["generated"]} == {
        path: hashlib.sha256(body).hexdigest() for path, body in prereg.files.items()
    }
    assert note["checks"]["trial_count"] == 4


def test_verification_reports_a_compile_rejection_without_writing(root: Path) -> None:
    data = card_data()
    data["strategy"]["components"][1]["parameters"]["window_bars"]["value"] = 120
    prereg = generate(DirView(root), card=card_bytes(data), version=1)
    result = verify_preregistration(OverlayView(DirView(root), {}), prereg)
    assert not result.passed
    assert not (root / prereg.experiment_path).exists()
