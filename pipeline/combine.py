"""Silver -> gold: deduplicate the clinical record and combine it with claims.

Gold tables
-----------
ref_ndc_rxnorm_learned     NDC -> RxNorm crosswalk *learned from the C-CDAs themselves*
ref_snomed_icd10_learned   SNOMED -> ICD-10 crosswalk learned from C-CDA translations
patient_medications        one row per patient x distinct medication (C-CDA, deduped across documents)
patient_problems           one row per patient x distinct problem   (C-CDA, deduped across documents)
medication_records         C-CDA medication entries UNION ALL pharmacy fills (common schema)
condition_records          C-CDA problem entries  UNION ALL claim diagnoses (common schema)
condition_concordance      patient x ICD-10: documented in C-CDA? billed on claims? both?
medication_concordance     patient x drug: on the C-CDA med list? filled at pharmacy? both?
patient_summary            one row per patient: demographics, volumes, cost, concordance KPIs
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .normalize import drug_name_key, icd10_category, icd10_description, ndc_product


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _mode(s: pd.Series):
    s = s.dropna()
    return s.mode().iat[0] if len(s) else None


def _latest(df: pd.DataFrame, keys: list[str], order_col: str, cols: list[str]) -> pd.DataFrame:
    """Value of `cols` from the most recent document per key."""
    return (df.sort_values(order_col, na_position="first")
              .groupby(keys, as_index=False).last()[keys + cols])


# --------------------------------------------------------------------------- #
# learned crosswalks
# --------------------------------------------------------------------------- #
def learn_ndc_rxnorm(meds: pd.DataFrame) -> pd.DataFrame:
    m = meds.dropna(subset=["ndc11", "rxnorm_code"])
    if m.empty:
        return pd.DataFrame(columns=["ndc11", "ndc_product", "rxnorm_code", "drug_name", "n_observations"])
    g = m.groupby("ndc11").agg(rxnorm_code=("rxnorm_code", _mode), drug_name=("drug_name", _mode),
                               n_observations=("rxnorm_code", "size")).reset_index()
    g["ndc_product"] = g["ndc11"].map(ndc_product)
    return g[["ndc11", "ndc_product", "rxnorm_code", "drug_name", "n_observations"]]


def learn_snomed_icd10(probs: pd.DataFrame) -> pd.DataFrame:
    """Global SNOMED -> ICD-10 pairs observed in documents, voted by *distinct patients*.

    Counting patients (not entries) stops one patient with 40 copies of the same
    document from deciding the mapping. A code is only accepted when it wins a
    strict majority of patients; otherwise icd10_code is NULL (ambiguous) and the
    competing candidates are listed.
    """
    p = probs.dropna(subset=["snomed_code", "icd10_code"])
    cols = ["snomed_code", "icd10_code", "patient_share", "n_patients", "n_observations",
            "n_distinct_icd10", "candidates"]
    if p.empty:
        return pd.DataFrame(columns=cols)
    votes = (p.groupby(["snomed_code", "icd10_code"])
              .agg(n_pat=("patient_id", "nunique"), n_obs=("patient_id", "size")).reset_index()
              .sort_values(["snomed_code", "n_pat", "n_obs"], ascending=[True, False, False]))
    rows = []
    for sno, g in votes.groupby("snomed_code", sort=False):
        total = g["n_pat"].sum()
        top = g.iloc[0]
        unique_top = len(g) == 1 or g["n_pat"].iat[1] < top["n_pat"]
        share = top["n_pat"] / total
        rows.append({
            "snomed_code": sno,
            "icd10_code": top["icd10_code"] if unique_top and share > 0.5 else None,
            "patient_share": round(float(share), 2), "n_patients": int(total),
            "n_observations": int(g["n_obs"].sum()), "n_distinct_icd10": len(g),
            "candidates": "|".join(f"{c}({n})" for c, n in zip(g["icd10_code"], g["n_pat"])),
        })
    return pd.DataFrame(rows, columns=cols)


# product-level drug vocabularies whose codes identify one RxNorm clinical/branded drug
DRUG_PRODUCT_SYSTEMS = {"NDC", "MULTUM_DRUG_SYNONYM_ID", "MEDISPAN_DDID", "MEDISPAN_GPI", "MEDISPAN_MEDFILE",
                        "FDB_NDDF", "FDB_MEDID"}


def _translation_pairs(meds: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for idx, tj in meds["translations_json"].dropna().items():
        for t in json.loads(tj):
            if t["system"] in DRUG_PRODUCT_SYSTEMS:
                rows.append((idx, t["system"], t["code"]))
    return pd.DataFrame(rows, columns=["idx", "system", "code"])


def fill_rxnorm_from_translations(meds: pd.DataFrame) -> pd.DataFrame:
    """Entries coded only in vendor vocabularies (e.g. Cerner Multum) inherit the RxNorm that
    other entries carrying the *same* vendor code were sent with (unambiguous pairs only)."""
    meds = meds.copy()
    meds["rxnorm_source"] = np.where(meds["rxnorm_code"].notna(), "document", None)
    tp = _translation_pairs(meds)
    if tp.empty:
        return meds
    tp["rxnorm"] = tp["idx"].map(meds["rxnorm_code"])
    known = tp.dropna(subset=["rxnorm"]).groupby(["system", "code"])["rxnorm"].agg(["nunique", "first"])
    known = known[known["nunique"] == 1]["first"]
    todo = tp[tp["rxnorm"].isna()].copy()
    todo["learned"] = [known.get((s_, c_)) for s_, c_ in zip(todo["system"], todo["code"])]
    hit = todo.dropna(subset=["learned"]).groupby("idx")["learned"].first()
    meds.loc[hit.index, "rxnorm_code"] = hit
    meds.loc[hit.index, "rxnorm_source"] = "learned_vendor_code"
    return meds


# --------------------------------------------------------------------------- #
# C-CDA enrichment & dedup
# --------------------------------------------------------------------------- #
def enrich_ccda(meds: pd.DataFrame, probs: pd.DataFrame, docs: pd.DataFrame,
                ndc_x: pd.DataFrame, sno_x: pd.DataFrame):
    #sys_map = docs.set_index("document_id")["source_system"]
    sys_map = docs.drop_duplicates("document_id").set_index("document_id")["source_system"]

    meds = meds.copy()
    meds["source_system"] = meds["document_id"].map(sys_map)
    meds["ndc_product"] = meds["ndc11"].map(ndc_product)
    meds["drug_name_key"] = meds["drug_name"].map(drug_name_key)
    meds["med_key"] = np.where(meds["rxnorm_code"].notna(), "RXNORM:" + meds["rxnorm_code"].astype(str),
                      np.where(meds["ndc11"].notna(), "NDC:" + meds["ndc11"].astype(str),
                      np.where(meds["drug_name_key"].notna(), "NAME:" + meds["drug_name_key"].astype(str), None)))
    meds["is_usable"] = ~meds["is_placeholder"] & ~meds["negated"] & meds["med_key"].notna()

    probs = probs.copy()
    probs["source_system"] = probs["document_id"].map(sys_map)
    probs["icd10_source"] = np.where(probs["icd10_code"].notna(), "document", None)
    # (a) the same patient's other documents translated this SNOMED code -> reuse it
    same = (probs.dropna(subset=["snomed_code", "icd10_code"])
                 .groupby(["patient_id", "snomed_code"])["icd10_code"].agg(_mode))
    keys = list(zip(probs["patient_id"], probs["snomed_code"]))
    cand = pd.Series([same.get(k) for k in keys], index=probs.index)
    fill = probs["icd10_code"].isna() & cand.notna()
    probs.loc[fill, "icd10_code"] = cand[fill]
    probs.loc[fill, "icd10_source"] = "learned_same_patient"
    # (b) otherwise the cross-patient majority mapping, if unambiguous
    learned = (sno_x.dropna(subset=["icd10_code"]).set_index("snomed_code")["icd10_code"]
               if len(sno_x) else pd.Series(dtype=object))
    fill = probs["icd10_code"].isna() & probs["snomed_code"].isin(learned.index)
    probs.loc[fill, "icd10_code"] = probs.loc[fill, "snomed_code"].map(learned)
    probs.loc[fill, "icd10_source"] = "learned_crosswalk"
    probs["icd10_category"] = probs["icd10_code"].map(icd10_category)
    probs["problem_key"] = np.where(probs["snomed_code"].notna(), "SNOMED:" + probs["snomed_code"].astype(str),
                           np.where(probs["icd10_code"].notna(), "ICD10:" + probs["icd10_code"].astype(str),
                           np.where(probs["problem_name"].notna(),
                                    "NAME:" + probs["problem_name"].str.lower().str.strip(), None)))
    probs["is_usable"] = ~probs["is_placeholder"] & ~probs["negated"] & probs["problem_key"].notna()
    return meds, probs


def dedupe_medications(meds: pd.DataFrame) -> pd.DataFrame:
    m = meds[meds["is_usable"]]
    keys = ["patient_id", "med_key"]
    agg = m.groupby(keys).agg(
        rxnorm_code=("rxnorm_code", "first"),
        ndc11=("ndc11", "first"),
        first_start_date=("start_date", "min"),
        last_end_date=("end_date", "max"),
        n_documents=("document_id", "nunique"),
        n_entries=("document_id", "size"),
        first_seen_document_date=("document_date", "min"),
        last_seen_document_date=("document_date", "max"),
        source_systems=("source_system", lambda s: "|".join(sorted(set(s.dropna())))),
    ).reset_index()
    latest = _latest(m, keys, "document_date", ["drug_name", "status_code", "mood_code", "route",
                                                "frequency", "sig_text"])
    latest = latest.rename(columns={"status_code": "latest_status", "mood_code": "latest_mood_code"})
    out = agg.merge(latest, on=keys, how="left")
    return out[["patient_id", "med_key", "drug_name", "rxnorm_code", "ndc11", "latest_status",
                "latest_mood_code", "route", "frequency", "sig_text", "first_start_date", "last_end_date",
                "first_seen_document_date", "last_seen_document_date", "n_documents", "n_entries",
                "source_systems"]]


def dedupe_problems(probs: pd.DataFrame) -> pd.DataFrame:
    p = probs[probs["is_usable"]]
    keys = ["patient_id", "problem_key"]
    agg = p.groupby(keys).agg(
        snomed_code=("snomed_code", "first"),
        icd10_code=("icd10_code", _mode),
        icd10_source=("icd10_source", "first"),
        first_onset_date=("onset_date", "min"),
        last_abatement_date=("abatement_date", "max"),
        n_documents=("document_id", "nunique"),
        n_entries=("document_id", "size"),
        first_seen_document_date=("document_date", "min"),
        last_seen_document_date=("document_date", "max"),
        source_systems=("source_system", lambda s: "|".join(sorted(set(s.dropna())))),
    ).reset_index()
    latest = _latest(p, keys, "document_date", ["problem_name", "problem_status", "concern_status",
                                                "problem_type"])
    latest = latest.rename(columns={"problem_status": "latest_problem_status",
                                    "concern_status": "latest_concern_status"})
    out = agg.merge(latest, on=keys, how="left")
    out["icd10_category"] = out["icd10_code"].map(icd10_category)
    return out[["patient_id", "problem_key", "problem_name", "snomed_code", "icd10_code", "icd10_category",
                "icd10_source", "problem_type", "latest_problem_status", "latest_concern_status",
                "first_onset_date", "last_abatement_date", "first_seen_document_date",
                "last_seen_document_date", "n_documents", "n_entries", "source_systems"]]


# --------------------------------------------------------------------------- #
# unified record-level tables (clinical UNION claims)
# --------------------------------------------------------------------------- #
def medication_records(meds: pd.DataFrame, rx: pd.DataFrame, ndc_x: pd.DataFrame,
                       terminology: tuple[dict, dict] | None = None) -> pd.DataFrame:
    m = meds[meds["is_usable"]]
    ccda = pd.DataFrame({
        "patient_id": m["patient_id"], "source": "CCDA",
        "source_record_id": m["document_id"] + ":" + m["entry_index"].astype(str),
        "record_date": m["start_date"].fillna(m["document_date"]),
        "record_end_date": m["end_date"],
        "rxnorm_code": m["rxnorm_code"], "ndc11": m["ndc11"], "ndc_product": m["ndc_product"],
        "drug_name": m["drug_name"], "drug_identity_source": "document",
        "status": m["status_code"], "days_supply": np.nan, "paid_amount": np.nan,
        "source_system": m["source_system"],
    })
    by_ndc = ndc_x.set_index("ndc11")
    by_prod = ndc_x.dropna(subset=["ndc_product"]).groupby("ndc_product").agg(
        rxnorm_code=("rxnorm_code", _mode), drug_name=("drug_name", _mode))
    rxn = rx["ndc11"].map(by_ndc["rxnorm_code"]) if len(by_ndc) else pd.Series(None, index=rx.index)
    nm = rx["ndc11"].map(by_ndc["drug_name"]) if len(by_ndc) else pd.Series(None, index=rx.index)
    src = pd.Series(np.where(rxn.notna(), "learned_ndc11", None), index=rx.index, dtype=object)
    prod_hit = rxn.isna() & rx["ndc_product"].isin(by_prod.index)
    rxn = rxn.where(~prod_hit, rx["ndc_product"].map(by_prod["rxnorm_code"]))
    nm = nm.where(~prod_hit, rx["ndc_product"].map(by_prod["drug_name"]))
    src = src.where(~prod_hit, "learned_ndc_product")
    if terminology:  # RxNav (authoritative) beats the learned crosswalk
        ndc_map = terminology[0]
        api_rx = rx["ndc11"].map(lambda n: (ndc_map.get(n) or {}).get("rxcui"))
        api_nm = rx["ndc11"].map(lambda n: (ndc_map.get(n) or {}).get("name"))
        hit = api_rx.notna()
        rxn, nm = rxn.where(~hit, api_rx), nm.where(~hit, api_nm)
        src = src.where(~hit, "rxnav")
    claims = pd.DataFrame({
        "patient_id": rx["patient_id"], "source": "RX_CLAIM",
        "source_record_id": rx["claim_id"],
        "record_date": rx["fill_date"], "record_end_date": rx["supply_end_date"],
        "rxnorm_code": rxn, "ndc11": rx["ndc11"], "ndc_product": rx["ndc_product"],
        "drug_name": nm, "drug_identity_source": src.fillna("ndc_only"),
        "status": "dispensed", "days_supply": rx["days_supply"], "paid_amount": rx["paid"],
        "source_system": "pharmacy_claims",
    })
    out = pd.concat([ccda, claims], ignore_index=True)
    ing_map = terminology[1] if terminology else {}
    out["ingredient"] = out["rxnorm_code"].map(lambda c: ing_map.get(c) if isinstance(c, str) else None)
    return out.sort_values(["patient_id", "record_date", "source"]).reset_index(drop=True)


def condition_records(probs: pd.DataFrame, dx_long: pd.DataFrame) -> pd.DataFrame:
    p = probs[probs["is_usable"]]
    ccda = pd.DataFrame({
        "patient_id": p["patient_id"], "source": "CCDA",
        "source_record_id": p["document_id"] + ":" + p["entry_index"].astype(str),
        "record_date": p["onset_date"].fillna(p["concern_start_date"]).fillna(p["document_date"]),
        "icd10_code": p["icd10_code"], "icd10_category": p["icd10_category"],
        "icd10_source": p["icd10_source"], "snomed_code": p["snomed_code"],
        "description": p["problem_name"], "status": p["problem_status"],
        "diag_position": pd.NA, "source_system": p["source_system"],
    })
    claims = pd.DataFrame({
        "patient_id": dx_long["patient_id"], "source": "DX_CLAIM",
        "source_record_id": dx_long["claim_line_key"],
        "record_date": dx_long["service_date"],
        "icd10_code": dx_long["icd10_code"], "icd10_category": dx_long["icd10_category"],
        "icd10_source": "claim", "snomed_code": None,
        "description": dx_long["icd10_description"], "status": "billed",
        "diag_position": dx_long["diag_position"], "source_system": "medical_claims",
    })
    out = pd.concat([ccda, claims], ignore_index=True)
    out["diag_position"] = out["diag_position"].astype("Int64")
    return out.sort_values(["patient_id", "record_date", "source"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# concordance
# --------------------------------------------------------------------------- #
def condition_concordance(patient_problems: pd.DataFrame, dx_long: pd.DataFrame) -> pd.DataFrame:
    c = (patient_problems.dropna(subset=["icd10_code"])
         .groupby(["patient_id", "icd10_code"])
         .agg(ccda_problem_name=("problem_name", "first"),
              ccda_first_seen=("first_seen_document_date", "min"),
              ccda_n_documents=("n_documents", "max"),
              ccda_icd10_source=("icd10_source", "first"))
         .reset_index())
    d = (dx_long.dropna(subset=["icd10_code"])
         .groupby(["patient_id", "icd10_code"])
         .agg(claims_first_service=("service_date", "min"),
              claims_last_service=("service_date", "max"),
              claims_n_lines=("claim_line_key", "nunique"),
              claims_principal=("is_principal", "any"))
         .reset_index())
    out = c.merge(d, on=["patient_id", "icd10_code"], how="outer")
    out["in_ccda"] = out["ccda_first_seen"].notna() | out["ccda_problem_name"].notna()
    out["in_claims"] = out["claims_n_lines"].notna()
    out["icd10_category"] = out["icd10_code"].map(icd10_category)
    out["description"] = out["icd10_code"].map(icd10_description).fillna(out["ccda_problem_name"])
    cat_c = set(zip(c["patient_id"], c["icd10_code"].map(icd10_category)))
    cat_d = set(zip(d["patient_id"], d["icd10_code"].map(icd10_category)))
    pairs = list(zip(out["patient_id"], out["icd10_category"]))
    out["category_in_ccda"] = [p in cat_c for p in pairs]
    out["category_in_claims"] = [p in cat_d for p in pairs]
    out["match_level"] = np.select(
        [out["in_ccda"] & out["in_claims"],
         out["category_in_ccda"] & out["category_in_claims"]],
        ["exact_code", "same_category"], default=np.where(out["in_ccda"], "ccda_only", "claims_only"))
    out["claims_n_lines"] = out["claims_n_lines"].fillna(0).astype(int)
    cols = ["patient_id", "icd10_code", "icd10_category", "description", "in_ccda", "in_claims",
            "match_level", "category_in_ccda", "category_in_claims", "ccda_problem_name", "ccda_icd10_source", "ccda_first_seen", "ccda_n_documents",
            "claims_first_service", "claims_last_service", "claims_n_lines", "claims_principal"]
    return out[cols].sort_values(["patient_id", "icd10_code"]).reset_index(drop=True)


def medication_concordance(med_records: pd.DataFrame) -> pd.DataFrame:
    r = med_records.copy()
    r["drug_key"] = np.where(r["ingredient"].notna(), "IN:" + r["ingredient"].astype(str),
                    np.where(r["rxnorm_code"].notna(), "RXNORM:" + r["rxnorm_code"].astype(str),
                    np.where(r["ndc_product"].notna(), "NDCP:" + r["ndc_product"].astype(str),
                    np.where(r["drug_name"].notna(),
                             "NAME:" + r["drug_name"].map(drug_name_key).astype(str), None))))
    r = r.dropna(subset=["drug_key"])
    g = r.groupby(["patient_id", "drug_key", "source"]).agg(
        drug_name=("drug_name", _mode), first_date=("record_date", "min"),
        last_date=("record_date", "max"), n_records=("source_record_id", "size"),
        paid=("paid_amount", "sum")).reset_index()
    c = g[g["source"] == "CCDA"].drop(columns=["source", "paid"]).add_prefix("ccda_")
    x = g[g["source"] == "RX_CLAIM"].drop(columns=["source"]).add_prefix("rx_")
    c = c.rename(columns={"ccda_patient_id": "patient_id", "ccda_drug_key": "drug_key"})
    x = x.rename(columns={"rx_patient_id": "patient_id", "rx_drug_key": "drug_key"})
    out = c.merge(x, on=["patient_id", "drug_key"], how="outer")
    out["drug_name"] = out["ccda_drug_name"].fillna(out["rx_drug_name"])
    out["on_ccda_med_list"] = out["ccda_n_records"].notna()
    out["filled_at_pharmacy"] = out["rx_n_records"].notna()
    out["match_status"] = np.select(
        [out["on_ccda_med_list"] & out["filled_at_pharmacy"], out["on_ccda_med_list"]],
        ["both", "ccda_only"], default="rx_claims_only")
    for col in ("ccda_n_records", "rx_n_records"):
        out[col] = out[col].fillna(0).astype(int)
    cols = ["patient_id", "drug_key", "drug_name", "on_ccda_med_list", "filled_at_pharmacy", "match_status",
            "ccda_first_date", "ccda_last_date", "ccda_n_records", "rx_first_date", "rx_last_date",
            "rx_n_records", "rx_paid"]
    return out[cols].sort_values(["patient_id", "drug_key"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# patient summary
# --------------------------------------------------------------------------- #
def patient_summary(docs, patient_meds, patient_probs, dx_lines, dx_long, rx, cond_conc, med_conc):
    ok = docs[docs["parse_status"] == "ok"]
    patients = pd.Index(sorted(set(docs["patient_id"]) | set(dx_lines["patient_id"]) | set(rx["patient_id"])),
                        name="patient_id")
    s = pd.DataFrame(index=patients)
    s["gender"] = ok.groupby("patient_id")["gender"].agg(_mode)
    s["birth_date"] = ok.groupby("patient_id")["birth_date"].agg(_mode)
    s["n_distinct_birth_dates"] = ok.groupby("patient_id")["birth_date"].nunique()
    s["n_ccda_documents"] = docs.groupby("patient_id").size()
    s["first_document_date"] = ok.groupby("patient_id")["document_date"].min()
    s["last_document_date"] = ok.groupby("patient_id")["document_date"].max()
    s["n_source_systems"] = ok.groupby("patient_id")["source_system"].nunique()
    s["n_ccda_medications"] = patient_meds.groupby("patient_id").size()
    s["n_ccda_problems"] = patient_probs.groupby("patient_id").size()
    s["n_dx_claim_lines"] = dx_lines.groupby("patient_id").size()
    s["n_distinct_dx_codes"] = dx_long.groupby("patient_id")["icd10_code"].nunique()
    s["medical_paid"] = dx_lines.groupby("patient_id")["paid"].sum()
    s["first_medical_service"] = dx_lines.groupby("patient_id")["from_date"].min()
    s["last_medical_service"] = dx_lines.groupby("patient_id")["from_date"].max()
    s["n_rx_fills"] = rx.groupby("patient_id").size()
    s["n_distinct_ndcs"] = rx.groupby("patient_id")["ndc11"].nunique()
    s["rx_paid"] = rx.groupby("patient_id")["paid"].sum()
    cc = cond_conc.assign(
        both=cond_conc["in_claims"] & cond_conc["in_ccda"],
        claim_cat_doc=cond_conc["in_claims"] & cond_conc["category_in_ccda"]).groupby("patient_id")
    s["dx_codes_in_both"] = cc["both"].sum()
    n_claim_codes = cc["in_claims"].sum().replace(0, np.nan)
    s["claims_dx_codes_documented_pct"] = (100 * cc["both"].sum() / n_claim_codes).round(1)
    s["claims_dx_categories_documented_pct"] = (100 * cc["claim_cat_doc"].sum() / n_claim_codes).round(1)
    ms = med_conc["match_status"]
    s["drugs_on_list_and_filled"] = (ms == "both").groupby(med_conc["patient_id"]).sum()
    s["drugs_filled_not_on_list"] = (ms == "rx_claims_only").groupby(med_conc["patient_id"]).sum()
    # do the clinical documents and the claims even cover the same period?
    c_start = s["first_document_date"]
    c_end = s["last_document_date"]
    k_start = pd.concat([s["first_medical_service"], rx.groupby("patient_id")["fill_date"].min()], axis=1).min(axis=1)
    k_end = pd.concat([s["last_medical_service"], rx.groupby("patient_id")["fill_date"].max()], axis=1).max(axis=1)
    s["claims_period_start"], s["claims_period_end"] = k_start, k_end
    s["ccda_claims_overlap_days"] = ((pd.concat([c_end, k_end], axis=1).min(axis=1) -
                                      pd.concat([c_start, k_start], axis=1).max(axis=1)).dt.days
                                     .clip(lower=0).fillna(0).astype(int))
    s["has_ccda"] = s["n_ccda_documents"].fillna(0) > 0
    s["has_medical_claims"] = s["n_dx_claim_lines"].fillna(0) > 0
    s["has_rx_claims"] = s["n_rx_fills"].fillna(0) > 0
    for c in s.columns:
        if c.startswith("n_") or c.startswith("drugs_") or c == "dx_codes_in_both":
            s[c] = s[c].fillna(0).astype(int)
    for c in ("medical_paid", "rx_paid"):
        s[c] = s[c].fillna(0).round(2)
    return s.reset_index()


# --------------------------------------------------------------------------- #
def build_gold(silver: dict[str, pd.DataFrame], terminology: tuple[dict, dict] | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    """Returns (enriched silver tables, gold tables)."""
    docs, meds, probs = silver["ccda_documents"], silver["ccda_medications"], silver["ccda_problems"]
    dx_lines, dx_long, rx = silver["claims_dx_lines"], silver["claims_dx_diagnoses"], silver["claims_rx"]

    meds = fill_rxnorm_from_translations(meds)
    ndc_x = learn_ndc_rxnorm(meds)
    sno_x = learn_snomed_icd10(probs)
    meds, probs = enrich_ccda(meds, probs, docs, ndc_x, sno_x)

    pm = dedupe_medications(meds)
    pp = dedupe_problems(probs)
    mr = medication_records(meds, rx, ndc_x, terminology)
    cr = condition_records(probs, dx_long)
    cc = condition_concordance(pp, dx_long)
    mc = medication_concordance(mr)
    ps = patient_summary(docs, pm, pp, dx_lines, dx_long, rx, cc, mc)

    silver = {**silver, "ccda_medications": meds, "ccda_problems": probs}
    gold = {
        "patient_summary": ps,
        "patient_medications": pm,
        "patient_problems": pp,
        "medication_records": mr,
        "condition_records": cr,
        "condition_concordance": cc,
        "medication_concordance": mc,
        "ref_ndc_rxnorm_learned": ndc_x,
        "ref_snomed_icd10_learned": sno_x,
    }
    return silver, gold
