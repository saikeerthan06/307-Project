from __future__ import annotations
import os, json, time
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

app = FastAPI(title="Model Inference Service", version="1.0.1")

class PredictRequest(BaseModel):
    model_path: str
    artifacts_dir: str = ARTIFACT_DIR
    mode: Literal["records","csv"] = "records"
    records: Optional[List[Dict[str,Any]]] = None
    data_path: Optional[str] = None
    target_column: Optional[str] = "Target"
    records_are_raw: bool = True
    return_proba: bool = True
    top_k: int = 3
    save_predictions: bool = True
    save_as: Optional[str] = None

class PredictResponse(BaseModel):
    n_rows: int
    sample: List[Dict[str,Any]]
    predictions_path: Optional[str] = None
    classes: Optional[List[str]] = None

def _load_json(p:str)->Dict[str,Any]:
    if not os.path.exists(p): return {}
    with open(p,"r",encoding="utf-8") as f: return json.load(f)

def _ensure_dirs():
    os.makedirs(PRED_DIR, exist_ok=True)

def _auto_target(df:pd.DataFrame, explicit:Optional[str])->Optional[str]:
    if explicit and explicit in df.columns: return explicit
    if explicit:
        for c in df.columns:
            if c.lower() == str(explicit).lower(): return c
    for cand in ["target","label","y"]:
        for c in df.columns:
            if str(c).strip().lower() == cand: return c
    return None

def _normalize_strings(series: pd.Series) -> pd.Series:
    if series.dtype != object: return series
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

def _apply_encoders(df:pd.DataFrame, enc:Dict[str,Any])->pd.DataFrame:
    work = df.copy()
    for c in work.columns: work[c] = _normalize_strings(work[c])

    # Binary
    for col, mapping in enc.get("binary", {}).items():
        if col in work.columns:
            work[col] = work[col].map(lambda v: mapping.get(str(v), np.nan)).astype("float")

    # Ordinal
    for col, info in enc.get("ordinal", {}).items():
        if col in work.columns:
            mp = info.get("mapping", {})
            work[col] = work[col].map(lambda v: mp.get(v, np.nan)).astype("float")

    # One-hot
    for col, info in enc.get("onehot", {}).items():
        if col in work.columns:
            dummies = pd.get_dummies(work[col], prefix=col, dtype="int64")
            for cat in info.get("categories", []):
                cname = f"{col}_{cat}"
                if cname not in dummies.columns: dummies[cname] = 0
            keep = [f"{col}_{cat}" for cat in info.get("categories", [])]
            dummies = dummies[keep]
            work = pd.concat([work.drop(columns=[col]), dummies], axis=1)

    # Label features
    for col, info in enc.get("label_features", {}).items():
        if col in work.columns:
            classes = [str(x) for x in info.get("classes", [])]
            idx = {cls:i for i,cls in enumerate(classes)}
            work[col] = work[col].astype(str).map(lambda v: idx.get(v, 0)).astype("int64")

    # attempt numeric for any remaining objects
    for c in work.columns:
        if work[c].dtype == object:
            work[c] = pd.to_numeric(work[c], errors="ignore")
    return work

def _align_features(df:pd.DataFrame, meta:Dict[str,Any], target_col:Optional[str])->(pd.DataFrame,List[str]):
    # If meta missing, use all non-target cols
    if not meta:
        tgt = _auto_target(df, target_col)
        feats = [c for c in df.columns if c != tgt]
        return df[feats], feats
    final_cols = meta.get("final_columns", [])
    tgt = meta.get("target_column") or _auto_target(df, target_col)
    feats = [c for c in final_cols if c != tgt and c in df.columns]
    for c in final_cols:
        if c != tgt and c not in df.columns: df[c] = 0
    keep = [c for c in final_cols if c != tgt]
    if keep:
        df = df[keep]; feats = keep
    return df, feats

def _maybe_scale(df:pd.DataFrame, meta:Dict[str,Any], art_dir:str)->pd.DataFrame:
    sc = (meta.get("scaler") or {})
    stype = sc.get("type","none"); cols = sc.get("num_cols",[])
    if stype in ("zscore","robust") and cols:
        pkl = os.path.join(art_dir, f"{stype}_scaler.pkl")
        if os.path.exists(pkl):
            scaler = joblib.load(pkl)
            use = [c for c in cols if c in df.columns]
            if use: df.loc[:,use] = scaler.transform(df[use])
    return df

def _topk(prob:np.ndarray, classes:List[str], k:int)->Dict[str,float]:
    idx = np.argsort(prob)[::-1][:max(1,k)]
    return {classes[i]: float(prob[i]) for i in idx}

@app.get("/healthz")
def healthz(): return {"status":"ok"}

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    _ensure_dirs()
    if not os.path.isabs(req.model_path): raise HTTPException(400, "model_path must be absolute")
    if not os.path.exists(req.model_path): raise HTTPException(404, f"model_path not found: {req.model_path}")
    if not os.path.isabs(req.artifacts_dir): raise HTTPException(400, "artifacts_dir must be absolute")

    model = joblib.load(req.model_path)
    meta = _load_json(os.path.join(req.artifacts_dir, META_FILE))
    enc  = _load_json(os.path.join(req.artifacts_dir, ENC_FILE))
    classes = [str(c) for c in (enc.get("target", {}).get("classes", []) or [])] or None

    # Prepare input
    if req.mode == "records":
        if not req.records: raise HTTPException(400, "records required for mode=records")
        df = pd.DataFrame(req.records)
        if req.records_are_raw:
            # If encoders are missing, we still continue using pass-through numeric columns.
            if enc:
                df = _apply_encoders(df, enc)
            df = _maybe_scale(df, meta, req.artifacts_dir)
        X, feats = _align_features(df, meta, req.target_column)
    else:
        if not req.data_path or not os.path.isabs(req.data_path): raise HTTPException(400,"data_path must be absolute")
        if not os.path.exists(req.data_path): raise HTTPException(404, f"data_path not found: {req.data_path}")
        df = pd.read_csv(req.data_path)
        X, feats = _align_features(df, meta, req.target_column)

    yhat_idx = model.predict(X)
    if classes and isinstance(yhat_idx[0], (np.integer,int)) and max(yhat_idx) < len(classes):
        yhat_lbl = [classes[int(i)] for i in yhat_idx]
    else:
        yhat_lbl = [str(i) for i in yhat_idx]

    out = pd.DataFrame({"prediction_index": yhat_idx, "prediction_label": yhat_lbl})

    sample: List[Dict[str,Any]] = []
    if req.return_proba and hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        if classes is None: classes = [str(i) for i in range(proba.shape[1])]
        topk = [_topk(proba[i], classes, req.top_k) for i in range(proba.shape[0])]
        out["topk"] = [json.dumps(t) for t in topk]
        for i in range(min(5,len(out))):
            sample.append({"prediction_label": yhat_lbl[i], "prediction_index": int(yhat_idx[i]), "topk": topk[i]})
    else:
        for i in range(min(5,len(out))):
            sample.append({"prediction_label": yhat_lbl[i], "prediction_index": int(yhat_idx[i])})

    pred_path = None
    if req.save_predictions:
        ts = time.strftime("%Y%m%d-%H%M%S")
        fname = req.save_as if req.save_as else f"predictions_{ts}.csv"
        pred_path = os.path.join(PRED_DIR, fname)
        out.to_csv(pred_path, index=False)

    return PredictResponse(n_rows=int(len(out)), sample=sample, predictions_path=pred_path, classes=classes)
