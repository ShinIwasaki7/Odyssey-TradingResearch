"""探索の実験のレポートと `experiment report` / `reproduce` の探索の経路（段階5 実装 PR 5）。

D09 §11.5（9 見出しの書式）・§10.4（途中で止まった実行）・§10.12.3（束縛の照合 R1〜R7）・
§11.3 の6（「退避中」の印）・§11.4（終了コード。Q37 の登録簿）を、T02 の人工データの作業場で
確かめる。作業場・試験用の研究ポリシー版 3（2 fold。用途は機構の確認。**テスト用であり研究
ポリシーではない**）・探索の実験設定の作り方は `tests/integration/app/test_search_run.py` と同じ。

確かめること（D08 §7.4 の行の番号を括弧に書いた）:

- `experiment run` は最後にレポートを書き、9 つの見出しが同じ順に出る。1行目は用途で、機構の
  確認の版のレポートには「共通基準を満たす」の語が出ない（#17 の後半）。
- 同じ成果物からは同じバイト列（別の場所へ写しても同じ。時刻・絶対パスを入れない）。
- 照合の R1〜R7 の各状況の表示と終了コード（#22 の読む側）。R2・R4 は読込の誤り（終了コード 2）。
- 途中で止まった実行: 各単位の状態（中断・未試行・試行済み）と各 fold の状態を出し、検証済みの
  fold は最低条件の結果と取引件数の写しと「証拠の要件は未確定」だけ。判定・頻度区分は出さない
  （#25）。
- 取引が少ない fold で最低条件を割った事実が判定の欄に出る（#16 の後半）。
- 研究ポリシーの版の登録簿が無い・壊れている・版の要素が無い・ダイジェストが違うと、
  `experiment report` は終了コード 2 でレポートを書かない。`experiment run` の最後に読めなければ
  結末記録と台帳の行はそのままで終了コード 1（#26）。
- 「退避中」の印がある版は終了コード 2。`experiment reproduce` は探索の実験を終了コード 2 で拒否。

最後まで通す実行は1回だけ行い（`base`）、ほかのテストはその成果物を写した基点で走らせる
（同じ設定の単位は既存の run と評価を再利用する。D07 §19.6）。台帳と登録簿は、レポートだけを
読むテストでは作業場の写し（別のリポジトリの根）を使い、作業場の台帳を壊さない。
"""

from __future__ import annotations

import io
import json
import shutil
from collections.abc import Iterator
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config import ConfigError
from odyssey_fx.common.canonical import digest
from odyssey_fx.evaluation.adapters.fs_store import (
    TRIAL_LEDGER_LOCK_PATH,
    experiment_outcome_payload,
    read_experiment_outcome,
    read_selections,
)
from odyssey_fx.evaluation.adapters.search_report import SEARCH_REPORT_HEADINGS
from odyssey_fx.evaluation.application import run_experiment
from odyssey_fx.evaluation.domain import research_policy
from odyssey_fx.evaluation.domain.metrics import MetricId
from odyssey_fx.evaluation.domain.search import (
    Comparator,
    ConditionOutcome,
    ConditionResult,
    ConditionScope,
    SufficiencyShortfall,
    SufficiencyShortfallKind,
)
from tests.fixtures.acceptance.t02_workspace import T02Workspace, build_workspace
from tests.fixtures.evaluation.research_policies import (
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import (
    TWO_FOLD_SPLIT,
    install_policy_v3,
    write_search_experiment,
)

#: 4 試行ともコンパイルが通る2軸（`test_search_run.py` と同じ）。
AXES = (
    "    - {instance: m15_ema, parameter: period, values: [10, 20]}\n"
    "    - {instance: stop_level, parameter: lookback, values: [10, 20]}"
)
_REGISTRY = Path("configs/policies/research/registry.yaml")
_LEDGER = Path("research/trial_ledger.jsonl")


@pytest.fixture(scope="module")
def workspace(tmp_path_factory: pytest.TempPathFactory) -> T02Workspace:
    """試験用の研究ポリシー版 3（機構の確認）・版 4（試行数の上限 3）・版 6（共通基準の判定）。"""
    built = build_workspace(tmp_path_factory.mktemp("t02-search-report"))
    install_policy_v3(built.repo)
    install_policy_v3(built.repo, version=4, trials=3)
    text = policy_v3_text(
        version=6, replace={"split": TWO_FOLD_SPLIT, "purpose": "  purpose: STANDARD"}
    )
    write_policy(built.repo, "research_policy", 6, text)
    append_registry_entry(built.repo, "research_policy", 6)
    return built


def _reset_ledger(workspace: T02Workspace) -> Path:
    ledger = workspace.repo / _LEDGER
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"")
    (workspace.repo / TRIAL_LEDGER_LOCK_PATH).unlink(missing_ok=True)
    return ledger


@pytest.fixture(autouse=True)
def empty_ledger(workspace: T02Workspace) -> Iterator[Path]:
    ledger = _reset_ledger(workspace)
    yield ledger
    (workspace.repo / TRIAL_LEDGER_LOCK_PATH).unlink(missing_ok=True)


def _cli(args: list[str]) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(args)
    return code, stdout.getvalue(), stderr.getvalue()


def _run(workspace: T02Workspace, out: Path, name: str, **kwargs: Any) -> tuple[int, str]:
    experiment = write_search_experiment(
        workspace.repo, f"{name}.yaml", experiment_id=name, axes=AXES, **kwargs
    )
    code, stdout, stderr = _cli(
        [
            "experiment",
            "run",
            "--experiment",
            str(experiment),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(workspace.repo),
        ]
    )
    return code, stdout + stderr


def _report(directory: Path, repo: Path | None) -> tuple[int, str]:
    args = ["experiment", "report", "--experiment-dir", str(directory)]
    if repo is not None:
        args += ["--repo-root", str(repo)]
    code, stdout, stderr = _cli(args)
    return code, stdout + stderr


def _version(out: Path, name: str, version: int = 1) -> Path:
    return out / "runs" / "experiments" / name / f"v{version}"


@dataclass(frozen=True)
class _Base:
    out: Path
    ledger: bytes
    report: str


@pytest.fixture(scope="module")
def base(workspace: T02Workspace, tmp_path_factory: pytest.TempPathFactory) -> _Base:
    """2 × 2 × 2 fold を最後まで通した1回の実行（機構の確認の版 3）。"""
    _reset_ledger(workspace)
    out = tmp_path_factory.mktemp("report-base")
    code, output = _run(workspace, out, "base")
    assert code == 0, output
    return _Base(
        out=out,
        ledger=(workspace.repo / _LEDGER).read_bytes(),
        report=(_version(out, "base") / "report.md").read_text(encoding="utf-8"),
    )


def _repo_copy(workspace: T02Workspace, tmp_path: Path, ledger: bytes) -> Path:
    """登録簿と台帳だけを持つ別のリポジトリの根（レポートが読むのはこの2つだけ）。"""
    repo = tmp_path / "repo"
    (repo / _REGISTRY).parent.mkdir(parents=True)
    shutil.copy(workspace.repo / _REGISTRY, repo / _REGISTRY)
    (repo / _LEDGER).parent.mkdir(parents=True)
    (repo / _LEDGER).write_bytes(ledger)
    return repo


def _copy_base(base: _Base, tmp_path: Path) -> Path:
    """`base` の成果物の基点を丸ごと写す（実験の記録と run・評価の成果物）。"""
    out = tmp_path / "out"
    shutil.copytree(base.out, out)
    return out


def _seeded(base: _Base, tmp_path: Path) -> Path:
    """run と評価の成果物だけを写した基点（実験の記録は写さない）。"""
    out = tmp_path / "seeded"
    (out / "runs").mkdir(parents=True)
    for entry in (base.out / "runs").iterdir():
        if entry.name != "experiments":
            shutil.copytree(entry, out / "runs" / entry.name)
    return out


def _rewrite_outcome(directory: Path, **changes: Any) -> None:
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.search is not None
    search = replace(outcome.search, **changes)
    payload = experiment_outcome_payload(replace(outcome, search=search))
    (directory / "experiment_outcome.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _sections(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## ")]


def _section(text: str, number: int) -> str:
    start = text.index(SEARCH_REPORT_HEADINGS[number - 1])
    if number == len(SEARCH_REPORT_HEADINGS):
        return text[start:]
    return text[start : text.index(SEARCH_REPORT_HEADINGS[number])]


# --- 書式・用途・決定論（#17 の後半）---------------------------------------------------------


def test_17_the_report_has_the_nine_headings_and_starts_with_the_purpose(base: _Base) -> None:
    text = base.report
    assert text.startswith("# 実験レポート: base 版 1\n")
    assert "保存済みの成果物だけから作った表示" in text
    assert _sections(text) == list(SEARCH_REPORT_HEADINGS)
    first = _section(text, 1).splitlines()
    assert first[2] == "用途: 機構の確認（共通基準の判定ではない。最終検証に進めない）"
    assert "- 判定: 機構の確認: " in _section(text, 1)
    assert "（機構確認用）" in _section(text, 1)
    assert "共通基準を満たす" not in text
    assert "合格" not in "".join(_sections(text)) and "採用" not in text


def test_the_same_artifacts_give_the_same_bytes_anywhere(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    """`experiment report` は run が書いたものと同じバイト列を作り（何もしない）、別の場所へ写した
    成果物と台帳からも同じバイト列になる（時刻・絶対パスを入れない。D07 §22.1）。"""
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    out = _copy_base(base, tmp_path)
    directory = _version(out, "base")
    code, output = _report(directory, repo)
    assert code == 0, output
    assert "既存のレポートと内容が同じなので何もしていない" in output
    (directory / "report.md").unlink()
    assert _report(directory, repo)[0] == 0
    assert (directory / "report.md").read_text(encoding="utf-8") == base.report


def test_a_standard_purpose_report_shows_the_current_version_and_turns_older_when_registered(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    """用途が共通基準の判定の版: 1行目と「現行」。登録簿に新しい版が足されると「旧版」になり、
    内容の違うレポートは退避してから書く（D09 §10.11 の2・§11.5）。"""
    out = _seeded(base, tmp_path)
    code, output = _run(workspace, out, "standard", policy_version=6)
    assert code == 0, output
    directory = _version(out, "standard")
    text = (directory / "report.md").read_text(encoding="utf-8")
    assert _section(text, 1).splitlines()[2] == "用途: 共通基準の判定"
    assert "版 6（現行）" in text
    assert "この判定は研究履歴の中の検証区間で共通基準を満たしたかどうかを示すもの" in text

    repo = _repo_copy(workspace, tmp_path, (workspace.repo / _LEDGER).read_bytes())
    entry = (repo / _REGISTRY).read_text(encoding="utf-8").splitlines()[-1]
    (repo / _REGISTRY).write_text(
        (repo / _REGISTRY).read_text(encoding="utf-8")
        + entry.replace("version: 6", "version: 7")
        + "\n",
        encoding="utf-8",
    )
    code, output = _report(directory, repo)
    assert code == 0, output
    assert "退避（report.<n>.md）してから書いた" in output
    assert "版 6（旧版）" in (directory / "report.md").read_text(encoding="utf-8")
    assert (directory / "report.1.md").read_text(encoding="utf-8") == text


# --- 照合 R1〜R7（#22 の読む側）---------------------------------------------------------------


def test_r1_a_search_rejected_by_the_pre_run_checks_reports_that_nothing_was_searched(
    workspace: T02Workspace, tmp_path: Path
) -> None:
    code, output = _run(workspace, tmp_path, "rejected", policy_version=4)
    assert code == 3, output
    text = (_version(tmp_path, "rejected") / "report.md").read_text(encoding="utf-8")
    assert _sections(text) == list(SEARCH_REPORT_HEADINGS)
    verdict = _section(text, 1)
    assert "探索は行っていない（事前検査で止めた）" in verdict
    assert "trial_count_within_limit" in verdict
    assert "事前検査で止めたので台帳に書いていない" in _section(text, 8)
    assert "探索を行っていない" in _section(text, 3)


def test_r2_a_missing_binding_with_start_records_is_a_read_error(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    (directory / "search" / "ledger_execution.json").unlink()
    (directory / "report.md").unlink()
    code, output = _report(directory, repo)
    assert code == 2, output
    assert "R2" in output
    assert not (directory / "report.md").exists()


def test_r3_a_run_stopped_before_the_binding_reports_without_the_counts(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    for name in ("experiment_outcome.json", "report.md"):
        (directory / name).unlink()
    shutil.rmtree(directory / "search")
    code, output = _report(directory, repo)
    assert code == 0, output
    text = (directory / "report.md").read_text(encoding="utf-8")
    assert "この実行と台帳の行を対応付けられない（実行番号の記録が無い）" in _section(text, 8)
    assert "照合できないので出せない" in _section(text, 7)
    assert "未着手" in _section(text, 3) and "`NOT_STARTED`" in _section(text, 3)


@pytest.mark.parametrize("case", ["experiment_id", "execution"])
def test_r4_a_binding_that_disagrees_with_its_generation_is_a_read_error(
    workspace: T02Workspace, base: _Base, tmp_path: Path, case: str
) -> None:
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    path = directory / "search" / "ledger_execution.json"
    if case == "experiment_id":
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["experiment_id"] = digest("another experiment").hex
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        _rewrite_outcome(directory, ledger_execution=2)
    before = (directory / "report.md").read_bytes()
    code, output = _report(directory, repo)
    assert code == 2, output
    assert "R4" in output
    assert (directory / "report.md").read_bytes() == before
    assert not (directory / "report.1.md").exists()


def test_r4_an_unreadable_binding_is_a_read_error(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    (directory / "search" / "ledger_execution.json").write_text("not json", encoding="utf-8")
    assert _report(directory, repo)[0] == 2


def test_r5_an_unreadable_ledger_is_written_in_the_ledger_section(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    repo = _repo_copy(workspace, tmp_path, b"not json\n")
    directory = _version(_copy_base(base, tmp_path), "base")
    code, output = _report(directory, repo)
    assert code == 0, output
    text = (directory / "report.md").read_text(encoding="utf-8")
    assert "台帳が読めない" in _section(text, 8) and "LINE_CORRUPT" in _section(text, 8)


def test_r6_a_ledger_without_the_started_line_reports_without_the_counts(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    repo = _repo_copy(workspace, tmp_path, b"")
    directory = _version(_copy_base(base, tmp_path), "base")
    code, output = _report(directory, repo)
    assert code == 0, output
    text = (directory / "report.md").read_text(encoding="utf-8")
    assert "台帳にこの実行の開始の行が無い" in _section(text, 8)
    assert "- (a)" not in _section(text, 8) and "- (b)" not in _section(text, 8)
    assert "照合できないので出せない" in _section(text, 7)


def test_r7_and_15_the_counts_see_only_the_lines_before_the_started_line(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    """同じ検証区間を見る2つ目の実験は (a) で1件・4試行、(b) で1つ目を判定とともに数える。1つ目の
    レポートは、後から台帳が伸びても 0 件のまま（#15 の後半）。比較の前提は全項目を出す。"""
    out = _seeded(base, tmp_path)
    assert _run(workspace, out, "first")[0] == 0
    first = (_version(out, "first") / "report.md").read_text(encoding="utf-8")
    assert _run(workspace, out, "second")[0] == 0
    second = (_version(out, "second") / "report.md").read_text(encoding="utf-8")
    assert "0 件、試行の累計 0" in _section(first, 8)
    assert "1 件、試行の累計 4" in _section(second, 8)
    assert "| first | 1 | 1 |" in _section(second, 8)
    assert "- この番号の結末の行: あり" in _section(second, 8)
    for name in ("research_policy_ref", "snapshot_ref", "account", "env_digest"):
        assert f"| {name} |" in _section(second, 7)

    code, output = _report(_version(out, "first"), workspace.repo)
    assert code == 0, output
    assert "既存のレポートと内容が同じなので何もしていない" in output

    with (workspace.repo / _LEDGER).open("ab") as stream:
        stream.write(b'{"entry": {"eve')
    assert _report(_version(out, "second"), workspace.repo)[0] == 0
    text = (_version(out, "second") / "report.md").read_text(encoding="utf-8")
    assert "末尾に書きかけの行がある（数えていない。次の追記で切り詰められる）" in text


# --- 途中で止まった実行（#25）-------------------------------------------------------------------


def test_25_an_interrupted_run_shows_unit_and_fold_states_without_a_verdict(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    """fold 0 は検証済み、fold 1 は選定区間の試行 0 の開始記録だけ（中断）で残りは未試行。"""
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    for name in ("experiment_outcome.json", "report.md"):
        (directory / name).unlink()
    search = directory / "search"
    for name in ("trial_units.parquet", "trial_metrics.parquet", "selection_f1.json"):
        (search / name).unlink()
    for path in sorted((search / "units").iterdir()):
        if path.name.startswith("f1_") and path.name != "f1_TRAIN_t0.start.json":
            path.unlink()
    code, output = _report(directory, repo)
    assert code == 0, output
    text = (directory / "report.md").read_text(encoding="utf-8")
    assert _sections(text) == list(SEARCH_REPORT_HEADINGS)
    assert "- 判定: 未確定（途中で止まった）" in _section(text, 1)
    states = _section(text, 3)
    for value in ("`ABORTED`", "`NOT_STARTED`", "`COMPLETED`"):
        assert value in states
    assert "中断（`ABORTED`）" in states and "未試行（`NOT_STARTED`）" in states
    assert "検証済み" in states and "証拠の要件は未確定（頻度区分が決まっていない）" in states
    selected = read_selections(directory)[0].selected_trial_index
    assert f"| f0_VALIDATION_t{selected} |" in states
    assert "MAX_DRAWDOWN_MTM_RATE LE 0.2: " in states
    assert "頻度区分: " not in text
    # 選定記録の無い fold 1（中断）を、指標の計算可否の節で候補なしと取り違えない。
    availability = _section(text, 4)
    assert "- fold 1: 候補なし" not in availability
    assert "- fold 1: 検証区間の単位が終わっていない" in availability
    assert "未確定（途中で止まった）" in _section(text, 6)
    assert "頑健性の表は出さない" in _section(text, 9)
    assert "- この番号の結末の行: あり" in _section(text, 8)


# --- 取引が少ない fold での違反（#16 の後半）----------------------------------------------------


def test_16_a_floor_breached_with_few_trades_is_shown_in_the_verdict_section(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.search is not None
    selected = outcome.search.selections[0].selected_trial_index
    assert selected is not None
    breach = ConditionResult(
        scope=ConditionScope.FOLD_FLOOR,
        fold_index=0,
        metric=MetricId.MAX_DRAWDOWN_MTM_RATE,
        statistic=None,
        comparator=Comparator.LE,
        threshold=Decimal("0.2"),
        observed=Decimal("0.5"),
        outcome=ConditionOutcome.NOT_MET,
        unavailable_reason=None,
    )
    others = tuple(item for item in outcome.search.condition_results if item.fold_index != 0)
    shortfall = SufficiencyShortfall(
        kind=SufficiencyShortfallKind.FLOOR_NOT_MET_TRADES_BELOW,
        fold_index=0,
        trial_index=selected,
        metric=None,
        reason=None,
        required=5,
        observed=0,
    )
    rest = tuple(item for item in outcome.search.shortfalls if item.fold_index != 0)
    _rewrite_outcome(
        directory,
        condition_results=(breach, *others),
        shortfalls=tuple(sorted((shortfall, *rest), key=lambda item: item.key)),
    )
    code, output = _report(directory, repo)
    assert code == 0, output
    text = (directory / "report.md").read_text(encoding="utf-8")
    verdict = _section(text, 1)
    assert "最低条件を割った fold がある（取引が少ないため判定は証拠不足）" in verdict
    assert "| 0 | MAX_DRAWDOWN_MTM_RATE | 0.5 | LE 0.2 | 0 | 5 |" in verdict
    assert "取引が少ないため証拠不足として扱った" in _section(text, 2)
    assert "証拠不足（最低条件を割った）" in _section(text, 3)


# --- 登録簿（#26）・退避中の印・事後検査 ----------------------------------------------------------


def _registry_missing(path: Path) -> None:
    path.unlink()


def _registry_broken(path: Path) -> None:
    path.write_text("entries: [\n", encoding="utf-8")


def _registry_without_version(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(line for line in lines if "version: 3," not in line) + "\n", encoding="utf-8"
    )


def _registry_other_digest(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lines = [
        line.replace(line.split("digest: '")[1][:64], "0" * 64) if "version: 3," in line else line
        for line in text.splitlines()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _registry_other_purpose(path: Path) -> None:
    """版 3 の要素の用途だけを書き換える（ダイジェストは据え置き。D09 §10.9 の照合 (5)）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = [
        line.replace("MECHANISM_CHECK", "STANDARD") if "version: 3," in line else line
        for line in lines
    ]
    assert changed != lines
    path.write_text("\n".join(changed) + "\n", encoding="utf-8")


_REGISTRY_DAMAGE = {
    "missing": _registry_missing,
    "other_purpose": _registry_other_purpose,
    "broken": _registry_broken,
    "no_element": _registry_without_version,
    "other_digest": _registry_other_digest,
}


@pytest.mark.parametrize("case", sorted(_REGISTRY_DAMAGE))
def test_26_an_unreadable_registry_stops_the_report_with_exit_code_2(
    workspace: T02Workspace, base: _Base, tmp_path: Path, case: str
) -> None:
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    _REGISTRY_DAMAGE[case](repo / _REGISTRY)
    directory = _version(_copy_base(base, tmp_path), "base")
    (directory / "report.md").write_text("an earlier report\n", encoding="utf-8")
    code, output = _report(directory, repo)
    assert code == 2, output
    assert (directory / "report.md").read_text(encoding="utf-8") == "an earlier report\n"
    assert not (directory / "report.1.md").exists()


def test_26_the_run_ends_with_exit_code_1_when_the_registry_cannot_be_read_at_the_end(
    workspace: T02Workspace, base: _Base, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """実行の最後のレポートで登録簿が読めなければ、結末記録と台帳の行はそのままでレポートを
    書かず、成果の表示の後に終了コード 1（D09 §11.4。Q37）。"""

    def unreadable(path: Path) -> Any:
        raise ConfigError("the registry vanished during the run")

    monkeypatch.setattr(composition, "load_research_policy_registry", unreadable)
    out = _seeded(base, tmp_path)
    code, output = _run(workspace, out, "late_registry")
    assert code == 1, output
    assert "実験の結末: COMPLETED" in output
    assert "the registry vanished during the run" in output
    directory = _version(out, "late_registry")
    assert read_experiment_outcome(directory) is not None
    assert not (directory / "report.md").exists()
    events = [
        json.loads(line)["entry"]["event"]
        for line in (workspace.repo / _LEDGER).read_text(encoding="utf-8").splitlines()
    ]
    assert events == ["STARTED", "FINISHED"]


def test_a_retreat_marker_stops_the_report_with_exit_code_2(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    (directory / "retreat_in_progress.json").write_text(
        json.dumps({"schema_version": 1, "n": 1}), encoding="utf-8"
    )
    before = (directory / "report.md").read_bytes()
    code, output = _report(directory, repo)
    assert code == 2, output
    assert "退避の途中で止まった。同じ版を `experiment run` で再実行すると回復する" in output
    assert (directory / "report.md").read_bytes() == before


def test_a_unit_failing_a_post_run_check_is_listed_first(
    workspace: T02Workspace, base: _Base, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = research_policy.check_evaluation_rule

    def failing(expected: int, observed: int) -> Any:
        return original(expected, observed + 1)

    monkeypatch.setattr(run_experiment, "check_evaluation_rule", failing)
    out = _seeded(base, tmp_path)
    code, output = _run(workspace, out, "postrun")
    assert code == 4, output
    text = (_version(out, "postrun") / "report.md").read_text(encoding="utf-8")
    verdict = _section(text, 1)
    assert "記録の検査（事後検査）に合格でない単位がある" in verdict
    assert "| f0_TRAIN_t0 | evaluation_rule_matches | FAILED |" in verdict


def test_an_unreadable_aggregate_table_is_written_as_unreadable_not_raised(
    workspace: T02Workspace, base: _Base, tmp_path: Path
) -> None:
    """集約表が読めなければ、例外にせず該当する欄に「読めない（理由）」と書く（終了コード 0）。"""
    repo = _repo_copy(workspace, tmp_path, base.ledger)
    directory = _version(_copy_base(base, tmp_path), "base")
    (directory / "search" / "trial_metrics.parquet").write_bytes(b"not a parquet file")
    code, output = _report(directory, repo)
    assert code == 0, output
    text = (directory / "report.md").read_text(encoding="utf-8")
    assert "読めない（" in _section(text, 3)
    assert str(tmp_path) not in text


# --- reproduce の拒否 --------------------------------------------------------------------------


@pytest.mark.parametrize("stopped", [False, True])
def test_reproduce_refuses_a_search_experiment(
    workspace: T02Workspace, base: _Base, tmp_path: Path, stopped: bool
) -> None:
    """探索の実験の別プロセスでの再現は段階5 の対象外（D09 §15）。結末記録の有無によらず終了
    コード 2 で、reproduction.json を書かない。"""
    directory = _version(_copy_base(base, tmp_path), "base")
    if stopped:
        (directory / "experiment_outcome.json").unlink()
    target = tmp_path / "reproduced"
    code, stdout, stderr = _cli(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(directory),
            "--snapshots",
            str(workspace.repo / "data/snapshots"),
            "--out",
            str(target),
            "--repo-root",
            str(workspace.repo),
        ]
    )
    assert code == 2, stdout + stderr
    assert "段階5 の対象外" in stderr
    assert not (target / "reproduction.json").exists()
