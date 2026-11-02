# Approach, decisions and findings (exam step 5)

## How I approached it

1. **Profile before parsing.** I scanned all 569 documents for section codes/templateIds, EHR vendor,
   code systems and nullFlavors before writing the parser. C-CDA is a standard, but every vendor uses
   its optional parts differently. Profiling showed which patterns mattered (below) and gave the test
   fixtures their shape.
2. **Keep every source fact, then decide.** Silver keeps one row per XML entry or claim line, with lineage
   (`document_id` + `entry_index`). Deduplication, crosswalks and concordance happen in gold, where the
   business rules are visible and can be changed without re-parsing.
3. **Build for messy input.** One bad file, one odd timestamp or one vendor quirk must never stop the
   run. These cases are flagged, counted in the DQ report, and kept out of joins.
4. **Use the data's own terminology before buying terminology.** EHRs often send SNOMED with an ICD-10
   `<translation>`, or RxNorm with an NDC. Mining those pairs gave usable crosswalks offline, with no
   UMLS licence.

## What the C-CDAs look like

| | |
|---|---|
| Documents / patients | 569 / 24 (1 to 111 documents per patient); all parse, 0 failures |
| Size | 28 KB – 12.5 MB per file (median 284 KB) |
| Document type | All CCD "Summarization of Episode Note", 4 spellings of the same LOINC 34133-9 |
| Source EHRs | 23 software strings: Cerner Millennium (422 docs), athenahealth, Greenway, eClinicalWorks, Epic, Rosetta… |
| Medication entries | 12,752 (12,718 med list + 34 "medications administered") → **1,796** distinct patient-drugs |
| Problem entries | 8,268 → **1,146** distinct patient-problems |

**Vendor differences the parser had to handle**

| Pattern | Example | Handling |
|---|---|---|
| Section found by LOINC code only, or by different templateId versions (`2.1` vs `2.1.1`, `2.5` vs `2.5.1`) | Greenway vs Cerner | Match either templateId **or** LOINC code |
| Drug code `nullFlavor="OTH"` with RxNorm only in `<translation>` | Cerner: Multum IDs + RxNorm translation | Search primary code, then translations, for each target system |
| Name only in the narrative via `<originalText><reference value="#…"/>` | **Epic: 100% of med names**; Cerner 21% | Build an `@ID → text` index per document and resolve references |
| Name only in `<manufacturedMaterial><name>` | athenahealth | Included in the name-precedence chain; `drug_name_source` records which one won |
| "No data available for this section" sent as a real entry | 42 med and 52 problem entries | `is_placeholder` flag; excluded from gold |
| Non-HL7 timestamps (`2022-03-21 08:26:00`) and partial precision (`2015`, `202106`) | Rosetta | Tolerant TS parser; precision fills to first month/day |
| Problem status sent as SNOMED code without displayName | eClinicalWorks | SNOMED status code → label map |
| Same SNOMED code translated to different ICD-10 codes | 34 of 504 SNOMED codes (e.g. *Diabetes mellitus* → `E11.9` for 3 patients, `E11.59` for 1) | Reuse the same patient's own translation first; across patients take a strict majority of *patients*, so one patient with 40 copies of a document can't decide; 23 ties stay unmapped (`ref_snomed_icd10_learned.candidates`) |

## What the claims look like

* Dx claims: 2,019 lines, 24 members, all ICD-10, 2016-01 → 2021-12, up to 9 diagnosis positions used.
* Rx claims: 541 fills, 19 members (5 members have no pharmacy claims), 2016 → 2021.
* **Upstream defects found:**
  * **107 NDCs (20%) lost leading zeros** (8 or 9 digits instead of 11), consistent with the file having
    passed through Excel or a numeric cast. They are left-padded and flagged with `ndc_was_padded`.
    Without this, they would never join to anything.
  * **`srcLOB` is 100% Excel-mangled into dates** (`9-Jan`, `1-Mar`). It is kept raw but not used; `LOB` is clean.
  * Keys are clean: ClaimID+LineNum is unique in Dx, ClaimID is unique in Rx.

## Findings from combining the data

1. **Clinical and claims coverage barely overlap in time.** Claims run 2016–2021, but the C-CDAs are
   mostly from 2021–2024. 9 of 24 patients have **no overlap at all** between their document dates and
   their claims period (`patient_summary.ccda_claims_overlap_days`). Any concordance metric should be read
   with this in mind. In production this is the first thing I would raise with the data provider.
2. **Problem lists and claims describe different things.** Only 33 patient×ICD-10 pairs match exactly, and
   another 99 match at the 3-character category level. The billed codes that never appear on a problem
   list are mostly visit/screening Z-codes (Z00 general exam: 18 patients; Z20 COVID exposure: 14;
   Z23 immunization and Z01 special exams: 13 each) and acute, self-limiting illnesses (J06, R05, J02). The matches are chronic
   conditions such as hypertension (I10), obesity, hyperlipidemia and anxiety, which is what problem lists
   are meant to hold. For risk use cases, chronic-condition matching at category or HCC level is more
   meaningful than exact-code matching.
3. **The learned SNOMED→ICD-10 crosswalk nearly triples usable ICD-10 coverage** on the clinical side (25% → 70%
   of problem entries: 2,482 filled from the same patient's other documents, 1,254 from the cross-patient vote). That is what makes clinical-vs-claims comparison possible at all without a licensed map.
4. **Medication concordance needs an NDC dictionary.** Only ~9% of pharmacy fills resolve to RxNorm offline,
   so most fills show as "rx_claims_only". Run with `--rxnav` (internet required) to match at the ingredient
   level. The test suite shows a 20 mg list entry matching a 40 mg fill of a different NDC.
5. **Deduplication matters more than parsing.** Each patient's medication list repeats across up to 111
   documents (≈7 copies of every entry on average). Without dedup, any count-based feature (number of
   medications, number of chronic conditions) would be inflated 5–10×.
6. **One patient has two different birth dates** across their documents (`2w8e42im-…`). This is flagged by the DQ
   report. `patient_summary` uses the most common value and records `n_distinct_birth_dates`.

## Key design decisions (and the alternatives considered)

| Decision | Why | Alternative |
|---|---|---|
| lxml + XPath, explicit per-template logic | Fast (8 s for 230 MB), transparent, easy to test against vendor quirks | Generic XML→JSON flattening (loses C-CDA semantics); commercial parsers or FHIR converters (Microsoft FHIR-Converter, which I'd consider at scale; see IMPROVEMENTS) |
| Patient ID from file name | The data overview defines it; masked documents' internal MRNs are not reliable join keys | Use `recordTarget/id` (inconsistent across vendors) |
| "Latest document wins" for med/problem status | The most recent clinical statement is the best estimate of current state | Majority vote; status from the latest *author time* (more precise but often null) |
| ICD-10 category-level concordance alongside exact | Claims use maximum specificity (`E11.65`) while problem lists often hold `E11.9` | HCC / CCSR grouping (better; needs a mapping table) |
| NULL for ambiguous 10-digit NDCs | A wrong join is worse than a missing one | Try all three 10→11 conversions and keep any that hit (possible with an NDC directory) |
| Parquet with `DATE` columns as the hand-off format | Loads into Delta without casts, keeps types, small | Write Delta directly (needs Spark/delta-rs locally); CSV only (loses types) |
| SQLite as well | Lets reviewers query everything with zero setup | DuckDB (better analytically, but an extra dependency) |
