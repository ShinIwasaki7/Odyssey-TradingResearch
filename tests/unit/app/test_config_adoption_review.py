"""採用審査の規則のファイル・登録簿・申告ファイルの読込（D16 §8・§10.3。仮置き 10）。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.app.config import ConfigError
from odyssey_fx.app.config.adoption_review import (
    adoption_review_registry_path,
    adoption_review_rules_path,
    declaration_path,
    load_adoption_review_registry,
    load_adoption_review_rules,
    load_declaration,
)
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.money import CurrencyCode
from odyssey_fx.evaluation.adapters.fs_store import TRIAL_LEDGER_PATH, read_trial_ledger_file
from odyssey_fx.evaluation.application.ports import TrialLedgerContents
from odyssey_fx.evaluation.domain.adoption_review import DeclarationStatus
from tests.fixtures.evaluation.adoption_review import REPO_ROOT

_RULES = adoption_review_rules_path(REPO_ROOT, 1)
_RULES_V2 = adoption_review_rules_path(REPO_ROOT, 2)
#: 版 1 の登録簿の値（R3 の PR で固定した。版 2 の形を読めるようにしても変わらないこと）。
_V1_DIGEST = "d1623445fd1407a6bcc0db1f49db14577a1e65dd05119ba399ac1d0cad0df03c"


def test_the_committed_rules_version_1_matches_its_registry_entry() -> None:
    """版 1 は研究候補が 0 件のうちに固定し、登録簿のダイジェストと一致する（D16 §10.3）。"""
    rules, rules_digest = load_adoption_review_rules(_RULES, version=1)
    first, *_ = load_adoption_review_registry(adoption_review_registry_path(REPO_ROOT))
    assert (first.rules_id, first.version, first.digest) == (
        rules.rules_id,
        rules.version,
        rules_digest,
    )
    assert rules_digest.hex == _V1_DIGEST
    assert first.effective_after is None  # 版 1 は最初から効く（R1-10 (a)）


def test_the_committed_rules_version_2_matches_its_registry_entry() -> None:
    """版 2（行 5 の版 2。D16 v0.4 案 §10.3）は登録簿の 2 つ目の要素で、ダイジェストが一致し、
    効き始めの行は版管理の試行台帳の行（登録の時点の末尾の行）である（R1-10 (a)）。"""
    rules, rules_digest = load_adoption_review_rules(_RULES_V2, version=2)
    _, second = load_adoption_review_registry(adoption_review_registry_path(REPO_ROOT))
    assert (second.rules_id, second.version, second.digest) == (
        rules.rules_id,
        rules.version,
        rules_digest,
    )
    contents = read_trial_ledger_file(REPO_ROOT / TRIAL_LEDGER_PATH)
    assert isinstance(contents, TrialLedgerContents)
    assert second.effective_after in [line.digest for line in contents.lines]


def test_version_2_differs_from_version_1_only_in_the_conversion_row() -> None:
    """版 2 の値は行 5 の一覧のほかは版 1 と同じ（D16 v0.4 案 §10.3）。"""
    first, _ = load_adoption_review_rules(_RULES, version=1)
    second, _ = load_adoption_review_rules(_RULES_V2, version=2)
    assert second.convertible_settlement_currencies == (CurrencyCode("CHF"), CurrencyCode("USD"))
    assert replace(second, version=1, convertible_settlement_currencies=()) == first


def _v2_text(conversion: str) -> str:
    text = _RULES_V2.read_text(encoding="utf-8")
    old = 'currency_conversion: {convertible_settlement_currencies: ["CHF", "USD"]}'
    assert old in text
    return text.replace(old, f"currency_conversion: {conversion}")


def test_the_version_2_digest_ignores_the_order_of_the_list() -> None:
    _, original = load_adoption_review_rules(_RULES_V2, version=2)
    _, reordered = load_adoption_review_rules(
        _RULES_V2,
        version=2,
        text=_v2_text('{convertible_settlement_currencies: ["USD", "CHF"]}'),
    )
    assert reordered == original
    _, changed = load_adoption_review_rules(
        _RULES_V2, version=2, text=_v2_text('{convertible_settlement_currencies: ["USD"]}')
    )
    assert changed != original


@pytest.mark.parametrize(
    "conversion",
    [
        '{require_account_currency: true, convertible_settlement_currencies: ["USD"]}',
        "{}",
        "{convertible_settlement_currencies: []}",
        '{convertible_settlement_currencies: ["USD", "USD"]}',
        '{convertible_settlement_currencies: ["usd"]}',
        "{convertible_settlement_currencies: USD}",
    ],
)
def test_malformed_conversion_rows_are_rejected(conversion: str) -> None:
    with pytest.raises(ConfigError):
        load_adoption_review_rules(_RULES_V2, version=2, text=_v2_text(conversion))


def test_the_digest_ignores_how_a_value_is_written() -> None:
    text = _RULES.read_text(encoding="utf-8")
    _, original = load_adoption_review_rules(_RULES, version=1)
    _, rewritten = load_adoption_review_rules(
        _RULES, version=1, text=text.replace('length: "PT2H"', 'length: "PT120M"')
    )
    assert rewritten == original
    _, changed = load_adoption_review_rules(
        _RULES, version=1, text=text.replace('max_share: "0.25"', 'max_share: "0.5"')
    )
    assert changed != original


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("version: 1\n", "version: 2\n"),
        ("rule: ANY_CROSSING", "rule: MAJORITY"),
        ("undetermined_result: DEFERRED", "undetermined_result: DO_NOT_PROCEED"),
        ("declared_fits: [BAR_RESOLUTION, ", "declared_fits: ["),
        ('max_ratio: "2"', "max_ratio: 2"),
    ],
)
def test_rules_outside_the_decisions_are_rejected(old: str, new: str) -> None:
    text = _RULES.read_text(encoding="utf-8")
    assert old in text
    with pytest.raises(ConfigError):
        load_adoption_review_rules(_RULES, version=1, text=text.replace(old, new, 1))


def _registry(tmp_path: Path, body: str) -> Path:
    path = adoption_review_registry_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"schema_version: 1\nentries:\n{body}", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "body",
    [
        f'  - {{id: adoption_review, version: 1, digest: "{"a" * 64}",'
        f' effective_after: "{"b" * 64}"}}\n',
        f'  - {{id: adoption_review, version: 2, digest: "{"a" * 64}", effective_after: null}}\n',
        f'  - {{id: other, version: 1, digest: "{"a" * 64}", effective_after: null}}\n',
        f'  - {{id: adoption_review, version: 1, digest: "{"a" * 64}", effective_after: null}}\n'
        f'  - {{id: adoption_review, version: 1, digest: "{"c" * 64}", effective_after: null}}\n',
        '  - {id: adoption_review, version: 1, digest: "short", effective_after: null}\n',
    ],
)
def test_malformed_registries_are_rejected(tmp_path: Path, body: str) -> None:
    with pytest.raises(ConfigError):
        load_adoption_review_registry(_registry(tmp_path, body))


def test_a_missing_registry_is_a_read_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_adoption_review_registry(adoption_review_registry_path(tmp_path))


_ID = ExperimentId(digest("experiment"))


def _declaration(tmp_path: Path, text: str) -> Path:
    path = declaration_path(tmp_path, _ID, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _text(used: str = "false", execution: int = 1) -> str:
    return (
        "schema_version: 1\n"
        f'experiment_id: "{_ID}"\n'
        f"execution: {execution}\n"
        'question: "研究の外で見た相場を候補の選定・調整に使ったか"\n'
        'answer: "使っていない"\n'
        'recorded_by: "司令塔"\n'
        f"used_outside_viewing: {used}\n"
    )


def test_a_declaration_records_the_answer_and_its_digest(tmp_path: Path) -> None:
    path = _declaration(tmp_path, _text())
    loaded = load_declaration(path, _ID, 1)
    assert loaded.status is DeclarationStatus.NOT_DECLARED
    assert loaded.file_digest is not None
    declared = load_declaration(_declaration(tmp_path, _text("true")), _ID, 1)
    assert declared.status is DeclarationStatus.DECLARED


@pytest.mark.parametrize(
    "text",
    [
        _text(execution=2),
        _text('"false"'),
        _text().replace('recorded_by: "司令塔"\n', ""),
        _text().replace('answer: "使っていない"', 'answer: " "'),
    ],
)
def test_a_malformed_declaration_counts_as_missing(tmp_path: Path, text: str) -> None:
    loaded = load_declaration(_declaration(tmp_path, text), _ID, 1)
    assert loaded.status is DeclarationStatus.MISSING
    assert loaded.file_digest is None


def test_an_absent_declaration_counts_as_missing(tmp_path: Path) -> None:
    loaded = load_declaration(declaration_path(tmp_path, _ID, 1), _ID, 1)
    assert loaded.status is DeclarationStatus.MISSING
