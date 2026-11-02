# Data quality report

Generated 2026-11-02T23:13:45+00:00

## Row counts

| table | rows |
|---|---:|
| silver.ccda_documents | 569 |
| silver.ccda_medications | 12,752 |
| silver.ccda_problems | 8,268 |
| silver.claims_dx_lines | 2,019 |
| silver.claims_dx_diagnoses | 4,672 |
| silver.claims_rx | 541 |
| gold.patient_summary | 24 |
| gold.patient_medications | 1,796 |
| gold.patient_problems | 1,146 |
| gold.medication_records | 13,251 |
| gold.condition_records | 12,888 |
| gold.condition_concordance | 1,556 |
| gold.medication_concordance | 2,022 |
| gold.ref_ndc_rxnorm_learned | 324 |
| gold.ref_snomed_icd10_learned | 504 |

## Checks

| result | severity | check | metric | detail |
|---|---|---|---:|---|
| PASS | error | ccda_parse_failures | 0 | 0 of 569 documents failed to parse (quarantined, run continues) |
| PASS | info | ccda_docs_without_med_or_problem_section | 0 | documents with neither section (e.g. narrative-only notes) |
| PASS | error | ccda_duplicate_document_ids | 0 | patient_id+document_id must be unique |
| FAIL | warn | patient_birth_date_consistent_across_documents | 1 | patients with >1 birth date across their C-CDAs: ['2w8e42im-c2g2-76e3-i5d4-86xn28060yz0'] |
| PASS | info | ccda_medication_placeholder_entries | 42 | 'No data available' / uncoded medication entries excluded from gold |
| PASS | warn | ccda_medication_without_name | 36 | medication entries with no resolvable name |
| PASS | info | ccda_problem_placeholder_entries | 52 | 'No data available' / uncoded problem entries excluded from gold |
| PASS | warn | ccda_problem_without_name | 8 | problem entries with no resolvable name |
| PASS | info | ccda_medication_rxnorm_coverage_document | 79.8 | % usable medication entries with RxNorm in the document |
| PASS | info | ccda_medication_rxnorm_coverage_after_vendor_crosswalk | 81.9 | % usable medication entries with RxNorm after learning vendor-code (Multum/Medi-Span/FDB) pairs |
| PASS | info | ccda_problem_icd10_coverage_document | 24.7 | % usable problem entries with an ICD-10 translation in the document |
| PASS | info | ccda_problem_icd10_coverage_after_crosswalk | 70.2 | % usable problem entries with ICD-10 after same-patient + cross-patient SNOMED->ICD-10 learning |
| PASS | info | snomed_icd10_ambiguous_mappings | 23 | SNOMED codes translated inconsistently across patients; left unmapped |
| PASS | warn | ccda_ndc_unnormalisable | 31 | C-CDA NDC translations that are 10-digit/2-segment and therefore ambiguous; not joined |
| PASS | warn | rx_ndc_leading_zeros_restored | 107 | Rx claim NDCs shorter than 11 digits (leading zeros lost upstream, e.g. Excel); left-padded |
| FAIL | warn | claims_srclob_excel_date_mangling | 100.0 | % of srcLOB values that look like Excel-converted dates ('9-Jan'); kept raw, use LOB instead |
| PASS | error | dx_claim_line_key_unique | 0 | ClaimID+LineNum is unique |
| PASS | error | rx_claim_id_unique | 0 | Rx ClaimID is unique |
| PASS | warn | dx_invalid_icd10_codes | 0 | claim diagnosis codes failing ICD-10 shape check |
| PASS | info | dx_codes_without_icd10cm_description | 0 | claim diagnoses not found in the bundled ICD-10-CM release (non-billable/retired codes) |
| PASS | error | dx_from_date_parse | 0 | unparseable from_date values |
| PASS | error | dx_paid_date_parse | 0 | unparseable paid_date values |
| PASS | warn | dx_paid_before_service | 0 | claim lines paid before service date |
| PASS | info | negative_paid_amounts | 1 | reversals/adjustments present |
| PASS | warn | patients_with_ccda_but_no_claims | 0 | [] |
| PASS | warn | patients_with_claims_but_no_ccda | 0 | [] |
| PASS | info | rx_claims_resolved_to_rxnorm_via_learned_crosswalk | 8.7 | % of pharmacy fills whose NDC could be mapped to RxNorm using only crosswalk pairs observed in the C-CDAs |
