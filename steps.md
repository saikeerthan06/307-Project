# ----------------------------
# 1️⃣ Build and push Docker images
# ----------------------------
docker build -t <your-dockerhub-username>/data-preprocessing:latest ./data-preprocessing
docker push <your-dockerhub-username>/data-preprocessing:latest

docker build -t <your-dockerhub-username>/model-training:latest ./model-training
docker push <your-dockerhub-username>/model-training:latest

docker build -t <your-dockerhub-username>/model-inference:latest ./model-inference
docker push <your-dockerhub-username>/model-inference:latest

# ----------------------------
# 2️⃣ Apply Kubernetes YAML manifests
# ----------------------------
kubectl apply -f namespace.yaml
kubectl apply -f pvc.yaml
kubectl apply -f data-preprocessing-job.yaml
kubectl apply -f model-training-deployment.yaml
kubectl apply -f model-training-service.yaml
kubectl apply -f model-inference-deployment.yaml
kubectl apply -f model-inference-service.yaml

# ----------------------------
# 3️⃣ Verify pods and services
# ----------------------------
kubectl get pods -n hospital-ml
kubectl get svc -n hospital-ml

# ----------------------------
# 4️⃣ Inspect files in shared volume
# ----------------------------
kubectl -n hospital-ml exec -it deploy/model-training -- ls -l /app/shared/clean
kubectl -n hospital-ml exec -it deploy/model-training -- ls -l /app/shared/models

# ----------------------------
# 5️⃣ Port-forward inference service
# ----------------------------
kubectl -n hospital-ml port-forward svc/model-inference-svc 8001:8000

# ----------------------------
# 6️⃣ Test inference endpoint
# ----------------------------
curl -s http://127.0.0.1:8001/healthz | jq
curl -s -X POST http://127.0.0.1:8001/predict \
  -H "Content-Type: application/json" \
  -d '{"input_data": [...]}'