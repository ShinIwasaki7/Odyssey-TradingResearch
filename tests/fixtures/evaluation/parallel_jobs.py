"""並列の実行（D26 §3.4）の試験で組の子が走らせる仕事（子のプロセスが名前から読み込む）。

`spawn` で起こした子は親の `sys.path` を受け継ぐので、このモジュールを読み込める。仕事の入力
（`payload`）は `parallel_search.dumps` で直列化した対応表（`Mapping[str, object]`）。

- `wait_for_stop`: 自分の PID を書き、止める指示（境目の確かめの例外）が来るまで境目を回る。
- `raise_now`: すぐに例外を送出する（親から見て E4）。
- `raise_later`: 少し待ってから例外を送出する。
- `exit_hard`: 結果も例外も返さずにプロセスを終える（親から見て E5）。
- `return_value`: 入力の `value` を返す（E3）。
- `ignore_stop_and_return`: 止める指示を見ずに、少し待ってから値を返す（S3 で E3 を起こす）。
- `failing_candidate_after_first_unit`: 探索の実験の本物の仕事を走らせ、候補の組だけ最初の単位の
  後の境目で例外を送出する（結合試験の S1/S2 + E4）。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from odyssey_fx.app import composition
from odyssey_fx.app.parallel_search import loads
from odyssey_fx.evaluation.application.run_experiment import VariantRun
from odyssey_fx.evaluation.domain.variant import Variant


def _options(payload: bytes) -> Mapping[str, Any]:
    value = loads(payload)
    if not isinstance(value, Mapping):
        raise TypeError("the test job expects a mapping")
    return value


def _write_pid(options: Mapping[str, Any]) -> None:
    """PID を別名に書いてから置き換える（ファイルがあれば読む試験に空の中身を見せない）。"""
    path = options.get("pid_file")
    if isinstance(path, str):
        target = Path(path)
        partial = target.with_name(f"{target.name}.{os.getpid()}.partial")
        partial.write_text(str(os.getpid()), encoding="ascii")
        os.replace(partial, target)


def wait_for_stop(payload: bytes, boundary: Callable[[], None]) -> object:
    options = _options(payload)
    _write_pid(options)
    deadline = time.monotonic() + float(options.get("timeout", 60))
    count = 0
    while time.monotonic() < deadline:
        boundary()
        count += 1
        marker = options.get("count_file")
        if isinstance(marker, str):
            Path(marker).write_text(str(count), encoding="ascii")
        time.sleep(0.05)
    raise TimeoutError("the stop instruction did not arrive")


def raise_now(payload: bytes, boundary: Callable[[], None]) -> object:
    del boundary
    _write_pid(_options(payload))
    raise RuntimeError("injected failure (test)")


def raise_later(payload: bytes, boundary: Callable[[], None]) -> object:
    del boundary
    options = _options(payload)
    _write_pid(options)
    time.sleep(float(options.get("delay", 0.5)))
    raise RuntimeError("injected late failure (test)")


def exit_hard(payload: bytes, boundary: Callable[[], None]) -> object:
    del boundary
    _write_pid(_options(payload))
    os._exit(3)


def return_value(payload: bytes, boundary: Callable[[], None]) -> object:
    options = _options(payload)
    _write_pid(options)
    boundary()
    return options.get("value")


def ignore_stop_and_return(payload: bytes, boundary: Callable[[], None]) -> object:
    del boundary
    options = _options(payload)
    _write_pid(options)
    time.sleep(float(options.get("delay", 1.0)))
    return options.get("value")


def failing_candidate_after_first_unit(payload: bytes, boundary: Callable[[], None]) -> VariantRun:
    """探索の実験の組の子の本物の仕事。候補の組だけ、最初の単位を終えた後の境目で失敗する。"""
    work = composition._load_work(payload)
    if work.variant is not Variant.CANDIDATE:
        return composition.search_variant_work(payload, boundary)
    calls = [0]

    def failing() -> None:
        boundary()
        calls[0] += 1
        if calls[0] == 2:  # 1 回目は組み立ての後（最初の単位の前）、2 回目は最初の単位の後
            raise RuntimeError("injected failure after the first candidate unit (test)")

    return composition.search_variant_work(payload, failing)
