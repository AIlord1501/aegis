import pytest


async def register_and_login(client, email: str) -> dict[str, str]:
    response = await client.post("/auth/register", json={"email": email, "password": "correct horse battery staple"})
    assert response.status_code == 201
    response = await client.post("/auth/login", json={"email": email, "password": "correct horse battery staple"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_register_login_and_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    headers = await register_and_login(client, "alice@example.com")
    response = await client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["email"] == "alice@example.com"


@pytest.mark.asyncio
async def test_password_is_not_accepted_when_wrong(client):
    await client.post("/auth/register", json={"email": "alice@example.com", "password": "correct horse battery staple"})
    response = await client.post("/auth/login", json={"email": "alice@example.com", "password": "wrong password"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_services_and_incidents_are_user_scoped(client):
    alice = await register_and_login(client, "alice@example.com")
    bob = await register_and_login(client, "bob@example.com")
    service = await client.post("/services", headers=alice, json={"name": "payments"})
    assert service.status_code == 201
    service_id = service.json()["id"]

    assert (await client.get("/services", headers=alice)).json()[0]["id"] == service_id
    assert (await client.get("/services", headers=bob)).json() == []
    assert (await client.get(f"/services/{service_id}", headers=bob)).status_code == 404
    assert (await client.patch(f"/services/{service_id}", headers=bob, json={"name": "stolen"})).status_code == 404
    assert (await client.delete(f"/services/{service_id}", headers=bob)).status_code == 404

    incident = await client.post(
        "/incidents", headers=alice, json={"service_id": service_id, "title": "Checkout outage"}
    )
    assert incident.status_code == 201
    incident_id = incident.json()["id"]
    assert (await client.get("/incidents", headers=bob)).json() == []
    assert (await client.get(f"/incidents/{incident_id}", headers=bob)).status_code == 404
    assert (await client.post("/incidents", headers=bob, json={"service_id": service_id, "title": "Intrusion"})).status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_resources_are_rejected(client):
    assert (await client.get("/services")).status_code == 401
    assert (await client.get("/incidents")).status_code == 401
