# Data dictionary

Generated from `output/parquet` by `scripts/make_data_dictionary.py`. Every table is also in `output/csv/<layer>/` and in `output/ccda_claims.db` as `<layer>_<table>`.

## silver.ccda_documents

One row per C-CDA file. **Grain / key:** patient_id, document_id. **Rows:** 569

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| document_id | string |  |
| file_name | string |  |
| file_size_bytes | int64 |  |
| parse_status | string |  |
| parse_error | string |  |
| clinical_document_id | string |  |
| document_type_code | string |  |
| document_type | string |  |
| title | string |  |
| document_date | date32[day] |  |
| set_id | string |  |
| version_number | string |  |
| source_system | string |  |
| custodian_org | string |  |
| gender | string |  |
| birth_date | date32[day] |  |
| patient_state | string |  |
| has_medication_section | bool |  |
| has_problem_section | bool |  |
| null_flavored_sections | string |  |
| medication_entry_count | int64 |  |
| problem_entry_count | int64 |  |

## silver.ccda_medications

One row per medication entry (substanceAdministration) per document. **Grain / key:** document_id, entry_index. **Rows:** 12,752

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| document_id | string |  |
| document_date | date32[day] |  |
| section_type | string |  |
| entry_index | int64 | 0-based position of the entry within the document (lineage) |
| entry_id | string |  |
| mood_code | string | INT = intended/ordered, EVN = actually taken/administered |
| negated | bool |  |
| status_code | string |  |
| start_date | date32[day] |  |
| end_date | date32[day] |  |
| start_raw | string |  |
| end_raw | string |  |
| frequency | string |  |
| dose_value | string |  |
| dose_unit | string |  |
| route | string |  |
| drug_code | string |  |
| drug_code_system | string |  |
| drug_code_null_flavor | string |  |
| rxnorm_code | string |  |
| ndc_raw | string |  |
| ndc11 | string | NDC normalised to 11-digit 5-4-2; NULL if ambiguous |
| drug_name | string |  |
| drug_name_source | string | where drug_name came from: rxnorm_display | code_display | material_name | original_text | entry_text |
| is_placeholder | bool | 'No data available…' style or completely uncoded/unnamed entry |
| drug_original_text | string |  |
| material_name | string |  |
| sig_text | string |  |
| entry_text | string |  |
| author_time | date32[day] |  |
| translations_json | string | all <translation> codings: [{code, system, display}] |
| rxnorm_source | string | document | learned_vendor_code (Multum/Medi-Span/FDB code seen elsewhere with an RxNorm) |
| source_system | string |  |
| ndc_product | string | first 9 digits of NDC11 (labeler+product, ignores package) |
| drug_name_key | string |  |
| med_key | string | RXNORM:<rxcui> | NDC:<ndc11> | NAME:<normalised name> |
| is_usable | bool | not placeholder, not negated, has an identity key; only these reach gold |

## silver.ccda_problems

One row per problem observation per document. **Grain / key:** document_id, entry_index. **Rows:** 8,268

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| document_id | string |  |
| document_date | date32[day] |  |
| entry_index | int64 | 0-based position of the entry within the document (lineage) |
| concern_id | string |  |
| concern_status | string |  |
| concern_start_date | date32[day] |  |
| concern_end_date | date32[day] |  |
| observation_id | string |  |
| problem_type_code | string |  |
| problem_type | string |  |
| negated | bool |  |
| onset_date | date32[day] |  |
| abatement_date | date32[day] |  |
| onset_raw | string |  |
| problem_code | string |  |
| problem_code_system | string |  |
| problem_code_null_flavor | string |  |
| snomed_code | string |  |
| icd10_code | string | ICD-10 without dot, upper-case |
| icd10_code_raw | string |  |
| icd9_code | string |  |
| problem_name | string |  |
| problem_name_source | string | where problem_name came from |
| is_placeholder | bool | 'No data available…' style or completely uncoded/unnamed entry |
| problem_original_text | string |  |
| problem_status | string |  |
| author_time | date32[day] |  |
| translations_json | string | all <translation> codings: [{code, system, display}] |
| source_system | string |  |
| icd10_source | string | document (translation in the C-CDA) | learned_same_patient | learned_crosswalk | claim |
| icd10_category | string |  |
| problem_key | string | SNOMED:<code> | ICD10:<code> | NAME:<name> |
| is_usable | bool | not placeholder, not negated, has an identity key; only these reach gold |

## silver.claims_dx_lines

One row per medical claim line. **Grain / key:** claim_id, line_num. **Rows:** 2,019

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| claim_id | string |  |
| line_num | int64 |  |
| claim_line_key | string |  |
| from_date | date32[day] |  |
| to_date | date32[day] |  |
| admit_date | date32[day] |  |
| discharge_date | date32[day] |  |
| paid_date | date32[day] |  |
| service_type | string | IP inpatient, OP outpatient facility, PO professional, PA other |
| mr_line | string | Milliman Health Cost Guidelines service line |
| place_of_service | string |  |
| specialty | string |  |
| provider_id | string |  |
| bill_type | string |  |
| rev_code | string |  |
| hcpcs | string |  |
| modifier | string |  |
| units | double |  |
| billed | double |  |
| allowed | double |  |
| paid | double |  |
| patient_pay | double |  |
| icd_version | string |  |
| admit_diag | string |  |
| principal_diag | string |  |
| lob | string |  |
| product | string |  |
| src_lob_raw | string | kept as delivered; values are Excel-mangled dates (e.g. '9-Jan') |
| claim_year | int64 |  |

## silver.claims_dx_diagnoses

One row per claim line x diagnosis position (ICDDiag1..30 unpivoted). **Grain / key:** claim_line_key, diag_position. **Rows:** 4,672

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| claim_id | string |  |
| line_num | int64 |  |
| service_date | date32[day] |  |
| icd_version | string |  |
| diag_position | int64 |  |
| icd10_code_raw | string |  |
| icd10_code | string | ICD-10 without dot, upper-case |
| claim_line_key | string |  |
| is_principal | bool |  |
| icd10_category | string |  |
| icd10_description | string |  |

## silver.claims_rx

One row per pharmacy fill. **Grain / key:** claim_id. **Rows:** 541

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| claim_id | string |  |
| fill_date | date32[day] |  |
| paid_date | date32[day] |  |
| ndc_raw | string |  |
| ndc11 | string | NDC normalised to 11-digit 5-4-2; NULL if ambiguous |
| ndc_was_padded | bool | source NDC had < 11 digits (leading zeros restored) |
| days_supply | int64 |  |
| quantity_dispensed | double |  |
| mail_order | string |  |
| generic_brand_line | string |  |
| provider_id | string |  |
| ingredient_cost | double |  |
| dispensing_fee | double |  |
| billed | double |  |
| allowed | double |  |
| paid | double |  |
| patient_pay | double |  |
| lob | string |  |
| product | string |  |
| src_lob_raw | string | kept as delivered; values are Excel-mangled dates (e.g. '9-Jan') |
| claim_year | int64 |  |
| ndc_product | string | first 9 digits of NDC11 (labeler+product, ignores package) |
| supply_end_date | date32[day] |  |

## gold.patient_summary

One row per patient. **Grain / key:** patient_id. **Rows:** 24

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| gender | string |  |
| birth_date | date32[day] |  |
| n_distinct_birth_dates | int64 |  |
| n_ccda_documents | int64 |  |
| first_document_date | date32[day] |  |
| last_document_date | date32[day] |  |
| n_source_systems | int64 |  |
| n_ccda_medications | int64 |  |
| n_ccda_problems | int64 |  |
| n_dx_claim_lines | int64 |  |
| n_distinct_dx_codes | int64 |  |
| medical_paid | double |  |
| first_medical_service | date32[day] |  |
| last_medical_service | date32[day] |  |
| n_rx_fills | int64 |  |
| n_distinct_ndcs | int64 |  |
| rx_paid | double |  |
| dx_codes_in_both | int64 |  |
| claims_dx_codes_documented_pct | double | % of the patient's distinct billed ICD-10 codes also on a C-CDA problem list |
| claims_dx_categories_documented_pct | double | same at 3-character ICD-10 category level |
| drugs_on_list_and_filled | int64 |  |
| drugs_filled_not_on_list | int64 |  |
| claims_period_start | date32[day] |  |
| claims_period_end | date32[day] |  |
| ccda_claims_overlap_days | int64 | days in which the patient's C-CDA document dates and claims period overlap |
| has_ccda | bool |  |
| has_medical_claims | bool |  |
| has_rx_claims | bool |  |

## gold.patient_medications

Distinct medication per patient, deduplicated across documents. **Grain / key:** patient_id, med_key. **Rows:** 1,796

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| med_key | string | RXNORM:<rxcui> | NDC:<ndc11> | NAME:<normalised name> |
| drug_name | string |  |
| rxnorm_code | string |  |
| ndc11 | string | NDC normalised to 11-digit 5-4-2; NULL if ambiguous |
| latest_status | string | status_code from the most recent document containing the drug |
| latest_mood_code | string |  |
| route | string |  |
| frequency | string |  |
| sig_text | string |  |
| first_start_date | date32[day] |  |
| last_end_date | date32[day] |  |
| first_seen_document_date | date32[day] |  |
| last_seen_document_date | date32[day] |  |
| n_documents | int64 |  |
| n_entries | int64 |  |
| source_systems | string |  |

## gold.patient_problems

Distinct problem per patient, deduplicated across documents. **Grain / key:** patient_id, problem_key. **Rows:** 1,146

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| problem_key | string | SNOMED:<code> | ICD10:<code> | NAME:<name> |
| problem_name | string |  |
| snomed_code | string |  |
| icd10_code | string | ICD-10 without dot, upper-case |
| icd10_category | string |  |
| icd10_source | string | document (translation in the C-CDA) | learned_same_patient | learned_crosswalk | claim |
| problem_type | string |  |
| latest_problem_status | string |  |
| latest_concern_status | string |  |
| first_onset_date | date32[day] |  |
| last_abatement_date | date32[day] |  |
| first_seen_document_date | date32[day] |  |
| last_seen_document_date | date32[day] |  |
| n_documents | int64 |  |
| n_entries | int64 |  |
| source_systems | string |  |

## gold.medication_records

C-CDA medication entries UNION ALL pharmacy fills. **Grain / key:** source, source_record_id. **Rows:** 13,251

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| source | string |  |
| source_record_id | string | document_id:entry_index (CCDA) or claim id / claim line key (claims) |
| record_date | date32[day] | CCDA: start/onset, falls back to concern start then document date; claims: service/fill date |
| record_end_date | date32[day] |  |
| rxnorm_code | string |  |
| ndc11 | string | NDC normalised to 11-digit 5-4-2; NULL if ambiguous |
| ndc_product | string | first 9 digits of NDC11 (labeler+product, ignores package) |
| drug_name | string |  |
| drug_identity_source | string | document | learned_ndc11 | learned_ndc_product | rxnav | ndc_only |
| status | string |  |
| days_supply | double |  |
| paid_amount | double |  |
| source_system | string |  |
| ingredient | string |  |

## gold.condition_records

C-CDA problem entries UNION ALL claim diagnoses. **Grain / key:** source, source_record_id (+ diag_position for claims). **Rows:** 12,888

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| source | string |  |
| source_record_id | string | document_id:entry_index (CCDA) or claim id / claim line key (claims) |
| record_date | date32[day] | CCDA: start/onset, falls back to concern start then document date; claims: service/fill date |
| icd10_code | string | ICD-10 without dot, upper-case |
| icd10_category | string |  |
| icd10_source | string | document (translation in the C-CDA) | learned_same_patient | learned_crosswalk | claim |
| snomed_code | string |  |
| description | string |  |
| status | string |  |
| diag_position | int64 |  |
| source_system | string |  |

## gold.condition_concordance

Patient x ICD-10 code across both sources. **Grain / key:** patient_id, icd10_code. **Rows:** 1,556

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| icd10_code | string | ICD-10 without dot, upper-case |
| icd10_category | string |  |
| description | string |  |
| in_ccda | bool |  |
| in_claims | bool |  |
| match_level | string | exact_code | same_category (3-char ICD-10) | ccda_only | claims_only |
| category_in_ccda | bool |  |
| category_in_claims | bool |  |
| ccda_problem_name | string |  |
| ccda_icd10_source | string |  |
| ccda_first_seen | date32[day] |  |
| ccda_n_documents | double |  |
| claims_first_service | date32[day] |  |
| claims_last_service | date32[day] |  |
| claims_n_lines | int64 |  |
| claims_principal | bool |  |

## gold.medication_concordance

Patient x drug across both sources. **Grain / key:** patient_id, drug_key. **Rows:** 2,022

| column | type | notes |
|---|---|---|
| patient_id | string |  |
| drug_key | string | IN:<ingredient> (with --rxnav) | RXNORM:<rxcui> | NDCP:<ndc product> | NAME:<name> |
| drug_name | string |  |
| on_ccda_med_list | bool |  |
| filled_at_pharmacy | bool |  |
| match_status | string | both | ccda_only | rx_claims_only |
| ccda_first_date | date32[day] |  |
| ccda_last_date | date32[day] |  |
| ccda_n_records | int64 |  |
| rx_first_date | date32[day] |  |
| rx_last_date | date32[day] |  |
| rx_n_records | int64 |  |
| rx_paid | double |  |

## gold.ref_ndc_rxnorm_learned

NDC11 -> RxNorm pairs observed in C-CDA translations. **Grain / key:** ndc11. **Rows:** 324

| column | type | notes |
|---|---|---|
| ndc11 | string | NDC normalised to 11-digit 5-4-2; NULL if ambiguous |
| ndc_product | string | first 9 digits of NDC11 (labeler+product, ignores package) |
| rxnorm_code | string |  |
| drug_name | string |  |
| n_observations | int64 |  |

## gold.ref_snomed_icd10_learned

SNOMED -> most frequent ICD-10 observed in C-CDA translations. **Grain / key:** snomed_code. **Rows:** 504

| column | type | notes |
|---|---|---|
| snomed_code | string |  |
| icd10_code | string | ICD-10 without dot, upper-case |
| patient_share | double | share of patients whose documents used the winning ICD-10 for this SNOMED code |
| n_patients | int64 |  |
| n_observations | int64 |  |
| n_distinct_icd10 | int64 | >1 means the SNOMED code was translated to different ICD-10 codes by different documents |
| candidates | string | all ICD-10 codes seen for this SNOMED code with patient counts |
