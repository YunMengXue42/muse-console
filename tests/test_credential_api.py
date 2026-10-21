from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from muse_console.api.routes.credential_ingestions import router
from test_credential_ingestion import envelope, setup_service


async def client_for(tmp_path):
    svc, grant = await setup_service(tmp_path)
    app = FastAPI()
    app.state.services = SimpleNamespace(credential_ingestion=svc, settings=svc.settings)
    app.include_router(router)
    return (
        svc,
        grant,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://ingest.example"
        ),
    )


async def test_device_transport_and_admin_separation(tmp_path):
    svc, grant, client = await client_for(tmp_path)
    headers = {"Authorization": "Bearer " + grant["token"]}
    async with client:
        response = await client.get("/credential-ingestions/server")
        assert response.json()["server_id"] == grant["server_id"]
        assert (await client.get("/admin/credential-devices", headers=headers)).status_code == 401
        assert (
            await client.post(
                "/credential-ingestions", headers={"Authorization": "Bearer admin-secret"}, json={}
            )
        ).status_code == 401
        env = envelope(grant["device_id"])
        result = await client.post(
            "/credential-ingestions", headers=headers, json=env.model_dump(mode="json")
        )
        assert result.status_code == 200 and result.json()["status"] == "committed"
        receipt = await client.get("/credential-ingestions/" + env.capture_id, headers=headers)
        assert receipt.json() == result.json()
        assert "secret_cookie" not in receipt.text and "fingerprint" not in receipt.text
        assert (
            await client.get("/credential-ingestions/missing_capture", headers=headers)
        ).status_code == 404
        rotated = await client.post("/credential-ingestions/rotate", headers=headers)
        assert rotated.status_code == 200
        assert (
            await client.get("/credential-ingestions/" + env.capture_id, headers=headers)
        ).status_code == 401
    await svc.close()


async def test_origin_host_body_and_validation_redaction(tmp_path):
    svc, grant, client = await client_for(tmp_path)
    auth = {"Authorization": "Bearer " + grant["token"]}
    async with client:
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}):
            assert (
                await client.get("/credential-ingestions/server", headers=headers)
            ).status_code == 403
        # Forwarded headers cannot select a different approved server.
        response = await client.get(
            "/credential-ingestions/server",
            headers={
                "Host": "evil.example",
                "X-Forwarded-Host": "ingest.example",
                "X-Forwarded-Proto": "https",
            },
        )
        assert response.status_code == 403
        response = await client.post("/credential-ingestions", headers=auth, content=b"x" * 65537)
        assert response.status_code == 413
        response = await client.post(
            "/credential-ingestions", headers=auth, json={"cookies": "secret-must-not-echo"}
        )
        assert response.status_code == 422 and "secret-must-not-echo" not in response.text
        other = envelope("other_device")
        response = await client.post(
            "/credential-ingestions", headers=auth, json=other.model_dump(mode="json")
        )
        assert response.status_code == 403
        svc.settings.credential_ingestion_enabled = False
        assert (await client.get("/credential-ingestions/server")).status_code == 404
    await svc.close()


async def test_challenge_and_own_receipt_only(tmp_path):
    svc, grant, client = await client_for(tmp_path)
    async with client:
        challenge = await client.post(
            "/admin/credential-devices/pairings",
            headers={"Authorization": "Bearer admin-secret"},
            json={},
        )
        assert challenge.status_code == 200
        assert challenge.json()["server_origin"] == "https://ingest.example"
        assert challenge.json()["fingerprint"] == svc.devices.server()["fingerprint"]
        pair = await client.post(
            "/credential-ingestions/pair",
            json={k: challenge.json()[k] for k in ("challenge", "server_id")},
        )
        assert pair.status_code == 200
        listing = await client.get(
            "/admin/credential-devices", headers={"Authorization": "Bearer admin-secret"}
        )
        assert listing.status_code == 200
        assert set(listing.json()["data"][0]) == {
            "id",
            "label",
            "expires_at",
            "revoked",
            "new_account_limit",
        }
        first = await svc.ingest(envelope(grant["device_id"]))
        changed = envelope(
            grant["device_id"], capture="capture_changed", revision=2, value="changed", base=1
        )
        assert (await svc.ingest(changed))["status"] == "needs_identity_confirmation"
        confirmation = await client.post(
            "/admin/credential-devices/" + grant["device_id"] + "/confirm",
            headers={"Authorization": "Bearer admin-secret"},
            json={"capture_id": changed.capture_id, "account_id": first["account_id"]},
        )
        assert confirmation.status_code == 200
        assert confirmation.json()["confirmed"] is True
        assert svc.pool.get(first["account_id"]).credential_version == 1
        assert (await svc.ingest(changed))["credential_version"] == 2
        stale = envelope(
            grant["device_id"], capture="capture_conflict", revision=3, value="candidate", base=1
        )
        headers = {"Authorization": "Bearer " + grant["token"]}
        response = await client.post(
            "/credential-ingestions", headers=headers, json=stale.model_dump(mode="json")
        )
        assert response.json()["status"] == "conflict"
        assert response.json()["account_id"] == first["account_id"]
        assert response.json()["credential_version"] == 2
        response = await client.post(
            "/admin/credential-devices/" + grant["device_id"] + "/confirm",
            headers={"Authorization": "Bearer admin-secret"},
            json={"capture_id": stale.capture_id, "account_id": first["account_id"]},
        )
        assert response.status_code == 200
        response = await client.post(
            "/credential-ingestions", headers=headers, json=stale.model_dump(mode="json")
        )
        assert response.json()["status"] == "committed"
        assert response.json()["credential_version"] == 3
        assert "secret_cookie" not in response.text and "fingerprint" not in response.text
        response = await client.get(
            "/credential-ingestions/capture_0001",
            headers={"Authorization": "Bearer " + pair.json()["token"]},
        )
        assert response.status_code == 404
        response = await client.delete(
            "/admin/credential-devices/" + grant["device_id"],
            headers={"Authorization": "Bearer admin-secret"},
        )
        assert response.status_code == 200
    await svc.close()


EXT_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


async def test_preflight_only_for_well_formed_extension_origin(tmp_path):
    svc, grant, client = await client_for(tmp_path)
    async with client:
        preflight = await client.request(
            "OPTIONS",
            "/credential-ingestions",
            headers={
                "Origin": EXT_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == EXT_ORIGIN
        assert preflight.headers["access-control-allow-methods"] == "GET, POST"
        assert preflight.headers["access-control-allow-headers"] == "authorization, content-type"
        assert "access-control-allow-credentials" not in preflight.headers
        for path in ("/credential-ingestions/server", "/credential-ingestions/rotate"):
            assert (
                await client.request("OPTIONS", path, headers={"Origin": EXT_ORIGIN})
            ).status_code == 204
        for origin in (
            "https://evil.example",
            "chrome-extension://abcdefghijklmnopabcdefghijklmno",
            "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        ):
            rejected = await client.request(
                "OPTIONS", "/credential-ingestions", headers={"Origin": origin}
            )
            assert rejected.status_code == 403
            assert "access-control-allow-origin" not in rejected.headers
        env = envelope(grant["device_id"])
        result = await client.post(
            "/credential-ingestions",
            headers={"Authorization": "Bearer " + grant["token"], "Origin": EXT_ORIGIN},
            json=env.model_dump(mode="json"),
        )
        assert result.status_code == 200
        assert result.headers["access-control-allow-origin"] == EXT_ORIGIN
        plain = await client.get("/credential-ingestions/server")
        assert "access-control-allow-origin" not in plain.headers
    await svc.close()
