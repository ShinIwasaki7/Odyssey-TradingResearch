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
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
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
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AccountId, ExperimentId, RunId, SnapshotId
from odyssey_fx.common.money import CurrencyCode, Money, decimal_from_str
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
    CategoryCount,
    FillDiagnostic,
    MetricRecord,
    TradeRecord,
)
from odyssey_fx.evaluation.domain.research_policy import (
    ComplexityLimits,
    ComplexityMeasures,
    PolicyCheck,
    PolicyCheckResult,
    PolicyCheckStage,
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

__all__ = [
    "EXPERIMENT_MANIFEST_FILE",
    "EXPERIMENT_OUTCOME_FILE",
    "REPRODUCTION_FILE",
    "FileSystemExperimentStore",
    "FileSystemResultRepository",
    "FileSystemResultWriter",
    "FileSystemTraceSink",
    "create_artifact_directory",
    "evaluation_directory",
    "experiment_directory",
    "experiment_manifest_from_payload",
    "experiment_manifest_payload",
    "experiment_outcome_from_payload",
    "experiment_outcome_payload",
    "manifest_from_payload",
    "read_experiment_manifest",
    "read_experiment_outcome",
    "replaced_manifest_name",
    "reproduction_path",
    "reproduction_payload",
    "require_absent",
    "require_run_directory_absent",
    "reserve_run_directory",
    "result_from_payload",
    "run_directory",
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

#: 退避した旧い結末記録（`experiment_outcome.<n>.json`。n は 1 から。D07 §19.3）。
_KEPT_OUTCOME = re.compile(r"experiment_outcome\.([1-9][0-9]*)\.json")


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


def _write_new_file(path: Path, text: str) -> None:
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
    """
    policy = manifest.research_policy_ref
    strategy = manifest.strategy_ref
    return {
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
        "search_plan": manifest.search_plan,
        "split": manifest.split,
        "resolved_files": [
            {"role": item.role, "sha256": item.sha256, "text": item.text}
            for item in sorted(manifest.resolved_files, key=lambda item: item.role)
        ],
        "strategy_ref": {
            "id": strategy.strategy_id,
            "version": strategy.version,
            "digest": strategy.digest.hex,
        },
        "compiled_ref": manifest.compiled_ref.digest.hex,
        "expected_config_digest": manifest.expected_config_digest.digest.hex,
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


def experiment_manifest_from_payload(payload: Mapping[str, Any]) -> ExperimentManifest:
    """保存した記録票を読み戻す。項目の欠け・型の違いは例外（`KeyError` など）になる。"""
    policy = payload["research_policy_ref"]
    strategy = payload["strategy_ref"]
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
        search_plan=payload["search_plan"],
        split=payload["split"],
        resolved_files=tuple(
            ResolvedFile(role=item["role"], text=item["text"], sha256=item["sha256"])
            for item in payload["resolved_files"]
        ),
        strategy_ref=StrategyRef(
            strategy_id=strategy["id"],
            version=strategy["version"],
            digest=_digest_of(strategy["digest"], "strategy_ref.digest"),
        ),
        compiled_ref=CompiledStrategyRef(_digest_of(payload["compiled_ref"], "compiled_ref")),
        expected_config_digest=ConfigDigest(
            _digest_of(payload["expected_config_digest"], "expected_config_digest")
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
    )


def _optional_hex(value: ContentDigest | None) -> str | None:
    return None if value is None else value.hex


def experiment_outcome_payload(outcome: ExperimentOutcome) -> dict[str, Any]:
    """結末記録を JSON へ落とす（D07 §19.3 の表の項目すべて）。"""
    return {
        "experiment_id": outcome.experiment_id.hex,
        "status": outcome.status.value,
        "expected_run_id": outcome.expected_run_id.hex,
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


def _optional_digest(value: object, label: str) -> ContentDigest | None:
    return None if value is None else _digest_of(value, label)


def experiment_outcome_from_payload(payload: Mapping[str, Any]) -> ExperimentOutcome:
    """保存した結末記録を読み戻す。"""
    run_id = _optional_digest(payload["run_id"], "run_id")
    run_status = payload["run_status"]
    evaluation_status = payload["evaluation_status"]
    return ExperimentOutcome(
        experiment_id=ExperimentId(_digest_of(payload["experiment_id"], "experiment_id")),
        status=ExperimentStatus(payload["status"]),
        expected_run_id=RunId(_digest_of(payload["expected_run_id"], "expected_run_id")),
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
                _write_new_file(path, _json_text(experiment_manifest_payload(manifest)))
                result = ManifestSaveResult.CREATED
            except FileExistsError:
                # 確かめた後に別の実行が書いた。書いたものと比べ直す。
                result = self._compare_existing(path, manifest) or ManifestSaveResult.CONFLICT
        if result is not ManifestSaveResult.CONFLICT:
            self._keep_previous_outcome(directory)
        return result

    @staticmethod
    def _compare_existing(path: Path, manifest: ExperimentManifest) -> ManifestSaveResult | None:
        if not (path.exists() or path.is_symlink()):
            return None
        try:
            existing = read_experiment_manifest(path.parent)
        except KernelValueError:
            return ManifestSaveResult.CONFLICT
        if existing.experiment_id == manifest.experiment_id:
            return ManifestSaveResult.ALREADY_IDENTICAL
        return ManifestSaveResult.CONFLICT

    @staticmethod
    def _keep_previous_outcome(directory: Path) -> None:
        """同じ版に既にある結末記録を `experiment_outcome.<n>.json` へ退避する（D07 §19.3）。

        n は 1 からの連番（既に退避した件数 + 1）。旧い記録は消さず、既存の退避先は上書き
        しない（作成と存在の確認を排他的な作成で1つの操作にする。R4）。
        """
        current = directory / EXPERIMENT_OUTCOME_FILE
        if not (current.exists() or current.is_symlink()):
            return
        if current.is_symlink() or not current.is_file():
            raise ArtifactAlreadyExists(
                f"{current} is not a plain file; it was left as is and nothing ran (D07 §19.3)"
            )
        kept = [
            int(match.group(1))
            for entry in directory.iterdir()
            if (match := _KEPT_OUTCOME.fullmatch(entry.name))
        ]
        target = directory / f"experiment_outcome.{max(kept, default=0) + 1}.json"
        try:
            os.link(current, target)
        except FileExistsError:
            raise ArtifactAlreadyExists(
                f"{target} already exists; kept outcomes are never overwritten, and nothing ran"
                " (D07 §19.3, R4)"
            ) from None
        current.unlink()

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
            _write_new_file(
                directory / EXPERIMENT_OUTCOME_FILE,
                _json_text(experiment_outcome_payload(outcome)),
            )
        except FileExistsError:
            raise ArtifactAlreadyExists(
                f"{directory / EXPERIMENT_OUTCOME_FILE} already exists; outcomes are never"
                " overwritten (D07 §19.3, R4)"
            ) from None


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
        _write_new_file(path, _json_text(payload))
    except FileExistsError:
        raise _already_exists(
            path, "Choose another --out, or move the earlier reproduction away"
        ) from None
    return path
