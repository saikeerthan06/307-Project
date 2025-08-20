from __future__ import annotations
import os, json, time
from typing import List, Dict, Any, Optional, Literal, Tuple

import numpy as np
import pandas as pd
import joblib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ---------- Paths / constants ----------
RAW_DIR       = os.getenv("RAW_DIR", "/shared/data/raw")
CLEAN_DIR     = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR     = os.getenv("MODEL_DIR", "/shared/models")
ARTIFACT_DIR  = os.path.join(MODEL_DIR, "artifacts")
PRED_DIR      = os.path.join(ARTIFACT_DIR, "predictions")

META_FILE = "preprocess_meta.json"
ENC_FILE  = "encoders.json"

app = FastAPI(title="Model Inference Service", version="1.0.2")

# ---------- Schemas ----------
class PredictRequest(BaseModel):
    model_path: str
    artifacts_dir: str = ARTIFACT_DIR
    mode: Literal["records", "csv"] = "records"
    records: Optional[List[Dict[str, Any]]] = None
    data_path: Optional[str] = None
    target_column: Optional[str] = "Target"
    records_are_raw: bool = True
    return_proba: bool = True
    top_k: int = 3
    save_predictions: bool = True
    save_as: Optional[str] = None

class PredictResponse(BaseModel):
    n_rows: int
    sample: List[Dict[str, Any]]
    predictions_path: Optional[str] = None
    classes: Optional[List[str]] = None

# ---------- Helpers ----------
def _load_json(p: str) -> Dict[str, Any]:
    if not os.path.exists(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

def _ensure_dirs() -> None:
    os.makedirs(PRED_DIR, exist_ok=True)

def _auto_target(df: pd.DataFrame, explicit: Optional[str]) -> Optional[str]:
    # exact
    if explicit and explicit in df.columns:
        return explicit
    # case-insensitive
    if explicit:
        for c in df.columns:
            if str(c).lower() == str(explicit).lower():
                return c
    # heuristics
    for cand in ("target", "label", "y"):
        for c in df.columns:
            if str(c).strip().lower() == cand:
                return c
    return None

def _apply_encoders(df: pd.DataFrame, enc: Dict[str, Any]) -> pd.DataFrame:
    """
    Make input DF match the training-time encoders spec.
    Robust to booleans, 1/0, yes/no, capitalization, and missing/unseen categories.
    """
    work = df.copy()
    work.columns = [str(c) for c in work.columns]

    def _series(colname: str) -> pd.Series:
        obj = work[colname]
        if isinstance(obj, pd.DataFrame):
            if obj.shape[1] != 1:
                raise ValueError(f"_apply_encoders got multiple columns for '{colname}': {list(obj.columns)}")
            return obj.iloc[:, 0]
        return obj

    # -------- Binary features (e.g., Yes/No → 1/0) --------
    for col, mapping in enc.get("binary", {}).items():
        if col in work.columns:
            s = _series(col)
            # Fast path: already bool or numeric 0/1
            if pd.api.types.is_bool_dtype(s):
                work[col] = s.astype("Int64")
                continue
            if pd.api.types.is_integer_dtype(s) or pd.api.types.is_float_dtype(s):
                # Coerce any numeric to Int (NaNs safe with Int64)
                work[col] = pd.to_numeric(s, errors="coerce").astype("Int64")
                continue

            # General string mapping with robust synonyms
            lower = s.astype(str).str.strip().str.lower()
            m = {str(k).strip().lower(): int(v) for k, v in dict(mapping).items()}
            # Enrich with common synonyms
            m.update({"1": 1, "0": 0, "true": 1, "false": 0, "yes": 1, "no": 0})
            work[col] = lower.map(m).astype("Int64")

    # -------- Ordinal features (e.g., High > Moderate > Low) --------
    for col, order in enc.get("ordinal", {}).items():
        if col in work.columns:
            order = [str(x) for x in (order if isinstance(order, (list, tuple)) else list(order))]
            s = _series(col).astype(str)
            cat = pd.Categorical(s, categories=order, ordered=True)
            codes = pd.Series(cat.codes, index=work.index).replace(-1, np.nan)
            work[col] = codes.astype("Int64")

    # -------- One-hot features --------
    for col, info in enc.get("onehot", {}).items():
        cats = info.get("categories", info) if isinstance(info, dict) else info
        cats = [str(x) for x in (cats if isinstance(cats, (list, tuple)) else list(cats))]
        expected = [f"{col}_{c}" for c in cats]

        if col in work.columns:
            s = _series(col).astype(str)
            s = pd.Categorical(s, categories=cats)
            dummies = pd.get_dummies(s, prefix=col, dtype="int64")
        else:
            dummies = pd.DataFrame(0, index=work.index, columns=expected, dtype="int64")

        # Ensure all expected columns exist and in correct order
        for name in expected:
            if name not in dummies.columns:
                dummies[name] = 0
        dummies = dummies[expected]

        work = pd.concat([work.drop(columns=[col], errors="ignore"), dummies], axis=1)

    # -------- Label-encoded categorical features --------
    for col, info in enc.get("label_features", {}).items():
        classes = info.get("classes", info) if isinstance(info, dict) else info
        classes = [str(x) for x in (classes if isinstance(classes, (list, tuple)) else list(classes))]
        if col in work.columns:
            s = _series(col).astype(str)
            idx = {c: i for i, c in enumerate(classes)}
            work[col] = s.map(idx).astype("Int64")

    # -------- Numeric features --------
    for col in enc.get("numeric_features", []):
        if col in work.columns:
            work[col] = pd.to_numeric(_series(col), errors="coerce")

    return work

def _align_features(df: pd.DataFrame, meta: Dict[str, Any], target_col: Optional[str]) -> Tuple[pd.DataFrame, List[str]]:
    # If meta missing, use all non-target cols
    if not meta:
        tgt = _auto_target(df, target_col)
        feats = [c for c in df.columns if c != tgt]
        return df[feats], feats

    final_cols = meta.get("final_columns", [])
    tgt = meta.get("target_column") or _auto_target(df, target_col)
    feats = [c for c in final_cols if c != tgt and c in df.columns]

    # Add any missing expected columns as zeros to match training shape
    for c in final_cols:
        if c != tgt and c not in df.columns:
            df[c] = 0

    keep = [c for c in final_cols if c != tgt]
    if keep:
        df = df[keep]
        feats = keep
    return df, feats

def _maybe_scale(df: pd.DataFrame, meta: Dict[str, Any], art_dir: str) -> pd.DataFrame:
    sc = meta.get("scaler", {})
    stype = sc.get("type", "none")
    cols = sc.get("num_cols", [])
    if stype in ("zscore", "robust") and cols:
        pkl = os.path.join(art_dir, f"{stype}_scaler.pkl")
        if os.path.exists(pkl):
            scaler = joblib.load(pkl)
            use = [c for c in cols if c in df.columns]
            if not use:
                return df
            try:
                if set(use) == set(cols):
                    df.loc[:, cols] = scaler.transform(df[cols])
                else:
                    # Partial overlap: apply per-column with learned stats
                    if stype == "zscore" and hasattr(scaler, "mean_") and hasattr(scaler, "scale_"):
                        means = pd.Series(scaler.mean_, index=cols)
                        scales = pd.Series(scaler.scale_, index=cols).replace(0, 1.0)
                        df.loc[:, use] = (df[use].astype(float) - means[use]) / scales[use]
                    elif stype == "robust" and hasattr(scaler, "center_") and hasattr(scaler, "scale_"):
                        centers = pd.Series(scaler.center_, index=cols)
                        scales = pd.Series(scaler.scale_, index=cols).replace(0, 1.0)
                        df.loc[:, use] = (df[use].astype(float) - centers[use]) / scales[use]
            except Exception:
                # Be permissive: do not block predictions if scaling cannot be applied
                pass
    return df

def _topk(prob: np.ndarray, classes: List[str], k: int) -> Dict[str, float]:
    idx = np.argsort(prob)[::-1][:max(1, k)]
    return {classes[i]: float(prob[i]) for i in idx}

def _sanitize_features_for_xgb(df: pd.DataFrame) -> pd.DataFrame:
    """
    Make feature matrix safe for XGBoost:
      - ensure unique column names (drop duplicate columns, keeping first)
      - flatten any accidental DataFrame-valued columns to Series
      - coerce all columns to numeric
      - cast to float32 (xgboost-friendly)
    """
    X = df.copy()

    # 1) Ensure string colnames
    X.columns = [str(c) for c in X.columns]

    # 2) Drop duplicate-named columns (keep first)
    if X.columns.duplicated().any():
        # If you prefer to *sum* duplicate columns instead of dropping:
        # X = X.groupby(level=0, axis=1).sum()
        X = X.loc[:, ~X.columns.duplicated()]

    # 3) Flatten any DataFrame-valued columns (paranoia guard)
    for c in list(X.columns):
        col = X[c]
        if isinstance(col, pd.DataFrame):
            if col.shape[1] == 1:
                X[c] = col.iloc[:, 0]
            else:
                # if multiple columns somehow share the same label, keep first
                X[c] = col.iloc[:, 0]

    # 4) Coerce to numeric; non-numeric -> NaN
    for c in list(X.columns):
        X[c] = pd.to_numeric(X[c], errors="coerce")

    # 5) Replace inf/-inf and fill NaN if desired (optional, safe defaults)
    X = X.replace([np.inf, -np.inf], np.nan)

    # 6) XGBoost is happiest with float32
    X = X.astype(np.float32)

    return X


# ---------- Routes ----------
@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    _ensure_dirs()

    if not os.path.isabs(req.model_path):
        raise HTTPException(400, "model_path must be absolute")
    if not os.path.exists(req.model_path):
        raise HTTPException(404, f"model_path not found: {req.model_path}")
    if not os.path.isabs(req.artifacts_dir):
        raise HTTPException(400, "artifacts_dir must be absolute")

    try:
        model = joblib.load(req.model_path)
    except ModuleNotFoundError as e:
        # Typical when the inference image is missing a library (e.g., xgboost)
        raise HTTPException(500, f"Model dependency missing in inference image: {e}. "
                                 f"Rebuild image with required libs and redeploy.") from e

    meta = _load_json(os.path.join(req.artifacts_dir, META_FILE))
    enc  = _load_json(os.path.join(req.artifacts_dir, ENC_FILE))
    classes = [str(c) for c in (enc.get("target", {}).get("classes", []) or [])] or None

    # Prepare inputs
    if req.mode == "records":
        if not req.records:
            raise HTTPException(400, "records required for mode=records")
        df = pd.DataFrame(req.records)
        if req.records_are_raw:
            if enc:
                df = _apply_encoders(df, enc)
            df = _maybe_scale(df, meta, req.artifacts_dir)
        X, feats = _align_features(df, meta, req.target_column)
    else:
        if not req.data_path or not os.path.isabs(req.data_path):
            raise HTTPException(400, "data_path must be absolute")
        if not os.path.exists(req.data_path):
            raise HTTPException(404, f"data_path not found: {req.data_path}")
        df = pd.read_csv(req.data_path)
        if enc:
            df = _apply_encoders(df, enc)
        df = _maybe_scale(df, meta, req.artifacts_dir)
        X, feats = _align_features(df, meta, req.target_column)

    # Predict
    X = _sanitize_features_for_xgb(X) 
    yhat_idx = model.predict(X)
    if classes and isinstance(yhat_idx[0], (np.integer, int)) and int(np.max(yhat_idx)) < len(classes):
        yhat_lbl = [classes[int(i)] for i in yhat_idx]
    else:
        yhat_lbl = [str(i) for i in yhat_idx]

    out = pd.DataFrame({"prediction_index": yhat_idx, "prediction_label": yhat_lbl})
    sample: List[Dict[str, Any]] = []

    if req.return_proba and hasattr(model, "predict_proba"):
        proba = model.predict_proba(X)
        if classes is None:
            classes = [str(i) for i in range(proba.shape[1])]
        topk = [_topk(proba[i], classes, req.top_k) for i in range(proba.shape[0])]
        out["topk"] = [json.dumps(t) for t in topk]
        for i in range(min(5, len(out))):
            sample.append({
                "prediction_label": yhat_lbl[i],
                "prediction_index": int(yhat_idx[i]),
                "topk": topk[i]
            })
    else:
        for i in range(min(5, len(out))):
            sample.append({
                "prediction_label": yhat_lbl[i],
                "prediction_index": int(yhat_idx[i])
            })

    pred_path = None
    if req.save_predictions:
        ts = time.strftime("%Y%m%d-%H%M%S")
        fname = req.save_as if req.save_as else f"predictions_{ts}.csv"
        pred_path = os.path.join(PRED_DIR, fname)
        out.to_csv(pred_path, index=False)

    return PredictResponse(
        n_rows=int(len(out)),
        sample=sample,
        predictions_path=pred_path,
        classes=classes
    )

