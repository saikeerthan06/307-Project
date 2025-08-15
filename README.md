# EGT307 - AI Application Development README File 
---------------------------------------------------------------------------


## 👥 The Team

| Name                  | Contribution (Modules)  |
| --------------------- | ----------------------- |
| **Sai Keerthan** (Leader) | Model Training        |
| **Xiu Wen**           | User Interface           |
| **Wei Xuan**          | Data Pre-Processing      |
| **Hao Wem**           | Model Inference          |

---

## 🏗 Architecture

```text
┌───────────────────────────────────────────────────────────┐
│                 Namespace: hospital-ml                    │
│                                                           │
│  User (Lecturer)                                           │
│    ┌──────────┐  HTTP►  ┌────────────────┐                 │
│    │   UI     │         │ model_inference│                 │
│    │(Streamlit)         │    (Flask)     │                 │
│    └────┬─────┘         └───────┬────────┘                 │
│         │                       ▲                          │
│         │ Train/Status          │ Predict                  │
│         ▼                       │                          │
│    ┌──────────────┐  HTTP► ┌────┴───────────┐               │
│    │ data_preproc │       │ model_training │               │
│    │    (Flask)   │ ◄──── │    (Flask)     │               │
│    └──────┬───────┘       └────────────────┘               │
│           │ (reads/writes shared artifacts)                 │
│    ┌──────▼─────────────────────────────────────────┐      │
│    │                     PVC                         │      │
│    │ /app/shared/data    (raw .csv)                  │      │
│    │ /app/shared/clean   (X/y cleaned)               │      │
│    │ /app/shared/models  (trained_model.pkl etc.)    │      │
│    └────────────────────────────────────────────────┘      │
│                                                           │
│  NetworkPolicy: only UI can call model_training            │
│  Secret: TRAIN_AUTH_TOKEN required for /train              │
└───────────────────────────────────────────────────────────┘


## Project Structure | Repository Layout 

307-Project/
├─ data_preprocessing/               # Preprocessing library & CLI
│  └─ preprocessing.py
├─ model_training/                   # Training API + CLI
│  ├─ app.py
│  ├─ Dockerfile
│  └─ requirements.txt
├─ model_inference/                  # Inference service
│  ├─ app.py
│  ├─ Dockerfile
│  └─ requirements.txt
├─ ui/                               # UI service
│  ├─ app.py
│  ├─ Dockerfile
│  └─ requirements.txt
├─ shared/                           # Local datasets & artifacts (also mirrored in PVC)
│  ├─ data/
│  ├─ clean/
│  └─ models/
└─ k8s/
   ├─ 00-namespace-rbac.yaml         # Namespace + RBAC
   ├─ 01-config-secret.yaml          # Config + token
   ├─ 02-pvc.yaml                    # PersistentVolumeClaim
   ├─ 03-deployment-service.yaml     # Deployment/Service
   ├─ 04-networkpolicy.yaml          # Network policies
   └─ 05-cronjob-train.yaml          # Nightly retrain job


---

## Module Details

### 1) UI (Streamlit)
- Lets the lecturer:
  - **Enter patient features** and request predictions from `model_inference`.
  - **Trigger model training** by sending a `POST /train` request to `model_training`.
  - **View model metrics** via `GET /metrics` from `model_training`.
- **K8s:** UI has its own Deployment/Service and should have the label `app: ui` so NetworkPolicies can allow communication with `model_training`.

---

### 2) data_preprocessing (Flask / Library)
- **File:** `data_preprocessing/preprocessing.py`
- **Role:** Performs all preprocessing.
- **Capabilities:**
  - Removes invalid ages (`Age == 0`).
  - Optional IQR-based outlier removal.
  - Missing value imputation.
  - Scaling for numeric features.
  - One-hot encoding of categorical features (stable column ordering).
  - Saves cleaned datasets, feature names, and preprocessing reports to `shared/clean`.
- **K8s:** Can be deployed as a separate microservice or imported directly by `model_training` for faster execution.

---

### 3) model_training (Flask + CLI)
- **File:** `model_training/app.py`
- **Modes:**
  - **Server**:
    - `GET /healthz` → Returns `{status: "ok", modelReady: bool}`
    - `GET /metrics` → Returns training metrics from `metrics.json`
    - `POST /train` → Triggers preprocessing and training (secured via bearer token)
  - **Train**: CLI mode for local runs or CronJobs (preprocess → train → save → print summary)
- **Security:**
  - Uses `TRAIN_AUTH_TOKEN` from a Kubernetes Secret to secure `/train`.
  - NetworkPolicy restricts `/train` access to UI pods only.
- **Artifacts Saved:**
  - `trained_model.pkl`
  - `metrics.json`
  - `preprocessing_report.json`
  - Preprocessed `X` and `y` datasets

---

### 4) model_inference (Flask)
- **Role:** Loads the trained model and provides predictions.
- **Ensures:** Feature order in predictions matches training order by loading `*_feature_names.json`.
- **Endpoints:**
  - `GET /healthz`
  - `POST /predict` → Takes JSON features and returns predictions + probabilities.
- **K8s:** Deployed as its own Deployment/Service. Can be horizontally scaled using HPA.

---

## Kubernetes YAML Files

### `k8s/00-namespace-rbac.yaml`
Defines the project namespace and RBAC permissions.

### `k8s/01-config-secret.yaml`
Stores environment variables and secrets like `TRAIN_AUTH_TOKEN`.

### `k8s/02-pvc.yaml`
PersistentVolumeClaim to store shared data between pods (models, metrics, cleaned data).

### `k8s/03-deployment-service.yaml`
Defines Deployments and Services for each module, ensuring they are discoverable in the cluster.

### `k8s/04-networkpolicy.yaml`
Restricts inter-pod communication:
- UI → Training: Allowed
- Inference → UI: Allowed
- External → UI and Inference: Allowed
- All other traffic: Denied

### `k8s/05-cronjob-train.yaml`
Schedules automatic model retraining (e.g., nightly) using `app.py --mode train`.

