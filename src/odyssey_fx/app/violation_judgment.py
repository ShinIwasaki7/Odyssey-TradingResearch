"""後から品質確認の条件違反と判定した閲覧を記録する管理コマンドの結線（ADR-0035 第3項）。

人間だけが使う管理コマンド `odyssey-fx gate judge-violation`（`app.cli`）の中身。人間が「この
品質確認の閲覧は条件 1・3・6 のどれかを満たしていなかった」と判定したとき、次をこの順で
fail-closed に行う（ADR-0035 第3項の「書き手と経路」。PR #110 の要決定 S3-A により S5a の前の
単独の PR で足した。D15 §2.2 の R4 の行の注記 (1)）。

1. 品質確認の閲覧の記録 `research/quality_access_log.jsonl` に**判定の行**を追記する（ロックの
   下。ADR-0035 第4項の 5・7）。同じ閲覧の識別子の判定の行が既にあれば追記を飛ばす（やり直し。
   PR #107 の要決定 S1-2）。
2. 違反の範囲（ADR-0035 第3項。partition の対象はその系列とその期間、原ファイルの対象はその
   銘柄の封印の区分の全体。PR #110 の仮置き S3-7）に重なる、既にあるすべての確定済みの snapshot の
   封印の区分の `SEALED` の partition に、消費の記録を `HoldoutAccessLog` を通して追記する。行は
   版 1（D09 §9.10.4。理由 `VIOLATION_JUDGMENT`、許可の識別子の欄は判定の行の識別子。R4 で
   版 0 から改めた。版 0 で書かれた行は版 0 のまま読む）。
3. 変更したファイルだけを commit し、origin の既定ブランチへ push する（ADR-0014 の消費遷移の
   直列化。ADR-0038 第8項の 3）。

書き込みを始める前に、記録の読込・閲覧の開始の行・partition の状態・暫定 snapshot・ロック・
origin の状態をすべて確かめる。途中で止まったときは、どこまで進んだかを件数と識別子だけで返し、
部分的な commit を残さない（push が断られたら commit を取り消す）。作業木に足した行は消さない
（追記専用。やり直しで残りだけを足して commit する）。

**価格を読まない**: 読むのは品質確認の閲覧の記録・manifest・消費の記録だけで、partition の
足（価格）を開かない。出力は件数・識別子・時刻だけ。
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from odyssey_fx.app.sealed_intake import SnapshotSealedPartition, snapshot_sealed_partitions
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.application.ports import HoldoutAccessLog, HoldoutAccessLogStop
from odyssey_fx.marketdata.adapters.parquet_store import ParquetSnapshotStore
from odyssey_fx.marketdata.adapters.quality_access_log_store import (
    QUALITY_ACCESS_LOCK_PATH,
    QUALITY_ACCESS_LOG_PATH,
    QualityAccessLogFile,
)
from odyssey_fx.marketdata.application.snapshot_access import PENDING_DIRECTORY
from odyssey_fx.marketdata.domain.access import HoldoutState
from odyssey_fx.marketdata.domain.consumption_log import ConsumptionReason, ConsumptionRow
from odyssey_fx.marketdata.domain.errors import HoldoutStateUnknown, MarketDataValueError
from odyssey_fx.marketdata.domain.quality_access import (
    QualityAccessJudgment,
    QualityAccessLogFailure,
    QualityAccessStart,
    QualityLogAppendRefused,
    violation_scopes,
)
from odyssey_fx.marketdata.domain.sealed_state import ViolationScope

__all__ = [
    "CONSUMPTION_PURPOSE",
    "JudgmentPlan",
    "JudgmentProgress",
    "ViolationJudgmentStopped",
    "judge_violation",
    "plan_judgment",
]

#: 消費の行の理由（版 1 の `reason`。D09 §9.10.4。版 0 の時代は `purpose` の頭に書いた）。
CONSUMPTION_PURPOSE: Final = ConsumptionReason.VIOLATION_JUDGMENT.value


@dataclass(slots=True)
class JudgmentProgress:
    """管理コマンドがどこまで進んだか（止まったときの報告にも使う。件数と識別子だけ）。"""

    access_id: str
    judgment_row_id: str | None = None
    #: `True` はこの回に判定の行を追記した、`False` は既にあった（やり直し）、`None` はまだ。
    judgment_appended: bool | None = None
    covered: int = 0
    already_consumed: int = 0
    to_consume: int = 0
    consumed_now: int = 0
    snapshots: tuple[str, ...] = ()
    #: やり直しのとき、既にある判定の行と入力の値が違う項目の名前（既にある行を使う）。
    differing: tuple[str, ...] = ()
    written_snapshots: list[str] = field(default_factory=list)
    commit: str | None = None


class ViolationJudgmentStopped(Exception):
    """管理コマンドを止めた（`stage` は止まった段、`progress` はそこまでに進んだこと）。"""

    def __init__(self, stage: str, detail: str, progress: JudgmentProgress) -> None:
        super().__init__(f"{stage}: {detail}")
        self.stage = stage
        self.detail = detail
        self.progress = progress


@dataclass(frozen=True, slots=True)
class JudgmentPlan:
    """書き込みの前に確かめた内容（乾式実行の出力にもなる）。"""

    judgment: QualityAccessJudgment
    #: 同じ閲覧の識別子の判定の行が既にある（やり直し。追記しない）。
    existing: bool
    #: やり直しのとき、既にある判定の行と入力の値が違う項目の名前。
    differing: tuple[str, ...]
    covered: tuple[SnapshotSealedPartition, ...]
    to_consume: tuple[SnapshotSealedPartition, ...]
    snapshots: tuple[str, ...]
    #: commit する消費の記録の snapshot（この判定と、他のすべての判定の行の範囲の snapshot）。
    publish_snapshots: tuple[str, ...] = ()


def _now() -> UtcTime:
    return UtcTime(datetime.now(UTC))


def _new_id() -> str:
    return secrets.token_hex(16)


def _pending_sealed_covered(snapshots_root: Path, scopes: Sequence[ViolationScope]) -> int:
    """暫定 snapshot（`_pending/`）の `SEALED` で始めた partition のうち、違反の範囲に重なる数。"""
    root = Path(snapshots_root)
    pending = root / PENDING_DIRECTORY
    if not pending.is_dir():
        return 0
    store = ParquetSnapshotStore(root=root)
    count = 0
    for directory in sorted(path for path in pending.iterdir() if path.is_dir()):
        try:
            manifest = store.read_manifest(f"{PENDING_DIRECTORY}/{directory.name}")
        except Exception as exc:  # noqa: BLE001 - どの失敗も「確かめられない」に倒す
            raise HoldoutStateUnknown(
                f"the pending snapshot {directory.name} cannot be read ({type(exc).__name__})"
            ) from exc
        for item in manifest.sealed_initial_states:
            if item.state is HoldoutState.SEALED and any(
                scope.covers(item.partition_id) for scope in scopes
            ):
                count += 1
    return count


def plan_judgment(
    *,
    repo_root: Path,
    snapshots_root: Path,
    access_id: str,
    conditions: frozenset[int],
    judged_by: str,
    judged_at: UtcTime,
    new_id: Callable[[], str] = _new_id,
) -> JudgmentPlan:
    """記録と snapshot を読み、判定の行と消費の対象を決める（何も書かない）。"""
    progress = JudgmentProgress(access_id=access_id)
    contents = QualityAccessLogFile(Path(repo_root)).read()
    if isinstance(contents, QualityAccessLogFailure):
        raise ViolationJudgmentStopped(
            "read",
            f"the quality access log cannot be read ({contents.kind.value}): {contents.detail}",
            progress,
        )
    start = next(
        (
            row
            for row in contents.rows
            if isinstance(row, QualityAccessStart) and row.access_id == access_id
        ),
        None,
    )
    if start is None:
        raise ViolationJudgmentStopped(
            "read", f"the quality access log has no start row of the access {access_id!r}", progress
        )
    existing = next(
        (
            row
            for row in contents.rows
            if isinstance(row, QualityAccessJudgment) and row.access_id == access_id
        ),
        None,
    )
    differing: tuple[str, ...] = ()
    if existing is None:
        try:
            judgment = QualityAccessJudgment(
                row_id=new_id(),
                access_id=access_id,
                violated_conditions=conditions,
                judged_at=judged_at,
                judged_by=judged_by,
            )
        except MarketDataValueError as exc:
            raise ViolationJudgmentStopped("input", str(exc), progress) from exc
    else:
        judgment = existing
        differing = tuple(
            name
            for name, same in (
                ("violated_conditions", existing.violated_conditions == conditions),
                ("judged_by", existing.judged_by == judged_by),
                ("judged_at", existing.judged_at == judged_at),
            )
            if not same
        )
    progress.judgment_row_id = judgment.row_id
    scopes = violation_scopes((start, judgment))
    try:
        partitions = snapshot_sealed_partitions(Path(snapshots_root), legacy=True)
        pending = _pending_sealed_covered(Path(snapshots_root), scopes)
    except HoldoutStateUnknown as exc:
        raise ViolationJudgmentStopped("state", str(exc), progress) from exc
    covered = tuple(
        item
        for item in partitions
        if any(scope.covers(item.partition.partition_id) for scope in scopes)
    )
    unknown = sorted(
        str(item.partition.partition_id) for item in covered if item.partition.state is None
    )
    if unknown:
        raise ViolationJudgmentStopped(
            "state",
            f"the states of {len(unknown)} covered partitions cannot be derived (the consumption"
            f" log cannot be read): {unknown}",
            progress,
        )
    if pending:
        raise ViolationJudgmentStopped(
            "pending",
            f"{pending} SEALED partitions of pending snapshots (_pending/) overlap the violation;"
            " finalize or discard those snapshots first, then run this command again",
            progress,
        )
    # 他の閲覧の判定の行: その範囲に `SEALED` が残っていれば、その閲覧のやり直しを先にする
    # （判定の行だけが消費の記録なしに origin へ出ないようにする）。範囲の snapshot の消費の記録は、
    # 品質確認の閲覧の記録と一緒に commit する（前の回の未 commit の消費の記録を置き去りにしない）。
    starts = {row.access_id: row for row in contents.rows if isinstance(row, QualityAccessStart)}
    others: set[str] = set()
    publish = {item.snapshot_id for item in covered}
    for row in contents.rows:
        if not isinstance(row, QualityAccessJudgment) or row.access_id == access_id:
            continue
        other_scopes = violation_scopes((starts[row.access_id], row))
        for item in partitions:
            if any(scope.covers(item.partition.partition_id) for scope in other_scopes):
                publish.add(item.snapshot_id)
                if item.partition.state is not HoldoutState.CONSUMED:
                    others.add(row.access_id)
    if others:
        raise ViolationJudgmentStopped(
            "other-judgment",
            f"the judgments of the accesses {sorted(others)} still leave SEALED (or unknown)"
            " partitions in their range; run this command for those accesses first",
            progress,
        )
    to_consume = tuple(item for item in covered if item.partition.state is HoldoutState.SEALED)
    return JudgmentPlan(
        judgment=judgment,
        existing=existing is not None,
        differing=differing,
        covered=covered,
        to_consume=to_consume,
        snapshots=tuple(sorted({item.snapshot_id for item in covered})),
        publish_snapshots=tuple(sorted(publish)),
    )


def _relative(repo_root: Path, path: Path, progress: JudgmentProgress) -> str:
    root = Path(repo_root).resolve()
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise ViolationJudgmentStopped(
            "base", f"{path} is outside the repository {repo_root}", progress
        ) from exc


def _message(plan: JudgmentPlan, consumed_now: int) -> str:
    judgment = plan.judgment
    conditions = ",".join(str(item) for item in sorted(judgment.violated_conditions))
    return (
        f"research: 品質確認の条件違反の判定（閲覧 {judgment.access_id}）と消費の記録"
        "（ADR-0035 第3項）\n\n"
        f"判定の行: {judgment.row_id}\n"
        f"違反した条件: {conditions}\n"
        f"判定者: {judgment.judged_by}\n"
        f"判定の日時: {judgment.judged_at}\n"
        f"違反の範囲に重なる封印の partition: {len(plan.covered)}"
        f"（snapshot {len(plan.snapshots)}）\n"
        f"この回に追記した消費の記録: {consumed_now}\n\n"
        "odyssey-fx gate judge-violation が書いた。\n"
    )


def judge_violation(
    *,
    repo_root: Path,
    snapshots_root: Path,
    access_id: str,
    conditions: frozenset[int],
    judged_by: str,
    judged_at: UtcTime,
    log: HoldoutAccessLog,
    now: Callable[[], UtcTime] = _now,
    new_id: Callable[[], str] = _new_id,
) -> JudgmentProgress:
    """判定の行の追記 → 消費の記録の追記 → commit → push を、この順で fail-closed に行う。"""
    root = Path(repo_root)
    plan = plan_judgment(
        repo_root=root,
        snapshots_root=snapshots_root,
        access_id=access_id,
        conditions=conditions,
        judged_by=judged_by,
        judged_at=judged_at,
        new_id=new_id,
    )
    progress = JudgmentProgress(
        access_id=access_id,
        judgment_row_id=plan.judgment.row_id,
        covered=len(plan.covered),
        already_consumed=len(plan.covered) - len(plan.to_consume),
        to_consume=len(plan.to_consume),
        snapshots=plan.snapshots,
        differing=plan.differing,
    )
    paths = [
        QUALITY_ACCESS_LOG_PATH.as_posix(),
        *(
            _relative(root, Path(snapshots_root) / snapshot / "access_log.jsonl", progress)
            for snapshot in plan.publish_snapshots
        ),
    ]
    if (root / QUALITY_ACCESS_LOCK_PATH).exists():
        raise ViolationJudgmentStopped(
            "lock",
            f"{QUALITY_ACCESS_LOCK_PATH} exists: another append is running, or a stopped writer"
            " left it (ADR-0035 第4項の 5 の 1). Nothing was written",
            progress,
        )
    try:
        log.require_published_base(paths)
    except HoldoutAccessLogStop as stop:
        raise ViolationJudgmentStopped(stop.stage, stop.detail, progress) from stop
    if plan.existing:
        progress.judgment_appended = False
    else:
        appended = QualityAccessLogFile(root).append(plan.judgment)
        if isinstance(appended, QualityLogAppendRefused):
            raise ViolationJudgmentStopped(
                "judgment",
                f"the judgment row was not appended ({appended.kind.value}): {appended.detail}",
                progress,
            )
        progress.judgment_appended = True
    recorded_at = now()
    for item in plan.to_consume:
        try:
            # 版 1 の行（D09 §9.10.4・§9.10.13 の 1。R4）: 理由 `VIOLATION_JUDGMENT`、許可の
            # 識別子の欄に判定の行の識別子、閲覧の識別子、候補と目的は空。
            log.append_row(
                item.snapshot_id,
                ConsumptionRow(
                    row_id=new_id(),
                    partition=str(item.partition.partition_id),
                    reason=ConsumptionReason.VIOLATION_JUDGMENT,
                    permit_id=plan.judgment.row_id,
                    candidate=None,
                    access_id=plan.judgment.access_id,
                    recorded_at=recorded_at,
                    actor=plan.judgment.judged_by,
                    purpose="",
                ),
            )
        except HoldoutAccessLogStop as stop:
            raise ViolationJudgmentStopped(stop.stage, stop.detail, progress) from stop
        progress.consumed_now += 1
        if item.snapshot_id not in progress.written_snapshots:
            progress.written_snapshots.append(item.snapshot_id)
    try:
        progress.commit = log.publish(paths, _message(plan, progress.consumed_now))
    except HoldoutAccessLogStop as stop:
        progress.commit = stop.commit
        raise ViolationJudgmentStopped(stop.stage, stop.detail, progress) from stop
    return progress
