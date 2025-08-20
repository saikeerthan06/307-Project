#!/usr/bin/env bash
set -euo pipefail

# backup-tools.sh
# Manage nightly backups of /shared artifacts (CronJob + on-demand).
# Usage:
#   backup-tools.sh [NAMESPACE] install
#   backup-tools.sh [NAMESPACE] run-now
#   backup-tools.sh [NAMESPACE] list
#   backup-tools.sh [NAMESPACE] jobs
#   backup-tools.sh [NAMESPACE] restore <backup-file.tgz> [pod-label-selector]
#
NS="${1:-hospital-ml}"; shift || true
CMD="${1:-}"; shift || true

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
OPS="$ROOT/k8s/ops"
CRON_NAME="nightly-backup"

pick_pod() {
  local sel="$1"
  # Prefer backends (they all mount /shared), fall back to UI
  kubectl -n "$NS" get pod -l "$sel" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

first_shared_pod() {
  for L in "app=data-preprocessing" "app=model-training" "app=model-inference" "app=ui"; do
    P="$(pick_pod "$L")"
    if [[ -n "$P" ]]; then echo "$P"; return 0; fi
  done
  return 1
}

case "$CMD" in
  install)
    echo "==> Applying CronJob: $CRON_NAME"
    kubectl -n "$NS" apply -f "$OPS/cronjob-backup.yaml"
    kubectl -n "$NS" get cronjob "$CRON_NAME" -o wide
    ;;
  run-now)
    ts="$(date +%Y%m%d-%H%M%S)"
    job="backup-now-$ts"
    echo "==> Creating one-off Job from CronJob: $job"
    kubectl -n "$NS" create job "$job" --from=cronjob/$CRON_NAME
    echo "==> Waiting for Job to complete..."
    kubectl -n "$NS" wait --for=condition=complete "job/$job" --timeout=180s || true
    echo "==> Job logs:"
    pod=$(kubectl -n "$NS" get pods --selector=job-name="$job" -o jsonpath='{.items[0].metadata.name}')
    kubectl -n "$NS" logs "$pod" || true
    ;;
  list)
    POD="$(first_shared_pod || true)"
    if [[ -z "${POD:-}" ]]; then echo "No suitable pod found with /shared mounted." >&2; exit 1; fi
    echo "==> Listing backups on PVC via pod $POD"
    kubectl -n "$NS" exec "$POD" -- sh -lc 'ls -lh /shared/models/artifacts/backups || true'
    ;;
  jobs)
    kubectl -n "$NS" get cronjob "$CRON_NAME" -o wide || true
    kubectl -n "$NS" get jobs -l job-name -o wide || true
    ;;
  restore)
    FILE="${1:-}"; shift || true
    if [[ -z "$FILE" ]]; then echo "Usage: $0 [NS] restore <backup-file.tgz> [optional-pod-label]"; exit 1; fi
    SEL="${1:-}"; shift || true
    if [[ -n "$SEL" ]]; then
      POD="$(pick_pod "$SEL")"
    else
      POD="$(first_shared_pod || true)"
    fi
    if [[ -z "${POD:-}" ]]; then echo "No pod available to perform restore."; exit 1; fi
    echo "==> Restoring $FILE into /shared via pod $POD"
    # Assuming the file already exists on PVC. If it is local, user must kubectl cp first.
    kubectl -n "$NS" exec "$POD" -- sh -lc "test -f /shared/models/artifacts/backups/$FILE"
    kubectl -n "$NS" exec "$POD" -- sh -lc "tar xzf /shared/models/artifacts/backups/$FILE -C /shared && echo 'Restore completed.'"
    ;;
  *)
    cat <<EOF >&2
Usage:
  $0 [NAMESPACE] install              # apply CronJob
  $0 [NAMESPACE] run-now              # one-off Job from CronJob
  $0 [NAMESPACE] list                 # list .tgz backups on PVC
  $0 [NAMESPACE] jobs                 # show CronJob/Jobs
  $0 [NAMESPACE] restore <file.tgz> [pod-label]  # restore backup (overwrites files)
EOF
    exit 1
    ;;
esac
