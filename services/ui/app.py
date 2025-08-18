# ui/app.py
import os, io
import pandas as pd
import streamlit as st
from client import BackendClient, RAW_DIR, CLEAN_DIR
from pathlib import Path

# Default columns fallback (used when no CSV is uploaded yet)
DEFAULT_COLUMNS = [
    "Genetic Markers",
    "Autoantibodies",
    "Family History",
    "Environmental Factors",
    "Insulin Levels",
    "Age",
    "BMI",
    "Physical Activity",
    "Dietary Habits",
    "Blood Pressure",
    "Cholesterol Levels",
    "Waist Circumference",
    "Blood Glucose Levels",
    "Ethnicity",
    "Socioeconomic Factors",
    "Smoking Status",
    "Alcohol Consumption",
    "Glucose Tolerance Test",
    "History of PCOS",
    "Previous Gestational Diabetes",
    "Pregnancy History",
    "Weight Gain During Pregnancy",
    "Pancreatic Health",
    "Pulmonary Function",
    "Cystic Fibrosis Diagnosis",
    "Steroid Use History",
    "Genetic Testing",
    "Neurological Assessments",
    "Liver Function Tests",
    "Digestive Enzyme Levels",
    "Urine Test",
    "Birth Weight",
    "Early Onset Symptoms",
]

# --- Helpers ---
def load_schema_from_csv(path: str, max_unique_for_select: int = 30):
    import pandas as _pd
    try:
        df = _pd.read_csv(path)
    except Exception:
        return []
    schema = []
    for col in df.columns:
        # skip common target names
        if str(col).strip().lower() in {"target", "label", "y"}:
            continue
        dtype = str(df[col].dtype)
        if dtype.startswith("int") or dtype.startswith("float"):
            # numeric input: use observed min/max for sensible bounds
            try:
                cmin = float(_pd.to_numeric(df[col], errors="coerce").min())
                cmax = float(_pd.to_numeric(df[col], errors="coerce").max())
            except Exception:
                cmin, cmax = 0.0, 0.0
            step = 1.0 if dtype.startswith("int") else 0.1
            schema.append({"name": col, "kind": "number", "min": cmin, "max": cmax, "step": step})
        else:
            # categorical/text: prefer selectbox for small cardinality
            uniques = (
                df[col]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )
            uniques = sorted(uniques)
            if 0 < len(uniques) <= max_unique_for_select:
                schema.append({"name": col, "kind": "select", "options": uniques, "default": uniques[0] if uniques else ""})
            else:
                schema.append({"name": col, "kind": "text", "default": ""})
    return schema

st.set_page_config(page_title="EGT307 – ML UI", layout="wide")
st.title("EGT307 – End‑to‑End ML (UI)")

client = BackendClient()

# --- Service health badges ---
with st.sidebar:
    st.header("Services")
    health = client.healthy()
    for k, v in health.items():
        st.write(("✅ " if v else "❌ ") + k)

st.markdown("### A) Upload & Preprocess")

# Ensure shared dirs exist and show where we save
try:
    Path(RAW_DIR).mkdir(parents=True, exist_ok=True)
    Path(CLEAN_DIR).mkdir(parents=True, exist_ok=True)
except Exception as _mkerr:
    st.warning(f"Could not ensure shared dirs exist: {RAW_DIR}, {CLEAN_DIR} → {_mkerr}")

st.caption(f"RAW_DIR: {RAW_DIR}  |  CLEAN_DIR: {CLEAN_DIR}")

uploaded = st.file_uploader("Upload CSV", type=["csv"])
raw_path = None

# 1) If user uploads a CSV now, persist it to the shared RAW_DIR
if uploaded is not None:
    dest_path = Path(RAW_DIR) / uploaded.name
    try:
        with open(dest_path, "wb") as f:
            f.write(uploaded.getbuffer())
        st.success(f"Saved to {dest_path}")
        raw_path = str(dest_path)
        st.session_state["raw_path"] = raw_path
        # preview
        try:
            df_preview = pd.read_csv(dest_path).head(10)
            st.dataframe(df_preview)
            # store schema for manual prediction form
            st.session_state["schema"] = load_schema_from_csv(str(dest_path))
        except Exception as e:
            st.warning(f"Preview failed: {e}")
    except Exception as e:
        st.error(f"Failed to save upload to {dest_path}: {e}")

# 2) If nothing uploaded this run, allow choosing an existing CSV from RAW_DIR
if raw_path is None:
    try:
        existing = sorted([p for p in Path(RAW_DIR).glob("*.csv")])
    except Exception:
        existing = []
    if existing:
        default_idx = 0
        # prefer previously used file from session if present
        prev = st.session_state.get("raw_path")
        if prev:
            for i, p in enumerate(existing):
                if str(p) == prev:
                    default_idx = i
                    break
        choice = st.selectbox("Or pick an existing CSV from RAW_DIR", existing, index=default_idx, format_func=lambda p: p.name)
        if choice:
            raw_path = str(choice)
            st.session_state["raw_path"] = raw_path
            st.info(f"Selected: {raw_path}")
            try:
                df_preview = pd.read_csv(choice).head(10)
                st.dataframe(df_preview)
                st.session_state["schema"] = load_schema_from_csv(str(choice))
            except Exception as e:
                st.warning(f"Preview failed: {e}")
    else:
        st.info("No CSVs found in RAW_DIR yet. Upload one above.")

# 3) Kick off preprocessing using the resolved path (from upload or picker or session)
resolved_raw_path = st.session_state.get("raw_path", raw_path)

if st.button("Run Preprocessing", disabled=(not resolved_raw_path)):
    if not resolved_raw_path:
        st.error("No input file path found. Please upload or select a CSV in RAW_DIR.")
    elif not Path(resolved_raw_path).exists():
        st.error(f"File not found: {resolved_raw_path}. Make sure the UI saved it under RAW_DIR.")
    else:
        with st.spinner("Preprocessing..."):
            job = client.start_preprocess(resolved_raw_path)
            result = client.poll_preprocess(job)
        if result.get("state") == "succeeded":
            clean_path = result.get("output_path")
            st.session_state["clean_path"] = clean_path
            # refresh schema from cleaned dataset if available
            if clean_path:
                st.session_state["schema"] = load_schema_from_csv(clean_path)
            st.success(f"Preprocess done → {clean_path}")
        else:
            st.error(f"Preprocess failed: {result}")

st.markdown("---")
st.markdown("### B) Train Model")
clean_path = st.text_input("Cleaned data path", value=st.session_state.get("clean_path", ""))
if st.button("Train", disabled=(not clean_path)):
    with st.spinner("Training..."):
        job = client.start_train(clean_path)
        result = client.poll_train(job)
    if result["state"] == "succeeded":
        st.session_state["model_path"] = result.get("model_path", "")
        st.success(f"Training complete. Model: {st.session_state['model_path']}")
        metrics = result.get("metrics", {})
        if metrics:
            st.json(metrics)
    else:
        st.error(f"Training failed: {result}")

st.markdown("---")
st.markdown("### C) Predict (Manual)")

# Determine schema priority: cleaned path → uploaded raw → existing session
schema = st.session_state.get("schema", [])

# If user typed a cleaned path manually later, try to (re)load schema lazily
if not schema and st.session_state.get("clean_path"):
    schema = load_schema_from_csv(st.session_state["clean_path"]) or []
    st.session_state["schema"] = schema

# If still empty, try raw path (from upload)
if not schema and st.session_state.get("raw_path"):
    schema = load_schema_from_csv(st.session_state["raw_path"]) or []
    st.session_state["schema"] = schema

# Final fallback: hardcoded default columns so fields are ALWAYS visible
if not schema:
    schema = [{"name": c, "kind": "text", "default": ""} for c in DEFAULT_COLUMNS]
    st.session_state["schema"] = schema

with st.form("predict_form"):
    features = {}
    # Force TEXT INPUTS for every column (excluding target), as requested
    for field in schema:
        name = field.get("name") if isinstance(field, dict) else str(field)
        # fall back to empty string as default
        value = st.text_input(name, (field.get("default", "") if isinstance(field, dict) else ""))
        features[name] = value

    submitted = st.form_submit_button("Predict")

if submitted:
    try:
        out = client.predict(features)
        pred = out.get("prediction")
        st.success(f"Prediction: {pred}")
        st.caption(f"Model version: {out.get('model_version', 'unknown')}")
        # Optionally echo the sent features for transparency
        with st.expander("Sent features"):
            st.json(features)
    except Exception as e:
        st.error(f"Prediction failed: {e}")
