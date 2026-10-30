"""最終検証の規則（D09 §9.10.3・§9.10.6・§9.10.11）の単体テスト。人工の partition の事実だけ。"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.evaluation.domain.final_validation import (
    WINDOW_START,
    ApplicationState,
    FinalLedgerResult,
    FinalReason,
    FinalRunSummary,
    FinalWindow,
    SealedPartitionFact,
    WindowRefusal,
    combine_final,
    derive_application_state,
    derive_final_window,
    judge_final_variant,
)
from odyssey_fx.evaluation.domain.metrics import MetricId, MetricUnavailableReason, RatioValue
from odyssey_fx.evaluation.domain.search import (
    EvidenceAxis,
    ProfitabilityAxis,
    SearchVerdict,
    StandardPurpose,
)
from odyssey_fx.evaluation.domain.variant import VARIANTS, Variant
from odyssey_fx.marketdata.domain.access import AccessClass, HoldoutState, sealed_partition_period
from odyssey_fx.marketdata.domain.series import SeriesId
from odyssey_fx.marketdata.domain.snapshot import PartitionId
from tests.fixtures.evaluation.search_units import standard
from tests.fixtures.synthetic import market

H1 = market.series()
D1 = market.series(timeframe_id="1d")


def _weeks(count: int) -> list[Interval]:
    out: list[Interval] = []
    start = WINDOW_START
    for _ in range(count):
        period = sealed_partition_period(start)
        out.append(period)
        start = period.end
    return out


WEEKS = _weeks(12)
T_FIX = WEEKS[-1].end + timedelta(days=2)


def _facts(
    periods: list[Interval] = WEEKS,
    *,
    snapshot: str = "s",
    consumed: frozenset[int] = frozenset(),
    approved: str = "2026-11-20T00:00:00Z",
    digest: str = "d",
    series: tuple[SeriesId, ...] = (H1, D1),
) -> list[SealedPartitionFact]:
    out: list[SealedPartitionFact] = []
    for variant in VARIANTS:
        for item in series:
            for index, period in enumerate(periods):
                out.append(
                    SealedPartitionFact(
                        snapshot_id=f"{snapshot}-{variant.value}",
                        partition_id=PartitionId(
                            series=item,
                            access_class=AccessClass.HOLDOUT_SINCE_2024,
                            period_start=period.start,
                        ),
                        variant=variant,
                        digest=digest,
                        approved_at=UtcTime.parse(approved),
                        state=HoldoutState.CONSUMED if index in consumed else HoldoutState.SEALED,
                    )
                )
    return out


def _window(facts: list[SealedPartitionFact], **kwargs: object) -> FinalWindow | WindowRefusal:
    values: dict[str, object] = {
        "symbols": frozenset({"USDJPY"}),
        "t_fix": T_FIX,
        "excluded": (),
        "length_days": 7 * 8,
        "warmup": timedelta(days=2),
    }
    values.update(kwargs)
    return derive_final_window(facts, **values)  # type: ignore[arg-type]


# --- 窓（D09 §9.10.3）------------------------------------------------------------------------


def test_the_whole_usable_window_is_consumed_with_all_timeframes_and_variants() -> None:
    window = _window(_facts())
    assert isinstance(window, FinalWindow)
    assert window.consumption == Interval(start=WINDOW_START, end=WEEKS[-1].end)
    assert window.scoring_start == WINDOW_START  # 2024-01-01 から: ずらさない（仮置き R2-10）
    assert len(window.targets) == 12 * 2 * 2
    assert all(len(window.reads.of(variant)) == 12 * 2 for variant in VARIANTS)


def test_the_latest_continuous_part_is_used_and_the_warmup_shifts_the_scoring_start() -> None:
    """他の候補の消費（4 週目）で途切れたら最も新しい連続した部分だけ（要決定 R2-9 (a)）。"""
    window = _window(_facts(consumed=frozenset({3})), length_days=7 * 7)
    assert isinstance(window, FinalWindow)
    assert window.consumption.start == WEEKS[4].start
    assert window.scoring_start == WEEKS[4].start + timedelta(days=2)
    assert window.weeks == 8
    assert {pid.period_start for _, pid in window.targets} == {item.start for item in WEEKS[4:]}


def test_a_forward_period_of_another_candidate_cuts_the_window() -> None:
    excluded = (Interval(start=WEEKS[8].start, end=WEEKS[9].end),)
    window = _window(_facts(), excluded=excluded, length_days=7)
    assert isinstance(window, FinalWindow)
    assert window.consumption == Interval(start=WEEKS[10].start, end=WEEKS[11].end)


def test_a_window_shorter_than_l_is_refused() -> None:
    refusal = _window(_facts(consumed=frozenset({3})), length_days=7 * 8)
    assert isinstance(refusal, WindowRefusal)
    assert refusal.reason is FinalReason.WINDOW_SHORTER_THAN_L


def test_a_week_with_no_partition_of_one_variant_and_series_stops() -> None:
    facts = [
        fact
        for fact in _facts()
        if not (
            fact.variant is Variant.CANDIDATE
            and fact.partition_id.series == D1
            and fact.partition_id.period_start == WEEKS[5].start
        )
    ]
    refusal = _window(facts)
    assert isinstance(refusal, WindowRefusal)
    assert refusal.reason is FinalReason.WINDOW_HAS_MISSING_WEEK


def test_duplicates_read_the_first_approved_and_consume_all_or_stop_on_a_digest_mismatch() -> None:
    first = _facts(snapshot="a", approved="2026-11-20T00:00:00Z")
    later = _facts(snapshot="b", approved="2026-11-21T00:00:00Z")
    window = _window(first + later)
    assert isinstance(window, FinalWindow)
    assert len(window.targets) == 2 * 12 * 2 * 2
    assert {item.snapshot_id for variant in VARIANTS for item in window.reads.of(variant)} == {
        "a-standard",
        "a-candidate",
    }
    other = _facts(snapshot="b", digest="other")
    refusal = _window(first + other)
    assert isinstance(refusal, WindowRefusal)
    assert refusal.reason is FinalReason.DUPLICATE_PARTITION_MISMATCH


def test_different_states_across_snapshots_are_a_structure_error() -> None:
    refusal = _window(_facts(snapshot="a") + _facts(snapshot="b", consumed=frozenset({2})))
    assert isinstance(refusal, WindowRefusal)
    assert refusal.reason is FinalReason.STATE_DIFFERS_ACROSS_SNAPSHOTS


def test_the_week_of_t_fix_and_later_weeks_are_never_in_the_window() -> None:
    facts = _facts(_weeks(14))
    window = _window(facts, t_fix=WEEKS[-1].end + timedelta(hours=1))
    assert isinstance(window, FinalWindow)
    assert window.consumption.end == WEEKS[-1].end


def test_a_requested_symbol_without_any_partition_is_refused() -> None:
    """要求した銘柄の一部に封印の partition が無ければ、存在する側だけを消費しない。"""
    refusal = _window(_facts(), symbols=frozenset({"USDJPY", "EURUSD"}))
    assert isinstance(refusal, WindowRefusal)
    assert refusal.reason is FinalReason.WINDOW_HAS_MISSING_WEEK


def test_no_usable_week_is_refused() -> None:
    refusal = _window(_facts(consumed=frozenset(range(12))))
    assert isinstance(refusal, WindowRefusal)
    assert refusal.reason is FinalReason.NO_USABLE_WINDOW


# --- 申請の状態（D09 §9.10.6）-----------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "state"),
    [
        ({"lock": True, "record": True}, ApplicationState.B_IN_PROGRESS),
        ({"record": True, "consumed_on_origin": True}, ApplicationState.D_VALIDATED),
        (
            {"results": frozenset({FinalLedgerResult.COMPLETED}), "consumed_on_origin": True},
            ApplicationState.D_VALIDATED,
        ),
        (
            {"results": frozenset({FinalLedgerResult.FAILED_AFTER_CONSUMPTION})},
            ApplicationState.E_FAILED_AFTER_CONSUMPTION,
        ),
        ({"results": frozenset({FinalLedgerResult.WITHDRAWN})}, ApplicationState.F_WITHDRAWN),
        ({"declared_used_for_experiment": True}, ApplicationState.G_DECLARED_USED),
        ({"consumed_on_origin": True}, ApplicationState.C_CONSUMED_WITHOUT_RECORD),
        ({}, ApplicationState.A_NOT_APPLIED),
    ],
)
def test_the_state_is_the_first_that_applies_in_the_order_b_d_e_f_g_c_a(
    kwargs: dict[str, object], state: ApplicationState
) -> None:
    values: dict[str, object] = {
        "lock": False,
        "record": False,
        "results": frozenset(),
        "declared_used_for_experiment": False,
        "consumed_on_origin": False,
    }
    values.update(kwargs)
    assert derive_application_state(**values) is state  # type: ignore[arg-type]


# --- 判定（要決定 R2-7 (a)）------------------------------------------------------------------


def _summary(net: str = "0.05", drawdown: str = "0.10", trades: int = 40) -> FinalRunSummary:
    return FinalRunSummary(
        metrics=(
            (MetricId.NET_RETURN_RATE, RatioValue(Decimal(net))),
            (MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, RatioValue(Decimal(drawdown))),
        ),
        trade_count=trades,
    )


REQUIREMENT = standard().sufficiency.classes[0]  # 合計 20 件


@pytest.mark.parametrize(
    ("summary", "verdict", "profitability", "evidence"),
    [
        (_summary(), SearchVerdict.MEETS_STANDARD, ProfitabilityAxis.MET, EvidenceAxis.SUFFICIENT),
        (
            _summary(net="-0.01"),
            SearchVerdict.BELOW_STANDARD,
            ProfitabilityAxis.NOT_MET,
            EvidenceAxis.SUFFICIENT,
        ),
        (
            _summary(drawdown="0.31"),
            SearchVerdict.BELOW_STANDARD,
            ProfitabilityAxis.NOT_MET,
            EvidenceAxis.SUFFICIENT,
        ),
        (
            _summary(trades=5),
            SearchVerdict.INSUFFICIENT_EVIDENCE,
            ProfitabilityAxis.MET,
            EvidenceAxis.INSUFFICIENT,
        ),
        (
            _summary(drawdown="0.5", trades=5),
            SearchVerdict.INSUFFICIENT_EVIDENCE,
            ProfitabilityAxis.NOT_MET,
            EvidenceAxis.INSUFFICIENT,
        ),
    ],
)
def test_the_final_verdict_follows_the_two_axes_and_four_values(
    summary: FinalRunSummary,
    verdict: SearchVerdict,
    profitability: ProfitabilityAxis,
    evidence: EvidenceAxis,
) -> None:
    outcome = judge_final_variant(standard(), Variant.STANDARD, summary, REQUIREMENT)
    assert (outcome.verdict, outcome.profitability, outcome.evidence) == (
        verdict,
        profitability,
        evidence,
    )


def test_an_uncomputable_metric_is_insufficient_and_not_judged() -> None:
    from odyssey_fx.evaluation.domain.metrics import MetricKind, Unavailable

    summary = FinalRunSummary(
        metrics=(
            (
                MetricId.NET_RETURN_RATE,
                Unavailable(kind=MetricKind.RATIO, reason=MetricUnavailableReason.NO_TRADES),
            ),
            (MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK, RatioValue(Decimal("0.1"))),
        ),
        trade_count=30,
    )
    outcome = judge_final_variant(standard(), Variant.STANDARD, summary, REQUIREMENT)
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert outcome.profitability is ProfitabilityAxis.NOT_JUDGED and outcome.hold is None


def test_the_mechanism_check_purpose_never_meets_the_standard() -> None:
    rule = standard(purpose=StandardPurpose.MECHANISM_CHECK)
    outcome = judge_final_variant(rule, Variant.STANDARD, _summary(), REQUIREMENT)
    assert outcome.verdict is SearchVerdict.MET_IN_MECHANISM_CHECK


def test_the_combined_verdict_needs_both_variants() -> None:
    met = judge_final_variant(standard(), Variant.STANDARD, _summary(), REQUIREMENT)
    candidate_met = replace(met, variant=Variant.CANDIDATE)
    assert combine_final(met, candidate_met).verdict is SearchVerdict.MEETS_STANDARD
    below = replace(
        judge_final_variant(standard(), Variant.CANDIDATE, _summary(net="-0.1"), REQUIREMENT)
    )
    split = combine_final(met, below)
    assert split.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE and split.split
