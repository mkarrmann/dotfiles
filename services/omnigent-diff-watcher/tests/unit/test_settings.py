from __future__ import annotations

from pathlib import Path

import pytest

from omnigent_diff_watcher.settings import ServiceSettings


def test_loads_checked_in_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_URL", raising=False)
    settings = ServiceSettings.load(Path(__file__).resolve().parents[2] / "config.toml")
    assert settings.server_url == "http://127.0.0.1:6767"
    assert settings.delivery_mode == "log_only"
    assert settings.delivery_session_allowlist == frozenset()
    assert settings.watcher.batch_window_seconds == 300


def test_environment_server_url_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config.toml"
    config.write_text('server_url = "http://configured:1"\n')
    monkeypatch.setenv("OMNIGENT_URL", "http://environment:2/")
    assert ServiceSettings.load(config).server_url == "http://environment:2"


@pytest.mark.parametrize(
    "text",
    [
        'delivery_mode = "unsafe"\n',
        "reconcile_interval_seconds = 0\n",
        "unknown = true\n",
        "[watcher]\nunknown = 1\n",
    ],
)
def test_rejects_invalid_or_unknown_configuration(tmp_path: Path, text: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(text)
    with pytest.raises(ValueError):
        ServiceSettings.load(config)
