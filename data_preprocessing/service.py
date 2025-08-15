import os, json
from flask import Flask, request, jsonify
from preprocessing import preprocess

app = Flask(__name__)

@app.get("/healthz")
def healthz():
    return jsonify({"status":"ok"}), 200

@app.post("/run")
def run():
    payload = request.get_json(force=True, silent=True) or {}
    input_path  = payload.get("input_path", "shared/data/diabetes_dataset00.csv")
    output_dir  = payload.get("output_dir", os.getenv("PREP_OUTPUT_DIR","shared/clean"))
    target      = payload.get("target", os.getenv("PREP_TARGET","Target"))
    label_encode= str(payload.get("label_encode_target", os.getenv("PREP_LABEL_ENCODE_TARGET","true"))).lower()=="true"
    report = preprocess(
        input_path=input_path, output_dir=output_dir, target=target,
        label_encode_target=label_encode, save_csv=True, save_xlsx=False, save_report=True
    )
    return jsonify(report), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)