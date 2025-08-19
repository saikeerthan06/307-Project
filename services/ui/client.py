# ui/client.py
import os, time, requests

PREPROC_URL = os.getenv("PREPROC_URL", "http://data-preprocessing-svc:8000")
TRAIN_URL   = os.getenv("TRAIN_URL",   "http://model-training-svc:8000")
INFER_URL   = os.getenv("INFER_URL",   "http://model-inference-svc:8000")

RAW_DIR   = os.getenv("RAW_DIR",   "/shared/data/raw")
CLEAN_DIR = os.getenv("CLEAN_DIR", "/shared/data/clean")
MODEL_DIR = os.getenv("MODEL_DIR", "/shared/models")

class BackendClient:
    def __init__(self, timeout=10):
        self.timeout = timeout

    # ---------- PREPROCESS ----------
    def start_preprocess(self, input_path: str, **overrides):
        # Ensure absolute path under RAW_DIR (UI writes uploads here)
        if not input_path.startswith(RAW_DIR):
            raise ValueError(f"input_path must be under {RAW_DIR}, got {input_path}")

        payload = {
            "input_path": input_path,
            "drop_duplicates": True,
            "impute_numeric": False,
            "impute_categorical": False,
            "encode_target": True,
            "encode_categorical": "mixed",   # one-hot Urine Test, ordinal 3-levels, 0/1 binaries
            "scale_numeric": "robust",       # RobustScaler for your dataset
            "persist_artifacts": True,
            "target_column": "Target",
        }
        payload.update(overrides or {})

        # IMPORTANT: send JSON (NOT form data)
        r = requests.post(f"{PREPROC_URL}/preprocess", json=payload, timeout=self.timeout)
        r.raise_for_status()
        js = r.json()
        return js.get("job_id")

    def poll_preprocess(self, job_id: str, wait_s: float = 0.8, timeout_s: int = 300):
        import time as _t
        deadline = _t.time() + timeout_s
        while _t.time() < deadline:
            r = requests.get(f"{PREPROC_URL}/preprocess/{job_id}", timeout=self.timeout)
            r.raise_for_status()
            js = r.json()
            state = js.get("state")
            if state in ("succeeded", "failed"):
                return js
            _t.sleep(wait_s)
        raise TimeoutError(f"preprocess job {job_id} did not finish within {timeout_s}s")

    # --- Train ---
    def start_train(self, input_path: str, **overrides):
        # MUST be an absolute path under CLEAN_DIR
        if not input_path.startswith(CLEAN_DIR):
            raise ValueError(f"input_path must be under {CLEAN_DIR}, got {input_path}")

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
                "objective": "multi:softprob"
            },
            "early_stopping_rounds": 20,
            "persist_metrics": True
        }
        payload.update(overrides or {})

        # IMPORTANT: send JSON, not form data
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
