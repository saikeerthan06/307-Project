# services/ui/app.py
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

import streamlit as st
import pandas as pd
import requests

# --- Env (must match your deployments) ---
PREPROC_URL = os.getenv("PREPROC_URL", "http://data-preprocessing-svc:8000")
TRAIN_URL   = os.getenv("TRAIN_URL",   "http://model-training-svc:8000")
INFER_URL   = os.getenv("INFER_URL",   "http://model-inference-svc:8000")  # reserved for later

RAW_DIR   = os.getenv("RAW_DIR",   "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR = os.getenv("MODEL_DIR", "/shared/models")

# --- Client wrapper (uses your client.py if present) ---
try:
    from client import BackendClient  # must implement start_preprocess, poll_preprocess, start_train, poll_train
except Exception:
    class BackendClient:
        def __init__(self, timeout: int = 10):
            self.timeout = timeout

        # PREPROCESS
        def start_preprocess(self, input_path: str, **overrides):
            payload = {
                "input_path": input_path,
                "drop_duplicates": True,
                "impute_numeric": False,
                "impute_categorical": False,
                "encode_target": True,
                "encode_categorical": "mixed",
                "scale_numeric": "robust",
                "persist_artifacts": True,
                "target_column": "Target",
            }
            payload.update(overrides or {})
            r = requests.post(f"{PREPROC_URL}/preprocess", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json().get("job_id")

        def poll_preprocess(self, job_id: str, wait_s: float = 0.8, timeout_s: int = 300):
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                r = requests.get(f"{PREPROC_URL}/preprocess/{job_id}", timeout=self.timeout)
                r.raise_for_status()
                js = r.json()
                if js.get("state") in ("succeeded", "failed"):
                    return js
                time.sleep(wait_s)
            raise TimeoutError(f"preprocess job {job_id} did not finish within {timeout_s}s")

        # TRAIN
        def start_train(self, input_path: str, **overrides):
            payload = {
                "input_path": input_path,
                "target_column": "Target",
                "test_size": 0.2,
                "val_size": 0.2,
                "random_state": 42,
                "stratify": True,
                "xgb_params": {
                    "n_estimators": 300,
                    "learning_rate": 0.05,
                    "max_depth": 6,
                    "subsample": 0.8,
                    "colsample_bytree": 0.8,
                    "n_jobs": 0,
                    "tree_method": "hist",
                    "objective": "multi:softprob",
                },
                "early_stopping_rounds": 20,   # ignored safely by our trainer
                "persist_metrics": True,
            }
            payload.update(overrides or {})
            r = requests.post(f"{TRAIN_URL}/train", json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r.json().get("job_id")

        def poll_train(self, job_id: str, wait_s: float = 1.0, timeout_s: int = 1800):
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                r = requests.get(f"{TRAIN_URL}/train/{job_id}", timeout=self.timeout)
                r.raise_for_status()
                js = r.json()
                if js.get("state") in ("succeeded", "failed"):
                    return js
                time.sleep(wait_s)
            raise TimeoutError(f"train job {job_id} did not finish in {timeout_s}s")


client = BackendClient(timeout=20)

# --- Helpers ---
def ensure_dirs():
    Path(RAW_DIR).mkdir(parents=True, exist_ok=True)
    Path(CLEAN_DIR).mkdir(parents=True, exist_ok=True)
    Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    (Path(MODEL_DIR)/"artifacts").mkdir(parents=True, exist_ok=True)

def service_health(url: str) -> bool:
    try:
        r = requests.get(f"{url}/healthz", timeout=3)
        return r.ok
    except Exception:
        return False

def run_inference_csv(model_path: str, csv_path: str, top_k: int = 3) -> dict:
    payload = {
        "model_path": model_path,
        "artifacts_dir": str(Path(MODEL_DIR) / "artifacts"),
        "mode": "csv",
        "data_path": csv_path,
        "return_proba": True,
        "top_k": int(top_k),
        "save_predictions": True,
    }
    r = requests.post(f"{INFER_URL}/predict", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def render_training_metrics(report: Dict[str, Any]):
    st.subheader("Training metrics")
    c1, c2, c3 = st.columns(3)
    c1.metric("Val accuracy", f"{report.get('val',{}).get('accuracy',0):.3f}")
    c2.metric("Test accuracy", f"{report.get('test',{}).get('accuracy',0):.3f}")
    c3.metric("Classes", int(report.get("num_classes", 0)))

    cr = report.get("classification_report")
    cr_text = report.get("classification_report_text", "")
    if cr:
        st.caption("Classification report (test set)")
        try:
            df = pd.DataFrame(cr).T
            st.dataframe(df, use_container_width=True)
        except Exception:
            st.text(cr_text)
    cm = report.get("confusion_matrix")
    if cm:
        st.caption("Confusion matrix (test set)")
        st.dataframe(pd.DataFrame(cm), use_container_width=True)

def list_paths(dir_path: str, glob_pat: str) -> list[Path]:
    p = Path(dir_path)
    return sorted([x for x in p.glob(glob_pat) if x.is_file()])

# --- UI state ---
if "pp_job_id" not in st.session_state:
    st.session_state.pp_job_id = None
if "mt_job_id" not in st.session_state:
    st.session_state.mt_job_id = None
if "last_clean_path" not in st.session_state:
    st.session_state.last_clean_path = None
if "last_model_path" not in st.session_state:
    st.session_state.last_model_path = None

# --- Layout ---
st.set_page_config(page_title="Hospital ML – UI", layout="wide")
st.title("🏥 Hospital ML – End-to-End (Kubernetes)")

ensure_dirs()

with st.sidebar:
    st.subheader("Services")
    dp_ok = service_health(PREPROC_URL)
    mt_ok = service_health(TRAIN_URL)
    st.write(f"Preprocessing: {'🟢' if dp_ok else '🔴'}")
    st.write(f"Model Training: {'🟢' if mt_ok else '🔴'}")
    st.caption("Paths")
    st.code(f"RAW_DIR   = {RAW_DIR}\nCLEAN_DIR = {CLEAN_DIR}\nMODEL_DIR = {MODEL_DIR}")

# --- Section: Upload & Preprocess ---
st.header("1) Upload raw CSV and preprocess")

uploaded = st.file_uploader("Upload a raw CSV", type=["csv"])
col_u1, col_u2 = st.columns([3, 2])

if uploaded is not None:
    # Save into RAW_DIR
    raw_path = Path(RAW_DIR) / uploaded.name
    with open(raw_path, "wb") as f:
        f.write(uploaded.getbuffer())
    st.success(f"Uploaded to: {raw_path}")

    # Launch preprocess
    if col_u1.button("Run preprocessing", use_container_width=True, disabled=not dp_ok):
        try:
            job = client.start_preprocess(str(raw_path))
            st.session_state.pp_job_id = job
            with st.spinner("Preprocessing..."):
                res = client.poll_preprocess(job)
            if res.get("state") == "succeeded":
                out_path = res.get("output_path")
                st.session_state.last_clean_path = out_path
                st.success(f"Preprocessing complete! Clean CSV → {out_path}")
                # Brief summary
                report = res.get("report", {}) or {}
                shape_b = report.get("shape_before", [])
                shape_a = report.get("shape_after", [])
                st.write(f"Rows/Cols before: {shape_b} → after: {shape_a}")
            else:
                st.error(f"Preprocess failed: {res.get('error')}")
                if res.get("trace"):
                    with st.expander("Traceback"):
                        st.code(res["trace"])
        except Exception as e:
            st.error(f"Preprocess error: {e}")

else:
    col_u1.info("Upload a CSV to enable preprocessing.")
    if not dp_ok:
        col_u2.warning("Preprocessing service not healthy.")

# --- Section: Choose a cleaned CSV (from PVC) ---
st.header("2) Pick a cleaned CSV to train on")
clean_files = list_paths(CLEAN_DIR, "*_clean.csv")
if st.session_state.last_clean_path:
    # Ensure its presence at top
    lp = Path(st.session_state.last_clean_path)
    if lp.exists() and lp not in clean_files:
        clean_files = [lp] + clean_files

if clean_files:
    sel_clean = st.selectbox(
        "Select a cleaned CSV",
        options=clean_files,
        index=0,
        format_func=lambda p: p.name,
        key="sel_clean",
    )
    # Preview
    try:
        df_preview = pd.read_csv(sel_clean).head(10)
        st.caption("Preview (first 10 rows)")
        st.dataframe(df_preview, use_container_width=True)
    except Exception as e:
        st.warning(f"Could not preview CSV: {e}")
else:
    st.info("No *_clean.csv found yet. Run preprocessing above.")

# --- Section: Train ---
st.header("3) Train XGBoost on the cleaned CSV")
col_t1, col_t2 = st.columns([3, 2])

if clean_files and col_t1.button("Start training", use_container_width=True, disabled=not mt_ok):
    try:
        job = client.start_train(str(st.session_state.sel_clean))
        st.session_state.mt_job_id = job
        with st.spinner("Training..."):
            result = client.poll_train(job)
        if result.get("state") == "succeeded":
            st.success("Training complete!")
            st.session_state.last_model_path = result.get("model_path")
            # Metrics
            render_training_metrics(result.get("report", {}) or {})
            # Paths
            st.caption(f"Model saved to: {result.get('model_path')}")
            if result.get("report_path"):
                st.caption(f"Report saved to: {result.get('report_path')}")
        else:
            st.error(f"Training failed: {result.get('error')}")
            if result.get("trace"):
                with st.expander("Traceback"):
                    st.code(result["trace"])
    except Exception as e:
        st.error(f"Train error: {e}")
else:
    if not mt_ok:
        col_t2.warning("Model-training service not healthy.")

# --- Section: Predict (Model Inference) ---
st.header("4) Predict on a cleaned CSV")

# Choose a model
models = list_paths(MODEL_DIR, "*.joblib")
model_choice = st.selectbox("Choose model (.joblib)", models, format_func=lambda p: p.name, key="inf_model")

# Choose a cleaned CSV
clean_files = list_paths(CLEAN_DIR, "*_clean.csv")
csv_choice = st.selectbox("Choose cleaned CSV", clean_files, format_func=lambda p: p.name, key="inf_csv")

top_k = st.slider("Top-K probabilities to return", min_value=1, max_value=5, value=3)

inf_col1, inf_col2 = st.columns([3, 2])
if model_choice and csv_choice and inf_col1.button("Run inference", use_container_width=True):
    try:
        with st.spinner("Running model inference..."):
            resp = run_inference_csv(str(model_choice), str(csv_choice), top_k=top_k)
        # ✅ clear, visible completion text
        st.success(f"Model inference complete! Predictions saved to: {resp.get('predictions_path')}")
        # Show a tiny sample
        sample = resp.get("sample", [])
        if sample:
            st.caption("Sample predictions")
            st.dataframe(pd.DataFrame(sample), use_container_width=True)
    except Exception as e:
        st.error(f"Inference error: {e}")


# --- Section: Artifacts (download from PVC) ---
st.header("4) Artifacts")
cA, cB, cC = st.columns(3)

with cA:
    st.subheader("Cleaned CSVs")
    try:
        clean_files = list_paths(CLEAN_DIR, "*.csv")
        if not clean_files:
            st.write("No files in CLEAN_DIR.")
        else:
            chosen = st.selectbox("Choose CSV", clean_files, format_func=lambda p: p.name, key="dl_csv")
            if chosen and chosen.exists():
                with open(chosen, "rb") as f:
                    st.download_button(
                        "Download CSV",
                        data=f.read(),
                        file_name=chosen.name,
                        mime="text/csv",
                        use_container_width=True,
                    )
    except Exception as e:
        st.warning(f"Could not list CLEAN_DIR: {e}")

with cB:
    st.subheader("Models (.joblib)")
    try:
        models = list_paths(MODEL_DIR, "*.joblib")
        if not models:
            st.write("No models in MODEL_DIR.")
        else:
            chosen = st.selectbox("Choose model", models, format_func=lambda p: p.name, key="dl_model")
            if chosen and chosen.exists():
                with open(chosen, "rb") as f:
                    st.download_button(
                        "Download model",
                        data=f.read(),
                        file_name=chosen.name,
                        mime="application/octet-stream",
                        use_container_width=True,
                    )
    except Exception as e:
        st.warning(f"Could not list MODEL_DIR: {e}")

with cC:
    st.subheader("Training reports")
    artifacts_dir = Path(MODEL_DIR) / "artifacts"
    try:
        reports = list_paths(str(artifacts_dir), "training_report_*.json")
        if not reports:
            st.write("No training reports yet.")
        else:
            chosen = st.selectbox("Choose report", reports, format_func=lambda p: p.name, key="dl_report")
            if chosen and chosen.exists():
                with open(chosen, "rb") as f:
                    st.download_button(
                        "Download report",
                        data=f.read(),
                        file_name=chosen.name,
                        mime="application/json",
                        use_container_width=True,
                    )
    except Exception as e:
        st.warning(f"Could not list artifacts: {e}")

pred_dir = Path(MODEL_DIR) / "artifacts" / "predictions"
st.subheader("Predictions (CSV)")
try:
    preds = list_paths(str(pred_dir), "*.csv")
    if not preds:
        st.write("No predictions saved yet.")
    else:
        sel_pred = st.selectbox("Choose predictions file", preds, format_func=lambda p: p.name, key="dl_preds")
        if sel_pred and sel_pred.exists():
            with open(sel_pred, "rb") as f:
                st.download_button(
                    "Download predictions CSV",
                    data=f.read(),
                    file_name=sel_pred.name,
                    mime="text/csv",
                    use_container_width=True,
                )
except Exception as e:
    st.warning(f"Could not list predictions: {e}")


st.caption("Note: files live inside the cluster PVC; use the download buttons to save locally.")
