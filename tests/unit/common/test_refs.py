"""`odyssey_fx.common.refs` の単体テスト（D02 §9.1・§9.2・§9.4・§11）。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from odyssey_fx.common import canonical
from odyssey_fx.common.errors import KernelValueError
from odyssey_fx.common.ids import EvidenceId, OrderId, RunId, SnapshotId
from odyssey_fx.common.refs import (
    CodeDigest,
    CompiledStrategyRef,
    ConfigDigest,
    ContentDigest,
    ContractRef,
    EnvDigest,
    EvidenceRef,
    ImplementationRef,
    LockDigest,
    PolicyRef,
    SnapshotRef,
    StrategyRef,
    run_id,
)

HEX = "a" * 64
DIGEST = ContentDigest.sha256(HEX)


# --- ContentDigest ----------------------------------------------------------


def test_content_digest_accepts_lowercase_sha256_hex() -> None:
    assert str(DIGEST) == HEX
    assert DIGEST.algorithm == "sha256"


@pytest.mark.parametrize("hex_value", ["A" * 64, "a" * 63, "a" * 65, "z" * 64, ""])
def test_content_digest_rejects_malformed_hex(hex_value: str) -> None:
    with pytest.raises(KernelValueError, match="64 lowercase hex"):
        ContentDigest.sha256(hex_value)


def test_content_digest_rejects_other_algorithms() -> None:
    with pytest.raises(KernelValueError, match="algorithm"):
        ContentDigest(algorithm="md5", hex=HEX)


# --- 版付き参照 -------------------------------------------------------------


def test_contract_ref_holds_component_version_and_digest() -> None:
    ref = ContractRef(component_id="ema", version=2, digest=DIGEST)
    assert (ref.component_id, ref.version, ref.digest) == ("ema", 2, DIGEST)


@pytest.mark.parametrize("version", [0, -1])
def test_version_must_be_at_least_one(version: int) -> None:
    with pytest.raises(KernelValueError, match=">= 1"):
        ContractRef(component_id="ema", version=version, digest=DIGEST)


def test_identifiers_must_not_be_empty() -> None:
    with pytest.raises(KernelValueError, match="non-empty"):
        ContractRef(component_id="", version=1, digest=DIGEST)
    with pytest.raises(KernelValueError, match="non-empty"):
        ImplementationRef(implementation_id="", digest=DIGEST)
    with pytest.raises(KernelValueError, match="non-empty"):
        PolicyRef(policy_kind="", policy_id="p", version=1, digest=DIGEST)
    with pytest.raises(KernelValueError, match="non-empty"):
        StrategyRef(strategy_id="", version=1, digest=DIGEST)


def test_refs_require_a_content_digest() -> None:
    with pytest.raises(KernelValueError, match="ContentDigest"):
        ContractRef(component_id="ema", version=1, digest=HEX)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match="ContentDigest"):
        CompiledStrategyRef(digest=HEX)  # type: ignore[arg-type]


def test_policy_ref_accepts_the_documented_kinds() -> None:
    for kind in ("research", "risk", "execution", "cost", "delay"):
        assert PolicyRef(kind, "default", 1, DIGEST).policy_kind == kind


# --- RunId の構成（ADR-0006）-----------------------------------------------


def _digests(suffix: str = "a") -> tuple[ConfigDigest, CodeDigest, LockDigest, EnvDigest]:
    return (
        ConfigDigest(ContentDigest.sha256(suffix * 64)),
        CodeDigest(ContentDigest.sha256("b" * 64)),
        LockDigest(ContentDigest.sha256("c" * 64)),
        EnvDigest(ContentDigest.sha256("d" * 64)),
    )


def test_run_id_matches_the_documented_mapping() -> None:
    config, code, lock, env = _digests()
    expected = canonical.digest(
        {
            "code": code.digest.hex,
            "config": config.digest.hex,
            "env": env.digest.hex,
            "lock": lock.digest.hex,
        }
    )
    assert run_id(config, code, lock, env) == RunId(expected)


def test_run_id_is_stable_for_the_same_complete_input() -> None:
    assert run_id(*_digests()) == run_id(*_digests())


def test_run_id_changes_when_any_component_changes() -> None:
    baseline = run_id(*_digests("a"))
    assert run_id(*_digests("e")) != baseline

    config, code, lock, env = _digests()
    other_code = CodeDigest(ContentDigest.sha256("f" * 64))
    assert run_id(config, other_code, lock, env) != baseline


def test_run_id_rejects_swapped_argument_types() -> None:
    config, code, lock, env = _digests()
    with pytest.raises(KernelValueError, match="config must be a ConfigDigest"):
        run_id(code, code, lock, env)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match="code must be a CodeDigest"):
        run_id(config, config, lock, env)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match="lock must be a LockDigest"):
        run_id(config, code, env, env)  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match="env must be an EnvDigest"):
        run_id(config, code, lock, lock)  # type: ignore[arg-type]


# --- CodeDigest / LockDigest / EnvDigest の算出（D02 §9.4）-----------------


def test_code_digest_from_the_imported_package_directory() -> None:
    import odyssey_fx

    assert odyssey_fx.__file__ is not None
    package_dir = Path(odyssey_fx.__file__).parent
    digest = CodeDigest.from_package_dir(package_dir)
    assert digest.digest.hex == canonical.code_digest_hex(package_dir)
    assert digest == CodeDigest.from_package_dir(package_dir)


def test_lock_digest_from_a_lock_file(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b"version = 1\n")
    assert LockDigest.from_lock_file(lock).digest.hex == (
        hashlib.sha256(b"version = 1\n").hexdigest()
    )


def test_env_digest_from_environment_is_order_independent() -> None:
    kwargs = {
        "python_implementation": "CPython",
        "python_version": "3.12.13",
        "sys_platform": "darwin",
        "machine": "arm64",
    }
    first = EnvDigest.from_environment(distributions={"ruff": "0.14", "mypy": "1.18"}, **kwargs)
    second = EnvDigest.from_environment(distributions={"mypy": "1.18", "ruff": "0.14"}, **kwargs)
    assert first == second

    other = EnvDigest.from_environment(distributions={"ruff": "0.15", "mypy": "1.18"}, **kwargs)
    assert other != first


# --- ID への参照 ------------------------------------------------------------


def test_snapshot_and_evidence_refs_require_their_id_types() -> None:
    assert SnapshotRef(SnapshotId(DIGEST)).snapshot_id == SnapshotId(DIGEST)
    assert EvidenceRef(EvidenceId(3)).evidence_id == EvidenceId(3)
    with pytest.raises(KernelValueError, match="SnapshotId"):
        SnapshotRef(RunId(DIGEST))  # type: ignore[arg-type]
    with pytest.raises(KernelValueError, match="EvidenceId"):
        EvidenceRef(OrderId(3))  # type: ignore[arg-type]
