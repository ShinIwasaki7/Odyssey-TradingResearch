"""再取得（補充）の domain 型（D03 §14.5・§14.9・§14.10）。

- 保管場所のパスの月は 1 始まり、URL の月は 0 始まり（URL を組み立てるときだけ 1 を引く）。
- 価格は整数値を桁で割って作る（浮動小数を経由しない）。
- 指数バックオフ（30 秒から倍にして 480 秒で頭打ち）。
- 取得計画は `plan.json` の内容から読み戻せ、`plan_id` は内容だけから決まる。
- 取得記録の行は内容から読み戻せる。最終結果の不変条件（出所を写さない等）。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from odyssey_fx.common import canonical
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import (
    AttemptRecord,
    FailureKind,
    FinalResult,
    HourKey,
    HourOutcome,
    Invalidation,
    ManifestHour,
    PauseEnd,
    PauseStart,
    PlanHour,
    ProviderSymbol,
    RefillFilter,
    RefillManifestCore,
    RefillPlan,
    RetryMark,
    TargetBar,
    ValidationRecord,
    journal_entry_from_payload,
)
from tests.fixtures.refill import (
    HOUR_00,
    HOUR_01,
    USDJPY_1H,
    USDJPY_15M,
    calendar_ref,
    communication,
    provider_ref,
)
from tests.fixtures.synthetic import market

AT = UtcTime.parse("2026-10-01T00:00:00Z")
DIGEST = "a" * 64


def _plan(**overrides: object) -> RefillPlan:
    targets = [
        TargetBar(
            series=USDJPY_15M, interval=Interval(start=HOUR_01, end=HOUR_01 + timedelta(minutes=15))
        ),
        TargetBar(
            series=USDJPY_1H, interval=Interval(start=HOUR_01, end=HOUR_01 + timedelta(hours=1))
        ),
    ]
    values: dict[str, object] = {
        "snapshot_id": "b" * 64,
        "calendar": calendar_ref(),
        "provider": provider_ref(),
        "filter": RefillFilter(),
        "target_bars": tuple(sorted(targets, key=lambda bar: bar.sort_key())),
        "hours": (
            PlanHour(hour=HourKey(symbol=market.USDJPY, start=HOUR_00), reference=True),
            PlanHour(hour=HourKey(symbol=market.USDJPY, start=HOUR_01), reference=False),
        ),
    }
    values.update(overrides)
    return RefillPlan(**values)  # type: ignore[arg-type]


# --- 月の数え方（D03 §14.5）------------------------------------------------------


def test_the_archive_path_counts_months_from_one() -> None:
    hour = HourKey(symbol=market.USDJPY, start=UtcTime.parse("2020-01-05T07:00:00Z"))
    assert hour.archive_directory == "_ticks/USDJPY/2020/01/05/07h"


def test_the_url_counts_months_from_zero() -> None:
    settings = provider_ref().settings
    january = HourKey(symbol=market.USDJPY, start=UtcTime.parse("2020-01-05T07:00:00Z"))
    december = HourKey(symbol=market.USDJPY, start=UtcTime.parse("2020-12-31T23:00:00Z"))
    assert settings.url_for(january).endswith("/USDJPY/2020/00/05/07h_ticks.bi5")
    assert settings.url_for(december).endswith("/USDJPY/2020/11/31/23h_ticks.bi5")


def test_an_hour_key_must_be_on_the_hour() -> None:
    with pytest.raises(MarketDataValueError):
        HourKey(symbol=market.USDJPY, start=UtcTime.parse("2020-01-05T07:15:00Z"))


# --- 価格の桁（D03 §14.6）-----------------------------------------------------------


def test_prices_are_exact_decimal_divisions() -> None:
    jpy = ProviderSymbol(symbol=market.USDJPY, price_scale=1000, pip_size=decimal_from_str("0.01"))
    usd = ProviderSymbol(
        symbol=market.EURUSD, price_scale=100000, pip_size=decimal_from_str("0.0001")
    )
    assert jpy.price_of(104089) == decimal_from_str("104.089")
    assert usd.price_of(119680) == decimal_from_str("1.19680")


def test_a_price_scale_must_be_a_power_of_ten() -> None:
    with pytest.raises(MarketDataValueError):
        ProviderSymbol(symbol=market.USDJPY, price_scale=1500, pip_size=decimal_from_str("0.01"))


# --- 通信の値（D03 §14.9）-----------------------------------------------------------


def test_the_backoff_doubles_up_to_the_maximum() -> None:
    comm = communication()
    assert [comm.backoff_seconds(index) for index in range(1, 7)] == [30, 60, 120, 240, 480, 480]


def test_the_source_digest_ignores_communication_values() -> None:
    first = provider_ref(comm=communication(request_interval_seconds=8)).settings
    second = provider_ref(comm=communication(request_interval_seconds=20)).settings
    assert first.source_digest() == second.source_digest()
    third = provider_ref(
        url_template="https://example.invalid/{symbol}/{year}/{month0}/{day}/{hour}"
    ).settings
    assert third.source_digest() != first.source_digest()


def test_the_source_digest_ignores_price_scales_and_pips() -> None:
    """価格の桁・pip は保管した後の集約と比較にだけ使うので保管場所の鍵に入れない（D03 §14.5）。"""
    base = provider_ref().settings
    changed = replace(
        base,
        symbols=tuple(
            replace(entry, pip_size=entry.pip_size * 10, price_scale=entry.price_scale * 10)
            for entry in base.symbols
        ),
    )
    assert changed.source_digest() == base.source_digest()


def test_the_url_template_must_hold_each_placeholder_once() -> None:
    with pytest.raises(MarketDataValueError):
        provider_ref(url_template="https://example.invalid/{symbol}/{year}/{day}/{hour}")


# --- 取得計画（D03 §14.10）-----------------------------------------------------------


def test_a_plan_reads_back_from_its_payload_with_the_same_id() -> None:
    plan = _plan()
    payload = canonical.encode(plan.identity_payload())
    import json

    restored = RefillPlan.from_payload(json.loads(payload))
    assert restored == plan
    assert restored.plan_id() == plan.plan_id()


def test_the_plan_id_reflects_the_provider_content() -> None:
    plan = _plan()
    other = _plan(provider=provider_ref(comm=communication(request_interval_seconds=20)))
    assert plan.plan_id() != other.plan_id()


def test_a_plan_marks_reference_hours_exactly_when_they_hold_no_target() -> None:
    with pytest.raises(MarketDataValueError):
        _plan(
            hours=(
                PlanHour(hour=HourKey(symbol=market.USDJPY, start=HOUR_00), reference=False),
                PlanHour(hour=HourKey(symbol=market.USDJPY, start=HOUR_01), reference=False),
            )
        )


def test_a_plan_without_target_bars_is_not_built() -> None:
    with pytest.raises(MarketDataValueError):
        _plan(target_bars=())


def test_a_tampered_plan_payload_is_rejected() -> None:
    import json

    payload = json.loads(canonical.encode(_plan().identity_payload()))
    payload["format"] = "something_else"
    with pytest.raises(MarketDataValueError):
        RefillPlan.from_payload(payload)


# --- 取得記録の行（D03 §14.11.1 の W3）------------------------------------------------


def _fetched(**overrides: object) -> FinalResult:
    values: dict[str, object] = {
        "hour": HourKey(symbol=market.USDJPY, start=HOUR_00),
        "outcome": HourOutcome.FETCHED,
        "from_archive": False,
        "tick_digest": DIGEST,
        "tick_count": 3,
        "archive_file": "_ticks/USDJPY/2020/11/30/00h/x/y.json",
        "url": "https://example.invalid/x",
        "fetched_at": AT,
        "http_status": 200,
        "response_sha256": DIGEST,
        "attempts": 1,
        "failure": None,
        "detail": "",
        "at": AT,
    }
    values.update(overrides)
    return FinalResult(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "entry",
    [
        _fetched(),
        _fetched(
            from_archive=True,
            url=None,
            fetched_at=None,
            http_status=None,
            response_sha256=None,
            attempts=0,
        ),
        FinalResult(
            hour=HourKey(symbol=market.USDJPY, start=HOUR_01),
            outcome=HourOutcome.NOT_FETCHED,
            from_archive=False,
            tick_digest=None,
            tick_count=None,
            archive_file=None,
            url="https://example.invalid/x",
            fetched_at=None,
            http_status=404,
            response_sha256=None,
            attempts=1,
            failure=FailureKind.HTTP_404,
            detail="Not Found",
            at=AT,
        ),
        AttemptRecord(
            hour=HourKey(symbol=market.USDJPY, start=HOUR_01),
            attempt=2,
            failure=FailureKind.TIMEOUT,
            http_status=None,
            detail="timed out",
            at=AT,
        ),
        AttemptRecord(
            hour=HourKey(symbol=market.USDJPY, start=HOUR_01),
            attempt=0,
            failure=FailureKind.HOUR_LOCKED,
            http_status=None,
            detail="locked",
            at=AT,
        ),
        RetryMark(hour=HourKey(symbol=market.USDJPY, start=HOUR_01), at=AT),
        Invalidation(hour=HourKey(symbol=market.USDJPY, start=HOUR_01), reason="gone", at=AT),
        PauseStart(consecutive_failures=3, seconds=180, at=AT),
        PauseEnd(at=AT),
        ValidationRecord(passed=False, reasons=("mismatch",), at=AT),
    ],
)
def test_journal_entries_read_back_from_their_payload(entry: object) -> None:
    import json

    payload = json.loads(canonical.encode(entry.payload()))  # type: ignore[attr-defined]
    assert journal_entry_from_payload(payload) == entry


def test_a_result_read_from_the_archive_does_not_copy_the_provenance() -> None:
    with pytest.raises(MarketDataValueError):
        _fetched(from_archive=True, attempts=0)


def test_a_not_fetched_result_has_no_tick_digest() -> None:
    with pytest.raises(MarketDataValueError):
        _fetched(outcome=HourOutcome.NOT_FETCHED, failure=FailureKind.TIMEOUT)


def test_an_outcome_must_match_the_tick_count() -> None:
    with pytest.raises(MarketDataValueError):
        _fetched(outcome=HourOutcome.FETCHED_EMPTY)


def test_an_unknown_journal_kind_is_rejected() -> None:
    with pytest.raises(MarketDataValueError):
        journal_entry_from_payload({"kind": "something"})


def test_only_an_hour_lock_record_has_attempt_zero() -> None:
    """時間のロックの競合は通信していないので試行の回数 0。ほかの失敗は 1 以上。"""
    hour = HourKey(symbol=market.USDJPY, start=HOUR_01)
    with pytest.raises(MarketDataValueError):
        AttemptRecord(
            hour=hour, attempt=0, failure=FailureKind.TIMEOUT, http_status=None, detail="", at=AT
        )
    with pytest.raises(MarketDataValueError):
        AttemptRecord(
            hour=hour,
            attempt=1,
            failure=FailureKind.HOUR_LOCKED,
            http_status=None,
            detail="",
            at=AT,
        )


# --- 補充の manifest のうち PR 1 が読む部分 -------------------------------------------


def _manifest_hours() -> list[dict[str, object]]:
    return [
        {
            "hour": HourKey(symbol=market.USDJPY, start=HOUR_00).payload(),
            "outcome": "FETCHED",
            "tick_digest": DIGEST,
        },
        {
            "hour": HourKey(symbol=market.USDJPY, start=HOUR_01).payload(),
            "outcome": "NOT_FETCHED",
            "tick_digest": None,
        },
    ]


def test_the_manifest_core_reads_back_and_ignores_other_keys() -> None:
    core = RefillManifestCore.from_payload(
        {"plan_id": DIGEST, "hours": _manifest_hours(), "refill_id": "x", "files": []}
    )
    assert core.plan_id == DIGEST
    assert [item.outcome for item in core.hours] == [
        HourOutcome.FETCHED,
        HourOutcome.NOT_FETCHED,
    ]
    assert RefillManifestCore.from_payload(core.payload()) == core


@pytest.mark.parametrize(
    "payload",
    [
        {"plan_id": DIGEST},
        {"hours": _manifest_hours()},
        {"plan_id": "short", "hours": _manifest_hours()},
        {"plan_id": DIGEST, "hours": []},
        {"plan_id": DIGEST, "hours": list(reversed(_manifest_hours()))},
        {"plan_id": DIGEST, "hours": [_manifest_hours()[0], _manifest_hours()[0]]},
        {"plan_id": DIGEST, "hours": [{**_manifest_hours()[0], "tick_digest": None}]},
        {"plan_id": DIGEST, "hours": [{**_manifest_hours()[1], "tick_digest": DIGEST}]},
        {"plan_id": DIGEST, "hours": [{"hour": _manifest_hours()[0]["hour"]}]},
        [],
    ],
)
def test_an_invalid_manifest_core_is_rejected(payload: object) -> None:
    with pytest.raises(MarketDataValueError):
        RefillManifestCore.from_payload(payload)


def test_a_manifest_hour_keeps_digests_only_for_fetched_hours() -> None:
    hour = HourKey(symbol=market.USDJPY, start=HOUR_00)
    assert ManifestHour(hour=hour, outcome=HourOutcome.FETCHED_EMPTY, tick_digest=DIGEST)
    with pytest.raises(MarketDataValueError):
        ManifestHour(hour=hour, outcome=HourOutcome.NOT_FETCHED, tick_digest=DIGEST)
