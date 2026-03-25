<img width="2978" height="1698" alt="image" src="https://github.com/user-attachments/assets/b77d955e-dc79-4f7c-9d05-ade1efe90367" />

# 🍎 Job Harvestor: Distributed AI-Powered Career Engine

Job Harvestor is an enterprise-grade, distributed web scraping pipeline designed to ingest and process job postings at scale. It leverages **Apache Kafka** for reliable asynchronous messaging, **Kubernetes** for cloud-native orchestration, and **OpenAI's GPT-4o** for autonomous CSS selector extraction.

## 🚀 Key Features

*   **Autonomous AI Selector Extraction For New companies**: Integrated `gpt-4o` engine that analyzes DOM structures in real-time to generate and validate CSS selectors for dynamic SPAs.
*   **Distributed Architecture**: Decoupled Producer-Consumer model running on **Kubernetes**, utilizing **Kafka** for high-throughput message queuing.
*   **Massive Scalability**: Built with **PostgreSQL Threaded Connection Pooling** and per-domain **Token-Bucket Rate Limiting** to handle thousands of target companies concurrently.
*   **Test-Extract Workbench**: A dedicated developer dashboard (`/test`) for synchronous AI extraction testing and selector refinement.
*   **Full-Stack Observability**: Pre-configured monitoring suite with **Grafana**, **Prometheus** (metrics), **Loki** (logging), and **Jaeger** (distributed tracing via OpenTelemetry).

---

## 🛠 Architecture Stack

*   **API Dashboard (`src/api.py`)**: FastAPI-based management interface for company configurations and live harvest triggering.
*   **LLM Extractor (`src/llm_extractor.py`)**: Core AI logic using **Pyppeteer** and **GPT-4o** to autonomously find selectors on listing and detail pages.
*   **Message Brokers**: Pluggable interface supporting both **Apache Kafka** (Production) and **Redis** (Local Dev).
*   **Scraper Engine**: Multi-mode headless Chromium workers with advanced stealth profiles to bypass bot detection.
*   **Data Layer**: PostgreSQL with auto-incrementing identity and URL-based deduplication for 100% data integrity.

---

## 📦 Local Setup (Minikube)

### 1. Requirements
*   Docker & Minikube
*   `kubectl`
*   OpenAI API Key (for automated extraction)

### 2. Infrastructure Initialization
Allocate at least 4GB of memory for the Kafka/Zookeeper stack:
```bash
minikube start --memory=4096 --cpus=4
eval $(minikube docker-env)
```

### 3. Build & Deploy
Build the core application images and apply the Kubernetes manifests:
```bash
docker build -t jobharvestor-api:latest -f Dockerfile.producer .
docker build -t jobharvestor-consumer:latest -f Dockerfile.consumer .

kubectl apply -f jobharvestor-stack.yaml
```

### 4. Access the Platform
```bash
# Open the API Dashboard
minikube service api-dashboard

# Establish port-forwarding for the dev dashboard
kubectl port-forward service/api-dashboard 8080:8080
```
Visit `http://localhost:8080` for the main dashboard and `http://localhost:8080/test` for the AI extraction workbench.

---

## 📊 Observability Suite

Job Harvestor exposes deep telemetry across the entire pipeline:

*   **Metrics**: `minikube service prometheus` (Track `consumer_jobs_inserted_total`)
*   **Tracing**: `minikube service jaeger` (Analyze scrapers' DOM interaction latency)
*   **Logs**: `minikube service loki` (Centralized log aggregation)

---

## 🛑 Stopping the Cluster
```bash
kubectl delete -f jobharvestor-stack.yaml
minikube stop
```

<img width="2978" height="1698" alt="image" src="https://github.com/user-attachments/assets/1bc16128-284a-4b64-b9fd-e61b35c8e652" />
