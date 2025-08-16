# model_inference/app.py
from flask import Flask, request, jsonify
import os, joblib
import numpy as np
import pandas as pd
from scipy import sparse

app = Flask(__name__)

# ---------------------------
# Paths / environment
# ---------------------------
MODEL_DIR     = os.getenv("MODEL_DIR", "/shared/models")
MODEL_PATH    = os.path.join(MODEL_DIR, "trained_model.pkl")
ENCODER_PATH  = os.path.join(MODEL_DIR, "encoder_ohe.pkl")
LABELS_PATH   = os.path.join(MODEL_DIR, "target_labels.pkl")
CAT_COLS_PATH = os.path.join(MODEL_DIR, "categorical_cols.pkl")
NUM_COLS_PATH = os.path.join(MODEL_DIR, "numerical_cols.pkl")

# ---------------------------
# Load artifacts once at boot
# ---------------------------
model = ohe = labels = categorical_cols = numerical_cols = None
load_error = None
try:
    model = joblib.load(MODEL_PATH)
    ohe = joblib.load(ENCODER_PATH)
    labels = joblib.load(LABELS_PATH)  # can be list/ndarray/dict or None
    categorical_cols = joblib.load(CAT_COLS_PATH)  # list[str]
    numerical_cols = joblib.load(NUM_COLS_PATH)    # list[str]
    app.logger.info("✅ Model inference artifacts loaded.")
except Exception as e:
    load_error = str(e)
    app.logger.error(f"❌ Failed to load artifacts: {e}")

# ---------------------------
# Helpers
# ---------------------------
def ok():
    return all(x is not None for x in [model, ohe, categorical_cols, numerical_cols])

def norm(name: str) -> str:
    """Normalize arbitrary keys → training schema (remove non-alnum, UPPER)."""
    return "".join(ch for ch in str(name) if ch.isalnum()).upper()

def align_records(raw_records):
    """
    Convert any user-provided keys (any case/spelling like 'yearsExperience')
    into the exact training column names using normalization.
    Missing values are filled with safe defaults.
    """
    aligned = []

    # precompute normalized mapping for training schema
    cat_norm_map = {norm(c): c for c in categorical_cols}
    num_norm_map = {norm(c): c for c in numerical_cols}

    for rec in raw_records:
        # normalize incoming keys once
        incoming = {norm(k): v for k, v in dict(rec).items()}
        fixed = {}

        # categorical -> default ""
        for c_norm, c in cat_norm_map.items():
            val = incoming.get(c_norm, "")
            fixed[c] = None if val is None else str(val)

        # numerical -> default 0.0
        for n_norm, n in num_norm_map.items():
            val = incoming.get(n_norm, 0.0)
            try:
                fixed[n] = float(val) if val is not None and str(val) != "" else 0.0
            except Exception:
                fixed[n] = 0.0

        aligned.append(fixed)
    return aligned

def encode_features(df: pd.DataFrame):
    """OHE + numeric hstack (supports sparse)."""
    X_cat = ohe.transform(df[categorical_cols])
    X_num = df[numerical_cols].to_numpy(dtype=float)

    if sparse.issparse(X_cat):
        X = sparse.hstack([sparse.csr_matrix(X_num), X_cat]).tocsr()
    else:
        X = np.hstack([X_num, X_cat])
    return X

def map_label(p):
    """Optionally map numeric class -> friendly label."""
    if labels is None:
        return p
    try:
        idx = int(p)
        if isinstance(labels, (list, np.ndarray)):
            return labels[idx]
        if isinstance(labels, dict):
            return labels.get(idx, p)
    except Exception:
        pass
    return p

# ---------------------------
# Routes
# ---------------------------
@app.get("/healthz")
def healthz():
    if ok():
        return jsonify({"status": "ok"}), 200
    return jsonify({"status": "not_ready", "error": load_error}), 503

@app.get("/")
def home():
    return "Model Inference API is running."

@app.get("/schema")
def schema():
    """Tell clients what columns the model expects."""
    return jsonify({
        "categorical_cols": list(categorical_cols or []),
        "numerical_cols": list(numerical_cols or []),
        "model_path": MODEL_PATH
    }), 200

@app.post("/predict")
def predict():
    if not ok():
        return jsonify({"error": f"Model artifacts not loaded: {load_error}"}), 500

    if not request.is_json:
        return jsonify({"error": "Expected application/json"}), 415

    payload = request.get_json()

    # Accept any of these forms:
    #   {"features": {...}}
    #   {"records": [{...}, {...}]}
    #   [{...}, {...}]
    #   {...}
    if isinstance(payload, dict) and "features" in payload:
        records = [payload["features"]]
    elif isinstance(payload, dict) and "records" in payload:
        records = payload["records"]
    elif isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = [payload]
    else:
        return jsonify({"error": "Body must be an object or list of objects"}), 400

    try:
        # Case/format-agnostic alignment to training schema
        aligned = align_records(records)
        df = pd.DataFrame(aligned, columns=(categorical_cols + numerical_cols))

        # Encode + predict
        X = encode_features(df)
        preds = model.predict(X)

        # Ensure JSON-friendly types
        preds = np.asarray(preds).tolist()
        mapped = [map_label(p) for p in preds]

        # Single vs batch convenience
        response = {
            "predictions": mapped,
            "model_path": MODEL_PATH
        }
        if len(mapped) == 1:
            response["prediction"] = mapped[0]

        return jsonify(response), 200

    except Exception as e:
        app.logger.exception("prediction failed")
        return jsonify({"error": str(e)}), 400

# Backward-compat alias
@app.post("/infer")
def infer_alias():
    return predict()

if __name__ == "__main__":
    # Use Flask dev server inside container; can swap to gunicorn if desired.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
