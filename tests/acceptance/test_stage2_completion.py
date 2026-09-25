"""段階2の完了条件を人工データで通しで確かめる（全体計画 §8.2）。

完了条件は3つある。

1. 人工データで**注文・数量・損益・資産が手計算に一致**する。
2. **同一入力の再実行で trace が一致**する。
3. **warmup 中の注文がゼロ**である。

手計算の正本は紙上トレース [T01](../../docs/traces/T01_paper_trace.md) である。あわせて、
swap / rollover を計上していないことが成果物だけから読めること（ADR-0029、D07 §7.2）も
確かめる。

日付は T01 v1.3 と同じ 2015 年1月である。T01 の起草時の日付（2026 年1月）を使えない理由は
`tests/fixtures/acceptance/t01_market.py` の冒頭に書いた（2026 年以降は未分類の隔離期間で
どの経路からも読めない）。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from odyssey_fx.app.cli.main import main
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.metrics import ratio_of
from tests.acceptance.conftest import Artifacts, run_argv
from tests.fixtures.acceptance.t01_market import EXPECTED, WARMUP_END

#: 判断履歴の15表（再実行の一致はこの全表で確かめる）。
_TRACE_TABLES = (
    "OUTPUTS",
    "EVALUATIONS",
    "OPPORTUNITY_TRANSITIONS",
    "ORDER_REQUESTS",
    "ATTEMPT_DECISIONS",
    "RISK_ASSESSMENTS",
    "ORDERS",
    "ORDER_EVENTS",
    "FILLS",
    "RESERVATIONS",
    "POSITIONS",
    "MANAGEMENT_APPLICATIONS",
    "INTRABAR_RESOLUTIONS",
    "LEDGER_SNAPSHOTS",
    "EVIDENCE",
)


def _decimal(value: object) -> Decimal:
    assert isinstance(value, str), value
    return decimal_from_str(value)


def _metric(artifacts: Artifacts, metric_id: str) -> dict[str, object]:
    rows = [row for row in artifacts.evaluation("METRICS") if row["metric_id"] == metric_id]
    assert len(rows) == 1, rows
    return rows[0]


def _amount(artifacts: Artifacts, metric_id: str) -> Decimal:
    row = _metric(artifacts, metric_id)
    assert row["value_kind"] == "AMOUNT", row
    assert row["value_amount_currency"] == "JPY", row
    return _decimal(row["value_amount_amount"])


def _ratio(artifacts: Artifacts, metric_id: str) -> Decimal:
    row = _metric(artifacts, metric_id)
    assert row["value_kind"] == "RATIO", row
    return _decimal(row["value_ratio"])


def _mentions_warmup(diagnoses: object) -> bool:
    """評価見送りの診断に助走不足が含まれるか（D02 §8.3）。

    診断の列は正規化エンコード文字列の `list` 列である（D06 §9.1）。復号せず、理由コードの
    語がその文字列に現れるかだけを見る。
    """
    assert isinstance(diagnoses, list), diagnoses
    return any("WARMUP_INSUFFICIENT" in str(item) for item in diagnoses)


# --- 完了条件1: 注文・数量・損益・資産が手計算に一致する ----------------------


def test_the_orders_match_the_paper_trace(artifacts: Artifacts) -> None:
    """発注試行はエントリー2件と決済1件だけで、すべて受け付けられる（T01 §2・§9）。

    余分な試行が1件でもあれば、人工データが T01 の経路からずれている。
    """
    requests = artifacts.trace("ORDER_REQUESTS")
    assert [row["payload_kind"] for row in requests] == [
        "ENTRY_REQUEST",
        "CLOSE_REQUEST",
        "ENTRY_REQUEST",
    ]
    decisions = artifacts.trace("ATTEMPT_DECISIONS")
    assert [row["kind"] for row in decisions] == ["ACCEPTED", "ACCEPTED", "ACCEPTED"]
    assert [row["reason_code"] for row in decisions] == [None, None, None]


def test_the_fills_match_the_paper_trace(artifacts: Artifacts) -> None:
    """約定価格と数量が T01 の値（§2.4・§2.6・§9）と厳密に一致する。"""
    fills = artifacts.trace("FILLS")
    assert len(fills) == 3, fills
    entry1, exit1, entry2 = fills

    assert _decimal(entry1["price"]) == EXPECTED.entry1_price.value
    assert _decimal(entry1["quantity"]) == EXPECTED.entry1_quantity
    assert _decimal(exit1["price"]) == EXPECTED.exit1_price.value
    assert _decimal(exit1["quantity"]) == EXPECTED.entry1_quantity
    assert _decimal(entry2["price"]) == EXPECTED.entry2_price.value
    assert _decimal(entry2["quantity"]) == EXPECTED.entry2_quantity


def test_the_positions_match_the_paper_trace(artifacts: Artifacts) -> None:
    """完了取引の確定損益と、残存建玉の保護水準が T01 の値と一致する（§2.6・§9）。"""
    positions = artifacts.trace("POSITIONS")
    assert len(positions) == 2, positions
    closed, open_position = positions

    assert closed["status"] == "CLOSED"
    assert _decimal(closed["entry_price"]) == EXPECTED.entry1_price.value
    assert _decimal(closed["realized_amount"]) == EXPECTED.trade1_realized
    assert _decimal(closed["protection_stop_loss"]) == decimal_from_str("149.500")
    assert _decimal(closed["protection_take_profit"]) == decimal_from_str("151.240")

    # 残存建玉は未決済のまま残り、架空の決済損益を持たない（D06 §10.1 の手順7）。
    assert open_position["status"] == "OPEN"
    assert open_position["close_fill_id"] is None
    assert open_position["realized_amount"] is None
    assert _decimal(open_position["entry_price"]) == EXPECTED.entry2_price.value
    assert _decimal(open_position["quantity"]) == EXPECTED.entry2_quantity
    assert _decimal(open_position["protection_stop_loss"]) == decimal_from_str("150.400")
    assert _decimal(open_position["protection_take_profit"]) == decimal_from_str("152.200")


def test_the_balance_and_equity_path_matches_the_paper_trace(artifacts: Artifacts) -> None:
    """資産の推移が T01 §9.4 の値を通る。

    確かめるのは4点である。初期残高、入場直後（手数料だけを引いた残高と、直前に完了した
    執行足の終値で評価した含み損）、決済直後、そして最終 snapshot。
    """
    snapshots = artifacts.trace("LEDGER_SNAPSHOTS")
    balances = [_decimal(row["balance_amount"]) for row in snapshots]
    equities = [_decimal(row["equity_amount"]) for row in snapshots]

    assert balances[0] == EXPECTED.initial_balance
    assert equities[0] == EXPECTED.initial_balance
    assert EXPECTED.balance_after_entry1 in balances
    assert EXPECTED.balance_after_exit1 in balances
    assert balances[-1] == EXPECTED.balance_after_entry2
    # 含み損益込みの資産の最小値は入場直後の 998,688 円（T01 §9.4 の行#2）。
    assert min(equities) == EXPECTED.min_equity
    # 最終 snapshot の含み込み資産は 1,051,706 円（T01 §9.4 の行#6）。
    assert equities[-1] == EXPECTED.equity_with_mtm


def test_the_final_summaries_match_the_paper_trace(artifacts: Artifacts) -> None:
    """末尾の3集計と費用の内訳が T01 §9.3 の値と一致する。"""
    assert _amount(artifacts, "NET_PROFIT") == EXPECTED.realized
    assert _amount(artifacts, "END_EQUITY_MTM") == EXPECTED.equity_with_mtm
    assert _amount(artifacts, "HYPOTHETICAL_CLOSED_PROFIT") == EXPECTED.hypothetical_closed
    assert _amount(artifacts, "COST_CHARGED_TOTAL") == EXPECTED.commission
    assert _amount(artifacts, "COST_PRICE_EMBEDDED_TOTAL") == (
        EXPECTED.slippage_in_price + EXPECTED.spread_in_price
    )


def test_the_metrics_match_the_paper_trace_check_values(artifacts: Artifacts) -> None:
    """D07 §5.2 の「T01 検算」の値と一致する（段階2 の15件すべて）。

    建玉を保有していた時間の割合（`EXPOSURE_RATE`）は、2026-09-22 の人間の決定により
    **完了取引だけ**を数える（D07 §5.2 v1.3）。値そのものの検算は
    `test_the_exposure_rate_counts_only_the_closed_trade` で行う。

    完了取引の損益（#2）は指標集合 v2 で入場費用込みの取引損益の合計になる（D07 §7.3。
    §1.2 の例外 (a)）。T01 の値は `36,768 − 32 = 36,736 JPY`（P1 の入場手数料 32 円）。
    """
    assert _amount(artifacts, "NET_PROFIT") == EXPECTED.realized
    assert _amount(artifacts, "CLOSED_TRADE_PROFIT") == EXPECTED.trade1_realized - Decimal(32)

    trade_count = _metric(artifacts, "TRADE_COUNT")
    assert trade_count["value_kind"] == "COUNT"
    assert trade_count["value_count"] == 1

    assert _ratio(artifacts, "WIN_RATE") == decimal_from_str("1")
    assert _amount(artifacts, "MAX_DRAWDOWN_MTM") == EXPECTED.max_drawdown_mtm
    assert _ratio(artifacts, "MAX_DRAWDOWN_MTM_RATE") == EXPECTED.max_drawdown_mtm_rate
    assert _amount(artifacts, "MAX_DRAWDOWN_BALANCE") == EXPECTED.max_drawdown_balance
    assert _ratio(artifacts, "MAX_DRAWDOWN_BALANCE_RATE") == EXPECTED.max_drawdown_balance_rate
    assert _amount(artifacts, "COST_CHARGED_TOTAL") == EXPECTED.commission
    assert _amount(artifacts, "COST_PRICE_EMBEDDED_TOTAL") == (
        EXPECTED.slippage_in_price + EXPECTED.spread_in_price
    )

    offset = _metric(artifacts, "MAX_ADVERSE_FILL_OFFSET")
    assert offset["value_kind"] == "PRICE_OFFSET"
    assert _decimal(offset["value_offset"]) == EXPECTED.max_adverse_fill_offset

    assert _amount(artifacts, "END_EQUITY_MTM") == EXPECTED.equity_with_mtm
    assert _amount(artifacts, "HYPOTHETICAL_CLOSED_PROFIT") == EXPECTED.hypothetical_closed
    assert _ratio(artifacts, "NET_RETURN_RATE") == decimal_from_str("0.036706")


def test_the_exposure_rate_counts_only_the_closed_trade(artifacts: Artifacts) -> None:
    """建玉を保有していた時間の割合（D07 §5.2 の #9）を手計算と照合する。

    完了取引の保有時間（2時間15分 = 8,100 秒）だけを、run 区間の長さ（12 日 =
    1,036,800 秒）で割る。**残存建玉の保有時間は数えない**（2026-09-22 の人間の決定。
    D07 §5.2 v1.3）。run 末尾に残っている建玉 P2 は分子に入らない。
    """
    held_seconds = int(EXPECTED.holding1.total_seconds())
    assert held_seconds == 8_100
    # 比率は除算を1回だけカーネル精度で行う規則（D07 §5.1）に合わせて期待値を作る。
    expected = ratio_of(decimal_from_str(str(held_seconds)), decimal_from_str("1036800"))
    assert expected == decimal_from_str("0.0078125")
    assert _ratio(artifacts, "EXPOSURE_RATE") == expected


def test_the_trade_record_matches_the_paper_trace(artifacts: Artifacts) -> None:
    """完了取引の表が T01 §2 の1取引をそのまま表す（D07 §5.2 の `TradeRecord`）。"""
    trades = artifacts.evaluation("TRADES")
    assert len(trades) == 1, trades
    trade = trades[0]
    assert trade["trade_seq"] == 1
    assert trade["symbol"] == "USDJPY"
    assert trade["side"] == "BUY"
    assert trade["outcome"] == "WIN"
    assert trade["close_cause"] == "TAKE_PROFIT"
    assert _decimal(trade["entry_price"]) == EXPECTED.entry1_price.value
    assert _decimal(trade["exit_price"]) == EXPECTED.exit1_price.value
    assert _decimal(trade["realized_amount"]) == EXPECTED.trade1_realized
    # 保有時間は秒数の十進文字列（D02 §9.3 の表示の書式）。
    assert _decimal(trade["holding"]) == decimal_from_str(
        f"{int(EXPECTED.holding1.total_seconds())}.000000"
    )
    # 建玉 → 入場約定 → 注文 → 試行の連鎖を辿って取引機会が埋まる（D07 §8.1）。
    assert trade["opportunity_id"] == "OPP:00000001"


def test_the_consistency_checks_all_pass(artifacts: Artifacts) -> None:
    """整合検査12件が全件実施され、すべて合格する（D07 §10.2・§10.4）。"""
    checks = artifacts.evaluation("CONSISTENCY_CHECKS")
    assert [row["check"] for row in checks] == [
        "required_columns_present",
        "run_id_consistent",
        "trade_count_matches",
        "id_chain_complete",
        "realized_matches_balance",
        "opportunity_count_matches",
        "snapshot_order_monotonic",
        "single_account_currency",
        "all_values_readable",
        "calendar_matches_run",
        "run_manifest_readable",
        "input_keys_unique",
    ]
    assert all(row["outcome"] == "PASSED" for row in checks), checks


# --- 完了条件2: 同一入力の再実行で trace が一致する --------------------------


def test_the_rerun_produces_the_same_run_identifier(artifacts: Artifacts, rerun: Artifacts) -> None:
    """同じ完全入力の再実行は同じ実行の識別子になる（ADR-0006）。

    snapshot の識別子も同じである（同じ原ファイル・設定・コード版・分類から作るため、
    D03 §3.7.1）。
    """
    assert rerun.snapshot_id == artifacts.snapshot_id
    assert rerun.run_id == artifacts.run_id


def test_the_rerun_produces_the_same_trace(artifacts: Artifacts, rerun: Artifacts) -> None:
    """判断履歴の15表が1行ずつ一致する（全体計画 §8.2）。"""
    for table in _TRACE_TABLES:
        assert rerun.trace(table) == artifacts.trace(table), table


def test_the_rerun_produces_the_same_evaluation(artifacts: Artifacts, rerun: Artifacts) -> None:
    """評価結果のダイジェストと5表が一致する（D07 §9.1・§9.2）。

    再現性の判定はファイルのバイト列ではなく結果のダイジェストで行う（Parquet の
    メタデータや圧縮設定でバイト列は変わりうる）。5表そのものも比べて、ダイジェストだけが
    一致して中身が違う状態を残さない。
    """
    first = artifacts.evaluation_manifest()
    second = rerun.evaluation_manifest()
    assert second["result_digest"] == first["result_digest"]
    assert second["run_evaluation_id"] == first["run_evaluation_id"]
    for table in ("METRICS", "CATEGORY_COUNTS", "TRADES", "FILL_DIAGNOSTICS", "CONSISTENCY_CHECKS"):
        assert rerun.evaluation(table) == artifacts.evaluation(table), table


# --- 完了条件3: warmup 中の注文がゼロ ----------------------------------------


def test_the_warmup_skips_evaluations_instead_of_inventing_values(
    artifacts: Artifacts,
) -> None:
    """助走中の評価は見送りとして記録され、0 や成功値へ置換されない（上位 §4.3.15）。"""
    skipped = [row for row in artifacts.trace("EVALUATIONS") if row["outcome_kind"] == "SKIPPED"]
    assert skipped, "助走中の見送りが1件も無い（人工データが助走を含んでいない）"
    assert all(not row["outcome_output_ids"] for row in skipped), "見送りは出力を生まない"
    warmup = [row for row in skipped if _mentions_warmup(row["outcome_diagnoses"])]
    assert warmup, "助走不足の診断が1件も無い"
    assert all(UtcTime.parse(str(row["decision_time"])) <= WARMUP_END for row in warmup), (
        "助走不足の診断が助走の終わりより後に出ている"
    )


def test_no_order_is_placed_during_the_warmup(artifacts: Artifacts) -> None:
    """助走が終わるまで、発注試行・注文・約定が1件も起きない（全体計画 §8.2）。"""
    for table, column in (
        ("ORDER_REQUESTS", "created_at_time"),
        ("ORDERS", "accepted_at_time"),
        ("FILLS", "processed_at_time"),
    ):
        moments = [UtcTime.parse(str(row[column])) for row in artifacts.trace(table)]
        assert moments, f"{table} が空では助走中ゼロを確かめられない"
        assert all(moment > WARMUP_END for moment in moments), (table, moments)


# --- swap 未計上の明記（ADR-0029、D07 §7.2）----------------------------------


def test_the_evaluation_manifest_states_that_swap_is_not_modelled(
    artifacts: Artifacts,
) -> None:
    """評価 manifest の `swap_modeled` が必須項目として `false` で入っている（D07 §7.2）。"""
    manifest = artifacts.evaluation_manifest()
    assert "swap_modeled" in manifest, manifest
    assert manifest["swap_modeled"] is False
    assert manifest["status"] == "COMPLETED"
    assert manifest["run_status"] == "COMPLETED"
    assert manifest["fatal_failure_count"] == 0


def test_the_profit_metrics_carry_the_swap_caveat(artifacts: Artifacts) -> None:
    """損益と費用の指標に swap 未計上の注記が付く（D07 §7.2）。

    manifest だけに置くと、指標を抜き出して比較した時点で注記が消える。
    """
    for metric_id in (
        "NET_PROFIT",
        "CLOSED_TRADE_PROFIT",
        "COST_CHARGED_TOTAL",
        "COST_PRICE_EMBEDDED_TOTAL",
        "END_EQUITY_MTM",
        "HYPOTHETICAL_CLOSED_PROFIT",
        "NET_RETURN_RATE",
    ):
        caveats = _metric(artifacts, metric_id)["caveats"]
        assert isinstance(caveats, list)
        assert "SWAP_NOT_MODELED" in caveats, metric_id


def test_the_saved_artifacts_are_where_the_design_says(artifacts: Artifacts) -> None:
    """成果物の置き場所が `runs/<run_id>/eval/<評価 ID>/` である（D07 §8.2、Q4 決定）。"""
    directory = artifacts.evaluation_directory
    assert directory.parent.name == "eval"
    assert directory.parent.parent.name == artifacts.run_id
    assert directory.name == str(artifacts.evaluation_manifest()["run_evaluation_id"])
    for table in ("METRICS", "CATEGORY_COUNTS", "TRADES", "FILL_DIAGNOSTICS", "CONSISTENCY_CHECKS"):
        assert (directory / f"{table}.parquet").is_file(), table
    assert (directory / "evaluation.json").is_file()


def test_running_again_into_the_same_place_refuses_to_overwrite(
    artifacts: Artifacts, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同じ成果物の基点へもう一度回すと、既存の成果物を上書きせずに失敗する（ADR-0006）。

    同じ完全入力の再実行は同じ実行の識別子になるので、`runs/<run_id>/` が既にあることは
    ふつうに起こる。黙って上書きすると、再現性の成果物が消える。

    成果物の基点は**この試験専用に作る**。通しで作った成果物を書き換えると、他の試験が
    読む内容がこの試験の実行順に左右される。
    """
    root = tmp_path / "artifacts"
    assert main(run_argv(artifacts.repo, root)) == 0
    capsys.readouterr()

    assert main(run_argv(artifacts.repo, root)) == 1
    message = capsys.readouterr().err
    assert "already holds artifacts" in message
    assert "replace=True" in message

    # 置換を明示すれば書き直せる。旧 manifest は記録に残る（ADR-0006）。
    assert main(run_argv(artifacts.repo, root, replace=True)) == 0
    assert (root / "runs" / artifacts.run_id / "manifest.replaced.json").is_file()


def test_a_calendar_that_the_snapshot_did_not_use_is_refused(
    artifacts: Artifacts, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """snapshot が記録したカレンダーと違う版では run を始めない（D03 §3.7）。

    承認済みの足と完全性検査の報告は受入れのときのカレンダーで作られている。別の版で
    run を組むと、休場・夏時間・セッション境界の違いが公開イベントの予定と入力不足の
    判定を変え、**受入れをやり直さないまま結果の意味だけが変わる**。
    """
    other = tmp_path / "fx_ny17_v2.yaml"
    original = (artifacts.repo / "configs/calendars/fx_ny17_v1.yaml").read_text(encoding="utf-8")
    # カレンダー自身の版だけを上げる（`schema_version` は設定ファイルの形式の版であって
    # カレンダーの版ではない）。
    assert "\nversion: 1\n" in original, original
    other.write_text(original.replace("\nversion: 1\n", "\nversion: 2\n", 1), encoding="utf-8")

    argv = run_argv(artifacts.repo, tmp_path / "artifacts")
    argv[argv.index("--calendar") + 1] = str(other)
    assert main(argv) == 1
    message = capsys.readouterr().err
    assert "fx_ny17@v1" in message
    assert "fx_ny17@v2" in message


def test_a_timeframe_definition_the_snapshot_did_not_use_is_refused(
    artifacts: Artifacts, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """snapshot が記録した時間足定義と違う版では run を始めない（D03 §3.1・§3.2）。

    版を上げた定義では足境界と公開イベントの予定が変わる。**執行系列でない系列**（ここでは
    戦略が読む1時間足）の定義を差し替えても、承認済みの足が別の整列規則で読まれることに
    なるので、実行する前に止める。
    """
    other = tmp_path / "timeframes_v2.yaml"
    original = (artifacts.repo / "configs/calendars/timeframes_v1.yaml").read_text(encoding="utf-8")
    # 1時間足の定義だけ版を上げる（執行系列の15分足はそのまま）。
    changed = original.replace("- id: 1h\n    version: 1", "- id: 1h\n    version: 2", 1)
    assert changed != original, "時間足定義の書き方が変わっている"
    other.write_text(changed, encoding="utf-8")

    argv = run_argv(artifacts.repo, tmp_path / "artifacts")
    argv[argv.index("--timeframes") + 1] = str(other)
    assert main(argv) == 1
    assert "timeframe definition" in capsys.readouterr().err


def test_the_run_output_shows_an_evaluate_command_that_works(artifacts: Artifacts) -> None:
    """run の出力が案内する評価コマンドは、必須のカレンダーを含む（D07 §4.1 v2.0）。"""
    hint = [line for line in artifacts.run_output.splitlines() if "odyssey-fx evaluate" in line]
    assert len(hint) == 1, artifacts.run_output
    assert f"--run {artifacts.run_id}" in hint[0]
    assert "--calendar" in hint[0]
    assert "fx_ny17_v1.yaml" in hint[0]
