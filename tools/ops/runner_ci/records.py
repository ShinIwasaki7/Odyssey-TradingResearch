"""検査 5（記録の書式）と検査 6（自由文に数字が無い）。D25 §3.1・§3.9・§2.3・§6.3 の 5・6。

対象は、この PR で足された・変わったファイルのうち、仮説カードの置き場 `research/hypotheses/` と
一巡の記録の置き場 `research/loops/` の下のもの（削除は対象外。移動の先は足されたファイルとして
読む）。

- カード `research/hypotheses/{queue,taken,done}/<カード>.yaml`: D25 §3.1 の項目・型・語彙。戦略の
  宣言の中身（部品・接続）と格子の試行の数は、取り出しの後の機械の検査（D25 §3.5。PR 7）が見る。
- 終わり方 `research/hypotheses/done/<カード>.outcome.yaml`、事前登録の注記 `prereg_v<版>.yaml`、
  変更の提案 `change_v<版>.yaml`（D25 §3.9）、実行記録 `run_v<版>.yaml`、一巡の報告 `report.yaml`:
  D25 §2.3 の中身の説明から本 PR が置いた形（本 PR の仮置き。書き手は PR 7・PR 8a）。

検証の正規表現は `fullmatch` で当て、数字は ASCII の `[0-9]` だけにする（`$` は末尾の改行の前にも
一致し、`\\d` は全角などの数字にも一致するため）。

YAML は設定ファイルと同じ安全な読込（重複キー・anchor・alias を拒む。`app.config.loader`）で読む。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Final

from odyssey_fx.app.config.loader import ConfigError, load_yaml_mapping
from tools.ops.runner_ci.model import ChangeStatus, CheckInput, CheckResult, Status, result

HYPOTHESES: Final = "research/hypotheses/"
LOOPS: Final = "research/loops/"
CARD_DIRECTORIES: Final = ("queue", "taken", "done")

#: カードの識別子（D25 §3.2、仮置き 4）: `<口の 1 文字>-<YYYYMMDD>-<短い名前>`。
CARD_ID: Final = r"[ha]-[0-9]{8}-[a-z0-9-]{3,24}"
_CARD_ID_RE: Final = re.compile(r"^(?P<mouth>[ha])-(?P<date>[0-9]{8})-(?P<title>[a-z0-9-]{3,24})$")
_CARD_PATH_RE: Final = re.compile(
    rf"^research/hypotheses/(?P<place>queue|taken|done)/(?P<card>{CARD_ID})\.yaml$"
)
_OUTCOME_PATH_RE: Final = re.compile(
    rf"^research/hypotheses/done/(?P<card>{CARD_ID})\.outcome\.yaml$"
)
_LOOP_PATH_RE: Final = re.compile(
    rf"^research/loops/(?P<card>{CARD_ID})/"
    r"(?:(?P<kind>prereg|change|run)_v(?P<version>[1-9][0-9]*)|(?P<report>report))\.yaml$"
)

#: 承認済みの研究用 snapshot がある 8 通貨（D23 の仮置き 15）。
SYMBOLS: Final = frozenset(
    {"AUDJPY", "AUDUSD", "EURJPY", "EURUSD", "GBPJPY", "GBPUSD", "USDCHF", "USDJPY"}
)
_STRATEGY_KEYS: Final = frozenset(
    {"entry_policy", "opportunity_validity", "components", "roles", "opportunity_concurrency"}
)
_OPERATIONS: Final = {
    "set_axis_values": "PARAMETERS",
    "add_axis": "PARAMETERS",
    "remove_axis": "PARAMETERS",
    "set_parameter": "STRATEGY_CODE",
    "add_component": "STRATEGY_CODE",
    "remove_component": "STRATEGY_CODE",
    "rewire_input": "STRATEGY_CODE",
    "set_role": "STRATEGY_CODE",
}
_UTC_TIME_RE: Final = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENT_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
_EXPERIMENT_ID_RE: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
_NUMBER_RE: Final = re.compile(r"\d+(?:\.\d+)?")


def experiment_id_of(card: str, symbol: str) -> str:
    """カードから実験と戦略の識別子を作る（D25 §3.2）: `c_<口><日付>_<名前の - を _ に>_<通貨>`。"""
    matched = _CARD_ID_RE.fullmatch(card)
    if matched is None:
        raise ValueError(f"not a card id: {card!r}")
    title = matched["title"].replace("-", "_")
    return f"c_{matched['mouth']}{matched['date']}_{title}_{symbol.lower()}"


class RecordKind(Enum):
    CARD = "カード"
    OUTCOME = "終わり方"
    PREREG = "事前登録の注記"
    CHANGE = "変更の提案"
    RUN = "実行記録"
    REPORT = "一巡の報告"


@dataclass(frozen=True, slots=True)
class RecordPath:
    """記録の置き場から読んだ種類・カード・版（版の無い種類は `None`）。"""

    kind: RecordKind
    card: str
    version: int | None
    place: str | None


def record_path(path: str) -> RecordPath | None:
    """記録の置き場のパスを読む（決まった名前でなければ `None`）。"""
    if (card := _CARD_PATH_RE.fullmatch(path)) is not None:
        return RecordPath(RecordKind.CARD, card["card"], None, card["place"])
    if (outcome := _OUTCOME_PATH_RE.fullmatch(path)) is not None:
        return RecordPath(RecordKind.OUTCOME, outcome["card"], None, "done")
    if (loop := _LOOP_PATH_RE.fullmatch(path)) is not None:
        if loop["report"]:
            return RecordPath(RecordKind.REPORT, loop["card"], None, None)
        kinds = {"prereg": RecordKind.PREREG, "change": RecordKind.CHANGE, "run": RecordKind.RUN}
        return RecordPath(kinds[loop["kind"]], loop["card"], int(loop["version"]), None)
    return None


def card_path(place: str, card: str) -> str:
    return f"{HYPOTHESES}{place}/{card}.yaml"


def is_record_path(path: str) -> bool:
    return path.startswith((HYPOTHESES, LOOPS))


# --- 書式の検査の部品 -----------------------------------------------------------------

Check = Callable[[object, str], list[str]]


def _type_name(value: object) -> str:
    return type(value).__name__


def text(value: object, where: str) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [f"{where}: 空でない文字列でない（{_type_name(value)}）"]
    return []


def integer(low: int | None = None, high: int | None = None) -> Check:
    def check(value: object, where: str) -> list[str]:
        if isinstance(value, bool) or not isinstance(value, int):
            return [f"{where}: 整数でない（{_type_name(value)}）"]
        if (low is not None and value < low) or (high is not None and value > high):
            return [f"{where}: 範囲の外"]
        return []

    return check


def equals(expected: object) -> Check:
    def check(value: object, where: str) -> list[str]:
        if isinstance(value, bool) != isinstance(expected, bool) or value != expected:
            return [f"{where}: {expected!r} でない"]
        return []

    return check


def one_of(*values: str) -> Check:
    def check(value: object, where: str) -> list[str]:
        if value not in values:
            return [f"{where}: 語彙（{' / '.join(values)}）の外"]
        return []

    return check


def pattern(regex: re.Pattern[str], label: str) -> Check:
    def check(value: object, where: str) -> list[str]:
        if not isinstance(value, str) or regex.fullmatch(value) is None:
            return [f"{where}: {label}の形でない"]
        return []

    return check


def utc_time(value: object, where: str) -> list[str]:
    if not isinstance(value, str) or _UTC_TIME_RE.fullmatch(value) is None:
        return [f"{where}: UTC の時刻（YYYY-MM-DDTHH:MM:SSZ。引用符付きの文字列）でない"]
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return [f"{where}: 時刻として成り立たない"]
    return []


HEX64: Final = pattern(_HEX64_RE, "SHA-256（16 進 64 文字の小文字）")
IDENT: Final = pattern(_IDENT_RE, "理由の識別子（英大文字・数字・_）")


def relative_path(value: object, where: str) -> list[str]:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        return [f"{where}: 相対パスでない"]
    if ".." in value.split("/"):
        return [f"{where}: `..` を含む"]
    return []


def nullable(inner: Check) -> Check:
    def check(value: object, where: str) -> list[str]:
        return [] if value is None else inner(value, where)

    return check


def sequence(item: Check, *, minimum: int = 0) -> Check:
    def check(value: object, where: str) -> list[str]:
        if not isinstance(value, list):
            return [f"{where}: 並び（list）でない（{_type_name(value)}）"]
        if len(value) < minimum:
            return [f"{where}: 要素が {minimum} 個より少ない"]
        return [
            error for i, element in enumerate(value) for error in item(element, f"{where}[{i}]")
        ]

    return check


def scalar(value: object, where: str) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return [f"{where}: 値（文字列か数）でない（{_type_name(value)}）"]
    return []


def anything_mapping(value: object, where: str) -> list[str]:
    return [] if isinstance(value, dict) else [f"{where}: mapping でない（{_type_name(value)}）"]


def mapping(required: Mapping[str, Check], optional: Mapping[str, Check] | None = None) -> Check:
    extra = optional or {}

    def check(value: object, where: str) -> list[str]:
        if not isinstance(value, dict):
            return [f"{where}: mapping でない（{_type_name(value)}）"]
        errors = [f"{where}.{key}: 必須の項目が無い" for key in required if key not in value]
        errors += [
            f"{where}.{key}: 書式に無い項目"
            for key in value
            if key not in required and key not in extra
        ]
        for key, item in (*required.items(), *extra.items()):
            if key in value:
                errors += item(value[key], f"{where}.{key}")
        return errors

    return check


# --- 各記録の形 ------------------------------------------------------------------

_AXIS: Final = mapping({"instance": text, "parameter": text, "values": sequence(scalar, minimum=1)})
#: カードの戦略は D04 の宣言の本体のうち 5 項目（D25 §3.1）。部品の中身は PR 7 の検査が見る。
_STRATEGY: Final = mapping(
    {
        "entry_policy": anything_mapping,
        "opportunity_validity": anything_mapping,
        "components": sequence(anything_mapping, minimum=1),
        "roles": anything_mapping,
        "opportunity_concurrency": anything_mapping,
    }
)
_CARD_FIELDS: Final[dict[str, Check]] = {
    "schema_version": equals(1),
    "id": pattern(_CARD_ID_RE, "カードの識別子"),
    "mouth": one_of("human", "ai"),
    "created_at": utc_time,
    "title": pattern(re.compile(r"^[a-z0-9-]{3,24}$"), "短い名前（英小文字・数字・- の 3〜24 字）"),
    "symbol": one_of(*sorted(SYMBOLS)),
    "rule": text,
    "strategy": _STRATEGY,
    "grid": mapping({"axes": sequence(_AXIS, minimum=1)}),
    "prediction": text,
    "inspiration": text,
    "evidence_estimate": mapping(
        {
            "frequency_band": one_of("LOW", "MEDIUM", "HIGH"),
            "reaches_floor": one_of("LIKELY", "UNSURE", "UNLIKELY"),
        }
    ),
}
_CARD_OPTIONAL: Final[dict[str, Check]] = {
    "favored_regimes": sequence(text),
    "read_scope": sequence(relative_path, minimum=1),
}
CARD: Final = mapping(_CARD_FIELDS, _CARD_OPTIONAL)

OUTCOME: Final = mapping(
    {
        "schema_version": equals(1),
        "card": pattern(_CARD_ID_RE, "カードの識別子"),
        "reason": IDENT,
        "at": utc_time,
        "last_version": integer(0),
    }
)
_FILE_HASH: Final = mapping({"path": relative_path, "sha256": HEX64})
PREREG: Final = mapping(
    {
        "schema_version": equals(1),
        "card": pattern(_CARD_ID_RE, "カードの識別子"),
        "version": integer(1),
        "experiment_id": pattern(_EXPERIMENT_ID_RE, "実験の識別子"),
        "card_sha256": HEX64,
        "change_sha256": nullable(HEX64),
        "generator_digest": HEX64,
        "generated": sequence(_FILE_HASH, minimum=1),
        "checks": mapping(
            {
                "load": equals("PASS"),
                "compile": equals("PASS"),
                "prepare": equals("PASS"),
                "trial_count": integer(1),
                "rejected_count": integer(0),
                "seconds": integer(0),
                "max_rss_bytes": integer(0),
            }
        ),
    }
)
CHANGE: Final = mapping(
    {
        "schema_version": equals(1),
        "card": pattern(_CARD_ID_RE, "カードの識別子"),
        "from_version": integer(1, 2),
        "decision": equals("CHANGE"),
        "change_kind": one_of("PARAMETERS", "STRATEGY_CODE"),
        "operations": sequence(anything_mapping, minimum=1),
        "reason": text,
        "prediction": text,
        "memo_sha256": HEX64,
    }
)
RUN: Final = mapping(
    {
        "schema_version": equals(1),
        "card": pattern(_CARD_ID_RE, "カードの識別子"),
        "version": integer(1),
        "experiment_id": pattern(_EXPERIMENT_ID_RE, "実験の識別子"),
        "execution": integer(1),
        "started_at": utc_time,
        "finished_at": utc_time,
        "exit_code": integer(),
        "max_rss_bytes": nullable(integer(0)),
        "record_id": text,
        "artifacts": sequence(_FILE_HASH),
        "memo": nullable(_FILE_HASH),
    }
)
REPORT: Final = mapping(
    {
        "schema_version": equals(1),
        "card": pattern(_CARD_ID_RE, "カードの識別子"),
        "final_version": integer(1, 3),
        "text": mapping(
            {
                "prediction": text,
                "change_reasons": sequence(text),
                "change_contents": sequence(text),
            }
        ),
        "comparison": _FILE_HASH,
        "gate": mapping(
            {
                "items": sequence(
                    mapping({"item": integer(1, 5), "result": one_of("PASS", "FAIL")}), minimum=1
                ),
                "verifier": mapping({"result": one_of("PASS", "FAIL"), "reasons": sequence(IDENT)}),
            }
        ),
    }
)
_SHAPES: Final = {
    RecordKind.CARD: CARD,
    RecordKind.OUTCOME: OUTCOME,
    RecordKind.PREREG: PREREG,
    RecordKind.CHANGE: CHANGE,
    RecordKind.RUN: RUN,
    RecordKind.REPORT: REPORT,
}


# --- 読込 -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Record:
    """足された・変わった記録 1 つ（`data` は読めたときの中身、`error` は読めない理由）。"""

    path: str
    where: RecordPath | None
    raw: bytes
    data: dict[str, Any] | None
    error: str | None


def load(path: str, raw: bytes) -> Record:
    where = record_path(path)
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return Record(path, where, raw, None, "UTF-8 でない")
    try:
        data = load_yaml_mapping(Path(path), text=decoded)
    except ConfigError as exc:
        # 理由の文は値を含みうるので、行の位置だけを残す。
        located = re.search(r"line [0-9]+", str(exc))
        return Record(
            path, where, raw, None, "YAML として読めない" + (f"（{located[0]}）" if located else "")
        )
    return Record(path, where, raw, data, None)


def changed_records(given: CheckInput) -> tuple[Record, ...]:
    """この PR で足された・変わった記録（置き場の下のファイル）を読む。"""
    loaded: list[Record] = []
    for change in given.changes:
        if change.status is ChangeStatus.DELETED or not is_record_path(change.path):
            continue
        raw = given.head.read(change.path)
        if raw is not None:
            loaded.append(load(change.path, raw))
    return tuple(loaded)


def head_card(given: CheckInput, card: str) -> tuple[str, dict[str, Any] | None, bytes] | None:
    """先頭の時点でカードが置かれた場所と中身（無ければ `None`。複数あれば最初の場所）。"""
    for place in CARD_DIRECTORIES:
        raw = given.head.read(card_path(place, card))
        if raw is not None:
            loaded = load(card_path(place, card), raw)
            return place, loaded.data, raw
    return None


# --- 検査 5: 記録の書式 -------------------------------------------------------------

FORMAT_NAME: Final = "記録の書式（カードと research/loops/ の記録）"


def _cross_checks(given: CheckInput, record: Record) -> list[str]:
    where, data = record.where, record.data
    assert where is not None and data is not None  # noqa: S101 - 呼び出し側が確かめた
    errors: list[str] = []
    if where.kind is RecordKind.CARD:
        if data.get("id") != where.card:
            errors.append(f"{record.path}: `id` がファイル名と違う")
        mouth, created, title = data.get("mouth"), data.get("created_at"), data.get("title")
        if isinstance(mouth, str) and isinstance(created, str) and isinstance(title, str):
            expected = f"{mouth[:1]}-{created[:10].replace('-', '')}-{title}"
            if data.get("id") != expected:
                errors.append(f"{record.path}: `id` が `<口>-<created_at の日付>-<title>` でない")
        if mouth == "ai" and "read_scope" not in data:
            errors.append(f"{record.path}: AI の口のカードに `read_scope` が無い")
        return errors
    if data.get("card") != where.card:
        errors.append(f"{record.path}: `card` が置き場のカードと違う")
    if where.kind in {RecordKind.PREREG, RecordKind.RUN} and data.get("version") != where.version:
        errors.append(f"{record.path}: `version` がファイル名の版と違う")
    if where.kind is RecordKind.CHANGE:
        if where.version not in {2, 3}:
            errors.append(f"{record.path}: 変更の提案の版は 2 か 3（再評価は 2 回まで。決定 103）")
        elif data.get("from_version") != where.version - 1:
            errors.append(f"{record.path}: `from_version` が前の版でない")
        errors += _operation_kinds(record.path, data)
    if where.kind is RecordKind.PREREG:
        if (data.get("change_sha256") is None) != (where.version == 1):
            errors.append(f"{record.path}: `change_sha256` は版 1 で null、版 2 からは必須")
    if where.kind in {RecordKind.PREREG, RecordKind.RUN}:
        located = head_card(given, where.card)
        symbol = None if located is None or located[1] is None else located[1].get("symbol")
        if located is None:
            errors.append(f"{record.path}: カード {where.card} が置き場に無い")
        elif isinstance(symbol, str) and data.get("experiment_id") != experiment_id_of(
            where.card, symbol
        ):
            errors.append(
                f"{record.path}: `experiment_id` がカードから作る識別子と違う（D25 §3.2）"
            )
    return errors


def _operation_kinds(path: str, data: Mapping[str, Any]) -> list[str]:
    operations = data.get("operations")
    if not isinstance(operations, list):
        return []
    errors: list[str] = []
    for index, operation in enumerate(operations):
        name = operation.get("op") if isinstance(operation, dict) else None
        if name not in _OPERATIONS:
            errors.append(f"{path}: operations[{index}].op が操作の語彙の外")
        elif _OPERATIONS[name] != data.get("change_kind"):
            errors.append(
                f"{path}: operations[{index}] が `change_kind` の種類でない（1 種類だけ）"
            )
    return errors


def _duplicate_cards(given: CheckInput) -> list[str]:
    seen: dict[str, list[str]] = {}
    for path in given.head.list(HYPOTHESES):
        where = record_path(path)
        if where is not None and where.kind is RecordKind.CARD:
            seen.setdefault(where.card, []).append(path)
    return [
        f"カード {card} が複数の置き場にある（{', '.join(paths)}）"
        for card, paths in sorted(seen.items())
        if len(paths) > 1
    ]


def check_formats(given: CheckInput, records: Sequence[Record]) -> CheckResult:
    if not records:
        return CheckResult(5, FORMAT_NAME, Status.NOT_APPLICABLE, ("足された・変わった記録が無い",))
    problems: list[str] = []
    for record in records:
        if record.where is None:
            problems.append(f"{record.path}: 記録の置き場の決まった名前でない")
            continue
        if record.data is None:
            problems.append(f"{record.path}: {record.error}")
            continue
        shape_errors = _SHAPES[record.where.kind](record.data, record.path)
        problems += shape_errors
        if not shape_errors:
            problems += _cross_checks(given, record)
    problems += _duplicate_cards(given)
    return result(5, FORMAT_NAME, problems)


# --- 検査 6: 自由文に数字が無い --------------------------------------------------------

FREE_TEXT_NAME: Final = "自由文に数字が無い（rule の数はパラメータか格子の値にある）"


def has_digit(value: str) -> bool:
    """数字（半角・全角を含む Unicode の数字の分類 N*）を含むか（D25 の仮置き 6）。"""
    return any(unicodedata.category(character).startswith("N") for character in value)


def _free_texts(kind: RecordKind, data: Mapping[str, Any]) -> Iterator[tuple[str, object]]:
    if kind is RecordKind.CARD:
        yield "prediction", data.get("prediction")
        yield "inspiration", data.get("inspiration")
    elif kind is RecordKind.CHANGE:
        yield "reason", data.get("reason")
        yield "prediction", data.get("prediction")
    elif kind is RecordKind.REPORT:
        body = data.get("text")
        if isinstance(body, dict):
            yield "text.prediction", body.get("prediction")
            for key in ("change_reasons", "change_contents"):
                items = body.get(key)
                for index, item in enumerate(items if isinstance(items, list) else []):
                    yield f"text.{key}[{index}]", item


def _numbers(value: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER_RE.findall(unicodedata.normalize("NFKC", value)):
        try:
            found.add(Decimal(token).normalize())
        except InvalidOperation:  # pragma: no cover - 正規表現が数だけを拾う
            continue
    return found


def _scalar_numbers(value: object) -> set[Decimal]:
    """宣言・格子の値に現れる数（数の値と、文字列の値の中の数）。"""
    if isinstance(value, bool):
        return set()
    if isinstance(value, int | float):
        return _numbers(str(value))
    if isinstance(value, str):
        return _numbers(value)
    if isinstance(value, dict):
        return {number for item in value.values() for number in _scalar_numbers(item)}
    if isinstance(value, list):
        return {number for item in value for number in _scalar_numbers(item)}
    return set()


def _rule_numbers(path: str, data: Mapping[str, Any]) -> list[str]:
    rule = data.get("rule")
    if not isinstance(rule, str):
        return []
    allowed = _scalar_numbers(data.get("strategy")) | _scalar_numbers(data.get("grid"))
    if _numbers(rule) - allowed:
        return [
            f"{path}: `rule` の数に、戦略のパラメータにも格子の値にも無いものがある"
            "（D25 の仮置き 6）"
        ]
    return []


def check_free_text(records: Sequence[Record]) -> CheckResult:
    readable = [
        record for record in records if record.where is not None and record.data is not None
    ]
    if not readable:
        return CheckResult(6, FREE_TEXT_NAME, Status.NOT_APPLICABLE, ("読めた記録が無い",))
    problems: list[str] = []
    for record in readable:
        assert record.where is not None and record.data is not None  # noqa: S101
        for field, value in _free_texts(record.where.kind, record.data):
            if isinstance(value, str) and has_digit(value):
                problems.append(f"{record.path}: 自由文 `{field}` に数字がある")
        if record.where.kind is RecordKind.CARD:
            problems += _rule_numbers(record.path, record.data)
    return result(6, FREE_TEXT_NAME, problems)
