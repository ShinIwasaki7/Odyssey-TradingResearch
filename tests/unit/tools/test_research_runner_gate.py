"""関門の機械の項目の試験（D25 §5.5 の 1〜5）。人工の台帳・記録票・レポートと、偽のコマンド。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from tools.ops.hypothesis_cards.repo import DictView
from tools.ops.research_runner.gate import NOT_INDEPENDENT_NOTE, LocalGate
from tools.ops.research_runner.ports import CommandNotAllowed, GateInputs

EXPERIMENT: Final = "c_h20261120_dual_ema_gbpusd"
RECORD_1: Final = "1" * 32
RECORD_2: Final = "2" * 32


def _row(event: str, record: str, execution: int, lineage: Any = None) -> str:
    return json.dumps(
        {"event": event, "experiment_id": record, "execution": execution, "lineage": lineage}
    )


def _ledger(*, derived: str = RECORD_1, kinds: Sequence[str] = ("PARAMETERS",)) -> bytes:
    lineage = {
        "derived_from": {"experiment_id": derived, "execution": 1},
        "change_kinds": list(kinds),
        "viewed_before": [],
        "note_digest": "d" * 64,
    }
    rows = [
        _row("STARTED", RECORD_1, 1),
        _row("FINISHED", RECORD_1, 1),
        _row("STARTED", RECORD_2, 1, lineage),
        _row("FINISHED", RECORD_2, 1),
    ]
    return ("\n".join(rows) + "\n").encode()


class _World:
    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.exec = root / "exec"
        self.ledger = _ledger()
        self.ancestors: set[tuple[str, str]] = {("p1", "g1"), ("p2", "g2")}
        self.codes: list[int] = [0, 0]
        self.commands: list[list[str]] = []
        for version, commit in ((1, "g1"), (2, "g2")):
            base = self.repo / "runs" / "experiments" / EXPERIMENT / f"v{version}"
            base.mkdir(parents=True)
            (base / "experiment_manifest.json").write_text(
                json.dumps({"git_commit": commit, "git_dirty": False}), encoding="utf-8"
            )
            note = NOT_INDEPENDENT_NOTE if version == 2 else ""
            (base / "report.md").write_text(f"# 判定\n\n{note}\n", encoding="utf-8")

    def run(self, command: Sequence[str], cwd: Path) -> int:
        self.commands.append(list(command))
        return self.codes[len(self.commands) - 1]

    def gate(self, **kw: Any) -> LocalGate:
        view = DictView({"research/trial_ledger.jsonl": self.ledger})
        return LocalGate(
            repo=self.repo,
            exec_worktree=self.exec,
            view=lambda: view,
            is_ancestor=lambda a, b: (a, b) in self.ancestors,
            run=self.run,
            **kw,
        )


def _inputs(**overrides: Any) -> GateInputs:
    data: dict[str, Any] = {
        "card_id": "h-20261120-dual-ema",
        "experiment": EXPERIMENT,
        "versions": (1, 2),
        "prereg_commits": {"1": "p1", "2": "p2"},
        "executions": {
            "1": {"experiment_id": RECORD_1, "execution": 1},
            "2": {"experiment_id": RECORD_2, "execution": 1},
        },
        "repro": "MATCH",
    }
    data.update(overrides)
    return GateInputs(**data)


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def _items(world: _World, inputs: GateInputs | None = None) -> dict[int, bool]:
    return dict(world.gate().check(inputs or _inputs()).items)


def test_all_items_pass(world: _World) -> None:
    result = world.gate().check(_inputs())
    assert result.passed and [item for item, _ in result.items] == [1, 2, 3, 4, 5]
    # レポートの作り直しは全版に当て、許可の一覧の `experiment report` だけを走らせる。
    assert [command[3:6] for command in world.commands] == [
        ["odyssey-fx", "experiment", "report"]
    ] * 2


def test_a_dirty_or_unrelated_record_fails_item_1(world: _World) -> None:
    world.ancestors.discard(("p2", "g2"))
    assert _items(world)[1] is False


def test_a_dirty_tree_fails_item_1(world: _World) -> None:
    manifest = world.repo / "runs" / "experiments" / EXPERIMENT / "v1" / "experiment_manifest.json"
    manifest.write_text(json.dumps({"git_commit": "g1", "git_dirty": True}), encoding="utf-8")
    assert _items(world)[1] is False


@pytest.mark.parametrize(
    "ledger",
    [
        _ledger(derived="9" * 32),  # 系譜が前の版を指さない
        _ledger(kinds=("PARAMETERS", "STRATEGY_CODE")),  # 変更の種類が 2 つ
        _ledger().replace(
            b'"FINISHED", "experiment_id": "' + RECORD_2.encode(), b'"OTHER", "experiment_id": "'
        ),
        b"",
    ],
)
def test_ledger_problems_fail_item_2(world: _World, ledger: bytes) -> None:
    world.ledger = ledger
    assert _items(world)[2] is False


def test_a_report_that_cannot_be_rebuilt_fails_item_3(world: _World) -> None:
    world.codes = [0, 1]
    assert _items(world)[3] is False


def test_a_repro_mismatch_fails_item_4(world: _World) -> None:
    assert _items(world, _inputs(repro="MISMATCH"))[4] is False


def test_a_missing_independence_note_fails_item_5(world: _World) -> None:
    report = world.repo / "runs" / "experiments" / EXPERIMENT / "v2" / "report.md"
    report.write_text("# 判定\n", encoding="utf-8")
    assert _items(world)[5] is False


def test_a_command_outside_the_allowed_list_is_not_run(world: _World) -> None:
    with pytest.raises(CommandNotAllowed):
        world.gate(odyssey=("curl", "-s")).check(_inputs())
    assert world.commands == []


def test_the_note_is_the_report_text() -> None:
    from odyssey_fx.evaluation.adapters import search_report

    assert NOT_INDEPENDENT_NOTE == search_report._NOT_INDEPENDENT_NOTE
