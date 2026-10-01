"""元データの再取得（補充）のテストの材料（D03 §14.16、D08 §12）。

- 録画した時間ファイル 2 本: 2026-09-30 の実証で提供元 dukascopy から取得した USDJPY
  2020-11-30 の 00 時・01 時（UTC）の bi5（研究履歴区分の中）。通信はしない。
- `PROBE_BARS`: 同じ実証で、この 2 本の bid から作った 15分足 8 本・1時間足 2 本。00 時の足は
  原データ（`histdata`）と始値・高値・安値・終値の差がすべて 0 だった（D03 §14.3）。
- `FakeTickSource`: 固定の応答を返す偽の取得元（D03 §14.16「取得の流れを、固定の応答を返す
  偽の取得で確かめる」）。時計は仮想で、待ちは時計を進めるだけで実際には待たない。
- `provider_ref`・`manifest_for`: 計画の材料を人工的に作る。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Final

from odyssey_fx.common.money import Price, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.adapters.dukascopy_source import decode_bi5
from odyssey_fx.marketdata.domain.bar import Bar, Provenance, ProvenanceKind
from odyssey_fx.marketdata.domain.calendar import TradingCalendar
from odyssey_fx.marketdata.domain.classification import (
    ClassificationOutcome,
    ResolvedClassification,
)
from odyssey_fx.marketdata.domain.integrity import CheckKind
from odyssey_fx.marketdata.domain.refill import (
    CalendarRef,
    CommunicationSettings,
    DecodedTicks,
    FailureKind,
    HttpResult,
    ProviderRef,
    ProviderSettings,
    ProviderSymbol,
    content_digest_of,
)
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId
from odyssey_fx.marketdata.domain.snapshot import (
    Approval,
    BasisDeclaration,
    ConversionRecord,
    SnapshotManifest,
    SourceFile,
)
from tests.fixtures.synthetic import market

FIXTURE_DIR: Final = Path(__file__).resolve().parent

#: 録画した時間ファイル（USDJPY 2020-11-30 の 00 時・01 時 UTC）。
BI5_00H: Final = (FIXTURE_DIR / "USDJPY_2020-11-30_00h.bi5").read_bytes()
BI5_01H: Final = (FIXTURE_DIR / "USDJPY_2020-11-30_01h.bi5").read_bytes()

HOUR_00: Final = UtcTime.parse("2020-11-30T00:00:00Z")
HOUR_01: Final = UtcTime.parse("2020-11-30T01:00:00Z")

URL_TEMPLATE: Final = (
    "https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month0}/{day}/{hour}h_ticks.bi5"
)

#: 実証で bid から作った足（`(時間足, 開始時刻, 始値, 高値, 安値, 終値)`）。
PROBE_BARS: Final = (
    ("15m", "2020-11-30T00:00:00Z", "104.089", "104.108", "104.048", "104.059"),
    ("15m", "2020-11-30T00:15:00Z", "104.06", "104.063", "104.019", "104.019"),
    ("15m", "2020-11-30T00:30:00Z", "104.019", "104.023", "103.972", "104.017"),
    ("15m", "2020-11-30T00:45:00Z", "104.018", "104.02", "103.846", "103.88"),
    ("15m", "2020-11-30T01:00:00Z", "103.879", "103.886", "103.831", "103.851"),
    ("15m", "2020-11-30T01:15:00Z", "103.853", "103.886", "103.84", "103.881"),
    ("15m", "2020-11-30T01:30:00Z", "103.88", "103.902", "103.88", "103.899"),
    ("15m", "2020-11-30T01:45:00Z", "103.898", "103.9", "103.857", "103.866"),
    ("1h", "2020-11-30T00:00:00Z", "104.089", "104.108", "103.846", "103.88"),
    ("1h", "2020-11-30T01:00:00Z", "103.879", "103.902", "103.831", "103.866"),
)

USDJPY_15M: Final = market.series(market.USDJPY, "15m")
USDJPY_1H: Final = market.series(market.USDJPY, "1h")

#: 研究履歴の原系列を受け入れた人工 snapshot の範囲（日曜の開場から月曜 06 時まで）。
WINDOW: Final = Interval(
    start=UtcTime.parse("2020-11-29T22:00:00Z"), end=UtcTime.parse("2020-11-30T06:00:00Z")
)


#: 補充の計画に使うカレンダー（版 2。D03 §14.4 は版 2 または版 3 を受ける）。人工の週の開閉で、
#: 2020-11-30 の前後に休場の宣言は無い。
REFILL_CALENDAR: Final = market.calendar(version=2)


def decoded(body: bytes) -> DecodedTicks:
    """録画した bi5 を解凍する。"""
    return decode_bi5(body)


def probe_bar(
    timeframe_id: str, start: str, *, kind: ProvenanceKind = ProvenanceKind.HISTDATA
) -> Bar:
    """実証の値の足を 1 本作る（原データの代わり）。"""
    for row in PROBE_BARS:
        if row[0] == timeframe_id and row[1] == start:
            series = USDJPY_15M if timeframe_id == "15m" else USDJPY_1H
            definition = market.TF_15M if timeframe_id == "15m" else market.TF_1H
            bar_start = UtcTime.parse(start)
            interval = definition.boundaries(bar_start)
            return Bar(
                series=series,
                interval=interval,
                open=Price(decimal_from_str(row[2])),
                high=Price(decimal_from_str(row[3])),
                low=Price(decimal_from_str(row[4])),
                close=Price(decimal_from_str(row[5])),
                volume=decimal_from_str("0"),
                available_at=interval.end,
                provenance=Provenance(
                    kind=kind, source_ref=f"data/raw/market/USDJPY_{timeframe_id}_merged.csv"
                ),
            )
    raise KeyError((timeframe_id, start))


def raw_bars(
    *,
    calendar: TradingCalendar | None = None,
    drop_hours: Iterable[UtcTime] = (HOUR_01,),
    window: Interval = WINDOW,
) -> dict[SeriesId, tuple[Bar, ...]]:
    """USDJPY の原データの代わり（15分足・1時間足）。

    00 時台の足は実証の値（録画した tick と一致する）、他は人工の値。`drop_hours` の時間は
    15分足・1時間足の両方を落とす（データ欠損を作る）。
    """
    trading = market.calendar() if calendar is None else calendar
    dropped = set(drop_hours)
    result: dict[SeriesId, tuple[Bar, ...]] = {}
    for series, definition in ((USDJPY_15M, market.TF_15M), (USDJPY_1H, market.TF_1H)):
        bars: list[Bar] = []
        for bar in market.make_bars(series, definition, trading, window):
            hour = bar.bar_start.value.replace(minute=0)
            if UtcTime(hour) in dropped:
                continue
            if UtcTime(hour) == HOUR_00:
                bar = probe_bar(definition.ref.id, str(bar.bar_start))
            bars.append(bar)
        result[series] = tuple(bars)
    return result


def gap_resolutions(
    series_bars: Mapping[SeriesId, Sequence[Bar]] | None = None,
    hours: Iterable[UtcTime] = (HOUR_01,),
    outcome: ClassificationOutcome = ClassificationOutcome.DATA_GAP,
) -> tuple[ResolvedClassification, ...]:
    """落とした時間の足をデータ欠損と分類した記録（警告 1 件ごと）。"""
    del series_bars
    resolved: list[ResolvedClassification] = []
    for hour in hours:
        resolved.append(
            ResolvedClassification(
                kind=CheckKind.MISSING_EXPECTED_BAR,
                series_id=USDJPY_1H,
                interval=Interval(start=hour, end=hour + timedelta(hours=1)),
                outcome=outcome,
            )
        )
        for index in range(4):
            start = hour + timedelta(minutes=15 * index)
            resolved.append(
                ResolvedClassification(
                    kind=CheckKind.MISSING_EXPECTED_BAR,
                    series_id=USDJPY_15M,
                    interval=Interval(start=start, end=start + timedelta(minutes=15)),
                    outcome=outcome,
                )
            )
    return tuple(resolved)


def manifest_for(
    resolved: Sequence[ResolvedClassification],
    *,
    approved: bool = True,
    sources: Sequence[SourceFile] | None = None,
) -> SnapshotManifest:
    """分類だけを持つ人工の manifest（partition は持たない）。"""
    files = (
        tuple(sources)
        if sources is not None
        else tuple(
            SourceFile(
                path=f"data/raw/market/USDJPY_{tf}_merged.csv",
                sha256="0" * 64,
                rows=1,
                symbol=market.USDJPY,
                timeframe=series.timeframe,
                declared_basis=PriceBasis.BID,
            )
            for tf, series in (("15m", USDJPY_15M), ("1h", USDJPY_1H))
        )
    )
    manifest = SnapshotManifest(
        created_at=UtcTime.parse("2026-10-01T00:00:00Z"),
        basis_declaration=BasisDeclaration(value=PriceBasis.BID),
        sources=files,
        conversion=ConversionRecord(
            code_version="test",
            time_convention="explicit_offset_utc",
            aggregation_rule_version="test",
            calendar_id="fx_ny17",
            calendar_version=1,
        ),
        series=(),
        partitions=(),
        integrity_report_ref=ContentDigest.sha256("1" * 64),
        resolved_classifications=tuple(resolved),
    )
    if approved:
        manifest = manifest.with_approval(
            Approval(approved_by="tester", approved_at=UtcTime.parse("2026-10-01T00:00:00Z"))
        )
    return manifest


def communication(**overrides: int) -> CommunicationSettings:
    """通信の値（既定は D03 §14.9 の試行の初期値）。"""
    values = {
        "request_interval_seconds": 8,
        "request_timeout_seconds": 60,
        "max_retries": 5,
        "backoff_initial_seconds": 30,
        "backoff_max_seconds": 480,
        "pause_after_consecutive_failures": 3,
        "pause_seconds": 180,
    }
    values.update(overrides)
    return CommunicationSettings(**values)


def provider_ref(
    *, comm: CommunicationSettings | None = None, url_template: str = URL_TEMPLATE
) -> ProviderRef:
    """提供元の設定（USDJPY と EURUSD）。"""
    settings = ProviderSettings(
        id="dukascopy_tick",
        version=1,
        url_template=url_template,
        symbols=(
            ProviderSymbol(
                symbol=market.EURUSD, price_scale=100000, pip_size=decimal_from_str("0.0001")
            ),
            ProviderSymbol(
                symbol=market.USDJPY, price_scale=1000, pip_size=decimal_from_str("0.01")
            ),
        ),
        communication=communication() if comm is None else comm,
    )
    content = {"settings": dict(settings.payload())}
    return ProviderRef(content_digest=content_digest_of(content), settings=settings)


def calendar_ref(calendar: TradingCalendar | None = None) -> CalendarRef:
    """カレンダーの記録（人工のカレンダーの識別と版、正規化内容は簡略）。"""
    trading = REFILL_CALENDAR if calendar is None else calendar
    content = {"id": trading.id, "version": trading.version, "note": "synthetic"}
    return CalendarRef(
        id=trading.id,
        version=trading.version,
        content_digest=content_digest_of(content),
        content=content,
    )


class FakeTickSource:
    """固定の応答を返す偽の取得元（D03 §14.16）。時計は仮想で、待ちは時計を進めるだけ。

    `responses` は URL ごとの応答の列。応答は `bytes`（200 の中身）、`int`（HTTP の状態）、
    `FailureKind`（応答なし）のどれか。列を使い切った URL への要求はテストの誤りとして止める。
    """

    def __init__(self, responses: Mapping[str, Sequence[bytes | int | FailureKind]]) -> None:
        self._responses = {url: deque(items) for url, items in responses.items()}
        self.clock = UtcTime.parse("2026-10-01T00:00:00Z")
        self.requests: list[tuple[UtcTime, str]] = []
        self.waits: list[float] = []

    def request(self, url: str, timeout_seconds: int) -> HttpResult:
        del timeout_seconds
        queue = self._responses.get(url)
        if not queue:
            raise AssertionError(f"unexpected request to {url}")
        self.requests.append((self.clock, url))
        response = queue.popleft()
        self.clock = self.clock + timedelta(seconds=1)
        if isinstance(response, bytes):
            return HttpResult(
                url=url, status=200, body=response, failure=None, detail="", fetched_at=self.clock
            )
        if isinstance(response, FailureKind):
            return HttpResult(
                url=url,
                status=None,
                body=b"",
                failure=response,
                detail="fake",
                fetched_at=self.clock,
            )
        return HttpResult(
            url=url, status=response, body=b"", failure=None, detail="fake", fetched_at=self.clock
        )

    def decode(self, body: bytes) -> DecodedTicks:
        return decode_bi5(body)

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.clock = self.clock + timedelta(seconds=seconds)

    def now(self) -> UtcTime:
        return self.clock

    def remaining(self) -> dict[str, int]:
        """使われなかった応答の数（URL ごと）。"""
        return {url: len(queue) for url, queue in self._responses.items() if queue}


def url_of(hour: UtcTime, symbol: str = "USDJPY") -> str:
    """時間ファイルの URL（月は 0 始まり）。"""
    moment = hour.value
    return (
        f"https://datafeed.dukascopy.com/datafeed/{symbol}/{moment.year:04d}/"
        f"{moment.month - 1:02d}/{moment.day:02d}/{moment.hour:02d}h_ticks.bi5"
    )
