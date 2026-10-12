"""Import a product catalog JSON into the database (idempotent).

Usage:
    python -m scripts.import_catalog [--file data/catalog/sample_catalog.json]

This is the legitimate path for growing the catalog:
- the bundled sample file is clearly labeled "sample-fixture";
- real retailer/manufacturer exports can be imported the same way as long
  as they carry source attribution (never a fabricated "giant catalog").

Upsert keys: manufacturers/retailers by normalized name key, products by
SKU (or canonical name when SKU-less), aliases by (product, alias_text),
catalog entries by (retailer, catalog_sku).
"""

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.product.normalization import (  # noqa: E402
    make_name_key,
    normalize_manufacturer,
    normalize_retailer,
)
from app.db.session import SessionLocal  # noqa: E402
from app.models.product import (  # noqa: E402
    AliasType,
    Manufacturer,
    Product,
    ProductAlias,
    Retailer,
    RetailerCatalogEntry,
)


def import_catalog(path: Path) -> dict:
    data = json.loads(path.read_text())
    source = data.get("_meta", {}).get("source", "import")
    counts = {"manufacturers": 0, "retailers": 0, "products": 0, "aliases": 0, "catalog_entries": 0}

    db = SessionLocal()
    try:
        manufacturers: dict[str, int] = {}
        for m in data.get("manufacturers", []):
            name = normalize_manufacturer(m["name"]) or m["name"]
            key = make_name_key(name)
            mfr_row = db.scalar(select(Manufacturer).where(Manufacturer.name_key == key))
            if mfr_row is None:
                mfr_row = Manufacturer(name=name, name_key=key, country=m.get("country"))
                db.add(mfr_row)
                db.flush()
                counts["manufacturers"] += 1
            else:
                mfr_row.name = name
            manufacturers[m["name"]] = mfr_row.id

        retailers: dict[str, int] = {}
        for r in data.get("retailers", []):
            name = normalize_retailer(r["name"]) or r["name"]
            key = make_name_key(name)
            ret_row = db.scalar(select(Retailer).where(Retailer.name_key == key))
            if ret_row is None:
                ret_row = Retailer(name=name, name_key=key, country=r.get("country"))
                db.add(ret_row)
                db.flush()
                counts["retailers"] += 1
            else:
                ret_row.name = name
            retailers[r["name"]] = ret_row.id

        products: dict[str, int] = {}
        for p in data.get("products", []):
            key_sku = p.get("sku")
            if key_sku:

                prod_row = db.scalar(select(Product).where(Product.sku == key_sku))
            else:
                prod_row = db.scalar(select(Product).where(Product.canonical_name == p["canonical_name"]))
            if prod_row is None:
                prod_row = Product(canonical_name=p["canonical_name"])
                db.add(prod_row)
                db.flush()
                counts["products"] += 1
            mfr_name = p.get("manufacturer")
            if mfr_name and mfr_name in manufacturers:
                prod_row.manufacturer_id = manufacturers[mfr_name]
            prod_row.brand = p.get("brand") or prod_row.brand
            prod_row.category = p.get("category") or prod_row.category
            prod_row.model_number = p.get("model_number") or prod_row.model_number
            if key_sku:
                prod_row.sku = key_sku
            db.flush()
            products[p["canonical_name"]] = prod_row.id

            for a in p.get("aliases", []):
                alias_exists = db.scalar(
                    select(ProductAlias).where(
                        ProductAlias.product_id == prod_row.id,
                        ProductAlias.alias_text == a["text"],
                    )
                )
                if alias_exists is None:
                    db.add(
                        ProductAlias(
                            product_id=prod_row.id,
                            alias_text=a["text"],
                            alias_type=AliasType(a.get("type", "RECEIPT_TEXT")),
                            source=source,
                        )
                    )
                    counts["aliases"] += 1

        for e in data.get("catalog_entries", []):
            pid = products.get(e["product"])
            rid = retailers.get(e["retailer"])
            if pid is None or rid is None:
                print(f"  skip catalog entry (unknown product/retailer): {e}")
                continue
            if e.get("catalog_sku"):
                entry_exists = db.scalar(
                    select(RetailerCatalogEntry).where(
                        RetailerCatalogEntry.retailer_id == rid,
                        RetailerCatalogEntry.catalog_sku == e["catalog_sku"],
                    )
                )
            else:
                entry_exists = None
            if entry_exists is None:
                db.add(
                    RetailerCatalogEntry(
                        retailer_id=rid,
                        product_id=pid,
                        catalog_sku=e.get("catalog_sku"),
                        catalog_model=e.get("catalog_model"),
                        catalog_name=e.get("catalog_name"),
                        source=source,
                    )
                )
                counts["catalog_entries"] += 1
        db.commit()
    finally:
        db.close()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    default = Path(__file__).resolve().parents[1] / "data" / "catalog" / "sample_catalog.json"
    ap.add_argument("--file", default=str(default))
    args = ap.parse_args()
    counts = import_catalog(Path(args.file))
    print(f"imported from {args.file}: {counts}")


if __name__ == "__main__":
    main()
