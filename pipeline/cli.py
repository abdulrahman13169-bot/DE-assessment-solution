"""Command-line entry point: `python run_pipeline.py [options]`."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from . import ccda_ingest, claims, combine, load, quality, terminology

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_args(argv=None):
    p = argparse.ArgumentParser(description="C-CDA + claims integration pipeline")
    p.add_argument("--ccda-dir", default=os.path.join(HERE, "ccda_files"),
                   help="folder of C-CDA XML files (default: ./ccda_files)")
    p.add_argument("--ccda-url-csv", default=None,
                   help="optional ccda_pre_signed_urls.csv; files are downloaded into --ccda-dir first")
    p.add_argument("--dx-claims", default=os.path.join(HERE, "data_engineer_exam_claims_final.csv"))
    p.add_argument("--rx-claims", default=os.path.join(HERE, "data_engineer_exam_rx_final.csv"))
    p.add_argument("--out", default=os.path.join(HERE, "output"))
    p.add_argument("--formats", default="parquet,csv,sqlite",
                   help="comma list of parquet,csv,sqlite")
    p.add_argument("--workers", type=int, default=0, help="parser processes (0 = all CPUs, 1 = sequential)")
    p.add_argument("--rxnav", action="store_true",
                   help="enrich NDC->RxNorm->ingredient via NLM RxNav (needs internet; cached)")
    p.add_argument("--rxnav-offline", action="store_true",
                   help="use only reference/rxnav_cache.json (no network)")
    p.add_argument("--strict", action="store_true", help="exit 1 if any 'error' data-quality check fails")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = build_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s", datefmt="%H:%M:%S")
    log = logging.getLogger("pipeline")
    t0 = time.time()

    # 1. bronze -> silver: clinical
    if a.ccda_url_csv:
        n = ccda_ingest.download_from_presigned_csv(a.ccda_url_csv, a.ccda_dir)
        log.info("landed %d C-CDA files from pre-signed URLs", n)
    files = ccda_ingest.discover(a.ccda_dir)
    log.info("found %d C-CDA files in %s", len(files), a.ccda_dir)
    silver = ccda_ingest.parse_all(files, workers=a.workers)

    # 2. bronze -> silver: claims
    silver["claims_dx_lines"], silver["claims_dx_diagnoses"] = claims.load_dx_claims(a.dx_claims)
    silver["claims_rx"] = claims.load_rx_claims(a.rx_claims)

    # 3. silver -> gold
    term = None
    if a.rxnav or a.rxnav_offline:
        client = terminology.RxNav(os.path.join(HERE, "reference", "rxnav_cache.json"), offline=a.rxnav_offline)
        term = terminology.enrich(silver["claims_rx"]["ndc11"], silver["ccda_medications"]["rxnorm_code"], client)
    silver, gold = combine.build_gold(silver, term)

    # 4. quality + load
    checks = quality.run_checks(silver, gold)
    written = load.write_all({"silver": silver, "gold": gold}, a.out, tuple(a.formats.split(",")))
    meta = {"ccda_files": len(files), "seconds": round(time.time() - t0, 1), "row_counts": written,
            "inputs": {"ccda_dir": a.ccda_dir, "dx_claims": a.dx_claims, "rx_claims": a.rx_claims}}
    quality.write_report(checks, meta, a.out)

    failed = [c for c in checks if not c["passed"]]
    for c in failed:
        (log.error if c["severity"] == "error" else log.warning)("DQ %s: %s (%s)", c["check"], c["metric"],
                                                                  c["detail"])
    for k, v in written.items():
        log.info("wrote %-38s %8d rows", k, v)
    log.info("done in %.1fs -> %s", time.time() - t0, a.out)
    if a.strict and any(c["severity"] == "error" for c in failed):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
