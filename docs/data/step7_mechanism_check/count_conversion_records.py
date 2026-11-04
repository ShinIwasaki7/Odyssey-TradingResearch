"""順 7 の機構確認 M1: run の判断履歴の換算の記録を、値を読まずに数える（記録用の一回の道具）。

D06 §19.7 の記録の置き場（根拠記録 表15 の `conversion_paths` と `market_refs`、受付の審査記録
表6 の `conversion_*`、約定 表9 の `costs`）に、換算の率・観測時刻が書かれている件数と、古さを
導ける件数（処理点の時刻と経路の観測時刻の両方がある経路。仮置き C1-7）を数える。

**率の値・価格・数量・金額は出さない**。出すのは件数と通貨の名前だけ。率は「空でないか」だけを見る。

使い方（リポジトリの根で）::

    uv run python -I docs/data/step7_mechanism_check/count_conversion_records.py \\
        <run の成果物のディレクトリ> ...
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import polars as pl


def _present(value: Any) -> bool:
    return value is not None and value != ""


def count(run_dir: Path) -> dict[str, Any]:
    evidence = pl.read_parquet(
        run_dir / "EVIDENCE.parquet", columns=["kind", "at_time", "market_refs", "conversion_paths"]
    )
    kinds: Counter[str] = Counter()
    rows_with_paths = paths = legged = legs = rows_with_refs = 0
    legs_by_series: Counter[str] = Counter()
    rate_ok = observed_ok = staleness_ok = 0
    for kind, at_time, refs, items in evidence.iter_rows():
        kinds[kind] += 1
        if not items:
            continue
        rows_with_paths += 1
        rows_with_refs += bool(refs)
        for item in items:
            path = json.loads(item)
            paths += 1
            path_legs = path.get("legs") or []
            if path_legs:
                legged += 1
                legs += len(path_legs)
                for leg in path_legs:
                    legs_by_series[str(leg.get("series"))] += 1
            rate_ok += _present(path.get("rate"))
            observed_ok += _present(path.get("observed_at"))
            staleness_ok += _present(path.get("observed_at")) and _present(at_time)
    assessments = pl.read_parquet(
        run_dir / "RISK_ASSESSMENTS.parquet",
        columns=[
            "conversion_from_currency",
            "conversion_to_currency",
            "conversion_rate",
            "conversion_observed_at",
            "conversion_evidence_evidence_id",
        ],
    )
    pairs: Counter[str] = Counter()
    a_rate = a_observed = a_evidence = 0
    for source, target, rate, observed, evidence_id in assessments.iter_rows():
        if source is not None:
            pairs[f"{source}->{target}"] += 1
        a_rate += _present(rate)
        a_observed += _present(observed)
        a_evidence += _present(evidence_id)
    fills = pl.read_parquet(run_dir / "FILLS.parquet", columns=["costs"])
    cost_entries = cost_converted = 0
    cost_pairs: Counter[str] = Counter()
    for (items,) in fills.iter_rows():
        for item in items or ():
            entry = json.loads(item)
            cost_entries += 1
            conversion = entry.get("conversion")
            if (
                conversion
                and _present(conversion.get("rate"))
                and _present(conversion.get("observed_at"))
            ):
                cost_converted += 1
                pair = f"{conversion.get('from_currency')}->{conversion.get('to_currency')}"
                cost_pairs[pair] += 1
    return {
        "evidence_rows_by_kind": dict(sorted(kinds.items())),
        "evidence_rows_with_conversion_paths": rows_with_paths,
        "conversion_paths": paths,
        "conversion_paths_with_legs": legged,
        "conversion_legs": legs,
        "conversion_legs_by_series": dict(sorted(legs_by_series.items())),
        "evidence_rows_with_conversion_paths_and_market_refs": rows_with_refs,
        "conversion_paths_with_rate": rate_ok,
        "conversion_paths_with_observed_at": observed_ok,
        "conversion_paths_staleness_derivable": staleness_ok,
        "risk_assessments": assessments.height,
        "risk_assessments_by_currency_pair": dict(sorted(pairs.items())),
        "risk_assessments_with_rate": a_rate,
        "risk_assessments_with_observed_at": a_observed,
        "risk_assessments_with_evidence_ref": a_evidence,
        "fill_cost_entries": cost_entries,
        "fill_cost_entries_with_conversion": cost_converted,
        "fill_cost_entries_by_currency_pair": dict(sorted(cost_pairs.items())),
    }


def main(argv: list[str]) -> int:
    out = {Path(arg).name: count(Path(arg)) for arg in argv}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
