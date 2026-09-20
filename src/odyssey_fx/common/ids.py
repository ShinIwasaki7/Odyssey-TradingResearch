"""用途別の ID 型と決定論的な採番（D02 §7、ADR-0006）。

ID は2系統ある。

- **ダイジェスト系**（`RunId`・`SnapshotId`・`ExperimentId`）: 内容のダイジェストそのもの。
  同じ内容なら何度作っても同じ値になる。`__str__` は 16進 64 文字。
- **連番系**（`OrderId` 等、種別コードを持つもの）: run 内で 1 から単調増加する整数。
  `__str__` は `f"{KIND}:{seq:08d}"`（例: `ORD:00000042`）。run 内でのみ一意なので、
  永続参照は `(RunId, ID)` の組にする。

採番は `IdAllocator`（D02 §7.3）を通す。これは `common` で唯一の可変オブジェクトであり、
値ではなく実行コンテキストに属する（D02 §1 の例外）。

`refs` との循環 import を避けるため、`ContentDigest` の実行時検査は遅延 import で行う。
モジュール本体は `errors` と `canonical` にしか依存しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, TypeVar

from odyssey_fx.common.errors import KernelValueError

if TYPE_CHECKING:
    from odyssey_fx.common.refs import ContentDigest

__all__ = [
    "AllocationId",
    "AttemptId",
    "DigestId",
    "EvaluationId",
    "EventId",
    "EvidenceId",
    "ExperimentId",
    "FillId",
    "IdAllocator",
    "OpportunityId",
    "OrderId",
    "OutputId",
    "PositionId",
    "RequestId",
    "ReservationId",
    "RunId",
    "SequentialId",
    "SnapshotId",
    "short_digest",
]

#: 連番 ID の文字列形式（D02 §7.2、承認事項3）。
_SEQUENTIAL_PATTERN: Final = re.compile(r"^(?P<kind>[A-Z]+):(?P<seq>\d{8,})$")

#: 連番 ID の桁数（`ORD:00000042`）。9 桁以上になった場合は桁を伸ばす。
_SEQUENCE_WIDTH: Final = 8

#: 短縮表示に使う先頭桁数（D02 §7.2）。識別には使わない。
_SHORT_DIGEST_LENGTH: Final = 12


@dataclass(frozen=True, slots=True)
class DigestId:
    """内容のダイジェストで identity が決まる ID の基底（D02 §7.2）。

    `__str__` は 16進 64 文字。短縮表示は `short_digest()` で行い、識別には使わない。
    """

    digest: ContentDigest

    def __post_init__(self) -> None:
        # `refs` → `ids` の import 方向を保つため、ここで遅延 import する。
        from odyssey_fx.common.refs import ContentDigest as _ContentDigest

        if not isinstance(self.digest, _ContentDigest):
            raise KernelValueError(
                f"{type(self).__name__}.digest must be a ContentDigest,"
                f" got {type(self.digest).__name__}"
            )

    @property
    def hex(self) -> str:
        """ダイジェストの 16進 64 文字。"""
        return self.digest.hex

    def __str__(self) -> str:
        return self.digest.hex

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3: ID 型は `__str__`）。"""
        return str(self)


@dataclass(frozen=True, slots=True)
class RunId(DigestId):
    """完全入力（設定・コード・lock・環境）の論理識別（D02 §7.1、ADR-0006）。

    物理的な1回の実行を指す ID ではない。同じ完全入力なら何度実行しても同じ値になる。
    構成は `digest(ConfigDigest, CodeDigest, LockDigest, EnvDigest)` で、組み立ては
    `refs.run_id` が行う。git commit・dirty 状態・Python バージョンは manifest に記録し、
    識別子には含めない。
    """


@dataclass(frozen=True, slots=True)
class SnapshotId(DigestId):
    """snapshot manifest のダイジェスト（D02 §7.1）。採番は `marketdata`（D03）。"""


@dataclass(frozen=True, slots=True)
class ExperimentId(DigestId):
    """実験 spec のダイジェスト（D02 §7.1）。採番は `evaluation`（D07）。"""


def short_digest(value: DigestId, length: int = _SHORT_DIGEST_LENGTH) -> str:
    """ダイジェスト ID の短縮表示（D02 §7.2）。表示専用で、識別には使わない。"""
    if not isinstance(value, DigestId):
        raise KernelValueError("short_digest requires a DigestId")
    if length < 1 or length > len(value.hex):
        raise KernelValueError(f"short_digest length must be in 1..{len(value.hex)}, got {length}")
    return value.hex[:length]


@dataclass(frozen=True, slots=True)
class SequentialId:
    """run 内で 1 から単調増加する連番 ID の基底（D02 §7.2）。

    `KIND` は型ごとのクラス定数（種別コード）。`__str__` は `KIND:00000042` の形式で、
    `parse` は種別が一致しない文字列を拒否する。連番は run 内でのみ一意なので、永続参照は
    `(RunId, ID)` の組にする（各記録は `run_id` を別フィールドで持つ）。
    """

    #: 種別コード（D02 §7.1 の表）。基底は種別を持たないので空文字にする。
    KIND: ClassVar[str] = ""

    seq: int

    def __post_init__(self) -> None:
        if not type(self).KIND:
            raise KernelValueError(f"{type(self).__name__} must define a KIND")
        if isinstance(self.seq, bool) or not isinstance(self.seq, int):
            raise KernelValueError(f"{type(self).__name__}.seq must be an int, got {self.seq!r}")
        if self.seq < 1:
            raise KernelValueError(f"{type(self).__name__}.seq must be >= 1, got {self.seq}")

    def __str__(self) -> str:
        return f"{type(self).KIND}:{self.seq:0{_SEQUENCE_WIDTH}d}"

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3: ID 型は `__str__`）。"""
        return str(self)

    @classmethod
    def parse(cls, text: str) -> SequentialId:
        """`KIND:00000042` 形式を読む。種別が一致しない文字列は拒否する（D02 §7.2）。"""
        if not isinstance(text, str):
            raise KernelValueError(f"invalid {cls.__name__} literal: {text!r}")
        match = _SEQUENTIAL_PATTERN.match(text)
        if match is None:
            raise KernelValueError(f"invalid {cls.__name__} literal: {text!r}")
        kind = match.group("kind")
        if kind != cls.KIND:
            raise KernelValueError(
                f"{cls.__name__} expects kind {cls.KIND!r}, got {kind!r} in {text!r}"
            )
        return cls(seq=int(match.group("seq")))

    def __lt__(self, other: SequentialId) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.seq < other.seq

    def __le__(self, other: SequentialId) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.seq <= other.seq

    def __gt__(self, other: SequentialId) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.seq > other.seq

    def __ge__(self, other: SequentialId) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        return self.seq >= other.seq


@dataclass(frozen=True, slots=True)
class EvaluationId(SequentialId):
    """部品の1回の評価（D02 §7.1）。採番は戦略ランタイム。"""

    KIND: ClassVar[str] = "EVAL"


@dataclass(frozen=True, slots=True)
class RequestId(SequentialId):
    """評価要求（待機・追い越しの単位、D02 §7.1）。採番は戦略ランタイム。"""

    KIND: ClassVar[str] = "REQ"


@dataclass(frozen=True, slots=True)
class OutputId(SequentialId):
    """`OutputRecord`（D02 §7.1）。採番は戦略ランタイム。"""

    KIND: ClassVar[str] = "OUT"


@dataclass(frozen=True, slots=True)
class OpportunityId(SequentialId):
    """取引機会（D02 §7.1）。採番は戦略ランタイム。"""

    KIND: ClassVar[str] = "OPP"


@dataclass(frozen=True, slots=True)
class AttemptId(SequentialId):
    """発注試行（D02 §7.1）。採番は `backtest.admission`。"""

    KIND: ClassVar[str] = "ATT"


@dataclass(frozen=True, slots=True)
class OrderId(SequentialId):
    """受付済み注文（D02 §7.1）。採番は `backtest.admission`。"""

    KIND: ClassVar[str] = "ORD"


@dataclass(frozen=True, slots=True)
class FillId(SequentialId):
    """約定（D02 §7.1）。採番は `backtest.execution`。"""

    KIND: ClassVar[str] = "FIL"


@dataclass(frozen=True, slots=True)
class PositionId(SequentialId):
    """建玉（D02 §7.1）。採番は `backtest.portfolio`。"""

    KIND: ClassVar[str] = "POS"


@dataclass(frozen=True, slots=True)
class ReservationId(SequentialId):
    """リスク予約（D02 §7.1）。採番は `backtest.admission`。"""

    KIND: ClassVar[str] = "RSV"


@dataclass(frozen=True, slots=True)
class AllocationId(SequentialId):
    """建玉リスク割当（D02 §7.1）。採番は `backtest.portfolio`。"""

    KIND: ClassVar[str] = "ALC"


@dataclass(frozen=True, slots=True)
class EventId(SequentialId):
    """状態遷移イベント（D02 §7.1）。再配送でも同じ値を使う。採番は `backtest.engine`。"""

    KIND: ClassVar[str] = "EVT"


@dataclass(frozen=True, slots=True)
class EvidenceId(SequentialId):
    """根拠記録（D02 §7.1）。採番は `backtest.trace`。"""

    KIND: ClassVar[str] = "EVD"


_SequentialIdT = TypeVar("_SequentialIdT", bound=SequentialId)


class IdAllocator:
    """run 内の連番 ID 採番器（D02 §7.3）。

    `common` で唯一の可変オブジェクトであり、値ではなく実行コンテキストに属する
    （D02 §1 の例外）。`backtest.engine` が1 run に1つ生成して各層へ渡し、記録・ダイジェスト
    の対象には含めない。採番順は処理順（`ProcessingPoint` の順）に一致させる。同一入力の
    再実行で同じ ID 列になることを再現性テストで検証する。
    """

    __slots__ = ("_counters", "_run_id")

    def __init__(self, run_id: RunId) -> None:
        if not isinstance(run_id, RunId):
            raise KernelValueError("IdAllocator requires a RunId")
        self._run_id = run_id
        self._counters: dict[str, int] = {}

    @property
    def run_id(self) -> RunId:
        """この採番器が属する run の識別子。"""
        return self._run_id

    def next(self, id_type: type[_SequentialIdT]) -> _SequentialIdT:
        """種別ごとに 1 から単調増加する次の ID を返す（D02 §7.3）。"""
        if not (isinstance(id_type, type) and issubclass(id_type, SequentialId)):
            raise KernelValueError(
                f"IdAllocator.next requires a SequentialId type, got {id_type!r}"
            )
        kind = id_type.KIND
        if not kind:
            raise KernelValueError(f"{id_type.__name__} must define a KIND")
        nxt = self._counters.get(kind, 0) + 1
        self._counters[kind] = nxt
        return id_type(seq=nxt)

    def snapshot(self) -> dict[str, int]:
        """各種別の最終値（D02 §7.3）。run manifest と trace に保存する。

        呼び出し側が書き換えても採番器の状態に影響しないよう、複製を返す。
        """
        return dict(self._counters)
