from fastapi import APIRouter

from app.api.v1 import (
    account,
    admin,
    auth,
    claims,
    dashboard,
    documents,
    notifications,
    products,
    purchases,
    system,
    warranty,
)

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(products.router, prefix="/products", tags=["products"])
api_router.include_router(purchases.router, prefix="/purchases", tags=["purchases"])
api_router.include_router(claims.router, prefix="/claims", tags=["claims"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_router.include_router(account.router, tags=["account"])
api_router.include_router(dashboard.router, tags=["dashboard"])
api_router.include_router(system.router, tags=["system"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(warranty.router, tags=["warranty"])
