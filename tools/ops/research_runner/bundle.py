"""作業役に渡す読む束（D25 §5.2、決定 152・177、ADR-0041 第2項）。

実行器は作業役を起動する前に `runs/runner/bundles/<起動の識別子>/` を作り、許可の一覧のファイル
だけを**写す**（リンクにしない）。どの役の束にも、封印データと `data/` の配下、2024 年以降のデータ、
最終検証・前向き検証の出力と台帳、手元の旧データの CSV、`console.log`、`docs/status/` を入れない。

手順は 3 段で、写すのは最後の段だけ:

1. **計画**（`plan_bundle`）: 役ごとに、D25 §5.2 の表の「写すもの」の出どころから、写す元と束の中の
   置き場の組を並べる。出どころは 2 つ: 版管理のファイル（`origin/main` の時点。`RepoView`）と、
   版管理の外の `runs/experiments/<実験>/v<版>/` の決まった名前のファイル。
2. **確かめ**（`check_plan`）: 計画の各組を**場所と名前だけで**判定する（中身を開かない）。役の表の
   外・写さないもの（封印・`data/`・最終検証と前向き検証・CSV など）が 1 つでもあれば、
   **何も写さずに** `BundleOutOfScope` を出す（D25 §4.6 の `SEALED_NEEDED`。PR #201 の
   仮置き F3 の約束）。
3. **写す**（`write_bundle`）: 束を作り直し、各ファイルをバイト列で書き（リンクを作らない）、定義を
   `.claude/agents/` に写し、`out/` を作り、`manifest.json`（パスと SHA-256）を書く。

`runs/` の下は、置き場までの途中にリンク（と普通でないファイル）が 1 つでもあれば、そのファイルを
写さない（先を読まない。写さない側に倒す。本 PR の仮置き G4）。実験名と版のディレクトリの名前が
字種に合わなければ、その置き場を見ない。

中身を読むのは、README の研究期間の節の切り出し（見出しだけで判定。D25 の仮置き 34）と、設計書の
第3.1節・第3.9節の切り出しだけである。成績の数値の入ったファイル（レポート・分析メモ・台帳）は
バイト列のまま写し、実行器は中身を読まない。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Final

from tools.ops.hypothesis_cards.generate import experiment_path
from tools.ops.hypothesis_cards.repo import (
    EXPERIMENTS_DIR,
    HYPOTHESES_DIR,
    LOOPS_DIR,
    STRATEGIES_DIR,
    RepoView,
    ToolFailure,
)
from tools.ops.research_runner.ports import BundleOutOfScope

__all__ = [
    "AGENT_OF_ROLE",
    "ANALYSIS_FILES",
    "BundlePlan",
    "BundleScope",
    "Entry",
    "MANIFEST",
    "OUT_DIR",
    "PLACES",
    "REPORT_TEXT",
    "check_plan",
    "forbidden_reason",
    "plan_bundle",
    "write_bundle",
]

#: 実行器の役（`WorkerRequest.role`）→ 定義の名前（D25 §5.1。分析役は段 5・段 7 の 2 つの役）。
AGENT_OF_ROLE: Final[Mapping[str, str]] = {
    "ideator": "ideator",
    "analyst_change": "analyst",
    "analyst_report": "analyst",
    "verifier": "verifier",
}
OUT_DIR: Final = "out"
MANIFEST: Final = "manifest.json"
#: 束の中の置き場（D25 §5.2 の表の行）。
PLACES: Final = ("ledger", "reports", "analysis", "reviews", "records", "catalog", "rules")
#: 役ごとに写す置き場（D25 §5.2 の表の「写さない」の行を除いたもの）。
ROLE_PLACES: Final[Mapping[str, frozenset[str]]] = {
    "ideator": frozenset(PLACES),
    "analyst_change": frozenset({"ledger", "reports", "analysis", "records", "catalog", "rules"}),
    "analyst_report": frozenset({"ledger", "reports", "analysis", "records", "catalog", "rules"}),
    "verifier": frozenset({"reports", "analysis", "records", "rules"}),
}
#: 分析役の出力を実行器が写す、版のディレクトリの中の置き場（D25 §2.3 の `analysis/`）。
ANALYSIS_DIR: Final = "analysis"
#: `analysis/` から束に写すファイル（分析メモ・比較表。D25 §5.2）。
ANALYSIS_FILES: Final = ("analysis_memo.md", "comparison.md")
#: 段 7 の報告の文（実行器が `analysis/` に写す。確かめ役の束では `records/` に置く。仮置き G6）。
REPORT_TEXT: Final = "report_text.yaml"
TRIAL_LEDGER: Final = "research/trial_ledger.jsonl"
REVIEW_LEDGER: Final = "research/adoption_reviews.jsonl"
D25_PATH: Final = "docs/design/D25_research_runner_and_queue.md"
#: 設計書から写す節（D25 §5.2 の `rules/` の行: 第3.1節・第3.9節）。
D25_SECTIONS: Final = ("3.1", "3.9")
#: README の研究期間の節だけを写す（見出しにこの語を含む節とその下の節は写さない。仮置き 34）。
README_EXCLUDED_WORDS: Final = ("最終検証", "前向き検証", "封印")
_EXPERIMENT_NAME: Final = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]*")
_VERSION_DIR: Final = re.compile(r"v([1-9][0-9]*)")
_CARD_ID: Final = re.compile(r"[a-z0-9][a-z0-9-]*")
_RESEARCH_LOOP_README: Final = re.compile(r"docs/data/[A-Za-z0-9_.-]+_research_loop/README\.md")
_HEADING: Final = re.compile(r"^(#{1,6})\s")


@dataclass(frozen=True, slots=True)
class BundleScope:
    """1 回の起動の束の範囲（役・カード・実験・版）。発想役はカードを持たない。"""

    role: str
    card_id: str | None = None
    experiment: str | None = None
    #: そのカードの版（1 から最後の版まで）。
    versions: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Entry:
    """写すファイル 1 つ。`dest` は束の中の相対パス、`origin` は出どころ（判定に使う）。

    `origin` は版管理のファイルなら `repo:<パス>`、`runs/` の下なら `runs:<runs からの相対パス>`、
    実行器が作るもの（部品のカタログ・設計書の節の写し）なら `made:<名前>`。
    """

    dest: str
    origin: str
    data: bytes | None = None
    source: Path | None = None
    #: `runs/` の根（`runs:` の出どころだけ）。写すときに根から 1 段ずつリンクを辿らずに開く。
    root: Path | None = None


@dataclass(frozen=True, slots=True)
class BundlePlan:
    scope: BundleScope
    entries: tuple[Entry, ...]
    #: 途中にリンクなどがあって写さなかった `runs/` の下の置き場（名前だけ。中身は読んでいない）。
    skipped: tuple[str, ...] = ()


# --- 判定（場所と名前だけ） ---------------------------------------------------------------


def forbidden_reason(origin: str) -> str | None:
    """出どころが「写さないもの（どの役割も）」（D25 §5.2 の表の最後の行）なら、その短い理由。"""
    kind, _, path = origin.partition(":")
    parts = PurePosixPath(path).parts
    lowered = path.lower()
    if kind not in {"repo", "runs", "made"}:
        return "出どころの種類が無い"
    if path.startswith("/") or ".." in parts or "" in path.split("/"):
        return "束の外に届くパス"
    if kind == "repo":
        if parts and parts[0] == "data":
            return "data/ の配下（封印データ・snapshot）"
        if path == "research/final_validations.jsonl":
            return "最終検証の台帳"
        if path.startswith("docs/status/"):
            return "docs/status/"
    if kind == "runs":
        # `experiments/<実験>/v<版>/final/` は最終検証の出力（D25 §5.2）。
        if "final" in parts or "forward" in parts or "forward_validation" in parts:
            return "最終検証・前向き検証の出力"
        if parts and parts[0] != "experiments":
            return "runs/experiments/ の外"
    if any(word in lowered for word in ("sealed", "final_validation", "forward_validation")):
        return "封印・最終検証・前向き検証の名前"
    if lowered.endswith(".csv"):
        return "CSV（手元の旧データ）"
    if PurePosixPath(path).name == "console.log":
        return "console.log"
    return None


def _allowed(scope: BundleScope, entry: Entry) -> str | None:
    """`entry` がこの役の許可の一覧にあるか。無ければ理由（場所と名前だけで判定）。"""
    found = forbidden_reason(entry.origin)
    if found is not None:
        return found
    dest = PurePosixPath(entry.dest)
    if entry.dest.startswith("/") or ".." in dest.parts or not dest.parts:
        return "束の中の置き場が束の外に届く"
    place = dest.parts[0]
    if place not in ROLE_PLACES.get(scope.role, frozenset()):
        return f"この役に写さない置き場 {place}/"
    kind, _, path = entry.origin.partition(":")
    if place == "ledger":
        return (
            None
            if (kind, path) in {("repo", TRIAL_LEDGER), ("repo", REVIEW_LEDGER)}
            else ("台帳の置き場に台帳でないもの")
        )
    if place in {"reports", "analysis", "reviews"}:
        if kind != "runs":
            return f"{place}/ は runs/experiments/ の下から写す"
        parts = PurePosixPath(path).parts
        if len(parts) < 4 or not _VERSION_DIR.fullmatch(parts[2]):
            return f"{place}/ の出どころが版のディレクトリでない"
        mine = scope.experiment is not None and parts[1] == scope.experiment
        if scope.role != "ideator" and not mine:
            return "ほかのカードの実験"
        name = parts[-1]
        if place == "reports":
            return None if len(parts) == 4 and name == "report.md" else "レポートでない"
        if place == "analysis":
            return (
                None
                if len(parts) == 5 and parts[3] == ANALYSIS_DIR and name in ANALYSIS_FILES
                else "分析メモ・比較表でない"
            )
        return (
            None
            if len(parts) == 6 and parts[3] == "review" and name == "review.md"
            else ("審査の review.md でない")
        )
    if place == "records":
        return _allowed_record(scope, kind, path)
    if place == "catalog":
        return None if entry.origin == "made:catalog" else "カタログでない"
    # rules/
    if kind == "made" and path.startswith("d25_"):
        return None
    return (
        None
        if kind == "repo" and path.startswith("configs/policies/research/")
        else ("研究ポリシー・設計書の節でない")
    )


def _allowed_record(scope: BundleScope, kind: str, path: str) -> str | None:
    if kind == "runs":
        parts = PurePosixPath(path).parts
        if (
            scope.role == "verifier"
            and len(parts) == 5
            and parts[1] == scope.experiment
            and parts[3] == ANALYSIS_DIR
            and parts[4] == REPORT_TEXT
        ):
            return None
        return "記録の置き場に runs/ のもの"
    if kind != "repo":
        return "記録の出どころでない"
    card = scope.card_id
    if scope.role == "ideator":
        if path.startswith((f"{LOOPS_DIR}/", f"{HYPOTHESES_DIR}/")):
            return None
        if path.startswith((f"{STRATEGIES_DIR}/", f"{EXPERIMENTS_DIR}/")):
            return None
        return None if _RESEARCH_LOOP_README.fullmatch(path) else "数値なしの記録でない"
    if card is None or scope.experiment is None:
        return "カードの無い束"
    if path.startswith(f"{LOOPS_DIR}/{card}/"):
        return None
    if any(path == f"{HYPOTHESES_DIR}/{place}/{card}.yaml" for place in ("queue", "taken", "done")):
        return None
    if path == f"{HYPOTHESES_DIR}/done/{card}.outcome.yaml":
        return None
    if path in {experiment_path(scope.experiment, version) for version in scope.versions}:
        return None
    if path.startswith(f"{STRATEGIES_DIR}/{scope.experiment}_v") and path.endswith(".yaml"):
        return None
    return "このカードの記録でない"


def check_plan(plan: BundlePlan) -> None:
    """計画のすべてが役の許可の一覧にあることを確かめる。無ければ写す前に `BundleOutOfScope`。"""
    if plan.scope.role not in AGENT_OF_ROLE:
        raise BundleOutOfScope(f"役 {plan.scope.role} の許可の一覧が無い")
    seen: set[str] = set()
    for entry in plan.entries:
        reason = _allowed(plan.scope, entry)
        if reason is not None:
            raise BundleOutOfScope(f"{entry.dest}: {reason}")
        if entry.dest in seen:
            raise BundleOutOfScope(f"{entry.dest}: 同じ置き場に 2 つ写す")
        seen.add(entry.dest)


# --- 計画 -------------------------------------------------------------------------------


def _plain_runs_file(runs: Path, relative: str) -> Path | None:
    """`runs/<relative>` が、途中にリンクの無い普通のファイルで、ほかの名前からの hardlink でない
    （リンクの数が 1）なら、そのパス（中身は開かない。hardlink は後追いの PR の仮置き H8）。"""
    current = runs
    try:
        if stat.S_ISLNK(os.lstat(current).st_mode):
            return None
        info = os.lstat(current)
        for part in PurePosixPath(relative).parts:
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                return None
        return current if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 else None
    except OSError:
        return None


_DIR_FLAGS: Final = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE_FLAGS: Final = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


def read_plain_runs_file(root: Path, relative: str) -> bytes | None:
    """`runs/<relative>` を、根から 1 段ずつリンクを辿らずに開いて読む（後追いの PR の仮置き H8）。

    計画の段の `lstat` から読むまでの間に、途中のディレクトリやファイルがリンクに差し替えられて
    いたら開けない（`O_NOFOLLOW`）。開いたものが普通のファイルでない・リンクの数が 1 でない
    （hardlink）なら読まない。どれでも `None`（写さない側）。
    """
    parts = PurePosixPath(relative).parts
    if not parts or ".." in parts or relative.startswith("/"):
        return None
    opened: list[int] = []
    try:
        current = os.open(root, _DIR_FLAGS)
        opened.append(current)
        for part in parts[:-1]:
            current = os.open(part, _DIR_FLAGS, dir_fd=current)
            opened.append(current)
        handle = os.open(parts[-1], _FILE_FLAGS, dir_fd=current)
        opened.append(handle)
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        chunks: list[bytes] = []
        while chunk := os.read(handle, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _plain_dir(path: Path) -> bool:
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _experiment_versions(runs: Path, experiment: str) -> list[tuple[int, str]]:
    base = runs / "experiments" / experiment
    if not _plain_dir(runs) or not _plain_dir(runs / "experiments") or not _plain_dir(base):
        return []
    found: list[tuple[int, str]] = []
    for child in base.iterdir():
        matched = _VERSION_DIR.fullmatch(child.name)
        if matched is not None and _plain_dir(child):
            found.append((int(matched.group(1)), child.name))
    return sorted(found)


def _experiments(runs: Path) -> list[str]:
    base = runs / "experiments"
    if not _plain_dir(runs) or not _plain_dir(base):
        return []
    return sorted(
        child.name
        for child in base.iterdir()
        if _EXPERIMENT_NAME.fullmatch(child.name) and _plain_dir(child)
    )


def _runs_entries(
    runs: Path, experiments: Sequence[str], places: frozenset[str], versions: frozenset[int] | None
) -> tuple[list[Entry], list[str]]:
    entries: list[Entry] = []
    skipped: list[str] = []

    def add(dest: str, relative: str) -> None:
        source = _plain_runs_file(runs, relative)
        if source is not None:
            entries.append(Entry(dest=dest, origin=f"runs:{relative}", source=source, root=runs))
        elif os.path.lexists(runs / relative):
            skipped.append(relative)

    for experiment in experiments:
        for number, name in _experiment_versions(runs, experiment):
            if versions is not None and number not in versions:
                continue
            base = f"experiments/{experiment}/{name}"
            # 探索の実験だけ（版のディレクトリに `search/` がある。名前で判定。D25 §5.2）。
            if "reports" in places and _plain_dir(runs / base / "search"):
                add(f"reports/{experiment}/{name}/report.md", f"{base}/report.md")
            if "analysis" in places:
                for file in ANALYSIS_FILES:
                    add(f"analysis/{experiment}/{name}/{file}", f"{base}/{ANALYSIS_DIR}/{file}")
            review_base = runs / base / "review"
            if "reviews" in places and _plain_dir(review_base):
                for child in sorted(review_base.iterdir(), key=lambda item: item.name):
                    if re.fullmatch(r"[0-9a-f]{8,128}", child.name) and _plain_dir(child):
                        add(
                            f"reviews/{experiment}/{name}/{child.name}/review.md",
                            f"{base}/review/{child.name}/review.md",
                        )
    return entries, skipped


def _repo_entry(view: RepoView, place: str, path: str) -> Entry | None:
    data = view.read(path)
    if data is None:
        return None
    return Entry(dest=f"{place}/{path}", origin=f"repo:{path}", data=data)


def research_period_sections(text: str) -> str:
    """README から、見出しに「最終検証」「前向き検証」「封印」を含む節とその下の節を除く。

    D25 の仮置き 34（研究期間の節だけを写す）。"""
    kept: list[str] = []
    skipping: int | None = None
    fence = False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            fence = not fence
        matched = None if fence else _HEADING.match(line)
        if matched is not None:
            level = len(matched.group(1))
            if skipping is not None and level <= skipping:
                skipping = None
            if skipping is None and any(word in line for word in README_EXCLUDED_WORDS):
                skipping = level
        if skipping is None:
            kept.append(line)
    return "".join(kept)


def design_section(text: str, number: str) -> str | None:
    """設計書から `### <number> ` の節を、次の同じか上の見出しの前まで切り出す。"""
    lines = text.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.startswith(f"### {number} ")), None
    )
    if start is None:
        return None
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith(("### ", "## ")) or lines[index].rstrip() == "---":
            end = index
            break
    return "".join(lines[start:end])


def _plain(value: object, depth: int = 0) -> object:
    """部品の契約を、YAML に書ける素の値にする（名前・版・入力・パラメータの型と範囲）。"""
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Enum):
        return str(value.value) if isinstance(value.value, str | int) else value.name
    if hasattr(value, "canonical_str"):
        return str(value.canonical_str())
    if isinstance(value, Mapping):
        return {str(key): _plain(item, depth + 1) for key, item in sorted(value.items(), key=str)}
    if isinstance(value, tuple | list | frozenset | set):
        items = [_plain(item, depth + 1) for item in value]
        return items if isinstance(value, tuple | list) else sorted(items, key=str)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _plain(getattr(value, item.name), depth + 1)
            for item in fields(value)
            if item.name not in {"implementation_ref", "schema_version", "evaluate", "check"}
        }
    return str(value)


def catalog_document() -> bytes:
    """部品のカタログの一覧（D25 §5.2 の `catalog/`。実行器がコードから作る）。"""
    from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG

    components: list[dict[str, Any]] = []
    for key in sorted(
        INITIAL_CATALOG.registrations, key=lambda item: (item.component_id, item.version)
    ):
        contract = INITIAL_CATALOG.registrations[key].contract
        components.append(
            {
                "name": contract.component_id,
                "version": contract.version,
                "inputs": _plain(contract.inputs),
                "outputs": _plain(contract.outputs),
                "parameters": _plain(contract.parameters),
            }
        )
    text = json.dumps({"schema_version": 1, "components": components}, ensure_ascii=False, indent=1)
    return (text + "\n").encode("utf-8")


def plan_bundle(
    scope: BundleScope,
    *,
    view: RepoView,
    runs: Path,
    policy_path: str,
    catalog: bytes | None = None,
) -> BundlePlan:
    """役ごとの写す一覧（D25 §5.2 の表）。中身は開かない（README と設計書の節の切り出しを除く）。

    `view` は版管理のファイル（`origin/main` の時点）、`runs` は版管理の外の `runs/` の根、
    `policy_path` は研究ポリシーの現行の版のファイル（登録簿で選んだもの）。カードの束で実験名が
    字種に合わないときは、`runs/` の外に届きうるので `BundleOutOfScope`（写す前）。
    """
    if scope.role not in AGENT_OF_ROLE:
        raise BundleOutOfScope(f"役 {scope.role} の許可の一覧が無い")
    places = ROLE_PLACES[scope.role]
    entries: list[Entry] = []
    if scope.role != "ideator":
        if scope.card_id is None or not _CARD_ID.fullmatch(scope.card_id):
            raise BundleOutOfScope("カードの識別子が置き場の名前でない")
        if scope.experiment is None or not _EXPERIMENT_NAME.fullmatch(scope.experiment):
            raise BundleOutOfScope("実験名が置き場の名前でない（束の外に届きうる）")
    if "ledger" in places:
        for path in (TRIAL_LEDGER, REVIEW_LEDGER):
            found = _repo_entry(view, "ledger", path)
            if found is not None:
                entries.append(
                    Entry(f"ledger/{PurePosixPath(path).name}", found.origin, found.data)
                )
    experiments = _experiments(runs) if scope.role == "ideator" else [str(scope.experiment)]
    versions = None if scope.role == "ideator" else frozenset(scope.versions)
    found_runs, skipped = _runs_entries(runs, experiments, places, versions)
    entries.extend(found_runs)
    if scope.role == "verifier" and scope.versions:
        last = max(scope.versions)
        relative = f"experiments/{scope.experiment}/v{last}/{ANALYSIS_DIR}/{REPORT_TEXT}"
        source = _plain_runs_file(runs, relative)
        if source is not None:
            entries.append(
                Entry(f"records/{REPORT_TEXT}", f"runs:{relative}", source=source, root=runs)
            )
        elif os.path.lexists(runs / relative):
            skipped.append(relative)
    entries.extend(_record_entries(scope, view))
    if "catalog" in places:
        entries.append(
            Entry("catalog/components.json", "made:catalog", catalog or catalog_document())
        )
    entries.extend(_rule_entries(view, policy_path))
    return BundlePlan(scope=scope, entries=tuple(entries), skipped=tuple(sorted(skipped)))


def _record_entries(scope: BundleScope, view: RepoView) -> list[Entry]:
    paths: list[str] = []
    if scope.role == "ideator":
        for prefix in (
            f"{LOOPS_DIR}/",
            f"{HYPOTHESES_DIR}/",
            f"{STRATEGIES_DIR}/",
            f"{EXPERIMENTS_DIR}/",
        ):
            paths.extend(view.list(prefix))
        readmes = [
            path for path in view.list("docs/data/") if _RESEARCH_LOOP_README.fullmatch(path)
        ]
    else:
        card = str(scope.card_id)
        experiment = str(scope.experiment)
        paths.extend(view.list(f"{LOOPS_DIR}/{card}/"))
        paths.extend(
            f"{HYPOTHESES_DIR}/{place}/{card}.yaml" for place in ("queue", "taken", "done")
        )
        paths.append(f"{HYPOTHESES_DIR}/done/{card}.outcome.yaml")
        paths.extend(experiment_path(experiment, version) for version in scope.versions)
        paths.extend(
            path
            for path in view.list(f"{STRATEGIES_DIR}/")
            if path.startswith(f"{STRATEGIES_DIR}/{experiment}_v") and path.endswith(".yaml")
        )
        readmes = []
    entries: list[Entry] = []
    for path in dict.fromkeys(paths):
        found = _repo_entry(view, "records", path)
        if found is not None:
            entries.append(found)
    for path in readmes:
        data = view.read(path)
        if data is None:
            continue
        try:
            text = research_period_sections(data.decode("utf-8"))
        except UnicodeDecodeError:
            continue  # 読めない README は写さない（写さない側）
        entries.append(Entry(f"records/{path}", f"repo:{path}", text.encode("utf-8")))
    return entries


def _rule_entries(view: RepoView, policy_path: str) -> list[Entry]:
    entries: list[Entry] = []
    policy = _repo_entry(view, "rules", policy_path)
    if policy is None:
        raise ToolFailure(f"研究ポリシーの現行の版 {policy_path} が無い")
    entries.append(Entry(f"rules/{PurePosixPath(policy_path).name}", policy.origin, policy.data))
    design = view.read(D25_PATH)
    if design is None:
        raise ToolFailure(f"{D25_PATH} が無い")
    text = design.decode("utf-8")
    for number in D25_SECTIONS:
        section = design_section(text, number)
        if section is None:
            raise ToolFailure(f"{D25_PATH} に第{number}節が無い")
        name = f"d25_{number.replace('.', '_')}"
        entries.append(Entry(f"rules/{name}.md", f"made:{name}", section.encode("utf-8")))
    return entries


# --- 写す ---------------------------------------------------------------------------------


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_remove(target: Path) -> None:
    """前の束を消す（束の置き場そのものがリンクなら消さずに道具の失敗）。"""
    if not os.path.lexists(target):
        return
    if stat.S_ISLNK(os.lstat(target).st_mode) or not target.is_dir():
        raise ToolFailure(f"束の置き場 {target.name} がディレクトリでない")
    shutil.rmtree(target)


def write_bundle(
    plan: BundlePlan, *, target: Path, agent: str, definition: bytes
) -> dict[str, Any]:
    """確かめた計画を `target` に写し、`manifest.json` の中身を返す。

    写す前にもう一度 `check_plan` を当てる（確かめを通らない計画を写す経路を作らない）。前の束は
    消してから作り直す（同じ起動をやり直すとき、前の出力を捨てる。`Workers` の約束）。
    """
    check_plan(plan)
    _safe_remove(target)
    target.mkdir(parents=True)
    files: list[dict[str, str]] = []
    skipped = list(plan.skipped)
    for entry in plan.entries:
        data = entry.data
        if data is None:
            kind, _, relative = entry.origin.partition(":")
            if entry.source is None or entry.root is None or kind != "runs":
                raise ToolFailure(f"{entry.dest}: 写す元が無い")
            data = read_plain_runs_file(entry.root, relative)
            if data is None:
                # 計画の後にリンク・hardlink に変わった: 写さない側に倒し、名前だけ残す。
                skipped.append(relative)
                continue
        destination = target / entry.dest
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:  # 新しいファイルとして書く（リンクを辿らない）
            handle.write(data)
        files.append({"path": entry.dest, "sha256": _sha(data)})
    agents = target / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / f"{agent}.md").write_bytes(definition)
    (target / OUT_DIR).mkdir()
    if plan.scope.role == "ideator":
        (target / OUT_DIR / "cards").mkdir()
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "role": plan.scope.role,
        "card": plan.scope.card_id,
        "versions": list(plan.scope.versions),
        "agent": {"path": f".claude/agents/{agent}.md", "sha256": _sha(definition)},
        "files": sorted(files, key=lambda item: item["path"]),
        "skipped": sorted(set(skipped)),
    }
    (target / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return manifest


# --- 束の一覧の控え（束の外に置く。後追いの PR の項目 2） ------------------------------------


def _outside_out(relative: str) -> bool:
    return relative != OUT_DIR and not relative.startswith(f"{OUT_DIR}/")


def bundle_listing(bundle: Path) -> dict[str, Any]:
    """束の `out/` の中を除く全部（`manifest.json`・定義・束の直下と `.claude/` を含む）の一覧。

    ファイルはパスと SHA-256、ディレクトリはパスだけ。実行器は起動の前にこれを束の**外**（作業役の
    状態の置き場）に控え、作業役の後に `listing_problem` で比べる（束の中の `manifest.json` は
    作業役が書き換えうるので、比べる元にしない）。リンク・普通でないファイルがあれば道具の失敗。
    """
    files: dict[str, str] = {}
    dirs: list[str] = []
    for root, names_dirs, names in os.walk(bundle, followlinks=False):
        base = Path(root)
        for name in sorted([*names_dirs, *names]):
            full = base / name
            relative = full.relative_to(bundle).as_posix()
            mode = os.lstat(full).st_mode
            if stat.S_ISDIR(mode):
                dirs.append(relative)
            elif stat.S_ISREG(mode):
                if _outside_out(relative):
                    files[relative] = _sha(full.read_bytes())
            else:
                raise ToolFailure(f"束の中にリンク・普通でないファイル: {relative}")
        # `out/` の中は作業役の出力なので一覧に入れない（`out/` そのものは入れる）。
        names_dirs[:] = [
            name
            for name in names_dirs
            if _outside_out((base / name).relative_to(bundle).as_posix())
        ]
    return {
        "schema_version": 1,
        "files": dict(sorted(files.items())),
        "dirs": sorted(dirs),
    }


def listing_problem(bundle: Path, listing: Mapping[str, Any]) -> str | None:
    """束の `out/` の外が控えと違えば、その短い説明（書き込みの絞りが効いていない形跡）。

    見るもの: 控えのファイルが消えた・中身が変わった・普通のファイルでなくなった、控えに無い
    ファイル・ディレクトリが増えた（束の直下・`.claude/`・写しの置き場のどこでも）、控えの
    ディレクトリが消えた、リンク・普通でないファイルがある。`out/` の中だけは増えてよい。
    """
    expected_files = listing.get("files")
    expected_dirs = listing.get("dirs")
    if not isinstance(expected_files, dict) or not isinstance(expected_dirs, list):
        return "束の一覧の控えの形が違う"
    try:
        current = bundle_listing(bundle)
    except ToolFailure as exc:
        return str(exc)
    except OSError as exc:
        return f"束を読めない: {type(exc).__name__}"
    now_files: dict[str, str] = current["files"]
    now_dirs = set(current["dirs"])
    for path in sorted(set(expected_files) - set(now_files)):
        return f"写しが消えた: {path}"
    for path in sorted(set(now_files) - set(expected_files)):
        return f"束に増えたファイル: {path}"
    for path in sorted(expected_files):
        if now_files[path] != expected_files[path]:
            return f"写しが変わった: {path}"
    for path in sorted(set(map(str, expected_dirs)) - now_dirs):
        return f"束のディレクトリが消えた: {path}"
    for path in sorted(now_dirs - set(map(str, expected_dirs))):
        if _outside_out(path):
            return f"束に増えたディレクトリ: {path}"
    return None
