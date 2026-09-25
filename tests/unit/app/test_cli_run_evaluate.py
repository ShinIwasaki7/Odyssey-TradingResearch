"""`run` と `evaluate` の引数解析と失敗の出方（D03 §10、ADR-0028）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import build_parser, main
from odyssey_fx.evaluation.application.manifest import METRIC_SET_VERSION

#: 評価が受け取る取引カレンダー（D07 §4.1 v2.0 の `--calendar`）。
CALENDAR = str(Path(__file__).resolve().parents[3] / "configs/calendars/fx_ny17_v1.yaml")


def test_the_top_level_commands_are_the_five_of_the_design() -> None:
    """市場データの3コマンドに実行と評価の2コマンドを足した5コマンド。"""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
    for argv in (["data", "accept"], ["run"], ["evaluate"]):
        with pytest.raises(SystemExit):
            # 必須の引数を欠いた呼び出しは argparse が止める。
            parser.parse_args(argv)


def test_the_run_command_takes_the_configuration_and_the_roots() -> None:
    """実験設定・カレンダー・時間足・銘柄仕様・snapshot の位置を受ける。"""
    args = build_parser().parse_args(
        [
            "run",
            "--experiment",
            "configs/experiments/strategy_a_t01.yaml",
            "--calendar",
            "configs/calendars/fx_ny17_v1.yaml",
            "--timeframes",
            "configs/calendars/timeframes_v1.yaml",
            "--symbols",
            "configs/symbols",
            "--snapshots",
            "data/snapshots",
        ]
    )
    assert args.command == "run"
    assert args.experiment == Path("configs/experiments/strategy_a_t01.yaml")
    assert args.snapshots == Path("data/snapshots")
    # 成果物の基点とリポジトリの位置は既定で現在のディレクトリ。
    assert args.out == Path(".")
    assert args.repo_root == Path(".")
    # 既存の成果物は既定では置き換えない（ADR-0006）。
    assert args.replace is False


def test_the_evaluate_command_takes_the_run_identifier_and_the_calendar() -> None:
    """評価は実行の識別子と取引カレンダーを受け、市場データの設定を要らない（D07 §4.1）。

    カレンダーは v2.0 で必須の引数になった（Q8 決定。年率化と日次の資産系列を取引日で
    数えるため）。run manifest は識別と版しか持たないので、本文を呼び出し側が渡す。
    """
    args = build_parser().parse_args(["evaluate", "--run", "a" * 64, "--calendar", CALENDAR])
    assert args.command == "evaluate"
    assert args.run == "a" * 64
    assert args.calendar == Path(CALENDAR)
    assert args.out == Path(".")
    assert args.metric_set_version == METRIC_SET_VERSION


def test_the_evaluate_command_accepts_another_metric_set_version() -> None:
    """指標集合の版を変えて評価し直せる（D07 §9.2）。"""
    args = build_parser().parse_args(
        ["evaluate", "--run", "a" * 64, "--calendar", CALENDAR, "--metric-set-version", "1"]
    )
    assert args.metric_set_version == 1


def test_the_evaluate_command_requires_the_calendar() -> None:
    """カレンダーを渡さない評価は argparse が止める（D07 §4.1 v2.0 の渡し方 (a)）。"""
    with pytest.raises(SystemExit):
        build_parser().parse_args(["evaluate", "--run", "a" * 64])


def test_an_identifier_that_is_not_a_digest_fails_with_exit_code_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実行の識別子が16進64文字でなければ、行き先の分かる1行で終える（D03 §10）。"""
    assert main(["evaluate", "--run", "not-a-digest", "--calendar", CALENDAR]) == 1
    assert "16進64文字" in capsys.readouterr().err


def test_evaluating_a_run_that_was_never_saved_fails_with_exit_code_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """保存されていない run の評価は失敗として終える（想定していない失敗にしない）。"""
    argv = ["evaluate", "--run", "a" * 64, "--calendar", CALENDAR, "--out", str(tmp_path)]
    assert main(argv) == 1
    assert "result.json" in capsys.readouterr().err


def test_running_with_a_missing_experiment_file_fails_with_exit_code_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """設定ファイルが無ければ、どのファイルが無いかを示して終える（D01 §10.1）。"""
    code = main(
        [
            "run",
            "--experiment",
            str(tmp_path / "missing.yaml"),
            "--calendar",
            str(tmp_path / "calendar.yaml"),
            "--timeframes",
            str(tmp_path / "timeframes.yaml"),
            "--symbols",
            str(tmp_path),
            "--snapshots",
            str(tmp_path),
        ]
    )
    assert code == 1
    assert "設定ファイルが見つからない" in capsys.readouterr().err


def test_an_unimplemented_metric_set_version_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """式のある版だけを受ける（D07 §5.5・§9.2）。

    版の番号だけを変えても評価は現在の版の式で走るので、通してしまうと「別の指標集合で
    作った」と名乗る成果物ができる。実装が持つ指標集合は最新の1版だけである。
    """
    code = main(
        [
            "evaluate",
            "--run",
            "a" * 64,
            "--calendar",
            CALENDAR,
            "--out",
            str(tmp_path),
            "--metric-set-version",
            str(METRIC_SET_VERSION + 1),
        ]
    )
    assert code == 1
    assert "式は無い" in capsys.readouterr().err


# --- 書式 v2（D07 §18.2・§18.5）---------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_the_run_command_takes_a_v2_experiment_without_the_environment_options() -> None:
    """書式 v2 は環境の3つを実験設定の `environment` で指すので、引数では要らない。"""
    args = build_parser().parse_args(
        [
            "run",
            "--experiment",
            "configs/experiments/strategy_b_t02_d1_2s.yaml",
            "--snapshots",
            "data/snapshots",
        ]
    )
    assert args.calendar is None
    assert args.timeframes is None
    assert args.symbols is None


def test_a_v2_experiment_with_the_environment_options_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """書式 v2 に環境の引数も渡すと、同じものを2か所で指すことになるので拒否する。"""
    code = main(
        [
            "run",
            "--experiment",
            str(_REPO_ROOT / "configs/experiments/strategy_b_t02_d1_2s.yaml"),
            "--calendar",
            str(_REPO_ROOT / "configs/calendars/fx_ny17_v1.yaml"),
            "--snapshots",
            str(tmp_path),
            "--repo-root",
            str(_REPO_ROOT),
        ]
    )
    assert code == 1
    assert "--calendar" in capsys.readouterr().err


def test_a_v1_experiment_without_the_environment_options_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """書式 v1 は環境の3つを引数で受ける（v1 の読込は変えない。D07 §18.5）。"""
    code = main(
        [
            "run",
            "--experiment",
            str(_REPO_ROOT / "configs/experiments/strategy_a_t01.yaml"),
            "--snapshots",
            str(tmp_path),
        ]
    )
    assert code == 1
    err = capsys.readouterr().err
    assert "--calendar" in err
    assert "--symbols" in err


def test_an_unknown_experiment_schema_version_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`schema_version` が 1・2 以外の実験設定は拒否する（D07 §18.6 の1）。"""
    path = tmp_path / "experiment.yaml"
    path.write_text("schema_version: 3\nid: x\n", encoding="utf-8")
    code = main(["run", "--experiment", str(path), "--snapshots", str(tmp_path)])
    assert code == 1
    assert "schema_version" in capsys.readouterr().err
