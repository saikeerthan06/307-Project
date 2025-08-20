#!/usr/bin/env bash
set -euo pipefail

# netpol-apply.sh
# Apply the security baseline: default-deny + explicit allows.
# Usage: netpol-apply.sh [NAMESPACE]
NS="${1:-hospital-ml}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)"
SEC="$ROOT/k8s/security"

echo "==> Applying NetworkPolicies into namespace: $NS"
kubectl -n "$NS" apply -f "$SEC/networkpolicy-default-deny.yaml"
kubectl -n "$NS" apply -f "$SEC/networkpolicy-allow-dns.yaml"
kubectl -n "$NS" apply -f "$SEC/networkpolicy-allow-ui-egress.yaml"
kubectl -n "$NS" apply -f "$SEC/networkpolicy-allow-ingress-to-ui.yaml"

echo "==> Effective policies:"
kubectl -n "$NS" get netpol
echo "Done."
