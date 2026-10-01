"""提供元 dukascopy の時間ファイルの取得（D03 §8・§14.3・§14.5、D01 §4 v2.9）。

`TickArchiveSource`（`marketdata.application.ports`）を実装する。ポートの定義は import せず
構造的に満たす（D01 §2.2 規則7）。標準ライブラリだけで作る（`urllib`・`lzma`・`struct`。
D03 §14.16）。

- `request`: URL へ 1 回だけ要求する。タイムアウト・接続不能は例外にせず、失敗の種類として
  返す。何回・いつ要求するかは application の規則が決める。
- `decode`: bi5 の中身（LZMA 圧縮。解凍後は tick 1 件が 20 バイトのビッグエンディアン
  `>iiiff`: 時間の始まりからのミリ秒、ask の整数値、bid の整数値、ask の量、bid の量）を
  読み取る。空のバイト列は tick 0 件。tick の内容のダイジェストは**解凍後のバイト列**の
  sha256（圧縮のやり方が変わっても tick が同じなら同じ値。D03 §14.10）。
- `wait` / `now`: 待ちと実時計（D01 §4 v2.9 で adapters の側に置く）。
"""

from __future__ import annotations

import http.client
import lzma
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from odyssey_fx.common.time import UtcTime
from odyssey_fx.marketdata.domain.errors import MarketDataValueError
from odyssey_fx.marketdata.domain.refill import (
    DecodedTicks,
    FailureKind,
    HttpResult,
    Tick,
    sha256_hex,
)

__all__ = ["DukascopyTickSource", "decode_bi5"]

#: 解凍後の tick 1 件の形（D03 §14.3）。
_TICK: Final = struct.Struct(">iiiff")

#: 要求に付ける利用者の名乗り。
_USER_AGENT: Final = "odyssey-fx-refill/1"


def decode_bi5(body: bytes) -> DecodedTicks:
    """bi5 の中身を解凍して tick を読み取る。読めなければ `MarketDataValueError`。"""
    if not isinstance(body, bytes):
        raise MarketDataValueError("decode_bi5 requires bytes")
    if body:
        try:
            raw = lzma.decompress(body)
        except lzma.LZMAError as exc:
            raise MarketDataValueError(f"the content is not LZMA-compressed bi5: {exc}") from exc
    else:
        raw = b""
    if len(raw) % _TICK.size:
        raise MarketDataValueError(
            f"the decompressed content has {len(raw)} bytes, not a multiple of"
            f" {_TICK.size} (one tick is >iiiff)"
        )
    ticks = tuple(
        Tick(offset_ms=offset, ask_raw=ask, bid_raw=bid)
        for offset, ask, bid, _ask_volume, _bid_volume in _TICK.iter_unpack(raw)
    )
    return DecodedTicks(tick_digest=sha256_hex(raw), ticks=ticks)


def _now() -> UtcTime:
    return UtcTime(datetime.now(UTC))


def _is_timeout(error: str | BaseException) -> bool:
    return isinstance(error, TimeoutError)


@dataclass(frozen=True, slots=True)
class DukascopyTickSource:
    """dukascopy の時間ファイルを取る（D03 §14.3）。"""

    user_agent: str = _USER_AGENT

    def request(self, url: str, timeout_seconds: int) -> HttpResult:
        """URL へ 1 回だけ要求する。https 以外の URL は拒否する（設定の誤り）。"""
        if not url.startswith("https://"):
            raise MarketDataValueError(f"the provider URL must use https, got {url!r}")
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                body = response.read()
                status = int(response.status)
        except urllib.error.HTTPError as error:
            return HttpResult(
                url=url,
                status=int(error.code),
                body=b"",
                failure=None,
                detail=str(error.reason),
                fetched_at=_now(),
            )
        except urllib.error.URLError as error:
            kind = FailureKind.TIMEOUT if _is_timeout(error.reason) else FailureKind.CONNECTION
            return HttpResult(
                url=url, status=None, body=b"", failure=kind, detail=str(error), fetched_at=_now()
            )
        except TimeoutError as error:
            return HttpResult(
                url=url,
                status=None,
                body=b"",
                failure=FailureKind.TIMEOUT,
                detail=str(error) or "timed out",
                fetched_at=_now(),
            )
        except (OSError, http.client.HTTPException) as error:
            return HttpResult(
                url=url,
                status=None,
                body=b"",
                failure=FailureKind.CONNECTION,
                detail=f"{type(error).__name__}: {error}",
                fetched_at=_now(),
            )
        return HttpResult(
            url=url, status=status, body=body, failure=None, detail="", fetched_at=_now()
        )

    def decode(self, body: bytes) -> DecodedTicks:
        """bi5 の中身を解凍して tick を読み取る（`decode_bi5`）。"""
        return decode_bi5(body)

    def wait(self, seconds: float) -> None:
        """指定した秒数だけ待つ。"""
        if seconds > 0:
            time.sleep(seconds)

    def now(self) -> UtcTime:
        """いまの UTC の時刻。"""
        return _now()
