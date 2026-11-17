"""CI の検査 4（作り直しの一致。PR 6 の `tools.ops.runner_ci`）に仮説カードの生成の道具を差し込んだ
後の通しの試験（D25 §3.4 の 3・§6.3 の 4、決定 172。PR 6 の仮置き 11 を解いた）。

人工のカードから本物の生成の道具で事前登録を作り、事前登録の定型 PR の入力（基点と先頭の時点）を
組み立てて、PR 6 の 8 項目の検査を `REGENERATOR`（= `regenerate`）で当てる。生成物を 1 バイト
変えると検査 4 が落ちる。成績も snapshot も読まない。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from tests.fixtures.hypothesis_cards import REPO_ROOT, card_bytes, card_data
from tests.fixtures.runner_ci import ledgers, make_input
from tools.ops.hypothesis_cards.generate import PreregChecks, generate, prereg_note
from tools.ops.hypothesis_cards.repo import DictView
from tools.ops.runner_ci import regen
from tools.ops.runner_ci.cli import run_checks
from tools.ops.runner_ci.model import CheckInput, Status

CARD_ID = "a-20261120-dual-ema"
QUEUE = f"research/hypotheses/queue/{CARD_ID}.yaml"
TAKEN = f"research/hypotheses/taken/{CARD_ID}.yaml"
#: 生成の道具が読む設定（登録簿・研究ポリシー・型板）。
_INPUTS = (
    "configs/policies/research/registry.yaml",
    "configs/policies/research/research_policy_v5.yaml",
    "configs/runner/experiment_templates/GBPUSD.yaml",
)


def _prereg_pr(tmp_path: Path, *, tamper: bool = False) -> CheckInput:
    raw = card_bytes(card_data(mouth="ai"))
    base = dict(ledgers(tmp_path))
    base.update({path: (REPO_ROOT / path).read_bytes() for path in _INPUTS})
    before = dict(base, **{QUEUE: raw})
    prereg = generate(DictView(before), card=raw, version=1)
    files = dict(prereg.files)
    if tamper:
        files[prereg.experiment_path] += b"# hand edit\n"
    note = prereg_note(prereg, PreregChecks(4, 0, 1, 1))
    if tamper:
        # 注記の SHA-256 は手で直した後の生成物に合わせる（作り直しの一致だけが落ちるように）。

        for path, body in files.items():
            note = note.replace(
                hashlib.sha256(prereg.files[path]).hexdigest().encode(),
                hashlib.sha256(body).hexdigest().encode(),
            )
    head = dict(base, **{TAKEN: raw, **files, f"research/loops/{CARD_ID}/prereg_v1.yaml": note})
    return make_input(
        before,
        head,
        branch=f"runner/prereg-{CARD_ID}-v1",
        title=f"[runner] 事前登録 {CARD_ID} v1",
        kind="事前登録",
        card_line=CARD_ID,
        experiment=prereg.experiment_id,
        auto_merge=True,
    )


def test_the_regenerator_is_plugged_in() -> None:
    assert regen.REGENERATOR is not None


def test_a_generated_preregistration_passes_every_routine_check(tmp_path: Path) -> None:
    results = run_checks(_prereg_pr(tmp_path), regen.REGENERATOR)
    failed = {check.number: check.messages for check in results if check.status is Status.FAIL}
    assert failed == {}
    by_number = {check.number: check for check in results}
    assert by_number[4].status is Status.PASS


def test_a_hand_edited_generated_file_fails_the_regeneration_check(tmp_path: Path) -> None:
    results = run_checks(_prereg_pr(tmp_path, tamper=True), regen.REGENERATOR)
    by_number = {check.number: check for check in results}
    assert by_number[4].status is Status.FAIL
    assert any("バイト単位で一致しない" in message for message in by_number[4].messages)
