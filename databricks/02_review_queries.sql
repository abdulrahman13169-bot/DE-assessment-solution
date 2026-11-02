-- Databricks notebook source
-- Review queries for the technical interview. Assumes main.ccda_assessment (see 01_load_to_delta).
USE main.ccda_assessment;

-- COMMAND ----------
-- 1. Patient-level overview
SELECT patient_id, gender, birth_date, n_ccda_documents, n_source_systems,
       n_ccda_medications, n_ccda_problems, n_dx_claim_lines, n_rx_fills,
       medical_paid, rx_paid, ccda_claims_overlap_days,
       claims_dx_codes_documented_pct, claims_dx_categories_documented_pct
FROM gold_patient_summary
ORDER BY n_ccda_documents DESC;

-- COMMAND ----------
-- 2. One patient's combined condition timeline (clinical + claims in one schema)
SELECT record_date, source, icd10_code, snomed_code, description, status, source_system
FROM gold_condition_records
WHERE patient_id = (SELECT patient_id FROM gold_patient_summary ORDER BY n_dx_claim_lines DESC LIMIT 1)
ORDER BY record_date;

-- COMMAND ----------
-- 3. How well does the clinical problem list explain what was billed?
SELECT match_level, COUNT(*) AS patient_codes, COUNT(DISTINCT patient_id) AS patients
FROM gold_condition_concordance
GROUP BY match_level
ORDER BY patient_codes DESC;

-- COMMAND ----------
-- 4. Billed diagnoses (top categories) that never appear on the patient's problem list
SELECT icd10_category, ANY_VALUE(description) AS example_description,
       COUNT(DISTINCT patient_id) AS patients, SUM(claims_n_lines) AS claim_lines
FROM gold_condition_concordance
WHERE in_claims AND NOT category_in_ccda
GROUP BY icd10_category
ORDER BY patients DESC, claim_lines DESC
LIMIT 20;

-- COMMAND ----------
-- 5. Current (latest-status active) medication list per patient, with document support
SELECT patient_id, drug_name, rxnorm_code, latest_status, first_start_date,
       last_seen_document_date, n_documents, source_systems
FROM gold_patient_medications
WHERE latest_status IN ('active', 'ordered') OR latest_status IS NULL
ORDER BY patient_id, last_seen_document_date DESC;

-- COMMAND ----------
-- 6. Parse coverage by EHR vendor (where do names / codes come from?)
SELECT d.source_system,
       COUNT(*)                                             AS med_entries,
       ROUND(100 * AVG(CASE WHEN m.rxnorm_code IS NOT NULL THEN 1 ELSE 0 END), 1) AS pct_rxnorm,
       ROUND(100 * AVG(CASE WHEN m.drug_name_source = 'original_text' THEN 1 ELSE 0 END), 1) AS pct_name_from_narrative
FROM silver_ccda_medications m JOIN silver_ccda_documents d USING (patient_id, document_id)
GROUP BY d.source_system
ORDER BY med_entries DESC;
