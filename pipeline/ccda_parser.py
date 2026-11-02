"""Parse one C-CDA document into Document, Medication and Problem records.

Sections are located by templateId *or* LOINC section code (vendors are
inconsistent about which they send, and about templateId versions):

  Medications             2.16.840.1.113883.10.20.22.2.1(.1)   LOINC 10160-0
  Medications Administered 2.16.840.1.113883.10.20.22.2.38      LOINC 29549-3
  Problems                2.16.840.1.113883.10.20.22.2.5(.1)   LOINC 11450-4

Every output row carries patient_id + document_id + entry_index for lineage
back to the exact source XML entry.
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

from . import hl7
from .hl7 import NS
from .normalize import icd10, ndc11

MED_SECTIONS = {"2.16.840.1.113883.10.20.22.2.1", "2.16.840.1.113883.10.20.22.2.1.1"}
MED_ADMIN_SECTIONS = {"2.16.840.1.113883.10.20.22.2.38"}
PROBLEM_SECTIONS = {"2.16.840.1.113883.10.20.22.2.5", "2.16.840.1.113883.10.20.22.2.5.1"}
SECTION_LOINC = {"10160-0": "medications", "29549-3": "medications_administered",
                 "11450-4": "problems"}

# Free-text "nothing here" rows some EHRs emit as entries instead of a nullFlavor section
PLACEHOLDER_PATTERNS = re.compile(
    r"^(no data available|none recorded|no known|no active|none reported|not on file|unknown)\b",
    re.IGNORECASE)

PROBLEM_OBS_TEMPLATE = "2.16.840.1.113883.10.20.22.4.4"
PROBLEM_STATUS_TEMPLATE = "2.16.840.1.113883.10.20.22.4.6"
MED_INSTRUCTIONS_TEMPLATE = "2.16.840.1.113883.10.20.22.4.147"


def ids_from_filename(path: str) -> tuple[str, str]:
    """'<patient>_<document>_masked.xml' -> (patient_id, document_id)."""
    stem = os.path.basename(path)
    for suffix in (".xml", ".XML"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    if stem.endswith("_masked"):
        stem = stem[: -len("_masked")]
    patient_id, _, document_id = stem.partition("_")
    return patient_id, document_id or stem


def classify_section(section) -> Optional[str]:
    roots = {t.get("root") for t in section.findall("h:templateId", NS)}
    if roots & MED_SECTIONS:
        return "medications"
    if roots & MED_ADMIN_SECTIONS:
        return "medications_administered"
    if roots & PROBLEM_SECTIONS:
        return "problems"
    code = section.find("h:code", NS)
    return SECTION_LOINC.get(code.get("code")) if code is not None else None


def _first_author_time(el) -> Optional[str]:
    t = el.find("h:author/h:time", NS)
    return t.get("value") if t is not None else None


def _status(el) -> Optional[str]:
    s = el.find("h:statusCode", NS)
    return s.get("code").lower() if s is not None and s.get("code") else None


# --------------------------------------------------------------------------- #
# Medications
# --------------------------------------------------------------------------- #
def parse_medication(sa, ctx: dict, refs: dict, section_type: str, idx: int) -> dict:
    eff = hl7.effective_interval(sa)
    material = sa.find("h:consumable/h:manufacturedProduct/h:manufacturedMaterial", NS)
    code = hl7.coded(material.find("h:code", NS) if material is not None else None, refs)
    rx_code, rx_display = hl7.pick(code, "RXNORM")
    ndc_raw, _ = hl7.pick(code, "NDC")
    material_name = hl7.clean_text(material.findtext("h:name", namespaces=NS)) \
        if material is not None else None
    entry_text = hl7.resolve_text(sa.find("h:text", NS), refs)

    sig = None
    for er in sa.findall("h:entryRelationship/h:substanceAdministration", NS):
        if hl7.has_template(er, MED_INSTRUCTIONS_TEMPLATE):
            sig = hl7.resolve_text(er.find("h:text", NS), refs)

    # best available human-readable name, and where it came from
    name, name_source = None, None
    for cand, src in ((rx_display, "rxnorm_display"), (code["display"], "code_display"),
                      (material_name, "material_name"), (code["original_text"], "original_text"),
                      (entry_text, "entry_text")):
        if cand:
            name, name_source = cand, src
            break

    route = sa.find("h:routeCode", NS)
    route_c = hl7.coded(route, refs)
    dose = sa.find("h:doseQuantity", NS)

    return {
        **ctx,
        "section_type": section_type,
        "entry_index": idx,
        "entry_id": hl7.ii(sa.find("h:id", NS)),
        "mood_code": sa.get("moodCode"),
        "negated": sa.get("negationInd") == "true",
        "status_code": _status(sa),
        "start_date": hl7.parse_ts(eff["low"]),
        "end_date": hl7.parse_ts(eff["high"]),
        "start_raw": eff["low"],
        "end_raw": eff["high"],
        "frequency": hl7.frequency(sa),
        "dose_value": dose.get("value") if dose is not None else None,
        "dose_unit": dose.get("unit") if dose is not None else None,
        "route": route_c["display"] or route_c["original_text"] or route_c["code"],
        "drug_code": code["code"],
        "drug_code_system": code["system"],
        "drug_code_null_flavor": code["null_flavor"],
        "rxnorm_code": rx_code,
        "ndc_raw": ndc_raw,
        "ndc11": ndc11(ndc_raw),
        "drug_name": name,
        "drug_name_source": name_source,
        "is_placeholder": bool(name and PLACEHOLDER_PATTERNS.match(name)) or (
            name is None and rx_code is None and ndc_raw is None),
        "drug_original_text": code["original_text"],
        "material_name": material_name,
        "sig_text": sig,
        "entry_text": entry_text,
        "author_time": hl7.parse_ts(_first_author_time(sa)),
        "translations_json": json.dumps(code["translations"]) if code["translations"] else None,
    }


# --------------------------------------------------------------------------- #
# Problems
# --------------------------------------------------------------------------- #
def _problem_status(obs, refs) -> Optional[str]:
    for so in obs.findall("h:entryRelationship/h:observation", NS):
        c = so.find("h:code", NS)
        if hl7.has_template(so, PROBLEM_STATUS_TEMPLATE) or (c is not None and c.get("code") == "33999-4"):
            v = hl7.coded(so.find("h:value", NS), refs)
            return v["display"] or hl7.SNOMED_DISPLAY.get(v["code"] or "") or v["code"]
    return None


def parse_problem(obs, act, ctx: dict, refs: dict, idx: int) -> dict:
    act_eff = hl7.effective_interval(act) if act is not None else {"low": None, "high": None}
    eff = hl7.effective_interval(obs)
    ptype = hl7.coded(obs.find("h:code", NS), refs)
    value = hl7.coded(obs.find("h:value", NS), refs)
    snomed, snomed_display = hl7.pick(value, "SNOMED")
    icd_code, icd_display = hl7.pick(value, hl7.ICD10_SYSTEMS)
    icd9_code, _ = hl7.pick(value, "ICD9CM")
    obs_text = hl7.resolve_text(obs.find("h:text", NS), refs)

    name, name_source = None, None
    for cand, src in ((value["display"], "code_display"), (value["original_text"], "original_text"),
                      (icd_display, "icd10_display"), (obs_text, "entry_text"),
                      (next((t["display"] for t in value["translations"] if t["display"]), None),
                       "translation_display")):
        if cand:
            name, name_source = cand, src
            break

    return {
        **ctx,
        "entry_index": idx,
        "concern_id": hl7.ii(act.find("h:id", NS)) if act is not None else None,
        "concern_status": _status(act) if act is not None else None,
        "concern_start_date": hl7.parse_ts(act_eff["low"]),
        "concern_end_date": hl7.parse_ts(act_eff["high"]),
        "observation_id": hl7.ii(obs.find("h:id", NS)),
        "problem_type_code": ptype["code"],
        "problem_type": ptype["display"] or hl7.SNOMED_DISPLAY.get(ptype["code"] or ""),
        "negated": obs.get("negationInd") == "true",
        "onset_date": hl7.parse_ts(eff["low"]),
        "abatement_date": hl7.parse_ts(eff["high"]),
        "onset_raw": eff["low"],
        "problem_code": value["code"],
        "problem_code_system": value["system"],
        "problem_code_null_flavor": value["null_flavor"],
        "snomed_code": snomed,
        "icd10_code": icd10(icd_code),
        "icd10_code_raw": icd_code,
        "icd9_code": icd9_code,
        "problem_name": name,
        "problem_name_source": name_source,
        "is_placeholder": bool(name and PLACEHOLDER_PATTERNS.match(name)) or (
            name is None and value["code"] is None),
        "problem_original_text": value["original_text"],
        "problem_status": _problem_status(obs, refs),
        "author_time": hl7.parse_ts(_first_author_time(obs) or (
            _first_author_time(act) if act is not None else None)),
        "translations_json": json.dumps(value["translations"]) if value["translations"] else None,
    }


def _iter_problem_observations(section):
    """Yield (concern_act, observation) pairs; tolerate observations placed directly in <entry>."""
    for entry in section.findall("h:entry", NS):
        for act in entry.findall("h:act", NS):
            for obs in act.findall("h:entryRelationship/h:observation", NS):
                if hl7.has_template(obs, PROBLEM_OBS_TEMPLATE) or obs.find("h:value", NS) is not None:
                    yield act, obs
        for obs in entry.findall("h:observation", NS):
            yield None, obs


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #
def parse_document(path: str) -> dict:
    """Parse a single C-CDA. Never raises: failures come back as a document row with an error."""
    patient_id, document_id = ids_from_filename(path)
    doc = {
        "patient_id": patient_id, "document_id": document_id,
        "file_name": os.path.basename(path), "file_size_bytes": os.path.getsize(path),
        "parse_status": "ok", "parse_error": None,
    }
    result = {"document": doc, "medications": [], "problems": []}
    try:
        tree = hl7.parse_xml(path)
    except Exception as exc:  # malformed XML -> quarantine, keep going
        doc.update(parse_status="failed", parse_error=f"{type(exc).__name__}: {exc}"[:500])
        return result

    root = tree.getroot()
    refs = hl7.build_reference_index(root)
    code = hl7.coded(root.find("h:code", NS), refs)
    pat = root.find("h:recordTarget/h:patientRole/h:patient", NS)
    eff = root.find("h:effectiveTime", NS)
    doc.update({
        "clinical_document_id": hl7.ii(root.find("h:id", NS)),
        "document_type_code": code["code"],
        "document_type": code["display"],
        "title": hl7.clean_text(root.findtext("h:title", namespaces=NS)),
        "document_date": hl7.parse_ts(eff.get("value") if eff is not None else None),
        "set_id": hl7.ii(root.find("h:setId", NS)),
        "version_number": (root.find("h:versionNumber", NS).get("value")
                           if root.find("h:versionNumber", NS) is not None else None),
        "source_system": hl7.clean_text(root.findtext(".//h:author//h:softwareName", namespaces=NS)),
        "custodian_org": hl7.clean_text(root.findtext(
            "h:custodian/h:assignedCustodian/h:representedCustodianOrganization/h:name",
            namespaces=NS)),
        "gender": (pat.find("h:administrativeGenderCode", NS).get("code")
                   if pat is not None and pat.find("h:administrativeGenderCode", NS) is not None else None),
        "birth_date": hl7.parse_ts(pat.find("h:birthTime", NS).get("value"))
        if pat is not None and pat.find("h:birthTime", NS) is not None else None,
        "patient_state": hl7.clean_text(root.findtext("h:recordTarget/h:patientRole/h:addr/h:state",
                                                      namespaces=NS)),
    })
    ctx = {"patient_id": patient_id, "document_id": document_id, "document_date": doc["document_date"]}

    sections_seen = {"medications": 0, "medications_administered": 0, "problems": 0}
    null_sections = []
    med_i = prob_i = 0
    for section in root.iter("{urn:hl7-org:v3}section"):
        kind = classify_section(section)
        if not kind:
            continue
        sections_seen[kind] += 1
        if section.get("nullFlavor"):
            null_sections.append(kind)
        if kind in ("medications", "medications_administered"):
            for sa in section.findall("h:entry/h:substanceAdministration", NS):
                result["medications"].append(parse_medication(sa, ctx, refs, kind, med_i))
                med_i += 1
        else:
            for act, obs in _iter_problem_observations(section):
                result["problems"].append(parse_problem(obs, act, ctx, refs, prob_i))
                prob_i += 1

    doc.update({
        "has_medication_section": sections_seen["medications"] > 0,
        "has_problem_section": sections_seen["problems"] > 0,
        "null_flavored_sections": ",".join(sorted(set(null_sections))) or None,
        "medication_entry_count": med_i,
        "problem_entry_count": prob_i,
    })
    return result
