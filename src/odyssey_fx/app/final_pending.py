"""同じ版の最終検証の申請が状態 B・C かを手元のファイルだけで確かめる（D09 仮置き R2-29 の (1)）。

`experiment run` が記録票の保存の前に呼ぶ（終了コード 5、理由 `FINAL_VALIDATION_PENDING`。合成の
`run_search_experiment`。仮置き R4b-9）。**関所・最終検証の規則・origin の消費の記録を読み込まない**
（受入テスト 18「`experiment run` は最終評価の許可を受け取らず、関所の最終評価を呼ばない」を保つ
ため、本モジュールは最終検証のモジュールを import しない）。見るのは同じ版のディレクトリの
`final/` の下のロック・控え・最終検証の記録と、作業木の最終検証の台帳の行の許可の識別子だけ。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

__all__ = [
    "FINAL_DIRECTORY",
    "FINAL_EVALUATION_FILE",
    "FINAL_LEDGER_PATH",
    "FINAL_LOCK_FILE",
    "PERMIT_FILE",
    "pending_final_validation",
]

#: 最終検証の台帳（リポジトリの根からの相対パス。D09 §9.10.5）。
FINAL_LEDGER_PATH: Final = Path("research") / "final_validations.jsonl"
#: 実験の版のディレクトリの下の最終検証の置き場（D09 §9.10.5）と、その中のファイル。
FINAL_DIRECTORY: Final = "final"
FINAL_LOCK_FILE: Final = "final.lock"
PERMIT_FILE: Final = "permit.json"
FINAL_EVALUATION_FILE: Final = "final_evaluation.json"


def _ledger_permits(repo_root: Path) -> frozenset[str]:
    """作業木の最終検証の台帳の行の許可の識別子（読めなければ `ValueError` の系統か `OSError`）。"""
    data = (Path(repo_root) / FINAL_LEDGER_PATH).read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("the final-validation ledger ends with a fragment")
    permits: set[str] = set()
    for raw in data.split(b"\n")[:-1] if data else ():
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("permit_id"), str):
            raise ValueError("a row of the final-validation ledger has no permit_id")
        permits.add(payload["permit_id"])
    return frozenset(permits)


def pending_final_validation(experiment_dir: Path, repo_root: Path) -> str | None:
    """同じ版の最終検証の申請が状態 B・C か（当たれば理由の文、当たらなければ `None`）。

    同じ版のディレクトリの `final/` の下の各実行について、(i) ロックがある（状態 B）、(ii) 控えが
    あり、最終検証の記録が無く、作業木の最終検証の台帳にその許可の識別子の行が無い（状態 C）、の
    どちらかに当たれば理由の文を返す（仮置き R4b-9）。控えや台帳が読めないときは当たるものとして
    扱う（R4b の仮置き。fail-closed）。控えの無い状態 C（段 4 の直後に止まった）は見つけないが、その
    申請はやり直せない（仮置き R2-22）。
    """
    base = Path(experiment_dir) / FINAL_DIRECTORY
    if not base.is_dir():
        return None
    permits: frozenset[str] | None = None
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / FINAL_LOCK_FILE).exists():
            return f"final/{entry.name}/{FINAL_LOCK_FILE} exists (state B)"
        permit = entry / PERMIT_FILE
        if not permit.is_file() or (entry / FINAL_EVALUATION_FILE).exists():
            continue
        try:
            permit_id = str(json.loads(permit.read_text(encoding="utf-8"))["permit_id"])
            if permits is None:
                permits = _ledger_permits(repo_root)
        except (OSError, ValueError, KeyError, TypeError):
            return f"final/{entry.name}/{PERMIT_FILE} or {FINAL_LEDGER_PATH} cannot be read"
        if permit_id not in permits:
            return f"final/{entry.name} waits for a resume or a close (state C)"
    return None
