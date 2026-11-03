"""研究期間の原ファイルの確認の読み取りの結線（ADR-0036 第8項の 2、ADR-0040、D03 §17.4）。

順 7 の受入れの実装 PR A2。

確認済みハッシュの一覧（D03 §17.3。D17 の A3a で人間が merge で足す）に原ファイルを載せる前に、
研究期間用の品質確認の許可（`configs/quality_checks/oanda_mt5_research_raw_verify.yaml`。D17 の AP
で人間が merge で承認する）の下で、1 通貨の取得記録のファイルを 1 回ずつ読んで確かめる。業務の規則は
持たず、`marketdata`（読む前の照合・確認の判定・時刻の列だけの読み取り）と関所
（`evaluation.application.holdout_gate`）を品質確認専用の入口（`app.quality_check.run_quality_check`）
でつなぐだけ（D01 §7、ADR-0036 第5項・第9項）。

手順（どこで失敗しても結果を出さない。ADR-0036 第7項。D03 §17.4 の 3）:

1. 読む前の照合（読まない段）: 取得記録の原本と版管理の写しの SHA-256、取得記録の状態・要求の
   終わり・最後の足の時刻（`raw_verification.pre_read_problems`）。外れたら許可を求めずに止める。
2. 関所の許可の確認と閲覧の記録の開始の行（区分は研究履歴だけ、種類は確認の読み取り、対象は
   その通貨のファイル（正規の置き場と取得記録の `sha256_gz`）、出力は件数・ハッシュ・合否）。
3. 読み取り（`raw_verification.verify_raw_files`）。2024 年以降の行・表が覆わない行に当たったら
   その場で止め、`ResearchVerifyStopped` を送出する（入口が「途中で停止」の終了の行を書く）。
4. 終了の行（完了）。

- **道具**: `oanda_mt5_research_raw_verify` 版 1（`odyssey-fx data verify-raw-mt5`。A1-3）。1 回の
  実行で 1 通貨（閲覧の識別子は通貨ごとに 1 つ）。
- **設定のダイジェスト**: 確認の読み取りが読む固定のファイル（時刻の規則の表 版 1・承認済みの表の
  一覧・列対応の宣言 版 1）の置き場と SHA-256 の正規化 JSON の SHA-256（`research_config_digest`。
  封印の `sealed_config_digest` と同じ形。A1-9）。
- **置き場**: 許可の対象と結果の置き場は正規の置き場（`data/raw/oanda_mt5/<取得日>/<file>`）。原
  ファイルを実際に読む場所は `raw_root` で替えられる（作業木の外の本体の作業ディレクトリなど）。
  読んだバイト列の SHA-256 が許可の対象のハッシュと一致するものだけを展開する。

出力は件数・ハッシュ・合否と不合格の理由の種類の名前だけ（ADR-0040 第1項の 5）。時刻・区間・価格を
出さない。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from odyssey_fx.app.config.loader import ConfigError
from odyssey_fx.app.config.time_rules import load_time_rule_table
from odyssey_fx.app.mt5 import DEFAULT_MT5_DATASOURCE, load_datasource
from odyssey_fx.app.quality_check import run_quality_check
from odyssey_fx.common.symbol import Symbol
from odyssey_fx.common.time import UtcTime
from odyssey_fx.evaluation.domain.quality_permit import RESEARCH_PERIOD, QualityCheckRequest
from odyssey_fx.marketdata.adapters.mt5_csv_source import (
    file_sha256,
    read_fetch_manifest,
    read_month_times,
)
from odyssey_fx.marketdata.application.mt5_acceptance import Mt5FetchEntry
from odyssey_fx.marketdata.application.raw_verification import (
    MonthTimes,
    RawVerification,
    pre_read_problems,
    verify_raw_files,
)
from odyssey_fx.marketdata.domain.quality_access import (
    RESEARCH_QUALITY_CLASSES,
    RESEARCH_VERIFY_OUTPUT_KINDS,
    QualityCheckKind,
    QualityReadPermit,
    RawFileTarget,
    ToolRef,
)

__all__ = [
    "RESEARCH_CONFIG_FILES",
    "RESEARCH_VERIFY_TOOL",
    "ResearchVerification",
    "ResearchVerifyRefused",
    "ResearchVerifyStopped",
    "research_config_digest",
    "verification_payload",
    "verify_research_raw",
]

#: 確認の読み取りの道具（許可の定義 `configs/quality_checks/oanda_mt5_research_raw_verify.yaml`。
#: ファイル名は道具の名前と同じ。D03 §17.5。A1-3）。
RESEARCH_VERIFY_TOOL: Final = ToolRef(name="oanda_mt5_research_raw_verify", version="1")

#: 時刻の規則の表 版 1 と承認済みの表の一覧（研究期間の受入れの数え方の規則 版 1 が指すもの）。
_TABLE_V1: Final = Path("docs/design/time_rules/time_rule_table_v1.yaml")
_APPROVED_TABLES: Final = Path("docs/design/time_rules/approved_time_rule_tables.yaml")

#: 許可の定義の「設定のダイジェスト」の対象（確認の読み取りが読む固定のファイル。D03 §17.5 の
#: `config_digest`。A1-9）。取得記録は読む前の照合（原本と写し）で別に確かめる。
RESEARCH_CONFIG_FILES: Final = (_TABLE_V1, _APPROVED_TABLES, DEFAULT_MT5_DATASOURCE)


class ResearchVerifyRefused(ConfigError):
    """確認の読み取りを始めない（読む前の照合に外れた。許可を求めず、何も読まない）。"""


class ResearchVerifyStopped(Exception):  # noqa: N818 - 「途中で止めた」という事実の名前
    """2024 年以降の行・表が覆わない行に当たって途中で止めた（`result.stopped` が真）。

    品質確認の入口はこの例外で「途中で停止」の終了の行を書いてから送出し直す。呼び出し側は
    `result` の最後のファイルの置き場と SHA-256 だけを人間に報告する（ADR-0040 第3項の 4。A1-6）。
    """

    def __init__(self, verification: ResearchVerification) -> None:
        last = verification.result.verdicts[-1]
        reason = "" if last.reason is None else last.reason.value
        super().__init__(f"{reason}: {last.location} {last.sha256}")
        self.verification = verification


@dataclass(frozen=True, slots=True)
class ResearchVerification:
    """1 通貨の確認の読み取りの結果（閲覧の識別子・取得記録の SHA-256・判定）。"""

    fetch_date: str
    symbol: Symbol
    access_id: str
    fetch_manifest_sha256: str
    config_digest: str
    result: RawVerification


def research_config_digest(repo_root: Path) -> str:
    """許可の定義の設定のダイジェスト（固定したファイルの置き場と SHA-256 の正規化 JSON の
    SHA-256）。1 バイトでも変われば許可の定義と一致しなくなる（ADR-0036 第4項）。"""
    root = Path(repo_root)
    files = sorted(RESEARCH_CONFIG_FILES, key=lambda path: path.as_posix())
    payload = {path.as_posix(): file_sha256(root / path) for path in files}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_prefix(root: Path, fetch_date: str) -> str:
    if not fetch_date or "/" in fetch_date or "\\" in fetch_date or fetch_date in (".", ".."):
        raise ResearchVerifyRefused(f"取得日 {fetch_date!r} は置き場の名前として使えない")
    return f"{root.as_posix()}/{fetch_date}"


def verify_research_raw(
    *,
    repo_root: Path,
    fetch_date: str,
    symbol: Symbol,
    raw_root: Path | None = None,
    now: Callable[[], UtcTime] | None = None,
    new_id: Callable[[], str] | None = None,
) -> ResearchVerification:
    """1 通貨の研究期間の原ファイルを、品質確認の許可の下で確かめる（モジュールの説明文）。

    許可が出ない・記録に失敗したときは何も読まずに `QualityCheckRefused`。読む前の照合に外れたら
    許可を求めずに `ResearchVerifyRefused`。止まる理由に当たったら「途中で停止」の終了の行を書いて
    `ResearchVerifyStopped`。
    """
    root = Path(repo_root)
    datasource = load_datasource(root)
    table = load_time_rule_table(root / _TABLE_V1, root / _APPROVED_TABLES)
    prefix = _canonical_prefix(datasource.root, fetch_date)
    raw_dir = (root / datasource.root if raw_root is None else Path(raw_root)) / fetch_date
    record_path = raw_dir / datasource.fetch_manifest
    copy_path = root / datasource.docs_copy_for(fetch_date)
    if not record_path.is_file() or not copy_path.is_file():
        raise ResearchVerifyRefused(
            f"取得記録 {record_path} か版管理の写し {copy_path} が無い（D03 §17.4 の 3 の 1）"
        )
    record_sha = file_sha256(record_path)
    if file_sha256(copy_path) != record_sha:
        raise ResearchVerifyRefused(
            f"取得記録 {record_path} と版管理の写し {copy_path} が違う。許可を求めずに止める"
            "（D03 §17.4 の 3 の 1）"
        )
    own = [entry for entry in read_fetch_manifest(record_path) if entry.symbol == symbol]
    if not own:
        raise ResearchVerifyRefused(f"{record_path} に {symbol} の要求が無い")
    problems = pre_read_problems(own)
    if problems:
        raise ResearchVerifyRefused(
            f"{len(problems)} 件の要求が読む前の照合に外れた（{', '.join(problems)}）。許可を"
            "求めずに止める。外れたファイルは一覧に載せない（D03 §17.4 の 3 の 1）"
        )
    locations = {entry.rid: f"{prefix}/{entry.file}" for entry in own}
    digest = research_config_digest(root)
    request = QualityCheckRequest(
        tool=RESEARCH_VERIFY_TOOL,
        config_digest=digest,
        check_kind=QualityCheckKind.RAW_FILE_VERIFICATION,
        classes=RESEARCH_QUALITY_CLASSES,
        period=RESEARCH_PERIOD,
        targets=tuple(
            RawFileTarget(location=locations[entry.rid], sha256=entry.sha256_gz, symbol=symbol)
            for entry in own
        ),
        output_kinds=RESEARCH_VERIFY_OUTPUT_KINDS,
    )

    def tool(permit: QualityReadPermit) -> ResearchVerification:
        def open_times(entry: Mt5FetchEntry) -> MonthTimes:
            return read_month_times(
                raw_dir / entry.file,
                entry,
                datasource.columns,
                read_permit=permit,
                location=locations[entry.rid],
            )

        result = verify_raw_files(
            own, symbol=symbol, locations=locations, open_times=open_times, table=table
        )
        verification = ResearchVerification(
            fetch_date=fetch_date,
            symbol=symbol,
            access_id=permit.access_id,
            fetch_manifest_sha256=record_sha,
            config_digest=digest,
            result=result,
        )
        if result.stopped:
            raise ResearchVerifyStopped(verification)
        return verification

    if now is None or new_id is None:
        return run_quality_check(repository_root=root, request=request, tool=tool)
    return run_quality_check(
        repository_root=root, request=request, tool=tool, now=now, new_id=new_id
    )


def verification_payload(verification: ResearchVerification) -> dict[str, object]:
    """結果の JSON の形（一覧の行に写す材料。時刻・区間・価格を含まない。D03 §17.4 の 4）。"""
    result = verification.result
    return {
        "schema_version": 1,
        "tool": {"name": RESEARCH_VERIFY_TOOL.name, "version": RESEARCH_VERIFY_TOOL.version},
        "fetch_date": verification.fetch_date,
        "symbol": str(verification.symbol),
        "access_id": verification.access_id,
        "fetch_manifest_sha256": verification.fetch_manifest_sha256,
        "config_digest": verification.config_digest,
        "stopped": result.stopped,
        "counts": {"pass": result.passed, "fail": result.failed, "unread": result.unread},
        "files": [
            {
                "location": item.location,
                "symbol": str(item.symbol),
                "sha256": item.sha256,
                "access_id": verification.access_id,
                "result": "PASS" if item.passed else "FAIL",
                "reason": None if item.reason is None else item.reason.value,
            }
            for item in result.verdicts
        ],
    }
