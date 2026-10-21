"""研究ポリシー第 3 版の実装の単体テスト（D13 §2.3・§2.4・§4.3〜§4.6・§5.5・§6.4・§7.1、D09 の
2026-10-21 改訂。順 4 の実装 PR）。

人工の値だけを使う（実データ・戦略の成績は使わない）。D13 §4.5 の確認例（最高の資産 2,000,000 円、
終端の時価の資産 1,401,000 円、決済の費用 2,000 円）は人間のレビュー 1 の仮の数である。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from odyssey_fx.backtest.domain.account import AccountSpec
from odyssey_fx.backtest.domain.policies import RunConfig, RunEndMode
from odyssey_fx.backtest.trace.manifest import config_digest_of
from odyssey_fx.common.canonical import digest
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import AccountId, ExperimentId, SnapshotId
from odyssey_fx.common.money import CurrencyCode, Money
from odyssey_fx.common.refs import CompiledStrategyRef, PolicyRef, SnapshotRef
from odyssey_fx.common.symbol import Symbol, SymbolSpecRef
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.evaluation.domain.diagnostics import (
    TradeFact,
    concentration,
    holding_summary,
    reward_summary,
    statistics_summary,
    timing_summary,
    yearly_counts,
)
from odyssey_fx.evaluation.domain.metrics import (
    MetricId,
    MetricUnavailableReason,
    RatioValue,
    max_drawdown,
    max_drawdown_rate_peak,
    metric_ids_of,
)
from odyssey_fx.evaluation.domain.path import (
    PathEnd,
    path_metrics,
    plan_segments,
)
from odyssey_fx.evaluation.domain.policy_basis import (
    PREMISE_NAMES,
    FixedComparisonBasis,
    PremiseFile,
)
from odyssey_fx.evaluation.domain.search import (
    ChangeKind,
    ConditionOutcome,
    ConditionScope,
    EvidenceAxis,
    FrequencyClass,
    LineageSource,
    ProfitabilityAxis,
    SearchVerdict,
    StandardPurpose,
    SufficiencyShortfallKind,
    TrialLedgerDefect,
    VariantOutcome,
    combine_verdicts,
    ledger_defect,
    lineage_of,
)
from odyssey_fx.evaluation.domain.splits import (
    Fold,
    PartialFold,
    SplitStandard,
    SplitWindow,
    check_full_coverage,
    folds_for_policy,
    generate_folds,
)
from odyssey_fx.evaluation.domain.variant import Paired, Variant
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from tests.fixtures.evaluation import search_units, trial_ledger
from tests.fixtures.evaluation.search_units import (
    evidence,
    judge,
    make_fold,
    path_summary,
    standard,
    train_unit,
    validation_unit,
)

DAY = 86_400
JPY = CurrencyCode("JPY")

# --- 端数の fold と U1・U2（D13 §2.3・§2.4）-------------------------------------------------


def _v3_split(*, start: str = "2017-03-25T12:00:00Z") -> SplitStandard:
    return SplitStandard(
        range=Interval(start=UtcTime.parse(start), end=UtcTime.parse("2023-12-30T12:00:00Z")),
        train_seconds=728 * DAY,
        validation_seconds=364 * DAY,
        window=SplitWindow.ROLLING,
        purge_seconds=0,
        min_folds=5,
        partial_fold=PartialFold.AT_START,
    )


def test_the_v3_split_makes_four_full_folds_and_one_partial_fold_at_the_start() -> None:
    """D13 §2.2 の候補 A の表のとおり（fold 0 が端数の fold で検証 287 日）。"""
    folds = folds_for_policy(_v3_split(), research_until=UtcTime.parse("2024-01-01T00:00:00Z"))
    table = [
        (fold.fold_index, fold.partial, str(fold.train), str(fold.validation)) for fold in folds
    ]
    assert table == [
        (
            0,
            True,
            "[2017-03-25T12:00:00Z, 2019-03-23T12:00:00Z)",
            "[2019-03-23T12:00:00Z, 2020-01-04T12:00:00Z)",
        ),
        (
            1,
            False,
            "[2018-01-06T12:00:00Z, 2020-01-04T12:00:00Z)",
            "[2020-01-04T12:00:00Z, 2021-01-02T12:00:00Z)",
        ),
        (
            2,
            False,
            "[2019-01-05T12:00:00Z, 2021-01-02T12:00:00Z)",
            "[2021-01-02T12:00:00Z, 2022-01-01T12:00:00Z)",
        ),
        (
            3,
            False,
            "[2020-01-04T12:00:00Z, 2022-01-01T12:00:00Z)",
            "[2022-01-01T12:00:00Z, 2022-12-31T12:00:00Z)",
        ),
        (
            4,
            False,
            "[2021-01-02T12:00:00Z, 2022-12-31T12:00:00Z)",
            "[2022-12-31T12:00:00Z, 2023-12-30T12:00:00Z)",
        ),
    ]
    assert folds[0].validation.duration == timedelta(days=287)
    assert sum((fold.validation.duration for fold in folds), timedelta(0)) == timedelta(days=1743)


def test_no_partial_fold_when_the_range_divides_exactly() -> None:
    """`R − T − P` が `V` の倍数なら端数の fold は作らない（D13 §2.3 の末尾）。"""
    split = _v3_split(start="2018-01-06T12:00:00Z")
    folds = generate_folds(split)
    assert [fold.partial for fold in folds] == [False] * 4
    check_full_coverage(folds, split)


def test_the_coverage_checks_reject_a_hole_and_an_overlap() -> None:
    """U1・U2 は破れれば構造エラー（D13 §2.4。検査2・3 と同じ扱い）。"""
    split = _v3_split()
    folds = generate_folds(split)
    with pytest.raises(KernelValueError, match="U1|U2"):
        check_full_coverage(folds[1:], split)
    shifted = Fold(
        fold_index=4,
        train=folds[4].train,
        validation=Interval(
            start=folds[4].validation.start - timedelta(days=7), end=folds[4].validation.end
        ),
    )
    with pytest.raises(KernelValueError, match="U2"):
        check_full_coverage((*folds[:4], shifted), split)


def test_a_range_shorter_than_one_full_fold_still_uses_its_remainder() -> None:
    """完全な fold が 0 個でも、`T + P` より長い範囲は端数の fold を 1 つ作る（D13-3 (a)）。"""
    split = SplitStandard(
        range=Interval(
            start=UtcTime.parse("2017-03-25T12:00:00Z"), end=UtcTime.parse("2019-06-01T12:00:00Z")
        ),
        train_seconds=728 * DAY,
        validation_seconds=364 * DAY,
        window=SplitWindow.ROLLING,
        purge_seconds=0,
        min_folds=1,
        partial_fold=PartialFold.AT_START,
    )
    folds = generate_folds(split)
    assert len(folds) == 1 and folds[0].partial
    check_full_coverage(folds, split)


# --- 指標 #20（D07 §5.6）------------------------------------------------------------------


def test_the_peak_rate_drawdown_differs_from_the_amount_drawdown_rate() -> None:
    """金額で最大の下落と割合で最大の下落は別の時点になりうる（D13 §4.3 の例の形）。"""
    series = [Decimal("100"), Decimal("70"), Decimal("300"), Decimal("240")]
    fall = max_drawdown(series)
    assert fall is not None and fall.amount == Decimal("60") and fall.peak == Decimal("300")
    assert max_drawdown_rate_peak(series) == Decimal("0.3")


def test_the_peak_rate_drawdown_reports_why_it_has_no_value() -> None:
    assert max_drawdown_rate_peak([]) is MetricUnavailableReason.NO_OBSERVATIONS
    assert max_drawdown_rate_peak([Decimal("0"), Decimal("0")]) is (
        MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )


def test_metric_sets_two_and_three() -> None:
    """指標集合 v2 は 19 件、v3 は #20 を足した 20 件（実装 PR の仮置き 53）。"""
    assert len(metric_ids_of(2)) == 19
    assert metric_ids_of(3) == (*metric_ids_of(2), MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK)
    with pytest.raises(KernelValueError):
        metric_ids_of(1)


# --- 経路の区間と経路の指標（D13 §4.5・§4.6）-------------------------------------------------


def _folds(count: int) -> tuple[Fold, ...]:
    return tuple(make_fold(index, validation_days=73) for index in range(count))


def test_segments_join_folds_of_the_same_trial_and_isolate_folds_without_a_candidate() -> None:
    """同じ試行が続く fold は 1 区間、候補なしの fold は単独の区間（仮置き 14・15）。"""
    folds = _folds(5)
    segments = plan_segments(folds, [1, 1, None, 1, 2])
    assert [(item.fold_indices, item.trial_index) for item in segments] == [
        ((0, 1), 1),
        ((2,), None),
        ((3,), 1),
        ((4,), 2),
    ]
    assert segments[0].interval == Interval(
        start=folds[0].validation.start, end=folds[1].validation.end
    )
    assert [item.carried_boundaries for item in segments] == [1, 0, 0, 0]


def test_the_settlement_cost_is_in_the_path_drawdown() -> None:
    """区間の終わりの費用を引いた後の資産を系列の最後の点にする（仮置き 28。D13 §4.5 の確認例）。"""
    initial = Money(Decimal("1000000"), JPY)
    marked = [Decimal("1000000"), Decimal("2000000"), Decimal("1401000")]
    without_cost = dict(path_metrics(initial, marked, Money(Decimal("1401000"), JPY)))
    with_cost = dict(
        path_metrics(initial, [*marked, Decimal("1399000")], Money(Decimal("1399000"), JPY))
    )
    assert without_cost[MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK] == RatioValue(Decimal("0.2995"))
    assert with_cost[MetricId.MAX_DRAWDOWN_MTM_RATE_PEAK] == RatioValue(Decimal("0.3005"))
    assert with_cost[MetricId.NET_RETURN_RATE] == RatioValue(Decimal("0.399"))


# --- 判定の順序・2 軸・保留の印（D13 §5.5、D09 §7.3 の改訂）------------------------------------


_V3_CLASS = FrequencyClass(
    name="ALL",
    min_train_trades_per_365d=Decimal("0"),
    min_validation_trades_per_fold=0,
    min_validation_trades_total=8,
)


def _rule() -> object:
    return standard(floors=None, aggregate=(), classes=(_V3_CLASS,))


def _two_folds(trades: int = 10, drawdown: str = "0.1") -> list[object]:
    rule = standard(aggregate=(), classes=(_V3_CLASS,))
    folds = _folds(2)
    return [
        evidence(
            rule,
            fold,
            [train_unit(0), train_unit(1)],
            validation_unit(
                0,
                {
                    MetricId.TRADE_COUNT: trades,
                    MetricId.MAX_DRAWDOWN_MTM_RATE: Decimal(drawdown),
                },
            ),
        )
        for fold in folds
    ]


def _judge(
    trades: int = 10,
    *,
    drawdown: str = "0.1",
    series: Sequence[str] = ("1050000", "1100000"),
    final: str = "1100000",
    end: PathEnd = PathEnd.COMPLETED,
) -> VariantOutcome:
    rule = standard(aggregate=(), classes=(_V3_CLASS,))
    return judge(
        rule,
        _two_folds(trades, drawdown),  # type: ignore[arg-type]
        path_summary(2, final=final, series=series, end=end),
    )


def test_a_profitable_path_within_the_drawdown_limit_meets_the_standard() -> None:
    outcome = _judge()
    assert outcome.verdict is SearchVerdict.MEETS_STANDARD
    assert outcome.profitability is ProfitabilityAxis.MET
    assert outcome.evidence is EvidenceAxis.SUFFICIENT
    assert outcome.hold is None
    scopes = {item.scope for item in outcome.condition_results}
    assert {ConditionScope.PATH_FLOOR, ConditionScope.PATH_AGGREGATE} <= scopes


def test_a_path_without_profit_is_below_the_standard() -> None:
    outcome = _judge(final="1000000", series=("1000000",))
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
    assert outcome.profitability is ProfitabilityAxis.NOT_MET


def test_a_path_drawdown_breach_with_enough_trades_is_below_the_standard() -> None:
    """手順1: 経路の最大ドローダウン率 0.30 超で、合計件数が要件以上（D13 §5.5）。"""
    outcome = _judge(series=("690000", "1100000"))
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
    floors = [item for item in outcome.condition_results if item.scope is ConditionScope.PATH_FLOOR]
    assert [item.outcome for item in floors] == [ConditionOutcome.NOT_MET]


def test_a_path_drawdown_breach_with_few_trades_is_insufficient_evidence_without_hold() -> None:
    """D13-14 (a): 件数不足のまま割った違反は証拠不足、理由を残し保留にしない（仮置き 13）。"""
    outcome = _judge(trades=3, series=("690000", "1100000"))
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    kinds = {item.kind for item in outcome.shortfalls}
    assert SufficiencyShortfallKind.FLOOR_BREACHED_TOTAL_TRADES_BELOW in kinds
    assert SufficiencyShortfallKind.TOTAL_TRADES_BELOW in kinds
    breach = next(
        item
        for item in outcome.shortfalls
        if item.kind is SufficiencyShortfallKind.FLOOR_BREACHED_TOTAL_TRADES_BELOW
    )
    assert breach.fold_index is None and breach.required == 8 and breach.observed == 6
    assert outcome.hold is False


def test_a_fold_drawdown_breach_with_few_trades_is_insufficient_evidence() -> None:
    """fold の最低条件を割った fold も、合計件数が要件未満なら証拠不足（R-16 (9)）。"""
    outcome = _judge(trades=3, drawdown="0.5")
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    breach = [
        item
        for item in outcome.shortfalls
        if item.kind is SufficiencyShortfallKind.FLOOR_BREACHED_TOTAL_TRADES_BELOW
    ]
    assert [item.fold_index for item in breach] == [0, 1]


def test_profitable_but_too_few_trades_is_held() -> None:
    """保留の印: 証拠不足で、収益性・リスクは条件を満たす側（D13 §5.5）。"""
    outcome = _judge(trades=3)
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert outcome.profitability is ProfitabilityAxis.MET
    assert outcome.hold is True


def test_a_failed_path_segment_makes_the_variant_incomplete() -> None:
    """仮置き 16: 区間の run の失敗で経路は判定できない。経路の条件の結果は作らない。"""
    outcome = _judge(end=PathEnd.INCOMPLETE)
    assert outcome.verdict is SearchVerdict.INCOMPLETE
    assert outcome.profitability is ProfitabilityAxis.NOT_JUDGED
    assert not any(
        item.scope in (ConditionScope.PATH_FLOOR, ConditionScope.PATH_AGGREGATE)
        for item in outcome.condition_results
    )


def test_a_capital_loss_is_below_the_standard_not_incomplete() -> None:
    """仮置き 29: 資金喪失は確定した結果で、最大ドローダウン率 1.0 以上で満たさない。"""
    outcome = _judge(series=("-5000",), final="-5000", end=PathEnd.CAPITAL_LOST)
    assert outcome.verdict is SearchVerdict.BELOW_STANDARD
    assert outcome.path.end is PathEnd.CAPITAL_LOST


def test_a_fold_without_a_candidate_keeps_the_experiment_insufficient() -> None:
    """D13-24 (a): 候補なしの fold が 1 つでもあれば証拠不足（経路の値で満たすに進まない）。"""
    rule = standard(aggregate=(), classes=(_V3_CLASS,))
    folds = _folds(2)
    unavailable = {MetricId.NET_RETURN_RATE: MetricUnavailableReason.NO_TRADES}
    items = [
        evidence(rule, folds[0], [train_unit(0)], validation_unit(0)),
        evidence(rule, folds[1], [train_unit(0, unavailable)], None),
    ]
    outcome = judge(rule, items, path_summary(2))
    assert outcome.verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
    assert SufficiencyShortfallKind.NO_CANDIDATE_METRIC_UNAVAILABLE in {
        item.kind for item in outcome.shortfalls
    }


# --- 組の判定（D09 §7.12）-----------------------------------------------------------------


def _variant(verdict: SearchVerdict, *, hold: bool | None = None) -> VariantOutcome:
    base = _judge()
    profit = ProfitabilityAxis.MET if hold else ProfitabilityAxis.NOT_MET
    return VariantOutcome(
        variant=base.variant,
        selections=base.selections,
        fold_verdicts=base.fold_verdicts,
        verdict=verdict,
        frequency=base.frequency,
        condition_results=base.condition_results,
        shortfalls=base.shortfalls,
        trial_counts=base.trial_counts,
        purpose=StandardPurpose.STANDARD,
        profitability=profit
        if verdict is SearchVerdict.INSUFFICIENT_EVIDENCE
        else base.profitability,
        evidence=base.evidence,
        hold=hold if verdict is SearchVerdict.INSUFFICIENT_EVIDENCE else None,
        path=base.path,
    )


MEETS = SearchVerdict.MEETS_STANDARD
BELOW = SearchVerdict.BELOW_STANDARD
SHORT = SearchVerdict.INSUFFICIENT_EVIDENCE
NONE = SearchVerdict.INCOMPLETE


@pytest.mark.parametrize(
    ("left", "right", "expected", "split"),
    [
        (MEETS, MEETS, MEETS, False),
        (MEETS, BELOW, SHORT, True),
        (MEETS, SHORT, SHORT, True),
        (MEETS, NONE, NONE, False),
        (BELOW, MEETS, SHORT, True),
        (BELOW, BELOW, BELOW, False),
        (BELOW, SHORT, BELOW, False),
        (BELOW, NONE, NONE, False),
        (SHORT, MEETS, SHORT, True),
        (SHORT, BELOW, BELOW, False),
        (SHORT, SHORT, SHORT, False),
        (SHORT, NONE, NONE, False),
        (NONE, MEETS, NONE, False),
        (NONE, BELOW, NONE, False),
        (NONE, SHORT, NONE, False),
        (NONE, NONE, NONE, False),
    ],
)
def test_the_combined_verdict_follows_the_table(
    left: SearchVerdict, right: SearchVerdict, expected: SearchVerdict, split: bool
) -> None:
    """D09 §7.12 の表の 16 通り（［n］の順）。"""
    standard_ = _variant(left, hold=False if left is SHORT else None)
    candidate = _variant(right, hold=False if right is SHORT else None)
    candidate = replace(candidate, variant=Variant.CANDIDATE)
    combined = combine_verdicts(standard_, candidate)
    assert (combined.verdict, combined.split) == (expected, split)


@pytest.mark.parametrize(
    ("left_hold", "right_hold", "expected"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_the_combined_hold_needs_every_short_variant_held(
    left_hold: bool, right_hold: bool, expected: bool
) -> None:
    standard_ = _variant(SHORT, hold=left_hold)
    candidate = replace(_variant(SHORT, hold=right_hold), variant=Variant.CANDIDATE)
    assert combine_verdicts(standard_, candidate).hold is expected


def test_a_split_keeps_the_hold_of_the_short_variant() -> None:
    """片方だけが満たす: 満たさない組の保留の印を組の保留の印にする（D09 §7.12 の表）。"""
    standard_ = _variant(MEETS)
    candidate = replace(_variant(SHORT, hold=True), variant=Variant.CANDIDATE)
    combined = combine_verdicts(standard_, candidate)
    assert combined.verdict is SHORT and combined.split and combined.hold is True


# --- 台帳の行の形式版 2 と系譜（D09 §10.10・§10.12 の改訂、実装 PR の仮置き 55・56）------------


def test_a_version_one_row_cannot_be_built() -> None:
    entry = trial_ledger.started()
    with pytest.raises(KernelValueError):
        replace(entry, schema_version=1)


def test_a_finished_row_keeps_the_frequency_class_in_its_results() -> None:
    """L5: 版 2 の結末の行の最上位の頻度区分は空（組ごとの値は results）。"""
    opening = trial_ledger.started()
    closing = trial_ledger.finished(opening)
    assert closing.frequency_class is None and closing.results is not None
    broken = replace(closing, frequency_class="LOW")
    lines = trial_ledger.chain([opening, broken])
    found = ledger_defect(lines)
    assert found is not None and found[0] is TrialLedgerDefect.EVENT_SHAPE


def test_lineage_collects_the_intervals_seen_by_its_ancestors() -> None:
    """系譜の viewed_before は元の実行の検証区間と、その系譜の viewed_before の和（仮置き 56）。"""
    first = trial_ledger.started("a")
    lines = trial_ledger.chain([first])
    source = LineageSource(experiment_id=first.experiment_id, execution=1)
    built = lineage_of(lines, source, (ChangeKind.PARAMETERS,), None)
    assert not isinstance(built, str)
    assert built.viewed_before == first.validation_intervals
    missing = LineageSource(experiment_id=ExperimentId(digest("absent")), execution=1)
    assert isinstance(lineage_of(lines, missing, (ChangeKind.RETRY,), None), str)


# --- 前提の記録と比較の前提の固定（D13 §6.4・§6.6）-----------------------------------------


def test_premise_files_need_a_known_name_and_a_sha256() -> None:
    PremiseFile(name="calendar", version=1, path="configs/calendars/x.yaml", sha256="a" * 64)
    with pytest.raises(KernelValueError):
        PremiseFile(name="other", version=1, path="x.yaml", sha256="a" * 64)
    with pytest.raises(KernelValueError):
        PremiseFile(name="calendar", version=None, path="/abs.yaml", sha256="a" * 64)


def test_the_basis_needs_every_premise_file() -> None:
    """前提のファイルは 7 件すべてを書く（D13 §6.4。1 件でも欠ければ拒否）。"""
    basis = search_units.test_basis()
    assert {item.name for item in basis.premises} == PREMISE_NAMES
    with pytest.raises(KernelValueError, match="time_rule_table"):
        replace(
            basis,
            premises=tuple(item for item in basis.premises if item.name != "time_rule_table"),
        )


def test_the_fixed_comparison_basis_rejects_unsorted_limits() -> None:
    with pytest.raises(KernelValueError):
        FixedComparisonBasis(
            currency="JPY",
            initial_balance=Decimal("1000000"),
            trial_risk_rate=Decimal("0.02"),
            account_risk_cap=Decimal("0.20"),
            spread_offset=Decimal("0.02"),
            commission_per_unit=Decimal("0.001"),
            entry_slippage=Decimal("0.01"),
            close_slippage=Decimal("0.01"),
            entry_delay_bars=0,
            adverse_fill_limits=(("USDJPY", Decimal("0.05")), ("EURJPY", Decimal("0.05"))),
        )


# --- run の終わり方と ConfigDigest（D06 §3・§9.3 の改訂、D13-23 (a)）---------------------------


def _config(run_end: RunEndMode | None = None) -> RunConfig:
    policy = PolicyRef(policy_kind="risk", policy_id="risk_v1", version=1, digest=digest("risk"))
    values: dict[str, object] = {
        "run_interval": Interval(
            start=UtcTime.parse("2015-01-05T00:00:00Z"), end=UtcTime.parse("2015-01-06T00:00:00Z")
        ),
        "snapshot_ref": SnapshotRef(snapshot_id=SnapshotId(digest("snapshot"))),
        "compiled_ref": CompiledStrategyRef(digest("compiled")),
        "account": AccountSpec(
            account_id=AccountId("ACC1"),
            currency=JPY,
            initial_balance=Money(Decimal("1000000"), JPY),
        ),
        "risk_policy_ref": policy,
        "execution_policy_ref": policy,
        "cost_model_ref": policy,
        "conversion_policy_ref": policy,
        "delay_scenario_ref": policy,
        "execution_series": SeriesId(
            symbol=Symbol("USDJPY"),
            timeframe=TimeframeRef(id="15m", version=1),
            basis=PriceBasis.BID,
        ),
    }
    if run_end is not None:
        values["run_end"] = run_end
    return RunConfig(**values)  # type: ignore[arg-type]


def test_the_run_end_mode_is_always_in_the_config_digest() -> None:
    """既定値も含めて常に入る（D13-23 (a)）ので、終わり方だけが違う run は別の識別子になる。"""
    spec = SymbolSpecRef(symbol=Symbol("USDJPY"), version=1, digest=digest("spec"))

    def of(config: RunConfig) -> object:
        return config_digest_of(config, symbol_spec_ref=spec, calendar_ref="fx_ny17@v1")

    assert of(_config()) == of(_config(RunEndMode.MARK_TO_MARKET))
    assert of(_config()) != of(_config(RunEndMode.CLOSE_ALL))
    assert "run_end" in RunConfig.__dataclass_fields__


def test_paired_values_follow_the_variant_order() -> None:
    pair = Paired(standard=1, candidate=2)
    assert pair.items() == ((Variant.STANDARD, 1), (Variant.CANDIDATE, 2))
    assert pair.map(lambda _, value: value * 10) == Paired(standard=10, candidate=20)


# --- 診断の表示の計算（D13 §5.3・§5.4。判定に使わない）--------------------------------------


def _trade(entry: str, exit_at: str, profit: str) -> TradeFact:
    return TradeFact(
        entry_at=UtcTime.parse(entry), exit_at=UtcTime.parse(exit_at), profit=Decimal(profit)
    )


_TRADES = (
    _trade("2019-01-07T10:00:00Z", "2019-01-08T10:00:00Z", "300"),
    _trade("2019-03-04T10:00:00Z", "2019-03-04T12:00:00Z", "-100"),
    _trade("2020-02-03T10:00:00Z", "2020-02-05T10:00:00Z", "100"),
    _trade("2020-02-10T10:00:00Z", "2020-02-10T11:00:00Z", "-50"),
)


def test_yearly_counts_mark_years_without_trades() -> None:
    span = Interval(
        start=UtcTime.parse("2018-06-01T00:00:00Z"), end=UtcTime.parse("2020-06-01T00:00:00Z")
    )
    assert yearly_counts(_TRADES, span) == (
        (2018, 0, Decimal("0")),
        (2019, 2, Decimal("200")),
        (2020, 2, Decimal("50")),
    )


def test_concentration_of_the_largest_profits() -> None:
    found = concentration(_TRADES)
    assert found.winning_total == Decimal("400")
    assert found.top1_share == Decimal("0.75") and found.top3_share == Decimal("1")
    assert (found.net_without_top1, found.net_without_top3) == (Decimal("-50"), Decimal("-150"))


def test_timing_holding_and_reward() -> None:
    timing = timing_summary(_TRADES)
    assert timing.monthly == (("2019-01", 1), ("2019-03", 1), ("2020-02", 2))
    # 2019-03〜2020-02 の連続 12 か月に 3 件。
    assert timing.max_12_months_share == Decimal("0.75")
    assert timing.longest_gap == UtcTime.parse("2020-02-03T10:00:00Z") - UtcTime.parse(
        "2019-03-04T10:00:00Z"
    )
    holding = holding_summary(_TRADES, (UtcTime.parse("2019-01-07T22:00:00Z"),))
    assert holding.shortest == timedelta(hours=1) and holding.longest == timedelta(days=2)
    assert holding.day_end_crossings == 1
    reward = reward_summary(_TRADES)
    assert reward.win_rate == Decimal("0.5") and reward.reward_ratio == Decimal(
        "2.666666666666666666666666667"
    )


def test_statistics_and_the_exact_sign_test() -> None:
    stats = statistics_summary(_TRADES)
    assert stats.count == 4 and stats.mean == Decimal("62.5") and stats.wins == 2
    # 勝ち 2・負け 2 で、勝ちが 2 件以上の片側の確率は 11/16。
    assert stats.sign_test_p == Decimal("0.6875")
    assert statistics_summary(()).mean is None
