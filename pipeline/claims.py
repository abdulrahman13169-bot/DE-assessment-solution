"""Bronze -> silver for the medical (Dx) and pharmacy (Rx) claims extracts."""
from __future__ import annotations

import logging

import pandas as pd

from .normalize import icd10, icd10_category, icd10_description, ndc11, ndc_product

log = logging.getLogger(__name__)

DATE_FMT = "%m/%d/%Y"
N_DIAG = 30


def _dates(df: pd.DataFrame, cols) -> None:
    for c in cols:
        df[c] = pd.to_datetime(df[c], format=DATE_FMT, errors="coerce")


def _num(df: pd.DataFrame, cols) -> None:
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")


def load_dx_claims(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (claim lines, diagnoses-long).

    Claim lines keep the clinically/financially useful columns (not all ~200
    actuarial grouper columns, which stay available in bronze). Diagnoses are
    unpivoted from ICDDiag1..30 to one row per (claim line, position) so they
    can be joined/unioned with C-CDA problems.
    """
    raw = pd.read_csv(path, dtype=str, keep_default_na=True)
    lines = pd.DataFrame({
        "patient_id": raw["MemberID"],
        "claim_id": raw["ClaimID"],
        "line_num": pd.to_numeric(raw["LineNum"], errors="coerce").astype("Int64"),
        "claim_line_key": raw["ClaimID"] + "|" + raw["LineNum"],
        "from_date": raw["FromDate"], "to_date": raw["ToDate"],
        "admit_date": raw["AdmitDate"], "discharge_date": raw["DischDate"],
        "paid_date": raw["PaidDate"],
        "service_type": raw["ServiceType"],          # IP / OP / PO (professional) / PA
        "mr_line": raw["MR_Line"],                   # Milliman HCG line
        "place_of_service": raw["POS"],
        "specialty": raw["Specialty"],
        "provider_id": raw["ProviderID"],
        "bill_type": raw["BillType"],
        "rev_code": raw["RevCode"],
        "hcpcs": raw["HCPCS"],
        "modifier": raw["Modifier"],
        "units": raw["Units"],
        "billed": raw["Billed"], "allowed": raw["Allowed"], "paid": raw["Paid"],
        "patient_pay": raw["PatientPay"],
        "icd_version": raw["ICDVersion"],
        "admit_diag": raw["AdmitDiag"].map(icd10),
        "principal_diag": raw["ICDDiag1"].map(icd10),
        "lob": raw["LOB"], "product": raw["Product"],
        "src_lob_raw": raw["srcLOB"],
        "claim_year": pd.to_numeric(raw["Year"], errors="coerce").astype("Int64"),
    })
    _dates(lines, ["from_date", "to_date", "admit_date", "discharge_date", "paid_date"])
    _num(lines, ["units", "billed", "allowed", "paid", "patient_pay"])

    diag_cols = [f"ICDDiag{i}" for i in range(1, N_DIAG + 1)]
    long = raw[["MemberID", "ClaimID", "LineNum", "FromDate", "ICDVersion"] + diag_cols].melt(
        id_vars=["MemberID", "ClaimID", "LineNum", "FromDate", "ICDVersion"],
        value_vars=diag_cols, var_name="diag_position", value_name="icd10_code_raw",
    ).dropna(subset=["icd10_code_raw"])
    long["diag_position"] = long["diag_position"].str.replace("ICDDiag", "").astype(int)
    long["icd10_code"] = long["icd10_code_raw"].map(icd10)
    long = long.rename(columns={"MemberID": "patient_id", "ClaimID": "claim_id",
                                "LineNum": "line_num", "FromDate": "service_date",
                                "ICDVersion": "icd_version"})
    long["line_num"] = pd.to_numeric(long["line_num"]).astype("Int64")
    long["claim_line_key"] = long["claim_id"] + "|" + long["line_num"].astype(str)
    long["is_principal"] = long["diag_position"] == 1
    long["icd10_category"] = long["icd10_code"].map(icd10_category)
    long["icd10_description"] = long["icd10_code"].map(icd10_description)
    _dates(long, ["service_date"])
    long = long.sort_values(["patient_id", "service_date", "claim_id", "line_num", "diag_position"])
    log.info("dx claims: %d lines, %d diagnosis rows", len(lines), len(long))
    return lines.reset_index(drop=True), long.reset_index(drop=True)


def load_rx_claims(path: str) -> pd.DataFrame:
    raw = pd.read_csv(path, dtype=str)
    rx = pd.DataFrame({
        "patient_id": raw["MemberID"],
        "claim_id": raw["ClaimID"],
        "fill_date": raw["FromDate"],
        "paid_date": raw["PaidDate"],
        "ndc_raw": raw["NDC"],
        "ndc11": raw["NDC"].map(ndc11),
        "ndc_was_padded": raw["NDC"].str.len() < 11,
        "days_supply": raw["DaysSupply"],
        "quantity_dispensed": raw["QuantityDispensed"],
        "mail_order": raw["MailOrder"],
        "generic_brand_line": raw["MR_Line_Det"],   # e.g. GEN-N-N
        "provider_id": raw["ProviderID"],
        "ingredient_cost": raw["IngredientCost"], "dispensing_fee": raw["DispensingFee"],
        "billed": raw["Billed"], "allowed": raw["Allowed"], "paid": raw["Paid"],
        "patient_pay": raw["PatientPay"],
        "lob": raw["LOB"], "product": raw["Product"], "src_lob_raw": raw["srcLOB"],
        "claim_year": pd.to_numeric(raw["Year"], errors="coerce").astype("Int64"),
    })
    rx["ndc_product"] = rx["ndc11"].map(ndc_product)
    _dates(rx, ["fill_date", "paid_date"])
    _num(rx, ["days_supply", "quantity_dispensed", "ingredient_cost", "dispensing_fee",
              "billed", "allowed", "paid", "patient_pay"])
    rx["supply_end_date"] = rx["fill_date"] + pd.to_timedelta(
        rx["days_supply"].fillna(1).clip(lower=1) - 1, unit="D")
    log.info("rx claims: %d fills (%d NDCs left-padded)", len(rx), int(rx["ndc_was_padded"].sum()))
    return rx.sort_values(["patient_id", "fill_date"]).reset_index(drop=True)
