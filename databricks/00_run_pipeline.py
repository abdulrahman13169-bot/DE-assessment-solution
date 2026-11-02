# Databricks notebook source
# MAGIC %md
# MAGIC # Run the pipeline on Databricks (Option B)
# MAGIC Run this notebook from the repo checked out as a **Git folder**. It runs the same Python package
# MAGIC on the driver (≈10 s for the 569 exam documents) and writes Parquet to a Volume for `01_load_to_delta`.
# MAGIC For production-scale volumes, see `docs/PIPELINE_DESIGN.md` → the parser is wrapped in
# MAGIC `mapInPandas` so parsing is distributed across executors.

# COMMAND ----------

# MAGIC %pip install -r ../requirements.txt

# COMMAND ----------

dbutils.widgets.text("input_volume", "/Volumes/main/ccda_assessment/raw", "Input Volume (ccda_files/ + CSVs)")
dbutils.widgets.text("output_path", "/Volumes/main/ccda_assessment/raw/parquet_run", "Output folder")
inp, out = dbutils.widgets.get("input_volume").rstrip("/"), dbutils.widgets.get("output_path").rstrip("/")

# COMMAND ----------

import os, sys
sys.path.insert(0, os.path.abspath(".."))          # repo root
from pipeline.cli import main

rc = main([
    "--ccda-dir", f"{inp}/ccda_files",
    "--dx-claims", f"{inp}/data_engineer_exam_claims_final.csv",
    "--rx-claims", f"{inp}/data_engineer_exam_rx_final.csv",
    "--out", "/tmp/ccda_out", "--formats", "parquet",
])
assert rc == 0, "pipeline failed - see log above"

# copy local driver output to the Volume (writing many small files directly to a Volume is slower)
dbutils.fs.cp("file:/tmp/ccda_out/parquet", f"{out}", recurse=True)
dbutils.fs.cp("file:/tmp/ccda_out/data_quality_report.md", f"{out}/data_quality_report.md")
print(open("/tmp/ccda_out/data_quality_report.md").read())
