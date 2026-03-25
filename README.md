<img width="2978" height="1698" alt="image" src="https://github.com/user-attachments/assets/b77d955e-dc79-4f7c-9d05-ade1efe90367" /># Job Harvestor

A web scraping pipeline for extracting job listings from company career pages and storing them in a PostgreSQL database. It uses a Producer-Consumer architecture backed by a message broker (Kafka or Redis).

## Architecture Stack
- **FastAPI Dashboard (`src/api.py`)**: A web interface to manage target companies, CSS selectors, and trigger scraping jobs. Includes a `gpt-4o` extractor (`src/llm_extractor.py`) to auto-fill CSS selectors from external URLs.
- **MessageBroker (`src/broker/`)**: An interface that allows the system to use either Redis or Kafka for message queuing depending on the `BROKER_TYPE` environment variable.
- **Producer (`src/producer.py`)**: A worker that paginates through job listings using headless Chrome (Pyppeteer) and queues the job URLs into the message broker.
- **Consumer (`src/consumer.py`)**: A continuous background worker that reads URLs from the message broker, navigates to the job page, extracts details using the provided CSS selectors, and saves the data to PostgreSQL.
- **Observability**: Metrics via Prometheus, distributed tracing via Jaeger, and structured logs via Loki.

---

## Local Setup (Minikube)

The project is fully dockerized and orchestrated using Kubernetes.

### 1. Requirements
- Docker
- Minikube
- `kubectl`

### 2. Start Minikube
Allocate enough memory (at least 4GB) to support the Kafka and Zookeeper nodes:
```bash
minikube start --memory=4096 --cpus=4
```

Point your local Docker daemon to use Minikube's internal registry (required since `imagePullPolicy` is set to `Never` for local development):
```bash
eval $(minikube docker-env)
```

### 3. Build the Docker Images
Build the producer and consumer images locally:
```bash
docker build -t jobharvestor-api:latest -f Dockerfile.producer .
docker build -t jobharvestor-consumer:latest -f Dockerfile.consumer .
```

### 4. Deploy to Kubernetes
Apply the stack manifest to spin up all services (PostgreSQL, Redis, Kafka, Zookeeper, Observability tools, and the application):
```bash
kubectl apply -f jobharvestor-stack.yaml
```
You can check the pod status with `kubectl get pods`.

### 5. Access the Web Dashboard
Use `minikube service` to open the GUI in your browser:
```bash
minikube service api-dashboard
```

### 6. Access the Database
If you need to connect to the PostgreSQL database directly using a SQL client (e.g., DBeaver, pgAdmin), you can port-forward the database service:
```bash
kubectl port-forward service/postgres 5432:5432
```
**Connection Details:**
- Host: `localhost`
- Port: `5432`
- User: `postgres`
- Password: `your_postgres_password`
- Database: `postgres`

---

## Observability

Telemetry is exposed on ports `8000` (Producer) and `8001` (Consumer).

**Metrics (Prometheus):**
```bash
minikube service prometheus
```
*(Search for the metric `consumer_jobs_inserted_total`)*

**Tracing (Jaeger):**
```bash
minikube service jaeger
```
*(Select `jobharvestor-consumer` and find traces)*

**Logs (Loki):**
```bash
minikube service loki
```
*(Note: The API Dashboard fetches logs directly from Loki's HTTP API for the Live Terminal UI feature)*

**Raw Container Logs:**
```bash
kubectl logs -l app=api-dashboard -f
kubectl logs -l app=consumer -f
```

<img width="2978" height="1698" alt="image" src="https://github.com/user-attachments/assets/1bc16128-284a-4b64-b9fd-e61b35c8e652" />

---

## Stopping the Cluster
To spin down the microservices, delete the manifest:
```bash
kubectl delete -f jobharvestor-stack.yaml
```

To stop Minikube:
```bash
minikube stop
```
