"""仮説カードの書式と識別子（D25 §3.1・§3.2、仮置き 4・6・7。検査 1 `CARD_FORMAT`）と、生成物の
YAML の書き方（`emit`）の試験。人工のカードだけを使い、成績を読まない。"""

from __future__ import annotations

import pytest

from tests.fixtures.hypothesis_cards import card_bytes, card_data
from tools.ops.hypothesis_cards.card import (
    CardFormatError,
    experiment_id_of,
    has_digit,
    parse_card,
)
from tools.ops.hypothesis_cards.emit import dump_document
from tools.ops.hypothesis_cards.repo import ToolFailure


def _problems(data: dict[str, object], **kwargs: object) -> tuple[str, ...]:
    with pytest.raises(CardFormatError) as caught:
        parse_card(card_bytes(data), **kwargs)  # type: ignore[arg-type]
    return caught.value.problems


def test_the_default_card_reads_and_derives_the_identifiers() -> None:
    card = parse_card(
        card_bytes(card_data()), path="research/hypotheses/queue/h-20261120-dual-ema.yaml"
    )
    assert card.card_id == "h-20261120-dual-ema"
    assert card.experiment_id == "c_h20261120_dual_ema_gbpusd"
    assert [axis.values for axis in card.axes] == [(50, 100), (1.5, 3.0)]
    assert (
        experiment_id_of("a-20261120-ema-pullback", "EURUSD") == "c_a20261120_ema_pullback_eurusd"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("mouth", "robot"),
        ("symbol", "EURGBP"),
        ("schema_version", 2),
        ("title", "X"),
        ("created_at", "2026-11-20"),
        ("rule", ""),
    ],
)
def test_wrong_values_of_the_fields_are_card_format(key: str, value: object) -> None:
    data = card_data()
    data[key] = value
    assert _problems(data)


def test_missing_and_extra_fields_are_card_format() -> None:
    data = card_data()
    del data["prediction"]
    data["score"] = "x"
    problems = _problems(data)
    assert any("必須の項目が無い" in item for item in problems)
    assert any("書式に無い項目" in item for item in problems)


def test_the_id_must_match_mouth_date_and_title_and_the_file_name() -> None:
    data = card_data()
    data["id"] = "h-20261121-dual-ema"
    assert any("created_at の日付" in item for item in _problems(data))
    assert any(
        "ファイル名" in item
        for item in _problems(card_data(), path="research/hypotheses/queue/h-20261120-other.yaml")
    )


@pytest.mark.parametrize("text", ["二つの局面で多い", "局面 ２ で多い", "三割ほど多い 3"])
def test_free_text_with_digits_is_card_format(text: str) -> None:
    """半角・全角の数字を落とす（仮置き 6）。漢数字は Unicode の数字の分類に入らない。"""
    data = card_data()
    data["prediction"] = text
    if has_digit(text):
        assert any("prediction" in item for item in _problems(data))
    else:
        parse_card(card_bytes(data))


def test_numbers_in_the_rule_must_be_parameters_or_grid_values() -> None:
    data = card_data()
    data["rule"] = "期間 20 の EMA が期間 50 か 100 の EMA を上抜けたら買う規則"
    parse_card(card_bytes(data))
    data["rule"] = "期間 21 の EMA が上抜けたら買う規則"
    assert any("`rule` の数" in item for item in _problems(data))


@pytest.mark.parametrize("rule", ["1 時間足で上抜けたら買う規則", "取引が 15 倍になる規則"])
def test_numbers_in_series_names_and_versions_are_not_allowed_in_the_rule(rule: str) -> None:
    """系列名（`1h`・`15m`）・部品の版の数は許さない（仮置き 6。Codex 2 巡目の指摘）。"""
    data = card_data()
    data["strategy"]["components"][0]["inputs"]["prices"] = ["market:GBPUSD/15m/bid:CLOSE"]
    data["rule"] = rule
    assert any("`rule` の数" in item for item in _problems(data))


def test_the_written_axis_parameter_must_be_the_first_grid_value() -> None:
    data = card_data()
    data["grid"]["axes"][0]["values"] = [100, 50]
    assert any("格子の先頭の値" in item for item in _problems(data))


def test_ai_cards_need_a_read_scope_inside_the_bundle() -> None:
    data = card_data(mouth="ai")
    parse_card(card_bytes(data), allowed_read_paths={"ledger/trial_ledger.jsonl"})
    assert any("読む束" in item for item in _problems(data, allowed_read_paths={"other"}))
    del data["read_scope"]
    assert any("read_scope" in item for item in _problems(data))


def test_the_strategy_must_have_exactly_the_five_body_keys() -> None:
    data = card_data()
    data["strategy"]["strategy_id"] = "x"
    del data["strategy"]["roles"]
    problems = _problems(data)
    assert any("必須の項目が無い（roles）" in item for item in problems)
    assert any("strategy_id" in item for item in problems)


def test_yaml_that_does_not_read_is_card_format() -> None:
    with pytest.raises(CardFormatError):
        parse_card(b"schema_version: 1\nid: &a x\n")
    with pytest.raises(CardFormatError):
        parse_card(b"\xff\xfe")


# --- emit -----------------------------------------------------------------------------


def test_the_emitter_round_trips_floats_unicode_and_nesting() -> None:
    data = {
        "schema_version": 1,
        "small": 1e-05,
        "big": 1e20,
        "plain": 3.0,
        "text": '日本語と "引用符" と \\ と改行\nを含む',
        "flags": [True, False, None],
        "empty": {},
        "none": [],
        "rows": [{"a": 1, "b": [1, 2]}, {"c": {"d": "e"}}],
        "key with space": 1,
    }
    first = dump_document(data, comments=["一行目", ""])
    assert first == dump_document(data, comments=["一行目", ""])
    assert first.startswith("# 一行目\n#\n\n".encode())
    assert b"small: 1.0e-05" in first


def test_the_emitter_refuses_values_it_cannot_write() -> None:
    with pytest.raises(ToolFailure):
        dump_document({"schema_version": 1, "x": float("nan")})
    with pytest.raises(ToolFailure):
        dump_document({"schema_version": 1, "x": object()})
    with pytest.raises(ToolFailure):
        dump_document({"schema_version": 1}, comments=["二\n行"])
