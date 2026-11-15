"""指標集合 v4 の 3 件（#21〜#23）の式と、単一の run の評価での出し方（研究ポリシー第 5 版の設計
D24 §3.2・§4.1・§6、仮置き 2・8・19。実装 PR 4-1）。

- #21 区間末の評価を含む純収益率: `(最後の残高 + hypothetical_closed − 初期残高) ÷ 初期残高`。
  受入テスト RP5-5: 区間末に含み損の建玉を持つ run で #15 より小さく、決済の費用の分だけ #13 の率
  より小さい。
- #22 平均利益 ÷ 平均損失（参考値。決定 138）。
- #23 年率化下側偏差比率（日次・目標値 0・分母は全日数。決定 161）。

判断履歴は紙上トレース T01 の人工の表（`tests/fixtures/evaluation/traces.py`）で、成績を読まない。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, localcontext
from typing import Final

from odyssey_fx.common.money import CurrencyCode, Money, decimal_from_str, kernel_context
from odyssey_fx.common.refs import CodeDigest, ContentDigest
from odyssey_fx.evaluation.application.evaluate_run import EvaluateRun, EvaluationReport
from odyssey_fx.evaluation.domain.metrics import (
    METRIC_SET_V4_ADDED,
    AmountValue,
    MetricCaveat,
    MetricId,
    MetricUnavailableReason,
    RatioValue,
    Unavailable,
    annualized_downside_ratio,
    annualized_sharpe_ratio,
    average_win_loss_ratio,
    metric_ids_of,
    net_return_rate_closed_out,
)
from odyssey_fx.evaluation.domain.search import REFERENCE_METRICS, is_selectable_metric
from tests.fixtures.evaluation import traces

JPY: Final = CurrencyCode("JPY")
_CODE_DIGEST: Final = CodeDigest(digest=ContentDigest.sha256("b" * 64))


def _yen(text: str) -> Money:
    return Money(decimal_from_str(text), JPY)


def _amounts(*texts: str) -> list[Decimal]:
    return [decimal_from_str(text) for text in texts]


def _evaluate_v4(*, open_loss: bool = False) -> EvaluationReport:
    """T01 の run を指標集合 v4 で評価する。`open_loss` なら区間末の建玉を含み損に置き換える。

    T01 の末尾の集計: 残高 1,036,706 円、含み込みの資産 1,051,706 円（含み益 15,000 円）、仮決済の
    損益 14,670 円（決済の費用 330 円を引いた後）。含み損の版は、同じ建玉が 20,000 円の含み損を
    持ち、決済の費用 330 円を引いて仮決済の損益が −20,330 円になる人工の集計である。
    """
    repository = traces.repository_for()
    result = traces.result_for(repository.manifest)
    if open_loss:
        assert result.summaries is not None
        result = replace(
            result,
            summaries=replace(
                result.summaries,
                equity_with_mtm=_yen("1016706"),
                hypothetical_closed=_yen("-20330"),
            ),
        )
    return EvaluateRun(evaluation_code_digest=_CODE_DIGEST).evaluate(
        result, repository, 4, traces.CALENDAR
    )


def _value(report: EvaluationReport, metric_id: MetricId) -> object:
    found = [record for record in report.metrics if record.metric_id is metric_id]
    assert len(found) == 1, found
    return found[0].value


def _ratio(report: EvaluationReport, metric_id: MetricId) -> Decimal:
    value = _value(report, metric_id)
    assert isinstance(value, RatioValue), value
    return value.ratio


def _end_equity_rate(report: EvaluationReport) -> Decimal:
    """#13 の率（含み込みの最終資産 ÷ 初期残高 − 1）。#13 は金額なので率は試験の側で作る。"""
    value = _value(report, MetricId.END_EQUITY_MTM)
    assert isinstance(value, AmountValue), value
    initial = decimal_from_str("1000000")
    with localcontext(kernel_context()):
        return (value.amount.amount - initial) / initial


# --- 指標集合 v4 の形 -----------------------------------------------------------------------


def test_metric_set_v4_is_v3_plus_the_three_in_declaration_order() -> None:
    report = _evaluate_v4()
    assert [record.metric_id for record in report.metrics] == list(metric_ids_of(4))
    assert [record.metric_id for record in report.metrics][-3:] == list(METRIC_SET_V4_ADDED)
    assert len(report.metrics) == 23


def test_the_v4_metrics_are_absent_from_v2_and_v3() -> None:
    for version in (2, 3):
        assert not set(METRIC_SET_V4_ADDED) & set(metric_ids_of(version))


def test_the_average_win_loss_ratio_is_a_reference_metric_and_the_others_are_adopted() -> None:
    """#22 は参考値（決定 138）、#21・#23 は採用指標（D24 §6）。"""
    assert MetricId.AVERAGE_WIN_LOSS_RATIO in REFERENCE_METRICS
    assert not is_selectable_metric(MetricId.AVERAGE_WIN_LOSS_RATIO)
    assert is_selectable_metric(MetricId.NET_RETURN_RATE_CLOSED_OUT)
    assert is_selectable_metric(MetricId.ANNUALIZED_DOWNSIDE_RATIO)


def test_the_v4_caveats_follow_the_design() -> None:
    """注記（D24 §6）: #21 は `SWAP_NOT_MODELED` だけ、#22 は #18 と同じ、#23 は #17 と同じ。"""
    report = _evaluate_v4()
    caveats = {record.metric_id: record.caveats for record in report.metrics}
    assert caveats[MetricId.NET_RETURN_RATE_CLOSED_OUT] == (MetricCaveat.SWAP_NOT_MODELED,)
    assert caveats[MetricId.AVERAGE_WIN_LOSS_RATIO] == caveats[MetricId.PROFIT_FACTOR]
    assert caveats[MetricId.ANNUALIZED_DOWNSIDE_RATIO] == caveats[MetricId.ANNUALIZED_SHARPE_RATIO]


# --- #21（RP5-5）-------------------------------------------------------------------------------


def test_rp5_5_an_open_loss_puts_closed_out_return_below_net_return_and_end_equity_rate() -> None:
    """RP5-5: 区間末に含み損の建玉を持つ run で、#21 < #15、#13 の率 − #21 = 決済の費用 ÷ 初期残高。

    #15 = 36,706 ÷ 1,000,000 = 0.036706。#21 = (1,036,706 − 20,330 − 1,000,000) ÷ 1,000,000 =
    0.016376。#13 の率 = 16,706 ÷ 1,000,000 = 0.016706。差 0.00033 = 330 ÷ 1,000,000。
    """
    report = _evaluate_v4(open_loss=True)
    closed_out = _ratio(report, MetricId.NET_RETURN_RATE_CLOSED_OUT)
    net_return = _ratio(report, MetricId.NET_RETURN_RATE)
    assert closed_out == decimal_from_str("0.016376")
    assert net_return == decimal_from_str("0.036706")
    assert closed_out < net_return
    assert _end_equity_rate(report) - closed_out == decimal_from_str("0.00033")


def test_rp5_5_closed_out_return_with_an_open_gain_includes_the_gain_symmetrically() -> None:
    """含み益も同じ式で入る（利益の側だけを抜く規則を作らない。D24 §3.2）。T01 の集計:
    (1,036,706 + 14,670 − 1,000,000) ÷ 1,000,000 = 0.051376。"""
    report = _evaluate_v4()
    assert _ratio(report, MetricId.NET_RETURN_RATE_CLOSED_OUT) == decimal_from_str("0.051376")


def test_closed_out_return_formula_and_zero_initial_balance() -> None:
    assert net_return_rate_closed_out(
        _yen("950000"), _yen("-30000"), _yen("1000000")
    ) == decimal_from_str("-0.08")
    assert net_return_rate_closed_out(_yen("1000000"), _yen("0"), _yen("1000000")) == 0
    assert (
        net_return_rate_closed_out(_yen("0"), _yen("0"), _yen("0"))
        is MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )


# --- #22 ----------------------------------------------------------------------------------------


def test_average_win_loss_ratio_divides_the_mean_win_by_the_mean_loss() -> None:
    """勝ち 300・100（平均 200）、負け −50・−150（平均の絶対値 100）、損益 0 は数えない → 2。"""
    profits = [_yen("300"), _yen("-50"), _yen("100"), _yen("0"), _yen("-150")]
    assert average_win_loss_ratio(profits) == decimal_from_str("2")


def test_average_win_loss_ratio_is_unavailable_without_wins_or_losses() -> None:
    assert average_win_loss_ratio([]) is MetricUnavailableReason.NO_TRADES
    assert (
        average_win_loss_ratio([_yen("10"), _yen("0")])
        is MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )
    assert average_win_loss_ratio([_yen("-10")]) is MetricUnavailableReason.UNDEFINED_DENOMINATOR


def test_the_t01_run_has_no_losing_trade_so_the_ratio_is_unavailable() -> None:
    """T01 の完了取引は勝ち 1 件だけなので #22 は `UNDEFINED_DENOMINATOR`（0 や無限大にしない）。"""
    value = _value(_evaluate_v4(), MetricId.AVERAGE_WIN_LOSS_RATIO)
    assert isinstance(value, Unavailable)
    assert value.reason is MetricUnavailableReason.UNDEFINED_DENOMINATOR


# --- #23 ----------------------------------------------------------------------------------------


def test_downside_ratio_penalises_only_the_losing_days() -> None:
    """日次の収益率 +10%・−10%・0%（資産 100 → 110 → 99 → 99）。

    `m = (0.1 − 0.1 + 0) ÷ 3 = 0`、`d = √(0.01 ÷ 3)`、比率 0。勝ちの日を大きくしても `d` は
    変わらない（利益の側のばらつきを罰しない。決定 161）。
    """
    assert annualized_downside_ratio(_amounts("100", "110", "99", "99")) == 0
    bigger_win = annualized_downside_ratio(_amounts("100", "130", "117", "117"))
    smaller_win = annualized_downside_ratio(_amounts("100", "120", "108", "108"))
    assert isinstance(bigger_win, Decimal) and isinstance(smaller_win, Decimal)
    assert bigger_win > smaller_win > 0


def test_downside_ratio_matches_the_hand_formula() -> None:
    """`r = (0.02, −0.01, 0.01)`: `m = 0.02 ÷ 3`、`d = √(0.0001 ÷ 3)`、比率 `(m ÷ d) × √260`。"""
    equities = _amounts("100", "102", "100.98", "101.9898")
    with localcontext(kernel_context()):
        returns = [b / a - 1 for a, b in zip(equities, equities[1:], strict=False)]
        mean = sum(returns, Decimal(0)) / 3
        downside = (sum((min(r, Decimal(0)) ** 2 for r in returns), Decimal(0)) / 3).sqrt()
        expected = (mean / downside) * Decimal(260).sqrt()
    assert annualized_downside_ratio(equities) == expected


def test_downside_ratio_is_unavailable_like_the_sharpe_ratio() -> None:
    """`T < 2` は `NO_OBSERVATIONS`、負けの日が無い・前日の資産 0 は `UNDEFINED_DENOMINATOR`。"""
    assert annualized_downside_ratio(_amounts("100", "101")) is (
        MetricUnavailableReason.NO_OBSERVATIONS
    )
    assert annualized_downside_ratio(_amounts("100", "101", "102")) is (
        MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )
    assert annualized_downside_ratio(_amounts("100", "0", "1")) is (
        MetricUnavailableReason.UNDEFINED_DENOMINATOR
    )


def test_the_t01_downside_ratio_uses_the_same_daily_equities_as_the_sharpe_ratio() -> None:
    """#23 は #17 と同じ日次の資産から作る（D24 の仮置き 8）。T01 の日次の資産（`test_evaluate_run`
    の #17 の検算と同じ 11 点）で、負けの日は 1/8 の 1 日だけ。"""
    daily = _amounts(
        "1000000",  # E_0 初期残高
        "1000000",  # 1/5
        "1036736",  # 1/6
        "1036736",  # 1/7
        "1036706",  # 1/8
        "1036706",  # 1/9
        "1036706",  # 1/12
        "1036706",  # 1/13
        "1036706",  # 1/14
        "1036706",  # 1/15
        "1051706",  # 1/16
    )
    expected = annualized_downside_ratio(daily)
    assert isinstance(expected, Decimal) and expected > 0
    report = _evaluate_v4()
    assert _ratio(report, MetricId.ANNUALIZED_DOWNSIDE_RATIO) == expected
    assert _ratio(report, MetricId.ANNUALIZED_SHARPE_RATIO) == annualized_sharpe_ratio(daily)
    counts = {record.metric_id: record.observation_count for record in report.metrics}
    assert counts[MetricId.ANNUALIZED_DOWNSIDE_RATIO] == counts[MetricId.ANNUALIZED_SHARPE_RATIO]
