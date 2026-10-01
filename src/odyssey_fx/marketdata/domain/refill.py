"""元データの再取得（補充）の domain 型（D03 §14、D01 §4 v2.9）。

研究履歴の原データの欠落（データ欠損と分類した足）を、提供元 dukascopy の tick から取り直して
補うための値を定める。tick の型・時間ファイルの結果の区分・取得計画・取得記録の行・保管場所の
出所の記録は本モジュールに置く（D01 §4 v2.9「型の置き場所」）。規則（対象足の導出・集約・
検証・取得の順序と待ち時間）は `marketdata.application`、通信・解凍・ファイルの読み書き・
実時計は `marketdata.adapters` が持つ。

本モジュールの型はすべて不変で、実時計・乱数・環境変数を読まない（D01 §2.2 規則2）。

**取得計画の識別子 `plan_id`**（D03 §14.10）は `RefillPlan.identity_payload()` の正規化
エンコードの sha256 である。入力の snapshot の識別子、カレンダーの識別と版と正規化内容の
ダイジェスト、提供元の設定の識別と版と正規化内容のダイジェスト、絞り込み、対象足の列、
時間ファイルの列（照合用の時間の印を含む）を入れ、変換コード版は入れない。`plan.json` は
この内容そのものを書くので、`plan.json` から `plan_id` を計算し直せる（D03 §14.11.1 の W5）。

**月の数え方**（D03 §14.5）: 保管場所のパスの月は 1 始まり（`01`〜`12`）。提供元の URL の
月は 0 始まりなので、URL を組み立てるとき（`ProviderSettings.url_for`）にだけ 1 を引く。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Any, ClassVar, Final

from odyssey_fx.common import canonical
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.money import decimal_from_int, decimal_from_str, kernel_context
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.series import PriceBasis, SeriesId

__all__ = [
    "ARCHIVE_DIRECTORY",
    "HOUR",
    "HOUR_MILLISECONDS",
    "PLAN_FORMAT",
    "REFILL_TIMEFRAME_IDS",
    "WORK_DIRECTORY",
    "content_digest_of",
    "ArchiveProvenance",
    "ArchiveRead",
    "AttemptRecord",
    "CalendarRef",
    "CommunicationSettings",
    "DecodedTicks",
    "FailureKind",
    "FinalResult",
    "HourKey",
    "HourOutcome",
    "HttpResult",
    "Invalidation",
    "JournalEntry",
    "PauseEnd",
    "PauseStart",
    "PlanHour",
    "ProviderRef",
    "ProviderSettings",
    "ProviderSymbol",
    "ManifestHour",
    "RETRYABLE_FAILURES",
    "STOPPING_FAILURES",
    "RefillFilter",
    "RefillManifestCore",
    "RefillPlan",
    "RetryMark",
    "TargetBar",
    "Tick",
    "ValidationRecord",
    "journal_entry_from_payload",
    "require_hex_digest",
    "series_from_record",
    "sha256_hex",
]

#: 1 時間（時間ファイルの長さ。D03 §14.5）。
HOUR: Final = timedelta(hours=1)

#: 時間ファイルの中の tick のミリ秒の上限（`0 <= ms < 3600000`。D03 §14.6）。
HOUR_MILLISECONDS: Final = 3_600_000

#: 補充の対象にする時間足の `id`（原則4。4時間足・日足は 1時間足から作る導出系列）。
REFILL_TIMEFRAME_IDS: Final = frozenset({"15m", "1h"})

#: 取得計画の形式の印（`plan.json` の `format`）。
PLAN_FORMAT: Final = "refill_plan_v1"

#: 補充の置き場の下の、tick の保管場所と作業ディレクトリの名前（D03 §14.11）。
ARCHIVE_DIRECTORY: Final = "_ticks"
WORK_DIRECTORY: Final = "_work"

#: 提供元の URL の型に置ける差し込み（D03 §14.3。月は 0 始まりの `month0`）。
_URL_PLACEHOLDERS: Final = ("{symbol}", "{year}", "{month0}", "{day}", "{hour}")

_HEX_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


def require_hex_digest(value: object, label: str) -> str:
    """16進 64 文字（sha256）であることを確かめる。照合は `fullmatch`。"""
    if not isinstance(value, str) or not _HEX_PATTERN.fullmatch(value):
        raise MarketDataValueError(f"{label} must be 64 lowercase hex characters, got {value!r}")
    return value


def sha256_hex(content: bytes) -> str:
    """バイト列の sha256（16進 64 文字）。"""
    return hashlib.sha256(content).hexdigest()


def _require_int(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketDataValueError(f"{label} must be an int, got {value!r}")
    if value < minimum:
        raise MarketDataValueError(f"{label} must be >= {minimum}, got {value}")
    return value


def _require_str(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise MarketDataValueError(f"{label} must be a non-empty str, got {value!r}")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise MarketDataValueError(f"{label} must be a bool, got {value!r}")
    return value


def _require_time(value: object, label: str) -> UtcTime:
    if not isinstance(value, UtcTime):
        raise MarketDataValueError(f"{label} must be a UtcTime")
    return value


def _parse_time(value: object, label: str) -> UtcTime:
    if not isinstance(value, str):
        raise MarketDataValueError(f"{label} must be a UTC literal, got {value!r}")
    try:
        return UtcTime.parse(value)
    except KernelValueError as exc:
        raise MarketDataValueError(f"{label}: {exc}") from exc


def _parse_optional_time(value: object, label: str) -> UtcTime | None:
    return None if value is None else _parse_time(value, label)


def _optional_str(value: object, label: str) -> str | None:
    return None if value is None else _require_str(value, label)


def _optional_int(value: object, label: str, *, minimum: int) -> int | None:
    return None if value is None else _require_int(value, label, minimum=minimum)


def _optional_hex(value: object, label: str) -> str | None:
    return None if value is None else require_hex_digest(value, label)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MarketDataValueError(f"{label} must be a mapping, got {type(value).__name__}")
    return value


def _sequence(value: object, label: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise MarketDataValueError(f"{label} must be a list, got {type(value).__name__}")
    return value


def _require_keys(payload: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    """記録の鍵の集合が宣言どおりであることを確かめる（足りない・余分な鍵を拒否）。"""
    actual = frozenset(payload)
    if actual != expected:
        raise MarketDataValueError(
            f"{label} keys {sorted(actual)} do not match the declared keys {sorted(expected)}"
        )


def series_from_record(text: object, timeframe_version: object, label: str) -> SeriesId:
    """記録の系列の文字列（`USDJPY/1h/bid`）と時間足の版から系列を作る。

    系列の文字列は時間足の版を持たないので、版は別の項目（`timeframe_version`）で受ける。
    正規の形（`str(series)` と同じ文字列）でなければ拒否する。
    """
    value = _require_str(text, f"{label}.series")
    parts = value.split("/")
    if len(parts) != 3:
        raise MarketDataValueError(f"{label}.series must look like USDJPY/1h/bid, got {value!r}")
    try:
        series = SeriesId(
            symbol=Symbol(parts[0]),
            timeframe=TimeframeRef(
                id=parts[1],
                version=_require_int(timeframe_version, f"{label}.timeframe_version", minimum=1),
            ),
            basis=PriceBasis(parts[2]),
        )
    except (KernelValueError, ValueError) as exc:
        raise MarketDataValueError(f"{label}: {exc}") from exc
    if str(series) != value:
        raise MarketDataValueError(f"{label}.series is not in the canonical form: {value!r}")
    return series


# --- 時間ファイル（D03 §14.5）--------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HourKey:
    """時間ファイル 1 本の鍵 `(銘柄, UTC の 1 時間の開始時刻)`（D03 §14.5・§14.13）。"""

    symbol: Symbol
    start: UtcTime

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("HourKey.symbol must be a Symbol")
        _require_time(self.start, "HourKey.start")
        moment = self.start.value
        if moment.minute or moment.second or moment.microsecond:
            raise MarketDataValueError(f"HourKey.start must be on the hour, got {self.start}")

    @property
    def interval(self) -> Interval:
        """この時間ファイルが覆う区間 `[start, start + 1h)`。"""
        return Interval(start=self.start, end=self.start + HOUR)

    @property
    def archive_directory(self) -> str:
        """tick の保管場所の時間のディレクトリ（補充の置き場からの相対。D03 §14.5）。

        `_ticks/<SYMBOL>/<YYYY>/<MM>/<DD>/<HH>h`。**月は 1 始まりの 2 桁**、日と時も 2 桁。
        """
        moment = self.start.value
        return (
            f"{ARCHIVE_DIRECTORY}/{self.symbol}/{moment.year:04d}/{moment.month:02d}"
            f"/{moment.day:02d}/{moment.hour:02d}h"
        )

    def sort_key(self) -> tuple[str, str]:
        """整列鍵 `(銘柄, 時刻)`（D03 §14.10）。"""
        return (str(self.symbol), str(self.start))

    def __str__(self) -> str:
        return f"{self.symbol}@{self.start}"

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {"symbol": str(self.symbol), "start": str(self.start)}

    @classmethod
    def from_payload(cls, payload: object, label: str) -> HourKey:
        """記録から読む。"""
        mapping = _mapping(payload, label)
        _require_keys(mapping, frozenset({"symbol", "start"}), label)
        try:
            symbol = Symbol(_require_str(mapping["symbol"], f"{label}.symbol"))
        except KernelValueError as exc:
            raise MarketDataValueError(f"{label}.symbol: {exc}") from exc
        return cls(symbol=symbol, start=_parse_time(mapping["start"], f"{label}.start"))


class HourOutcome(Enum):
    """時間ファイルの最終結果の区分（D03 §14.5）。

    - `FETCHED`: 取得した。tick が 1 件以上。
    - `FETCHED_EMPTY`: 取得した。tick が 0 件（応答は成功で中身が空）。
    - `NOT_FETCHED`: 取得できなかった（再試行の上限・HTTP 404 など。理由を残す）。HTTP 404 を
      「提供元に tick が無い」とは断定しない（D03 §14.18 の 3）。
    """

    FETCHED = "FETCHED"
    FETCHED_EMPTY = "FETCHED_EMPTY"
    NOT_FETCHED = "NOT_FETCHED"


class FailureKind(Enum):
    """1 回の試行の失敗の種類（D03 §14.9）。

    `TIMEOUT`・`CONNECTION`・`HTTP_429`・`HTTP_5XX` は再試行の対象（D03 §14.9 の表）。
    `HTTP_404` は再試行せず `NOT_FETCHED`（D03 §14.18 の 3）。

    PR #58 の仮置きへの人間の決定（2026-10-01）による扱い:

    - `HTTP_AUTH`（401・403。認証・権限の誤り）と `HTTP_CLIENT`（404・429 以外の 4xx。要求や
      設定の誤り）は、欠落として取得を続けず、計画を止めて原因を表示する（`RefillSourceRefused`）。
      その時間には最終結果を書かない（設定を直してから同じ計画で再開すれば取り直す）。
    - `HTTP_OTHER`（4xx・5xx・200 以外の状態）は再試行せず `NOT_FETCHED`。
    - `INVALID_CONTENT`（成功の応答の中身が bi5 として読めない）は再試行し、上限に達したら
      理由を `INVALID_CONTENT` として `NOT_FETCHED` にする。tick が無いという判断には読み替えない。
    - `HOUR_LOCKED`（保管場所の時間のロックを取れない。通信はしない。D03 §14.11.1 の W1）は
      通信の再試行の回数を使わず、別の理由として取得記録に残し、その時間を未取得のまま残す
      （`NOT_FETCHED` にしない。同じ計画をもう一度 `fetch` すれば取る）。
    """

    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    HTTP_429 = "HTTP_429"
    HTTP_5XX = "HTTP_5XX"
    HTTP_404 = "HTTP_404"
    HTTP_AUTH = "HTTP_AUTH"
    HTTP_CLIENT = "HTTP_CLIENT"
    HTTP_OTHER = "HTTP_OTHER"
    INVALID_CONTENT = "INVALID_CONTENT"
    HOUR_LOCKED = "HOUR_LOCKED"


#: 再試行の対象にする失敗の種類（D03 §14.9。`INVALID_CONTENT` は人間の条件付き承認 2026-10-01）。
RETRYABLE_FAILURES: Final = frozenset(
    {
        FailureKind.TIMEOUT,
        FailureKind.CONNECTION,
        FailureKind.HTTP_429,
        FailureKind.HTTP_5XX,
        FailureKind.INVALID_CONTENT,
    }
)

#: 計画を止めて原因を表示する失敗の種類（認証・権限・設定の誤り。人間の修正指示 2026-10-01）。
STOPPING_FAILURES: Final = frozenset({FailureKind.HTTP_AUTH, FailureKind.HTTP_CLIENT})


# --- tick（D03 §14.3・§14.6）--------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tick:
    """tick 1 件（D03 §14.3 の `>iiiff` のうち、価格の整数値と時刻）。

    `offset_ms` は時間ファイルの始まりからのミリ秒。範囲（`0 <= ms < 3600000`）は構築時には
    拒否せず、検証 1 が数えて不合格にする（D03 §14.6・§14.7 の 1）。量（単精度の浮動小数）は
    使わないので持たない（出来高は「出来高不明」。D03 §14.6）。中身は保管場所のバイト列と
    `tick_digest` が残す。
    """

    offset_ms: int
    ask_raw: int
    bid_raw: int

    def __post_init__(self) -> None:
        for name in ("offset_ms", "ask_raw", "bid_raw"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise MarketDataValueError(f"Tick.{name} must be an int, got {value!r}")

    @property
    def in_hour(self) -> bool:
        """時刻が時間ファイルの範囲に入るか（D03 §14.6）。"""
        return 0 <= self.offset_ms < HOUR_MILLISECONDS


@dataclass(frozen=True, slots=True)
class DecodedTicks:
    """解凍して読み取った tick の列と、その内容のダイジェスト（D03 §14.5）。

    `tick_digest` は**解凍後のバイト列**の sha256。圧縮のやり方が変わっても tick が同じなら
    同じ値になる（D03 §14.10）。`ticks` は解凍後のファイル内の順。
    """

    tick_digest: str
    ticks: tuple[Tick, ...]

    def __post_init__(self) -> None:
        require_hex_digest(self.tick_digest, "DecodedTicks.tick_digest")
        if not isinstance(self.ticks, tuple) or not all(
            isinstance(tick, Tick) for tick in self.ticks
        ):
            raise MarketDataValueError("DecodedTicks.ticks must be a tuple of Tick")

    @property
    def outcome(self) -> HourOutcome:
        """tick の件数から決まる区分（`FETCHED` / `FETCHED_EMPTY`）。"""
        return HourOutcome.FETCHED if self.ticks else HourOutcome.FETCHED_EMPTY


@dataclass(frozen=True, slots=True)
class HttpResult:
    """提供元への要求 1 回の結果（`TickArchiveSource.request` の戻り値。D03 §14.9）。

    応答があれば `status` に HTTP の状態、`body` に中身を持つ。応答が無い（タイムアウト・
    接続不能）なら `status` は `None` で `failure` に種類を持つ。`fetched_at` は応答を受けた
    （または失敗した）UTC の時刻で、adapters が実時計から読む（D01 §4 v2.9）。
    """

    url: str
    status: int | None
    body: bytes
    failure: FailureKind | None
    detail: str
    fetched_at: UtcTime

    def __post_init__(self) -> None:
        _require_str(self.url, "HttpResult.url")
        if self.status is not None:
            _require_int(self.status, "HttpResult.status", minimum=100)
        if not isinstance(self.body, bytes):
            raise MarketDataValueError("HttpResult.body must be bytes")
        if self.failure is not None and not isinstance(self.failure, FailureKind):
            raise MarketDataValueError("HttpResult.failure must be a FailureKind or None")
        if (self.status is None) == (self.failure is None):
            raise MarketDataValueError(
                "HttpResult carries either an HTTP status (a response) or a failure kind"
                " (no response), not both and not neither"
            )
        _require_str(self.detail, "HttpResult.detail", allow_empty=True)
        _require_time(self.fetched_at, "HttpResult.fetched_at")


# --- 提供元の設定（D03 §14.5・§14.9）---------------------------------------------


@dataclass(frozen=True, slots=True)
class ProviderSymbol:
    """銘柄ごとの価格の桁と pip の大きさ（D03 §14.5）。

    `price_scale` は 10 のべき（JPY を含む銘柄は 1000、他は 100000）。価格は整数値を
    この桁で割って作る（浮動小数を経由しない。ADR-0012）。`pip_size` は前後の足との差を
    pip で表すためだけに使う。
    """

    symbol: Symbol
    price_scale: int
    pip_size: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, Symbol):
            raise MarketDataValueError("ProviderSymbol.symbol must be a Symbol")
        _require_int(self.price_scale, "ProviderSymbol.price_scale", minimum=1)
        if self.price_exponent is None:
            raise MarketDataValueError(
                f"ProviderSymbol.price_scale must be a power of 10, got {self.price_scale}"
            )
        if not isinstance(self.pip_size, Decimal) or not self.pip_size.is_finite():
            raise MarketDataValueError("ProviderSymbol.pip_size must be a finite Decimal")
        if self.pip_size <= 0:
            raise MarketDataValueError(f"ProviderSymbol.pip_size must be > 0, got {self.pip_size}")

    @property
    def price_exponent(self) -> int | None:
        """`price_scale = 10 ** k` の `k`（10 のべきでなければ `None`）。"""
        text = str(self.price_scale)
        if text[0] == "1" and set(text[1:]) <= {"0"}:
            return len(text) - 1
        return None

    def price_of(self, raw: int) -> Decimal:
        """整数値から価格を作る（`raw / price_scale`。浮動小数を経由しない。D03 §14.6）。"""
        exponent = self.price_exponent
        if exponent is None:  # pragma: no cover - 構築時に拒否している
            raise MarketDataValueError("price_scale must be a power of 10")
        return decimal_from_int(raw).scaleb(-exponent, context=kernel_context())

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "pip_size": canonical.encode_decimal(self.pip_size),
            "price_scale": self.price_scale,
            "symbol": str(self.symbol),
        }


@dataclass(frozen=True, slots=True)
class CommunicationSettings:
    """通信の間隔と再試行の値（D03 §14.9。代表例の試行に限る初期値。D03 §14.18 の 8）。

    値は提供元の設定に書き、コードに埋め込まない（D03 §14.9）。単位はすべて秒。

    - `request_interval_seconds`: 前の要求の終わりから次の要求の始まりまで。
    - `request_timeout_seconds`: 1 回の要求の待ち時間の上限。
    - `max_retries`: 同じ時間ファイルの再試行の上限（最初の試行を含まない回数）。
    - `backoff_initial_seconds` / `backoff_max_seconds`: 再試行の待ち（倍にしていく）。
    - `pause_after_consecutive_failures` / `pause_seconds`: 連続した失敗（別の時間ファイルを
      またいで数える）がこの回数に達したら、この秒数だけ一時停止する。
    """

    request_interval_seconds: int
    request_timeout_seconds: int
    max_retries: int
    backoff_initial_seconds: int
    backoff_max_seconds: int
    pause_after_consecutive_failures: int
    pause_seconds: int

    def __post_init__(self) -> None:
        _require_int(
            self.request_interval_seconds,
            "CommunicationSettings.request_interval_seconds",
            minimum=0,
        )
        _require_int(
            self.request_timeout_seconds, "CommunicationSettings.request_timeout_seconds", minimum=1
        )
        _require_int(self.max_retries, "CommunicationSettings.max_retries", minimum=0)
        _require_int(
            self.backoff_initial_seconds, "CommunicationSettings.backoff_initial_seconds", minimum=0
        )
        _require_int(
            self.backoff_max_seconds, "CommunicationSettings.backoff_max_seconds", minimum=0
        )
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise MarketDataValueError(
                "CommunicationSettings.backoff_max_seconds must be >= backoff_initial_seconds"
            )
        _require_int(
            self.pause_after_consecutive_failures,
            "CommunicationSettings.pause_after_consecutive_failures",
            minimum=1,
        )
        _require_int(self.pause_seconds, "CommunicationSettings.pause_seconds", minimum=0)

    def backoff_seconds(self, retry_index: int) -> int:
        """`retry_index` 回目（1 始まり）の再試行の前の待ち（D03 §14.9。倍にして上限で頭打ち）。"""
        _require_int(retry_index, "retry_index", minimum=1)
        wait = self.backoff_initial_seconds
        for _ in range(retry_index - 1):
            wait = min(wait * 2, self.backoff_max_seconds)
            if wait == self.backoff_max_seconds:
                break
        return min(wait, self.backoff_max_seconds)

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "backoff_initial_seconds": self.backoff_initial_seconds,
            "backoff_max_seconds": self.backoff_max_seconds,
            "max_retries": self.max_retries,
            "pause_after_consecutive_failures": self.pause_after_consecutive_failures,
            "pause_seconds": self.pause_seconds,
            "request_interval_seconds": self.request_interval_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """提供元の設定（`configs/datasources/dukascopy_tick_v1.yaml`。D03 §14.5）。

    `url_template` は `{symbol}`・`{year}`・`{month0}`・`{day}`・`{hour}` をちょうど 1 度ずつ
    含む。`month0` は **0 始まりの月**（D03 §14.3）で、URL を組み立てるときにだけ 1 を引く。
    """

    id: str
    version: int
    url_template: str
    symbols: tuple[ProviderSymbol, ...]
    communication: CommunicationSettings

    def __post_init__(self) -> None:
        _require_str(self.id, "ProviderSettings.id")
        _require_int(self.version, "ProviderSettings.version", minimum=1)
        _require_str(self.url_template, "ProviderSettings.url_template")
        for placeholder in _URL_PLACEHOLDERS:
            if self.url_template.count(placeholder) != 1:
                raise MarketDataValueError(
                    f"ProviderSettings.url_template must contain {placeholder} exactly once,"
                    f" got {self.url_template!r}"
                )
        stripped = self.url_template
        for placeholder in _URL_PLACEHOLDERS:
            stripped = stripped.replace(placeholder, "")
        if "{" in stripped or "}" in stripped:
            raise MarketDataValueError(
                f"ProviderSettings.url_template may only use {list(_URL_PLACEHOLDERS)},"
                f" got {self.url_template!r}"
            )
        if not isinstance(self.symbols, tuple) or not self.symbols:
            raise MarketDataValueError("ProviderSettings.symbols must be a non-empty tuple")
        for entry in self.symbols:
            if not isinstance(entry, ProviderSymbol):
                raise MarketDataValueError("ProviderSettings.symbols must contain ProviderSymbol")
        names = [str(entry.symbol) for entry in self.symbols]
        if len(set(names)) != len(names):
            raise MarketDataValueError(f"ProviderSettings.symbols lists a symbol twice: {names}")
        ordered = tuple(sorted(self.symbols, key=lambda entry: str(entry.symbol)))
        if ordered != self.symbols:
            object.__setattr__(self, "symbols", ordered)
        if not isinstance(self.communication, CommunicationSettings):
            raise MarketDataValueError(
                "ProviderSettings.communication must be CommunicationSettings"
            )

    def symbol(self, symbol: Symbol) -> ProviderSymbol:
        """銘柄の価格の桁と pip。設定に無い銘柄は拒否する。"""
        for entry in self.symbols:
            if entry.symbol == symbol:
                return entry
        raise MarketDataValueError(
            f"provider {self.id} v{self.version} has no settings for {symbol}"
            " (price scale and pip size are declared per symbol, D03 §14.5)"
        )

    def url_for(self, hour: HourKey) -> str:
        """時間ファイルの URL（D03 §14.3）。**月だけ 0 始まり**（ここでだけ 1 を引く）。"""
        moment = hour.start.value
        return (
            self.url_template.replace("{symbol}", str(hour.symbol))
            .replace("{year}", f"{moment.year:04d}")
            .replace("{month0}", f"{moment.month - 1:02d}")
            .replace("{day}", f"{moment.day:02d}")
            .replace("{hour}", f"{moment.hour:02d}")
        )

    def source_payload(self) -> Mapping[str, Any]:
        """取得した中身を決める設定の部分（提供元の識別と URL の型。D03 §14.5）。

        通信の値と価格の桁・pip の大きさは入れない。通信の値は取得した中身を変えず、価格の
        桁・pip は保管した後の集約と比較にだけ使うので、試行の後に通信の値を見直しても試行で
        取った時間ファイルを読み直せるようにするため（D03 §14.5）。
        """
        return {"id": self.id, "url_template": self.url_template}

    def source_digest(self) -> str:
        """保管場所の `<source_digest>`（`source_payload` の正規化内容の sha256）。"""
        return canonical.digest(self.source_payload()).hex

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形（`plan.json` の `provider.settings`）。"""
        return {
            "communication": self.communication.payload(),
            "id": self.id,
            "symbols": [entry.payload() for entry in self.symbols],
            "url_template": self.url_template,
            "version": self.version,
        }

    @classmethod
    def from_payload(cls, payload: object, label: str) -> ProviderSettings:
        """記録から読む（`plan.json` を読み戻すとき）。"""
        mapping = _mapping(payload, label)
        _require_keys(
            mapping,
            frozenset({"communication", "id", "symbols", "url_template", "version"}),
            label,
        )
        comm = _mapping(mapping["communication"], f"{label}.communication")
        _require_keys(
            comm,
            frozenset(
                {
                    "backoff_initial_seconds",
                    "backoff_max_seconds",
                    "max_retries",
                    "pause_after_consecutive_failures",
                    "pause_seconds",
                    "request_interval_seconds",
                    "request_timeout_seconds",
                }
            ),
            f"{label}.communication",
        )
        symbols: list[ProviderSymbol] = []
        for index, raw in enumerate(_sequence(mapping["symbols"], f"{label}.symbols")):
            entry = _mapping(raw, f"{label}.symbols[{index}]")
            _require_keys(
                entry, frozenset({"pip_size", "price_scale", "symbol"}), f"{label}.symbols"
            )
            try:
                symbols.append(
                    ProviderSymbol(
                        symbol=Symbol(_require_str(entry["symbol"], f"{label}.symbol")),
                        price_scale=_require_int(
                            entry["price_scale"], f"{label}.price_scale", minimum=1
                        ),
                        pip_size=_decimal_from_canonical(entry["pip_size"], f"{label}.pip_size"),
                    )
                )
            except KernelValueError as exc:
                raise MarketDataValueError(f"{label}.symbols[{index}]: {exc}") from exc
        return cls(
            id=_require_str(mapping["id"], f"{label}.id"),
            version=_require_int(mapping["version"], f"{label}.version", minimum=1),
            url_template=_require_str(mapping["url_template"], f"{label}.url_template"),
            symbols=tuple(symbols),
            communication=CommunicationSettings(
                request_interval_seconds=comm["request_interval_seconds"],
                request_timeout_seconds=comm["request_timeout_seconds"],
                max_retries=comm["max_retries"],
                backoff_initial_seconds=comm["backoff_initial_seconds"],
                backoff_max_seconds=comm["backoff_max_seconds"],
                pause_after_consecutive_failures=comm["pause_after_consecutive_failures"],
                pause_seconds=comm["pause_seconds"],
            ),
        )


_CANONICAL_DECIMAL: Final = re.compile(r"(-?)(\d+)e(-?\d+)")


def _decimal_from_canonical(value: object, label: str) -> Decimal:
    """`canonical.encode_decimal` の表現（`"<sign><digits>e<exponent>"`）を読み戻す。"""
    if not isinstance(value, str):
        raise MarketDataValueError(f"{label} must be a canonical decimal string, got {value!r}")
    match = _CANONICAL_DECIMAL.fullmatch(value)
    if match is None:
        raise MarketDataValueError(f"{label} must be a canonical decimal string, got {value!r}")
    result = decimal_from_str(f"{match.group(1)}{match.group(2)}E{match.group(3)}")
    if canonical.encode_decimal(result) != value:
        raise MarketDataValueError(f"{label} is not in the canonical form: {value!r}")
    return result


def _freeze(value: Any, label: str) -> Any:
    """設定の正規化内容を不変の形にする（mapping は読み取り専用、列は tuple）。"""
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise MarketDataValueError(f"{label} keys must be str, got {key!r}")
            frozen[key] = _freeze(value[key], f"{label}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, label) for item in value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise MarketDataValueError(
        f"{label} holds a {type(value).__name__}; the normalized content of a configuration"
        " file may only hold strings, integers, booleans, null, lists and mappings"
    )


def _thaw(value: Any) -> Any:
    """`_freeze` の逆（記録に書く JSON 互換の形）。"""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ProviderRef:
    """計画が使う提供元の設定（D03 §14.10）。

    識別と版と、設定ファイルの正規化内容のダイジェスト（`content_digest`。価格の桁・URL の型・
    通信の値を含む。同じ版のまま中身を変えても別の計画になる）。取得（`fetch`）は計画だけを
    入力にするので、設定の中身（`settings`）も計画に持たせる。
    """

    content_digest: str
    settings: ProviderSettings

    def __post_init__(self) -> None:
        require_hex_digest(self.content_digest, "ProviderRef.content_digest")
        if not isinstance(self.settings, ProviderSettings):
            raise MarketDataValueError("ProviderRef.settings must be ProviderSettings")

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "content_digest": self.content_digest,
            "id": self.settings.id,
            "settings": self.settings.payload(),
            "version": self.settings.version,
        }


@dataclass(frozen=True, slots=True)
class CalendarRef:
    """計画が使うカレンダー（D03 §14.10）。

    識別と版と、設定ファイルの正規化内容のダイジェスト。書き出し（`finalize`）が計画だけから
    同じカレンダーを組み立て直せるよう、正規化内容（`content`。読み取り専用）も持つ。
    """

    id: str
    version: int
    content_digest: str
    content: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_str(self.id, "CalendarRef.id")
        _require_int(self.version, "CalendarRef.version", minimum=1)
        require_hex_digest(self.content_digest, "CalendarRef.content_digest")
        frozen = _freeze(_mapping(self.content, "CalendarRef.content"), "CalendarRef.content")
        object.__setattr__(self, "content", frozen)
        if canonical.digest(_thaw(frozen)).hex != self.content_digest:
            raise MarketDataValueError(
                "CalendarRef.content_digest does not match the digest of its content"
            )

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "content": _thaw(self.content),
            "content_digest": self.content_digest,
            "id": self.id,
            "version": self.version,
        }


def content_digest_of(content: Mapping[str, Any]) -> str:
    """設定ファイルの正規化内容のダイジェスト（16進 64 文字。D03 §14.10）。"""
    return canonical.digest(_thaw(_freeze(content, "content"))).hex


# --- 取得計画（D03 §14.4・§14.10）----------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefillFilter:
    """絞り込み（任意。D03 §14.4 の 5）。`None` は「絞らない」。

    `symbols` は整列済みの銘柄の列、`interval` は足の区間がこの区間に**完全に含まれる**
    対象足だけを残す（仮置き）。
    """

    symbols: tuple[Symbol, ...] | None = None
    interval: Interval | None = None

    def __post_init__(self) -> None:
        if self.symbols is not None:
            if not isinstance(self.symbols, tuple) or not self.symbols:
                raise MarketDataValueError("RefillFilter.symbols must be a non-empty tuple")
            if not all(isinstance(symbol, Symbol) for symbol in self.symbols):
                raise MarketDataValueError("RefillFilter.symbols must contain Symbol")
            if len(set(self.symbols)) != len(self.symbols):
                raise MarketDataValueError("RefillFilter.symbols lists a symbol twice")
            ordered = tuple(sorted(self.symbols, key=str))
            if ordered != self.symbols:
                object.__setattr__(self, "symbols", ordered)
        if self.interval is not None and not isinstance(self.interval, Interval):
            raise MarketDataValueError("RefillFilter.interval must be an Interval or None")

    def admits(self, series: SeriesId, interval: Interval) -> bool:
        """対象足がこの絞り込みに入るか。"""
        if self.symbols is not None and series.symbol not in self.symbols:
            return False
        if self.interval is not None and not (
            self.interval.start <= interval.start and interval.end <= self.interval.end
        ):
            return False
        return True

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "interval": None
            if self.interval is None
            else {"end": str(self.interval.end), "start": str(self.interval.start)},
            "symbols": None if self.symbols is None else [str(symbol) for symbol in self.symbols],
        }


@dataclass(frozen=True, slots=True)
class TargetBar:
    """対象足 1 本（D03 §14.4）。区間は整列上の区間（期待区間と一致することを確かめた後）。"""

    series: SeriesId
    interval: Interval

    def __post_init__(self) -> None:
        if not isinstance(self.series, SeriesId):
            raise MarketDataValueError("TargetBar.series must be a SeriesId")
        if not isinstance(self.interval, Interval):
            raise MarketDataValueError("TargetBar.interval must be an Interval")
        if self.series.timeframe.id not in REFILL_TIMEFRAME_IDS:
            raise MarketDataValueError(
                f"TargetBar.series must be a 15m or 1h series (D03 §14.2 の 4), got {self.series}"
            )

    @property
    def start(self) -> UtcTime:
        """足の開始時刻（足のラベル）。"""
        return self.interval.start

    @property
    def hour(self) -> HourKey:
        """この足を作る時間ファイル（足の開始時刻を含む UTC の 1 時間）。"""
        moment = self.interval.start.value.replace(minute=0, second=0, microsecond=0)
        return HourKey(symbol=self.series.symbol, start=UtcTime(moment))

    def sort_key(self) -> tuple[str, str]:
        """整列鍵 `(系列の文字列, 開始時刻)`（D03 §14.10）。"""
        return (str(self.series), str(self.interval.start))

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。系列の文字列は時間足の版を持たないので、版を別に書く。"""
        return {
            "end": str(self.interval.end),
            "series": str(self.series),
            "start": str(self.interval.start),
            "timeframe_version": self.series.timeframe.version,
        }

    @classmethod
    def from_payload(cls, payload: object, label: str) -> TargetBar:
        """記録から読む。"""
        mapping = _mapping(payload, label)
        _require_keys(mapping, frozenset({"end", "series", "start", "timeframe_version"}), label)
        series = series_from_record(mapping["series"], mapping["timeframe_version"], label)
        try:
            interval = Interval(
                start=_parse_time(mapping["start"], f"{label}.start"),
                end=_parse_time(mapping["end"], f"{label}.end"),
            )
        except KernelValueError as exc:
            raise MarketDataValueError(f"{label}: {exc}") from exc
        return cls(series=series, interval=interval)


@dataclass(frozen=True, slots=True)
class PlanHour:
    """計画の時間ファイル 1 本（D03 §14.4）。`reference` は照合用の時間の印。"""

    hour: HourKey
    reference: bool

    def __post_init__(self) -> None:
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("PlanHour.hour must be an HourKey")
        _require_bool(self.reference, "PlanHour.reference")

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "reference": self.reference,
            "start": str(self.hour.start),
            "symbol": str(self.hour.symbol),
        }


@dataclass(frozen=True, slots=True)
class RefillPlan:
    """取得計画（D03 §14.4・§14.10）。

    対象足は `(系列の文字列, 開始時刻)`、時間ファイルは `(銘柄, 時刻)` で整列する。対象足が
    1 本も無い計画は作らない（作る側が `RefillPlanEmpty` で止める）。対象足の時間はすべて
    時間ファイルの列に入り、照合用の時間の印の付いた時間には対象足が無い。
    """

    snapshot_id: str
    calendar: CalendarRef
    provider: ProviderRef
    filter: RefillFilter
    target_bars: tuple[TargetBar, ...]
    hours: tuple[PlanHour, ...]

    def __post_init__(self) -> None:
        require_hex_digest(self.snapshot_id, "RefillPlan.snapshot_id")
        if not isinstance(self.calendar, CalendarRef):
            raise MarketDataValueError("RefillPlan.calendar must be a CalendarRef")
        if not isinstance(self.provider, ProviderRef):
            raise MarketDataValueError("RefillPlan.provider must be a ProviderRef")
        if not isinstance(self.filter, RefillFilter):
            raise MarketDataValueError("RefillPlan.filter must be a RefillFilter")
        if not isinstance(self.target_bars, tuple) or not self.target_bars:
            raise MarketDataValueError("RefillPlan.target_bars must be a non-empty tuple")
        if not all(isinstance(bar, TargetBar) for bar in self.target_bars):
            raise MarketDataValueError("RefillPlan.target_bars must contain TargetBar")
        if not isinstance(self.hours, tuple) or not all(
            isinstance(hour, PlanHour) for hour in self.hours
        ):
            raise MarketDataValueError("RefillPlan.hours must be a tuple of PlanHour")
        bar_keys = [bar.sort_key() for bar in self.target_bars]
        if bar_keys != sorted(bar_keys) or len(set(bar_keys)) != len(bar_keys):
            raise MarketDataValueError(
                "RefillPlan.target_bars must be sorted by (series, start) without duplicates"
            )
        hour_keys = [hour.hour.sort_key() for hour in self.hours]
        if hour_keys != sorted(hour_keys) or len(set(hour_keys)) != len(hour_keys):
            raise MarketDataValueError(
                "RefillPlan.hours must be sorted by (symbol, start) without duplicates"
            )
        target_hours = {bar.hour for bar in self.target_bars}
        for hour in self.hours:
            if hour.reference == (hour.hour in target_hours):
                raise MarketDataValueError(
                    f"RefillPlan.hours: {hour.hour} must be a reference hour exactly when it"
                    " holds no target bar (D03 §14.4)"
                )
        listed = {hour.hour for hour in self.hours}
        missing = sorted(str(hour) for hour in target_hours - listed)
        if missing:
            raise MarketDataValueError(
                f"RefillPlan.hours lacks the hours of target bars: {missing}"
            )
        for bar in self.target_bars:
            self.provider.settings.symbol(bar.series.symbol)

    @property
    def hour_keys(self) -> tuple[HourKey, ...]:
        """時間ファイルの鍵の列（計画の順）。"""
        return tuple(hour.hour for hour in self.hours)

    def identity_payload(self) -> Mapping[str, Any]:
        """`plan_id` のダイジェスト対象で、`plan.json` に書く内容そのもの（D03 §14.10）。"""
        return {
            "calendar": self.calendar.payload(),
            "filter": self.filter.payload(),
            "format": PLAN_FORMAT,
            "hours": [hour.payload() for hour in self.hours],
            "provider": self.provider.payload(),
            "snapshot_id": self.snapshot_id,
            "target_bars": [bar.payload() for bar in self.target_bars],
        }

    def plan_id(self) -> str:
        """取得計画の識別子（16進 64 文字。D03 §14.10）。"""
        return canonical.digest(self.identity_payload()).hex

    @classmethod
    def from_payload(cls, payload: object) -> RefillPlan:
        """`plan.json` の内容から読む。形が違えば `MarketDataValueError`。"""
        label = "plan.json"
        mapping = _mapping(payload, label)
        _require_keys(
            mapping,
            frozenset(
                {"calendar", "filter", "format", "hours", "provider", "snapshot_id", "target_bars"}
            ),
            label,
        )
        if mapping["format"] != PLAN_FORMAT:
            raise MarketDataValueError(
                f"{label}.format must be {PLAN_FORMAT!r}, got {mapping['format']!r}"
            )
        calendar = _mapping(mapping["calendar"], f"{label}.calendar")
        _require_keys(
            calendar, frozenset({"content", "content_digest", "id", "version"}), f"{label}.calendar"
        )
        provider = _mapping(mapping["provider"], f"{label}.provider")
        _require_keys(
            provider,
            frozenset({"content_digest", "id", "settings", "version"}),
            f"{label}.provider",
        )
        settings = ProviderSettings.from_payload(provider["settings"], f"{label}.provider.settings")
        if provider["id"] != settings.id or provider["version"] != settings.version:
            raise MarketDataValueError(
                f"{label}.provider id/version do not match provider.settings"
            )
        filter_payload = _mapping(mapping["filter"], f"{label}.filter")
        _require_keys(filter_payload, frozenset({"interval", "symbols"}), f"{label}.filter")
        symbols_raw = filter_payload["symbols"]
        interval_raw = filter_payload["interval"]
        try:
            symbols = (
                None
                if symbols_raw is None
                else tuple(
                    Symbol(_require_str(item, f"{label}.filter.symbols"))
                    for item in _sequence(symbols_raw, f"{label}.filter.symbols")
                )
            )
            interval: Interval | None = None
            if interval_raw is not None:
                interval_map = _mapping(interval_raw, f"{label}.filter.interval")
                _require_keys(interval_map, frozenset({"end", "start"}), f"{label}.filter.interval")
                interval = Interval(
                    start=_parse_time(interval_map["start"], f"{label}.filter.interval.start"),
                    end=_parse_time(interval_map["end"], f"{label}.filter.interval.end"),
                )
        except KernelValueError as exc:
            raise MarketDataValueError(f"{label}.filter: {exc}") from exc
        hours: list[PlanHour] = []
        for index, raw in enumerate(_sequence(mapping["hours"], f"{label}.hours")):
            entry = _mapping(raw, f"{label}.hours[{index}]")
            _require_keys(entry, frozenset({"reference", "start", "symbol"}), f"{label}.hours")
            hours.append(
                PlanHour(
                    hour=HourKey.from_payload(
                        {"symbol": entry["symbol"], "start": entry["start"]},
                        f"{label}.hours[{index}]",
                    ),
                    reference=_require_bool(entry["reference"], f"{label}.hours.reference"),
                )
            )
        return cls(
            snapshot_id=require_hex_digest(mapping["snapshot_id"], f"{label}.snapshot_id"),
            calendar=CalendarRef(
                id=_require_str(calendar["id"], f"{label}.calendar.id"),
                version=_require_int(calendar["version"], f"{label}.calendar.version", minimum=1),
                content_digest=require_hex_digest(
                    calendar["content_digest"], f"{label}.calendar.content_digest"
                ),
                content=_mapping(calendar["content"], f"{label}.calendar.content"),
            ),
            provider=ProviderRef(
                content_digest=require_hex_digest(
                    provider["content_digest"], f"{label}.provider.content_digest"
                ),
                settings=settings,
            ),
            filter=RefillFilter(symbols=symbols, interval=interval),
            target_bars=tuple(
                TargetBar.from_payload(raw, f"{label}.target_bars[{index}]")
                for index, raw in enumerate(
                    _sequence(mapping["target_bars"], f"{label}.target_bars")
                )
            ),
            hours=tuple(hours),
        )


# --- 保管場所の出所の記録（D03 §14.5・§14.7 の 5）-----------------------------------


@dataclass(frozen=True, slots=True)
class ArchiveProvenance:
    """保管場所の 1 件に書く、その取得の出所（D03 §14.5）。

    出所は取得したときに 1 回だけ記録し、写さない。`response_sha256` は応答の中身（bi5 の
    バイト列）の sha256、`tick_digest` は解凍後の tick の内容の sha256。
    """

    url: str
    fetched_at: UtcTime
    http_status: int
    attempts: int
    response_sha256: str
    tick_digest: str
    tick_count: int
    provider_id: str
    provider_version: int
    provider_content_digest: str
    source_digest: str

    def __post_init__(self) -> None:
        _require_str(self.url, "ArchiveProvenance.url")
        _require_time(self.fetched_at, "ArchiveProvenance.fetched_at")
        _require_int(self.http_status, "ArchiveProvenance.http_status", minimum=100)
        _require_int(self.attempts, "ArchiveProvenance.attempts", minimum=1)
        require_hex_digest(self.response_sha256, "ArchiveProvenance.response_sha256")
        require_hex_digest(self.tick_digest, "ArchiveProvenance.tick_digest")
        _require_int(self.tick_count, "ArchiveProvenance.tick_count", minimum=0)
        _require_str(self.provider_id, "ArchiveProvenance.provider_id")
        _require_int(self.provider_version, "ArchiveProvenance.provider_version", minimum=1)
        require_hex_digest(
            self.provider_content_digest, "ArchiveProvenance.provider_content_digest"
        )
        require_hex_digest(self.source_digest, "ArchiveProvenance.source_digest")

    @property
    def outcome(self) -> HourOutcome:
        """tick の件数から決まる区分。"""
        return HourOutcome.FETCHED if self.tick_count else HourOutcome.FETCHED_EMPTY

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "attempts": self.attempts,
            "fetched_at": str(self.fetched_at),
            "http_status": self.http_status,
            "provider_content_digest": self.provider_content_digest,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "response_sha256": self.response_sha256,
            "source_digest": self.source_digest,
            "tick_count": self.tick_count,
            "tick_digest": self.tick_digest,
            "url": self.url,
        }

    @classmethod
    def from_payload(cls, payload: object, label: str) -> ArchiveProvenance:
        """記録から読む。"""
        mapping = _mapping(payload, label)
        _require_keys(
            mapping,
            frozenset(
                {
                    "attempts",
                    "fetched_at",
                    "http_status",
                    "provider_content_digest",
                    "provider_id",
                    "provider_version",
                    "response_sha256",
                    "source_digest",
                    "tick_count",
                    "tick_digest",
                    "url",
                }
            ),
            label,
        )
        return cls(
            url=_require_str(mapping["url"], f"{label}.url"),
            fetched_at=_parse_time(mapping["fetched_at"], f"{label}.fetched_at"),
            http_status=_require_int(mapping["http_status"], f"{label}.http_status", minimum=100),
            attempts=_require_int(mapping["attempts"], f"{label}.attempts", minimum=1),
            response_sha256=require_hex_digest(
                mapping["response_sha256"], f"{label}.response_sha256"
            ),
            tick_digest=require_hex_digest(mapping["tick_digest"], f"{label}.tick_digest"),
            tick_count=_require_int(mapping["tick_count"], f"{label}.tick_count", minimum=0),
            provider_id=_require_str(mapping["provider_id"], f"{label}.provider_id"),
            provider_version=_require_int(
                mapping["provider_version"], f"{label}.provider_version", minimum=1
            ),
            provider_content_digest=require_hex_digest(
                mapping["provider_content_digest"], f"{label}.provider_content_digest"
            ),
            source_digest=require_hex_digest(mapping["source_digest"], f"{label}.source_digest"),
        )


@dataclass(frozen=True, slots=True)
class ArchiveRead:
    """保管場所の 1 件を読んだ結果（`RefillStore.read_archive` の戻り値。D03 §14.11.1 の W5）。

    adapters はファイルを読み、応答の中身の sha256 を計算し、解凍して tick を読み取るところ
    まで行う。記録との突き合わせ（W5 の検算）は application が行う。`path` は補充の置き場
    からの相対パス、`directory_source_digest` と `file_tick_digest` はパスから読んだ名前。
    解凍できなければ `decoded` は `None` で、`decode_error` に理由を持つ。
    """

    path: str
    hour: HourKey
    directory_source_digest: str
    file_tick_digest: str
    provenance: ArchiveProvenance
    body_sha256: str
    decoded: DecodedTicks | None
    decode_error: str

    def __post_init__(self) -> None:
        _require_str(self.path, "ArchiveRead.path")
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("ArchiveRead.hour must be an HourKey")
        _require_str(self.directory_source_digest, "ArchiveRead.directory_source_digest")
        _require_str(self.file_tick_digest, "ArchiveRead.file_tick_digest")
        if not isinstance(self.provenance, ArchiveProvenance):
            raise MarketDataValueError("ArchiveRead.provenance must be an ArchiveProvenance")
        require_hex_digest(self.body_sha256, "ArchiveRead.body_sha256")
        if self.decoded is not None and not isinstance(self.decoded, DecodedTicks):
            raise MarketDataValueError("ArchiveRead.decoded must be DecodedTicks or None")
        _require_str(self.decode_error, "ArchiveRead.decode_error", allow_empty=True)


# --- 取得記録 `journal.jsonl` の行（D03 §14.9・§14.11.1 の W3・§14.12）-----------------


@dataclass(frozen=True, slots=True)
class FinalResult:
    """時間ファイルの最終結果の行（D03 §14.9・§14.13）。

    通信して得た結果（`from_archive=False`）は URL・取得時刻・HTTP の状態・応答の中身の
    sha256・試行の回数を持つ。保管場所から読んだ結果（`from_archive=True`）は「保管場所から
    読んだ」印と読んだファイル（`archive_file`）だけを持ち、出所はそのファイルが記録する
    （写さない。D03 §14.5）。`NOT_FETCHED` は保管しないので `archive_file` を持たず、理由
    （`failure`・`detail`）と試行の回数を持つ。
    """

    hour: HourKey
    outcome: HourOutcome
    from_archive: bool
    tick_digest: str | None
    tick_count: int | None
    archive_file: str | None
    url: str | None
    fetched_at: UtcTime | None
    http_status: int | None
    response_sha256: str | None
    attempts: int
    failure: FailureKind | None
    detail: str
    at: UtcTime

    KIND: ClassVar[str] = "final"

    def __post_init__(self) -> None:
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("FinalResult.hour must be an HourKey")
        if not isinstance(self.outcome, HourOutcome):
            raise MarketDataValueError("FinalResult.outcome must be an HourOutcome")
        _require_bool(self.from_archive, "FinalResult.from_archive")
        _require_int(self.attempts, "FinalResult.attempts", minimum=0)
        _require_str(self.detail, "FinalResult.detail", allow_empty=True)
        _require_time(self.at, "FinalResult.at")
        if self.failure is not None and not isinstance(self.failure, FailureKind):
            raise MarketDataValueError("FinalResult.failure must be a FailureKind or None")
        if self.outcome is HourOutcome.NOT_FETCHED:
            if self.from_archive or self.failure is None:
                raise MarketDataValueError(
                    "a NOT_FETCHED result is never read from the archive and carries a failure"
                )
            if any(
                value is not None
                for value in (self.tick_digest, self.tick_count, self.archive_file)
            ):
                raise MarketDataValueError(
                    "a NOT_FETCHED result is not archived and has no tick digest (D03 §14.5)"
                )
            return
        require_hex_digest(self.tick_digest, "FinalResult.tick_digest")
        count = _require_int(self.tick_count, "FinalResult.tick_count", minimum=0)
        if (count > 0) != (self.outcome is HourOutcome.FETCHED):
            raise MarketDataValueError(
                f"FinalResult.outcome {self.outcome.value} does not match tick_count {count}"
            )
        _require_str(self.archive_file, "FinalResult.archive_file")
        if self.failure is not None:
            raise MarketDataValueError("a fetched result carries no failure")
        if self.from_archive:
            if (
                any(
                    value is not None
                    for value in (self.url, self.fetched_at, self.http_status, self.response_sha256)
                )
                or self.attempts != 0
            ):
                raise MarketDataValueError(
                    "a result read from the archive does not copy the provenance; the archive"
                    " file records it (D03 §14.5)"
                )
        else:
            _require_str(self.url, "FinalResult.url")
            _require_time(self.fetched_at, "FinalResult.fetched_at")
            _require_int(self.http_status, "FinalResult.http_status", minimum=100)
            require_hex_digest(self.response_sha256, "FinalResult.response_sha256")
            _require_int(self.attempts, "FinalResult.attempts", minimum=1)

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "archive_file": self.archive_file,
            "at": str(self.at),
            "attempts": self.attempts,
            "detail": self.detail,
            "failure": None if self.failure is None else self.failure.value,
            "fetched_at": None if self.fetched_at is None else str(self.fetched_at),
            "from_archive": self.from_archive,
            "hour": self.hour.payload(),
            "http_status": self.http_status,
            "kind": self.KIND,
            "outcome": self.outcome.value,
            "response_sha256": self.response_sha256,
            "tick_count": self.tick_count,
            "tick_digest": self.tick_digest,
            "url": self.url,
        }


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """失敗した試行 1 回の行（D03 §14.12 の出来事4。成功の試行は最終結果の行が表す）。

    `HOUR_LOCKED`（時間のロックを取れなかった）の行は通信していないので `attempt` が 0 で、
    通信の再試行の回数に数えない。それ以外の失敗の `attempt` は 1 以上。
    """

    hour: HourKey
    attempt: int
    failure: FailureKind
    http_status: int | None
    detail: str
    at: UtcTime

    KIND: ClassVar[str] = "attempt"

    def __post_init__(self) -> None:
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("AttemptRecord.hour must be an HourKey")
        if not isinstance(self.failure, FailureKind):
            raise MarketDataValueError("AttemptRecord.failure must be a FailureKind")
        if self.failure is FailureKind.HOUR_LOCKED:
            if self.attempt != 0 or self.http_status is not None:
                raise MarketDataValueError(
                    "an HOUR_LOCKED record made no request: attempt 0 and no HTTP status"
                )
        else:
            _require_int(self.attempt, "AttemptRecord.attempt", minimum=1)
        if self.http_status is not None:
            _require_int(self.http_status, "AttemptRecord.http_status", minimum=100)
        _require_str(self.detail, "AttemptRecord.detail", allow_empty=True)
        _require_time(self.at, "AttemptRecord.at")

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "at": str(self.at),
            "attempt": self.attempt,
            "detail": self.detail,
            "failure": self.failure.value,
            "hour": self.hour.payload(),
            "http_status": self.http_status,
            "kind": self.KIND,
        }


@dataclass(frozen=True, slots=True)
class RetryMark:
    """取り直しの印の行（`fetch --retry-failed`。D03 §14.12 の出来事9）。

    この行より前の同じ時間の最終結果を無効にする（D03 §14.12「有効な最終結果」）。
    """

    hour: HourKey
    at: UtcTime

    KIND: ClassVar[str] = "retry_mark"

    def __post_init__(self) -> None:
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("RetryMark.hour must be an HourKey")
        _require_time(self.at, "RetryMark.at")

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {"at": str(self.at), "hour": self.hour.payload(), "kind": self.KIND}


@dataclass(frozen=True, slots=True)
class Invalidation:
    """無効化の行（D03 §14.11.1 の W5。最終結果が指す保管場所のものが無いとき）。"""

    hour: HourKey
    reason: str
    at: UtcTime

    KIND: ClassVar[str] = "invalidate"

    def __post_init__(self) -> None:
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("Invalidation.hour must be an HourKey")
        _require_str(self.reason, "Invalidation.reason")
        _require_time(self.at, "Invalidation.at")

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "at": str(self.at),
            "hour": self.hour.payload(),
            "kind": self.KIND,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PauseStart:
    """一時停止の開始の行（D03 §14.12 の一時停止。プロセスの中の状態の記録）。"""

    consecutive_failures: int
    seconds: int
    at: UtcTime

    KIND: ClassVar[str] = "pause_start"

    def __post_init__(self) -> None:
        _require_int(self.consecutive_failures, "PauseStart.consecutive_failures", minimum=1)
        _require_int(self.seconds, "PauseStart.seconds", minimum=0)
        _require_time(self.at, "PauseStart.at")

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "at": str(self.at),
            "consecutive_failures": self.consecutive_failures,
            "kind": self.KIND,
            "seconds": self.seconds,
        }


@dataclass(frozen=True, slots=True)
class PauseEnd:
    """一時停止の終了の行。"""

    at: UtcTime

    KIND: ClassVar[str] = "pause_end"

    def __post_init__(self) -> None:
        _require_time(self.at, "PauseEnd.at")

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {"at": str(self.at), "kind": self.KIND}


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    """検証の結果の行（D03 §14.7・§14.11.1 の W3。書き出し（`finalize`）が追記する）。

    取得（`fetch`）は書かない。状態の判定（不合格。D03 §14.12）のために読む。`reasons` は
    不合格の理由（合格なら空）。

    `details` は不合格の行に残す構造的な記録（D03 §14.7）: 照合で合わなかった足と差
    （`mismatches`）、未照合の塊ごとの `(系列, 塊の開始時刻, 対象足の数)`（`unreconciled`）、
    作らなかった対象足と理由（`not_built`）。補充分のディレクトリが作られない不合格では、報告
    （D03 §14.15）がこの行から未照合と作らなかった足を読む。値は JSON の形（文字列・整数・
    真偽値・null・列・mapping）だけで、読み取り専用に固める。
    """

    passed: bool
    reasons: tuple[str, ...]
    at: UtcTime
    details: Mapping[str, Any] = MappingProxyType({})

    KIND: ClassVar[str] = "validation"

    def __post_init__(self) -> None:
        _require_bool(self.passed, "ValidationRecord.passed")
        if not isinstance(self.reasons, tuple) or not all(
            isinstance(reason, str) and reason for reason in self.reasons
        ):
            raise MarketDataValueError("ValidationRecord.reasons must be a tuple of str")
        if self.passed == bool(self.reasons):
            raise MarketDataValueError(
                "ValidationRecord carries reasons exactly when the validation failed"
            )
        _require_time(self.at, "ValidationRecord.at")
        object.__setattr__(
            self,
            "details",
            _freeze(_mapping(self.details, "ValidationRecord.details"), "ValidationRecord.details"),
        )

    def payload(self) -> Mapping[str, Any]:
        """記録に書く形。"""
        return {
            "at": str(self.at),
            "details": _thaw(self.details),
            "kind": self.KIND,
            "passed": self.passed,
            "reasons": list(self.reasons),
        }


# --- 補充の manifest のうち PR 1 が読む部分（D03 §14.11・§14.12「書き出し済み」）--------


@dataclass(frozen=True, slots=True)
class ManifestHour:
    """補充の manifest の時間ファイル 1 本の記録（時間・区分・tick の内容のダイジェスト）。

    `NOT_FETCHED` はダイジェストを持たない（`None`）。それ以外は 16進 64 文字。
    """

    hour: HourKey
    outcome: HourOutcome
    tick_digest: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.hour, HourKey):
            raise MarketDataValueError("ManifestHour.hour must be an HourKey")
        if not isinstance(self.outcome, HourOutcome):
            raise MarketDataValueError("ManifestHour.outcome must be an HourOutcome")
        if self.outcome is HourOutcome.NOT_FETCHED:
            if self.tick_digest is not None:
                raise MarketDataValueError("a NOT_FETCHED hour has no tick digest")
        else:
            require_hex_digest(self.tick_digest, "ManifestHour.tick_digest")

    def payload(self) -> Mapping[str, Any]:
        """manifest に書く形。"""
        return {
            "hour": self.hour.payload(),
            "outcome": self.outcome.value,
            "tick_digest": self.tick_digest,
        }


@dataclass(frozen=True, slots=True)
class RefillManifestCore:
    """補充の manifest `refill_manifest.json` のうち、書き出し済みの判定と取り直しに使う部分。

    PR 1 が定める最小の項目（PR 2 の書き出しはこれを含めて拡張する。ほかの鍵は読まない）:

    - `plan_id`: この補充分を書き出した計画（16進 64 文字）。
    - `hours`: 時間ファイルごとの `{"hour": {"symbol", "start"}, "outcome", "tick_digest"}` の
      列。`(銘柄, 時刻)` で整列し、同じ時間は 1 回だけ。
    - 完成の印: `refill_manifest.json` そのもの（補充分の他のファイルをすべて置いた後に置く。
      D03 §14.11.1 の W4）。

    形が違えば `MarketDataValueError`（読み手は `RefillStoreInconsistent` にする。W5・W6）。
    """

    plan_id: str
    hours: tuple[ManifestHour, ...]

    def __post_init__(self) -> None:
        require_hex_digest(self.plan_id, "RefillManifestCore.plan_id")
        if not isinstance(self.hours, tuple) or not self.hours:
            raise MarketDataValueError("RefillManifestCore.hours must be a non-empty tuple")
        keys = [item.hour.sort_key() for item in self.hours]
        if any(earlier >= later for earlier, later in zip(keys, keys[1:], strict=False)):
            raise MarketDataValueError(
                "RefillManifestCore.hours must be sorted by (symbol, start) without duplicates"
            )

    def digests(self) -> dict[HourKey, ManifestHour]:
        """時間ごとの記録。"""
        return {item.hour: item for item in self.hours}

    def payload(self) -> Mapping[str, Any]:
        """manifest に書く形（PR 1 の範囲の鍵だけ）。"""
        return {"hours": [item.payload() for item in self.hours], "plan_id": self.plan_id}

    @classmethod
    def from_payload(cls, payload: object) -> RefillManifestCore:
        """manifest の内容から読む（ほかの鍵は PR 2 のもので、ここでは読まない）。"""
        label = "refill_manifest.json"
        mapping = _mapping(payload, label)
        for key in ("plan_id", "hours"):
            if key not in mapping:
                raise MarketDataValueError(f"{label} lacks {key!r}")
        hours: list[ManifestHour] = []
        for index, item in enumerate(_sequence(mapping["hours"], f"{label}.hours")):
            item_label = f"{label}.hours[{index}]"
            entry = _mapping(item, item_label)
            for key in ("hour", "outcome", "tick_digest"):
                if key not in entry:
                    raise MarketDataValueError(f"{item_label} lacks {key!r}")
            hours.append(
                ManifestHour(
                    hour=HourKey.from_payload(entry["hour"], f"{item_label}.hour"),
                    outcome=_enum(entry["outcome"], HourOutcome, f"{item_label}.outcome"),
                    tick_digest=_optional_hex(entry["tick_digest"], f"{item_label}.tick_digest"),
                )
            )
        return cls(
            plan_id=require_hex_digest(mapping["plan_id"], f"{label}.plan_id"),
            hours=tuple(hours),
        )


#: 取得記録の行の型。
JournalEntry = (
    FinalResult
    | AttemptRecord
    | RetryMark
    | Invalidation
    | PauseStart
    | PauseEnd
    | ValidationRecord
)


def _enum(value: object, enum_type: type[Any], label: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise MarketDataValueError(f"{label}: unknown value {value!r}") from exc


def journal_entry_from_payload(payload: object) -> JournalEntry:
    """取得記録の 1 行の内容から行の型を作る。形が違えば `MarketDataValueError`。"""
    mapping = _mapping(payload, "journal entry")
    kind = mapping.get("kind")
    label = f"journal entry ({kind})"
    if kind == FinalResult.KIND:
        _require_keys(
            mapping,
            frozenset(
                {
                    "archive_file",
                    "at",
                    "attempts",
                    "detail",
                    "failure",
                    "fetched_at",
                    "from_archive",
                    "hour",
                    "http_status",
                    "kind",
                    "outcome",
                    "response_sha256",
                    "tick_count",
                    "tick_digest",
                    "url",
                }
            ),
            label,
        )
        return FinalResult(
            hour=HourKey.from_payload(mapping["hour"], f"{label}.hour"),
            outcome=_enum(mapping["outcome"], HourOutcome, f"{label}.outcome"),
            from_archive=_require_bool(mapping["from_archive"], f"{label}.from_archive"),
            tick_digest=_optional_hex(mapping["tick_digest"], f"{label}.tick_digest"),
            tick_count=_optional_int(mapping["tick_count"], f"{label}.tick_count", minimum=0),
            archive_file=_optional_str(mapping["archive_file"], f"{label}.archive_file"),
            url=_optional_str(mapping["url"], f"{label}.url"),
            fetched_at=_parse_optional_time(mapping["fetched_at"], f"{label}.fetched_at"),
            http_status=_optional_int(mapping["http_status"], f"{label}.http_status", minimum=100),
            response_sha256=_optional_hex(mapping["response_sha256"], f"{label}.response_sha256"),
            attempts=_require_int(mapping["attempts"], f"{label}.attempts", minimum=0),
            failure=None
            if mapping["failure"] is None
            else _enum(mapping["failure"], FailureKind, f"{label}.failure"),
            detail=_require_str(mapping["detail"], f"{label}.detail", allow_empty=True),
            at=_parse_time(mapping["at"], f"{label}.at"),
        )
    if kind == AttemptRecord.KIND:
        _require_keys(
            mapping,
            frozenset({"at", "attempt", "detail", "failure", "hour", "http_status", "kind"}),
            label,
        )
        return AttemptRecord(
            hour=HourKey.from_payload(mapping["hour"], f"{label}.hour"),
            attempt=_require_int(mapping["attempt"], f"{label}.attempt", minimum=0),
            failure=_enum(mapping["failure"], FailureKind, f"{label}.failure"),
            http_status=_optional_int(mapping["http_status"], f"{label}.http_status", minimum=100),
            detail=_require_str(mapping["detail"], f"{label}.detail", allow_empty=True),
            at=_parse_time(mapping["at"], f"{label}.at"),
        )
    if kind == RetryMark.KIND:
        _require_keys(mapping, frozenset({"at", "hour", "kind"}), label)
        return RetryMark(
            hour=HourKey.from_payload(mapping["hour"], f"{label}.hour"),
            at=_parse_time(mapping["at"], f"{label}.at"),
        )
    if kind == Invalidation.KIND:
        _require_keys(mapping, frozenset({"at", "hour", "kind", "reason"}), label)
        return Invalidation(
            hour=HourKey.from_payload(mapping["hour"], f"{label}.hour"),
            reason=_require_str(mapping["reason"], f"{label}.reason"),
            at=_parse_time(mapping["at"], f"{label}.at"),
        )
    if kind == PauseStart.KIND:
        _require_keys(mapping, frozenset({"at", "consecutive_failures", "kind", "seconds"}), label)
        return PauseStart(
            consecutive_failures=_require_int(
                mapping["consecutive_failures"], f"{label}.consecutive_failures", minimum=1
            ),
            seconds=_require_int(mapping["seconds"], f"{label}.seconds", minimum=0),
            at=_parse_time(mapping["at"], f"{label}.at"),
        )
    if kind == PauseEnd.KIND:
        _require_keys(mapping, frozenset({"at", "kind"}), label)
        return PauseEnd(at=_parse_time(mapping["at"], f"{label}.at"))
    if kind == ValidationRecord.KIND:
        _require_keys(mapping, frozenset({"at", "details", "kind", "passed", "reasons"}), label)
        return ValidationRecord(
            passed=_require_bool(mapping["passed"], f"{label}.passed"),
            reasons=tuple(
                _require_str(reason, f"{label}.reasons")
                for reason in _sequence(mapping["reasons"], f"{label}.reasons")
            ),
            at=_parse_time(mapping["at"], f"{label}.at"),
            details=_mapping(mapping["details"], f"{label}.details"),
        )
    raise MarketDataValueError(f"unknown journal entry kind {kind!r}")
