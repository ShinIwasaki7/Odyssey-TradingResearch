"""検証戦略 B（T02）の人工データをコマンド経由で受け入れ、書式 v2 の実験設定で run する作業場。

段階3 の受入テストはエンジンの利用口を直接呼んでいた（`t02_run.py`）。実験設定の書式が検証
戦略 B も遅延シナリオも書けなかったためである。書式 v2（D07 §18）でそれが書けるようになった
ので、本組み立ては**設定ファイル → 受入れ → 分類 → 承認 → run** を `odyssey-fx` のコマンドで
通す（D07 §17.2 の実装 PR 2 の統合テスト）。

素の足は `t02_run.raw_bars()` と同じもの（1時間足・15分足）を原 CSV に書き戻す。日足は
受入れが1時間足から集約する（D03 §5.1）ので、`t02_run` が生成器の集約で作った日足と同じ
規則で作られる。

作業場はリポジトリの根の形をまねる。書式 v2 のパスは**リポジトリの根からの相対パス**で
解決される（D07 §18.2）ので、設定を写し、`uv.lock` も写す（`--repo-root` に作業場を渡す。
`uv.lock` の内容ダイジェストは写しても変わらない）。設定は正本（`configs/`）を写すので、
書き方が変われば本組み立ても追従する。

研究ポリシーの版の登録簿（D09 §10.9）は正本を写さず、**作業場の登録簿をテストの中で作る**
（写した版 1 の1行だけを載せる）。試験用の版（例: 上限を下げた版 2）を足すテストは、
`tests.fixtures.evaluation.research_policies.append_registry_entry` で作業場の登録簿へ1行足す
（D08 §2.3・§2.4）。

**封印 partition を持つ作業場**（`build_sealed_workspace`。段階5 の受入テスト。D08 §9.5 の
「封印期間のテストの例外」、D09 §9.8 の Q2 決定）: 素の足の生成区間を `2015-01-23T22:00Z` まで
延ばし、受入れのときだけ期間境界を `SEALED_BOUNDARIES`（研究履歴は `2015-01-17T00:00Z` 未満、
封印期間は `2015-02-01T00:00Z` 未満）にする。週明け `2015-01-18T22:00Z` 以降の足が封印期間
（`LEGACY_HOLDOUT`）の partition になり、T02 の run 区間（`2015-01-16T22:00Z` まで）は研究履歴の
partition に収まる。期間境界は CLI の `data accept` を同じプロセスで呼ぶ間だけ
`composition.acceptance_service` に渡す（CLI の引数は増やさない）。実データの分類（ADR-0014 の
表）には影響しない。

`accept_variant` は同じ作業場に、指定区間の素の足だけを別の値にした原データから別の snapshot を
受け入れて承認する（段階5 の完了条件1 の (b)）。原データは作業場の原データとは別の置き場に書き、
作業場の原データを上書きしない。
"""

from __future__ import annotations

import io
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, redirect_stdout
from dataclasses import replace
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Final

from odyssey_fx.app import composition
from odyssey_fx.app.cli.main import main
from odyssey_fx.common.money import PriceOffset
from odyssey_fx.common.time import Interval, UtcTime
from odyssey_fx.marketdata.application.snapshot_access import PENDING_DIRECTORY
from odyssey_fx.marketdata.domain.access import AccessBoundaries
from odyssey_fx.marketdata.domain.bar import Bar
from odyssey_fx.marketdata.domain.series import SeriesId
from tests.fixtures.acceptance import t02_market
from tests.fixtures.acceptance.t02_run import raw_bars
from tests.fixtures.evaluation.research_policies import append_registry_entry
from tests.fixtures.strategy.strategy_b import HOURLY_SERIES, M15_SERIES
from tests.fixtures.synthetic import market

__all__ = [
    "CASE_EXPERIMENTS",
    "LAST_VALIDATION",
    "REPO_ROOT",
    "SEALED_BOUNDARIES",
    "SEALED_GENERATION_END",
    "SNAPSHOT_PLACEHOLDER",
    "T02Workspace",
    "accept_variant",
    "build_sealed_workspace",
    "build_workspace",
    "call",
    "sealed_raw_bars",
]

REPO_ROOT: Final = Path(__file__).resolve().parents[3]

#: 同梱の実験設定が既定値として置く snapshot の識別子（各ファイルの注記）。
SNAPSHOT_PLACEHOLDER: Final = "0" * 64

#: 遅延シナリオ4ケース（T02 §1.4）と、それぞれの実験設定（書式 v2）。
CASE_EXPERIMENTS: Final = {
    "none": "configs/experiments/strategy_b_t02_none.yaml",
    "d1_2s": "configs/experiments/strategy_b_t02_d1_2s.yaml",
    "d1_25h": "configs/experiments/strategy_b_t02_d1_25h.yaml",
    "d1_bar_hold": "configs/experiments/strategy_b_t02_d1_bar_hold.yaml",
}

#: 封印 partition を持つ作業場の素の足の生成区間の終わり（T02 の run 区間の終わりの1週間後）。
SEALED_GENERATION_END: Final = UtcTime.parse("2015-01-23T22:00:00Z")

#: 封印 partition を持つ作業場の受入れだけに使う期間境界（D08 §9.5 の例外。テスト用）。
#: `2015-01-17T00:00Z` 未満が研究履歴、`2015-02-01T00:00Z` 未満が封印期間。
SEALED_BOUNDARIES: Final = AccessBoundaries(
    research_until=UtcTime.parse("2015-01-17T00:00:00Z"),
    holdout_until=UtcTime.parse("2015-02-01T00:00:00Z"),
)

#: 試験用の研究ポリシー版 3 の2 fold（`search_experiments.TWO_FOLD_SPLIT`）の最後の fold の
#: 検証区間。どの fold の選定区間にも入らない（D09 §13 の完了条件1 の (b)）。
LAST_VALIDATION: Final = Interval(
    start=UtcTime.parse("2015-01-12T22:00:00Z"),
    end=UtcTime.parse("2015-01-16T22:00:00Z"),
)

#: 素の15分足を作る区間の始まり（`t02_run._M15_WINDOW` と同じ）。
_M15_START: Final = UtcTime.parse("2014-12-28T22:00:00Z")

#: 作業場へ写す設定（正本の相対パス）。
_COPIED: Final = (
    "uv.lock",
    "configs/calendars/fx_ny17_v1.yaml",
    "configs/calendars/timeframes_v1.yaml",
    "configs/datasources/legacy_merged_csv_v1.yaml",
    "configs/symbols/USDJPY.yaml",
    "configs/strategies/strategy_b_v1.yaml",
    "configs/policies/research/research_policy_v1.yaml",
    *CASE_EXPERIMENTS.values(),
)


def call(argv: list[str]) -> str:
    """コマンドを実行し、画面へ出た内容を返す。終了コードが 0 でなければ失敗させる。"""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    output = buffer.getvalue()
    assert code == 0, f"{argv} exited with {code}\n{output}"
    return output


class T02Workspace:
    """受入れと承認を済ませた作業場。"""

    def __init__(self, repo: Path, snapshot_id: str) -> None:
        self.repo = repo
        self.snapshot_id = snapshot_id

    def experiment(self, case: str) -> Path:
        """そのケースの実験設定（snapshot の識別子を承認済みのものへ書き換えたもの）。"""
        return self.repo / CASE_EXPERIMENTS[case]

    def run_argv(self, case: str, artifacts_root: Path) -> list[str]:
        """書式 v2 の `run` コマンドの引数（環境の3つは実験設定の `environment` が指す）。"""
        return [
            "run",
            "--experiment",
            str(self.experiment(case)),
            "--snapshots",
            str(self.repo / "data/snapshots"),
            "--out",
            str(artifacts_root),
            "--repo-root",
            str(self.repo),
        ]

    def run(self, case: str, artifacts_root: Path) -> str:
        """そのケースで run を1回通し、`run_id` を返す。"""
        call(self.run_argv(case, artifacts_root))
        directories = sorted(path.name for path in (artifacts_root / "runs").iterdir())
        assert len(directories) == 1, directories
        return directories[0]


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _entries(parent: Path) -> set[str]:
    return {path.name for path in parent.iterdir()} if parent.is_dir() else set()


def _write_raw(raw: Path, bars: Mapping[SeriesId, Sequence[Bar]]) -> None:
    raw.mkdir(parents=True)
    (raw / "USDJPY_1h_merged.csv").write_text(
        t02_market.csv_text(bars[HOURLY_SERIES]), encoding="utf-8"
    )
    (raw / "USDJPY_15m_merged.csv").write_text(
        t02_market.csv_text(bars[M15_SERIES]), encoding="utf-8"
    )


@contextmanager
def _accepting_with(boundaries: AccessBoundaries | None) -> Iterator[None]:
    """`data accept` を同じプロセスで呼ぶ間だけ受入れの期間境界を差し替える（D08 §9.5 の例外）。"""
    if boundaries is None:
        yield
        return
    original = composition.acceptance_service
    composition.acceptance_service = partial(original, boundaries=boundaries)
    try:
        yield
    finally:
        composition.acceptance_service = original


def _accept(
    repo: Path, raw_root: Path, *, by: str, boundaries: AccessBoundaries | None = None
) -> str:
    """`raw_root` の下の原データを受け入れ・分類・承認し、新しい snapshot の識別子を返す。"""
    configs = repo / "configs"
    snapshots = repo / "data/snapshots"
    pending_root = snapshots / PENDING_DIRECTORY
    pending_before = _entries(pending_root)
    with _accepting_with(boundaries):
        call(
            [
                "data",
                "accept",
                "--datasource",
                str(configs / "datasources/legacy_merged_csv_v1.yaml"),
                "--calendar",
                str(configs / "calendars/fx_ny17_v1.yaml"),
                "--timeframes",
                str(configs / "calendars/timeframes_v1.yaml"),
                "--symbols",
                str(configs / "symbols"),
                "--out",
                str(snapshots),
                "--repo-root",
                str(raw_root),
            ]
        )
    pending = sorted(_entries(pending_root) - pending_before)
    assert len(pending) == 1, pending
    decisions = repo / "decisions.yaml"
    decisions.write_text("schema_version: 2\ndecisions: []\n", encoding="utf-8")
    snapshots_before = _entries(snapshots)
    call(
        [
            "data",
            "classify",
            "--pending",
            pending[0],
            "--decisions",
            str(decisions),
            "--out",
            str(snapshots),
            "--timeframes",
            str(configs / "calendars/timeframes_v1.yaml"),
        ]
    )
    created = sorted(
        name
        for name in _entries(snapshots) - snapshots_before
        if (snapshots / name).is_dir() and name != PENDING_DIRECTORY
    )
    assert len(created) == 1, created
    snapshot_id = created[0]
    call(
        [
            "data",
            "approve",
            "--snapshot",
            snapshot_id,
            "--by",
            by,
            "--comment",
            "人工データ（T02 の値）",
            "--out",
            str(snapshots),
        ]
    )
    return snapshot_id


def _install(root: Path) -> Path:
    repo = root / "repo"
    for name in _COPIED:
        _copy(REPO_ROOT / name, repo / name)
    append_registry_entry(repo, "research_policy", 1)
    return repo


def _point_experiments(repo: Path, snapshot_id: str) -> None:
    for relative in CASE_EXPERIMENTS.values():
        path = repo / relative
        text = path.read_text(encoding="utf-8")
        assert SNAPSHOT_PLACEHOLDER in text, "実験設定の snapshot の既定値が変わっている"
        path.write_text(text.replace(SNAPSHOT_PLACEHOLDER, snapshot_id), encoding="utf-8")


def build_workspace(root: Path) -> T02Workspace:
    """作業場を作り、T02 の人工データを受け入れて承認する。"""
    repo = _install(root)
    _write_raw(repo / "data/raw/market", raw_bars())
    snapshot_id = _accept(repo, repo, by="integration-test")
    _point_experiments(repo, snapshot_id)
    return T02Workspace(repo=repo, snapshot_id=snapshot_id)


def _shifted(bar: Bar, shift: PriceOffset) -> Bar:
    return replace(
        bar,
        open=bar.open + shift,
        high=bar.high + shift,
        low=bar.low + shift,
        close=bar.close + shift,
    )


def sealed_raw_bars(
    *, changed: Interval | None = None, shift: str = "0.500"
) -> Mapping[SeriesId, tuple[Bar, ...]]:
    """生成区間を `SEALED_GENERATION_END` まで延ばした素の足（1時間足・15分足）。

    値は `t02_run.raw_bars()` と同じ表から作る（表の最後の行より後は同じ値が続く。`t02_market`）。
    `changed` を渡すと、足の区間がその区間に収まる足だけ4本値を `shift` だけずらす（足の不変条件
    D03 §3.3 は保たれる）。日足は受入れが1時間足から集約するので、その区間の日足も変わる。
    """
    calendar = market.calendar()
    hourly_window = Interval(start=t02_market.GENERATION_INTERVAL.start, end=SEALED_GENERATION_END)
    quarter_window = Interval(start=_M15_START, end=SEALED_GENERATION_END)
    made = {
        HOURLY_SERIES: t02_market.bars_for("1h", market.TF_1H, calendar, hourly_window),
        M15_SERIES: t02_market.bars_for("15m", market.TF_15M, calendar, quarter_window),
    }
    if changed is None:
        return made
    offset = PriceOffset(Decimal(shift))
    result: dict[SeriesId, tuple[Bar, ...]] = {}
    for series, bars in made.items():
        result[series] = tuple(
            _shifted(bar, offset)
            if changed.start.value <= bar.interval.start.value
            and bar.interval.end.value <= changed.end.value
            else bar
            for bar in bars
        )
        assert result[series] != bars, f"no {series} bar falls in {changed}"
    return result


def build_sealed_workspace(root: Path) -> T02Workspace:
    """封印 partition を持つ作業場を作る（モジュールの説明。D08 §9.5 の例外）。

    実験設定は `build_workspace` と同じく承認した snapshot を指す。
    """
    repo = _install(root)
    _write_raw(repo / "data/raw/market", sealed_raw_bars())
    snapshot_id = _accept(repo, repo, by="acceptance-test", boundaries=SEALED_BOUNDARIES)
    _point_experiments(repo, snapshot_id)
    return T02Workspace(repo=repo, snapshot_id=snapshot_id)


def accept_variant(
    workspace: T02Workspace, raw_root: Path, bars: Mapping[SeriesId, Sequence[Bar]]
) -> str:
    """作業場に別の原データ（`raw_root` の下に書く）から別の snapshot を受け入れて承認する。

    期間境界は `build_sealed_workspace` と同じ `SEALED_BOUNDARIES`。作業場の原データは変えない。
    """
    _write_raw(raw_root / "data/raw/market", bars)
    return _accept(workspace.repo, raw_root, by="acceptance-test", boundaries=SEALED_BOUNDARIES)
