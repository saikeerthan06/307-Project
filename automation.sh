#!/usr/bin/env bash

set -euo pipefail
trap 'err "Aborted at $BASH_SOURCE:$LINENO ($BASH_COMMAND)"' ERR

# =========================================
# automation.sh  – one-command DEV deploy & controls
# =========================================
# Features:
# - Builds images (Minikube-aware) & deploys all services (UI/DP/MT/MI)
# - Ensures labels/selectors & ports/probes are aligned
# - Applies PVC perms fix (one-off pod)
# - Robust rollout controls: deploy | restart | pause | resume | status | logs | curl | cleanup
# - Idempotent patches; safe to re-run any time
# - Defensive timeouts + diagnostics when rollouts hang
#
# Usage examples:
#   ./automation.sh deploy                # full (re)build & (re)deploy
#   ./automation.sh restart ui            # restart just UI
#   ./automation.sh pause model-inference # pause a single deployment
#   ./automation.sh resume all            # resume all
#   ./automation.sh status                # rollout statuses
#   ./automation.sh logs model-inference  # tail logs
#   ./automation.sh curl mi               # healthz to MI via ephemeral curl pod
#   ./automation.sh cleanup               # stop PF, remove curl pods, etc. (non-destructive)
# =========================================

# ---- Config (env-overridable)
UI_IMAGE_TAG="${UI_IMAGE_TAG:-ui:1.2.5}"
DP_IMAGE_TAG="${DP_IMAGE_TAG:-data-preprocessing:2.0.0}"
MT_IMAGE_TAG="${MT_IMAGE_TAG:-model-training:1.0.2}"
MI_IMAGE_TAG="${MI_IMAGE_TAG:-model-inference:1.0.1}"

NAMESPACE="${NAMESPACE:-hospital-ml}"
UI_DOMAIN="${UI_DOMAIN:-ui.localtest.me}"
DEV_MODE="${DEV_MODE:-true}"
ENABLE_PORT_FORWARD="${ENABLE_PORT_FORWARD:-true}"
BUILD_IN_MINIKUBE="${BUILD_IN_MINIKUBE:-true}"   # build inside minikube docker-env

RAW_DIR_IN_POD="/shared/data/raw"
CLEAN_DIR_IN_POD="/shared/data/clean"
MODEL_DIR_IN_POD="/shared/models"
ARTIFACTS_DIR_IN_POD="/shared/models/artifacts"
DATASET_LOCAL_PATH="${DATASET_LOCAL_PATH:-}"     # optional local dataset to copy into RAW

# Map short names to resource names
# declare -A DEP_MAP=(
#   [dp]=data-preprocessing
#   [mt]=model-training
#   [mi]=model-inference
#   [ui]=ui
# )

res_name() {
  case "$1" in
    dp) echo "data-preprocessing" ;;
    mt) echo "model-training" ;;
    mi) echo "model-inference" ;;
    ui) echo "ui" ;;
    *)  echo "$1" ;;
  esac
}


# -------------- Helpers --------------
err() { echo >&2 "[ERROR] $*"; }
log() { echo "[INFO]  $*"; }
warn(){ echo "[WARN]  $*"; }

die() { err "$*"; exit 1; }
require() { command -v "$1" >/dev/null || die "$1 not found"; }
ns() { kubectl -n "$NAMESPACE" "$@"; }


kubectl_safe_apply_dir() { # apply if dir exists
  local p="$1"; [[ -d "$p" ]] && ns apply -f "$p" || true
}

build_images() {
  log "Building images… (BUILD_IN_MINIKUBE=${BUILD_IN_MINIKUBE})"
  if [[ "${BUILD_IN_MINIKUBE}" == "true" ]]; then
    eval "$(minikube -p minikube docker-env)"
  fi
  (cd services/data_preprocessing && docker build -t "${DP_IMAGE_TAG}" .)
  (cd services/model_training      && docker build -t "${MT_IMAGE_TAG}" .)
  (cd services/model_inference     && docker build -t "${MI_IMAGE_TAG}" .)
  (cd services/ui                  && docker build -t "${UI_IMAGE_TAG}" .)

  # If not building inside Minikube, load images explicitly
  if [[ "${BUILD_IN_MINIKUBE}" != "true" ]]; then
    log "Loading images into Minikube cache…"
    minikube image load "${DP_IMAGE_TAG}" "${MT_IMAGE_TAG}" "${MI_IMAGE_TAG}" "${UI_IMAGE_TAG}"
  fi
}

ensure_cluster() {
  require kubectl; require minikube; require docker; require jq
  if ! minikube status >/dev/null 2>&1; then
    log "Starting Minikube…"; minikube start
  fi
  log "Enabling ingress addon"; minikube addons enable ingress >/dev/null || true
  kubectl get ns "$NAMESPACE" >/dev/null 2>&1 || kubectl create ns "$NAMESPACE"
}

apply_manifests() {
  log "Applying base & services manifests…"
  kubectl_safe_apply_dir k8s/base/
  kubectl_safe_apply_dir k8s/services/data-preprocessing/
  kubectl_safe_apply_dir k8s/services/model-training/
  kubectl_safe_apply_dir k8s/services/model-inference/
  kubectl_safe_apply_dir k8s/services/ui/
}

set_images() {
  log "Setting deployment images…"
  ns set image deploy/data-preprocessing api="${DP_IMAGE_TAG}" || true
  ns set image deploy/model-training   api="${MT_IMAGE_TAG}" || true
  ns set image deploy/model-inference  api="${MI_IMAGE_TAG}" || true
  ns set image deploy/ui              ui="${UI_IMAGE_TAG}"  || true
}

pvc_perms_fix() {
  log "Ensuring /shared perms (one-off)…"
  cat <<'EOF' | ns apply -f - >/dev/null
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
  ns wait --for=condition=Ready pod/pvc-perm-fix --timeout=30s || true
  ns delete pod pvc-perm-fix --ignore-not-found >/dev/null 2>&1 || true
}

security_context_all() {
  log "Enforcing pod/container securityContext…"
  for DEP in data-preprocessing model-training model-inference ui; do
    ns patch deploy "$DEP" -p '{"spec":{"template":{"spec":{"securityContext":{"runAsUser":1000,"runAsGroup":1000,"fsGroup":1000,"fsGroupChangePolicy":"Always"}}}}}' || true
    local cname="api"; [[ "$DEP" == "ui" ]] && cname="ui"
    ns patch deploy "$DEP" -p "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"${cname}\",\"securityContext\":{\"runAsUser\":1000,\"runAsGroup\":1000}}]}}}}" || true
  done
}

recreate_strategy_all() {
  if [[ "${DEV_MODE}" == "true" ]]; then
    log ">> DEV_MODE=true: disabling HPAs and using Recreate strategy"
    ns delete hpa data-preprocessing-hpa ui-hpa model-training-hpa model-inference-hpa --ignore-not-found
    for DEP in data-preprocessing model-training model-inference ui; do
      ns patch deploy "$DEP" -p '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}' || true
      ns patch deploy "$DEP" -p '{"spec":{"template":{"spec":{"terminationGracePeriodSeconds":10}}}}' || true
    done
  fi
}

# Align labels/selectors and ports/probes for inference (most common culprit)
fix_inference_labels_ports() {
  log "Synchronizing model-inference labels, selectors, ports & probes…"
  ns label deploy model-inference app=model-inference --overwrite || true
  ns patch svc model-inference-svc --type=merge -p '{"spec":{"selector":{"app":"model-inference"}}}' || true
  ns patch svc model-inference-svc --type=merge -p '{"spec":{"ports":[{"port":8000,"targetPort":8000,"protocol":"TCP","name":"http"}]}}' || true
  ns patch deploy model-inference --type=merge -p '{"spec":{"template":{"spec":{"containers":[{"name":"api","ports":[{"containerPort":8000,"name":"http"}],"readinessProbe":{"tcpSocket":{"port":"http"},"initialDelaySeconds":5,"periodSeconds":5,"timeoutSeconds":2,"failureThreshold":6},"livenessProbe":{"tcpSocket":{"port":"http"},"initialDelaySeconds":10,"periodSeconds":10}}]}}}}' || true
}

scale_clean_restart_all() {
  log "Recreate all deployments cleanly (scale 0 → delete pods/RS → scale 1)…"
  for DEP in data-preprocessing model-training model-inference ui; do
    log ">> Recreate ${DEP}"
    ns scale deploy/${DEP} --replicas=0 || true
    ns wait --for=delete pod -l app=${DEP} --timeout=90s || true
    ns delete rs -l app=${DEP} --ignore-not-found || true
    ns scale deploy/${DEP} --replicas=1 || true
  done
}

wait_rollouts() {
  log "Waiting for rollouts…"
  ns rollout status deploy/data-preprocessing --timeout=180s || { warn "DP rollout timeout"; }
  ns rollout status deploy/model-training   --timeout=240s || { warn "MT rollout timeout"; }
  ns rollout status deploy/model-inference  --timeout=240s || { warn "MI rollout timeout"; diagnose_mi; }
  ns rollout status deploy/ui              --timeout=240s || { warn "UI rollout timeout"; }
}

post_deploy_dataset_copy() {
  [[ -n "${DATASET_LOCAL_PATH}" && -f "${DATASET_LOCAL_PATH}" ]] || return 0
  log "Copying dataset to RAW…"
  local dpod
  dpod=$(ns get pods -l app=data-preprocessing -o jsonpath='{.items[0].metadata.name}')
  ns exec "$dpod" -- mkdir -p "$RAW_DIR_IN_POD" || true
  ns cp "${DATASET_LOCAL_PATH}" "$dpod:${RAW_DIR_IN_POD}/$(basename "${DATASET_LOCAL_PATH}")"
}

health_checks() {
  log "Health checks via ephemeral curl pods…"
  ns run curl-dp --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
    curl -sS data-preprocessing-svc:8000/healthz || true
  ns run curl-mt --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
    curl -sS model-training-svc:8000/healthz || true
  ns run curl-mi --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- \
    curl -sS model-inference-svc:8000/healthz || true
}

diagnose_mi() {
  warn "Diagnosing model-inference…"
  ns get svc model-inference-svc -o wide || true
  ns get endpoints model-inference-svc -o wide || true
  ns get pods -l app=model-inference -o wide || true
  ns describe pod -l app=model-inference | sed -n '/Events/,$p' || true
  ns logs -l app=model-inference --tail=200 || true
}

setup_ingress_hosts() {
  local ip; ip=$(minikube ip)
  if kubectl get ingress -n "$NAMESPACE" ui >/dev/null 2>&1; then
    if ! grep -q "${UI_DOMAIN}" /etc/hosts; then
      log "Adding ${UI_DOMAIN} to /etc/hosts → ${ip} (sudo required)"
      echo "${ip} ${UI_DOMAIN}" | sudo tee -a /etc/hosts >/dev/null || true
    fi
  fi
}

port_forward_ui() {
  [[ "${ENABLE_PORT_FORWARD}" == "true" ]] || return 0
  log "Port-forward UI → http://localhost:8501"
  if [[ -f /tmp/ui_pf.pid ]] && ps -p "$(cat /tmp/ui_pf.pid)" >/dev/null 2>&1; then
    kill "$(cat /tmp/ui_pf.pid)" || true; rm -f /tmp/ui_pf.pid
  fi
  nohup kubectl -n "$NAMESPACE" port-forward svc/ui 8501:80 >/tmp/ui_pf.log 2>&1 &
  echo "$!" >/tmp/ui_pf.pid
}

# ---------- Rollout controls ----------
ensure_not_paused() {
  local dep="$1"; local name
  name=$(res_name "$dep")
  if ns rollout status deploy/"$name" >/dev/null 2>&1; then
    if ns get deploy "$name" -o json | jq -e '.spec.paused == true' >/dev/null 2>&1; then
      log "Deployment $name is paused → resuming"; ns rollout resume deploy/"$name" || true
    fi
  fi
}

cmd_restart() {
  local dep="${1:-all}"
  if [[ "$dep" == "all" ]]; then
    local k
    for k in dp mt mi ui; do ensure_not_paused "$k"; done
    local DEP
    for DEP in data-preprocessing model-training model-inference ui; do ns rollout restart deploy/"$DEP" || true; done
    wait_rollouts
  else
    local name; name=$(res_name "$dep"); ensure_not_paused "$dep"
    ns rollout restart deploy/"$name"; ns rollout status deploy/"$name"
  fi
}

cmd_pause() {
  local dep="${1:-all}"
  if [[ "$dep" == "all" ]]; then
    local DEP
    for DEP in data-preprocessing model-training model-inference ui; do ns rollout pause deploy/"$DEP" || true; done
  else
    ns rollout pause deploy/"$(res_name "$dep")" || true
  fi
}

cmd_resume() {
  local dep="${1:-all}"
  if [[ "$dep" == "all" ]]; then
    local DEP
    for DEP in data-preprocessing model-training model-inference ui; do ns rollout resume deploy/"$DEP" || true; done
  else
    ns rollout resume deploy/"$(res_name "$dep")" || true
  fi
}

cmd_status() {
  local DEP
  for DEP in data-preprocessing model-training model-inference ui; do
    echo "---- $DEP"; ns rollout status deploy/"$DEP" || true
  done
}

cmd_logs() {
  local dep="${1:-ui}"; local name; name=$(res_name "$dep")
  ns logs deploy/"$name" --tail=200 -f
}

cmd_curl() {
  local dep="${1:-ui}"; case "$dep" in
    dp) ns run curl-dp --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- curl -sS data-preprocessing-svc:8000/healthz || true;;
    mt) ns run curl-mt --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- curl -sS model-training-svc:8000/healthz || true;;
    mi) ns run curl-mi --rm -it --restart=Never --image=curlimages/curl:8.9.1 -- curl -sS model-inference-svc:8000/healthz || true;;
    ui) warn "UI is a web app; use port-forward or ingress";;
  esac
}

cmd_cleanup() {
  [[ -f /tmp/ui_pf.pid ]] && { kill "$(cat /tmp/ui_pf.pid)" || true; rm -f /tmp/ui_pf.pid; }
  local p
  for p in curl-dp curl-mt curl-mi curl-mi-s; do ns delete pod "$p" --ignore-not-found || true; done
  log "Cleanup complete."
}

# ---------- Main entry ----------
main() {
  local cmd="${1:-deploy}"; shift || true

  case "$cmd" in
    deploy)
      ensure_cluster
      build_images
      apply_manifests
      set_images
      pvc_perms_fix
      security_context_all
      recreate_strategy_all
      fix_inference_labels_ports
      scale_clean_restart_all
      wait_rollouts
      post_deploy_dataset_copy || true
      health_checks || true
      setup_ingress_hosts || true
      port_forward_ui || true
      echo; echo "=============================================="
      echo "✅ Deploy complete!"
      echo "UI: http://localhost:8501"
      echo "DP: data-preprocessing-svc:8000"
      echo "MT: model-training-svc:8000"
      echo "MI: model-inference-svc:8000"
      echo "==============================================" ;;

    restart) cmd_restart "${1:-all}" ;;
    pause)   cmd_pause   "${1:-all}" ;;
    resume)  cmd_resume  "${1:-all}" ;;
    status)  cmd_status ;;
    logs)    cmd_logs    "${1:-ui}" ;;
    curl)    cmd_curl    "${1:-mi}" ;;
    cleanup) cmd_cleanup ;;
    *)
      cat <<USAGE
Usage: $0 <command> [component]

Commands:
  deploy                  Build images, deploy & fix common issues
  restart [all|dp|mt|mi|ui]  Restart rollout
  pause   [all|dp|mt|mi|ui]  Pause deployment
  resume  [all|dp|mt|mi|ui]  Resume deployment
  status                  Rollout status for all
  logs   [dp|mt|mi|ui]    Tail logs
  curl   [dp|mt|mi]       Health check via curl pod
  cleanup                 Stop PF & remove helper pods

Env vars:
  UI_IMAGE_TAG, DP_IMAGE_TAG, MT_IMAGE_TAG, MI_IMAGE_TAG
  NAMESPACE, DEV_MODE, ENABLE_PORT_FORWARD, BUILD_IN_MINIKUBE
USAGE
      exit 2 ;;
  esac
}

main "$@"
