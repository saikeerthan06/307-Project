#!/usr/bin/env bash
set -euo pipefail
NS=${1:-hospital-ml}
TO=${2:-green}  # "blue" or "green"
kubectl -n "$NS" patch svc ui -p "{\"spec\":{\"selector\":{\"app\":\"ui\",\"version\":\"$TO\"}}}"
kubectl -n "$NS" get endpoints ui -o wide
echo "Switched UI service to version=$TO"
