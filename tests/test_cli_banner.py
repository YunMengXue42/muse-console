from __future__ import annotations

from muse_console.__main__ import dashboard_url, startup_banner
from muse_console.config import Settings


def _settings(tmp_path, **kw) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", **kw)


def test_dashboard_url_maps_wildcard_host_to_loopback(tmp_path):
    assert dashboard_url(_settings(tmp_path, host="0.0.0.0")) == "http://127.0.0.1:18610/dashboard"
    assert dashboard_url(_settings(tmp_path, host="::1", port=9)) == "http://[::1]:9/dashboard"
    s = _settings(tmp_path, public_base="https://api.example.com/")
    assert dashboard_url(s) == "https://api.example.com/dashboard"


def test_banner_prints_generated_key_only_on_first_start(tmp_path):
    s = _settings(tmp_path)
    key = s.resolve_api_key()
    first = startup_banner(s, key_was_generated=True)
    assert key in first and "/dashboard" in first
    later = startup_banner(s, key_was_generated=False)
    assert key not in later
    assert str(s.key_file) in later


def test_banner_never_prints_configured_keys(tmp_path):
    s = _settings(tmp_path, api_key="configured-secret", admin_key="admin-secret")
    text = startup_banner(s, key_was_generated=False)
    assert "configured-secret" not in text and "admin-secret" not in text
