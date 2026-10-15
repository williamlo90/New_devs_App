from decimal import Decimal, ROUND_HALF_UP, ROUND_HALF_DOWN
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Dict, Any
from app.services.cache import get_revenue_summary
from app.core.auth import authenticate_request as get_current_user
from app.models.auth import AuthenticatedUser
from app.services.reservations import list_tenant_properties

router = APIRouter()


def require_tenant(current_user: AuthenticatedUser) -> str:
    if not current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant membership required")
    return current_user.tenant_id


@router.get("/dashboard/properties")
async def get_dashboard_properties(current_user: AuthenticatedUser = Depends(get_current_user)):
    return await list_tenant_properties(require_tenant(current_user))


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    month: int = Query(None, ge=1, le=12),
    year: int = Query(None, ge=1, le=9998)
) -> Dict[str, Any]:
    
    tenant_id = require_tenant(current_user)
    
    if (month is None) != (year is None):
        raise HTTPException(status_code=422, detail="Provide both month and year")
    revenue_data = await get_revenue_summary(property_id, tenant_id, month, year)
    
    total = Decimal(revenue_data['total'])
    # Preserve the dashboard's two-decimal, ties-toward-positive-infinity display
    # rule (Math.round), without binary floating point or per-reservation rounding.
    display = total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP if total >= 0 else ROUND_HALF_DOWN)
    if display == 0:
        display = abs(display)
    
    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": str(total),
        "display_total_revenue": format(display, ".2f"),
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count']
    }
