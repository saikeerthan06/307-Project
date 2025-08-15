# ui/app.py
import os, io
import pandas as pd
import streamlit as st
from client import BackendClient, RAW_DIR, CLEAN_DIR

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
uploaded = st.file_uploader("Upload CSV", type=["csv"])
raw_path = None
if uploaded is not None:
    os.makedirs(RAW_DIR, exist_ok=True)
    raw_path = os.path.join(RAW_DIR, uploaded.name)
    with open(raw_path, "wb") as f:
        f.write(uploaded.getbuffer())
    st.success(f"Saved to {raw_path}")

    # preview
    try:
        df_preview = pd.read_csv(io.BytesIO(uploaded.getvalue()), nrows=10)
        st.dataframe(df_preview)
    except Exception as e:
        st.warning(f"Preview failed: {e}")

if st.button("Run Preprocessing", disabled=(raw_path is None)):
    with st.spinner("Preprocessing..."):
        job = client.start_preprocess(raw_path)
        result = client.poll_preprocess(job)
    if result["state"] == "succeeded":
        clean_path = result.get("output_path")
        st.session_state["clean_path"] = clean_path
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
with st.form("predict_form"):
    # Example feature fields; adjust to your dataset
    years = st.number_input("yearsExperience", min_value=0.0, step=0.5)
    dist  = st.number_input("distanceFromCBD", min_value=0.0, step=0.5)
    job   = st.text_input("jobRole", "engineer")
    edu   = st.text_input("education", "bachelor")
    major = st.text_input("major", "computer science")
    industry = st.text_input("industry", "tech")
    submitted = st.form_submit_button("Predict")

if submitted:
    features = {
        "yearsExperience": years,
        "distanceFromCBD": dist,
        "jobRole": job,
        "education": edu,
        "major": major,
        "industry": industry,
    }
    try:
        out = client.predict(features)
        st.success(f"Prediction: {out.get('prediction')}")
        st.caption(f"Model version: {out.get('model_version', 'unknown')}")
    except Exception as e:
        st.error(f"Prediction failed: {e}")
