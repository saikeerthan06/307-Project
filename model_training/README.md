# README File for Model Training Container. Developed by Satini Sai Keerthan (232594T)

# Model Training Microservice (Diabetes Prediction) — Docker + Kubernetes

Hospital-ready training service that:
- preprocesses the diabetes dataset,
- trains an XGBoost classifier,
- saves model + metrics,
- exposes health/metrics endpoints,
- is secured behind in-cluster access and an auth token.

This repo contains:
- **model_training/app.py** — Flask API and CLI (`--mode server|train`)
- **data_preprocessing/preprocessing.py** — the *only* place where preprocessing happens
- **model_training/Dockerfile** — container image
- **k8s/** — Kubernetes manifests (namespace, RBAC, ConfigMap/Secret, PVC, Deployment, Service, NetworkPolicy, CronJob)

---

## 1) How the code works

### 1.1 `data_preprocessing/preprocessing.py`
- Programmatic API:
  - `preprocess(input_path, output_dir, target, scaling, drop_age_zero, iqr_enabled, iqr_k, label_encode_target, save_csv, save_xlsx, save_report) -> dict`
- Steps:
  - Loads CSV/XLSX, drops invalid `Age==0`, optional IQR outlier filtering.
  - Splits `X` (features) / `y` (target), encodes categoricals via OHE with **stable feature order**.
  - Optional label encoding of target.
  - Saves artifacts in `shared/clean`: `*_X.csv`, `*_y.csv`, `*_feature_names.json`, `*_report.json`.
- Returns a JSON-able **report** including output paths and label classes.

### 1.2 `model_training/app.py`
- Imports the preprocessor and **delegates all preprocessing** to it.
- **Server mode** (`--mode server` - default)
  - `GET /healthz` → `{status: ok, modelReady: bool}`
  - `GET /metrics` → returns last `metrics.json` if present.
  - `POST /train` → Triggers preprocessing + training on an uploaded CSV or default dataset.
    - **Security**: if `TRAIN_AUTH_TOKEN` env-var is set, requires header `Authorization: Bearer <token>`.
- **Train mode** (`--mode train`)
  - One-shot CLI that runs preprocess → train → save → prints a short summary.
- Saves artifacts in `shared/models`: `trained_model.pkl`, `metrics.json`, `preprocessing_report.json`.

### 1.3 Security snippet (hospital)
```python
TRAIN_AUTH_TOKEN = os.getenv("TRAIN_AUTH_TOKEN")
@app.before_request
def _guard_train():
    if request.path.startswith("/train"):
        if not TRAIN_AUTH_TOKEN:
            return
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth.split(" ", 1)[1] != TRAIN_AUTH_TOKEN:
            return jsonify({"error": "unauthorized"}), 401