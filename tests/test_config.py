"""Brand migration settings use disposable dotenv files, never the real .env."""

import os

import pytest

from muse_console.config import Settings


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    for name in os.environ:
        if name.upper().startswith(("MUSE_CONSOLE_", "MUSE2API_")):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("prefix", ["MUSE_CONSOLE_", "MUSE2API_"])
@pytest.mark.parametrize("medium", ["env", "dotenv"])
def test_both_namespaces_supported(tmp_path, monkeypatch, prefix, medium):
    values = {"PORT": "18001", "HEADLESS": "false", "DATA_DIR": str(tmp_path / "storage")}
    if medium == "env":
        for key, value in values.items():
            monkeypatch.setenv(prefix + key, value)
    else:
        (tmp_path / ".env").write_text(
            "\n".join(f"{prefix}{key}={value}" for key, value in values.items()), encoding="utf-8"
        )
    settings = Settings()
    assert settings.port == 18001
    assert settings.headless is False
    assert settings.data_dir == tmp_path / "storage"


@pytest.mark.parametrize("new_medium", ["env", "dotenv"])
@pytest.mark.parametrize("old_medium", ["env", "dotenv"])
def test_new_name_wins_across_media(tmp_path, monkeypatch, new_medium, old_medium):
    lines = []
    for prefix, value, medium in (
        ("MUSE_CONSOLE_", "18002", new_medium),
        ("MUSE2API_", "18003", old_medium),
    ):
        if medium == "env":
            monkeypatch.setenv(prefix + "PORT", value)
        else:
            lines.append(f"{prefix}PORT={value}")
    (tmp_path / ".env").write_text("\n".join(lines), encoding="utf-8")
    assert Settings().port == 18002
    assert Settings(port=18004).port == 18004


@pytest.mark.parametrize("prefix", ["MUSE_CONSOLE_", "MUSE2API_"])
def test_environment_wins_within_namespace(tmp_path, monkeypatch, prefix):
    (tmp_path / ".env").write_text(f"{prefix}PORT=18001", encoding="utf-8")
    monkeypatch.setenv(prefix + "PORT", "18002")
    assert Settings().port == 18002


@pytest.mark.parametrize("prefix", ["MUSE_CONSOLE_", "MUSE2API_"])
def test_env_file_none_does_not_read_dotenv(tmp_path, monkeypatch, prefix):
    (tmp_path / ".env").write_text(f"{prefix}PORT=invalid", encoding="utf-8")
    assert Settings(_env_file=None).port == 18610
    monkeypatch.setenv(prefix + "PORT", "18005")
    assert Settings(_env_file=None).port == 18005


def test_explicit_dotenv_path_and_legacy_fallback(tmp_path):
    path = tmp_path / "custom.env"
    path.write_text("MUSE2API_PORT=18006\nMUSE_CONSOLE_HOST=0.0.0.0", encoding="utf-8")
    settings = Settings(_env_file=path)
    assert settings.port == 18006
    assert settings.host == "0.0.0.0"
