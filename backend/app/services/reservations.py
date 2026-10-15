from datetime import datetime
from typing import Dict, Any, List
from contextlib import asynccontextmanager
import logging
from fastapi import HTTPException
from sqlalchemy import text

logger = logging.getLogger(__name__)

@asynccontextmanager
async def revenue_session():
    from app.core.database_pool import db_pool

    try:
        await db_pool.initialize()
        async with db_pool.get_session() as session:
            yield session
    except HTTPException:
        raise
    except Exception:
        logger.exception("Revenue database unavailable")
        raise HTTPException(status_code=503, detail="Revenue data temporarily unavailable")


async def list_tenant_properties(tenant_id: str) -> List[Dict[str, Any]]:
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Tenant membership required")
    async with revenue_session() as session:
        result = await session.execute(text("""
            SELECT id, name, timezone FROM properties
            WHERE tenant_id = :tenant_id ORDER BY id
        """), {"tenant_id": tenant_id})
        return [dict(row) for row in result.mappings()]


async def get_owned_property(property_id: str, tenant_id: str) -> Dict[str, Any]:
    if not tenant_id:
        raise HTTPException(status_code=403, detail="Tenant membership required")
    async with revenue_session() as session:
        result = await session.execute(text("""
            SELECT id, name, timezone FROM properties
            WHERE id = :property_id AND tenant_id = :tenant_id
        """), {"property_id": property_id, "tenant_id": tenant_id})
        row = result.mappings().first()
        if row is None:
            # Identical response for a foreign property and a nonexistent property.
            raise HTTPException(status_code=404, detail="Property not found")
        return dict(row)


async def calculate_total_revenue(property_id: str, tenant_id: str, month: int = None, year: int = None, property_timezone: str = None) -> Dict[str, Any]:
    """Aggregate only reservations belonging to the authenticated tenant."""
    params = {"property_id": property_id, "tenant_id": tenant_id}
    date_filter = ""
    if month is not None:
        # The existing recognition rule uses the whole amount at check-in.
        # Convert each local midnight independently: DST may change the offset.
        params.update(start=datetime(year, month, 1),
                      end=datetime(year + (month == 12), month % 12 + 1, 1),
                      timezone=property_timezone)
        date_filter = """
            AND check_in_date >= (CAST(:start AS timestamp) AT TIME ZONE :timezone)
            AND check_in_date < (CAST(:end AS timestamp) AT TIME ZONE :timezone)
        """
    async with revenue_session() as session:
        if month is not None:
            valid = await session.execute(text("SELECT 1 FROM pg_timezone_names WHERE name = :timezone"),
                                          {"timezone": property_timezone})
            if valid.first() is None:
                raise HTTPException(status_code=422, detail="Property timezone is invalid")
        result = await session.execute(text("""
            SELECT COALESCE(SUM(total_amount), 0) AS total_revenue,
                   COUNT(*) AS reservation_count, currency
            FROM reservations
            WHERE property_id = :property_id AND tenant_id = :tenant_id
        """ + date_filter + " GROUP BY currency"), params)
        rows = result.all()
        if len(rows) > 1 or (rows and not rows[0].currency):
            raise HTTPException(status_code=422, detail="Revenue requires a single known currency; conversion is not configured")
        row = rows[0] if rows else None
        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": str(row.total_revenue) if row else "0",
            "currency": row.currency if row else "USD",
            "count": row.reservation_count if row else 0,
        }
