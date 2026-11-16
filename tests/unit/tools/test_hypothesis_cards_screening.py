"""カードの機械の検査 1〜7（D25 §3.5、決定 173、仮置き 29）と、既出の規則の正規化の試験。

人工のカード（`tests/fixtures/hypothesis_cards.py`）を、本物のリポジトリの設定（研究ポリシー
第 4 版・型板・部品のカタログ・既存の戦略の宣言）に当てる。生成物はファイルに書かない。snapshot も
成績も読まない。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import REPO_ROOT, card_bytes, card_data
from tools.ops.hypothesis_cards.normalize import NormalizationError, rule_digest
from tools.ops.hypothesis_cards.repo import DirView, OverlayView, ToolFailure
from tools.ops.hypothesis_cards.screening import (
    CARD_COMPONENT_UNKNOWN,
    CARD_DUPLICATE_RULE,
    CARD_EVIDENCE_UNLIKELY,
    CARD_FORMAT,
    CARD_GRID_SIZE,
    CARD_HISTORY_TOO_LONG,
    CARD_PREREG_REJECTED,
    known_rule_digests,
    screen_card,
)

VIEW = DirView(REPO_ROOT)


def _screen(data: dict[str, Any], view: Any = VIEW) -> tuple[str | None, tuple[str, ...]]:
    result = screen_card(view, card_bytes(data))
    return result.reason, result.problems


def test_a_card_on_another_currency_passes_all_checks() -> None:
    """H7 と同じ部品と接続でも、通貨が違えば別の規則（決定 173）。全 4 試行がコンパイルを通る。"""
    result = screen_card(VIEW, card_bytes(card_data()))
    assert result.passed, result.problems
    assert result.verification is not None
    assert (result.verification.trial_count, result.verification.rejected_count) == (4, 0)


def test_the_same_rule_on_the_same_currency_is_a_duplicate() -> None:
    reason, problems = _screen(card_data(symbol="EURUSD"))
    assert reason == CARD_DUPLICATE_RULE
    assert any("h7_h1_dual_ema_cross_eurusd_v1.yaml" in item for item in problems)


def test_a_format_error_is_reported_first() -> None:
    data = card_data()
    data["prediction"] = "取引は 2 倍"
    data["grid"]["axes"][0]["values"] = [50, 100, 150, 200]
    assert _screen(data)[0] == CARD_FORMAT


def test_an_unknown_component_or_version() -> None:
    data = card_data()
    data["strategy"]["components"][0]["component_version"] = 99
    assert _screen(data)[0] == CARD_COMPONENT_UNKNOWN


def test_a_history_window_longer_than_sixty_daily_bars() -> None:
    """1 時間足の EMA の窓 1,441 本（日足 60 本 ＋ 1 時間）は長すぎる。1,440 本はちょうど上限。"""
    data = card_data()
    data["strategy"]["components"][1]["parameters"]["window_bars"]["value"] = 1441
    assert _screen(data)[0] == CARD_HISTORY_TOO_LONG
    data["strategy"]["components"][1]["parameters"]["window_bars"]["value"] = 1440
    assert _screen(data)[0] is None


def test_an_excluded_latest_bar_counts_one_more_bar() -> None:
    """当該足を除く高値・安値の窓は 1 本多く数える（日足の 60 本の窓は 61 本になる）。"""
    data = card_data()
    stop = data["strategy"]["components"][2]
    stop["inputs"]["prices"] = ["market:GBPUSD/1d_ny17/bid:LOW"]
    stop["triggers"] = [{"name": "d1", "when": "bar_close", "series": "GBPUSD/1d_ny17/bid"}]
    stop["parameters"]["lookback"]["value"] = 59
    assert _screen(data)[0] is None
    stop["parameters"]["lookback"]["value"] = 60
    assert _screen(data)[0] == CARD_HISTORY_TOO_LONG


@pytest.mark.parametrize("values", [[50, 100, 150, 200], [50]])
def test_grids_outside_four_to_six_trials(values: list[int]) -> None:
    data = card_data()
    data["grid"]["axes"][0]["values"] = values
    assert _screen(data)[0] == CARD_GRID_SIZE


def test_unlikely_evidence_is_rejected_after_the_grid() -> None:
    data = card_data()
    data["evidence_estimate"]["reaches_floor"] = "UNLIKELY"
    assert _screen(data)[0] == CARD_EVIDENCE_UNLIKELY
    data["grid"]["axes"][0]["values"] = [50, 100, 150, 200]
    assert _screen(data)[0] == CARD_GRID_SIZE


def test_a_declaration_that_does_not_compile_is_rejected_at_check_seven() -> None:
    """長い EMA の窓が期間の 2 倍より短い（コンパイルが拒否する）なら 7 で落ちる。"""
    data = card_data()
    data["strategy"]["components"][1]["parameters"]["window_bars"]["value"] = 120
    reason, problems = _screen(data)
    assert reason == CARD_PREREG_REJECTED
    assert any("コンパイル" in item or "読込" in item for item in problems)


def test_favored_regimes_need_policy_version_five() -> None:
    """第 4 版を指す実験は得意な局面を書けない（D24 の検査 E12）ので、今の登録簿では 7 で落ちる。"""
    data = card_data()
    data["favored_regimes"] = ["UP_HIGH"]
    assert _screen(data)[0] == CARD_PREREG_REJECTED


def test_identifier_collisions_are_card_format() -> None:
    raw = card_bytes(card_data())
    view = OverlayView(VIEW, {"research/hypotheses/done/h-20261120-dual-ema.yaml": raw})
    reason, problems = _screen(card_data(), view)
    assert reason == CARD_FORMAT and any("同じ識別子のカード" in item for item in problems)
    own = "research/hypotheses/queue/h-20261120-dual-ema.yaml"
    assert screen_card(OverlayView(VIEW, {own: raw}), raw, path=own).passed


def test_cards_in_taken_and_done_count_as_existing_rules() -> None:
    other = card_data(title="other-ema")
    view = OverlayView(
        VIEW, {"research/hypotheses/taken/h-20261120-other-ema.yaml": card_bytes(other)}
    )
    assert _screen(card_data(), view)[0] == CARD_DUPLICATE_RULE


# --- 正規化（仮置き 29） ---------------------------------------------------------------


def _body() -> dict[str, Any]:
    return copy.deepcopy(card_data()["strategy"])


def test_names_order_and_values_do_not_change_the_digest() -> None:
    body = _body()
    renamed = _body()
    renamed["components"].reverse()
    for component in renamed["components"]:
        component["instance_id"] = "x_" + component["instance_id"]
        for sources in component.get("inputs", {}).values():
            sources[:] = [s.replace("output:", "output:x_") for s in sources]
        for trigger in component["triggers"]:
            trigger["name"] = "renamed"
        for parameter in component.get("parameters", {}).values():
            if parameter["type"] == "INT":
                parameter["value"] += 1
    renamed["roles"] = {key: "x_" + value for key, value in renamed["roles"].items()}
    renamed["opportunity_concurrency"]["max_active"] = 2
    assert rule_digest(renamed) == rule_digest(body)


def test_connections_series_components_and_roles_change_the_digest() -> None:
    base = rule_digest(_body())
    rewired = _body()
    rewired["components"][3]["inputs"]["level"] = ["market:GBPUSD/1h/bid:HIGH"]
    timeframe = _body()
    timeframe["components"][0]["inputs"]["prices"] = ["market:GBPUSD/15m/bid:CLOSE"]
    version = _body()
    version["components"][3]["component_version"] = 2
    entry = _body()
    entry["entry_policy"] = {"kind": "AWAIT_CONFIRMATION"}
    digests = {rule_digest(item) for item in (rewired, timeframe, version, entry)}
    assert base not in digests and len(digests) == 4


def test_cycles_and_dangling_references_do_not_normalize() -> None:
    body = _body()
    body["components"][0]["inputs"]["prices"] = ["output:entry_trigger.opportunity"]
    with pytest.raises(NormalizationError):
        rule_digest(body)
    body = _body()
    body["roles"]["trigger"] = "missing.opportunity"
    with pytest.raises(NormalizationError):
        rule_digest(body)


def test_every_existing_declaration_normalizes_and_currencies_stay_distinct() -> None:
    known = known_rule_digests(VIEW)
    paths = [path for listed in known.values() for path in listed]
    assert len(paths) == len(list((REPO_ROOT / "configs/strategies").glob("*.yaml")))
    by_path = {path: digest for digest, listed in known.items() for path in listed}
    strategies = "configs/strategies/"
    assert (
        by_path[strategies + "strategy_a_v1.yaml"]
        != by_path[strategies + "strategy_a_eurusd_v1.yaml"]
    )


def test_an_unreadable_existing_declaration_is_a_tool_failure(tmp_path: Path) -> None:
    view = OverlayView(VIEW, {"configs/strategies/broken.yaml": b"schema_version: 1\n"})
    with pytest.raises(ToolFailure):
        known_rule_digests(view)
