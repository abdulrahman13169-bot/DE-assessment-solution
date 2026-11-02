# Databricks notebook source
# MAGIC %md
# MAGIC # Load pipeline output into Delta tables
# MAGIC
# MAGIC **Option A — load the pre-built output (fastest):** upload the repo's `output/parquet/` folder to a
# MAGIC Unity Catalog Volume (Catalog Explorer → Volume → *Upload to this volume*), keeping the
# MAGIC `silver/` and `gold/` sub-folders, and set `source_path` below.
# MAGIC
# MAGIC **Option B — run the pipeline on Databricks:** clone the repo as a Git folder, upload `ccda_files/`
# MAGIC and the two claims CSVs to a Volume, and run `00_run_pipeline` first (it writes Parquet to the
# MAGIC Volume), then this notebook.
# MAGIC
# MAGIC All Parquet date columns are written as `DATE` (not timestamps), strings as `STRING`, so the schemas
# MAGIC land cleanly in Delta without casting.

# COMMAND ----------

dbutils.widgets.text("source_path", "/Volumes/main/ccda_assessment/raw/parquet", "Parquet folder")
dbutils.widgets.text("catalog", "main", "Catalog")
dbutils.widgets.text("schema", "ccda_assessment", "Schema")

source_path = dbutils.widgets.get("source_path").rstrip("/")
catalog, schema = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")

# COMMAND ----------

COMMENTS = {
    "silver_ccda_documents": "One row per C-CDA file: header metadata, demographics, parse status.",
    "silver_ccda_medications": "Every medication entry parsed from every C-CDA (lineage: document_id + entry_index).",
    "silver_ccda_problems": "Every problem observation parsed from every C-CDA.",
    "silver_claims_dx_lines": "Medical claim lines (conformed subset of columns).",
    "silver_claims_dx_diagnoses": "Medical claim diagnoses unpivoted from ICDDiag1..30.",
    "silver_claims_rx": "Pharmacy fills with NDC normalised to NDC11.",
    "gold_patient_summary": "One row per patient: demographics, volumes, cost, clinical/claims concordance.",
    "gold_patient_medications": "C-CDA medication list deduplicated across documents (patient x drug).",
    "gold_patient_problems": "C-CDA problem list deduplicated across documents (patient x problem).",
    "gold_medication_records": "C-CDA medication entries UNION ALL pharmacy fills in one schema.",
    "gold_condition_records": "C-CDA problems UNION ALL claim diagnoses in one schema.",
    "gold_condition_concordance": "Patient x ICD-10: in C-CDA, in claims, or both.",
    "gold_medication_concordance": "Patient x drug: on med list, filled at pharmacy, or both.",
    "gold_ref_ndc_rxnorm_learned": "NDC->RxNorm pairs observed inside the C-CDAs.",
    "gold_ref_snomed_icd10_learned": "SNOMED->ICD-10 pairs observed inside the C-CDAs.",
}

loaded = []
for layer in ("silver", "gold"):
    for f in dbutils.fs.ls(f"{source_path}/{layer}"):
        if not f.name.endswith(".parquet"):
            continue
        table = f"{layer}_{f.name[:-len('.parquet')]}"
        fq = f"{catalog}.{schema}.{table}"
        df = spark.read.parquet(f.path)
        (df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(fq))
        if table in COMMENTS:
            spark.sql(f"COMMENT ON TABLE {fq} IS '{COMMENTS[table]}'")
        loaded.append((fq, df.count()))

display(spark.createDataFrame(loaded, "table string, rows long"))

# COMMAND ----------

# MAGIC %md ## Primary keys (informational constraints in Unity Catalog)

# COMMAND ----------

PKS = {
    "silver_ccda_documents": ["patient_id", "document_id"],
    "silver_ccda_medications": ["document_id", "entry_index"],
    "silver_ccda_problems": ["document_id", "entry_index"],
    "silver_claims_dx_lines": ["claim_id", "line_num"],
    "silver_claims_rx": ["claim_id"],
    "gold_patient_summary": ["patient_id"],
    "gold_patient_medications": ["patient_id", "med_key"],
    "gold_patient_problems": ["patient_id", "problem_key"],
}
for t, cols in PKS.items():
    fq = f"{catalog}.{schema}.{t}"
    dupes = spark.table(fq).groupBy(*cols).count().filter("count > 1").count()
    print(f"{fq:<60} PK({', '.join(cols)}) duplicates={dupes}")
    if dupes == 0:
        for c in cols:
            spark.sql(f"ALTER TABLE {fq} ALTER COLUMN {c} SET NOT NULL")
        spark.sql(f"ALTER TABLE {fq} DROP CONSTRAINT IF EXISTS pk_{t}")
        spark.sql(f"ALTER TABLE {fq} ADD CONSTRAINT pk_{t} PRIMARY KEY ({', '.join(cols)}) NOT ENFORCED")
