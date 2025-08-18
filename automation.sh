# #!/usr/bin/env bash
# set -euo pipefail

# # ================================
# # automation.sh  (UI + Data-Preprocessing + Model-Training)
# # Minikube one-shot deploy with perms + routing fixes baked-in
# # ================================

# # ---- Config (edit if you change tags/paths) ----
# UI_IMAGE_TAG="${UI_IMAGE_TAG:-ui:1.0.1}"
# DP_IMAGE_TAG="${DP_IMAGE_TAG:-data-preprocessing:2.0.0}"
# MT_IMAGE_TAG="${MT_IMAGE_TAG:-model-training:1.0.0}"

# NAMESPACE="${NAMESPACE:-hospital-ml}"
# PVC_NAME="${PVC_NAME:-shared-pvc}"
# UI_DOMAIN="${UI_DOMAIN:-ui.localtest.me}"                # Ingress host (optional)

# DATASET_LOCAL_PATH="${DATASET_LOCAL_PATH:-./datasets/diabetes_dataset00.csv}"  # optional copy to RAW
# RAW_DIR_IN_POD="/shared/data/raw"
# CLEAN_DIR_IN_POD="/shared/data/clean"

# ENABLE_PORT_FORWARD="${ENABLE_PORT_FORWARD:-true}"       # start PF to http://localhost:8501

# echo "==> Using config:"
# echo "    NAMESPACE=${NAMESPACE}"
# echo "    UI_IMAGE_TAG=${UI_IMAGE_TAG}"
# echo "    DP_IMAGE_TAG=${DP_IMAGE_TAG}"
# echo "    MT_IMAGE_TAG=${MT_IMAGE_TAG}"
# echo "    UI_DOMAIN=${UI_DOMAIN}"
# echo "    DATASET_LOCAL_PATH=${DATASET_LOCAL_PATH}"
# echo "    ENABLE_PORT_FORWARD=${ENABLE_PORT_FORWARD}"
# echo

# # -------------------------------------------
# # Preflight: tools check
# # -------------------------------------------
# echo ">> Checking for required tools..."
# command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
# command -v minikube >/dev/null || { echo "minikube not found"; exit 1; }
# command -v docker >/dev/null || { echo "docker not found"; exit 1; }
# echo "OK"

# # -------------------------------------------
# # Minikube up + Ingress
# # -------------------------------------------
# if ! minikube status >/dev/null 2>&1; then
#   echo ">> Starting minikube..."
#   minikube start
# else
#   echo ">> Minikube already running."
# fi

# echo ">> Enabling ingress addon (for NGINX Ingress Controller)..."
# minikube addons enable ingress >/dev/null

# # -------------------------------------------
# # Build images inside Minikube's Docker daemon
# # -------------------------------------------
# echo ">> Pointing Docker CLI to Minikube's Docker daemon..."
# eval "$(minikube -p minikube docker-env)"

# echo ">> Building Data-Preprocessing image (services/data_preprocessing)..."
# pushd services/data_preprocessing >/dev/null
# docker build -t "${DP_IMAGE_TAG}" .
# popd >/dev/null

# echo ">> Building Model-Training image (services/model_training)..."
# pushd services/model_training >/dev/null
# docker build -t "${MT_IMAGE_TAG}" .
# popd >/dev/null

# echo ">> Building UI image (services/ui)..."
# pushd services/ui >/dev/null
# docker build -t "${UI_IMAGE_TAG}" .
# popd >/dev/null

# # -------------------------------------------
# # Namespace + base resources
# # -------------------------------------------
# echo ">> Ensuring namespace exists..."
# kubectl get ns "${NAMESPACE}" >/dev/null 2>&1 || kubectl create ns "${NAMESPACE}"

# echo ">> Applying base manifests (k8s/base)..."
# kubectl apply -n "${NAMESPACE}" -f k8s/base/ || true

# # -------------------------------------------
# # Apply service manifests
# # -------------------------------------------
# echo ">> Applying Data-Preprocessing manifests (k8s/services/data-preprocessing)..."
# kubectl apply -n "${NAMESPACE}" -f k8s/services/data-preprocessing/

# echo ">> Applying Model-Training manifests (k8s/services/model-training)..."
# kubectl apply -n "${NAMESPACE}" -f k8s/services/model-training/

# echo ">> Applying UI manifests (k8s/services/ui)..."
# kubectl apply -n "${NAMESPACE}" -f k8s/services/ui/

# # -------------------------------------------
# # Set images (latest local builds)
# # -------------------------------------------
# echo ">> Setting images..."
# kubectl -n "${NAMESPACE}" set image deploy/data-preprocessing api="${DP_IMAGE_TAG}" || true
# kubectl -n "${NAMESPACE}" set image deploy/model-training   api="${MT_IMAGE_TAG}" || true
# kubectl -n "${NAMESPACE}" set image deploy/ui              ui="${UI_IMAGE_TAG}"  || true

# # -------------------------------------------
# # PVC permission fixes (permanent & one-off)
# # -------------------------------------------
# echo ">> Patching Deployments to run as UID/GID/fsGroup 1000 and ensure /shared perms (initContainers)..."

# # Data-Preprocessing (volume name: shared-volume)
# kubectl -n "${NAMESPACE}" patch deploy data-preprocessing --type='merge' -p '
# {
#   "spec": { "template": { "spec": {
#     "securityContext": {
#       "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000, "fsGroupChangePolicy": "Always"
#     },
#     "initContainers": [{
#       "name": "init-perms",
#       "image": "busybox:1.36",
#       "command": ["sh","-c","mkdir -p /shared/data/raw /shared/data/clean /shared/models/artifacts && chown -R 1000:1000 /shared && chmod -R g+rwX /shared"],
#       "securityContext": {"runAsUser": 0},
#       "volumeMounts": [{"name":"shared-volume","mountPath":"/shared"}]
#     }]
#   } } }
# }' >/dev/null

# # Model-Training (volume name: shared-volume)
# kubectl -n "${NAMESPACE}" patch deploy model-training --type='merge' -p '
# {
#   "spec": { "template": { "spec": {
#     "securityContext": {
#       "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000, "fsGroupChangePolicy": "Always"
#     },
#     "initContainers": [{
#       "name": "init-perms",
#       "image": "busybox:1.36",
#       "command": ["sh","-c","mkdir -p /shared/data/raw /shared/data/clean /shared/models/artifacts && chown -R 1000:1000 /shared && chmod -R g+rwX /shared"],
#       "securityContext": {"runAsUser": 0},
#       "volumeMounts": [{"name":"shared-volume","mountPath":"/shared"}]
#     }]
#   } } }
# }' >/dev/null

# # UI (volume name: shared)
# kubectl -n "${NAMESPACE}" patch deploy ui --type='merge' -p '
# {
#   "spec": { "template": { "spec": {
#     "securityContext": {
#       "runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000, "fsGroupChangePolicy": "Always"
#     },
#     "initContainers": [{
#       "name": "init-perms",
#       "image": "busybox:1.36",
#       "command": ["sh","-c","mkdir -p /shared/data/raw /shared/data/clean /shared/models/artifacts && chown -R 1000:1000 /shared && chmod -R g+rwX /shared"],
#       "securityContext": {"runAsUser": 0},
#       "volumeMounts": [{"name":"shared","mountPath":"/shared"}]
#     }]
#   } } }
# }' >/dev/null

# # One-off repair (existing PVs with root-only perms)
# echo ">> Running one-off PVC permission repair (root BusyBox)..."
# kubectl -n "${NAMESPACE}" apply -f - >/dev/null <<'EOF'
# apiVersion: v1
# kind: Pod
# metadata:
#   name: pvc-perm-fix
# spec:
#   restartPolicy: Never
#   volumes:
#     - name: shared
#       persistentVolumeClaim: { claimName: shared-pvc }
#   containers:
#     - name: fix
#       image: busybox:1.36
#       command: ["sh","-c","mkdir -p /shared/data/raw /shared/data/clean /shared/models/artifacts && chown -R 1000:1000 /shared && chmod -R g+rwX /shared && echo done && sleep 1"]
#       securityContext: { runAsUser: 0 }
#       volumeMounts: [{ name: shared, mountPath: /shared }]
# EOF
# kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod/pvc-perm-fix --timeout=30s || true
# kubectl -n "${NAMESPACE}" logs pvc-perm-fix || true
# kubectl -n "${NAMESPACE}" delete pod pvc-perm-fix --ignore-not-found >/dev/null 2>&1 || true

# # -------------------------------------------
# # Sticky routing + job-store safety for DP
# # -------------------------------------------
# echo ">> Enforcing single-replica DP (in-memory job store) and sticky Service..."
# kubectl -n "${NAMESPACE}" scale deploy/data-preprocessing --replicas=1 || true
# kubectl -n "${NAMESPACE}" delete hpa data-preprocessing-hpa --ignore-not-found || true
# kubectl -n "${NAMESPACE}" patch svc data-preprocessing-svc --type=merge -p '
# spec:
#   sessionAffinity: ClientIP
#   sessionAffinityConfig:
#     clientIP: { timeoutSeconds: 10800 }
# ' >/dev/null

# # -------------------------------------------
# # Wait for rollouts
# # -------------------------------------------
# echo ">> Waiting for rollouts..."
# kubectl -n "${NAMESPACE}" rollout status deploy/data-preprocessing --timeout=180s
# kubectl -n "${NAMESPACE}" rollout status deploy/model-training   --timeout=240s
# kubectl -n "${NAMESPACE}" rollout status deploy/ui              --timeout=180s

# # -------------------------------------------
# # Optional: copy dataset into RAW via a DP pod
# # -------------------------------------------
# if [[ -f "${DATASET_LOCAL_PATH}" ]]; then
#   echo ">> Copying dataset into the shared volume (RAW) via a DP pod..."
#   DP_POD="$(kubectl -n "${NAMESPACE}" get pods -l app=data-preprocessing -o jsonpath='{.items[0].metadata.name}')"
#   kubectl -n "${NAMESPACE}" exec "${DP_POD}" -- mkdir -p "${RAW_DIR_IN_POD}"
#   kubectl -n "${NAMESPACE}" cp "${DATASET_LOCAL_PATH}" "${DP_POD}:${RAW_DIR_IN_POD}/$(basename "${DATASET_LOCAL_PATH}")"
#   echo "   Copied to ${RAW_DIR_IN_POD}/$(basename "${DATASET_LOCAL_PATH}")"
# else
#   echo ">> Skipping dataset copy (file not found): ${DATASET_LOCAL_PATH}"
# fi

# # -------------------------------------------
# # Smoke checks
# # -------------------------------------------
# echo ">> Health checks:"
# kubectl -n "${NAMESPACE}" run curl-dp --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
#   curl -sS data-preprocessing-svc:8000/healthz || true
# kubectl -n "${NAMESPACE}" run curl-mt-hz --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
#   curl -sS model-training-svc:8000/healthz || true

# # Try to locate a cleaned CSV and kick a training job (best-effort)
# echo ">> Looking for a cleaned CSV under ${CLEAN_DIR_IN_POD} to kick a training job..."
# MT_POD="$(kubectl -n "${NAMESPACE}" get pods -l app=model-training -o jsonpath='{.items[0].metadata.name}')"
# CLEAN_PATH="$(kubectl -n "${NAMESPACE}" exec "${MT_POD}" -- sh -lc "ls -1 ${CLEAN_DIR_IN_POD}/*_clean.csv 2>/dev/null | head -n1" || true)"
# if [[ -n "${CLEAN_PATH}" ]]; then
#   echo "   Found: ${CLEAN_PATH}"
#   echo ">> Starting training job against ${CLEAN_PATH} ..."
#   kubectl -n "${NAMESPACE}" run curl-mt --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
#     curl -sS -X POST model-training-svc:8000/train \
#       -H 'Content-Type: application/json' \
#       -d "{\"input_path\":\"${CLEAN_PATH}\",\"target_column\":\"Target\",\"test_size\":0.2,\"val_size\":0.2,\"random_state\":42,\"stratify\":true}" \
#     || true
# else
#   echo "   No *_clean.csv found yet. Run preprocessing from the UI, then train."
# fi

# # -------------------------------------------
# # List services/ingresses
# # -------------------------------------------
# echo ">> Listing services and ingresses in ${NAMESPACE}..."
# kubectl -n "${NAMESPACE}" get svc,ingress

# # -------------------------------------------
# # Ingress host mapping for UI (optional)
# # -------------------------------------------
# MINIKUBE_IP="$(minikube ip)"
# if kubectl get ingress -n "${NAMESPACE}" ui >/dev/null 2>&1; then
#   echo ">> Adding ${UI_DOMAIN} -> ${MINIKUBE_IP} to /etc/hosts (sudo may prompt for password)..."
#   if ! grep -q "${UI_DOMAIN}" /etc/hosts; then
#     echo "${MINIKUBE_IP} ${UI_DOMAIN}" | sudo tee -a /etc/hosts >/dev/null
#   else
#     echo "   /etc/hosts already contains ${UI_DOMAIN}; leaving as-is."
#   fi
# fi

# # -------------------------------------------
# # Optional: start port-forward to UI
# # -------------------------------------------
# if [[ "${ENABLE_PORT_FORWARD}" == "true" ]]; then
#   echo ">> Starting port-forward: svc/ui -> http://localhost:8501 (background)"
#   if [[ -f /tmp/ui_pf.pid ]] && ps -p "$(cat /tmp/ui_pf.pid)" >/dev/null 2>&1; then
#     kill "$(cat /tmp/ui_pf.pid)" || true
#     rm -f /tmp/ui_pf.pid
#   fi
#   (kubectl -n "${NAMESPACE}" port-forward svc/ui 8501:80 >/tmp/ui_pf.log 2>&1 &) && echo $! >/tmp/ui_pf.pid
# fi

# echo
# echo "=============================================="
# echo "✅ Deploy complete!"
# echo "UI (port-forward):   http://localhost:8501"
# if kubectl get ingress -n "${NAMESPACE}" ui >/dev/null 2>&1; then
#   echo "UI (Ingress):        http://${UI_DOMAIN}/   (if controller is ready)"
# fi
# echo "DP svc (internal):   data-preprocessing-svc:8000"
# echo "MT svc (internal):   model-training-svc:8000"
# echo
# echo "To stop port-forward: kill \$(cat /tmp/ui_pf.pid)"
# echo "Logs: kubectl -n ${NAMESPACE} logs deploy/ui -f"
# echo "      kubectl -n ${NAMESPACE} logs deploy/data-preprocessing -f"
# echo "      kubectl -n ${NAMESPACE} logs deploy/model-training -f"
# echo "=============================================="

#!/usr/bin/env bash
set -euo pipefail

# ================================
# automation.sh  (UI + Data-Preprocessing + Model-Training + Model-Inference)
# Minikube one-shot deploy with perms + routing fixes baked-in
# ================================

# ---- Config (edit if you change tags/paths) ----
UI_IMAGE_TAG="${UI_IMAGE_TAG:-ui:1.0.3}"
DP_IMAGE_TAG="${DP_IMAGE_TAG:-data-preprocessing:2.0.0}"
MT_IMAGE_TAG="${MT_IMAGE_TAG:-model-training:1.0.2}"
MI_IMAGE_TAG="${MI_IMAGE_TAG:-model-inference:1.0.0}"

NAMESPACE="${NAMESPACE:-hospital-ml}"
PVC_NAME="${PVC_NAME:-shared-pvc}"
UI_DOMAIN="${UI_DOMAIN:-ui.localtest.me}"                # Ingress host (optional)

DATASET_LOCAL_PATH="${DATASET_LOCAL_PATH:-./datasets/diabetes_dataset00.csv}"  # optional copy to RAW
RAW_DIR_IN_POD="/shared/data/raw"
CLEAN_DIR_IN_POD="/shared/data/clean"
MODEL_DIR_IN_POD="/shared/models"
ARTIFACTS_DIR_IN_POD="/shared/models/artifacts"

ENABLE_PORT_FORWARD="${ENABLE_PORT_FORWARD:-true}"       # start PF to http://localhost:8501

echo "==> Using config:"
echo "    NAMESPACE=${NAMESPACE}"
echo "    UI_IMAGE_TAG=${UI_IMAGE_TAG}"
echo "    DP_IMAGE_TAG=${DP_IMAGE_TAG}"
echo "    MT_IMAGE_TAG=${MT_IMAGE_TAG}"
echo "    MI_IMAGE_TAG=${MI_IMAGE_TAG}"
echo "    UI_DOMAIN=${UI_DOMAIN}"
echo "    DATASET_LOCAL_PATH=${DATASET_LOCAL_PATH}"
echo "    ENABLE_PORT_FORWARD=${ENABLE_PORT_FORWARD}"
echo

# -------------------------------------------
# Preflight
# -------------------------------------------
command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }
command -v minikube >/dev/null || { echo "minikube not found"; exit 1; }
command -v docker >/dev/null || { echo "docker not found"; exit 1; }

# -------------------------------------------
# Minikube + Ingress
# -------------------------------------------
if ! minikube status >/dev/null 2>&1; then
  echo ">> Starting minikube..."
  minikube start
else
  echo ">> Minikube already running."
fi
minikube addons enable ingress >/dev/null

# -------------------------------------------
# Build images (inside Minikube's Docker)
# -------------------------------------------
eval "$(minikube -p minikube docker-env)"

echo ">> Building Data-Preprocessing image..."
pushd services/data_preprocessing >/dev/null
docker build -t "${DP_IMAGE_TAG}" .
popd >/dev/null

echo ">> Building Model-Training image..."
pushd services/model_training >/dev/null
docker build -t "${MT_IMAGE_TAG}" .
popd >/dev/null

echo ">> Building Model-Inference image..."
pushd services/model_inference >/dev/null
docker build -t "${MI_IMAGE_TAG}" .
popd >/dev/null

echo ">> Building UI image..."
pushd services/ui >/dev/null
docker build -t "${UI_IMAGE_TAG}" .
popd >/dev/null

# -------------------------------------------
# Namespace + base
# -------------------------------------------
kubectl get ns "${NAMESPACE}" >/dev/null 2>&1 || kubectl create ns "${NAMESPACE}"
kubectl apply -n "${NAMESPACE}" -f k8s/base/ || true

# -------------------------------------------
# Apply service manifests
# -------------------------------------------
kubectl apply -n "${NAMESPACE}" -f k8s/services/data-preprocessing/
kubectl apply -n "${NAMESPACE}" -f k8s/services/model-training/
kubectl apply -n "${NAMESPACE}" -f k8s/services/model-inference/
kubectl apply -n "${NAMESPACE}" -f k8s/services/ui/

# -------------------------------------------
# Set images
# -------------------------------------------
kubectl -n "${NAMESPACE}" set image deploy/data-preprocessing api="${DP_IMAGE_TAG}" || true
kubectl -n "${NAMESPACE}" set image deploy/model-training   api="${MT_IMAGE_TAG}" || true
kubectl -n "${NAMESPACE}" set image deploy/model-inference  api="${MI_IMAGE_TAG}" || true
kubectl -n "${NAMESPACE}" set image deploy/ui              ui="${UI_IMAGE_TAG}"  || true

# -------------------------------------------
# PVC permission patches (init-perms + securityContext) for all Deployments
# -------------------------------------------
patch_perms () {
  local deploy="$1" volname="$2"
  kubectl -n "${NAMESPACE}" patch deploy "${deploy}" --type='merge' -p "
  {
    \"spec\": { \"template\": { \"spec\": {
      \"securityContext\": {
        \"runAsUser\": 1000, \"runAsGroup\": 1000, \"fsGroup\": 1000, \"fsGroupChangePolicy\": \"Always\"
      },
      \"initContainers\": [{
        \"name\": \"init-perms\",
        \"image\": \"busybox:1.36\",
        \"command\": [\"sh\",\"-c\",\"mkdir -p /shared/data/raw /shared/data/clean /shared/models/artifacts/predictions && chown -R 1000:1000 /shared && chmod -R g+rwX /shared\"],
        \"securityContext\": {\"runAsUser\": 0},
        \"volumeMounts\": [{\"name\":\"${volname}\",\"mountPath\":\"/shared\"}]
      }]
    } } }
  }" >/dev/null
}

echo ">> Patching Deployments for perms..."
patch_perms "data-preprocessing" "shared-volume"
patch_perms "model-training"    "shared-volume"
patch_perms "model-inference"   "shared-volume"
patch_perms "ui"                "shared"

# One-off repair (existing PV with root-only perms)
echo ">> One-off PVC permission repair..."
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

# -------------------------------------------
# DP sticky routing (in-memory job store)
# -------------------------------------------
kubectl -n "${NAMESPACE}" scale deploy/data-preprocessing --replicas=1 || true
kubectl -n "${NAMESPACE}" delete hpa data-preprocessing-hpa --ignore-not-found || true
kubectl -n "${NAMESPACE}" patch svc data-preprocessing-svc --type=merge -p '
spec:
  sessionAffinity: ClientIP
  sessionAffinityConfig:
    clientIP: { timeoutSeconds: 10800 }
' >/dev/null

# -------------------------------------------
# Wait for rollouts
# -------------------------------------------
kubectl -n "${NAMESPACE}" rollout status deploy/data-preprocessing --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deploy/model-training   --timeout=240s
kubectl -n "${NAMESPACE}" rollout status deploy/model-inference  --timeout=180s
kubectl -n "${NAMESPACE}" rollout status deploy/ui              --timeout=180s

# -------------------------------------------
# Optional: copy dataset to RAW
# -------------------------------------------
if [[ -f "${DATASET_LOCAL_PATH}" ]]; then
  echo ">> Copying dataset into RAW..."
  DP_POD="$(kubectl -n "${NAMESPACE}" get pods -l app=data-preprocessing -o jsonpath='{.items[0].metadata.name}')"
  kubectl -n "${NAMESPACE}" exec "${DP_POD}" -- mkdir -p "${RAW_DIR_IN_POD}"
  kubectl -n "${NAMESPACE}" cp "${DATASET_LOCAL_PATH}" "${DP_POD}:${RAW_DIR_IN_POD}/$(basename "${DATASET_LOCAL_PATH}")"
fi

# -------------------------------------------
# Health checks
# -------------------------------------------
echo ">> Health checks:"
kubectl -n "${NAMESPACE}" run curl-dp --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS data-preprocessing-svc:8000/healthz || true
kubectl -n "${NAMESPACE}" run curl-mt-hz --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS model-training-svc:8000/healthz || true
kubectl -n "${NAMESPACE}" run curl-mi-hz --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
  curl -sS model-inference-svc:8000/healthz || true

# -------------------------------------------
# Smoke predict (if we have BOTH a model and a cleaned CSV)
# -------------------------------------------
echo ">> Attempting a smoke prediction (best-effort)..."
MI_POD="$(kubectl -n "${NAMESPACE}" get pods -l app=model-inference -o jsonpath='{.items[0].metadata.name}')"
MODEL_PATH="$(kubectl -n "${NAMESPACE}" exec "${MI_POD}" -- sh -lc "ls -1 ${MODEL_DIR_IN_POD}/*.joblib 2>/dev/null | head -n1" || true)"
CLEAN_PATH="$(kubectl -n "${NAMESPACE}" exec "${MI_POD}" -- sh -lc "ls -1 ${CLEAN_DIR_IN_POD}/*_clean.csv 2>/dev/null | head -n1" || true)"
if [[ -n "${MODEL_PATH}" && -n "${CLEAN_PATH}" ]]; then
  echo "   Using model: ${MODEL_PATH}"
  echo "   Using data : ${CLEAN_PATH}"
  kubectl -n "${NAMESPACE}" run curl-mi --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
    curl -sS -X POST model-inference-svc:8000/predict \
      -H 'Content-Type: application/json' \
      -d "{\"model_path\":\"${MODEL_PATH}\",\"artifacts_dir\":\"${ARTIFACTS_DIR_IN_POD}\",\"mode\":\"csv\",\"data_path\":\"${CLEAN_PATH}\",\"return_proba\":true,\"top_k\":3}" \
    || true
else
  echo "   Skip: need a trained model and a cleaned CSV to smoke-test predict."
fi

# -------------------------------------------
# List services/ingresses
# -------------------------------------------
kubectl -n "${NAMESPACE}" get svc,ingress

# -------------------------------------------
# Ingress host mapping for UI (optional)
# -------------------------------------------
MINIKUBE_IP="$(minikube ip)"
if kubectl get ingress -n "${NAMESPACE}" ui >/dev/null 2>&1; then
  if ! grep -q "${UI_DOMAIN}" /etc/hosts; then
    echo "${MINIKUBE_IP} ${UI_DOMAIN}" | sudo tee -a /etc/hosts >/dev/null
  fi
fi

# -------------------------------------------
# Optional: start port-forward to UI
# -------------------------------------------
if [[ "${ENABLE_PORT_FORWARD}" == "true" ]]; then
  echo ">> Starting port-forward: svc/ui -> http://localhost:8501 (background)"
  if [[ -f /tmp/ui_pf.pid ]] && ps -p "$(cat /tmp/ui_pf.pid)" >/dev/null 2>&1; then
    kill "$(cat /tmp/ui_pf.pid)" || true
    rm -f /tmp/ui_pf.pid
  fi
  (kubectl -n "${NAMESPACE}" port-forward svc/ui 8501:80 >/tmp/ui_pf.log 2>&1 &) && echo $! >/tmp/ui_pf.pid
fi

echo
echo "=============================================="
echo "✅ Deploy complete!"
echo "UI (port-forward):   http://localhost:8501"
if kubectl get ingress -n "${NAMESPACE}" ui >/dev/null 2>&1; then
  echo "UI (Ingress):        http://${UI_DOMAIN}/   (if controller is ready)"
fi
echo "DP svc (internal):   data-preprocessing-svc:8000"
echo "MT svc (internal):   model-training-svc:8000"
echo "MI svc (internal):   model-inference-svc:8000"
echo
echo "To stop port-forward: kill \$(cat /tmp/ui_pf.pid)"
echo "Logs: kubectl -n ${NAMESPACE} logs deploy/ui -f"
echo "      kubectl -n ${NAMESPACE} logs deploy/data-preprocessing -f"
echo "      kubectl -n ${NAMESPACE} logs deploy/model-training -f"
echo "      kubectl -n ${NAMESPACE} logs deploy/model-inference -f"
echo "=============================================="

