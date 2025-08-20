#!/usr/bin/env bash
set -euo pipefail

# bluegreen-setup.sh
# Ensure Blue/Green UI is deployed and the ui Service points to the desired color.
# Usage: bluegreen-setup.sh [NAMESPACE] [blue|green]
NS="${1:-hospital-ml}"
COLOR="${2:-green}"
if [[ "$COLOR" != "blue" && "$COLOR" != "green" ]]; then
  echo "Usage: $0 [NAMESPACE] [blue|green]" >&2
  exit 1
fi

# Repo root (script lives in repo/scripts)
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
K8S="$ROOT/k8s"

echo "==> Applying UI base (configmap/service/ingress/hpa/networkpolicy/pdb)"
kubectl -n "$NS" apply -f "$K8S/services/ui/configmap.yaml"
kubectl -n "$NS" apply -f "$K8S/services/ui/service.yaml"
# ingress/hpa/pdb/networkpolicy are optional in local clusters; apply if present
for f in "$K8S/services/ui/ingress.yaml" "$K8S/services/ui/hpa.yaml" "$K8S/services/ui/pdb.yaml" "$K8S/services/ui/networkpolicy.yaml"; do
  [[ -f "$f" ]] && kubectl -n "$NS" apply -f "$f" || true
done

echo "==> Deploying ui-blue and ui-green"
kubectl -n "$NS" apply -f "$K8S/services/ui-blue/deployment.yaml"
kubectl -n "$NS" apply -f "$K8S/services/ui-green/deployment.yaml"

echo "==> Waiting for Deployments to be Available"
kubectl -n "$NS" rollout status deploy/ui-blue --timeout=180s || true
kubectl -n "$NS" rollout status deploy/ui-green --timeout=180s || true

echo "==> Switching Service selector to version=$COLOR"
kubectl -n "$NS" patch svc ui -p "{\"spec\":{\"selector\":{\"app\":\"ui\",\"version\":\"$COLOR\"}}}"

echo "==> Endpoints after switch:"
kubectl -n "$NS" get endpoints ui -o wide

echo "Done. Visit the UI service and verify the color ($COLOR)."