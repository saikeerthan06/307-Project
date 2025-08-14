# service.py
import os, json, uuid, traceback
from flask import Flask, request, jsonify
from preprocessing import preprocess

app = Flask(__name__)

JOBS = {}  # job_id -> {state, report, error}

@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200

# --- existing simple route (kept for compatibility) ---
@app.post("/run")
def run_once():
    payload = request.get_json(force=True, silent=True) or {}
    input_path   = payload.get("input_path", os.getenv("PREP_INPUT_PATH", "shared/data/diabetes_dataset00.csv"))
    output_dir   = payload.get("output_dir", os.getenv("PREP_OUTPUT_DIR", "shared/clean"))
    target       = payload.get("target", os.getenv("PREP_TARGET", "Target"))
    label_encode = str(payload.get("label_encode_target", os.getenv("PREP_LABEL_ENCODE_TARGET", "true"))).lower() == "true"
    report = preprocess(
        input_path=input_path, output_dir=output_dir, target=target,
        label_encode_target=label_encode, save_csv=True, save_xlsx=False, save_report=True
    )
    return jsonify(report), 200

# --- NEW: UI-friendly job API ---
@app.post("/preprocess")
def start_preprocess():
    payload = request.get_json(force=True, silent=True) or {}
    input_path   = payload.get("input_path", os.getenv("PREP_INPUT_PATH", "shared/data/diabetes_dataset00.csv"))
    output_dir   = payload.get("output_dir", os.getenv("PREP_OUTPUT_DIR", "shared/clean"))
    target       = payload.get("target", os.getenv("PREP_TARGET", "Target"))
    label_encode = str(payload.get("label_encode_target", os.getenv("PREP_LABEL_ENCODE_TARGET", "true"))).lower() == "true"

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"state": "running", "report": None, "error": None}
    try:
        report = preprocess(
            input_path=input_path, output_dir=output_dir, target=target,
            label_encode_target=label_encode, save_csv=True, save_xlsx=False, save_report=True
        )
        JOBS[job_id].update({"state": "succeeded", "report": report})
    except Exception as e:
        JOBS[job_id].update({"state": "failed", "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
    return jsonify({"job_id": job_id}), 200

@app.get("/preprocess/status")
def preprocess_status():
    job_id = request.args.get("job_id")
    if not job_id or job_id not in JOBS:
        return jsonify({"error": "job not found"}), 404
    return jsonify(JOBS[job_id]), 200

if __name__ == "__main__":
    # container exposes 8000 in K8s
    app.run(host="0.0.0.0", port=8000)
