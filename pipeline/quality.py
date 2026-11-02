"""Data-quality checks. Each check is (name, severity, passed, metric, detail).

Severity 'error' fails the run (exit code 1) when --strict is used; 'warn' and
'info' are reported only. Results go to output/data_quality_report.{json,md}.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd


def _chk(name, severity, passed, metric, detail):
    return {"check": name, "severity": severity, "passed": bool(passed), "metric": metric, "detail": detail}


def run_checks(silver: dict[str, pd.DataFrame], gold: dict[str, pd.DataFrame]) -> list[dict]:
    docs, meds, probs = silver["ccda_documents"], silver["ccda_medications"], silver["ccda_problems"]
    dx, dxl, rx = silver["claims_dx_lines"], silver["claims_dx_diagnoses"], silver["claims_rx"]
    out = []

    failed = (docs["parse_status"] != "ok").sum()
    out.append(_chk("ccda_parse_failures", "error", failed == 0, int(failed),
                    f"{failed} of {len(docs)} documents failed to parse (quarantined, run continues)"))

    no_sections = (~docs["has_medication_section"] & ~docs["has_problem_section"]).sum()
    out.append(_chk("ccda_docs_without_med_or_problem_section", "info", True, int(no_sections),
                    "documents with neither section (e.g. narrative-only notes)"))

    dup = docs.duplicated(["patient_id", "document_id"]).sum()
    out.append(_chk("ccda_duplicate_document_ids", "error", dup == 0, int(dup), "patient_id+document_id must be unique"))

    bd = docs[docs.parse_status == "ok"].groupby("patient_id")["birth_date"].nunique()
    conflicting = bd[bd > 1].index.tolist()
    out.append(_chk("patient_birth_date_consistent_across_documents", "warn", not conflicting, len(conflicting),
                    f"patients with >1 birth date across their C-CDAs: {conflicting}"))

    for label, df, null_col in (("medication", meds, "drug_name"), ("problem", probs, "problem_name")):
        ph = int(df["is_placeholder"].sum())
        out.append(_chk(f"ccda_{label}_placeholder_entries", "info", True, ph,
                        f"'No data available' / uncoded {label} entries excluded from gold"))
        nn = int(df[null_col].isna().sum())
        out.append(_chk(f"ccda_{label}_without_name", "warn", nn / max(len(df), 1) < 0.01, nn,
                        f"{label} entries with no resolvable name"))

    usable = meds[meds["is_usable"]]
    out.append(_chk("ccda_medication_rxnorm_coverage_document", "info", True,
                    round(100 * (usable["rxnorm_source"] == "document").mean(), 1),
                    "% usable medication entries with RxNorm in the document"))
    out.append(_chk("ccda_medication_rxnorm_coverage_after_vendor_crosswalk", "info", True,
                    round(100 * usable["rxnorm_code"].notna().mean(), 1),
                    "% usable medication entries with RxNorm after learning vendor-code (Multum/Medi-Span/FDB) pairs"))
    up = probs[probs["is_usable"]]
    out.append(_chk("ccda_problem_icd10_coverage_document", "info", True,
                    round(100 * (up["icd10_source"] == "document").mean(), 1),
                    "% usable problem entries with an ICD-10 translation in the document"))
    out.append(_chk("ccda_problem_icd10_coverage_after_crosswalk", "info", True,
                    round(100 * up["icd10_code"].notna().mean(), 1),
                    "% usable problem entries with ICD-10 after same-patient + cross-patient SNOMED->ICD-10 learning"))
    amb = int(gold["ref_snomed_icd10_learned"]["icd10_code"].isna().sum())
    out.append(_chk("snomed_icd10_ambiguous_mappings", "info", True, amb,
                    "SNOMED codes translated inconsistently across patients; left unmapped"))

    bad_ndc = meds["ndc_raw"].notna() & meds["ndc11"].isna()
    out.append(_chk("ccda_ndc_unnormalisable", "warn", True, int(bad_ndc.sum()),
                    "C-CDA NDC translations that are 10-digit/2-segment and therefore ambiguous; not joined"))

    pad = int(rx["ndc_was_padded"].sum())
    out.append(_chk("rx_ndc_leading_zeros_restored", "warn", True, pad,
                    "Rx claim NDCs shorter than 11 digits (leading zeros lost upstream, e.g. Excel); left-padded"))

    excel = dx["src_lob_raw"].str.match(r"^\d{1,2}-[A-Z][a-z]{2}$", na=False).mean()
    out.append(_chk("claims_srclob_excel_date_mangling", "warn", excel == 0, round(100 * excel, 1),
                    "% of srcLOB values that look like Excel-converted dates ('9-Jan'); "
                    "kept raw, use LOB instead"))

    k = dx.duplicated(["claim_id", "line_num"]).sum()
    out.append(_chk("dx_claim_line_key_unique", "error", k == 0, int(k), "ClaimID+LineNum is unique"))
    k = rx.duplicated(["claim_id"]).sum()
    out.append(_chk("rx_claim_id_unique", "error", k == 0, int(k), "Rx ClaimID is unique"))

    inv = int(dxl["icd10_code"].isna().sum())
    out.append(_chk("dx_invalid_icd10_codes", "warn", inv == 0, inv, "claim diagnosis codes failing ICD-10 shape check"))
    nodesc = dxl.dropna(subset=["icd10_code"])
    nodesc = int(nodesc["icd10_description"].isna().sum())
    out.append(_chk("dx_codes_without_icd10cm_description", "info", True, nodesc,
                    "claim diagnoses not found in the bundled ICD-10-CM release (non-billable/retired codes)"))

    for c in ("from_date", "paid_date"):
        n = int(dx[c].isna().sum())
        out.append(_chk(f"dx_{c}_parse", "error", n == 0, n, f"unparseable {c} values"))
    lag = (dx["paid_date"] < dx["from_date"]).sum()
    out.append(_chk("dx_paid_before_service", "warn", lag == 0, int(lag), "claim lines paid before service date"))
    neg = int((dx["paid"] < 0).sum() + (rx["paid"] < 0).sum())
    out.append(_chk("negative_paid_amounts", "info", True, neg, "reversals/adjustments present"))

    ccda_p, claim_p = set(docs["patient_id"]), set(dx["patient_id"]) | set(rx["patient_id"])
    out.append(_chk("patients_with_ccda_but_no_claims", "warn", not (ccda_p - claim_p), len(ccda_p - claim_p),
                    sorted(ccda_p - claim_p)))
    out.append(_chk("patients_with_claims_but_no_ccda", "warn", not (claim_p - ccda_p), len(claim_p - ccda_p),
                    sorted(claim_p - ccda_p)))

    rx_named = gold["medication_records"].query("source == 'RX_CLAIM'")["rxnorm_code"].notna().mean()
    out.append(_chk("rx_claims_resolved_to_rxnorm_via_learned_crosswalk", "info", True,
                    round(100 * rx_named, 1), "% of pharmacy fills whose NDC could be mapped to RxNorm "
                    "using only crosswalk pairs observed in the C-CDAs"))
    return out


def write_report(checks: list[dict], run_meta: dict, out_dir: str) -> None:
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "run": run_meta, "checks": checks}
    with open(f"{out_dir}/data_quality_report.json", "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    icon = {True: "PASS", False: "FAIL"}
    lines = ["# Data quality report", "", f"Generated {payload['generated_at']}", "",
             "## Row counts", "", "| table | rows |", "|---|---:|"]
    lines += [f"| {k} | {v:,} |" for k, v in run_meta.get("row_counts", {}).items()]
    lines += ["", "## Checks", "", "| result | severity | check | metric | detail |", "|---|---|---|---:|---|"]
    for c in checks:
        detail = c["detail"] if isinstance(c["detail"], str) else json.dumps(c["detail"])
        lines.append(f"| {icon[c['passed']]} | {c['severity']} | {c['check']} | {c['metric']} | {detail} |")
    with open(f"{out_dir}/data_quality_report.md", "w") as fh:
        fh.write("\n".join(lines) + "\n")
