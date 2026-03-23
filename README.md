# Job Harvestor 🍎

A robust, cloud-native Producer-Consumer architecture for harvesting job listings into a PostgreSQL database, orchestrated via a Redis message queue.

## 🏗️ Architecture Stack
- **FastAPI Dashboard (`src/api.py`)**: A purely Vanilla HTML/JS frontend that connects to PostgreSQL to dynamically save, edit, and trigger CSS selectors for any given company.
- **Producer (`src/producer.py`)**: A background worker that pulls a company's dynamic configuration, iterates through its job listing paginations using Pyppeteer (headless Chromium), and queues JSON payloads into Redis.
- **Consumer (`src/consumer.py`)**: An endless loop that pops JSON payloads from the Redis queue, dynamically applies the CSS selectors attached to that specific payload, and extracts full details into PostgreSQL.
- **Observability (`k8s/observability.yaml`)**: Built-in Prometheus (Metrics), Jaeger (Distributed Traces via OpenTelemetry), and Loki (Structured Logs).

---

## 🚀 Kubernetes Deployment (Minikube)

We have fully dockerized this architecture. The easiest way to run the entire system is through Kubernetes (Minikube).

### 1. Start Minikube & Docker
```bash
minikube start
eval $(minikube docker-env)
```
*(This ensures your local Docker layers are built directly into Minikube's internal registry.)*

### 2. Build the Docker Images
```bash
docker build -t jobharvestor-api:latest -f Dockerfile.producer .
docker build -t jobharvestor-consumer:latest -f Dockerfile.consumer .
```

### 3. Deploy the Stack
Deploy the databases, consumer, API, and all observability tools instantly:
```bash
kubectl apply -f k8s/
```
Wait a few seconds for the pods to boot up (`kubectl get pods`). Because the Python scripts contain an initialization routine, the PostgreSQL tables and default "Apple" configuration will automatically seed on boot.

### 4. Access the Web Dashboard
Since Kubernetes isolates the network, you must open the API Dashboard using the Minikube tunnel:
```bash
minikube service api-dashboard
```
Check out the UI, add new companies, and click **🚀 Run Harvest** to trigger the Producer scraping!

---

## 🔎 Observability & Monitoring

The architecture automatically emits advanced telemetry on ports `8000` and `8001`. You can visualize your scraping rate, errors, and spans!

**To open the Metrics Graph (Prometheus):**
```bash
minikube service prometheus
```
*(Search for `consumer_jobs_inserted_total`)*

**To view the Distributed Traces (Jaeger):**
```bash
minikube service jaeger
```
*(Select `jobharvestor-consumer` and click "Find Traces")*

**To watch the logs live:**
```bash
kubectl logs -l app=api-dashboard -f
kubectl logs -l app=consumer -f
```

---

## 🛑 Stopping the Cluster
To delete the deployment and spin everything down:
```bash
kubectl delete -f k8s/
```
To shut down your Minikube virtual machine:
```bash
minikube stop
```
*(If you run into persistent `connection refused` errors when booting Minikube on macOS later, use `minikube delete` to wipe the corrupted profile and start fresh).*
