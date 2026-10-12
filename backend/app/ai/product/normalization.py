"""Value normalization for product matching (SRS ML-04, TSD §10-12).

All normalizers are pure functions — they never invent content. Matching
compares normalized forms on both sides, so catalog entries and receipt
text with different casing, separators, or known abbreviations can be
matched without mutating either stored value.
"""

from __future__ import annotations

import re
import unicodedata

# --- retailers ---------------------------------------------------------------
# Retailer abbreviations known *in this deployment* (the retail formats used
# by this system's documents/evaluation). Keyed by normalized short text.
# Adding real retailers is done through the catalog import (with source
# attribution), not by fabricating entries here.
RETAILER_ABBREVIATIONS: dict[str, str] = {
    "nortech": "Nortech Home Appliances",
    "nortechhomeappliances": "Nortech Home Appliances",
    "citymart": "CityMart Grocers",
    "citymartgrocers": "CityMart Grocers",
    "bluebox": "BlueBox Electronics",
    "blueboxelectronics": "BlueBox Electronics",
}

# --- manufacturers -----------------------------------------------------------
# Unambiguous, widely-known manufacturer abbreviations / variant spellings,
# used as a *constraint* during matching (they never create a product).
MANUFACTURER_ALIASES: dict[str, str] = {
    "sam": "Samsung",
    "samsung": "Samsung",
    "samsung electronics": "Samsung",
    "samsung corp": "Samsung",
    "lge": "LG",
    "lg": "LG",
    "lg electronics": "LG",
    "sony": "Sony",
    "sony corp": "Sony",
    "bosch": "Bosch",
    "bosch home appliances": "Bosch",
    "daewoo": "Daewoo",
    "haier": "Haier",
    "whirlpool": "Whirlpool",
    "panasonic": "Panasonic",
    "philips": "Philips",
    "tcl": "TCL",
    "vizio": "Vizio",
    "hisense": "Hisense",
    "sharp": "Sharp",
    "toshiba": "Toshiba",
    "asus": "ASUS",
    "msi": "MSI",
    "dell": "Dell",
    "lenovo": "Lenovo",
    "acer": "Acer",
    "hp": "HP",
    "hewlett packard": "HP",
}


def normalize_text(s: str | None) -> str:
    """Casefold, de-accent, and collapse to lowercase alphanumerics + single
    spaces. For display-agnostic text comparison (names, descriptions)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_sku(s: str | None) -> str:
    """SKU form: uppercase, separators stripped. 'BX-K870' == 'bxxk870'? No —
    'BX-K870' -> 'BXK870' on both sides; comparison is normalized-vs-
    normalized so this is consistent and reversible for display only."""
    if not s:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


def normalize_model(s: str | None) -> str:
    """Model form: uppercase, separators/whitespace stripped (model numbers
    are printed inconsistently: 'QT55Q90RA', 'qt-55q90ra', 'QT 55 Q90 RA')."""
    if not s:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()


def normalize_retailer(s: str | None) -> str | None:
    """Resolve a receipt retailer string (possibly an abbreviation) to the
    canonical retailer name known in this deployment, else None (unknown)."""
    if not s:
        return None
    key = normalize_text(s).replace(" ", "")
    if key in RETAILER_ABBREVIATIONS:
        return RETAILER_ABBREVIATIONS[key]
    # Also try the space-collapsed full-name key.
    key_space = normalize_text(s)
    for v in RETAILER_ABBREVIATIONS.values():
        if key_space == normalize_text(v):
            return v
    return None


def normalize_manufacturer(s: str | None) -> str | None:
    """Resolve a manufacturer spelling/abbreviation to canonical form, else
    None. Unknown manufacturers are returned as their title-cased form
    (still usable as a soft constraint via text comparison)."""
    if not s:
        return None
    key = normalize_text(s)
    if key in MANUFACTURER_ALIASES:
        return MANUFACTURER_ALIASES[key]
    # Title-case fallback for unknown but real brand strings.
    return re.sub(r"\s+", " ", s.strip()).title() or None


def make_name_key(name: str) -> str:
    """Dedup key for manufacturer/retailer identity (TSD §11: different
    spellings must not create duplicates)."""
    return normalize_text(name).replace(" ", "")
