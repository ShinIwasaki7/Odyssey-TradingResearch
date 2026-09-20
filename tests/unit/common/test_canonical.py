"""`odyssey_fx.common.canonical` の単体テスト（D02 §9.3・§9.4・§11）。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import Enum
from pathlib import Path

import pytest

from odyssey_fx.common import canonical
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import OrderId, RunId
from odyssey_fx.common.money import CurrencyCode, decimal_from_str
from odyssey_fx.common.refs import ContentDigest
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.common.timeframe import TimeframeRef

D = decimal_from_str


class _Color(Enum):
    RED = "red"
    ONE = 1


@dataclass(frozen=True, slots=True)
class _Point:
    x: int
    y: str


# --- Decimal の厳密表現（D02 §9.3）-----------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("150.00", "15e1"),
        ("150", "15e1"),
        ("1.5e2", "15e1"),
        ("-0.5", "-5e-1"),
        ("0", "0e0"),
        ("-0", "0e0"),
        ("0.000", "0e0"),
        ("1", "1e0"),
        ("100", "1e2"),
        ("0.001", "1e-3"),
        ("-123.450", "-12345e-2"),
    ],
)
def test_encode_decimal_normalizes_trailing_zeros(text: str, expected: str) -> None:
    assert canonical.encode_decimal(D(text)) == expected


def test_encode_decimal_keeps_huge_exponents_unexpanded() -> None:
    """指数を桁へ展開しないので、巨大な指数でもメモリを消費しない（D02 §9.3）。"""
    encoded = canonical.encode_decimal(D("1E+1000000000"))
    assert encoded == "1e1000000000"
    assert len(encoded) < 20


def test_encode_decimal_separates_values_beyond_context_precision() -> None:
    """精度 28 を超える桁数の異なる2値が異なる表現になる（D02 §9.3・§11）。"""
    left = D("123456789012345678901234567890")
    right = D("123456789012345678901234567900")
    assert left != right
    assert canonical.encode_decimal(left) != canonical.encode_decimal(right)


def test_encode_decimal_is_independent_of_the_active_context() -> None:
    value = D("123456789012345678901234567890")
    baseline = canonical.encode_decimal(value)
    for precision in (1, 5, 28, 50):
        with localcontext() as context:
            context.prec = precision
            assert canonical.encode_decimal(value) == baseline


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
def test_encode_decimal_rejects_non_finite_values(text: str) -> None:
    with pytest.raises(KernelValueError, match="non-finite"):
        canonical.encode_decimal(Decimal(text))


def test_encode_decimal_requires_a_decimal() -> None:
    with pytest.raises(KernelValueError, match="requires a Decimal"):
        canonical.encode_decimal("1.0")  # type: ignore[arg-type]


# --- スカラーの符号化 -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, b"null"),
        (True, b"true"),
        (False, b"false"),
        (0, b"0"),
        (-42, b"-42"),
        ("abc", b'"abc"'),
        (0.5, b"0.5"),
        (_Color.RED, b'"red"'),
        (_Color.ONE, b"1"),
    ],
)
def test_encode_scalars(value: object, expected: bytes) -> None:
    assert canonical.encode(value) == expected


def test_encode_decimal_values_are_quoted_strings() -> None:
    assert canonical.encode(D("150.00")) == b'"15e1"'


def test_encode_strings_keeps_non_ascii_and_escapes_controls() -> None:
    assert canonical.encode("円") == '"円"'.encode()
    assert canonical.encode('a"b\\c') == b'"a\\"b\\\\c"'
    assert canonical.encode("a\nb\tc\rd") == b'"a\\nb\\tc\\rd"'
    assert canonical.encode("\x00") == b'"\\u0000"'


@pytest.mark.parametrize(
    "text",
    ["", "abc", "円", 'a"b', "a\\b", "a\nb", "a\tb", "a\rb", "a\bb", "a\fb", "\x00", "\x7f", "🙂"],
)
def test_string_encoding_matches_the_standard_json_encoder(text: str) -> None:
    """外部ツールが manifest から再計算できるよう、標準の JSON エンコーダと一致させる。

    自前のエスケープ処理では `\\b`・`\\f`・`0x7f` の扱いが標準と食い違っていた。
    """
    assert canonical.encode(text) == json.dumps(text, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_encode_rejects_non_finite_floats(value: float) -> None:
    with pytest.raises(KernelValueError, match="non-finite float"):
        canonical.encode(value)


def test_encode_floats_use_the_shortest_roundtrip_repr() -> None:
    assert canonical.encode(0.1 + 0.2) == repr(0.30000000000000004).encode()


# --- コレクションと dataclass -----------------------------------------------


def test_mapping_keys_are_sorted_by_code_point() -> None:
    assert canonical.encode({"b": 1, "a": 2, "A": 3}) == b'{"A":3,"a":2,"b":1}'


def test_mapping_encoding_is_independent_of_insertion_order() -> None:
    assert canonical.encode({"b": 1, "a": 2}) == canonical.encode({"a": 2, "b": 1})


def test_mapping_rejects_non_string_keys() -> None:
    with pytest.raises(KernelValueError, match="keys must be str"):
        canonical.encode({1: "a"})


def test_sequences_keep_their_order() -> None:
    assert canonical.encode([1, 2, 3]) == b"[1,2,3]"
    assert canonical.encode((1, 2, 3)) == b"[1,2,3]"
    assert canonical.encode([3, 1, 2]) != canonical.encode([1, 2, 3])


def test_sets_are_rejected() -> None:
    with pytest.raises(KernelValueError, match="set"):
        canonical.encode({1, 2})
    with pytest.raises(KernelValueError, match="set"):
        canonical.encode(frozenset({1, 2}))


def test_dataclasses_encode_as_field_mappings_without_the_type_name() -> None:
    assert canonical.encode(_Point(1, "a")) == b'{"x":1,"y":"a"}'


def test_dataclasses_with_the_same_fields_encode_identically() -> None:
    @dataclass(frozen=True, slots=True)
    class _Other:
        x: int
        y: str

    assert canonical.encode(_Point(1, "a")) == canonical.encode(_Other(1, "a"))


def test_unsupported_types_are_rejected() -> None:
    with pytest.raises(KernelValueError, match="cannot canonically encode"):
        canonical.encode(object())
    with pytest.raises(KernelValueError, match="cannot canonically encode"):
        canonical.encode(Path("/tmp"))


# --- カーネル型の符号化（D02 §9.3）-----------------------------------------


def test_utc_time_encodes_as_its_string() -> None:
    moment = UtcTime.from_components(2026, 3, 1, 12)
    assert canonical.encode(moment) == b'"2026-03-01T12:00:00Z"'


def test_interval_encodes_as_start_and_end() -> None:
    span = Interval(UtcTime.from_components(2026, 3, 1), UtcTime.from_components(2026, 3, 2))
    assert canonical.encode(span) == (
        b'{"end":"2026-03-02T00:00:00Z","start":"2026-03-01T00:00:00Z"}'
    )


def test_ids_symbols_currencies_and_timeframes_encode_as_their_strings() -> None:
    assert canonical.encode(OrderId(42)) == b'"ORD:00000042"'
    assert canonical.encode(Symbol("USDJPY")) == b'"USDJPY"'
    assert canonical.encode(CurrencyCode("JPY")) == b'"JPY"'
    assert canonical.encode(TimeframeRef("15m", 1)) == b'"15m@v1"'
    assert canonical.encode(RunId(ContentDigest.sha256("a" * 64))) == b'"' + b"a" * 64 + b'"'


def test_content_digest_encodes_as_a_field_mapping() -> None:
    """`ContentDigest` は D02 §9.3 の `__str__` 一覧に無いので、通常の dataclass として扱う。

    16進文字列だけに符号化すると `algorithm` が落ち、manifest からダイジェストを再計算する
    外部ツールと結果が食い違う。
    """
    digest = ContentDigest.sha256("a" * 64)
    assert canonical.encode(digest) == (b'{"algorithm":"sha256","hex":"' + b"a" * 64 + b'"}')


def test_references_embed_the_digest_as_a_nested_mapping() -> None:
    """`ContractRef` などは入れ子の mapping として符号化される（D02 §9.3）。"""
    from odyssey_fx.common.refs import CompiledStrategyRef, ContractRef

    hex_value = "b" * 64
    digest = ContentDigest.sha256(hex_value)
    nested = b'{"algorithm":"sha256","hex":"' + hex_value.encode() + b'"}'

    assert canonical.encode(CompiledStrategyRef(digest)) == b'{"digest":' + nested + b"}"
    assert canonical.encode(ContractRef("ema", 2, digest)) == (
        b'{"component_id":"ema","digest":' + nested + b',"version":2}'
    )


def test_a_digest_id_and_a_bare_content_digest_encode_differently() -> None:
    """ID 型は `__str__`（16進のみ）、`ContentDigest` は mapping で、取り違えが起きない。"""
    digest = ContentDigest.sha256("c" * 64)
    assert canonical.encode(RunId(digest)) != canonical.encode(digest)


# --- digest -----------------------------------------------------------------


def test_digest_is_the_sha256_of_the_encoding() -> None:
    payload = {"a": 1, "b": "x"}
    result = canonical.digest(payload)
    assert result.algorithm == "sha256"
    assert result.hex == hashlib.sha256(canonical.encode(payload)).hexdigest()


def test_digest_is_independent_of_key_order() -> None:
    assert canonical.digest({"b": 1, "a": 2}) == canonical.digest({"a": 2, "b": 1})


def test_digest_distinguishes_different_values() -> None:
    assert canonical.digest({"a": 1}) != canonical.digest({"a": 2})


# --- CodeDigest / LockDigest（D02 §9.4）------------------------------------


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_code_digest_matches_the_documented_feed(tmp_path: Path) -> None:
    _write(tmp_path, "b.py", "b\n")
    _write(tmp_path, "a.py", "a\n")
    _write(tmp_path, "sub/c.py", "c\n")

    expected = hashlib.sha256()
    for relative, content in (("a.py", b"a\n"), ("b.py", b"b\n"), ("sub/c.py", b"c\n")):
        expected.update(relative.encode("utf-8"))
        expected.update(b"\0")
        expected.update(str(len(content)).encode("utf-8"))
        expected.update(b"\0")
        expected.update(content)

    assert canonical.code_digest_hex(tmp_path) == expected.hexdigest()


def test_code_digest_ignores_non_python_and_pycache(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "a\n")
    baseline = canonical.code_digest_hex(tmp_path)

    _write(tmp_path, "py.typed", "")
    _write(tmp_path, "notes.md", "docs\n")
    _write(tmp_path, "__pycache__/a.cpython-312.pyc", "compiled\n")
    _write(tmp_path, "__pycache__/a.py", "compiled\n")
    assert canonical.code_digest_hex(tmp_path) == baseline


def test_code_digest_reacts_to_content_and_path_changes(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "a\n")
    baseline = canonical.code_digest_hex(tmp_path)

    _write(tmp_path, "a.py", "a \n")
    assert canonical.code_digest_hex(tmp_path) != baseline

    _write(tmp_path, "a.py", "a\n")
    _write(tmp_path, "b.py", "a\n")
    assert canonical.code_digest_hex(tmp_path) != baseline


def test_code_digest_does_not_normalize_line_endings(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_bytes(b"a\n")
    unix = canonical.code_digest_hex(tmp_path)
    (tmp_path / "a.py").write_bytes(b"a\r\n")
    assert canonical.code_digest_hex(tmp_path) != unix


def test_code_digest_requires_a_directory(tmp_path: Path) -> None:
    with pytest.raises(KernelValueError, match="package directory"):
        canonical.code_digest_hex(tmp_path / "missing")


def test_lock_digest_is_the_sha256_of_the_bytes(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"version = 1\n")
    assert canonical.lock_digest_hex(lock) == hashlib.sha256(b"version = 1\n").hexdigest()


def test_lock_digest_requires_an_existing_file(tmp_path: Path) -> None:
    with pytest.raises(KernelValueError, match="lock file not found"):
        canonical.lock_digest_hex(tmp_path / "uv.lock")


# --- EnvDigest の入力（D02 §9.4）-------------------------------------------


def test_env_digest_input_normalizes_and_sorts_distribution_names() -> None:
    payload = canonical.env_digest_input(
        python_implementation="CPython",
        python_version="3.12.13",
        sys_platform="darwin",
        machine="arm64",
        distributions={"Ruff": "0.14.0", "import_linter": "2.1", "my.pkg": "1.0"},
    )
    assert payload["distributions"] == ("import-linter==2.1", "my-pkg==1.0", "ruff==0.14.0")
    assert payload["python_version"] == "3.12.13"


def test_env_digest_input_is_independent_of_distribution_order() -> None:
    kwargs = {
        "python_implementation": "CPython",
        "python_version": "3.12.13",
        "sys_platform": "linux",
        "machine": "x86_64",
    }
    first = canonical.env_digest_input(distributions={"a": "1", "b": "2"}, **kwargs)
    second = canonical.env_digest_input(distributions={"b": "2", "a": "1"}, **kwargs)
    assert canonical.digest(first) == canonical.digest(second)
