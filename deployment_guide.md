# 🚀 Job Harvestor: New Mac Deployment Guide

This guide covers setting up and running the Distributed Job Harvestor on a fresh macOS environment.

## 1. Prerequisites (Installation)

Open your terminal and run:

```bash
# Install Homebrew (if not installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install core tools
brew install minikube kubectl docker docker-compose

# Start Minikube with Docker driver (recommended for Mac)
minikube start --driver=docker --cpus=4 --memory=8192
```

## 2. Environment Setup

Configure your terminal to use the Minikube Docker environment (this allows the cluster to "see" your locally built images):

```bash
eval $(minikube docker-env)
```

## 3. Build Docker Images

Build the core system components from the project root:

```bash
# Build API Dashboard
docker build -t jobharvestor-api:latest -f Dockerfile.producer .

# Build Distributed Consumers (Discovery & Detail)
docker build -t jobharvestor-consumer:latest -f Dockerfile.consumer .
```

## 4. Deploy the Unified Stack

Apply the synchronized manifest that contains ALL infrastructure (DB, Kafka, Redis, Obervability, App):

```bash
kubectl apply -f jobharvestor-stack.yaml
```

Wait a minute for all pods to reach `Running` state:
```bash
kubectl get pods --watch
```

## 5. Access the System (Port Forwarding)

Run these in separate terminal windows (or as background processes):

```bash
# API Dashboard
kubectl port-forward service/api-dashboard 8080:8080 --address 0.0.0.0

# Grafana Monitoring
kubectl port-forward service/grafana 3000:3000 --address 0.0.0.0
```

- **Dashboard**: [http://localhost:8080](http://localhost:8080)
- **Grafana**: [http://localhost:3000](http://localhost:3000) (User: `admin`, Pass: `admin`)

---

### 🛠️ Troubleshooting

- **Connection Refused**: Ensure `eval $(minikube docker-env)` was run before building images.
- **Pod Error (psycopg2)**: If pods crash, check that the Postgres service is fully up (`kubectl get pods -l app=postgres`).
- **Clean slate**: To wipe everything and start fresh, run `kubectl delete -f jobharvestor-stack.yaml`.
