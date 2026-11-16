"""origin の既定ブランチ上の候補の固定の記録の読込（D09 §9.11.3・§9.10.3 の 1 の 5）。

順 11 の PR 4c。
固定のコマンド（`app.candidate_fixation`）と最終検証のコマンド（`app.final_validation`）の両方が使う。

- **有効な記録**（仮置き F1-2・F1-12）: 1 つの実行 `(experiment_id, execution)` で有効なのは、
  `fix_no` が最も大きい 1 つだけ。それより小さい番号は置き換えられた記録で、予約を持たない（取り下げ
  と同じ扱い。決定 74）。
- **固定時点 `T_fix`**（仮置き F1-3）: origin の既定ブランチの first-parent の履歴で、その記録の
  ファイルを初めて含んだコミットの committer の時刻。ファイルが main に入った後に変わった（履歴に
  2 つ以上のコミットがある、または初めて含んだコミットの中身と今の中身が違う）なら、その記録は読込で
  拒否する（その候補は最終検証も前向き検証も申請できない）。
- **予約**（D09 §9.10.3 の 1 の 5、§9.11.4 の 4）: 他の候補の有効な記録の前向きの期間 `[S, E)`。
  予約を解く条件（決定 74＝要決定 F1-D (a)）に当たった期間は除かない。

読むのは版管理のファイルと git の履歴だけで、partition の足（価格）は読まない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath

from odyssey_fx.app.config.candidate_fix import load_candidate_fix, parse_candidate_fix_path
from odyssey_fx.app.config.forward_validation import CANDIDATE_FIXES_DIRECTORY
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.final_validation_records import FinalLedger
from odyssey_fx.app.holdout_access_log import GitHoldoutAccessLog
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.candidate_fixation import ResolvedFix, reservation_released
from odyssey_fx.evaluation.domain.forward_validation import CandidateFixId

__all__ = [
    "FixRecordStop",
    "Reservation",
    "fix_ids_at_origin",
    "reservations_at_origin",
    "resolve_fix_at_origin",
    "valid_fix_at_origin",
]


class FixRecordStop(ConfigError):
    """origin の固定の記録を読めない・変わっている（構造の誤り。読込で止める）。"""


@dataclass(frozen=True, slots=True)
class Reservation:
    """他の候補の予約した前向きの期間（解かれていないもの）。"""

    fix: ResolvedFix
    period: Interval


def fix_ids_at_origin(log: GitHoldoutAccessLog) -> dict[tuple[str, int], list[CandidateFixId]]:
    """origin の既定ブランチの固定の記録の識別子を実行ごとに集める（`fix_no` の昇順）。

    置き場の下に形の違うファイルがあれば構造の誤り（`FixRecordStop`）。
    """
    found: dict[tuple[str, int], list[CandidateFixId]] = {}
    for rel in log.list_at_origin(CANDIDATE_FIXES_DIRECTORY.as_posix()):
        if PurePosixPath(rel).name == ".gitkeep":
            continue
        fix_id = parse_candidate_fix_path(rel)
        if fix_id is None:
            raise FixRecordStop(
                f"{rel} on origin is not in the form {CANDIDATE_FIXES_DIRECTORY.as_posix()}/"
                "<experiment_id>/<execution>/<fix_no>.yaml (仮置き F1-2)"
            )
        found.setdefault((fix_id.experiment_id.hex, fix_id.execution), []).append(fix_id)
    for items in found.values():
        items.sort(key=lambda item: item.fix_no)
    return found


def _rel(fix_id: CandidateFixId) -> str:
    return (
        PurePosixPath(CANDIDATE_FIXES_DIRECTORY.as_posix())
        / fix_id.experiment_id.hex
        / str(fix_id.execution)
        / f"{fix_id.fix_no}.yaml"
    ).as_posix()


def resolve_fix_at_origin(log: GitHoldoutAccessLog, fix_id: CandidateFixId) -> ResolvedFix:
    """origin の既定ブランチの 1 つの固定の記録を読み、固定時点を導く（仮置き F1-3）。"""
    rel = _rel(fix_id)
    data = log.read_at_origin(rel)
    if data is None:
        raise FixRecordStop(f"{rel} is not on the default branch of origin")
    try:
        record = load_candidate_fix(rel, text=data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise FixRecordStop(f"{rel} on origin is not UTF-8") from exc
    except ConfigError as exc:
        raise FixRecordStop(str(exc)) from exc
    history = log.first_parent_history_at_origin(rel)
    if not history:
        raise FixRecordStop(f"{rel}: no first-parent commit of origin includes it")
    if len(history) > 1:
        raise FixRecordStop(
            f"{rel} changed on the default branch after it was first included ({len(history)}"
            " first-parent commits touch it). A fix record is never changed; make a new one"
            " (D09 §9.11.3、仮置き F1-3)"
        )
    commit, seconds = history[0]
    t_fix = UtcTime(datetime.fromtimestamp(seconds, UTC))
    return ResolvedFix(record=record, path=rel, commit=commit, t_fix=t_fix)


def valid_fix_at_origin(
    log: GitHoldoutAccessLog, experiment_id: str, execution: int
) -> ResolvedFix | None:
    """その実行の有効な固定の記録（`fix_no` が最も大きいもの）。無ければ `None`。"""
    items = fix_ids_at_origin(log).get((experiment_id, execution))
    if not items:
        return None
    return resolve_fix_at_origin(log, items[-1])


def _released(fix: ResolvedFix, ledgers: Sequence[FinalLedger]) -> bool:
    experiment = fix.record.candidate.experiment_id.hex
    execution = fix.record.candidate.execution
    rows = [
        (str(row.payload["result"]), str(row.payload.get("combined_verdict", "")))
        for ledger in ledgers
        for row in ledger.of_experiment(experiment)
        if row.execution == execution
    ]
    declared = any(
        str(row.payload["result"]) == "DECLARED_USED"
        for ledger in ledgers
        for row in ledger.of_experiment(experiment)
    )
    return reservation_released(rows, declared_used_for_experiment=declared)


def reservations_at_origin(
    log: GitHoldoutAccessLog,
    ledgers: Sequence[FinalLedger],
    *,
    exclude: tuple[str, int] | None = None,
) -> list[Reservation]:
    """他の候補の予約した前向きの期間（解かれていないもの。D09 §9.10.3 の 1 の 5）。

    `exclude` の実行（申請・固定をしている候補自身）は数えない。置き換えられた記録は数えない
    （仮置き F1-12）。予約を解く条件は、最終検証の台帳の行から導く（決定 74）。
    """
    out: list[Reservation] = []
    for key, items in sorted(fix_ids_at_origin(log).items()):
        if key == exclude:
            continue
        fix = resolve_fix_at_origin(log, items[-1])
        if _released(fix, ledgers):
            continue
        out.append(Reservation(fix=fix, period=fix.forward_period))
    return out
