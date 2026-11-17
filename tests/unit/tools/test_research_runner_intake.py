"""口ごとの取り出しの上限（人間の決定 256、D27 §4.6 と仮置き 20〜23）の試験。

人工の世界（`tests.fixtures.research_runner`。偽の git・GitHub・作業役）だけを使う。本物の run・
`claude`・`gh` は起動しない。成績の数値は扱わない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.hypothesis_cards import REPO_ROOT, card_bytes, card_data
from tests.fixtures.research_runner import Rig, make_rig
from tools.ops.hypothesis_cards.repo import DictView, ToolFailure
from tools.ops.research_runner import intake
from tools.ops.research_runner.intake import INTAKE_PATH, MouthLimits, mouth_limits
from tools.ops.research_runner.ports import IdeatorOutput
from tools.ops.research_runner.runner import Runner
from tools.ops.research_runner.state import CardState, GlobalState, StateStore, format_time


def _config(human: object = None, ai: object = None) -> bytes:
    def value(item: object) -> str:
        return "null" if item is None else str(item)

    return (
        f"schema_version: 1\nmax_cards_per_mouth:\n  human: {value(human)}\n  ai: {value(ai)}\n"
    ).encode()


def _card(
    *, mouth: str = "human", title: str, symbol: str, rejected: bool = False
) -> tuple[str, bytes]:
    data = card_data(mouth=mouth, title=title, symbol=symbol)
    if rejected:
        data["evidence_estimate"]["reaches_floor"] = "UNLIKELY"  # 棄却（T3）
    return str(data["id"]), card_bytes(data)


def _queue(rig: Rig, *cards: tuple[str, bytes]) -> list[str]:
    ids = []
    for card_id, raw in cards:
        rig.world.put_on_main({f"research/hypotheses/queue/{card_id}.yaml": raw})
        rig.clock.advance(minutes=1)
        ids.append(card_id)
    return ids


def _taken(rig: Rig) -> list[str]:
    path = rig.paths.state_dir / "global.json"
    return list(rig.state()["cards"]) if path.is_file() else []


# --- 設定の読み方 -----------------------------------------------------------------------------


def test_no_config_means_no_limit() -> None:
    assert mouth_limits(DictView({})) == MouthLimits({})


@pytest.mark.parametrize(
    ("text", "caps"),
    [
        (b"schema_version: 1\n", {}),
        (_config(), {}),
        (_config(human=1), {"human": 1}),
        (_config(human=1, ai=2), {"human": 1, "ai": 2}),
        (b"schema_version: 1\nmax_cards_per_mouth:\n  ai: 3\n", {"ai": 3}),
        (_config(human=100), {"human": 100}),
    ],
)
def test_the_limits_come_from_the_config(text: bytes, caps: dict[str, int]) -> None:
    assert mouth_limits(DictView({INTAKE_PATH: text})) == MouthLimits(caps)


@pytest.mark.parametrize(
    "text",
    [
        _config(human=0),
        _config(ai=-1),
        _config(human=101),
        _config(human=10**9),
        _config(human="true"),
        _config(ai="'1'"),
        _config(ai=1.5),
        _config(human="[1]"),
        b"schema_version: 1\nmax_cards_per_mouth:\n  people: 1\n",
        b"schema_version: 1\nmax_cards_per_mouth: null\n",
        b"schema_version: 1\nmax_cards_per_mouth: 1\n",
        b"schema_version: 1\nmax_card_per_mouth:\n  human: 1\n",
        b"schema_version: 2\nmax_cards_per_mouth:\n  human: 1\n",
        b"max_cards_per_mouth:\n  human: 1\n",
        b"schema_version: 1\nmax_cards_per_mouth: {human: 1\n",
        b"",
        b"\xff\xfe",
    ],
)
def test_a_broken_config_is_a_tool_failure_not_no_limit(text: bytes) -> None:
    with pytest.raises(ToolFailure):
        mouth_limits(DictView({INTAKE_PATH: text}))


def test_the_repository_config_has_the_values_of_the_end_to_end_check() -> None:
    """リポジトリの設定は通しの値（人間の口 1 枚・AI の口 1 枚。D27 の仮置き 23）で読める。"""
    raw = (REPO_ROOT / INTAKE_PATH).read_bytes()
    assert mouth_limits(DictView({INTAKE_PATH: raw})) == MouthLimits({"human": 1, "ai": 1})


def test_full_mouths_counts_every_taken_card() -> None:
    limits = MouthLimits({"human": 1, "ai": 2})
    assert intake.full_mouths(limits, []) == frozenset()
    assert intake.full_mouths(limits, ["human", "ai"]) == {"human"}
    assert intake.full_mouths(limits, ["human", "ai", "ai"]) == {"human", "ai"}
    assert intake.full_mouths(MouthLimits({}), ["human"] * 5) == frozenset()


# --- 取り出し（D25 §3.6 の注記） ----------------------------------------------------------------


def test_no_config_takes_cards_as_before(tmp_path: Path) -> None:
    """設定が無ければ上限なし（いまの動き）: 棄却の後に同じ口の次のカードを取り出す。"""
    rig = make_rig(tmp_path)
    first, second = _queue(
        rig,
        _card(title="first-card", symbol="GBPUSD", rejected=True),
        _card(title="second-card", symbol="AUDUSD"),
    )
    rig.step()
    rig.step()
    assert _taken(rig) == [first, second]


def test_one_card_per_mouth_and_the_third_is_not_taken(tmp_path: Path) -> None:
    """人間の口 1・AI の口 1: 人間のカードを 1 枚、次に AI のカードを 1 枚取り出し、3 枚目からは
    取り出さない。棄却したカードも数える（D27 の仮置き 21）。人間の口が上限なら AI の口へ進む。"""
    rig = make_rig(tmp_path)
    rig.world.put_on_main({INTAKE_PATH: _config(human=1, ai=1)})
    human, _, ai, _ = _queue(
        rig,
        _card(title="first-card", symbol="GBPUSD", rejected=True),
        _card(title="second-card", symbol="AUDUSD", rejected=True),
        _card(mouth="ai", title="third-card", symbol="EURUSD", rejected=True),
        _card(mouth="ai", title="fourth-card", symbol="USDJPY", rejected=True),
    )
    for _ in range(6):
        rig.step()
    assert _taken(rig) == [human, ai]
    assert rig.card(human)["phase"] == "C14" and rig.card(ai)["phase"] == "C14"
    # 棄却は 2 枚だけ（3 枚目を取り出さないので、棄却の 3 連続の止まりも出ない）。
    assert rig.state()["reasons"] == []


def test_raising_the_limit_takes_the_next_card_of_that_mouth(tmp_path: Path) -> None:
    """入口に届かずに終えたカードの代わり（D27 の仮置き 17）: 人間がその口の上限を 1 つ上げる
    （設定の PR の merge）と、次の 1 歩で同じ口の次のカードを取り出す。"""
    rig = make_rig(tmp_path)
    rig.world.put_on_main({INTAKE_PATH: _config(human=1, ai=1)})
    first, second = _queue(
        rig,
        _card(title="first-card", symbol="GBPUSD", rejected=True),
        _card(title="second-card", symbol="AUDUSD"),
    )
    for _ in range(3):
        rig.step()
    assert _taken(rig) == [first]
    rig.world.put_on_main({INTAKE_PATH: _config(human=2, ai=1)})
    rig.step()
    assert _taken(rig) == [first, second]


class _Crash(BaseException):
    """実行器のプロセスが途中で止まった（停電・強制終了の代わり）。"""


@pytest.mark.parametrize("limited", [True, False])
def test_a_restart_after_a_crash_does_not_take_beyond_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limited: bool
) -> None:
    """取り出した直後（検査の途中）に実行器が止まり、起動し直しても、数は状態から数えるので
    上限を超えて取り出さない（上限なしなら、同じ流れで次のカードを取り出す: 対照）。"""
    rig = make_rig(tmp_path)
    if limited:
        rig.world.put_on_main({INTAKE_PATH: _config(human=1)})
    first, second = _queue(
        rig,
        _card(title="first-card", symbol="GBPUSD", rejected=True),
        _card(title="second-card", symbol="AUDUSD"),
    )

    def crash(self: Runner, ctx: Any, card: CardState) -> None:
        raise _Crash

    monkeypatch.setattr(Runner, "_c1_screen", crash)
    with pytest.raises(_Crash):
        rig.step()
    monkeypatch.undo()
    assert _taken(rig) == [first]
    rig.system.boot = "boot-2"  # 起動し直した
    for _ in range(4):
        rig.step()
    assert rig.card(first)["phase"] == "C14"
    assert _taken(rig) == ([first] if limited else [first, second])


@pytest.mark.parametrize(
    "text",
    [
        _config(human=0),
        _config(human=-1),
        _config(human=1000),
        _config(ai="yes"),
        b"schema_version: 1\nmax_cards_per_mouth: [\n",
    ],
)
def test_a_broken_config_neither_takes_nor_ideates(tmp_path: Path, text: bytes) -> None:
    """壊れた設定・0・負・大きすぎる値: 上限なしに倒さず、取り出しも発想役の起動もしない。止まりの
    issue は作らない（`workers.yaml` の前例）。直れば次の 1 歩で取り出す。"""
    rig = make_rig(tmp_path)
    rig.world.put_on_main({INTAKE_PATH: text})
    rig.workers.add("ideator", IdeatorOutput((), ()))
    notes: list[str] = []
    for _ in range(3):
        notes.extend(rig.step())
    assert _taken(rig) == []
    assert rig.workers.launched == []
    assert any(INTAKE_PATH in note for note in notes)
    assert not [issue for issue in rig.world.issues.values() if issue.label == "runner-stop"]
    (card_id,) = _queue(rig, _card(title="first-card", symbol="GBPUSD"))
    rig.step()
    assert _taken(rig) == []  # まだ壊れている
    rig.world.put_on_main({INTAKE_PATH: _config(human=1, ai=1)})
    rig.step()
    assert _taken(rig) == [card_id]


# --- 発想役（D25 §3.7 の注記）と QUEUE_EMPTY（D25 §4.6 の注記） -----------------------------------


def _ai_card_done(rig: Rig, ideator: dict[str, Any]) -> str:
    """AI の口のカード 1 枚を取り出し終えた（C14）状態を置く（待ち行列は空）。"""
    card_id, raw = _card(mouth="ai", title="ai-card", symbol="EURUSD")
    rig.world.put_on_main({f"research/hypotheses/done/{card_id}.yaml": raw})
    store = StateStore(rig.paths.state_dir)
    store.save_card(
        CardState(
            card_id=card_id,
            mouth="ai",
            phase="C14",
            taken_at=format_time(rig.clock.now()),
            outcome="GATE_FAILED",
        )
    )
    store.save_global(
        GlobalState(
            boot_id="boot-1",
            cards=[card_id],
            weekly_last_week="2026-11-16",
            ideator=ideator,
        )
    )
    return card_id


@pytest.mark.parametrize("trigger", ["after_done", "queue_empty"])
def test_the_ideator_is_not_launched_while_the_ai_mouth_is_full(
    tmp_path: Path, trigger: str
) -> None:
    """AI の口が上限に達している間は、`done/` の後の 1 回も、待ち行列が空のときの 1 回も発想役を
    起動せず、`QUEUE_EMPTY` で止めない（D27 の仮置き 22）。上限を上げれば、残したきっかけで
    起動する。"""
    rig = make_rig(tmp_path)
    rig.world.put_on_main({INTAKE_PATH: _config(human=1, ai=1)})
    _ai_card_done(rig, {"after_done": 1} if trigger == "after_done" else {})
    rig.workers.add("ideator", IdeatorOutput((), ()))
    for _ in range(3):
        rig.step()
    assert rig.workers.launched == []
    assert rig.state()["reasons"] == []
    assert not [issue for issue in rig.world.issues.values() if issue.label == "runner-stop"]
    rig.world.put_on_main({INTAKE_PATH: _config(human=1, ai=2)})
    rig.step()
    assert [request.role for request in rig.workers.launched] == ["ideator"]


def test_without_a_config_the_same_state_launches_the_ideator(tmp_path: Path) -> None:
    """対照: 設定が無ければ、同じ状態で発想役を起動し、書けなければ `QUEUE_EMPTY` で止まる
    （いまの動き）。"""
    rig = make_rig(tmp_path)
    _ai_card_done(rig, {})
    rig.workers.add("ideator", IdeatorOutput((), ()))
    rig.step()
    rig.step()
    assert [request.role for request in rig.workers.launched] == ["ideator"]
    assert rig.world.issue_for("QUEUE_EMPTY")


@pytest.mark.parametrize("config", ["limited", "none", "broken"])
def test_an_ideator_finishing_after_the_ai_mouth_filled_does_not_stop(
    tmp_path: Path, config: str
) -> None:
    """走っている間に AI の口が上限に達した発想役が何も書かずに終わっても、`QUEUE_EMPTY` で止めない
    （上限なしなら止まる: 対照）。設定が壊れていても終わりは読み（カードの作業役を待たせない）、
    止まりを足さない。"""
    rig = make_rig(tmp_path)
    if config == "limited":
        rig.world.put_on_main({INTAKE_PATH: _config(ai=1)})
    elif config == "broken":
        rig.world.put_on_main({INTAKE_PATH: _config(ai=0)})
    running = {"role": "ideator", "attempt": 1, "started_at": format_time(rig.clock.now())}
    _ai_card_done(rig, {"running": running, "trigger": "queue_empty", "queue_empty_used": True})
    rig.workers.add("ideator", IdeatorOutput((), ()))
    rig.step()
    reasons = [item["reason"] for item in rig.state()["reasons"]]
    assert reasons == (["QUEUE_EMPTY"] if config == "none" else [])
    assert "running" not in rig.state()["ideator"]
