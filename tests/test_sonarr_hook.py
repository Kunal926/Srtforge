from __future__ import annotations

from pathlib import Path

import pytest

from srtforge import sonarr_hook


class DummyConsole:
    def log(self, *args, **kwargs):  # pragma: no cover - trivial
        pass

    def print(self, *args, **kwargs):  # pragma: no cover - trivial
        pass


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for key in [
        "SONARR_EVENTTYPE",
        "sonarr_eventtype",
        "SONARR_EPISODEFILE_PATH",
        "sonarr_episodefile_path",
        "SONARR_EPISODE_FILE_PATH",
        "sonarr_episode_file_path",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_runs_pipeline_for_download_event(monkeypatch, tmp_path):
    media = tmp_path / "episode.mkv"
    called = {}

    def fake_run_pipeline(config):
        called["config"] = config

    monkeypatch.setattr(sonarr_hook, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(sonarr_hook, "get_console", lambda: DummyConsole())

    monkeypatch.setenv("SONARR_EVENTTYPE", "Download")
    monkeypatch.setenv("SONARR_EPISODEFILE_PATH", str(media))

    sonarr_hook.main()

    assert "config" in called
    assert called["config"].media_path == Path(media)


def test_alias_events_are_supported(monkeypatch, tmp_path):
    media = tmp_path / "episode2.mkv"
    invoked = {}

    monkeypatch.setattr(sonarr_hook, "run_pipeline", lambda config: invoked.setdefault("config", config))
    monkeypatch.setattr(sonarr_hook, "get_console", lambda: DummyConsole())

    monkeypatch.setenv("sonarr_eventtype", "OnImport")
    monkeypatch.setenv("sonarr_episodefile_path", str(media))

    sonarr_hook.main()

    assert "config" in invoked
    assert invoked["config"].media_path == Path(media)


def test_ignores_unrelated_events(monkeypatch, tmp_path):
    media = tmp_path / "episode3.mkv"
    monkeypatch.setenv("SONARR_EVENTTYPE", "Test")
    monkeypatch.setenv("SONARR_EPISODEFILE_PATH", str(media))

    monkeypatch.setattr(sonarr_hook, "run_pipeline", lambda config: (_ for _ in ()).throw(AssertionError("should not run")))
    monkeypatch.setattr(sonarr_hook, "get_console", lambda: DummyConsole())

    sonarr_hook.main()
