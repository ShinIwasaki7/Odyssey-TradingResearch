"""作業役の起動の実物と出力の検査の試験（D25 §5.1・§5.2・§5.6、決定 237）。

本物の `claude` は起動しない。起動は偽の `Processes`（コマンドを記録するだけ）で受け、作業役の
出力は試験が束の `out/` に書く。すべて人工のファイル（一時ディレクトリ）。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from tests.fixtures.hypothesis_cards import card_bytes, card_data
from tests.fixtures.research_runner import Rig, make_rig
from tools.ops.hypothesis_cards.card import parse_card
from tools.ops.hypothesis_cards.changes import parse_change
from tools.ops.hypothesis_cards.repo import DictView
from tools.ops.research_runner import workers as workers_module
from tools.ops.research_runner.bundle import BundlePlan, Entry, plan_bundle
from tools.ops.research_runner.ports import (
    BundleOutOfScope,
    ChangeOutput,
    CommandNotAllowed,
    GateInputs,
    GateResult,
    IdeatorOutput,
    ReportOutput,
    VerdictOutput,
    WorkerPoll,
    WorkerRequest,
    WorkerStatus,
    WorkerUnavailable,
)
from tools.ops.research_runner.runner import Ports, Runner
from tools.ops.research_runner.state import CardState, GlobalState, StateStore, format_time
from tools.ops.research_runner.workers import (
    MAX_FILE_BYTES,
    ClaudeWorkers,
    verdict_of,
)

RAW_CARD: Final = card_bytes(card_data())
CARD: Final = parse_card(RAW_CARD)
CARD_ID: Final = CARD.card_id
EXPERIMENT: Final = CARD.experiment_id
POLICY: Final = "configs/policies/research/research_policy_v5.yaml"
D25: Final = b"### 3.1 a\n\ntext\n\n### 3.9 b\n\ntext\n"
SUCCESS: Final = json.dumps(
    {"type": "result", "subtype": "success", "is_error": False, "result": "DONE"}
)
MEMO: Final = "# 分析メモ\n\n読んだ表示、原因の仮説、変更の候補、予想（数値入り 1.23）。\n".encode()


@dataclass
class _Clock:
    moment: datetime = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.moment


@dataclass
class _Procs:
    """偽の起動（コマンドを記録するだけ。何も走らせない）。"""

    alive_pids: set[int] = field(default_factory=set)
    spawned: list[dict[str, Any]] = field(default_factory=list)

    def alive(self, pid: int) -> bool:
        return pid in self.alive_pids

    def is_group_leader(self, pid: int) -> bool:
        return False

    def signal_group(self, group: int, signal: int) -> None:
        return None

    def spawn(self, command: Sequence[str], *, cwd: Path, state_dir: Path) -> int:
        pid = 7000 + len(self.spawned)
        self.alive_pids.add(pid)
        (state_dir / "pid.txt").write_text(f"{pid}\n", encoding="utf-8")
        self.spawned.append({"command": list(command), "cwd": cwd, "state_dir": state_dir})
        return pid


def _files(definitions: bool = True) -> dict[str, bytes]:
    files = {
        "research/trial_ledger.jsonl": b"\n",
        "research/adoption_reviews.jsonl": b"\n",
        f"research/hypotheses/taken/{CARD_ID}.yaml": RAW_CARD,
        POLICY: b"policy: 5\n",
        "docs/design/D25_research_runner_and_queue.md": D25,
    }
    if definitions:
        for name in ("ideator", "analyst", "verifier"):
            files[f".claude/agents/{name}.md"] = f"---\nname: {name}\n---\n".encode()
    return files


@dataclass
class World:
    repo: Path
    procs: _Procs
    clock: _Clock
    workers: ClaudeWorkers


@pytest.fixture
def world(tmp_path: Path) -> World:
    repo = tmp_path / "repo"
    version_dir = repo / "runs" / "experiments" / EXPERIMENT / "v1"
    (version_dir / "search").mkdir(parents=True)
    (version_dir / "report.md").write_text("# report\n", encoding="utf-8")
    procs, clock = _Procs(), _Clock()
    view = DictView(_files())
    workers = ClaudeWorkers(
        repo=repo,
        view=lambda: view,
        procs=procs,
        clock=clock,
        policy_path=lambda _view: POLICY,
    )
    return World(repo, procs, clock, workers)


def _request(role: str, version: int = 1) -> WorkerRequest:
    if role == "ideator":
        return WorkerRequest(role="ideator", attempt=1, extra={"capacity": "2"})
    return WorkerRequest(role=role, attempt=1, card_id=CARD_ID, version=version)


def _launch(world: World, role: str, version: int = 1) -> tuple[WorkerRequest, Path]:
    request = _request(role, version)
    world.workers.launch(request)
    return request, world.workers.bundle_dir(request)


def _finish(world: World, request: WorkerRequest, *, code: int = 0, log: str = SUCCESS) -> None:
    state = world.workers.state_dir(request)
    (state / "output.log").write_text(log, encoding="utf-8")
    (state / "exit_code.txt").write_text(f"{code}\n", encoding="utf-8")
    world.procs.alive_pids.clear()


def _change(**overrides: Any) -> bytes:
    data: dict[str, Any] = {
        "schema_version": 1,
        "card": CARD_ID,
        "from_version": 1,
        "decision": "CHANGE",
        "change_kind": "PARAMETERS",
        "operations": [
            {
                "op": "set_axis_values",
                "instance": "slow_ema",
                "parameter": "period",
                "values": [60, 120],
            }
        ],
        "reason": "長い期間の平均で騙しの交差を減らす",
        "prediction": "取引は減るが損切りの連続が短くなる",
    }
    data.update(overrides)
    return yaml.safe_dump(data, allow_unicode=True).encode()


# --- 起動 -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "agent"),
    [
        ("ideator", "ideator"),
        ("analyst_change", "analyst"),
        ("analyst_report", "analyst"),
        ("verifier", "verifier"),
    ],
)
def test_the_launch_uses_the_design_tools_and_the_bundle_as_cwd(
    world: World, role: str, agent: str
) -> None:
    request, bundle = _launch(world, role)
    [spawned] = world.procs.spawned
    command: list[str] = spawned["command"]
    assert command[:2] == ["claude", "-p"]
    assert command[command.index("--agent") + 1] == agent
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert command[command.index("--output-format") + 1] == "json"
    # 道具は読み取りと書き込みだけ（Bash・ネットワーク・エージェントの起動は無い）。
    tools = command[command.index("--tools") + 1].split(",")
    assert tools == ["Read", "Glob", "Grep", "Write"]
    assert not [part for part in command if "Bash" in part or "WebFetch" in part]
    # 確認なしで許すのは束の中の読み取りと out/ への書き込みだけ。
    allowed = command[command.index("--allowedTools") + 1].split(",")
    assert allowed == ["Read(./**)", "Edit(./out/**)"]
    assert "--strict-mcp-config" in command
    assert spawned["cwd"] == bundle
    # 包みの状態（終了コード・結果の JSON）は束の外（作業役から見えない）。
    assert bundle not in Path(spawned["state_dir"]).parents
    assert (bundle / ".claude" / "agents" / f"{agent}.md").is_file()
    assert (bundle / "out").is_dir()
    assert world.workers.poll(request).status is WorkerStatus.RUNNING


def test_a_program_outside_the_allowed_list_is_not_launched(world: World) -> None:
    world.workers.program = "bash"
    request = _request("analyst_change")
    with pytest.raises(CommandNotAllowed):
        world.workers.launch(request)
    assert world.procs.spawned == []
    assert not world.workers.bundle_dir(request).exists()


def test_a_missing_definition_is_unavailable(world: World) -> None:
    view = DictView(_files(definitions=False))
    world.workers.view = lambda: view
    with pytest.raises(WorkerUnavailable):
        world.workers.launch(_request("verifier"))
    assert world.procs.spawned == []


def test_a_bundle_that_needs_something_outside_the_list_is_not_written(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = plan_bundle

    def wider(*args: Any, **kwargs: Any) -> BundlePlan:
        plan = original(*args, **kwargs)
        sealed = Entry("records/data/sealed/x.parquet", "repo:data/sealed/x.parquet", b"x")
        return BundlePlan(plan.scope, (*plan.entries, sealed))

    monkeypatch.setattr(workers_module, "plan_bundle", wider)
    request = _request("analyst_change")
    with pytest.raises(BundleOutOfScope):
        world.workers.launch(request)
    assert world.procs.spawned == []
    assert not world.workers.bundle_dir(request).exists()


def test_poll_without_an_exit_code_follows_the_process(world: World) -> None:
    request, _ = _launch(world, "verifier")
    assert world.workers.poll(request).status is WorkerStatus.RUNNING
    world.procs.alive_pids.clear()
    assert world.workers.poll(request).status is WorkerStatus.LOST
    assert world.workers.poll(_request("verifier", 2)).status is WorkerStatus.LOST


# --- 終わり方 -----------------------------------------------------------------------------


def test_a_usage_limit_answer_is_reported_as_the_usage_limit(world: World) -> None:
    request, _ = _launch(world, "verifier")
    log = json.dumps(
        {"subtype": "success", "is_error": True, "result": "Claude AI usage limit reached"}
    )
    _finish(world, request, code=1, log=log)
    assert world.workers.poll(request).status is WorkerStatus.USAGE_LIMIT


def test_an_error_ending_is_a_failure(world: World) -> None:
    request, bundle = _launch(world, "verifier")
    (bundle / "out" / "verdict.yaml").write_text("x: 1\n", encoding="utf-8")
    _finish(world, request, code=1, log="not json")
    assert world.workers.poll(request).status is WorkerStatus.FAILED


# --- 分析メモのハッシュ（決定 237） ---------------------------------------------------------


def test_the_runner_puts_the_memo_sha256_and_ignores_the_analyst_value(world: World) -> None:
    request, bundle = _launch(world, "analyst_change")
    (bundle / "out" / "analysis_memo.md").write_bytes(MEMO)
    (bundle / "out" / "change.yaml").write_bytes(_change(memo_sha256="0" * 64))
    _finish(world, request)
    poll = world.workers.poll(request)
    assert poll.status is WorkerStatus.DONE, poll.reason
    output = poll.output
    assert isinstance(output, ChangeOutput)
    real = hashlib.sha256(MEMO).hexdigest()
    assert output.memo_sha256 == real
    change = parse_change(output.raw, card_id=CARD_ID, expected_from_version=1)
    assert change.memo_sha256 == real
    assert b"0" * 64 not in output.raw
    # 分析メモは版のディレクトリの analysis/ に同じバイト列で写る。
    assert output.memo_path == f"runs/experiments/{EXPERIMENT}/v1/analysis/analysis_memo.md"
    assert (world.repo / output.memo_path).read_bytes() == MEMO


def test_a_change_without_the_memo_sha256_gets_it_from_the_runner(world: World) -> None:
    request, bundle = _launch(world, "analyst_change")
    (bundle / "out" / "analysis_memo.md").write_bytes(MEMO)
    (bundle / "out" / "change.yaml").write_bytes(_change())
    _finish(world, request)
    output = world.workers.poll(request).output
    assert isinstance(output, ChangeOutput)
    assert output.memo_sha256 == hashlib.sha256(MEMO).hexdigest()
    assert output.decision == "CHANGE"


def test_digits_in_the_reason_are_out_of_rules(world: World) -> None:
    request, bundle = _launch(world, "analyst_change")
    (bundle / "out" / "analysis_memo.md").write_bytes(MEMO)
    (bundle / "out" / "change.yaml").write_bytes(_change(reason="期間を 2 倍にする"))
    _finish(world, request)
    poll = world.workers.poll(request)
    assert (poll.status, poll.reason) == (WorkerStatus.REJECTED, "CHANGE_OUT_OF_RULES")
    assert not (world.repo / "runs" / "experiments" / EXPERIMENT / "v1" / "analysis").exists()


def test_an_ambiguous_change_carries_its_reason_identifier(world: World) -> None:
    request, bundle = _launch(world, "analyst_change")
    (bundle / "out" / "analysis_memo.md").write_bytes(MEMO)
    data = {"schema_version": 1, "card": CARD_ID, "from_version": 1, "decision": "AMBIGUOUS"}
    (bundle / "out" / "change.yaml").write_bytes(
        yaml.safe_dump({**data, "reason_id": "NEW_COMPONENT_NEEDED"}).encode()
    )
    _finish(world, request)
    output = world.workers.poll(request).output
    assert isinstance(output, ChangeOutput)
    assert (output.decision, output.reason) == ("AMBIGUOUS", "NEW_COMPONENT_NEEDED")


# --- 出力の検査（D25 §5.6） ------------------------------------------------------------------


def test_a_link_in_out_is_rejected(world: World, tmp_path: Path) -> None:
    request, bundle = _launch(world, "analyst_change")
    secret = tmp_path / "secret.md"
    secret.write_bytes(b"outside\n")
    os.symlink(secret, bundle / "out" / "analysis_memo.md")
    (bundle / "out" / "change.yaml").write_bytes(_change())
    _finish(world, request)
    poll = world.workers.poll(request)
    assert poll.status is WorkerStatus.FAILED and "リンク" in str(poll.reason)


def test_a_write_outside_out_is_rejected(world: World) -> None:
    request, bundle = _launch(world, "verifier")
    (bundle / "out" / "verdict.yaml").write_bytes(_verdict())
    (bundle / "records" / "added.yaml").parent.mkdir(parents=True, exist_ok=True)
    (bundle / "records" / "added.yaml").write_text("x: 1\n", encoding="utf-8")
    _finish(world, request)
    poll = world.workers.poll(request)
    assert poll.status is WorkerStatus.FAILED and "増えた" in str(poll.reason)


def test_a_changed_copy_in_the_bundle_is_rejected(world: World) -> None:
    request, bundle = _launch(world, "verifier")
    (bundle / "out" / "verdict.yaml").write_bytes(_verdict())
    (bundle / "rules" / "d25_3_1.md").write_text("changed\n", encoding="utf-8")
    _finish(world, request)
    assert world.workers.poll(request).status is WorkerStatus.FAILED


def test_an_extra_file_is_discarded_and_recorded(world: World) -> None:
    request, bundle = _launch(world, "verifier")
    (bundle / "out" / "verdict.yaml").write_bytes(_verdict())
    (bundle / "out" / "notes.txt").write_text("extra\n", encoding="utf-8")
    _finish(world, request)
    poll = world.workers.poll(request)
    assert poll.status is WorkerStatus.DONE
    record = json.loads(
        (world.workers.state_dir(request) / "inspection.json").read_text(encoding="utf-8")
    )
    assert record["discarded"] == ["notes.txt"]


def test_a_too_large_output_is_rejected(world: World) -> None:
    request, bundle = _launch(world, "analyst_change")
    (bundle / "out" / "analysis_memo.md").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    (bundle / "out" / "change.yaml").write_bytes(_change())
    _finish(world, request)
    poll = world.workers.poll(request)
    assert poll.status is WorkerStatus.FAILED and "大きすぎる" in str(poll.reason)


def test_a_missing_output_is_a_failure(world: World) -> None:
    request, bundle = _launch(world, "analyst_change")
    (bundle / "out" / "analysis_memo.md").write_bytes(MEMO)
    _finish(world, request)
    assert world.workers.poll(request).status is WorkerStatus.FAILED


def test_the_report_text_is_kept_with_the_comparison(world: World) -> None:
    request, bundle = _launch(world, "analyst_report")
    (bundle / "out" / "comparison.md").write_text("| 版 | 値 |\n| 1 | 0.5 |\n", encoding="utf-8")
    text = {
        "schema_version": 1,
        "card": CARD_ID,
        "prediction": "上昇の局面で取引が多い",
        "change_reasons": ["騙しの交差を減らす"],
        "change_contents": ["長い平均の期間の軸の値を変えた"],
    }
    (bundle / "out" / "report_text.yaml").write_bytes(
        yaml.safe_dump(text, allow_unicode=True).encode()
    )
    _finish(world, request)
    output = world.workers.poll(request).output
    assert isinstance(output, ReportOutput)
    assert output.change_reasons == ("騙しの交差を減らす",)
    assert (world.repo / output.comparison_path).is_file()
    kept = world.repo / "runs" / "experiments" / EXPERIMENT / "v1" / "analysis" / "report_text.yaml"
    assert kept.is_file()


def test_digits_in_the_report_text_are_out_of_rules(world: World) -> None:
    request, bundle = _launch(world, "analyst_report")
    (bundle / "out" / "comparison.md").write_text("比較\n", encoding="utf-8")
    text = {
        "schema_version": 1,
        "card": CARD_ID,
        "prediction": "取引が ３ 割減る",
        "change_reasons": [],
        "change_contents": [],
    }
    (bundle / "out" / "report_text.yaml").write_bytes(
        yaml.safe_dump(text, allow_unicode=True).encode()
    )
    _finish(world, request)
    poll = world.workers.poll(request)
    assert (poll.status, poll.reason) == (WorkerStatus.REJECTED, "CHANGE_OUT_OF_RULES")


def test_the_ideator_cards_come_with_the_manifest_paths(world: World) -> None:
    request, bundle = _launch(world, "ideator")
    (bundle / "out" / "cards" / "a-20261120-new-rule.yaml").write_text("id: x\n", encoding="utf-8")
    _finish(world, request)
    output = world.workers.poll(request).output
    assert isinstance(output, IdeatorOutput)
    assert output.cards == (b"id: x\n",)
    assert "ledger/trial_ledger.jsonl" in output.read_paths


# --- 確かめ役の判定ファイル（#200 の仮置き 6） ---------------------------------------------------


def _verdict(**overrides: Any) -> bytes:
    data: dict[str, Any] = {
        "schema_version": 1,
        "card": CARD_ID,
        "result": "PASS",
        "checks": {
            "reason_from_memo": "PASS",
            "content_matches_reason": "PASS",
            "prediction_before_change": "PASS",
            "no_digits_in_report_text": "PASS",
        },
        "reasons": [],
    }
    data.update(overrides)
    return yaml.safe_dump(data).encode()


def test_the_verdict_pass_and_fail_are_read() -> None:
    assert verdict_of(_verdict(), CARD_ID) == VerdictOutput(passed=True, reasons=())
    failed = _verdict(
        result="FAIL",
        checks={
            "reason_from_memo": "FAIL",
            "content_matches_reason": "PASS",
            "prediction_before_change": "PASS",
            "no_digits_in_report_text": "PASS",
        },
        reasons=["REASON_NOT_FROM_MEMO"],
    )
    assert verdict_of(failed, CARD_ID) == VerdictOutput(
        passed=False, reasons=("REASON_NOT_FROM_MEMO",)
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"::: not yaml",
        _verdict(result="FAIL"),  # 全部 PASS なのに FAIL
        _verdict(reasons=["REASON_NOT_FROM_MEMO"]),  # PASS なのに理由がある
        _verdict(card="h-20261120-other"),
        _verdict(checks={"reason_from_memo": "PASS"}),
        _verdict(result="MAYBE"),
        _verdict(extra="x"),
    ],
)
def test_a_broken_verdict_is_no_output(raw: bytes, world: World) -> None:
    assert verdict_of(raw, CARD_ID) is None
    request, bundle = _launch(world, "verifier")
    (bundle / "out" / "verdict.yaml").write_bytes(raw)
    _finish(world, request)
    assert world.workers.poll(request).status is WorkerStatus.FAILED


# --- 実行器との受け渡し -------------------------------------------------------------------------


@dataclass
class _Refusing:
    """起動のコマンドが許可の一覧に無いと知らせる作業役の口。"""

    def launch(self, request: WorkerRequest) -> None:
        raise CommandNotAllowed("bash")

    def poll(self, request: WorkerRequest) -> WorkerPoll:
        return WorkerPoll(WorkerStatus.LOST)


@dataclass
class _RefusingGate:
    def check(self, inputs: GateInputs) -> GateResult:
        raise CommandNotAllowed("curl")


def _card_at(rig: Rig, phase: str) -> None:
    rig.world.put_on_main({f"research/hypotheses/taken/{CARD_ID}.yaml": RAW_CARD})
    store = StateStore(rig.paths.state_dir)
    store.save_card(
        CardState(
            card_id=CARD_ID,
            mouth="human",
            phase=phase,
            taken_at=format_time(rig.clock.now()),
            symbol="GBPUSD",
            experiment=EXPERIMENT,
            version=2,
            executions={"2": {"experiment_id": "a" * 64, "execution": 1}},
            prereg_commits={"2": "c" * 40},
            repro="MATCH",
        )
    )
    store.save_global(GlobalState(boot_id="boot-1", cards=[CARD_ID], weekly_last_week="2026-11-16"))


def _with(rig: Rig, **ports: object) -> Runner:
    base = rig.runner().ports
    replaced = Ports(**{name: getattr(base, name) for name in base.__dataclass_fields__})
    for name, value in ports.items():
        object.__setattr__(replaced, name, value)
    return Runner(rig.paths, replaced)


def test_a_worker_command_outside_the_list_stops_everything(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    _card_at(rig, "C8")
    rig.clock.advance(minutes=5)
    _with(rig, workers=_Refusing()).step()
    state = rig.state()
    reasons = {item["reason"]: item for item in state["reasons"]}
    assert reasons["COST_RISK"]["card"] == CARD_ID
    assert "worker" not in rig.card(CARD_ID)["procs"]


def test_a_gate_command_outside_the_list_stops_everything(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    _card_at(rig, "C9")
    rig.clock.advance(minutes=5)
    _with(rig, gate=_RefusingGate()).step()
    reasons = {item["reason"]: item for item in rig.state()["reasons"]}
    assert reasons["COST_RISK"]["card"] == CARD_ID


def test_the_gate_gets_the_identifiers_from_the_card_state(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    _card_at(rig, "C9")
    rig.step()
    [inputs] = rig.gate.seen
    assert (inputs.card_id, inputs.experiment, inputs.versions) == (CARD_ID, EXPERIMENT, (1, 2))
    assert inputs.prereg_commits == {"2": "c" * 40} and inputs.repro == "MATCH"


def test_the_launch_grace_counts_from_the_launch_time(world: World) -> None:
    request, _ = _launch(world, "verifier")
    (world.workers.state_dir(request) / "pid.txt").unlink()
    assert world.workers.poll(request).status is WorkerStatus.RUNNING
    world.clock.moment += timedelta(minutes=11)
    assert world.workers.poll(request).status is WorkerStatus.LOST
