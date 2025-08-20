# services/ui/app.py
import os, json, time
from pathlib import Path
from typing import Dict, Any, List, Optional
from client import BackendClient
cli = BackendClient(timeout=20)

import requests
import streamlit as st
import pandas as pd

# ---------------- ENV ----------------
PREPROC_URL = os.getenv("PREPROC_URL", "http://data-preprocessing-svc:8000")
TRAIN_URL   = os.getenv("TRAIN_URL",   "http://model-training-svc:8000")
INFER_URL   = os.getenv("INFER_URL",   "http://model-inference-svc:8000")

RAW_DIR   = os.getenv("RAW_DIR",   "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR = os.getenv("MODEL_DIR", "/shared/models")
ARTIFACTS_DIR   = str(Path(MODEL_DIR) / "artifacts")
PREDICTIONS_DIR = str(Path(ARTIFACTS_DIR) / "predictions")

import os
RAW_DIR   = os.getenv("RAW_DIR", "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")

if "uploaded_raw_path" not in st.session_state:
    st.session_state.uploaded_raw_path = None  # abs path to uploaded raw CSV
if "cleaned_csv_path" not in st.session_state:
    st.session_state.cleaned_csv_path = None   # abs path returned by preprocess job
if "model_ready" not in st.session_state:
    st.session_state.model_ready = False       # flip True after training succeeds

# Sidebar controls: show/hide prior cleaned files & session reset
if "show_prior_cleaned" not in st.session_state:
    st.session_state.show_prior_cleaned = True  # default: list prior cleaned CSVs

def reset_ui_session():
    for k in ["uploaded_raw_path", "cleaned_csv_path", "model_ready", "model_path", "_preproc_state"]:
        if k in st.session_state:
            del st.session_state[k]
    st.session_state.show_prior_cleaned = False  # after reset, hide old files until new preprocess

# -------------- Client ---------------
class BackendClient:
    def __init__(self, timeout:int=20): self.timeout = timeout
    def start_preprocess(self, input_path:str, **overrides):
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
        }; payload.update(overrides or {})
        r = requests.post(f"{PREPROC_URL}/preprocess", json=payload, timeout=self.timeout); r.raise_for_status()
        return r.json().get("job_id")
    def poll_preprocess(self, job_id:str, wait_s=0.8, timeout_s=300, progress_fn=None):
        t_end = time.time() + timeout_s
        while time.time() < t_end:
            r = requests.get(f"{PREPROC_URL}/preprocess/{job_id}", timeout=self.timeout); r.raise_for_status()
            j = r.json()
            if progress_fn: progress_fn(j)
            if j.get("state") in ("succeeded","failed"): return j
            time.sleep(wait_s)
        raise TimeoutError(f"preprocess {job_id} timed out")
    def start_train(self, input_path:str, **overrides):
        payload = {
            "input_path": input_path,
            "target_column": "Target",
            "test_size": 0.2,
            "val_size": 0.2,
            "random_state": 42,
            "stratify": True,
            "xgb_params": {
                "n_estimators": 300, "learning_rate": 0.05, "max_depth": 6,
                "subsample": 0.8, "colsample_bytree": 0.8, "n_jobs": 0,
                "tree_method": "hist", "objective": "multi:softprob",
            },
            "early_stopping_rounds": 20,   # ignored if unsupported
            "persist_metrics": True,
        }; payload.update(overrides or {})
        r = requests.post(f"{TRAIN_URL}/train", json=payload, timeout=self.timeout); r.raise_for_status()
        return r.json().get("job_id")
    def poll_train(self, job_id:str, wait_s=1.0, timeout_s=1800):
        t_end = time.time() + timeout_s
        while time.time() < t_end:
            r = requests.get(f"{TRAIN_URL}/train/{job_id}", timeout=self.timeout); r.raise_for_status()
            j = r.json()
            if j.get("state") in ("succeeded","failed"): return j
            time.sleep(wait_s)
        raise TimeoutError(f"train {job_id} timed out")

client = BackendClient(timeout=20)

# -------------- Helpers --------------
def ensure_dirs():
    for p in [RAW_DIR, CLEAN_DIR, MODEL_DIR, ARTIFACTS_DIR, PREDICTIONS_DIR]:
        Path(p).mkdir(parents=True, exist_ok=True)

def service_ok(url:str)->bool:
    try: return requests.get(f"{url}/healthz", timeout=3).ok
    except Exception: return False

def list_paths(d:str, pat:str)->List[Path]:
    p = Path(d); return sorted([x for x in p.glob(pat) if x.is_file()])

def load_artifacts()->Dict[str,Any]:
    enc, meta = {}, {}
    ep, mp = Path(ARTIFACTS_DIR)/"encoders.json", Path(ARTIFACTS_DIR)/"preprocess_meta.json"
    if ep.exists():
        try: enc = json.loads(ep.read_text(encoding="utf-8"))
        except Exception: pass
    if mp.exists():
        try: meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception: pass
    return {"enc": enc, "meta": meta}

def render_train_metrics(rep:Dict[str,Any]):
    st.subheader("Training metrics")
    c1,c2,c3 = st.columns(3)
    c1.metric("Val acc", f"{rep.get('val',{}).get('accuracy',0):.3f}")
    c2.metric("Test acc", f"{rep.get('test',{}).get('accuracy',0):.3f}")
    c3.metric("Classes", int(rep.get("num_classes",0)))
    cr = rep.get("classification_report")
    if cr:
        st.caption("Classification report (test)")
        try: st.dataframe(pd.DataFrame(cr).T, use_container_width=True)
        except Exception: st.text(rep.get("classification_report_text",""))
    cm = rep.get("confusion_matrix")
    if cm:
        st.caption("Confusion matrix (test)")
        st.dataframe(pd.DataFrame(cm), use_container_width=True)

def normalize_num(s:Optional[str])->Optional[float]:
    if s is None or s.strip()=="":
        return None
    try: return float(s)
    except Exception: return None

def infer_records(model_path:str, record:Dict[str,Any], top_k:int=3)->Dict[str,Any]:
    payload = {
        "model_path": model_path,
        "artifacts_dir": ARTIFACTS_DIR,
        "mode": "records",
        "records": [record],
        "records_are_raw": True,
        "return_proba": True,
        "top_k": int(top_k),
        "save_predictions": True,
    }
    r = requests.post(f"{INFER_URL}/predict", json=payload, timeout=60); r.raise_for_status()
    return r.json()

def infer_csv(model_path:str, csv_path:str, top_k:int=3)->Dict[str,Any]:
    payload = {
        "model_path": model_path,
        "artifacts_dir": ARTIFACTS_DIR,
        "mode": "csv",
        "data_path": csv_path,
        "return_proba": True,
        "top_k": int(top_k),
        "save_predictions": True,
    }
    r = requests.post(f"{INFER_URL}/predict", json=payload, timeout=60); r.raise_for_status()
    return r.json()

# ---------- Build a schema for manual form ----------
def build_feature_schema(enc:Dict[str,Any], meta:Dict[str,Any], cleaned_csv:Optional[Path])->Dict[str,Any]:
    """
    Returns a schema with:
      fields: List[ Dict{name, kind, options(list)|hint(str)} ]
      target: Optional[str]
      source: "encoders" | "meta+csv" | "meta" | "csv" | "fallback"
    kind ∈ {"numeric","checkbox","select","text"}
    """
    schema = {"fields": [], "target": None, "source": "fallback"}

    # Try to get target
    tgt = None
    if meta.get("target_column"):
        tgt = meta["target_column"]
    elif enc.get("target", {}).get("column"):
        tgt = enc["target"]["column"]
    schema["target"] = tgt

    # 1) If encoders contain actual mappings, construct categorical widgets first
    has_cats = any(enc.get(k) for k in ("binary","ordinal","onehot","label_features"))
    if has_cats:
        schema["source"] = "encoders"
        existing = set()
        # Binary
        for col, mapping in enc.get("binary", {}).items():
            schema["fields"].append({"name": col, "kind":"select", "options": list(mapping.keys())})
            existing.add(col)
        # Ordinal
        for col, info in enc.get("ordinal", {}).items():
            ordered = info.get("ordered")
            if not ordered:
                mp = info.get("mapping", {})
                ordered = [k for k,_ in sorted(mp.items(), key=lambda kv: kv[1])]
            schema["fields"].append({"name": col, "kind":"select", "options": ordered or []})
            existing.add(col)
        # Onehot (single select)
        for col, info in enc.get("onehot", {}).items():
            cats = info.get("categories", [])
            schema["fields"].append({"name": col, "kind":"select", "options": cats})
            existing.add(col)
        # Label
        for col, info in enc.get("label_features", {}).items():
            classes = [str(x) for x in info.get("classes", [])]
            schema["fields"].append({"name": col, "kind":"select", "options": classes})
            existing.add(col)
        # Add numeric from meta if present
        numeric_added = False
        for col in meta.get("num_cols", []):
            if col != tgt and col not in existing:
                schema["fields"].append({"name": col, "kind": "numeric"})
                existing.add(col)
                numeric_added = True

        # If we only had categorical encoders and meta lacks num_cols,
        # augment with numerics inferred from the cleaned CSV sample
        if not numeric_added and cleaned_csv and cleaned_csv.exists():
            try:
                _df = pd.read_csv(cleaned_csv, nrows=2000)
                for col in _df.columns:
                    if col == tgt or col in existing:
                        continue
                    s = _df[col]
                    if pd.api.types.is_numeric_dtype(s):
                        uniq = sorted(pd.unique(s.dropna()))
                        if len(uniq) <= 2 and set(uniq).issubset({0, 1}):
                            schema["fields"].append({"name": col, "kind": "checkbox"})
                        else:
                            schema["fields"].append({"name": col, "kind": "numeric"})
                        existing.add(col)
                # Mark that our field set came from encoders + csv augmentation
                schema["source"] = "encoders+csv"
            except Exception:
                pass

        # If we have any fields at this point, return the schema
        if schema["fields"]:
            return schema

    # 2) If meta has final_columns, derive from CSV dtypes when available
    final_cols = meta.get("final_columns") or []
    if final_cols:
        feats = [c for c in final_cols if c != tgt]
        sample_df = None
        if cleaned_csv and cleaned_csv.exists():
            try:
                sample_df = pd.read_csv(cleaned_csv, nrows=2000)
            except Exception:
                sample_df = None

        if sample_df is not None:
            # Use dtypes & values to pick widgets
            for col in feats:
                if col not in sample_df.columns:
                    # if not in CSV (possible in some flows), fall back to text
                    schema["fields"].append({"name": col, "kind":"text"})
                    continue
                s = sample_df[col]
                if pd.api.types.is_numeric_dtype(s):
                    # checkbox if strictly binary {0,1}
                    uniq = sorted(pd.unique(s.dropna()))
                    if len(uniq) <= 2 and set(uniq).issubset({0,1}):
                        schema["fields"].append({"name": col, "kind":"checkbox"})
                    else:
                        schema["fields"].append({"name": col, "kind":"numeric"})
                else:
                    top = s.dropna().astype(str).value_counts().index.tolist()[:15]
                    if 1 <= len(top) <= 15:
                        schema["fields"].append({"name": col, "kind":"select", "options": top})
                    else:
                        schema["fields"].append({"name": col, "kind":"text", "hint": ", ".join(top[:5])})
            schema["source"] = "meta+csv"
            return schema
        else:
            # No CSV — render all as text so the form never disappears
            for col in feats:
                schema["fields"].append({"name": col, "kind":"text"})
            schema["source"] = "meta"
            return schema

    # 3) Last-resort CSV-only (no meta at all)
    if cleaned_csv and cleaned_csv.exists():
        try:
            df = pd.read_csv(cleaned_csv, nrows=2000)
            # pick target heuristically
            if schema["target"] is None:
                for cand in ["Target","target","label","y"]:
                    if cand in df.columns: schema["target"] = cand; break
            feats = [c for c in df.columns if c != schema["target"]]
            for col in feats:
                s = df[col]
                if pd.api.types.is_numeric_dtype(s):
                    uniq = sorted(pd.unique(s.dropna()))
                    if len(uniq) <= 2 and set(uniq).issubset({0,1}):
                        schema["fields"].append({"name": col, "kind":"checkbox"})
                    else:
                        schema["fields"].append({"name": col, "kind":"numeric"})
                else:
                    top = s.dropna().astype(str).value_counts().index.tolist()[:15]
                    if 1 <= len(top) <= 15:
                        schema["fields"].append({"name": col, "kind":"select", "options": top})
                    else:
                        schema["fields"].append({"name": col, "kind":"text", "hint": ", ".join(top[:5])})
            schema["source"] = "csv"
            return schema
        except Exception:
            pass

    # 4) Fallback — empty schema: show nothing (but the button will remain)
    return schema

# ---------------- Page ----------------
st.set_page_config(page_title="Hospital ML – UI", layout="wide", initial_sidebar_state="expanded")
st.title("🏥 Hospital ML – End-to-End (Kubernetes)")
# Top-right quick reset (mirrors the sidebar button)
cr1, cr2 = st.columns([6,1])
with cr2:
    st.button(
        "Reset session",
        on_click=reset_ui_session,
        help="Clear UI state and hide previously discovered files until you preprocess again.",
        use_container_width=True,
        key="reset_top"
    )
ensure_dirs()

with st.sidebar:
    st.subheader("Services")
    st.write(f"Preprocessing: {'🟢' if service_ok(PREPROC_URL) else '🔴'}")
    st.write(f"Training:      {'🟢' if service_ok(TRAIN_URL) else '🔴'}")
    st.write(f"Inference:     {'🟢' if service_ok(INFER_URL) else '🔴'}")
    st.caption("Paths")
    st.code(f"RAW_DIR={RAW_DIR}\nCLEAN_DIR={CLEAN_DIR}\nMODEL_DIR={MODEL_DIR}")
    st.divider()
    st.subheader("Session controls")
    st.checkbox(
        "Show previous cleaned files",
        key="show_prior_cleaned",
        help="When off, Section 2 will not list old cleaned CSVs; only files produced in this session will be used.",
    )
    st.button(
        "Reset UI session",
        on_click=reset_ui_session,
        help="Clear UI state and hide previously discovered files until you preprocess again.",
        use_container_width=True,
    )

# --- 1) Upload & preprocess ---
# --- 1) Upload raw CSV and preprocess ---
st.header("1) Upload raw CSV and preprocess")
up = st.file_uploader("Upload CSV", type=["csv"])
c1, c2 = st.columns([3,2])
if up is not None:
    raw_path = Path(RAW_DIR) / up.name
    with open(raw_path, "wb") as f: f.write(up.getbuffer())
    st.success(f"Uploaded → {raw_path}")
    st.session_state.uploaded_raw_path = str(raw_path)
    if c1.button("Run preprocessing", use_container_width=True):
        try:
            jid = client.start_preprocess(str(raw_path))
            status_ph = st.empty()
            def _report(j: Dict[str, Any]):
                st.session_state["_preproc_state"] = j.get("state")
                s = j.get("state", "?")
                prog = j.get("progress") or j.get("pct") or j.get("step")
                msg = j.get("message") or j.get("detail")
                line = f"**Status:** {s}"
                if prog is not None:
                    line += f" | **Progress:** {prog}"
                if msg:
                    line += f"\n{msg}"
                status_ph.markdown(line)

            with st.spinner("Preprocessing..."):
                res = client.poll_preprocess(jid, timeout_s=900, progress_fn=_report)

            if res.get("state") == "succeeded":
                out_path = res.get("output_path")
                st.session_state.cleaned_csv_path = out_path
                st.session_state.model_ready = False
                status_ph.empty()
                st.success(f"✅ Preprocessing complete → {out_path}")
                rep = res.get("report",{}) or {}
                st.write(f"Shape before/after: {rep.get('shape_before')} → {rep.get('shape_after')}")
            elif res.get("state") == "failed":
                st.error(f"Preprocess failed: {res.get('error')}")
                if res.get("trace"): st.code(res["trace"])
            else:
                st.warning("Preprocess ended in an unknown state. Please check the data-preprocessing logs.")
        except Exception as e:
            st.error(f"Preprocess error: {e}")
else:
    c1.info("Upload a CSV to enable preprocessing.")

# --- 2) pick cleaned CSV (gated, no preview) ---
st.header("2) Pick a cleaned CSV")
sel_clean: Optional[Path] = None
if st.session_state.cleaned_csv_path:
    # Build choices but default to the file produced by the last preprocess
    default_path = Path(st.session_state.cleaned_csv_path)
    clean_files = list_paths(CLEAN_DIR, "*_clean.csv") if st.session_state.get("show_prior_cleaned", True) else []
    # Ensure the default is in the options
    if default_path.exists() and default_path not in clean_files:
        clean_files = [default_path] + clean_files
    if clean_files:
        # Pre-select the default file when present
        def_idx = 0
        if default_path in clean_files:
            def_idx = clean_files.index(default_path)
        sel_clean = st.selectbox(
            "Cleaned CSV",
            clean_files,
            index=def_idx,
            format_func=lambda p: p.name,
        )
    else:
        # Fallback to the session path if the glob missed it
        sel_clean = default_path
    # Persist selection
    if sel_clean:
        st.session_state.cleaned_csv_path = str(sel_clean)
        st.caption(f"Using cleaned CSV: `{Path(st.session_state.cleaned_csv_path).name}`")
else:
    st.info("Upload a CSV and run preprocessing to unlock this step.")

# --- 3) Train model (XGBoost) ---
st.header("3) Train model (XGBoost)")
t1, _ = st.columns([3,2])
sel_clean_path = Path(st.session_state.cleaned_csv_path) if st.session_state.cleaned_csv_path else None
if sel_clean_path and t1.button("Start training", use_container_width=True):
    try:
        jid = client.start_train(str(sel_clean_path))
        with st.spinner("Training..."):
            res = client.poll_train(jid)
        if res.get("state") == "succeeded":
            st.success("✅ Training complete!")
            render_train_metrics(res.get("report", {}) or {})
            st.caption(f"Model saved:  {res.get('model_path')}")
            if res.get("report_path"): st.caption(f"Report: {res.get('report_path')}")
            # Gate inference on successful training
            st.session_state.model_ready = True
            if res.get("model_path"):
                st.session_state.model_path = res.get("model_path")
        else:
            st.error(f"Training failed: {res.get('error')}")
            if res.get("trace"): st.code(res["trace"]) 
    except Exception as e:
        st.error(f"Train error: {e}")
elif not sel_clean_path:
    st.info("Pick a cleaned CSV to enable training.")


# --- 4) Inference (gated until a trained model exists) ---
st.header("4) Inference")
if st.session_state.model_ready:
    # 4A) Predict from manual inputs (single record)
    st.subheader("4A) Predict from manual inputs (single record)")
    models = list_paths(MODEL_DIR, "*.joblib")
    # Prefer the most recently trained model if available
    default_model_idx = 0
    if models and getattr(st.session_state, "model_path", None):
        try:
            default_model_idx = models.index(Path(st.session_state.model_path))
        except ValueError:
            default_model_idx = 0
    model_choice = st.selectbox(
        "Model (.joblib)",
        models,
        index=default_model_idx if models else 0,
        format_func=lambda p: p.name,
        key="inf_model_form",
    ) if models else None

    art = load_artifacts()
    sel_clean_for_schema = Path(st.session_state.cleaned_csv_path) if st.session_state.cleaned_csv_path else None
    schema = build_feature_schema(art.get("enc",{}) or {}, art.get("meta",{}) or {}, sel_clean_for_schema)

    if schema["source"] == "encoders":
        st.caption("Fields built from preprocessing artifacts.")
    elif schema["source"] == "meta+csv":
        st.caption("Fields built from final_columns + cleaned CSV dtypes.")
    elif schema["source"] == "meta":
        st.caption("No CSV sample — rendering text fields from final_columns.")
    elif schema["source"] == "csv":
        st.caption("No artifacts — fields inferred directly from the cleaned CSV.")
    else:
        st.caption("No artifacts/CSV available; form will be minimal.")

    with st.form("manual_inference_form", clear_on_submit=False):
        # rec: Dict[str,Any] = {}
        # for fld in schema["fields"]:
        #     name = fld["name"]; kind = fld["kind"]
        #     if kind == "numeric":
        #         rec[name] = st.number_input(name, key=f"num_{name}")
        #     elif kind == "checkbox":
        #         rec[name] = st.checkbox(name, key=f"chk_{name}", value=False)
        #     elif kind == "select":
        #         options = fld.get("options", [])
        #         rec[name] = st.selectbox(name, options, key=f"sel_{name}") if options else st.text_input(name, key=f"txt_{name}")
        #     else:  # text
        #         rec[name] = st.text_input(name, key=f"txt_{name}", help=fld.get("hint"))
        # submitted = st.form_submit_button("Run inference (manual)")
        rec: Dict[str, Any] = {}
        for fld in schema["fields"]:
            name = fld["name"]; kind = fld["kind"]
            if kind == "numeric":
                rec[name] = st.number_input(name, key=f"num_{name}")
            elif kind == "checkbox":
                rec[name] = 1 if st.checkbox(name, key=f"chk_{name}", value=False) else 0   # <-- CHANGED
            elif kind == "select":
                options = fld.get("options", [])
                rec[name] = st.selectbox(name, options, key=f"sel_{name}") if options else st.text_input(name, key=f"txt_{name}")
            else:  # text
                rec[name] = st.text_input(name, key=f"txt_{name}", help=fld.get("hint"))
        submitted = st.form_submit_button("Run inference (manual)")

    if submitted:
        if not model_choice:
            st.warning("Select a model first.")
        else:
            cleaned: Dict[str,Any] = {}
            for k, v in rec.items():
                if isinstance(v, str):
                    if v.strip()=="":
                        continue
                    nv = normalize_num(v)
                    cleaned[k] = nv if nv is not None else v
                else:
                    cleaned[k] = int(v) if isinstance(v, bool) else v
            try:
                with st.spinner("Running model inference..."):
                    resp = infer_records(str(model_choice), cleaned, top_k=3)
                st.success(f"✅ Model inference complete! Predictions saved to: {resp.get('predictions_path')}")
                samp = resp.get("sample") or []
                if samp:
                    st.caption("Sample prediction")
                    st.dataframe(pd.DataFrame(samp), use_container_width=True)
            except Exception as e:
                st.error(f"Inference error: {e}")

else:
    st.info("Train a model to enable inference.")

# --- 5) artifacts & downloads ---
st.header("5) Artifacts")
cols = st.columns(4)
with cols[0]:
    st.subheader("Cleaned CSVs")
    files = list_paths(CLEAN_DIR, "*.csv")
    if files:
        sel = st.selectbox("Choose CSV", files, format_func=lambda p:p.name, key="dl_csv")
        if sel.exists():
            with open(sel,"rb") as f: st.download_button("Download CSV", f.read(), file_name=sel.name, mime="text/csv", use_container_width=True)
    else: st.write("No files.")
with cols[1]:
    st.subheader("Models")
    files = list_paths(MODEL_DIR, "*.joblib")
    if files:
        sel = st.selectbox("Choose model", files, format_func=lambda p:p.name, key="dl_model")
        if sel.exists():
            with open(sel,"rb") as f: st.download_button("Download model", f.read(), file_name=sel.name, mime="application/octet-stream", use_container_width=True)
    else: st.write("No models.")
with cols[2]:
    st.subheader("Training reports")
    files = list_paths(ARTIFACTS_DIR, "training_report_*.json")
    if files:
        sel = st.selectbox("Choose report", files, format_func=lambda p:p.name, key="dl_report")
        if sel.exists():
            with open(sel,"rb") as f: st.download_button("Download report", f.read(), file_name=sel.name, mime="application/json", use_container_width=True)
    else: st.write("None yet.")
with cols[3]:
    st.subheader("Predictions")
    files = list_paths(PREDICTIONS_DIR, "*.csv")
    if files:
        sel = st.selectbox("Choose predictions", files, format_func=lambda p:p.name, key="dl_preds")
        if sel.exists():
            with open(sel,"rb") as f: st.download_button("Download predictions", f.read(), file_name=sel.name, mime="text/csv", use_container_width=True)
    else: st.write("None yet.")

st.caption("Tip: Upload → Preprocess → Train before running inference. ")
