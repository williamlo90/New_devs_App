"""Regression tests for revenue cache isolation (no external services)."""
import json
from unittest.mock import AsyncMock

import pytest

from app.services import cache, reservations


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("tenant-a", "tenant-b"), ("tenant-b", "tenant-a")])
async def test_shared_property_id_never_reuses_another_tenants_revenue(monkeypatch, order):
    redis = MemoryRedis()
    monkeypatch.setattr(cache, "redis_client", redis)
    monkeypatch.setattr(reservations, "get_owned_property", AsyncMock(), raising=False)
    totals = {"tenant-a": "2250.000", "tenant-b": "0.00"}

    async def calculate(property_id, tenant_id):
        return {"property_id": property_id, "tenant_id": tenant_id,
                "total": totals[tenant_id], "currency": "USD", "count": 0}

    calculator = AsyncMock(side_effect=calculate)
    monkeypatch.setattr(reservations, "calculate_total_revenue", calculator)
    for tenant in order * 3:
        result = await cache.get_revenue_summary("prop-001", tenant)
        assert result["tenant_id"] == tenant
        assert result["total"] == totals[tenant]

    assert calculator.await_count == 2  # One miss per tenant, then cache hits.
    assert len(redis.values) == 2
    assert set(redis.ttls.values()) == {300}


@pytest.mark.asyncio
async def test_legacy_and_mismatched_cache_entries_are_not_served(monkeypatch):
    redis = MemoryRedis()
    wrong = {"property_id": "prop-001", "tenant_id": "tenant-a",
             "total": "2250.000", "currency": "USD", "count": 4}
    redis.values["revenue:prop-001"] = json.dumps(wrong)
    redis.values['revenue:v2:["tenant-b","prop-001"]'] = json.dumps(wrong)
    redis.values['revenue:v3:["tenant-b","prop-001",null,null,null]'] = json.dumps(wrong)
    monkeypatch.setattr(cache, "redis_client", redis)
    monkeypatch.setattr(reservations, "get_owned_property", AsyncMock(), raising=False)
    expected = {**wrong, "tenant_id": "tenant-b", "total": "0.00", "count": 0}
    monkeypatch.setattr(reservations, "calculate_total_revenue", AsyncMock(return_value=expected))
    assert await cache.get_revenue_summary("prop-001", "tenant-b") == expected
