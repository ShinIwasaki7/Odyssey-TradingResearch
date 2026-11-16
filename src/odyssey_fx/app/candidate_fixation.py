"""候補の固定の記録のコマンドの結線（`odyssey-fx experiment fix`・`fix-check`・`fix-show`）。

順 11 の PR 4c（人間の決定 189。D25 §2.1・§6.5・§8、D23 §8）。採用審査（D16）が
「進める」になった研究候補について、**固定の前の機械的な確かめ**（D09 §9.11.10 の
8 項目）を行い、通れば**固定の記録**（D09 §9.11.3・§9.11.12 の 2）を作る。置き場は
`research/candidate_fixes/<experiment_id>/<execution>/<fix_no>.yaml`。固定時点 `T_fix` は、
人間がその記録の PR を merge した時刻（main の first-parent の履歴で記録を初めて含んだ
コミットの committer の時刻。仮置き F1-3。関所 1）で、記録には書かない。

- **作る**（`experiment fix`）: 審査台帳の「進める」の行・試行台帳・記録票・審査の数値の
  記録（頻度 `r` の材料）・登録簿から固定の記録の値を組み立て、8 項目を確かめ、すべて
  通れば作業木に書く（「存在すれば失敗」）。commit・PR・merge はしない（PR は実行器が
  作り、merge は人間。D25 §6.5）。`--dry-run` は確かめだけで何も書かない。
- **確かめ直す**（`experiment fix-check`）: 作業木の既存の固定の記録（merge の前の PR の
  枝の上のもの）に 8 項目を当て直す（他の記録が先に main に入ったとき。D09 §9.11.10）。
- **読む**（`experiment fix-show`）: origin の既定ブランチ上の有効な固定の記録と、そこから
  導く固定時点・開始 `S`・前向きの期間 `[S, E)` を出す（何も書かない）。

**終了コード**（実行器が呼ぶ入口。D25 §4.1 の T32・§6.5 の 3・4）:

- 0 = 8 項目すべて合格（`fix` は記録を書いた）。
- 8 = 項目 6（窓の長さ）だけが、待てば解けうる理由で外れた（固定の延期。何も書かない）。
- 7 = それ以外の外れ（固定しない。何も書かない）。
- 2 = 引数・記録・台帳・登録簿の読込の誤りと、origin に届かないこと（何も書かない）。
- 1 = 記録を書けなかった。
- `fix-show` は 0 = 有効な記録がある、7 = 無い、2 = 読めない。

**申告は無い**（決定 150、ADR-0045）: 研究の外の相場の申告のファイルを入力にも資格にも
使わない。**読むもの**は版管理の台帳・登録簿・規則のファイル・固定の記録、snapshot の
manifest と承認の記録、消費の記録、最終検証の台帳、審査の数値の記録のうち `r` の材料
だけで、partition の足（価格）は読まない。**出力は日数・partition と週の数・合否・理由の
識別子だけ**で、`r` と取引の件数は出さない（仮置き F1-5）。前向き検証の申請（F-6）は
入れない（D23 §1.5 の注記）。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Final, Protocol

from odyssey_fx.app.candidate_fix_records import (
    Reservation,
    fix_ids_at_origin,
    reservations_at_origin,
    valid_fix_at_origin,
)
from odyssey_fx.app.config.candidate_fix import (
    candidate_fix_relative_path,
    candidate_fix_text,
    load_candidate_fix,
    parse_candidate_fix_path,
)
from odyssey_fx.app.config.forward_validation import (
    CANDIDATE_FIXES_DIRECTORY,
    forward_validation_registry_path,
    forward_validation_rules_path,
    load_forward_validation_registry,
    load_registered_forward_validation_rules,
)
from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.research_policy import (
    load_research_policy_registry,
    research_policy_registry_path,
)
from odyssey_fx.app.final_validation import (
    CandidateFacts,
    ExperimentCandidates,
    FinalRunner,
    derive_window,
    review_lines_at_origin,
    sealed_facts_at_origin,
)
from odyssey_fx.app.final_validation_records import (
    FINAL_LEDGER_PATH,
    FINAL_LOCK_FILE,
    PERMIT_FILE,
    FinalLedger,
    FinalLedgerError,
    final_directory,
    parse_final_ledger,
    read_final_ledger,
)
from odyssey_fx.app.holdout_access_log import GitHoldoutAccessLog
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    TRIAL_LEDGER_PATH,
    parse_trial_ledger_bytes,
    read_experiment_manifest,
)
from odyssey_fx.evaluation.adapters.review_store import (
    REVIEW_DIRECTORY,
    REVIEW_RECORD_FILE,
    review_line_bytes,
)
from odyssey_fx.evaluation.application.ports import HoldoutAccessLogStop, TrialLedgerReadFailure
from odyssey_fx.evaluation.domain.adoption_review import AdoptionReviewLine, ReviewResult
from odyssey_fx.evaluation.domain.candidate_fixation import (
    FixCheckItem,
    FixCheckOutcome,
    FixCheckReason,
    FixVerdict,
    ResolvedFix,
    fix_verdict,
    frequency_per_365_days,
    required_length_days,
)
from odyssey_fx.evaluation.domain.final_validation import (
    CONVERSION_TIMEFRAME,
    FinalReason,
    FinalWindow,
    SealedPartitionFact,
    WindowRefusal,
)
from odyssey_fx.evaluation.domain.forward_validation import (
    FORWARD_VALIDATION_ID,
    CandidateFix,
    CandidateFixId,
    FixCandidate,
    FixedTrialPin,
    ForwardRulesEntry,
    FrequencyMaterialRef,
    VersionedRef,
)
from odyssey_fx.evaluation.domain.research_policy import current_standard_version
from odyssey_fx.evaluation.domain.search import (
    TrialLedgerLine,
    finished_line_of,
    started_line_of,
)
from odyssey_fx.evaluation.domain.variant import VARIANTS, Paired
from odyssey_fx.marketdata.domain.series import PriceBasis

__all__ = [
    "EXIT_DEFERRED",
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_READ_ERROR",
    "EXIT_REFUSED",
    "ExperimentFixSource",
    "FixFacts",
    "FixMode",
    "FixOutcome",
    "FixRequest",
    "FixShowOutcome",
    "FixSource",
    "fix_candidate",
    "show_fix",
]

EXIT_OK: Final = 0
EXIT_FAILED: Final = 1
EXIT_READ_ERROR: Final = 2
#: 固定しない（項目 1〜5・7・8 のどれか、または項目 6 の延期できない理由で外れた。D25 の T32）。
EXIT_REFUSED: Final = 7
#: 固定の延期（項目 6 の窓の長さだけが、待てば解けうる理由で外れた。D15-15 (a)、D25 §6.5 の 3）。
EXIT_DEFERRED: Final = 8

_ADOPTION_REVIEWS_PATH: Final = Path("research") / "adoption_reviews.jsonl"
_FIX_DIRECTORY: Final = CANDIDATE_FIXES_DIRECTORY.as_posix()


class FixMode(Enum):
    """起動の形。"""

    CREATE = "create"
    DRY_RUN = "dry_run"
    CHECK = "check"


@dataclass(frozen=True, slots=True)
class FixRequest:
    """コマンドの引数。`record` は `CHECK` のときだけ（確かめ直す固定の記録のパス）。"""

    experiment_dir: Path
    execution: int
    repo_root: Path
    snapshots_root: Path
    by: str
    mode: FixMode = FixMode.CREATE
    supersede: bool = False
    record: Path | None = None


@dataclass(frozen=True, slots=True)
class FixFacts:
    """固定に使う候補の事実（記録票と結末記録から。価格・指標の値・取引の件数を含まない）。

    `trials` は記録票のコンパイルが通った試行（番号 → 割当のダイジェストと `compiled_ref`）。
    """

    candidate: CandidateFacts
    record_sheet_digest: ContentDigest
    trials: Mapping[int, FixedTrialPin]


class FixSource(Protocol):
    """候補の事実を読む口（本番は `ExperimentFixSource`）。読めなければ `ConfigError`。"""

    def read(self, experiment_dir: Path, execution: int) -> FixFacts: ...


class ExperimentFixSource:
    """実験の版のディレクトリの記録票・結末記録・束縛の記録と研究ポリシーの登録簿から読む。"""

    def __init__(self, repo_root: Path) -> None:
        self._candidates = ExperimentCandidates(repo_root)

    def read(self, experiment_dir: Path, execution: int) -> FixFacts:
        facts = self._candidates.read(experiment_dir, execution)
        directory = Path(experiment_dir).absolute()
        try:
            manifest = read_experiment_manifest(directory)
        except KernelValueError as exc:
            raise ConfigError(f"記録票を読めない: {exc}") from exc
        path = directory / "experiment_manifest.json"
        sheet = ContentDigest.sha256(hashlib.sha256(path.read_bytes()).hexdigest())
        trials = {
            trial.trial_index: FixedTrialPin(
                trial_index=trial.trial_index,
                assignment_digest=digest(trial.assignment),
                compiled_ref=trial.compiled_ref,
            )
            for trial in manifest.trials
            if trial.compiled_ref is not None
        }
        return FixFacts(candidate=facts, record_sheet_digest=sheet, trials=trials)


@dataclass(slots=True)
class FixOutcome:
    """コマンドの結果（CLI が表示する行と終了コード）。"""

    exit_code: int
    verdict: FixVerdict | None
    detail: str
    lines: list[str]
    outcomes: tuple[FixCheckOutcome, ...] = ()
    record_path: str | None = None
    written: bool = False


class _Stop(Exception):
    def __init__(self, exit_code: int, detail: str) -> None:
        super().__init__(detail)
        self.exit_code = exit_code
        self.detail = detail


def _now() -> UtcTime:
    return UtcTime(datetime.now(UTC))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row_sha(line: AdoptionReviewLine) -> str:
    """審査台帳の行の SHA-256（改行を除いたバイト列。最終検証の控えの値と同じ）。"""
    return _sha(review_line_bytes(line).rstrip(b"\n"))


def _material_id(nonce: str) -> str:
    """頻度 `r` の材料の識別子（審査の数値の記録の、実験の版のディレクトリからの相対パス）。"""
    return f"{REVIEW_DIRECTORY}/{nonce}/{REVIEW_RECORD_FILE}"


# --- 入力（origin の既定ブランチと作業木）---------------------------------------------------


@dataclass(slots=True)
class _Inputs:
    request: FixRequest
    log: GitHoldoutAccessLog
    facts: FixFacts
    trial_lines: tuple[TrialLedgerLine, ...]
    started: TrialLedgerLine | None
    reviews: tuple[AdoptionReviewLine, ...]
    worktree_ledger: FinalLedger
    origin_ledger: FinalLedger
    now: UtcTime
    runner: FinalRunner
    lines: list[str]
    window_lines: list[str] = field(default_factory=list)

    @property
    def experiment_hex(self) -> str:
        return self.facts.candidate.key.experiment_id

    @property
    def execution(self) -> int:
        return self.request.execution

    @property
    def ledgers(self) -> tuple[FinalLedger, FinalLedger]:
        return (self.worktree_ledger, self.origin_ledger)

    def proceeds(self) -> list[AdoptionReviewLine]:
        return [line for line in self.reviews if line.entry.result is ReviewResult.PROCEED]

    def own_proceed(self) -> AdoptionReviewLine | None:
        return next(
            (
                line
                for line in self.proceeds()
                if (line.entry.experiment_id.hex, line.entry.execution)
                == (self.experiment_hex, self.execution)
            ),
            None,
        )

    def same_on_origin(self, rel: str) -> bool:
        """作業木の `rel` の内容が origin の既定ブランチの内容と同じか（版管理の入力の確かめ）。"""
        path = Path(self.request.repo_root) / rel
        worktree = path.read_bytes() if path.is_file() else None
        return worktree is not None and worktree == self.log.read_at_origin(rel)


def _inputs(
    request: FixRequest,
    *,
    source: FixSource,
    runner: FinalRunner,
    log: GitHoldoutAccessLog,
    now: UtcTime,
    lines: list[str],
) -> _Inputs:
    try:
        branch, remote = log.fetch_origin()
    except HoldoutAccessLogStop as stop:
        raise _Stop(EXIT_READ_ERROR, f"origin cannot be confirmed: {stop.detail}") from stop
    lines.append(f"origin の既定ブランチ: {branch}（{remote[:12]}）")
    try:
        facts = source.read(request.experiment_dir, request.execution)
        reviews = review_lines_at_origin(log)
        worktree_ledger = read_final_ledger(request.repo_root)
        origin_data = log.read_at_origin(FINAL_LEDGER_PATH.as_posix())
        if origin_data is None:
            raise FinalLedgerError(f"{FINAL_LEDGER_PATH} is not on origin (D09 §9.10.5)")
        origin_ledger = parse_final_ledger(origin_data)
    except (ConfigError, FinalLedgerError) as exc:
        raise _Stop(EXIT_READ_ERROR, str(exc)) from exc
    key = facts.candidate.key
    if key.execution != request.execution:
        raise _Stop(EXIT_READ_ERROR, "the candidate facts are of another execution")
    data = log.read_at_origin(TRIAL_LEDGER_PATH.as_posix())
    if data is None:
        raise _Stop(EXIT_READ_ERROR, f"{TRIAL_LEDGER_PATH} is not on the default branch of origin")
    contents = parse_trial_ledger_bytes(data)
    if isinstance(contents, TrialLedgerReadFailure):
        raise _Stop(
            EXIT_READ_ERROR, f"{TRIAL_LEDGER_PATH} on origin cannot be read ({contents.detail})"
        )
    try:
        experiment_id = ExperimentId(ContentDigest.sha256(key.experiment_id))
    except KernelValueError as exc:
        raise _Stop(EXIT_READ_ERROR, f"the experiment id cannot be read: {exc}") from exc
    started = started_line_of(contents.lines, experiment_id, key.execution)
    finished = finished_line_of(contents.lines, experiment_id, key.execution)
    lines.append(
        f"候補: {key.experiment_name} v{key.experiment_version}"
        f"（experiment_id {key.experiment_id}、実行番号 {key.execution}）"
    )
    return _Inputs(
        request=request,
        log=log,
        facts=facts,
        trial_lines=contents.lines,
        started=started if finished is not None else None,
        reviews=reviews,
        worktree_ledger=worktree_ledger,
        origin_ledger=origin_ledger,
        now=now,
        runner=runner,
        lines=lines,
    )


def _forward_entries(repo_root: Path) -> tuple[ForwardRulesEntry, ...]:
    return load_forward_validation_registry(forward_validation_registry_path(repo_root))


def _current_forward_ref(repo_root: Path) -> VersionedRef:
    """前向き検証の規則の現行の版（登録簿の最大の版。読込でファイルと照合する）の参照。"""
    entries = _forward_entries(repo_root)
    version = max(entry.version for entry in entries)
    loaded = load_registered_forward_validation_rules(repo_root, version)
    return loaded.entry.ref


# --- 頻度 r と長さ L（D09 §9.11.5。値は出さない）-----------------------------------------------


def _numeric_record(inputs: _Inputs, review: AdoptionReviewLine) -> Mapping[str, Any] | str:
    """審査の数値の記録（`r` の材料）。読めない・審査台帳の行のダイジェストと違えば理由の文。"""
    path = Path(inputs.request.experiment_dir) / _material_id(review.entry.review_nonce)
    if not path.is_file():
        return (
            f"the numeric record of the review {_material_id(review.entry.review_nonce)} is absent"
        )
    data = path.read_bytes()
    if _sha(data) != review.entry.numeric_record_digest.hex:
        return "the numeric record of the review differs from numeric_record_digest of the row"
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "the numeric record of the review is not JSON"
    if not isinstance(payload, dict) or (
        payload.get("experiment_id"),
        payload.get("execution"),
        payload.get("review_nonce"),
    ) != (review.entry.experiment_id.hex, review.entry.execution, review.entry.review_nonce):
        return "the numeric record of the review is of another review"
    return payload


def _length(inputs: _Inputs, review: AdoptionReviewLine, rules_version: int) -> int | str:
    """規則で計算した `L`（日）。求まらなければ理由の識別子の値（`FREQUENCY_*`）と説明。"""
    payload = _numeric_record(inputs, review)
    if isinstance(payload, str):
        return f"{FixCheckReason.FREQUENCY_UNAVAILABLE.value}: {payload}"
    if inputs.started is None:
        return (
            f"{FixCheckReason.FREQUENCY_UNAVAILABLE.value}: the STARTED and FINISHED lines of this"
            " execution are not both on the trial ledger of origin"
        )
    validation = inputs.started.entry.validation_intervals
    try:
        loaded = load_registered_forward_validation_rules(inputs.request.repo_root, rules_version)
    except ConfigError as exc:
        return f"{FixCheckReason.FREQUENCY_UNAVAILABLE.value}: the forward rules: {exc}"
    rates: dict[str, Decimal | None] = {}
    minimums: dict[str, int] = {}
    for variant in VARIANTS:
        item = payload.get("variants", {}).get(variant.value, {})
        total = item.get("trade_total") if isinstance(item, dict) else None
        requirement = inputs.facts.candidate.requirements.of(variant)
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            return (
                f"{FixCheckReason.FREQUENCY_UNAVAILABLE.value}: the {variant.value} trade total of"
                " the fixed trial is not recorded"
            )
        if requirement is None:
            return (
                f"{FixCheckReason.FREQUENCY_UNAVAILABLE.value}: the {variant.value} frequency"
                " class is not recorded"
            )
        rate = frequency_per_365_days(total, validation)
        if rate is not None and rate <= 0:
            return f"{FixCheckReason.FREQUENCY_ZERO.value}: r is 0 for the {variant.value} pair"
        rates[variant.value] = rate
        minimums[variant.value] = requirement.min_validation_trades_total
    length = required_length_days(
        Paired(standard=rates["standard"], candidate=rates["candidate"]),
        minimum_trades=Paired(standard=minimums["standard"], candidate=minimums["candidate"]),
        rule=loaded.rules.length,
    )
    if length is None:
        return f"{FixCheckReason.FREQUENCY_UNAVAILABLE.value}: L cannot be derived"
    return length


# --- 記録の組み立て（`experiment fix`）-----------------------------------------------------------


def _existing_ids(inputs: _Inputs) -> list[CandidateFixId]:
    """この実行の固定の記録（origin と作業木。`fix_no` の昇順。重複は 1 つ）。"""
    found = {
        item.fix_no: item
        for item in fix_ids_at_origin(inputs.log).get((inputs.experiment_hex, inputs.execution), [])
    }
    directory = (
        Path(inputs.request.repo_root)
        / _FIX_DIRECTORY
        / inputs.experiment_hex
        / str(inputs.execution)
    )
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            rel = path.relative_to(Path(inputs.request.repo_root)).as_posix()
            fix_id = parse_candidate_fix_path(rel)
            if fix_id is None:
                raise _Stop(EXIT_READ_ERROR, f"{rel} is not a fix record file name (仮置き F1-2)")
            found.setdefault(fix_id.fix_no, fix_id)
    return [found[number] for number in sorted(found)]


def _lineage_fix(inputs: _Inputs) -> CandidateFixId | None:
    """別の版（別の実行）の固定で置き換える元の固定の記録（D09 §9.11.9 の 2）。

    試行台帳の系譜（`derived_from`）を元の実行へたどり、origin の既定ブランチに有効な固定の記録を
    持つ最も近い実行の、その記録（`fix_no` が最も大きいもの）を返す。系譜が無い・どの元の実行も
    固定されていなければ `None`（仮置き）。
    """
    started = inputs.started
    source = (
        None
        if started is None or started.entry.lineage is None
        else (started.entry.lineage.derived_from)
    )
    fixed = fix_ids_at_origin(inputs.log)
    seen: set[tuple[str, int]] = set()
    while source is not None:
        key = (source.experiment_id.hex, source.execution)
        if key in seen:
            return None
        seen.add(key)
        items = fixed.get(key)
        if items:
            return items[-1]
        line = started_line_of(inputs.trial_lines, source.experiment_id, source.execution)
        source = (
            None
            if line is None or line.entry.lineage is None
            else (line.entry.lineage.derived_from)
        )
    return None


def _build(inputs: _Inputs) -> tuple[CandidateFix, str] | FixCheckOutcome:
    """固定の記録の値を組み立てる。組み立てられなければ、外れた項目の結果。"""
    review = inputs.own_proceed()
    if review is None:
        return FixCheckOutcome(
            FixCheckItem.REVIEW_PROCEEDED,
            (FixCheckReason.REVIEW_NOT_PROCEEDED,),
            f"no PROCEED row of this execution on {_ADOPTION_REVIEWS_PATH} of origin",
        )
    fixed = review.entry.fixed_trials
    if fixed.standard is None or fixed.candidate is None:
        return FixCheckOutcome(
            FixCheckItem.REVIEW_PROCEEDED,
            (FixCheckReason.REVIEW_TRIALS_MISMATCH,),
            "the review row does not fix a trial for both pairs",
        )
    pins: dict[str, FixedTrialPin] = {}
    for variant, trial in fixed.items():
        assert trial is not None  # noqa: S101 - 上で確かめた
        pin = inputs.facts.trials.get(trial.trial_index)
        if pin is None or pin.assignment_digest != trial.assignment_digest:
            return FixCheckOutcome(
                FixCheckItem.FORMAT_AND_DIGESTS,
                (FixCheckReason.DIGEST_MISMATCH,),
                f"the {variant.value} fixed trial of the review is not a compiled trial of the"
                " record sheet with the same assignment",
            )
        pins[variant.value] = pin
    try:
        forward = _current_forward_ref(inputs.request.repo_root)
    except ConfigError as exc:
        raise _Stop(EXIT_READ_ERROR, str(exc)) from exc
    length = _length(inputs, review, forward.version)
    if isinstance(length, str):
        reason = FixCheckReason(length.split(":", 1)[0])
        return FixCheckOutcome(FixCheckItem.LENGTH_MATCHES, (reason,), length.split(": ", 1)[-1])
    existing = _existing_ids(inputs)
    if existing and inputs.request.supersede:
        previous: CandidateFixId | None = existing[-1]
        fix_no = existing[-1].fix_no + 1
    else:
        previous, fix_no = _lineage_fix(inputs), 1
    policy = review.entry.research_policy_ref
    lineage = (
        None
        if inputs.started is None or inputs.started.entry.lineage is None
        else (inputs.started.entry.lineage.derived_from)
    )
    try:
        record = CandidateFix(
            candidate=FixCandidate(
                experiment_id=review.entry.experiment_id, execution=review.entry.execution
            ),
            fix_no=fix_no,
            fixed_trials=Paired(standard=pins["standard"], candidate=pins["candidate"]),
            record_sheet_digest=inputs.facts.record_sheet_digest,
            research_policy=VersionedRef(
                ref_id=policy.policy_id, version=policy.version, digest=policy.digest
            ),
            forward_rules=forward,
            adoption_review_row_digest=ContentDigest.sha256(_row_sha(review)),
            length_days=length,
            frequency_source=FrequencyMaterialRef(
                material_id=_material_id(review.entry.review_nonce),
                digest=review.entry.numeric_record_digest,
            ),
            derived_from=lineage,
            supersedes=previous,
            fixed_by=inputs.request.by,
        )
    except KernelValueError as exc:
        raise _Stop(EXIT_READ_ERROR, f"the fix record cannot be built: {exc}") from exc
    rel = candidate_fix_relative_path(
        record.candidate.experiment_id, record.candidate.execution, fix_no
    )
    return record, rel


# --- 8 項目（D09 §9.11.10）---------------------------------------------------------------


def _outcome(
    item: FixCheckItem, reasons: list[FixCheckReason], detail: str = ""
) -> FixCheckOutcome:
    unique = tuple(dict.fromkeys(reasons))
    return FixCheckOutcome(item, unique, detail)


def _check_review(inputs: _Inputs, record: CandidateFix) -> FixCheckOutcome:
    """項目 1: 審査台帳の「進める」の行が origin にあり、組ごとの固定した試行と一致する。"""
    review = inputs.own_proceed()
    if review is None:
        return _outcome(FixCheckItem.REVIEW_PROCEEDED, [FixCheckReason.REVIEW_NOT_PROCEEDED])
    reasons: list[FixCheckReason] = []
    for variant, trial in review.entry.fixed_trials.items():
        pin = record.fixed_trials.of(variant)
        if trial is None or (trial.trial_index, trial.assignment_digest) != (
            pin.trial_index,
            pin.assignment_digest,
        ):
            reasons.append(FixCheckReason.REVIEW_TRIALS_MISMATCH)
    return _outcome(FixCheckItem.REVIEW_PROCEEDED, reasons)


def _applied(inputs: _Inputs) -> bool:
    """この実行が最終検証を申請したか（台帳の行・控え・ロックのどれか。D09 §9.11.10 の項目 2）。"""
    rows = [
        row
        for ledger in inputs.ledgers
        for row in ledger.of_experiment(inputs.experiment_hex)
        if row.execution == inputs.execution
    ]
    final_dir = final_directory(inputs.request.experiment_dir, inputs.execution)
    return (
        bool(rows) or (final_dir / PERMIT_FILE).exists() or (final_dir / FINAL_LOCK_FILE).exists()
    )


def _check_other_fixes(inputs: _Inputs, record: CandidateFix, rel: str) -> FixCheckOutcome:
    """項目 2: その実行にほかの有効な固定の記録が無い。

    置き換えは、申請の前の書き誤りの直しだけ（仮置き F1-12）。確かめ直し（`fix-check`）では確かめる
    記録そのものを数えない。作るとき（`fix`）は同じ置き場の既存の記録も「ほかの記録」に数える。
    """
    checking = inputs.request.mode is FixMode.CHECK
    others = [
        item
        for item in _existing_ids(inputs)
        if not checking
        or candidate_fix_relative_path(item.experiment_id, item.execution, item.fix_no) != rel
    ]
    previous = record.supersedes
    reasons: list[FixCheckReason] = []
    if previous is None or not record.fix_id.same_execution(previous):
        if others:
            reasons.append(FixCheckReason.OTHER_VALID_FIX_EXISTS)
        # 別の版（別の実行）の固定は、系譜をたどった元の実行の有効な固定の記録を置き換える
        # （D09 §9.11.9 の 2）。元の実行が固定されていなければ空。
        if previous != _lineage_fix(inputs):
            reasons.append(FixCheckReason.SUPERSEDES_MISMATCH)
    else:
        if not others or others[-1].fix_no != previous.fix_no:
            reasons.append(FixCheckReason.SUPERSEDES_MISMATCH)
        if _applied(inputs):
            reasons.append(FixCheckReason.SUPERSEDED_ALREADY_APPLIED)
    detail = f"この実行のほかの固定の記録: {len(others)}"
    return _outcome(FixCheckItem.NO_OTHER_VALID_FIX, reasons, detail)


def _check_forward_rules(inputs: _Inputs, record: CandidateFix) -> FixCheckOutcome:
    """項目 3: 前向き検証の規則の版が登録簿にあり、現行の版で、ダイジェストが一致する。"""
    root = inputs.request.repo_root
    ref = record.forward_rules
    reasons: list[FixCheckReason] = []
    try:
        entries = _forward_entries(root)
    except ConfigError as exc:
        return _outcome(
            FixCheckItem.FORWARD_RULES_CURRENT,
            [FixCheckReason.FORWARD_RULES_NOT_REGISTERED],
            str(exc),
        )
    entry = next(
        (item for item in entries if (item.rules_id, item.version) == (ref.ref_id, ref.version)),
        None,
    )
    if entry is None:
        reasons.append(FixCheckReason.FORWARD_RULES_NOT_REGISTERED)
    else:
        if ref.version != max(item.version for item in entries):
            reasons.append(FixCheckReason.FORWARD_RULES_NOT_CURRENT)
        try:
            loaded = load_registered_forward_validation_rules(root, ref.version)
        except ConfigError:
            reasons.append(FixCheckReason.FORWARD_RULES_DIGEST_MISMATCH)
        else:
            if loaded.digest != ref.digest or entry.digest != ref.digest:
                reasons.append(FixCheckReason.FORWARD_RULES_DIGEST_MISMATCH)
        for rel in (
            forward_validation_registry_path(Path()).as_posix(),
            forward_validation_rules_path(Path(), ref.version).as_posix(),
        ):
            if not inputs.same_on_origin(rel):
                reasons.append(FixCheckReason.INPUT_NOT_ON_ORIGIN)
    return _outcome(FixCheckItem.FORWARD_RULES_CURRENT, reasons, f"{ref.ref_id} 版 {ref.version}")


def _check_research_policy(inputs: _Inputs, record: CandidateFix) -> FixCheckOutcome:
    """項目 4: 研究ポリシーの版が、確かめの時点の現行の版である（仮置き F1-16）。"""
    root = inputs.request.repo_root
    ref = record.research_policy
    reasons: list[FixCheckReason] = []
    try:
        registry = load_research_policy_registry(research_policy_registry_path(root))
    except ConfigError as exc:
        return _outcome(
            FixCheckItem.RESEARCH_POLICY_CURRENT,
            [FixCheckReason.RESEARCH_POLICY_NOT_CURRENT],
            str(exc),
        )
    if current_standard_version(registry, ref.ref_id) != ref.version:
        reasons.append(FixCheckReason.RESEARCH_POLICY_NOT_CURRENT)
    entry = next(
        (item for item in registry if (item.policy_id, item.version) == (ref.ref_id, ref.version)),
        None,
    )
    if entry is None or entry.digest != ref.digest:
        reasons.append(FixCheckReason.RESEARCH_POLICY_DIGEST_MISMATCH)
    if not inputs.same_on_origin(research_policy_registry_path(Path()).as_posix()):
        reasons.append(FixCheckReason.INPUT_NOT_ON_ORIGIN)
    return _outcome(FixCheckItem.RESEARCH_POLICY_CURRENT, reasons, f"{ref.ref_id} 版 {ref.version}")


def _check_length(inputs: _Inputs, record: CandidateFix) -> FixCheckOutcome:
    """項目 5: `r` が組ごとに求まり（0 でない）、`L` が規則で計算した値と一致する。

    `r` と取引の件数は出さない（仮置き F1-5）。
    """
    review = inputs.own_proceed()
    if review is None:
        return _outcome(
            FixCheckItem.LENGTH_MATCHES,
            [FixCheckReason.FREQUENCY_UNAVAILABLE],
            "no PROCEED row of this execution",
        )
    length = _length(inputs, review, record.forward_rules.version)
    if isinstance(length, str):
        reason, _, detail = length.partition(": ")
        return _outcome(FixCheckItem.LENGTH_MATCHES, [FixCheckReason(reason)], detail)
    if length != record.length_days:
        return _outcome(
            FixCheckItem.LENGTH_MATCHES,
            [FixCheckReason.LENGTH_MISMATCH],
            f"規則で計算した L: {length} 日、記録の L: {record.length_days} 日",
        )
    return _outcome(FixCheckItem.LENGTH_MATCHES, [], f"L: {record.length_days} 日")


def _earlier_pending(inputs: _Inputs) -> int:
    """審査の順で先の候補のうち、最終検証が終わっていないものの数。

    「終わった」は D09 §9.10.10 の 8 と同じ（最終検証の台帳の行から導く）。
    """
    proceeds = inputs.proceeds()
    own = inputs.own_proceed()
    if own is None:
        return 0
    finished = {"COMPLETED", "FAILED_AFTER_CONSUMPTION", "WITHDRAWN", "DECLARED_USED"}
    pending = 0
    for line in proceeds[: proceeds.index(own)]:
        experiment, execution = line.entry.experiment_id.hex, line.entry.execution
        if (experiment, execution) == (inputs.experiment_hex, inputs.execution):
            continue
        rows = [
            str(row.payload["result"])
            for ledger in inputs.ledgers
            for row in ledger.of_experiment(experiment)
            if row.execution == execution or str(row.payload["result"]) == "DECLARED_USED"
        ]
        if not finished.intersection(rows):
            pending += 1
    return pending


def _window_end(facts_now: Sequence[SealedPartitionFact], inputs: _Inputs) -> UtcTime | None:
    """確かめの時点の窓の終わり（組・系列ごとの承認済みの partition の最も遅い終わりの最小）。

    取得・承認の前の週は、まだ封印のデータが無いので窓に入れない（仮置き。窓の途中の抜けは
    `WINDOW_HAS_MISSING_WEEK` のまま）。
    """
    candidate = inputs.facts.candidate
    ends: dict[tuple[str, str], UtcTime] = {}
    for fact in facts_now:
        series = fact.partition_id.series
        symbol = str(series.symbol)
        chosen = symbol in candidate.symbols or (
            symbol in candidate.conversion_symbols
            and series.timeframe.id == CONVERSION_TIMEFRAME
            and series.basis is PriceBasis.BID
        )
        if not chosen or fact.period.end > inputs.now:
            continue
        key = (fact.variant.value, str(series))
        if key not in ends or ends[key] < fact.period.end:
            ends[key] = fact.period.end
    return min(ends.values()) if ends else None


def _check_window(
    inputs: _Inputs, record: CandidateFix, reservations: Sequence[Reservation]
) -> FixCheckOutcome:
    """項目 6: 確かめの時点の使える窓の採点区間の日数が `L` 以上（外れれば固定の延期）。"""
    item = FixCheckItem.WINDOW_LONG_ENOUGH
    pending = _earlier_pending(inputs)
    if pending:
        return _outcome(
            item,
            [FixCheckReason.EARLIER_CANDIDATE_PENDING],
            f"審査の順で先の候補のうち最終検証が終わっていないもの: {pending}",
        )
    facts_now = sealed_facts_at_origin(inputs.log)
    if isinstance(facts_now, WindowRefusal):
        return _outcome(item, [FixCheckReason.WINDOW_NOT_DERIVABLE], facts_now.reason.value)
    end = _window_end(facts_now, inputs)
    if end is None:
        return _outcome(
            item, [FixCheckReason.NO_USABLE_WINDOW], "承認済みの封印の partition が無い"
        )
    prepared = inputs.runner.prepare(inputs.facts.candidate, inputs.request.experiment_dir)
    if isinstance(prepared, WindowRefusal):
        return _outcome(item, [FixCheckReason.WINDOW_NOT_DERIVABLE], prepared.reason.value)
    derived = derive_window(
        facts_now,
        facts=inputs.facts.candidate,
        preparation=prepared,
        t_fix=end,
        excluded=[reservation.period for reservation in reservations],
        length_days=1,
    )
    if isinstance(derived, WindowRefusal):
        reason = {
            FinalReason.NO_USABLE_WINDOW: FixCheckReason.NO_USABLE_WINDOW,
            FinalReason.WINDOW_SHORTER_THAN_L: FixCheckReason.WINDOW_SHORTER_THAN_L,
        }.get(derived.reason, FixCheckReason.WINDOW_NOT_DERIVABLE)
        return _outcome(item, [reason], derived.reason.value)
    inputs.window_lines.extend(_window_lines(derived, record.length_days))
    scored = derived.scoring.end - derived.scoring.start
    detail = (
        f"使える窓: 取引週 {derived.weeks}、採点区間 {scored // timedelta(days=1)} 日、"
        f"長さ L: {record.length_days} 日"
    )
    if scored < timedelta(days=record.length_days):
        return _outcome(item, [FixCheckReason.WINDOW_SHORTER_THAN_L], detail)
    return _outcome(item, [], detail)


def _window_lines(window: FinalWindow, length_days: int) -> list[str]:
    scored = window.scoring.end - window.scoring.start
    return [
        f"確かめの時点の使える窓: [{window.consumption.start}, {window.consumption.end})"
        f"（取引週 {window.weeks}、partition {len(window.targets)}）",
        f"採点区間: {scored // timedelta(days=1)} 日（長さ L: {length_days} 日）",
    ]


def _check_reservations(reservations: Sequence[Reservation], now: UtcTime) -> FixCheckOutcome:
    """項目 7: 他の候補の予約した前向きの期間の終わりが、確かめの時刻より後にない。"""
    active = [item for item in reservations if item.period.end > now]
    return _outcome(
        FixCheckItem.RESERVATIONS_ENDED,
        [FixCheckReason.RESERVATION_NOT_ENDED] if active else [],
        f"他の候補の予約: {len(reservations)}、終わっていないもの: {len(active)}",
    )


def _check_format(inputs: _Inputs, record: CandidateFix, rel: str, text: str) -> FixCheckOutcome:
    """項目 8: 書式が読め、指すファイルのダイジェストが一致する。"""
    item = FixCheckItem.FORMAT_AND_DIGESTS
    reasons: list[FixCheckReason] = []
    try:
        reread = load_candidate_fix(rel, text=text)
    except ConfigError as exc:
        return _outcome(item, [FixCheckReason.RECORD_UNREADABLE], str(exc))
    if reread != record:
        reasons.append(FixCheckReason.RECORD_UNREADABLE)
    if (record.candidate.experiment_id.hex, record.candidate.execution) != (
        inputs.experiment_hex,
        inputs.execution,
    ):
        reasons.append(FixCheckReason.RECORD_PATH_MISMATCH)
    if record.record_sheet_digest != inputs.facts.record_sheet_digest:
        reasons.append(FixCheckReason.DIGEST_MISMATCH)
    for _, pin in record.fixed_trials.items():
        if inputs.facts.trials.get(pin.trial_index) != pin:
            reasons.append(FixCheckReason.DIGEST_MISMATCH)
    review = inputs.own_proceed()
    if review is not None:
        policy = review.entry.research_policy_ref
        expected_material = FrequencyMaterialRef(
            material_id=_material_id(review.entry.review_nonce),
            digest=review.entry.numeric_record_digest,
        )
        if (
            record.adoption_review_row_digest.hex != _row_sha(review)
            or (record.research_policy.ref_id, record.research_policy.version)
            != (policy.policy_id, policy.version)
            or record.research_policy.digest != policy.digest
            or record.frequency_source != expected_material
        ):
            reasons.append(FixCheckReason.DIGEST_MISMATCH)
        material = Path(inputs.request.experiment_dir) / expected_material.material_id
        if (
            not material.is_file()
            or _sha(material.read_bytes()) != record.frequency_source.digest.hex
        ):
            reasons.append(FixCheckReason.DIGEST_MISMATCH)
    else:
        reasons.append(FixCheckReason.DIGEST_MISMATCH)
    lineage = (
        None
        if inputs.started is None or inputs.started.entry.lineage is None
        else inputs.started.entry.lineage.derived_from
    )
    if inputs.started is None or record.derived_from != lineage:
        reasons.append(FixCheckReason.DIGEST_MISMATCH)
    if record.forward_rules.ref_id != FORWARD_VALIDATION_ID:  # pragma: no cover - 型が拒否する
        reasons.append(FixCheckReason.DIGEST_MISMATCH)
    return _outcome(item, reasons)


def _check_all(
    inputs: _Inputs, record: CandidateFix, rel: str, text: str
) -> tuple[FixCheckOutcome, ...]:
    try:
        reservations = reservations_at_origin(
            inputs.log, inputs.ledgers, exclude=(inputs.experiment_hex, inputs.execution)
        )
    except ConfigError as exc:
        raise _Stop(EXIT_READ_ERROR, str(exc)) from exc
    return (
        _check_review(inputs, record),
        _check_other_fixes(inputs, record, rel),
        _check_forward_rules(inputs, record),
        _check_research_policy(inputs, record),
        _check_length(inputs, record),
        _check_window(inputs, record, reservations),
        _check_reservations(reservations, inputs.now),
        _check_format(inputs, record, rel, text),
    )


# --- 入口 --------------------------------------------------------------------------------------

_VERDICT_EXIT: Final[Mapping[FixVerdict, int]] = {
    FixVerdict.PASSED: EXIT_OK,
    FixVerdict.DEFERRED: EXIT_DEFERRED,
    FixVerdict.REFUSED: EXIT_REFUSED,
}


def _item_line(outcome: FixCheckOutcome) -> str:
    head = f"項目 {outcome.item.value}（{outcome.item.name}）: "
    result = (
        "合格"
        if outcome.passed
        else "不合格（" + "・".join(r.value for r in outcome.reasons) + "）"
    )
    return head + result + (f" — {outcome.detail}" if outcome.detail else "")


def _recorded_runner(request: FixRequest) -> FinalRunner:
    """本番のウォームアップの求め方（最終検証の段 2 (f) と同じ。`app.final_run`）。"""
    from odyssey_fx.app.final_run import RecordedFinalRunner  # noqa: PLC0415

    return RecordedFinalRunner(
        repo_root=request.repo_root,
        snapshots_root=request.snapshots_root,
        out_root=request.repo_root,
    )


def fix_candidate(
    request: FixRequest,
    *,
    source: FixSource | None = None,
    runner: FinalRunner | None = None,
    log: GitHoldoutAccessLog | None = None,
    now: Callable[[], UtcTime] = _now,
) -> FixOutcome:
    """固定の前の確かめと固定の記録の作成（`experiment fix`・`fix-check`）。"""
    lines: list[str] = []
    try:
        if isinstance(request.execution, bool) or request.execution < 1:
            raise _Stop(EXIT_READ_ERROR, f"`--execution` は 1 以上: {request.execution}")
        if not request.by.strip():
            raise _Stop(EXIT_READ_ERROR, "`--by` が空である")
        try:
            chosen_log = log or GitHoldoutAccessLog(request.repo_root, request.snapshots_root)
        except HoldoutAccessLogStop as stop:
            raise _Stop(EXIT_READ_ERROR, stop.detail) from stop
        inputs = _inputs(
            request,
            source=source or ExperimentFixSource(request.repo_root),
            runner=runner or _recorded_runner(request),
            log=chosen_log,
            now=now(),
            lines=lines,
        )
        return _fix(inputs)
    except _Stop as stop:
        return FixOutcome(stop.exit_code, None, stop.detail, lines)
    except HoldoutAccessLogStop as stop:
        return FixOutcome(EXIT_READ_ERROR, None, f"{stop.stage}: {stop.detail}", lines)
    except (ConfigError, KernelValueError) as exc:
        return FixOutcome(EXIT_READ_ERROR, None, str(exc), lines)


def _fix(inputs: _Inputs) -> FixOutcome:
    request = inputs.request
    lines = inputs.lines
    if request.mode is FixMode.CHECK:
        if request.record is None:
            raise _Stop(EXIT_READ_ERROR, "`--record` is required")
        path = (
            request.record
            if request.record.is_absolute()
            else Path(request.repo_root) / request.record
        )
        try:
            rel = path.resolve().relative_to(Path(request.repo_root).resolve()).as_posix()
            text = path.read_text(encoding="utf-8")
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            raise _Stop(
                EXIT_READ_ERROR, f"the fix record {request.record} cannot be read: {exc}"
            ) from exc
        try:
            record = load_candidate_fix(rel, text=text)
        except ConfigError as exc:
            failed = _outcome(
                FixCheckItem.FORMAT_AND_DIGESTS, [FixCheckReason.RECORD_UNREADABLE], str(exc)
            )
            lines.append(_item_line(failed))
            lines.append("項目 8 が外れたので、ほかの項目は確かめていない（固定しない）")
            return FixOutcome(EXIT_REFUSED, FixVerdict.REFUSED, str(exc), lines, (failed,), rel)
    else:
        built = _build(inputs)
        if isinstance(built, FixCheckOutcome):
            lines.append(_item_line(built))
            lines.append(
                f"項目 {built.item.value} が外れたので固定の記録を組み立てられない（ほかの項目は"
                "確かめていない。固定しない）"
            )
            return FixOutcome(EXIT_REFUSED, FixVerdict.REFUSED, built.detail, lines, (built,))
        record, rel = built
        text = candidate_fix_text(record)
    lines.append(f"固定の記録: {rel}（fix_no {record.fix_no}）")
    lines.append(f"長さ L: {record.length_days} 日")
    outcomes = _check_all(inputs, record, rel, text)
    verdict = fix_verdict(outcomes)
    lines.extend(inputs.window_lines)
    lines.extend(_item_line(item) for item in outcomes)
    lines.append(f"判定: {verdict.value}")
    outcome = FixOutcome(_VERDICT_EXIT[verdict], verdict, verdict.value, lines, outcomes, rel)
    if verdict is not FixVerdict.PASSED:
        lines.append(
            "固定を延期する（項目 6 の窓の長さだけが外れた。次の定期取得の承認の後に確かめ直す）。"
            "何も書いていない"
            if verdict is FixVerdict.DEFERRED
            else "固定しない。何も書いていない"
        )
        return outcome
    if request.mode is FixMode.CREATE:
        target = Path(request.repo_root) / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        except OSError as exc:
            outcome.exit_code = EXIT_FAILED
            outcome.detail = f"the fix record cannot be written: {exc}"
            lines.append(f"固定の記録を書けなかった（{type(exc).__name__}）")
            return outcome
        outcome.written = True
        lines.append(
            f"固定の記録を書いた: {rel}"
            "（commit・PR は実行器、merge は人間。merge の時刻が固定時点）"
        )
    else:
        lines.append("8 項目すべて合格。何も書いていない")
    return outcome


# --- `experiment fix-show` -----------------------------------------------------------------------


@dataclass(slots=True)
class FixShowOutcome:
    """`fix-show` の結果。"""

    exit_code: int
    fix: ResolvedFix | None
    lines: list[str]
    detail: str = ""


def show_fix(
    *,
    experiment_id: str,
    execution: int,
    repo_root: Path,
    log: GitHoldoutAccessLog | None = None,
) -> FixShowOutcome:
    """origin の既定ブランチ上の有効な固定の記録と、固定時点・開始・前向きの期間を出す。

    何も書かない。
    """
    lines: list[str] = []
    try:
        chosen = log or GitHoldoutAccessLog(repo_root, repo_root)
        branch, remote = chosen.fetch_origin()
        lines.append(f"origin の既定ブランチ: {branch}（{remote[:12]}）")
        fix = valid_fix_at_origin(chosen, experiment_id, execution)
    except HoldoutAccessLogStop as stop:
        return FixShowOutcome(EXIT_READ_ERROR, None, lines, f"{stop.stage}: {stop.detail}")
    except ConfigError as exc:
        return FixShowOutcome(EXIT_READ_ERROR, None, lines, str(exc))
    if fix is None:
        lines.append("有効な固定の記録が origin の既定ブランチに無い")
        return FixShowOutcome(EXIT_REFUSED, None, lines, "not fixed")
    period: Interval = fix.forward_period
    lines.extend(
        [
            f"固定の記録: {fix.path}（fix_no {fix.record.fix_no}）",
            f"固定時点 T_fix: {fix.t_fix}（コミット {fix.commit[:12]}）",
            f"長さ L: {fix.length_days} 日",
            f"開始 S: {period.start}",
            f"前向きの期間: [{period.start}, {period.end})",
            "組ごとの固定した試行: "
            + "、".join(f"{variant.value} {fix.fixed_trials.of(variant)}" for variant in VARIANTS),
        ]
    )
    return FixShowOutcome(EXIT_OK, fix, lines)
