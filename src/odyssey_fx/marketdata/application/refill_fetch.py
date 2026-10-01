"""再取得（補充）の取得と再開（D03 §14.5・§14.9・§14.11.1・§14.12 の出来事2〜9）。

取得計画の時間ファイルを提供元から取り、tick の保管場所に置いて、取得記録 `journal.jsonl`
に最終結果を 1 行ずつ追記する。プロセスが止まっても、同じ計画で取得をやり直せば有効な最終
結果のある時間ファイルは取らずに残りだけを取る。

本モジュールは通信・待ち・ファイル・実時計を**ポート越しにだけ**使う（`TickArchiveSource`・
`RefillStore`。D01 §4 v2.9）。何回・いつ要求するか（間隔・再試行・指数バックオフ・一時停止）、
状態の判定、保管場所の検算（W5）は本モジュールの規則である。

**状態**（D03 §14.12）は作業ディレクトリと補充分のディレクトリの中身から決まる:

- 書き出し済み: 完成した補充分のうち `refill_manifest.json` の `plan_id` がこの計画のものが
  ある（ほかの判定より先に行う）。
- 不合格: 最後の検証の結果の行が不合格で、その後に最終結果・無効化・取り直しの印の行が無い。
- 取得終了: すべての時間ファイルに有効な最終結果がある。
- 計画済み: 最終結果・取り直しの印・無効化の行が 1 行も無い。
- 取得中: それ以外（有効な最終結果の無い時間ファイルがある）。

**有効な最終結果**: 時間ファイルごとの最後の最終結果の行。ただし、その後に同じ時間の取り直し
の印か無効化の行があるものは数えない。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.application.ports import JournalLine, RefillStore, TickArchiveSource
from odyssey_fx.marketdata.application.refill_plan import (
    finalized_refills,
    require_consistent_refills,
    require_plan_matches,
)
from odyssey_fx.marketdata.domain.errors import (
    MarketDataValueError,
    RefillAlreadyFinalized,
    RefillPlanNotFound,
    RefillStoreInconsistent,
    RefillValidationFailed,
)
from odyssey_fx.marketdata.domain.refill import (
    RETRYABLE_FAILURES,
    ArchiveProvenance,
    ArchiveRead,
    AttemptRecord,
    CommunicationSettings,
    DecodedTicks,
    FailureKind,
    FinalResult,
    HourKey,
    HourOutcome,
    HttpResult,
    Invalidation,
    JournalEntry,
    PauseEnd,
    PauseStart,
    RefillPlan,
    RetryMark,
    ValidationRecord,
    journal_entry_from_payload,
    sha256_hex,
)

__all__ = [
    "FetchReport",
    "JournalState",
    "PlanState",
    "classify_status",
    "derive_state",
    "effective_finals",
    "fetch_plan",
    "read_journal",
    "seconds_to_wait",
    "verify_archive",
]


class PlanState(Enum):
    """補充の一回分の状態（D03 §14.12。一時停止はプロセスの中の状態なので含めない）。"""

    PLANNED = "PLANNED"
    FETCHING = "FETCHING"
    FETCH_DONE = "FETCH_DONE"
    FINALIZED = "FINALIZED"
    REJECTED = "REJECTED"


# --- 取得記録を読む（W3）---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JournalState:
    """取得記録を読んだ結果。`entries` は有効な行（読んだ順）。

    `broken_tail_offset` は最後の行が不完全なときのその行の先頭（書き手が追記の前に切り
    詰める。W3）。不完全でなければ `None`。
    """

    entries: tuple[JournalEntry, ...]
    broken_tail_offset: int | None


def read_journal(plan_id: str, lines: Sequence[JournalLine], plan: RefillPlan) -> JournalState:
    """取得記録の行を検算して行の型にする（D03 §14.11.1 の W3・W5）。

    最後の行だけが不完全なら書き込みの途中で止まった行として無効にする。最後の行以外が壊れて
    いる・行の形が読めない・計画に無い時間を指す行がある、といった食い違いは
    `RefillStoreInconsistent`（W6）。
    """
    entries: list[JournalEntry] = []
    broken_tail: int | None = None
    hours = set(plan.hour_keys)
    for index, line in enumerate(lines):
        if line.entry is None:
            if index == len(lines) - 1:
                broken_tail = line.offset
                break
            raise RefillStoreInconsistent(
                f"_work/{plan_id}/journal.jsonl line {index + 1} is broken ({line.problem}) and"
                " is not the last line. Nothing was written (D03 §14.11.1 W3・W6)"
            )
        try:
            entry = journal_entry_from_payload(line.entry)
        except MarketDataValueError as exc:
            raise RefillStoreInconsistent(
                f"_work/{plan_id}/journal.jsonl line {index + 1} cannot be read: {exc}."
                " Nothing was written (D03 §14.11.1 W3・W6)"
            ) from exc
        hour = getattr(entry, "hour", None)
        if hour is not None and hour not in hours:
            raise RefillStoreInconsistent(
                f"_work/{plan_id}/journal.jsonl line {index + 1} names the hour {hour}, which is"
                " not in the plan. Nothing was written (D03 §14.11.1 W5・W6)"
            )
        entries.append(entry)
    return JournalState(entries=tuple(entries), broken_tail_offset=broken_tail)


def effective_finals(entries: Sequence[JournalEntry]) -> dict[HourKey, FinalResult]:
    """時間ファイルごとの有効な最終結果（D03 §14.12）。"""
    finals: dict[HourKey, FinalResult] = {}
    for entry in entries:
        if isinstance(entry, FinalResult):
            finals[entry.hour] = entry
        elif isinstance(entry, (RetryMark, Invalidation)):
            finals.pop(entry.hour, None)
    return finals


def derive_state(
    plan: RefillPlan, entries: Sequence[JournalEntry], *, finalized: bool
) -> PlanState:
    """状態を取得記録と補充分の有無だけから決める（D03 §14.12）。"""
    if finalized:
        return PlanState.FINALIZED
    last_validation: ValidationRecord | None = None
    changed_after_validation = False
    any_fetch_line = False
    for entry in entries:
        if isinstance(entry, ValidationRecord):
            last_validation = entry
            changed_after_validation = False
        elif isinstance(entry, (FinalResult, RetryMark, Invalidation)):
            any_fetch_line = True
            changed_after_validation = True
    if last_validation is not None and not last_validation.passed and not changed_after_validation:
        return PlanState.REJECTED
    finals = effective_finals(entries)
    if all(key in finals for key in plan.hour_keys):
        return PlanState.FETCH_DONE
    if not any_fetch_line:
        return PlanState.PLANNED
    return PlanState.FETCHING


# --- 保管場所の検算（W5）---------------------------------------------------------------


def verify_archive(read: ArchiveRead, plan: RefillPlan) -> DecodedTicks:
    """保管場所の 1 件を使う前に識別子とダイジェストをすべて計算し直す（W5）。

    応答の中身の sha256＝出所の記録、解凍した tick の内容のダイジェスト＝ファイル名＝出所の
    記録、出所の `<source_digest>`＝ディレクトリ名＝計画の設定から求めた値、出所の URL＝
    置き場の `(銘柄, 時間)` と計画の URL の型から組み立て直した URL、tick の件数＝出所の記録。
    合わなければ `RefillStoreInconsistent`（W6）。
    """
    settings = plan.provider.settings
    problems: list[str] = []
    provenance = read.provenance
    if read.body_sha256 != provenance.response_sha256:
        problems.append("the content's sha256 differs from the recorded one")
    if read.decoded is None:
        problems.append(f"the content cannot be decoded ({read.decode_error})")
    else:
        if read.decoded.tick_digest != read.file_tick_digest:
            problems.append("the decoded tick digest differs from the file name")
        if read.decoded.tick_digest != provenance.tick_digest:
            problems.append("the decoded tick digest differs from the recorded one")
        if len(read.decoded.ticks) != provenance.tick_count:
            problems.append("the tick count differs from the recorded one")
    expected_source = settings.source_digest()
    if read.directory_source_digest != expected_source:
        problems.append("the directory is not the plan's provider settings directory")
    if provenance.source_digest != read.directory_source_digest:
        problems.append("the recorded source digest differs from the directory name")
    if provenance.url != settings.url_for(read.hour):
        problems.append(
            f"the recorded URL {provenance.url} is not the URL of {read.hour}"
            f" ({settings.url_for(read.hour)}); the file was copied or moved"
        )
    if problems or read.decoded is None:
        raise RefillStoreInconsistent(
            f"{read.path}: {'; '.join(problems)}. Nothing was written; delete the file after"
            " checking, then retry (D03 §14.11.1 W5・W6)"
        )
    return read.decoded


def _expected_archive_file(hour: HourKey, source_digest: str, tick_digest: str) -> str:
    return f"{hour.archive_directory}/{source_digest}/{tick_digest}.json"


def _read_archive_for(
    store: RefillStore, plan: RefillPlan, hour: HourKey, tick_digest: str
) -> ArchiveRead | None:
    source_digest = plan.provider.settings.source_digest()
    names = store.list_archive(hour, source_digest)
    if len(names) > 1:
        raise RefillStoreInconsistent(
            f"{hour.archive_directory}/{source_digest}/ holds {len(names)} completed files"
            f" ({', '.join(names)}); one hour and one setting keep exactly one."
            " Nothing was written (D03 §14.11.1 W5・W6)"
        )
    return store.read_archive(hour, source_digest, tick_digest)


# --- 待ち時間と応答の分類（規則）--------------------------------------------------------


def classify_status(status: int) -> FailureKind | None:
    """HTTP の状態を失敗の種類へ分ける。200 は `None`（成功）。"""
    if status == 200:
        return None
    if status == 404:
        return FailureKind.HTTP_404
    if status == 429:
        return FailureKind.HTTP_429
    if 500 <= status <= 599:
        return FailureKind.HTTP_5XX
    return FailureKind.HTTP_OTHER


def seconds_to_wait(
    now: UtcTime, last_request_end: UtcTime | None, required_seconds: float
) -> float:
    """前の要求の終わりから `required_seconds` 空けるために、いま待つ秒数（0 以上）。"""
    if last_request_end is None:
        return 0.0
    elapsed = (now - last_request_end).total_seconds()
    return max(0.0, required_seconds - elapsed)


# --- 取得 ------------------------------------------------------------------------------


@dataclass(slots=True)
class FetchReport:
    """取得 1 回の集計（代表例の試行の見直しの材料。D03 §14.9・§14.14 の段 3）。"""

    plan_id: str
    state_before: PlanState
    state_after: PlanState
    skipped: int = 0
    from_archive: int = 0
    outcomes: Counter[HourOutcome] = field(default_factory=Counter)
    failures: Counter[FailureKind] = field(default_factory=Counter)
    retry_marks: int = 0
    invalidations: int = 0
    pauses: int = 0
    requests: int = 0
    started_at: UtcTime | None = None
    finished_at: UtcTime | None = None


class _Fetcher:
    """取得の 1 回分（プロセスの中の状態: 連続失敗の数と前の要求の終わり）。"""

    def __init__(
        self,
        *,
        plan_id: str,
        plan: RefillPlan,
        store: RefillStore,
        source: TickArchiveSource,
        report: FetchReport,
    ) -> None:
        self._plan_id = plan_id
        self._plan = plan
        self._store = store
        self._source = source
        self._report = report
        self._comm: CommunicationSettings = plan.provider.settings.communication
        self._consecutive_failures = 0
        self._last_request_end: UtcTime | None = None

    def _append(self, entry: JournalEntry) -> None:
        self._store.append_journal(self._plan_id, entry.payload())

    def _wait_for_request(self, required_seconds: float) -> None:
        wait = seconds_to_wait(self._source.now(), self._last_request_end, required_seconds)
        if wait > 0:
            self._source.wait(wait)

    def _record_failure(
        self, hour: HourKey, attempt: int, failure: FailureKind, result: HttpResult | None
    ) -> None:
        self._report.failures[failure] += 1
        self._append(
            AttemptRecord(
                hour=hour,
                attempt=attempt,
                failure=failure,
                http_status=None if result is None else result.status,
                detail="" if result is None else result.detail,
                at=self._source.now(),
            )
        )

    def _pause_if_needed(self) -> None:
        if self._consecutive_failures < self._comm.pause_after_consecutive_failures:
            return
        self._append(
            PauseStart(
                consecutive_failures=self._consecutive_failures,
                seconds=self._comm.pause_seconds,
                at=self._source.now(),
            )
        )
        self._source.wait(self._comm.pause_seconds)
        self._append(PauseEnd(at=self._source.now()))
        self._report.pauses += 1
        self._consecutive_failures = 0

    def _finish_not_fetched(
        self, hour: HourKey, attempts: int, failure: FailureKind, result: HttpResult | None
    ) -> None:
        self._append(
            FinalResult(
                hour=hour,
                outcome=HourOutcome.NOT_FETCHED,
                from_archive=False,
                tick_digest=None,
                tick_count=None,
                archive_file=None,
                url=self._plan.provider.settings.url_for(hour),
                fetched_at=None,
                http_status=None if result is None else result.status,
                response_sha256=None,
                attempts=attempts,
                failure=failure,
                detail="" if result is None else result.detail,
                at=self._source.now(),
            )
        )
        self._report.outcomes[HourOutcome.NOT_FETCHED] += 1

    def _use_archive(self, hour: HourKey) -> bool:
        """保管場所に完成したものがあれば検算して使う（通信しない。D03 §14.5）。"""
        settings = self._plan.provider.settings
        source_digest = settings.source_digest()
        names = self._store.list_archive(hour, source_digest)
        if not names:
            return False
        if len(names) > 1:
            raise RefillStoreInconsistent(
                f"{hour.archive_directory}/{source_digest}/ holds {len(names)} completed files"
                f" ({', '.join(names)}); one hour and one setting keep exactly one."
                " Nothing was written (D03 §14.11.1 W5・W6)"
            )
        tick_digest = names[0].removesuffix(".json")
        read = self._store.read_archive(hour, source_digest, tick_digest)
        if read is None:  # pragma: no cover - 一覧の直後に消えた
            return False
        decoded = verify_archive(read, self._plan)
        self._append(
            FinalResult(
                hour=hour,
                outcome=decoded.outcome,
                from_archive=True,
                tick_digest=decoded.tick_digest,
                tick_count=len(decoded.ticks),
                archive_file=read.path,
                url=None,
                fetched_at=None,
                http_status=None,
                response_sha256=None,
                attempts=0,
                failure=None,
                detail="",
                at=self._source.now(),
            )
        )
        self._report.outcomes[decoded.outcome] += 1
        self._report.from_archive += 1
        return True

    def fetch_hour(self, hour: HourKey) -> None:
        """時間ファイル 1 本を取り、最終結果を 1 行追記する（D03 §14.9・§14.12）。"""
        settings = self._plan.provider.settings
        url = settings.url_for(hour)
        attempts = 0
        locked = False
        try:
            while True:
                failure: FailureKind | None = None
                result: HttpResult | None = None
                if not locked:
                    locked = self._store.acquire_hour_lock(hour)
                    if locked and self._use_archive(hour):
                        return
                if not locked:
                    failure = FailureKind.HOUR_LOCKED
                else:
                    required = float(self._comm.request_interval_seconds)
                    if attempts:
                        required = max(required, float(self._comm.backoff_seconds(attempts)))
                    self._wait_for_request(required)
                    result = self._source.request(url, self._comm.request_timeout_seconds)
                    self._last_request_end = self._source.now()
                    self._report.requests += 1
                    failure = (
                        result.failure if result.status is None else classify_status(result.status)
                    )
                    if failure is None:
                        try:
                            decoded = self._source.decode(result.body)
                        except MarketDataValueError as exc:
                            failure = FailureKind.INVALID_CONTENT
                            result = HttpResult(
                                url=result.url,
                                status=result.status,
                                body=b"",
                                failure=None,
                                detail=str(exc),
                                fetched_at=result.fetched_at,
                            )
                        else:
                            self._consecutive_failures = 0
                            self._store_fetched(hour, attempts + 1, result, decoded)
                            return
                attempts += 1
                if failure not in RETRYABLE_FAILURES:
                    # HTTP 404 とその他の状態は再試行しない（D03 §14.18 の 3）。
                    self._consecutive_failures = 0
                    self._report.failures[failure] += 1
                    self._finish_not_fetched(hour, attempts, failure, result)
                    return
                self._record_failure(hour, attempts, failure, result)
                if failure is not FailureKind.HOUR_LOCKED:
                    self._consecutive_failures += 1
                if failure is not FailureKind.HOUR_LOCKED:
                    # 上限に達した最後の失敗でも、連続失敗が数に達していれば一時停止してから
                    # 次の時間ファイルへ進む（別の時間ファイルをまたいで数える。D03 §14.9）。
                    self._pause_if_needed()
                if attempts > self._comm.max_retries:
                    self._finish_not_fetched(hour, attempts, failure, result)
                    return
                if failure is FailureKind.HOUR_LOCKED:
                    self._source.wait(self._comm.backoff_seconds(attempts))
        finally:
            if locked:
                self._store.release_hour_lock(hour)

    def _store_fetched(
        self, hour: HourKey, attempts: int, result: HttpResult, decoded: DecodedTicks
    ) -> None:
        """取得した中身と出所を保管場所に置いてから、最終結果を追記する（W4）。"""
        settings = self._plan.provider.settings
        provenance = ArchiveProvenance(
            url=result.url,
            fetched_at=result.fetched_at,
            http_status=200 if result.status is None else result.status,
            attempts=attempts,
            response_sha256=sha256_hex(result.body),
            tick_digest=decoded.tick_digest,
            tick_count=len(decoded.ticks),
            provider_id=settings.id,
            provider_version=settings.version,
            provider_content_digest=self._plan.provider.content_digest,
            source_digest=settings.source_digest(),
        )
        path = self._store.write_archive(hour, result.body, provenance)
        expected = _expected_archive_file(hour, provenance.source_digest, decoded.tick_digest)
        if path != expected:  # pragma: no cover - adapters の約束
            raise RefillStoreInconsistent(f"the archive was written to {path}, not {expected}")
        self._append(
            FinalResult(
                hour=hour,
                outcome=decoded.outcome,
                from_archive=False,
                tick_digest=decoded.tick_digest,
                tick_count=len(decoded.ticks),
                archive_file=path,
                url=result.url,
                fetched_at=result.fetched_at,
                http_status=provenance.http_status,
                response_sha256=provenance.response_sha256,
                attempts=attempts,
                failure=None,
                detail="",
                at=self._source.now(),
            )
        )
        self._report.outcomes[decoded.outcome] += 1


def _check_finals(
    plan_id: str,
    plan: RefillPlan,
    store: RefillStore,
    entries: Sequence[JournalEntry],
    now: UtcTime,
) -> list[Invalidation]:
    """有効な最終結果が指す保管場所のものを検算する（W5）。指すものが無ければ無効化の行。"""
    invalidations: list[Invalidation] = []
    source_digest = plan.provider.settings.source_digest()
    for hour, final in sorted(
        effective_finals(entries).items(), key=lambda item: item[0].sort_key()
    ):
        if final.outcome is HourOutcome.NOT_FETCHED:
            continue
        assert final.tick_digest is not None  # FinalResult の不変条件
        expected = _expected_archive_file(hour, source_digest, final.tick_digest)
        if final.archive_file != expected:
            raise RefillStoreInconsistent(
                f"_work/{plan_id}/journal.jsonl: the final result of {hour} points at"
                f" {final.archive_file}, not {expected}. Nothing was written"
                " (D03 §14.11.1 W5・W6)"
            )
        read = _read_archive_for(store, plan, hour, final.tick_digest)
        if read is None:
            invalidations.append(Invalidation(hour=hour, reason=f"{expected} is missing", at=now))
            continue
        decoded = verify_archive(read, plan)
        if decoded.outcome is not final.outcome:
            raise RefillStoreInconsistent(
                f"{expected}: the archived ticks give {decoded.outcome.value}, but the final"
                f" result says {final.outcome.value}. Nothing was written (D03 §14.11.1 W5)"
            )
    return invalidations


def fetch_plan(
    plan_id: str,
    *,
    store: RefillStore,
    source: TickArchiveSource,
    retry_failed: bool,
) -> FetchReport:
    """取得を始める・再開する（D03 §14.12 の出来事2）。`retry_failed` は出来事9。

    手順:

    1. 作業ディレクトリが無ければ、この計画の補充分があれば `RefillAlreadyFinalized`、無ければ
       `RefillPlanNotFound`。
    2. 計画のロックを取る（取れなければ `RefillPlanLocked`。状態を判定しない）。
    3. 検算（W5・W6）: 書きかけの補充分、`plan.json` の `plan_id`、取得記録の各行、有効な
       最終結果が指す保管場所のもの。最後の行だけが不完全なら切り詰める（W3）。
    4. 状態を判定する。書き出し済みなら `RefillAlreadyFinalized`（書き出した計画は取得し
       直さない）。
    5. 指すものが消えた最終結果は無効化の行を追記する（以後は取り直しの対象）。
    6. `retry_failed` なら、計画済みでは何もしない。不合格で `NOT_FETCHED` が無ければ
       `RefillValidationFailed`。それ以外は `NOT_FETCHED` の時間ごとに取り直しの印を追記する。
    7. 有効な最終結果の無い時間ファイルを計画の順に取得する。
    """
    if not store.work_dir_exists(plan_id):
        finished = finalized_refills(store, plan_id)
        if finished:
            raise RefillAlreadyFinalized(
                f"the plan {plan_id} is already finalized as {', '.join(finished)};"
                " a finalized plan is not fetched again (D03 §14.12)"
            )
        raise RefillPlanNotFound(
            f"no refill plan {plan_id} under _work/; create it with"
            " `odyssey-fx data refill plan` (D03 §14.12)"
        )
    store.acquire_plan_lock(plan_id)
    try:
        return _fetch_locked(plan_id, store=store, source=source, retry_failed=retry_failed)
    finally:
        store.release_plan_lock(plan_id)


def _fetch_locked(
    plan_id: str,
    *,
    store: RefillStore,
    source: TickArchiveSource,
    retry_failed: bool,
) -> FetchReport:
    require_consistent_refills(store)
    plan = require_plan_matches(plan_id, store.read_plan(plan_id))
    journal = read_journal(plan_id, store.read_journal(plan_id), plan)
    finalized = bool(finalized_refills(store, plan_id))
    if finalized:
        raise RefillAlreadyFinalized(
            f"the plan {plan_id} is already finalized; a finalized plan is not fetched again."
            " Retry failed hours with a new plan from the new snapshot (D03 §14.12, RF-7)"
        )
    if journal.broken_tail_offset is not None:
        store.truncate_journal(plan_id, journal.broken_tail_offset)
    entries = list(journal.entries)
    state_before = derive_state(plan, entries, finalized=False)
    report = FetchReport(
        plan_id=plan_id,
        state_before=state_before,
        state_after=state_before,
        started_at=source.now(),
    )

    for invalidation in _check_finals(plan_id, plan, store, entries, source.now()):
        store.append_journal(plan_id, invalidation.payload())
        entries.append(invalidation)
        report.invalidations += 1

    if retry_failed:
        state = derive_state(plan, entries, finalized=False)
        failed = [
            hour
            for hour, final in sorted(
                effective_finals(entries).items(), key=lambda item: item[0].sort_key()
            )
            if final.outcome is HourOutcome.NOT_FETCHED
        ]
        if state is PlanState.PLANNED:
            report.finished_at = source.now()
            return report  # 取り直す時間ファイルが無い（D03 §14.12 の計画済み×出来事9）
        if state is PlanState.REJECTED and not failed:
            raise RefillValidationFailed(
                f"the plan {plan_id} failed validation and has no hour to retry; fix the cause"
                " and re-plan (D03 §14.12)"
            )
        for hour in failed:
            mark = RetryMark(hour=hour, at=source.now())
            store.append_journal(plan_id, mark.payload())
            entries.append(mark)
            report.retry_marks += 1

    finals = effective_finals(entries)
    fetcher = _Fetcher(plan_id=plan_id, plan=plan, store=store, source=source, report=report)
    for hour in plan.hour_keys:
        if hour in finals:
            report.skipped += 1
            continue
        fetcher.fetch_hour(hour)

    lines = read_journal(plan_id, store.read_journal(plan_id), plan)
    report.state_after = derive_state(plan, lines.entries, finalized=False)
    report.finished_at = source.now()
    return report
