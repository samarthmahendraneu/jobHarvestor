# JobHarvestor — System Design Description
## Context for System Design Diagram

---

## 1. System Overview

**JobHarvestor** is a production-grade, distributed web-scraping platform engineered to autonomously discover and extract structured job postings from arbitrary company career pages — without any manually written CSS selectors. The system uses a **GPT-4o-powered LLM Extractor** to auto-generate and validate DOM selectors on the fly, feeding a decoupled, event-driven pipeline of headless browser workers that run at scale on Kubernetes.

The architecture is divided into five logical layers:

| Layer | Responsibility |
|---|---|
| **API & Dashboard** | User-facing control plane — trigger harvests, register companies, view logs & traces |
| **LLM Selector Engine** | GPT-4o auto-generates and self-corrects CSS selectors for any career page |
| **Message Broker** | Pluggable Kafka / Redis queue decouples producers from consumers |
| **Worker Pipeline** | Discovery consumer finds job links; Detail consumer scrapes structured fields |
| **Observability Stack** | Prometheus + Grafana + Loki + Jaeger/OTel for metrics, logs, and traces |

---

## 2. Entry Point — API & Dashboard ([api.py](file:///Users/samarthmahendra/PycharmProjects/jobHarvestor/src/api.py))

- **Framework**: FastAPI served via Uvicorn on port **8090**
- **Frontend**: Jinja2 HTML template served at `GET /` — acts as a single-page operations dashboard
- **Endpoints**:

| Endpoint | Purpose |
|---|---|
| `GET /api/companies` | List all configured company targets from PostgreSQL |
| `POST /api/companies` | Register a new company with its base URL and CSS selector config |
| `POST /api/harvest/{company_id}` | Publish a harvest-request JSON message to the `harvest-requests` topic |
| `POST /api/test-extract` | Monolithic end-to-end LLM test: listings + details for a given URL |
| `POST /api/test-listings` | LLM listing selector extraction only |
| `POST /api/test-details` | LLM detail selector extraction only |
| `GET /api/logs` | Proxy: fetches latest 50 log lines from Loki |
| `GET /api/stats` | Returns Redis queue depth + recent Jaeger trace latencies |

- **Prometheus**: Starts a `/metrics` scrape server on port **8000** at startup via FastAPI lifespan hook.

---

## 3. LLM Selector & Extractor ([llm_extractor.py](file:///Users/samarthmahendra/PycharmProjects/jobHarvestor/src/llm_extractor.py))

This is the most distinctive subsystem. Rather than hardcoding scrapers, the system uses **GPT-4o with structured output (Pydantic `response_format`)** to identify the right CSS selectors for any career page.

### 3.1 Flow

```
User submits URL
    → Headless Chrome (Pyppeteer) renders the page
    → HTML is condensed (scripts, styles, SVGs stripped; whitespace collapsed; truncated to 150,000 chars)
    → Two-phase LLM call:
        Phase 1: extract_listing_selectors()  →  ListingSchema
        Phase 2: extract_details_selectors()  →  DetailsSchema
    → Each call is VALIDATED live on the DOM (document.querySelector)
    → If invalid → self-correction loop (up to 3 attempts with GPT conversation memory)
```

### 3.2 Schemas

**[ListingSchema](file:///Users/samarthmahendra/PycharmProjects/jobHarvestor/src/llm_extractor.py#13-17)** (Pydantic):
- `job_list_selector` — repeating wrapper per job
- `title_selector` — job title inside wrapper
- `link_selector` — `<a>` tag inside wrapper

**[DetailsSchema](file:///Users/samarthmahendra/PycharmProjects/jobHarvestor/src/llm_extractor.py#18-26)** (Pydantic):
- `job_id_selector`, `job_title_selector`, `location_selector`, `department_selector`, `summary_selector`, `long_description_selector`, `date_selector`

### 3.3 Retry & Self-Correction (LLM Layer)

| Param | Value |
|---|---|
| Max attempts per selector set | **3** |
| On validation failure | GPT conversation is extended with the failure reason — CSS selector that returned null — and GPT self-corrects |
| Model | `gpt-4o`, temperature `0.1` (near-deterministic) |
| Fallback | After 3 failed attempts, best-guess selectors are used with a logged error |

### 3.4 Bridge Navigation
After extracting listing selectors, the extractor navigates to the **first discovered job link** (resolving relative hrefs with `urljoin`) and runs Phase 2 detail extraction on the actual job detail page.

---

## 4. Pluggable Message Broker Layer (`src/broker/`)

The broker is **fully pluggable** — selected at runtime via the `BROKER_TYPE` environment variable (`kafka` or `redis`). All components call `get_broker()` which returns the correct implementation of the abstract `MessageBroker` interface.

### 4.1 Kafka Broker (`KafkaBroker.py`)
- **Library**: `confluent_kafka` (native librdkafka bindings — high-throughput)
- **Producer**: Persistent TCP connection; `flush()` called after every `produce()` for durability
- **Consumer**: Subscribes lazily on first consume; polls `batch_size` messages per call with a 3-second timeout
- **Consumer Group**: `KAFKA_GROUP_ID` env var (`discovery-group` or `detail-group`) — allows **independent offset tracking** per consumer fleet
- **Topics**:

| Topic | Producer | Consumer |
|---|---|---|
| `harvest-requests` | API Dashboard | Discovery Consumer |
| `jobs` | Discovery Consumer | Detail Consumer |
| `jobs-dlq` | Detail Consumer (on failure) | (Dead-letter queue for inspection/retry) |

### 4.2 Redis Broker (`RedisBroker.py`)
- **Data structure**: Redis **List** — `RPUSH` to produce, `LPOP` to consume (FIFO queue)
- **Minikube TCP URL fix**: Handles Minikube's `REDIS_PORT=tcp://10.x.x.x:6379` injection by parsing the port number from the URL string
- **Use case**: Simpler single-node mode; also used for queue depth stats in `GET /api/stats`

---

## 5. Worker/Consumer Pipeline

The pipeline is **split into two independent consumer fleets**, each running as separate Kubernetes Deployments.

### 5.1 Discovery Consumer (`discovery_consumer.py`)
**K8s Deployment**: `consumer-discovery` | **Replicas**: 2 | **Prometheus Port**: 8002

**Responsibility**: Poll the `harvest-requests` topic → use Pyppeteer to render the career listings page → extract all job links using the LLM-generated selectors → publish each job's URL + selector config as a JSON payload to the `jobs` topic.

**Infinite Daemon Loop**:
```
while True:
    messages = broker.consume("harvest-requests", batch_size=1)
    if no messages → sleep 5s → continue
    for each message:
        run_discovery(config, broker)
            → launch headless Chrome
            → navigate to base_url (networkidle2, 60s timeout)
            → evaluate JS to collect all job links
            → normalize URLs (urljoin + path deduplication)
            → broker.produce("jobs", payload)
```

**Observability**:
- `discovery_listings_found_total` — Counter
- `discovery_pages_processed_total` — Counter
- `discovery_pages_failed_total` — Counter
- `discovery_scrape_duration_seconds` — Histogram (buckets: 1…60s)
- `discovery_active_browsers` — Gauge

### 5.2 Detail Consumer (`consumer.py`)
**K8s Deployment**: `consumer-detail` | **Replicas**: 5 | **Prometheus Port**: 8001

**Responsibility**: Poll the `jobs` topic in micro-batches of 3 → launch a single shared Chrome browser → scrape each job detail page concurrently using `asyncio.gather()` → insert structured data into PostgreSQL → on failure, publish to `jobs-dlq` Dead Letter Queue.

**Batch Processing Loop**:
```
while True:
    messages = broker.consume("jobs", batch_size=3)
    if no messages → sleep 5s → continue
    parse each message → ScraperPayload
    scrape_batch(payloads, broker):
        launch 1 Chrome browser (shared across batch)
        asyncio.gather([process_job_detail(p, browser) for p in payloads])
        close browser
    sleep 2s
```

**Selector fields extracted per job**: `job_id`, `title`, `location`, `department`, `summary`, `long_description`, `date`

**Observability**:
- `consumer_jobs_inserted_total` — Counter
- `consumer_jobs_failed_total` — Counter
- `consumer_batches_processed_total` — Counter
- `consumer_job_scrape_duration_seconds` — Histogram (1…120s)
- `consumer_batch_duration_seconds` — Histogram (5…300s)
- `consumer_active_browsers` — Gauge

---

## 6. Retry, Backoff & Dead-Letter Queue

| Mechanism | Where | Behavior |
|---|---|---|
| **LLM self-correction retry** | `llm_extractor.py` | 3 attempts; conversation-based correction; proceeds on best-guess after max |
| **Consumer loop retry** | `consumer.py`, `discovery_consumer.py` | Outer `while True` catch-all; sleeps 5s on exception before next iteration |
| **Browser page retry** | `consumer.py` | Each job processed in isolation; one failure doesn't block others in the batch |
| **Dead-Letter Queue (DLQ)** | `consumer.py` | Failed jobs published to `jobs-dlq` Kafka/Redis topic with `url`, `error`, selector fields for replay or manual inspection |
| **Rate Limiter** | `rate_limiter.py` | Per-domain token bucket; called before every `page.goto()` to prevent IP bans |
| **Jitter / Random Sleep** | `producer.py`, `consumer.py` | `asyncio.sleep(random.uniform(1, 3))` before each page navigation — exponential-jitter-style anti-bot delay |
| **Page navigation timeout** | All consumers | 90,000ms (90s) hard timeout on `page.goto()` |
| **SPA stabilization wait** | Detail consumer | Extra `asyncio.sleep(3.5)` after `networkidle0` for SPA element mounting |

---

## 7. Stealth Browser Infrastructure (`stealth.py`)

All browser pages go through `prepare_stealth_page()` before navigation:
- Sets realistic User-Agent strings
- Removes WebDriver-specific navigator properties (`navigator.webdriver = false`)
- Uses `--disable-blink-features=AutomationControlled` Chrome flag
- Launched with `--no-sandbox`, `--disable-dev-shm-usage` for container compatibility
- Chrome executable path via `CHROME_PATH` env var (`/usr/bin/chromium` in containers)

---

## 8. Data Persistence — PostgreSQL

- **Deployment**: `postgres.yaml` — single-replica PostgreSQL pod in Kubernetes
- **Tables**:
  - `companies_config` — company name, base URL, all CSS selector fields
  - `job_details` — `job_id`, `title`, `location`, `department`, `summary`, `long_description`, `date`, `url` (UNIQUE constraint on `url`, with `ON CONFLICT DO NOTHING` for idempotent inserts)
- **Accessed by**: API (company CRUD), Detail Consumer (insert job records)

---

## 9. Observability Stack

### 9.1 Prometheus (Metrics)
- **Deployment**: `observability.yaml` — `prom/prometheus:latest`, port **9090** (NodePort 30090)
- **Scrape Config**: Kubernetes Service Discovery (`kubernetes_sd_configs`, `role: pod`) — automatically discovers all pods annotated with `prometheus.io/scrape: "true"` and the correct `prometheus.io/port`
- **Scrape interval**: 15 seconds
- **Scraped Services**: API (8000), Discovery Consumer (8002), Detail Consumer (8001)

### 9.2 Grafana (Dashboards)
- **Deployed via**: `jobharvestor-stack.yaml` (Helm-based stack), grafana provisioned config in `grafana/`
- **Datasources** (`grafana/datasources.yaml`): Prometheus (`http://prometheus:9090`), Loki (`http://loki:3100`)
- **Dashboard** (`grafana/dashboard.json`): Pre-built dashboard with panels for queue depth, jobs inserted/failed, scrape durations, active browsers, and log streams
- **Auto-provisioned**: Dashboards loaded via `grafana/dashboards.yaml` ConfigMap at startup

### 9.3 Loki (Log Aggregation)
- **Deployment**: `observability.yaml` — `grafana/loki:latest`, port **3100**
- **Log Shipping**: All services push structured logs using `logging_loki.LokiHandler` with `application` tag labels:
  - `jobharvestor-producer`
  - `jobharvestor-consumer`
- **API Integration**: `GET /api/logs` proxies a Loki LogQL query (`{application=~"jobharvestor-consumer|jobharvestor-producer"}`) and returns the latest 50 log lines
- **Grafana Datasource**: Loki connected to Grafana for log panel rendering

### 9.4 Jaeger / OpenTelemetry (Distributed Tracing)
- **Deployment**: `observability.yaml` — `jaegertracing/all-in-one:latest`, OTLP port **4318**, UI port **16686** (NodePort 30086)
- **OTLP Exporter**: All services configure `OTLPSpanExporter` pointing to `OTLP_ENDPOINT` env var
  - Default: `http://jaeger:4318/v1/traces`
- **Traced Spans**:
  - `scrape_jobs_on_page` — Producer: per-page scrape span with `payload.url` and `jobs_found` attributes
  - `scrape_batch` — Producer: batch-level span with `batch_size`
  - `scrape_job_details_on_page` — Consumer: detail scrape with `payload.url`; records exceptions
  - `consumer_scrape_batch` — Consumer: batch-level span
- **SDK Config**: `Resource(service.name=...)` per service, `BatchSpanProcessor` for async export
- **API Integration**: `GET /api/stats` queries Jaeger REST API for recent traces and returns latency data to the dashboard

---

## 10. Docker & Container Infrastructure

### Dockerfiles
- **`Dockerfile.consumer`** — Builds a single image used by **both** consumer deployments; the K8s `command` field differentiates which module runs (`src.discovery_consumer` vs `src.consumer`)
- **`Dockerfile.producer`** — Builds the legacy standalone producer image (now superseded by the discovery consumer)

### Container Strategy
- Chromium is baked into the consumer image for headless browser support
- `imagePullPolicy: Never` used for local Minikube builds

---

## 11. Kubernetes Deployment Topology (`k8s/`)

| Manifest | Resources Defined |
|---|---|
| `consumer.yaml` | `consumer-discovery` Deployment (2 replicas) + `consumer-detail` Deployment (5 replicas) |
| `api.yaml` | API Dashboard Deployment + Service (NodePort) |
| `api-dashboard.yaml` | Additional API ingress / dashboard config |
| `observability.yaml` | Prometheus Deployment+Service, Jaeger Deployment+Service, Loki Deployment+Service |
| `redis.yaml` | Redis Deployment + Service |
| `postgres.yaml` | PostgreSQL Deployment + Service |

### Consumer Env Vars (per pod)
```
DB_HOST=postgres
REDIS_HOST=redis
KAFKA_HOST=kafka:9092
KAFKA_GROUP_ID=discovery-group | detail-group
LOKI_URL=http://loki:3100/loki/api/v1/push
OTLP_ENDPOINT=http://jaeger:4318/v1/traces
CHROME_PATH=/usr/bin/chromium
BROKER_TYPE=kafka | redis
```

---

## 12. End-to-End Data Flow (Summary)

```
User (Browser)
  ↓ POST /api/harvest/{company_id}
API Dashboard (FastAPI :8090)
  ↓ broker.produce("harvest-requests", config_json)
Message Broker (Kafka topic: harvest-requests | Redis List)
  ↓ broker.consume("harvest-requests", batch_size=1)
Discovery Consumer Pod (×2 replicas)
  ↓ Pyppeteer renders career listings page
  ↓ Extracts all job links via LLM-generated CSS selectors
  ↓ broker.produce("jobs", {url, selectors...})
Message Broker (Kafka topic: jobs | Redis List)
  ↓ broker.consume("jobs", batch_size=3)
Detail Consumer Pod (×5 replicas)
  ↓ Pyppeteer renders each job detail page (asyncio.gather, batch of 3)
  ↓ Extracts: title, location, department, summary, description, date
  ↓ INSERT INTO job_details ... ON CONFLICT DO NOTHING
PostgreSQL
  ↓ (on failure)
Dead-Letter Queue (Kafka topic: jobs-dlq | Redis List)

──────────────────────────────────────────────
Observability side-channel (all components):
  → Prometheus scrapes /metrics every 15s
  → Logs pushed to Loki via LokiHandler
  → OTel spans exported to Jaeger via OTLP/HTTP
  → Grafana visualizes Prometheus + Loki data
  → API /api/stats queries Jaeger REST + Redis queue depth
  → API /api/logs queries Loki LogQL
```

---

## 13. Key Design Decisions

| Decision | Rationale |
|---|---|
| **LLM auto-extraction** | Zero hardcoded scrapers; works on any career page without engineering per-site |
| **Split Discovery + Detail pipeline** | Independent scaling; discovery is I/O-light (link extraction), detail is heavy (full page render + DB insert) |
| **Pluggable broker (Kafka/Redis)** | Redis for local/dev, Kafka for production throughput with consumer group offset management |
| **Dead-Letter Queue** | Failed jobs don't block the pipeline; stored for replay and post-mortem analysis |
| **Stealth browser** | Evade bot detection (Cloudflare, Akamai) without proxy dependencies |
| **Rate limiter + jitter** | Per-domain request pacing prevents IP bans; random delay emulates human browsing |
| **`ON CONFLICT DO NOTHING`** | Idempotent inserts — consumers can process the same URL twice without duplicates |
| **Structured OTel tracing** | Span attributes (`url`, `jobs_found`, `batch_size`) enable per-request latency analysis in Jaeger UI |
