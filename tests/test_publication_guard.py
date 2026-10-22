from tools.check_publication import private_path, scan_blob


def test_private_paths_are_blocked_but_examples_are_allowed():
    for path in (
        ".env",
        ".env.production",
        "nested/.env.local",
        "data/accounts.json",
        "data-preview/deployment.json",
        "export/session.har",
        "account.db-wal",
        "credentials.key",
        "browser-profile/Default/Cookies",
    ):
        assert private_path(path), path
    for path in (".env.example", "tests/test_credentials.py", "frontend/package-lock.json"):
        assert not private_path(path), path


def test_findings_are_redacted_and_include_location():
    value = b"ghp_" + b"a" * 36
    result = scan_blob("example.txt", b"first line\n" + value, "object-id")
    assert result == [
        {"rule": "github-token", "path": "example.txt", "object": "object-id", "line": 2}
    ]
    assert value.decode() not in str(result)


def test_long_session_cookie_and_key_are_detected_without_echoing_values():
    for value in (b'hatch_sess="' + b"x" * 80 + b'"', b"m2a-" + b"x" * 36):
        assert scan_blob("fixture.txt", value, "object-id")
    assert not scan_blob("fixture.txt", b'hatch_sess="test-only"', "object-id")


def test_private_session_reference_is_detected_without_echo():
    value = b"https://claude.ai/code/" + b"session_synthetic-reference"
    result = scan_blob("<commit-metadata>", value, "object-id")
    assert result[0]["rule"] == "private-ai-session"
    assert value.decode() not in str(result)
