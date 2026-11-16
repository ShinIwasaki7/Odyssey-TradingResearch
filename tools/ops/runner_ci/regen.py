"""検査 4: 作り直しの一致（D25 §3.4 の 3、§6.3 の 4。D25-2 (a)、決定 172）。

事前登録の定型 PR で足された・変わった生成物（戦略の宣言・実験設定）を、カード（と変更の提案）から
生成の道具で作り直し、バイト単位で一致することを確かめる。

**生成の道具を呼ぶ部分は PR 7 の後に有効にする**（D25 §8 の PR 6 の行）。PR 7 が生成の道具を
入れるまでは `REGENERATOR` が `None` で、生成物を含む事前登録の定型 PR はこの検査で落ちる
（未有効の間は通さない側に倒す。本 PR の仮置き）。生成の道具が無くても確かめられる部分（注記に
書いたカード・変更の提案・生成物の SHA-256 と、生成物の先頭の生成の印）は今から当てる。

生成の道具は PR の時点の main の道具を使う（D25 §3.4 の 3）。定型 PR は `tools/`・`src/` を
変えられない（検査 3）ので、CI が取り出した merge のコミットの道具は main の道具と同じである。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Protocol

from tools.ops.runner_ci.model import (
    ChangeStatus,
    CheckInput,
    CheckResult,
    Snapshot,
    Status,
    result,
    sha256_hex,
)
from tools.ops.runner_ci.records import card_path, load
from tools.ops.runner_ci.routine import Form, Routine

NAME: Final = "作り直しの一致（カードから生成物を作り直してバイト単位で一致）"
GENERATED_PREFIXES: Final = ("configs/strategies/", "configs/experiments/")
NOT_ENABLED: Final = "生成の道具を呼ぶ部分は未有効（PR 7 で生成の道具が入った後に有効にする）"


class Regenerator(Protocol):
    """カード（と変更の提案）から生成物を作り直す道具（PR 7 が実装する）。"""

    def __call__(
        self, *, card: bytes, change: bytes | None, version: int, head: Snapshot
    ) -> Mapping[str, bytes]:
        """生成物のパス → バイト列。"""
        ...


#: PR 7 で生成の道具をここに差し込む（それまでは未有効）。
REGENERATOR: Final[Regenerator | None] = None


def header_line(card: str, card_sha256: str, generator: str) -> str:
    """生成物の先頭の生成の印（D25 §3.8 の「先頭のコメント」）。"""
    return (
        f"# generated-by: research_runner card={card} card_sha256={card_sha256}"
        f" generator={generator}"
    )


def check_regeneration(
    given: CheckInput, routine: Routine | list[str] | None, regenerator: Regenerator | None
) -> CheckResult:
    if not isinstance(routine, Routine) or routine.form is not Form.PREREG:
        return CheckResult(
            4, NAME, Status.NOT_APPLICABLE, ("事前登録の定型 PR だけに当てる", NOT_ENABLED)
        )
    card, version = routine.card, routine.version
    assert card is not None and version is not None  # noqa: S101 - 事前登録の枝は両方を持つ
    generated = {
        change.path
        for change in given.changes
        if change.status is not ChangeStatus.DELETED and change.path.startswith(GENERATED_PREFIXES)
    }
    note_path = f"research/loops/{card}/prereg_v{version}.yaml"
    raw_note = given.head.read(note_path)
    note = None if raw_note is None else load(note_path, raw_note).data
    card_bytes = given.head.read(card_path("taken", card))
    if note is None or card_bytes is None:
        return result(
            4,
            NAME,
            ["事前登録の注記かカード（taken/）が読めないので確かめられない"],
            (NOT_ENABLED,),
        )
    problems: list[str] = []
    card_sha = sha256_hex(card_bytes)
    if note.get("card_sha256") != card_sha:
        problems.append(f"{note_path}: `card_sha256` がカードのバイト列の SHA-256 と違う")
    change_bytes = None
    if version >= 2:
        change_bytes = given.head.read(f"research/loops/{card}/change_v{version}.yaml")
        if change_bytes is None or note.get("change_sha256") != sha256_hex(change_bytes):
            problems.append(f"{note_path}: `change_sha256` が変更の提案の SHA-256 と違う")
    listed = note.get("generated")
    recorded = {
        item.get("path"): item.get("sha256")
        for item in (listed if isinstance(listed, list) else [])
        if isinstance(item, dict)
    }
    if set(recorded) != generated:
        problems.append(
            f"{note_path}: `generated` の並びが、この PR で足した・変えた生成物と一致しない"
        )
    generator = note.get("generator_digest")
    for path in sorted(generated):
        data = given.head.read(path) or b""
        if recorded.get(path) not in {None, sha256_hex(data)}:
            problems.append(f"{path}: 注記の SHA-256 と違う")
        first = data.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        if not isinstance(generator, str) or first != header_line(card, card_sha, generator):
            problems.append(f"{path}: 先頭の行が生成の印（D25 §3.8）でない")
    if not generated:
        return result(4, NAME, problems, ("生成物が無い",))
    if regenerator is None:
        problems.append(f"{NOT_ENABLED}。それまで、生成物を含む事前登録の定型 PR は通さない")
        return result(4, NAME, problems)
    produced = regenerator(card=card_bytes, change=change_bytes, version=version, head=given.head)
    if set(produced) != generated:
        problems.append("作り直した生成物のパスが、この PR の生成物と一致しない")
    for path in sorted(generated & set(produced)):
        if produced[path] != given.head.read(path):
            problems.append(f"{path}: 作り直した生成物とバイト単位で一致しない")
    return result(4, NAME, problems)
