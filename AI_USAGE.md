# Disclosure of AI usage

> **Candidate: review and edit this file before submitting.** It records how AI assistance was used, as the
> assessment requires. Add your own contributions, changes and review notes in the last section.

## Tool
* **Claude (Anthropic), model Claude Opus 5.5**, used as an agentic assistant with a sandboxed Linux workspace
  (Python, file editing, shell). The exam files were uploaded to the session: the exam brief, data overview,
  both claims CSVs and the repository zip containing `ccda_files/`.

## What the AI did
| Area | AI contribution |
|---|---|
| Data profiling | Wrote throw-away profiling scripts across all 569 C-CDAs (section codes/templateIds, EHR vendor, code systems, nullFlavors, translation systems) and the claims files (keys, date ranges, NDC lengths, column anomalies). The findings shaped the parser and the DQ checks |
| Pipeline code | Drafted all modules in `pipeline/`, `run_pipeline.py`, the Databricks notebooks and `scripts/` |
| Tests | Wrote the synthetic C-CDA and claims fixtures and `tests/test_pipeline.py` (37 tests) |
| Validation | Ran the pipeline end-to-end on the real data; re-ran the test suite on pandas 2.2 and 3.0; loaded every Parquet output into local Spark 3.5 and executed `02_review_queries.sql` against it. Delta itself and Databricks were **not** available in the sandbox, so the notebooks are untested on a live workspace. The `--rxnav` path was tested only against a mocked cache (NLM RxNav was not reachable from the sandbox) |
| Documentation | Drafted the README and everything in `docs/` |

Artifacts: [`ai_artifacts/session_log.md`](ai_artifacts/session_log.md) summarises the prompts and the main
steps and decisions in the session.

## Candidate review / own contributions
<!-- Fill in, e.g.:
* Reviewed every module line by line; changed …
* Re-ran on my machine (OS / Python version): …
* Ran the Databricks notebooks on workspace …: results …
* Decisions I would make differently / questions I'd raise with the data provider: …
-->
