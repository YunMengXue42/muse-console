"""E3: process-local Prometheus metrics."""

from __future__ import annotations

import pytest

from muse_console.core import metrics


@pytest.fixture(autouse=True)
def _clean():
    metrics.reset()
    yield
    metrics.reset()


def test_render_counters_and_histograms():
    metrics.media_outcome("video", "retry_text_only")
    metrics.media_outcome("video", "retry_text_only")
    metrics.first_token(3.2, 'a"b')
    text = metrics.render()
    assert 'muse_media_outcomes_total{kind="video",outcome="retry_text_only"} 2' in text
    assert "# TYPE muse_chat_first_token_seconds histogram" in text
    assert 'muse_chat_first_token_seconds_bucket{account="a\\"b",le="5"} 1' in text
    assert 'muse_chat_first_token_seconds_bucket{account="a\\"b",le="2.5"} 0' in text
    assert 'muse_chat_first_token_seconds_count{account="a\\"b"} 1' in text


def test_metrics_endpoint_requires_admin_and_counts_chat(client, auth, admin):
    assert client.get("/admin/metrics", headers=auth).status_code == 401
    r = client.post(
        "/v1/chat/completions",
        headers=auth,
        json={"model": "muse-chat", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 200
    body = client.get("/admin/metrics", headers=admin)
    assert body.status_code == 200
    assert body.headers["content-type"].startswith("text/plain")
    text = body.text
    assert 'muse_upstream_requests_total{account="anonymous",op="chat",result="ok"} 1' in text
    assert "muse_chat_first_token_seconds_count" in text
    assert "# TYPE muse_pool_accounts gauge" in text


def test_image_generation_recorded(client, auth, admin):
    r = client.post(
        "/v1/images/generations", headers=auth, json={"model": "muse-image", "prompt": "cat"}
    )
    assert r.status_code == 200
    text = client.get("/admin/metrics", headers=admin).text
    assert 'muse_generation_seconds_count{kind="image",result="ok"} 1' in text
