#!/usr/bin/env bash

set -euo pipefail

# =========================================
# automation.sh  (UI + DP + MT + MI) — DEV-safe, clean quoting
# =========================================

# Images (bump UI so the latest app.py lands)
UI_IMAGE_TAG="${UI_IMAGE_TAG:-ui:1.1.0}"
DP_IMAGE_TAG="${DP_IMAGE_TAG:-data-preprocessing:2.0.0}"
MT_IMAGE_TAG="${MT_IMAGE_TAG:-model-training:1.0.2}"
MI_IMAGE_TAG="${MI_IMAGE_TAG:-model-inference:1.0.0}"

# Cluster config
NAMESPACE="${NAMESPACE:-hospital-ml}"
UI_DOMAIN="${UI_DOMAIN:-ui.localtest.me}"
DEV_MODE="${DEV_MODE:-true}"

# Paths inside pods
RAW_DIR_IN_POD="/shared/data/raw"
CLEAN_DIR_IN_POD="/shared/data/clean"
MODEL_DIR_IN_POD="/shared/models"
ARTIFACTS_DIR_IN_POD="/shared/models/artifacts"

# Optional dataset copy (host path)
DATASET_LOCAL_PATH="${DATASET_LOCAL_PATH:-./datasets/diabetes_dataset00.csv}"

# Local port-forward
ENABLE_PORT_FORWARD="${ENABLE_PORT_FORWARD:-true}"

# ---- Preflight
command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
command -v minikube >/dev/null || { echo "minikube not found"; exit 1; }
command -v docker  >/dev/null || { echo "docker not found"; exit 1; }

# ---- Minikube & ingress
if ! minikube status >/dev/null 2>&1; then
  minikube start
fi
minikube addons enable ingress >/dev/null

# ---- Build images in Minikube’s Docker
eval "$(minikube -p minikube docker-env)"

echo ">> Building images..."
pushd services/data_preprocessing >/dev/null; docker build -t "${DP_IMAGE_TAG}" .; popd >/dev/null
pushd services/model_training      >/dev/null; docker build -t "${MT_IMAGE_TAG}" .; popd >/dev/null
pushd services/model_inference     >/dev/null; docker build -t "${MI_IMAGE_TAG}" .; popd >/dev/null
pushd services/ui                  >/dev/null; docker build -t "${UI_IMAGE_TAG}" .; popd >/dev/null

# ---- Namespace + base
kubectl get ns "${NAMESPACE}" >/dev/null 2>&1 || kubectl create ns "${NAMESPACE}"
kubectl apply -n "${NAMESPACE}" -f k8s/base/ || true

# ---- Apply service manifests
kubectl apply -n "${NAMESPACE}" -f k8s/services/data-preprocessing/
kubectl apply -n "${NAMESPACE}" -f k8s/services/model-training/
kubectl apply -n "${NAMESPACE}" -f k8s/services/model-inference/
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui/configmap.yaml
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui/service.yaml
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui/ingress.yaml
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui/networkpolicy.yaml
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui/pdb.yaml
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui-blue/
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui-green/
kubectl apply -n "${NAMESPACE}" -f k8s/ops/
kubectl apply -n "${NAMESPACE}" -f k8s/security/

# ---- Set images
kubectl -n "${NAMESPACE}" set image deploy/data-preprocessing api="${DP_IMAGE_TAG}" || true
kubectl -n "${NAMESPACE}" set image deploy/model-training   api="${MT_IMAGE_TAG}" || true
kubectl -n "${NAMESPACE}" set image deploy/model-inference  api="${MI_IMAGE_TAG}" || true
kubectl -n "${NAMESPACE}" set image deploy/ui-blue         ui="${UI_IMAGE_TAG}"  || true
kubectl -n "${NAMESPACE}" set image deploy/ui-green        ui="${UI_IMAGE_TAG}"  || true

# ---- One-off PVC perms (defensive)
echo ">> Ensuring /shared perms (one-off)..."
kubectl -n "${NAMESPACE}" apply -f - >/dev/null <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: pvc-perm-fix
spec:
  restartPolicy: Never
  volumes:
    - name: shared
      persistentVolumeClaim: { claimName: shared-pvc }
  containers:
    - name: fix
      image: busybox:1.36
      command: ["sh","-c","mkdir -p /shared/data/raw /shared/data/clean /shared/models/artifacts/predictions && chown -R 1000:1000 /shared && chmod -R g+rwX /shared && echo done && sleep 1"]
      securityContext: { runAsUser: 0 }
      volumeMounts: [{ name: shared, mountPath: /shared }]
EOF
kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod/pvc-perm-fix --timeout=30s || true
kubectl -n "${NAMESPACE}" delete pod pvc-perm-fix --ignore-not-found >/dev/null 2>&1 || true

# ---- SecurityContext (pod + container) for all Deployments
echo ">> Enforcing pod & container securityContext..."
for DEP in data-preprocessing model-training model-inference ui-blue ui-green; do
  # Pod-level (strategic patch; no --type flag)
  kubectl -n "${NAMESPACE}" patch deploy "${DEP}" -p '{
    "spec": { "template": { "spec": {
      "securityContext": {
        "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000, "fsGroupChangePolicy": "Always"
      }
    } } }
  }' || true

  # Container-level (ui container is "ui", others are "api")
  CNAME="api"; [[ "${DEP}" == ui* ]] && CNAME="ui"
  kubectl -n "${NAMESPACE}" patch deploy "${DEP}" -p "{
    \"spec\": { \"template\": { \"spec\": {
      \"containers\": [ { \"name\": \"${CNAME}\", \"securityContext\": { \"runAsUser\": 1000, \"runAsGroup\": 1000 } } ]
    } } }
  }" || true
done

# ---- DEV mode: disable HPAs + Recreate + clean restart (no quoted args anywhere)
if [[ "${DEV_MODE}" == "true" ]]; then
  echo ">> DEV_MODE=true: disabling HPAs and using Recreate + single replica"
  kubectl -n "${NAMESPACE}" delete hpa data-preprocessing-hpa model-training-hpa model-inference-hpa --ignore-not-found

  # Use Recreate (and null rollingUpdate to satisfy schema)
  for DEP in data-preprocessing model-training model-inference ui-blue ui-green; do
    kubectl -n "${NAMESPACE}" patch deploy "${DEP}" -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}' || true
    kubectl -n "${NAMESPACE}" patch deploy "${DEP}" -p '{"spec":{"template":{"spec":{"terminationGracePeriodSeconds":10}}}}' || true
  done

  # Hard restart each deployment cleanly: scale 0 → delete pods/RS → scale 1
  for DEP in data-preprocessing model-training model-inference ui-blue ui-green; do
    echo ">> Recreate ${DEP}"
    kubectl -n "${NAMESPACE}" scale deploy/${DEP} --replicas=0 || true
    kubectl -n "${NAMESPACE}" wait --for=delete pod -l app=${DEP} --timeout=90s || true
    kubectl -n "${NAMESPACE}" delete rs -l app=${DEP} --ignore-not-found || true
    kubectl -n "${NAMESPACE}" scale deploy/${DEP} --replicas=1
  done
fi

# ---- Wait for rollouts
echo ">> Waiting for rollouts..."
kubectl -n "${NAMESPACE}" rollout status deploy/data-preprocessing --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deploy/model-training   --timeout=240s
kubectl -n "${NAMESPACE}" rollout status deploy/model-inference  --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deploy/ui-blue         --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deploy/ui-green        --timeout=180s

# ---- Optional dataset copy to RAW
if [[ -f "${DATASET_LOCAL_PATH}" ]]; then
  echo ">> Copying dataset to RAW..."
  DP_POD="$(kubectl -n "${NAMESPACE}" get pods -l app=data-preprocessing -o jsonpath='{.items[0].metadata.name}')"
  kubectl -n "${NAMESPACE}" exec "${DP_POD}" -- mkdir -p "${RAW_DIR_IN_POD}"
  kubectl -n "${NAMESPACE}" cp "${DATASET_LOCAL_PATH}" "${DP_POD}:${RAW_DIR_IN_POD}/$(basename "${DATASET_LOCAL_PATH}")"
fi

# ---- Health checks
echo ">> Health checks"
kubectl -n "${NAMESPACE}" run curl-dp --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS data-preprocessing-svc:8000/healthz || true
kubectl -n "${NAMESPACE}" run curl-mt --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS model-training-svc:8000/healthz || true
kubectl -n "${NAMESPACE}" run curl-mi --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS model-inference-svc:8000/healthz || true

# ---- Smoke predict (best-effort)
echo ">> Smoke prediction (best-effort)"
MI_POD="$(kubectl -n "${NAMESPACE}" get pods -l app=model-inference -o jsonpath='{.items[0].metadata.name}')"
MODEL_PATH="$(kubectl -n "${NAMESPACE}" exec "${MI_POD}" -- sh -lc "ls -1 ${MODEL_DIR_IN_POD}/*.joblib 2>/dev/null | head -n1" || true)"
CLEAN_PATH="$(kubectl -n "${NAMESPACE}" exec "${MI_POD}" -- sh -lc "ls -1 ${CLEAN_DIR_IN_POD}/*_clean.csv 2>/dev/null | head -n1" || true)"
if [[ -n "${MODEL_PATH}" && -n "${CLEAN_PATH}" ]]; then
  kubectl -n "${NAMESPACE}" run curl-mi-s --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
    curl -sS -X POST model-inference-svc:8000/predict \
      -H 'Content-Type: application/json' \
      -d "{\"model_path\":\"${MODEL_PATH}\",\"artifacts_dir\":\"${ARTIFACTS_DIR_IN_POD}\",\"mode\":\"csv\",\"data_path\":\"${CLEAN_PATH}\",\"return_proba\":true,\"top_k\":3}" || true
fi

# ---- Ingress mapping (optional)
MINIKUBE_IP="$(minikube ip)"
if kubectl get ingress -n "${NAMESPACE}" ui >/dev/null 2>&1; then
  if ! grep -q "${UI_DOMAIN}" /etc/hosts; then
    echo "${MINIKUBE_IP} ${UI_DOMAIN}" | sudo tee -a /etc/hosts >/dev/null
  fi
fi

# ---- Port-forward UI
if [[ "${ENABLE_PORT_FORWARD}" == "true" ]]; then
  echo ">> Port-forward UI -> http://localhost:8501"
  if [[ -f /tmp/ui_pf.pid ]] && ps -p "$(cat /tmp/ui_pf.pid)" >/dev/null 2>&1; then
    kill "$(cat /tmp/ui_pf.pid)" || true; rm -f /tmp/ui_pf.pid
  fi
  (kubectl -n "${NAMESPACE}" port-forward svc/ui 8501:80 >/tmp/ui_pf.log 2>&1 &) && echo $! >/tmp/ui_pf.pid
fi

echo
echo "=============================================="
echo "✅ Deploy complete!"
echo "UI: http://localhost:8501"
echo "DP: data-preprocessing-svc:8000"
echo "MT: model-training-svc:8000"
echo "MI: model-inference-svc:8000"
echo "=============================================="
