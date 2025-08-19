import os
import json
import time
from typing import Dict, Any, Optional, List, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix
from xgboost import XGBClassifier

META_FILE = "preprocess_meta.json"     # written by preprocessing
ENC_FILE  = "encoders.json"            # optional; for reference


def ensure_dirs(paths: List[str]) -> None:
    for p in paths:
        os.makedirs(p, exist_ok=True)


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


def _load_meta(artifacts_dir: str) -> Dict[str, Any]:
    meta_path = os.path.join(artifacts_dir, META_FILE)
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _align_columns(df: pd.DataFrame, meta: Dict[str, Any], target_col: Optional[str]) -> Tuple[pd.DataFrame, str, List[str]]:
    """
    Ensure we use the same feature ordering as preprocessing.
    If meta is missing, fall back to 'all columns except target'.
    """
    tgt = _auto_target(df, target_col)
    if not tgt:
        raise ValueError("Unable to determine target column; set target_column explicitly.")

    if meta.get("final_columns"):
        final_cols = [c for c in meta["final_columns"] if c in df.columns]
        features = [c for c in final_cols if c != tgt]
    else:
        features = [c for c in df.columns if c != tgt]

    return df, tgt, features


def train_xgb(
    csv_path: str,
    target_col: Optional[str],
    test_size: float,
    val_size: float,
    random_state: int,
    stratify: bool,
    xgb_params: Dict[str, Any],
    early_stopping_rounds: Optional[int],  # kept for API compatibility (IGNORED)
    model_dir: str,
    artifacts_dir: str,
    save_as: Optional[str],
    persist_metrics: bool,
) -> Dict[str, Any]:

    t0 = time.time()

    # Load dataset
    df = pd.read_csv(csv_path)
    meta = _load_meta(artifacts_dir)

    # Align target & features
    df, tgt, features = _align_columns(df, meta, target_col)

    # Defensive numeric coercion (should already be numeric from preprocessing)
    for c in features + [tgt]:
        if df[c].dtype == "object":
            df[c] = pd.to_numeric(df[c], errors="ignore")

    X = df[features].copy()
    y = df[tgt].copy()

    # Train/val/test split (stratified if desired)
    strat = y if stratify else None
    X_train, X_tmp, y_train, y_tmp = train_test_split(
        X, y, test_size=(test_size + val_size), random_state=random_state, stratify=strat
    )
    rel_val = val_size / (test_size + val_size) if (test_size + val_size) > 0 else 0.0
    X_val, X_test, y_val, y_test = train_test_split(
        X_tmp, y_tmp, test_size=(1 - rel_val), random_state=random_state, stratify=(y_tmp if stratify else None)
    )

    # Configure model
    num_classes = int(len(np.unique(y)))
    params = dict(xgb_params)
    if "objective" not in params:
        params["objective"] = "multi:softprob" if num_classes > 2 else "binary:logistic"
    if params.get("objective", "").startswith("multi") and "num_class" not in params:
        params["num_class"] = num_classes
    params.setdefault("tree_method", "hist")
    params.setdefault("n_jobs", 0)

    model = XGBClassifier(**params)

    # Train (no early_stopping kw to avoid version issues)
    eval_set = [(X_train, y_train), (X_val, y_val)]
    model.fit(X_train, y_train, eval_set=eval_set, verbose=False)

    # Metrics
    def _metrics(y_true, y_pred) -> Dict[str, float]:
        return {
            "accuracy": float(accuracy_score(y_true, y_pred)),
            "f1_micro": float(f1_score(y_true, y_pred, average="micro")),
            "f1_macro": float(f1_score(y_true, y_pred, average="macro")),
        }

    preds_val = model.predict(X_val)
    preds_test = model.predict(X_test)

    cr_dict = classification_report(y_test, preds_test, output_dict=True, zero_division=0)
    cr_text = classification_report(y_test, preds_test, digits=3, zero_division=0)
    cm = confusion_matrix(y_test, preds_test).tolist()
    classes = sorted(np.unique(y).tolist())

    metrics = {
        "val": _metrics(y_val, preds_val),
        "test": _metrics(y_test, preds_test),
        "classification_report": cr_dict,
        "classification_report_text": cr_text,
        "confusion_matrix": cm,
        "classes": classes,
        "n_train": int(len(y_train)),
        "n_val": int(len(y_val)),
        "n_test": int(len(y_test)),
        "num_classes": int(len(classes)),
        "features": features[:50] + (["..."] if len(features) > 50 else []),
        "target": tgt,
        "csv_path": csv_path,
        "elapsed_sec": float(time.time() - t0),
    }

    # Persist model
    os.makedirs(model_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    model_name = save_as if save_as else f"xgb_model_{ts}.joblib"
    model_path = os.path.join(model_dir, model_name)
    joblib.dump(model, model_path)

    # Optional metrics report
    report_path = None
    if persist_metrics:
        os.makedirs(artifacts_dir, exist_ok=True)
        report_path = os.path.join(artifacts_dir, f"training_report_{ts}.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2, ensure_ascii=False)

    return {"model_path": model_path, "report_path": report_path, "report": metrics}

