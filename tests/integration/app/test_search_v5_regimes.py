"""研究ポリシー第 5 版の局面と表示の結合テスト（人工データ。D24 §5・§9.3、実装 PR 4-3）。

T02 の人工データを受け入れた作業場に候補の組の snapshot を足し（候補の組の snapshot は評価範囲の
後ろの週が封印期間の partition。`t02_workspace.accept_candidate`）、試験用の研究ポリシー版 5
（2 fold。局面の窓を人工データに合わせて短くした仮の値）を指す探索の実験を 1 試行で走らせる。

確かめること:

- 組ごとの局面の成果物 `search/regimes_<組>.json` が書かれ、結末記録はそのダイジェストだけを持つ
  （仮置き 15）。局面の損益の和は経路の純損益と一致する（RP5-14）。
- 得意な局面の宣言（`favored_regimes`）は記録票に入り、局面の成果物に確かめが出る（決定 141）。
- レポートの順 9「診断」に小見出し「局面（判定に使わない）」が出て、判定の節（順 1〜8）には局面が
  出ない（決定 165）。
- 日足の読み出しは封印・隔離の partition を読まない（読もうとすると足を読む前に止まる。RP5-15）。
- 局面の成果物を壊しても・局面が出せなくても、判定の値は変わらない（RP5-16）。

人工データだけを使う（実価格・成績の値は確かめない。件数・識別子・構造だけを見る）。
"""

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.app.config.experiment_v2 import ExperimentV2, load_experiment_v2
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.adapters.fs_store import (
    read_experiment_manifest,
    read_experiment_outcome,
    read_path_records,
    read_regime_report,
    search_directory,
)
from odyssey_fx.evaluation.adapters.search_report import build_search_report
from odyssey_fx.evaluation.application.ports import DailyBarsUnavailable
from odyssey_fx.evaluation.domain.metrics import METRIC_SET_VERSIONS
from odyssey_fx.evaluation.domain.regimes import RegimeLabel, RegimeReportStatus
from odyssey_fx.evaluation.domain.variant import VARIANTS, Variant
from odyssey_fx.marketdata.domain.errors import HoldoutAccessViolation
from odyssey_fx.strategy.catalog.initial import INITIAL_CATALOG
from tests.fixtures.acceptance.t02_workspace import T02Workspace, accept_candidate, build_workspace
from tests.fixtures.evaluation.research_policies import (
    append_registry_entry,
    policy_v3_text,
    write_policy,
)
from tests.fixtures.evaluation.search_experiments import TWO_FOLD_SPLIT, write_search_experiment

#: 1 試行（両軸とも値 1 つ）。両 fold で同じ試行が選ばれ、経路は 1 区間になる。
ONE_TRIAL: Final = (
    "    - {instance: m15_ema, parameter: period, values: [10]}\n"
    "    - {instance: stop_level, parameter: lookback, values: [10]}"
)

#: 人工データ（約 3 週間の日足）で局面が付くように窓を短くした試験用の局面の値（研究ポリシーでは
#: ない。D24 §5.1 の値は本数と倍率だけなので、短い窓でも同じ式を通る）。
SHORT_REGIMES: Final = (
    "  regimes:\n"
    "    role: DISPLAY_ONLY\n"
    '    timeframe: "1d"\n'
    '    direction: {ema_bars: 2, slope_bars: 1, atr_bars: 2, dead_band_atr_multiple: "0.5"}\n'
    "    volatility: {atr_bars: 2, median_window_bars: 3}\n"
    "    label_at: LATEST_AVAILABLE_DAILY_BAR_AT_ENTRY"
)

FAVORED: Final = (RegimeLabel.UP_HIGH, RegimeLabel.UP_LOW)


@dataclass(frozen=True)
class _Setup:
    workspace: T02Workspace
    experiment: Path
    out: Path
    code: int
    output: str


def _call(argv: list[str]) -> tuple[int, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, stdout.getvalue() + stderr.getvalue()


def _run(workspace: T02Workspace, experiment: Path, out: Path) -> tuple[int, str]:
    return _call(
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


@pytest.fixture(scope="module")
def setup(tmp_path_factory: pytest.TempPathFactory) -> _Setup:
    root = tmp_path_factory.mktemp("search-v5-regimes")
    workspace = build_workspace(root)
    candidate = accept_candidate(workspace, root / "candidate")
    text = policy_v3_text(version=5, replace={"split": TWO_FOLD_SPLIT, "regimes": SHORT_REGIMES})
    write_policy(workspace.repo, "research_policy", 5, text)
    append_registry_entry(workspace.repo, "research_policy", 5)
    ledger = workspace.repo / "research" / "trial_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_bytes(b"")
    experiment = write_search_experiment(
        workspace.repo,
        "v5_regimes.yaml",
        experiment_id="v5_regimes",
        policy_version=5,
        axes=ONE_TRIAL,
        max_trials=1,
        candidate_snapshot=candidate,
    )
    experiment.write_text(
        experiment.read_text(encoding="utf-8") + "favored_regimes: [UP_HIGH, UP_LOW]\n",
        encoding="utf-8",
    )
    out = root / "out"
    code, output = _run(workspace, experiment, out)
    return _Setup(workspace=workspace, experiment=experiment, out=out, code=code, output=output)


def _directory(setup: _Setup) -> Path:
    return setup.out / "runs/experiments/v5_regimes/v1"


def test_the_regime_artifacts_are_written_and_the_outcome_holds_their_digests(
    setup: _Setup,
) -> None:
    assert setup.code == 0, setup.output
    directory = _directory(setup)
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.search is not None
    assert [item.variant for item in outcome.regimes] == list(VARIANTS)
    for item in outcome.regimes:
        assert (search_directory(directory) / f"regimes_{item.variant.value}.json").is_file()
        report = read_regime_report(directory, item.variant)
        assert report is not None
        assert digest(report) == item.digest
    # 結末記録は局面の中身を持たない（ダイジェストだけ）。
    saved = json.loads((directory / "experiment_outcome.json").read_text(encoding="utf-8"))
    assert set(saved["regimes"][0]) == {"variant", "digest"}


def test_rp5_14_the_regime_rows_add_up_to_the_path_net(setup: _Setup) -> None:
    directory = _directory(setup)
    outcome = read_experiment_outcome(directory)
    assert outcome is not None and outcome.search is not None
    for variant in VARIANTS:
        report = read_regime_report(directory, variant)
        assert report is not None
        assert report.status is RegimeReportStatus.AVAILABLE, report.reason
        path = outcome.search.variants.of(variant).path
        assert path is not None and path.final_balance is not None
        initial = path.segments[0].initial_balance
        assert report.path_net == path.final_balance - initial
        assert report.regime_net == report.path_net
        # 取引の件数は、区間の run の完了取引と区間の終わりの仮決済の和。
        _, ends = read_path_records(directory, variant)
        expected = sum((end.trade_count or 0) + len(end.settlement_lines) for end in ends.values())
        assert len(report.trades) == expected
        assert any(day.label is not RegimeLabel.UNLABELED for day in report.days)
        assert report.daily_series == "USDJPY/1d_ny17/bid"


def test_the_favored_regimes_are_recorded_and_checked(setup: _Setup) -> None:
    """宣言は記録票の実験設定の本文（識別の入力）にあり、局面の成果物に写って確かめが出る。"""
    directory = _directory(setup)
    assert (
        "favored_regimes: [UP_HIGH, UP_LOW]"
        in read_experiment_manifest(directory).file("experiment").text
    )
    for variant in VARIANTS:
        report = read_regime_report(directory, variant)
        assert report is not None and report.favored is not None
        assert report.favored_regimes == report.favored.declared == FAVORED


def test_the_report_shows_the_regimes_only_in_the_diagnosis(setup: _Setup) -> None:
    text = (_directory(setup) / "report.md").read_text(encoding="utf-8")
    judged, diagnosis = text.split("## 9. 診断（判定に使わない）")
    assert "局面" not in judged
    assert diagnosis.count("#### 局面（判定に使わない）") == 2
    for expected in (
        "局面は判定に使わない。局面の定義は研究ポリシーの `regimes` の値で、結果を見て変えない",
        "局面ごとの損益",
        "局面の時間の割合",
        "得意な局面の確かめ（実験設定の `favored_regimes`",
        "上昇・値動き大（UP_HIGH）",
    ):
        assert expected in diagnosis, expected


def _loaded(setup: _Setup) -> ExperimentV2:
    return load_experiment_v2(
        setup.experiment,
        repo_root=setup.workspace.repo,
        registry=INITIAL_CATALOG,
        metric_set_versions=METRIC_SET_VERSIONS,
    )


def _daily_source(setup: _Setup) -> composition._SearchDailyBars:
    loaded = _loaded(setup)
    parts = composition._prepare_search(
        loaded=loaded,
        snapshots_root=setup.workspace.repo / "data/snapshots",
        repo_root=setup.workspace.repo,
        registry=INITIAL_CATALOG,
    )
    runner, _ = composition._search_wiring(loaded, parts, setup.out)
    return composition._SearchDailyBars(
        loaded=loaded, parts=parts, publication_logs=runner.publication_logs, timeframe_id="1d_ny17"
    )


def test_rp5_15_reading_daily_bars_into_the_sealed_partitions_stops_before_reading(
    setup: _Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候補の組の snapshot は `2015-01-18T22:00Z` から封印期間。そこまでの要求は読む前に止まる。"""
    source = _daily_source(setup)
    inside = source.daily_bars(Variant.CANDIDATE, UtcTime.parse("2015-01-16T22:00:00Z"))
    assert not isinstance(inside, DailyBarsUnavailable)
    assert inside and all(bar.bar_start < UtcTime.parse("2015-01-16T22:00:00Z") for bar in inside)

    def no_read(*args: object, **kwargs: object) -> object:
        raise AssertionError("the daily bars were read before the access check")

    monkeypatch.setattr(composition, "readable_surface", no_read)
    monkeypatch.setattr(composition.SnapshotInputs, "verified_bars", no_read)
    with pytest.raises(HoldoutAccessViolation, match="outside the research history"):
        source.daily_bars(Variant.CANDIDATE, UtcTime.parse("2015-01-21T22:00:00Z"))


def test_rp5_16_unavailable_regimes_do_not_change_the_judgement(
    setup: _Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """局面が出せない実行でも、組の判定・2 軸・保留の印・経路の要約は同じ（決定 134）。"""
    original = read_experiment_outcome(_directory(setup))
    assert original is not None and original.search is not None
    copied = tmp_path / "workspace"
    shutil.copytree(setup.workspace.repo, copied)
    (copied / "research/trial_ledger.jsonl").write_bytes(b"")
    monkeypatch.setattr(
        composition._SearchDailyBars,
        "daily_bars",
        lambda self, variant, until: DailyBarsUnavailable("試験のために読めなくした"),
    )
    workspace = T02Workspace(repo=copied, snapshot_id=setup.workspace.snapshot_id)
    out = tmp_path / "out"
    code, output = _run(workspace, copied / "configs/experiments/v5_regimes.yaml", out)
    assert code == 0, output
    directory = out / "runs/experiments/v5_regimes/v1"
    rerun = read_experiment_outcome(directory)
    assert rerun is not None and rerun.search is not None
    assert rerun.search.variants == original.search.variants
    assert rerun.search.combined == original.search.combined
    report = read_regime_report(directory, Variant.STANDARD)
    assert report is not None and report.status is RegimeReportStatus.UNAVAILABLE
    assert "試験のために読めなくした" in (report.reason or "")


def test_rp5_16_a_broken_regime_artifact_changes_only_the_regime_subsection(
    setup: _Setup, tmp_path: Path
) -> None:
    """局面の成果物を書き換えると、局面の小見出しはダイジェストの不一致を出し、判定の節は同じ。"""
    copied = tmp_path / "copied"
    shutil.copytree(setup.out, copied)
    directory = copied / "runs/experiments/v5_regimes/v1"
    registry = composition.search_registry(
        setup.workspace.repo, read_experiment_manifest(directory)
    )
    before = build_search_report(
        directory, copied, repo_root=setup.workspace.repo, registry=registry
    )
    path = search_directory(directory) / "regimes_standard.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["days"] = payload["days"][1:]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    after = build_search_report(
        directory, copied, repo_root=setup.workspace.repo, registry=registry
    )
    head_before, _ = before.split("## 9. 診断（判定に使わない）")
    head_after, diagnosis = after.split("## 9. 診断（判定に使わない）")
    assert head_after == head_before
    assert "局面の成果物が結末記録のダイジェストと一致しない" in diagnosis


def test_a_search_with_favored_regimes_is_reproduced(setup: _Setup, tmp_path: Path) -> None:
    """得意な局面の宣言を持つ記録票も、記録票の本文から組み立て直して照合できる（D07 §21.2）。"""
    out = tmp_path / "reproduce"
    code, output = _call(
        [
            "experiment",
            "reproduce",
            "--experiment-dir",
            str(_directory(setup)),
            "--snapshots",
            str(setup.workspace.repo / "data/snapshots"),
            "--out",
            str(out),
            "--repo-root",
            str(setup.workspace.repo),
        ]
    )
    assert code == 0, output
    report = json.loads((out / "reproduction.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "REPRODUCED"
