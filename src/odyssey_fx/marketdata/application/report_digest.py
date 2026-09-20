"""完全性検査の報告の正規化表現とダイジェスト（D03 §3.7.1）。

manifest は検査報告への参照（`integrity_report_ref`）をダイジェストで持つ。その値は
snapshot 識別子の対象なので、**報告の内容から決まらなければならない**。呼び出し元が別途
渡した値を記録していると、報告と食い違うダイジェストのまま確定・承認できてしまう。

正規化表現を `application` に置く理由は、受入れ（`acceptance`）が報告からダイジェストを
自分で計算できるようにするためである。adapters はこれと**同じ文字列**をファイルへ書き、
同じ値を返すので、書いた内容と manifest の記録が必ず一致する。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from odyssey_fx.common.time import Interval
from odyssey_fx.marketdata.domain.integrity import IntegrityReport
from odyssey_fx.marketdata.domain.series import SeriesId

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "integrity_report_digest_hex",
    "integrity_report_payload",
    "integrity_report_text",
]

#: 報告の保存形式の版。読み込み時に未知の版を拒否する。
REPORT_SCHEMA_VERSION = 1


def _series_payload(series: SeriesId) -> Mapping[str, str]:
    return {
        "basis": series.basis.value,
        "symbol": str(series.symbol),
        "timeframe": str(series.timeframe),
    }


def _interval_payload(interval: Interval) -> Mapping[str, str]:
    return {"end": str(interval.end), "start": str(interval.start)}


def integrity_report_payload(report: IntegrityReport) -> Mapping[str, Any]:
    """報告を JSON 互換の構造へ変換する（D03 §3.7.1 の整列鍵順）。

    結果の並びは `IntegrityReport` が構築時に正規化しているので、検査の実行順は表現に
    影響しない。
    """
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "results": [
            {
                "detail": [{"key": key, "value": value} for key, value in result.detail],
                "interval": _interval_payload(result.interval),
                "kind": result.kind.value,
                "series": _series_payload(result.series),
                "severity": result.severity.value,
            }
            for result in report.results
        ],
    }


def integrity_report_text(report: IntegrityReport) -> str:
    """報告の正規化された保存表現（キーをコードポイント順に整列した JSON）。

    adapters はこの文字列をそのままファイルへ書く。書いた内容とダイジェストの対象が同じ
    文字列なので、両者が食い違うことはない。
    """
    payload = integrity_report_payload(report)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def integrity_report_digest_hex(report: IntegrityReport) -> str:
    """報告の内容ダイジェスト（16進 64 文字、D03 §3.7.1）。"""
    return hashlib.sha256(integrity_report_text(report).encode("utf-8")).hexdigest()
