#!/usr/bin/env bash
set -euo pipefail
NS=${1:-hospital-ml}
DEP=${2:-model-inference}
OLD_IMG=$(kubectl -n "$NS" get deploy "$DEP" -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "Current image: $OLD_IMG"

# 1) Annotate a change-cause (shows up in history)
kubectl -n "$NS" annotate deploy "$DEP" kubernetes.io/change-cause="Demo: bump image for rollout controls" --overwrite

# 2) Rollout a new image tag (change tag to something that exists in your cluster)
NEW_IMG="${3:-model-inference:1.0.1}"
kubectl -n "$NS" set image deploy/"$DEP" api="$NEW_IMG"

# 3) Watch rollout
kubectl -n "$NS" rollout status deploy/"$DEP" --timeout=180s || true

# 4) Show history
kubectl -n "$NS" rollout history deploy/"$DEP"

# 5) Pause mid-flight (if you re-run with a slow image, this shows beautifully)
kubectl -n "$NS" rollout pause deploy/"$DEP"
echo "Paused. (Do checks here: metrics/logs)"
sleep 2
kubectl -n "$NS" rollout resume deploy/"$DEP"

# 6) Roll back to previous
kubectl -n "$NS" rollout undo deploy/"$DEP"
kubectl -n "$NS" rollout status deploy/"$DEP" --timeout=180s
kubectl -n "$NS" rollout history deploy/"$DEP"
