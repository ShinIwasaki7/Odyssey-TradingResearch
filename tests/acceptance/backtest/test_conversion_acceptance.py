"""円以外の決済通貨の換算の受入テスト（D06 v1.20 案 §19.16 の CT-2〜7・12・16・17・21・29〜33）。

人工データだけを使う（T01 の人工の足を別の通貨ペアの系列に付け替え、換算の脚は人工の率の
1 分足）。実データ・成績・実価格は読まない。値の検算は手計算の式（D06 §19.6）で行う。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, time, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext
from typing import Final

import pytest

from odyssey_fx.backtest.domain.fills import CostKind, FillRecord
from odyssey_fx.backtest.domain.policies import ConversionPolicy
from odyssey_fx.backtest.domain.positions import Position
from odyssey_fx.backtest.portfolio.ledger import LedgerSnapshot
from odyssey_fx.backtest.trace.recorder import (
    CompositeRow,
    EvidenceKind,
    EvidenceRecord,
    TraceTable,
)
from odyssey_fx.backtest.trace.result import RunStatus
from odyssey_fx.common.money import CurrencyCode, kernel_context
from odyssey_fx.common.reason import ReasonCode
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.strategy.declarations.refs import MarketDataField
from tests.fixtures.backtest.conversion import (
    CONVERSION_POLICY,
    CONVERSION_SNAPSHOT,
    EURUSD,
    USDCHF,
    USDJPY,
    FakeMinutes,
    FakeQuotes,
    minute_rates,
    minute_series,
    relabel,
    run_converted,
)
from tests.fixtures.backtest.harness import RunOutput, bars
from tests.fixtures.backtest.paths import (
    QUIET_BAR,
    RUN_INTERVAL,
    execution_bars,
    signal_bars,
)
from tests.fixtures.strategy.strategy_a import SIGNAL_SERIES

#: 日曜の開場の始値が損切りを飛び越える足（窓開けの損切り。D06 §18.4 の順 1）。
GAP_BAR: Final = ("149.300", "149.400", "149.200", "149.300")

JPY: Final = CurrencyCode("JPY")
USD: Final = CurrencyCode("USD")

#: 換算の脚の 1 分足を作る区間（run 区間の前後に余裕を持たせる）。
QUOTES_SPAN: Final = Interval(
    start=UtcTime.from_components(2026, 1, 6, 6, 0), end=UtcTime.from_components(2026, 1, 6, 13, 0)
)


def _at(hour: int, minute: int = 0, day: int = 6) -> UtcTime:
    return UtcTime.from_components(2026, 1, day, hour, minute)


Rate = str | Callable[[UtcTime], str]


def _quotes(
    rates: Mapping[Symbol, Rate] | None = None,
    *,
    symbols: Sequence[Symbol] = (USDJPY,),
    holes: Mapping[Symbol, Sequence[Interval]] | None = None,
    span: Interval = QUOTES_SPAN,
) -> FakeQuotes:
    """通貨ペアごとの率（文字列か、分から率への関数）で 1 分足を作る。"""
    given: Mapping[Symbol, Rate] = rates or {USDJPY: "150"}
    gaps: Mapping[Symbol, Sequence[Interval]] = holes or {}
    made = {
        symbol: minute_rates(symbol, span, rate, holes=gaps.get(symbol, ()))
        for symbol, rate in given.items()
    }
    return FakeQuotes(symbols=frozenset(symbols) | frozenset({EURUSD, USDCHF}), bars=made)


def _run(
    symbol: Symbol = EURUSD,
    *,
    quotes: FakeQuotes | None,
    execution: tuple[Bar, ...] | None = None,
    signal: tuple[Bar, ...] | None = None,
    run_interval: Interval = RUN_INTERVAL,
    minutes: FakeMinutes | None = None,
    policy: ConversionPolicy = CONVERSION_POLICY,
) -> RunOutput:
    return run_converted(
        symbol=symbol,
        signal_bars=relabel(signal_bars() if signal is None else signal, symbol),
        execution_bars=relabel(execution_bars() if execution is None else execution, symbol),
        run_interval=run_interval,
        quotes=quotes,
        minutes=minutes,
        conversion_policy=policy,
    )


def _fills(output: RunOutput) -> list[FillRecord]:
    return [row for row in output.rows(TraceTable.FILLS) if isinstance(row, FillRecord)]


def _snapshots(output: RunOutput) -> list[LedgerSnapshot]:
    return [
        row for row in output.rows(TraceTable.LEDGER_SNAPSHOTS) if isinstance(row, LedgerSnapshot)
    ]


def _evidence(output: RunOutput, kind: EvidenceKind) -> list[EvidenceRecord]:
    return [
        row
        for row in output.rows(TraceTable.EVIDENCE)
        if isinstance(row, EvidenceRecord) and row.kind is kind
    ]


def _positions(output: RunOutput) -> list[Position]:
    rows = output.rows(TraceTable.POSITIONS)
    return [row.primary for row in rows if isinstance(row, CompositeRow)]  # type: ignore[misc]


def _amounts(output: RunOutput) -> dict[str, object]:
    """比べる金額（数量・予約額・費用・確定損益・台帳の snapshot・末尾の 3 集計）。"""
    summaries = output.result.summaries
    assert summaries is not None
    return {
        "quantities": [fill.quantity.units for fill in _fills(output)],
        "reservations": [
            row.primary.amount.amount  # type: ignore[attr-defined]
            for row in output.rows(TraceTable.RESERVATIONS)
        ],
        "costs": [
            (entry.kind, entry.account.amount, entry.account.currency)
            for fill in _fills(output)
            for entry in fill.costs
        ],
        "realized": [p.realized.amount for p in _positions(output) if p.realized is not None],
        "snapshots": [
            (s.balance.amount, s.equity.amount, s.consumed.amount) for s in _snapshots(output)
        ],
        "summaries": (
            summaries.realized.amount,
            summaries.equity_with_mtm.amount,
            summaries.hypothetical_closed.amount,
            tuple(
                sorted(
                    (kind.value, value.amount) for kind, value in summaries.cost_breakdown.items()
                )
            ),
        ),
    }


# --- CT-2: 率 1・換算あり ---------------------------------------------------------


def test_ct2_a_unit_rate_conversion_matches_the_yen_settled_run() -> None:
    """CT-2: 決済通貨が円の run と、米ドル決済で USDJPY の率が常に 1 の run の金額が一致する。"""
    yen = _run(USDJPY, quotes=None)
    usd = _run(EURUSD, quotes=_quotes({USDJPY: "1"}))
    assert yen.result.status is RunStatus.COMPLETED
    assert usd.result.status is RunStatus.COMPLETED
    assert _fills(yen), "the scenario must open and close a position"
    assert _amounts(usd) == _amounts(yen)
    # 違うのは通貨の名前と経路の記録だけ。
    assert not _evidence(yen, EvidenceKind.MARK_TO_MARKET)
    assert _evidence(usd, EvidenceKind.MARK_TO_MARKET)


# --- CT-3: 率 ≠ 1・一定 ---------------------------------------------------------


def test_ct3_a_constant_rate_scales_the_price_terms_but_not_the_commission() -> None:
    """CT-3: USDJPY が常に 150。数量・費用・確定損益が D06 §19.6 の式どおり。"""
    output = _run(EURUSD, quotes=_quotes({USDJPY: "150"}))
    assert output.result.status is RunStatus.COMPLETED
    entry, close = _fills(output)
    rate = Decimal("150")
    commission = Decimal("0.001")
    with localcontext(kernel_context()):
        # 受付: 参照価格 ask = 150.040 + 0.02、P_limit = ask + Δ(0.05)、S = 149.500。
        p_limit = Decimal("150.060") + Decimal("0.05")
        per_unit = (p_limit - Decimal("149.500") + Decimal("0.01")) * rate + 2 * commission
        expected_q = (Decimal("20000") / per_unit).quantize(Decimal("1"), rounding=ROUND_DOWN)
    assert entry.quantity.units == expected_q
    reservation = output.rows(TraceTable.RESERVATIONS)[0].primary.amount  # type: ignore[attr-defined]
    assert reservation.amount == per_unit * expected_q
    assert reservation.currency == JPY
    q = expected_q
    for fill in (entry, close):
        commission_entry = fill.cost_of(CostKind.COMMISSION)
        slippage_entry = fill.cost_of(CostKind.SLIPPAGE_IN_PRICE)
        assert commission_entry is not None and slippage_entry is not None
        assert commission_entry.native.currency == JPY
        assert commission_entry.account.amount == commission * q
        assert commission_entry.conversion.rate == Decimal("1")
        assert slippage_entry.native.currency == USD
        assert slippage_entry.native.amount == Decimal("0.01") * q
        assert slippage_entry.account.amount == Decimal("0.01") * q * rate
    spread_entry = entry.cost_of(CostKind.SPREAD_IN_PRICE)
    assert spread_entry is not None
    assert spread_entry.account.amount == Decimal("0.02") * q * rate
    position = _positions(output)[0]
    assert position.realized is not None
    with localcontext(kernel_context()):
        gross = (close.price.value - entry.price.value) * q * rate
    # 手数料は円のまま引く（確定損益の全体は 150 倍にならない）。
    assert position.realized.amount == gross - commission * q


# --- CT-4: 率が時間で変わる -----------------------------------------------------


def _stepped(moment: UtcTime) -> str:
    if moment.value < _at(10).value:
        return "150"
    if moment.value < _at(11).value:
        return "160"
    return "170"


def test_ct4_a_changing_rate_applies_the_rate_of_each_processing_point() -> None:
    """CT-4: 予約額は受付の率のまま、確定損益は決済の率、含み損益は各処理点の率。"""
    output = _run(EURUSD, quotes=_quotes({USDJPY: _stepped}))
    assert output.result.status is RunStatus.COMPLETED
    entry, close = _fills(output)
    q = entry.quantity.units
    reservation = output.rows(TraceTable.RESERVATIONS)[0].primary.amount  # type: ignore[attr-defined]
    with localcontext(kernel_context()):
        per_unit = (Decimal("150.110") - Decimal("149.500") + Decimal("0.01")) * Decimal(
            "150"
        ) + Decimal("0.002")
    assert reservation.amount == per_unit * q
    position = _positions(output)[0]
    assert position.realized is not None
    close_rate = close.cost_of(CostKind.SLIPPAGE_IN_PRICE).conversion.rate  # type: ignore[union-attr]
    assert close_rate == Decimal(_stepped(close.processed_at.time - timedelta(minutes=1)))
    with localcontext(kernel_context()):
        assert (
            position.realized.amount
            == (close.price.value - entry.price.value) * q * close_rate - Decimal("0.001") * q
        )
    marks = {record.at: record for record in _evidence(output, EvidenceKind.MARK_TO_MARKET)}
    open_snapshots = [snap for snap in _snapshots(output) if snap.open_position_ids]
    assert open_snapshots
    for snap in open_snapshots:
        path = marks[snap.at].conversion_paths[0]
        assert path.rate == Decimal(_stepped(snap.at.time - timedelta(minutes=1)))
        # 資産 = 残高 + 含み損益（決済通貨の価格の差 × 処理点の率）。
        assert snap.equity.currency == JPY
    # 最大ドローダウンは円建ての equity から作る（評価の側で換算しない。D07 §7.1）。
    assert {snap.equity.currency for snap in _snapshots(output)} == {JPY}


# --- CT-5: 2 本の経路（USDCHF 型） ------------------------------------------------


def test_ct5_a_pivot_path_inverts_the_first_leg_and_records_skew() -> None:
    """CT-5: 1 本目の脚が逆数、率は (1 ÷ USDCHF) × USDJPY、観測時刻は古い方、ずれは差。"""
    holes = {USDCHF: (Interval(start=_at(8, 55), end=_at(9, 0)),)}
    quotes = _quotes({USDCHF: "0.8", USDJPY: "150"}, symbols=(USDCHF, USDJPY), holes=holes)
    output = _run(USDCHF, quotes=quotes)
    admission = _evidence(output, EvidenceKind.ADMISSION)[0]
    path = admission.conversion_paths[0]
    first, second = path.legs
    assert first.series == minute_series(USDCHF) and first.inverted is True
    assert second.series == minute_series(USDJPY) and second.inverted is False
    with localcontext(kernel_context()):
        assert path.rate == (Decimal("1") / Decimal("0.8")) * Decimal("150")
    assert path.observed_at == _at(8, 54)
    assert path.skew == timedelta(minutes=5)
    assert {
        ref.snapshot_ref for ref in admission.market_refs if ref.series.timeframe.id == "1m"
    } == {CONVERSION_SNAPSHOT}


# --- CT-6: ずれの超過 -----------------------------------------------------------


def test_ct6_skew_beyond_the_limit_rejects_admission_and_fails_open_positions() -> None:
    """CT-6: 2 本の脚の観測時刻の差が 15 分を超える受付は DATA_ERROR、建玉の評価では run の失敗。"""
    before = {USDCHF: (Interval(start=_at(8, 30), end=_at(9, 0)),)}
    quotes = _quotes({USDCHF: "0.8", USDJPY: "150"}, symbols=(USDCHF, USDJPY), holes=before)
    rejected = _run(USDCHF, quotes=quotes)
    decision = rejected.rows(TraceTable.ATTEMPT_DECISIONS)[0].primary  # type: ignore[attr-defined]
    assert decision.reason.code is ReasonCode.DATA_ERROR
    assert rejected.result.status is RunStatus.COMPLETED
    later = {USDCHF: (Interval(start=_at(9, 30), end=_at(10, 30)),)}
    quotes = _quotes({USDCHF: "0.8", USDJPY: "150"}, symbols=(USDCHF, USDJPY), holes=later)
    failed = _run(USDCHF, quotes=quotes)
    assert failed.result.status is RunStatus.FAILED_DATA_ERROR
    assert failed.manifest.reason is not None
    assert failed.manifest.reason.code is ReasonCode.DATA_ERROR


# --- CT-7: 古さの超過（1 本の経路） -----------------------------------------------


def test_ct7_staleness_rejects_admission_at_step_5_and_fails_only_with_open_positions() -> None:
    """CT-7: USDJPY の 15 分を超える無記録の時点の受付は reached_step=5 の DATA_ERROR。"""
    before = {USDJPY: (Interval(start=_at(8, 30), end=_at(9, 0)),)}
    rejected = _run(EURUSD, quotes=_quotes({USDJPY: "150"}, holes=before))
    assessment = rejected.rows(TraceTable.RISK_ASSESSMENTS)[0]
    assert assessment.reached_step == 5  # type: ignore[attr-defined]
    assert assessment.conversion is None  # type: ignore[attr-defined]
    assert assessment.stop_after_rounding is not None  # type: ignore[attr-defined]
    decision = rejected.rows(TraceTable.ATTEMPT_DECISIONS)[0].primary  # type: ignore[attr-defined]
    assert decision.reason.code is ReasonCode.DATA_ERROR
    # 建玉が無いので run は続いて完走する。
    assert rejected.result.status is RunStatus.COMPLETED
    later = {USDJPY: (Interval(start=_at(9, 30), end=_at(10, 30)),)}
    failed = _run(EURUSD, quotes=_quotes({USDJPY: "150"}, holes=later))
    assert failed.result.status is RunStatus.FAILED_DATA_ERROR


# --- CT-12: 能力検査 -----------------------------------------------------------


def test_ct12_capability_blocks_a_missing_route_and_a_shortfall_but_not_unrecorded_minutes() -> (
    None
):
    """CT-12: 経路を組めない・換算の系列に取得不足がある run は FAILED_CAPABILITY。

    無記録の分だけなら実行できる。
    """
    no_usdjpy = FakeQuotes(symbols=frozenset({EURUSD}), bars={})
    blocked = _run(EURUSD, quotes=no_usdjpy)
    assert blocked.result.status is RunStatus.FAILED_CAPABILITY
    report = blocked.manifest.capability_report.conversion
    assert report is not None and report.route_found is False
    gap = Interval(start=_at(10), end=_at(10, 30))
    short = run_converted(
        symbol=EURUSD,
        signal_bars=relabel(signal_bars(), EURUSD),
        execution_bars=relabel(execution_bars(), EURUSD),
        run_interval=RUN_INTERVAL,
        quotes=_quotes({USDJPY: "150"}),
        shortfall={minute_series(USDJPY): ((gap, "status=failed"),)},
    )
    assert short.result.status is RunStatus.FAILED_CAPABILITY
    leg = short.manifest.capability_report.conversion.legs[0]  # type: ignore[union-attr]
    assert leg.shortfall == ((gap, "status=failed"),)
    holes = {USDJPY: (Interval(start=_at(11, 30), end=_at(11, 40)),)}
    runnable = _run(EURUSD, quotes=_quotes({USDJPY: "150"}, holes=holes))
    assert runnable.result.capability_report.runnable is True


# --- CT-16・CT-17: 記録と費用の通貨 ------------------------------------------------


def test_ct16_records_carry_the_paths_and_the_leg_observations() -> None:
    """CT-16: 受付・約定・時価評価の根拠記録に経路と脚の観測の参照。古さは at − observed_at。"""
    output = _run(EURUSD, quotes=_quotes({USDJPY: "150"}))
    for kind in (EvidenceKind.ADMISSION, EvidenceKind.FILL, EvidenceKind.MARK_TO_MARKET):
        records = _evidence(output, kind)
        assert records, kind
        for record in records:
            path = record.conversion_paths[0]
            assert not path.is_identity
            leg_refs = [ref for ref in record.market_refs if ref.series == minute_series(USDJPY)]
            assert len(leg_refs) == 1
            assert leg_refs[0].snapshot_ref == CONVERSION_SNAPSHOT
            assert leg_refs[0].field is MarketDataField.CLOSE
            staleness = record.at.time - path.observed_at
            assert timedelta(0) < staleness <= timedelta(minutes=15)
    for record in _evidence(output, EvidenceKind.MARK_TO_MARKET):
        assert record.ledger_snapshot_at == record.at
    yen = _run(USDJPY, quotes=None)
    assert not _evidence(yen, EvidenceKind.MARK_TO_MARKET)


def test_ct17_commission_stays_in_yen_and_price_unit_costs_convert() -> None:
    """CT-17: 手数料は原通貨も計上も円（恒等の率）、滑り・スプレッドは原通貨が米ドルで計上が円。"""
    output = _run(EURUSD, quotes=_quotes({USDJPY: "150"}))
    for fill in _fills(output):
        for entry in fill.costs:
            assert entry.account.currency == JPY
            if entry.kind is CostKind.COMMISSION:
                assert entry.native.currency == JPY
                assert (entry.conversion.from_currency, entry.conversion.to_currency) == (JPY, JPY)
            else:
                assert entry.native.currency == USD
                assert entry.conversion.from_currency == USD
    # 台帳・建玉・末尾の集計の金額はすべて円（評価の整合検査 C8 が読む列）。
    assert {snap.balance.currency for snap in _snapshots(output)} == {JPY}
    summaries = output.result.summaries
    assert summaries is not None
    assert {summaries.realized.currency, summaries.equity_with_mtm.currency} == {JPY}


# --- CT-21・CT-29: 休場をまたぐ処理点 ----------------------------------------------

#: 金曜（2026-01-09）の判断時刻。週の閉場は 22:00Z（ニューヨーク 17:00）。
_FRIDAY: Final = UtcTime.from_components(2026, 1, 9, 20, 0)
_FRIDAY_CLOSE: Final = UtcTime.from_components(2026, 1, 9, 22, 0)
_SUNDAY_OPEN: Final = UtcTime.from_components(2026, 1, 11, 22, 0)


def _friday_signal() -> tuple[Bar, ...]:
    specs: list[tuple[str, str, str, str]] = [("149.505", "150.000", "149.500", "149.505")] * 20
    specs.append(("149.900", "150.100", "149.800", "150.040"))
    return bars(SIGNAL_SERIES, _FRIDAY - timedelta(hours=21), timedelta(hours=1), specs)


def _friday_execution(*, sunday: tuple[str, str, str, str] | None = None) -> tuple[Bar, ...]:
    """金曜 19:45〜22:00 の執行足（建玉を開いたまま週末へ持ち越す）と、任意の日曜の始値の足。"""
    from tests.fixtures.backtest.harness import EXECUTION_SERIES

    specs: list[tuple[str, str, str, str]] = [
        ("150.040", "150.060", "150.040", "150.040"),
        ("150.050", "150.120", "150.020", "150.100"),
    ]
    specs.extend([QUIET_BAR] * 7)
    made = list(
        bars(EXECUTION_SERIES, _FRIDAY - timedelta(minutes=15), timedelta(minutes=15), specs)
    )
    if sunday is not None:
        made.extend(bars(EXECUTION_SERIES, _SUNDAY_OPEN, timedelta(minutes=15), [sunday]))
    return tuple(made)


_WEEKEND_QUOTES: Final = Interval(
    start=_FRIDAY - timedelta(hours=2), end=_SUNDAY_OPEN + timedelta(hours=1)
)


def test_ct21_a_run_ending_inside_the_weekend_close_measures_staleness_to_the_close() -> None:
    """CT-21: 土曜の閉場の中で終わる run の末尾の時価評価は、閉場の直前の観測で換算できる。"""
    run = Interval(start=_FRIDAY, end=UtcTime.from_components(2026, 1, 10, 12, 0))
    ok = _run(
        EURUSD,
        quotes=_quotes({USDJPY: "150"}, span=_WEEKEND_QUOTES),
        signal=_friday_signal(),
        execution=_friday_execution(),
        run_interval=run,
    )
    assert ok.result.status is RunStatus.COMPLETED
    assert ok.result.summaries is not None and ok.result.summaries.hypothetical_close_lines
    last = _evidence(ok, EvidenceKind.MARK_TO_MARKET)[-1]
    # 記録の古さ（at − observed_at）は実際の差のまま残る（1 日以上）。
    assert last.at.time - last.conversion_paths[0].observed_at > timedelta(hours=12)
    holes = {USDJPY: (Interval(start=_FRIDAY_CLOSE - timedelta(minutes=35), end=_FRIDAY_CLOSE),)}
    stale = _run(
        EURUSD,
        quotes=_quotes({USDJPY: "150"}, span=_WEEKEND_QUOTES, holes=holes),
        signal=_friday_signal(),
        execution=_friday_execution(),
        run_interval=run,
    )
    assert stale.result.status is RunStatus.FAILED_DATA_ERROR


def test_ct29_a_monday_gap_close_of_a_carried_position_converts_across_the_weekend() -> None:
    """CT-29: 金曜から持ち越した建玉の日曜の開場の窓開けの損切りは、休場を除いた古さで換算する。"""
    run = Interval(start=_FRIDAY, end=_SUNDAY_OPEN + timedelta(minutes=30))
    ok = _run(
        EURUSD,
        quotes=_quotes({USDJPY: "150"}, span=_WEEKEND_QUOTES),
        signal=_friday_signal(),
        execution=_friday_execution(sunday=GAP_BAR),
        run_interval=run,
    )
    assert ok.result.status is RunStatus.COMPLETED, ok.manifest.reason
    closes = [fill for fill in _fills(ok) if fill.processed_at.time == _SUNDAY_OPEN]
    assert closes, "the carried position must be stopped out at the Sunday open"
    holes = {USDJPY: (Interval(start=_FRIDAY_CLOSE - timedelta(minutes=35), end=_FRIDAY_CLOSE),)}
    stale = _run(
        EURUSD,
        quotes=_quotes({USDJPY: "150"}, span=_WEEKEND_QUOTES, holes=holes),
        signal=_friday_signal(),
        execution=_friday_execution(sunday=GAP_BAR),
        run_interval=run,
    )
    assert stale.result.status is RunStatus.FAILED_DATA_ERROR


def test_ct29_a_holiday_closure_is_excluded_from_staleness_too() -> None:
    """CT-29（祝日）: 平日の休場をまたぐ処理点でも、休場の時間を除いて古さを測る。"""
    from odyssey_fx.backtest.domain.policies import ConversionPath
    from odyssey_fx.backtest.portfolio.conversion import require_usable, session_staleness
    from tests.fixtures.synthetic import market

    holiday = market.calendar(
        closures=(
            market.closure(date(2026, 1, 7), start=time(10, 0), end=time(16, 0), note="test"),
        )
    )
    observed = UtcTime.from_components(2026, 1, 7, 14, 59)  # NY 09:59、休場の直前の観測
    at = UtcTime.from_components(2026, 1, 7, 21, 5)  # NY 16:05、休場明け 5 分
    from odyssey_fx.backtest.domain.policies import ConversionLeg
    from odyssey_fx.marketdata.domain.bar import BarKey

    leg = ConversionLeg(
        series=minute_series(USDJPY),
        bar_key=BarKey(series=minute_series(USDJPY), bar_start=observed),
        rate=Decimal("150"),
        observed_at=observed,
    )
    path = ConversionPath(legs=(leg,), rate=Decimal("150"), observed_at=observed, skew=timedelta(0))
    assert session_staleness(path, at, holiday) == timedelta(minutes=6)
    require_usable(path, at, CONVERSION_POLICY, holiday)


# --- CT-30: 週明け直後の 2 本の脚のずれ（決定 (28) (a)。v1.21 案 §19.5） ---------------------


def test_ct30_two_legs_across_the_weekend_count_their_skew_without_the_close() -> None:
    """CT-30: 金曜の閉場の直前の脚と週明けの開場の後の脚のずれは、休場を除いて上限内と数える。

    日曜の開場から 10 分、USDCHF にまだ記録が無く（最後は金曜 21:59）、USDJPY は開場の後の分を
    持つ。記録のずれ（実際の差）は 48 時間を超えるが、休場を除いたずれは 10 分で換算できる。
    USDCHF が開場から 30 分無記録なら、開場から 30 分の処理点では、休場を除いた古さが 31 分になり、
    理由は古さの超過になる（ずれは上限 30 分ちょうどで上限内。決定 (32)）。
    """
    from odyssey_fx.backtest.portfolio.conversion import (
        ConversionFailure,
        ConversionUnavailable,
        RouteHop,
        observe_path,
        require_usable,
        session_skew,
    )
    from tests.fixtures.backtest.harness import CALENDAR

    policy = CONVERSION_POLICY
    route = (RouteHop(USDCHF, True), RouteHop(USDJPY))
    holes = {USDCHF: (Interval(start=_SUNDAY_OPEN, end=_SUNDAY_OPEN + timedelta(minutes=30)),)}
    quotes = _quotes(
        {USDCHF: "0.8", USDJPY: "150"}, symbols=(USDCHF, USDJPY), holes=holes, span=_WEEKEND_QUOTES
    )
    at = _SUNDAY_OPEN + timedelta(minutes=10)
    path = observe_path(route, at, quotes)
    assert path.observed_at == _FRIDAY_CLOSE - timedelta(minutes=1)
    assert path.skew > timedelta(hours=48)
    assert session_skew(path, CALENDAR) == timedelta(minutes=10)
    require_usable(path, at, policy, CALENDAR)
    boundary = _SUNDAY_OPEN + timedelta(minutes=30)
    late = observe_path(route, boundary, quotes)
    assert session_skew(late, CALENDAR) == timedelta(minutes=30)
    with pytest.raises(ConversionUnavailable) as raised:
        require_usable(late, boundary, policy, CALENDAR)
    assert raised.value.failure is ConversionFailure.STALENESS_EXCEEDED
    assert raised.value.length == timedelta(minutes=31)


# --- CT-31〜CT-33: 取引の通貨ペアにも記録の無い時間（決定 (31) (a)。v1.22 案 §19.5） -----------


def _traded(symbol: Symbol, holes: Sequence[Interval] = ()) -> FakeMinutes:
    """取引の通貨ペア（執行系列の通貨ペア）の 1 分足（MT5 経路の run。`holes` の分は無記録）。"""
    gaps = tuple(holes)
    return FakeMinutes(
        minute_series=minute_series(symbol),
        bars=minute_rates(symbol, QUOTES_SPAN, "1.1", holes=gaps),
        unrecorded=gaps,
    )


def _cause(output: RunOutput) -> str:
    reason = output.manifest.reason
    assert reason is not None
    detail = reason.detail
    cause = getattr(detail, "cause", None)
    assert isinstance(cause, str)
    return cause


def test_ct31_mark_to_market_converts_while_both_pairs_are_unrecorded() -> None:
    """CT-31: USDJPY と EURUSD の両方が 60 分無記録の間も、建玉の時価評価は換算されて run が続く。

    9:30〜10:30 に両方が無記録。休場だけを除いた古さは 10:00 で 31 分（今までの測り方では上限
    30 分を超えて run の失敗）だが、EURUSD に記録のある分だけを数えると観測の分の 1 分。
    記録の `at − observed_at` は実際の差のまま。EURUSD の 1 分足を持たない run（仮置き C4-2）は
    今までの測り方で失敗する。
    """
    hole = (Interval(start=_at(9, 30), end=_at(10, 30)),)
    quotes = _quotes({USDJPY: "150"}, holes={USDJPY: hole})
    ok = _run(EURUSD, quotes=quotes, minutes=_traded(EURUSD, hole))
    assert ok.result.status is RunStatus.COMPLETED, ok.manifest.reason
    inside = [
        row
        for row in _evidence(ok, EvidenceKind.MARK_TO_MARKET)
        if row.conversion_paths and _at(9, 45) <= row.at.time <= _at(10, 30)
    ]
    assert inside, "the open position must be marked inside the hole"
    for row in inside:
        assert row.conversion_paths[0].observed_at == _at(9, 29)
    assert max(row.at.time - row.conversion_paths[0].observed_at for row in inside) == (
        timedelta(minutes=61)
    )
    without = _run(EURUSD, quotes=quotes)
    assert without.result.status is RunStatus.FAILED_DATA_ERROR
    assert "0:31:00 old in session time at" in _cause(without)


def test_ct31_a_traded_pair_recording_every_minute_keeps_the_previous_outcome() -> None:
    """CT-31: EURUSD がセッションの毎分に記録を持てば、結果は決定 (25) (a) の測り方と同じ。

    USDJPY だけが 9:30〜10:30 に無記録なら、1 分足を持つ run も持たない run も 10:00 の時価評価で
    同じ古さ（31 分）の超過で失敗する（上限 30 分。決定 (32)）。
    """
    quotes = _quotes(
        {USDJPY: "150"}, holes={USDJPY: (Interval(start=_at(9, 30), end=_at(10, 30)),)}
    )
    with_minutes = _run(EURUSD, quotes=quotes, minutes=_traded(EURUSD))
    without = _run(EURUSD, quotes=quotes)
    assert with_minutes.result.status is RunStatus.FAILED_DATA_ERROR
    assert without.result.status is RunStatus.FAILED_DATA_ERROR
    assert "0:31:00 old in session time counting only minutes" in _cause(with_minutes)
    assert "0:31:00 old in session time at" in _cause(without)


def test_ct32_staleness_of_exactly_30_counted_minutes_converts_and_31_fails() -> None:
    """CT-32: USDJPY が無記録の間に EURUSD が記録を持つ分だけを数え、上限 30 分ちょうどは換算する。

    USDJPY の最後の観測は 9:29（9:30〜10:30 が無記録）。EURUSD が 9:59〜10:30 に無記録なら、
    10:00〜10:30 の処理点の古さはどれも 30 分ちょうどで run が続く。EURUSD が 10:00 から無記録
    なら 10:00 の古さは 31 分で FAILED_DATA_ERROR、理由の長さも 31 分（仮置き C4-5、決定 (32)）。
    """
    quotes = _quotes(
        {USDJPY: "150"}, holes={USDJPY: (Interval(start=_at(9, 30), end=_at(10, 30)),)}
    )
    exact = _run(
        EURUSD,
        quotes=quotes,
        minutes=_traded(EURUSD, (Interval(start=_at(9, 59), end=_at(10, 30)),)),
    )
    assert exact.result.status is RunStatus.COMPLETED, exact.manifest.reason
    over = _run(
        EURUSD,
        quotes=quotes,
        minutes=_traded(EURUSD, (Interval(start=_at(10, 0), end=_at(10, 30)),)),
    )
    assert over.result.status is RunStatus.FAILED_DATA_ERROR
    cause = _cause(over)
    assert "STALENESS_EXCEEDED" in cause
    assert "0:31:00 old in session time counting only minutes the traded pair recorded" in cause


def test_ct32_admission_is_still_rejected_before_the_order_when_only_usdjpy_is_stale() -> None:
    """CT-32: EURUSD が記録を持つ間の USDJPY の 30 分の無記録は、今の規則のまま受付前の拒否。"""
    before = {USDJPY: (Interval(start=_at(8, 30), end=_at(9, 0)),)}
    rejected = _run(EURUSD, quotes=_quotes({USDJPY: "150"}, holes=before), minutes=_traded(EURUSD))
    assessment = rejected.rows(TraceTable.RISK_ASSESSMENTS)[0]
    assert assessment.reached_step == 5  # type: ignore[attr-defined]
    decision = rejected.rows(TraceTable.ATTEMPT_DECISIONS)[0].primary  # type: ignore[attr-defined]
    assert decision.reason.code is ReasonCode.DATA_ERROR
    assert rejected.result.status is RunStatus.COMPLETED


def test_ct33_two_legs_convert_while_the_traded_usdchf_is_unrecorded() -> None:
    """CT-33（仮置き C4-6）: USDCHF が無記録で USDJPY だけが記録を持つ間も換算できる。

    9:30〜10:30 に USDCHF（取引の通貨ペア・1 本目の脚）が無記録。USDCHF の 1 分足を持つ run は
    ずれも古さも USDCHF に記録のある分だけを数えて上限内で、run が続く。持たない run は今までの
    測り方で失敗する。USDCHF に記録があり USDJPY が 9:29 から無記録なら、10:00 のずれは 31 分で
    換算不可（検査の順でずれが先。上限 30 分。決定 (32)）。
    """
    rates = {USDCHF: "0.8", USDJPY: "150"}
    hole = (Interval(start=_at(9, 30), end=_at(10, 30)),)
    quotes = _quotes(rates, symbols=(USDCHF, USDJPY), holes={USDCHF: hole})
    ok = _run(USDCHF, quotes=quotes, minutes=_traded(USDCHF, hole))
    assert ok.result.status is RunStatus.COMPLETED, ok.manifest.reason
    inside = [
        row
        for row in _evidence(ok, EvidenceKind.MARK_TO_MARKET)
        if row.conversion_paths and _at(9, 45) <= row.at.time <= _at(10, 30)
    ]
    assert inside
    # 記録のずれ（実際の差）は上限を超えたまま残る（10:30 で 60 分）。
    assert max(row.conversion_paths[0].skew for row in inside) == timedelta(minutes=60)
    assert _run(USDCHF, quotes=quotes).result.status is RunStatus.FAILED_DATA_ERROR
    moving = _quotes(
        rates,
        symbols=(USDCHF, USDJPY),
        holes={USDJPY: (Interval(start=_at(9, 29), end=_at(10, 30)),)},
    )
    skewed = _run(USDCHF, quotes=moving, minutes=_traded(USDCHF))
    assert skewed.result.status is RunStatus.FAILED_DATA_ERROR
    cause = _cause(skewed)
    assert "SKEW_EXCEEDED" in cause
    assert "0:31:00 apart" in cause


# --- CT-34: 上限 30 分の境界（人間の決定 (32)。v1.23 案 §19.5） ---------------------------


def _sunday_carry(
    holes: Sequence[Interval], policy: ConversionPolicy = CONVERSION_POLICY
) -> RunOutput:
    """金曜から持ち越した EURUSD の建玉を、日曜の開場から 45 分まで持つ run。

    日曜の執行足は損切り・利確に届かない足（`QUIET_BAR`）を 3 本。`holes` は日曜の USDJPY の
    無記録の区間。古さは休場の時間を除いて数える（金曜の最後の観測 21:59 の 1 分と日曜の分）。
    """
    from tests.fixtures.backtest.harness import EXECUTION_SERIES

    sunday = bars(EXECUTION_SERIES, _SUNDAY_OPEN, timedelta(minutes=15), [QUIET_BAR] * 3)
    return _run(
        EURUSD,
        quotes=_quotes({USDJPY: "150"}, span=_WEEKEND_QUOTES, holes={USDJPY: tuple(holes)}),
        signal=_friday_signal(),
        execution=_friday_execution() + tuple(sunday),
        run_interval=Interval(start=_FRIDAY, end=_SUNDAY_OPEN + timedelta(minutes=45)),
        policy=policy,
    )


def test_ct34_staleness_of_exactly_30_minutes_converts_and_31_fails_in_a_run() -> None:
    """CT-34: 上限 30 分ちょうどの古さの時価評価は換算でき、31 分は FAILED_DATA_ERROR（決定 (32)）。

    日曜の USDJPY は 22:00 の分だけ記録があり、22:01〜22:30 が無記録なら、22:30 の処理点の古さは
    30 分ちょうど（観測は 22:00）で run が続く。22:00〜22:30 が無記録なら、最後の観測は金曜 21:59
    で、休場を除いた古さは 31 分（金曜の 1 分と日曜の 30 分）になり、建玉の時価評価で止まる。
    """
    exact = _sunday_carry(
        (
            Interval(
                start=_SUNDAY_OPEN + timedelta(minutes=1), end=_SUNDAY_OPEN + timedelta(minutes=30)
            ),
        )
    )
    assert exact.result.status is RunStatus.COMPLETED, exact.manifest.reason
    boundary = [
        row
        for row in _evidence(exact, EvidenceKind.MARK_TO_MARKET)
        if row.conversion_paths and row.at.time == _SUNDAY_OPEN + timedelta(minutes=30)
    ]
    assert boundary, "the carried position must be marked at 22:30 on Sunday"
    assert boundary[0].conversion_paths[0].observed_at == _SUNDAY_OPEN
    over = _sunday_carry((Interval(start=_SUNDAY_OPEN, end=_SUNDAY_OPEN + timedelta(minutes=30)),))
    assert over.result.status is RunStatus.FAILED_DATA_ERROR
    cause = _cause(over)
    assert "STALENESS_EXCEEDED" in cause
    assert "0:31:00 old in session time at" in cause


def test_ct34_a_19_minute_gap_after_the_sunday_open_converts_under_30_but_not_under_15() -> None:
    """CT-34: 日曜の週の始まりに USDJPY が 19 分無記録でも、上限 30 分なら建玉を持ったまま続く。

    M1 の 2 回目で戦略 A が止まった形（第19.19節の追加の決定 7）を人工データで写す。22:00〜22:19
    が無記録のとき、22:15 の処理点の古さは 16 分。上限 30 分の換算のポリシーでは `COMPLETED`、
    上限 15 分（決定 (32) の前の値）では FAILED_DATA_ERROR。
    """
    gap = (Interval(start=_SUNDAY_OPEN, end=_SUNDAY_OPEN + timedelta(minutes=19)),)
    assert _sunday_carry(gap).result.status is RunStatus.COMPLETED
    previous = ConversionPolicy(pivot_currency=USD, max_observation_skew=timedelta(minutes=15))
    stopped = _sunday_carry(gap, previous)
    assert stopped.result.status is RunStatus.FAILED_DATA_ERROR
    assert "0:16:00 old in session time at" in _cause(stopped)
