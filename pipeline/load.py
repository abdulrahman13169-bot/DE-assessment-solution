"""Writers: Parquet (Databricks/Delta-ready), CSV, and a single SQLite database."""
from __future__ import annotations

import os
import sqlite3

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def _is_date_col(name: str, s: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(s)


def _to_arrow(df: pd.DataFrame) -> pa.Table:
    """Datetime columns -> DATE (no fake midnight timestamps / timezone surprises in Spark)."""
    df = df.copy()
    for c in df.columns:
        if _is_date_col(c, df[c]):
            df[c] = df[c].dt.date.astype(object).where(df[c].notna(), None)
        elif df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
            df[c] = df[c].astype(object).where(df[c].notna(), None)
    table = pa.Table.from_pandas(df, preserve_index=False)
    # all-NULL columns infer as Arrow `null`, which Delta/Spark reject (NullType) -> STRING
    schema = pa.schema([pa.field(f.name, pa.string()) if pa.types.is_null(f.type) else f
                        for f in table.schema]).remove_metadata()
    return table.cast(schema)


def _plain(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if _is_date_col(c, df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df


def write_all(layers: dict[str, dict[str, pd.DataFrame]], out_dir: str, formats=("parquet", "csv", "sqlite")):
    """layers = {'silver': {...}, 'gold': {...}}. Table names are prefixed by layer in SQLite."""
    os.makedirs(out_dir, exist_ok=True)
    written = {}
    con = None
    if "sqlite" in formats:
        db = os.path.join(out_dir, "ccda_claims.db")
        if os.path.exists(db):
            os.remove(db)
        con = sqlite3.connect(db)
    for layer, tables in layers.items():
        for name, df in tables.items():
            if "parquet" in formats:
                d = os.path.join(out_dir, "parquet", layer)
                os.makedirs(d, exist_ok=True)
                pq.write_table(_to_arrow(df), os.path.join(d, f"{name}.parquet"), compression="snappy")
            if "csv" in formats:
                d = os.path.join(out_dir, "csv", layer)
                os.makedirs(d, exist_ok=True)
                _plain(df).to_csv(os.path.join(d, f"{name}.csv"), index=False)
            if con is not None:
                p = _plain(df)
                for c in p.columns:
                    if p[c].dtype == bool:
                        p[c] = p[c].astype(int)
                p.to_sql(f"{layer}_{name}", con, index=False)
            written[f"{layer}.{name}"] = len(df)
    if con is not None:
        con.commit()
        con.close()
    return written
