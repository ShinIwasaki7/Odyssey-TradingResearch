"""再取得（補充）の設定の読込（D03 §9・§14.5・§14.9・§14.10）。

- 実物の提供元の設定（`configs/datasources/dukascopy_tick_v1.yaml`）が読め、10 銘柄の価格の
  桁と pip の大きさ（JPY を含む銘柄は 1000・0.01、他は 100000・0.0001）と、通信の試行の初期値
  （D03 §14.9）を持つ。
- 設定ファイルの正規化内容のダイジェストはコメントに依存せず、中身が変われば変わる。
- https 以外の URL・未宣言キー・10 のべきでない桁は拒否する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from odyssey_fx.app.config import ConfigError
from odyssey_fx.app.config.refill import load_calendar_ref, load_refill_provider
from odyssey_fx.common.money import decimal_from_str
from odyssey_fx.common.symbol import Symbol

REPO_ROOT = Path(__file__).resolve().parents[3]
PROVIDER = REPO_ROOT / "configs/datasources/dukascopy_tick_v1.yaml"
CALENDAR = REPO_ROOT / "configs/calendars/fx_ny17_v2.yaml"


def test_the_provider_settings_load() -> None:
    reference = load_refill_provider(PROVIDER)
    settings = reference.settings
    assert (settings.id, settings.version) == ("dukascopy_tick", 1)
    assert len(settings.symbols) == 10
    for entry in settings.symbols:
        jpy = "JPY" in str(entry.symbol)
        assert entry.price_scale == (1000 if jpy else 100000)
        assert entry.pip_size == decimal_from_str("0.01" if jpy else "0.0001")
    comm = settings.communication
    assert (
        comm.request_interval_seconds,
        comm.request_timeout_seconds,
        comm.max_retries,
        comm.backoff_initial_seconds,
        comm.backoff_max_seconds,
        comm.pause_after_consecutive_failures,
        comm.pause_seconds,
    ) == (8, 60, 5, 30, 480, 3, 180)
    assert settings.symbol(Symbol("USDJPY")).price_scale == 1000


def test_the_content_digest_ignores_comments_but_not_values(tmp_path: Path) -> None:
    text = PROVIDER.read_text(encoding="utf-8")
    commented = tmp_path / "commented.yaml"
    commented.write_text("# another comment\n" + text, encoding="utf-8")
    changed = tmp_path / "changed.yaml"
    changed.write_text(
        text.replace("request_interval_seconds: 8", "request_interval_seconds: 12"),
        encoding="utf-8",
    )
    original = load_refill_provider(PROVIDER).content_digest
    assert load_refill_provider(commented).content_digest == original
    assert load_refill_provider(changed).content_digest != original


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("https://datafeed", "http://datafeed"),
        ("price_scale: 1000\n", "price_scale: 1500\n"),
        ("pause_seconds: 180", "pause_seconds: 180\n  unknown_key: 1"),
        ("/{month0}/", "/{month}/"),
    ],
)
def test_invalid_provider_settings_are_rejected(tmp_path: Path, old: str, new: str) -> None:
    path = tmp_path / "provider.yaml"
    path.write_text(PROVIDER.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_refill_provider(path)


def test_the_calendar_reference_carries_its_content() -> None:
    calendar, reference = load_calendar_ref(CALENDAR)
    assert (reference.id, reference.version) == (calendar.id, calendar.version) == ("fx_ny17", 2)
    assert reference.content["version"] == 2
