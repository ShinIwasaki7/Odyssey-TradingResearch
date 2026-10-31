"""採用審査の規則の単体テスト（D16 §3〜§9。受入テスト T2〜T7c・T13・T14 の規則の層）。

入力はすべて人工の値（試行台帳の行・完了取引の記録・登録簿の要素）。実データも成績も使わない。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from odyssey_fx.app.config import load_calendar
from odyssey_fx.app.config.adoption_review import load_adoption_review_rules
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import CurrencyCode
from odyssey_fx.common.refs import PolicyRef
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.adoption_review import (
    AdoptionReviewLine,
    Capability,
    CostFitResult,
    CostFitRow,
    CostModelRow,
    DeclarationStatus,
    EntranceAccepted,
    EntranceRejected,
    FitOutcome,
    FloorOutcome,
    ReviewDefect,
    ReviewReason,
    ReviewResult,
    ReviewRules,
    ReviewRulesEntry,
    applicable_rules_entry,
    check_entrance,
    combine_result,
    cost_model_fit,
    evidence_floor,
    ledger_tally,
    review_ledger_defect,
)
from odyssey_fx.evaluation.domain.experiment import ExperimentStatus
from odyssey_fx.evaluation.domain.metrics import TradeRecord
from odyssey_fx.evaluation.domain.research_policy import RegistryEntry
from odyssey_fx.evaluation.domain.search import (
    ChangeKind,
    Lineage,
    LineageSource,
    SearchVerdict,
    StandardPurpose,
    TrialLedgerEntry,
    TrialLedgerLine,
)
from odyssey_fx.evaluation.domain.variant import Paired
from odyssey_fx.marketdata.domain.calendar import TradingCalendar, WeeklyMoment
from tests.fixtures.evaluation.adoption_review import (
    REPO_ROOT,
    review_entry,
    review_lines,
    rules_ref,
    trade,
)
from tests.fixtures.evaluation.trial_ledger import chain, experiment_id, finished, started

_POLICY = PolicyRef(policy_kind="research", policy_id="research", version=3, digest=digest("p3"))
_REGISTRY = (
    RegistryEntry(
        policy_id="research", version=3, digest=digest("p3"), purpose=StandardPurpose.STANDARD
    ),
)
_RULES_V1 = ReviewRulesEntry(
    rules_id="adoption_review", version=1, digest=digest("rules"), effective_after=None
)


def _rules() -> ReviewRules:
    rules, _ = load_adoption_review_rules(
        REPO_ROOT / "configs/policies/adoption_review/adoption_review_v1.yaml", version=1
    )
    return rules


def _calendar() -> TradingCalendar:
    return load_calendar(REPO_ROOT / "configs/calendars/fx_ny17_oanda_v1.yaml")


def _with_policy(entry: TrialLedgerEntry, policy: PolicyRef = _POLICY) -> TrialLedgerEntry:
    return replace(
        entry,
        basis=entry.basis.map(lambda _, item: replace(item, research_policy_ref=policy)),
    )


def _candidate_ledger(
    *, verdict: SearchVerdict = SearchVerdict.MEETS_STANDARD, name: str = "a"
) -> list[TrialLedgerLine]:
    opening = _with_policy(started(name, purpose=StandardPurpose.STANDARD))
    return chain([opening, finished(opening, verdict=verdict)])


def _entrance(
    lines: Sequence[TrialLedgerLine],
    reviews: Sequence[AdoptionReviewLine] = (),
    registry: Sequence[RegistryEntry] = _REGISTRY,
    rules: Sequence[ReviewRulesEntry] = (_RULES_V1,),
    name: str = "a",
) -> EntranceAccepted | EntranceRejected:
    return check_entrance(
        trial_lines=lines,
        policy_registry=registry,
        review_lines=reviews,
        rules_registry=rules,
        experiment_id=experiment_id(name),
        execution=1,
    )


# --- 入口の検査（T2 の規則の層。D16 §3.1）----------------------------------------------


def test_a_candidate_passes_the_entrance() -> None:
    accepted = _entrance(_candidate_ledger())
    assert isinstance(accepted, EntranceAccepted)
    assert accepted.rules_entry == _RULES_V1
    assert accepted.research_policy_ref == _POLICY


def test_entrance_order_1_not_in_ledger() -> None:
    assert _entrance(_candidate_ledger(), name="zzz") == EntranceRejected(
        ReviewReason.ENTRY_NOT_IN_LEDGER
    )


def test_entrance_order_2_not_finished() -> None:
    lines = _candidate_ledger()[:1]
    assert _entrance(lines) == EntranceRejected(ReviewReason.ENTRY_NOT_FINISHED)


def test_entrance_order_3_status_not_completed() -> None:
    opening = _with_policy(started(purpose=StandardPurpose.STANDARD))
    closing = finished(
        opening, verdict=SearchVerdict.BELOW_STANDARD, status=ExperimentStatus.FAILED_POST_RUN_CHECK
    )
    assert _entrance(chain([opening, closing])) == EntranceRejected(
        ReviewReason.ENTRY_STATUS_NOT_COMPLETED
    )


def test_entrance_order_4_purpose_not_standard() -> None:
    opening = _with_policy(started(purpose=StandardPurpose.MECHANISM_CHECK))
    closing = finished(opening, verdict=SearchVerdict.MET_IN_MECHANISM_CHECK)
    assert _entrance(chain([opening, closing])) == EntranceRejected(
        ReviewReason.ENTRY_PURPOSE_NOT_STANDARD
    )


@pytest.mark.parametrize(
    "verdict",
    [
        SearchVerdict.BELOW_STANDARD,
        SearchVerdict.INSUFFICIENT_EVIDENCE,
        SearchVerdict.INCOMPLETE,
    ],
)
def test_entrance_order_5_verdict_not_meets_standard(verdict: SearchVerdict) -> None:
    assert _entrance(_candidate_ledger(verdict=verdict)) == EntranceRejected(
        ReviewReason.ENTRY_VERDICT_NOT_MEETS_STANDARD
    )


def test_entrance_order_6_policy_not_current() -> None:
    newer = (
        *_REGISTRY,
        RegistryEntry(
            policy_id="research", version=4, digest=digest("p4"), purpose=StandardPurpose.STANDARD
        ),
    )
    assert _entrance(_candidate_ledger(), registry=newer) == EntranceRejected(
        ReviewReason.ENTRY_POLICY_NOT_CURRENT
    )
    other_digest = (replace(_REGISTRY[0], digest=digest("changed")),)
    assert _entrance(_candidate_ledger(), registry=other_digest) == EntranceRejected(
        ReviewReason.ENTRY_POLICY_NOT_CURRENT
    )


@pytest.mark.parametrize("result", [ReviewResult.PROCEED, ReviewResult.DO_NOT_PROCEED])
def test_entrance_order_7_already_reviewed(result: ReviewResult) -> None:
    reviews = review_lines([review_entry(experiment_id("a"), result=result, ref=_RULES_V1.ref)])
    assert _entrance(_candidate_ledger(), reviews) == EntranceRejected(
        ReviewReason.ENTRY_ALREADY_REVIEWED
    )


def test_a_deferred_review_is_retried_with_the_rules_of_its_first_line() -> None:
    """保留の後のやり直しには最初の行の版が当たる（仮置き 12。D16 §9.3。T7b の規則の層）。"""
    v2 = ReviewRulesEntry(
        rules_id="adoption_review", version=2, digest=digest("v2"), effective_after=None
    )
    reviews = review_lines([review_entry(experiment_id("a"), ref=_RULES_V1.ref)])
    accepted = _entrance(_candidate_ledger(), reviews, rules=(_RULES_V1, replace(v2)))
    assert isinstance(accepted, EntranceAccepted)
    assert accepted.rules_entry.version == 1


def test_entrance_order_8_rules_version_changed() -> None:
    reviews = review_lines([review_entry(experiment_id("a"), ref=rules_ref(1, "older"))])
    assert _entrance(_candidate_ledger(), reviews) == EntranceRejected(
        ReviewReason.ENTRY_RULES_VERSION_CHANGED
    )
    gone = review_lines([review_entry(experiment_id("a"), ref=rules_ref(5, "rules"))])
    assert _entrance(_candidate_ledger(), gone) == EntranceRejected(
        ReviewReason.ENTRY_RULES_VERSION_CHANGED
    )


# --- 当てる規則の版（R1-10 (a)。T7c の規則の層）-------------------------------------------


def test_a_new_rules_version_applies_only_to_executions_started_after_its_registration() -> None:
    first = _with_policy(started("a", purpose=StandardPurpose.STANDARD))
    lines = chain([first, finished(first, verdict=SearchVerdict.MEETS_STANDARD)])
    v2 = ReviewRulesEntry(
        rules_id="adoption_review", version=2, digest=digest("v2"), effective_after=lines[-1].digest
    )
    second = _with_policy(started("b", purpose=StandardPurpose.STANDARD))
    lines = chain([first, finished(first, verdict=SearchVerdict.MEETS_STANDARD), second])
    registry = (_RULES_V1, v2)
    assert applicable_rules_entry(lines, registry, lines[0]).version == 1
    assert applicable_rules_entry(lines, registry, lines[2]).version == 2


def test_a_rules_version_whose_start_line_is_not_in_the_ledger_is_a_registry_error() -> None:
    lines = _candidate_ledger()
    v2 = ReviewRulesEntry(
        rules_id="adoption_review", version=2, digest=digest("v2"), effective_after=digest("x")
    )
    with pytest.raises(KernelValueError):
        applicable_rules_entry(lines, (_RULES_V1, v2), lines[0])


# --- 台帳の累計（D16 §6。R1-9 (a)・仮置き 7）---------------------------------------------


def _derived(name: str, source: TrialLedgerEntry) -> TrialLedgerEntry:
    entry = _with_policy(started(name, purpose=StandardPurpose.STANDARD))
    return replace(
        entry,
        lineage=Lineage(
            derived_from=LineageSource(experiment_id=source.experiment_id, execution=1),
            change_kinds=(ChangeKind.PARAMETERS,),
            viewed_before=source.validation_intervals,
            note_digest=None,
        ),
    )


def test_the_ledger_tally_counts_the_whole_lineage_and_the_whole_ledger() -> None:
    root = _with_policy(started("root", purpose=StandardPurpose.STANDARD))
    left = _derived("left", root)
    right = _derived("right", root)
    later = replace(
        _with_policy(started("later", purpose=StandardPurpose.STANDARD)),
        strategy_id="other",
        validation_intervals=(
            Interval(
                start=UtcTime.parse("2016-01-01T00:00:00Z"),
                end=UtcTime.parse("2016-02-01T00:00:00Z"),
            ),
        ),
    )
    lines = chain([root, left, right, later])
    tally = ledger_tally(lines, lines[1])
    assert tally.lineage_executions == 3  # 根と兄弟の枝を含む（仮置き 7）
    assert tally.lineage_trials == 12
    assert tally.overlapping_executions == 3  # 重ならない later は数えない
    assert tally.overlapping_trials == 12
    assert tally.same_strategy_executions == 3
    assert (tally.prior_overlapping_executions, tally.prior_overlapping_trials) == (1, 4)


# --- 証拠の最低線（D16 §4。T3 の規則の層）--------------------------------------------------


def test_the_evidence_floor_compares_the_total_with_the_minimum() -> None:
    assert evidence_floor([3, 0, 2, 2, 1], 8) is FloorOutcome.MET
    assert evidence_floor([3, 0, 2, 1, 1], 8) is FloorOutcome.NOT_MET  # 1 件足りない
    assert evidence_floor([3, None, 2, 2, 1], 8) is FloorOutcome.UNDETERMINED
    assert evidence_floor([3, 0, 2, 2, 1], None) is FloorOutcome.UNDETERMINED


# --- 費用・約定モデルの適合（D16 §7。T4・T13 の規則の層）-------------------------------------

_WEEK = (
    Interval(
        start=UtcTime.parse("2015-01-04T22:00:00Z"), end=UtcTime.parse("2015-01-09T22:00:00Z")
    ),
)


def _fit(trades: Sequence[TradeRecord] | None, *, currency: str = "JPY") -> CostFitResult:
    return cost_model_fit(
        rules=_rules(),
        trades=trades,
        intervals=_WEEK,
        calendar=_calendar(),
        symbol=Symbol("USDJPY"),
        account_currency=CurrencyCode(currency),
    )


def _outcomes(result: CostFitResult) -> dict[CostModelRow, FitOutcome]:
    return {row.row: row.outcome for row in result.rows}


def test_an_intraday_trade_with_a_wide_move_fits_every_row() -> None:
    result = _fit([trade("2015-01-07T09:00:00Z", "2015-01-07T13:15:00Z")])
    assert set(_outcomes(result).values()) == {FitOutcome.FITS}
    assert [row.row for row in result.rows] == list(CostModelRow)
    # 0.02（入場のスプレッド）+ 0.01 + 0.01（滑り）÷ 0.50
    assert result.measures.fixed_cost_share == Decimal("0.08")
    assert result.measures.crossing_trades == 0
    assert result.measures.holding_median_trading_days == 0  # 同じ取引日の中で決済
    # 窓（週の開場から 2 時間）は 5 日の取引時間（120 時間）の 1/60
    assert result.measures.time_share is not None
    assert abs(result.measures.time_share - Decimal(2) / Decimal(120)) < Decimal("1e-20")


def test_row_2_2_a_heavy_fixed_cost_share_needs_spread_variation() -> None:
    result = _fit([trade("2015-01-07T09:00:00Z", "2015-01-07T13:15:00Z", exit_price="150.10")])
    rows = {row.row: row for row in result.rows}
    assert rows[CostModelRow.FIXED_SPREAD].outcome is FitOutcome.NEEDS_CAPABILITY
    assert rows[CostModelRow.FIXED_SPREAD].capabilities == (Capability.SPREAD_VARIATION,)


def test_row_2_1_fills_concentrated_after_the_weekly_open_need_spread_variation() -> None:
    # 2015-01-04T22:30Z はニューヨークの日曜 17:30（週の開場から 30 分）。
    result = _fit([trade("2015-01-04T22:30:00Z", "2015-01-05T03:00:00Z")])
    assert _outcomes(result)[CostModelRow.FIXED_SPREAD] is FitOutcome.NEEDS_CAPABILITY
    assert result.measures.window_fill_count == 1


def test_row_3_a_single_trade_across_the_day_end_needs_swap_rollover() -> None:
    # 2015-01-07T22:00Z がニューヨーク 17 時（取引日の終わり）。R1-5 (a): 1 件でも。
    result = _fit([trade("2015-01-07T20:00:00Z", "2015-01-08T01:00:00Z")])
    rows = {row.row: row for row in result.rows}
    assert rows[CostModelRow.SWAP_ROLLOVER].capabilities == (Capability.SWAP_ROLLOVER,)
    assert result.measures.crossing_trades == 1


_TWO_WEEKS = (
    Interval(
        start=UtcTime.parse("2015-01-04T22:00:00Z"), end=UtcTime.parse("2015-01-16T22:00:00Z")
    ),
)


def _swap_fit(trades: Sequence[TradeRecord], calendar: TradingCalendar | None) -> CostFitResult:
    return cost_model_fit(
        rules=_rules(),
        trades=trades,
        intervals=_TWO_WEEKS,
        calendar=calendar,
        symbol=Symbol("USDJPY"),
        account_currency=CurrencyCode("JPY"),
    )


def test_the_holding_median_counts_the_trading_day_ends_each_trade_crosses() -> None:
    """D16 §7.4（2026-11-01 決定）: 保有中にまたいだニューヨーク 17 時の回数の中央値。"""
    weekend = trade("2015-01-09T20:00:00Z", "2015-01-12T03:00:00Z")  # NY の金曜 15 時〜日曜 22 時
    three_days = trade("2015-01-05T10:00:00Z", "2015-01-07T23:00:00Z")  # 月・火・水の終わり
    intraday = trade("2015-01-13T09:00:00Z", "2015-01-13T13:15:00Z")
    # 週末（土日）は開場時間が無く取引日に数えない（D07 §5.5 の `N` と同じ）ので、週末またぎは 1。
    alone = _swap_fit([weekend], _calendar())
    assert alone.measures.holding_median_trading_days == 1
    result = _swap_fit([weekend, three_days, intraday], _calendar())
    assert result.measures.holding_median_trading_days == 1  # 回数 [1, 3, 0] の中央値
    assert result.measures.crossing_trades == 2
    even = _swap_fit([three_days, intraday], _calendar())
    assert even.measures.holding_median_trading_days == Decimal("1.5")  # 中央の 2 つの平均


def test_the_holding_median_is_missing_without_trading_day_ends() -> None:
    weekend = trade("2015-01-09T20:00:00Z", "2015-01-12T03:00:00Z")
    # 週の開場と閉場の時刻が違うと取引日の境界が 1 つに決まらない（取引日単位の休場は外す）。
    uneven = replace(_calendar(), weekly_close=WeeklyMoment(weekday=4, at=time(16, 0)), closures=())
    assert uneven.trading_day_boundary is None
    result = _swap_fit([weekend], uneven)
    assert _outcomes(result)[CostModelRow.SWAP_ROLLOVER] is FitOutcome.UNDETERMINED
    assert result.measures.holding_median_trading_days is None
    assert result.measures.crossing_trades is None
    unreadable = _swap_fit([weekend], None)
    assert unreadable.measures.holding_median_trading_days is None


def test_row_5_an_account_in_another_currency_needs_conversion() -> None:
    result = _fit([trade("2015-01-07T09:00:00Z", "2015-01-07T13:15:00Z")], currency="USD")
    assert _outcomes(result)[CostModelRow.CURRENCY_CONVERSION] is FitOutcome.NEEDS_CAPABILITY


def test_missing_spread_costs_count_as_zero_and_missing_slippage_is_undetermined() -> None:
    """T13: スプレッドの費用の値なし（売りの約定・保護水準での決済）は 0 として数える。"""
    no_spread = _fit(
        [
            trade(
                "2015-01-07T09:00:00Z",
                "2015-01-07T13:15:00Z",
                entry_spread=None,
                close_spread=None,
            )
        ]
    )
    assert _outcomes(no_spread)[CostModelRow.FIXED_SPREAD] is FitOutcome.FITS
    no_slippage = _fit([trade("2015-01-07T09:00:00Z", "2015-01-07T13:15:00Z", close_slippage=None)])
    assert _outcomes(no_slippage)[CostModelRow.FIXED_SPREAD] is FitOutcome.UNDETERMINED


def test_failed_runs_make_the_trade_rows_undetermined() -> None:
    outcomes = _outcomes(_fit(None))
    assert outcomes[CostModelRow.FIXED_SPREAD] is FitOutcome.UNDETERMINED
    assert outcomes[CostModelRow.SWAP_ROLLOVER] is FitOutcome.UNDETERMINED
    assert outcomes[CostModelRow.BAR_RESOLUTION] is FitOutcome.FITS  # 仮置き 9


# --- 結果の合わせ方（D16 §9.1。T5・T6・T14 の規則の層）---------------------------------------


def _rows(outcome: FitOutcome = FitOutcome.FITS) -> tuple[CostFitRow, ...]:
    def row(item: CostModelRow) -> CostFitRow:
        if item is CostModelRow.SWAP_ROLLOVER and outcome is not FitOutcome.FITS:
            capabilities = (
                (Capability.SWAP_ROLLOVER,) if outcome is FitOutcome.NEEDS_CAPABILITY else ()
            )
            return CostFitRow(row=item, outcome=outcome, capabilities=capabilities)
        return CostFitRow(row=item, outcome=FitOutcome.FITS, capabilities=())

    return tuple(row(item) for item in CostModelRow)


def _combine(
    floor: FloorOutcome = FloorOutcome.MET,
    fit: FitOutcome = FitOutcome.FITS,
    declaration: DeclarationStatus = DeclarationStatus.NOT_DECLARED,
) -> tuple[ReviewResult, tuple[ReviewReason, ...]]:
    return combine_result(
        floors=Paired(standard=FloorOutcome.MET, candidate=floor),
        fits=Paired(standard=_rows(), candidate=_rows(fit)),
        declaration=declaration,
    )


def test_everything_passing_proceeds() -> None:
    assert _combine() == (ReviewResult.PROCEED, ())


def test_a_definite_failure_wins_over_an_undetermined_check() -> None:
    """T14: 証拠の最低線の不合格と適合の検査の判定できないが同時なら進めない（両方の理由）。"""
    assert _combine(FloorOutcome.NOT_MET, FitOutcome.UNDETERMINED) == (
        ReviewResult.DO_NOT_PROCEED,
        (ReviewReason.EVIDENCE_FLOOR_NOT_MET, ReviewReason.COST_MODEL_UNDETERMINED),
    )


def test_needs_capability_and_a_declaration_do_not_proceed() -> None:
    assert _combine(fit=FitOutcome.NEEDS_CAPABILITY) == (
        ReviewResult.DO_NOT_PROCEED,
        (ReviewReason.COST_MODEL_NEEDS_CAPABILITY,),
    )
    assert _combine(declaration=DeclarationStatus.DECLARED) == (
        ReviewResult.DO_NOT_PROCEED,
        (ReviewReason.OUTSIDE_VIEWING_DECLARED,),
    )


def test_undetermined_checks_defer() -> None:
    assert _combine(declaration=DeclarationStatus.MISSING) == (
        ReviewResult.DEFERRED,
        (ReviewReason.DECLARATION_MISSING,),
    )
    assert _combine(FloorOutcome.UNDETERMINED) == (
        ReviewResult.DEFERRED,
        (ReviewReason.EVIDENCE_FLOOR_UNDETERMINED,),
    )


# --- 審査台帳の行の列（D16 §10.2・仮置き 12）---------------------------------------------


def test_the_review_ledger_keeps_one_final_line_per_execution() -> None:
    target = experiment_id("a")
    ok = review_lines([review_entry(target), review_entry(target, result=ReviewResult.PROCEED)])
    assert review_ledger_defect(ok) is None
    after = review_lines([review_entry(target, result=ReviewResult.PROCEED), review_entry(target)])
    assert review_ledger_defect(after) is not None
    assert review_ledger_defect(after)[0] is ReviewDefect.REVIEW_AFTER_FINAL  # type: ignore[index]
    changed = review_lines([review_entry(target), review_entry(target, ref=rules_ref(2))])
    assert review_ledger_defect(changed)[0] is ReviewDefect.RULES_REF_CHANGED  # type: ignore[index]


def test_review_rules_reject_values_the_decisions_did_not_choose() -> None:
    rules = _rules()
    with pytest.raises(KernelValueError):
        replace(rules, undetermined_result=ReviewResult.DO_NOT_PROCEED)
    with pytest.raises(KernelValueError):
        replace(rules, declared_fits=(CostModelRow.BAR_RESOLUTION,))
    assert rules.window_length == timedelta(hours=2)
    assert rules.max_concentration_ratio == Decimal("2")
    assert rules.max_fixed_cost_share == Decimal("0.25")


def test_the_fixture_root_is_the_repository() -> None:
    assert (REPO_ROOT / "pyproject.toml").is_file()
    assert isinstance(REPO_ROOT, Path)
