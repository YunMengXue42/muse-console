from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from muse2api.app import create_app
from muse2api.drivers.mock import MockDriver


@pytest.mark.parametrize("user", [None, "same-client-supplied-user"])
def test_conversation_owner_bound_to_authenticated_key(settings, user):
    class RecordingDriver(MockDriver):
        def __init__(self):
            super().__init__(delay=0)
            self.hints = []

        async def chat_stream(self, account, req):
            self.hints.append(req.conversation_hint)
            yield "offline"

    driver = RecordingDriver()
    with TestClient(create_app(settings, driver)) as client:
        admin = {"Authorization": "Bearer admin-key"}
        keys = [
            client.post("/admin/keys", headers=admin, json={"name": name}).json()["api_key"]
            for name in ("alice", "bob")
        ]
        body = {"messages": [{"role": "user", "content": "same history"}]}
        if user is not None:
            body["user"] = user
        for key in (keys[0], keys[1], keys[0]):
            response = client.post(
                "/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body
            )
            assert response.status_code == 200
        assert driver.hints[0] == driver.hints[2]
        assert driver.hints[0] != driver.hints[1]
        assert all(len(hint) == 64 for hint in driver.hints)
        assert all(key not in str(driver.hints) for key in keys)
        body["user"] = "different-user"
        assert (
            client.post(
                "/v1/chat/completions", headers={"Authorization": f"Bearer {keys[0]}"}, json=body
            ).status_code
            == 200
        )
        assert driver.hints[-1] != driver.hints[0]
