"""判断履歴と結果の保存（D06 §9.1、ADR-0027）。

`TraceSink` と `ResultWriter`（`backtest.application.ports`）を実装する。ポート定義は
import せず、構造的に満たす（D01 §2.2 規則7）。保存先は `runs/<run_id>/` で、表形式データは
Parquet、manifest は JSON である。

**平坦化の規則は `backtest.trace.recorder` が持つ**（D06 §9.1 が決めたのは backtest 側の
責務）。ここは受け取った行を `flatten_row` で列の辞書にしてから書くだけで、規則を持たない。
DataFrame はこのモジュールの外へ出さない（D01 §2.2 規則1）。

十進数はすべて**文字列列**として保存する。二進浮動小数へ落とすと再現性が壊れる
（ADR-0012）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

import polars as pl

from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.domain.fills import CostKind
from odyssey_fx.backtest.domain.policies import (
    HierarchyCheckResult,
    ResolutionHierarchy,
    RunConfig,
)
from odyssey_fx.backtest.trace.manifest import (
    DataCapabilityReport,
    RunManifest,
    config_digest_of,
)
from odyssey_fx.backtest.trace.recorder import (
    TraceTable,
    canonical_text,
    column_kinds,
    column_names,
    flatten_row,
    table_column_kinds,
    table_columns,
)
from odyssey_fx.backtest.trace.result import BacktestResult, FinalSummaries, RunStatus
from odyssey_fx.common.canonical import digest, encode
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AccountId, ExperimentId, RunId, SnapshotId
from odyssey_fx.common.money import (
    CurrencyCode,
    Money,
    PriceOffset,
    decimal_from_str,
    kernel_context,
)
from odyssey_fx.common.reason import Reason, ReasonCode
from odyssey_fx.common.refs import (
    CodeDigest,
    CompiledStrategyRef,
    ConfigDigest,
    ContentDigest,
    EnvDigest,
    LockDigest,
    PolicyRef,
    SnapshotRef,
    StrategyRef,
)
from odyssey_fx.common.symbol import Symbol, SymbolSpecRef
from odyssey_fx.common.time import Interval, PhaseRank, PhaseSet, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.application.evaluate_run import EvaluationReport
from odyssey_fx.evaluation.application.manifest import (
    EvaluationManifest,
    EvaluationTable,
    RunEvaluationId,
)
from odyssey_fx.evaluation.application.ports import (
    ColumnValueKind,
    EvaluationReadFailure,
    ManifestReadFailure,
    ManifestSaveResult,
    ResultReadFailure,
    StoredEvaluation,
    TableReadResult,
    TraceColumnSpec,
    TrialLedgerAppendRefused,
    TrialLedgerContents,
    TrialLedgerReadFailure,
    TrialLedgerRefusal,
)
from odyssey_fx.evaluation.domain.errors import ArtifactAlreadyExists
from odyssey_fx.evaluation.domain.experiment import (
    ExperimentManifest,
    ExperimentOutcome,
    ExperimentStatus,
    ResolvedFile,
    require_experiment_name,
)
from odyssey_fx.evaluation.domain.metrics import (
    AmountValue,
    CategoryCount,
    CountValue,
    DurationValue,
    FillDiagnostic,
    MetricCaveat,
    MetricId,
    MetricKind,
    MetricRecord,
    MetricUnavailableReason,
    MetricValue,
    PriceOffsetValue,
    RatioValue,
    TradeRecord,
    Unavailable,
)
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityLimits,
    ComplexityMeasures,
    PolicyCheck,
    PolicyCheckResult,
    PolicyCheckStage,
)
from odyssey_fx.evaluation.domain.search import (
    AggregateCondition,
    CandidateStatus,
    Comparator,
    ComparisonBasis,
    ConditionOutcome,
    ConditionResult,
    ConditionScope,
    EvaluationStandard,
    FoldSelection,
    FoldStatistic,
    FoldVerdict,
    FrequencyAssessment,
    FrequencyClass,
    MetricCondition,
    ParameterAssignment,
    ParameterAxis,
    SearchOutcome,
    SearchPlan,
    SearchPlanKind,
    SearchVerdict,
    SelectionDirection,
    SelectionRule,
    StandardPurpose,
    SufficiencyRule,
    SufficiencyShortfall,
    SufficiencyShortfallKind,
    TrialLedgerBinding,
    TrialLedgerDefect,
    TrialLedgerEntry,
    TrialLedgerEvent,
    TrialLedgerLine,
    TrialPhase,
    TrialPlan,
    TrialRunRecord,
    TrialStartRecord,
    TrialStatus,
    TrialUnitKey,
    ValidationRule,
    last_digest,
    ledger_defect,
    ledger_line_digest,
)
from odyssey_fx.evaluation.domain.splits import (
    FinalHoldoutSpec,
    Fold,
    SplitKind,
    SplitSpec,
    SplitStandard,
    SplitWindow,
)
from odyssey_fx.evaluation.domain.status import (
    CheckOutcome,
    ConsistencyCheckResult,
    EvaluationStatus,
)
from odyssey_fx.marketdata.domain.access import AccessClass
from odyssey_fx.marketdata.domain.bar import BarKey
from odyssey_fx.marketdata.domain.integrity import (
    CheckKind,
    CheckResult,
    IntegrityReport,
    Severity,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.strategy.declarations.specs import (
    BoolValue,
    FloatValue,
    IntValue,
    ParameterValue,
    StrValue,
)

__all__ = [
    "EXPERIMENT_MANIFEST_FILE",
    "EXPERIMENT_OUTCOME_FILE",
    "LEDGER_BINDING_FILE",
    "REPORT_FILE",
    "REPRODUCTION_FILE",
    "RETREAT_MARKER_FILE",
    "SEARCH_DIRECTORY",
    "TRIAL_LEDGER_LOCK_PATH",
    "TRIAL_LEDGER_PATH",
    "TRIAL_METRICS_TABLE",
    "TRIAL_UNITS_TABLE",
    "UNITS_DIRECTORY",
    "UNIT_NAME",
    "FileSystemExperimentStore",
    "FileSystemResultRepository",
    "FileSystemResultWriter",
    "FileSystemTraceSink",
    "create_artifact_directory",
    "ensure_experiment_directory",
    "evaluation_directory",
    "experiment_directory",
    "experiment_manifest_from_payload",
    "experiment_manifest_payload",
    "experiment_outcome_from_payload",
    "experiment_outcome_payload",
    "append_trial_ledger_file",
    "keep_previous_records",
    "keep_previous_report",
    "keep_previous_search_records",
    "ledger_entry_of",
    "ledger_line_bytes",
    "manifest_from_payload",
    "next_kept_number",
    "read_experiment_manifest",
    "read_experiment_outcome",
    "read_ledger_binding",
    "read_ledger_binding_records",
    "read_selections",
    "read_trial_ledger_file",
    "read_trial_metrics",
    "read_unit_records",
    "replaced_manifest_name",
    "reproduction_path",
    "reproduction_payload",
    "require_absent",
    "require_plain_experiment_path",
    "require_run_directory_absent",
    "reserve_run_directory",
    "result_from_payload",
    "run_directory",
    "search_directory",
    "trial_units_rows",
    "unit_name",
    "unit_of_name",
    "write_new_file",
    "write_reproduction",
]


def run_directory(root: Path, run_id: object) -> Path:
    """`runs/<run_id>/`（ADR-0027）。"""
    return Path(root) / "runs" / str(run_id)


def _columns(
    rows: Sequence[Mapping[str, object]], declared: Sequence[str]
) -> dict[str, list[object]]:
    """行の並びを保ったまま、列ごとの値へ組み替える。

    行ごとに列が欠けることは無い（平坦化の規則が「その行の変種に無い列は `None`」と定めて
    いる）。**行が1件も無い表でも列は落とさない**。取引が1件も無かった正常な run と、必須の
    列を欠いた壊れた表とを読む側が区別できなくなるためである。
    """
    names: list[str] = list(declared)
    for row in rows:
        for name in row:
            if name not in names:
                names.append(name)
    return {name: [row.get(name) for row in rows] for name in names}


def _already_exists(directory: Path, remedy: str) -> ArtifactAlreadyExists:
    """書き出し先が既にあることの例外（R4）。文言は検査の場所によらず1つにする。"""
    return ArtifactAlreadyExists(
        f"{directory} already exists; artifacts are never overwritten, and nothing was"
        f" written. {remedy} (R4)"
    )


def require_absent(directory: Path, *, remedy: str) -> None:
    """書き出し先がまだ無いことを確かめる（R4。作らない）。

    書き出しより前に、無駄な計算を始める前に止めたいときに使う。作ること自体で確かめる
    `create_artifact_directory` の代わりにはならない（確かめた後に別の実行が作る隙間が
    残る）ので、書き出しの直前には必ずそちらを通す。壊れたシンボリックリンクも「ある」と
    数える。
    """
    if directory.exists() or directory.is_symlink():
        raise _already_exists(directory, remedy)


def _nearest_existing(path: Path) -> Path | None:
    """`path` とその祖先のうち、最初に「ある」もの（リンクは辿らずに判定）。無ければ None。"""
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or candidate.exists():
            return candidate
    return None


def _require_root_is_directory(base: Path) -> None:
    """成果物の根（`runs/`）と、それが無いときはその最も近い既存の祖先（`--out` など）が、
    （リンクの先が）ディレクトリでなければならない。

    根は利用者が指す置き場の一部なので、別ディスクへのリンクでもよい（D06 §9.1、
    2026-09-28 の人間の決定）。ただし通常ファイルや先の無いリンクなら、その下に成果物は
    書けない。根がまだ無いときは、根を作るときに通る最初の既存の祖先を同じ条件で見る
    （`--out` そのものが通常ファイルなら、根の作成が失敗する）。ここで型付きに止めないと、
    実行前の検査は子の保存先を「無い」と判定して run を最後まで走らせ、書き出しで
    未捕捉の例外になる（D06 §10.6）。
    """
    existing = _nearest_existing(base)
    if existing is not None and not existing.is_dir():
        raise _root_not_a_directory(existing)


def _root_not_a_directory(path: Path) -> ArtifactAlreadyExists:
    return ArtifactAlreadyExists(
        f"{path} exists but is not a directory (or links to none); artifacts are written"
        " only under a directory, and nothing was written. Move or delete it first"
        " (D06 §9.1, R4)"
    )


def _make_root(base: Path) -> None:
    """成果物の根を（無ければ祖先ごと）作る。作れなければ型付きで失敗する。

    種類は `_require_root_is_directory` で先に見ているが、確かめた後に別のプロセスが
    祖先へファイルを置いた場合も未捕捉の例外にはしない。
    """
    _require_root_is_directory(base)
    try:
        base.mkdir(parents=True, exist_ok=True)
    except (FileExistsError, NotADirectoryError) as error:
        raise _root_not_a_directory(_nearest_existing(base) or base) from error


def _make_plain_parents(base: Path, directory: Path, error: ArtifactAlreadyExists) -> None:
    """`base` から `directory` の親までの要素を、リンクでない実ディレクトリとして用意する。

    `base`（成果物の根 `runs/`。利用者が指す置き場の一部で、別ディスクへのリンクでもよい。
    D06 §9.1、2026-09-28 の人間の決定）そのものは検査しない。その下の要素
    （`<run_id>`・`eval` など）がリンクや別の種類なら、リンク先や根の外に成果物が
    書かれるので、何も作らずに `error` で失敗する。無い要素は作る。
    """
    relative = directory.relative_to(base)
    current = base
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise error
    current = base
    for part in relative.parts[:-1]:
        current = current / part
        current.mkdir(exist_ok=True)


def create_artifact_directory(directory: Path, *, base: Path, remedy: str) -> Path:
    """成果物の書き出し先を新しく作る（R4。D06 §9.1、D07 §8.2）。

    成果物の書き込みは「**存在すれば、何も書かずに失敗する**」。空のディレクトリでも、
    前の実行が途中で落ちて残した書きかけのディレクトリでも同じに扱う。中身を見て
    「書きかけなら続きを書く」とすると、新旧の成果物が混ざる。

    確かめてから作ると、確かめた後に別の実行が同じディレクトリを作る隙間が残るので、
    **作ること自体で確かめる**（既にあれば作成が失敗する）。
    `base` から `directory` の親までの要素は、リンクでない実ディレクトリでなければ
    ならない（無ければ作る）。`base` そのものは利用者が指す場所なので検査しない。
    `remedy` は利用者が取れる手当て（置換の指示、または人間による移動・削除）の説明。
    """
    _make_root(base)
    _make_plain_parents(
        base,
        directory,
        ArtifactAlreadyExists(
            f"a directory between {base} and {directory} is a symbolic link or not a"
            " directory; artifacts are never written through links, and nothing was written."
            " Replace it with a plain directory first (R4)"
        ),
    )
    try:
        directory.mkdir()
    except FileExistsError:
        raise _already_exists(directory, remedy) from None
    return directory


#: run の成果物が既にあるときの手当て（ADR-0006。置換の指示を持つのは run だけ）。
_RUN_REMEDY = (
    "It already holds artifacts for this run (re-running the same complete input produces"
    " the same RunId), or was left by an earlier attempt that stopped halfway."
    " Pass replace=True (`odyssey-fx run --replace`) to replace it; the previous manifest"
    " is kept as manifest.replaced.<NNN>.json, one file per replacement (ADR-0006)"
)

#: 置換で残した旧 manifest の名前（世代番号は 001 から欠番なく増える。D06 §9.3）。
#: 番号の無い `manifest.replaced.json` は v1.12 より前の置換が残したもので、消さずに残す。
_REPLACED_MANIFEST = re.compile(r"manifest\.replaced(?:\.(\d{3,}))?\.json")

#: 世代の候補として数える名前の接頭辞と、世代に数えない旧形式の名前。
_REPLACED_PREFIX = "manifest.replaced"
_LEGACY_REPLACED_MANIFEST = "manifest.replaced.json"


def replaced_manifest_name(generation: int) -> str:
    """置換の第 `generation` 世代で残す旧 manifest の名前（D06 §9.3）。"""
    if generation < 1:
        raise KernelValueError(f"replacement generations start at 1, got {generation}")
    return f"manifest.replaced.{generation:03d}.json"


def _kept_generations(directory: Path) -> int:
    """置換で残した旧 manifest の世代が 001 から欠番なく並ぶことを確かめ、その数を返す。

    **置換の手順の先頭で、現在の manifest の有無に依らず必ず1回行う**（D06 §9.3）。
    manifest の無い書きかけの run でも、欠番のある並びの上に置換を重ねない。

    `manifest.replaced` で始まる名前は、正しい世代名でなくても（`01`・`abc` など）すべて
    世代の候補として数える。正規の名前だけを数えると、表記の崩れた旧 manifest が並びの
    検査をすり抜けて、そのあと消されてしまう。番号の無い旧形式 `manifest.replaced.json`
    だけは世代に数えず、消さずに残す。
    """
    kept = sorted(
        path.name
        for path in directory.iterdir()
        if path.name.startswith(_REPLACED_PREFIX) and path.name != _LEGACY_REPLACED_MANIFEST
    )
    expected = sorted(replaced_manifest_name(number) for number in range(1, len(kept) + 1))
    if kept != expected:
        # 欠番・重複表記（`0001` など）のある並びに次の世代を足すと、壊れた履歴のまま
        # 置換が成功する。**何も作らず何も消さずに**止める。
        raise ArtifactAlreadyExists(
            f"{directory} keeps replaced manifests {kept}, which do not run from 001 without"
            " gaps; nothing was replaced. Restore that order before replacing again"
            " (D06 §9.3, R4)"
        )
    return len(kept)


def _require_plain_entries(directory: Path) -> None:
    """置換が読む・残す・消す・畳む項目が、想定した種類の本物であることを確かめる（D06 §9.3）。

    置換の手順の先頭で、何かを作る・消す前に1回行う。**run ディレクトリ直下のすべての項目**
    を見る（名前を知っている項目だけを見ると、知らない名前の穴が毎回1つずつ残る）。
    許すのは、リンクでない通常のファイル（旧 manifest・残した世代・番号の無い旧形式
    `manifest.replaced.json`・19表の Parquet・`result.json` など、置換が残すか消すもの）と、
    リンクでないディレクトリの評価の成果物（`eval`。置換が畳むもの）だけである。
    それ以外（リンク、`eval` 以外のディレクトリ、その他の種類）が1つでもあれば、何も作らず
    何も消さずに失敗する。受け入れると、実体の無い世代を残したり、リンク先を消したり畳んだり、
    途中で書き込みが失敗して新旧の成果物が混ざった run が残る。
    """
    for path in sorted(directory.iterdir()):
        if path.is_symlink():
            plain, kind = False, "a regular file or the eval directory"
        elif path.name == _EVALUATION_DIRECTORY:
            plain, kind = path.is_dir(), "a directory"
        else:
            plain, kind = path.is_file(), "a regular file"
        if not plain:
            raise ArtifactAlreadyExists(
                f"{path} must be {kind} that is not a symbolic link; nothing was replaced."
                " Move or delete it first (D06 §9.3, R4)"
            )


def _keep_replaced_manifest(directory: Path, previous: Path, generation: int) -> Path:
    """旧 manifest を第 `generation` 世代の名前で残す（ADR-0006、D06 §9.3、R4）。

    世代の並びは `_kept_generations` が先に確かめている。その名前のファイルが既にあるのは、
    確かめた後に別の置換が作ったときだけで（同じ run の同時置換は設計の対象外）、
    **そのときも上書きせずに失敗する**。作成と存在の確認は排他的な作成1回で行う。
    """
    target = directory / replaced_manifest_name(generation)
    content = previous.read_text(encoding="utf-8")
    try:
        with target.open("x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError:
        raise ArtifactAlreadyExists(
            f"{target} already exists; the manifests kept by earlier replacements are never"
            " overwritten, and nothing was replaced. The kept generations are expected to run"
            " from 001 without gaps; restore that order before replacing again"
            " (D06 §9.3, R4)"
        ) from None
    return target


#: 評価の成果物が既にあるときの手当て（評価は置換の指示を持たない。D07 §8.2）。
_EVALUATION_REMEDY = (
    "Evaluations are never replaced; move or delete that directory first if this"
    " evaluation should be written again (D07 §8.2)"
)


def require_run_directory_absent(root: Path, run_id: object) -> None:
    """run を始める前に、`runs/<run_id>/` がまだ無いことを確かめる（D06 §10.6、R4）。

    run の識別子は実行前に決まるので、実行してから書き出しで失敗するより先に止める。
    置換を指示した run では呼ばない。書き出しの直前の確保（`reserve_run_directory`）は
    これとは別にもう一度行う。
    """
    _require_root_is_directory(Path(root) / "runs")
    require_absent(run_directory(root, run_id), remedy=_RUN_REMEDY)


def reserve_run_directory(root: Path, run_id: object, *, replace: bool = False) -> Path:
    """成果物の置き場所を確保する（ADR-0006、D06 §9.1、R4）。

    同じ完全入力の再実行は同じ `RunId` になるので、`runs/<run_id>/` が既にあることは
    ふつうに起こる。**既定は「存在すれば、何も書かずに失敗する」**（R4。空のディレクトリ
    でも失敗する）。置換は明示的な指示があるときだけ行い、置換したときも**旧 manifest を
    記録に残す**（ADR-0006）。

    途中まで書いたところで失敗すると新旧の表が混ざるので、書き始める前にここで判断する。
    """
    directory = run_directory(root, run_id)
    # 根の種類は置換の有無に依らず先に見る（置換経路で未捕捉の例外にしない）。
    _require_root_is_directory(Path(root) / "runs")
    if not replace:
        return create_artifact_directory(directory, base=Path(root) / "runs", remedy=_RUN_REMEDY)
    if directory.is_symlink():
        # 置換はリンクを辿らない。辿ると、リンク先（別の run や根の外）を消してしまう。
        raise ArtifactAlreadyExists(
            f"{directory} is a symbolic link; a replacement never follows links, and nothing"
            " was replaced. Remove the link first (D06 §9.3, R4)"
        )
    if directory.exists() and not directory.is_dir():
        # ディレクトリでないもの（ファイルなど）は置換の対象にしない。
        raise ArtifactAlreadyExists(
            f"{directory} exists but is not a directory; nothing was replaced. Move or delete"
            " it first (D06 §9.3, R4)"
        )
    existing = sorted(directory.glob("*")) if directory.exists() else []
    if existing:
        # 何かを作る・消す前に、残した世代の並びを1回だけ確かめる（現在の manifest の
        # 有無に依らない。D06 §9.3）。
        _require_plain_entries(directory)
        generations = _kept_generations(directory)
        previous = directory / "manifest.json"
        if previous.exists():
            # 置換しても旧成果物の manifest は記録に残す（ADR-0006）。**世代ごとに別名で
            # 残し、既存の世代は上書きしない**（D06 §9.3、R4）。消す前に残すので、残せな
            # ければ何も消さずに失敗する。
            _keep_replaced_manifest(directory, previous, generations + 1)
        for path in existing:
            if path.is_file() and not _REPLACED_MANIFEST.fullmatch(path.name):
                path.unlink()
            elif path.is_dir() and path.name == _EVALUATION_DIRECTORY:
                # **評価の成果物も一緒に畳む**。判断履歴だけを書き直すと、同じ実行の
                # 識別子の下に「前の判断履歴から作った指標」と「新しい判断履歴」が並ぶ。
                # 置換を頼むのは成果物が壊れているときなので、古い指標が信用できる値として
                # 残るのがいちばん危うい（ADR-0006）。
                shutil.rmtree(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass(slots=True)
class FileSystemTraceSink:
    """19表を `runs/<run_id>/<TABLE>.parquet` へ書く（D06 §9.1・§9.2）。

    最初の書き出しの前に置き場所を確保し、書き出し先が既にあれば何も書かずに失敗する
    （ADR-0006、R4）。
    """

    root: Path
    run_id: object
    replace: bool = False
    #: 置き場所を確保したかどうか。run のはじめに1度だけ検査するための覚えで、
    #: 書き出し口の同一性には関わらない。
    _reserved: bool = field(default=False, repr=False, compare=False)

    def write(self, table: TraceTable, rows: tuple[object, ...]) -> None:
        """1つの表を書き出す。書き出し専用で、検索元にはならない。

        **全行が `run_id` を持つ**（上位設計書 §4.7.15）。連番 ID は run 内でのみ一意なので、
        永続参照は `(run_id, ID)` の組になる。行そのものが `run_id` を持たない表でも、
        ここで必ず列として足す。
        """
        if not self._reserved:
            reserve_run_directory(self.root, self.run_id, replace=self.replace)
            self._reserved = True
        directory = run_directory(self.root, self.run_id)
        run_id = str(self.run_id)
        flattened = [{"run_id": run_id, **flatten_row(row)} for row in rows]
        columns = _columns(flattened, table_columns(table))
        kinds = table_column_kinds(table)
        frame = pl.DataFrame(
            columns,
            schema={name: _dtype_of(kinds.get(name, "string")) for name in columns},
            strict=False,
        )
        frame.write_parquet(directory / f"{table.value}.parquet")


#: 列の物理的な型（`backtest.trace.recorder` が宣言した名前から引く）。
_DTYPES: Mapping[str, pl.DataType] = {
    "string": pl.String(),
    "int": pl.Int64(),
    "bool": pl.Boolean(),
    "list": pl.List(pl.String()),
}


def _dtype_of(kind: str) -> pl.DataType:
    """宣言された型に対応する物理的な型。

    行の有無で型が変わらないよう、**推論せず宣言に従う**。十進数は文字列で保存する決まり
    なので（ADR-0012）、実際に文字列以外になるのは整数・真偽・`list` の列だけである。
    """
    return _DTYPES.get(kind, pl.String())


@dataclass(frozen=True, slots=True)
class FileSystemResultWriter:
    """run manifest と結果 DTO を JSON で書く（ADR-0027）。"""

    root: Path

    def write(self, result: BacktestResult, manifest: RunManifest) -> None:
        """`manifest.json` と `result.json` を書き出す。

        置き場所の確保（既存成果物の検査）は判断履歴の書き出し口が run のはじめに行う
        （ADR-0006）。ここで作り直すと、同じ run の途中でもう一度検査することになる。
        """
        directory = run_directory(self.root, manifest.run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "manifest.json").write_text(
            json.dumps(_manifest_payload(manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (directory / "result.json").write_text(
            json.dumps(_result_payload(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _manifest_payload(manifest: RunManifest) -> dict[str, Any]:
    """run manifest の7群を JSON へ落とす（D06 §9.3）。

    能力検査の結果は**全体のまま**入れる（要約に畳まない）。入れ子のレコードは D02 §9.3 の
    正規化エンコード文字列にし、外部ツールが同じ文字列からダイジェストを再計算できるように
    する。
    """
    return {
        "run_id": str(manifest.run_id),
        "config_digest": str(manifest.config_digest.digest),
        "code_digest": manifest.code_digest.digest.hex,
        "lock_digest": manifest.lock_digest.digest.hex,
        "env_digest": manifest.env_digest.digest.hex,
        "git_commit": manifest.git_commit,
        "git_dirty": manifest.git_dirty,
        "config": canonical_text(manifest.config),
        "phases": [{"rank": phase.rank, "name": phase.name} for phase in manifest.phases.ordered()],
        "id_allocator_snapshot": dict(manifest.id_allocator_snapshot),
        "symbol_spec_ref": canonical_text(manifest.symbol_spec_ref),
        "calendar_ref": manifest.calendar_ref,
        "timeframe_def_refs": [str(ref) for ref in manifest.timeframe_def_refs],
        "resolution_hierarchy": [str(level) for level in manifest.resolution_hierarchy.levels],
        "unresolved_intrabar_count": manifest.unresolved_intrabar_count,
        "unresolved_intrabar_ratio": str(manifest.unresolved_intrabar_ratio),
        "swap_modeled": manifest.swap_modeled,
        "capability_report": _capability_payload(manifest.capability_report),
        "status": manifest.status,
        "reason": None if manifest.reason is None else manifest.reason.code.value,
        "warnings": list(manifest.warnings),
    }


def _capability_payload(report: DataCapabilityReport) -> dict[str, Any]:
    """データ能力検査の結果を**全体のまま** JSON へ落とす（D06 §9.3 の能力検査の群）。

    要約に畳まず、合格・不合格のどちらでも個別の結果を残す。run manifest と結果 DTO の
    両方が同じ内容を持つのは、`FAILED_CAPABILITY` の run では判断履歴の表が空になりうる
    ため、結果からも直接読めるようにするという D06 §9.4 の要求による。
    """
    return {
        "compiled_match": report.compiled_match,
        "runnable": report.runnable,
        "reason": None if report.reason is None else report.reason.code.value,
        "integrity": [canonical_text(result) for result in report.integrity.results],
        "hierarchy_checks": [canonical_text(check) for check in report.hierarchy_checks],
        "diagnostics": list(report.diagnostics),
    }


def _result_payload(result: BacktestResult) -> dict[str, Any]:
    """結果 DTO を JSON へ落とす（D06 §9.4）。"""
    summaries = result.summaries
    return {
        "run_id": str(result.run_id),
        "status": result.status.value,
        "manifest_ref": result.manifest_ref,
        "trace_tables": {table.value: path for table, path in result.trace_tables.items()},
        "capability_report": _capability_payload(result.capability_report),
        "swap_modeled": result.swap_modeled,
        "unresolved_intrabar_count": result.unresolved_intrabar_count,
        "trade_count": result.trade_count,
        "opportunity_count": result.opportunity_count,
        "summaries": None
        if summaries is None
        else {
            "realized": canonical_text(summaries.realized),
            "equity_with_mtm": canonical_text(summaries.equity_with_mtm),
            "hypothetical_closed": canonical_text(summaries.hypothetical_closed),
            "cost_breakdown": {
                kind.value: canonical_text(amount)
                for kind, amount in summaries.cost_breakdown.items()
            },
        },
    }


# --- run 成果物の読み戻し（D07 §4.1・§4.3）-----------------------------------


#: `runs/<run_id>/` の下で評価の成果物を置くディレクトリ（D07 §8.2、Q4 決定）。
_EVALUATION_DIRECTORY = "eval"


def evaluation_directory(root: Path, run_id: RunId, run_evaluation_id: RunEvaluationId) -> Path:
    """`runs/<run_id>/eval/<run_evaluation_id>/`（D07 §8.2、Q4 決定）。

    識別子が違う成果物を同じ場所へ書かない。評価コードを変えて評価し直した結果は
    `RunEvaluationId` が別の値になるので、前の成果物を上書きしない。
    """
    return run_directory(root, run_id) / _EVALUATION_DIRECTORY / str(run_evaluation_id)


def _digest_of(hex_value: object, label: str) -> ContentDigest:
    if not isinstance(hex_value, str):
        raise KernelValueError(f"{label} must be a hexadecimal string, got {hex_value!r}")
    return ContentDigest.sha256(hex_value)


def _loads(text: object, label: str) -> Any:
    """保存された正規化エンコード文字列を構造へ戻す（D02 §9.3）。

    正規化エンコードは JSON 互換のテキストなので（同節）、そのまま読み戻せる。数値は
    十進の文字列として入っているので、`Decimal` へは文字列から作る（ADR-0012）。
    """
    if not isinstance(text, str):
        raise KernelValueError(f"{label} must be a canonical-encoded string, got {text!r}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise KernelValueError(f"{label} is not canonical-encoded JSON: {exc}") from exc


def _interval_of(payload: Mapping[str, Any]) -> Interval:
    return Interval(start=UtcTime.parse(payload["start"]), end=UtcTime.parse(payload["end"]))


def _series_of(text: str, timeframes: Mapping[str, TimeframeRef]) -> SeriesId:
    """`USDJPY/15m/bid` から系列を組み立てる（D03 §3.1）。

    系列の文字列は時間足の**版を持たない**（同節: 「版は manifest の `conversion` と
    `series` が別に保持する」）。run manifest が記録している時間足定義の版参照から引く。
    引けない系列は、manifest が使った定義を特定できないということなので拒否する。
    """
    parts = text.split("/")
    if len(parts) != 3:
        raise KernelValueError(f"invalid series literal in the run manifest: {text!r}")
    symbol, timeframe_id, basis = parts
    timeframe = timeframes.get(timeframe_id)
    if timeframe is None:
        raise KernelValueError(
            f"the run manifest records the series {text!r} but declares no timeframe definition"
            f" for {timeframe_id!r}; the version of the definition cannot be recovered"
            " (D03 §3.1)"
        )
    return SeriesId(symbol=Symbol(symbol), timeframe=timeframe, basis=PriceBasis(basis))


def _policy_ref_of(payload: Mapping[str, Any]) -> PolicyRef:
    return PolicyRef(
        policy_kind=payload["policy_kind"],
        policy_id=payload["policy_id"],
        version=int(payload["version"]),
        digest=_digest_of(payload["digest"]["hex"], "PolicyRef.digest"),
    )


def _money_of(payload: Mapping[str, Any]) -> Money:
    return Money(decimal_from_str(payload["amount"]), CurrencyCode(payload["currency"]))


def _reason_of(code: object) -> Reason | None:
    """理由コードだけを読み戻す（D02 §8.2）。

    **型付き詳細（`detail`）は復元しない**。run manifest は理由コードだけを記録しており
    （D06 §9.3 の保存形式）、詳細は判断履歴の `*_detail` 列に正規化エンコード文字列として
    残る。無い値を作って埋めないため、ここでは詳細なしの `Reason` を返す。
    """
    if code is None:
        return None
    if not isinstance(code, str):
        raise KernelValueError(f"a reason code must be a string, got {code!r}")
    return Reason(ReasonCode(code))


def _check_result_of(text: str, timeframes: Mapping[str, TimeframeRef]) -> CheckResult:
    payload = _loads(text, "IntegrityReport entry")
    return CheckResult(
        kind=CheckKind(payload["kind"]),
        severity=Severity(payload["severity"]),
        series=_series_of(payload["series"], timeframes),
        interval=_interval_of(payload["interval"]),
        detail=tuple((key, value) for key, value in payload.get("detail", ())),
    )


def _bar_key_of(
    payload: Mapping[str, Any] | None, timeframes: Mapping[str, TimeframeRef]
) -> BarKey | None:
    if payload is None:
        return None
    return BarKey(
        series=_series_of(payload["series"], timeframes),
        bar_start=UtcTime.parse(payload["bar_start"]),
    )


def _hierarchy_check_of(text: str, timeframes: Mapping[str, TimeframeRef]) -> HierarchyCheckResult:
    payload = _loads(text, "HierarchyCheckResult entry")

    def series(key: str) -> SeriesId | None:
        value = payload.get(key)
        return None if value is None else _series_of(value, timeframes)

    def interval(key: str) -> Interval | None:
        value = payload.get(key)
        return None if value is None else _interval_of(value)

    def moment(key: str) -> UtcTime | None:
        value = payload.get(key)
        return None if value is None else UtcTime.parse(value)

    def basis(key: str) -> PriceBasis | None:
        value = payload.get(key)
        return None if value is None else PriceBasis(value)

    parent = series("parent_series")
    if parent is None:  # pragma: no cover - 構築時に必須の項目
        raise KernelValueError("HierarchyCheckResult.parent_series is missing from the manifest")
    return HierarchyCheckResult(
        check=payload["check"],
        passed=bool(payload["passed"]),
        parent_series=parent,
        child_series=series("child_series"),
        parent_bar=_bar_key_of(payload.get("parent_bar"), timeframes),
        child_bar=_bar_key_of(payload.get("child_bar"), timeframes),
        expected_interval=interval("expected_interval"),
        child_intervals=tuple(_interval_of(item) for item in payload.get("child_intervals", ())),
        coverage_gaps=tuple(_interval_of(item) for item in payload.get("coverage_gaps", ())),
        coverage_overlaps=tuple(
            _interval_of(item) for item in payload.get("coverage_overlaps", ())
        ),
        expected_boundary=moment("expected_boundary"),
        observed_boundary=moment("observed_boundary"),
        expected_basis=basis("expected_basis"),
        observed_basis=basis("observed_basis"),
        expected_available_at=moment("expected_available_at"),
        observed_available_at=moment("observed_available_at"),
    )


def _capability_report_of(
    payload: Mapping[str, Any], timeframes: Mapping[str, TimeframeRef]
) -> DataCapabilityReport:
    return DataCapabilityReport(
        compiled_match=bool(payload["compiled_match"]),
        integrity=IntegrityReport(
            results=tuple(
                _check_result_of(item, timeframes) for item in payload.get("integrity", ())
            )
        ),
        hierarchy_checks=tuple(
            _hierarchy_check_of(item, timeframes) for item in payload.get("hierarchy_checks", ())
        ),
        runnable=bool(payload["runnable"]),
        reason=_reason_of(payload.get("reason")),
        diagnostics=tuple(payload.get("diagnostics", ())),
    )


def _run_config_of(payload: Mapping[str, Any], timeframes: Mapping[str, TimeframeRef]) -> RunConfig:
    account = payload["account"]
    return RunConfig(
        run_interval=_interval_of(payload["run_interval"]),
        snapshot_ref=SnapshotRef(
            snapshot_id=SnapshotId(_digest_of(payload["snapshot_ref"]["snapshot_id"], "SnapshotId"))
        ),
        compiled_ref=CompiledStrategyRef(
            digest=_digest_of(payload["compiled_ref"]["digest"]["hex"], "CompiledStrategyRef")
        ),
        account=AccountSpec(
            account_id=AccountId(account["account_id"]),
            currency=CurrencyCode(account["currency"]),
            initial_balance=_money_of(account["initial_balance"]),
        ),
        risk_policy_ref=_policy_ref_of(payload["risk_policy_ref"]),
        execution_policy_ref=_policy_ref_of(payload["execution_policy_ref"]),
        cost_model_ref=_policy_ref_of(payload["cost_model_ref"]),
        conversion_policy_ref=_policy_ref_of(payload["conversion_policy_ref"]),
        delay_scenario_ref=_policy_ref_of(payload["delay_scenario_ref"]),
        execution_series=_series_of(payload["execution_series"], timeframes),
        seed=int(payload["seed"]),
    )


def _timeframes_of(payload: Mapping[str, Any]) -> dict[str, TimeframeRef]:
    """manifest が記録した時間足定義の版参照を `id` で引けるようにする（D03 §3.1）。"""
    return {
        ref.id: ref for ref in (TimeframeRef.parse(item) for item in payload["timeframe_def_refs"])
    }


def manifest_from_payload(payload: Mapping[str, Any]) -> RunManifest:
    """保存した run manifest を読み戻す（D06 §9.3、D07 §4.1 の入力2）。

    `FileSystemResultWriter.write` が書いた JSON と対になる。入れ子のレコードは正規化
    エンコード文字列で保存されており（同節）、その表現は JSON 互換なのでそのまま読める。
    """
    timeframes = _timeframes_of(payload)
    config = _run_config_of(_loads(payload["config"], "RunManifest.config"), timeframes)
    symbol_spec = _loads(payload["symbol_spec_ref"], "RunManifest.symbol_spec_ref")
    symbol_spec_ref = SymbolSpecRef(
        symbol=Symbol(symbol_spec["symbol"]),
        version=int(symbol_spec["version"]),
        digest=_digest_of(symbol_spec["digest"]["hex"], "SymbolSpecRef.digest"),
    )
    timeframe_def_refs = tuple(TimeframeRef.parse(item) for item in payload["timeframe_def_refs"])
    stored_digest = ConfigDigest(_digest_of(payload["config_digest"], "ConfigDigest"))
    recomputed = config_digest_of(
        config,
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=payload["calendar_ref"],
        timeframe_def_refs=timeframe_def_refs,
    )
    if recomputed != stored_digest:
        # **設定の中身と、その指紋を別々に信じない**（D06 §9.3、ADR-0006）。`RunManifest` は
        # 実行の識別子が4つのダイジェストから来ていることを確かめるが、設定の中身がその
        # ダイジェストと合っているかは見ない。中身だけを書き換えた manifest を通すと、
        # たとえば run 区間を変えるだけで指標（保有時間の割合など）が変わるのに、整合検査
        # 8件はすべて合格し、実行と評価の識別子も同じままになる。
        raise KernelValueError(
            f"the run manifest records the config digest {stored_digest.digest.hex} but its"
            f" config hashes to {recomputed.digest.hex}; the recorded settings and their"
            " fingerprint disagree (D06 §9.3)"
        )
    return RunManifest(
        run_id=RunId(_digest_of(payload["run_id"], "RunId")),
        config=config,
        config_digest=stored_digest,
        code_digest=CodeDigest(_digest_of(payload["code_digest"], "CodeDigest")),
        lock_digest=LockDigest(_digest_of(payload["lock_digest"], "LockDigest")),
        env_digest=EnvDigest(_digest_of(payload["env_digest"], "EnvDigest")),
        phases=PhaseSet(
            tuple(
                PhaseRank(rank=int(item["rank"]), name=item["name"]) for item in payload["phases"]
            )
        ),
        id_allocator_snapshot={
            key: int(value) for key, value in payload["id_allocator_snapshot"].items()
        },
        capability_report=_capability_report_of(payload["capability_report"], timeframes),
        resolution_hierarchy=ResolutionHierarchy(
            levels=tuple(_series_of(item, timeframes) for item in payload["resolution_hierarchy"])
        ),
        unresolved_intrabar_count=int(payload["unresolved_intrabar_count"]),
        unresolved_intrabar_ratio=decimal_from_str(payload["unresolved_intrabar_ratio"]),
        swap_modeled=bool(payload["swap_modeled"]),
        status=payload["status"],
        symbol_spec_ref=symbol_spec_ref,
        calendar_ref=payload["calendar_ref"],
        timeframe_def_refs=timeframe_def_refs,
        git_commit=payload["git_commit"],
        git_dirty=bool(payload["git_dirty"]),
        reason=_reason_of(payload.get("reason")),
        warnings=tuple(payload.get("warnings", ())),
    )


def result_from_payload(
    payload: Mapping[str, Any], timeframes: Mapping[str, TimeframeRef]
) -> BacktestResult:
    """保存した結果 DTO を読み戻す（D06 §9.4、D07 §4.1 の入力1）。"""
    summaries = payload["summaries"]
    return BacktestResult(
        run_id=RunId(_digest_of(payload["run_id"], "RunId")),
        status=RunStatus(payload["status"]),
        trace_tables={TraceTable(name): path for name, path in payload["trace_tables"].items()},
        capability_report=_capability_report_of(payload["capability_report"], timeframes),
        manifest_ref=payload["manifest_ref"],
        swap_modeled=bool(payload["swap_modeled"]),
        unresolved_intrabar_count=int(payload["unresolved_intrabar_count"]),
        trade_count=int(payload["trade_count"]),
        opportunity_count=int(payload["opportunity_count"]),
        summaries=None
        if summaries is None
        else FinalSummaries(
            realized=_money_of(_loads(summaries["realized"], "FinalSummaries.realized")),
            equity_with_mtm=_money_of(
                _loads(summaries["equity_with_mtm"], "FinalSummaries.equity_with_mtm")
            ),
            hypothetical_closed=_money_of(
                _loads(summaries["hypothetical_closed"], "FinalSummaries.hypothetical_closed")
            ),
            cost_breakdown={
                CostKind(name): _money_of(_loads(amount, "FinalSummaries.cost_breakdown"))
                for name, amount in summaries["cost_breakdown"].items()
            },
        ),
    )


#: 評価結果の5表と、その行の型（D07 §8.1）。行が1件も無くても列は落とさない。
_EVALUATION_ROW_TYPES: Mapping[EvaluationTable, type] = {
    EvaluationTable.METRICS: MetricRecord,
    EvaluationTable.CATEGORY_COUNTS: CategoryCount,
    EvaluationTable.TRADES: TradeRecord,
    EvaluationTable.FILL_DIAGNOSTICS: FillDiagnostic,
    EvaluationTable.CONSISTENCY_CHECKS: ConsistencyCheckResult,
}


def _list_column(value: object) -> str | None:
    """`list` 列の値を1つの文字列にする（D07 §4.3 の `LIST_STRING`）。

    判断履歴の可変長の列は正規化エンコード文字列の `list` として保存されている
    （D06 §9.1）。読み出しの口は文字列（または `None`）の行を返す約束なので、要素の列を
    JSON の配列として1つの文字列に畳む。要素そのものは触らない。
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    raise KernelValueError(
        f"a LIST_STRING column must hold a sequence of canonical-encoded strings, got {value!r}"
    )


def _linked_run_path(directory: Path) -> Path | None:
    """run のディレクトリ（と、あればその `eval`）のうち、リンクまたはディレクトリでないもの。

    成果物の根 `runs/` の下はリンクを辿らない（R4。D06 §9.1）。評価の書き込み
    （`create_artifact_directory`）は `<run_id>` と `eval` がリンクなら失敗するので、読む側も
    同じ境界で「読めない」とする。
    """
    for candidate in (directory, directory / _EVALUATION_DIRECTORY):
        if candidate.is_symlink() or (candidate.exists() and not candidate.is_dir()):
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class FileSystemResultRepository:
    """run の成果物の読み書き（D01 §4、D07 §4.3・§8.2）。

    `ResultRepository`（`evaluation.application.ports`）を構造的に満たす。Parquet を開くのは
    このモジュールだけで、`domain` と `application` には表形式ライブラリを入れない
    （D01 §5・ADR-0025）。

    `read_result` は D07 v2.0 §4.1 でポートの操作になった（段階4 の `RunExperiment` が既存の
    run 成果物を再利用するときに読むため。実装 PR 3）。
    """

    root: Path

    def read_manifest(self, run_id: RunId) -> RunManifest | ManifestReadFailure:
        """`runs/<run_id>/manifest.json` を読む（D06 §9.3）。

        **読めないとき（ファイルが無い・JSON として壊れている・項目が欠けている・設定と
        その指紋が食い違う）は例外にせず `ManifestReadFailure` を返す**（D07 v2.0 §3、
        §10.1.1 の R1-D07-4）。評価はそれを整合検査 C11 の不合格として残す。
        """
        path = run_directory(self.root, run_id) / "manifest.json"
        if not path.is_file():
            return ManifestReadFailure(
                run_id=run_id,
                detail=canonical_text(f"{path.name} does not exist; this run has no manifest"),
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise KernelValueError("the run manifest must be a JSON object")
            return manifest_from_payload(payload)
        except OSError as exc:
            # **絶対パスを観測値に入れない**（D07 §9.1 の条件4）。`OSError` の文字列表現は
            # パスを含むので、理由の文言だけを残す。入れると同じ run の成果物を別の場所に
            # 置いただけで結果のダイジェストが変わる。
            return ManifestReadFailure(
                run_id=run_id,
                detail=canonical_text(f"{type(exc).__name__}: {exc.strerror} ({path.name})"),
            )
        except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
            # `KernelValueError` と JSON の読み取りの失敗は `ValueError` の派生。項目の欠落は
            # `KeyError`、形の違う値は `TypeError` / `AttributeError`、桁あふれ（JSON の
            # `1e999` を整数にするなど）は `ArithmeticError` として来る。
            return ManifestReadFailure(
                run_id=run_id, detail=canonical_text(f"{type(exc).__name__}: {exc}")
            )

    def read_result(self, run_id: RunId) -> BacktestResult | ResultReadFailure:
        """`runs/<run_id>/result.json` を読む（D06 §9.4、D07 v2.0 §4.1）。

        **読めないとき（ファイルが無い・壊れている・中身の `run_id` が引数と違う）は例外に
        せず `ResultReadFailure` を返す**（D07 v2.0 §3・§4.1。`read_manifest` と同じ扱い）。
        """
        directory = run_directory(self.root, run_id)
        result_path = directory / "result.json"
        manifest_path = directory / "manifest.json"
        linked = _linked_run_path(directory)
        if linked is not None:
            # リンク越しの run は「読めない」とする。読めても、その run の評価は書き込みの
            # 検査（R4。根の下のリンクを辿らない）で必ず止まるので、再利用や評価の対象に
            # 選んでから失敗させない（D07 §19.6 の手順1〜3・5。PR #47 第2巡）。
            return ResultReadFailure(
                run_id=run_id,
                detail=(
                    f"{linked} is a symbolic link or not a directory; run artifacts are never"
                    " read or written through links (D06 §9.1, R4)"
                ),
            )
        if not result_path.is_file():
            return ResultReadFailure(
                run_id=run_id,
                detail=f"{result_path} does not exist; this run has no result to read",
            )
        if not manifest_path.is_file():
            return ResultReadFailure(
                run_id=run_id,
                detail=(
                    f"{manifest_path} does not exist; the timeframe definitions recorded there"
                    " are needed to read the series of the result (D03 §3.1)"
                ),
            )
        try:
            timeframes = _timeframes_of(json.loads(manifest_path.read_text(encoding="utf-8")))
            result = result_from_payload(
                json.loads(result_path.read_text(encoding="utf-8")), timeframes
            )
        except OSError as exc:
            return ResultReadFailure(
                run_id=run_id, detail=f"{type(exc).__name__}: {exc.strerror} ({result_path})"
            )
        except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
            return ResultReadFailure(
                run_id=run_id, detail=f"{result_path}: {type(exc).__name__}: {exc}"
            )
        if result.run_id != run_id:
            # **読んだ場所と中身の実行の識別子が食い違ったまま進めない**。評価はこのあと
            # 結果 DTO の識別子で manifest と判断履歴を引くので、食い違ったまま通すと、
            # 利用者が指した run とは**別の run** の指標を、しかも別の場所へ書いてしまう。
            # 整合検査 C2 はその別の run の中では辻褄が合うので気付けない。
            return ResultReadFailure(
                run_id=run_id,
                detail=(
                    f"{result_path} holds the result of run {result.run_id} but it was read as"
                    f" {run_id}; evaluating it would produce metrics for a different run"
                    " (D07 §4.1・§10.2 の C2)"
                ),
            )
        return result

    def run_exists(self, run_id: RunId) -> bool:
        """`runs/<run_id>/` があるか（D07 §19.6 の手順1。空・書きかけ・リンクも「ある」）。"""
        directory = run_directory(self.root, run_id)
        return directory.exists() or directory.is_symlink()

    def read_evaluation(
        self, run_id: RunId, run_evaluation_id: RunEvaluationId
    ) -> StoredEvaluation | EvaluationReadFailure | None:
        """評価 manifest を読む（D07 §19.6 の手順2・5）。保存先が無ければ `None`。"""
        directory = evaluation_directory(self.root, run_id, run_evaluation_id)
        if not (directory.exists() or directory.is_symlink()):
            return None
        path = directory / "evaluation.json"
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise KernelValueError("the evaluation directory is not a plain directory")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise KernelValueError("the evaluation manifest must be a JSON object")
            return StoredEvaluation(
                run_id=RunId(_digest_of(payload["run_id"], "run_id")),
                run_evaluation_id=RunEvaluationId(
                    _digest_of(payload["run_evaluation_id"], "run_evaluation_id")
                ),
                metric_set_version=payload["metric_set_version"],
                status=EvaluationStatus(payload["status"]),
                result_digest=_digest_of(payload["result_digest"], "result_digest"),
            )
        except OSError as exc:
            return EvaluationReadFailure(
                run_id=run_id, detail=f"{type(exc).__name__}: {exc.strerror} ({path})"
            )
        except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
            return EvaluationReadFailure(
                run_id=run_id, detail=f"{path}: {type(exc).__name__}: {exc}"
            )

    def read_evaluation_metrics(
        self, run_id: RunId, run_evaluation_id: RunEvaluationId
    ) -> tuple[MetricRecord, ...] | EvaluationReadFailure:
        """保存済みの評価の `METRICS` 表を読む（D07 v2.11 §3・§19.6、D09 §17.7.4 の2）。

        行を保存された順（`MetricId` の宣言順）の `MetricRecord` として返す。0行の表は空の組。
        保存先か表が無い・読めない・`MetricRecord` の形で読めない・同じ保存先の評価 manifest の
        識別子が引数と合わないときは `EvaluationReadFailure` を返す。**評価をやり直して補わない**。
        """
        directory = evaluation_directory(self.root, run_id, run_evaluation_id)
        path = directory / f"{EvaluationTable.METRICS.value}.parquet"
        try:
            if directory.is_symlink() or not directory.is_dir():
                raise KernelValueError(
                    "the evaluation directory is missing or not a plain directory"
                )
            stored = self.read_evaluation(run_id, run_evaluation_id)
            if stored is None or isinstance(stored, EvaluationReadFailure):
                detail = "missing" if stored is None else stored.detail
                raise KernelValueError(f"the evaluation manifest cannot be read: {detail}")
            if stored.run_id != run_id or stored.run_evaluation_id != run_evaluation_id:
                raise KernelValueError(
                    f"the evaluation manifest records {stored.run_evaluation_id} of the run"
                    f" {stored.run_id}, not {run_evaluation_id} of {run_id}"
                )
            if path.is_symlink() or not path.is_file():
                raise KernelValueError(f"{path.name} is missing or not a plain file")
            frame = pl.read_parquet(path)
            expected = column_names(MetricRecord)
            if tuple(frame.columns) != expected:
                raise KernelValueError(
                    f"the columns {list(frame.columns)} are not the METRICS columns"
                    f" {list(expected)}"
                )
            records = tuple(_metric_record_of(row) for row in frame.iter_rows(named=True))
            order = [_METRIC_ORDER[record.metric_id.value] for record in records]
            if order != sorted(set(order)):
                raise KernelValueError(
                    "the metric rows are not in the MetricId declaration order without repeats"
                )
            return records
        except OSError as exc:
            return EvaluationReadFailure(
                run_id=run_id, detail=f"{type(exc).__name__}: {exc.strerror} ({path})"
            )
        except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError) as exc:
            return EvaluationReadFailure(
                run_id=run_id, detail=f"{path}: {type(exc).__name__}: {exc}"
            )
        except pl.exceptions.PolarsError as exc:
            return EvaluationReadFailure(
                run_id=run_id, detail=f"{path}: {type(exc).__name__}: {exc}"
            )

    def read_table(
        self, run_id: RunId, table: TraceTable, columns: tuple[TraceColumnSpec, ...]
    ) -> TableReadResult:
        """判断履歴の表から、要求した列だけを読む（D07 §4.3）。

        **列が無いことと行が0件であることを戻り値で区別する**。表が無ければ
        `table_present=False`、要求した列のうち表に無いものは `missing_columns` に入れ、
        `rows` は空にする。読み出しの時点では例外にしない（評価は「なぜ評価できなかったか」を
        残すのが仕事であり、ここで落ちると理由が残らない）。
        """
        names = tuple(spec.column for spec in columns)
        kinds = tuple(spec.value_kind for spec in columns)
        path = run_directory(self.root, run_id) / f"{table.value}.parquet"
        if not path.is_file():
            return TableReadResult(table=table, table_present=False)
        frame = pl.read_parquet(path)
        missing = tuple(name for name in names if name not in frame.columns)
        if missing:
            return TableReadResult(table=table, table_present=True, missing_columns=missing)
        selected = frame.select(list(names))
        rows: list[tuple[str | None, ...]] = []
        for record in selected.iter_rows():
            rows.append(
                tuple(
                    _list_column(value)
                    if kind is ColumnValueKind.LIST_STRING
                    else (None if value is None else str(value))
                    for value, kind in zip(record, kinds, strict=True)
                )
            )
        return TableReadResult(table=table, table_present=True, rows=tuple(rows))

    def write_evaluation(
        self, report: EvaluationReport, rows: Mapping[EvaluationTable, tuple[object, ...]]
    ) -> None:
        """評価結果の5表と評価 manifest を書く（D07 §8.1・§8.2）。

        保存先は `runs/<run_id>/eval/<run_evaluation_id>/`（Q4 決定）。**どの状態でも5表
        すべてを書く**。表の有無で状態を表すと、書き出しが途中で落ちた成果物と区別できない。
        保存先が既にあれば、何も書かずに `ArtifactAlreadyExists` で失敗する（R4）。
        """
        manifest = report.manifest
        if dict(rows) != report.rows:
            # 結果のダイジェストは `EvaluationReport.rows` から作られている（D07 §9.2）。
            # 別の行を書くと、ダイジェストと保存された表が食い違う成果物ができる。
            raise KernelValueError(
                "write_evaluation must save the same rows the result digest was built from"
                " (D07 §9.2); the report and the rows given disagree"
            )
        directory = evaluation_directory(self.root, manifest.run_id, manifest.run_evaluation_id)
        # 書き出し先は新しく作る。既にあれば何も書かずに失敗する（D07 §8.2、R4）。評価は
        # 置換の指示を持たない（置換を許すのは run の成果物だけ。ADR-0006）。
        create_artifact_directory(
            directory, base=Path(self.root) / "runs", remedy=_EVALUATION_REMEDY
        )
        for table in EvaluationTable:
            row_type = _EVALUATION_ROW_TYPES[table]
            declared = column_names(row_type)
            flattened = [flatten_row(row) for row in rows.get(table, ())]
            values = _columns(flattened, declared)
            kinds = column_kinds(row_type)
            frame = pl.DataFrame(
                values,
                schema={name: _dtype_of(kinds.get(name, "string")) for name in values},
                strict=False,
            )
            frame.write_parquet(directory / f"{table.value}.parquet")
        (directory / "evaluation.json").write_text(
            json.dumps(_evaluation_payload(manifest), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_evaluation_manifest(
        self, run_id: RunId, run_evaluation_id: RunEvaluationId
    ) -> dict[str, Any]:
        """保存した評価 manifest を読む（受入れ確認と再現性の比較に使う）。"""
        path = evaluation_directory(self.root, run_id, run_evaluation_id) / "evaluation.json"
        if not path.is_file():
            raise KernelValueError(f"{path} does not exist")
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return payload


def _evaluation_payload(manifest: EvaluationManifest) -> dict[str, Any]:
    """評価 manifest の6群を JSON へ落とす（D07 §8.3）。

    **実行時刻を入れない**。壁時計の時刻を入れると同じ判断履歴から同じ成果物が出なくなる。
    run manifest から写す5項目は、run manifest が読めなかったとき `null` になる
    （D07 §10.1.1 の R1-D07-4）。
    """
    reason = manifest.run_failure_reason
    calendar_id, calendar_version = manifest.calendar_ref
    return {
        "run_evaluation_id": str(manifest.run_evaluation_id),
        "run_id": str(manifest.run_id),
        "run_manifest_ref": None
        if manifest.run_manifest_ref is None
        else manifest.run_manifest_ref.hex,
        "metric_set_version": manifest.metric_set_version,
        "calendar_ref": {"id": calendar_id, "version": calendar_version},
        "evaluation_code_digest": manifest.evaluation_code_digest.digest.hex,
        "run_code_digest": None
        if manifest.run_code_digest is None
        else manifest.run_code_digest.digest.hex,
        "input_tables": [table.value for table in manifest.input_tables],
        "account_currency": None
        if manifest.account_currency is None
        else str(manifest.account_currency),
        "run_status": None if manifest.run_status is None else manifest.run_status.value,
        "run_failure_reason": None if reason is None else reason.code.value,
        "swap_modeled": manifest.swap_modeled,
        "status": manifest.status.value,
        "fatal_failure_count": manifest.fatal_failure_count,
        "warning_failure_count": manifest.warning_failure_count,
        "unreadable_check_count": manifest.unreadable_check_count,
        "result_digest": manifest.result_digest.hex,
    }


# --- 実験の記録票と結末記録（D07 §19.1・§19.3）-----------------------------------

#: 記録票・結末記録のファイル名（D07 §19.1）。
EXPERIMENT_MANIFEST_FILE = "experiment_manifest.json"
EXPERIMENT_OUTCOME_FILE = "experiment_outcome.json"

#: レポートのファイル名（D07 §22.1。本文は `evaluation.adapters.report` が作る）。
REPORT_FILE = "report.md"

#: 退避した旧い結末記録（`experiment_outcome.<n>.json`）と旧いレポート（`report.<n>.md`）。
#: n は 1 からの連番で、**2つで1つの連番を共有する**（D07 §19.3・§22.1）。
_KEPT_OUTCOME = re.compile(r"experiment_outcome\.([1-9][0-9]*)\.json")
_KEPT_REPORT = re.compile(r"report\.([1-9][0-9]*)\.md")


def next_kept_number(directory: Path) -> int:
    """次に退避する記録の連番（退避済みの結末記録・レポート・探索の記録の番号の最大 + 1）。

    探索の記録 `search.<n>/` の番号も数える（D09 §11.3。途中で止まった実行は探索の記録だけを
    残すので、数えないと同じ番号へ退避しようとして衝突する）。

    結末記録とレポートは同じ実行のものを同じ番号で退避する（D07 §22.1「結末記録と同じ連番」）。
    片方だけを退避するとき（`experiment report` がレポートだけを書き直すとき）も同じ連番から
    取り、番号が2つの系列で食い違わないようにする。
    """
    kept = [
        int(match.group(1))
        for entry in Path(directory).iterdir()
        if (
            match := _KEPT_OUTCOME.fullmatch(entry.name)
            or _KEPT_REPORT.fullmatch(entry.name)
            or _KEPT_SEARCH.fullmatch(entry.name)
        )
    ]
    return max(kept, default=0) + 1


def _present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _keep_file(current: Path, target: Path) -> None:
    """`current` を `target` へ退避する。退避先は上書きしない（排他的な作成。R4）。"""
    if current.is_symlink() or not current.is_file():
        raise ArtifactAlreadyExists(
            f"{current} is not a plain file; it was left as is and nothing was written"
            " (D07 §19.3・§22.1)"
        )
    try:
        os.link(current, target)
    except FileExistsError:
        raise ArtifactAlreadyExists(
            f"{target} already exists; kept records are never overwritten, and nothing was"
            " written (D07 §19.3・§22.1, R4)"
        ) from None
    current.unlink()


def keep_previous_records(directory: Path) -> None:
    """同じ版にある結末記録とレポートを同じ連番で退避する（D07 §19.3・§22.1）。

    記録票の保存が成功した直後、run を始める前に呼ぶ。こうしておくと、今回の実行が途中で
    止まったときに「結末記録もレポートも無い＝途中で止まった」がそのまま成り立つ。
    """
    directory = Path(directory)
    outcome = directory / EXPERIMENT_OUTCOME_FILE
    report = directory / REPORT_FILE
    present = [path for path in (outcome, report) if _present(path)]
    if not present:
        return
    for path in present:
        if path.is_symlink() or not path.is_file():
            raise ArtifactAlreadyExists(
                f"{path} is not a plain file; it was left as is and nothing ran (D07 §19.3)"
            )
    number = next_kept_number(directory)
    if _present(outcome):
        _keep_file(outcome, directory / f"experiment_outcome.{number}.json")
    if _present(report):
        _keep_file(report, directory / f"report.{number}.md")


def keep_previous_report(directory: Path) -> None:
    """`report.md` だけを次の連番の `report.<n>.md` へ退避する（D07 §22.1）。

    `experiment report` が内容の違うレポートを書き直すときに使う。
    """
    directory = Path(directory)
    _keep_file(directory / REPORT_FILE, directory / f"report.{next_kept_number(directory)}.md")


def ensure_experiment_directory(artifacts_root: Path, directory: Path) -> Path:
    """実験の版のディレクトリがリンクを経由しない実ディレクトリであることを確かめる（R4）。"""
    return _ensure_plain_directory(Path(artifacts_root) / "runs", Path(directory))


def require_plain_experiment_path(artifacts_root: Path, directory: Path) -> bool:
    """`runs/` より下の要素にリンクや別の種類が無いかを、**何も作らずに**確かめる（R4）。

    要素がリンク（リンク切れを含む）か、ディレクトリでないものなら `ArtifactAlreadyExists`。
    途中が無ければ `False`（版のディレクトリが無い）、すべて実ディレクトリなら `True`。
    成果物の根 `runs/` そのものはリンクでもよい（D06 §9.1）。
    """
    base = Path(artifacts_root) / "runs"
    current = base
    for part in Path(directory).relative_to(base).parts:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ArtifactAlreadyExists(
                f"{current} is a symbolic link or not a directory; experiment records are never"
                " written through links, and nothing was written (D07 §19.1・§22.2, R4)"
            )
        if not current.exists():
            return False
    return True


def experiment_directory(root: Path, experiment_name: str, experiment_version: int) -> Path:
    """`runs/experiments/<experiment_name>/v<experiment_version>/`（D07 §19.1、D01 §10.3）。"""
    require_experiment_name(experiment_name)
    return Path(root) / "runs" / "experiments" / experiment_name / f"v{experiment_version}"


def _ensure_plain_directory(base: Path, directory: Path) -> Path:
    """`base`（成果物の根 `runs/`）の下に、リンクでない実ディレクトリを（無ければ）作る。

    記録票の置き場は同じ版の再実行で使い回す（D07 §19.3）ので、`create_artifact_directory`
    と違って**あっても失敗しない**。ただし根の下の要素（`experiments`・名前・版）がリンクや
    別の種類なら、リンク先に書かずに `ArtifactAlreadyExists` で失敗する（R4 と同じ境界。
    根そのものはリンクでもよい。D06 §9.1）。
    """
    _make_root(base)
    current = base
    for part in directory.relative_to(base).parts:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise ArtifactAlreadyExists(
                f"{current} is a symbolic link or not a directory; experiment records are never"
                " written through links, and nothing was written. Replace it with a plain"
                " directory first (D07 §19.3, R4)"
            )
        current.mkdir(exist_ok=True)
    return directory


def write_new_file(path: Path, text: str) -> None:
    """ファイルを**新しく**、原子的に書く（一時ファイル＋改名。D07 §19.4 の「保存を試みる」行）。

    既にあれば何も書かずに `FileExistsError`。途中で落ちても書きかけのファイルが本来の名前で
    残らない。改名には既存を上書きしない `os.link` を使う（`os.replace` は上書きする）。
    """
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _policy_check_payload(item: PolicyCheckResult) -> dict[str, str]:
    return {
        "check": item.check.value,
        "stage": item.stage.value,
        "outcome": item.outcome.value,
        "expected": item.expected,
        "observed": item.observed,
    }


def _policy_check_of(payload: Mapping[str, Any]) -> PolicyCheckResult:
    return PolicyCheckResult(
        check=PolicyCheck(payload["check"]),
        stage=PolicyCheckStage(payload["stage"]),
        outcome=CheckOutcome(payload["outcome"]),
        expected=payload["expected"],
        observed=payload["observed"],
    )


_MEASURES = ("component_kinds", "instances", "parameters", "decision_outputs")


def experiment_manifest_payload(manifest: ExperimentManifest) -> dict[str, Any]:
    """記録票を JSON へ落とす（D07 §19.2 の表の項目すべて。ADR-0027）。

    **実行時刻を入れない**（D07 §19.2）。本文（`resolved_files` の `text`）はそのまま入れる。

    **探索の実験の記録票**（D09 §10.2）は、`search_plan` / `split` を解決済みの形のまま正規化形
    （D02 §9.3）の JSON の値で持ち、`evaluation_standard`・`final_holdout`・`trials` を足す。単数の
    `compiled_ref` / `expected_config_digest` は `null`。単一実行の実験の記録票の形は変えない。
    """
    compiled_ref = manifest.compiled_ref
    expected_config_digest = manifest.expected_config_digest
    if not manifest.is_search and (compiled_ref is None or expected_config_digest is None):
        raise KernelValueError(  # pragma: no cover - 記録票の構築時に検査済み
            "a single-run manifest carries compiled_ref and its config digest"
        )
    policy = manifest.research_policy_ref
    strategy = manifest.strategy_ref
    payload = {
        "schema_version": manifest.schema_version,
        "experiment_id": manifest.experiment_id.hex,
        "experiment_name": manifest.experiment_name,
        "experiment_version": manifest.experiment_version,
        "hypothesis": manifest.hypothesis,
        "research_policy_ref": {
            "id": policy.policy_id,
            "version": policy.version,
            "digest": policy.digest.hex,
        },
        "metric_set_version": manifest.metric_set_version,
        "search_plan": _canonical_json(manifest.search_plan)
        if manifest.is_search
        else manifest.search_plan,
        "split": _canonical_json(manifest.split) if manifest.is_search else manifest.split,
        "resolved_files": [
            {"role": item.role, "sha256": item.sha256, "text": item.text}
            for item in sorted(manifest.resolved_files, key=lambda item: item.role)
        ],
        "strategy_ref": {
            "id": strategy.strategy_id,
            "version": strategy.version,
            "digest": strategy.digest.hex,
        },
        "compiled_ref": None if compiled_ref is None else compiled_ref.digest.hex,
        "expected_config_digest": None
        if expected_config_digest is None
        else expected_config_digest.digest.hex,
        "snapshot_id": manifest.snapshot_id.hex,
        "allowed_partitions": [
            {"partition": partition, "access_class": access.value}
            for partition, access in manifest.allowed_partitions.items()
        ],
        "complexity": {name: getattr(manifest.complexity, name) for name in _MEASURES},
        "complexity_limits": {
            name: getattr(manifest.complexity_limits, name) for name in _MEASURES
        },
        "pre_run_checks": [_policy_check_payload(item) for item in manifest.pre_run_checks],
        "code_digest": manifest.code_digest.digest.hex,
        "lock_digest": manifest.lock_digest.digest.hex,
        "env_digest": manifest.env_digest.digest.hex,
        "git_commit": manifest.git_commit,
        "git_dirty": manifest.git_dirty,
    }
    if manifest.is_search:
        payload.update(_search_manifest_items(manifest))
    return payload


def experiment_manifest_from_payload(payload: Mapping[str, Any]) -> ExperimentManifest:
    """保存した記録票を読み戻す。項目の欠け・型の違いは例外（`KeyError` など）になる。

    `search_plan` が JSON のオブジェクトなら探索の実験の記録票（D09 §10.2）として読む。
    """
    policy = payload["research_policy_ref"]
    strategy = payload["strategy_ref"]
    if isinstance(payload["search_plan"], Mapping):
        search = _search_manifest_values(payload)
    else:
        search = {
            "search_plan": payload["search_plan"],
            "split": payload["split"],
            "compiled_ref": CompiledStrategyRef(
                _digest_of(payload["compiled_ref"], "compiled_ref")
            ),
            "expected_config_digest": ConfigDigest(
                _digest_of(payload["expected_config_digest"], "expected_config_digest")
            ),
        }
    return ExperimentManifest(
        experiment_id=ExperimentId(_digest_of(payload["experiment_id"], "experiment_id")),
        experiment_name=payload["experiment_name"],
        experiment_version=payload["experiment_version"],
        schema_version=payload["schema_version"],
        hypothesis=payload["hypothesis"],
        research_policy_ref=PolicyRef(
            policy_kind="research",
            policy_id=policy["id"],
            version=policy["version"],
            digest=_digest_of(policy["digest"], "research_policy_ref.digest"),
        ),
        metric_set_version=payload["metric_set_version"],
        resolved_files=tuple(
            ResolvedFile(role=item["role"], text=item["text"], sha256=item["sha256"])
            for item in payload["resolved_files"]
        ),
        strategy_ref=StrategyRef(
            strategy_id=strategy["id"],
            version=strategy["version"],
            digest=_digest_of(strategy["digest"], "strategy_ref.digest"),
        ),
        snapshot_id=SnapshotId(_digest_of(payload["snapshot_id"], "snapshot_id")),
        allowed_partitions={
            item["partition"]: AccessClass(item["access_class"])
            for item in payload["allowed_partitions"]
        },
        complexity=ComplexityMeasures(**{name: payload["complexity"][name] for name in _MEASURES}),
        complexity_limits=ComplexityLimits(
            **{name: payload["complexity_limits"][name] for name in _MEASURES}
        ),
        pre_run_checks=tuple(_policy_check_of(item) for item in payload["pre_run_checks"]),
        code_digest=CodeDigest(_digest_of(payload["code_digest"], "code_digest")),
        lock_digest=LockDigest(_digest_of(payload["lock_digest"], "lock_digest")),
        env_digest=EnvDigest(_digest_of(payload["env_digest"], "env_digest")),
        git_commit=payload["git_commit"],
        git_dirty=payload["git_dirty"],
        **search,
    )


def _optional_hex(value: ContentDigest | None) -> str | None:
    return None if value is None else value.hex


def experiment_outcome_payload(outcome: ExperimentOutcome) -> dict[str, Any]:
    """結末記録を JSON へ落とす（D07 §19.3 の表の項目すべて）。

    探索の実験の結末記録（D09 §10.6）は `expected_run_id` が `null` で、`search` に
    `SearchOutcome` の正規化形（D02 §9.3）の JSON の値（`REJECTED_BY_POLICY` なら `null`）を持つ。
    単一実行の実験の結末記録の形は変えない（`search` のキーを持たない）。
    """
    payload: dict[str, Any] = {
        "experiment_id": outcome.experiment_id.hex,
        "status": outcome.status.value,
        "expected_run_id": _optional_hex(
            None if outcome.expected_run_id is None else outcome.expected_run_id.digest
        ),
        "code_digest": outcome.code_digest.digest.hex,
        "lock_digest": outcome.lock_digest.digest.hex,
        "env_digest": outcome.env_digest.digest.hex,
        "git_commit": outcome.git_commit,
        "git_dirty": outcome.git_dirty,
        "run_id": None if outcome.run_id is None else outcome.run_id.hex,
        "run_status": None if outcome.run_status is None else outcome.run_status.value,
        "run_reused": outcome.run_reused,
        "run_evaluation_id": _optional_hex(outcome.run_evaluation_id),
        "evaluation_status": None
        if outcome.evaluation_status is None
        else outcome.evaluation_status.value,
        "result_digest": _optional_hex(outcome.result_digest),
        "outcome_checks": [_policy_check_payload(item) for item in outcome.outcome_checks],
        "failed_checks": [check.value for check in outcome.failed_checks],
    }
    if outcome.is_search:
        payload["search"] = _canonical_json(outcome.search)
    return payload


def _optional_digest(value: object, label: str) -> ContentDigest | None:
    return None if value is None else _digest_of(value, label)


def experiment_outcome_from_payload(payload: Mapping[str, Any]) -> ExperimentOutcome:
    """保存した結末記録を読み戻す。"""
    run_id = _optional_digest(payload["run_id"], "run_id")
    run_status = payload["run_status"]
    evaluation_status = payload["evaluation_status"]
    expected = _optional_digest(payload["expected_run_id"], "expected_run_id")
    search = payload.get("search")
    if expected is None and "search" not in payload:
        raise KernelValueError("a search outcome (expected_run_id null) carries the search item")
    return ExperimentOutcome(
        experiment_id=ExperimentId(_digest_of(payload["experiment_id"], "experiment_id")),
        status=ExperimentStatus(payload["status"]),
        expected_run_id=None if expected is None else RunId(expected),
        code_digest=CodeDigest(_digest_of(payload["code_digest"], "code_digest")),
        lock_digest=LockDigest(_digest_of(payload["lock_digest"], "lock_digest")),
        env_digest=EnvDigest(_digest_of(payload["env_digest"], "env_digest")),
        git_commit=payload["git_commit"],
        git_dirty=payload["git_dirty"],
        run_id=None if run_id is None else RunId(run_id),
        run_status=None if run_status is None else RunStatus(run_status),
        run_reused=payload["run_reused"],
        run_evaluation_id=_optional_digest(payload["run_evaluation_id"], "run_evaluation_id"),
        evaluation_status=None
        if evaluation_status is None
        else EvaluationStatus(evaluation_status),
        result_digest=_optional_digest(payload["result_digest"], "result_digest"),
        outcome_checks=tuple(_policy_check_of(item) for item in payload["outcome_checks"]),
        failed_checks=tuple(PolicyCheck(name) for name in payload["failed_checks"]),
        search=None if search is None else _exact(search, _search_outcome_of, "search"),
    )


#: 記録票・結末記録を読めなかったときの例外（形の違い・項目の欠け・JSON の壊れ）。
_READ_ERRORS = (ValueError, KeyError, TypeError, AttributeError, ArithmeticError)


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise KernelValueError(f"{path} is not a readable {label} file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KernelValueError(f"{path} cannot be read as JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise KernelValueError(f"{path} must hold a JSON object")
    return payload


def read_experiment_manifest(directory: Path) -> ExperimentManifest:
    """実験の版のディレクトリの記録票を読む。無い・読めなければ `KernelValueError`。"""
    path = Path(directory) / EXPERIMENT_MANIFEST_FILE
    payload = _read_json_object(path, "experiment manifest")
    try:
        return experiment_manifest_from_payload(payload)
    except _READ_ERRORS as exc:
        raise KernelValueError(
            f"{path} is not a valid experiment manifest: {type(exc).__name__}: {exc}"
        ) from exc


def read_experiment_outcome(directory: Path) -> ExperimentOutcome | None:
    """実験の版のディレクトリの結末記録を読む。無ければ `None`、読めなければ `KernelValueError`。"""
    path = Path(directory) / EXPERIMENT_OUTCOME_FILE
    if not (path.exists() or path.is_symlink()):
        return None
    payload = _read_json_object(path, "experiment outcome")
    try:
        return experiment_outcome_from_payload(payload)
    except _READ_ERRORS as exc:
        raise KernelValueError(
            f"{path} is not a valid experiment outcome: {type(exc).__name__}: {exc}"
        ) from exc


@dataclass(frozen=True, slots=True)
class FileSystemExperimentStore:
    """1つの実験の版の記録票と結末記録の保存（D01 §4、D07 §19.3）。

    `ExperimentStore`（`evaluation.application.ports`）を構造的に満たす。置き場は
    `runs/experiments/<experiment_name>/v<experiment_version>/`（D07 §19.1）。
    """

    root: Path
    experiment_name: str
    experiment_version: int
    #: 記録票の中身から識別子を再計算する関数（D07 §19.2 の「識別の入力」。合成が渡す）。
    #: 計算できなければ `ValueError` 系を送出する（その記録票は読めないものと同じに扱う）。
    identity_of: Callable[[ExperimentManifest], ExperimentId]
    #: 試行台帳を置くリポジトリの根（`<根>/research/trial_ledger.jsonl`。D09 §10.10）。探索の
    #: 実験だけが使う（単一実行の実験は台帳に書かない）。
    repo_root: Path | None = None

    @property
    def directory(self) -> Path:
        """この実験の版のディレクトリ。"""
        return experiment_directory(self.root, self.experiment_name, self.experiment_version)

    def _require_own(self, manifest: ExperimentManifest) -> None:
        if (manifest.experiment_name, manifest.experiment_version) != (
            self.experiment_name,
            self.experiment_version,
        ):
            raise KernelValueError(
                f"this store keeps {self.experiment_name} v{self.experiment_version}, not"
                f" {manifest.experiment_name} v{manifest.experiment_version}"
            )

    def save_manifest(self, manifest: ExperimentManifest) -> ManifestSaveResult:
        """記録票を保存する（D07 §19.3）。

        - 無ければ書く（一時ファイル＋改名）→ `CREATED`。
        - あり、識別子が同じなら何もしない → `ALREADY_IDENTICAL`（同じ版の再実行は正当）。
        - あり、識別子が違う、**または読めない**なら書かない → `CONFLICT`（検査 P3 の不合格。
          既存の記録票は上書きしない。読めない記録票を上書きすると、事前固定の証拠を消して
          しまう）。

        保存が成功したら、run を始める前に、同じ版に既にある結末記録を退避する（同節）。
        """
        self._require_own(manifest)
        directory = _ensure_plain_directory(Path(self.root) / "runs", self.directory)
        path = directory / EXPERIMENT_MANIFEST_FILE
        result = self._compare_existing(path, manifest)
        if result is None:
            try:
                write_new_file(path, _json_text(experiment_manifest_payload(manifest)))
                result = ManifestSaveResult.CREATED
            except FileExistsError:
                # 確かめた後に別の実行が書いた。書いたものと比べ直す。
                result = self._compare_existing(path, manifest) or ManifestSaveResult.CONFLICT
        if result is not ManifestSaveResult.CONFLICT:
            if manifest.is_search:
                # 探索の実験は「退避中」の印の回復と、探索の記録を含む退避（D09 §11.3 の Q13）。
                keep_previous_search_records(directory)
            else:
                self._keep_previous_outcome(directory)
        return result

    def _compare_existing(
        self, path: Path, manifest: ExperimentManifest
    ) -> ManifestSaveResult | None:
        """既存の記録票と比べる。**記録された識別子を信じず、中身から再計算して比べる**。

        記録票の項目（仮説など）だけを書き換えて識別子を残した記録票を「同じ内容」と読むと、
        事前固定の検査 P3 が改変を見逃す（D07 §19.3 の「内容のダイジェストが同じ」）。本文の
        SHA-256 も照合する（識別子には本文ではなく SHA-256 が入るため）。識別子の再計算には
        実験設定の値（YAML の読込）が要るので、計算は合成が `identity_of` として渡す。
        """
        if not (path.exists() or path.is_symlink()):
            return None
        try:
            existing = read_experiment_manifest(path.parent)
        except KernelValueError:
            return ManifestSaveResult.CONFLICT
        if not all(item.intact for item in existing.resolved_files):
            return ManifestSaveResult.CONFLICT
        try:
            recomputed = self.identity_of(existing)
        except ValueError:
            return ManifestSaveResult.CONFLICT
        if recomputed != existing.experiment_id:
            return ManifestSaveResult.CONFLICT
        if existing.experiment_id == manifest.experiment_id:
            return ManifestSaveResult.ALREADY_IDENTICAL
        return ManifestSaveResult.CONFLICT

    @staticmethod
    def _keep_previous_outcome(directory: Path) -> None:
        """同じ版に既にある結末記録とレポートを退避する（D07 §19.3・§22.1）。

        結末記録は `experiment_outcome.<n>.json`、レポートは `report.<n>.md` へ、**同じ n**
        で退避する（n は `next_kept_number`）。旧い記録は消さず、既存の退避先は上書きしない。
        """
        keep_previous_records(directory)

    def read_manifest(self, path: str) -> ExperimentManifest:
        """実験の版のディレクトリ `path` の記録票を読む（読めなければ `KernelValueError`）。"""
        return read_experiment_manifest(Path(path))

    def write_outcome(self, outcome: ExperimentOutcome) -> None:
        """結末記録を書く（D07 §19.3）。前の結末記録は保存時に退避済みである。"""
        directory = self.directory
        manifest = read_experiment_manifest(directory)
        if manifest.experiment_id != outcome.experiment_id:
            raise KernelValueError(
                f"the outcome belongs to the experiment {outcome.experiment_id}, but"
                f" {directory} records {manifest.experiment_id} (D07 §19.3)"
            )
        try:
            write_new_file(
                directory / EXPERIMENT_OUTCOME_FILE,
                _json_text(experiment_outcome_payload(outcome)),
            )
        except FileExistsError:
            raise ArtifactAlreadyExists(
                f"{directory / EXPERIMENT_OUTCOME_FILE} already exists; outcomes are never"
                " overwritten (D07 §19.3, R4)"
            ) from None

    # --- 探索の記録（D09 §10.3・§11.1・§11.2）------------------------------------------

    def _search_path(self, *parts: str) -> Path:
        """`search/` の下の書き先。途中の要素はリンクでない実ディレクトリとして用意する（R4）。"""
        path = search_directory(self.directory).joinpath(*parts)
        _ensure_plain_directory(Path(self.root) / "runs", path.parent)
        return path

    def write_trial_start(self, record: TrialStartRecord) -> None:
        """開始記録を書く（D09 §10.3。既にあれば何も書かずに失敗する。R4）。"""
        path = self._search_path(UNITS_DIRECTORY, unit_name(record.unit) + _START_SUFFIX)
        _write_new_json(path, _canonical_json(record), "a start record")

    def write_trial_run(self, record: TrialRunRecord) -> None:
        """試行記録を書く（D09 §10.3。既にあれば何も書かずに失敗する。R4）。"""
        path = self._search_path(UNITS_DIRECTORY, unit_name(record.unit) + _RUN_SUFFIX)
        _write_new_json(path, _canonical_json(record), "a trial record")

    def write_selection(self, selection: FoldSelection) -> None:
        """選定記録を書く（D09 §7.5。同じ実行の中で書き換えない。既にあれば失敗する。R4）。"""
        path = self._search_path(f"selection_f{selection.fold_index}.json")
        _write_new_json(path, _canonical_json(selection), "a selection record")

    def write_ledger_binding(self, binding: TrialLedgerBinding) -> None:
        """束縛の記録を書く（D09 §10.12.3 の2。W2: 一時名から排他的に作成する）。"""
        path = self._search_path(LEDGER_BINDING_FILE)
        _write_new_json(path, _canonical_json(binding), "the ledger binding")

    def write_aggregate_tables(
        self,
        manifest: ExperimentManifest,
        selections: tuple[FoldSelection, ...],
        records: tuple[TrialRunRecord, ...],
        metrics: Mapping[TrialUnitKey, tuple[MetricRecord, ...]],
    ) -> None:
        """集約表2つを書く（D09 §11.2）。0 行でも列と型を残し、指標の値は計算し直さない。

        指標の行は、選定・判定に使った値（`metrics`）を写す。評価の成果物をここで読まない
        （読み出しは `read_evaluation_metrics` の1経路。D07 v2.11 §3、D09 §17.7.4 の4）。
        """
        self._require_own(manifest)
        if set(metrics) != {record.unit for record in records} or len(metrics) != len(records):
            raise KernelValueError(
                "the metrics given for trial_metrics must cover exactly the units of the trial"
                " records (D09 §11.2)"
            )
        rows = trial_units_rows(manifest, selections, records)
        units = pl.DataFrame(
            {name: [row[name] for row in rows] for name in _TRIAL_UNITS_SCHEMA},
            schema=dict(_TRIAL_UNITS_SCHEMA),
        )
        schema = _metrics_schema()
        metric_rows: list[dict[str, object]] = []
        for record in sorted(records, key=lambda item: item.unit.order):
            unit = record.unit
            for metric in metrics[unit]:
                if not isinstance(metric, MetricRecord):
                    raise KernelValueError("trial_metrics rows must be MetricRecord values")
                metric_rows.append(
                    {
                        "fold_index": unit.fold_index,
                        "phase": unit.phase.value,
                        "trial_index": unit.trial_index,
                        **flatten_row(metric),
                    }
                )
        table = pl.DataFrame(
            {name: [row[name] for row in metric_rows] for name in schema},
            schema=schema,
        )
        order = [_METRIC_ORDER[value] for value in table.get_column("metric_id").to_list()]
        table = (
            table.with_columns(pl.Series("_order", order, dtype=pl.Int64()))
            .sort(["fold_index", "phase", "trial_index", "_order"])
            .drop("_order")
        )
        if table.select(["fold_index", "phase", "trial_index", "metric_id"]).is_duplicated().any():
            raise KernelValueError("trial_metrics has a duplicated primary key (D09 §11.2)")
        _write_new_parquet(self._search_path(TRIAL_UNITS_TABLE), units)
        _write_new_parquet(self._search_path(TRIAL_METRICS_TABLE), table)

    # --- 試行台帳（D09 §10.10・§10.12）-------------------------------------------------

    def _ledger_root(self) -> Path:
        if self.repo_root is None:
            raise KernelValueError(
                "the trial ledger lives under the repository root; this store was built without"
                " one (D09 §10.10)"
            )
        return Path(self.repo_root)

    def read_trial_ledger(self) -> TrialLedgerContents | TrialLedgerReadFailure:
        """試行台帳を読む（D09 §10.12.1・§10.12.2。ロックは取らない）。"""
        return read_trial_ledger_file(self._ledger_root() / TRIAL_LEDGER_PATH)

    def append_trial_ledger(
        self, line: TrialLedgerLine
    ) -> TrialLedgerLine | TrialLedgerAppendRefused:
        """1行を台帳へ追記する（D09 §10.12.1 の操作の形）。"""
        return append_trial_ledger_file(self._ledger_root(), line)

    def read_ledger_bindings(
        self, out_base: str
    ) -> tuple[tuple[str, TrialLedgerBinding | TrialLedgerReadFailure], ...]:
        """各実験の版の束縛の記録がある最も新しい世代の束縛の記録（D09 §10.12.2 の L11）。"""
        return read_ledger_binding_records(Path(out_base))


# --- 探索の実験の記録（D09 §10.2・§10.3・§10.6・§11.1〜§11.3。段階5 実装 PR 4）-----------------

#: 探索の記録のディレクトリ（D09 §11.1）と、その中の名前。
SEARCH_DIRECTORY = "search"
UNITS_DIRECTORY = "units"
LEDGER_BINDING_FILE = "ledger_execution.json"
TRIAL_UNITS_TABLE = "trial_units.parquet"
TRIAL_METRICS_TABLE = "trial_metrics.parquet"
#: 「退避中」の印（D09 §11.3 の Q13。退避の途中にだけある）。
RETREAT_MARKER_FILE = "retreat_in_progress.json"

#: 単位の記録のファイル名の鍵の部分（D09 §11.1。先頭のゼロを許さない。照合は `fullmatch`）。
UNIT_NAME = re.compile(r"f(0|[1-9][0-9]*)_(TRAIN|VALIDATION)_t(0|[1-9][0-9]*)")
_SELECTION_NAME = re.compile(r"selection_f(0|[1-9][0-9]*)\.json")
_KEPT_SEARCH = re.compile(r"search\.([1-9][0-9]*)")
_START_SUFFIX = ".start.json"
_RUN_SUFFIX = ".json"


def unit_name(unit: TrialUnitKey) -> str:
    """単位の鍵からファイル名の鍵の部分を作る（`f<k>_<局面>_t<i>`。D09 §11.1）。"""
    return f"f{unit.fold_index}_{unit.phase.value}_t{unit.trial_index}"


def unit_of_name(name: str) -> TrialUnitKey:
    """ファイル名の鍵の部分から単位の鍵を読む。正規表現に完全一致しなければ構造エラー。"""
    match = UNIT_NAME.fullmatch(name)
    if match is None:
        raise KernelValueError(
            f"{name!r} is not a unit record name f<k>_<TRAIN|VALIDATION>_t<i> (D09 §11.1)"
        )
    return TrialUnitKey(
        fold_index=int(match.group(1)),
        phase=TrialPhase(match.group(2)),
        trial_index=int(match.group(3)),
    )


def _canonical_json(value: object) -> Any:
    """正規化エンコード（D02 §9.3）した JSON の値（保存はこの形で行い、読み戻しで照合する）。"""
    return json.loads(encode(value))


def _keys(payload: object, names: Sequence[str], label: str) -> Mapping[str, Any]:
    """キーの集合がちょうど `names` の JSON オブジェクトであることを確かめる。"""
    if not isinstance(payload, Mapping):
        raise KernelValueError(f"{label} must be a JSON object")
    if set(payload) != set(names):
        raise KernelValueError(
            f"{label} must have exactly the keys {sorted(names)}, got {sorted(payload)}"
        )
    return payload


def _int_of(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    return value


def _str_of(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise KernelValueError(f"{label} must be a str, got {value!r}")
    return value


def _decimal_of(value: object, label: str) -> Decimal:
    return decimal_from_str(_str_of(value, label))


def _list_of(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise KernelValueError(f"{label} must be a JSON array")
    return value


def _cdigest_of(payload: object, label: str) -> ContentDigest:
    item = _keys(payload, ("algorithm", "hex"), label)
    return ContentDigest(
        algorithm=_str_of(item["algorithm"], label), hex=_str_of(item["hex"], label)
    )


def _wrapped_digest(payload: object, label: str) -> ContentDigest:
    """`{"digest": {...}}`（`CompiledStrategyRef` / `ConfigDigest` / `CodeDigest` の正規化形）。"""
    return _cdigest_of(_keys(payload, ("digest",), label)["digest"], label)


def _hex_id(value: object, label: str) -> ContentDigest:
    """ID 型の正規化形（16進 64 文字。D02 §9.3: ID 型は `__str__`）。"""
    return ContentDigest.sha256(_str_of(value, label))


def _interval_c(payload: object, label: str) -> Interval:
    item = _keys(payload, ("start", "end"), label)
    return Interval(
        start=UtcTime.parse(_str_of(item["start"], label)),
        end=UtcTime.parse(_str_of(item["end"], label)),
    )


def _parameter_value_of(payload: object, label: str) -> ParameterValue:
    item = _keys(payload, ("kind", "value"), label)
    kind = item["kind"]
    value = item["value"]
    if kind == "INT":
        return IntValue(_int_of(value, label))
    if kind == "BOOL":
        if not isinstance(value, bool):
            raise KernelValueError(f"{label} must hold a bool")
        return BoolValue(value)
    if kind == "STR":
        return StrValue(_str_of(value, label))
    if kind == "FLOAT":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise KernelValueError(f"{label} must hold a number")
        return FloatValue(float(value))
    raise KernelValueError(f"{label} has an unknown parameter value kind {kind!r}")


def _unit_key_of(payload: object, label: str) -> TrialUnitKey:
    item = _keys(payload, ("fold_index", "phase", "trial_index"), label)
    return TrialUnitKey(
        fold_index=_int_of(item["fold_index"], label),
        phase=TrialPhase(item["phase"]),
        trial_index=_int_of(item["trial_index"], label),
    )


def _search_plan_of(payload: object) -> SearchPlan:
    item = _keys(payload, ("kind", "axes", "max_trials"), "search_plan")
    return SearchPlan(
        kind=SearchPlanKind(item["kind"]),
        axes=tuple(
            ParameterAxis(
                instance_id=_str_of(axis["instance_id"], "axis.instance_id"),
                parameter=_str_of(axis["parameter"], "axis.parameter"),
                values=tuple(
                    _parameter_value_of(value, "axis.values")
                    for value in _list_of(axis["values"], "axis.values")
                ),
            )
            for axis in (
                _keys(entry, ("instance_id", "parameter", "values"), "search_plan.axes")
                for entry in _list_of(item["axes"], "search_plan.axes")
            )
        ),
        max_trials=_int_of(item["max_trials"], "search_plan.max_trials"),
    )


def _fold_of(payload: object) -> Fold:
    item = _keys(payload, ("fold_index", "train", "validation"), "fold")
    return Fold(
        fold_index=_int_of(item["fold_index"], "fold.fold_index"),
        train=_interval_c(item["train"], "fold.train"),
        validation=_interval_c(item["validation"], "fold.validation"),
    )


def _split_spec_of(payload: object) -> SplitSpec:
    item = _keys(payload, ("kind", "folds", "purge_seconds"), "split")
    return SplitSpec(
        kind=SplitKind(item["kind"]),
        folds=tuple(_fold_of(entry) for entry in _list_of(item["folds"], "split.folds")),
        purge_seconds=_int_of(item["purge_seconds"], "split.purge_seconds"),
    )


def _metric_condition_of(payload: object) -> MetricCondition:
    item = _keys(payload, ("metric", "comparator", "threshold"), "condition")
    return MetricCondition(
        metric=MetricId(item["metric"]),
        comparator=Comparator(item["comparator"]),
        threshold=_decimal_of(item["threshold"], "condition.threshold"),
    )


def _evaluation_standard_of(payload: object) -> EvaluationStandard:
    item = _keys(
        payload,
        ("purpose", "split", "selection", "validation", "sufficiency"),
        "evaluation_standard",
    )
    split = _keys(
        item["split"],
        (
            "range",
            "train_seconds",
            "validation_seconds",
            "window",
            "purge_seconds",
            "min_folds",
        ),
        "evaluation_standard.split",
    )
    selection = _keys(
        item["selection"], ("metric", "direction", "eligibility"), "evaluation_standard.selection"
    )
    validation = _keys(
        item["validation"], ("fold_floors", "aggregate"), "evaluation_standard.validation"
    )
    sufficiency = _keys(item["sufficiency"], ("classes",), "evaluation_standard.sufficiency")
    return EvaluationStandard(
        purpose=StandardPurpose(item["purpose"]),
        split=SplitStandard(
            range=_interval_c(split["range"], "split.range"),
            train_seconds=_int_of(split["train_seconds"], "split.train_seconds"),
            validation_seconds=_int_of(split["validation_seconds"], "split.validation_seconds"),
            window=SplitWindow(split["window"]),
            purge_seconds=_int_of(split["purge_seconds"], "split.purge_seconds"),
            min_folds=_int_of(split["min_folds"], "split.min_folds"),
        ),
        selection=SelectionRule(
            metric=MetricId(selection["metric"]),
            direction=SelectionDirection(selection["direction"]),
            eligibility=tuple(
                _metric_condition_of(entry)
                for entry in _list_of(selection["eligibility"], "eligibility")
            ),
        ),
        validation=ValidationRule(
            fold_floors=tuple(
                _metric_condition_of(entry)
                for entry in _list_of(validation["fold_floors"], "fold_floors")
            ),
            aggregate=tuple(
                AggregateCondition(
                    metric=MetricId(entry["metric"]),
                    statistic=FoldStatistic(entry["statistic"]),
                    comparator=Comparator(entry["comparator"]),
                    threshold=_decimal_of(entry["threshold"], "aggregate.threshold"),
                )
                for entry in (
                    _keys(raw, ("metric", "statistic", "comparator", "threshold"), "aggregate")
                    for raw in _list_of(validation["aggregate"], "aggregate")
                )
            ),
        ),
        sufficiency=SufficiencyRule(
            classes=tuple(
                FrequencyClass(
                    name=_str_of(entry["name"], "class.name"),
                    min_train_trades_per_365d=_decimal_of(
                        entry["min_train_trades_per_365d"], "class.min_train_trades_per_365d"
                    ),
                    min_validation_trades_per_fold=_int_of(
                        entry["min_validation_trades_per_fold"],
                        "class.min_validation_trades_per_fold",
                    ),
                    min_validation_trades_total=_int_of(
                        entry["min_validation_trades_total"], "class.min_validation_trades_total"
                    ),
                )
                for entry in (
                    _keys(
                        raw,
                        (
                            "name",
                            "min_train_trades_per_365d",
                            "min_validation_trades_per_fold",
                            "min_validation_trades_total",
                        ),
                        "sufficiency.classes",
                    )
                    for raw in _list_of(sufficiency["classes"], "sufficiency.classes")
                )
            )
        ),
    )


def _final_holdout_of(payload: object) -> FinalHoldoutSpec | None:
    if payload is None:
        return None
    item = _keys(payload, ("interval", "purpose"), "final_holdout")
    return FinalHoldoutSpec(
        interval=_interval_c(item["interval"], "final_holdout.interval"),
        purpose=_str_of(item["purpose"], "final_holdout.purpose"),
    )


def _trial_plan_of(payload: object) -> TrialPlan:
    item = _keys(
        payload,
        (
            "trial_index",
            "assignment",
            "compiled_ref",
            "compile_rejections",
            "expected_config_digests",
        ),
        "trial",
    )
    assignment = _keys(item["assignment"], ("values",), "trial.assignment")
    values: list[tuple[str, str, ParameterValue]] = []
    for entry in _list_of(assignment["values"], "trial.assignment.values"):
        triple = _list_of(entry, "trial.assignment.values")
        if len(triple) != 3:
            raise KernelValueError("an assignment value is (instance_id, parameter, value)")
        values.append(
            (
                _str_of(triple[0], "instance_id"),
                _str_of(triple[1], "parameter"),
                _parameter_value_of(triple[2], "assignment value"),
            )
        )
    digests: list[tuple[TrialUnitKey, ConfigDigest]] = []
    for entry in _list_of(item["expected_config_digests"], "trial.expected_config_digests"):
        pair = _list_of(entry, "trial.expected_config_digests")
        if len(pair) != 2:
            raise KernelValueError("an expected config digest is (unit, digest)")
        digests.append(
            (
                _unit_key_of(pair[0], "expected_config_digests.unit"),
                ConfigDigest(_wrapped_digest(pair[1], "expected_config_digests.digest")),
            )
        )
    compiled = item["compiled_ref"]
    return TrialPlan(
        trial_index=_int_of(item["trial_index"], "trial.trial_index"),
        assignment=ParameterAssignment(values=tuple(values)),
        compiled_ref=None
        if compiled is None
        else CompiledStrategyRef(_wrapped_digest(compiled, "trial.compiled_ref")),
        compile_rejections=tuple(
            _str_of(text, "compile_rejections")
            for text in _list_of(item["compile_rejections"], "trial.compile_rejections")
        ),
        expected_config_digests=tuple(digests),
    )


def _exact[ValueT](raw: object, decoder: Callable[[Any], ValueT], label: str) -> ValueT:
    """正規化形の値を型へ直し、型から作り直した正規化形が元と一致することを確かめる。

    表し方の違う値（例: 同じ十進数の別の書き方）を同じ値として通さない。保存した記録票から
    識別子を再計算したときに同じ値になることの保証でもある（D07 §19.2）。
    """
    typed = decoder(raw)
    if encode(typed) != encode(raw):
        raise KernelValueError(f"{label} is not in the canonical form it was saved in")
    return typed


def _fold_selection_of(payload: object) -> FoldSelection:
    item = _keys(
        payload,
        (
            "fold_index",
            "selected_trial_index",
            "selected_value",
            "selected_train_trade_count",
            "inputs",
        ),
        "selection",
    )
    inputs: list[tuple[int, ContentDigest | None, CandidateStatus]] = []
    for entry in _list_of(item["inputs"], "selection.inputs"):
        triple = _list_of(entry, "selection.inputs")
        if len(triple) != 3:
            raise KernelValueError("a selection input is (trial_index, digest, status)")
        inputs.append(
            (
                _int_of(triple[0], "selection.inputs.trial_index"),
                None if triple[1] is None else _cdigest_of(triple[1], "selection.inputs.digest"),
                CandidateStatus(triple[2]),
            )
        )
    value = item["selected_value"]
    trial = item["selected_trial_index"]
    count = item["selected_train_trade_count"]
    return FoldSelection(
        fold_index=_int_of(item["fold_index"], "selection.fold_index"),
        selected_trial_index=None if trial is None else _int_of(trial, "selected_trial_index"),
        selected_value=None if value is None else _decimal_of(value, "selected_value"),
        selected_train_trade_count=None if count is None else _int_of(count, "trade count"),
        inputs=tuple(inputs),
    )


def _condition_result_of(payload: object) -> ConditionResult:
    item = _keys(
        payload,
        (
            "scope",
            "fold_index",
            "metric",
            "statistic",
            "comparator",
            "threshold",
            "observed",
            "outcome",
            "unavailable_reason",
        ),
        "condition_result",
    )
    fold = item["fold_index"]
    statistic = item["statistic"]
    observed = item["observed"]
    reason = item["unavailable_reason"]
    return ConditionResult(
        scope=ConditionScope(item["scope"]),
        fold_index=None if fold is None else _int_of(fold, "condition_result.fold_index"),
        metric=MetricId(item["metric"]),
        statistic=None if statistic is None else FoldStatistic(statistic),
        comparator=Comparator(item["comparator"]),
        threshold=_decimal_of(item["threshold"], "condition_result.threshold"),
        observed=None if observed is None else _decimal_of(observed, "condition_result.observed"),
        outcome=ConditionOutcome(item["outcome"]),
        unavailable_reason=None if reason is None else MetricUnavailableReason(reason),
    )


def _shortfall_of(payload: object) -> SufficiencyShortfall:
    item = _keys(
        payload,
        ("kind", "fold_index", "trial_index", "metric", "reason", "required", "observed"),
        "shortfall",
    )

    def optional_int(key: str) -> int | None:
        value = item[key]
        return None if value is None else _int_of(value, f"shortfall.{key}")

    return SufficiencyShortfall(
        kind=SufficiencyShortfallKind(item["kind"]),
        fold_index=optional_int("fold_index"),
        trial_index=optional_int("trial_index"),
        metric=None if item["metric"] is None else MetricId(item["metric"]),
        reason=None if item["reason"] is None else MetricUnavailableReason(item["reason"]),
        required=optional_int("required"),
        observed=optional_int("observed"),
    )


def _search_outcome_of(payload: object) -> SearchOutcome:
    item = _keys(
        payload,
        (
            "selections",
            "fold_verdicts",
            "verdict",
            "frequency",
            "condition_results",
            "shortfalls",
            "trial_counts",
            "ledger_execution",
            "purpose",
        ),
        "search",
    )
    verdicts: list[tuple[int, FoldVerdict]] = []
    for entry in _list_of(item["fold_verdicts"], "search.fold_verdicts"):
        pair = _list_of(entry, "search.fold_verdicts")
        if len(pair) != 2:
            raise KernelValueError("a fold verdict is (fold_index, verdict)")
        verdicts.append((_int_of(pair[0], "fold_verdicts.fold_index"), FoldVerdict(pair[1])))
    counts: list[tuple[TrialStatus, int]] = []
    for entry in _list_of(item["trial_counts"], "search.trial_counts"):
        pair = _list_of(entry, "search.trial_counts")
        if len(pair) != 2:
            raise KernelValueError("a trial count is (status, count)")
        counts.append((TrialStatus(pair[0]), _int_of(pair[1], "trial_counts.count")))
    frequency = item["frequency"]
    if frequency is not None:
        found = _keys(frequency, ("class_name", "train_trade_count", "train_seconds"), "frequency")
        assessment: FrequencyAssessment | None = FrequencyAssessment(
            class_name=_str_of(found["class_name"], "frequency.class_name"),
            train_trade_count=_int_of(found["train_trade_count"], "frequency.train_trade_count"),
            train_seconds=_int_of(found["train_seconds"], "frequency.train_seconds"),
        )
    else:
        assessment = None
    return SearchOutcome(
        selections=tuple(
            _fold_selection_of(entry) for entry in _list_of(item["selections"], "selections")
        ),
        fold_verdicts=tuple(verdicts),
        verdict=SearchVerdict(item["verdict"]),
        frequency=assessment,
        condition_results=tuple(
            _condition_result_of(entry)
            for entry in _list_of(item["condition_results"], "condition_results")
        ),
        shortfalls=tuple(
            _shortfall_of(entry) for entry in _list_of(item["shortfalls"], "shortfalls")
        ),
        trial_counts=tuple(counts),
        ledger_execution=_int_of(item["ledger_execution"], "search.ledger_execution"),
        purpose=StandardPurpose(item["purpose"]),
    )


def _policy_check_c(payload: object) -> PolicyCheckResult:
    item = _keys(payload, ("check", "stage", "outcome", "expected", "observed"), "check")
    return _policy_check_of(item)


def _start_record_of(payload: object) -> TrialStartRecord:
    item = _keys(payload, ("experiment_id", "unit", "expected_run_id"), "start record")
    return TrialStartRecord(
        experiment_id=ExperimentId(_hex_id(item["experiment_id"], "experiment_id")),
        unit=_unit_key_of(item["unit"], "start record.unit"),
        expected_run_id=RunId(_hex_id(item["expected_run_id"], "expected_run_id")),
    )


def _run_record_of(payload: object) -> TrialRunRecord:
    item = _keys(
        payload,
        (
            "experiment_id",
            "unit",
            "status",
            "expected_run_id",
            "run_id",
            "run_status",
            "run_reused",
            "run_evaluation_id",
            "evaluation_status",
            "result_digest",
            "outcome_checks",
        ),
        "trial record",
    )
    reused = item["run_reused"]
    if not isinstance(reused, bool):
        raise KernelValueError("trial record.run_reused must be a bool")
    return TrialRunRecord(
        experiment_id=ExperimentId(_hex_id(item["experiment_id"], "experiment_id")),
        unit=_unit_key_of(item["unit"], "trial record.unit"),
        status=TrialStatus(item["status"]),
        expected_run_id=RunId(_hex_id(item["expected_run_id"], "expected_run_id")),
        run_id=RunId(_hex_id(item["run_id"], "run_id")),
        run_status=RunStatus(item["run_status"]),
        run_reused=reused,
        run_evaluation_id=_cdigest_of(item["run_evaluation_id"], "run_evaluation_id"),
        evaluation_status=EvaluationStatus(item["evaluation_status"]),
        result_digest=_cdigest_of(item["result_digest"], "result_digest"),
        outcome_checks=tuple(
            _policy_check_c(entry) for entry in _list_of(item["outcome_checks"], "outcome_checks")
        ),
    )


def _binding_of(payload: object) -> TrialLedgerBinding:
    item = _keys(
        payload,
        ("schema_version", "experiment_id", "execution", "started_line_digest"),
        "ledger binding",
    )
    return TrialLedgerBinding(
        schema_version=_int_of(item["schema_version"], "ledger binding.schema_version"),
        experiment_id=ExperimentId(_hex_id(item["experiment_id"], "experiment_id")),
        execution=_int_of(item["execution"], "ledger binding.execution"),
        started_line_digest=_cdigest_of(item["started_line_digest"], "started_line_digest"),
    )


def _policy_ref_c(payload: object, label: str) -> PolicyRef:
    item = _keys(payload, ("policy_kind", "policy_id", "version", "digest"), label)
    return PolicyRef(
        policy_kind=_str_of(item["policy_kind"], label),
        policy_id=_str_of(item["policy_id"], label),
        version=_int_of(item["version"], label),
        digest=_cdigest_of(item["digest"], label),
    )


def _basis_of(payload: object) -> ComparisonBasis:
    item = _keys(
        payload,
        (
            "research_policy_ref",
            "metric_set_version",
            "snapshot_ref",
            "strategy_ref",
            "execution_series",
            "seed",
            "account",
            "risk_policy_ref",
            "execution_policy_ref",
            "cost_model_ref",
            "conversion_policy_ref",
            "delay_scenario_ref",
            "symbol_spec_ref",
            "calendar_ref",
            "timeframe_def_refs",
            "code_digest",
            "lock_digest",
            "env_digest",
        ),
        "basis",
    )
    timeframe_refs = tuple(
        TimeframeRef.parse(_str_of(text, "basis.timeframe_def_refs"))
        for text in _list_of(item["timeframe_def_refs"], "basis.timeframe_def_refs")
    )
    strategy = _keys(item["strategy_ref"], ("strategy_id", "version", "digest"), "strategy_ref")
    account = _keys(item["account"], ("account_id", "currency", "initial_balance"), "account")
    balance = _keys(account["initial_balance"], ("amount", "currency"), "initial_balance")
    symbol_spec = _keys(item["symbol_spec_ref"], ("symbol", "version", "digest"), "symbol_spec")
    snapshot = _keys(item["snapshot_ref"], ("snapshot_id",), "snapshot_ref")
    return ComparisonBasis(
        research_policy_ref=_policy_ref_c(item["research_policy_ref"], "research_policy_ref"),
        metric_set_version=_int_of(item["metric_set_version"], "basis.metric_set_version"),
        snapshot_ref=SnapshotRef(snapshot_id=SnapshotId(_hex_id(snapshot["snapshot_id"], "id"))),
        strategy_ref=StrategyRef(
            strategy_id=_str_of(strategy["strategy_id"], "strategy_ref.strategy_id"),
            version=_int_of(strategy["version"], "strategy_ref.version"),
            digest=_cdigest_of(strategy["digest"], "strategy_ref.digest"),
        ),
        execution_series=_series_of(
            _str_of(item["execution_series"], "basis.execution_series"),
            {ref.id: ref for ref in timeframe_refs},
        ),
        seed=_int_of(item["seed"], "basis.seed"),
        account=AccountSpec(
            account_id=AccountId(_str_of(account["account_id"], "account.account_id")),
            currency=CurrencyCode(_str_of(account["currency"], "account.currency")),
            initial_balance=Money(
                _decimal_of(balance["amount"], "initial_balance.amount"),
                CurrencyCode(_str_of(balance["currency"], "initial_balance.currency")),
            ),
        ),
        risk_policy_ref=_policy_ref_c(item["risk_policy_ref"], "risk_policy_ref"),
        execution_policy_ref=_policy_ref_c(item["execution_policy_ref"], "execution_policy_ref"),
        cost_model_ref=_policy_ref_c(item["cost_model_ref"], "cost_model_ref"),
        conversion_policy_ref=_policy_ref_c(item["conversion_policy_ref"], "conversion_policy_ref"),
        delay_scenario_ref=_policy_ref_c(item["delay_scenario_ref"], "delay_scenario_ref"),
        symbol_spec_ref=SymbolSpecRef(
            symbol=Symbol(_str_of(symbol_spec["symbol"], "symbol_spec_ref.symbol")),
            version=_int_of(symbol_spec["version"], "symbol_spec_ref.version"),
            digest=_cdigest_of(symbol_spec["digest"], "symbol_spec_ref.digest"),
        ),
        calendar_ref=_str_of(item["calendar_ref"], "basis.calendar_ref"),
        timeframe_def_refs=timeframe_refs,
        code_digest=CodeDigest(_wrapped_digest(item["code_digest"], "basis.code_digest")),
        lock_digest=LockDigest(_wrapped_digest(item["lock_digest"], "basis.lock_digest")),
        env_digest=EnvDigest(_wrapped_digest(item["env_digest"], "basis.env_digest")),
    )


def ledger_entry_of(payload: object) -> TrialLedgerEntry:
    """台帳の行の `entry`（正規化形の JSON の値）を型に直す（D09 §10.12.2 の L4）。

    未知のキー・欠けたキー・型の不一致・語彙外の値・`schema_version` が 1 でないものは
    `KernelValueError`（`ValueError` 系）。行の種類ごとの形と判定と用途の組は見ない（L5・L10）。
    """
    item = _keys(payload, tuple(TrialLedgerEntry.__dataclass_fields__), "ledger entry")
    status = item["status"]
    verdict = item["verdict"]
    frequency_class = item["frequency_class"]
    return TrialLedgerEntry(
        schema_version=_int_of(item["schema_version"], "entry.schema_version"),
        event=TrialLedgerEvent(item["event"]),
        experiment_id=ExperimentId(_hex_id(item["experiment_id"], "entry.experiment_id")),
        execution=_int_of(item["execution"], "entry.execution"),
        execution_nonce=_str_of(item["execution_nonce"], "entry.execution_nonce"),
        experiment_name=_str_of(item["experiment_name"], "entry.experiment_name"),
        experiment_version=_int_of(item["experiment_version"], "entry.experiment_version"),
        strategy_id=_str_of(item["strategy_id"], "entry.strategy_id"),
        basis=_basis_of(item["basis"]),
        trial_count=_int_of(item["trial_count"], "entry.trial_count"),
        search_plan_digest=_cdigest_of(item["search_plan_digest"], "entry.search_plan_digest"),
        validation_intervals=tuple(
            _interval_c(entry, "entry.validation_intervals")
            for entry in _list_of(item["validation_intervals"], "entry.validation_intervals")
        ),
        final_holdout=_final_holdout_of(item["final_holdout"]),
        purpose=StandardPurpose(item["purpose"]),
        status=None if status is None else ExperimentStatus(status),
        verdict=None if verdict is None else SearchVerdict(verdict),
        frequency_class=None
        if frequency_class is None
        else _str_of(frequency_class, "entry.frequency_class"),
    )


def search_directory(directory: Path) -> Path:
    """実験の版のディレクトリの探索の記録の置き場 `search/`（D09 §11.1）。"""
    return Path(directory) / SEARCH_DIRECTORY


def _search_manifest_items(manifest: ExperimentManifest) -> dict[str, Any]:
    """探索の記録票に足す項目（D09 §10.2）を正規化形の JSON の値にする。"""
    return {
        "evaluation_standard": _canonical_json(manifest.evaluation_standard),
        "final_holdout": _canonical_json(manifest.final_holdout),
        "trials": [_canonical_json(trial) for trial in manifest.trials],
    }


def _search_manifest_values(payload: Mapping[str, Any]) -> dict[str, Any]:
    """探索の記録票の項目を型へ戻す（保存した正規化形と一致することも確かめる）。"""
    return {
        "search_plan": _exact(payload["search_plan"], _search_plan_of, "search_plan"),
        "split": _exact(payload["split"], _split_spec_of, "split"),
        "evaluation_standard": _exact(
            payload["evaluation_standard"], _evaluation_standard_of, "evaluation_standard"
        ),
        "final_holdout": _exact(payload["final_holdout"], _final_holdout_of, "final_holdout"),
        "trials": tuple(
            _exact(item, _trial_plan_of, "trials") for item in _list_of(payload["trials"], "trials")
        ),
        "compiled_ref": None,
        "expected_config_digest": None,
    }


def _write_new_json(path: Path, payload: Any, label: str) -> None:
    """JSON を新しく書く（既にあれば何も書かずに `ArtifactAlreadyExists`。R4）。"""
    try:
        write_new_file(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        raise ArtifactAlreadyExists(
            f"{path} already exists; {label} is never overwritten, and nothing was written"
            " (D09 §11.1, R4)"
        ) from None


def _read_json_value(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        raise KernelValueError(f"{path} is not a readable {label} file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KernelValueError(f"{path} cannot be read as JSON: {exc}") from exc


def _read_record[ValueT](path: Path, decoder: Callable[[Any], ValueT], label: str) -> ValueT:
    payload = _read_json_value(path, label)
    try:
        return _exact(payload, decoder, label)
    except _READ_ERRORS as exc:
        raise KernelValueError(
            f"{path} is not a valid {label}: {type(exc).__name__}: {exc}"
        ) from exc


def read_unit_records(
    directory: Path,
) -> tuple[dict[TrialUnitKey, TrialStartRecord], dict[TrialUnitKey, TrialRunRecord]]:
    """実験の版のディレクトリの開始記録と試行記録を読む（D09 §10.3・§11.1）。

    `search/units/` の名前は正規表現 `f<k>_<TRAIN|VALIDATION>_t<i>` に完全一致する鍵に
    `.start.json` か `.json` を付けたものだけを読み、それ以外は構造エラーとする。ファイル名の鍵と
    中身の単位の鍵が違うものも構造エラー。`search/units/` が無ければ両方とも空。
    """
    units = search_directory(directory) / UNITS_DIRECTORY
    starts: dict[TrialUnitKey, TrialStartRecord] = {}
    runs: dict[TrialUnitKey, TrialRunRecord] = {}
    if not (units.exists() or units.is_symlink()):
        return starts, runs
    if units.is_symlink() or not units.is_dir():
        raise KernelValueError(f"{units} is not a plain directory (D09 §11.1)")
    for path in sorted(units.iterdir()):
        name = path.name
        if name.endswith(_START_SUFFIX):
            unit = unit_of_name(name.removesuffix(_START_SUFFIX))
            start = _read_record(path, _start_record_of, "start record")
            if start.unit != unit:
                raise KernelValueError(f"{path} holds the start record of {start.unit}")
            starts[unit] = start
        elif name.endswith(_RUN_SUFFIX):
            unit = unit_of_name(name.removesuffix(_RUN_SUFFIX))
            record = _read_record(path, _run_record_of, "trial record")
            if record.unit != unit:
                raise KernelValueError(f"{path} holds the trial record of {record.unit}")
            runs[unit] = record
        else:
            raise KernelValueError(f"{path} is not a unit record file (D09 §11.1)")
    return starts, runs


def read_selections(directory: Path) -> dict[int, FoldSelection]:
    """実験の版のディレクトリの選定記録を fold の番号で読む（D09 §7.5・§11.1）。"""
    search = search_directory(directory)
    found: dict[int, FoldSelection] = {}
    if not search.is_dir() or search.is_symlink():
        return found
    for path in sorted(search.iterdir()):
        match = _SELECTION_NAME.fullmatch(path.name)
        if match is None:
            continue
        selection = _read_record(path, _fold_selection_of, "selection record")
        if selection.fold_index != int(match.group(1)):
            raise KernelValueError(f"{path} holds the selection of fold {selection.fold_index}")
        found[selection.fold_index] = selection
    return found


def read_ledger_binding(directory: Path) -> TrialLedgerBinding | None:
    """今の世代の束縛の記録 `search/ledger_execution.json` を読む（無ければ `None`）。"""
    path = search_directory(directory) / LEDGER_BINDING_FILE
    if not (path.exists() or path.is_symlink()):
        return None
    return _read_record(path, _binding_of, "ledger binding")


# --- 集約表（D09 §11.2）------------------------------------------------------------

#: `trial_units` の列と型（D09 §11.2。宣言に従い、推論しない。十進数は無い）。
_TRIAL_UNITS_SCHEMA: Mapping[str, pl.DataType] = {
    "fold_index": pl.Int64(),
    "phase": pl.String(),
    "trial_index": pl.Int64(),
    "assignment": pl.String(),
    "compiled_ref": pl.String(),
    "status": pl.String(),
    "run_id": pl.String(),
    "run_status": pl.String(),
    "run_reused": pl.Boolean(),
    "run_evaluation_id": pl.String(),
    "evaluation_status": pl.String(),
    "candidate_status": pl.String(),
    "selected": pl.Boolean(),
}

#: `trial_metrics` の鍵の列（指標の列は評価の `METRICS` 表の列をそのまま写す。D07 §8.1）。
_TRIAL_METRICS_KEYS: Mapping[str, pl.DataType] = {
    "fold_index": pl.Int64(),
    "phase": pl.String(),
    "trial_index": pl.Int64(),
}


def _metrics_schema() -> dict[str, pl.DataType]:
    kinds = column_kinds(MetricRecord)
    return {
        **_TRIAL_METRICS_KEYS,
        **{name: _dtype_of(kinds.get(name, "string")) for name in column_names(MetricRecord)},
    }


def _write_new_parquet(path: Path, frame: pl.DataFrame) -> None:
    """Parquet を新しく書く（一時ファイル＋上書きしない改名。既にあれば失敗。R4）。"""
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    try:
        frame.write_parquet(temporary)
        with open(temporary, "rb") as stream:
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise ArtifactAlreadyExists(
                f"{path} already exists; aggregate tables are never overwritten, and nothing was"
                " written (D09 §11.2, R4)"
            ) from None
    finally:
        Path(temporary).unlink(missing_ok=True)


_METRIC_ORDER: Mapping[str, int] = {metric.value: index for index, metric in enumerate(MetricId)}


#: `METRICS` 表の値の列（区分ごとに1列。D06 §9.1 の規則2 で値の無い列は `None`）。
_METRIC_VALUE_COLUMNS: Mapping[MetricKind, str] = {
    MetricKind.AMOUNT: "value_amount_amount",
    MetricKind.RATIO: "value_ratio",
    MetricKind.COUNT: "value_count",
    MetricKind.DURATION: "value_duration",
    MetricKind.PRICE_OFFSET: "value_offset",
}


def _metric_record_of(row: Mapping[str, object]) -> MetricRecord:
    """`METRICS` 表の1行を `MetricRecord` へ戻す（D07 §8.1・§8.2 の平坦化の逆）。

    区分に合わない列に値がある・値の列と値なしの理由が両方ある（または両方無い）行は、
    読み替えずに拒否する。
    """
    metric = MetricId(_str_of(row["metric_id"], "metric_id"))
    kind = MetricKind(_str_of(row["value_kind"], "value_kind"))
    reason = row["value_reason"]
    present = {
        column: row[column]
        for column in (*_METRIC_VALUE_COLUMNS.values(), "value_amount_currency")
        if row[column] is not None
    }
    value: MetricValue
    if reason is not None:
        if present:
            raise KernelValueError(
                f"{metric.value}: an unavailable value has value columns {present}"
            )
        value = Unavailable(
            kind=kind, reason=MetricUnavailableReason(_str_of(reason, "value_reason"))
        )
    else:
        allowed = {_METRIC_VALUE_COLUMNS[kind]}
        if kind is MetricKind.AMOUNT:
            allowed.add("value_amount_currency")
        if set(present) != allowed:
            raise KernelValueError(
                f"{metric.value}: a {kind.value} value must fill exactly {sorted(allowed)},"
                f" got {sorted(present)}"
            )
        column = _METRIC_VALUE_COLUMNS[kind]
        if kind is MetricKind.AMOUNT:
            value = AmountValue(
                amount=Money(
                    _decimal_of(row[column], column),
                    CurrencyCode(_str_of(row["value_amount_currency"], "value_amount_currency")),
                )
            )
        elif kind is MetricKind.RATIO:
            value = RatioValue(ratio=_decimal_of(row[column], column))
        elif kind is MetricKind.COUNT:
            value = CountValue(count=_int_of(row[column], column))
        elif kind is MetricKind.DURATION:
            seconds = _decimal_of(row[column], column)
            # 換算はカーネルの十進数設定の下で行う（プロセス全体の十進数設定に依存しない。
            # ADR-0012、D02。段階5 実装 PR 4 の残件）。
            with localcontext(kernel_context()):
                microseconds = int(seconds * 1_000_000)
            value = DurationValue(duration=timedelta(microseconds=microseconds))
        else:
            value = PriceOffsetValue(offset=PriceOffset(_decimal_of(row[column], column)))
    return MetricRecord(
        metric_id=metric,
        value=value,
        caveats=tuple(
            MetricCaveat(_str_of(item, "caveats")) for item in _list_of(row["caveats"], "caveats")
        ),
        observation_count=_int_of(row["observation_count"], "observation_count"),
        inputs=tuple(
            TraceTable(_str_of(item, "inputs")) for item in _list_of(row["inputs"], "inputs")
        ),
    )


def read_trial_metrics(directory: Path) -> dict[TrialUnitKey, tuple[MetricRecord, ...]]:
    """完了した実行の集約表 `search/trial_metrics.parquet` を単位ごとの指標の行として読む。

    D09 §11.2。

    行は保存の順（主キーの順）のまま単位ごとにまとめ、`MetricRecord` へ戻す（`_metric_record_of`。
    値を計算し直さない）。表が無い・リンク・列が足りない・行が読めない・主キーが重複するときは
    `KernelValueError`（レポートは「読めない」と表示する）。
    """
    path = search_directory(directory) / TRIAL_METRICS_TABLE
    if path.is_symlink() or not path.is_file():
        raise KernelValueError(f"{SEARCH_DIRECTORY}/{TRIAL_METRICS_TABLE} is missing or not a file")
    try:
        frame = pl.read_parquet(path)
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise KernelValueError(
            f"{SEARCH_DIRECTORY}/{TRIAL_METRICS_TABLE} cannot be read: {type(exc).__name__}"
        ) from exc
    expected = tuple(_metrics_schema())
    if tuple(frame.columns) != expected:
        raise KernelValueError(
            f"{SEARCH_DIRECTORY}/{TRIAL_METRICS_TABLE} has the columns {list(frame.columns)},"
            f" not {list(expected)}"
        )
    found: dict[TrialUnitKey, list[MetricRecord]] = {}
    seen: set[tuple[TrialUnitKey, str]] = set()
    for row in frame.iter_rows(named=True):
        try:
            unit = TrialUnitKey(
                fold_index=_int_of(row["fold_index"], "fold_index"),
                phase=TrialPhase(_str_of(row["phase"], "phase")),
                trial_index=_int_of(row["trial_index"], "trial_index"),
            )
            record = _metric_record_of(row)
        except _READ_ERRORS as exc:
            raise KernelValueError(
                f"{SEARCH_DIRECTORY}/{TRIAL_METRICS_TABLE} has a row that cannot be read:"
                f" {type(exc).__name__}: {exc}"
            ) from exc
        key = (unit, record.metric_id.value)
        if key in seen:
            raise KernelValueError(
                f"{SEARCH_DIRECTORY}/{TRIAL_METRICS_TABLE} repeats the primary key {key}"
                " (D09 §11.2)"
            )
        seen.add(key)
        found.setdefault(unit, []).append(record)
    return {unit: tuple(records) for unit, records in found.items()}


def trial_units_rows(
    manifest: ExperimentManifest,
    selections: Sequence[FoldSelection],
    records: Sequence[TrialRunRecord],
) -> list[dict[str, object]]:
    """`trial_units` の行（D09 §11.2）。失敗の試行は選定区間の fold ごとに1行、検証区間は選んだ
    試行の分だけ。主キー `(fold_index, phase, trial_index)` の順。重複は構造エラー。"""
    by_unit = {record.unit: record for record in records}
    if len(by_unit) != len(records):
        raise KernelValueError("two trial records share a unit (D09 §11.2: 主キーの重複)")
    plans = {trial.trial_index: trial for trial in manifest.trials}
    rows: list[dict[str, object]] = []
    for selection in sorted(selections, key=lambda item: item.fold_index):
        status_of = {entry[0]: entry[2] for entry in selection.inputs}
        units = [
            TrialUnitKey(fold_index=selection.fold_index, phase=TrialPhase.TRAIN, trial_index=index)
            for index in sorted(plans)
        ]
        if selection.selected_trial_index is not None:
            units.append(
                TrialUnitKey(
                    fold_index=selection.fold_index,
                    phase=TrialPhase.VALIDATION,
                    trial_index=selection.selected_trial_index,
                )
            )
        for unit in units:
            plan = plans[unit.trial_index]
            record = by_unit.get(unit)
            if plan.compiled and record is None:
                raise KernelValueError(
                    f"the unit {unit_name(unit)} has no trial record; the aggregate tables are"
                    " written only when the search has finished (D09 §11.2)"
                )
            rows.append(
                {
                    "fold_index": unit.fold_index,
                    "phase": unit.phase.value,
                    "trial_index": unit.trial_index,
                    "assignment": encode(plan.assignment).decode("utf-8"),
                    "compiled_ref": None
                    if plan.compiled_ref is None
                    else plan.compiled_ref.digest.hex,
                    "status": (
                        TrialStatus.COMPLETED if record is not None else TrialStatus.FAILED
                    ).value,
                    "run_id": None if record is None else record.run_id.hex,
                    "run_status": None if record is None else record.run_status.value,
                    "run_reused": None if record is None else record.run_reused,
                    "run_evaluation_id": None if record is None else record.run_evaluation_id.hex,
                    "evaluation_status": None if record is None else record.evaluation_status.value,
                    "candidate_status": status_of[unit.trial_index].value
                    if unit.phase is TrialPhase.TRAIN
                    else None,
                    "selected": unit.trial_index == selection.selected_trial_index,
                }
            )
    leftover = set(by_unit) - {
        TrialUnitKey(
            fold_index=row["fold_index"],  # type: ignore[arg-type]
            phase=TrialPhase(row["phase"]),
            trial_index=row["trial_index"],  # type: ignore[arg-type]
        )
        for row in rows
    }
    if leftover:
        raise KernelValueError(
            f"trial records {sorted(unit_name(unit) for unit in leftover)} belong to no unit of"
            " the selections (D09 §10.4)"
        )
    return rows


# --- 試行台帳（D09 §10.10・§10.12）----------------------------------------------------

#: 台帳とロックの置き場（リポジトリの根からの相対パス。D01 §10.4、D09 §10.10・§10.12.1）。
TRIAL_LEDGER_PATH = Path("research") / "trial_ledger.jsonl"
TRIAL_LEDGER_LOCK_PATH = Path("research") / "trial_ledger.lock"


def ledger_line_bytes(line: TrialLedgerLine) -> bytes:
    """台帳の1行の符号化（D09 §10.12.6）: `{digest, entry, prev}` の正規化エンコード＋改行1つ。"""
    return (
        encode(
            {
                "digest": line.digest.hex,
                "entry": line.entry,
                "prev": None if line.prev is None else line.prev.hex,
            }
        )
        + b"\n"
    )


def _line_failure(
    kind: TrialLedgerDefect, number: int | None, detail: str
) -> TrialLedgerReadFailure:
    return TrialLedgerReadFailure(kind=kind, line_number=number, detail=detail)


def _raw_line(data: bytes) -> tuple[Mapping[str, Any], ContentDigest, ContentDigest | None] | str:
    """改行で終わる1行に L2 を当てる。合格なら `(包み, digest, prev)`、不合格なら理由。"""
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return f"the line is not UTF-8 JSON: {exc}"
    if not isinstance(payload, dict) or set(payload) != {"digest", "entry", "prev"}:
        return "the line is not an object with exactly digest / entry / prev"
    try:
        if encode(payload) != data:
            return "the line is not in the canonical encoding (D09 §10.12.6)"
        recorded = ContentDigest.sha256(_str_of(payload["digest"], "digest"))
        prev = payload["prev"]
        previous = None if prev is None else ContentDigest.sha256(_str_of(prev, "prev"))
        computed = digest({"entry": payload["entry"], "prev": prev})
    except _READ_ERRORS as exc:
        return f"the line cannot be checked: {type(exc).__name__}: {exc}"
    if computed != recorded:
        return f"digest {recorded.hex} does not match {{entry, prev}} ({computed.hex})"
    return payload, recorded, previous


def read_trial_ledger_file(path: Path) -> TrialLedgerContents | TrialLedgerReadFailure:
    """台帳のファイルを読み、読込の検査 L0〜L10 を当てる（D09 §10.12.2）。

    ファイルの先頭から行の順に当て、最初に当たった食い違いを1件返す。最後の改行より後の、改行で
    終わらない断片は書きかけ（L1）として行に入れず `torn_tail = true` にする（失敗にしない）。
    """
    if not (path.exists() or path.is_symlink()):
        return _line_failure(
            TrialLedgerDefect.FILE_MISSING,
            None,
            f"{TRIAL_LEDGER_PATH} does not exist; it is placed (empty) under version control and"
            " is never created by an append. Restore it from the history (D09 §10.12.2 の L0)",
        )
    try:
        data = path.read_bytes()
    except OSError as exc:
        return _line_failure(
            TrialLedgerDefect.FILE_UNREADABLE,
            None,
            f"{TRIAL_LEDGER_PATH} cannot be read: {type(exc).__name__}: {exc.strerror}",
        )
    parts = data.split(b"\n")
    complete, fragment = parts[:-1], parts[-1]
    lines: list[TrialLedgerLine] = []
    stopped: TrialLedgerReadFailure | None = None
    previous: ContentDigest | None = None
    for number, raw in enumerate(complete, start=1):
        checked = _raw_line(raw)
        if isinstance(checked, str):
            stopped = _line_failure(TrialLedgerDefect.LINE_CORRUPT, number, checked)
            break
        payload, recorded, prev = checked
        try:
            entry = _exact(payload["entry"], ledger_entry_of, "the ledger entry")
            line = TrialLedgerLine(entry=entry, prev=prev, digest=recorded)
        except _READ_ERRORS as exc:
            if prev != previous:
                # 同じ行では検査の番号の順に当てる（L3 は L4 より先）。
                stopped = _line_failure(
                    TrialLedgerDefect.CHAIN_BROKEN,
                    number,
                    "prev does not equal the digest of the line before",
                )
            else:
                stopped = _line_failure(
                    TrialLedgerDefect.ENTRY_INVALID, number, f"{type(exc).__name__}: {exc}"
                )
            break
        lines.append(line)
        previous = recorded
    found = ledger_defect(lines)
    if found is not None:
        kind, number, detail = found
        return _line_failure(kind, number, detail)
    if stopped is not None:
        return stopped
    return TrialLedgerContents(lines=tuple(lines), torn_tail=bool(fragment))


def _lock_text() -> str:
    """ロックの中身（人間が読むためのもの。ツールはこれを見て判断しない。D09 §10.12.1 の W1）。"""
    return (
        json.dumps(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "host": socket.gethostname(),
                "pid": os.getpid(),
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def _append_bytes(path: Path, keep: int, data: bytes) -> None:
    """末尾の書きかけを切り詰め（`keep` バイトまで戻す）、行全体を1回で書いて同期する。

    書き込みか同期に失敗すれば `OSError`（呼び出し側が `WRITE_FAILED` にする）。
    """
    descriptor = os.open(path, os.O_WRONLY)
    try:
        os.ftruncate(descriptor, keep)
        os.lseek(descriptor, 0, os.SEEK_END)
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError(f"only {written} of {len(data)} bytes were written")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def append_trial_ledger_file(
    root: Path, line: TrialLedgerLine
) -> TrialLedgerLine | TrialLedgerAppendRefused:
    """1行を台帳へ追記する（D09 §10.12.1 の操作の形の (1)〜(6)）。

    (1) 台帳が無ければロックも作らずに `READ_FAILED`。あればロックを排他的に作る（できなければ
    `LOCKED`）。(2) ロックの下で読み直して検査し、(3) 最後の完全な行の `digest` と `line.prev` を
    照合し（違えば `TAIL_CHANGED`）、(4) 末尾の書きかけを切り詰め、(5) 行を1回で書いて同期し、
    (6) ロックを消す。断るとき（(1) を除く）もロックを消してから返す。
    """
    if line.digest != ledger_line_digest(line.entry, line.prev):
        raise KernelValueError("the line digest does not match {entry, prev} (D09 §10.12.1)")
    path = Path(root) / TRIAL_LEDGER_PATH
    lock = Path(root) / TRIAL_LEDGER_LOCK_PATH
    if not (path.exists() or path.is_symlink()):
        failure = read_trial_ledger_file(path)
        assert isinstance(failure, TrialLedgerReadFailure)  # noqa: S101  無いので必ず読めない
        return TrialLedgerAppendRefused(
            kind=TrialLedgerRefusal.READ_FAILED, read_failure=failure, detail=failure.detail
        )
    try:
        write_new_file(lock, _lock_text())
    except FileExistsError:
        return TrialLedgerAppendRefused(
            kind=TrialLedgerRefusal.LOCKED,
            read_failure=None,
            detail=(
                f"{TRIAL_LEDGER_LOCK_PATH} exists: another append is running, or a stopped"
                " writer left it. It is never removed automatically; make sure no writer is"
                " running, then delete it (D09 §10.12.1 の W1)"
            ),
        )
    try:
        contents = read_trial_ledger_file(path)
        if isinstance(contents, TrialLedgerReadFailure):
            return TrialLedgerAppendRefused(
                kind=TrialLedgerRefusal.READ_FAILED, read_failure=contents, detail=contents.detail
            )
        tail = last_digest(contents.lines)
        if tail != line.prev:
            return TrialLedgerAppendRefused(
                kind=TrialLedgerRefusal.TAIL_CHANGED,
                read_failure=None,
                detail=(
                    "the last line of the ledger changed after it was read (its digest is"
                    f" {None if tail is None else tail.hex}); nothing was written"
                ),
            )
        keep = len(path.read_bytes())
        if contents.torn_tail:
            keep = path.read_bytes().rfind(b"\n") + 1
        try:
            _append_bytes(path, keep, ledger_line_bytes(line))
        except OSError as exc:
            return TrialLedgerAppendRefused(
                kind=TrialLedgerRefusal.WRITE_FAILED,
                read_failure=None,
                detail=(
                    f"writing or syncing the line failed: {exc}. If the line was written in full"
                    " it is a valid line on the next read (D09 §10.12.1 の W3)"
                ),
            )
        return line
    finally:
        lock.unlink(missing_ok=True)


def _latest_binding_file(version: Path) -> Path | None:
    """束縛の記録がある最も新しい世代の束縛の記録（D09 §10.12.2 の L11。Q39）。"""
    current = version / SEARCH_DIRECTORY / LEDGER_BINDING_FILE
    if current.exists() or current.is_symlink():
        return current
    kept: list[tuple[int, Path]] = []
    for entry in version.iterdir():
        match = _KEPT_SEARCH.fullmatch(entry.name)
        if match is None:
            continue
        candidate = entry / LEDGER_BINDING_FILE
        if candidate.exists() or candidate.is_symlink():
            kept.append((int(match.group(1)), candidate))
    return max(kept)[1] if kept else None


def read_ledger_binding_records(
    out_base: Path,
) -> tuple[tuple[str, TrialLedgerBinding | TrialLedgerReadFailure], ...]:
    """成果物の基点の下の各実験の版の、束縛の記録がある最も新しい世代の束縛の記録を集める。

    `<基点>/runs/experiments/*/v*/` の各版について1つずつ、基点からの相対パス（POSIX の区切り）の
    辞書順に `(パス, 中身か読めない理由)` で返す（D09 §10.12.1・§10.12.2 の L11。Q38・Q39）。
    """
    base = Path(out_base)
    experiments = base / "runs" / "experiments"
    found: list[tuple[str, TrialLedgerBinding | TrialLedgerReadFailure]] = []
    if not experiments.is_dir():
        return ()
    for name in sorted(experiments.iterdir()):
        if not name.is_dir():
            continue
        for version in sorted(name.iterdir()):
            if not version.is_dir() or not re.fullmatch(r"v[1-9][0-9]*", version.name):
                continue
            path = _latest_binding_file(version)
            if path is None:
                continue
            relative = path.relative_to(base).as_posix()
            try:
                found.append((relative, _read_record(path, _binding_of, "ledger binding")))
            except KernelValueError as exc:
                found.append(
                    (
                        relative,
                        _line_failure(
                            TrialLedgerDefect.UNMATCHED_BINDING,
                            None,
                            f"{relative} cannot be read: {exc}",
                        ),
                    )
                )
    return tuple(sorted(found, key=lambda item: item[0]))


# --- 同じ版の再実行の退避（D09 §11.3 の Q13）--------------------------------------------


def _move_directory(current: Path, target: Path) -> None:
    """`current` を `target` へ移す。移し先が既にあれば何もせずに失敗する（R4）。"""
    if current.is_symlink() or not current.is_dir():
        raise ArtifactAlreadyExists(
            f"{current} is not a plain directory; it was left as is and nothing ran (D09 §11.3)"
        )
    if target.exists() or target.is_symlink():
        raise ArtifactAlreadyExists(
            f"{target} already exists; kept records are never overwritten (D09 §11.3, R4)"
        )
    current.rename(target)


def _retreat_pairs(directory: Path, number: int) -> tuple[tuple[Path, Path], ...]:
    return (
        (directory / EXPERIMENT_OUTCOME_FILE, directory / f"experiment_outcome.{number}.json"),
        (directory / REPORT_FILE, directory / f"report.{number}.md"),
        (directory / SEARCH_DIRECTORY, directory / f"search.{number}"),
    )


def _move_generation(directory: Path, number: int) -> None:
    for current, target in _retreat_pairs(directory, number):
        if not _present(current):
            continue
        if current.name == SEARCH_DIRECTORY:
            _move_directory(current, target)
        else:
            _keep_file(current, target)


def _read_marker(marker: Path) -> int:
    payload = _read_json_value(marker, "retreat marker")
    try:
        item = _keys(payload, ("schema_version", "n"), "retreat marker")
        if _int_of(item["schema_version"], "schema_version") != 1:
            raise KernelValueError("the retreat marker must have schema_version 1")
        number = _int_of(item["n"], "n")
    except _READ_ERRORS as exc:
        raise KernelValueError(
            f"{marker} cannot be read ({exc}); nothing was moved and it is kept. Inspect the"
            " directory by hand (D09 §11.3 の5)"
        ) from exc
    if number < 1:
        raise KernelValueError(f"{marker} has n {number}; nothing was moved (D09 §11.3 の5)")
    return number


def keep_previous_search_records(directory: Path) -> None:
    """探索の実験の同じ版の再実行の退避（D09 §11.3 の Q13 の1〜5）。記録票の保存の直後に呼ぶ。

    「退避中」の印があれば印の `n` で前回の退避を回復し（元の場所にあるものだけを移す。元の場所と
    退避先の両方にあれば何も動かさず印も残して止める）、印を消す。そのうえで旧い結末記録・
    レポート・探索の記録があれば、連番 `n` を決めて印を書き、3つを移してから印を消す。
    """
    directory = Path(directory)
    marker = directory / RETREAT_MARKER_FILE
    if _present(marker):
        number = _read_marker(marker)
        both = [
            current.name
            for current, target in _retreat_pairs(directory, number)
            if _present(current) and _present(target)
        ]
        if both:
            raise ArtifactAlreadyExists(
                f"recovering the retreat {number} found {both} both in place and kept; nothing was"
                f" moved and {RETREAT_MARKER_FILE} is kept. Compare them, delete the unneeded"
                " side, and re-run without deleting the marker (D09 §11.3 の5)"
            )
        _move_generation(directory, number)
        marker.unlink()
    present = [current for current, _ in _retreat_pairs(directory, 0) if _present(current)]
    if not present:
        return
    number = next_kept_number(directory)
    try:
        write_new_file(marker, json.dumps({"schema_version": 1, "n": number}) + "\n")
    except FileExistsError:  # pragma: no cover - 直前に無いことを確かめた
        raise ArtifactAlreadyExists(f"{marker} appeared while retreating (D09 §11.3)") from None
    _move_generation(directory, number)
    marker.unlink()


# --- 別プロセスでの再現の報告（D07 §21.2）------------------------------------------

#: 再現の報告のファイル名（`--out` の直下。D07 §21.2 の手順5）。
REPRODUCTION_FILE = "reproduction.json"


def reproduction_payload(
    experiment_id: ExperimentId,
    verdict: str,
    expected_run_id: RunId,
    observed_run_id: RunId | None,
    expected_result_digest: ContentDigest,
    observed_result_digest: ContentDigest | None,
) -> dict[str, Any]:
    """再現の報告（`ReproductionReport`）の JSON。"""
    return {
        "experiment_id": experiment_id.hex,
        "verdict": verdict,
        "expected_run_id": expected_run_id.hex,
        "observed_run_id": None if observed_run_id is None else observed_run_id.hex,
        "expected_result_digest": expected_result_digest.hex,
        "observed_result_digest": _optional_hex(observed_result_digest),
    }


def reproduction_path(out_root: Path) -> Path:
    """再現の報告の置き場（`--out` の直下）。"""
    return Path(out_root) / REPRODUCTION_FILE


def write_reproduction(out_root: Path, payload: Mapping[str, Any]) -> Path:
    """再現の報告を新しく書く。既にあれば何も書かずに `ArtifactAlreadyExists`（R4）。"""
    path = reproduction_path(out_root)
    _require_root_is_directory(Path(out_root))
    Path(out_root).mkdir(parents=True, exist_ok=True)
    try:
        write_new_file(path, _json_text(payload))
    except FileExistsError:
        raise _already_exists(
            path, "Choose another --out, or move the earlier reproduction away"
        ) from None
    return path
