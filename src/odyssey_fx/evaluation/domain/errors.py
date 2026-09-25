"""evaluation の構造エラー（D01 §8）。

本パッケージの基底例外は `EvaluationError` であり、`marketdata` と同じく共通カーネルの
`KernelValueError`（D02 §10）を継承して「構造エラーは ValueError 系」という扱いを揃える。

- `ArtifactAlreadyExists`: 書き出し先の成果物ディレクトリ（`runs/<run_id>/`、
  `runs/<run_id>/eval/<run_evaluation_id>/`）が既にある（D06 §9.1、D07 §8.2。成果物の
  書き込みを「存在すれば失敗」に統一する規則 R4）。
"""

from __future__ import annotations

from odyssey_fx.common.errors import KernelValueError

__all__ = ["ArtifactAlreadyExists", "EvaluationError"]


class EvaluationError(KernelValueError):
    """`evaluation` の構造エラーの基底（D01 §8）。"""


class ArtifactAlreadyExists(EvaluationError):
    """書き出し先の成果物ディレクトリが既にある（D06 §9.1、D07 §8.2、R4）。

    成果物の書き込みは「存在すれば、何も書かずに失敗する」。置換を明示の指示で許すのは
    run の成果物（`runs/<run_id>/`）だけで（ADR-0006）、評価の成果物は置換しない。
    """
