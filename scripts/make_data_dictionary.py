"""Regenerate docs/DATA_DICTIONARY.md from the Parquet output's actual schemas."""
import os
import sys

import pyarrow.parquet as pq

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TABLES = {
    "silver/ccda_documents": ("One row per C-CDA file", "patient_id, document_id"),
    "silver/ccda_medications": ("One row per medication entry (substanceAdministration) per document", "document_id, entry_index"),
    "silver/ccda_problems": ("One row per problem observation per document", "document_id, entry_index"),
    "silver/claims_dx_lines": ("One row per medical claim line", "claim_id, line_num"),
    "silver/claims_dx_diagnoses": ("One row per claim line x diagnosis position (ICDDiag1..30 unpivoted)", "claim_line_key, diag_position"),
    "silver/claims_rx": ("One row per pharmacy fill", "claim_id"),
    "gold/patient_summary": ("One row per patient", "patient_id"),
    "gold/patient_medications": ("Distinct medication per patient, deduplicated across documents", "patient_id, med_key"),
    "gold/patient_problems": ("Distinct problem per patient, deduplicated across documents", "patient_id, problem_key"),
    "gold/medication_records": ("C-CDA medication entries UNION ALL pharmacy fills", "source, source_record_id"),
    "gold/condition_records": ("C-CDA problem entries UNION ALL claim diagnoses", "source, source_record_id (+ diag_position for claims)"),
    "gold/condition_concordance": ("Patient x ICD-10 code across both sources", "patient_id, icd10_code"),
    "gold/medication_concordance": ("Patient x drug across both sources", "patient_id, drug_key"),
    "gold/ref_ndc_rxnorm_learned": ("NDC11 -> RxNorm pairs observed in C-CDA translations", "ndc11"),
    "gold/ref_snomed_icd10_learned": ("SNOMED -> most frequent ICD-10 observed in C-CDA translations", "snomed_code"),
}

NOTES = {
    "entry_index": "0-based position of the entry within the document (lineage)",
    "is_placeholder": "'No data available…' style or completely uncoded/unnamed entry",
    "is_usable": "not placeholder, not negated, has an identity key; only these reach gold",
    "drug_name_source": "where drug_name came from: rxnorm_display | code_display | material_name | original_text | entry_text",
    "problem_name_source": "where problem_name came from",
    "translations_json": "all <translation> codings: [{code, system, display}]",
    "ndc11": "NDC normalised to 11-digit 5-4-2; NULL if ambiguous",
    "ndc_product": "first 9 digits of NDC11 (labeler+product, ignores package)",
    "ndc_was_padded": "source NDC had < 11 digits (leading zeros restored)",
    "icd10_code": "ICD-10 without dot, upper-case",
    "icd10_source": "document (translation in the C-CDA) | learned_same_patient | learned_crosswalk | claim",
    "rxnorm_source": "document | learned_vendor_code (Multum/Medi-Span/FDB code seen elsewhere with an RxNorm)",
    "patient_share": "share of patients whose documents used the winning ICD-10 for this SNOMED code",
    "candidates": "all ICD-10 codes seen for this SNOMED code with patient counts",
    "med_key": "RXNORM:<rxcui> | NDC:<ndc11> | NAME:<normalised name>",
    "problem_key": "SNOMED:<code> | ICD10:<code> | NAME:<name>",
    "drug_key": "IN:<ingredient> (with --rxnav) | RXNORM:<rxcui> | NDCP:<ndc product> | NAME:<name>",
    "match_level": "exact_code | same_category (3-char ICD-10) | ccda_only | claims_only",
    "match_status": "both | ccda_only | rx_claims_only",
    "drug_identity_source": "document | learned_ndc11 | learned_ndc_product | rxnav | ndc_only",
    "latest_status": "status_code from the most recent document containing the drug",
    "mood_code": "INT = intended/ordered, EVN = actually taken/administered",
    "ccda_claims_overlap_days": "days in which the patient's C-CDA document dates and claims period overlap",
    "claims_dx_codes_documented_pct": "% of the patient's distinct billed ICD-10 codes also on a C-CDA problem list",
    "claims_dx_categories_documented_pct": "same at 3-character ICD-10 category level",
    "source_record_id": "document_id:entry_index (CCDA) or claim id / claim line key (claims)",
    "record_date": "CCDA: start/onset, falls back to concern start then document date; claims: service/fill date",
    "n_distinct_icd10": ">1 means the SNOMED code was translated to different ICD-10 codes by different documents",
    "service_type": "IP inpatient, OP outpatient facility, PO professional, PA other",
    "mr_line": "Milliman Health Cost Guidelines service line",
    "src_lob_raw": "kept as delivered; values are Excel-mangled dates (e.g. '9-Jan')",
}


def main():
    out = ["# Data dictionary", "",
           "Generated from `output/parquet` by `scripts/make_data_dictionary.py`. Every table is also in "
           "`output/csv/<layer>/` and in `output/ccda_claims.db` as `<layer>_<table>`.", ""]
    for t, (desc, key) in TABLES.items():
        path = os.path.join(ROOT, "output", "parquet", t + ".parquet")
        if not os.path.exists(path):
            continue
        f = pq.ParquetFile(path)
        out += [f"## {t.replace('/', '.')}", "", f"{desc}. **Grain / key:** {key}. **Rows:** {f.metadata.num_rows:,}", "",
                "| column | type | notes |", "|---|---|---|"]
        for fld in f.schema_arrow:
            out.append(f"| {fld.name} | {fld.type} | {NOTES.get(fld.name, '')} |")
        out.append("")
    with open(os.path.join(ROOT, "docs", "DATA_DICTIONARY.md"), "w") as fh:
        fh.write("\n".join(out))


if __name__ == "__main__":
    main()
