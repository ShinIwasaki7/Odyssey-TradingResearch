"""字種検査が末尾の改行を受け入れないことの確認（Codex レビュー round 1 指摘1）。

Python の正規表現では `$` が「文字列の末尾」だけでなく「末尾の改行1文字の直前」にも一致
する。そのため `re.match(r"^[A-Z]{3}$", "USD\\n")` は成功してしまい、`CurrencyCode("USD\\n")`
のような値が構築できてしまっていた。`ContentDigest` では 64 文字のはずの16進文字列に 65
文字が入るなど、不変条件そのものが破れる。

`common` の字種検査はすべて `fullmatch` で行う。ここではその挙動を型ごとに固定する。
検査を `match` に戻すと、このテストがすべて落ちる。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import OrderId
from odyssey_fx.common.money import CurrencyCode, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import PhaseRank
from odyssey_fx.common.timeframe import TimeframeRef

#: (型の名前, 正しい値, その値から構築する呼び出し)。
#: 呼び出しには「末尾に改行を付けた値」を渡してもよいよう、値を引数に取る形にする。
VALIDATED_BY_PATTERN: list[tuple[str, str, Callable[[str], object]]] = [
    ("ContentDigest.hex", "a" * 64, ContentDigest.sha256),
    ("CurrencyCode.code", "USD", CurrencyCode),
    ("Symbol.code", "USDJPY", Symbol),
    ("PhaseRank.name", "ADMISSION", lambda name: PhaseRank(0, name)),
    ("TimeframeRef.id", "15m", lambda identifier: TimeframeRef(identifier, 1)),
    ("SequentialId.parse", "ORD:00000042", OrderId.parse),
    ("decimal_from_str", "1.5", decimal_from_str),
]


@pytest.mark.parametrize(
    ("label", "valid", "construct"),
    VALIDATED_BY_PATTERN,
    ids=[case[0] for case in VALIDATED_BY_PATTERN],
)
def test_the_valid_value_is_accepted(
    label: str, valid: str, construct: Callable[[str], object]
) -> None:
    """改行なしの正しい値は受け入れられる（検査が過剰に厳しくないことの確認）。"""
    assert construct(valid) is not None


@pytest.mark.parametrize(
    ("label", "valid", "construct"),
    VALIDATED_BY_PATTERN,
    ids=[case[0] for case in VALIDATED_BY_PATTERN],
)
def test_a_trailing_newline_is_rejected(
    label: str, valid: str, construct: Callable[[str], object]
) -> None:
    with pytest.raises(KernelValueError):
        construct(valid + "\n")


@pytest.mark.parametrize(
    ("label", "valid", "construct"),
    VALIDATED_BY_PATTERN,
    ids=[case[0] for case in VALIDATED_BY_PATTERN],
)
def test_a_leading_newline_is_rejected(
    label: str, valid: str, construct: Callable[[str], object]
) -> None:
    with pytest.raises(KernelValueError):
        construct("\n" + valid)


@pytest.mark.parametrize(
    ("label", "valid", "construct"),
    VALIDATED_BY_PATTERN,
    ids=[case[0] for case in VALIDATED_BY_PATTERN],
)
def test_trailing_text_after_a_newline_is_rejected(
    label: str, valid: str, construct: Callable[[str], object]
) -> None:
    """改行のあとに別の内容が続く値も拒否する。"""
    with pytest.raises(KernelValueError):
        construct(valid + "\nxx")


def test_a_content_digest_never_stores_more_than_64_characters() -> None:
    """指摘の具体例: 65 文字の16進文字列が保存できてはならない。"""
    with pytest.raises(KernelValueError, match="64 lowercase hex"):
        ContentDigest.sha256("a" * 64 + "\n")
