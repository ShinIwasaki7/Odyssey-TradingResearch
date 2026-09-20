"""参照型とダイジェスト型（D02 §9.1・§9.2・§9.4）。

`ContentDigest` は「内容のハッシュ」そのもの、各 `*Ref` は「どの版のどの内容を指すか」を
固定する不変の参照である。実体（設定・ポリシー・戦略定義）は各パッケージと `configs/`
に置き、`common` は参照だけを持つ。

`RunId` の構成（ADR-0006）はここで組み立てる: `ConfigDigest`（解決済み run 設定）、
`CodeDigest`（実行したソースコード）、`LockDigest`（`uv.lock`）、`EnvDigest`（実行環境）の
4つを mapping にして `canonical.digest` にかける（D02 §9.3）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Self

from odyssey_fx.common import canonical
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EvidenceId, RunId, SnapshotId

__all__ = [
    "CodeDigest",
    "CompiledStrategyRef",
    "ConfigDigest",
    "ContentDigest",
    "ContractRef",
    "EnvDigest",
    "EvidenceRef",
    "ImplementationRef",
    "LockDigest",
    "PolicyRef",
    "SnapshotRef",
    "StrategyRef",
    "run_id",
]

#: `ContentDigest.hex` の形式（D02 §9.1: 64 文字小文字16進）。
#: 照合は必ず `fullmatch` で行う。正規表現の `$` は末尾の改行1文字を許すため、`match` だと
#: `"a" * 64 + "\n"` のような 65 文字の値を取り込んでしまう。以降の各パターンも同様。
_HEX_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")

#: 初版で許可するハッシュ関数（D02 §9.1）。
_ALLOWED_ALGORITHMS: Final = frozenset({"sha256"})


@dataclass(frozen=True, slots=True)
class ContentDigest:
    """内容のハッシュ（D02 §9.1）。初版は `sha256` のみ。"""

    algorithm: str
    hex: str

    def __post_init__(self) -> None:
        if self.algorithm not in _ALLOWED_ALGORITHMS:
            raise KernelValueError(
                f"ContentDigest.algorithm must be one of"
                f" {sorted(_ALLOWED_ALGORITHMS)}, got {self.algorithm!r}"
            )
        if not isinstance(self.hex, str) or not _HEX_PATTERN.fullmatch(self.hex):
            raise KernelValueError(
                f"ContentDigest.hex must be 64 lowercase hex characters, got {self.hex!r}"
            )

    def __str__(self) -> str:
        """人間向けの表示。16進 64 文字だけを返す。

        正規化エンコード（D02 §9.3）はこの文字列を使わない。§9.3 が `__str__` で符号化すると
        定めるのは `UtcTime`・ID 型・`Symbol`・`CurrencyCode`・`TimeframeRef` だけであり、
        `ContentDigest` はそれ以外の dataclass として `{"algorithm": …, "hex": …}` の
        mapping に符号化される。`algorithm` を落とすと、manifest からダイジェストを再計算する
        外部ツールと結果が食い違うため、`canonical_str()` は定義しない。
        """
        return self.hex

    @classmethod
    def sha256(cls, hex_value: str) -> Self:
        """16進 64 文字から sha256 のダイジェストを作る。"""
        return cls(algorithm="sha256", hex=hex_value)


def _require_identifier(value: str, label: str) -> None:
    """識別子が空でない文字列であることを確かめる。字種の規約は各設計文書が決める。"""
    if not isinstance(value, str) or not value:
        raise KernelValueError(f"{label} must be a non-empty str, got {value!r}")


def _require_version(value: int, label: str) -> None:
    """版番号が 1 以上の整数であることを確かめる。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise KernelValueError(f"{label} must be an int, got {value!r}")
    if value < 1:
        raise KernelValueError(f"{label} must be >= 1, got {value}")


def _require_digest(value: ContentDigest, label: str) -> None:
    if not isinstance(value, ContentDigest):
        raise KernelValueError(f"{label} must be a ContentDigest, got {type(value).__name__}")


# --- 版と内容を固定する参照（D02 §9.2）--------------------------------------


@dataclass(frozen=True, slots=True)
class ContractRef:
    """部品契約の固定参照（D02 §9.2、上位設計書 §4.3.5）。"""

    component_id: str
    version: int
    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_identifier(self.component_id, "ContractRef.component_id")
        _require_version(self.version, "ContractRef.version")
        _require_digest(self.digest, "ContractRef.digest")


@dataclass(frozen=True, slots=True)
class ImplementationRef:
    """登録済み実装の ID と内容ハッシュ（D02 §9.2）。"""

    implementation_id: str
    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_identifier(self.implementation_id, "ImplementationRef.implementation_id")
        _require_digest(self.digest, "ImplementationRef.digest")


@dataclass(frozen=True, slots=True)
class PolicyRef:
    """ポリシーの不変参照（D02 §9.2）。run manifest から解決する。

    `policy_kind` の例: `research` / `risk` / `execution` / `cost` / `delay`。語彙の正本は
    D06 が持ち、`common` は自由文字列として受ける。
    """

    policy_kind: str
    policy_id: str
    version: int
    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_identifier(self.policy_kind, "PolicyRef.policy_kind")
        _require_identifier(self.policy_id, "PolicyRef.policy_id")
        _require_version(self.version, "PolicyRef.version")
        _require_digest(self.digest, "PolicyRef.digest")


@dataclass(frozen=True, slots=True)
class StrategyRef:
    """`StrategyDefinition` への参照（D02 §9.2）。"""

    strategy_id: str
    version: int
    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_identifier(self.strategy_id, "StrategyRef.strategy_id")
        _require_version(self.version, "StrategyRef.version")
        _require_digest(self.digest, "StrategyRef.digest")


@dataclass(frozen=True, slots=True)
class CompiledStrategyRef:
    """解決済み設定（探索で作る割当を含む）の識別（D02 §9.2）。"""

    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_digest(self.digest, "CompiledStrategyRef.digest")


# --- `RunId` の構成要素（D02 §9.2・§9.4、ADR-0006）--------------------------


@dataclass(frozen=True, slots=True)
class ConfigDigest:
    """解決済み run 設定の正規化内容のダイジェスト（D02 §9.2）。項目の正本は D06。"""

    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_digest(self.digest, "ConfigDigest.digest")


@dataclass(frozen=True, slots=True)
class CodeDigest:
    """実行したソースコードの内容ダイジェスト（D02 §9.2・§9.4）。

    算出は `app` が run 開始時に `from_package_dir` で行う。git commit・dirty 状態は
    算出に使わず、manifest に別途記録する。
    """

    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_digest(self.digest, "CodeDigest.digest")

    @classmethod
    def from_package_dir(cls, package_dir: Path) -> Self:
        """import されたパッケージディレクトリ配下の `.py` からダイジェストを作る。

        `package_dir` には `odyssey_fx.__file__` が解決するディレクトリ（editable install
        では `src/odyssey_fx/`）を渡す。git の作業ツリーやリポジトリのパスからは決めない
        （D02 §9.4）。
        """
        return cls(ContentDigest.sha256(canonical.code_digest_hex(package_dir)))


@dataclass(frozen=True, slots=True)
class LockDigest:
    """`uv.lock` の内容バイト列の sha256（D02 §9.2・§9.4）。"""

    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_digest(self.digest, "LockDigest.digest")

    @classmethod
    def from_lock_file(cls, lock_path: Path) -> Self:
        """リポジトリ直下の `uv.lock` からダイジェストを作る。

        `uv.lock` が `pyproject.toml` と整合しない場合に run を開始しない判断は `app` の
        責務であり、本メソッドは内容のダイジェストだけを返す（D02 §9.4）。
        """
        return cls(ContentDigest.sha256(canonical.lock_digest_hex(lock_path)))


@dataclass(frozen=True, slots=True)
class EnvDigest:
    """実行環境のダイジェスト（D02 §9.2・§9.4、ADR-0006）。

    同じ `uv.lock` でも環境が違えば別の wheel が選ばれ数値結果が変わりうるため、識別子に
    含める。入力の収集（インタプリタ・プラットフォーム・インストール済み配布物）は `app`
    が行い、`canonical.env_digest_input` で mapping に整えてからダイジェストにする。
    """

    digest: ContentDigest

    def __post_init__(self) -> None:
        _require_digest(self.digest, "EnvDigest.digest")

    @classmethod
    def from_environment(
        cls,
        *,
        python_implementation: str,
        python_version: str,
        sys_platform: str,
        machine: str,
        distributions: Mapping[str, str],
    ) -> Self:
        """環境情報の mapping からダイジェストを作る（D02 §9.4）。"""
        payload = canonical.env_digest_input(
            python_implementation=python_implementation,
            python_version=python_version,
            sys_platform=sys_platform,
            machine=machine,
            distributions=distributions,
        )
        return cls(canonical.digest(payload))


def run_id(config: ConfigDigest, code: CodeDigest, lock: LockDigest, env: EnvDigest) -> RunId:
    """完全入力から `RunId` を作る（D02 §9.3、ADR-0006）。

    `{"config": …, "code": …, "lock": …, "env": …}` の mapping（値は各ダイジェストの
    16進 64 文字）を正規化エンコードし、その sha256 を `RunId` とする。同じ完全入力による
    再実行は同じ `RunId` になる。
    """
    if not isinstance(config, ConfigDigest):
        raise KernelValueError("run_id() config must be a ConfigDigest")
    if not isinstance(code, CodeDigest):
        raise KernelValueError("run_id() code must be a CodeDigest")
    if not isinstance(lock, LockDigest):
        raise KernelValueError("run_id() lock must be a LockDigest")
    if not isinstance(env, EnvDigest):
        raise KernelValueError("run_id() env must be an EnvDigest")
    payload = {
        "code": code.digest.hex,
        "config": config.digest.hex,
        "env": env.digest.hex,
        "lock": lock.digest.hex,
    }
    return RunId(canonical.digest(payload))


# --- ID への参照（D02 §9.2）------------------------------------------------


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """データ snapshot の参照（D02 §9.2）。"""

    snapshot_id: SnapshotId

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, SnapshotId):
            raise KernelValueError("SnapshotRef.snapshot_id must be a SnapshotId")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """保存された根拠記録への参照（D02 §9.2）。"""

    evidence_id: EvidenceId

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, EvidenceId):
            raise KernelValueError("EvidenceRef.evidence_id must be an EvidenceId")
