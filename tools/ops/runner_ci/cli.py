"""台帳と定型 PR の CI の段の入口（D25 §6.3）。

GitHub Actions では出来事のファイルから読む::

    uv run python -m tools.ops.runner_ci --repo . \\
        --event-name "$GITHUB_EVENT_NAME" --event-path "$GITHUB_EVENT_PATH"

- `pull_request`: 基点は `pull_request.base.sha`、先頭は取り出した merge のコミット（`HEAD`）。
  枝・題・本文・自動 merge の指定は出来事のファイルから読む。検査 1〜8 を当てる。
- `push`（main）: 基点は `before`（新しい枝で基点が無ければ追記だけの確かめは当てない）、先頭は
  `HEAD`。検査 1 だけを当てる（2〜8 は PR の検査）。

手元では `--base <コミット> --head <コミット>`（と、PR として確かめるなら `--branch`・`--title`・
`--body-file`・`--auto-merge`）を渡す。

終了コード: 0 = 全部合格（当てない検査を含む）/ 1 = どれかが不合格 / 2 = 入力の誤り（git・出来事）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from tools.ops.runner_ci import regen
from tools.ops.runner_ci.ledgers import check_ledgers
from tools.ops.runner_ci.model import (
    CheckInput,
    CheckResult,
    GitError,
    GitSnapshot,
    PullRequest,
    Status,
    git_changes,
    resolve,
)
from tools.ops.runner_ci.records import changed_records, check_formats, check_free_text
from tools.ops.runner_ci.regen import Regenerator, check_regeneration
from tools.ops.runner_ci.routine import (
    check_auto_merge,
    check_body,
    check_branch,
    check_paths,
    parse_routine,
)

_NO_COMMIT: Final = "0" * 40
_PR_ONLY: Final = (
    (2, "枝の名前と種類"),
    (3, "許されたパスだけ"),
    (4, "作り直しの一致"),
    (5, "記録の書式"),
    (6, "自由文に数字が無い"),
    (7, "定型 PR の題と本文の型"),
    (8, "自動 merge の守り"),
)


class InputError(ValueError):
    """出来事のファイル・引数が読めない（終了コード 2）。"""


def run_checks(given: CheckInput, regenerator: Regenerator | None = None) -> list[CheckResult]:
    """検査 1〜8 を順に当てる（PR でなければ検査 1 だけ）。"""
    results = [check_ledgers(given)]
    pr = given.pull_request
    if pr is None:
        results += [
            CheckResult(number, name, Status.NOT_APPLICABLE, ("push では当てない（PR の検査）",))
            for number, name in _PR_ONLY
        ]
        return results
    routine = parse_routine(pr)
    records = changed_records(given)
    others = [
        check_branch(pr, routine),
        check_paths(given, routine),
        check_regeneration(given, routine, regenerator),
        check_formats(given, records),
        check_free_text(records),
        check_body(given, pr, routine),
    ]
    results += others
    results.append(check_auto_merge(given, pr, routine, others))
    return results


def render(results: Sequence[CheckResult]) -> str:
    lines: list[str] = []
    for check in results:
        lines.append(f"検査 {check.number} {check.name}: {check.status.value}")
        lines += [f"  - {message}" for message in check.messages]
    failed = any(check.status is Status.FAIL for check in results)
    lines.append(f"結果: {'不合格' if failed else '合格'}")
    return "\n".join(lines) + "\n"


def _field(payload: Mapping[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            raise InputError(f"the event has no {'.'.join(keys)}")
        value = value[key]
    return value


def input_from_event(repo: Path, event_name: str, payload: Mapping[str, Any]) -> CheckInput:
    head = resolve(repo, "HEAD")
    if event_name == "pull_request":
        pr = _field(payload, "pull_request")
        base = resolve(repo, str(_field(pr, "base", "sha")))
        request = PullRequest(
            branch=str(_field(pr, "head", "ref")),
            title=str(_field(pr, "title")),
            body=str(pr.get("body") or ""),
            auto_merge=pr.get("auto_merge") is not None,
        )
        return CheckInput(
            base=GitSnapshot(repo, base),
            head=GitSnapshot(repo, head),
            changes=git_changes(repo, base, head),
            pull_request=request,
        )
    if event_name == "push":
        before = str(_field(payload, "before"))
        start = None if before == _NO_COMMIT else GitSnapshot(repo, resolve(repo, before))
        return CheckInput(base=start, head=GitSnapshot(repo, head), changes=(), pull_request=None)
    raise InputError(f"unsupported event {event_name!r} (pull_request / push)")


def input_from_arguments(arguments: argparse.Namespace) -> CheckInput:
    repo = Path(arguments.repo)
    head = resolve(repo, arguments.head)
    base = resolve(repo, arguments.base) if arguments.base else None
    request = None
    if arguments.branch is not None:
        if base is None:
            raise InputError("--branch needs --base")
        body = Path(arguments.body_file).read_text(encoding="utf-8") if arguments.body_file else ""
        request = PullRequest(arguments.branch, arguments.title or "", body, arguments.auto_merge)
    return CheckInput(
        base=None if base is None else GitSnapshot(repo, base),
        head=GitSnapshot(repo, head),
        changes=() if base is None or request is None else git_changes(repo, base, head),
        pull_request=request,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.ops.runner_ci",
        description="台帳と定型 PR の CI の段（D25 §6.3 の 8 項目）",
    )
    parser.add_argument("--repo", default=".", help="リポジトリの根")
    parser.add_argument("--event-name", help="GITHUB_EVENT_NAME（pull_request / push）")
    parser.add_argument("--event-path", help="GITHUB_EVENT_PATH（出来事の JSON）")
    parser.add_argument("--base", help="基点のコミット（手元で確かめるとき）")
    parser.add_argument("--head", default="HEAD", help="先頭のコミット（既定 HEAD）")
    parser.add_argument("--branch", help="PR として確かめるときの枝の名前")
    parser.add_argument("--title", help="PR の題")
    parser.add_argument("--body-file", help="PR の本文のファイル")
    parser.add_argument("--auto-merge", action="store_true", help="自動 merge が指定されている")
    return parser


def main(argv: Sequence[str] | None = None, regenerator: Regenerator | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.event_name is not None:
            if arguments.event_path is None:
                raise InputError("--event-name needs --event-path")
            payload = json.loads(Path(arguments.event_path).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise InputError("the event file is not a JSON object")
            given = input_from_event(Path(arguments.repo), arguments.event_name, payload)
        else:
            given = input_from_arguments(arguments)
    except (GitError, InputError, OSError, ValueError) as exc:
        sys.stderr.write(f"入力の誤り: {exc}\n")
        return 2
    results = run_checks(given, regenerator if regenerator is not None else regen.REGENERATOR)
    sys.stdout.write(render(results))
    return 1 if any(check.status is Status.FAIL for check in results) else 0
