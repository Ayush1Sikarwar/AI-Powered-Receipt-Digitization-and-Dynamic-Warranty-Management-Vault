"""Import warranty sources + policies JSON into the database (idempotent).

Usage:
    python -m scripts.import_warranties [--file data/warranty/warranty_policies.json]

Official-source entries (Samsung/LG) carry real URLs, retrieval dates and
trust levels (SRS FR-12). Sample-fixture entries for fictitious dev-catalog
brands are clearly labeled in the source title.

Upsert keys: manufacturers by name key, sources by (manufacturer, title,
url), policies by (manufacturer, policy_name, policy_version, region),
coverages by (policy, component).
"""

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.product.normalization import (  # noqa: E402
    make_name_key,
    normalize_manufacturer,
)
from app.db.session import SessionLocal  # noqa: E402
from app.models.product import Manufacturer  # noqa: E402
from app.models.warranty import (  # noqa: E402
    SourceType,
    WarrantySource,
)


def import_warranties(path: Path) -> dict:
    data = json.loads(path.read_text())
    counts = {"manufacturers": 0, "sources": 0, "policies": 0, "coverages": 0}

    db = SessionLocal()
    try:
        manufacturers: dict[str, int] = {}
        sources: dict[int, int] = {}

        # --- manufacturers (create if missing) ----------------------------
        for s in data.get("sources", []):
            mfr_name = s["manufacturer"]
            if mfr_name in manufacturers:
                continue
            key = make_name_key(normalize_manufacturer(mfr_name) or mfr_name)
            mfr_row = db.scalar(select(Manufacturer).where(Manufacturer.name_key == key))
            if mfr_row is None:
                mfr_row = Manufacturer(name=normalize_manufacturer(mfr_name) or mfr_name, name_key=key)
                db.add(mfr_row)
                db.flush()
                counts["manufacturers"] += 1
            manufacturers[mfr_name] = mfr_row.id

        # --- sources -------------------------------------------------------
        for idx, s in enumerate(data.get("sources", [])):
            mfr_id = manufacturers[s["manufacturer"]]
            src_stmt = select(WarrantySource).where(WarrantySource.source_title == s["source_title"])
            if s.get("source_url"):
                src_stmt = src_stmt.where(WarrantySource.source_url == s["source_url"])
            src_row = db.scalar(src_stmt)
            if src_row is None:
                src_row = WarrantySource(
                    manufacturer_id=mfr_id,
                    source_type=SourceType(s.get("source_type", "OTHER")),
                    source_url=s.get("source_url"),
                    source_title=s["source_title"],
                    source_version=s.get("source_version"),
                    region=s.get("region"),
                    trust_level=int(s.get("trust_level", 3)),
                    retrieved_at=datetime.now(UTC),
                    document_hash=s.get("document_hash"),
                )
                db.add(src_row)
                db.flush()
                counts["sources"] += 1
            else:
                # Idempotent re-import: refresh provenance fields (incl.
                # content hash) but never reset retrieval status.
                for field in ("source_version", "region", "document_hash"):
                    if s.get(field) is not None:
                        setattr(src_row, field, s[field])
                if s.get("trust_level") is not None:
                    src_row.trust_level = int(s["trust_level"])
            sources[idx] = src_row.id

        # --- policies + coverages (versioned upsert, history preserved) ----
        from app.warranty.versioning import upsert_policy_versioned

        for p in data.get("policies", []):
            mfr_id = manufacturers[p["manufacturer"]]
            out = upsert_policy_versioned(
                db,
                manufacturer_id=mfr_id,
                policy_name=p["policy_name"],
                region=p.get("region"),
                policy_version=p.get("policy_version", "1.0"),
                effective_from=date.fromisoformat(p["effective_from"]),
                effective_to=date.fromisoformat(p["effective_to"]) if p.get("effective_to") else None,
                product_category=p.get("product_category"),
                product_model_pattern=p.get("product_model_pattern"),
                source_id=sources.get(p.get("source_index", 0)),
                coverages=p.get("coverages") or [],
            )
            if out["created"]:
                counts["policies"] += 1
            counts["coverages"] += len(p.get("coverages") or [])
        db.flush()
        db.commit()
    finally:
        db.close()
    return counts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(Path(__file__).resolve().parents[1] / "data/warranty/warranty_policies.json"))
    args = ap.parse_args()
    result = import_warranties(Path(args.file))
    print(json.dumps(result, indent=2))
