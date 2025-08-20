# ui/client.py
import os, time, requests

PREPROC_URL = os.getenv("PREPROC_URL", "http://data-preprocessing-svc:8000")
TRAIN_URL   = os.getenv("TRAIN_URL",   "http://model-training-svc:8000")
INFER_URL   = os.getenv("INFER_URL",   "http://model-inference-svc:8000")

RAW_DIR     = os.getenv("RAW_DIR",   "/shared/data/raw")
CLEAN_DIR   = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR   = os.getenv("MODEL_DIR", "/shared/models")
ART_DIR     = os.path.join(MODEL_DIR, "artifacts")

class BackendClient:
    def __init__(self, timeout=20):
        self.timeout = timeout

    # ---------- PREPROCESS ----------
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

    def poll_preprocess(self, job_id: str, wait_s=0.8, timeout_s=300):
        t_end = time.time() + timeout_s
        while time.time() < t_end:
            r = requests.get(f"{PREPROC_URL}/preprocess/{job_id}", timeout=self.timeout)
            r.raise_for_status()
            j = r.json()
            if j.get("state") in ("succeeded", "failed"):
                return j
            time.sleep(wait_s)
        raise TimeoutError(f"preprocess {job_id} timed out")

    # ---------- TRAIN ----------
    def start_train(self, input_path: str, **overrides):
        payload = {
            "input_path": input_path,
            "target_column": "Target",
            "test_size": 0.2,
        }
        payload.update(overrides or {})
        r = requests.post(f"{TRAIN_URL}/train", json=payload, timeout=self.timeout)
        r.raise_for_status()
        return r.json().get("job_id")

    def poll_train(self, job_id: str, wait_s=1.0, timeout_s=1200):
        t_end = time.time() + timeout_s
        while time.time() < t_end:
            r = requests.get(f"{TRAIN_URL}/train/{job_id}", timeout=self.timeout)
            r.raise_for_status()
            j = r.json()
            if j.get("state") in ("succeeded", "failed"):
                return j
            time.sleep(wait_s)
        raise TimeoutError(f"train {job_id} timed out")

    # ---------- INFER ----------
    def infer_records(self, model_path: str, records, top_k=3, records_are_raw=True):
        payload = {
            "model_path": model_path,
            "artifacts_dir": ART_DIR,
            "mode": "records",
            "records": records,
            "records_are_raw": bool(records_are_raw),
            "return_proba": True,
            "top_k": int(top_k),
            "save_predictions": True,
        }
        r = requests.post(f"{INFER_URL}/predict", json=payload, timeout=max(self.timeout, 60))
        r.raise_for_status()
        return r.json()

    def infer_csv(self, model_path: str, cleaned_csv_path: str, top_k=3):
        payload = {
            "model_path": model_path,
            "artifacts_dir": ART_DIR,
            "mode": "csv",
            "data_path": cleaned_csv_path,
            "return_proba": True,
            "top_k": int(top_k),
            "save_predictions": True,
        }
        r = requests.post(f"{INFER_URL}/predict", json=payload, timeout=max(self.timeout, 60))
        r.raise_for_status()
        return r.json()

    # --- Health (for UI badges) ---
    def healthy(self):
        ok = {}
        for name, base in [("preproc", PREPROC_URL), ("train", TRAIN_URL), ("infer", INFER_URL)]:
            try:
                ok[name] = requests.get(f"{base}/healthz", timeout=3).status_code == 200
            except Exception:
                ok[name] = False
        return ok
