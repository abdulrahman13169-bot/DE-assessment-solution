# C-CDA + Claims Integration Pipeline — Data Engineer Assessment

This pipeline parses 569 C-CDA clinical documents into **Medications** and **Problems**, combines them with
the medical (Dx) and pharmacy (Rx) claims extracts, and publishes typed, deduplicated, analysis-ready tables
as **Parquet (Delta-ready), CSV and SQLite**. Each step writes its output, and a data-quality report gates the run.

```
569 C-CDA XML ──► parse ──► silver (1 row per source fact) ──► gold (deduped + clinical ∪ claims + concordance)
2 claims CSVs ──► conform ──┘                                     └──► Parquet / CSV / SQLite ──► Databricks Delta
```

| Exam step | Where |
|---|---|
| 1. Parse C-CDA into Medications & Problems | `pipeline/ccda_parser.py` → `silver.ccda_medications`, `silver.ccda_problems` (+ deduped `gold.patient_medications`, `gold.patient_problems`) |
| 2. Combine clinical with claims | `pipeline/combine.py` → `gold.medication_records`, `gold.condition_records`, `gold.*_concordance`, `gold.patient_summary` |
| 3. Store output, easy to import into Databricks | `output/parquet/{silver,gold}/*.parquet`, `output/csv/`, `output/ccda_claims.db`; loader notebook `databricks/01_load_to_delta.py` |
| 4. Describe the pipeline | [`docs/PIPELINE_DESIGN.md`](docs/PIPELINE_DESIGN.md) |
| 5. Thought process & findings | [`docs/APPROACH_AND_FINDINGS.md`](docs/APPROACH_AND_FINDINGS.md) |
| 6. Further improvements | [`docs/IMPROVEMENTS.md`](docs/IMPROVEMENTS.md) |
| 7. CDM (OMOP / FHIR) structure | [`docs/CDM_MAPPING.md`](docs/CDM_MAPPING.md) |
| Data dictionary | [`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md) |
| AI usage disclosure | [`AI_USAGE.md`](AI_USAGE.md) |

## Quick start

Requires Python ≥ 3.10. Tested on Python 3.13 with pandas 2.2 and 3.0; output loads into Spark 3.5.

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run_pipeline.py            # ~10 s; reads ./ccda_files and the two CSVs in the repo root
python -m pytest -q               # 37 tests on synthetic fixtures
```

Expected input layout (as delivered in the assessment repo):

```
ccda_files/<patient_id>_<document_id>_masked.xml
data_engineer_exam_claims_final.csv
data_engineer_exam_rx_final.csv
```

Options:

| flag | purpose |
|---|---|
| `--ccda-dir`, `--dx-claims`, `--rx-claims`, `--out` | override input/output locations |
| `--ccda-url-csv ccda_pre_signed_urls.csv` | download the C-CDAs from pre-signed URLs first (idempotent) |
| `--formats parquet,csv,sqlite` | choose outputs |
| `--workers N` | parser processes (default: all CPUs; `1` = sequential for debugging) |
| `--rxnav` / `--rxnav-offline` | enrich NDC → RxNorm → ingredient via NLM RxNav (internet required; cached in `reference/rxnav_cache.json`) for ingredient-level medication matching |
| `--strict` | exit code 1 if any `error`-severity data-quality check fails (CI / workflow gate) |

## Load into Databricks

1. Upload `output/parquet/` (keeping `silver/` and `gold/`) to a Unity Catalog Volume, e.g.
   `/Volumes/main/ccda_assessment/raw/parquet`.
2. Import and run `databricks/01_load_to_delta.py`. It creates `main.ccda_assessment.{silver_*,gold_*}` Delta
   tables with comments and informational primary keys.
3. Explore with `databricks/02_review_queries.sql`.

To run the pipeline itself on Databricks, use `databricks/00_run_pipeline.py` from a Git folder.
Parquet dates are `DATE`, and all-null columns are typed `STRING`, so nothing needs casting.
Without Databricks, open `output/ccda_claims.db` in any SQLite client.

## Outputs

**Silver** — one row per source fact, typed, with lineage back to the XML entry or claim line

| table | rows | grain |
|---|---:|---|
| `ccda_documents` | 569 | C-CDA file (header, source EHR, demographics, parse status) |
| `ccda_medications` | 12,752 | medication entry per document |
| `ccda_problems` | 8,268 | problem observation per document |
| `claims_dx_lines` | 2,019 | medical claim line |
| `claims_dx_diagnoses` | 4,672 | claim line × diagnosis position (ICDDiag1..30 unpivoted) |
| `claims_rx` | 541 | pharmacy fill (NDC normalised to 11 digits) |

**Gold** — what an analyst or underwriting model consumes

| table | rows | grain |
|---|---:|---|
| `patient_summary` | 24 | patient: demographics, volumes, cost, concordance KPIs, clinical/claims overlap window |
| `patient_medications` | 1,796 | distinct drug per patient (deduplicated across documents; latest status) |
| `patient_problems` | 1,146 | distinct problem per patient (deduplicated; SNOMED + ICD-10) |
| `medication_records` | 13,251 | C-CDA medication entries ∪ pharmacy fills, one schema |
| `condition_records` | 12,888 | C-CDA problems ∪ claim diagnoses, one schema |
| `condition_concordance` | 1,556 | patient × ICD-10: C-CDA only / claims only / both (exact or category) |
| `medication_concordance` | 2,022 | patient × drug: on med list / filled / both |
| `ref_ndc_rxnorm_learned`, `ref_snomed_icd10_learned` | 324 / 504 | crosswalks learned from the C-CDAs' own `<translation>` codes |

Plus `output/data_quality_report.md` (27 checks: keys, parse failures, coding coverage, upstream defects).

## Highlights

* **All 569 documents parse** across 23 EHR software versions (Cerner, Epic, athenahealth, Greenway, eCW, …).
  The parser locates sections by templateId *or* LOINC code, and resolves drug and problem names from
  `displayName`, `<name>`, `<originalText>` or `#reference` links into the narrative. Every Epic medication name is only in the narrative.
* **Deduplication:** 12.7k medication entries → 1.8k real patient-drugs, and 8.3k problem entries → 1.1k
  patient-problems. Without it, count-based features would be inflated about 7×.
* **Learned crosswalks** from the documents' own `<translation>` codes raise clinical ICD-10 coverage from 25% to 70%
  (same-patient mapping first, then a cross-patient majority vote; ambiguous codes stay unmapped) and fill RxNorm
  for Multum/Medi-Span-only entries (80% → 82%), with no licensed terminology.
* **Upstream defects caught:** 20% of Rx NDCs had lost leading zeros (restored), and `srcLOB` is 100% Excel-date-mangled.
* **Finding:** clinical documents (mostly 2021–24) and claims (2016–21) barely overlap in time. 9 of 24 patients have no
  overlap at all, and billed-but-undocumented codes are mostly screening and acute Z/R/J codes. Details in
  [`docs/APPROACH_AND_FINDINGS.md`](docs/APPROACH_AND_FINDINGS.md).

## Repository layout

```
run_pipeline.py              entry point (python run_pipeline.py --help)
pipeline/
  hl7.py                     HL7 v3 helpers: namespaces, OIDs, timestamps, coded elements, narrative refs
  ccda_parser.py             one C-CDA → document / medication / problem records
  ccda_ingest.py             discovery, optional pre-signed URL download, parallel parse
  claims.py                  Dx + Rx claims conformance (unpivot, NDC11, typing)
  normalize.py               NDC / ICD-10 / drug-name normalisation, ICD-10-CM descriptions
  combine.py                 learned crosswalks, dedup, unions, concordance, patient summary
  terminology.py             optional RxNav enrichment with on-disk cache
  quality.py                 data-quality checks + report
  load.py                    Parquet / CSV / SQLite writers
  cli.py                     orchestration
databricks/                  00_run_pipeline · 01_load_to_delta · 02_review_queries.sql
docs/                        design, findings, improvements, CDM mapping, data dictionary
scripts/make_data_dictionary.py
tests/                       pytest suite + synthetic C-CDA / claims fixtures (no real patient data)
ai_artifacts/                record of how AI assistance was used
output/                      generated: parquet/ (committed), csv/, ccda_claims.db, data_quality_report.*
```
