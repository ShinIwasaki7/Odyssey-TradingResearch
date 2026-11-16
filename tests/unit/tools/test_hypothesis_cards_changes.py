"""改善の変更の提案の検査と適用（D25 §3.9、決定 102・103、仮置き 6・12）の試験。人工の提案だけ。"""

from __future__ import annotations

from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import REPO_ROOT, card_bytes, card_data
from tools.ops.hypothesis_cards.card import parse_card
from tools.ops.hypothesis_cards.changes import (
    CHANGE_OUT_OF_RULES,
    VALUE_MISSING,
    ChangeRejected,
    apply_change,
    initial_state,
    parse_change,
)
from tools.ops.hypothesis_cards.emit import dump_document
from tools.ops.hypothesis_cards.repo import DirView
from tools.ops.hypothesis_cards.screening import check_change

CARD_ID = "h-20261120-dual-ema"


def _proposal(**overrides: object) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": 1,
        "card": CARD_ID,
        "from_version": 1,
        "decision": "CHANGE",
        "change_kind": "PARAMETERS",
        "operations": [
            {
                "op": "set_axis_values",
                "instance": "slow_ema",
                "parameter": "period",
                "values": [60, 80],
            }
        ],
        "reason": "長い側の移動平均を短くすると取引の数がどう変わるかを見る",
        "prediction": "取引が増える",
        "memo_sha256": "cd" * 32,
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def _reason(data: dict[str, Any], *, from_version: int = 1) -> str:
    with pytest.raises(ChangeRejected) as caught:
        parse_change(dump_document(data), card_id=CARD_ID, expected_from_version=from_version)
    return caught.value.reason


def test_a_well_formed_proposal_reads() -> None:
    change = parse_change(dump_document(_proposal()), card_id=CARD_ID, expected_from_version=1)
    assert (change.to_version, change.change_kind) == (2, "PARAMETERS")


@pytest.mark.parametrize(
    "overrides",
    [
        {"card": "h-20261120-other"},
        {"from_version": 2},
        {"decision": "MAYBE"},
        {"change_kind": "DATA"},
        {"operations": [{"op": "delete_everything"}]},
        {"memo_sha256": "x"},
        {"extra": "x"},
    ],
)
def test_format_errors_are_out_of_rules(overrides: dict[str, Any]) -> None:
    assert _reason(_proposal(**overrides)) == CHANGE_OUT_OF_RULES


def test_one_kind_per_change() -> None:
    """操作が `change_kind` と違う種類を含めば落とす（決定 102）。"""
    operations = [
        {
            "op": "set_axis_values",
            "instance": "slow_ema",
            "parameter": "period",
            "values": [60, 80],
        },
        {"op": "set_role", "role": "market_state", "source": None},
    ]
    assert _reason(_proposal(operations=operations)) == CHANGE_OUT_OF_RULES


def test_at_most_two_reevaluations() -> None:
    assert _reason(_proposal(from_version=3), from_version=3) == CHANGE_OUT_OF_RULES


def test_finish_only_after_version_two() -> None:
    finish = {
        "schema_version": 1,
        "card": CARD_ID,
        "from_version": 1,
        "decision": "FINISH",
        "reason": "変えるところが無い",
        "memo_sha256": "cd" * 32,
    }
    assert _reason(finish) == CHANGE_OUT_OF_RULES
    finish["from_version"] = 2
    parsed = parse_change(dump_document(finish), card_id=CARD_ID, expected_from_version=2)
    assert parsed.decision == "FINISH"


def test_ambiguous_carries_a_reason_identifier() -> None:
    ambiguous = {
        "schema_version": 1,
        "card": CARD_ID,
        "from_version": 1,
        "decision": "AMBIGUOUS",
        "reason_id": "RULE_NOT_COVERED",
        "memo_sha256": "cd" * 32,
    }
    parsed = parse_change(dump_document(ambiguous), card_id=CARD_ID, expected_from_version=1)
    assert parsed.reason_id == "RULE_NOT_COVERED"


def test_an_operation_without_its_value_is_value_missing() -> None:
    operations = [{"op": "set_axis_values", "instance": "slow_ema", "parameter": "period"}]
    assert _reason(_proposal(operations=operations)) == VALUE_MISSING
    operations_with_none: list[dict[str, object]] = [
        {"op": "set_parameter", "instance": "stop_level", "parameter": "lookback", "value": None}
    ]
    assert (
        _reason(_proposal(change_kind="STRATEGY_CODE", operations=operations_with_none))
        == VALUE_MISSING
    )


def test_digits_in_the_reason_are_out_of_rules() -> None:
    assert _reason(_proposal(reason="期間を 60 にする")) == CHANGE_OUT_OF_RULES


def _apply(operations: list[dict[str, Any]], kind: str) -> Any:
    card = parse_card(card_bytes(card_data()))
    change = parse_change(
        dump_document(_proposal(change_kind=kind, operations=operations)),
        card_id=CARD_ID,
        expected_from_version=1,
    )
    return apply_change(initial_state(card), change)


def test_parameters_operations_change_only_the_grid() -> None:
    state = _apply(
        [
            {
                "op": "add_axis",
                "instance": "stop_level",
                "parameter": "lookback",
                "values": [10, 20],
            },
            {"op": "remove_axis", "instance": "take_profit", "parameter": "reward_risk"},
        ],
        "PARAMETERS",
    )
    assert state.strategy_version == 1
    assert [(a.instance, a.parameter) for a in state.axes] == [
        ("slow_ema", "period"),
        ("stop_level", "lookback"),
    ]


def test_strategy_code_operations_change_the_declaration() -> None:
    state = _apply(
        [
            {
                "op": "rewire_input",
                "instance": "entry_trigger",
                "input": "level",
                "sources": ["market:GBPUSD/1h/bid:HIGH"],
            },
            {"op": "remove_component", "instance": "fast_ema"},
        ],
        "STRATEGY_CODE",
    )
    assert state.strategy_version == 2
    names = [item["instance_id"] for item in state.strategy["components"]]
    assert "fast_ema" not in names


@pytest.mark.parametrize(
    ("operations", "kind"),
    [
        ([{"op": "remove_axis", "instance": "x", "parameter": "y"}], "PARAMETERS"),
        (
            [{"op": "add_axis", "instance": "slow_ema", "parameter": "period", "values": [1, 2]}],
            "PARAMETERS",
        ),
        (
            [
                {
                    "op": "set_parameter",
                    "instance": "slow_ema",
                    "parameter": "period",
                    "value": {"type": "INT", "value": 70},
                }
            ],
            "STRATEGY_CODE",
        ),
        ([{"op": "remove_component", "instance": "slow_ema"}], "STRATEGY_CODE"),
        ([{"op": "set_role", "role": "trigger", "source": None}], "STRATEGY_CODE"),
    ],
)
def test_operations_that_cannot_apply(operations: list[dict[str, Any]], kind: str) -> None:
    with pytest.raises(ChangeRejected) as caught:
        _apply(operations, kind)
    assert caught.value.reason == CHANGE_OUT_OF_RULES


def test_a_change_with_a_grid_outside_four_to_six_is_out_of_rules() -> None:
    """適用の後の (5) の部品と試行 4〜6 の検査は、生成（前の版の実行記録を読む）の前に当たる。"""
    change = dump_document(
        _proposal(
            operations=[
                {
                    "op": "set_axis_values",
                    "instance": "slow_ema",
                    "parameter": "period",
                    "values": [60, 80, 100, 120],
                }
            ]
        )
    )
    result = check_change(
        DirView(REPO_ROOT), card=card_bytes(card_data()), change=change, from_version=1
    )
    assert result.reason == CHANGE_OUT_OF_RULES
    assert result.prereg is None


def test_a_non_change_decision_passes_without_generation() -> None:
    finish = dump_document(
        {
            "schema_version": 1,
            "card": CARD_ID,
            "from_version": 2,
            "decision": "FINISH",
            "reason": "変えるところが無い",
            "memo_sha256": "cd" * 32,
        }
    )
    result = check_change(
        DirView(REPO_ROOT),
        card=card_bytes(card_data()),
        change=finish,
        from_version=2,
        previous_changes={},
    )
    assert result.passed and result.prereg is None
