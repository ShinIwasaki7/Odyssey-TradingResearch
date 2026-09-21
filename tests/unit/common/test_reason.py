"""`odyssey_fx.common.reason` の単体テスト（D02 §8・§11）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import OrderId, PositionId
from odyssey_fx.common.money import CurrencyCode, Money, decimal_from_str
from odyssey_fx.common.reason import (
    CarryNotAllowedDetail,
    DataErrorDetail,
    ExpiryDetail,
    MissingInputReason,
    NoCandidateDetail,
    PositionClosedDetail,
    Reason,
    ReasonCode,
    ReasonDetail,
    RiskRejectionDetail,
    RunEndDetail,
)
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, PhaseRank, ProcessingPoint, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef

D = decimal_from_str
JPY = CurrencyCode("JPY")
USD = CurrencyCode("USD")
MOMENT = UtcTime.from_components(2026, 3, 1, 12)
POINT = ProcessingPoint(MOMENT, PhaseRank(1, "ADMISSION"), 0)


# --- 語彙 -------------------------------------------------------------------


def test_reason_code_vocabulary_matches_the_design() -> None:
    """D02 §8.1 の表（上位設計書 §4.7.14 の写し）と列挙が一致する。

    後半8件は ADR-0031・ADR-0032・ADR-0033 と D06 の決定で語彙へ加わり、列挙への反映は
    取引機会の状態機械を実装する段階2 で行うと定められていたもの（D02 §8.1 v1.3〜v1.5）。
    """
    assert {code.value for code in ReasonCode} == {
        "RISK",
        "NO_CANDIDATE",
        "RUN_END",
        "DATA_ERROR",
        "EXPIRED",
        "CARRY_NOT_ALLOWED",
        "POSITION_CLOSED",
        "MARKET_STATE_INVALIDATED",
        "SUPERSEDED",
        "CLOSED_BY_ORDER_ACCEPTANCE",
        "CONCURRENCY_LIMIT_REACHED",
        "ORDER_ATTEMPT_REJECTED",
        "FULFILLED_BY_ORDER_ACCEPTANCE",
        "REQUEST_SUPERSEDED",
        "PROTECTION_INVALID",
    }


def test_missing_input_reason_vocabulary_matches_the_design() -> None:
    assert {reason.value for reason in MissingInputReason} == {
        "WARMUP_INSUFFICIENT",
        "INPUT_MISSING_OR_INVALID",
        "LATEST_BAR_UNAVAILABLE",
        "MAX_AGE_EXCEEDED",
    }


def test_missing_input_reason_is_separate_from_reason_code() -> None:
    """評価見送りの理由は受付拒否の理由とは別の enum（D02 §8.3）。"""
    assert not isinstance(MissingInputReason.WARMUP_INSUFFICIENT, ReasonCode)
    assert MissingInputReason.WARMUP_INSUFFICIENT != ReasonCode.RISK  # type: ignore[comparison-overlap]


# --- Reason と詳細の対応 ----------------------------------------------------


def test_reason_accepts_a_matching_detail() -> None:
    reason = Reason(ReasonCode.RUN_END, RunEndDetail(run_end=MOMENT))
    assert reason.code is ReasonCode.RUN_END
    assert str(reason) == "RUN_END"


def test_reason_allows_no_detail() -> None:
    assert Reason(ReasonCode.RISK).detail is None


def test_reason_rejects_a_mismatched_detail() -> None:
    with pytest.raises(KernelValueError, match="carries RUN_END, but the reason is RISK"):
        Reason(ReasonCode.RISK, RunEndDetail(run_end=MOMENT))


def test_reason_rejects_a_detail_without_a_reason_code() -> None:
    @dataclass(frozen=True, slots=True)
    class _Bogus:
        note: str

    with pytest.raises(KernelValueError, match="ReasonDetail"):
        Reason(ReasonCode.RISK, _Bogus("x"))  # type: ignore[arg-type]


# --- 詳細型は不変でなければならない（Codex レビュー round 2 指摘E）----------
#
# `code` を持つだけの任意のオブジェクトを受けると、`Reason` 自体が frozen でも詳細の中身が
# 後から書き換わり、記録した理由が実行後に変わってしまう（D02 §1 規則2・§8.2）。


def test_reason_rejects_a_mutable_dataclass_detail() -> None:
    @dataclass
    class _MutableDetail:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        note: str = "x"

    with pytest.raises(KernelValueError, match="frozen dataclass"):
        Reason(ReasonCode.RISK, _MutableDetail())


def test_reason_rejects_a_plain_object_detail() -> None:
    class _PlainDetail:
        code: ClassVar[ReasonCode] = ReasonCode.RISK

    with pytest.raises(KernelValueError, match="frozen dataclass"):
        Reason(ReasonCode.RISK, _PlainDetail())


def test_a_mutable_detail_really_could_have_changed_after_the_fact() -> None:
    """拒否する理由の実証: 可変な詳細は構築後に中身を書き換えられる。"""

    @dataclass
    class _MutableDetail:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        note: str = "before"

    detail = _MutableDetail()
    detail.note = "after"
    assert detail.note == "after"
    with pytest.raises(KernelValueError, match="frozen dataclass"):
        Reason(ReasonCode.RISK, detail)


# --- 詳細の中身も再帰的に不変であること（Codex レビュー round 3 指摘）-------
#
# frozen dataclass でも `notes: list[str]` のような可変フィールドを持てば、記録したあとで
# 中身を書き換えられてしまう。根拠として保存する以上、葉まで不変でなければならない。


def test_reason_rejects_a_detail_holding_a_list() -> None:
    @dataclass(frozen=True)
    class _WithList:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        notes: list[str]

    with pytest.raises(KernelValueError, match=r"Reason\.detail\.notes must be immutable"):
        Reason(ReasonCode.RISK, _WithList(["a"]))


@pytest.mark.parametrize(
    ("factory", "label"),
    [
        (lambda: {"k": 1}, "dict"),
        (lambda: {1, 2}, "set"),
        (lambda: bytearray(b"x"), "bytearray"),
    ],
    ids=["dict", "set", "bytearray"],
)
def test_reason_rejects_other_mutable_field_values(factory: object, label: str) -> None:
    @dataclass(frozen=True)
    class _WithMutable:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        payload: object

    with pytest.raises(KernelValueError, match="must be immutable"):
        Reason(ReasonCode.RISK, _WithMutable(factory()))  # type: ignore[operator]


def test_reason_rejects_a_mutable_value_nested_inside_a_tuple() -> None:
    """検査は葉まで届く。エラーには問題のある位置が示される。"""

    @dataclass(frozen=True)
    class _WithTuple:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        items: tuple[object, ...]

    with pytest.raises(KernelValueError, match=r"Reason\.detail\.items\[1\] must be immutable"):
        Reason(ReasonCode.RISK, _WithTuple(("ok", ["bad"])))


def test_reason_rejects_a_mutable_dataclass_nested_inside_a_frozen_one() -> None:
    @dataclass
    class _MutableInner:
        value: int = 1

    @dataclass(frozen=True)
    class _WithInner:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        inner: object

    with pytest.raises(KernelValueError, match=r"Reason\.detail\.inner must be a frozen"):
        Reason(ReasonCode.RISK, _WithInner(_MutableInner()))


def test_reason_accepts_a_nested_tuple_of_frozen_dataclasses() -> None:
    @dataclass(frozen=True)
    class _Inner:
        value: int

    @dataclass(frozen=True)
    class _WithTuple:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        items: tuple[_Inner, ...]

    detail = _WithTuple((_Inner(1), _Inner(2)))
    assert Reason(ReasonCode.RISK, detail).detail is detail


def test_reason_accepts_immutable_leaf_values() -> None:
    """`None`・数値・文字列・列挙・十進数・時刻は葉としてそのまま通る。"""

    @dataclass(frozen=True)
    class _Leaves:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        nothing: object
        flag: bool
        count: int
        ratio: float
        text: str
        amount: object
        enumerated: object
        moment: object

    detail = _Leaves(None, True, 1, 0.5, "x", D("1.5"), MissingInputReason.MAX_AGE_EXCEEDED, MOMENT)
    assert Reason(ReasonCode.RISK, detail).detail is detail


def test_a_list_field_really_could_have_changed_after_the_fact() -> None:
    """拒否する理由の実証: 可変フィールドは記録後に中身を書き換えられる。"""

    @dataclass(frozen=True)
    class _WithList:
        code: ClassVar[ReasonCode] = ReasonCode.RISK
        notes: list[str]

    detail = _WithList(["before"])
    detail.notes.append("after")
    assert detail.notes == ["before", "after"]
    with pytest.raises(KernelValueError, match="must be immutable"):
        Reason(ReasonCode.RISK, detail)


def test_reason_still_accepts_every_detail_type_common_defines() -> None:
    """`common` の詳細型はすべて frozen dataclass なので、検査を通る。"""
    accepted = [
        Reason(ReasonCode.RUN_END, RunEndDetail(run_end=MOMENT)),
        Reason(ReasonCode.EXPIRED, ExpiryDetail(expires_at=MOMENT, observed_at=POINT)),
        Reason(
            ReasonCode.NO_CANDIDATE,
            NoCandidateDetail(expires_at=MOMENT, earliest_candidate=None),
        ),
        Reason(
            ReasonCode.CARRY_NOT_ALLOWED,
            CarryNotAllowedDetail(next_candidate=MOMENT, session_close=MOMENT),
        ),
        Reason(
            ReasonCode.POSITION_CLOSED,
            PositionClosedDetail(position_id=PositionId(1), closed_at=POINT),
        ),
        Reason(ReasonCode.RISK, RiskRejectionDetail("c", D("1"), D("2"))),
    ]
    assert all(reason.detail is not None for reason in accepted)


def test_reason_rejects_a_non_reason_code() -> None:
    with pytest.raises(KernelValueError, match="ReasonCode"):
        Reason("RISK")  # type: ignore[arg-type]


def test_domains_may_add_their_own_detail_types() -> None:
    """`ReasonDetail` は Protocol なので、各 domain が詳細型を追加できる（承認事項6）。"""

    @dataclass(frozen=True, slots=True)
    class _ExecutionDetail:
        code: ClassVar[ReasonCode] = ReasonCode.DATA_ERROR
        note: str

    detail = _ExecutionDetail("gap")
    assert isinstance(detail, ReasonDetail)
    assert Reason(ReasonCode.DATA_ERROR, detail).detail is detail


# --- 各詳細型 ---------------------------------------------------------------


def test_data_error_detail() -> None:
    detail = DataErrorDetail(
        symbol=Symbol("USDJPY"),
        timeframe=TimeframeRef("15m", 1),
        field="close",
        expected_interval=Interval(MOMENT, MOMENT + timedelta(hours=1)),
        observed_interval=None,
        cause="missing bar",
    )
    assert DataErrorDetail.code is ReasonCode.DATA_ERROR
    assert Reason(ReasonCode.DATA_ERROR, detail).detail is detail


def test_data_error_detail_validates_its_fields() -> None:
    common = {
        "symbol": Symbol("USDJPY"),
        "timeframe": TimeframeRef("15m", 1),
        "expected_interval": None,
        "observed_interval": None,
    }
    with pytest.raises(KernelValueError, match="field"):
        DataErrorDetail(field="", cause="c", **common)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match="cause"):
        DataErrorDetail(field="close", cause="", **common)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match="Symbol"):
        DataErrorDetail(
            symbol="USDJPY",  # type: ignore[arg-type]
            timeframe=TimeframeRef("15m", 1),
            field="close",
            expected_interval=None,
            observed_interval=None,
            cause="c",
        )
    with pytest.raises(KernelValueError, match="expected_interval"):
        DataErrorDetail(
            symbol=Symbol("USDJPY"),
            timeframe=TimeframeRef("15m", 1),
            field="close",
            expected_interval=MOMENT,  # type: ignore[arg-type]
            observed_interval=None,
            cause="c",
        )


def test_expiry_detail() -> None:
    detail = ExpiryDetail(expires_at=MOMENT, observed_at=POINT)
    assert ExpiryDetail.code is ReasonCode.EXPIRED
    assert Reason(ReasonCode.EXPIRED, detail).detail is detail
    with pytest.raises(KernelValueError, match="ProcessingPoint"):
        ExpiryDetail(expires_at=MOMENT, observed_at=MOMENT)  # type: ignore[arg-type]


def test_no_candidate_detail_allows_an_absent_candidate() -> None:
    assert NoCandidateDetail(expires_at=MOMENT, earliest_candidate=None).earliest_candidate is None
    assert NoCandidateDetail(expires_at=MOMENT, earliest_candidate=MOMENT).earliest_candidate
    with pytest.raises(KernelValueError, match="earliest_candidate"):
        NoCandidateDetail(expires_at=MOMENT, earliest_candidate=POINT)  # type: ignore[arg-type]


def test_run_end_detail() -> None:
    assert RunEndDetail.code is ReasonCode.RUN_END
    with pytest.raises(KernelValueError, match="UtcTime"):
        RunEndDetail(run_end=POINT)  # type: ignore[arg-type]


def test_carry_not_allowed_detail() -> None:
    detail = CarryNotAllowedDetail(next_candidate=MOMENT, session_close=MOMENT)
    assert CarryNotAllowedDetail.code is ReasonCode.CARRY_NOT_ALLOWED
    assert Reason(ReasonCode.CARRY_NOT_ALLOWED, detail).detail is detail
    with pytest.raises(KernelValueError, match="session_close"):
        CarryNotAllowedDetail(next_candidate=MOMENT, session_close=POINT)  # type: ignore[arg-type]


def test_position_closed_detail() -> None:
    detail = PositionClosedDetail(position_id=PositionId(1), closed_at=POINT)
    assert PositionClosedDetail.code is ReasonCode.POSITION_CLOSED
    assert Reason(ReasonCode.POSITION_CLOSED, detail).detail is detail
    with pytest.raises(KernelValueError, match="PositionId"):
        PositionClosedDetail(position_id=OrderId(1), closed_at=POINT)  # type: ignore[arg-type]


def test_risk_rejection_detail_accepts_money_and_decimal_pairs() -> None:
    money_detail = RiskRejectionDetail(
        check="max_total_risk", limit=Money(D("10000"), JPY), observed=Money(D("12000"), JPY)
    )
    ratio_detail = RiskRejectionDetail(check="max_risk_ratio", limit=D("0.02"), observed=D("0.03"))
    assert RiskRejectionDetail.code is ReasonCode.RISK
    assert Reason(ReasonCode.RISK, money_detail).detail is money_detail
    assert Reason(ReasonCode.RISK, ratio_detail).detail is ratio_detail


def test_risk_rejection_detail_requires_matching_kinds() -> None:
    """金額と比率を混ぜた比較不能な記録は拒否する（D02 §8.2 v1.2、確定）。"""
    with pytest.raises(KernelValueError, match="same type"):
        RiskRejectionDetail(check="c", limit=Money(D("1"), JPY), observed=D("1"))
    # 逆向き（上限が比率で実測が金額）も同じく拒否する。
    with pytest.raises(KernelValueError, match="same type"):
        RiskRejectionDetail(check="c", limit=D("1"), observed=Money(D("1"), JPY))


def test_risk_rejection_detail_requires_one_currency() -> None:
    with pytest.raises(KernelValueError, match="share a currency"):
        RiskRejectionDetail(check="c", limit=Money(D("1"), JPY), observed=Money(D("1"), USD))


def test_risk_rejection_detail_validates_check_and_values() -> None:
    with pytest.raises(KernelValueError, match="check"):
        RiskRejectionDetail(check="", limit=D("1"), observed=D("2"))
    with pytest.raises(KernelValueError, match="must be a Money or a Decimal"):
        RiskRejectionDetail(check="c", limit=1, observed=2)  # type: ignore[arg-type]
