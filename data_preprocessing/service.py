# service.py
import os, uuid, traceback
from flask import Flask, request, jsonify
from data_preprocessing.preprocessing import preprocess

app = Flask(__name__)

# job_id -> {state, report, error, trace, output_path}
JOBS = {}


@app.get("/healthz")
def healthz():
    # UI sidebar health badge depends on this
    return jsonify({"status": "ok"}), 200


@app.post("/preprocess")
def start_preprocess():
    """
    Starts a preprocessing job.
    Accepts both 'dataset_path' (UI) and 'input_path' (legacy) for compatibility.
    Returns: {"job_id": "..."}
    """
    payload = request.get_json(force=True, silent=True) or {}

    # Accept UI's key first; fall back to legacy/env; final fallback is a sane default
    input_path = (
        payload.get("dataset_path")
        or payload.get("input_path")
        or os.getenv("PREP_INPUT_PATH", "/shared/data/raw/diabetes_dataset00.csv")
    )

    # Align with UI's expected clean directory by default
    output_dir = payload.get("output_dir", os.getenv("PREP_OUTPUT_DIR", "/shared/data/clean"))

    # Optional knobs (kept compatible with your preprocessing.py)
    target = payload.get("target", os.getenv("PREP_TARGET", "Target"))
    label_encode = str(
        payload.get("label_encode_target", os.getenv("PREP_LABEL_ENCODE_TARGET", "true"))
    ).lower() in {"1", "true", "yes", "y"}

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"state": "running", "report": None, "error": None, "trace": None, "output_path": None}

    try:
        report = preprocess(
            input_path=input_path,
            output_dir=output_dir,
            target=target,
            label_encode_target=label_encode,
            save_csv=True,      # UI expects a path it can pass on
            save_xlsx=False,    # keep off unless you need it
            save_report=True
        )

        # Prefer CSV for the UI; fall back to xlsx if needed
        outputs = (report or {}).get("outputs", {}) if isinstance(report, dict) else {}
        output_path = outputs.get("X_csv") or outputs.get("X_xlsx")

        JOBS[job_id].update({
            "state": "succeeded",
            "report": report,
            "output_path": output_path
        })
    except Exception as e:
        JOBS[job_id].update({
            "state": "failed",
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc()
        })

    return jsonify({"job_id": job_id}), 200


@app.get("/preprocess/status")
def preprocess_status():
    """
    Returns the current state of a job:
      {"state": "running|succeeded|failed", "report": {...}, "output_path": "...", "error": "..."}
    The UI polls this and reads 'state' and 'output_path'.
    """
    job_id = request.args.get("job_id")
    if not job_id or job_id not in JOBS:
        return jsonify({"error": "job not found"}), 404
    return jsonify(JOBS[job_id]), 200


if __name__ == "__main__":
    # Bind to 0.0.0.0 for K8s, port 8000 to match other services
    app.run(host="0.0.0.0", port=8000)
