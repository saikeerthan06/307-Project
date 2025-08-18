"""
Model Inference Service (FastAPI)

- Replays preprocessing using saved artifacts (encoders.json, preprocess_meta.json, scaler.pkl)
- Predicts with a trained .joblib model
- Accepts either:
    * mode="records": JSON list of feature dicts
    * mode="csv": path to a CLEANED CSV (output of preprocessing service)

Endpoints
---------
GET  /healthz
POST /predict
    {
      "model_path": "/shared/models/xgb_model_YYYYmmdd-HHMMSS.joblib",
      "artifacts_dir": "/shared/models/artifacts",
      "mode": "records",                # "records" or "csv"
      "records": [ { ... }, ... ],      # required if mode="records"
      "data_path": "/shared/data/clean/diabetes_dataset00_clean.csv",  # if mode="csv"
      "target_column": "Target",
      "records_are_raw": true,          # true -> apply encoders/scaler; false -> already cleaned/encoded
      "return_proba": true,
      "top_k": 3,
      "save_predictions": true,
      "save_as": null
    }
"""

from __future__ import annotations

import os
import json
import time
from typing import List, Dict, Any, Optional, Literal

import numpy as np
import pandas as pd
import joblib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

RAW_DIR   = os.getenv("RAW_DIR", "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR = os.getenv("MODEL_DIR", "/shared/models")
ARTIFACT_DIR = os.path.join(MODEL_DIR, "artifacts")
PRED_DIR = os.path.join(ARTIFACT_DIR, "predictions")

META_FILE = "preprocess_meta.json"
ENC_FILE  = "encoders.json"

app = FastAPI(title="Model Inference Service", version="1.0.0")


# ---------- Schemas ----------

class PredictRequest(BaseModel):
    model_path: str = Field(..., description="Absolute path to trained .joblib model")
    artifacts_dir: str = Field(default=ARTIFACT_DIR, description="Dir containing encoders.json, preprocess_meta.json, scaler")
    mode: Literal["records", "csv"] = "records"
    records: Optional[List[Dict[str, Any]]] = None
    data_path: Optional[str] = None
    target_column: Optional[str] = "Target"
    records_are_raw: bool = True       # if True, apply encoders/scaler to records
    return_proba: bool = True
    top_k: int = 3
    save_predictions: bool = True
    save_as: Optional[str] = None


class PredictResponse(BaseModel):
    n_rows: int
    sample: List[Dict[str, Any]]
    predictions_path: Optional[str] = None
    classes: Optional[List[str]] = None


# ---------- Utils ----------

def _load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _ensure_dirs() -> None:
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    os.makedirs(PRED_DIR, exist_ok=True)

def _auto_target(df: pd.DataFrame, explicit: Optional[str]) -> Optional[str]:
    if explicit and explicit in df.columns:
        return explicit
    if explicit:
        for c in df.columns:
            if c.lower() == str(explicit).lower():
                return c
    for cand in ["target", "label", "y"]:
        for c in df.columns:
            if str(c).strip().lower() == cand:
                return c
    return None

def _normalize_strings(series: pd.Series) -> pd.Series:
    if series.dtype != object:
        return series
    def norm(v):
        if pd.isna(v): return v
        s = str(v).strip()
        mapping = {
            "yes":"Yes","no":"No",
            "positive":"Positive","negative":"Negative",
            "present":"Present","absent":"Absent",
            "normal":"Normal","abnormal":"Abnormal",
            "low":"Low","medium":"Medium","moderate":"Moderate","high":"High",
            "smoker":"Smoker","non-smoker":"Non-Smoker",
            "low risk":"Low Risk","high risk":"High Risk",
            "glucose present":"Glucose Present",
            "protein present":"Protein Present",
            "ketones present":"Ketones Present",
        }
        return mapping.get(s.lower(), s)
    return series.map(norm)

def _apply_encoders_to_df(df: pd.DataFrame, enc: Dict[str, Any]) -> pd.DataFrame:
    """
    Reproduce preprocessing encodings on a raw records DataFrame.
    Binary -> 0/1, Ordinal -> integer, One-hot -> dummies, Label features -> label index.
    Unknown values are handled conservatively (map to NaN/0).
    """
    work = df.copy()
    for c in work.columns:
        work[c] = _normalize_strings(work[c])

    # Binary
    for col, mapping in enc.get("binary", {}).items():
        if col in work.columns:
            work[col] = work[col].map(lambda v: mapping.get(str(v), np.nan)).astype("float")

    # Ordinal
    for col, info in enc.get("ordinal", {}).items():
        if col in work.columns:
            mapping = info.get("mapping", {})
            work[col] = work[col].map(lambda v: mapping.get(v, np.nan)).astype("float")

    # One-hot
    for col, info in enc.get("onehot", {}).items():
        if col in work.columns:
            dummies = pd.get_dummies(work[col], prefix=col, dtype="int64")
            # Ensure ALL categories exist in columns (even if 0)
            for cat in info.get("categories", []):
                cname = f"{col}_{cat}"
                if cname not in dummies.columns:
                    dummies[cname] = 0
            # Keep only known categories to match training
            keep_cols = [f"{col}_{cat}" for cat in info.get("categories", [])]
            dummies = dummies[keep_cols]
            work = pd.concat([work.drop(columns=[col]), dummies], axis=1)

    # Label-encoded features
    for col, info in enc.get("label_features", {}).items():
        if col in work.columns:
            classes = [str(x) for x in info.get("classes", [])]
            index_map = {cls: i for i, cls in enumerate(classes)}
            work[col] = work[col].astype(str).map(lambda v: index_map.get(v, 0)).astype("int64")

    # Remaining non-numeric -> attempt numeric
    for c in work.columns:
        if work[c].dtype == object:
            work[c] = pd.to_numeric(work[c], errors="ignore")

    return work

def _align_features(df: pd.DataFrame, meta: Dict[str, Any], target_col: Optional[str]) -> (pd.DataFrame, List[str]):
    if not meta:
        # Fall back to "all columns except target"
        tgt = _auto_target(df, target_col)
        feats = [c for c in df.columns if c != tgt]
        return df[feats], feats
    final_cols = meta.get("final_columns", [])
    tgt = meta.get("target_column", None) or _auto_target(df, target_col)
    # features are meta columns except target
    feats = [c for c in final_cols if c != tgt and c in df.columns]
    # Add missing feature columns as zeros (for one-hot categories not present)
    for c in final_cols:
        if c != tgt and c not in df.columns:
            df[c] = 0
    # Drop extras
    keep = [c for c in final_cols if c != tgt]
    if keep:
        df = df[keep]
        feats = keep
    return df, feats

def _maybe_scale(df: pd.DataFrame, meta: Dict[str, Any], artifacts_dir: str) -> pd.DataFrame:
    scaler_info = meta.get("scaler", {}) or {}
    stype = scaler_info.get("type", "none")
    num_cols = scaler_info.get("num_cols", [])
    if stype in ("zscore", "robust") and num_cols:
        # Only scale if those columns exist (raw records case)
        sc_name = f"{stype}_scaler.pkl"
        sc_path = os.path.join(artifacts_dir, sc_name)
        if os.path.exists(sc_path):
            scaler = joblib.load(sc_path)
            cols = [c for c in num_cols if c in df.columns]
            if cols:
                df.loc[:, cols] = scaler.transform(df[cols])
    return df

def _topk(prob: np.ndarray, classes: List[str], k: int) -> Dict[str, float]:
    idx = np.argsort(prob)[::-1][:max(1, k)]
    return {classes[i]: float(prob[i]) for i in idx}


# ---------- Routes ----------

@app.get("/healthz")
def healthz():
    try:
        _ensure_dirs()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    _ensure_dirs()

    # Validate paths
    if not os.path.isabs(req.model_path):
        raise HTTPException(status_code=400, detail="model_path must be absolute")
    if not os.path.exists(req.model_path):
        raise HTTPException(status_code=404, detail=f"model_path not found: {req.model_path}")
    if not os.path.isabs(req.artifacts_dir):
        raise HTTPException(status_code=400, detail="artifacts_dir must be absolute")

    # Load model & artifacts
    model = joblib.load(req.model_path)
    meta_path = os.path.join(req.artifacts_dir, META_FILE)
    enc_path  = os.path.join(req.artifacts_dir, ENC_FILE)
    meta = _load_json(meta_path)
    enc  = _load_json(enc_path)
    target_info = enc.get("target") or {}
    classes = [str(c) for c in target_info.get("classes", [])] or None

    # Prepare input DF
    if req.mode == "records":
        if not req.records:
            raise HTTPException(status_code=400, detail="records must be provided when mode='records'")
        df_in = pd.DataFrame(req.records)
        if req.records_are_raw:
            df_in = _apply_encoders_to_df(df_in, enc)
            df_in = _maybe_scale(df_in, meta, req.artifacts_dir)
        X, feats = _align_features(df_in, meta, req.target_column)
    else:  # "csv"
        if not req.data_path or not os.path.isabs(req.data_path):
            raise HTTPException(status_code=400, detail="data_path must be an absolute path when mode='csv'")
        if not os.path.exists(req.data_path):
            raise HTTPException(status_code=404, detail=f"data_path not found: {req.data_path}")
        dfc = pd.read_csv(req.data_path)
        # Assume CSV is already cleaned/encoded/scaled by preprocessing; just align columns
        X, feats = _align_features(dfc, meta, req.target_column)

    # Predict
    yhat_idx = model.predict(X)
    if classes and isinstance(yhat_idx[0], (np.integer, int)) and max(yhat_idx) < len(classes):
        yhat_lbl = [classes[int(i)] for i in yhat_idx]
    else:
        yhat_lbl = [str(i) for i in yhat_idx]

    out_df = pd.DataFrame({"prediction_index": yhat_idx, "prediction_label": yhat_lbl})

    # Probabilities (top-k)
    sample = []
    if req.return_proba and hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        if classes is None:
            classes = [str(i) for i in range(proba.shape[1])]
        topk_list = [_topk(proba[i], classes, req.top_k) for i in range(proba.shape[0])]
        out_df["topk"] = [json.dumps(t) for t in topk_list]
        # include a small sample in the response
        for i in range(min(5, len(out_df))):
            row = {"prediction_label": yhat_lbl[i], "prediction_index": int(yhat_idx[i]), "topk": topk_list[i]}
            sample.append(row)
    else:
        for i in range(min(5, len(out_df))):
            sample.append({"prediction_label": yhat_lbl[i], "prediction_index": int(yhat_idx[i])})

    # Save predictions CSV if requested
    pred_path = None
    if req.save_predictions:
        os.makedirs(PRED_DIR, exist_ok=True)
        ts = time.strftime("%Y%m%d-%H%M%S")
        fname = req.save_as if req.save_as else f"predictions_{ts}.csv"
        pred_path = os.path.join(PRED_DIR, fname)
        out_df.to_csv(pred_path, index=False)

    return PredictResponse(
        n_rows=int(len(out_df)),
        sample=sample,
        predictions_path=pred_path,
        classes=classes,
    )
