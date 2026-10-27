"""研究の画面: 実験ごと・版ごとに「戦略の内容」と「run の結果」を 1 枚の HTML で見せる。

2026-10-27 の人間の指示（設計書は作らず、画面を見ながら直す）による表示の道具で、成果物から
作る表示であり正本ではない。正本は実験の版のディレクトリの記録票・結末記録・`report.md`。

- **読むもの**: `<runs-root>/runs/experiments/<名前>/v<版>/` の記録票（`experiment_manifest.json`。
  実行のときの実験設定と戦略宣言の全文を持つ）・結末記録（`experiment_outcome.json`）・レポート
  （`report.md`）・実行の包みが書く時刻と終了コードのファイル。まだ走らせていない版は
  `<repo-root>/configs/experiments/` の実験設定（書式 v2）から、戦略の内容だけを出す。
- **計算し直さない**: 成績の数値は `report.md` を変換して出すだけで、成果物の表（Parquet）は
  開かない（Parquet を開くのは `report.py` と `fs_store` だけ。D01 §5・ADR-0025）。
- **出力**: `<runs-root>/runs/viewer/index.html`（git 管理外）。CSS と JS は埋め込み、外部へ通信
  しない。標準出力には置き場と件数だけを出し、成績の数値を出さない。

使い方（run の後に実行役が走らせる）::

    uv run python -m tools.viewer.research_viewer --runs-root <リポジトリ> --open
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from tools.viewer.markdown_html import Document, convert

# --- 語彙の表示 ---------------------------------------------------------------------

#: 実験の判定（`SearchVerdict` の値。表示名は `search_report.py` と同じ）。
VERDICT_NAMES: Mapping[str, str] = {
    "MEETS_STANDARD": "共通基準を満たす",
    "BELOW_STANDARD": "共通基準を満たさない",
    "INSUFFICIENT_EVIDENCE": "証拠不足",
    "INCOMPLETE": "判定できない",
    "MET_IN_MECHANISM_CHECK": "条件をすべて満たした",
}

#: 判定ごとの色の区分（画面の印の色。良し悪しの評価ではなく判定の種類を見分けるため）。
VERDICT_TONES: Mapping[str, str] = {
    "MEETS_STANDARD": "good",
    "MET_IN_MECHANISM_CHECK": "good",
    "BELOW_STANDARD": "bad",
    "INSUFFICIENT_EVIDENCE": "warn",
    "INCOMPLETE": "warn",
}

#: 系譜の変更の種類（D13 §7.1）。
CHANGE_KIND_NAMES: Mapping[str, str] = {
    "PARAMETERS": "格子の値・軸の変更",
    "STRATEGY_CODE": "戦略の宣言の変更",
    "HYPOTHESIS": "仮説の変更",
    "POLICY_REVISION": "評価基準の改訂",
    "DATA": "データの版の変更",
    "RETRY": "再試行",
}

ROLE_NAMES: Mapping[str, str] = {
    "market_state": "市場状態（取引してよい向き）",
    "trigger": "取引機会（入る合図）",
    "order": "発注",
    "protection": "損切り",
    "exit": "出口（利確など）",
}

_FIELD_NAMES: Mapping[str, str] = {
    "OPEN": "始値",
    "HIGH": "高値",
    "LOW": "安値",
    "CLOSE": "終値",
}

_TIMEFRAME_NAMES: Mapping[str, str] = {
    "1m": "1 分足",
    "15m": "15 分足",
    "1h": "1 時間足",
    "4h_ny17": "4 時間足",
    "1d_ny17": "日足",
}

_MARKET_REF = re.compile(
    r"^market:(?P<symbol>[A-Z]+)/(?P<tf>[^/]+)/(?P<side>[a-z]+):(?P<field>\w+)$"
)


# --- 集めた内容 -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunFacts:
    """実行の事実（成績ではない）。無いファイルは `None`。"""

    experiment_id: str | None = None
    git_commit: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    exit_code: str | None = None
    max_memory_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class VersionView:
    """1 つの版。`report` は走らせていない版では `None`。"""

    name: str
    version: int
    status: str
    status_label: str
    experiment: Mapping[str, Any]
    experiment_text: str
    strategy: Mapping[str, Any] | None
    strategy_text: str
    strategy_path: str
    facts: RunFacts = field(default_factory=RunFacts)
    verdict: str | None = None
    verdict_split: bool | None = None
    report: Document | None = None


@dataclass(slots=True)
class ExperimentView:
    name: str
    versions: list[VersionView] = field(default_factory=list)


class ViewerInputError(Exception):
    """表示の材料が読めない（その版を飛ばさず、止めて理由を出す）。"""


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def _load_yaml(text: str, where: str) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ViewerInputError(f"{where} を YAML として読めない: {exc}") from exc
    if not isinstance(data, dict):
        raise ViewerInputError(f"{where} の中身が対応表ではない")
    return data


def _max_memory(time_log: str | None) -> int | None:
    """`/usr/bin/time -l` の記録から最大メモリ（バイト）を読む。"""
    if time_log is None:
        return None
    match = re.search(r"^\s*(\d+)\s+maximum resident set size", time_log, re.MULTILINE)
    return int(match.group(1)) if match else None


#: 結果の欄に出さない前置きの段落の書き出し（2026-10-27 の人間の指示「保存済みの成果物だけから
#: 作った表示である〜の表示は無くす」）。`report.md` そのものは変えない。
_HIDDEN_PREAMBLE_PREFIX = "<p>保存済みの成果物"


def _report(text: str) -> Document:
    document = convert(text)
    document.preamble = "\n".join(
        block
        for block in document.preamble.split("\n")
        if not block.startswith(_HIDDEN_PREAMBLE_PREFIX)
    )
    return document


def _run_version(directory: Path) -> VersionView:
    """実行した（または実行を始めた）版を、版のディレクトリから読む。"""
    manifest_path = directory / "experiment_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ViewerInputError(f"{manifest_path} を読めない: {exc}") from exc
    texts = {item.get("role"): item.get("text", "") for item in manifest.get("resolved_files", [])}
    experiment_text = texts.get("experiment") or ""
    strategy_text = texts.get("strategy") or ""
    experiment = _load_yaml(experiment_text, f"{manifest_path} の実験設定")
    strategy = _load_yaml(strategy_text, f"{manifest_path} の戦略宣言") if strategy_text else None

    outcome_path = directory / "experiment_outcome.json"
    outcome: Mapping[str, Any] | None = None
    if outcome_path.exists():
        try:
            outcome = json.loads(outcome_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ViewerInputError(f"{outcome_path} を読めない: {exc}") from exc
    exit_code = _read_text(directory / "exit_code.txt")
    if outcome is not None:
        status, label = "done", "実行済み"
    elif exit_code is None:
        status, label = "running", "実行中、または途中で止まった"
    else:
        status, label = "stopped", f"途中で止まった（終了コード {exit_code}）"

    combined: Mapping[str, Any] = ((outcome or {}).get("search") or {}).get("combined") or {}
    report_text = _read_text(directory / "report.md")
    facts = RunFacts(
        experiment_id=manifest.get("experiment_id"),
        git_commit=manifest.get("git_commit"),
        started_at=_read_text(directory / "started_at_utc.txt"),
        finished_at=_read_text(directory / "finished_at_utc.txt"),
        exit_code=exit_code,
        max_memory_bytes=_max_memory(_read_text(directory / "time.log")),
    )
    return VersionView(
        name=str(manifest.get("experiment_name") or experiment.get("id")),
        version=int(manifest.get("experiment_version") or experiment.get("version") or 0),
        status=status,
        status_label=label,
        experiment=experiment,
        experiment_text=experiment_text,
        strategy=strategy,
        strategy_text=strategy_text,
        strategy_path=str(experiment.get("strategy", "")),
        facts=facts,
        verdict=combined.get("verdict"),
        verdict_split=combined.get("split"),
        report=_report(report_text) if report_text else None,
    )


def _registered_versions(
    repo_root: Path, names: set[str], seen: set[tuple[str, int]]
) -> list[VersionView]:
    """まだ走らせていない版（実験設定だけがある版）を集める。対象は既に実行のある実験名だけ。"""
    found: list[VersionView] = []
    for path in sorted((repo_root / "configs" / "experiments").glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict) or data.get("schema_version") != 2:
            continue
        name, version = data.get("id"), data.get("version")
        if name not in names or not isinstance(version, int) or (name, version) in seen:
            continue
        strategy_rel = str(data.get("strategy", ""))
        strategy_text = _read_text(repo_root / strategy_rel) or ""
        found.append(
            VersionView(
                name=str(name),
                version=version,
                status="registered",
                status_label="事前登録のみ（未実行）",
                experiment=data,
                experiment_text=text,
                strategy=_load_yaml(strategy_text, strategy_rel) if strategy_text else None,
                strategy_text=strategy_text,
                strategy_path=strategy_rel,
            )
        )
    return found


def collect(runs_root: Path, repo_root: Path) -> list[ExperimentView]:
    """実験の一覧を、名前の順・版の順に集める。"""
    experiments: dict[str, ExperimentView] = {}
    base = runs_root / "runs" / "experiments"
    for directory in sorted(base.glob("*/v*")):
        if not (directory / "experiment_manifest.json").exists():
            continue
        view = _run_version(directory)
        experiments.setdefault(view.name, ExperimentView(view.name)).versions.append(view)
    seen = {(view.name, view.version) for exp in experiments.values() for view in exp.versions}
    for view in _registered_versions(repo_root, set(experiments), seen):
        experiments[view.name].versions.append(view)
    for exp in experiments.values():
        exp.versions.sort(key=lambda item: item.version)
    return [experiments[name] for name in sorted(experiments)]


# --- 戦略の内容の表示 -----------------------------------------------------------------


def _esc(value: object) -> str:
    return html.escape(str(value))


def _join_wrapped(previous: str, line: str) -> str:
    """折り返された行をつなぐ。日本語どうしは空白を入れない。"""
    if not previous:
        return line
    if previous[-1].isascii() and line[:1].isascii():
        return previous + " " + line
    return previous + line


def header_comment(text: str) -> list[str]:
    """YAML の先頭の注記（`#` の行）を、段落と箇条の並びにする（各要素は 1 つの段落か箇条）。"""
    blocks: list[str] = []
    current = ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if blocks or current:
                break
            continue
        if not stripped.startswith("#"):
            break
        body = stripped[1:]
        body = body[1:] if body.startswith(" ") else body
        if not body.strip():
            if current:
                blocks.append(current)
            current = ""
        elif body.startswith("- "):
            if current:
                blocks.append(current)
            current = body
        elif body.startswith(" ") and current:
            current = _join_wrapped(current, body.strip())
        else:
            current = _join_wrapped(current, body.strip())
    if current:
        blocks.append(current)
    return blocks


def _inline_code(text: str) -> str:
    parts = text.split("`")
    return "".join(
        f"<code>{_esc(part)}</code>" if index % 2 == 1 and index < len(parts) - 1 else _esc(part)
        for index, part in enumerate(parts)
    )


def _comment_html(text: str) -> str:
    blocks = header_comment(text)
    if not blocks:
        return ""
    out: list[str] = []
    items: list[str] = []
    for block in blocks:
        if block.startswith("- "):
            items.append(f"<li>{_inline_code(block[2:])}</li>")
            continue
        if items:
            out.append("<ul>" + "".join(items) + "</ul>")
            items = []
        out.append(f"<p>{_inline_code(block)}</p>")
    if items:
        out.append("<ul>" + "".join(items) + "</ul>")
    return "".join(out)


def describe_ref(ref: str) -> str:
    """入力の参照を読める形にする（例: `market:USDJPY/1h/bid:CLOSE` → 1 時間足の終値）。"""
    market = _MARKET_REF.match(ref)
    if market:
        timeframe = _TIMEFRAME_NAMES.get(market["tf"], market["tf"])
        field_name = _FIELD_NAMES.get(market["field"], market["field"])
        return f"{timeframe}の{field_name}（{market['symbol']} {market['side']}）"
    if ref.startswith("output:"):
        instance, _, port = ref[len("output:") :].partition(".")
        return f"部品 {instance} の出力 {port}"
    if ref == "runtime:POSITION":
        return "保有中のポジション"
    return ref


def _trigger_text(trigger: Mapping[str, Any]) -> str:
    when = trigger.get("when")
    if when == "bar_close":
        series = str(trigger.get("series", ""))
        parts = series.split("/")
        timeframe = _TIMEFRAME_NAMES.get(parts[1], parts[1]) if len(parts) > 1 else series
        return f"{timeframe}が確定するたび"
    if when == "input_event":
        return f"入力 {trigger.get('input_name')} が届いたとき"
    if when == "runtime_event":
        return f"{trigger.get('event')} のとき"
    return str(trigger)


def _axis_values(experiment: Mapping[str, Any]) -> dict[tuple[str, str], list[Any]]:
    plan = experiment.get("search_plan")
    if not isinstance(plan, dict):
        return {}
    return {
        (str(axis.get("instance")), str(axis.get("parameter"))): list(axis.get("values") or [])
        for axis in plan.get("axes") or []
    }


def _values_text(values: Sequence[Any]) -> str:
    return " / ".join(str(value) for value in values)


def _components_table(
    strategy: Mapping[str, Any], axes: Mapping[tuple[str, str], list[Any]]
) -> str:
    rows: list[str] = []
    for component in strategy.get("components") or []:
        instance = str(component.get("instance_id"))
        inputs = "".join(
            f"<div><span class='muted'>{_esc(port)}</span> ← "
            + "、".join(
                f"<span title='{_esc(ref)}'>{_esc(describe_ref(ref))}</span>" for ref in refs
            )
            + "</div>"
            for port, refs in (component.get("inputs") or {}).items()
        )
        params: list[str] = []
        for key, spec in (component.get("parameters") or {}).items():
            value = spec.get("value") if isinstance(spec, dict) else spec
            axis = axes.get((instance, str(key)))
            if axis is not None:
                params.append(
                    f"<div><span class='muted'>{_esc(key)}</span> = "
                    f"<span class='axis'>探索 {_esc(_values_text(axis))}</span></div>"
                )
            else:
                params.append(f"<div><span class='muted'>{_esc(key)}</span> = {_esc(value)}</div>")
        triggers = "".join(
            f"<div>{_esc(_trigger_text(item))}</div>" for item in component.get("triggers") or []
        )
        rows.append(
            "<tr>"
            f"<td><code>{_esc(instance)}</code></td>"
            f"<td>{_esc(component.get('component'))} <span class='muted'>v"
            f"{_esc(component.get('component_version'))}</span></td>"
            f"<td>{inputs or '<span class=muted>なし</span>'}</td>"
            f"<td>{''.join(params) or '<span class=muted>なし</span>'}</td>"
            f"<td>{triggers}</td>"
            "</tr>"
        )
    return (
        "<div class='table-wrap'><table><thead><tr><th>部品</th><th>種類</th><th>入力</th>"
        "<th>パラメータ</th><th>動くとき</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _roles_table(strategy: Mapping[str, Any]) -> str:
    rows = "".join(
        f"<tr><td>{_esc(ROLE_NAMES.get(role, role))}</td><td><code>{_esc(output)}</code></td></tr>"
        for role, output in (strategy.get("roles") or {}).items()
    )
    return (
        "<div class='table-wrap'><table><thead><tr><th>役割</th><th>担う出力</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _axes_table(experiment: Mapping[str, Any]) -> str:
    axes = _axis_values(experiment)
    if not axes:
        return "<p class='muted'>探索の軸はない。</p>"
    plan = experiment.get("search_plan") or {}
    rows = "".join(
        f"<tr><td><code>{_esc(instance)}.{_esc(parameter)}</code></td>"
        f"<td>{_esc(_values_text(values))}</td></tr>"
        for (instance, parameter), values in axes.items()
    )
    return (
        f"<p>格子（{_esc(plan.get('kind'))}）・試行数の上限 {_esc(plan.get('max_trials'))}</p>"
        "<div class='table-wrap'><table><thead><tr><th>軸（部品.パラメータ）</th><th>値</th></tr>"
        f"</thead><tbody>{rows}</tbody></table></div>"
    )


def _decimal_text(value: object) -> str:
    """設定の数の文字を、指数や末尾の 0 を付けずに出す（読めなければそのまま）。"""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    return format(number.normalize(), "f")


def _grouped(value: object) -> str:
    """金額を 3 桁ごとに区切る（例: 1000000 → 1,000,000）。"""
    try:
        number = Decimal(str(value)).normalize()
    except InvalidOperation:
        return str(value)
    return f"{number:,f}"


def _percent(value: object) -> str:
    try:
        return _decimal_text(Decimal(str(value)) * 100) + "%"
    except InvalidOperation:
        return str(value)


def _duration_text(value: object) -> str:
    """ISO 8601 の時間の長さ（`PT20M` など）を日本語にする。"""
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(value))
    if not match or not any(match.groups()):
        return str(value)
    units = ("時間", "分", "秒")
    return " ".join(
        f"{number} {unit}" for number, unit in zip(match.groups(), units, strict=True) if number
    )


@dataclass(frozen=True, slots=True)
class _Market:
    """条件の表示に使う、執行の系列から読む値（通貨の表示と pip の大きさ）。"""

    timeframe: str
    price_unit: str
    pip: Decimal | None

    def price(self, value: object) -> str:
        text = f"{_decimal_text(value)} {self.price_unit}"
        if self.pip is None:
            return text
        try:
            pips = Decimal(str(value)) / self.pip
        except InvalidOperation:
            return text
        return f"{text}（{_decimal_text(pips)} pip）"


def _market(experiment: Mapping[str, Any]) -> _Market:
    parts = str(experiment.get("execution_series", "")).split("/")
    symbol = parts[0] if parts else ""
    timeframe = _TIMEFRAME_NAMES.get(parts[1], parts[1]) if len(parts) > 1 else "執行足"
    quote = symbol[3:] if len(symbol) == 6 else ""
    # pip の大きさは円の組で 0.01、それ以外で 0.0001（銘柄仕様の `pip_size` と同じ慣行）。
    pip = Decimal("0.01") if quote == "JPY" else Decimal("0.0001") if quote else None
    return _Market(timeframe, "円" if quote == "JPY" else quote or "価格", pip)


#: 実験の条件の表（2026-10-27 に人間と確認した項目名と意味）。値の書き方は関数で決める。
_ConditionRow = tuple[str, str, Callable[[Any, Mapping[str, Any], _Market], tuple[str, str]]]


def _money(value: Any, account: Mapping[str, Any]) -> str:
    currency = account.get("currency")
    return f"{_grouped(value)} {'円' if currency == 'JPY' else currency}"


_CONDITION_ROWS: Mapping[str, Sequence[_ConditionRow]] = {
    "account": (
        ("account_id", "口座名", lambda v, a, m: (str(v), "記録上の識別用。結果には影響しない")),
        (
            "currency",
            "口座の通貨",
            lambda v, a, m: ("円（JPY）" if v == "JPY" else str(v), "損益と残高を数える通貨"),
        ),
        (
            "initial_balance",
            "初期残高",
            lambda v, a, m: (_money(v, a), "各 fold の開始時の口座残高"),
        ),
    ),
    "risk_policy": (
        (
            "trial_risk_rate",
            "1取引のリスク上限",
            lambda v, a, m: (
                f"残高の {_percent(v)}",
                "損切りまでに失ってよい額。これが取引量の上限を決める",
            ),
        ),
        (
            "account_risk_cap",
            "口座全体のリスク上限",
            lambda v, a, m: (f"残高の {_percent(v)}", "同時に抱えるリスクの合計の上限"),
        ),
    ),
    "cost_model": (
        (
            "commission_per_unit",
            "手数料",
            lambda v, a, m: (
                f"1 通貨あたり {_money(v, a)}（片道）",
                "入るときと出るときにそれぞれかかる。1 万通貨なら片道 "
                f"{_money(Decimal(str(v)) * 10000, a)}",
            ),
        ),
        (
            "spread_offset",
            "スプレッド",
            lambda v, a, m: (
                f"{m.price(v)}固定",
                f"買値＝売値＋{_decimal_text(v)} {m.price_unit}",
            ),
        ),
        (
            "entry_slippage",
            "入るときの約定ずれ",
            lambda v, a, m: (
                m.price(v),
                f"約定価格を不利な向きに {_decimal_text(v)} {m.price_unit}ずらす",
            ),
        ),
        (
            "close_slippage",
            "出るときの約定ずれ",
            lambda v, a, m: (
                m.price(v),
                f"決済・損切りの約定価格を不利な向きに {_decimal_text(v)} {m.price_unit}ずらす",
            ),
        ),
    ),
    "execution_policy": (
        (
            "entry_delay_bars",
            "約定の見送り本数",
            lambda v, a, m: (
                f"{v} 本",
                f"注文を受け付けた後、最初の{m.timeframe}の始値で約定する"
                if v == 0
                else f"注文を受け付けた後の始値を {v} 本見送ってから約定する",
            ),
        ),
        (
            "entry_valid_for",
            "新規注文の有効時間",
            lambda v, a, m: (_duration_text(v), "この時間内に約定しなければ注文は消える"),
        ),
        (
            "close_valid_for",
            "決済注文の有効時間",
            lambda v, a, m: (
                _duration_text(v),
                "この時間内に約定しなければ注文は消える（決済注文）",
            ),
        ),
        (
            "adverse_fill_limits",
            "不利な約定の見込み幅",
            lambda v, a, m: (
                "、".join(f"{symbol} {m.price(width)}" for symbol, width in dict(v).items())
                if isinstance(v, dict)
                else str(v),
                "注文時の価格からこの幅まで不利に約定しうると見込んで、取引量を決める",
            ),
        ),
        (
            "reference_quote_source",
            "注文時の基準価格",
            lambda v, a, m: (
                f"直前に確定した{m.timeframe}の終値"
                if v == "EXECUTION_SERIES_LAST_CLOSE"
                else str(v),
                "上の「不利な約定の見込み幅」を測る起点になる価格",
            ),
        ),
    ),
}

_CONDITION_GROUPS: Mapping[str, str] = {
    "account": "口座",
    "risk_policy": "リスク",
    "cost_model": "費用",
    "execution_policy": "執行",
}


def _conditions_table(experiment: Mapping[str, Any]) -> str:
    """口座・リスク・費用・執行など、比較の前提として全実験で揃える条件。"""
    policy = experiment.get("research_policy") or {}
    head_rows: list[tuple[str, str]] = [
        ("研究ポリシー", f"{policy.get('id')} 第 {policy.get('version')} 版"),
        ("執行の系列", str(experiment.get("execution_series"))),
        ("分割", str(experiment.get("split"))),
        ("最終検証", str(experiment.get("final_holdout"))),
    ]
    head = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in head_rows)
    account = experiment.get("account") or {}
    market = _market(experiment)
    rows: list[str] = []
    for group, group_label in _CONDITION_GROUPS.items():
        values = experiment.get(group)
        if not isinstance(values, dict):
            continue
        known = {key for key, _, _ in _CONDITION_ROWS[group]}
        cells: list[tuple[str, str, str, str]] = []
        for key, label, describe in _CONDITION_ROWS[group]:
            if key in values:
                value, meaning = describe(values[key], account, market)
                cells.append((label, key, value, meaning))
        # 表に無い項目は、元の名前と値のまま出す（黙って落とさない）。
        cells.extend(
            (key, key, str(value), "") for key, value in values.items() if key not in known
        )
        for index, (label, key, value, meaning) in enumerate(cells):
            group_cell = (
                f"<th rowspan='{len(cells)}' class='group'>{_esc(group_label)}</th>"
                if index == 0
                else ""
            )
            rows.append(
                f"<tr>{group_cell}<td title='{_esc(key)}'>{_esc(label)}</td>"
                f"<td>{_esc(value)}</td><td class='muted'>{_esc(meaning)}</td></tr>"
            )
    return (
        f"<div class='table-wrap'><table class='kv'><tbody>{head}</tbody></table></div>"
        "<div class='table-wrap'><table class='conditions'><thead><tr><th>区分</th><th>項目</th>"
        f"<th>値</th><th>意味</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    )


def _yaml_details(title: str, text: str) -> str:
    return f"<details class='raw'><summary>{_esc(title)}</summary><pre>{_esc(text)}</pre></details>"


def strategy_html(view: VersionView) -> str:
    """「戦略」の欄。仮説・戦略の説明・部品・役割・探索の軸・条件・元のファイル。"""
    out = [
        "<section class='card'><h3>仮説</h3>"
        f"<p class='hypothesis'>{_esc(view.experiment.get('hypothesis', ''))}</p></section>"
    ]
    if view.strategy is not None:
        strategy = view.strategy
        out.append(
            "<section class='card'><h3>戦略の説明"
            f" <span class='muted'>{_esc(strategy.get('strategy_id'))} 版 "
            f"{_esc(strategy.get('version'))}</span></h3>"
            f"{_comment_html(view.strategy_text)}</section>"
        )
        out.append(
            "<section class='card'><h3>部品の組み合わせ</h3>"
            f"{_components_table(strategy, _axis_values(view.experiment))}"
            f"<h4>役割</h4>{_roles_table(strategy)}</section>"
        )
    else:
        out.append(
            f"<section class='card'><p class='muted'>戦略宣言 {_esc(view.strategy_path)} を"
            "読めなかった。</p></section>"
        )
    out.append(f"<section class='card'><h3>探索する軸</h3>{_axes_table(view.experiment)}</section>")
    out.append(
        "<section class='card'><h3>実験の条件（全実験で揃える前提）</h3>"
        f"{_conditions_table(view.experiment)}"
        f"{_yaml_details('戦略宣言の全文（' + view.strategy_path + '）', view.strategy_text)}"
        f"{_yaml_details('実験設定の全文', view.experiment_text)}</section>"
    )
    return "".join(out)


# --- 版の違い -------------------------------------------------------------------------


def _without_comments(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        body = line.split(" #", 1)[0].rstrip()
        if body:
            lines.append(body)
    return lines


def diff_html(before: str, after: str) -> str:
    """注記の行を除いた差分。同じなら空。"""
    lines = list(
        difflib.unified_diff(_without_comments(before), _without_comments(after), lineterm="", n=2)
    )[2:]
    if not lines:
        return ""
    out: list[str] = []
    for line in lines:
        kind = {"+": "add", "-": "del", "@": "hunk"}.get(line[:1], "ctx")
        out.append(f"<span class='{kind}'>{_esc(line)}</span>")
    return "<pre class='diff'>" + "\n".join(out) + "</pre>"


def change_html(view: VersionView, previous: VersionView | None) -> str:
    """「前の版から何を変えたか」。系譜と、軸・戦略宣言・実験設定の違い。"""
    if previous is None:
        return ""
    lineage = view.experiment.get("lineage")
    out = [f"<section class='card change'><h3>版 {previous.version} からの変更</h3>"]
    if isinstance(lineage, dict):
        kinds = "、".join(
            CHANGE_KIND_NAMES.get(str(kind), str(kind)) + f"（{kind}）"
            for kind in lineage.get("change_kinds") or []
        )
        source = lineage.get("derived_from")
        if isinstance(source, dict):
            source_text = (
                f"実験 {str(source.get('experiment_id', ''))[:12]}… の実行番号 "
                f"{source.get('execution')}"
            )
        else:
            source_text = str(source)
        out.append(
            "<table class='kv'><tbody>"
            f"<tr><th>変更の種類</th><td>{_esc(kinds)}</td></tr>"
            f"<tr><th>変更元</th><td>{_esc(source_text)}</td></tr>"
            f"<tr><th>理由</th><td>{_esc(lineage.get('note', ''))}</td></tr>"
            "</tbody></table>"
        )
    else:
        out.append("<p class='muted'>実験設定に系譜（変更元と理由）が書かれていない。</p>")
    before_axes, after_axes = _axis_values(previous.experiment), _axis_values(view.experiment)
    rows = []
    for key in list(dict.fromkeys([*before_axes, *after_axes])):
        old, new = before_axes.get(key), after_axes.get(key)
        mark = "" if old == new else " class='changed'"
        rows.append(
            f"<tr{mark}><td><code>{_esc(key[0])}.{_esc(key[1])}</code></td>"
            f"<td>{_esc(_values_text(old)) if old is not None else '―'}</td>"
            f"<td>{_esc(_values_text(new)) if new is not None else '―'}</td></tr>"
        )
    out.append(
        "<h4>探索する軸</h4><div class='table-wrap'><table><thead><tr><th>軸</th>"
        f"<th>版 {previous.version}</th><th>版 {view.version}</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )
    strategy_diff = diff_html(previous.strategy_text, view.strategy_text)
    out.append(
        "<h4>戦略宣言</h4>" + (strategy_diff or "<p>変わっていない（注記の行を除いて同じ）。</p>")
    )
    experiment_diff = diff_html(previous.experiment_text, view.experiment_text)
    if experiment_diff:
        out.append(
            "<details class='raw'><summary>実験設定の差分（注記の行を除く）</summary>"
            f"{experiment_diff}</details>"
        )
    out.append("</section>")
    return "".join(out)


# --- 実行の事実・結果 ------------------------------------------------------------------


def _memory_text(value: int | None) -> str | None:
    return None if value is None else f"{value / 1024**3:.2f} GiB"


def facts_html(view: VersionView) -> str:
    facts = view.facts
    rows = [
        ("状態", view.status_label),
        ("実験の識別子", (facts.experiment_id or "")[:16] + "…" if facts.experiment_id else None),
        ("コード", (facts.git_commit or "")[:7] or None),
        ("開始（UTC）", facts.started_at),
        ("終了（UTC）", facts.finished_at),
        ("終了コード", facts.exit_code),
        ("最大メモリ", _memory_text(facts.max_memory_bytes)),
    ]
    body = "".join(
        f"<tr><th>{_esc(k)}</th><td>{_esc(v)}</td></tr>" for k, v in rows if v is not None
    )
    return f"<table class='kv'><tbody>{body}</tbody></table>"


def verdict_badge(view: VersionView) -> str:
    if view.verdict is None:
        return f"<span class='badge none'>{_esc(view.status_label)}</span>"
    tone = VERDICT_TONES.get(view.verdict, "warn")
    label = VERDICT_NAMES.get(view.verdict, view.verdict)
    split = "<span class='badge warn'>両組で判定が割れた</span>" if view.verdict_split else ""
    return f"<span class='badge {tone}'>{_esc(label)}</span>{split}"


# --- 画面 -----------------------------------------------------------------------------


def _version_payload(view: VersionView, previous: VersionView | None) -> dict[str, Any]:
    report = view.report
    return {
        "version": view.version,
        "status": view.status,
        "statusLabel": view.status_label,
        "badge": verdict_badge(view),
        "verdict": VERDICT_NAMES.get(view.verdict, view.verdict) if view.verdict else None,
        "previous": previous.version if previous else None,
        "strategy": strategy_html(view),
        "change": change_html(view, previous),
        "facts": facts_html(view),
        "reportPreamble": report.preamble if report else "",
        "sections": [{"title": s.title, "html": s.html} for s in report.sections] if report else [],
    }


def page_data(experiments: Sequence[ExperimentView]) -> dict[str, Any]:
    payload = []
    for exp in experiments:
        versions = []
        for index, view in enumerate(exp.versions):
            previous = exp.versions[index - 1] if index > 0 else None
            versions.append(_version_payload(view, previous))
        payload.append(
            {
                "name": exp.name,
                "hypothesis": str(exp.versions[-1].experiment.get("hypothesis", "")),
                "versions": versions,
            }
        )
    return {"experiments": payload}


def render_page(experiments: Sequence[ExperimentView], generated_at: str) -> str:
    data = json.dumps(page_data(experiments), ensure_ascii=False).replace("</", "<\\/")
    return _PAGE.replace("__GENERATED_AT__", _esc(generated_at)).replace("__DATA__", data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("."),
        help="`experiment run --out` に渡した置き場（その下の runs/experiments/ を読む）",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="未実行の版の実験設定を探すリポジトリ（configs/experiments/ を読む）",
    )
    parser.add_argument(
        "--out", type=Path, help="出力の HTML（既定: <runs-root>/runs/viewer/index.html）"
    )
    parser.add_argument("--open", action="store_true", help="書き出した後にブラウザで開く（macOS）")
    args = parser.parse_args(argv)

    try:
        experiments = collect(args.runs_root, args.repo_root)
    except ViewerInputError as exc:
        print(f"画面の材料を読めない: {exc}", file=sys.stderr)
        return 1
    out = args.out or args.runs_root / "runs" / "viewer" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M")
    out.write_text(render_page(experiments, generated_at), encoding="utf-8")
    count = sum(len(exp.versions) for exp in experiments)
    # 成績の数値は出さない（置き場と件数だけ）。
    print(f"研究の画面を書き出した: {out.resolve()}（実験 {len(experiments)} 件・版 {count} 件）")
    if args.open and sys.platform == "darwin":
        subprocess.run(["open", str(out)], check=False)
    return 0


_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>研究の画面</title>
<style>
:root {
  --bg: #f6f7f9; --panel: #ffffff; --text: #1d2330; --muted: #677085; --line: #dfe3ea;
  --accent: #2f6fdb; --accent-soft: #e8f0fd; --code: #f0f2f5;
  --good: #1f7a4d; --good-soft: #e3f4ea; --bad: #b42318; --bad-soft: #fde8e6;
  --warn: #9a5b00; --warn-soft: #fdf1dc; --none-soft: #eceff3;
  --add: #e3f4ea; --del: #fde8e6; --changed: #fff6d6;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #14171d; --panel: #1c2028; --text: #e4e7ee; --muted: #9aa3b5; --line: #2e3442;
    --accent: #7aa7ff; --accent-soft: #22304a; --code: #262b35;
    --good: #6fd29b; --good-soft: #17352a; --bad: #ff8a80; --bad-soft: #3d1d1b;
    --warn: #f0b860; --warn-soft: #3a2d14; --none-soft: #2a2f3a;
    --add: #17352a; --del: #3d1d1b; --changed: #3a3214;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; background: var(--bg); color: var(--text); }
body {
  font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Hiragino Sans", "Hiragino Kaku Gothic ProN",
    "Noto Sans JP", sans-serif;
  display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 100vh;
}
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
code { background: var(--code); padding: 1px 4px; border-radius: 4px; }
nav { background: var(--panel); border-right: 1px solid var(--line); padding: 16px 0;
  position: sticky; top: 0; height: 100vh; overflow-y: auto; }
nav h1 { font-size: 15px; margin: 0 16px 4px; }
nav .generated { color: var(--muted); font-size: 12px; margin: 0 16px 16px; }
nav .exp { margin-bottom: 12px; }
nav .exp-name { font-weight: 600; font-size: 13px; padding: 4px 16px; word-break: break-all; }
nav a.ver { display: flex; gap: 8px; align-items: center; padding: 6px 16px 6px 24px;
  color: var(--text); text-decoration: none; font-size: 13px; }
nav a.ver:hover { background: var(--accent-soft); }
nav a.ver.active { background: var(--accent-soft); color: var(--accent); font-weight: 600;
  box-shadow: inset 3px 0 0 var(--accent); }
.dot { width: 9px; height: 9px; border-radius: 50%; flex: none; background: var(--muted); }
.dot.good { background: var(--good); } .dot.bad { background: var(--bad); }
.dot.warn { background: var(--warn); } .dot.registered { background: transparent;
  border: 2px solid var(--muted); }
main { padding: 24px 32px 64px; min-width: 0; }
header.top h2 { margin: 0 0 4px; font-size: 20px; }
header.top .sub { color: var(--muted); margin: 0 0 12px; }
.badges { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 16px; }
.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12.5px;
  font-weight: 600; }
.badge.good { background: var(--good-soft); color: var(--good); }
.badge.bad { background: var(--bad-soft); color: var(--bad); }
.badge.warn { background: var(--warn-soft); color: var(--warn); }
.badge.none { background: var(--none-soft); color: var(--muted); }
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin-bottom: 20px; }
.tabs button { border: 0; background: none; color: var(--muted); font: inherit; font-weight: 600;
  padding: 8px 14px; cursor: pointer; border-bottom: 2px solid transparent; }
.tabs button.active { color: var(--accent); border-bottom-color: var(--accent); }
.tabs button:disabled { opacity: .4; cursor: default; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px 20px; margin-bottom: 16px; }
.card h3 { margin: 0 0 10px; font-size: 15px; }
.card h4 { margin: 16px 0 8px; font-size: 13.5px; }
.card.change { border-color: var(--accent); }
.hypothesis { font-size: 15px; margin: 0; }
.muted { color: var(--muted); }
.axis { background: var(--changed); padding: 0 6px; border-radius: 4px; font-weight: 600; }
.table-wrap { overflow-x: auto; margin: 8px 0; }
table { border-collapse: collapse; font-size: 13px; min-width: 50%; }
th, td { border: 1px solid var(--line); padding: 5px 9px; text-align: left; vertical-align: top; }
thead th { background: var(--code); position: sticky; top: 0; white-space: nowrap; }
tbody tr:nth-child(even) td { background: color-mix(in srgb, var(--code) 45%, transparent); }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
table.kv th { background: var(--code); white-space: nowrap; width: 1%; }
table.conditions th.group { background: var(--code); white-space: nowrap; }
table.conditions td:nth-child(2), table.conditions td:first-child { white-space: nowrap; }
tr.changed td { background: var(--changed) !important; font-weight: 600; }
details.sec { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  margin-bottom: 12px; }
details.sec > summary { cursor: pointer; padding: 12px 20px; font-weight: 700; font-size: 15px; }
details.sec > .body { padding: 0 20px 16px; }
details.sec h3 { font-size: 14.5px; margin: 18px 0 6px; padding-top: 10px;
  border-top: 1px dashed var(--line); }
details.sec h4, details.sec h5 { font-size: 13.5px; margin: 14px 0 6px; }
details.raw { margin-top: 12px; }
details.raw summary { cursor: pointer; color: var(--accent); }
pre { background: var(--code); padding: 12px; border-radius: 8px; overflow-x: auto; }
pre.diff span { display: block; } pre.diff .add { background: var(--add); }
pre.diff .del { background: var(--del); } pre.diff .hunk { color: var(--muted); }
.toc { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
.toc a { font-size: 12.5px; padding: 3px 10px; border: 1px solid var(--line); border-radius: 999px;
  color: var(--text); text-decoration: none; background: var(--panel); }
.toc a:hover { border-color: var(--accent); color: var(--accent); }
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; flex-wrap: wrap; }
.toolbar select, .toolbar button { font: inherit; padding: 4px 8px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--panel); color: var(--text); }
.compare { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 12px; }
.compare > div { min-width: 0; }
.compare .colhead { font-weight: 700; margin-bottom: 4px; }
.empty { color: var(--muted); padding: 24px; text-align: center; }
@media (max-width: 800px) {
  body { grid-template-columns: 1fr; }
  nav { position: static; height: auto; }
  main { padding: 16px; }
  .compare { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<nav id="nav"><h1>研究の画面</h1><p class="generated">作成 __GENERATED_AT__</p></nav>
<main id="main"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
(() => {
  const data = JSON.parse(document.getElementById("data").textContent);
  const nav = document.getElementById("nav");
  const main = document.getElementById("main");
  const tone = (v) => {
    const m = v.badge.match(/badge (good|bad|warn)/);
    return v.status === "registered" ? "registered" : (m ? m[1] : "");
  };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  for (const exp of data.experiments) {
    const box = document.createElement("div");
    box.className = "exp";
    box.innerHTML = `<div class="exp-name">${esc(exp.name)}</div>` + exp.versions.map((v) =>
      `<a class="ver" href="#${encodeURIComponent(exp.name)}/${v.version}/strategy"
         data-key="${esc(exp.name)}/${v.version}"><span class="dot ${tone(v)}"></span>
         版 ${v.version}<span class="muted">${esc(v.verdict || v.statusLabel)}</span></a>`
    ).join("");
    nav.appendChild(box);
  }

  function route() {
    const [rawName, rawVer, rawTab] = location.hash.slice(1).split("/");
    let exp = data.experiments.find((e) => e.name === decodeURIComponent(rawName || ""));
    exp = exp || data.experiments[0];
    if (!exp) { main.innerHTML = '<p class="empty">表示できる実験がない。</p>'; return; }
    const ver = exp.versions.find((v) => String(v.version) === rawVer) || exp.versions.at(-1);
    const tab = ["strategy", "results", "compare"].includes(rawTab) ? rawTab : "strategy";
    for (const a of nav.querySelectorAll("a.ver")) {
      a.classList.toggle("active", a.dataset.key === `${exp.name}/${ver.version}`);
    }
    render(exp, ver, tab);
  }

  function link(exp, ver, tab) { return `#${encodeURIComponent(exp.name)}/${ver.version}/${tab}`; }

  function render(exp, ver, tab) {
    const hasReport = ver.sections.length > 0;
    const canCompare = exp.versions.length > 1;
    const tabs = [["strategy", "戦略"], ["results", "結果"], ["compare", "版の比較"]];
    main.innerHTML = `
      <header class="top"><h2>${esc(exp.name)} — 版 ${ver.version}</h2>
        <div class="badges">${ver.badge}</div></header>
      <div class="tabs">${tabs.map(([key, label]) => {
        const disabled = (key === "results" && !hasReport) || (key === "compare" && !canCompare);
        return `<button data-tab="${key}" class="${key === tab ? "active" : ""}"
          ${disabled ? "disabled" : ""}>${label}</button>`;
      }).join("")}</div>
      <div id="pane"></div>`;
    for (const b of main.querySelectorAll(".tabs button")) {
      b.addEventListener("click", () => { location.hash = link(exp, ver, b.dataset.tab); });
    }
    const pane = document.getElementById("pane");
    if (tab === "results" && hasReport) pane.innerHTML = resultsHtml(ver);
    else if (tab === "compare" && canCompare) renderCompare(pane, exp, ver);
    else pane.innerHTML = ver.change + ver.strategy +
      `<section class="card"><h3>実行の記録</h3>${ver.facts}</section>`;
    window.scrollTo(0, 0);
  }

  function sectionsHtml(ver, prefix) {
    return ver.sections.map((s, i) => `<details class="sec" open id="${prefix}-${i}">
      <summary>${esc(s.title)}</summary><div class="body">${s.html}</div></details>`).join("");
  }

  function resultsHtml(ver) {
    const toc = ver.sections.map((s, i) => `<a href="javascript:void(0)"
      data-target="r-${i}">${esc(s.title)}</a>`).join("");
    setTimeout(() => {
      for (const a of main.querySelectorAll(".toc a")) {
        a.addEventListener("click", () => {
          const el = document.getElementById(a.dataset.target);
          el.open = true; el.scrollIntoView({behavior: "smooth", block: "start"});
        });
      }
      const toggle = main.querySelector("#toggle-all");
      toggle.addEventListener("click", () => {
        const all = [...main.querySelectorAll("details.sec")];
        const open = !all.every((d) => d.open);
        all.forEach((d) => { d.open = open; });
      });
    });
    return `<div class="toolbar"><div class="toc">${toc}</div>
        <button id="toggle-all">すべて開く／閉じる</button></div>
      ${ver.reportPreamble ? `<div class="card">${ver.reportPreamble}</div>` : ""}
      ${sectionsHtml(ver, "r")}`;
  }

  function renderCompare(pane, exp, ver) {
    const options = (selected) => exp.versions.map((v) =>
      `<option value="${v.version}" ${v.version === selected ? "selected" : ""}>版 ${v.version}
       （${esc(v.verdict || v.statusLabel)}）</option>`).join("");
    const right = ver;
    const left = exp.versions.find((v) => v.version === ver.previous) ||
      exp.versions.find((v) => v.version !== ver.version);
    pane.innerHTML = `<div class="toolbar">
      左 <select id="cmp-left">${options(left.version)}</select>
      右 <select id="cmp-right">${options(right.version)}</select></div>
      <div id="cmp-body"></div>`;
    const pick = (id) => {
      const value = pane.querySelector(id).value;
      return exp.versions.find((v) => String(v.version) === value);
    };
    const draw = () => {
      const a = pick("#cmp-left");
      const b = pick("#cmp-right");
      const titles = [...new Set([...a.sections, ...b.sections].map((s) => s.title))];
      const cell = (v, title) => {
        const s = v.sections.find((x) => x.title === title);
        if (s) return s.html;
        return `<p class="muted">${v.sections.length ? "この節は無い" : esc(v.statusLabel)}</p>`;
      };
      const pair = (left, right) => `<div class="body compare">
          <div><div class="colhead">版 ${a.version}</div>${left}</div>
          <div><div class="colhead">版 ${b.version}</div>${right}</div></div>`;
      let change = "";
      if (b.previous === a.version) change = b.change;
      else if (a.previous === b.version) change = a.change;
      pane.querySelector("#cmp-body").innerHTML = change +
        `<details class="sec" open><summary>判定</summary>${pair(a.badge, b.badge)}</details>` +
        titles.map((t) => `<details class="sec" open><summary>${esc(t)}</summary>
          ${pair(cell(a, t), cell(b, t))}</details>`).join("");
    };
    pane.querySelector("#cmp-left").addEventListener("change", draw);
    pane.querySelector("#cmp-right").addEventListener("change", draw);
    draw();
  }

  window.addEventListener("hashchange", route);
  route();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
