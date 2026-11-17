"""作業役の起動の実物と出力の検査（D25 §5.1・§5.2・§5.6、決定 176・177・237）。

**起動**（`ClaudeWorkers.launch`）:

1. 役の定義（`origin/main` の `.claude/agents/<定義>.md`。PR #200）を読む。無ければ
   `WorkerUnavailable`（実行器は道具の失敗として 1 回やり直して止まる）。
2. 読む束の計画を作り、許可の一覧で確かめる（`bundle.check_plan`）。一覧の外が要れば、何も写さずに
   `BundleOutOfScope`（`SEALED_NEEDED`）。
3. 起動のコマンドを組み立て、許可の一覧（`commands.unlisted_command`。`Runner._spawn` と同じ確かめ）
   に当てる。無ければ起動せずに `CommandNotAllowed`（`COST_RISK`）。
4. 束を写し、束をカレントディレクトリにして、切り離した小さな包み（`Processes.spawn`）で
   `claude -p <起動の文> --agent <定義> --permission-mode dontAsk --output-format json …` を
   起動する。
   道具は `--tools Read,Glob,Grep,Write` に絞り、確認なしで許すのは束の中の読み取り
   `Read(./**)` と `out/` への書き込み `Edit(./out/**)` だけ（D25 §5.2、#200 の仮置き 8。
   書き込みの規則は Claude Code では `Edit(...)` が `Write` にも当たる）。コマンドの実行（Bash）・
   ネットワーク・ほかのエージェントの起動は道具の一覧に無い。MCP の道具も読まない
   （`--strict-mcp-config`。本 PR の仮置き G2）。

**様子と出力の検査**（`poll`。D25 §5.6）: 終わったら、結果の JSON の終わり方（成功・誤り・
利用上限）だけを読み、作業役の文の中身は `out/` のファイルとして形を検査する（中身の良し悪しは
判断しない）。

- 束の中の写し（`manifest.json` の各ファイル・定義）が変わった、写しの置き場にファイルが増えた
  （`out/` の外への書き込み）→ 出力なし（`FAILED`）。
- `out/` の中にリンク・普通でないファイルがある、大きさが上限を超える → 出力なし。
- 決まったファイルが無い・YAML として読めない・項目と型が合わない → 出力なし。
- 自由文に数字がある（仮置き 6）、変更の提案の静的な検査（D25 §3.9 の (1)〜(4)・(6)・(7)）に
  通らない → 規則の外（`REJECTED`）。
- 決まったファイルのほかのファイルが `out/` にある → 捨てて記録（失敗にしない）。

**分析メモのハッシュ**（決定 237）: 分析役は `memo_sha256` を書かない。実行器が受け取った
`out/analysis_memo.md` から SHA-256 を計算し、`change.yaml` の `memo_sha256` に入れてから
（分析役が書いた値があっても捨てる）変更の提案の検査（`changes.parse_change`。必須の項目）に当てる。

分析メモ・比較表・報告の文は、`runs/experiments/<実験>/v<版>/analysis/` に写す（D25 §2.3。版管理の
外）。実行器は中身を読まない（形と数字の有無だけ）。成績の数値を状態・issue・PR 本文に写さない。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from tools.ops.hypothesis_cards.card import has_digit, parse_card
from tools.ops.hypothesis_cards.changes import ChangeRejected, parse_change
from tools.ops.hypothesis_cards.emit import dump_document
from tools.ops.hypothesis_cards.generate import select_policy
from tools.ops.hypothesis_cards.queue import card_path
from tools.ops.hypothesis_cards.repo import RepoView, ToolFailure
from tools.ops.research_runner.bundle import (
    AGENT_OF_ROLE,
    ANALYSIS_DIR,
    MANIFEST,
    OUT_DIR,
    PLACES,
    REPORT_TEXT,
    BundleScope,
    check_plan,
    plan_bundle,
    write_bundle,
)
from tools.ops.research_runner.commands import unlisted_command
from tools.ops.research_runner.ports import (
    ChangeOutput,
    Clock,
    CommandNotAllowed,
    IdeatorOutput,
    Processes,
    ReportOutput,
    VerdictOutput,
    WorkerOutput,
    WorkerPoll,
    WorkerRequest,
    WorkerStatus,
    WorkerUnavailable,
)
from tools.ops.research_runner.state import format_time, parse_time

__all__ = [
    "ALLOWED_TOOLS",
    "AVAILABLE_TOOLS",
    "ClaudeWorkers",
    "MAX_FILE_BYTES",
    "MAX_OUT_BYTES",
    "REQUIRED_OUTPUTS",
    "VERDICT_CHECKS",
    "inspect_out",
    "worker_command",
]

#: 作業役に見せる組み込みの道具（D25 §5.2。Bash・ネットワーク・エージェントの起動は無い）。
AVAILABLE_TOOLS: Final = "Read,Glob,Grep,Write"
#: 確認なしで許す道具の規則（束の中の読み取りと `out/` への書き込みだけ。#200 の仮置き 8）。
#: Read の規則は Glob・Grep にも当たり、Edit の規則は Write にも当たる（Claude Code の権限の規則）。
ALLOWED_TOOLS: Final = ("Read(./**)", f"Edit(./{OUT_DIR}/**)")
#: 役ごとの `out/` の決まったファイル（D25 §5.1 の表。発想役は `cards/` の下の 0〜3 枚）。
REQUIRED_OUTPUTS: Final[Mapping[str, tuple[str, ...]]] = {
    "ideator": (),
    "analyst_change": ("analysis_memo.md", "change.yaml"),
    "analyst_report": ("comparison.md", REPORT_TEXT),
    "verifier": ("verdict.yaml",),
}
#: 大きさの上限（本 PR の仮置き G3）: 1 つのファイル・`out/` の合計・ファイルの数。
MAX_FILE_BYTES: Final = 1024 * 1024
MAX_OUT_BYTES: Final = 4 * 1024 * 1024
MAX_OUT_FILES: Final = 32
#: 確かめ役の 4 項目と、`FAIL` のときの理由の識別子（#200 の仮置き 6。決定 240 でこのまま）。
VERDICT_CHECKS: Final[Mapping[str, str]] = {
    "reason_from_memo": "REASON_NOT_FROM_MEMO",
    "content_matches_reason": "CONTENT_NOT_MATCHING_REASON",
    "prediction_before_change": "PREDICTION_OR_RESULT_PLACE_MISSING",
    "no_digits_in_report_text": "DIGITS_IN_REPORT_TEXT",
}
_CARD_FILE: Final = re.compile(r"cards/([a-z0-9][a-z0-9-]{0,63})\.yaml")
_DECISIONS: Final = frozenset({"CHANGE", "FINISH", "AMBIGUOUS"})
#: 起動の直後、包みが PID を書くまでの猶予（実行器の `LAUNCH_GRACE` と同じ長さ）。
LAUNCH_GRACE: Final = timedelta(minutes=10)
#: 利用上限の応答の見分け（結果の JSON が誤りで終わり、文にこの語があるとき。本 PR の仮置き G5）。
_USAGE_LIMIT: Final = re.compile(
    r"usage limit|rate limit|limit reached|limit will reset|out of extra usage|"
    r"5-hour limit|weekly limit|session limit",
    re.IGNORECASE,
)
_LOG_READ_BYTES: Final = 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def worker_command(program: str, agent: str, prompt: str) -> list[str]:
    """作業役の起動のコマンド（D25 §5.1 の形。`tools/ops/claude_review.py` の前例）。"""
    return [
        program,
        "-p",
        prompt,
        "--agent",
        agent,
        "--permission-mode",
        "dontAsk",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--tools",
        AVAILABLE_TOOLS,
        "--allowedTools",
        ",".join(ALLOWED_TOOLS),
    ]


def worker_prompt(request: WorkerRequest, *, now: str) -> str:
    """起動の文（#200 の仮置き 3 の渡すもの。成績の数値を含まない）。"""
    extra = dict(request.extra)
    if request.role == "ideator":
        capacity = extra.get("capacity", "")
        return (
            f"発想役として、束の rules/ の書式でカードを out/cards/ に書く。"
            f"書けるカードの枚数の上限（capacity）: {capacity}。いまの UTC の時刻: {now}。"
            "終わったら最後のメッセージを DONE か NO_CARD の 1 語にする。"
        )
    card = request.card_id or ""
    version = request.version or 0
    if request.role == "analyst_change":
        text = (
            f"段: analyst_change（段 5）。カード: {card}。結果を読んだ版: {version}。"
            "out/analysis_memo.md と out/change.yaml を書く（memo_sha256 は書かない）。"
        )
        note = extra.get("note")
        if note:
            text += f"止まった後の起動し直し: {note}（前の変更の提案は records/ にある）。"
        return text + "終わったら最後のメッセージを DONE の 1 語にする。"
    if request.role == "analyst_report":
        return (
            f"段: analyst_report（段 7）。カード: {card}。最後の版: {version}。"
            "out/comparison.md と out/report_text.yaml を書く。"
            "終わったら最後のメッセージを DONE の 1 語にする。"
        )
    return (
        f"確かめ役として、カード {card} の最後の版 {version} までの説明のつながりを確かめ、"
        "out/verdict.yaml を書く。終わったら最後のメッセージを DONE の 1 語にする。"
    )


# --- out/ の検査 ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Inspection:
    """`out/` の検査の結果（`problem` があれば出力なし）。"""

    files: Mapping[str, bytes]
    discarded: tuple[str, ...] = ()
    problem: str | None = None


def _bundle_problem(bundle: Path, manifest: Mapping[str, Any]) -> str | None:
    """束の中の写しが変わった・増えたなら、その短い説明（`out/` の外への書き込み）。"""
    expected: dict[str, str] = {
        str(item["path"]): str(item["sha256"]) for item in manifest.get("files", [])
    }
    agent = manifest.get("agent") or {}
    expected[str(agent.get("path"))] = str(agent.get("sha256"))
    for path, digest in expected.items():
        target = bundle / path
        try:
            mode = os.lstat(target).st_mode
        except OSError:
            return f"写しが消えた: {path}"
        if not stat.S_ISREG(mode) or _sha(target.read_bytes()) != digest:
            return f"写しが変わった: {path}"
    for place in (*PLACES, ".claude"):
        base = bundle / place
        if not os.path.lexists(base):
            continue
        for root, dirs, names in os.walk(base, followlinks=False):
            for name in [*dirs, *names]:
                full = Path(root) / name
                relative = full.relative_to(bundle).as_posix()
                mode = os.lstat(full).st_mode
                if stat.S_ISLNK(mode):
                    return f"写しの置き場にリンク: {relative}"
                if stat.S_ISREG(mode) and relative not in expected:
                    return f"写しの置き場に増えたファイル: {relative}"
    return None


def inspect_out(bundle: Path, role: str) -> Inspection:
    """束の `out/` の形を検査する（D25 §5.6。中身の良し悪しは判断しない）。"""
    try:
        manifest = json.loads((bundle / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Inspection({}, problem="manifest.json を読めない")
    if not isinstance(manifest, dict):
        return Inspection({}, problem="manifest.json が mapping でない")
    problem = _bundle_problem(bundle, manifest)
    if problem is not None:
        return Inspection({}, problem=problem)
    out = bundle / OUT_DIR
    try:
        if not stat.S_ISDIR(os.lstat(out).st_mode):
            return Inspection({}, problem="out/ がディレクトリでない")
    except OSError:
        return Inspection({}, problem="out/ が無い")
    found: dict[str, Path] = {}
    total = 0
    for root, dirs, names in os.walk(out, followlinks=False):
        for name in [*dirs, *names]:
            full = Path(root) / name
            relative = full.relative_to(out).as_posix()
            mode = os.lstat(full).st_mode
            if stat.S_ISLNK(mode):
                return Inspection({}, problem=f"out/ の中にリンク: {relative}")
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                return Inspection({}, problem=f"out/ の中に普通でないファイル: {relative}")
            size = os.lstat(full).st_size
            total += size
            if size > MAX_FILE_BYTES:
                return Inspection({}, problem=f"大きすぎるファイル: {relative}")
            found[relative] = full
    if total > MAX_OUT_BYTES or len(found) > MAX_OUT_FILES:
        return Inspection({}, problem="out/ の合計が上限を超える")
    wanted = set(REQUIRED_OUTPUTS[role])
    kept: dict[str, bytes] = {}
    discarded: list[str] = []
    for relative, full in sorted(found.items()):
        if relative in wanted or (role == "ideator" and _CARD_FILE.fullmatch(relative)):
            kept[relative] = full.read_bytes()
        else:
            discarded.append(relative)
    missing = sorted(wanted - set(kept))
    if missing:
        return Inspection({}, tuple(discarded), problem=f"決まったファイルが無い: {missing}")
    return Inspection(kept, tuple(discarded))


def _yaml(raw: bytes, name: str) -> dict[str, Any] | None:
    try:
        return load_yaml_mapping(Path(name), text=raw.decode("utf-8"))
    except (ConfigError, UnicodeDecodeError):
        return None


def _text(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def with_memo_sha256(raw_change: bytes, memo: bytes) -> bytes | None:
    """変更の提案に、実行器が分析メモから計算した `memo_sha256` を入れる（決定 237）。

    分析役が書いた `memo_sha256` は捨てる（使わない）。読めない・mapping でないなら `None`。
    """
    data = _yaml(raw_change, "change.yaml")
    if data is None:
        return None
    data.pop("memo_sha256", None)
    data["memo_sha256"] = _sha(memo)
    try:
        return dump_document(data)
    except ToolFailure:
        return None


def verdict_of(raw: bytes, card_id: str) -> VerdictOutput | None:
    """確かめ役の判定ファイル（#200 の仮置き 6 の書式）。形が合わなければ `None`（出力なし）。"""
    data = _yaml(raw, "verdict.yaml")
    if data is None or set(data) != {"schema_version", "card", "result", "checks", "reasons"}:
        return None
    if data["schema_version"] != 1 or isinstance(data["schema_version"], bool):
        return None
    checks = data["checks"]
    reasons = data["reasons"]
    if data["card"] != card_id or data["result"] not in {"PASS", "FAIL"}:
        return None
    if not isinstance(checks, dict) or set(checks) != set(VERDICT_CHECKS):
        return None
    if any(value not in {"PASS", "FAIL"} for value in checks.values()):
        return None
    if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
        return None
    failed = {VERDICT_CHECKS[name] for name, value in checks.items() if value == "FAIL"}
    passed = not failed
    # 合否と理由がそろっている（全部 PASS なら PASS で理由は空、FAIL なら FAIL の項目の理由だけ）。
    if (
        (data["result"] == "PASS") != passed
        or set(reasons) != failed
        or len(set(reasons)) != len(reasons)
    ):
        return None
    return VerdictOutput(passed=passed, reasons=tuple(sorted(reasons)))


@dataclass(frozen=True, slots=True)
class _Report:
    prediction: str
    reasons: tuple[str, ...]
    contents: tuple[str, ...]


def _report_text(raw: bytes, card_id: str) -> _Report | None:
    data = _yaml(raw, REPORT_TEXT)
    keys = {"schema_version", "card", "prediction", "change_reasons", "change_contents"}
    if data is None or set(data) != keys:
        return None
    if data["schema_version"] != 1 or isinstance(data["schema_version"], bool):
        return None
    if data["card"] != card_id:
        return None
    prediction = data["prediction"]
    if not isinstance(prediction, str) or not prediction.strip():
        return None
    lists = []
    for key in ("change_reasons", "change_contents"):
        value = data[key]
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            return None
        lists.append(tuple(value))
    return _Report(prediction, lists[0], lists[1])


# --- 実物 ---------------------------------------------------------------------------------


@dataclass
class ClaudeWorkers:
    """作業役の口の実物。`repo` は人間の主の作業木（`runs/` の根）、`view` は `origin/main` の時点。

    束は `runs/runner/bundles/<起動の識別子>/`、包みの状態（PID・終了コード・結果の JSON）は束の
    外の `runs/runner/workers/<起動の識別子>/` に置く（作業役から見えない）。
    """

    repo: Path
    view: Callable[[], RepoView]
    procs: Processes
    clock: Clock
    odyssey: tuple[str, ...] = ("uv", "run", "--frozen", "odyssey-fx")
    program: str = "claude"
    #: 研究ポリシーの現行の版のファイル（登録簿の用途 `STANDARD` の最大の版。仮置き 10）。
    policy_path: Callable[[RepoView], str] = field(default=lambda view: select_policy(view).path)

    @property
    def runs(self) -> Path:
        return self.repo / "runs"

    def _launch_id(self, request: WorkerRequest) -> str:
        return (
            f"{request.card_id or 'ideator'}--{request.role}"
            f"--v{request.version or 0}--a{request.attempt}"
        )

    def bundle_dir(self, request: WorkerRequest) -> Path:
        return self.runs / "runner" / "bundles" / self._launch_id(request)

    def state_dir(self, request: WorkerRequest) -> Path:
        return self.runs / "runner" / "workers" / self._launch_id(request)

    def _scope(self, view: RepoView, request: WorkerRequest) -> BundleScope:
        if request.role == "ideator":
            return BundleScope(role="ideator")
        card_id = request.card_id
        if card_id is None or request.version is None or request.version < 1:
            raise WorkerUnavailable(f"作業役 {request.role} にカードと版が無い")
        raw = None
        for place in ("taken", "done", "queue"):
            try:
                raw = view.read(card_path(place, card_id))
            except ValueError:
                raw = None
            if raw is not None:
                break
        if raw is None:
            raise WorkerUnavailable(f"カード {card_id} が origin/main に無い")
        experiment = parse_card(raw).experiment_id
        return BundleScope(
            role=request.role,
            card_id=card_id,
            experiment=experiment,
            versions=tuple(range(1, request.version + 1)),
        )

    def launch(self, request: WorkerRequest) -> None:
        agent = AGENT_OF_ROLE.get(request.role)
        if agent is None:
            raise WorkerUnavailable(f"役 {request.role} の定義が無い")
        try:
            view = self.view()
            definition = view.read(f".claude/agents/{agent}.md")
            if definition is None:
                raise WorkerUnavailable(f"定義 .claude/agents/{agent}.md が origin/main に無い")
            scope = self._scope(view, request)
            policy = self.policy_path(view)
            plan = plan_bundle(scope, view=view, runs=self.runs, policy_path=policy)
        except (ToolFailure, ValueError) as exc:
            raise WorkerUnavailable(f"束を作れない: {exc}") from exc
        check_plan(plan)  # 一覧の外が要れば、写す前に `BundleOutOfScope`
        now = format_time(self.clock.now())
        command = worker_command(self.program, agent, worker_prompt(request, now=now))
        unlisted = unlisted_command(command, self.odyssey)
        if unlisted is not None:
            raise CommandNotAllowed(unlisted)
        bundle = self.bundle_dir(request)
        state = self.state_dir(request)
        try:
            write_bundle(plan, target=bundle, agent=agent, definition=definition)
            if os.path.lexists(state):
                if stat.S_ISLNK(os.lstat(state).st_mode):
                    raise ToolFailure("作業役の状態の置き場がリンク")
                for child in state.iterdir():
                    child.unlink()
            state.mkdir(parents=True, exist_ok=True)
            (state / "request.json").write_text(
                json.dumps(
                    {
                        "role": request.role,
                        "attempt": request.attempt,
                        "card": request.card_id,
                        "version": request.version,
                        "experiment": scope.experiment,
                        "launched_at": now,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise WorkerUnavailable(f"束を写せない: {exc}") from exc
        self.procs.spawn(command, cwd=bundle, state_dir=state)

    # --- 様子 ---------------------------------------------------------------------------

    def _read(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None

    def poll(self, request: WorkerRequest) -> WorkerPoll:
        state = self.state_dir(request)
        meta_text = self._read(state / "request.json")
        if meta_text is None:
            return WorkerPoll(WorkerStatus.LOST)
        try:
            meta = json.loads(meta_text)
        except ValueError:
            return WorkerPoll(WorkerStatus.LOST)
        code_text = self._read(state / "exit_code.txt")
        if code_text is None:
            pid_text = self._read(state / "pid.txt")
            if pid_text is not None and pid_text.isdigit():
                alive = self.procs.alive(int(pid_text))
                return WorkerPoll(WorkerStatus.RUNNING if alive else WorkerStatus.LOST)
            launched = parse_time(str(meta.get("launched_at")))
            within = self.clock.now() - launched < LAUNCH_GRACE
            return WorkerPoll(WorkerStatus.RUNNING if within else WorkerStatus.LOST)
        try:
            code = int(code_text)
        except ValueError:
            return WorkerPoll(WorkerStatus.FAILED, reason="終了コードを読めない")
        ended = self._ending(state / "output.log", code)
        if ended is not None:
            return ended
        inspection = inspect_out(self.bundle_dir(request), request.role)
        self._record(state, inspection)
        if inspection.problem is not None:
            return WorkerPoll(WorkerStatus.FAILED, reason=inspection.problem)
        return self._output(request, meta, inspection)

    def _ending(self, log: Path, code: int) -> WorkerPoll | None:
        """結果の JSON の終わり方（成功なら `None`）。作業役の文の中身は読まない（形だけ）。"""
        try:
            with log.open("rb") as handle:
                raw = handle.read(_LOG_READ_BYTES)
        except OSError:
            raw = b""
        text = raw.decode("utf-8", errors="replace")
        try:
            envelope = json.loads(text)
        except ValueError:
            envelope = None
        error = code != 0 or not isinstance(envelope, dict)
        if isinstance(envelope, dict):
            error = error or bool(envelope.get("is_error"))
            error = error or envelope.get("subtype") not in (None, "success")
        if not error:
            return None
        if isinstance(envelope, dict):
            said = " ".join(
                str(envelope.get(key) or "")
                for key in ("subtype", "result", "error", "api_error_status")
            )
        else:
            said = text
        if _USAGE_LIMIT.search(said):
            return WorkerPoll(WorkerStatus.USAGE_LIMIT)
        return WorkerPoll(
            WorkerStatus.FAILED, reason=f"作業役が誤りで終わった（終了コード {code}）"
        )

    def _record(self, state: Path, inspection: Inspection) -> None:
        """検査の結果（捨てたファイルの名前と問題の短い説明だけ）を状態の置き場に残す。"""
        try:
            (state / "inspection.json").write_text(
                json.dumps(
                    {"discarded": list(inspection.discarded), "problem": inspection.problem},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def _analysis_dir(self, experiment: str, version: int) -> Path:
        """`runs/experiments/<実験>/v<版>/analysis/`（途中のリンクは道具の失敗。写さない）。"""
        base = self.runs / "experiments" / experiment / f"v{version}" / ANALYSIS_DIR
        current = self.repo
        for part in base.relative_to(self.repo).parts:
            current = current / part
            if os.path.lexists(current):
                mode = os.lstat(current).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ToolFailure(f"分析の置き場の途中がディレクトリでない: {part}")
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _keep(self, experiment: str, version: int, files: Mapping[str, bytes]) -> dict[str, str]:
        """分析役の出力を版のディレクトリの `analysis/` に写し、リポジトリの根からのパスを返す。"""
        base = self._analysis_dir(experiment, version)
        placed: dict[str, str] = {}
        for name, data in files.items():
            target = base / name
            if os.path.lexists(target) and stat.S_ISLNK(os.lstat(target).st_mode):
                raise ToolFailure(f"分析の置き場の {name} がリンク")
            tmp = base / f".{name}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, target)
            placed[name] = target.relative_to(self.repo).as_posix()
        return placed

    def _output(
        self, request: WorkerRequest, meta: Mapping[str, Any], inspection: Inspection
    ) -> WorkerPoll:
        files = inspection.files
        card_id = request.card_id or ""
        experiment = str(meta.get("experiment") or "")
        version = int(request.version or 0)
        output: WorkerOutput
        if request.role == "ideator":
            manifest = json.loads((self.bundle_dir(request) / MANIFEST).read_text(encoding="utf-8"))
            read_paths = tuple(str(item["path"]) for item in manifest.get("files", []))
            cards = tuple(files[name] for name in sorted(files))
            return WorkerPoll(WorkerStatus.DONE, IdeatorOutput(cards=cards, read_paths=read_paths))
        if request.role == "verifier":
            verdict = verdict_of(files["verdict.yaml"], card_id)
            if verdict is None:
                return WorkerPoll(WorkerStatus.FAILED, reason="判定ファイルの形が合わない")
            return WorkerPoll(WorkerStatus.DONE, verdict)
        if request.role == "analyst_report":
            comparison = files["comparison.md"]
            report = _report_text(files[REPORT_TEXT], card_id)
            if _text(comparison) is None or report is None:
                return WorkerPoll(WorkerStatus.FAILED, reason="比較表・報告の文の形が合わない")
            texts = (report.prediction, *report.reasons, *report.contents)
            if any(has_digit(text) for text in texts):
                return WorkerPoll(WorkerStatus.REJECTED, reason="CHANGE_OUT_OF_RULES")
            placed = self._keep(
                experiment, version, {"comparison.md": comparison, REPORT_TEXT: files[REPORT_TEXT]}
            )
            output = ReportOutput(
                prediction=report.prediction,
                change_reasons=report.reasons,
                change_contents=report.contents,
                comparison_path=placed["comparison.md"],
                comparison_sha256=_sha(comparison),
            )
            return WorkerPoll(WorkerStatus.DONE, output)
        # analyst_change
        memo = files["analysis_memo.md"]
        data = _yaml(files["change.yaml"], "change.yaml")
        if _text(memo) is None or data is None or data.get("decision") not in _DECISIONS:
            return WorkerPoll(WorkerStatus.FAILED, reason="分析メモ・変更の提案の形が合わない")
        raw = with_memo_sha256(files["change.yaml"], memo)
        if raw is None:
            return WorkerPoll(WorkerStatus.FAILED, reason="変更の提案を書き直せない")
        try:
            change = parse_change(raw, card_id=card_id, expected_from_version=version)
        except ChangeRejected as exc:
            return WorkerPoll(WorkerStatus.REJECTED, reason=exc.reason)
        placed = self._keep(experiment, version, {"analysis_memo.md": memo})
        output = ChangeOutput(
            decision=change.decision,
            raw=raw,
            memo_path=placed["analysis_memo.md"],
            memo_sha256=change.memo_sha256,
            reason=change.reason_id if change.decision == "AMBIGUOUS" else None,
        )
        return WorkerPoll(WorkerStatus.DONE, output)
