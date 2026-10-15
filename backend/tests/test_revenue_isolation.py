from decimal import Decimal
"""Run against a dedicated database loaded with database/schema.sql and seed.sql.

Set TEST_DATABASE_URL and TEST_REDIS_URL (Redis database 15, reserved for tests).
Authentication, SQL queries, and Redis are real; no auth dependency overrides.
"""
import json
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL for integration tests"
)]


@pytest_asyncio.fixture
async def api(monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from redis.asyncio import Redis
    from app.config import settings
    from app.core.auth import clear_auth_cache
    from app.core.database_pool import db_pool
    from app.services import cache
    from app.api.v1 import login, dashboard

    redis_url = os.environ["TEST_REDIS_URL"]
    assert redis_url.endswith("/15"), "Use a dedicated Redis test database (15)"
    assert "isolation_test" in os.environ["TEST_DATABASE_URL"]
    monkeypatch.setattr(settings, "database_url", os.environ["TEST_DATABASE_URL"])
    redis = Redis.from_url(redis_url)
    monkeypatch.setattr(cache, "redis_client", redis)
    await redis.flushdb()
    clear_auth_cache()
    app = FastAPI()
    app.include_router(login.router, prefix="/api/v1")
    app.include_router(dashboard.router, prefix="/api/v1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, redis
    await redis.flushdb()
    await redis.aclose()
    await db_pool.close()
    db_pool.engine = db_pool.session_factory = None
    clear_auth_cache()


async def sign_in(client, tenant):
    email, password = {
        "tenant-a": ("sunset@propertyflow.com", "client_a_2024"),
        "tenant-b": ("ocean@propertyflow.com", "client_b_2024"),
    }[tenant]
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["access_token"]}


@pytest.mark.parametrize("order", [("tenant-a", "tenant-b"), ("tenant-b", "tenant-a")])
async def test_alternating_logins_refreshes_cache_hits_and_misses(api, order):
    client, redis = api
    headers = {tenant: await sign_in(client, tenant) for tenant in order}
    for i, tenant in enumerate(order * 4):
        if i == 4:
            await redis.flushdb()  # Expiration/miss followed by another round of hits.
        response = await client.get("/api/v1/dashboard/summary",
                                    params={"property_id": "prop-001", "_t": i}, headers=headers[tenant])
        assert response.status_code == 200, response.text
        assert Decimal(response.json()["total_revenue"]) == (2250 if tenant == "tenant-a" else 0)
        assert response.json()["reservations_count"] == (4 if tenant == "tenant-a" else 0)
    keys = await redis.keys("revenue:*")
    assert len(keys) == 2
    assert {json.loads(await redis.get(key))["tenant_id"] for key in keys} == set(order)
    assert all(0 < ttl <= 300 for ttl in [await redis.ttl(key) for key in keys])


@pytest.mark.parametrize("tenant,foreign_id", [("tenant-a", "prop-004"), ("tenant-b", "prop-002")])
async def test_foreign_properties_rejected_even_with_cache_or_spoofed_tenant(api, tenant, foreign_id):
    client, redis = api
    headers = await sign_in(client, tenant)
    other = "tenant-b" if tenant == "tenant-a" else "tenant-a"
    cold = await client.get("/api/v1/dashboard/summary", params={"property_id": foreign_id}, headers=headers)
    assert cold.status_code == 404
    await client.get("/api/v1/dashboard/summary", params={"property_id": foreign_id},
                     headers=await sign_in(client, other))
    key = "revenue:v3:" + json.dumps([tenant, foreign_id, None, None, None], separators=(",", ":"))
    await redis.setex(key, 300, json.dumps({"tenant_id": tenant, "property_id": foreign_id,
                                          "total": "999", "count": 1, "currency": "USD"}))
    response = await client.get("/api/v1/dashboard/summary",
                                params={"property_id": foreign_id, "tenant_id": other},
                                headers={**headers, "X-Simulated-Tenant": other, "X-Tenant-ID": other})
    assert response.status_code == 404


@pytest.mark.parametrize("tenant,ids,name", [
    ("tenant-a", {"prop-001", "prop-002", "prop-003"}, "Beach House Alpha"),
    ("tenant-b", {"prop-001", "prop-004", "prop-005"}, "Mountain Lodge Beta"),
])
async def test_property_list_is_scoped_to_authenticated_tenant(api, tenant, ids, name):
    client, _ = api
    response = await client.get("/api/v1/dashboard/properties", headers=await sign_in(client, tenant))
    assert response.status_code == 200
    assert {p["id"] for p in response.json()} == ids
    assert next(p for p in response.json() if p["id"] == "prop-001")["name"] == name


async def test_missing_forged_expired_and_static_tokens_are_rejected(api):
    from jose import jwt
    from app.config import settings

    client, _ = api
    claims = {"id": "attacker", "email": "candidate@propertyflow.com", "aud": "authenticated",
              "app_metadata": {"tenant_id": "tenant-a"},
              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    tokens = [None, "mock-token-123", jwt.encode(claims, "wrong-secret", algorithm="HS256"),
              jwt.encode({**claims, "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
                         settings.secret_key, algorithm="HS256")]
    for token in tokens:
        headers = {"Authorization": "Bearer " + token} if token else {}
        response = await client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers)
        assert response.status_code == 401


async def test_tenant_claim_is_required_and_user_metadata_cannot_override_it(api):
    from jose import jwt
    from app.config import settings

    client, _ = api
    claims = {"id": "user-ocean", "email": "ocean@propertyflow.com", "aud": "authenticated",
              "user_metadata": {"tenant_id": "tenant-a"},
              "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    for metadata, expected_status in [({}, 403), ({"tenant_id": "tenant-b"}, 200)]:
        token = jwt.encode({**claims, "app_metadata": metadata}, settings.secret_key, algorithm="HS256")
        response = await client.get("/api/v1/dashboard/summary?property_id=prop-001",
                                    headers={"Authorization": "Bearer " + token})
        assert response.status_code == expected_status
        if expected_status == 200:
            assert Decimal(response.json()["total_revenue"]) == 0


async def test_unknown_or_wrong_password_cannot_acquire_tenant_access(api):
    client, _ = api
    for email in ["sunset@propertyflow.com", "ocean@propertyflow.com", "candidate@propertyflow.com"]:
        response = await client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
        assert response.status_code == 401


async def test_database_failure_does_not_return_mock_or_cached_revenue(api, monkeypatch):
    from app.core.database_pool import db_pool

    client, _ = api
    headers = await sign_in(client, "tenant-a")
    assert (await client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers)).status_code == 200
    monkeypatch.setattr(db_pool, "initialize", AsyncMock(side_effect=RuntimeError("database unavailable")))
    response = await client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers)
    assert response.status_code == 503


async def test_concurrent_cold_requests_remain_isolated(api):
    client, _ = api
    headers = {t: await sign_in(client, t) for t in ("tenant-a", "tenant-b")}
    tenants = ["tenant-a", "tenant-b"] * 10
    responses = await asyncio.gather(*[
        client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers[t])
        for t in tenants
    ])
    for tenant, response in zip(tenants, responses):
        assert response.status_code == 200
        assert Decimal(response.json()["total_revenue"]) == (2250 if tenant == "tenant-a" else 0)


async def test_cached_revenue_is_rejected_after_property_access_is_removed(api):
    from sqlalchemy import text
    from app.core.database_pool import db_pool

    client, _ = api
    headers = await sign_in(client, "tenant-b")
    assert (await client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers)).status_code == 200
    # Only modify the dedicated test database; this property has no reservations.
    async with db_pool.get_session() as session:
        await session.execute(text("DELETE FROM properties WHERE tenant_id='tenant-b' AND id='prop-001'"))
        await session.commit()
    try:
        response = await client.get("/api/v1/dashboard/summary?property_id=prop-001", headers=headers)
        assert response.status_code == 404
    finally:
        async with db_pool.get_session() as session:
            await session.execute(text("""
                INSERT INTO properties (id, tenant_id, name, timezone)
                VALUES ('prop-001', 'tenant-b', 'Mountain Lodge Beta', 'America/New_York')
            """))
            await session.commit()
