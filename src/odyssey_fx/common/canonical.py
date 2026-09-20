"""正規化エンコードとダイジェスト（D02 §9.3・§9.4）。

同じ値が常に同じバイト列になり、異なる値が異なるバイト列になる「正規化エンコード」を
与え、その sha256 をダイジェストとする。設定・snapshot・実験 spec の識別子はすべて
これで作る（D02 §9.3）。

エンコード規則（D02 §9.3）:

- JSON 互換のテキスト。キーは Unicode コードポイント順に整列、空白なし、
  `ensure_ascii=False`、UTF-8。
- `Decimal` は Decimal コンテキストに依存しない厳密表現。`normalize()` はコンテキストの
  精度で丸めるため使わず、`as_tuple()` の `(sign, digits, exponent)` から末尾のゼロ桁だけを
  取り除いて指数を調整し、その組を展開せずに `"<sign><digits>e<exponent>"` と符号化する。
- `float` は有限値のみ、`repr` の最短往復表現。`nan` / `inf` は拒否。
- `UtcTime` / ID 型 / `Symbol` / `CurrencyCode` / `TimeframeRef` は `__str__`。
  `Interval` は `{"start": …, "end": …}`。
- `Enum` は `value`。dataclass はフィールド名をキーとする mapping（型名は含めない）。
  `tuple` / `list` は配列、`Mapping` はオブジェクト、`set` は拒否（順序が定まらない）。
- `None` は `null`。それ以外の型は `KernelValueError`。

本モジュールは `common` の他モジュールを import しない（`errors` を除く）。型の判定は
構造的に行い、`refs` / `time` / `ids` からの循環 import を避ける。
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from odyssey_fx.common.errors import KernelValueError

if TYPE_CHECKING:
    from odyssey_fx.common.refs import ContentDigest

__all__ = [
    "DIGEST_ALGORITHM",
    "CanonicalScalar",
    "code_digest_hex",
    "digest",
    "encode",
    "encode_decimal",
    "env_digest_input",
    "lock_digest_hex",
]

#: 初版で使うハッシュ関数（D02 §9.1）。
DIGEST_ALGORITHM: Final = "sha256"

#: `CodeDigest` の対象とする拡張子（D02 §9.4）。
_SOURCE_SUFFIX: Final = ".py"

#: `CodeDigest` の対象から外すディレクトリ名（D02 §9.4）。
_EXCLUDED_DIR_NAMES: Final = frozenset({"__pycache__"})


@runtime_checkable
class CanonicalScalar(Protocol):
    """正規化表現を1つの文字列で与える型の印（D02 §9.3）。

    `UtcTime`・ID 型・`Symbol`・`CurrencyCode`・`TimeframeRef` は dataclass だが、
    D02 §9.3 はこれらを `__str__` で符号化すると定める。dataclass の一般規則（フィールド名
    をキーとする mapping）より先にこの印を見て、`canonical_str()` の戻り値を採用する。
    `canonical.py` が `time` / `ids` / `symbol` を import せずに済むよう、型ではなく構造で
    見分ける。
    """

    def canonical_str(self) -> str: ...


def encode_decimal(value: Decimal) -> str:
    """`Decimal` のコンテキスト非依存な厳密表現（D02 §9.3）。

    `as_tuple()` の `(sign, digits, exponent)` から末尾のゼロ桁だけを取り除いて指数を
    調整し、`"<sign><digits>e<exponent>"` を返す。指数を桁に展開しないため、
    `Decimal("1E+1000000000")` のような巨大な指数でもメモリを消費しない。
    桁数・指数に上限を設けず、いかなるコンテキストでも同じ値は同じ表現、異なる値は異なる
    表現になる。`150.00` と `150` は同じ値として同じ表現（`"15e1"`）になり、`-0` は `0`
    に正規化する。

    人間向けの表示形式（固定小数）は各型の `__str__` が担い、ダイジェストには使わない。
    """
    if not isinstance(value, Decimal):  # pragma: no cover - 呼び出し側で保証する
        raise KernelValueError(f"encode_decimal requires a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise KernelValueError(f"cannot canonically encode a non-finite Decimal: {value!r}")

    sign, digits, exponent = value.as_tuple()
    if not isinstance(exponent, int):  # pragma: no cover - is_finite() で除外済み
        raise KernelValueError(f"cannot canonically encode a special Decimal: {value!r}")

    # 末尾のゼロ桁を取り除き、取り除いた分だけ指数を上げる（値は変わらない）。
    end = len(digits)
    while end > 1 and digits[end - 1] == 0:
        end -= 1
        exponent += 1
    kept = digits[:end]

    # ゼロは符号と指数を捨てて `0e0` に正規化する（`-0` → `0`、`0.00` → `0`）。
    if kept == (0,):
        return "0e0"

    prefix = "-" if sign else ""
    body = "".join(str(digit) for digit in kept)
    return f"{prefix}{body}e{exponent}"


def _encode_float(value: float) -> str:
    """`float` の最短往復表現（D02 §9.3）。`nan` / `inf` は拒否する。"""
    if not math.isfinite(value):
        raise KernelValueError(f"cannot canonically encode a non-finite float: {value!r}")
    return repr(value)


def _encode_str(value: str) -> str:
    """JSON 文字列リテラル（`ensure_ascii=False` で非 ASCII をそのまま残す）。

    エスケープ規則を自前で書くと標準の JSON エンコーダと細部（`\\b`・`\\f`・`0x7f` の扱い）
    が食い違い、manifest から外部ツールがダイジェストを再計算したときに一致しなくなる。
    D02 §9.3 が求める「JSON 互換のテキスト」を保証するため、標準ライブラリに委ねる。
    """
    return json.dumps(value, ensure_ascii=False)


def _encode_mapping(value: Mapping[Any, Any]) -> str:
    """キーを Unicode コードポイント順に整列したオブジェクト（D02 §9.3）。"""
    items: list[tuple[str, Any]] = []
    for key, item in value.items():
        if not isinstance(key, str):
            raise KernelValueError(f"canonical mapping keys must be str, got {type(key).__name__}")
        items.append((key, item))
    items.sort(key=lambda pair: pair[0])
    seen: set[str] = set()
    for key, _ in items:
        if key in seen:  # pragma: no cover - Mapping はキー重複を持たない
            raise KernelValueError(f"duplicate canonical mapping key: {key!r}")
        seen.add(key)
    body = ",".join(f"{_encode_str(key)}:{_to_text(item)}" for key, item in items)
    return f"{{{body}}}"


def _encode_sequence(value: Sequence[Any]) -> str:
    return "[" + ",".join(_to_text(item) for item in value) + "]"


def _encode_dataclass(value: Any) -> str:
    """dataclass はフィールド名をキーとする mapping（型名は含めない、D02 §9.3）。"""
    payload = {field.name: getattr(value, field.name) for field in fields(value)}
    return _encode_mapping(payload)


def _to_text(value: Any) -> str:
    """D02 §9.3 の規則に従って1つの値をテキストへ符号化する。"""
    # `None` と `bool` は `int` より先に判定する（`bool` は `int` の派生型）。
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return _to_text(value.value)
    if isinstance(value, Decimal):
        return _encode_str(encode_decimal(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _encode_float(value)
    if isinstance(value, str):
        return _encode_str(value)
    if isinstance(value, (set, frozenset)):
        raise KernelValueError("cannot canonically encode a set (its order is undefined)")
    # dataclass の一般規則より先に、`canonical_str()` を持つ型（`UtcTime`・ID 型・
    # `Symbol`・`CurrencyCode`・`TimeframeRef`）を文字列として符号化する。
    if isinstance(value, CanonicalScalar):
        text = value.canonical_str()
        if not isinstance(text, str):  # pragma: no cover - 実装側で保証する
            raise KernelValueError("canonical_str() must return a str")
        return _encode_str(text)
    if isinstance(value, Mapping):
        return _encode_mapping(value)
    if isinstance(value, (tuple, list)):
        return _encode_sequence(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _encode_dataclass(value)
    raise KernelValueError(f"cannot canonically encode a value of type {type(value).__name__}")


def encode(obj: Any) -> bytes:
    """正規化エンコード（D02 §9.3）。UTF-8 のバイト列を返す。"""
    return _to_text(obj).encode("utf-8")


def digest(obj: Any) -> ContentDigest:
    """正規化エンコードの sha256（D02 §9.3）。"""
    from odyssey_fx.common.refs import ContentDigest

    return ContentDigest(algorithm=DIGEST_ALGORITHM, hex=hashlib.sha256(encode(obj)).hexdigest())


def _source_files(package_dir: Path) -> list[tuple[str, Path]]:
    """パッケージディレクトリ配下の `.py` を、相対パスのコードポイント順で返す。"""
    collected: list[tuple[str, Path]] = []
    for path in package_dir.rglob(f"*{_SOURCE_SUFFIX}"):
        if not path.is_file():
            continue
        relative = path.relative_to(package_dir)
        if _EXCLUDED_DIR_NAMES.intersection(relative.parts):
            continue
        collected.append((relative.as_posix(), path))
    collected.sort(key=lambda pair: pair[0])
    return collected


def code_digest_hex(package_dir: Path) -> str:
    """実際に import されたパッケージのソース内容のダイジェスト（D02 §9.4）。

    対象ディレクトリは `odyssey_fx.__file__` が解決するパッケージディレクトリであり、
    git の作業ツリーやリポジトリのパスからは決めない。対象ファイルはその配下の拡張子
    `.py` すべて（`__pycache__` は除く）。相対パス（POSIX 区切り）を Unicode コードポイント
    順に整列し、各ファイルについて
    `path_bytes + b"\\0" + str(len(content)).encode() + b"\\0" + content_bytes`
    を順に sha256 へ投入する。改行コードや空白の正規化は行わない。
    git commit・dirty 状態は算出に使わず、manifest に別途記録する。
    """
    directory = Path(package_dir)
    if not directory.is_dir():
        raise KernelValueError(f"code digest requires a package directory: {directory}")

    hasher = hashlib.sha256()
    for relative, path in _source_files(directory):
        content = path.read_bytes()
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(len(content)).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(content)
    return hasher.hexdigest()


def lock_digest_hex(lock_path: Path) -> str:
    """`uv.lock` の内容バイト列の sha256（D02 §9.4）。

    `uv.lock` が存在しない、または `pyproject.toml` と整合しない場合に run を開始しない
    判断は `app` の責務であり、本関数は内容のダイジェストだけを返す。
    """
    path = Path(lock_path)
    if not path.is_file():
        raise KernelValueError(f"lock file not found: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def env_digest_input(
    *,
    python_implementation: str,
    python_version: str,
    sys_platform: str,
    machine: str,
    distributions: Mapping[str, str],
) -> Mapping[str, Any]:
    """`EnvDigest` の対象となる mapping を組み立てる（D02 §9.4、ADR-0006）。

    `distributions` は「配布物名 → 版」。名前は正規化形（小文字、`-` / `_` / `.` を `-` に
    統一）にし、その順に整列した `"名前==版"` の列にする。`odyssey_fx` 自身は `CodeDigest`
    が識別するため呼び出し側（`app`）が除外する。実際の値の収集は `app` が行い、
    `common` は構造だけを与える。

    正規化後に同じ名前になる配布物が2つ以上あるとき（例: `foo_bar` と `foo-bar`）は
    `KernelValueError` を送出する。黙って一方を捨てると、`distributions` の並び順という
    本来無関係なものに `EnvDigest`（ひいては `RunId`）が左右され、決定論が崩れるため。
    """
    normalized: dict[str, tuple[str, str]] = {}
    for raw_name, version in distributions.items():
        key = _normalize_distribution_name(raw_name)
        existing = normalized.get(key)
        if existing is not None:
            existing_raw, existing_version = existing
            raise KernelValueError(
                f"distribution names {existing_raw!r} and {raw_name!r} both normalize to"
                f" {key!r} (versions {existing_version!r} and {version!r});"
                " resolve the collision before building the environment digest"
            )
        normalized[key] = (raw_name, version)
    entries = tuple(f"{name}=={normalized[name][1]}" for name in sorted(normalized))
    return {
        "distributions": entries,
        "machine": machine,
        "python_implementation": python_implementation,
        "python_version": python_version,
        "sys_platform": sys_platform,
    }


def _normalize_distribution_name(name: str) -> str:
    """配布物名の正規化形（小文字、`-` / `_` / `.` を `-` に統一）。"""
    lowered = name.lower()
    for char in ("_", "."):
        lowered = lowered.replace(char, "-")
    return lowered
