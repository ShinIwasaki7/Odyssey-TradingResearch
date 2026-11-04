"""回帰確認用のダイジェスト（D06 v1.20 案 §19.15。仮置き C1-12。受入テスト CT-18）。

順 7 の終了条件 G1 の「既存の USDJPY の再現が一致」を、換算の実装の前と後の main で走らせた
2 本の run の成果物で確かめる。結果ダイジェスト（`result_digest`。D07 §9.2）は run の識別子を含み、
run の識別子にはコードのダイジェストが入る（D07 §21.1）ので、USDJPY の計算が同じでもコードが
変われば必ず変わる。そこで、コードが変われば必ず変わる項目を除いたダイジェストを別に作る。
**`result_digest` の契約は変えない**（本道具は成果物を読むだけで、何も書かない）。

定義（D06 §19.15 の手順 1〜5）:

1. 対象: 評価 5 表（D07 §8.1。`runs/<run_id>/eval/<run_evaluation_id>/<表>.parquet`）と、判断履歴の
   表（D06 §9.2・§18.10。`runs/<run_id>/<表>.parquet`）のうち比べる 2 本の run の両方にある表。
2. 各表を整列鍵（D07 §8.1 の評価 5 表の主キー、D06 §9.2 の判断履歴の表の主キー。平坦化した列名。
   `EVALUATION_SORT_KEYS`・`TRACE_SORT_KEYS`）で並べる。鍵が同じ行は、正規化した行全体の正規化エンコードの順で並べる。
3. 除く列: run の識別子・評価の識別子・コード・依存・環境・設定のダイジェストとそれを写した列
   （`DROPPED_COLUMN_NAMES`。列の名前で除く）と、識別子を含むパスの列（値のある行がすべて
   `runs/<run_id>/` の下のパス）。
   他の値と同じ列に埋め込まれたパスは列ごと除かず、3a で正規化する。
3a. 残した列のすべての文字列の値（`list` の列は要素ごと）について、その run 自身の `run_id`・
   `run_evaluation_id`・`code_digest`・`lock_digest`・`env_digest`・`config_digest` の値の出現を、
   種類ごとに決まった置き換えの文字列（`<RUN_ID>` など）に置き換える。置き換えは値の一致だけで行い
   （列の名前に依らない）、他の run の識別子は置き換えない。値は run manifest（`manifest.json`）と
   評価 manifest（`evaluation.json`）から読む。
4. 前の run に無く後の run にだけある列は後の run のダイジェストから除き、その列の値が恒等換算の
   既定の値（`None`・空の文字列・空の `list`・恒等の経路の正規化エンコード）だけであることを別に
   確かめる。違えば失敗を返す（列の追加で比較が成り立たなくなることを防ぐ）。
5. 残りを D02 §9.3 の正規化エンコード（`odyssey_fx.common.canonical`）にして SHA-256
   （16 進 64 文字）。

使い方（D17 §3 の G1 の (4)）::

    uv run python -m tools.ops.regression_digest \
        --before runs/<前の run_id> --after runs/<後の run_id>

標準出力には 2 つのダイジェストと判定（一致・不一致・失敗）と、失敗のときの表と列の名前だけを出す。
**成績の値・価格は出さない**。終了コード: 0 一致、1 不一致または失敗、2 入力の誤り。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import polars as pl

from odyssey_fx.backtest.trace.recorder import TraceTable
from odyssey_fx.common.canonical import digest, encode
from odyssey_fx.evaluation.application.manifest import EvaluationTable

__all__ = [
    "DROPPED_COLUMN_NAMES",
    "EVALUATION_SORT_KEYS",
    "REPLACEMENT_TOKENS",
    "TRACE_SORT_KEYS",
    "RegressionComparison",
    "RegressionDigestError",
    "RunArtifact",
    "compare_runs",
    "is_identity_default",
    "locate_artifact",
    "main",
]

#: 評価 5 表の整列鍵（D07 §8.1 の整列鍵と §9.3 の R1-D07-5 の主キー。平坦化した列名）。
#: `METRICS`・`CATEGORY_COUNTS`・`CONSISTENCY_CHECKS` の宣言順は主キーと 1 対 1 なので、
#: 主キーの値の順で並べる。
EVALUATION_SORT_KEYS: Final[Mapping[EvaluationTable, tuple[str, ...]]] = {
    EvaluationTable.METRICS: ("metric_id",),
    EvaluationTable.CATEGORY_COUNTS: ("category", "key"),
    EvaluationTable.TRADES: ("entry_at_time", "entry_at_phase", "entry_at_sequence", "position_id"),
    EvaluationTable.FILL_DIAGNOSTICS: ("fill_id",),
    EvaluationTable.CONSISTENCY_CHECKS: ("check",),
}

_AT: Final = ("at_time", "at_phase", "at_sequence")

#: 判断履歴の表の主キー（D06 §9.2 の表 1〜19、§18.10 の表 20〜22。平坦化した列名。処理点は 3 列）。
TRACE_SORT_KEYS: Final[Mapping[TraceTable, tuple[str, ...]]] = {
    TraceTable.OUTPUTS: ("output_id",),
    TraceTable.EVALUATIONS: ("evaluation_id",),
    TraceTable.OPPORTUNITY_TRANSITIONS: ("opportunity_id", *_AT),
    TraceTable.ORDER_REQUESTS: ("attempt_id",),
    TraceTable.ATTEMPT_DECISIONS: ("attempt_id",),
    TraceTable.RISK_ASSESSMENTS: ("assessment_id",),
    TraceTable.ORDERS: ("order_id",),
    TraceTable.ORDER_EVENTS: ("event_id",),
    TraceTable.FILLS: ("fill_id",),
    TraceTable.RESERVATIONS: ("reservation_id",),
    TraceTable.POSITIONS: ("position_id",),
    TraceTable.MANAGEMENT_APPLICATIONS: (
        "position_id",
        "application_at_time",
        "application_at_phase",
        "application_at_sequence",
    ),
    TraceTable.INTRABAR_RESOLUTIONS: ("fill_id",),
    TraceTable.LEDGER_SNAPSHOTS: _AT,
    TraceTable.EVIDENCE: ("evidence_id",),
    TraceTable.WAIT_EVENTS: ("request_id", *_AT),
    TraceTable.INPUT_SUBSTITUTIONS: ("evaluation_id", "input_name", "source_index"),
    TraceTable.CONFIRMATION_ATTEMPTS: ("opportunity_id", "bar_key_series", "bar_key_bar_start"),
    TraceTable.VALIDITY_RECHECKS: ("opportunity_id", *_AT),
    TraceTable.PUBLICATIONS: ("bar_key_series", "bar_key_bar_start"),
    TraceTable.RESUMPTIONS: ("series", "minute"),
    TraceTable.HISTORY_RESETS: (
        "series",
        "no_record_bar_key_series",
        "no_record_bar_key_bar_start",
        "window_kind",
        "window_bars",
        "exclude_latest_bars",
        "window_duration",
        "insufficient_from",
    ),
}

#: 手順 3 で列の名前で除く列: run の識別子・評価の識別子・コード・依存・環境・設定のダイジェストと、
#: それを写した列（評価 manifest の `run_code_digest`・`evaluation_code_digest`・`run_manifest_ref`
#: ＝設定のダイジェスト）。今の成果物の表に現れるのは判断履歴の全表の `run_id` だけ。
DROPPED_COLUMN_NAMES: Final = frozenset(
    {
        "run_id",
        "run_evaluation_id",
        "code_digest",
        "lock_digest",
        "env_digest",
        "config_digest",
        "run_code_digest",
        "evaluation_code_digest",
        "run_manifest_ref",
    }
)

#: 手順 3a の置き換え（識別子の種類 → 置き換えの文字列）。この順に置き換える。
REPLACEMENT_TOKENS: Final = (
    ("run_id", "<RUN_ID>"),
    ("run_evaluation_id", "<RUN_EVALUATION_ID>"),
    ("code_digest", "<CODE_DIGEST>"),
    ("lock_digest", "<LOCK_DIGEST>"),
    ("env_digest", "<ENV_DIGEST>"),
    ("config_digest", "<CONFIG_DIGEST>"),
)

_RUN_MANIFEST: Final = "manifest.json"
_EVALUATION_MANIFEST: Final = "evaluation.json"
_EVALUATION_DIRECTORY: Final = "eval"
#: 恒等換算の経路の正規化エンコードの率（`identity_path`: 脚 0 本・率 1。D06 §8.5.1）。
_IDENTITY_RATE: Final = "1e0"


class RegressionDigestError(Exception):
    """成果物が読めない・形が違う（入力の誤り。終了コード 2）。"""


@dataclass(frozen=True, slots=True)
class RunArtifact:
    """1 本の run の成果物の置き場と、その run 自身の識別子・ダイジェスト（手順 3a の値）。"""

    run_directory: Path
    evaluation_directory: Path
    identifiers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RegressionComparison:
    """2 本の run の回帰確認用のダイジェストと、比べた範囲（値は持たない）。

    - `compared_tables`: 手順 1 で比べた表（評価 5 表と、両方にある判断履歴の表）。
    - `dropped_columns`: 表ごとに手順 3 で除いた列（両方の run の和）。
    - `after_only_columns`: 手順 4 で後の run から除いた列（`表.列`）。
    - `failures`: 手順 4 の確かめに落ちた列（`表.列`）。空でなければ失敗。
    """

    before_digest: str
    after_digest: str
    compared_tables: tuple[str, ...]
    dropped_columns: Mapping[str, tuple[str, ...]]
    after_only_columns: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def matches(self) -> bool:
        """2 つのダイジェストが一致し、手順 4 の失敗が無い。"""
        return self.before_digest == self.after_digest and not self.failures


def _read_json(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise RegressionDigestError(f"{path} が無い")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegressionDigestError(f"{path} を JSON として読めない: {exc}") from exc
    if not isinstance(payload, dict):
        raise RegressionDigestError(f"{path} が JSON のオブジェクトでない")
    return payload


def _text_of(payload: Mapping[str, Any], key: str, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RegressionDigestError(f"{path} の {key} が文字列でない")
    return value


def locate_artifact(run_directory: Path, run_evaluation_id: str | None = None) -> RunArtifact:
    """`runs/<run_id>/` から評価の置き場と、run 自身の識別子・ダイジェストを決める。

    評価は `runs/<run_id>/eval/<run_evaluation_id>/`（D07 §8.2）。`run_evaluation_id` を渡さ
    なければ、評価の置き場がちょうど 1 つのときだけそれを使う。run manifest の `run_id` と
    ディレクトリ名、評価 manifest の `run_id`・`run_evaluation_id` と置き場が食い違えば入力の
    誤り。
    """
    directory = Path(run_directory)
    manifest_path = directory / _RUN_MANIFEST
    manifest = _read_json(manifest_path)
    run_id = _text_of(manifest, "run_id", manifest_path)
    if run_id != directory.name:
        raise RegressionDigestError(f"{manifest_path} の run_id がディレクトリ名と違う")
    evaluations = directory / _EVALUATION_DIRECTORY
    if run_evaluation_id is None:
        found = (
            sorted(item for item in evaluations.iterdir() if item.is_dir())
            if (evaluations.is_dir())
            else []
        )
        if len(found) != 1:
            raise RegressionDigestError(
                f"{evaluations} の評価の置き場が {len(found)} 個（評価の識別子を指定する）"
            )
        evaluation_directory = found[0]
    else:
        evaluation_directory = evaluations / run_evaluation_id
    evaluation_path = evaluation_directory / _EVALUATION_MANIFEST
    evaluation = _read_json(evaluation_path)
    if _text_of(evaluation, "run_id", evaluation_path) != run_id:
        raise RegressionDigestError(f"{evaluation_path} の run_id が run と違う")
    evaluation_id = _text_of(evaluation, "run_evaluation_id", evaluation_path)
    if evaluation_id != evaluation_directory.name:
        raise RegressionDigestError(f"{evaluation_path} の run_evaluation_id が置き場の名前と違う")
    identifiers = {
        "run_id": run_id,
        "run_evaluation_id": evaluation_id,
        **{
            name: _text_of(manifest, name, manifest_path)
            for name in ("code_digest", "lock_digest", "env_digest", "config_digest")
        },
    }
    return RunArtifact(
        run_directory=directory,
        evaluation_directory=evaluation_directory,
        identifiers=identifiers,
    )


def _normalize(value: Any, replacements: Sequence[tuple[str, str]]) -> Any:
    """手順 3a: 文字列の値（`list` は要素ごと）の中の、run 自身の識別子の出現を置き換える。"""
    if isinstance(value, str):
        for needle, token in replacements:
            value = value.replace(needle, token)
        return value
    if isinstance(value, list):
        return [_normalize(item, replacements) for item in value]
    return value


def is_identity_default(value: object) -> bool:
    """手順 4 の「恒等換算の既定の値」か: `None`・空の文字列・空の `list`（要素がどれも既定の値の
    `list` を含む）・恒等の経路の正規化エンコード（脚 0 本・率 1。`identity_path`。D06 §8.5.1）。"""
    if value is None or value == "":
        return True
    if isinstance(value, list):
        return all(is_identity_default(item) for item in value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            return False
        return (
            isinstance(decoded, dict)
            and decoded.get("legs") == []
            and decoded.get("rate") == _IDENTITY_RATE
        )
    return False


def _sort_key(value: Any) -> tuple[int, Any]:
    return (1, "") if value is None else (0, value)


def _path_columns(frame: pl.DataFrame, run_id: str) -> set[str]:
    """パスの列（手順 3 の「識別子を含むパス」）: 値のある行がすべて `runs/<run_id>/` の下のパス。

    パスでない値と同じ列に埋め込まれたパス（整合検査の表の `expected`・`observed` の
    `runs/<run_id>/manifest.json` など）は列ごと除かず、手順 3a の置き換えで正規化する。
    """
    needle = f"runs/{run_id}/"
    found: set[str] = set()
    for column, dtype in zip(frame.columns, frame.dtypes, strict=True):
        values = frame[column]
        if dtype == pl.List(pl.String()):
            values = values.explode(empty_as_null=True)
        elif dtype != pl.String():
            continue
        present = values.drop_nulls()
        if len(present) > 0 and present.str.contains(needle, literal=True).all():
            found.add(column)
    return found


def _table_payload(
    frame: pl.DataFrame,
    columns: Sequence[str],
    keys: Sequence[str],
    replacements: Sequence[tuple[str, str]],
    label: str,
) -> dict[str, Any]:
    """手順 2・3a: 残す列の行を正規化し、整列鍵（同じ鍵の行は行全体のエンコード）で並べる。"""
    missing = [key for key in keys if key not in frame.columns]
    if missing:
        raise RegressionDigestError(f"{label} に整列鍵の列 {missing} が無い")
    positions = [columns.index(key) for key in keys if key in columns]
    rows = [
        [_normalize(value, replacements) for value in row]
        for row in frame.select(list(columns)).rows()
    ]
    rows.sort(key=lambda row: (tuple(_sort_key(row[index]) for index in positions), encode(row)))
    return {"columns": list(columns), "rows": rows}


def _read_parquet(path: Path) -> pl.DataFrame:
    try:
        return pl.read_parquet(path)
    except (OSError, pl.exceptions.PolarsError) as exc:
        raise RegressionDigestError(f"{path} を Parquet として読めない: {exc}") from exc


def compare_runs(
    before: Path,
    after: Path,
    *,
    before_evaluation_id: str | None = None,
    after_evaluation_id: str | None = None,
) -> RegressionComparison:
    """2 本の run の成果物（`runs/<run_id>/`）の回帰確認用のダイジェストを作って比べる。

    手順 1〜5（モジュールの説明）。成果物を読むだけで、何も書かない。
    """
    first = locate_artifact(before, before_evaluation_id)
    second = locate_artifact(after, after_evaluation_id)
    sources: list[tuple[str, Path, Path, tuple[str, ...]]] = [
        (
            table.value,
            first.evaluation_directory / f"{table.value}.parquet",
            second.evaluation_directory / f"{table.value}.parquet",
            keys,
        )
        for table, keys in EVALUATION_SORT_KEYS.items()
    ]
    for path in (
        item for _, before_path, after_path, _ in sources for item in (before_path, after_path)
    ):
        if not path.is_file():
            raise RegressionDigestError(
                f"評価の表 {path} が無い（D07 §8.1。どの状態でも 5 表を書く）"
            )
    sources += [
        (
            table.value,
            first.run_directory / f"{table.value}.parquet",
            second.run_directory / f"{table.value}.parquet",
            keys,
        )
        for table, keys in TRACE_SORT_KEYS.items()
        if (first.run_directory / f"{table.value}.parquet").is_file()
        and (second.run_directory / f"{table.value}.parquet").is_file()
    ]
    replacements = [
        tuple((artifact.identifiers[name], token) for name, token in REPLACEMENT_TOKENS)
        for artifact in (first, second)
    ]
    payloads: tuple[dict[str, Any], dict[str, Any]] = ({}, {})
    dropped: dict[str, tuple[str, ...]] = {}
    after_only: list[str] = []
    failures: list[str] = []
    for name, before_path, after_path, keys in sources:
        frames = (_read_parquet(before_path), _read_parquet(after_path))
        extra = [column for column in frames[1].columns if column not in frames[0].columns]
        for column in extra:
            after_only.append(f"{name}.{column}")
            if not all(is_identity_default(value) for value in frames[1][column].to_list()):
                failures.append(f"{name}.{column}")
        removed = (
            {
                column
                for frame in frames
                for column in frame.columns
                if column in DROPPED_COLUMN_NAMES
            }
            | _path_columns(frames[0], first.identifiers["run_id"])
            | _path_columns(frames[1], second.identifiers["run_id"])
        )
        dropped[name] = tuple(sorted(removed))
        for index, frame in enumerate(frames):
            kept = [
                column
                for column in frame.columns
                if column not in removed and not (index == 1 and column in extra)
            ]
            payloads[index][name] = _table_payload(
                frame, kept, keys, replacements[index], f"{name}（{'前' if index == 0 else '後'}）"
            )
    return RegressionComparison(
        before_digest=digest(payloads[0]).hex,
        after_digest=digest(payloads[1]).hex,
        compared_tables=tuple(name for name, *_ in sources),
        dropped_columns=dropped,
        after_only_columns=tuple(after_only),
        failures=tuple(failures),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """`--before`・`--after` の 2 本の run を比べ、2 つのダイジェストと判定だけを出す。"""
    parser = argparse.ArgumentParser(
        prog="python -m tools.ops.regression_digest",
        description="回帰確認用のダイジェスト（D06 §19.15）。成績の値・価格は出さない。",
    )
    parser.add_argument("--before", type=Path, required=True, help="前の run の runs/<run_id>/")
    parser.add_argument("--after", type=Path, required=True, help="後の run の runs/<run_id>/")
    parser.add_argument("--before-evaluation-id", default=None, help="前の run の評価の識別子")
    parser.add_argument("--after-evaluation-id", default=None, help="後の run の評価の識別子")
    args = parser.parse_args(argv)
    try:
        result = compare_runs(
            args.before,
            args.after,
            before_evaluation_id=args.before_evaluation_id,
            after_evaluation_id=args.after_evaluation_id,
        )
    except RegressionDigestError as exc:
        print(f"失敗: {exc}", file=sys.stderr)
        return 2
    print(f"前の run の回帰確認用のダイジェスト: {result.before_digest}")
    print(f"後の run の回帰確認用のダイジェスト: {result.after_digest}")
    for column in result.failures:
        print(f"後の run にだけある列に既定でない値がある: {column}")
    if result.failures:
        print("判定: 失敗")
    else:
        print(f"判定: {'一致' if result.matches else '不一致'}")
    return 0 if result.matches else 1


if __name__ == "__main__":  # pragma: no cover - コマンドの入口
    sys.exit(main())
