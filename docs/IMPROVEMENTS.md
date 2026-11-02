# Further steps and improvements (exam step 6)

Ordered roughly by value to an underwriting / risk use case.

## 1. Terminology (biggest impact on combined-data quality)
* **Licensed / authoritative maps instead of learned ones.**
  * SNOMED CT → ICD-10-CM via the NLM *SNOMED CT to ICD-10-CM Map* (UMLS licence; rule-based, uses age/sex context).
  * NDC → RxNorm via RxNorm's monthly `RXNSAT` NDC table loaded as a Delta reference table, not live API calls.
    `terminology.py` already implements the RxNav API path with caching; the table approach removes the network dependency.
  * RxNorm → ingredient / ATC / therapeutic class (RxClass) for class-level matching ("any statin").
  * Proprietary IDs seen in the documents (Multum, Medi-Span GPI/DDID, FDB, IMO) are kept in
    `translations_json`, ready to map if a vendor crosswalk is licensed.
* **Clinical groupers** on both sides: CMS-HCC / Milliman groupers, AHRQ CCSR, or chronic-condition flags.
  Concordance at that level is what a risk model consumes.
* **Version-aware ICD-10-CM** validation: descriptions come from one bundled release; validate against the
  code set in force on the date of service.

## 2. More C-CDA domains
The parser framework (section classifier + coded-element helpers) makes these mostly mapping work:
allergies (`2.6.1`), results/labs (`2.3.1`, with LOINC + UCUM normalisation), vital signs (`2.4.1`),
encounters and encounter diagnoses (`2.22.1`), procedures (`2.7.1`), immunizations (`2.2.1`),
social history incl. smoking status (`2.17`), payers (`2.18`). Also **medication indications**
(`entryRelationship` → Indication `4.19`), which link a drug to the problem it treats.

## 3. Clinical logic
* **Medication episodes from fills:** build continuous therapy periods from `fill_date` + `days_supply` with
  a grace gap, and compute adherence (PDC). Then compare episodes with C-CDA start/stop dates rather than
  just matching drug presence.
* **Time-aware concordance:** only compare a claim diagnosis with problem lists from documents dated within
  ±N months, which avoids the coverage-window mismatch described in the findings.
* **Negation and uncertainty:** `negationInd` is honoured; "history of", "rule out" and "family history"
  problems (SNOMED situation/context concepts) should become separate flags.
* **Medication status inference:** `moodCode=INT` + `ordered` vs. `EVN` + `completed` vs. an explicit end date.
  Collapse these to `active / stopped / unknown` with documented rules.

## 4. Identity and document management
* **Master patient index:** cross-check `MemberID` against the C-CDA `recordTarget` identifiers and demographics,
  and probabilistically match on name/DOB/sex/ZIP. This is needed as soon as documents arrive without the ID in the file name.
* **Document versioning:** use `setId` + `versionNumber` to keep only the latest version of a re-issued document,
  and `relatedDocument` (RPLC/APND) to supersede earlier versions.
* **Source trust scoring:** weight facts by vendor and recency, e.g. prefer an entry with a structured code over a
  narrative-only one when they conflict.

## 5. Engineering
* **Spark-native distributed parse** (`binaryFile` + `mapInPandas`) and Auto Loader incremental ingestion,
  as described in `PIPELINE_DESIGN.md`. The parser is already a pure function per document, so it can run on executors as-is.
* **Write Delta directly** with `MERGE` for idempotent upserts, plus Change Data Feed so downstream CDM tables update incrementally.
* **Schema contracts:** pydantic/pandera schemas for each silver/gold table, enforced in CI and at runtime.
* **XSD + Schematron validation** of incoming C-CDAs (HL7 C-CDA R2.1 schematron) as an informational DQ dimension.
* **Observability:** row counts, DQ metrics and parse-time per vendor written to a metrics table with alerting.
  Track drift in `drug_name_source` / coverage per vendor, because a vendor upgrade usually shows up there first.
* **Packaging:** `pyproject.toml`, a pinned lock file, a Docker image, and Databricks Asset Bundles for deployment.
* **Performance at scale:** `lxml.iterparse` with element clearing for very large documents (the largest
  here is 12.5 MB, which is fine in memory); skip narrative indexing for sections that are not parsed.

## 6. Privacy / governance
* Tokenise `patient_id` at ingestion (salted hash), and keep the re-identification key in a restricted schema.
* Unity Catalog column masks on `birth_date`, `patient_state`; row filters by line of business.
* Retention policy for bronze XML; audit of who queried PHI tables.
* Free-text fields (`sig_text`, `entry_text`) can contain PHI and should be scanned (e.g. Presidio) before broad sharing.

## 7. NLP on narrative-only content
Some documents carry clinically relevant content only as narrative (assessment & plan, HPI, notes sections).
A clinical NER model (e.g. medSpaCy, AWS Comprehend Medical, or an LLM extraction step with
code validation) could add problems and medications that are missing from the structured entries. Each such fact
would be tagged with `source = 'NLP'` and a confidence score so it never gets mixed up with coded data.
