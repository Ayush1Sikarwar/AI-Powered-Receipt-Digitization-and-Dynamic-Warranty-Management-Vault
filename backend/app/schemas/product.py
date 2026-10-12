"""Product Intelligence schemas (TSD §20)."""

from pydantic import BaseModel, Field


class ProductResolveRequest(BaseModel):
    """Ad-hoc product resolution input (TSD §20)."""

    manufacturer: str | None = None
    brand: str | None = None
    sku: str | None = None
    model_number: str | None = None
    description: str | None = None
    retailer: str | None = None

    model_config = {"extra": "ignore"}


class ProductMatchDecision(BaseModel):
    """User decision on a document's product match.

    action=confirm + product_id  -> confirm that candidate
    action=confirm (no product)  -> confirm the top candidate
    action=reject                -> no matching product
    action=create_confirm        -> create a catalog product from the
                                    receipt values and confirm it
    """

    action: str = Field(pattern="^(confirm|reject|create_confirm)$")
    product_id: int | None = None
