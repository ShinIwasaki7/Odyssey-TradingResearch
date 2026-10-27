"""研究の画面（tools/viewer）のテスト。

実験の版のディレクトリは手書きの合成データで作る（実データ・実際の成績には触れない）。記録票は
実行のときの実験設定と戦略宣言の全文（`resolved_files`）だけを持つ最小の形にする。レポートは
`search_report.py` と同じ見出しの並びで、値は作り物。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tools.viewer import research_viewer as rv
from tools.viewer.markdown_html import convert, inline, split_cells

STRATEGY_V1 = """# 合成の戦略。日足の上昇局面で 1 時間足の
# 移動平均を上抜けたら買う。
#
# - 部品は `ema` と `breakout_trigger`。
#   続きの行。

schema_version: 1
strategy_id: "synthetic_cross"
version: 1
components:
  - instance_id: "h1_ema"
    component: "ema"
    component_version: 1
    inputs:
      prices:
        - "market:USDJPY/1h/bid:CLOSE"
    parameters:
      period: {type: "INT", value: 20}
      window_bars: {type: "INT", value: 150}
    triggers:
      - {name: "h1", when: "bar_close", series: "USDJPY/1h/bid"}
  - instance_id: "take_profit"
    component: "fixed_rr_take_profit"
    component_version: 1
    inputs:
      position:
        - "runtime:POSITION"
    parameters:
      reward_risk: {type: "FLOAT", value: 1.5}
    triggers:
      - {name: "filled", when: "runtime_event", event: "POSITION_OPENED"}
roles:
  trigger: "h1_ema.value"
  exit: "take_profit.action"
"""


def experiment_text(version: int, periods: list[int], lineage: bool = False) -> str:
    lineage_text = (
        "lineage:\n"
        '  derived_from: {experiment_id: "abc123def4567890", execution: 1}\n'
        "  change_kinds: [PARAMETERS]\n"
        '  note: "期間の軸を上側へ動かす <理由>"\n'
        if lineage
        else ""
    )
    return (
        f"# 合成の実験設定 版 {version}\n"
        "schema_version: 2\n"
        "id: synthetic_cross\n"
        f"version: {version}\n"
        f"{lineage_text}"
        'hypothesis: "合成の仮説の文"\n'
        "research_policy: {id: research_policy, version: 3}\n"
        'strategy: "configs/strategies/synthetic_cross_v1.yaml"\n'
        'execution_series: "USDJPY/15m/bid"\n'
        'account: {currency: JPY, initial_balance: "1000000"}\n'
        "search_plan:\n"
        "  kind: GRID\n"
        "  max_trials: 4\n"
        "  axes:\n"
        f"    - {{instance: h1_ema, parameter: period, values: {periods}}}\n"
        "    - {instance: take_profit, parameter: reward_risk, values: [1.5, 3.0]}\n"
        "split: STANDARD\n"
        "final_holdout: NONE\n"
    )


REPORT = """# 実験レポート: synthetic_cross 版 1

## 1. 判定

用途: 共通基準の判定

| 組 | 判定 | 収益性 |
|---|---|---|
| 標準 | 共通基準を満たさない | 満たさない |
| 候補 | 共通基準を満たさない | 満たさない |

### 標準の組（standard）

- 判定: 共通基準を満たさない
  - 理由: `NET_RETURN_RATE` が条件を割った
- 証拠: 十分

## 3. fold ごとの成績

| fold | 純収益率 | 取引件数 | 注記 |
|---|---|---|---|
| 0 | -0.012345 | 12 | a \\| b |
| 1 | 0.034567 | 9 | x<y |
"""


def write_run(root: Path, version: int, periods: list[int], lineage: bool = False) -> Path:
    directory = root / "runs" / "experiments" / "synthetic_cross" / f"v{version}"
    directory.mkdir(parents=True)
    manifest = {
        "experiment_id": f"{version}" * 64,
        "experiment_name": "synthetic_cross",
        "experiment_version": version,
        "git_commit": "e521f56568da33aabfaa2d84bad4070b429d8d18",
        "resolved_files": [
            {"role": "experiment", "text": experiment_text(version, periods, lineage)},
            {"role": "strategy", "text": STRATEGY_V1},
        ],
    }
    (directory / "experiment_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    outcome = {"status": "COMPLETED", "search": {"combined": {"verdict": "BELOW_STANDARD"}}}
    (directory / "experiment_outcome.json").write_text(json.dumps(outcome), encoding="utf-8")
    (directory / "report.md").write_text(REPORT, encoding="utf-8")
    (directory / "exit_code.txt").write_text("0\n", encoding="utf-8")
    (directory / "started_at_utc.txt").write_text("2026-10-26T15:04:38Z\n", encoding="utf-8")
    (directory / "time.log").write_text(
        "      5200000000  maximum resident set size\n", encoding="utf-8"
    )
    return directory


def write_registered(repo: Path, version: int, periods: list[int]) -> None:
    configs = repo / "configs" / "experiments"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / f"synthetic_cross_v{version}.yaml").write_text(
        experiment_text(version, periods, lineage=True), encoding="utf-8"
    )
    strategies = repo / "configs" / "strategies"
    strategies.mkdir(parents=True, exist_ok=True)
    (strategies / "synthetic_cross_v1.yaml").write_text(STRATEGY_V1, encoding="utf-8")


def make_fixture(root: Path) -> None:
    """版 1 は実行済み、版 2 は事前登録だけ（未実行）。"""
    write_run(root, 1, [20, 50])
    write_registered(root, 1, [20, 50])
    write_registered(root, 2, [50, 75])


# --- Markdown の変換 ---------------------------------------------------------------


def test_split_cells_unescapes_pipe_and_backslash() -> None:
    assert split_cells("| a \\| b | c\\\\d |") == ["a | b", "c\\d"]


def test_inline_escapes_html_and_keeps_code_literal() -> None:
    assert inline("x<y **太字** `**a**<b>`") == (
        "x&lt;y <strong>太字</strong> <code>**a**&lt;b&gt;</code>"
    )


def test_convert_splits_sections_and_builds_tables_and_nested_lists() -> None:
    document = convert(REPORT)
    assert document.title == "実験レポート: synthetic_cross 版 1"
    assert [section.title for section in document.sections] == ["1. 判定", "3. fold ごとの成績"]
    first = document.sections[0].html
    assert "<table>" in first and "<th>判定</th>" in first
    assert "<ul><li>判定: 共通基準を満たさない<ul><li>理由:" in first
    assert "</li></ul></li><li>証拠: 十分</li></ul>" in first
    folds = document.sections[1].html
    assert '<td class="num">-0.012345</td>' in folds
    assert "<td>a | b</td>" in folds
    assert "<td>x&lt;y</td>" in folds


def test_interval_columns_show_dates_only() -> None:
    document = convert(
        "## 節\n\n| fold | 選定区間 | 検証区間 | 注記 |\n|---|---|---|---|\n"
        "| 0 | [2017-03-25T12:00:00Z, 2019-03-23T12:00:00Z) | "
        "[2019-03-23T12:00:00Z, 2020-03-21T12:00:00Z) | [2017-03-25T12:00:00Z, x) |\n"
        "| 1 | 対象外 | なし | |\n"
        "\n| 単位 | 実行区間 |\n|---|---|\n| f0 | [2017-01-01T00:00:00Z, 2017-02-01T00:00:00Z) |\n"
    )
    body = document.sections[0].html
    assert "<td>2017-01-01 〜 2017-02-01</td>" in body
    assert "<td>2017-03-25 〜 2019-03-23</td>" in body
    assert "<td>2019-03-23 〜 2020-03-21</td>" in body
    assert "T12:00:00Z, x)" in body  # 対象の列でなければ変えない
    assert "<td>対象外</td>" in body  # 区間の形でなければ変えない


def test_convert_keeps_unknown_syntax_as_text() -> None:
    document = convert("## 節\n\n> 引用\n1. 番号\n")
    assert "&gt; 引用<br>1. 番号" in document.sections[0].html


# --- 集める・画面 -------------------------------------------------------------------


def test_collect_reads_run_and_registered_versions(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    experiments = rv.collect(tmp_path, tmp_path)
    assert [exp.name for exp in experiments] == ["synthetic_cross"]
    versions = experiments[0].versions
    assert [(view.version, view.status) for view in versions] == [(1, "done"), (2, "registered")]
    assert versions[0].verdict == "BELOW_STANDARD"
    assert versions[0].facts.max_memory_bytes == 5200000000
    assert versions[1].report is None


def test_unrelated_configs_are_not_listed(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    other = tmp_path / "configs" / "experiments" / "other.yaml"
    other.write_text(experiment_text(1, [5]).replace("synthetic_cross", "other"), encoding="utf-8")
    assert [exp.name for exp in rv.collect(tmp_path, tmp_path)] == ["synthetic_cross"]


def test_stopped_run_is_marked(tmp_path: Path) -> None:
    directory = write_run(tmp_path, 1, [20, 50])
    (directory / "experiment_outcome.json").unlink()
    (directory / "exit_code.txt").write_text("1\n", encoding="utf-8")
    view = rv.collect(tmp_path, tmp_path)[0].versions[0]
    assert view.status == "stopped" and view.verdict is None
    assert "終了コード 1" in view.status_label


def test_strategy_html_shows_comment_components_and_axes(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    view = rv.collect(tmp_path, tmp_path)[0].versions[0]
    page = rv.strategy_html(view)
    assert "合成の戦略。日足の上昇局面で 1 時間足の移動平均を上抜けたら買う。" in page
    assert "<li>部品は <code>ema</code> と <code>breakout_trigger</code>。続きの行。</li>" in page
    assert "1 時間足の終値（USDJPY bid）" in page
    assert "探索 20 / 50" in page
    assert "保有中のポジション" in page


def test_conditions_are_shown_in_japanese_with_units() -> None:
    experiment = {
        "execution_series": "USDJPY/15m/bid",
        "account": {"account_id": "ACC1", "currency": "JPY", "initial_balance": "1000000"},
        "risk_policy": {"trial_risk_rate": "0.02", "account_risk_cap": "0.20"},
        "cost_model": {
            "commission_per_unit": "0.001",
            "entry_slippage": "0.01",
            "close_slippage": "0.01",
            "spread_offset": "0.02",
        },
        "execution_policy": {
            "entry_delay_bars": 0,
            "entry_valid_for": "PT20M",
            "close_valid_for": "PT20M",
            "adverse_fill_limits": {"USDJPY": "0.05"},
            "reference_quote_source": "EXECUTION_SERIES_LAST_CLOSE",
            "new_key": "x",
        },
    }
    page = rv._conditions_table(experiment)
    for expected in (
        "<td title='account_id'>口座名</td><td>ACC1</td>",
        "<td title='initial_balance'>初期残高</td><td>1,000,000 円</td>",
        "<td title='trial_risk_rate'>1取引のリスク上限</td><td>残高の 2%</td>",
        "<td>残高の 20%</td>",
        "<td>1 通貨あたり 0.001 円（片道）</td>",
        "1 万通貨なら片道 10 円",
        "<td>0.02 円（2 pip）固定</td>",
        "<td>0.01 円（1 pip）</td>",
        "最初の15 分足の始値で約定する",
        "<td>20 分</td>",
        "<td>USDJPY 0.05 円（5 pip）</td>",
        "<td>直前に確定した15 分足の終値</td>",
        "<td title='new_key'>new_key</td><td>x</td>",
        "<th>執行の系列</th><td>USDJPY/15m/bid</td>",
    ):
        assert expected in page, expected


def test_change_html_shows_lineage_and_changed_axis(tmp_path: Path) -> None:
    make_fixture(tmp_path)
    first, second = rv.collect(tmp_path, tmp_path)[0].versions
    change = rv.change_html(second, first)
    assert "格子の値・軸の変更（PARAMETERS）" in change
    assert "期間の軸を上側へ動かす &lt;理由&gt;" in change
    assert re.search(
        r"<tr class='changed'><td><code>h1_ema.period</code>.*20 / 50.*50 / 75", change
    )
    assert "変わっていない" in change
    assert rv.change_html(first, None) == ""


def test_main_writes_page_without_printing_numbers(tmp_path: Path, capsys: object) -> None:
    make_fixture(tmp_path)
    assert rv.main(["--runs-root", str(tmp_path), "--repo-root", str(tmp_path)]) == 0
    out = tmp_path / "runs" / "viewer" / "index.html"
    page = out.read_text(encoding="utf-8")
    payload = page.split('<script id="data" type="application/json">', 1)[1].split("</script>")[0]
    assert "</" not in payload.replace("<\\/", "")
    data = json.loads(payload)
    versions = data["experiments"][0]["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert [s["title"] for s in versions[0]["sections"]] == ["1. 判定", "3. fold ごとの成績"]
    printed = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "実験 1 件・版 2 件" in printed
    assert "0.012345" not in printed
