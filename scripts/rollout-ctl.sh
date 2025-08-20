#!/usr/bin/env bash
set -euo pipefail

# rollout-ctl.sh
# Helper for Deployment rollout controls.
# Usage:
#   rollout-ctl.sh [NAMESPACE] <deploy> status|history
#   rollout-ctl.sh [NAMESPACE] <deploy> set-image <container> <image:tag> [--cause "reason"]
#   rollout-ctl.sh [NAMESPACE] <deploy> pause|resume
#   rollout-ctl.sh [NAMESPACE] <deploy> undo [--to-revision N]
#
NS="${1:-hospital-ml}"; shift || true
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 [NAMESPACE] <deploy> <cmd> ..." >&2; exit 1
fi
DEP="$1"; shift
CMD="$1"; shift

case "$CMD" in
  status)
    kubectl -n "$NS" rollout status "deploy/$DEP" --timeout=180s || true
    ;;
  history)
    kubectl -n "$NS" rollout history "deploy/$DEP"
    ;;
  set-image)
    if [[ $# -lt 2 ]]; then
      echo "set-image requires <container> <image:tag> [--cause 'reason']" >&2; exit 1
    fi
    CONTAINER="$1"; IMAGE="$2"; shift 2
    CAUSE="Manual image update"
    if [[ "${1:-}" == "--cause" ]]; then
      shift; CAUSE="${1:-$CAUSE}"; shift || true
    fi
    kubectl -n "$NS" annotate deploy "$DEP" kubernetes.io/change-cause="$CAUSE" --overwrite
    kubectl -n "$NS" set image "deploy/$DEP" "$CONTAINER=$IMAGE"
    kubectl -n "$NS" rollout status "deploy/$DEP" --timeout=180s || true
    kubectl -n "$NS" rollout history "deploy/$DEP"
    ;;
  pause)
    kubectl -n "$NS" rollout pause "deploy/$DEP"
    ;;
  resume)
    kubectl -n "$NS" rollout resume "deploy/$DEP"
    kubectl -n "$NS" rollout status "deploy/$DEP" --timeout=180s || true
    ;;
  undo)
    if [[ "${1:-}" == "--to-revision" ]]; then
      shift; REV="${1:?revision required}"; shift || true
      kubectl -n "$NS" rollout undo "deploy/$DEP" --to-revision="$REV"
    else
      kubectl -n "$NS" rollout undo "deploy/$DEP"
    fi
    kubectl -n "$NS" rollout status "deploy/$DEP" --timeout=180s || true
    kubectl -n "$NS" rollout history "deploy/$DEP"
    ;;
  *)
    echo "Unknown command: $CMD" >&2; exit 1
    ;;
esac
