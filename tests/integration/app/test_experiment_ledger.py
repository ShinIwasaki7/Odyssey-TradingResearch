"""`odyssey-fx experiment ledger`（D09 §11.4・§10.12.2。段階5 実装 PR 5）。

試行台帳・登録簿・束縛の記録はテストの中のリポジトリの根と成果物の基点に直接組み立てる（台帳の
行は人工のもの。実データも run も使わない。D09 §13）。

確かめること:

- 一覧は研究ポリシーの `(id, 版)` ごとのまとまり（版の昇順）で、見出しに「現行／旧版／機構確認用」
  を出す。1行が1実行で、結末の行が無ければ「結末の行なし」。まとまりの最初の行と比較の前提が
  違うセルの先頭に `*` を付ける。`--strategy` で絞れる。末尾の書きかけは1行添える。空の台帳は
  「台帳に行が無い」。終了コード 0。
- 台帳が読めない（L0〜L10）・逆照合 L11 に当たる・登録簿のファイル自体が読めないと終了コード 2。
  記録票の版参照の照合はしない（登録簿に無い版の行があっても 0）。
"""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

import pytest

from odyssey_fx.common.canonical import digest
from odyssey_fx.common.ids import ExperimentId
from odyssey_fx.common.refs import PolicyRef
from odyssey_fx.evaluation.adapters.fs_store import FileSystemExperimentStore
from odyssey_fx.evaluation.domain.experiment import ExperimentManifest
from odyssey_fx.evaluation.domain.search import (
    SearchVerdict,
    StandardPurpose,
    TrialLedgerBinding,
    TrialLedgerEntry,
)
from tests.fixtures.evaluation.trial_ledger import chain, finished, started, write_ledger

_V3 = PolicyRef(policy_kind="research", policy_id="research", version=3, digest=digest("v3"))


def _registry(repo: Path, *, with_v3: bool = True) -> None:
    path = repo / "configs/policies/research/registry.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "schema_version: 1",
        "entries:",
        f"  - {{id: research, version: 1, digest: '{digest('research').hex}'}}",
    ]
    if with_v3:
        lines.append(
            f"  - {{id: research, version: 3, digest: '{_V3.digest.hex}', purpose: STANDARD}}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _standard(name: str) -> TrialLedgerEntry:
    entry = started(name, purpose=StandardPurpose.STANDARD)
    return replace(entry, basis=replace(entry.basis, research_policy_ref=_V3))


def _ledger(repo: Path, out: Path, *args: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        from odyssey_fx.app.cli.main import main

        code = main(["experiment", "ledger", "--repo-root", str(repo), "--out", str(out), *args])
    return code, stdout.getvalue(), stderr.getvalue()


def _row(text: str, experiment: str) -> list[str]:
    line = next(item for item in text.splitlines() if item.startswith(f"| {experiment} |"))
    return [cell.strip() for cell in line.strip("|").split("|")]


def test_the_listing_groups_by_policy_version_and_marks_differing_basis_cells(
    tmp_path: Path,
) -> None:
    _registry(tmp_path)
    first = started("a")
    second = replace(started("b"), basis=replace(started("b").basis, seed=7))
    third = _standard("c")
    write_ledger(
        tmp_path,
        chain([first, second, third, finished(first, verdict=SearchVerdict.BELOW_STANDARD)]),
        tail=b'{"entry": {"eve',
    )
    code, text, stderr = _ledger(tmp_path, tmp_path / "out")
    assert code == 0, stderr
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings == [
        "## 研究ポリシー research 版 1（機構確認用）",
        "## 研究ポリシー research 版 3（現行）",
    ]
    row_a, row_b = _row(text, "exp_a"), _row(text, "exp_b")
    header = next(line for line in text.splitlines() if line.startswith("| 実験 |"))
    names = [cell.strip() for cell in header.strip("|").split("|")]
    seed = names.index("seed")
    assert row_a[names.index("判定")] == "BELOW_STANDARD"
    assert row_b[names.index("判定")] == "結末の行なし"
    assert (row_a[seed], row_b[seed]) == ("0", "*7")
    assert all(not cell.startswith("*") for cell in row_b[: names.index("research_policy_ref")])
    assert "`*` はまとまりの最初の行と比較の前提が違う項目。" in text
    assert "末尾に書きかけの行がある（数えていない。次の追記で切り詰められる）。" in text


def test_the_listing_can_be_narrowed_to_one_strategy(tmp_path: Path) -> None:
    _registry(tmp_path)
    other = replace(started("x"), strategy_id="another_strategy")
    write_ledger(tmp_path, chain([started("a"), other]))
    code, text, _ = _ledger(tmp_path, tmp_path / "out", "--strategy", "another_strategy")
    assert code == 0
    assert "| exp_x |" in text and "| exp_a |" not in text
    code, text, _ = _ledger(tmp_path, tmp_path / "out", "--strategy", "nothing")
    assert code == 0 and "台帳に行が無い" in text


def test_an_empty_ledger_lists_nothing(tmp_path: Path) -> None:
    _registry(tmp_path)
    write_ledger(tmp_path, [])
    code, text, _ = _ledger(tmp_path, tmp_path / "out")
    assert code == 0
    assert "台帳に行が無い。" in text


def test_the_listing_does_not_check_the_version_reference_against_the_registry(
    tmp_path: Path,
) -> None:
    """登録簿のファイル自体が読めれば、登録簿に無い版の行があっても一覧を出す（D09 §11.4）。"""
    _registry(tmp_path, with_v3=False)
    write_ledger(tmp_path, chain([_standard("c")]))
    code, text, stderr = _ledger(tmp_path, tmp_path / "out")
    assert code == 0, stderr
    assert "## 研究ポリシー research 版 3（旧版）" in text


def _missing(repo: Path) -> None:
    (repo / "research/trial_ledger.jsonl").unlink()


def _corrupt(repo: Path) -> None:
    write_ledger(repo, [b"not json\n"])


def _chain(repo: Path) -> None:
    write_ledger(repo, [*chain([started("x")]), *chain([started("y")], prev=digest("z"))])


def _gap(repo: Path) -> None:
    write_ledger(repo, chain([started("x", 2)]))


def _orphan(repo: Path) -> None:
    write_ledger(repo, chain([finished(started("x"))]))


def _verdict_purpose(repo: Path) -> None:
    opening = started("x")
    write_ledger(repo, chain([opening, finished(opening, verdict=SearchVerdict.MEETS_STANDARD)]))


_DEFECTS: dict[str, tuple[Callable[[Path], None], str]] = {
    "L0": (_missing, "FILE_MISSING"),
    "L2": (_corrupt, "LINE_CORRUPT"),
    "L3": (_chain, "CHAIN_BROKEN"),
    "L7": (_gap, "EXECUTION_GAP"),
    "L8": (_orphan, "ORPHAN_FINISHED"),
    "L10": (_verdict_purpose, "VERDICT_PURPOSE"),
}


@pytest.mark.parametrize("case", sorted(_DEFECTS))
def test_an_unreadable_ledger_ends_with_exit_code_2(tmp_path: Path, case: str) -> None:
    _registry(tmp_path)
    write_ledger(tmp_path, chain([started("a")]))
    damage, kind = _DEFECTS[case]
    damage(tmp_path)
    code, text, stderr = _ledger(tmp_path, tmp_path / "out")
    assert code == 2, text
    assert kind in stderr
    assert "# 試行台帳の一覧" not in text


def _store(out: Path) -> FileSystemExperimentStore:
    def unused(manifest: ExperimentManifest) -> ExperimentId:  # pragma: no cover
        raise AssertionError("not used")

    return FileSystemExperimentStore(
        root=out, experiment_name="exp_a", experiment_version=1, identity_of=unused
    )


def test_l11_a_binding_that_the_ledger_does_not_match_ends_with_exit_code_2(
    tmp_path: Path,
) -> None:
    """成果物の基点に、台帳と合わない束縛の記録（数え直し待ちの版）があれば終了コード 2。"""
    _registry(tmp_path)
    lines = chain([started("a")])
    write_ledger(tmp_path, lines)
    out = tmp_path / "out"
    store = _store(out)
    store.write_ledger_binding(
        TrialLedgerBinding(
            schema_version=1,
            experiment_id=lines[0].entry.experiment_id,
            execution=1,
            started_line_digest=lines[0].digest,
        )
    )
    assert _ledger(tmp_path, out)[0] == 0
    write_ledger(tmp_path, [])
    code, _, stderr = _ledger(tmp_path, out)
    assert code == 2
    assert "UNMATCHED_BINDING" in stderr
    assert "runs/experiments/exp_a/v1/search/ledger_execution.json" in stderr


def test_an_unreadable_registry_file_ends_with_exit_code_2(tmp_path: Path) -> None:
    write_ledger(tmp_path, chain([started("a")]))
    code, text, _ = _ledger(tmp_path, tmp_path / "out")
    assert code == 2 and "# 試行台帳の一覧" not in text
    _registry(tmp_path)
    (tmp_path / "configs/policies/research/registry.yaml").write_text(
        "entries: [\n", encoding="utf-8"
    )
    assert _ledger(tmp_path, tmp_path / "out")[0] == 2
