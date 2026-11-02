"""Code normalisation shared by the clinical (C-CDA) and claims sides of the join."""
from __future__ import annotations

import re
from typing import Optional


def _missing(x) -> bool:
    """True for None / NaN / pd.NA / empty - pandas 2 and 3 use different missing sentinels."""
    if x is None:
        return True
    try:
        return bool(x != x) or (isinstance(x, str) and not x.strip())
    except (TypeError, ValueError):
        return False


def ndc11(raw) -> Optional[str]:
    """Normalise an NDC to the 11-digit 5-4-2 billing format.

    * Hyphenated (4-4-2, 5-3-2, 5-4-1, 5-4-2): pad each segment -> unambiguous.
    * 11 digits: already 5-4-2.
    * < 10 digits (no hyphens): leading zeros were dropped by a numeric
      cast/Excel (seen in the Rx claims file) -> left-pad to 11.
    * 10 digits without hyphens: ambiguous (could be 4-4-2, 5-3-2 or 5-4-1),
      returned as None so it is never mis-joined.
    """
    if _missing(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none"):
        return None
    if s.endswith(".0"):
        s = s[:-2]
    if "-" in s:
        parts = s.split("-")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            return None
        a, b, c = parts
        if len(a) > 5 or len(b) > 4 or len(c) > 2:
            return None
        return a.zfill(5) + b.zfill(4) + c.zfill(2)
    s = re.sub(r"\D", "", s)
    if len(s) == 11:
        return s
    if 0 < len(s) < 10:
        return s.zfill(11)
    return None


def ndc_product(ndc: Optional[str]) -> Optional[str]:
    """Labeler + product (first 9 of NDC11) - ignores package size."""
    return ndc[:9] if not _missing(ndc) and len(str(ndc)) == 11 else None


def icd10(raw) -> Optional[str]:
    if _missing(raw):
        return None
    s = re.sub(r"[^A-Z0-9]", "", str(raw).upper())
    if not s or s in ("NAN", "NONE") or not re.match(r"^[A-Z][0-9][0-9A-Z]", s):
        return None
    return s


def icd10_category(code: Optional[str]) -> Optional[str]:
    return None if _missing(code) else str(code)[:3]


def drug_name_key(name: Optional[str]) -> Optional[str]:
    if _missing(name):
        return None
    s = re.sub(r"[^a-z0-9.% ]+", " ", name.lower())
    return re.sub(r"\s+", " ", s).strip() or None


_ICD_DESC_CACHE: dict = {}


def icd10_description(code: Optional[str]) -> Optional[str]:
    """Offline ICD-10-CM description via `simple_icd_10_cm` if installed."""
    if _missing(code):
        return None
    if code in _ICD_DESC_CACHE:
        return _ICD_DESC_CACHE[code]
    desc = None
    try:
        import simple_icd_10_cm as cm  # optional dependency
        if cm.is_valid_item(code):
            desc = cm.get_description(code)
    except Exception:
        desc = None
    _ICD_DESC_CACHE[code] = desc
    return desc
