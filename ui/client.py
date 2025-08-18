# ui/client.py
import os, time, requests

# Default to ClusterIP Service names (override via env in the UI Deployment if needed)
PREPROC_URL = os.getenv("PREPROC_URL", "http://data-preprocessing-svc:8000")
TRAIN_URL   = os.getenv("TRAIN_URL",   "http://model-training-svc:8000")
INFER_URL   = os.getenv("INFER_URL",   "http://model-inference-svc:8000")

RAW_DIR   = os.getenv("RAW_DIR",   "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR = os.getenv("MODEL_DIR", "/shared/models")

class BackendClient:
    def __init__(self, timeout=10):
        self.timeout = timeout

    # --- Preprocess ---
    def start_preprocess(self, dataset_path: str) -> str:
        r = requests.post(f"{PREPROC_URL}/preprocess",
                          json={"dataset_path": dataset_path}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["job_id"]

    def poll_preprocess(self, job_id: str, poll_interval=2):
        while True:
            r = requests.get(f"{PREPROC_URL}/preprocess/status", params={"job_id": job_id}, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
            if payload["state"] in ("succeeded", "failed"):
                return payload
            time.sleep(poll_interval)

    # --- Train ---
    def start_train(self, data_path: str) -> str:
        r = requests.post(f"{TRAIN_URL}/train", json={"data_path": data_path}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["job_id"]

    def poll_train(self, job_id: str, poll_interval=3):
        while True:
            r = requests.get(f"{TRAIN_URL}/train/status", params={"job_id": job_id}, timeout=self.timeout)
            r.raise_for_status()
            payload = r.json()
            if payload["state"] in ("succeeded", "failed"):
                return payload
            time.sleep(poll_interval)

    # --- Inference ---
    def predict(self, features: dict):
        r = requests.post(f"{INFER_URL}/predict", json={"features": features}, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # --- Health checks (optional UI badges) ---
    def healthy(self):
        ok = {}
        for name, base in [("preproc", PREPROC_URL), ("train", TRAIN_URL), ("infer", INFER_URL)]:
            try:
                r = requests.get(f"{base}/healthz", timeout=3)
                ok[name] = (r.status_code == 200)
            except Exception:
                ok[name] = False
        return ok
