"""Unit + end-to-end tests. Run: `python -m pytest -q`. Uses only synthetic fixtures."""
import json
import os
import sqlite3
from datetime import date

import pandas as pd
import pytest

from pipeline import ccda_ingest, claims, combine, hl7, terminology
from pipeline.ccda_parser import ids_from_filename, parse_document
from pipeline.cli import main
from pipeline.normalize import icd10, ndc11

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
DOC1 = os.path.join(FIX, "ccda", "PAT001_doc0001_masked.xml")


# ----------------------------------------------------------------- normalisers
@pytest.mark.parametrize("raw,expected", [
    ("20230615103000-0500", date(2023, 6, 15)),
    ("20230615", date(2023, 6, 15)),
    ("202306", date(2023, 6, 1)),
    ("2023", date(2023, 1, 1)),
    ("2022-03-21 08:26:00", date(2022, 3, 21)),
    ("20231340", None), ("", None), (None, None), ("abc", None),
])
def test_parse_ts(raw, expected):
    assert hl7.parse_ts(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("0071-0155-23", "00071015523"),    # 4-4-2
    ("63304-505-01", "63304050501"),    # 5-3-2
    ("12345-6789-1", "12345678901"),    # 5-4-1
    ("68180035205", "68180035205"),     # already 11
    ("555904958", "00555904958"),       # Excel dropped 2 leading zeros
    ("3786492001", None),               # 10 digits, no hyphens -> ambiguous
    ("99073-70805", None), (None, None), (float("nan"), None),
])
def test_ndc11(raw, expected):
    assert ndc11(raw) == expected


def test_icd10():
    assert icd10("e11.65") == "E1165"
    assert icd10(" Z3A.39 ") == "Z3A39"
    assert icd10("250.00") is None and icd10(None) is None


def test_ids_from_filename():
    assert ids_from_filename("/x/005ao54m-c566_055d57c0_masked.xml") == ("005ao54m-c566", "055d57c0")


# ----------------------------------------------------------------- C-CDA parser
@pytest.fixture(scope="module")
def doc1():
    return parse_document(DOC1)


def test_document_header(doc1):
    d = doc1["document"]
    assert d["parse_status"] == "ok"
    assert (d["patient_id"], d["document_id"]) == ("PAT001", "doc0001")
    assert d["document_date"] == date(2023, 6, 15)
    assert d["gender"] == "F" and d["birth_date"] == date(1970, 1, 2)
    assert d["source_system"] == "TestEHR"
    assert d["medication_entry_count"] == 3 and d["problem_entry_count"] == 3


def test_medication_coded(doc1):
    m = doc1["medications"][0]
    assert m["rxnorm_code"] == "617310"
    assert m["ndc11"] == "00071015523"
    assert m["drug_name"] == "atorvastatin 20 MG Oral Tablet"
    assert m["drug_original_text"] == "Atorvastatin 20 mg tablet"   # resolved #reference
    assert m["sig_text"] == "Take 1 tablet nightly"
    assert m["status_code"] == "active"                          # lower-cased
    assert m["start_date"] == date(2022, 1, 10) and m["end_date"] is None
    assert m["frequency"] == "every 24 h" and m["route"] == "Oral"
    assert not m["is_placeholder"]


def test_medication_name_from_narrative_only(doc1):
    m = doc1["medications"][1]
    assert m["rxnorm_code"] is None and m["drug_code_null_flavor"] == "OTH"
    assert m["drug_name"] == "Ergocalciferol 50,000 unit capsule"
    assert m["drug_name_source"] == "original_text"
    assert m["start_date"] == date(2021, 3, 21) and m["end_date"] == date(2021, 6, 1)
    assert m["translations_json"] is None                         # UNK translation dropped


def test_placeholders_flagged(doc1):
    assert doc1["medications"][2]["is_placeholder"]
    assert doc1["problems"][2]["is_placeholder"]


def test_problem_with_icd10_and_status(doc1):
    p = doc1["problems"][0]
    assert p["snomed_code"] == "44054006" and p["icd10_code"] == "E119"
    assert p["problem_status"] == "Active"                        # SNOMED code -> display
    assert p["concern_status"] == "active" and p["onset_date"] == date(2020, 3, 1)


def test_problem_name_from_reference(doc1):
    p = doc1["problems"][1]
    assert p["problem_name"] == "Essential hypertension" and p["icd10_code"] is None
    assert p["onset_date"] == date(2015, 1, 1) and p["abatement_date"] == date(2019, 1, 1)
    assert p["problem_type"] == "Disease"


def test_malformed_xml_is_quarantined(tmp_path):
    bad = tmp_path / "PATX_docX_masked.xml"
    bad.write_text("<ClinicalDocument><unclosed>")
    r = parse_document(str(bad))
    assert r["document"]["parse_status"] == "failed" and r["medications"] == []


# ----------------------------------------------------------------- claims + gold
@pytest.fixture(scope="module")
def built():
    silver = ccda_ingest.parse_all(ccda_ingest.discover(os.path.join(FIX, "ccda")), workers=1)
    silver["claims_dx_lines"], silver["claims_dx_diagnoses"] = claims.load_dx_claims(
        os.path.join(FIX, "claims", "dx_claims.csv"))
    silver["claims_rx"] = claims.load_rx_claims(os.path.join(FIX, "claims", "rx_claims.csv"))
    return combine.build_gold(silver)


def test_dx_unpivot(built):
    silver, _ = built
    dxl = silver["claims_dx_diagnoses"]
    assert len(dxl) == 4
    assert set(dxl["icd10_code"]) == {"E119", "I10", "E1165", "J189"}
    assert dxl.loc[dxl.icd10_code == "E119", "icd10_description"].iat[0].startswith("Type 2 diabetes")


def test_rx_ndc_padding(built):
    rx = built[0]["claims_rx"]
    assert rx.loc[rx.claim_id == "R1", "ndc11"].iat[0] == "00071015523"
    assert bool(rx.loc[rx.claim_id == "R1", "ndc_was_padded"].iat[0])


def test_medications_deduped_across_documents(built):
    pm = built[1]["patient_medications"]
    ator = pm[pm.med_key == "RXNORM:617310"].iloc[0]
    assert ator["n_documents"] == 2
    assert ator["latest_status"] == "completed"                    # from the 2024 document
    assert len(pm) == 2                                            # placeholder excluded


def test_learned_snomed_crosswalk_applied(built):
    probs = built[0]["ccda_problems"]
    later = probs[(probs.document_id == "doc0002")].iloc[0]
    assert later["icd10_code"] == "E119" and later["icd10_source"] == "learned_same_patient"


def test_snomed_crosswalk_votes_by_patient_not_by_entry():
    # one patient repeats E1159 in 10 documents; three patients say E119 -> E119 wins
    rows = [("P1", "73211009", "E1159")] * 10 + [("P2", "73211009", "E119"), ("P3", "73211009", "E119"),
                                                 ("P4", "73211009", "E119"),
                                                 ("P1", "38341003", "I10"), ("P2", "38341003", "I119")]
    x = combine.learn_snomed_icd10(pd.DataFrame(rows, columns=["patient_id", "snomed_code", "icd10_code"]))
    x = x.set_index("snomed_code")
    assert x.loc["73211009", "icd10_code"] == "E119"
    assert pd.isna(x.loc["38341003", "icd10_code"])          # 1 vs 1 patient -> ambiguous, unmapped


def test_rxnorm_learned_from_vendor_codes():
    multum = json.dumps([{"code": "40733", "system": "MULTUM_DRUG_SYNONYM_ID", "display": None}])
    meds = pd.DataFrame({"rxnorm_code": ["152695", None], "translations_json": [multum, multum]})
    out = combine.fill_rxnorm_from_translations(meds)
    assert out.loc[1, "rxnorm_code"] == "152695" and out.loc[1, "rxnorm_source"] == "learned_vendor_code"


def test_condition_concordance(built):
    cc = built[1]["condition_concordance"].set_index(["patient_id", "icd10_code"])
    assert cc.loc[("PAT001", "E119"), "match_level"] == "exact_code"
    assert cc.loc[("PAT001", "E1165"), "match_level"] == "same_category"
    assert cc.loc[("PAT002", "J189"), "match_level"] == "claims_only"


def test_medication_concordance_via_learned_ndc(built):
    mc = built[1]["medication_concordance"]
    row = mc[(mc.patient_id == "PAT001") & (mc.drug_key == "RXNORM:617310")].iloc[0]
    assert row["match_status"] == "both"


def test_rxnav_ingredient_matching(built, tmp_path):
    """Offline RxNav with a pre-seeded cache: NDC -> RxCUI -> ingredient."""
    b = terminology.BASE
    cache = {
        f"{b}/ndcstatus.json?ndc=68180035205": {"ndcStatus": {"rxcui": "617312", "conceptName": "atorvastatin 40 MG Oral Tablet"}},
        f"{b}/ndcstatus.json?ndc=00071015523": {"ndcStatus": {"rxcui": "617310", "conceptName": "atorvastatin 20 MG Oral Tablet"}},
        f"{b}/rxcui/617310/related.json?tty=IN": {"relatedGroup": {"conceptGroup": [{"tty": "IN", "conceptProperties": [{"name": "Atorvastatin"}]}]}},
        f"{b}/rxcui/617312/related.json?tty=IN": {"relatedGroup": {"conceptGroup": [{"tty": "IN", "conceptProperties": [{"name": "Atorvastatin"}]}]}},
    }
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(cache))
    client = terminology.RxNav(str(path), offline=True)
    silver = built[0]
    term = terminology.enrich(silver["claims_rx"]["ndc11"], silver["ccda_medications"]["rxnorm_code"], client)
    mr = combine.medication_records(silver["ccda_medications"], silver["claims_rx"],
                                    combine.learn_ndc_rxnorm(silver["ccda_medications"]), term)
    mc = combine.medication_concordance(mr)
    row = mc[mc.drug_key == "IN:atorvastatin"].iloc[0]
    assert row["match_status"] == "both" and row["rx_n_records"] == 2  # 20 mg AND 40 mg fills match


def test_end_to_end_cli(tmp_path):
    out = tmp_path / "out"
    rc = main(["--ccda-dir", os.path.join(FIX, "ccda"),
               "--dx-claims", os.path.join(FIX, "claims", "dx_claims.csv"),
               "--rx-claims", os.path.join(FIX, "claims", "rx_claims.csv"),
               "--out", str(out), "--workers", "1", "--strict"])
    assert rc == 0
    ps = pd.read_parquet(out / "parquet" / "gold" / "patient_summary.parquet")
    assert set(ps.patient_id) == {"PAT001", "PAT002"}
    assert isinstance(ps["first_document_date"].dropna().iat[0], date)    # DATE, not timestamp
    with sqlite3.connect(out / "ccda_claims.db") as con:
        n = con.execute("select count(*) from gold_condition_records").fetchone()[0]
    assert n > 0
    assert (out / "data_quality_report.md").exists()
