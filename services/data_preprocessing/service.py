"""
Data Preprocessing Service (FastAPI)

- Cleans, encodes, and (optionally) scales a CSV stored under RAW_DIR.
- Persists artifacts (encoders, scaler, metadata) for reproducible training & inference.

API (contract stays UI-friendly):
  GET  /healthz
  POST /preprocess
      body: {
        "input_path": "<abs path under RAW_DIR>",
        "drop_duplicates": true,
        "impute_numeric": false,
        "impute_categorical": false,
        "encode_target": true,
        "encode_categorical": "mixed",     # "none" | "label_all" | "onehot_all" | "mixed"
        "scale_numeric": "robust",         # "none" | "zscore" | "robust"
        "onehot_cols": ["Urine Test"],     # list of cols to one-hot (if present)
        "ordinal_maps": {                  # optional custom orders
          "Physical Activity": ["Low","Moderate","High"],
          "Socioeconomic Factors": ["Low","Medium","High"],
          "Alcohol Consumption": ["Low","Moderate","High"]
        },
        "persist_artifacts": true,
        "target_column": "Target",
        "save_as": null
      }
  GET  /preprocess/{job_id}

Environment:
  RAW_DIR   (default "/shared/data/raw")
  CLEAN_DIR (default "/shared/data/clean")
  MODEL_DIR (default "/shared/models")  -> artifacts are saved to MODEL_DIR/artifacts
"""

from __future__ import annotations

import os
import json
import uuid
import traceback
from typing import Dict, Any, Optional, List, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

# ML utils
from sklearn.preprocessing import LabelEncoder, StandardScaler, RobustScaler
import joblib

RAW_DIR   = os.getenv("RAW_DIR", "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR = os.getenv("MODEL_DIR", "/shared/models")
ARTIFACT_DIR = os.path.join(MODEL_DIR, "artifacts")

app = FastAPI(title="Data Preprocessing Service", version="2.0.0")

# In-memory job store (sufficient for demo)
JOBS: Dict[str, Dict[str, Any]] = {}


# --------------------- Pydantic models ---------------------
class PreprocessRequest(BaseModel):
    input_path: str = Field(..., description="Absolute path to raw CSV within RAW_DIR")
    drop_duplicates: bool = True
    impute_numeric: bool = False
    impute_categorical: bool = False

    encode_target: bool = True
    encode_categorical: Literal["none", "label_all", "onehot_all", "mixed"] = "mixed"
    scale_numeric: Literal["none", "zscore", "robust"] = "robust"

    onehot_cols: List[str] = Field(default_factory=lambda: ["Urine Test"])
    ordinal_maps: Dict[str, List[str]] = Field(
        default_factory=lambda: {
            "Physical Activity": ["Low", "Moderate", "High"],
            "Socioeconomic Factors": ["Low", "Medium", "High"],
            "Alcohol Consumption": ["Low", "Moderate", "High"],
        }
    )

    persist_artifacts: bool = True
    target_column: Optional[str] = "Target"
    save_as: Optional[str] = None


class PreprocessStatus(BaseModel):
    job_id: Optional[str] = None
    state: str
    output_path: Optional[str] = None
    report: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    trace: Optional[str] = None


# --------------------- Helpers ---------------------
def _is_subpath(path: str, parent: str) -> bool:
    try:
        parent_real = os.path.realpath(parent)
        path_real = os.path.realpath(path)
        return path_real.startswith(parent_real + os.sep)
    except Exception:
        return False


def _safe_join(base: str, *paths: str) -> str:
    joined = os.path.realpath(os.path.join(base, *paths))
    if not _is_subpath(joined, base):
        raise ValueError("Unsafe path traversal detected")
    return joined


def _default_target(df: pd.DataFrame, explicit: Optional[str]) -> Optional[str]:
    # Try explicit first (case-sensitive)
    if explicit and explicit in df.columns:
        return explicit
    # Try case-insensitive match
    if explicit:
        for c in df.columns:
            if str(c).lower() == str(explicit).lower():
                return c
    # Heuristics: common names
    for cand in ["target", "label", "y"]:
        for c in df.columns:
            if str(c).strip().lower() == cand:
                return c
    return None


def _normalize_strings(series: pd.Series) -> pd.Series:
    # Strip whitespace & standardize capitalization of common tokens
    if series.dtype != object:
        return series
    def norm(v):
        if pd.isna(v):
            return v
        s = str(v).strip()
        # canonical title-casing where appropriate
        s_low = s.lower()
        # Normalizations for common binary/ordinal values
        mapping = {
            "yes": "Yes", "no": "No",
            "positive": "Positive", "negative": "Negative",
            "present": "Present", "absent": "Absent",
            "normal": "Normal", "abnormal": "Abnormal",
            "low": "Low", "medium": "Medium", "moderate": "Moderate", "high": "High",
            "smoker": "Smoker", "non-smoker": "Non-Smoker",
            "low risk": "Low Risk", "high risk": "High Risk",
            "glucose present": "Glucose Present",
            "protein present": "Protein Present",
            "ketones present": "Ketones Present",
        }
        return mapping.get(s_low, s)
    return series.map(norm)


def _detect_numeric_and_categorical(df: pd.DataFrame, exclude: List[str]) -> (List[str], List[str]):
    num_cols = [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])]
    cat_cols = [c for c in df.columns if c not in exclude and c not in num_cols]
    return num_cols, cat_cols


def _binary_map(values: List[Any]) -> Dict[str, int]:
    """
    Build a consistent 0/1 mapping for binary string values.
    Tries to choose the semantically 'positive' as 1 where obvious.
    """
    # Normalize to strings for mapping keys
    vals = [str(v) for v in values if pd.notna(v)]
    if len(set(vals)) != 2:
        raise ValueError("Not binary")
    a, b = sorted(set(vals))
    # Heuristic positives
    positive_tokens = {"yes", "positive", "present", "abnormal", "smoker", "high risk"}
    if a.lower() in positive_tokens and b.lower() not in positive_tokens:
        return {a: 1, b: 0}
    if b.lower() in positive_tokens and a.lower() not in positive_tokens:
        return {a: 0, b: 1}
    # Fallback: alphabetical -> {first:0, second:1}
    return {a: 0, b: 1}


def _apply_encoding_and_scaling(
    df: pd.DataFrame,
    target_col: Optional[str],
    req: PreprocessRequest
) -> Dict[str, Any]:
    """
    Returns dict with:
      df: processed dataframe
      encoders: JSON-serializable spec for categorical encodings
      scaler_info: dict describing scaler used
      final_columns: list of column names after transforms
    """
    work = df.copy()

    # 1) Normalize string columns for consistency
    for c in work.columns:
        work[c] = _normalize_strings(work[c])

    # 2) Drop duplicates if requested
    if req.drop_duplicates:
        work = work.drop_duplicates()

    # 3) Identify target & split
    target_col = _default_target(work, req.target_column)
    features = work.drop(columns=[target_col]) if (target_col and target_col in work.columns) else work

    # 4) Type-based splits
    num_cols, cat_cols = _detect_numeric_and_categorical(features, exclude=[])

    # 5) Optional imputation (dataset has no missing, but keep knobs)
    if req.impute_numeric and num_cols:
        for c in num_cols:
            if features[c].isna().any():
                med = pd.to_numeric(features[c], errors="coerce").median()
                features[c] = pd.to_numeric(features[c], errors="coerce").fillna(med)
    if req.impute_categorical and cat_cols:
        for c in cat_cols:
            if features[c].isna().any():
                mode_val = features[c].mode(dropna=True)
                mode_val = mode_val.iloc[0] if not mode_val.empty else "Unknown"
                features[c] = features[c].fillna(mode_val)

    encoders: Dict[str, Any] = {"target": None, "binary": {}, "ordinal": {}, "onehot": {}, "label_features": {}}

    # 6) Categorical encoding
    if req.encode_categorical != "none" and cat_cols:
        # Mixed policy:
        #   - One-hot for nominated columns (if present)
        #   - Ordinal for columns listed in ordinal_maps
        #   - Binary (0/1) for 2-level categoricals
        #   - LabelEncoder for remaining categoricals (only if "mixed" or "label_all")
        remaining = set(cat_cols)

        # 6a) One-hot first
        if req.encode_categorical in ("onehot_all", "mixed"):
            for col in req.onehot_cols:
                if col in remaining:
                    cats = sorted([str(v) for v in features[col].dropna().unique().tolist()])
                    encoders["onehot"][col] = {"categories": cats}
                    dummies = pd.get_dummies(features[col], prefix=col, dtype="int64")
                    features = pd.concat([features.drop(columns=[col]), dummies], axis=1)
                    remaining.remove(col)

        # 6b) Ordinal for known ordered columns
        if req.encode_categorical in ("mixed",):
            for col, order in req.ordinal_maps.items():
                if col in remaining:
                    mapping = {v: i for i, v in enumerate(order)}
                    encoders["ordinal"][col] = {"order": order, "mapping": mapping}
                    features[col] = features[col].map(mapping).astype("Int64")  # nullable int
                    remaining.remove(col)

        # 6c) Binary (0/1) for 2-level columns
        if req.encode_categorical in ("mixed", "label_all", "onehot_all"):
            # In "onehot_all", we already dummied nominated onehot cols; treat others:
            #   - if exactly 2 categories -> 0/1 map (more compact than one-hot)
            #   - else:
            #       * onehot_all -> one-hot the rest
            #       * label_all -> label-encode
            #       * mixed -> label-encode remaining non-binary
            still = list(remaining)
            for col in still:
                uniq = [str(v) for v in features[col].dropna().unique().tolist()]
                nunique = len(set(uniq))
                if nunique == 2:
                    mapping = _binary_map(uniq)
                    encoders["binary"][col] = mapping
                    features[col] = features[col].map(mapping).astype("Int64")
                    remaining.remove(col)

        # 6d) Handle leftovers by policy
        if remaining:
            if req.encode_categorical == "onehot_all":
                for col in list(remaining):
                    dummies = pd.get_dummies(features[col], prefix=col, dtype="int64")
                    encoders["onehot"][col] = {"categories": sorted([str(v) for v in features[col].dropna().unique().tolist()])}
                    features = pd.concat([features.drop(columns=[col]), dummies], axis=1)
                    remaining.remove(col)
            else:
                # label_all or mixed -> LabelEncoder for each leftover
                for col in list(remaining):
                    le = LabelEncoder()
                    vals = features[col].astype(str).fillna("NaN")
                    fitted = le.fit(vals)
                    features[col] = pd.Series(fitted.transform(vals), index=features.index).astype("int64")
                    encoders["label_features"][col] = {"classes": [str(c) for c in le.classes_.tolist()]}
                    remaining.remove(col)

    # 7) Scaling numeric columns
    scaler_info: Dict[str, Any] = {"type": "none"}
    if req.scale_numeric in ("zscore", "robust") and num_cols:
        if req.scale_numeric == "zscore":
            scaler = StandardScaler(with_mean=True, with_std=True)
        else:
            scaler = RobustScaler(with_centering=True, with_scaling=True, quantile_range=(25.0, 75.0))
        features[num_cols] = scaler.fit_transform(features[num_cols])
        scaler_info = {"type": req.scale_numeric, "num_cols": num_cols}
    else:
        scaler = None

    # 8) Reattach target
    if target_col and target_col in work.columns:
        target_series = work[target_col]
        if req.encode_target:
            le_t = LabelEncoder()
            fitted_t = le_t.fit(target_series.astype(str))
            y_enc = fitted_t.transform(target_series.astype(str)).astype("int64")
            out_df = features.copy()
            out_df[target_col] = y_enc
            encoders["target"] = {
                "column": target_col,
                "classes": [str(c) for c in fitted_t.classes_.tolist()],
            }
        else:
            out_df = features.copy()
            out_df[target_col] = target_series
    else:
        out_df = features.copy()

    # 9) Final columns order & dtypes
    final_columns = out_df.columns.tolist()

    # 10) Return pieces (scaler object is not JSON-serializable; returned separately)
    return {
        "df": out_df,
        "encoders": encoders,
        "scaler_obj": scaler,
        "scaler_info": scaler_info,
        "final_columns": final_columns,
        "target_col": target_col,
        "num_cols": num_cols,
    }


def _save_artifacts(
    encoders: Dict[str, Any],
    scaler_obj,
    scaler_info: Dict[str, Any],
    final_columns: List[str],
    out_df: pd.DataFrame,
    target_col: Optional[str],
) -> Dict[str, str]:
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    paths = {}

    # encoders.json
    enc_path = os.path.join(ARTIFACT_DIR, "encoders.json")
    with open(enc_path, "w", encoding="utf-8") as f:
        json.dump(encoders, f, indent=2, ensure_ascii=False)
    paths["encoders"] = enc_path

    # scaler (if any)
    if scaler_obj is not None and scaler_info.get("type") in ("zscore", "robust"):
        sc_path = os.path.join(ARTIFACT_DIR, f"{scaler_info['type']}_scaler.pkl")
        joblib.dump(scaler_obj, sc_path)
        paths["scaler"] = sc_path

    # preprocess_meta.json (column order, dtypes)
    meta = {
        "final_columns": final_columns,
        "dtypes": {c: str(out_df[c].dtype) for c in final_columns},
        "target_column": target_col,
        "scaler": scaler_info,
    }
    meta_path = os.path.join(ARTIFACT_DIR, "preprocess_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    paths["meta"] = meta_path

    return paths


def _save_clean(df: pd.DataFrame, input_path: str, save_as: Optional[str]) -> str:
    os.makedirs(CLEAN_DIR, exist_ok=True)
    filename = save_as if save_as else f"{os.path.splitext(os.path.basename(input_path))[0]}_clean.csv"
    out_path = _safe_join(CLEAN_DIR, filename)
    df.to_csv(out_path, index=False)
    return out_path


# --------------------- Background job ---------------------
def _run_preprocess(job_id: str, req: PreprocessRequest) -> None:
    JOBS[job_id]["state"] = "running"
    try:
        # Validate path
        in_path = req.input_path
        if not os.path.isabs(in_path):
            raise ValueError("input_path must be absolute")
        if not _is_subpath(in_path, RAW_DIR):
            raise ValueError(f"input_path must be inside RAW_DIR: {RAW_DIR}")
        if not os.path.exists(in_path):
            raise FileNotFoundError(f"Input file not found: {in_path}")

        # Load
        df = pd.read_csv(in_path, low_memory=False)

        # Process
        res = _apply_encoding_and_scaling(df, req.target_column, req)
        out_df: pd.DataFrame = res["df"]
        encoders = res["encoders"]
        scaler = res["scaler_obj"]
        scaler_info = res["scaler_info"]
        final_columns = res["final_columns"]
        target_col = res["target_col"]
        num_cols = res["num_cols"]

        # Persist artifacts (optional)
        artifact_paths = {}
        if req.persist_artifacts:
            artifact_paths = _save_artifacts(
                encoders=encoders,
                scaler_obj=scaler,
                scaler_info=scaler_info,
                final_columns=final_columns,
                out_df=out_df,
                target_col=target_col,
            )

        # Save the cleaned CSV
        out_path = _save_clean(out_df, in_path, req.save_as)

        # Build report
        report: Dict[str, Any] = {
            "input_path": in_path,
            "output_path": out_path,
            "shape_before": [int(df.shape[0]), int(df.shape[1])],
            "shape_after": [int(out_df.shape[0]), int(out_df.shape[1])],
            "missing_before": int(df.isna().sum().sum()),
            "missing_after": int(out_df.isna().sum().sum()),
            "numeric_columns": num_cols,
            "scaler": scaler_info,
            "encoders_summary": {
                "target_encoded": encoders["target"] is not None,
                "binary_cols": list(encoders["binary"].keys()),
                "ordinal_cols": list(encoders["ordinal"].keys()),
                "onehot_cols": list(encoders["onehot"].keys()),
                "label_encoded_cols": list(encoders["label_features"].keys()),
            },
            "artifacts": artifact_paths,
            "final_columns": final_columns[:50] + (["..."] if len(final_columns) > 50 else []),
        }

        JOBS[job_id].update({
            "state": "succeeded",
            "output_path": out_path,
            "report": report,
        })

    except Exception as e:
        JOBS[job_id].update({
            "state": "failed",
            "error": str(e),
            "trace": traceback.format_exc(limit=8),
        })


# --------------------- Routes ---------------------
@app.get("/healthz")
def healthz():
    try:
        os.makedirs(RAW_DIR, exist_ok=True)
        os.makedirs(CLEAN_DIR, exist_ok=True)
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Shared dir issue: {e}")
    return {"status": "ok"}


@app.post("/preprocess", response_model=PreprocessStatus)
def start_preprocess(req: PreprocessRequest, background: BackgroundTasks):
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {
        "job_id": job_id,
        "state": "queued",
        "output_path": None,
        "report": None,
        "error": None,
        "trace": None,
    }
    background.add_task(_run_preprocess, job_id, req)
    return PreprocessStatus(job_id=job_id, state="queued")


@app.get("/preprocess/{job_id}", response_model=PreprocessStatus)
def get_preprocess(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id not found")
    return PreprocessStatus(**job)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("service:app", host="0.0.0.0", port=8000, reload=False)
