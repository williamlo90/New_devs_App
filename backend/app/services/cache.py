import json
import redis.asyncio as redis
from typing import Dict, Any
import os

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

async def get_revenue_summary(property_id: str, tenant_id: str, month: int = None, year: int = None) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    """
    from app.services.reservations import get_owned_property, calculate_total_revenue

    # Authorization must precede cache reads, including for a warm cache.
    owned = await get_owned_property(property_id, tenant_id)
    cache_key = "revenue:v3:" + json.dumps([tenant_id, property_id, year, month, owned["timezone"] if month is not None else None], separators=(",", ":"))
    
    # Try to get from cache
    cached = await redis_client.get(cache_key)
    if cached:
        try:
            result = json.loads(cached)
            if (isinstance(result, dict) and result.get("tenant_id") == tenant_id
                    and result.get("property_id") == property_id):
                return result
        except (ValueError, TypeError):
            pass
    
    # Calculate revenue
    if month is None:
        result = await calculate_total_revenue(property_id, tenant_id)
    else:
        result = await calculate_total_revenue(property_id, tenant_id, month, year, owned["timezone"])
    
    # Cache the result for 5 minutes
    await redis_client.setex(cache_key, 300, json.dumps(result))
    
    return result
