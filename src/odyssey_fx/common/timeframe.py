"""時間足の参照（D02 §6）。

長さ・整列・セッション規則を持つ定義本体は `marketdata.domain`（D03）に置き、`common` は
参照だけを持つ。固定 enum にはしない（上位設計書 §4.3.9）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Self

from odyssey_fx.common.errors import KernelValueError

__all__ = ["TimeframeRef"]

#: `TimeframeRef.id` に許す字種（D02 §6）。
_TIMEFRAME_ID_PATTERN: Final = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class TimeframeRef:
    """時間足定義への版付き参照（D02 §6）。

    `id` の例: `15m`、`1h`、`4h_ny17`、`1d_ny17`。
    """

    id: str
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not _TIMEFRAME_ID_PATTERN.match(self.id):
            raise KernelValueError(f"TimeframeRef.id must match ^[a-z0-9_]+$, got {self.id!r}")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise KernelValueError(f"TimeframeRef.version must be an int, got {self.version!r}")
        if self.version < 1:
            raise KernelValueError(f"TimeframeRef.version must be >= 1, got {self.version}")

    def __str__(self) -> str:
        return f"{self.id}@v{self.version}"

    def canonical_str(self) -> str:
        """正規化エンコードでの表現（D02 §9.3: `TimeframeRef` は `__str__`）。"""
        return str(self)

    @classmethod
    def parse(cls, text: str) -> Self:
        """`<id>@v<version>` 形式を読む。"""
        if not isinstance(text, str) or "@v" not in text:
            raise KernelValueError(f"invalid TimeframeRef literal: {text!r}")
        raw_id, _, raw_version = text.rpartition("@v")
        if not raw_version.isdigit():
            raise KernelValueError(f"invalid TimeframeRef literal: {text!r}")
        return cls(id=raw_id, version=int(raw_version))
