"""補充の後の「残存欠落と実行可能な連続期間」の報告を作る（D03 §14.14 の段 10・11、§14.15）。

報告の置き場は ``docs/data/research_history_gaps.md``（RF-9 の決定。新しい snapshot の内容に
書き直し、旧 snapshot の数字は比較の表と旧 snapshot の CSV に残す）。本スクリプトは、その書き
直しの材料になる報告の本文（Markdown）と、残存欠落の区間ごとの CSV を作る。文書そのものは人が
書き直す（本スクリプトは文書を上書きしない）。

報告の規則は D03 v1.17 §14.15（2026-10-01 の人間の決定）:

- **入力の限定**: 正式な報告の入力は、分類と確定を済ませ承認された新 snapshot に限る。承認されて
  いない snapshot からの出力は、表題とファイル名に「下書き」と明示する。
- **残存欠落 1 件ごとに分けて示す**: (a) 現在の状態（理由の語彙）、(b) 取得・検証を試みた履歴
  （関係する計画と結果を全件。上書きしない）、(c) 休場の候補の印（理由とは別の列）、(d) 根拠と
  なる計画の識別子。
- **前後関係は参照関係で決める**: 記録 X が記録 Y より前とみなすのは、X の補充分が Y の入力
  snapshot に（重ねた補充を辿って）含まれるときだけ。補充分の数や識別子の並びで 1 つを選ばない。
  比較できない記録が残れば併記する。
- **自動収集**: 補充の置き場から、対象 snapshot とその前の snapshot を入力にした計画と補充分を
  すべて集める。置き場の中に読めないものがあって網羅性を確かめられなければ、どの記録も無い足を
  「対象だが計画・試行されていない」とせず「理由未確定」とする。
- **件数の表示**: 理由ごとに件数の列を分け、複数の理由を含む区間を 1 つの理由として数えない。

読むもの（戦略の成績は読まない。D03 §14.2 の 7）: snapshot の ``manifest.json``、補充分の
``refill_manifest.json`` と ``validation.json``、作業ディレクトリの ``plan.json`` と
``journal.jsonl``。使う前に記録から導ける識別子とダイジェストを計算し直す（D03 §14.11.1 の
W3・W5。``plan_id``・``refill_id``・``validation.json`` の sha256・取得記録の行のダイジェスト）。

出力（報告と CSV）は「存在すれば失敗」: 出力先にどちらかが既にあれば、どちらも書かずに止める。

実行はリポジトリの根で ``uv run python -m tools.ops.refill_report …``（``tools`` パッケージとして
import するため、ファイルのパスを直接渡す形では動かない）。識別子とダイジェストの計算には
本体の正規化エンコード（``odyssey_fx.common.canonical``）を使う。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from odyssey_fx.app.composition import snapshot_store
from odyssey_fx.common import canonical
from tools.ops import research_history_gaps as rhg

# --- 語彙 ----------------------------------------------------------------------------

#: 残存欠落の足の「現在の状態」（D03 §14.15 の 3。CSV の値）。日本語の意味は ``STATE_LABELS``。
NOT_FETCHED = "NOT_FETCHED"
PROVIDER_NO_TICKS = "PROVIDER_NO_TICKS"
UNRECONCILED = "UNRECONCILED"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
VALIDATION_REJECTED = "VALIDATION_REJECTED"
NOT_PLANNED = "NOT_PLANNED"
UNDETERMINED = "UNDETERMINED"

STATE_LABELS: dict[str, str] = {
    NOT_FETCHED: "取得できなかった（HTTP 404 を含む）",
    PROVIDER_NO_TICKS: "提供元にも tick が無い",
    UNRECONCILED: "未照合（判断待ち）",
    OUT_OF_SCOPE: "補充の対象外（研究履歴区分の外）",
    VALIDATION_REJECTED: "検証で不合格になり補充しなかった",
    NOT_PLANNED: "対象だが計画・試行されていない",
    UNDETERMINED: "理由未確定",
}
STATE_ORDER: tuple[str, ...] = tuple(STATE_LABELS)

#: 記録の結果のうち、状態の語彙に無いもの（履歴にだけ出る）。状態としては「理由未確定」。
BUILT = "BUILT"  # 補充分が足を作り、その補充分が新 snapshot に入っている（なお欠落なら食い違い）
BUILT_NOT_IN_SNAPSHOT = "BUILT_NOT_IN_SNAPSHOT"  # 足を作ったが、その補充分は新 snapshot に無い
IN_PROGRESS = "IN_PROGRESS"  # 計画はあるが、書き出しも不合格もまだ無い

RESULT_LABELS: dict[str, str] = {
    **STATE_LABELS,
    BUILT: "補充した（新 snapshot に入っている）",
    BUILT_NOT_IN_SNAPSHOT: "補充した（その補充分は新 snapshot に無い）",
    IN_PROGRESS: "計画・取得の途中（書き出しも不合格もまだ無い）",
}
RESULT_TO_STATE: dict[str, str] = {
    NOT_FETCHED: NOT_FETCHED,
    PROVIDER_NO_TICKS: PROVIDER_NO_TICKS,
    UNRECONCILED: UNRECONCILED,
    VALIDATION_REJECTED: VALIDATION_REJECTED,
    BUILT: UNDETERMINED,
    BUILT_NOT_IN_SNAPSHOT: UNDETERMINED,
    IN_PROGRESS: UNDETERMINED,
}

#: 補充の manifest の「作らなかった理由」から結果への対応（D03 §14.8 の表）。
NOT_BUILT_TO_RESULT: dict[str, str] = {
    "HOUR_NOT_FETCHED": NOT_FETCHED,
    "PROVIDER_EMPTY": PROVIDER_NO_TICKS,
    "NO_TICK_IN_BAR": PROVIDER_NO_TICKS,
    "UNRECONCILED": UNRECONCILED,
}

#: 取得記録で「不合格」の状態を終わらせる行（D03 §14.12 の「不合格」の定義）。
_REOPENING_KINDS = frozenset({"final", "invalidate", "retry_mark"})

#: 休場の候補（保留。D03 §3.4.2 の候補 1・2・9。RF-11・RF-12・RF-19）。
HOLIDAY_CANDIDATES: tuple[str, ...] = ("1", "2", "9")

RESIDUAL_FIELDS: tuple[str, ...] = (
    "symbol",
    "timeframe",
    "start_utc",
    "end_utc",
    "duration_hours",
    "year_of_bar_end",
    "bar_count",
    "states",
    *(f"bars_{state}" for state in STATE_ORDER),
    "bars_with_unordered_records",
    "holiday_candidate_pending",
    "holiday_candidates",
    "basis_plan_ids",
    "history",
)

#: 時間足の足の長さ（残存欠落の区間を足 1 本ずつにほどくため）。
BAR_LENGTH: dict[str, timedelta] = {"15m@v1": timedelta(minutes=15), "1h@v1": timedelta(hours=1)}

REFILL_PREFIX = "data/raw/market/refill/"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

BarKey = tuple[str, str, datetime]


class ReportInputError(RuntimeError):
    """報告の入力（manifest・補充分）が読めない・食い違う。"""


# --- 読み込み ------------------------------------------------------------------------


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise ReportInputError(f"ファイルが無い: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportInputError(f"読めない: {path} ({exc})") from exc


def load_snapshot(snapshot_root: Path, snapshot_id: str) -> dict[str, Any]:
    """確定済みの snapshot の manifest を読む（D03 §3.7.1）。

    本体の読込（``snapshot_store().read_manifest``）で形と承認の記録の構造を確かめ、内容から
    ``snapshot_id`` を計算し直して記録とディレクトリ名に一致することを確かめてから使う（改変
    された manifest から報告を作らない）。暫定 snapshot（``_pending/``）は分類が無いので入力に
    できない（ここでは見つからない）。
    """
    path = snapshot_root / snapshot_id / "manifest.json"
    if not path.is_file():
        raise ReportInputError(f"ファイルが無い: {path}")
    try:
        verified = snapshot_store(snapshot_root).read_manifest(snapshot_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ReportInputError(f"snapshot の manifest を確かめられない: {path} ({exc})") from exc
    if str(verified.snapshot_id()) != snapshot_id:
        raise ReportInputError(f"snapshot_id がディレクトリ名と一致しない: {path}")
    manifest = _load_json(path)
    if not isinstance(manifest, dict):
        raise ReportInputError(f"snapshot の manifest が JSON の object ではない: {path}")
    return manifest


def is_approved(manifest: dict[str, Any]) -> bool:
    """承認済みか（manifest の ``approval`` が記入されている。D03 §3.7.1 の 3）。"""
    approval = manifest.get("approval")
    return isinstance(approval, dict) and bool(approval.get("approved_by"))


def refill_ids_of(manifest: dict[str, Any]) -> list[str]:
    """snapshot の ``sources`` が指す補充分の識別子（整列・重複なし）。"""
    found = {
        src["path"][len(REFILL_PREFIX) :].split("/", 1)[0]
        for src in manifest["sources"]
        if isinstance(src.get("path"), str) and src["path"].startswith(REFILL_PREFIX)
    }
    return sorted(found)


def _digest(payload: Any) -> str:
    return canonical.digest(payload).hex


def refill_id_from(manifest: dict[str, Any]) -> str:
    """補充の manifest の記録から ``refill_id`` を計算し直す（D03 §14.10）。"""
    hours = sorted(
        manifest["hours"], key=lambda item: (item["hour"]["symbol"], item["hour"]["start"])
    )
    return _digest(
        {
            "aggregation_rule_version": manifest["aggregation_rule_version"],
            "code_version": manifest["code_version"],
            "hours": [
                {
                    "outcome": item["outcome"],
                    "start": item["hour"]["start"],
                    "symbol": item["hour"]["symbol"],
                    "tick_digest": item["tick_digest"] or "",
                }
                for item in hours
            ],
            "plan_id": manifest["plan_id"],
        }
    )


def verified_refill_manifest(refill_root: Path, refill_id: str) -> dict[str, Any]:
    """補充の manifest を読み、記録から導ける識別子を計算し直して確かめる（D03 §14.11.1 の W5）。

    計画の中身から ``plan_id``、時間ファイルの記録と版から ``refill_id`` を計算し直し、記録と
    ディレクトリ名に一致すること。入力 snapshot の識別子が計画の中身と一致すること。
    """
    path = refill_root / refill_id / "refill_manifest.json"
    manifest = _load_json(path)
    try:
        if not isinstance(manifest, dict) or manifest.get("refill_id") != refill_id:
            raise ReportInputError(f"refill_id がディレクトリ名と一致しない: {path}")
        if _digest(manifest["plan"]) != manifest["plan_id"]:
            raise ReportInputError(f"plan_id が計画の中身と一致しない: {path}")
        if refill_id_from(manifest) != refill_id:
            raise ReportInputError(f"refill_id が記録から計算し直した値と一致しない: {path}")
        if manifest["snapshot_id"] != manifest["plan"]["snapshot_id"]:
            raise ReportInputError(f"入力 snapshot の識別子が計画の中身と一致しない: {path}")
        targets = set(_targets(manifest["plan"]))
        listed = [_bar_key(item) for item in manifest["not_built"]]
        if len(set(listed)) != len(listed) or not set(listed) <= targets:
            raise ReportInputError(
                f"作らなかった足の記録が計画の対象足を 1 回ずつ指していない: {path}"
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReportInputError(f"補充の manifest の形が読めない: {path} ({exc})") from exc
    return manifest


def load_refill(refill_root: Path, refill_id: str) -> dict[str, Any]:
    """補充分の manifest と検証記録を読む（W5 の検算と、検証記録の sha256 の照合）。"""
    manifest = verified_refill_manifest(refill_root, refill_id)
    path = refill_root / refill_id / "validation.json"
    if not path.is_file():
        raise ReportInputError(f"ファイルが無い: {path}")
    content = path.read_bytes()
    recorded = [item for item in manifest.get("files", []) if item.get("name") == path.name]
    if len(recorded) != 1 or recorded[0].get("sha256") != hashlib.sha256(content).hexdigest():
        raise ReportInputError(f"検証記録の sha256 が補充の manifest と一致しない: {path}")
    try:
        validation = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportInputError(f"読めない: {path} ({exc})") from exc
    return {"manifest": manifest, "validation": validation}


def snapshot_chain(
    snapshot_root: Path, refill_root: Path, snapshot_id: str
) -> dict[str, frozenset[str]]:
    """対象 snapshot とその前の snapshot ごとに、含む補充分を重ねた補充まで辿った集合。

    snapshot S の集合は、S の ``sources`` が指す補充分と、その補充分の入力 snapshot の集合の
    和。前後関係（D03 §14.15）の判定に使う。読めない snapshot・補充分があれば止める（前後関係
    を確かめられないため）。
    """
    closures: dict[str, frozenset[str]] = {}
    visiting: set[str] = set()

    def closure(current: str) -> frozenset[str]:
        if current in closures:
            return closures[current]
        if current in visiting:
            raise ReportInputError(f"snapshot と補充分の参照が循環している: {current}")
        visiting.add(current)
        found: set[str] = set()
        for refill_id in refill_ids_of(load_snapshot(snapshot_root, current)):
            found.add(refill_id)
            manifest = verified_refill_manifest(refill_root, refill_id)
            found |= closure(str(manifest["snapshot_id"]))
        visiting.discard(current)
        closures[current] = frozenset(found)
        return closures[current]

    closure(snapshot_id)
    return closures


# --- 記録の自動収集 --------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    """取得・検証を試みた記録 1 件（補充分 1 つ、取得記録の不合格の行 1 つ、または途中の計画）。

    ``is_state`` は、その計画の現在の状態を表す記録か（D03 §14.12: 補充分があれば補充分、
    無ければ最後の検証が不合格のままならその行、どちらでもなければ途中の計画）。状態を表さない
    記録（後で取り直した計画の古い不合格の行など）は履歴にだけ出る。
    """

    plan_id: str
    input_snapshot: str
    source: str
    refill_id: str | None
    recorded_at: str
    is_state: bool
    results: dict[BarKey, str]
    reasons: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Collection:
    """補充の置き場から集めた記録と、網羅性を確かめられなかった理由。"""

    records: list[Record]
    problems: list[str]
    plan_count: int = 0
    refill_count: int = 0

    @property
    def complete(self) -> bool:
        return not self.problems


def _tf_key(series: str, version: int) -> tuple[str, str]:
    symbol, timeframe, _ = series.split("/")
    return symbol, f"{timeframe}@v{version}"


def _bar_key(item: dict[str, Any]) -> BarKey:
    symbol, tf = _tf_key(item["series"], item["timeframe_version"])
    return symbol, tf, rhg.parse_utc(item["start"])


def _targets(plan: dict[str, Any]) -> list[BarKey]:
    return [_bar_key(target) for target in plan["target_bars"]]


def _not_built(records: list[dict[str, Any]]) -> dict[BarKey, str]:
    return {_bar_key(item): NOT_BUILT_TO_RESULT[item["reason"]] for item in records}


def _read_journal(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """取得記録の行（``entry``）を読み、行ごとのダイジェストを検算する（D03 §14.11.1 の W3）。

    最後の行だけが不完全（改行で終わらない・JSON として読めない・ダイジェストが合わない）なら
    書き込みの途中で止まった行として除く。最後の行以外が壊れていれば読めない取得記録とする。
    """
    if not path.is_file():
        return [], None
    try:
        content = path.read_bytes()
    except OSError as exc:
        return [], f"取得記録が読めない: {path} ({exc})"
    segments = content.split(b"\n")
    terminated = segments[-1] == b""
    lines = segments[:-1] if terminated else segments
    entries: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        last = number == len(lines)
        try:
            if last and not terminated:
                raise ValueError("not terminated by a newline")
            record = json.loads(line.decode("utf-8"))
            if not isinstance(record, dict) or set(record) != {"digest", "entry"}:
                raise ValueError("not a {digest, entry} record")
            entry = record["entry"]
            if not isinstance(entry, dict):
                raise ValueError("the entry is not an object")
            if hashlib.sha256(canonical.encode(entry)).hexdigest() != record["digest"]:
                raise ValueError("the digest does not match the entry")
        except (ValueError, UnicodeDecodeError) as exc:
            if last:
                continue
            return [], f"取得記録の {number} 行目が読めない（{exc}）: {path}"
        entries.append(entry)
    return entries, None


def collect_records(
    refill_root: Path,
    chain: dict[str, frozenset[str]],
    in_snapshot: frozenset[str],
) -> Collection:
    """対象 snapshot とその前の snapshot を入力にした計画・補充分の記録をすべて集める。

    ``chain`` の鍵（対象 snapshot と前の snapshot）を入力にした計画が対象。置き場の中に読めない
    もの（書きかけの補充分、読めない計画・取得記録、置き場に無いはずの名前のディレクトリ）が
    あれば ``problems`` に残す（そのものが対象の計画かどうか分からないので、網羅性を確かめられ
    ない）。
    """
    problems: list[str] = []
    records: list[Record] = []
    if not refill_root.is_dir():
        return Collection(records, [f"補充の置き場が無い: {refill_root}"])

    plans_with_refill: set[str] = set()
    refill_count = 0
    for directory in sorted(refill_root.iterdir(), key=lambda item: item.name):
        name = directory.name
        if name.startswith(("_", ".")) or not directory.is_dir():
            continue
        if not _HEX64.match(name):
            problems.append(f"補充分の名前ではないディレクトリ: {directory}")
            continue
        manifest_path = directory / "refill_manifest.json"
        if not manifest_path.is_file():
            problems.append(f"書きかけの補充分（refill_manifest.json が無い）: {directory}")
            continue
        try:
            manifest = verified_refill_manifest(refill_root, name)
            snapshot_id = str(manifest["snapshot_id"])
            if snapshot_id not in chain:
                continue
            plan_id = str(manifest["plan_id"])
            not_built = _not_built(manifest["not_built"])
            targets = _targets(manifest["plan"])
        except (ReportInputError, KeyError, TypeError, ValueError) as exc:
            problems.append(f"補充分が読めない: {directory} ({exc})")
            continue
        built = BUILT if name in in_snapshot else BUILT_NOT_IN_SNAPSHOT
        records.append(
            Record(
                plan_id=plan_id,
                input_snapshot=snapshot_id,
                source=f"refill:{name}",
                refill_id=name,
                recorded_at=str(manifest.get("created_at", "")),
                is_state=True,
                results={bar: not_built.get(bar, built) for bar in targets},
            )
        )
        plans_with_refill.add(plan_id)
        refill_count += 1

    plan_count = 0
    work_root = refill_root / "_work"
    work_dirs = sorted(work_root.iterdir(), key=lambda p: p.name) if work_root.is_dir() else []
    for work in work_dirs:
        if not work.is_dir():
            continue
        if not _HEX64.match(work.name):
            problems.append(f"計画の名前ではない作業ディレクトリ: {work}")
            continue
        try:
            plan = _load_json(work / "plan.json")
            if _digest(plan) != work.name:
                raise ReportInputError("plan_id が計画の中身から計算し直した値と一致しない")
            snapshot_id = str(plan["snapshot_id"])
            if snapshot_id not in chain:
                continue
            targets = _targets(plan)
        except (ReportInputError, KeyError, TypeError, ValueError) as exc:
            problems.append(f"計画が読めない: {work} ({exc})")
            continue
        entries, problem = _read_journal(work / "journal.jsonl")
        if problem is not None:
            problems.append(problem)
            continue
        plan_count += 1
        plan_id = work.name
        validations = [
            (number, entry)
            for number, entry in enumerate(entries, start=1)
            if entry.get("kind") == "validation"
        ]
        last_validation = validations[-1][0] if validations else 0
        rejected_now = (
            plan_id not in plans_with_refill
            and bool(validations)
            and validations[-1][1].get("passed") is False
            and not any(
                entry.get("kind") in _REOPENING_KINDS for entry in entries[last_validation:]
            )
        )
        for number, entry in validations:
            if entry.get("passed") is not False:
                continue
            details = entry.get("details") or {}
            try:
                not_built = _not_built(details.get("not_built", []))
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"取得記録の {number} 行目が読めない: {work} ({exc})")
                continue
            records.append(
                Record(
                    plan_id=plan_id,
                    input_snapshot=snapshot_id,
                    source=f"journal:{number}",
                    refill_id=None,
                    recorded_at=str(entry.get("at", "")),
                    is_state=rejected_now and number == last_validation,
                    results={bar: not_built.get(bar, VALIDATION_REJECTED) for bar in targets},
                    reasons=tuple(str(reason) for reason in entry.get("reasons", [])),
                    details=dict(details),
                )
            )
        if plan_id not in plans_with_refill and not rejected_now:
            records.append(
                Record(
                    plan_id=plan_id,
                    input_snapshot=snapshot_id,
                    source="plan",
                    refill_id=None,
                    recorded_at=str(entries[-1].get("at", "")) if entries else "",
                    is_state=True,
                    results=dict.fromkeys(targets, IN_PROGRESS),
                )
            )
    return Collection(records, problems, plan_count=plan_count, refill_count=refill_count)


# --- 足ごとの状態 ----------------------------------------------------------------------


@dataclass(frozen=True)
class BarState:
    """残存欠落の足 1 本の現在の状態（D03 §14.15 の (a)・(d)）。"""

    states: tuple[str, ...]
    basis: tuple[str, ...]
    unordered: bool


def precedes(earlier: Record, later: Record, chain: dict[str, frozenset[str]]) -> bool:
    """``earlier`` が ``later`` より前と確かめられるか（参照関係だけで決める）。

    ``earlier`` の補充分が ``later`` の入力 snapshot に（重ねた補充を辿って）含まれるときだけ
    前とする。補充分の無い記録（不合格・途中）は、どの記録の入力にも入らないので前と言えない。
    """
    if earlier.refill_id is None:
        return False
    return earlier.refill_id in chain.get(later.input_snapshot, frozenset())


def bar_states(
    bars: list[BarKey],
    collection: Collection,
    chain: dict[str, frozenset[str]],
) -> dict[BarKey, BarState]:
    """残存欠落の足ごとの現在の状態。

    その足を対象にした「状態を表す記録」のうち、参照関係で後の記録が無いもの（最も後の記録）の
    結果を状態にする。最も後の記録が 2 つ以上あれば併記する（1 つを選ばない）。どの記録も無い足
    は、網羅性を確かめられたときだけ「対象だが計画・試行されていない」、確かめられなければ
    「理由未確定」。
    """
    covering: dict[BarKey, list[Record]] = defaultdict(list)
    wanted = set(bars)
    for record in collection.records:
        if not record.is_state:
            continue
        for bar in record.results:
            if bar in wanted:
                covering[bar].append(record)
    result: dict[BarKey, BarState] = {}
    for bar in bars:
        found = covering.get(bar, [])
        if not found:
            state = NOT_PLANNED if collection.complete else UNDETERMINED
            result[bar] = BarState((state,), (), False)
            continue
        latest = [
            record
            for record in found
            if not any(precedes(record, other, chain) for other in found if other is not record)
        ]
        states = {RESULT_TO_STATE[record.results[bar]] for record in latest}
        result[bar] = BarState(
            states=tuple(state for state in STATE_ORDER if state in states),
            basis=tuple(sorted({record.plan_id for record in latest})),
            unordered=len(latest) > 1,
        )
    return result


def _bars_of(gap: rhg.GapInterval) -> list[BarKey]:
    step = BAR_LENGTH[gap.timeframe]
    bars: list[BarKey] = []
    moment = gap.start
    while moment < gap.end:
        bars.append((gap.symbol, gap.timeframe, moment))
        moment += step
    return bars


# --- 休場の候補の印 --------------------------------------------------------------------


def candidate_number(gap: rhg.GapInterval) -> str | None:
    """PR #55 の欠落区間が保留の休場の候補（D03 §3.4.2 の候補 1・2・9）のどれに当たるか。"""
    attribution, _, _ = rhg.classify(gap)
    if attribution == rhg.CALENDAR_UNDECLARED_OR_PARTLY_SOURCE:
        return "1"
    if attribution == rhg.CALENDAR:
        return "2"
    start_ny = gap.start.astimezone(rhg.NEW_YORK)
    if start_ny.weekday() == 4 and start_ny.hour == 16 and gap.hours <= 1.5:
        return "9"
    return None


def candidate_intervals(
    merged: dict[tuple[str, str], list[rhg.Interval]],
) -> dict[tuple[str, str], list[tuple[datetime, datetime, str]]]:
    """候補区間: 候補を定めた snapshot（PR #55）の欠落区間のうち、候補に当たるもの（系列ごと）。"""
    found: dict[tuple[str, str], list[tuple[datetime, datetime, str]]] = defaultdict(list)
    for (symbol, tf), gaps in merged.items():
        for start, end in gaps:
            number = candidate_number(rhg.GapInterval(symbol, tf, start, end))
            if number is not None:
                found[(symbol, tf)].append((start, end, number))
    return found


def overlapping_candidates(
    gap: rhg.GapInterval,
    candidates: dict[tuple[str, str], list[tuple[datetime, datetime, str]]],
) -> list[str]:
    """残存欠落と重なる候補区間の番号（同じ系列。部分補充で欠落が短くなっても重なれば付く）。"""
    numbers = {
        number
        for start, end, number in candidates.get((gap.symbol, gap.timeframe), [])
        if gap.start < end and start < gap.end
    }
    return [number for number in HOLIDAY_CANDIDATES if number in numbers]


# --- 残存欠落の行 ----------------------------------------------------------------------


def _history(bars: list[BarKey], collection: Collection) -> list[tuple[str, str, str, str, int]]:
    """区間の足を対象にした記録をすべて（状態を表さない記録も）。記録した時刻の順。"""
    counts: Counter[tuple[str, str, str, str]] = Counter()
    wanted = set(bars)
    for record in collection.records:
        for bar, result in record.results.items():
            if bar in wanted:
                counts[(record.recorded_at, record.plan_id, record.source, result)] += 1
    return [(*key, count) for key, count in sorted(counts.items())]


def residual_rows(
    merged: dict[tuple[str, str], list[rhg.Interval]],
    collection: Collection,
    chain: dict[str, frozenset[str]],
    candidates: dict[tuple[str, str], list[tuple[datetime, datetime, str]]],
) -> list[dict[str, Any]]:
    """新しい snapshot の残存欠落の区間ごとの行（CSV）。"""
    gaps = [
        rhg.GapInterval(symbol, tf, start, end)
        for symbol in rhg.SYMBOLS
        for tf in rhg.TIMEFRAMES
        for start, end in merged[(symbol, tf)]
    ]
    states = bar_states([bar for gap in gaps for bar in _bars_of(gap)], collection, chain)
    rows: list[dict[str, Any]] = []
    for gap in gaps:
        bars = _bars_of(gap)
        per_state: Counter[str] = Counter()
        basis: set[str] = set()
        unordered = 0
        for bar in bars:
            state = states[bar]
            per_state.update(state.states)
            basis.update(state.basis)
            unordered += state.unordered
        numbers = overlapping_candidates(gap, candidates)
        rows.append(
            {
                "symbol": gap.symbol,
                "timeframe": gap.timeframe,
                "start_utc": rhg.fmt_utc(gap.start),
                "end_utc": rhg.fmt_utc(gap.end),
                "duration_hours": round(gap.hours, 2),
                "year_of_bar_end": gap.end.year,
                "bar_count": len(bars),
                "states": "|".join(state for state in STATE_ORDER if per_state[state]),
                **{f"bars_{state}": per_state[state] for state in STATE_ORDER},
                "bars_with_unordered_records": unordered,
                "holiday_candidate_pending": bool(numbers),
                "holiday_candidates": "|".join(numbers),
                "basis_plan_ids": "|".join(sorted(basis)),
                "history": " ; ".join(
                    f"{at or '時刻なし'} plan={plan} {source} {result}×{count}"
                    for at, plan, source, result, count in _history(bars, collection)
                ),
            }
        )
    return rows


# --- 報告の本文 ------------------------------------------------------------------------


def _windows(
    merged: dict[tuple[str, str], list[rhg.Interval]],
    bounds: dict[tuple[str, str], rhg.Interval],
) -> tuple[list[rhg.Interval], list[rhg.Interval]]:
    """20 系列すべてと USDJPY 15 分足の、欠落の無い連続区間（長い順）。"""
    lo_all = max(lo for lo, _ in bounds.values())
    hi_all = min(hi for _, hi in bounds.values())
    union = rhg.merge_adjacent([g for gaps in merged.values() for g in gaps])
    all_series = sorted(
        rhg.contiguous_windows(union, lo_all, hi_all), key=lambda w: w[1] - w[0], reverse=True
    )
    key = ("USDJPY", "15m@v1")
    usdjpy: list[rhg.Interval] = []
    if key in bounds:
        lo, hi = bounds[key]
        usdjpy = sorted(
            rhg.contiguous_windows(merged[key], lo, hi), key=lambda w: w[1] - w[0], reverse=True
        )
    return all_series, usdjpy


def _days(window: rhg.Interval) -> str:
    return f"{(window[1] - window[0]).total_seconds() / 86400:.2f}"


def _hours(gaps: list[rhg.Interval]) -> float:
    return sum((e - s).total_seconds() / 3600 for s, e in gaps)


def render_report(
    *,
    old_id: str,
    new_id: str,
    new_manifest: dict[str, Any],
    approved: bool,
    candidates_id: str,
    old_merged: dict[tuple[str, str], list[rhg.Interval]],
    old_bounds: dict[tuple[str, str], rhg.Interval],
    new_merged: dict[tuple[str, str], list[rhg.Interval]],
    new_bounds: dict[tuple[str, str], rhg.Interval],
    refills: list[dict[str, Any]],
    collection: Collection,
    rows: list[dict[str, Any]],
    command: str,
) -> str:
    """報告の本文（Markdown。D03 §14.15 の 1〜5）。価格は書かない（前後の差は pip だけ）。"""
    conversion = new_manifest.get("conversion", {})
    calendar_id = conversion.get("calendar_id")
    calendar_version = conversion.get("calendar_version")
    refill_ids = ", ".join(f"`{item['manifest']['refill_id']}`" for item in refills) or "なし"
    rejected = [r for r in collection.records if r.is_state and r.source.startswith("journal:")]
    rejected_ids = ", ".join(f"`{r.plan_id}`" for r in rejected) or "なし"
    title = "# 補充の後の残存欠落と実行可能な連続期間（報告の材料）"
    lines: list[str] = [f"# 【下書き】{title[2:]}" if not approved else title, ""]
    if not approved:
        lines += [
            "**下書き**: 新 snapshot は承認されていない。正式な残存欠落の報告の入力は、分類と確定を"
            "済ませ承認された新 snapshot に限る（D03 §14.15）。承認の後に作り直すこと。",
            "",
        ]
    lines += ["## 1. 入力と出力の識別", ""]
    approval = new_manifest.get("approval") or {}
    lines += [
        "| 項目 | 値 |",
        "|---|---|",
        f"| 旧 snapshot | `{old_id}` |",
        f"| 新 snapshot | `{new_id}` |",
        "| 新 snapshot の承認 | "
        + (
            f"承認済み（{approval.get('approved_by')}、{approval.get('approved_at')}）"
            if approved
            else "未承認（この出力は下書き）"
        )
        + " |",
        f"| カレンダー | `{calendar_id}` 版 {calendar_version} |",
        f"| 補充の識別子（新 snapshot に入っているもの） | {refill_ids} |",
        f"| 不合格のままの計画（自動収集） | {rejected_ids} |",
        f"| 休場の候補区間を定めた snapshot | `{candidates_id}` |",
        "",
    ]
    lines.append(
        f"補充の置き場の自動収集: 計画 {collection.plan_count} 件・補充分"
        f" {collection.refill_count} 件（新 snapshot とその前の snapshot を入力にしたもの）。"
        + (
            "置き場の中はすべて読めた（網羅性を確かめた）。"
            if collection.complete
            else "置き場に読めないものがあり網羅性を確かめられないので、どの記録も無い足は"
            "「理由未確定」とした:"
        )
    )
    lines += [f"- {problem}" for problem in collection.problems]
    lines += [
        "",
        "人間が消した作業ディレクトリ（`_work/<plan_id>/`）の計画は置き場から分からないので、"
        "この収集に入らない。",
        "",
    ]

    lines += ["## 2. 補充の結果", ""]
    lines += [
        "補充した足の出来高は 0 で、「出来高不明」を意味する"
        "（実際の出来高として使わない。D03 §14.6）。",
        "",
        "| 系列 | 対象足 | 補充した | 作らなかった | 理由別（作らなかった足） |",
        "|---|---|---|---|---|",
    ]
    for refill in refills:
        manifest = refill["manifest"]
        by_reason: dict[str, Counter[str]] = defaultdict(Counter)
        for item in manifest["not_built"]:
            by_reason[item["series"]][item["reason"]] += 1
        for count in manifest["series_counts"]:
            detail = ", ".join(
                f"{reason} {number}"
                for reason, number in sorted(by_reason[count["series"]].items())
            )
            lines.append(
                f"| {count['series']} | {count['targets']} | {count['built']} |"
                f" {count['not_built']} | {detail or '—'} |"
            )
    lines.append("")
    unreconciled = [item for refill in refills for item in refill["manifest"]["unreconciled"]] + [
        item for rej in rejected for item in rej.details.get("unreconciled", [])
    ]
    lines.append(f"未照合の塊（補充分に書かず、人間の判断を待つ）: {len(unreconciled)}")
    lines += [
        f"- {item['series']} {item['chunk_start']}（対象足 {item['target_count']} 本）"
        for item in unreconciled
    ]
    lines.append("")
    for refill in refills:
        validation = refill["validation"]
        review = [item for item in validation["neighbors"] if item["needs_review"]]
        short = refill["manifest"]["refill_id"][:12]
        lines.append(
            f"検証（`{short}…`）: 照合 {validation['reconciled_count']} 本・"
            f"一致 {validation['matched_count']} 本、"
            f"範囲外の tick {validation['out_of_range_tick_count']} 件、"
            f"bid が ask より大きい tick {validation['bid_above_ask_tick_count']} 件"
            f"（合否に使わない）、「要確認」の印 {len(review)} 件"
        )
        lines += [
            f"- 要確認: {item['series']} {item['chunk_start']} {item['side']}"
            f" 差 {item['difference_pips']} pip"
            for item in review
        ]
    for rej in rejected:
        lines.append(f"不合格の計画 `{rej.plan_id[:12]}…`: " + "; ".join(rej.reasons))
    lines.append("")

    lines += ["## 3. 残存欠落", ""]
    lines += [
        "区間ごとの (a) 現在の状態・(b) 試行の履歴・(c) 休場の候補の印・(d) 根拠の計画は"
        " CSV の列 `states`（と状態ごとの足の数 `bars_<状態>`）・`history`・"
        "`holiday_candidate_pending`／`holiday_candidates`・`basis_plan_ids`。",
        "",
    ]
    lines += ["### 3.1 系列別（旧 → 新。件数 / 合計時間）", ""]
    lines += ["| 系列 | 旧 | 新 |", "|---|---|---|"]
    for symbol in rhg.SYMBOLS:
        for tf in rhg.TIMEFRAMES:
            old = old_merged.get((symbol, tf), [])
            new = new_merged.get((symbol, tf), [])
            lines.append(
                f"| {symbol} {tf} | {len(old)} / {_hours(old):.2f}h |"
                f" {len(new)} / {_hours(new):.2f}h |"
            )
    total_old = [g for gaps in old_merged.values() for g in gaps]
    total_new = [g for gaps in new_merged.values() for g in gaps]
    lines += [
        f"| **合計** | **{len(total_old)} / {_hours(total_old):.2f}h** |"
        f" **{len(total_new)} / {_hours(total_new):.2f}h** |",
        "",
    ]
    lines += ["### 3.2 年別（新。足の終端の年）", "", "| 年 | 件数 / 合計 |", "|---|---|"]
    by_year: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        by_year[row["year_of_bar_end"]].append(row["duration_hours"])
    for year in sorted(by_year):
        lines.append(f"| {year} | {len(by_year[year])} / {sum(by_year[year]):.2f}h |")
    lines.append("")
    lines += [
        "### 3.3 状態別（新）",
        "",
        "状態ごとに列を分けて数える。1 つの区間に複数の状態の足があれば、その区間は各行に数える"
        "（区間の数の列の合計は区間の総数を超えうる）。比較できない記録を併記した足は、併記した"
        "状態のそれぞれに数える。",
        "",
        "| 状態 | その状態の足を含む区間 | 足の数 | 足の時間の合計 |",
        "|---|---|---|---|",
    ]
    for state in STATE_ORDER:
        column = f"bars_{state}"
        with_state = [row for row in rows if row[column]]
        bars = sum(row[column] for row in rows)
        hours = sum(
            row[column] * BAR_LENGTH[row["timeframe"]].total_seconds() / 3600 for row in rows
        )
        lines.append(
            f"| {STATE_LABELS[state]}（`{state}`） | {len(with_state)} | {bars} | {hours:.2f}h |"
        )
    mixed = [row for row in rows if "|" in row["states"]]
    unordered = sum(row["bars_with_unordered_records"] for row in rows)
    pending = [row for row in rows if row["holiday_candidate_pending"]]
    lines += [
        "",
        f"- 複数の状態の足を含む区間: {len(mixed)} / 区間の総数 {len(rows)}",
        f"- 比較できない記録を併記した足: {unordered} 本（CSV の `bars_with_unordered_records`）",
        f"- 休場の候補（保留）の区間と重なる残存欠落: {len(pending)} 区間 / "
        f"{sum(row['duration_hours'] for row in pending):.2f}h（D03 §3.4.2 の候補 1・2・9。"
        "状態とは別の印）",
        "",
    ]

    lines += ["## 4. 実行可能な連続期間", ""]
    old_all, old_usdjpy = _windows(old_merged, old_bounds)
    new_all, new_usdjpy = _windows(new_merged, new_bounds)
    for heading, windows in (
        ("20 系列すべてに欠落の無い連続区間の上位 5（新）", new_all),
        ("USDJPY 15 分足だけの上位 5（新）", new_usdjpy),
    ):
        lines += [
            f"### {heading}",
            "",
            "| 順位 | 始端（UTC） | 終端（UTC） | 日数 |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {rank} | {rhg.fmt_utc(s)} | {rhg.fmt_utc(e)} | {_days((s, e))} |"
            for rank, (s, e) in enumerate(windows[:5], start=1)
        ]
        lines.append("")
    lines += [
        "### 旧 snapshot との比較",
        "",
        "| 項目 | 旧 | 新 |",
        "|---|---|---|",
        f"| 20 系列の連続区間の数 | {len(old_all)} | {len(new_all)} |",
        f"| 20 系列の最長（日） | {_days(old_all[0]) if old_all else '—'} |"
        f" {_days(new_all[0]) if new_all else '—'} |",
        f"| USDJPY 15 分足の連続区間の数 | {len(old_usdjpy)} | {len(new_usdjpy)} |",
        f"| USDJPY 15 分足の最長（日） | {_days(old_usdjpy[0]) if old_usdjpy else '—'} |"
        f" {_days(new_usdjpy[0]) if new_usdjpy else '—'} |",
        "",
    ]

    lines += ["## 5. 再現", "", "```", command, "```", ""]
    lines.append(
        "戦略の成績（指標・レポート・`runs/`）は読んでいない。snapshot の manifest・補充分の"
        " manifest と検証記録・計画と取得記録だけを読んだ（D03 §14.2 の 7）。"
    )
    return "\n".join(lines) + "\n"


def write_outputs(
    csv_path: Path, rows: list[dict[str, Any]], report_path: Path, report: str
) -> None:
    """CSV と報告を排他的に作成する（既にあれば失敗し、上書きしない）。"""
    with csv_path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RESIDUAL_FIELDS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with report_path.open("x", encoding="utf-8") as handle:
        handle.write(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--snapshot-root", type=Path, default=Path("data/snapshots"))
    parser.add_argument("--snapshot-id", required=True, help="新しい snapshot の識別子")
    parser.add_argument(
        "--previous-snapshot-id",
        default=rhg.DEFAULT_SNAPSHOT_ID,
        help="比較する旧 snapshot の識別子（既定は補充の前の承認済み snapshot）",
    )
    parser.add_argument(
        "--candidates-snapshot-id",
        default=rhg.DEFAULT_SNAPSHOT_ID,
        help="休場の候補区間を定めた snapshot（既定は PR #55 の欠落一覧の snapshot）",
    )
    parser.add_argument("--refill-root", type=Path, default=Path("data/raw/market/refill"))
    parser.add_argument("--out", type=Path, required=True, help="報告と CSV の出力先ディレクトリ")
    args = parser.parse_args(argv)

    try:
        new_manifest = load_snapshot(args.snapshot_root, args.snapshot_id)
        old_manifest = load_snapshot(args.snapshot_root, args.previous_snapshot_id)
        candidates_manifest = load_snapshot(args.snapshot_root, args.candidates_snapshot_id)
        in_snapshot = refill_ids_of(new_manifest)
        refills = [load_refill(args.refill_root, rid) for rid in in_snapshot]
        chain = snapshot_chain(args.snapshot_root, args.refill_root, args.snapshot_id)
    except (ReportInputError, ValueError, KeyError, TypeError) as exc:
        print(f"報告の入力が読めない: {exc}", file=sys.stderr)
        return 1
    collection = collect_records(args.refill_root, chain, frozenset(in_snapshot))
    new_merged, new_bounds = rhg.collect_gaps(new_manifest)
    old_merged, old_bounds = rhg.collect_gaps(old_manifest)
    candidates = candidate_intervals(rhg.collect_gaps(candidates_manifest)[0])
    rows = residual_rows(new_merged, collection, chain, candidates)
    approved = is_approved(new_manifest)
    command = " ".join(
        [
            "uv run python -m tools.ops.refill_report",
            f"--snapshot-root {args.snapshot_root}",
            f"--snapshot-id {args.snapshot_id}",
            f"--previous-snapshot-id {args.previous_snapshot_id}",
            f"--candidates-snapshot-id {args.candidates_snapshot_id}",
            f"--refill-root {args.refill_root}",
            f"--out {args.out}",
        ]
    )
    report = render_report(
        old_id=args.previous_snapshot_id,
        new_id=args.snapshot_id,
        new_manifest=new_manifest,
        approved=approved,
        candidates_id=args.candidates_snapshot_id,
        old_merged=old_merged,
        old_bounds=old_bounds,
        new_merged=new_merged,
        new_bounds=new_bounds,
        refills=refills,
        collection=collection,
        rows=rows,
        command=command,
    )
    suffix = "" if approved else "_draft"
    csv_path = args.out / f"residual_gaps{suffix}.csv"
    report_path = args.out / f"refill_report{suffix}.md"
    existing = [path for path in (csv_path, report_path) if path.exists() or path.is_symlink()]
    if existing:
        print(
            "出力先に報告が既にある（上書きしない。どちらも書かなかった）: "
            + ", ".join(str(path) for path in existing),
            file=sys.stderr,
        )
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    write_outputs(csv_path, rows, report_path, report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
