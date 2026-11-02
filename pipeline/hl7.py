"""Low-level HL7 v3 / C-CDA helpers: namespaces, code systems, timestamps,
coded elements and narrative-reference resolution.

Everything here is vendor-agnostic. Real-world C-CDAs (Cerner, Epic, athena,
eClinicalWorks, Greenway, Rosetta, ...) disagree on *where* they put the useful
value (code/@displayName vs. <name> vs. <originalText> vs. a #reference into the
section narrative), so the helpers below always try every legal location and
record which one was used.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from lxml import etree

NS = {
    "h": "urn:hl7-org:v3",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "sdtc": "urn:hl7-org:sdtc",
}
XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

# OID -> short, stable code-system name used throughout the pipeline.
CODE_SYSTEMS = {
    "2.16.840.1.113883.6.96": "SNOMED",
    "2.16.840.1.113883.6.90": "ICD10CM",
    "2.16.840.1.113883.6.3": "ICD10",
    "2.16.840.1.113883.6.103": "ICD9CM",
    "2.16.840.1.113883.6.104": "ICD9CM_PROC",
    "2.16.840.1.113883.6.4": "ICD10PCS",
    "2.16.840.1.113883.6.88": "RXNORM",
    "2.16.840.1.113883.6.69": "NDC",
    "2.16.840.1.113883.6.1": "LOINC",
    "2.16.840.1.113883.6.12": "CPT",
    "2.16.840.1.113883.6.285": "HCPCS",
    "2.16.840.1.113883.6.314": "MULTUM_DRUG_ID",
    "2.16.840.1.113883.6.312": "MULTUM_DRUG_SYNONYM_ID",
    "2.16.840.1.113883.6.253": "MEDISPAN_DDID",
    "2.16.840.1.113883.6.162": "MEDISPAN_MEDFILE",
    "2.16.840.1.113883.6.68": "MEDISPAN_GPI",
    "2.16.840.1.113883.6.208": "FDB_NDDF",
    "2.16.840.1.113883.3.247.1.1": "IMO",
    "2.16.840.1.113883.6.338": "HEALTH_LANGUAGE",
    "2.16.840.1.113883.6.26": "MEDCIN",
    "2.16.840.1.113883.5.4": "HL7_ACTCODE",
    "2.16.840.1.113883.3.26.1.1": "NCI_THESAURUS",
}

ICD10_SYSTEMS = {"ICD10CM", "ICD10"}

# SNOMED codes used for problem status / problem type when displayName is absent
SNOMED_DISPLAY = {
    "55561003": "Active",
    "73425007": "Inactive",
    "413322009": "Resolved",
    "246455001": "Recurrence",
    "277022003": "Remission",
    "55607006": "Problem",
    "404684003": "Finding",
    "282291009": "Diagnosis",
    "64572001": "Disease",
    "418799008": "Symptom",
    "409586006": "Complaint",
    "248536006": "Functional limitation",
}

_WS = re.compile(r"\s+")
_TZ = re.compile(r"[+-]\d{4}$")


def clean_text(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    s = _WS.sub(" ", s).strip()
    return s or None


def code_system_name(oid: Optional[str], name: Optional[str] = None) -> Optional[str]:
    """Map an OID to the short name; fall back to codeSystemName, then the raw OID."""
    if oid and oid in CODE_SYSTEMS:
        return CODE_SYSTEMS[oid]
    if name:
        return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return oid


# --------------------------------------------------------------------------- #
# Timestamps
# --------------------------------------------------------------------------- #
def parse_ts(value: Optional[str]) -> Optional[date]:
    """Parse an HL7 TS (YYYY[MM[DD[HH[MM[SS]]]]][+/-ZZZZ]) to a date.

    Also tolerates ISO-ish strings some vendors emit ("2022-03-21 08:26:00").
    Partial precision is filled with the first month/day. Invalid -> None.
    """
    if not value:
        return None
    v = _TZ.sub("", value.strip())
    digits = re.sub(r"\D", "", v.split(".")[0])
    if len(digits) < 4:
        return None
    try:
        y = int(digits[0:4])
        m = int(digits[4:6]) if len(digits) >= 6 else 1
        d = int(digits[6:8]) if len(digits) >= 8 else 1
        if not 1800 <= y <= 2100:
            return None
        return date(y, m, d)
    except ValueError:
        return None


def ts_precision(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    n = len(re.sub(r"\D", "", _TZ.sub("", value.strip()).split(".")[0]))
    return {4: "year", 6: "month", 8: "day"}.get(n, "time" if n > 8 else None)


def effective_interval(el) -> dict:
    """Return low/high/value timestamps from the IVL_TS (or untyped) effectiveTime."""
    out = {"low": None, "high": None}
    if el is None:
        return out
    for et in el.findall("h:effectiveTime", NS):
        t = et.get(XSI_TYPE) or ""
        if t.endswith("PIVL_TS") or t.endswith("EIVL_TS") or t.endswith("SXPR_TS"):
            continue
        low, high = et.find("h:low", NS), et.find("h:high", NS)
        if et.get("value"):
            out["low"] = out["low"] or et.get("value")
        if low is not None and low.get("value"):
            out["low"] = low.get("value")
        if high is not None and high.get("value"):
            out["high"] = high.get("value")
        break
    return out


def frequency(el) -> Optional[str]:
    """Human-readable dosing frequency from a PIVL_TS effectiveTime, e.g. '12 h'."""
    for et in el.findall("h:effectiveTime", NS):
        if (et.get(XSI_TYPE) or "").endswith("PIVL_TS"):
            p = et.find("h:period", NS)
            if p is not None and p.get("value"):
                return f"every {p.get('value')} {p.get('unit') or ''}".strip()
    return None


# --------------------------------------------------------------------------- #
# Narrative references
# --------------------------------------------------------------------------- #
def build_reference_index(root) -> dict:
    """Map every narrative @ID to its flattened text (for <reference value='#id'>)."""
    idx = {}
    for e in root.iter():
        if not isinstance(e.tag, str):
            continue
        _id = e.get("ID")
        if _id:
            idx[_id] = clean_text(" ".join(e.itertext()))
    return idx


def resolve_text(el, ref_index: dict) -> Optional[str]:
    """Text of a <text> / <originalText> element: inline text first, else #reference."""
    if el is None:
        return None
    inline = clean_text(
        " ".join(t for t in el.itertext() if t is not None)
    )
    if inline:
        return inline
    ref = el.find("h:reference", NS)
    if ref is not None and ref.get("value"):
        return ref_index.get(ref.get("value").lstrip("#"))
    return None


def ii(el) -> Optional[str]:
    """Instance identifier 'root^extension' (or just root)."""
    if el is None or el.get("nullFlavor") and not el.get("root"):
        return None
    root, ext = el.get("root"), el.get("extension")
    if root and ext:
        return f"{root}^{ext}"
    return root or ext


# --------------------------------------------------------------------------- #
# Coded elements
# --------------------------------------------------------------------------- #
def coded(el, ref_index: dict) -> dict:
    """Flatten a CD/CE element (with translations) into a dict."""
    out = {
        "code": None, "system": None, "display": None,
        "original_text": None, "null_flavor": None, "translations": [],
    }
    if el is None:
        return out
    out["null_flavor"] = el.get("nullFlavor")
    if el.get("code") and el.get("code") not in ("UNK", "NI", "OTH"):
        out["code"] = el.get("code").strip()
        out["system"] = code_system_name(el.get("codeSystem"), el.get("codeSystemName"))
    out["display"] = clean_text(el.get("displayName"))
    out["original_text"] = resolve_text(el.find("h:originalText", NS), ref_index)
    for tr in el.findall("h:translation", NS):
        code = tr.get("code")
        if not code or code in ("UNK", "NI", "OTH") or tr.get("nullFlavor"):
            continue
        out["translations"].append({
            "code": code.strip(),
            "system": code_system_name(tr.get("codeSystem"), tr.get("codeSystemName")),
            "display": clean_text(tr.get("displayName")),
        })
    return out


def pick(c: dict, systems) -> tuple[Optional[str], Optional[str]]:
    """First (code, display) whose system is in `systems`: primary code, then translations."""
    systems = {systems} if isinstance(systems, str) else set(systems)
    if c["code"] and c["system"] in systems:
        return c["code"], c["display"]
    for t in c["translations"]:
        if t["system"] in systems:
            return t["code"], t["display"]
    return None, None


def has_template(el, *roots: str) -> bool:
    return any(t.get("root") in roots for t in el.findall("h:templateId", NS))


def parse_xml(path: str):
    parser = etree.XMLParser(huge_tree=True, resolve_entities=False, no_network=True,
                             remove_comments=True)
    return etree.parse(path, parser)
