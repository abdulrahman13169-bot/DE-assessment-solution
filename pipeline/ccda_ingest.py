"""Bronze -> silver for C-CDAs: discover (or download) the XMLs and parse them in parallel."""
from __future__ import annotations

import glob
import logging
import os
import urllib.request
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from .ccda_parser import ids_from_filename, parse_document

log = logging.getLogger(__name__)


def download_from_presigned_csv(csv_path: str, dest_dir: str, url_column: str | None = None) -> int:
    """Optional landing step for the original `ccda_pre_signed_urls.csv` delivery.

    Pre-signed S3 URLs expire, so this is idempotent (skips files already landed)
    and logs-and-continues on individual failures rather than aborting the run.
    """
    os.makedirs(dest_dir, exist_ok=True)
    df = pd.read_csv(csv_path, dtype=str)
    col = url_column or next(c for c in df.columns if df[c].astype(str).str.startswith("http").any())
    landed = 0
    for url in df[col].dropna():
        name = os.path.basename(url.split("?")[0])
        target = os.path.join(dest_dir, name)
        if os.path.exists(target):
            continue
        try:
            urllib.request.urlretrieve(url, target)  # noqa: S310 (trusted, signed URL)
            landed += 1
        except Exception as exc:
            log.warning("download failed for %s: %s", name, exc)
    return landed


def discover(ccda_dir: str) -> list[str]:
    files = sorted(os.path.join(ccda_dir, f) for f in os.listdir(ccda_dir)
                   if f.lower().endswith(".xml") and os.path.isfile(os.path.join(ccda_dir, f)))
    if not files:
        raise FileNotFoundError(f"No C-CDA XML files found in {ccda_dir!r}")
    return files


def parse_all(files: list[str], workers: int = 0) -> dict[str, pd.DataFrame]:
    """Parse every file; workers=0 -> one per CPU, 1 -> sequential (easier debugging)."""
    workers = workers or os.cpu_count() or 1
    docs, meds, probs = [], [], []
    if workers == 1:
        results = map(parse_document, files)
    else:
        pool = ProcessPoolExecutor(max_workers=workers)
        results = pool.map(parse_document, files, chunksize=8)
    for r in results:
        docs.append(r["document"])
        meds.extend(r["medications"])
        probs.extend(r["problems"])
    if workers != 1:
        pool.shutdown()
    log.info("parsed %d documents -> %d medication entries, %d problem entries",
             len(docs), len(meds), len(probs))
    out = {
        "ccda_documents": pd.DataFrame(docs),
        "ccda_medications": pd.DataFrame(meds),
        "ccda_problems": pd.DataFrame(probs),
    }
    for df in out.values():
        for c in df.columns:
            if c.endswith("_date") or c == "author_time":
                df[c] = pd.to_datetime(df[c], errors="coerce")
    return out


__all__ = ["discover", "parse_all", "download_from_presigned_csv", "ids_from_filename"]
