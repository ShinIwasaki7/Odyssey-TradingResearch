"""検査 2〜8（枝と種類・許されたパス・作り直し・記録の書式・自由文の数字・題と本文・自動 merge）。

D25 §6.2・§6.3。関門 AU3 の「許されないパス・数字の入った自由文・自動 merge の付いた設計 PR で
落ちる試験」を含む。人工のカード・記録・台帳だけを使う。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import card_bytes as hc_card_bytes
from tests.fixtures.hypothesis_cards import card_data
from tests.fixtures.runner_ci import (
    CARD_ID,
    EXPERIMENT,
    HUMAN_CARD_ID,
    TRIAL,
    card_bytes,
    dump,
    generated_files,
    ledgers,
    make_input,
    outcome,
    prereg_note,
    report,
    run_record,
    trial_ledger,
)
from tools.ops.hypothesis_cards.card import CardFormatError, parse_card
from tools.ops.runner_ci.cli import run_checks
from tools.ops.runner_ci.model import CheckInput, CheckResult, Snapshot, Status

QUEUE = f"research/hypotheses/queue/{CARD_ID}.yaml"
TAKEN = f"research/hypotheses/taken/{CARD_ID}.yaml"
DONE = f"research/hypotheses/done/{CARD_ID}.yaml"
LOOPS = f"research/loops/{CARD_ID}/"


def _by_number(results: list[CheckResult]) -> dict[int, CheckResult]:
    return {check.number: check for check in results}


def _failed(results: list[CheckResult]) -> set[int]:
    return {check.number for check in results if check.status is Status.FAIL}


@pytest.fixture
def base(tmp_path: Path) -> dict[str, bytes]:
    return ledgers(tmp_path)


# --- 定型 PR の 3 種類が通る ------------------------------------------------------------


def _cards_pr(base: Mapping[str, bytes], **override: object) -> CheckInput:
    head = dict(base, **{QUEUE: card_bytes()})
    options: dict[str, object] = {
        "branch": "runner/cards-20261120T030000Z",
        "title": "[runner] 事前登録 cards-20261120T030000Z",
        "kind": "事前登録",
        "version": None,
        "auto_merge": True,
    }
    options.update(override)
    return make_input(base, head, **options)  # type: ignore[arg-type]


def test_a_cards_only_routine_pr_with_auto_merge_passes(base: dict[str, bytes]) -> None:
    results = run_checks(_cards_pr(base))
    assert _failed(results) == set(), [check.messages for check in results]
    assert _by_number(results)[4].status is Status.NOT_APPLICABLE


def _exec_pr(base: dict[str, bytes], **override: object) -> CheckInput:
    base = dict(base, **{TAKEN: card_bytes()})
    head = dict(base, **{TRIAL: trial_ledger(4), f"{LOOPS}run_v1.yaml": run_record()})
    options: dict[str, object] = {
        "branch": "runner/exec",
        "title": f"[runner] 実行記録 {CARD_ID} v1",
        "kind": "実行記録",
        "experiment": EXPERIMENT,
        "auto_merge": True,
    }
    options.update(override)
    return make_input(base, head, **options)  # type: ignore[arg-type]


def test_an_execution_record_routine_pr_passes(base: dict[str, bytes]) -> None:
    results = run_checks(_exec_pr(base))
    assert _failed(results) == set(), [check.messages for check in results]


def test_a_report_routine_pr_moves_the_card_to_done(base: dict[str, bytes]) -> None:
    raw = card_bytes()
    before = dict(base, **{TAKEN: raw})
    head = dict(base, **{DONE: raw, DONE.replace(".yaml", ".outcome.yaml"): outcome()})
    head[f"{LOOPS}report.yaml"] = report()
    given = make_input(
        before,
        head,
        branch=f"runner/report-{CARD_ID}",
        title=f"[runner] 一巡の報告 {CARD_ID} v2",
        kind="一巡の報告",
        version=2,
        experiment=EXPERIMENT,
        auto_merge=True,
    )
    results = run_checks(given)
    assert _failed(results) == set(), [check.messages for check in results]


def _prereg_pr(base: dict[str, bytes]) -> CheckInput:
    raw = card_bytes()
    before = dict(base, **{QUEUE: raw})
    generated = generated_files(raw)
    head = dict(
        base, **{TAKEN: raw, **generated, f"{LOOPS}prereg_v1.yaml": prereg_note(raw, generated)}
    )
    return make_input(
        before,
        head,
        branch=f"runner/prereg-{CARD_ID}-v1",
        title=f"[runner] 事前登録 {CARD_ID} v1",
        kind="事前登録",
        experiment=EXPERIMENT,
        auto_merge=True,
    )


def test_a_prereg_routine_pr_fails_while_regeneration_is_not_enabled(
    base: dict[str, bytes],
) -> None:
    results = _by_number(run_checks(_prereg_pr(base)))
    assert {number for number, check in results.items() if check.status is Status.FAIL} == {4, 8}
    assert any("未有効" in message for message in results[4].messages)


def _same_as_head(given: CheckInput) -> object:
    def regenerate(
        *, card: bytes, change: bytes | None, version: int, head: Snapshot
    ) -> dict[str, bytes]:
        del change, version
        return {path: given.head.read(path) or b"" for path in generated_files(card)}

    return regenerate


def test_a_prereg_routine_pr_passes_once_the_generator_is_in(base: dict[str, bytes]) -> None:
    given = _prereg_pr(base)
    results = run_checks(given, _same_as_head(given))  # type: ignore[arg-type]
    assert _failed(results) == set(), [check.messages for check in results]


def test_a_hand_edited_generated_file_fails_the_regeneration(base: dict[str, bytes]) -> None:
    given = _prereg_pr(base)

    def regenerate(
        *, card: bytes, change: bytes | None, version: int, head: Snapshot
    ) -> dict[str, bytes]:
        del change, version, head
        return {path: data + b"# differs\n" for path, data in generated_files(card).items()}

    results = _by_number(run_checks(given, regenerate))
    assert results[4].status is Status.FAIL
    assert any("バイト単位で一致しない" in message for message in results[4].messages)


def test_a_card_rewritten_after_the_note_fails_the_hash_check(base: dict[str, bytes]) -> None:
    given = _prereg_pr(base)
    head = dict(given.head.files)  # type: ignore[attr-defined]
    head[TAKEN] = card_bytes(inspiration="書き換えた着想")
    rewritten = make_input(
        dict(given.base.files),  # type: ignore[union-attr]
        head,
        branch=f"runner/prereg-{CARD_ID}-v1",
        title=f"[runner] 事前登録 {CARD_ID} v1",
        kind="事前登録",
        experiment=EXPERIMENT,
    )
    results = _by_number(run_checks(rewritten, _same_as_head(rewritten)))  # type: ignore[arg-type]
    assert results[4].status is Status.FAIL
    assert any("card_sha256" in message for message in results[4].messages)


# --- 許されないパス（AU3）-------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "src/odyssey_fx/app/cli/main.py",
        "docs/design/D25_research_runner_and_queue.md",
        "configs/policies/research/registry.yaml",
        ".github/workflows/ci.yml",
        "research/candidate_fixes/x/1/1.yaml",
        ".github/workflows/ledger.yml",
        "tools/ops/runner_ci/routine.py",
    ],
)
def test_a_routine_pr_with_a_path_outside_its_kind_fails(base: dict[str, bytes], path: str) -> None:
    clean = _exec_pr(base)
    head = dict(clean.head.files, **{path: b"changed\n"})  # type: ignore[attr-defined]
    given = make_input(
        dict(clean.base.files),  # type: ignore[union-attr]
        head,
        branch="runner/exec",
        title=f"[runner] 実行記録 {CARD_ID} v1",
        kind="実行記録",
        experiment=EXPERIMENT,
        auto_merge=True,
    )
    results = _by_number(run_checks(given))
    assert results[3].status is Status.FAIL
    assert any(path in message for message in results[3].messages)
    assert results[8].status is Status.FAIL


def test_a_routine_pr_may_not_change_a_frozen_card(base: dict[str, bytes]) -> None:
    clean = _exec_pr(base)
    head = dict(clean.head.files, **{TAKEN: card_bytes(inspiration="書き換えた")})  # type: ignore[attr-defined]
    given = make_input(
        dict(clean.base.files),  # type: ignore[union-attr]
        head,
        branch="runner/exec",
        title=f"[runner] 実行記録 {CARD_ID} v1",
        kind="実行記録",
        experiment=EXPERIMENT,
    )
    assert 3 in _failed(run_checks(given))


def test_a_routine_pr_may_not_add_a_human_card(base: dict[str, bytes]) -> None:
    head = dict(base, **{f"research/hypotheses/queue/{HUMAN_CARD_ID}.yaml": card_bytes("human")})
    given = make_input(
        base,
        head,
        branch="runner/cards-20261120T030000Z",
        title="[runner] 事前登録 cards-20261120T030000Z",
        kind="事前登録",
        card_line=HUMAN_CARD_ID,
        version=None,
    )
    results = _by_number(run_checks(given))
    assert results[3].status is Status.FAIL


def test_a_non_routine_pr_may_add_a_human_card_but_not_an_ai_card(base: dict[str, bytes]) -> None:
    human = dict(base, **{f"research/hypotheses/queue/{HUMAN_CARD_ID}.yaml": card_bytes("human")})
    assert _failed(run_checks(make_input(base, human, branch="docs/card"))) == set()
    ai = dict(base, **{QUEUE: card_bytes()})
    results = _by_number(run_checks(make_input(base, ai, branch="docs/card")))
    assert results[3].status is Status.FAIL
    assert any("mouth: ai" in message for message in results[3].messages)


def test_a_non_routine_pr_may_not_touch_taken_or_done(base: dict[str, bytes]) -> None:
    before = dict(base, **{TAKEN: card_bytes()})
    head = dict(base, **{TAKEN: card_bytes(inspiration="書き換えた")})
    assert 3 in _failed(run_checks(make_input(before, head, branch="feat/x")))
    removed = dict(base)
    assert 3 in _failed(run_checks(make_input(before, removed, branch="feat/x")))


# --- 記録の書式・自由文の数字（AU3）--------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prediction", "上昇の局面で 3 回"),
        ("inspiration", "順１０の記録"),
        ("prediction", "Ⅱ の局面"),
    ],
)
def test_digits_in_free_text_fail(base: dict[str, bytes], field: str, value: str) -> None:
    head = dict(base, **{QUEUE: card_bytes(**{field: value})})
    given = make_input(
        base,
        head,
        branch="runner/cards-20261120T030000Z",
        title="[runner] 事前登録 cards-20261120T030000Z",
        kind="事前登録",
        version=None,
        auto_merge=True,
    )
    results = _by_number(run_checks(given))
    assert results[6].status is Status.FAIL
    assert any(f"`{field}`" in message for message in results[6].messages)
    assert results[8].status is Status.FAIL


def test_digits_in_report_text_fail(base: dict[str, bytes]) -> None:
    raw = card_bytes()
    before = dict(base, **{TAKEN: raw})
    head = dict(base, **{DONE: raw, DONE.replace(".yaml", ".outcome.yaml"): outcome()})
    head[f"{LOOPS}report.yaml"] = report(prediction="勝率が 5 割を超える")
    given = make_input(
        before,
        head,
        branch=f"runner/report-{CARD_ID}",
        title=f"[runner] 一巡の報告 {CARD_ID} v2",
        kind="一巡の報告",
        version=2,
        experiment=EXPERIMENT,
    )
    assert 6 in _failed(run_checks(given))


def test_numbers_in_the_rule_must_be_parameters_or_grid_values(base: dict[str, bytes]) -> None:
    ok = dict(base, **{QUEUE: card_bytes(rule="期間 10 と 20 の EMA の交差で入る")})
    bad = dict(base, **{QUEUE: card_bytes(rule="期間 15 の EMA の交差で入る")})
    options = {
        "branch": "runner/cards-20261120T030000Z",
        "title": "[runner] 事前登録 cards-20261120T030000Z",
        "kind": "事前登録",
        "version": None,
    }
    assert 6 not in _failed(run_checks(make_input(base, ok, **options)))  # type: ignore[arg-type]
    assert 6 in _failed(run_checks(make_input(base, bad, **options)))  # type: ignore[arg-type]


# --- rule の数: CI と仮説カードの道具の判定を揃える（D25 §3.1・§6.3 の 6、仮置き 6） ------------

_D04_CARD_ID = "a-20261120-dual-ema"


def _d04_card(
    rule: str, *, series: str | None = None, version: int | None = None
) -> dict[str, Any]:
    """D04 の形の部品を持つ人工のカード（仮説カードの道具の試験と同じもの）。`series` は速い EMA が
    読む系列の足の名前を、`version` は速い EMA の部品の版を差し替える。"""
    data = card_data(mouth="ai")
    data["rule"] = rule
    fast = data["strategy"]["components"][0]
    if series is not None:
        name = f"GBPUSD/{series}/bid"
        fast["inputs"] = {"prices": [f"market:{name}:CLOSE"]}
        fast["triggers"] = [{"name": "bar", "when": "bar_close", "series": name}]
    if version is not None:
        fast["component_version"] = version
    return data


def _ci_rule_messages(base: Mapping[str, bytes], data: dict[str, Any]) -> list[str]:
    head = dict(base, **{f"research/hypotheses/queue/{_D04_CARD_ID}.yaml": hc_card_bytes(data)})
    given = make_input(
        base,
        head,
        branch="runner/cards-20261120T030000Z",
        title="[runner] 事前登録 cards-20261120T030000Z",
        kind="事前登録",
        version=None,
    )
    return [message for message in _by_number(run_checks(given))[6].messages if "`rule`" in message]


def _tool_rule_problems(data: dict[str, Any]) -> list[str]:
    try:
        parse_card(hc_card_bytes(data))
    except CardFormatError as error:
        return [problem for problem in error.problems if "`rule`" in problem]
    return []


@pytest.mark.parametrize(
    ("rule", "series", "version"),
    [
        ("15 分足の短い EMA が長い EMA を上抜けたら買う規則", "15m", None),
        ("1 時間足の短い EMA が長い EMA を上抜けたら買う規則", None, None),
        ("版 2 の EMA が長い EMA を上抜けたら買う規則", None, 2),
    ],
)
def test_numbers_only_in_series_names_or_versions_fail_the_rule_check(
    base: dict[str, bytes], rule: str, series: str | None, version: int | None
) -> None:
    assert _ci_rule_messages(base, _d04_card(rule, series=series, version=version))


def test_numbers_in_parameter_values_and_grid_values_pass_the_rule_check(
    base: dict[str, bytes],
) -> None:
    rule = "期間 20 の EMA が期間 50 か 100 の EMA を上抜けたら買い、利確は損切りの幅の 1.5 か 3 倍"
    assert _ci_rule_messages(base, _d04_card(rule)) == []


@pytest.mark.parametrize(
    ("rule", "series", "version"),
    [
        ("期間 20 の EMA が期間 50 か 100 の EMA を上抜けたら買う規則", None, None),
        ("期間 ２０ の EMA が上抜けたら買う規則", None, None),
        ("損切りの幅の 1.50 倍に利確を置く規則", None, None),
        ("期間 21 の EMA が上抜けたら買う規則", None, None),
        ("15 分足の短い EMA が長い EMA を上抜けたら買う規則", "15m", None),
        ("1 時間足の短い EMA が長い EMA を上抜けたら買う規則", None, None),
        ("版 2 の EMA が長い EMA を上抜けたら買う規則", None, 2),
        ("Ⅱ の局面で買う規則", None, None),
    ],
)
def test_the_ci_and_the_card_tool_judge_the_rule_numbers_alike(
    base: dict[str, bytes], rule: str, series: str | None, version: int | None
) -> None:
    data = _d04_card(rule, series=series, version=version)
    assert bool(_ci_rule_messages(base, data)) == bool(_tool_rule_problems(data))


@pytest.mark.parametrize(
    "raw",
    [
        card_bytes(id="a-20261121-ema-pullback"),
        card_bytes(colour="赤"),
        card_bytes(mouth="robot"),
        card_bytes(symbol="EURCHF"),
        card_bytes(evidence_estimate={"frequency_band": "SOMETIMES", "reaches_floor": "LIKELY"}),
        card_bytes().replace(b"'2026-11-20T03:00:00Z'", b"2026-11-20T03:00:00Z"),
        card_bytes() + b"mouth: ai\n",
        b"schema_version: 1\nid: [\n",
    ],
)
def test_a_malformed_card_fails_the_format_check(base: dict[str, bytes], raw: bytes) -> None:
    head = dict(base, **{QUEUE: raw})
    results = _by_number(run_checks(make_input(base, head, branch="docs/x")))
    assert results[5].status is Status.FAIL


def test_the_same_card_in_two_places_fails(base: dict[str, bytes]) -> None:
    before = dict(base, **{TAKEN: card_bytes()})
    head = dict(before, **{QUEUE: card_bytes()})
    results = _by_number(
        run_checks(make_input(before, head, branch="runner/cards-20261120T030000Z"))
    )
    assert results[5].status is Status.FAIL


def test_an_unknown_file_under_the_record_places_fails(base: dict[str, bytes]) -> None:
    head = dict(base, **{f"{LOOPS}notes.md": b"memo\n"})
    assert 5 in _failed(run_checks(make_input(base, head, branch="docs/x")))


# --- 題と本文の型 ----------------------------------------------------------------------


def test_a_free_sentence_in_the_body_fails(base: dict[str, bytes]) -> None:
    clean = _exec_pr(base)
    text = clean.pull_request.body + "勝率は上がった\n"  # type: ignore[union-attr]
    results = _by_number(run_checks(_exec_pr(base, body_text=text)))
    assert results[7].status is Status.FAIL
    assert results[8].status is Status.FAIL


def test_a_wrong_hash_or_a_missing_fixed_sentence_fails(base: dict[str, bytes]) -> None:
    clean = _exec_pr(base).pull_request
    assert clean is not None
    hash_line = next(line for line in clean.body.splitlines() if line.startswith("- "))
    wrong = clean.body.replace(hash_line, hash_line[:-64] + "0" * 64)
    results = _by_number(run_checks(_exec_pr(base, body_text=wrong)))
    assert any("SHA-256 がファイルと違う" in message for message in results[7].messages)
    missing = "\n".join(line for line in clean.body.splitlines() if "ADR-0043" not in line)
    assert 7 in _failed(run_checks(_exec_pr(base, body_text=missing)))


def test_the_title_must_agree_with_the_branch(base: dict[str, bytes]) -> None:
    results = _by_number(run_checks(_exec_pr(base, title=f"[runner] 一巡の報告 {CARD_ID} v1")))
    assert results[2].status is Status.FAIL


def test_an_unknown_runner_branch_fails(base: dict[str, bytes]) -> None:
    results = _by_number(run_checks(_exec_pr(base, branch="runner/anything")))
    assert results[2].status is Status.FAIL


# --- 自動 merge の守り（AU3）---------------------------------------------------------------


def test_a_design_pr_with_auto_merge_fails(base: dict[str, bytes]) -> None:
    head = dict(base, **{"docs/design/D99_example.md": b"# example\n"})
    without = _by_number(run_checks(make_input(base, head, branch="docs/d99")))
    assert _failed(list(without.values())) == set()
    with_auto = _by_number(run_checks(make_input(base, head, branch="docs/d99", auto_merge=True)))
    assert with_auto[8].status is Status.FAIL
    assert _failed(list(with_auto.values())) == {8}


def test_a_fixation_pr_with_auto_merge_fails(base: dict[str, bytes]) -> None:
    head = dict(base, **{"research/candidate_fixes/c_x/1/1.yaml": dump({"schema_version": 1})})
    given = make_input(base, head, branch="candidate-fix/c_x-1", auto_merge=True)
    results = _by_number(run_checks(given))
    assert results[8].status is Status.FAIL
    assert any("固定の記録" in message for message in results[8].messages)
    assert _failed(run_checks(make_input(base, head, branch="candidate-fix/c_x-1"))) == set()


def test_push_runs_only_the_ledger_check(base: dict[str, bytes]) -> None:
    results = run_checks(make_input(base, base))
    assert results[0].status is Status.PASS
    assert {check.status for check in results[1:]} == {Status.NOT_APPLICABLE}


# --- 検証の正規表現は文字列の全体に当てる（Codex 1 巡目）----------------------------------


def test_a_hash_with_a_trailing_newline_fails_the_format_check(base: dict[str, bytes]) -> None:
    bad = run_record().replace(b"sha256: " + b"c" * 64, b'sha256: "' + b"c" * 64 + b'\\n"')
    assert bad != run_record()
    clean = _exec_pr(base)
    head = dict(clean.head.files, **{f"{LOOPS}run_v1.yaml": bad})  # type: ignore[attr-defined]
    given = make_input(
        dict(clean.base.files),  # type: ignore[union-attr]
        head,
        branch="runner/exec",
        title=f"[runner] 実行記録 {CARD_ID} v1",
        kind="実行記録",
        experiment=EXPERIMENT,
        auto_merge=True,
    )
    results = _by_number(run_checks(given))
    assert results[5].status is Status.FAIL
    assert results[8].status is Status.FAIL


@pytest.mark.parametrize(
    "override",
    [
        {"created_at": "2026-11-20T03:00:00Z\n"},
        {"created_at": "２０２６-11-20T03:00:00Z"},
        {"title": "ema-pullback\n"},
    ],
)
def test_trailing_newlines_and_non_ascii_digits_fail(
    base: dict[str, bytes], override: dict[str, str]
) -> None:
    head = dict(
        base, **{f"research/hypotheses/queue/{HUMAN_CARD_ID}.yaml": card_bytes("human", **override)}
    )
    assert 5 in _failed(run_checks(make_input(base, head, branch="docs/card")))


def test_a_title_with_a_trailing_newline_is_not_a_routine_title(base: dict[str, bytes]) -> None:
    results = _by_number(run_checks(_exec_pr(base, title=f"[runner] 実行記録 {CARD_ID} v1\n")))
    assert results[2].status is Status.FAIL


# --- 文字列の項目は閉じた形か、数字の検査を当てる自由文（Codex 2 巡目）------------------------


@pytest.mark.parametrize(
    ("favored", "fails"),
    [
        (["UP_HIGH", "FLAT_LOW"], False),
        (["SHARPE 1.75"], True),
        (["UNLABELED"], True),
        (["UP_HIGH", "UP_HIGH"], True),
        ([], True),
    ],
)
def test_favored_regimes_must_be_regime_names(
    base: dict[str, bytes], favored: list[str], fails: bool
) -> None:
    head = dict(base, **{QUEUE: card_bytes(favored_regimes=favored)})
    given = make_input(
        base,
        head,
        branch="runner/cards-20261120T030000Z",
        title="[runner] 事前登録 cards-20261120T030000Z",
        kind="事前登録",
        version=None,
        auto_merge=True,
    )
    results = _by_number(run_checks(given))
    assert (results[5].status is Status.FAIL) is fails
    assert (results[8].status is Status.FAIL) is fails
    assert not any("SHARPE" in message for message in results[5].messages)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"record_id: " + b"e" * 64, b"record_id: SHARPE 1.75"),
        (b"artifacts:", b"artifacts: []\nunused:"),
    ],
)
def test_run_record_strings_are_closed_forms(
    base: dict[str, bytes], old: bytes, new: bytes
) -> None:
    bad = run_record().replace(old, new)
    assert bad != run_record()
    clean = _exec_pr(base)
    head = dict(clean.head.files, **{f"{LOOPS}run_v1.yaml": bad})  # type: ignore[attr-defined]
    given = make_input(
        dict(clean.base.files),  # type: ignore[union-attr]
        head,
        branch="runner/exec",
        title=f"[runner] 実行記録 {CARD_ID} v1",
        kind="実行記録",
        experiment=EXPERIMENT,
    )
    assert 5 in _failed(run_checks(given))


def test_axis_names_are_identifiers(base: dict[str, bytes]) -> None:
    axes = {"axes": [{"instance": "fast ema", "parameter": "period", "values": ["10", "20"]}]}
    head = dict(
        base, **{f"research/hypotheses/queue/{HUMAN_CARD_ID}.yaml": card_bytes("human", grid=axes)}
    )
    assert 5 in _failed(run_checks(make_input(base, head, branch="docs/card")))


# --- 設計適合レビューの反映 ------------------------------------------------------------


@pytest.mark.parametrize("item", ["実験", "件数"])
def test_the_body_needs_the_experiment_and_the_counts(base: dict[str, bytes], item: str) -> None:
    clean = _exec_pr(base).pull_request
    assert clean is not None
    text = "\n".join(line for line in clean.body.splitlines() if not line.startswith(f"{item}: "))
    results = _by_number(run_checks(_exec_pr(base, body_text=text)))
    assert any(f"「{item}」が無い" in message for message in results[7].messages)


@pytest.mark.parametrize(
    ("rule", "fails"),
    [
        ("期間 ⑤ の EMA", True),
        ("期間 Ⅱ の EMA", True),
        ("期間 ١٠ の EMA", True),
        ("期間 １０ の EMA", False),
        ("期間十の EMA", False),
    ],
)
def test_rule_digits_use_the_same_classes_as_free_text(
    base: dict[str, bytes], rule: str, fails: bool
) -> None:
    head = dict(
        base, **{f"research/hypotheses/queue/{HUMAN_CARD_ID}.yaml": card_bytes("human", rule=rule)}
    )
    assert (6 in _failed(run_checks(make_input(base, head, branch="docs/card")))) is fails


@pytest.mark.parametrize(
    "path",
    [
        "research/hypotheses/README.md",
        "research/hypotheses/queue/.gitkeep",
        "research/hypotheses/done/.gitkeep",
        "research/loops/README.md",
    ],
)
def test_place_readmes_and_keep_files_are_not_records(base: dict[str, bytes], path: str) -> None:
    head = dict(base, **{path: b"# place\n"})
    assert _failed(run_checks(make_input(base, head, branch="docs/places"))) == set()


def test_a_readme_inside_a_card_folder_is_still_checked(base: dict[str, bytes]) -> None:
    head = dict(base, **{f"{LOOPS}README.md": b"# memo\n"})
    assert 5 in _failed(run_checks(make_input(base, head, branch="docs/places")))


@pytest.mark.parametrize(
    ("name", "allowed"),
    [(f"{EXPERIMENT}_v2.yaml", True), (f"{EXPERIMENT}.yaml", False)],
)
def test_an_improved_version_writes_its_own_experiment_file(
    base: dict[str, bytes], name: str, allowed: bool
) -> None:
    """改善の版 n の実験設定は `<実験>_v<n>.yaml`（PR 7 の `experiment_path`。PR 8a で合わせた）。

    版 2 の事前登録の PR で版 1 の置き場を変えると落ちる。
    """
    from tools.ops.runner_ci.routine import check_paths, parse_routine

    raw = card_bytes()
    before = dict(base, **{TAKEN: raw})
    head = dict(
        before,
        **{
            f"configs/experiments/{name}": b"id: x\n",
            f"{LOOPS}prereg_v2.yaml": b"x: 1\n",
            f"{LOOPS}change_v2.yaml": b"x: 1\n",
        },
    )
    given = make_input(
        before,
        head,
        branch=f"runner/prereg-{CARD_ID}-v2",
        title=f"[runner] 事前登録 {CARD_ID} v2",
        kind="事前登録",
        version=2,
        experiment=EXPERIMENT,
        auto_merge=True,
    )
    assert given.pull_request is not None
    routine = parse_routine(given.pull_request)
    result = check_paths(given, routine)
    assert (result.status is Status.PASS) is allowed, result.messages
