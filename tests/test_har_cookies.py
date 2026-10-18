"""HAR cookie extraction used when importing a real account."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("har_cookies", REPO / "tools" / "har_cookies.py")
har_cookies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(har_cookies)


def _har(*headers: str) -> dict:
    return {
        "log": {
            "entries": [
                {
                    "request": {
                        "url": f"https://muse.ai/api/hatch/vm/wake?n={index}",
                        "headers": [{"name": "Cookie", "value": value}],
                    }
                }
                for index, value in enumerate(headers)
            ]
        }
    }


def test_extract_merges_cookie_headers_from_muse_requests():
    har = _har(
        "hatch_sess=s1; hatch_vml=v1",
        "hatch_gw=g1; hatch_native_auth_device=d1; theme=dark",
    )
    assert har_cookies.extract(har) == {
        "hatch_sess": "s1",
        "hatch_vml": "v1",
        "hatch_gw": "g1",
        "hatch_native_auth_device": "d1",
        "theme": "dark",
    }


def test_extract_ignores_other_hosts():
    har = {
        "log": {
            "entries": [
                {
                    "request": {
                        "url": "https://example.com/",
                        "headers": [{"name": "Cookie", "value": "hatch_sess=leak"}],
                    }
                }
            ]
        }
    }
    assert har_cookies.extract(har) == {}


def test_cli_returns_three_when_session_cookie_missing(tmp_path, capsys, monkeypatch):
    path = tmp_path / "muse.har"
    path.write_text(json.dumps(_har("hatch_sess=s1; theme=dark")), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["har_cookies.py", str(path)])
    assert har_cookies.main() == 3
    out = capsys.readouterr()
    assert "hatch_sess=s1" in out.out
    assert "theme" not in out.out
