# Hospital ML Kubernetes Cluster

This repository contains a sample machine-learning platform deployed on Kubernetes. It includes services for data preprocessing, model training, model inference, and a Streamlit-based UI. The cluster is secured with network policies and features a nightly backup CronJob. UI blue/green deployments allow zero-downtime upgrades.

## Components

### Core Services
- **Data Preprocessing** – prepares raw data for model training.
- **Model Training** – trains and updates models.
- **Model Inference** – serves predictions.
- **UI** – Streamlit frontend that calls the other services.

### Blue/Green UI Deployments
Two UI deployments (`ui-blue` and `ui-green`) run simultaneously. The `ui` Service routes traffic to the blue version by default. Switch versions with:

```bash
scripts/switch-ui-color.sh hospital-ml green   # or blue
```

### Operations
- **Nightly Backup CronJob** (`k8s/ops/cronjob-backup.yaml`) archives model artifacts and cleaned data.
- **Backup RBAC** (`k8s/ops/backup-rbac.yaml`) provides a dedicated service account with minimal permissions.

### Security
- **Default deny** and **DNS egress** network policies under `k8s/security/` lock down traffic to only what is explicitly allowed.
- Service-specific policies in `k8s/services/*/networkpolicy.yaml` allow required communication paths.

## Automation
Use `automation.sh` to build images, create the namespace, apply manifests (including ops and security resources), and wait for rollouts.

```bash
./automation.sh
```

The script also patches security contexts, supports an optional dev mode, and port-forwards the UI to `http://localhost:8501`.

## Switching UI Versions
Deployments for both colors are created automatically. Patch the Service selector to cut over:

```bash
scripts/switch-ui-color.sh hospital-ml green
```

## Backup and Restore
Backups are stored under `/shared/models/artifacts/backups` in the shared volume. To restore, extract the desired archive into the same path inside the PVC.

## Further Ideas
- Integrate monitoring (Prometheus/Grafana) and alerting.
- Add cleanup/retention logic for backups.
- Implement separate HPAs for `ui-blue` and `ui-green` in production.

