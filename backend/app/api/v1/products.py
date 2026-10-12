"""Product API (TSD §20): search, detail, and ad-hoc resolution.

The catalog is shared reference data (readable by any authenticated user).
Product *decisions* for a document live on the documents router and are
strictly user-scoped.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api import envelope
from app.api.deps import current_user, get_db
from app.models.user import User
from app.schemas.product import ProductResolveRequest
from app.services import product_service

router = APIRouter()


@router.get("/search")
def search_products(
    request: Request,
    q: str | None = Query(default=None, max_length=120),
    retailer: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    items = product_service.search_products(db, q=q, retailer=retailer, limit=limit)
    return envelope.ok({"items": items, "total": len(items)}, request)


@router.post("/resolve")
def resolve_product(
    request: Request,
    body: ProductResolveRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    result = product_service.resolve_adhoc(db, body.model_dump())
    return envelope.ok(result, request)


@router.get("/{product_id}")
def get_product(
    request: Request,
    product_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    product = product_service.get_product(db, product_id)
    return envelope.ok(product, request)
