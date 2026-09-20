"""正規化エンコードのプロパティテスト（D02 §9.3・§11）。

hypothesis で多数の値を生成し、次を確かめる。

- ダイジェストがキーの並び順に依存しないこと。
- `Decimal` の表現が正規化されること（`150.00` と `150` が同じ）と厳密であること
  （精度 28 を超える桁数の異なる2値が異なる表現になり、コンテキスト精度を変えても表現が
  変わらず、巨大な指数の値でも桁へ展開しないこと）。
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from odyssey_fx.common import canonical

#: 生成する JSON 互換の値（入れ子を含む）。
_json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers(min_value=-(10**12), max_value=10**12)
    | st.text(max_size=20)
    | st.decimals(allow_nan=False, allow_infinity=False, places=None),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children, max_size=4)
    ),
    max_leaves=12,
)

#: 有限の `Decimal`（特殊値を除く）。
_finite_decimals = st.decimals(allow_nan=False, allow_infinity=False, places=None)


# --- キー順序への非依存 -----------------------------------------------------


@given(st.dictionaries(st.text(max_size=8), _json_values, max_size=8), st.data())
def test_digest_does_not_depend_on_key_order(
    payload: dict[str, object], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(list(payload)))
    reordered = {key: payload[key] for key in shuffled}
    assert canonical.digest(payload) == canonical.digest(reordered)
    assert canonical.encode(payload) == canonical.encode(reordered)


@given(st.lists(st.tuples(st.text(max_size=6), _json_values), max_size=6))
def test_mapping_encoding_is_a_function_of_the_key_value_set(
    pairs: list[tuple[str, object]],
) -> None:
    forward = dict(pairs)
    backward = dict(reversed(pairs))
    assume(forward == backward)
    assert canonical.encode(forward) == canonical.encode(backward)


# --- Decimal の正規化と厳密性 -----------------------------------------------


@given(_finite_decimals)
def test_equal_decimals_share_one_representation(value: Decimal) -> None:
    """数値として等しい `Decimal` は、桁の書き方が違っても同じ表現になる。"""
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int)
    # 末尾にゼロ桁を1つ足し、指数を1つ下げる（値は変わらないが `as_tuple()` は変わる）。
    padded = Decimal((sign, tuple(digits) + (0,), exponent - 1))
    assert padded == value
    assert canonical.encode_decimal(padded) == canonical.encode_decimal(value)


@given(_finite_decimals, _finite_decimals)
def test_different_decimals_get_different_representations(left: Decimal, right: Decimal) -> None:
    assume(left != right)
    assert canonical.encode_decimal(left) != canonical.encode_decimal(right)


@given(_finite_decimals, st.integers(min_value=1, max_value=60))
def test_decimal_representation_is_independent_of_the_active_context(
    value: Decimal, precision: int
) -> None:
    baseline = canonical.encode_decimal(value)
    with localcontext() as context:
        context.prec = precision
        assert canonical.encode_decimal(value) == baseline


@given(
    st.integers(min_value=1, max_value=10**28),
    st.integers(min_value=10**6, max_value=10**9),
)
@settings(max_examples=50)
def test_huge_exponents_are_not_expanded(digits: int, exponent: int) -> None:
    """指数を桁へ展開しないので、表現の長さは桁数と指数の桁数で抑えられる。

    `scaleb` はコンテキストの Emax で頭打ちになるため、`as_tuple()` から直接組み立てる。
    """
    text = str(digits)
    value = Decimal((0, tuple(int(char) for char in text), exponent))
    encoded = canonical.encode_decimal(value)
    assert len(encoded) <= len(text) + len(str(exponent)) + 1
    assert "e" in encoded
    # 展開すれば 10 億桁になる値でも、表現は数十文字で収まる。
    assert len(encoded) < 100


@given(st.integers(min_value=-(10**6), max_value=10**6), st.integers(min_value=0, max_value=20))
def test_trailing_zero_digits_do_not_change_the_representation(digits: int, zeros: int) -> None:
    value = Decimal(digits)
    padded = Decimal(f"{digits}{'0' * zeros}").scaleb(-zeros)
    assert padded == value
    assert canonical.encode_decimal(padded) == canonical.encode_decimal(value)


def test_precision_28_boundary_is_exact() -> None:
    """精度 28 で丸めると衝突する2値が、厳密表現では区別される（D02 §9.3）。"""
    left = Decimal("123456789012345678901234567890")
    right = Decimal("123456789012345678901234567900")
    with localcontext() as context:
        context.prec = 28
        assert +left == +right
    assert canonical.encode_decimal(left) != canonical.encode_decimal(right)
    assert canonical.digest(left) != canonical.digest(right)
