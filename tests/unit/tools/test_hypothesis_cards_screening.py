"""カードの機械の検査 1〜7（D25 §3.5、決定 173、仮置き 29）と、既出の規則の正規化の試験。

人工のカード（`tests/fixtures/hypothesis_cards.py`）を、本物のリポジトリの設定（研究ポリシーの
現行の版（2026-11-17 の登録の後は第 5 版）・型板・部品のカタログ・既存の戦略の宣言）に当てる。
生成物はファイルに書かない。snapshot も成績も読まない。
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import REPO_ROOT, card_bytes, card_data
from tools.ops.hypothesis_cards.normalize import NormalizationError, rule_digest
from tools.ops.hypothesis_cards.repo import DirView, OverlayView
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


@pytest.mark.parametrize(
    ("key", "value"),
    [("component_version", 99), ("component_version", 0), ("component", "unknown component")],
)
def test_an_unknown_component_or_version(key: str, value: object) -> None:
    """鍵として成り立たない名前・版も例外にせず不合格にする（Codex 延長の巡の指摘）。"""
    data = card_data()
    data["strategy"]["components"][0][key] = value
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


def _registry_without_v5() -> bytes:
    """リポジトリの登録簿から第 5 版の行を除いた本文（第 5 版の登録の前の時点。D24 §8.2）。"""
    path = "configs/policies/research/registry.yaml"
    lines = (REPO_ROOT / path).read_bytes().splitlines(keepends=True)
    kept = [line for line in lines if b"version: 5," not in line]
    assert len(kept) == len(lines) - 1
    return b"".join(kept)


def test_favored_regimes_need_policy_version_five() -> None:
    """第 4 版を指す実験は得意な局面を書けない（D24 の検査 E12）ので、第 5 版の登録の前は
    検査 7 で落ちる。

    2026-11-17 改訂: 第 5 版を登録した（D24 §8.2）ので、今の登録簿では検査 7 を通る。登録の前の
    時点は、登録簿から第 5 版の行を除いた時点で確かめる。
    """
    data = card_data()
    data["favored_regimes"] = ["UP_HIGH"]
    before = OverlayView(VIEW, {"configs/policies/research/registry.yaml": _registry_without_v5()})
    assert _screen(data, before)[0] == CARD_PREREG_REJECTED
    assert _screen(data)[0] is None


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
    # 既出の規則の出どころは、戦略の宣言と、`taken/`・`done/` のカードの 2 種類（D25 §3.5 の 6）。
    # 実行器がカードを取り出すと `taken/` にカードが増えるので、宣言とカードを分けて数える
    # （2026-11-25 の最初の事前登録の PR で、宣言の数だけと比べていた検査が落ちた）。
    known = known_rule_digests(VIEW)
    strategies = "configs/strategies/"
    # 宣言はすべて正規化できる。書式で棄却されて `done/` に入ったカードが外れるのは設計どおり。
    assert [note for note in known.skipped if note.startswith(strategies)] == []
    paths = [path for listed in known.digests.values() for path in listed]
    declared = [path for path in paths if path.startswith(strategies)]
    assert len(declared) == len(list((REPO_ROOT / "configs/strategies").glob("*.yaml")))
    cards = [
        path
        for place in ("taken", "done")
        for path in (REPO_ROOT / "research/hypotheses" / place).glob("*.yaml")
        if not path.name.endswith(".outcome.yaml")
    ]
    assert len(paths) - len(declared) + len(known.skipped) == len(cards)
    by_path = {path: digest for digest, listed in known.digests.items() for path in listed}
    assert (
        by_path[strategies + "strategy_a_v1.yaml"]
        != by_path[strategies + "strategy_a_eurusd_v1.yaml"]
    )


def test_unreadable_or_broken_existing_rules_are_skipped_with_a_reason() -> None:
    """書式で棄却されて `done/` に入ったカードなどは照合から外し、道具の失敗にしない
    （設計適合レビューの重大 1。`TOOL_FAILURE` は道具と環境の失敗だけ。D25 §3.5）。"""
    broken = card_data(title="broken")
    broken["strategy"]["roles"]["trigger"] = "nowhere"
    view = OverlayView(
        VIEW,
        {
            "configs/strategies/broken.yaml": b"schema_version: 1\n",
            "research/hypotheses/done/h-20261120-unreadable.yaml": b"x: [\n",
            "research/hypotheses/done/h-20261120-broken.yaml": card_bytes(broken),
        },
    )
    known = known_rule_digests(view)
    assert len(known.skipped) == 3
    result = screen_card(view, card_bytes(card_data()))
    assert result.passed, result.problems
    assert len(result.notes) == 3


@pytest.mark.parametrize(
    ("component", "key", "value"),
    [
        (4, "inputs", {"opportunity": ["output:missing.opportunity"]}),
        (0, "inputs", {"prices": ["output:entry_trigger.opportunity"]}),
    ],
)
def test_a_card_with_broken_wiring_is_rejected_at_check_seven(
    component: int, key: str, value: object
) -> None:
    """配線が壊れたカード（未定義の使用箇所・接続の輪）は重なりでなく 7 で落ちる
    （設計適合レビューの重大 1）。"""
    data = card_data()
    data["strategy"]["components"][component][key] = value
    assert _screen(data)[0] == CARD_PREREG_REJECTED


def test_a_role_without_a_port_is_rejected_at_check_seven() -> None:
    data = card_data()
    data["strategy"]["roles"]["trigger"] = "entry_trigger"
    assert _screen(data)[0] == CARD_PREREG_REJECTED


def test_series_of_another_currency_are_card_format() -> None:
    data = card_data()
    data["strategy"]["components"][0]["inputs"]["prices"] = ["market:EURUSD/1h/bid:CLOSE"]
    reason, problems = _screen(data)
    assert reason == CARD_FORMAT and any("EURUSD" in item for item in problems)


@pytest.mark.parametrize("rule", ["Ⅳ 本の足で上抜けたら買う規則", "期間 ٢٠ の規則"])
def test_unreadable_digits_in_the_rule_are_card_format(rule: str) -> None:
    data = card_data()
    data["rule"] = rule
    reason, problems = _screen(data)
    assert reason == CARD_FORMAT and any("読めない数字" in item for item in problems)


@pytest.mark.parametrize("escape", ["\\x85", "\\u2028", "\\x07", "\\N"])
def test_control_characters_are_card_format_not_tool_failures(escape: str) -> None:
    """YAML の二重引用符の中の escape で入る制御文字・区切りの文字は、生成物に書く前に書式で落とす
    （道具の失敗にしない。設計適合レビューの軽微 3）。"""
    raw = card_bytes(card_data()).replace(
        "二本の移動平均".encode(), ("二本の" + escape + "移動平均").encode()
    )
    assert screen_card(VIEW, raw).reason == CARD_FORMAT


def test_sharing_a_single_instance_differs_from_two_same_shaped_instances() -> None:
    """値だけが違う 2 つの EMA を比べる接続と、同じ EMA を両方の入力に使う接続は別の規則
    （Codex 1 巡目の指摘）。2 つの EMA の役を入れ替えただけなら同じ規則。"""
    base = rule_digest(_body())
    shared = _body()
    shared["components"][3]["inputs"]["level"] = ["output:fast_ema.value"]
    assert rule_digest(shared) != base
    swapped = _body()
    swapped["components"][3]["inputs"] = {
        "price": ["output:slow_ema.value"],
        "level": ["output:fast_ema.value"],
    }
    assert rule_digest(swapped) == base


def test_a_huge_grid_is_rejected_without_enumerating_it() -> None:
    """研究ポリシーの上限を大きく超える格子は、列挙とコンパイルの前に試行の数で落とす。"""
    data = card_data()
    data["grid"]["axes"] = [
        {"instance": "slow_ema", "parameter": "period", "values": list(range(50, 250))},
        {
            "instance": "take_profit",
            "parameter": "reward_risk",
            "values": [1.5 + step / 10 for step in range(200)],
        },
        {"instance": "stop_level", "parameter": "lookback", "values": list(range(20, 220))},
    ]
    result = screen_card(VIEW, card_bytes(data))
    assert result.reason == CARD_GRID_SIZE
    assert result.verification is None


def test_identifiers_with_a_trailing_newline_are_card_format() -> None:
    """識別子は文字列の全体で照合する（`$` は末尾の改行の前でも合う。Codex 1 巡目の指摘）。"""
    data = card_data()
    data["id"] += "\n"
    data["title"] += "\n"
    assert _screen(data)[0] == CARD_FORMAT


def test_non_finite_numbers_and_dates_are_card_format_not_tool_failures() -> None:
    """生成物に書けない値（`.nan`・`.inf`・日時）はカードの不合格（Codex 3 巡目の指摘）。"""
    raw = card_bytes(card_data())
    for old, new in ((b"[1.5, 3.0]", b"[1.5, .nan]"), (b"value: 300", b"value: 2026-11-20")):
        assert old in raw
        result = screen_card(VIEW, raw.replace(old, new, 1))
        assert result.reason == CARD_FORMAT, result.problems


def test_the_read_card_and_its_preregistration_cannot_be_mutated() -> None:
    """凍結した結果の入れ子も書き換えられない（Codex 3 巡目の指摘）。"""
    result = screen_card(VIEW, card_bytes(card_data()))
    assert result.card is not None and result.prereg is not None
    with pytest.raises(TypeError):
        result.card.strategy["roles"]["trigger"] = "x.y"
    with pytest.raises(TypeError):
        result.prereg.files["x"] = b""  # type: ignore[index]
