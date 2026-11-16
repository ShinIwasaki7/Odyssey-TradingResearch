"""検査 2（枝の名前と種類）・3（許されたパスだけ）・7（題と本文の型）・8（自動 merge の守り）。

D25 §6.2 の表（定型 PR の種類・枝・足す・変えるファイル・本文の型）と §6.3 の 2・3・7・8。
枝が `runner/` で始まる PR を定型 PR として扱う。枝の形・題の形・本文の行の形は本 PR の仮置き
（D25 §6.2 の字句を機械で読める形にしたもの）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from tools.ops.runner_ci.model import (
    ChangeStatus,
    CheckInput,
    CheckResult,
    PullRequest,
    Status,
    result,
    sha256_hex,
)
from tools.ops.runner_ci.records import (
    CARD_ID,
    HYPOTHESES,
    RecordKind,
    card_path,
    experiment_id_of,
    head_card,
    load,
    record_path,
)

RUNNER_PREFIX: Final = "runner/"
CANDIDATE_FIXES: Final = "research/candidate_fixes/"
LEDGER_PATHS: Final = (
    "research/trial_ledger.jsonl",
    "research/adoption_reviews.jsonl",
    "research/final_validations.jsonl",
)
#: 本文の定型の文（D25 §6.2）。
FIXED_SENTENCE: Final = "本 PR は実行器の定型 PR（ADR-0043）。成績の数値・判定の値を含まない。"


class Kind(Enum):
    """定型 PR の種類（ADR-0043 第2項の 3、D25 §6.2）。"""

    PREREG = "事前登録"
    EXEC = "実行記録"
    REPORT = "一巡の報告"


class Form(Enum):
    """枝の形（D25 §6.2 の「枝」の列）。"""

    PREREG = "runner/prereg-<カード>-v<版>"
    CARDS = "runner/cards-<時刻>"
    EXEC = "runner/exec"
    FINAL = "runner/final-<実験>-<実行番号>"
    REPORT = "runner/report-<カード>"


_KIND_OF: Final = {
    Form.PREREG: Kind.PREREG,
    Form.CARDS: Kind.PREREG,
    Form.EXEC: Kind.EXEC,
    Form.FINAL: Kind.EXEC,
    Form.REPORT: Kind.REPORT,
}
_STAMP: Final = r"[0-9]{8}T[0-9]{6}Z"
_BRANCHES: Final = (
    (Form.PREREG, re.compile(rf"^runner/prereg-(?P<card>{CARD_ID})-v(?P<version>[1-9][0-9]*)$")),
    (Form.CARDS, re.compile(rf"^runner/cards-(?P<stamp>{_STAMP})$")),
    (Form.EXEC, re.compile(r"^runner/exec$")),
    (
        Form.FINAL,
        re.compile(
            r"^runner/final-(?P<experiment>[A-Za-z0-9_][A-Za-z0-9_-]*)-(?P<execution>[1-9][0-9]*)$"
        ),
    ),
    (Form.REPORT, re.compile(rf"^runner/report-(?P<card>{CARD_ID})$")),
)
_TITLE: Final = re.compile(
    r"^\[runner\] (?P<kind>事前登録|実行記録|一巡の報告) "
    rf"(?P<card>{CARD_ID}) v(?P<version>0|[1-9][0-9]*)$"
)
_CARDS_TITLE: Final = re.compile(rf"^\[runner\] 事前登録 cards-(?P<stamp>{_STAMP})$")


@dataclass(frozen=True, slots=True)
class Routine:
    """読めた定型 PR（枝と題が整合したもの）。"""

    form: Form
    kind: Kind
    card: str | None
    version: int | None
    final_experiment: str | None = None


def parse_routine(pr: PullRequest) -> Routine | list[str] | None:
    """枝と題を読む。定型でなければ `None`、定型の枝だが読めなければ理由の並び。"""
    if not pr.branch.startswith(RUNNER_PREFIX):
        return None
    found = next(
        (
            (form, matched)
            for form, pattern in _BRANCHES
            if (matched := pattern.fullmatch(pr.branch)) is not None
        ),
        None,
    )
    if found is None:
        return [f"枝 {pr.branch} は `runner/` で始まるが、定型 PR の枝の形（D25 §6.2）でない"]
    form, branch = found
    if form is Form.CARDS:
        title = _CARDS_TITLE.fullmatch(pr.title)
        if title is None or title["stamp"] != branch["stamp"]:
            return [f"題が `[runner] 事前登録 cards-{branch['stamp']}` でない"]
        return Routine(form, Kind.PREREG, None, None)
    matched = _TITLE.fullmatch(pr.title)
    if matched is None:
        return ["題が `[runner] <種類> <カードの識別子> v<版>` の形でない（D25 §6.2）"]
    kind, card, version = Kind(matched["kind"]), matched["card"], int(matched["version"])
    problems: list[str] = []
    if kind is not _KIND_OF[form]:
        problems.append(f"題の種類「{kind.value}」が枝の種類「{_KIND_OF[form].value}」と違う")
    if form in {Form.PREREG, Form.REPORT} and card != branch["card"]:
        problems.append("題のカードの識別子が枝のものと違う")
    if form is Form.PREREG and version != int(branch["version"]):
        problems.append("題の版が枝の版と違う")
    if form is not Form.REPORT and version == 0:
        problems.append("版 0 は一巡の報告（版を走らせずに終えたカード）だけ")
    if problems:
        return problems
    experiment = branch["experiment"] if form is Form.FINAL else None
    return Routine(form, kind, card, version, experiment)


# --- 検査 2 ----------------------------------------------------------------------

BRANCH_NAME: Final = "枝の名前と種類（runner/ の枝は定型 PR）"


def check_branch(pr: PullRequest, routine: Routine | list[str] | None) -> CheckResult:
    if routine is None:
        return CheckResult(2, BRANCH_NAME, Status.PASS, ("定型でない PR として扱う",))
    if isinstance(routine, list):
        return result(2, BRANCH_NAME, routine)
    return CheckResult(
        2, BRANCH_NAME, Status.PASS, (f"定型 PR（{routine.kind.value}、{pr.branch}）",)
    )


# --- 検査 3 ----------------------------------------------------------------------

PATHS_NAME: Final = "許されたパスだけ（D25 §6.2 の表とカードの置き場の規則）"


@dataclass(frozen=True, slots=True)
class CardMove:
    """カードの置き場の移動（同じ名前のファイルの、削除と、中身が同じ追加の組）。"""

    card: str
    source: str
    target: str


def card_moves(given: CheckInput) -> tuple[CardMove, ...]:
    deleted: dict[str, str] = {}
    added: dict[str, str] = {}
    for change in given.changes:
        where = record_path(change.path)
        if where is None or where.kind is not RecordKind.CARD or where.place is None:
            continue
        if change.status is ChangeStatus.DELETED:
            deleted[where.card] = where.place
        elif change.status is ChangeStatus.ADDED:
            added[where.card] = where.place
    moves: list[CardMove] = []
    for card, source in sorted(deleted.items()):
        target = added.get(card)
        if target is None or given.base is None:
            continue
        before = given.base.read(card_path(source, card))
        after = given.head.read(card_path(target, card))
        if before is not None and before == after:
            moves.append(CardMove(card, source, target))
    return tuple(moves)


def _mouth(given: CheckInput, path: str) -> object:
    raw = given.head.read(path)
    record = None if raw is None else load(path, raw)
    return None if record is None or record.data is None else record.data.get("mouth")


def _non_routine_paths(given: CheckInput) -> list[str]:
    problems: list[str] = []
    for change in given.changes:
        where = record_path(change.path)
        if not change.path.startswith(HYPOTHESES):
            continue
        if where is not None and where.place in {"taken", "done"}:
            problems.append(f"{change.path}: 定型でない PR が taken/・done/ を変えている（凍結）")
        elif change.status is not ChangeStatus.DELETED and _mouth(given, change.path) == "ai":
            problems.append(
                f"{change.path}: 定型でない PR が AI の口（mouth: ai）のカードを足している"
            )
    return problems


def _experiment_id(given: CheckInput, card: str) -> str | None:
    located = head_card(given, card)
    symbol = None if located is None or located[1] is None else located[1].get("symbol")
    return experiment_id_of(card, symbol) if isinstance(symbol, str) else None


def _routine_paths(given: CheckInput, routine: Routine) -> list[str]:
    problems: list[str] = []
    moves = {move.card: move for move in card_moves(given)}
    card, version = routine.card, routine.version
    allowed: set[str] = set()
    required: list[str] = []
    for move in moves.values():
        expected = (
            routine.form is Form.PREREG
            and version == 1
            and move.source == "queue"
            and move.target == "taken"
        ) or (
            routine.form is Form.REPORT
            and move.source in {"queue", "taken"}
            and move.target == "done"
        )
        if move.card == card and expected:
            allowed |= {card_path(move.source, move.card), card_path(move.target, move.card)}
    if routine.form in {Form.PREREG, Form.CARDS}:
        for change in given.changes:
            where = record_path(change.path)
            if (
                change.status is ChangeStatus.ADDED
                and where is not None
                and where.kind is RecordKind.CARD
                and where.place == "queue"
                and where.card not in moves
            ):
                if _mouth(given, change.path) == "ai":
                    allowed.add(change.path)
                else:
                    problems.append(f"{change.path}: 定型 PR が人間の口のカードを足している")
    if routine.form is Form.CARDS and not allowed:
        problems.append("カードだけの事前登録の PR に、足した AI の口のカードが無い")
    if routine.form is Form.PREREG and card is not None and version is not None:
        loops = f"research/loops/{card}/"
        if head_card(given, card) is None or given.head.read(card_path("taken", card)) is None:
            problems.append(
                f"カード {card} が taken/ に無い（事前登録はカードを taken/ に移してから）"
            )
        allowed.add(f"{loops}prereg_v{version}.yaml")
        required.append(f"{loops}prereg_v{version}.yaml")
        if version >= 2:
            allowed.add(f"{loops}change_v{version}.yaml")
            required.append(f"{loops}change_v{version}.yaml")
        experiment = _experiment_id(given, card)
        if experiment is not None:
            allowed.add(f"configs/experiments/{experiment}.yaml")
            strategies = re.compile(
                rf"^configs/strategies/{re.escape(experiment)}_v[1-9][0-9]*\.yaml$"
            )
            allowed |= {
                change.path for change in given.changes if strategies.fullmatch(change.path)
            }
    if routine.form in {Form.EXEC, Form.FINAL} and card is not None:
        allowed |= set(LEDGER_PATHS)
        allowed.add(f"research/loops/{card}/run_v{routine.version}.yaml")
    if routine.form is Form.REPORT and card is not None:
        allowed.add(f"research/loops/{card}/report.yaml")
        allowed.add(f"{HYPOTHESES}done/{card}.outcome.yaml")
        required.append(f"{HYPOTHESES}done/{card}.outcome.yaml")
        if card not in moves or moves[card].target != "done":
            problems.append(f"カード {card} の queue/ か taken/ から done/ への移動が無い")
    sources = {card_path(move.source, move.card) for move in moves.values()}
    changed = {change.path: change for change in given.changes}
    for path, change in changed.items():
        if path.startswith(CANDIDATE_FIXES):
            problems.append(
                f"{path}: 候補の固定の記録は定型 PR に入れない（人間が merge する関所 1）"
            )
        elif path not in allowed:
            problems.append(f"{path}: この種類の定型 PR に許されたパスでない")
        elif change.status is ChangeStatus.DELETED and path not in sources:
            problems.append(f"{path}: 定型 PR がファイルを消している（カードの移動のほか）")
        elif path in LEDGER_PATHS and change.status is not ChangeStatus.MODIFIED:
            problems.append(f"{path}: 台帳は追記（変更）だけ")
    for path in required:
        if path not in changed or changed[path].status is ChangeStatus.DELETED:
            problems.append(f"{path}: この種類の定型 PR に要るファイルが無い")
    return problems


def check_paths(given: CheckInput, routine: Routine | list[str] | None) -> CheckResult:
    if isinstance(routine, list):
        return result(3, PATHS_NAME, ["定型 PR の枝・題が読めないので、許されたパスを決められない"])
    if routine is None:
        return result(3, PATHS_NAME, _non_routine_paths(given), ("定型でない PR のカードの規則",))
    return result(3, PATHS_NAME, _routine_paths(given, routine))


# --- 検査 7 ----------------------------------------------------------------------

BODY_NAME: Final = "定型 PR の題と本文の型（決まった項目のほかの文が無い）"
_SHA_LINE: Final = re.compile(r"^- (?P<path>\S+): (?P<sha>[0-9a-f]{64})$")
_COUNT_ITEM: Final = re.compile(r"^(?:試行|行|カード) (?:0|[1-9][0-9]*)$")
_UTC: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_EXPERIMENT: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _body_lines(body: str) -> list[str]:
    return [line.rstrip() for line in body.replace("\r\n", "\n").split("\n") if line.strip()]


def check_body(
    given: CheckInput, pr: PullRequest, routine: Routine | list[str] | None
) -> CheckResult:
    if routine is None:
        return CheckResult(7, BODY_NAME, Status.NOT_APPLICABLE, ("定型でない PR",))
    if isinstance(routine, list):
        return result(7, BODY_NAME, ["題が定型 PR の型でない（検査 2）"])
    problems: list[str] = []
    seen: dict[str, str] = {}
    hashes: dict[str, str] = {}
    in_hashes = False
    for number, line in enumerate(_body_lines(pr.body), start=1):
        if line == FIXED_SENTENCE:
            key, value = "定型の文", line
        elif line == "SHA-256:":
            key, value, in_hashes = "SHA-256", "", True
        elif in_hashes and (sha := _SHA_LINE.fullmatch(line)) is not None:
            hashes[sha["path"]] = sha["sha"]
            continue
        elif ": " in line and line.split(": ", 1)[0] in {
            "種類",
            "カード",
            "版",
            "実験",
            "時刻",
            "件数",
        }:
            key, value = line.split(": ", 1)
            in_hashes = False
        else:
            problems.append(f"本文の {number} 行目が型の項目でない")
            continue
        if key in seen:
            problems.append(f"本文の項目「{key}」が 2 回ある")
        seen[key] = value
    problems += _body_values(given, routine, seen, hashes)
    return result(7, BODY_NAME, problems)


def _body_values(
    given: CheckInput, routine: Routine, seen: dict[str, str], hashes: dict[str, str]
) -> list[str]:
    problems: list[str] = []
    required = ["種類", "カード", "時刻", "定型の文", "SHA-256"]
    if routine.form is not Form.CARDS:
        required.append("版")
    problems += [f"本文に項目「{key}」が無い" for key in required if key not in seen]
    if "種類" in seen and seen["種類"] != routine.kind.value:
        problems.append("本文の「種類」が題と違う")
    if routine.form is Form.CARDS:
        added = sorted(
            where.card
            for change in given.changes
            if change.status is ChangeStatus.ADDED
            and (where := record_path(change.path)) is not None
            and where.kind is RecordKind.CARD
        )
        if "カード" in seen and seen["カード"].split(", ") != added:
            problems.append(
                "本文の「カード」が足したカードの識別子の並び（辞書順、`, ` 区切り）でない"
            )
    elif "カード" in seen and seen["カード"] != routine.card:
        problems.append("本文の「カード」が題と違う")
    if "版" in seen and seen["版"] != str(routine.version):
        problems.append("本文の「版」が題と違う")
    if "時刻" in seen and _UTC.fullmatch(seen["時刻"]) is None:
        problems.append("本文の「時刻」が UTC の時刻の形でない")
    if "実験" in seen:
        problems += _experiment_value(given, routine, seen["実験"])
    if "件数" in seen and not all(_COUNT_ITEM.fullmatch(item) for item in seen["件数"].split("、")):
        problems.append("本文の「件数」が `試行 <数>、行 <数>` の形でない")
    expected = {
        change.path: given.head.read(change.path)
        for change in given.changes
        if change.status is not ChangeStatus.DELETED
    }
    if set(hashes) != set(expected):
        problems.append("本文の SHA-256 の並びが、足した・変えたファイルの全部と一致しない")
    for path, sha in sorted(hashes.items()):
        data = expected.get(path)
        if data is not None and sha256_hex(data) != sha:
            problems.append(f"本文の {path} の SHA-256 がファイルと違う")
    return problems


def _experiment_value(given: CheckInput, routine: Routine, value: str) -> list[str]:
    if _EXPERIMENT.fullmatch(value) is None:
        return ["本文の「実験」が実験の識別子の形でない"]
    if routine.form is Form.FINAL and value != routine.final_experiment:
        return ["本文の「実験」が枝の実験と違う"]
    if routine.card is not None and routine.form is not Form.FINAL:
        expected = _experiment_id(given, routine.card)
        if expected is not None and value != expected:
            return ["本文の「実験」がカードから作る識別子と違う（D25 §3.2）"]
    return []


# --- 検査 8 ----------------------------------------------------------------------

AUTO_MERGE_NAME: Final = "自動 merge の守り"


def check_auto_merge(
    given: CheckInput,
    pr: PullRequest,
    routine: Routine | list[str] | None,
    others: Sequence[CheckResult],
) -> CheckResult:
    if not pr.auto_merge:
        return CheckResult(8, AUTO_MERGE_NAME, Status.PASS, ("自動 merge の指定なし",))
    problems: list[str] = []
    if not isinstance(routine, Routine):
        problems.append("定型 PR でない PR に自動 merge が指定されている（人間が merge する PR）")
    elif any(check.status is Status.FAIL for check in others):
        problems.append("定型 PR の検査 2〜7 のどれかに通らないのに、自動 merge が指定されている")
    if any(change.path.startswith(CANDIDATE_FIXES) for change in given.changes):
        problems.append(
            "候補の固定の記録を含む PR に自動 merge が指定されている（関所 1 は人間の merge）"
        )
    return result(8, AUTO_MERGE_NAME, problems)
