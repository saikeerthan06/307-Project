# UI → Kubernetes → Localhost (Port-Forward) — Steps

## 0) Prereqs
- A working Kubernetes cluster (e.g., minikube or Docker Desktop)
- `kubectl` and `docker` CLIs logged in
- Namespace: `hospital-ml`

```bash
kubectl create namespace hospital-ml 2>/dev/null || true

# From the UI folder containing Dockerfile, app.py, client.py, requirements.txt
docker build -t saikeerthan06/ui:1.0.2 .
docker push saikeerthan06/ui:1.0.2

cat <<'YAML' | kubectl apply -n hospital-ml -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ml-data-pvc
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 10Gi
YAML

kubectl apply -n hospital-ml -f k8s_deployment.yaml

# Always pull latest tag during dev
kubectl -n hospital-ml patch deploy ui --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}]'

# Streamlit writes to $HOME/.streamlit; set HOME=/app (safe even if already in Dockerfile)
kubectl -n hospital-ml set env deploy/ui HOME=/app

# Remove any volumeMounts accidentally shadowing /app
kubectl -n hospital-ml patch deploy ui --type=json -p='[
  { "op": "remove", "path": "/spec/template/spec/containers/0/volumeMounts" }
]' || true

# Remove unused volumes (if present)
kubectl -n hospital-ml patch deploy ui --type=json -p='[
  { "op": "remove", "path": "/spec/template/spec/volumes" }
]' || true

kubectl -n hospital-ml rollout restart deploy/ui
kubectl -n hospital-ml rollout status deploy/ui --timeout=120s

kubectl -n hospital-ml get pods -l app=ui -o wide
POD=$(kubectl -n hospital-ml get pods -l app=ui -o jsonpath="{.items[0].metadata.name}")
kubectl -n hospital-ml get pod "$POD" -o jsonpath="{.spec.containers[0].image}{'\n'}"
kubectl -n hospital-ml logs deploy/ui --tail=50

kubectl -n hospital-ml port-forward svc/ui 8501:80