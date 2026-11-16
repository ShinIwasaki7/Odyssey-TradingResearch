"""並列の実行の親を別プロセスで走らせる試験の道具（親が合図を受ける場合。D26 §3.4 の E6・F3）。

使い方: ``python -m tests.fixtures.evaluation.parallel_harness <作業ディレクトリ> <仕事>``

両組の子に同じ仕事（`parallel_jobs` の関数名）を走らせる。子の PID は
``<作業ディレクトリ>/<組>.pid`` に書かれる。親は ``<作業ディレクトリ>/lock/`` の実行中の印の
ロックを取り、子へ引き継ぐ。終わり方は終了コードで分かる（合図で終われば負の値）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from odyssey_fx.app.parallel_search import (
    VariantJob,
    dumps,
    execution_lock,
    run_variant_jobs,
)
from odyssey_fx.evaluation.domain.variant import Paired
from tests.fixtures.evaluation import parallel_jobs


def main() -> int:
    directory = Path(sys.argv[1])
    work = getattr(parallel_jobs, sys.argv[2])
    jobs = Paired.build(
        lambda variant: VariantJob(
            variant=variant,
            work=work,
            payload=dumps(
                {
                    "pid_file": str(directory / f"{variant.value}.pid"),
                    "value": variant.value,
                    "delay": 4.0,
                    "timeout": 60,
                }
            ),
        )
    )
    with execution_lock(directory / "lock") as lock:
        run_variant_jobs(jobs, lock=lock, notify=lambda text: print(text, flush=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
